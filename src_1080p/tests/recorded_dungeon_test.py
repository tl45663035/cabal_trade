"""Recorded dungeon runs (src_1080p/recorded_dungeon.py): settings and file
lookup, one run (entry, Challenge, the replay, the end), and the rotation
inside the trading run. The game is pretend: fake_input.py takes the
replay's input, and the bot's own steps and screen reads are scripted."""

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC.parent / "tools"))
sys.argv = ["dungeon.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import dungeon
import record_action as ra
import record_all
import recorded_dungeon as rd
from fake_input import FakeInput, Script, patched, record_session

for name in ("SendInput", "SetCursorPos", "SetForegroundWindow", "BringWindowToTop",
             "AttachThreadInput", "ShowWindow"):
    setattr(ra.user32, name, no_input._raiser(name))

checks = 0
failures = []


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


SCRATCH = Path(tempfile.mkdtemp(prefix="cabal_recorded_dungeon_"))
SEQ = SCRATCH / "dungeon"
OUT = SCRATCH / "recordings"
for folder in (SEQ / "fire", SEQ / "ice", OUT):
    folder.mkdir(parents=True)
LAYOUT = (0, 0, 1920, 1080)


def make_recording(path, script, cursor=(960, 540)):
    """A real record_all recording of script, saved as path."""
    with contextlib.redirect_stdout(io.StringIO()), \
            patched((record_all, "game_rect", lambda: None)):
        made, fake = record_session(script, SCRATCH, fake=FakeInput(layout=LAYOUT,
                                                                     cursor=cursor))
    path.write_bytes(made.read_bytes())
    made.unlink()
    return path


def walk(seconds=2.0, step=200):
    s = Script().ctrl_twice("start")
    for _ in range(int(seconds * 1000 / step)):
        s.tap("W", hold=40, gap=step - 40)
    return s.ctrl_twice("stop")


DUNGEONS = {
    "fire": {"locations": {"Fire": {"entry": [185, 401], "name": "Lava Hellfire"}},
             "daily_limit": 30, "start_at": "01:40", "cooldown": 35, "end_wait": 2,
             "before_exit": ["cancel_bm"],
             "exit": [["button", "confirmation", 0.0], ["button", "fire_leave", 0.0]],
             "manual_exit": [["play", "fire", "out"]],
             "after_exit": []},
    "ice": {"locations": {"Ice": {"entry": [10, 10], "name": "Ice"}},
            "daily_limit": 5, "cooldown": 0},
    "arena": {"locations": {"A1": {"entry": [1, 1], "name": "Chaos Arena Lv.1"},
                            "A2": {"entry": [2, 2], "name": "Chaos Arena Lv.2"}}},
}
make_recording(SEQ / "fire" / "run.jsonl", walk())
make_recording(SEQ / "fire" / "out.jsonl", walk(0.6))
make_recording(OUT / "ice_run.jsonl", walk())
make_recording(OUT / "ice_out.jsonl", walk(0.6))
make_recording(SEQ / "fire" / "both.jsonl", walk(0.4))
make_recording(OUT / "both.jsonl", walk(0.4))
elsewhere = SEQ / "fire" / "elsewhere.jsonl"
lines = (SEQ / "fire" / "run.jsonl").read_text(encoding="utf-8").splitlines()
head = json.loads(lines[0])
head["desktop"] = [0, 0, 5360, 1440]
elsewhere.write_text("\n".join([json.dumps(head)] + lines[1:]) + "\n", encoding="utf-8")

RECORDED = {
    "fire": {"enabled": True, "recording": "run.jsonl",
             "timeout_seconds": 30, "cooldown": 10},
    "arena": {"enabled": False, "recording": "run.jsonl",
              "timeout_seconds": 30},
    "ice": {"enabled": True, "recording": "ice_run.jsonl", "timeout_seconds": 30},
}


@contextlib.contextmanager
def world(recorded=None, dungeons=None):
    """The pretend config, folders and screen for one test."""
    with patched((dungeon, "DUNGEONS", dict(dungeons or DUNGEONS)),
                 (dungeon, "_DUNGEON", {**dungeon._DUNGEON,
                                        "recorded": dict(recorded or RECORDED)}),
                 (dungeon, "MANUAL_EXIT", [["play", "fire", "out"]]),
                 (rd, "SEQUENCES", SEQ), (ra, "OUT", OUT),
                 (ra, "screen", lambda: [1920, 1080]),
                 (ra, "desktop", lambda: list(LAYOUT)),
                 (record_all, "game_rect", lambda: None)):
        yield


# ============================== Task 1 ==============================

with world():
    check(rd.enabled() == ["fire", "ice"],
          f"enabled_in_config_order: got {rd.enabled()}")
    fire = rd.settings("fire")
    check(fire["cooldown"] == 10 and fire["exit"] == DUNGEONS["fire"]["exit"]
          and fire["recording"] == "run.jsonl" and fire["start_at"] == "01:40",
          f"settings_overlay: a recorded cooldown beats the dungeon's, the rest "
          f"comes through, got {fire}")
    check(rd.find("fire", "both.jsonl") == SEQ / "fire" / "both.jsonl",
          "find_order: the dungeon folder comes first")
    check(rd.find("ice", "ice_run.jsonl") == OUT / "ice_run.jsonl",
          "find_order: then the recordings folder")
    full = SEQ / "fire" / "run.jsonl"
    check(rd.find("ice", str(full)) == full, "find_order: a full path is itself")
    check(rd.find("fire", "nope.jsonl") is None, "find_order: missing is None")
    check(rd.problem("fire") is None, f"problem: a good entry has none, got "
                                      f"{rd.problem('fire')}")

cases = [
    ("arena", {"arena": {**RECORDED["arena"], "enabled": True}}, "one location"),
    ("fire", {"fire": {**RECORDED["fire"], "recording": "nope.jsonl"}}, "recording"),
    ("fire", {"fire": {**RECORDED["fire"], "manual_exit": [["play", "fire", "nope"]]}},
     "exit"),
    ("fire", {"fire": {k: v for k, v in RECORDED["fire"].items()
                       if k != "timeout_seconds"}}, "timeout"),
    ("fire", {"fire": {**RECORDED["fire"], "recording": "elsewhere.jsonl"}},
     "would not line up"),
]
for name, recorded, word in cases:
    with world(recorded):
        said = rd.problem(name) or ""
    check(word in said, f"problem_reasons: {word!r} expected, got {said!r}")

# ============================== Task 2 ==============================

def taps(name, seconds, step=200):
    s = Script().ctrl_twice("start")
    for _ in range(int(seconds * 1000 / step)):
        s.tap(name, hold=40, gap=step - 40)
    return s.ctrl_twice("stop")


RUN_FILE = make_recording(SEQ / "fire" / "walk.jsonl", taps("W", 1.0))
EXIT_FILE = make_recording(SEQ / "fire" / "leave.jsonl", taps("Esc", 0.4))
RUN_LENGTH = record_all.read_recording(RUN_FILE)[1][-1]["t"]
W_SCAN, ESC_SCAN = 17, 1


class Scene:
    """The bot's own steps (logged, not sent) and the screen as the run goes."""

    def __init__(self, banner_at=None, defeat_at=None, lose_at=None, broken_at=None,
                 during=None, unfocus_at=None, refocus_ok=True, clock=None, timer=None,
                 hp=None):
        self.calls, self.reads, self.t0 = [], 0, None
        self.timer, self.hp, self.hp_reads = timer, hp, 0
        self.banner_at, self.defeat_at = banner_at, defeat_at
        self.lose_at, self.broken_at, self.during = lose_at, broken_at, during or {}
        self.unfocus_at, self.refocus_ok, self.clock = unfocus_at, refocus_ok, clock
        self.unfocus_wall = None

    def lose_focus_in(self, seconds):
        self.unfocus_wall = time.monotonic() + seconds

    def present(self):
        at = self.since()
        return self.lose_at is None or at is None or at < self.lose_at

    def refocus(self):
        self.calls.append("refocus")
        if self.refocus_ok:
            self.unfocus_at, self.unfocus_wall = None, None
        return self.refocus_ok

    def now(self):
        self.calls.append("clock")
        return self.clock

    def since(self):
        return None if self.t0 is None else time.monotonic() - self.t0

    def sequence(self, name, location, phase, verbose=True, steps=None,
                 stop_when_over=True, timer=None):
        steps = tuple(s if isinstance(s, str) else tuple(s) for s in steps)
        self.calls.append(("seq", steps))
        if steps in self.during:
            self.during[steps]()

    def challenge(self, verbose=True, wait=0.0):
        self.calls.append("challenge")
        self.challenge_wait = wait
        self.t0 = time.monotonic()
        return self.t0

    def screen(self):
        self.reads += 1
        at = self.since()
        if self.broken_at is not None and at >= self.broken_at:
            raise RuntimeError("the screen could not be read")
        if self.banner_at is not None and at >= self.banner_at:
            return "cleared"
        if self.defeat_at is not None and at >= self.defeat_at:
            return "defeated"
        return None

    def read_timer(self, image=None):
        """The dungeon timer: timer(seconds since Challenge) gives what it shows
        (timer(None) for a picture read before Challenge)."""
        since = None if image is not None else self.since()
        seconds = None if self.timer is None else self.timer(since)
        return seconds, "" if seconds is None else (
            f"00:{seconds // 60:02d}:{seconds % 60:02d}")

    def health(self):
        """The character's HP as a fraction: hp(seconds since Challenge, how
        many reads so far) gives it (None: unreadable)."""
        self.hp_reads += 1
        return None if self.hp is None else self.hp(self.since(), self.hp_reads)

    def front(self):
        at = self.since()
        if self.unfocus_wall is not None and time.monotonic() >= self.unfocus_wall:
            return False
        if self.unfocus_at is not None and at is not None and at >= self.unfocus_at:
            return False
        return self.present()


def play_run(scene, recorded=None, every=0.05, fake=None, real_count=False, hp_every=0.05):
    fake = fake or FakeInput(layout=LAYOUT, cursor=(3, 3))
    sends = []

    def send(item):
        sends.append((time.monotonic(), item))
        fake.send(item)

    out = io.StringIO()
    entry = dict(RECORDED["fire"], recording="walk.jsonl",
                 manual_exit=[["play", "fire", "leave"]], timeout_seconds=20)
    entry.update(recorded or {})
    counting = ([(dungeon, "count_dungeon",
                  lambda name, day: scene.calls.append(("counted", day)) or 1)]
                if real_count else
                [(rd, "count_run", lambda *a, **k: scene.calls.append("count"))])
    with world({"fire": entry}), \
            patched((dungeon, "_DUNGEON", {**dungeon._DUNGEON, "recorded": {"fire": entry},
                                           "end_check_every": every,
                                           "hp_check_every": hp_every}),
                    (dungeon, "run_sequence", scene.sequence),
                    *counting,
                    (dungeon, "exit_dungeon", lambda *a, **k: scene.calls.append("exit")),
                    (dungeon, "press_buttons",
                     lambda label, *a, **k: scene.calls.append(("buttons", label))),
                    (dungeon, "wait_timer",
                     lambda seconds, verbose=True: scene.calls.append(("wait", seconds))),
                    (dungeon, "off_for_the_day",
                     lambda *a, **k: scene.calls.append("off_for_the_day")),
                    (dungeon, "internet_now", scene.now),
                    (rd, "challenge", scene.challenge),
                    (rd, "screen_says", scene.screen),
                    (rd, "read_timer", scene.read_timer),
                    (rd, "health", scene.health),
                    (rd, "game_in_front", scene.front),
                    (rd, "game_present", scene.present),
                    (dungeon, "focus_game", scene.refocus),
                    (record_all, "focus_game", lambda: True),
                    (ra, "send", send),
                    (record_all, "cursor_now", lambda: tuple(fake.cursor)),
                    (record_all, "set_cursor", fake.set_cursor)), \
            contextlib.redirect_stdout(out):
        try:
            result = rd.run("fire")
        except BaseException as exc:
            result = exc
    keys = [d for _t, k, d in fake.game.log if k == "key"]
    return types.SimpleNamespace(result=result, calls=scene.calls, fake=fake, sends=sends,
                                 said=out.getvalue(), reads=scene.reads, t0=scene.t0,
                                 run_keys=[d for d in keys if d[0] == W_SCAN],
                                 exit_keys=[d for d in keys if d[0] == ESC_SCAN])


ENTRY = ("seq", ("get_entry", "open_dungeon_key"))
BEFORE = ("seq", ("cancel_bm",))

r = play_run(Scene(banner_at=0.5))
check(r.result == "cleared", f"cleared: returns 'cleared', got {r.result!r}")
check(r.calls == [ENTRY, "clock", "challenge", "count", BEFORE, ("wait", 2), "exit"],
      f"cleared: entry, Challenge, then BM cancel, end wait, claim and leave; got {r.calls}")
late = [t for t, _item in r.sends if t > r.t0 + 0.5 + 0.05 + 0.15]
check(not late, f"cleared: no input after the banner, got {len(late)} late sends")
check(not r.fake.game.keys and not r.fake.game.buttons, "cleared: nothing held")
check(r.sends and r.sends[0][1].type == ra.W["INPUT_MOUSE"]
      and r.sends[0][1].u.mi.dwFlags & ra.W["MOUSEEVENTF_ABSOLUTE"],
      "cleared: the only input before the recording's own is the start cursor")
check(not r.exit_keys, "cleared: the exit clip is not played")

r = play_run(Scene(banner_at=RUN_LENGTH + 0.05))
check(r.result == "cleared" and r.calls.count("exit") == 1,
      f"cleared_right_at_the_end: one exit, got {r.result!r} {r.calls}")

r = play_run(Scene(banner_at=RUN_LENGTH + 1.0))
last = max(t for t, _item in r.sends)
check(r.result == "cleared" and last < r.t0 + RUN_LENGTH + 0.3,
      f"replay_ends_first: cleared, with nothing sent while waiting for the banner")
check(len(r.run_keys) == 2 * 5, f"replay_ends_first: the whole recording played, got "
                                f"{len(r.run_keys)} W events")

r = play_run(Scene(), {"timeout_seconds": 0.6})
check(r.result == "timeout", f"timeout: returns 'timeout', got {r.result!r}")
check(r.calls[-1] == BEFORE and "exit" not in r.calls and "off_for_the_day" not in r.calls,
      f"timeout: BM cancel, then the exit clip; no claim, no day off; got {r.calls}")
check(len(r.exit_keys) == 2 * 2, f"timeout: the exit clip plays, got {r.exit_keys}")
check(len(r.run_keys) < 2 * 5, "timeout: the run recording is cut short")
check(not r.fake.game.keys and not r.fake.game.buttons, "timeout: nothing held")

r = play_run(Scene(defeat_at=0.4))
check(r.result == "defeated" and r.calls[-1] == ("buttons", "defeat") and r.exit_keys,
      f"defeated: revive, then the exit clip; got {r.result!r} {r.calls}")

r = play_run(Scene(lose_at=0.4))
check(isinstance(r.result, RuntimeError) and "game" in str(r.result),
      f"lost_game: raises about the game, got {r.result!r}")
check(not r.exit_keys and not r.fake.game.keys, "lost_game: no exit clip, nothing held")

calibration.STOPPED.clear()
threading.Timer(0.4, calibration.STOPPED.set).start()
r = play_run(Scene(banner_at=5))
calibration.STOPPED.clear()
check(isinstance(r.result, KeyboardInterrupt), f"ctrl_stop_mid_replay: stops, got {r.result!r}")
check(not r.exit_keys and not r.fake.game.keys and "exit" not in r.calls,
      "ctrl_stop_mid_replay: nothing held, no exit clip, no claim")

r = play_run(Scene(banner_at=0.3, during={("cancel_bm",): calibration.STOPPED.set}))
calibration.STOPPED.clear()
check(isinstance(r.result, KeyboardInterrupt) and "exit" not in r.calls and not r.exit_keys,
      f"ctrl_stop_after_the_replay: stops before the claim, got {r.result!r} {r.calls}")

r = play_run(Scene(broken_at=0.3))
check(isinstance(r.result, RuntimeError) and "screen" in str(r.result) and r.exit_keys,
      f"raises_after_challenge: the exit clip plays, then it raises; got {r.result!r}")

moved = SCRATCH / "leave_moved.jsonl"
r = play_run(Scene(during={("cancel_bm",): lambda: EXIT_FILE.rename(moved)}),
             {"timeout_seconds": 0.5})
moved.rename(EXIT_FILE)
check(isinstance(r.result, RuntimeError) and "leave.jsonl" in str(r.result),
      f"exit_clip_fails: raises naming the exit recording, got {r.result!r}")

r = play_run(Scene(banner_at=0.5))
check("  RECORDED RUN start fire\n" in r.said and "  RECORDED RUN end fire (cleared)\n" in r.said,
      f"log_lines: start and end lines, got {r.said[-300:]!r}")
r = play_run(Scene(lose_at=0.3))
check("  RECORDED RUN end fire (failed)\n" in r.said, "log_lines: a failed run says so")

r = play_run(Scene(), {"timeout_seconds": 3.0}, every=0.5)
check(5 <= r.reads <= 7, f"checks_at_its_rate: 3s at 0.5s is about 6 reads, got {r.reads}")

clicks = []
with patched((dungeon, "focus_game", lambda: True),
             (dungeon, "wait_for", lambda window, word, timeout: (True, word)),
             (dungeon, "click", lambda x, y, settle: clicks.append((x, y)))), \
        contextlib.redirect_stdout(io.StringIO()):
    at = rd.challenge()
check(clicks == [tuple(dungeon.centre(dungeon.CHALLENGE_WINDOW))] and at > 0,
      f"challenge: only Challenge is clicked (no hotbar tab II), got {clicks}")
with patched((dungeon, "focus_game", lambda: True),
             (dungeon, "wait_for", lambda window, word, timeout: (False, "Loading")),
             (dungeon, "click", lambda x, y, settle: clicks.append((x, y)))):
    try:
        rd.challenge(False)
        said = None
    except RuntimeError as exc:
        said = str(exc)
check(said and "Nothing clicked" in said and len(clicks) == 1,
      f"challenge: no Challenge button, nothing clicked, got {said!r}")

order = []
with patched((dungeon, "focus_game", lambda: True),
             (dungeon, "wait_for",
              lambda window, word, timeout: order.append("button seen") or (True, word)),
             (dungeon, "wait_timer",
              lambda seconds, verbose=True: order.append(("wait", seconds))),
             (dungeon, "click", lambda x, y, settle: order.append("click"))),         contextlib.redirect_stdout(io.StringIO()):
    rd.challenge(wait=3.0)
check(order == ["button seen", ("wait", 3.0), "click"],
      f"challenge_wait: the button shows, then the wait for the dungeon to load, then "
      f"the click; got {order}")
order.clear()
with patched((dungeon, "focus_game", lambda: True),
             (dungeon, "wait_for",
              lambda window, word, timeout: order.append("button seen") or (True, word)),
             (dungeon, "wait_timer",
              lambda seconds, verbose=True: order.append(("wait", seconds))),
             (dungeon, "click", lambda x, y, settle: order.append("click"))),         contextlib.redirect_stdout(io.StringIO()):
    rd.challenge()
check(order == ["button seen", "click"], f"no challenge_wait: no wait, got {order}")

scene = Scene(banner_at=0.5)
play_run(scene, {"challenge_wait": 3})
check(getattr(scene, "challenge_wait", None) == 3.0,
      f"challenge_wait: the run passes the dungeon's own wait to the Challenge click, "
      f"got {getattr(scene, 'challenge_wait', None)!r}")
scene = Scene(banner_at=0.5)
play_run(scene)
check(getattr(scene, "challenge_wait", None) == 0.0,
      f"no challenge_wait: none passed, got {getattr(scene, 'challenge_wait', None)!r}")

r = play_run(Scene(), {"recording": "nope.jsonl"})
check(isinstance(r.result, RuntimeError) and not r.sends and r.calls == [],
      f"a missing recording fails before any input, got {r.result!r} {r.calls}")

# ============================== Task 3 ==============================

import datetime

check(rd.pick({"a", "b", "c"}, ["a", "b", "c"], "a") == "b", "pick_rotates: after a, b")
check(rd.pick({"a", "b", "c"}, ["a", "b", "c"], "c") == "a", "pick_rotates: wraps to a")
check(rd.pick({"a", "c"}, ["a", "b", "c"], "a") == "c", "pick_rotates: skips the closed")
check(rd.pick({"b"}, ["a", "b", "c"], None) == "b", "pick_rotates: no last, first open")
check(rd.pick(set(), ["a", "b"], None) is None, "pick_rotates: nothing open")
check(rd.pick({"a"}, ["a", "b"], "gone") == "a", "pick_rotates: an unknown last starts over")

ROT = {
    "a": {"locations": {"A": {"entry": [1, 1], "name": "A"}}, "daily_limit": 2,
          "start_at": "16:00", "cooldown": 10},
    "b": {"locations": {"B": {"entry": [2, 2], "name": "B"}}, "daily_limit": 3,
          "start_at": "16:00", "cooldown": 0},
}


class Runaway(BaseException):
    """A rotation that never stops; it escapes turn()'s error handling."""


class Rotation:
    """Pretend clock-of-day, run counts and runs for the rotation."""

    def __init__(self, since_reset_h=2.0, outcomes=None):
        self.ran, self.counts, self.waits, self.snaps = [], {}, [], []
        self.state = {}
        self.since = datetime.timedelta(hours=since_reset_h)
        self.outcomes = outcomes or {}

    def run(self, name, verbose=True):
        if len(self.ran) >= 40:
            raise Runaway(f"still running after {self.ran}")
        self.ran.append(name)
        self.counts[name] = self.counts.get(name, 0) + 1
        result = self.outcomes.get(name, "cleared")
        if isinstance(result, list):
            result = result.pop(0) if result else "cleared"
        if isinstance(result, Exception):
            raise result
        return result

    def save(self, state):
        self.state = dict(state)


def rotate(recorded, rot, off=None, rested=None, between=None, dungeons=None):
    off = set() if off is None else off
    rested = {} if rested is None else rested
    out = io.StringIO()
    with world(recorded, dungeons or ROT), \
            patched((rd, "run", rot.run), (rd, "problem", lambda name: None),
                    (dungeon, "since_reset", lambda: rot.since),
                    (dungeon, "runs_today", lambda name: rot.counts.get(name, 0)),
                    (dungeon, "_state", lambda: dict(rot.state)),
                    (dungeon, "_save", rot.save),
                    (dungeon, "wait_timer",
                     lambda seconds, verbose=True: rot.waits.append(seconds)),
                    (calibration, "snap", lambda *a, **k: rot.snaps.append(a))), \
            contextlib.redirect_stdout(out):
        try:
            result = rd.turn(off, rested, between=between)
        except (Runaway, Exception) as exc:
            print(f"the rotation broke: {exc!r}")
            result = (None, None)
    return result, out.getvalue(), off


both = {"a": {"enabled": True}, "b": {"enabled": True}}
rot = Rotation()
(acted, due), said, off = rotate(both, rot)
check(rot.ran == ["a", "b", "a", "b", "b"], f"rotation_runs_by_turns: got {rot.ran}")
check(acted and due is not None and due > time.monotonic() + 3600,
      f"rotation_runs_by_turns: back to trading until the next day, got {acted} {due}")
check(rot.state.get("recorded_last") == "b", "rotation_runs_by_turns: the last is saved")

rot = Rotation()
rot.state = {"recorded_last": "a"}
rotate(both, rot)
check(rot.ran[0] == "b", f"rotation_survives_restart: picks up after a, got {rot.ran}")

later = {"a": {"enabled": True}, "b": {"enabled": True, "start_at": "18:10"}}
rot = Rotation(since_reset_h=3.0)
(acted, due), said, off = rotate(later, rot)
check(rot.ran == ["a", "a"], f"start_times_gate_each_dungeon: only a runs, got {rot.ran}")
check(due is not None and abs(due - time.monotonic() - 600) < 5,
      f"start_times_gate_each_dungeon: b is due in 600s, got "
      f"{None if due is None else round(due - time.monotonic())}s")

rot = Rotation()
rotate(both, rot)
check(len(rot.waits) == 1 and 9 < rot.waits[0] <= 10,
      f"shared_cooldown: a waits its 10s after the run before it, b waits none, got "
      f"{rot.waits}")

rot = Rotation(outcomes={"a": "timeout"})
rotate(both, rot)
check(rot.ran.count("a") == 2 and rot.ran.count("b") == 3,
      f"timeout_counts_and_continues: timeouts still count and the loop goes on, got "
      f"{rot.ran}")

rot = Rotation(outcomes={"a": RuntimeError("the screen could not be read")})
(acted, due), said, off = rotate(both, rot)
check(rot.ran == ["a", "b", "b", "b"] and off == {"a"} and rot.snaps,
      f"failure_turns_one_off: a is off for the trading run, b carries on, got "
      f"{rot.ran} {off}")
check("the screen could not be read" in said, "failure_turns_one_off: the reason is logged")

rot = Rotation()
out = io.StringIO()
with world(both, ROT), patched((rd, "run", rot.run),
                               (rd, "problem", lambda name: f"{name} is broken"),
                               (dungeon, "since_reset", lambda: rot.since)), \
        contextlib.redirect_stdout(out):
    first = rd.turn(set(), {})
    off = set(ROT)
    second = rd.turn(off, {})
check(first == (False, None) and not rot.ran and out.getvalue().count("a is broken") == 1,
      f"all_refused_goes_back_to_trading: no runs, each reason once, got {first} "
      f"{out.getvalue()!r}")
check(second == (False, None), "all_refused_goes_back_to_trading: and stays quiet after")

LONG = {"a": {**ROT["a"], "daily_limit": 10, "cooldown": 0}, "b": ROT["b"]}
rot = Rotation(outcomes={"a": "low_hp"})
(acted, due), said, off = rotate(both, rot, dungeons=LONG)
check(rot.ran.count("a") == 3 and "a" in off and rot.ran.count("b") == 3,
      f"fail_streak_turns_one_off: 3 runs in a row that don't clear turn a off, b "
      f"carries on, got {rot.ran} {off}")
check("3 runs in a row" in said, f"fail_streak_turns_one_off: the log says why, got {said!r}")

rot = Rotation(outcomes={"a": ["timeout", "low_hp", "cleared", "timeout", "defeated",
                               "cleared", "timeout", "cleared", "cleared", "cleared"]})
(acted, due), said, off = rotate(both, rot, dungeons=LONG)
check(rot.ran.count("a") == 10 and "a" not in off,
      f"fail_streak_resets_on_a_clear: a clear in between keeps a on, got {rot.ran} {off}")

rested = {}
rot = Rotation(outcomes={"a": ["timeout", "timeout"]})
rotate({"a": {"enabled": True, "daily_limit": 2}}, rot, rested=rested, dungeons=LONG)
rot.counts = {}
rot.outcomes = {"a": ["timeout"]}
(acted, due), said, off = rotate({"a": {"enabled": True, "daily_limit": 3}}, rot,
                                 rested=rested, dungeons=LONG)
check(rot.ran == ["a", "a", "a"] and "a" in off,
      f"fail_streak_carries_between_turns: the streak is kept in rested, got {rot.ran} "
      f"{off}")



calls = []
rot = Rotation()
rotate(both, rot, between=lambda: calls.append(len(rot.ran)))
check(calls == [1, 2, 3, 4], f"between runs (world boss), not before the first, got {calls}")

ran = {"n": 0}
with world({"b": {"enabled": True, "daily_limit": 1}},
           {"b": {"locations": {"B": {"entry": [2, 2], "name": "B"}}}}), \
        patched((dungeon, "_state", lambda: {}), (dungeon, "_save", lambda s: None),
                (dungeon, "count_dungeon",
                 lambda name, day: ran.__setitem__("n", ran["n"] + 1) or ran["n"]),
                (dungeon, "internet_now", lambda: None)), \
        contextlib.redirect_stdout(io.StringIO()):
    rd.count_run("b", "2026-10-19")
check(ran["n"] == 1, "a run counts toward a daily limit set only on the recorded entry")

# The driver hands its dungeon turn to the rotation when a recorded dungeon is on.
os.environ.setdefault("CABAL_SALES_DB", str(SCRATCH / "sales.db"))
import driver
picked = []
with patched((driver, "dungeon_on", lambda: True),
             (driver, "recorded_turn", lambda verbose=True: picked.append("recorded") or True),
             (driver, "back_to_back_turn", lambda *a, **k: picked.append("b2b") or True),
             (rd, "enabled", lambda: ["fire"])):
    driver.dungeon_turn()
with patched((driver, "dungeon_on", lambda: True),
             (driver, "recorded_turn", lambda verbose=True: picked.append("recorded") or True),
             (driver, "back_to_back_turn", lambda *a, **k: picked.append("b2b") or True),
             (rd, "enabled", lambda: []),
             (dungeon, "DUNGEONS", {**dungeon.DUNGEONS,
                                    dungeon.TRADING_DUNGEON: {"back_to_back": True}})):
    driver.dungeon_turn()
check(picked == ["recorded", "b2b"],
      f"driver_picks_recorded: recorded when on, today's path when not, got {picked}")

state = {"due": None, "off": False}
with patched((driver, "_DUNGEON", dict(driver._DUNGEON)),
             (rd, "turn", lambda off, rested, verbose=True, between=None:
              (True, time.monotonic() + 600))), \
        contextlib.redirect_stdout(io.StringIO()):
    acted = driver.recorded_turn()
    due = driver._DUNGEON["due"]
check(acted is True and due is not None and abs(due - time.monotonic() - 600) < 5,
      f"recorded_turn: the next check is due when the rotation says, got {acted} {due}")

# ======================= fixes from the final review =======================

r = play_run(Scene(unfocus_at=0.4))
check(r.result == "lost_focus" and "refocus" in r.calls and r.calls[-1] == BEFORE
      and r.exit_keys and not r.fake.game.keys,
      f"lost focus, window still there: the game is brought back, BM cancel, then the "
      f"exit clip, and the run ends; got {r.result!r} {r.calls}")
r = play_run(Scene(unfocus_at=0.4, refocus_ok=False))
check(isinstance(r.result, rd.GameLost) and not r.exit_keys,
      f"lost focus and the game won't come back: raises GameLost, no exit clip, got "
      f"{r.result!r}")
r = play_run(Scene(lose_at=0.4))
check(isinstance(r.result, rd.GameLost) and not r.exit_keys,
      f"the game window gone: raises GameLost, no exit clip, got {r.result!r}")

scene = Scene()
scene.during = {("cancel_bm",): lambda: scene.lose_focus_in(0.15)}
r = play_run(scene, {"timeout_seconds": 0.5})
check(isinstance(r.result, rd.GameLost) and len(r.exit_keys) < 2 * 2
      and not r.fake.game.keys,
      f"the exit clip stops the moment the game loses focus, got {r.result!r} "
      f"{len(r.exit_keys)} Esc events")


def after_exit_breaks():
    raise RuntimeError("could not bring the game to the foreground.")


r = play_run(Scene(banner_at=0.3, during={("after",): after_exit_breaks}),
             {"after_exit": ["after"]})
check(isinstance(r.result, RuntimeError) and not r.exit_keys,
      f"after leaving, a failure doesn't play the exit clip outside, got {r.result!r} "
      f"{len(r.exit_keys)}")

moved = SCRATCH / "leave_moved2.jsonl"
r = play_run(Scene(during={("cancel_bm",): lambda: EXIT_FILE.rename(moved)}),
             {"timeout_seconds": 0.5})
moved.rename(EXIT_FILE)
check(isinstance(r.result, RuntimeError) and "the exit recording failed too" not in r.said,
      f"a failed exit clip isn't tried a second time, got {r.said[-200:]!r}")

r = play_run(Scene(banner_at=0.6), {"timeout_seconds": 0.6}, every=0.25)
check(r.result == "cleared", f"a banner up at the deadline is a clear, not a timeout, got "
                             f"{r.result!r}")

r = play_run(Scene(banner_at=0.4, clock=datetime.datetime(2026, 10, 19, 20, 0,
                                                            tzinfo=datetime.timezone.utc)),
             real_count=True)
check("clock" in r.calls and r.calls.index("clock") < r.calls.index("challenge")
      and any(isinstance(c, tuple) and c[0] == "counted" and c[1] is not None
              for c in r.calls),
      f"the day is read before Challenge and the run counted with it, got {r.calls}")
r = play_run(Scene(banner_at=0.4, clock=None), real_count=True)
check(not any(isinstance(c, tuple) and c[0] == "counted" for c in r.calls)
      and "not counted" in r.said and r.result == "cleared",
      f"no clock: the run plays but isn't counted (never a count with no day), got "
      f"{r.calls} {r.said[-200:]!r}")

utf16 = SEQ / "fire" / "utf16.jsonl"
utf16.write_bytes(RUN_FILE.read_text(encoding="utf-8").encode("utf-16"))
with world({"fire": {**RECORDED["fire"], "recording": "utf16.jsonl"}}):
    try:
        said = rd.problem("fire") or ""
    except Exception as exc:
        said = f"raised {exc!r}"
check("utf16.jsonl" in said and not said.startswith("raised"),
      f"a file that can't be read is a problem, not a crash, got {said!r}")


def broken_check(name):
    if name == "a":
        raise UnicodeDecodeError("utf-8", b"", 0, 1, "bad")
    return None


rot = Rotation()
with world({"a": {"enabled": True}, "b": {"enabled": True}}, ROT), \
        patched((rd, "run", rot.run), (rd, "problem", broken_check),
                (dungeon, "since_reset", lambda: rot.since),
                (dungeon, "runs_today", lambda name: rot.counts.get(name, 0)),
                (dungeon, "_state", lambda: dict(rot.state)), (dungeon, "_save", rot.save),
                (dungeon, "wait_timer", lambda seconds, verbose=True: None)), \
        contextlib.redirect_stdout(io.StringIO()):
    try:
        outcome = rd.turn(set(), {})
    except Exception as exc:
        outcome = exc
check(not isinstance(outcome, Exception) and rot.ran == ["b", "b", "b"],
      f"a check that raises turns that dungeon off; the rotation goes on, got {outcome!r} "
      f"{rot.ran}")

rot = Rotation(outcomes={"a": rd.GameLost("the game window is gone")}
               if hasattr(rd, "GameLost") else {})
(result, said, off) = rotate({"a": {"enabled": True}, "b": {"enabled": True}}, rot)
check(rot.ran == ["a"] and result == (None, None) and "GameLost" in said,
      f"a lost game ends the rotation (no next dungeon), got {rot.ran} {result}")

with world({"fire": dict(RECORDED["fire"])},
           {"fire": {k: v for k, v in DUNGEONS["fire"].items() if k != "daily_limit"}}):
    said = rd.problem("fire")
check(said is None, f"no daily_limit is fine: the game's entries-left count says when "
                    f"a dungeon is done, got {said!r}")

# ===== a long dungeon name cut short at the edge of the list's read =====

IC = "Illusion Castle Radiant Hall(Apocrypha)"
for row, want in [("Illusion Castle Radiant Hall(Apocr", True),
                  ("Illusion Castle Radiant Hall(Apocrypha)(26/30)[New", True),
                  ("Illusion Castle Radiant Hall (Apoc)", True),
                  ("Illusion Castle Radiant Hall(30/30)[New Entry]", False),
                  ("Illusion Castle Radiant Hall(Ap", False),
                  ("Illusion Castle Underworld(Apocr", False),
                  ("Illusion Castle Radiant Hall(Apocx", False)]:
    check(dungeon.names(row, IC) is want,
          f"names: {row!r} {'is' if want else 'is not'} {IC!r}")
for row, name, want in [("Steamer Crazy (Awakened)(30/30)L", "Steamer Crazy (Awakened)", True),
                        ("Steamer Crazy (Normal)(10/10)", "Steamer Crazy (Awakened)", False),
                        ("Steamer Crazy (Aw", "Steamer Crazy (Awakened)", False),
                        ("Edge of Phantom(8/30)", "Edge of Phantom", True),
                        ("Edge of Phan", "Edge of Phantom", True),
                        ("Edge of Phantoms", "Edge of Phantom", False)]:
    check(dungeon.names(row, name) is want,
          f"names: {row!r} {'is' if want else 'is not'} {name!r}")

# ======== the game's own count of entries left decides the day ========

for row, want in [("Panic Cave (Awakened)(15/30)[New", (15, 30)),
                  ("Hazardous Valley (Awakened)(29/3)", (29, 3)),
                  ("Steamer Crazy (Awakened)(0/30)L", (0, 30)),
                  ("Edge of Phantom(8/30)[New Entry]", (8, 30)),
                  ("Panic Cave (Awakened)( 7 / 30)", (7, 30)),
                  ("(Elite)Panic Cave (Premium)(5/", (5, None)),
                  ("Panic Cave (Awakened)", None), ("", None)]:
    got = dungeon.entries_left(row)
    check(got == want, f"entries_left: {row!r} is {want}, got {got!r}")

ROW = 3


def open_key(rows_seen):
    """open_dungeon_key with the list already up; each read shows the next
    rows (the dungeon's row is ROW). Selecting the row stops it."""
    class Selected(Exception):
        pass
    seen, pressed = list(rows_seen), []

    def find(name, timeout):
        rows = seen.pop(0) if len(seen) > 1 else seen[0]
        return dungeon.LEVEL_WINDOWS[ROW], rows

    def selected(*a, **k):
        raise Selected()

    with patched((dungeon, "_state", lambda: {"entry": {"tab": 3, "slot": [1, 1],
                                                         "point": [1, 1]}}),
                 (dungeon, "focus_game", lambda: True),
                 (dungeon, "find_in_list", find),
                 (dungeon, "press_button", lambda key, *a, **k: pressed.append(key)),
                 (dungeon, "click", selected),
                 (dungeon.ctypes.windll.user32, "SetCursorPos", lambda *a: 1)),             contextlib.redirect_stdout(io.StringIO()) as out:
        try:
            dungeon.open_dungeon_key("fire", "Fire")
            result = "went on"
        except Selected:
            result = "selected"
        except BaseException as exc:
            result = exc
    return result, pressed, out.getvalue()


def rows_with(text):
    rows = [""] * len(dungeon.LEVEL_WINDOWS)
    rows[ROW] = text
    return rows


FIRE = {**dungeon.DUNGEONS, "fire": {**dungeon.DUNGEONS["fire"],
                                     "locations": {"Fire": {"entry": [1, 1],
                                                            "name": "Lava Hellfire"}}}}
with patched((dungeon, "DUNGEONS", FIRE)):
    result, pressed, said = open_key([rows_with("Lava Hellfire(0/30)[New Entry]")])
    check(isinstance(result, dungeon.NoEntriesLeft) and pressed == ["list_close"],
          f"no_entries_left: 0 left closes the list, selects nothing, got {result!r} "
          f"{pressed}")
    result, pressed, said = open_key([rows_with("Lava Hellfire(0/30)[New Entry]"),
                                      rows_with("Lava Hellfire(10/30)[New Entry]")])
    check(result == "selected" and pressed == [],
          f"no_entries_left: a 0 the second read doesn't agree with is not taken, got "
          f"{result!r} {pressed}")
    result, pressed, said = open_key([rows_with("Lava Hellfire(12/30)[New Entry]")])
    check(result == "selected" and "12 of 30 entries left" in said,
          f"entries left: some left goes on and says how many, got {result!r} {said!r}")
    result, pressed, said = open_key([rows_with("Lava Hellfire[New Entry]")])
    check(result == "selected", f"an unreadable count goes on, got {result!r}")

TODAY = "2026-10-21"
store = {}
with patched((dungeon, "_state", lambda: dict(store)),
             (dungeon, "_save", lambda state: store.update(state)),
             (dungeon, "server_day", lambda: TODAY)):
    r = play_run(Scene(banner_at=30, during={("get_entry", "open_dungeon_key"): lambda: (
        _ for _ in ()).throw(dungeon.NoEntriesLeft("Lava Hellfire has 0 of 30 left"))}))
check(r.result == "no_entries_left" and "challenge" not in r.calls and not r.run_keys
      and not r.exit_keys,
      f"no_entries_left run: nothing more is done, got {r.result!r} {r.calls}")
check(store.get("recorded_spent", {}).get("fire") == TODAY,
      f"no_entries_left run: fire is done for the dungeon day, got {store}")


class SpendingRotation(Rotation):
    """Each dungeon has `left` entries; at 0 a run reports no entries left and
    marks the dungeon done for the day, as run() does."""

    def __init__(self, left, **kw):
        super().__init__(**kw)
        self.left = dict(left)

    def run(self, name, verbose=True):
        if self.left.get(name, 0) <= 0:
            if len(self.ran) >= 40:
                raise Runaway(f"still running after {self.ran}")
            self.ran.append(name)
            spent = dict(self.state.get("recorded_spent", {}), **{name: TODAY})
            self.state = dict(self.state, recorded_spent=spent)
            return "no_entries_left"
        self.left[name] -= 1
        return super().run(name, verbose)


NOLIMIT = {"a": {**ROT["a"], "cooldown": 0}, "b": {**ROT["b"]}}
for d in NOLIMIT.values():
    d.pop("daily_limit")
rot = SpendingRotation({"a": 2, "b": 4})
with patched((dungeon, "server_day", lambda: TODAY)):
    (acted, due), said, off = rotate(both, rot, dungeons=NOLIMIT)
check(rot.ran == ["a", "b", "a", "b", "a", "b", "b", "b"] and not off,
      f"the game's count ends each dungeon's day (no daily_limit), got {rot.ran} {off}")
check(due is not None and due > time.monotonic() + 3600,
      f"and then back to trading until the next day, got {due}")
check("didn't clear" not in said, "no entries left is not a failed run")

rot = SpendingRotation({"a": 5, "b": 5})
rot.state = {"recorded_spent": {"a": TODAY}}
with patched((dungeon, "server_day", lambda: TODAY)):
    rotate(both, rot, dungeons=NOLIMIT)
check("a" not in rot.ran[:5], f"a dungeon done today is skipped, got {rot.ran}")
rot = SpendingRotation({"a": 1, "b": 0})
rot.state = {"recorded_spent": {"a": "2026-10-20"}}
with patched((dungeon, "server_day", lambda: TODAY)):
    rotate(both, rot, dungeons=NOLIMIT)
check(rot.ran[0] == "a", f"done on an earlier day is open again, got {rot.ran}")

turned, slept = [], []
done = {"a": False}
answers = [(True, time.monotonic() + 60.0), (True, time.monotonic() + 86000.0)]


def fake_turn(off, rested, verbose=True, between=None):
    turned.append(set(off))
    if len(turned) == 2:
        done["a"] = True
    return answers[len(turned) - 1]


out = io.StringIO()
with patched((rd, "turn", fake_turn), (rd, "enabled", lambda: ["a"]),
             (rd, "done_today", lambda name: done[name]),
             (dungeon, "wait_timer", lambda seconds, verbose=True: slept.append(seconds))),         contextlib.redirect_stdout(out):
    dungeon.command(["recorded", "all"])
check(len(turned) == 2 and len(slept) == 1 and 55 < slept[0] <= 60,
      f"recorded_all: waits for a dungeon that opens later, then goes on until every "
      f"enabled one is done for the day; got turns {turned}, waits {slept}")
check("done for the day" in out.getvalue(),
      f"recorded_all: says how it ended, got {out.getvalue()!r}")

turned.clear()
with patched((rd, "turn", lambda *a, **k: turned.append(1) or (False, None)),
             (rd, "enabled", lambda: ["a"]), (rd, "done_today", lambda name: False)),         contextlib.redirect_stdout(io.StringIO()):
    dungeon.command(["recorded", "all"])
check(turned == [1], f"recorded_all: no clock answer (no next time) ends it, got {turned}")

turned.clear()
out = io.StringIO()
with patched((rd, "turn", lambda off, rested, verbose=True, between=None:
              turned.append(set(off)) or (True, time.monotonic() + 86000.0)),
             (rd, "enabled", lambda: ["a", "b"]), (rd, "done_today", lambda name: True)),         contextlib.redirect_stdout(out):
    dungeon.command([])
check(turned == [set()] and "done for the day" in out.getvalue(),
      f"no command: runs the profile's enabled recorded dungeons in turns, as recorded "
      f"all does, got {turned} {out.getvalue()!r}")

with patched((rd, "enabled", lambda: [])), contextlib.redirect_stdout(io.StringIO()):
    try:
        dungeon.command([])
        said = ""
    except SystemExit as exc:
        said = str(exc)
check("no recorded dungeon is enabled" in said,
      f"no command and nothing enabled: says so instead of running, got {said!r}")

with world({"a": {"enabled": True}, "b": {"enabled": True, "daily_limit": 2}}, ROT),         patched((dungeon, "_state", lambda: {"recorded_spent": {"a": TODAY}}),
                (dungeon, "server_day", lambda: TODAY),
                (dungeon, "runs_today", lambda name: 2)):
    check(rd.done_today("a") and rd.done_today("b"),
          "done_today: no entries left, or the bot's own count at a daily_limit")
with world({"a": {"enabled": True}}, NOLIMIT),         patched((dungeon, "_state", lambda: {}), (dungeon, "server_day", lambda: TODAY),
                (dungeon, "runs_today", lambda name: 50)):
    check(not rd.done_today("a"), "done_today: without a daily_limit, only the game's "
                                  "count ends the day")

no_entry = {**DUNGEONS, "fire": {**DUNGEONS["fire"],
                                 "locations": {"Fire": {"name": "Lava Hellfire"}}}}
with world(None, no_entry):
    said = rd.problem("fire") or ""
check("entry" in said, f"a dungeon whose entry isn't known yet is refused, got {said!r}")

# ============ lining the replay up with the Challenge click ============

CW = dungeon.CHALLENGE_WINDOW
cx, cy = (CW[0] + CW[2]) // 2, (CW[1] + CW[3]) // 2
with_click = [
    {"t": 0.5, "kind": "mouse", "action": "move", "x": 100, "y": 100, "injected": False},
    {"t": 1.0, "kind": "key", "action": "down", "vk": 87, "scan": 17, "extended": False,
     "name": "W", "repeat": False, "injected": False},
    {"t": 1.1, "kind": "key", "action": "up", "vk": 87, "scan": 17, "extended": False,
     "name": "W", "repeat": False, "injected": False},
    {"t": 2.0, "kind": "mouse", "action": "down", "button": "left", "x": cx, "y": cy,
     "injected": False},
    {"t": 2.1, "kind": "mouse", "action": "up", "button": "left", "x": cx, "y": cy,
     "injected": False},
    {"t": 2.6, "kind": "key", "action": "down", "vk": 49, "scan": 2, "extended": False,
     "name": "1", "repeat": False, "injected": False},
    {"t": 2.7, "kind": "key", "action": "up", "vk": 49, "scan": 2, "extended": False,
     "name": "1", "repeat": False, "injected": False},
]
events, cursor, at = rd.align(with_click, [5, 5])
check(at == 2.0 and cursor == [cx, cy],
      f"align: finds the Challenge click at 2.0s and starts the cursor on it, got {at} {cursor}")
check([(round(e["t"], 6), e.get("name") or e.get("button"), e["action"]) for e in events]
      == [(0.6, "1", "down"), (0.7, "1", "up")],
      f"align: drops everything up to and including the click; times count from the "
      f"click's press, got {events}")
same_events, same_cursor, none_at = rd.align(with_click[:3], [5, 5])
check(none_at is None and same_events == with_click[:3] and same_cursor == [5, 5],
      "align: a recording that starts after Challenge is used as it is")
outside = [dict(e, x=10, y=10) if e.get("button") else e for e in with_click]
check(rd.align(outside, [5, 5])[2] is None,
      "align: a left click elsewhere is not taken for Challenge")
right = [dict(e, button="right") if e.get("button") else e for e in with_click]
check(rd.align(right, [5, 5])[2] is None, "align: a right click on the button doesn't count")
ground_first = ([{"t": 0.3, "kind": "mouse", "action": "down", "button": "left", "x": 200,
                  "y": 300, "injected": False},
                 {"t": 0.35, "kind": "mouse", "action": "up", "button": "left", "x": 200,
                  "y": 300, "injected": False}] + with_click)
check(rd.align(ground_first, [5, 5])[2] is None,
      "align: only the recording's first left click can be Challenge; a later click that "
      "lands on the button's spot (a ground click mid-run) is never taken for it")

click_file = SEQ / "fire" / "with_challenge.jsonl"
walk_events = record_all.read_recording(RUN_FILE)
head = dict(walk_events[0])
shifted = [dict(e, t=e["t"] + 3.0) for e in walk_events[1]]
lead = [{"t": 0.2, "kind": "key", "action": "down", "vk": 69, "scan": 18, "extended": False,
         "name": "E", "repeat": False, "injected": False},
        {"t": 0.3, "kind": "key", "action": "up", "vk": 69, "scan": 18, "extended": False,
         "name": "E", "repeat": False, "injected": False},
        {"t": 2.9, "kind": "mouse", "action": "down", "button": "left", "x": cx, "y": cy,
         "injected": False},
        {"t": 2.95, "kind": "mouse", "action": "up", "button": "left", "x": cx, "y": cy,
         "injected": False}]
click_file.write_text("\n".join(json.dumps(x) for x in [head] + lead + shifted) + "\n",
                      encoding="utf-8")
r = play_run(Scene(banner_at=RUN_LENGTH + 0.5), {"recording": "with_challenge.jsonl"})
first_w = min((t for t, item in r.sends if item.type == ra.W["INPUT_KEYBOARD"]
               and item.u.ki.wScan == W_SCAN), default=float("inf"))
want = r.t0 + 3.0 + walk_events[1][0]["t"] - 2.9
check(r.result == "cleared" and not any(d[0] == 18 for d in r.run_keys + [
          d for _t, k, d in r.fake.game.log if k == "key"]),
      f"a run recorded from before Challenge: what came before the click is not played, "
      f"got {r.result!r}")
check(first_w != float("inf") and abs(first_w - want) < 0.05,
      f"and the rest lines up with the bot's own Challenge click: first W "
      f"{first_w - r.t0:.3f}s after it, wanted {want - r.t0:.3f}s")
check("lined up with the Challenge click" in r.said, "the log says it lined up")

r = play_run(Scene(banner_at=RUN_LENGTH + 1.5), {"start_delay": 0.6})
first_w = min((t for t, item in r.sends if item.type == ra.W["INPUT_KEYBOARD"]
               and item.u.ki.wScan == W_SCAN), default=float("inf"))
want = r.t0 + walk_events[1][0]["t"] + 0.6
check(r.result == "cleared" and first_w != float("inf") and abs(first_w - want) < 0.05,
      f"start_delay: a recording begun after Challenge starts that long after the bot's "
      f"click, got {first_w - r.t0:.3f}s, wanted {want - r.t0:.3f}s")
check("0.6s after the Challenge click" in r.said, "the log says how long it waited")
r = play_run(Scene(banner_at=RUN_LENGTH + 0.5),
             {"recording": "with_challenge.jsonl", "start_delay": 5.0})
first_w = min((t for t, item in r.sends if item.type == ra.W["INPUT_KEYBOARD"]
               and item.u.ki.wScan == W_SCAN), default=float("inf"))
check(abs(first_w - (r.t0 + 3.0 + walk_events[1][0]["t"] - 2.9)) < 0.05,
      "a recording with its own Challenge click ignores start_delay")

# ============ starting the replay from the dungeon's timer ============

for text, want in [("00:04:58", 298), ("00:01.29", 89), ("00.09.59", 599),
                   ("0 0:04:58", 298), ("00:00:00", 0), ("", None), ("ny", None),
                   ("00:04", None)]:
    check(rd.timer_seconds(text) == want,
          f"timer_seconds: {text!r} is {want}, got {rd.timer_seconds(text)!r}")


def timer_wait(readings, target=298, timeout=5.0):
    """until_timer on a pretend clock: each read takes 0.1s and shows the next
    reading (None for nothing readable)."""
    clock = {"t": 0.0}
    left = list(readings)

    def read():
        clock["t"] += 0.1
        seconds = left.pop(0) if left else None
        return seconds, "" if seconds is None else str(seconds)

    def sleep(seconds):
        clock["t"] += seconds

    return rd.until_timer(target, timeout, read, clock=lambda: clock["t"], sleep=sleep,
                          every=0.0)


start, seen = timer_wait([None, 0, 300, 299, 299, 298, 298])
check(seen == 298 and start is not None and abs(start - (0.45 + 0.5)) < 1e-6,
      f"until_timer: the tick into the start value is between the last read above it "
      f"(0.4s) and the first at it (0.5s); the replay starts half a second after, "
      f"got {start!r} {seen!r}")
start, seen = timer_wait([0, 0, 0, 300, 298, 298], target=298)
check(seen == 298 and abs(start - (0.35 + 0.5)) < 1e-6,
      f"until_timer: 00:00:00 while loading is not the timer, got {start!r} {seen!r}")
start, seen = timer_wait([290, 290, 289])
check(seen == 290 and abs(start - 0.0) < 1e-6,
      f"until_timer: a timer already past the start value starts the replay at once "
      f"(from the first read), got {start!r} {seen!r}")
start, seen = timer_wait([300, 299, 28, 299, 298, 298])
check(seen == 298 and abs(start - (0.35 + 0.5)) < 1e-6,
      f"until_timer: one misread below the start value is not taken, got "
      f"{start!r} {seen!r}")
start, seen = timer_wait([None] * 100, timeout=1.0)
check(start is None, f"until_timer: no timer within the timeout is None, got {start!r}")

with world({"fire": {**RECORDED["fire"], "start_timer": "nonsense"}}):
    said = rd.problem("fire") or ""
check("start_timer" in said, f"problem: an unreadable start_timer is one, got {said!r}")


def counting_down(starts_at, shows):
    """A timer that reads 00:00:00 until starts_at seconds after Challenge,
    then shows `shows` and counts down a second at a time."""
    def timer(since):
        if since is None or since < starts_at:
            return 0
        return shows - int(since - starts_at)
    return timer


r = play_run(Scene(banner_at=RUN_LENGTH + 2.5, timer=counting_down(0.2, 300)),
             {"start_timer": "00:04:59", "start_delay": 5.0})
first_w = min((t for t, item in r.sends if item.type == ra.W["INPUT_KEYBOARD"]
               and item.u.ki.wScan == W_SCAN), default=float("inf"))
want = r.t0 + 1.2 + 0.5 + walk_events[1][0]["t"]
check(r.result == "cleared" and abs(first_w - want) < 0.12,
      f"start_timer: the replay starts half a second after the timer ticks to the "
      f"recording's start value (start_delay is not used), first W "
      f"{first_w - r.t0:.3f}s after Challenge, wanted {want - r.t0:.3f}s; {r.result!r}")
check("00:04:59" in r.said, f"the log says it waited for the timer, got {r.said!r}")

r = play_run(Scene(banner_at=RUN_LENGTH + 1.0, timer=counting_down(0.0, 290)),
             {"start_timer": "00:04:58"})
first_w = min((t for t, item in r.sends if item.type == ra.W["INPUT_KEYBOARD"]
               and item.u.ki.wScan == W_SCAN), default=float("inf"))
check(r.result == "cleared" and first_w - r.t0 < walk_events[1][0]["t"] + 0.15,
      f"start_timer_late: a timer already past the start value starts the replay at "
      f"once, first W {first_w - r.t0:.3f}s after Challenge; {r.result!r}")
check("late" in r.said, f"start_timer_late: the log says it is late, got {r.said!r}")

r = play_run(Scene(banner_at=30, timer=lambda since: None),
             {"start_timer": "00:04:58", "timer_timeout": 0.4})
check(r.result == "no_timer", f"no_timer: returns 'no_timer', got {r.result!r}")
check(r.calls[-1] == BEFORE and "exit" not in r.calls and not r.run_keys
      and len(r.exit_keys) == 2 * 2,
      f"no_timer: no replay; BM cancel, then the manual exit; got {r.calls} "
      f"{len(r.run_keys)} run keys")

calibration.STOPPED.clear()
threading.Timer(0.3, calibration.STOPPED.set).start()
r = play_run(Scene(banner_at=30, timer=lambda since: 0), {"start_timer": "00:04:58"})
calibration.STOPPED.clear()
check(isinstance(r.result, KeyboardInterrupt) and not r.run_keys,
      f"ctrl_stop_waiting_for_timer: stops before any replay, got {r.result!r}")

pictured = SEQ / "fire" / "pictured.jsonl"
picture_head = dict(walk_events[0], start_shot="pictured_start.png")
pictured.write_text("\n".join(json.dumps(x) for x in [picture_head] + walk_events[1])
                    + "\n", encoding="utf-8")
from PIL import Image as _Image
_Image.new("RGB", (1920, 1080)).save(SEQ / "fire" / "pictured_start.png")
r = play_run(Scene(banner_at=RUN_LENGTH + 0.5,
                   timer=lambda since: 298 if since is None else 0),
             {"recording": "pictured.jsonl"})
first_w = min((t for t, item in r.sends if item.type == ra.W["INPUT_KEYBOARD"]
               and item.u.ki.wScan == W_SCAN), default=float("inf"))
check(r.result == "cleared" and abs(first_w - (r.t0 + walk_events[1][0]["t"])) < 0.05,
      f"no start_timer: the replay starts right at the Challenge click, even with a "
      f"start picture showing a timer; got {r.result!r}, first W "
      f"{first_w - r.t0:.3f}s after Challenge")

r = play_run(Scene(banner_at=RUN_LENGTH + 0.5, timer=lambda since: 0),
             {"recording": "with_challenge.jsonl", "start_timer": "00:04:58"})
first_w = min((t for t, item in r.sends if item.type == ra.W["INPUT_KEYBOARD"]
               and item.u.ki.wScan == W_SCAN), default=float("inf"))
check(r.result == "cleared"
      and abs(first_w - (r.t0 + 3.0 + walk_events[1][0]["t"] - 2.9)) < 0.05,
      f"a recording with its own Challenge click lines up on it, not the timer; "
      f"{r.result!r}")

# ============ watching HP: leaving before the character dies ============

for text, want in [("11223/11223", 1.0), ("5000/10000", 0.5), ("0/11223", 0.0),
                   ("11 223/11223", 1.0), ("12000/11223", None), ("abc", None),
                   ("", None), ("11223/0", None)]:
    got = rd.hp_fraction(text)
    check(got == want, f"hp_fraction: {text!r} is {want}, got {got!r}")

r = play_run(Scene(banner_at=30, hp=lambda since, n: 1.0 if since < 0.4 else 0.2),
             {"timeout_seconds": 20})
check(r.result == "low_hp", f"low_hp: HP under the line ends the run, got {r.result!r}")
check(r.calls[-1] == BEFORE and "exit" not in r.calls and len(r.exit_keys) == 2 * 2,
      f"low_hp: BM cancel, then the manual exit (no claim); got {r.calls}")
check(len(r.run_keys) < 2 * 5, f"low_hp: the replay is cut short, got {len(r.run_keys)}")
check(not r.fake.game.keys and not r.fake.game.buttons, "low_hp: nothing held")
check("HP" in r.said, f"low_hp: the log says why, got {r.said!r}")

r = play_run(Scene(banner_at=RUN_LENGTH + 0.3, hp=lambda since, n: 0.1 if n == 3 else 0.9))
check(r.result == "cleared", f"one low HP read is checked again before leaving, got "
                             f"{r.result!r}")

r = play_run(Scene(banner_at=RUN_LENGTH + 0.3, hp=lambda since, n: None))
check(r.result == "cleared", f"unreadable HP doesn't end the run, got {r.result!r}")

r = play_run(Scene(banner_at=RUN_LENGTH + 0.3, hp=lambda since, n: 0.5))
check(r.result == "cleared", f"HP above the line doesn't end the run, got {r.result!r}")

scene = Scene(banner_at=RUN_LENGTH + 0.3, hp=lambda since, n: 1.0)
r = play_run(scene, hp_every=0.4)
want = (RUN_LENGTH + 0.3) / 0.4
check(abs(scene.hp_reads - want) <= 2,
      f"hp_check_every: HP is read that often, got {scene.hp_reads} reads, wanted "
      f"about {want:.0f}")

# ================= leaving by the dungeon's manual exit =================

with world():
    check(rd.exit_steps("fire") == [["play", "fire", "out"]],
          "a dungeon with its own manual exit leaves by it")
    check(rd.exit_steps("ice") == [["play", "fire", "out"]],
          "a dungeon without one leaves by the shared default (Chaos Arena's exit clip)")
    check(rd.problem("ice") is None, f"no exit recording is needed, got {rd.problem('ice')}")

elsewhere_exit = SEQ / "fire" / "leave_other_layout.jsonl"
lines = EXIT_FILE.read_text(encoding="utf-8").splitlines()
head = json.loads(lines[0])
head["desktop"] = [-3840, 0, 5760, 1440]
moved_off = json.loads(lines[1])
elsewhere_exit.write_text("\n".join([json.dumps(head),
                                     json.dumps({**moved_off, "kind": "mouse", "action": "move",
                                                 "x": -2000, "y": 300, "injected": False})]
                                    + lines[1:]) + "\n", encoding="utf-8")
off_screen = SEQ / "fire" / "leave_off_screen.jsonl"
off_screen.write_text("\n".join([json.dumps(head)] + lines[1:] + [json.dumps(
    {"t": 9.0, "kind": "mouse", "action": "down", "button": "left", "x": -2000, "y": 300,
     "injected": False})]) + "\n", encoding="utf-8")
with world({"fire": {**RECORDED["fire"], "manual_exit": [["play", "fire", "leave_other_layout"]]}}):
    check(rd.problem("fire") is None,
          f"a manual exit clip from another monitor layout, its clicks on the main screen, "
          f"is accepted as dungeon.py accepts it, got {rd.problem('fire')}")
with world({"fire": {**RECORDED["fire"], "manual_exit": [["play", "fire", "leave_off_screen"]]}}):
    said = rd.problem("fire") or ""
check("main screen" in said, f"one with a click off the main screen is refused, got {said!r}")
r = play_run(Scene(), {"timeout_seconds": 0.5,
                       "manual_exit": [["play", "fire", "leave_other_layout"]]})
moves = [d for _t, k, d in r.fake.game.log if k == "move"]
check(r.result == "timeout" and len(r.exit_keys) == 2 * 2
      and all(0 <= x < 1920 and 0 <= y < 1080 for x, y in moves),
      f"it plays, its cursor kept on the main screen, got {r.result!r} {len(r.exit_keys)} "
      f"{[m for m in moves if not (0 <= m[0] < 1920)][:3]}")

r = play_run(Scene(), {"timeout_seconds": 0.5,
                       "manual_exit": [["button", "confirmation", 0.0],
                                       ["play", "fire", "leave"]]})
check(r.result == "timeout" and ("seq", (("button", "confirmation", 0.0),)) in r.calls
      and len(r.exit_keys) == 2 * 2,
      f"a manual exit's button steps go through the dungeon's steps and its clips are "
      f"played, got {r.result!r} {r.calls} {len(r.exit_keys)}")

# ============================== Task 4 ==============================

ran_one = []
with patched((rd, "run", lambda name, verbose=True: ran_one.append(name) or "cleared")), \
        contextlib.redirect_stdout(io.StringIO()):
    dungeon.command(["recorded", "fire"])
check(ran_one == ["fire"], f"dungeon.py recorded NAME plays one recorded run, got {ran_one}")
try:
    with contextlib.redirect_stdout(io.StringIO()):
        dungeon.command(["nonsense"])
    usage = ""
except SystemExit as exc:
    usage = str(exc)
check("recorded DUNGEON" in usage, f"the help names the recorded command, got {usage!r}")
check("recorded all" in usage, f"the help names recorded all, got {usage!r}")

for profile in ("cang", "trung", "bot"):
    entry = json.loads((SRC / "configs" / f"{profile}.json").read_text(encoding="utf-8"))[
        "dungeon"]["recorded"]["fire"]
    check(entry.get("enabled") is (profile == "cang")
          and entry.get("recording") == f"{profile}_fire_run.jsonl"
          and entry.get("timeout_seconds") == 300 and "exit_recording" not in entry,
          f"{profile} lists fire as a recorded dungeon, on only for cang; it "
          f"leaves by the dungeon's manual exit, so there is no exit recording, got {entry}")

for profile in ("cang", "trung", "bot"):
    entries = json.loads((SRC / "configs" / f"{profile}.json").read_text(encoding="utf-8"))[
        "dungeon"].get("recorded", {})
    check(all(entry.get("timeout_seconds") == 300 for entry in entries.values()),
          f"{profile}: every recorded dungeon has 300s per run, got "
          f"{ {name: e.get('timeout_seconds') for name, e in entries.items()} }")

# ============================== the end ==============================

check(not TRIPPED, f"tests sent real input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
