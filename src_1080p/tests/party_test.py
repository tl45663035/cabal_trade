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

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
