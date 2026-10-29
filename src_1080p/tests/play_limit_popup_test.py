import contextlib
import io
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["dungeon.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import ctypes
import calibration
import dungeon

checks = 0
failures = []


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


RESET = dungeon.DUNGEONS["windmill"]["reset"]
ENTER = tuple(dungeon.centre(dungeon.ENTER_WINDOW))
screen = {}
clicks = []


def fresh(shown, left, popup_again=False):
    screen.update(at="list", shown=shown, left=left, again=popup_again)
    clicks.clear()


def click(x, y, settle=0.0):
    clicks.append((x, y))
    if (x, y) == ENTER:
        screen["at"] = ("popup" if screen["left"] == 0 or screen["again"]
                        else "inside")
    elif [x, y] == list(RESET["button"]):
        screen.update(at="list", left=3, shown="(3/3)")


def read_windows(image, windows):
    texts = {tuple(RESET["heading_box"]): "Reset dungeon play limit",
             tuple(RESET["cancel_box"]): "Cancel",
             tuple(RESET["count_box"]): "0/2"}
    out = []
    for box in windows:
        box = tuple(box)
        if screen["at"] == "popup" and box in texts:
            out.append(texts[box])
        elif screen["at"] == "inside" and box == dungeon.CHALLENGE_WINDOW:
            out.append(dungeon.CHALLENGE_WORD)
        elif screen["at"] == "list" and box == dungeon.ENTER_WINDOW:
            out.append(dungeon.ENTER_WORD)
        else:
            out.append("")
    return out


def find_in_list(name, timeout, words, windows):
    row = f"Holy Windmill{screen['shown']}[New Entry]"
    return windows[0], [row] + [""] * (len(windows) - 1)


saved = {name: getattr(dungeon, name) for name in
         ("click", "read_windows", "find_in_list", "focus_game",
          "set_difficulty", "_state", "forget_entry", "WAIT_SLICE")}
saved_grab, saved_snap = calibration.grab, calibration.snap
dungeon.click = click
dungeon.read_windows = read_windows
dungeon.find_in_list = find_in_list
dungeon.focus_game = lambda: True
dungeon.set_difficulty = lambda level, verbose=True: None
dungeon._state = lambda: {"entry": {"tab": 7, "slot": [1, 1],
                                    "point": [1475, 236]}}
dungeon.forget_entry = lambda: clicks.append("forgot")
dungeon.WAIT_SLICE = 0.0
calibration.grab = lambda *a, **k: None
blocked_cursor = ctypes.windll.user32.SetCursorPos
ctypes.windll.user32.SetCursorPos = lambda *a: None
calibration.snap = lambda *a, **k: None
try:
    # The row says an entry is left, but the game has none: Enter brings
    # the reset popup, which is answered, and the entry opens again.
    fresh("(1/3)", 0)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        dungeon.open_dungeon_key("windmill", "WINDMILL")
    button = tuple(RESET["button"])
    check(clicks.count(ENTER) == 2, f"Enter twice, got {clicks}")
    check(button in clicks, f"the reset button clicked, got {clicks}")
    check(clicks.index(button) < len(clicks) - 1
          and clicks[-1] == "forgot", f"entered after the reset, got {clicks}")
    check(screen["at"] == "inside", f"inside after all, got {screen}")
    check("no entries left after all" in out.getvalue(),
          f"the popup is logged, got {out.getvalue()!r}")

    # The row is right: Enter goes straight to Challenge, no reset.
    fresh("(1/3)", 1)
    with contextlib.redirect_stdout(io.StringIO()):
        dungeon.open_dungeon_key("windmill", "WINDMILL")
    check(clicks[1:] == [ENTER, "forgot"], f"no reset when entries are left, "
          f"got {clicks}")

    # The row reads 0: the reset runs first, as before.
    fresh("(0/3)", 0)
    with contextlib.redirect_stdout(io.StringIO()):
        dungeon.open_dungeon_key("windmill", "WINDMILL")
    check(clicks.count(button) == 1 and clicks[-1] == "forgot",
          f"the 0/3 reset still works, got {clicks}")

    # The popup again after the reset: stop, nothing more.
    fresh("(1/3)", 0, popup_again=True)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            dungeon.open_dungeon_key("windmill", "WINDMILL")
        check(False, "a second popup raises")
    except RuntimeError as error:
        check("again after the reset" in str(error), f"got {error}")
        check("forgot" not in clicks, f"the entry is kept, got {clicks}")
finally:
    for name, value in saved.items():
        setattr(dungeon, name, value)
    calibration.grab, calibration.snap = saved_grab, saved_snap
    ctypes.windll.user32.SetCursorPos = blocked_cursor

check(not TRIPPED, f"no real input sent, got {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for failure in failures:
    print("  FAIL", failure)
sys.exit(1 if failures else 0)
