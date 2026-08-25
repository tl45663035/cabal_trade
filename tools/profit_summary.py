import collections
import pathlib
import datetime
import re
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent
LEDGERS = (("trade.py", ROOT / "sales.db", True),
           ("src", ROOT / "src" / "sales.db", False))
PACK = re.compile(r"\bX\s*[\d,]+", re.I)


def key(name):
    stripped = PACK.sub(" ", name or "")
    return re.sub(r"[^a-z]", "", stripped.lower()).replace("set", "")


def pack(name):
    found = PACK.findall(name or "")
    if not found:
        return 1
    return max(1, int(re.sub(r"[^\d]", "", found[-1])))


def bucket(name):
    return "Chaos" if "chaos" in (name or "").lower() else "Cores"


def since_midnight():
    return datetime.datetime.now().replace(hour=0, minute=0, second=0,
                                           microsecond=0)


def gather(cursor, start):
    lots = collections.defaultdict(collections.deque)
    for item, spend, qty in cursor.execute(
            "SELECT item, spend, qty FROM purchases WHERE run>=? ORDER BY at",
            (start,)):
        if qty:
            lots[key(item)].append([qty, (spend or 0) / qty])
    return lots


def match(cursor, start, lots, listed_in_packs):
    per_item = {}
    per_run = {}
    for run, item, qty, price, proceeds in cursor.execute(
            "SELECT run, item, qty, price, proceeds FROM sales "
            "WHERE run>=? ORDER BY at", (start,)):
        units = (qty or 0) * (pack(item) if listed_in_packs else 1)
        if not units:
            continue
        gross = proceeds if proceeds is not None else (price or 0) * (qty or 0)
        if not gross:
            continue
        each = gross / units
        k = key(item)
        row = per_item.setdefault(
            k, {"bucket": bucket(item), "units": 0, "revenue": 0.0,
                "cost": 0.0, "unmatched": 0})
        tally = per_run.setdefault(run, {"units": 0, "profit": 0.0})
        left = units
        while left and lots[k]:
            lot = lots[k][0]
            take = min(left, lot[0])
            gain = take * (each - lot[1])
            row["units"] += take
            row["revenue"] += take * each
            row["cost"] += take * lot[1]
            tally["units"] += take
            tally["profit"] += gain
            lot[0] -= take
            left -= take
            if lot[0] <= 0:
                lots[k].popleft()
        row["unmatched"] += left
    return per_item, per_run


def line(char="-", width=87):
    print(char * width)


def main():
    start = since_midnight()
    stamp = start.strftime("%Y-%m-%dT%H:%M:%S")
    now = datetime.datetime.now().strftime("%H:%M")
    print(f"PROFIT SUMMARY -- runs launched since {start:%Y-%m-%d} 00:00 "
          f"(as of {now})")
    print("only units the script both bought and sold in those runs, "
          "matched oldest purchase first")
    every = []
    for label, path, listed_in_packs in LEDGERS:
        if not path.exists():
            print("")
            print(f"{label}: no ledger at {path}")
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        lots = gather(conn, stamp)
        per_item, per_run = match(conn, stamp, lots, listed_in_packs)
        conn.close()
        print("")
        print(f"{label} -- {path}")
        every.append(report(per_item, per_run, lots))
    if len(every) > 1:
        print("")
        line("=")
        units = sum(g[0] for g in every)
        revenue = sum(g[1] for g in every)
        cost = sum(g[2] for g in every)
        profit = revenue - cost
        margin = f"{100 * profit / revenue:>7.1f}%" if revenue else f"{'--':>8}"
        print(f"{'BOTH LEDGERS':<26}{profit:>15,.0f}{units:>8,}{margin}"
              f"{revenue:>16,.0f}{cost:>16,.0f}")


def report(per_item, per_run, lots):
    print(f"{'item':<26}{'profit':>15}{'units':>8}{'margin':>8}"
          f"{'revenue':>16}{'cost':>16}")
    line()

    groups = {"Cores": [0, 0.0, 0.0], "Chaos": [0, 0.0, 0.0]}
    for k, row in sorted(per_item.items(), key=lambda kv: -kv[1]["revenue"]):
        if not row["units"]:
            continue
        profit = row["revenue"] - row["cost"]
        g = groups[row["bucket"]]
        g[0] += row["units"]
        g[1] += row["revenue"]
        g[2] += row["cost"]
        print(f"{k[:25]:<26}{profit:>15,.0f}{row['units']:>8,}"
              f"{100 * profit / row['revenue']:>7.1f}%"
              f"{row['revenue']:>16,.0f}{row['cost']:>16,.0f}")
    line()
    for label in ("Cores", "Chaos"):
        units, revenue, cost = groups[label]
        if not units:
            continue
        profit = revenue - cost
        print(f"{label:<26}{profit:>15,.0f}{units:>8,}"
              f"{100 * profit / revenue:>7.1f}%"
              f"{revenue:>16,.0f}{cost:>16,.0f}")
    line("=")
    units = sum(g[0] for g in groups.values())
    revenue = sum(g[1] for g in groups.values())
    cost = sum(g[2] for g in groups.values())
    profit = revenue - cost
    margin = f"{100 * profit / revenue:>7.1f}%" if revenue else f"{'--':>8}"
    print(f"{'TOTAL':<26}{profit:>15,.0f}{units:>8,}{margin}"
          f"{revenue:>16,.0f}{cost:>16,.0f}")
    totals = (units, revenue, cost)

    if per_run:
        print("")
        print("by run:")
        for run, tally in sorted(per_run.items()):
            print(f"  {run}   {tally['units']:>7,} units"
                  f"{tally['profit']:>16,.0f} Alz")

    skipped = {k: r["unmatched"] for k, r in per_item.items()
               if r["unmatched"]}
    if skipped:
        print("")
        print("sold by those runs but not bought by them, so left out:")
        for k, units in sorted(skipped.items(), key=lambda kv: -kv[1]):
            print(f"  {k:<26}{units:>8,} units")

    held = {k: (sum(l[0] for l in dq), sum(l[0] * l[1] for l in dq))
            for k, dq in lots.items() if sum(l[0] for l in dq)}
    if held:
        print("")
        print("bought by those runs and still unsold:")
        for k, (units, value) in sorted(held.items(), key=lambda kv: -kv[1][1]):
            print(f"  {k:<26}{units:>8,} units{value:>18,.0f} Alz")
        print(f"  {'':<26}{'':>8} {'':>17}{sum(v for _, v in held.values()):>17,.0f} Alz tied up")

    return totals


if __name__ == "__main__":
    main()
