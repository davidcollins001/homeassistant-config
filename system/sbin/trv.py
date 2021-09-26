#!/usr/bin/python

import sys
import time
import argparse

import rf_regs as regs
import rf


DEBUG = False

TRV_PKT_GAP = 30e-3
TRV_PKT_COUNT = 30
TRV_GROUP = 0xD4

TRV_CONFIG = [
    (0x0B, 0x20),             # low M
    (0x11, 0x5F),             # use PA1
    (0x29, 0xC4),             # RSSI thres -98dB
    (0x2B, 0x40),             # no RSSI timeout
    # (0x2D, 0x07),             # Preamble 6 bytes
    (0x2D, 0x01),             # Preamble 6 bytes
    (0x2E, 0x90),             # sync size 3 bytes
    (0x2F, 0xAA),             # sync1
    (0x30, 0x8D),             # sync2
    (0x31, 0xD1),             # sync3
    (0x38, 0x14),             # max 4 byte payload
    (0x39, 0x00),             # node filtering
    (0x3D, 0x12),             # PacketConfig2, interpkt = 1, autorxrestart on
    (0x6F, 0x20),             # Test DAGC
    # (0x71, 0x02),             # RegTestAfc
]

MODEM = [
    (0x02, 0x00),                # packet mode, fsk
    (0x03, 0x01), (0x04, 0x40),  # bit rate 100kbps
    (0x05, 0x02), (0x06, 0x67),  # 37.5kHzFdev -> modulation index = 2
    (0x19, 0x00), (0x1A, 0x00),  # RxBw 125khz, AFCBw 125khz
    (0x37, 0x00),                # drop pkt if CRC fails
    # 37D8 h                     # deliver even if CRC fails
]

PACKETS = [
    #  71632AA6    02654B80    6A817200    40013040  000000000000000000013

    [1, 1, 0xFF, 0xFF, ],
    [0x71632AA6, 0x02440B00, 0x40000C80, 0x00010080, 0x00000000],
    [0x5F5F5F5F, 0x5F5F5F5F, 0x5F5F5F5F, 0x5F5F5F5F, 0x5F5F5F5F],

    # [0x71632AA6, ],  # 0x02440B00, 0x40000C80, 0x00010080, 0x00000000],
    # [0x71632AA6, ],  # 0x02654B80, 0x6A822EC0, 0x40058040, 0x00000000],
    # [0x71632AA6, ],  # 0x02654B80, 0x6A822EC0, 0x40058040, 0x00000000],
    # [0x70630006, ],  # 0x00200180, 0x20000640, 0x00028000, 0x00000000],
    # [0x71632AA6, ],  # 0x02654B80, 0x6A822EC0, 0x40048000, 0x00000000],
    # [0x71632AA6, ],  # 0x02654B80, 0x6A822640, 0x00008000, 0x00000000],
    # [0x716328A2, ],  # 0x00200180, 0x20000640, 0x00028020, 0x00000000],
    # [0x71632AA6, ],  # 0x02654B80, 0x20000640, 0x00028020, 0x00000000],
    # [0x71632AA6, ],  # 0x02654B80, 0x6A822EC0, 0x40058040, 0x00000000],
    # [0x71632AA6, ],  # 0x02654B80, 0x6A822EC0, 0x40058040, 0x00000000],
]


class TRV(object):

    def __init__(self, rf_freq, group=TRV_GROUP, pkt_gap=TRV_PKT_GAP,
                 driver=None):
        self.pkt_gap = pkt_gap
        self.driver = driver or rf.RFM69()
        self.driver.init(rf_freq, group=group, modem=MODEM,
                         config=TRV_CONFIG)

    def send(self, cmd):
        self.driver.flush_fifo()

        # timeout after 2 minutes so that it doesn't block
        if not self.driver.can_send.wait(timeout=120):
            print('boiler send wait timeout, resetting')
            self.driver.can_send.set()
            return False

        if DEBUG:
            print("sending ", ' '.join(f'{d:02X}' for d in cmd))

        return self.driver.send(cmd)

    def switch(self):
        for pkt in PACKETS:
            self.send(pkt)
            # time.sleep(self.pkt_gap)

            print(self.driver.mode, self.driver.can_send.is_set())
            while not self.driver.can_send.is_set():
                pass

            self.driver.set_rx_mode()

            time.sleep(3)
            print(self.driver.mode, self.driver.can_send.is_set())
            # self.driver.


def show_packet(packet, rssi):
    print(f" RF69 ({rssi})", ' '.join([f"{b:02X}" for b in packet]))
    radio.driver.set_rx_mode()


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc, topic="sensor/heating/set"):
    print("Connected with result code "+str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe(topic)


def parse_args(argv=sys.argv):
    # TODO: add rf arg for with below args
    #       rf_boiler.py rf -c 1
    #       rf_boiler.py client
    parser = argparse.ArgumentParser(description='Connect with jeenode')
    parser.add_argument('-c', '--cmd', help='switch boiler on or off', choices=[0, 1],
                        type=int)
    parser.add_argument('-l', '--listen', action='store_true',
                        help='start listener mode')
    parser.add_argument('-r', '--reset', action='store_true', help='reset the radio')
    parser.add_argument('-d', '--dump-regs', action='store_true',
                        help='dump radio registers')
    parser.add_argument('-n', '--client', action='store_true',
                        help="run as mqtt client")

    return parser.parse_args()


if __name__ == "__main__":
    print("running trv")
    args = parse_args()
    rf_freq = 8663
    radio = TRV(rf_freq)
    while True:
        radio.switch()
        time.sleep(10)
