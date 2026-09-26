import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_cash0_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
import no_input
TRIPPED = no_input.arm()
from PIL import Image
import calibration
import cashshop

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


image = Image.open(HERE / "fixtures" / "2026-09-26_184309_00008_cash_balance_unread.png").convert("RGB")
calibration.grab = lambda *a, **k: image
t = time.perf_counter()
cash = calibration._await_cash(cashshop.read_cc)
ms = (time.perf_counter() - t) * 1000
check("a Cash balance of 0 is read as 0 without waiting out the timeout",
      cash == 0 and ms < calibration.DIALOG_TIMEOUT * 1000, f"{cash!r} in {ms:,.0f} ms")
check("the gem box still reads 160", calibration._await_cash(cashshop.read_gems) == 160)
t = time.perf_counter()
answer = calibration._await_cash(lambda: False, timeout=0.3)
ms = (time.perf_counter() - t) * 1000
check("a yes/no wait still waits while the answer is no", answer is None and ms >= 290, f"{answer!r} after {ms:.0f} ms")
check("a yes/no wait returns at once on yes", calibration._await_cash(lambda: True) is True)
check("a wait for a point still returns the point", calibration._await_cash(lambda: (1, 2)) == (1, 2))
check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
