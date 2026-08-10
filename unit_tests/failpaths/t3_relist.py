"""_relist_cycle() / relist() / relist_rows() failure paths.

The question that matters here: when a cancel COMMITS and the register then
fails, is the operator told that an item is now sitting unlisted in the
inventory? That is the STRAND case.
"""
import harness as H
from harness import (Harness, check, note, section, summary, run, where,
                     make_row, empty_panel)
import trade

ITEM = "Upgrade Core (Ultimate)"
OTHER = "Force Core (Ultimate)"

STRAND_WORDS = ("already been cancelled", "unlisted", "in your inventory",
                "sitting in your inventory")


def fresh(rows=None, **flags):
    h = Harness(rows=rows if rows is not None else
                [make_row(1, ITEM, price=410_000, qty=100),
                 make_row(2, OTHER, price=1_500_000, qty=50)],
                panel=empty_panel())
    h.register_name = ITEM
    for key, value in flags.items():
        setattr(h, key, value)
    return h


def strand_reported(h) -> bool:
    return any(w.casefold() in h.out().casefold() for w in STRAND_WORDS)


def only_refresh_clicks(h) -> bool:
    """Refresh is the one click a cycle makes before it decides anything."""
    return all(abs(x - H.REFRESH_XY[0]) < 60 and abs(y - H.REFRESH_XY[1]) < 60
               for _, (x, y) in h.clicks())


# ---------------------------------------------------------------------------
section("3a. THE STRAND: the cancel commits, then the register fails")
# SHOULD: say loudly that row N was cancelled and the item is now unlisted in
# the inventory -- the code already prints exactly that for two OTHER
# post-cancel failures.
h = fresh(load_fails=True)          # ctrl+click never loads the shop slot
with h:
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True)

check("3a returns FAILED", outcome == trade.FAILED, f"got {outcome!r} {exc!r}")
check("3a the cancel really committed", "cancel.committed" in h.labels(),
      str(h.labels()))
check("3a the listing really is gone", len(h.rows) == 1, str(h.rows))
check("3a nothing was relisted", h.registered == [], str(h.registered))
if not strand_reported(h):
    note("3a the strand is never reported",
         "after cancel_item() succeeds, register_item() failing takes the "
         "`if not listed and not report.get('committed'): return FAILED` exit "
         "at trade.py:4427-4428. That exit prints NOTHING about the item "
         "having been cancelled. The same function prints "
         "'IMPORTANT: row N has already been cancelled - ITEM is in your "
         "inventory, unlisted' for the two neighbouring post-cancel failures "
         "(trade.py:4391 and 4408), so the message exists -- this path just "
         "does not use it. The operator sees only 'Nothing was listed.'")
check("3a the operator is told the item is cancelled and unlisted",
      strand_reported(h),
      "tail: " + h.out()[-500:].replace("\n", " | "))


# ---------------------------------------------------------------------------
section("3b. STRAND: the table will not refresh after a committed cancel")
h = fresh()
h.table_refreshes = True


def refresh_dies_after_cancel(h):
    """wait_for_table starts failing once the cancel has committed."""
    original = h._wait_for_table

    def patched(timeout=20.0, poll=1.0):
        if "cancel.committed" in h.labels():
            h.log("wait_for_table(False)")
            return False
        return original(timeout, poll)
    return patched


with h:
    h.patch("wait_for_table", refresh_dies_after_cancel(h))
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True)

check("3b returns FAILED", outcome == trade.FAILED, f"got {outcome!r}")
check("3b the cancel committed", "cancel.committed" in h.labels())
check("3b said the table did not refresh",
      h.said("did not finish refreshing after the cancel"), h.out()[-300:])
if not strand_reported(h):
    note("3b a second silent strand",
         "trade.py:4376-4378 returns FAILED after a committed cancel with no "
         "mention that the item is now loose in the inventory.")
check("3b the operator is told the item is cancelled and unlisted",
      strand_reported(h), h.out()[-400:].replace("\n", " | "))


# ---------------------------------------------------------------------------
section("3c. STRAND that IS reported: the returned item cannot be located")
h = fresh(returned_slots=[])
with h:
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True)

check("3c returns FAILED", outcome == trade.FAILED, f"got {outcome!r}")
check("3c the strand IS reported here", strand_reported(h),
      h.out()[-400:].replace("\n", " | "))
check("3c recorded the diff-empty frame", "inventory.diff_empty" in h.labels(),
      str(h.labels()))
note("3c", "this is the message 3a and 3b should be reusing")


# ---------------------------------------------------------------------------
section("3d. a row that shows 'Receive'")
h = fresh(rows=[make_row(1, ITEM, action="receive", price=410_000, qty=100),
                make_row(2, OTHER, price=1_500_000, qty=50)])
