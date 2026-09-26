import contextlib
import io
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_relist_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
from PIL import Image
import calibration
import driver
import row_model

FX = HERE / "fixtures"
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


def one_by_one(image):
    return [word for word in (row_model.DISMISS_WORD, row_model.CONFIRM_WORD, row_model.RECEIPT_WORD)
            if row_model._dialog_button_seen(word, image)]


for name, want in (("00045_click_1101_634.png", [row_model.DISMISS_WORD, row_model.CONFIRM_WORD]),
                   ("00046_click_968_634.png", []), ("00088_click_833_752.png", [])):
    image = Image.open(FX / f"2026-09-26_195236_{name}").convert("RGB")
    t = time.perf_counter()
    single = one_by_one(image)
    single_ms = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    together = row_model.dialog_buttons(image)
    together_ms = (time.perf_counter() - t) * 1000
    check(f"dialog buttons in {name[:5]}: read together {together} matches one by one and what the frame shows",
          together == single == want, f"together {together_ms:.0f} ms, one by one {single_ms:.0f} ms")

calls = []
saved = (driver.special_names, driver.cash_item_of, driver.resupply_special, driver.back_to_the_shop,
         driver.register_tab)
driver.special_names = lambda: {row_model.item_key("Chaos Core")}
driver.cash_item_of = lambda name: None
driver.resupply_special = lambda *a, **k: calls.append("special row bought back")
driver.back_to_the_shop = lambda *a, **k: calls.append("reopen the shop") or True
driver.register_tab = lambda *a, **k: calls.append("Register tab")
model = row_model.RowModel().seed({})
try:
    with contextlib.redirect_stdout(io.StringIO()):
        driver.restock_now(model, "Chaos Core", 1, 30)
finally:
    (driver.special_names, driver.cash_item_of, driver.resupply_special, driver.back_to_the_shop,
     driver.register_tab) = saved
check("a special row that sold is bought back with one shop reopen and one Register tab click",
      calls == ["special row bought back", "reopen the shop", "Register tab"], str(calls))

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
