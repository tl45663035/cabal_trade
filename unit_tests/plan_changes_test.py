"""The 2026-08-14 plan: no cross-run carry, real floors, honest row sizing.

DRIVES NOTHING. Pure arithmetic and in-memory model state.
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "plan_changes_test.db")

sys.argv = ["plan_changes_test"]
import trade  # noqa: E402

PASS = FAIL = 0


def check(ok, why):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {why}")


def rule(title):
    print("=" * 74)
    print(title)
    print("=" * 74)


def row(i, name, qty=250, price=500_000):
    return trade.Row(index=i, name=name, change=(0, 0), top=0, bottom=0,
                     action="change", price=price, qty=qty, status="On Sale")


def empty(i):
    return trade.Row(index=i, name="", change=(0, 0), top=0, bottom=0,
                     action="register", price=None, qty=None, status="")


CORE = "Force Core(High)"

rule("1. each launch is a separate run -- nothing survives it")

check(not hasattr(trade, "load_carried"),
      "there is no load_carried: a new process does not restore a previous "
      "one's carry")
check(not hasattr(trade, "_persist_carried"),
      "and nothing mirrors the carry to the ledger")
check(trade.carried_total() == 0,
      "a fresh import carries nothing")
check(not trade.chaos_stranded(),
      "and inherits no chaos strand")

# Within one run it still works -- that is the part worth keeping.
trade.note_carried_sets(3, 40)
check(trade.carried_sets(3) == 40,
      "in-run carry still tracks, because restock needs it to avoid "
      "re-buying stock it already paid for")
trade.note_carried_sets(3, 0)
check(trade.carried_total() == 0, "and clears")

rule("4. the model gets the floors the init price read established")

model = trade.ShopModel()
model.adopt([(i, row(i, CORE) if i == 1 else empty(i)) for i in range(1, 31)])
check((model.content(1) or {}).get("floor") == 0,
      "at seed time the row has floor 0 -- the walk runs before any buying "
      "or price read, which is what printed as '-' in the model table")

trade._MARKET_FLOORS[trade._floor_key(CORE)] = 250_000
changed = model.refresh_floors(verbose=False)
check(changed == 1, f"refresh_floors corrects one row (got {changed})")
check((model.content(1) or {}).get("floor") == 250_000,
      "and the row now carries the floor the market read established")
check(model.refresh_floors(verbose=False) == 0,
      "a second refresh changes nothing -- it is idempotent")

# An empty slot is not given a floor out of nowhere.
check(model.content(2) is None, "an empty slot stays empty")

rule("6. the row gate is sized on the purchase about to happen")

check(trade.restock_rows_needed(200) == 5,
      "with nothing known it still assumes the worst the limits permit -- an "
      "unknown overshoot is not a small one")
check(trade.restock_rows_needed(total=700) == 3,
      "the operator's example: a buy that will reach 700 needs 3 rows, "
      "not 5")
check(trade.restock_rows_needed(200, pack=28) == 1,
      "200 Sets in bundles of 28 needs one row, because the overshoot is one "
      "bundle rather than a 999 stack")
check(trade.restock_rows_needed(200, pack=250) <= 2,
      "and in bundles of 250, two")
check(trade.restock_rows_needed(200, pack=28)
      < trade.restock_rows_needed(200),
      "knowing the bundle size can only ever ask for FEWER rows than the "
      "blind worst case")
check(trade.restock_rows_needed(0, pack=1) >= 1,
      "never asks for zero rows")

rule("6b. the bundle size comes from the price read, not another search")

trade._MARKET_PACKS[trade._floor_key(CORE)] = 28
check(trade.market_pack(CORE) == 28,
      "market_pack reports what row 1 held when the market was priced")
check(trade.market_pack("Nothing Priced Here") == 0,
      "and 0 for an item the read never saw, which falls back to the worst "
      "case rather than to a guess")

rule("7. every table traversal announces itself")

for fn in ("shop_listing_pairs", "whole_shop_listings", "read_top_row",
           "goto_row", "scroll_to_end", "scroll_one", "scroll_chunk",
           "calibrate_scroll"):
    import inspect
    src = inspect.getsource(getattr(trade, fn))
    check("note_scan(" in src,
          f"{fn} announces its scan -- the operator counted two walks in "
          f"game and the log showed one")

before = trade.scan_count()
trade.note_scan("test", "detail")
check(trade.scan_count() == before + 1, "scans are counted")
trade.reset_walk_count()
check(trade.scan_count() == 0, "and reset with the cycle")

print()
print("-" * 74)
print(f"{PASS + FAIL} checks, {FAIL} failed")
sys.exit(1 if FAIL else 0)
