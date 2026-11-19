import datetime
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
WORK = Path(tempfile.mkdtemp(prefix="cabal_special_tally_"))
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(WORK / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC.parent / "tools"))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import ledger
import profit_summary as ps

ledger.DB = WORK / "sales.db"
ledger._RUN = None
ledger.start()
ps.LEDGER = ledger.DB
ps.LOGS = WORK
ps.shift = lambda: datetime.timedelta(0)
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


now = datetime.datetime.now().replace(microsecond=0)
SPECIAL_RUN = now - datetime.timedelta(minutes=3)
PLAIN_RUN = now - datetime.timedelta(minutes=9)
LISTED = "  the Chaos Core special row is listed at 516,639 in row 10\n"
RELISTED = "    row 10 is the Chaos Core special row; listing 7,372 under the market, no floor\n"


def log_of(run):
    return WORK / f"{run:%Y-%m-%d_%H%M%S}_run.log"


def sale(run, item, qty, price, each_cost):
    with sqlite3.connect(ledger.DB) as db:
        db.execute("INSERT INTO sales (at, run, item, price, proceeds, qty, cost) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   ((now - datetime.timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S"),
                    run.strftime("%Y-%m-%dT%H:%M:%S"), item, price, price * qty, qty, each_cost * qty))


def report():
    book = ps.open_book()
    return ps.caught(lambda: ps.report_day(book)), ps.caught(lambda: ps.by_day(book))


def row(text, start):
    return next((line for line in text.splitlines() if line.startswith(start)), "")


log_of(SPECIAL_RUN).write_text(LISTED, encoding="utf-8")
log_of(PLAIN_RUN).write_text("  an ordinary run\n", encoding="utf-8")
sale(SPECIAL_RUN, "Chaos Core", 5, 516_639, 524_000)
sale(SPECIAL_RUN, "Chaos Core", 12, 519_000, 526_000)
sale(SPECIAL_RUN, "Chaos Core Set X 3", 3, 530_000, 520_000)
sale(SPECIAL_RUN, "Force Core(High", 6, 224_839, 210_000)
sale(PLAIN_RUN, "Chaos Core", 4, 574_290, 574_290)

day, week = report()
line = row(day, "Special row (expected)")
check("the day has one special row line, adding up both special sales",
      all(part in line for part in ("-120,805", "8,811,195", "8,932,000")) and line.split()[6] == "17", line)
check("it shows what a core cost and what it sold for", "525,412" in line and "518,306" in line, line)
check("the numbers appear once, not again as an item line", day.lower().count("special row") == 1, day)
chaos = row(day, "Chaos ")
check("the Chaos line keeps the Sets and a raw sale from a run with no special row, and none of the special sales",
      all(part in chaos for part in ("30,000", "3,887,160", "3,857,160")), chaos)
check("the Chaos Core item line matches it", all(part in row(day, "chaoscore") for part in ("30,000", "3,887,160")),
      row(day, "chaoscore"))
check("Force Core is left alone", "1,349,034" in row(day, "Cores"), row(day, "Cores"))
total = row(day, "TOTAL")
check("the total still counts everything, so no profit is hidden",
      all(part in total for part in ("-1,771", "14,047,389", "14,049,160")), total)
order = [next(i for i, text in enumerate(day.splitlines()) if text.startswith(name))
         for name in ("Chaos ", "Special row (expected)", "TOTAL")]
check("the special row sits between the groups and the total", order == sorted(order), str(order))
tally = row(week, "incl. special row losses")
check("the 7-day block tallies it under its total",
      all(part in tally for part in ("-120,805", "8,811,195", "8,932,000")), tally)
check("the 7-day total still includes it", "-1,771" in row(week, "7 DAYS"), row(week, "7 DAYS"))
check("the short report keeps the line", "Special row (expected)" in ps.tighten(day), "")

log_of(SPECIAL_RUN).write_text(RELISTED, encoding="utf-8")
day, week = report()
check("a run that only relisted a special row counts too", "-120,805" in row(day, "Special row (expected)"),
      row(day, "Special row (expected)"))

log_of(SPECIAL_RUN).write_text("  an ordinary run\n", encoding="utf-8")
day, week = report()
line = row(day, "Special row (expected)")
check("with no special row listed the line stays, at zero", line.split()[3:7] == ["0", "0", "0", "0"], line)
check("and every raw core sale stays in Chaos", "12,698,355" in row(day, "Chaos "), row(day, "Chaos "))
check("the 7-day tally reads zero too", row(week, "incl. special row losses").split()[4:8] == ["0", "0", "0", "0"],
      row(week, "incl. special row losses"))
check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
