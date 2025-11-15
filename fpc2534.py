import struct
import time
import sys
from PIL import Image
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CMD_STATUS =                              0x0040
CMD_VERSION =                             0x0041
CMD_BIST =                                0x0044
CMD_CAPTURE =                             0x0050
CMD_ABORT =                               0x0052
CMD_IMAGE_DATA =                          0x0053
CMD_ENROLL =                              0x0054
CMD_IDENTIFY =                            0x0055
CMD_LIST_TEMPLATES =                      0x0060
CMD_DELETE_TEMPLATE =                     0x0061
CMD_GET_TEMPLATE_DATA =                   0x0062
CMD_PUT_TEMPLATE_DATA =                   0x0063
CMD_GET_SYSTEM_CONFIG =                   0x006A
CMD_SET_SYSTEM_CONFIG =                   0x006B
CMD_RESET =                               0x0072
CMD_SET_CRYPTO_KEY =                      0x0083
CMD_SET_DBG_LOG_LEVEL =                   0x00B0
CMD_FACTORY_RESET =                       0x00FA
CMD_DATA_GET =                            0x0101
CMD_DATA_PUT =                            0x0102
CMD_NAVIGATION =                          0x0200
CMD_NAVIGATION_PS =                       0x0201
CMD_GPIO_CONTROL =                        0x0300

STATES = {
    0x0001: 'STATE_APP_FW_READY',
    0x0002: 'STATE_SECURE_INTERFACE',
    0x0004: 'STATE_CAPTURE',
    0x0010: 'STATE_IMAGE_AVAILABLE',
    0x0040: 'STATE_DATA_TRANSFER',
    0x0080: 'STATE_FINGER_DOWN',
    0x0400: 'STATE_SYS_ERROR',
    0x1000: 'STATE_ENROLL',
    0x2000: 'STATE_IDENTIFY',
    0x4000: 'STATE_NAVIGATION',
}

EVENTS = {
    0: 'EVENT_NONE',
    1: 'EVENT_IDLE',
    3: 'EVENT_FINGER_DETECT',
    4: 'EVENT_FINGER_LOST',
    5: 'EVENT_IMAGE_READY',
    6: 'EVENT_CMD_FAILED',
}

NAV_EVENTS = {
	0: 'CMD_NAV_EVENT_NONE',
	1: 'CMD_NAV_EVENT_UP',
	2: 'CMD_NAV_EVENT_DOWN',
	3: 'CMD_NAV_EVENT_RIGHT',
	4: 'CMD_NAV_EVENT_LEFT',
	5: 'CMD_NAV_EVENT_PRESS',
	6: 'CMD_NAV_EVENT_LONG_PRESS',
}

ENROLL_STATES = {
	1: 'ENROLL_FEEDBACK_DONE',
	2: 'ENROLL_FEEDBACK_PROGRESS',
	3: 'ENROLL_FEEDBACK_REJECT_LOW_QUALITY',
	4: 'ENROLL_FEEDBACK_REJECT_LOW_COVERAGE',
	5: 'ENROLL_FEEDBACK_REJECT_LOW_MOBILITY',
	6: 'ENROLL_FEEDBACK_REJECT_OTHER',
	7: 'ENROLL_FEEDBACK_PROGRESS_IMMOBILE',
}

APP_CODES = {
	11: 'FPC_RESULT_FAILURE',
	12: 'FPC_RESULT_INVALID_PARAM',
	13: 'FPC_RESULT_WRONG_STATE',
	14: 'FPC_RESULT_OUT_OF_MEMORY',
	15: 'FPC_RESULT_TIMEOUT',
	16: 'FPC_RESULT_NOT_SUPPORTED',
}

PARSERS = {}

KEY=b'\x60\x39\x3e\xc5\x92\x24\x9c\x4e\xfd\xcf\x04\x4a\x8d\x8e\x48\x46\xcf\xa8\x71\x73\xbe\x5d\xe6\x73\xe2\x4c\x4a\x0c\xa0\xe8\x21\x3c'

def parser(cmd):
    def hook(func):
        PARSERS[cmd] = func
        return func
    return hook
    
@parser(CMD_STATUS)
def parse_state(data):
    event, state, app_fail_code = struct.unpack('<HHH', data[:6])
    states = []
    for key in STATES.keys():
        if (key & state) != 0:
            states.append(STATES[key])
    return {
        'event': EVENTS[event],
        'states': states,
        'app_fail_code': APP_CODES.get(app_fail_code, app_fail_code)
    }

