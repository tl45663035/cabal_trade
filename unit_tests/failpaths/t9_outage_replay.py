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

# Captured before any Harness installs its stubs. 9c/9d put these BACK, so the
# real functions run and the claims about them are testable.
#
# The earlier 9c replaced require_empty_work_tab wholesale and then asserted
# that it recorded a frame. It never could: the assertion was measuring the
# stub, not the function, so the "no frame on disk" finding stayed open for as
# long as the test existed and no fix to trade.py could ever have closed it.
# trade.py has recorded worktab.not_empty since the day that was written.
REAL_REQUIRE = trade.require_empty_work_tab
REAL_RECOVER = trade.recover_stranded_work_tab
REAL_ENSURE = trade.ensure_work_tab_empty


def drive_work_tab_by_slots(h, strand, clears):
    """Run the REAL work-tab code, with the strand modelled in one place.

    Everything below the API under test stays stubbed (no capture, no OCR, no
    input); the only thing this decides is what the inventory grid contains.
    require_empty_work_tab, recover_stranded_work_tab and ensure_work_tab_empty
    all then run for real, and agree with each other by construction -- which
    the old split model did not.
    """
    h.patch("require_empty_work_tab", REAL_REQUIRE)
    h.patch("recover_stranded_work_tab", REAL_RECOVER)
    h.patch("ensure_work_tab_empty", REAL_ENSURE)
    h.patch("occupied_slots", lambda *a, **k: list(strand))

    def register(row, col, **kw):
        h.log("register_item", row, col)
        if clears["yes"]:
            strand.clear()
            return True
        return False
    h.patch("register_item", register)


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
section("9c. a strand that CANNOT be cleared still stops the run")
# The historical outcome, and still the right one: if the stack will not go
# back on the shop, starting a cycle would diff a dirty tab and could pick up
# an unrelated item. Stopping is correct. What was wrong was that this was the
# ONLY outcome -- see 9d.
h = build(64)
strand = [(r, c) for r in range(1, 9) for c in range(1, 9)]   # 64 slots, as logged
clears = {"yes": False}

with h:
    drive_work_tab_by_slots(h, strand, clears)
    ok, exc = run(trade.run_loop, ["relist-rows 1"], 5.0, 0.0)

cycles = h.out().count("===== cycle ")
labels = h.labels()

print(f"  cycles run: {cycles}")
print(f"  labels recorded: {sorted(set(labels))}")
check("9c the run stops", "stopped early" in h.out(), h.out()[-300:])
check(f"9c after exactly MAX_CONSECUTIVE_FAILURES="
      f"{trade.MAX_CONSECUTIVE_FAILURES} cycles (the strand cycle is the "
      f"first of them)",
      cycles == trade.MAX_CONSECUTIVE_FAILURES, f"{cycles} cycles")
check("9c the breaker explains itself", h.said("cycles have failed in a row"),
      h.out()[-500:])

# The blind window that made the 3 August outage invisible: the index's last
# entry was tab.register_open at 19:56:48, then nothing for five hours. The
# failing STEP now writes its own frame, so the reason is on disk and not only
# in a printed line the operator never saw.
blind = [lab for lab in labels
         if lab not in ("cycle.start", "cycle.end", "loop.stopped")]
check("9c the cycle that cannot start records WHY",
      "worktab.not_empty" in labels,
      f"recorded: {sorted(set(blind))} -- require_empty_work_tab is the single "
      f"most likely cycle-killer; if it writes no frame the operator gets a "
      f"printed line and nothing on disk")
check("9c the recovery attempt is recorded too",
      "strand.recovering" in labels, f"{sorted(set(blind))}")
check("9c it says how much was stranded",
      (h.rec("worktab.not_empty") or {}).get("occupied") == 64,
      f"{h.rec('worktab.not_empty')}")
check("9c it did try to clear it before giving up",
      "register_item" in h.names(),
      "stopping without attempting the recovery is the old behaviour")


# ---------------------------------------------------------------------------
section("9d. a strand that CAN be cleared no longer ends the run")
# This is the finding that stayed open longest: "the cause is recorded now, but
# the recovery does not exist." It exists now, and this is the same replay with
# the one thing changed that the fix changes.
h = build(64)
strand = [(r, c) for r in range(1, 9) for c in range(1, 9)]
clears = {"yes": True}

with h:
    drive_work_tab_by_slots(h, strand, clears)
    h.patch("relist", lambda *a, **k: trade.RELISTED)
    ok, exc = run(trade.relist_rows, [1])

check("9d the batch runs instead of aborting", ok is True, f"{ok!r} {exc!r}")
check("9d the work tab was cleared", strand == [], f"{len(strand)} slot(s) left")
check("9d the strand was re-listed", "register_item" in h.names(), f"{h.names()}")
check("9d it did not print the abort", not h.said("must be empty to start"),
      h.out()[-300:])
check("9d and it is on the record", "strand.recovering" in h.labels(),
      str(sorted(set(h.labels()))))

raise SystemExit(summary())
