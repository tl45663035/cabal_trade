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
OVERRIDES = ("daily_limit", "start_at", "cooldown", "end_wait", "before_exit", "manual_exit",
             "after_exit")
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
    location = next(iter(place["locations"].values()))
    for need in ("entry", "name"):
        if not location.get(need):
            return (f"{name}: its location has no {need!r} yet (the shop entry to "
                    f"Alt+click, and its name in the dungeon list), so the bot can't "
                    f"get in")
    if not place.get("timeout_seconds"):
        return f"{name} has no timeout_seconds for its recorded run"
    if place.get("daily_limit") is None:
        return (f"{name} has no daily_limit; without one its recorded runs would go "
                f"on back to back and trading would never resume")
    path = find(name, place.get("recording"))
    if path is None:
        return (f"{name}: no recording {place.get('recording')!r} in "
                f"{SEQUENCES / name} or {ra.OUT}")
    clips = [("recording", path, record_all.prepare)]
    for step in exit_steps(name):
        clip = exit_clip(name, step)
        if clip is not None:
            if not clip.exists():
                return f"{name}: its manual exit plays {clip}, which is not there"
            clips.append(("manual exit clip", clip, on_main_screen))
    for what, clip, check in clips:
        try:
            _head, _events, refused = check(clip)
        except Exception as exc:
            return f"{name}: the {what} {clip.name} can't be read ({type(exc).__name__}: {exc})"
        if refused:
            return f"{name}: the {what} {clip.name} is refused: {refused}"
    return None


def on_main_screen(path):
    """A manual exit clip checked the way dungeon.play checks it: the same
    main screen size, and every click and wheel turn on the main screen. The
    other monitors may be laid out differently (Chaos Arena's exit clip was
    recorded with monitors to the left), so cursor moves are kept on the main
    screen too. Returns (head, events, refused) like record_all.prepare."""
    head, events, refused = record_all.read_recording(path)
    if refused:
        return None, None, refused
    width, height = ra.screen()
    if head.get("screen") != [width, height]:
        return None, None, (f"{path.name} was recorded on a {head.get('screen')} main "
                            f"screen and this one is {[width, height]}")
    off = [e for e in events if e["kind"] == "mouse" and e["action"] not in ("move", "raw")
           and not (0 <= e["x"] < width and 0 <= e["y"] < height)]
    if off:
        return None, None, (f"{len(off)} of its clicks or wheel turns are off the main "
                            f"screen, the first at {[off[0]['x'], off[0]['y']]}")
    for e in events:
        if e["kind"] == "mouse" and "x" in e:
            e["x"] = min(max(e["x"], 0), width - 1)
            e["y"] = min(max(e["y"], 0), height - 1)
    head = {**head, "desktop": ra.desktop()}
    if head.get("cursor"):
        head["cursor"] = [min(max(head["cursor"][0], 0), width - 1),
                          min(max(head["cursor"][1], 0), height - 1)]
    return head, events, None


def exit_steps(name):
    """How the dungeon is left by hand, as dungeon.py leaves it after its time
    limit: its own manual_exit, or the shared one (Chaos Arena's exit clip)."""
    return settings(name).get("manual_exit", dungeon.MANUAL_EXIT)


def exit_clip(name, step):
    """The recording a manual exit step plays, or None for any other step
    (a button, a wait), which goes through dungeon.run_sequence."""
    part, *rest = step if isinstance(step, list) else [step]
    if part == "play":
        return SEQUENCES / rest[0] / f"{rest[1]}.jsonl"
    if not rest and (SEQUENCES / name / f"{part}.jsonl").exists():
        return SEQUENCES / name / f"{part}.jsonl"
    return None


def checked(name):
    """problem(name), with anything it raises turned into the reason."""
    try:
        return problem(name)
    except Exception as exc:
        return f"{name} could not be checked ({type(exc).__name__}: {exc})"


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


class GameLost(RuntimeError):
    """The game window went away, or lost focus and would not come back: no
    more input may be sent, and the rotation stops."""


def game_present():
    return record_all.find_game() is not None


def watch(stop, outcome, deadline, every, read=True):
    """Checks the game (and, with read, the end screen) every `every`
    seconds until something ends the run; says what in outcome["end"], then
    sets stop. A banner up at the deadline still counts as the end screen."""
    try:
        while not stop.is_set():
            if calibration.STOPPED.is_set():
                outcome["end"] = "stopped"
            elif not game_present():
                outcome["end"] = "lost_game"
            elif not game_in_front():
                outcome["end"] = "lost_focus"
            else:
                outcome["end"] = screen_says() if read else None
                if not outcome["end"] and time.monotonic() >= deadline:
                    outcome["end"] = "timeout"
            if outcome["end"]:
                break
            stop.wait(every)
    except Exception as exc:
        outcome["end"], outcome["error"] = "error", exc
    stop.set()


def load(path):
    """A recording, checked and parsed before any input goes out."""
    head, events, refused = record_all.prepare(path)
    if refused:
        raise RuntimeError(f"{path.name} is refused: {refused}")
    return head, events, path.parent


def play(recording, stop):
    """Replays a loaded recording on a worker thread until it ends or stop is
    set. The main thread only waits, so a Ctrl stop's interruption can't land
    inside the replay while it lets go of what it holds."""
    head, events, base = recording
    player = record_all.Replay(events, head.get("cursor"), head, base,
                               on_ctrl=dungeon._replay_holds_ctrl)
    player.stop = stop
    worker = threading.Thread(target=player.play, daemon=True)
    interrupted = None
    try:
        worker.start()
        while worker.is_alive():
            try:
                worker.join(JOIN_SLICE)
                if calibration.STOPPED.is_set():
                    stop.set()
            except KeyboardInterrupt as exc:
                interrupted = exc
                stop.set()
    finally:
        if worker.is_alive():
            stop.set()
            worker.join()
    if interrupted is not None:
        raise interrupted
    if player.refused:
        raise RuntimeError(player.refused)
    return player