@parser(CMD_NAVIGATION)
def parse_navigation(data):
    gesture, n_samples = struct.unpack('<HH', data[:4])

    return {
        'gesture': NAV_EVENTS[gesture],
        'samples': struct.unpack(f'<{n_samples}H', data[4:])
    }

@parser(CMD_VERSION)
def parse_version(data):
    mcu_id, fw_id, fuse_level, version_length = struct.unpack('<12sBBH', data[:16])

    return {
        'mcu_id': mcu_id,
        'fw_id': fw_id,
        'fuse_level': fuse_level,
        'version': data[16:].decode()
    }

@parser(CMD_ENROLL)
def parse_enroll(data):
    template_id, feedback, samples_remaining = struct.unpack('<HBB', data)

    return {
        'template_id': template_id,
        'feedback': ENROLL_STATES[feedback],
        'samples_remaining': samples_remaining
    }

@parser(CMD_IDENTIFY)
def parse_identify(data):
    identify_result, template_type, template_id, tag = struct.unpack('<HHHH', data)

    return {
        'finger_found': identify_result == 0x61EC,
        'template_id': template_id if identify_result == 0x61EC else None,
        'tag': tag
    }

@parser(CMD_GET_SYSTEM_CONFIG)
def parse_system_config(data):
    type, unknoown1, version, finger_scan_interval, sys_flags, uart_irq_delay, uart_baudrate, max_consecutive_fails, lockout_time, idle_before_sleep, enroll_touches, immobile_touches, i2c_address, unknown = struct.unpack('<HHHHIBBBBHBBHH', data)

    return {
        'type': type,
        'version': version,
        'finger_scan_interval': finger_scan_interval,

        'event_at_boot': sys_flags & 0x001 != 0,
        'uart_stop_mode': sys_flags & 0x010 != 0,
        'irq_before_tx': sys_flags & 0x020 != 0,
        'allow_factory_reset': sys_flags & 0x100 != 0,

        'uart_irq_delay': uart_irq_delay,
        'uart_baudrate': uart_baudrate,
        'max_consecutive_fails': max_consecutive_fails,
        'lockout_time': lockout_time,
        'idle_before_sleep': idle_before_sleep,
        'enroll_touches': enroll_touches,
        'immobile_touches': immobile_touches,
        'i2c_address': i2c_address,
    }

@parser(CMD_GET_TEMPLATE_DATA)
def parse_template_get(data):
    template_id, max_chunk_size, total_size = struct.unpack('<HHH', data)

    return {
        'template_id': template_id,
        'max_chunk_size': max_chunk_size,
        'total_size': total_size
    }

@parser(CMD_DATA_GET)
def parse_data_get(data):
    remaining, data_size = struct.unpack('<II', data[:8])

    return {
        'remaining': remaining,
        'chunk_size': data_size,
        'data': data[8:]
    }

@parser(CMD_IMAGE_DATA)
def parse_image_data(data):
    image_size, width, height, image_type, max_chunk_size = struct.unpack('<IHHHH', data)

    return {
        'size': image_size,
        'width': width,
        'height': height,
        'type': image_type,
        'max_chunk_size': max_chunk_size
    }

def send_packet(data, secure):
    flags = 0x10
    length = len(data)

    if secure:
        length += 28
        flags |= 0x01

    header = struct.pack('<HHHH', 0x04, 0x11, flags, length)

    if secure:
        cipher = AESGCM(KEY)
        nonce = b'\x00' * 12

        data = cipher.encrypt(
            nonce=nonce,
            data=data,
            associated_data=header
        )

        data = nonce + data[-16:] + data[:-16]

    return header + data

def parse_response(data):
    header = data[:8]
    version, type, flags, length = struct.unpack('<HHHH', header)

    secure = (flags & 1) != 0

    if secure:
        iv = data[8:20]
        gmac = data[20:36]

        cipher = AESGCM(KEY)

        data = data[36:]

        response = cipher.decrypt(
            nonce=iv,
            data=data + gmac,
            associated_data=header
        )
    else:
        response = data[8:]

    cmd, type = struct.unpack('<HH', response[:4])
    
    if type == 0x12: # handle response
        return PARSERS[cmd](response[4:])
    elif type == 0x13: # handle event
        return PARSERS[cmd](response[4:])
    else:
        raise RuntimeError('Unknown incoming packet type')

