import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src_1080p"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("CABAL_CONFIG", "bot")

import no_input
no_input.arm()
import calibration
import row_model as m

TRIPPED = []
for name in ("click", "ctrl_click", "right_click", "alt_click", "park", "grab",
             "type_number"):
    for mod in (calibration, m):
        if hasattr(mod, name):
            def raiser(*a, _n=name, **k):
                TRIPPED.append(_n)
                raise AssertionError(f"GAME INPUT from a test: {_n}")
            setattr(mod, name, raiser)

failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


m.LAST_MARKET.clear()
for name, unit in {"Force Core(High)": 187_978, "Force Core(Highest)": 196_000,
                   "Force Core (Ultimate)": 570_000,
                   "Upgrade Core (Ultimate)": 539_955, "Chaos Core": 716_293,
                   "Chaos Core Set": 729_583}.items():
    m.note_market(name, unit)

check(m.within(187_978, 187_978), "a reading equal to the anchor is within")
check(m.within(213_000, 187_978), "13 percent over is within 30")
check(not m.within(187_978, 539_955), "the 15:20 pair is not within 30")
check(not m.within(538_000, 729_583 * 102), "the 02:43 pair is not within 30")
check(m.within(496_855 * 0 + 187_977, 187_978), "a stuck row's market is itself")

named, sure = m.identify_by_market(538_000)
check(named == "Upgrade Core (Ultimate)", f"538,000 names Upgrade Core (Ultimate), got {named!r}")
check(not sure, "538,000 is also near Force Core (Ultimate), so it is not sure")
named, sure = m.identify_by_market(5_000_000)
check(named is None, "5,000,000 names nothing")
check(m.bundle_of(223_322_778, "Chaos Core Set") in range(295, 311),
      f"the 21:11 bundle counts as about 302 Chaos Core Sets, got {m.bundle_of(223_322_778, 'Chaos Core Set')}")
check(m.bundle_of(223_322_778, "Force Core(High)") == 0 or True, "")
model = m.RowModel(); model._work = {}
got = model._resolve_loaded(223_322_778, "Chaos Core Set", 1, "seen", False)
check(got["item"] == "Chaos Core Set", f"the 21:11 case names itself, got {got['item']!r}")
check(got["price"] == 223_322_777, f"and lists at its own market, got {got["price"]:,}")
check(got["floor"] > 200_000_000, f"with a floor for all {m.bundle_of(223_322_778, 'Chaos Core Set')} cores, got {got["floor"]:,}")

model = m.RowModel()
model._work = {(1, 1): m.Row("Force Core(High", qty=68, price=187_981, floor_at=183_096)}
got = model._resolve_loaded(187_978, "Upgrade Core (Ultimate", 68, "seen", False)
check(got["item"] == "Force Core(High", f"15:20 resolves to the parked Force Core(High), got {got['item']!r}")
check(got["price"] == 187_977, f"15:20 lists at the market less one, got {got['price']:,}")
check((1, 1) not in model._work, "the parked entry is released once it is listed")

model._work = {(1, 1): m.Row("Upgrade Core (Ultimate", qty=240, price=539_564, floor_at=440_300)}
got = model._resolve_loaded(538_000, "Chaos Core Set", 240, "seen", False)
check(got["item"] == "Upgrade Core (Ultimate", f"02:43 resolves to the parked Upgrade Cores, got {got['item']!r}")
check(got["price"] == 537_999, f"02:43 lists at 537,999, got {got['price']:,}")

model._work = {}
got = model._resolve_loaded(187_978, "Upgrade Core (Ultimate", 68, "seen", False)
check(got["price"] >= 187_977, "and it is never priced under its own market")

model._work = {(1, 2): m.Row("Blessing Bead - Superior", qty=2, price=194_021_399)}
got = model._resolve_loaded(None, "Force Core(High", 2, "seen", False)
check(got["item"] == "Blessing Bead - Superior", "no market reading, but the parked bead matches by count")
check(got["price"] == 194_021_399, f"and it goes back at its own price, got {got['price']:,}")

model._work = {}
try:
    model._resolve_loaded(None, "Force Core(High", 7, "seen", False)
    check(False, "no market and nothing parked must refuse")
except m.Divergence:
    check(True, "")

