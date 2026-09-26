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
VERIFIED = HERE / "verified_costs.json"
_VERIFIED = None
CONFIG = HERE / "config.json"
CONFIGS = HERE / "configs"
CONFIG_ENV = "CABAL_CONFIG"


def config_names():
    return sorted(p.stem for p in CONFIGS.glob("*.json"))


def config_name():
    import os
    name = (os.environ.get(CONFIG_ENV) or "").strip()
    names = config_names()
    if name not in names:
        raise SystemExit(
            f"no config chosen; pass --config with one of {names}"
            if not name else
            f"config {name!r} is not one of {names}")
    return name


def use_config(name):
    import os
    names = config_names()
    if name not in names:
        raise SystemExit(f"config {name!r} is not one of {names}")
    os.environ[CONFIG_ENV] = name
    global _CACHE
    _CACHE = None
    return name


def _overlay(base, over):
    for name, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(base.get(name), dict):
            _overlay(base[name], value)
        else:
            base[name] = value
    return base
LOG_DIR = HERE / "logs"

_CACHE = None
_MERGED = None


def screen_size() -> "tuple[int, int]":
    import mss
    with mss.MSS() as sct:
        m = sct.monitors[1]
    return m["width"], m["height"]


def resolution_key(size=None) -> str:
    w, h = size or screen_size()
    return f"{w}x{h}"


def require_screen() -> str:
    want = str(load_shared()["run"]["screen"])
    seen = resolution_key()
    if seen != want:
        raise RuntimeError(
            f"the screen is {seen} and run.screen requires {want}; "
            f"nothing started.")
    return seen


def _read(path) -> dict:
    if not path.exists():
        raise RuntimeError(f"{path.name} is missing; nothing is built in.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{path.name} would not read: {exc}") from exc


def _sections(data) -> dict:
    return {name: dict(values) for name, values in data.items()
            if isinstance(values, dict) and name != "by_resolution"}


def _resolve_swap(shared):
    slots = [str(s) for s in shared["game_facts"]["swap_slots"]]
    pairs = shared["game_facts"]["swap_pairs"]
    table = shared["resupply"]["enable_buying"]
    fold = lambda v: re.sub(r"[^a-z0-9]", "", (v or "").lower())
    on = [core for core in pairs
          if any(fold(name) == fold(core) and value
                 for name, value in table.items())]
    if len(on) > 1:
        raise SystemExit(
            f"config.json enables {' and '.join(repr(c) for c in on)} at "
            f"once. They share favourite slots {'-'.join(slots)}, so "
            f"only one can be favourited in the game. Turn one off in "
            f"resupply.enable_buying.")
    if not on:
        return shared
    core, made = pairs[on[0]]
    shared["favourite_items"][slots[0]] = core
    shared["favourite_items"][slots[1]] = made
    return shared


def swapped_for(slot, text):
    if str(slot) not in SWAP_SLOTS:
        return None
    fold = lambda v: re.sub(r"[^a-z0-9]", "", (v or "").lower())
    here = fold(FAVOURITE_ITEMS.get(str(slot)))
    seen = fold(text)
    if not seen or here in seen:
        return None
    for core, (name, made) in SWAP_PAIRS.items():
        wanted = fold(name if str(slot) == SWAP_SLOTS[0] else made)
        if wanted != here and wanted in seen:
            return name if str(slot) == SWAP_SLOTS[0] else made
    return None


def load_shared() -> dict:
    out = _sections(_read(OUT))
    chosen = _read(CONFIGS / f"{config_name()}.json")
    for section, values in _sections(_overlay(_read(CONFIG), chosen)).items():
        out.setdefault(section, {}).update(values)
    return _resolve_swap(out)


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
    merged.update(_sections(data))
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
PANEL_PRICE_INSET = _S["detect"]["panel_price_inset"]
PANEL_PRICE_LIFT = _S["detect"]["panel_price_lift"]
PANEL_DIFF_THRESHOLD = _S["detect"]["panel_diff_threshold"]
PANEL_ITEM_HALF = int(_S["detect"]["panel_item_half"])
PANEL_LABEL_GAP = _S["detect"]["panel_label_gap"]
PANEL_LABEL_PAD = _S["detect"]["panel_label_pad"]
PANEL_ALZ_GAP = _S["detect"]["panel_alz_gap"]
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
SLOT_INSET = _S["detect"]["slot_inset"]
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
GRID_RULES_MIN = _DET["grid_rules_min"]
GRID_RULE_SLACK = _DET["grid_rule_slack"]
MIN_PLAUSIBLE_BALANCE = _DET["min_plausible_balance"]
BUTTON_PREFIX_MIN = int(_DET["button_prefix_min"])
BUTTON_MISSING_MAX = int(_DET["button_missing_max"])
FAV_COUNT_SHORT = int(_DET["fav_count_short"])
FAV_COUNT_EXTRA = int(_DET["fav_count_extra"])
TOGGLE_TRIES = int(_S["timing"]["toggle_tries"])
VOUCHER_WORD = _S["text"]["voucher_word"]
VOUCHER_FLOOR_PARTS = int(_S["game_facts"]["voucher_cash"])
SWAP_SLOTS = tuple(str(s) for s in _S["game_facts"]["swap_slots"])
SWAP_PAIRS = {core: tuple(pair)
              for core, pair in _S["game_facts"]["swap_pairs"].items()}
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
CONVERT_GRID_SLACK = _DET["convert_grid_slack"]
CONVERT_GRID_SETTLE = _S["timing"]["convert_grid_settle"]
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
VENDOR_TABS = _S["game_facts"]["vendor_tabs"].split("|")
_ALZ_WORD = _S["text"]["alz_word"]
SERVER_LAG_TEXT = re.compile(_S["text"]["server_lag"],
                             re.IGNORECASE)
SERVER_LAG_IDLE = _S["timing"]["server_lag_idle"]
SERVER_LAG_PIXELS = _S["detect"]["server_lag_pixels"]
SERVER_LAG_SURE = _S["detect"]["server_lag_sure"]
SERVER_LAG_RED = _S["detect"]["server_lag_red"]
SERVER_LAG_GREEN = tuple(_S["detect"]["server_lag_green"])
SERVER_LAG_BLUE = _S["detect"]["server_lag_blue"]
GAME_WAIT_BAND_F = tuple(_REG["game_wait_band"])
GAME_WAIT_TEXT = re.compile(_S["text"]["game_wait"], re.IGNORECASE)
GAME_WAIT_PIXELS = _S["detect"]["game_wait_pixels"]
GAME_WAIT_RED = _S["detect"]["game_wait_red"]
GAME_WAIT_GREEN = _S["detect"]["game_wait_green"]
GAME_WAIT_BLUE = _S["detect"]["game_wait_blue"]
SERVER_LAG_BUDGET = _S["timing"]["server_lag_budget"]
VENDOR_TAB_WORDS = {w.strip().lower() for w in
                    _S["text"]["vendor_tab_words"].split("|")}
CONVERT_INVENTORY_TAB = _S["game_facts"]["convert_inventory_tab"]

GRID = _S["game_facts"]["grid_size"]
ACTION_GAP = _S["timing"]["action_gap"]
PARK_SETTLE = _S["timing"]["park_settle"]
TAB_SETTLE = _S["timing"]["tab_settle"]
DIALOG_TIMEOUT = _S["timing"]["dialog_timeout"]
CASH_MEASURE_TIMEOUT = _S["timing"]["cash_measure_timeout"]
PANEL_LOAD_TIMEOUT = _S["timing"]["panel_load_timeout"]
SEARCH_TIMEOUT = _S["timing"]["search_timeout"]
SEARCH_RETRIES = _S["timing"]["search_retries"]
ALZ_SEARCH = None
_NOT_DIGIT = re.compile("[^0-9]")


_STEPS = []
_NOW = {"phase": "", "step": ""}


@contextlib.contextmanager
def step(label):
    started = time.perf_counter()
    before = _NOW["step"]
    _NOW["step"] = label
    try:
        yield
    finally:
        _NOW["step"] = before
        _STEPS.append((label, (time.perf_counter() - started) * 1000))


_PHASES = []


