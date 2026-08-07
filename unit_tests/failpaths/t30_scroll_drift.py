"""measure_shift must survive the table moving under it -- but never guess.

From the 07:57 run of 2026-08-06: cycles 5 and 6 both died on

    could not tell how far the view moved - refusing to guess which listing is
    which

and the breaker stopped a run that was otherwise working. The old test was

    all(b[i] == a[j] for i, j in overlap)

so ONE changed row anywhere in the overlap made the true offset match nothing,
and then nothing matched at all. Not ambiguity -- zero candidates. Three things
cause that here and each was enough alone:

  * a quantity misreading (140 -> 120 and 130 -> 30 both recorded that run)
  * a listing selling during the ~18s scroll
  * this script's own repricing between the two reads

The fix keeps the exact test first and unchanged, then falls back to scoring.
The safety property is the MARGIN: the best offset must beat the runner-up by
SCROLL_MATCH_MARGIN rows. Picking the wrong offset means cancelling the wrong
listing, which is far worse than losing a cycle, so a close call must still
refuse. Most of this file is that refusal.
"""
from harness import check, section, summary

import trade


def mk(i, name, qty=None, price=None, action="change"):
    return trade.Row(index=i, name=name, action=action, price=price, qty=qty,
                     change=(0, i * 79), top=i * 79 - 30, bottom=i * 79 + 30)


def win(src, start, n=10):
    """A 10-row screen onto `src`, renumbered 1..n as read_rows would."""
    return [mk(j + 1, r.name, r.qty, r.price, r.action)
            for j, r in enumerate(src[start:start + n])]


FULL = [mk(i, f"Item {i:02d}", 10 + i, 100_000 + i) for i in range(1, 31)]

# The shop exactly as it stood when cycle 6 died.
LIVE = ([mk(1, "Force Gem Package (x400)", 2, 187_000_000),
         mk(2, "Yekaterina VIP Membership", 2, 108_999_999),
         mk(3, "Force Core(High)", 2, 210_000),
         mk(4, "Force Core (Ultimate)", 1, 381_614),
         mk(5, "Force Core(Medium)", 1, 100_000),
         mk(6, "Siena's Unbinding Stone", 1, 75_000_000),
         mk(7, "Force Core(High)", 0, 210_000, "receive")]
        + [mk(i, "(empty)", None, None, "register") for i in range(8, 15)]
        + [mk(15, "Force Core(Highest)", 0, 200_000, "receive"),
           mk(16, "Force Core(High)", 250, 215_000),
           mk(17, "(empty)", None, None, "register"),
           mk(18, "Force Core(High)", 247, 215_000),
           mk(19, "Force Core (Ultimate)", 250, 381_615),
           mk(20, "Force Core(High)", 250, 215_000)])


# ===========================================================================
section("the exact case still answers exactly")

for off in (1, 3, 5, 7):
    check(f"a still table, scroll {off}",
          trade.measure_shift(win(FULL, 0), win(FULL, off)) == off,
          f"got {trade.measure_shift(win(FULL, 0), win(FULL, off))!r}")

check("the real emptied shop, scroll 7",
      trade.measure_shift(win(LIVE, 0), win(LIVE, 7)) == 7,
      f"got {trade.measure_shift(win(LIVE, 0), win(LIVE, 7))!r} -- a block of "
      f"identical empty rows does NOT confuse it; that was my first guess at "
      f"the cause and it was wrong")


# ===========================================================================
section("one changed row no longer loses the cycle")

def drift(src, off, changes):
    """The scrolled screen, with `changes` rows perturbed."""
    rows = win(src, off)
    for idx, fn in changes:
        rows[idx] = fn(rows[idx])
    return rows


qty_misread = (0, lambda r: mk(r.index, r.name, (r.qty or 0) - 20, r.price))
sold_midway = (1, lambda r: mk(r.index, r.name, 0, r.price, "receive"))
repriced = (2, lambda r: mk(r.index, r.name, r.qty, (r.price or 0) + 5_000))

check("a quantity misreading (the recorded 140 -> 120)",
      trade.measure_shift(win(FULL, 0), drift(FULL, 7, [qty_misread])) == 7,
      f"got {trade.measure_shift(win(FULL, 0), drift(FULL, 7, [qty_misread]))!r}")
