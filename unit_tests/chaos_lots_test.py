import os
import pathlib
import sys
import tempfile

_DB = pathlib.Path(tempfile.gettempdir()) / f"chaos_lots_test_{os.getpid()}.db"
if _DB.exists():
    _DB.unlink()
os.environ["CABAL_SALES_DB"] = str(_DB)

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


check(str(m.SALES_DB) == str(_DB),
      f"the suite must be pointed at its own database, not the real ledger; "
      f"got {m.SALES_DB}")


def reset():
    conn = m.sales_db()
    conn.execute("DELETE FROM chaos_lots")
    conn.commit()
    conn.close()


SET = "Chaos Core Set"
K = 100

reset()
floor, lot = m.chaos_row_floor(SET, K, 74_000_000)
check(floor == 0 and lot == 0,
      f"a bundle with no recorded lot gets NO floor and is priced exactly as "
      f"it always was -- inventing one would either block a fine sale or wave "
      f"through a bad one. got {floor:,}")

reset()
m.note_chaos_lot(680_000, 70_000_000, K)
m.note_chaos_lot(690_000, 70_000_000, K)

m.reset_chaos_ranks()
floor_a, id_a = m.chaos_row_floor(SET, K, 70_000_000, rank=m.next_chaos_rank())
floor_b, id_b = m.chaos_row_floor(SET, K, 70_000_000, rank=m.next_chaos_rank())
check(floor_a == 680_000 * K,
      f"the first row priced takes the CHEAPEST lot, got {floor_a:,}")
check(floor_b == 690_000 * K,
      f"the second takes the next cheapest, got {floor_b:,}")
check(floor_a != floor_b,
      "the two must differ EVEN AT THE SAME LISTED PRICE -- that identical "
      "price is the case the old price key collapsed on, pinning both at the "
      "dearer floor")
check(id_a != id_b, "and they must be different lots")
check(floor_a == 68_000_000,
      f"the floor is the stack total, not the per-unit price; a per-unit floor "
      f"would be 680,000 and let {K} Sets go for one Set's money. "
      f"got {floor_a:,}")

check(floor_a + floor_b == (680_000 + 690_000) * K,
      "the floors must sum to the total cost outstanding, or some stock is "
      "unprotected however the sales fall")

third, _ = m.chaos_row_floor(SET, K, 70_000_000, rank=m.next_chaos_rank())
check(third == 690_000 * K,
      f"a third row against two lots takes the dearest, got {third:,}")
no_rank, _ = m.chaos_row_floor(SET, K, 70_000_000, rank=None)
check(no_rank == 690_000 * K,
      f"and so does a row priced with no rank at all, got {no_rank:,}")

check(m.clear_cheapest_chaos_lot() is True, "a sale retires a lot")
check([c for _, c in m.chaos_lots_cheapest_first()] == [690_000],
      f"the cheap lot goes and the dear one remains, got "
      f"{m.chaos_lots_cheapest_first()}")
m.reset_chaos_ranks()
survivor, _ = m.chaos_row_floor(SET, K, 70_000_000, rank=m.next_chaos_rank())
check(survivor == 690_000 * K,
      f"and the surviving row now floors at the dearer cost, got {survivor:,}")

m.note_chaos_lot(600_000, 62_000_000, K)
m.reset_chaos_ranks()
fresh, _ = m.chaos_row_floor(SET, K, 62_000_000, rank=m.next_chaos_rank())
check(fresh == 600_000 * K,
      f"a new cheaper bundle takes rank 0 and floors at what IT cost -- "
      f"otherwise cheap stock is held off the market by old dear stock. "
      f"got {fresh:,}")

m.reset_chaos_ranks()
check([m.next_chaos_rank() for _ in range(3)] == [0, 1, 2],
      "ranks are claimed in order")
m.reset_chaos_ranks()
check(m.next_chaos_rank() == 0,
      "and reset per batch -- without the reset the second batch starts at "
      "rank N and every row falls through to the dearest-lot fallback")

