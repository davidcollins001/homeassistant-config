import sys
import time
import argparse
import gatt
import logging
import datetime as dt
import paho.mqtt.client as mqtt

import setup_log  # noqa

logger = logging.getLogger("bt_backend")
logger.setLevel(logging.WARN)

DEFAULT_CMD_QUEUE = 'cmnd/lights/#'


def use_socket(mac, port):
    # doesn't work
    import socket
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s.connect((mac, port))
    s.send(bytes(text, 'UTF-8'))


class Manager(gatt.DeviceManager):
    def device_discovered(self, device):
        if "Hue Lamp" in device.alias():
            if not device.is_connected():
                logging.info(f"{dt.datetime.now()} [{device.mac_address}] "
                             f"reconnect: {device.alias()}")
                device.connect()

class BTDevice(gatt.Device):
    def __init__(self, mac_address, manager, uuids=None, auto_reconnect=False,
                 **kwargs):
        super().__init__(mac_address=mac_address, manager=manager, **kwargs)
        self.auto_reconnect = auto_reconnect
        self.uuids = uuids or self.UUIDS
        self.control = {}

    def connect_failed(self, error):
        super().connect_failed(error)
        logger.error(f"[self.mac_address] Connection failed: {error}")

    def services_resolved(self):
        super().services_resolved()

        for service in self.services:
            for characteristic in service.characteristics:
                cmd_uuid = self.uuids.get(characteristic.uuid)
                if cmd_uuid:
                    self.control[cmd_uuid] = characteristic
                    # TODO: needed to make write commands work
                    characteristic.read_value()


class HueDevice(BTDevice):
    UUIDS = {"932c32bd-0002-47a2-835a-a8d455b859dd": "power",
             "932c32bd-0003-47a2-835a-a8d455b859dd": "dimmer"}

    def update(self, command, message):
        p = int(message.payload)
        cmds = {'POWER': self.power,
                'Dimmer': self.dimmer}
        try:
            cmds.get(command)(p)
        except Exception as e:
            logger.exception(f"failed to process data: {p}: {e}")

    def power(self, state):
        self.control['power'].write_value([state])

    def dimmer(self, state):
        msg = "Invalid dimmer value: {state} must be > 0 and < 255"
        assert (state > 0) and (state < 255), msg
        self.control['dimmer'].write_value([state])


class LedStripDevice(BTDevice):
    UUIDS = {"0000fff3-0000-1000-8000-00805f9b34fb": "command"}

    def write_value(self, data):
        self.control["command"].write_value(data)

    def update(self, command, message):
        if command == 'POWER':
            p = int(message.payload)
            data = [0x7E, 4, 4, p, 0, p, 0xFF, 0, 0xEF]
        elif command == 'Dimmer':
            p = int(message.payload)
            data = [0x7E, 4, 1, p, 1, 0xFF, 0xFF, 0, 0xEF]
        elif command == 'white':
            # TODO: this probably isn't white - maybe brightness?
            # read color (or 0xFF) multiply color by brightness
            # r, g, b = message.payload.decode().split(',')
            p = int(message.payload)
            if p:
                logger.warning(f"what should be done with white {p}")
                data = [0x7E, 7, 5, 3, p * 0xFF, p * 0xFF, p * 0xFF, 16, 0xEF]
            else:
                return
        elif command == 'Color':
            r, g, b = message.payload.decode().split(',')
            data = [0x7E, 7, 5, 3, int(g), int(b), int(r), 16, 0xEF]
        elif command == 'command':
            # pass data through to bt device
            data = list(map(lambda n: int(n), message.payload))

        try:
            self.write_value(data)
        except Exception as e:
            logger.exception(f"failed to process data: {data}: {e}")


def dispatch_message(devices, message, c):
    logger.info(f"received payload on {message.topic}: {message.payload}")
    *base_topic, device, command = message.topic.split('/')
    req = devices.get(device)
    # ignore mqtt messages for other lights
    if req:
        req.update(command, message)


def manager(devices, device="hci0", queue=DEFAULT_CMD_QUEUE):
    # dev_man = gatt.DeviceManager(device)
    dev_man = Manager(device)

    # connect to led floor lamp
    led = LedStripDevice(macs['led_strip'], dev_man, auto_reconnect=True)
    led.connect()

    # connect to hue lamp
    hue = HueDevice(macs['hue_lamp_1'], dev_man, auto_reconnect=True)
    hue.connect()

    devices = {'led_strip': led,
               'hue_lamp_1': hue}

    # on reconnect subscriptions will be renewed
    client = mqtt.Client()
    client.on_connect = lambda c, d, f, rc: c.subscribe(queue)
    client.on_message = lambda c, d, m: dispatch_message(devices, m, c)
    client.connect("homeassistant", 1883, 60)
    logger.info(f"connected to {queue}")

    # block and process network traffic, callbacks and reconnecting
    # client.loop_forever()
    client.loop_start()
    # client.loop_stop()

    # watch for broadcast to reconnect if necessary
    dev_man.start_discovery()
    dev_man.run()

    return client


def parse_args(args=sys.argv):
    parser = argparse.ArgumentParser(description='Bluetooth agent')
    parser.add_argument('-m', '--manager', action='store_true',
                        help='Start bluetooth backend')
    parser.add_argument('-c', '--command', action='store_true',
                        help='Send test commands to led strip')

    args = parser.parse_args()
    return args


def _test_command():
    # TODO: test data;
    # (126, 4, 4, (240|224|16|0), 3, (1|0), -1, 0, -17)
    # for f in '\x04\x04\xf0\x03\x01\xff\x00'
    #           '\x04\x04\xf0\x03\x00\xff\x00'
    #           '\x04\x04\xe0\x03\x01\xff\x00'
    #           '\x04\x04\xe0\x03\x00\xff\x00'
    #           '\x04\x04\x10\x03\x01\xff\x00'
    #           '\x04\x04\x10\x03\x00\xff\x00'
    #           '\x04\x04\x00\x03\x01\xff\x00'
    #           '\x04\x04\x00\x03\x00\xff\x00' ; do
    #   echo $f; echo -ne $f | mosquitto_pub -h homeassistant -t \
            #       cmnd/lights/led_strip/command -s; sleep 60; done

    # pin_seq = [6, 0x81, (s >> 16 & 0xFF), (s >> 8 & 0xFF), (s & 0xFF), 255, 0]
    # brightness/mode
    b, mode = 1, 1
    mr_mo = [4, 1, b, mode, 0xFF, 0xFF, 0]
    # speed
    s = 0x10
    speed = [4, 2, s, 255, 255, 255, 0]

     # s = intent.getIntExtra("wl.extra.bluetoothle.timing.hour.minute.second", 0);
     # m = intent.getIntExtra("wl.extra.bluetoothle.timing.mode", 0);
     # w = intent.getIntExtra("wl.extra.bluetoothle.timing.weeks", 0);
     # data = [8; 0x82; int(g); int(b); int(r); m; w]

    client = mqtt.Client()
    client.connect("homeassistant", 1883, 60)
    logger.info(f"connected to {queue}")
    client.publish("cmnd/lights/led_strip/command", data)


if __name__ == "__main__":
    args = parse_args()

    # hue mac = "CE:96:CB:60:81:74"
    # led mac = "FC:4F:22:91:4D:23",
    macs = {"led_strip": "21:04:77:64:04:AA",
            "hue_lamp_1": "CB:80:F1:7D:0A:34"}
    queue= DEFAULT_CMD_QUEUE

    if args.manager:
        manager(macs, queue=queue)
    elif args.command is not None:
        p = args.power
        _test_command()
