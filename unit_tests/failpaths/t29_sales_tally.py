"""What sold, and for how much -- printed on every termination including Ctrl+C.

The table cannot answer "how much sold". A row's QTY column shows what is STILL
on sale, and collecting the proceeds does not change it (the same fact behind
the collect-by-action fix in t26). So the quantity sold is not readable from the
listing either before or after the collect.

The Alz balance is. Read either side of a Receive it gives that sale's credit
exactly, and dividing by the listing's unit price recovers the quantity. The
division doubles as the check: a remainder means the two readings do not
describe one clean sale at that price, so the quantity is left unclaimed rather
than guessed.

The failure mode that matters is get_alz's contract: it returns 0 -- never
raises -- when the Inventory panel is closed or the digits do not read. Treated
as a real balance, a 0 "before" would book a sale worth the entire purse and a 0
"after" would book a negative one. Every path here therefore reads 0 as UNKNOWN,
counts the sale, and says so in the report rather than folding a guess into the
gross.
"""
from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade


def clear():
    trade.SALES.clear()


# ===========================================================================
section("note_sale: quantity is derived, and only when it divides")

clear()
trade.note_sale("Force Core(High)", 210_000, 6_300_000)
sale = trade.SALES[-1]
check("quantity recovered from proceeds / price", sale["qty"] == 30,
      f"{sale!r} -- 6,300,000 / 210,000 = 30")
check("proceeds kept as read", sale["proceeds"] == 6_300_000, f"{sale!r}")

clear()
trade.note_sale("Force Core(High)", 210_000, 6_300_001)   # does not divide
check("an inexact division claims no quantity",
      trade.SALES[-1]["qty"] is None,
      f"{trade.SALES[-1]!r} -- a remainder means the readings do not describe "
      f"one clean sale, and a rounded guess would be silently wrong")

clear()
trade.note_sale("Mystery Item", None, 5_000_000)
check("no price means no quantity", trade.SALES[-1]["qty"] is None,
      f"{trade.SALES[-1]!r}")
check("...but the proceeds still count",
      trade.SALES[-1]["proceeds"] == 5_000_000, f"{trade.SALES[-1]!r}")

clear()
trade.note_sale("Unmeasured", 210_000, None)
check("an unmeasured sale is still recorded", len(trade.SALES) == 1, "")
check("...with no proceeds and no quantity",
      trade.SALES[-1]["proceeds"] is None and trade.SALES[-1]["qty"] is None,
      f"{trade.SALES[-1]!r}")

clear()
trade.note_sale("Zero", 210_000, 0)
check("a zero credit is not a measured sale",
      trade.SALES[-1]["qty"] is None, f"{trade.SALES[-1]!r}")


# ===========================================================================
section("the report")

clear()
check("nothing sold prints nothing", trade.sales_report() == "",
      f"{trade.sales_report()!r}")

clear()
trade.note_sale("Force Core(High)", 210_000, 6_300_000)      # 30
trade.note_sale("Force Core(High)", 210_000, 2_100_000)      # 10
trade.note_sale("Force Gem Package (x400)", 187_000_000, 187_000_000)
trade.note_sale("Force Core(Highest)", 200_000, None)        # unmeasured
report = trade.sales_report()

check("totals the gross", "195,400,000" in report, report)
check("counts every collection, measured or not", "4 collection(s)" in report,
      report)
check("groups by item", report.count("Force Core(High)") >= 1, report)
check("sums quantity per item", " 40 " in report or "40" in report, report)
check("says the unmeasured sale is missing from the gross",
      "1 sale(s) could not be measured" in report, report)
check("...and calls the total a floor", "floor" in report, report)
check("orders by gross, biggest first",
      report.index("Force Gem Package") < report.index("Force Core(High)"),
      report)

clear()
trade.note_sale("Force Core(High)", 210_000, 6_300_000)
check("no warning when everything measured",
      "could not be measured" not in trade.sales_report(),
      trade.sales_report())


# ===========================================================================
section("a collect records a sale, measured off the Alz balance")

