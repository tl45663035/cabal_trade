"""Are the 'index stops mid-cycle' gaps missing RUN, or missing INDEX LINES?"""

import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent.parent.parent
_sys.path.insert(0, str(_ROOT))
import json
from pathlib import Path

CORPUS = Path(_ROOT / "unit_tests" / "corpus")
INDEX = CORPUS / "run_index.jsonl"
entries = [json.loads(l) for l in
           INDEX.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]

indexed = {e["file"] for e in entries if e.get("file")}
on_disk = sorted(p.name for p in CORPUS.glob("run_*.png"))
orphans = [n for n in on_disk if n not in indexed]

print(f"PNGs on disk        : {len(on_disk)}")
print(f"index entries       : {len(entries)}")
print(f"PNGs never indexed  : {len(orphans)}")
print("  " + ", ".join(orphans))

missing_png = [e["file"] for e in entries
               if e.get("file") and e["file"] not in set(on_disk)]
print(f"index entries whose PNG is gone: {len(missing_png)}  {missing_png[:10]}")

print("\nThe three cycles that reached inventory.before_cancel and stopped:")
for i, e in enumerate(entries):
    if e.get("label") != "inventory.before_cancel":
        continue
    nxt = entries[i + 1] if i + 1 < len(entries) else None
    if nxt is None or nxt.get("label") == "cancel.before_change":
        continue
    a = int(e["file"][4:9])
    b = int(nxt["file"][4:9])
    between = [f"run_{n:05d}.png" for n in range(a + 1, b)]
    exists = [n for n in between if (CORPUS / n).exists()]
    print(f"\n  {e['at']}  {e['file']}  ->  {nxt['at']}  {nxt['file']} "
          f"({nxt['label']})")
    print(f"    frames written in between : {len(between)}")
    print(f"    ...of which exist on disk : {len(exists)}  {exists}")
    print("    => " + ("the run KEPT GOING and the index lines were lost"
                       if exists else
                       "no frames written either: the run really did stop here"))

print("\nSize of the corpus and the free space it needs:")
total = sum(p.stat().st_size for p in CORPUS.glob("run_*.png"))
print(f"  {total / 2**30:.2f} GiB over {len(on_disk)} frames "
      f"({total / max(1, len(on_disk)) / 2**20:.1f} MiB each)")
print(f"  recording now keeps a ROLLING window and prunes the oldest, so the "
      f"corpus\n  settles at a fixed size instead of stopping dead at a cap")

