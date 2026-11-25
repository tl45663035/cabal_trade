"""Run one batch of dungeon runs, then exit.

Used by tools/combo.py, which alternates these batches with single trading
passes. This file only calls what dungeon.py already offers; it changes none
of dungeon.py's own logic.

    py combo_dungeon.py --config cang --batch 5 [--first]

The batch follows the same order the full dungeon loop uses: the CA levels
in `dungeon.order` (first when `dungeon.levels_first` is on), then the
rotation groups, taking turns inside each group. Where the rotation stopped
is kept in logs/combo_state.json so the next batch carries on from there.
The outcome is written to logs/combo_result.json.
"""
import datetime
import json
import os
import sys
import time
from pathlib import Path


def _arg(name, default=None):
    for index, arg in enumerate(sys.argv):
        if arg.startswith(f"{name}="):
            return arg.split("=", 1)[1]
        if arg == name and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return default


os.environ["CABAL_CONFIG"] = _arg("--config", "") or ""
BATCH = int(_arg("--batch", "5"))
FIRST = "--first" in sys.argv
# dungeon.py reads its own flags (--frames) from sys.argv; the
# flags only this file knows are taken out so it never sees them.
_OWN = {"--batch", "--first"}
_clean, _skip = [sys.argv[0]], False
for _a in sys.argv[1:]:
    if _skip:
        _skip = False
        continue
    if _a in _OWN:
        _skip = _a == "--batch"
        continue
    if _a.startswith("--batch="):
        continue
    _clean.append(_a)
sys.argv = _clean

import calibration  # noqa: E402
import row_model  # noqa: E402
import dungeon  # noqa: E402

HERE = Path(__file__).resolve().parent
STATE = HERE / "logs" / "combo_state.json"
RESULT = HERE / "logs" / "combo_result.json"


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state), encoding="utf-8")


def ca_left():
    return [level for level in dungeon.ORDER if dungeon.level_left(level) > 0]


def missing_moves(name):
    """The move_to_* steps of a dungeon's sequence with no recording for this
    profile: entering it would raise mid-run, so the rotation skips it."""
    return [step for step in dungeon.DUNGEONS[name].get("sequence", [])
            if isinstance(step, str) and step.startswith("move_to_")
            and not (dungeon.SEQUENCES / name / f"{step}.jsonl").exists()]


def rotation_turn():
    """The first rotation group that still has runs, and its open names."""
    for group in dungeon.ROTATION_GROUPS:
        turn = []
        for name in group["dungeons"]:
            if (dungeon.rotation_done(name, group.get("successful"))
                    or name == dungeon.CUT_OFF["dungeon"]):
                continue
            missing = missing_moves(name)
            if missing:
                print(f"  {name}: skipped, no recording for "
                      f"{', '.join(missing)} in {dungeon.SEQUENCES / name}")
                continue
            turn.append(name)
        if turn:
            return group, turn
    return None, []


def next_rotation(state):
    group, turn = rotation_turn()
    if not turn:
        return None
    last, streak = state.get("last"), int(state.get("streak", 0))
    if last in turn and not state.get("failed"):
        batch = (int(dungeon.DUNGEONS[last]["daily_limit"])
                 if dungeon.DUNGEONS[last].get("all_in_turn")
                 else int(group.get("batch", dungeon.ROTATION_RUNS) or 1))
        if streak < batch:
            return last
    order = group["dungeons"]
    start = order.index(last) + 1 if last in order else 0
    for step in range(len(order)):
        name = order[(start + step) % len(order)]
        if name in turn:
            return name
    return turn[0]


def ca_in_trading():
    """With dungeon.enabled on, the trading passes run the CA levels the way
    driver.py always has (start, trade through the timer, kill the boss), so
    the batches leave them alone."""
    return bool(dungeon._DUNGEON.get("enabled"))


def skip_ca():
    """dungeon.combo_skip_ca: the batches run the rotation only, never the
    CA levels."""
    return bool(dungeon._DUNGEON.get("combo_skip_ca"))


