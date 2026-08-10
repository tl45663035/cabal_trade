"""The ledger must not invent cost, or throw away income, or clamp a nonsense.

Three defects found on 2026-08-09, all of them in the direction of a figure
that LOOKS authoritative:

  1. sale_rejection refused every possible proceeds whenever it had no size
     evidence -- 313,683,417 Alz across seven collections on the live ledger,
     each refused with "more than the 0 this listing could have held". A bound
     of zero is an absence of information, not a ceiling. Worse, the veto came
     from a WORSE reading than the thing it vetoed: the registration lookup is
     keyed on an OCR'd name and an OCR'd price, and it missed on a price off by
     one digit, a name misread, and a quantity read as 2 instead of 220.

  2. cost_of_goods_sold charged the current average cost to every unit ever
     sold of an item that had ANY purchase -- including the ~1,700 units that
     predate the ledger. That invented roughly 437,000,000 Alz of cost, and the
     honesty note beside it said "2 units have no recorded purchase".

  3. INVENTORY (at cost) printed max(0, spend - cogs), so when (2) drove the
     difference negative it showed a confident 0 -- read as "no stock" when the
     truth was "not computable from this ledger".

The numbers below are the real ones off the live ledger, so a regression is
recognisable rather than abstract.
"""
import os
import sys
import tempfile
from pathlib import Path as _Path

sys.path.insert(0, r"C:\Users\Trung\Cabal")
# Never the operator's ledger: this file only reads, but the import binds
# SALES_DB once and a later edit here must not be able to reach the real one.
os.environ["CABAL_SALES_DB"] = str(
    _Path(tempfile.gettempdir()) / "cabal_accounting_test.db")

import trade as m  # noqa: E402

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


# ==========================================================================
# 1. sale_rejection: no evidence is not evidence of zero
# ==========================================================================
# (proceeds, price, still_listed, listed_units) taken verbatim from the seven
# rows that were refused on the live ledger.
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

# The readings this function EXISTS for must still be refused. If any of these
# starts passing, the fix above has been taken too far.
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

# A real partial sale, which the original strict rule threw away.
check(m.sale_rejection(52_499_750, 209_999, 0, 250) == "",
      "a full 250-unit sale against a 250 registration is accepted")
check(m.sale_rejection(2_099_990, 209_999, 240, 250) == "",
      "and a partial one -- 10 sold, 240 left on the row")

# The whole-units rule is the one doing the real work, and it must not depend
# on any bound at all.
check(m.sale_rejection(100_001, 100_000, 999, 999) != "",
      "a non-whole number of units is refused however generous the bound")


# ==========================================================================
# 2. cost_of_goods_sold charges only what the purchases cover
# ==========================================================================
_saved_db = m.sales_db


class FakeDB:
    """A ledger with more sold than bought -- the live situation."""

    def __init__(self, purchases, sales):
        self.purchases, self.sales = purchases, sales

    def execute(self, sql, *a):
        if "FROM purchases" in sql:
            return list(self.purchases)
        return list(self.sales)

    def close(self):
        pass


try:
    # 100 Sets bought; 250 Cores sold. Only 100 may be charged.
    # THREE columns, matching the real query. cost_of_goods_sold now selects
    # `item, price, qty` so it can take the unit cost from the same scan --
    # purchase_cost_basis resolves through set_behind and returns 0 for chaos.
    # A 2-tuple fixture raised ValueError into the blanket `except`, so the
    # function returned (0, 0, 0) and all three checks below "passed" against
    # a swallowed error rather than against the arithmetic.
    m.sales_db = lambda: FakeDB(
        purchases=[("Force Core Set (Highest) X 100", 100_000, 100)],
        sales=[("Force Core(Highest)", 250)])
    _basis = m.purchase_cost_basis
    m.purchase_cost_basis = lambda name: 1_000
    cogs, priced, unpriced = m.cost_of_goods_sold()
    check(priced == 100,
          f"only the 100 units a purchase covers may be priced, got {priced}")
    check(unpriced == 150,
          f"and the other 150 must be reported as unpriced, got {unpriced}")
    check(cogs == 100_000,
          f"cost is 100 x 1,000, not 250 x 1,000 -- charging the 150 invents "
          f"150,000 Alz of cost. got {cogs:,}")

    # Nothing bought at all: everything is unpriced, nothing is charged.
    m.sales_db = lambda: FakeDB(purchases=[],
                                sales=[("Force Core(Highest)", 250)])
    m.purchase_cost_basis = lambda name: 0
    cogs, priced, unpriced = m.cost_of_goods_sold()
    check((cogs, priced, unpriced) == (0, 0, 250),
          f"with no purchases nothing is charged and all 250 are unpriced, "
          f"got {(cogs, priced, unpriced)}")
finally:
    m.sales_db = _saved_db
    m.purchase_cost_basis = _basis


# ==========================================================================
# 3. the Set -> Core mapping the charge depends on
# ==========================================================================
# Derived from the live slot table, not a fixed list: the roster changes when
# the operator adds or removes an item, and a hardcoded list would then fail
# for a reason that has nothing to do with accounting.
MANAGED = [m.FAVOURITE_SLOTS[s] for s in m.managed_core_slots()]
check(len(MANAGED) >= 3,
      f"there should be several managed Cores to check, got {MANAGED}")
for core in MANAGED:
    setname = m.set_behind(core)
    check(setname != "", f"{core} has a Set behind it")
    check(m.core_behind(setname) == core,
          f"and it round-trips: core_behind({setname!r}) should be {core!r}, "
          f"got {m.core_behind(setname)!r}")

# Purchases carry a pack marker; the mapping must survive it, or every
# purchase fails to match its sales and COGS silently drops to zero.
check(m.core_behind("Force Core Set (Highest) X 10") == "Force Core(Highest)",
      f"a pack marker must not break the mapping, got "
      f"{m.core_behind('Force Core Set (Highest) X 10')!r}")

# Non-Sets must not resolve, or a registration-fee row would be counted as
# stock bought.
for other in ("Yekaterina VIP Membership", "Force Core(Highest)",
              "registration fee: Force Core(High)"):
    check(m.core_behind(other) == "",
          f"{other!r} is not a paired Set and must not resolve, got "
          f"{m.core_behind(other)!r}")


print(f"accounting_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
