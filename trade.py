"""Cabal Online automation: screen capture, Alz reading and Agent Shop trading.

Everything lives here -- capture, OCR, input and the trade automation -- so
there is one file to read and one to run.

Requires: pip install mss Pillow
          winget install UB-Mannheim.TesseractOCR

Cabal runs elevated, so this must be run from an Administrator terminal or
Windows silently discards every click.

    py trade.py --calibrate                measure this machine's layout
    py trade.py --shot                     capture the screen
    py trade.py --alz                      read the Alz balance
    py trade.py --list                     show the Agent Shop listings
    py trade.py --open                     open the shop via the NPC
    py trade.py --relist 3                 cancel row 3 and re-list it
    py trade.py --relist-rows 1-10         the same for the first ten rows
    py trade.py --repeat "relist-rows 1-10" --for 120 --every 30

FIRST RUN ON A NEW MACHINE
    Open the Agent Shop so the Trade window is visible, then:
        py trade.py --calibrate
    The coordinates built into this file were measured at 2560x1440. On any
    other screen they point at the wrong pixels, and a click that misses the UI
    lands in the game world -- which moves the character or an item. So every
    command that clicks refuses to run until a calibration exists that matches
    the current screen and game window. The result is saved to
    calibration.json and re-measured automatically whenever either changes.
"""

from __future__ import annotations

import argparse
import atexit
import csv
import ctypes
import ctypes.wintypes
import io
import json
import math
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# ==========================================================================
# SETTINGS -- the values you are most likely to want to change.
# ==========================================================================
#
# Everything here is a plain literal and is defined ONCE, in this block.
# The rest of the file reads these names; there is no second copy further
# down that would silently win. Derived geometry (regions, offsets, the
# calibration frame) is NOT here on purpose -- it is computed from the
# measured layout and editing it by hand would send clicks into the game
# world.
#

# --------------------------------------------------------------------------
# PRICE FLOORS -- the money numbers
# --------------------------------------------------------------------------

# Absolute per-item floors, matched against the listing name (case-insensitive).
# These bind no matter what the market suggests.
# (token, full catalogue name, floor). The token is a fast substring test; the
# full name is what a corrupted read is compared against, because a 3-character
# token is far too small a target for OCR. Measured over realistic single-glyph
# corruptions of the VIP name, the token alone lost the floor 4.8% of the time
# -- every one of V->Y, V->U, P->F, P->R, P->B, I->T -- while also matching
# 'V|pgrade Core(High)', which folds to contain "vip" and would have listed 158
# cores at 110,000,000 each.
#
# The catalogue name must be spelled as the GAME renders it, not as it is said
# in conversation: the row reads "Siena's Unbinding Stone", and dropping the
# "'s" would cost similarity on every single match for no reason.
ITEM_PRICE_FLOORS: tuple[tuple[str, str, int], ...] = (
    ("vip", "Yekaterina VIP Membership", 104_000_000),
    # 'siena' is the token rather than 'unbinding': the plain "Unbinding Stone"
    # is a different, far cheaper item, and 'unbinding' would hand it this
    # floor on the token route. 'siena' is 5 characters and unique to this
    # item, so it is both a stronger target than 'vip' and a narrower one.
    ("siena", "Siena's Unbinding Stone", 71_000_000),
    # 'gempack' rather than 'gem': plain "Force Gem" scores 0.593 against this
    # catalogue name, which clears the 0.4 token bar, so the shorter token
    # would hand a different item this floor. 'gempack' survives every damaged
    # read measured (a clipped trailer, a dropped letter, 0-for-O) because the
    # similarity route carries those.
    #
    # CAVEAT, measured not assumed: a DIFFERENT PACK SIZE inherits this floor
    # and no token can prevent it. "Force Gem Package (x100)" scores 0.947
    # against this name and "(x40)" scores 0.973, both far above the 0.75
    # similarity bar -- they are nearly the same string. Only the x400 has ever
    # been listed on this account, so it is latent; if a smaller pack is ever
    # listed it would go up at 180,000,000, never sell, and pay a percentage
    # fee on that figure. Give it its own entry before listing one.
    ("gempack", "Force Gem Package (x400)", 180_000_000),
)

FALLBACK_PRICE = 10_000_000_000    # 10B, when the game suggests no price

# Below this, a table price is treated as a misread rather than a real figure.
# Every guard derives from that number, and the `if original` / `if price_floor`
# short-circuits mean a small value silently disables all of them at once.
MIN_PLAUSIBLE_PRICE = 1_000

# Sanity checks on relisting.
#
# There is NO relative floor against the previous price -- the rule is to take
# the lowest current price, whatever it is. A MAX_PRICE_DROP constant and a
# price_floor_for() helper implementing a 5% floor used to live here, called
# from nowhere, while relist()'s docstring described them as binding. Dead
# safety machinery is worse than none: it invites the one floor that does bind
# (ITEM_PRICE_FLOORS) to be relaxed against a backstop that was never there.
#
# A market price below this fraction of the listed price is reported as a NOTE
# and then listed at anyway.
SUSPECT_PRICE_FRACTION = 0.5

# --------------------------------------------------------------------------
# WHAT GETS RELISTED
# --------------------------------------------------------------------------

# Relisting pushes the quantity to the maximum available for every item, so a
# listing of 3 does not come back as a listing of 1. Add name fragments here to
# exclude particular items from that.
MAXIMISE_ALL_QUANTITIES = True

NO_MAX_QUANTITY_ITEMS: tuple[str, ...] = ()

# What to type to fill the quantity field. The game clamps entry to the stack's
# maximum, so anything larger than a stack can hold maximises it -- no need to
# read the maximum off the panel first and type it back exactly.
MAX_QTY_ENTRY = 9999

# How far the panel's stack size may differ from the quantity the table showed
# before the load is treated as a different item rather than a misread digit.
# A different item differs by orders of magnitude; an OCR slip differs by one
# glyph. Sized from the incident that motivated it: the table read a 233-stack
# as 230, and exact equality aborted AFTER the cancel had committed -- which
# stranded the stack, failed the next three cycles on the empty-work-tab check,
# and stopped a five-hour run.
QTY_CROSSCHECK_ABSOLUTE = 5

QTY_CROSSCHECK_FRACTION = 0.10

# --------------------------------------------------------------------------
# PACE AND PATIENCE
# --------------------------------------------------------------------------

# Pace every input the script sends. The game is a live client with its own
# animations and server round-trips; acting faster than a person can leaves it
# behind and produces the "the click did nothing" failures.
ACTION_COOLDOWN = 0.5   # after a move, a click or a key press

TYPE_COOLDOWN = 0.5     # between keystrokes while entering a value

# Shortest turnaround between loop cycles, so `--every 0` cannot spin a core
# when a cycle happens to do no work at all.
MIN_CYCLE_SECONDS = 1.0

# A sold row must be collected before it can be relisted; the server needs a
# moment to settle before the table is worth re-reading.
RECEIVE_WAIT = 3.0

RELIST_ATTEMPTS = 3

# How many times a refused input is retried before it counts as blocked.
# SendInput returning 0 is usually transient (a hook, a momentary elevated
# foreground, a desktop switch). Treating one refusal as permanent ended an
# unattended run mid-cycle with no diagnosis; _release() already retried the
# identical call three times for a key-up, so this was an asymmetry, not a
# policy. Real UIPI blocking fails every attempt and still raises.
SEND_ATTEMPTS = 4

# Consecutive failed cycles before the loop gives up. Retrying only helps if
# something might change between attempts; a stranded item in the work tab, for
# instance, blocks every later cycle identically until a human clears it.
MAX_CONSECUTIVE_FAILURES = 3

# Every polling deadline in this file is measured around a Tesseract call, so
# without a timeout here a wedged tesseract.exe hangs the whole run.
TESSERACT_TIMEOUT = 30.0

# --------------------------------------------------------------------------
# GAME AND INVENTORY
# --------------------------------------------------------------------------

GAME_TITLE_HINT = "PlayCabal"

# The tab cancelled items are expected to land in. It must be empty before a
# run: a large stack scatters across tabs, and pre-existing items there make it
# impossible to tell which slots the cancel actually filled.
WORK_TAB = 4

EXPECTED_ROWS = 10

# --------------------------------------------------------------------------
# FRAME RECORDING
# --------------------------------------------------------------------------

RECORD_ENABLED = False

RECORD_LIMIT = 12000         # stop before filling the disk

# ==========================================================================
# END SETTINGS
# ==========================================================================


try:
    import mss
except ImportError:
    sys.exit("Missing dependency 'mss'. Install it with:  pip install mss")

try:
    from PIL import Image, ImageChops, ImageOps
except ImportError:
    sys.exit("Missing dependency 'Pillow'. Install it with:  pip install Pillow")

SCRIPT_DIR = Path(__file__).resolve().parent


# Reading a full 10-row table costs ~18s of OCR (measured), so any deadline
# that is meant to allow a retry has to be a multiple of that, not a few
# seconds. Deadlines are checked after each read, never mid-read.
TABLE_READ_BUDGET = 45.0

# ==========================================================================
# Screen capture, Alz reading and low-level input
# ==========================================================================
DEFAULT_OUTDIR = SCRIPT_DIR / "screenshots"

# mss >= 10 renamed the entry point; the lowercase alias is deprecated.
open_capture = getattr(mss, "MSS", None) or mss.mss

# Generous band around the Alz figure in the Inventory panel, verified against a
# 2560x1440 capture. The left edge stays right of the gold coin icon, which is
# also orange and would otherwise register as a digit blob.
ALZ_REGION = (2330, 872, 2525, 928)

TESSERACT_CANDIDATES = (
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
)


# --------------------------------------------------------------------------
# Alz reading
# --------------------------------------------------------------------------

def find_tesseract() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in TESSERACT_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return None


def _isolate_digits(
    image: Image.Image, region: tuple[int, int, int, int]
) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    """Black-on-white image of just the orange Alz digits, plus the box those
    digits occupy in the source image. None if the region holds no digits."""
    crop = image.crop(region).convert("RGB")
    scale = 5
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)

    px = crop.load()
    mask = Image.new("L", crop.size, 255)
    m = mask.load()
    for y in range(crop.height):
        for x in range(crop.width):
            r, g, b = px[x, y]
            # The Alz figure is drawn in a saturated colour that varies -- it is
            # orange normally but green after a change -- while the "Alz" label
            # beside it is grey and the panel behind it is dark. So key on
            # "bright and colourful" rather than on a specific hue.
            hi, lo = max(r, g, b), min(r, g, b)
            if hi > 110 and hi - lo > 45:
                m[x, y] = 0

    # Trim to the actual glyphs so panel edges do not skew the OCR.
    bbox = ImageOps.invert(mask).getbbox()
    if not bbox:
        return None

    prepared = ImageOps.expand(mask.crop(bbox), border=60, fill=255).convert("RGB")
    # Map the upscaled crop's box back onto the source image.
    source_box = (
        region[0] + bbox[0] // scale,
        region[1] + bbox[1] // scale,
        region[0] + bbox[2] // scale,
        region[1] + bbox[3] // scale,
    )
    return prepared, source_box


def get_alz(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int] | None = None,
    debug_path: Path | None = None,
) -> int:
    """Read the Alz balance from a screenshot.

    Returns 0 when the Inventory panel is closed, the region holds no digits,
    or Tesseract is unavailable -- this never raises on a failed read.
    """
    tesseract = find_tesseract()
    if tesseract is None:
        print(
            "Alz: skipped, Tesseract not found "
            "(winget install UB-Mannheim.TesseractOCR)",
            file=sys.stderr,
        )
        return 0

    image = source if isinstance(source, Image.Image) else Image.open(source)
    region = region if region is not None else ALZ_REGION

    # A region outside the image means a different resolution or layout.
    if region[2] > image.width or region[3] > image.height:
        print(
            f"Alz: skipped, region {region} falls outside the "
            f"{image.width}x{image.height} image",
            file=sys.stderr,
        )
        return 0

    found = _isolate_digits(image, region)
    if found is None:
        return 0
    prepared, _ = found

    tmp = debug_path or (SCRIPT_DIR / ".alz_tmp.png")
    try:
        prepared.save(tmp)
        result = subprocess.run(
            [
                tesseract, str(tmp), "stdout",
                "--psm", "7",
                "-c", "tessedit_char_whitelist=0123456789,",
            ],
            capture_output=True,
            text=True,
            timeout=TESSERACT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(f"Alz: skipped, Tesseract did not respond within "
              f"{TESSERACT_TIMEOUT:g}s", file=sys.stderr)
        return 0
    except OSError as exc:
        print(f"Alz: skipped, could not run Tesseract ({exc})", file=sys.stderr)
        return 0
    finally:
        if debug_path is None and tmp.exists():
            tmp.unlink()

    if result.returncode != 0:
        print(f"Alz: skipped, Tesseract failed ({result.stderr.strip()})", file=sys.stderr)
        return 0

    digits = re.sub(r"[^0-9]", "", result.stdout)
    return int(digits) if digits else 0


def find_alz(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int] | None = None,
    origin: tuple[int, int] = (0, 0),
) -> tuple[int, int, int, int] | None:
    """Screen box the Alz digits occupy, as (left, top, right, bottom).

    Returns None when the Inventory panel is not visible. `origin` is the
    captured monitor's top-left in virtual-desktop coordinates, so boxes from a
    secondary monitor's capture land in the right place on screen.
    """
    image = source if isinstance(source, Image.Image) else Image.open(source)
    # Resolved at call time, never as a default argument: a default is bound
    # when the function is defined, which is before calibrate() has run, so
    # these three functions would have kept searching the reference machine's
    # coordinates no matter what the calibration found.
    region = region if region is not None else ALZ_REGION
    if region[2] > image.width or region[3] > image.height:
        return None

    found = _isolate_digits(image, region)
    if found is None:
        return None

    _, box = found
    # A box that fills the search region is not a number, it is the 3D world.
    # _isolate_digits keys on "bright and colourful", which snow-lit scenery
    # satisfies everywhere, so with the Inventory panel SHUT this returned the
    # whole region on 13 of 13 frames -- and the docstring promises None. The
    # inventory anchor is derived from this box, so the consequence was a grid
    # offset by 43px and Ctrl+Clicks into the open world.
    width, height = region[2] - region[0], region[3] - region[1]
    if (box[2] - box[0]) >= width * 0.95 and (box[3] - box[1]) >= height * 0.95:
        return None

    ox, oy = origin
    return (box[0] + ox, box[1] + oy, box[2] + ox, box[3] + oy)


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def move_mouse(x: int, y: int) -> bool:
    """Move the cursor to a virtual-desktop pixel coordinate.

    make_dpi_aware() must have run first, otherwise Windows rescales these
    coordinates on a scaled display and the cursor lands short.

    Returns False if Windows refused the move. The usual cause is UIPI: Cabal
    runs elevated, so a normal-integrity process cannot inject input while the
    game holds the foreground. Running this script as Administrator fixes it.
    """
    make_dpi_aware()
    ctypes.windll.user32.SetCursorPos.restype = ctypes.c_int
    moved = bool(ctypes.windll.user32.SetCursorPos(int(x), int(y)))
    if moved:
        cooldown()
    return moved


def cursor_position() -> tuple[int, int]:
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    point = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


CURSOR_BLOCKED_HINT = (
    "Windows refused the cursor move. Cabal runs elevated, so a normal process "
    "cannot move the cursor while the game is in the foreground -- run this "
    "script as Administrator."
)


def move_mouse_to_alz(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int] | None = None,
    origin: tuple[int, int] = (0, 0),
) -> tuple[int, int] | None:
    """Park the cursor on the Alz figure. Returns where it went, or None if the
    panel was not visible (in which case the cursor is left alone).

    Raises PermissionError if Windows blocked the move (see CURSOR_BLOCKED_HINT).
    """
    box = find_alz(source, region, origin)
    if box is None:
        return None
    centre = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
    if not move_mouse(*centre):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    return centre


def human(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------

def make_dpi_aware() -> None:
    """Capture at native resolution on scaled displays."""
    if sys.platform != "win32":
        return
    try:
        # Per-monitor DPI aware v2, best result on mixed-DPI setups.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def list_monitors(sct) -> None:
    # sct.monitors[0] is the union of all monitors; real displays start at 1.
    for i, mon in enumerate(sct.monitors[1:], start=1):
        print(f"{i}: {mon['width']}x{mon['height']} at ({mon['left']},{mon['top']})")


def resolve_monitor(sct, choice: str) -> tuple[dict, str]:
    displays = sct.monitors[1:]

    if choice == "all":
        return sct.monitors[0], "all"
    if choice == "primary":
        # mss orders by OS enumeration; the primary display sits at (0, 0).
        for mon in displays:
            if mon["left"] == 0 and mon["top"] == 0:
                return mon, "primary"
        return displays[0], "primary"
    if choice.isdigit():
        index = int(choice)
        if not 1 <= index <= len(displays):
            sys.exit(
                f"Monitor {index} does not exist. Detected {len(displays)} "
                f"monitor(s). Run with --monitors to see them."
            )
        return displays[index - 1], f"monitor{index}"

    sys.exit(
        f"Invalid --monitor value {choice!r}. "
        "Use 'primary', 'all', or a 1-based monitor number."
    )


def unique_path(path: Path) -> Path:
    """Avoid clobbering an existing file: timestamps only resolve to a second,
    so two captures inside the same second would otherwise collide."""
    if not path.exists():
        return path
    for n in range(2, 100):
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def prune_screenshots(outdir: Path, keep: int, protect: Path) -> int:
    """Delete all but the `keep` newest captures. Returns how many were removed.

    Only touches files matching the generated screenshot_*.png name, so
    unrelated files sharing the folder are left alone.
    """
    shots = sorted(
        outdir.glob("screenshot_*.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in shots[max(keep, 1):]:
        if old.resolve() == protect.resolve():
            continue
        try:
            old.unlink()
            removed += 1
        except OSError as exc:
            print(f"Could not delete {old.name}: {exc}", file=sys.stderr)
    return removed


def copy_to_clipboard(png_bytes: bytes) -> None:
    """Put the image on the Windows clipboard as CF_DIB."""
    import io

    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    with io.BytesIO() as buf:
        image.save(buf, "BMP")
        # A BMP file header is 14 bytes; CF_DIB expects everything after it.
        dib = buf.getvalue()[14:]

    CF_DIB, GMEM_MOVEABLE = 8, 0x0002
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
    if not handle:
        print("Skipping --clipboard: GlobalAlloc failed", file=sys.stderr)
        return

    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, dib, len(dib))
    kernel32.GlobalUnlock(handle)

    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(ctypes.c_void_p(handle))
        print("Skipping --clipboard: could not open the clipboard", file=sys.stderr)
        return
    try:
        user32.EmptyClipboard()
        # On success the system owns the handle, so it must not be freed here.
        if not user32.SetClipboardData(CF_DIB, handle):
            kernel32.GlobalFree(ctypes.c_void_p(handle))
            print("Skipping --clipboard: SetClipboardData failed", file=sys.stderr)
            return
    finally:
        user32.CloseClipboard()

    print("Copied to clipboard.")


def take_screenshot(monitor: str = "primary", delay: float = 0.0):
    """Capture and return (png_bytes, width, height, label, origin), where
    origin is the captured area's top-left on the virtual desktop."""
    make_dpi_aware()
    with open_capture() as sct:
        region, label = resolve_monitor(sct, monitor)
        if delay > 0:
            time.sleep(delay)
        shot = sct.grab(region)
    origin = (region["left"], region["top"])
    return mss.tools.to_png(shot.rgb, shot.size), shot.width, shot.height, label, origin


# ==========================================================================
# Layout calibration
# ==========================================================================
#
# Every coordinate below was measured on one machine: 2560x1440, the game
# maximised. None of it transfers. The Trade window is drawn at a position that
# depends on the client size, and the panel inside it may or may not scale with
# resolution -- so a constant like DIALOG_BUTTON_MIN_X = 1200, which exists to
# separate dialog buttons (x~1290) from the table's own buttons (x~1126), is
# simply wrong at 1920x1080 where the dialog sits near x~830. It does not
# misbehave subtly: it filters away EVERY dialog button, so nothing is ever
# clickable and the run fails with a misleading message.
#
# The fix is to keep these numbers as a REFERENCE frame -- relative to the
# Trade window's own top-left, at a known reference scale -- and to measure the
# real frame at runtime. `calibrate()` finds the Trade window, derives the
# offset and scale against this reference, and `LAYOUT` then answers every
# geometry question. The reference values are still exactly what was measured
# here, so on this machine calibration resolves to a no-op.
#
# Anything that cannot be measured is refused rather than guessed: clicking a
# coordinate derived from an unverified layout is how items get moved and
# listings get cancelled by accident.

# The display the reference numbers were measured on.
REF_SCREEN = (2560, 1440)
# The Trade window on that display: origin and size.
REF_TRADE_ORIGIN = (10, 30)
REF_TRADE_SIZE = (1225, 1035)

# Two words far apart inside the Trade window, used to measure the runtime
# offset and scale. Both OCR at high confidence and sit near opposite corners,
# which is what makes the baseline between them long enough to measure a scale
# from without amplifying a few pixels of OCR jitter into a large error.
# Every value below was MEASURED on six live captures and is stable to the
# pixel across all of them. Two earlier entries were not: "Function" was out by
# 74px in y, and "Purchase" was never found at all because the game's FPS
# overlay is drawn across that tab. A wrong reference point does not fail
# loudly -- it is absorbed into `scale`, so calibration reports success and
# every derived coordinate is quietly wrong.
# Every one of these was measured on BOTH a 2560x1440 fullscreen capture and a
# live 1920x1080 window, and fits with a worst residual of 1.7px. They are all
# chrome that exists on the Purchase tab as well as the Register tab, and each
# OCRs as a unique word on the screen.
#
# "Register Item" was dropped: it is a Register-tab panel label, so calibrating
# while the Purchase tab is showing found nothing. "Purchase" was dropped the
# other way round -- it reads at 1080p but never at 1440p, where the game's FPS
# overlay is drawn across that tab.
REF_ANCHORS: tuple[tuple[str, tuple[int, int]], ...] = (
    ("Trade", (608, 19)),       # window title, top centre
    ("Name", (492, 119)),       # column header, upper left
    ("Adjust", (919, 65)),      # "Adjust fee", upper right
    ("Function", (1126, 118)),  # column header, upper right
    ("Selling", (331, 982)),    # footer, bottom left
    ("Refresh", (1119, 981)),   # button, bottom right
)
# How far apart two anchors must be before their separation is trusted to
# measure a scale. Below this, OCR jitter of a few pixels dominates.
MIN_ANCHOR_BASELINE = 300.0
# ...and how far they must spread on EACH axis. The baseline above is the
# longest diagonal, which a row of anchors along one line satisfies while
# telling you nothing about the perpendicular direction: the fit is then
# extrapolated off that line. Measured, a set covering 430px of x and 100px of
# y produced 40px of click error at the far end of the window.
MIN_ANCHOR_SPREAD = 250.0
# Confidence an anchor must reach when it is matched only as a CLIPPED word
# (the font drops a leading glyph at smaller UI scales, so 'Trade' reads as
# 'rade'). Higher than the 40.0 used for a whole-word match, because a partial
# word is weaker evidence: the read that shifted the calibrated origin by 2px
# scored 40.1, clearing the ordinary bar by a tenth of a point.
NEAR_ANCHOR_MIN_CONF = 70.0
# How many anchors must survive outlier rejection. Higher than the absolute
# minimum of 3 on purpose: dropping is only sound while enough independent
# evidence remains to contradict the next bad one.
MIN_ANCHORS_AFTER_DROP = 4
# A measured scale outside this range means the anchors were misread, not that
# the UI is unusual.
SCALE_LIMITS = (0.4, 2.5)
# Where the calibration is remembered between runs.
CALIBRATION_FILE = SCRIPT_DIR / "calibration.json"


@dataclass(frozen=True)
class Layout:
    """The runtime geometry of the game's UI.

    `origin` is the Trade window's top-left in screen pixels and `scale` is its
    size relative to REF_TRADE_SIZE. Everything else is derived, so there is one
    place to be wrong rather than forty.
    """
    screen: tuple[int, int]
    origin: tuple[int, int]
    scale: float
    client: tuple[int, int, int, int] | None = None
    measured_from: str = "reference"

    # -- conversions ------------------------------------------------------
    def x(self, value: float) -> int:
        return int(round(self.origin[0] + value * self.scale))

    def y(self, value: float) -> int:
        return int(round(self.origin[1] + value * self.scale))

    def point(self, ref: tuple[float, float]) -> tuple[int, int]:
        return (self.x(ref[0]), self.y(ref[1]))

    def box(self, ref: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        left, top, right, bottom = ref
        return (self.x(left), self.y(top), self.x(right), self.y(bottom))

    def length(self, value: float) -> int:
        return int(round(value * self.scale))

    # -- derived regions ---------------------------------------------------
    @property
    def trade(self) -> tuple[int, int, int, int]:
        return self.box((0, 0, REF_TRADE_SIZE[0], REF_TRADE_SIZE[1]))

    def describe(self) -> str:
        return (f"screen {self.screen[0]}x{self.screen[1]}, Trade window at "
                f"{self.origin}, scale {self.scale:.3f} ({self.measured_from})")


# The Function column's x, relative to the Trade window origin (screen 1126 on
# the reference display). Used only to sanity-check that the dialog-button
# boundary still lands to the right of it after scaling.
REF_FUNCTION_COLUMN_X = 1116
# The dialog-button boundary as measured, in absolute reference-screen pixels.
# Kept as its own value rather than derived from the Function column: the
# derivation guessed "+40" and produced 1166 where the measurement says 1200.
REF_DIALOG_BUTTON_MIN_X = 1200

# Replaced by calibrate(). The default reproduces the measured reference frame
# exactly, so behaviour on the machine this was written on is unchanged.
LAYOUT = Layout(screen=REF_SCREEN, origin=REF_TRADE_ORIGIN, scale=1.0,
                measured_from="reference defaults")

# The Trade window and the dialog band, in screen pixels. Defined here rather
# than only inside apply_layout() because _capture_reference_geometry() reads
# them out of globals() to build the reference frame, and ~30 call sites read
# them directly. At the default layout these are exactly the values that were
# measured by hand: (10, 30, 1235, 1065) and the dialog band around it.
TRADE_REGION = LAYOUT.trade
# The popup is centred on the game window, whose size varies; search a band.
POPUP_REGION = (500, 350, 2100, 1150)


def _clamp_box(box: tuple[int, int, int, int],
               screen: tuple[int, int]) -> tuple[int, int, int, int]:
    """Keep a region inside the screen and the right way round.

    PIL pads an out-of-bounds crop with black rather than complaining, so an
    unclamped region reads as "nothing there" instead of failing -- and a box
    that scaling has inverted (right < left) makes crop raise mid-sequence,
    possibly after a cancel has already committed.
    """
    left, top, right, bottom = box
    left, top = max(0, left), max(0, top)
    right, bottom = min(screen[0], right), min(screen[1], bottom)
    return (left, top, max(left + 1, right), max(top + 1, bottom))


# ==========================================================================
# Agent Shop automation
# ==========================================================================

# The dialog reads "Cancel item registration" with [Confirmation] [Cancel].
# Confirmation performs the cancellation; Cancel merely closes the dialog.
CONFIRM_WORD = "Confirmation"
DISMISS_WORD = "Cancel"
# Collecting a sale raises "Confirm Receipt", whose accept button reads Receive
# rather than Confirmation.
RECEIPT_WORD = "Receive"
# Dialog buttons sit at x ~1290 / ~1471; the table's own Change/Receive buttons
# are at x ~1126. Filtering on x keeps a table button from being mistaken for a
# dialog button -- which matters for "Receive", a word that appears in both.
DIALOG_BUTTON_MIN_X = 1200
# Registering can raise more than one confirmation: pricing more than 25% below
# the weekly average adds an extra "are you sure" dialog on top of the usual one.
MAX_CONFIRM_STEPS = 3
# How long to keep looking for the Registration Extension dialog after the main
# wait has expired, before accepting that it never came.
#
# The main wait polls the TITLE, which Tesseract drops at POPUP_REGION scale on
# some frames, so "expired" routinely means "did not read it in time" rather
# than "it is not there". Only spent on a path that was about to abort.
EXTENSION_RECHECK_SECONDS = 4.0
# Dialog titles are large ornate glyphs that OCR poorly at the crop size this
# search uses; a 40% bar dropped them entirely. Junk admitted at 25% is
# filtered by having to match a specific title word, not by confidence.
DIALOG_TEXT_MIN_CONF = 25.0


@dataclass
class Word:
    text: str
    left: int
    top: int
    right: int
    bottom: int
    conf: float

    @property
    def centre(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


# --------------------------------------------------------------------------
# Text location
# --------------------------------------------------------------------------

def _prep_for_text(image: Image.Image, region: tuple[int, int, int, int], scale: int):
    """Cabal draws light text on dark panels; Tesseract wants the opposite."""
    crop = image.crop(region).convert("L")
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    return ImageOps.autocontrast(ImageOps.invert(crop))


_CALIBRATED = False


def _ocr_reference_scale() -> float:
    """The UI scale to pick an OCR upscale from, before calibration exists.

    LAYOUT.scale is the measured answer, but it is the built-in 1.0 until a
    calibration succeeds -- and a calibration needs OCR to succeed first. That
    circle is what made a 1080p screen uncalibratable: the first pass ran at
    the x2 intended for 1440p, which is documented as splitting 'Refresh' into
    'R' + 'efresh', and the upscale could only improve after reaching the state
    it was blocking.

    The window's own client rectangle breaks it, with no OCR at all. Measured
    on the machine that could not calibrate: client height 1041 against the
    reference 1369 gives 0.7604, where the anchors later measured 0.7607 --
    0.04% out, against upscale bins ~30% wide.

    `min` of the two axes, not the height alone, so a letterboxed client errs
    toward MORE upscale, which costs time rather than accuracy.

    This value only ever chooses an integer upscale. It must never reach
    LAYOUT.scale: a guessed scale that reaches apply_layout is exactly the
    "clicks confidently in the wrong place" failure the whole layer exists to
    prevent.
    """
    if _CALIBRATED:
        return LAYOUT.scale
    try:
        make_dpi_aware()          # GetClientRect returns logical px without it
        client = client_rect()
        if client:
            width = client[2] - client[0]
            height = client[3] - client[1]
            ref_w = REF_CLIENT[2] - REF_CLIENT[0]
            ref_h = REF_CLIENT[3] - REF_CLIENT[1]
            if width > 100 and height > 100:     # not minimised
                guess = min(width / ref_w, height / ref_h)
                if SCALE_LIMITS[0] <= guess <= SCALE_LIMITS[1]:
                    return guess
    except Exception:             # noqa: BLE001 - never break OCR over a guess
        pass
    return LAYOUT.scale


def find_words(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int],
    min_conf: float = 40.0,
    scale: int | None = None,
) -> list[Word]:
    """Every word Tesseract can see in `region`, in screen coordinates.

    `scale` upsamples before OCR and defaults to tracking the layout, so the
    glyphs Tesseract sees stay about the same size whatever the screen is. A
    fixed x2 was enough at 2560x1440 and not at 1920x1080: 'Refresh' came back
    split as 'R' + 'efresh', which find_text (a whole-word substring match)
    does not match -- so refresh_table() could never find its button and no
    relist cycle could complete, with a perfect calibration.
    """
    if scale is None:
        scale = max(2, min(6, int(round(2 / max(_ocr_reference_scale(), 0.34)))))
    tesseract = find_tesseract()
    if tesseract is None:
        # Degrade like the timeout and non-zero-exit paths below rather than
        # sys.exit. SystemExit derives from BaseException, so it is caught by
        # nothing here -- and this function is reachable from close_shop's
        # `finally`, where it would replace an in-flight FatalAbort and kill
        # the process after a withdrawal had already committed. main() checks
        # for the binary once at startup; that is where a hard stop belongs.
        print("Tesseract not found (winget install UB-Mannheim.TesseractOCR); "
              "treating this frame as unreadable.", file=sys.stderr)
        return []

    image = source if isinstance(source, Image.Image) else Image.open(source)
    region = (
        max(0, region[0]), max(0, region[1]),
        min(image.width, region[2]), min(image.height, region[3]),
    )
    prepared = _prep_for_text(image, region, scale)

    buf = io.BytesIO()
    prepared.save(buf, "PNG")
    # A timeout is essential, not defensive: every deadline in this file is
    # measured AROUND this call, so a wedged tesseract.exe would hang the run
    # forever with no timeout able to fire.
    try:
        result = subprocess.run(
            [tesseract, "stdin", "stdout", "--psm", "11", "tsv"],
            input=buf.getvalue(),
            capture_output=True,
            timeout=TESSERACT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(f"Tesseract did not respond within {TESSERACT_TIMEOUT:g}s; "
              "treating this frame as unreadable.", file=sys.stderr)
        return []
    except OSError as exc:
        # Every sibling OCR path catches this; this one did not. It matters
        # more than it looks: PermissionError is an OSError subclass, so an
        # antivirus lock or a bad exec bit on tesseract.exe escaped to
        # run_loop's `except PermissionError` and stopped the run with
        # "Input was refused" -- a confidently wrong diagnosis.
        print(f"Could not run Tesseract ({exc}); treating this frame as "
              "unreadable.", file=sys.stderr)
        return []
    if result.returncode != 0:
        # Degrade exactly as the timeout above does. sys.exit raises
        # SystemExit, which derives from BaseException and is therefore caught
        # by nothing in this file -- not `except Aborted`, not run_loop's
        # handler, not the back-out blocks. A single transient tesseract.exe
        # failure between a committed cancel and its relist killed the process
        # outright, leaving a dialog open and the item stranded, and it could
        # replace an in-flight FatalAbort on the way out.
        print(f"Tesseract failed ({result.stderr.decode(errors='replace').strip()}); "
              "treating this frame as unreadable.", file=sys.stderr)
        return []

    words: list[Word] = []
    # QUOTE_NONE: Tesseract emits a bare `"` as a word and never escapes it.
    # With default quoting that one word swallows every row after it into its
    # own text field, so those words vanish and the survivor carries the quote
    # glyph's coordinates -- clicks then land on the quote, not the button.
    reader = csv.DictReader(
        io.StringIO(result.stdout.decode("utf-8", errors="replace")),
        delimiter="\t", quoting=csv.QUOTE_NONE,
    )
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row["conf"])
            left, top = int(row["left"]), int(row["top"])
            width, height = int(row["width"]), int(row["height"])
        except (TypeError, ValueError, KeyError):
            continue
        if conf < min_conf:
            continue
        words.append(Word(
            text=text,
            left=region[0] + left // scale,
            top=region[1] + top // scale,
            right=region[0] + (left + width) // scale,
            bottom=region[1] + (top + height) // scale,
            conf=conf,
        ))
    return words


# Letters a lone digit is misread as, in this UI's font. Only unambiguous
# shapes are listed: 'D' is left out because it stood in for a 3 here, not a 0.
_DIGIT_LOOKALIKES = str.maketrans({
    "O": "0", "o": "0", "Q": "0",
    "I": "1", "i": "1", "l": "1", "|": "1",
    "Z": "2", "z": "2",
    "S": "5", "s": "5",
    "G": "6",
    "T": "7",
    "B": "8",
})


# Minimum light-to-dark range in a raw cell before it is believed to hold a
# glyph. Blank panel measures 33-110 on live frames, a real digit 231-232, so
# anything in that gap separates them; 160 is the middle of it.
INK_CONTRAST_MIN = 160


def _ink_box(prepared: Image.Image, threshold: int = 128):
    """Bounding box of the actual ink in a prepared (inverted) crop, or None.

    `ImageOps.invert(prepared).getbbox()` does NOT do this: getbbox() bounds
    every non-zero pixel, and _prep_for_text ends in autocontrast, so the
    background is near-white rather than white and inverts to near-zero rather
    than zero. Every background pixel therefore counts and the box is always
    the whole crop -- measured at 168x312 in 100% of ~240 samples, which made
    the guard that depends on it accept unconditionally.
    """
    return prepared.point(lambda v: 255 if v < threshold else 0).getbbox()


def read_number(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int],
    min_conf: float = 0.0,
) -> int | None:
    """The number written in a small cell, or None if there is none to read.

    `find_words` runs Tesseract in sparse-text mode, which needs something to
    segment: a lone digit in a narrow cell comes back with *no words at all*,
    at any confidence, so every single-digit quantity in the listings table
    read as None. Since quantity is what tells two stacks of the same item
    apart, that silently disabled the duplicate handling for small stacks.

    So: the ordinary read first, then a single-line, digits-only pass over the
    same pixels with blank margin added. The margin is padding, not more of the
    screenshot -- widening the crop would pull in the neighbouring column's
    digits, and this cell's neighbour is the price.
    """
    words = sorted(find_words(source, region, min_conf), key=lambda w: w.left)
    value = _digits("".join(w.text for w in words))
    if value is not None:
        return value

    tesseract = find_tesseract()
    if tesseract is None:
        return None
    image = source if isinstance(source, Image.Image) else Image.open(source)
    box = (max(0, region[0]), max(0, region[1]),
           min(image.width, region[2]), min(image.height, region[3]))
    if box[2] - box[0] < 4 or box[3] - box[1] < 4:
        return None

    # _prep_for_text inverts, so the background is white and the padding must
    # be white too, or the border reads as an inked edge.
    prepared = ImageOps.expand(_prep_for_text(image, box, 4), border=24, fill=255)

    # Is there anything here at all? Both rescue passes below return a glyph
    # for a completely blank crop -- measured, an empty strip of panel came
    # back as '1' in two cases out of three. That is worse than reading
    # nothing: an empty row reporting qty=1 is indistinguishable from a real
    # stack of 1, and locate_row narrows siblings on exactly that field.
    #
    # Tested on the RAW crop, not the prepared one: _prep_for_text ends in
    # autocontrast, which stretches the sensor noise in a blank panel across
    # the full range and manufactures ink out of nothing. Measured on live
    # frames the two populations do not overlap at all -- blank strips span
    # 33-110, every real digit cell 231-232.
    lo, hi = image.crop(box).convert("L").getextrema()
    if hi - lo < INK_CONTRAST_MIN:
        return None
    buf = io.BytesIO()
    prepared.save(buf, "PNG")
    try:
        result = subprocess.run(
            [tesseract, "stdin", "stdout", "--psm", "7",
             "-c", "tessedit_char_whitelist=0123456789,"],
            input=buf.getvalue(), capture_output=True, timeout=TESSERACT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode == 0:
        value = _digits(result.stdout.decode("utf-8", errors="replace"))
        if value is not None:
            return value

    # Last resort: the same crop with letters allowed. Tesseract reads this
    # font's '2' as 'Z', and the LSTM engine will not emit a character the
    # whitelist forbids -- it returns nothing instead. That is exactly why
    # every quantity of 2 read as None while 1 and 82 read fine.
    #
    # Only a single glyph that is *not already a digit* is accepted. A digit
    # that reads as a digit would have been found above, so seeing one here
    # means this pass is looking at a fragment of a longer number: '53' comes
    # back as '2)', and taking that 2 would report a stack of 53 as a stack of
    # 2. A wrong quantity is worse than an unread one -- it is what the
    # duplicate handling and sanity_check both key on.
    try:
        result = subprocess.run(
            [tesseract, "stdin", "stdout", "--psm", "10"],
            input=buf.getvalue(), capture_output=True, timeout=TESSERACT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None

    glyph = re.sub(r"[^0-9A-Za-z|]", "",
                   result.stdout.decode("utf-8", errors="replace"))
    if len(glyph) != 1 or glyph.isdigit():
        return None

    # psm 10 means "single character", so it returns ONE glyph whatever it is
    # shown -- given '53' it happily reports 'S'. Rejecting digits is not
    # enough, because the whole reason this pass exists is that this font's
    # digits come back as letters: 'S'->5 would turn a stack of 53 into 5.
    # So check the ink itself is one glyph wide before trusting it.
    ink = _ink_box(prepared)
    if ink is None:
        return None
    ink_w, ink_h = ink[2] - ink[0], ink[3] - ink[1]
    # 1.15, not 1.4: a real two-digit '23' measures 1.357 and slipped through.
    if ink_h <= 0 or ink_w > ink_h * 1.15:
        return None  # wider than one glyph: this is a fragment of a longer number
    return _digits(glyph.translate(_DIGIT_LOOKALIKES))


def find_text(
    source: Image.Image | Path | str,
    needle: str,
    region: tuple[int, int, int, int],
    min_conf: float = 40.0,
) -> list[Word]:
    """Words matching `needle` (case-insensitive), ordered top-to-bottom."""
    needle = needle.casefold()
    hits = [w for w in find_words(source, region, min_conf) if needle in w.text.casefold()]
    return sorted(hits, key=lambda w: w.top)


def _text_lines(words: list[Word], tolerance: int = 10) -> list[list[Word]]:
    """Group words into lines by vertical proximity, each ordered left to right."""
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.top, w.left)):
        for line in lines:
            if abs(word.centre[1] - line[0].centre[1]) <= tolerance:
                line.append(word)
                break
        else:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda w: w.left)
    return lines


def _minimal_window(line: list[Word], fragments: tuple[str, ...]) -> list[Word] | None:
    """Shortest run of words in `line` whose joined text holds every fragment.

    A "line" is only a band of similar y, so it collects whatever else happens
    to sit at that height. Taking its full extent as the match drags in
    unrelated text and shifts the centre by a hundred pixels or more: the Agent
    Shop nameplate shares a band with the Trade window's first row, and
    averaging the two put every click on the table instead of the NPC.
    """
    window = None
    for start in range(len(line)):
        joined = ""
        for end in range(start, len(line)):
            joined += line[end].text
            normalised = _normalise(joined)
            if all(fragment in normalised for fragment in fragments):
                span = line[start:end + 1]
                if window is None or len(span) < len(window):
                    window = span
                break
    return window


def _span_centre(words: list[Word]) -> tuple[int, int]:
    left = min(w.left for w in words)
    right = max(w.right for w in words)
    top = min(w.top for w in words)
    bottom = max(w.bottom for w in words)
    return ((left + right) // 2, (top + bottom) // 2)


def find_phrase(
    source: Image.Image | Path | str,
    phrase: str,
    region: tuple[int, int, int, int],
    min_conf: float = 40.0,
) -> tuple[int, int] | None:
    """Centre of `phrase`, tolerating OCR splitting it into several words.

    Ornate UI titles get chopped up -- "Inventory" comes back as 'I' plus
    'nventory' -- so whole-word matching misses them. This stitches each line
    back together before comparing.
    """
    target = _normalise(phrase)
    if not target:
        return None
    for line in _text_lines(find_words(source, region, min_conf)):
        if target not in _normalise("".join(w.text for w in line)):
            continue
        window = _minimal_window(line, (target,))
        if window is None:
            continue
        return _span_centre(window)
    return None


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------

class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long), ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KeyInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _InputUnion)]


INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
# One wheel notch. Windows sends multiples of this; how many LINES the target
# turns it into is the app's business, which is why the scroll offset is
# recovered by reading the table rather than by counting notches.
WHEEL_DELTA = 120
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12    # Alt. Module level: release_modifiers() needs it too.




def cooldown(seconds: float | None = None) -> None:
    """Wait after an input action. Centralised so the pace is tunable."""
    time.sleep(ACTION_COOLDOWN if seconds is None else seconds)


SEND_RETRY_PAUSE = 0.12

def _send(event: _Input, attempts: int = SEND_ATTEMPTS) -> None:
    """Inject one input event, retrying a refusal before giving up.

    SendInput returning 0 is usually TRANSIENT: a low-level hook, a moment
    where an elevated window owns the foreground, a desktop switch in
    progress. It was being treated as permanent -- one refusal raised
    PermissionError, which run_loop catches by BREAKING, so a single dropped
    event ended an unattended run. Measured in the recorded corpus: a 47-minute
    dead gap mid-cycle, with no abort recorded, because nothing retried and
    nothing survived to say why.

    The asymmetry was indefensible on its face: _release() already retries the
    same call three times for a key-UP, on the reasoning that a dropped release
    is dangerous. A dropped key-DOWN or click is no more permanent than that,
    and the cost of being wrong is a whole run.

    Genuine UIPI blocking does not clear on retry, so a real permission problem
    still raises -- it just has to fail every attempt first, which takes a
    fraction of a second and costs nothing.
    """
    for attempt in range(1, attempts + 1):
        try:
            if ctypes.windll.user32.SendInput(
                    1, ctypes.byref(event), ctypes.sizeof(_Input)) == 1:
                if attempt > 1:
                    print(f"  (input accepted on attempt {attempt})",
                          file=sys.stderr)
                return
        except OSError:
            pass
        if attempt < attempts:
            time.sleep(SEND_RETRY_PAUSE)
    raise PermissionError(CURSOR_BLOCKED_HINT)


def _release(event: _Input, what: str, attempts: int = 3) -> bool:
    """Release an input, retrying, and say so loudly if it will not go.

    Called from `finally` blocks, so it must not raise -- raising would mask
    the original error. But it must not stay SILENT either: SendInput reports
    a refusal in its return value, which these paths ignored entirely, so a
    dropped key-up looked exactly like a successful one.

    A dropped Ctrl-up is the worst case in this file. The game keeps believing
    Ctrl is held, and Ctrl+Click is the gesture that moves items into the shop
    slot -- so every ordinary click afterwards, on the NPC, on Change, on
    Confirmation, becomes an item move.
    """
    for _ in range(attempts):
        try:
            if ctypes.windll.user32.SendInput(
                    1, ctypes.byref(event), ctypes.sizeof(_Input)) == 1:
                return True
        except OSError:
            pass
        time.sleep(0.05)
    print(f"WARNING: could not release {what}; it may still be held down. "
          "Press it once by hand before continuing.", file=sys.stderr)
    return False


def _release_left_button() -> bool:
    return _release(_mouse_event(MOUSEEVENTF_LEFTUP), "the left mouse button")


def _release_key(vk: int) -> bool:
    return _release(_key_event(vk, up=True), f"key 0x{vk:02X}")


def release_modifiers() -> None:
    """Force every modifier up. Cheap insurance at the start of a cycle.

    If a previous cycle died with Ctrl held -- a refused key-up, a crash
    between press and release -- nothing else in the run would ever notice.
    """
    for vk in (VK_CONTROL, VK_MENU, VK_SHIFT):
        try:
            _release(_key_event(vk, up=True), f"modifier 0x{vk:02X}", attempts=1)
        except Exception:  # noqa: BLE001 - best effort only
            pass


def _mouse_event(flags: int) -> _Input:
    return _Input(type=INPUT_MOUSE,
                  u=_InputUnion(mi=_MouseInput(0, 0, 0, flags, 0, None)))


def _key_event(vk: int, up: bool) -> _Input:
    """Key event carrying both the virtual key and its scan code.

    Games that read the keyboard through DirectInput / raw input look at the
    scan code and ignore virtual-key-only events, so Ctrl appears unheld.
    """
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    return _Input(type=INPUT_KEYBOARD,
                  u=_InputUnion(ki=_KeyInput(vk, scan, flags, 0, None)))


VK_BACK = 0x08
VK_ESCAPE = 0x1B
# Backspaces sent before typing a value: enough to clear the widest price the
# field holds, without paying the typing cooldown for a long tail of no-ops.
CLEAR_KEYSTROKES = 13


def press_escape(settle: float = 0.5) -> None:
    """Tap Escape, which backs the game out to its default state.

    Sends a scan code, not just a virtual key -- the game ignores virtual-key
    only events, which is why an earlier attempt at this appeared to do nothing.
    """
    _send(_key_event(VK_ESCAPE, up=False))
    try:
        time.sleep(0.05)
    finally:
        _release_key(VK_ESCAPE)
    time.sleep(settle)
    cooldown()


def type_number(value: int, per_key: float = TYPE_COOLDOWN,
                clear_first: bool = True, clear: int | None = None) -> None:
    """Type digits into whatever field currently has focus.

    Paced at TYPE_COOLDOWN per keystroke -- slower than a burst, but the field
    drops characters when typed at machine speed.
    """
    def tap(vk: int) -> None:
        """Press and release, guaranteeing the release.

        A key left down triggers Windows auto-repeat, which would keep firing
        into whatever gains focus next.
        """
        _send(_key_event(vk, up=False))
        try:
            time.sleep(0.02)
        finally:
            _release_key(vk)
        time.sleep(per_key)

    if clear_first:
        # Enough backspaces to clear the widest price the field holds.
        # `clear` lets a caller size this to the field it is typing into.
        # CLEAR_KEYSTROKES is sized for the widest price; using it on the
        # 4-digit quantity field sent 9 no-op backspaces, costing 4.5s of
        # every relist -- and, if the field ignores Backspace when empty,
        # sending them to the game instead.
        for _ in range(CLEAR_KEYSTROKES if clear is None else clear):
            tap(VK_BACK)
    for ch in str(value):
        tap(0x30 + int(ch))
    cooldown()


def scroll_wheel(x: int, y: int, notches: int, settle: float = 0.35) -> None:
    """Turn the mouse wheel over (x, y). Negative scrolls DOWN (further into
    the list), positive scrolls UP, matching how Windows reports a real wheel.

    The cursor is moved first because the wheel goes to the window under the
    pointer, not to the focused one -- scrolling with the cursor parked
    somewhere else would scroll whatever is there instead.

    Sends one notch at a time with a pause between. A burst of notches in a
    single event is legal, but the game animates the list and coalescing them
    made the settle time unpredictable, which matters because the caller has
    to re-read the table afterwards to learn where it landed.
    """
    make_dpi_aware()
    if not move_mouse(x, y):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    # mouseData is unsigned in the struct, so a downward notch has to be sent
    # as its two's-complement value rather than a bare -120.
    step = (WHEEL_DELTA if notches > 0 else -WHEEL_DELTA) & 0xFFFFFFFF
    for _ in range(abs(notches)):
        _send(_Input(type=INPUT_MOUSE,
                     u=_InputUnion(mi=_MouseInput(0, 0, step,
                                                  MOUSEEVENTF_WHEEL, 0, None))))
        time.sleep(settle)
    cooldown()


def click(x: int, y: int, settle: float = 0.15) -> None:
    """Left-click at a screen coordinate.

    Raises PermissionError if Windows refuses the cursor move, which is the
    reliable signal that UIPI is blocking us (see CURSOR_BLOCKED_HINT).
    """
    make_dpi_aware()
    if not move_mouse(x, y):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    time.sleep(settle)

    # The button-up must fire even if something goes wrong in between: a left
    # button left logically down turns every later cursor move into a drag.
    _send(_mouse_event(MOUSEEVENTF_LEFTDOWN))
    try:
        time.sleep(0.09)
    finally:
        _release_left_button()
    time.sleep(0.05)
    cooldown()


def ctrl_click(x: int, y: int, settle: float = 0.15) -> None:
    """Ctrl+Left-click, which is how Cabal moves an item into the shop slot."""
    make_dpi_aware()
    if not move_mouse(x, y):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    time.sleep(settle)

    # Hold Ctrl well before and after the click: the game samples modifier
    # state on its own frame tick, so a brief hold can be missed entirely.
    _send(_key_event(VK_CONTROL, up=False))
    try:
        time.sleep(0.25)
        _send(_mouse_event(MOUSEEVENTF_LEFTDOWN))
        try:
            time.sleep(0.12)
        finally:
            # Release the button too, not just Ctrl: releasing only the
            # modifier left the button down while reporting a clean error.
            _release_left_button()
        time.sleep(0.25)
    finally:
        _release_key(VK_CONTROL)
        time.sleep(0.08)
    cooldown()


def grab() -> Image.Image:
    global _last_shot
    png, _, _, _, _ = take_screenshot()
    _last_shot = Image.open(io.BytesIO(png))
    return _last_shot


# --------------------------------------------------------------------------
# Recording frames for the test corpus
# --------------------------------------------------------------------------
#
# A live run sees states no amount of sitting and capturing will reproduce:
# the Registration Extension dialog exists for about a second, an empty shop
# row only exists between a cancel and its register, and the genuinely useful
# frames are the ones where something went wrong. So the run records them.
#
# It reuses the frame the step already captured -- `record()` never takes a
# screenshot of its own -- so the cost is a PNG write, not a capture. Frames
# are written alongside a JSONL index carrying the label and whatever context
# the call site knew, which is what makes them usable as test fixtures rather
# than just a pile of images.

RECORD_DIR = SCRIPT_DIR / "unit_tests" / "corpus"
_last_shot: "Image.Image | None" = None
_record_seq = 0
_record_full = False


