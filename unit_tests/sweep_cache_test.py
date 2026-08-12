"""The shop sweep: how often it runs, whether it says so, and how to skip it.

Measured live on 2026-08-09: the sweep took 485 seconds out of a ~1,000 second
cycle -- a third of the run spent re-learning which Cores are listed. The code
documents it as "about twenty seconds", and it ran with verbose=False so the
log showed one line and then an eight-minute silence.

Three things were wrong, and this file pins all three:

  * the cache meant to stop it repeating expired before the next cycle could
    reach it, so the sweep ran EVERY cycle;
  * only one direction of the cache was invalidated by events;
  * the sweep was invisible, so none of the above could be seen from a log.
"""
import inspect
import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m  # noqa: E402

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


# -- THE CACHE MUST OUTLIVE A CYCLE ----------------------------------------
# This is the whole bug. The cache is only consulted at the start of the NEXT
# cycle, so a TTL shorter than a cycle means it has always expired by the time
# anything asks -- it can never once serve a read, and the expensive sweep runs
# every time. The old value was 600s against a cycle measured at over 1,000s.
#
# The same mistake is on record one layer down: enumerate_listings' docstring
# describes a 90s catalogue removed for exactly this, "the restock between the
# two passes always outlived its 90s expiry".
MEASURED_CYCLE = 1000.0
check(m.CORE_STOCK_TTL > MEASURED_CYCLE,
      f"the sweep cache must outlive a cycle or it can never be used: TTL "
      f"{m.CORE_STOCK_TTL:g}s vs a measured {MEASURED_CYCLE:g}s cycle")
check(m.CORE_STOCK_TTL >= 2 * MEASURED_CYCLE,
      f"and with margin -- a cycle that collects more rows runs longer, and a "
      f"TTL that only just clears today's cycle is one slow cycle from being "
      f"the same bug again. got {m.CORE_STOCK_TTL:g}s")


# -- it remembers, and it forgets on demand --------------------------------
m.forget_unlisted()
check(m.cached_unlisted([1, 5]) is None,
      "with nothing remembered a sweep is required, not assumed")

m.note_unlisted([1, 5])
check(m.cached_unlisted([1, 5]) == [1, 5],
      f"a remembered verdict is reused, got {m.cached_unlisted([1, 5])}")
check(m.cached_unlisted([1]) == [1], "a subset is answerable")
# NOT-IN-THE-LIST MEANS LISTED, not unknown. The sweep reads the whole shop and
# records every enabled Core it found no row for, so a Core absent from that
# record was seen and found listed further down. Answering [1] for [1, 7] is
# therefore "buy 1, leave 7 alone" -- which is the point of having swept.
check(m.cached_unlisted([1, 7]) == [1],
      f"a Core the sweep saw and found listed further down must be excluded "
      f"from the restock, not treated as unknown; got "
      f"{m.cached_unlisted([1, 7])}")

m.forget_unlisted()
check(m.cached_unlisted([1, 5]) is None, "and forgetting really forgets")

# Expiry still works, so a shop changed by hand is eventually re-read.
m.note_unlisted([1])
m._UNLISTED_CACHE["at"] -= m.CORE_STOCK_TTL + 1
check(m.cached_unlisted([1]) is None,
      "past the TTL the answer is re-taken -- the operator can list something "
      "by hand and no event would tell this script about it")
m.forget_unlisted()


# -- BOTH DIRECTIONS must be invalidated by events -------------------------
# The TTL is now a backstop, so the events carry the correctness. Listing a
# Core makes it listed; collecting a sold-out row makes it unlisted. Only the
# first was wired -- so when a Core's last listing sold, the cache went on
# saying "listed" and the restock skipped a genuinely empty shelf.
# _restock_each, not restock_sold_out_slots: the latter is the entry point and
# the per-Core loop that actually lists is underneath it. Pinning the wrapper
# would have asserted nothing while reading as though it covered the path.
restock_src = inspect.getsource(m._restock_each)
check("forget_unlisted()" in restock_src,
      "listing a Core must drop the remembered verdict -- it has just become "
      "listed, and the memory says otherwise")

relist_src = inspect.getsource(m._relist_cycle)
receive_branch = relist_src.split('target.action == "receive"')[-1][:1500]
check("forget_unlisted()" in receive_branch,
      "collecting a sold row must ALSO drop it: that is the event that makes a "
      "Core unlisted, and without it the restock skips a sold-out Core for the "
      "whole TTL -- which is now an hour")


