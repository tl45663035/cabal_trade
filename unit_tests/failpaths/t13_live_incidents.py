"""The five ways cycles actually died on 2026-08-04, replayed.

Every scenario here is taken from a real run log, not invented. The names carry
the cycle number so a failure points at the incident it is guarding:

  cycle 1  two rows relisted and VERIFIED, then the post-row table wait timed
           out and the whole batch was abandoned -- 8 rows never attempted and
           a productive cycle scored as a failure
  cycle 3  cancel committed, the returned item could not be identified in the
           work tab, so it was left stranded and every later row failed the
           same precondition
  cycle 5  the Registration Extension dialog never appeared; nothing was
           changed, so the batch SHOULD carry on to the other nine rows
  cycle 6  the table could not be read at all at the start of the batch
  cycle 7  a sold row was collected and a duplicate made the follow-up
           ambiguous, stranding the collected item

What matters in each is not the message but the DECISION: keep going, or stop.
Getting that wrong is what turned single bad rows into dead runs.
"""
from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade

ITEM = "Upgrade Core (Ultimate)"
OTHER = "Force Core (Ultimate)"


def ten_rows():
    return [make_row(i, f"Item {i:02d}", price=100_000 + i, qty=50 + i)
            for i in range(1, 11)]


def relisted_after(h, failures, strands=False):
    """Patch relist(): FAILED for the named rows, RELISTED otherwise.

    `strands` models what actually happened on cycles 3 and 7 -- the row's
    cancel COMMITTED and then could not be completed, leaving an item in the
    work tab. The tab must be clean when the batch starts, or relist_rows
    refuses to begin at all, which is a different (and correct) refusal.
    """
    def fake(row, *a, expect=None, **kw):
        name = expect.name if expect is not None else f"row {row}"
        h.log("relist_call", row, expect=expect)
        outcome = trade.FAILED if name in failures else trade.RELISTED
        if strands:
            h.work_tab_empty = False
        return outcome
    return fake


def acted(h):
    return [kw["expect"].name for n, _a, kw in h.calls
            if n == "relist_call" and kw.get("expect") is not None]


# ---------------------------------------------------------------------------
section("cycle 1: a verified relist must survive a post-row refresh timeout")

h = Harness(rows=ten_rows(), panel=empty_panel())
with h:
    h.patch("relist", relisted_after(h, set()))
    h.table_refreshes = False          # every wait_for_table times out
    h.work_tab_empty = True            # ...but nothing is stranded
    ok, exc = run(trade.relist_rows, [1, 2, 3])
    check("c1 batch still succeeded", ok is True, f"got {ok!r} {exc!r}")
    check("c1 all three rows were attempted",
          acted(h) == ["Item 01", "Item 02", "Item 03"],
          f"{acted(h)} -- a refresh timeout after a COMMITTED relist says "
          f"nothing about the rows after it")
    check("c1 said the relist completed anyway",
          h.said("the relist completed") or h.said("continuing with"),
          h.out()[-400:])


# ---------------------------------------------------------------------------
section("cycle 1b: the same timeout WITH a dirty tab must still stop")

h = Harness(rows=ten_rows(), panel=empty_panel())
with h:
    # Clean at the start, dirty once row 1 has been through relist().
    h.patch("relist", relisted_after(h, set(), strands=True))
    h.table_refreshes = False
    ok, exc = run(trade.relist_rows, [1, 2, 3])
    check("c1b batch stopped", ok is False, f"got {ok!r}")
    check("c1b stopped after the first row", len(acted(h)) == 1,
          f"attempted {acted(h)} -- a stranded item fails every later row "
          f"identically, so continuing just burns the shop")
    check("c1b named the dirty tab", h.said("not clean"), h.out()[-300:])


# ---------------------------------------------------------------------------
section("cycle 5: one row aborting cleanly must not freeze the other nine")

h = Harness(rows=ten_rows(), panel=empty_panel())
with h:
    h.patch("relist", relisted_after(h, {"Item 01"}))
    h.work_tab_empty = True            # the abort changed nothing
    ok, exc = run(trade.relist_rows, [1, 2, 3])
    check("c5 batch continued past the bad row",
          acted(h) == ["Item 01", "Item 02", "Item 03"],
          f"{acted(h)} -- this is the 'one poison row froze 80% of the shop' "
          f"case")
    check("c5 said the failure was confined to that row",
          h.said("confined to this row"), h.out()[-400:])
    check("c5 reported which row was skipped", h.said("Item 01"),
          h.out()[-500:])


