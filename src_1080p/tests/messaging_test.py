import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["messaging_test.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import messaging

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


from fake_broker import Broker, Client


CONF = dict(calibration.load_shared()["messaging"])
P = CONF["topic_prefix"]
KEY_HEX = "5e" * 32
KEY = bytes.fromhex(KEY_HEX)
ACCOUNT = {"host": "broker.test", "port": 8883, "secret": KEY_HEX}
broker = Broker()
made = []


def make(client_id):
    made.append(Client(broker, client_id))
    return made[-1]


said = []
clock = [1000.0]


def orchestrator():
    return messaging.Orchestrator(said.append, make=make, account=ACCOUNT, conf=CONF, start=False)


def peer(name, special=True):
    return messaging.Participant(name, special, make=make, account=ACCOUNT, conf=CONF, clock=lambda: clock[0])


def holder(*peers):
    return [p.name for p in peers if p.holds()]


def unwrap(payload):
    return json.loads(payload)["m"]


def grant():
    return unwrap(broker.retained[f"{P}/grant"])


def signed(topic, body):
    return messaging.sign(KEY, topic, body).encode()


outsider = Client(Broker(), "outsider")


def orchestrator_client():
    return next(c for c in broker.clients if c.client_id == f"{P}-orchestrator")


check("the order is bot, Trung, Cang, bot orchestrates, the poll runs every minute",
      CONF["order"] == ["bot", "trung", "cang"] and CONF["orchestrator"] == "bot" and CONF["poll_seconds"] == 60,
      str(CONF))

boss = orchestrator()
check("the channel and the signing key come from config: a random channel name and a 32-byte key",
      P.startswith("cabal-") and len(P) > 12 and len(bytes.fromhex(CONF["secret"])) == 32
      and CONF["host"] == "broker.emqx.io" and CONF["port"] == 8883, f"{CONF['host']}:{CONF['port']}")
check("the orchestrator connects over TLS with no login and says it is online, kept on the broker",
      made[0].tls and made[0].auth is None and unwrap(broker.retained[f"{P}/orchestrator"])["online"] is True, "")
check("its will says offline, signed, kept on the broker",
      messaging.opened(KEY, made[0].will[0], made[0].will[1]) == {"online": False, "session": boss.session}
      and made[0].will[2], "")
boss.begin()
check("with nobody online, nobody holds the right and no grant is sent", f"{P}/grant" not in broker.retained, "")

bot = peer("bot")
check("bot comes online and holds turn 1", holder(bot) == ["bot"] and grant()["turn"] == 1, str(grant()))
check("bot's new turn is picked up once at the next row", bot.take_fresh() and not bot.take_fresh(), "")
trung = peer("trung")
check("Trung coming online does not move the right", holder(bot, trung) == ["bot"] and grant()["turn"] == 1, "")
cang = peer("cang", special=False)
check("Cang with special rows off is online but never a candidate", "cang" not in boss.online, str(boss.online))

started = time.perf_counter()
reps = 10_000
for _ in range(reps):
    bot.holds()
check(f"asking whether this machine holds the right costs {(time.perf_counter() - started) / reps * 1e6:.2f} us",
      (time.perf_counter() - started) / reps < 50e-6, "")

started = time.perf_counter()
told = bot.sold("Chaos Core")
cycle = (time.perf_counter() - started) * 1000
check(f"bot's row sells out: Trung holds turn 2 ({cycle:.2f} ms through the in-memory broker)",
      told == 1 and holder(bot, trung) == ["trung"] and grant()["turn"] == 2 and grant()["holder"] == "trung", str(grant()))
check("bot does not act on its spent turn", not bot.take_fresh() and not bot.holds(), "")
check("Trung picks up its turn once", trung.take_fresh() and not trung.take_fresh(), "")
lines = []
bot.drain(lines.append)
check("every script hears the sold-out: bot's log shows Trung's turn",
      any("turn 2: trung holds it (bot's special row sold out)" in line for line in lines), str(lines))
lines = []
cang.drain(lines.append)
check("Cang, not a candidate, still hears that bot's row sold out",
      any("bot's Chaos Core special row sold out" in line for line in lines), str(lines))

trung.sold("Chaos Core")
check("Trung's row sells out; Cang has special rows off, so the turn wraps to bot", holder(bot, trung) == ["bot"]
      and grant()["turn"] == 3, str(grant()))

broker.drop(made[[c.client_id for c in made].index(next(c.client_id for c in made if "cang" in c.client_id))], will=False)
cang = peer("cang")
check("Cang comes back with special rows on and becomes a candidate", "cang" in boss.online, str(boss.online))
order = []
for current in (bot, trung, cang, bot, trung):
    current.sold("Chaos Core")
    order.append(grant()["holder"])
check("turns run in order: bot -> Trung -> Cang -> bot -> Trung -> Cang", order == ["trung", "cang", "bot", "trung", "cang"],
      str(order))

client_of = {p.name: next(c for c in reversed(made) if f"-{p.name}-" in c.client_id) for p in (bot, trung, cang)}
broker.drop(client_of["cang"])
check("Cang, holding the right, drops off: its will hands the right on at once, to bot",
      grant()["holder"] == "bot" and "went offline" in grant()["why"] and holder(bot, trung) == ["bot"], str(grant()))
check("Cang's last turn number is stale now", not cang.holds(), "")
cang_old = cang
cang = peer("cang")
client_of["cang"] = made[-1]
check("Cang reconnects: it is a candidate again but the right stays with bot", grant()["holder"] == "bot"
      and "cang" in boss.online, str(boss.online))
broker.publish(cang_old.t["presence"], signed(cang_old.t["presence"], {"online": False, "session": cang_old.session}),
               True, outsider)
check("a late will from Cang's old session does not take the new session offline", "cang" in boss.online, "")

turn = grant()["turn"]
stale = {"from": "trung", "epoch": 1, "turn": 2, "item": "Chaos Core"}
broker.publish(f"{P}/sold", signed(f"{P}/sold", stale), False, outsider)
check("a sold-out from an earlier turn is ignored", grant()["turn"] == turn and grant()["holder"] == "bot", "")

forged = {"holder": "trung", "epoch": 10 ** 15, "turn": 999, "why": "forged"}
broker.publish(f"{P}/grant", json.dumps(forged).encode(), False, outsider)
check("a grant without the group's signature is ignored", holder(bot, trung, cang) == ["bot"], str(holder(bot, trung, cang)))
broker.publish(f"{P}/grant", messaging.sign(bytes(32), f"{P}/grant", forged).encode(), False, outsider)
check("a grant signed with another key is ignored", holder(bot, trung, cang) == ["bot"], str(holder(bot, trung, cang)))
moved = messaging.sign(KEY, f"{P}/sold", forged)
broker.publish(f"{P}/grant", moved.encode(), False, outsider)
check("a properly signed message moved to another channel is ignored", holder(bot, trung, cang) == ["bot"], "")
broker.publish(f"{P}/grant", b"", False, outsider)
check("an empty message on the grant channel changes nothing", holder(bot, trung, cang) == ["bot"], "")
lines = []
trung.drain(lines.append)
check("the ignored messages are noted once for the channel, not once each",
      sum("does not carry this group's signature" in line for line in lines) == 1, str(lines))
check("every message the scripts sent carries the group's signature",
      all(messaging.opened(KEY, t, payload) is not None for c, t, payload in broker.log
          if c.startswith(P)), "")

deaf = trung._message
trung._message = lambda topic, data: None if topic == trung.t["ping"] else deaf(topic, data)
trung.link.on_message = trung._message
boss.poll()
missing = boss.close_poll()
check("Trung stops answering the minute poll: counted offline", missing == ["trung"] and "trung" not in boss.online,
      str(missing))
trung.link.on_message = deaf
trung._message = deaf
boss.poll()
check("Trung answers the next poll and is online again", "trung" in boss.online, str(boss.online))
bot.sold("Chaos Core")
check("so the next turn goes to Trung", grant()["holder"] == "trung", str(grant()))
broker.drop(client_of["trung"], will=False)
trung.link.up = False
for c in (client_of["trung"],):
    c.on_disconnect = trung.link._lost
check("Trung, holding the right, stops answering polls: the poll hands the right on",
      boss.poll() and boss.close_poll() == ["trung"] and grant()["holder"] == "cang", str(grant()))

peers = {"bot": bot, "cang": cang}


def holders():
    return sorted(name for name, p in peers.items() if p.holds())


check("Cang holds the right and nobody else does", holders() == ["cang"] and grant()["holder"] == "cang", str(holders()))

boss.poll()
broker.drop(client_of["cang"])
check("Cang's driver dies holding the right: its will hands it to bot at once",
      grant()["holder"] == "bot" and "cang went offline" in grant()["why"] and holders() == ["bot"], str(grant()))
cang = peer("cang")
client_of["cang"] = made[-1]
peers["cang"] = cang
check("Cang's new driver joins after the ping went out and is not counted offline at that poll",
      "cang" not in boss.close_poll() and "cang" in boss.online, str(boss.online))
check("and the right stays with bot", holders() == ["bot"], str(holders()))

before = grant()["epoch"]
broker.drop(made[0])
check("bot's orchestrator drops off: nobody holds the right until it is back",
      holders() == [] and unwrap(broker.retained[f"{P}/orchestrator"])["online"] is False, str(holders()))
boss2 = orchestrator()
boss2.begin()
check("a new orchestrator reads the kept grant, and bot keeps the right on the same turn",
      grant()["epoch"] == before and grant()["holder"] == "bot" and "turn" in said[-1]
      and "bot keeps the right it held" in said[-1], f"{grant()}; {said[-1]}")
check("bot holds it again as soon as the orchestrator is back", holders() == ["bot"], str(holders()))

clock[0] += CONF["poll_seconds"] * CONF["lease_polls"] + 1
check("with no poll or grant for three polls, the holder stops holding", holders() == [], str(holders()))
boss2.poll()
missing = boss2.close_poll()
check("the next poll renews it, and catches Trung, whose kept presence still said online though it was gone",
      missing == ["trung"] and holders() == ["bot"], f"{missing}; {holders()}")

client = client_of["bot"]
client.connected = False
bot.link.up = False
check("while its connection is down, bot does not hold the right", holders() == [], str(holders()))
turn = grant()["turn"]
bot.sold("Chaos Core")
check("a sold-out sent while the connection is down waits in the queue",
      grant()["turn"] == turn and len(client.queued) == 1 and client.queued[0][0] == f"{P}/sold", str(client.queued))
broker.join(client)
check("and goes out on reconnect: the right moves on to Cang", grant()["turn"] == turn + 1
      and grant()["holder"] == "cang" and holders() == ["cang"], f"{grant()}; {holders()}")

epochs = [unwrap(p)["epoch"] for c, t, p in broker.log if t == f"{P}/grant" and c.startswith(P)]
check("every grant carries a larger number than the one before", all(b > a for a, b in zip(epochs, epochs[1:])),
      str(epochs[-4:]))
grants_from = {c for c, t, _p in broker.log if t == f"{P}/grant" and c.startswith(P)}
check("only the orchestrator ever sends a grant", grants_from == {f"{P}-orchestrator"}, str(grants_from))

alone = Broker()
broker = alone
said.clear()
boss3 = orchestrator()
boss3.begin()
solo = peer("bot")
turns = []
for _ in range(3):
    solo.take_fresh()
    solo.sold("Chaos Core")
    turns.append((grant()["holder"], solo.take_fresh()))
check("with only bot online, bot gets every turn and picks each one up", turns == [("bot", True)] * 3, str(turns))

lost = Broker()
broker = lost
said.clear()
first = orchestrator()
first.begin()
one, two = peer("bot"), peer("trung")
check("a fresh pair: bot holds turn 1", grant()["holder"] == "bot", str(grant()))
held = grant()
broker.drop(orchestrator_client())
one.sold("Chaos Core")
check("bot sells while the orchestrator is down: nobody hears it, the grant still names bot",
      grant() == held and not one.holds() and not two.holds(), str(grant()))
second = orchestrator()
second.begin()
check("the restarted orchestrator keeps bot at first, as the kept grant says", grant() == held, str(grant()))
second.poll()
check("bot's poll answer shows its turn is used, so the right moves on to Trung within one poll",
      grant()["holder"] == "trung" and "poll answer shows" in grant()["why"] and two.holds(), str(grant()))
second.close_poll()

early = Broker()
broker = early
said.clear()
boss_a = orchestrator()
boss_a.begin()
x, y = peer("bot"), peer("trung")
broker.drop(orchestrator_client())
boss_b = orchestrator()
x.sold("Chaos Core")
check("a sale that reaches a new orchestrator before it is ready waits for it", grant()["holder"] == "bot"
      and boss_b.owed, str(boss_b.owed))
boss_b.begin()
check("and moves the right on as soon as it starts", grant()["holder"] == "trung" and y.holds(), str(grant()))

old = {"online": False, "session": "1234-old"}
broker.publish(f"{P}/orchestrator", signed(f"{P}/orchestrator", old), True, outsider)
check("a late offline from an old orchestrator session does not switch the scripts off", y.holds() and y.boss,
      str(y.boss))

notes = []
for p in (bot, trung, cang, solo, one, two, x, y):
    p.drain(notes.append)
check("the signing key never appears in any log line",
      not any(KEY_HEX in line or CONF["secret"] in line for line in notes + said), "")

check("with the public broker, channel and key in config, nothing else is needed: messaging is on",
      messaging.why_off(CONF) is None, str(messaging.why_off(CONF)))
check("a config without the key leaves messaging off and says why",
      "lacks host, port, secret or topic_prefix" in (messaging.why_off(dict(CONF, secret="")) or ""), "")

runs, heard = [], []
real_paho, real_run = messaging._paho, messaging.subprocess.run
present = [None]
messaging._paho = lambda: present[0]


def pip(argv, **kw):
    runs.append((argv, kw.get("timeout")))
    present[0] = real_paho()
    return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()


messaging.subprocess.run = pip
try:
    ok = messaging.ensure_library(heard.append, CONF)
    check("a missing library is installed with this Python's pip, at the pinned version, then used",
          ok and runs and runs[0][0][:4] == [sys.executable, "-m", "pip", "install"]
          and runs[0][0][-1] == CONF["library"] == "paho-mqtt==2.1.0"
          and runs[0][1] == CONF["install_timeout"], str(runs))
    check("and the log says it was missing and is now installed",
          any("is not installed" in h for h in heard) and any("installed in" in h for h in heard), str(heard))
    runs.clear()
    check("once installed, the startup check runs no install", messaging.ensure_library(heard.append, CONF)
          and runs == [], str(runs))
    present[0] = None
    messaging.subprocess.run = lambda argv, **kw: type("Done", (), {"returncode": 1, "stdout": "",
                                                               "stderr": "ERROR: no network"})()
    heard.clear()
    off = messaging.why_off(CONF, heard.append)
    check("if pip fails, messaging stays off for this run, says why, and nothing raises",
          off and "not installed" in off and any("no network" in h for h in heard), f"{off}; {heard}")

    def boom(argv, **kw):
        raise messaging.subprocess.TimeoutExpired(argv, kw.get("timeout"))

    messaging.subprocess.run = boom
    heard.clear()
    check("a pip that hangs past the timeout leaves messaging off, nothing raises",
          not messaging.ensure_library(heard.append, CONF) and any("did not run" in h for h in heard), str(heard))
finally:
    messaging._paho, messaging.subprocess.run = real_paho, real_run
told = []
broker = Broker()
real_client = messaging.paho_client
messaging.paho_client = lambda: make
try:
    joined = messaging.start_participant("bot", True, say=told.append)
finally:
    messaging.paho_client = real_client
check("the driver's start joins the public broker from config over TLS with no login, and says nothing else",
      joined is not None and made[-1].target == (CONF["host"], CONF["port"], CONF["keepalive"])
      and made[-1].tls and made[-1].auth is None and told == [], f"{made[-1].target}; {told}")
joined.close()
check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
