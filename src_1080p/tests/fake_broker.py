class Sent:
    def wait_for_publish(self, timeout=None):
        return True


class Fine:
    is_failure = False

    def __str__(self):
        return "Success"


class Broker:
    def __init__(self):
        self.retained, self.clients, self.log = {}, [], []

    @staticmethod
    def matches(pattern, topic):
        want, got = pattern.split("/"), topic.split("/")
        for at, part in enumerate(want):
            if part == "#":
                return True
            if at >= len(got) or (part != "+" and part != got[at]):
                return False
        return len(want) == len(got)

    def publish(self, topic, payload, retain, sender):
        self.log.append((sender.client_id, topic, payload))
        if retain:
            self.retained[topic] = payload
        for client in list(self.clients):
            if client.connected and any(self.matches(p, topic) for p in client.subs):
                client.deliver(topic, payload)

    def join(self, client):
        client.connected = True
        client.subs = []
        self.clients.append(client)
        client.on_connect(client, None, None, Fine(), None)
        for topic, payload, retain in client.queued:
            self.publish(topic, payload, retain, client)
        client.queued.clear()

    def drop(self, client, will=True):
        if client in self.clients:
            self.clients.remove(client)
        client.connected = False
        if will and client.will:
            self.publish(client.will[0], client.will[1], client.will[2], client)
        client.on_disconnect(client, None, None, "connection lost", None)


class Client:
    def __init__(self, broker, client_id):
        self.broker, self.client_id = broker, client_id
        self.subs, self.queued, self.will, self.connected = [], [], None, False
        self.auth = self.target = None
        self.tls = False
        self.on_connect = self.on_message = self.on_disconnect = None

    def username_pw_set(self, username, password):
        self.auth = (username, password)

    def tls_set(self):
        self.tls = True

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload.encode(), retain)

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        pass

    def connect_async(self, host, port, keepalive):
        self.target = (host, port, keepalive)

    def loop_start(self):
        self.broker.join(self)

    def loop_stop(self):
        pass

    def subscribe(self, wanted):
        for topic, _qos in wanted:
            self.subs.append(topic)
            for held, payload in sorted(self.broker.retained.items()):
                if self.broker.matches(topic, held):
                    self.deliver(held, payload)

    def publish(self, topic, payload, qos=0, retain=False):
        data = payload.encode()
        if self.connected:
            self.broker.publish(topic, data, retain, self)
        else:
            self.queued.append((topic, data, retain))
        return Sent()

    def disconnect(self):
        self.broker.drop(self, will=False)

    def deliver(self, topic, payload):
        self.on_message(self, None, type("Message", (), {"topic": topic, "payload": payload})())
