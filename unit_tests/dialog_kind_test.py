"""dialog_kind must find the modal that is plainly on screen.

The 2026-08-15 11:04 run aborted a relist eleven times with "the Registration
Extension dialog did not appear" while the dialog WAS up -- Register QTY
250/250, Register Cost 498,887, Register and Cancel buttons, all visible in the
recorded frame. Three separate waits missed it and the run gave up on the row.

The cause is not the matching. dialog_kind already stitches split titles and
tolerates a slipped character, and none of that can rescue a word that never
reaches it: Tesseract's segmentation is crop-dependent, and in a 1600x800
POPUP_REGION crop it drops the ornate title glyphs ENTIRELY. The 128-word read
of that frame contains nothing at all in the title band. A crop of the modal's
own box reads 'trationextension' and classifies correctly.

Replays recorded frames. DRIVES NOTHING -- no clicks, no game, no live OCR of
anything but these files.
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "dialog_kind_test.db")

sys.argv = ["dialog_kind_test"]
import trade as m  # noqa: E402
from PIL import Image  # noqa: E402

PASS = FAIL = 0
GOLD = _ROOT / "unit_tests" / "corpus" / "goldens"


def check(ok, why):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {why}")


def section(title):
    print("=" * 74)
    print(title)
    print("=" * 74)


def frame(name):
    path = GOLD / name
    return Image.open(path) if path.exists() else None


section("the modal the 11:04 run could not see")

shot = frame("dialog_extension_missed.png")
if shot is None:
    print("  (no golden; NOT exercised)")
else:
    # The frame the live run classified as 'none', recorded by cancel.after_
    # change on the attempt that aborted.
    check(m.dialog_kind(shot) == "extension",
          f"the Registration Extension dialog is recognised, got "
          f"{m.dialog_kind(shot)!r}")

    # And the reason, pinned so a future change to the region cannot quietly
    # undo it: the wide sweep genuinely cannot see this title.
    wide = m.find_words(shot, m.POPUP_REGION, m.DIALOG_TEXT_MIN_CONF)
    tight = m.find_words(shot, m.MODAL_DIALOG_REGION, m.DIALOG_TEXT_MIN_CONF)

    def has_title(words):
        texts = [m._normalise(w.text) for w in words]
        texts += [m._normalise("".join(w.text for w in line))
                  for line in m._text_lines(words)]
        return any("exten" in t or "istrat" in t for t in texts)

    check(not has_title(wide),
          "POPUP_REGION does not contain the title at all -- this is the "
          "measurement the fix rests on, so if it ever stops being true the "
          "fallback is doing the work and this test should say so")
    check(has_title(tight),
          "the modal's own box does contain it")
    check(len(tight) < len(wide),
          f"and is a far smaller read: {len(tight)} words against {len(wide)}")

section("a modal that was already being found stays found")

shot = frame("dialog_extension_seen.png")
if shot is None:
    print("  (no golden; NOT exercised)")
else:
    check(m.dialog_kind(shot) == "extension",
          f"still 'extension', got {m.dialog_kind(shot)!r}")

section("no dialog is still no dialog")

shot = frame("dialog_none_before_change.png")
if shot is None:
    print("  (no golden; NOT exercised)")
else:
    # Recorded by cancel.before_change -- the Change click has not gone in yet,
    # so the table is bare. A reader that answers "extension" here would make
    # the script click Cancel on a dialog that is not there.
    check(m.dialog_kind(shot) is None,
          f"the frame before the Change click has no dialog, got "
          f"{m.dialog_kind(shot)!r}")

section("the region is the modal's, and the fallback is still wired")

check(m.MODAL_DIALOG_REGION == (975, 470, 1570, 945),
      f"MODAL_DIALOG_REGION is the measured box, got {m.MODAL_DIALOG_REGION}")
check(m.MODAL_DIALOG_REGION == m.CONVERT_DIALOG_REGION,
      "and is the same box CONVERT_DIALOG_REGION was measured over -- these "
      "modals share a position, so if one moves both must")

import inspect  # noqa: E402
src = inspect.getsource(m.dialog_kind)
check("MODAL_DIALOG_REGION" in src and "POPUP_REGION" in src,
      "dialog_kind reads the tight box AND keeps the wide one as a fallback, "
      "so a dialog drawn somewhere unexpected is still seen")

# Callers that pass their own words must not silently get a second OCR pass.
words = m.find_words(frame("dialog_extension_missed.png"),
                     m.MODAL_DIALOG_REGION, m.DIALOG_TEXT_MIN_CONF) \
    if frame("dialog_extension_missed.png") else []
if words:
    check(m.dialog_kind(None, words=words) == "extension",
          "an explicit word list is classified without touching a frame -- "
          "source is None here, so any read would raise")

print()
print("-" * 74)
print(f"{PASS + FAIL} checks, {FAIL} failed")
sys.exit(1 if FAIL else 0)
