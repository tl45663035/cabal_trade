"""The gameplay recorder and replayer, end to end, on a pretend keyboard,
mouse, Windows and game (fake_input.py): a session is played by a pretend
hand (and bot), recorded by the real tools/record_all.record(), replayed by
the real replayers, and what the game was told both times must match, at the
same microsecond.

Scenarios, by id (the design doc lists them too):
  H  happy paths     E  edge cases     F  failure paths

RECORD_REPLAY_SEEDS sets how many random sessions E18 plays (default 40)."""

import contextlib
import io
import json
import os
import random
import sys
import tempfile
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
import dungeon
import record_action as ra
import record_all
from fake_input import (FakeInput, Script, bot_click_at, bot_click_here, bot_key,
                        bot_tap, bot_type, differences, patched, record_session,
                        replay_session, replay_zero, window)

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


OUT = Path(tempfile.mkdtemp(prefix="cabal_record_replay_"))
LOGIN = ("cang01", "s3cr#t pw")
SINGLE = (0, 0, 1920, 1080)
WIDE = (0, 0, 5360, 1440)
LEFT_SIDE = (-2560, 0, 4480, 1440)
GAME = [-8, -8, 1928, 1048]


def head_of(path):
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


def events_of(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()[1:]
            if l.strip()]


def record(script, command=None, layout=SINGLE, cursor=(960, 540), crash=False,
           window=None):
    with contextlib.redirect_stdout(io.StringIO()):
        return record_session(script, OUT, fake=FakeInput(layout=layout, cursor=cursor),
                              command=command, crash=crash, window=window)


def told(rec, path):
    zero = rec.marks["start"]
    end = rec.marks["stop"] - zero if "stop" in rec.marks else None
    return window(rec.game.log, zero, end)


def replay(path, entry="direct", layout=SINGLE, **kw):
    return replay_session(path, entry, fake=FakeInput(layout=layout, cursor=(3, 3)),
                          dungeon=dungeon, **kw)


def roundtrip(script, entry="direct", command=None, layout=SINGLE, cursor=(960, 540),
              crash=False):
    path, rec = record(script, command, layout, cursor, crash)
    rep = replay(path, entry, layout)
    recorded = told(rec, path)
    replayed = window(rep.game.log, replay_zero(rep))
    start = head_of(path)["cursor"]
    soft = [t - rec.marks["start"] for t in rec.game.soft]
    found = differences(recorded, replayed, start, start, soft=soft)
    return found, path, rec, rep, recorded, replayed


def same(sid, label, result):
    found, path, rec, rep, recorded, replayed = result
    check(not found, f"{sid} {label}: the replay told the game something else: {found[:3]}")
    check(len(recorded) > 0, f"{sid} {label}: the session did something")
    check(not rep.game.keys and not rep.game.buttons,
          f"{sid} {label}: nothing is left held after the replay, got "
          f"{rep.game.keys} {rep.game.buttons}")
    return result


def sent_inputs(rep):
    return [item for _t, item in rep.sends]


# ============================== happy paths ==============================

def h1():
    s = Script().ctrl_twice("start")
    s.tap("W", hold=40, repeats=12)
    s.tap("S", hold=200, repeats=3, repeat_every=50)
    return s.ctrl_twice("stop")


found, path, rec, rep, recorded, replayed = same("H1", "walk with a held key",
                                                 roundtrip(h1()))
check(sum(1 for _t, k, d in replayed if k == "key" and d == (17, False, False)) == 13,
      "H1 the W press and its 12 repeats all reach the game")


def h2():
    s = Script().ctrl_twice("start")
    for name in ("1", "2", "0", "9", "Q", "E", "Z", "I", "1", "1", "2"):
        s.tap(name, hold=30, gap=40)
    return s.ctrl_twice("stop")


same("H2", "skill and number keys", roundtrip(h2()))


def h3():
    s = Script().ctrl_twice("start")
    s.wander([(3, 1), (1, 0), (0, 1), (1, 1), (-2, 5), (7, -3), (1, 0), (1, 0)])
    s.click("left").click("right").click("left", hold=900)
    s.wander([(-5, -5)] * 30, every=1)
    return s.ctrl_twice("stop")


