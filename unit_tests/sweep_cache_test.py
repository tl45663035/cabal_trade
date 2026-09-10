import inspect
import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


MEASURED_CYCLE = 1000.0
check(m.CORE_STOCK_TTL > MEASURED_CYCLE,
      f"the sweep cache must outlive a cycle or it can never be used: TTL "
      f"{m.CORE_STOCK_TTL:g}s vs a measured {MEASURED_CYCLE:g}s cycle")
check(m.CORE_STOCK_TTL >= 2 * MEASURED_CYCLE,
      f"and with margin -- a cycle that collects more rows runs longer, and a "
      f"TTL that only just clears today's cycle is one slow cycle from being "
      f"the same bug again. got {m.CORE_STOCK_TTL:g}s")


m.forget_unlisted()
check(m.cached_unlisted([1, 5]) is None,
      "with nothing remembered a sweep is required, not assumed")

m.note_unlisted([1, 5])
check(m.cached_unlisted([1, 5]) == [1, 5],
      f"a remembered verdict is reused, got {m.cached_unlisted([1, 5])}")
check(m.cached_unlisted([1]) == [1], "a subset is answerable")
check(m.cached_unlisted([1, 7]) == [1],
      f"a Core the sweep saw and found listed further down must be excluded "
      f"from the restock, not treated as unknown; got "
      f"{m.cached_unlisted([1, 7])}")

m.forget_unlisted()
check(m.cached_unlisted([1, 5]) is None, "and forgetting really forgets")

m.note_unlisted([1])
m._UNLISTED_CACHE["at"] -= m.CORE_STOCK_TTL + 1
check(m.cached_unlisted([1]) is None,
      "past the TTL the answer is re-taken -- the operator can list something "
      "by hand and no event would tell this script about it")
m.forget_unlisted()


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


pass_src = inspect.getsource(m.restock_pass)
import re as _re
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


check(m.BUY_NO_SWEEP is False,
      "skipping the sweep must be OFF by default: it trades a money risk for a "
      "time saving, and that is the operator's call to make explicitly")
check("BUY_NO_SWEEP" in pass_src,
      "restock_pass must honour the flag")
check("--buy-no-sweep" in inspect.getsource(m.main),
      "and it must be reachable from the command line")

after_flag = pass_src.split("if BUY_NO_SWEEP:")[1].split("return")[0]
check("shop_listing_pairs" not in after_flag,
      "the no-sweep branch must not read the whole shop anyway")
check("restock_sold_out_slots" in after_flag,
      "but it must still restock the Cores it decided were sold out, or the "
      "flag quietly turns --buy off instead of speeding it up")



core_src = inspect.getsource(m.restock_core)
check('result["rows_grown"] = result["rows_listed"]' in core_src,
      "shop growth must be taken from the registration count, not measured")
check("after_rows" not in core_src,
      "and the post-listing enumerate must be gone entirely")
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

check(callable(getattr(m, "cached_rows_used", None)),
      "the row count must be cacheable, or avoiding the sweep saves nothing")
pass_src2 = inspect.getsource(m.restock_pass)
check(pass_src2.count("cached_rows_used()") >= 2,
      f"both fast paths must consult it, got "
      f"{pass_src2.count('cached_rows_used()')}")

m.forget_unlisted()
check(m.cached_rows_used() is None,
      "with nothing remembered there is no count to reuse")

_saved_added = m.BUY_ADDED_ROWS
try:
    m.forget_rows_used()
    m.BUY_ADDED_ROWS = 7
    m.note_rows_used(25)
    check(m.cached_rows_used() == 25,
          f"right after a count it is that count, not inflated by rows added "
          f"before it; got {m.cached_rows_used()}")
    m.note_rows_added(2)
    check(m.cached_rows_used() == 27,
          f"and grows by what has been listed SINCE, got {m.cached_rows_used()}")

    m.note_unlisted([1])
    m.forget_unlisted()
    check(m.cached_rows_used() == 27,
          f"dropping the unlisted verdict must not drop the row count; got "
          f"{m.cached_rows_used()}")
    m.forget_rows_used()
    check(m.cached_rows_used() is None, "and it can be dropped on its own")

    m.note_rows_used(25)
    import time as _t
    m._ROWS_USED_CACHE["at"] -= 10 * m.CORE_STOCK_TTL
    check(m.cached_rows_used() == 25,
          f"the count must NOT expire on age alone; got {m.cached_rows_used()}")

    for fn, why in ((m.chaos_pass, "it lists a brand-new bundle"),
                    (m._restock_each, "it lists converted Cores"),
                    (m.recover_strand if hasattr(m, "recover_strand") else
                     m._restock_each, "strand recovery lists an orphan")):
        check("note_rows_added(" in inspect.getsource(fn),
              f"{fn.__name__} must report the row it added -- {why}")

    receive_src = inspect.getsource(m._relist_cycle)
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

check("note_rows_used(" in inspect.getsource(m.shop_rows_used),
      "shop_rows_used must remember what its walk cost")
check("note_rows_used(" in pass_src2,
      "and the sweep must feed it too -- it has already read every row")


pass_src3 = inspect.getsource(m.restock_pass)
check("scope" in inspect.signature(m.restock_pass).parameters,
      "restock_pass must take the relist scope")

check(pass_src3.count("r.index in set(scope)") >= 1,
      "the visible rows must be filtered to the scope by row index")
check("_dc.replace(r, index=i)" in pass_src3,
      "a range past one screen must be read with ABSOLUTE row numbers, or the "
      "scope filter compares screen positions against absolute rows")
_pass_code = chr(10).join(l for l in pass_src3.splitlines()
                          if not l.strip().startswith("#"))
check("scope = None" not in _pass_code,
      "restock_pass must NOT discard the scope past row 10: that made "
      "restock_core's whole scoped block -- including the chaos row cap -- "
      "unreachable, and let the restock list outside the batch")

after_scope = pass_src3.split('record("restock.scoped"')[-1]     .split("if BUY_NO_SWEEP:")[0]
check("cached_rows_used()" in after_scope,
      "the scoped path must use the remembered count, never re-derive it")

check("shop_listing_pairs" not in after_scope,
      "a scoped restock must not sweep: the whole-shop count cancels out of "
      "its own capacity gate")
check("no sweep" in after_scope,
      "and it must say so, or a reader cannot tell a fast path from a "
      "missing one")
check("restock_sold_out_slots" in after_scope,
      "it must still restock what it decided was missing, or scoping quietly "
      "turns --buy off instead of speeding it up")

rows_src2 = inspect.getsource(m.relist_rows)
check("scope=None if all_rows else list(rows)" in rows_src2,
      "an all-rows batch must pass no scope, keeping the sweep -- otherwise "
      "the one case where absence is real loses its check")

check("bought" in pass_src3 and "again" in pass_src3,
      "the scoped path must warn that a Core listed further down gets bought "
      "again -- the operator accepted that cost and should see when it applies")


print(f"sweep_cache_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
