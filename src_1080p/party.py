import os
import threading
import time

import messaging


def topics(conf, name):
    prefix = f"{conf['topic_prefix']}/party"
    return {"presence": f"{prefix}/presence/{name}",
            "everyone": f"{prefix}/presence/+",
            "mark": f"{prefix}/mark"}


class Party:
    """Two bots in one dungeon: each sends a mark at each sync point and
    waits for the partner's same mark of the same run."""

    def __init__(self, name, partner, leader, make=None, account=None,
                 conf=None, clock=time.monotonic, sleep=time.sleep,
                 repeat=1.0):
        conf = messaging.settings() if conf is None else conf
        self.name, self.partner, self.leader = name, partner, bool(leader)
        self.clock, self.sleep, self.repeat = clock, sleep, float(repeat)
        self.session = f"{os.getpid()}-{time.time_ns()}"
        self.t = topics(conf, name)
        self.lock = threading.Lock()
        self.online = False
        self.heard = self.acked = False
        self.runs = 0
        self.run = None
        self.last_run = None
        self.finished = set()
        self.alone = False
        self.formed = False
        self.seen = {}
        self.quit_runs = set()
        self.done_runs = set()
        self.rooms_of = {}
        self.notes = []
        self.link = messaging.Link(
            f"{conf['topic_prefix']}-party-{name}-{self.session}",
            (self.t["presence"], {"online": False, "session": self.session}),
            (self.t["everyone"], self.t["mark"]),
            self._message, self._ready, self.note,
            make or messaging.paho_client(), account or messaging.login(conf),
            conf)
        self.link.start()

    def note(self, line):
        with self.lock:
            self.notes.append(line)

    def drain(self, say):
        with self.lock:
            notes, self.notes = self.notes, []
        for line in notes:
            say(f"  party: {line}")

    def _ready(self):
        self.note(f"connected as {self.name}; partner {self.partner}")
        self.link.publish(self.t["presence"], {
            "online": True, "session": self.session}, retain=True)

    def _message(self, topic, data):
        if topic == f"{self.t['everyone'][:-1]}{self.partner}":
            online = bool(data.get("online"))
            with self.lock:
                changed, self.online = self.online != online, online
            if changed:
                self.note(f"{self.partner} is "
                          f"{'online' if online else 'offline'}")
            return
        if topic != self.t["mark"] or data.get("from") != self.partner:
            return
        mark, run = data.get("mark"), data.get("run")
        if mark in ("hello", "hello_back"):
            with self.lock:
                self.heard = True
                self.acked = self.acked or mark == "hello_back"
            if mark == "hello":
                self.send("hello_back")
            return
        with self.lock:
            if mark == "ready":
                self.seen[("ready", run)] = data
                return
            self.seen[(mark, run)] = data
            if mark == "quit":
                self.quit_runs.add(run)
            elif mark == "done":
                self.done_runs.add(run)

    def greet(self, stop=None):
        sent = None
        while True:
            if stop is not None and stop():
                return False
            with self.lock:
                if self.heard and self.acked:
                    return True
            now = self.clock()
            if sent is None or now - sent >= self.repeat:
                self.send("hello")
                sent = now
            self.sleep(0.1)

    def partner_online(self):
        with self.lock:
            return self.online

    def send(self, mark, **extra):
        self.link.publish(self.t["mark"], {
            "from": self.name, "run": self.run, "mark": mark, **extra})

    def _wait(self, found, mark, timeout, stop, extra):
        deadline = self.clock() + timeout
        sent = None
        while True:
            if stop is not None and stop():
                return None
            with self.lock:
                hit = found()
                quit = self.run is not None and (
                    self.run in self.quit_runs or self.run in self.done_runs)
            if hit is not None:
                return hit
            if quit:
                return False
            now = self.clock()
            if now >= deadline:
                return False
            if sent is None or now - sent >= self.repeat:
                self.send(mark, **extra)
                sent = now
            self.sleep(min(0.1, max(0.0, deadline - now)))

    def begin_run(self, rooms, formed, timeout, stop=None, left=None):
        """The `ready` meet: the leader names the run, the member takes it.
        Returns the partner's ready data, or None (stopped) / False (no
        partner within timeout; this run is solo)."""
        self.alone = False
        extra = {"rooms": list(rooms), "party": bool(formed), "left": left}
        if self.leader:
            self.runs += 1
            self.run = f"{self.session}-{self.runs}"
            with self.lock:
                self.seen = {key: data for key, data in self.seen.items()
                             if key[0] != "ready"}

            def found():
                for (mark, run), data in self.seen.items():
                    if mark == "ready" and run in (None, self.run):
                        return data
                return None
        else:
            self.run = None

            def found():
                for (mark, run), data in self.seen.items():
                    if (mark == "ready" and run is not None
                            and run not in self.finished
                            and run not in self.done_runs):
                        self.run = run
                        return data
                return None
        got = self._wait(found, "ready", timeout, stop, extra)
        if not got:
            self.alone = True
            return got
        self.rooms_of[self.run] = got.get("rooms")
        self.send("ready", **extra)
        return got

    def meet(self, mark, timeout, stop=None):
        """Sends this mark and waits for the partner's same mark of this run.
        True when both are there; False when the wait ran out or the partner
        quit (the rest of the run is solo); None when stopped."""
        if self.alone or self.run is None:
            return False
        run = self.run
        got = self._wait(lambda: self.seen.get((mark, run)), mark, timeout,
                         stop, {})
        if got is None:
            return None
        if not got:
            self.alone = True
            return False
        self.send(mark)
        return True

    def solo(self):
        self.alone, self.run = True, None

    def finish(self, failed=False):
        if self.run is not None and not self.alone:
            self.send("quit" if failed else "done")
        self.last_run = self.run
        if self.run is not None:
            self.finished.add(self.run)
            with self.lock:
                self.seen = {key: data for key, data in self.seen.items()
                             if key[1] not in self.finished}

    def close(self, wait=0.0):
        if self.run is not None:
            self.send("quit")
        self.link.close((self.t["presence"], {
            "online": False, "session": self.session}), wait)