@contextlib.contextmanager
def phase(label):
    started = time.perf_counter()
    before = _NOW["phase"]
    _NOW["phase"] = label
    try:
        yield
    finally:
        _NOW["phase"] = before
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
FRAME_INDEX = _S["debug"]["frame_index"]
FRAMES_ON = False
RUN_FRAMES = None
_FRAME_N = 0


def _run_name():
    import sys
    name = getattr(getattr(sys.stdout, "handle", None), "name", None)
    return (Path(name).stem if name
            else datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S"))


def _log_offset():
    import sys
    handle = getattr(sys.stdout, "handle", None)
    try:
        return handle.tell() if handle is not None else None
    except (OSError, ValueError):
        return None


PRUNE_EVERY = int(_S["debug"]["prune_every"])


def frames_on(enabled: "bool | None" = None) -> bool:
    global FRAMES_ON, RUN_FRAMES
    if enabled is None:
        enabled = bool(load_shared()["debug"]["frames"])
    FRAMES_ON = bool(enabled)
    if FRAMES_ON:
        if RUN_FRAMES is None:
            RUN_FRAMES = FRAME_DIR / _run_name()
        RUN_FRAMES.mkdir(parents=True, exist_ok=True)
        print(f"  debug frames -> {RUN_FRAMES}, indexed in {FRAME_INDEX}; "
              f"earlier runs' frames are kept until the "
              f"{int(load_shared()['debug']['keep_frames']):,}-frame budget "
              f"needs the room")
    if FRAMES_ON and bool(load_shared()["debug"]["video"]):
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
        reels = sorted(VIDEO_DIR.glob("*.mp4"), key=lambda f: f.name)
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


def clear_videos() -> int:
    if not VIDEO_DIR.exists():
        return 0
    gone = 0
    for old in VIDEO_DIR.glob("*.mp4"):
        try:
            old.unlink()
            gone += 1
        except OSError:
            pass
    return gone


def recording_on():
    global _TAPE
    if _TAPE is not None:
        return VIDEO_DIR
    knobs = load_shared()["debug"]
    gone = clear_videos()
    try:
        _TAPE = _Tape(knobs).start()
    except Exception as exc:
        print(f"  no screen recording: {type(exc).__name__}: {exc}")
        _TAPE = None
        return None
    print(f"  recording -> {VIDEO_DIR} ({knobs['video_seconds']}s a reel, "
          f"{knobs['keep_videos']} kept)")
    if gone:
        print(f"  cleared {gone} reel(s) from earlier runs; this one numbers "
              f"from 0001")
    return VIDEO_DIR


def recording_off() -> None:
    global _TAPE
    if _TAPE is None:
        return
    _TAPE.stop.set()
    _TAPE.thread.join(timeout=load_shared()["timing"]["dialog_timeout"])
    _TAPE = None


def _frame_age(frame):
    try:
        return frame.stat().st_mtime
    except OSError:
        return 0.0


def prune_frames() -> None:
    keep = int(load_shared()["debug"]["keep_frames"])
    if keep <= 0 or not FRAME_DIR.exists():
        return
    shots = sorted(FRAME_DIR.rglob("*.png"), key=_frame_age)
    for old_frame in shots[:max(0, len(shots) - keep)]:
        try:
            old_frame.unlink()
        except OSError:
            pass
    for folder in [p for p in FRAME_DIR.iterdir() if p.is_dir()]:
        if folder == RUN_FRAMES or any(folder.glob("*.png")):
            continue
        try:
            for leftover in folder.iterdir():
                leftover.unlink()
            folder.rmdir()
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
            if job[0] == "prune":
                prune_frames()
                continue
            _kind, out, image, meta = job
            image.save(out)
            with open(out.parent / FRAME_INDEX, "a",
                      encoding="utf-8") as index:
                index.write(json.dumps(meta) + chr(10))
        except Exception as exc:
            print(f"  the frame writer failed on {job[0]!r}: "
                  f"{type(exc).__name__}: {exc}")
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
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("_") or "frame"
    out = (RUN_FRAMES or FRAME_DIR) / f"{_FRAME_N:05d}_{safe}.png"
    meta = {"n": _FRAME_N, "file": out.name,
            "at": datetime.datetime.now().isoformat(timespec="milliseconds"),
            "log": _log_offset(), "phase": _NOW["phase"],
            "step": _NOW["step"]}
    try:
        waiting = _frames_waiting()
    except Exception as exc:
        print(f"  no frame writer: {type(exc).__name__}: {exc}")
        return None
    try:
        waiting.put_nowait(("frame", out, grab() if image is None else image,
                            meta))
    except Exception:
        _FRAMES_DROPPED += 1
        return None
    if _FRAME_N % PRUNE_EVERY == 0:
        try:
            waiting.put_nowait(("prune",))
        except Exception:
            pass
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
            down = bool(_user32.GetAsyncKeyState(vk)
                        & _S["input"]["KEY_STATE_DOWN"])
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


_GROUPED = re.compile(_S["text"]["money_grouped"])
_WEDGED = re.compile(_S["text"]["money_wedged"])
_SEPARATOR = re.compile(_S["text"]["money_separator"])


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


_LOOKALIKE = str.maketrans(dict(_S["text"]["lookalikes"]))


def read_balance_from(image):
    box = balance_box()
    seen = read_line(image, box)
    label = re.search(_ALZ_WORD, seen, flags=re.IGNORECASE)
    trimmed = seen[:label.start()] if label else seen
    for token in reversed(trimmed.split()):
        for candidate in (token, token.translate(_LOOKALIKE)):
            value = _digits(candidate)
            if value is not None and value >= MIN_PLAUSIBLE_BALANCE:
                if candidate != token or len(trimmed.split()) > 1:
                    print(f"  the balance band reads {seen.strip()!r}; the "
                          f"figure against the label is {value:,}")
                return value
    return read_money(image, box)


def undercut(price, by=None):
    by = int(load_shared()["run"]["undercut_by"] if by is None else by)
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
            io.StringIO(run.stdout.decode("utf-8", "replace")), delimiter="\t",
            quoting=csv.QUOTE_NONE):
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


UNDERPRICE_F = tuple(_REG["underprice_warning"])
UNDERPRICE_LINE_F = tuple(_REG["underprice_question_line"])
UNDERPRICE_TEXT = re.compile(_S["text"]["underprice_warning"], re.IGNORECASE)


def underprice_warning(image=None):
    image = image if image is not None else grab()
    if not has_ink(image, _box(UNDERPRICE_LINE_F)):
        return False
    seen = " ".join(t for t, _c, _p in ocr(image, _box(UNDERPRICE_F)))
    return UNDERPRICE_TEXT.search(seen) is not None


def underprice_warning_gone(timeout=None):
    deadline = time.monotonic() + (DIALOG_TIMEOUT if timeout is None
                                   else timeout)
    while time.monotonic() < deadline:
        if not underprice_warning():
            return True
        time.sleep(POLL_GAP)
    return False


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
            io.StringIO(run.stdout.decode("utf-8", "replace")), delimiter="\t",
            quoting=csv.QUOTE_NONE):
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


def grid_rules(image=None):
    inventory = _measured().get("inventory")
    if inventory is None:
        return None
    image = image if image is not None else grab()
    one = inventory["slots"]["1x1"]
    spx, spy = inventory["slot_pitch"]
    x0, y0 = round(one[0] - spx / 2), round(one[1] - spy / 2)
    x1, y1 = round(x0 + GRID * spx) + 1, round(y0 + GRID * spy) + 1
    grey = np.asarray(image.crop((x0, y0, x1, y1)).convert("L"), dtype=float)
    if grey.shape[0] <= GRID or grey.shape[1] <= GRID:
        return 0.0
    down = (np.abs(np.diff(grey, axis=1)) > PANEL_RULE_CONTRAST).sum(axis=0)
    across = (np.abs(np.diff(grey, axis=0)) > PANEL_RULE_CONTRAST).sum(axis=1)

    def present(profile, pitch, span):
        slack = round(pitch * GRID_RULE_SLACK)
        found = []
        for k in range(GRID + 1):
            at = round(k * pitch)
            lo, hi = max(0, at - slack), min(len(profile), at + slack + 1)
            found.append(profile[lo:hi].max() / span if hi > lo else 0.0)
        return sum(found) / len(found)

    return min(present(down, spx, grey.shape[0]),
               present(across, spy, grey.shape[1]))


def inventory_grid_shown(image=None):
    rules = grid_rules(image)
    return rules is None or rules >= GRID_RULES_MIN


