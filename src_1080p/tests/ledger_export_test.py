import csv
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ledger_export as export

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


work = Path(tempfile.mkdtemp(prefix="cabal_ledger_export_"))
db = work / "sales.db"
con = sqlite3.connect(db)
con.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, run TEXT, item TEXT NOT NULL, "
            "price INTEGER, proceeds INTEGER, qty INTEGER, note TEXT, cost INTEGER)")
con.execute("CREATE TABLE purchases (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, run TEXT, item TEXT NOT NULL, "
            "price INTEGER, spend INTEGER, qty INTEGER, note TEXT, expect INTEGER)")
con.execute("CREATE TABLE board (row INTEGER PRIMARY KEY, at TEXT NOT NULL, item TEXT NOT NULL, qty INTEGER, "
            "price INTEGER, buy_cost INTEGER, floor_at INTEGER)")
con.executemany("INSERT INTO sales (at, run, item, price, proceeds, qty, note, cost) VALUES (?,?,?,?,?,?,?,?)",
                [("2026-10-01T00:00:28", "r1", "Chaos Core Set X 249", 774711, 192903287, 249, None, 185142954),
                 ("2026-10-01T00:00:40", "r1", "Siena's Unbinding Stone, \"gold\"", 98799999, 98799999, 1, "a, note", None)])
con.execute("INSERT INTO purchases (at, run, item, price, spend, qty, note, expect) VALUES (?,?,?,?,?,?,?,?)",
            ("2026-10-01T00:00:03", "r1", "Chaos Core", 744089, 744089, 1, None, None))
con.execute("INSERT INTO board VALUES (2, '2026-10-01T16:22:04', 'Force Core(High', 35, 165000, 0, 0)")
con.execute("INSERT INTO board VALUES (1, '2026-10-01T16:22:04', 'Siena''s Unbinding Stone', 1, 98799995, 75111111, 75111111)")
con.commit()
con.close()

export.LEDGER = db
copy = export.snapshot()
out = work / "bot" / "ledger"
out.mkdir(parents=True)
written = [export.write(copy, table, out) for table in ("sales", "purchases", "board")]
check("one file per table", [p.name for p in written] == ["sales.csv", "purchases.csv", "board.csv"])
sales = list(csv.reader(open(out / "sales.csv", encoding="utf-8", newline="")))
check("every column of the table, in the table's order", sales[0] == ["id", "at", "run", "item", "price", "proceeds",
                                                                       "qty", "note", "cost"], str(sales[0]))
check("every row, in id order", [r[0] for r in sales[1:]] == ["1", "2"])
check("commas and quotes in a name survive", sales[2][3] == "Siena's Unbinding Stone, \"gold\"" and sales[2][7] == "a, note",
      str(sales[2]))
check("an empty cost stays empty", sales[2][8] == "", str(sales[2]))
board = list(csv.reader(open(out / "board.csv", encoding="utf-8", newline="")))
check("the board in row order", [r[0] for r in board[1:]] == ["1", "2"], str(board))
check("nothing left half-written", not list(out.glob("*.tmp")))
check("the live ledger was only read", sqlite3.connect(db).execute("SELECT count(*) FROM sales").fetchone()[0] == 2)
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