class Selling(Harness):
    """A shop where collecting credits the balance."""

    def __init__(self, credit, before=500_000_000, readable=True, **kw):
        super().__init__(rows=[make_row(1, "Force Core(High)",
                                        action="receive",
                                        price=210_000, qty=140)],
                         panel=empty_panel(), **kw)
        self.balance = before
        self.credit = credit
        self.readable = readable

    def install(self):
        super().install()
        h = self
        trade.get_alz = lambda *a, **k: (h.balance if h.readable else 0)
        return self

    def _collect(self):
        self.balance += self.credit
        row = self.rows[0]
        self.rows[0] = make_row(row.index, row.name, action="change",
                                price=row.price, qty=row.qty)


clear()
h = Selling(credit=6_300_000)
with h:
    h.patch("cancel_item", lambda *a, **k: True)
    h.patch("register_item", lambda *a, **k: True)
    run(trade.relist, 1, None, None, False, 8.0, True)

check("the sale was recorded", len(trade.SALES) == 1, f"{trade.SALES!r}")
check("the credit was measured",
      trade.SALES[0]["proceeds"] == 6_300_000, f"{trade.SALES!r}")
check("the quantity was derived", trade.SALES[0]["qty"] == 30,
      f"{trade.SALES!r}")
check("the item is named", trade.SALES[0]["item"] == "Force Core(High)",
      f"{trade.SALES!r}")
check("it is announced", h.said("Collected 6,300,000 Alz"), h.out()[-400:])
check("...and recorded to the index", h.rec("sale.collected") is not None,
      f"{h.labels()}")


# ===========================================================================
section("an unreadable balance never invents a sale")

# get_alz returns 0 when the Inventory panel is closed. 0 read as a real
# balance would book a sale worth the whole purse on the "before" side, or a
# negative one on the "after" side.
clear()
h = Selling(credit=6_300_000, readable=False)
with h:
    h.patch("cancel_item", lambda *a, **k: True)
    h.patch("register_item", lambda *a, **k: True)
    run(trade.relist, 1, None, None, False, 8.0, True)

check("the sale is still counted", len(trade.SALES) == 1, f"{trade.SALES!r}")
check("but no proceeds are invented", trade.SALES[0]["proceeds"] is None,
      f"{trade.SALES!r} -- a 0 balance is UNKNOWN, not 'the purse is empty'")
check("and it says so", h.said("counted but not measured"), h.out()[-400:])

# A balance that goes DOWN across the collect is not a sale either.
clear()
h = Selling(credit=-1_000_000)
with h:
    h.patch("cancel_item", lambda *a, **k: True)
    h.patch("register_item", lambda *a, **k: True)
    run(trade.relist, 1, None, None, False, 8.0, True)
check("a falling balance books no proceeds",
      trade.SALES and trade.SALES[0]["proceeds"] is None,
      f"{trade.SALES!r}")


# ===========================================================================
section("a broken Alz reader must not cost the listing")

clear()
h = Selling(credit=6_300_000)
with h:
    h.patch("cancel_item", lambda *a, **k: True)
    h.patch("register_item", lambda *a, **k: True)
    h.patch("get_alz",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True)

check("the relist still succeeds", outcome == trade.RELISTED,
      f"got {outcome!r} {exc!r} -- bookkeeping must never break a listing")
check("no exception escaped", exc is None, repr(exc))
check("the sale is counted anyway", len(trade.SALES) == 1, f"{trade.SALES!r}")


# ===========================================================================
section("printed on termination, including Ctrl+C")

clear()
trade.note_sale("Force Core(High)", 210_000, 6_300_000)
trade._run_finished = False
printed: list[str] = []
real_print = print
try:
    import builtins
    builtins.print = lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    trade.finish_run_log("KeyboardInterrupt")
finally:
    builtins.print = real_print

out = "\n".join(printed)
check("the tally is printed", "SOLD THIS RUN" in out, out[:400])
check("...with the gross", "6,300,000" in out, out[:400])
check("...alongside the duration line", "Ran for" in out, out[:400])
check("...on the Ctrl+C path", "KeyboardInterrupt" in out, out[:400])

