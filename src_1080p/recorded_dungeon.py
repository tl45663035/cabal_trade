"""Dungeon runs played from a recording.

A profile lists its recorded dungeons under dungeon.recorded in its config:
each names a dungeon from dungeon.dungeons, and adds the recording of a
run (made from just after Challenge to the "Cleared dungeon" banner), the
recording of leaving the dungeon by hand, and a time limit counted from the
Challenge click. The dungeon's own settings supply the rest: the entry, the
key, the difficulty, the daily limit, the start time, the BM cooldown, the
BM cancel before leaving, and the claim-and-leave buttons."""

from pathlib import Path

import dungeon
import record_action as ra
import record_all

SEQUENCES = dungeon.SEQUENCES
OVERRIDES = ("daily_limit", "start_at", "cooldown", "end_wait", "before_exit")


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
