import contextlib
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace

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
from PIL import Image

fails = 0
FRAME = HERE / "fixtures" / "extraction_pending.png"


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


def word_in(name, image):
    button = dungeon.BUTTONS[name]
    seen = dungeon.read_windows(image, [tuple(button["window"])])[0] or ""
    key = dungeon.row_model._key
    return key(button["word"]) in key(seen), seen


with Image.open(FRAME) as frame:
    image = frame.convert("RGB")
for name in ("extraction_pending", "extraction_receive", "extraction_pending_close"):
    found, seen = word_in(name, image)
    check(f"the {name} window reads its word on a real frame of the leftover Extract "
          f"Item window", found, repr(seen))
found, seen = word_in("item_extraction", image)
check("the item_extraction read does not take the leftover window for the Item "
      "Extraction dialog", not found, repr(seen))

steps = []
pending = {"up": True}


def fake_press(name, *a, **k):
    steps.append(name)
    if name == "item_extraction" and pending["up"]:
        raise RuntimeError("the item_extraction window never read 'Item Extraction'")
    if name == "extraction_receive":
        pending["up"] = False


def fake_wait_for(window, word, timeout):
    if word == dungeon.BUTTONS["extraction_pending"]["word"]:
        return pending["up"], word if pending["up"] else ""
    return True, word


no_cursor = SimpleNamespace(windll=SimpleNamespace(user32=SimpleNamespace(
    SetCursorPos=lambda *a: steps.append("cursor"))))
with patched((dungeon, "ctypes", no_cursor),
             (dungeon, "focus_game", lambda *a, **k: True),
             (dungeon.open_agent_shop_premium, "ensure_inventory_open", lambda *a, **k: None),
             (dungeon, "click", lambda x, y, settle=None: steps.append(("click", x, y))),
             (dungeon, "press_button", fake_press),
             (dungeon, "wait_for", fake_wait_for),
             (dungeon, "wait_timer", lambda *a, **k: None),
             (dungeon.calibration, "park", lambda *a, **k: None)), \
        contextlib.redirect_stdout(io.StringIO()):
    try:
        dungeon.disenchant_all(True)
        raised = None
    except RuntimeError as exc:
        raised = exc

check("a leftover Extract Item window does not fail the run", raised is None, str(raised))
names = [step if isinstance(step, str) else "disenchant" if step[1:] == tuple(dungeon.DISENCHANT_ICON)
         else f"click{step[1:]}" for step in steps]
check("Receive All, then Disenchant again, then the normal extraction",
      "extraction_receive" in names
      and names.index("extraction_receive") < len(names) - 1 - names[::-1].index("disenchant")
      < len(names) - 1 - names[::-1].index("item_extraction")
      and "extraction_agree" in names, str(names))

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
