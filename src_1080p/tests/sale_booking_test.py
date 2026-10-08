import contextlib
import datetime
import io
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_booking_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC.parent / "tools"))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import get_alz
import ledger
import row_model as m

ledger.DB = Path(os.environ["CABAL_SALES_DB"])
ledger._RUN = None
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


def last_sale():
    with sqlite3.connect(ledger.DB) as db:
        return db.execute("SELECT item, price, proceeds, qty, cost FROM sales ORDER BY id DESC LIMIT 1").fetchone()


def other_form(name):
    return calibration.market_unit(
        calibration.FAVOURITE_ITEMS[str(calibration.pair_slot(calibration.favourite_slot_of(name)))])


market = other_form("Force Core(High)")
chaos_core = calibration.market_unit("Chaos Core")
voucher_share = calibration.price_floor("Yekaterina VIP Membership")[0]
check("a row bought this run costs what was paid",
      m.cost_basis(m.Row("Force Core(High", qty=180, price=172998, buy_cost=170000)) == 170000)
check("a Force Core(High) row the run started with costs the Force Core Set (High) start-up price",
      market > 0 and m.cost_basis(m.Row("Force Core(High", qty=180, price=172998)) == market, f"{market:,}")
check("a Chaos Core Set row the run started with costs the Chaos Core start-up price",
      chaos_core > 0 and m.cost_basis(m.Row("Chaos Core Set X 877", qty=1, price=653_365_000)) == chaos_core,
      f"{chaos_core:,}")
check("a Cash Shop row the run started with costs its share of the voucher",
      voucher_share > 0 and m.cost_basis(m.Row("Yekaterina VIP Membership", qty=1, price=124999998)) == voucher_share,
      f"{voucher_share:,}")
check("an item the run does not price has no cost of its own",
      m.cost_basis(m.Row("WEXP Saver", qty=3, price=5000000)) == 0)

balances = []
get_alz.read_balance = lambda *a, **k: balances.pop(0) if balances else None
board = m.RowModel().seed({})
before = 1_000_000_000
said = io.StringIO()
with contextlib.redirect_stdout(said):
    balances[:] = [before + 180 * 172998]
    board._book(9, m.Row("Force Core(High", qty=180, price=172998, buy_cost=170000), before,
                ("", m.REGISTER_WORD), True)
check("a full sale books every unit on the row, at its cost",
      last_sale() == ("Force Core(High", 172998, 180 * 172998, 180, 180 * 170000), str(last_sale()))

with contextlib.redirect_stdout(said):
    balances[:] = [before + 20 * 172998]
    board._book(10, m.Row("Force Core(High", qty=180, price=172998, buy_cost=170000), before,
                ("Force Core(High) 160 172,998 On Sale Change", m.CHANGE_WORD), False)
check("a partial sale books what left the row, 180 down to 160",
      last_sale() == ("Force Core(High", 172998, 20 * 172998, 20, 20 * 170000), str(last_sale()))

said = io.StringIO()
with contextlib.redirect_stdout(said):
    balances[:] = [before + 102_459_960]
    board._book(10, m.Row("Force Core(High", qty=180, price=172998, buy_cost=170000), before,
                ("Force Core(High) 160 172,998 On Sale Change", m.CHANGE_WORD), False)
check("the 23:03 case: a misread balance of 102,459,960 no longer books 592 units; the row count's 20 stand",
      last_sale()[2:4] == (3_459_960, 20) and "booked from the row count" in said.getvalue(),
      f"{last_sale()}; {said.getvalue().strip()[:120]}")

said = io.StringIO()
with contextlib.redirect_stdout(said):
    balances[:] = [before + 20 * 172998]
    board._book(10, m.Row("Force Core(High", qty=180, price=172998, buy_cost=170000), before,
                ("garbled", m.CHANGE_WORD), False)
check("when the row count does not read, the balance decides and the log says so",
      last_sale()[3] == 20 and "did not read" in said.getvalue(), said.getvalue().strip()[:120])

with contextlib.redirect_stdout(io.StringIO()):
    balances[:] = [before + 5 * 172998]
    board._book(3, m.Row("Force Core(High", qty=5, price=172998), before, ("", m.REGISTER_WORD), True)
check("a sale from stock the run started with is costed at the other form's start-up price",
      last_sale()[4] == 5 * market, str(last_sale()))

with contextlib.redirect_stdout(io.StringIO()):
    balances[:] = [before + 124999998]
    board._book(9, m.Row("Yekaterina VIP Membership", qty=1, price=124999998), before, ("", m.REGISTER_WORD), True)
check("a VIP the run started with is costed at its voucher share",
      last_sale()[4] == voucher_share, str(last_sale()))

said = io.StringIO()
with contextlib.redirect_stdout(said):
    balances[:] = [before + 3 * 5000000]
    board._book(25, m.Row("WEXP Saver", qty=3, price=5000000), before, ("", m.REGISTER_WORD), True)
