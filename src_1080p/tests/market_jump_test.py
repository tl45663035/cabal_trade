import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_market_jump_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import ledger
import open_agent_shop_premium
import row_model as m
from PIL import Image

ledger.DB = Path(os.environ["CABAL_SALES_DB"])
ledger._RUN = None
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


class Typed(Exception):
    pass


def typed(value, presses):
    raise Typed(value)


def replay(reads, markets, **listing):
    saved = (m._panel, m._asking, m._disputed, m._pick_suggestion, m.type_number, m.panel_holds_item,
             calibration.grab, calibration.ctrl_click, calibration.click, calibration.snap,
             open_agent_shop_premium.slot_point, dict(m.LAST_MARKET))
    m._panel = lambda: {"suggestion_boxes": [[30, 350, 160, 365], [30, 376, 160, 391]]}
    m._asking = lambda image, panel: reads
    m._disputed = lambda *a: False
    m._pick_suggestion = lambda point: None
    m.type_number = typed
    m.panel_holds_item = lambda *a, **k: False
    calibration.grab = lambda *a, **k: Image.new("RGB", (1920, 1080))
    calibration.ctrl_click = lambda *a, **k: None
    calibration.click = lambda *a, **k: None
    calibration.snap = lambda *a, **k: None
    open_agent_shop_premium.slot_point = lambda row, col: (1479, 236)
    m.LAST_MARKET.clear()
    m.LAST_MARKET.update({m.item_key(name): unit for name, unit in markets.items()})
    out = io.StringIO()
    price = None
    try:
        with contextlib.redirect_stdout(out):
            m.RowModel()._list_slot(wait_fill=False, **listing)
    except Typed as exc:
        price = exc.args[0]
    except m.NothingLoaded as exc:
        out.write(f"NothingLoaded: {exc}\n")
    finally:
        (m._panel, m._asking, m._disputed, m._pick_suggestion, m.type_number, m.panel_holds_item,
         calibration.grab, calibration.ctrl_click, calibration.click, calibration.snap,
         open_agent_shop_premium.slot_point, last) = saved
        m.LAST_MARKET.clear()
        m.LAST_MARKET.update(last)
    return price, out.getvalue()


def last(said):
    lines = [l for l in said.strip().splitlines() if l.strip()]
    return lines[-1].strip() if lines else ""


price, said = replay((1_000_000, 1_000_000, 1_000_000, 195_232),
                     {"Force Core(Highest)": 1_000_000, "Force Core Set (Highest)": 188_548},
                     row=1, col=2, floor=440_437, why="a Force Core Set (Highest) cost 440,437 this pass",
                     lands_in=11, expect_item="Force Core(Highest)", resupply_cost=440_437)
check("21:36 run: the 193 Force Core(Highest) bought at 440,437 and converted list at 999,999 against the "
      "1,000,000 market, not refused for the 195,232 week average", price == 999_999, last(said))

price, said = replay((1_000_000, 1_000_000, 1_000_000, 195_232), {},
                     row=1, col=2, floor=0, why="", unit_market=None, floor_each=0, floor_item=None,
                     rule_item="Force Core Set (Highest)")
check("21:48 repair: list-named of the same stack lists at 999,999 instead of stopping with nothing loaded",
      price == 999_999, last(said))

price, said = replay((999_998, 999_998, 999_998, 195_232),
                     {"Force Core(Highest)": 195_222, "Force Core Set (Highest)": 440_000},
                     row=1, col=1, floor=180_000,
                     why="a Force Core Set (Highest) as last bought costs 180,000",
                     expect_item="Force Core(Highest", listed_at=1_000_000, expect_qty=1, under=None,
                     special=False, expect_market=195_222, lands_in=14, overlap=True, weekly=False)
check("22:54 relist of row 14: the 1 Force Core(Highest) goes back at 999,997, 1 under the 999,998 market",
      price == 999_997, last(said))
check("22:54 relist of row 14: the 1 Force Core(Highest) is taken as itself, not as something else because "
      "the 195,222 the run noted from a 7-unit listing nine minutes earlier is far from 999,998",
      "this is not" not in said,
      next((l.strip() for l in said.splitlines() if "this is not" in l), ""))
check("22:54 relist of row 14: the stack is counted as the 1 the panel holds, never as 5 from its price",
      "of them and not the" not in said,
      next((l.strip() for l in said.splitlines() if "of them and not the" in l), ""))


def relist(reads, expected, name, qty, listed_at, floor):
    return replay(reads, {name: expected}, row=1, col=1, floor=floor, why=f"it cost {floor:,} each",
                  expect_item=name, listed_at=listed_at, expect_qty=qty, under=None, special=False,
                  expect_market=expected * m.pack_size(name), lands_in=5, overlap=True, weekly=False)


def renamed(said):
    return next((l.strip() for l in said.splitlines()
                 if "this is not" in l or "of them and not the" in l or "says '" in l), "")


price, said = relist((728_157, 728_157, 728_157, 728_157), 165_000, "Force Core(Highest", 87, 180_000, 172_727)
check("2 Oct relist of 87 Force Core(Highest): the market had jumped to 728,157; they go back at 728,156 as "
      "themselves, not as 4 of them", price == 728_156 and not renamed(said), renamed(said) or last(said))
price, said = relist((740_580, 740_580, 740_580, 726_252), 348_895, "Force Core (Ultimate", 214, 348_857,
                     328_500)
check("1 Oct relist of 214 Force Core (Ultimate): the market had jumped to 740,580; they go back at 740,579 as "
      "themselves, not as 2 of them", price == 740_579 and not renamed(said), renamed(said) or last(said))
