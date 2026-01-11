import quart
import os
import aiomqtt
import asyncio
import fpc2534 as fpc2534
import functools

sensor = fpc2534.FPC2534()

response_queue = asyncio.Queue()

async def send_data(data, wait_for_response=True):
    payload = ','.join(map(str, data))
    await app.mqtt_client.publish(
        'ble_devices/cb:6f:0f:38:a5:24/383f0000-7947-d815-7830-14f1584109c5/383f0001-7947-d815-7830-14f1584109c5/Set',
        payload
    )
    
    if wait_for_response:
        return await response_queue.get()

app = quart.Quart(__name__)
app.config['MAX_CONTENT_LENGTH'] = 640000

async def loop_messages():
    async with aiomqtt.Client('localhost') as client:
        print('connected')
        app.mqtt_client = client
        await client.subscribe('ble_devices/cb:6f:0f:38:a5:24/383f0000-7947-d815-7830-14f1584109c5/383f0002-7947-d815-7830-14f1584109c5')
        async for message in client.messages:
            await response_queue.put(
                sensor.parse_response(
                    bytes(map(int, message.payload.decode().split(',')))
                )
            )
            
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
        chunk_size = min(177, remaining)
        get_response = await send_data(sensor.data_get(chunk_size))
        print(remaining, get_response, len(get_response.get('data')))
        yield get_response['data']
        remaining = get_response['remaining']
        
async def respond_download(total_size):
    res = await quart.make_response(download_data(total_size), 200, {
        'Content-Length': total_size
    })
    res.timeout = 120
    
    return res

async def ensure_idle():
    status = await get_status()
    if len(status['states']) != 0:
        print('sending abort')
        print(await send_data(sensor.abort()))

@app.before_serving
async def _start_loop():
    asyncio.create_task(loop_messages())

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
    print(quart.request.headers)
    
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
        chunk_size = min(177, remaining)
        start = data_length - remaining
        chunk = data[start : start + chunk_size]
        
        response = await send_data(sensor.data_put(remaining, chunk))
                
        remaining = data_length - response['total_received']
        
        print(response)
    
    return 'ok'

@app.websocket('/sensor/identify')
async def _identify():
    state = await get_status()
    
    async def start_identify():
        print(await send_data(sensor.identify_finger()))
    
    if len(state['states']) == 0 or state['states'][0] != 'STATE_IDENTIFY':
        print('putting to identify mode...')
        await send_data(sensor.abort())
        
        await start_identify()
        
    await quart.websocket.accept()
            
    print('waiting for identification')
        
    while True:
        done, pending = await asyncio.wait([
            asyncio.create_task(response_queue.get(), name='response'), 
            asyncio.create_task(quart.websocket.receive(), name='heartbeat')
        ], return_when=asyncio.FIRST_COMPLETED)
        
        for p in pending:
            p.cancel()
                
        done_task = done.pop()
                
        if done_task.get_name() == 'heartbeat':
            print(f'received heartbeat: {done_task.result()}')
            continue
        
        response = done_task.result()
        
        if response.get('finger_found') is not None:
            response['event'] = 'FINGER_MATCHED'
        
        await quart.websocket.send_json(response)
        
        if response['event'] == 'EVENT_FINGER_LOST':
            await start_identify()
            
@app.get('/sensor/image')
async def _get_image():
    await ensure_idle()
    
    response = await send_data(sensor.encode_request(fpc2534.CMD_CAPTURE))
    print(response)
    
    while True:
        event = await response_queue.get()
        if event['event'] == 'EVENT_FINGER_LOST':
            image_available = 'STATE_IMAGE_AVAILABLE' in event['states']
            break
        
    if not image_available:
        return 'Failed capturing image', 500
    
    response = await send_data(sensor.request_image_data())
    print(response)
    
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
    # untested
    payload = await quart.request.json
    return await send_data(sensor.set_system_config(**payload))

@app.route('/sensor/key', methods=['PUT', 'POST'])
async def _set_key():
    # untested
    key = await quart.request.get_data()
    if len(key) not in [16, 32]:
        return 'Key must be of length 16 or 32', 400
    
    return await send_data(sensor.set_key(key))

@app.post('/sensor/enroll')
async def _enroll():
    # untested
    pass

@app.post('/sensor/reset')
async def _reset():
    return await send_data(sensor.reset())