import collections
import datetime
import re
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().with_name("sales.db")

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS purchases (
           id    INTEGER PRIMARY KEY AUTOINCREMENT,
           at    TEXT    NOT NULL,
           run   TEXT,
           item  TEXT    NOT NULL,
           price INTEGER,
           spend INTEGER,
           qty   INTEGER,
           note  TEXT
       )""",
    """CREATE TABLE IF NOT EXISTS sales (
           id       INTEGER PRIMARY KEY AUTOINCREMENT,
           at       TEXT    NOT NULL,
           run      TEXT,
           item     TEXT    NOT NULL,
           price    INTEGER,
           proceeds INTEGER,
           qty      INTEGER,
           note     TEXT
       )""",
)

_RUN = None


def start(stamp=None):
    global _RUN
    _RUN = stamp or datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with sqlite3.connect(DB) as db:
        for statement in _SCHEMA:
            db.execute(statement)
    return _RUN


def _write(table, columns, values):
    if _RUN is None:
        start()
    at = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    names = ", ".join(("at", "run") + columns)
    holes = ", ".join("?" * (len(columns) + 2))
    with sqlite3.connect(DB) as db:
        db.execute(f"INSERT INTO {table} ({names}) VALUES ({holes})",
                   (at, _RUN) + values)


def bought(item, price, spend, qty, note=None):
    _write("purchases", ("item", "price", "spend", "qty", "note"),
           (item, int(price or 0), int(spend or 0), int(qty or 0), note))


def sold(item, price, proceeds, qty, note=None):
    _write("sales", ("item", "price", "proceeds", "qty", "note"),
           (item, int(price or 0), int(proceeds or 0), int(qty or 0), note))


_PACK = re.compile(r"\bX\s*[\d,]+", re.I)
_PACK_COUNT = re.compile(r"\bX\s*([\d,]+)", re.I)


def _pack(name):
    found = _PACK_COUNT.search(name or "")
    if not found:
        return 1
    try:
        return max(1, int(found.group(1).replace(",", "")))
    except ValueError:
        return 1


def _key(name):
    return re.sub(r"[^a-z]", "", _PACK.sub(" ", name or "").lower()).replace(
        "set", "")


def run_profit(run=None):
    run = run or _RUN
    if run is None or not DB.exists():
        return []
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    lots = collections.defaultdict(collections.deque)
    for item, spend, qty in db.execute(
            "SELECT item, spend, qty FROM purchases WHERE run=? ORDER BY at",
            (run,)):
        if qty:
            lots[_key(item)].append([qty, (spend or 0) / qty])
    rows = {}
    for item, proceeds, qty in db.execute(
            "SELECT item, proceeds, qty FROM sales WHERE run=? ORDER BY at",
            (run,)):
        left = (qty or 0) * _pack(item)
        if not left or not proceeds:
            continue
        each = proceeds / left
        key = _key(item)
        row = rows.setdefault(key, {"name": item, "units": 0, "revenue": 0.0,
                                    "cost": 0.0, "unmatched": 0})
        while left and lots[key]:
            lot = lots[key][0]
            take = min(left, lot[0])
            row["units"] += take
            row["revenue"] += take * each
            row["cost"] += take * lot[1]
            lot[0] -= take
            left -= take
            if lot[0] <= 0:
                lots[key].popleft()
        row["unmatched"] += left
    db.close()
    held = {k: (sum(l[0] for l in q), sum(l[0] * l[1] for l in q))
            for k, q in lots.items() if sum(l[0] for l in q)}
    return sorted(rows.values(), key=lambda r: -r["revenue"]), held


def print_run_profit(run=None):
    rows, held = run_profit(run) or ([], {})
    print("")
    print(f"PROFIT THIS RUN -- only cores this run both bought and sold")
    if not rows:
        print("  nothing this run bought has sold yet.")
    else:
        print(f"  {'item':<26}{'profit':>14}{'units':>8}{'margin':>8}"
              f"{'revenue':>15}{'cost':>15}")
        units = revenue = cost = 0
        for row in rows:
            if not row["units"]:
                continue
            gain = row["revenue"] - row["cost"]
            units += row["units"]
            revenue += row["revenue"]
            cost += row["cost"]
            print(f"  {row['name'][:25]:<26}{gain:>14,.0f}{row['units']:>8,}"
                  f"{100 * gain / row['revenue']:>7.1f}%"
                  f"{row['revenue']:>15,.0f}{row['cost']:>15,.0f}")
        gain = revenue - cost
        print(f"  {'-' * 84}")
        print(f"  {'TOTAL':<26}{gain:>14,.0f}{units:>8,}"
              f"{(100 * gain / revenue if revenue else 0):>7.1f}%"
              f"{revenue:>15,.0f}{cost:>15,.0f}")
    if held:
        print("  bought this run and still unsold:")
        for key, (units, value) in sorted(held.items(), key=lambda kv: -kv[1][1]):
            print(f"    {key:<28}{units:>8,} core(s){value:>16,.0f} Alz")