# -- THE SWEEP MUST SAY WHAT IT IS DOING -----------------------------------
# 485 seconds passed with a single log line before it. A step that can cost a
# third of the cycle cannot be silent, or there is no way to tell scrolling
# from retrying from stuck.
pass_src = inspect.getsource(m.restock_pass)
# Asserted on the ARGUMENT of the real CALL, not on a one-line text shape.
#
# Two things broke the old form: the call now also passes stop_after so it
# wraps across lines, and the name appears in a COMMENT further up, so a naive
# split lands on prose instead of code. Find the invocations that actually
# pass arguments.
import re as _re
# The sweep is shop_listing_pairs now, not whole_shop_listings: restock_pass
# needs the ABSOLUTE row numbers alongside the rows, and whole_shop_listings
# drops them. Asserting on the old name meant _calls came back empty, the
# check below failed, and the split on a name no longer in the source raised
# IndexError -- aborting this file rather than reporting a failure.
_calls = [c for c in _re.findall(r"shop_listing_pairs\(([^)]*)\)", pass_src)
          if "timeout" in c]
check(_calls, "restock_pass must actually call shop_listing_pairs")
check(all("verbose=verbose" in c for c in _calls),
      "the sweep must inherit the caller's verbosity, not be hardcoded silent")
check("verbose=False)" not in pass_src.split("shop_listing_pairs")[1][:60],
      "and must not be pinned to verbose=False again")
check("the shop sweep took" in pass_src,
      "and it must report how long it took, so a regression in its cost is "
      "visible in the log rather than needing to be measured by hand")


# -- THE OPT-OUT ------------------------------------------------------------
check(m.BUY_NO_SWEEP is False,
      "skipping the sweep must be OFF by default: it trades a money risk for a "
      "time saving, and that is the operator's call to make explicitly")
check("BUY_NO_SWEEP" in pass_src,
      "restock_pass must honour the flag")
check("--buy-no-sweep" in inspect.getsource(m.main),
      "and it must be reachable from the command line")

# The branch must SKIP the sweep, not merely log about it -- otherwise the flag
# costs the money risk and saves nothing.
after_flag = pass_src.split("if BUY_NO_SWEEP:")[1].split("return")[0]
check("shop_listing_pairs" not in after_flag,
      "the no-sweep branch must not read the whole shop anyway")
check("restock_sold_out_slots" in after_flag,
      "but it must still restock the Cores it decided were sold out, or the "
      "flag quietly turns --buy off instead of speeding it up")


# -- THE TABLE WALKS THAT WERE PURE WASTE ----------------------------------
# An enumerate is FOUR traversals: down to measure the shop's extent, back up,
# down again in chunks, and up once more in a finally. Two calls were paying
# that price for a number already known.

# 1. How much the shop grew after a restock listed. This called
#    shop_rows_used() -- 213.6 SECONDS in one measured restock -- to learn a
#    figure identical to the number of registrations just made. The comment
#    that justified it claimed counting registrations "over-states it by
#    however many empty rows were waiting"; across every occurrence in the
#    logs, twenty of them, the two numbers agree exactly. They must: each
#    registration occupies one row, and the count is of OCCUPIED rows.
# restock_core, not _restock_each: the growth is measured where the listing
# happens, and _restock_each is only the loop around it.
core_src = inspect.getsource(m.restock_core)
check('result["rows_grown"] = result["rows_listed"]' in core_src,
      "shop growth must be taken from the registration count, not measured")
check("after_rows" not in core_src,
      "and the post-listing enumerate must be gone entirely")
# ONE occurrence is allowed and correct: the fallback for a caller that did not
# supply rows_used. Forbidding every call would forbid that too, and the sweep
# path legitimately needs a count from somewhere when it has none.
# Code lines only. The comment explaining the removal names the function, and
# counting that as a call would fail on its own explanation.
core_code = [l for l in core_src.splitlines()
             if "shop_rows_used(" in l and not l.strip().startswith("#")]
check(len(core_code) <= 1,
      f"only the rows_used fallback may enumerate; a second call is the "
      f"post-listing measurement coming back. got {core_code}")
tail_after_growth = core_src.split('result["rows_grown"]')[-1]
tail_code = [l for l in tail_after_growth.splitlines()
             if "shop_rows_used(" in l and not l.strip().startswith("#")]
check(not tail_code,
      f"and nothing after the growth is recorded may walk the table, got "
      f"{tail_code}")

# 2. Both fast paths then threw the saving away. The cache announced "Using
#    the last shop sweep rather than repeating it" and immediately called
#    shop_rows_used(), walking the table it had just avoided; --buy-no-sweep
#    said "without reading the rest of the shop" and read the rest of the shop.
check(callable(getattr(m, "cached_rows_used", None)),
      "the row count must be cacheable, or avoiding the sweep saves nothing")
pass_src2 = inspect.getsource(m.restock_pass)
check(pass_src2.count("cached_rows_used()") >= 2,
      f"both fast paths must consult it, got "
      f"{pass_src2.count('cached_rows_used()')}")

