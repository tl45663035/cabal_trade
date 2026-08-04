"""End-to-end replay of the real 5-hour outage of 2026-08-03 19:56.

From the recorded index (unit_tests/corpus/run_index.jsonl):

  19:56:12  cancel.committed   Force Core(Highest) x230 at 207,988
  19:56:19  register.before_load
  19:56:23  register.aborted   'loaded 233 of an item but the cancelled
                                listing held 230 - this is not the same item'
  19:56:39  npc.found / shop.opened / tab.register_open
  ...       NOTHING until 00:59:01, after a manual restart

The claim under test: the abort left 233 items in work tab 4, every later
cycle then failed require_empty_work_tab (which records nothing and returns
before refresh_table), and MAX_CONSECUTIVE_FAILURES stopped the run.
"""
import harness as H
from harness import (Harness, check, note, section, summary, run, make_row,
                     empty_panel)
import trade

ITEM = "Force Core(Highest)"


def build(loaded_qty):
    h = Harness(rows=[make_row(1, ITEM, price=207_988, qty=230)],
                panel=empty_panel())
    h.register_name = ITEM
    h.load_as = {"qty": loaded_qty, "qty_max": loaded_qty}
    return h


section(f"build under test: {H.VERSION}")

# ---------------------------------------------------------------------------
section("9a. the historical numbers (233 loaded vs 230 expected)")
h = build(233)
with h:
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True)
slack = max(trade.QTY_CROSSCHECK_ABSOLUTE,
            int(230 * trade.QTY_CROSSCHECK_FRACTION))
print(f"  slack today = max({trade.QTY_CROSSCHECK_ABSOLUTE}, "
      f"10% of 230) = {slack}; the discrepancy was 3")
check("9a the exact historical case no longer aborts",
      outcome == trade.RELISTED, f"got {outcome!r}; {h.out()[-300:]}")
note("9a", "the tolerance at trade.py:3774-3787 closes the specific incident. "
     "The tests below show the SHAPE is still reachable for any discrepancy "
     "larger than the slack.")


# ---------------------------------------------------------------------------
section("9b. the same shape, outside the tolerance (64 loaded vs 230)")
h = build(64)
with h:
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True)

check("9b returns FAILED", outcome == trade.FAILED, f"got {outcome!r}")
check("9b the cancel committed first", "cancel.committed" in h.labels(),
      str(h.labels()))
check("9b register aborted on the cross-check",
      "register.aborted" in h.labels(), str(h.labels()))
check("9b the listing is gone from the shop", h.rows == [], str(h.rows))
check("9b the item is loose in the work tab (nothing put it back)",
      h.panel["loaded"] is True, str(h.panel))
check("9b the strand IS now announced", h.said("UNLISTED"),
      h.out()[-500:].replace("\n", " | "))
check("9b and recorded", "relist.stranded" in h.labels(), str(h.labels()))
note("9b", "the strand announcement and relist.stranded frame at "
     "trade.py:4438-4446 are new since the incident; in the recorded build "
     "this exit printed nothing, which is why the index shows a "
     "register.aborted and then silence.")


# ---------------------------------------------------------------------------
section("9c. the consequence: every later cycle dies before it records anything")
h = build(64)
stranded = {"yes": False}


def work_tab_reflects_the_strand(h):
    def patched(verbose=True):
        h.log("require_empty_work_tab")
        if stranded["yes"]:
            if verbose:
                trade.print(f"Inventory tab {trade.WORK_TAB} is not empty - "
                            "233 slot(s) in use")
            return False
        return True
    return patched


with h:
    h.patch("require_empty_work_tab", work_tab_reflects_the_strand(h))

    original_register = trade.register_item

    def register_then_strand(*a, **k):
        result = original_register(*a, **k)
        if not result:
            stranded["yes"] = True
        return result
    h.patch("register_item", register_then_strand)

    ok, exc = run(trade.run_loop, ["relist-rows 1"], 5.0, 0.0)

cycles = h.out().count("===== cycle ")
labels = h.labels()
after_strand = labels[labels.index("relist.stranded") + 1:] if \
    "relist.stranded" in labels else labels

print(f"  cycles run: {cycles}")
print(f"  labels recorded AFTER the strand: {after_strand}")
check("9c the run stops", "stopped early" in h.out(), h.out()[-300:])
check(f"9c after exactly MAX_CONSECUTIVE_FAILURES="
      f"{trade.MAX_CONSECUTIVE_FAILURES} cycles (the strand cycle is the "
      f"first of them)",
      cycles == trade.MAX_CONSECUTIVE_FAILURES, f"{cycles} cycles")
check("9c the breaker explains itself", h.said("cycles have failed in a row"),
      h.out()[-500:])

blind = [lab for lab in after_strand
         if lab not in ("cycle.start", "cycle.end", "loop.stopped")]
check("9c the failing cycles record NOTHING of their own", blind == [],
      f"recorded: {blind}")
note("9c the blind window",
     "require_empty_work_tab (trade.py:3181-3213) returns False without a "
     "single record(), and relist_rows returns before refresh_table -- so a "
     "cycle that dies there leaves no frame at all. In the build that ran on "
     "3 August that made the entire 5-hour outage invisible: the index's last "
     "entry is tab.register_open at 19:56:48. The cycle.start/cycle.end/"
     "loop.stopped entries added since (trade.py:5020, 5094, 5101) now bound "
     "the gap, but the failing STEP still records nothing.")

check("9c a cycle that cannot start records why", bool(blind),
      "require_empty_work_tab is the single most likely cycle-killer and it "
      "writes no frame; the operator gets a printed line and nothing on disk")

raise SystemExit(summary())
