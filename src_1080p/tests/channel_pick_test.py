import contextlib
import datetime
import io
import os
import sys
import types
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
import dungeon

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


@contextlib.contextmanager
def patched(*changes):
    saved = [(obj, name, getattr(obj, name)) for obj, name, _value in changes]
    try:
        for obj, name, value in changes:
            setattr(obj, name, value)
        yield
    finally:
        for obj, name, value in saved:
            setattr(obj, name, value)


REGIONS = [{"name": "SEA", "timezone": "Asia/Singapore", "channels": [21, 22, 23, 24, 25, 26, 27]},
           {"name": "NA", "timezone": "America/Los_Angeles", "channels": [11, 12]},
           {"name": "EU", "timezone": "Europe/Berlin", "channels": [1, 2, 3, 4, 5, 6, 7, 8, 9]}]
NIGHT = ["00:00", "06:00"]


def utc(hour, minute=0):
    return datetime.datetime(2026, 10, 21, hour, minute, tzinfo=datetime.timezone.utc)


def pick(moment, regions=REGIONS):
    offered = []

    def choice(channels):
        offered.append(list(channels))
        return channels[0]

    names, channel = dungeon.pick_channel(moment, regions, NIGHT, choice)
    return names, channel, offered[0]


names, channel, offered = pick(utc(18))
check("at 02:00 in Singapore the channel comes from SEA's 21-27",
      names == ["SEA"] and offered == list(range(21, 28)), f"{names} {offered}")
names, channel, offered = pick(utc(23))
check("at 01:00 in Berlin the channel comes from EU's 1-9",
      names == ["EU"] and offered == list(range(1, 10)), f"{names} {offered}")
names, channel, offered = pick(utc(9))
check("at 02:00 in Los Angeles the channel comes from NA's 11 and 12",
      names == ["NA"] and offered == [11, 12], f"{names} {offered}")
names, channel, offered = pick(utc(5))
check("with no region at night, the region whose night ended most recently is used",
      names == ["EU"] and offered == list(range(1, 10)), f"{names} {offered}")
names, channel, offered = pick(utc(6, 30))
check("with no region at night, the region whose night starts soonest is used",
      names == ["NA"] and offered == [11, 12], f"{names} {offered}")
both = [dict(REGIONS[0]), {"name": "JP", "timezone": "Asia/Tokyo", "channels": [30, 31]}]
names, channel, offered = pick(utc(17), both)
check("when two regions are at night the channel comes from both",
      names == ["SEA", "JP"] and offered == list(range(21, 28)) + [30, 31], f"{names} {offered}")
check("the picked channel is one of those offered", channel == offered[0], str(channel))


def burst_start(relog_fails):
    calls = []

    def relog(verbose=True, channel=None):
        calls.append(("relog", channel))
        if relog_fails:
            raise RuntimeError("no 'Channel 23' in the server list")

    fake_recovery = types.SimpleNamespace(
        relog=relog, recover=lambda verbose=True: calls.append(("recover", None)),
        logged_out=lambda: False, HOME_CHANNEL="Channel 12")
    burst = {"run_minutes": [20, 30], "rest_minutes": [20, 30], "check_every": 30,
             "regions": REGIONS, "night": NIGHT}
    saved_recovery = sys.modules.get("recovery")
    sys.modules["recovery"] = fake_recovery
    try:
        with patched((dungeon, "BURST", burst), (dungeon, "BURST_STATE", {"until": None}),
                     (dungeon, "online_now", lambda: utc(18)),
                     (dungeon, "random", types.SimpleNamespace(uniform=lambda a, b: a,
                                                               choice=lambda c: c[2])),
                     (dungeon, "run_dungeon", lambda *a, **k: calls.append(("run", None))),
                     (dungeon, "world_boss_turn", lambda *a, **k: False),
                     (dungeon, "start_block", lambda *a, **k: None),
                     (dungeon, "cool_down", lambda *a, **k: None),
                     (dungeon, "cancel_bm", lambda *a, **k: None)), \
                contextlib.redirect_stdout(io.StringIO()) as said:
            after = dungeon.run_in_turn("eop", "EOP", None, True)
            dungeon.run_in_turn("eop", "EOP", after, True)
    finally:
        if saved_recovery is not None:
            sys.modules["recovery"] = saved_recovery
        else:
            del sys.modules["recovery"]
    return calls, said.getvalue()


calls, said = burst_start(False)
check("a burst starts with a relog into the picked channel, then the runs",
      calls == [("relog", 23), ("run", None), ("run", None)], str(calls))
check("the log says which region and channel", "SEA night" in said and "Channel 23" in said, said)
calls, said = burst_start(True)
check("a relog that fails falls back to the home channel and the burst goes on",
      calls == [("relog", 23), ("recover", None), ("run", None), ("run", None)], str(calls))

check("Cang picks channels by region night time",
      [r["name"] for r in dungeon.BURST.get("regions", [])] == ["SEA", "NA", "EU"]
      and dungeon.BURST.get("night") == NIGHT, str(dungeon.BURST))

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
