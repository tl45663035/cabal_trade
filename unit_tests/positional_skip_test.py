"""An unreadable row is skipped, never searched for by identity.

The positional path scrolls to an absolute row, reads the one band it
occupies, and checks that band against the row model. When the read fails it
used to fall back to bring_into_view -- an identity search -- and that throws
away both guarantees at once:

  * identity CANNOT tell siblings apart. Live on 2026-08-15 rows 11 and 12
    were both 'Force Core (Ultimate)' at qty 250, differing only in price. The
    search ranks same-named rows by an ordinal counted over the whole 30-row
    shop and indexes it into a 10-row view, which is the 2026-08-13 divergence
    exactly: cycle 25 asked for absolute row 12, "followed" to screen 9
    (absolute 20), cancelled it, and SHOP.cancel(12) emptied a slot the game
    had never touched.
  * it skips SHOP.check, the ONLY thing that ever tests the model against the
    shop. So the fallback picked a row it could not identify AND silenced the
    alarm that would have caught it.

Skipping costs one row for one cycle: nothing has been cancelled at that
point, so the work tab is clean, relist_rows continues with the rest and
retries next cycle.

DRIVES NOTHING. Source structure and one faked reader.
"""
import inspect
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "positional_skip_test.db")

sys.argv = ["positional_skip_test"]
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


SRC = inspect.getsource(m.relist_rows)

section("the identity fallback is gone from the positional path")

# The block: `top = goto_row(...)` then `if top is None:`. What follows that
# must not re-enable the search.
i = SRC.index("top = goto_row(")
block = SRC[i:i + 2600]
check("falling back to the identity search" not in block,
      "the fallback message is gone")
check("positional = False" not in block,
      "and nothing re-enables the identity path by clearing `positional` -- "
      "that is what routed an unreadable row into bring_into_view")
check("continue" in block,
      "the row is skipped instead")
check("failed_rows.append" in block,
      "and recorded as a failed row, so it is reported and retried rather "
      "than silently dropped")
check("relist.positional_unreadable" in block,
      "with a record() label, so the frequency is measurable in the corpus")

section("the check the fallback used to skip is still there")

check("SHOP.check(index, top)" in SRC,
      "the successful path still checks the model against the shop -- this is "
      "the only thing that can catch a divergence, and the fallback bypassed "
      "it entirely")

section("goto_row returns None when the band will not read")

# That is the trigger. Faked at read_top_row so no scrolling or OCR happens.
saved = {n: getattr(m, n) for n in
         ("read_top_row", "scroll_to_end", "scroll_wheel",
          "scroll_rows_per_notch", "note_scan")}
try:
    m.read_top_row = lambda *a, **k: None
    m.scroll_to_end = lambda *a, **k: True
    m.scroll_wheel = lambda *a, **k: True
    m.scroll_rows_per_notch = lambda *a, **k: 1.0
    m.note_scan = lambda *a, **k: 0
    got = m.goto_row(12, verbose=False)
    check(got is None,
          f"an unreadable band means goto_row reports failure, got {got!r}")

    # And a readable one still works, so the skip cannot be permanent.
    row = m.Row(index=1, name="Force Core (Ultimate)", change=(0, 0), top=0,
                bottom=0, action="change", price=445_500, qty=250,
                status="On Sale")
    m.read_top_row = lambda *a, **k: row
    check(m.goto_row(12, verbose=False) is row,
          "and a band that DOES read returns the row unchanged")
finally:
    for n, v in saved.items():
        setattr(m, n, v)

section("siblings are why identity cannot substitute for position")

# The live pair. Same name, same quantity, same status -- only the price
# differs, and price is not what the search matches on.
a = m.Row(index=11, name="Force Core (Ultimate)", change=(0, 0), top=0,
          bottom=0, action="change", price=433_253, qty=250, status="On Sale")
b = m.Row(index=12, name="Force Core (Ultimate)", change=(0, 0), top=0,
          bottom=0, action="change", price=445_500, qty=250, status="On Sale")
check(a.name == b.name and a.qty == b.qty,
      "rows 11 and 12 were indistinguishable by name and quantity")
check(m._floor_key(m.item_name(a.name)) == m._floor_key(m.item_name(b.name)),
      "and they fold to the same key, which is what the search matches on -- "
      "so it has nothing left to separate them by")
check(a.price != b.price,
      "only the price differed, and cancelling the wrong one costs that "
      "difference plus a model that no longer describes the shop")

print()
print("-" * 74)
print(f"{PASS + FAIL} checks, {FAIL} failed")
sys.exit(1 if FAIL else 0)