m.forget_unlisted()
check(m.cached_rows_used() is None,
      "with nothing remembered there is no count to reuse")

# The stamped delta, not the running total. BUY_ADDED_ROWS accumulates across
# the whole run, and the sweep's own count already covers everything listed
# before it -- so adding the total would inflate rows_used, and an inflated
# count makes the restock refuse to buy at all.
_saved_added = m.BUY_ADDED_ROWS
try:
    m.forget_rows_used()
    m.BUY_ADDED_ROWS = 7          # history from earlier in the run
    m.note_rows_used(25)
    check(m.cached_rows_used() == 25,
          f"right after a count it is that count, not inflated by rows added "
          f"before it; got {m.cached_rows_used()}")
    m.note_rows_added(2)
    check(m.cached_rows_used() == 27,
          f"and grows by what has been listed SINCE, got {m.cached_rows_used()}")

    # A count of its OWN, not a field on the unlisted verdict. A scoped
    # restock learns the row count without learning which Cores are unlisted
    # elsewhere -- that second claim is exactly what scoping gives up, so the
    # two must be able to expire and be dropped independently.
    m.note_unlisted([1])
    m.forget_unlisted()
    check(m.cached_rows_used() == 27,
          f"dropping the unlisted verdict must not drop the row count; got "
          f"{m.cached_rows_used()}")
    m.forget_rows_used()
    check(m.cached_rows_used() is None, "and it can be dropped on its own")

    # NO TIMER. The count only goes stale if the shop changes, and every way
    # this script changes it now reports: listing (the restock, the chaos pass
    # and the strand recovery all call note_rows_added), collecting
    # (forget_rows_used), and a relist, which cancels one row and registers one
    # and so nets to nothing. A row SELLING does not change it either -- the
    # row becomes `receive`, still occupied, still counted.
    #
    # A timer would only guard against listings made by hand, and could not do
    # that reliably anyway: an interval short enough to catch them promptly is
    # short enough to throw the count away constantly, which is the very bug
    # the cache was added to fix.
    m.note_rows_used(25)
    import time as _t
    m._ROWS_USED_CACHE["at"] -= 10 * m.CORE_STOCK_TTL
    check(m.cached_rows_used() == 25,
          f"the count must NOT expire on age alone; got {m.cached_rows_used()}")

    # Every path that creates a row must report it, or the count reads LOW and
    # the capacity gate believes there is room that is not there.
    # _restock_each for the restock: restock_core RETURNS rows_grown and the
    # loop around it reports the total. Pinning restock_core would assert
    # nothing while reading as though it covered the path.
    for fn, why in ((m.chaos_pass, "it lists a brand-new bundle"),
                    (m._restock_each, "it lists converted Cores"),
                    (m.recover_strand if hasattr(m, "recover_strand") else
                     m._restock_each, "strand recovery lists an orphan")):
        check("note_rows_added(" in inspect.getsource(fn),
              f"{fn.__name__} must report the row it added -- {why}")

    # Collecting a sold row frees one, so the count must MOVE -- but by the
    # known delta, not by being thrown away.
    #
    # Dropping it meant the next restock had to re-derive it, and on a deep
    # shop that is a full sweep: 93 SECONDS measured 2026-08-10, run twice in
    # one cycle for the same answer (30 listings both times) because a partial
    # sale in between discarded the count. The delta needs no reading at all --
    # a collect either empties the row or leaves a remainder that is relisted
    # into it -- so the count is decremented here and given back on the
    # remainder path.
    receive_src = inspect.getsource(m._relist_cycle)
    # A generous window. This was 2600 characters and broke when a comment was
    # added between the branch head and the call -- a source-slice assertion
    # that fails on prose is testing formatting, not behaviour.
    branch = receive_src.split('target.action == "receive"')[-1][:4200]
    check("note_rows_used(max(0, _known_rows - 1))" in branch,
          "collecting a row must ADJUST the remembered count, not discard it")
    check("forget_rows_used()" not in branch,
          "and must not fall back to discarding it -- that is the 93s sweep")
    check("note_rows_used(_known_rows + 1)" in receive_src,
          "a partial sale relists into the same row, so the decrement above "
          "must be given back -- erring DOWN lets the restock overfill")
finally:
    m.BUY_ADDED_ROWS = _saved_added
    m.forget_unlisted()
    m.forget_rows_used()

# The expensive walk must feed the cache, or the scoped restock pays for it
# again on the very next cycle -- which is the saving it exists to deliver.
check("note_rows_used(" in inspect.getsource(m.shop_rows_used),
      "shop_rows_used must remember what its walk cost")
check("note_rows_used(" in pass_src2,
      "and the sweep must feed it too -- it has already read every row")


