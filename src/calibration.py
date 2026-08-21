import csv
import ctypes
import datetime
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
CONFIG = HERE / "config.json"
LOG_DIR = HERE / "logs"

_CACHE = None

DEFAULTS = {
    "run": {
        "relist_from": 1,
        "relist_to": 22,
        "undercut_by": 1,
        "home_notches": 30,
        "for_minutes": 60,
    },
    "resupply": {
        "enabled": False,
        "rows_threshold": 3,
        "buy_min": 250,
        "price_diff_threshold": {},
    },
    "debug": {
        "frames": False,
        "keep_frames": 2000,
    },
    "timing": {
        "action_gap": 0.5,
        "key_hold": 0.02,
        "key_gap": 0.05,
        "hover_settle": 0.15,
        "modifier_settle": 0.25,
        "click_hold": 0.12,
        "focus_settle": 0.35,
        "wheel_gap": 0.12,
        "park_settle": 0.25,
        "tab_settle": 0.6,
        "poll_gap": 0.0,
        "panel_reread_gap": 1.0,
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
        "park": [0.5078, 0.8800],
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
        "dialog_buttons": [0.4688, 0.5296, 0.6641, 0.7085],
        "register_table_band": [0.1000, 0.1200, 0.4800, 0.6600],
        "register_button_band": [0.41, 0.12, 0.48, 0.66],
        "purchase_button_band": [0.41, 0.15, 0.48, 0.66],
        "purchase_header_band": [0.02, 0.155, 0.48, 0.205],
        "trade_tabs_band": [0.0, 0.035, 0.24, 0.08],
        "register_panel": [0.0039, 0.0709, 0.1133, 0.7597],
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
        "dialog_button_half": [70, 24],
        "min_plausible_balance": 1000,
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
        "panel_field_inset": 30,
        "panel_field_half": 14,
        "panel_label_gap": 22,
        "panel_rereads": 5,
        "min_name_overlap": 6,
        "alz_min_digits": 4,
        "slot_half": 24,
        "slot_occupied_stdev": 8.0,
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


CONFIG_SECTIONS = ("run", "debug", "timing", "resupply")


def _read(path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_config_if_absent() -> None:
    if CONFIG.exists():
        return
    CONFIG.write_text(json.dumps(
        {"_README": [
            "The knobs. calibration.json holds what was measured off the",
            "screen; nothing in here is measured and a calibration pass",
            "never writes to this file.",
        ]} | {k: dict(DEFAULTS[k]) for k in CONFIG_SECTIONS},
        indent=2), encoding="utf-8")


def load_shared() -> dict:
    measured = _read(OUT)
    knobs = _read(CONFIG)
    out = {}
    for section, default in DEFAULTS.items():
        merged = dict(default)
        merged.update(measured.get(section) or {})
        if section in CONFIG_SECTIONS:
            merged.update(knobs.get(section) or {})
        out[section] = merged
    return out


def remember_shop(key, value) -> None:
    global _CACHE
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    per = data.setdefault("by_resolution", {}).setdefault(resolution_key(), {})
    per.setdefault("shop", {})[key] = value
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _CACHE = None


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
VENDOR_TITLE_BAND_F = tuple(_REG["vendor_title_band"])
VENDOR_TAB_BAND_F = tuple(_REG["vendor_tab_band"])
CONVERT_GRID_BAND_F = tuple(_REG["convert_grid_band"])
BOUNDARY_WINDOW_F = tuple(_REG["boundary_window"])
SLOT_PITCH_F = tuple(_REG["slot_pitch"])
PURCHASE_SORT_BAND_F = tuple(_REG["purchase_sort_band"])
PURCHASE_BUY_BAND_F = tuple(_REG["purchase_buy_band"])
PURCHASE_TABLE_BAND_F = tuple(_REG["purchase_table_band"])
POPUP_F = tuple(_REG["popup"])
DIALOG_BUTTONS_F = tuple(_REG["dialog_buttons"])
REGISTER_TABLE_BAND_F = tuple(_REG["register_table_band"])
REGISTER_BUTTON_BAND_F = _S["regions"]["register_button_band"]
PURCHASE_BUTTON_BAND_F = _S["regions"]["purchase_button_band"]
PURCHASE_HEADER_BAND_F = _S["regions"]["purchase_header_band"]
TRADE_TABS_BAND_F = _S["regions"]["trade_tabs_band"]
REGISTER_PANEL_F = _S["regions"]["register_panel"]
PANEL_FIELD_INSET = _S["detect"]["panel_field_inset"]
PANEL_FIELD_HALF = _S["detect"]["panel_field_half"]
PANEL_LABEL_GAP = _S["detect"]["panel_label_gap"]
CLEAR_PRESSES_QTY = _S["detect"]["clear_presses_qty"]
CLEAR_PRESSES_PRICE = _S["detect"]["clear_presses_price"]
KEY_GAP = _S["timing"]["key_gap"]
CLEAR_GAP = _S["timing"]["clear_gap"]
HOVER_SETTLE = _S["timing"]["hover_settle"]
MODIFIER_SETTLE = _S["timing"]["modifier_settle"]
CLICK_HOLD = _S["timing"]["click_hold"]
PANEL_REREADS = _S["detect"]["panel_rereads"]
PANEL_REREAD_GAP = _S["timing"]["panel_reread_gap"]
MIN_PLAUSIBLE_PRICE = _S["detect"]["min_plausible_price"]
FAVOURITE_ITEMS = _S["favourite_items"]
MIN_NAME_OVERLAP = _S["detect"]["min_name_overlap"]
ALZ_MIN_DIGITS = _S["detect"]["alz_min_digits"]
SLOT_HALF = _S["detect"]["slot_half"]
SLOT_OCCUPIED_STDEV = _S["detect"]["slot_occupied_stdev"]
POLL_GAP = _S["timing"].get("poll_gap", 0.0)

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
CONVERT_PEAK_CUT = _DET["convert_peak_cut"]
CONVERT_MERGE_GAP = _DET["convert_merge_gap"]
FAV_PITCH_SPREAD = _DET["fav_pitch_spread"]
SORT_PAD_LEFT = _DET["sort_pad_left"]
SORT_PAD_RIGHT = _DET["sort_pad_right"]
SORT_PAD_Y = _DET["sort_pad_y"]
SCROLL_POINT_INSET = _DET["scroll_point_inset"]
OCR_TIMEOUT = _OCR["timeout"]
FAVOURITE_COUNT = _S["game_facts"]["favourite_count"]
CONVERT_GRADES = _S["game_facts"]["convert_grades"]
CONVERT_ROW_COUNT = _S["game_facts"]["convert_rows"]
CONVERT_SET_TO_CORE_ROWS = _S["game_facts"]["convert_set_to_core_rows"]
CONVERT_TAB = _S["game_facts"]["convert_tab"]
CONVERT_INVENTORY_TAB = _S["game_facts"]["convert_inventory_tab"]

GRID = _S["game_facts"]["grid_size"]
ACTION_GAP = _S["timing"]["action_gap"]
PARK_SETTLE = _S["timing"]["park_settle"]
TAB_SETTLE = _S["timing"]["tab_settle"]
DIALOG_TIMEOUT = _S["timing"]["dialog_timeout"]
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


def park(settle: bool = True) -> None:
    ctypes.windll.user32.SetCursorPos(*_point(PARK_F))
    if settle:
        time.sleep(PARK_SETTLE)


FRAME_DIR = Path(__file__).resolve().parent / "debug_frames"
FRAMES_ON = False
_FRAME_N = 0


def frames_on(enabled: "bool | None" = None) -> bool:
    global FRAMES_ON
    if enabled is None:
        enabled = bool(load_shared()["debug"]["frames"])
    FRAMES_ON = bool(enabled)
    if FRAMES_ON:
        FRAME_DIR.mkdir(parents=True, exist_ok=True)
        print(f"  debug frames -> {FRAME_DIR}")
    return FRAMES_ON


def prune_frames() -> None:
    keep = int(load_shared()["debug"]["keep_frames"])
    if keep <= 0 or not FRAME_DIR.exists():
        return
    shots = sorted(FRAME_DIR.glob("*.png"), key=lambda f: f.stat().st_mtime)
    for old_frame in shots[:max(0, len(shots) - keep)]:
        try:
            old_frame.unlink()
        except OSError:
            pass


def snap(label: str) -> "Path | None":
    global _FRAME_N
    if not FRAMES_ON:
        return None
    _FRAME_N += 1
    if _FRAME_N == 1:
        prune_frames()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("_") or "frame"
    out = FRAME_DIR / f"{_FRAME_N:05d}_{safe}.png"
    try:
        grab().save(out)
    except Exception as exc:
        print(f"  could not write {out.name}: {exc}")
        return None
    return out


def _mouse_event(flags: int):
    from open_inventory import _Input, _InputUnion, _MouseInput
    return _Input(type=_S["input"]["INPUT_MOUSE"], u=_InputUnion(mi=_MouseInput(0, 0, 0, flags, 0, None)))


def _button(down: int, up: int, x: int, y: int, settle: float) -> None:
    from open_inventory import _user32
    _user32.SetCursorPos(int(x), int(y))
    time.sleep(HOVER_SETTLE)
    _user32.SendInput(1, ctypes.byref(_mouse_event(down)),
                      ctypes.sizeof(_mouse_event(down)))
    try:
        time.sleep(CLICK_HOLD)
    finally:
        _user32.SendInput(1, ctypes.byref(_mouse_event(up)),
                          ctypes.sizeof(_mouse_event(up)))
    time.sleep(settle)


def ctrl_click(x: int, y: int, settle: float = None) -> None:
    from open_inventory import _user32, _Input, _event
    shared = load_shared()
    keys, timing = shared["input"], shared["timing"]
    vk = keys["VK_CONTROL"]
    gap = timing["action_gap"] if settle is None else settle

    _user32.SetCursorPos(int(x), int(y))
    time.sleep(HOVER_SETTLE)
    _user32.SendInput(1, ctypes.byref(_event(vk, up=False)),
                      ctypes.sizeof(_Input))
    try:
        time.sleep(MODIFIER_SETTLE)
        _user32.SendInput(1, ctypes.byref(_mouse_event(
            keys["MOUSEEVENTF_LEFTDOWN"])), ctypes.sizeof(_Input))
        try:
            time.sleep(CLICK_HOLD)
        finally:
            _user32.SendInput(1, ctypes.byref(_mouse_event(
                keys["MOUSEEVENTF_LEFTUP"])), ctypes.sizeof(_Input))
        time.sleep(MODIFIER_SETTLE)
    finally:
        _user32.SendInput(1, ctypes.byref(_event(vk, up=True)),
                          ctypes.sizeof(_Input))
    time.sleep(gap)
    snap(f"ctrlclick_{x}_{y}")


def alt_click(x: int, y: int, settle: float = None) -> None:
    from open_inventory import _user32, _Input, _event
    shared = load_shared()
    keys, timing = shared["input"], shared["timing"]
    vk = keys["VK_MENU"]
    gap = timing["action_gap"] if settle is None else settle

    _user32.SetCursorPos(int(x), int(y))
    time.sleep(HOVER_SETTLE)
    _user32.SendInput(1, ctypes.byref(_event(vk, up=False)),
                      ctypes.sizeof(_Input))
    try:
        time.sleep(MODIFIER_SETTLE)
        _user32.SendInput(1, ctypes.byref(_mouse_event(
            keys["MOUSEEVENTF_LEFTDOWN"])), ctypes.sizeof(_Input))
        try:
            time.sleep(CLICK_HOLD)
        finally:
            _user32.SendInput(1, ctypes.byref(_mouse_event(
                keys["MOUSEEVENTF_LEFTUP"])), ctypes.sizeof(_Input))
        time.sleep(MODIFIER_SETTLE)
    finally:
        _user32.SendInput(1, ctypes.byref(_event(vk, up=True)),
                          ctypes.sizeof(_Input))
    time.sleep(gap)
    snap(f"altclick_{x}_{y}")


def type_number(value: int, clear: int) -> None:
    from open_inventory import press
    keys = load_shared()["input"]
    for _ in range(clear):
        press(keys["VK_BACK"])
        time.sleep(CLEAR_GAP)
    for ch in str(int(value)):
        press(keys[f"VK_{ch}"])
        time.sleep(KEY_GAP)


def read_money(image, box):
    text = re.sub(r"[,\s]", "", read_line(image, tuple(box)))
    runs = re.findall(r"\d+", text)
    return int(max(runs, key=len)) if runs else None


def panel_qty(panel):
    text = read_line(grab(), tuple(panel["qty_box"]))
    nums = [int(re.sub(r"[^\d]", "", m)) for m in re.findall(r"\d[\d,]*", text)]
    if len(nums) < 2:
        return (0, 0)
    return (nums[0], nums[-1])


def undercut(price):
    by = int(load_shared()["run"]["undercut_by"])
    if by <= 0 or price is None:
        return price
    lowered = price - by
    return lowered if lowered >= MIN_PLAUSIBLE_PRICE else price


def panel_suggestion(panel):
    seen = []
    image = grab()
    for box in panel["suggestion_boxes"]:
        value = read_money(image, tuple(box))
        if value and value >= MIN_PLAUSIBLE_PRICE:
            seen.append(value)
    return min(seen) if seen else None


def click(x: int, y: int, settle: float = None) -> None:
    shared = load_shared()
    _button(shared["input"]["MOUSEEVENTF_LEFTDOWN"],
            shared["input"]["MOUSEEVENTF_LEFTUP"], x, y,
            shared["timing"]["action_gap"] if settle is None else settle)
    snap(f"click_{x}_{y}")


def right_click(x: int, y: int, settle: float = None) -> None:
    shared = load_shared()
    _button(shared["input"]["MOUSEEVENTF_RIGHTDOWN"],
            shared["input"]["MOUSEEVENTF_RIGHTUP"], x, y,
            shared["timing"]["action_gap"] if settle is None else settle)
    snap(f"rightclick_{x}_{y}")


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


def inventory_open(image=None):
    image = image if image is not None else grab()
    box = find_alz(image)
    if box is None:
        return None
    digits = re.sub(r"[^0-9]", "", read_line(image, box))
    if len(digits) < ALZ_MIN_DIGITS:
        return None
    return box


def await_inventory(timeout=None, verbose=False):
    from open_inventory import VK_I, press
    span = DIALOG_TIMEOUT if timeout is None else timeout
    park()
    box = inventory_open()
    if box is not None:
        return box
    for attempt in (1, 2):
        if verbose:
            print(f"  no balance is readable, so the Inventory panel is not "
                  f"open; pressing I (attempt {attempt})")
        press(VK_I)
        snap("press_I")
        deadline = time.monotonic() + span
        while time.monotonic() < deadline:
            box = inventory_open()
            if box is not None:
                return box
            time.sleep(POLL_GAP)
    return None


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
        words = ocr(grab(), _box(TRADE_TABS_BAND_F))
    except Exception:
        return False
    return any(t.lower() in ("register", "purchase") for t, _c, _p in words)


def purchase_tab_showing(image=None) -> bool:
    try:
        words = ocr(image if image is not None else grab(),
                    _box(PURCHASE_HEADER_BAND_F))
    except Exception:
        return False
    return any(t.strip().lower() == "category" for t, _c, _p in words)


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

    if not purchase_tab_showing():
        say(f"  the favourites live on the Purchase tab; switching to "
            f"{purchase} to measure them")
        click(*purchase)
        park()
        deadline = time.monotonic() + DIALOG_TIMEOUT
        while not purchase_tab_showing():
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"the Purchase tab would not open at {purchase}, so the "
                    f"favourite row cannot be measured.")
            time.sleep(POLL_GAP)
        image = grab()

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
    table_band = _box(PURCHASE_TABLE_BAND_F)
    button_band = _box(PURCHASE_BUTTON_BAND_F)
    image, seen = None, []
    while time.monotonic() < deadline:
        image = grab()
        seen = ocr(image, button_band)
        if [1 for t, _, _ in seen if t.strip().lower() == "buy"]:
            break
        time.sleep(POLL_GAP)
    else:
        raise RuntimeError(
            f"favourite 1 returned no offers within {SEARCH_TIMEOUT}s, so the "
            f"table has no rows to measure.")
    say(f"  offers arrived after "
        f"{SEARCH_TIMEOUT - (deadline - time.monotonic()):.1f}s")

    buys = [p for t, c, p in seen if t.strip().lower() == "buy"]
    if len(buys) < 2:
        raise RuntimeError(
            f"found {len(buys)} Buy button(s) in {button_band}; need at least "
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
    park()
    image = grab()
    deadline = time.monotonic() + DIALOG_TIMEOUT
    while not [1 for t, _c, _p in ocr(image, _box(REGISTER_BUTTON_BAND_F))
               if t.strip().lower() in ("change", "register")]:
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_GAP)
        image = grab()

    band = _box(REGISTER_TABLE_BAND_F)
    buttons = _box(REGISTER_BUTTON_BAND_F)
    marks = [p for t, _, p in ocr(image, buttons)
             if t.strip().lower() in ("change", "register")]
    if len(marks) < 2:
        raise RuntimeError(
            f"found {len(marks)} row button(s) in the Register button column "
            f"{buttons}; need at least two to measure the row pitch.")
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
    say(f"  button column {buttons}")
    say(f"  Register table: {len(marks)} row(s), pitch {pitch}px, "
        f"row 1 y={out['row_one_y']}")
    say(f"  table_x {out['table_x']}, scroll point {out['table_point']}")
    return out


WORK_TAB = _S["game_facts"]["work_tab"]


def inventory_tab_point(tab):
    tabs = load()["inventory"]["tabs"]
    key = str(int(tab))
    if key not in tabs:
        raise RuntimeError(
            f"inventory tab {key} is not in calibration.json, which has "
            f"{sorted(tabs)}")
    return tuple(tabs[key])


ACTION_BUTTON_WORDS = ("Confirmation", "Cancel", "Receive", "Register")

RECEIPT_WORD = "Receive"


def panel_agrees(panel, want_qty, want_price, say=lambda *a: None):
    box = panel.get("net_sales_box")
    if not box:
        raise RuntimeError(
            "the net sales box was never measured, so a price cannot be "
            "checked. Recalibrate before listing anything.")
    expect = want_qty * want_price
    for attempt in range(1, PANEL_REREADS + 2):
        image = grab()
        price = read_money(image, tuple(panel["price_field"])) or 0
        net = read_money(image, tuple(box)) or 0
        checks = {
            "price field": price == want_price,
            "net sales": net == expect,
            "net / quantity": net // want_qty == want_price
            if want_qty and net % want_qty == 0 else False,
            "net / price": net // want_price == want_qty
            if want_price and net % want_price == 0 else False,
        }
        if all(checks.values()):
            say(f"  panel agrees four ways: {want_qty} x {price:,} = {net:,}")
            return True
        bad = ", ".join(k for k, ok in checks.items() if not ok)
        say(f"  read {attempt}: price {price:,}, net {net:,} "
            f"(wanted {want_qty} x {want_price:,} = {expect:,})"
            f" -- disagrees on {bad}")
        time.sleep(PANEL_REREAD_GAP)
    return False


def pair_slot(slot):
    slot = int(slot)
    if slot % 2:
        return slot + 1 if str(slot + 1) in FAVOURITE_ITEMS else None
    return slot - 1 if str(slot - 1) in FAVOURITE_ITEMS else None


def favourite_slot_of(name):
    want = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    if not want:
        return None
    best = None
    for slot, item in FAVOURITE_ITEMS.items():
        key = re.sub(r"[^a-z0-9]", "", item.lower())
        if not key:
            continue
        shared = 0
        for a, b in zip(want, key):
            if a != b:
                break
            shared += 1
        if shared < MIN_NAME_OVERLAP:
            continue
        exact = key == want
        score = (shared, exact, -abs(len(key) - len(want)))
        if best is None or score > best[0]:
            best = (score, int(slot))
    return best[1] if best else None


def price_floor(name):
    prices = _read(OUT).get("market", {}).get("unit_price", {})
    slot = favourite_slot_of(name)
    if slot is None:
        return 0, ""
    pair = pair_slot(slot)
    if pair is None:
        return 0, ""
    floor = int(prices.get(str(pair)) or 0)
    if floor < MIN_PLAUSIBLE_PRICE:
        return None, FAVOURITE_ITEMS[str(pair)]
    return floor, FAVOURITE_ITEMS[str(pair)]


def _peaks(profile, cut_at, merge_gap):
    floor, ceiling = profile.min(), profile.max()
    cut = floor + (ceiling - floor) * cut_at
    peaks, run = [], []
    for i, v in enumerate(profile):
        if v >= cut:
            run.append(i)
        elif run:
            peaks.append(max(run, key=lambda j: profile[j]))
            run = []
    if run:
        peaks.append(max(run, key=lambda j: profile[j]))
    merged = []
    for i in peaks:
        if merged and i - merged[-1] < merge_gap:
            if profile[i] > profile[merged[-1]]:
                merged[-1] = i
        else:
            merged.append(i)
    return merged


def vendor_open(image=None) -> bool:
    image = image if image is not None else grab()
    try:
        words = {t.strip().lower()
                 for t, _c, _p in ocr(image, _box(VENDOR_TITLE_BAND_F))}
    except Exception:
        return False
    return {"shop", "normal", "repurchase"} <= words


def await_vendor(timeout=None, verbose=False):
    from open_inventory import press
    keys = load_shared()["input"]
    span = DIALOG_TIMEOUT if timeout is None else timeout
    if vendor_open():
        return True
    for attempt in (1, 2):
        if verbose:
            print(f"  pressing N to open the vendor Shop "
                  f"(attempt {attempt})")
        press(keys["VK_N"])
        snap("press_N")
        deadline = time.monotonic() + span
        while time.monotonic() < deadline:
            if vendor_open():
                return True
            time.sleep(POLL_GAP)
    return False


def vendor_tab_point(name, image=None):
    image = image if image is not None else grab()
    want = re.sub(r"[^a-z]", "", name.lower())
    for text, _c, point in ocr(image, _box(VENDOR_TAB_BAND_F)):
        if re.sub(r"[^a-z]", "", text.lower()) == want:
            return point
    return None


def calibrate_convert(verbose=True):
    say = print if verbose else (lambda *a: None)
    if _trade_window_open():
        raise RuntimeError(
            "the Agent Shop is open. The vendor will not open on top of it, "
            "so the conversion grid cannot be measured. Nothing written.")
    if not await_vendor(verbose=verbose):
        raise RuntimeError(
            "the vendor Shop did not open on N, so the conversion grid "
            "cannot be measured. Nothing written.")

    tab = vendor_tab_point(CONVERT_TAB)
    if tab is None:
        raise RuntimeError(
            f"the {CONVERT_TAB} tab was not found in the vendor tab band "
            f"{_box(VENDOR_TAB_BAND_F)}. Nothing written.")
    say(f"  {CONVERT_TAB} tab at {tab}")
    click(*tab)
    time.sleep(TAB_SETTLE)
    park()

    image = grab()
    band = _box(CONVERT_GRID_BAND_F)
    grid = np.asarray(image.crop(band).convert("L"), dtype=float)
    cols = _peaks(grid.mean(axis=0), CONVERT_PEAK_CUT, CONVERT_MERGE_GAP)
    rows = _peaks(grid.mean(axis=1), CONVERT_PEAK_CUT, CONVERT_MERGE_GAP)
    xs = [band[0] + i for i in cols]
    ys = [band[1] + i for i in rows]
    say(f"  grid band {band}: {len(xs)} column(s) at {xs}, "
        f"{len(ys)} row(s) at {ys}")
    if len(xs) != len(CONVERT_GRADES):
        raise RuntimeError(
            f"expected {len(CONVERT_GRADES)} conversion columns, one a grade, "
            f"and found {len(xs)} at {xs}. Nothing written.")
    if len(ys) != CONVERT_ROW_COUNT:
        raise RuntimeError(
            f"expected {CONVERT_ROW_COUNT} conversion rows and found "
            f"{len(ys)} at {ys}. Nothing written.")

    cells, pairs = {}, {}
    for r, y in enumerate(ys, start=1):
        for col, x in enumerate(xs, start=1):
            cells[f"{r}x{col}"] = [int(x), int(y)]
    for row_key, family in CONVERT_SET_TO_CORE_ROWS.items():
        r = int(row_key)
        for col, grade in enumerate(CONVERT_GRADES, start=1):
            core = (f"{family} Core ({grade})" if grade == "Ultimate"
                    else f"{family} Core({grade})")
            pairs[core] = {"cell": f"{r}x{col}",
                           "point": cells[f"{r}x{col}"],
                           "costs": f"{family} Core Set ({grade})"}
            say(f"    {core:<28} <- {pairs[core]['costs']:<30} "
                f"r{r}c{col} at {tuple(cells[f'{r}x{col}'])}")
    snap("convert_grid")
    return {"tab": CONVERT_TAB, "tab_point": list(tab),
            "cells": cells, "set_to_core": pairs,
            "columns": [int(v) for v in xs], "rows": [int(v) for v in ys]}


def price_diff_threshold(core_name):
    table = load_shared()["resupply"]["price_diff_threshold"] or {}
    want = re.sub(r"[^a-z0-9]", "", (core_name or "").lower())
    for name, value in table.items():
        if re.sub(r"[^a-z0-9]", "", name.lower()) == want:
            return int(value)
    return None


def calibrate_prices(verbose=True):
    import get_price
    say = print if verbose else (lambda *a: None)
    seen = {}
    for slot in sorted(FAVOURITE_ITEMS, key=int):
        row = get_price.get_price(int(slot), verbose=False)
        if row and row.get("unit_price"):
            seen[str(slot)] = int(row["unit_price"])
            say(f"  {FAVOURITE_ITEMS[slot]:<28}{seen[str(slot)]:>12,}")
        else:
            say(f"  {FAVOURITE_ITEMS[slot]:<28}{'unread':>12}")
    return seen


def calibrate_panel(verbose=True):
    say = print if verbose else (lambda *a: None)
    box = _box(REGISTER_PANEL_F)
    words = ocr(grab(), box)

    def below(word, after_y=0):
        hits = [p for t, _c, p in words
                if t.strip().lower() == word and p[1] > after_y]
        return min(hits, key=lambda p: p[1]) if hits else None

    price = below("price")
    qty_label = below("qty")
    if price is None or qty_label is None:
        raise RuntimeError(
            f"the register panel {box} does not read as a panel: found "
            f"{[t for t, _c, _p in words][:8]}. Nothing measured.")

    alz = [p for t, _c, p in words
           if t.strip().lower() == "alz" and price[1] < p[1] < qty_label[1]]
    if not alz:
        raise RuntimeError(
            "no Alz label between Price and Register QTY, so the price field "
            "cannot be placed.")
    alz = min(alz, key=lambda p: p[1])

    slash = [p for t, _c, p in words
             if t.strip().startswith("/") and p[1] > qty_label[1]]
    digit = [p for t, _c, p in words
             if t.strip().rstrip("/").isdigit() and p[1] > qty_label[1]
             and (not slash or p[0] < slash[0][0])]
    if not digit:
        raise RuntimeError(
            "no quantity field found under Register QTY; nothing measured.")
    qty = min(digit, key=lambda p: p[1])

    net_label = [p for t, _c, p in words
                 if t.strip().lower() == "net" and p[1] > qty[1]]
    net_row = None
    if net_label:
        after = min(net_label, key=lambda p: p[1])[1]
        tails = [p for t, _c, p in words
                 if t.strip().lower() == "alz" and p[1] > after]
        if tails:
            net_row = min(tails, key=lambda p: p[1])[1]

    button = [p for t, _c, p in words
              if t.strip().lower() == "register" and p[1] > qty[1]]
    if not button:
        raise RuntimeError(
            "no Register button below the quantity field; nothing measured.")
    button = max(button, key=lambda p: p[1])

    rows = []
    for _t, _c, point in words:
        if not price[1] < point[1] < alz[1] - PANEL_FIELD_HALF:
            continue
        if not any(abs(point[1] - y) <= PANEL_FIELD_HALF for y in rows):
            rows.append(point[1])
    rows.sort()

    left = box[0] + PANEL_FIELD_INSET
    right = alz[0] - PANEL_LABEL_GAP
    out = {
        "panel_box": list(box),
        "price_field": [left, alz[1] - PANEL_FIELD_HALF,
                        right, alz[1] + PANEL_FIELD_HALF],
        "price_point": [(left + right) // 2, alz[1]],
        "qty_point": [qty[0], qty[1]],
        "qty_box": [left, qty[1] - PANEL_FIELD_HALF,
                    right, qty[1] + PANEL_FIELD_HALF],
        "suggestion_boxes": [[left, y - PANEL_FIELD_HALF,
                              right, y + PANEL_FIELD_HALF] for y in rows],
        "register_button": [button[0], button[1]],
    }
    if net_row is not None:
        out["net_sales_box"] = [left, net_row - PANEL_FIELD_HALF,
                                right, net_row + PANEL_FIELD_HALF]
    say(f"  price field {out['price_field']} (click {out['price_point']})")
    say(f"  quantity box {out['qty_box']} (click {out['qty_point']})")
    say(f"  {len(rows)} suggested price row(s) at y {rows}")
    say(f"  net sales box {out.get('net_sales_box')}")
    say(f"  Register button at {out['register_button']}")
    return out


def slot_is_empty(image, row, col):
    point = inventory_slot_point(row, col)
    half = SLOT_HALF
    crop = image.crop((point[0] - half, point[1] - half,
                       point[0] + half, point[1] + half)).convert("L")
    data = list(crop.getdata())
    mean = sum(data) / len(data)
    stdev = (sum((v - mean) ** 2 for v in data) / len(data)) ** 0.5
    return stdev < SLOT_OCCUPIED_STDEV


def first_free_slot(tab, verbose=False):
    click(*inventory_tab_point(tab))
    park()
    image = grab()
    grid = _S["game_facts"]["grid_size"]
    for row in range(1, grid + 1):
        for col in range(1, grid + 1):
            if slot_is_empty(image, row, col):
                if verbose:
                    print(f"  tab {tab} slot ({row},{col}) is the first free "
                          f"one; a withdrawal lands there")
                return (row, col)
    return None


def inventory_slot_point(row, col):
    slots = load()["inventory"]["slots"]
    key = f"{int(row)}x{int(col)}"
    if key not in slots:
        raise RuntimeError(f"inventory slot {key} is not in calibration.json")
    return tuple(slots[key])


def calibrate_actions(shop, verbose=True):
    say = print if verbose else (lambda *a: None)
    panel = shop.get("panel")
    if not panel:
        raise RuntimeError("the register panel must be measured first.")
    timing = load_shared()["timing"]
    budget = timing["dialog_timeout"]
    dialog = _box(DIALOG_BUTTONS_F)
    min_x = load_shared()["detect"]["dialog_button_min_x"]
    learned = {}

    def buttons_now():
        found = {}
        for text, _conf, point in ocr(grab(), dialog):
            key = text.strip().lower()
            for word in ACTION_BUTTON_WORDS:
                if key == word.lower() and point[0] >= min_x:
                    found[word] = (int(point[0]), int(point[1]))
        return found

    def await_button(word):
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            here = buttons_now()
            for name, point in here.items():
                learned[f"button_{name.lower()}"] = list(point)
            if word in here:
                return here[word]
        return None

    if buttons_now():
        raise RuntimeError(
            "a dialog is already open over the Register table. Close it and "
            "calibrate again; nothing was clicked.")

    row_one = tuple(shop["row_one_box"])
    before = read_line(grab(), row_one)
    lowered = before.lower()
    listed = re.search(r"\d[\d,]{2,}", before) is not None
    if not listed or RECEIPT_WORD.lower() in lowered:
        say(f"  row 1 reads {before[:48]!r}; it is not a live listing this "
            f"pass can withdraw. Earlier positions stand.")
        return {}

    landing = first_free_slot(WORK_TAB, verbose=verbose)
    if landing is None:
        say(f"  inventory tab {WORK_TAB} is full, so a withdrawal would land "
            f"somewhere this pass cannot follow. Not walking the actions; "
            f"earlier positions stand.")
        return {}

    change = (shop["button_x"], shop["row_one_y"])
    say(f"  row 1 is {before[:48]!r}")
    say(f"  Change at {change}")
    click(*change)
    park(settle=False)

    cancel = await_button("Cancel")
    if cancel is None:
        raise RuntimeError(
            "no Cancel button appeared after Change on row 1. Nothing has "
            "been withdrawn.")
    say(f"  Cancel at {cancel}")
    click(*cancel)
    park(settle=False)

    confirm = await_button("Confirmation")
    if confirm is None:
        raise RuntimeError(
            "no Confirmation button appeared after Cancel on row 1. The "
            "dialog is open and nothing is committed -- close it by hand.")
    say(f"  Confirmation at {confirm}")
    click(*confirm)
    park()

    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if not buttons_now():
            break
    else:
        raise RuntimeError(
            "the dialog stayed open after Confirmation on row 1. Whether the "
            "cancel committed is unknown -- check the shop by hand.")

    after = read_line(grab(), row_one)
    say(f"  row 1 now reads {after[:48]!r}")

    tab = inventory_tab_point(WORK_TAB)
    say(f"  back to inventory tab {WORK_TAB} at {tab}; the withdrawal moves "
        f"the panel to whichever tab it landed on")
    click(*tab)
    slot = inventory_slot_point(*landing)
    if slot_is_empty(grab(), *landing):
        raise RuntimeError(
            f"tab {WORK_TAB} slot {landing} is empty after the withdrawal, so "
            f"the item did not land where the free slot was. It is in the "
            f"bag; list it by hand.")
    say(f"  listing it back from tab {WORK_TAB} slot {landing} at {slot}")
    ctrl_click(*slot)
    held = (0, 0)
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        held = panel_qty(panel)
        if held[1]:
            break
        time.sleep(POLL_GAP)
    if not held[1]:
        say(f"  nothing loaded on the first ctrl-click; trying once more")
        ctrl_click(*slot)
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            held = panel_qty(panel)
            if held[1]:
                break
            time.sleep(POLL_GAP)
    if not held[1]:
        raise RuntimeError(
            f"nothing loaded from tab {WORK_TAB} slot {landing} after two "
            f"ctrl-clicks, so it cannot be listed back. The item is in the "
            f"bag; list it by hand.")
    price = undercut(panel_suggestion(panel))
    if price is None:
        raise RuntimeError(
            f"the panel suggests no price for the {held[1]} withdrawn, so "
            f"there is nothing to list at. The item is in the bag.")
    say(f"  {held[1]} held, panel suggests {price:,}")

    click(*panel["price_point"])
    type_number(price, CLEAR_PRESSES_PRICE)
    click(*panel["qty_point"])
    type_number(held[1], CLEAR_PRESSES_QTY)
    park()
    if not panel_agrees(panel, held[1], price, say):
        raise RuntimeError(
            f"the panel will not confirm {held[1]} at {price:,} on every "
            f"check after {PANEL_REREADS + 1} reads. Nothing has been listed; "
            f"the item is in the bag.")

    click(*panel["register_button"], settle=0.0)
    learned["button_register"] = list(panel["register_button"])
    confirm = await_button("Confirmation")
    if confirm is None:
        raise RuntimeError(
            "no Confirmation appeared after Register. Nothing committed; the "
            "item is in the bag.")
    click(*confirm, settle=0.0)
    park()
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if not buttons_now():
            break
        time.sleep(POLL_GAP)
    else:
        raise RuntimeError(
            "the dialog stayed open after Confirmation on the relist. Whether "
            "it committed is unknown -- check the shop by hand.")
    back = read_line(grab(), row_one)
    digits = [int(re.sub(r"[^\d]", "", m))
              for m in re.findall(r"\d[\d,]*", back)]
    if price not in digits:
        raise RuntimeError(
            f"the listing went through but row 1 reads {back[:48]!r}, which "
            f"does not show {price:,}. Check the shop by hand -- something is "
            f"on the board at a price nobody chose.")
    say(f"  row 1 reads {back[:48]!r} again, at {price:,} as typed")
    say(f"  learned {', '.join(sorted(learned))}")
    return learned


def main(close: bool = True) -> None:
    from open_inventory import VK_I, VK_ESCAPE, focus_game, press

    write_config_if_absent()

    shared = load_shared()
    gap = shared["timing"]["action_gap"]
    facts = shared["game_facts"]

    if not focus_game():
        raise RuntimeError(
            f"could not bring the {shared['game']['title_hint']!r} window to "
            f"the foreground. Nothing measured.")

    print("inventory:")

    if await_inventory(verbose=True) is None:
        raise RuntimeError(
            "no readable Alz balance after pressing I twice, so the Inventory "
            "panel is not open. Measuring from a guessed anchor would put "
            "every click somewhere in the world. Nothing measured.")

    snap("inventory_as_measured")
    inventory = calibrate_inventory()
    snap("inventory_after_measure")

    print("opening the Agent Shop:")
    tab = inventory["tabs"][str(facts["agent_shop_tab"])]
    row, col = facts["agent_shop_slot"]
    key = inventory["slots"][f"{row}x{col}"]
    print(f"  tab {facts['agent_shop_tab']} at {tab}")
    click(*tab)

    for attempt in (1, 2):
        if _trade_window_open():
            break
        if inventory_open() is None:
            print(f"  the Inventory panel is not on screen; the key at {key} "
                  f"is not there to right-click")
            snap("inventory_gone_before_rightclick")
            if await_inventory(verbose=True) is None:
                raise RuntimeError(
                    "the Inventory panel would not open, so the Agent Shop "
                    "key cannot be right-clicked. Nothing written.")
            click(*tab)
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

    snap("shop_open")
    print("agent shop:")
    park()
    shop = calibrate_shop()

    print("purchase tab:")
    shop.update(calibrate_purchase(shop))

    print("register table:")
    shop.update(calibrate_register_table(shop))

    print("market prices:")
    prices = calibrate_prices()

    print("register panel:")
    click(*shop["register_tab"])
    park()
    deadline = time.monotonic() + DIALOG_TIMEOUT
    while purchase_tab_showing():
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "the Purchase tab is still showing after clicking Register, "
                "so the register panel cannot be measured.")
        time.sleep(POLL_GAP)
    shop["panel"] = calibrate_panel()

    print("actions:")
    shop.update(calibrate_actions(shop))

    convert_block = None
    vendor_visited = False
    if shared["resupply"]["enabled"]:
        print("conversion vendor:")
        press(VK_ESCAPE)
        time.sleep(gap)
        snap("press_escape_before_vendor")
        if _trade_window_open():
            raise RuntimeError(
                "the Agent Shop would not close, and the vendor will not open "
                "on top of it. Nothing written.")
        convert_block = calibrate_convert()
        vendor_visited = True
        press(VK_ESCAPE)
        time.sleep(gap)
        snap("press_escape_after_vendor")

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
    if convert_block is not None:
        measured["convert"] = convert_block

    existing = {}
    if OUT.exists():
        try:
            existing = json.loads(OUT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}

    out = dict(existing)
    for section in DEFAULTS:
        if section in CONFIG_SECTIONS:
            out.pop(section, None)
            continue
        merged = dict(DEFAULTS[section])
        merged.update(existing.get(section) or {})
        out[section] = merged
    per = out.setdefault("by_resolution", {})
    prior = dict(per.get(resolution_key()) or {})
    kept = {}
    for section, values in prior.items():
        if not isinstance(values, dict) or section not in measured:
            continue
        missing = {k: v for k, v in values.items()
                   if k not in measured[section]}
        if missing:
            kept[section] = missing
            measured[section] = {**missing, **measured[section]}
    per[resolution_key()] = measured
    if prices:
        out["market"] = {"measured_at": measured["measured_at"],
                         "unit_price": prices}
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}  [{resolution_key()}]")
    print(f"  resolutions in the file: {sorted(out['by_resolution'])}")
    for section, values in kept.items():
        print(f"  kept {len(values)} value(s) this pass does not measure in "
              f"{section}: {', '.join(sorted(values))}")

    if close:
        close_everything(verbose=True)
    elif vendor_visited:
        print("reopening the Agent Shop the caller was promised:")
        if await_inventory(verbose=True) is None:
            raise RuntimeError(
                "the Inventory panel would not reopen after the vendor, so "
                "the Agent Shop cannot be brought back. calibration.json is "
                "written; the game is not where the caller expects it.")
        click(*inventory["tabs"][str(facts["agent_shop_tab"])])
        time.sleep(gap)
        right_click(*inventory["slots"][f"{row}x{col}"])
        time.sleep(gap)
        park()
        if not _trade_window_open():
            raise RuntimeError(
                "the Agent Shop would not reopen after the vendor. "
                "calibration.json is written; the game is not where the "
                "caller expects it.")
        print("  the Agent Shop is open again")


def close_everything(verbose: bool = False) -> None:
    from open_inventory import VK_I, VK_ESCAPE, focus_game, press
    gap = load_shared()["timing"]["action_gap"]
    if not focus_game():
        return
    if verbose:
        print("restoring the default state:")

    press(VK_ESCAPE)
    time.sleep(gap)
    snap("press_escape")
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


class _Tee:
    def __init__(self, stream, handle):
        self.stream = stream
        self.handle = handle

    def write(self, text):
        self.stream.write(text)
        self.handle.write(text)
        self.handle.flush()
        return len(text)

    def flush(self):
        self.stream.flush()
        self.handle.flush()

    def isatty(self):
        return self.stream.isatty()

    def fileno(self):
        return self.stream.fileno()


def log_to_file(what="run"):
    import sys
    if isinstance(sys.stdout, _Tee):
        return Path(sys.stdout.handle.name)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = LOG_DIR / f"{stamp}_{what}.log"
    handle = open(path, "a", encoding="utf-8", buffering=1)
    handle.write(stamp + "  " + " ".join(sys.argv) + chr(10))
    sys.stdout = _Tee(sys.stdout, handle)
    sys.stderr = _Tee(sys.stderr, handle)
    print(f"  logging to {path}")
    return path


if __name__ == "__main__":
    import sys as _sys
    log_to_file("calibrate")
    frames_on(True if "--frames" in _sys.argv else None)
    main()