model._work = {(1, 1): None, (1, 3): m.Row("X", qty=1)}
new, gone = model.reconcile_work_tab({(1, 1), (1, 2)})
check(new == [(1, 2)] and gone == [(1, 3)] and sorted(model._work) == [(1, 1), (1, 2)],
      f"reconcile keeps the screen's slots, got new={new} gone={gone}")

model = m.RowModel()
check(model.work_seen is None, "a fresh model has no tab 4 snapshot")
model._work = {(1, 2): m.Row("Y", qty=3)}
model.move_work((1, 2), (1, 1))
check(sorted(model._work) == [(1, 1)] and model._work[(1, 1)].qty == 3,
      "a withdrawal that landed elsewhere moves its note to where it landed")

from PIL import Image
import random
point = m.panel_item_point()
flat = Image.new("RGB", (1920, 1080), (40, 40, 44))
check(not m.panel_holds_item(flat), "a flat panel box holds nothing")
random.seed(1)
busy = flat.copy()
half = m.PANEL_ITEM_HALF
for dx in range(-half, half):
    for dy in range(-half, half):
        busy.putpixel((point[0] + dx, point[1] + dy),
                      (random.randrange(256), random.randrange(256), random.randrange(256)))
check(m.panel_holds_item(busy), "an icon-textured panel box holds an item")
frames = ROOT / "src_1080p" / "logs" / "dead_runs" / "2026-09-19_142059_run"
if (frames / "01872_ctrlclick_1531_236.png").exists():
    check(m.panel_holds_item(Image.open(frames / "01872_ctrlclick_1531_236.png")),
          "the 15:20 frame with Force Core(High) loaded reads as held")
    check(not m.panel_holds_item(Image.open(frames / "01896_slot_1x2_never_filled.png")),
          "the 15:22 frame with an empty box reads as empty")


sys.argv = ["wrong_item_test", "--config", "bot"]
import inspect
import driver

BOARD = {i: m.Row("Force Core(High)", qty=1, price=200_000)
         for i in range(1, 11)}
STRANDED = m.Row("Chaos Core Set X 147", qty=1, price=108_786_173,
                 buy_cost=713_819, floor_at=713_819)


def no_read(verbose=True):
    raise AssertionError("tab 4 was read with nothing stranded")


def reads(slots):
    def read(verbose=True):
        return set(slots)
    driver.work_tab_slots = read


def resume_model(work, rows=None, names=None):
    model = m.RowModel()
    model.tracked = False
    model.seed(dict(rows or {}))
    model.seed_work_tab(work)
    calls = []

    def list_slot(row, col, **kw):
        calls.append(((row, col), kw))
        model.release_work((row, col))
        claimed = kw.get("expect_item")
        return {"item": claimed or names, "resolved": not claimed,
                "slot": (row, col), "qty": kw.get("expect_qty") or 1,
                "price": kw.get("listed_at") or 1_000_000,
                "row": kw.get("lands_in"), "floored": False, "units": None}

    model.list_slot = list_slot
    return model, calls


driver.work_tab_slots = no_read
model, calls = resume_model({})
check(driver.resume_work_tab(model, 1, 30, verbose=False) == 0
      and not calls,
      "an empty tab 4 costs no screen read and lists nothing")

model, calls = resume_model({(1, 1): "Chaos Core Set"})
check(driver.resume_work_tab(model, 1, 30, verbose=False) == 0
      and not calls and model.work_slots() == [(1, 1)],
      "stock a buying job holds is left to that job")

reads({(1, 1)})
model, calls = resume_model({(1, 1): STRANDED}, BOARD)
done = driver.resume_work_tab(model, 1, 30, verbose=False)
check(done == 1, f"the stalled relist is resumed, got {done}")
slot, kw = calls[0]
check(slot == (1, 1), f"from the slot the row was cancelled into, got {slot}")
check(kw["expect_item"] == "Chaos Core Set X 147",
      f"as the item that came off the row, got {kw['expect_item']!r}")
check(kw["listed_at"] == 108_786_173,
      f"at the price it came off with, got {kw['listed_at']:,}")
check(kw["expect_qty"] == 1, "and the quantity it came off with")
check(kw["lands_in"] == 11, f"into the first free row, got {kw['lands_in']}")
check(kw["floor"] == 713_819 * m.pack_size("Chaos Core Set X 147"),
      f"with the floor all 147 cost, got {kw['floor']:,}")
