"""The one-shot table-freshness marker: it may skip ONE refresh, never two.

A sold row refreshes twice for one fact -- once to read the table, then again
after collecting to confirm the row is gone -- and the next row opens with its
own refresh, back to back with that one. Measured on the 12:26 run of
2026-08-15: nine refreshes for five rows against a single cancel, 25,944 ms of
38,783 ms instrumented, 67% of the measured time.

The general fix for this is a cache, and this file already carries the scar of
one: it "saved about twelve seconds a row" and was removed because COLLECTING A
SOLD ROW INVALIDATED NOTHING. So the marker is one-shot -- consumed by the
first reader -- which bounds the damage from a wrong invalidation list to a
single row instead of a whole run.

DRIVES NOTHING. Pure module state.
"""
import inspect
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "table_fresh_test.db")

sys.argv = ["table_fresh_test"]
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


section("it is spent by the first reader")

m.mark_table_fresh()
check(m.consume_table_fresh() is True,
      "a refresh makes the next reader skip")
check(m.consume_table_fresh() is False,
      "and the reader AFTER that does not -- one refresh buys one skip, so "
      "two rows can never both coast on the same refetch")
check(m.consume_table_fresh() is False, "and it stays spent")

section("a fresh import trusts nothing")

# A new process has not refreshed anything, so the very first row must refetch.
# The old cache defaulted the other way and read the client's stale copy.
m.mark_table_stale("reset")
check(m.consume_table_fresh() is False,
      "with nothing known, the answer is 'refresh'")

section("every table change invalidates it")

for label, why in (("a cancel", "a listing was cancelled"),
                   ("a register", "a listing was registered"),
                   ("a collect", "a sold row was collected")):
    m.mark_table_fresh()
    m.mark_table_stale(why)
    check(m.consume_table_fresh() is False,
          f"{label} makes the next row refetch")

section("...and all three are actually wired, not just available")

# The 2026-08-15 removal note is explicit that the previous cache knew about
# two of these three. Grepping the call sites is the only way to assert the
# third is present, because reaching it needs the game.
cancel_src = inspect.getsource(m.cancel_item)
check("mark_table_stale" in cancel_src,
      "cancel_item marks the table stale when it commits")

register_src = inspect.getsource(m.register_item)
check("mark_table_stale" in register_src,
      "register_item marks the table stale when it commits")

cycle_src = inspect.getsource(m._relist_cycle)
check("mark_table_stale" in cycle_src,
      "and the COLLECT path does too -- the one the previous cache missed, "
      "which is why that cache had to be removed")
check(cycle_src.index("mark_table_stale") < cycle_src.index("click(*accept.centre)"),
      "marked BEFORE the receipt click, so an exit between the click and the "
      "refresh below cannot leave the table looking fresh")
check("consume_table_fresh" in cycle_src,
      "and the start-of-row refresh consults the marker")

section("refresh_table sets it only when the table really loaded")

refresh_src = inspect.getsource(m.refresh_table)
check("mark_table_fresh" in refresh_src, "a good refresh marks it fresh")
check("mark_table_stale" in refresh_src,
      "and a refresh that TIMED OUT marks it stale rather than leaving the "
      "previous value -- a failed refetch is not evidence of anything")
i_fresh = refresh_src.index("mark_table_fresh()")
i_after = refresh_src.index('record("refresh.after")')
check(i_after < i_fresh,
      "and only after the load is confirmed, not at the click")

section("the sold-row sequence, end to end")

# Row N: refresh -> read -> collect -> refresh. Row N+1 opens.
m.mark_table_stale("new row")
m.mark_table_fresh()                       # the row's own opening refresh
check(m.consume_table_fresh() is True, "row N does not double-refresh itself")
m.mark_table_stale("a sold row was collected")
m.mark_table_fresh()                       # the post-collect confirming refresh
check(m.consume_table_fresh() is True,
      "row N+1 skips, because the post-collect refresh came AFTER the collect "
      "-- this is the duplicate being removed")
check(m.consume_table_fresh() is False,
      "and row N+2 refetches, because nothing has refreshed since")

# The dangerous ordering: collect with NO refresh after it.
m.mark_table_fresh()
m.mark_table_stale("a sold row was collected")
check(m.consume_table_fresh() is False,
      "a collect with no refresh behind it leaves the next row refetching, "
      "which is the exact case that killed the previous cache")

print()
print("-" * 74)
print(f"{PASS + FAIL} checks, {FAIL} failed")
sys.exit(1 if FAIL else 0)
