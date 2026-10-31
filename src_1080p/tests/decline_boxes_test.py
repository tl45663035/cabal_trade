import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_decline_boxes_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import driver
import dungeon

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


every = calibration.load_shared()["run"]["decline_boxes_every"]
check("bot declines the boxes every 5 rows", every == 5, str(every))
check("bot's close point is the bottom box's decline button", list(dungeon.NOTIFICATION_CLOSE) == [1903, 825],
      str(dungeon.NOTIFICATION_CLOSE))

clicks = []
saved_click, saved_focus = dungeon.click, dungeon.focus_game
dungeon.click = lambda x, y, settle: clicks.append((x, y, settle))
dungeon.focus_game = lambda *a, **k: True
try:
    dungeon.close_notifications(verbose=False)
finally:
    dungeon.click, dungeon.focus_game = saved_click, saved_focus
gap = dungeon.NOTIFICATION_GAP
check("one decline clicks the point 3 times, a gap after all but the last",
      clicks == [(1903, 825, gap), (1903, 825, gap), (1903, 825, 0.0)], str(clicks))

declined = []
saved_close = dungeon.close_notifications
dungeon.close_notifications = lambda verbose=True: declined.append(True)


class Model:
    def home(self, verbose=True):
        pass


names = ("shop_ready", "world_boss_turn", "dungeon_pause", "relist_one", "board_trace", "pending_holds_stock",
         "urgent_check", "special_turn", "special_pass", "stock_rows")
saved = {name: getattr(driver, name) for name in names}
walked = []
driver.shop_ready = lambda *a, **k: None
driver.world_boss_turn = lambda *a, **k: False
driver.dungeon_pause = lambda *a, **k: False
driver.relist_one = lambda model, index, **k: walked.append(index) or (index % 3 == 0)
driver.board_trace = lambda *a, **k: None
driver.pending_holds_stock = lambda: False
driver.urgent_check = driver.special_turn = driver.special_pass = lambda *a, **k: None
driver.stock_rows = lambda last: ()
try:
    driver.relist_pass(Model(), 1, 30, verbose=False)
    check("a 30-row walk declines after rows 5, 10, 15, 20, 25 and 30", len(declined) == 6 and len(walked) == 30,
          f"{len(declined)} declines, {len(walked)} rows")
    declined.clear()
    walked.clear()
    driver.relist_pass(Model(), 1, 30, verbose=False, start=12)
    check("a walk carried on at row 12 counts from there: 19 rows, 3 declines", len(declined) == 3 and len(walked) == 19,
          f"{len(declined)} declines, {len(walked)} rows")
    declined.clear()
    walked.clear()
    driver.relist_pass(Model(), 1, 30, verbose=False, collect_only=True)
    check("the collect walk declines too", len(declined) == 6, f"{len(declined)} declines")
    declined.clear()
    saved_load = calibration.load_shared

    def off():
        shared = saved_load()
        shared["run"]["decline_boxes_every"] = 0
        return shared
    calibration.load_shared = off
    try:
        driver.relist_pass(Model(), 1, 30, verbose=False)
    finally:
        calibration.load_shared = saved_load
    check("0 turns the decline off", declined == [], f"{len(declined)} declines")
    saved_point = dungeon.NOTIFICATION_CLOSE
    dungeon.NOTIFICATION_CLOSE = None
    try:
        driver.relist_pass(Model(), 1, 30, verbose=False)
    finally:
        dungeon.NOTIFICATION_CLOSE = saved_point
    check("no close point in the config means no decline", declined == [], f"{len(declined)} declines")
finally:
    for name, value in saved.items():
        setattr(driver, name, value)
    dungeon.close_notifications = saved_close

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
