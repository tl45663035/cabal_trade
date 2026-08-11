"""A dialog whose TITLE will not OCR must still be seen, and still be closed.

This is the cause behind cycles 1 and 2 of the 07:51 run, and cycles 5 and 6
of the 06:17 one -- four of the eight failed cycles that day.

    ABORTED: the Registration Extension dialog did not appear.
      dialog_kind sees: 'extension'          <- it WAS up
    Nothing was changed.                     <- ...and this closed nothing
    Note: the Trade window would not close with Escape.
    ########## 6/10 ##########
    The listings could not be read

dialog_kind identifies a dialog by reading its title, and its own docstring
records that Tesseract drops ornate title glyphs at POPUP_REGION scale. So it
returns None with a modal plainly on screen. Two decision points trusted it to
say "nothing is open":

  * cancel_item's abort recovery, which then closed nothing
  * prepare_for_actions' escape loop, which then declared the screen clean

The modal stayed up, covered the table, and every read afterwards returned no
rows -- for the rest of that cycle and the whole of the next.

Every test here keeps a dialog genuinely on screen while dialog_kind lies about
it, which is the exact condition that occurred.
"""
from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade

ITEM = "Upgrade Core (Ultimate)"


def rows():
    return [make_row(1, ITEM, price=410_000, qty=100),
            make_row(2, "Force Core (Ultimate)", price=1_500_000, qty=50)]


def blind(h):
    """Patch dialog_kind to always read None: the title never OCRs."""
    h.patch("dialog_kind", lambda *a, **k: None)


# ---------------------------------------------------------------------------
section("dialog_present sees what dialog_kind misses")

h = Harness(rows=rows(), panel=empty_panel(), dialog="extension")
with h:
    check("baseline: dialog_kind reports the open dialog",
          trade.dialog_kind(None) == "extension",
          f"got {trade.dialog_kind(None)!r}")
    blind(h)
    check("with the title unreadable, dialog_kind says nothing is open",
          trade.dialog_kind(None) is None, "harness setup check")
    check("dialog_present still sees it, via the Cancel button",
          trade.dialog_present() is True,
          "a modal that reports as absent is the whole failure -- it gets "
          "left on screen covering the table")

h = Harness(rows=rows(), panel=empty_panel(), dialog=None)
with h:
    check("dialog_present says NO when nothing is open",
          trade.dialog_present() is False,
          "a permanent yes would make close_any_dialog click for ever")


# ---------------------------------------------------------------------------
section("cycle 1: an abort must close the dialog it is aborting on")

h = Harness(rows=rows(), panel=empty_panel())
with h:
    blind(h)
    ok, exc = run(trade.cancel_item, 1)
    check("abort: cancel_item returned False", ok is False, f"got {ok!r} {exc!r}")
    check("abort: the dialog was NOT left on screen", h.dialog is None,
          f"dialog is still {h.dialog!r} -- this is what covered the table "
          f"for the next two cycles")
    check("abort: said it backed out rather than 'Nothing was changed'",
          h.said("Backed out of the open dialog"),
          f"{h.out()[-400:]}")
    check("abort: nothing was committed",
          not h.said("Cancelled registration"), h.out()[-300:])


# ---------------------------------------------------------------------------
section("cycle 2: the next cycle's prepare must clear it too")

h = Harness(rows=rows(), panel=empty_panel(), dialog="confirm")
with h:
    blind(h)
    ok, exc = run(trade.prepare_for_actions)
    check("prepare: the dialog was cleared", h.dialog is None,
          f"dialog is still {h.dialog!r} -- a cycle starting behind a modal "
          f"reads no rows and fails before touching anything")
    check("prepare: reported success", ok is True, f"got {ok!r} {exc!r}")

h = Harness(rows=rows(), panel=empty_panel(), dialog=None)
with h:
    ok, exc = run(trade.prepare_for_actions)
    check("prepare: a clean screen still passes", ok is True,
          f"got {ok!r} {exc!r}")
    check("prepare: did not click Cancel at a screen with no dialog",
          not h.said("Dialog still open"), h.out()[-300:])


# ---------------------------------------------------------------------------
section("a dialog that arrives LATE is used, not aborted on")

class LateDialog(Harness):
    """dialog_kind reads nothing until the wait has already given up."""

    def __init__(self, *a, reveal_after=6, **kw):
        super().__init__(*a, **kw)
        self.reveal_after = reveal_after
        self.kind_calls = 0

    def _dialog_kind(self, source=None, words=None, **_):
        self.kind_calls += 1
        if self.kind_calls < self.reveal_after:
            return None
        return super()._dialog_kind(source)


h = LateDialog(rows=rows(), panel=empty_panel())
with h:
    ok, exc = run(trade.cancel_item, 1)
    check("late dialog: did not abort on 'did not appear'",
          not h.said("did not appear") or h.said("it IS up on a fresh frame"),
          f"{h.out()[-500:]}")
    check("late dialog: the cancel went through", ok is True,
          f"got {ok!r} {exc!r} -- the dialog was there, one frame later than "
          f"the wait allowed")
    check("late dialog: it committed exactly once",
          h.out().count("Cancelled registration") == 1,
          f"{h.out().count('Cancelled registration')} commits")


# ---------------------------------------------------------------------------
section("the consequence: a cycle after an abort can still read the table")

h = Harness(rows=rows(), panel=empty_panel())
with h:
    blind(h)
    run(trade.cancel_item, 1)                     # aborts, closes the dialog
    h.patch("dialog_kind", h._dialog_kind)        # OCR recovers
    seen = trade.read_rows(None)
    check("after an abort the table is readable again", len(seen) == 2,
          f"read {len(seen)} row(s) -- if the dialog were still up this is "
          f"the 'listings could not be read' that ended cycle 1")


raise SystemExit(summary())
