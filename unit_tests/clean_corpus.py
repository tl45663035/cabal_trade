
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

CORPUS = _ROOT / "unit_tests" / "corpus"
INDEX = CORPUS / "run_index.jsonl"
COORD = re.compile(r"^\(\d+,\s*\d+\)$")

DEBRIS_LABELS = {"x", "selftest.step", "selftest.aborted"}
PLACEHOLDER_NAMES = {"VIP", "x"}

raw = INDEX.read_text(encoding="utf-8").splitlines()
seen = len(raw)

keep, dropped, fixed_label, fixed_at = [], [], 0, 0
for line in raw:
    if not line.strip():
        continue
    try:
        e = json.loads(line)
    except ValueError:
        keep.append(line)
        continue

    if COORD.match(str(e.get("label", ""))):
        e["ctx_label"] = e["label"]
        e["label"] = "npc.found"
        fixed_label += 1
    if COORD.match(str(e.get("at", ""))):
        e["ctx_at"] = e["at"]
        png = CORPUS / e.get("file", "")
        e["at"] = (datetime.fromtimestamp(png.stat().st_mtime)
                   .isoformat(timespec="seconds")) if png.exists() else ""
        fixed_at += 1

    if (e.get("label") in DEBRIS_LABELS
            or e.get("name") in PLACEHOLDER_NAMES
            or e.get("item") in PLACEHOLDER_NAMES
            or e.get("reason") in PLACEHOLDER_NAMES):
        dropped.append(e)
        continue

    import trade as _mm
    if e.get("label") == "price.suggestions" and e.get("rows") in ("[]", "", None):
        e["dropped_because"] = "price rows '[]' cannot occur under current code"
        dropped.append(e)
        continue
    if (e.get("label") in ("price.suggestions", "price.before_select")
            and (e.get("lowest") or e.get("price") or _mm.MIN_PLAUSIBLE_PRICE)
            < _mm.MIN_PLAUSIBLE_PRICE):
        e["dropped_because"] = "recorded price below MIN_PLAUSIBLE_PRICE"
        dropped.append(e)
        continue

    if e.get("label") == "npc.found" and (CORPUS / e.get("file", "")).exists():
        import trade as _m
        from PIL import Image as _Image
        if _m.find_npc(_Image.open(CORPUS / e["file"]), retries=1) is None:
            e["dropped_because"] = "npc.found frame does not contain the NPC"
            dropped.append(e)
            continue

    keep.append(json.dumps(e))

now = INDEX.read_text(encoding="utf-8").splitlines()
if len(now) > seen:
    keep.extend(now[seen:])
    print(f"carried over {len(now) - seen} entries written during the clean")

print(f"labels repaired:    {fixed_label}")
print(f"timestamps repaired:{fixed_at}")
print(f"entries dropped:    {len(dropped)}")
for e in dropped:
    ctx = {k: v for k, v in e.items() if k not in ("file", "at")}
    print(f"    {e.get('file')} {ctx}")
print(f"entries kept:       {len(keep)}  (was {seen})")

if "--apply" in sys.argv:
    INDEX.write_text("\n".join(keep) + "\n", encoding="utf-8")
    print("written")
else:
    print("dry run - pass --apply to write")
