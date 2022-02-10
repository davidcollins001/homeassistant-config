# add_event_detect() runtime error
# https://github.com/gpiozero/gpiozero/issues/50

import time
import spidev
import RPi.GPIO as gpio
import threading as th
import logging
import queue

import rf_regs as regs

# TODO: remove packet handling from ISR - use flag

logger = logging.getLogger("rf")
# logger.setLevel(logging.INFO)
# logger.setLevel(logging.DEBUG)
logger.setLevel(logging.ERROR)

gpio.setmode(gpio.BCM)


RST_PIN_BCM = 24  # 18
IRQ_PIN_BCM = 25  # 22

ACK_FLAG = 0x1

MAXDATA = 66

RETRIES = 3
TIMEOUT = 40  # ms

RF_CONFIG = [
    (0x0B, 0x20),               # AfcCtrl, afclowbetaon
    (0x11, 0x5F),               # Usa PA1
    (0x29, 0xE4),               # RSSI threshold
    (0x2B, 0x00),               # no RSSI timeout
    (0x2E, 0x90),               # SyncConfig = sync on, sync size = 3
    (0x2F, 0xAA),               # SyncValue1 = $AA
    (0x30, 0x2D),               # SyncValue2 = $2D
    (0x38, MAXDATA),            # PacketLength
    (0x3C, 0x8F),               # FifoTresh, not empty, level 15
    (0x3D, 0x12),               # PacketConfig2, interpkt = 1, autorxrestart off
    (0x6F, 0x20),               # TestDagc ...
    # (0x6F, 0x30),               # TestDagc ...
]

GFSK_Rb250Fd250 = [
    (0x02, 0x01),                   # GFSK BT = 1.0
    (0x03, 0x00), (0x04, 0x80),     # bit rate  250kbs
    (0x05, 0x10), (0x06, 0x00),     # Fdev 250kHz
    (0x19, 0xE0), (0x1A, 0xe0),     # RxBw 125khz, AFCBw 125khz
    # (0x37, 0xD2),                   # variable, dc white, crc, node filt
    (0x37, 0xDA),                   # variable, dc white, crc, no crc fail, node filt
]


class Exit(object):
    """Poison pill for radio"""


class Packet(object):
    def __init__(self, data, rssi):
        self._data = data
        self.rssi = rssi
        if len(data) > 4:
            self.to, self.addr = data[0:2]
            self.flags = data[2] >> 4
            self.ack = bool(self.flags & ACK_FLAG)
            # TODO: update opposite flag after updating jz4
            self.ack = not self.ack
            self.seq = int(data[2]) & 0xF
            self.data = data[3:]


