import datetime
import hashlib
import hmac
import importlib
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque

if __name__ == "__main__":
    for at, arg in enumerate(sys.argv):
        if arg == "--config" and at + 1 < len(sys.argv):
            os.environ["CABAL_CONFIG"] = sys.argv[at + 1]
        elif arg.startswith("--config="):
            os.environ["CABAL_CONFIG"] = arg.split("=", 1)[1]

import calibration


def settings():
    return calibration.load_shared().get("messaging") or {}


def login(conf=None):
    conf = settings() if conf is None else conf
    if not all(conf.get(key) for key in ("host", "port", "secret",
                                         "topic_prefix")):
        return None
    return {"host": conf["host"], "port": int(conf["port"]),
            "secret": conf["secret"]}


def _paho():
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return None
    return mqtt


def ensure_library(say, conf=None):
    if _paho() is not None:
        return True
    conf = settings() if conf is None else conf
    wanted = conf["library"]
    say(f"messaging: {wanted} is not installed for this Python; "
        f"installing it")
    began = time.perf_counter()
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pip", "install",
             "--disable-pip-version-check", "--quiet", wanted],
            capture_output=True, text=True,
            timeout=float(conf["install_timeout"]))
    except (OSError, subprocess.SubprocessError) as exc:
        say(f"messaging: the install did not run: "
            f"{type(exc).__name__}: {exc}")
        return False
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()
        say(f"messaging: the install failed: "
            f"{tail[-1] if tail else 'pip exit ' + str(done.returncode)}")
        return False
    importlib.invalidate_caches()
    ok = _paho() is not None
    say(f"messaging: {wanted} installed in "
        f"{time.perf_counter() - began:.0f}s" if ok else
        f"messaging: {wanted} installed but does not import until the "
        f"next start")
    return ok


def paho_client():
    mqtt = _paho()
    if mqtt is None:
        return None

    def make(client_id):
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id, protocol=mqtt.MQTTv311)
        client.suppress_exceptions = True
        return client
    return make


def why_off(conf=None, say=print):
    conf = settings() if conf is None else conf
    if not conf:
        return "config.json has no messaging section"
    if not ensure_library(say, conf):
        return f"{conf['library']} is not installed for this Python"
    if login(conf) is None:
        return "the messaging section lacks host, port, secret or topic_prefix"
    return None


def stamp():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def sign(key, topic, data):
    body = _canonical(data)
    mac = hmac.new(key, f"{topic}\n{body}".encode("utf-8"),
                   hashlib.sha256).hexdigest()
    return json.dumps({"m": data, "s": mac})


def opened(key, topic, payload):
    try:
        found = json.loads(payload.decode("utf-8")) if payload else None
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(found, dict) or not isinstance(found.get("m"), dict):
        return None
    mac = hmac.new(key, f"{topic}\n{_canonical(found['m'])}".encode("utf-8"),
                   hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, str(found.get("s") or "")):
        return None
    return found["m"]


class Link:
    def __init__(self, client_id, will, subscriptions, on_message, on_ready,
                 note, make, account, conf):
        self.subscriptions = [(topic, 1) for topic in subscriptions]
        self.on_message, self.on_ready, self.note = on_message, on_ready, note
        self.key = bytes.fromhex(account["secret"])
        self.refused = set()
        self.up = False
        self.client = make(client_id)
        self.client.tls_set()
        if will:
            self.client.will_set(will[0], sign(self.key, will[0], will[1]),
                                 qos=1, retain=True)
        self.client.reconnect_delay_set(min_delay=1,
                                        max_delay=int(conf["reconnect_max"]))
        self.client.on_connect = self._connected
        self.client.on_disconnect = self._lost
        self.client.on_message = self._message
        self.target = (account["host"], int(account["port"]),
                       int(conf["keepalive"]))

    def start(self):
        self.client.connect_async(*self.target)
        self.client.loop_start()
        return self

    def _connected(self, client, userdata, flags, reason_code, properties):
        if getattr(reason_code, "is_failure", False):
            self.up = False
            self.note(f"messaging refused the connection: {reason_code}")
            return
        self.up = True
        try:
            self.client.subscribe(self.subscriptions)
            self.on_ready()
        except Exception as exc:
            self.note(f"messaging start failed: {type(exc).__name__}: {exc}")

    def _lost(self, client, userdata, flags, reason_code, properties):
        if self.up:
            self.note(f"messaging connection lost ({reason_code}); "
                      f"reconnecting")
        self.up = False

    def _message(self, client, userdata, message):
        data = opened(self.key, message.topic, message.payload)
        if data is None:
            if message.payload and message.topic not in self.refused:
                self.refused.add(message.topic)
                self.note(f"messaging ignored a message on {message.topic} "
                          f"that does not carry this group's signature")
            return
        try:
            self.on_message(message.topic, data)
        except Exception as exc:
            self.note(f"messaging could not handle {message.topic}: "
                      f"{type(exc).__name__}: {exc}")

    def publish(self, topic, data, retain=False):
        try:
            return self.client.publish(topic, sign(self.key, topic, data),
                                       qos=1, retain=retain)
        except Exception as exc:
            self.note(f"messaging could not send {topic}: "
                      f"{type(exc).__name__}: {exc}")
            return None

    def close(self, last=None, wait=0.0):
        sent = self.publish(*last, retain=True) if last and self.up else None
        try:
            if sent is not None and wait > 0:
                sent.wait_for_publish(wait)
        except Exception:
            pass
        self.up = False
        try:
            self.client.disconnect()
        finally:
            self.client.loop_stop()


