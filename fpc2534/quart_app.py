import quart
import os
import aiomqtt
import asyncio
import fpc2534 as fpc2534
import functools
import os

MAX_CHUNK_SIZE = 140
DOWNLOAD_TIMEOUT = 120

key = os.environ.get('FPC2534_KEY')
if key:
    key = bytes.fromhex(key)
sensor = fpc2534.FPC2534(key)

finite_action_queue = None
infinite_action_queue = asyncio.Queue()
finite_action_finished = asyncio.Event()

identify_queues: set[asyncio.Queue] = set()
identification_subscriber_appeared = asyncio.Event()


async def identify_loop():
    while True:
        if len(identify_queues) == 0:
            identification_subscriber_appeared.clear()
            await identification_subscriber_appeared.wait()
            continue
        
        if finite_action_queue is not None:
            finite_action_finished.clear()
            await finite_action_finished.wait()
        
        response = await send_data(sensor.identify_finger(), infinite_action_queue)
                
        states = response.get('states', [])
        
        if 'STATE_IDENTIFY' not in states:
            await asyncio.sleep(10)
            
            continue

        for queue in identify_queues:
            await queue.put({'event': 'EVENT_IDENTIFY_STARTED'})
        
        while True:
            finite_action_finished.clear()
            done, pending = await asyncio.wait([
                asyncio.create_task(finite_action_finished.wait(), name='finite'),
                asyncio.create_task(infinite_action_queue.get())
            ], return_when=asyncio.FIRST_COMPLETED)
        
            done = done.pop()
            pending.pop().cancel()
            
            if done.get_name() == 'finite':
                # restart identify
                break

            response = done.result()
            
            for queue in identify_queues:
                await queue.put(response)
                
            if response.get('event') == 'EVENT_FINGER_LOST':
                # allow to restart identification
                break

async def send_data(data, response_loop=None):
    await app.mqtt_client.publish(
        'ble_devices/cb:6f:0f:38:a5:24/383f0000-7947-d815-7830-14f1584109c5/383f0001-7947-d815-7830-14f1584109c5/Set',
        ','.join(map(str, data))
    )
    
    if response_loop is None:
        response_loop = finite_action_queue
    
    return await response_loop.get()

app = quart.Quart(__name__)
app.config['MAX_CONTENT_LENGTH'] = 640000

async def loop_messages():
    async with aiomqtt.Client(
            os.environ.get('MQTT_HOST', 'localhost'), 
            int(os.environ.get('MQTT_PORT', 1883))
        ) as client:
        print('connected')
        app.mqtt_client = client
        await client.subscribe('ble_devices/cb:6f:0f:38:a5:24/383f0000-7947-d815-7830-14f1584109c5/383f0002-7947-d815-7830-14f1584109c5')
        async for message in client.messages:
            response = sensor.parse_response(
                bytes(map(int, message.payload.decode().split(',')))
            )
                    
            if finite_action_queue is not None:
                await finite_action_queue.put(response)
            else:
                await infinite_action_queue.put(response)
            
async def get_status(filtered_states=['STATE_APP_FW_READY', 'STATE_SECURE_INTERFACE']):
    response = await send_data(sensor.encode_request(fpc2534.CMD_STATUS))
    
    response['states'] = list(filter(
        lambda state: state not in filtered_states,
        response['states']
    ))
    return response

async def download_data(total_size):
    remaining = total_size
    
    while remaining > 0:
        chunk_size = min(MAX_CHUNK_SIZE, remaining)
        get_response = await send_data(sensor.data_get(chunk_size))
        yield get_response['data']
        remaining = get_response['remaining']

    global finite_action_queue
    finite_action_queue = None
    finite_action_finished.set()
        
async def respond_download(total_size):
    res = await quart.make_response(download_data(total_size), 200, {
        'Content-Length': total_size
    })
    res.timeout = DOWNLOAD_TIMEOUT
    
    return res

async def ensure_idle():
    status = await get_status()
    if len(status['states']) != 0:
        await send_data(sensor.abort())

@app.before_serving
async def _start_loop():
    asyncio.create_task(loop_messages())
    asyncio.create_task(identify_loop())
    
@app.before_request
async def _before_request():
    if quart.request.url == '/sensor/identify':
        return
    
    global finite_action_queue
    if finite_action_queue is not None:
        return 'Another finite request is already running', 503
    
    finite_action_queue = asyncio.Queue()

@app.after_request
async def _after_request(response: quart.wrappers.Response):
    if quart.request.path == '/sensor/enroll':
        return response
    
    if response.status_code == 503:
        # request rejected anyway
        return response
    
    if response.timeout == DOWNLOAD_TIMEOUT:
        # generator, will clean up itself
        return response
    
    global finite_action_queue
    finite_action_queue = None
    
    finite_action_finished.set()
    
    return response

