"""Dungeon runs played from a recording.

A profile lists its recorded dungeons under dungeon.recorded in its config:
each names a dungeon from dungeon.dungeons, and adds the recording of a
run (made from just after Challenge to the "Cleared dungeon" banner), the
recording of leaving the dungeon by hand, and a time limit counted from the
Challenge click. The dungeon's own settings supply the rest: the entry, the
key, the difficulty, the daily limit, the start time, the BM cooldown, the
BM cancel before leaving, and the claim-and-leave buttons."""

import threading
import time
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
        dungeon.count_challenge(name, location, {"started": dungeon.internet_now()},
                                verbose)
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