# ---------------------------------------------------------------------------
section("cycle 3: a failure with a dirty tab stops the batch")

h = Harness(rows=ten_rows(), panel=empty_panel())
with h:
    # The cancel committed and the item could not be re-listed, so it is now
    # sitting in the work tab: exactly the state cycle 3 stopped in.
    h.patch("relist", relisted_after(h, {"Item 01"}, strands=True))
    ok, exc = run(trade.relist_rows, [1, 2, 3])
    check("c3 batch stopped", ok is False, f"got {ok!r}")
    check("c3 rows after the failure were NOT attempted",
          acted(h) == ["Item 01"], f"{acted(h)}")
    check("c3 said how many were left", h.said("not attempted"),
          h.out()[-300:])


# ---------------------------------------------------------------------------
section("cycle 6: an unreadable table is never an empty shop")

h = Harness(rows=[], panel=empty_panel())
with h:
    h.patch("relist", relisted_after(h, set()))
    ok, exc = run(trade.relist_rows, [1, 2])
    check("c6 refused", ok is False, f"got {ok!r}")
    check("c6 acted on nothing", acted(h) == [], f"{acted(h)}")
    check("c6 said the table was unreadable", h.said("No listings visible"),
          h.out()[-300:])
    check("c6 clicked nothing at all", h.clicks() == [], f"{h.clicks()[:4]}")


# ---------------------------------------------------------------------------
section("cycle 6b: a table that goes unreadable MID-batch also stops")

h = Harness(rows=ten_rows(), panel=empty_panel())
with h:
    h.patch("relist", relisted_after(h, set()))
    # The first read works; the table then stops being readable.
    original = h._read_rows
    state = {"n": 0}

    def flaky(source=None):
        state["n"] += 1
        return original(source) if state["n"] <= 2 else []

    h.patch("read_rows", flaky)
    ok, exc = run(trade.relist_rows, [1, 2, 3])
    check("c6b stopped rather than skipping rows as sold",
          ok is False, f"got {ok!r}")
    check("c6b said so explicitly",
          h.said("could not be read") or h.said("No listings visible"),
          h.out()[-400:])


# ---------------------------------------------------------------------------
section("the breaker: consecutive failures stop the run, successes reset it")

h = Harness(rows=ten_rows(), panel=empty_panel())
with h:
    cycles = {"n": 0}

    def always_fail(*a, **k):
        cycles["n"] += 1
        return False

    h.patch("run_sequence", always_fail)
    h.patch("prepare_for_actions", lambda *a, **k: True)
    ok, exc = run(trade.run_loop, ["relist-rows 1-10"], 60.0, 0.0)
    check("breaker stopped the run", ok is False, f"got {ok!r} {exc!r}")
    check(f"breaker fired at {trade.MAX_CONSECUTIVE_FAILURES} cycles, not later",
          cycles["n"] == trade.MAX_CONSECUTIVE_FAILURES,
          f"ran {cycles['n']} cycles for a 60-minute window -- the point of "
          f"the breaker is not repeating a broken cycle for hours")
    check("breaker said why", h.said("failed in a row"), h.out()[-400:])

h = Harness(rows=ten_rows(), panel=empty_panel())
with h:
    outcomes = [False, False, True, False, False]
    seq = {"n": 0}

    def alternating(*a, **k):
        seq["n"] += 1
        return outcomes[seq["n"] - 1] if seq["n"] <= len(outcomes) else False

    h.patch("run_sequence", alternating)
    h.patch("prepare_for_actions", lambda *a, **k: True)
    run(trade.run_loop, ["relist-rows 1-10"], 60.0, 0.0)
    check("a success resets the consecutive-failure count",
          seq["n"] > trade.MAX_CONSECUTIVE_FAILURES,
          f"only {seq['n']} cycles ran; a green cycle in the middle must "
          f"clear the tally or a flaky shop stops after three bad cycles "
          f"however many good ones follow")


raise SystemExit(summary())