price, said = relist((200_000, 200_000, 200_000, 310_068), 288_268, "Force Core (Ultimate", 1, 288_096, 274_274)
check("11 Oct relist of a Force Core (Ultimate) after its market fell to 200,000: it keeps its name and never "
      "goes under its 274,274 floor (it went out at 199,999 as 'Force Core Set (High)')",
      price is not None and price >= 274_274 and not renamed(said),
      f"typed {price:,}" if price else renamed(said) or last(said))
price, said = relist((95_998_784, 95_998_784, 95_998_784, 98_999_016), 70_000_000, "Siena's Unbinding Stone",
                     1, 95_998_784, 74_600_000)
check("24 Sep relist of a Siena's Unbinding Stone at its own 95,998,784: it goes back at 95,998,783 instead of "
      "stopping the run on a stale 70,000,000", price == 95_998_783 and not renamed(said),
      renamed(said) or last(said))
price, said = relist((746_000, 746_000, 746_000, 721_724), 760_706, "Chaos Core Set X 192", 1, 146_056_703,
                     140_160_000)
check("28 Sep: a raw Chaos Core loaded where the Chaos Core Set X 192 was expected goes out at no less than the "
      "Set's 140,160,000 floor: never under a floor, at worst too high to sell",
      price is not None and price >= 140_160_000, f"typed {price:,}" if price else last(said))

def weekly_relist(market, week, cores, listed_at, floor, noted):
    mode = {"now": "market"}
    saved = (m._panel, m._asking, m._disputed, m._pick_suggestion, m.type_number, m.panel_holds_item,
             calibration.grab, calibration.ctrl_click, calibration.click, calibration.snap, calibration.park,
             open_agent_shop_premium.slot_point, dict(m.LAST_MARKET))
    m._panel = lambda: {"suggestion_boxes": [[30, 350, 160, 365], [30, 376, 160, 391]],
                        "price_point": (90, 420)}
    m._asking = lambda image, panel: ((market, week, week, week) if mode["now"] == "weekly"
                                      else (market, market, market, week))
    m._disputed = lambda *a: False
    m._pick_suggestion = lambda point: mode.update(now="weekly" if point[1] < 370 else "market")
    m.type_number = typed
    m.panel_holds_item = lambda *a, **k: False
    calibration.grab = lambda *a, **k: Image.new("RGB", (1920, 1080))
    calibration.ctrl_click = calibration.click = calibration.snap = calibration.park = lambda *a, **k: None
    open_agent_shop_premium.slot_point = lambda row, col: (1479, 236)
    m.LAST_MARKET.clear()
    m.LAST_MARKET[m.item_key("Chaos Core Set")] = noted
    name = f"Chaos Core Set X {cores}"
    out = io.StringIO()
    price = None
    try:
        with contextlib.redirect_stdout(out):
            m.RowModel()._list_slot(1, 1, floor=floor, why=f"it cost {floor:,}", expect_item=name,
                                    listed_at=listed_at, expect_qty=1, under=None, special=False,
                                    expect_market=noted * cores, lands_in=3, overlap=True, weekly=True,
                                    wait_fill=False)
    except Typed as exc:
        price = exc.args[0]
    except Exception as exc:
        out.write(f"{type(exc).__name__}: {exc}\n")
    finally:
        (m._panel, m._asking, m._disputed, m._pick_suggestion, m.type_number, m.panel_holds_item,
         calibration.grab, calibration.ctrl_click, calibration.click, calibration.snap, calibration.park,
         open_agent_shop_premium.slot_point, last_markets) = saved
        m.LAST_MARKET.clear()
        m.LAST_MARKET.update(last_markets)
    return price, out.getvalue()


price, said = weekly_relist(437_400_000, 450_000_000, 600, 452_000_000, 432_600_000, 728_751)
check("a Chaos Core Set X 600 goes out at the 450,000,000 week average when that is above the 437,400,000 market",
      price == 450_000_000, last(said))
price, said = weekly_relist(456_000_000, 450_000_000, 600, 455_000_000, 432_600_000, 728_751)
check("a Chaos Core Set X 600 goes out at 455,999,999, 1 under the market, when the market is above the "
      "450,000,000 week average", price == 455_999_999, last(said))
price, said = weekly_relist(437_400_000, 450_000_000, 600, 452_000_000, 432_600_000, 195_222)
check("a Chaos Core Set X 600 still goes out when the run's noted Chaos price is stale; no check stops it",
      price == 450_000_000 and "Wrong" not in said, last(said))
price, said = weekly_relist(380_000_000, 390_000_000, 600, 400_000_000, 432_600_000, 728_751)
check("a Chaos Core Set X 600 never goes under its 432,600,000 floor, whatever the week average and market",
      price is not None and price >= 432_600_000, f"typed {price:,}" if price else last(said))

kept = []
saved_note = calibration.note_verified_cost
calibration.note_verified_cost = lambda name, each: kept.append((name, each)) or True
try:
    with contextlib.redirect_stdout(io.StringIO()):
        m.RowModel()._keep_verified_cost("Force Core(Highest)", 440_000, 889_999, True)
        m.RowModel()._keep_verified_cost("Force Core(Highest)", 434_681, 890_000, True)
finally:
    calibration.note_verified_cost = saved_note
check("12 Oct 23:33: the 440,000 and 434,681 paid for Force Core(Highest) go into the cost file though it listed at "
      "889,999 (they were left out, so the file kept 180,000)",
      kept == [("Force Core(Highest)", 440_000), ("Force Core(Highest)", 434_681)], str(kept))

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
