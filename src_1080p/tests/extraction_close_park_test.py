import contextlib
import io
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "cang"
os.chdir(SRC)
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC))
sys.argv = ["dungeon.py", "--config", "cang"]
import no_input
TRIPPED = no_input.arm()
import dungeon

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


@contextlib.contextmanager
def patched(*changes):
    saved = [(obj, name, getattr(obj, name)) for obj, name, _value in changes]
    try:
        for obj, name, value in changes:
            setattr(obj, name, value)
        yield
    finally:
        for obj, name, value in saved:
            setattr(obj, name, value)


steps = []
with patched((dungeon, "focus_game", lambda *a, **k: True),
             (dungeon.open_agent_shop_premium, "ensure_inventory_open", lambda *a, **k: None),
             (dungeon, "click", lambda x, y, settle=None: steps.append(("click", x, y))),
             (dungeon, "press_button", lambda name, *a, **k: steps.append(("button", name))),
             (dungeon, "wait_for", lambda window, word, timeout: (True, word)),
             (dungeon, "wait_timer", lambda *a, **k: steps.append(("wait",))),
             (dungeon.calibration, "park", lambda *a, **k: steps.append(("park",)))), \
        contextlib.redirect_stdout(io.StringIO()):
    dungeon.disenchant_all(True)

names = [step[0] if step[0] != "button" else step[1] for step in steps]
check("the mouse is parked after the extraction wait and before Close is read, so no item "
      "tooltip covers Close",
      "park" in names and names.index("wait") < names.index("park")
      < names.index("extraction_close"), str(names))

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