same("H3", "cursor moves (sub-pixel too), clicks and a long press", roundtrip(h3()))


def h4():
    s = Script().ctrl_twice("start")
    s.wander([(1, 0)] * 3 + [(1, 1)])
    s.turn([(1, 0), (2, 0), (3, -1), (5, 0), (1, 0), (1, 1), (-1, 0), (7, 2)] * 6)
    s.click("left")
    s.turn([(25, 3)] * 30 + [(-9, 1)] * 10)
    return s.ctrl_twice("stop")


found, path, rec, rep, *_ = same("H4", "camera turns", roundtrip(h4()))
check(rec.game.camera == rep.game.camera and rec.game.camera != [0, 0],
      f"H4 the camera ends facing exactly the same way, got {rec.game.camera} "
      f"vs {rep.game.camera}")
check(sum(1 for e in events_of(path) if e["action"] == "raw") == rec.raw_made,
      "H4 every raw count the mouse made is in the file")
check(all(e["action"] != "raw" or (e["dx"] or e["dy"]) for e in events_of(path)),
      "H4 no empty raw counts are saved")


def h5():
    s = Script().ctrl_twice("start")
    s.wheel(-120).wheel(-120).wheel(240).wheel(120, horizontal=True)
    return s.ctrl_twice("stop")


same("H5", "zoom with the wheel", roundtrip(h5()))


def h6():
    s = Script().ctrl_twice("start")
    s.at(50, lambda f: f.button("left", True))
    s.wander([(4, 2)] * 40, every=3)
    s.at(20, lambda f: f.button("left", False))
    return s.ctrl_twice("stop")


same("H6", "click-to-move with the left button held", roundtrip(h6()))


def h7():
    s = Script().ctrl_twice("start")
    s.at(40, lambda f: f.press("W"))
    s.turn([(6, 0)] * 30, gap=20)
    s.at(5, lambda f: f.press("W"))
    s.at(30, lambda f: f.release("W"))
    return s.ctrl_twice("stop")


same("H7", "walk while turning", roundtrip(h7()))


def h8():
    s = Script()
    s.bot(lambda f: bot_tap(f, "I"))
    s.bot(lambda f: bot_click_at(f, 812, 400))
    s.hover(1000, 300).hover(1010, 305)
    s.bot(lambda f: bot_click_here(f))
    s.bot(lambda f: (bot_key(f, "Ctrl", False), f.wait(20), bot_click_here(f),
                     f.wait(20), bot_key(f, "Ctrl", True)))
    s.bot(lambda f: bot_click_at(f, 700, 650, "right"))
    s.bot(lambda f: bot_tap(f, "Esc"))
    return s


for entry in ("direct", "cli", "dungeon"):
    found, path, rec, rep, *_ = same("H8", f"a bot run, replayed via {entry}",
                                     roundtrip(h8(), entry=entry, command=["bot"]))
polled = [e for e in events_of(path) if e.get("source") == "poll"]
check([(e["x"], e["y"]) for e in polled] == [(1000, 300), (1010, 305)],
      f"H8 the bot's hovers, which no hook sees, are picked up, got {polled}")


def h9():
    s = Script()
    s.bot(lambda f: bot_tap(f, "I"))
    s.tap("W", repeats=5)
    s.bot(lambda f: bot_click_at(f, 400, 400))
    s.turn([(3, 1)] * 20)
    s.hover(800, 800)
    s.click("left")
    s.bot(lambda f: bot_tap(f, "Esc"))
    return s


same("H9", "the hand and the bot together", roundtrip(h9(), command=["bot"]))

path, rec = record(h4())
logs = {}
for entry in ("direct", "cli", "dungeon"):
    rep = replay(path, entry)
    logs[entry] = window(rep.game.log, replay_zero(rep))
check(logs["direct"] == logs["cli"] == logs["dungeon"],
      "H10 Replay, record_all.py replay and dungeon.py replay tell the game the same")

