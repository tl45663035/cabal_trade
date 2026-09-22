import datetime
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETTINGS = json.loads((ROOT / "src_1080p" / "config.json")
                      .read_text(encoding="utf-8"))
KNOBS = SETTINGS["tools"]
SUP = SETTINGS["supervise"]
UNITS = {"B": KNOBS["short_b"], "M": KNOBS["short_m"], "K": KNOBS["short_k"]}
PLACES = KNOBS["short_places"]
DIR = ROOT / SUP["report_dir"]
NAME = SUP["report_name"]
BRANCH = SUP["report_branch"]
TOGETHER = SUP["together_dir"]
ACCOUNTS = SUP["together_configs"]
GIT_TIMEOUT = SUP["report_timeout"]

DAY = re.compile(r"^(\w{3} \d{4}-\d{2}-\d{2})( \(so far\))?\s+([\d.]+)h\s+"
                 r"([\d.]+)h\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")
ASOF = re.compile(r"^PROFIT SUMMARY -- sold since (\S+ \S+ \S+) \(as of (.+)\)")
WORTH = re.compile(r"^\s+(stock at its listed price|Alz, latest balance line|"
                   r"NET WORTH|bought since that board, at what it cost|"
                   r"rows the board did not show)\s+(\S+)\s*$")


def value(text):
    text = text.strip().replace(",", "")
    if text in ("-", "--", ""):
        return 0.0
    sign = -1.0 if text.startswith("-") else 1.0
    text = text.lstrip("+-")
    if text.endswith("%"):
        return sign * float(text[:-1])
    for suffix, scale in UNITS.items():
        if text.endswith(suffix):
            return sign * float(text[:-1]) * scale
    return sign * float(text)


def short(number):
    for suffix, scale in UNITS.items():
        if abs(number) >= scale:
            return f"{number / scale:.{PLACES}f}{suffix}"
    return f"{number:,.0f}"


def read(account, live):
    path = DIR / account / NAME
    if account == live and path.exists():
        return path.read_text(encoding="utf-8", errors="replace"), "this machine"
    out = subprocess.run(
        ["git", "show", f"origin/{BRANCH}:{path.relative_to(ROOT).as_posix()}"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=GIT_TIMEOUT)
    if out.returncode:
        return "", "no report pushed yet"
    when = subprocess.run(
        ["git", "log", "-1", "--format=%ad", "--date=format:%H:%M",
         f"origin/{BRANCH}", "--", path.relative_to(ROOT).as_posix()],
        cwd=str(ROOT), capture_output=True, text=True, timeout=GIT_TIMEOUT)
    return out.stdout, f"pushed {when.stdout.strip() or '?'}"


def parse(text):
    days, worth, asof = {}, {}, ""
    for line in text.splitlines():
        hit = DAY.match(line)
        if hit:
            label, sofar, up, gone, profit, _hour, revenue, cost, units, _m = \
                hit.groups()
            days[label] = {"so far": bool(sofar), "up": float(up),
                           "elapsed": float(gone), "profit": value(profit),
                           "revenue": value(revenue), "cost": value(cost),
                           "units": value(units)}
            continue
        hit = ASOF.match(line)
        if hit:
            asof = hit.group(2)
            continue
        hit = WORTH.match(line)
        if hit:
            worth[hit.group(1)] = value(hit.group(2))
    return {"days": days, "worth": worth, "as of": asof}


def line(char="-", width=118):
    print(char * width)


def main(argv):
    live = argv[1] if len(argv) > 1 else ACCOUNTS[0]
    books = {}
    for account in ACCOUNTS:
        text, source = read(account, live)
        books[account] = parse(text)
        books[account]["source"] = source
    labels = sorted({d for b in books.values() for d in b["days"]},
                    key=lambda d: d.split()[1])
    now = datetime.datetime.now()
    print(f"TOGETHER -- {', '.join(ACCOUNTS)}, compiled {now:%Y-%m-%d %H:%M} "
          f"from each account's own profit summary")
    print("")
    for account in ACCOUNTS:
        book = books[account]
        print(f"  {account:<8}{book['source']:<22}"
              + (f"as of {book['as of']}" if book["as of"] else "nothing to read"))
    print("")
    head = (f"{'day':<26}{'up time':>9}" + "".join(f"{a:>12}" for a in ACCOUNTS)
            + f"{'PROFIT':>13}{'an hour':>12}{'revenue':>12}{'cost':>12}"
              f"{'units':>10}{'margin':>9}")
    print(head)
    line(width=len(head))
    grand = {k: 0.0 for k in ("up", "profit", "revenue", "cost", "units")}
    for label in labels:
        row = {k: 0.0 for k in grand}
        each = []
        for account in ACCOUNTS:
            day = books[account]["days"].get(label)
            each.append(day["profit"] if day else 0.0)
            if not day:
                continue
            for k in row:
                row[k] += day[k]
        sofar = any(books[a]["days"].get(label, {}).get("so far")
                    for a in ACCOUNTS)
        for k in grand:
            grand[k] += row[k]
        shown = label + (" (so far)" if sofar else "")
        margin = row["profit"] / row["revenue"] * 100 if row["revenue"] else None
        print(f"{shown:<26}{row['up']:>8.2f}h"
              + "".join(f"{short(v):>12}" for v in each)
              + f"{short(row['profit']):>13}"
              + f"{short(row['profit'] / row['up']) if row['up'] else '-':>12}"
              + f"{short(row['revenue']):>12}{short(row['cost']):>12}"
              + f"{short(row['units']):>10}"
              + (f"{margin:>8.1f}%" if margin is not None else f"{'--':>9}"))
    line("=", width=len(head))
    totals = [sum(books[a]["days"].get(label, {}).get("profit", 0.0)
                  for label in labels) for a in ACCOUNTS]
    print(f"{f'{len(labels)} DAYS':<26}{grand['up']:>8.2f}h"
          + "".join(f"{short(v):>12}" for v in totals)
          + f"{short(grand['profit']):>13}"
          + f"{short(grand['profit'] / grand['up']) if grand['up'] else '-':>12}"
          + f"{short(grand['revenue']):>12}{short(grand['cost']):>12}"
          + f"{short(grand['units']):>10}"
          + (f"{grand['profit'] / grand['revenue'] * 100:>8.1f}%"
             if grand["revenue"] else f"{'--':>9}"))
    print("")
    print("")
    parts = ["stock at its listed price", "bought since that board, at what it cost",
             "Alz, latest balance line", "NET WORTH"]
    head = f"{'account':<26}" + "".join(f"{p.split(',')[0]:>26}" for p in parts[:1]) \
        + f"{'bought since':>16}{'Alz':>16}{'NET WORTH':>16}"
    print("NET WORTH -- each account at the moment its report was written")
    print("")
    print(head)
    line(width=len(head))
    sums = {p: 0.0 for p in parts}
    for account in ACCOUNTS:
        worth = books[account]["worth"]
        for p in parts:
            sums[p] += worth.get(p, 0.0)
        print(f"{account:<26}{short(worth.get(parts[0], 0.0)):>26}"
              f"{short(worth.get(parts[1], 0.0)):>16}"
              f"{short(worth.get(parts[2], 0.0)):>16}"
              f"{short(worth.get(parts[3], 0.0)):>16}")
    line("=", width=len(head))
    print(f"{'TOGETHER':<26}{short(sums[parts[0]]):>26}{short(sums[parts[1]]):>16}"
          f"{short(sums[parts[2]]):>16}{short(sums[parts[3]]):>16}")


if __name__ == "__main__":
    main(sys.argv)
