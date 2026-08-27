import collections
import contextlib
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
from PIL import Image, ImageChops, ImageDraw, ImageOps

HERE = Path(__file__).resolve().parent
OUT = HERE / "calibration.json"
CONFIG = HERE / "config.json"
LOG_DIR = HERE / "logs"

_CACHE = None
_MERGED = None

DEFAULTS = {
    "run": {
        "relist_from": 1,
        "relist_to": 22,
        "undercut_by": 1,
        "home_notches": 30,
        "for_minutes": 60,
        "price_check_factor": 2.0,
        "stop_key_presses": 4,
        "floor_break_after": 5,
        "stop_key_window": 1.5,
    },
    "war": {
        "enabled": False,
        "start_hours": [1, 4, 7, 10, 13, 16, 19, 22],
        "war_minutes": 30,
        "quiet_before_end": 60,
        "quiet_seconds": 300,
        "clock_uncertainty": 59,
        "clock_resync": 1800.0,
        "clock_confirm_pause": 1.0,
        "clock_max_drift": 150.0,
    },
    "resupply": {
        "enabled": False,
        "enable_buying": {},
        "rows_threshold": {"default": 3},
        "buy_min": {"default": 250},
        "buy_max": {"default": 500},
        "buy_retries": 3,
        "price_diff_threshold": {},
    },
    "debug": {
        "frames": False,
        "keep_frames": 2000,
        "frames_queued": 64,
        "video_fps": 15,
        "video_seconds": 180,
        "keep_videos": 5,
        "video_scale": 0.5,
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
        "refresh_settle": 0.05,
        "poll_gap": 0.0,
        "stop_key_poll": 0.03,
        "stale_sweep": 1.0,
        "panel_reread_gap": 1.0,
        "craft_settle_per_block": 5.0,
        "craft_settle_block": 50,
        "craft_settle_max": 300.0,
        "search_timeout": 8.0,
        "search_retries": 3,
        "dialog_timeout": 8.0,
        "retry_gap": 1.0,
    },
    "input": {
        "INPUT_MOUSE": 0,
        "INPUT_KEYBOARD": 1,
        "KEYEVENTF_KEYUP": 0x0002,
        "KEYEVENTF_UNICODE": 0x0004,
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
        "VK_END": 0x23,
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
        "recovery_dialog_button": [0.5004, 0.5457],
        "recovery_dual_yes": [0.4766, 0.5457],
        "recovery_select_panel": [0.7800, 0.3000, 0.9900, 0.7500],
        "recovery_enter_button": [0.8000, 0.9000, 0.9900, 0.9800],
        "alz_search": [0.8750, 0.6245, 0.9805, 0.6567],
        "top_strip": [0.0000, 0.0197, 0.5078, 0.1585],
        "tab_band": [0.0000, 0.0270, 0.2734, 0.0709],
        "fav_band": [0.2422, 0.7100, 0.4648, 0.7465],
        "boundary_window": [0.0781, 0.1289],
        "slot_pitch": [0.0266, 0.0312],
        "craft_window": [0.0039, 0.0051, 0.5078, 0.7283],
        "craft_tiers": [0.0234, 0.1147, 0.2031, 0.2023],
        "craft_recipes": [0.0234, 0.1147, 0.2031, 0.2900],
        "craft_material": [0.2188, 0.4215, 0.3906, 0.4945],
        "craft_buttons": [0.0234, 0.6771, 0.5078, 0.7210],
        "purchase_sort_band": [0.2500, 0.1200, 0.5000, 0.1700],
        "purchase_buy_band": [0.3000, 0.6800, 0.5100, 0.7300],
        "purchase_table_band": [0.1000, 0.1500, 0.4800, 0.6600],
        "popup": [0.1953, 0.2389, 0.8203, 0.8232],
        "dialog_buttons": [0.4688, 0.5296, 0.6641, 0.7085],
        "gift_icon": [0.1480, 0.9620],
        "gift_window": [0.1719, 0.1585, 0.8320, 0.8451],
        "register_table_band": [0.1000, 0.1200, 0.4800, 0.6600],
        "register_footer_band": [0.1000, 0.6600, 0.5100, 0.7300],
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
        "alz_sweep_height": 44,
        "alz_sweep_step": 18,
        "alz_label_back": 260,
        "alz_band_pad": 8,
        "alz_band_left": 60,
        "panel_scale_low": 0.6,
        "panel_scale_high": 1.2,
        "panel_scale_step": 0.002,
        "panel_rule_contrast": 20,
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
        "panel_label_pad": 12,
        "word_row_slack": 6,
        "tier_row_slack": 4,
        "panel_rereads": 5,
        "min_name_overlap": 6,
        "alz_min_digits": 4,
        "slot_half": 24,
        "slot_occupied_stdev": 30.0,
        "gift_column_spread": 12,
        "server_lag_pixels": 2000,
        "server_lag_sure": 4000,
        "server_lag_red": 210,
        "server_lag_green": [120, 200],
        "server_lag_blue": 80,
        "panel_moved_slack": 30,
    },
    "text": {
        "empty_row": "premiumexclusiveslot",
        "sort_direction": r"price\s*:?\s*(low|high)",
        "purchase_row": '^(?P<name>.*?)\\s+(?P<qty>\\S+)\\s+(?P<price>\\d[\\d,]*)\\s*\\D*$',
        "pack_marker": r"\bX\s*(\d+)\s*$",
        "row_grouping": r"(?<=\d)[.\s](?=\d{3}(?!\d)(?!,))",
        "change_word": "Change",
        "dismiss_word": "Cancel",
        "confirm_word": "Confirmation",
        "receipt_word": "Receive",
        "close_word": "Close",
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
        "craft_tab": 8,
        "craft_key_slot": [1, 8],
        "craft_tier_words": "2000|2999",
        "craft_recipe_words": "Chaos Core Set|x3",
        "refresh_word": "Refresh",
        "set_word": "set",
        "held_of_needed": "/",
        "craft_request_words": "Request|All",
        "craft_request_word": "Repeat",
        "craft_complete_word": "Complete",
        "craft_material_word": "Material",
        "craft_cores_per_set": 3,
        "grid_size": 8,
        "agent_shop_tab": 8,
        "agent_shop_slot": [1, 7],
        "work_tab": 4,
        "gift_boxes": 4,
        "work_slot": "1,1",
        "shop_capacity": 30,
        "shop_visible": 10,
        "max_stack": 250,
    },
    "recovery": {
        "screen_timeout": 25.0,
        "world_timeout": 120.0,
        "sub_password_wait": 12.0,
        "after_typing_wait": 10.0,
        "failed_retry_wait": 5.0,
        "reconnect_settle": 60.0,
        "login_tries": 6,
        "reconnect_tries": 4,
        "notice_tries": 3,
        "clear_keys": 32,
        "double_gap": 0.08,
        "phrase_gap_x": 260,
        "phrase_gap_y": 16,
        "password_above_login": 86,
        "username_above_login": 127,
        "panel_reach": 0.16,
        "disconnect_words": ["disconnect", "disconnected", "log-out", "logged"],
        "failed_words": ["failed to connect", "try later"],
        "dual_words": ["dual login", "already in use", "like to reconnect"],
        "login_word": "login",
        "ok_word": "ok",
        "confirm_word": "confirmation",
        "yes_word": "yes",
        "enter_word": "enter server",
    },
}


def screen_size() -> "tuple[int, int]":
    import mss
    with mss.MSS() as sct:
        m = sct.monitors[1]
    return m["width"], m["height"]


def resolution_key(size=None) -> str:
    w, h = size or screen_size()
    return f"{w}x{h}"


CONFIG_SECTIONS = ("run", "debug", "timing", "resupply", "war", "recovery")


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