class RFM69(object):
    """
    Low level driver for hope RFM69 radio module
    """
    def __init__(self, bus=0, device=0, settings=0b00, speed=regs.SPI_SPEED,
                 irq_pin=IRQ_PIN_BCM):
        """
        Connect to device on SPI bus with cpol/cpha settings
        """
        spi = spidev.SpiDev()
        spi.open(bus, device)
        spi.mode = settings
        spi.max_speed_hz = speed
        self.radio = spi
        self.irq_pin = irq_pin

        self._idle_mode = regs.RF_M_STDBY
        self._rssi = 0
        self._lna = 0
        self._afc = 0
        self._fei = 0

        self._rx_good = 0
        self._tx_good = 0
        self._last_preamble_time = 0
        self._fixed_payload = None

        self.payload_ready = queue.SimpleQueue()
        self.can_send = th.Event()
        self.can_send.set()
        self._mode_set = th.Event()
        self._mode_set.set()

        self.payload = None
        self.error = None

    def close(self):
        self.radio.close()

    def init(self, freq, group=0xD4, nodeid=0, modem=None, config=RF_CONFIG):
        self.modem = modem
        self.config = config

        self.group = group
        self.nodeid = nodeid

        self.write_regs(config)
        self.set_modem_config(modem)

        gpio.setup(self.irq_pin, gpio.IN, pull_up_down=gpio.PUD_UP)
        # gpio.setup(self.irq_pin, gpio.IN)
        gpio.remove_event_detect(self.irq_pin)
        time.sleep(0.1)
        gpio.add_event_detect(self.irq_pin, gpio.RISING, self.irq_handler)

        self.set_freq(freq)

        # set group
        self.write_reg(regs.RF_SYN3, group)
        self.write_reg(regs.RF_ADDR, nodeid)

        # determine packet size
        config = self.read_reg(regs.RF_CONF)
        if not config & (1 << 7):
            self._fixed_payload = self.read_reg(regs.RF_PAYLOAD_LEN)

        self.set_idle_mode()

    def set_modem_config(self, config):
        # TODO: accept list instead of pairs of reg, val
        self.write_regs(config)

    def set_freq(self, freq):
        self.freq = freq

        # auto scale to MHz range
        rf_freq = freq
        while rf_freq < 100000000:
            rf_freq *= 10

        rf_freq = (rf_freq << 2) // (32000000 >> 11)

        rf_freq_msb = rf_freq >> 10
        rf_freq_mid = rf_freq >> 2
        rf_freq_lsb = rf_freq << 6

        # set frequency
        self.write_regs([(0x7, rf_freq_msb),
                         (0x8, rf_freq_mid),
                         (0x9, rf_freq_lsb)])

    def _set_mode(self, mode):
        """Set mode of radio"""
        self.mode = mode
        self.write_reg(regs.RF_OP, self.read_reg(regs.RF_OP) & 0xE3 | mode)
        # TODO: use interrupts
        # wait for mode to be ready
        # while not self.read_reg(regs.RF_IRQ1) & regs.RF_IRQ1_MRDY:
            # pass

    def set_rx_mode(self):
        self._set_mode(regs.RF_M_RX)

    def set_tx_mode(self):
        self._set_mode(regs.RF_M_TX)

    def set_idle_mode(self, mode=None):
        if mode:
            self._idle_mode = mode
        self._set_mode(self._idle_mode)

    def write_reg(self, reg, value):
        self.radio.xfer2([reg | 0x80, value])

    def read_reg(self, reg):
        return self.radio.xfer2([reg, 0])[1]

    def write_regs(self, data):
        """
        Set registers in radio. Odd elements and register addresses and even
        elements are the corresponding values

        eg data = [reg1, val1, reg2, val2]
        """
        # or each reg address with 0x80 to actually write to register
        for reg, value in data:
            self.write_reg(reg, value)

    def read_regs(self, regs):
        return [self.read_reg(regs) for reg in regs]

    def write_fifo(self, data):
        self.radio.xfer2([regs.RF_FIFO | 0x80] + list(data))

    def read_fifo(self):
        if self._fixed_payload:
            pkt_len = self._fixed_payload + 1
        else:
            pkt_len = self.read_reg(regs.RF_FIFO) + 1

        self.payload = Packet(self.radio.readbytes(pkt_len)[1:], self._rssi)

    def flush_fifo(self, ):
        self.radio.readbytes(MAXDATA)

    def irq_handler(self, pin):
        try:
            irq2 = self.read_reg(regs.RF_IRQ2)
            m = {regs.RF_M_TX: 'tx', regs.RF_M_RX: 'rx'}
        except Exception as e:
            m = {regs.RF_M_TX: 'tx', regs.RF_M_RX: 'rx'}
            self.error = f"{self.mode} {e}"

        # message has been fully sent
        if self.mode == regs.RF_M_TX:
            # get the interrupt cause
            if irq2 & regs.RF_IRQ2_SENT:
                self.set_idle_mode()
                self._tx_good += 1
                self.can_send.set()
            else:
                self.can_send.set()
                self.error = f"irq tx - not run self.can_send.set() {irq2:b}"
                logger.debug(self.error)

        # wait for PAYLOADREADY, not CRCOK - PAYLOADREADY occurs after AES decryption
        elif self.mode == regs.RF_M_RX:
            # get the interrupt cause
            if irq2 & regs.RF_IRQ2_RECVD:
                # complete message has been received with good CRC
                # self._rssi = - (self.read_reg(regs.RF_RSSI) >> 1)
                self._last_preamble_time = time.time()
                self._rx_good += 1
                self.rf_status()

                self.set_idle_mode()
                self.read_fifo()
                self.payload_ready.put(True)
                self.can_send.set()
            else:
                self.can_send.set()
                self.error = (f"irq rx - not run self.can_send.set() {irq2:b} "
                              f"{self.read_reg(regs.RF_IRQ2)}")
                logger.debug(self.error)
        else:
            self.can_send.set()
            self.error = "irq - not run self.can_send.set()"

        self.can_send.set()

    def rf_power(self, power):
        # TODO: use PA2 if higher power is needed
        # https://andrehessling.de/2015/02/07/figuring-out-the-power-level-settings-of-hoperfs-rfm69-hwhcw-modules/
        # RFM69(C)W only has PA0
        # RFM69H(C)W has PA1 and PA2
        # feather wing is RFM69HCW - PA1 and PA2 - don't use PA0
        # jeenode zero if RFM69CW (probably) - only PA0
        self.write_reg(regs.RF_PA, 0x40 | power)

    def rf_status(self):
        # fetch rssi, lna, and afc values
        self._rssi = - (self.read_reg(regs.RF_RSSI) >> 1)
        self._lna = (self.read_reg(regs.RF_LNA) >> 3) & 7
        self._afc = ((self.read_reg(regs.RF_AFC) << 8) |
                     self.read_reg(regs.RF_AFC + 1))
        self._fei = ((self.read_reg(regs.RF_FEI) << 8) |
                     self.read_reg(regs.RF_FEI + 1))

    def rf_correct(self):
        # : rf-correct ( -- ) \ correct the freq based on the AFC measurement of the last packet
        #   rf.afc @ 16 lshift 16 arshift 61 *         \ AFC correction applied in Hz
        #   2 arshift                                  \ apply 1/4 of measured offset as correction
        #   5000 over 0< if negate max else min then   \ don't apply more than 5khz
        #   rf.freq @ + dup rf.freq ! rf-freq!         \ apply correction
        correction = (self._afc * 61) >> 2
        # 1 or -1
        sgn = (2 * int(correction >= 0)) - 1
        self.correction = sgn * min(abs(correction), 5000)
        # self.set_freq(self.freq - correction)

    def reset(self, pin=RST_PIN_BCM, delay=100):
        """set `pin` high for to reset radio"""
        try:
            gpio.setup(pin, gpio.OUT)
            gpio.output(pin, 1)
            time.sleep(delay / 1000)
            gpio.output(pin, 0)
        finally:
            gpio.cleanup()

    def dump_regs(self):
        """Dump radio register"""
        # display header
        print('     ', end='')
        for i in range(16):
            print(f'{i:X}', end='  ')

        # print regs in rows 16 long
        for i in range(6):
            print(f'\n{i * 10:02}: ', end='')
            for j in range(16):
                if not i and not j:
                    print('--', end=' ')
                else:
                    print("{:02X}".format(self.read_reg((i * 16) + j)), end=' ')
        print()

    def recv(self):
        if self.mode == regs.RF_M_TX:
            return False

        self.set_rx_mode()

        # set int to trigger for PayloadReady on DIO0
        # self.write_reg(0x25, 0x01 << 6)
        self.write_reg(0x25, 0x40)

        if self.payload_ready.empty():
            return False

        # perform frequency correction
        # radio.rf_correct()
        # logger.info("freq correction: %s %s %s",
                    # radio._afc, radio._fei, radio.correction)

        # TODO: this is probably causing race condition with boiler on/off
        # pop received notification
        while self.payload_ready.qsize():
            logger.info("dropping notification: ")
            pl = self.payload_ready.get()
            logger.info(f"***: {pl}")
            return False
        return True

    def send(self, data):
        if self.mode == regs.RF_M_TX:
            return False

        logger.debug(f"ready {self.can_send.is_set()}")

        # self.error = None
        self.can_send.clear()
        self.set_idle_mode()

        # logger.debug("wait for tx ready")
        # self.write_reg(0x25, 0x1)
        # self.can_send.wait()

        # set int to trigger for PacketSent on DIO0
        self.write_reg(0x25, 0x0)

        # logger.debug(data)
        self.write_fifo(data)

        logger.debug("sending")
        self.set_tx_mode()
        return True


