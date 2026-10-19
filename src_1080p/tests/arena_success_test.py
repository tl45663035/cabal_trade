import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
TOOLS = SRC.parent / "tools"
os.environ["CABAL_CONFIG"] = "bot"
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(TOOLS))
sys.argv = ["dungeon.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import dungeon
import profit_summary

checks = 0
failures = []


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


SCRATCH = Path(tempfile.mkdtemp(prefix="cabal_arena_success_"))
LIVE = dungeon.STATE.read_bytes() if dungeon.STATE.exists() else None
MANUAL = dungeon.DUNGEONS["chaos_arena"]["manual_exit"]
AFTER = dungeon.DUNGEONS["chaos_arena"]["after_exit"]
DAY = "2026-10-19"
done = []
ending = {"kind": "cleared"}


def run_sequence(dungeon_name, location, phase, verbose=True, steps=None,
                 stop_when_over=True, timer=None):
    if steps is not None:
        done.append(("sequence", steps))
        return
    if ending["kind"] == "none":
        phase["killing"].set()
        return
    dungeon.END["at"] = time.monotonic() - dungeon.END_WAIT
    dungeon.END["overtime"] = ending["kind"] == "overtime"
    dungeon.END["defeated"] = ending["kind"] == "defeated"
    dungeon.OVER.set()
    dungeon.ENDED.set()


def write(state):
    dungeon.STATE.write_text(json.dumps(state), encoding="utf-8")


def read():
    return json.loads(dungeon.STATE.read_text(encoding="utf-8"))


def arena(kind, location="CA4"):
    done.clear()
    ending["kind"] = kind
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        dungeon.run_dungeon("chaos_arena", location)
    dungeon.OVER.clear()
    dungeon.ENDED.clear()
    return said.getvalue()


saved = (dungeon.run_sequence, dungeon.right_click, dungeon.exit_dungeon,
         dungeon.focus_game, dungeon.off_for_the_day, dungeon.press_buttons,
         dungeon.END_TIMEOUT, dungeon.STATE)
dungeon.STATE = SCRATCH / "dungeon_next.json"
dungeon.run_sequence = run_sequence
dungeon.right_click = lambda x, y, settle: done.append(("rightclick", x, y))
dungeon.exit_dungeon = lambda *a, **k: done.append(("exit",))
dungeon.focus_game = lambda *a, **k: True
dungeon.off_for_the_day = lambda verbose=True: done.append(("off",))
dungeon.press_buttons = lambda label, steps, verbose=True: done.append(("buttons", label))
dungeon.END_TIMEOUT = 0.0
try:
    write({"server_dated": True, "ran": {"4": 2}, "successful": {"4": 1},
           "day": DAY, "last": 4})
    said = arena("cleared")
    left = read()
    check(left["successful"] == {"4": 2} and left["ran"] == {"4": 2}
          and left["day"] == DAY,
          f"a cleared arena that exits counts one more success, got {left}")
    check(done[1:] == [("exit",), ("sequence", AFTER)],
          f"the success is counted with the exit, before after_exit, got {done}")
    check("level 4: 2 of 2 entered were successful on this server day" in said,
          f"the log says the success, got {said!r}")

    for kind, ends in (("overtime", [("sequence", MANUAL), ("off",)]),
                       ("defeated", [("buttons", "defeat"), ("sequence", MANUAL)]),
                       ("none", [("sequence", MANUAL)])):
        said = arena(kind)
        left = read()
        check(left["successful"] == {"4": 2} and done == ends
              and "successful" not in said,
              f"a manual exit ({kind}) is not a success, got {left} and {done}: {said!r}")

    write({"server_dated": True, "ran": {"1": 5, "4": 1}, "day": DAY, "last": 4})
    said = arena("cleared", "CA1")
    check(read()["successful"] == {"1": 1},
          f"a state with no successes yet starts at one, got {read()}")

    write({"server_dated": True, "ran": {"4": 2}, "successful": {"4": 2},
           "day": DAY, "last": 4})
    dungeon.count_entry(4, True, DAY)
    left = read()
    check(left["ran"] == {"4": 3} and left["successful"] == {"4": 2},
          f"an entry on the same server day keeps the successes, got {left}")
    dungeon.count_entry(5, True, "2026-10-20")
    left = read()
    check(left["ran"] == {"5": 1} and left["successful"] == {}
          and left["day"] == "2026-10-20",
          f"the first entry of a new server day starts the successes again, got {left}")
    dungeon.count_entry(5, True, None)
    check(read()["successful"] == {} and read()["ran"] == {"5": 2},
          f"an entry with no online time keeps the day's successes, got {read()}")
finally:
    (dungeon.run_sequence, dungeon.right_click, dungeon.exit_dungeon,
     dungeon.focus_game, dungeon.off_for_the_day, dungeon.press_buttons,
     dungeon.END_TIMEOUT, dungeon.STATE) = saved
    dungeon.OVER.clear()
    dungeon.ENDED.clear()

REPORT = SCRATCH / "report"
REPORT.mkdir()
shift = profit_summary.shift
logs = profit_summary.LOGS
profit_summary.shift = lambda: datetime.timedelta(0)
profit_summary.LOGS = REPORT
try:
    today = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        hours=float(profit_summary.SETTINGS["game_facts"]["server_utc_offset_hours"]))
             ).date().isoformat()
    (REPORT / "dungeon_next.json").write_text(json.dumps(
        {"server_dated": True, "ran": {"1": 5, "2": 5, "3": 5, "4": 7},
         "successful": {"1": 5, "2": 4, "3": 5, "4": 6}, "day": today, "last": 4}),
        encoding="utf-8")
    shown = profit_summary.caught(profit_summary.report_dungeons).splitlines()
    check(shown[2] == "level     done  limit  successful"
          and shown[3] == "CA1          5      5           5"
          and shown[4] == "CA2          5      5           4"
          and shown[6] == "CA4          7      7           6"
          and shown[7] == "CA5          0      7           0"
          and shown[10] == "total       22     54          20",
          f"the report shows every level with its successes, got {shown}")
    (REPORT / "dungeon_next.json").write_text(json.dumps(
        {"server_dated": True, "ran": {"4": 7}, "successful": {"4": 6},
         "day": "2000-01-01"}), encoding="utf-8")
    shown = profit_summary.caught(profit_summary.report_dungeons).splitlines()
    check(shown[6] == "CA4          0      7           0"
          and shown[10] == "total        0     54           0",
          f"another day's counts are not shown, got {shown}")
finally:
    profit_summary.shift = shift
    profit_summary.LOGS = logs

check(dungeon.STATE.name == "dungeon_next.json"
      and (dungeon.STATE.read_bytes() if dungeon.STATE.exists() else None) == LIVE,
      "the live dungeon_next.json is left alone")
check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