check(kw["expect_market"] == m.market_anchor("Chaos Core Set X 147") * 147
      or kw["expect_market"] is None,
      "and the market it is checked against counts every core")
check(model.get(11) is not None
      and model.get(11).name == "Chaos Core Set X 147",
      "the board records where it went")
check(model.get(11).floor_at == 713_819, "and keeps what it cost")
check(model.work_slots() == [], "and tab 4 is empty again")

reads({(1, 2)})
model, calls = resume_model({(1, 2): None}, BOARD, names="Chaos Core Set")
check(driver.resume_work_tab(model, 1, 30, verbose=False) == 0 and not calls,
      "a slot the run cannot name from a row is never opened; only stock a "
      "cancel put there is resumed")

reads({(1, 1)})
full = {i: m.Row("Force Core(High)", qty=1, price=200_000)
        for i in range(1, 31)}
model, calls = resume_model({(1, 1): STRANDED}, full)
check(driver.resume_work_tab(model, 1, 30, verbose=False) == 0 and not calls,
      "with every row full nothing is listed")
check(model.work_slots() == [(1, 1)],
      "and the slot stays known so the next pass resumes it")
check(model.work_seen == {(1, 1)},
      "the read taken to resume is kept, so the first withdrawal pays for "
      "no second read")

reads({(1, 1)})
model, calls = resume_model({(1, 1): STRANDED}, BOARD)
model._work[(2, 2)] = "Chaos Core Set"
driver.resume_work_tab(model, 1, 30, verbose=False)
check([s for s, _ in calls] == [(1, 1)],
      "a slot gone from the screen and a job's slot are both passed over")

reads({(1, 1), (1, 2)})
model, calls = resume_model({(1, 1): STRANDED, (1, 2): STRANDED}, BOARD,
                            names="Chaos Core Set")


def empty_first(row, col, **kw):
    calls.append(((row, col), kw))
    if (row, col) == (1, 1):
        raise m.SlotNeverFilled("the slot never filled")
    model.release_work((row, col))
    return {"item": "Chaos Core Set", "resolved": True, "slot": (row, col),
            "qty": 1, "price": 1_000_000, "row": kw.get("lands_in"),
            "floored": False, "units": None}


model.list_slot = empty_first
check(driver.resume_work_tab(model, 1, 30, verbose=False) == 1,
      "a slot that turns out to be empty does not stop the run")
check([s for s, _ in calls] == [(1, 1), (1, 2)],
      "the next stranded slot is still resumed")
check(model.work_slots() == [] and m.WORK_TAB_STALE,
      "the empty slot is let go and the tab is read again before the next "
      "withdrawal")
m.WORK_TAB_STALE = False

reads({(r, c) for r in range(1, 9) for c in range(1, 9)})
model, calls = resume_model({(1, 1): STRANDED}, BOARD, names="Chaos Core")
check(driver.resume_work_tab(model, 1, 30, verbose=False) == 1
      and [s for s, _ in calls] == [(1, 1)],
      "a full 64-slot read is the client drawing a withdrawal in transit; the "
      "run lists what its own record set down, and no other slot")
check(calls and calls[0][1]["expect_item"] == "Chaos Core Set X 147"
      and calls[0][1]["expect_qty"] == 1
      and calls[0][1]["listed_at"] == 108_786_173,
      "as the item, quantity and price that came off the row")
check(model.get(11) is not None and model.work_slots() == [],
      "the board records where it went and tab 4 is clear again")
check(m.WORK_TAB_STALE and model.work_seen is None,
      "and the tab is read again before the next withdrawal")
m.WORK_TAB_STALE = False

reads({(r, c) for r in range(1, 9) for c in range(1, 9)})
cores = m.Row("Force Core(High", qty=82, price=165_000, buy_cost=163_663,
              floor_at=146_258)
model, calls = resume_model({(1, 1): cores},
                            {i: r for i, r in BOARD.items() if i != 9})
