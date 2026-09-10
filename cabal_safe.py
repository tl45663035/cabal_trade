
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



ITEM_PRICE_FLOORS: tuple[tuple[str, str, int], ...] = (
    ("vip", "Yekaterina VIP Membership", 104_000_000),
    ("siena", "Siena's Unbinding Stone", 71_000_000),
)

FALLBACK_PRICE = 10_000_000_000

MIN_PLAUSIBLE_PRICE = 1_000

SUSPECT_PRICE_FRACTION = 0.5


MAXIMISE_ALL_QUANTITIES = True

NO_MAX_QUANTITY_ITEMS: tuple[str, ...] = ()

MAX_QTY_ENTRY = 9999

QTY_CROSSCHECK_ABSOLUTE = 5

QTY_CROSSCHECK_FRACTION = 0.10


ACTION_COOLDOWN = 0.5

TYPE_COOLDOWN = 0.5

MIN_CYCLE_SECONDS = 1.0

RECEIVE_WAIT = 3.0

RELIST_ATTEMPTS = 3

SEND_ATTEMPTS = 4

MAX_CONSECUTIVE_FAILURES = 3

TESSERACT_TIMEOUT = 30.0


GAME_TITLE_HINT = "PlayCabal"

WORK_TAB = 4

EXPECTED_ROWS = 10


RECORD_ENABLED = False

RECORD_LIMIT = 12000



try:
    import mss
except ImportError:
    sys.exit("Missing dependency 'mss'. Install it with:  pip install mss")

try:
    from PIL import Image, ImageChops, ImageOps
except ImportError:
    sys.exit("Missing dependency 'Pillow'. Install it with:  pip install Pillow")

SCRIPT_DIR = Path(__file__).resolve().parent


TABLE_READ_BUDGET = 45.0

DEFAULT_OUTDIR = SCRIPT_DIR / "screenshots"

open_capture = getattr(mss, "MSS", None) or mss.mss

ALZ_REGION = (2330, 872, 2525, 928)

TESSERACT_CANDIDATES = (
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
)



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
    crop = image.crop(region).convert("RGB")
    scale = 5
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)

    px = crop.load()
    mask = Image.new("L", crop.size, 255)
    m = mask.load()
    for y in range(crop.height):
        for x in range(crop.width):
            r, g, b = px[x, y]
            hi, lo = max(r, g, b), min(r, g, b)
            if hi > 110 and hi - lo > 45:
                m[x, y] = 0

    bbox = ImageOps.invert(mask).getbbox()
    if not bbox:
        return None

    prepared = ImageOps.expand(mask.crop(bbox), border=60, fill=255).convert("RGB")
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
    image = source if isinstance(source, Image.Image) else Image.open(source)
    region = region if region is not None else ALZ_REGION
    if region[2] > image.width or region[3] > image.height:
        return None

    found = _isolate_digits(image, region)
    if found is None:
        return None

    _, box = found
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



def make_dpi_aware() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def list_monitors(sct) -> None:
    for i, mon in enumerate(sct.monitors[1:], start=1):
        print(f"{i}: {mon['width']}x{mon['height']} at ({mon['left']},{mon['top']})")


def resolve_monitor(sct, choice: str) -> tuple[dict, str]:
    displays = sct.monitors[1:]

    if choice == "all":
        return sct.monitors[0], "all"
    if choice == "primary":
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
    if not path.exists():
        return path
    for n in range(2, 100):
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def prune_screenshots(outdir: Path, keep: int, protect: Path) -> int:
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
    import io

    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    with io.BytesIO() as buf:
        image.save(buf, "BMP")
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
        if not user32.SetClipboardData(CF_DIB, handle):
            kernel32.GlobalFree(ctypes.c_void_p(handle))
            print("Skipping --clipboard: SetClipboardData failed", file=sys.stderr)
            return
    finally:
        user32.CloseClipboard()

    print("Copied to clipboard.")


def take_screenshot(monitor: str = "primary", delay: float = 0.0):
    make_dpi_aware()
    with open_capture() as sct:
        region, label = resolve_monitor(sct, monitor)
        if delay > 0:
            time.sleep(delay)
        shot = sct.grab(region)
    origin = (region["left"], region["top"])
    return mss.tools.to_png(shot.rgb, shot.size), shot.width, shot.height, label, origin



REF_SCREEN = (2560, 1440)
REF_TRADE_ORIGIN = (10, 30)
REF_TRADE_SIZE = (1225, 1035)

REF_ANCHORS: tuple[tuple[str, tuple[int, int]], ...] = (
    ("Trade", (608, 19)),
    ("Name", (492, 119)),
    ("Adjust", (919, 65)),
    ("Function", (1126, 118)),
    ("Selling", (331, 982)),
    ("Refresh", (1119, 981)),
)
MIN_ANCHOR_BASELINE = 300.0
MIN_ANCHOR_SPREAD = 250.0
NEAR_ANCHOR_MIN_CONF = 70.0
MIN_ANCHORS_AFTER_DROP = 4
SCALE_LIMITS = (0.4, 2.5)
CALIBRATION_FILE = SCRIPT_DIR / "calibration.json"


@dataclass(frozen=True)
class Layout:
    screen: tuple[int, int]
    origin: tuple[int, int]
    scale: float
    client: tuple[int, int, int, int] | None = None
    measured_from: str = "reference"

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

    @property
    def trade(self) -> tuple[int, int, int, int]:
        return self.box((0, 0, REF_TRADE_SIZE[0], REF_TRADE_SIZE[1]))

    def describe(self) -> str:
        return (f"screen {self.screen[0]}x{self.screen[1]}, Trade window at "
                f"{self.origin}, scale {self.scale:.3f} ({self.measured_from})")


REF_FUNCTION_COLUMN_X = 1116
REF_DIALOG_BUTTON_MIN_X = 1200

LAYOUT = Layout(screen=REF_SCREEN, origin=REF_TRADE_ORIGIN, scale=1.0,
                measured_from="reference defaults")

TRADE_REGION = LAYOUT.trade
POPUP_REGION = (500, 350, 2100, 1150)


def _clamp_box(box: tuple[int, int, int, int],
               screen: tuple[int, int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    left, top = max(0, left), max(0, top)
    right, bottom = min(screen[0], right), min(screen[1], bottom)
    return (left, top, max(left + 1, right), max(top + 1, bottom))



CONFIRM_WORD = "Confirmation"
DISMISS_WORD = "Cancel"
RECEIPT_WORD = "Receive"
DIALOG_BUTTON_MIN_X = 1200
MAX_CONFIRM_STEPS = 3
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



def _prep_for_text(image: Image.Image, region: tuple[int, int, int, int], scale: int):
    crop = image.crop(region).convert("L")
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    return ImageOps.autocontrast(ImageOps.invert(crop))


_CALIBRATED = False


def _ocr_reference_scale() -> float:
    if _CALIBRATED:
        return LAYOUT.scale
    try:
        make_dpi_aware()
        client = client_rect()
        if client:
            width = client[2] - client[0]
            height = client[3] - client[1]
            ref_w = REF_CLIENT[2] - REF_CLIENT[0]
            ref_h = REF_CLIENT[3] - REF_CLIENT[1]
            if width > 100 and height > 100:
                guess = min(width / ref_w, height / ref_h)
                if SCALE_LIMITS[0] <= guess <= SCALE_LIMITS[1]:
                    return guess
    except Exception:
        pass
    return LAYOUT.scale


def find_words(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int],
    min_conf: float = 40.0,
    scale: int | None = None,
) -> list[Word]:
    if scale is None:
        scale = max(2, min(6, int(round(2 / max(_ocr_reference_scale(), 0.34)))))
    tesseract = find_tesseract()
    if tesseract is None:
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
        print(f"Could not run Tesseract ({exc}); treating this frame as "
              "unreadable.", file=sys.stderr)
        return []
    if result.returncode != 0:
        print(f"Tesseract failed ({result.stderr.decode(errors='replace').strip()}); "
              "treating this frame as unreadable.", file=sys.stderr)
        return []

    words: list[Word] = []
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


_DIGIT_LOOKALIKES = str.maketrans({
    "O": "0", "o": "0", "Q": "0",
    "I": "1", "i": "1", "l": "1", "|": "1",
    "Z": "2", "z": "2",
    "S": "5", "s": "5",
    "G": "6",
    "T": "7",
    "B": "8",
})


INK_CONTRAST_MIN = 160


def _ink_box(prepared: Image.Image, threshold: int = 128):
    return prepared.point(lambda v: 255 if v < threshold else 0).getbbox()


def read_number(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int],
    min_conf: float = 0.0,
) -> int | None:
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

    prepared = ImageOps.expand(_prep_for_text(image, box, 4), border=24, fill=255)

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

    ink = _ink_box(prepared)
    if ink is None:
        return None
    ink_w, ink_h = ink[2] - ink[0], ink[3] - ink[1]
    if ink_h <= 0 or ink_w > ink_h * 1.15:
        return None
    return _digits(glyph.translate(_DIGIT_LOOKALIKES))


