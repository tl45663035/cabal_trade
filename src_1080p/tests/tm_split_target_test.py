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
from PIL import Image
import dungeon

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


split = Image.open(HERE / "fixtures" / "tm_split_target.png").convert("RGB")
check("Cang runs the shared TM, the BM cycle Trung runs",
      "bm_cycle" in dungeon.DUNGEONS["tm"]["sequence"]
      and "main_bm_recast" not in dungeon.DUNGEONS["tm"],
      str(dungeon.DUNGEONS["tm"]["sequence"][:8]))
check("in TM's BM every round presses 6, and 5 once per target",
      dungeon._DUNGEON["main_kill_loop"] == ["5", "6", "`"], str(dungeon._DUNGEON["main_kill_loop"]))

dungeon.BM_CYCLE.update(slot="main_bm", ends=float("inf"))
check("in the A4 BM the middle bar is read, as before", not dungeon.two_targets_now())
dungeon.BM_CYCLE["slot"] = "alternate_bm"
check("in the A5 BM the two target bars are read", dungeon.two_targets_now())
bars = dungeon.target_bars(split)
check("in TM's BM the split bar is read as two targets with their HP",
      len(bars) == 2 and all(dungeon.TARGET_HP.search(bar) for bar in bars), str(bars))

left_only = split.copy()
left_only.paste((0, 0, 0), (940, 40, 1340, 64))
bars = dungeon.target_bars(left_only)
check("with one half of the split bar left, that target is still read",
      len(bars) == 1 and dungeon.TARGET_HP.search(bars[0]), str(bars))

def search_keys(keys):
    pressed = []

    def watch(name, words, stop, first, failed):
        first.set()
        while not stop.is_set():
            dungeon.LOCK["reads"] += 1
            stop.wait(0.02)

    saved = (dungeon.tap, dungeon.focus_game, dungeon.watch_target, dict(dungeon.SEARCH_ATTACK))
    dungeon.tap = pressed.append
    dungeon.focus_game = lambda: True
    dungeon.watch_target = watch
    dungeon.SEARCH_ATTACK["keys"] = keys
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            dungeon.hunt("any monster", [], lambda since: None, "kill_bm", None,
                         search=0.3)
    finally:
        dungeon.tap, dungeon.focus_game, dungeon.watch_target = saved[:3]
        dungeon.SEARCH_ATTACK.update(saved[3])
    return pressed


dungeon.BM_CYCLE["slot"] = None
target, six = dungeon.KEYS[dungeon.TARGET_KEY], dungeon.KEYS["6"]
pressed = search_keys(list(dungeon.setting("tm", "search_attack")))
check("in Cang's TM every target press is followed by 6, so a target the bar can't be read for is still hit",
      pressed and pressed[::2] == [target] * (len(pressed) // 2) and pressed[1::2] == [six] * (len(pressed) // 2),
      str(pressed))
pressed = search_keys(list(dungeon.setting("fire", "search_attack")))
check("other dungeons only press the target key while searching",
      pressed and set(pressed) == {target}, str(pressed))

four = dungeon.KEYS["4"]
dungeon.BM_CYCLE.update(slot="main_bm", ends=float("inf"))
pressed = search_keys(list(dungeon.setting("tm", "search_attack")))
check("in the A4 BM (Battle Mode 3) the search follows each target press with 4, not 6",
      pressed and pressed[1::2] == [four] * (len(pressed) // 2), str(pressed))
check("in the A4 BM the kill loop presses 3 and 4 where the A5 BM presses 5 and 6",
      [dungeon.cycle_key(key) for key in dungeon._DUNGEON["main_kill_loop"]] == ["3", "4", "`"])
dungeon.BM_CYCLE["slot"] = "alternate_bm"
check("in the A5 BM (Battle Mode 2) the kill loop keeps 5 and 6",
      [dungeon.cycle_key(key) for key in dungeon._DUNGEON["main_kill_loop"]] == ["5", "6", "`"])
dungeon.BM_CYCLE["slot"] = None

fn = dungeon.DUNGEONS["fn"]
check("Cang's FN also runs the BM cycle, with no single-BM recast that leaves 30-45s gaps",
      "bm_cycle" in fn["sequence"] and "bm_aura" not in fn["sequence"] and "main_bm_recast" not in fn)
check("Cang's FN keeps the BM and its aura up through the room L boss",
      fn["sequence"].index("keep_aura") == fn["sequence"].index("move_to_L") + 1)

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
