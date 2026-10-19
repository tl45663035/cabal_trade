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
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_war_arena_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import driver
import dungeon
import war

checks = 0
failures = []


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


shared = json.loads((SRC / "config.json").read_text(encoding="utf-8"))
check(shared["dungeon"]["war_quiet_minutes"] == 35
      and dungeon.WAR_QUIET == datetime.timedelta(minutes=35),
      f"no CA for 35 min from each war's start, got {dungeon.WAR_QUIET}")
check(dungeon.WAR_LEAD == datetime.timedelta(seconds=dungeon.HARD_LIMIT + 60),
      f"no CA starts within the hard limit plus 60s of a war, got {dungeon.WAR_LEAD}")
for config in sorted((SRC / "configs").glob("*.json")):
    own = json.loads(config.read_text(encoding="utf-8"))
    mine = own.get("dungeon") or {}
    check(not {"war_quiet_minutes", "war_margin_seconds", "hard_limit_seconds"} & set(mine)
          and "war" not in own,
          f"{config.name} keeps config.json's war schedule and arena war window")

PACIFIC = dungeon.RESET_ZONE
server = {"now": None}
saved_now = war.now
war.now = lambda verbose=False: server["now"]


def at(text):
    server["now"] = datetime.datetime.fromisoformat(text)
    return dungeon.war_hold()


def pacific(text):
    return datetime.datetime.fromisoformat(text).replace(tzinfo=PACIFIC)


try:
    check(war.next_war(datetime.datetime(2026, 10, 19, 21, 0),
                       dungeon.WAR_QUIET) == datetime.datetime(2026, 10, 19, 22, 0),
          "the next war after server 21:00 is 22:00")
    check(at("2026-10-19T21:48:59") is None and at("2026-10-19T21:49:00") is None,
          "a CA may start until 11 min before the 22:00 server war (13:00 Pacific)")
    want = (pacific("2026-10-19T12:49"), pacific("2026-10-19T13:00"),
            pacific("2026-10-19T13:35"))
    for moment in ("2026-10-19T21:49:01", "2026-10-19T22:00:00",
                   "2026-10-19T22:20:00", "2026-10-19T22:34:59"):
        got = at(moment)
        check(got == want, f"server {moment}: no CA from 12:49 until 13:35 Pacific, got {got}")
    check(at("2026-10-19T22:35:00") is None,
          "a CA may start again 35 min after the war's start")
    got = at("2026-10-20T00:49:30")
    check(got == (pacific("2026-10-19T15:49"), pacific("2026-10-19T16:00"),
                  pacific("2026-10-19T16:35")),
          f"the 01:00 server war after midnight holds CAs too, got {got}")
    check(at("2026-10-19T23:59:00") is None,
          "between wars a CA may start")
    server["now"] = None
    check(dungeon.war_hold() is None, "with no server time the war hold says nothing")

    calls = []
    hold = {"value": want}
    stubs = {"resume": lambda: None, "dungeon_open": lambda: True,
             "off_today": lambda today: False, "server_day": lambda: "2026-10-19",
             "next_level": lambda today: 4, "war_hold": lambda: hold["value"],
             "start": lambda level, verbose=True, rotate=False:
                 calls.append(("start", level)) or time.monotonic() + 300,
             "kill_and_finish": lambda verbose=True: calls.append(("finish",))}
    saved = {name: getattr(dungeon, name) for name in stubs}
    saved_on = driver.dungeon_on
    saved_state = dict(driver._DUNGEON)
    for name, stub in stubs.items():
        setattr(dungeon, name, stub)
    driver.dungeon_on = lambda: True
    try:
        driver._DUNGEON.update(due=None, spent=False, off=False, resume_checked=True,
                               closed=False, war=None)
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            first = driver.dungeon_turn()
            second = driver.dungeon_turn()
        lines = [line for line in said.getvalue().splitlines() if "war" in line]
        check(first is False and second is False and calls == [],
              f"no key is bought inside the war hold, got {first}, {second}, {calls}")
        check(lines == ["  dungeon: no CA from 12:49 until 13:35 America/Los_Angeles "
                        "time, around the 13:00 war; trading until then"],
              f"the hold is said once, got {lines}")

        driver._DUNGEON.update(due=time.monotonic() - 1)
        with contextlib.redirect_stdout(io.StringIO()):
            acted = driver.dungeon_turn()
        check(acted is True and calls == [("finish",)] and driver._DUNGEON["due"] is None,
              f"an arena already running still finishes, then no new one starts, got {acted}, {calls}")

        calls.clear()
        hold["value"] = None
        with contextlib.redirect_stdout(io.StringIO()):
            acted = driver.dungeon_turn()
        check(acted is True and calls == [("start", 4)],
              f"outside the hold the next CA starts as before, got {acted}, {calls}")
    finally:
        for name, value in saved.items():
            setattr(dungeon, name, value)
        driver.dungeon_on = saved_on
        driver._DUNGEON.clear()
        driver._DUNGEON.update(saved_state)
finally:
    war.now = saved_now

check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
