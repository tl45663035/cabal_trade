import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC.parent / "tools"))
sys.argv = ["networth_board_test.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import networth

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


HEAD = "                                                   bought/u   listed/u  margin      row price"
ROWS = [
    "     1  Siena's Unbinding Stone            x1    75,111,111 98,799,994  +24.0%     98,799,994",
    "     2  Force Core (Ultimate               x2       328,460    328,443   -0.0%        328,443",
    "     3  Chaos Core                         x1       720,868    720,192   -0.1%        720,192",
    "     4  Chaos Core Set X 327               x1       723,539    776,713   +6.8%    253,985,477",
    "     5  Potion of Honor                    x1             -  7,250,000       -      7,250,000",
]
PROFIT = ["      23,688,883", "             -34 <- here", "            -676", "      17,387,898", "               -"]
run_log = "\n".join(["counting only rows 1-30", "  board after pass 1:", HEAD] + ROWS + ["  balance now 1,000,000,000", ""])
trace = "\n".join(["  board during pass 2, at row 2 of 1-30, read 2026-10-02T02:01:44", HEAD + "  profit if sold"]
                  + [row + profit for row, profit in zip(ROWS, PROFIT)] + [""])
work = Path(tempfile.mkdtemp(prefix="cabal_networth_board_"))
log = work / "2026-10-02_000000_run.log"
log.write_text(run_log, encoding="utf-8")

_, after, _, _, _ = networth.read(log)
_, live, _, _, _ = networth.read(log, trace)
check("the end-of-pass board reads every row", [r[0] for r in after] == [1, 2, 3, 4, 5], str([r[0] for r in after]))
check("the live trace reads every row, a loss in the profit column included",
      [r[0] for r in live] == [1, 2, 3, 4, 5], str([r[0] for r in live]))
check("the row being walked, marked here, is read", any(r[0] == 2 and r[2] == 2 for r in live))
check("both reads value the stock alike",
      sum(networth.row_worth(r[2], r[3], r[4]) for r in live)
      == sum(networth.row_worth(r[2], r[3], r[4]) for r in after)
      == 98_799_994 + 2 * 328_443 + 720_192 + 253_985_477 + 7_250_000)
import profit_summary

fresh = "    23  Chaos Core Set                     x1       710,579    723,728   +1.8%    115,072,910       2,090,775"
named = fresh.replace("Chaos Core Set    ", "Chaos Core Set X 159")
costed = [profit_summary.row_cost(profit_summary.raw_margin(line)) for line in (fresh, named)]
check("a Set just crafted, named without its size until the row is read back, costs its 159 units, as named",
      all(" 112,982,061 " in line for line in costed), " | ".join(c.strip()[:110] for c in costed))
_, fresh_rows, _, _, _ = networth.read(log, "\n".join([trace.splitlines()[0], HEAD + "  profit if sold", fresh, ""]))
units = [(name, networth.row_units(name, qty, each, listed)) for _, name, qty, each, listed, _ in fresh_rows]
check("the net worth tool counts the same 159 units for it", units == [("Chaos Core Set", 159)], str(units))
check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