def pick(state):
    levels_first = bool(dungeon._DUNGEON.get("levels_first"))
    levels = [] if ca_in_trading() or skip_ca() else ca_left()
    rotation = next_rotation(state)
    if levels and (levels_first or rotation is None):
        return ("ca", levels[0])
    if rotation is not None:
        return ("rotation", rotation)
    return None


def write_result(ran, left, note):
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps({
        "at": f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
        "day": str(dungeon.server_day()),
        "ran": ran, "left": left, "note": note}), encoding="utf-8")
    print(f"COMBO_RESULT ran={ran} left={left} {note}")


def run_batch(verbose=True):
    state = load_state()
    if ca_in_trading() and dungeon.resume() is not None:
        # a CA started by a trading pass is waiting on its boss; entering
        # another dungeon now would strand it
        write_result(0, True, "a CA is waiting on its boss; trading first")
        return
    if pick(state) is None:
        write_result(0, False, "every dungeon is at its daily limit")
        return
    dungeon.start_reports(verbose)
    dungeon.watch_disconnect(verbose)
    dungeon.watch_death(verbose)
    if FIRST:
        dungeon.start_home(verbose)
    ran = attempts = 0
    after = None
    while ran < BATCH and attempts < BATCH * 3:
        choice = pick(state)
        if choice is None:
            break
        attempts += 1
        kind, what = choice
        print(f"COMBO: run {ran + 1} of {BATCH} in this batch: "
              f"{'CA level ' + str(what) if kind == 'ca' else what}")
        if kind == "ca":
            location = dungeon.level_location(what)
            after, relogged = dungeon.run_or_relog(
                dungeon.TRADING_DUNGEON, location, after, verbose)
            ran += 1
            continue
        name = what
        last = state.get("last")
        if dungeon.ROTATION_RELOG and last and last != name:
            print(f"COMBO: {last} -> {name}; relogging before the next "
                  f"dungeon type")
            try:
                dungeon.relog(verbose)
            except Exception as exc:
                print(f"COMBO: the relog between dungeon types did not "
                      f"finish ({type(exc).__name__}: {exc}); carrying on")
        after, relogged = dungeon.run_with_play_time(name, after, verbose)
        if dungeon.DEATH_WATCH["again"]:
            continue
        failed = bool(relogged or dungeon.RUN_OUTCOME["failed"])
        streak = (int(state.get("streak", 0)) + 1
                  if state.get("last") == name else 1)
        state = {"last": name, "streak": streak, "failed": failed}
        save_state(state)
        ran += 1
    left = pick(state) is not None
    write_result(ran, left, "batch done" if left
                 else "every dungeon is at its daily limit")


ONE_SHOT = HERE / "logs" / "before_next_run.json"


def one_shot(verbose=True):
    """A one-time job queued for the start of the next batch: right-click
    each listed inventory slot (to equip it), then close the inventory.
    The file is deleted before anything is clicked, so it runs only once."""
    try:
        job = json.loads(ONE_SHOT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    ONE_SHOT.unlink(missing_ok=True)
    # an open Trade window takes the right-click as a Register, not an
    # equip (2026-11-12: the Arcana stayed in its slot), so shut it first
    calibration.close_everything(True)
    for click in job.get("right_click", []):
        tab, point = int(click["tab"]), tuple(click["point"])
        print(f"  one-time job: inventory tab {tab}, right-click {list(point)}"
              f" ({click.get('why', 'queued')})")
        dungeon.prepare(verbose, tab)
        dungeon.right_click(*point, 1.0)
    dungeon.close_inventory(verbose)


def main():
    row_model.start_backup_reader()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")
    calibration.log_to_file("dungeon")
    print(f"combo batch of {BATCH} dungeon run(s); running on commit "
          f"{dungeon.running_commit()}")
    calibration.frames_on(True if "--frames" in sys.argv else None)
    calibration.watch_for_stop()
    try:
        one_shot()
        run_batch()
    finally:
        calibration.frames_written()


if __name__ == "__main__":
    main()