def topics(conf, name=None):
    prefix = conf["topic_prefix"]
    out = {key: f"{prefix}/{key}" for key in ("grant", "sold", "ping",
                                               "pong", "orchestrator")}
    out["presence"] = f"{prefix}/presence/{name}" if name else None
    out["everyone"] = f"{prefix}/presence/+"
    return out


class Participant:
    def __init__(self, name, special, make=None, account=None, conf=None,
                 clock=time.monotonic):
        conf = settings() if conf is None else conf
        self.name, self.special, self.clock = name, bool(special), clock
        self.session = f"{os.getpid()}-{time.time_ns()}"
        self.lease = float(conf["poll_seconds"]) * float(conf["lease_polls"])
        self.t = topics(conf, name)
        self.lock = threading.Lock()
        self.boss, self.boss_session = False, None
        self.holder, self.epoch, self.turn = None, 0, 0
        self.leased = None
        self.spent = 0
        self.fresh = False
        self.notes = deque()
        self.link = Link(
            f"{conf['topic_prefix']}-{name}-{self.session}",
            (self.t["presence"], {"online": False, "session": self.session}),
            (self.t["orchestrator"], self.t["grant"], self.t["ping"],
             self.t["sold"]),
            self._message, self._ready, self.note,
            make or paho_client(), account or login(conf), conf)
        self.link.start()

    def note(self, line):
        self.notes.append(line)

    def drain(self, say):
        while self.notes:
            say(f"  messaging: {self.notes.popleft()}")

    def _ready(self):
        self.note(f"connected as {self.name}")
        self.link.publish(self.t["presence"], {
            "online": True, "session": self.session,
            "special": self.special, "at": stamp()}, retain=True)

    def _message(self, topic, data):
        if topic == self.t["grant"]:
            self._grant(data)
        elif topic == self.t["ping"]:
            with self.lock:
                self.leased = self.clock()
            with self.lock:
                spent = self.spent
            self.link.publish(self.t["pong"], {
                "name": self.name, "n": data.get("n"),
                "session": self.session, "special": self.special,
                "spent": spent})
        elif topic == self.t["orchestrator"]:
            online = bool(data.get("online"))
            session = data.get("session")
            with self.lock:
                if (not online and session
                        and self.boss_session not in (None, session)):
                    return
                if online:
                    self.boss_session = session
                changed, self.boss = self.boss != online, online
            if changed:
                self.note(f"the orchestrator is {'online' if online else 'offline'}")
        elif topic == self.t["sold"] and data.get("from") != self.name:
            self.note(f"{data.get('from')}'s {data.get('item')} special row "
                      f"sold out (turn {data.get('turn')})")

    def _grant(self, data):
        epoch = int(data.get("epoch") or 0)
        holder = data.get("holder")
        with self.lock:
            if epoch < self.epoch:
                return
            news = epoch > self.epoch
            self.holder, self.epoch = holder, epoch
            self.turn = int(data.get("turn") or 0)
            self.leased = self.clock()
            self.fresh = holder == self.name and epoch > self.spent
        if news:
            self.note(f"special row turn {self.turn}: "
                      f"{holder or 'nobody'} holds it"
                      + (f" ({data['why']})" if data.get("why") else ""))

    def _holds(self):
        fresh = (self.leased is not None
                 and self.clock() - self.leased <= self.lease)
        ok = (self.link.up and self.boss and self.holder == self.name
              and self.epoch > self.spent and fresh)
        return self.epoch if ok else 0

    def holds(self):
        with self.lock:
            return self._holds()

    def named(self):
        with self.lock:
            return self.holder == self.name and self.epoch > self.spent

    def take_fresh(self):
        with self.lock:
            if self.fresh and self._holds():
                self.fresh = False
                return True
            return False

    def sold(self, item):
        with self.lock:
            mine = self.holder == self.name and self.epoch > self.spent
            epoch, turn = (self.epoch if mine else 0), self.turn
            if epoch:
                self.spent = epoch
        if epoch:
            self.link.publish(self.t["sold"], {
                "from": self.name, "epoch": epoch, "turn": turn,
                "item": item, "at": stamp()})
        return turn if epoch else 0

    def close(self, wait=0.0):
        self.link.close((self.t["presence"], {
            "online": False, "session": self.session}), wait)


