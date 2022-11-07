import sys
import time
import pydbus
import argparse
import logging
import paho.mqtt.client as mqtt

import setup_log  # noqa

# To recover from the following error reset the bulb and connect/pair in
# bluetoothctl:
#   object does not export any interfaces; you might need to pass object path as
#   the 2nd argument for get()


logger = logging.getLogger("bt_backend")
logger.setLevel(logging.WARN)

DEFAULT_CMD_QUEUE = 'cmnd/lights/#'
STATE_QUEUE = 'state/lights/{}/{}'

# DBus object paths
BLUEZ_SERVICE = 'org.bluez'
ADAPTER_PATH = '/org/bluez/hci0'


def hue_lamp_update(device, command, message):
    connection = device.get("connection")[command]

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


def led_strip_update(device, command, message):
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

    connection = device.get("connection")
    connection.WriteValue(bytes(data), None)


def dbus_connect(peripheral_address, uuids, callback=None):
    def get_characteristic(bus, dev_path, uuid_name, uuid):
        """Look up DBus path for characteristic UUID"""
        mngr = bus.get(BLUEZ_SERVICE, '/')
        mng_objs = mngr.GetManagedObjects()
        for path in mng_objs:
            chr_uuid = mng_objs[path].get('org.bluez.GattCharacteristic1',
                                          {}).get('UUID')
            if path.startswith(dev_path) and chr_uuid == uuid.casefold():
                char = bus.get(BLUEZ_SERVICE, path)
                if callback:
                    # import pdb; pdb.set_trace()  # noqa
                    fn, mqtt, dev_name = callback
                    char.onPropertiesChanged = fn(
                        STATE_QUEUE.format(dev_name, uuid_name), mqtt
                    )
                    char.StartNotify()
                return char

    bus = pydbus.SystemBus()
    # adapter = bus.get(BLUEZ_SERVICE, ADAPTER_PATH)
    device_path = f"{ADAPTER_PATH}/dev_{peripheral_address.replace(':', '_')}"
    device = bus.get(BLUEZ_SERVICE, device_path)

    device.Connect()
    if not device.Connected:
        raise Exception("Connection failed")

    if isinstance(uuids, dict):
        chars = {uuid_name: get_characteristic(bus, device_path, uuid_name, uuid)
                 for uuid_name, uuid in uuids.items()}
    else:
        chars = get_characteristic(bus, device_path, uuids)

    return chars


def ble_connect(fn):
    def inner(devices, message, callback):
        *base_topic, dev_name, command = message.topic.split('/')
        # ignore mqtt messages for other lights
        if not devices.get(dev_name):
            return

        for _ in range(5):
            try:
                if not devices[dev_name].get("connection"):
                    mac = devices[dev_name]['mac']
                    uuids = devices[dev_name]['uuids']
                    devices[dev_name]["connection"] = dbus_connect(mac, uuids,
                                                                   callback +
                                                                   (dev_name,))
                return fn(devices, message)
            except Exception as e:
                logger.error(f"failed to write ({e}), reconnecting: {dev_name}")
                time.sleep(0.2)
                # if conn:
                    # conn.disconnect()
                if "connection" in devices[dev_name]:
                    del devices[dev_name]["connection"]
                devices[dev_name]["connection"] = None

        logger.warning("failed to switch")

    return inner


@ble_connect
def dispatch_message(devices, message):
    logger.info(f"received payload on {message.topic}: {message.payload}")
    *base_topic, dev_name, command = message.topic.split('/')
    # import pdb; pdb.set_trace()  # noqa
    device = devices.get(dev_name)
    update = globals()[f"{dev_name}_update"]
    return update(device, command, message)


# def update_cb(m, c):
def update_cb(topic, c):
    print("setup callback: ", topic)

    def inner(char, data, *args):
        # org.bluez.GattCharacteristic1 {'Notifying': True} []
        print(char, type(char))
        print(topic, char, data, *args)
        # TODO: what is characteristic to get notification value update
        value = data.get("Value", None)
        if value:
            # c.publish(m.topic.replace('cmnd', 'state'), value[0])
            c.publish(topic, value[0])
    return inner


def manager(devices, device="hci0", queue=DEFAULT_CMD_QUEUE):
    # on reconnect subscriptions will be renewed
    client = mqtt.Client()
    client.on_connect = lambda c, d, f, rc: c.subscribe(queue)
    client.on_message = lambda c, d, m: dispatch_message(devices, m,
                                                         (update_cb, c))
    # import pdb; pdb.set_trace()  # noqa
    client.connect("homeassistant", 1883, 60)
    logger.info(f"connected to {queue}")

    # block and process network traffic, callbacks and reconnecting
    # client.loop_forever()
    client.loop_start()
    # client.loop_stop()

    from gi.repository import GLib
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


if __name__ == "__main__":
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
    elif args.notify:
        notify()
