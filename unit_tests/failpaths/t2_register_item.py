"""register_item() failure paths.

Central question for every case: is the item left in the shop slot, and is the
caller told enough to know that?
"""
import harness as H
from harness import (Harness, check, note, section, summary, run, where,
                     make_row, loaded_panel, empty_panel)
import trade

ITEM = "Upgrade Core (Ultimate)"


def fresh(**flags):
    h = Harness(rows=[make_row(1, ITEM, price=410_000, qty=100)],
                panel=empty_panel())
    for key, value in flags.items():
        setattr(h, key, value)
    return h


def call(h, **kwargs):
    report: dict = {}
    base = dict(expect_item=ITEM, expect_qty=100, report=report)
    base.update(kwargs)
    with h:
        ok, exc = run(trade.register_item, 1, 1, **base)
    return ok, exc, report


# ---------------------------------------------------------------------------
section("2a. nothing loads into the shop slot")
# SHOULD: retry LOAD_ATTEMPTS times, abort, leave nothing listed, commit nothing.
h = fresh(load_fails=True)
ok, exc, report = call(h)

check("2a returns False", ok is False, f"got {ok!r}")
check("2a raised nothing", exc is None, repr(exc))
check(f"2a tried LOAD_ATTEMPTS={trade.LOAD_ATTEMPTS} ctrl+clicks",
      sum(1 for n, _ in h.clicks() if n == "ctrl_click") == trade.LOAD_ATTEMPTS,
      str(h.clicks()))
check("2a recorded register.aborted", "register.aborted" in h.labels(),
      str(h.labels()))
check("2a committed=False", (h.rec("register.aborted") or {}).get("committed")
      is False)
check("2a report carries no commit", report.get("committed") is not True,
      str(report))
check("2a shop slot really is empty", h.panel["loaded"] is False)
check("2a nothing was listed", len(h.rows) == 1)


# ---------------------------------------------------------------------------
section("2b. qty_max disagrees with expect_qty (a DIFFERENT item loaded)")
# SHOULD: abort before pricing, and make sure the wrong item does not stay in
# the shop slot -- the next cycle cannot register anything while it sits there.
h = fresh(load_as={"qty": 64, "qty_max": 64})
ok, exc, report = call(h)

check("2b returns False", ok is False, f"got {ok!r}")
# The cross-check became a LOWER BOUND on 2026-08-08. It used to demand
# equality, but expect_qty is what the LISTING held while qty_max is what the
# panel offers -- everything owned of that item across the whole inventory,
# because a Ctrl+Click gathers matching items from every tab. Owning MORE is
# ordinary (a 250-Core conversion spills past tab 4 by design); owning FEWER is
# the case worth refusing, and is what this section exercises: 100 cancelled,
# 64 offered.
check("2b aborted on the quantity cross-check",
      h.said("offers only") or h.said("not the same item"), h.out()[-400:])
check("2b and said how big the shortfall was",
      h.said("short by"), h.out()[-400:])
check("2b nothing was listed", len(h.rows) == 1)
check("2b told the caller the slot is still occupied",
      h.said("still sitting in the shop slot"), h.out()[-400:])
if h.panel["loaded"]:
    note("2b the wrong item is left in the shop slot",
         "register_item aborts with the item still loaded (trade.py:4046-4049 "
         "only PRINTS a note). The recovery is a human running --clear, or "
         "prepare_for_actions() next cycle; nothing in the relist path clears "
         "it, and relist() then presses Escape on a shop holding an item.")
check("2b the slot is left occupied (documented, not fixed)",
      h.panel["loaded"] is True,
      "harness expectation: the code does not clear it")
check("2b return value alone does not distinguish 'wrong item' from "
      "'nothing happened'", ok is False and report == {},
      f"report={report}")


# ---------------------------------------------------------------------------
section("2b2. qty_max is None -- the cross-check silently does not run")
h = fresh(load_as={"qty": 64, "qty_max": None})
ok, exc, report = call(h)
check("2b2 the mismatched item was listed anyway", ok is True,
      f"got {ok!r}; out={h.out()[-300:]}")
if ok:
    note("2b2 the identity guard fails open",
         "trade.py:3754 requires panel['qty_max'] is not None. The code's own "
         "comments call the qty field 'the worst OCR target on the panel' and "
         "read_register_panel's docstring says qty_max 'is often None even "
         "when an item is sitting there', so the one check that would catch "
         "'this is not the same item' is disabled exactly when the read it "
         "depends on fails. A 64-stack was listed under a 100-stack's identity "
         "with no complaint and no note in the output.")

# the tolerance band: a mismatch inside max(5, 10%) is accepted on purpose
h = fresh(load_as={"qty": 95, "qty_max": 95})
ok, exc, report = call(h)
check("2b3 a mismatch inside the slack is accepted", ok is True, f"{ok!r}")
check("2b3 the operator is told in the log", h.said("within 10"), h.out()[-500:])
if "qty_disagreement" not in report:
    note("2b3 report['qty_disagreement'] can never be set",
         "trade.py:3781 is `report and report.update(qty_disagreement=...)`. "
         "Every caller passes a FRESH empty dict, and nothing writes to it "
         "before this line, so `report` is {} -- falsy -- and update() is "
         "short-circuited away on every single call. The machine-readable "
         "record of a quantity disagreement is unreachable code. Every other "
         "write in this function correctly tests `if report is not None`.")