def _measured() -> dict:
    global _CACHE
    if _CACHE is None:
        if not OUT.exists():
            return {}
        try:
            _CACHE = json.loads(OUT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return (_CACHE.get("by_resolution") or {}).get(resolution_key()) or {}


def remember(section, values) -> None:
    global _CACHE, _MERGED
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    per = data.setdefault("by_resolution", {}).setdefault(resolution_key(), {})
    per.setdefault(section, {}).update(values)
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _CACHE = _MERGED = None


def remember_band(name, box) -> None:
    x, y, w, h = _client_rect()
    remember("regions", {name: [(box[0] - x) / w, (box[1] - y) / h,
                                (box[2] - x) / w, (box[3] - y) / h]})


def remember_shop(key, value) -> None:
    remember("shop", {key: value})


def load(force: bool = False) -> dict:
    global _CACHE, _MERGED
    if _CACHE is None or force:
        if not OUT.exists():
            raise RuntimeError(
                f"{OUT.name} is missing.")
        _CACHE = json.loads(OUT.read_text(encoding="utf-8"))
        _MERGED = None

    if _MERGED is not None:
        return _MERGED
    data = _CACHE
    key = resolution_key()
    per = (data.get("by_resolution") or {}).get(key)
    if per is None:
        known = sorted((data.get("by_resolution") or {}))
        raise RuntimeError(
            f"calibration.json has no measurements for {key}; it has "
            f"{known or 'nothing'}.")

    merged = dict(per)
    for shared, default in DEFAULTS.items():
        section = dict(default)
        section.update(data.get(shared) or {})
        merged[shared] = section
    merged["resolution"] = key
    _MERGED = merged
    return merged

_S = load_shared()
_OCR = _S["ocr"]
_REG = _S["regions"]
_DET = _S["detect"]

TESSERACT = _OCR["tesseract"]
OCR_SCALE = _OCR["scale"]
OCR_MIN_CONF = _OCR["min_conf"]
OCR_PSM = _OCR["psm"]
OCR_BORDER = _OCR["border"]
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
SERVER_LAG_BAND_F = tuple(_REG["server_lag_band"])
BOUNDARY_WINDOW_F = tuple(_REG["boundary_window"])
PURCHASE_SORT_BAND_F = tuple(_REG["purchase_sort_band"])
PURCHASE_TABLE_BAND_F = tuple(_REG["purchase_table_band"])
DIALOG_BUTTONS_F = tuple(_REG["dialog_buttons"])
REGISTER_TABLE_BAND_F = tuple(_REG["register_table_band"])
REGISTER_FOOTER_BAND_F = tuple(_REG["register_footer_band"])
REGISTER_BUTTON_BAND_F = _S["regions"]["register_button_band"]
PURCHASE_BUTTON_BAND_F = _S["regions"]["purchase_button_band"]
PURCHASE_HEADER_BAND_F = _S["regions"]["purchase_header_band"]
TRADE_TABS_BAND_F = _S["regions"]["trade_tabs_band"]
REGISTER_PANEL_F = _S["regions"]["register_panel"]
PANEL_FIELD_INSET = _S["detect"]["panel_field_inset"]
PANEL_FIELD_HALF = _S["detect"]["panel_field_half"]
PANEL_LABEL_GAP = _S["detect"]["panel_label_gap"]
PANEL_LABEL_PAD = _S["detect"]["panel_label_pad"]
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
POLL_GAP = _S["timing"]["poll_gap"]

ALZ_BRIGHT = _DET["alz_bright"]
ALZ_SATURATION = _DET["alz_saturation"]
ALZ_MIN_PIXELS = _DET["alz_min_pixels"]
ALZ_LINE_HALF = _DET["alz_line_half"]
ALZ_MAX_WIDTH_FRACTION = _DET["alz_max_width_fraction"]
ALZ_MIN_HEIGHT = _DET["alz_min_height"]
ALZ_MAX_HEIGHT = _DET["alz_max_height"]
ALZ_SWEEP_HEIGHT = _DET["alz_sweep_height"]
ALZ_SWEEP_STEP = _DET["alz_sweep_step"]
ALZ_LABEL_BACK = _DET["alz_label_back"]
ALZ_BAND_PAD = _DET["alz_band_pad"]
ALZ_BAND_LEFT = _DET["alz_band_left"]
PANEL_SCALE_LOW = _DET["panel_scale_low"]
PANEL_SCALE_HIGH = _DET["panel_scale_high"]
PANEL_SCALE_STEP = _DET["panel_scale_step"]
PANEL_RULE_CONTRAST = _DET["panel_rule_contrast"]
MIN_PLAUSIBLE_BALANCE = _DET["min_plausible_balance"]
EDGE_CANDIDATES = _DET["edge_candidates"]
EDGE_MIN_GAP = _DET["edge_min_gap"]
PURCHASE_HEADER_UP = _DET["purchase_header_up"]
PURCHASE_HEADER_DOWN = _DET["purchase_header_down"]
PURCHASE_CELL_INSET = _DET["purchase_cell_inset"]
ROW_BORDER_CANDIDATES = _DET["row_border_candidates"]
ROW_BORDER_MIN_GAP = _DET["row_border_min_gap"]
QTY_HALF_WIDTH = _DET["qty_half_width"]
FUNCTION_HALF_WIDTH = _DET["function_half_width"]
PRICE_RIGHT_GAP = _DET["price_right_gap"]
INK_THRESHOLD = _DET["ink_threshold"]
INK_PAD = _DET["ink_pad"]
INK_CONTRAST_MIN = _DET["ink_contrast_min"]
WARM_MIN_BRIGHT = _DET["warm_min_bright"]
WARM_MIN_SATURATION = _DET["warm_min_saturation"]
ALZ_MAX_TEXT_HEIGHT = _DET["alz_max_text_height"]
BULK_MIN_CONF = _DET["bulk_min_conf"]
RESCUE_MIN_CONF = _DET["rescue_min_conf"]
MIN_PLAUSIBLE_PRICE = _DET["min_plausible_price"]
PRICE_MIN_DIGITS = _DET["price_min_digits"]
MIN_CLIENT_SIDE = _DET["min_client_side"]
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
MAX_STACK = _S["game_facts"]["max_stack"]
SUGGESTION_RADIO_DX = _S["detect"]["suggestion_radio_dx"]
CONVERT_GRADES = _S["game_facts"]["convert_grades"]
CONVERT_ROW_COUNT = _S["game_facts"]["convert_rows"]
CONVERT_SET_TO_CORE_ROWS = _S["game_facts"]["convert_set_to_core_rows"]
CONVERT_TAB = _S["game_facts"]["convert_tab"]
_ALZ_WORD = _S["text"]["alz_word"]
SERVER_LAG_TEXT = re.compile(_S["text"]["server_lag"],
                             re.IGNORECASE)
SERVER_LAG_IDLE = _S["timing"]["server_lag_idle"]
SERVER_LAG_PIXELS = _S["detect"]["server_lag_pixels"]
SERVER_LAG_SURE = _S["detect"]["server_lag_sure"]
SERVER_LAG_RED = _S["detect"]["server_lag_red"]
SERVER_LAG_GREEN = tuple(_S["detect"]["server_lag_green"])
SERVER_LAG_BLUE = _S["detect"]["server_lag_blue"]
SERVER_LAG_BUDGET = _S["timing"]["server_lag_budget"]
VENDOR_TAB_WORDS = {w.strip().lower() for w in
                    _S["text"]["vendor_tab_words"].split("|")}
CONVERT_INVENTORY_TAB = _S["game_facts"]["convert_inventory_tab"]

GRID = _S["game_facts"]["grid_size"]
ACTION_GAP = _S["timing"]["action_gap"]
PARK_SETTLE = _S["timing"]["park_settle"]
TAB_SETTLE = _S["timing"]["tab_settle"]
DIALOG_TIMEOUT = _S["timing"]["dialog_timeout"]
SEARCH_TIMEOUT = _S["timing"]["search_timeout"]
SEARCH_RETRIES = _S["timing"]["search_retries"]
ALZ_SEARCH = None
_NOT_DIGIT = re.compile("[^0-9]")


_STEPS = []


@contextlib.contextmanager
def step(label):
    started = time.perf_counter()
    try:
        yield
    finally:
        _STEPS.append((label, (time.perf_counter() - started) * 1000))


_PHASES = []


@contextlib.contextmanager
def phase(label):
    started = time.perf_counter()
    try:
        yield
    finally:
        _PHASES.append((label, (time.perf_counter() - started) * 1000))


def phases_reset():
    _PHASES.clear()


def phases_table(title):
    total = sum(ms for _l, ms in _PHASES)
    rolled = {}
    for label, ms in _PHASES:
        seen = rolled.setdefault(label, [0, 0.0])
        seen[0] += 1
        seen[1] += ms
    print("")
    print(f"  {title}")
    print(f"  {'#':>3}  {'ms':>10}  {'share':>6}  {'n':>4}  {'each':>8}  phase")
    for i, (label, (times, ms)) in enumerate(
            sorted(rolled.items(), key=lambda kv: -kv[1][1]), start=1):
        print(f"  {i:>3}  {ms:>10,.1f}  "
              f"{(ms / total * 100) if total else 0:>5.1f}%  {times:>4}  "
              f"{ms / times:>8,.1f}  {label}")
    print(f"       {total:>10,.1f}  100.0%")
    return total


def steps_reset():
    _STEPS.clear()


def steps_table(title):
    total = sum(ms for _l, ms in _STEPS)
    print("")
    print(f"  {title}")
    print(f"  {'#':>3}  {'ms':>9}  {'share':>6}  step")
    for i, (label, ms) in enumerate(_STEPS, start=1):
        print(f"  {i:>3}  {ms:>9.1f}  {(ms / total * 100) if total else 0:>5.1f}%"
              f"  {label}")
    print(f"       {total:>9.1f}  100.0%  TOTAL")
    return total


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


def _centre(frac, rect=None):
    x0, y0, x1, y1 = _box(frac, rect)
    return ((x0 + x1) // 2, (y0 + y1) // 2)


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
        recording_on()
    else:
        recording_off()
    return FRAMES_ON


VIDEO_DIR = Path(__file__).resolve().parent / "debug_video"
_TAPE = None


class _Tape:
    def __init__(self, knobs):
        import threading
        self.fps = max(1, int(knobs["video_fps"]))
        self.seconds = max(1, int(knobs["video_seconds"]))
        self.keep = max(1, int(knobs["keep_videos"]))
        self.scale = float(knobs["video_scale"])
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._roll, daemon=True)
        self.reel = 0

    def start(self):
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        self.thread.start()
        return self

    def _prune(self):
        reels = sorted(VIDEO_DIR.glob("*.mp4"), key=lambda f: f.stat().st_mtime)
        for spent in reels[:max(0, len(reels) - self.keep)]:
            try:
                spent.unlink()
            except OSError:
                pass

    def _open(self, size):
        import cv2
        self.reel += 1
        out = VIDEO_DIR / f"{self.reel:04d}_{time.strftime('%H%M%S')}.mp4"
        return cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"),
                               self.fps, size)

    def _roll(self):
        import cv2
        import numpy
        import mss
        writer = None
        started = 0.0
        gap = 1.0 / self.fps
        with mss.MSS() as sct:
            where = sct.monitors[1]
            while not self.stop.is_set():
                due = time.monotonic() + gap
                try:
                    frame = numpy.asarray(sct.grab(where))[:, :, :3]
                    if self.scale != 1.0:
                        frame = cv2.resize(frame, None, fx=self.scale,
                                           fy=self.scale,
                                           interpolation=cv2.INTER_AREA)
                    if writer is None or (time.monotonic() - started
                                          >= self.seconds):
                        if writer is not None:
                            writer.release()
                            self._prune()
                        writer = self._open((frame.shape[1], frame.shape[0]))
                        started = time.monotonic()
                    writer.write(frame)
                except Exception:
                    pass
                left = due - time.monotonic()
                if left > 0:
                    self.stop.wait(left)
        if writer is not None:
            writer.release()
            self._prune()


