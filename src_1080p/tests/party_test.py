import contextlib
import io
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "cang"
os.chdir(SRC)
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC))
sys.argv = ["dungeon.py", "--config", "cang"]
import no_input
TRIPPED = no_input.arm()
import calibration
import dungeon
import party
from fake_broker import Broker, Client

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


CONF = dict(calibration.load_shared()["messaging"])
ACCOUNT = {"host": "broker.test", "port": 8883, "secret": "5e" * 32}
ROOMS = ["move_to_A", "move_to_B", "move_to_C"]


def pair():
    broker = Broker()

    def make(client_id):
        return Client(broker, client_id)

    lead = party.Party("cang", "trung", True, make=make, account=ACCOUNT,
                       conf=CONF, repeat=0.05)
    member = party.Party("trung", "cang", False, make=make, account=ACCOUNT,
                         conf=CONF, repeat=0.05)
    return lead, member


def both(first, second):
    out = {}
    thread = threading.Thread(target=lambda: out.update(second=second()))
    thread.start()
    out["first"] = first()
    thread.join(5)
    return out.get("first"), out.get("second")


lead, member = pair()
check("each sees the other online", lead.partner_online() and member.partner_online())
got_lead, got_member = both(lambda: lead.begin_run(ROOMS, False, 3.0),
                            lambda: member.begin_run(ROOMS, False, 3.0))
check("both meet at ready and swap their rooms",
      bool(got_lead) and bool(got_member) and got_lead["rooms"] == ROOMS
      and got_member["rooms"] == ROOMS, f"{got_lead} {got_member}")
check("the member takes the leader's run", member.run == lead.run is not None,
      f"{member.run} {lead.run}")

started = time.monotonic()
met = both(lambda: lead.meet("challenge:0", 3.0),
           lambda: member.meet("challenge:0", 3.0))
check("both meet at Challenge", met == (True, True) and time.monotonic() - started < 2, str(met))

lead_room = threading.Thread(target=lambda: lead.meet("room:1:move_to_B", 3.0))
member.meet("room:0:move_to_A", 0.01)
member.alone = False
lead_room.start()
time.sleep(0.3)
check("the later door waits for this bot", lead_room.is_alive())
check("this bot arrives: both go on", member.meet("room:1:move_to_B", 3.0) is True)
lead_room.join(3)
check("the leader's wait ended once this bot arrived", not lead_room.is_alive())

started = time.monotonic()
check("no partner at a door: False after the wait", lead.meet("room:2:move_to_C", 0.3) is False)
check("the wait was the door wait", 0.25 <= time.monotonic() - started < 1.5)
check("after that the run is solo", lead.alone and lead.meet("anything", 3.0) is False)

lead.finish()
member.finish()
got_lead, got_member = both(lambda: lead.begin_run(ROOMS, True, 3.0),
                            lambda: member.begin_run(ROOMS, True, 3.0))
check("the next run meets again with a new run id", bool(got_lead) and bool(got_member)
      and member.run == lead.run and member.run != member.last_run, f"{member.run} {member.last_run}")
check("the party flag travels with ready", got_lead.get("party") is True and got_member.get("party") is True)
check("a fresh run is not solo", not lead.alone and not member.alone)

waiting = {}
thread = threading.Thread(target=lambda: waiting.update(met=lead.meet("room:0:move_to_A", 5.0)))
thread.start()
time.sleep(0.2)
member.finish(failed=True)
thread.join(3)
check("a partner that quits ends the wait at once, as solo",
      waiting.get("met") is False and lead.alone, str(waiting))

lead.finish()
member.finish()
stale = dict(lead.seen)
for old in member.finished:
    member.seen[("ready", old)] = {"from": "cang", "run": old, "mark": "ready"}
check("an old run's ready is not taken for a new run",
      member.begin_run(ROOMS, True, 0.3) is False and member.run is None)

lead.close()
time.sleep(0.1)
check("closing tells the partner it is offline", not member.partner_online())
member.close()



steps = ["get_entry", ["tap", "="], "move_to_A", ["kill_all", "x"], "move_to_B", "move_to_C"]
check("rooms are numbered by their order among the move_to steps",
      [sum(1 for earlier in steps[:at] if dungeon.step_name(earlier).startswith(dungeon.ROOM_PREFIX))
       for at, step in enumerate(steps) if dungeon.step_name(step).startswith(dungeon.ROOM_PREFIX)] == [0, 1, 2])
check("cang's party block: leader, partner trung, character Jarvan, ic1",
      dungeon.PARTY_SET["leader"] is True and dungeon.PARTY_SET["partner"] == "trung"
      and dungeon.PARTY_SET["partner_character"] == "Jarvan" and dungeon.PARTY_SET["dungeon"] == "ic1")

import recovery
sent = []
saved = (dungeon.tap, dungeon.escape_everything, dungeon.focus_game,
         recovery._type, calibration.snap, dungeon.PARTY_SET["settle"])