for label, script, command in (("hand", h3(), None), ("turns", h4(), None),
                               ("bot", h8(), ["bot"])):
    path, rec = record(script, command)
    fake = FakeInput(cursor=(3, 3))
    capture = record_all.Everything(control=False, command=["capture"])
    capture.zero = fake.now()
    capture.path = OUT / f"capture_{label}{record_all.SUFFIX}"
    with patched((record_all, "time", types.SimpleNamespace(
            perf_counter=fake.now, sleep=lambda s: None)),
            (ra, "screen", lambda: [1920, 1080]), (ra, "desktop", lambda: list(SINGLE))):
        capture.open()
        replay_session(path, "direct", fake=fake, capture=capture)
        capture.finish()
    found = record_all.compare(path, capture.path)
    check(found["missing"] == 0 and found["extra"] == 0 and found["matched"] > 0,
          f"H11 compare ({label}): nothing missing or extra, got {found}")
    check(found["jitter_ms"][2] < 0.002 and abs(found["drift_ms"]) < 0.002
          and not found["clicks_off"],
          f"H11 compare ({label}): every event and click in its place, got {found}")

path, rec = record(h1())
rep = replay(path, "dungeon", times=3, rest=0.01)
zeros = rep.marks["zeros"]
check(len(zeros) == 3 and rep.marks.get("done") == 3, f"H12 three loops played, got {zeros}")
recorded = told(rec, path)
for n, zero in enumerate(zeros):
    end = zeros[n + 1] - zero + 1 if n + 1 < len(zeros) else None
    run = window(rep.game.log, zero, end)
    check([e for e in run if e[1] == "key"] == [e for e in recorded if e[1] == "key"],
          f"H12 loop {n + 1} tells the game exactly what the recording did")

# ============================== edge cases ===============================

def e1():
    s = Script().ctrl_twice("start")
    for name in ("Esc", "Enter", "NumEnter", "Space", "Tab", "Backspace", "Up", "Down",
                 "Left", "Right", "Delete", "End", "F1", "F10", "Slash", "Win", "Shift",
                 "Alt"):
        s.tap(name)
    return s.ctrl_twice("stop")


found, path, rec, rep, recorded, replayed = same("E1", "every special key",
                                                 roundtrip(e1()))
scans = {d[:2] for _t, k, d in replayed if k == "key"}
check({(1, False), (28, False), (28, True), (57, False), (15, False), (14, False),
       (72, True), (80, True), (75, True), (77, True), (83, True), (79, True),
       (59, False), (68, False), (53, False), (91, True), (42, False), (56, False)}
      <= scans, f"E1 each special key reaches the game by scan code and extended "
                f"flag, got {sorted(scans)}")


def e2():
    s = Script().ctrl_twice("start")
    s.combo("Ctrl", "1").combo("Ctrl", "2").combo("RCtrl", "9")
    s.combo("Alt", "1").combo("Shift", "Z")
    s.at(50, lambda f: f.press("Ctrl"))
    s.click("left")
    s.at(40, lambda f: f.release("Ctrl"))
    return s.ctrl_twice("stop")


found, path, rec, *_ = same("E2", "modifier combos", roundtrip(e2()))
check(rec.marks["stop"] > rec.marks["start"],
      "E2 Ctrl used with other keys never stopped the recording early")


def e3():
    s = Script().ctrl_twice("start")
    s.at(50, lambda f: f.press("Ctrl"))
    s.click("left")
    s.at(40, lambda f: f.release("Ctrl"))
    return s.ctrl_twice("stop")


found, path, *_ = same("E3", "a Ctrl release just before the stop", roundtrip(e3()))
ctrl = [(e["action"]) for e in events_of(path) if e.get("vk") == 162]
check(ctrl == ["down", "up"], f"E3 the Ctrl press and release are both kept, got {ctrl}")


def e4():
    s = Script().ctrl_twice("start")
    s.at(50, lambda f: f.press("W"))
    for _ in range(5):
        s.at(33, lambda f: f.press("W"))

    def stop_holding(f):
        f.marks["stop"] = f.clock
        f.press("Ctrl")
    s.at(33, stop_holding)
    s.at(20, lambda f: f.press("W"))
    s.at(20, lambda f: f.release("Ctrl"))
    s.at(33, lambda f: f.press("W"))
    s.at(20, lambda f: f.press("Ctrl"))
    s.at(20, lambda f: f.release("Ctrl"))
    s.at(100, lambda f: f.release("W"))
    return s