class Orchestrator:
    def __init__(self, say, make=None, account=None, conf=None, clock=None,
                 start=True):
        conf = settings() if conf is None else conf
        self.say = say
        self.order = [str(name) for name in conf["order"]]
        self.every = float(conf["poll_seconds"])
        self.wait = float(conf["pong_wait"])
        self.t = topics(conf)
        self.session = f"{os.getpid()}-{time.time_ns()}"
        self.clock = clock or time.time
        self.lock = threading.Lock()
        self.sessions, self.special, self.ponged = {}, {}, {}
        self.joined = {}
        self.online = set()
        self.n = 0
        self.holder, self.epoch, self.turn, self.last = None, 0, 0, None
        self.ready = False
        self.owed = None
        self.stopping = threading.Event()
        self.link = Link(
            f"{conf['topic_prefix']}-orchestrator",
            (self.t["orchestrator"], {"online": False,
                                      "session": self.session}),
            (self.t["everyone"], self.t["pong"], self.t["sold"],
             self.t["grant"]),
            self._message, self._ready, self.note,
            make or paho_client(), account or login(conf), conf)
        self.link.start()
        self.thread = None
        if start:
            self.thread = threading.Thread(target=self.run, daemon=True,
                                           name="orchestrator")
            self.thread.start()

    def note(self, line):
        self.say(line)

    def _ready(self):
        self.link.publish(self.t["orchestrator"], {
            "online": True, "session": self.session, "at": stamp()},
            retain=True)

    def _message(self, topic, data):
        if topic == self.t["pong"]:
            self._pong(data)
        elif topic == self.t["sold"]:
            self._sold(data)
        elif topic == self.t["grant"]:
            self._grant_seen(data)
        elif topic.startswith(self.t["everyone"][:-1]):
            self._presence(topic.rsplit("/", 1)[-1], data)

    def _grant_seen(self, data):
        epoch = int(data.get("epoch") or 0)
        with self.lock:
            if epoch > self.epoch:
                self.epoch, self.holder = epoch, data.get("holder")
                self.turn = int(data.get("turn") or 0)

    def _presence(self, name, data):
        if name not in self.order:
            return
        session = data.get("session")
        with self.lock:
            if data.get("online"):
                self.sessions[name] = session
                self.special[name] = bool(data.get("special"))
                was = name in self.online
                if self.special[name]:
                    if not was:
                        self.joined[name] = self.n
                    self.online.add(name)
                else:
                    self.online.discard(name)
                now = name in self.online
            else:
                if session and self.sessions.get(name) not in (None, session):
                    return
                was, now = name in self.online, False
                self.online.discard(name)
        if was != now:
            self.say(f"{name} is {'online' if now else 'offline'}")
            self.settle(f"{name} came online" if now
                        else f"{name} went offline")

    def _pong(self, data):
        name = data.get("name")
        if name not in self.order:
            return
        with self.lock:
            if data.get("n") == self.n:
                self.ponged[name] = self.n
            used = (self.ready and name == self.holder
                    and int(data.get("spent") or 0) == self.epoch > 0)
            back = (name not in self.online and data.get("special")
                    and self.sessions.get(name) in (None,
                                                    data.get("session")))
            if back:
                self.sessions[name] = data.get("session")
                self.special[name] = True
                self.joined[name] = self.n
                self.online.add(name)
        if back:
            self.say(f"{name} answered the poll; online again")
            self.settle(f"{name} came back")
        if used:
            self.settle(f"{name}'s special row sold out, as its poll answer "
                        f"shows", turn=True)

    def _sold(self, data):
        with self.lock:
            fits = (data.get("from") == self.holder and self.holder
                    and int(data.get("epoch") or 0) == self.epoch)
            if fits and not self.ready:
                self.owed = f"{data['from']}'s special row sold out"
        if fits:
            self.settle(f"{data['from']}'s special row sold out", turn=True)
        else:
            self.say(f"a sold-out from {data.get('from')} for an earlier turn "
                     f"was ignored")

    def _next(self):
        after = self.holder or self.last
        ring = self.order
        if after in self.order:
            at = self.order.index(after)
            ring = self.order[at + 1:] + self.order[:at + 1]
        return next((name for name in ring if name in self.online), None)

    def settle(self, why, turn=False):
        with self.lock:
            if not self.ready:
                return None
            if not turn and self.holder in self.online:
                return None
            pick = self._next()
            if pick == self.holder and not turn:
                return None
            self.epoch = max(self.epoch + 1, int(self.clock() * 1000))
            self.turn += 1
            self.last = self.holder or self.last
            self.holder = pick
            grant = {"holder": pick, "epoch": self.epoch, "turn": self.turn,
                     "why": why, "at": stamp()}
        self.link.publish(self.t["grant"], grant, retain=True)
        self.say(f"turn {grant['turn']}: {pick or 'nobody'} holds the right "
                 f"({why})")
        return pick

    def poll(self):
        with self.lock:
            self.n += 1
            n = self.n
        self.link.publish(self.t["ping"], {"n": n, "at": stamp()})
        return n

    def close_poll(self):
        with self.lock:
            missing = sorted(name for name in self.online
                             if self.joined.get(name, 0) < self.n
                             and self.ponged.get(name) != self.n)
            for name in missing:
                self.online.discard(name)
        for name in missing:
            self.say(f"{name} did not answer the poll; counted offline")
        if missing:
            self.settle(f"{', '.join(missing)} did not answer the poll")
        return missing

    def begin(self):
        with self.lock:
            self.ready = True
            holder, owed, self.owed = self.holder, self.owed, None
        if owed:
            return self.settle(owed, turn=True)
        if holder is not None and holder in self.online:
            self.say(f"turn {self.turn}: {holder} keeps the right it held")
            return holder
        return self.settle("the orchestrator started")

    def run(self):
        while not self.link.up:
            if self.stopping.wait(self.wait):
                return
        if self.stopping.wait(self.wait):
            return
        self.begin()
        while not self.stopping.is_set():
            self.poll()
            if self.stopping.wait(self.wait):
                return
            self.close_poll()
            if self.stopping.wait(max(0.0, self.every - self.wait)):
                return

    def close(self, wait=0.0):
        self.stopping.set()
        self.link.close((self.t["orchestrator"], {
            "online": False, "session": self.session}), wait)


