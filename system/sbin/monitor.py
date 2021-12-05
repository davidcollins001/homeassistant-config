import logging
import threading as th
from functools import partial

import paho.mqtt.client as mqtt
import telebot

# TODO: this should be imported automatically from __init__
import setup_log  # noqa

logger = logging.getLogger("monitor")
logger.setLevel(logging.INFO)


MINS = 60
MONITOR_QUEUES = [
    "state/sensor/794296394"
]
# Viper bot
# BOT_TOKEN = ""
# Alert bot
BOT_TOKEN = ""
CHAT_ID = "1620310931"
# 4x longest update time
TIMEOUT = 4 * 15 * MINS

WAITERS = {}


def alert(queue=None, bot_token=BOT_TOKEN, chat_id=CHAT_ID):
    message = f"ALERT: no messages for {TIMEOUT//60} mins for \"{queue}\""
    logger.info(f"{message}")
    set_timer(queue)
    bot = telebot.TeleBot(bot_token)
    bot.config['api_key'] =bot_token
    res = bot.send_message(chat_id, message)
    if not res['ok']:
        logger.error(res['error'])


def set_timer(queue):
    WAITERS[queue] = w = th.Timer(TIMEOUT, partial(alert, queue=queue))
    w.start()


def subscribe(client, queue):
    set_timer(queue)
    return client.subscribe(queue)


def on_message(c, d, m):
    logger.debug(f"received on {m.topic}")
    wait = WAITERS.get(m.topic)
    if wait:
        wait.cancel()

    set_timer(m.topic)


def connect(queues):
    client = mqtt.Client()

    client.on_connect = lambda c, d, f, rc: {q: subscribe(client, q)
                                             for q in queues}
    client.on_message = lambda c, d, m: on_message(c, d, m)

    client.connect("homeassistant", 1883, 60)
    logger.info(f"connected to {queues}")

    # client.loop_start()
    client.loop_forever()


def main():
    # args = parse_args()
    connect(MONITOR_QUEUES)


if __name__ == "__main__":
    main()