path, rec = record(e4())
rep = replay(path)
zero = rec.marks["start"]
ctrl_scan = (29, False)
# The W repeats made while pressing the stop gesture are real input, so
# they are kept; the gesture's own Ctrl taps are not. W was let go only
# after the recording stopped, so the replay lets go of it at the end.
wanted = [e for e in window(rec.game.log, zero) if e[1] == "key" and e[2][:2] != ctrl_scan
          and not (e[2][:2] == (17, False) and e[2][2])]
got = [e for e in window(rep.game.log, replay_zero(rep)) if e[1] == "key"]
check(got[:-1] == wanted and got[-1][2] == (17, False, True) and got[-1][0] == got[-2][0],
      f"E4 stopping while holding W keeps W and its repeats, drops the gesture, and the "
      f"replay lets go of W at the end: wanted {wanted[-3:]}, got {got[-4:]}")
check(not rep.game.keys, "E4 nothing is left held after the replay")


def e5():
    s = Script().ctrl_twice("start")
    s.click("middle").click("x1").click("x2")
    s.at(30, lambda f: f.button("left", True))
    s.at(30, lambda f: f.button("right", True))
    s.wander([(2, 0)] * 20)
    s.at(10, lambda f: f.button("right", False))
    s.at(10, lambda f: f.button("left", False))
    return s.ctrl_twice("stop")


same("E5", "middle and side buttons, both buttons held", roundtrip(e5()))


def e6():
    s = Script().ctrl_twice("start")
    s.wander([(400, 0)] * 6)
    s.turn([(50, 0)] * 20 + [(-30, 0)] * 10)
    s.wander([(0, -400)] * 4)
    s.turn([(0, -40)] * 15)
    return s.ctrl_twice("stop")


found, path, rec, rep, *_ = same("E6", "turning into the screen edge", roundtrip(e6()))
check(rec.game.camera == rep.game.camera, "E6 the camera still turns the same at the edge")


def e7():
    s = Script().ctrl_twice("start")
    s.turn([(2, 1)] * 300, every=0.25)
    s.turn([(1, 0)] * 10, every=180)
    return s.ctrl_twice("stop")


same("E7", "very fast and very slow turns", roundtrip(e7()))


def e8():
    s = Script()
    s.bot(lambda f: bot_click_at(f, 400, 400))
    s.at(40, lambda f: f.button("right", True))
    s.wander([(4, 0)] * 10)
    s.hover(900, 200, gap=3)
    s.wander([(4, 1)] * 10)
    s.bot(lambda f: f.send(ra.mouse_input({"x": 300, "y": 300}, 0)), gap=3)
    s.wander([(-2, 0)] * 10)
    s.at(5, lambda f: f.button("right", False))
    s.bot(lambda f: bot_click_here(f))
    return s


same("E8", "the bot moves the cursor during a turn", roundtrip(e8(), command=["bot"]))


def e9():
    # 967 lasts half a millisecond, so the 1ms cursor check never sees it;
    # it sees 975. The bot then hovers back to 967 within 0.1s.
    s = Script()
    s.wander([(10, 0)], every=5)
    s.wander([(10, 0)], every=0.5)
    s.hover(967, 540, gap=5)
    s.bot(lambda f: bot_click_here(f))
    return s


same("E9", "a hover back to a spot the cursor check missed",
     roundtrip(e9(), command=["bot"]))


def e10():
    s = Script()
    s.hover(500, 500, gap=10)
    s.wander([(3, 3)], every=0.4)
    s.bot(lambda f: bot_click_here(f), gap=20)
    return s


found, path, rec, rep, recorded, replayed = roundtrip(e10(), command=["bot"])
check(not found, f"E10 a hover under 1ms is at most one cursor check late: {found}")
clicks = [d[2] for _t, k, d in replayed if k == "button"]
check(clicks and clicks[0] == rec.game.cursor,
      "E10 and the click after it still lands where it did")


def e11(across):
    s = Script().ctrl_twice("start")
    s.wander([(across, 3)] * 40)
    s.click("left")
    s.turn([(9, 0)] * 20)
    s.wander([(-across, -2)] * 80)
    s.click("right")
    return s.ctrl_twice("stop")