def record(label: str, shot: "Image.Image | None" = None, /, **context) -> None:
    """Save the frame this step is already looking at, for later tests.

    Never raises and never captures: recording must not be able to change what
    a run does, or a failure this exists to capture would be caused by it.

    `label` and `shot` are positional-only so a caller can pass context named
    anything -- including `label=` or `shot=` -- without colliding with the
    signature. That is not hypothetical: `record("npc.found", label=...)`
    raised TypeError at argument-binding time, BEFORE the try below could
    swallow it, and killed three consecutive cycles. Argument binding is the
    one failure this function cannot catch, so it must be made impossible.
    """
    global _record_seq, _record_full
    if not RECORD_ENABLED or _record_full:
        return
    image = shot if shot is not None else _last_shot
    if image is None:
        return
    try:
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        if _record_seq == 0:
            # The HIGHEST existing number, not the count. Counting breaks the
            # moment a frame is deleted: with run_00002 removed, len() is 4
            # while run_00005 exists, so the next write silently OVERWRITES a
            # live frame -- and the index keeps the old entry, so the suite
            # then asserts one frame's recorded values against a different
            # image and reports confident, specific, wrong failures.
            #
            # That matters because pruning is the natural response to hitting
            # RECORD_LIMIT, i.e. exactly when someone is most likely to delete
            # frames.
            highest = 0
            for existing in RECORD_DIR.glob("run_*.png"):
                digits = existing.stem[4:]
                if digits.isdigit():
                    highest = max(highest, int(digits))
            _record_seq = highest
        if _record_seq >= RECORD_LIMIT:
            _record_full = True
            print(f"Frame recording stopped at {RECORD_LIMIT} frames.",
                  file=sys.stderr)
            return
        _record_seq += 1
        name = f"run_{_record_seq:05d}.png"
        image.save(RECORD_DIR / name)
        entry = {"file": name, "label": label,
                 "at": datetime.now().isoformat(timespec="seconds"),
                 # The layout the frame was READ under, not just when it was
                 # taken. Every search region is derived from LAYOUT, and
                 # Tesseract's sparse-text segmentation is crop-dependent -- so
                 # replaying a frame under a different layout can legitimately
                 # return a different answer from the same pixels.
                 #
                 # That is not hypothetical: calibration lands on origin (9,29)
                 # or (10,30) from OCR jitter alone, 11 times and 13 times in
                 # one day's runs. Replaying (9,29) frames under (10,30) shifted
                 # the NPC nameplate centre by up to 5px and failed 4 of 378,764
                 # corpus assertions -- a comparison the frames carried no way
                 # to make fair. Now they do.
                 "layout": {"origin": list(LAYOUT.origin),
                            "scale": round(LAYOUT.scale, 6),
                            "screen": list(LAYOUT.screen)}}
        # Context must never overwrite the three fields the index is keyed on.
        # Making label/shot positional-only stopped `record(..., label=x)` from
        # RAISING, but update() then let that same kwarg clobber the label
        # anyway -- which is worse than the crash, because it is silent: 15
        # frames were written with a coordinate where their label should be,
        # and `at=` overwrote the timestamp on 15 more. A colliding key is
        # kept under a prefixed name rather than dropped, so the caller's
        # value still reaches the index.
        for key, value in context.items():
            if value is None:
                continue
            entry["ctx_" + key if key in ("file", "label", "at") else key] = value
        with (RECORD_DIR / "run_index.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except Exception:  # noqa: BLE001 - recording is never worth a failure
        pass


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
UOI_NAME = 2
DESKTOP_SWITCHDESKTOP = 0x0100


def session_locked() -> bool:
    """True when the workstation is locked or a secure desktop is up.

    Locking switches the input desktop to Winlogon: screen captures come back
    black and injected input goes to the secure desktop, so every action would
    fail in confusing ways. Detecting it lets us say so plainly instead.
    """
    user32 = ctypes.windll.user32
    handle = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not handle:
        return True
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = ctypes.c_ulong()
        if user32.GetUserObjectInformationW(
            handle, UOI_NAME, name, ctypes.sizeof(name), ctypes.byref(needed)
        ):
            return name.value.casefold() != "default"
        return False
    finally:
        user32.CloseDesktop(handle)


def keep_awake(enable: bool = True) -> bool:
    """Hold off display and system sleep while a long run is in progress.

    Does NOT stop a manual lock (Win+L) or a screensaver set to require sign-in;
    nothing running as a normal user can prevent those.
    """
    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState.restype = ctypes.c_ulong
    flags = ES_CONTINUOUS
    if enable:
        flags |= ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    return bool(kernel32.SetThreadExecutionState(flags))


# --------------------------------------------------------------------------
# Window focus
# --------------------------------------------------------------------------



def find_game_window() -> int | None:
    """HWND of the Cabal window, or None."""
    user32 = ctypes.windll.user32
    found: list[int] = []

    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if GAME_TITLE_HINT.casefold() in buf.value.casefold():
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(proto(callback), None)
    return found[0] if found else None


def focus_game(settle: float = 0.35) -> bool:
    """Bring Cabal to the foreground so clicks hit controls instead of merely
    activating the window. Returns True if it ended up foreground."""
    user32 = ctypes.windll.user32
    hwnd = find_game_window()
    if hwnd is None:
        return False

    if user32.GetForegroundWindow() == hwnd:
        return True

    # SW_RESTORE only when actually minimised -- calling it on a maximised
    # window un-maximises it, which moves every control we just measured.
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(settle)

    if user32.GetForegroundWindow() == hwnd:
        return True

    # SetForegroundWindow is refused unless the caller owns the foreground.
    # Two ways round it, tried in turn:
    #   1. A synthetic Alt tap. Windows lifts the restriction for a process
    #      that has just received input, which is what unsticks the common
    #      case of another console (often an elevated one) holding focus.
    #   2. Borrowing the foreground and target threads' input state, so we
    #      count as owning the foreground for the duration of the call.
    try:
        _send(_key_event(VK_MENU, up=False))
        try:
            time.sleep(0.03)
        finally:
            # Always release Alt. This path only runs when input is already
            # being refused, so a failed release here is the likeliest way to
            # leave a modifier stuck -- after which every click is Alt+click.
            _release_key(VK_MENU)
        time.sleep(0.05)
    except PermissionError:
        _release_key(VK_MENU)  # keydown may have landed before the refusal

    user32.SetForegroundWindow(hwnd)
    time.sleep(settle)
    if user32.GetForegroundWindow() == hwnd:
        return True

    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    foreground_thread = user32.GetWindowThreadProcessId(
        user32.GetForegroundWindow(), None)
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    for thread in {target_thread, foreground_thread} - {current_thread, 0}:
        user32.AttachThreadInput(current_thread, thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        for thread in {target_thread, foreground_thread} - {current_thread, 0}:
            user32.AttachThreadInput(current_thread, thread, False)
    time.sleep(settle)
    return user32.GetForegroundWindow() == hwnd


# --------------------------------------------------------------------------
# Trade actions
# --------------------------------------------------------------------------

NAME_COLUMN = (275, 715)  # fallback only; normally derived from the headers
# The game's own label for a VACANT premium listing slot, as it appears in the
# name column: "Premium Exclusive Slot". Matched on the leading word after
# _normalise, because the name column clips it to "Premium Ex".
#
# This exists because treating that label as a misread made read_rows discard
# the entire table, which stopped a live run: a sold-out row was collected, the
# slot it vacated became a labelled premium slot, and the script then read zero
# listings from a perfectly readable screen until the failure breaker fired.
PREMIUM_SLOT_MARKER = "premium"
# Row spacing on the reference display; scaled through LAYOUT at runtime.
REF_ROW_PITCH = 79
# Every table row carries one of these in the Function column. Rows that are
# sold show Receive and empty slots show Register, so counting only Change
# buttons would renumber the rows relative to what is on screen.
BUTTON_WORDS = ("Change", "Receive", "Register")
# A single digit in the QTY column reads at low confidence; junk is filtered by
# the column bounds rather than by the confidence bar.
QTY_COL_MIN_CONF = 15.0
# Somewhere harmless to leave the cursor: the Trade window's title bar. Hovering
# a listing pops a large item tooltip that covers the table and wrecks the OCR.
PARK_POINT = (600, 45)


@dataclass
class Row:
    index: int
    name: str
    change: tuple[int, int]
    top: int
    bottom: int
    action: str  # 'change', 'receive' or 'register'
    price: int | None = None
    qty: int | None = None

    @property
    def cancellable(self) -> bool:
        return self.action == "change"


def park_cursor(settle: float = 0.0) -> None:
    """Move the cursor off the listings so no tooltip covers the table.

    Raises PermissionError like every other input path. Swallowing a refused
    move here left the cursor over a listing, whose tooltip then covered the
    table, and the run reported "no listings visible" instead of "input is
    blocked -- run as Administrator".
    """
    if not move_mouse(*PARK_POINT):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    # move_mouse already ends in cooldown(), so the tooltip has ACTION_COOLDOWN
    # to clear. `settle` used to add another 0.45s on top, making a park cost
    # ~0.95s -- the one input path that was not on the standard cooldown.
    if settle:
        time.sleep(settle)


# --------------------------------------------------------------------------
# Reopening the Trade window
# --------------------------------------------------------------------------

# Matched as a fragment: the in-world label OCRs imperfectly ("YÃƒÂ©ekaterina"),
# but the middle of the name survives.
NPC_NAME_FRAGMENT = "katerina"
# BOTH fragments must appear on the same OCR line. The name alone was too weak:
# it matched something transient while the character was at the Warehouse, and
# the resulting blind click landed in the game world -- which moves the
# character or interacts with whatever happens to be under it.
NPC_TITLE_FRAGMENT = "agentshop"
# Only the middle of the screen. The NPC's name also appears in the top
# target-nameplate banner, the chat log in the bottom-left and the sale
# notifications in the bottom-right -- none of which are the NPC.
NPC_SEARCH_REGION = (600, 150, 1900, 900)
# Belt and braces: reject a match landing in either bottom corner even if the
# search box is ever widened.
NPC_EXCLUDE_ZONES = (
    (0, 700, 900, 1440),      # bottom-left: chat
    (1850, 0, 2560, 1440),    # right: inventory panel and sale notifications
)
# The floating label sits above the model, centred on it, but how far above
# depends on the camera angle and how close you are standing -- a single fixed
# offset missed and the click went to the ground, which walks the character.
# So sweep a grid below the label, nearest its centre first, re-locating and
# verifying after each click.
NPC_CLICK_ATTEMPTS = 100
# Where the model usually sits relative to the centre of its name label.
NPC_BODY_OFFSET = (0, 120)
NPC_CLICK_OFFSET = NPC_BODY_OFFSET  # kept for callers wanting a single point


def _npc_click_offsets(attempts: int = NPC_CLICK_ATTEMPTS) -> tuple:
    """Click points around the model, ordered outward from its centre.

    Ordering matters more than coverage: the first few attempts should be the
    ones most likely to land on her, so the usual case still opens the shop in
    seconds and the long tail only costs anything when she is somewhere
    unexpected. Ties break towards the vertical centre line, then downward --
    a click below the model hits the ground, a click above it hits the label.
    """
    cx, cy = NPC_BODY_OFFSET
    # The grid is a distance below a nameplate, so it scales with the UI. On a
    # smaller screen the model is smaller and closer to its label, and an
    # unscaled grid would sweep past it entirely.
    step_y = max(4, LAYOUT.length(10))
    step_x = max(6, LAYOUT.length(15))
    span_y = LAYOUT.length(110), LAYOUT.length(160)
    span_x = LAYOUT.length(60)
    grid = [(cx + dx, cy + dy)
            for dy in range(-span_y[0], span_y[1] + 1, step_y)
            for dx in range(-span_x, span_x + 1, step_x)]
    # Vertical distance is discounted against horizontal. The label is drawn
    # centred over the model, so sideways error is small and fairly constant,
    # while how far above her it floats swings with the camera angle -- that is
    # the axis worth spending attempts on.
    grid.sort(key=lambda p: (4 * (p[0] - cx) ** 2 + (p[1] - cy) ** 2,
                             abs(p[0] - cx), p[1]))
    return tuple(grid[:attempts])


NPC_CLICK_OFFSETS = _npc_click_offsets()
# Per-attempt wait. Deliberately short: with a hundred candidates the sweep has
# to move on quickly, and a real open is detected by the panel probe below.
NPC_CLICK_WAIT = 1.5
# Wall clock for the whole sweep. At ~6s an attempt the full 100 points is
# ~10 minutes, and every miss is a move order that walks the character further
# from the NPC, so the attempt count alone is not a usable bound.
NPC_SWEEP_BUDGET = 120.0
# Consecutive attempts with no nameplate visible before giving up. The sweep
# falls back to the last known label, which is right for a one-frame OCR flake
# and wrong once the character has actually walked away.
NPC_LOST_LIMIT = 6
PURCHASE_TAB_WORD = "Purchase"
REGISTER_TAB_WORD = "Register"
# Detecting "the Trade window is up" must not hinge on one word OCRing: any of
# these appearing means it is open, whichever tab it opened on. Searched over a
# wider box than TRADE_REGION in case the window is not where the Register-tab
# layout puts it.
TRADE_OPEN_MARKERS = ("Purchase", "Adjust", "Register", "Function")
# Stops above the bottom-left chat log: a chat line containing "Register" or
# "Purchase" would otherwise report the window open while it is shut, so the
# NPC click is skipped and the run fails looking for the Register tab.
TRADE_WINDOW_SEARCH = (0, 0, 1700, 700)


def find_npc(
    source: Image.Image | None = None, retries: int = 4,
    seen: dict | None = None,
) -> tuple[int, int] | None:
    """Centre of the Agent Shop NPC's floating name label, or None.

    Callers add one of NPC_CLICK_OFFSETS to reach the model. The label sits
    over moving scenery and OCRs intermittently ("Yekaterina" one frame,
    "Yeekaterina" the next), so this retries on fresh frames.

    Pass `seen` to get back the frame she was actually found in, under key
    'shot'. Because the retries grab NEW frames, the caller's `_last_shot` is
    not necessarily the one that matched -- a frame recorded from it can show
    the Trade window covering her, which is exactly what one corpus frame
    labelled npc.found turned out to contain.

    Requires the name AND the "(Agent Shop)" title on the same line. Matching
    the name alone once latched onto something while the character was at the
    Warehouse, and the click that followed went into the open world.
    """
    def excluded(point: tuple[int, int]) -> bool:
        x, y = point
        return any(left <= x <= right and top <= y <= bottom
                   for left, top, right, bottom in NPC_EXCLUDE_ZONES)

    def label_centre(image: Image.Image, region=None) -> tuple[int, int] | None:
        for line in _text_lines(find_words(image, region or NPC_SEARCH_REGION, 25)):
            joined = _normalise("".join(w.text for w in line))
            if NPC_NAME_FRAGMENT not in joined or NPC_TITLE_FRAGMENT not in joined:
                continue
            # Only the words spelling the label. The nameplate sits at the same
            # height as the Trade window's first row, so the OCR line also held
            # that row's price, status and Change button; measuring across all
            # of it reported a centre ~190px left of the NPC, and every click
            # in the sweep landed on the table.
            window = _minimal_window(line, (NPC_NAME_FRAGMENT, NPC_TITLE_FRAGMENT))
            if window is None:
                continue
            return _span_centre(window)
        return None

    # Tesseract's sparse-text segmentation depends on the exact crop, and this
    # search region moves with the calibrated layout -- which is re-measured
    # every cycle and legitimately lands on origin (9,29) or (10,30) depending
    # on OCR jitter. Measured on a real frame: NPC_SEARCH_REGION (600,150,...)
    # finds nothing while (599,149,...) finds her at (1340,247), with her
    # nameplate sitting at 90-96% confidence either way. One pixel.
    #
    # So a miss is retried against a slightly wider crop before believing it.
    # Only on failure, so the common path still costs one pass.
    def look(image):
        for pad in (0, 8):
            box = NPC_SEARCH_REGION if pad == 0 else (
                max(0, NPC_SEARCH_REGION[0] - pad),
                max(0, NPC_SEARCH_REGION[1] - pad),
                NPC_SEARCH_REGION[2] + pad, NPC_SEARCH_REGION[3] + pad)
            got = label_centre(image, box)
            if got is not None and not excluded(got):
                return got
        return None

    for _ in range(retries):
        image = source if source is not None else grab()
        label = look(image)
        if label is not None:
            if seen is not None:
                seen["shot"] = image
            return label
        if source is not None:
            break  # a fixed frame will not change between retries
        time.sleep(0.4)
    return None


def panel_covers_trade_area(gap: float = 0.35, threshold: float = 2.0) -> bool:
    """Fast probe: is a static UI panel covering the Trade window's area?

    Two frames a moment apart. The 3D world animates constantly, so with no
    window open the area differs noticeably between them; an opaque UI panel
    barely changes at all. Costs ~0.8s against ~5s for the OCR check, which is
    what makes sweeping many click points practical. Indicative only -- always
    confirm with trade_window_open() before acting on it.
    """
    first = grab().convert("L").crop(TRADE_REGION)
    time.sleep(gap)
    second = grab().convert("L").crop(TRADE_REGION)
    diff = ImageChops.difference(first, second)
    data = list(getattr(diff, "get_flattened_data", diff.getdata)())
    return (sum(data) / len(data)) < threshold


def trade_window_open(source: Image.Image | None = None) -> bool:
    """True when the Trade window is up, on either tab."""
    image = source if source is not None else grab()
    return any(find_text(image, marker, TRADE_WINDOW_SEARCH)
               for marker in TRADE_OPEN_MARKERS)


def register_tab_open(source: Image.Image | None = None) -> bool:
    """True when the Trade window is showing the Register tab."""
    image = source if source is not None else grab()
    return find_phrase(image, "Register Item", REGISTER_PANEL) is not None


def open_trade_window(timeout: float = 15.0, verbose: bool = True) -> bool:
    """Make sure the Trade window is open on the Register tab.

    The game closes it on its own sometimes; this clicks the Agent Shop NPC to
    reopen it and switches to Register. Already-open is a no-op.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    def wait_until(predicate, limit: float) -> bool:
        deadline = time.monotonic() + limit
        while True:
            if predicate():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)

    if register_tab_open():
        return True

    if not trade_window_open():
        # Try each offset from the label in turn, re-locating the label every
        # time: a miss lands on the ground, which walks the character and moves
        # both the label and the NPC, so a stale point would miss again.
        # `seen` so the recorded frame is the one she was matched in. Without
        # it the fallback to _last_shot can save a later frame in which the
        # Trade window covers her -- a frame labelled npc.found that does not
        # contain the NPC is worse than no frame, because a test then asserts
        # against it.
        seen: dict = {}
        label = find_npc(seen=seen)
        if label is None:
            say("Lady Yekaterina (Agent Shop) is not on screen - walk to "
                "her before running this. Nothing was clicked.")
            return False
        record("npc.found", seen.get("shot"), centre=str(label))
        say(f"Found 'Lady Yekaterina (Agent Shop)' at {label}; sweeping "
            f"{len(NPC_CLICK_OFFSETS)} points beneath it.")

        opened = False
        index = 0
        lost = 0
        # A miss is a move order, so an unbounded sweep does not just waste
        # time -- it walks the character away from the NPC, one click at a
        # time. Both limits below exist to stop that: a wall clock, because at
        # ~6s an attempt the full sweep is ~10 minutes of clicking into the
        # world, and a lost-nameplate count, because `or label` deliberately
        # keeps the last known point and would otherwise keep firing at a
        # coordinate the character has already left.
        sweep_deadline = time.monotonic() + NPC_SWEEP_BUDGET
        for index, offset in enumerate(NPC_CLICK_OFFSETS, start=1):
            if time.monotonic() >= sweep_deadline:
                say(f"  giving up after {index - 1} attempts "
                    f"({NPC_SWEEP_BUDGET:g}s budget spent).")
                break

            # Re-locate every time: a miss lands on the ground, which walks the
            # character, moving both the nameplate and the NPC. One frame only
            # -- find_npc's default retries poll on fresh frames, which is the
            # right trade for a single lookup but not a hundred of them.
            fresh = find_npc(retries=1)
            lost = 0 if fresh else lost + 1
            if lost >= NPC_LOST_LIMIT:
                say(f"  the nameplate has not been visible for {lost} attempts "
                    "- she is out of view, so stopping rather than clicking "
                    "the world blind.")
                break
            label = fresh or label

            point = (label[0] + offset[0], label[1] + offset[1])
            say(f"  try {index}/{len(NPC_CLICK_OFFSETS)}: {point}  "
                f"({offset[0]:+}, {offset[1]:+} from the name)")
            click(*point)
            time.sleep(NPC_CLICK_WAIT)

            # Cheap probe first; only pay for the OCR check when it looks open.
            if panel_covers_trade_area() and trade_window_open():
                opened = True
                break

        if not opened:
            say(f"The Trade window did not open after {index} attempt(s) "
                "beneath the nameplate. Stand closer so she is fully on "
                "screen, then retry.")
            return False
    record("shop.opened", attempts=index)

    if register_tab_open():
        return True

    tabs = find_text(grab(), REGISTER_TAB_WORD, TRADE_REGION)
    if not tabs:
        say("Could not find the Register tab.")
        return False
    tab = tabs[0]  # the tab sits above the table's Register buttons
    record("tab.before_register_click", centre=str(tab.centre))
    click(*tab.centre)
    if not wait_until(register_tab_open, timeout):
        say("The Register tab did not open.")
        return False
    record("tab.register_open")
    return True


def refresh_table(timeout: float = 20.0, verbose: bool = True) -> bool:
    """Click the Trade window's Refresh button and wait for the reload.

    Worth doing before acting: the client's copy of the table goes stale, so a
    row can read On Sale here while the server already has it sold.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    # One capture, used for both the button search and the record: the pair
    # before/after a refresh is the only evidence of what the reload changed,
    # which is what makes a stale-table bug reconstructable afterwards.
    shot = grab()
    record("refresh.before", shot)
    buttons = find_text(shot, "Refresh", TRADE_REGION)
    if not buttons:
        record("refresh.no_button", shot)
        say("Refresh button not found - is the Trade window open?")
        return False

    click(*buttons[-1].centre)
    time.sleep(0.6)
    if not wait_for_table(max(timeout, 20.0)):
        record("refresh.timeout")
        say("The table did not finish refreshing.")
        return False
    record("refresh.after")
    return True


def find_change_buttons(source: Image.Image | Path | str | None = None) -> list[Word]:
    """The visible 'Change' buttons, ordered top-to-bottom."""
    image = source if source is not None else grab()
    return find_text(image, "Change", TRADE_REGION)


def find_row_buttons(image: Image.Image,
                     words: "list[Word] | None" = None) -> list[Word]:
    """One button per visible table row, ordered top-to-bottom.

    `words` lets a caller that has already OCR'd the table pass the result in,
    instead of paying for three more full-region passes.
    """
    if words is None:
        hits: list[Word] = []
        for word in BUTTON_WORDS:
            hits.extend(find_text(image, word, TRADE_REGION))
    else:
        wanted = tuple(w.casefold() for w in BUTTON_WORDS)
        hits = [w for w in words
                if any(t in w.text.casefold() for t in wanted)
                and w.conf >= 40.0]
    if not hits:
        return []

    # The left rail has its own Register button; keep only the Function column,
    # located from the Change buttons (or whatever else is most common).
    anchors = [w for w in hits if "change" in w.text.casefold()] or hits
    xs = sorted(w.centre[0] for w in anchors)
    column_x = xs[len(xs) // 2]
    hits = [w for w in hits if abs(w.centre[0] - column_x) <= 45]

    # Two words can land on one row (OCR splitting); keep one per y band.
    hits.sort(key=lambda w: w.top)
    deduped: list[Word] = []
    for w in hits:
        if deduped and w.top - deduped[-1].top < 20:
            continue
        deduped.append(w)
    return deduped


def table_loading(source: Image.Image | Path | str) -> bool:
    """True while the Trade window shows 'Waiting for the server response'.

    During a refresh every row reads Register and the counts read 0, so reading
    the table then yields a confidently wrong answer.
    """
    return bool(find_text(source, "Waiting", TRADE_REGION))


def wait_for_table(timeout: float = 20.0, poll: float = 1.0) -> bool:
    """Block until the table has finished refreshing. False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not table_loading(grab()):
            return True
        time.sleep(poll)
    return False


def _header(words: "list[Word] | None", image: Image.Image, needle: str):
    """The topmost header word, from a pre-OCR'd list when one is supplied."""
    if words is None:
        hits = find_text(image, needle, TRADE_REGION)
    else:
        low = needle.casefold()
        hits = sorted((w for w in words
                       if low in w.text.casefold() and w.conf >= 40.0),
                      key=lambda w: w.top)
    return hits[0] if hits else None


def name_column(image: Image.Image,
                words: "list[Word] | None" = None) -> tuple[int, int]:
    """x-range of the Name column, derived from the Name and QTY headers so it
    tracks the Trade window being resized."""
    header = _header(words, image, "Name")
    qty_header = _header(words, image, "QTY")
    names = [header] if header else []
    qtys = [qty_header] if qty_header else []
    if not names or not qtys:
        return NAME_COLUMN
    header, qty = names[0], qtys[0]
    centre = (header.left + header.right) // 2
    half = qty.left - centre
    if half <= 0:
        return NAME_COLUMN
    left, right = max(0, centre - half + 4), qty.left - 6
    # A reversed box makes PIL raise mid-sequence, possibly after a cancel has
    # committed; fall back rather than crash.
    if right - left < 40:
        return NAME_COLUMN
    return (left, right)


def price_column(image: Image.Image,
                 words: "list[Word] | None" = None) -> tuple[int, int] | None:
    """x-range of the Price column, bounded by the QTY and Status headers."""
    qty_header = _header(words, image, "QTY")
    status_header = _header(words, image, "Status")
    if qty_header is None or status_header is None:
        return None
    qtys, statuses = [qty_header], [status_header]
    left, right = qtys[0].right + 4, statuses[0].left - 4
    return (left, right) if right > left else None


def await_rows(timeout: float = TABLE_READ_BUDGET, poll: float = 0.5) -> list[Row]:
    """Read the listings, retrying until the table is non-empty.

    A single read comes back empty often enough -- mid-refresh, a tooltip over
    the table, plain OCR flake -- that using one as a gate turns a working table
    into "the Trade window must be closed".

    The budget is measured in whole reads, not seconds: a full 10-row table
    costs ~18s of OCR, so any timeout under that permits exactly one attempt
    and the retry never happens.
    """
    # Park the cursor here rather than trusting every caller to. A tooltip over
    # the table produced a confidently wrong row on 23% of unparked captures --
    # a real listing reading as an empty slot, with all ten buttons present and
    # evenly spaced, so no guard fired. Only 2 of 8 call sites parked first,
    # and one that did not is the re-read that decides whether to withdraw a
    # listing. Best-effort: a refused move must not stop a read.
    try:
        park_cursor()
    except PermissionError:
        pass

    deadline = time.monotonic() + max(timeout, TABLE_READ_BUDGET)
    while True:
        shot = grab()
        # A table mid-refresh is not an empty table: every row reads "Register"
        # and the counts read 0, so read_rows returns ten confidently wrong
        # rows. Callers then either skip them all as empty slots and report
        # "All rows processed" having done nothing, or filter them out and
        # report live listings "already sold out". Only the empty result was
        # ever retried, so the wrong answer went straight through.
        if not table_loading(shot):
            rows = read_rows(shot)
            if rows:
                return rows
        if time.monotonic() >= deadline:
            return []
        time.sleep(poll)


def read_rows(source: Image.Image | Path | str) -> list[Row]:
    """Every visible table row, numbered top-to-bottom as displayed.

    Rows are anchored on the Function-column button, then the name is read from
    that button's vertical band.
    """
    image = source if isinstance(source, Image.Image) else Image.open(source)

    # ONE OCR pass for the whole table, then slice it locally.
    #
    # This used to run ~37 separate Tesseract invocations per read -- three for
    # the buttons, four for the column headers, and one per name, price and
    # quantity cell -- costing about 7s. A single pass over the same region
    # returns the same words in 0.66s, and the row/column logic below is
    # unchanged: it filters a list instead of cropping and re-OCRing.
    #
    # min_conf=0 here because the callers below apply their own bars: 40 for
    # names, prices and headers, QTY_COL_MIN_CONF for quantities. Collecting at
    # the lowest bar and filtering up keeps each of those exactly as it was.
    words = find_words(image, TRADE_REGION, 0.0)
    if not words:
        return []

    buttons = find_row_buttons(image, words)
    if not buttons:
        return []

    # The shop is a fixed EXPECTED_ROWS slots and every slot always carries one
    # of BUTTON_WORDS -- empty ones read "Register" -- so anything else is a
    # partial read, not a short table. This is the only check that catches a
    # button missed at the TOP or BOTTOM: those leave the remaining buttons
    # evenly spaced, so the gap guard below sees nothing wrong while every row
    # is numbered one out and "cancel row 7" hits row 8. await_rows() retries
    # on a fresh frame, so rejecting here costs a read, not the run.
    if len(buttons) != EXPECTED_ROWS:
        return []

    # Scaled, because it is a distance inside the Trade window. Only reached
    # when a single button is visible, which the cardinality gate above already
    # rejects -- kept so the value is never silently a reference-machine pixel.
    pitch = LAYOUT.length(REF_ROW_PITCH)
    if len(buttons) > 1:
        gaps = sorted(b.top - a.top for a, b in zip(buttons, buttons[1:]))
        pitch = gaps[len(gaps) // 2]  # median, not mean: one gap must not skew it
        # A gap of roughly two pitches means a button failed to OCR. Row
        # numbers are assigned by counting buttons, so a missed one shifts
        # every row below it and "cancel row 7" would hit row 8.
        if any(gap > pitch * 1.6 for gap in gaps):
            return []

    left, right = name_column(image, words)
    price_bounds = price_column(image, words)

    def cell(x0: int, y0: int, x1: int, y1: int, min_conf: float = 40.0):
        """The already-OCR'd words inside a cell, in place of a fresh pass.

        Matched on the word's centre, so a glyph box straddling a boundary
        belongs to exactly one cell -- the same rule a crop applied, since a
        crop cut the glyph and Tesseract then read whichever part it had.
        """
        return [w for w in words
                if w.conf >= min_conf
                and x0 <= w.centre[0] <= x1 and y0 <= w.centre[1] <= y1]

    rows: list[Row] = []
    for i, button in enumerate(buttons, start=1):
        cy = button.centre[1]
        top, bottom = cy - pitch // 2, cy + pitch // 2
        # Drop icon noise from the name cell. Items with option slots draw an
        # empty socket box inside the name, which Tesseract reads as a short
        # junk token -- 'an' at confidence 40.2 against a 40.0 bar, 'mm', '=o'.
        # Whether it lands above or below the bar changes the item's NAME, and
        # identity matching is exact, so a row would flip between matching and
        # "already sold out" frame to frame. A real name word is either longer
        # than two characters or read confidently.
        cell_words = [w for w in cell(left, top, right, bottom)
                      if len(w.text.strip()) > 2 or w.conf >= NAME_FRAGMENT_MIN_CONF]
        # Group into lines by proximity, as _text_lines does, then read each
        # line left to right. Bucketing on `w.top // 12` sorted by a fixed
        # 12-pixel grid instead: two words on the SAME line whose tops straddle
        # a bucket boundary -- routine, since Tesseract reports the glyph box
        # and capitals sit higher -- came out in the wrong order. That made
        # "Master's SIGMetal Headgear (FA)" read as "Master's Headgear
        # SIGMetal (FA)", which matches nothing, and the row was reported
        # "already sold out". Data-dependent, so it fired intermittently.
        name = " ".join(w.text for line in _text_lines(cell_words) for w in line)

        # An empty name on a row that is plainly a live listing means the bulk
        # pass could not segment it, NOT that the slot is vacant -- re-read
        # just that cell before believing it.
        #
        # Tesseract's sparse-text segmentation depends on the crop it is given.
        # Over the whole 2450x2070 table it drops names it reads perfectly from
        # a cell-sized image: measured on a real listing, the full-table pass
        # returned only the socket icon while the same pixels cropped to the
        # cell gave 'Dragonium Daikatana of Outrageous + 5' at 93% confidence.
        # That listing is worth 298,000,021 Alz, and read_rows called it
        # '(empty)' -- which relist_rows skips as a vacant slot, silently, while
        # reporting the cycle a success.
        #
        # Costs one extra OCR pass, and only for rows that would otherwise be
        # reported nameless. A genuinely empty slot has no button text either,
        # so this cannot resurrect one: it is gated on the row having a real
        # action word.
        if not name.strip() and button.text.strip().casefold() != "register":
            retry = [w for w in find_words(image, (left, top, right, bottom), 40.0)
                     if len(w.text.strip()) > 2 or w.conf >= NAME_FRAGMENT_MIN_CONF]
            if retry:
                name = " ".join(w.text for line in _text_lines(retry)
                                for w in line)

        # Join the words before parsing. Taking max() over separate words means
        # a price OCR splits -- "105," + "000,000" -- reads as the fragment 105.
        #
        # Take a single LINE, not everything in the band: the band is a full
        # row pitch tall and centred on the button rather than on the text, so
        # it catches the neighbouring row's price, whose digits then interleave
        # by x into one nonsense number.
        price = None
        if price_bounds:
            cell_lines = _text_lines(
                cell(price_bounds[0], top, price_bounds[1], bottom))
            if cell_lines:
                nearest = min(cell_lines,
                              key=lambda ln: abs(_span_centre(ln)[1] - cy))
                price = _digits("".join(w.text
                                        for w in sorted(nearest,
                                                        key=lambda w: w.left)))

        # QTY sits between the Name and Price columns. A lone digit there OCRs
        # at low confidence, so this needs a lower bar than the default.
        # Join, as with the price: "12" splitting into "1" and "2" read as 2,
        # which made sanity_check withdraw a correct listing for "wrong qty".
        qty = None
        if price_bounds and price_bounds[0] - (right + 2) >= 8:
            box = (right + 2, top, price_bounds[0] - 2, bottom)
            digits = sorted(cell(*box, QTY_COL_MIN_CONF), key=lambda w: w.left)
            qty = _digits("".join(w.text for w in digits))
            if qty is None:
                # The single-digit rescue still needs its own targeted passes:
                # a lone digit in this narrow cell returns NO words from the
                # bulk pass at any confidence -- measured at ~30% of quantity
                # cells -- which is exactly what read_number's psm-7/psm-10
                # fallbacks exist for. Only reached when the bulk pass found
                # nothing, so it costs a subprocess for those cells alone
                # rather than for all ten.
                qty = read_number(image, box, QTY_COL_MIN_CONF)

        action = button.text.strip().casefold()
        # An empty slot may or may not have an empty name cell, and the
        # difference matters enormously.
        #
        # A row saying "Register" whose name cell holds an ITEM name is a
        # misread: every caller treats action='register' as "nothing here", so
        # a live listing would be skipped silently. That is worth discarding
        # the whole frame over.
        #
        # But the game LABELS its own vacant premium slots "Premium Exclusive
        # Slot", in the name column, at 96% confidence. The original comment
        # here guessed that was "a screen overlay". It is not -- it is the
        # game, and treating it as a misread threw away every row in the table.
        #
        # Measured cost of that mistake: 522 of 2,575 recorded frames, and one
        # live run stopped. A sold-out row was collected, the slot it vacated
        # became a labelled premium slot, read_rows returned nothing for the
        # full 45-second budget, and three cycles later the failure breaker
        # ended the run. The table was perfectly readable the whole time.
        if action == "register" and name.strip():
            if PREMIUM_SLOT_MARKER in _normalise(name):
                name = ""            # a vacant slot, exactly as it says
            else:
                return []
        rows.append(Row(
            index=i, name=name.strip() or "(empty)", change=button.centre,
            top=top, bottom=bottom, action=action,
            price=price, qty=qty,
        ))
    return rows


def dialog_button(
    source: Image.Image | Path | str, word: str, min_conf: float = 40.0
) -> Word | None:
    """A dialog button by label. Takes the lowest match, since the title bar can
    repeat the word and the buttons sit along the bottom.

    The table's own Change/Receive buttons are excluded by DISTANCE FROM THE
    FUNCTION COLUMN, not by an absolute x. "Receive" appears on both a table
    row and the Confirm Receipt dialog, so they must be told apart -- but an
    absolute boundary only works while the dialog sits to the right of the
    table, which is true only if the dialog and the Trade window scale
    together. If the game keeps its UI at a fixed size on a smaller screen the
    dialog is centred on the client and lands LEFT of the table, and a
    boundary tuned for the reference machine filters out every real dialog
    button -- so a cancel aborts after its Change click has committed.
    """
    column_x = LAYOUT.x(REF_FUNCTION_COLUMN_X)
    keep_away = max(40, LAYOUT.length(60))
    hits = [w for w in find_text(source, word, POPUP_REGION, min_conf)
            if abs(w.centre[0] - column_x) > keep_away]
    return hits[-1] if hits else None


def await_dialog_button(
    word: str, timeout: float = 6.0, poll: float = 0.4
) -> Word | None:
    """Wait for a dialog button to be readable.

    A dialog that is up stays up, so a miss means OCR flaked on that frame, not
    that the button is absent -- retry on fresh frames, then once more with a
    lower confidence bar before giving up.
    """
    deadline = time.monotonic() + timeout
    while True:
        button = dialog_button(grab(), word)
        if button is not None:
            return button
        if time.monotonic() >= deadline:
            break
        time.sleep(poll)
    # Last resort: the same frame often does contain the word, just faintly.
    return dialog_button(grab(), word, min_conf=15.0)


def _mentions(texts: list[str], keyword: str, threshold: float = 0.85) -> bool:
    """True when one of `texts` is `keyword`, tolerating a slipped character.

    A dialog is identified by a single word, so one misread character decides
    whether the script thinks it is there: "Extension" came back as
    "Exteneion", the Registration Extension dialog read as "no dialog at all",
    and a cancel aborted after its Change click had already gone in.

    Fuzziness is dangerous in the other direction, though: "Register" sits on
    every empty row's button and all over the Register panel, a stem away from
    "Registration". Similarity alone does separate them (0.70 against a 0.85
    bar), but not by much once the word on screen is itself misread, so the
    length guard rejects the pairing outright rather than relying on that gap.
    """
    from difflib import SequenceMatcher

    for text in texts:
        if keyword in text:
            return True
        if abs(len(text) - len(keyword)) > 2:
            continue
        # A clipped fragment of the right word. Dialog titles render as light
        # text on a blue gradient, and _prep_for_text autocontrasts the whole
        # region at once -- so the title goes grey-on-grey while the body and
        # buttons stay crisp, and Tesseract clips its ends. Measured on a real
        # capture with a Confirm Receipt modal plainly up: the only usable
        # token was 'eceip', which scores 0.833 against 'receipt' and missed
        # the 0.85 bar by 0.017, so dialog_kind reported no dialog at all.
        #
        # Placed BELOW the length guard on purpose: that guard is what stops
        # 'register' (8) matching 'registration' (12), and this line inherits
        # it. Lowering the threshold instead would move toward 'receive'
        # (0.714), which sits on every sold row of the table.
        if text in keyword:
            return True
        if SequenceMatcher(None, keyword, text).ratio() >= threshold:
            return True
    return False


def dialog_kind(source: Image.Image | Path | str) -> str | None:
    """Which dialog is up: 'extension', 'confirm', 'receipt', or None.

    Clicking Change opens the Registration Extension dialog ([Register]
    [Cancel]); its Cancel leads to a confirmation dialog ([Confirmation]
    [Cancel]) that actually pulls the listing. Both put their buttons in the
    same place, so they must be told apart by their text, not geometry.
    """
    # Match against JOINED LINES as well as individual words, at a low
    # confidence bar.
    #
    # Two measured failures, both of which produced "the Registration
    # Extension dialog did not appear" while the dialog was plainly up:
    #
    #  * Tesseract's segmentation is crop-dependent. The Trade window's own
    #    title reads at 96% confidence in a tight crop and is NOT FOUND AT ALL
    #    when handed a POPUP_REGION-sized crop -- the size this function always
    #    uses. Ornate title glyphs are exactly what it drops at that scale.
    #  * A split title defeats whole-word matching: ['regi','stration',
    #    'exten','sion'] matches nothing. This UI splits ornate titles
    #    routinely -- find_phrase exists because "Inventory" arrives as "I" +
    #    "nventory" -- and dialog_kind was the only title-matching path in the
    #    file that did not stitch a line back together first.
    words = find_words(source, POPUP_REGION, DIALOG_TEXT_MIN_CONF)
    texts = [_normalise(w.text) for w in words]
    texts += [_normalise("".join(w.text for w in line))
              for line in _text_lines(words)]
    if _mentions(texts, "receipt"):
        return "receipt"
    if _mentions(texts, "confirmation"):
        return "confirm"
    # Either word of the title will do. They fail independently -- "Extension"
    # misread while "Registration" read at 96% confidence on the same frame --
    # so requiring one specific word makes the check as weak as its worst word.
    if _mentions(texts, "extension") or _mentions(texts, "registration"):
        return "extension"
    return None


def dialog_present(source: Image.Image | Path | str | None = None) -> bool:
    """Is ANY dialog on screen? Deliberately harder to fool than dialog_kind.

    dialog_kind decides by reading the TITLE, and its own docstring records why
    that is fragile: Tesseract's segmentation is crop-dependent and drops
    ornate title glyphs at POPUP_REGION scale, so it returns None with a modal
    plainly up. Every dialog also carries a Cancel button, which survives that
    failure -- close_any_dialog already prefers the button finder for exactly
    this reason, and says so.

    Trusting dialog_kind alone to say "nothing is open" is what turned one bad
    row into two dead cycles on 2026-08-04. The abort path asked it, got None
    while the diagnostic one line earlier had read 'extension', closed nothing,
    and left a modal covering the table. Escape could then not close the Trade
    window, and every read for the rest of that cycle and the whole of the next
    returned no rows.

    Answering "is something open" with a false NO is the expensive direction.
    A false YES only costs a harmless Cancel click.
    """
    shot = source if source is not None else grab()
    if dialog_kind(shot) is not None:
        return True
    return dialog_button(shot, DISMISS_WORD) is not None


def confirm_open_dialogs(settle: float = 0.8, verbose: bool = True) -> bool:
    """Click Confirmation through however many dialogs are stacked up.

    Recovery for a run that stopped mid-chain: the game can queue a second
    confirmation (a price warning) behind the first.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    confirmed = 0
    for step in range(1, MAX_CONFIRM_STEPS + 1):
        # The accept button is Confirmation on most dialogs but Receive on
        # Confirm Receipt, so try both before deciding nothing is open.
        button = await_dialog_button(CONFIRM_WORD, timeout=3.0)
        label = CONFIRM_WORD
        if button is None:
            button = await_dialog_button(RECEIPT_WORD, timeout=5.0)
            label = RECEIPT_WORD
        if button is None:
            say(f"Nothing left to confirm after {confirmed} click(s).")
            break
        say(f"Dialog {step}: clicking {label} at {button.centre}")
        click(*button.centre)
        confirmed += 1
        time.sleep(settle)
    return await_dialog(None, timeout=6.0) is not None


def close_any_dialog(settle: float = 0.7, tries: int = 4) -> bool:
    """Back out of any open dialog without changing anything.

    Both dialogs carry a Cancel button. On the confirmation dialog it closes
    outright; on the Extension dialog it advances to the confirmation dialog,
    whose Cancel then closes both. The game ignores Escape here, so this walks
    the chain with clicks instead.
    """
    # Driven by the button finder rather than dialog_kind(): a single flaked
    # read would otherwise report success with a dialog still on screen.
    for _ in range(tries):
        button = await_dialog_button(DISMISS_WORD, timeout=3.0)
        if button is None:
            break
        click(*button.centre)
        time.sleep(settle)
    return await_dialog(None, timeout=6.0) is not None


def _normalise(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


# Characters OCR routinely swaps for one another in this UI. Folding them
# together lets "S1GMetal" and "SIGMetal" compare equal while leaving a real
# difference like "(FA)" vs "(FB)" intact.
# Only the confusions that cannot collide with a real item difference. Folding
# digits like 6/9 or 2/7 would make "+6 Blade" and "+9 Blade" compare EQUAL,
# and match_row's exact branch would then return the wrong item with full
# confidence, bypassing every threshold below.
_OCR_CONFUSIONS = str.maketrans({"0": "o", "1": "i", "l": "i"})

# Fuzzy fallback bar. It sits above the measured similarity of genuinely
# different items -- "SIGMetal Headgear(FA)" and "(FB)" score 0.944 -- because
# below that there is no value that separates a noisy read of the right item
# from a clean read of the wrong one.
FUZZY_NAME_THRESHOLD = 0.95
FUZZY_NAME_MARGIN = 0.03
# A row this similar to what we are looking for, which still failed the bar
# above, means "the name did not read cleanly" -- not "the listing is gone".
# Well below FUZZY_NAME_THRESHOLD on purpose: this is not a match, it is
# evidence that something very like the target is still on screen.
UNMATCHED_NAME_SIMILARITY = 0.70
# A one- or two-character token in a name cell is almost always an option-
# socket icon, not a word. Below this confidence it is dropped, so an item's
# identity does not change with the icon's OCR luck.
NAME_FRAGMENT_MIN_CONF = 60.0


def _canonical(text: str) -> str:
    """Normalised and with OCR-confusable characters folded together."""
    return _normalise(text).translate(_OCR_CONFUSIONS)


# Glyphs a letter can be misread as, folded BEFORE punctuation is stripped.
# Used only for matching an item against its price floor -- never for telling
# two items apart, where this much folding would be reckless.
#
# _canonical cannot serve here: _normalise deletes non-alphanumerics outright,
# so "V|P" becomes "vp" and stops matching "vip", and the floor silently
# vanishes. Folding towards a match is the safe direction for a floor: applying
# too high a floor to the wrong item only means it does not sell, while missing
# one means a 105M item can be listed for whatever the market says.
_FLOOR_LOOKALIKES = str.maketrans({
    "|": "i", "!": "i", "1": "i", "l": "i", "[": "i", "]": "i",
    "/": "i", "\\": "i", "j": "i", ":": "i", ";": "i", "¦": "i",
    "0": "o",
})


def _floor_key(text: str) -> str:
    """Canonical form for price-floor matching. See _FLOOR_LOOKALIKES."""
    folded = text.casefold().translate(_FLOOR_LOOKALIKES)
    return "".join(ch for ch in folded if ch.isalnum())


# A table cell carries a second line after the item name -- "Yekaterina VIP
# Membership" then "Use Period: 30 days" -- and a tooltip adds its own trailer.
# Everything from these markers on is descriptive, not part of the name.
_NAME_TRAILER = re.compile(
    r"\b(use\s*period|duration|grade\s*\d|drop\s*not\s*allowed|register\s*cost"
    r"|item\s*sold|remaining\s*time|sales?\s*price)\b.*",
    re.IGNORECASE | re.DOTALL,
)


def item_name(row_name: str) -> str:
    """The item's name with the descriptive trailer removed."""
    return _NAME_TRAILER.sub("", row_name).strip(" :-")


def match_rows(rows: list[Row], item: str,
               threshold: float = FUZZY_NAME_THRESHOLD) -> list[Row]:
    """Every row naming `item`, in table order.

    Several rows legitimately carry the same name -- two separate stacks of the
    same core -- so this returns all of them and leaves the choice to the
    caller (see `locate_row`). Exact match on the OCR-folded name first; only
    if nothing matches exactly does it fall back to similarity, and then it
    demands both a high score and a clear lead over the runner-up. Item names
    here differ by a single character, so a near-miss is far more likely to be
    the wrong item than a misread of the right one.
    """
    from difflib import SequenceMatcher

    needle = _canonical(item)
    if not needle:
        return []

    exact = [r for r in rows if _canonical(r.name) == needle]
    if exact:
        return exact

    scored = []
    for row in rows:
        candidate = _canonical(row.name)
        if candidate:
            scored.append((SequenceMatcher(None, needle, candidate).ratio(), row))
    if not scored:
        return []

    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]
    best_name = _canonical(best.name)

    # Measure the margin against the best DIFFERENTLY-named row, and return
    # every row sharing the winning name.
    #
    # Taking scored[1] blindly made fuzzy matching structurally impossible
    # exactly where it was needed most: with two rows of the same item, the
    # runner-up IS the identical twin, the margin is ~0, and this returned []
    # for both. locate_row read that as 'missing' and the batch reported two
    # perfectly good stacks "already sold out" -- the very misreport the
    # duplicate handling was built to end.
    runner_up = next((score for score, row in scored[1:]
                      if _canonical(row.name) != best_name), 0.0)

    # Scale the bar with the name's length. A single substituted character
    # scores about 1 - 1/n, so a fixed 0.95 only rejects names shorter than 20
    # characters: "Master's SIGMetal Headgear (FA)" vs "(FB)" scores 0.96 and
    # would otherwise pass as a match.
    required = max(threshold, 1.0 - 1.0 / max(len(needle), 1) + 0.005)
    if best_score < required or best_score - runner_up < FUZZY_NAME_MARGIN:
        return []
    return [row for _, row in scored if _canonical(row.name) == best_name]


def match_row(rows: list[Row], item: str,
              threshold: float = FUZZY_NAME_THRESHOLD) -> Row | None:
    """The one row naming `item`, or None when there is not exactly one.

    Use this only where a second row of the same name genuinely means "cannot
    proceed". Anywhere a duplicate name is normal -- the shop happily holds two
    stacks of the same item -- use `locate_row`, which tells them apart.
    """
    found = match_rows(rows, item, threshold)
    return found[0] if len(found) == 1 else None


@dataclass(frozen=True)
class RowRef:
    """Enough of a listing to find it again once the table has been redrawn.

    A name alone is not an identity: the shop happily holds two stacks of the
    same item, and matching on the name then finds two rows and gives up.
    Quantity, then price, then position among the same-named rows narrow it
    down -- in that order, because quantity survives a relist unchanged while
    price is exactly what a relist is meant to alter.
    """
    name: str
    qty: int | None = None
    price: int | None = None
    ordinal: int = 0  # 0-based position among rows sharing this name

    @classmethod
    def of(cls, row: Row, rows: list[Row] | None = None) -> "RowRef":
        # The ordinal counts only rows that are indistinguishable from this one
        # in every readable respect -- the same set `locate_row` is left
        # holding after its filters. Counting over a wider set (all rows
        # sharing the name) would index into a narrower pool and land on the
        # wrong sibling, or off the end of it.
        ordinal = 0
        if rows:
            for position, candidate in enumerate(cls._siblings(rows, row)):
                if candidate.index == row.index:
                    ordinal = position
                    break
        return cls(row.name, row.qty, row.price, ordinal)

    @staticmethod
    def _siblings(rows: list[Row], row: Row) -> list[Row]:
        """The pool locate_row will be left holding, narrowed the same way.

        It must mirror locate_row exactly, or the ordinal counts positions in
        one set and is then used to index another. Filtering unconditionally
        (including None == None) diverged whenever a quantity or price was
        unreadable -- routine for the QTY column -- and the ordinal then
        pointed at a different row than the one it was measured from.
        """
        pool = match_rows(rows, row.name)
        for value, attribute in ((row.qty, "qty"), (row.price, "price")):
            if value is None:
                continue
            narrowed = [r for r in pool if getattr(r, attribute) == value]
            if narrowed:
                pool = narrowed
        return pool


# --------------------------------------------------------------------------
# Scrolling the listings table
# --------------------------------------------------------------------------
#
# The shop holds more listings than the ten on screen. Scrolling is only safe
# if the script knows WHICH listings it is looking at afterwards, and read_rows
# numbers rows by screen position -- so after a scroll, "row 1" is a different
# listing with nothing to signal it. Every caller that acts on an index would
# be acting on the wrong one.
#
# Measured on the live shop:
#   * one wheel notch moves exactly one row
#   * the row bands do NOT move; only content scrolls, so a screen position's
#     click target is stable
#   * scrolling clamps at both ends, so over-scrolling is safe and idempotent
#
# The offset is therefore recovered by matching content across two reads, never
# by counting notches. Stepping ONE row at a time leaves nine of ten rows
# overlapping, which over-determines the answer; a seven-row step leaves
# exactly three, the bare minimum, and a twenty-row step leaves none.

# Beyond any plausible list length. Over-scrolling clamps, so this is how the
# view is driven to a known end rather than tracked with a running counter --
# a counter drifts, and re-deriving from a known end cannot.
SCROLL_TO_END_NOTCHES = 40
# Rows that must overlap before an offset is believed.
MIN_SCROLL_OVERLAP = 3


def _row_key(row: Row) -> tuple:
    """What makes two sightings the same listing, for scroll matching."""
    return (row.name, row.price, row.qty, row.action)


def measure_shift(before: list[Row], after: list[Row],
                  minimum: int = MIN_SCROLL_OVERLAP) -> int | None:
    """How many rows the view moved down, or None if it cannot be told.

    Returns EVERY offset that fits and refuses unless exactly one does. With
    duplicate listings more than one can fit -- 41 of 43 recorded tables carry
    rows identical in name, quantity and price -- and taking the first would
    silently pick the wrong one.
    """
    b = [_row_key(r) for r in before]
    a = [_row_key(r) for r in after]
    if not b or not a:
        return None
    fits = []
    for d in range(-len(b), len(b) + 1):
        overlap = [(i, i + d) for i in range(len(b)) if 0 <= i + d < len(a)]
        if len(overlap) >= minimum and all(b[i] == a[j] for i, j in overlap):
            fits.append(-d)
    return fits[0] if len(fits) == 1 else None


def scroll_to_end(up: bool, timeout: float = 8.0,
                  verbose: bool = True) -> list[Row] | None:
    """Drive the view to the top (up) or bottom, and return what is showing.

    Relies on the clamp: asking for more than the list can give is a no-op, so
    this needs no knowledge of how long the list is.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    centre = ((TRADE_REGION[0] + TRADE_REGION[2]) // 2,
              (TRADE_REGION[1] + TRADE_REGION[3]) // 2)
    scroll_wheel(*centre, SCROLL_TO_END_NOTCHES if up else -SCROLL_TO_END_NOTCHES)
    park_cursor()
    rows = await_rows(timeout)
    if not rows:
        say("  the table could not be read after scrolling.")
        return None
    return rows


def scroll_one(down: bool, before: list[Row], timeout: float = 8.0,
               verbose: bool = True) -> tuple[list[Row] | None, int | None]:
    """Move the view exactly one row. Returns (rows_after, shift).

    `shift` is measured from content, and is 0 when the view is already at the
    end -- which is how the caller learns it has seen everything. Anything
    other than 0 or 1 means the wheel did something unexpected, and the caller
    must stop rather than reinterpret it.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    centre = ((TRADE_REGION[0] + TRADE_REGION[2]) // 2,
              (TRADE_REGION[1] + TRADE_REGION[3]) // 2)
    scroll_wheel(*centre, -1 if down else 1)
    park_cursor()
    after = await_rows(timeout)
    if not after:
        say("  the table could not be read after scrolling.")
        return None, None
    shift = measure_shift(before, after)
    if shift is None:
        say("  could not tell how far the view moved - refusing to guess "
            "which listing is which.")
        return after, None
    want = 1 if down else -1
    if shift not in (0, want):
        say(f"  one notch moved {shift} rows, expected 0 or {want} - stopping "
            "rather than reinterpreting it.")
        return after, None
    return after, shift


# Rows to move in one verified step. Seven leaves exactly MIN_SCROLL_OVERLAP
# rows in common between the two reads, which is the least that pins the offset
# down. Larger steps leave nothing to match against and the offset becomes a
# guess; one-at-a-time is safe but costs a full table read (~18s of OCR) per
# row, which is minutes per listing once the shop is thirty deep.
SCROLL_STEP = 7
# Enough chunks to walk a full shop top to bottom, with headroom.
MAX_SCROLL_CHUNKS = 8


def scroll_chunk(notches: int, before: list[Row], timeout: float = 8.0,
                 verbose: bool = True) -> tuple[list[Row] | None, int | None]:
    """Move the view down by up to `notches` rows, verified against content.

    Like scroll_one but in steps big enough to be affordable. `shift` is 0 when
    the view is already at the bottom, which is how the caller learns it has
    seen everything. A shift outside 0..notches means the wheel did something
    unexpected, and the caller must stop rather than reinterpret it.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    centre = ((TRADE_REGION[0] + TRADE_REGION[2]) // 2,
              (TRADE_REGION[1] + TRADE_REGION[3]) // 2)
    scroll_wheel(*centre, -abs(notches))
    park_cursor()
    after = await_rows(timeout)
    if not after:
        say("  the table could not be read after scrolling.")
        return None, None
    shift = measure_shift(before, after)
    if shift is None:
        say("  could not tell how far the view moved - refusing to guess "
            "which listing is which.")
        return after, None
    if not 0 <= shift <= abs(notches):
        say(f"  {abs(notches)} notch(es) moved the view {shift} rows - "
            "stopping rather than reinterpreting it.")
        return after, None
    return after, shift


def bring_into_view(ref: RowRef, timeout: float = 8.0,
                    verbose: bool = True) -> list[Row] | None:
    """Scroll until the listing `ref` names is on screen; return that view.

    Walks down from the top in verified chunks. Deliberately does NOT take an
    absolute position: relist() closes the Trade window after every row, so the
    view is back at the top each time this is called, and a position measured
    when the batch started is stale by the time later rows are reached --
    cancelling one listing renumbers everything below it. Identity does not go
    stale, so identity is what this searches by.

    Returns the view containing the listing. If the whole shop is walked
    without a match, returns the LAST view read rather than None, so the
    caller's own locate_row reports 'missing' -- which means the listing sold,
    a normal outcome. None is reserved for "the table could not be read",
    which is not.
    """
    rows = scroll_to_end(up=True, timeout=timeout, verbose=verbose)
    if not rows:
        return None

    def holds(view: list[Row]) -> bool:
        live = [r for r in view if r.action in ("change", "receive")]
        return locate_row(live, ref)[0] is not None

    for _ in range(MAX_SCROLL_CHUNKS):
        if holds(rows):
            return rows
        after, shift = scroll_chunk(SCROLL_STEP, rows, timeout=timeout,
                                    verbose=verbose)
        if after is None or shift is None:
            return None
        rows = after
        if shift == 0:
            break                       # clamped: the bottom is on screen
    return rows


def enumerate_listings(timeout: float = 8.0,
                       verbose: bool = True) -> list[tuple[int, Row]] | None:
    """Every listing in the shop, paired with its absolute position.

    Walks from the top one row at a time. Absolute index 1 is the first
    listing in the shop, independent of what is on screen.

    Returns None rather than a partial list if the view is ever lost: a
    half-enumerated shop is indistinguishable from a complete one to the
    caller, and acting on it would act on the wrong listings.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    rows = scroll_to_end(up=True, timeout=timeout, verbose=verbose)
    if not rows:
        return None

    found: list[tuple[int, Row]] = [(i + 1, r) for i, r in enumerate(rows)]
    top = 1                      # absolute index of screen row 1
    steps = 0
    # Bounded: the shop cannot hold more listings than this, and an unbounded
    # loop here would scroll for ever if the shift ever read 1 without the
    # view actually moving.
    while steps < SCROLL_TO_END_NOTCHES:
        steps += 1
        after, shift = scroll_one(True, rows, timeout=timeout, verbose=verbose)
        if after is None or shift is None:
            return None
        if shift == 0:
            break                # clamped: the bottom is on screen
        top += shift
        rows = after
        # Only the row that just arrived at the bottom is new.
        index = top + len(rows) - 1
        if index > len(found):
            found.append((index, rows[-1]))
    else:
        say(f"  still scrolling after {steps} steps - refusing to continue.")
        return None

    say(f"  {len(found)} listing(s) in the shop "
        f"({len(found) - EXPECTED_ROWS} beyond the first screen)")
    return found


def listing_family(rows: list[Row], name: str,
                   price: int | None) -> list[Row]:
    """Live rows sharing a listing's name and price.

    The unit the collect check counts. Module level, not a closure inside
    relist(), so the tests can drive the real thing against real recorded
    tables instead of a reimplementation that can drift from it.
    """
    pool = [r for r in match_rows(rows, name)
            if r.action in ("change", "receive")]
    if price is not None:
        # Filtered STRICTLY, unlike locate_row. locate_row ignores a filter
        # that matches nothing, because there an empty pool means "lost the
        # row" and an unread price should not cause that. Here an empty family
        # is the correct answer: it means every stack at that price is gone.
        #
        # Copying the fallback was a real bug. With two stacks of one item at
        # DIFFERENT prices, collecting the only stack at its price left no row
        # matching, the filter was ignored, and the family widened to the
        # other stack -- so a stack that had never sold was read as the
        # remainder and relisted. That is a registration fee and a wrong price
        # on a listing nobody asked to touch.
        #
        # The failure this trades against is milder: if a price fails to OCR
        # on the later frame its row drops out, the stack reads as fully
        # collected, and a remainder waits for the next cycle.
        pool = [r for r in pool if r.price == price]
    return pool


def family_quantities(pool: list[Row]) -> list:
    """The family's quantities, ordered so two readings compare directly."""
    return sorted((r.qty for r in pool), key=lambda q: (q is None, q))


def collect_delta(before: list, after: list) -> tuple[list, list]:
    """(lost, gained): the multiset difference between two family readings.

    This is what tells a collect apart from a dropped click without needing to
    know WHICH row is which -- the question that has no answer when two stacks
    are identical in name, quantity and price.

        lost=[q], gained=[]    the stack went: fully sold and collected
        lost=[q], gained=[n]   partial sale, n is the remainder
        lost=[],  gained=[]    nothing moved: the click did not take
    """
    unmatched = list(before)
    gained = []
    for value in after:
        if value in unmatched:
            unmatched.remove(value)
        else:
            gained.append(value)
    return unmatched, gained


def locate_row(rows: list[Row], ref: RowRef,
               strict: bool = False) -> tuple[Row | None, str]:
    """The row `ref` points at, plus a note, tolerating duplicate names.

    Returns (row, note). `note` is '' when the row was identified outright,
    'missing' when nothing carries the name at all, and 'ambiguous' when
    several rows do and `strict` forbade guessing between them.

    Missing and ambiguous must never be conflated. "Missing" means the listing
    sold and was collected; "ambiguous" means the script cannot see which of
    two rows it is looking at. Reporting the latter as the former is what made
    an unsold stack look collected and skipped it.
    """
    same = match_rows(rows, ref.name)
    if not same:
        # "No confident match" is NOT "the listing is gone". match_rows also
        # returns nothing when a row is there but its name fell under the
        # fuzzy bar -- and that bar rejects a single substituted character by
        # construction. Callers treat 'missing' as "it sold", so conflating
        # the two turns one flaked glyph into a live stack being skipped and
        # the cycle reporting success.
        from difflib import SequenceMatcher

        needle = _canonical(ref.name)
        near = max((SequenceMatcher(None, needle, _canonical(r.name)).ratio()
                    for r in rows if _canonical(r.name)), default=0.0)
        if near >= UNMATCHED_NAME_SIMILARITY:
            return None, "unmatched"
        return None, "missing"
    if len(same) == 1:
        return same[0], ""

    pool = same
    for attribute, value in (("qty", ref.qty), ("price", ref.price)):
        if value is None:
            continue
        narrowed = [r for r in pool if getattr(r, attribute) == value]
        if len(narrowed) == 1:
            return narrowed[0], ""
        if narrowed:
            pool = narrowed

    if strict:
        return None, "ambiguous"

    # Identical in name, quantity and price. Take them in the order they were
    # first seen rather than refusing outright.
    #
    # Position is the only thing left to go on, and the table may have been
    # reordered since, so this can land on a sibling that was already relisted
    # earlier in the batch. That is survivable: a relist only moves a row out
    # of this pool by changing its price, so a sibling still sitting at the old
    # price is either untouched or was relisted at a price the market says is
    # already correct -- and relisting an already-correct listing changes
    # nothing. What must never happen is silently reporting it as sold.
    chosen = pool[ref.ordinal] if 0 <= ref.ordinal < len(pool) else pool[0]
    priced = f"at {ref.price:,} Alz" if ref.price is not None else "price unread"
    return chosen, (f"{len(pool)} rows are identical ({ref.name!r} x{ref.qty} "
                    f"{priced}); taking row {chosen.index} by position")


# --------------------------------------------------------------------------
# Inventory grid and the Register panel
# --------------------------------------------------------------------------

# Offset from the centre of the "Inventory" title to the centre of slot (1,1),
# and the slot pitch. Measured on the maximised 2560x1440 layout; anchoring on
# the title lets the panel be dragged without breaking.
SLOT_ONE_OFFSET = (-261, 120)
SLOT_PITCH = (73.9, 74.1)
GRID_SIZE = 8

# Register panel, left rail of the Trade window.
REGISTER_PANEL = (10, 120, 275, 1040)
# Starts right of the radio column: PANEL_RADIO_X is 39, and including it let
# the radio glyph OCR as a digit and prepend itself to the price -- a filled
# radio read as "8" turns 1,234,567 into 81,234,567.
PRICE_ROWS = (70, 460, 260, 530)   # the two suggested-price rows
PRICE_FIELD = (40, 545, 204, 573)  # the free-text price box
QTY_FIELD = (40, 634, 226, 667)
NET_SALES_ROWS = (30, 700, 265, 800)
SHOP_SLOT = (144, 290)             # the Register Item box, centre
SHOP_SLOT_BOX = (30, 179, 256, 399)  # the box itself, for occupancy checks
# An empty box is near-uniform dark; any item icon lifts the spread well past this.
SHOP_SLOT_STDEV = 20.0
QTY_INPUT = (90, 651)              # the editable number in "N / MAX"
# The separator in "N / MAX" OCRs badly -- '/' comes back as '[' at ~33%
# confidence -- so this field needs a lower bar than the rest of the UI.
QTY_MIN_CONF = 15.0
LOAD_ATTEMPTS = 3


def wants_max_quantity(name: str) -> bool:
    lowered = name.casefold()
    if any(token in lowered for token in NO_MAX_QUANTITY_ITEMS):
        return False
    return MAXIMISE_ALL_QUANTITIES
# Top row is the week's average price, bottom row the lowest currently listed.
# Identify the bottom row by where it sits, not by counting rows: OCR sometimes
# reads only one of the two, and a count check cannot tell which one it got.
PRICE_TOP_Y = 477
PRICE_BOTTOM_Y = 513
PRICE_ROW_Y_TOL = 14

# A corrupted name must still look like the catalogue entry to earn its floor.
FLOOR_NAME_SIMILARITY = 0.75
# ...and a token hit must not be wildly unlike it, which is what rejects
# 'V|pgrade Core(High)' (0.21) while accepting any real VIP read (>= 0.9).
FLOOR_TOKEN_MIN_SIMILARITY = 0.40
# How short a name may be, relative to the catalogue entry, and still earn that
# entry's floor by SIMILARITY alone. Similarity cannot separate a catalogue
# name from a shorter, different item whose name sits inside it: plain
# "Unbinding Stone" scores 0.8235 against "Siena's Unbinding Stone" -- clear of
# the 0.75 bar -- and would take its 71M floor, parking a far cheaper item at a
# price nobody pays. Real OCR damage barely changes length (a substitution not
# at all), so this costs nothing where it matters: measured over 8,000
# corruptions of both catalogue names, it loses zero floors.
#
# SET TO 0.0 ON PURPOSE -- this guard is disabled, and that is the decision.
#
# It was added to stop the plain "Unbinding Stone" (a separate, far cheaper
# item) claiming Siena's 71M floor: it folds to 'unbindingstone', 14 chars,
# scoring 0.8235 against the 20-char catalogue key. The guard worked for that.
#
# What it also did was lose the floor whenever CHARACTERS DROP OUT of a real
# name. Measured over deletion sweeps: at 5 lost characters, 12,482 of 12,501
# Siena floor losses were caused solely by this constant; at 6, 51,977 of
# 52,145. The justification comment claimed "8,000 corruptions, zero losses" --
# true only for substitutions, which barely move the length.
#
# The two cases are genuinely indistinguishable: "Siena's Unbinding Stone" with
# its first word lost folds to exactly the same key as the cheap item. No
# threshold separates them, so this is a choice about which way to fail:
#
#   guard ON  -> a 71,000,000 item can list at a cheap item's price. Money gone.
#   guard OFF -> a cheap item can list at 71,000,000. It does not sell.
#
# The standing instruction on floors is unambiguous, and the second failure
# costs nothing but a listing nobody buys. Raise this only if that changes.
FLOOR_LENGTH_RATIO = 0.0

# How many single Escape presses to try when backing out to a clean state.
ESCAPE_ATTEMPTS = 3

# relist() outcomes. "Fully sold" is a success, not a failure: there is simply
# nothing left to relist, and a batch should move on rather than stop.
RELISTED = "relisted"
SOLD_OUT = "sold_out"
FAILED = "failed"


def choose_price(
    suggested: int,
    price_floor: int = 0,
    floor_price: int | None = None,
    absolute_floor: int = 0,
) -> tuple[int, str]:
    """Decide the listing price. Returns (price, why) -- why is '' if the
    market's suggestion was used unchanged.

    Take the lowest currently listed price; when there is none, use
    FALLBACK_PRICE. Two things override that:

      * `absolute_floor` -- a per-item minimum from ITEM_PRICE_FLOORS that
        binds ALWAYS (VIP is never listed below 105M, whatever the market
        says). This is a standing requirement, not a tunable.
      * `price_floor` -- an explicit --floor for one command, which refuses
        rather than substituting a number.
    """
    if suggested <= 0:
        # No lowest-current-price to take, so park it high rather than guess.
        return max(FALLBACK_PRICE, absolute_floor), \
            "no market price; using the fallback"

    # An explicit --floor is the only thing that refuses outright.
    if price_floor and suggested < price_floor:
        raise Aborted(
            f"suggested {suggested:,} is below the --floor {price_floor:,}"
        )

    # DO NOT REMOVE. Per-item absolute floors bind unconditionally -- a VIP is
    # never listed below ITEM_PRICE_FLOORS regardless of what the market says.
    # This outranks "always take the lowest current price": that rule decides
    # WHICH market figure to use, this one decides how low the listing may go.
    if absolute_floor and suggested < absolute_floor:
        return absolute_floor, (
            f"market {suggested:,} is below the {absolute_floor:,} floor for "
            "this item; listing at the floor"
        )

    return suggested, ""


def item_price_floor(name: str) -> int:
    """Absolute floor for a listing name, or 0 if none applies.

    Two independent routes, because neither alone is good enough:

      * the token ("vip") as a substring -- fast, but a 3-character target is
        tiny, so one bad glyph in V, I or P loses it entirely; and
      * similarity of the WHOLE name against the catalogue entry -- which
        survives any single corruption, because the other 22 characters carry
        the match.

    A token hit additionally has to clear a low similarity bar. That is what
    stops a folded 'V|pgrade Core(High)' -- which really does contain "vip" --
    from claiming a 110,000,000 floor and parking 158 cores nobody will buy.

    Compared against both the full folded name and its leading window, so a
    descriptive trailer that item_name() did not strip cannot dilute the score.
    """
    from difflib import SequenceMatcher

    key = _floor_key(item_name(name))
    if not key:
        return 0

    best = 0
    for token, catalogue, floor in ITEM_PRICE_FLOORS:
        reference = _floor_key(catalogue)
        if not reference:
            continue
        ratio = max(
            SequenceMatcher(None, reference, key).ratio(),
            SequenceMatcher(None, reference, key[:len(reference)]).ratio(),
        )
        token_hit = _floor_key(token) in key
        # The length guard applies to the similarity route ONLY. The token
        # route is deliberately exempt: a name clipped by a misjudged column
        # keeps its leading token ("siena", "vip"), and rescuing exactly that
        # read is what the token route is for. Guarding both would trade a
        # cosmetic over-match for the one failure that must never happen.
        long_enough = len(key) >= len(reference) * FLOOR_LENGTH_RATIO
        if (ratio >= FLOOR_NAME_SIMILARITY and long_enough) or (
                token_hit and ratio >= FLOOR_TOKEN_MIN_SIMILARITY):
            best = max(best, floor)
    return best


def strictest_price_floor() -> int:
    """The highest floor any item carries.

    Used when the item cannot be named. Refusing to guess would block ordinary
    manual use, and guessing 0 is what let `--register` list a VIP unfloored,
    so an unidentified item takes the strictest floor on the books instead --
    too high never sells anything cheap, which is the direction to fail in.
    """
    # `*_, floor`, not `_, floor`: ITEM_PRICE_FLOORS carries (token, catalogue,
    # floor). Unpacking it as a pair raised ValueError on every explicitly
    # priced registration -- after the item had already been Ctrl+Clicked into
    # the shop slot, and reported by run_sequence as "non-numeric argument".
    return max((floor for *_, floor in ITEM_PRICE_FLOORS), default=0)
PANEL_RADIO_X = 39                 # x of the price radio buttons


INVENTORY_TITLE_REGION = (1400, 100, 2560, 300)
# Offset from the Alz box's (right, top) to the Inventory title centre. The Alz
# digits are right-aligned, so the right edge holds still as the number changes.
ALZ_TO_TITLE = (-241, -718)


def inventory_origin(
    source: Image.Image | None = None, retries: int = 3
) -> tuple[int, int] | None:
    """Anchor for the inventory grid, expressed as the title's centre.

    Prefers the Alz box: it is found by colour rather than OCR, and measured
    identical across frames, whereas the ornate "Inventory" title sits over
    moving game art and OCRs intermittently (sometimes as 'I' + 'nventory',
    sometimes not at all). The title is kept only as a fallback.

    Costs a scan, so callers needing many slots should resolve it once.
    """
    for _ in range(retries):
        image = source if source is not None else grab()

        box = find_alz(image)
        if box:
            return (box[2] + ALZ_TO_TITLE[0], box[1] + ALZ_TO_TITLE[1])

        centre = find_phrase(image, "Inventory", INVENTORY_TITLE_REGION)
        if centre is not None:
            return centre

        if source is not None:
            break  # a fixed frame will not change between retries
        time.sleep(0.4)
    return None


def slot_centre_at(origin: tuple[int, int], row: int, col: int) -> tuple[int, int]:
    """Screen centre of slot (row, col) given the panel anchor. Pure arithmetic."""
    if not (1 <= row <= GRID_SIZE and 1 <= col <= GRID_SIZE):
        raise ValueError(f"slot ({row},{col}) is outside the {GRID_SIZE}x{GRID_SIZE} grid")
    tx, ty = origin
    return (
        round(tx + SLOT_ONE_OFFSET[0] + SLOT_PITCH[0] * (col - 1)),
        round(ty + SLOT_ONE_OFFSET[1] + SLOT_PITCH[1] * (row - 1)),
    )


def slot_centre(row: int, col: int, source: Image.Image | None = None) -> tuple[int, int]:
    """Screen centre of inventory slot (row, col), both 1-based."""
    origin = inventory_origin(source)
    if origin is None:
        raise Aborted("could not find the Inventory panel - is it open?")
    return slot_centre_at(origin, row, col)


# Inventory tabs I..VIII, measured from the panel anchor.
TAB_ONE_OFFSET = (-281, 52)
TAB_PITCH = 69.2
TAB_COUNT = 8
# How far above the median a tab must sit to count as the selected one.
TAB_ACTIVE_MARGIN = 6.0
# An empty slot measures near 0; an item icon lifts the spread well past this.
SLOT_OCCUPIED_STDEV = 8.0

SLOT_INSET = 26          # half-width of the sampled area inside a slot
SLOT_CHANGE_MIN = 6.0    # mean per-pixel delta that counts as "something moved"
SLOT_CHANGE_MARGIN = 2.0 # the winner must beat the runner-up by this factor


def inventory_cells(
    image: Image.Image, origin: tuple[int, int]
) -> dict[tuple[int, int], Image.Image]:
    """Grayscale interior of every inventory slot, keyed by (row, col).

    Takes the anchor rather than finding it, so slicing 64 slots costs no OCR.
    """
    cells = {}
    for r in range(1, GRID_SIZE + 1):
        for c in range(1, GRID_SIZE + 1):
            cx, cy = slot_centre_at(origin, r, c)
            # Clamp to the image: PIL pads an out-of-bounds crop with black
            # instead of erroring, and the padding inflates the cell's spread
            # so an empty slot reads as occupied for ever.
            box = (max(0, cx - SLOT_INSET), max(0, cy - SLOT_INSET),
                   min(image.width, cx + SLOT_INSET),
                   min(image.height, cy + SLOT_INSET))
            cells[(r, c)] = image.crop(box).convert("L")
    return cells


def occupied_slots(
    image: Image.Image, origin: tuple[int, int]
) -> list[tuple[int, int]]:
    """Inventory slots holding an item, judged by pixel spread not by OCR."""
    found = []
    for key, cell in inventory_cells(image, origin).items():
        data = list(getattr(cell, "get_flattened_data", cell.getdata)())
        mean = sum(data) / len(data)
        stdev = (sum((p - mean) ** 2 for p in data) / len(data)) ** 0.5
        if stdev >= SLOT_OCCUPIED_STDEV:
            found.append(key)
    return sorted(found)


def require_empty_work_tab(verbose: bool = True) -> bool:
    """Tab WORK_TAB must be empty before a run.

    Cancelling a large stack scatters items across tabs; if the tab already
    holds something, the before/after diff cannot tell which slots the cancel
    filled, and the script could pick up an unrelated item.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    origin = inventory_origin()
    if origin is None:
        record("worktab.no_panel")
        say("The Inventory panel is not visible - open it and rerun.")
        return False

    if not select_inventory_tab(WORK_TAB, origin):
        record("worktab.tab_switch_failed", want=WORK_TAB)
        say(f"Could not switch to inventory tab {WORK_TAB}.")
        return False

    park_cursor()
    occupied = occupied_slots(grab(), origin)
    if occupied:
        where = ", ".join(f"{r},{c}" for r, c in occupied[:12])
        more = f" (+{len(occupied) - 12} more)" if len(occupied) > 12 else ""
        # THE blind step. This refusal is the most common cycle-killer after a
        # strand -- it is what every cycle hits once an item is left behind --
        # and it recorded nothing, so three consecutive deaths here left an
        # empty index and a five-hour outage with no attributable cause.
        record("worktab.not_empty", tab=WORK_TAB, occupied=len(occupied),
               slots=", ".join(f"{r},{c}" for r, c in occupied[:12]))
        say(f"Inventory tab {WORK_TAB} is not empty - {len(occupied)} slot(s) "
            f"in use: {where}{more}.\n"
            "Clear it before running: cancelled items land here, and leftover "
            "items make it impossible to tell which ones came back.")
        return False

    say(f"Inventory tab {WORK_TAB} is empty.")
    return True


def changed_slots(
    before: Image.Image,
    after: Image.Image,
    origin: tuple[int, int] | None = None,
) -> list[tuple[int, int]]:
    """Every inventory slot that changed between two frames, in reading order.

    Cancelling a listing of quantity N returns N separate items, filling N
    slots at once, so this must not assume a single winner.

    The panel does not move between the two frames, so one anchor serves both.
    """
    if origin is None:
        origin = inventory_origin(before) or inventory_origin(after) or inventory_origin()
    if origin is None:
        return []

    old, new = inventory_cells(before, origin), inventory_cells(after, origin)
    changed: list[tuple[int, int]] = []
    for key, cell in old.items():
        diff = ImageChops.difference(cell, new[key])
        # Pillow 14 renames getdata(); prefer the new name where available.
        flat = getattr(diff, "get_flattened_data", diff.getdata)()
        delta = sum(flat) / (diff.width * diff.height)
        if delta >= SLOT_CHANGE_MIN:
            changed.append(key)
    return sorted(changed)


def changed_slot(
    before: Image.Image,
    after: Image.Image,
    origin: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    """The first changed inventory slot, or None if nothing changed."""
    slots = changed_slots(before, after, origin)
    return slots[0] if slots else None


def tab_centre(origin: tuple[int, int], tab: int) -> tuple[int, int]:
    """Screen centre of inventory tab `tab` (1-based, I..VIII)."""
    if not 1 <= tab <= TAB_COUNT:
        raise ValueError(f"tab {tab} is outside I..{TAB_COUNT}")
    return (round(origin[0] + TAB_ONE_OFFSET[0] + TAB_PITCH * (tab - 1)),
            round(origin[1] + TAB_ONE_OFFSET[1]))


def active_inventory_tab(
    source: Image.Image | None = None, origin: tuple[int, int] | None = None
) -> int | None:
    """Which inventory tab is selected, by pixel brightness.

    The selected tab is drawn raised and lighter than the rest, which reads far
    more reliably than OCRing roman numerals (I/II/V come back as 'i', 'Vl',
    'vis' and worse).
    """
    image = source if source is not None else grab()
    if origin is None:
        origin = inventory_origin(image)
    if origin is None:
        return None

    brightness = []
    for tab in range(1, TAB_COUNT + 1):
        cx, cy = tab_centre(origin, tab)
        cell = image.crop((cx - 20, cy - 12, cx + 20, cy + 12)).convert("L")
        data = list(getattr(cell, "get_flattened_data", cell.getdata)())
        brightness.append((sum(data) / len(data), tab))

    # Compare against the median rather than the runner-up: the tab row's ends
    # are a little brighter than its middle, so the second-brightest tab can sit
    # close to the active one while the median stays well below both.
    values = sorted(v for v, _ in brightness)
    median = (values[TAB_COUNT // 2 - 1] + values[TAB_COUNT // 2]) / 2
    best = max(brightness)
    return best[1] if best[0] - median >= TAB_ACTIVE_MARGIN else None


def select_inventory_tab(
    tab: int, origin: tuple[int, int] | None = None, timeout: float = 5.0
) -> bool:
    """Click an inventory tab and confirm it became the active one."""
    if origin is None:
        origin = inventory_origin()
    if origin is None:
        return False
    if active_inventory_tab(origin=origin) == tab:
        return True

    click(*tab_centre(origin, tab))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if active_inventory_tab(origin=origin) == tab:
            return True
        time.sleep(0.4)
    return False


# Note: identifying the loaded item by hovering it for a tooltip was tried and
# removed. The tooltip has to be isolated by diffing frames, which proved
# unreliable in both directions -- loose enough to read the listings table
# behind it, tight enough to reject the real tooltip -- and every failure
# stranded the item in the shop slot. The listing is verified instead by
# sanity_check(), after it exists and can simply be read off the table.


def _digits(text: str) -> int | None:
    cleaned = re.sub(r"[^0-9]", "", text)
    return int(cleaned) if cleaned else None


# x beyond which the price rows hold the refresh button, not the figure.
PRICE_TEXT_MAX_X = 230
# The selected price row renders in gold and OCRs poorly -- around 33% where the
# unselected row scores 90 -- so this field needs a lower bar than the default.
# Junk that sneaks in is rejected by _price_value() rather than by confidence.
PRICE_MIN_CONF = 15.0


def _price_value(text: str) -> int | None:
    """Parse a price row's text. None when it is not a price at all.

    "0 Alz" comes back from OCR as 'OAlz' / 'OAIz' -- the digit zero read as a
    letter -- so a row with no digits but nothing else in it means zero, not
    unreadable. Getting that wrong turns "no market data" into "row missing".
    """
    cleaned = re.sub(r"a[il1]z", "", text, flags=re.IGNORECASE)
    digits = re.sub(r"[^0-9]", "", cleaned)
    if digits:
        return int(digits)
    # A zero renders as "0" but OCRs as "O"/"o"/"()" and similar. Require one
    # of those to actually be present: an empty or punctuation-only read means
    # the figure was not read at all, and reporting that as a real 0 sends the
    # caller down the "no market data" path and out to the 10B fallback.
    if re.fullmatch(r"[Oo0°©()\s.,]*", cleaned) and \
            re.search(r"[Oo0°©()]", cleaned):
        return 0
    return None


def read_register_panel(source: Image.Image | Path | str) -> dict:
    """Price and quantity currently shown on the Register Item panel.

    Returns a dict including 'prices', 'price_rows' (value with radio y, top
    row first), 'qty', 'qty_max', 'net_sales', and 'loaded'.

    Use 'loaded' -- not qty_max -- to tell whether an item is in the shop slot:
    the quantity's "/ MAX" separator OCRs unreliably, so qty_max is often None
    even when an item is sitting there.
    """
    image = source if isinstance(source, Image.Image) else Image.open(source)

    # Read the two suggested-price rows at their known positions rather than by
    # hunting for digits: a "0 Alz" row contains no digits at all, and a row
    # that is simply missed must not be confused with one that reads zero.
    words = find_words(image, PRICE_ROWS, PRICE_MIN_CONF)
    prices: list[tuple[int, int]] = []
    for expected_y in (PRICE_TOP_Y, PRICE_BOTTOM_Y):
        on_row = [w for w in words
                  if abs(w.centre[1] - expected_y) <= PRICE_ROW_Y_TOL
                  and w.centre[0] < PRICE_TEXT_MAX_X]
        if not on_row:
            continue
        text = "".join(w.text for w in sorted(on_row, key=lambda w: w.left))
        value = _price_value(text)
        if value is None:
            continue
        y = round(sum(w.centre[1] for w in on_row) / len(on_row))
        prices.append((value, y))

    typed = None
    for word in find_words(image, PRICE_FIELD):
        typed = _digits(word.text) or typed

    qty = qty_max = None
    qty_text = " ".join(
        w.text for w in sorted(find_words(image, QTY_FIELD, QTY_MIN_CONF),
                               key=lambda w: w.left)
    )
    # Pull the numbers out rather than insisting on a '/', which OCR mangles.
    numbers = [_digits(chunk) for chunk in re.findall(r"\d[\d,]*", qty_text)]
    numbers = [n for n in numbers if n is not None]
    if numbers:
        qty = numbers[0]
        if len(numbers) > 1:
            qty_max = numbers[-1]

    # Net sales is the game's own price x quantity; it stays 0 until a price is
    # actually selected, which makes it the reliable "ready to register" signal.
    # Join, do not max(): a split total ("1,260," + "000,000") read as 1,260
    # fails the price x quantity equality and aborts a correct listing.
    net_cell = sorted(find_words(image, NET_SALES_ROWS), key=lambda w: w.left)
    net = _digits("".join(w.text for w in net_cell)) or 0

    # Whether an item is sitting in the shop slot, judged by pixels rather than
    # text: an empty box is near-uniform dark, any icon is not. Text signals are
    # unreliable here -- the quantity's "/ MAX" often fails to OCR at all.
    box = image.crop(SHOP_SLOT_BOX).convert("L")
    pixels = list(getattr(box, "get_flattened_data", box.getdata)())
    mean = sum(pixels) / len(pixels)
    stdev = (sum((p - mean) ** 2 for p in pixels) / len(pixels)) ** 0.5
    loaded = stdev >= SHOP_SLOT_STDEV or bool(qty) or net > 0

    return {"prices": [p for p, _ in prices], "price_rows": prices, "typed": typed,
            "qty": qty, "qty_max": qty_max, "qty_text": qty_text,
            "net_sales": net, "loaded": loaded, "slot_stdev": round(stdev, 1)}


def await_dialog(kind: str | None, timeout: float = 8.0, poll: float = 0.35):
    """Poll until the dialog state equals `kind`. Returns the screenshot proving
    it, or None on timeout. `kind=None` waits for every dialog to be gone."""
    deadline = time.monotonic() + timeout
    while True:
        shot = grab()
        if dialog_kind(shot) == kind:
            return shot
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


class Aborted(Exception):
    """A step did not produce the state the sequence requires."""


class FatalAbort(Exception):
    """Something went wrong that must stop the whole run, not just this cycle.

    Raised when the script has listed something that does not match what was
    there before. Retrying cannot help and might list it wrong again, so the
    bad listing is pulled and everything stops for a human to look.
    """


def cancel_item(
    row: int,
    dry_run: bool = False,
    timeout: float = 8.0,
    verbose: bool = True,
    expect: "RowRef | None" = None,
) -> bool:
    """Cancel the listing on table row `row` (1-based, as displayed).

    Rows are counted exactly as they appear, including sold rows (which show
    Receive) and empty slots (which show Register); those cannot be cancelled.

    Pass `expect` whenever the row number came from an earlier table read: this
    function re-reads the table, and the row that was chosen may not be the row
    that number now points at. Without it the only check is that the row says
    "Change", which any listing does.

    The sequence is strict: Change must produce the Registration Extension
    dialog, its Cancel must produce the confirmation dialog, and Confirmation
    must close it. Any deviation aborts, backs out of whatever is on screen,
    and returns False without committing anything.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise Aborted(reason)

    committed = False
    try:
        # ---- preconditions, before anything is clicked ----------------------
        if not dry_run:
            require(not session_locked(),
                    "the workstation is locked - screen capture is blank and "
                    "input goes to the secure desktop")
            require(focus_game(), "could not bring Cabal to the foreground")
            # Read with nothing hovered, or an item tooltip covers the table.
            park_cursor()
            require(
                not table_loading(grab()) or wait_for_table(max(timeout, 20.0)),
                "the table is still waiting for the server response",
            )
            require(
                dialog_kind(grab()) is None,
                "a dialog was already open before starting",
            )

        rows = await_rows(timeout)
        require(bool(rows),
                "no listings visible - is the Trade window open on Register?")
        require(1 <= row <= len(rows),
                f"row {row} is out of range; {len(rows)} row(s) visible")

        target = rows[row - 1]
        say(f"Row {row}: {target.name!r} [{target.action}] -> button at {target.change}")
        require(target.cancellable,
                f"row {row} shows '{target.action}', not 'Change'")

        # A row NUMBER is not an identity. The caller resolved this row from a
        # different table read, tens of seconds and one server refresh ago;
        # this function then re-read the table and indexed into it blind,
        # checking only that whatever sits there now says "Change". A row
        # collected or sold in between shifts everything up, and the wrong
        # listing gets cancelled -- with the previous row's name, quantity and
        # price floor then carried into the relist.
        if expect is not None:
            # Resolved NON-strictly, then checked against the index we were
            # given. Strict mode refuses whenever two rows are indistinguishable
            # -- exactly the duplicate-stack case the caller already resolved by
            # position before handing the identity down. Re-asking strictly here
            # meant two identical stacks could never be cancelled at all: the
            # caller picked a sibling, this refused it, the batch stopped, and
            # three cycles later the whole run gave up. This shop has two such
            # pairs today.
            #
            # `resolved.index == row` is the guard that matters anyway: it
            # catches the table having shifted, which is why this check exists.
            resolved, note = locate_row(rows, expect)
            moved = f"it is now at row {resolved.index}" if resolved else "it is gone"
            require(resolved is not None and resolved.index == row,
                    f"row {row} no longer holds {expect.name!r} "
                    f"({note or moved}) - the table changed since it was "
                    "chosen, so nothing was cancelled")
            if note:
                say(f"  {note}")
            say(f"  identity confirmed: row {row} still holds {expect.name!r}")

        if dry_run:
            say("[dry run] would click Change -> Cancel -> Confirmation")
            return True

        # ---- step 1: Change must open the Registration Extension dialog -----
        record("cancel.before_change", row=row, name=target.name,
               price=target.price, qty=target.qty)
        click(*target.change)
        shot = await_dialog("extension", timeout)
        # `shot` positionally, not shot=: it is a positional-only parameter, so
        # the keyword form quietly became *context* -- the frame saved was
        # whatever grab() last returned rather than this one, and a PIL object
        # was serialised into the index as a repr string.
        record("cancel.after_change", shot, row=row, name=target.name,
               dialog="extension" if shot else "none")
        if shot is None:
            # Say what WAS on screen. This failure has recurred, and "it did
            # not appear" is indistinguishable between three different causes:
            # the click missed, the dialog opened but was classified as another
            # kind, or its title would not OCR. Escape failing to close the
            # Trade window afterwards suggests a modal really is up, so the
            # words below are the evidence needed to tell them apart.
            probe = grab()
            say(f"  dialog_kind sees: {dialog_kind(probe)!r}")
            say(f"  trade window still open: {trade_window_open(probe)}")
            words = sorted(find_words(probe, POPUP_REGION, 25),
                           key=lambda w: -w.conf)[:12]
            say("  strongest words in the dialog area: "
                + ", ".join(f"{w.text!r}@{w.conf:.0f}" for w in words))
            # The diagnostic frame has caught the dialog arriving late. On
            # 2026-08-04 at 07:57 this probe read 'extension' on the line
            # AFTER the wait gave up, and the run aborted regardless -- then
            # left that dialog covering the table for the next two cycles.
            #
            # A fresh read of the exact dialog expected is evidence, not a
            # guess: clicking a row's Change button opens this one and nothing
            # else, and if the state is wrong anyway the next step's own
            # require() catches it before anything commits.
            if dialog_kind(probe) == "extension":
                say("  ...but it IS up on a fresh frame: it arrived after the "
                    "wait expired. Continuing rather than aborting.")
                shot = probe
            else:
                # One probe is a one-frame window, and the dialog does not
                # arrive on a schedule. A sweep of arrival times showed the
                # single recheck rescuing NONE of them: it happened to land on
                # the right frame at 07:57 and would not have next time.
                #
                # So look again properly. This only runs on a path that was
                # about to abort, so the cost is a few seconds on a failure
                # rather than on every cancel.
                shot = await_dialog("extension", EXTENSION_RECHECK_SECONDS)
                if shot is not None:
                    say("  ...it IS up on a fresh frame after a second look; "
                        "continuing rather than aborting.")
        require(shot is not None, "the Registration Extension dialog did not appear")

        cancel = await_dialog_button(DISMISS_WORD, timeout)
        require(cancel is not None,
                "no Cancel button on the Registration Extension dialog")

        # ---- step 2: Cancel must open the confirmation dialog ---------------
        say(f"{DISMISS_WORD} button at {cancel.centre} (conf {cancel.conf:.0f})")
        click(*cancel.centre)
        shot = await_dialog("confirm", timeout)
        require(shot is not None, "the confirmation dialog did not appear")

        confirm = await_dialog_button(CONFIRM_WORD, timeout)
        require(confirm is not None,
                "no Confirmation button on the confirmation dialog")

        # ---- step 3: Confirmation commits and must close the dialog ---------
        say(f"{CONFIRM_WORD} button at {confirm.centre} (conf {confirm.conf:.0f})")
        click(*confirm.centre)
        committed = True
        require(await_dialog(None, timeout) is not None,
                "the dialog stayed open after Confirmation")

        record("cancel.committed", row=row, name=target.name,
               price=target.price, qty=target.qty)
        say(f"Cancelled registration on row {row}: {target.name!r}.")
        return True

    except Aborted as exc:
        # The most valuable frame in the whole run: whatever was on screen when
        # the sequence refused to continue. Recorded before any recovery
        # clicking, so the corpus keeps the state that caused it rather than
        # the state after backing out of it.
        # Read the dialog BEFORE recording, so the record carries what was
        # determined rather than contradicting it. `committed` means only "the
        # Confirmation click was sent"; whether the game ACCEPTED it is a
        # different fact, and the corpus was storing the first while the log
        # printed the second -- three recorded aborts say committed=True for
        # cancellations the log calls refused. Reading costs one screenshot and
        # no clicks, so it still happens before any recovery input.
        still = dialog_kind(grab()) if committed else None
        record("cancel.aborted", reason=str(exc), row=row, committed=committed,
               dialog_after=still,
               accepted=None if not committed else (still != "confirm"))
        say(f"ABORTED: {exc}.")
        if committed:
            # Past the point of no return; only report, never click further.
            #
            # But "clicked" is not "accepted". A cancellation the game took
            # closes the dialog; if the confirmation dialog is STILL up, the
            # game refused the action and the listing is intact. Reading that
            # costs one screenshot and no clicks, and it replaces a guess with
            # an observation -- the previous message said the cancel "may have
            # gone through" in exactly the case where it provably had not,
            # which sends you hunting for a stranded item that does not exist.
            if still == "confirm":
                # An inference, not an observation, and it can be wrong:
                # the game stacks confirmation dialogs (MAX_CONFIRM_STEPS
                # exists for that on the register side), so it can commit
                # AND still be showing one. Saying "still on the market"
                # as fact would send the operator away from a listing that
                # had in fact been withdrawn.
                say("A confirmation dialog is still open. USUALLY that "
                    "means the game refused the cancellation and the "
                    "listing is untouched - but the game can also stack "
                    "dialogs after accepting one, so this is not proof.")
                say("CHECK THE LISTING before retrying: if it is gone, the "
                    f"stack is in inventory tab {WORK_TAB}, unlisted.")
                # Deliberately hedged. The corpus contains three consecutive
                # refusals of the same listing where the work tab was verified
                # EMPTY on all three, so free space in tab 4 is demonstrably
                # not the whole story -- the game may check total space across
                # every tab, which one frame cannot see.
                say("A likely cause is not enough free inventory space to "
                    "receive the stack: a cancelled 250-item listing comes "
                    "back as ~64 separate slots, and the game refuses rather "
                    "than partially withdrawing. Note the check covers your "
                    "WHOLE inventory, not just the work tab -- this has been "
                    "observed refusing while the work tab was empty.")
                say("Retrying this row will refuse identically until space is "
                    "freed, so the run will stop after "
                    f"{MAX_CONSECUTIVE_FAILURES} attempts.")
            else:
                say("WARNING: Confirmation was already clicked and the dialog "
                    f"is now {still!r}, so the cancellation may have gone "
                    "through. Check the listing before retrying.")
            return False
        # --dry-run is exempt from the elevation gate precisely because it does
        # not click. This recovery path was not gated, so a dry run that
        # aborted early went on to click its way out of a dialog.
        if dry_run:
            say("[dry run] leaving the screen exactly as it is.")
            return False
        # dialog_present, not dialog_kind: a title that failed to OCR read as
        # "no dialog", so this branch announced "Nothing was changed" and left
        # a modal sitting over the table. Everything after it -- the rest of
        # the batch and the whole next cycle -- then failed to read any rows.
        if not dialog_present():
            say("Nothing was changed.")
        elif close_any_dialog():
            say("Backed out of the open dialog; nothing was changed.")
        else:
            say("WARNING: could not close the dialog still on screen - "
                "dismiss it manually before rerunning.")
        return False


def clear_shop_slot(timeout: float = 15.0, verbose: bool = True) -> bool:
    """Ctrl+Click the shop slot to send its item back to the inventory."""
    # Without the Trade window open, SHOP_SLOT_BOX samples the game world and
    # the verdict is meaningless -- either "clear" while an item sits there, or
    # a Ctrl+Click into the open world.
    if not trade_window_open():
        if verbose:
            print("The Trade window is not open, so the shop slot cannot be read.")
        return False

    panel = read_register_panel(grab())
    if not panel["loaded"]:
        return True
    if verbose:
        print(f"Returning the shop slot item (qty {panel['qty_text']!r}) to the inventory")
    ctrl_click(*SHOP_SLOT)
    # Move off the slot before polling: leaving the cursor there raises the
    # item tooltip, which renders over SHOP_SLOT_BOX and keeps the pixel spread
    # high, so "loaded" would never go false and every cycle would fail setup.
    park_cursor()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not read_register_panel(grab())["loaded"]:
            return True
        time.sleep(0.5)
    return False


def register_item(
    row: int,
    col: int,
    dry_run: bool = False,
    timeout: float = 8.0,
    verbose: bool = True,
    price_floor: int = 0,
    floor_price: int | None = None,
    floor_reason: str = "",
    maximise_qty: bool | None = None,
    force_price: int | None = None,
    force_qty: int | None = None,
    expect_item: str | None = None,
    expect_qty: int | None = None,
    report: dict | None = None,
) -> bool:
    """List the item in inventory slot (row, col) on the Agent Shop.

    Ctrl+Clicks the slot into the shop slot, selects the lowest currently
    listed price (the bottom of the two suggested rows; the top row is the
    week's average), or types FALLBACK_PRICE when that row reads 0, leaves the
    quantity at whatever the game defaulted to, and presses Register.

    Strict: every step must produce the expected state or the whole thing
    aborts and backs out without listing anything.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise Aborted(reason)

    committed = False
    try:
        # Gated on dry_run: focus_game() injects a synthetic Alt tap and
        # park_cursor() moves the physical cursor, so running these in a dry
        # run contradicts "locate everything but do not click" -- and the
        # elevation check is skipped for dry runs, so they were unguarded too.
        if not dry_run:
            require(focus_game(), "could not bring Cabal to the foreground")
            park_cursor()
            require(not table_loading(grab()) or wait_for_table(max(timeout, 20.0)),
                    "the table is still waiting for the server response")
            require(dialog_kind(grab()) is None, "a dialog was already open")

        panel = read_register_panel(grab())
        require(not panel["loaded"],
                f"the shop slot already holds an item "
                f"(qty {panel['qty_text']!r}, spread {panel['slot_stdev']})")

        centre = slot_centre(row, col)
        record("register.before_load", row=row, col=col, item=expect_item)
        say(f"Ctrl+Click inventory slot ({row},{col}) at {centre}")
        if dry_run:
            say("[dry run] would load the slot, set the price, then Register")
            return True

        # ---- step 1: load the item into the shop slot -----------------------
        # Ctrl+Click commits nothing, so retrying is safe. The click after a
        # dialog closes is sometimes swallowed while the game takes focus back.
        panel = {"loaded": False}
        for attempt in range(1, LOAD_ATTEMPTS + 1):
            ctrl_click(*centre)
            time.sleep(0.8)
            park_cursor()
            panel = read_register_panel(grab())
            if panel["loaded"]:
                break
            if attempt < LOAD_ATTEMPTS:
                say(f"Slot ({row},{col}) did not load on attempt {attempt}; retrying.")
                time.sleep(0.6)
        require(panel["loaded"],
                f"nothing loaded into the shop slot after {LOAD_ATTEMPTS} attempts "
                f"(slot ({row},{col}) may be empty, or the item moved)")
        say(f"Loaded: qty {panel['qty_text']!r} -> {panel['qty']}/{panel['qty_max']}, "
            f"suggested {panel['prices'] or 'none'}")

        # ---- quantity cross-check -------------------------------------------
        # The loaded stack should hold as many as the cancelled listing did.
        # This is a cheap consistency check, not proof of identity -- that is
        # sanity_check()'s job, after the listing exists and can be read back.
        if expect_item and expect_qty is not None and panel["qty_max"] is not None:
            # Exact equality here stranded a 233-item stack and stopped a
            # five-hour run. The table's QTY column read 233 as 230 -- one
            # glyph -- and this fired AFTER the cancel had committed, so the
            # item was already out of the shop and in the work tab. Three
            # cycles then failed the empty-work-tab check and the breaker
            # stopped everything.
            #
            # Which number to trust: `expect_qty` comes from the table's
            # cramped QTY column, `qty_max` from the panel's dedicated numeric
            # field. When they disagree slightly the PANEL is the better read,
            # and the quantity typed is MAX_QTY_ENTRY anyway, so the game
            # clamps to whatever is really there.
            #
            # The check's stated purpose is catching a completely different
            # item, and a different item does not differ by 1% -- it differs by
            # orders of magnitude. So tolerate OCR noise, refuse on a real
            # discrepancy, and never abort over a rounding error once the
            # cancel is irreversible.
            loaded = panel["qty_max"]
            slack = max(QTY_CROSSCHECK_ABSOLUTE,
                        int(expect_qty * QTY_CROSSCHECK_FRACTION))
            if loaded != expect_qty and abs(loaded - expect_qty) <= slack:
                say(f"NOTE: the panel holds {loaded} but the table said "
                    f"{expect_qty} (within {slack}). The panel field is the "
                    "more reliable read, so continuing with it - the table's "
                    "QTY column is narrow and misreads a digit occasionally.")
                # `if report is not None`, not `report and`: every caller
                # passes a fresh {}, which is FALSY, so the truthiness form
                # never executed once. Every other write in this function
                # already gets this right.
                if report is not None:
                    report["qty_disagreement"] = (expect_qty, loaded)
            else:
                require(loaded == expect_qty,
                        f"loaded {loaded} of an item but the cancelled listing "
                        f"held {expect_qty} - off by {abs(loaded - expect_qty)}, "
                        f"more than the {slack} tolerated, so this is probably "
                        "not the same item")

        # ---- step 2: quantity, settled before pricing -----------------------
        # The suggested price is per item and Net sales is price x quantity, so
        # the quantity has to be final before either figure means anything.
        # Type it and move on. The field clamps entry to the stack's maximum,
        # so anything larger than a stack can hold fills it, and nothing is
        # read back here: this field is the worst OCR target on the panel --
        # '158' came back as '15a', '2 / 2' as '[2' -- and every attempt to
        # verify it against a number read out of the same field aborted runs
        # whose quantity had gone in perfectly well.
        #
        # It is not unverified, only verified later and by a better number: the
        # quantity the game settles on is recovered below from Net sales, which
        # the game computes itself and which OCRs cleanly.
        # maximise_qty=None means "use the configured policy". Passing it
        # explicitly is for callers that have already decided.
        #
        # This defaulted to False, so the two entry points read
        # MAXIMISE_ALL_QUANTITIES differently: the relist path resolved it via
        # wants_max_quantity() and maximised, while `--register R C` silently
        # did not. A stack of six VIP passes listed as ONE, and the setting
        # that was supposed to govern it had no effect on that path at all.
        #
        # The exclusion list can only be applied to an item the script can
        # name. Where it cannot -- `--register` reads an inventory slot, not a
        # listing -- an empty list is unambiguous and a non-empty one is not,
        # so the ambiguous case is refused rather than guessed. That mirrors
        # how pricing already treats an unnameable item.
        if maximise_qty is None:
            if force_qty:
                # An explicit quantity settles it, so the policy and the
                # exclusion list are moot -- refusing here would reject
                # `--register R C --qty 6`, which states exactly what to do.
                maximise_qty = False
            elif expect_item:
                maximise_qty = wants_max_quantity(expect_item)
            else:
                require(not (MAXIMISE_ALL_QUANTITIES and NO_MAX_QUANTITY_ITEMS),
                        "cannot decide whether to maximise the quantity: the "
                        "item in this slot cannot be named, so "
                        f"NO_MAX_QUANTITY_ITEMS {NO_MAX_QUANTITY_ITEMS} cannot "
                        "be checked against it. Pass --qty N or --max-qty to "
                        "say which you want")
                maximise_qty = MAXIMISE_ALL_QUANTITIES

        entry = force_qty if force_qty else (MAX_QTY_ENTRY if maximise_qty else None)
        if entry is not None:
            record("qty.before_typing", entry=entry, item=expect_item)
            say(f"Setting quantity: typing {entry}"
                + ("" if force_qty else " - the game clamps it to the stack maximum"))
            click(*QTY_INPUT)
            # The quantity field holds at most len(str(MAX_QTY_ENTRY)) digits,
            # so clearing it needs that many backspaces plus a little slack --
            # not the price field's worth.
            type_number(entry, clear=len(str(MAX_QTY_ENTRY)) + 2)
            time.sleep(0.4)
            park_cursor()

        # ---- step 3: settle on a price --------------------------------------
        # Two rows: the top is the week's average price, the bottom is the
        # lowest currently listed. Always take the bottom one, by position --
        # never by value, which would drift between rows run to run.
        rows_seen = panel["price_rows"]

        # Resolve the floor BEFORE any branch can set a price. DO NOT REMOVE,
        # and do not move this inside a branch: it lived in the market-price
        # branch, so --price skipped it entirely and listed a VIP at whatever
        # the caller typed.
        #
        # An unidentified item takes the strictest floor rather than none. The
        # old `... if expect_item else 0` failed OPEN: every caller except the
        # relist cycle passes no name, so `--register` and `do register` listed
        # floor-bearing items with no floor at all.
        if expect_item:
            absolute_floor = item_price_floor(expect_item)
        else:
            # The item cannot be named here, so its floor cannot be looked up.
            #
            # Substituting the strictest floor and listing at it is NOT the
            # safe direction, which an earlier version assumed. Nine of the ten
            # things on this account are worth 85,000-15,000,000; listing one
            # at 110,000,000 pays a percentage sales fee on that inflated
            # figure and takes the item off the market for the whole listing
            # period. Refusing costs nothing by comparison.
            #
            # So: auto-pricing an unidentified item is refused outright. A
            # human stating the price with --price is honoured -- that is an
            # explicit instruction, not a guess.
            absolute_floor = 0
            require(force_price is not None,
                    "cannot price an item the script cannot name, because its "
                    "price floor cannot be looked up. Use --relist, which "
                    "reads the name off the listing, or pass --price to state "
                    "the price yourself")
            # An unnamed item could be anything, including a VIP. A stated
            # price below the strictest floor on the books therefore needs the
            # item named -- otherwise `--register R C --price 410000` on a slot
            # that happens to hold a VIP dumps 110M of stock, and no
            # sanity_check runs on that path to catch it.
            strictest = strictest_price_floor()
            require(not strictest or force_price >= strictest,
                    f"--price {force_price:,} is below the strictest floor on "
                    f"the books ({strictest:,}) and the item cannot be named "
                    f"here, so it might be one the floor protects. Use "
                    f"--relist, which reads the name off the listing")
        if absolute_floor:
            say(f"Absolute floor for this item: {absolute_floor:,} Alz")

        # `is not None`, matching the guard above. Testing truthiness let a
        # price of 0 satisfy `require(force_price is not None, ...)` and then
        # fall through to MARKET pricing with absolute_floor still 0 -- exactly
        # the fail-open the guard exists to close.
        if force_price is not None:
            # An explicit price is a human instruction, so it is honoured --
            # but never below the floor of an item the script has identified.
            require(not (expect_item and absolute_floor
                         and force_price < absolute_floor),
                    f"--price {force_price:,} is below the {absolute_floor:,} "
                    f"floor for {expect_item!r}")
            suggested, price_y = force_price, None
            price, why = force_price, "forced by --price"
            say(f"Price forced to {force_price:,} Alz")
        else:
            require(bool(rows_seen), "no suggested-price rows could be read")

            # Take the row sitting at the bottom position, and verify by y that
            # it really is that row -- reading only the top row must not be
            # mistaken for reading the bottom one.
            suggested, price_y = min(rows_seen,
                                     key=lambda r: abs(r[1] - PRICE_BOTTOM_Y))
            require(abs(price_y - PRICE_BOTTOM_Y) <= PRICE_ROW_Y_TOL,
                    f"the lowest-current-price row was not found; read {rows_seen} "
                    f"(expected a row near y={PRICE_BOTTOM_Y})")

            average = next((p for p, y in rows_seen
                            if abs(y - PRICE_TOP_Y) <= PRICE_ROW_Y_TOL), None)
            record("price.suggestions", lowest=suggested, rows=str(rows_seen))
            say(f"Suggested: lowest current {suggested:,}, week average "
                + (f"{average:,}" if average else "unread"))
            if price_floor:
                say(f"--floor: {price_floor:,} Alz"
                    + (f" ({floor_reason})" if floor_reason else ""))

            # NOTE: absolute_floor is resolved above, before any branch. It was
            # recomputed here as `... if expect_item else 0`, which silently
            # undid that and handed every unnamed caller a floor of zero.
            price, why = choose_price(suggested, price_floor, floor_price,
                                      absolute_floor)
            if why and floor_reason:
                why = f"{why} ({floor_reason})"

            # A market far below the previous price is only reported, never
            # overridden: the rule is to take the lowest current price.
            if (floor_price and suggested > 0
                    and suggested < floor_price * SUSPECT_PRICE_FRACTION):
                say(f"NOTE: market {suggested:,} is only "
                    f"{suggested / floor_price:.1%} of the previous "
                    f"{floor_price:,} - listing at the market price anyway.")

        # The last gate before anything is clicked, and it covers every branch
        # above. MIN_PLAUSIBLE_PRICE previously guarded only the price read off
        # the table, never the one about to be listed -- so a clipped market
        # read ("105,000,000" losing all but "105,") went straight through and
        # would have listed at 105 Alz.
        require(price >= MIN_PLAUSIBLE_PRICE,
                f"refusing to list at {price:,} Alz, below the "
                f"{MIN_PLAUSIBLE_PRICE:,} plausibility floor - the price was "
                "probably misread")
        require(not absolute_floor or price >= absolute_floor,
                f"refusing to list at {price:,} Alz, below the "
                f"{absolute_floor:,} floor for this item")

        if price == suggested and price_y is not None and price > 0:
            record("price.before_select", price=price, y=price_y)
            click(PANEL_RADIO_X, price_y)
        else:
            say(f"Overriding to {price:,} Alz - {why}")
            click((PRICE_FIELD[0] + PRICE_FIELD[2]) // 2,
                  (PRICE_FIELD[1] + PRICE_FIELD[3]) // 2)
            type_number(price)
        time.sleep(0.5)

        park_cursor()
        panel = read_register_panel(grab())
        require(panel["net_sales"] > 0,
                f"price did not take - net sales is still {panel['net_sales']}")

        # Net sales is the game's own price x quantity, so dividing it by the
        # price just set recovers the quantity the game actually holds. That is
        # the reliable direction: this figure is a long digit string that OCRs
        # at high confidence, while the quantity field beside it is a couple of
        # glyphs that routinely come back mangled.
        #
        # The division not being exact is itself the check -- a remainder means
        # the price on screen is not the price that was meant to be set.
        require(panel["net_sales"] % price == 0,
                f"net sales {panel['net_sales']:,} is not a whole multiple of the "
                f"{price:,} price that was set - the price did not take correctly")
        qty = panel["net_sales"] // price
        say(f"Net sales {panel['net_sales']:,} Alz = {price:,} x {qty}"
            f"  (field reads {panel['qty_text']!r})")

        # ---- step 4: Register -----------------------------------------------
        shot = grab()
        buttons = find_text(shot, "Register", REGISTER_PANEL)
        require(bool(buttons), "could not find the Register button")
        button = buttons[-1]  # lowest: below 'Register Item' and 'Register QTY'
        record("register.priced", shot, price=price, qty=panel.get("qty"),
               net_sales=panel.get("net_sales"), item=expect_item)
        say(f"Register button at {button.centre} (conf {button.conf:.0f})")

        # Register does not list anything yet: it raises a confirmation dialog.
        # Sometimes two -- pricing more than 25% under the weekly average adds
        # an extra "are you sure" step -- so confirm through the whole chain
        # rather than assuming a single dialog.
        click(*button.centre)
        shot = await_dialog("confirm", timeout)
        require(shot is not None, "no confirmation dialog appeared after Register")

        # ---- step 5: confirm through every dialog; this commits -------------
        # Driven by the button finder, which polls: a single dialog_kind() read
        # can flake to None and end the chain with a dialog still up.
        for step in range(1, MAX_CONFIRM_STEPS + 1):
            # Check a dialog is actually up before hunting for its button.
            # await_dialog_button ends with a 15%-confidence sweep of a
            # 1600x800 region, most of which is 3D scenery once the dialogs
            # have closed -- junk matching "confirmation" there gets clicked
            # into the world, which walks the character away from the NPC.
            if dialog_kind(grab()) is None:
                break
            confirm = await_dialog_button(CONFIRM_WORD, timeout=4.0)
            if confirm is None:
                break  # nothing left to confirm
            say(f"{CONFIRM_WORD} {step} at {confirm.centre} "
                f"(conf {confirm.conf:.0f})")
            click(*confirm.centre)
            committed = True
            # Recorded HERE, not after the loop: in the single-dialog case this
            # click IS the commit, and an exception later in the loop (a
            # refused click, a failed grab) would otherwise carry the listing's
            # existence away with it -- the caller would be told "input was
            # refused" and nothing about a possibly-live, unverified listing.
            if report is not None:
                report["committed"] = True
            time.sleep(0.8)

        # Record what was committed BEFORE the checks below can abort. Both of
        # them fire after the listing is live, and returning a bare failure
        # made the caller skip sanity_check entirely -- leaving a live,
        # never-verified listing, which is the one thing that check exists for.
        if committed and report is not None:
            report["price"] = price
            report["qty"] = qty
            report["total"] = panel["net_sales"]
            report["committed"] = True

        require(await_dialog(None, timeout) is not None,
                f"a confirmation dialog is still open after {MAX_CONFIRM_STEPS} steps")

        # The shop slot emptying is the evidence the listing went through.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            after = read_register_panel(grab())
            if not after["loaded"]:
                if report is not None:
                    report["price"] = price
                    report["qty"] = qty
                    report["total"] = panel["net_sales"]
                record("register.committed", row=row, col=col, price=price,
                       qty=qty, item=expect_item)
                say(f"Registered ({row},{col}) qty {qty} at {price:,} Alz "
                    f"each ({panel['net_sales']:,} total).")
                return True
            time.sleep(0.5)
        require(False, "the shop slot did not clear after Confirmation")
        return False

    except Aborted as exc:
        record("register.aborted", reason=str(exc), row=row, col=col,
               item=expect_item, committed=committed)
        say(f"ABORTED: {exc}.")
        if committed:
            say("WARNING: Confirmation was already clicked, so the listing may "
                "have gone through. It will be checked against the table.")
            return False
        if dry_run:
            say("[dry run] leaving the screen exactly as it is. Nothing was listed.")
            return False
        if dialog_kind(grab()) is not None and not close_any_dialog():
            say("WARNING: a dialog is still open - dismiss it manually.")
        if read_register_panel(grab())["loaded"]:
            say("NOTE: the item is still sitting in the shop slot; "
                "run --clear to put it back in the inventory.")
        say("Nothing was listed.")
        return False


def relist(
    row: int,
    inv_row: int | None = None,
    inv_col: int | None = None,
    dry_run: bool = False,
    timeout: float = 8.0,
    verbose: bool = True,
    attempts: int = RELIST_ATTEMPTS,
    expect: "RowRef | None" = None,
) -> str:
    """Cancel the listing on `row`, then re-list it from inventory (inv_row, inv_col).

    Cancelling returns the item to the inventory, so the second half picks it
    back up and lists it at the lowest currently listed price, subject to two
    floors:

      * per-item floors in ITEM_PRICE_FLOORS bind absolutely (VIP >= 105M), and
      * MIN_PLAUSIBLE_PRICE, below which the market read is treated as a
        misread rather than a price.

    There is deliberately NO relative floor against the previous price: the
    rule is to take the lowest current price, whatever it is. A large drop is
    reported (SUSPECT_PRICE_FRACTION) but never overridden. This docstring used
    to promise a 5% floor and a restore-the-original path, neither of which
    existed in the code -- which is worse than having no floor, because it
    invites the absolute floor to be relaxed on the strength of a backstop that
    is not there.

    Strictly sequential: if the cancel does not fully succeed, nothing is listed.

    If the row has sold (it shows Receive instead of Change), the proceeds are
    collected. If any quantity is still listed afterwards the relist restarts
    against it; if the listing has gone entirely, that is SOLD_OUT -- there is
    nothing left to relist and it is not a failure.

    Each call is a complete cycle that leaves the shop closed behind it:

        click the Agent Shop NPC -> Register tab -> Refresh
          -> cancel the row -> re-list it -> Escape

    so the next call starts from scratch rather than depending on whatever the
    previous one left on screen. Refresh matters: the client's copy of the
    table goes stale, and cancelling a stale row is how you cancel something
    that has already sold.

    Returns RELISTED, SOLD_OUT or FAILED.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    def close_shop() -> None:
        """Leave the shop closed, so the next cycle starts from the NPC.

        Never raises. This runs in a `finally`, so an exception escaping here
        would REPLACE an in-flight FatalAbort -- the caller would then see an
        ordinary failure, retry, and re-list the very thing the FatalAbort was
        raised to stop.
        """
        if dry_run:
            return
        try:
            for _ in range(ESCAPE_ATTEMPTS):
                if not trade_window_open():
                    return
                press_escape()
            if trade_window_open():
                say("Note: the Trade window would not close with Escape.")
        except Exception as exc:  # noqa: BLE001 - must not mask the real error
            say(f"Note: could not close the Trade window ({exc}).")

    try:
        return _relist_cycle(row, inv_row, inv_col, dry_run, timeout,
                             verbose, attempts, say, expect)
    finally:
        close_shop()


def _relist_cycle(row, inv_row, inv_col, dry_run, timeout, verbose, attempts, say,
                  expect=None):
    """The body of relist(); relist() wraps this to always close the shop."""
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            say(f"\n=== relist attempt {attempt}/{attempts} ===")

        # Step 1 of the cycle: the shop, from the NPC. Then a fresh table --
        # the client's copy goes stale, and acting on a stale row is how you
        # cancel something that has already sold.
        if not dry_run:
            require_focus = focus_game()
            if not require_focus:
                say("Could not bring Cabal to the foreground.")
                return FAILED
            park_cursor()
            if not open_trade_window(verbose=verbose):
                say("Could not open the Agent Shop on the Register tab.")
                return FAILED
            if attempt == 1 and not require_empty_work_tab(verbose=verbose):
                say("Aborting: the working inventory tab must be empty to start.")
                return FAILED
            if not refresh_table(timeout=max(timeout, 20.0), verbose=verbose):
                say("Could not refresh the table - stopping.")
                return FAILED
            park_cursor()

        # Read the price BEFORE cancelling; once cancelled the row is gone.
        rows = await_rows(timeout)
        if not 1 <= row <= len(rows):
            say(f"Row {row} is out of range; {len(rows)} row(s) visible.")
            return FAILED

        target = rows[row - 1]

        # This function reopened the shop and forced a server refresh since the
        # caller picked this row, so the number it was given may now point
        # somewhere else. Everything below -- the price floor, the expected
        # quantity, what sanity_check will look for -- is taken from `target`,
        # so a shift here means relisting one item under another's identity.
        if expect is not None:
            resolved, note = locate_row(rows, expect)
            if resolved is None:
                say(f"{expect.name!r} is no longer in the table "
                    f"({note or 'gone'}) - nothing was cancelled.")
                return FAILED
            if resolved.index != row:
                say(f"{expect.name!r} moved from row {row} to "
                    f"{resolved.index} since it was chosen - following it.")
                row = resolved.index
            target = resolved

        # The table as it stood when this row was chosen, with the choice
        # itself in the index. Without it a later frame shows what happened
        # but not which listing it was supposed to be happening to.
        # The whole table, not just the chosen row. read_rows read all ten, so
        # recording all ten costs one JSON line and gives the tests ten
        # ground-truth comparisons per frame instead of one -- and the nine
        # extra ones cover the rows a relist never targets, which are exactly
        # the rows no other check looks at.
        record("table.target", row=row, name=target.name, action=target.action,
               price=target.price, qty=target.qty, visible=len(rows),
               attempt=attempt,
               table=[[r.index, r.name, r.action, r.price, r.qty] for r in rows])

        # A sold listing shows Receive. Collect the proceeds, then see whether
        # any quantity is still on sale: if so relist that, if not the listing
        # is done. Collecting renumbers the table, so re-locate by name.
        if target.action == "receive":
            say(f"Row {row} is sold ({target.name!r}) - clicking Receive.")
            if dry_run:
                say("[dry run] would click Receive, then relist any remainder")
                return RELISTED
            if attempt == attempts:
                say(f"Still sold on the final attempt ({attempts}) - stopping.")
                return FAILED

            click(*target.change)

            # Collecting raises "Confirm Receipt", whose accept button reads
            # Receive. Leaving it open would also cover the table and corrupt
            # the row read that follows.
            accept = await_dialog_button(RECEIPT_WORD, timeout=6.0)
            if accept is None:
                say("The Confirm Receipt dialog did not appear - stopping.")
                return FAILED
            say(f"Confirm Receipt: accepting at {accept.centre} "
                f"(conf {accept.conf:.0f})")
            click(*accept.centre)
            time.sleep(1.0)
            if await_dialog(None, timeout) is None:
                say("The Confirm Receipt dialog stayed open - stopping.")
                return FAILED

            say(f"Waiting {RECEIVE_WAIT:g}s for the sale to settle...")
            time.sleep(RECEIVE_WAIT)
            if not wait_for_table(max(timeout, 20.0)):
                say("The table did not finish refreshing after Receive - stopping.")
                return FAILED

            # What happened is decided by COUNTING the rows that look like this
            # one, not by identifying which row is which.
            #
            # Identity cannot answer this question. Two stacks may be identical
            # in name, quantity AND price -- routine on this shop, which
            # regularly holds two Force Core(High) at the same price -- and
            # after collecting one, the survivor matches the ref perfectly.
            # Asking "is my listing still there?" then gets a confident yes
            # about a DIFFERENT stack:
            #
            #   two identical stacks   -> exactly one match survives, read as
            #                             "the click did not take", so the
            #                             sibling is collected too. Two stacks
            #                             pulled off the market for one sale.
            #   three identical stacks -> two survive, locate_row says
            #                             'ambiguous', the run stops with the
            #                             collected item stranded in the work
            #                             tab, and every later cycle then fails
            #                             its empty-tab precondition.
            #
            # Both live runs on 2026-08-04 ended this way. Counting is immune:
            # collecting a stack removes exactly one row from the family
            # whether or not its siblings are distinguishable, and it still
            # works when the QTY column is unreadable, which is precisely when
            # identity matching is weakest.
            def family(table: list[Row]) -> list[Row]:
                return listing_family(table, target.name, target.price)

            def quantities(pool: list[Row]) -> list:
                return family_quantities(pool)

            before = quantities(family(rows))

            # Make the client REFETCH before counting. wait_for_table above
            # only waits for a reload to finish; it does not cause one, so the
            # poll below was reading the client's stale copy -- which still
            # shows the pre-sale quantity however long it is polled.
            #
            # Measured on the 08:27 run: 16 collects across rows 2-8 polled the
            # full budget, concluded "the click did not take", and retried. On
            # the retry -- which reopens the shop, and therefore refreshes --
            # the row read as [change] with the collected quantity already
            # gone. The collect had worked every time. That is ~45s of polling
            # plus a whole wasted attempt per sale, and it made a working
            # collect look like a failing one in the log.
            refresh_table(timeout=timeout, verbose=False)

            after: list = []
            after_rows: list[Row] = []
            saw_table = False
            deadline = time.monotonic() + max(timeout, TABLE_READ_BUDGET)
            while time.monotonic() < deadline:
                rows_now = read_rows(grab())
                if rows_now:
                    saw_table = True
                    after_rows = family(rows_now)
                    after = quantities(after_rows)
                    if after != before:
                        break
                time.sleep(0.8)

            if not saw_table:
                say("The table could not be read while checking for a "
                    "remainder - stopping rather than assuming it sold out.")
                return FAILED

            # Multiset difference: what left the family, and what appeared.
            lost, gained = collect_delta(before, after)

            if not lost and not gained:
                # Says what was MEASURED. The old wording claimed the row
                # "still shows Receive", which this code never checked -- and
                # it was wrong every time it printed during the 08:27 run,
                # where the collect had in fact gone through and only the
                # client's copy of the table was stale.
                priced = (f"at {target.price:,} " if target.price is not None
                          else "")
                say(f"The {target.name!r} listings {priced}are unchanged "
                    f"after collecting ({before}) - the click did not take; "
                    f"retrying.")
                continue

            if len(lost) == 1 and not gained:
                say(f"{target.name!r} is no longer in the table - fully sold "
                    "and collected.")
                return SOLD_OUT

            if len(lost) == 1 and len(gained) == 1:
                # A partial sale: the stack shrank rather than vanishing. The
                # remainder is the row carrying the quantity that appeared.
                candidates = [r for r in after_rows if r.qty == gained[0]]
                if len(candidates) != 1:
                    say(f"A remainder of {gained[0]} appeared but "
                        f"{len(candidates)} rows carry it, so which one is the "
                        "remainder cannot be told - it will be picked up next "
                        "cycle rather than relisting the wrong stack.")
                    return FAILED
                row = candidates[0].index
                say(f"Partially sold: {lost[0]} -> {gained[0]} at row {row} "
                    "- relisting the remainder.")
                continue

            # Anything else means the family changed in a way one collect does
            # not explain -- another stack selling during the same few seconds,
            # most likely. Saying so beats guessing.
            say(f"The {target.name!r} listings changed in a way this collect "
                f"does not explain (was {before}, now {after}) - leaving them "
                "for the next cycle rather than acting on a table that moved "
                "underneath us.")
            return FAILED

        if target.action != "change":
            say(f"Row {row} shows '{target.action}', not 'Change' - nothing to relist.")
            return FAILED

        original = target.price
        if original is None:
            say(f"Could not read the current price of row {row} ({target.name!r}); "
                "refusing to relist without a price to sanity-check against.")
            return FAILED
        # A truncated read is worse than no read: every guard is derived from
        # this number, and `if original` / `if price_floor` short-circuit to
        # nothing when it is small, so a clipped 1,000,000,000 read as 0 would
        # disable the 5% floor, the absolute floor and the suspect check at once.
        if original < MIN_PLAUSIBLE_PRICE:
            say(f"Row {row} ({target.name!r}) priced at {original:,} Alz, below "
                f"the {MIN_PLAUSIBLE_PRICE:,} plausibility floor - the price "
                "column was probably misread. Refusing to relist it.")
            return FAILED

        # The NAME deserves the same treatment as the price, because the price
        # floor is derived from it. An unreadable name reads as "(empty)",
        # whose floor is 0 -- so a VIP whose name column blanked for one frame
        # would relist at whatever the market said, with nothing to stop it.
        if target.name == "(empty)" or len(_floor_key(item_name(target.name))) < 6:
            say(f"Row {row}'s name did not read ({target.name!r}). The price "
                "floor is looked up from the name, so relisting without one "
                "could list a floored item unprotected. Refusing.")
            return FAILED

        max_qty = wants_max_quantity(target.name)
        say(f"[relist 1/2] row {row}: {target.name!r} at {original:,} Alz")
        say("             will relist at the lowest current market price")
        if max_qty:
            say("             quantity will be maximised")

        # Snapshot the inventory so the item can be followed to wherever the
        # cancel drops it, instead of assuming a slot.
        before = origin = start_tab = None
        if not dry_run and (inv_row is None or inv_col is None):
            focus_game()
            park_cursor()
            before = grab()
            # Resolve the grid anchor now, while the panel is known-visible.
            origin = inventory_origin(before) or inventory_origin()
            if origin is None:
                say("The Inventory panel is not visible, so the returned item "
                    "could not be followed. Open it (or pass an explicit slot) "
                    "and rerun. Nothing has been cancelled yet.")
                return FAILED
            # Remember the tab so we can come back to it: cancelling a large
            # stack scatters items across tabs, and the game may switch away.
            start_tab = active_inventory_tab(before, origin)
            if start_tab is None:
                say("Could not tell which inventory tab is open, so the "
                    "returned items could not be followed reliably. "
                    "Nothing has been cancelled yet.")
                return FAILED
            # It must be the work tab. require_empty_work_tab verified tab
            # WORK_TAB was empty, but the diff is taken against whatever tab
            # happens to be showing -- so if they differ, "empty" was checked
            # on one tab and "what came back" measured on another, and every
            # item already sitting on the visible tab reads as newly returned.
            if start_tab != WORK_TAB:
                say(f"Inventory tab {start_tab} is open, but the work tab is "
                    f"{WORK_TAB} - the emptiness check and the returned-item "
                    "diff would be looking at different tabs. Nothing has been "
                    "cancelled yet.")
                return FAILED
            record("inventory.before_cancel", tab=start_tab, origin=str(origin))
            say(f"Inventory tab {start_tab} is open; will return to it after "
                "cancelling.")

        # Hand the identity down, not just the number: cancel_item re-reads the
        # table and would otherwise cancel whatever now sits at this index.
        if not cancel_item(row, dry_run=dry_run, timeout=timeout, verbose=verbose,
                           expect=RowRef.of(target, rows)):
            # Deliberately not "nothing will be listed": cancel_item returns
            # False both when nothing happened AND when it committed but could
            # not verify, and it has just printed which. Asserting the stronger
            # claim here contradicted its own warning two lines earlier.
            say("Cancel did not complete - see above for what state it left. "
                "Nothing further will be listed this cycle.")
            return FAILED

        if not dry_run and not wait_for_table(max(timeout, 20.0)):
            # The cancel committed one statement earlier, so this is a
            # strand too. The comment on the sibling exit below named this
            # very line as still missing its warning; here it is.
            record("relist.stranded", stage="table_refresh", row=row,
                   item=target.name, qty=target.qty, tab=start_tab)
            say("The table did not finish refreshing after the cancel.")
            say(f"IMPORTANT: row {row} was already cancelled, so "
                f"{target.name!r} x{target.qty} is in inventory tab "
                f"{start_tab}, UNLISTED. Later cycles will fail their "
                "empty-work-tab check until it is cleared.")
            return FAILED

        slot = (inv_row, inv_col) if inv_row and inv_col else None
        if slot is None:
            if dry_run:
                say("[dry run] would locate the returned item by diffing the inventory")
                slot = (1, 1)
            else:
                # The game can switch tabs when a big stack comes back; go back
                # to the one the diff's "before" frame was taken on, or the
                # comparison is meaningless.
                if not select_inventory_tab(start_tab, origin):
                    say(f"Could not return to inventory tab {start_tab}.\n"
                        f"IMPORTANT: row {row} has already been cancelled - "
                        f"{target.name!r} is in your inventory, unlisted.")
                    return FAILED
                park_cursor()

                # The single most useful frame in a failed cycle: it shows
                # where the item actually came back to. Captured once and
                # reused for the diff, so recording costs nothing.
                after = grab()
                record("inventory.after_cancel", after, tab=start_tab)
                returned = changed_slots(before, after, origin)
                if not returned:
                    record("inventory.diff_empty", after, tab=start_tab)
                    say(f"Could not tell which slot on tab {start_tab} "
                        f"{target.name!r} returned to, so refusing to list an "
                        "unidentified item.\n"
                        f"IMPORTANT: row {row} has already been cancelled - "
                        f"{target.name!r} is sitting in your inventory, unlisted.\n"
                        "Re-list it with: --register INV_ROW INV_COL")
                    return FAILED
                where = ", ".join(f"{r},{c}" for r, c in returned)
                record("inventory.returned", after, tab=start_tab, slots=where,
                       count=len(returned), taking=f"{returned[0]}")
                say(f"{target.name!r} returned to {len(returned)} slot(s) on "
                    f"tab {start_tab}: {where}")
                slot = returned[0]

        say(f"\n[relist 2/2] listing inventory slot ({slot[0]},{slot[1]})")
        report: dict = {}
        # floor_price is passed for reporting only -- it is the price the
        # listing had before, used to note a large drop, never to override one.
        listed = register_item(*slot, dry_run=dry_run,
                               timeout=timeout, verbose=verbose,
                               floor_price=original, maximise_qty=max_qty,
                               expect_item=target.name, expect_qty=target.qty,
                               report=report)
        if not listed and not report.get("committed"):
            # THE path that cost five hours. The cancel committed ~60 lines
            # above, so the item is out of the shop and sitting in the work
            # tab -- and this returned FAILED without a word. register_item
            # printed its own abort, but nothing said the consequence: every
            # later cycle now fails require_empty_work_tab identically until a
            # human clears it, which is exactly what the failure breaker is
            # for and exactly why it fired three cycles later.
            #
            # Two of the six post-commit exits already say this. This one and
            # the table-refresh exit below did not.
            record("relist.stranded", stage="register_failed", row=row,
                   item=target.name, qty=target.qty, tab=start_tab)
            say(f"\nIMPORTANT: row {row} was cancelled, so {target.name!r} "
                f"x{target.qty} is now in inventory tab {start_tab}, UNLISTED.")
            say("Every later cycle will fail its empty-work-tab check until "
                "that is cleared, so the run will stop after "
                f"{MAX_CONSECUTIVE_FAILURES} of them.")
            say(f"Re-list it by hand with:  --register INV_ROW INV_COL")
            return FAILED
        if dry_run:
            return RELISTED
        if not listed:
            # The listing IS live -- register_item committed and then failed a
            # post-commit check (a dialog slow to close, the shop slot slow to
            # clear). Returning here skipped sanity_check and left a live,
            # unverified listing on the market, so fall through and check it.
            say("The listing was committed before the failure, so it is on the "
                "market. Verifying it against the table rather than assuming.")

        # Verify the outcome, not just the panel we filled in. A mismatch means
        # something was listed that should not have been: pull it back off the
        # shop and stop everything rather than leaving it on the market.
        found: dict = {}
        if not sanity_check(target.name, report.get("price"), report.get("qty"),
                            timeout=timeout, verbose=verbose, found=found):
            bad = found.get("row")
            if bad is None:
                # Could not verify (table unreadable, refresh failed). That is
                # not evidence of a wrong listing, so it must not kill the run:
                # retry next cycle rather than raising.
                say(f"Could not verify the listing for {target.name!r}. "
                    "Nothing was withdrawn; will be checked again next cycle.")
                return FAILED
            # Format defensively: these strings are the only account of what
            # went wrong, and a None slipping into a ',' format raises
            # TypeError *instead of* the FatalAbort -- which escapes run_loop's
            # handler entirely and kills the process after the withdrawal has
            # already committed.
            def money(value: int | None) -> str:
                return f"{value:,}" if isinstance(value, int) else "an unreadable price"

            say(f"Withdrawing the mismatched listing on row {bad.index} "
                f"({bad.name!r})...")
            # The withdrawal must not be able to swallow the FatalAbort. It is
            # called before the raise, outside any handler, and it runs OCR and
            # input -- so an OSError or a refused click there meant the abort
            # was never constructed, the wrong listing stayed on the market,
            # and the unattended loop carried on.
            try:
                withdrawn = cancel_item(bad.index, expect=RowRef.of(bad, [bad]),
                                        timeout=timeout, verbose=verbose)
            except FatalAbort:
                raise
            except Exception as exc:  # noqa: BLE001 - must still report
                raise FatalAbort(
                    f"listed {bad.name!r} at {money(bad.price)}, which does not "
                    f"match what was registered, AND the withdrawal itself "
                    f"failed ({type(exc).__name__}: {exc}). It is still on the "
                    "shop - remove it by hand."
                ) from exc
            if withdrawn:
                raise FatalAbort(
                    f"listed {bad.name!r} at {money(bad.price)} which does not "
                    f"match the {money(report.get('price'))} that was "
                    "registered. It has been withdrawn from the shop and the "
                    "run stopped."
                )
            raise FatalAbort(
                f"listed {bad.name!r} at {money(bad.price)}, which does not "
                f"match what was registered, AND it could not be withdrawn. It "
                "is still on the shop - remove it by hand."
            )
        return RELISTED

    say(f"Gave up after {attempts} attempts - the listing kept coming back sold.")
    return FAILED


def parse_row_spec(specs: list[str]) -> list[int]:
    """Turn CLI row specs into row numbers: '1-10', '1,3,5' and '1 3 5' all work."""
    rows: list[int] = []
    for spec in specs:
        for chunk in str(spec).replace(",", " ").split():
            if "-" in chunk[1:]:
                lo, _, hi = chunk.partition("-")
                start, stop = int(lo), int(hi)
                if start > stop:
                    raise ValueError(f"range {chunk!r} runs backwards")
                rows.extend(range(start, stop + 1))
            else:
                rows.append(int(chunk))
    seen: set[int] = set()
    return [r for r in rows if not (r in seen or seen.add(r))]


def sanity_check(
    name: str,
    price: int | None,
    qty: int | None,
    timeout: float = 8.0,
    verbose: bool = True,
    found: dict | None = None,
) -> bool:
    """After relisting, confirm the table really holds what we meant to list.

    Everything up to this point verifies the *panel* -- the price selected, the
    quantity typed, the shop slot emptying. This checks the outcome instead:
    that a row now exists for `name`, priced at `price` for `qty`. It is the
    only step that would catch the whole sequence having acted on the wrong
    item or the wrong figure.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    record("sanity.start", name=name, price=price, qty=qty)
    if not refresh_table(timeout=max(timeout, 20.0), verbose=verbose):
        say("  could not refresh the table to check the listing.")
        return False
    park_cursor()

    rows = await_rows(timeout)
    if not rows:
        say("  the table could not be read.")
        return False

    # Identify by what was just registered, not by name alone: another stack of
    # the same item may well be listed too. `strict` stops it guessing between
    # them -- the failure path here withdraws a listing, so an unresolved
    # duplicate has to read as "cannot verify", never as "wrong item".
    ref = RowRef(name, qty, price)
    changeable = [r for r in rows if r.action == "change"]

    # Several rows all matching what was registered is not ambiguity, it is
    # confirmation: whichever one is ours, a row carrying this name at this
    # price and quantity exists, which is the whole question. Asking
    # locate_row first returned 'ambiguous' for two identical stacks and
    # stalled the batch on its first row, every cycle, with nothing wrong.
    witnesses = [r for r in changeable
                 if _canonical(r.name) == _canonical(name)
                 and (price is None or r.price == price)
                 and (qty is None or r.qty is None or r.qty == qty)]
    if witnesses:
        say(f"  row {witnesses[0].index} matches what was registered"
            + (f" ({len(witnesses)} identical rows)" if len(witnesses) > 1 else "")
            + ".")
        return True

    listed, note = locate_row(changeable, ref, strict=True)
    if listed is None:
        if note == "ambiguous":
            say(f"  several rows are named {name!r} and none carries the price "
                "and quantity just registered, so the new listing cannot be "
                "identified. Not withdrawing anything.")
            return False
        # 'missing' is not "cannot verify" -- it is the strongest evidence the
        # script has that the WRONG ITEM was listed: no row carries the name we
        # believe we just registered. Treating it as unverifiable left a
        # wrongly-listed item on the market and returned a retryable failure,
        # which with the tooltip check removed meant nothing detected it at all.
        #
        # So look for the row holding the figures we registered, whatever it is
        # called, and hand it back to be withdrawn.
        # Report it; do NOT withdraw anything.
        #
        # An earlier version cancelled the row carrying the registered price
        # and quantity, on the theory that a name that is not there means the
        # wrong item was listed. That is one explanation of 'missing'; the
        # others are that our own row's name flaked on this single frame, that
        # the new listing sold before the check, or that nothing listed at all.
        # match_rows deliberately rejects a single substituted character, so
        # ONE bad glyph reaches here -- and the row it then identified as the
        # "suspect" was our own correct listing, whose price and quantity read
        # fine. Cancelling a 119M listing over one character, off one frame, is
        # far worse than leaving a wrong listing up and saying so loudly.
        say(f"  {name!r} is not in the table after relisting it.")
        suspect = [r for r in changeable if price is not None and r.price == price]
        if suspect:
            say("  these rows carry the price just registered: "
                + ", ".join(f"row {r.index} ({r.name!r})" for r in suspect[:4]))
            say("  that is most likely this listing with a misread name. "
                "Nothing has been withdrawn.")
        else:
            say("  nothing carries that price either - the listing may have "
                "sold, or may never have been created.")
        say("  CHECK THE SHOP BY HAND before the next run: if the wrong item "
            "was listed, it is still on the market.")
        return False
    say(f"  row {listed.index} names {listed.name!r} - matches what was relisted.")

    # An unreadable figure is "cannot verify", not "mismatch". Treating it as a
    # mismatch both condemned a correct listing and crashed formatting None.
    if price is not None and listed.price is None:
        say("  the listed price could not be read, so this cannot be verified.")
        return False
    if qty is not None and listed.qty is None:
        say("  the listed quantity could not be read; checking the price only.")

    # Only a PRICE mismatch may lead to withdrawing a listing.
    #
    # The quantity on both sides of this comparison is unreliable: the expected
    # value is derived from net sales, and the observed one comes from the QTY
    # column, the worst OCR target in the table. A quantity difference also
    # cannot cost anything -- the game clamps entry to the stack, so listing
    # too many is impossible -- whereas acting on one destroys a good listing.
    # A stack of 250 reading as '25O' -> 25 is enough to trigger it, and
    # re-reading does not help because the misread is systematic, not a flake.
    if qty is not None and listed.qty is not None and listed.qty != qty:
        say(f"  note: quantity reads {listed.qty}, expected {qty} - not acting "
            "on that; the quantity column is not reliable enough to withdraw on.")

    problems = []
    if price is not None and listed.price is not None and listed.price != price:
        problems.append(f"price is {listed.price:,}, expected {price:,}")

    if problems:
        # Confirm before acting: the consequence is cancelling a listing, and
        # one flaked digit would destroy a correct one. Every other guard in
        # this file polls; this one used a single frame.
        say(f"  possible mismatch on row {listed.index}: " + "; ".join(problems))
        say("  re-reading to confirm before withdrawing anything...")
        time.sleep(1.0)
        recheck = await_rows(timeout)
        again, _ = locate_row([r for r in recheck if r.action == "change"],
                              ref, strict=True)
        if again is None:
            say("  the row could not be identified on the re-read; "
                "not withdrawing.")
            return False
        # Unreadable is not evidence of anything. The first read is guarded
        # this way already; the re-read was not, so a price cell that came back
        # empty fell through to "MISMATCH confirmed" and withdrew a correct
        # listing -- then crashed formatting None, destroying the FatalAbort
        # that was supposed to carry the story out.
        if again.price is None:
            say("  the price could not be read on the re-read; not withdrawing.")
            return False
        if again.price < MIN_PLAUSIBLE_PRICE:
            say(f"  the re-read price {again.price:,} is below the "
                f"{MIN_PLAUSIBLE_PRICE:,} plausibility floor, so it is a "
                "misread rather than a mismatch; not withdrawing.")
            return False
        if again.price == price:
            say(f"  second read agrees with what was registered "
                f"({again.price:,}) - the first read was a misread.")
            return True
        say(f"  MISMATCH confirmed on row {again.index} ({again.name!r}): "
            f"price {again.price:,}, quantity {again.qty}")
        if found is not None:
            found["row"] = again
        return False

    say(f"  row {listed.index}: {listed.name!r} at {listed.price:,} Alz"
        + (f" x{listed.qty}" if listed.qty is not None else "") + " - matches.")
    return True


def ensure_shop_ready(verbose: bool = True) -> bool:
    """Focus the game and get the Agent Shop open on the Register tab.

    Needed before any table read: each relist() closes the shop behind it, so
    a caller that reads the table between relists starts from a closed window.
    """
    if not focus_game():
        if verbose:
            print("Could not bring Cabal to the foreground.")
        return False
    park_cursor()
    return open_trade_window(verbose=verbose)


def relist_rows(
    rows: list[int],
    dry_run: bool = False,
    timeout: float = 8.0,
    verbose: bool = True,
) -> bool:
    """Relist several rows, tracking each by name rather than by position.

    Cancelling a listing empties its row, and registering fills the first empty
    row -- which is not always the one the item came from. Row numbers therefore
    shift during a batch, so each item is re-located by name immediately before
    it is relisted. Empty rows are skipped; any real failure stops the batch.

    Rows beyond the visible ten are supported: the shop holds thirty, and a
    sale sitting at row 25 was previously never collected because the loop
    could not see it. Asking for one enumerates the shop once, then scrolls
    each listing into view by identity before acting on it.

    A batch that asks only for rows 1-10 takes none of that: no enumeration, no
    scrolling, and the same single table read it always did. Scrolling costs a
    table read per chunk, and the common case should not pay for the rare one.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    # Open the shop before reading anything: relist() closes it after each row.
    if not dry_run:
        if not ensure_shop_ready(verbose=verbose):
            say("Could not open the Agent Shop to read the listings.")
            return False
        if not require_empty_work_tab(verbose=verbose):
            say("Aborting: the working inventory tab must be empty to start.")
            return False

    snapshot = await_rows(timeout)
    if not snapshot:
        say("No listings visible - is the Trade window open on the Register tab?")
        return False

    # Rows past the first screen need the whole shop enumerated first, because
    # their identity has to be taken from the full list: a RowRef built against
    # ten visible rows counts its ordinal in the wrong pool.
    #
    # Rows 1-10 keep the cheap path exactly as it was -- one table read, no
    # scrolling, no enumeration. Scrolling costs a table read per chunk (~18s
    # of OCR each), so making every batch pay for it would slow the common case
    # by minutes to serve a case it does not have.
    beyond = [i for i in rows if i > len(snapshot)]
    scrolling = bool(beyond)
    targets: list[tuple[int, RowRef, str]] = []

    if scrolling:
        say(f"Row(s) {', '.join(str(i) for i in beyond)} are past the first "
            f"screen of {len(snapshot)}; enumerating the whole shop.")
        listings = enumerate_listings(timeout=timeout, verbose=verbose)
        if listings is None:
            say("The shop could not be enumerated, so rows past the first "
                "screen cannot be addressed safely - stopping rather than "
                "acting on a position that might be the wrong listing.")
            return False
        catalogue = [row for _, row in listings]
        by_index = dict(listings)
        for index in rows:
            row = by_index.get(index)
            if row is None:
                say(f"Row {index} is out of range; the shop holds "
                    f"{len(listings)} listing(s).")
                return False
            targets.append((index, RowRef.of(row, catalogue), row.action))
    else:
        for index in rows:
            if not 1 <= index <= len(snapshot):
                say(f"Row {index} is out of range; {len(snapshot)} row(s) visible.")
                return False
            row = snapshot[index - 1]
            targets.append((index, RowRef.of(row, snapshot), row.action))

    say(f"Relisting {len(targets)} row(s), tracked by name, quantity and price:")
    for index, ref, action in targets:
        priced = f"{ref.price:,} Alz" if ref.price is not None else "price unread"
        say(f"  {index:2d}. [{action}] {ref.name} x{ref.qty} at {priced}")

    worked = 0

    failed_rows: list[str] = []
    for position, (index, ref, action) in enumerate(targets, start=1):
        name = ref.name
        say(f"\n########## {position}/{len(targets)}: row {index} - {name!r} ##########")

        if action == "register" or name == "(empty)":
            say("Empty slot - nothing to relist, skipping.")
            continue

        # The previous relist closed the shop, so reopen before reading, then
        # re-read: earlier relists in this batch may have moved things.
        # A sold row (Receive) is still a candidate -- relist() collects it.
        if not dry_run and not ensure_shop_ready(verbose=verbose):
            say("Could not reopen the Agent Shop - stopping.")
            return False
        # Past the first screen the listing has to be scrolled to. Identity,
        # not the index: the shop is reopened between rows so the view is back
        # at the top, and earlier relists in this batch have renumbered
        # everything below whatever they touched.
        live = (bring_into_view(ref, timeout=timeout, verbose=verbose)
                if scrolling else await_rows(timeout))
        # An unreadable table is not an empty shop. Without this guard the
        # chain launders "I cannot see the table" into "the item sold": an
        # empty read makes locate_row report 'missing', every row is skipped
        # with "already sold out", and the batch returns True -- so run_loop
        # counts a SUCCESS, resets the consecutive-failure counter, and an
        # eight-hour run reports every cycle green having touched nothing.
        if not live:
            say("The listings could not be read - stopping rather than "
                "treating an unreadable table as an empty shop.")
            return False
        current = [r for r in live if r.action in ("change", "receive")]
        match, note = locate_row(current, ref)
        if match is None and note == "unmatched":
            # Something very like this row is on screen but its name did not
            # read cleanly. Skipping would report a live listing as sold and
            # let the batch finish "successfully" having refreshed nothing.
            say(f"{name!r} is on the table but its name did not read clearly "
                "enough to act on. Stopping rather than skipping a live "
                "listing as though it had sold.")
            return False
        if match is None:
            say(f"{name!r} is no longer in the table - already sold out, skipping.")
            continue
        if note:
            say(f"  {note}")
        if match.index != index:
            say(f"Moved: now at row {match.index}.")

        # Pass the identity, not just the number: relist reopens the shop and
        # refreshes the table, so this index is resolved against a different
        # read than the one that produced it.
        outcome = relist(match.index, dry_run=dry_run, timeout=timeout,
                         verbose=verbose, expect=ref)
        if outcome == SOLD_OUT:
            # Collecting a sale IS work: the row was found, acted on, and the
            # proceeds taken. Only a row that was never located counts as a
            # no-op for the purposes of the check at the end.
            worked += 1
            say(f"{name!r} sold out - collected, nothing to relist. Moving on.")
            continue
        if outcome != RELISTED:
            # A failure on ONE row is not evidence about the other nine -- but
            # some failures leave debris that makes every later row fail too,
            # and those must stop the batch. The difference is decidable, not a
            # guess: if the work tab is still empty, the cancel either did not
            # happen or was undone, and the next row starts from a clean state.
            # If it is dirty, an item is stranded there and every subsequent
            # row would fail its precondition identically.
            #
            # Measured cost of not making this distinction: three consecutive
            # cycles each relisted row 1, failed on row 2, never attempted rows
            # 3-10, and still counted as total failures -- which tripped the
            # breaker on a run that was doing half its work. One poison row
            # froze 80% of the shop.
            left = len(targets) - position
            if not dry_run and left and require_empty_work_tab(verbose=False):
                say(f"Relisting {name!r} failed, but inventory tab {WORK_TAB} "
                    f"is clean, so the failure is confined to this row - "
                    f"continuing with {left} row(s) still to go.")
                failed_rows.append(name)
                continue
            if not dry_run and left:
                say(f"Relisting {name!r} failed AND inventory tab {WORK_TAB} is "
                    "not clean, so every later row would fail the same way.")
            say(f"Relisting {name!r} failed - stopping; "
                f"{len(targets) - position} row(s) not attempted.")
            return False
        worked += 1

        if not dry_run and not wait_for_table(max(timeout, 20.0)):
            # This row's work is COMMITTED and already verified against the
            # table. The wait exists only so the NEXT row does not read a
            # mid-refresh table, so a timeout here says nothing about what was
            # just done -- and throwing the batch away over it discards
            # finished, confirmed relists.
            #
            # Measured cost: on 2026-08-04 rows 1 and 2 were relisted and both
            # confirmed ("row 2 matches what was registered"), then this
            # timeout abandoned rows 3-10 and scored the cycle a failure. The
            # run had done exactly what it was asked and moved one step closer
            # to the failure breaker for it.
            #
            # Same decidable test used for a failed row above: a clean work tab
            # means nothing is stranded and the next row starts fresh.
            left = len(targets) - position
            if not left:
                break                 # last row; there is nothing left to read
            if require_empty_work_tab(verbose=False):
                say(f"The table did not finish refreshing after {name!r}, but "
                    f"the relist completed and inventory tab {WORK_TAB} is "
                    f"clean - continuing with {left} row(s) still to go.")
                continue
            say(f"The table did not finish refreshing after {name!r} AND "
                f"inventory tab {WORK_TAB} is not clean - stopping.")
            return False

    # A batch that did no work is not a success. Every row reporting "already
    # sold out" returned True, which made run_loop count a green cycle and
    # reset the consecutive-failure counter -- so a run could report hundreds
    # of successful cycles having relisted nothing at all. Rows that were
    # genuinely empty in the snapshot do not count against this.
    actionable = sum(1 for _, _, action in targets
                     if action in ("change", "receive"))
    if actionable and not worked:
        say(f"\nNone of the {actionable} live row(s) were relisted - every one "
            "read as already sold. That is not a successful cycle; stopping so "
            "it is not reported as one.")
        return False

    if failed_rows:
        # A partial batch must not read as a clean one. It is still a success
        # -- work was done and the remaining rows were reached, which is the
        # whole point of continuing past a bad row -- but the rows that failed
        # have to be named, or "All 10 rows processed" hides them.
        say(f"\n{len(failed_rows)} row(s) failed and were skipped: "
            + ", ".join(repr(n) for n in failed_rows))
        say("Each was confined to its own row - the work tab stayed clean, so "
            "the rest of the batch continued. They will be retried next cycle.")
    say(f"\nAll {len(targets)} row(s) processed"
        + (f" ({worked} relisted" if worked else " (none relisted")
        + (f", {len(failed_rows)} failed)." if failed_rows else ")."))
    return True


def run_sequence(actions: list[str], dry_run: bool = False, verbose: bool = True) -> bool:
    """Run actions back to back, stopping at the first failure.

    Each action is a string: 'cancel ROW', 'register ROW COL', or 'clear'.

    Row numbers are resolved fresh for each action, immediately before it runs.
    That matters: cancelling a row renumbers everything below it, so a sequence
    written against the table as it looks now would otherwise hit wrong rows.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    for position, spec in enumerate(actions, start=1):
        parts = spec.split()
        if not parts:
            say(f"[{position}/{len(actions)}] empty action - stopping.")
            return False

        verb, args = parts[0].casefold(), parts[1:]
        say(f"\n[{position}/{len(actions)}] {spec}")

        try:
            if verb == "cancel" and len(args) == 1:
                ok = cancel_item(int(args[0]), dry_run=dry_run, verbose=verbose)
            elif verb == "register" and len(args) in (2, 3):
                # An optional PRICE, because register_item refuses to price an
                # item it cannot name -- and this verb has no way to name one.
                # Without this the verb could never succeed at all.
                forced = int(args[2]) if len(args) == 3 else None
                ok = register_item(int(args[0]), int(args[1]),
                                   dry_run=dry_run, verbose=verbose,
                                   force_price=forced)
            elif verb == "relist" and len(args) in (1, 3):
                slot = (int(args[1]), int(args[2])) if len(args) == 3 else (None, None)
                # Sold out is not a failure: there was simply nothing to relist.
                ok = relist(int(args[0]), *slot,
                            dry_run=dry_run, verbose=verbose) != FAILED
            elif verb in ("relist-rows", "relist_rows") and args:
                ok = relist_rows(parse_row_spec(args), dry_run=dry_run, verbose=verbose)
            elif verb == "clear" and not args:
                ok = True if dry_run else clear_shop_slot(verbose=verbose)
            else:
                say(f"Unknown action {spec!r}. Use 'cancel ROW', "
                    "'register ROW COL', 'relist ROW [R C]', "
                    "'relist-rows 1-10', or 'clear'.")
                return False
        except ValueError:
            say(f"Action {spec!r} has a non-numeric argument.")
            return False
        except Aborted as exc:
            # PermissionError is deliberately NOT caught here. Catching it
            # turned "input is being refused" into an ordinary retryable cycle
            # failure, so an unattended loop retried a blocked run every tick
            # for hours, printing the same message and achieving nothing. It
            # now propagates to run_loop, which stops.
            say(f"Stopped: {exc}")
            return False

        if not ok:
            say(f"Action {position} failed - stopping; "
                f"{len(actions) - position} action(s) not attempted.")
            return False

        # Both actions make the client refetch the table; the next action must
        # not read it mid-refresh.
        if not dry_run and not wait_for_table():
            say("The table did not finish refreshing - stopping.")
            return False

    say(f"\nAll {len(actions)} action(s) completed.")
    return True


def prepare_for_actions(verbose: bool = True) -> bool:
    """Put the game into a state where a cycle can run.

    A failed cycle leaves debris -- a dialog part-way through, an item stranded
    in the shop slot, the Trade window closed by the game -- so this clears all
    of it before the next attempt rather than failing the same way again.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not focus_game():
        say("Could not bring Cabal to the foreground.")
        return False

    # Clear any modifier a previous cycle may have left held. Nothing else in
    # the run would ever detect a stuck Ctrl, and with it held every plain
    # click becomes a Ctrl+Click, which moves items into the shop slot.
    release_modifiers()
    park_cursor()

    # Escape back to the default state. Pressed one at a time and rechecked:
    # a blind second press would close a dialog and then open the system menu.
    for attempt in range(ESCAPE_ATTEMPTS):
        # dialog_present, not dialog_kind: this is the other place a flaked
        # title read let a modal survive into the next cycle, where it covered
        # the table and produced "No listings visible" before a single row had
        # been touched.
        if not dialog_present():
            break
        say(f"Dialog still open - pressing Escape ({attempt + 1}).")
        press_escape()
    else:
        # Escape did not clear it; fall back to backing out with Cancel.
        if not close_any_dialog():
            say("Could not close a dialog left open on screen.")
            return False

    if not open_trade_window(verbose=verbose):
        say("Could not open the Trade window on the Register tab.")
        return False

    # Re-measure the layout now the window is up, every cycle. The Trade window
    # can be dragged between cycles without the screen or client rect changing,
    # and every coordinate below is relative to where it sits -- so carrying a
    # measurement forward from the previous cycle is exactly the stale-layout
    # risk that reusing a saved one carries.
    if not calibrate(verbose=verbose, save=False):
        say("Could not measure the layout for this cycle - not acting on "
            "coordinates that have not been checked.")
        return False

    if not clear_shop_slot(verbose=verbose):
        say("Could not clear the shop slot.")
        return False

    return True


def run_loop(
    actions: list[str],
    minutes: float,
    every: float,
    dry_run: bool = False,
    verbose: bool = True,
) -> bool:
    """Repeat `actions` every `every` minutes for `minutes` minutes.

    The interval is measured from the start of each cycle, so a slow cycle eats
    into the wait rather than pushing everything later. A cycle that overruns
    the interval simply starts the next one immediately. `every=0` means never
    wait: cycles run back to back until the duration is up.

    Every cycle begins by reopening the Trade window and clearing any debris a
    previous failure left behind. A failed cycle is therefore not fatal: it is
    reported and retried on the next tick. Only a locked workstation stops the
    loop outright, since nothing can work through that.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    interval = every * 60.0
    end = time.monotonic() + minutes * 60.0
    cycle = succeeded = failures = consecutive = 0
    stopped = False

    if every:
        cadence = (f"every {every:g} min for {minutes:g} min "
                   f"(about {max(1, int(minutes / every))} cycles)")
    else:
        # How many fit depends on how long a cycle takes, so do not guess.
        cadence = f"back to back for {minutes:g} min"
    say(f"Looping {actions} {cadence}. A failed cycle is retried on the next "
        f"tick. Ctrl+C to quit.")

    if keep_awake(True):
        say("Holding off display/system sleep for the duration.")
    else:
        say("WARNING: could not inhibit sleep; the run may be cut short by it.")

    try:
        while time.monotonic() < end:
            cycle += 1
            started = time.monotonic()
            say(f"\n===== cycle {cycle} at {datetime.now():%H:%M:%S} =====")
            # A gap in the index is only diagnostic if the boundaries are in
            # it. A cycle.start with no cycle.end means it died mid-cycle; a
            # cycle.end with no following start means the loop exited, and
            # the loop.stopped beside it says why. Without these, three
            # cycles that each failed in a record()-free function left NO
            # frames at all -- which is what made a five-hour outage
            # unattributable.
            record("cycle.start", cycle=cycle, consecutive=consecutive,
                   succeeded=succeeded, failures=failures)

            # A locked workstation blanks captures and swallows input, so every
            # action would fail. Say why rather than emitting confusing errors.
            if session_locked():
                say("The workstation is locked - screen capture and input are "
                    "unavailable. Terminating the loop.")
                stopped = True
                break

            # Start from a known state: Trade window open on Register, no
            # dialog up, shop slot empty. This is what makes a failed cycle
            # recoverable on the next tick.
            try:
                if dry_run or prepare_for_actions(verbose=verbose):
                    if run_sequence(actions, dry_run=dry_run, verbose=verbose):
                        succeeded += 1
                        consecutive = 0
                    else:
                        failures += 1
                        consecutive += 1
                        say(f"\nCycle {cycle} failed - will retry next cycle.")
                else:
                    failures += 1
                    consecutive += 1
                    say(f"\nCycle {cycle}: could not get the game ready - "
                        "will retry next cycle.")
            except FatalAbort as exc:
                record("loop.stopped", reason="fatal", detail=str(exc),
                       cycle=cycle, consecutive=consecutive)
                # Not retryable: the run listed something it should not have.
                say(f"\nFATAL: {exc}")
                say("Terminating the loop; nothing further will be attempted.")
                failures += 1
                stopped = True
                break
            except PermissionError as exc:
                record("loop.stopped", reason="permission", detail=str(exc),
                       cycle=cycle, consecutive=consecutive)
                # Input is being refused -- not elevated, or the foreground
                # went to an elevated window or the secure desktop. Retrying
                # cannot fix it, and it used to escape the loop entirely from
                # prepare_for_actions (which is inside this try but was not
                # covered), killing the run with a raw traceback and no summary.
                say(f"\nInput was refused: {exc}")
                say("Terminating the loop; nothing further can be clicked.")
                failures += 1
                stopped = True
                break
            except Exception as exc:  # noqa: BLE001 - an unattended run must not die
                # The traceback, not just type(exc).__name__. A NameError at
                # line 4200 and one at 3900 print identically otherwise, and
                # this is the only handler standing between an unattended run
                # and an unexplained stop.
                record("cycle.exception", cycle=cycle, exc=type(exc).__name__,
                       detail=str(exc), traceback=traceback.format_exc(),
                       consecutive=consecutive)
                say(traceback.format_exc())
                failures += 1
                consecutive += 1
                say(f"\nCycle {cycle} raised {type(exc).__name__}: {exc}")
                if session_locked():
                    record("loop.stopped", reason="locked", cycle=cycle)
                    say("The workstation is locked - terminating the loop.")
                    stopped = True
                    break
                say("Will retry next cycle.")

            # Checked out here, after the handlers, so a cycle that RAISES also
            # counts towards it. With the check inside the try and the handler
            # not touching `consecutive`, a recurring exception -- a bad
            # coordinate, a NameError, a changed Tesseract path -- retried for
            # the whole duration, which is the exact failure this breaker was
            # added to stop.
            record("cycle.end", cycle=cycle, consecutive=consecutive,
                   succeeded=succeeded, failures=failures)
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                # THE entry that was missing. This breaker fired correctly at
                # 19:56 one night, printed an accurate diagnosis, and left no
                # trace on disk -- so a five-hour outage looked like an
                # unexplained crash rather than a deliberate, correct stop.
                record("loop.stopped", reason="consecutive_failures",
                       cycle=cycle, consecutive=consecutive)
                say(f"\n{consecutive} cycles have failed in a row - stopping "
                    "rather than repeating it for the rest of the run. Check "
                    f"the shop and inventory tab {WORK_TAB} by hand.")
                stopped = True
                break

            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            wait = min(max(0.0, interval - (time.monotonic() - started)), remaining)
            if wait > 0:
                say(f"Next cycle in {wait / 60:.1f} min "
                    f"({remaining / 60:.1f} min left overall).")
                time.sleep(wait)
            elif time.monotonic() - started < MIN_CYCLE_SECONDS:
                # Floor the turnaround. With --every 0 a cycle that does no
                # work -- a dry run, an unknown verb, an action list that
                # returns immediately -- costs microseconds, and the loop would
                # spin a core flat out for the whole duration.
                time.sleep(min(MIN_CYCLE_SECONDS - (time.monotonic() - started),
                               remaining))
    except KeyboardInterrupt:
        record("loop.stopped", reason="interrupt")
        say("\nInterrupted - stopping the loop.")
        stopped = True
    finally:
        keep_awake(False)

    say(f"\nDone: {cycle} cycle(s) run, {succeeded} succeeded, {failures} failed"
        + (f"; stopped early at cycle {cycle}." if stopped else "."))
    return succeeded > 0 and not stopped


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------
#
# Every geometry constant above was measured on one 2560x1440 machine. This
# section captures those values as a REFERENCE frame, measures the real frame
# on whatever machine is running, and rewrites the constants accordingly.
#
# Design notes, because the failure modes here are expensive:
#
#  * Measurement beats assumption. The Trade window is located by OCR of words
#    inside it, not by assuming where it sits.
#  * Scale is measured from the LONGEST available baseline. Two anchors a few
#    pixels apart would turn OCR jitter into a large scale error, which is
#    worse than not scaling at all, so short baselines are rejected.
#  * Anything unmeasurable is refused, not guessed. A wrong coordinate here
#    does not produce a wrong answer, it produces a click somewhere in the game
#    world -- which moves items, walks the character, or cancels a listing.
#  * The reference machine must be unaffected. With scale 1.0 and the reference
#    origin, every derived value equals the original constant exactly.

# Constants that live in the Trade window's frame, and how each maps.
#   box      (l, t, r, b) inside the Trade window
#   point    (x, y) inside the Trade window
#   x / y    a single coordinate inside the Trade window
#   xpair    (x1, x2) both inside the Trade window
#   len      a distance, scaled but not translated
#   lenpair  two distances
_TRADE_FRAME_GEOMETRY = {
    "TRADE_REGION": "box", "REGISTER_PANEL": "box", "PRICE_ROWS": "box",
    "PRICE_FIELD": "box", "QTY_FIELD": "box", "NET_SALES_ROWS": "box",
    "SHOP_SLOT_BOX": "box",
    "SHOP_SLOT": "point", "QTY_INPUT": "point", "PARK_POINT": "point",
    "PRICE_TOP_Y": "y", "PRICE_BOTTOM_Y": "y",
    "PANEL_RADIO_X": "x", "PRICE_TEXT_MAX_X": "x",
    "NAME_COLUMN": "xpair",
    "DIALOG_BUTTON_MIN_X": "x",
    "PRICE_ROW_Y_TOL": "len",
    # These follow the game window rather than the Trade window, but they are
    # mapped through the same frame on purpose. Deriving them from the client
    # rect instead produced different regions on the REFERENCE machine -- e.g.
    # POPUP_REGION became (10, 30, 2135, 1185) against the measured
    # (500, 350, 2100, 1150) -- which silently changes behaviour on the one
    # setup known to work. Scaling the measured value is approximate elsewhere
    # and exact here; that is the right way round.
    "POPUP_REGION": "box",
    "NPC_SEARCH_REGION": "box",
    "TRADE_WINDOW_SEARCH": "box",
    "NPC_EXCLUDE_ZONES": "boxes",
    # Anchored on the Inventory panel, not the Trade window, but mapped the
    # same way for the same reason. Deriving ALZ_REGION from the client rect
    # replaced a tight 195x56 band with a whole screen quadrant -- and
    # find_alz works by taking the bounding box of every bright, saturated
    # pixel, so it then returned the entire quadrant instead of the digits.
    # Measured consequence at 1920x1080: the inventory anchor moved 139px, the
    # tab row was missed entirely, and slot (8,8) landed at x=1931 on a
    # 1920-wide screen -- a Ctrl+Click into the game world, which is precisely
    # what this layer exists to prevent.
}

# The reference machine's game client, and the regions that belong to the
# Inventory panel rather than the Trade window. Cabal docks the Inventory to
# the RIGHT edge of the client, so these must follow the client, not the Trade
# window: if the game keeps its UI at a fixed pixel size on a smaller screen
# (rather than scaling it), a Trade-window-anchored ALZ_REGION lands off the
# right edge, find_alz returns None, and slot_centre() raises -- so nothing can
# be registered at all. Anchoring to the client is identical on the reference
# machine and correct either way.
REF_CLIENT = (0, 23, 2560, 1392)
_CLIENT_FRAME_GEOMETRY = ("ALZ_REGION", "INVENTORY_TITLE_REGION")

# Constants measured in pixels but anchored on the Inventory panel, which is
# found separately (by the colour of the Alz figure). These only need scaling.
_INVENTORY_FRAME_GEOMETRY = {
    "SLOT_PITCH": "lenpair", "SLOT_ONE_OFFSET": "lenpair",
    "TAB_ONE_OFFSET": "lenpair", "ALZ_TO_TITLE": "lenpair",
    "TAB_PITCH": "len", "SLOT_INSET": "len",
}

# Captured at import, before anything can rewrite them.
_REFERENCE_GEOMETRY: dict[str, object] = {}


def _capture_reference_geometry() -> None:
    """Snapshot the constants, converted into the Trade window's own frame.

    The constants are written as ABSOLUTE screen coordinates on the reference
    display -- TRADE_REGION is literally (10, 30, 1235, 1065). Layout.box()
    translates by the live origin, so feeding it an absolute value adds the
    origin a second time: at the reference layout every region came out
    shifted by exactly (+10, +30), which is a silent, uniform, plausible-
    looking error. Subtracting the reference origin here is what makes
    apply_layout() an identity at scale 1.0.

    Distances (`len`, `lenpair`) are not translated -- only scaled -- so they
    are captured as they are.
    """
    ox, oy = REF_TRADE_ORIGIN
    for name, kind in _TRADE_FRAME_GEOMETRY.items():
        value = globals()[name]
        if kind == "box":
            value = (value[0] - ox, value[1] - oy, value[2] - ox, value[3] - oy)
        elif kind == "point":
            value = (value[0] - ox, value[1] - oy)
        elif kind == "x":
            value = value - ox
        elif kind == "y":
            value = value - oy
        elif kind == "xpair":
            value = (value[0] - ox, value[1] - ox)
        elif kind == "boxes":
            value = tuple((b[0] - ox, b[1] - oy, b[2] - ox, b[3] - oy)
                          for b in value)
        # "len" is a distance: no translation.
        _REFERENCE_GEOMETRY[name] = value

    for name in _INVENTORY_FRAME_GEOMETRY:
        _REFERENCE_GEOMETRY[name] = globals()[name]

    # Client-anchored regions are stored as insets from the client's RIGHT and
    # TOP edges, so they follow the Inventory panel wherever the client is.
    cl, ct, cr, _cb = REF_CLIENT
    for name in _CLIENT_FRAME_GEOMETRY:
        left, top, right, bottom = globals()[name]
        _REFERENCE_GEOMETRY[name] = (cr - left, top - ct, cr - right, bottom - ct)


def apply_layout(layout: "Layout") -> None:
    """Rewrite every geometry constant into `layout`'s frame.

    Called once, before anything reads a coordinate. Rewriting globals is
    deliberate: these names are read from ~30 places, and threading a layout
    object through all of them would be a far larger change with far more
    opportunity to miss one -- and a missed one clicks the wrong pixel.
    """
    # A real measurement now exists, so _ocr_reference_scale stops
    # guessing from the client rect and uses LAYOUT.scale instead.
    global _CALIBRATED
    _CALIBRATED = True

    global LAYOUT
    LAYOUT = layout
    if not _REFERENCE_GEOMETRY:
        _capture_reference_geometry()

    for name, kind in _TRADE_FRAME_GEOMETRY.items():
        ref = _REFERENCE_GEOMETRY[name]
        if kind == "box":
            value = layout.box(ref)
        elif kind == "point":
            value = layout.point(ref)
        elif kind == "x":
            value = layout.x(ref)
        elif kind == "y":
            value = layout.y(ref)
        elif kind == "xpair":
            value = (layout.x(ref[0]), layout.x(ref[1]))
        elif kind == "boxes":
            value = tuple(_clamp_box(layout.box(b), layout.screen) for b in ref)
        else:  # len
            value = layout.length(ref)
        if kind == "box":
            value = _clamp_box(value, layout.screen)
        globals()[name] = value

    for name, kind in _INVENTORY_FRAME_GEOMETRY.items():
        ref = _REFERENCE_GEOMETRY[name]
        if kind == "lenpair":
            # Keep sub-pixel precision: SLOT_PITCH is fractional and is
            # multiplied by up to 7, so rounding it early walks the grid off
            # by several pixels at the far corner.
            value = (ref[0] * layout.scale, ref[1] * layout.scale)
            if all(float(v).is_integer() for v in ref):
                value = (int(round(value[0])), int(round(value[1])))
        else:  # len
            value = ref * layout.scale
            if float(ref).is_integer():
                value = int(round(value))
        globals()[name] = value

    # Inventory regions, as insets from the client's right and top edges.
    client = layout.client or (0, 0, *layout.screen)
    cl, ct, cr, cb = client
    for name in _CLIENT_FRAME_GEOMETRY:
        left_in, top_in, right_in, bottom_in = _REFERENCE_GEOMETRY[name]
        globals()[name] = _clamp_box(
            (cr - layout.length(left_in), ct + layout.length(top_in),
             cr - layout.length(right_in), ct + layout.length(bottom_in)),
            layout.screen)

    # NPC_BODY_OFFSET and the sweep grid are distances below the nameplate.
    globals()["NPC_BODY_OFFSET"] = (
        int(round(_REFERENCE_NPC_BODY_OFFSET[0] * layout.scale)),
        int(round(_REFERENCE_NPC_BODY_OFFSET[1] * layout.scale)))
    globals()["NPC_CLICK_OFFSETS"] = _npc_click_offsets()


_REFERENCE_NPC_BODY_OFFSET = NPC_BODY_OFFSET

def client_rect() -> tuple[int, int, int, int] | None:
    """The game's client area in screen pixels, or None if not found.

    The client area, not the window rect: the title bar and borders are not
    part of the rendered UI, and including them shifts every derived region.
    """
    hwnd = find_game_window()
    if not hwnd:
        return None
    try:
        rect = ctypes.wintypes.RECT()
        if not ctypes.windll.user32.GetClientRect(ctypes.c_void_p(hwnd),
                                                  ctypes.byref(rect)):
            return None
        point = ctypes.wintypes.POINT(rect.left, rect.top)
        if not ctypes.windll.user32.ClientToScreen(ctypes.c_void_p(hwnd),
                                                   ctypes.byref(point)):
            return None
        return (point.x, point.y,
                point.x + (rect.right - rect.left),
                point.y + (rect.bottom - rect.top))
    except Exception:  # noqa: BLE001 - calibration must not crash the run
        return None


def _anchor_centre(phrase: str, words: list, lines: list):
    """Where an anchor phrase sits in one OCR pass, or None.

    Multi-word anchors are matched across a joined line and then narrowed to
    just the words that spell them -- measuring across the whole line puts the
    centre wherever the neighbouring text happens to end.
    """
    if " " in phrase:
        target = _normalise(phrase)
        for line in lines:
            if target in _normalise("".join(w.text for w in line)):
                window = _minimal_window(line, (target,))
                if window:
                    return _span_centre(window)
        return None
    needle = phrase.casefold()
    hits = sorted((w for w in words if needle in w.text.casefold()),
                  key=lambda w: w.top)

    # AMBIGUOUS MEANS UNKNOWN. If the word appears more than once on screen,
    # discard the anchor rather than pick one -- a missing anchor costs one
    # unit of drop budget, a wrong one poisons the shared fit and consumes a
    # drop to undo.
    #
    # The old rule took the topmost match, which is not a tie-break at all but
    # an unconditional pick that happens to work because the chat log is drawn
    # low. Measured over 3,019 frames with the Trade window open: 'Trade' has a
    # second match on 33 frames, 'Selling' on 52, 'Name' on 13 -- all chat
    # adverts ('SELLING Upgrade Core 85k', trade-channel tags, a player called
    # NoNameNeeded). Discarding every ambiguous match still calibrates
    # 3,019/3,019 and never leaves fewer than four anchors, so the safety costs
    # nothing measurable.
    #
    # It also closes a gap "topmost wins" cannot: chat renders OVER the Trade
    # window and grows upward. The highest chat line yet recorded sits at
    # y=1031 while 'Selling' is at y=1012 and 'Refresh' at y=1010 -- a 19px
    # margin, narrower than the 26px line pitch. One busier evening and the
    # advert IS the topmost match.
    if len(hits) > 1:
        return None
    if hits:
        return hits[0].centre
    # Nothing matched the whole word. At smaller UI scales this font clips its
    # leading glyph: measured at 1920x1080, the Trade window's title reads
    # 'rade' at 96% confidence on upscale 2, 3 AND 4 -- so a substring test can
    # never find it, at any upscale, and the only remaining word on screen
    # containing 'trade' is a chat line. That is how a decoy 858px away came to
    # be treated as the window title.
    #
    # So: accept a word that is the anchor missing its first or last character,
    # provided it is long enough that the match is still meaningful. Four
    # characters minimum -- 'rade' qualifies, a stray 'na' would not.
    if len(needle) >= 5:
        # A clipped word's CENTRE IS NOT THE FULL WORD'S CENTRE. 'rade' is
        # missing its leading glyph, so its box starts one character later and
        # its centre sits about half a glyph to the right. Taking it raw put
        # 'Trade' 9px off and shifted the whole calibrated origin by 2px --
        # the same drift this file already fixed once. Compensate by the
        # measured average glyph width of the text actually read.
        #
        # Confidence bar is higher than the 40.0 used for whole-word matches:
        # a partial word is weaker evidence, and the case that caused the
        # drift scored 40.1, i.e. it cleared the normal bar by a tenth.
        for w in sorted(words, key=lambda x: x.top):
            text = w.text.casefold().strip()
            if w.conf < NEAR_ANCHOR_MIN_CONF or not text:
                continue
            lost_first = text == needle[1:]
            lost_last = text == needle[:-1]
            if not (lost_first or lost_last):
                continue
            glyph = (w.right - w.left) / max(len(text), 1)
            cx, cy = w.centre
            # Losing the FIRST glyph shifts the observed centre right, so
            # correct left, and vice versa.
            return (int(round(cx - glyph / 2 if lost_first else cx + glyph / 2)),
                    cy)
    return None


def measure_layout(image: Image.Image | None = None,
                   verbose: bool = True) -> "Layout | None":
    """Measure the live Trade window and return a Layout, or None.

    Requires the Trade window to be open and visible. Returns None -- never a
    guess -- when the anchors cannot be found or disagree.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    shot = image if image is not None else grab()
    screen = shot.size
    client = client_rect()
    search = (0, 0, screen[0], screen[1])

    # Locate every anchor word we can. Each gives one (measured, reference)
    # pair; two of them give an offset and a scale.
    # ONE OCR pass for all the anchors, not one pass each.
    #
    # find_text/find_phrase each run find_words over the whole screen, so six
    # anchors meant six full-screen Tesseract invocations -- about 13s, paid on
    # every cycle now that the layout is measured live. The words are identical
    # whichever way they are collected; only the number of subprocess spawns
    # differs.
    # The upscale find_words picks is derived from LAYOUT.scale -- which, on
    # the FIRST calibration of a session, is still the built-in 1.0 whatever
    # the screen really is. On a smaller screen that means x2, and x2 is
    # exactly the setting find_words' own docstring says splits 'Refresh' into
    # 'R' + 'efresh' at 1920x1080. So the bootstrap loses anchors precisely
    # when it can least afford to, and cannot recover, because the upscale
    # only rises after a calibration succeeds.
    #
    # Retrying at a larger upscale costs one extra OCR pass and only happens
    # when anchors are missing. Anchors already found are kept: a second pass
    # cannot make a word that read cleanly read better.
    def collect(scale_override=None):
        got = find_words(shot, search, 40.0, scale=scale_override) \
            if scale_override else find_words(shot, search, 40.0)
        return got, _text_lines(got)

    words, lines = collect()
    attempts = [(words, lines)]
    hits = sum(1 for p, _ in REF_ANCHORS
               if _anchor_centre(p, words, lines) is not None)

    # Retry ONLY when there is not enough to fit safely -- never merely to
    # collect the full set.
    #
    # Retrying whenever fewer than six were found actively made calibration
    # worse: measured over 181 corpus frames, 24 of them found five anchors
    # that fit PERFECTLY (residual 0.0px, origin exactly (10,30)), and the
    # larger pass then contributed a sixth, 'Trade', 9px from where the other
    # five put it. That is inside the 11.7px bar, so it was accepted, and it
    # dragged the origin 2px. More anchors is not the goal; a consistent fit
    # is. 'Trade' is the least reliable of the six -- found on 237 of 286
    # frames, the lowest of any -- and was also the decoy that made a 1080p
    # screen uncalibratable.
    if hits < MIN_ANCHORS_AFTER_DROP:
        for bigger in (3, 4):
            words2, lines2 = collect(bigger)
            attempts.append((words2, lines2))
            if sum(1 for p, _ in REF_ANCHORS
                   if _anchor_centre(p, words2, lines2) is not None) >= len(REF_ANCHORS):
                break

    found: list[tuple[str, tuple[int, int], tuple[int, int]]] = []
    for phrase, ref_point in REF_ANCHORS:
        centre = None
        used = 0
        for n, (ws, ls) in enumerate(attempts):
            centre = _anchor_centre(phrase, ws, ls)
            if centre is not None:
                used = n
                break
        if centre is not None:
            found.append((phrase, centre, ref_point))
            say(f"  anchor {phrase!r:16} at {centre}  (reference {ref_point})"
                + ("" if used == 0 else "  [found on a larger OCR pass]"))
        else:
            say(f"  anchor {phrase!r:16} not found")

    # THREE, not two. A two-point fit has the same blind spot the old
    # pair-and-drift check had: with scale derived from |dm|/|dr|, any error
    # ALONG the baseline is absorbed entirely into the scale and the residual
    # is exactly 0. Measured, a 100px baseline-parallel displacement of one
    # anchor was accepted with "worst residual 0.0px" and produced 134px of
    # click error. The third anchor is what makes the residual mean anything.
    if len(found) < 3:
        # Say what was OBSERVED, then draw only the conclusions the evidence
        # supports. The old text named one cause ("not overlapped?") and
        # guessed it, which is useless when the real cause is one of the three
        # others -- and those need opposite fixes.
        say(f"\nCalibration could not measure the Trade window.")
        say(f"  display captured   {screen[0]}x{screen[1]}  (primary display only)")
        if client:
            say(f"  game client area   ({client[0]},{client[1]})-({client[2]},{client[3]})")
            # Provable, not guessed: screenshots only ever cover the primary
            # display, so a client rect outside it means the window was never
            # in frame. This is a real failure that previously read as
            # "the Trade window is not open".
            if (client[0] < 0 or client[1] < 0
                    or client[2] > screen[0] or client[3] > screen[1]):
                say("  >> The game window is NOT on the display being captured.")
                say("     Screenshots only ever cover the primary display, so the")
                say("     Trade window was never in the frame and nothing here can")
                say("     be measured. Move Cabal to the primary display.")
                return None
        say(f"  words read on screen {len(words)}")
        if not words:
            say("  >> OCR returned nothing at all, on the whole screen. That is")
            say("     Tesseract failing, not the game. Nothing about the window")
            say("     can be concluded from this frame -- check the messages")
            say("     above and that Tesseract is installed.")
            return None
        say(f"  anchors found      {len(found)} of {len(REF_ANCHORS)}")
        for phrase, centre, ref in found:
            say(f"    {phrase!r:12} at {centre}   reference {ref}")
        missing = [p for p, _ in REF_ANCHORS if p not in {f[0] for f in found}]
        say(f"  anchors missing    {', '.join(repr(p) for p in missing)}")
        # Where the missing ones live tells the user which EDGE is the problem.
        low = [p for p in missing
               if dict(REF_ANCHORS)[p][1] > REF_SCREEN[1] // 2]
        if low and len(low) == len(missing):
            say("  >> Every missing anchor sits along the window's lower half.")
            say("     The bottom of the Trade window is off-screen or covered.")
        say("\n  Three anchors are the minimum: with two, an error along the line")
        say("  between them is indistinguishable from a different scale, so the")
        say("  result cannot be checked.")
        say("\n  Re-running --calibrate will read the same screen and fail the")
        say("  same way. Change one of these first:")
        say("    - move the Cabal window fully onto the primary display;")
        say("    - close any overlay covering the Trade window;")
        say("    - drag the Trade window so all four corners are visible.")
        return None

    # Fit origin and scale over EVERY anchor found, then reject on the worst
    # residual.
    #
    # The previous version took the pair with the longest baseline and checked
    # that the two implied origins agreed. That check cannot do what it looks
    # like it does: with `s = |dm| / |dr|`, the vectors `dm` and `s*dr` have
    # equal length by construction, so their difference measures only the ANGLE
    # between them. It validates no scale error and no error along the
    # baseline -- and it threw away every anchor outside the winning pair, so a
    # third anchor flatly contradicting the result was never consulted.
    #
    # A least-squares fit uses all of them and makes disagreement visible:
    # on a clean frame the residuals are 0.0px, and a decoy word pasted on
    # screen produces ~47px. There is no comparable separation available from
    # two points.
    def fit(anchors):
        """Least-squares similarity fit. Returns (data, reason).

        `data` is (scale, ox, oy, residuals, span, allowed); on failure it is
        None and `reason` says why.
        """
        refs = [r for _, _, r in anchors]
        obs = [m for _, m, _ in anchors]
        span = max(math.hypot(a[0] - b[0], a[1] - b[1])
                   for a in refs for b in refs)
        if span < MIN_ANCHOR_BASELINE:
            return None, (f"the anchors found span only {span:.0f}px in the "
                          "reference frame - too close together to measure a "
                          "scale from.")

        # Spread on BOTH axes, not just the longest diagonal. Anchors strung
        # along one line fit perfectly and then extrapolate wildly off it: a
        # set covering 430px of x but only 100px of y passes a max-pairwise
        # span test and was measured producing 40px of click error at the far
        # end of the window - two thirds of a row pitch, i.e. the wrong
        # listing. The diagonal cannot see that; per-axis spread can.
        x_spread = max(r[0] for r in refs) - min(r[0] for r in refs)
        y_spread = max(r[1] for r in refs) - min(r[1] for r in refs)
        if min(x_spread, y_spread) < MIN_ANCHOR_SPREAD:
            return None, (f"the anchors cover {x_spread:.0f}px horizontally "
                          f"and {y_spread:.0f}px vertically; at least "
                          f"{MIN_ANCHOR_SPREAD}px on BOTH axes is needed, or "
                          "the fit is extrapolated off the line they sit on.")

        rx = sum(r[0] for r in refs) / len(refs)
        ry = sum(r[1] for r in refs) / len(refs)
        mx = sum(m[0] for m in obs) / len(obs)
        my = sum(m[1] for m in obs) / len(obs)
        numerator = sum((r[0] - rx) * (m[0] - mx) + (r[1] - ry) * (m[1] - my)
                        for r, m in zip(refs, obs))
        denominator = sum((r[0] - rx) ** 2 + (r[1] - ry) ** 2 for r in refs)
        if denominator <= 0:
            return None, "the anchors found are all in the same place."
        s = numerator / denominator
        if not SCALE_LIMITS[0] <= s <= SCALE_LIMITS[1]:
            return None, (f"measured scale {s:.3f} is outside the plausible "
                          f"range {SCALE_LIMITS} - the anchors were misread.")

        ox_, oy_ = mx - s * rx, my - s * ry
        res = [(name, math.hypot(m[0] - (ox_ + s * r[0]),
                                 m[1] - (oy_ + s * r[1])))
               for (name, m, r) in anchors]
        return (s, ox_, oy_, res, span, max(8.0, 0.01 * span)), ""

    # Drop misread anchors one at a time rather than refusing outright.
    #
    # A single decoy poisons the whole fit: the shared origin and scale shift
    # to split the difference, so every CORRECT anchor is reported as wrong
    # too. Observed on a real 1080p screen -- a stray 'Trade' at (923,909)
    # against its true (478,52) made the four good anchors, which agree with
    # each other to half a pixel, read as 173-244px off. Calibration was
    # impossible with a perfectly measurable window on screen.
    #
    # Dropping is only safe because what remains is re-checked from scratch:
    # the survivors must still span both axes and still agree within the bar.
    # A set that only fits because the disagreeing evidence was discarded
    # fails the spread test instead.
    while True:
        data, reason = fit(found)

        # A GATE failure can also be caused by one bad anchor, so try dropping
        # before giving up -- not only a residual failure.
        #
        # This ordering was the real defect. A single decoy drags the shared
        # scale far enough to fail SCALE_LIMITS, fit() returns None, and the
        # outlier-rejection loop below never runs. Measured on a 1920x1080
        # screen: decoy + three good anchors fits at scale 0.250, outside the
        # (0.4, 2.5) range, so calibration aborted with a message naming no
        # anchor -- while the three good ones alone fit at 0.7607 with a 0.0px
        # residual. The code written to rescue exactly that case was
        # unreachable.
        #
        # Leave-one-out rather than "drop the worst residual", because a failed
        # fit has no residuals to rank. Each candidate is dropped in turn and
        # the survivors re-fitted; a drop is only accepted if what remains
        # passes every gate on its own.
        if data is None and len(found) - 1 >= MIN_ANCHORS_AFTER_DROP:
            best = None
            for candidate in found:
                trial = [e for e in found if e[0] != candidate[0]]
                t_data, _ = fit(trial)
                if t_data is None:
                    continue
                t_worst = max(t_data[3], key=lambda pair: pair[1])[1]
                if t_worst <= t_data[5] and (best is None or t_worst < best[0]):
                    best = (t_worst, candidate[0], trial)
            if best is not None:
                say(f"  {best[1]!r} is inconsistent with the others ({reason}); "
                    f"without it the remaining {len(best[2])} agree to "
                    f"{best[0]:.1f}px. Dropping it and continuing.")
                found = best[2]
                continue

        if data is None:
            # The geometric reason alone leaves the user with nothing to do.
            # Which anchors are MISSING is what tells them what to change, and
            # it is already known here.
            say(f"\nCalibration refused: {reason}")
            missing = [p for p, _ in REF_ANCHORS if p not in {f[0] for f in found}]
            if missing:
                say(f"\n  found   {', '.join(repr(f[0]) for f in found)}")
                say(f"  missing {', '.join(repr(p) for p in missing)}")
                lower = [p for p in missing
                         if dict(REF_ANCHORS)[p][1] > REF_TRADE_SIZE[1] // 2]
                if lower:
                    say(f"  >> {', '.join(repr(p) for p in lower)} sit along the "
                        "Trade window's bottom edge. Missing them is why the "
                        "anchors are all bunched at the top, and it usually "
                        "means the window's lower part is off-screen, covered "
                        "by another window, or below the visible area.")
                say("\n  Fix: make the WHOLE Trade window visible -- all four "
                    "corners -- then re-run --calibrate. Re-running without "
                    "moving anything will read the same screen and refuse "
                    "again.")
            return None
        scale, ox, oy, residuals, span, allowed = data
        worst_name, worst = max(residuals, key=lambda pair: pair[1])
        if worst <= allowed:
            break
        if len(found) - 1 < MIN_ANCHORS_AFTER_DROP:
            # A scalar distance for a two-dimensional problem tells the user
            # nothing they can act on. Everything below is already computed by
            # fit() and was being thrown away: where the anchor was found,
            # where the others say it should be, and whether it lies outside
            # the window they describe -- which is what distinguishes "a
            # DIFFERENT 'Trade' somewhere on screen" from "the window moved".
            say("\nThe anchors disagree about where the Trade window is.")
            say(f"\n  fitted from {len(found)} anchors: origin ({ox:.0f},{oy:.0f}), "
                f"scale {scale:.3f}, span {span:.0f}px")
            say(f"  tolerance {allowed:.0f}px\n")
            by_ref = {name: r for name, _, r in found}
            for name, residual in sorted(residuals, key=lambda p: p[1]):
                where = next(m_ for n_, m_, _ in found if n_ == name)
                say(f"    {name!r:12} off by {residual:7.1f}px   found at {where}")
            # Predict from a fit that EXCLUDES the outlier. Using the fit it
            # corrupted would report a fabricated position with total
            # confidence: on the real 1080p failure, the 4-anchor fit including
            # the decoy gave scale 0.250 and origin (528,293), where the four
            # good anchors alone give scale 0.761 and origin (15,37).
            ref = by_ref[worst_name]
            rest = [e for e in found if e[0] != worst_name]
            clean, _ = fit(rest) if len(rest) >= 3 else (None, "")
            if clean:
                c_scale, c_ox, c_oy = clean[0], clean[1], clean[2]
                say(f"\n  Without {worst_name!r}, the rest agree on origin "
                    f"({c_ox:.0f},{c_oy:.0f}) scale {c_scale:.3f}.")
            else:
                c_scale, c_ox, c_oy = scale, ox, oy
            predicted = (int(c_ox + c_scale * ref[0]),
                         int(c_oy + c_scale * ref[1]))
            say(f"  {worst_name!r} was found at "
                f"{next(m_ for n_, m_, _ in found if n_ == worst_name)}, but they "
                f"put it at {predicted}.")
            win = (int(c_ox), int(c_oy),
                   int(c_ox + c_scale * REF_TRADE_SIZE[0]),
                   int(c_oy + c_scale * REF_TRADE_SIZE[1]))
            actual = next(m_ for n_, m_, _ in found if n_ == worst_name)
            outside = not (win[0] <= actual[0] <= win[2]
                           and win[1] <= actual[1] <= win[3])
            if outside:
                say(f"  That is OUTSIDE the window the others describe "
                    f"({win[0]},{win[1]})-({win[2]},{win[3]}), so it is a "
                    f"different {worst_name!r} on screen -- a chat line, a quest "
                    "panel, another window -- not part of the Trade window.")
                say(f"\n  Fix: close or move whatever shows {worst_name!r} at "
                    f"{actual}, or drag the Trade window clear of it, THEN "
                    "re-run --calibrate.")
            else:
                say("  That is inside the window the others describe, so the "
                    "reading is inconsistent rather than a decoy -- the window "
                    "may have moved while it was being measured. Retry with "
                    "the window held still.")
            say(f"\n  Dropping it would leave {len(found) - 1} anchors, below the "
                f"{MIN_ANCHORS_AFTER_DROP} that must survive for the remaining "
                "fit to be checkable, so this refuses rather than calibrating "
                "from a misread.")
            return None
        say(f"  {worst_name!r} is {worst:.0f}px from where the other "
            f"{len(found) - 1} put it (bar {allowed:.0f}px) - treating it as a "
            "misread and refitting without it.")
        found = [entry for entry in found if entry[0] != worst_name]

    dropped = len(REF_ANCHORS) - len(found)
    return Layout(screen=screen, origin=(int(round(ox)), int(round(oy))),
                  scale=scale, client=client,
                  measured_from=f"{len(found)} anchors fitted, worst residual "
                                f"{worst:.1f}px over a {span:.0f}px span"
                                + (f" ({dropped} not used)" if dropped else ""))


def validate_layout(layout: "Layout", verbose: bool = True) -> bool:
    """Reject a layout whose numbers cannot be right."""
    def say(message: str) -> None:
        if verbose:
            print(message)

    left, top, right, bottom = layout.trade
    if left < 0 or top < 0 or right > layout.screen[0] or bottom > layout.screen[1]:
        say(f"  the Trade window would sit at {layout.trade}, partly off a "
            f"{layout.screen[0]}x{layout.screen[1]} screen.")
        return False
    if right <= left or bottom <= top:
        say("  the derived Trade window has no area.")
        return False
    # The dialog-button boundary must land inside the screen, and to the right
    # of the table's Function column -- excluding that column is its whole job.
    boundary = layout.x(REF_DIALOG_BUTTON_MIN_X - REF_TRADE_ORIGIN[0])
    if not layout.x(REF_FUNCTION_COLUMN_X) < boundary < layout.screen[0]:
        say(f"  the dialog-button boundary ({boundary}) does not sit between "
            f"the Function column and the right edge of the screen.")
        return False
    # Check the mapped regions have area. These come from the geometry table
    # now, not from Layout properties -- validate_layout still called a
    # `layout.npc_search` property after it was removed, which is an
    # AttributeError on the only path every clicking command goes through.
    for name in ("NPC_SEARCH_REGION", "POPUP_REGION", "TRADE_WINDOW_SEARCH"):
        ref = _REFERENCE_GEOMETRY.get(name)
        if ref is None:
            continue
        box = _clamp_box(layout.box(ref), layout.screen)
        if box[2] <= box[0] or box[3] <= box[1]:
            say(f"  the derived {name} has no area.")
            return False
    return True


def save_calibration(layout: "Layout", verbose: bool = True) -> bool:
    try:
        CALIBRATION_FILE.write_text(json.dumps({
            "screen": list(layout.screen),
            "origin": list(layout.origin),
            "scale": layout.scale,
            "client": list(layout.client) if layout.client else None,
            "measured_from": layout.measured_from,
        }, indent=2), encoding="utf-8")
        if verbose:
            print(f"Saved calibration to {CALIBRATION_FILE}")
        return True
    except OSError as exc:
        if verbose:
            print(f"Could not save the calibration ({exc}); it will have to be "
                  "measured again next run.", file=sys.stderr)
        return False


def load_calibration(verbose: bool = True) -> "Layout | None":
    """DEPRECATED and deliberately uncalled. See ensure_calibrated.

    Kept only so a saved calibration can be inspected by hand. Nothing in the
    run reads it: the layout is measured fresh every time, because a stored one
    cannot detect the Trade window having been dragged within an unchanged
    client, and a stale layout clicks confidently in the wrong place.
    """
    """The stored calibration, if it still matches this machine."""
    try:
        data = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
        layout = Layout(screen=tuple(data["screen"]), origin=tuple(data["origin"]),
                        scale=float(data["scale"]),
                        client=tuple(data["client"]) if data.get("client") else None,
                        measured_from=data.get("measured_from", "stored"))
    except (OSError, ValueError, KeyError, TypeError):
        return None

    # A stored calibration is only valid for the screen it was taken on, and
    # only while the game window has not moved or been resized. Using a stale
    # one is worse than having none: it clicks confidently in the wrong place.
    current = current_screen_size()
    if current and tuple(layout.screen) != current:
        if verbose:
            print(f"Stored calibration was taken at {layout.screen[0]}x"
                  f"{layout.screen[1]} but this screen is {current[0]}x"
                  f"{current[1]} - re-measuring.")
        return None
    now = client_rect()
    if layout.client and now and tuple(layout.client) != tuple(now):
        if verbose:
            print("The game window has moved or been resized since the last "
                  "calibration - re-measuring.")
        return None
    return layout


def current_screen_size() -> tuple[int, int] | None:
    """Physical pixels of the primary display, or None if it cannot be read."""
    try:
        make_dpi_aware()
        with mss.mss() as sct:
            region, _ = resolve_monitor(sct, "primary")
            return (int(region["width"]), int(region["height"]))
    except Exception:  # noqa: BLE001 - never let a probe stop the run
        return None


def calibrate(verbose: bool = True, save: bool = True) -> bool:
    """Measure this machine's layout and apply it. False if it could not be."""
    def say(message: str) -> None:
        if verbose:
            print(message)

    say("Calibrating against the current screen and game window...")
    screen = current_screen_size()
    if screen:
        say(f"  screen {screen[0]}x{screen[1]}")
    rect = client_rect()
    say(f"  game client area: {rect if rect else 'not found'}")

    if not trade_window_open():
        say("  the Trade window is not open. Calibration measures it, so open "
            "the Agent Shop first (or run --open) and try again.")
        return False

    layout = measure_layout(verbose=verbose)
    if layout is None:
        return False
    if not validate_layout(layout, verbose=verbose):
        return False

    apply_layout(layout)
    say(f"Calibrated: {layout.describe()}")
    say(f"  Trade window   {LAYOUT.trade}")
    say(f"  Register panel {REGISTER_PANEL}")
    say(f"  Dialog buttons right of x={DIALOG_BUTTON_MIN_X}")
    say(f"  NPC search     {NPC_SEARCH_REGION}")
    if save:
        save_calibration(layout, verbose=verbose)
    return True


def ensure_calibrated(verbose: bool = True, required: bool = True) -> bool:
    """Measure the layout fresh before anything is clicked.

    A stored calibration is deliberately NEVER reused. It cannot be trusted:
    the Trade window is draggable within an unchanged client, so the staleness
    test (screen size + client rect) misses the one number the layout is
    actually about -- its origin. A stale layout does not fail loudly, it
    clicks confidently in the wrong place, which is the failure this whole
    layer exists to prevent.

    Measuring costs a few seconds of OCR per run against that risk, so it is
    measured every time. `save_calibration` still records what was measured,
    for diagnosis; nothing reads it back.

    `required=False` is for read-only commands, which are allowed to run on the
    reference defaults and simply be wrong about where things are.
    """
    if calibrate(verbose=verbose):
        return True

    # The monitor matching is NOT enough: the built-in coordinates assume the
    # reference CLIENT too, and the game can be windowed. On a 1920x1080 window
    # inside a 2560x1440 desktop -- the setup this was actually run on -- the
    # monitor matches while every coordinate is wrong, and the failure is
    # silent: the table read adapts and looks healthy, but dialog_button's
    # distance filter then returns a TABLE ROW's Receive button, so --confirm
    # collects a sale on a row nobody asked about.
    screen = current_screen_size()
    client = client_rect()
    if (screen and tuple(screen) == REF_SCREEN
            and client and tuple(client) == REF_CLIENT):
        if verbose:
            print("Could not calibrate, but this screen and game window match "
                  "the reference the coordinates were measured on, so the "
                  "built-in values are used.")
        return True
    # DO NOT diagnose here. calibrate() has just run and printed exactly why it
    # failed; anything invented at this point is a guess layered over evidence.
    # The old text asserted a resolution mismatch as the cause, which produced
    # the literal "This screen is 2560x1440 but the built-in coordinates were
    # measured at 2560x1440" whenever the CLIENT rect was the thing that
    # differed -- self-contradictory, and it overwrote the accurate message
    # printed immediately above it.
    if not required:
        return False
    if not verbose:
        return False

    print("\nRefusing to click without a calibration.")
    if not screen:
        print("The screen size could not be determined.")
        return False

    if tuple(screen) == REF_SCREEN and client and tuple(client) != REF_CLIENT:
        # Specific, true, and different from "your resolution is wrong".
        print(f"The display matches the reference ({screen[0]}x{screen[1]}), but "
              f"the game window is {client[2] - client[0]}x{client[3] - client[1]} "
              f"at ({client[0]},{client[1]}) where the built-in coordinates "
              f"assume {REF_CLIENT[2] - REF_CLIENT[0]}x"
              f"{REF_CLIENT[3] - REF_CLIENT[1]} at "
              f"({REF_CLIENT[0]},{REF_CLIENT[1]}), so they do not apply.")
    elif tuple(screen) != REF_SCREEN:
        print(f"This screen is {screen[0]}x{screen[1]} and the built-in "
              f"coordinates were measured at {REF_SCREEN[0]}x{REF_SCREEN[1]}, "
              "so they point at the wrong pixels here.")
    print("A click that misses the UI lands in the game world, which moves "
          "your character -- so nothing will be clicked.")
    print("\nCalibration was attempted just now and failed for the reason "
          "printed above. Running --calibrate again WITHOUT CHANGING ANYTHING "
          "will read the same screen and fail identically.")
    return False


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Run logging
# --------------------------------------------------------------------------
#
# Every run writes its own file. This exists because a five-hour unattended run
# died in the small hours and left NOTHING to diagnose from: the frame index
# stopped mid-cycle, no abort was recorded, and the console output was gone
# with the terminal. The screenshots showed a completely healthy screen one
# second before the end, so the failure was in code -- and nothing had captured
# it.
#
# Three properties matter more than tidiness here:
#
#   * one file per run, named by start time, so a crash is never overwritten by
#     the restart that follows it;
#   * flushed on EVERY line, because the interesting content is whatever was
#     written immediately before the process stopped, and a buffer is exactly
#     what is lost when it stops;
#   * incapable of breaking the run. Logging that can raise would become a new
#     failure mode in a script whose whole problem is unexplained failures.

LOG_DIR = SCRIPT_DIR / "logs"
_log_handle = None
# Set at import, not in start_run_log, so the duration covers the whole process
# even on paths that never reach the logging setup -- an argument error, or an
# exception during import of something below this point.
_RUN_STARTED = time.monotonic()
_RUN_STARTED_AT = datetime.now()
_run_finished = False


class _Tee:
    """Write to the console and the run log at once.

    Wraps a stream rather than replacing it, so anything already holding a
    reference to sys.stdout keeps working. Every write is flushed: the lines
    that matter are the last ones before a crash.
    """

    def __init__(self, stream, handle):
        self._stream = stream
        self._handle = handle

    def write(self, text):
        self._stream.write(text)
        try:
            self._stream.flush()
        except Exception:      # noqa: BLE001 - a console that will not flush
            pass               # must not stop the log being written
        try:
            self._handle.write(text)
            self._handle.flush()
        except Exception:      # noqa: BLE001 - logging never breaks the run
            pass
        return len(text)

    def flush(self):
        for target in (self._stream, self._handle):
            try:
                target.flush()
            except Exception:  # noqa: BLE001
                pass

    def isatty(self):
        try:
            return self._stream.isatty()
        except Exception:      # noqa: BLE001
            return False

    def __getattr__(self, name):
        return getattr(self._stream, name)


def start_run_log(argv: list[str]) -> "Path | None":
    """Begin a per-run log. Returns its path, or None if it could not start.

    Installs an excepthook as well as the tee: an uncaught exception is exactly
    the case this is for, and by default Python prints the traceback and exits
    without anything else seeing it.
    """
    global _log_handle
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        started = datetime.now()
        path = LOG_DIR / f"run_{started:%Y-%m-%d_%H%M%S}.log"
        # Never clobber: two runs started inside the same second would
        # otherwise share a file, and one of them is probably the crash.
        n = 1
        while path.exists():
            n += 1
            path = LOG_DIR / f"run_{started:%Y-%m-%d_%H%M%S}_{n}.log"
        _log_handle = path.open("w", encoding="utf-8", buffering=1)
        _log_handle.write(
            f"=== trade.py run log ===\n"
            f"started   {started.isoformat(timespec='seconds')}\n"
            f"command   {' '.join(argv)}\n"
            f"script    {Path(__file__).resolve()}\n"
            f"python    {sys.version.split()[0]}\n"
            f"cwd       {Path.cwd()}\n"
            f"{'=' * 60}\n")
        _log_handle.flush()
        sys.stdout = _Tee(sys.stdout, _log_handle)
        sys.stderr = _Tee(sys.stderr, _log_handle)

        def log_uncaught(kind, value, tb):
            # The whole point: a crash must leave a traceback behind. Written
            # through the raw handle as well as stderr, in case the tee is the
            # thing that broke.
            try:
                text = "".join(traceback.format_exception(kind, value, tb))
                _log_handle.write(f"\n=== UNCAUGHT {kind.__name__} ===\n{text}")
                _log_handle.write(f"ended     {datetime.now().isoformat(timespec='seconds')}\n")
                _log_handle.flush()
            except Exception:  # noqa: BLE001
                pass
            sys.__excepthook__(kind, value, tb)

        sys.excepthook = log_uncaught
        return path
    except Exception:          # noqa: BLE001 - a run without a log is still a run
        return None


def _format_duration(seconds: float) -> str:
    """A duration a human reads at a glance: '2h 14m 07s', '3m 21s', '4.2s'."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def finish_run_log(note: str = "") -> None:
    """Report how long the run lasted, to the console AND the log.

    Runs on EVERY termination -- a clean finish, sys.exit, Ctrl+C, an uncaught
    exception -- because it is registered with atexit as well as being called
    from the __main__ guard. `_run_finished` makes it idempotent so the two
    routes cannot both print.

    The duration is printed to the console even when there is no log file: the
    question "how long did it actually run before it died?" is the first one
    asked after any unattended failure, and until now the only way to answer it
    was to subtract timestamps out of the frame index.

    Deliberately never raises. This is the last thing a dying process does, and
    an exception here would replace the real cause of death with its own.
    """
    global _run_finished
    if _run_finished:
        return
    _run_finished = True
    try:
        ended = datetime.now()
        elapsed = time.monotonic() - _RUN_STARTED
        line = (f"Ran for {_format_duration(elapsed)}  "
                f"({_RUN_STARTED_AT:%H:%M:%S} -> {ended:%H:%M:%S})"
                + (f"  [{note}]" if note else ""))
        # print() already tees into the log, so writing `line` to the handle as
        # well duplicated it in every file. Only the closing marker -- which is
        # not printed to the console -- goes direct.
        print(f"\n{line}")
        if _log_handle is not None:
            _log_handle.write(f"{'=' * 60}\n"
                              f"ended     {ended.isoformat(timespec='seconds')}\n")
            _log_handle.flush()
    except Exception:          # noqa: BLE001
        pass


def main() -> None:
    p = argparse.ArgumentParser(
        description="Cabal Online automation: capture, Alz and the Agent Shop.")
    p.add_argument("--shot", action="store_true",
                   help="capture the screen to the screenshots folder")
    p.add_argument("--alz", action="store_true",
                   help="read the Alz balance from the screen")
    p.add_argument("--monitor", "-m", default="primary",
                   help="with --shot: 'primary', 'all', or a 1-based monitor number")
    p.add_argument("--monitors", action="store_true",
                   help="list the detected monitors and exit")
    p.add_argument("--outdir", type=Path, default=None,
                   help="with --shot: folder to save into")
    p.add_argument("--keep", type=int, default=1, metavar="N",
                   help="with --shot: how many recent captures to retain")
    p.add_argument("--no-prune", action="store_true",
                   help="with --shot: keep every capture instead of pruning")
    p.add_argument("--no-alz", action="store_true",
                   help="with --shot: skip reading the Alz balance")
    p.add_argument("--no-record", action="store_true",
                   help="do not save frames to unit_tests/corpus during the run")
    p.add_argument("--record", action="store_true",
                   help="save frames even for read-only commands")
    p.add_argument("--calibrate", action="store_true",
                   help="measure this machine's screen and game window, and "
                        "remember the layout (open the Agent Shop first)")
    p.add_argument("--no-calibrate", action="store_true",
                   help="skip the layout check and use the built-in 2560x1440 "
                        "coordinates as-is")
    p.add_argument("--cancel", type=int, metavar="ROW",
                   help="cancel the listing on this 1-based table row")
    p.add_argument("--list", action="store_true",
                   help="show the listings currently visible")
    p.add_argument("--relist-rows", nargs="+", metavar="SPEC",
                   help="relist several rows, e.g. --relist-rows 1-10 or 1 3 5; "
                        "each is tracked by name, since rows renumber as you go")
    p.add_argument("--relist", type=int, nargs="+", metavar="N",
                   help="cancel a row then re-list it: --relist ROW [INV_ROW INV_COL]; "
                        "without a slot it follows the item to wherever it lands")
    p.add_argument("--do", nargs="+", metavar="ACTION",
                   help="run actions back to back, e.g. --do \"cancel 3\" \"register 1 1\"")
    p.add_argument("--repeat", nargs="+", metavar="ACTION",
                   help="repeat actions on a timer; needs --for and --every, "
                        "e.g. --repeat \"relist 1\" --for 60 --every 5")
    p.add_argument("--for", dest="duration", type=float, metavar="MINUTES",
                   help="with --repeat, how long to keep looping")
    p.add_argument("--every", type=float, metavar="MINUTES",
                   help="with --repeat, how often to run the actions; "
                        "0 starts each cycle as soon as the last one finishes")
    p.add_argument("--register", type=int, nargs=2, metavar=("ROW", "COL"),
                   help="list the item in this inventory slot on the Agent Shop")
    p.add_argument("--load", type=int, nargs=2, metavar=("ROW", "COL"),
                   help="Ctrl+Click an inventory slot into the shop slot, then stop")
    p.add_argument("--panel", action="store_true",
                   help="read the Register panel's price and quantity")
    p.add_argument("--clear", action="store_true",
                   help="close any dialog and return the shop slot item to the inventory")
    p.add_argument("--confirm", action="store_true",
                   help="click Confirmation through any dialogs left open by a stopped run")
    p.add_argument("--open", action="store_true",
                   help="reopen the Trade window via the Agent Shop NPC, on the Register tab")
    p.add_argument("--reset", action="store_true",
                   help="Escape out, reopen the shop and clear the slot - the "
                        "recovery a failed cycle performs")
    p.add_argument("--words", action="store_true",
                   help="dump every word OCR sees in the trade window")
    p.add_argument("--listings", action="store_true",
                   help="scroll the whole shop and list every listing with " 
                        "its absolute position (reads and scrolls only)")
    p.add_argument("--scroll", type=int, metavar="NOTCHES",
                   help="probe: turn the wheel over the listings table by N "
                        "notches (negative scrolls down) and report how far "
                        "the rows actually moved. Nothing else uses scrolling "
                        "yet")
    p.add_argument("--max-qty", action="store_true",
                   help="with --register, set the quantity to the maximum available")
    # default=None, not 0: the numeric validation below rejects non-positive
    # values, and a default of 0 made it fire on EVERY invocation -- so every
    # command exited 2 with "--floor must be a positive number (got 0)".
    # Every consumer already short-circuits on a falsy floor.
    p.add_argument("--floor", type=int, default=None, metavar="ALZ",
                   help="with --register, abort if the suggested price is below this")
    p.add_argument("--price", type=int, default=None, metavar="ALZ",
                   help="with --register, list at exactly this price")
    p.add_argument("--qty", type=int, default=None, metavar="N",
                   help="with --register, list exactly this quantity")
    p.add_argument("--dry-run", action="store_true",
                   help="locate everything but do not click")
    args = p.parse_args()

    # Read-only commands first: these need no elevation and no game state.
    if args.monitors:
        make_dpi_aware()
        with open_capture() as sct:
            list_monitors(sct)
        return

    if args.shot:
        make_dpi_aware()
        png, width, height, label, _ = take_screenshot(args.monitor)
        outdir = args.outdir or DEFAULT_OUTDIR
        outdir.mkdir(parents=True, exist_ok=True)
        path = unique_path(
            outdir / f"screenshot_{datetime.now():%Y-%m-%d_%H%M%S}_{label}.png")
        path.write_bytes(png)
        if not args.no_prune:
            removed = prune_screenshots(outdir, args.keep, path)
            if removed:
                print(f"Pruned {removed} older screenshot(s), keeping {args.keep}.")
        print(f"Saved {width}x{height} screenshot to: {path}")
        if not args.no_alz:
            alz = get_alz(path)
            print(f"Alz: {alz:,}  ({human(alz)})" if alz
                  else "Alz: 0 (Inventory panel not visible)")
        return

    if args.alz:
        alz = get_alz(grab())
        print(f"{alz:,} Alz  ({human(alz)})" if alz else "0 Alz (Inventory panel not visible)")
        return

    # Validate before the elevation check, so a bad argument reads as one.
    if args.repeat:
        if args.duration is None or args.every is None:
            p.error("--repeat needs both --for MINUTES and --every MINUTES")
        if args.duration <= 0:
            p.error("--for must be positive")
        if args.every < 0:
            p.error("--every cannot be negative; use --every 0 to start each "
                    "cycle as soon as the last one finishes")
        # 0 is the "no wait" case, not a too-short interval, so it skips the
        # comparison below -- which would otherwise never trigger anyway.
        if args.every and args.every > args.duration:
            p.error(f"--every {args.every:g} is longer than --for {args.duration:g}, "
                    "so the actions would run once at most")

    # Validate every numeric option here, before the elevation gate, so a bad
    # argument reads as a bad argument. These used to fail deep inside a run:
    # type_number(-5000) does int('-') and raises ValueError, which no handler
    # catches -- after the item was already sitting in the shop slot.
    for name, value in (("--price", args.price), ("--qty", args.qty),
                        ("--floor", args.floor), ("--cancel", args.cancel)):
        if value is not None and value <= 0:
            p.error(f"{name} must be a positive number (got {value})")
    for name, pair in (("--register", args.register), ("--load", args.load)):
        if pair and not all(1 <= v <= GRID_SIZE for v in pair):
            p.error(f"{name} takes ROW COL, each between 1 and {GRID_SIZE}")
    if args.relist is not None:
        if len(args.relist) not in (1, 3):
            p.error("--relist takes ROW, or ROW INV_ROW INV_COL")
        if args.relist[0] <= 0:
            p.error("--relist ROW must be positive")
        if len(args.relist) == 3 and not all(1 <= v <= GRID_SIZE
                                             for v in args.relist[1:]):
            p.error(f"--relist's INV_ROW and INV_COL must be 1..{GRID_SIZE}")
    if args.relist_rows is not None:
        try:
            parse_row_spec(args.relist_rows)
        except ValueError as exc:
            p.error(f"bad --relist-rows spec: {exc}")

    # These five have no dry-run mode -- they click unconditionally -- so they
    # must NOT be exempted by --dry-run. Exempting them dropped the elevation
    # check while the clicks still went through.
    # --calibrate belongs here: it focuses the game and parks the cursor, so it
    # injects input and must face the elevation check like every other command
    # that does. Without it the run died on an uncaught PermissionError instead
    # of printing the "run as Administrator" message.
    # `is not None` for --scroll: 0 is a legitimate no-op probe, and `or 0` is
    # falsy, which would let it through the gate ungated.
    always_clicks = (args.load is not None or args.clear or args.confirm
                     or args.open or args.reset or args.calibrate
                     or args.scroll is not None or args.listings)
    honours_dry_run = (args.cancel is not None or args.register is not None
                       or args.relist is not None
                       or args.relist_rows is not None
                       or args.do is not None
                       or args.repeat is not None)
    clicking = always_clicks or (honours_dry_run and not args.dry_run)
    if clicking and not is_elevated():
        sys.exit(
            "Refusing to click: not running as Administrator.\n"
            "Cabal runs elevated, so once it holds the foreground Windows blocks "
            "our input entirely -- the click would move the cursor and do nothing "
            "else, leaving a stray tooltip on screen.\n"
            "Open an Administrator PowerShell and rerun, or add --dry-run."
        )

    # Record by default whenever the run is going to act. Those are the frames
    # worth having -- a live run reaches states that sitting and capturing
    # never will, and the ones where it aborts are the most useful of all.
    global RECORD_ENABLED
    RECORD_ENABLED = (args.record or clicking) and not args.no_record
    if RECORD_ENABLED:
        print(f"Recording frames to {RECORD_DIR} (--no-record to turn off).")

    if args.calibrate:
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        sys.exit(0 if calibrate() else 1)

    # Establish the layout before ANY coordinate is read. Every region in this
    # file was measured at 2560x1440; on a different screen they point at the
    # wrong pixels, and for a clicking command that means hitting the game
    # world instead of the UI. Read-only commands are allowed to proceed
    # uncalibrated -- they can only report something wrong, not do something
    # wrong -- but they still try, so --list and --panel work on any machine.
    #
    # --open is exempt: calibration measures the Trade window, so it needs the
    # shop open, and --open is what opens it. Gating it made the two commands
    # refuse each other in a loop on any non-reference screen -- "run
    # --calibrate" / "the Trade window is not open, run --open" -- with no way
    # out. It only needs the NPC regions, and it is the bootstrap command.
    if not args.no_calibrate and not args.open:
        if not ensure_calibrated(required=clicking):
            sys.exit(1)
    elif args.no_calibrate and clicking:
        screen = current_screen_size()
        if screen and tuple(screen) != REF_SCREEN:
            print(f"WARNING: --no-calibrate on a {screen[0]}x{screen[1]} screen. "
                  f"The built-in coordinates were measured at "
                  f"{REF_SCREEN[0]}x{REF_SCREEN[1]}, so clicks will land in the "
                  "wrong place.", file=sys.stderr)

    if args.panel:
        print(read_register_panel(grab()))
        return

    if args.reset:
        ok = prepare_for_actions()
        print(f"trade open: {trade_window_open()}, "
              f"register tab: {register_tab_open()}, "
              f"shop slot: {read_register_panel(grab())['qty_text']!r}")
        sys.exit(0 if ok else 1)

    if args.open:
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        ok = open_trade_window()
        print(f"trade window open: {trade_window_open()}, "
              f"register tab: {register_tab_open()}")
        sys.exit(0 if ok else 1)

    if args.confirm:
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        ok = confirm_open_dialogs()
        print(f"shop slot: {read_register_panel(grab())['qty_text']!r}, "
              f"loaded={read_register_panel(grab())['loaded']}")
        sys.exit(0 if ok else 1)

    if args.clear:
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        if dialog_kind(grab()) is not None and not close_any_dialog():
            sys.exit("Could not close the open dialog.")
        if not clear_shop_slot():
            sys.exit("Could not clear the shop slot.")
        print(f"Shop slot clear: {read_register_panel(grab())['qty_text']}")
        return

    if args.load:
        row, col = args.load
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        before = read_register_panel(grab())
        if before["loaded"]:
            sys.exit(f"The shop slot already holds an item ({before}). Clear it first.")
        centre = slot_centre(row, col)
        print(f"Ctrl+Click inventory slot ({row},{col}) at {centre}")
        ctrl_click(*centre)
        time.sleep(0.8)
        park_cursor()
        after = read_register_panel(grab())
        print(f"panel after: {after}")
        if not after["loaded"]:
            sys.exit("Nothing was loaded into the shop slot.")
        return

    if args.listings:
        # Walks the whole shop one row at a time and prints it with ABSOLUTE
        # positions. Still read-only in effect: it scrolls and reads, and
        # touches nothing. This is the foundation relisting past row 10 needs,
        # exercised on its own before anything acts on it.
        found = enumerate_listings()
        if found is None:
            print("\nCould not enumerate the shop. Nothing was acted on.")
            sys.exit(1)
        floors = 0
        print(f"\n{'#':>3}  {'action':8} {'name':44} {'qty':>5} {'price':>14}")
        for index, row in found:
            floor = item_price_floor(row.name)
            if floor:
                floors += 1
            print(f"{index:3d}  {row.action:8} {row.name[:44]:44} "
                  f"{str(row.qty):>5} {row.price if row.price is None else format(row.price, ',') :>14}"
                  + (f"   floor {floor:,}" if floor else ""))
        live = [r for _, r in found if r.action in ("change", "receive")]
        print(f"\n{len(found)} listing(s), {len(live)} live, "
              f"{len(found) - EXPECTED_ROWS} beyond the first screen, "
              f"{floors} carrying a price floor")
        sys.exit(0)

    if args.scroll is not None:
        # Deliberately a probe, not a feature. Nothing in the relist path uses
        # scrolling yet, because the hard part is not turning the wheel -- it
        # is knowing WHICH listings you are looking at afterwards. read_rows
        # numbers rows by screen position, so once the view moves, "row 1"
        # is a different listing and every caller that acts on an index is
        # acting on the wrong one. This measures the behaviour first.
        before = grab()
        rows_before = read_rows(before)
        print(f"before: {len(rows_before)} rows")
        for r in rows_before:
            print(f"   {r.index:2d} [{r.action:8}] {r.name[:38]:40} "
                  f"x{str(r.qty):>4} {r.price}  band {r.top}-{r.bottom}")
        centre = ((TRADE_REGION[0] + TRADE_REGION[2]) // 2,
                  (TRADE_REGION[1] + TRADE_REGION[3]) // 2)
        print(f"\nscrolling {args.scroll:+d} notch(es) at {centre}")
        record("scroll.before", before, notches=args.scroll)
        scroll_wheel(*centre, args.scroll)
        time.sleep(0.8)
        park_cursor()
        after = grab()
        rows_after = read_rows(after)
        record("scroll.after", after, notches=args.scroll)
        print(f"\nafter: {len(rows_after)} rows")
        for r in rows_after:
            print(f"   {r.index:2d} [{r.action:8}] {r.name[:38]:40} "
                  f"x{str(r.qty):>4} {r.price}  band {r.top}-{r.bottom}")

        # How far did it move? Recovered from CONTENT, never from the notch
        # count -- the wheel-to-lines ratio is the game's business and may not
        # even be constant. Identity here is (name, price, qty).
        def key(r):
            return (r.name, r.price, r.qty)
        b = [key(r) for r in rows_before]
        a = [key(r) for r in rows_after]
        # EVERY offset that fits, not the first. With duplicate listings more
        # than one can fit, and taking the first would silently pick the wrong
        # one -- 41 of 43 recorded tables contain rows identical in name,
        # quantity AND price, so this is the normal case here, not an edge one.
        fits = []
        for d in range(-len(b), len(b) + 1):
            overlap = [(i, i + d) for i in range(len(b))
                       if 0 <= i + d < len(a)]
            if len(overlap) >= 3 and all(b[i] == a[j] for i, j in overlap):
                fits.append((-d, len(overlap)))
        if len(fits) == 1:
            shift = fits[0][0]
        elif len(fits) > 1:
            shift = None
            print(f"\nAMBIGUOUS: {len(fits)} offsets fit equally well "
                  f"{[f[0] for f in fits]} - duplicate listings make the view "
                  "position unrecoverable from content alone.")
        else:
            shift = None
        print(f"\nrows moved: {shift if shift is not None else 'COULD NOT TELL'}")
        if fits:
            print(f"  offsets that fit: {fits}  (offset, overlapping rows)")
        if shift == 0:
            print("  the view did not move - the list may be at its end, or the")
            print("  wheel event did not reach the table.")
        elif shift is None:
            print("  no consistent overlap of 3+ rows. Either the whole page")
            print("  changed, or a listing changed under us. Do NOT act on")
            print("  positions in this state.")
        return

    if args.words:
        for w in sorted(find_words(grab(), TRADE_REGION), key=lambda w: (w.top, w.left)):
            print(f"{w.centre}  conf={w.conf:5.1f}  {w.text!r}")
        return

    if args.list:
        rows = await_rows()
        if not rows:
            print("No listings visible.")
        for row in rows:
            print(f"row {row.index:2d}: [{row.action:8s}] button={row.change}  {row.name!r}")
        return

    if args.relist_rows:
        try:
            wanted = parse_row_spec(args.relist_rows)
        except ValueError as exc:
            p.error(f"bad --relist-rows spec: {exc}")
        if not wanted:
            p.error("--relist-rows needs at least one row")
        try:
            ok = relist_rows(wanted, dry_run=args.dry_run)
        except FatalAbort as exc:
            sys.exit(f"FATAL: {exc}")
        except (PermissionError, Aborted) as exc:
            sys.exit(f"Blocked: {exc}")
        sys.exit(0 if ok else 1)

    if args.relist:
        if len(args.relist) not in (1, 3):
            p.error("--relist takes ROW, or ROW INV_ROW INV_COL")
        slot = tuple(args.relist[1:]) if len(args.relist) == 3 else (None, None)
        try:
            outcome = relist(args.relist[0], *slot, dry_run=args.dry_run)
        except FatalAbort as exc:
            sys.exit(f"FATAL: {exc}")
        except (PermissionError, Aborted) as exc:
            sys.exit(f"Blocked: {exc}")
        print(f"outcome: {outcome}")
        sys.exit(0 if outcome != FAILED else 1)

    if args.repeat:
        sys.exit(0 if run_loop(args.repeat, args.duration, args.every,
                               dry_run=args.dry_run) else 1)

    if args.do:
        try:
            ok = run_sequence(args.do, dry_run=args.dry_run)
        except FatalAbort as exc:
            sys.exit(f"FATAL: {exc}")
        except (PermissionError, Aborted) as exc:
            sys.exit(f"Blocked: {exc}")
        sys.exit(0 if ok else 1)

    if args.register is not None:
        try:
            ok = register_item(*args.register, dry_run=args.dry_run,
                               # None, not False: absent means "use the
                               # configured policy", which is what the relist
                               # path does. Passing False here was the bug --
                               # it overrode MAXIMISE_ALL_QUANTITIES with a
                               # flag default and listed one unit of a stack.
                               maximise_qty=True if args.max_qty else None,
                               price_floor=args.floor,
                               floor_reason="--floor" if args.floor else "",
                               force_price=args.price, force_qty=args.qty)
        except FatalAbort as exc:
            sys.exit(f"FATAL: {exc}")
        except (PermissionError, Aborted) as exc:
            sys.exit(f"Blocked: {exc}")
        sys.exit(0 if ok else 1)

    if args.cancel is None:
        p.error("nothing to do. Capture: --shot, --alz, --monitors. Read: "
                "--list, --panel, --words. Act: --open, --relist, --relist-rows, "
                "--cancel, --register, --load, --clear, --confirm, --reset, "
                "--do, --repeat. See --help.")

    try:
        ok = cancel_item(args.cancel, dry_run=args.dry_run)
    except PermissionError as exc:
        sys.exit(f"Blocked: {exc}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    # Logging is started here rather than inside main() so it covers argument
    # parsing too -- a bad argument that exits is still something worth having
    # a record of -- and so the `finally` cannot be skipped by any of main()'s
    # many sys.exit() calls.
    # atexit as well as the handlers below, so the duration is printed on
    # paths that never reach them: os.abort, a sys.exit deep inside a library,
    # or an exception raised while the __main__ guard itself is unwinding.
    # finish_run_log is idempotent, so whichever fires first wins.
    atexit.register(finish_run_log)

    _log_path = start_run_log(sys.argv)
    if _log_path:
        print(f"Logging this run to {_log_path}")
    try:
        main()
    except SystemExit as exc:
        finish_run_log(f"exit {exc.code}")
        raise
    except BaseException as exc:          # noqa: BLE001 - includes KeyboardInterrupt
        # sys.excepthook writes the traceback; this adds the closing line so a
        # log that stops without one means the process was killed outright
        # rather than having raised.
        finish_run_log(f"{type(exc).__name__}")
        raise
    else:
        finish_run_log()
