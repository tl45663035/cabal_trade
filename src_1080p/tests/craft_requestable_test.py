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


RECT = (0, 23, 1920, 1009)
saved = (c._client_rect, c.craft_window_open, c.grab, c.ocr, c.click, c.inventory_tab_point,
         c.time.sleep, c.craft_cores)
c._client_rect = lambda *a, **k: RECT
icon = c._centre(tuple(c._REG["craft_requestable"]))
check("the View only requestable products icon is the middle of the three at the top right of the "
      "recipe list, (500, 86) in the 2026-10-18 10:35 crash frame", icon == (500, 86), str(icon))

tiers_box = c._box(tuple(c._REG["craft_tiers"]))
recipes_box = c._box(tuple(c._REG["craft_recipes"]))
band_box = c._box(tuple(c._REG["craft_buttons"]))
tier_words = [("999", 90, (70, 151)), ("and", 90, (110, 151)), ("below", 90, (140, 151)),
              ("1000", 90, (60, 180)), ("-", 90, (90, 180)), ("1999", 90, (120, 180)),
              ("2000", 90, (60, 209)), ("-", 90, (90, 209)), ("2999", 90, (120, 209))]
every = tier_words + [
    ("[2000]", 90, (70, 239)), ("Sword", 90, (110, 239)), ("Damage", 90, (150, 239)),
    ("Amplifier", 90, (200, 239)), ("x", 90, (275, 239)), ("100", 90, (290, 239)),
    ("[2000]", 90, (70, 268)), ("Magic", 90, (110, 268)), ("Damage", 90, (150, 268)),
    ("Amplifier", 90, (200, 268)), ("x", 90, (275, 268)), ("100", 90, (290, 268)),
    ("[2000]", 90, (70, 297)), ("Material", 90, (115, 297)), ("Core", 90, (150, 297)),
    ("(Osmium)", 90, (190, 297)), ("x", 90, (230, 297)), ("20", 90, (245, 297))]
requestable = tier_words + [
    ("[2000]", 90, (70, 239)), ("Sword", 90, (110, 239)), ("Damage", 90, (150, 239)),
    ("Amplifier", 90, (200, 239)), ("x", 90, (275, 239)), ("100", 90, (290, 239)),
    ("[2500]", 90, (70, 268)), ("Chaos", 90, (110, 268)), ("Core", 90, (140, 268)),
    ("Set", 90, (165, 268)), ("(x3)", 90, (195, 268))]
band = [("Request", 90, (230, 730)), ("All", 90, (290, 730)),
        ("Complete", 90, (850, 729)), ("All", 90, (900, 729))]
state = {"lists": None, "shown": 0}
clicks = []


def fake_ocr(image, box, *a, **k):
    box = tuple(box)
    if box == tiers_box:
        return list(tier_words)
    if box == recipes_box:
        return list(state["lists"][min(state["shown"], len(state["lists"]) - 1)])
    if box == band_box:
        return list(band)
    return []


def fake_click(x, y, *a, **k):
    clicks.append((x, y))
    if (x, y) == icon:
        state["shown"] += 1


c.craft_window_open = lambda *a, **k: True
c.grab = lambda *a, **k: None
c.ocr = fake_ocr
c.click = fake_click
c.inventory_tab_point = lambda tab: (tab * 100, 181)
c.time.sleep = lambda s: None
c.craft_cores = lambda: ["Chaos Core"]
tier = (90, 209)
tab_one = (c.CRAFT_COLLECT_TAB * 100, 181)
try:
    state.update(lists=[every, requestable], shown=0)
    clicks.clear()
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        out = c.calibrate_craft(verbose=True)
    check("every recipe shown and no Chaos Core Set (x3): the requestable icon is clicked once, "
          "then the 2000-2999 tier again, then the Chaos Core Set line",
          clicks == [tier, icon, tier, (132, 268), tab_one, (875, 729)], str(clicks))
    check("the measured recipe is the Chaos Core Set line of the requestable list",
          out["recipe"] == [132, 268] and out["recipes"] == {"Chaos Core": [132, 268]}
          and out["tier"] == [90, 209], str(out))
    check("the log says the recipe was missing and the requestable icon was clicked",
          "no Chaos Core Set x3 in the list; View only requestable products at [500, 86], "
          "then the tier again" in said.getvalue(), said.getvalue())

    state.update(lists=[requestable], shown=0)
    clicks.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        out = c.calibrate_craft(verbose=True)
    check("the Chaos Core Set line already in the list: the requestable icon is never clicked",
          icon not in clicks and clicks == [tier, (132, 268), tab_one, (875, 729)], str(clicks))

    state.update(lists=[every, every], shown=0)
    clicks.clear()
    raised = None
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            c.calibrate_craft(verbose=True)
        except RuntimeError as error:
            raised = str(error)
    check("still no Chaos Core Set after the requestable icon: the same error as before, nothing written",
          raised is not None and raised.startswith(
              "no recipe under the 2000-2999 tier reads ['Chaos Core Set', 'x3']")
          and raised.endswith("Nothing written."), str(raised))
    check("the requestable icon is clicked only once, and nothing after the second tier click",
          clicks == [tier, icon, tier], str(clicks))
finally:
    (c._client_rect, c.craft_window_open, c.grab, c.ocr, c.click, c.inventory_tab_point,
     c.time.sleep, c.craft_cores) = saved

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
