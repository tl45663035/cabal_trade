"""Did the batch relist every row in range, once, without skipping?

Reads a run log and answers three questions per cycle, from the log alone:

  COVERED    every row the batch planned was visited
  ONCE       no row was visited twice
  ACTED      every visited row either changed price, was correctly left at
             the price it already had, or gave a stated reason

The third is the one that matters and the one that is easy to fake. A row
"visited" and then silently dropped looks identical in a summary to a row
that was correctly left alone -- so this pulls the REASON out of each row's
own segment and refuses to count an unexplained skip as success.

    python unit_tests/row_coverage_check.py [logfile]
"""
import collections
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def newest_log() -> Path:
    logs = sorted((_ROOT / "logs").glob("run_*.log"),
                  key=lambda p: p.stat().st_mtime)
    if not logs:
        raise SystemExit("no run logs found")
    return logs[-1]


# What a row's segment can end with, and whether that counts as handled.
OUTCOMES = [
    (r"Registered \(.*?\) qty .*?\. Row (\d+)",        "RELISTED", True),
    (r"is already at the (?:lowest|market)",           "at market", True),
    (r"already the lowest|already lowest",             "at market", True),
    (r"floor",                                          "held at floor", True),
    (r"no longer in the table|already sold out",       "sold", True),
    (r"is empty|nothing to relist",                    "empty slot", True),
    (r"did not read clearly|could not be read",        "UNREADABLE", False),
    (r"has already been relisted this cycle",          "SKIPPED (duplicate guard)", False),
    (r"skipping",                                       "SKIPPED", False),
]


def analyse(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    cycles, cur = [], None
    for l in lines:
        if "===== cycle" in l:
            if cur is not None:
                cycles.append(cur)
            cur = {"plan": [], "segs": []}
            continue
        if cur is None:
            continue
        m = re.search(r"^\s*\[.*?\]\s+(\d+)\.\s+\[(change|register|receive)\]", l)
        if m:
            cur["plan"].append(int(m.group(1)))
        m = re.search(r"#{5,}\s*(\d+)/(\d+): row (\d+) - '(.*?)'", l)
        if m:
            cur["segs"].append({"pos": int(m.group(1)), "row": int(m.group(3)),
                                "item": m.group(4), "lines": []})
            continue
        if cur["segs"]:
            cur["segs"][-1]["lines"].append(l)
    if cur is not None:
        cycles.append(cur)
    cycles = [c for c in cycles if c["segs"]]

    print(f"log: {path.name}\n")
    problems = 0
    for i, c in enumerate(cycles, 1):
        plan = c["plan"]
        seen = [s["row"] for s in c["segs"]]
        dupes = [r for r, n in collections.Counter(seen).items() if n > 1]
        missing = [r for r in plan if r not in seen]
        extra = [r for r in seen if plan and r not in plan]

        verdicts = collections.Counter()
        unexplained = []
        for s in c["segs"]:
            body = "\n".join(s["lines"])
            label, ok = "no stated outcome", False
            for pat, name, good in OUTCOMES:
                if re.search(pat, body, re.I):
                    label, ok = name, good
                    break
            verdicts[label] += 1
            if not ok:
                unexplained.append((s["row"], s["item"], label))

        bad = bool(dupes or missing or extra or unexplained)
        problems += bad
        print(f"  cycle {i}: {len(seen)} row(s) visited of {len(plan)} planned"
              f"   {'PROBLEM' if bad else 'clean'}")
        print(f"    covered : {'yes' if not missing else f'NO -- missing {missing}'}")
        print(f"    once    : {'yes' if not dupes else f'NO -- repeated {dupes}'}")
        print(f"    in plan : {'yes' if not extra else f'NO -- unplanned {extra}'}")
        for label, n in verdicts.most_common():
            print(f"      {n:>3}  {label}")
        for row, item, label in unexplained[:8]:
            print(f"      !! row {row} ({item}) -> {label}")
        print()

    print(f"{len(cycles)} cycle(s), {problems} with a problem")
    return 1 if problems else 0


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else newest_log()
    raise SystemExit(analyse(p))
