"""Report why calibration fails on this machine. Read-only: never clicks.

Run it on the machine that cannot calibrate, with Cabal open and the Agent
Shop showing the Register tab:

    py calibrate_probe.py

It prints every input calibration uses and the point at which it gives up,
and writes calibrate_probe.png beside itself. Send both back.

Nothing here moves the mouse, presses a key, or changes the game. The only
actions are a screen capture and OCR.
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trade  # noqa: E402


def rule(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


rule("1. the machine")
trade.make_dpi_aware()
screen = trade.current_screen_size()
client = trade.client_rect()
print(f"  screen size            {screen}")
print(f"  reference screen       {trade.REF_SCREEN}")
print(f"  game client rect       {client}")
print(f"  reference client rect  {trade.REF_CLIENT}")
if client:
    w, h = client[2] - client[0], client[3] - client[1]
    rw = trade.REF_CLIENT[2] - trade.REF_CLIENT[0]
    rh = trade.REF_CLIENT[3] - trade.REF_CLIENT[1]
    print(f"  client size            {w} x {h}   (reference {rw} x {rh})")
    print(f"  width ratio            {w / rw:.4f}")
    print(f"  height ratio           {h / rh:.4f}")
else:
    print("  !! the Cabal window was not found -- nothing below can work")

rule("2. the OCR upscale seed (no OCR involved)")
try:
    seed = trade._ocr_reference_scale()
    print(f"  _ocr_reference_scale() {seed:.4f}")
    print(f"  LAYOUT.scale           {trade.LAYOUT.scale}  (built-in until calibrated)")
    print(f"  _CALIBRATED            {trade._CALIBRATED}")
except Exception:
    traceback.print_exc()

rule("3. what is on screen")
shot = trade.grab()
out = Path(__file__).resolve().parent / "calibrate_probe.png"
shot.save(out)
print(f"  captured {shot.size[0]}x{shot.size[1]} -> {out}")
print(f"  trade_window_open      {trade.trade_window_open(shot)}")
print(f"  register_tab_open      {trade.register_tab_open(shot)}")
print(f"  dialog_kind            {trade.dialog_kind(shot)!r}")
if not trade.trade_window_open(shot):
    print("  !! the Trade window is not open. Open the Agent Shop on the")
    print("     Register tab and run this again -- calibration measures words")
    print("     INSIDE that window.")

rule("4. the anchors calibration needs")
try:
    words = trade.find_words(shot, (0, 0, shot.size[0], shot.size[1]), 40.0)
    lines = trade._text_lines(words)
    print(f"  words found on screen  {len(words)}")
    found = missing = 0
    for phrase, ref in trade.REF_ANCHORS:
        centre = trade._anchor_centre(phrase, words, lines)
        if centre:
            found += 1
            print(f"    OK      {phrase:<12} at {str(centre):>14}  reference {ref}")
        else:
            missing += 1
            print(f"    MISSING {phrase:<12} {'':>14}  reference {ref}")
    print(f"\n  {found} found, {missing} missing "
          f"(a fit needs at least two, far apart)")
    if missing:
        print("  Words that DID read, in case an anchor came back split:")
        for w in sorted(words, key=lambda w: -w.conf)[:25]:
            print(f"    {w.text!r:22} conf {w.conf:5.1f} at {w.centre}")
except Exception:
    traceback.print_exc()

rule("5. the fit")
try:
    layout = trade.measure_layout(shot, verbose=True)
    print(f"\n  measure_layout -> {layout}")
    if layout is not None:
        print(f"  validate_layout -> {trade.validate_layout(layout, verbose=True)}")
except Exception:
    traceback.print_exc()

rule("6. full calibrate(), exactly as a run would call it")
try:
    print(f"  calibrate(save=False) -> {trade.calibrate(verbose=True, save=False)}")
except Exception:
    traceback.print_exc()

print("\nDone. Send this whole output plus calibrate_probe.png.")
print("Nothing was clicked; the game is untouched.")