def recording_on():
    global _TAPE
    if _TAPE is not None:
        return VIDEO_DIR
    knobs = load_shared()["debug"]
    try:
        _TAPE = _Tape(knobs).start()
    except Exception as exc:
        print(f"  no screen recording: {type(exc).__name__}: {exc}")
        _TAPE = None
        return None
    print(f"  recording -> {VIDEO_DIR} ({knobs['video_seconds']}s a reel, "
          f"{knobs['keep_videos']} kept)")
    return VIDEO_DIR


def recording_off() -> None:
    global _TAPE
    if _TAPE is None:
        return
    _TAPE.stop.set()
    _TAPE.thread.join(timeout=load_shared()["timing"]["dialog_timeout"])
    _TAPE = None


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


_FRAME_QUEUE = None
_FRAME_SCRIBE = None
_FRAMES_DROPPED = 0


def _scribe():
    while True:
        job = _FRAME_QUEUE.get()
        try:
            if job is None:
                return
            out, image = job
            image.save(out)
        except Exception as exc:
            print(f"  could not write {job[0].name}: {exc}")
        finally:
            _FRAME_QUEUE.task_done()


def _frames_waiting():
    global _FRAME_QUEUE, _FRAME_SCRIBE
    if _FRAME_SCRIBE is not None and _FRAME_SCRIBE.is_alive():
        return _FRAME_QUEUE
    import queue
    import threading
    _FRAME_QUEUE = queue.Queue(
        maxsize=int(load_shared()["debug"]["frames_queued"]))
    _FRAME_SCRIBE = threading.Thread(target=_scribe, daemon=True)
    _FRAME_SCRIBE.start()
    return _FRAME_QUEUE


def frames_written() -> None:
    if _FRAME_QUEUE is None:
        return
    _FRAME_QUEUE.join()
    if _FRAMES_DROPPED:
        print(f"  {_FRAMES_DROPPED} frame(s) went unwritten; the writer could "
              f"not keep up")


def snap(label: str, image=None) -> "Path | None":
    global _FRAME_N, _FRAMES_DROPPED
    if not FRAMES_ON:
        return None
    _FRAME_N += 1
    if _FRAME_N == 1:
        prune_frames()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("_") or "frame"
    out = FRAME_DIR / f"{_FRAME_N:05d}_{safe}.png"
    try:
        waiting = _frames_waiting()
    except Exception as exc:
        print(f"  no frame writer: {type(exc).__name__}: {exc}")
        return None
    try:
        waiting.put_nowait((out, grab() if image is None else image))
    except Exception:
        _FRAMES_DROPPED += 1
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


_OWN_CTRL = 0


@contextlib.contextmanager
def _own_ctrl():
    global _OWN_CTRL
    _OWN_CTRL += 1
    try:
        yield
    finally:
        _OWN_CTRL -= 1


def watch_for_stop(verbose=True):
    import _thread
    import threading
    from open_inventory import _user32
    shared = load_shared()
    vk = shared["input"]["VK_CONTROL"]
    presses = int(shared["run"]["stop_key_presses"])
    window = float(shared["run"]["stop_key_window"])
    gap = float(shared["timing"]["stop_key_poll"])

    def watch():
        seen, held = [], False
        while True:
            time.sleep(gap)
            if _OWN_CTRL:
                seen, held = [], False
                continue
            down = bool(_user32.GetAsyncKeyState(vk) & 0x8000)
            if down and not held:
                at = time.monotonic()
                seen = [t for t in seen if at - t <= window] + [at]
                if len(seen) >= presses:
                    print(f"{chr(10)}  Ctrl {presses} times: stopping the "
                          f"run the way Ctrl+C would.")
                    _thread.interrupt_main()
                    return
            held = down

    threading.Thread(target=watch, daemon=True).start()
    if verbose:
        print(f"  press Ctrl {presses} times within {window:g}s to stop, "
              f"from the game or anywhere else")


def ctrl_click(x: int, y: int) -> None:
    hold_if_busy()
    from open_inventory import _user32, _Input, _event
    keys = load_shared()["input"]
    vk = keys["VK_CONTROL"]

    _user32.SetCursorPos(int(x), int(y))
    time.sleep(HOVER_SETTLE)
    with _own_ctrl():
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
    hold_if_busy()
    from open_inventory import press
    keys = load_shared()["input"]
    for _ in range(clear):
        press(keys["VK_BACK"])
        time.sleep(CLEAR_GAP)
    for ch in str(int(value)):
        press(keys[f"VK_{ch}"])
        time.sleep(KEY_GAP)


def _tesseract(prepared, psm, whitelist=None):
    buf = io.BytesIO()
    prepared.save(buf, "PNG")
    args = [TESSERACT, "stdin", "stdout", "--psm", str(psm)]
    if whitelist:
        args += ["-c", "tessedit_char_whitelist=" + whitelist]
    run = subprocess.run(args, input=buf.getvalue(), capture_output=True,
                         timeout=OCR_TIMEOUT)
    return run.stdout.decode("utf-8", "replace")


_GROUPED = re.compile(r"^\d+(,\d{3})+$")
_WEDGED = re.compile(r"\d[A-Za-z]|[A-Za-z]\d")
_SEPARATOR = re.compile(r"(?<=\d)[.,\s]+(?=\d)")


def _digits(text):
    cleaned = text or ""
    label = re.search(_ALZ_WORD, cleaned, flags=re.IGNORECASE)
    if label:
        cleaned = cleaned[:label.start()]
    if _WEDGED.search(cleaned):
        return None
    cleaned = _SEPARATOR.sub(",", cleaned)
    grouped = re.sub(r"[^0-9,]", "", cleaned).strip(",")
    if "," in grouped and not _GROUPED.match(grouped):
        return None
    cleaned = re.sub(r"[^0-9]", "", cleaned)
    return int(cleaned) if cleaned else None


def read_money(image, box):
    box = tuple(box)
    if not has_ink(image, box):
        return None
    value = _digits(read_line(image, box))
    if value is not None:
        return value
    for prepared in (prep_for_text(image, box, OCR_SCALE, OCR_BORDER),
                     warm_text(image, box, OCR_SCALE, OCR_BORDER),
                     isolate_digits(image, box)):
        if prepared is None:
            continue
        value = _digits(_tesseract(prepared, ROW_PSM, DIGIT_WHITELIST))
        if value is not None:
            return value
    return None


def read_money_all(image, box):
    box = tuple(box)
    if not has_ink(image, box):
        return []
    seen = []
    value = _digits(read_line(image, box))
    if value is not None:
        seen.append(value)
    for prepared in (prep_for_text(image, box, OCR_SCALE, OCR_BORDER),
                     warm_text(image, box, OCR_SCALE, OCR_BORDER),
                     isolate_digits(image, box)):
        if prepared is None:
            continue
        value = _digits(_tesseract(prepared, ROW_PSM, DIGIT_WHITELIST))
        if value is not None and value not in seen:
            seen.append(value)
    return seen


def alz_band():
    frac = (_measured().get("regions") or {}).get("alz_search")
    return _box(tuple(frac)) if frac else _box(ALZ_SEARCH_F)


def balance_box():
    band = alz_band()
    measured = tuple(load()["inventory"]["alz_box"])
    return (min(band[0], measured[0]), min(band[1], measured[1]),
            max(band[2], measured[2]), max(band[3], measured[3]))


def read_balance_from(image):
    return read_money(image, balance_box())


def undercut(price):
    by = int(load_shared()["run"]["undercut_by"])
    if by <= 0 or price is None:
        return price
    lowered = price - by
    return lowered if lowered >= MIN_PLAUSIBLE_PRICE else price