check("2b3 the disagreement reaches the caller's report",
      report.get("qty_disagreement") == (100, 95),
      f"{report} -- `report and report.update(...)` is dead on an empty dict")


# ---------------------------------------------------------------------------
section("2c. net sales is not divisible by the price")
h = fresh(net_sales_extra=7)
ok, exc, report = call(h)

check("2c returns False", ok is False, f"got {ok!r}")
check("2c aborted on the divisibility check",
      h.said("not a whole multiple"), h.out()[-400:])
check("2c committed=False", (h.rec("register.aborted") or {}).get("committed")
      is False)
check("2c nothing was listed", len(h.rows) == 1)
check("2c the item is left in the shop slot", h.panel["loaded"] is True,
      "same strand as 2b")
check("2c the price was already selected before the abort",
      "price.before_select" in h.labels(), str(h.labels()))


# ---------------------------------------------------------------------------
section("2d. the Register button cannot be found")
h = fresh(register_button_present=False)
ok, exc, report = call(h)

check("2d returns False", ok is False, f"got {ok!r}")
check("2d aborted on the missing button",
      h.said("could not find the Register button"), h.out()[-400:])
check("2d nothing was listed", len(h.rows) == 1)
check("2d the item is left in the shop slot, priced and ready",
      h.panel["loaded"] is True and h.panel.get("net_sales", 0) > 0,
      str(h.panel))
check("2d no register.priced frame was recorded",
      "register.priced" not in h.labels(), str(h.labels()))


# ---------------------------------------------------------------------------
section("2e. post-commit failure: the shop slot never clears")
# SHOULD: report the commit through `report` so the caller verifies rather than
# assuming, even though the function returns False.
h = fresh(post_commit_slot_sticks=True)
ok, exc, report = call(h)

check("2e returns False", ok is False, f"got {ok!r}")
check("2e aborted on the slot not clearing",
      h.said("shop slot did not clear"), h.out()[-400:])
check("2e report says committed=True", report.get("committed") is True,
      str(report))
check("2e report carries the price and quantity",
      report.get("price") == 410_000 and report.get("qty") == 100, str(report))
check("2e the listing really is live", len(h.rows) == 2, str(h.rows))
check("2e recorded register.aborted with committed=True",
      (h.rec("register.aborted") or {}).get("committed") is True,
      str(h.rec("register.aborted")))
check("2e warned the operator", h.said("may have gone through"), h.out()[-300:])


# ---------------------------------------------------------------------------
section("2f. post-commit failure: a confirmation dialog stays open")
h = fresh(confirm_sticks=True)
ok, exc, report = call(h)

check("2f returns False", ok is False, f"got {ok!r}")
check("2f report says committed=True", report.get("committed") is True,
      str(report))
check("2f the listing really is live", len(h.rows) == 2, str(h.rows))
if report.get("price") is None:
    note("2f the commit is reported without its figures",
         "trade.py:3999-4001 sets report['committed']=True inside the confirm "
         "loop, but price/qty/total are only filled in after it. The caller "
         "then runs sanity_check(target.name, report.get('price')=None, ...), "
         "which verifies a listing against 'price unknown' and passes on the "
         "strength of the NAME alone.")
check("2f the caller is given the price it must verify against",
      report.get("price") == 410_000, str(report))


# ---------------------------------------------------------------------------
section("2g. PermissionError during the Register click")
h = fresh()
h.arm_after = {"register.priced": ("click", PermissionError(
    trade.CURSOR_BLOCKED_HINT))}
ok, exc, report = call(h)

check("2g raises PermissionError", isinstance(exc, PermissionError), repr(exc))
check("2g recorded no abort", "register.aborted" not in h.labels(),
      str(h.labels()))
check("2g the item is left in the shop slot", h.panel["loaded"] is True)
note("2g escape path", where(exc) if exc else "-")


# ---------------------------------------------------------------------------
section("2h. PermissionError AFTER the commit click")
h = fresh()
h.arm_after = {}
h.click_fault = {}
with h:
    report = {}
    # fail the grab that follows the committing click
    h.arm_after = {}
    original = h._click
    committed_seen = {"n": 0}

    def click_then_fail(x, y, settle=0.15):
        original(x, y, settle)
        if h.pending_register is None and h.registered:
            committed_seen["n"] += 1
            if committed_seen["n"] == 1:
                raise PermissionError(trade.CURSOR_BLOCKED_HINT)

    trade.click = click_then_fail
    ok, exc = run(trade.register_item, 1, 1, expect_item=ITEM, expect_qty=100,
                  report=report)

check("2h raises PermissionError after the commit",
      isinstance(exc, PermissionError), repr(exc))
check("2h the listing really is live", len(h.rows) == 2, str(h.rows))
if report.get("committed") is not True:
    note("2h one-statement window where a live listing is never reported",
         "report['committed'] is set at trade.py:3999 on the statement AFTER "
         "click(*confirm.centre) at 3992. An exception raised by click() once "
         "the button-down has already been delivered lands in that window: the "
         "listing exists, `committed` is still False, no register.aborted is "
         "recorded, and the caller receives an exception instead of a report. "
         "Narrow, but it is the same shape as the cancel-side gap in 1g.")
check("2h a listing that exists is reported as committed",
      report.get("committed") is True,
      f"report={report}: the commit is invisible to the caller")

raise SystemExit(summary())
