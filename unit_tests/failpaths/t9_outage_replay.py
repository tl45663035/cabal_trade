import harness as H
from harness import (Harness, check, note, section, summary, run, make_row,
                     empty_panel)
import trade

ITEM = "Force Core(Highest)"

REAL_REQUIRE = trade.require_empty_work_tab
REAL_RECOVER = trade.recover_stranded_work_tab
REAL_ENSURE = trade.ensure_work_tab_empty


def drive_work_tab_by_slots(h, strand, clears):
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


section("9c/9d. a strand stops the run IMMEDIATELY -- rule changed 2026-08-08")
h = build(64)
strand = [(r, c) for r in range(1, 9) for c in range(1, 9)]
clears = {"yes": True}

with h:
    drive_work_tab_by_slots(h, strand, clears)
    ok, exc = run(trade.run_loop, ["relist-rows 1"], 5.0, 0.0)

cycles = h.out().count("===== cycle ")
labels = h.labels()
print(f"  cycles run: {cycles}")
print(f"  labels recorded: {sorted(set(labels))}")

check("9c the run stops", "stopped" in h.out(), h.out()[-400:])
check("9c on the FIRST cycle, not after three",
      cycles == 1,
      f"{cycles} cycles -- a strand does not clear itself, so retrying it "
      f"twice more only spends the breaker's budget arriving at the same "
      f"place")
check("9c the cycle that cannot start records WHY",
      "worktab.not_empty" in labels,
      f"recorded: {sorted(set(labels))} -- this is the single most likely "
      f"cycle-killer, and the 3 August outage was invisible because it wrote "
      f"no frame")
check("9c it says how much was stranded",
      (h.rec("worktab.not_empty") or {}).get("occupied") == 64,
      f"{h.rec('worktab.not_empty')}")
check("9c NOTHING was listed",
      "register_item" not in h.names(),
      f"{h.names()} -- listing an unnameable item at the strictest floor is "
      f"exactly what this change removed")
check("9c the strand is left for a human",
      len(strand) == 64, f"{len(strand)} slot(s) left")
check("9c and the reason reaches the operator",
      h.said("not empty") or h.said("tab"), h.out()[-400:])


raise SystemExit(summary())