check("a listing selling during the scroll",
      trade.measure_shift(win(FULL, 0), drift(FULL, 7, [sold_midway])) == 7, "")
check("a price moving between reads",
      trade.measure_shift(win(FULL, 0), drift(FULL, 7, [repriced])) == 7, "")
# A 7-row step leaves only 3 rows overlapping, so all three drifting means
# there is no surviving evidence at all. Refusing is the right answer, not a
# shortcoming -- what recovers it is the smaller step, below.
check("all three drift at a 7-row step: refuses",
      trade.measure_shift(win(FULL, 0),
                          drift(FULL, 7, [qty_misread, sold_midway, repriced]))
      is None,
      f"got {trade.measure_shift(win(FULL, 0), drift(FULL, 7, [qty_misread, sold_midway, repriced]))!r}")
check("...and the smaller step carries all three",
      trade.measure_shift(win(FULL, 0),
                          drift(FULL, 3, [qty_misread, sold_midway])) == 3,
      f"got {trade.measure_shift(win(FULL, 0), drift(FULL, 3, [qty_misread, sold_midway]))!r} "
      f"-- 7 rows overlap at a 3-row step, so two drifting still leaves five")

# On the real shop a 7-row step lands the whole overlap inside the block of
# empty slots, so there is nothing distinctive to agree on. It must refuse --
# scoring plain matches here returned 5 for a view that had moved 7, which
# would have cancelled the wrong listing. enumerate_listings recovers by
# re-sweeping at SCROLL_STEP_FALLBACK, which leaves 7 rows overlapping.
got = trade.measure_shift(win(LIVE, 0), drift(LIVE, 7, [sold_midway]))
check("the live shop, drift inside the empty block: refuses, never guesses",
      got is None,
      f"got {got!r} -- 5 was the answer before empty rows were excluded from "
      f"the vote, and 5 is a different listing")
check("...and the smaller step measures it correctly",
      trade.measure_shift(win(LIVE, 0), drift(LIVE, 3, [qty_misread])) == 3,
      f"got {trade.measure_shift(win(LIVE, 0), drift(LIVE, 3, [qty_misread]))!r}")
check("SCROLL_STEP_FALLBACK leaves more overlap than SCROLL_STEP",
      trade.SCROLL_STEP_FALLBACK < trade.SCROLL_STEP,
      f"{trade.SCROLL_STEP_FALLBACK} vs {trade.SCROLL_STEP}")


# ===========================================================================
section("but it still refuses rather than guess")

# Everything changed: there is no evidence for any offset.
scrambled = [mk(i + 1, f"Other {i:02d}", 99, 999_999) for i in range(10)]
check("a completely different table refuses",
      trade.measure_shift(win(FULL, 0), scrambled) is None,
      f"got {trade.measure_shift(win(FULL, 0), scrambled)!r}")

# Identical reads report 0 -- "nothing moved" -- even when the rows are
# interchangeable. The protection against acting on that is no longer here: a 0
# arriving before the measured bottom is rejected by the sweep as a stuck view.
same = [mk(i + 1, "Force Core(High)", 250, 215_000) for i in range(10)]
check("an all-identical table reports 0, not a guess",
      trade.measure_shift(same, same[:]) == 0,
      f"got {trade.measure_shift(same, same[:])!r}")

# Majority-changed: below the ratio, so not a candidate at all.
heavy = win(FULL, 7)
for k in range(6):
    heavy[k] = mk(heavy[k].index, f"Replaced {k}", 5, 50_000)
check("a mostly-changed overlap refuses",
      trade.measure_shift(win(FULL, 0), heavy) is None,
      f"got {trade.measure_shift(win(FULL, 0), heavy)!r}")

check("an empty reading refuses",
      trade.measure_shift([], win(FULL, 3)) is None
      and trade.measure_shift(win(FULL, 0), []) is None, "")


# ===========================================================================
section("the margin: a close second place must refuse")

# A repeating shop: every row identical to the one 5 below it. Offsets 0 and 5
# then score alike, and taking "the best" would cancel a listing five rows from
# the one intended. Only the margin refuses here.
period = [mk(i, f"Item {(i - 1) % 5:02d}", 10 + ((i - 1) % 5),
             100_000 + ((i - 1) % 5)) for i in range(1, 31)]