with h:
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True)

check("3d returns SOLD_OUT when the stack fully sold",
      outcome == trade.SOLD_OUT, f"got {outcome!r} {exc!r}")
check("3d collected the proceeds", h.said("clicking Receive"), h.out()[:400])
check("3d nothing was cancelled", "cancel.committed" not in h.labels())
check("3d nothing was registered", h.registered == [])

# a partial sale: quantity remains, so the cycle must go round again
h = fresh(rows=[make_row(1, ITEM, action="receive", price=410_000, qty=100),
                make_row(2, OTHER, price=1_500_000, qty=50)])
remainder = make_row(1, ITEM, action="change", price=410_000, qty=100)


def collect_leaves_remainder(h):
    def patched():
        row = h._cancel_target
        if row is not None and row in h.rows:
            h.rows[h.rows.index(row)] = remainder
            h._renumber()
    return patched


with h:
    h._collect = collect_leaves_remainder(h)
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True)
check("3d2 a partial sale relists the remainder",
      outcome == trade.RELISTED, f"got {outcome!r} {exc!r}; "
      + h.out()[-300:].replace("\n", " | "))

# still Receive on the final attempt
h = fresh(rows=[make_row(1, ITEM, action="receive", price=410_000, qty=100)])
with h:
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True,
                       attempts=1)
check("3d3 a sold row on the final attempt is FAILED, not SOLD_OUT",
      outcome == trade.FAILED, f"got {outcome!r}")
check("3d3 and it clicked nothing but Refresh", only_refresh_clicks(h),
      str(h.clicks()))


# ---------------------------------------------------------------------------
section("3e. the row changes identity between the read and the act")
# The caller chose ITEM at row 1; by the time relist reads the table it has
# moved to row 2. SHOULD: follow it, and cancel the right listing.
h = fresh(rows=[make_row(1, OTHER, price=1_500_000, qty=50),
                make_row(2, ITEM, price=410_000, qty=100)])
ref = trade.RowRef(ITEM, 100, 410_000, 0)
with h:
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True,
                       expect=ref)
check("3e followed the item to its new row", h.said("moved from row 1 to 2"),
      h.out()[:600].replace("\n", " | "))
check("3e cancelled the RIGHT listing",
      all(r.name != ITEM or r.price != 410_000 or r.qty != 100
          for r in h.rows[:1]) and OTHER in [r.name for r in h.rows],
      str(h.rows))
check("3e outcome is RELISTED", outcome == trade.RELISTED, f"got {outcome!r}")

# the item is gone entirely
h = fresh(rows=[make_row(1, OTHER, price=1_500_000, qty=50)])
with h:
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True,
                       expect=ref)
check("3e2 a vanished item is FAILED and clicks nothing but Refresh",
      outcome == trade.FAILED and only_refresh_clicks(h),
      f"{outcome!r} {h.clicks()}")

# the table shifts BETWEEN _relist_cycle's read and cancel_item's re-read
h = fresh(rows=[make_row(1, ITEM, price=410_000, qty=100),
                make_row(2, OTHER, price=1_500_000, qty=50)])


def shift_on_second_read(h):
    original = h._read_rows

    def patched(source=None):
        rows = original(source)
        if h.n_rows >= 2 and rows and rows[0].name == ITEM:
            shifted = [make_row(1, OTHER, price=1_500_000, qty=50),
                       make_row(2, ITEM, price=410_000, qty=100)]
            h.rows = shifted
            return list(shifted)
        return rows
    return patched


with h:
    h.patch("read_rows", shift_on_second_read(h))
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True,
                       expect=ref)
# The shift is caught on attempt 1 and RECOVERED on attempt 2 -- behaviour
# changed 2026-08-08 when cancel_item started reporting whether it committed.
#
# Attempt 1 detects that the row moved and aborts BEFORE the Change click, so
# nothing is cancelled and `committed` is False. That is now visible to the
# caller, which re-reads the table, re-locates the item by identity at its new
# position, and relists it. Previously every such abort returned one bare
# False, indistinguishable from "committed but unverified", so the caller had
# to assume the dangerous case and abandon the row.
#
# The safety property is unchanged, and the third check is what asserts it:
# recovering must not mean relisting whatever slid into row 1.
_labels = h.labels()
check("3e3 the shift is caught before anything is cancelled",
      "cancel.aborted" in _labels
      and (("cancel.committed" not in _labels)
           or _labels.index("cancel.aborted") < _labels.index("cancel.committed")),
      f"labels={_labels}")
