"""Zoom in on the interesting windows of the real run_index.jsonl."""

import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent.parent.parent
_sys.path.insert(0, str(_ROOT))
import json
from datetime import datetime
from pathlib import Path

INDEX = _ROOT / "unit_tests" / "corpus" / "run_index.jsonl"
entries = [json.loads(l) for l in
           INDEX.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]


def show(lo, hi, title):
    print(f"\n{'=' * 74}\n{title}  (entries {lo}..{hi})\n{'=' * 74}")
    for i in range(max(0, lo), min(len(entries), hi)):
        e = entries[i]
        extra = {k: v for k, v in e.items() if k not in ("file", "label", "at")}
        print(f"  {i:5d} {e.get('at')} {e.get('label'):28} "
              f"{(e.get('file') or '-'):15} {str(extra)[:90]}")


# the two adjacent inventory.before_cancel pairs
show(126, 160, "09:55 and 09:58 - inventory.before_cancel with no cancel after it")

# the third truncation
show(714, 730, "10:53 - inventory.before_cancel followed by a fresh refresh")

# the five-hour outage
target = datetime.fromisoformat("2026-08-03T19:56:48")
idx = min(range(len(entries)),
          key=lambda i: abs(datetime.fromisoformat(entries[i]["at"]) - target))
show(idx - 20, idx + 6, "19:56 - the 5-hour outage")

# the four aborts actually recorded
print(f"\n{'=' * 74}\nevery cancel.aborted / register.aborted recorded\n{'=' * 74}")
for i, e in enumerate(entries):
    if e.get("label") in ("cancel.aborted", "register.aborted"):
        print(f"  {i:5d} {e['at']} {e['label']}: "
              + str({k: v for k, v in e.items()
                     if k not in ('file', 'label', 'at')})[:200])

# file numbering: does the index ever skip a frame number?
print(f"\n{'=' * 74}\nframe numbering continuity\n{'=' * 74}")
nums = [int(e["file"][4:9]) for e in entries if e.get("file") or "".startswith("run_")]
skips = [(a, b) for a, b in zip(nums, nums[1:]) if b != a + 1]
print(f"  {len(nums)} numbered frames, {len(skips)} discontinuities")
for a, b in skips[:20]:
    print(f"    run_{a:05d} -> run_{b:05d}   ({b - a - 1} frame(s) never indexed)")

png = sorted(Path(_ROOT / "unit_tests" / "corpus").glob("run_*.png"))
print(f"  PNGs on disk: {len(png)}, highest = {png[-1].name if png else '-'}")
indexed = {e["file"] for e in entries if e.get("file")}
orphans = [p.name for p in png if p.name not in indexed]
print(f"  PNGs with NO index entry: {len(orphans)}  {orphans[:8]}")