@app.get('/sensor/status')
async def _get_status():
    return await get_status(filtered_states=[])

@app.get('/sensor/templates')
async def _list_templates():
    return await send_data(sensor.encode_request(fpc2534.CMD_LIST_TEMPLATES))

@app.get('/sensor/templates/<int:id>')
async def _download_template(id: int):
    await ensure_idle()
        
    response =  await send_data(sensor.download_template(id))
    if response.get('app_fail_code') == 21:
        return f'Template {id} not found', 404
                        
    return await respond_download(response['total_size'])

@app.delete('/sensor/templates/<int:id>')
async def _delete_template(id):
    return await send_data(sensor.delete_template(id))
    
@app.put('/sensor/templates/<int:id>')
async def _upload_demplate(id):
    data_length = int(quart.request.headers['Content-Length'])
    
    if data_length != 18000:
        return 'Payload must be sized 18000', 400
    
    await ensure_idle()
    
    response = await send_data(sensor.upload_template(id, 18000))
        
    if response.get('app_fail_code') == 20:
        return 'Template already exists', 409
    
    remaining = data_length
    data = await quart.request.get_data()
    
    while remaining > 0:
        chunk_size = min(MAX_CHUNK_SIZE, remaining)
        start = data_length - remaining
        chunk = data[start : start + chunk_size]
        
        response = await send_data(sensor.data_put(remaining, chunk))
                
        remaining = data_length - response['total_received']
            
    return 'ok'

@app.websocket('/sensor/identify')
async def _identify():
    event_queue = asyncio.Queue()
    identify_queues.add(event_queue)
    
    try:
        await quart.websocket.accept()

        identification_subscriber_appeared.set()
                    
        while True:
            response = await event_queue.get()
            
            if response.get('finger_found') is not None:
                response['event'] = 'EVENT_FINGER_MATCHED'
            
            try:
                await quart.websocket.send_json(response)
            except:
                pass
    finally:
        identify_queues.remove(event_queue)
            
@app.get('/sensor/image')
async def _get_image():
    await ensure_idle()
    
    response = await send_data(sensor.encode_request(fpc2534.CMD_CAPTURE))
    
    while True:
        event = await finite_action_queue.get()
        if event['event'] == 'EVENT_FINGER_LOST':
            image_available = 'STATE_IMAGE_AVAILABLE' in event['states']
            break
        
    if not image_available:
        return 'Failed capturing image', 500
    
    response = await send_data(sensor.request_image_data())
    
    if response.get('app_fail_code') == 43:
        return 'No image available', 404
    
    return await respond_download(response['size'])

@app.get('/sensor/config/default')
@app.get('/sensor/config/current')
async def _get_system_config():
    return await send_data(sensor.get_system_config(quart.request.url.endswith('default')))


@app.route('/sensor/config', methods=['PUT', 'POST'])
@app.route('/sensor/config/current', methods=['PUT', 'POST'])
async def _set_system_config():
    payload = await quart.request.json
    del payload['type']
    return await send_data(sensor.set_system_config(**payload))

@app.route('/sensor/key', methods=['PUT', 'POST'])
async def _set_key():
    key = await quart.request.get_data()
    if len(key) not in [16, 32]:
        return 'Key must be of length 16 or 32', 400
    
    return await send_data(sensor.set_key(key))

@app.post('/sensor/enroll')
async def _enroll():
    await ensure_idle()
    template_id = quart.request.args.get('template_id')
    if template_id is not None:
        template_id = int(template_id)
    response = await send_data(sensor.enroll_finger(template_id))
    
    if not 'STATE_ENROLL' in response['states']:
        return response, 500
        
    global finite_action_queue
    
    stream = quart.request.headers.get('Accept') == 'multipart/related'
    
    async def generator():
        global finite_action_queue
        while True:
            response = await finite_action_queue.get()
            
            if stream:
                yield quart.json.dumps(response) + '\n'
            else:
                yield response
            
            if response.get('feedback') in ['ENROLL_FEEDBACK_PROGRESS', 'ENROLL_FEEDBACK_REJECT_LOW_QUALITY', 'ENROLL_FEEDBACK_PROGRESS_IMMOBILE']:
                # right within process
                continue
            
            if response.get('event') in ['EVENT_FINGER_DETECT', 'EVENT_IMAGE_READY', 'EVENT_FINGER_LOST']:
                # irrelevant events
                continue
            
            # await FINGER_LOST event
            await finite_action_queue.get()
            
            finite_action_queue = None
            finite_action_finished.set()
            
            break
        
    if stream:
        return generator(), 200, {
            'Content-Type': 'application/text'
        }
        
    async for event in generator():
        response = event
    
    finite_action_queue = None
    finite_action_finished.set()
        
    return response
        
    

@app.post('/sensor/reset')
async def _reset():
    return await send_data(sensor.reset())