check("3e3 and the retry recovers the row instead of losing it",
      outcome == trade.RELISTED,
      f"{outcome!r} -- a cancel that never committed left the listing on the "
      f"market untouched, so trying again is free and correct")
check("3e3 the item relisted is the one that was asked for",
      not any(r.name == OTHER and r.price == 410_000 for r in h.rows),
      f"{[(r.name, r.price) for r in h.rows]}")
check("3e3 explained the abort",
      h.said("did not commit") or h.said("nothing was cancelled")
      or h.said("Cancel did not complete"),
      h.out()[-400:].replace(chr(10), " | "))


# ---------------------------------------------------------------------------
section("3f. an empty table")
h = fresh(rows=[])
with h:
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True)
check("3f relist on an empty table is FAILED",
      outcome == trade.FAILED, f"got {outcome!r} {exc!r}")
check("3f clicked nothing but Refresh", only_refresh_clicks(h), str(h.clicks()))

h = fresh(rows=[])
with h:
    ok, exc = run(trade.relist_rows, [1])
check("3f2 relist_rows on an empty table is False", ok is False, f"got {ok!r}")
check("3f2 refused to treat it as an empty shop",
      h.said("No listings visible"), h.out()[-300:])


# ---------------------------------------------------------------------------
section("3g. a table that will not refresh")
h = fresh(table_refreshes=False)
with h:
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True)
check("3g returns FAILED", outcome == trade.FAILED, f"got {outcome!r}")
check("3g said it could not refresh", h.said("Could not refresh the table"),
      h.out()[-300:])
check("3g clicked nothing but Refresh",
      all(abs(x - H.REFRESH_XY[0]) < 60 for _, (x, y) in h.clicks()),
      str(h.clicks()))
check("3g nothing was cancelled", "cancel.committed" not in h.labels())


# ---------------------------------------------------------------------------
section("3h. relist_rows: every row reads as already sold")
h = fresh()
with h:
    h.patch("relist", lambda *a, **k: trade.SOLD_OUT)
    ok, exc = run(trade.relist_rows, [1, 2])
check("3h a batch that only collected is still True",
      ok is True, f"got {ok!r}")

h = fresh()
with h:
    # every row vanishes from the live read -> 'already sold out, skipping'
    def vanish(source=None):
        h.n_rows += 1
        return [] if h.n_rows > 1 else list(h.rows)
    h.patch("read_rows", vanish)
    ok, exc = run(trade.relist_rows, [1, 2])
check("3h2 an unreadable live table stops the batch", ok is False, f"{ok!r}")
check("3h2 and says why", h.said("could not be read"), h.out()[-300:])


# ---------------------------------------------------------------------------
section("3i. relist_rows: a mid-batch failure does NOT stop a clean batch")
# This used to assert the opposite, and the opposite was measured to be wrong:
# three consecutive cycles each relisted row 1, failed on row 2, never
# attempted rows 3-10, and still counted as total failures -- which tripped the
# breaker on a run doing half its work. One poison row froze 80% of the shop.
#
# The rule now is decidable rather than a guess: if the work tab is CLEAN the
# cancel either did not happen or was undone, the next row starts from a known
# state, and the batch carries on. A dirty tab means something is stranded and
# every later row would fail its precondition identically, so that still stops.
h = fresh()
with h:
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        return trade.RELISTED if calls["n"] == 1 else trade.FAILED
    h.patch("relist", flaky)
    ok, exc = run(trade.relist_rows, [1, 2])
check("3i a clean tab lets the batch finish", ok is True, f"got {ok!r}")
check("3i said the failure was confined to that row",
      h.said("confined to this row"), h.out()[-400:])
check("3i and named the failed row in the summary",
      h.said("failed and were skipped"), h.out()[-600:])

# The other half of the rule: a DIRTY work tab still stops the batch, because
# the strand makes every later row fail the same way.
h = fresh()
with h:
    calls = {"n": 0}

    def flaky2(*a, **k):
        calls["n"] += 1
        return trade.RELISTED if calls["n"] == 1 else trade.FAILED
    h.patch("relist", flaky2)
    # Clean at the START of the batch, dirty only AFTER the row that failed.
    # Patching it to fail unconditionally trips the start-of-batch check
    # instead, and the batch never reaches row 2 -- so the test would pass
    # while proving nothing about the mid-batch rule it names.
    h.patch("require_empty_work_tab", lambda verbose=True: calls["n"] < 2)
    ok, exc = run(trade.relist_rows, [1, 2])
check("3i2 a dirty tab stops the batch", ok is False, f"got {ok!r}")
check("3i2 and says every later row would fail the same way",
      h.said("is not clean") or h.said("not attempted"), h.out()[-500:])

raise SystemExit(summary())

