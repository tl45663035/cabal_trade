"""Check real recordings: replay each one on the pretend Windows and game
(fake_input.py) and confirm the game is told exactly what the file holds.

  py recorder/check_recordings.py                today's recordings
  py recorder/check_recordings.py 2026-10-19     recordings from that day
  py recorder/check_recordings.py FILE...        these files

Nothing is sent to the real machine. Per file it reports:
  file:    the file itself (header, order, raw counts, keys or buttons left
           held at the end)
  keys / clicks / wheel:  each reaches the game at its recorded microsecond,
           in order, at its recorded spot
  cursor:  every recorded cursor position, outside camera turns, is where the
           game's cursor is at that moment
  turns:   while the right button is held, the camera turns by exactly the
           recorded raw counts
  compare: a capture of the replay, run through record_all.py compare"""

import contextlib
import datetime
import io
import json
import os
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src_1080p"
os.environ.setdefault("CABAL_CONFIG", "bot")
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
import no_input
TRIPPED = no_input.arm()
import recorder_settings
import record_action as ra
import record_all
from fake_input import FakeInput, patched, replay_session, vk_to_scan

for name in ("SendInput", "SetCursorPos", "SetForegroundWindow", "BringWindowToTop",
             "AttachThreadInput", "ShowWindow"):
    setattr(ra.user32, name, no_input._raiser(name))

W = ra.W
US = 1_000_000


def us(t):
    return int(round(t * US))


def typed(e):
    return "secret" in e or e.get("vk") == record_all.VK_PACKET


def turn_spans(events):
    """Physical right-button holds, in a recording that has raw counts."""
    if not any(e["action"] == "raw" for e in events):
        return []
    spans, began = [], None
    for e in events:
        if e.get("button") in record_all.TURN and e.get("injected") is not True:
            if e["action"] == "down" and began is None:
                began = us(e["t"])
            elif e["action"] == "up" and began is not None:
                spans.append((began, us(e["t"])))
                began = None
    if began is not None:
        spans.append((began, float("inf")))
    return spans


def inside(spans, t, ends=False):
    return any(a < t < b or (ends and t == b) for a, b in spans)


def file_findings(head, events):
    out = []
    ts = [e["t"] for e in events]
    if ts != sorted(ts):
        out.append("events are out of time order")
    for field in ("screen", "desktop", "cursor"):
        if field not in head:
            out.append(f"header has no {field}")
    moves = sum(1 for e in events if e["action"] == "move" and e.get("injected") is False)
    raws = sum(1 for e in events if e["action"] == "raw")
    if raws and raws > moves:
        out.append(f"{raws} raw counts but only {moves} mouse moves")
    held, buttons = {}, {}
    for e in events:
        if e["kind"] == "key" and not typed(e):
            (held.__setitem__(e["vk"], e) if e["action"] == "down"
             else held.pop(e["vk"], None))
        elif e["kind"] == "mouse" and e["action"] in ("down", "up"):
            (buttons.__setitem__(e["button"], e) if e["action"] == "down"
             else buttons.pop(e["button"], None))
    if held:
        out.append("ends with keys held: " + ", ".join(
            f"{e.get('name') or e['vk']} (down at {e['t']:.3f}s)" for e in held.values())
            + "; the replay lets go of them at the end")
    if buttons:
        out.append("ends with buttons held: " + ", ".join(buttons)
                   + "; the replay lets go of them at the end")
    if any(typed(e) for e in events):
        out.append(f"{sum(1 for e in events if typed(e))} typed-text events (older "
                   f"recorder); skipped on replay")
    return out


def expected_and_told(path, head, events):
    left, top, width, height = head["desktop"]
    fake = FakeInput(layout=head["desktop"], primary=head["screen"],
                     cursor=(left + 1, top + 1))
    capture = record_all.Everything(control=False, command=["capture"])
    capture.zero = fake.now()
    capture.path = Path(os.environ.get("TEMP", ".")) / f"capture_{path.stem}.jsonl"
    with patched((record_all, "time", types.SimpleNamespace(
            perf_counter=fake.now, sleep=lambda s: None)),
            (ra, "screen", lambda: list(head["screen"])),
            (ra, "desktop", lambda: list(head["desktop"]))), \
            contextlib.redirect_stdout(io.StringIO()):
        capture.open()
        fake = replay_session(path, "direct", fake=fake, capture=capture,
                              window=head.get("window"))
        capture.finish()
    return fake, capture.path


