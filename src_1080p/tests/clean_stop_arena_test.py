import contextlib
import io
import json
import os
import sys
import tempfile
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
import cleanly_terminate
import dungeon
import supervise

checks = 0
failures = []


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


SCRATCH = Path(tempfile.mkdtemp(prefix="cabal_clean_stop_"))
GIFT = "  pressed 2 Receive button(s); the gift box at [960, 540] took 3,859 ms"
TRADE = "  listed 1 at 176,790,000; it lands in row 11"
START3 = "  dungeon: buying the level 3 key and starting the next one"
START2 = "  dungeon: buying the level 2 key and starting the next one"
RESUME = "  dungeon: resuming level 3 after a script crash; the boss is due in 120s"
WAITING = "  CA3: trading until 5s before the timer is up, in 347s"
BOSS = "DUNGEON: the boss is due; trading stops here and the dungeon runs"
SKIPPED = "  the dungeon is over; kill_phase and the rest are skipped"
KILL_OVER = "  kill_bm 'Chaotic Killian Grausten': the dungeon is over, after 0 round(s) in 36.4s"
OVER3 = "  chaos_arena CA3: the dungeon is over"
OVER2 = "  chaos_arena CA2: the dungeon is over"
OVERTIME = "  chaos_arena CA2: past its 600s; left it by hand"
STORM = ("  dungeon: the level 2 row [1, 2, 3, 4] reads ''; the storm click opened no "
         "dungeon list. Nothing more clicked. We are not at the storm, so no more "
         "dungeons this run; trading only")


def run(start, steps, alive_for=None):
    log = SCRATCH / f"case_{checks}.log"
    log.write_bytes(("\r\n".join(start) + "\r\n").encode("utf-8"))
    state = {"sleeps": 0}

    def sleep(_seconds):
        if state["sleeps"] < len(steps):
            with open(log, "ab") as f:
                f.write(("\r\n".join(steps[state["sleeps"]]) + "\r\n").encode("utf-8"))
        state["sleeps"] += 1
        if state["sleeps"] > len(steps) + 5:
            raise RuntimeError("the wait never ended")

    def pid():
        if alive_for is not None and state["sleeps"] >= alive_for:
            return None
        return 4242

    saved = cleanly_terminate.live_pid, cleanly_terminate.time.sleep
    cleanly_terminate.live_pid, cleanly_terminate.time.sleep = pid, sleep
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            result = cleanly_terminate.wait_until_quiet(log)
    finally:
        cleanly_terminate.live_pid, cleanly_terminate.time.sleep = saved
    return result, state["sleeps"], out.getvalue()


result, sleeps, said = run([TRADE, GIFT, TRADE], [[TRADE], [TRADE], [GIFT]])
check(result is True and sleeps == 3 and "gift box is open" in said,
      f"no arena: stops at the next gift box, got {result} after {sleeps} sleeps: {said!r}")

result, sleeps, said = run([TRADE, START3, WAITING, TRADE],
                           [[GIFT], [TRADE], [BOSS], [SKIPPED, KILL_OVER], [OVER3, START2], [GIFT]])
check(result is True and sleeps == 5 and "arena is over" in said and "gift box is open" not in said,
      f"in an arena: gift boxes and mid-arena 'is over' lines are passed, the stop comes right after "
      f"the arena's own end even with the next arena already started, got {result} after {sleeps}: {said!r}")

result, sleeps, said = run([TRADE, GIFT], [[TRADE], [START2], [GIFT], [TRADE], [OVERTIME], [GIFT]])
check(result is True and sleeps == 5 and "it is in an arena" in said,
      f"an arena that starts while waiting is finished first and an overrun ends it, "
      f"got {result} after {sleeps}: {said!r}")

result, sleeps, said = run([TRADE, RESUME], [[GIFT], [BOSS], [OVER3]])
check(result is True and sleeps == 3,
      f"a resumed arena counts as one in progress, got {result} after {sleeps}: {said!r}")

result, sleeps, said = run([TRADE, START2], [[STORM], [TRADE], [GIFT]])
check(result is True and sleeps == 1 and "arena is over" in said,
      f"a start that never reached the storm counts as ended, got {result} after {sleeps}: {said!r}")

result, sleeps, said = run([TRADE, START3, OVER3, START2, WAITING], [[GIFT], [OVER2]])
check(result is True and sleeps == 2 and "gift box is open" not in said,
      f"only the newest start counts: an earlier finished arena does not end the open one, "
      f"got {result} after {sleeps}: {said!r}")

result, sleeps, said = run([TRADE, START3, WAITING], [[GIFT], [TRADE], [TRADE]], alive_for=2)
check(result is False and "ended on its own" in said,
      f"the run ending before the arena is over is reported and nothing is pressed, got {result}: {said!r}")

check(cleanly_terminate.open_arena("\r\n".join([TRADE, START3, OVER3])) is None,
      "a finished arena is not open")
check(cleanly_terminate.open_arena("\r\n".join([TRADE, GIFT])) is None,
      "a log with no arena has none open")

real_state = dungeon.STATE
dungeon.STATE = SCRATCH / "dungeon_next.json"
try:
    dungeon.STATE.write_text(json.dumps({"server_dated": True, "ran": {"3": 1}, "day": "2026-10-18",
                                         "last": 3, "off_day": "2026-10-18"}), encoding="utf-8")
    gone = dungeon.allow_today()
    left = json.loads(dungeon.STATE.read_text(encoding="utf-8"))
    check(gone == "2026-10-18" and "off_day" not in left
          and left == {"server_dated": True, "ran": {"3": 1}, "day": "2026-10-18", "last": 3},
          f"allow_today removes only the day-off mark, got {gone!r} and {left}")
    before = dungeon.STATE.read_bytes()
    check(dungeon.allow_today() is None and dungeon.STATE.read_bytes() == before,
          "allow_today with no mark returns None and leaves the file alone")
    calls = []
    saved = dungeon.allow_today
    dungeon.allow_today = lambda: calls.append(1) or "2026-10-18"
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        supervise.allow_dungeons()
    dungeon.allow_today = lambda: calls.append(1) or None
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        supervise.allow_dungeons()
    dungeon.allow_today = saved
    check(calls == [1, 1] and "cleared the dungeon day-off mark for 2026-10-18" in out.getvalue()
          and quiet.getvalue() == "",
          f"the supervisor clears the mark and says so only when there was one: "
          f"{out.getvalue()!r} / {quiet.getvalue()!r}")
finally:
    dungeon.STATE = real_state

check(real_state.name == "dungeon_next.json" and dungeon.STATE == real_state,
      "the real state file path is restored")
source = (TOOLS / "supervise.py").read_text(encoding="utf-8")
main_body = source[source.index("def main():"):]
check(main_body.index("allow_dungeons()") < main_body.index("pids = driver_pids()"),
      "the supervisor clears the mark at launch, before it attaches to or launches a run")

check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
