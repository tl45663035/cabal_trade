"""Bug 1: a dialog is on screen and dialog_kind reports nothing.

Swept systematically, because the live failure was not "dialog_kind is broken"
-- it reads correctly most of the time. It is that dialog_kind reads the TITLE,
Tesseract's segmentation is crop-dependent, and ornate title glyphs vanish at
POPUP_REGION scale on SOME frames. So the interesting axis is not whether the
title reads, it is how UNRELIABLY it reads:

    honest   reads correctly every time          (the common case)
    blind    never reads the title               (the worst case)
    flaky    reads on alternate frames           (what actually happened)
    late     reads only after several attempts   (the 07:57 case)

Every combination of dialog kind and reliability is checked, and the property
is always the same: a dialog that is up must be SEEN, and must not survive the
code that is supposed to clear it. A modal left on screen covers the table, and
every read afterwards returns no rows.

The reverse is asserted just as hard: on a clean screen nothing may be detected
and no Cancel may be clicked. A detector that always says yes would click for
ever.
"""
from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade

KINDS = ("extension", "confirm", "receipt")
MODES = ("honest", "blind", "flaky", "late")


def rows():
    return [make_row(1, "Upgrade Core (Ultimate)", price=410_000, qty=100),
            make_row(2, "Force Core (Ultimate)", price=1_500_000, qty=50)]


def unreliable(h, mode, reveal_at=8):
    """Replace dialog_kind with a title reader of the given reliability."""
    real = h._dialog_kind
    seen = {"n": 0}

    def reader(source=None, words=None, **_):
        seen["n"] += 1
        if mode == "honest":
            return real(source)
        if mode == "blind":
            return None
        if mode == "flaky":
            return real(source) if seen["n"] % 2 == 0 else None
        if mode == "late":
            return real(source) if seen["n"] >= reveal_at else None
        raise AssertionError(f"unknown mode {mode!r}")

    h.patch("dialog_kind", reader)


def fresh(kind=None, **flags):
    h = Harness(rows=rows(), panel=empty_panel(), dialog=kind)
    for key, value in flags.items():
        setattr(h, key, value)
    return h


# ===========================================================================
section("A. an open dialog must be DETECTED, however badly the title reads")

for kind in KINDS:
    for mode in MODES:
        h = fresh(kind)
        with h:
            unreliable(h, mode)
            check(f"A {kind:9} / {mode:6}: dialog_present sees it",
                  trade.dialog_present() is True,
                  "a modal reported as absent gets left on screen, and then "
                  "covers the table for every read that follows")


# ===========================================================================
section("B. a clean screen must NOT look like a dialog, in any mode")

for mode in MODES:
    h = fresh(None)
    with h:
        unreliable(h, mode)
        check(f"B none / {mode:6}: dialog_present says no",
              trade.dialog_present() is False,
              "a detector that always says yes makes close_any_dialog click "
              "Cancel for ever at a screen with nothing on it")


# ===========================================================================
section("C. the honest limit: both signals gone means undetectable")

# Worth pinning down rather than pretending otherwise. If neither the title
# nor the Cancel button reads, nothing can see the dialog -- and the test says
# so, so a future change that claims to fix it has to prove it.
for kind in KINDS:
    h = fresh(kind, no_cancel_button=True)
    with h:
        unreliable(h, "blind")
        check(f"C {kind:9}: title AND button unreadable -> not detected",
              trade.dialog_present() is False,
              "documented limit, not a passing grade: the button finder is "
              "the last line and there is nothing behind it")


# ===========================================================================
section("D. close_any_dialog clears it whatever the title does")

for kind in KINDS:
    for mode in MODES:
        h = fresh(kind)
        with h:
            unreliable(h, mode)
            run(trade.close_any_dialog)
            check(f"D {kind:9} / {mode:6}: dialog gone",
                  h.dialog is None,
                  f"still {h.dialog!r} -- close_any_dialog is driven by the "
                  f"button finder precisely so the title cannot stop it")


# ===========================================================================
section("E. the next cycle's prepare clears it too")

for kind in KINDS:
    for mode in MODES:
        h = fresh(kind)
        with h:
            unreliable(h, mode)
            ok, exc = run(trade.prepare_for_actions)
            check(f"E {kind:9} / {mode:6}: dialog gone", h.dialog is None,
                  f"still {h.dialog!r} -- a cycle starting behind a modal "
                  f"reads no rows and fails before touching anything")
            check(f"E {kind:9} / {mode:6}: reported ready", ok is True,
                  f"got {ok!r} {exc!r}")


# ===========================================================================
section("F. prepare does not click at a screen with nothing on it")

for mode in MODES:
    h = fresh(None)
    with h:
        unreliable(h, mode)
        ok, exc = run(trade.prepare_for_actions)
        check(f"F none / {mode:6}: reported ready", ok is True,
              f"got {ok!r} {exc!r}")
        check(f"F none / {mode:6}: never announced an open dialog",
              not h.said("Dialog still open"), h.out()[-200:])
        cancels = [c for c in h.clicks()
                   if abs(c[1][0] - 1500) <= 40 and abs(c[1][1] - 900) <= 40]
        check(f"F none / {mode:6}: clicked no Cancel button",
              not cancels, f"{len(cancels)} Cancel click(s) on a clean screen")


# ===========================================================================
section("G. cancel_item never leaves a dialog behind, whatever it decides")

for mode in MODES:
    h = fresh()
    with h:
        unreliable(h, mode)
        ok, exc = run(trade.cancel_item, 1)
        check(f"G {mode:6}: no exception escaped", exc is None, repr(exc))
        check(f"G {mode:6}: no dialog left on screen", h.dialog is None,
              f"still {h.dialog!r} -- whether it committed or backed out, "
              f"leaving one up poisons every read that follows")
        # Whatever it decided, it must not half-commit.
        commits = h.out().count("Cancelled registration")
        check(f"G {mode:6}: committed at most once", commits <= 1,
              f"{commits} commits")
        if ok is False:
            check(f"G {mode:6}: a refusal says what state it left",
                  h.said("Nothing was changed")
                  or h.said("Backed out")
                  or h.said("could not close"),
                  h.out()[-300:])
        else:
            check(f"G {mode:6}: success means exactly one commit",
                  commits == 1, f"{commits} commits")


# ===========================================================================
section("H. a dialog appearing mid-recovery is still caught")

# close_any_dialog walks a chain: Cancel on the Extension dialog opens the
# confirmation dialog, whose Cancel closes both. A title that reads on the
# first and not the second must not strand the second.
for mode in MODES:
    h = fresh("extension")
    with h:
        unreliable(h, mode)
        run(trade.close_any_dialog)
        check(f"H {mode:6}: walked the whole chain", h.dialog is None,
              f"stopped at {h.dialog!r} -- the confirmation dialog behind the "
              f"extension one is still a modal over the table")


raise SystemExit(summary())