# The two screens are byte-identical, so this reports 0 even though the view
# really moved 5. It is the one reading the pixels support, and it is SAFE
# rather than correct: the sweep sees 0 before the measured bottom, calls the
# view stuck, and fails instead of mislabelling five listings.
got = trade.measure_shift(win(period, 0), win(period, 5))
check("a shop repeating every 5 rows reports 0 rather than guessing 5",
      got == 0,
      f"got {got!r} -- guessing 5 would cancel a stack five rows from the one "
      f"intended; 0 is refused upstream as a stuck view")

check(f"the margin is at least 2 rows", trade.SCROLL_MATCH_MARGIN >= 2,
      f"SCROLL_MATCH_MARGIN={trade.SCROLL_MATCH_MARGIN} -- a margin of 1 would "
      f"accept a single-row lead, which OCR noise alone can produce")
check("the ratio demands a real majority",
      trade.SCROLL_MATCH_RATIO >= 0.6,
      f"SCROLL_MATCH_RATIO={trade.SCROLL_MATCH_RATIO}")


# ===========================================================================
section("an exact answer always beats a scored one")

# Where the exact test succeeds, the fallback must never get a vote: it is
# strictly weaker evidence.
exact = trade.measure_shift(win(FULL, 0), win(FULL, 4))
check("exact wins outright", exact == 4, f"got {exact!r}")

# And where the exact test finds SEVERAL, that is real ambiguity -- the
# fallback must not be used to break the tie.
twins = [mk(i, "Force Core(High)", 250, 215_000) for i in range(1, 11)]
check("identical twins report 0 rather than falling through to scoring",
      trade.measure_shift(twins, twins[:]) == 0,
      f"got {trade.measure_shift(twins, twins[:])!r}")
# Real ambiguity -- different content, several offsets fitting -- must still
# refuse. That guarantee has not moved.
_dead = [mk(1, "Siena's Unbinding Stone", 1, 75_000_000)] +         [mk(i, "(empty)", None, None, "register") for i in range(2, 11)]
_next = [mk(i, "(empty)", None, None, "register") for i in range(1, 10)] +         [mk(10, "Force Core(Highest)", 0, 200_000, "receive")]
check("genuine ambiguity still refuses",
      trade.measure_shift(_dead, _next) is None,
      f"got {trade.measure_shift(_dead, _next)!r}")




# ===========================================================================
section("the actual cause: an overlap of nothing but empty slots")

# Reproduced live on 2026-08-06. The top screen was six listings then four
# empty slots; a 7-row step left three empty rows overlapping, and BOTH shift 6
# and shift 7 fitted them perfectly. Two exact answers is not tolerance-of-
# drift, it is ambiguity, and no amount of fuzzy scoring can resolve it -- the
# rows genuinely carry no information. The step has to change instead.
TOP = [mk(1, "Force Gem Package (x400)", 2, 187_000_000),
       mk(2, "Yekaterina VIP Membership", 2, 108_999_999),
       mk(3, "Force Core(High)", 2, 210_000),
       mk(4, "Force Core (Ultimate)", 1, 381_614),
       mk(5, "Force Core(Medium)", 1, 100_000),
       mk(6, "Siena's Unbinding Stone", 1, 75_000_000)] + \
      [mk(i, "(empty)", None, None, "register") for i in range(7, 11)]

step = trade.informative_step(TOP, trade.SCROLL_STEP)
check("a screen whose tail is empty steps shorter", step == 4,
      f"got {step} -- the overlap must reach back to rows 5 and 6, which name "
      f"themselves; rows 7-10 are interchangeable")
check("...and the overlap it leaves is nameable",
      sum(1 for r in TOP[step:] if r.name != "(empty)")
      >= trade.SCROLL_MATCH_MIN_LIVE,
      f"step {step} leaves {[r.name for r in TOP[step:]]}")

full_screen = [mk(i, f"Item {i:02d}", 10 + i, 100_000 + i) for i in range(1, 11)]
check("a full screen keeps the cheap 7-row step",
      trade.informative_step(full_screen, trade.SCROLL_STEP) == trade.SCROLL_STEP,
      f"got {trade.informative_step(full_screen, trade.SCROLL_STEP)} -- "
      f"shrinking the step costs reads, so a healthy shop must not pay for it")