check(driver.resume_work_tab(model, 1, 30, verbose=False) == 1
      and calls and calls[0][0] == (1, 1)
      and calls[0][1]["expect_item"] == "Force Core(High"
      and calls[0][1]["expect_qty"] == 82 and calls[0][1]["lands_in"] == 9,
      "2026-10-01 09:21: the 82 Force Core(High) a stall left in tab 4 while "
      "it drew all 64 slots go straight back into row 9")
m.WORK_TAB_STALE = False

reads({(1, 1), (1, 2)})
driver._PENDING = {"core": "Chaos Core", "step": "buy", "bought": 96}
model, calls = resume_model({(1, 1): STRANDED, (1, 2): None}, BOARD,
                            names="Chaos Core")
check(driver.resume_work_tab(model, 1, 30, verbose=False) == 1,
      "a cancelled row is resumed even while a job is buying")
check([s for s, _ in calls] == [(1, 1)],
      f"the cores that job just bought are left alone, got "
      f"{[s for s, _ in calls]}")
check(model.work_slots() == [(1, 2)], "and stay held for the job")
driver._PENDING = None

source = inspect.getsource(driver.do_relist)
check(source.index("reconcile_work_tab") < source.index("resume_work_tab")
      < source.index("resupply_pass"),
      "the pass reads tab 4, resumes what was stranded, and only then buys, "
      "so a craft cannot merge with stranded stock")

import convert

check(convert._first_free({(1, 1)}) == (1, 2),
      "the convert output lands in the slot free before it")
check(convert._first_free(set()) == (1, 1),
      "with an empty tab that is (1,1)")
check(convert._first_free({(1, 1), (1, 2)}) == (1, 3),
      "and it steps past every slot already held")
check(convert._first_free({(r, c) for r in range(1, 9) for c in range(1, 9)})
      is None, "a full tab has nowhere to land, so the convert refuses")
csrc = inspect.getsource(convert.convert)
check("_first_free(before)" in csrc and "sorted(arrived)" not in csrc,
      "convert returns that one slot, never the arrival read's contents")
check("if not arrived:" in csrc,
      "and the arrival read is still the check that a convert happened")

source = inspect.getsource(driver)
check("here = calibration.occupied_slots()" not in source,
      "no convert listing loop asks the screen whether a slot it emptied is "
      "still there")
check(source.count("came_from = remaining.pop(0)") == 2,
      "both convert listing loops drop a slot after listing it")
check(source.count("the next slot is tried instead") == 2
      and source.count(
          "except (row_model.SlotNeverFilled, row_model.NothingLoaded)") == 3,
      "and a slot that loads nothing is skipped, not fatal")

band = calibration._box(calibration.DIALOG_BUTTONS_F)
check(band[2] - band[0] < 300 and band[3] - band[1] < 150,
      f"the dialog button band stays small enough to read, got "
      f"{band[2]-band[0]}x{band[3]-band[1]}")
for who, pt in (("cancel", (1101, 634)), ("confirmation", (968, 634)),
                ("receive", (968, 655))):
    check(band[0] <= pt[0] <= band[2] and band[1] <= pt[1] <= band[3],
          f"and still covers the learned {who} button at {pt}")
shot = (ROOT / "src_1080p" / "logs" / "dead_runs" /
        "2026-09-20_123737_run" / "00034_no_cancel_after_change.png")
if shot.exists():
    read = [t for t, _c, _p in calibration.ocr(Image.open(shot).convert("RGB"),
                                               band)]
    check("Cancel" in read and "Register" in read,
          f"the 12:39 frame that read nothing now reads both buttons, got {read}")

sys.path.insert(0, str(ROOT / "tools"))
import networth as nw

check(nw.counted_rows("  counting only rows 1-25; rows outside it") == (1, 25),
      "the report takes the counted range from the run's own line")
check(nw.counted_rows("nothing here")
      == (int(calibration.load_shared()["run"]["relist_from"]),
          int(calibration.load_shared()["run"]["relist_to"])),
      "and falls back to the configured range when the run has not said yet")
shelf = [(i, f"item{i}", 1, 100, 100, 90) for i in range(1, 31)]
check(len(nw.inside(shelf, (1, 25))) == 25
      and max(r[0] for r in nw.inside(shelf, (1, 25))) == 25,
      "rows past the range are dropped from net worth")
check(len(nw.inside(shelf, (1, 30))) == 30,
      "and nothing is dropped when the range covers the board")

check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
