import argparse
import re
import statistics
import sys
from pathlib import Path

LOGS = Path(__file__).resolve().parent / "logs"
STEP_HEAD = re.compile(r"^\s+#\s+ms\s+share\s+step\s*$")
PHASE_HEAD = re.compile(r"^\s+#\s+ms\s+share\s+n\s+each\s+phase\s*$")
STEP_ROW = re.compile(r"^\s+\d+\s+([\d,.]+)\s+[\d.]+%\s+(.+?)\s*$")
PHASE_ROW = re.compile(r"^\s+\d+\s+([\d,.]+)\s+[\d.]+%\s+(\d+)\s+([\d,.]+)\s+(.+?)\s*$")
TOTAL_ROW = re.compile(r"^\s+([\d,.]+)\s+100\.0%\s*(TOTAL)?\s*$")
BUY_TITLE = re.compile(r"^buy from favourite slot (\d+): (.+)$")
FULL_COLLECT = re.compile(r"^collect row \d+ in full$")
COLLECT_CHECKS = {"read the row and its button after collecting",
                  "look for 'please wait and try again'",
                  "close and open the Agent Shop again",
                  "scroll to the row again",
                  "read the row and its button again"}
CARRIED = re.compile(r"balance before [\d,]+, as the last order left it")
LINES = [
    (re.compile(r"tab \d read in (\d+) ms after the listing"), "tab 4 read after a listing"),
    (re.compile(r"tab \d read from the screen in (\d+) ms"), "tab 4 read before a withdrawal"),
    (re.compile(r"PaddleOCR read all four again in (\d+) ms"), "backup OCR re-read of the four prices"),
    (re.compile(r"the gift box took ([\d,]+) ms"), "gift box"),
    (re.compile(r"^\s+margin check for (?:.+?): (?:.+?), ([\d,]+) ms$"), "margin check, whole"),
]
COUNTS = [
    (re.compile(r"timed out after \d+s; row 1 reads"), "favourite searches that timed out"),
    (re.compile(r"^-- pass \d+ --"), "passes"),
    (re.compile(r"relisted \d+ at [\d,]+ in row"), "rows relisted"),
]
OUTER = "get_price: search the favourite and read row 1"


def num(text):
    return float(text.replace(",", ""))


def plain(label):
    return re.sub(r"\(OCR [0-9x]+\)|\(\d+ look\(s\), \d+ read\(s\)\)", "",
                  re.sub(r"\d[\d,]*", "N", label)).strip()


def read_run(stamp):
    data, counts = {}, {}

    def add(key, value):
        data.setdefault(key, []).append(value)

    path = LOGS / f"{stamp}_run.log"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    title, rows, kind, carried = "", [], None, False
    for i, line in enumerate(lines):
        for rule, label in COUNTS:
            if rule.search(line):
                counts[label] = counts.get(label, 0) + 1
        if CARRIED.search(line):
            carried = True
        if STEP_HEAD.match(line) or PHASE_HEAD.match(line):
            kind = "phase" if PHASE_HEAD.match(line) else "step"
            back = i - 1
            while back >= 0 and not lines[back].strip():
                back -= 1
            title, rows = lines[back].strip() if back >= 0 else "", []
            continue
        if kind == "step":
            if TOTAL_ROW.match(line):
                buy = BUY_TITLE.match(title)
                if buy and buy.group(2).startswith("bought"):
                    order = "later" if carried else "first"
                    labels = {s: ms for ms, s in rows}
                    add(f"buy order, whole ({order})",
                        sum(ms for ms, s in rows if not s.startswith("get_price:") or s == OUTER))
                    searched = any(s.startswith("get_price: click favourite") for _ms, s in rows)
                    if OUTER in labels:
                        add(f"buy order: find the offer ({'search' if searched else 'row 1 in place'})",
                            labels[OUTER])
                    for ms, s in rows:
                        if not s.startswith("get_price:") or s == OUTER:
                            add(f"buy order step: {plain(s)} ({order})", ms)
                if buy:
                    carried = False
                elif title.startswith("cancel row"):
                    add("relist: cancel, whole", sum(ms for ms, _s in rows))
                    for ms, s in rows:
                        add(f"relist cancel step: {plain(s)}", ms)
                elif re.match(r"^list \d+ at [\d,]+$", title):
                    add("relist: listing, whole", sum(ms for ms, _s in rows))
                    for ms, s in rows:
                        add(f"relist listing step: {plain(s)}", ms)
                elif FULL_COLLECT.match(title):
                    whole = sum(ms for ms, _s in rows)
                    before, seen = 0.0, set()
                    for ms, s in rows:
                        if s not in COLLECT_CHECKS and s not in seen:
                            before += ms
                            seen.add(s)
                    add("collect in full: whole", whole)
                    add("collect in full: without the new checks", before)
                    add("collect in full: added by the new checks",
                        whole - before)
                    for ms, s in rows:
                        add(f"collect in full step: {plain(s)}", ms)
                kind = None
                continue
            m = STEP_ROW.match(line)
            if m:
                rows.append((num(m.group(1)), m.group(2)))
                continue
        if kind == "phase":
            m = PHASE_ROW.match(line)
            if m:
                for _ in range(int(m.group(2))):
                    add(f"phase: {plain(m.group(4))}", num(m.group(3)))
                continue
            if not line.strip():
                kind = None
        for rule, label in LINES:
            m = rule.search(line)
            if m:
                add(label, num(m.group(1)))
    return data, counts


def pool(stamps):
    data, counts = {}, {}
    for stamp in stamps:
        d, c = read_run(stamp)
        for k, v in d.items():
            data.setdefault(k, []).extend(v)
        for k, v in c.items():
            counts[k] = counts.get(k, 0) + v
    return data, counts


def cell(values):
    return f"{statistics.median(values):,.0f} ms (n={len(values)})" if values else "-"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--after")
    parser.add_argument("--only")
    args = parser.parse_args()
    newest = sorted(LOGS.glob("*_run.log"))[-1].name[:-len("_run.log")]
    before = [s for s in args.before.split(",") if s]
    after = [s for s in (args.after or newest).split(",") if s]
    b, bc = pool(before)
    a, ac = pool(after)
    keys = sorted(set(b) | set(a))
    if args.only:
        keys = [k for k in keys if args.only.lower() in k.lower()]
    width = max([len(k) for k in keys] + [10])
    print(f"before: {', '.join(before)}\nafter:  {', '.join(after)}\n")
    print(f"{'':{width}}  {'before (median)':>22}  {'after (median)':>22}  change")
    for key in keys:
        bv, av = b.get(key, []), a.get(key, [])
        change = ""
        if bv and av and statistics.median(bv):
            d = statistics.median(av) - statistics.median(bv)
            change = f"{d:+,.0f} ms ({d / statistics.median(bv) * 100:+.0f}%)"
        print(f"{key:{width}}  {cell(bv):>22}  {cell(av):>22}  {change}")
    print()
    for label in sorted(set(bc) | set(ac)):
        print(f"{label:{width}}  {bc.get(label, 0):>22}  {ac.get(label, 0):>22}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
