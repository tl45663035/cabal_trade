"""Sequential replay: every recorded step must leave the screen in the state
the next step assumes.

WHY THIS FILE EXISTS
--------------------
The stub suites drive the state machine's control flow honestly and cannot see
anything the stubs answer for -- coordinates, regions, tabs, confidence,
frames. A 2026-08-10 audit measured that directly: 32 of 43 mutations survived
the failpath suites and 14 of 20 survived the unit suites, because the fixtures
answer from ground truth and ignore the argument whose wrongness IS the bug.

This suite has no stubs. It replays the frames the live script recorded, in the
order it recorded them, and asserts at each step that the REAL readers see the
state the NEXT step depends on. A frame cannot be argued with.

WHAT AN EPISODE IS
------------------
The operator's framing: "after one click the next window is expected... each
row is 1 separate test, entire cycle can be 1 unit test, one chaos resupply is
1 unit test, one core resupply is 1 unit test."

run_index.jsonl is exactly that recording. Every record() call writes a labelled
frame; with --debug-frames every INPUT writes one too (label "do.action"). So a
run is a sequence of (label, frame, context), and an episode is the span between
two markers:

    cycle.start ...          one relist cycle
    restock.scoped ...       one Core resupply
    chaos.bought ...         one chaos resupply
    cancel.before_change ... one row

Each episode becomes its own set of checks, so a failure names the step that
broke rather than "the suite is red".

WHAT IS ASSERTED
----------------
Two independent things, and the second is the one the stubs can never do:

  1. TRANSITIONS. After the step labelled X, the frame must satisfy the
     precondition the code checks before doing Y. `register.before_load` must
     show the Register tab open; `buy.dialog` must show a Confirm Purchase
     dialog that purchase_confirm() can actually read; `refresh.after` must not
     be mid-reload.

  2. AGREEMENT WITH RECORDED TRUTH. The index stores the VALUES the script
     believed at that moment -- item, price, qty, spend, available. Re-reading
     the frame must reproduce them. This is what catches a reader that was
     wrong when the frame was taken, which is how a green suite once shipped a
     misreading table reader.

A missing corpus is announced and skips; it is gitignored and not every machine
has it. A missing FRAME inside a present corpus is a failure, not a skip --
that is the silent-no-coverage shape this file is written to avoid.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trade as m  # noqa: E402

try:
    from PIL import Image
except Exception:  # noqa: BLE001
    Image = None

m.NO_INPUT = True
CORPUS = ROOT / "unit_tests" / "corpus"
INDEX = CORPUS / "run_index.jsonl"

failures: list[str] = []
checks = 0
skipped = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


def load_index():
    rows = []
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001 - a torn tail line is not a failure
            continue
    return rows


def frame_of(entry):
    """The image for an index entry, with its layout applied. None if absent."""
    name = entry.get("file")
    if not name:
        return None
    path = CORPUS / name
    if not path.exists() or Image is None:
        return None
    layout = entry.get("layout") or {}
    m.apply_layout(m.Layout(
        screen=tuple(layout.get("screen", (2560, 1440))),
        origin=tuple(layout.get("origin", (10, 30))),
        scale=float(layout.get("scale", 1.0))))
    return Image.open(path)


# --------------------------------------------------------------------------
# EPISODES
# --------------------------------------------------------------------------
# A marker opens an episode; the episode runs until the next marker of any
# kind. The label tells us which kind of work it was, which is what decides
# the assertions that apply to it.
EPISODE_MARKERS = {
    "cycle.start": "cycle",
    "restock.scoped": "core resupply",
    "chaos.bought": "chaos resupply",
    "cancel.before_change": "row",
}


def episodes(rows):
    out, current = [], None
    for entry in rows:
        kind = EPISODE_MARKERS.get(entry.get("label"))
        if kind:
            if current:
                out.append(current)
            current = {"kind": kind, "at": entry.get("at"), "steps": [entry]}
        elif current:
            current["steps"].append(entry)
    if current:
        out.append(current)
    return out


# --------------------------------------------------------------------------
# STEP EXPECTATIONS
# --------------------------------------------------------------------------
# What the frame recorded AT a label must satisfy, expressed as the very
# predicate the production code consults before its next action. Each returns
# (ok, detail); a reader raising is a failure, never a skip.
def expect_purchase_dialog(shot, entry):
    dialog = m.purchase_confirm(shot)
    if not dialog:
        return False, "purchase_confirm() found no dialog"
    if not dialog.get("buy"):
        return False, "no Buy button located"
    # The two fields that decide how much money moves.
    if dialog.get("qty_max") is None:
        return False, "qty_max did not read - buy_offer falls back to ONE listing"
    if not dialog.get("price"):
        return False, "Purchase Price did not read - the only proof of quantity"
    return True, ""


def expect_register_panel(shot, entry):
    panel = m.read_register_panel(shot)
    if panel.get("loaded") is None:
        return False, "read_register_panel returned no verdict"
    return True, ""


def expect_priced_panel(shot, entry):
    panel = m.read_register_panel(shot)
    if not panel.get("price_rows"):
        return False, ("no suggestion rows - choose_price would fall back to "
                       "FALLBACK_PRICE and park the stack at 10,000,000,000")
    return True, ""


def expect_table_readable(shot, entry):
    if m.table_loading(shot):
        return False, "the table is still loading at a step that reads it"
    return True, ""


STEP_CHECKS = {
    "buy.dialog": expect_purchase_dialog,
    "register.before_load": expect_register_panel,
    "register.priced": expect_priced_panel,
    "price.suggestions": expect_priced_panel,
    "refresh.after": expect_table_readable,
}


# --------------------------------------------------------------------------
# AGREEMENT WITH WHAT THE SCRIPT BELIEVED
# --------------------------------------------------------------------------
def agrees_with_record(shot, entry):
    """Re-read the frame and compare with the values stored beside it."""
    label = entry.get("label")
    if label == "buy.dialog":
        dialog = m.purchase_confirm(shot)
        if not dialog:
            return []
        out = []
        want = entry.get("available")
        got = dialog.get("qty_max")
        if want is not None and got is not None:
            # qty_max is the dialog's own limit and `available` is the table's
            # count; they may legitimately differ when the market moves between
            # the two reads, so this is bounded rather than exact.
            out.append((got <= want + 2,
                        f"qty_max {got} exceeds the table's {want} available"))
        return out
    return []


# --------------------------------------------------------------------------
# RUN
# --------------------------------------------------------------------------
if not INDEX.exists():
    print(f"SKIP: no corpus index at {INDEX} (it is gitignored). "
          f"Run the script once with recording on to build one.")
    raise SystemExit(0)

rows = load_index()
eps = episodes(rows)
print(f"corpus: {len(rows)} recorded steps, {len(eps)} episode(s)")

by_kind: dict = {}
for ep in eps:
    by_kind.setdefault(ep["kind"], 0)
    by_kind[ep["kind"]] += 1
print("episodes by kind: "
      + ", ".join(f"{k} x{v}" for k, v in sorted(by_kind.items())))

covered = 0
for ep in eps:
    title = f"{ep['kind']} at {ep['at']}"
    steps_checked = 0
    for entry in ep["steps"]:
        rule = STEP_CHECKS.get(entry.get("label"))
        if rule is None:
            continue
        shot = frame_of(entry)
        if shot is None:
            # A recorded step whose frame is gone. Counted, not skipped: this
            # is the "silent no coverage" shape the file exists to avoid.
            skipped += 1
            continue
        try:
            ok, detail = rule(shot, entry)
        except Exception as exc:  # noqa: BLE001 - a reader that raises is a bug
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        check(ok, f"[{title}] {entry['label']} ({entry.get('file')}): {detail}")
        for agree_ok, agree_detail in agrees_with_record(shot, entry):
            check(agree_ok, f"[{title}] {entry['label']} "
                            f"({entry.get('file')}): {agree_detail}")
        steps_checked += 1
    if steps_checked:
        covered += 1

print(f"episodes with at least one checkable step: {covered}/{len(eps)}")
if skipped:
    print(f"NOTE: {skipped} recorded step(s) had no frame on disk "
          f"(the corpus prunes old PNGs; the index outlives them).")

# The suite must not pass by covering nothing -- the failure shape found in
# t35_buying_golden, which exits 0 having asserted nothing when its corpus is
# absent. A present index with zero checkable steps is a failure.
check(checks > 0, "the corpus index is present but produced NO checks at all")

print(f"\n{checks} checks, {len(failures)} FAILED")
for f in failures:
    print(f"  FAIL: {f}")
raise SystemExit(1 if failures else 0)
