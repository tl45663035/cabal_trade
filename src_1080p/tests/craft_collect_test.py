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

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


tiers_box = c._box(tuple(c._REG["craft_tiers"]))
recipes_box = c._box(tuple(c._REG["craft_recipes"]))
band_box = c._box(tuple(c._REG["craft_buttons"]))
read = {
    tiers_box: [("2000", 90, (60, 209)), ("-", 90, (90, 209)), ("2999", 90, (120, 209))],
    recipes_box: [("[2500]", 90, (70, 268)), ("Chaos", 90, (110, 268)), ("Core", 90, (140, 268)),
                  ("Set", 90, (165, 268)), ("(x3)", 90, (195, 268))],
    band_box: [("Request", 90, (230, 730)), ("All", 90, (290, 730)),
               ("Complete", 90, (850, 729)), ("All", 90, (900, 729))],
}
clicks = []
saved = (c.craft_window_open, c.grab, c.ocr, c.click, c.inventory_tab_point, c.time.sleep, c.craft_cores)
c.craft_window_open = lambda *a, **k: True
c.grab = lambda *a, **k: None
c.ocr = lambda image, box, *a, **k: list(read.get(tuple(box), []))
c.click = lambda x, y, *a, **k: clicks.append((x, y))
c.inventory_tab_point = lambda tab: (tab * 100, 181)
c.time.sleep = lambda s: None
c.craft_cores = lambda: ["Chaos Core"]
try:
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        out = c.calibrate_craft(verbose=True)
    tab_one = (c.CRAFT_COLLECT_TAB * 100, 181)
    complete = tuple(out["complete"])
    check("the collect tab is tab 1, from config.json's game_facts", c.CRAFT_COLLECT_TAB == 1, str(c.CRAFT_COLLECT_TAB))
    check("at launch, after the Chaos Core Set recipe is chosen, tab 1 is shown and then Complete All is clicked",
          clicks[-2:] == [tab_one, complete] and clicks.index(tuple(out["recipe"])) < len(clicks) - 2, str(clicks))
    check("the craft window's measurements come back as before",
          out["recipe"] == [132, 268] and out["request"] == [260, 730] and out["complete"] == [875, 729], str(out))
    check("the log says why", "Sets an earlier craft left in the queue land in tab 1" in said.getvalue(),
          said.getvalue().strip().splitlines()[-1])
finally:
    c.craft_window_open, c.grab, c.ocr, c.click, c.inventory_tab_point, c.time.sleep, c.craft_cores = saved

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