for layout in (WIDE, LEFT_SIDE):
    found, path, rec, *_ = same("E11", f"monitors {layout}",
                                roundtrip(e11(60), layout=layout))
    xs = [d[0] for _t, k, d in rec.game.log if k == "move"]
    check(max(xs) >= 1920 or min(xs) < 0, f"E11 {layout}: the cursor left the main screen")

found, path, *_ = same("E12", "starting with the cursor on another monitor",
                       roundtrip(h3(), layout=WIDE, cursor=(3000, 700)))
check(head_of(path)["cursor"] == [3000, 700], "E12 the start cursor is noted")


def e13():
    s = Script()
    s.bot(lambda f: bot_click_at(f, 960, 400))
    s.bot(lambda f: (bot_type(f, LOGIN[0]), bot_tap(f, "End"), bot_tap(f, "Backspace"),
                     bot_type(f, LOGIN[1]), bot_tap(f, "Enter")))
    s.bot(lambda f: bot_tap(f, "Enter"), gap=400)
    return s


for entry in ("direct", "cli", "dungeon"):
    found, path, rec, rep, recorded, replayed = same(
        "E13", f"a relog while recording, replayed via {entry}",
        roundtrip(e13(), entry=entry, command=["bot"]))
    check(not any(k == "char" for _t, k, _d in replayed),
          f"E13 ({entry}) no typed character is ever sent")
text = path.read_text(encoding="utf-8")
check(LOGIN[1] not in text and LOGIN[0] not in text and "s3cr" not in text
      and not any(e.get("vk") == 231 for e in events_of(path)),
      "E13 neither the username nor the password, nor any typed character, is saved")
check(any(k == "char" for _t, k, _d in recorded),
      "E13 (the bot did type them while recording)")
keys = [e.get("vk") for e in events_of(path) if e["kind"] == "key" and e["action"] == "down"]
check(keys == [35, 8, 13, 13], f"E13 the keys around the typing are kept, got {keys}")


def e14():
    rng = random.Random(14)
    s = Script().ctrl_twice("start")
    for _ in range(25):
        s.wander([(rng.randint(-6, 6), rng.randint(-6, 6)) for _ in range(150)],
                 every=rng.choice([0.5, 1, 2]))
        s.tap("W", repeats=rng.randint(0, 20))
        s.turn([(rng.randint(-9, 9), rng.randint(-2, 2)) for _ in range(60)])
        s.click("left")
    return s.ctrl_twice("stop")


found, path, rec, rep, *_ = same("E14", "a long session", roundtrip(e14()))
check(len(events_of(path)) > 10000, f"E14 (the session had {len(events_of(path))} events)")


def e15():
    s = Script()

    def burst(f):
        bot_key(f, "Ctrl", False)
        f.send(record_all.button_input("left", "down"))
        f.send(record_all.button_input("left", "up"))
        bot_key(f, "Ctrl", True)
        bot_key(f, "Esc", False)
        bot_key(f, "Esc", True)
    s.bot(burst)
    return s


found, path, rec, rep, recorded, replayed = same(
    "E15", "inputs at the same microsecond", roundtrip(e15(), command=["bot"]))
check([k for _t, k, _d in replayed] == [k for _t, k, _d in recorded],
      "E15 they reach the game in the same order")

path, rec = record(h4())
lines = path.read_text(encoding="utf-8").splitlines()
older = OUT / f"older_no_raw{record_all.SUFFIX}"
older.write_text("\n".join(l for l in lines if '"raw"' not in l) + "\n", encoding="utf-8")
rep = replay(older)
check(not any(item.type == ra.W["INPUT_MOUSE"]
              and item.u.mi.dwFlags & ra.W["MOUSEEVENTF_MOVE"]
              and not item.u.mi.dwFlags & ra.W["MOUSEEVENTF_ABSOLUTE"]
              for item in sent_inputs(rep)),
      "E16 an older recording without raw counts sends no raw moves")
