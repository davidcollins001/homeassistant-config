import logging
import threading as th
from functools import partial

import paho.mqtt.client as mqtt
import telebot

# TODO: this should be imported automatically from __init__
import setup_log  # noqa
from secrets import BOT_TOKEN

logger = logging.getLogger("monitor")
logger.setLevel(logging.INFO)


MINS = 60
HOURS = 60 * MINS
MONITOR_QUEUES = {
    "state/sensor/794296394": 60 * MINS,  # 4x longest update time
    "state/sensor/1985242708": 24 * HOURS,
}
CHAT_ID = "1620310931"


def alert(queue, timeout, bot_token=BOT_TOKEN, chat_id=CHAT_ID):
    message = (f"ALERT: no messages for {timeout} mins for \"{queue}\"")
    logger.info(message)
    set_timer(queue, timeout)
    bot = telebot.TeleBot(bot_token)
    bot.config['api_key'] = bot_token
    res = bot.send_message(chat_id, message)
    if not res['ok']:
        logger.error(res['error'])


def setup_set_timer():
    queues = {}

    def set_timer(queue, timeout):
        w = queues.get(queue)
        if w:
            w.cancel()
        queues[queue] = th.Timer(timeout, partial(alert, queue, timeout))
        queues[queue].start()
    return set_timer


set_timer = setup_set_timer()


def subscribe(c, d, f, rc, queues):
    for q, t in queues.items():
        set_timer(q, t)
        c.subscribe(q)


def on_message(c, d, m, queues):
    logger.debug(f"received on {m.topic}")
    set_timer(m.topic, queues[m.topic])


def connect(queues):
    client = mqtt.Client()

    client.on_connect = lambda c, d, f, rc: subscribe(c, d, f, rc, queues)
    client.on_message = lambda c, d, m: on_message(c, d, m, queues)

    client.connect("homeassistant", 1883, 60)
    logger.info(f"connected to {queues}")

    # client.loop_start()
    client.loop_forever()


def main():
    # args = parse_args()
    connect(MONITOR_QUEUES)


if __name__ == "__main__":
    main()
