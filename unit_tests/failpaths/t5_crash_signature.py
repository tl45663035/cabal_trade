"""Reproduce the observed crash signature.

Observed: the frame index stops mid-cycle between

    record("inventory.before_cancel")      (trade.py:4360, _relist_cycle)
    record("cancel.before_change")         (trade.py:4360 -> 3544, cancel_item)

with NO abort recorded, and the run had to be restarted by hand.

This drives the FULL stack -- run_loop -> run_sequence -> relist_rows ->
relist -> _relist_cycle -> cancel_item -- and arms one fault at a time on the
first stub called after record("inventory.before_cancel"), then reports the
resulting index tail and whether the loop survived.
"""
import harness as H
from harness import (Harness, check, note, section, summary, run, make_row,
                     empty_panel)
import trade

ITEM = "Upgrade Core (Ultimate)"

# Labels the loop itself writes. Their own comments in trade.py say they were
# added AFTER the outage this test reproduces ("THE entry that was missing",
# "which is what made a five-hour outage unattributable"), so the build that
# produced the observed index did not have them.
LOOP_LABELS = {"cycle.start", "cycle.end", "cycle.exception", "loop.stopped"}

HINT = trade.CURSOR_BLOCKED_HINT

CASES = [
    # (title, stub, exception, is it reachable on the real I/O layer?)
    ("park_cursor raises PermissionError", "park_cursor", PermissionError(HINT),
     "YES - move_mouse() returning False is the documented UIPI signal "
     "(trade.py:1591); this is the first input call after the label"),
    ("grab raises OSError", "grab", OSError("mss: screen capture failed"),
     "yes - mss/PIL raise OSError on a failed capture"),
    ("read_rows raises RuntimeError", "read_rows",
     RuntimeError("tesseract exited with 1"),
     "yes - pytesseract raises on a dead/renamed binary"),
    ("grab raises PermissionError", "grab", PermissionError(HINT),
     "no - capture failures surface as OSError, not PermissionError"),
    ("focus_game raises PermissionError", "focus_game", PermissionError(HINT),
     "NO - focus_game swallows PermissionError itself at trade.py:1560 and "
     "returns a bool, so this cannot happen"),
]


def build():
    h = Harness(rows=[make_row(1, ITEM, price=410_000, qty=100)],
                panel=empty_panel())
    h.register_name = ITEM
    return h


def drive(setup=None, minutes=0.2):
    h = build()
    if setup:
        setup(h)
    with h:
        ok, exc = run(trade.run_loop, ["relist-rows 1"], minutes, 0.0)
    return h, ok, exc


def tail(h, n=6):
    return [lab for lab in h.labels() if lab not in LOOP_LABELS][-n:]


def signature(h) -> bool:
    """Exactly the observed signature, judged on the pre-instrumentation build."""
    labels = [lab for lab in h.labels() if lab not in LOOP_LABELS]
    return (bool(labels) and labels[-1] == "inventory.before_cancel"
            and "cancel.aborted" not in labels
            and "cancel.before_change" not in labels)


section(f"build under test: {H.VERSION}")

# ---------------------------------------------------------------------------
section("5.0 the control: a clean cycle records the whole chain")
h, ok, exc = drive(minutes=0.05)
print("  labels:", [lab for lab in h.labels() if lab not in LOOP_LABELS])
check("5.0 the control cycle passes through both boundary labels",
      "inventory.before_cancel" in h.labels()
      and "cancel.before_change" in h.labels(), str(h.labels()))