def leave(name, place, verbose=True):
    """Leaves the dungeon by its manual exit, as dungeon.py does after its time
    limit. Its clips are played with the game watched: they stop the moment
    the game goes away or loses focus."""
    location = dungeon.only_location(name)
    if verbose:
        print(f"  {name}: leaving by the manual exit")
    for step in exit_steps(name):
        clip = exit_clip(name, step)
        if clip is None:
            dungeon.run_sequence(name, location, {}, verbose, [step], False)
        else:
            if not clip.exists():
                raise RuntimeError(f"{name}: the manual exit clip {clip.name} is gone; "
                                   f"the character may still be in the dungeon")
            head, events, refused = on_main_screen(clip)
            if refused:
                raise RuntimeError(f"{name}: the manual exit clip {clip.name} is refused: "
                                   f"{refused}")
            play_watched(name, (head, events, clip.parent), verbose)
        calibration.halt_if_stopped()


def play_watched(name, recording, verbose=True):
    """A clip played with the game watched (no end screen read)."""
    stop, outcome = threading.Event(), {"end": None}
    every = float(dungeon._DUNGEON.get("end_check_every", 0.5))
    guard = threading.Thread(target=watch, args=(stop, outcome, float("inf"), every, False),
                             daemon=True)
    guard.start()
    try:
        play(recording, stop)
    finally:
        stop.set()
        guard.join()
    calibration.halt_if_stopped()
    if outcome["end"] in ("lost_game", "lost_focus"):
        raise GameLost(f"{name}: the game {'went away' if outcome['end'] == 'lost_game' else 'lost focus'} "
                       f"during the manual exit; it stopped there")
    if outcome["end"] == "error":
        raise outcome["error"]


def run(name, verbose=True):
    """One recorded run. Returns "cleared", "defeated", "timeout" or
    "lost_focus" (the game was brought back and the run left); raises
    GameLost if the game is gone or won't come back."""
    trouble = checked(name)
    if trouble:
        raise RuntimeError(trouble)
    place = settings(name)
    location = dungeon.only_location(name)
    print(f"  RECORDED RUN start {name}", flush=True)
    result, inside = "failed", False
    try:
        recording = load(find(name, place["recording"]))
        dungeon.run_sequence(name, location, {}, verbose,
                             ["get_entry", "open_dungeon_key"], False)
        calibration.halt_if_stopped()
        day = dungeon.day_of(dungeon.internet_now())
        clicked = challenge(verbose)
        inside = True
        count_run(name, day, verbose)
        every = float(dungeon._DUNGEON.get("end_check_every", 0.5))
        stop, outcome = threading.Event(), {"end": None}
        watcher = threading.Thread(
            target=watch, args=(stop, outcome, clicked + float(place["timeout_seconds"]),
                                every), daemon=True)
        watcher.start()
        try:
            play(recording, stop)
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
            raise GameLost("the game window went away during the replay; the replay "
                           "stopped there")
        calibration.halt_if_stopped()
        if verbose:
            print(f"  {name}: {end} {time.monotonic() - clicked:.1f}s after "
                  f"{dungeon.CHALLENGE_WORD}")
        if end == "lost_focus":
            if not dungeon.focus_game():
                inside = False
                raise GameLost("the game lost focus during the replay and would not "
                               "come back to the front; the replay stopped there")
            print(f"  {name}: the game lost focus during the replay; it is back in "
                  f"front, and the run leaves by the manual exit")
        if end == "cleared":
            if place.get("before_exit"):
                dungeon.run_sequence(name, location, {}, verbose, place["before_exit"], False)
            calibration.halt_if_stopped()
            dungeon.wait_timer(float(place.get("end_wait", dungeon.END_WAIT)), False)
            calibration.halt_if_stopped()
            dungeon.exit_dungeon(name, verbose)
            inside = False
            if place.get("after_exit"):
                dungeon.run_sequence(name, location, {}, verbose, place["after_exit"], False)
        elif end == "defeated":
            dungeon.press_buttons("defeat", dungeon.DEFEAT, verbose)
            calibration.halt_if_stopped()
            inside = False
            leave(name, place, verbose)
        else:
            if place.get("before_exit"):
                dungeon.run_sequence(name, location, {}, verbose, place["before_exit"], False)
            calibration.halt_if_stopped()
            inside = False
            leave(name, place, verbose)
        result = end
        return end
    except GameLost:
        raise
    except Exception:
        if inside and not calibration.STOPPED.is_set() and game_in_front():
            try:
                leave(name, place, verbose)
            except Exception as exc:
                print(f"  {name}: the manual exit failed too: {exc}")
        raise
    finally:
        print(f"  RECORDED RUN end {name} ({result})", flush=True)


def count_run(name, day, verbose=True):
    """Counts a run toward its dungeon's daily limit, on the dungeon day read
    before the Challenge click, whether the limit comes from the dungeon or
    only from its recorded entry. With no day (the online clock did not
    answer) it is not counted rather than counted against no day, which
    would wipe the day's counts."""
    if day is None:
        print(f"  {name}: the online clock did not answer before {dungeon.CHALLENGE_WORD}, "
              f"so this run is not counted toward the daily limit")
        return None
    ran = dungeon.count_dungeon(name, day)
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
            trouble = checked(name)
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
        except (KeyboardInterrupt, GameLost):
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
