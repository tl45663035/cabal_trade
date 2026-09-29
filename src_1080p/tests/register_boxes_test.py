import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_register_boxes_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
from PIL import Image
import calibration
import driver
import row_model as m

FX = HERE / "fixtures"
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


def frame(name):
    return Image.open(FX / name).convert("RGB")


board = frame("2026-09-28_142914_01994_click_833_752.png")
saved = (calibration.click, calibration.park, calibration.grab, calibration.time.sleep)
calibration.click = lambda *a, **k: None
calibration.park = lambda *a, **k: None
calibration.grab = lambda *a, **k: board
calibration.time.sleep = lambda s: None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        measured = calibration.calibrate_register_table(calibration.load()["shop"], verbose=True)
finally:
    calibration.click, calibration.park, calibration.grab, calibration.time.sleep = saved
columns = measured["register_columns"]
check("the launch measurement finds the Register table's column lines", columns is not None, str(columns))
dividers = [(526, 526), (579, 579), (716, 717), (783, 784)]
xs = columns["x"] if columns else {}
order = [xs.get(k) for k in calibration.REGISTER_COLUMNS]
check("every box sits between two lines with 3 clear pixels on each side",
      all(order) and all(order[i][1] < dividers[i][0] - calibration.REGISTER_BOX_MARGIN
                         and order[i + 1][0] > dividers[i][1] + calibration.REGISTER_BOX_MARGIN
                         for i in range(len(dividers))), str(order))
check("the button box stops short of the scroll bar and the name box starts inside the table",
      bool(order) and order[-1][1] < 874 and order[0][0] > 208, str(order))
check("the boxes stay inside the row's own lines", bool(columns) and 0 < columns["up"] < 32 and 0 < columns["down"] < 26,
      str(columns and (columns["up"], columns["down"])))

shop = calibration.load()["shop"]
shop.update({k: measured[k] for k in ("register_columns", "row_one_y", "row_last_y", "row_pitch", "table_x", "button_x")})


def read(name, seat, quiet=True):
    image = frame(name)
    calibration.grab = lambda *a, **k: image
    try:
        with contextlib.redirect_stdout(io.StringIO()) if quiet else contextlib.nullcontext():
            text = m.read_row(seat)
            return text, m.row_button(image, seat), driver._row_from(text)
    finally:
        calibration.grab = saved[2]


cases = [
    ("2026-09-28_142914_01994_click_833_752.png", m.FIRST_SEAT, "an empty slot is empty because its button says Register",
     lambda t, b, r: t == m.REGISTER_WORD and b == m.REGISTER_WORD and m.row_is_empty(t)),
    ("2026-09-28_142914_01994_click_833_752.png", m.LAST_SEAT, "Upgrade Core (Ultimate) x8 at 457,998 with Change",
     lambda t, b, r: r is not None and (r.name, r.qty, r.price) == ("Upgrade Core (Ultimate", 8, 457998)
     and b == m.CHANGE_WORD and not m.row_is_empty(t) and not m.row_complete(t)),
    ("2026-09-28_125720_02309_click_831_697.png", m.LAST_SEAT,
     "the VIP's name on two lines is read from its first line; Tesseract read it as 'je oad'",
     lambda t, b, r: r is not None and (r.name, r.qty, r.price) == ("Yekaterina VIP Membership", 1, 124999998)
     and b == m.CHANGE_WORD),
    ("2026-09-28_142914_00367_click_830_698.png", m.LAST_SEAT, "the sold VIP reads Complete with a Receive button",
     lambda t, b, r: r is not None and r.name == "Yekaterina VIP Membership" and r.price == 124999998
     and m.row_complete(t) and b == m.RECEIPT_WORD),
    ("2026-09-28_142914_02492_click_830_175.png", m.FIRST_SEAT, "a sold Chaos Core at 733,791 reads Complete with Receive",
     lambda t, b, r: r is not None and (r.name, r.price) == ("Chaos Core", 733791) and m.row_complete(t)
     and b == m.RECEIPT_WORD),
]
for name, seat, label, ok in cases:
    text, button, row = read(name, seat)
    check(label, ok(text, button, row), f"{text!r}, button {button!r}")
