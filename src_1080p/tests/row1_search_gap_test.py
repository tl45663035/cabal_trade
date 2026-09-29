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
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_row1_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
import no_input
TRIPPED = no_input.arm()
from PIL import Image
import calibration
import get_price

FX = HERE / "fixtures"
FAKE_CLICKS = []
get_price.shop.click = lambda x, y, settle=None: FAKE_CLICKS.append((x, y))
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


buy_row = Image.open(FX / "2026-09-26_184309_01349_click_500_258.png").convert("RGB")
calls = []
real = {"trade": calibration._trade_window_open, "tab": calibration.purchase_tab_showing}
calibration._trade_window_open = lambda image=None: calls.append("trade window check") or real["trade"](image)
calibration.purchase_tab_showing = lambda image=None: calls.append("Purchase tab check") or real["tab"](image)
reads = {"fields": get_price.read_fields, "line": calibration.read_line}
get_price.read_fields = lambda image=None: calls.append("row 1 read") or reads["fields"](image)
calibration.read_line = lambda image, box, *a, **k: (
    calls.append("whole row 1 read by Tesseract") if tuple(box) == get_price.purchase_row_one_box() else None
) or reads["line"](image, box, *a, **k)
get_price.inv.focus_game = lambda *a, **k: True


def run(image, slot, search=False):
    calls.clear()
    calibration.grab = lambda *a, **k: image
    calibration.steps_reset()
    t = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        out = get_price.get_price(slot, verbose=False, search=search)
    return out, (time.perf_counter() - t) * 1000, list(calls)


in_order = ["trade window check", "Purchase tab check", "row 1 read"]
out, ms, seen = run(buy_row, 3)
check("reading row 1 where it stands: the Trade window and the Purchase tab are checked first, then row 1 is read once",
      out is not None and (out["name"], out["qty"], out["price"]) == ("Chaos Core", 35, 709999) and seen == in_order,
      f"{ms:.0f} ms, order {seen}, {out and (out['name'], out['qty'], out['price'])}")
check("row 1 in place is not read a second time as a whole line by Tesseract",
      "whole row 1 read by Tesseract" not in seen, f"{seen}")

register_image = Image.open(FX / "2026-09-26_184309_00096_click_833_752.png").convert("RGB")
FAKE_CLICKS.clear()
out, ms, seen = run(register_image, 3)
check("on the Register tab it asks for the Purchase tab before row 1 is read",
      seen == in_order and FAKE_CLICKS == [tuple(get_price._need("purchase_tab"))], f"{seen} {FAKE_CLICKS}")
check("the one-read shortcut is gone", not hasattr(get_price, "_on_sale") and not hasattr(get_price, "BUY_WORD"))

get_price.RETRIES = 1
get_price.SEARCH_TIMEOUT = get_price.FAVOURITE_GAP
sleeps = []
real_click, real_sleep = get_price.shop.click, time.sleep
get_price.shop.click = lambda x, y, settle=None: FAKE_CLICKS.append((x, y))
get_price.time.sleep = lambda s: sleeps.append(s)
get_price._SEEN["searched"] = time.monotonic()
calibration.steps_reset()
with contextlib.redirect_stdout(io.StringIO()):
    try:
        get_price.get_price(4, verbose=False, search=True, on_purchase=True,
                            before=(("Chaos Core", 35, 709999), get_price._band(buy_row)))
    except no_input.GameInput:
        raise
    except Exception as exc:
        print(exc)
get_price.shop.click, get_price.time.sleep = real_click, real_sleep
waits = [s for s in sleeps if s > 0.5]
check(f"a favourite click right after another waits out favourite_gap ({get_price.FAVOURITE_GAP:g} s) first",
      bool(waits) and abs(waits[0] - get_price.FAVOURITE_GAP) < 0.05, f"sleeps {[round(s, 3) for s in sleeps[:3]]}")
steps = [s for s, _ms in calibration._STEPS]
check("the wait is timed as its own step", any("wait out the gap" in s for s in steps), f"{steps[:4]}")

get_price._SEEN["searched"] = time.monotonic() - 5
sleeps.clear()
get_price.shop.click = lambda x, y, settle=None: FAKE_CLICKS.append((x, y))
get_price.time.sleep = lambda s: sleeps.append(s)
calibration.steps_reset()
with contextlib.redirect_stdout(io.StringIO()):
    try:
        get_price.get_price(4, verbose=False, search=True, on_purchase=True,
                            before=(("Chaos Core", 35, 709999), get_price._band(buy_row)))
    except no_input.GameInput:
        raise
    except Exception:
        pass
get_price.shop.click, get_price.time.sleep = real_click, real_sleep
check("no wait when the last favourite click was long enough ago",
      not any("wait out the gap" in s for s, _ms in calibration._STEPS))
check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