all_empty = [mk(i, "(empty)", None, None, "register") for i in range(1, 11)]
check("a wholly empty screen steps minimally rather than leaping",
      trade.informative_step(all_empty, trade.SCROLL_STEP) == 1,
      f"got {trade.informative_step(all_empty, trade.SCROLL_STEP)}")

check("never returns a step that leaves too little overlap",
      all(10 - trade.informative_step(s, trade.SCROLL_STEP)
          >= trade.MIN_SCROLL_OVERLAP
          for s in (TOP, full_screen, all_empty)), "")

# The pair that actually defeated it, asserted directly.
after7 = [mk(i, "(empty)", None, None, "register") for i in range(1, 8)] + \
         [mk(8, "Force Core(Highest)", 0, 200_000, "receive"),
          mk(9, "Force Core(High)", 250, 215_000),
          mk(10, "(empty)", None, None, "register")]
check("the recorded 7-row step is ambiguous and refuses",
      trade.measure_shift(TOP, after7) is None,
      f"got {trade.measure_shift(TOP, after7)!r} -- shifts 6 and 7 both fit; "
      f"answering either one mislabels every row below it")




# ===========================================================================
section("every scroll site uses the step rule, not just the sweep")

# Fixing enumerate_listings alone was not enough: bring_into_view scrolled with
# a fixed SCROLL_STEP and failed identically on the first cycle of the 11:50
# run of 2026-08-06 -- row 15, "could not tell how far the view moved". A rule
# that only some callers follow is not a rule.
import inspect

for fn in (trade.bring_into_view, trade._enumerate_at_step):
    src = inspect.getsource(fn)
    check(f"{fn.__name__} chooses its step from the screen",
          "informative_step(" in src,
          f"{fn.__name__} scrolls with a fixed step, so a screen whose tail is "
          f"empty will refuse and lose the cycle")
    check(f"{fn.__name__} does not scroll a bare SCROLL_STEP",
          "scroll_chunk(SCROLL_STEP" not in src,
          f"{fn.__name__} still passes the constant straight through")

check("bring_into_view still terminates",
      "while steps <" in inspect.getsource(trade.bring_into_view),
      "a smaller step needs more iterations, so the bound has to grow with it")




# ===========================================================================
section("inside a run of empty slots, the wheel decides -- not the content")

# The state that stopped the shop on 2026-08-06. Mid-sweep, every offset fitted:
#     exact fits: [(7,3), (6,4), (5,5), (4,6), (3,7), (2,8), (1,9)]
# No step size helps, because the rows carry no information at any scale. The
# wheel does: a notch moves one row. So when the overlap is made up ENTIRELY of
# empty slots, take the shift that was asked for -- every row skipped is empty
# by construction, so no listing can be mislabelled.
DEAD = [mk(1, "Siena's Unbinding Stone", 1, 75_000_000)] + \
       [mk(i, "(empty)", None, None, "register") for i in range(2, 11)]
NEXT = [mk(i, "(empty)", None, None, "register") for i in range(1, 10)] + \
       [mk(10, "Force Core(Highest)", 0, 200_000, "receive")]

check("without the wheel's opinion it refuses",
      trade.measure_shift(DEAD, NEXT) is None,
      f"got {trade.measure_shift(DEAD, NEXT)!r} -- several offsets fit equally")
check("with it, the requested shift is taken",
      trade.measure_shift(DEAD, NEXT, expected=1) == 1,
      f"got {trade.measure_shift(DEAD, NEXT, expected=1)!r}")
check("a different requested shift is honoured too",
      trade.measure_shift(DEAD, NEXT, expected=3) == 3,
      f"got {trade.measure_shift(DEAD, NEXT, expected=3)!r}")

# It must not rescue an offset that does not fit at all.
check("a requested shift that fits nothing is still refused",
      trade.measure_shift(DEAD, NEXT, expected=9) is None,
      f"got {trade.measure_shift(DEAD, NEXT, expected=9)!r}")

