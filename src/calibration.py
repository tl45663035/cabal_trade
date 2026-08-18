import csv
import ctypes
import io
import json
import re
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE / "calibration.json"

_CACHE = None

DEFAULTS = {
    "timing": {
        "action_gap": 0.05,
        "key_hold": 0.02,
        "focus_settle": 0.35,
        "wheel_gap": 0.12,
        "park_settle": 0.25,
        "tab_settle": 0.6,
        "search_timeout": 8.0,
        "search_retries": 3,
        "dialog_timeout": 8.0,
        "retry_gap": 1.0,
    },
    "input": {
        "INPUT_MOUSE": 0,
        "INPUT_KEYBOARD": 1,
        "KEYEVENTF_KEYUP": 0x0002,
        "KEYEVENTF_SCANCODE": 0x0008,
        "MAPVK_VK_TO_VSC": 0,
        "MOUSEEVENTF_LEFTDOWN": 0x0002,
        "MOUSEEVENTF_LEFTUP": 0x0004,
        "MOUSEEVENTF_RIGHTDOWN": 0x0008,
        "MOUSEEVENTF_RIGHTUP": 0x0010,
        "MOUSEEVENTF_WHEEL": 0x0800,
        "WHEEL_DELTA": 120,
        "VK_I": 0x49,
        "VK_MENU": 0x12,
        "VK_ESCAPE": 0x1B,
        "INPUT_STRUCT_SIZE": 40,
        "SW_RESTORE": 9,
        "DWORD_MASK": 0xFFFFFFFF,
    },
    "game": {
        "title_hint": "PlayCabal",
    },
    "panel_layout": {
        "slot_one": [-496, -596],
        "slot_pitch": [73.3, 73.4],
        "tab_one": [-524, -668],
        "tab_pitch": 69.6,
    },
    "ocr": {
        "tesseract": r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        "scale": 3,
        "min_conf": 45.0,
        "psm": "11",
        "row_psm": "7",
        "digit_psm": "13",
        "digit_whitelist": "0123456789,",
        "timeout": 60,
    },
    "regions": {
        "park": [0.5078, 0.7137],
        "alz_search": [0.8750, 0.6245, 0.9805, 0.6567],
        "top_strip": [0.0000, 0.0197, 0.5078, 0.1585],
        "tab_band": [0.0000, 0.0270, 0.2734, 0.0709],
        "fav_band": [0.2422, 0.7100, 0.4648, 0.7465],
        "boundary_window": [0.0781, 0.1289],
        "slot_pitch": [0.0266, 0.0312],
        "purchase_sort_band": [0.2500, 0.1200, 0.5000, 0.1700],
        "purchase_buy_band": [0.3000, 0.6800, 0.5100, 0.7300],
        "purchase_table_band": [0.1000, 0.1500, 0.4800, 0.6600],
        "popup": [0.1953, 0.2389, 0.8203, 0.8232],
        "register_table_band": [0.1000, 0.1200, 0.4800, 0.6600],
    },
    "detect": {
        "alz_bright": 110,
        "alz_saturation": 45,
        "alz_min_pixels": 150,
        "alz_line_half": 14,
        "alz_max_width_fraction": 0.95,
        "alz_min_height": 8,
        "alz_max_height": 30,
        "grid_fit_min": 0.02,
        "panel_open_change": 0.30,
        "edge_candidates": 40,
        "edge_min_gap": 15,
        "rule_candidates": 60,
        "rule_min_gap": 30,
        "purchase_header_up": 66,
        "purchase_header_down": 10,
        "purchase_divider_sigma": 3.0,
        "purchase_cell_inset": 2,
        "dialog_button_min_x": 1200,
        "row_border_candidates": 30,
        "row_border_min_gap": 15,
        "qty_half_width": 45,
        "function_half_width": 46,
        "price_right_gap": 46,
        "ink_threshold": 160,
        "ink_pad": 4,
        "bulk_min_conf": 55.0,
        "rescue_min_conf": 30.0,
        "min_plausible_price": 1000,
        "price_min_digits": 4,
        "min_client_side": 100,
        "fit_pitch_step": 0.02,
        "fit_start_step": 0.5,
        "fav_peak_cut": 0.6,
        "fav_merge_gap": 40,
        "fav_pitch_spread": 4,
        "sort_pad_left": 40,
        "sort_pad_right": 90,
        "sort_pad_y": 16,
        "scroll_point_inset": 600,
        "panel_moved_slack": 30,
    },
    "text": {
        "empty_row": "premiumexclusiveslot",
        "sort_direction": r"price\s*:?\s*(low|high)",
        "purchase_row": r"^(?P<name>.*?)\s+(?P<qty>\d[\d,]*)\s+(?P<price>\d[\d,]*)\s*\D*$",
        "pack_marker": r"\bX\s*(\d+)\s*$",
        "change_word": "Change",
        "dismiss_word": "Cancel",
        "confirm_word": "Confirmation",
        "receipt_word": "Receive",
        "register_word": "Register",
        "status_complete": "Complete",
    },
    "favourite_items": {
        "1": "Force Core(Highest)",
        "2": "Force Core Set (Highest)",
        "3": "Chaos Core",
        "4": "Chaos Core Set",
        "5": "Force Core (Ultimate)",
        "6": "Force Core Set (Ultimate)",
        "7": "Force Core(High)",
        "8": "Force Core Set (High)",
        "9": "Upgrade Core (Ultimate)",
        "10": "Upgrade Core Set (Ultimate)",
    },
    "game_facts": {
        "favourite_count": 10,
        "grid_size": 8,
        "agent_shop_tab": 8,
        "agent_shop_slot": [1, 7],
        "work_tab": 4,
        "shop_capacity": 30,
        "shop_visible": 10,
    },
}