recorded = told(rec, path)
replayed = window(rep.game.log, replay_zero(rep))
start = head_of(path)["cursor"]
check([e for e in replayed if e[1] == "move"] and not differences(
    [e for e in recorded if e[1] in ("move", "key")],
    [e for e in replayed if e[1] in ("move", "key")], start, start, tolerance=1),
    "E16 and drags its turns by the cursor, as it did before")

older = OUT / f"older_typed{record_all.SUFFIX}"
typed = [json.dumps({"t": 0.5 + n / 100, "kind": "key", "action": a, "vk": 231,
                     "secret": "password", "i": n, "injected": True})
         for n in range(3) for a in ("down", "up")]
older.write_text("\n".join([lines[0]] + typed + lines[1:]) + "\n", encoding="utf-8")
rep = replay(older)
check(not any(item.type == ra.W["INPUT_KEYBOARD"]
              and item.u.ki.dwFlags & ra.W["KEYEVENTF_UNICODE"]
              for item in sent_inputs(rep)) and rep.marks.get("refused") is None,
      "E17 an older recording with typed characters plays, sending none of them")

PLAIN = ["W", "A", "S", "D", "Q", "E", "Z", "I", "1", "2", "0", "9", "Esc", "Enter",
         "NumEnter", "Space", "Tab", "Up", "Left", "Delete", "F1", "Slash", "Shift"]


def counts(rng, n, reach):
    return [(rng.randint(-reach, reach), rng.randint(-reach, reach)) for _ in range(n)]


def hand_bits(rng, s):
    pick = rng.randrange(9)
    gap = rng.randint(1, 150)
    if pick == 0:
        s.tap(rng.choice(["W", "A", "S", "D"]), hold=rng.randint(10, 80), gap=gap,
              repeats=rng.randint(0, 15))
    elif pick == 1:
        s.tap(rng.choice(PLAIN), hold=rng.randint(5, 120), gap=gap)
    elif pick == 2:
        s.combo(rng.choice(["Ctrl", "Alt", "Shift", "RCtrl"]),
                rng.choice(["1", "2", "0", "9", "Z"]), gap=gap)
    elif pick == 3:
        s.wander(counts(rng, rng.randint(1, 40), rng.choice([1, 2, 8, 40])),
                 every=rng.choice([0.3, 0.5, 1, 2, 8]))
    elif pick == 4:
        s.click(rng.choice(["left", "left", "right", "middle", "x1", "x2"]),
                hold=rng.randint(5, 300), gap=gap)
    elif pick == 5:
        s.turn(counts(rng, rng.randint(3, 80), rng.choice([1, 3, 15])),
               every=rng.randint(1, 6), gap=gap)
    elif pick == 6:
        s.wheel(rng.choice([-240, -120, 120, 240]), gap=gap,
                horizontal=rng.random() < 0.2)
    elif pick == 7:
        s.at(gap, lambda f: f.press("W"))
        s.turn(counts(rng, rng.randint(3, 30), 6), gap=rng.randint(1, 40))
        s.at(rng.randint(1, 50), lambda f: f.release("W"))
    else:
        s.at(gap, lambda f: f.button("left", True))
        s.wander(counts(rng, rng.randint(2, 20), 5), every=rng.randint(1, 6))
        s.at(rng.randint(1, 40), lambda f: f.button("left", False))


def bot_bits(rng, s, layout):
    left, top, width, height = layout
    pick = rng.randrange(6)
    x, y = rng.randrange(left, left + width), rng.randrange(top, top + height)
    gap = rng.randint(1, 200)
    if pick == 0:
        s.bot(lambda f, k=rng.choice(PLAIN): bot_tap(f, k), gap=gap)
    elif pick == 1:
        s.bot(lambda f, b=rng.choice(["left", "right"]): bot_click_at(f, x, y, b), gap=gap)
    elif pick == 2:
        s.hover(x, y, gap=gap)
    elif pick == 3:
        s.bot(lambda f: bot_click_here(f), gap=gap)
    elif pick == 4:
        s.bot(lambda f: (bot_key(f, "Ctrl", False), f.wait(15), bot_click_here(f),
                         f.wait(15), bot_key(f, "Ctrl", True)), gap=gap)
    else:
        s.bot(lambda f: (bot_type(f, LOGIN[0]), bot_tap(f, "End"),
                         bot_type(f, LOGIN[1])), gap=gap)


