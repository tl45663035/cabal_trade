import os
import sys
import tempfile
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_SCRATCH = _Path(tempfile.mkdtemp(prefix="cabal_idle_test_"))
os.environ["CABAL_SALES_DB"] = str(_SCRATCH / "scratch.db")

import os as _os_guard
import sys as _sys_guard
_sys_guard.path.insert(0, _os_guard.path.dirname(
    _os_guard.path.abspath(__file__)))
import _no_input_guard

import trade as m

CHECKS = FAILED = 0


def check(cond, what):
    global CHECKS, FAILED
    CHECKS += 1
    if not cond:
        FAILED += 1
        print(f"  FAIL  {what}")


def row(index, action, name="Chaos Core Set X 200"):
    top = 200 + index * m.REF_ROW_PITCH
    return m.Row(index=index,
                 name=name if action != "register" else "",
                 change=(1126, top + 20),
                 top=top,
                 bottom=top + m.REF_ROW_PITCH,
                 action=action,
                 price=100 if action != "register" else None,
                 qty=1 if action != "register" else 0,
                 status="On Sale" if action == "change" else "")


def drive(first, again, rows=(1, 2, 3, 4), chaos=False, holding_off=False):
    reads = [list(first), list(again)]

    def fake_await(*a, **k):
        return reads.pop(0) if reads else list(again)

    saved = {
        "await_rows": m.await_rows,
        "read_rows": m.read_rows,
        "ensure_work_tab_empty": m.ensure_work_tab_empty,
        "ensure_shop_ready": m.ensure_shop_ready,
        "chaos_pass": m.chaos_pass,
        "restock_pass": m.restock_pass,
        "leave_shop": m.leave_shop,
        "CHAOS_ENABLED": m.CHAOS_ENABLED,
        "BUY_ENABLED": m.BUY_ENABLED,
        "CHAOS_HELD_OFF_ON_MARGIN": m.CHAOS_HELD_OFF_ON_MARGIN,
        "CHAOS_HELD_OFF_MARGIN": m.CHAOS_HELD_OFF_MARGIN,
        "CHAOS_HELD_OFF": m.CHAOS_HELD_OFF,
        "park_cursor": m.park_cursor,
        "leave_for_restock": getattr(m, "leave_for_restock", None),
        "note_range_view": getattr(m, "note_range_view", None),
    }
    m.park_cursor = lambda *a, **k: None
    if hasattr(m, "leave_for_restock"):
        m.leave_for_restock = lambda *a, **k: True
    if hasattr(m, "note_range_view"):
        m.note_range_view = lambda *a, **k: None
    m.CHAOS_HELD_OFF_ON_MARGIN = holding_off
    m.CHAOS_HELD_OFF_MARGIN = 3_000 if holding_off else None
    m.CHAOS_HELD_OFF = holding_off
    m.await_rows = fake_await
    m.read_rows = fake_await
    m.ensure_work_tab_empty = lambda *a, **k: True
    m.ensure_shop_ready = lambda *a, **k: True
    m.chaos_pass = lambda *a, **k: True
    m.restock_pass = lambda *a, **k: True
    m.leave_shop = lambda *a, **k: None
    m.CHAOS_ENABLED = chaos
    m.BUY_ENABLED = False
    try:
        out = m.relist_rows(list(rows), verbose=False)
        return ("return", out)
    except Exception as exc:
        return ("raise", type(exc).__name__)
    finally:
        for k, v in saved.items():
            if v is not None or hasattr(m, k):
                setattr(m, k, v)


print("idle cycle / sold-out scope")

empty_scope = [row(i, "register") for i in range(1, 5)]
live_outside = empty_scope + [row(i, "change") for i in range(5, 11)]

kind, what = drive(live_outside, live_outside)
check((kind, what) == ("raise", "ShopIdle"),
      f"rows 1-4 empty with 6 live rows outside the batch is ShopIdle, "
      f"got {kind} {what!r}")

check(not (kind == "return" and what is False),
      "a sold-out scope must NOT return False -- that is what tripped "
      "MAX_CONSECUTIVE_FAILURES three cycles later")

kind, what = drive(empty_scope,
                   [row(i, "change") for i in range(1, 5)])
check((kind, what) == ("return", False),
      f"a genuine mid-refresh still fails the cycle so it retries, "
      f"got {kind} {what!r}")

kind, what = drive([row(i, "register") for i in range(1, 11)],
                   [row(i, "register") for i in range(1, 11)])
check((kind, what) == ("raise", "ShopEmpty"),
      f"an empty table everywhere is still ShopEmpty, got {kind} {what!r}")

kind, what = drive(live_outside, live_outside, rows=(5, 6, 7, 8, 9, 10))
check(kind == "return",
      f"a batch scoped to live rows proceeds normally, got {kind} {what!r}")

all_empty = [row(i, "register") for i in range(1, 11)]

kind, what = drive(all_empty, all_empty, chaos=True, holding_off=True)
check((kind, what) == ("raise", "ShopIdle"),
      f"an empty shop with chaos holding off on margin is ShopIdle, not the "
      f"end of the run, got {kind} {what!r}")
check(what != "ShopEmpty",
      "ShopEmpty here would close the shop and stop the run at exactly the "
      "moment the strategy is meant to be sitting still")

kind, what = drive(all_empty, all_empty, chaos=True, holding_off=False)
check((kind, what) == ("raise", "ShopEmpty"),
      f"an empty shop with chaos NOT holding off is still ShopEmpty, "
      f"got {kind} {what!r}")

check(m.CHAOS_HELD_OFF is True,
      "CHAOS_HELD_OFF must default to True so an unexamined pass cannot read "
      "as a sold-out shop")

kind, what = drive(all_empty, all_empty, chaos=False, holding_off=True)
check((kind, what) == ("raise", "ShopEmpty"),
      f"a stale hold-off flag with --chaos off must not keep a sold-out run "
      f"alive, got {kind} {what!r}")

src_pass = (_ROOT / "trade.py").read_text(encoding="utf-8-sig")
body = src_pass[src_pass.index("def chaos_pass"):]
body = body[:body.index("        if margin <= CHAOS_MARGIN_FLOOR")]
check("CHAOS_HELD_OFF_ON_MARGIN = False" in body,
      "chaos_pass must clear the hold-off flag at the top of every pass")

src = (_ROOT / "trade.py").read_text(encoding="utf-8-sig")
handler = src[src.index("except ShopIdle"):src.index("except FatalAbort")]
check("succeeded += 1" not in handler,
      "an idle cycle must not count as a success -- that is how a run reports "
      "green cycles having relisted nothing")
check("failures += 1" not in handler and "consecutive += 1" not in handler,
      "an idle cycle must not count as a failure -- that is what ended the "
      "2026-08-12 run early")
check("consecutive = 0" not in handler,
      "an idle cycle must not RESET the breaker either: a real fault followed "
      "by a quiet market must still reach it")

print(f"idle cycle: {CHECKS} checks, {FAILED} failed")



print(f"idle cycle + priority: {CHECKS} checks, {FAILED} failed")
sys.exit(1 if FAILED else 0)
