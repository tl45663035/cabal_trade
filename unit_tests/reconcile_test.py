"""The reading is the truth: ShopModel.reconcile corrects the model from a walk.

DRIVES NOTHING. Pure model arithmetic against synthetic rows.

The case that matters is the 2026-08-13 divergence. A cancel aimed at absolute
row 12 hit a different row; the model emptied 12, the game emptied 20, and
nothing compared the two for thirteen minutes -- until a row the model called
empty turned out to hold 240 Upgrade Cores and the run stopped. The reading
that would have settled it was taken at the start of the very next cycle and
thrown away.
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "reconcile_test.db")

sys.argv = ["reconcile_test"]
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


def row(index, name, qty=250, price=500_000):
    """One occupied table row."""
    return trade.Row(index=index, name=name, change=(0, 0), top=0, bottom=0,
                     action="change", price=price, qty=qty, status="On Sale")


def empty(index):
    return trade.Row(index=index, name="", change=(0, 0), top=0, bottom=0,
                     action="register", price=None, qty=None, status="")


def seeded(pairs):
    """A model seeded from `pairs`, ready and enforcing."""
    m = trade.ShopModel()
    full = list(pairs) + [(i, empty(i)) for i in range(1, 31)
                          if i not in {p[0] for p in pairs}]
    m.adopt(sorted(full))
    return m


rule("the 2026-08-13 divergence, corrected instead of fatal")

# The model after the mis-aimed cancel: 12 emptied (wrongly), 20 still held.
# The shop: 12 still holds its partially-sold stack, 20 is the one that went.
model = seeded([(12, row(12, "Upgrade Core (Ultimate)", 240, 480_000)),
                (20, row(20, "Upgrade Core (Ultimate)", 250, 509_000))])
model.enforce = True
model.cancel(12)                     # the wrong slot, exactly as it happened
check(model.is_empty(12), "model believes row 12 is empty (the phantom)")
check(not model.is_empty(20), "and still believes row 20 is occupied")

# The next cycle's walk reads the shop as it really is.
walk = [(12, row(12, "Upgrade Core (Ultimate)", 240, 480_000)),
        (20, empty(20))]
fixed = model.reconcile(walk, scope={12, 20}, verbose=False)
check(fixed == 2, f"the walk corrects both rows (got {fixed})")
check(not model.is_empty(12), "row 12 is occupied again -- the reading wins")
check(model.is_empty(20), "row 20 is empty -- the reading wins")
check(model.first_empty() != 12,
      f"first_empty no longer offers 12 (got {model.first_empty()})")

# And the check that ended the run now passes, because the map is right.
try:
    model.check(12, row(12, "Upgrade Core (Ultimate)", 240, 480_000))
    check(True, "check(12) passes after reconciling -- no FATAL")
except Exception as exc:
    check(False, f"check(12) still raised: {exc}")

rule("what reconcile changes, and what it leaves alone")

model = seeded([(1, row(1, "Force Core(High)", 250, 250_000))])
check(model.reconcile([(1, row(1, "Force Core(High)", 250, 250_000))],
                      scope={1}, verbose=False) == 0,
      "an agreeing read changes nothing")

# A partial sale moves qty, not identity. Refreshed silently, not counted.
model.reconcile([(1, row(1, "Force Core(High)", 180, 250_000))],
                scope={1}, verbose=False)
check((model.content(1) or {}).get("qty") == 180,
      "a partial sale refreshes the quantity")

# A reprice moves price, not identity.
model.reconcile([(1, row(1, "Force Core(High)", 180, 249_999))],
                scope={1}, verbose=False)
check((model.content(1) or {}).get("price") == 249_999,
      "a reprice refreshes the price")

# A different ITEM in the slot is a correction.
n = model.reconcile([(1, row(1, "Upgrade Core (Ultimate)", 250, 500_000))],
                    scope={1}, verbose=False)
check(n == 1 and "Upgrade" in (model.content(1) or {}).get("name", ""),
      "a different item in the slot is taken from the reading")

rule("scope: a row that was not read is not evidence")

model = seeded([(1, row(1, "Force Core(High)")),
                (25, row(25, "Siena's Bracelet", 1, 550_000_000))])
# A walk of rows 1-17 says nothing about row 25.
model.reconcile([(1, row(1, "Force Core(High)"))], scope=set(range(1, 18)),
                verbose=False)
check(not model.is_empty(25),
      "row 25 survives a walk that never looked at it -- a walk of 1-17 is "
      "not evidence that 25 is empty")

# Without a scope, only rows actually present in the pairs are considered.
model.reconcile([(1, row(1, "Force Core(High)"))], verbose=False)
check(not model.is_empty(25),
      "and a read that simply omits row 25 does not empty it either")

rule("an unseeded model is left alone")

fresh = trade.ShopModel()
check(fresh.reconcile([(1, row(1, "Force Core(High)"))], verbose=False) == 0,
      "reconcile does nothing to a model that was never seeded -- an "
      "unseeded model has no beliefs to correct")

rule("the floor and cost are ledger facts, not screen facts")

model = seeded([(1, row(1, "Force Core(High)", 250, 250_000))])
before = dict(model.content(1) or {})
model.reconcile([(1, row(1, "Force Core(High)", 100, 240_000))],
                scope={1}, verbose=False)
after = model.content(1) or {}
check(after.get("floor") == before.get("floor")
      and after.get("cost") == before.get("cost"),
      "a qty/price refresh does not disturb the floor or the cost basis -- "
      "the screen cannot improve on what the ledger knows")

rule("an unreadable row is not an empty row")

model = seeded([(5, row(5, "Force Core(High)"))])
# A row whose Function column did not OCR: not "register", no name. The old
# code popped the slot, which makes first_empty() hand out a slot that is
# actually full -- and the next registration lands on top of a live listing.
unreadable = trade.Row(index=5, name="", change=(0, 0), top=0, bottom=0,
                       action="", price=None, qty=None, status="")
n = model.reconcile([(5, unreadable)], scope={5}, verbose=False)
check(not model.is_empty(5),
      "a row that did not resolve leaves the model alone -- an unreadable "
      "row is not evidence of an empty shop")
check(n == 0, f"and is not counted as a correction (got {n})")
check(model.mismatches > 0,
      "but it IS reported, so the log shows the read failed")

# A row that POSITIVELY says it is empty still empties the slot.
model = seeded([(5, row(5, "Force Core(High)"))])
model.reconcile([(5, empty(5))], scope={5}, verbose=False)
check(model.is_empty(5),
      "a row that reads Register with no name/price/qty does empty the slot")

rule("every walk reports; a price that moved is a mismatch")

model = seeded([(1, row(1, "Force Core(High)", 250, 250_000))])
was = model.mismatches
model.reconcile([(1, row(1, "Force Core(High)", 180, 250_000))],
                scope={1}, verbose=False)
check(model.mismatches == was,
      "a partial sale is the shop working, not a mismatch -- quantity falls "
      "whenever a buyer takes part of a stack")

model.reconcile([(1, row(1, "Force Core(High)", 180, 249_999))],
                scope={1}, verbose=False)
check(model.mismatches == was + 1,
      "a price the walk disagrees with IS a mismatch -- the price only moves "
      "when this script moves it, and the model is told at commit")
check((model.content(1) or {}).get("price") == 249_999,
      "and the reading wins: the model is corrected, not the shop")

rule("a price drift is reported and resynced, never fatal")

model = seeded([(8, row(8, "Force Core(High)", 100, 250_000))])
model.enforce = True
# The exact false positive from the 2026-08-12 log at t=3892: the row is
# right, the model is one Alz stale. As a FatalAbort this ended a 6.5-hour
# run 65 minutes in, after 4 of 26 cycles.
try:
    model.check(8, row(8, "Force Core(High)", 100, 249_999))
    check(True, "a one-Alz drift does not end the run")
except Exception as exc:                                  # noqa: BLE001
    check(False, f"a one-Alz drift still raised: {exc}")
check((model.content(8) or {}).get("price") == 249_999,
      "it resyncs from the reading instead")

# A different ITEM on the row is still fatal -- that is a real wrong-listing.
model = seeded([(8, row(8, "Force Core(High)", 100, 250_000))])
model.enforce = True
try:
    model.check(8, row(8, "Upgrade Core (Ultimate)", 100, 250_000))
    check(False, "a different item on the row should still be fatal")
except Exception:                                         # noqa: BLE001
    check(True, "a different item on the row is still fatal -- acting there "
          "would touch the wrong listing")

print()
print("-" * 74)
print(f"{PASS + FAIL} checks, {FAIL} failed")
sys.exit(1 if FAIL else 0)