reset()
m.note_chaos_lot(700_000, 106_412_000, 148)
floor, _ = m.chaos_row_floor("Chaos Core Set X 148", 1, 106_412_000)
check(floor == 700_000 * 148,
      f"the pack marker in the name is the real unit count, not the qty the "
      f"panel reports; expected {700_000 * 148:,}, got {floor:,}")
check(floor != 700_000,
      "and it must NOT be one unit's cost -- that is the 148x under-floor")

floor, _ = m.chaos_row_floor("Chaos Core Set", 148, 106_412_000)
check(floor == 700_000 * 148,
      f"an unbundled row still floors on its own quantity, got {floor:,}")

for name, qty, want_units in (("Chaos Core Set X 270", 1, 270),
                              ("Chaos Core Set X 100", 1, 100),
                              ("Chaos Core SetX30", 1, 30),
                              ("Chaos Core Set", 1, 1)):
    floor, _ = m.chaos_row_floor(name, qty, 106_412_000)
    check(floor == 700_000 * want_units,
          f"{name!r} must floor over {want_units} unit(s), got "
          f"{floor // 700_000} ({floor:,})")


reset()
m.note_chaos_lot(680_000, 70_000_000, K)
for other in ("Force Core(Highest)", "Force Core Set (Highest)",
              "Epic Booster (Highest)", "Chaos Core", "", "Upgrade Core Set"):
    floor, _ = m.chaos_row_floor(other, K, 70_000_000)
    check(floor == 0,
          f"{other!r} is not a Chaos Core Set and must get no chaos floor "
          f"(got {floor:,}) -- every other item on the board is priced by the "
          f"rules it already had")

check(m.is_chaos_set("Chaos Core Set X 100") is True,
      "the pack marker must not stop a row being recognised -- every listed "
      "bundle carries one")
check(m.is_chaos_set("Chaos Core") is False,
      "and the Core is not the Set: they are opposite sides of this trade, and "
      "confusing them floors the thing being bought with the cost of the thing "
      "being sold")

reset()
_saved = m.sales_db
try:
    m.sales_db = lambda: None
    floor, lot = m.chaos_row_floor(SET, K, 70_000_000)
    check(floor == 0 and lot == 0,
          "with no ledger reachable the floor is absent, not an exception: "
          "bookkeeping must never be what stops an item being listed")
    m.note_chaos_lot(1, 1, 1)
    check(m.chaos_lots_cheapest_first() == [],
          "an unreachable ledger reports no lots rather than raising")
    check(m.clear_cheapest_chaos_lot() is False,
          "and retiring one simply reports that there was nothing to retire")
finally:
    m.sales_db = _saved

reset()
m.note_chaos_lot(0, 70_000_000, K)
m.note_chaos_lot(680_000, 0, K)
m.note_chaos_lot(-5, -5, K)
check(m.chaos_lots() == [],
      f"a zero or negative cost or price is a failed read, not a free bundle; "
      f"recording one would floor a real stack at nothing. got {m.chaos_lots()}")

import inspect

relist_src = inspect.getsource(m._relist_cycle)
check("chaos_row_floor(" in relist_src,
      "relist must look up the row's own chaos floor -- without it a bundle is "
      "repriced against the market alone and can be walked below cost")
check("cost_floor=row_floor" in relist_src,
      "and must PASS it to register_item; computing a floor and then not "
      "handing it over is the same as having none")
check("next_chaos_rank()" in relist_src,
      "and must CLAIM A RANK for it -- that is the identity now. Without it "
      "every chaos row falls through to the dearest-lot fallback and the "
      "per-row floors collapse back into the single shared one.")
check("is_chaos_set(target.name)" in relist_src,
      "and must only claim a rank for chaos rows, or a shelf of Force Cores "
      "burns through the ranks and every real chaos row gets the fallback")

try:
    _DB.unlink()
except OSError:
    pass

print(f"chaos_lots_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