try:
    dungeon.tap = lambda vk, hold=None: sent.append(("key", vk))
    dungeon.escape_everything = lambda verbose=True: sent.append(("escape",))
    dungeon.focus_game = lambda *a, **k: True
    recovery._type = lambda text: sent.append(("type", text))
    calibration.snap = lambda *a, **k: None
    dungeon.PARTY_SET["settle"] = 0.0
    with contextlib.redirect_stdout(io.StringIO()):
        dungeon.invite_partner()
    check("the invite is Enter, '/party Jarvan', Enter, with no OCR",
          sent == [("escape",), ("key", dungeon.KEYS["Enter"]),
                   ("type", "/party Jarvan"), ("key", dungeon.KEYS["Enter"])],
          str(sent))
finally:
    (dungeon.tap, dungeon.escape_everything, dungeon.focus_game,
     recovery._type, calibration.snap, dungeon.PARTY_SET["settle"]) = saved

did = []
picks = iter([False, True])
saved = (dungeon.escape_everything, dungeon.focus_game, dungeon.right_click,
         dungeon.click, dungeon.find_word, dungeon.looting_selected,
         calibration.snap, dungeon.PARTY_SET["settle"])
try:
    dungeon.escape_everything = lambda verbose=True: did.append("escape")
    dungeon.focus_game = lambda *a, **k: True
    dungeon.right_click = lambda x, y, settle=0: did.append(("right", x, y))
    dungeon.click = lambda x, y, settle=0: did.append(("click", x, y))
    dungeon.find_word = lambda box, word, wait, label: did.append(("find", word)) or (1, 1)
    dungeon.looting_selected = lambda: next(picks)
    calibration.snap = lambda *a, **k: None
    dungeon.PARTY_SET["settle"] = 0.0
    with contextlib.redirect_stdout(io.StringIO()):
        dungeon.set_looting_rule()
    check("looting rule: HP bar, Looting Rule, the circle until it is selected, OK",
          did == ["escape", ("right", 143, 242), ("find", "Looting"), ("click", 434, 285),
                  ("find", "Immediate"), ("click", 790, 614), ("click", 790, 614),
                  ("find", "OK"), ("click", 966, 656)], str(did))
    did.clear()
    dungeon.find_word = lambda box, word, wait, label: None if word == "Looting" else (1, 1)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            dungeon.set_looting_rule()
        raised = False
    except RuntimeError:
        raised = True
    check("no Looting Rule in the menu: windows closed, an error, nothing more clicked",
          raised and did == ["escape", ("right", 143, 242), "escape"], str(did))
finally:
    (dungeon.escape_everything, dungeon.focus_game, dungeon.right_click,
     dungeon.click, dungeon.find_word, dungeon.looting_selected,
     calibration.snap, dungeon.PARTY_SET["settle"]) = saved

did = []
saved = (dungeon.escape_everything, dungeon.focus_game, dungeon.right_click,
         dungeon.click, dungeon.find_word, dungeon.left_notices,
         calibration.snap, dungeon.PARTY_SET["settle"])
try:
    dungeon.escape_everything = lambda verbose=True: did.append("escape")
    dungeon.focus_game = lambda *a, **k: True
    dungeon.right_click = lambda x, y, settle=0: did.append(("right", x, y))
    dungeon.click = lambda x, y, settle=0: did.append(("click", x, y))
    found = {"Leave": (415, 252), "sure": (900, 540)}
    dungeon.find_word = lambda box, word, wait, label: did.append(("find", word)) or found[word]
    counts = iter([1, 1, 2])
    dungeon.left_notices = lambda: next(counts)
    calibration.snap = lambda *a, **k: None
    dungeon.PARTY_SET["settle"] = 0.0
    with contextlib.redirect_stdout(io.StringIO()):
        left = dungeon.leave_party()
    check("leave party: HP bar, Leave where it was read, the confirmation, Yes, a new notice",
          left is True and did == ["escape", ("right", 143, 242), ("find", "Leave"),
                                   ("click", 415, 252), ("find", "sure"), ("click", 962, 614)],
          str(did))
    did.clear()
    dungeon.find_word = lambda box, word, wait, label: None
    with contextlib.redirect_stdout(io.StringIO()):
        left = dungeon.leave_party()
    check("no Leave in the menu: already out, windows closed, nothing clicked",
          left is False and did == ["escape", ("right", 143, 242), "escape"], str(did))
    did.clear()
    dungeon.find_word = lambda box, word, wait, label: found[word] if word == "Leave" else None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            dungeon.leave_party()
        raised = False
    except RuntimeError:
        raised = True
    check("no confirmation after Leave: an error and no Yes",
          raised and ("click", 962, 614) not in did, str(did))
    did.clear()
    dungeon.find_word = lambda box, word, wait, label: found[word]
    dungeon.left_notices = lambda: 1
    dungeon.LEAVING["gone_wait"], gone = 0.0, dungeon.LEAVING["gone_wait"]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            dungeon.leave_party()
        raised = False
    except RuntimeError:
        raised = True
    finally:
        dungeon.LEAVING["gone_wait"] = gone
    check("Yes with no new 'You have left the party' notice: an error", raised, str(did))
finally:
    (dungeon.escape_everything, dungeon.focus_game, dungeon.right_click,
     dungeon.click, dungeon.find_word, dungeon.left_notices,
     calibration.snap, dungeon.PARTY_SET["settle"]) = saved

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