# -- SCOPED TO THE ROWS BEING RELISTED --------------------------------------
# The operator's instruction: "if i relist 1-6, dont scroll entire table to
# determine if we are missing out some type of cores, only consider exact the
# specified number".
#
# A deliberate trade, made with the risk stated: a Core listed OUTSIDE the
# scope reads as sold out, so the restock buys more of something already on the
# shelf. What it buys is the sweep -- four traversals of a thirty-row table,
# measured at 112-485 seconds, the largest single step in a cycle.
pass_src3 = inspect.getsource(m.restock_pass)
check("scope" in inspect.signature(m.restock_pass).parameters,
      "restock_pass must take the relist scope")

# CHECKED OVER THE WHOLE FUNCTION, not the tail after `if scope:`.
#
# There are now TWO scoped paths: a range inside one screen filters the screen
# read, and a range past row 10 does a ranged walk and filters that. The second
# sits ABOVE the `if scope:` dispatch, so slicing on it hid the filter that
# does the work on every range the operator actually uses.
check(pass_src3.count("r.index in set(scope)") >= 1,
      "the visible rows must be filtered to the scope by row index")
check("_dc.replace(r, index=i)" in pass_src3,
      "a range past one screen must be read with ABSOLUTE row numbers, or the "
      "scope filter compares screen positions against absolute rows")
# COMMENTS STRIPPED. inspect.getsource returns them too, and the comment
# explaining why the scope must not be discarded contains the very literal
# this check forbids -- so it failed on correct code.
_pass_code = chr(10).join(l for l in pass_src3.splitlines()
                          if not l.strip().startswith("#"))
check("scope = None" not in _pass_code,
      "restock_pass must NOT discard the scope past row 10: that made "
      "restock_core's whole scoped block -- including the chaos row cap -- "
      "unreachable, and let the restock list outside the batch")

# THE REFINED RULE, after measuring it live.
#
# A scoped restock still needs a row count for the capacity gate, and with
# nothing remembered the only way to get one is to walk the table. The first
# version skipped the sweep and then called shop_rows_used() -- the SAME walk,
# yielding one integer instead of the whole shop. Measured 2026-08-09 that cost
# 232 seconds, worse than the 112s sweep it was avoiding.
#
# So: no walk at all when the count is remembered, and when it is not, take the
# sweep, because it answers both questions for the same single traversal.
# Sliced to the end of the scoped block, not to the first `return` -- the
# walk branch has an early return of its own, and cutting there would hide the
# restock call that comes after it.
# Bounded to the SCOPED block alone. restock_pass has three fast paths in a
# row -- scoped, --buy-no-sweep, and the cached verdict -- and each has its own
# "if rows_now is None". Slicing to the last one tested a different branch and
# reported the scoped path as broken when it was not.
after_scope = pass_src3.split('record("restock.scoped"')[-1]     .split("if BUY_NO_SWEEP:")[0]
check("cached_rows_used()" in after_scope,
      "the scoped path must use the remembered count, never re-derive it")

# THE SCOPED PATH NO LONGER WALKS AT ALL.
#
# It used to fall back to a full sweep when no count was remembered, because
# the capacity gate needed one. Once the RANGE became the ceiling that stopped
# being true: the gate is
#     used + need > used + free_inside
# so `used` cancels out, and what bounds a scoped restock is the free rows
# inside the range -- already known from the first screen.
#
# Walking rows 11-30 to answer a question about rows 1-10 cost 90s+, and up to
# 54.5s for a single step on a sparse shop. The branch is gone rather than
# skipped, so this pins its absence.
# The name the scoped branch must NOT contain is the one restock_pass actually
# calls now. Asserting the old name could not fail: restock_pass no longer
# mentions whole_shop_listings anywhere, so the scoped path could regress to a
# full sweep with this suite still green.
check("shop_listing_pairs" not in after_scope,
      "a scoped restock must not sweep: the whole-shop count cancels out of "
      "its own capacity gate")
check("no sweep" in after_scope,
      "and it must say so, or a reader cannot tell a fast path from a "
      "missing one")
check("restock_sold_out_slots" in after_scope,
      "it must still restock what it decided was missing, or scoping quietly "
      "turns --buy off instead of speeding it up")

# A batch over ALL rows has no scope to narrow to, so the sweep must survive:
# there, "not in these rows" really does mean absent from the shop.
rows_src2 = inspect.getsource(m.relist_rows)
check("scope=None if all_rows else list(rows)" in rows_src2,
      "an all-rows batch must pass no scope, keeping the sweep -- otherwise "
      "the one case where absence is real loses its check")

# And the risk must be SAID, every time, not buried in a comment. A silent
# over-buy is indistinguishable from a correct one in the log.
check("bought" in pass_src3 and "again" in pass_src3,
      "the scoped path must warn that a Core listed further down gets bought "
      "again -- the operator accepted that cost and should see when it applies")


print(f"sweep_cache_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
