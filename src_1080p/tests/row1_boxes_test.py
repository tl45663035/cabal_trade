import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_row1_boxes_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import contextlib
import io
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
import calibration
import get_price
import row_model

FX = HERE / "fixtures"
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


def frame(name):
    return Image.open(FX / name).convert("RGB")


set_row = frame("2026-09-27_005153_01524_click_1617_181.png")
saved = (calibration.click, calibration.park, calibration.grab, calibration.time.sleep)
calibration.click = lambda *a, **k: None
calibration.park = lambda *a, **k: None
calibration.grab = lambda *a, **k: set_row
calibration.time.sleep = lambda s: None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        measured = calibration.calibrate_purchase(calibration.load()["shop"], verbose=True)
finally:
    calibration.click, calibration.park, calibration.grab, calibration.time.sleep = saved
cols = measured["purchase_columns"]
name_qty_line, qty_price_line = 568, 627
check("the quantity box sits between the two divider lines",
      name_qty_line < cols["qty"][0] and cols["qty"][2] < qty_price_line, str(cols["qty"]))
check("the price box starts right of the quantity|price divider and no longer overlaps the quantity box",
      qty_price_line < cols["price"][0] and cols["qty"][2] < cols["price"][0], str(cols["price"]))
check("the name box ends left of the name|quantity divider", cols["name"][2] < name_qty_line, str(cols["name"]))

calibration.load()["shop"]["purchase_columns"] = cols
rows = {"2026-09-27_005153_01524_click_1617_181.png": (("Chaos Core Set X 18", 1, 13049333),
                                                       "the green 13,049,333 the whole-row read took as 13,049"),
        "2026-09-27_005153_01748_click_564_755.png": (("Chaos Core", 7, 719399),
                                                      "a quantity of 7 the whole-row read missed"),
        "2026-09-27_005153_04444_click_500_258.png": (("Chaos Core", 115, 718813),
                                                      "a quantity of 115 the digit-only read took as 5"),
        "2026-09-28_123134_00681_click_564_755.png": (("Chaos Core", 18, 711111),
                                                      "the 711,111 Tesseract took as 7, which stalled the Chaos Core lookups")}
fields = lambda got: (got["name"], got["qty"], got["price"])
for name, (want, why) in rows.items():
    seen = fields(get_price.read_fields(frame(name)))
    check(f"row 1 reads {want}: {why}", seen == want, str(seen))
check("PaddleOCR read all three boxes, not the Tesseract fallback",
      row_model._reader["proc"] is not None and not row_model._reader["failed"]
      and "tesseract" not in get_price._SEEN, str(row_model._reader["failed"]))

names = list(rows) * 3
with ThreadPoolExecutor(max_workers=len(rows)) as pool:
    together = list(pool.map(lambda n: fields(get_price.read_fields(frame(n))), names))
check("rows read at the same time each get their own name, quantity and price back",
      together == [rows[n][0] for n in names], str(together))

row_model._reader["failed"] = "PaddleOCR switched off by this test"
with contextlib.redirect_stdout(io.StringIO()) as said:
    get_price.read_fields(frame("2026-09-27_005153_04444_click_500_258.png"))
    got = fields(get_price.read_fields(frame("2026-09-27_005153_01748_click_564_755.png")))
check("without PaddleOCR the row falls back to Tesseract and still reads",
      got == ("Chaos Core", 7, 719399), str(got))
check("the fallback is announced once", said.getvalue().count("read by Tesseract instead") == 1,
      said.getvalue().strip())

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