def inventory_open(image=None):
    image = image if image is not None else grab()
    if not inventory_grid_shown(image):
        return None
    boxes = []
    if (_measured().get("inventory") or {}).get("alz_box"):
        boxes.append(balance_box())
    found = find_alz(image)
    if found is not None:
        boxes.append(found)
    for box in boxes:
        digits = re.sub(r"[^0-9]", "", read_line(image, box))
        if len(digits) >= ALZ_MIN_DIGITS:
            return box
    return None


def await_inventory(timeout=None, verbose=False):
    from open_inventory import VK_I, press
    span = DIALOG_TIMEOUT if timeout is None else timeout
    park()
    box = inventory_open()
    if box is not None:
        return box
    for attempt in range(1, TOGGLE_TRIES + 1):
        if verbose:
            print(f"  Inventory shut; pressing I (attempt {attempt})")
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
        half = round((SLOT_HALF - SLOT_INSET) * scale)
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


def _trade_window_open(image=None) -> bool:
    try:
        words = ocr(image if image is not None else grab(),
                    _box(TRADE_TABS_BAND_F))
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


def gift_buttons(image=None):
    image = image if image is not None else grab()
    words = [(text.strip().lower(), point)
             for text, _conf, point in ocr(image, _box(GIFT_WINDOW_F))
             if text.strip()]
    slack = _S["detect"]["word_row_slack"]

    def beside(point, word, to_the_right):
        for text, other in words:
            if text != word.lower() or abs(other[1] - point[1]) > slack:
                continue
            gap = other[0] - point[0] if to_the_right else point[0] - other[0]
            if 0 < gap <= GIFT_WORD_REACH:
                return other
        return None

    receive_all, singles = None, []
    for text, point in words:
        if text != RECEIPT_WORD.strip().lower():
            continue
        other = beside(point, GIFT_ALL_WORD, True)
        if other is not None:
            receive_all = ((point[0] + other[0]) // 2,
                           (point[1] + other[1]) // 2)
            continue
        if beside(point, GIFT_AUTO_WORD, False) is not None:
            continue
        singles.append(point)
    column = gift_column(singles)
    others = [p for p in singles if p not in column]
    return {"column": column, "receive_all": receive_all,
            "special": others[0] if others else None,
            "close": _gift_reads(CLOSE_WORD, image)}


def gift_slots(column):
    if len(column) < 2:
        return list(column)
    ys = sorted(p[1] for p in column)
    pitch = min(b - a for a, b in zip(ys, ys[1:]))
    x = column[0][0]
    slots = []
    y = ys[0]
    while y <= ys[-1] + pitch // 2 and len(slots) < GIFT_BOXES:
        slots.append((x, y))
        y += pitch
    return slots


def gift_column(points):
    best = []
    for point in points:
        together = [p for p in points
                    if abs(p[0] - point[0]) <= GIFT_COLUMN_SPREAD]
        if len(together) > len(best):
            best = together
    return sorted(best, key=lambda p: p[1])


def _gift_window(image=None):
    found = gift_buttons(image)
    return found["column"], found["close"]


def _gift_window_shows(listed, shut):
    return bool(shut)


def _await_gift_window():
    listed, shut = [], []
    deadline = time.monotonic() + DIALOG_TIMEOUT
    while time.monotonic() < deadline:
        listed, shut = _gift_window()
        if _gift_window_shows(listed, shut):
            break
        time.sleep(POLL_GAP)
    return listed, shut


def calibrate_gifts(verbose=True):
    say = print if verbose else (lambda *a: None)
    icon = _point(GIFT_ICON_F)
    say(f"  the gift box at {list(icon)}")
    listed, shut = _gift_window()
    if _gift_window_shows(listed, shut):
        say(f"  the gift box was already open; not clicking its icon")
    for attempt in range(1, GIFT_OPEN_TRIES + 1):
        if _gift_window_shows(listed, shut):
            break
        if attempt > 1:
            snap("gift_window_short")
            say(f"  the gift box shows no {CLOSE_WORD}; clicking its icon "
                f"again, {attempt} of {GIFT_OPEN_TRIES}")
        click(*icon)
        time.sleep(_S["timing"]["action_gap"])
        listed, shut = _await_gift_window()

    if not _gift_window_shows(listed, shut):
        snap("gift_window_short")
        say(f"  the gift box shows no {CLOSE_WORD}; leaving the gift points "
            f"unmeasured")
        return None

    found = gift_buttons()
    taking = gift_slots(found["column"])[:GIFT_BOXES]
    say(f"  {RECEIPT_WORD} at {[list(p) for p in taking]}; the gifts on "
        f"offer are read again each time the box is collected")
    if found["receive_all"] is not None:
        say(f"  {RECEIPT_WORD} {GIFT_ALL_WORD} at "
            f"{list(found['receive_all'])}")
    if found["special"] is not None:
        say(f"  the special giftbox's {RECEIPT_WORD} at "
            f"{list(found['special'])}")
    say(f"  {CLOSE_WORD} at {list(shut[0])}")
    click(*shut[0])
    park()
    block = {"icon": list(icon),
             "receive": [list(p) for p in taking],
             "close": list(shut[0])}
    if found["receive_all"] is not None:
        block["receive_all"] = list(found["receive_all"])
    if found["special"] is not None:
        block["special"] = list(found["special"])
    return block


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
    y = FAV[1] + (FAV[3] - FAV[1]) // 2
    found = [FAV[0] + i for i in peaks]
    gaps = list(np.diff(found)) if len(found) > 1 else []
    f_pitch = float(np.mean(gaps)) if gaps else 0.0
    say(f"  favourites: {len(found)} found, pitch {f_pitch:.2f}px, "
        f"first {[found[0], y] if found else None} "
        f"last {[found[-1], y] if found else None}")
    if not gaps or not (FAVOURITE_COUNT - FAV_COUNT_SHORT <= len(found)
                        <= FAVOURITE_COUNT + FAV_COUNT_EXTRA):
        raise RuntimeError(
            f"expected about {FAVOURITE_COUNT} favourite slots, found "
            f"{len(found)} at {found}. Not writing a calibration that does "
            f"not describe the row.")
    pitch = int(round(float(np.median(gaps))))
    if pitch <= 0:
        raise RuntimeError(f"the favourite row has no usable pitch: {gaps}.")
    favourites = [[found[0] + k * pitch, y] for k in range(FAVOURITE_COUNT)]
    say(f"  favourite row set on an even {pitch}px grid from {found[0]}: "
        f"{[p[0] for p in favourites]}")

    return {
        "purchase_tab": purchase,
        "register_tab": list(reg),
        "tab_boundary_x": boundary,
        "favourites": favourites,
        "favourite_pitch": pitch,
        "evidence": {
            "register_conf": named.get("register", (None,))[0],
            "favourite_raw_pitch": round(f_pitch, 2),
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
    seen = [t.strip() for t, _, _ in ocr(image, hdr)]
    words = sorted(((p[0], t.strip().lower()) for t, _, p in ocr(image, hdr)
                    if t.strip().lower() in ("name", "qty", "price",
                                             "function")))
    have = [n for _, n in words]
    missing = [w for w in ("name", "qty", "price") if w not in have]
    if missing:
        raise RuntimeError(
            f"the offers header is missing {missing}; read {seen}. "
            f"Cannot place the per-column boxes.")
    centre = {n: x for x, n in words}
    if "function" not in centre:
        centre["function"] = out["purchase_buy_x"]
        say(f"  the header read {seen}, with nothing for the Function "
            f"column; its Buy buttons are at x={out['purchase_buy_x']}, which "
            f"is the column itself, so taking the right edge from there")

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
    bottom = int(ys[-1])
    while (round((bottom - top) / pitch) + 1 < SHOP_VISIBLE
           and bottom + pitch <= band[3]):
        bottom += pitch
        say(f"  a row sits at y={bottom} with no button that read; counting it")
    rows = round((bottom - top) / pitch) + 1
    placed = rows == SHOP_VISIBLE
    if not placed:
        say(f"  the Register table shows {rows} row(s) from y={top} to "
            f"y={bottom} at {pitch}px, and game_facts.shop_visible says "
            f"{SHOP_VISIBLE}; the last row is not placed, so rows past "
            f"{SHOP_CAPACITY - SHOP_VISIBLE + 1} cannot be read this run")
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
        "row_last_y": int(bottom) if placed else None,
        "row_last_box": ([band[0], int(bottom) - pitch // 2,
                          band[2], int(bottom) + pitch // 2]
                         if placed else None),
        "button_x": int(xs[len(xs) // 2]),
        "table_point": [int(xs[len(xs) // 2]) - SCROLL_POINT_INSET, int(ys[0]) + pitch],
        "rows_per_notch": 1,
        "register_rows_seen": len(marks),
    }
    say(f"  button column {buttons}")
    say(f"  Register table: {len(marks)} row(s), pitch {pitch}px, "
        f"row 1 y={out['row_one_y']}"
        + (f", row {SHOP_VISIBLE} y={out['row_last_y']}" if placed else ""))
    if refresh is not None:
        out["refresh_point"] = refresh
    say(f"  table_x {out['table_x']}, scroll point {out['table_point']}")
    if refresh is not None:
        say(f"  {REFRESH_WORD} at {refresh}")
    return out


SHOP_VISIBLE = int(_S["game_facts"]["shop_visible"])
SHOP_CAPACITY = int(_S["game_facts"]["shop_capacity"])
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


def button_word_matches(seen, word):
    seen = re.sub(r"[^a-z0-9]", "", (seen or "").lower())
    word = re.sub(r"[^a-z0-9]", "", (word or "").lower())
    if not seen or not word:
        return False
    if seen == word:
        return True
    return (len(seen) >= BUTTON_PREFIX_MIN and word.startswith(seen)
            and len(word) - len(seen) <= BUTTON_MISSING_MAX)


ACTION_BUTTON_WORDS = (_S["text"]["confirm_word"], _S["text"]["dismiss_word"],
                       _S["text"]["receipt_word"], _S["text"]["register_word"])

RECEIPT_WORD = _S["text"]["receipt_word"]
_ROW_TEXT = re.compile(_S["text"]["purchase_row"])
_ROW_GROUPING = re.compile(_S["text"]["row_grouping"])


def row_price(text):
    cleaned = (text or "").strip()
    for candidate in (cleaned, _ROW_GROUPING.sub(",", cleaned)):
        found = _ROW_TEXT.match(candidate)
        if found is None:
            continue
        digits = re.sub(r"[^0-9]", "", found.group("price"))
        if digits:
            return int(digits)
    return None
CLOSE_WORD = _S["text"]["close_word"]
GIFT_BOXES = int(_S["game_facts"]["gift_boxes"])
GIFT_OPEN_TRIES = int(_S["timing"]["gift_open_tries"])
GIFT_COLUMN_SPREAD = int(_S["detect"]["gift_column_spread"])
GIFT_WORD_REACH = int(_S["detect"]["gift_word_reach"])
GIFT_ALL_WORD = _S["text"]["gift_all_word"]
GIFT_AUTO_WORD = _S["text"]["gift_auto_word"]
GIFT_ICON_F = tuple(_S["regions"]["gift_icon"])
GIFT_WINDOW_F = tuple(_S["regions"]["gift_window"])
CASH_ICON_F = tuple(_S["regions"]["cash_icon"])
CASH_TABS_F = tuple(_S["regions"]["cash_tabs"])
CASH_DIALOG_F = tuple(_S["regions"]["cash_dialog"])
CASH_BALANCE_F = tuple(_S["regions"]["cash_balance"])
CASH_FRAME_BRIGHT = _DET["cash_frame_bright"]
CASH_FRAME_RUN = _DET["cash_frame_run"]


def _await_cash(read, timeout=None):
    deadline = time.monotonic() + (DIALOG_TIMEOUT if timeout is None
                                   else timeout)
    while time.monotonic() < deadline:
        seen = read()
        if seen:
            return seen
        time.sleep(POLL_GAP)
    return None


def _longest_run(flags):
    best = run = 0
    for flag in flags:
        run = run + 1 if flag else 0
        if run > best:
            best = run
    return best


def frame_box(image, box):
    bright = (np.asarray(image.crop(box).convert("L"), dtype=int)
              > CASH_FRAME_BRIGHT)
    height, width = bright.shape
    rows = [y for y in range(height)
            if _longest_run(bright[y]) >= CASH_FRAME_RUN * width]
    cols = [x for x in range(width)
            if _longest_run(bright[:, x]) >= CASH_FRAME_RUN * height]
    if len(rows) < 2 or len(cols) < 2:
        return None
    return (box[0] + cols[0], box[1] + rows[0],
            box[0] + cols[-1], box[1] + rows[-1])


def calibrate_cashshop(verbose=True):
    import cashshop
    from open_inventory import VK_ESCAPE, press
    say = print if verbose else (lambda *a: None)
    icon = cashshop.icon_point()
    say(f"  the Cash Shop icon at {list(icon)}")
    if cashshop.is_open() and cashshop.tab_point(cashshop.DEFAULT_TAB) is None:
        say(f"  the Cash Shop is open on another view, without its "
            f"{cashshop.DEFAULT_TAB} tab; closing it first")
        cashshop.close_cash_shop(verbose=verbose)
    if cashshop.is_open():
        say("  the Cash Shop was already open; not clicking its icon")
    else:
        click(*icon)
        if _await_cash(cashshop.is_open) is None:
            snap("cash_shop_never_opened")
            say(f"  no {cashshop.DEFAULT_TAB} tab within {DIALOG_TIMEOUT:g}s "
                f"of clicking the icon; leaving the Cash Shop unmeasured")
            park()
            return None
    block = {"icon": list(icon)}
    balance = _await_cash(cashshop.read_cc)
    if balance is None:
        snap("cash_balance_unread")
        say(f"  the Cash balance box {list(cashshop.balance_box())} would "
            f"not read; not recorded")
    else:
        say(f"  the Cash balance box {list(cashshop.balance_box())} reads "
            f"{balance:,}")
        block["balance"] = list(cashshop.balance_box())
    gems = _await_cash(cashshop.read_gems)
    if gems is None:
        snap("cash_gems_unread")
        say(f"  the gem box {list(cashshop.gems_box())} would not read; not "
            f"recorded")
    else:
        say(f"  the gem box {list(cashshop.gems_box())} reads {gems:,}")
        block["gems"] = list(cashshop.gems_box())
    if not cashshop.items():
        say("  no items under resupply.cash_shop.rows; only the icon and "
            "the balance are measured")
    showing = None
    for item in cashshop.in_reading_order():
        word = cashshop.tab_for(item)
        tab = cashshop.tab_point(word)
        if tab is None:
            snap("cash_no_tab_" + cashshop._fold(word))
            say(f"  no {word!r} tab for {item!r}; not measured")
            continue
        if word != showing:
            say(f"  {word} at {list(tab)}")
            click(*tab)
            time.sleep(TAB_SETTLE)
            park()
            cashshop.at_top()
            showing = word
        block["tab_" + cashshop._fold(word)] = list(tab)
        cell = _await_cash(lambda: cashshop.seek_cell(item, verbose=True))
        if cell is None:
            snap("cash_cell_not_found_" + cashshop._fold(item))
            say(f"  {item!r} is not on the {word} tab's first page with a "
                f"{cashshop.PURCHASE_WORD} button; not measured")
            continue
        say(f"  {item} at {cell['price']} Cash; {cashshop.PURCHASE_WORD} at "
            f"{list(cell['purchase'])}")
        block["purchase_" + cashshop._fold(item)] = list(cell["purchase"])
        funds = cashshop.currency_of(item)
        held, coin = (gems, "gem(s)") if funds else (balance, "Cash")
        if held is not None and cell["price"] is not None \
                and held < cell["price"]:
            say(f"  {held:,} {coin} held and {item} costs {cell['price']}; "
                f"the game opens no confirmation for that, so it is not "
                f"measured this launch")
            continue
        click(*cell["purchase"], settle=0.0)
        seen = _await_cash(cashshop.dialog, timeout=CASH_MEASURE_TIMEOUT)
        if seen is None:
            snap("cash_no_confirmation")
            say(f"  no {cashshop.OK_WORD} and {cashshop.CANCEL_WORD} within "
                f"{CASH_MEASURE_TIMEOUT:g}s of {cashshop.PURCHASE_WORD}; "
                f"nothing pressed")
            park()
            continue
        snap("cash_confirmation")
        ok, cancel = seen["ok"], seen["cancel"]
        say(f"  the confirmation reads {seen['text']!r}")
        if not cashshop.matches(item, seen["text"]):
            snap("cash_confirmation_names_another_" + cashshop._fold(item))
            say(f"  it names something other than {item!r}, so the cell that "
                f"was found is not this item; cancelling and recording "
                f"neither the cell nor the window")
            block.pop("purchase_" + cashshop._fold(item), None)
        else:
            window = frame_box(grab(), _box(CASH_DIALOG_F))
            if window is not None and not (
                    window[0] < ok[0] < cancel[0] < window[2]
                    and window[1] < ok[1] < window[3]):
                say(f"  the frame lines read {list(window)}, which do not "
                    f"enclose {cashshop.OK_WORD} and {cashshop.CANCEL_WORD}; "
                    f"the window is not recorded")
                window = None
            if window is None:
                say("  the confirmation window was not found by its frame "
                    "lines")
            else:
                say(f"  the confirmation window {list(window)}, "
                    f"{window[2] - window[0]}x{window[3] - window[1]}")
                block["window"] = list(window)
            say(f"  {cashshop.OK_WORD} at {list(ok)}; {cashshop.CANCEL_WORD} "
                f"at {list(cancel)}")
            block["ok"], block["cancel"] = list(ok), list(cancel)
        click(*cancel)
        park()
        if _await_cash(lambda: cashshop.dialog() is None) is None:
            say(f"  the confirmation stayed after {cashshop.CANCEL_WORD}; "
                f"clicking it again")
            click(*cancel)
            park()
    for attempt in range(1, TOGGLE_TRIES + 1):
        if not cashshop.is_open():
            break
        say(f"  Escape to close the Cash Shop (attempt {attempt})")
        press(VK_ESCAPE)
        time.sleep(ACTION_GAP)
        if _await_cash(lambda: not cashshop.is_open()):
            break
    park()
    if inventory_open() is None:
        say("  the Inventory closed with the Cash Shop; opening it again")
        if await_inventory(verbose=verbose) is None:
            snap("cash_shop_left_inventory_shut")
            say("  the Inventory would not reopen after the Cash Shop")
    snap("cash_shop_measured")
    return block


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


def _grade_word(name):
    found = re.search(r"\(\s*([A-Za-z]+)", name or "")
    return found.group(1).lower() if found else None


def favourite_slot_of(name):
    want = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    if not want:
        return None
    grade = _grade_word(name)
    best = None
    for slot, item in FAVOURITE_ITEMS.items():
        key = re.sub(r"[^a-z0-9]", "", item.lower())
        if not key:
            continue
        if grade and _grade_word(item) and not _grade_word(item).startswith(grade):
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


def _verified_book():
    global _VERIFIED
    if _VERIFIED is None:
        try:
            _VERIFIED = json.loads(VERIFIED.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _VERIFIED = {}
    return _VERIFIED


def verified_cost(name):
    slot = favourite_slot_of(name)
    entry = _verified_book().get(str(slot)) if slot is not None else None
    return int(entry["each"]) if entry else 0


def note_verified_cost(name, each):
    slot = favourite_slot_of(name)
    if slot is None or not each:
        return False
    book = _verified_book()
    book[str(slot)] = {
        "item": FAVOURITE_ITEMS[str(slot)], "each": int(each),
        "at": datetime.datetime.now().isoformat(timespec="seconds")}
    spare = VERIFIED.with_name(VERIFIED.name + ".tmp")
    spare.write_text(json.dumps(book, indent=2), encoding="utf-8")
    spare.replace(VERIFIED)
    return True


def price_floor(name):
    item, ratio = voucher_floor_ratio(name)
    if ratio:
        voucher = voucher_unit()
        if voucher < MIN_PLAUSIBLE_PRICE:
            return 0, f"{VOUCHER_WORD} voucher, which did not price"
        return (voucher * ratio) // VOUCHER_FLOOR_PARTS,                f"{ratio}/{VOUCHER_FLOOR_PARTS} of a {VOUCHER_WORD} voucher"
    prices = _read(OUT).get("market", {}).get("unit_price", {})
    slot = favourite_slot_of(name)
    if slot is None:
        return 0, ""
    pair = pair_slot(slot)
    if pair is None:
        return 0, ""
    paid = verified_cost(name)
    if paid:
        return paid, f"{FAVOURITE_ITEMS[str(pair)]} as last bought"
    floor = int(prices.get(str(pair)) or 0)
    if floor < MIN_PLAUSIBLE_PRICE:
        return 0, f"{FAVOURITE_ITEMS[str(pair)]}, which did not price"
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


def game_says_wait(image=None) -> bool:
    image = image if image is not None else grab()
    box = _box(GAME_WAIT_BAND_F)
    try:
        patch = np.asarray(image.crop(box).convert("RGB"), dtype=int)
        ink = int(((patch[..., 0] > GAME_WAIT_RED)
                   & (patch[..., 1] < GAME_WAIT_GREEN)
                   & (patch[..., 2] < GAME_WAIT_BLUE)).sum())
        if ink < GAME_WAIT_PIXELS:
            return False
        seen = read_line(image, box)
    except Exception:
        return False
    return GAME_WAIT_TEXT.search(seen) is not None


class ServerStalled(Exception):
    pass


_HOLDING = False


def hold_if_busy() -> None:
    global _HOLDING
    if _HOLDING:
        return
    _HOLDING = True
    try:
        waited = wait_out_server_lag(verbose=True)
    finally:
        _HOLDING = False
    if waited:
        raise ServerStalled(
            f"the server stalled for {waited:.0f}s; nothing was clicked and "
            f"the shop is shut. The pass starts again.")


_ON_RECOVERED = None
_RECOVERING = False


def on_recovered(fn):
    global _ON_RECOVERED
    _ON_RECOVERED = fn
    return fn


def _recovered():
    global _RECOVERING
    if _ON_RECOVERED is None or _RECOVERING:
        return
    _RECOVERING = True
    try:
        _ON_RECOVERED()
    finally:
        _RECOVERING = False


def wait_out_server_lag(verbose=True, reset=True):
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
            if reset:
                _recovered()
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
    for attempt in range(1, TOGGLE_TRIES + 1):
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
    fold = lambda t: re.sub(r"[^a-z]", "", (t or "").lower())
    want = fold(name)
    seen = {}
    for text, _c, point in ocr(image, _box(VENDOR_TAB_BAND_F)):
        word = fold(text)
        if word:
            seen.setdefault(word, point)
    if want in seen:
        return seen[want]
    order = [fold(t) for t in VENDOR_TABS]
    if want not in order:
        return None
    here = [(order.index(w), p) for w, p in seen.items() if w in order]
    if len(here) < 2:
        return None
    here.sort()
    (a, left), (b, right) = here[0], here[-1]
    if b == a:
        return None
    step = (right[0] - left[0]) / (b - a)
    at = order.index(want)
    return (round(left[0] + step * (at - a)),
            round((left[1] + right[1]) / 2))


CRAFT_TAB = _S["game_facts"]["craft_tab"]
CRAFT_KEY_SLOT = tuple(_S["game_facts"]["craft_key_slot"])
REFRESH_WORD = _S["game_facts"]["refresh_word"]
HELD_OF_NEEDED = _S["game_facts"]["held_of_needed"]
CRAFT_TIER_WORDS = _S["game_facts"]["craft_tier_words"].split("|")
CRAFT_RECIPE_WORDS = _S["game_facts"]["craft_recipe_words"].split("|")
CRAFT_RECIPES = {core: words.split("|") for core, words
                 in _S["game_facts"]["craft_recipes"].items()}


def recipe_words(core):
    return CRAFT_RECIPES.get(core) or CRAFT_RECIPE_WORDS


def craft_cores():
    out = []
    for core in CRAFT_RECIPES:
        slot = favourite_slot_of(core)
        if slot is not None and pair_slot(slot) is not None:
            out.append(core)
    return out


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
    want = re.sub(r"[^a-z]", "", CRAFT_COMPLETE_WORD.lower())
    return any(want in word for word in seen)


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
            nearest = min(after, key=lambda p: p[0])
            return [(point[0] + nearest[0]) // 2, point[1]]
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
    def locate(words):
        for line in lines:
            flat = re.sub(r"[^a-z0-9]", "",
                          " ".join(t for _x, t in sorted(line["words"])).lower())
            if all(re.sub(r"[^a-z0-9]", "", w.lower()) in flat for w in words):
                xs = [x for x, _t in line["words"]]
                return [(min(xs) + max(xs)) // 2, line["y"]]
        return None

    cores = craft_cores() or [None]
    found = {}
    for core in cores:
        words = recipe_words(core) if core else CRAFT_RECIPE_WORDS
        point = locate(words)
        if point is None:
            raise RuntimeError(
                f"no recipe under the {'-'.join(CRAFT_TIER_WORDS)} tier reads "
                f"{words}; it read {[t for t, _c, _p in recipes]}. "
                f"Nothing written.")
        found[core or "_default"] = point
        say(f"  recipe {' '.join(words)} at {point}")

    first = next(iter(found.values()))
    click(*first)
    time.sleep(TAB_SETTLE)
    band = _box(tuple(_REG["craft_buttons"]))
    def joined(words, word):
        want = re.sub(r"[^a-z]", "", word.lower())
        for text, _conf, point in words:
            flat = re.sub(r"[^a-z]", "", text.lower())
            if flat == want:
                after = sorted(p[0] for _t, _c, p in words
                               if abs(p[1] - point[1]) <= WORD_ROW_SLACK
                               and p[0] > point[0])
                tail = after[0] if after else point[0]
                return [(point[0] + tail) // 2, point[1]]
            if want in flat:
                return list(point)
        return None
    chosen = ocr(grab(), band)
    deadline = time.monotonic() + DIALOG_TIMEOUT
    while time.monotonic() < deadline:
        if (_two_word_button(chosen, CRAFT_REQUEST_WORDS)
                or joined(chosen, CRAFT_REQUEST_WORDS[0])) and                 joined(chosen, CRAFT_COMPLETE_WORD):
            break
        time.sleep(POLL_GAP)
        chosen = ocr(grab(), band)
    request = (_two_word_button(chosen, CRAFT_REQUEST_WORDS)
               or joined(chosen, CRAFT_REQUEST_WORDS[0]))
    if request is None:
        raise RuntimeError(
            f"no {' '.join(CRAFT_REQUEST_WORDS)} button after choosing a "
            f"recipe; the band read {[t for t, _c, _p in chosen]}. "
            f"Nothing written.")
    complete = joined(chosen, CRAFT_COMPLETE_WORD)
    if complete is None:
        raise RuntimeError(
            f"no {CRAFT_COMPLETE_WORD} button after choosing a recipe; the "
            f"band read {[t for t, _c, _p in chosen]}. Nothing written.")

    out = {"tier": [left, tier_y],
           "recipe": first,
           "recipes": found,
           "request": request,
           "complete": complete,
           "material_box": list(_box(tuple(_REG["craft_material"])))}
    say(f"  tier {'-'.join(CRAFT_TIER_WORDS)} at {out['tier']}")
    say(f"  {' '.join(CRAFT_REQUEST_WORDS)} at {request}")
    say(f"  {CRAFT_COMPLETE_WORD} All at {complete}")
    say(f"  material counter {out['material_box']}")
    return out


def _even_grid(seen, want, lo, hi, slack):
    if len(seen) == want or len(seen) < 2:
        return seen
    first, last = seen[0], seen[-1]
    pitch = (last - first) / (want - 1)
    if pitch <= 0:
        return seen
    grid = [round(first + step * pitch) for step in range(want)]
    if grid[0] < lo or grid[-1] > hi:
        return seen
    if any(min(abs(at - on) for on in grid) > slack for at in seen):
        return seen
    return grid


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

    band = _box(CONVERT_GRID_BAND_F)

    def _read_grid():
        shot = grab()
        cell = np.asarray(shot.crop(band).convert("L"), dtype=float)
        return (shot,
                _peaks(cell.mean(axis=0), CONVERT_PEAK_CUT,
                       CONVERT_MERGE_GAP, keep=len(CONVERT_GRADES)),
                _peaks(cell.mean(axis=1), CONVERT_PEAK_CUT,
                       CONVERT_MERGE_GAP, keep=CONVERT_ROW_COUNT))

    image, cols, rows = _read_grid()
    deadline = time.monotonic() + CONVERT_GRID_SETTLE
    looks = 1
    while (len(rows) != CONVERT_ROW_COUNT
           or len(cols) != len(CONVERT_GRADES)) and             time.monotonic() < deadline:
        time.sleep(POLL_GAP)
        image, cols, rows = _read_grid()
        looks += 1
    if looks > 1:
        say(f"  the grid took {looks} look(s) to draw all "
            f"{CONVERT_ROW_COUNT} rows")
    xs = [band[0] + i for i in cols]
    ys = [band[1] + i for i in rows]
    say(f"  grid band {band}: {len(xs)} column(s) at {xs}, "
        f"{len(ys)} row(s) at {ys}")
    want = len(CONVERT_GRADES)
    if len(xs) != want and len(xs) >= 2:
        spans = sorted(b - a for a, b in zip(xs, xs[1:]))
        pitch = spans[len(spans) // 2]
        grid = [xs[0] + k * pitch for k in range(want)] if pitch > 0 else []
        while grid and grid[-1] > band[2] and grid[0] - pitch >= band[0]:
            grid = [x - pitch for x in grid]
        if grid and grid[0] >= band[0] and grid[-1] <= band[2]:
            say(f"  only {len(xs)} column(s) lit up at {xs}; the row is "
                f"{want} even columns {pitch}px apart and they fit the band "
                f"from {grid[0]}, so using {grid}")
            xs = grid
    if len(xs) != want:
        raise RuntimeError(
            f"expected {want} conversion columns, one a grade, "
            f"and found {len(xs)} at {xs}. Nothing written.")
    if len(ys) != CONVERT_ROW_COUNT:
        fitted = _even_grid(ys, CONVERT_ROW_COUNT, band[1], band[3],
                            CONVERT_GRID_SLACK)
        if fitted is not ys:
            say(f"  only {len(ys)} row(s) lit up at {ys}; the grid is "
                f"{CONVERT_ROW_COUNT} even rows and those sit on it, so "
                f"using {fitted}")
            ys = fitted
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


def _per_item_raw(table, core_name):
    want = re.sub(r"[^a-z0-9]", "", (core_name or "").lower())
    for name, value in table.items():
        if re.sub(r"[^a-z0-9]", "", name.lower()) == want:
            return value
    return table.get("default")


def first_list_price(name=None):
    table = load_shared()["run"]["first_list_price"]
    if not isinstance(table, dict):
        return int(table or 0)
    return int(_per_item_raw(table, name) or 0)


def max_drop(name=None):
    table = load_shared()["run"]["max_drop"]
    if not isinstance(table, dict):
        return int(table)
    value = _per_item_raw(table, name)
    return int(value or 0)


def _per_item(key, core_name):
    run = load_shared()["resupply"]
    table = run.get(key)
    if not isinstance(table, dict):
        return None if table is None else int(table)
    value = _per_item_raw(table, core_name)
    return int(value) if value is not None else None


def _ladder_today(run, core_name):
    wide = run.get("rows_by_margin_on")
    if not isinstance(wide, dict):
        return None
    days = wide.get("days") or []
    today = time.strftime("%a")
    if not any(today.lower().startswith(str(d)[:3].lower()) for d in days):
        return None
    return _per_item_raw({k: v for k, v in wide.items() if k != "days"},
                         core_name)


def rows_by_margin(core_name, margin):
    run = load_shared()["resupply"]
    table = run.get("rows_by_margin")
    if not isinstance(table, dict):
        return None
    ladder = _ladder_today(run, core_name) or _per_item_raw(
        {k: v for k, v in table.items() if k != "step"}, core_name)
    if not ladder:
        return None
    tier = int(margin // int(table["step"]))
    if tier < 1:
        return 0
    return int(ladder[min(tier, len(ladder)) - 1])


def rows_wanted_at_most(core_name):
    run = load_shared()["resupply"]
    table = run["rows_by_margin"]
    if not isinstance(table, dict):
        return None
    ladder = _ladder_today(run, core_name) or _per_item_raw(
        {k: v for k, v in table.items() if k != "step"}, core_name)
    if not ladder:
        return None
    return max(int(v) for v in ladder)


def margin_for_rows(core_name, rows):
    table = load_shared()["resupply"]["rows_by_margin"]
    if not isinstance(table, dict):
        return None
    ladder = _per_item_raw(
        {k: v for k, v in table.items() if k != "step"}, core_name)
    if not ladder:
        return None
    for tier, count in enumerate(ladder, 1):
        if int(count) >= rows:
            return tier * int(table["step"])
    return None


def buy_min(core_name):
    return _per_item("buy_min", core_name)


def buy_max(core_name):
    return _per_item("buy_max", core_name)


def buy_leave_behind(core_name):
    return _per_item("buy_leave_behind", core_name) or 0


def craft_min_cores(core_name):
    return _per_item("craft_min_cores", core_name) or 0


def buy_whole_row(core_name):
    return bool(_per_item("buy_whole_row", core_name))


def buy_under_lister(core_name):
    return _per_item("buy_under_lister", core_name) or 0


def buy_under_gap(core_name):
    return _per_item("buy_under_gap", core_name) or 0


def _swap_or_die(slot, get_price):
    if str(slot) not in SWAP_SLOTS:
        return
    text = (get_price.read_fields(grab()).get("name") or "").strip()
    instead = swapped_for(slot, text)
    if not instead:
        return
    want = FAVOURITE_ITEMS[str(slot)]
    snap(f"slot_{slot}_swapped_for_{instead}")
    raise SystemExit(
        f"favourite slot {slot} holds {instead!r} but config.json asks for "
        f"{want!r}. They share the slot, so one of the two is wrong: either "
        f"favourite {want!r} in the game, or turn {instead!r} on and {want!r} "
        f"off in resupply.enable_buying.")


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
            _swap_or_die(slot, get_price)
    return seen


def calibrate_voucher(verbose=True):
    import get_price
    say = print if verbose else (lambda *a: None)
    row = get_price.get_voucher_price(verbose=False)
    if not row or not row.get("unit_price"):
        say(f"  the {VOUCHER_WORD} voucher would not price; standing on "
            f"{voucher_default():,} for the rest of the run")
        return voucher_default() or None
    say(f"  a {VOUCHER_WORD} voucher costs {row['unit_price']:,}")
    return int(row["unit_price"])


def voucher_default():
    return int(load_shared()["run"]["voucher_default"])


def voucher_unit():
    seen = int((_read(OUT).get("voucher") or {}).get("unit_price") or 0)
    return seen if seen >= MIN_PLAUSIBLE_PRICE else voucher_default()


def voucher_floor_ratio(name):
    table = load_shared()["run"]["voucher_floor"]
    want = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    best, named, found = 0, None, 0
    for item, rule in table.items():
        if isinstance(rule, dict):
            ratio, words = int(rule["ratio"]), rule.get("any") or [item]
        else:
            ratio, words = int(rule), [item]
        for word in words:
            parts = word if isinstance(word, (list, tuple)) else [word]
            folded = [re.sub(r"[^a-z0-9]", "", str(p).lower()) for p in parts]
            if not folded or not all(p and p in want for p in folded):
                continue
            weight = sum(len(p) for p in folded)
            if weight > best:
                best, named, found = weight, item, ratio
    return named, found


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
    alz_right = next(edge for t, _c, point, edge in spans
                     if tuple(point) == tuple(alz))
    alz_wide = 2 * (alz_right - alz[0])

    def ends_at_label(y):
        here = [edge for t, _c, point, edge in spans
                if re.search(_ALZ_WORD, t, re.IGNORECASE)
                and abs(point[1] - y) <= PANEL_FIELD_HALF]
        return (max(here) - alz_wide - PANEL_ALZ_GAP) if here else right

    out = {
        "panel_box": list(box),
        "price_field": [box[0] + PANEL_PRICE_INSET,
                        alz[1] - PANEL_FIELD_HALF - PANEL_PRICE_LIFT,
                        alz_right - alz_wide - PANEL_ALZ_GAP,
                        alz[1] + PANEL_FIELD_HALF - PANEL_PRICE_LIFT],
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
    return round((SLOT_HALF - SLOT_INSET)
                 * load()["inventory"]["panel_scale"])


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


def _item_centre(before, after, panel):
    from PIL import ImageChops
    x0, x1 = int(panel["price_field"][0]), int(panel["price_field"][2])
    y0 = int(panel["panel_box"][1])
    y1 = int(panel["suggestion_boxes"][0][1]) - 2 * PANEL_FIELD_HALF
    box = (x0, y0, x1, y1)
    diff = ImageChops.difference(before.crop(box), after.crop(box))
    diff = diff.convert("L").point(
        lambda v: 255 if v > PANEL_DIFF_THRESHOLD else 0)
    changed = [(x, y) for y in range(diff.height) for x in range(diff.width)
               if diff.getpixel((x, y))]
    if len(changed) < PANEL_ITEM_HALF:
        return None
    return (sum(x for x, _y in changed) // len(changed) + x0,
            sum(y for _x, y in changed) // len(changed) + y0)


def calibrate_actions(shop, verbose=True, seat="row_one", position=1):
    say = print if verbose else (lambda *a: None)
    panel = shop.get("panel")
    if not panel:
        raise RuntimeError("the register panel must be measured first.")
    timing = load_shared()["timing"]
    budget = timing["dialog_timeout"]
    dialog = _box(DIALOG_BUTTONS_F)
    learned = {}

    import row_model
    row_model.wheel(-int(load_shared()["run"]["home_notches"]), verbose=False)
    time.sleep(TAB_SETTLE)
    say(f"  scrolled to the top, so position {position} is row {position}")

    def buttons_now():
        found = {}
        for text, _conf, point in ocr(grab(), dialog):
            for word in ACTION_BUTTON_WORDS:
                if button_word_matches(text, word):
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
                f"{budget}s after {after} on row {position}; that band reads "
                f"{band_reads()}. Nothing has been withdrawn.")

    def close_dialogs():
        from open_inventory import VK_ESCAPE, press
        for _attempt in range(TOGGLE_TRIES):
            here = buttons_now()
            if not here:
                return True
            dismiss = here.get(_S["text"]["dismiss_word"])
            if dismiss is not None:
                say(f"  {_S['text']['dismiss_word']} at {dismiss} to close "
                    f"the dialog")
                click(*dismiss)
            else:
                say(f"  pressing Escape to close the dialog")
                press(VK_ESCAPE)
            park()
        return not buttons_now()

    if buttons_now():
        raise RuntimeError(
            "a dialog is already open over the Register table. Close it and "
            "calibrate again; nothing was clicked.")

    row_seat = tuple(shop[seat + "_box"])
    before = read_line(grab(), row_seat)
    lowered = before.lower()
    listed = re.search(r"\d[\d,]{2,}", before) is not None
    if listed and RECEIPT_WORD.lower() in lowered:
        receipt = (shop["button_x"], shop[seat + "_y"])
        say(f"  row {position} has SOLD: {before!r}")
        say(f"  {RECEIPT_WORD} at {receipt}")
        click(*receipt)
        park(settle=False)
        accept = await_button(RECEIPT_WORD)
        if accept is None:
            raise RuntimeError(missing(RECEIPT_WORD, RECEIPT_WORD))
        say(f"  Confirm Receipt at {accept}")
        click(*accept)
        park(settle=False)
        before = read_line(grab(), row_seat)
        lowered = before.lower()
        listed = re.search(r"\d[\d,]{2,}", before) is not None
        say(f"  collected; row {position} now reads {before!r}")
    if not listed or RECEIPT_WORD.lower() in lowered:
        say(f"  row {position} reads {before!r}; it is not a live listing this "
            f"pass can withdraw. Earlier positions stand.")
        return {}
    was = row_price(before)
    if was is None:
        say(f"  row {position} reads {before!r} and its price would not read, so it "
            f"is not withdrawn; it could not be put back at the same price. "
            f"Earlier positions stand.")
        return {}

    landing = first_free_slot(WORK_TAB, verbose=verbose)
    if landing is None:
        say(f"  inventory tab {WORK_TAB} is full; not walking the actions. "
            f"Earlier positions stand.")
        return {}

    change = (shop["button_x"], shop[seat + "_y"])
    say(f"  row {position} is {before!r}")
    say(f"  Change at {change}")
    click(*change)
    park(settle=False)

    cancel = await_button(_S["text"]["dismiss_word"])
    if cancel is None:
        say(f"  {missing(_S['text']['dismiss_word'], _S['text']['change_word'])}")
        say(f"  closing whatever opened and leaving row {position} as it is. Earlier "
            f"positions stand.")
        if not close_dialogs():
            raise RuntimeError(f"a dialog opened after Change on row {position} and "
                               "would not close.")
        return {}
    say(f"  Cancel at {cancel}")
    click(*cancel)
    park(settle=False)

    confirm = await_button(_S["text"]["confirm_word"])
    if confirm is None:
        say(f"  {missing(_S['text']['confirm_word'], _S['text']['dismiss_word'])}")
        say(f"  closing whatever opened; nothing withdrawn. Earlier "
            f"positions stand.")
        if not close_dialogs():
            raise RuntimeError(f"a dialog opened after Cancel on row {position} and "
                               "would not close.")
        return {}
    say(f"  Confirmation at {confirm}")
    click(*confirm)
    park()

    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if not buttons_now():
            break
    else:
        snap(f"{seat}_cancel_refused")
        say(f"  the dialog stayed open after Confirmation on row {position}; the game "
            f"is refusing to withdraw it (it says so when the bag has no "
            f"room for what would come back). Dismissing the dialog and "
            f"leaving row {position} listed. Earlier positions stand.")
        click(*cancel)
        park()
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            if not buttons_now():
                break
            time.sleep(POLL_GAP)
        else:
            if not close_dialogs():
                raise RuntimeError(
                    f"the dialog stayed open after Confirmation on row {position}, "
                    "and neither Cancel nor Escape closed it.")
        return learned

    after = read_line(grab(), row_seat)
    say(f"  row {position} now reads {after!r}")

    tab = inventory_tab_point(WORK_TAB)
    say(f"  back to inventory tab {WORK_TAB} at {tab}; the withdrawal moves "
        f"the panel to whichever tab it landed on")
    click(*tab)
    slot = inventory_slot_point(*landing)
    if slot_is_empty(grab(), *landing):
        recheck = read_line(grab(), row_seat)
        say(f"  nothing landed in tab {WORK_TAB} slot {landing}; row {position} now "
            f"reads {recheck!r}. A sale on row {position} during the cancel left the "
            f"listing in place rather than withdrawing it. Keeping the "
            f"buttons measured so far and letting the relist pass take it.")
        return learned
    say(f"  listing it back from tab {WORK_TAB} slot {landing} at {slot}")
    before = grab()
    ctrl_click(*slot)
    suggested = None
    deadline = time.monotonic() + PANEL_LOAD_TIMEOUT
    while time.monotonic() < deadline:
        suggested = panel_suggestion(panel)
        if suggested is not None:
            break
        time.sleep(POLL_GAP)
    if suggested is None:
        say(f"  nothing loaded on the first ctrl-click; trying once more")
        ctrl_click(*slot)
        deadline = time.monotonic() + PANEL_LOAD_TIMEOUT
        while time.monotonic() < deadline:
            suggested = panel_suggestion(panel)
            if suggested is not None:
                break
            time.sleep(POLL_GAP)
    if suggested is not None:
        centre = _item_centre(before, grab(), panel)
        if centre is not None:
            learned["item_point"] = list(centre)
            say(f"  the loaded item shows at {centre} in the Register Item "
                f"box")
    price = was
    if suggested is None:
        say(f"  the market would not price it after two ctrl-clicks; listing "
            f"at the {price:,} row {position} was withdrawn at")
    else:
        say(f"  panel suggests {suggested:,}; listing at {price:,}, the price "
            f"row {position} was withdrawn at")

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

    if underprice_warning():
        snap("underprice_warning")
        say(f"  the game asks again because {price:,} is at least 25% under "
            f"its average for this item; accepting")
        click(*confirm, settle=0.0)
        if not underprice_warning_gone(budget):
            snap("underprice_warning_stays")
            raise RuntimeError(
                "the underprice question stayed open after Confirmation. "
                "Nothing committed; the item is in the bag.")
        park()
        time.sleep(ACTION_GAP)
        confirm = await_button(_S["text"]["confirm_word"])
        if confirm is None:
            snap("no_confirm_after_warning")
            raise RuntimeError(
                "no Confirmation appeared after the underprice question was "
                "accepted. Nothing committed; the item is in the bag.")
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
    while True:
        try:
            return _measure(close)
        except ServerStalled as exc:
            print(f"  {exc}")
            print("  measuring this screen again from the start")


def _measure(close: bool) -> None:
    from open_inventory import VK_I, VK_ESCAPE, focus_game, press

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

    print("cash shop:")
    cash_block = calibrate_cashshop()
    if cash_block is not None:
        remember("cashshop", cash_block)

    print("opening the Agent Shop:")
    tab = inventory["tabs"][str(facts["agent_shop_tab"])]
    row, col = facts["agent_shop_slot"]
    key = inventory["slots"][f"{row}x{col}"]
    if inventory_open() is None:
        print(f"  the Inventory panel is not on screen; tab "
              f"{facts['agent_shop_tab']} at {tab} is not there to click")
        snap("inventory_gone_before_tab")
        if await_inventory(verbose=True) is None:
            raise RuntimeError(
                "the Inventory panel would not open, so the Agent Shop key "
                "cannot be reached. Nothing clicked.")
    print(f"  tab {facts['agent_shop_tab']} at {tab}")
    click(*tab)

    for attempt in range(1, TOGGLE_TRIES + 1):
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
            if attempt > 1:
                print("  (the shop had been left open by an earlier run; "
                      "clicked again to get back to a known state)")
            break
        if attempt == TOGGLE_TRIES:
            raise RuntimeError(
                f"right-clicked the Agent Shop key {TOGGLE_TRIES} times and "
                f"the Trade window never appeared. Nothing written.")

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

    print("voucher:")
    voucher = calibrate_voucher()

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

    if shop.get("row_last_box"):
        print(f"actions at position {SHOP_VISIBLE}:")
        shop.update(calibrate_actions(shop, seat="row_last",
                                      position=SHOP_VISIBLE))
    else:
        print(f"actions at position {SHOP_VISIBLE}: the last row is not "
              f"placed, so it was not walked")

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
    _enabled = shared["resupply"]["enable_buying"]
    if any(_enabled.get(core) for core in craft_cores()):
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
    if cash_block is not None:
        measured["cashshop"] = cash_block

    existing = {}
    if OUT.exists():
        try:
            existing = json.loads(OUT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}

    out = dict(existing)
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
    if voucher:
        out["voucher"] = {"measured_at": measured["measured_at"],
                          "unit_price": int(voucher)}
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

    import cashshop
    if cashshop.is_open():
        try:
            cashshop.close_cash_shop(verbose=verbose)
        except Exception as exc:
            if verbose:
                print(f"  the Cash Shop stayed open ({exc})")

    park()
    if not inventory_grid_shown(grab()):
        if verbose:
            print("  Inventory already closed")
        return
    press(VK_I)
    time.sleep(gap)
    park()
    if verbose:
        if not inventory_grid_shown(grab()):
            print("  I: Inventory closed")
        else:
            print("  I: pressed; Inventory still open")


def close_gift_window(verbose: bool = False) -> bool:
    from open_inventory import focus_game
    if not focus_game():
        return False
    shut = _gift_reads(CLOSE_WORD)
    if not shut:
        if verbose:
            print("  no gift box open; not clicking Close")
        return False
    click(*shut[0])
    time.sleep(load_shared()["timing"]["action_gap"])
    snap("close_gift_window")
    park()
    if verbose:
        print(f"  {CLOSE_WORD}: gift box closed")
    return True


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

_BANNER_COLOUR = dict(_S["debug"]["banner_colours"])
_BANNER_SCALE = int(_S["debug"]["banner_scale"])
_GLYPH_ROWS = len(next(iter(_BIG_GLYPHS.values())))


def _big_text(word):
    rows = []
    for r in range(_GLYPH_ROWS):
        line = "  ".join("".join(ch * _BANNER_SCALE
                                 for ch in _BIG_GLYPHS[letter][r])
                         for letter in word if letter in _BIG_GLYPHS)
        rows.extend([line] * _BANNER_SCALE)
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
