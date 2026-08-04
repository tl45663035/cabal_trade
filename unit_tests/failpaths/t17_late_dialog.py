"""Bug 2: the dialog arrives after the wait has given up.

From the 07:51 log, two consecutive lines:

    ABORTED: the Registration Extension dialog did not appear.
      dialog_kind sees: 'extension'

The wait polled, saw nothing, and expired. The diagnostic probe fired one line
later and read the dialog at 97% confidence. It had arrived in between.

The axis swept here is WHEN it becomes visible, counted in dialog_kind calls:
early enough for the wait to catch it, late enough that only the diagnostic
probe does, or never at all. The invariants must hold at every point on that
axis, because the boundary moves with the machine's speed and the game's --
which is exactly why this was intermittent.

The strongest property is the monotonicity one at the end: a dialog appearing
LATER can never turn a failure into a success. If the pass/fail pattern is not
a clean prefix, something is deciding on timing noise rather than on evidence.
"""
from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade

# Spread across the whole range: inside the wait, around its expiry, past the
# diagnostic probe, and never.
REVEALS = (1, 2, 3, 4, 6, 8, 10, 13, 16, 20, 25, 30, 40, 60, None)


def rows():
    return [make_row(1, "Upgrade Core (Ultimate)", price=410_000, qty=100),
            make_row(2, "Force Core (Ultimate)", price=1_500_000, qty=50)]


def run_with_reveal(reveal_at):
    """Drive cancel_item with the title becoming readable at call `reveal_at`.

    reveal_at=None means it never reads at all.
    """
    h = Harness(rows=rows(), panel=empty_panel())
    with h:
        real = h._dialog_kind
        seen = {"n": 0}

        def reader(source=None):
            seen["n"] += 1
            if reveal_at is None or seen["n"] < reveal_at:
                return None
            return real(source)

        h.patch("dialog_kind", reader)
        ok, exc = run(trade.cancel_item, 1)
        return {
            "ok": ok,
            "exc": exc,
            "commits": h.out().count("Cancelled registration"),
            "dialog": h.dialog,
            "used_late": h.said("it IS up on a fresh frame"),
            "out": h.out(),
        }


section("invariants at every point on the arrival axis")

results = {}
for reveal in REVEALS:
    label = "never" if reveal is None else f"call {reveal:>2}"
    r = run_with_reveal(reveal)
    results[reveal] = r

    check(f"{label}: no exception escaped", r["exc"] is None, repr(r["exc"]))
    check(f"{label}: committed at most once", r["commits"] <= 1,
          f"{r['commits']} commits -- a retry that re-cancels pulls a second "
          f"listing that was never asked for")
    check(f"{label}: no dialog left on screen", r["dialog"] is None,
          f"still {r['dialog']!r} -- this is what covered the table for the "
          f"rest of the cycle and the whole of the next")
    if r["ok"]:
        check(f"{label}: success means exactly one commit", r["commits"] == 1,
              f"{r['commits']} commits")
    else:
        check(f"{label}: refusal means nothing was committed",
              r["commits"] == 0, f"{r['commits']} commits after a refusal")


section("the ends of the axis behave as they must")

check("visible immediately: the cancel goes through",
      results[1]["ok"] is True and results[1]["commits"] == 1,
      f"ok={results[1]['ok']} commits={results[1]['commits']}")

check("never visible: refused, and said so",
      results[None]["ok"] is False
      and "did not appear" in results[None]["out"],
      f"ok={results[None]['ok']}")

check("never visible: the dialog was still closed on the way out",
      results[None]["dialog"] is None,
      "the abort path must clear what it aborted on")


section("a dialog arriving later can never help")

# The property that matters most: the set of arrival points that succeed has to
# be a contiguous prefix. A hole in it means the outcome is being decided by
# timing noise rather than by whether the dialog is actually there.
order = [r for r in REVEALS if r is not None] + [None]
outcomes = [(r, results[r]["ok"]) for r in order]
first_fail = next((i for i, (_r, ok) in enumerate(outcomes) if not ok),
                  len(outcomes))
after = outcomes[first_fail:]
check("pass/fail is a clean prefix, with no holes",
      all(not ok for _r, ok in after),
      "outcomes: " + ", ".join(
          f"{'never' if r is None else r}={'ok' if ok else 'FAIL'}"
          for r, ok in outcomes))

recovered = [r for r in order if results[r]["used_late"]]
print(f"  arrival points rescued by the fresh-frame recheck: "
      + (", ".join(str(r) for r in recovered) if recovered else "none"))
succeeded = [r for r in order if results[r]["ok"]]
print(f"  arrival points that succeed: "
      + ", ".join("never" if r is None else str(r) for r in succeeded))


section("the recheck must not invent a dialog that is not there")

# The fix accepts the diagnostic probe as evidence. It must therefore refuse
# when the probe shows something ELSE, or nothing -- otherwise it is a guess
# wearing evidence's clothes.
h = Harness(rows=rows(), panel=empty_panel())
with h:
    h.patch("dialog_kind", lambda *a, **k: None)
    ok, exc = run(trade.cancel_item, 1)
    check("probe sees nothing: still refuses", ok is False, f"got {ok!r}")
    check("probe sees nothing: nothing committed",
          h.out().count("Cancelled registration") == 0, h.out()[-200:])

h = Harness(rows=rows(), panel=empty_panel())
with h:
    # The probe reports the WRONG dialog: not the one a Change click opens.
    h.patch("dialog_kind", lambda *a, **k: "receipt")
    ok, exc = run(trade.cancel_item, 1)
    check("probe sees the wrong dialog: refuses", ok is False, f"got {ok!r}")
    check("probe sees the wrong dialog: nothing committed",
          h.out().count("Cancelled registration") == 0, h.out()[-200:])
    check("probe sees the wrong dialog: no exception", exc is None, repr(exc))


raise SystemExit(summary())