seeds = int(os.environ.get("RECORD_REPLAY_SEEDS", "40"))
for seed in range(seeds):
    rng = random.Random(seed)
    layout = rng.choice([SINGLE, WIDE, LEFT_SIDE])
    if seed % 2 == 0:
        s = Script().ctrl_twice("start")
        for _ in range(rng.randint(10, 60)):
            hand_bits(rng, s)
        s.ctrl_twice("stop")
        command = None
    else:
        s = Script()
        for _ in range(rng.randint(10, 60)):
            (hand_bits(rng, s) if rng.random() < 0.5 else bot_bits(rng, s, layout))
        command = ["bot"]
    entry = ("direct", "cli", "dungeon")[seed % 3]
    found, path, rec, rep, recorded, replayed = roundtrip(
        s, entry=entry, command=command, layout=layout)
    check(not found, f"E18 random session {seed} ({entry}, {layout}): {found[:2]}")
    check(not rep.game.keys and not rep.game.buttons,
          f"E18 random session {seed}: nothing left held after the replay")
    check(not any(k == "char" for _t, k, _d in replayed),
          f"E18 random session {seed}: no typed character sent")

zoom_script = Script().ctrl_twice("start").tap("W", hold=40, gap=60).ctrl_twice("stop")
path, rec = record_session(zoom_script, OUT, zoom=5)
head = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
check(head.get("zoom_out") == 5, f"zoom e2e: the recording notes its zoom, got {head}")
rec_wheels = [d for _t, k, d in rec.game.log if k == "wheel"]
check(len(rec_wheels) == 5 and all(d[0] < 0 for d in rec_wheels),
      f"zoom e2e: the game was scrolled down 5 times before recording, got {rec_wheels}")
check(not any(e.get("kind") == "mouse" and e.get("action") == "wheel"
              for e in (json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()[1:])),
      "zoom e2e: those wheel turns are not in the recording")
rep = replay_session(path)
log = [(k, d) for _t, k, d in rep.game.log if k in ("wheel", "key")]
check([k for k, d in log][:6] == ["wheel"] * 5 + ["key"] and all(d[0] < 0 for k, d in log[:5]),
      f"zoom e2e: the replay scrolls down 5 times, then plays the recording, got {log[:7]}")

# ============================== failure paths ============================

def f1():
    s = Script()
    s.tap("W", repeats=3).click("left").wander([(5, 5)] * 10).turn([(3, 0)] * 10)
    s.ctrl_twice("start")
    s.tap("E")
    return s.ctrl_twice("stop")


path, rec = record(f1())
names = [e.get("vk") for e in events_of(path) if e["kind"] == "key"]
check(names == [69, 69], f"F1 nothing before the start gesture is kept, got {names}")
check(not any(e["kind"] == "mouse" and e["action"] in ("down", "up", "raw")
              for e in events_of(path)), "F1 not even the clicks or turns")


def f2():
    s = Script()
    s.bot(lambda f: (bot_tap(f, "Ctrl"), f.wait(80), bot_tap(f, "Ctrl")))
    s.tap("E")
    s.ctrl_twice("start")
    s.tap("Q")
    s.bot(lambda f: (bot_tap(f, "Ctrl"), f.wait(80), bot_tap(f, "Ctrl")))
    s.tap("W")
    return s.ctrl_twice("stop")


path, rec = record(f2())
downs = [e.get("vk") for e in events_of(path) if e["kind"] == "key" and e["action"] == "down"]
check(downs == [81, 162, 162, 87],
      f"F2 the bot's Ctrl x2 neither starts nor stops a hand recording, got {downs}")


def f3():
    s = Script().ctrl_twice("start")
    s.tap("Ctrl", gap=100)
    s.tap("Ctrl", gap=1500)
    s.tap("W")
    return s.ctrl_twice("stop")


path, rec = record(f3())
check(any(e.get("vk") == 87 for e in events_of(path)),
      "F3 two Ctrl taps further apart than the window don't stop the recording")


def f4():
    s = Script().ctrl_twice("start")
    s.tap("W", repeats=5).turn([(4, 1)] * 30).click("left").tap("Esc")
    return s