class DataGram(object):
    """
    Reliable communication protocal using ack's to confirm packet has been
    received
    """
    def __init__(self, driver=None, retries=RETRIES, timeout=TIMEOUT):
        self.retries = retries
        self._timeout = timeout
        self.driver = driver or RFM69()
        self.seq = 0

    def close(self):
        self.driver.close()

    def init(self, rf_freq, nodeid=0xB, group=0xB6, modem=GFSK_Rb250Fd250):
        self.driver.init(rf_freq, nodeid=nodeid, group=group, modem=modem)

    @property
    def payload(self):
        return self.driver.payload

    def build_payload(self, addr, data=None, flags=None, seq=None):
        if seq is None:
            seq = self.seq = (self.seq + 1) % 0xFF
        flags = flags or 0
        _data = [addr, self.driver.nodeid, (flags < 4) | seq]
        _data += (data or [])
        return [len(_data)] + _data

    def ack(self, addr):
        data = self.build_payload(addr, seq=self.payload.seq)
        logger.debug("sending ack: ", data)
        self.driver.send(data)

    def wait_sent(self, timeout=0):
        self.driver.can_send.wait(timeout=timeout)

    def recv(self, timeout=None):
        logger.debug("wait can_send")
        if not self.driver.can_send.wait(timeout=timeout):
            logger.debug("not ready to send")
            return False

        logger.debug("wait driver.recv")
        if not self.driver.recv():
            msg = self.driver.payload_ready.get(timeout=timeout)

            if msg is Exit:
                return False

        # received message ack it
        try:
            if self.payload.ack:
                self.ack(self.payload.addr)
        except Exception as e:
            logger.error(e)
        return True

    def recv_ack(self, addr):
        if not self.driver.recv():
            self.driver.payload_ready.get(self._timeout / 1000)

        if self.driver.payload_ready.qsize():
            return addr == self.payload.addr and self.seq == self.payload.seq

    def send_to(self, addr, data, flags=None):
        _data = self.build_payload(addr, data, flags)

        for attempt in range(self.retries):
            self.driver.can_send.wait()
            while not self.driver.send(_data):
                pass
            logger.debug(attempt, ":", _data)
            self.driver.can_send.wait()

            if self.recv_ack(addr):
                return True


