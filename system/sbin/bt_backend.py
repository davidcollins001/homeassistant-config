import sys
import pydbus
import argparse
import logging
import functools as ft
from gi.repository import GLib
import paho.mqtt.client as mqtt

import setup_log  # noqa

# To recover from the following error reset the bulb and connect/pair in
# bluetoothctl:
#   object does not export any interfaces; you might need to pass object path as
#   the 2nd argument for get()

# busctl tree org.bluez
# busctl introspect org.bluez /org/bluez/hci0/dev_D1_DC_DF_EA_C4_D3

logger = logging.getLogger("bt_backend")
logger.setLevel(logging.WARN)

DEFAULT_CMD_QUEUE = 'cmnd/lights/#'
STATE_QUEUE = 'state/lights/{}/{}'

# DBus object paths
BLUEZ_SERVICE = 'org.bluez'
ADAPTER_PATH = '/org/bluez/hci0'


def hue_lamp_update(connection, command, message):
    valid_ranges = {
        'POWER': (0, 1),
        'Dimmer': (0, 255),
    }

    state = int(message.payload)
    valid_range = valid_ranges.get(command)
    msg = (f"Invalid {command.lower()} value: {state} must "
           f"be between {valid_range[0]} and valid_range[1]")
    assert (state >= valid_range[0]) and (state <= valid_range[1]), msg
    connection.WriteValue([state], None)


hue_lamp_1_update = hue_lamp_update
hue_lamp_2_update = hue_lamp_update


def led_strip_update(connection, command, message):
    if command == 'POWER':
        p = int(message.payload)
        data = [0x7E, 4, 4, p, 0, p, 0xFF, 0, 0xEF]
    elif command == 'Dimmer':
        p = int(message.payload)
        data = [0x7E, 4, 1, p, 1, 0xFF, 0xFF, 0, 0xEF]
    elif command == 'white':
        p = int(message.payload)
        # data = [0x7E, 5, 5, 1, p, 0xFF, 0xFF, 16, 0xEF]
        # This sets white, but breaks other things
        return
    elif command == 'Color':
        r, g, b = message.payload.decode().split(',')
        data = [0x7E, 7, 5, 3, int(g), int(b), int(r), 16, 0xEF]
    elif command == 'effect':
        # payload matches `effect_list` in light definition
        p = int(message.payload)
        data = [0x7E, 5, 3, p, 6, 0xFF, 0xFF, 0, 0xEF]
    elif command == 'command':
        # pass data through to bt device
        data = list(map(int, message.payload))

    # p = lambda x: int(x)
    # rgb = lambda x: message.payload.decode().split(',')
    # {
        # 'POWER': (p, [0x7E, 4, 4, p, 0, p, 0xFF, 0, 0xEF]),
        # 'Dimmer': (p, [0x7E, 4, 1, p, 1, 0xFF, 0xFF, 0, 0xEF]),
        # # This sets white, but breaks other things
        # # 'white': (p, [0x7E, 5, 5, 1, p, 0xFF, 0xFF, 16, 0xEF]),
        # # return
        # 'Color': (rgb, [0x7E, 7, 5, 3, int(g), int(b), int(r), 16, 0xEF]),
        # # payload matches `effect_list` in light definition
        # 'effect': (p, [0x7E, 5, 3, p, 6, 0xFF, 0xFF, 0, 0xEF]),
        # # pass data through to bt device
        # 'command': (lambda x: x, list(map(int, message.payload)))
    # }

    connection.WriteValue(bytes(data), None)


def get_characteristic(dev_name, uuid_name, uuid):
    """Look up DBus path for characteristic UUID"""
    bus = pydbus.SystemBus()
    mngr = bus.get(BLUEZ_SERVICE, '/')
    mng_objs = mngr.GetManagedObjects()
    for path in mng_objs:
        chr_uuid = mng_objs[path].get('org.bluez.GattCharacteristic1', {}).get('UUID')
        dev_name = dev_name.replace(":", "_")
        if dev_name in path and chr_uuid == uuid.casefold():
            return bus.get(BLUEZ_SERVICE, path)