# ---------------------------------------------------------------------------
section("5.1 fault matrix: one fault armed after inventory.before_cancel")
results = []
for title, stub, exc_obj, reachable in CASES:
    def setup(h, stub=stub, exc_obj=exc_obj):
        h.arm_after = {"inventory.before_cancel": (stub, exc_obj)}
    h, ok, err = drive(setup, minutes=1.0)
    cycles = h.out().count("===== cycle ")
    stopped = "stopped early" in h.out()
    truncated = sum(1 for lab in h.labels() if lab == "inventory.before_cancel")
    results.append((title, tail(h), cycles, stopped, signature(h), truncated,
                    reachable))
    print(f"\n  {title}")
    print(f"    reachable on the real I/O layer? {reachable}")
    print(f"    index tail (pre-instrumentation view): {tail(h)}")
    print(f"    cycles run: {cycles}   truncated cycles in the index: "
          f"{truncated}   stopped early: {stopped}")
    print(f"    exact signature: {signature(h)}")
    print(f"    run_loop said: "
          f"{[l.strip() for l in h.printed if 'refused' in l or 'raised' in l or 'in a row' in l][:1]}")

# ---------------------------------------------------------------------------
section("5.2 contrast: faults that DO leave an abort in the index")
for title, setup in [
    ("focus_game returns False",
     lambda h: h.arm_after.update(
         {"inventory.before_cancel": ("focus_game", None)})),
]:
    pass

h = build()
h.focus_fault = {}


def focus_false_after_label(h):
    """focus_game returns False on the call cancel_item makes."""
    original = h._focus

    def patched(settle=0.35, **_):
        if "inventory.before_cancel" in h.labels():
            h.log("focus_game(False)")
            return False
        return original(settle)
    return patched


with h:
    h.patch("focus_game", focus_false_after_label(h))
    ok, exc = run(trade.run_loop, ["relist-rows 1"], 1.0, 0.0)
print("  index tail:", tail(h))
check("5.2 a refused focus DOES record cancel.aborted",
      "cancel.aborted" in h.labels(), str(tail(h)))
check("5.2 so it is distinguishable from the observed signature",
      not signature(h), str(tail(h)))

h = build()


def locked_after_label(h):
    def patched(**_):
        return "inventory.before_cancel" in h.labels()
    return patched


with h:
    h.patch("session_locked", locked_after_label(h))
    ok, exc = run(trade.run_loop, ["relist-rows 1"], 1.0, 0.0)
print("  index tail:", tail(h))
check("5.2b a locked workstation also records cancel.aborted",
      "cancel.aborted" in h.labels(), str(tail(h)))
if "loop.stopped" not in h.labels():
    note("5.2b the locked-session break records nothing",
         "trade.py:5025-5029 breaks out of the loop without a "
         "record('loop.stopped'), unlike every other exit. A run that stops "
         "because the workstation locked therefore ends with a cycle.start "
         "and no explanation -- the one remaining unexplained-stop shape.")

# a click failure lands AFTER cancel.before_change, so it is also distinguishable
h = build()
h.arm_after = {"cancel.before_change": ("click", PermissionError(HINT))}
with h:
    ok, exc = run(trade.run_loop, ["relist-rows 1"], 1.0, 0.0)
print("  index tail:", tail(h))
check("5.2c a failed Change click stops one label later",
      tail(h)[-1] == "cancel.before_change", str(tail(h)))


# ---------------------------------------------------------------------------
section("5.3 the verdict")
matches = [r for r in results if r[4]]
single = [r for r in results if r[4] and r[3] and r[5] == 1]
repeated = [r for r in results if r[4] and r[3] and r[5] > 1]

for title, t, cycles, stopped, sig, truncated, reachable in results:
    verdict = ("SIGNATURE, 1 truncated cycle, run STOPS" if sig and truncated == 1
               else f"signature, but {truncated} truncated cycles first"
               if sig else "does not match")
    print(f"  {title:38} -> {verdict}")

check("5.3 the signature is reproduced", bool(matches), str(results))
check("5.3 only a PermissionError truncates a SINGLE cycle and stops",
      len(single) >= 1 and all("Permission" in r[0] for r in single),
      f"single={[r[0] for r in single]} repeated={[r[0] for r in repeated]}")
