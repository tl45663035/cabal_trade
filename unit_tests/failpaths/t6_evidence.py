"""Read-only forensics on the real run_index.jsonl.

Applies the discriminator from t5 to the actual recorded evidence.
"""
import json
from collections import Counter
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent.parent

INDEX = _ROOT / "unit_tests" / "corpus" / "run_index.jsonl"

entries = []
bad = 0
for line in INDEX.read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        entries.append(json.loads(line))
    except Exception:
        bad += 1

print(f"index: {len(entries)} entries, {bad} unparseable")
print(f"first: {entries[0].get('at')}   last: {entries[-1].get('at')}")

labels = Counter(e.get("label") for e in entries)
print("\nlabel counts (top 25):")
for label, n in labels.most_common(25):
    print(f"  {n:6d}  {label}")

print("\ncycle-boundary instrumentation present?")
for label in ("cycle.start", "cycle.end", "cycle.exception", "loop.stopped"):
    print(f"  {label:18} {labels.get(label, 0)}")

# Every point at which the chain reached inventory.before_cancel and what
# came next.
print("\nwhat followed each inventory.before_cancel:")
after = Counter()
truncations = []
for i, e in enumerate(entries):
    if e.get("label") != "inventory.before_cancel":
        continue
    nxt = entries[i + 1].get("label") if i + 1 < len(entries) else "<END OF INDEX>"
    after[nxt] += 1
    if nxt != "cancel.before_change":
        truncations.append((i, e.get("at"), nxt))
for label, n in after.most_common():
    print(f"  {n:6d}  -> {label}")

print(f"\ncycles that did NOT reach cancel.before_change: {len(truncations)}")
for i, at, nxt in truncations[-12:]:
    print(f"  entry {i} at {at}: next label was {nxt!r}")

print("\nlast 15 entries:")
for e in entries[-15:]:
    extra = {k: v for k, v in e.items()
             if k not in ("file", "label", "at")}
    print(f"  {e.get('at')}  {e.get('label'):26} {str(extra)[:110]}")

# Gaps in wall-clock time: where a run stopped and a new one started.
print("\nlargest time gaps between consecutive entries:")
from datetime import datetime
stamped = [(datetime.fromisoformat(e["at"]), e) for e in entries if e.get("at")]
gaps = []
for (t0, e0), (t1, e1) in zip(stamped, stamped[1:]):
    gaps.append(((t1 - t0).total_seconds(), e0.get("label"), e1.get("label"), t0))
for secs, a, b, when in sorted(gaps, reverse=True)[:12]:
    print(f"  {secs/60:8.1f} min  after {a:26} at {when}  -> next {b}")
