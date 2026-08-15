"""Chaos cost basis is what THIS run paid, never a previous run's lots.

On 2026-08-15 the live log reported:

    Chaos Core Set X 264 x1 | sold 190,071,287 / cost 661,431 | made +15,453,287

The arithmetic was right and the basis was not. Chaos Cores were bought that
morning at 694,017 and that afternoon at 690,000, and nothing in hours had
cost 660,000 -- those were lots from EARLIER LAUNCHES, still outstanding in
chaos_lots because that table had no run column and every reader took the
whole table. A Set that cost 690,000 to make was billed against 660,000 and
reported 8%; on what it actually cost to replace, the margin was about half.

This is the same rule as the deleted `carried` table: each launch is a
separate run and inherits nothing from the last one.

DRIVES NOTHING. A scratch ledger, never sales.db.
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_DB = Path(tempfile.gettempdir()) / "lot_scope_test.db"
if _DB.exists():
    _DB.unlink()

# A ledger written by the OLD schema, holding a previous launch's cheap lot.
# Built before trade is imported, so the migration is exercised for real
# rather than a fresh table being created with the column already present.
_con = sqlite3.connect(_DB)
_con.executescript("""
    CREATE TABLE chaos_lots (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        unit_cost    INTEGER NOT NULL,
        listed_price INTEGER NOT NULL,
        qty          INTEGER NOT NULL,
        created      TEXT    NOT NULL
    );
""")
_con.execute("INSERT INTO chaos_lots (unit_cost, listed_price, qty, created) "
             "VALUES (660000, 719997, 255, 'a previous launch')")
_con.commit()
_con.close()

os.environ["CABAL_SALES_DB"] = str(_DB)
sys.argv = ["lot_scope_test"]
import trade as m  # noqa: E402

PASS = FAIL = 0


def check(ok, why):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {why}")


def section(title):
    print("=" * 74)
    print(title)
    print("=" * 74)


section("an old ledger is migrated, not left broken")

# Touch the ledger first: the schema block (and therefore the migration) runs
# when sales_db() opens a connection, not at import. Checking the column
# before anything opened the file reads the OLD schema and fails -- which is
# a fact about when the migration happens, worth stating rather than dodging.
_ = m.chaos_lots()

cols = [r[1] for r in sqlite3.connect(_DB).execute(
    "PRAGMA table_info(chaos_lots)")]
check("run" in cols,
      f"chaos_lots gained its `run` column on open, got {cols}")
# This is the sharp end: note_chaos_lot swallows exceptions so bookkeeping
# cannot block a listing. Without the migration every insert would fail
# SILENTLY and the chaos floors would quietly stop existing.
check("qty" in cols and "unit_cost" in cols,
      "and kept the columns it already had")

section("a previous launch's lots are invisible")

check(m.chaos_lots_cheapest_first() == [],
      f"the 660,000 lot from an earlier launch is not offered as a basis, "
      f"got {m.chaos_lots_cheapest_first()}")
check(m.chaos_lots() == [],
      f"nor by the reporting view, got {m.chaos_lots()}")

section("what this run paid is what this run is billed")

m.note_chaos_lot(690_000, 719_997, 264)
lots = m.chaos_lots_cheapest_first()
check(len(lots) == 1, f"this run's lot is the only one, got {lots}")
check(lots and lots[0][1] == 690_000,
      f"at the 690,000 it really paid, got {lots}")

# The live figures, recomputed on the right basis.
SOLD, QTY = 190_071_287, 264
honest = SOLD - QTY * 690_000
stale = SOLD - QTY * 660_000
check(honest == 7_911_287, f"the real margin is {honest:,}")
check(stale == 15_831_287, f"the stale basis claimed {stale:,}")
check(stale > honest * 2 - 1,
      f"which is about DOUBLE -- {stale:,} against {honest:,} -- so this was "
      f"never a rounding difference")

section("retiring a lot cannot reach into another run")

m.note_chaos_lot(700_000, 730_000, 100)
before = len(m.chaos_lots_cheapest_first())
m.clear_cheapest_chaos_lot()
after = m.chaos_lots_cheapest_first()
check(len(after) == before - 1, "a sale retires exactly one of this run's lots")
check(after and after[0][1] == 700_000,
      f"the cheapest went and the dearer stayed, got {after}")

# And the previous launch's row is still sitting there untouched -- scoped
# out, not deleted. Deleting another run's bookkeeping would be worse.
left = sqlite3.connect(_DB).execute(
    "SELECT COUNT(*) FROM chaos_lots WHERE run IS NULL").fetchone()[0]
check(left == 1,
      f"the old launch's row is ignored, NOT deleted, got {left}")

print()
print("-" * 74)
print(f"{PASS + FAIL} checks, {FAIL} failed")
sys.exit(1 if FAIL else 0)