def add_notify_cb(char, dev_name, uuid_name, callback):
    char.onPropertiesChanged = ft.partial(
        callback, STATE_QUEUE.format(dev_name, uuid_name),
    )
    char.StartNotify()
    return char


def ble_connect(fn):
    connections = {}

    def dbus_connect(devices, dev_name, callback):
        logger.debug(f"connecting to {dev_name}")
        address = devices[dev_name]['mac']
        uuids = devices[dev_name]['uuids']

        bus = pydbus.SystemBus()
        device_path = f"{ADAPTER_PATH}/dev_{address.replace(':', '_')}"
        device = bus.get(BLUEZ_SERVICE, device_path)

        if not device.Connected:
            device.Connect()

        return {uuid_name: add_notify_cb(
            get_characteristic(address, uuid_name, uuid),
            dev_name, uuid_name, callback
        ) for uuid_name, uuid in uuids.items()}

    def execute(client, userdata, message):
        *base_topic, dev_name, command = message.topic.split('/')
        callback = userdata["callback"]
        devices = userdata["devices"]
        # ignore mqtt messages for other lights
        if not devices.get(dev_name):
            return

        for i in range(2):
            try:
                device = connections.get(dev_name)
                if not device:
                    device = dbus_connect(devices, dev_name, callback)
                    connections[dev_name] = device
                if device:
                    return fn(device, message)
            except Exception as e:
                logger.error(f"failed to write: {e}")
                if dev_name in connections:
                    del connections[dev_name]
    return execute


@ble_connect
def mqtt_message_cb(device, message):
    # received a message on MQTT to process, send message to bluetoth dev
    logger.debug(f"received payload on {message.topic}: {message.payload}")
    *base_topic, dev_name, command = message.topic.split('/')
    connection = device[command]
    update = globals()[f"{dev_name}_update"]
    return update(connection, command, message)


def bt_notify_cb(client, topic, char, data, *args):
    # received data from DBUS/bluetooth, publish to state topic
    logger.debug(f"received bt update {topic}, {char}, {data}")
    value = data.get("Value", None)
    if value:
        client.publish(topic, value[0])


def manager(devices, device="hci0", queue=DEFAULT_CMD_QUEUE):
    # on reconnect subscriptions will be renewed
    client = mqtt.Client()
    client.user_data_set(userdata=dict(callback=ft.partial(bt_notify_cb,
                                                           client),
                                       devices=devices))
    client.on_connect = lambda c, d, f, rc: c.subscribe(queue)
    client.on_message = mqtt_message_cb
    client.connect("homeassistant", 1883, 60)
    logger.info(f"connected to {queue}")

    # block and process network traffic, callbacks and reconnecting
    # client.loop_forever()
    client.loop_start()
    # client.loop_stop()

    mainloop = GLib.MainLoop()
    mainloop.run()

    return client


def parse_args(args=sys.argv):
    parser = argparse.ArgumentParser(description='Bluetooth agent')
    parser.add_argument('-m', '--manager', action='store_true',
                        help='Start bluetooth backend')
    parser.add_argument('-c', '--command', action='store_true',
                        help='Send test commands to led strip')

    args = parser.parse_args()
    return args


def main():
    args = parse_args()

    devices = {
        "led_strip": {"mac": "21:04:77:64:04:AA",
                      "uuids": "0000fff3-0000-1000-8000-00805f9b34fb"},
        "hue_lamp_1": {"mac": "FE:BF:DA:ED:C2:50",
                       "uuids": {"POWER": "932c32bd-0002-47a2-835a-a8d455b859dd",
                                 "Dimmer": "932c32bd-0003-47a2-835a-a8d455b859dd"}},
        "hue_lamp_2": {"mac": "D1:DC:DF:EA:C4:D3",
                       "uuids": {"POWER": "932c32bd-0002-47a2-835a-a8d455b859dd",
                                 "Dimmer": "932c32bd-0003-47a2-835a-a8d455b859dd"}},
    }

    if args.manager:
        manager(devices, queue=DEFAULT_CMD_QUEUE)
    # elif args.command is not None:
        # p = args.power
        # _test_command()


if __name__ == "__main__":
    main()