# The moment a nameable row is in the overlap, content decides again.
NAMED = [mk(1, "Siena's Unbinding Stone", 1, 75_000_000),
         mk(2, "Force Core(High)", 250, 215_000)] + \
        [mk(i, "(empty)", None, None, "register") for i in range(3, 11)]
check("a nameable row in the overlap is not overridden by the wheel",
      trade.measure_shift(NAMED, NAMED[:], expected=5) == 0,
      f"got {trade.measure_shift(NAMED, NAMED[:], expected=5)!r} -- the "
      f"content says the view did not move, and content outranks the wheel")

# The bottom clamp: the wheel is asked to move but nothing does. This must
# report 0, or the sweep believes it is descending and never terminates.
# An all-empty screen reads identically whether the view moved or not, so
# content has no opinion and the wheel is the better witness. Answering 0 here
# wedges the sweep inside the gap -- the live shop did exactly that at 14:5x
# with a fifteen-row run of empty slots.
ALL_EMPTY = [mk(i, "(empty)", None, None, "register") for i in range(1, 11)]
check("identical all-empty reads follow the wheel, not the pixels",
      trade.measure_shift(ALL_EMPTY, ALL_EMPTY[:], expected=7) == 7,
      f"got {trade.measure_shift(ALL_EMPTY, ALL_EMPTY[:], expected=7)!r}")
check("...and report 0 when no shift was requested",
      trade.measure_shift(ALL_EMPTY, ALL_EMPTY[:]) == 0,
      f"got {trade.measure_shift(ALL_EMPTY, ALL_EMPTY[:])!r}")
check("a screen with ANY nameable row still reports 0",
      trade.measure_shift(TOP, TOP[:], expected=7) == 0,
      f"got {trade.measure_shift(TOP, TOP[:], expected=7)!r} -- a real move "
      f"would have changed it, so advancing the index would mislabel rows")
check("...and an unmoved view of distinct listings reports 0 too",
      trade.measure_shift(FULL[:10], FULL[:10], expected=7) == 0,
      "distinct rows pin d=0 on their own, with no clamp rule needed")
# Two identical reads report 0 -- "nothing moved" -- whatever the rows hold,
# and the wheel's request must NOT override that. Claiming movement the pixels
# deny would advance the absolute index past rows that never scrolled by.
ident = [mk(i, "Force Core(High)", 250, 215_000) for i in range(1, 11)]
check("identical reads report 0, not the requested shift",
      trade.measure_shift(ident, ident[:], expected=7) == 0,
      f"got {trade.measure_shift(ident, ident[:], expected=7)!r}")
# 0 means "nothing moved", NEVER "the bottom". The sweep decides the bottom by
# reaching the screen it measured, so a 0 arriving early is a stuck view and is
# reported as a failure -- not as a finished shop.
import inspect as _ins
_src = _ins.getsource(trade._enumerate_at_step)
check("the sweep measures the bottom instead of inferring it",
      "tail_keys" in _src and "scroll_to_end(up=False" in _src,
      "without a measured bottom, a run of empty slots ends the sweep early "
      "and the rest of the shop is invisible")
check("...and a shift of 0 never ends the sweep by itself",
      "if shift == 0" not in _src,
      "inside a run of empty slots a 0 arrives in the MIDDLE of the shop; "
      "ending there is the silent truncation again")
check("...the sweep ends on the tail, guarded for a featureless bottom",
      "at_tail and (len(set(tail_keys)) >= 2 or barren" in _src,
      "an all-empty bottom screen matches every all-empty screen above it, so "
      "reaching it is necessary but not sufficient")

# Both scroll sites must pass the wheel's request through.
import inspect as _i
for fn in (trade.scroll_chunk, trade.scroll_one):
    check(f"{fn.__name__} tells measure_shift what it asked for",
          "expected=" in _i.getsource(fn),
          f"{fn.__name__} drops the request, so the rule above never fires")




# ===========================================================================
section("the wheel never reaches the camera")

# With the Trade window shut the wheel is a CAMERA ZOOM, and scroll_to_end
# sends forty notches. On 2026-08-06 that zoomed the view so far in that the
# NPC left the screen: the next two cycles could not find her, the breaker
# stopped the run, and the camera had to be wound back by hand. One row scrolled
# a moment after the window closed was enough.
#
# Damage the script cannot see or undo, from an input it sends routinely -- so
# the guard is at the wheel, not at the callers. A rule only some callers follow
# is how the earlier step fix failed: it covered enumerate_listings and missed
# bring_into_view.
from harness import Harness, empty_panel, make_row, run

