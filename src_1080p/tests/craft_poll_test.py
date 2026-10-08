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
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration as c
import craft
from PIL import Image, ImageDraw

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


def screen(material=None):
    image = Image.new("RGB", (1920, 1080), (20, 20, 24))
    if material:
        x0, y0, _x1, _y1 = c._box(tuple(c._REG["craft_material_line"]))
        ImageDraw.Draw(image).rectangle((x0 + 10, y0 + 8, x0 + 60, y0 + 18), fill=material)
    return image


check("the material line drawn in the game's red (fewer than 3 left) reads as short",
      c.craft_material_short(screen(material=(240, 0, 0))))
check("the material line in white (cores left) does not",
      not c.craft_material_short(screen(material=(224, 224, 224))))
check("an empty material line does not", not c.craft_material_short(screen()))
x0, y0, x1, y1 = c._box(tuple(c._REG["craft_material_line"]))
check("the red check reads only the Chaos/Divine line, below the 'Required Material' header (y 462-474)",
      y0 > 474, str((x0, y0, x1, y1)))

clock = {"t": 0.0}
slept = []
saved = (c.grab, c.craft_material_short, craft.time.monotonic, craft.time.sleep)


def run(reds, core=None, before=192):
    clock["t"] = 0.0
    slept.clear()
    seq = iter(reds)
    now = {}

    def grab(*a, **k):
        now["red"] = next(seq, reds[-1])
        return "screen"

    def sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    c.grab = grab
    c.craft_material_short = lambda image=None: now["red"]
    craft.time.monotonic = lambda: clock["t"]
    craft.time.sleep = sleep
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        try:
            return craft.await_drain(before, core=core, verbose=True), None, said.getvalue()
        except craft.Refused as exc:
            return None, str(exc), said.getvalue()


try:
    out, err, said = run([False, False, False, True], core="Chaos Core")
    check("it polls until the line turns red, then waits 5s and goes on to tab 4 and Complete All",
          out == 192 and err is None and slept[-1] == craft.FINISH_SETTLE == 5.0
          and slept[:-1] == [craft.CRAFT_POLL] * 3, f"{err} {slept}")
    check("the log says when the line turned red and that it waits before tab 4",
          "line turned red after 1.5s" in said and "waiting 5s before tab 4" in said, said.strip())
    out, err, said = run([False, True], core="Divine Stone")
    check("a Divine Stone craft finishes the same way and names Divine Stone",
          out == 192 and err is None and "Divine Stone line turned red" in said, said.strip())
    out, err, said = run([False])
    check("a line that never turns red is refused at craft_settle_max, with no Complete All",
          out is None and err and "still not red" in err and clock["t"] >= craft.SETTLE_MAX
          and craft.FINISH_SETTLE not in slept, str(err))
finally:
    (c.grab, c.craft_material_short, craft.time.monotonic, craft.time.sleep) = saved

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
