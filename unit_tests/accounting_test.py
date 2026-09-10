import os
import sys
import tempfile
from pathlib import Path as _Path

sys.path.insert(0, r"C:\Users\Trung\Cabal")
os.environ["CABAL_SALES_DB"] = str(
    _Path(tempfile.gettempdir()) / "cabal_accounting_test.db")

import trade as m

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


NO_EVIDENCE = [
    (39_904_780, 469_468, 0, None),
    (117_367_000, 469_468, 0, None),
    (7_511_488, 469_468, 0, None),
    (52_499_750, 209_999, 0, None),
    (21_209_899, 209_999, 0, None),
]
for proceeds, price, still, units in NO_EVIDENCE:
    why = m.sale_rejection(proceeds, price, still, units)
    check(why == "",
          f"{proceeds:,} at {price:,} with no size evidence must be accepted "
          f"-- a bound of 0 rejects every possible figure. got {why!r}")

recovered = sum(p for p, *_ in NO_EVIDENCE)
check(recovered > 200_000_000,
      f"the cases in this file should account for real money, got "
      f"{recovered:,}")

MUST_REFUSE = [
    (1_662_294_744, 106_000_000, 1, None,
     "the VIP 1,662,294,744 -- 15.68 units, not a whole number"),
    (876_764_416, 54_797_776, 8, 8,
     "Epic Booster: 16 units from a stack that held 8"),
    (999_999_999_999, 100_000, 0, None,
     "absurd proceeds with NO bound available -- the plausibility cap"),
    (52_514_000, 238_700, 2, 2,
     "220 units where TWO independent readings both say the stack held 2"),
]
for proceeds, price, still, units, label in MUST_REFUSE:
    why = m.sale_rejection(proceeds, price, still, units)
    check(why != "", f"MUST still refuse: {label}")

check(m.sale_rejection(52_499_750, 209_999, 0, 250) == "",
      "a full 250-unit sale against a 250 registration is accepted")
check(m.sale_rejection(2_099_990, 209_999, 240, 250) == "",
      "and a partial one -- 10 sold, 240 left on the row")

check(m.sale_rejection(100_001, 100_000, 999, 999) != "",
      "a non-whole number of units is refused however generous the bound")


_saved_db = m.sales_db


class FakeDB:

    def __init__(self, purchases, sales):
        self.purchases, self.sales = purchases, sales

    def execute(self, sql, *a):
        if "FROM purchases" in sql:
            return list(self.purchases)
        return list(self.sales)

    def close(self):
        pass


try:
    m.sales_db = lambda: FakeDB(
        purchases=[("2026-08-01T00:00:00",
                    "Force Core Set (Highest) X 100", 100_000, 100)],
        sales=[("2026-08-02T00:00:00", "Force Core(Highest)", 250, 500_000)])
    _basis = m.purchase_cost_basis
    m.purchase_cost_basis = lambda name: 1_000
    cogs, priced, unpriced, spare = m.cost_of_goods_sold()
    check(priced == 100,
          f"only the 100 units a purchase covers may be priced, got {priced}")
    check(unpriced == 150,
          f"and the other 150 must be reported as unpriced, got {unpriced}")
    check(cogs == 100_000,
          f"cost is 100 x 1,000, not 250 x 1,000 -- charging the 150 invents "
          f"150,000 Alz of cost. got {cogs:,}")
    check(spare == 300_000,
          f"150 of 250 units uncosted means 300,000 of the 500,000 takings "
          f"are uncosted, got {spare:,}")

    m.sales_db = lambda: FakeDB(
        purchases=[],
        sales=[("2026-08-02T00:00:00", "Force Core(Highest)", 250, 500_000)])
    m.purchase_cost_basis = lambda name: 0
    cogs, priced, unpriced, spare = m.cost_of_goods_sold()
    check((cogs, priced, unpriced, spare) == (0, 0, 250, 500_000),
          f"with no purchases nothing is charged, all 250 are unpriced and "
          f"all 500,000 of takings is uncosted, got "
          f"{(cogs, priced, unpriced, spare)}")
finally:
    m.sales_db = _saved_db
    m.purchase_cost_basis = _basis


MANAGED = [m.FAVOURITE_SLOTS[s] for s in m.managed_core_slots()]
check(len(MANAGED) >= 3,
      f"there should be several managed Cores to check, got {MANAGED}")
for core in MANAGED:
    setname = m.set_behind(core)
    check(setname != "", f"{core} has a Set behind it")
    check(m.core_behind(setname) == core,
          f"and it round-trips: core_behind({setname!r}) should be {core!r}, "
          f"got {m.core_behind(setname)!r}")

check(m.core_behind("Force Core Set (Highest) X 10") == "Force Core(Highest)",
      f"a pack marker must not break the mapping, got "
      f"{m.core_behind('Force Core Set (Highest) X 10')!r}")

for other in ("Yekaterina VIP Membership", "Force Core(Highest)",
              "registration fee: Force Core(High)"):
    check(m.core_behind(other) == "",
          f"{other!r} is not a paired Set and must not resolve, got "
          f"{m.core_behind(other)!r}")


print(f"accounting_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