def check_file(path):
    head, events, refused = record_all.read_recording(path)
    if refused:
        return "skipped", [refused]
    if not events:
        return "skipped", ["header only, no events (a recording stopped at once)"]
    notes = file_findings(head, events)
    fake, capture = expected_and_told(path, head, events)
    if fake.marks.get("refused"):
        return "FAILED", notes + [f"replay refused: {fake.marks['refused']}"]
    zero = fake.marks["zeros"][0]
    log = [(t - zero, kind, data) for t, kind, data in fake.game.log if t >= zero]
    last = us(events[-1]["t"])
    problems = []
    spans = turn_spans(events)

    want_keys = [(us(e["t"]), (e.get("scan") or vk_to_scan(e["vk"]),
                               bool(e.get("extended")), e["action"] == "up"))
                 for e in events if e["kind"] == "key" and not typed(e)]
    got_keys = [(t, d) for t, k, d in log if k == "key"]
    released = got_keys[len(want_keys):]
    if got_keys[:len(want_keys)] != want_keys:
        n = next((i for i, (a, b) in enumerate(zip(want_keys, got_keys)) if a != b),
                 min(len(want_keys), len(got_keys)))
        problems.append(f"keys differ from #{n}: file {want_keys[n:n + 1]}, "
                        f"game {got_keys[n:n + 1]}")
    if any(t != last or not d[2] for t, d in released):
        problems.append(f"extra keys after the file's own: {released[:3]}")

    want_buttons = [(us(e["t"]), (e["button"], e["action"] == "down"), (e["x"], e["y"]))
                    for e in events if e["kind"] == "mouse" and e["action"] in ("down", "up")]
    got_buttons = [(t, d[:2], d[2]) for t, k, d in log if k == "button"]
    if [(t, d) for t, d, _p in got_buttons[:len(want_buttons)]] != \
            [(t, d) for t, d, _p in want_buttons]:
        problems.append("clicks differ in order or time")
    off = [(t, a, b) for (t, d, a), (_t, _d, b) in zip(want_buttons, got_buttons)
           if max(abs(a[0] - b[0]), abs(a[1] - b[1]))
           > (1 if inside(spans, t, ends=True) else 0)]
    if off:
        problems.append(f"{len(off)} click(s) landed elsewhere, first at "
                        f"{off[0][0] / US:.3f}s: file {off[0][1]}, game {off[0][2]}")

    want_wheel = [(us(e["t"]), (e["delta"], e["action"] == "hwheel"))
                  for e in events if e["action"] in ("wheel", "hwheel")]
    got_wheel = [(t, d[:2]) for t, k, d in log if k == "wheel"]
    if want_wheel != got_wheel:
        problems.append(f"wheel turns differ: file {len(want_wheel)}, game {len(got_wheel)}")

    timeline, where, i = [], tuple(head["cursor"]), 0
    moves = [(t, d) for t, k, d in log if k == "move"]
    cursor_off, in_turn_worst, checked = [], 0, 0
    # In a turn the cursor moves with each raw count, which arrives about
    # 0.1ms after its cursor move; look once that raw count is in.
    raw_at = [us(e["t"]) for e in events if e["action"] == "raw"]
    import bisect
    for e in events:
        if e["action"] != "move":
            continue
        t = us(e["t"])
        if inside(spans, t) and e.get("injected") is False:
            k = bisect.bisect_left(raw_at, t)
            if k < len(raw_at):
                t = raw_at[k]
        while i < len(moves) and moves[i][0] <= t:
            where = moves[i][1]
            i += 1
        gap = max(abs(where[0] - e["x"]), abs(where[1] - e["y"]))
        if inside(spans, t) and e.get("injected") is False:
            in_turn_worst = max(in_turn_worst, gap)
            continue
        checked += 1
        if gap:
            cursor_off.append((e["t"], (e["x"], e["y"]), where))
    if cursor_off:
        problems.append(f"{len(cursor_off)} of {checked} cursor positions off, first at "
                        f"{cursor_off[0][0]:.3f}s: file {cursor_off[0][1]}, "
                        f"game {cursor_off[0][2]}")

    want_turns = [(us(e["t"]), (e["dx"], e["dy"])) for e in events
                  if e["action"] == "raw" and inside(spans, us(e["t"]))]
    got_turns = [(t, d) for t, k, d in log if k == "turn"]
    if want_turns != got_turns:
        problems.append(f"camera turns differ: file {len(want_turns)} raw counts, "
                        f"game {len(got_turns)}")
    jumps = [(t, d) for t, k, d in log if k == "jump"]
    if not spans:
        held_right = sum(1 for e in events if e.get("button") == "right" and e["action"] == "down")
        if held_right:
            notes.append(f"made before raw counts were recorded: its {held_right} right "
                         f"drag(s) replay from cursor positions "
                         f"({len(jumps)} cursor jump(s) while turning)")
    elif jumps:
        notes.append(f"{len(jumps)} cursor jump(s) while turning, from moves the mouse "
                     f"itself didn't make")
    if fake.game.keys or fake.game.buttons:
        problems.append(f"left held after the replay: {fake.game.keys} {fake.game.buttons}")

    found = record_all.compare(path, capture)
    if found.get("missing") or found.get("extra"):
        problems.append(f"compare: {found['missing']} missing, {found['extra']} extra")
    if "jitter_ms" in found and (found["jitter_ms"][2] > 0.002 or abs(found["drift_ms"]) > 0.002):
        problems.append(f"compare: timing off by up to {found['jitter_ms'][2]:.3f}ms")
    if found.get("clicks_off"):
        problems.append(f"compare: {len(found['clicks_off'])} click(s) off")
    capture.unlink(missing_ok=True)

    summary = (f"{len(events)} events over {events[-1]['t']:.1f}s: "
               f"{len(want_keys)} key, {len(want_buttons)} click, {len(want_wheel)} wheel, "
               f"{checked} cursor positions checked, {len(want_turns)} raw counts in "
               f"{len(spans)} turn(s)"
               + (f", cursor within {in_turn_worst}px during turns" if spans else ""))
    return ("FAILED" if problems else "ok"), [summary] + problems + notes


def main(args):
    out = ra.OUT
    if not args:
        args = [datetime.date.today().isoformat()]
    paths = []
    for arg in args:
        if Path(arg).exists():
            paths.append(Path(arg))
        else:
            paths += sorted(out.glob(f"{arg}_*.jsonl"))
    if not paths:
        print(f"no recordings for {args} in {out}")
        return 1
    totals = {}
    for path in paths:
        try:
            verdict, lines = check_file(path)
        except Exception as exc:
            verdict, lines = "FAILED", [f"the check crashed: {exc!r}"]
        totals[verdict] = totals.get(verdict, 0) + 1
        print(f"{verdict:7} {path.name}")
        for line in lines:
            print(f"          {line}")
    print(f"\n{len(paths)} recording(s): " + ", ".join(f"{n} {v}" for v, n in totals.items()))
    if TRIPPED:
        print(f"real input was attempted: {TRIPPED}")
        return 2
    return 1 if totals.get("FAILED") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
