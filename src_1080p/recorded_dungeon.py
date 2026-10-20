"""Dungeon runs played from a recording.

A profile lists its recorded dungeons under dungeon.recorded in its config:
each names a dungeon from dungeon.dungeons, and adds the recording of a
run (made from just after Challenge to the "Cleared dungeon" banner), the
recording of leaving the dungeon by hand, and a time limit counted from the
Challenge click. The dungeon's own settings supply the rest: the entry, the
key, the difficulty, the daily limit, the start time, the BM cooldown, the
BM cancel before leaving, and the claim-and-leave buttons."""

import datetime
import threading
import time
import traceback
from pathlib import Path

import calibration
import dungeon
import record_action as ra
import record_all
import row_model

SEQUENCES = dungeon.SEQUENCES
OVERRIDES = ("daily_limit", "start_at", "cooldown", "end_wait", "before_exit")
JOIN_SLICE = 0.1


def recorded():
    return dungeon._DUNGEON.get("recorded") or {}


def enabled():
    return [name for name, entry in recorded().items() if entry.get("enabled")]


def settings(name):
    entry = recorded()[name]
    out = dict(dungeon.DUNGEONS[name])
    out.update({key: value for key, value in entry.items()
                if key in OVERRIDES or key not in out})
    return out


def find(name, file):
    if not file:
        return None
    path = Path(file)
    for place in (path if path.is_absolute() else None,
                  SEQUENCES / name / file, ra.OUT / file):
        if place is not None and place.exists():
            return place
    return None


def problem(name):
    if name not in dungeon.DUNGEONS:
        return f"{name} is not a dungeon in dungeon.dungeons"
    place = settings(name)
    if len(place.get("locations") or {}) != 1:
        return (f"{name} has {len(place.get('locations') or {})} locations; a "
                f"recorded run needs a dungeon with exactly one location")
    if not place.get("timeout_seconds"):
        return f"{name} has no timeout_seconds for its recorded run"
    for key, what in (("recording", "recording"), ("exit_recording", "exit recording")):
        path = find(name, place.get(key))
        if path is None:
            return (f"{name}: no {what} {place.get(key)!r} in "
                    f"{SEQUENCES / name} or {ra.OUT}")
        _head, _events, refused = record_all.prepare(path)
        if refused:
            return f"{name}: the {what} {path.name} is refused: {refused}"
    return None


def challenge(verbose=True):
    """dungeon.start_dungeon without its hotbar tab II click: from here on the
    recording is the only input."""
    if not dungeon.focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    up, seen = dungeon.wait_for(dungeon.CHALLENGE_WINDOW, dungeon.CHALLENGE_WORD,
                                dungeon.TRANSFER_TIMEOUT)
    if not up:
        raise RuntimeError(
            f"no {dungeon.CHALLENGE_WORD!r} in the dungeon window "
            f"{list(dungeon.CHALLENGE_WINDOW)} within {dungeon.TRANSFER_TIMEOUT:g}s; "
            f"it reads {seen!r}. Nothing clicked.")
    point = dungeon.centre(dungeon.CHALLENGE_WINDOW)
    if verbose:
        print(f"  {dungeon.CHALLENGE_WORD} at {point}")
    dungeon.click(*point, 0.0)
    return time.monotonic()


def screen_says():
    image = calibration.grab()
    defeat, banner = dungeon.read_windows(
        image, [tuple(dungeon.DEFEAT_SIGN["window"]), dungeon.CLEARED_WINDOW])
    if dungeon.CLEARED_TEXT.search(banner):
        calibration.snap("dungeon_end", image)
        return "cleared"
    if row_model._key(dungeon.DEFEAT_SIGN["word"]) in row_model._key(defeat):
        calibration.snap("dungeon_defeated", image)
        return "defeated"
    return None


def game_in_front():
    hwnd = record_all.find_game()
    return hwnd is not None and ra.user32.GetForegroundWindow() == hwnd