class Varint(object):
    # encoding is as follows:
    # - int is rotated 1 bit left placing sign bit in lowest bit position
    # - if bit 0 (now sign bit) is 1 (negative) all other bits are inverted
    # - group bits in integer in 7-bit groups, emit byte for each 7-bit group
    #   starting with highest non-zero group
    # - when emitting last byte for lowest bits, set top bit of byte
    # - zero encodes to 0x80
    @staticmethod
    def encode_varint(data):
        pass

    @staticmethod
    def decode_varint_orig(data):
        result = []
        value = 0
        for d in data:
            # remove last byte/sign indicator bits
            value |= ((d & 0x7F) >> 1)
            if d & 0x80:
                # negative numbers
                if d & 0x1:
                    value = ~value
                result.append(value)
                value = 0
            else:
                value <<= 7
        return result

    @staticmethod
    def decode_varint(data):
        #   0
        #   var.ptr @ var.end @ u2 if
        #     begin
        #       $80  var.ptr *++  tuck + swap
        #     $80 and until
        #     $80 -
        #     dup 1 and 0<> shl xor ror \ handle sign
        #     1
        #   then ;

        def ror(n, rotations, width):
            return (2**width-1) & (n >> rotations | n << (width - rotations))

        result = []
        parts = []
        for d in data:
            parts.append(d)
            if d & 0x80:
                # remove last byte marker
                parts[-1] = parts[-1] - 0x80
                # shift byte to correct position
                rs = 0
                for i, v in enumerate(reversed(parts)):
                    rs |= v << (i * 7)
                # decode number
                val = ror((rs ^ -(int(rs & 1 != 0) * 2)), 1, 32)
                if val > 2 ** 31:
                    val -= 2 ** 32

                result.append(val)
                parts = []

        return result


def show_fixed_packet(packet, rssi=0):
    print(f" RF69 ({hex(rssi)}): ",
          ' '.join([f"{b:X}" for b in packet]))


def show_packet(packet, rssi=0):
    # 0 - receiver node id
    # 1 - receiver node id
    # 2: - data
    logger.info(
        f" RF69 ({hex(rssi)}) [{packet.addr} -> {packet._data[0]}] " +
        f"{len(packet.data)}: " +
        ' '.join([f"{b:X}" for b in packet.data])
    )


def reconstruct_packet(payload, debug=False):
    # show packet
    logger.debug(
        f" RF69 ({hex(payload.rssi)}) " +
        f"[{payload.addr} -> {payload.to}] " +
        (f"{payload.data} > " if debug else "") +
        f"{Varint.decode_varint(payload.data)}"
    )


def listener(radio, handle_packet=show_packet):
    radio.driver.recv()
    radio.driver.dump_regs()

    while True:
        if radio.recv():
            handle_packet(radio.payload, radio.driver._rssi)
            # reconstruct_packet(radio.payload)


def sender(radio, data=None, addr=0x1E, count=1):
    print(radio.driver.freq, radio.driver.group, radio.driver.nodeid)
    # radio.dump_regs()

    while count != 0:
        if not data:
            data = [int(i) for i in str(int(time.time()))]
        # print(f'{abs(count)}: ', end='')

        # if not radio.send_to(addr, data):
        data = [7, addr, radio.driver.nodeid, 1, 2, 3, 4]
        if not radio.driver.send(data):
            logger.warning("missed ack. {} success".format(abs(count)))
            count = -1
        # time.sleep(10)
        # time.sleep(1)
        time.sleep(3)
        # time.sleep(0.1)

        count -= 1