def start_participant(name, special, say=print):
    conf = settings()
    off = why_off(conf, say)
    if off:
        say(f"  messaging off: {off}")
        return None
    return Participant(name, special, conf=conf)


def start_orchestrator(say):
    conf = settings()
    if conf.get("orchestrator") != calibration.config_name():
        return None
    off = why_off(conf, say)
    if off:
        say(f"messaging off: {off}")
        return None
    return Orchestrator(say, conf=conf)


def check(seconds=3.0):
    conf = settings()
    off = why_off(conf)
    if off:
        print(f"messaging off: {off}")
        return 1
    account = login(conf)
    t = topics(conf)
    seen, probe = {}, f"{conf['topic_prefix']}/probe/{os.getpid()}"
    sent = {}
    done = threading.Event()

    def message(topic, data):
        if topic == probe:
            sent["back"] = time.perf_counter()
            done.set()
        else:
            seen[topic] = data

    def ready():
        sent["out"] = time.perf_counter()
        link.publish(probe, {"at": stamp()})

    link = Link(f"{conf['topic_prefix']}-check-{os.getpid()}",
                None, (t["everyone"], t["grant"], t["orchestrator"],
                       probe),
                message, ready, print, paho_client(), account, conf)
    link.start()
    done.wait(seconds)
    time.sleep(0.5)
    print(f"broker {account['host']}:{account['port']}, "
          f"{'connected' if link.up else 'NOT connected'}")
    if "back" in sent:
        print(f"round trip {1000 * (sent['back'] - sent['out']):.0f} ms")
    for topic, data in sorted(seen.items()):
        print(f"  {topic}: {json.dumps(data)}")
    link.close()
    return 0 if "back" in sent else 1


if __name__ == "__main__":
    sys.exit(check())