def watch(stop, outcome, deadline, every):
    """Reads the end screen and checks the game every `every` seconds until
    something ends the run; says what in outcome["end"], then sets stop."""
    try:
        while not stop.is_set():
            if calibration.STOPPED.is_set():
                outcome["end"] = "stopped"
            elif time.monotonic() >= deadline:
                outcome["end"] = "timeout"
            elif not game_in_front():
                outcome["end"] = "lost_game"
            else:
                outcome["end"] = screen_says()
            if outcome["end"]:
                break
            stop.wait(every)
    except Exception as exc:
        outcome["end"], outcome["error"] = "error", exc
    stop.set()


def play(path, stop):
    """Replays a recording on a worker thread until it ends or stop is set.
    The main thread only waits, so a Ctrl stop's interruption can't land
    inside the replay while it lets go of what it holds."""
    head, events, refused = record_all.prepare(path)
    if refused:
        raise RuntimeError(f"{path.name} is refused: {refused}")
    player = record_all.Replay(events, head.get("cursor"), head, path.parent,
                               on_ctrl=dungeon._replay_holds_ctrl)
    player.stop = stop
    worker = threading.Thread(target=player.play, daemon=True)
    worker.start()
    interrupted = None
    while worker.is_alive():
        try:
            worker.join(JOIN_SLICE)
            if calibration.STOPPED.is_set():
                stop.set()
        except KeyboardInterrupt as exc:
            interrupted = exc
            stop.set()
    if player.refused:
        raise RuntimeError(player.refused)
    if interrupted is not None:
        raise interrupted
    return player


def leave(name, place, verbose=True):
    """The recorded exit clip, played to its end."""
    path = find(name, place.get("exit_recording"))
    if path is None:
        raise RuntimeError(f"{name}: the exit recording {place.get('exit_recording')!r} "
                           f"is gone; the character may still be in the dungeon")
    if verbose:
        print(f"  {name}: leaving by the exit recording {path.name}")
    stop = threading.Event()
    play(path, stop)
    calibration.halt_if_stopped()


def run(name, verbose=True):
    """One recorded run. Returns "cleared", "defeated" or "timeout"."""
    trouble = problem(name)
    if trouble:
        raise RuntimeError(trouble)
    place = settings(name)
    location = dungeon.only_location(name)
    print(f"  RECORDED RUN start {name}", flush=True)
    result, inside = "failed", False
    try:
        dungeon.run_sequence(name, location, {}, verbose,
                             ["get_entry", "open_dungeon_key"], False)
        calibration.halt_if_stopped()
        clicked = challenge(verbose)
        inside = True
        count_run(name, verbose)
        every = float(dungeon._DUNGEON.get("end_check_every", 0.5))
        stop, outcome = threading.Event(), {"end": None}
        watcher = threading.Thread(
            target=watch, args=(stop, outcome, clicked + float(place["timeout_seconds"]),
                                every), daemon=True)
        watcher.start()
        try:
            play(find(name, place["recording"]), stop)
            while not stop.wait(JOIN_SLICE):
                calibration.halt_if_stopped()
        finally:
            stop.set()
            watcher.join()
        end = outcome["end"]
        if end == "error":
            raise outcome["error"]
        if end == "lost_game":
            inside = False
            raise RuntimeError("the game window went away or lost focus during the "
                               "replay; the replay stopped")
        calibration.halt_if_stopped()
        if verbose:
            print(f"  {name}: {end} {time.monotonic() - clicked:.1f}s after "
                  f"{dungeon.CHALLENGE_WORD}")
        if end == "cleared":
            if place.get("before_exit"):
                dungeon.run_sequence(name, location, {}, verbose, place["before_exit"], False)
            calibration.halt_if_stopped()
            dungeon.wait_timer(float(place.get("end_wait", dungeon.END_WAIT)), False)
            calibration.halt_if_stopped()
            dungeon.exit_dungeon(name, verbose)
            if place.get("after_exit"):
                dungeon.run_sequence(name, location, {}, verbose, place["after_exit"], False)
        elif end == "defeated":
            dungeon.press_buttons("defeat", dungeon.DEFEAT, verbose)
            calibration.halt_if_stopped()
            leave(name, place, verbose)
        else:
            if place.get("before_exit"):
                dungeon.run_sequence(name, location, {}, verbose, place["before_exit"], False)
            calibration.halt_if_stopped()
            leave(name, place, verbose)
        inside = False
        result = end
        return end
    except Exception:
        if inside and not calibration.STOPPED.is_set() and game_in_front():
            try:
                leave(name, place, verbose)
            except Exception as exc:
                print(f"  {name}: the exit recording failed too: {exc}")
        raise
    finally:
        print(f"  RECORDED RUN end {name} ({result})", flush=True)