check("5.3 every other exception type leaves THREE truncated cycles",
      all(r[5] == trade.MAX_CONSECUTIVE_FAILURES for r in repeated),
      f"{[(r[0], r[5]) for r in repeated]}")

note("5.3 REPRODUCED",
     "park_cursor() raising PermissionError at trade.py:3489 -- the FIRST "
     "input call after record('inventory.before_cancel') at trade.py:4360 and "
     "before record('cancel.before_change') at trade.py:3544. cancel_item's "
     "only handler catches Aborted (trade.py:3596), so nothing records the "
     "abort; relist()'s finally swallows its own errors; run_sequence "
     "deliberately does not catch PermissionError (trade.py:4894-4899); "
     "run_loop catches it at trade.py:5057 and STOPS the run. One truncated "
     "cycle, no abort frame, no traceback, manual restart required -- exactly "
     "the report.")
note("5.3 DISCRIMINATOR",
     "count the truncated cycles in run_index.jsonl. ONE cycle ending at "
     "inventory.before_cancel => PermissionError (blocked input: not elevated, "
     "an elevated window took the foreground, or the secure desktop). THREE "
     "such cycles in a row => an OSError/RuntimeError from capture or OCR, "
     "retried until MAX_CONSECUTIVE_FAILURES stopped it.")


# ---------------------------------------------------------------------------
section("5.4 the OTHER way the index can stop: recording itself failing")
# record() swallows every exception by design (trade.py:1400-1401). If the PNG
# write fails -- a full disk, with ~3,600 frames at 1-3 MB each -- the index
# line is dropped too, silently, and the index truncates while the run carries
# on. Exercised against the REAL record(), with RECORD_DIR redirected into the
# scratchpad so the project corpus is never touched.
import json
from pathlib import Path
from PIL import Image

TMP = Path(__file__).resolve().parent / "_recdir"
TMP.mkdir(exist_ok=True)
for stale in TMP.glob("*"):
    stale.unlink()

# _record_full is gone: it was the "recording has stopped for good" latch, and
# recording no longer stops -- it prunes to a rolling window instead. RECORD_KEEP
# is saved in its place so this test's temporary corpus cannot be pruned out
# from under it mid-assertion.
saved = (trade.RECORD_DIR, trade.RECORD_ENABLED, trade._record_seq,
         trade.RECORD_KEEP)
try:
    trade.RECORD_DIR = TMP
    trade.RECORD_ENABLED = True
    trade._record_seq = 0
    trade.RECORD_KEEP = 10_000
    small = Image.new("RGB", (8, 8))
    trade.record("t.ok", small, note="written")

    class Unwritable:
        def save(self, *a, **k):
            raise OSError(28, "No space left on device")

    err = None
    try:
        trade.record("t.disk_full", Unwritable(), note="dropped")
    except BaseException as exc:      # noqa: BLE001
        err = exc

    index = TMP / "run_index.jsonl"
    entries = [json.loads(line) for line in
               index.read_text(encoding="utf-8").splitlines()] if index.exists() else []
finally:
    (trade.RECORD_DIR, trade.RECORD_ENABLED, trade._record_seq,
     trade.RECORD_KEEP) = saved

check("5.4 a healthy record() writes an index line",
      any(e["label"] == "t.ok" for e in entries), str(entries))
check("5.4 a failed PNG write raises nothing", err is None, repr(err))
check("5.4 ...and drops the index line silently",
      not any(e["label"] == "t.disk_full" for e in entries), str(entries))
note("5.4 second hypothesis",
     "a full disk truncates run_index.jsonl mid-cycle with no error and no "
     "gap in the run itself. It is DISTINGUISHABLE from the PermissionError "
     "case: recording failure leaves the run going (so the process is still "
     "alive and the shop keeps changing), whereas the observed incident "
     "required a manual restart. Worth confirming with free disk space "
     "against the corpus size before accepting the PermissionError verdict.")

raise SystemExit(summary())