found, path, *_ = same("F4", "a recorder that dies mid-session",
                       roundtrip(f4(), crash=True))
check(any(e["action"] == "raw" for e in events_of(path)),
      "F4 what was streamed before the crash includes the raw counts")

with contextlib.redirect_stdout(io.StringIO()):
    before = set(OUT.glob(f"*{record_all.SUFFIX}"))
    try:
        record_session(Script().tap("W"), OUT)
        made = None
    except AssertionError as exc:
        made = str(exc)
check(made is not None and set(OUT.glob(f"*{record_all.SUFFIX}")) == before,
      "F5 a session never started saves nothing")

path, rec = record(e11(60), layout=WIDE)
for other in (SINGLE, LEFT_SIDE):
    rep = replay(path, "direct", layout=other)
    check("would not line up" in (rep.marks.get("refused") or "") and not rep.sends,
          f"F6 a {WIDE} recording on {other} is refused and nothing is sent")
    rep = replay(path, "dungeon", layout=other)
    check("would not line up" in (rep.marks.get("refused") or "") and not rep.sends,
          f"F6 (dungeon.py) a {WIDE} recording on {other} is refused, nothing sent")

with contextlib.redirect_stdout(io.StringIO()):
    path, rec = record_session(h1(), OUT, window=GAME)
check(head_of(path)["window"] == GAME and head_of(path)["start_shot"],
      "F7 a recording made in the game notes its window and start picture")
for label, where in (("moved", [0, 0, 1920, 1080]), ("closed", None)):
    rep = replay(path, "direct", window=where)
    check(rep.marks.get("refused") and not rep.sends,
          f"F7 the game {label}: refused and nothing sent ({rep.marks.get('refused')})")
with contextlib.redirect_stdout(io.StringIO()):
    rep = replay(path, "direct", window=GAME)
check(rep.marks.get("refused") is None and rep.sends, "F7 the game in place: it plays")

with contextlib.redirect_stdout(io.StringIO()):
    rep = replay(path, "direct", window=GAME, focus=False)
check(rep.marks.get("refused") and not rep.sends,
      "F8 a game that can't be brought to the front gets nothing")


def f9():
    s = Script().ctrl_twice("start")
    s.at(50, lambda f: f.press("W"))
    s.at(30, lambda f: f.button("right", True))
    s.wander([(3, 0)] * 200, every=5)
    s.at(10, lambda f: f.button("right", False))
    s.at(10, lambda f: f.release("W"))
    return s.ctrl_twice("stop")


path, rec = record(f9())
recorded = told(rec, path)
for entry in ("direct", "cli", "dungeon"):
    rep = replay(path, entry, stop_at_ms=400)
    check(not rep.game.keys and not rep.game.buttons,
          f"F9 ({entry}) stopped mid-turn with W held: nothing left held, got "
          f"{rep.game.keys} {rep.game.buttons}")
    replayed = window(rep.game.log, replay_zero(rep))
    check([e for e in replayed if e[0] < 400_000 and e[1] != "move"]
          == [e for e in recorded if e[0] < 400_000 and e[1] != "move"],
          f"F9 ({entry}) up to the stop the game was told the same")
    lets_go = [item for item in sent_inputs(rep)[-3:] if item.type == ra.W["INPUT_MOUSE"]
               and item.u.mi.dwFlags & ra.W["MOUSEEVENTF_RIGHTUP"]]
    check(lets_go and not lets_go[0].u.mi.dwFlags & ra.W["MOUSEEVENTF_MOVE"],
          f"F10 ({entry}) the right button held in a turn is let go without a move")


def f10():
    s = Script().ctrl_twice("start")
    s.at(50, lambda f: f.press("Ctrl"))
    s.at(500, lambda f: f.press("1"))
    s.at(500, lambda f: f.release("1"))
    s.at(50, lambda f: f.release("Ctrl"))
    return s.ctrl_twice("stop")


path, rec = record(f10())
for entry in ("direct", "cli", "dungeon"):
    rep = replay(path, entry, stop_at_ms=300)
    check(not rep.game.keys, f"F10 ({entry}) stopped with Ctrl held: Ctrl is let go")

check(not TRIPPED, f"tests sent real input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
