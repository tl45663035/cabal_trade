import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trade as m

try:
    from PIL import Image
except Exception:
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
        except Exception:
            continue
    return rows


def frame_of(entry):
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


def expect_purchase_dialog(shot, entry):
    dialog = m.purchase_confirm(shot)
    if not dialog:
        return False, "purchase_confirm() found no dialog"
    if not dialog.get("buy"):
        return False, "no Buy button located"
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


def agrees_with_record(shot, entry):
    label = entry.get("label")
    if label == "buy.dialog":
        dialog = m.purchase_confirm(shot)
        if not dialog:
            return []
        out = []
        want = entry.get("available")
        got = dialog.get("qty_max")
        if want is not None and got is not None:
            out.append((got <= want + 2,
                        f"qty_max {got} exceeds the table's {want} available"))
        return out
    return []


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
            skipped += 1
            continue
        try:
            ok, detail = rule(shot, entry)
        except Exception as exc:
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

check(checks > 0, "the corpus index is present but produced NO checks at all")

print(f"\n{checks} checks, {len(failures)} FAILED")
for f in failures:
    print(f"  FAIL: {f}")
raise SystemExit(1 if failures else 0)
