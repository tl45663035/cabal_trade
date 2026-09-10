import os
import sys
import tempfile
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

os.environ.setdefault(
    "CABAL_SALES_DB",
    str(Path(tempfile.mkdtemp(prefix="cabal_guard_")) / "scratch.db"))
os.environ.setdefault("CABAL_NO_RECORD", "1")

import trade as m

TRIPPED = []

PRIMITIVES = [
    "click", "ctrl_click", "right_click", "move_mouse", "move_mouse_to_alz",
    "press_key", "press_escape", "type_number", "scroll_wheel", "park_cursor",
    "_send", "_release",
    "alt_click", "release_modifiers", "open_inventory",
]


def _arm(name):
    def raiser(*a, **k):
        TRIPPED.append(name)
        raise AssertionError(
            f"GAME INPUT from a test: {name}({', '.join(map(repr, a))[:70]}). "
            f"Unit tests must not drive the client.")
    return raiser


for _n in PRIMITIVES:
    if hasattr(m, _n):
        setattr(m, _n, _arm(_n))


def _no_focus(*a, **k):
    TRIPPED.append("focus_game(neutered)")
    return True


if hasattr(m, "focus_game"):
    m.focus_game = _no_focus

def _blank_grab(*a, **k):
    TRIPPED.append("grab(blanked)")
    img = m.Image.new("RGB", (2560, 1440), "black")
    m._last_shot = img
    try:
        m._FRAME_SERIAL += 1
    except Exception:
        pass
    return img


for _n in ("grab", "screenshot", "capture", "grab_frame"):
    if hasattr(m, _n):
        setattr(m, _n, _blank_grab)
if hasattr(m, "take_screenshot"):
    m.take_screenshot = _arm("take_screenshot")

def _report():
    if TRIPPED:
        seen = sorted(set(TRIPPED))
        print(f"\nDRIVES GAME INPUT -> {', '.join(seen)} "
              f"({len(TRIPPED)} call(s))")


import atexit
atexit.register(_report)

if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve()
    src = target.read_text(encoding="utf-8-sig")
    g = {"__name__": "__main__", "__file__": str(target)}
    try:
        exec(compile(src, str(target), "exec"), g)
    except SystemExit as e:
        raise SystemExit(e.code)
    except AssertionError as e:
        if TRIPPED:
            print(f"\nDRIVES GAME INPUT: {target.name} -> {TRIPPED[0]}")
            print(f"  {e}")
            raise SystemExit(2)
        raise
    except Exception:
        if TRIPPED:
            print(f"\nDRIVES GAME INPUT: {target.name} -> {TRIPPED[0]}")
            raise SystemExit(2)
        traceback.print_exc()
        raise SystemExit(3)
