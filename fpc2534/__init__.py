import struct
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import random

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

class FPC2534:
    def __init__(self, key=None):
        self._key = key
    
    def parser(cmd):
        def hook(func):
            PARSERS[cmd] = func
            return func
        return hook
        
    @parser(CMD_STATUS)
    def _parse_state(data):
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
    def _parse_navigation(data):
        gesture, n_samples = struct.unpack('<HH', data[:4])

        return {
            'gesture': NAV_EVENTS[gesture],
            'samples': struct.unpack(f'<{n_samples}H', data[4:])
        }

    @parser(CMD_VERSION)
    def _parse_version(data):
        mcu_id, fw_id, fuse_level, version_length = struct.unpack('<12sBBH', data[:16])

        return {
            'mcu_id': mcu_id,
            'fw_id': fw_id,
            'fuse_level': fuse_level,
            'version': data[16:].decode()
        }

    @parser(CMD_ENROLL)
    def _parse_enroll(data):
        template_id, feedback, samples_remaining = struct.unpack('<HBB', data)

        return {
            'template_id': template_id,
            'feedback': ENROLL_STATES[feedback],
            'samples_remaining': samples_remaining
        }

    @parser(CMD_IDENTIFY)
    def _parse_identify(data):
        identify_result, template_type, template_id, tag = struct.unpack('<HHHH', data)

        return {
            'finger_found': identify_result == 0x61EC,
            'template_id': template_id if identify_result == 0x61EC else None,
            'tag': tag
        }

    @parser(CMD_GET_SYSTEM_CONFIG)
    def _parse_system_config(data):
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
    def _parse_template_get(data):
        template_id, max_chunk_size, total_size = struct.unpack('<HHH', data)

        return {
            'template_id': template_id,
            'max_chunk_size': max_chunk_size,
            'total_size': total_size
        }

    @parser(CMD_DATA_GET)
    def _parse_data_get(data):
        remaining, data_size = struct.unpack('<II', data[:8])

        return {
            'remaining': remaining,
            'chunk_size': data_size,
            'data': data[8:]
        }

    @parser(CMD_IMAGE_DATA)
    def _parse_image_data(data):
        image_size, width, height, image_type, max_chunk_size = struct.unpack('<IHHHH', data)

        return {
            'size': image_size,
            'width': width,
            'height': height,
            'type': image_type,
            'max_chunk_size': max_chunk_size
        }
    
    @parser(CMD_PUT_TEMPLATE_DATA)
    def _parse_put_template_data(data):
        id, chunk_size, total_size = struct.unpack('<HHH', data)

        return {
            'id': id,
            'chunk_size': chunk_size,
            'total_size': total_size
        }
    
    @parser(CMD_DATA_PUT)
    def _parse_data_put(data):
        return {
            'total_received': struct.unpack('<I', data)
        }
        
    @parser(CMD_LIST_TEMPLATES)
    def _parse_list_templates(data):
        short_count = int(len(data) / 2)
        # first entry is count of ids
        return {
            'template_ids': struct.unpack(f'<{short_count}H', data)[1:]
        }

    def _wrap_packet(self, data):
        flags = 0x10
        length = len(data)
        
        secure = (self._key is not None)

        if secure:
            length += 28
            flags |= 0x01

        header = struct.pack('<HHHH', 0x04, 0x11, flags, length)

        if secure:
            cipher = AESGCM(self._key)
            nonce = random.randbytes(12)

            data = cipher.encrypt(
                nonce=nonce,
                data=data,
                associated_data=header
            )

            data = nonce + data[-16:] + data[:-16]

        return header + data

    def parse_response(self, data):
        header = data[:8]
        version, type, flags, length = struct.unpack('<HHHH', header)

        secure = (flags & 1) != 0

        if secure:
            if self._key is None:
                raise RuntimeError('Encrypted response, but no key set')
            
            iv = data[8:20]
            gmac = data[20:36]

            cipher = AESGCM(self._key)

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

    def encode_request(self, request_cmd, payload=[]):
        data = struct.pack('<HH', request_cmd, 0x11) + bytes(payload)

        return self._wrap_packet(data)
    
    def request_image_data(self):
        return self.encode_request(CMD_IMAGE_DATA, struct.pack('<I', 2))
    
    def abort(self):
        return self.encode_request(CMD_ABORT)

    def enroll_finger(self, id=None):
        id_type = 0x4045 if id is None else 0x3034
        id = 0 if id is None else id
        return self.encode_request(CMD_ENROLL, struct.pack('<HH', id_type, id))

    def set_key(self, key):
        if len(key) not in [16, 32]:
            raise RuntimeError('key must be of length 16 or 32')
        return self.encode_request(
            CMD_SET_CRYPTO_KEY,
            struct.pack(f'<B{len(key)}B', len(key), *key)
        )

    def identify_finger(self, id=None):
        id_type = 0x2023 if id is None else 0x3034
        id = 0 if id is None else id
        return self.encode_request(CMD_IDENTIFY, struct.pack('<HHH', id_type, id, 0))
    
    def upload_template(self, id, size):
        return self.encode_request(CMD_PUT_TEMPLATE_DATA, struct.pack('<HH', id, size))
    
    def download_template(self, id):
        return self.encode_request(CMD_GET_TEMPLATE_DATA, struct.pack('<HH', id, 0))
    
    def delete_template(self, id):
        return self.encode_request(CMD_DELETE_TEMPLATE, struct.pack('<HH', 0x3034, id))
    
    def data_put(self, remaining_size, data):
        payload = struct.pack('<II', remaining_size, len(data)) + data
        return self.encode_request(CMD_DATA_PUT, payload)
    
    def data_get(self, chunk_size):
        return self.encode_request(CMD_DATA_GET, struct.pack('<I', chunk_size))
    
    def get_system_config(self, default=False):
        return self.encode_request(CMD_GET_SYSTEM_CONFIG, struct.pack('<H', int(not default)))
            
    def set_system_config(self, version, finger_scan_interval, event_at_boot, uart_stop_mode, irq_before_tx, allow_factory_reset, uart_irq_delay, uart_baudrate, max_consecutive_fails, lockout_time, idle_before_sleep, enroll_touches, immobile_touches, i2c_address):
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

        return self.encode_request(CMD_SET_SYSTEM_CONFIG, payload)
    
    def reset(self):
        return self.encode_request(CMD_RESET)