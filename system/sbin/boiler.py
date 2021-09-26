#!/usr/bin/python

import sys
import time
import argparse
import paho.mqtt.client as mqtt
import logging
import setup_log  # noqa

import rf

logger = logging.getLogger("radio")
# logger.setLevel(logging.INFO)
logger.setLevel(logging.DEBUG)


BOILER_PKT_GAP = 30e-3
BOILER_PKT_COUNT = 30
BOILER_GROUP = 0xD4

BOILER_CONFIG = [
    (0x0B, 0x20),             # low M
    (0x11, 0x5F),             # use PA1
    (0x29, 0xC4),             # RSSI thres -98dB
    (0x2B, 0x40),             # no RSSI timeout
    (0x2D, 0x03),             # Preamble 3 bytes
    (0x2E, 0x90),             # sync size 3 bytes
    (0x2F, 0xAA),             # sync1: 0xAA -- really last preamble byte
    (0x30, 0x2D),             # sync2: 0x2D -- actual sync byte
    (0x38, 0x04),             # max 4 byte payload
    (0x39, 0x00),             # node filtering
    (0x3D, 0x12),             # PacketConfig2, interpkt = 1, autorxrestart on
    (0x6F, 0x20),             # Test DAGC
    # (0x71, 0x02),             # RegTestAfc
]

MODEM = [
    (0x02, 0x00),                # packet mode, fsk
    (0x03, 0x34), (0x04, 0x15),  # bit rate 2.4kbps
    (0x05, 0x04), (0x06, 0xCD),  # 75kHzFdev -> modulation index = 2
    (0x19, 0x42), (0x1A, 0x42),  # RxBw 125khz, AFCBw 125khz
    (0x37, 0x00),                # drop pkt if CRC fails
    # 37D8 h                     # deliver even if CRC fails
]

CMDS = {
    # index 0 = off, 1 = on
    0b11100: [(0x18, 0x02, 0x1A, 0x5A), (0x18, 0x01, 0x19, 0x5A)],
    0b01100: [(0x98, 0x02, 0x9A, 0x5A), (0x98, 0x01, 0x99, 0x5A)],
    0b00010: [(0xE8, 0x02, 0xEA, 0x5A), (0xE8, 0x01, 0xE9, 0x5A)],
    0b00011: [(0xE0, 0x02, 0xE2, 0x5A), (0xE0, 0x01, 0xE1, 0x5A)],
}


class Boiler(object):

    def __init__(self, rf_freq, group=BOILER_GROUP, pkt_gap=BOILER_PKT_GAP,
                 driver=None, addr=0b00011):
        self.pkt_gap = pkt_gap
        self.driver = driver or rf.RFM69()
        self.driver.init(rf_freq, group=group, modem=MODEM,
                         config=BOILER_CONFIG)
        self.cmds = CMDS[addr]

    def close(self):
        self.driver.close()

    def send(self, cmd):
        # self.driver.flush_fifo()

        if not self.driver.can_send.is_set():
            logger.debug(f'boiler wait: {self.driver.can_send.is_set()}')

            # timeout after 2 minutes so that it doesn't block
            if not self.driver.can_send.wait(timeout=10):
                logger.warn('boiler send wait timeout, resetting ' +
                            f'{self.driver.can_send.is_set()}')
                self.driver.can_send.set()
                return False

        logger.debug(f"sending {' '.join(f'{d:02X}' for d in cmd)}")

        try:
            return self.driver.send(cmd)
        finally:
            if self.driver.error:
                logger.error(self.driver.error)

    def switch(self, cmd, count=BOILER_PKT_COUNT):
        for i in range(count):
            self.send(self.cmds[cmd])
            time.sleep(self.pkt_gap)


def show_packet(packet, rssi):
    print(f" RF69 ({rssi})", ' '.join([f"{b:02X}" for b in packet]))
    radio.driver.set_rx_mode()


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc, topic="sensor/heating/set"):
    print("Connected with result code "+str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe(topic)


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    print(msg.topic+" "+str(msg.payload))
    radio.switch(int(msg.payload))


def parse_args(argv=sys.argv):
    # TODO: add rf arg for with below args
    #       rf_boiler.py rf -c 1
    #       rf_boiler.py client
    parser = argparse.ArgumentParser(description='Connect with jeenode')
    parser.add_argument('-c', '--cmd', help='switch boiler on or off',
                        choices=[0, 1], type=int)
    parser.add_argument('-l', '--listen', action='store_true',
                        help='start listener mode')
    parser.add_argument('-r', '--reset', action='store_true',
                        help='reset the radio')
    parser.add_argument('-d', '--dump-regs', action='store_true',
                        help='dump radio registers')
    parser.add_argument('-n', '--client', action='store_true',
                        help="run as mqtt client")

    return parser.parse_args()


if __name__ == "__main__":
    print("running boiler")
    args = parse_args()
    # rf_freq = 86826
    rf_freq = 86630
    try:
        radio = Boiler(rf_freq)
        if args.listen:
            rf.listener(radio, show_packet)
        elif args.reset:
            radio.driver.reset()
        elif args.dump_regs:
            radio.driver.dump_regs()
        elif args.client:
            mqtt.mqtt_client(on_connect, on_message)
        else:
            radio.switch(args.cmd)
    finally:
        radio.driver.set_idle_mode()
        radio.driver.close()