def panel_suggestion(panel):
    box = tuple(panel["suggestion_boxes"][-1])
    value = read_money(grab(), box)
    click(box[0] - SUGGESTION_RADIO_DX, (box[1] + box[3]) // 2)
    if value is None:
        value = read_money(grab(), box)
    if value and value >= MIN_PLAUSIBLE_PRICE:
        return value
    return None


def click(x: int, y: int, settle: float = None) -> None:
    hold_if_busy()
    shared = load_shared()
    _button(shared["input"]["MOUSEEVENTF_LEFTDOWN"],
            shared["input"]["MOUSEEVENTF_LEFTUP"], x, y,
            shared["timing"]["action_gap"] if settle is None else settle)
    snap(f"click_{x}_{y}")


def right_click(x: int, y: int, settle: float = None) -> None:
    hold_if_busy()
    shared = load_shared()
    _button(shared["input"]["MOUSEEVENTF_RIGHTDOWN"],
            shared["input"]["MOUSEEVENTF_RIGHTUP"], x, y,
            shared["timing"]["action_gap"] if settle is None else settle)
    snap(f"rightclick_{x}_{y}")


def prep_for_text(image: Image.Image, box, scale: int, border=0):
    crop = image.crop(box).convert("L")
    crop = crop.resize((crop.width * scale, crop.height * scale),
                       Image.LANCZOS)
    out = ImageOps.autocontrast(ImageOps.invert(crop))
    return ImageOps.expand(out, border=border, fill=255) if border else out


def warm_text(image: Image.Image, box, scale: int, border=0):
    r, g, b = image.crop(box).convert("RGB").split()
    warm = ImageChops.subtract(r, ImageChops.lighter(g, b))
    warm = warm.resize((warm.width * scale, warm.height * scale),
                       Image.LANCZOS)
    out = ImageOps.autocontrast(ImageOps.invert(warm))
    return ImageOps.expand(out, border=border, fill=255) if border else out


def isolate_digits(image: Image.Image, box, scale: int = None):
    scale = OCR_SCALE if scale is None else scale
    crop = image.crop(box).convert("RGB")
    crop = crop.resize((crop.width * scale, crop.height * scale),
                       Image.LANCZOS)
    px = crop.load()
    mask = Image.new("L", crop.size, 255)
    m = mask.load()
    for y in range(crop.height):
        for x in range(crop.width):
            red, green, blue = px[x, y]
            hi, lo = max(red, green, blue), min(red, green, blue)
            if hi > WARM_MIN_BRIGHT and hi - lo > WARM_MIN_SATURATION:
                m[x, y] = 0
    bbox = ImageOps.invert(mask).getbbox()
    if not bbox:
        return None
    if (bbox[3] - bbox[1]) > crop.height * ALZ_MAX_TEXT_HEIGHT:
        return None
    return ImageOps.expand(mask.crop(bbox), border=OCR_BORDER, fill=255)


def has_ink(image: Image.Image, box) -> bool:
    lo, hi = image.crop(box).convert("L").getextrema()
    return hi - lo >= INK_CONTRAST_MIN


def ocr_spans(image: Image.Image, box, scale: int = None,
              min_conf: float = None):
    scale = OCR_SCALE if scale is None else scale
    min_conf = OCR_MIN_CONF if min_conf is None else min_conf
    if not has_ink(image, box):
        return []
    buf = io.BytesIO()
    prep_for_text(image, box, scale).save(buf, "PNG")
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
        left = box[0] + int(row["left"]) / scale
        right = left + int(row["width"]) / scale
        y = box[1] + int(row["top"]) / scale + int(row["height"]) / scale / 2
        found.append((text, round(conf),
                      (round((left + right) / 2), round(y)), round(right)))
    return found


def ocr(image: Image.Image, box, scale: int = None, min_conf: float = None):
    return [(text, conf, point)
            for text, conf, point, _right in ocr_spans(image, box, scale,
                                                       min_conf)]




def read_line(image: Image.Image, box, scale: int = None, border: int = 0):
    scale = OCR_SCALE if scale is None else scale
    if not has_ink(image, box):
        return ""
    buf = io.BytesIO()
    prep_for_text(image, box, scale, border).save(buf, "PNG")
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
    buf = io.BytesIO()
    prep_for_text(image, tight, OCR_SCALE, OCR_BORDER).save(buf, "PNG")
    run = subprocess.run(
        [TESSERACT, "stdin", "stdout", "--psm", ROW_PSM,
         "-c", "tessedit_char_whitelist=" + DIGIT_WHITELIST],
        input=buf.getvalue(), capture_output=True, timeout=OCR_TIMEOUT)
    digits = _NOT_DIGIT.sub("", run.stdout.decode("utf-8", "replace"))
    return int(digits) if digits else None


def read_digits(image: Image.Image, box, scale: int = None):
    scale = OCR_SCALE if scale is None else scale
    if not has_ink(image, box):
        return None
    buf = io.BytesIO()
    prep_for_text(image, box, scale).save(buf, "PNG")
    run = subprocess.run(
        [TESSERACT, "stdin", "stdout", "--psm", DIGIT_PSM,
         "-c", "tessedit_char_whitelist=" + DIGIT_WHITELIST],
        input=buf.getvalue(), capture_output=True, timeout=OCR_TIMEOUT)
    digits = _NOT_DIGIT.sub("", run.stdout.decode("utf-8", "replace"))
    return int(digits) if digits else None


def find_alz(image: Image.Image, search=None):
    search = search or alz_band()
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


def dialog_words(image=None):
    try:
        seen = ocr(image if image is not None else grab(),
                   _box(DIALOG_BUTTONS_F))
    except Exception:
        return []
    words = {w.lower() for w in ACTION_BUTTON_WORDS}
    return sorted({text.strip() for text, _conf, _point in seen
                   if text.strip().lower() in words})


def _alz_candidates(image):
    x, y, w, h = _client_rect()
    seen = []
    for top in range(y, y + h - ALZ_MIN_HEIGHT, ALZ_SWEEP_STEP):
        band = (x, top, x + w, min(y + h, top + ALZ_SWEEP_HEIGHT))
        for text, _conf, point, right in ocr_spans(image, band):
            if not re.search(_ALZ_WORD, text, flags=re.IGNORECASE):
                continue
            look = (max(band[0], right - ALZ_LABEL_BACK),
                    max(band[1], point[1] - ALZ_LINE_HALF),
                    right, min(band[3], point[1] + ALZ_LINE_HALF))
            gold = find_alz(image, look)
            if gold is None:
                continue
            box = (gold[0] - ALZ_BAND_LEFT, gold[1] - ALZ_BAND_PAD,
                   right + ALZ_BAND_PAD, gold[3] + ALZ_BAND_PAD)
            value = read_money(image, box)
            if value is None or value < MIN_PLAUSIBLE_BALANCE:
                continue
            seen.append(((gold[1] + gold[3]) // 2, box, value))
    lines = []
    for row, box, value in sorted(seen):
        if lines and row - lines[-1][0][0] <= ALZ_LINE_HALF:
            lines[-1].append((row, box, value))
        else:
            lines.append([(row, box, value)])
    out = []
    for line in lines:
        tally = collections.Counter(value for _row, _box, value in line)
        winner = tally.most_common(1)[0][0]
        out.append((next(b for _row, b, v in line if v == winner), winner))
    return out


def locate_alz(verbose=True):
    from open_inventory import VK_I, press
    say = print if verbose else (lambda *a: None)
    found = _alz_candidates(grab())
    if not found:
        say("  nothing on screen reads as a balance; pressing I and looking "
            "again")
        press(VK_I)
        time.sleep(ACTION_GAP)
        park()
        snap("alz_locate_after_I")
        found = _alz_candidates(grab())
    say(f"  {len(found)} place(s) on screen read as a balance:")
    for box, value in found:
        say(f"    {box}  {value:,}")
    if not found:
        return None
    kept = found
    if len(found) > 1:
        say("  closing the Inventory to see which one goes away")
        press(VK_I)
        time.sleep(ACTION_GAP)
        park()
        shut = grab()
        snap("alz_locate_panel_shut")
        press(VK_I)
        time.sleep(ACTION_GAP)
        park()
        kept = []
        for box, value in found:
            if read_money(shut, box) == value:
                say(f"    {box} still reads {value:,} with the panel shut, "
                    f"so it is not the balance")
                continue
            kept.append((box, value))
    if len(kept) != 1:
        say(f"  {len(kept)} left after that, so nothing was measured")
        return None
    box, value = kept[0]
    remember_band("alz_search", box)
    say(f"  balance band {box} reading {value:,}, remembered for "
        f"{resolution_key()}")
    return box


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


def _comb(profile, shared, base, offset, fixed=None):
    spread = profile / (profile.mean() or 1)
    best = None
    pitch = fixed or shared * PANEL_SCALE_LOW
    while pitch <= (fixed or shared * PANEL_SCALE_HIGH):
        span = round(pitch * GRID)
        room = len(spread) - span
        if room > 0:
            score = np.zeros(room)
            for k in range(GRID + 1):
                at = round(k * pitch)
                score += spread[at:at + room]
            expect = base + offset * pitch / shared
            lo = max(0, round(expect - pitch / 2))
            hi = min(room, round(expect + pitch / 2) + 1)
            if hi > lo:
                first = lo + int(score[lo:hi].argmax())
                if best is None or score[first] > best[0]:
                    best = (float(score[first]), pitch, first)
        if fixed:
            break
        pitch += shared * PANEL_SCALE_STEP
    return best


def _panel_grid(image, anchor, verbose=True):
    say = print if verbose else (lambda *a: None)
    layout = load_shared()["panel_layout"]
    s1x, s1y = layout["slot_one"]
    spx, spy = layout["slot_pitch"]
    cx, cy, cw, ch = _client_rect()
    ends = (PANEL_SCALE_LOW, PANEL_SCALE_HIGH)
    near_x, far_x = s1x - spx / 2, s1x - spx / 2 + GRID * spx
    near_y, far_y = s1y - spy / 2, s1y - spy / 2 + GRID * spy
    x0 = max(cx, round(anchor[0] + min(near_x * s for s in ends)))
    x1 = min(cx + cw, round(anchor[0] + max(far_x * s for s in ends)))
    y0 = max(cy, round(anchor[1] + min(near_y * s for s in ends)))
    y1 = min(cy + ch, round(anchor[1] + max(far_y * s for s in ends)))
    grey = np.asarray(image.convert("L"), dtype=float)[y0:y1, x0:x1]
    if grey.shape[0] <= GRID or grey.shape[1] <= GRID:
        raise RuntimeError(
            f"the panel window {(x0, y0, x1, y1)} from anchor {anchor} has "
            f"nothing in it to fit a grid to. Nothing measured.")
    down = _comb((np.abs(np.diff(grey, axis=1)) > PANEL_RULE_CONTRAST
                  ).sum(axis=0).astype(float), spx, anchor[0] - x0, near_x)
    across = None if down is None else _comb(
        (np.abs(np.diff(grey, axis=0)) > PANEL_RULE_CONTRAST
         ).sum(axis=1).astype(float), spy, anchor[1] - y0, near_y,
        spy * down[1] / spx)
    if down is None or across is None:
        raise RuntimeError(
            f"no {GRID}x{GRID} grid of rules fits in {(x0, y0, x1, y1)} from "
            f"anchor {anchor}. Nothing measured.")
    pitch = (down[1], across[1])
    one = (x0 + down[2] + pitch[0] / 2, y0 + across[2] + pitch[1] / 2)
    say(f"  grid fitted in {(x0, y0, x1, y1)}: pitch "
        f"{pitch[0]:.1f}x{pitch[1]:.1f} against a shared {spx}x{spy}, "
        f"slot (1,1) at {one[0]:.0f},{one[1]:.0f}, "
        f"scores {down[0]:.0f} and {across[0]:.0f}")
    return one, pitch, pitch[0] / spx


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
    one, (spx, spy), scale = _panel_grid(image, anchor, verbose)
    t1x = one[0] + (layout["tab_one"][0] - layout["slot_one"][0]) * scale
    t1y = one[1] + (layout["tab_one"][1] - layout["slot_one"][1]) * scale
    tp = layout["tab_pitch"] * scale

    tabs = {str(k + 1): [round(t1x + tp * k), round(t1y)]
            for k in range(GRID)}
    slots = {}
    for row in range(1, GRID + 1):
        for col in range(1, GRID + 1):
            slots[f"{row}x{col}"] = [round(one[0] + spx * (col - 1)),
                                     round(one[1] + spy * (row - 1))]
    if FRAMES_ON:
        marked = image.copy()
        pen = ImageDraw.Draw(marked)
        half = round(SLOT_HALF * scale)
        for point in slots.values():
            pen.rectangle((point[0] - half, point[1] - half,
                           point[0] + half, point[1] + half), outline="red")
        for point in tabs.values():
            pen.ellipse((point[0] - half, point[1] - half,
                         point[0] + half, point[1] + half), outline="lime")
        pen.rectangle(alz, outline="yellow")
        snap("inventory_grid_as_fitted", marked)
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
            f"window ({cx},{cy} {cw}x{chh}), e.g. {outside[0]}, anchor "
            f"{anchor}. Nothing measured.")

    return {
        "alz_box": list(alz),
        "anchor": list(anchor),
        "tabs": tabs,
        "tab_pitch": tp,
        "slots": slots,
        "slot_pitch": [spx, spy],
        "panel_scale": scale,
        "placed_from": "grid fitted to the panel, tabs from panel_layout",
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


def _gift_reads(word, image=None):
    image = image if image is not None else grab()
    want = word.strip().lower()
    return [point for text, _conf, point in ocr(image, _box(GIFT_WINDOW_F))
            if text.strip().lower() == want]


def gift_column(points):
    best = []
    for point in points:
        together = [p for p in points
                    if abs(p[0] - point[0]) <= GIFT_COLUMN_SPREAD]
        if len(together) > len(best):
            best = together
    return sorted(best, key=lambda p: p[1])


def calibrate_gifts(verbose=True):
    say = print if verbose else (lambda *a: None)
    icon = _point(GIFT_ICON_F)
    say(f"  the gift box at {list(icon)}")
    click(*icon)
    time.sleep(_S["timing"]["action_gap"])

    listed, shut = [], []
    deadline = time.monotonic() + DIALOG_TIMEOUT
    while time.monotonic() < deadline:
        image = grab()
        listed = gift_column(_gift_reads(RECEIPT_WORD, image))
        shut = _gift_reads(CLOSE_WORD, image)
        if len(listed) >= GIFT_BOXES and shut:
            break
        time.sleep(POLL_GAP)

    if len(listed) < GIFT_BOXES or not shut:
        snap("gift_window_short")
        say(f"  the gift box shows {len(listed)} {RECEIPT_WORD} button(s) in "
            f"one column and {len(shut)} {CLOSE_WORD}, not {GIFT_BOXES} and "
            f"one; leaving the gift points unmeasured")
        if shut:
            click(*shut[0])
            park()
        return None

    taking = listed[:GIFT_BOXES]
    say(f"  {RECEIPT_WORD} at {[list(p) for p in taking]}")
    say(f"  {CLOSE_WORD} at {list(shut[0])}")
    click(*shut[0])
    park()
    return {"icon": list(icon),
            "receive": [list(p) for p in taking],
            "close": list(shut[0])}


def calibrate_shop(verbose=True):
    say = print if verbose else (lambda *a: None)
    image = grab()

    top_strip = _box(TOP_STRIP_F)
    words = ocr(image, top_strip)
    named = {t.lower(): (c, p) for t, c, p in words}

    said = [p for t, _c, p in words if t.lower() == "register"]
    if not said:
        raise RuntimeError(
            "the Register tab was not found, so the Trade window is not open "
            "on a tab this can measure. Nothing written.")
    reg = min(said, key=lambda p: p[1])
    if len(said) > 1:
        say(f"  the word Register reads at {said}; the tab is the highest of "
            f"them, the rest name the panel below it")
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
    out = {}

    sort_band = _box(PURCHASE_SORT_BAND_F)
    deadline = time.monotonic() + DIALOG_TIMEOUT
    image, sort_words, seen, anchor = None, [], "", None
    while True:
        image = grab()
        sort_words = ocr(image, sort_band)
        seen = " ".join(t for t, _, _ in sort_words)
        anchor = next((p for t, _c, p in sort_words
                       if "price" in re.sub(r"[^a-z]", "", t.lower())), None)
        if anchor is not None or time.monotonic() >= deadline:
            break
        say(f"  the sort band reads {seen!r}; the Purchase tab is still "
            f"arriving")
        time.sleep(POLL_GAP)
    if not sort_words:
        raise RuntimeError(
            f"nothing read in the sort band {sort_band}. The Purchase tab may "
            f"not have opened, or the band is wrong for this screen.")
    if anchor is None:
        raise RuntimeError(
            f"no word naming the sort was read in {sort_band} within "
            f"{DIALOG_TIMEOUT:g}s; it read {seen!r}. Nothing written.")
    out["purchase_sort_region"] = [anchor[0] - SORT_PAD_LEFT,
                                   anchor[1] - SORT_PAD_Y,
                                   anchor[0] + SORT_PAD_RIGHT,
                                   anchor[1] + SORT_PAD_Y]
    out["purchase_sort_text_seen"] = seen
    out["purchase_sort_anchor"] = list(anchor)
    say(f"  sort reads {out['purchase_sort_text_seen']!r} -> region "
        f"{out['purchase_sort_region']}")

    favs = shop.get("favourites") or []
    if not favs:
        raise RuntimeError("no favourite slots measured; cannot populate the "
                           "offers table to find the Buy column.")
    fx, fy = favs[0]
    table_band = _box(PURCHASE_TABLE_BAND_F)
    button_band = _box(PURCHASE_BUTTON_BAND_F)
    image, seen, offers = None, [], False
    for attempt in range(1, SEARCH_RETRIES + 1):
        say(f"  running favourite 1 at ({fx}, {fy}) to fill the table "
            f"(attempt {attempt}/{SEARCH_RETRIES})")
        click(fx, fy)
        park()
        deadline = time.monotonic() + SEARCH_TIMEOUT
        while time.monotonic() < deadline:
            image = grab()
            seen = ocr(image, button_band)
            if [1 for t, _, _ in seen if t.strip().lower() == "buy"]:
                offers = True
                break
            time.sleep(POLL_GAP)
        if offers:
            break
        snap(f"favourite_1_no_offers_{attempt}")
    if not offers:
        raise RuntimeError(
            f"favourite 1 returned no offers within {SEARCH_TIMEOUT}s across "
            f"{SEARCH_RETRIES} attempt(s), so the table has no rows to "
            f"measure. {button_band} read {[t for t, _c, _p in seen]}.")
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
    words = _S["text"]
    wanted = {w.strip().lower() for w in
              (words["change_word"], words["register_word"],
               words["receipt_word"])}
    marks = [p for t, _, p in ocr(image, buttons)
             if t.strip().lower() in wanted]
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
    top = int(ys[0])
    while top - pitch >= band[1] + pitch // 2:
        top -= pitch
        say(f"  a row sits at y={top} with no button that read; counting it")
    ys = [top] + [y for y in ys if y > top]
    footer = _box(REGISTER_FOOTER_BAND_F)
    want = re.sub(r"[^a-z]", "", REFRESH_WORD.lower())
    deadline = time.monotonic() + DIALOG_TIMEOUT
    refresh, seen = None, []
    while True:
        seen = ocr(grab(), footer)
        refresh = next((list(p) for t, _c, p in seen
                        if re.sub(r"[^a-z]", "", t.lower()) == want), None)
        if refresh is not None or time.monotonic() >= deadline:
            break
        time.sleep(POLL_GAP)
    if refresh is None:
        say(f"  no {REFRESH_WORD} button read in {footer} within "
            f"{DIALOG_TIMEOUT:g}s; it read {[t for t, _c, _p in seen]}. "
            f"Keeping whatever was measured before; a row that cannot be "
            f"refreshed is read as it stands.")

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
    if refresh is not None:
        out["refresh_point"] = refresh
    say(f"  table_x {out['table_x']}, scroll point {out['table_point']}")
    if refresh is not None:
        say(f"  {REFRESH_WORD} at {refresh}")
    return out


WORK_TAB = _S["game_facts"]["work_tab"]
WORK_SLOT = tuple(int(n) for n in
             _S["game_facts"]["work_slot"].split(","))


def inventory_tab_point(tab):
    tabs = load()["inventory"]["tabs"]
    key = str(int(tab))
    if key not in tabs:
        raise RuntimeError(
            f"inventory tab {key} is not in calibration.json, which has "
            f"{sorted(tabs)}")
    return tuple(tabs[key])


ACTION_BUTTON_WORDS = (_S["text"]["confirm_word"], _S["text"]["dismiss_word"],
                       _S["text"]["receipt_word"], _S["text"]["register_word"])

RECEIPT_WORD = _S["text"]["receipt_word"]
CLOSE_WORD = _S["text"]["close_word"]
GIFT_BOXES = int(_S["game_facts"]["gift_boxes"])
GIFT_COLUMN_SPREAD = int(_S["detect"]["gift_column_spread"])
GIFT_ICON_F = tuple(_S["regions"]["gift_icon"])
GIFT_WINDOW_F = tuple(_S["regions"]["gift_window"])


def panel_quantity(panel, want_price, say=lambda *a: None):
    box = panel.get("net_sales_box")
    if not box:
        raise RuntimeError(
            "the net sales box was never measured, so a price cannot be "
            "checked. Recalibrate before listing anything.")
    for attempt in range(1, PANEL_REREADS + 2):
        net = read_money(grab(), tuple(box)) or 0
        if net and net % want_price == 0:
            return net // want_price
        say(f"    read {attempt}: net sales {net:,} is not a whole number of "
            f"{want_price:,}")
        time.sleep(PANEL_REREAD_GAP)
    return None

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


def market_unit(name):
    prices = _read(OUT).get("market", {}).get("unit_price", {})
    slot = favourite_slot_of(name)
    if slot is None:
        return 0
    return int(prices.get(str(slot)) or 0)


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


def _peaks(profile, cut_at, merge_gap, keep=None):
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
    if keep and len(merged) > keep:
        merged = sorted(sorted(merged, key=lambda i: profile[i],
                               reverse=True)[:keep])
    return merged


def lag_ink(image, box) -> int:
    patch = np.asarray(image.crop(box).convert("RGB"), dtype=int)
    red, green, blue = patch[..., 0], patch[..., 1], patch[..., 2]
    return int(((red > SERVER_LAG_RED)
                & (green > SERVER_LAG_GREEN[0])
                & (green < SERVER_LAG_GREEN[1])
                & (blue < SERVER_LAG_BLUE)).sum())


def server_busy(image=None) -> bool:
    image = image if image is not None else grab()
    box = _box(SERVER_LAG_BAND_F)
    try:
        ink = lag_ink(image, box)
        if ink < SERVER_LAG_PIXELS:
            return False
        if ink >= SERVER_LAG_SURE:
            return True
        seen = read_line(image, box)
    except Exception:
        return False
    return SERVER_LAG_TEXT.search(seen) is not None


_HOLDING = False


def hold_if_busy() -> None:
    global _HOLDING
    if _HOLDING:
        return
    _HOLDING = True
    try:
        wait_out_server_lag(verbose=True)
    finally:
        _HOLDING = False


def wait_out_server_lag(verbose=True):
    if not server_busy():
        return 0.0
    snap("server_busy")
    started = time.monotonic()
    deadline = started + SERVER_LAG_BUDGET
    if verbose:
        print(f"  the server is not answering; idling {SERVER_LAG_IDLE:g}s "
              f"rather than reading a screen it cannot serve")
    while time.monotonic() < deadline:
        time.sleep(SERVER_LAG_IDLE)
        if not server_busy():
            waited = time.monotonic() - started
            snap("server_answered")
            if verbose:
                print(f"  the server is answering again after {waited:.0f}s")
            return waited
        if verbose:
            print(f"  still not answering; idling another "
                  f"{SERVER_LAG_IDLE:g}s")
    snap("server_still_down")
    raise RuntimeError(
        f"the server did not answer within {SERVER_LAG_BUDGET:g}s.")


def vendor_open(image=None) -> bool:
    image = image if image is not None else grab()
    try:
        words = {t.strip().lower()
                 for t, _c, _p in ocr(image, _box(VENDOR_TITLE_BAND_F))}
    except Exception:
        return False
    return VENDOR_TAB_WORDS <= words


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
        deadline = time.monotonic() + span
        while time.monotonic() < deadline:
            if vendor_open():
                snap(f"vendor_open_attempt_{attempt}")
                return True
            time.sleep(POLL_GAP)
        image = grab()
        snap(f"vendor_not_seen_attempt_{attempt}")
        if verbose:
            print(f"    after {span:g}s the title band "
                  f"{_box(VENDOR_TITLE_BAND_F)} reads "
                  f"{[t for t, _c, _p in ocr(image, _box(VENDOR_TITLE_BAND_F))]}")
        if attempt == 1 and verbose:
            print(f"    pressing N again would close it if it is open and "
                  f"only unrecognised")
    return False


def vendor_tab_point(name, image=None):
    image = image if image is not None else grab()
    want = re.sub(r"[^a-z]", "", name.lower())
    for text, _c, point in ocr(image, _box(VENDOR_TAB_BAND_F)):
        if re.sub(r"[^a-z]", "", text.lower()) == want:
            return point
    return None


CRAFT_TAB = _S["game_facts"]["craft_tab"]
CRAFT_KEY_SLOT = tuple(_S["game_facts"]["craft_key_slot"])
REFRESH_WORD = _S["game_facts"]["refresh_word"]
HELD_OF_NEEDED = _S["game_facts"]["held_of_needed"]
CRAFT_TIER_WORDS = _S["game_facts"]["craft_tier_words"].split("|")
CRAFT_RECIPE_WORDS = _S["game_facts"]["craft_recipe_words"].split("|")


def _craft_slots():
    fold = lambda v: re.sub(r"[^a-z0-9]", "", (v or "").lower())
    want = fold(CRAFT_RECIPE_WORDS[0])
    for slot, name in FAVOURITE_ITEMS.items():
        if fold(name) != want:
            continue
        made = int(slot)
        for other in FAVOURITE_ITEMS:
            if pair_slot(int(other)) == made:
                return int(other), made
    return None, None
CRAFT_REQUEST_WORDS = _S["game_facts"]["craft_request_words"].split("|")
CRAFT_REQUEST_WORD = _S["game_facts"]["craft_request_word"]
CRAFT_COMPLETE_WORD = _S["game_facts"]["craft_complete_word"]
CRAFT_MATERIAL_WORD = _S["game_facts"]["craft_material_word"]
CRAFT_CORES_PER_SET = _S["game_facts"]["craft_cores_per_set"]


def craft_window_open(image=None):
    image = image if image is not None else grab()
    box = _box(tuple(_REG["craft_buttons"]))
    seen = {re.sub(r"[^a-z]", "", t.lower()) for t, _c, _p in ocr(image, box)}
    return re.sub(r"[^a-z]", "", CRAFT_COMPLETE_WORD.lower()) in seen


WORD_ROW_SLACK = _DET["word_row_slack"]
TIER_ROW_SLACK = _DET["tier_row_slack"]


def _two_word_button(words, pair):
    first = re.sub(r"[^a-z]", "", pair[0].lower())
    second = re.sub(r"[^a-z]", "", pair[-1].lower())
    for text, _conf, point in words:
        if re.sub(r"[^a-z]", "", text.lower()) != first:
            continue
        after = [p for t, _c, p in words
                 if abs(p[1] - point[1]) <= WORD_ROW_SLACK and p[0] > point[0]
                 and re.sub(r"[^a-z]", "", t.lower()) == second]
        if after:
            return [(point[0] + after[0][0]) // 2, point[1]]
    return None


def calibrate_craft(verbose=True):
    say = print if verbose else (lambda *a: None)
    if not craft_window_open():
        if await_inventory(verbose=verbose) is None:
            raise RuntimeError(
                "no readable Alz balance, so the Inventory is not open and "
                "the craft key cannot be reached. Nothing written.")
        click(*inventory_tab_point(CRAFT_TAB))
        time.sleep(TAB_SETTLE)
        point = inventory_slot_point(*CRAFT_KEY_SLOT)
        say(f"  right-clicking the craft key on tab {CRAFT_TAB} slot "
            f"{CRAFT_KEY_SLOT} at {point}")
        right_click(*point)
        deadline = time.monotonic() + DIALOG_TIMEOUT
        while time.monotonic() < deadline:
            if craft_window_open():
                break
            time.sleep(POLL_GAP)
    if not craft_window_open():
        raise RuntimeError(
            f"the craft window did not open from tab {CRAFT_TAB} slot "
            f"{CRAFT_KEY_SLOT}. Nothing written.")

    image = grab()
    tiers = ocr(image, _box(tuple(_REG["craft_tiers"])))
    rows = {}
    for text, _conf, point in tiers:
        digits = re.sub(r"[^0-9]", "", text)
        if digits:
            rows.setdefault(point[1], []).append(digits)
    tier_y = None
    for y, digits in sorted(rows.items()):
        if all(any(w in d for d in digits) for w in CRAFT_TIER_WORDS):
            tier_y = y
            break
    if tier_y is None:
        raise RuntimeError(
            f"no tier row reads {CRAFT_TIER_WORDS} in "
            f"{_box(tuple(_REG['craft_tiers']))}; it read "
            f"{[t for t, _c, _p in tiers]}. Nothing written.")
    span = [p[0] for _t, _c, p in tiers if p[1] == tier_y]
    left = (min(span) + max(span)) // 2

    buttons = ocr(image, _box(tuple(_REG["craft_buttons"])))
    def button(word):
        want = re.sub(r"[^a-z]", "", word.lower())
        for text, _conf, point in buttons:
            if re.sub(r"[^a-z]", "", text.lower()) == want:
                return list(point)
        return None
    want = re.sub(r"[^a-z]", "", CRAFT_COMPLETE_WORD.lower())
    on_row = [p for t, _c, p in buttons
              if re.sub(r"[^a-z]", "", t.lower()) == want]
    if not on_row:
        raise RuntimeError(
            f"no {CRAFT_COMPLETE_WORD} button in the craft button band; it "
            f"read {[t for t, _c, _p in buttons]}. Nothing written.")
    at_y = on_row[0][1]
    right = sorted(p[0] for _t, _c, p in buttons
                   if abs(p[1] - at_y) <= WORD_ROW_SLACK
                   and p[0] > on_row[0][0])
    tail = right[0] if right else on_row[0][0]
    complete = [(on_row[0][0] + tail) // 2, at_y]

    click(left, tier_y)
    time.sleep(TAB_SETTLE)
    recipes = ocr(grab(), _box(tuple(_REG["craft_recipes"])))
    lines = []
    for text, _conf, point in sorted(recipes, key=lambda w: w[2][1]):
        if point[1] <= tier_y + TIER_ROW_SLACK:
            continue
        for line in lines:
            if abs(line["y"] - point[1]) <= WORD_ROW_SLACK:
                line["words"].append((point[0], text))
                break
        else:
            lines.append({"y": point[1], "words": [(point[0], text)]})
    recipe = None
    for line in lines:
        flat = re.sub(r"[^a-z0-9]", "",
                      " ".join(t for _x, t in sorted(line["words"])).lower())
        if all(re.sub(r"[^a-z0-9]", "", w.lower()) in flat
               for w in CRAFT_RECIPE_WORDS):
            xs = [x for x, _t in line["words"]]
            recipe = [(min(xs) + max(xs)) // 2, line["y"]]
            break
    if recipe is None:
        raise RuntimeError(
            f"no recipe under the {'-'.join(CRAFT_TIER_WORDS)} tier reads "
            f"{CRAFT_RECIPE_WORDS}; it read "
            f"{[t for t, _c, _p in recipes]}. Nothing written.")

    click(*recipe)
    time.sleep(TAB_SETTLE)
    chosen = ocr(grab(), _box(tuple(_REG["craft_buttons"])))
    request = _two_word_button(chosen, CRAFT_REQUEST_WORDS)
    if request is None:
        raise RuntimeError(
            f"no {' '.join(CRAFT_REQUEST_WORDS)} button after choosing "
            f"{' '.join(CRAFT_RECIPE_WORDS)}; the band read "
            f"{[t for t, _c, _p in chosen]}. Nothing written.")

    out = {"tier": [left, tier_y],
           "recipe": recipe,
           "request": request,
           "complete": complete,
           "material_box": list(_box(tuple(_REG["craft_material"])))}
    say(f"  tier {'-'.join(CRAFT_TIER_WORDS)} at {out['tier']}")
    say(f"  recipe {' '.join(CRAFT_RECIPE_WORDS)} at {recipe}")
    say(f"  {' '.join(CRAFT_REQUEST_WORDS)} at {request}")
    say(f"  {CRAFT_COMPLETE_WORD} All at {complete}")
    say(f"  material counter {out['material_box']}")
    return out


def calibrate_convert(verbose=True):
    say = print if verbose else (lambda *a: None)
    if _trade_window_open():
        raise RuntimeError(
            "the Agent Shop is open; the vendor cannot open over it. "
            "Nothing written.")
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
    cols = _peaks(grid.mean(axis=0), CONVERT_PEAK_CUT, CONVERT_MERGE_GAP,
                  keep=len(CONVERT_GRADES))
    rows = _peaks(grid.mean(axis=1), CONVERT_PEAK_CUT, CONVERT_MERGE_GAP,
                  keep=CONVERT_ROW_COUNT)
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


def _per_item(key, core_name):
    run = load_shared()["resupply"]
    table = run.get(key)
    if not isinstance(table, dict):
        return None if table is None else int(table)
    want = re.sub(r"[^a-z0-9]", "", (core_name or "").lower())
    for name, value in table.items():
        if re.sub(r"[^a-z0-9]", "", name.lower()) == want:
            return int(value)
    fallback = table.get("default")
    return int(fallback) if fallback is not None else None


def price_diff_threshold(core_name):
    return _per_item("price_diff_threshold", core_name)


def rows_threshold(core_name):
    return _per_item("rows_threshold", core_name)


def buy_min(core_name):
    return _per_item("buy_min", core_name)


def buy_max(core_name):
    return _per_item("buy_max", core_name)


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
    spans = ocr_spans(grab(), box)
    words = [(text, conf, point) for text, conf, point, _right in spans]

    def below(word, after_y=0):
        hits = [p for t, _c, p in words
                if t.strip().lower() == word and p[1] > after_y]
        return min(hits, key=lambda p: p[1]) if hits else None

    price = below("price")
    qty_label = below("qty")
    if price is None or qty_label is None:
        raise RuntimeError(
            f"the register panel {box} does not read as a panel: found "
            f"{[t for t, _c, _p in words]}. Nothing measured.")

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
    right = alz[0] + PANEL_LABEL_GAP

    def ends_at_label(y):
        here = [edge for t, _c, point, edge in spans
                if re.search(_ALZ_WORD, t, re.IGNORECASE)
                and abs(point[1] - y) <= PANEL_FIELD_HALF]
        return min(box[2], max(here) + PANEL_LABEL_PAD) if here else right

    out = {
        "panel_box": list(box),
        "price_field": [left, alz[1] - PANEL_FIELD_HALF,
                        right, alz[1] + PANEL_FIELD_HALF],
        "price_point": [(left + right) // 2, alz[1]],
        "qty_point": [qty[0], qty[1]],
        "qty_box": [left, qty[1] - PANEL_FIELD_HALF,
                    right, qty[1] + PANEL_FIELD_HALF],
        "suggestion_boxes": [[left, y - PANEL_FIELD_HALF,
                              ends_at_label(y), y + PANEL_FIELD_HALF]
                             for y in rows],
        "register_button": [button[0], button[1]],
    }
    if net_row is not None:
        out["net_sales_box"] = [left, net_row - PANEL_FIELD_HALF,
                                ends_at_label(net_row),
                                net_row + PANEL_FIELD_HALF]
    say(f"  price field {out['price_field']} (click {out['price_point']})")
    say(f"  quantity box {out['qty_box']} (click {out['qty_point']})")
    say(f"  {len(rows)} suggested price row(s) at y {rows}")
    say(f"  net sales box {out.get('net_sales_box')}")
    say(f"  Register button at {out['register_button']}")
    return out


def slot_half():
    return round(SLOT_HALF * (load()["inventory"].get("panel_scale") or 1))


def slot_is_empty(image, row, col):
    point = inventory_slot_point(row, col)
    half = slot_half()
    crop = image.crop((point[0] - half, point[1] - half,
                       point[0] + half, point[1] + half)).convert("L")
    data = list(crop.getdata())
    mean = sum(data) / len(data)
    stdev = (sum((v - mean) ** 2 for v in data) / len(data)) ** 0.5
    return stdev < SLOT_OCCUPIED_STDEV


def occupied_slots(image=None):
    image = image if image is not None else grab()
    grid = _S["game_facts"]["grid_size"]
    return {(row, col)
            for row in range(1, grid + 1)
            for col in range(1, grid + 1)
            if not slot_is_empty(image, row, col)}


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
    learned = {}

    def buttons_now():
        found = {}
        for text, _conf, point in ocr(grab(), dialog):
            key = text.strip().lower()
            for word in ACTION_BUTTON_WORDS:
                if key == word.lower():
                    found[word] = (int(point[0]), int(point[1]))
        return found

    def band_reads():
        return sorted({text.strip() for text, _conf, _point in ocr(grab(),
                                                                   dialog)
                       if text.strip()})

    def await_button(word):
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            here = buttons_now()
            for name, point in here.items():
                learned[f"button_{name.lower()}"] = list(point)
            if word in here:
                return here[word]
        return None

    def missing(word, after):
        snap(f"no_{word.lower()}_after_{after.lower()}")
        return (f"no {word} button appeared in {dialog} within "
                f"{budget}s after {after} on row 1; that band reads "
                f"{band_reads()}. Nothing has been withdrawn.")

    if buttons_now():
        raise RuntimeError(
            "a dialog is already open over the Register table. Close it and "
            "calibrate again; nothing was clicked.")

    row_one = tuple(shop["row_one_box"])
    before = read_line(grab(), row_one)
    lowered = before.lower()
    listed = re.search(r"\d[\d,]{2,}", before) is not None
    if listed and RECEIPT_WORD.lower() in lowered:
        receipt = (shop["button_x"], shop["row_one_y"])
        say(f"  row 1 has SOLD: {before!r}")
        say(f"  {RECEIPT_WORD} at {receipt}")
        click(*receipt)
        park(settle=False)
        accept = await_button(RECEIPT_WORD)
        if accept is None:
            raise RuntimeError(missing(RECEIPT_WORD, RECEIPT_WORD))
        say(f"  Confirm Receipt at {accept}")
        click(*accept)
        park(settle=False)
        before = read_line(grab(), row_one)
        lowered = before.lower()
        listed = re.search(r"\d[\d,]{2,}", before) is not None
        say(f"  collected; row 1 now reads {before!r}")
    if not listed or RECEIPT_WORD.lower() in lowered:
        say(f"  row 1 reads {before!r}; it is not a live listing this "
            f"pass can withdraw. Earlier positions stand.")
        return {}

    landing = first_free_slot(WORK_TAB, verbose=verbose)
    if landing is None:
        say(f"  inventory tab {WORK_TAB} is full; not walking the actions. "
            f"Earlier positions stand.")
        return {}

    change = (shop["button_x"], shop["row_one_y"])
    say(f"  row 1 is {before!r}")
    say(f"  Change at {change}")
    click(*change)
    park(settle=False)

    cancel = await_button(_S["text"]["dismiss_word"])
    if cancel is None:
        raise RuntimeError(missing(_S["text"]["dismiss_word"],
                                   _S["text"]["change_word"]))
    say(f"  Cancel at {cancel}")
    click(*cancel)
    park(settle=False)

    confirm = await_button(_S["text"]["confirm_word"])
    if confirm is None:
        raise RuntimeError(missing(_S["text"]["confirm_word"],
                                   _S["text"]["dismiss_word"]))
    say(f"  Confirmation at {confirm}")
    click(*confirm)
    park()

    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if not buttons_now():
            break
    else:
        raise RuntimeError(
            "the dialog stayed open after Confirmation on row 1; the cancel "
            "is unconfirmed.")

    after = read_line(grab(), row_one)
    say(f"  row 1 now reads {after!r}")

    tab = inventory_tab_point(WORK_TAB)
    say(f"  back to inventory tab {WORK_TAB} at {tab}; the withdrawal moves "
        f"the panel to whichever tab it landed on")
    click(*tab)
    slot = inventory_slot_point(*landing)
    if slot_is_empty(grab(), *landing):
        raise RuntimeError(
            f"tab {WORK_TAB} slot {landing} is empty after the withdrawal.")
    say(f"  listing it back from tab {WORK_TAB} slot {landing} at {slot}")
    ctrl_click(*slot)
    suggested = None
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        suggested = panel_suggestion(panel)
        if suggested is not None:
            break
        time.sleep(POLL_GAP)
    if suggested is None:
        say(f"  nothing loaded on the first ctrl-click; trying once more")
        ctrl_click(*slot)
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            suggested = panel_suggestion(panel)
            if suggested is not None:
                break
            time.sleep(POLL_GAP)
    if suggested is None:
        raise RuntimeError(
            f"nothing loaded from tab {WORK_TAB} slot {landing} after two "
            f"ctrl-clicks.")
    price = undercut(suggested)
    if price is None:
        raise RuntimeError(
            f"the panel suggests no price for what was withdrawn, so there "
            f"is nothing to list at. The item is in the bag.")
    say(f"  panel suggests {suggested:,}; listing at {price:,}")

    click(*panel["price_point"])
    type_number(price, CLEAR_PRESSES_PRICE)
    click(*panel["qty_point"])
    type_number(MAX_STACK, CLEAR_PRESSES_QTY)
    park()
    qty = panel_quantity(panel, price, say)
    if qty is None:
        raise RuntimeError(
            f"the panel will not price {price:,} against its net sales after "
            f"{PANEL_REREADS + 1} reads. Nothing listed.")
    say(f"  typed {MAX_STACK}; the net sales make it {qty}")

    click(*panel["register_button"], settle=0.0)
    learned["button_register"] = list(panel["register_button"])
    confirm = await_button(_S["text"]["confirm_word"])
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
            "the dialog stayed open after Confirmation on the relist; "
            "unconfirmed.")
    say(f"  listed {qty} at {price:,}; it lands in the lowest empty row")
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
        print("  the configured band holds no balance; sweeping the screen "
              "for one")
        if locate_alz() is None or await_inventory(verbose=True) is None:
            snap("inventory_would_not_open")
            waiting = dialog_words()
            blocked = (f" A dialog is open over the game and it swallows the "
                       f"I key; it reads {waiting}. Dismiss it and run "
                       f"again." if waiting else "")
            raise RuntimeError(
                f"no readable Alz balance after pressing I twice and "
                f"sweeping the screen; the Inventory panel is not open."
                f"{blocked} Nothing measured.")

    snap("inventory_as_measured")
    inventory = calibrate_inventory()
    remember("inventory", inventory)
    snap("inventory_after_measure")

    print("gift box:")
    gift_block = calibrate_gifts()
    if gift_block is not None:
        remember("gifts", gift_block)

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
    remember("shop", shop)

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

    craft_block = None
    if (shared["resupply"]["enable_buying"] or {}).get("Chaos Core"):
        print("craft window:")
        craft_block = calibrate_craft()
        press(VK_ESCAPE)
        time.sleep(gap)
        snap("press_escape_after_craft")

    win = find_game_window()
    measured = {
        "screen": list(screen_size()),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "game": {
            "title_seen": win[1] if win else None,
            "client_rect": win[2] if win else None,
        },
        "alz_detect": {
            "search": list(alz_band()),
            "bright": ALZ_BRIGHT,
            "saturation": ALZ_SATURATION,
            "min_pixels": ALZ_MIN_PIXELS,
            "line_half": ALZ_LINE_HALF,
        },
        "inventory": inventory,
        "shop": shop,
    }
    bands = _measured().get("regions") or {}
    if bands:
        measured["regions"] = bands
    if convert_block is not None:
        measured["convert"] = convert_block
    if craft_block is not None:
        measured["craft"] = craft_block
    if gift_block is not None:
        measured["gifts"] = gift_block

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
                "the Inventory panel would not reopen after the vendor. "
                "calibration.json is written.")
        click(*inventory["tabs"][str(facts["agent_shop_tab"])])
        time.sleep(gap)
        right_click(*inventory["slots"][f"{row}x{col}"])
        time.sleep(gap)
        park()
        if not _trade_window_open():
            raise RuntimeError(
                "the Agent Shop would not reopen after the vendor. "
                "calibration.json is written.")
        print("  the Agent Shop is open again")


def close_everything(verbose: bool = False) -> None:
    from open_inventory import VK_I, VK_ESCAPE, focus_game, press
    gap = load_shared()["timing"]["action_gap"]
    if not focus_game():
        return
    if verbose:
        print("restoring the default state:")

    if _trade_window_open() or vendor_open():
        press(VK_ESCAPE)
        time.sleep(gap)
        snap("press_escape")
        if verbose:
            print("  Escape: window closed")
    elif verbose:
        print("  no Trade window or vendor open; not pressing Escape")

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


_BIG_GLYPHS = {
    "A": (" ### ", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"),
    "C": (" ####", "#    ", "#    ", "#    ", "#    ", "#    ", " ####"),
    "D": ("#### ", "#   #", "#   #", "#   #", "#   #", "#   #", "#### "),
    "E": ("#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#####"),
    "F": ("#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#    "),
    "H": ("#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"),
    "I": ("#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "#####"),
    "N": ("#   #", "##  #", "# # #", "# # #", "#  ##", "#   #", "#   #"),
    "O": (" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "),
    "P": ("#### ", "#   #", "#   #", "#### ", "#    ", "#    ", "#    "),
    "R": ("#### ", "#   #", "#   #", "#### ", "# #  ", "#  # ", "#   #"),
    "S": (" ####", "#    ", "#    ", " ### ", "    #", "    #", "#### "),
    "T": ("#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  "),
}

_BANNER_COLOUR = {"FINISHED": "32", "STOPPED": "33", "CRASHED": "31"}


def _big_text(word, scale=2):
    rows = []
    for r in range(7):
        line = "  ".join("".join(ch * scale for ch in _BIG_GLYPHS[letter][r])
                         for letter in word if letter in _BIG_GLYPHS)
        rows.extend([line] * scale)
    return rows


def end_banner(word, note=""):
    import sys
    rows = _big_text(word)
    rule = "#" * max(len(r) for r in rows)
    body = ["", rule, ""] + rows + ["", rule]
    if note:
        body.append(f"  {word.lower()}: {note}")
    body.append("")
    plain = chr(10).join(body) + chr(10)
    stream = sys.stdout
    tee = stream if isinstance(stream, _Tee) else None
    console = tee.stream if tee is not None else stream
    shown = plain
    try:
        if hasattr(console, "isatty") and console.isatty():
            esc = chr(27)
            colour = _BANNER_COLOUR.get(word, "0")
            shown = f"{esc}[1;{colour}m{plain}{esc}[0m"
    except Exception:
        shown = plain
    if tee is not None:
        console.write(shown)
        tee.handle.write(plain)
    else:
        console.write(shown)


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
