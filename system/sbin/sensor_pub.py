import json


def sensor_794296394(client, raw_data):
    sensor_queue = "state/sensor/794296394"
    sensor_keys = ['hwid', 'iaq2', 'iaq', 'adc_temp', 'temperature',
                   'pressure', 'humidity', 'gas_res']
    # publish temp
    data = dict(zip(sensor_keys, raw_data))
    data['temperature'] /= 100
    client.publish(sensor_queue, json.dumps(data))
    # client.publish(sensor_queue, data['temperature'] / 100)


def NO_sensor_1985242708(client, raw_data):
    sensor_queue = "state/sensor/1985242708"
    sensor_keys = ['hwid', 'temperature', 'humidity']
    # publish temp
    data = dict(zip(sensor_keys, raw_data))
    data['temperature'] /= 100
    client.publish(sensor_queue, json.dumps(data))
    # client.publish(sensor_queue, data['temperature'] / 100)


def setup_1985242708():
    update_count = {"count": 0}

    def sensor_1985242708(client, raw_data):
        sensor_queue = "state/sensor/1985242708"
        sensor_keys = ['hwid', 'door_event']
        # publish temp
        # data = dict(zip(sensor_keys, raw_data))
        update_count["count"] += 1
        data = dict(zip(sensor_keys, [raw_data[0], update_count["count"]]))
        client.publish(sensor_queue, json.dumps(data))

    return sensor_1985242708


sensor_1985242708 = setup_1985242708()