def send_request(request_cmd, payload=[], secure=False):
    data = struct.pack('<HH', request_cmd, 0x11) + bytes(payload)

    return send_packet(data, secure)

def enroll_finger(id=None):
    id_type = 0x4045 if id is None else 0x3034
    id = 0 if id is None else id
    return send_request(CMD_ENROLL, struct.pack('<HH', id_type, id))

    while True:
        response = read_response()

        if response.get('feedback') is None:
            continue

        if response['feedback'].startswith('ENROLL_FEEDBACK_PROGRESS'):
            print(f'touch again, {response["samples_remaining"]} times')
            continue

        print(f'done, feedback: {response["feedback"]}, template id: {response["template_id"]}')
        return

def identify_finger(id=None):
    id_type = 0x2023 if id is None else 0x3034
    id = 0 if id is None else id
    return send_request(CMD_IDENTIFY, struct.pack('<HHH', id_type, id, 0))
        
        
def download_data(total_size, max_chunk_size):
    data = bytes()
    while total_size > 0:
        chunk = min(total_size, max_chunk_size)

        response = send_request(CMD_DATA_GET, struct.pack('<I', chunk))

        assert response['chunk_size'] == chunk
        assert response['remaining'] == (total_size - chunk)

        data += response['data']

        total_size -= chunk

    return data
        
def download_template(id):
    response = send_request(CMD_GET_TEMPLATE_DATA, struct.pack('<HH', id, 0))

    print(response)

    return download_data(response['total_size'], response['max_chunk_size'])

def capture_raw_image():
    send_request(CMD_CAPTURE)
    while True:
        response = read_response()
        print(response)
        if response.get('states') is None:
            continue
        if 'STATE_IMAGE_AVAILABLE' in response['states']:
            response = send_request(CMD_IMAGE_DATA, struct.pack('<HH', 2, 0))
            print(response)
            return download_data(response['size'], response['max_chunk_size'])

def set_system_config(version, finger_scan_interval, event_at_boot, uart_stop_mode, irq_before_tx, allow_factory_reset, uart_irq_delay, uart_baudrate, max_consecutive_fails, lockout_time, idle_before_sleep, enroll_touches, immobile_touches, i2c_address):
    sys_flags = 0

    if event_at_boot:
        sys_flags |= 0x001

    if uart_stop_mode:
        sys_flags |= 0x010

    if irq_before_tx:
        sys_flags |= 0x020

    if allow_factory_reset:
        sys_flags |= 0x100

    payload = struct.pack('<HHIBBBBHBBHH', version, finger_scan_interval, sys_flags, uart_irq_delay, uart_baudrate, max_consecutive_fails, lockout_time, idle_before_sleep, enroll_touches, immobile_touches, i2c_address, 1)

    return send_request(CMD_SET_SYSTEM_CONFIG, payload)

# print(send_request(CMD_STATUS, secure=True))

# sys.exit(0)

# print(set_system_config(2, 34, True, False, False, True, 1, 5, 5, 15, 0, 12, 0, 36))
# print(send_request(CMD_GET_SYSTEM_CONFIG, b'\x01\x00'))
# sys.exit(0)

# print(send_request(CMD_FACTORY_RESET, secure=False))

# sys.exit(0)

# print(send_request(CMD_SET_CRYPTO_KEY,  b'\x20' + KEY + b'\x00', secure=False))
# print(send_request(CMD_SET_CRYPTO_KEY, [16] + [1] * 17))

# sys.exit(0)

# print(send_request(CMD_ABORT))
#enroll_finger()
        
# enroll_finger(1)

# sys.exit(0)
#while True:
#    print(read_response())


while False:
    response = capture_raw_image()
    with open(f'{time.time()}.raw', 'wb') as file:
        file.write(response)
    img = Image.frombytes('L', (96, 100), response)
    img.show()


# print(set_system_config(2, 34, 1, 0, 0, 1, 1, 5, 5, 15, 0, 12, 0, 36))

# print(send_request(CMD_GET_SYSTEM_CONFIG, b'\x01\x00'))
# send_request(CMD_ABORT)

while False:
    result = identify_finger()
    print(result)
    await_ready_state()
    time.sleep(0.01)
