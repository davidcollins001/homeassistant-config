#!/usr/bin/python

# requirements:
#   apt: python3-paho-mqtt, python3-spidev
#   pip: telebot

import sys
import argparse
import logging
import queue
import paho.mqtt.client as mqtt
import json
import datetime as dt

import rf
import boiler as rf_boiler

# TODO: this should be imported automatically from __init__
import setup_log  # noqa

logger = logging.getLogger("radio")
# logger.setLevel(logging.INFO)
# logger.setLevel(logging.DEBUG)
logger.setLevel(logging.WARNING)

RF_FREQ = 86926
BOILER_FREQ = 86826
RF_GROUP = 0xB6
RF_NODE = 0xB

radio = None
boiler = None


def parse_args(args=sys.argv):
    parser = argparse.ArgumentParser(description='RFM69 driver')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-s', '--sender', action='store_true',
                       help='continuously send radio packets')
    group.add_argument('-l', '--listen', action='store_true',
                       help='continuously listen for radio packets')
    group.add_argument('-m', '--manager', action='store_true',
                       help='start manager for listening and messaging boiler')
    group.add_argument('-d', '--dump', action='store_true',
                       help='dump the radio config')
    group.add_argument('-r', '--reset', action='store_true',
                       help='reset the radio')

    args = parser.parse_args()
    return args


def radio_setup(rf_freq=RF_FREQ):
    radio = rf.DataGram(driver=rf.RFM69())
    radio.init(rf_freq, nodeid=RF_NODE, group=RF_GROUP, modem=rf.GFSK_Rb250Fd250)
    return radio


def notify_radio(radio, switch, cmd_queue):
    # notify radio listener to stop
    cmd_queue.put(switch)
    radio.driver.payload_ready.put(rf.Exit)


def boiler_setup(radio, queue, cmd_queue):
    global boiler
    boiler = rf_boiler.Boiler(BOILER_FREQ)  # , driver=radio.driver)

    client = mqtt.Client()

    # on reconnect subscriptions will be renewed
    client.on_connect = lambda c, d, f, rc: c.subscribe(queue)
    client.on_message = lambda c, d, m: notify_radio(radio, int(m.payload),
                                                     cmd_queue)

    client.connect("homeassistant", 1883, 60)
    logger.info(f"connected to {queue}")

    # block and process network traffic, callbacks and reconnecting
    # client.loop_forever()
    client.loop_start()
    # client.loop_stop()

    return client


def radio_reinit(radio):
    radio.driver.init(radio.driver.freq, radio.driver.group,
                      radio.driver.nodeid, radio.driver.modem,
                      radio.driver.config)


def boiler_cmd(radio, cmd):
    # radio.close()
    radio_reinit(boiler)
    try:
        logger.info(f"switching boiler: {cmd}")
        # send boiler message
        boiler.switch(cmd)
    finally:
        # radio.close()
        # radio = radio_setup()
        radio_reinit(radio)

    return radio


def publish(client, payload):
    def sensor_794296394(raw_data):
        sensor_queue = "state/sensor/794296394"
        sensor_keys = ['hwid', 'iaq2', 'iaq', 'adc_temp', 'temperature',
                       'pressure', 'humidity', 'gas_res']
        # publish temp
        data = dict(zip(sensor_keys, raw_data))
        data['temperature'] /= 100
        data['timestamp'] = dt.datetime.now().timestamp()
        client.publish(sensor_queue, json.dumps(data))
        # client.publish(sensor_queue, data['temperature'] / 100)

    def sensor_1985242708(raw_data):
        sensor_queue = "state/sensor/1985242708"
        sensor_keys = ['hwid', 'temperature', 'humidity']
        # publish temp
        data = dict(zip(sensor_keys, raw_data))
        data['temperature'] /= 100
        data['timestamp'] = dt.datetime.now().timestamp()
        client.publish(sensor_queue, json.dumps(data))
        # client.publish(sensor_queue, data['temperature'] / 100)

    def sensor_1985242708(raw_data):
        sensor_queue = "state/sensor/1985242708"
        sensor_keys = ['hwid', 'door_event']
        # publish temp
        data = dict(zip(sensor_keys, raw_data))
        data['timestamp'] = dt.datetime.now().timestamp()
        client.publish(sensor_queue, json.dumps(data))

    try:
        logger.debug("processing payload")
        data = rf.Varint.decode_varint(payload.data)
        if radio.driver.error:
            logger.info(radio.driver.error)
        radio.driver.error = None

        logger.debug(f" RF69 ({hex(payload.rssi)}) "
                     f"[{payload.addr} -> {payload.to}] {payload.flags} {data}")

        locals()[f'sensor_{data[0]}'](data)
    except Exception as e:
        try:
            logger.error(f"Failed to process message: {payload._data}: {e}")
        except Exception as e:
            logger.error(f"Failed to process message: {payload}: {e}")


def manager():
    """Wait for rf messages and wait on boiler commands"""
    boiler_queue = "cmnd/sensor/heating"

    global radio
    radio = radio_setup(RF_FREQ)
    cmd_queue = queue.SimpleQueue()
    client = boiler_setup(radio, boiler_queue, cmd_queue)
    radio_reinit(radio)

    # drop bogus messages
    logger.debug(f">> {radio.driver.recv()}")
    logger.debug(f">> {radio.driver.can_send.is_set()}")
    # while not radio.driver.recv():
        # logger.debug(f">> {radio.driver.payload}")

    while not cmd_queue.empty():
        cmd_queue.get()
    while not radio.driver.payload_ready.empty():
        radio.driver.payload_ready.get()

    radio.driver.can_send.set()
    logger.debug(f"{radio.driver.payload_ready.qsize()}")
    logger.debug(f">>> {radio.driver.can_send.is_set()}")

    while True:
        if radio.recv():
            logger.debug("publish message")
            publish(client, radio.payload)
        else:
            logger.debug("waiting to send boiler cmd")
            radio.driver.can_send.wait()
            # process all messages
            while cmd_queue.qsize():
                radio = boiler_cmd(radio, cmd_queue.get())


def listener():
    radio = radio_setup(RF_FREQ)
    if radio.driver._fixed_payload:
        pkt_fn = rf.show_fixed_packet
    else:
        pkt_fn = rf.show_packet
    rf.listener(radio, pkt_fn)


def run():
    # TODO: sort args
    # args = parse_args()

    operations = {
        '-s': lambda: rf.sender(radio_setup(RF_FREQ), data=None, count=-1),
        '-l': listener,
        '-m': manager,
        '-d': radio_setup(RF_FREQ).driver.dump_regs,
        '-r': radio_setup(RF_FREQ).driver.reset,
    }
    action = operations[sys.argv[1]]
    action()


if __name__ == "__main__":
    run()