def _merge_keeping_existing(fresh: dict, existing: dict) -> dict:
    out = dict(fresh)
    for section in DEFAULTS:
        merged = dict(DEFAULTS[section])
        merged.update(existing.get(section) or {})
        out[section] = merged
    return out


def screen_size() -> "tuple[int, int]":
    import mss
    with mss.MSS() as sct:
        m = sct.monitors[1]
    return m["width"], m["height"]


def resolution_key(size=None) -> str:
    w, h = size or screen_size()
    return f"{w}x{h}"


def load_shared() -> dict:
    data = {}
    if OUT.exists():
        try:
            data = json.loads(OUT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    out = {}
    for section, default in DEFAULTS.items():
        merged = dict(default)
        merged.update(data.get(section) or {})
        out[section] = merged
    return out


def load(force: bool = False) -> dict:
    global _CACHE
    if _CACHE is None or force:
        if not OUT.exists():
            raise RuntimeError(
                f"{OUT.name} is missing. Run `py src/calibration.py` once to "
                f"measure this screen; nothing else in src/ carries a "
                f"coordinate of its own.")
        _CACHE = json.loads(OUT.read_text(encoding="utf-8"))

    data = _CACHE
    key = resolution_key()
    per = (data.get("by_resolution") or {}).get(key)
    if per is None:
        known = sorted((data.get("by_resolution") or {}))
        raise RuntimeError(
            f"calibration.json has no measurements for {key}. It knows "
            f"{known or 'nothing'}. Run `py src/calibration.py` on this "
            f"monitor -- the positions from another resolution would be wrong "
            f"here, so they are not reused.")

    merged = dict(per)
    for shared, default in DEFAULTS.items():
        section = dict(default)
        section.update(data.get(shared) or {})
        merged[shared] = section
    merged["resolution"] = key
    return merged

_S = load_shared()
_OCR = _S["ocr"]
_REG = _S["regions"]
_DET = _S["detect"]

TESSERACT = _OCR["tesseract"]
OCR_SCALE = _OCR["scale"]
OCR_MIN_CONF = _OCR["min_conf"]
OCR_PSM = _OCR["psm"]
ROW_PSM = _OCR["row_psm"]
DIGIT_PSM = _OCR["digit_psm"]
DIGIT_WHITELIST = _OCR["digit_whitelist"]

PARK_F = tuple(_REG["park"])
ALZ_SEARCH_F = tuple(_REG["alz_search"])
TOP_STRIP_F = tuple(_REG["top_strip"])
TAB_BAND_F = tuple(_REG["tab_band"])
FAV_BAND_F = tuple(_REG["fav_band"])
BOUNDARY_WINDOW_F = tuple(_REG["boundary_window"])
SLOT_PITCH_F = tuple(_REG["slot_pitch"])
PURCHASE_SORT_BAND_F = tuple(_REG["purchase_sort_band"])
PURCHASE_BUY_BAND_F = tuple(_REG["purchase_buy_band"])
PURCHASE_TABLE_BAND_F = tuple(_REG["purchase_table_band"])
POPUP_F = tuple(_REG["popup"])
REGISTER_TABLE_BAND_F = tuple(_REG["register_table_band"])

ALZ_BRIGHT = _DET["alz_bright"]
ALZ_SATURATION = _DET["alz_saturation"]
ALZ_MIN_PIXELS = _DET["alz_min_pixels"]
ALZ_LINE_HALF = _DET["alz_line_half"]
ALZ_MAX_WIDTH_FRACTION = _DET["alz_max_width_fraction"]
ALZ_MIN_HEIGHT = _DET["alz_min_height"]
ALZ_MAX_HEIGHT = _DET["alz_max_height"]
GRID_FIT_MIN = _DET["grid_fit_min"]
PANEL_OPEN_CHANGE = _DET["panel_open_change"]
EDGE_CANDIDATES = _DET["edge_candidates"]
EDGE_MIN_GAP = _DET["edge_min_gap"]
RULE_CANDIDATES = _DET["rule_candidates"]
RULE_MIN_GAP = _DET["rule_min_gap"]
PURCHASE_HEADER_UP = _DET["purchase_header_up"]
PURCHASE_HEADER_DOWN = _DET["purchase_header_down"]
PURCHASE_DIVIDER_SIGMA = _DET["purchase_divider_sigma"]
PURCHASE_CELL_INSET = _DET["purchase_cell_inset"]
ROW_BORDER_CANDIDATES = _DET["row_border_candidates"]
ROW_BORDER_MIN_GAP = _DET["row_border_min_gap"]
QTY_HALF_WIDTH = _DET["qty_half_width"]
FUNCTION_HALF_WIDTH = _DET["function_half_width"]
PRICE_RIGHT_GAP = _DET["price_right_gap"]
INK_THRESHOLD = _DET["ink_threshold"]
INK_PAD = _DET["ink_pad"]
BULK_MIN_CONF = _DET["bulk_min_conf"]
RESCUE_MIN_CONF = _DET["rescue_min_conf"]
MIN_PLAUSIBLE_PRICE = _DET["min_plausible_price"]
PRICE_MIN_DIGITS = _DET["price_min_digits"]
MIN_CLIENT_SIDE = _DET["min_client_side"]
FIT_PITCH_STEP = _DET["fit_pitch_step"]
FIT_START_STEP = _DET["fit_start_step"]
FAV_PEAK_CUT = _DET["fav_peak_cut"]
FAV_MERGE_GAP = _DET["fav_merge_gap"]
FAV_PITCH_SPREAD = _DET["fav_pitch_spread"]
SORT_PAD_LEFT = _DET["sort_pad_left"]
SORT_PAD_RIGHT = _DET["sort_pad_right"]
SORT_PAD_Y = _DET["sort_pad_y"]
SCROLL_POINT_INSET = _DET["scroll_point_inset"]
OCR_TIMEOUT = _OCR["timeout"]
FAVOURITE_COUNT = _S["game_facts"]["favourite_count"]

GRID = _S["game_facts"]["grid_size"]
ACTION_GAP = _S["timing"]["action_gap"]
PARK_SETTLE = _S["timing"]["park_settle"]
TAB_SETTLE = _S["timing"]["tab_settle"]
SEARCH_TIMEOUT = _S["timing"]["search_timeout"]
ALZ_SEARCH = None
_NOT_DIGIT = re.compile("[^0-9]")


def _client_rect():
    win = find_game_window()
    if win is not None:
        x, y, w, h = win[2]
        if w > MIN_CLIENT_SIDE and h > MIN_CLIENT_SIDE:
            return x, y, w, h
    w, h = screen_size()
    return 0, 0, w, h


def _box(frac, rect=None):
    x, y, w, h = rect or _client_rect()
    return (round(x + frac[0] * w), round(y + frac[1] * h),
            round(x + frac[2] * w), round(y + frac[3] * h))


def _point(frac, rect=None):
    x, y, w, h = rect or _client_rect()
    return (round(x + frac[0] * w), round(y + frac[1] * h))



def grab() -> Image.Image:
    import mss
    with mss.MSS() as sct:
        raw = sct.grab(sct.monitors[1])
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def park() -> None:
    ctypes.windll.user32.SetCursorPos(*_point(PARK_F))
    time.sleep(PARK_SETTLE)


def _mouse_event(flags: int):
    from open_inventory import _Input, _InputUnion, _MouseInput
    return _Input(type=_S["input"]["INPUT_MOUSE"], u=_InputUnion(mi=_MouseInput(0, 0, 0, flags, 0, None)))


def _button(down: int, up: int, x: int, y: int, settle: float) -> None:
    from open_inventory import _user32
    _user32.SetCursorPos(int(x), int(y))
    _user32.SendInput(1, ctypes.byref(_mouse_event(down)),
                      ctypes.sizeof(_mouse_event(down)))
    try:
        pass
    finally:
        _user32.SendInput(1, ctypes.byref(_mouse_event(up)),
                          ctypes.sizeof(_mouse_event(up)))
    time.sleep(settle)


def click(x: int, y: int, settle: float = None) -> None:
    shared = load_shared()
    _button(shared["input"]["MOUSEEVENTF_LEFTDOWN"],
            shared["input"]["MOUSEEVENTF_LEFTUP"], x, y,
            shared["timing"]["action_gap"] if settle is None else settle)


def right_click(x: int, y: int, settle: float = None) -> None:
    shared = load_shared()
    _button(shared["input"]["MOUSEEVENTF_RIGHTDOWN"],
            shared["input"]["MOUSEEVENTF_RIGHTUP"], x, y,
            shared["timing"]["action_gap"] if settle is None else settle)


def ocr(image: Image.Image, box, scale: int = None, min_conf: float = None):
    scale = OCR_SCALE if scale is None else scale
    min_conf = OCR_MIN_CONF if min_conf is None else min_conf
    crop = image.crop(box)
    crop = crop.resize((crop.width * scale, crop.height * scale),
                       Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    run = subprocess.run(
        [TESSERACT, "stdin", "stdout", "--psm", OCR_PSM, "tsv"],
        input=buf.getvalue(), capture_output=True, timeout=OCR_TIMEOUT)
    found = []
    for row in csv.DictReader(
            io.StringIO(run.stdout.decode("utf-8", "replace")), delimiter="\t"):
        try:
            conf = float(row["conf"])
        except (TypeError, ValueError):
            continue
        text = (row.get("text") or "").strip()
        if not text or conf < min_conf:
            continue
        x = box[0] + int(row["left"]) / scale + int(row["width"]) / scale / 2
        y = box[1] + int(row["top"]) / scale + int(row["height"]) / scale / 2
        found.append((text, round(conf), (round(x), round(y))))
    return found




def read_line(image: Image.Image, box, scale: int = None):
    scale = OCR_SCALE if scale is None else scale
    crop = image.crop(box)
    crop = crop.resize((crop.width * scale, crop.height * scale),
                       Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    run = subprocess.run(
        [TESSERACT, "stdin", "stdout", "--psm", ROW_PSM, "tsv"],
        input=buf.getvalue(), capture_output=True, timeout=OCR_TIMEOUT)
    words = []
    for row in csv.DictReader(
            io.StringIO(run.stdout.decode("utf-8", "replace")), delimiter="	"):
        text = (row.get("text") or "").strip()
        if text:
            words.append((int(row["left"]), text))
    return " ".join(t for _, t in sorted(words))


def ink_box(image: Image.Image, box):
    grey = np.asarray(image.crop(box).convert("L"), dtype=float)
    rows, cols = np.where(grey > INK_THRESHOLD)
    if not len(rows):
        return None
    x0 = max(0, int(cols.min()) - INK_PAD)
    x1 = min(grey.shape[1], int(cols.max()) + 1 + INK_PAD)
    y0 = max(0, int(rows.min()) - INK_PAD)
    y1 = min(grey.shape[0], int(rows.max()) + 1 + INK_PAD)
    return (box[0] + x0, box[1] + y0, box[0] + x1, box[1] + y1)


def read_number(image: Image.Image, box):
    tight = ink_box(image, box)
    if tight is None:
        return None
    crop = image.crop(tight)
    crop = crop.resize((crop.width * OCR_SCALE, crop.height * OCR_SCALE),
                       Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    run = subprocess.run(
        [TESSERACT, "stdin", "stdout", "--psm", ROW_PSM,
         "-c", "tessedit_char_whitelist=" + DIGIT_WHITELIST],
        input=buf.getvalue(), capture_output=True, timeout=OCR_TIMEOUT)
    digits = _NOT_DIGIT.sub("", run.stdout.decode("utf-8", "replace"))
    return int(digits) if digits else None


def read_digits(image: Image.Image, box, scale: int = None):
    scale = OCR_SCALE if scale is None else scale
    crop = image.crop(box)
    crop = crop.resize((crop.width * scale, crop.height * scale),
                       Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    run = subprocess.run(
        [TESSERACT, "stdin", "stdout", "--psm", DIGIT_PSM,
         "-c", "tessedit_char_whitelist=" + DIGIT_WHITELIST],
        input=buf.getvalue(), capture_output=True, timeout=OCR_TIMEOUT)
    digits = _NOT_DIGIT.sub("", run.stdout.decode("utf-8", "replace"))
    return int(digits) if digits else None


def fit_periodic(profile, n, lo, hi, step=None):
    step = FIT_PITCH_STEP if step is None else step
    d = np.abs(np.diff(profile))
    d = d / (d.max() or 1.0)
    length = len(d)
    best = (-1.0, None, None)
    for pitch in np.arange(lo, hi, step):
        if pitch * n >= length - 1:
            continue
        for start in np.arange(0, length - 1 - pitch * n, FIT_START_STEP):
            pos = (start + pitch * np.arange(n + 1)).round().astype(int)
            score = float(d[pos].min())
            if score > best[0]:
                best = (score, float(pitch), float(start))
    return best




def find_alz(image: Image.Image, search=None):
    search = search or _box(ALZ_SEARCH_F)
    crop = image.crop(search)
    px = crop.load()
    xs, ys = [], []
    for y in range(crop.height):
        for x in range(crop.width):
            r, g, b = px[x, y]
            hi, lo = max(r, g, b), min(r, g, b)
            if hi > ALZ_BRIGHT and hi - lo > ALZ_SATURATION:
                xs.append(x)
                ys.append(y)
    if len(xs) < ALZ_MIN_PIXELS:
        return None

    rows = {}
    for y in ys:
        rows[y] = rows.get(y, 0) + 1
    peak = max(rows, key=rows.get)
    keep = [(x, y) for x, y in zip(xs, ys) if abs(y - peak) <= ALZ_LINE_HALF]
    if len(keep) < ALZ_MIN_PIXELS:
        return None
    kx = [x for x, _ in keep]
    ky = [y for _, y in keep]
    box = (search[0] + min(kx), search[1] + min(ky),
           search[0] + max(kx), search[1] + max(ky))

    width, height = box[2] - box[0], box[3] - box[1]
    span_w = search[2] - search[0]
    if width >= span_w * ALZ_MAX_WIDTH_FRACTION:
        return None
    if not ALZ_MIN_HEIGHT <= height <= ALZ_MAX_HEIGHT:
        return None
    return box


def _pitch_bounds():
    w = _client_rect()[2]
    return SLOT_PITCH_F[0] * w, SLOT_PITCH_F[1] * w


def calibrate_inventory(verbose=True):
    say = print if verbose else (lambda *a: None)
    image = grab()
    alz = find_alz(image)
    if alz is None:
        raise RuntimeError(
            "the Alz balance was not found, so the Inventory panel is not "
            "open. Nothing measured.")
    anchor = (alz[2], alz[1])
    say(f"  Alz box {alz}   anchor {anchor}")

    layout = load_shared()["panel_layout"]
    s1x, s1y = layout["slot_one"]
    spx, spy = layout["slot_pitch"]
    t1x, t1y = layout["tab_one"]
    tp = layout["tab_pitch"]

    tabs = {str(k + 1): [round(anchor[0] + t1x + tp * k),
                         round(anchor[1] + t1y)] for k in range(GRID)}
    slots = {}
    for row in range(1, GRID + 1):
        for col in range(1, GRID + 1):
            slots[f"{row}x{col}"] = [
                round(anchor[0] + s1x + spx * (col - 1)),
                round(anchor[1] + s1y + spy * (row - 1)),
            ]
    say(f"  tab I {tabs['1']}  tab VIII {tabs[str(GRID)]}")
    say(f"  slot (1,1) {slots['1x1']}  (1,7) {slots['1x7']}  "
        f"(8,8) {slots[f'{GRID}x{GRID}']}")

    cx, cy, cw, chh = _client_rect()
    every = list(slots.values()) + list(tabs.values())
    outside = [p for p in every
               if not (cx <= p[0] <= cx + cw and cy <= p[1] <= cy + chh)]
    if outside:
        raise RuntimeError(
            f"{len(outside)} of {len(every)} positions fall outside the game "
            f"window ({cx},{cy} {cw}x{chh}) -- e.g. {outside[0]}. The anchor "
            f"at {anchor} must be wrong. Nothing measured.")

    return {
        "alz_box": list(alz),
        "anchor": list(anchor),
        "tabs": tabs,
        "tab_pitch": tp,
        "slots": slots,
        "slot_pitch": [spx, spy],
        "placed_from": "panel_layout offsets, not fitted",
    }


def _trade_window_open() -> bool:
    try:
        words = ocr(grab(), _box(TOP_STRIP_F))
    except Exception:
        return False
    return any(t.lower() == "register" for t, _c, _p in words)


def calibrate_shop(verbose=True):
    say = print if verbose else (lambda *a: None)
    image = grab()

    top_strip = _box(TOP_STRIP_F)
    words = ocr(image, top_strip)
    named = {t.lower(): (c, p) for t, c, p in words}

    reg = next((p for t, c, p in words if t.lower() == "register"), None)
    if reg is None:
        raise RuntimeError(
            "the Register tab was not found, so the Trade window is not open "
            "on a tab this can measure. Nothing written.")
    say(f"  Register {reg}")

    band = np.asarray(image.crop(_box(TAB_BAND_F)).convert("L"), dtype=float)
    d = np.abs(np.diff(band.mean(axis=0)))
    edges = sorted(int(i) for i in np.argsort(d)[::-1][:EDGE_CANDIDATES])
    picked = []
    for i in edges:
        if all(abs(i - p) > EDGE_MIN_GAP for p in picked):
            picked.append(i)
    _cw = _client_rect()[2]
    _lo, _hi = BOUNDARY_WINDOW_F[0] * _cw, BOUNDARY_WINDOW_F[1] * _cw
    boundary = next((x for x in picked if _lo < x < _hi), None)
    if boundary is None:
        raise RuntimeError("could not find the Purchase/Register boundary.")
    purchase = [2 * boundary - reg[0], reg[1]]
    say(f"  Purchase {purchase}  (mirrored about the boundary at x={boundary})")

    FAV = _box(FAV_BAND_F)
    prof = np.asarray(image.crop(FAV).convert("L"), dtype=float).mean(axis=0)
    floor, ceiling = prof.min(), prof.max()
    cut = floor + (ceiling - floor) * FAV_PEAK_CUT
    peaks, run = [], []
    for i, v in enumerate(prof):
        if v >= cut:
            run.append(i)
        elif run:
            peaks.append(max(run, key=lambda j: prof[j]))
            run = []
    if run:
        peaks.append(max(run, key=lambda j: prof[j]))
    merged = []
    for i in peaks:
        if merged and i - merged[-1] < FAV_MERGE_GAP:
            if prof[i] > prof[merged[-1]]:
                merged[-1] = i
        else:
            merged.append(i)
    peaks = merged
    favourites = [[FAV[0] + i, FAV[1] + (FAV[3] - FAV[1]) // 2] for i in peaks]
    gaps = np.diff([p[0] for p in favourites]) if len(favourites) > 1 else []
    f_pitch = float(np.mean(gaps)) if len(gaps) else 0.0
    say(f"  favourites: {len(favourites)} found, pitch {f_pitch:.2f}px, "
        f"first {favourites[0] if favourites else None} "
        f"last {favourites[-1] if favourites else None}")
    if len(favourites) != FAVOURITE_COUNT:
        raise RuntimeError(
            f"expected {FAVOURITE_COUNT} favourite slots, found {len(favourites)} at "
            f"{[p[0] for p in favourites]}. Not writing a calibration that "
            f"does not describe the row.")
    if len(gaps) and (max(gaps) - min(gaps)) > FAV_PITCH_SPREAD:
        raise RuntimeError(
            f"the favourite slots are not evenly spaced: gaps {list(gaps)}. "
            f"Something other than a slot was picked up.")

    return {
        "purchase_tab": purchase,
        "register_tab": list(reg),
        "tab_boundary_x": boundary,
        "favourites": favourites,
        "favourite_pitch": round(f_pitch, 2),
        "evidence": {
            "register_conf": named.get("register", (None,))[0],
            "favourite_pitch_spread": (int(max(gaps) - min(gaps))
                                      if len(gaps) else 0),
            "purchase_is_derived": True,
        },
    }


def find_game_window(title: str = "PlayCabal"):
    user32 = ctypes.windll.user32
    found = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if title.casefold() in buf.value.casefold():
            found.append((hwnd, buf.value))
            return False
        return True

    user32.EnumWindows(proto(cb), None)
    if not found:
        return None
    hwnd, name = found[0]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    r = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = (ctypes.c_long * 2)(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return hwnd, name, [pt[0], pt[1], r.right, r.bottom]




def calibrate_purchase(shop, verbose=True):
    say = print if verbose else (lambda *a: None)
    if "purchase_tab" not in shop:
        raise RuntimeError("the Purchase tab point is not measured yet.")

    px, py = shop["purchase_tab"]
    say(f"  switching to the Purchase tab at ({px}, {py})")
    click(px, py)
    time.sleep(TAB_SETTLE)
    park()
    time.sleep(PARK_SETTLE)
    image = grab()

    out = {}

    sort_band = _box(PURCHASE_SORT_BAND_F)
    sort_words = ocr(image, sort_band)
    if not sort_words:
        raise RuntimeError(
            f"nothing read in the sort band {sort_band}. The Purchase tab may "
            f"not have opened, or the band is wrong for this screen.")
    xs = [p[0] for _, _, p in sort_words]
    ys = [p[1] for _, _, p in sort_words]
    out["purchase_sort_region"] = [min(xs) - SORT_PAD_LEFT, min(ys) - SORT_PAD_Y,
                          max(xs) + SORT_PAD_RIGHT, max(ys) + SORT_PAD_Y]
    out["purchase_sort_text_seen"] = " ".join(t for t, _, _ in sort_words)
    say(f"  sort reads {out['purchase_sort_text_seen']!r} -> region "
        f"{out['purchase_sort_region']}")

    favs = shop.get("favourites") or []
    if not favs:
        raise RuntimeError("no favourite slots measured; cannot populate the "
                           "offers table to find the Buy column.")
    fx, fy = favs[0]
    say(f"  running favourite 1 at ({fx}, {fy}) to fill the table")
    click(fx, fy)
    park()
    deadline = time.monotonic() + SEARCH_TIMEOUT
    image = None
    while time.monotonic() < deadline:
        time.sleep(ACTION_GAP)
        image = grab()
        if [1 for t, _, _ in ocr(image, _box(PURCHASE_TABLE_BAND_F))
                if t.strip().lower() == "buy"]:
            break
    else:
        raise RuntimeError(
            f"favourite 1 returned no offers within {SEARCH_TIMEOUT}s, so the "
            f"table has no rows to measure.")
    say(f"  offers arrived after "
        f"{SEARCH_TIMEOUT - (deadline - time.monotonic()):.1f}s")

    table_band = _box(PURCHASE_TABLE_BAND_F)
    buys = [p for t, c, p in ocr(image, table_band)
            if t.strip().lower() == "buy"]
    if len(buys) < 2:
        raise RuntimeError(
            f"found {len(buys)} Buy button(s) in {table_band}; need at least "
            f"two to measure the row pitch. Did favourite 1 return offers?")

    xs = sorted(p[0] for p in buys)
    ys = sorted(p[1] for p in buys)
    out["purchase_buy_x"] = int(round(xs[len(xs) // 2]))
    gaps = [b - a for a, b in zip(ys, ys[1:]) if b - a > 1]
    pitch = sorted(gaps)[len(gaps) // 2] if gaps else 0
    if not pitch:
        raise RuntimeError("the Buy buttons gave no usable row pitch.")
    half = pitch // 2
    x0, x1 = table_band[0], table_band[2]
    out["purchase_row_pitch"] = int(pitch)
    out["purchase_row_one_y"] = int(ys[0])
    out["purchase_row_one"] = [x0, int(ys[0]) - half, x1, int(ys[0]) + half]
    out["purchase_rows_seen"] = len(buys)
    top, bot = out["purchase_row_one"][1], out["purchase_row_one"][3]
    hdr = (table_band[0], top - PURCHASE_HEADER_UP, table_band[2],
           top - PURCHASE_HEADER_DOWN)
    words = sorted(((p[0], t.strip().lower()) for t, _, p in ocr(image, hdr)
                    if t.strip().lower() in ("name", "qty", "price",
                                             "function")))
    have = [n for _, n in words]
    missing = [w for w in ("name", "qty", "price", "function") if w not in have]
    if missing:
        raise RuntimeError(
            f"the offers header is missing {missing}; read {have}. "
            f"Cannot place the per-column boxes.")
    centre = {n: x for x, n in words}

    band = np.asarray(image.crop((table_band[0], top, table_band[2], bot))
                      .convert("L"), dtype=float)
    solid = np.abs(np.diff(band, axis=1)).min(axis=0)
    picks = []
    for x in sorted(int(v) for v in
                    np.argsort(solid)[::-1][:ROW_BORDER_CANDIDATES]):
        if all(abs(x - k) > ROW_BORDER_MIN_GAP for k in picks):
            picks.append(x)
    inner = [table_band[0] + p for p in picks]
    left = max([e for e in inner if e < centre["name"]],
               default=table_band[0])
    right = min([e for e in inner if e > centre["function"]],
                default=table_band[2])
    out["purchase_row_content"] = [left + PURCHASE_CELL_INSET, top,
                                   right - PURCHASE_CELL_INSET, bot]

    buy_x = out["purchase_buy_x"]
    qty_lo = centre["qty"] - QTY_HALF_WIDTH
    qty_hi = centre["qty"] + QTY_HALF_WIDTH
    fn_lo = buy_x - FUNCTION_HALF_WIDTH
    fn_hi = buy_x + FUNCTION_HALF_WIDTH
    cols = {
        "name": [left + PURCHASE_CELL_INSET, top, qty_lo - PURCHASE_CELL_INSET,
                 bot],
        "qty": [qty_lo, top, qty_hi, bot],
        "price": [(centre["qty"] + centre["price"]) // 2, top,
                  fn_lo - PRICE_RIGHT_GAP + FUNCTION_HALF_WIDTH, bot],
        "function": [fn_lo, top, fn_hi, bot],
    }
    out["purchase_columns"] = cols
    say(f"  row content {out['purchase_row_content']} -> "
        f"{read_line(image, tuple(out['purchase_row_content']))!r}")
    for field in ("name", "qty", "price", "function"):
        say(f"    {field:9} {cols[field]}  -> "
            f"{read_line(image, tuple(cols[field]))!r}")

    say(f"  Buy column x={out['purchase_buy_x']}, {len(buys)} row(s), pitch {pitch}px")
    say(f"  row 1 centre y={out['purchase_row_one_y']}, box {out['purchase_row_one']}")
    return out


def calibrate_register_table(shop, verbose=True):
    say = print if verbose else (lambda *a: None)
    click(*shop["register_tab"])
    time.sleep(TAB_SETTLE)
    park()
    time.sleep(PARK_SETTLE)
    image = grab()

    band = _box(REGISTER_TABLE_BAND_F)
    marks = [p for t, _, p in ocr(image, band)
             if t.strip().lower() in ("change", "register")]
    if len(marks) < 2:
        raise RuntimeError(
            f"found {len(marks)} row button(s) in the Register table {band}; "
            f"need at least two to measure the row pitch.")
    xs = sorted(p[0] for p in marks)
    ys = sorted(p[1] for p in marks)
    gaps = [b - a for a, b in zip(ys, ys[1:]) if b - a > 1]
    pitch = sorted(gaps)[len(gaps) // 2] if gaps else 0
    if not pitch:
        raise RuntimeError("the Register row buttons gave no usable pitch.")
    out = {
        "table_x": [band[0], band[2]],
        "row_one_y": int(ys[0]),
        "row_pitch": int(pitch),
        "row_one_box": [band[0], int(ys[0]) - pitch // 2,
                        band[2], int(ys[0]) + pitch // 2],
        "button_x": int(xs[len(xs) // 2]),
        "table_point": [int(xs[len(xs) // 2]) - SCROLL_POINT_INSET, int(ys[0]) + pitch],
        "rows_per_notch": 1,
        "register_rows_seen": len(marks),
    }
    say(f"  Register table: {len(marks)} row(s), pitch {pitch}px, "
        f"row 1 y={out['row_one_y']}")
    say(f"  table_x {out['table_x']}, scroll point {out['table_point']}")
    return out


def main() -> None:
    from open_inventory import VK_I, VK_ESCAPE, focus_game, press

    shared = load_shared()
    gap = shared["timing"]["action_gap"]
    facts = shared["game_facts"]

    if not focus_game():
        raise RuntimeError(
            f"could not bring the {shared['game']['title_hint']!r} window to "
            f"the foreground. Nothing measured.")

    print("inventory:")

    for attempt in (1, 2):
        park()
        if find_alz(grab()) is not None:
            break
        press(VK_I)
        time.sleep(gap)
        park()
        if find_alz(grab()) is not None:
            if attempt == 2:
                print("  (the panel had been left open by an earlier run; "
                      "pressed I twice to get back to a known state)")
            break
        if attempt == 2:
            raise RuntimeError(
                "pressed I twice and the Alz balance never appeared, so the "
                "Inventory panel is not opening. Nothing measured.")

    inventory = calibrate_inventory()

    print("opening the Agent Shop:")
    tab = inventory["tabs"][str(facts["agent_shop_tab"])]
    row, col = facts["agent_shop_slot"]
    key = inventory["slots"][f"{row}x{col}"]
    print(f"  tab {facts['agent_shop_tab']} at {tab}")
    click(*tab)

    for attempt in (1, 2):
        if _trade_window_open():
            break
        print(f"  right-clicking slot ({row},{col}) at {key}")
        right_click(*key)
        time.sleep(gap)
        park()
        if _trade_window_open():
            if attempt == 2:
                print("  (the shop had been left open by an earlier run; "
                      "clicked twice to get back to a known state)")
            break
        if attempt == 2:
            raise RuntimeError(
                "right-clicked the Agent Shop key twice and the Trade window "
                "never appeared. Nothing written.")

    print("agent shop:")
    park()
    shop = calibrate_shop()

    print("purchase tab:")
    shop.update(calibrate_purchase(shop))

    print("register table:")
    shop.update(calibrate_register_table(shop))

    win = find_game_window()
    measured = {
        "screen": list(screen_size()),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "game": {
            "title_seen": win[1] if win else None,
            "client_rect": win[2] if win else None,
        },
        "alz_detect": {
            "search": list(_box(ALZ_SEARCH_F)),
            "bright": ALZ_BRIGHT,
            "saturation": ALZ_SATURATION,
            "min_pixels": ALZ_MIN_PIXELS,
            "line_half": ALZ_LINE_HALF,
        },
        "inventory": inventory,
        "shop": shop,
    }

    existing = {}
    if OUT.exists():
        try:
            existing = json.loads(OUT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}

    out = dict(existing)
    for section in DEFAULTS:
        merged = dict(DEFAULTS[section])
        merged.update(existing.get(section) or {})
        out[section] = merged
    out.setdefault("by_resolution", {})[resolution_key()] = measured
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}  [{resolution_key()}]")
    print(f"  resolutions in the file: {sorted(out['by_resolution'])}")

    close_everything(verbose=True)


def close_everything(verbose: bool = False) -> None:
    from open_inventory import VK_I, VK_ESCAPE, focus_game, press
    gap = load_shared()["timing"]["action_gap"]
    if not focus_game():
        return
    if verbose:
        print("restoring the default state:")

    press(VK_ESCAPE)
    time.sleep(gap)
    if verbose:
        print("  Escape: Trade window closed")

    park()
    if find_alz(grab()) is None:
        if verbose:
            print("  Inventory already closed")
        return
    press(VK_I)
    time.sleep(gap)
    park()
    if verbose:
        if find_alz(grab()) is None:
            print("  I: Inventory closed")
        else:
            print("  I: pressed, but the balance is still visible -- the "
                  "panel did not close. Close it by hand.")


if __name__ == "__main__":
    main()
