"""Revert each fix and prove a test goes red.

A green suite is evidence of nothing until it can fail. The ten-agent review
found four bugs that 2,805 existing checks did not catch, and the reason was
always the same: the tests could not have failed if the code were wrong.

So every fix made in response to that review is reverted here, in memory, and
the suites are re-run against the broken build. A mutation that survives is a
fix with no test behind it, and is reported as a failure of THIS file.

Nothing is written to trade.py and nothing touches the game.

    py unit_tests/mutation_check.py            # all mutations
    py unit_tests/mutation_check.py -v         # show each suite's output
"""
import io
import os
import sys
import tempfile
import types
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "trade.py").read_text(encoding="utf-8-sig")
VERBOSE = "-v" in sys.argv

os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.mkdtemp(prefix="cabal_mutation_")) / "scratch.db")

# (name, what it breaks, old, new)
#
# Each `old` is quoted from the fix, so a later edit that moves the code makes
# this file complain rather than silently stop testing anything.
MUTATIONS = [
    ("cost-floor pack marker",
     "favourite_for stops stripping the pack marker, so a listed row resolves "
     "to no slot and the never-below-cost floor dies on every relist",
     'want = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", item)))',
     'want = _floor_key(item_name(item))'),

    ("pack marker case",
     "the pack regex goes back to uppercase-only X, so an OCR'd lowercase x "
     "records a 250-Set bundle as one item",
     '_PACK_SIZE = re.compile(r"\\bX\\s*([\\d,]+)\\s*$", re.IGNORECASE)',
     '_PACK_SIZE = re.compile(r"\\bX\\s*([\\d,]+)\\s*$")'),

    ("grade containment",
     "buy_offer goes back to a containment test, so a Highest dialog "
     "satisfies a High order at the last check before the money moves",
     '''    if any(key in shown for key in confusable):
        halt_buying(
            f"the Confirm Purchase dialog names a different grade than "
            f"{offer.name!r} -- the favourite slots and the shop disagree")
        return False, (f"the dialog names a longer grade than {offer.name!r}: "
                       f"{dialog['text'][:80]!r}")
''',
     ''),

    ("overshoot target",
     "the first-order exemption keys off the RESTOCK_TARGET constant again, "
     "so --buy-target re-opens the 428M single-click case",
     '''        target_now = BUY_TARGET or RESTOCK_TARGET
        first_order = (still_wanted is not None
                       and still_wanted >= target_now)''',
     '''        first_order = (still_wanted is not None
                       and still_wanted >= RESTOCK_TARGET)'''),

    ("balance is the proof",
     "buy_offer claims a purchase whenever the balance is merely READABLE, "
     "without checking that any money actually moved",
     # Was `== offer.price` until quantity buying landed and the comparison
     # became `== expected` (price x take). The mutation silently stopped
     # applying at that point and reported "the code moved" rather than a pass,
     # which is the only reason it was noticed -- a mutation that cannot be
     # applied proves nothing, exactly like the test it is meant to police.
     'if before and after and before - after == expected:',
     'if before and after:'),

    ("sort direction by substring",
     "purchase_sorted_low_to_high goes back to looking for 'low' anywhere in "
     "the control, so 'By Price:High to Low' reads as ready to buy and row 1 "
     "is the most expensive offer rather than the cheapest",
     '''    text = " ".join(w.text for w in words)
    match = _SORT_DIRECTION.search(text)
    if match is None:
        return False
    return match.group(1).casefold() == "low"''',
     '''    text = " ".join(w.text for w in words).casefold()
    return "low" in text and "price" in text'''),

    ("sort is never set",
     "open_purchase_tab goes back to merely CHECKING the sort, so a dropdown "
     "left on High to Low blocks every purchase for the rest of the run "
     "instead of being corrected",
     '''        return set_purchase_sort_low_to_high(verbose=verbose)

    if not trade_window_open():''',
     '''        return True

    if not trade_window_open():'''),

    ("unmeasured purchase",
     "a purchase whose balance could not be read is treated as no purchase, "
     "so the Alz is spent and nothing reaches the ledger",
     '    if not before or not after:',
     '    if False:'),

    ("restock re-buys",
     "restock_core stops resuming from carried stock, so a restock that "
     "cannot list buys a whole target again every cycle",
     '    carried = carried_sets(item_slot)',
     '    carried = 0'),

    ("carry is banked",
     "the purchase is no longer recorded before the convert/list rounds, so a "
     "crash mid-round loses the fact that the Sets were paid for",
     '        note_carried_sets(item_slot, purchase["bought"])',
     '        pass'),

    ("carry is settled",
     "the outstanding balance is not written back after the rounds, so a "
     "partly-listed restock either re-buys or never resumes",
     '''    outstanding = max(0, result["bought"] - result["listed"])
    note_carried_sets(item_slot, outstanding)''',
     '''    outstanding = max(0, result["bought"] - result["listed"])'''),

    ("strand recovery guard",
     "the strand recovery lists the restock's raw material at the strictest "
     "floor -- 175,000,000 for a 187,278 Set",
     '''    outstanding = carried_total()
    if outstanding:''',
     '''    outstanding = 0
    if outstanding:'''),

    ("list before breaking",
     "the round loop breaks on `converted <= 0` before list_cores runs -- the "
     "reading a SUCCESSFUL conversion gives on a full work tab",
     '        if conv["converted"] <= 0 and not candidates:',
     '        if conv["converted"] <= 0:'),

    ("partial sale ceiling",
     "the sale ceiling goes back to price x quantity-still-listed, which "
     "discards every partial sale",
     "    bound = max(max(0, still_listed or 0), listed_units or 0)",
     "    bound = max(0, still_listed or 0)"),

    ("one stack caps another",
     "the ceiling goes back to preferring the registration over the row, so "
     "a 200-stack registered earlier vetoes the 250-stack that actually sold "
     "-- the bug that discarded 103,949,505 Alz on 2026-08-07",
     "    bound = max(max(0, still_listed or 0), listed_units or 0)",
     "    bound = listed_units if listed_units else max(0, still_listed or 0)"),

    ("sold rows are not stock",
     "a listing in `receive` state -- sold, awaiting collection -- counts as "
     "stock again, so a sold-out Core is never restocked",
     '''        if getattr(row, "action", None) == "receive":
            continue''',
     "        pass"),

    ("sale must be whole units",
     "the whole-units test is dropped, so the 1,662,294,744 VIP reading is "
     "booked as income",
     '''    if proceeds % price:
        return (f"{proceeds:,} is not a whole number of units at "
                f"{price:,} each ({proceeds / price:.2f})")''',
     '    if False:\n        pass'),

    ("registration recorded",
     "note_registration stops recording, so no sale has a real ceiling",
     '''    if not item or not price or not qty:
        return''',
     '''    if True:
        return'''),

    ("vendor calibration",
     "the vendor and Purchase-tab coordinates leave the calibration table and "
     "stay at their 2560x1440 values on every machine",
     '    "PURCHASE_BUY_X": "x",',
     ''),
]