def rows10():
    return [make_row(i, f"Item {i:02d}", price=100_000 + i, qty=10 + i)
            for i in range(1, 11)]


for name, call in (
    ("scroll_to_end", lambda: trade.scroll_to_end(up=True, verbose=False)),
    ("scroll_one",    lambda: trade.scroll_one(True, rows10(), verbose=False)),
    ("scroll_chunk",  lambda: trade.scroll_chunk(7, rows10(), verbose=False)),
):
    h = Harness(rows=rows10(), panel=empty_panel(), verbose=False)
    with h:
        h.trade_open = False                 # the window is shut
        run(call)
        wheels = [c for c in h.calls if c[0] == "scroll_wheel"]
        check(f"{name} sends no wheel input with the window shut",
              wheels == [],
              f"{len(wheels)} wheel event(s) -- these would have zoomed the "
              f"camera and put the NPC off screen")

    h = Harness(rows=rows10(), panel=empty_panel(), verbose=False)
    with h:
        h.trade_open = True                  # the window is open
        run(call)
        wheels = [c for c in h.calls if c[0] == "scroll_wheel"]
        check(f"{name} still scrolls when the window IS open", len(wheels) >= 1,
              f"{len(wheels)} wheel event(s) -- the guard must not block "
              f"ordinary scrolling")

h = Harness(rows=rows10(), panel=empty_panel(), verbose=False)
with h:
    h.trade_open = False
    ok, exc = run(trade.scroll_to_end, True, 8.0, False)
    check("a refused scroll reports failure rather than pretending",
          ok is None, f"got {ok!r}")

h = Harness(rows=rows10(), panel=empty_panel(), verbose=False)
with h:
    h.trade_open = False
    run(trade.scroll_chunk, 7, rows10(), 8.0, False)
    check("the refusal is recorded", "scroll.refused_window_shut" in h.labels(),
          f"{h.labels()} -- a run that dies from this leaves no other trace")




# ===========================================================================
section("only offsets the wheel could have produced are candidates")

# The failure that killed the 15:19 run. A downward scroll moves the view
# between 0 and N rows -- never up, never past N. Searching the whole range
# invented candidates that made a perfectly determined shift look ambiguous.
# Recorded live for a view that had moved exactly 3, with SEVEN rows agreeing:
#
#     exact fits: [(3, 7), (-6, 4), (-7, 3)]
#
# Minus six and minus seven are nonsense -- the wheel was asked to go down.
# They fit only the three or four mostly-empty rows at the screen edge, and
# their presence alone was enough to refuse and lose the cycle. scroll_chunk
# already bounded the result (`0 <= shift <= notches`), but only after
# measure_shift had thrown the answer away.
E = lambda i: mk(i, "(empty)", None, None, "register")

MOVED_3_BEFORE = [E(1), E(2), E(3), E(4), mk(5, "Force Core(High)", 247, 215_000),
                  E(6), mk(7, "Force Core(High)", 0, 215_000, "receive"), E(8),
                  mk(9, "Force Core(High)", 250, 220_000), E(10)]
MOVED_3_AFTER = [E(1), mk(2, "Force Core(High)", 247, 215_000), E(3),
                 mk(4, "Force Core(High)", 0, 215_000, "receive"), E(5),
                 mk(6, "Force Core(High)", 250, 220_000), E(7), E(8), E(9), E(10)]
check("the recorded 3-row move is measured, not refused",
      trade.measure_shift(MOVED_3_BEFORE, MOVED_3_AFTER, expected=3) == 3,
      f"got {trade.measure_shift(MOVED_3_BEFORE, MOVED_3_AFTER, expected=3)!r}")

MOVED_7_BEFORE = [E(i) for i in range(1, 8)] + [
    mk(8, "Force Core(High)", 247, 215_000), E(9),
    mk(10, "Force Core(High)", 0, 215_000, "receive")]
