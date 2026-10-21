import contextlib
import io
import os
import sys
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


def rotate(per_turn, limits):
    ran, counts = [], {name: 0 for name in limits}

    def run_in_turn(name, location, after, verbose=True):
        ran.append(name)
        counts[name] += 1
        return name, 0.0

    with patched((dungeon, "ROTATION_RUNS", per_turn),
                 (dungeon, "run_in_turn", run_in_turn),
                 (dungeon, "limit_reached", lambda name: counts[name] >= limits[name]),
                 (dungeon, "runs_today", lambda name: counts[name])), \
            contextlib.redirect_stdout(io.StringIO()):
        dungeon.rotate(list(limits), None, True)
    return ran


ran = rotate(3, {"eop": 7, "ic1": 7})
check("each dungeon gets 3 entries in a row before the next one",
      ran[:12] == ["eop"] * 3 + ["ic1"] * 3 + ["eop"] * 3 + ["ic1"] * 3, str(ran))
check("a block stops early when the dungeon hits its limit, and the rest still run",
      ran == ["eop"] * 3 + ["ic1"] * 3 + ["eop"] * 3 + ["ic1"] * 3 + ["eop", "ic1"], str(ran))
ran = rotate(3, {"eop": 2, "ic1": 5})
check("a dungeon with fewer entries than a block stops at its limit",
      ran == ["eop", "eop", "ic1", "ic1", "ic1", "ic1", "ic1"], str(ran))
ran = rotate(1, {"eop": 2, "ic1": 2})
check("one entry per turn still alternates", ran == ["eop", "ic1", "eop", "ic1"], str(ran))

check("Cang runs 3 entries of each dungeon in a row", dungeon.ROTATION_RUNS == 3,
      str(dungeon.ROTATION_RUNS))

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
