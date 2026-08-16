"""Copy flow frames out of the rotating corpus into stable goldens.

The corpus PRUNES automatically -- frames are deleted from the front on every
run -- so a test keyed to `run_25506.png` works today and skips silently in a
week. That is exactly what happened to the 428,142,429 Alz purchase frame: it
was used as a fixture in the afternoon and had rotated out by midnight.

So the frames worth keeping are promoted into corpus/goldens/flow/, which
nothing prunes, together with a manifest carrying the context record() wrote
beside each one. The manifest is what makes the copies testable: without the
recorded price, pack and available count, a promoted PNG is just a picture.

Both directories are gitignored -- they hold the operator's balance, character
name and live listings -- so this is about surviving pruning, not about
committing anything.

    py unit_tests/promote_goldens.py           # show what would be promoted
    py unit_tests/promote_goldens.py --apply   # copy them
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "unit_tests" / "corpus"
INDEX = CORPUS / "run_index.jsonl"
DEST = CORPUS / "goldens" / "flow"
MANIFEST = DEST / "manifest.jsonl"

# label -> how many of the newest to keep, and why it is worth keeping.
WANTED = {
    "buy.dialog": (8, "the Confirm Purchase dialog, last frame before Alz "
                      "moves; carries the quantity field and its maximum"),
    "buy.completed": (6, "the table straight after a purchase"),
    "buy.unmeasured": (4, "a purchase whose balance could not be read"),
    "sale.implausible": (6, "collections the ceiling refused -- negative "
                            "controls, and the ones it refused WRONGLY are "
                            "the evidence that the fix is a fix"),
    "sale.collected": (4, "the table after a Receive"),
    "convert.dialog": (6, "the vendor's Mass Purchase dialog as it opens"),
    "convert.typed": (6, "the quantity field after typing, which the "
                         "read-back check judges"),
    "convert.confirming": (4, "the last frame before the Sets are spent"),
    "npc.found": (3, "the NPC located, which every walk back depends on"),
    "tab.register_open": (3, "the Register tab, distinguished from Purchase"),
    "register.committed": (4, "a listing the moment it went live"),
    # FAILURE frames. The most valuable and the rarest: they are states no
    # synthetic test can construct, and the corpus prunes them like any other.
    # The 2026-08-08 disconnect produced a complete cascade -- a registration
    # that committed and could not be verified, a dirty work tab, the strand
    # recovery reaching for 175,000,000, and the breaker firing.
    "register.aborted": (8, "a registration that stopped part-way; the "
                            "context carries whether it had COMMITTED"),
    "cancel.aborted": (8, "a cancel that stopped part-way, likewise"),
    "worktab.not_empty": (4, "the work tab dirty at the start of a batch"),
    "strand.recovering": (4, "the strand recovery about to list an unnameable "
                             "item at the strictest floor -- the one path "
                             "that can lose money without asking"),
    "loop.stopped": (4, "the breaker firing, with its reason"),
    "batch.trimmed": (3, "a row spec trimmed to what the shop actually holds"),
}


def load_index():
    if not INDEX.exists():
        return []
    out = []
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def main():
    apply = "--apply" in sys.argv
    rows = load_index()
    if not rows:
        print(f"no index at {INDEX}")
        return 1

    existing = set()
    if MANIFEST.exists():
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    existing.add(json.loads(line).get("from"))
                except ValueError:
                    pass

    plan = []
    for label, (keep, _why) in WANTED.items():
        got = [r for r in rows if r.get("label") == label
               and (CORPUS / (r.get("file") or "")).exists()]
        for n, row in enumerate(got[-keep:], 1):
            if row["file"] in existing:
                continue
            stem = label.replace(".", "_")
            plan.append((row, f"{stem}_{row['file'][4:-4]}.png"))

    print(f"{len(rows)} indexed frames, {len(plan)} to promote into {DEST}\n")
    by_label = {}
    for row, _name in plan:
        by_label[row["label"]] = by_label.get(row["label"], 0) + 1
    for label, (keep, why) in WANTED.items():
        have = sum(1 for r in rows if r.get("label") == label
                   and (CORPUS / (r.get("file") or "")).exists())
        print(f"  {label:22} {by_label.get(label, 0):>2} new "
              f"({have} on disk, keeping up to {keep})")
        print(f"      {why}")

    if not apply:
        print("\n(dry run - pass --apply to copy)")
        return 0

    DEST.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a", encoding="utf-8") as out:
        for row, name in plan:
            shutil.copy2(CORPUS / row["file"], DEST / name)
            entry = dict(row)
            entry["from"] = row["file"]
            entry["file"] = name
            out.write(json.dumps(entry) + "\n")
    print(f"\npromoted {len(plan)} frame(s) into {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
