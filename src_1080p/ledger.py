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
    """CREATE TABLE IF NOT EXISTS board (
           row      INTEGER PRIMARY KEY,
           at       TEXT    NOT NULL,
           item     TEXT    NOT NULL,
           qty      INTEGER,
           price    INTEGER,
           buy_cost INTEGER
       )""",
)

_RUN = None


def start(stamp=None):
    global _RUN
    _RUN = stamp or datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with sqlite3.connect(DB) as db:
        for statement in _SCHEMA:
            db.execute(statement)

        columns = {row[1] for row in db.execute("PRAGMA table_info(purchases)")}
        if "expect" not in columns:
            db.execute("ALTER TABLE purchases ADD COLUMN expect INTEGER")
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


def bought(item, price, spend, qty, note=None, expect=None):
    _write("purchases", ("item", "price", "spend", "qty", "note", "expect"),
           (item, int(price or 0), int(spend or 0), int(qty or 0), note,
            int(expect) if expect else None))


def sold(item, price, proceeds, qty, note=None):
    _write("sales", ("item", "price", "proceeds", "qty", "note"),
           (item, int(price or 0), int(proceeds or 0), int(qty or 0), note))


def board_save(rows):
    if _RUN is None:
        start()
    at = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with sqlite3.connect(DB) as db:
        db.execute("DELETE FROM board")
        db.executemany(
            "INSERT INTO board (row, at, item, qty, price, buy_cost) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(int(index), at, row.name, int(row.qty), int(row.price),
              int(row.buy_cost)) for index, row in rows.items()])


def board_costs():
    if _RUN is None:
        start()
    with sqlite3.connect(DB) as db:
        return {int(row): (item, int(cost or 0)) for row, item, cost
                in db.execute("SELECT row, item, buy_cost FROM board")}


_PACK = re.compile(r"\bX\s*[\d,]+", re.I)


def _key(name):
    return re.sub(r"[^a-z]", "", _PACK.sub(" ", name or "").lower()).replace(
        "set", "")


def run_profit(run=None):
    run = run or _RUN
    if run is None or not DB.exists():
        return [], {}
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    columns = {row[1] for row in db.execute("PRAGMA table_info(purchases)")}
    expect_col = "expect" if "expect" in columns else "NULL"
    lots = collections.defaultdict(collections.deque)
    for item, spend, qty, expect in db.execute(
            f"SELECT item, spend, qty, {expect_col} FROM purchases "
            f"WHERE run=? ORDER BY at, id", (run,)):
        if qty:
            cost = (spend or 0) / qty
            lots[_key(item)].append([qty, cost, expect or cost])
    rows = {}
    for item, proceeds, qty in db.execute(
            "SELECT item, proceeds, qty FROM sales WHERE run=? ORDER BY at, id",
            (run,)):
        left = qty or 0
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
    held = {}
    for key, dq in lots.items():
        units = sum(l[0] for l in dq)
        if units:
            held[key] = (units, sum(l[0] * l[1] for l in dq),
                         sum(l[0] * l[2] for l in dq))
    return sorted(rows.values(), key=lambda r: -r["revenue"]), held


def print_run_profit(run=None):
    rows, held = run_profit(run)
    print("")
    print("PROFIT THIS RUN -- what this run bought: sold lots at what they "
          "made, unsold lots at the price they were bought against")
    units = revenue = cost = 0
    if not rows:
        print("  nothing this run bought has sold yet.")
    else:
        print(f"  {'item':<26}{'profit':>14}{'units':>8}{'margin':>8}"
              f"{'revenue':>15}{'cost':>15}")
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
        print(f"  {'-' * 84}")
        gain = revenue - cost
        print(f"  {'SOLD':<26}{gain:>14,.0f}{units:>8,}"
              f"{(100 * gain / revenue if revenue else 0):>7.1f}%"
              f"{revenue:>15,.0f}{cost:>15,.0f}")
    realised = revenue - cost
    assumed = 0.0
    if held:
        print("  bought this run and still unsold, taken as sold at the price "
              "each lot was bought against:")
        for key, (n, paid, expected) in sorted(
                held.items(), key=lambda kv: -kv[1][1]):
            print(f"    {key:<28}{n:>8,} core(s){paid:>16,.0f} Alz paid"
                  f"{expected - paid:>+14,.0f}")
            assumed += expected - paid
    print(f"  {'-' * 84}")
    print(f"  {'RUN CLOSED':<26}{realised + assumed:>14,.0f}"
          f"   ({realised:,.0f} realised, {assumed:+,.0f} assumed)")
