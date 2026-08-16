"""Harness smoke test: the happy path must work before failures mean anything."""
import harness as H
from harness import Harness, check, section, summary, run
import trade

section("smoke: happy-path cancel_item")
h = Harness()
with h:
    ok, exc = run(trade.cancel_item, 1, verbose=True)
check("happy cancel returns True", ok is True, f"got {ok!r} exc={exc!r}")
check("happy cancel raised nothing", exc is None, repr(exc))
check("happy cancel recorded committed", "cancel.committed" in h.labels(),
      str(h.labels()))
check("happy cancel removed the row", len(h.rows) == 1, str(h.rows))
print("labels:", h.labels())
print("clicks:", h.clicks())
print("frame used:", Harness._frame_name)

section("smoke: happy-path register_item")
h = Harness(panel=H.empty_panel())
with h:
    report = {}
    ok, exc = run(trade.register_item, 1, 1, expect_item="Upgrade Core (Ultimate)",
                  expect_qty=100, report=report)
check("happy register returns True", ok is True, f"got {ok!r} exc={exc!r} "
      f"out={h.out()[-600:]}")
check("happy register raised nothing", exc is None, repr(exc))
print("report:", report)
print("labels:", h.labels())

raise SystemExit(summary())
