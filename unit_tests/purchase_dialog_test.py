"""The Confirm Purchase dialog must be readable when it is plainly on screen.

DRIVES NOTHING. Replays a captured frame.

On 2026-08-15 a chaos resupply refused three orders in a row with "the Confirm
Purchase dialog did not appear" while the dialog was fully drawn -- item,
quantity, max and price all reading at conf 90+. The chaos pass stopped at 0 of
200 Cores, the cycle failed, and three failures tripped the breaker.

The cause was the crop, not the dialog. purchase_confirm swept
PURCHASE_DIALOG_REGION (1000x550) for its Buy/Cancel labels; those labels are
low-contrast grey-on-grey and do not survive a sweep that large. The same two
words read at conf 96 and 97 from PURCHASE_DIALOG_BUTTONS, a 380x50 crop over
exactly that row -- Tesseract upscales a small crop far more than a large one.
PURCHASE_DIALOG_BUTTONS already existed and had no reader.
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "purchase_dialog_test.db")
sys.argv = ["purchase_dialog_test"]
import trade  # noqa: E402

PASS = FAIL = 0


def check(ok, why):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {why}")


FRAME = _ROOT / "unit_tests" / "corpus" / "goldens" / \
    "purchase_dialog_lowcontrast.png"

print("=" * 74)
print("a dialog that is on screen must not read as absent")
print("=" * 74)

if not FRAME.exists():
    # The corpus is gitignored, so this frame travels only on the machine that
    # captured it. Skipping is correct; silently passing would not be.
    print(f"  SKIPPED: {FRAME.name} is not present (corpus is gitignored).")
    print("  The guard below still runs against the source.")
else:
    from PIL import Image
    img = Image.open(FRAME)
    trade.grab = lambda *a, **k: img

    tight = [w.text.strip().lower()
             for w in trade.find_words(img, trade.PURCHASE_DIALOG_BUTTONS)]
    check("buy" in tight and "cancel" in tight,
          f"both buttons read from their own crop (got {tight})")

    wide = [w.text.strip().lower()
            for w in trade.find_words(img, trade.PURCHASE_DIALOG_REGION, 20)
            if w.conf >= 45 and w.centre[1] > trade.PURCHASE_DIALOG_BUTTONS_Y]
    check("buy" not in wide,
          "and are NOT found by the wide sweep -- which is the whole reason "
          f"the fallback exists (wide sweep saw {wide})")

    d = trade.purchase_confirm()
    check(d is not None,
          "purchase_confirm finds the dialog on this frame")
    if d:
        check(d.get("buy") is not None, "it has a Buy button to click")
        check(d.get("cancel") is not None, "and a Cancel button")
        check(d.get("price") == 678_999,
              f"price reads 678,999 (got {d.get('price')})")
        check(d.get("qty_max") == 4,
              f"qty_max reads 4 (got {d.get('qty_max')})")

# Source guard: the fallback must stay wired even without the frame.
import inspect  # noqa: E402
src = inspect.getsource(trade.purchase_confirm)
check("PURCHASE_DIALOG_BUTTONS" in src,
      "purchase_confirm reads PURCHASE_DIALOG_BUTTONS -- the constant existed "
      "for this and had no reader, which is what let the failure through")

print()
print("-" * 74)
print(f"{PASS + FAIL} checks, {FAIL} failed")
sys.exit(1 if FAIL else 0)