def find_text(
    source: Image.Image | Path | str,
    needle: str,
    region: tuple[int, int, int, int],
    min_conf: float = 40.0,
) -> list[Word]:
    needle = needle.casefold()
    hits = [w for w in find_words(source, region, min_conf) if needle in w.text.casefold()]
    return sorted(hits, key=lambda w: w.top)


def _text_lines(words: list[Word], tolerance: int = 10) -> list[list[Word]]:
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
WHEEL_DELTA = 120
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12




def cooldown(seconds: float | None = None) -> None:
    time.sleep(ACTION_COOLDOWN if seconds is None else seconds)


SEND_RETRY_PAUSE = 0.12

def _send(event: _Input, attempts: int = SEND_ATTEMPTS) -> None:
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
    for vk in (VK_CONTROL, VK_MENU, VK_SHIFT):
        try:
            _release(_key_event(vk, up=True), f"modifier 0x{vk:02X}", attempts=1)
        except Exception:
            pass


def _mouse_event(flags: int) -> _Input:
    return _Input(type=INPUT_MOUSE,
                  u=_InputUnion(mi=_MouseInput(0, 0, 0, flags, 0, None)))


def _key_event(vk: int, up: bool) -> _Input:
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    return _Input(type=INPUT_KEYBOARD,
                  u=_InputUnion(ki=_KeyInput(vk, scan, flags, 0, None)))


VK_BACK = 0x08
VK_ESCAPE = 0x1B
CLEAR_KEYSTROKES = 13


def press_escape(settle: float = 0.5) -> None:
    _send(_key_event(VK_ESCAPE, up=False))
    try:
        time.sleep(0.05)
    finally:
        _release_key(VK_ESCAPE)
    time.sleep(settle)
    cooldown()


def type_number(value: int, per_key: float = TYPE_COOLDOWN,
                clear_first: bool = True, clear: int | None = None) -> None:
    def tap(vk: int) -> None:
        _send(_key_event(vk, up=False))
        try:
            time.sleep(0.02)
        finally:
            _release_key(vk)
        time.sleep(per_key)

    if clear_first:
        for _ in range(CLEAR_KEYSTROKES if clear is None else clear):
            tap(VK_BACK)
    for ch in str(value):
        tap(0x30 + int(ch))
    cooldown()


def scroll_wheel(x: int, y: int, notches: int, settle: float = 0.35) -> None:
    make_dpi_aware()
    if not move_mouse(x, y):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    step = (WHEEL_DELTA if notches > 0 else -WHEEL_DELTA) & 0xFFFFFFFF
    for _ in range(abs(notches)):
        _send(_Input(type=INPUT_MOUSE,
                     u=_InputUnion(mi=_MouseInput(0, 0, step,
                                                  MOUSEEVENTF_WHEEL, 0, None))))
        time.sleep(settle)
    cooldown()


def click(x: int, y: int, settle: float = 0.15) -> None:
    make_dpi_aware()
    if not move_mouse(x, y):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    time.sleep(settle)

    _send(_mouse_event(MOUSEEVENTF_LEFTDOWN))
    try:
        time.sleep(0.09)
    finally:
        _release_left_button()
    time.sleep(0.05)
    cooldown()


def ctrl_click(x: int, y: int, settle: float = 0.15) -> None:
    make_dpi_aware()
    if not move_mouse(x, y):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    time.sleep(settle)

    _send(_key_event(VK_CONTROL, up=False))
    try:
        time.sleep(0.25)
        _send(_mouse_event(MOUSEEVENTF_LEFTDOWN))
        try:
            time.sleep(0.12)
        finally:
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



RECORD_DIR = SCRIPT_DIR / "unit_tests" / "corpus"
_last_shot: "Image.Image | None" = None
_record_seq = 0
_record_full = False