MOVED_7_AFTER = [mk(1, "Force Core(High)", 247, 215_000), E(2),
                 mk(3, "Force Core(High)", 0, 215_000, "receive"), E(4),
                 mk(5, "Force Core(High)", 250, 220_000)] + [E(i) for i in range(6, 11)]
check("the recorded 7-row move is measured, not refused",
      trade.measure_shift(MOVED_7_BEFORE, MOVED_7_AFTER, expected=7) == 7,
      f"got {trade.measure_shift(MOVED_7_BEFORE, MOVED_7_AFTER, expected=7)!r}")

check("a shift beyond what was asked for is never returned",
      all(trade.measure_shift(win(FULL, 0), win(FULL, off), expected=n) in (None, off)
          for off, n in ((7, 7), (3, 3), (5, 5))),
      "measure_shift must not answer with a move the wheel could not make")
check("negative shifts are never candidates when a direction is known",
      trade.measure_shift(win(FULL, 3), win(FULL, 0), expected=7) is None,
      f"got {trade.measure_shift(win(FULL, 3), win(FULL, 0), expected=7)!r} -- "
      f"that is the view moving UP, which a downward scroll cannot do")
check("with no expectation the full range is still searched",
      trade.measure_shift(win(FULL, 3), win(FULL, 0)) == -3,
      f"got {trade.measure_shift(win(FULL, 3), win(FULL, 0))!r}")




# ===========================================================================
section("a lying window detector must not let the wheel through")

# The live failure of 2026-08-07. trade_window_open() is a text search inside
# TRADE_WINDOW_SEARCH, and the 3D world can supply those glyphs. close_shop
# pressed Escape, asked it, was told the window was still open, and warned
# "the Trade window would not close with Escape" -- when it had closed. Two
# reads later the scroll guard asked the same detector, believed it, and forty
# notches zoomed the camera until the NPC left the screen. The next two cycles
# could not find her and the breaker stopped the run.
#
# panel_covers_trade_area() compares two frames a moment apart: the world
# animates, an opaque panel does not. It cannot be fooled by stray glyphs, and
# open_trade_window already requires BOTH before claiming the shop is open.
from harness import Harness as _H, empty_panel as _ep, make_row as _mk, run as _run

def _rows10():
    return [_mk(i, f"Item {i:02d}", price=100_000 + i, qty=10 + i)
            for i in range(1, 11)]


for name, call in (
    ("scroll_to_end", lambda: trade.scroll_to_end(up=True, verbose=False)),
    ("scroll_one",    lambda: trade.scroll_one(True, _rows10(), verbose=False)),
    ("scroll_chunk",  lambda: trade.scroll_chunk(7, _rows10(), verbose=False)),
):
    h = _H(rows=_rows10(), panel=_ep(), verbose=False)
    with h:
        h.trade_open = True                       # the OCR check LIES
        h.patch("panel_covers_trade_area", lambda *a, **k: False)  # world animates
        _run(call)
        wheels = [c for c in h.calls if c[0] == "scroll_wheel"]
        check(f"{name}: a false 'window open' alone does not open the gate",
              wheels == [],
              f"{len(wheels)} wheel event(s) -- this is the camera-zoom "
              f"failure exactly")

    h = _H(rows=_rows10(), panel=_ep(), verbose=False)
    with h:
        h.trade_open = True
        h.patch("panel_covers_trade_area", lambda *a, **k: True)
        _run(call)
        wheels = [c for c in h.calls if c[0] == "scroll_wheel"]
        check(f"{name}: both signals agreeing still scrolls normally",
              len(wheels) >= 1, f"{len(wheels)} wheel event(s)")

h = _H(rows=_rows10(), panel=_ep(), verbose=False)
with h:
    h.trade_open = True
    h.patch("panel_covers_trade_area", lambda *a, **k: False)
    _run(trade.scroll_chunk, 7, _rows10(), 8.0, False)
    check("the refusal is recorded even when the OCR check was fooled",
          "scroll.refused_window_shut" in h.labels(), f"{h.labels()}")

import inspect as _i2
check("table_scrollable requires the motion probe, not just the text search",
      "panel_covers_trade_area()" in _i2.getsource(trade.table_scrollable),
      "one detector that the game world can spoof is not a guard")


raise SystemExit(summary())