def count_run(name, verbose=True):
    """Counts a run toward its dungeon's daily limit at the Challenge click,
    whether the limit comes from the dungeon or only from its recorded entry."""
    ran = dungeon.count_dungeon(name, dungeon.day_of(dungeon.internet_now()))
    limit = settings(name).get("daily_limit")
    if verbose:
        print(f"  {name}: {ran}" + (f" of {limit}" if limit is not None else "")
              + " entered on this dungeon day")
    return ran


def _opens_after(name):
    hours, minutes = (int(part) for part in str(
        settings(name).get("start_at", dungeon.START_AT)).split(":"))
    return ((datetime.timedelta(hours=hours, minutes=minutes) - dungeon.RESET_HOUR)
            % datetime.timedelta(days=1))


def until_open(name):
    since = dungeon.since_reset()
    if since is None:
        return None
    return max(0.0, (_opens_after(name) - since).total_seconds())


def until_next_day(name):
    since = dungeon.since_reset()
    if since is None:
        return None
    return (datetime.timedelta(days=1) - since + _opens_after(name)).total_seconds()


def pick(open_names, order, last):
    """The open dungeon after `last` in `order`, wrapping round."""
    start = order.index(last) + 1 if last in order else 0
    for step in range(len(order)):
        name = order[(start + step) % len(order)]
        if name in open_names:
            return name
    return None


def _waiting(name):
    """Seconds until name can run (0 now), or None if the online clock is
    not answering."""
    wait = until_open(name)
    if wait is None or wait > 0:
        return wait
    limit = settings(name).get("daily_limit")
    if limit is not None and dungeon.runs_today(name) >= int(limit):
        return until_next_day(name)
    return 0.0


def turn(off, rested, verbose=True, between=None):
    """Recorded runs back to back, taking turns, while any enabled dungeon is
    open. off: dungeons turned off for this trading run. rested["at"]: when
    the last run ended (the BM cooldown counts from it). between: called
    between runs (the world boss). Returns (acted, monotonic time of the next
    check, or None)."""
    acted, order = False, enabled()
    while True:
        calibration.halt_if_stopped()
        for name in order:
            if name in off:
                continue
            trouble = problem(name)
            if trouble:
                off.add(name)
                print(f"  dungeon: {name} is off for this trading run: {trouble}")
        waits = {name: _waiting(name) for name in order if name not in off}
        ready = {name for name, wait in waits.items() if wait == 0.0}
        if not ready:
            later = [wait for wait in waits.values() if wait]
            return acted, (time.monotonic() + min(later)) if later else None
        name = pick(ready, order, dungeon._state().get("recorded_last"))
        if acted and between is not None:
            between()
        if rested.get("at") is not None:
            left = rested["at"] + float(settings(name).get("cooldown", 0)) - time.monotonic()
            if left > 0:
                if verbose:
                    print(f"  dungeon: {left:.1f}s of BM cooldown before {name}")
                dungeon.wait_timer(left, False)
        print(f"  dungeon: recorded {name}, run {dungeon.runs_today(name) + 1}"
              + (f" of {settings(name)['daily_limit']}"
                 if settings(name).get("daily_limit") is not None else "")
              + " this dungeon day")
        acted = True
        try:
            outcome = run(name, verbose)
            if verbose:
                print(f"  dungeon: recorded {name} ended: {outcome}")
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            traceback.print_exc()
            calibration.snap("recorded_dungeon_failed")
            off.add(name)
            print(f"  dungeon: recorded {name} failed ({type(exc).__name__}: {exc}); "
                  f"it is off for this trading run, the others carry on")
        finally:
            rested["at"] = time.monotonic()
            state = dungeon._state()
            state["recorded_last"] = name
            dungeon._save(state)
