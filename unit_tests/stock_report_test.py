import os
import pathlib
import sys
import tempfile

_DB = pathlib.Path(tempfile.gettempdir()) / f"stock_report_test_{os.getpid()}.db"
if _DB.exists():
    _DB.unlink()
os.environ["CABAL_SALES_DB"] = str(_DB)

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


check(str(m.SALES_DB) == str(_DB),
      f"must run against its own database, got {m.SALES_DB}")


def reset():
    conn = m.sales_db()
    conn.execute("DELETE FROM purchases")
    conn.execute("DELETE FROM sales")
    conn.commit()
    conn.close()


def buy(item, price, qty, spend=None):
    conn = m.sales_db()
    conn.execute("INSERT INTO purchases (at, run, item, price, spend, qty) "
                 "VALUES ('now','t',?,?,?,?)",
                 (item, price, spend if spend is not None else price * qty, qty))
    conn.commit()
    conn.close()


def sell(item, proceeds, qty):
    conn = m.sales_db()
    conn.execute("INSERT INTO sales (at, run, item, price, proceeds, qty) "
                 "VALUES ('now','t',?,?,?,?)", (item, 0, proceeds, qty))
    conn.commit()
    conn.close()


check(m.sells_as("Force Core Set (Highest)") == "Force Core(Highest)",
      f"a bought Set is sold as its Core, got "
      f"{m.sells_as('Force Core Set (Highest)')!r}")
check(m.sells_as("Chaos Core") == "Chaos Core Set",
      f"a bought Chaos CORE is sold as a Chaos Core SET -- the trade runs the "
      f"other way round. got {m.sells_as('Chaos Core')!r}")
check(m.sells_as("Chaos Core Set") != "Chaos Core",
      "and the Set must not be mapped back onto the Core, which would pair "
      "the chaos purchase with the thing it was made from")

marked = {m.sells_as(f"Upgrade Core Set (Highest) X {n}") for n in (4, 170, 458)}
check(len(marked) == 1,
      f"pack markers must be stripped before grouping, got {marked}")
check(not any(ch.isdigit() for ch in next(iter(marked))),
      f"and the surviving name must carry no quantity, got {marked}")

reset()
buy("Force Core Set (Highest)", 200_000, 100)
buy("Force Core Set (Highest)", 190_000, 100)
sell("Force Core(Highest)", 60_000_000, 60)

out = m.bought_stock_report()
check("Force Core(Highest)" in out,
      "the item is listed under the name it SELLS as, which is what the shop "
      "board and the sales ledger both show")
check("200" in out.replace(",", ""),
      f"200 bought must appear, got:\\n{out}")
check("195,000" in out,
      f"the average of 200,000 and 190,000 over equal quantities is 195,000; "
      f"got:\\n{out}")

reset()
buy("Force Core Set (Highest)", 0, 3, spend=100)
out = m.bought_stock_report()
check("34" in out,
      f"100 over 3 units must round UP to 34, not down to 33; got:\\n{out}")

reset()
buy("Force Core Set (Highest)", 100_000, 10)
sell("Force Core(Highest)", 3_000_000, 20)

out = m.bought_stock_report()
check("1,500,000" in out,
      f"takings must be apportioned to the COVERED units only -- claiming all "
      f"3,000,000 against the cost of just 10 units invents profit out of "
      f"stock that predates the ledger. got:\\n{out}")
check("1,000,000" in out,
      f"and the cost is the covered units', got:\\n{out}")
check("+500,000" in out,
      f"so profit is 1,500,000 - 1,000,000; got:\\n{out}")

check("UNITS SOLD OF THOSE" in out,
      f"part 2 must state how many of the BOUGHT units sold; got:\\n{out}")
part2 = out.split("2. PROFIT")[1].split("3. PRE-EXISTING")[0]
check("20" not in part2.replace("1,000,000", "").replace("+500,000", ""),
      f"the 20 units SOLD must not appear in the resupply figures -- only the "
      f"10 that were bought. got:\\n{part2}")

check("3. PRE-EXISTING STOCK" in out,
      f"sales with no purchase behind them need their own section; got:\\n{out}")
part3 = out.split("3. PRE-EXISTING")[1]
check("Force Core(Highest)" in part3,
      f"the uncovered units appear there under the name they sold as; "
      f"got:\\n{part3}")
check("10" in part3 and "1,500,000" in part3,
      f"with the 10 surplus units and the takings that were NOT counted as "
      f"profit; got:\\n{part3}")
check("no profit is claimed" in part3,
      "and it must say plainly that no profit is claimed for them, or the "
      "takings read as though they were earnings")

check("+500,000" in out and "1,500,000" in part3,
      "the 3,000,000 of takings splits into 1,500,000 counted against cost "
      "and 1,500,000 reported separately -- neither dropped nor double-counted")

reset()
buy("Upgrade Core Set (Highest) X 170", 88_000, 170)
sell("Upgrade Core(Highest)", 20_000_000, 100)
out = m.bought_stock_report()
check(m.sells_as("Upgrade Core Set (Highest)") == "Upgrade Core (Highest)",
      f"a retired Set must still resolve to its Core by name, got "
      f"{m.sells_as('Upgrade Core Set (Highest)')!r}")
resupply = out.split("2. PROFIT")[0]
check("100" in resupply,
      f"and its sales must be paired with its purchases rather than showing "
      f"as unsold stock; got:\\n{resupply}")
check("3. PRE-EXISTING" not in out,
      f"with the pairing working there is no orphan section at all; got:\\n{out}")

reset()
buy("Chaos Core", 680_000, 100)
out = m.bought_stock_report()
check("Chaos Core Set" in out,
      f"a chaos purchase appears under the Set it becomes; got:\\n{out}")
check("no profit to report" in out.lower(),
      f"stock that has not sold yet is stock, not a loss -- the report must "
      f"say so rather than print a large negative profit. got:\\n{out}")
check("100" in out, "and the units bought are still shown")

reset()
check(m.bought_stock_report() == "",
      "a ledger with no purchases produces no section, rather than an empty "
      "table with zeroes that reads like a real result")

_saved = m.sales_db
try:
    m.sales_db = lambda: None
    check(m.bought_stock_report() == "",
          "with no database reachable the report is absent, not an exception: "
          "this runs in the shutdown path, where raising would cost the "
          "duration line every post-mortem starts from")
finally:
    m.sales_db = _saved

try:
    _DB.unlink()
except OSError:
    pass

print(f"stock_report_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
