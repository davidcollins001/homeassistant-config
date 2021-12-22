# bt light service - 0000fff3-0000-1000-8000-00805f9b34fbo

# # Device 21:04:77:64:04:AA MELK-OA20

# [MELK-OA20:/service000c/char000d]#
# > bluetoothctl
# > connect "21:04:77:64:04:AA"
# > menu gatt
# > select-attribute fff3
# # color - G,B,R - 4,5,6 (index)
# > write "0x7e 0x07 0x05 0x03 0xff 0x0 0xff 0x16 0xef"

#~$ sudo gatttool -i hci0 -b 21:04:77:64:04:AA --char-write -a 0xfff3 -n 0x7e0705030000ff16ef
# red   # write "0x7e0705030000ff16ef"
# blue  # write "0x7e07050300ff0016ef"
# green # write "0x7e070503ff000016ef"
# off   # write "0x7e07050300000016ef"

import sys
import time
import argparse
import gattlib
import logging
import paho.mqtt.client as mqtt

import setup_log  # noqa

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARN)

DEFAULT_CMD_QUEUE = 'cmnd/lights/floor_lamp'


def use_socket(mac, port):
    # doesn't work
    import socket
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s.connect((mac, port))
    s.send(bytes(text, 'UTF-8'))


def discover(dev="hci0"):
    # need to be root
    d=gattlib.DiscoveryService(dev)
    from pprint import pprint as pp
    pp(d.discover(5))


def characteristics(r):
    from pprint import pprint as pp
    pp(r.discover_characteristics())


def send_data(r, uuid, data):
    handle = _get_handle(r, uuid)

    # write_cmd() has argument error: Boost.Python.ArgumentError
    r.write_by_handle(handle, data)


def _get_full_uuid(r, part_uuid):
    while True:
        try:
            uuid = [desc['uuid'] for desc in r.discover_descriptors()
                    if part_uuid.upper() in desc['uuid'].upper()]
            if not uuid:
                raise ValueError(f"uuid containing {part_uuid} not found")
            return uuid[0]
        except gattlib.BTIOException:
            print("failed to connect, wait and retry")
            time.sleep(2)


def _get_handle(r, uuid):
    cs = r.discover_characteristics()
    # return [c for c in cs if c['uuid'] == uuid][0]['value_handle']
    return [c for c in cs if uuid in c['uuid']][0]['value_handle']


def update_lamp(req, message, c):
    import json
    if 'POWER' in message.topic:
        state = {b'ON': 1, b'OFF': 0}
        p = state[message.payload]
        data = [0x7E, 4, 4, p, 0, p, 0xFF, 0, 0xEF]
    elif 'Dimmer' in message.topic:
        b = int(message.payload)
        data = [0x7E, 4, 1, b, 1, 0xFF, 0xFF, 0, 0xEF]
    elif 'white' in message.topic:
        data = [0x7E, 7, 5, 3, 0xFF, 0xFF, 0xFF, 16, 0xEF]
    elif 'Color' in message.topic:
        r, g, b = message.payload.decode().split(',')
        data = [0x7E, 7, 5, 3, int(g), int(b), int(r), 16, 0xEF]

    logger.info(f"payload: {data}")
    try:
        send_data(req, uuid, bytes(data))
    except Exception as e:
        logger.error(f"failed to process data: {data}: {e}")


def manager(mac, uuid, queue=DEFAULT_CMD_QUEUE):
    client = mqtt.Client()

    req = gattlib.GATTRequester(mac)

    # get full uuid - assume a full uuid contains '-' separating values
    if '-' not in uuid:
        uuid = _get_full_uuid(req, uuid)

    # on reconnect subscriptions will be renewed
    client.on_connect = lambda c, d, f, rc: c.subscribe(f"{queue}/+")
    client.on_message = lambda c, d, m: update_lamp(req, m, c)

    client.connect("homeassistant", 1883, 60)
    logger.info(f"connected to {queue}")

    # block and processes network traffic, callbacks and reconnecting
    client.loop_forever()
    # client.loop_start()
    # client.loop_stop()

    return client


def parse_args(args=sys.argv):
    parser = argparse.ArgumentParser(description='Floor light')
    # group = parser.add_mutually_exclusive_group()
    parser.add_argument('-p', '--power', type=int, help='Switch on/off')
    parser.add_argument('-c', '--color', help='RGB hex color "RRGGBB"')
    parser.add_argument('-C', '--color2', type=int, help='color, brightness?')
    parser.add_argument('-b', '--brightness', type=int,
                        help='Light brightness, [0-100]')
    parser.add_argument('-s', '--sequence', type=int, help='pin sequence')
    parser.add_argument('-w', '--warmth', type=lambda x: map(int, x.split(',')),
                        help='RGB hex color "RRGGBB"')
    parser.add_argument('-m', '--manager', action='store_true',
                        help='Start bluetooth backend')

    args = parser.parse_args()
    return args


if __name__ == "__main__":
    args = parse_args()

    mac = "21:04:77:64:04:AA"
    uuid = 'fff3'
    queue= DEFAULT_CMD_QUEUE

    if args.manager:
        manager(mac, uuid, queue)
    elif args.power is not None:
        p = args.power
        data = [0x7E, 4, 4, p, 0, p, 0xFF, 0, 0xEF]
    elif args.color:
        color = args.color
        r, g, b = [color[i:i+2] for i in range(0, len(color), 2)]
        data = [0x7E, 7, 5, 3, int(g, 16), int(b, 16), int(r, 16), 16, 0xEF]
    elif args.brightness:
        b = args.brightness
        data = [0x7E, 4, 1, b, 1, 0xFF, 0xFF, 0, 0xEF]
    elif args.warmth:
         w, c = args.warmth
         data = [0x7E, 6, 5, 2, w, c, 0xFF, 16, 0xEF,]
    elif args.color2:
        c = args.color2
        data = 0x7E, 5, 5, 1, c, 0xFF, 0xFF, 16, 0xEF,
    elif args.sequence:
        # 66051
        s1 = (args.sequence >> 16 & 0xFF)
        s2 = (args.sequence >> 8 & 0xFF)
        s3 = (args.sequence & 0xFF)
        data = [0x7F, 6, 0x81, s1, s2, s3, 0xFF, 0, 0xEF]

    # pp(r.discover_characteristics())
    # data = [0x7e, 0x04, 0x04, 224, 0x3,  0x1, 0xff, 0x0, 0xef]

    client = mqtt.Client()
    client.connect("homeassistant", 1883, 60)
    logger.info(f"connected to {queue}")

    client.publish(queue, bytes(data))