SUITES = ["review_fixes_test.py", "buying_gaps_test.py", "restock_test.py",
          "convert_cores_test.py", "sort_control_test.py"]


def build(source):
    """Load `source` as the `trade` module, replacing any cached one."""
    for name in [n for n in sys.modules if n == "trade"]:
        del sys.modules[name]
    module = types.ModuleType("trade")
    module.__file__ = str(ROOT / "trade.py")
    sys.modules["trade"] = module
    exec(compile(source, str(ROOT / "trade.py"), "exec"), module.__dict__)
    module.NO_INPUT = True
    return module


def run_suite(name):
    """Run one suite against whatever `trade` currently is. True if green."""
    path = ROOT / "unit_tests" / name
    namespace = {"__name__": "__main__", "__file__": str(path)}
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"),
                 namespace)
    except SystemExit as exc:
        if VERBOSE:
            print(buf.getvalue())
        return not exc.code
    except Exception as exc:  # noqa: BLE001 - a crash is a red suite
        if VERBOSE:
            print(f"    {type(exc).__name__}: {exc}")
        return False
    return True


print("Checking the suites are green on the real build...")
build(SOURCE)
baseline = {name: run_suite(name) for name in SUITES}
for name, green in baseline.items():
    print(f"  {'green' if green else 'RED  '}  {name}")
if not all(baseline.values()):
    print("\nThe suites are not green to begin with; fix that first.")
    sys.exit(1)

print(f"\nReverting {len(MUTATIONS)} fixes, one at a time:\n")
survived, unapplied = [], []
for label, describes, old, new in MUTATIONS:
    if SOURCE.count(old) != 1:
        unapplied.append(f"{label} ({SOURCE.count(old)} matches)")
        print(f"  ??  {label}: cannot apply -- {SOURCE.count(old)} matches")
        continue
    try:
        build(SOURCE.replace(old, new, 1))
    except Exception as exc:  # noqa: BLE001
        print(f"  ok  {label}: the module will not even import ({exc})")
        continue
    caught = [name for name in SUITES if not run_suite(name)]
    if caught:
        print(f"  ok  {label}\n        caught by: {', '.join(caught)}")
    else:
        survived.append(label)
        print(f"  ** SURVIVED: {label}\n        {describes}\n"
              f"        No test fails when this is reverted.")

build(SOURCE)
print("\n" + "=" * 60)
print(f"{len(MUTATIONS) - len(survived) - len(unapplied)}/{len(MUTATIONS)} "
      f"reverted fixes were caught")
if unapplied:
    print(f"{len(unapplied)} could not be applied (the code moved): "
          f"{', '.join(unapplied)}")
for label in survived:
    print(f"  UNCAUGHT: {label}")
sys.exit(1 if survived or unapplied else 0)