check("PaddleOCR did the reading, not the Tesseract backup",
      m._reader["proc"] is not None and not m._reader["failed"] and "rows" not in m._told, str(m._reader["failed"]))

calls = []
real_texts = m._backup_texts
m._backup_texts = lambda crops: calls.append(len(crops)) or real_texts(crops)
try:
    for name, seat in (("2026-09-28_142914_01994_click_833_752.png", m.LAST_SEAT),
                       ("2026-09-28_142914_02492_click_830_175.png", m.FIRST_SEAT),
                       ("2026-09-28_142914_01994_click_833_752.png", m.FIRST_SEAT)):
        image = frame(name)
        calibration.grab = lambda *a, **k: image
        calls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            together = m.read_row_and_button(seat)
        once = list(calls)
        with contextlib.redirect_stdout(io.StringIO()):
            apart = (m.read_row(seat), m.row_button(image, seat))
        check(f"the row and its button come from one PaddleOCR call ({name[18:23]}, {seat})",
              once == [5] and together == apart, f"calls {once}, together {together}, apart {apart}")
finally:
    m._backup_texts = real_texts
    calibration.grab = saved[2]

seeded = []
real_seed = (m.RowModel.scroll_to, m.RowModel.save, m.RowModel.home)
m.RowModel.scroll_to = lambda self, index, verbose=True: {"moved": False}
m.RowModel.save = lambda self: None
m.RowModel.home = lambda self, verbose=True: 1
image = frame("2026-09-28_142914_01994_click_833_752.png")
calibration.grab = lambda *a, **k: image
calls.clear()
m._backup_texts = lambda crops: calls.append(len(crops)) or real_texts(crops)
saved_register_tab = driver.register_tab
driver.register_tab = lambda verbose=True: None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        board_model = driver.seed(verbose=False)
finally:
    m._backup_texts = real_texts
    m.RowModel.scroll_to, m.RowModel.save, m.RowModel.home = real_seed
    driver.register_tab = saved_register_tab
    calibration.grab = saved[2]
check("the launch seed reads each row with one PaddleOCR call, not a button read and then a row read",
      calls and all(c == 5 for c in calls), f"calls {calls[:6]}")

m._reader["failed"] = "PaddleOCR switched off by this test"
said = io.StringIO()
with contextlib.redirect_stdout(said):
    text, button, row = read("2026-09-28_142914_01994_click_833_752.png", m.LAST_SEAT, quiet=False)
    read("2026-09-28_142914_01994_click_833_752.png", m.FIRST_SEAT, quiet=False)
check("without PaddleOCR, Tesseract still reads the row and its button",
      row is not None and (row.qty, row.price) == (8, 457998) and button == m.CHANGE_WORD, f"{text!r}, {button!r}")
check("and says so once", said.getvalue().count("read whole by Tesseract instead") == 1, said.getvalue().strip())
image = frame("2026-09-28_142914_01994_click_833_752.png")
calibration.grab = lambda *a, **k: image
with contextlib.redirect_stdout(io.StringIO()):
    seen, action = m.read_row_and_button(m.LAST_SEAT)
calibration.grab = saved[2]
check("and the row with its button falls back to the two Tesseract reads", action == m.CHANGE_WORD and "457,998" in seen,
      f"{seen!r}, {action!r}")

m._reader["failed"] = None
m._told.clear()
shop["register_columns"] = None
said = io.StringIO()
with contextlib.redirect_stdout(said):
    text, button, row = read("2026-09-28_142914_01994_click_833_752.png", m.LAST_SEAT, quiet=False)
check("with no measured columns the row is read whole by Tesseract, as before",
      row is not None and (row.qty, row.price) == (8, 457998) and "not measured" in said.getvalue(),
      f"{text!r}; {said.getvalue().strip()}")

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
