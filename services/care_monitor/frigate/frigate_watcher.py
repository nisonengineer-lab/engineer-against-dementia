import json
import logging
import queue

import paho.mqtt.client as mqtt

log = logging.getLogger("mqtt")


class FrigateListener:
    def __init__(self, host, port, user, password, events=None):
        self.host = host
        self.port = port
        self.events = events if events is not None else queue.Queue()
        self.stopping = False

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.username_pw_set(user, password)
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def start(self):
        log.info("подключаюсь к %s:%s", self.host, self.port)
        self.client.connect_async(self.host, self.port, 60)
        self.client.loop_start()

    def stop(self):
        self.stopping = True
        self.client.disconnect()
        self.client.loop_stop()
        log.info("слушатель остановлен")

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        log.info("подключено к брокеру: %s", reason_code)
        client.subscribe("frigate/events")
        client.subscribe("frigate/tracked_object_update")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        if self.stopping:
            return
        log.warning("связь с брокером потеряна (%s), переподключаюсь", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except ValueError:
            log.warning("не JSON в %s: %r", msg.topic, msg.payload[:200])
            return
        self.events.put((msg.topic, data))