check("an item the run does not price books its revenue as its cost, so no profit",
      last_sale()[2] == last_sale()[4] == 15_000_000 and "no profit is counted" in said.getvalue(),
      f"{last_sale()}; {said.getvalue().strip()[:120]}")

with contextlib.redirect_stdout(io.StringIO()):
    balances[:] = [before + 653_365_000]
    board._book(20, m.Row("Chaos Core Set X 877", qty=1, price=653_365_000, buy_cost=760725), before,
                ("", m.REGISTER_WORD), True)
check("a Set bundle books all its units at the cost per unit",
      last_sale() == ("Chaos Core Set X 877", 653_365_000 // 877, 653_365_000, 877, 877 * 760725), str(last_sale()))

saved_unit = calibration.market_unit
calibration.market_unit = lambda name: 499_798
try:
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        balances[:] = [before + 22 * 999_596]
        board._book(18, m.Row("Upgrade Core (Ultimate", qty=200, price=999_596, buy_cost=487_209), before,
                    ("Upgrade Core (Ultimate) 178 999,596 On Sale Change", m.CHANGE_WORD), False)
    check("the 18:00 case: 22 single cores sold at 999,596, twice the 499,798 market, book as 22 at 999,596",
          last_sale() == ("Upgrade Core (Ultimate", 999_596, 21_991_112, 22, 22 * 487_209)
          and "to a listing" not in said.getvalue() and "+512,387 a unit" in said.getvalue(),
          f"{last_sale()}; {said.getvalue().strip()[:160]}")

    calibration.market_unit = lambda name: 320_000
    with contextlib.redirect_stdout(io.StringIO()):
        balances[:] = [before + 1_279_996]
        board._book(1, m.Row("Force Core (Ultimate", qty=1, price=1_279_996, buy_cost=299_998), before,
                    ("", m.REGISTER_WORD), True)
    check("a single item sold at four times its market is still one unit, not a pack of four",
          last_sale() == ("Force Core (Ultimate", 1_279_996, 1_279_996, 1, 299_998), str(last_sale()))

    calibration.market_unit = lambda name: 722_970
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        balances[:] = [before + 180_019_778]
        board._book(27, m.Row("Chaos Core Set", qty=1, price=180_019_778, buy_cost=715_049), before,
                    ("", m.REGISTER_WORD), True)
    check("a Set whose size did not read still takes its units from the price: 249 at 722,970",
          last_sale() == ("Chaos Core Set", 722_970, 180_019_778, 249, 249 * 715_049)
          and "249 to a listing, 249 unit(s) at 722,970" in said.getvalue(),
          f"{last_sale()}; {said.getvalue().strip()[:160]}")
finally:
    calibration.market_unit = saved_unit

with contextlib.redirect_stdout(io.StringIO()):
    balances[:] = []
    board._book(4, m.Row("Force Core(High", qty=40, price=172998, buy_cost=170000), None,
                ("Force Core(High) 10 172,998 On Sale Change", m.CHANGE_WORD), False)
check("a balance that does not read no longer loses the sale: the row count books 30",
      last_sale()[3] == 30, str(last_sale()))

calls = []
saved = (m.RowModel._receive, m.RowModel.scroll_to, m.read_row_and_button, m.time.sleep)
m.RowModel._receive = lambda self, index, verbose=True, complete=False, again=False: calls.append("collect") or None
m.RowModel.scroll_to = lambda self, index, verbose=True: calls.append("scroll") or {"moved": False}
m.read_row_and_button = lambda seat=None: calls.append("read") or ("Force Core(High) 150 172,998 On Sale Change", m.CHANGE_WORD)
m.time.sleep = lambda s: calls.append(f"wait {s}")
try:
    board._slots[7] = m.Row("Force Core(High", qty=180, price=172998, buy_cost=170000)
    balances[:] = [before, before + 30 * 172998]
    with contextlib.redirect_stdout(io.StringIO()):
        after = board.receive(7, verbose=False, complete=False)
    check("a partial collection reads the row once and hands that read back to the caller",
          after == ("Force Core(High) 150 172,998 On Sale Change", m.CHANGE_WORD)
          and calls.count("read") == 1 and last_sale()[3] == 30, f"{calls}, {last_sale()}")
    calls.clear()
    balances[:] = [before, before + 30 * 172998]
    with contextlib.redirect_stdout(io.StringIO()):
        board.receive(7, verbose=False, complete=False, settle=True)
    check("the cancel's collection still waits its settle before that read",
          calls.index(f"wait {m.TAB_SETTLE}") < calls.index("read"), str(calls))
    calls.clear()
    del board._slots[7]
    balances[:] = [before, before + 180 * 172998]
    with contextlib.redirect_stdout(io.StringIO()):
        board.receive(7, verbose=False, complete=False,
                      listed=m.Row("Force Core(High", qty=180, price=172998, buy_cost=170000))
    check("a row that sold during its cancel is booked from the row the caller hands over, at the 180 the balance "
          "proves, not the 30 a stale read of the row says",
          last_sale()[3] == 180, str(last_sale()))
    balances[:] = [before, before + 30 * 172998]
    with contextlib.redirect_stdout(io.StringIO()):
        board.receive(7, verbose=False, complete=False,
                      listed=m.Row("Force Core(High", qty=180, price=172998, buy_cost=170000))
    check("and when the balance agrees with the row (30 sold), it books those 30",
          last_sale()[3] == 30, str(last_sale()))
finally:
    m.RowModel._receive, m.RowModel.scroll_to, m.read_row_and_button, m.time.sleep = saved

old = Path(tempfile.mkdtemp(prefix="cabal_old_ledger_")) / "sales.db"
with sqlite3.connect(old) as db:
    db.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, run TEXT, item TEXT NOT NULL, "
               "price INTEGER, proceeds INTEGER, qty INTEGER, note TEXT)")
    db.execute("INSERT INTO sales (at, run, item, price, proceeds, qty) VALUES ('2026-09-28T10:00:00', 'r', 'Chaos Core', 1, 1, 1)")
saved = (ledger.DB, ledger._RUN)
ledger.DB, ledger._RUN = old, None
try:
    ledger.start()
    with sqlite3.connect(old) as db:
        columns = [r[1] for r in db.execute("PRAGMA table_info(sales)")]
        kept = db.execute("SELECT count(*) FROM sales").fetchone()[0]
    check("an existing ledger gains the cost column and keeps its sales", "cost" in columns and kept == 1, str(columns))
finally:
    ledger.DB, ledger._RUN = saved

rows, _held = ledger.run_profit(ledger._RUN)
fch = next(r for r in rows if r["name"].startswith("Force Core(High"))
with sqlite3.connect(ledger.DB) as db:
    want = db.execute("SELECT sum(qty), sum(proceeds), sum(cost) FROM sales WHERE item LIKE 'Force Core(High%'").fetchone()
check("the end-of-run profit adds up the booked sales exactly",
      (fch["units"], round(fch["revenue"]), round(fch["cost"])) == want, f"{fch} against {want}")

import profit_summary as ps
ps.LEDGER = ledger.DB
buys, sells = ps.ledger_runs("2000-01-01")
booked = [s for run in sells.values() for s in run]
check("the profit summary reads each sale's booked cost",
      booked and all(s["cost"] is not None for s in booked), f"{len(booked)} sales")
with contextlib.redirect_stdout(io.StringIO()):
    runs = ps.close_runs(datetime.datetime(2000, 1, 1))
summary = [s for r in runs for s in r["sold"]]
with sqlite3.connect(ledger.DB) as db:
    total = db.execute("SELECT sum(proceeds), sum(cost), count(*) FROM sales").fetchone()
check("and counts every booked sale at its booked cost, even in a run with no log",
      (round(sum(s["revenue"] for s in summary)), round(sum(s["cost"] for s in summary)), len(summary)) == total,
      f"{len(summary)} of {total[2]}")

said = io.StringIO()
with contextlib.redirect_stdout(said):
    balances[:] = [before + 33_000_000]
    board._book(22, m.Row("Force Core(High", qty=1, price=165000, buy_cost=156125), before,
                ("", m.REGISTER_WORD), True)
check("a full sale the run had recorded as 1, where the balance rose exactly 200 x 165,000, books 200 (14:52 today)",
      last_sale() == ("Force Core(High", 165000, 33_000_000, 200, 200 * 156125)
      and "booked from the balance" in said.getvalue(), str(last_sale()))
with contextlib.redirect_stdout(io.StringIO()):
    balances[:] = [before + 5 * 2_315_951]
    board._book(14, m.Row("Chaos Core Set X 3", qty=1, price=2_315_951), before, ("", m.REGISTER_WORD), True)
check("a row of single Sets recorded as 1, where the balance rose exactly 5 x 2,315,951, books 5 Sets of 3 (15 units)",
      last_sale()[2:4] == (5 * 2_315_951, 15), str(last_sale()))
with contextlib.redirect_stdout(io.StringIO()):
    balances[:] = [before + 70 * 165000]
    board._book(21, m.Row("Force Core(High", qty=200, price=165000, buy_cost=156125), before,
                ("Force Core(High) 148 165,000 On Sale Change", m.CHANGE_WORD), False)
check("a partial sale whose count says 52 but whose balance rose exactly 70 x 165,000 books 70",
      last_sale() == ("Force Core(High", 165000, 70 * 165000, 70, 70 * 156125), str(last_sale()))
said = io.StringIO()
with contextlib.redirect_stdout(said):
    balances[:] = [before + 33_000_000 - 1_234]
    board._book(22, m.Row("Force Core(High", qty=1, price=165000, buy_cost=156125), before,
                ("", m.REGISTER_WORD), True)
check("a balance that is not an exact multiple of the price keeps the row count, as before",
      last_sale() == ("Force Core(High", 165000, 165000, 1, 156125)
      and "booked from the row count" in said.getvalue(), str(last_sale()))

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
