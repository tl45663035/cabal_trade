import contextlib
import inspect
import io
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import row_model as m

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


def agreed(asked, filled, net, average, listed_at=None):
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        value = m._agreed(asked, filled, average, listed_at, True, net)
    return value, said.getvalue()


everything = []
value, said = agreed(1_000_000, 1_000_000, 1_000_000, 195_232)
everything.append(said)
check("the 21:36 Force Core(Highest) read, 1,000,000 in all three places against a 195,232 week, is taken",
      value == 1_000_000, said.strip().splitlines()[-1])
value, said = agreed(999_998, 999_998, 999_998, 195_232, 1_000_000)
everything.append(said)
check("the 22:54 row 14 read, 999,998 against a 195,232 week and listed at 1,000,000, is taken",
      value == 999_998, said.strip().splitlines()[-1])
value, said = agreed(30_000, 30_000, 30_000, 195_232)
everything.append(said)
check("a read far under the week average with the three places agreeing is taken", value == 30_000,
      said.strip().splitlines()[-1])
value, said = agreed(190_000, 190_000, 190_000, 195_232, 191_000)
everything.append(said)
check("a read near the week average is taken as before", value == 190_000, said.strip().splitlines()[-1])
value, said = agreed(196_799, 196_794, 196_799, 200_700)
everything.append(said)
check("the three places disagreeing, 196,799 against 196,794 on 23 Sept, is still refused",
      value is None and "the price places must agree" in said, said.strip().splitlines()[-1])
value, said = agreed(14_612_004, 14_612_004, 14_612_004, 146_826_900, 146_121_029)
everything.append(said)
check("the 8 Oct read a digit short, 14,612,004 against a row listed at 146,121,029, is still refused by the split",
      value is None and "the witnesses split" in said, said.strip().splitlines()[-1])
check("no read is refused for being far from the week average any more",
      not any("too far apart" in s for s in everything))
check("today's cost exception is gone with the check",
      all("cost" not in inspect.signature(f).parameters
          for f in (m._agreed, m._read_and_agree, m.suggested_price)))

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