# finish_run_log is registered with atexit AND called from the __main__ guard,
# so it can fire twice. Printing the tally twice would double-report a run.
printed.clear()
try:
    builtins.print = lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    trade.finish_run_log("again")
finally:
    builtins.print = real_print
check("a second call prints nothing", printed == [], f"{printed!r}")

trade._run_finished = False
clear()

# Nothing sold: the run must not grow a stats block it has no data for.
trade._run_finished = False
printed.clear()
try:
    builtins.print = lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    trade.finish_run_log("exit 0")
finally:
    builtins.print = real_print
check("a run with no sales prints no tally",
      "SOLD THIS RUN" not in "\n".join(printed), f"{printed!r}")
check("...but still prints the duration",
      "Ran for" in "\n".join(printed), f"{printed!r}")

trade._run_finished = False
clear()




# ===========================================================================
section("a reading larger than the listing could be worth is refused")

# From a live run on 2026-08-06 the report printed:
#
#   Yekaterina VIP Membership   1 sale       -   1,662,294,744
#   Epic Booster (Highest)      1 sale      16     876,764,416
#   TOTAL                                        2,539,059,160
#
# The VIP sells for about 106,000,000, and the Booster stack held EIGHT at
# 54,797,776 -- yet 876,764,416 divided by that price exactly, so the report
# confidently claimed sixteen units from a stack of eight. An exact division is
# not evidence of anything when the numerator is wrong.
#
# Root cause was get_alz reading the shop's "...has been sold for N" overlay
# instead of the balance. This is the second line of defence: the row on screen
# carries the price and the quantity, so the most a sale can be worth is known
# exactly.

class Inflated(Harness):
    """A collect where the Alz reading jumps by more than the stack is worth."""

    def __init__(self, credit, qty=8, price=54_797_776, **kw):
        super().__init__(rows=[make_row(1, "Epic Booster (Highest)",
                                        action="receive", price=price, qty=qty)],
                         panel=empty_panel(), **kw)
        self.balance = 500_000_000
        self.credit = credit

    def install(self):
        super().install()
        h = self
        trade.get_alz = lambda *a, **k: h.balance
        return self

    def _collect(self):
        self.balance += self.credit
        row = self.rows[0]
        self.rows[0] = make_row(row.index, row.name, action="change",
                                price=row.price, qty=row.qty)


clear()
h = Inflated(credit=876_764_416)            # exactly 2x an 8-stack at that price
with h:
    h.patch("cancel_item", lambda *a, **k: True)
    h.patch("register_item", lambda *a, **k: True)
    run(trade.relist, 1, None, None, False, 8.0, True)

check("the implausible figure is not booked",
      trade.SALES and trade.SALES[0]["proceeds"] is None,
      f"{trade.SALES!r} -- 876,764,416 is twice what 8 x 54,797,776 can yield")
check("...the sale is still counted", len(trade.SALES) == 1, f"{trade.SALES!r}")
check("...and it says why", h.said("cannot be right"), h.out()[-400:])
check("...and it is on the record", h.rec("sale.implausible") is not None,
      f"{h.labels()}")
check("...so the report calls the gross a floor",
      "could not be measured" in trade.sales_report(), trade.sales_report())

clear()
h = Inflated(credit=8 * 54_797_776)         # the whole stack sold: exactly at the ceiling
with h:
    h.patch("cancel_item", lambda *a, **k: True)
    h.patch("register_item", lambda *a, **k: True)
    run(trade.relist, 1, None, None, False, 8.0, True)
check("a sale worth exactly the whole stack IS booked",
      trade.SALES and trade.SALES[0]["proceeds"] == 8 * 54_797_776,
      f"{trade.SALES!r} -- the ceiling is inclusive; selling out is normal")
check("...with the quantity derived", trade.SALES[0]["qty"] == 8,
      f"{trade.SALES!r}")