def record(label: str, shot: "Image.Image | None" = None, /, **context) -> None:
    global _record_seq, _record_full
    if not RECORD_ENABLED or _record_full:
        return
    image = shot if shot is not None else _last_shot
    if image is None:
        return
    try:
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        if _record_seq == 0:
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
                 "at": datetime.now().isoformat(timespec="seconds")}
        for key, value in context.items():
            if value is None:
                continue
            entry["ctx_" + key if key in ("file", "label", "at") else key] = value
        with (RECORD_DIR / "run_index.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass



ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
UOI_NAME = 2
DESKTOP_SWITCHDESKTOP = 0x0100


def session_locked() -> bool:
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
    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState.restype = ctypes.c_ulong
    flags = ES_CONTINUOUS
    if enable:
        flags |= ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    return bool(kernel32.SetThreadExecutionState(flags))





def find_game_window() -> int | None:
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
    user32 = ctypes.windll.user32
    hwnd = find_game_window()
    if hwnd is None:
        return False

    if user32.GetForegroundWindow() == hwnd:
        return True

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(settle)

    if user32.GetForegroundWindow() == hwnd:
        return True

    try:
        _send(_key_event(VK_MENU, up=False))
        try:
            time.sleep(0.03)
        finally:
            _release_key(VK_MENU)
        time.sleep(0.05)
    except PermissionError:
        _release_key(VK_MENU)

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



NAME_COLUMN = (275, 715)
PREMIUM_SLOT_MARKER = "premium"
REF_ROW_PITCH = 79
BUTTON_WORDS = ("Change", "Receive", "Register")
QTY_COL_MIN_CONF = 15.0
PARK_POINT = (600, 45)


@dataclass
class Row:
    index: int
    name: str
    change: tuple[int, int]
    top: int
    bottom: int
    action: str
    price: int | None = None
    qty: int | None = None

    @property
    def cancellable(self) -> bool:
        return self.action == "change"


def park_cursor(settle: float = 0.0) -> None:
    if not move_mouse(*PARK_POINT):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    if settle:
        time.sleep(settle)



NPC_NAME_FRAGMENT = "katerina"
NPC_TITLE_FRAGMENT = "agentshop"
NPC_SEARCH_REGION = (600, 150, 1900, 900)
NPC_EXCLUDE_ZONES = (
    (0, 700, 900, 1440),
    (1850, 0, 2560, 1440),
)
NPC_CLICK_ATTEMPTS = 100
NPC_BODY_OFFSET = (0, 120)
NPC_CLICK_OFFSET = NPC_BODY_OFFSET


def _npc_click_offsets(attempts: int = NPC_CLICK_ATTEMPTS) -> tuple:
    cx, cy = NPC_BODY_OFFSET
    step_y = max(4, LAYOUT.length(10))
    step_x = max(6, LAYOUT.length(15))
    span_y = LAYOUT.length(110), LAYOUT.length(160)
    span_x = LAYOUT.length(60)
    grid = [(cx + dx, cy + dy)
            for dy in range(-span_y[0], span_y[1] + 1, step_y)
            for dx in range(-span_x, span_x + 1, step_x)]
    grid.sort(key=lambda p: (4 * (p[0] - cx) ** 2 + (p[1] - cy) ** 2,
                             abs(p[0] - cx), p[1]))
    return tuple(grid[:attempts])


NPC_CLICK_OFFSETS = _npc_click_offsets()
NPC_CLICK_WAIT = 1.5
NPC_SWEEP_BUDGET = 120.0
NPC_LOST_LIMIT = 6
PURCHASE_TAB_WORD = "Purchase"
REGISTER_TAB_WORD = "Register"
TRADE_OPEN_MARKERS = ("Purchase", "Adjust", "Register", "Function")
TRADE_WINDOW_SEARCH = (0, 0, 1700, 700)


def find_npc(
    source: Image.Image | None = None, retries: int = 4,
    seen: dict | None = None,
) -> tuple[int, int] | None:
    def excluded(point: tuple[int, int]) -> bool:
        x, y = point
        return any(left <= x <= right and top <= y <= bottom
                   for left, top, right, bottom in NPC_EXCLUDE_ZONES)

    def label_centre(image: Image.Image, region=None) -> tuple[int, int] | None:
        for line in _text_lines(find_words(image, region or NPC_SEARCH_REGION, 25)):
            joined = _normalise("".join(w.text for w in line))
            if NPC_NAME_FRAGMENT not in joined or NPC_TITLE_FRAGMENT not in joined:
                continue
            window = _minimal_window(line, (NPC_NAME_FRAGMENT, NPC_TITLE_FRAGMENT))
            if window is None:
                continue
            return _span_centre(window)
        return None

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
            break
        time.sleep(0.4)
    return None


def panel_covers_trade_area(gap: float = 0.35, threshold: float = 2.0) -> bool:
    first = grab().convert("L").crop(TRADE_REGION)
    time.sleep(gap)
    second = grab().convert("L").crop(TRADE_REGION)
    diff = ImageChops.difference(first, second)
    data = list(getattr(diff, "get_flattened_data", diff.getdata)())
    return (sum(data) / len(data)) < threshold


def trade_window_open(source: Image.Image | None = None) -> bool:
    image = source if source is not None else grab()
    return any(find_text(image, marker, TRADE_WINDOW_SEARCH)
               for marker in TRADE_OPEN_MARKERS)


def register_tab_open(source: Image.Image | None = None) -> bool:
    image = source if source is not None else grab()
    return find_phrase(image, "Register Item", REGISTER_PANEL) is not None


def open_trade_window(timeout: float = 15.0, verbose: bool = True) -> bool:
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
        sweep_deadline = time.monotonic() + NPC_SWEEP_BUDGET
        for index, offset in enumerate(NPC_CLICK_OFFSETS, start=1):
            if time.monotonic() >= sweep_deadline:
                say(f"  giving up after {index - 1} attempts "
                    f"({NPC_SWEEP_BUDGET:g}s budget spent).")
                break

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
    tab = tabs[0]
    record("tab.before_register_click", centre=str(tab.centre))
    click(*tab.centre)
    if not wait_until(register_tab_open, timeout):
        say("The Register tab did not open.")
        return False
    record("tab.register_open")
    return True


def refresh_table(timeout: float = 20.0, verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

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
    image = source if source is not None else grab()
    return find_text(image, "Change", TRADE_REGION)


def find_row_buttons(image: Image.Image,
                     words: "list[Word] | None" = None) -> list[Word]:
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

    anchors = [w for w in hits if "change" in w.text.casefold()] or hits
    xs = sorted(w.centre[0] for w in anchors)
    column_x = xs[len(xs) // 2]
    hits = [w for w in hits if abs(w.centre[0] - column_x) <= 45]

    hits.sort(key=lambda w: w.top)
    deduped: list[Word] = []
    for w in hits:
        if deduped and w.top - deduped[-1].top < 20:
            continue
        deduped.append(w)
    return deduped


def table_loading(source: Image.Image | Path | str) -> bool:
    return bool(find_text(source, "Waiting", TRADE_REGION))


def wait_for_table(timeout: float = 20.0, poll: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not table_loading(grab()):
            return True
        time.sleep(poll)
    return False


def _header(words: "list[Word] | None", image: Image.Image, needle: str):
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
    if right - left < 40:
        return NAME_COLUMN
    return (left, right)


def price_column(image: Image.Image,
                 words: "list[Word] | None" = None) -> tuple[int, int] | None:
    qty_header = _header(words, image, "QTY")
    status_header = _header(words, image, "Status")
    if qty_header is None or status_header is None:
        return None
    qtys, statuses = [qty_header], [status_header]
    left, right = qtys[0].right + 4, statuses[0].left - 4
    return (left, right) if right > left else None


def await_rows(timeout: float = TABLE_READ_BUDGET, poll: float = 0.5) -> list[Row]:
    try:
        park_cursor()
    except PermissionError:
        pass

    deadline = time.monotonic() + max(timeout, TABLE_READ_BUDGET)
    while True:
        shot = grab()
        if not table_loading(shot):
            rows = read_rows(shot)
            if rows:
                return rows
        if time.monotonic() >= deadline:
            return []
        time.sleep(poll)


def read_rows(source: Image.Image | Path | str) -> list[Row]:
    image = source if isinstance(source, Image.Image) else Image.open(source)

    words = find_words(image, TRADE_REGION, 0.0)
    if not words:
        return []

    buttons = find_row_buttons(image, words)
    if not buttons:
        return []

    if len(buttons) != EXPECTED_ROWS:
        return []

    pitch = LAYOUT.length(REF_ROW_PITCH)
    if len(buttons) > 1:
        gaps = sorted(b.top - a.top for a, b in zip(buttons, buttons[1:]))
        pitch = gaps[len(gaps) // 2]
        if any(gap > pitch * 1.6 for gap in gaps):
            return []

    left, right = name_column(image, words)
    price_bounds = price_column(image, words)

    def cell(x0: int, y0: int, x1: int, y1: int, min_conf: float = 40.0):
        return [w for w in words
                if w.conf >= min_conf
                and x0 <= w.centre[0] <= x1 and y0 <= w.centre[1] <= y1]

    rows: list[Row] = []
    for i, button in enumerate(buttons, start=1):
        cy = button.centre[1]
        top, bottom = cy - pitch // 2, cy + pitch // 2
        cell_words = [w for w in cell(left, top, right, bottom)
                      if len(w.text.strip()) > 2 or w.conf >= NAME_FRAGMENT_MIN_CONF]
        name = " ".join(w.text for line in _text_lines(cell_words) for w in line)

        if not name.strip() and button.text.strip().casefold() != "register":
            retry = [w for w in find_words(image, (left, top, right, bottom), 40.0)
                     if len(w.text.strip()) > 2 or w.conf >= NAME_FRAGMENT_MIN_CONF]
            if retry:
                name = " ".join(w.text for line in _text_lines(retry)
                                for w in line)

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

        qty = None
        if price_bounds and price_bounds[0] - (right + 2) >= 8:
            box = (right + 2, top, price_bounds[0] - 2, bottom)
            digits = sorted(cell(*box, QTY_COL_MIN_CONF), key=lambda w: w.left)
            qty = _digits("".join(w.text for w in digits))
            if qty is None:
                qty = read_number(image, box, QTY_COL_MIN_CONF)

        action = button.text.strip().casefold()
        if action == "register" and name.strip():
            if PREMIUM_SLOT_MARKER in _normalise(name):
                name = ""
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
    column_x = LAYOUT.x(REF_FUNCTION_COLUMN_X)
    keep_away = max(40, LAYOUT.length(60))
    hits = [w for w in find_text(source, word, POPUP_REGION, min_conf)
            if abs(w.centre[0] - column_x) > keep_away]
    return hits[-1] if hits else None


def await_dialog_button(
    word: str, timeout: float = 6.0, poll: float = 0.4
) -> Word | None:
    deadline = time.monotonic() + timeout
    while True:
        button = dialog_button(grab(), word)
        if button is not None:
            return button
        if time.monotonic() >= deadline:
            break
        time.sleep(poll)
    return dialog_button(grab(), word, min_conf=15.0)


def _mentions(texts: list[str], keyword: str, threshold: float = 0.85) -> bool:
    from difflib import SequenceMatcher

    for text in texts:
        if keyword in text:
            return True
        if abs(len(text) - len(keyword)) > 2:
            continue
        if text in keyword:
            return True
        if SequenceMatcher(None, keyword, text).ratio() >= threshold:
            return True
    return False


def dialog_kind(source: Image.Image | Path | str) -> str | None:
    words = find_words(source, POPUP_REGION, DIALOG_TEXT_MIN_CONF)
    texts = [_normalise(w.text) for w in words]
    texts += [_normalise("".join(w.text for w in line))
              for line in _text_lines(words)]
    if _mentions(texts, "receipt"):
        return "receipt"
    if _mentions(texts, "confirmation"):
        return "confirm"
    if _mentions(texts, "extension") or _mentions(texts, "registration"):
        return "extension"
    return None


def confirm_open_dialogs(settle: float = 0.8, verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    confirmed = 0
    for step in range(1, MAX_CONFIRM_STEPS + 1):
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
    for _ in range(tries):
        button = await_dialog_button(DISMISS_WORD, timeout=3.0)
        if button is None:
            break
        click(*button.centre)
        time.sleep(settle)
    return await_dialog(None, timeout=6.0) is not None


def _normalise(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


_OCR_CONFUSIONS = str.maketrans({"0": "o", "1": "i", "l": "i"})

FUZZY_NAME_THRESHOLD = 0.95
FUZZY_NAME_MARGIN = 0.03
UNMATCHED_NAME_SIMILARITY = 0.70
NAME_FRAGMENT_MIN_CONF = 60.0


def _canonical(text: str) -> str:
    return _normalise(text).translate(_OCR_CONFUSIONS)


_FLOOR_LOOKALIKES = str.maketrans({
    "|": "i", "!": "i", "1": "i", "l": "i", "[": "i", "]": "i",
    "/": "i", "\\": "i", "j": "i", ":": "i", ";": "i", "¦": "i",
    "0": "o",
})


def _floor_key(text: str) -> str:
    folded = text.casefold().translate(_FLOOR_LOOKALIKES)
    return "".join(ch for ch in folded if ch.isalnum())


_NAME_TRAILER = re.compile(
    r"\b(use\s*period|duration|grade\s*\d|drop\s*not\s*allowed|register\s*cost"
    r"|item\s*sold|remaining\s*time|sales?\s*price)\b.*",
    re.IGNORECASE | re.DOTALL,
)


def item_name(row_name: str) -> str:
    return _NAME_TRAILER.sub("", row_name).strip(" :-")


def match_rows(rows: list[Row], item: str,
               threshold: float = FUZZY_NAME_THRESHOLD) -> list[Row]:
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

    runner_up = next((score for score, row in scored[1:]
                      if _canonical(row.name) != best_name), 0.0)

    required = max(threshold, 1.0 - 1.0 / max(len(needle), 1) + 0.005)
    if best_score < required or best_score - runner_up < FUZZY_NAME_MARGIN:
        return []
    return [row for _, row in scored if _canonical(row.name) == best_name]


def match_row(rows: list[Row], item: str,
              threshold: float = FUZZY_NAME_THRESHOLD) -> Row | None:
    found = match_rows(rows, item, threshold)
    return found[0] if len(found) == 1 else None


@dataclass(frozen=True)
class RowRef:
    name: str
    qty: int | None = None
    price: int | None = None
    ordinal: int = 0

    @classmethod
    def of(cls, row: Row, rows: list[Row] | None = None) -> "RowRef":
        ordinal = 0
        if rows:
            for position, candidate in enumerate(cls._siblings(rows, row)):
                if candidate.index == row.index:
                    ordinal = position
                    break
        return cls(row.name, row.qty, row.price, ordinal)

    @staticmethod
    def _siblings(rows: list[Row], row: Row) -> list[Row]:
        pool = match_rows(rows, row.name)
        for value, attribute in ((row.qty, "qty"), (row.price, "price")):
            if value is None:
                continue
            narrowed = [r for r in pool if getattr(r, attribute) == value]
            if narrowed:
                pool = narrowed
        return pool



SCROLL_TO_END_NOTCHES = 40
MIN_SCROLL_OVERLAP = 3


def _row_key(row: Row) -> tuple:
    return (row.name, row.price, row.qty, row.action)


def measure_shift(before: list[Row], after: list[Row],
                  minimum: int = MIN_SCROLL_OVERLAP) -> int | None:
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


SCROLL_STEP = 7
MAX_SCROLL_CHUNKS = 8


def scroll_chunk(notches: int, before: list[Row], timeout: float = 8.0,
                 verbose: bool = True) -> tuple[list[Row] | None, int | None]:
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
            break
    return rows


def enumerate_listings(timeout: float = 8.0,
                       verbose: bool = True) -> list[tuple[int, Row]] | None:
    def say(message: str) -> None:
        if verbose:
            print(message)

    rows = scroll_to_end(up=True, timeout=timeout, verbose=verbose)
    if not rows:
        return None

    found: list[tuple[int, Row]] = [(i + 1, r) for i, r in enumerate(rows)]
    top = 1
    steps = 0
    while steps < SCROLL_TO_END_NOTCHES:
        steps += 1
        after, shift = scroll_one(True, rows, timeout=timeout, verbose=verbose)
        if after is None or shift is None:
            return None
        if shift == 0:
            break
        top += shift
        rows = after
        index = top + len(rows) - 1
        if index > len(found):
            found.append((index, rows[-1]))
    else:
        say(f"  still scrolling after {steps} steps - refusing to continue.")
        return None

    say(f"  {len(found)} listing(s) in the shop "
        f"({len(found) - EXPECTED_ROWS} beyond the first screen)")
    return found


def locate_row(rows: list[Row], ref: RowRef,
               strict: bool = False) -> tuple[Row | None, str]:
    same = match_rows(rows, ref.name)
    if not same:
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

    chosen = pool[ref.ordinal] if 0 <= ref.ordinal < len(pool) else pool[0]
    priced = f"at {ref.price:,} Alz" if ref.price is not None else "price unread"
    return chosen, (f"{len(pool)} rows are identical ({ref.name!r} x{ref.qty} "
                    f"{priced}); taking row {chosen.index} by position")



SLOT_ONE_OFFSET = (-261, 120)
SLOT_PITCH = (73.9, 74.1)
GRID_SIZE = 8

REGISTER_PANEL = (10, 120, 275, 1040)
PRICE_ROWS = (70, 460, 260, 530)
PRICE_FIELD = (40, 545, 204, 573)
QTY_FIELD = (40, 634, 226, 667)
NET_SALES_ROWS = (30, 700, 265, 800)
SHOP_SLOT = (144, 290)
SHOP_SLOT_BOX = (30, 179, 256, 399)
SHOP_SLOT_STDEV = 20.0
QTY_INPUT = (90, 651)
QTY_MIN_CONF = 15.0
LOAD_ATTEMPTS = 3


def wants_max_quantity(name: str) -> bool:
    lowered = name.casefold()
    if any(token in lowered for token in NO_MAX_QUANTITY_ITEMS):
        return False
    return MAXIMISE_ALL_QUANTITIES
PRICE_TOP_Y = 477
PRICE_BOTTOM_Y = 513
PRICE_ROW_Y_TOL = 14

FLOOR_NAME_SIMILARITY = 0.75
FLOOR_TOKEN_MIN_SIMILARITY = 0.40
FLOOR_LENGTH_RATIO = 0.0

ESCAPE_ATTEMPTS = 3

RELISTED = "relisted"
SOLD_OUT = "sold_out"
FAILED = "failed"


def choose_price(
    suggested: int,
    price_floor: int = 0,
    floor_price: int | None = None,
    absolute_floor: int = 0,
) -> tuple[int, str]:
    if suggested <= 0:
        return max(FALLBACK_PRICE, absolute_floor), \
            "no market price; using the fallback"

    if price_floor and suggested < price_floor:
        raise Aborted(
            f"suggested {suggested:,} is below the --floor {price_floor:,}"
        )

    if absolute_floor and suggested < absolute_floor:
        return absolute_floor, (
            f"market {suggested:,} is below the {absolute_floor:,} floor for "
            "this item; listing at the floor"
        )

    return suggested, ""


def item_price_floor(name: str) -> int:
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
        long_enough = len(key) >= len(reference) * FLOOR_LENGTH_RATIO
        if (ratio >= FLOOR_NAME_SIMILARITY and long_enough) or (
                token_hit and ratio >= FLOOR_TOKEN_MIN_SIMILARITY):
            best = max(best, floor)
    return best


def strictest_price_floor() -> int:
    return max((floor for *_, floor in ITEM_PRICE_FLOORS), default=0)
PANEL_RADIO_X = 39


INVENTORY_TITLE_REGION = (1400, 100, 2560, 300)
ALZ_TO_TITLE = (-241, -718)


def inventory_origin(
    source: Image.Image | None = None, retries: int = 3
) -> tuple[int, int] | None:
    for _ in range(retries):
        image = source if source is not None else grab()

        box = find_alz(image)
        if box:
            return (box[2] + ALZ_TO_TITLE[0], box[1] + ALZ_TO_TITLE[1])

        centre = find_phrase(image, "Inventory", INVENTORY_TITLE_REGION)
        if centre is not None:
            return centre

        if source is not None:
            break
        time.sleep(0.4)
    return None


def slot_centre_at(origin: tuple[int, int], row: int, col: int) -> tuple[int, int]:
    if not (1 <= row <= GRID_SIZE and 1 <= col <= GRID_SIZE):
        raise ValueError(f"slot ({row},{col}) is outside the {GRID_SIZE}x{GRID_SIZE} grid")
    tx, ty = origin
    return (
        round(tx + SLOT_ONE_OFFSET[0] + SLOT_PITCH[0] * (col - 1)),
        round(ty + SLOT_ONE_OFFSET[1] + SLOT_PITCH[1] * (row - 1)),
    )


def slot_centre(row: int, col: int, source: Image.Image | None = None) -> tuple[int, int]:
    origin = inventory_origin(source)
    if origin is None:
        raise Aborted("could not find the Inventory panel - is it open?")
    return slot_centre_at(origin, row, col)


TAB_ONE_OFFSET = (-281, 52)
TAB_PITCH = 69.2
TAB_COUNT = 8
TAB_ACTIVE_MARGIN = 6.0
SLOT_OCCUPIED_STDEV = 8.0

SLOT_INSET = 26
SLOT_CHANGE_MIN = 6.0
SLOT_CHANGE_MARGIN = 2.0


def inventory_cells(
    image: Image.Image, origin: tuple[int, int]
) -> dict[tuple[int, int], Image.Image]:
    cells = {}
    for r in range(1, GRID_SIZE + 1):
        for c in range(1, GRID_SIZE + 1):
            cx, cy = slot_centre_at(origin, r, c)
            box = (max(0, cx - SLOT_INSET), max(0, cy - SLOT_INSET),
                   min(image.width, cx + SLOT_INSET),
                   min(image.height, cy + SLOT_INSET))
            cells[(r, c)] = image.crop(box).convert("L")
    return cells


def occupied_slots(
    image: Image.Image, origin: tuple[int, int]
) -> list[tuple[int, int]]:
    found = []
    for key, cell in inventory_cells(image, origin).items():
        data = list(getattr(cell, "get_flattened_data", cell.getdata)())
        mean = sum(data) / len(data)
        stdev = (sum((p - mean) ** 2 for p in data) / len(data)) ** 0.5
        if stdev >= SLOT_OCCUPIED_STDEV:
            found.append(key)
    return sorted(found)


def require_empty_work_tab(verbose: bool = True) -> bool:
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
    if origin is None:
        origin = inventory_origin(before) or inventory_origin(after) or inventory_origin()
    if origin is None:
        return []

    old, new = inventory_cells(before, origin), inventory_cells(after, origin)
    changed: list[tuple[int, int]] = []
    for key, cell in old.items():
        diff = ImageChops.difference(cell, new[key])
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
    slots = changed_slots(before, after, origin)
    return slots[0] if slots else None


def tab_centre(origin: tuple[int, int], tab: int) -> tuple[int, int]:
    if not 1 <= tab <= TAB_COUNT:
        raise ValueError(f"tab {tab} is outside I..{TAB_COUNT}")
    return (round(origin[0] + TAB_ONE_OFFSET[0] + TAB_PITCH * (tab - 1)),
            round(origin[1] + TAB_ONE_OFFSET[1]))


def active_inventory_tab(
    source: Image.Image | None = None, origin: tuple[int, int] | None = None
) -> int | None:
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

    values = sorted(v for v, _ in brightness)
    median = (values[TAB_COUNT // 2 - 1] + values[TAB_COUNT // 2]) / 2
    best = max(brightness)
    return best[1] if best[0] - median >= TAB_ACTIVE_MARGIN else None


def select_inventory_tab(
    tab: int, origin: tuple[int, int] | None = None, timeout: float = 5.0
) -> bool:
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




def _digits(text: str) -> int | None:
    cleaned = re.sub(r"[^0-9]", "", text)
    return int(cleaned) if cleaned else None


PRICE_TEXT_MAX_X = 230
PRICE_MIN_CONF = 15.0


def _price_value(text: str) -> int | None:
    cleaned = re.sub(r"a[il1]z", "", text, flags=re.IGNORECASE)
    digits = re.sub(r"[^0-9]", "", cleaned)
    if digits:
        return int(digits)
    if re.fullmatch(r"[Oo0°©()\s.,]*", cleaned) and \
            re.search(r"[Oo0°©()]", cleaned):
        return 0
    return None


def read_register_panel(source: Image.Image | Path | str) -> dict:
    image = source if isinstance(source, Image.Image) else Image.open(source)

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
    numbers = [_digits(chunk) for chunk in re.findall(r"\d[\d,]*", qty_text)]
    numbers = [n for n in numbers if n is not None]
    if numbers:
        qty = numbers[0]
        if len(numbers) > 1:
            qty_max = numbers[-1]

    net_cell = sorted(find_words(image, NET_SALES_ROWS), key=lambda w: w.left)
    net = _digits("".join(w.text for w in net_cell)) or 0

    box = image.crop(SHOP_SLOT_BOX).convert("L")
    pixels = list(getattr(box, "get_flattened_data", box.getdata)())
    mean = sum(pixels) / len(pixels)
    stdev = (sum((p - mean) ** 2 for p in pixels) / len(pixels)) ** 0.5
    loaded = stdev >= SHOP_SLOT_STDEV or bool(qty) or net > 0

    return {"prices": [p for p, _ in prices], "price_rows": prices, "typed": typed,
            "qty": qty, "qty_max": qty_max, "qty_text": qty_text,
            "net_sales": net, "loaded": loaded, "slot_stdev": round(stdev, 1)}


def await_dialog(kind: str | None, timeout: float = 8.0, poll: float = 0.35):
    deadline = time.monotonic() + timeout
    while True:
        shot = grab()
        if dialog_kind(shot) == kind:
            return shot
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


class Aborted(Exception):
    pass


class FatalAbort(Exception):
    pass


def cancel_item(
    row: int,
    dry_run: bool = False,
    timeout: float = 8.0,
    verbose: bool = True,
    expect: "RowRef | None" = None,
) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise Aborted(reason)

    committed = False
    try:
        if not dry_run:
            require(not session_locked(),
                    "the workstation is locked - screen capture is blank and "
                    "input goes to the secure desktop")
            require(focus_game(), "could not bring Cabal to the foreground")
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

        if expect is not None:
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

        record("cancel.before_change", row=row, name=target.name,
               price=target.price, qty=target.qty)
        click(*target.change)
        shot = await_dialog("extension", timeout)
        record("cancel.after_change", shot, row=row, name=target.name,
               dialog="extension" if shot else "none")
        if shot is None:
            probe = grab()
            say(f"  dialog_kind sees: {dialog_kind(probe)!r}")
            say(f"  trade window still open: {trade_window_open(probe)}")
            words = sorted(find_words(probe, POPUP_REGION, 25),
                           key=lambda w: -w.conf)[:12]
            say("  strongest words in the dialog area: "
                + ", ".join(f"{w.text!r}@{w.conf:.0f}" for w in words))
        require(shot is not None, "the Registration Extension dialog did not appear")

        cancel = await_dialog_button(DISMISS_WORD, timeout)
        require(cancel is not None,
                "no Cancel button on the Registration Extension dialog")

        say(f"{DISMISS_WORD} button at {cancel.centre} (conf {cancel.conf:.0f})")
        click(*cancel.centre)
        shot = await_dialog("confirm", timeout)
        require(shot is not None, "the confirmation dialog did not appear")

        confirm = await_dialog_button(CONFIRM_WORD, timeout)
        require(confirm is not None,
                "no Confirmation button on the confirmation dialog")

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
        still = dialog_kind(grab()) if committed else None
        record("cancel.aborted", reason=str(exc), row=row, committed=committed,
               dialog_after=still,
               accepted=None if not committed else (still != "confirm"))
        say(f"ABORTED: {exc}.")
        if committed:
            if still == "confirm":
                say("A confirmation dialog is still open. USUALLY that "
                    "means the game refused the cancellation and the "
                    "listing is untouched - but the game can also stack "
                    "dialogs after accepting one, so this is not proof.")
                say("CHECK THE LISTING before retrying: if it is gone, the "
                    f"stack is in inventory tab {WORK_TAB}, unlisted.")
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
        if dry_run:
            say("[dry run] leaving the screen exactly as it is.")
            return False
        if dialog_kind(grab()) is None:
            say("Nothing was changed.")
        elif close_any_dialog():
            say("Backed out of the open dialog; nothing was changed.")
        else:
            say("WARNING: could not close the dialog still on screen - "
                "dismiss it manually before rerunning.")
        return False


def clear_shop_slot(timeout: float = 15.0, verbose: bool = True) -> bool:
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
    maximise_qty: bool = False,
    force_price: int | None = None,
    force_qty: int | None = None,
    expect_item: str | None = None,
    expect_qty: int | None = None,
    report: dict | None = None,
) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise Aborted(reason)

    committed = False
    try:
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

        if expect_item and expect_qty is not None and panel["qty_max"] is not None:
            loaded = panel["qty_max"]
            slack = max(QTY_CROSSCHECK_ABSOLUTE,
                        int(expect_qty * QTY_CROSSCHECK_FRACTION))
            if loaded != expect_qty and abs(loaded - expect_qty) <= slack:
                say(f"NOTE: the panel holds {loaded} but the table said "
                    f"{expect_qty} (within {slack}). The panel field is the "
                    "more reliable read, so continuing with it - the table's "
                    "QTY column is narrow and misreads a digit occasionally.")
                if report is not None:
                    report["qty_disagreement"] = (expect_qty, loaded)
            else:
                require(loaded == expect_qty,
                        f"loaded {loaded} of an item but the cancelled listing "
                        f"held {expect_qty} - off by {abs(loaded - expect_qty)}, "
                        f"more than the {slack} tolerated, so this is probably "
                        "not the same item")

        entry = force_qty if force_qty else (MAX_QTY_ENTRY if maximise_qty else None)
        if entry is not None:
            record("qty.before_typing", entry=entry, item=expect_item)
            say(f"Setting quantity: typing {entry}"
                + ("" if force_qty else " - the game clamps it to the stack maximum"))
            click(*QTY_INPUT)
            type_number(entry, clear=len(str(MAX_QTY_ENTRY)) + 2)
            time.sleep(0.4)
            park_cursor()

        rows_seen = panel["price_rows"]

        if expect_item:
            absolute_floor = item_price_floor(expect_item)
        else:
            absolute_floor = 0
            require(force_price is not None,
                    "cannot price an item the script cannot name, because its "
                    "price floor cannot be looked up. Use --relist, which "
                    "reads the name off the listing, or pass --price to state "
                    "the price yourself")
            strictest = strictest_price_floor()
            require(not strictest or force_price >= strictest,
                    f"--price {force_price:,} is below the strictest floor on "
                    f"the books ({strictest:,}) and the item cannot be named "
                    f"here, so it might be one the floor protects. Use "
                    f"--relist, which reads the name off the listing")
        if absolute_floor:
            say(f"Absolute floor for this item: {absolute_floor:,} Alz")

        if force_price is not None:
            require(not (expect_item and absolute_floor
                         and force_price < absolute_floor),
                    f"--price {force_price:,} is below the {absolute_floor:,} "
                    f"floor for {expect_item!r}")
            suggested, price_y = force_price, None
            price, why = force_price, "forced by --price"
            say(f"Price forced to {force_price:,} Alz")
        else:
            require(bool(rows_seen), "no suggested-price rows could be read")

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

            price, why = choose_price(suggested, price_floor, floor_price,
                                      absolute_floor)
            if why and floor_reason:
                why = f"{why} ({floor_reason})"

            if (floor_price and suggested > 0
                    and suggested < floor_price * SUSPECT_PRICE_FRACTION):
                say(f"NOTE: market {suggested:,} is only "
                    f"{suggested / floor_price:.1%} of the previous "
                    f"{floor_price:,} - listing at the market price anyway.")

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

        require(panel["net_sales"] % price == 0,
                f"net sales {panel['net_sales']:,} is not a whole multiple of the "
                f"{price:,} price that was set - the price did not take correctly")
        qty = panel["net_sales"] // price
        say(f"Net sales {panel['net_sales']:,} Alz = {price:,} x {qty}"
            f"  (field reads {panel['qty_text']!r})")

        shot = grab()
        buttons = find_text(shot, "Register", REGISTER_PANEL)
        require(bool(buttons), "could not find the Register button")
        button = buttons[-1]
        record("register.priced", shot, price=price, qty=panel.get("qty"),
               net_sales=panel.get("net_sales"), item=expect_item)
        say(f"Register button at {button.centre} (conf {button.conf:.0f})")

        click(*button.centre)
        shot = await_dialog("confirm", timeout)
        require(shot is not None, "no confirmation dialog appeared after Register")

        for step in range(1, MAX_CONFIRM_STEPS + 1):
            if dialog_kind(grab()) is None:
                break
            confirm = await_dialog_button(CONFIRM_WORD, timeout=4.0)
            if confirm is None:
                break
            say(f"{CONFIRM_WORD} {step} at {confirm.centre} "
                f"(conf {confirm.conf:.0f})")
            click(*confirm.centre)
            committed = True
            if report is not None:
                report["committed"] = True
            time.sleep(0.8)

        if committed and report is not None:
            report["price"] = price
            report["qty"] = qty
            report["total"] = panel["net_sales"]
            report["committed"] = True

        require(await_dialog(None, timeout) is not None,
                f"a confirmation dialog is still open after {MAX_CONFIRM_STEPS} steps")

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
    def say(message: str) -> None:
        if verbose:
            print(message)

    def close_shop() -> None:
        if dry_run:
            return
        try:
            for _ in range(ESCAPE_ATTEMPTS):
                if not trade_window_open():
                    return
                press_escape()
            if trade_window_open():
                say("Note: the Trade window would not close with Escape.")
        except Exception as exc:
            say(f"Note: could not close the Trade window ({exc}).")

    try:
        return _relist_cycle(row, inv_row, inv_col, dry_run, timeout,
                             verbose, attempts, say, expect)
    finally:
        close_shop()


def _relist_cycle(row, inv_row, inv_col, dry_run, timeout, verbose, attempts, say,
                  expect=None):
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            say(f"\n=== relist attempt {attempt}/{attempts} ===")

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

        rows = await_rows(timeout)
        if not 1 <= row <= len(rows):
            say(f"Row {row} is out of range; {len(rows)} row(s) visible.")
            return FAILED

        target = rows[row - 1]

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

        record("table.target", row=row, name=target.name, action=target.action,
               price=target.price, qty=target.qty, visible=len(rows),
               attempt=attempt,
               table=[[r.index, r.name, r.action, r.price, r.qty] for r in rows])

        if target.action == "receive":
            say(f"Row {row} is sold ({target.name!r}) - clicking Receive.")
            if dry_run:
                say("[dry run] would click Receive, then relist any remainder")
                return RELISTED
            if attempt == attempts:
                say(f"Still sold on the final attempt ({attempts}) - stopping.")
                return FAILED

            click(*target.change)

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

            def family(table: list[Row]) -> list[Row]:
                pool = [r for r in match_rows(table, target.name)
                        if r.action in ("change", "receive")]
                if target.price is not None:
                    priced = [r for r in pool if r.price == target.price]
                    if priced:
                        pool = priced
                return pool

            def quantities(pool: list[Row]) -> list:
                return sorted((r.qty for r in pool),
                              key=lambda q: (q is None, q))

            before = quantities(family(rows))
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

            unmatched = list(before)
            gained = []
            for value in after:
                if value in unmatched:
                    unmatched.remove(value)
                else:
                    gained.append(value)
            lost = unmatched

            if not lost and not gained:
                say(f"Row {row} still shows Receive and the table is unchanged "
                    f"- the click did not take; retrying.")
                continue

            if len(lost) == 1 and not gained:
                say(f"{target.name!r} is no longer in the table - fully sold "
                    "and collected.")
                return SOLD_OUT

            if len(lost) == 1 and len(gained) == 1:
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
        if original < MIN_PLAUSIBLE_PRICE:
            say(f"Row {row} ({target.name!r}) priced at {original:,} Alz, below "
                f"the {MIN_PLAUSIBLE_PRICE:,} plausibility floor - the price "
                "column was probably misread. Refusing to relist it.")
            return FAILED

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

        before = origin = start_tab = None
        if not dry_run and (inv_row is None or inv_col is None):
            focus_game()
            park_cursor()
            before = grab()
            origin = inventory_origin(before) or inventory_origin()
            if origin is None:
                say("The Inventory panel is not visible, so the returned item "
                    "could not be followed. Open it (or pass an explicit slot) "
                    "and rerun. Nothing has been cancelled yet.")
                return FAILED
            start_tab = active_inventory_tab(before, origin)
            if start_tab is None:
                say("Could not tell which inventory tab is open, so the "
                    "returned items could not be followed reliably. "
                    "Nothing has been cancelled yet.")
                return FAILED
            if start_tab != WORK_TAB:
                say(f"Inventory tab {start_tab} is open, but the work tab is "
                    f"{WORK_TAB} - the emptiness check and the returned-item "
                    "diff would be looking at different tabs. Nothing has been "
                    "cancelled yet.")
                return FAILED
            record("inventory.before_cancel", tab=start_tab, origin=str(origin))
            say(f"Inventory tab {start_tab} is open; will return to it after "
                "cancelling.")

        if not cancel_item(row, dry_run=dry_run, timeout=timeout, verbose=verbose,
                           expect=RowRef.of(target, rows)):
            say("Cancel did not complete - see above for what state it left. "
                "Nothing further will be listed this cycle.")
            return FAILED

        if not dry_run and not wait_for_table(max(timeout, 20.0)):
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
                if not select_inventory_tab(start_tab, origin):
                    say(f"Could not return to inventory tab {start_tab}.\n"
                        f"IMPORTANT: row {row} has already been cancelled - "
                        f"{target.name!r} is in your inventory, unlisted.")
                    return FAILED
                park_cursor()

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
        listed = register_item(*slot, dry_run=dry_run,
                               timeout=timeout, verbose=verbose,
                               floor_price=original, maximise_qty=max_qty,
                               expect_item=target.name, expect_qty=target.qty,
                               report=report)
        if not listed and not report.get("committed"):
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
            say("The listing was committed before the failure, so it is on the "
                "market. Verifying it against the table rather than assuming.")

        found: dict = {}
        if not sanity_check(target.name, report.get("price"), report.get("qty"),
                            timeout=timeout, verbose=verbose, found=found):
            bad = found.get("row")
            if bad is None:
                say(f"Could not verify the listing for {target.name!r}. "
                    "Nothing was withdrawn; will be checked again next cycle.")
                return FAILED
            def money(value: int | None) -> str:
                return f"{value:,}" if isinstance(value, int) else "an unreadable price"

            say(f"Withdrawing the mismatched listing on row {bad.index} "
                f"({bad.name!r})...")
            try:
                withdrawn = cancel_item(bad.index, expect=RowRef.of(bad, [bad]),
                                        timeout=timeout, verbose=verbose)
            except FatalAbort:
                raise
            except Exception as exc:
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

    ref = RowRef(name, qty, price)
    changeable = [r for r in rows if r.action == "change"]

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

    if price is not None and listed.price is None:
        say("  the listed price could not be read, so this cannot be verified.")
        return False
    if qty is not None and listed.qty is None:
        say("  the listed quantity could not be read; checking the price only.")

    if qty is not None and listed.qty is not None and listed.qty != qty:
        say(f"  note: quantity reads {listed.qty}, expected {qty} - not acting "
            "on that; the quantity column is not reliable enough to withdraw on.")

    problems = []
    if price is not None and listed.price is not None and listed.price != price:
        problems.append(f"price is {listed.price:,}, expected {price:,}")

    if problems:
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
    def say(message: str) -> None:
        if verbose:
            print(message)

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

        if not dry_run and not ensure_shop_ready(verbose=verbose):
            say("Could not reopen the Agent Shop - stopping.")
            return False
        live = (bring_into_view(ref, timeout=timeout, verbose=verbose)
                if scrolling else await_rows(timeout))
        if not live:
            say("The listings could not be read - stopping rather than "
                "treating an unreadable table as an empty shop.")
            return False
        current = [r for r in live if r.action in ("change", "receive")]
        match, note = locate_row(current, ref)
        if match is None and note == "unmatched":
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

        outcome = relist(match.index, dry_run=dry_run, timeout=timeout,
                         verbose=verbose, expect=ref)
        if outcome == SOLD_OUT:
            worked += 1
            say(f"{name!r} sold out - collected, nothing to relist. Moving on.")
            continue
        if outcome != RELISTED:
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
            left = len(targets) - position
            if not left:
                break
            if require_empty_work_tab(verbose=False):
                say(f"The table did not finish refreshing after {name!r}, but "
                    f"the relist completed and inventory tab {WORK_TAB} is "
                    f"clean - continuing with {left} row(s) still to go.")
                continue
            say(f"The table did not finish refreshing after {name!r} AND "
                f"inventory tab {WORK_TAB} is not clean - stopping.")
            return False

    actionable = sum(1 for _, _, action in targets
                     if action in ("change", "receive"))
    if actionable and not worked:
        say(f"\nNone of the {actionable} live row(s) were relisted - every one "
            "read as already sold. That is not a successful cycle; stopping so "
            "it is not reported as one.")
        return False

    if failed_rows:
        say(f"\n{len(failed_rows)} row(s) failed and were skipped: "
            + ", ".join(repr(n) for n in failed_rows))
        say("Each was confined to its own row - the work tab stayed clean, so "
            "the rest of the batch continued. They will be retried next cycle.")
    say(f"\nAll {len(targets)} row(s) processed"
        + (f" ({worked} relisted" if worked else " (none relisted")
        + (f", {len(failed_rows)} failed)." if failed_rows else ")."))
    return True


def run_sequence(actions: list[str], dry_run: bool = False, verbose: bool = True) -> bool:
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
                forced = int(args[2]) if len(args) == 3 else None
                ok = register_item(int(args[0]), int(args[1]),
                                   dry_run=dry_run, verbose=verbose,
                                   force_price=forced)
            elif verb == "relist" and len(args) in (1, 3):
                slot = (int(args[1]), int(args[2])) if len(args) == 3 else (None, None)
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
            say(f"Stopped: {exc}")
            return False

        if not ok:
            say(f"Action {position} failed - stopping; "
                f"{len(actions) - position} action(s) not attempted.")
            return False

        if not dry_run and not wait_for_table():
            say("The table did not finish refreshing - stopping.")
            return False

    say(f"\nAll {len(actions)} action(s) completed.")
    return True


def prepare_for_actions(verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not focus_game():
        say("Could not bring Cabal to the foreground.")
        return False

    release_modifiers()
    park_cursor()

    for attempt in range(ESCAPE_ATTEMPTS):
        if dialog_kind(grab()) is None:
            break
        say(f"Dialog still open - pressing Escape ({attempt + 1}).")
        press_escape()
    else:
        if not close_any_dialog():
            say("Could not close a dialog left open on screen.")
            return False

    if not open_trade_window(verbose=verbose):
        say("Could not open the Trade window on the Register tab.")
        return False

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
            record("cycle.start", cycle=cycle, consecutive=consecutive,
                   succeeded=succeeded, failures=failures)

            if session_locked():
                say("The workstation is locked - screen capture and input are "
                    "unavailable. Terminating the loop.")
                stopped = True
                break

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
                say(f"\nFATAL: {exc}")
                say("Terminating the loop; nothing further will be attempted.")
                failures += 1
                stopped = True
                break
            except PermissionError as exc:
                record("loop.stopped", reason="permission", detail=str(exc),
                       cycle=cycle, consecutive=consecutive)
                say(f"\nInput was refused: {exc}")
                say("Terminating the loop; nothing further can be clicked.")
                failures += 1
                stopped = True
                break
            except Exception as exc:
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

            record("cycle.end", cycle=cycle, consecutive=consecutive,
                   succeeded=succeeded, failures=failures)
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
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
    "POPUP_REGION": "box",
    "NPC_SEARCH_REGION": "box",
    "TRADE_WINDOW_SEARCH": "box",
    "NPC_EXCLUDE_ZONES": "boxes",
}

REF_CLIENT = (0, 23, 2560, 1392)
_CLIENT_FRAME_GEOMETRY = ("ALZ_REGION", "INVENTORY_TITLE_REGION")

_INVENTORY_FRAME_GEOMETRY = {
    "SLOT_PITCH": "lenpair", "SLOT_ONE_OFFSET": "lenpair",
    "TAB_ONE_OFFSET": "lenpair", "ALZ_TO_TITLE": "lenpair",
    "TAB_PITCH": "len", "SLOT_INSET": "len",
}

_REFERENCE_GEOMETRY: dict[str, object] = {}


def _capture_reference_geometry() -> None:
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
        _REFERENCE_GEOMETRY[name] = value

    for name in _INVENTORY_FRAME_GEOMETRY:
        _REFERENCE_GEOMETRY[name] = globals()[name]

    cl, ct, cr, _cb = REF_CLIENT
    for name in _CLIENT_FRAME_GEOMETRY:
        left, top, right, bottom = globals()[name]
        _REFERENCE_GEOMETRY[name] = (cr - left, top - ct, cr - right, bottom - ct)


def apply_layout(layout: "Layout") -> None:
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
        else:
            value = layout.length(ref)
        if kind == "box":
            value = _clamp_box(value, layout.screen)
        globals()[name] = value

    for name, kind in _INVENTORY_FRAME_GEOMETRY.items():
        ref = _REFERENCE_GEOMETRY[name]
        if kind == "lenpair":
            value = (ref[0] * layout.scale, ref[1] * layout.scale)
            if all(float(v).is_integer() for v in ref):
                value = (int(round(value[0])), int(round(value[1])))
        else:
            value = ref * layout.scale
            if float(ref).is_integer():
                value = int(round(value))
        globals()[name] = value

    client = layout.client or (0, 0, *layout.screen)
    cl, ct, cr, cb = client
    for name in _CLIENT_FRAME_GEOMETRY:
        left_in, top_in, right_in, bottom_in = _REFERENCE_GEOMETRY[name]
        globals()[name] = _clamp_box(
            (cr - layout.length(left_in), ct + layout.length(top_in),
             cr - layout.length(right_in), ct + layout.length(bottom_in)),
            layout.screen)

    globals()["NPC_BODY_OFFSET"] = (
        int(round(_REFERENCE_NPC_BODY_OFFSET[0] * layout.scale)),
        int(round(_REFERENCE_NPC_BODY_OFFSET[1] * layout.scale)))
    globals()["NPC_CLICK_OFFSETS"] = _npc_click_offsets()


_REFERENCE_NPC_BODY_OFFSET = NPC_BODY_OFFSET

def client_rect() -> tuple[int, int, int, int] | None:
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
    except Exception:
        return None


def _anchor_centre(phrase: str, words: list, lines: list):
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

    if len(hits) > 1:
        return None
    if hits:
        return hits[0].centre
    if len(needle) >= 5:
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
            return (int(round(cx - glyph / 2 if lost_first else cx + glyph / 2)),
                    cy)
    return None


def measure_layout(image: Image.Image | None = None,
                   verbose: bool = True) -> "Layout | None":
    def say(message: str) -> None:
        if verbose:
            print(message)

    shot = image if image is not None else grab()
    screen = shot.size
    client = client_rect()
    search = (0, 0, screen[0], screen[1])

    def collect(scale_override=None):
        got = find_words(shot, search, 40.0, scale=scale_override) \
            if scale_override else find_words(shot, search, 40.0)
        return got, _text_lines(got)

    words, lines = collect()
    attempts = [(words, lines)]
    hits = sum(1 for p, _ in REF_ANCHORS
               if _anchor_centre(p, words, lines) is not None)

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

    if len(found) < 3:
        say(f"\nCalibration could not measure the Trade window.")
        say(f"  display captured   {screen[0]}x{screen[1]}  (primary display only)")
        if client:
            say(f"  game client area   ({client[0]},{client[1]})-({client[2]},{client[3]})")
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

    def fit(anchors):
        refs = [r for _, _, r in anchors]
        obs = [m for _, m, _ in anchors]
        span = max(math.hypot(a[0] - b[0], a[1] - b[1])
                   for a in refs for b in refs)
        if span < MIN_ANCHOR_BASELINE:
            return None, (f"the anchors found span only {span:.0f}px in the "
                          "reference frame - too close together to measure a "
                          "scale from.")

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

    while True:
        data, reason = fit(found)

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
            say("\nThe anchors disagree about where the Trade window is.")
            say(f"\n  fitted from {len(found)} anchors: origin ({ox:.0f},{oy:.0f}), "
                f"scale {scale:.3f}, span {span:.0f}px")
            say(f"  tolerance {allowed:.0f}px\n")
            by_ref = {name: r for name, _, r in found}
            for name, residual in sorted(residuals, key=lambda p: p[1]):
                where = next(m_ for n_, m_, _ in found if n_ == name)
                say(f"    {name!r:12} off by {residual:7.1f}px   found at {where}")
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
    boundary = layout.x(REF_DIALOG_BUTTON_MIN_X - REF_TRADE_ORIGIN[0])
    if not layout.x(REF_FUNCTION_COLUMN_X) < boundary < layout.screen[0]:
        say(f"  the dialog-button boundary ({boundary}) does not sit between "
            f"the Function column and the right edge of the screen.")
        return False
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
    try:
        data = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
        layout = Layout(screen=tuple(data["screen"]), origin=tuple(data["origin"]),
                        scale=float(data["scale"]),
                        client=tuple(data["client"]) if data.get("client") else None,
                        measured_from=data.get("measured_from", "stored"))
    except (OSError, ValueError, KeyError, TypeError):
        return None

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
    try:
        make_dpi_aware()
        with mss.mss() as sct:
            region, _ = resolve_monitor(sct, "primary")
            return (int(region["width"]), int(region["height"]))
    except Exception:
        return None


def calibrate(verbose: bool = True, save: bool = True) -> bool:
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
    if calibrate(verbose=verbose):
        return True

    screen = current_screen_size()
    client = client_rect()
    if (screen and tuple(screen) == REF_SCREEN
            and client and tuple(client) == REF_CLIENT):
        if verbose:
            print("Could not calibrate, but this screen and game window match "
                  "the reference the coordinates were measured on, so the "
                  "built-in values are used.")
        return True
    if not required:
        return False
    if not verbose:
        return False

    print("\nRefusing to click without a calibration.")
    if not screen:
        print("The screen size could not be determined.")
        return False

    if tuple(screen) == REF_SCREEN and client and tuple(client) != REF_CLIENT:
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




LOG_DIR = SCRIPT_DIR / "logs"
_log_handle = None
_RUN_STARTED = time.monotonic()
_RUN_STARTED_AT = datetime.now()
_run_finished = False


class _Tee:

    def __init__(self, stream, handle):
        self._stream = stream
        self._handle = handle

    def write(self, text):
        self._stream.write(text)
        try:
            self._stream.flush()
        except Exception:
            pass
        try:
            self._handle.write(text)
            self._handle.flush()
        except Exception:
            pass
        return len(text)

    def flush(self):
        for target in (self._stream, self._handle):
            try:
                target.flush()
            except Exception:
                pass

    def isatty(self):
        try:
            return self._stream.isatty()
        except Exception:
            return False

    def __getattr__(self, name):
        return getattr(self._stream, name)


def start_run_log(argv: list[str]) -> "Path | None":
    global _log_handle
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        started = datetime.now()
        path = LOG_DIR / f"run_{started:%Y-%m-%d_%H%M%S}.log"
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
            try:
                text = "".join(traceback.format_exception(kind, value, tb))
                _log_handle.write(f"\n=== UNCAUGHT {kind.__name__} ===\n{text}")
                _log_handle.write(f"ended     {datetime.now().isoformat(timespec='seconds')}\n")
                _log_handle.flush()
            except Exception:
                pass
            sys.__excepthook__(kind, value, tb)

        sys.excepthook = log_uncaught
        return path
    except Exception:
        return None


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def finish_run_log(note: str = "") -> None:
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
        print(f"\n{line}")
        if _log_handle is not None:
            _log_handle.write(f"{'=' * 60}\n"
                              f"ended     {ended.isoformat(timespec='seconds')}\n")
            _log_handle.flush()
    except Exception:
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
    p.add_argument("--floor", type=int, default=None, metavar="ALZ",
                   help="with --register, abort if the suggested price is below this")
    p.add_argument("--price", type=int, default=None, metavar="ALZ",
                   help="with --register, list at exactly this price")
    p.add_argument("--qty", type=int, default=None, metavar="N",
                   help="with --register, list exactly this quantity")
    p.add_argument("--dry-run", action="store_true",
                   help="locate everything but do not click")
    args = p.parse_args()

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

    if args.repeat:
        if args.duration is None or args.every is None:
            p.error("--repeat needs both --for MINUTES and --every MINUTES")
        if args.duration <= 0:
            p.error("--for must be positive")
        if args.every < 0:
            p.error("--every cannot be negative; use --every 0 to start each "
                    "cycle as soon as the last one finishes")
        if args.every and args.every > args.duration:
            p.error(f"--every {args.every:g} is longer than --for {args.duration:g}, "
                    "so the actions would run once at most")

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

    global RECORD_ENABLED
    RECORD_ENABLED = (args.record or clicking) and not args.no_record
    if RECORD_ENABLED:
        print(f"Recording frames to {RECORD_DIR} (--no-record to turn off).")

    if args.calibrate:
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        sys.exit(0 if calibrate() else 1)

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

        def key(r):
            return (r.name, r.price, r.qty)
        b = [key(r) for r in rows_before]
        a = [key(r) for r in rows_after]
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
                               maximise_qty=args.max_qty,
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
    atexit.register(finish_run_log)

    _log_path = start_run_log(sys.argv)
    if _log_path:
        print(f"Logging this run to {_log_path}")
    try:
        main()
    except SystemExit as exc:
        finish_run_log(f"exit {exc.code}")
        raise
    except BaseException as exc:
        finish_run_log(f"{type(exc).__name__}")
        raise
    else:
        finish_run_log()