clear()
h = Inflated(credit=3 * 54_797_776)         # a partial sale, well under the ceiling
with h:
    h.patch("cancel_item", lambda *a, **k: True)
    h.patch("register_item", lambda *a, **k: True)
    run(trade.relist, 1, None, None, False, 8.0, True)
check("an ordinary partial sale is unaffected",
      trade.SALES and trade.SALES[0]["proceeds"] == 3 * 54_797_776,
      f"{trade.SALES!r}")

clear()




# ===========================================================================
section("every collection is written to the database as it happens")

# The end-of-run report was the only place this lived, and a tally held in
# memory is worth nothing if the process never reaches its last line. On
# 2026-08-06 one run was stopped by Ctrl+C, one by the failure breaker, and one
# by a crash inside the tidy-up itself. A committed row survives all three.
import sqlite3
import tempfile
from pathlib import Path as _Path

_tmp = _Path(tempfile.mkdtemp()) / "sales_test.db"
_real_db, _real_ready = trade.SALES_DB, trade._sales_db_ready
trade.SALES_DB, trade._sales_db_ready = _tmp, False
try:
    clear()
    trade.note_sale("Force Core(High)", 210_000, 6_300_000)
    trade.note_sale("Epic Booster (Highest)", 54_797_776, None,
                    "implausible reading 876,764,416 > ceiling 438,382,208")

    check("the database file is created on first use", _tmp.exists(),
          f"{_tmp}")

    rows = trade.sales_since(hours=1)
    check("both collections are already on disk", len(rows) == 2,
          f"{rows!r} -- written at the collect, not at the end of the run")

    # Read with a SEPARATE connection: proves the rows are committed, not
    # sitting in an open transaction that a killed process would lose.
    conn = sqlite3.connect(_tmp)
    got = conn.execute("SELECT item, price, proceeds, qty, note FROM sales"
                       " ORDER BY id").fetchall()
    conn.close()
    check("...and committed, readable by another connection", len(got) == 2,
          f"{got!r}")
    check("a measured sale stores its proceeds and quantity",
          got[0] == ("Force Core(High)", 210_000, 6_300_000, 30, None),
          f"{got[0]!r}")
    check("an unmeasured sale is still stored, with the reason",
          got[1][0] == "Epic Booster (Highest)" and got[1][2] is None
          and "implausible" in (got[1][4] or ""),
          f"{got[1]!r} -- 'why is this blank' has to be answerable later")

    # Timestamps store to the second, so a sub-second window still contains a
    # row written this second -- my first version of this asserted otherwise
    # and failed for that reason, not because the window was broken. Test it
    # with a row that really is old.
    conn = sqlite3.connect(_tmp)
    with conn:
        conn.execute("INSERT INTO sales (at, item, price, proceeds, qty)"
                     " VALUES ('2020-01-01T00:00:00', 'Ancient', 1, 1, 1)")
    conn.close()
    check("sales_since excludes rows outside its window",
          all(r[1] != "Ancient" for r in trade.sales_since(hours=24)),
          f"{trade.sales_since(hours=24)!r}")
    check("...and includes them when the window is wide enough",
          any(r[1] == "Ancient" for r in trade.sales_since(hours=None)),
          "hours=None means all time")
    check("sales_since(None) returns everything",
          len(trade.sales_since(hours=None)) == 3, "two recent plus the old one")

    # Bookkeeping must never be able to cost a listing.
    trade.SALES_DB = _Path("Z:/nonexistent/dir/sales.db")
    trade._sales_db_ready = False
    clear()
    ok, exc = run(trade.note_sale, "Force Core(High)", 210_000, 1_000_000)
    check("an unwritable database does not raise", exc is None, repr(exc))
    check("...and the sale is still held in memory for the report",
          len(trade.SALES) == 1, f"{trade.SALES!r}")
    check("...and sales_since degrades to empty rather than throwing",
          trade.sales_since(hours=1) == [], "")
finally:
    trade.SALES_DB, trade._sales_db_ready = _real_db, _real_ready
    clear()


raise SystemExit(summary())
