import base64
import contextlib
import ctypes
import datetime
import difflib
import io
import zoneinfo
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time


def _chosen_config():
    for index, arg in enumerate(sys.argv):
        if arg == "--config" and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
        if arg.startswith("--config="):
            return arg.split("=", 1)[1]
    return ""


def _plain_argv():
    out, skip = [], False
    for arg in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if arg == "--config":
            skip = True
            continue
        if arg in ("--runs", "--record-from", "--from", "--channel"):
            skip = True
            continue
        if (arg.startswith("--config=") or arg == "--frames"
                or arg == "--no-breaks" or arg == "--no-banner"
                or arg.startswith("--runs=")
                or arg.startswith("--channel=")
                or arg.startswith("--record-from=")
                or arg.startswith("--from=")):
            continue
        out.append(arg)
    return out


def _runs():
    for index, arg in enumerate(sys.argv):
        if arg.startswith("--runs="):
            return int(arg.split("=", 1)[1])
        if arg == "--runs" and index + 1 < len(sys.argv):
            return int(sys.argv[index + 1])
    return 1


def _channel():
    for index, arg in enumerate(sys.argv):
        if arg.startswith("--channel="):
            return int(arg.split("=", 1)[1])
        if arg == "--channel" and index + 1 < len(sys.argv):
            return int(sys.argv[index + 1])
    return None


def _record_from():
    for index, arg in enumerate(sys.argv):
        if arg.startswith("--record-from="):
            return arg.split("=", 1)[1]
        if arg == "--record-from" and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return None


def _from_step():
    for index, arg in enumerate(sys.argv):
        if arg.startswith("--from="):
            return arg.split("=", 1)[1]
        if arg == "--from" and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return None


if __name__ == "__main__":
    os.environ["CABAL_CONFIG"] = _chosen_config()

from PIL import Image, ImageChops, ImageStat

import calibration
import open_agent_shop_premium
import row_model
import open_inventory
from open_inventory import VK_I, focus_game, press

_SHARED = calibration.load_shared()
_DUNGEON = _SHARED["dungeon"]
_INPUT = _SHARED["input"]
STORM = tuple(_DUNGEON["storm"])
STORM_SETTLE = float(_DUNGEON["storm_settle"])
PARK = tuple(_DUNGEON["park"])
LEVEL_WINDOWS = [tuple(box) for box in _DUNGEON["level_windows"]]
ENTER_WINDOW = tuple(_DUNGEON["enter_window"])
CHALLENGE_WINDOW = tuple(_DUNGEON["challenge_window"])
EXIT_WINDOW = tuple(_DUNGEON["exit_window"])
LEVEL_TEXT = re.compile(_DUNGEON["level_pattern"], re.IGNORECASE)
ENTER_WORD = _DUNGEON["enter_word"]
CANNOT_ENTER_WORD = _DUNGEON["cannot_enter_word"]
PLAY_TIME_REFILL = _DUNGEON["play_time_refill"]
REFILL = {"done": False}
ENTRIES_LEFT = re.compile(r"\((\d+|[Oo])\s*/\s*(\d+)\)")
RESETS_USED = re.compile(r"(\d+)\s*/\s*(\d+)")
CHALLENGE_WORD = _DUNGEON["challenge_word"]
EXIT_WORD = _DUNGEON["exit_word"]
SELECT_SETTLE = float(_DUNGEON["select_settle"])
ENTER_SETTLE = float(_DUNGEON["enter_settle"])
CHALLENGE_SETTLE = float(_DUNGEON["challenge_settle"])
BOSS_WAIT = float(_DUNGEON["boss_wait"])
WAIT_SLICE = float(_SHARED["timing"]["stop_key_poll"])
KEYS = _DUNGEON["keys"]
ARRIVE = [tuple(step) for step in _DUNGEON["arrive"]]
TARGET_WINDOW = tuple(_DUNGEON["target_window"])
TARGET_WINDOWS = [TARGET_WINDOW] + [tuple(box) for box in
                                    _DUNGEON["two_target_windows"]]
TARGET_TEXT_MIN = int(_DUNGEON["target_text_min"])
TARGET_HP = re.compile(_DUNGEON["target_hp_pattern"])
TARGET_SLOTS = [(slot["name"], tuple(slot["cap"]), tuple(slot["fill"]),
                 tuple(slot["frame"])) for slot in _DUNGEON["target_slots"]]
TARGET_CAP = tuple(_DUNGEON["target_cap"])
TARGET_FRAME = [tuple(colour) for colour in _DUNGEON["target_frame"]]
TARGET_FILL = tuple(_DUNGEON["target_fill"])
TARGET_SLACK = tuple(_DUNGEON["target_slack"])
SEARCH_GRACE = float(_DUNGEON["search_grace"])
SLASH_MISREAD = re.compile(_DUNGEON["slash_misread_pattern"])
LEAVE = _DUNGEON["leave"]
CYCLE_KEYS = {slot: dict(swap) for slot, swap in _DUNGEON["cycle_keys"].items()}
BIKE = _DUNGEON["bike"]
BOSSES = _DUNGEON["bosses"]
ORDER = [int(level) for level in _DUNGEON["order"]]
ROTATE_COUNT = int(_DUNGEON["rotate_count"])
ROTATION = list(_DUNGEON["rotation"])
ROTATION_GROUPS = ([group if isinstance(group, dict)
                    else {"dungeons": list(group)} for group in ROTATION]
                   if ROTATION and all(isinstance(group, (list, dict))
                                       for group in ROTATION)
                   else [{"dungeons": ROTATION}] if ROTATION else [])
ROTATION_NAMES = list(dict.fromkeys(name for group in ROTATION_GROUPS
                                    for name in group["dungeons"]))
ROTATION_RUNS = int(_DUNGEON.get("rotation_runs", 1))
DEATH = _DUNGEON.get("death")
HP_TEXT = re.compile(r"(\d+)/(\d+)")
FAILED_RUNS = {"in_a_row": 0}
RUN_OUTCOME = {"failed": False, "inside": None}
NO_TARGET_STOP = float(_DUNGEON["no_target_stop"] or 0)
LIMITS = {int(level): int(cap)
          for level, cap in _DUNGEON["daily_limits"].items()}
STATE = calibration.LOG_DIR / _DUNGEON["state_file"]
RESET_ZONE = zoneinfo.ZoneInfo(_DUNGEON["reset_timezone"])
RESET_HOUR = datetime.timedelta(hours=float(_DUNGEON["reset_hour"]))
START_AT = _DUNGEON["start_at"]
START_AFTER = datetime.timedelta(
    hours=int(START_AT.split(":")[0]),
    minutes=int(START_AT.split(":")[1])) - RESET_HOUR
TIME_LIMIT = datetime.timedelta(seconds=float(_DUNGEON["time_limit_seconds"]))
HARD_LIMIT = float(_DUNGEON["hard_limit_seconds"])
WAR_QUIET = datetime.timedelta(minutes=float(_DUNGEON["war_quiet_minutes"]))
WAR_LEAD = datetime.timedelta(
    seconds=HARD_LIMIT + float(_DUNGEON["war_margin_seconds"]))
sys.path.append(str(calibration.HERE.parent / "tools"))
CLEARED_WINDOW = tuple(_DUNGEON["cleared_window"])
CLEARED_TEXT = re.compile(_DUNGEON["cleared_pattern"], re.IGNORECASE)
BUTTONS = _DUNGEON["buttons"]
BUTTON_TIMEOUT = float(_SHARED["timing"]["dialog_timeout"])
FINISH = [tuple(step) for step in _DUNGEON["finish"]]
DUNGEONS = _DUNGEON["dungeons"]
TRADING_DUNGEON = _DUNGEON["trading_dungeon"]
ENTRY_RETRIES = int(_DUNGEON["entry_retries"])
CHALLENGE_RETRY_WAIT = float(_DUNGEON["challenge_retry_wait"])
WB_AREA_SEQUENCE = _DUNGEON["wb_area_sequence"]
WB_AREA_WAIT = float(_DUNGEON["wb_area_wait"])
WB_AREA_CHECK = _DUNGEON["wb_area_check"]
DISENCHANT_ICON = tuple(_DUNGEON["disenchant_icon"])
SORT_ICON = tuple(_DUNGEON["sort_icon"])
DISENCHANT_WAIT = float(_DUNGEON["disenchant_wait"])
MENU_WINDOW = tuple(_SHARED["world_boss"]["menu_window"])
MENU_WORD = _SHARED["world_boss"]["menu_word"]
ESCAPE_TRIES = int(_SHARED["world_boss"]["escape_tries"])
ESCAPE_GAP = float(_SHARED["world_boss"]["escape_gap"])
ACTIVE = {"level": None, "until": None, "deadline": None, "frames": []}
TAB_TWO = tuple(_DUNGEON["tab_two"])
NOTIFICATION_CLOSE = _DUNGEON["notification_close"]
NOTIFICATION_CLICKS = int(_DUNGEON["notification_clicks"])
NOTIFICATION_GAP = float(_DUNGEON["notification_gap"])
TRADE_TABS = [(tuple(tab["window"]), tab["word"])
              for tab in _DUNGEON["trade_tabs"]]
SEQUENCES = (calibration.HERE / _DUNGEON["sequences_dir"]
             / calibration.config_name())
WALK_FRAMES = {name: float(every)
               for name, every in (_DUNGEON.get("walk_frames") or {}).items()}
TARGET_KEY = _DUNGEON["kill_target_key"]
KILL_COMBO = [tuple(step) for step in _DUNGEON["kill_combo"]]
MAIN_BM = tuple(_DUNGEON["main_bm"])
ALTERNATE_BM = tuple(_DUNGEON["alternate_bm"])
BM_ICON = tuple(_DUNGEON["bm_icon"])
CANCEL_BM_CLICKS = int(_DUNGEON["cancel_bm_clicks"])
CANCEL_BM_GAP = float(_DUNGEON["cancel_bm_gap"])
MAIN_BM_CANCEL_WAIT = float(_DUNGEON["main_bm_cancel_wait"])
MAIN_BM_CAST_WAIT = float(_DUNGEON["main_bm_cast_wait"])
AURA = tuple(_DUNGEON["aura"])
BM_POT = tuple(_DUNGEON["bm_pot"])
OFFENSIVE_BUFFS = {name: tuple(point)
                   for name, point in _DUNGEON["offensive_buffs"].items()}
DEFENSIVE = "defensive_buffs"
BUFF_POINTS = {**_DUNGEON["offensive_buffs"], **_DUNGEON[DEFENSIVE]}
DEFENSIVE_BUFFS = [(name, tuple(BUFF_POINTS[name]))
                   for name in (_DUNGEON["defensive_slots"]
                                or _DUNGEON[DEFENSIVE])]
DEFENSIVE_SWITCH = float(_DUNGEON["defensive_switch"])
BUFF_PRESSES = int(_DUNGEON["buff_presses"])
BUFF_PRESS_GAP = float(_DUNGEON["buff_press_gap"])
LOOP_GAP = float(_DUNGEON["loop_gap"])
ONCE_PER_TARGET = list(_DUNGEON["once_per_target"])
ATTACK_KEYS = list(_DUNGEON["bm_attack"]["keys"])
BM_ATTACK = {slot: list(keys) for slot, keys in _DUNGEON["bm_attack"].items()
             if slot != "keys"}
BM_ATTACK_GAP = float(_DUNGEON["bm_attack_gap"])
AURA_GAP = float(_DUNGEON["aura_gap"])
USE_GAP = float(_DUNGEON["use_gap"])
BM_LOOP = list(_DUNGEON["bm_loop"])
BM_AURA = list(_DUNGEON["bm_aura"])
BM_CYCLE_SET = _DUNGEON["bm_cycle"]
BM_AURA_FOR = float(_DUNGEON["bm_aura_for"])
AURA_AFTER = float(_DUNGEON["aura_after"])
AURA_FOR = float(_DUNGEON["aura_for"])
AURA_TARGET = bool(_DUNGEON["aura_target"])
MAIN_BM_AFTER = float(_DUNGEON["main_bm_after"])
AURA_DUE = {"at": None, "after": None, "for": None, "since": None,
            "target": AURA_TARGET}
AURA_WINDOW = {"until": None, "since": None, "last": None, "clicks": 0}
MAIN_BM_DUE = {"at": None, "after": None, "for": None}
MAIN_BM_STATE = {"on": False}
ALT_BM = {"on": False}
HUNT_TARGET = {"last": 0.0}
LOCK_ALL = {"on": False, "every": None, "twin": None}
ATTACK_PRESS = {"last": 0.0, "key": None}
MAIN_RECAST = {"at": None, "dungeon": None}
BM_CYCLE = {"slot": None, "ends": None, "extended": False, "last": None,
            "resume": False, "off": {}, "keep_aura": False, "other": None,
            "pressed": None, "aura_target": False, "with_aura": False,
            "aura_at": None}
OTHER_BM_EVERY = float(BM_CYCLE_SET["other_every"])
OTHER_AURA_AFTER = float(BM_CYCLE_SET["other_aura_after"])
BM_OFF = {"at": None}
COOLDOWN = {"after": None}
SEARCH_ATTACK = {"keys": []}
MAIN_BM_OFF = {"at": None}
MAIN_BM_CAST = {"at": None}
POWERFUL_POT = {"at": None}
WORLD_BOSS = {"done": None}
BURST = _DUNGEON.get("burst")
NO_BREAKS = "--no-breaks" in sys.argv
NO_BANNER = "--no-banner" in sys.argv
CHANNEL = _channel()
BURST_STATE = {"until": None, "on_channel": False}
LAST_KILL = {"name": None, "words": None, "loop": None}
REPORT = _SHARED["supervise"]
STATE_LOCK = threading.RLock()
JEWELS = _DUNGEON["jewels"]
SUMMARY_DAYS = int(_DUNGEON["summary_days"])
LOOP_POINTS = {"main_bm": MAIN_BM, "alternate_bm": ALTERNATE_BM, "aura": AURA,
               "bm_pot": BM_POT, "bm_icon": BM_ICON, **OFFENSIVE_BUFFS}
SLOTS = {name: tuple(point) for name, point in _DUNGEON["slots"].items()}
CLICK_POINTS = {**LOOP_POINTS, **SLOTS}
ALT_ROW = _DUNGEON["alt_row"]
SLOT_MODIFIER = ALT_ROW["modifier"]
ALT_KEYS = {(x, ALT_ROW["y"]): key
            for x, key in zip(ALT_ROW["x"], ALT_ROW["keys"])}
KEYED_SLOTS = {name: ALT_KEYS.get(CLICK_POINTS[name])
               for name in _DUNGEON["keyed_points"]}
if None in KEYED_SLOTS.values():
    raise SystemExit(
        f"keyed points off the {SLOT_MODIFIER} hotbar row "
        f"{ALT_ROW['y']}: "
        f"{[name for name, key in KEYED_SLOTS.items() if key is None]}")
HEAL_KEY = _DUNGEON["heal_key"]
HEAL_EVERY = float(_DUNGEON["heal_every"])
HEAL = {"every": HEAL_EVERY}
END_WAIT = float(_DUNGEON["end_wait"])
END_TIMEOUT = float(_DUNGEON["end_timeout"])
END_POLL = float(_DUNGEON["end_poll"])
OVER = threading.Event()
HUNTING = threading.Event()
TARGET_WATCH = {"watch": None}
RUN_CLOCK = {"started": None, "rooms": []}
PLAYING = threading.Event()
END = {"at": None, "defeated": False, "overtime": False, "quest_failed": False}
EXIT = [tuple(step) for step in _DUNGEON["exit"]]
MANUAL_EXIT = _DUNGEON["manual_exit"]
DEFEAT = [tuple(step) for step in _DUNGEON["defeat"]]
DEFEAT_SIGN = BUTTONS[DEFEAT[0][1]]
REVIVE_SETTLE = float(_DUNGEON["revive_settle"])
DEATH_WATCH_EVERY = float(_DUNGEON["death_watch_every"])
BANNER = _DUNGEON["banner_window"]
BANNER_FILL = _DUNGEON["banner_fill"]
BANNER_SAFE = {BANNER_FILL["dungeon"]} if BANNER_FILL else set()
BANNER_FILLS = {"window": None, "done": 0}
DEATH_WATCH = {"on": False, "armed": False, "entered": False, "again": False}
DEATH_LOCK = threading.Lock()
CHAT_OPEN = float(_DUNGEON["chat_open"])
CHAT_EVERY = _DUNGEON["chat_every"]
CHAT_READS = _DUNGEON["chat_reads"]
ROOM_PREFIX = _DUNGEON["room_prefix"]
_CHAT_LINES = _DUNGEON["chat_lines"]
CHAT_BOXES = [(_CHAT_LINES["left"], _CHAT_LINES["top"] + row * _CHAT_LINES["gap"],
               _CHAT_LINES["right"],
               _CHAT_LINES["top"] + row * _CHAT_LINES["gap"]
               + _CHAT_LINES["height"])
              for row in range(_CHAT_LINES["count"])]
UNIQUE_TEXT = re.compile(_DUNGEON["unique_pattern"], re.IGNORECASE)
CHAT_SAME = float(_DUNGEON["chat_same"])
UNIQUE_FILE = calibration.LOG_DIR / _DUNGEON["unique_file"]
UNIQUE_IGNORE = [item.casefold() for item in _DUNGEON["unique_ignore"]]
UNIQUE_DAYS = int(_DUNGEON["unique_days"])
CHAT = {"seen": None, "run": None, "last": None, "fresh": False}
CHAT_WORK = threading.Lock()
CHAT_READER = {"proc": None, "lines": None, "down": None, "down_run": None,
               "run": 0}
CHAT_READER_LOCK = threading.Lock()
KILL_DONE = {"at": None}
KILL_STEPS = ("kill", "kill_bm", "kill_all", "kill_before_boss",
              "hold_kill_all", "ride_kill_all", "look_kill_bm")
FAILED_FRAMES = _DUNGEON["failed_frames"]
FAILED_VIDEOS = _DUNGEON["failed_videos"]
RUN_START = {"frame": None}
RUN_FILM = {"tape": None, "path": None}
DISCONNECT_GAP = float(_SHARED["supervise"]["screen_gap"])
DISCONNECT_RECHECK = float(_SHARED["supervise"]["disconnect_kill"])
RECOVER_RETRIES = int(_SHARED["supervise"]["recover_retries"])
RECOVER_WAIT = float(_SHARED["supervise"]["recover_wait"])
RELOG_TRIES = int(_SHARED["supervise"]["relog_tries"])
RELOGGING = threading.Event()
COMMIT = {"sha": None}
CONSOLE_LINES = [re.compile(pattern) for pattern in _DUNGEON["console_lines"]]
VIEW_SET = _DUNGEON["console_view"]
VIEW_LIVE = [re.compile(pattern) for pattern in VIEW_SET["live"]]
LIVE = {"view": None}
UNCOMMITTED_IGNORE = set(_DUNGEON["uncommitted_ignore"])
TARGET_EVERY = float(_DUNGEON["target_every"])
HUNT_TARGET_EVERY = float(_DUNGEON["hunt_target_every"])
TWIN_BM = _DUNGEON["twin_bm"]
TWIN_TARGETS = int(_DUNGEON["twin_targets"])
KEY_LOCK = threading.Lock()
REPLAY_MODIFIERS = set(_DUNGEON["replay_modifiers"])


def tap(vk, hold=None):
    with KEY_LOCK:
        if hold is None:
            press(vk)
        else:
            press(vk, hold=hold)
KEY_CELLS = [tuple(cell) for cell in _DUNGEON["key_cells"]]
DIFFICULTY = _DUNGEON["difficulty"]
PURCHASES = _DUNGEON["purchases"]
ORBS = _DUNGEON["summon_orbs"]
QUANTITY_CLEAR = int(_DUNGEON["quantity_clear"])
QUANTITY_MAX = int(_DUNGEON["quantity_max"])
KEY_WINDOW = tuple(_DUNGEON["key_window"])
KEY_TEXT = re.compile(_DUNGEON["key_pattern"], re.IGNORECASE)
BUY_SETTLE = float(_DUNGEON["buy_settle"])
TRANSFER_TIMEOUT = float(_DUNGEON["transfer_timeout"])
INVENTORY_TAB = int(_DUNGEON["inventory_tab"])
KEY_TAB = int(_DUNGEON["key_tab"])
LOCK = {"boss": None, "reading": None, "cleared": False, "level": ORDER[0] if ORDER else None,
        "killed": False, "watching": False, "reads": 0, "hits": [],
        "targets": 0, "target_hp": None, "named": False, "live": 0}
ENDED = threading.Event()


def click(x, y, settle):
    calibration._button(_INPUT["MOUSEEVENTF_LEFTDOWN"],
                        _INPUT["MOUSEEVENTF_LEFTUP"], x, y, settle)
    calibration.snap(f"click_{x}_{y}")


def slot_way(name):
    key = KEYED_SLOTS.get(name)
    return (f"pressed with {SLOT_MODIFIER}+{key}" if key is not None
            else f"right-clicked at {list(CLICK_POINTS[name])}")


def hold_modifier(down):
    event = open_inventory._event(KEYS[SLOT_MODIFIER], up=not down)
    open_inventory._user32.SendInput(1, ctypes.byref(event),
                                     ctypes.sizeof(open_inventory._Input))


def use_point(name, settle):
    key = KEYED_SLOTS.get(name)
    if key is None:
        right_click(*CLICK_POINTS[name], settle)
        return
    with KEY_LOCK:
        hold_modifier(True)
        try:
            press(KEYS[key])
        finally:
            hold_modifier(False)
    calibration.snap(f"key_{SLOT_MODIFIER}_{key}")
    time.sleep(settle)


def right_click(x, y, settle):
    calibration._button(_INPUT["MOUSEEVENTF_RIGHTDOWN"],
                        _INPUT["MOUSEEVENTF_RIGHTUP"], x, y, settle)
    calibration.snap(f"rightclick_{x}_{y}")


def centre(box):
    return [(box[0] + box[2]) // 2, (box[1] + box[3]) // 2]


def scaled(crop):
    return crop.resize((crop.width * row_model.BACKUP_SCALE,
                        crop.height * row_model.BACKUP_SCALE), Image.LANCZOS)


def white_text(image, box):
    red, green, blue = image.crop(box).convert("RGB").split()
    low = ImageChops.darker(ImageChops.darker(red, green), blue)
    return scaled(low.point(lambda v: 0 if v >= TARGET_TEXT_MIN else 255))


def read_crops(crops):
    texts = row_model._backup_texts(crops)
    if texts is None:
        fresh_reader()
        texts = row_model._backup_texts(crops)
    if texts is None:
        raise RuntimeError(f"the Paddle reader did not answer, and neither "
                           f"did a fresh one ({row_model._reader['failed']}); "
                           f"nothing read.")
    return [(text or "").strip() for text in texts]


def fresh_reader():
    with row_model._reading:
        proc, why = row_model._reader["proc"], row_model._reader["failed"]
        if not why or (proc is not None and proc.poll() is None):
            return
        print(f"  the Paddle reader stopped ({why}); a fresh one starts with "
              f"this read")
        row_model._reader["failed"] = None


def read_windows(image, windows):
    return read_crops([scaled(image.crop(box)) for box in windows])


def read_rows(image, windows):
    tight = [calibration.ink_box(image, box) for box in windows]
    texts = iter(read_crops([scaled(image.crop(box)) for box in tight
                             if box is not None]) if any(tight) else [])
    return [next(texts) if box is not None else "" for box in tight]


def boss_in(seen):
    name = BOSSES.get(str(LOCK["level"]))
    if name and row_model._key(name) in row_model._key(seen):
        return name
    return None


def targeted_boss(image):
    seen = read_crops([white_text(image, TARGET_WINDOW)])[0]
    return boss_in(seen), seen


def _read_target(image, key):
    seen, banner = read_crops([white_text(image, TARGET_WINDOW),
                               scaled(image.crop(CLEARED_WINDOW))])
    if CLEARED_TEXT.search(banner) and not LOCK["cleared"]:
        LOCK["cleared"] = True
        calibration.snap("dungeon_cleared", image)
        print(f"  {banner!r}: the dungeon is cleared, so hit_boss stops")
        return
    boss = boss_in(seen)
    if boss is not None and LOCK["boss"] is None:
        LOCK["boss"] = boss
        calibration.snap("boss_targeted", image)
        print(f"  the target bar reads {seen!r}: {boss} is targeted, so no "
              f"more {key}")
    elif boss is None and LOCK["boss"] is not None:
        print(f"  the target bar reads {seen!r}: {LOCK['boss']} is gone, so "
              f"{key} again")
        LOCK["boss"] = None
        calibration.snap("boss_gone", image)


def wait_for(window, word, timeout):
    deadline = time.monotonic() + timeout
    while True:
        seen = read_windows(calibration.grab(), [window])[0]
        if row_model._key(word) in row_model._key(seen):
            return True, seen
        if time.monotonic() >= deadline:
            return False, seen
        time.sleep(WAIT_SLICE)


def wait_for_enter():
    deadline = time.monotonic() + BUTTON_TIMEOUT
    while True:
        seen = read_windows(calibration.grab(), [ENTER_WINDOW])[0]
        key = row_model._key(seen)
        if (row_model._key(ENTER_WORD) in key
                and row_model._key(CANNOT_ENTER_WORD) not in key):
            return True, seen
        if time.monotonic() >= deadline:
            return False, seen
        time.sleep(WAIT_SLICE)


def wait_for_level(box, level, timeout):
    deadline = time.monotonic() + timeout
    while True:
        seen = read_windows(calibration.grab(), [box])[0]
        found = LEVEL_TEXT.search(seen)
        if found is not None and int(found.group(1)) == level:
            return True, seen
        if time.monotonic() >= deadline:
            return False, seen
        time.sleep(WAIT_SLICE)


def press_button(name, settle, verbose=True, required=True):
    button = BUTTONS[name]
    window = tuple(button["window"])
    timeout = float(button.get("timeout", BUTTON_TIMEOUT))
    up, seen = wait_for(window, button["word"], timeout)
    if not up and not required:
        if verbose:
            print(f"  no {button['word']!r} in the {name} window {list(window)} "
                  f"within {timeout:g}s; it reads {seen!r}, so nothing to "
                  f"claim")
        return
    if not up:
        raise RuntimeError(
            f"the {name} window {list(window)} never read {button['word']!r} "
            f"within {timeout:g}s; it reads {seen!r}. Nothing clicked.")
    if CHAT_READS is not None and name == CHAT_READS["button"]:
        room_chat(verbose)
    point = centre(window)
    if verbose:
        print(f"  {button['word']} at {point}")
    click(*point, settle)


def prepare(verbose=True, tab=INVENTORY_TAB):
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    open_agent_shop_premium.ensure_inventory_open(verbose=verbose,
                                                  tesseract=False)
    point = calibration.inventory_tab_point(tab)
    if verbose:
        print(f"  inventory tab {tab} at {list(point)}")
    click(*point, SELECT_SETTLE)


def close_inventory(verbose=True):
    for _attempt in range(open_agent_shop_premium.TOGGLE_TRIES):
        press(VK_I)
        time.sleep(open_agent_shop_premium.ACTION_GAP)
        if not open_agent_shop_premium.panel_open(verbose=False,
                                                  tesseract=False):
            if verbose:
                print("  inventory closed")
            return
    raise RuntimeError(
        f"pressed I {open_agent_shop_premium.TOGGLE_TRIES} times; the "
        f"Inventory panel is still open.")


def key_tab_slots(verbose=True):
    prepare(verbose, KEY_TAB)
    calibration.park()
    return calibration.occupied_slots()


def use_key(before, verbose=True):
    landed = sorted(key_tab_slots(verbose) - before)
    if len(landed) != 1:
        raise RuntimeError(
            f"tab {KEY_TAB} gained {landed or 'no slot'} with the purchase, "
            f"not one key. Nothing right-clicked.")
    point = calibration.inventory_slot_point(*landed[0])
    if verbose:
        print(f"  the key landed in tab {KEY_TAB} slot {landed[0]} at "
              f"{list(point)}; right-clicking it")
    right_click(*point, SELECT_SETTLE)


def relog(verbose=True):
    import recovery
    if verbose:
        print(f"  relogging into {recovery.HOME_CHANNEL}")
    with relogging():
        recover_retrying(lambda: recovery.relog(verbose=verbose), verbose)


def recover_retrying(first, verbose=True, channel=None):
    import recovery
    failed = relogged = 0
    attempt = first
    while True:
        try:
            return attempt()
        except Exception as exc:
            failed += 1
            if failed > RECOVER_RETRIES:
                if relogged >= RELOG_TRIES:
                    raise
                relogged += 1
                failed = 0
                print(f"  recovery failed {RECOVER_RETRIES + 1} times: {exc}; "
                      f"relogging ({relogged} of {RELOG_TRIES})")
                try:
                    recovery.relog(verbose=verbose, channel=channel)
                except Exception as refused:
                    print(f"  the relog was refused too: {refused}")
                    raise exc from refused
            else:
                print(f"  recovery attempt {failed} failed: {exc}; attempt "
                      f"{failed + 1} in {RECOVER_WAIT:g}s")
                time.sleep(RECOVER_WAIT)
            attempt = lambda: recovery.recover(verbose=verbose,
                                               channel=channel)


def relog_home(verbose=True):
    if verbose:
        print("  dead in the dungeon: a full relog, through the channel list "
              "to the home channel")
    relog(verbose)


def buy_key(level, verbose=True):
    if not 1 <= level <= len(KEY_CELLS):
        raise RuntimeError(f"no key cell for level {level}; the shop row "
                           f"holds levels 1-{len(KEY_CELLS)}.")
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    tab = BUTTONS["shop_dungeon"]
    if not wait_for(tuple(tab["window"]), tab["word"], 0)[0]:
        if verbose:
            print("  N for the shop")
        press(_INPUT["VK_N"])
    press_button("shop_dungeon", SELECT_SETTLE, verbose)
    x, y = KEY_CELLS[level - 1]
    if verbose:
        print(f"  Alt+click the level {level} key at {[x, y]}")
    calibration.alt_click(x, y, settle=SELECT_SETTLE)
    calibration.snap(f"altclick_{x}_{y}")
    deadline = time.monotonic() + BUTTON_TIMEOUT
    while True:
        seen = read_windows(calibration.grab(), [KEY_WINDOW])[0]
        found = KEY_TEXT.search(seen)
        if found is not None or time.monotonic() >= deadline:
            break
        time.sleep(WAIT_SLICE)
    if found is None:
        raise RuntimeError(
            f"no Purchase Item dialog naming a key after the Alt+click; "
            f"{list(KEY_WINDOW)} reads {seen!r}. Nothing bought.")
    if int(found.group(1)) != level:
        press_button("key_cancel", SELECT_SETTLE, verbose)
        raise RuntimeError(
            f"the dialog offers {seen!r}, not the level {level} key. "
            f"Cancelled; nothing bought.")
    if verbose:
        print(f"  the dialog offers {seen!r}")
    press_button("key_ok", BUY_SETTLE, verbose)
    press(_INPUT["VK_N"])
    if wait_for(tuple(tab["window"]), tab["word"], 0)[0]:
        raise RuntimeError("the shop is still open after N.")
    if verbose:
        print("  bought; N closed the shop")


def get_entry(x, y, verbose=True):
    before = key_tab_slots(verbose)
    tab = BUTTONS["shop_dungeon"]
    if not wait_for(tuple(tab["window"]), tab["word"], 0)[0]:
        if verbose:
            print("  N for the shop")
        press(_INPUT["VK_N"])
    press_button("shop_dungeon", SELECT_SETTLE, verbose)
    if verbose:
        print(f"  Alt+click the entry at {[x, y]}")
    calibration.alt_click(x, y, settle=0.0)
    press_button("key_ok", 0.0, verbose)
    deadline = time.monotonic() + BUTTON_TIMEOUT
    arrived = calibration.occupied_slots() - before
    while not arrived and time.monotonic() < deadline:
        time.sleep(WAIT_SLICE)
        arrived = calibration.occupied_slots() - before
    landed = sorted(arrived)
    press(_INPUT["VK_N"])
    if verbose:
        print("  bought; N to close the shop")
    if len(landed) != 1:
        raise RuntimeError(
            f"tab {KEY_TAB} gained {landed or 'no slot'} with the purchase, "
            f"not one entry. Nothing noted.")
    entry = note_entry(landed[0])
    if verbose:
        print(f"  the entry landed in tab {KEY_TAB} slot {landed[0]} at "
              f"{entry['point']}; noted in {STATE.name}")
    return entry


def type_quantity(quantity):
    for _press in range(QUANTITY_CLEAR):
        tap(KEYS["Backspace"])
    for digit in str(quantity):
        tap(KEYS[digit])


def buy_item(name, verbose=True, quantity=1):
    item = PURCHASES[name]
    prepare(verbose, int(item["inventory_tab"]))
    shop = BUTTONS["shop_dungeon"]
    if not wait_for(tuple(shop["window"]), shop["word"], 0)[0]:
        if verbose:
            print("  N for the shop")
        press(_INPUT["VK_N"])
    press_button(item["tab"], SELECT_SETTLE, verbose)
    x, y = item["cell"]
    if verbose:
        print(f"  Alt+click {item['name']} at {[x, y]}")
    calibration.alt_click(x, y, settle=0.0)
    if quantity != 1:
        ok = BUTTONS["key_ok"]
        wait_for(tuple(ok["window"]), ok["word"], BUTTON_TIMEOUT)
        type_quantity(quantity)
        if verbose:
            print(f"  quantity {quantity} typed")
    press_button("key_ok", 0.0, verbose)
    press(_INPUT["VK_N"])
    if wait_for(tuple(shop["window"]), shop["word"], 0)[0]:
        raise RuntimeError("the shop is still open after N.")
    if verbose:
        print(f"  bought {quantity}x {item['name']} into inventory tab "
              f"{item['inventory_tab']}; N closed the shop")


def orbs_per_run(dungeon_name):
    steps = (list(DUNGEONS[dungeon_name]["sequence"])
             + list(DUNGEONS[dungeon_name].get("after_exit", [])))
    return sum(step[1:].count(ORBS["key"]) for step in steps
               if isinstance(step, list) and step and step[0] == "tap")


def orb_runs_today(dungeon_name, location):
    if LEVEL_TEXT.search(location_of(dungeon_name, location)["name"]) is None:
        return runs_today(dungeon_name)
    return sum(ran_today(_state(), server_day()).values())


def powerful_pot_due(dungeon_name, verbose=True):
    pot = DUNGEONS[dungeon_name].get("powerful_pot")
    if pot is None:
        return
    every = float(pot["every"])
    since = (None if POWERFUL_POT["at"] is None
             else time.monotonic() - POWERFUL_POT["at"])
    if since is not None and since < every:
        return
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    if verbose:
        print(f"  {dungeon_name}: powerful pot {pot['slot']} "
              + ("for the first time in this launch" if since is None
                 else f"{since:.0f}s after the last one")
              + f"; the next one {every:g}s from now; hotbar tab II at "
              f"{list(TAB_TWO)} first, where the pot is")
    click(*TAB_TWO, SELECT_SETTLE)
    use_point(pot["slot"], SELECT_SETTLE)
    POWERFUL_POT["at"] = time.monotonic()


def pre_run_buys(dungeon_name, location, verbose=True):
    limit = DUNGEONS[dungeon_name].get("daily_limit")
    if limit is not None and runs_today(dungeon_name) >= int(limit):
        return
    close_notifications(verbose)
    level = DUNGEONS[dungeon_name].get("difficulty")
    buy = DIFFICULTY["buys"].get(str(level)) if level is not None else None
    if buy is not None:
        done = runs_today(dungeon_name)
        every = int(buy["every"])
        item = PURCHASES[buy["item"]]["name"]
        if done % every == 0:
            if verbose:
                print(f"  {done} {dungeon_name} run(s) so far today; "
                      f"difficulty {level} takes 1x {item} every {every} "
                      f"runs: buying it now")
            buy_item(buy["item"], verbose)
        elif verbose:
            print(f"  {done} {dungeon_name} run(s) so far today; the next 1x "
                  f"{item} is bought in {every - done % every} run(s)")
    summons = orbs_per_run(dungeon_name) if ORBS is not None else 0
    if summons:
        done = orb_runs_today(dungeon_name, location)
        every = int(ORBS["every"])
        item = PURCHASES[ORBS["item"]]["name"]
        if done % every == 0:
            if verbose:
                print(f"  {done} {dungeon_name} run(s) so far today, each "
                      f"summoning {summons}x with {ORBS['key']}; {summons}x "
                      f"{item} every {every} runs: buying it now")
            buy_item(ORBS["item"], verbose, summons)
        elif verbose:
            print(f"  {done} {dungeon_name} run(s) so far today, each "
                  f"summoning {summons}x with {ORBS['key']}; the next "
                  f"{summons}x {item} is bought in {every - done % every} "
                  f"run(s)")


def buy_max(name, verbose=True):
    item = PURCHASES[name]
    prepare(verbose, int(item["inventory_tab"]))
    shop = BUTTONS["shop_dungeon"]
    if not wait_for(tuple(shop["window"]), shop["word"], 0)[0]:
        if verbose:
            print("  N for the shop")
        press(_INPUT["VK_N"])
    press_button(item["tab"], SELECT_SETTLE, verbose)
    x, y = item["cell"]
    if verbose:
        print(f"  Alt+click {item['name']} at {[x, y]}")
    calibration.alt_click(x, y, settle=0.0)
    ok = BUTTONS["key_ok"]
    up, seen = wait_for(tuple(ok["window"]), ok["word"], BUTTON_TIMEOUT)
    if up:
        type_quantity(QUANTITY_MAX)
        if verbose:
            print(f"  quantity {QUANTITY_MAX} typed; the window keeps it to "
                  f"the most there is enough for")
        press_button("key_ok", BUY_SETTLE, verbose)
        if wait_for(tuple(ok["window"]), ok["word"], 0)[0]:
            if verbose:
                print("  the purchase window is still up after OK, so nothing "
                      "was bought")
            press_button("key_cancel", 0.0, verbose)
    elif verbose:
        print(f"  no purchase window within {BUTTON_TIMEOUT:g}s; the "
              f"{ok['word']} window reads {seen!r}, so nothing bought")
    press(_INPUT["VK_N"])
    if wait_for(tuple(shop["window"]), shop["word"], 0)[0]:
        raise RuntimeError("the shop is still open after N.")
    if verbose:
        print(f"  {item['name']} done; N closed the shop")


def set_difficulty(level, verbose=True):
    point = tuple(DIFFICULTY["points"][str(level)])
    if verbose:
        print(f"  difficulty {level} on the slider at {list(point)}")
    click(*point, 0.0)


def note_entry(slot):
    state = _state()
    state["entry"] = {"tab": KEY_TAB, "slot": list(slot),
                      "point": list(calibration.inventory_slot_point(*slot))}
    _save(state)
    return state["entry"]


def forget_entry():
    state = _state()
    if state.pop("entry", None) is not None:
        _save(state)


def names(text, name):
    words = r"\W*".join(re.escape(word)
                        for word in re.findall(r"[0-9A-Za-z]+", name))
    return re.search(words + r"(?![0-9A-Za-z])", text,
                     re.IGNORECASE) is not None


def find_in_list(name, timeout, words=None, windows=LEVEL_WINDOWS):
    deadline = time.monotonic() + timeout
    while True:
        image = calibration.grab()
        reads = read_rows(image, windows)
        for box, seen in zip(windows, reads):
            if (all(row_model._key(word) in row_model._key(seen)
                    for word in words) if words else names(seen, name)):
                calibration.snap("dungeon_list", image)
                return box, reads
        if time.monotonic() >= deadline:
            calibration.snap("no_dungeon_list", image)
            return None, reads
        calibration.halt_if_stopped()
        time.sleep(WAIT_SLICE)


def location_of(dungeon_name, location):
    if dungeon_name not in DUNGEONS:
        raise RuntimeError(f"no dungeon {dungeon_name!r} in dungeon.dungeons; "
                           f"it holds {sorted(DUNGEONS)}. Nothing clicked.")
    places = DUNGEONS[dungeon_name]["locations"]
    if location not in places:
        raise RuntimeError(f"no location {location!r} in {dungeon_name}; it "
                           f"holds {sorted(places)}. Nothing clicked.")
    return places[location]


def only_location(dungeon_name):
    places = sorted(DUNGEONS[dungeon_name]["locations"])
    if len(places) != 1:
        raise SystemExit(f"{dungeon_name} has {len(places)} locations "
                         f"({', '.join(places)}); name one: dungeon.py "
                         f"{dungeon_name} LOCATION")
    return places[0]


def hard_limit(dungeon_name):
    return float(DUNGEONS[dungeon_name].get("hard_limit_seconds", HARD_LIMIT))


def end_wait(dungeon_name):
    return float(DUNGEONS[dungeon_name].get("end_wait", END_WAIT))


def manual_exit(dungeon_name):
    return DUNGEONS[dungeon_name].get("manual_exit", MANUAL_EXIT)


def note_main_bm_off():
    if MAIN_BM_STATE["on"]:
        MAIN_BM_OFF["at"] = time.monotonic()


def wait_after_main_bm_off(verbose=True):
    if MAIN_BM_OFF["at"] is None:
        return
    left = MAIN_BM_OFF["at"] + MAIN_BM_CANCEL_WAIT - time.monotonic()
    if left <= 0:
        return
    if verbose:
        print(f"  the main BM was cancelled "
              f"{MAIN_BM_CANCEL_WAIT - left:.1f}s ago; {left:.1f}s more so "
              f"the recording starts {MAIN_BM_CANCEL_WAIT:g}s after it")
    wait_timer(left, False)


def wait_after_main_bm_cast(verbose=True):
    if MAIN_BM_CAST["at"] is None:
        return
    left = MAIN_BM_CAST["at"] + MAIN_BM_CAST_WAIT - time.monotonic()
    if left <= 0:
        return
    if verbose:
        print(f"  the main BM was switched on "
              f"{MAIN_BM_CAST_WAIT - left:.1f}s ago; {left:.1f}s more so "
              f"the recording starts {MAIN_BM_CAST_WAIT:g}s after it")
    wait_timer(left, False)


def cancel_bm_clicks(dungeon_name):
    return int(DUNGEONS[dungeon_name].get("cancel_bm_clicks",
                                          CANCEL_BM_CLICKS))


def cancel_bm(dungeon_name, verbose=True):
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    clicks = cancel_bm_clicks(dungeon_name)
    for _click in range(clicks):
        right_click(*BM_ICON, CANCEL_BM_GAP)
    BM_OFF["at"] = time.monotonic()
    note_main_bm_off()
    calibration.snap("cancel_bm")
    MAIN_BM_STATE["on"] = False
    ALT_BM["on"] = False
    if BM_CYCLE["slot"] is not None:
        BM_CYCLE["off"][BM_CYCLE["slot"]] = time.monotonic()
    BM_CYCLE.update(last=BM_CYCLE["slot"] or BM_CYCLE["last"], slot=None,
                    ends=None, extended=False, resume=False)
    pending = AURA_DUE["at"] is not None
    AURA_DUE["at"] = None
    if verbose:
        print(f"  BM icon right-clicked {clicks} times at {list(BM_ICON)}, "
              f"{CANCEL_BM_GAP:g}s after each"
              + ("; the aura still due for that BM is dropped" if pending
                 else ""))


def setting(dungeon_name, key):
    return DUNGEONS[dungeon_name].get(key, _DUNGEON[key])


def refill_play_time(row_y, verbose=True):
    refill = PLAY_TIME_REFILL
    settle = float(refill["settle"])
    if verbose:
        print(f"  {CANNOT_ENTER_WORD} {ENTER_WORD}: the dungeon play time is "
              f"refilled: "
              f"Refill on the row at {[refill['row_x'], row_y]}, the gem "
              f"option at {refill['gem']}, {refill['time_name']!r} from the "
              f"Refill Time list, Refill at {refill['refill']}, then "
              f"{BUTTONS['warp_yes']['word']}")
    for point in ([refill["row_x"], row_y], refill["gem"],
                  refill["time_menu"], refill["time_option"],
                  refill["refill"]):
        click(*point, settle)
    press_button("warp_yes", settle, verbose)
    REFILL["done"] = True
    calibration.snap("play_time_refilled")
    click(*refill["cancel"], settle)
    ctypes.windll.user32.SetCursorPos(*TAB_TWO)


def reset_play_limit(reset, verbose=True):
    settle = float(reset["settle"])
    heading = tuple(reset["heading_box"])
    cancel = tuple(reset["cancel_box"])
    enter_point = centre(ENTER_WINDOW)
    if verbose:
        print(f"  no entries left on the row, so the button at {enter_point} "
              f"for the reset")
    click(*enter_point, 0.0)
    up, seen = wait_for(heading, reset["heading_word"], float(reset["wait"]))
    if not up:
        raise RuntimeError(
            f"no {reset['heading_word']!r} popup in {list(heading)} within "
            f"{float(reset['wait']):g}s of the click; it reads {seen!r}. "
            f"Nothing more clicked.")
    answer_reset(reset, seen, verbose)


def reset_popup(reset):
    """After Enter: True once the reset popup is up, False once the
    Challenge window is (or after the reset's wait with neither)."""
    heading = tuple(reset["heading_box"])
    deadline = time.monotonic() + float(reset["wait"])
    while True:
        up, challenge = read_windows(calibration.grab(),
                                     [heading, CHALLENGE_WINDOW])
        if row_model._key(reset["heading_word"]) in row_model._key(up):
            return True
        if (row_model._key(CHALLENGE_WORD) in row_model._key(challenge)
                or time.monotonic() >= deadline):
            return False
        time.sleep(WAIT_SLICE)


def answer_reset(reset, seen, verbose=True):
    settle = float(reset["settle"])
    heading = tuple(reset["heading_box"])
    cancel = tuple(reset["cancel_box"])
    if not wait_for(cancel, reset["cancel_word"], 0)[0]:
        raise RuntimeError(
            f"the {reset['heading_word']!r} popup has no "
            f"{reset['cancel_word']!r} in {list(cancel)}. Nothing more "
            f"clicked.")
    used = read_windows(calibration.grab(), [tuple(reset["count_box"])])[0]
    found = RESETS_USED.search(used)
    if found is not None and int(found.group(1)) >= int(found.group(2)):
        click(*centre(cancel), settle)
        raise RuntimeError(
            f"the Daily Reset Limit reads {used!r}: no resets left today. "
            f"{reset['cancel_word']} clicked.")
    if verbose:
        print(f"  {seen!r}, Daily Reset Limit {used!r}: the reset button at "
              f"{reset['button']}")
    click(*reset["button"], 0.0)
    deadline = time.monotonic() + float(reset["wait"])
    while wait_for(heading, reset["heading_word"], 0)[0]:
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"the {reset['heading_word']!r} popup is still up "
                f"{float(reset['wait']):g}s after the reset button.")
        time.sleep(WAIT_SLICE)
    calibration.snap("play_limit_reset")
    time.sleep(settle)


def open_dungeon_key(dungeon_name, location, verbose=True, refill=False,
                     after_reset=False):
    place = location_of(dungeon_name, location)
    name, words = place["name"], place.get("list_words")
    right = place.get("list_right")
    windows = (LEVEL_WINDOWS if right is None
               else [(box[0], box[1], right, box[3]) for box in LEVEL_WINDOWS])
    entry = _state().get("entry")
    if not entry:
        raise RuntimeError(f"no entry is noted in {STATE.name}; get_entry "
                           f"notes one when it buys. Nothing clicked.")
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    yes = BUTTONS["warp_yes"]
    ctypes.windll.user32.SetCursorPos(*TAB_TWO)
    box, reads = find_in_list(name, 0, words, windows)
    if box is None:
        if not wait_for(tuple(yes["window"]), yes["word"], 0)[0]:
            prepare(verbose, entry["tab"])
            if verbose:
                print(f"  right-click the entry in tab {entry['tab']} slot "
                      f"{tuple(entry['slot'])} at {entry['point']}")
            right_click(*entry["point"], 0.0)
        press_button("warp_yes", 0.0, verbose)
        ctypes.windll.user32.SetCursorPos(*TAB_TWO)
        box, reads = find_in_list(name, TRANSFER_TIMEOUT, words, windows)
        if box is None:
            raise RuntimeError(
                f"no {name!r} in the dungeon list within "
                f"{TRANSFER_TIMEOUT:g}s of {yes['word']}, read with the cursor "
                f"on tab II {list(TAB_TWO)}; its rows read {reads}. Nothing "
                f"more clicked.")
    point = centre(box)
    if verbose:
        print(f"  with the cursor on tab II {list(TAB_TWO)} the dungeon list "
              f"reads {reads}")
        print(f"  {location} {name!r} at {point}")
    click(*point, 0.0)
    level = DUNGEONS[dungeon_name].get("difficulty")
    if level is not None:
        set_difficulty(int(level), verbose)
    reset = DUNGEONS[dungeon_name].get("reset")
    if reset is not None:
        row = reads[windows.index(box)]
        left = ENTRIES_LEFT.search(row)
        if left is None and verbose:
            print(f"  no entry count on the row {row!r}; entering as usual")
        elif left is not None and left.group(1) in ("0", "O", "o"):
            if after_reset:
                raise RuntimeError(
                    f"the row still reads {row!r} after the reset. Nothing "
                    f"entered.")
            reset_play_limit(reset, verbose)
            return open_dungeon_key(dungeon_name, location, verbose, refill,
                                    after_reset=True)
        elif verbose:
            print(f"  the row reads {row!r}: entries left")
    up, seen = wait_for_enter()
    if (not up and refill and not REFILL["done"]
            and row_model._key(CANNOT_ENTER_WORD) in row_model._key(seen)):
        refill_play_time(point[1], verbose)
        up, seen = wait_for_enter()
    if not up:
        raise RuntimeError(
            f"with {location} selected the {ENTER_WORD} window "
            f"{list(ENTER_WINDOW)} reads {seen!r}. Nothing entered.")
    enter_point = centre(ENTER_WINDOW)
    if verbose:
        print(f"  {ENTER_WORD} at {enter_point}")
    click(*enter_point, 0.0)
    if reset is not None and reset_popup(reset):
        if after_reset:
            raise RuntimeError(
                f"the {reset['heading_word']!r} popup came up again after "
                f"the reset. Nothing more clicked.")
        if verbose:
            print(f"  the {reset['heading_word']!r} popup came up although "
                  f"the row read {row!r}: no entries left after all")
        answer_reset(reset, reset["heading_word"], verbose)
        return open_dungeon_key(dungeon_name, location, verbose, refill,
                                after_reset=True)
    DEATH_WATCH.update(armed=False, entered=True)
    forget_entry()
    if verbose:
        print(f"  entered {location}; the entry note is cleared")


def snap_every(name, every, stop):
    started, shot = time.monotonic(), 0
    while True:
        calibration.snap(f"{name}_{shot * every:g}s")
        shot += 1
        if stop.wait(max(0.0, started + shot * every - time.monotonic())):
            return


def rides_bike(events):
    if BIKE is None:
        return False
    alt = False
    for event in events:
        if event["kind"] != "key":
            continue
        if event.get("name") == SLOT_MODIFIER:
            alt = event["action"] == "down"
        elif (alt and event["action"] == "down"
              and event.get("name") == BIKE["key"]):
            return True
    return False


def bike_slot(image=None):
    image = calibration.grab() if image is None else image
    return ImageStat.Stat(image.convert("HSV").crop(
        tuple(BIKE["slot"]))).mean[2]


def greyed(image=None):
    return bike_slot(image) < float(BIKE["grey_below"])


def on_bike_now(tab=True):
    if tab:
        click(*TAB_TWO, SELECT_SETTLE)
    if not greyed():
        return None
    time.sleep(float(BIKE["recheck"]))
    image = calibration.grab()
    value = bike_slot(image)
    if value >= float(BIKE["grey_below"]):
        return None
    calibration.snap("on_bike", image)
    return value


def lit_again(seconds):
    deadline = time.monotonic() + seconds
    while True:
        value = bike_slot()
        if value >= float(BIKE["grey_below"]):
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(WAIT_SLICE)


def off_bike(when, verbose=True, tab=True):
    if BIKE is None:
        return False
    key, settle = BIKE["key"], float(BIKE["settle"])
    greyed_at = on_bike_now(tab)
    if greyed_at is None:
        return False
    if verbose:
        print(f"  {when}: hotbar slot 1 is greyed out (reads "
              f"{greyed_at:.0f}, under {BIKE['grey_below']}), so on the "
              f"bike: {SLOT_MODIFIER}+{key} to get off, then up to "
              f"{BIKE['dismount_wait']}s for it to light up")
    with KEY_LOCK:
        hold_modifier(True)
        try:
            press(KEYS[key])
        finally:
            hold_modifier(False)
    time.sleep(settle)
    lit = lit_again(float(BIKE["dismount_wait"]))
    if lit is None:
        raise RuntimeError(f"still on the bike {when}, after "
                           f"{SLOT_MODIFIER}+{key}; stopped so no run is "
                           f"spent unable to attack.")
    if verbose:
        print(f"  off the bike now: hotbar slot 1 reads {lit:.0f}")
    return True


def bike_after_crash(dungeon_name, verbose=True):
    when = f"after the {dungeon_name} crash"
    try:
        if focus_game():
            off_bike(when, verbose)
        elif verbose:
            print(f"  the game is not in the foreground, so no bike check "
                  f"{when}")
    except Exception as exc:
        if verbose:
            print(f"  the bike check {when} failed ({type(exc).__name__}: "
                  f"{exc})")


def play(dungeon_name, sequence, verbose=True):
    import record_action
    path = SEQUENCES / dungeon_name / f"{sequence}.jsonl"
    if not path.exists():
        raise RuntimeError(f"no sequence {path}; nothing played.")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    head, events = json.loads(lines[0]), [json.loads(line)
                                          for line in lines[1:]]
    width, height = record_action.screen()
    off = [(event["x"], event["y"]) for event in events
           if "x" in event and event["action"] != "move"
           and not (0 <= event["x"] < width and 0 <= event["y"] < height)]
    if head.get("screen") != [width, height] or off:
        raise RuntimeError(
            f"{path} was recorded on a {head.get('screen')} screen and this "
            f"one is {[width, height]}"
            + (f"; {len(off)} of its clicks or wheel turns are off the main "
               f"screen, the first at {list(off[0])}" if off else "")
            + "; nothing played.")
    wait_after_main_bm_off(verbose)
    wait_after_main_bm_cast(verbose)
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    bike = rides_bike(events)
    begin = head.get("cursor") or next(
        ([event["x"], event["y"]] for event in events if "x" in event), None)
    if begin is not None:
        begin = [min(max(begin[0], 0), width - 1),
                 min(max(begin[1], 0), height - 1)]
        ctypes.windll.user32.SetCursorPos(*begin)
    heal_ride = DUNGEONS.get(dungeon_name, {}).get("heal_ride_every")
    extras = [f"heal every {heal_ride:g}s"] if heal_ride else []
    if verbose:
        print(f"  {dungeon_name}/{path.name}: {len(events)} events over "
              f"{events[-1]['t']:.1f}s, the cursor first put at {begin}"
              + (" (where the recording began)" if head.get("cursor")
                 else " (the recording's first mouse point)")
              + (f"; a frame every {WALK_FRAMES[sequence]:g}s while it plays"
                 if sequence in WALK_FRAMES else "")
              + (f"; only {' and '.join(extras)} go in between until it ends"
                 if extras else "; nothing else is pressed until it ends"))
    keys, buttons, played = {}, {}, 0
    holding = False
    shooting = threading.Event()
    if sequence in WALK_FRAMES:
        threading.Thread(target=snap_every,
                         args=(sequence, WALK_FRAMES[sequence], shooting),
                         daemon=True).start()
    record_action.winmm.timeBeginPeriod(record_action.TIMER_MS)
    with KEY_LOCK:
        PLAYING.set()
    zero = time.perf_counter()
    try:
        for event in events:
            record_action.wait_until(zero + event["t"], ENDED)
            if ENDED.is_set() or calibration.STOPPED.is_set():
                break
            if event["kind"] == "key":
                up = event["action"] == "up"
                if not up and event["vk"] in REPLAY_MODIFIERS and not holding:
                    KEY_LOCK.acquire()
                    holding = True
                record_action.send(record_action.key_input(event, up))
                if up:
                    keys.pop(event["vk"], None)
                else:
                    keys[event["vk"]] = event
                if holding and not REPLAY_MODIFIERS & set(keys):
                    KEY_LOCK.release()
                    holding = False
            else:
                ctypes.windll.user32.SetCursorPos(
                    min(max(event["x"], 0), width - 1),
                    min(max(event["y"], 0), height - 1))
                action = event["action"]
                if action in record_action.WHEEL_FLAGS:
                    record_action.send(_mouse_input(
                        record_action.WHEEL_FLAGS[action], event["delta"]))
                elif action != "move":
                    button = event["button"]
                    record_action.send(_mouse_input(
                        record_action.BUTTON_FLAGS[(action, button)],
                        record_action.X_DATA.get(button, 0)))
                    if action == "down":
                        buttons[button] = event
                    else:
                        buttons.pop(button, None)
            played += 1
    finally:
        shooting.set()
        for event in list(keys.values()):
            record_action.send(record_action.key_input(event, True))
        for button in list(buttons):
            record_action.send(_mouse_input(
                record_action.BUTTON_FLAGS[("up", button)],
                record_action.X_DATA.get(button, 0)))
        if holding:
            KEY_LOCK.release()
        PLAYING.clear()
        record_action.winmm.timeEndPeriod(record_action.TIMER_MS)
    calibration.snap(f"played_{sequence}")
    if verbose:
        print(f"  played {played} of {len(events)} events; nothing is left "
              f"held down")
    if (bike and played == len(events) and not ENDED.is_set()
            and not calibration.STOPPED.is_set()):
        off_bike(f"after {sequence}", verbose, False)


def replay_run(dungeon_name, file, verbose=True):
    recorder = str(calibration.HERE.parent / "recorder")
    if recorder not in sys.path:
        sys.path.append(recorder)
    import dungeon_replay
    dungeon_replay.run(sys.modules[__name__], dungeon_name, file, verbose)


def _mouse_input(flags, data=0):
    import record_action
    return record_action.INPUT(
        type=record_action.W["INPUT_MOUSE"],
        u=record_action.INPUTUNION(mi=record_action.MOUSEINPUT(
            0, 0, ctypes.c_ulong(data).value, flags, 0, 0)))


def hp_left(text):
    found = TARGET_HP.search(text)
    return (None if found is None
            else int(re.sub(r"\D", "", found.group().split("/")[0]) or 0))


def hp_most(text):
    found = TARGET_HP.search(text)
    return (None if found is None
            else int(re.sub(r"\D", "", found.group().split("/")[-1]) or 0))


def note_target(bar, fresh):
    left, most = hp_left(bar), hp_most(bar)
    last = None if fresh else LOCK["target_hp"]
    readable = left is not None and most is not None
    if fresh or (readable and last is not None
                 and (most != last[1] or left > last[0])):
        LOCK["targets"] += 1
    LOCK["target_hp"] = (left, most) if readable else last


def slash_misread(text):
    return SLASH_MISREAD.sub(
        lambda found: found.group(1) + ("0" if found.group(2) else "") + "/",
        text)


def read_inked(crops):
    inked = [ImageChops.invert(crop).getbbox() is not None for crop in crops]
    texts = iter(read_crops([crop for crop, ink in zip(crops, inked) if ink])
                 if any(inked) else [])
    return [slash_misread(next(texts)) if ink else "" for ink in inked]


def cycle_key(entry):
    return CYCLE_KEYS.get(BM_CYCLE["slot"], {}).get(entry, entry)


def current_bm():
    if BM_CYCLE["slot"] is not None:
        return BM_CYCLE["slot"]
    if MAIN_BM_STATE["on"]:
        return "main_bm"
    return "alternate_bm" if ALT_BM["on"] else None


def bm_keys(names):
    keys = BM_ATTACK.get(current_bm())
    if keys is None or not any(entry in ATTACK_KEYS for entry in names):
        return names
    first = next(at for at, entry in enumerate(names) if entry in ATTACK_KEYS)
    rest = [entry for entry in names if entry not in ATTACK_KEYS]
    return rest[:first] + keys + rest[first:]


def near(pixel, colour):
    return all(abs(have - want) <= slack
               for have, want, slack in zip(pixel, colour, TARGET_SLACK))


def bar_up(image, slot):
    _name, cap, _fill, (x, y) = slot
    return (near(image.getpixel(cap), TARGET_CAP)
            and all(near(image.getpixel((x, y + row)), colour)
                    for row, colour in enumerate(TARGET_FRAME)))


def target_slots(image):
    up = [index for index, slot in enumerate(TARGET_SLOTS)
          if bar_up(image, slot)]
    return up, [index for index in up
                if near(image.getpixel(TARGET_SLOTS[index][2]), TARGET_FILL)]


def target_bars(image, words):
    up, live = target_slots(image)
    if not words or not live:
        return live, []
    return live, read_inked([white_text(image, TARGET_WINDOWS[index])
                             for index in up])


def yellow_bars(live):
    names = [TARGET_SLOTS[index][0] for index in live]
    return (f"the {' and '.join(names)} HP bar"
            + (" is" if len(names) == 1 else "s are") + " yellow")


def watch_target(name, words, stop, first, failed):
    try:
        while not stop.is_set():
            image = calibration.grab()
            live, bars = target_bars(image, words)
            LOCK["live"] = len(live)
            LOCK["reads"] += 1
            first.set()
            if not words:
                if live and LOCK["boss"] is None:
                    LOCK["boss"] = name
                    LOCK["targets"] += 1
                    calibration.snap("target_found", image)
                    print(f"  {yellow_bars(live)}: {name} is targeted"
                          + (f"; {TWIN_BM} is on, so {TARGET_KEY} every "
                             f"{LOCK_ALL['twin']:g}s until {TWIN_TARGETS} "
                             f"HP bars are yellow"
                             if LOCK_ALL["on"] and need_target() else
                             ", so no more auto target"))
                elif not live and LOCK["boss"] is not None:
                    LOCK["killed"] = True
                    calibration.snap("target_gone", image)
                    print(f"  no HP bar is yellow: {name} is gone, so it is "
                          f"dead")
                    ENDED.set()
                    return
                continue
            seen = " | ".join(bars)
            named = [bar for bar in bars
                     if any(word.casefold() in bar.casefold()
                            for word in words)]
            dead = [bar for bar in named if hp_left(bar) == 0]
            hits = [word for bar in named if hp_left(bar) != 0
                    for word in words if word.casefold() in bar.casefold()]
            if hits:
                LOCK["hits"] = hits
                note_target(next(bar for bar in named if hp_left(bar) != 0),
                            LOCK["boss"] is None)
            if hits and LOCK["boss"] is None:
                LOCK["boss"] = name
                calibration.snap("target_found", image)
                print(f"  the HP bar reads {seen!r}: {name} is targeted "
                      f"({', '.join(hits)}), so no more auto target")
            elif (not hits and not dead and LOCK["boss"] is not None
                  and any(hp_left(bar) for bar in bars)):
                LOCK["boss"] = None
                calibration.snap("target_switched", image)
                print(f"  the HP bar reads {seen!r}: another monster is "
                      f"targeted, not {name}, so {TARGET_KEY} again until it "
                      f"shows {' or '.join(map(repr, words))}")
            elif (not hits and LOCK["boss"] is not None
                  and (dead or not any(bars))):
                LOCK["killed"] = True
                calibration.snap("target_gone", image)
                print(f"  the HP bar reads {seen!r}: {name} is gone, so it is "
                      f"dead")
                ENDED.set()
                return
    except Exception as exc:
        failed["error"] = exc
        first.set()
        ENDED.set()


def start_watch(name, words):
    LOCK.update(boss=None, killed=False, watching=True, reads=0, hits=[],
                named=bool(words), live=0)
    ENDED.clear()
    watch = {"name": name, "words": words, "stop": threading.Event(),
             "first": threading.Event(), "failed": {}}
    watch["thread"] = threading.Thread(
        target=watch_target,
        args=(name, words, watch["stop"], watch["first"], watch["failed"]),
        daemon=True)
    watch["thread"].start()
    return watch


def drop_early_watch():
    watch = TARGET_WATCH["watch"]
    TARGET_WATCH["watch"] = None
    if watch is None:
        return
    watch["stop"].set()
    watch["thread"].join()
    LOCK.update(boss=None, killed=False, watching=False)
    ENDED.clear()


def hunt(name, words, attack, label, plan, verbose=True, search=None):
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    limit = TIME_LIMIT.total_seconds()
    watch = TARGET_WATCH["watch"]
    early = watch is not None and (watch["name"], watch["words"]) == (name,
                                                                      words)
    if early:
        TARGET_WATCH["watch"] = None
    else:
        drop_early_watch()
        watch = start_watch(name, words)
    stop, first, failed = watch["stop"], watch["first"], watch["failed"]
    watcher = watch["thread"]
    if verbose:
        print(f"  {label} {name!r}: the HP bar is read all the time; "
              f"{TARGET_KEY}"
              + (f" (each followed by {' -> '.join(SEARCH_ATTACK['keys'])}, "
                 f"for a target the bar can't be read for)"
                 if SEARCH_ATTACK["keys"] else "")
              + " until it shows "
              f"{' or '.join(map(repr, words)) if words else 'any monster'}, "
              + (f"then {plan} over and over, " if plan else
                 "then nothing clicked, ")
              + (f"with {TARGET_KEY} every {LOCK_ALL['every']:g}s only while "
                 f"nothing is targeted, and in {TWIN_BM} every "
                 f"{LOCK_ALL['twin']:g}s while fewer than {TWIN_TARGETS} HP "
                 f"bars are yellow, none from the loop"
                 if LOCK_ALL["on"] else
                 f"with {TARGET_KEY} every {HUNT_TARGET_EVERY:g}s between "
                 f"attacks"
                 + (" (not while the named target is locked)" if words
                    else ""))
              + ", stopped the moment none of them shows"
              + f"; in a BM its attack keys go with the {TARGET_KEY} presses "
                f"and in the loop, taking turns, one every "
                f"{BM_ATTACK_GAP:g}s at most"
              + (f"; it has been read since the BM cast's first {TARGET_KEY}"
                 if early else ""))
    presses, rounds, started = 0, 0, time.monotonic()
    quiet = started
    try:
        HUNTING.set()
        first.wait(limit)
        since = None
        while not ENDED.is_set() and time.monotonic() - started < limit:
            if LOCK["boss"] is None:
                if (search is not None and since is None
                        and time.monotonic() - started >= search):
                    graced = time.monotonic() + SEARCH_GRACE
                    while (time.monotonic() < graced and LOCK["boss"] is None
                           and not ENDED.is_set()):
                        ENDED.wait(min(WAIT_SLICE, graced - time.monotonic()))
                    if LOCK["boss"] is None:
                        break
                    continue
                if (search is None and since is None and NO_TARGET_STOP
                        and time.monotonic() - quiet >= NO_TARGET_STOP):
                    if off_bike(f"{NO_TARGET_STOP:g}s with no target for "
                                f"{name!r}", verbose, False):
                        quiet = time.monotonic()
                        continue
                    if OVER.is_set() or ENDED.is_set():
                        break
                    raise RuntimeError(
                        f"{name!r}: nothing targeted in {NO_TARGET_STOP:g}s "
                        f"and not on the bike; the run is given up here, not "
                        f"at its time limit.")
                if not focus_game():
                    raise RuntimeError(f"the game is not in the foreground; "
                                       f"the {label} stopped.")
                casting = time.monotonic()
                aura_when_due(verbose)
                main_bm_when_due(verbose)
                main_bm_recast_when_due(verbose)
                bm_cycle_when_due(verbose)
                other_bm_when_on(verbose)
                aura_in_window(verbose)
                quiet += time.monotonic() - casting
                auto_target()
                for key in SEARCH_ATTACK["keys"]:
                    tap(KEYS[cycle_key(key)])
                presses += 1
                if LOCK_ALL["on"]:
                    HUNT_TARGET["last"] = time.monotonic()
                    until = HUNT_TARGET["last"] + target_every()
                    while (time.monotonic() < until and LOCK["boss"] is None
                           and not ENDED.is_set()):
                        other_bm_when_on(verbose)
                        ENDED.wait(WAIT_SLICE)
                    continue
                pressed_at = LOCK["reads"]
                while (LOCK["reads"] < pressed_at + 2 and LOCK["boss"] is None
                       and not ENDED.is_set()):
                    ENDED.wait(WAIT_SLICE)
                continue
            if since is None:
                since = time.monotonic()
                if verbose:
                    print(f"  {presses} {TARGET_KEY} press(es) before "
                          f"{name!r} was targeted")
            attack(since)
            rounds += 1
            calibration.snap(f"{label}_{rounds}")
        if since is None and verbose:
            print(f"  {name!r} was targeted and died during the BM cast"
                  if early and LOCK["killed"] else
                  f"  {presses} {TARGET_KEY} press(es); {name!r} was never "
                  f"targeted")
    finally:
        HUNTING.clear()
        stop.set()
        watcher.join()
        killed = LOCK["killed"]
        LOCK.update(boss=None, killed=False, watching=False)
        ENDED.clear()
    took = time.monotonic() - started
    if failed:
        raise RuntimeError(f"the HP bar could not be read: "
                           f"{failed['error']}. Stopped.")
    if OVER.is_set():
        if verbose:
            print(f"  {label} {name!r}: the dungeon is over, after {rounds} "
                  f"round(s) in {took:.1f}s")
        return True
    if not killed and search is not None and since is None:
        if verbose:
            print(f"  {label} {name!r}: none targeted in {search:g}s, nor in "
                  f"the {SEARCH_GRACE:g}s after the last {TARGET_KEY}")
        return False
    if not killed:
        raise RuntimeError(f"{name!r} was not killed in {rounds} round(s) "
                           f"over {took:.0f}s. Stopped.")
    KILL_DONE["at"] = time.monotonic()
    if verbose:
        print(f"  {label} {name!r}: dead after {rounds} round(s) in "
              f"{took:.1f}s")
    return True


def kill(name, words, verbose=True):
    return hunt(name, words, lambda since: move(KILL_COMBO, False, False),
                "kill",
                ", ".join(f"{key} + {wait:g}s"
                          for _a, key, wait in KILL_COMBO),
                verbose)


def bm_aura(verbose=True, names=None, seconds=None, after=None, spam=None,
            main=None, target=None):
    aura = names is None
    names = BM_AURA if names is None else names
    seconds = BM_AURA_FOR if seconds is None else seconds
    after = AURA_AFTER if after is None else after
    spam = AURA_FOR if spam is None else spam
    main = MAIN_BM_AFTER if main is None else main
    target = AURA_TARGET if target is None else target
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    if verbose:
        print(f"  {' -> '.join(names)} right-clicked {LOOP_GAP:g}s apart, "
              f"over and over for {seconds:g}s from the first click"
              + ((f"; aura {after:g}s after the first"
                  + (f", right-clicked over and over for {spam:g}s"
                     + ("" if target else f" with no {TARGET_KEY}")
                     if spam else "") if after else "; no aura")
                 + (f"; main BM {main:g}s after the first, right-clicked over "
                    f"and over for {seconds:g}s" if main else "")
                 if aura else ""))
    started = time.monotonic()
    if aura:
        AURA_DUE.update(at=started + after if after else None, after=after,
                        since=started, target=target, **{"for": spam})
        MAIN_BM_DUE.update(at=started + main if main else None, after=main,
                           **{"for": seconds})
    rounds = 0
    while time.monotonic() < started + seconds and not ENDED.is_set():
        click_round(names, started)
        rounds += 1
    if verbose:
        print(f"  {rounds} round(s) of {' -> '.join(names)} in "
              f"{time.monotonic() - started:.1f}s")


def aura_when_due(verbose=True):
    due = AURA_DUE["at"]
    if due is None or OVER.is_set() or time.monotonic() < due:
        return
    AURA_DUE["at"] = None
    started, clicks = time.monotonic(), 0
    while not OVER.is_set():
        slot = time.monotonic()
        use_point("aura", 0.0)
        if AURA_DUE["target"] and not named_locked():
            auto_target()
        clicks += 1
        if time.monotonic() >= started + AURA_DUE["for"]:
            break
        OVER.wait(max(0.0, slot + AURA_GAP - time.monotonic()))
    if verbose:
        print(f"  {started - AURA_DUE['since']:.0f}s since BM: aura "
              f"{slot_way('aura')} {clicks} time(s) in "
              f"{time.monotonic() - started:.1f}s"
              + (f", {TARGET_KEY} after each" if AURA_DUE["target"] else ""))


def recording_length(dungeon_name, sequence):
    path = SEQUENCES / dungeon_name / f"{sequence}.jsonl"
    if not path.exists():
        return 0.0
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    if len(lines) < 2:
        return 0.0
    return float(json.loads(lines[-1]).get("t", 0.0))


def aura_before(dungeon_name, sequence, verbose=True):
    due = AURA_DUE["at"]
    if due is None or OVER.is_set():
        return
    length = recording_length(dungeon_name, sequence)
    left = due - time.monotonic()
    if length < left:
        return
    if verbose:
        print(f"  {sequence} runs {length:.1f}s and the aura is due in "
              f"{max(0.0, left):.1f}s, so the aura goes before it")
    AURA_DUE["at"] = time.monotonic()
    aura_when_due(verbose)


def aura_now(verbose=True):
    if AURA_DUE["for"] is None or OVER.is_set():
        if verbose:
            print("  no BM aura has been set up in this run, so no aura")
        return
    now = time.monotonic()
    AURA_DUE["at"] = None
    AURA_WINDOW.update(until=now + AURA_DUE["for"], since=now, last=None,
                       clicks=0)
    if verbose:
        print(f"  {now - AURA_DUE['since']:.0f}s since BM: the aura goes "
              f"now, {slot_way('aura')} at most every "
              f"{AURA_GAP:g}s for {AURA_DUE['for']:g}s, between the "
              f"{TARGET_KEY} presses and the attacks of the next kill")


def aura_in_window(verbose=True):
    if AURA_WINDOW["until"] is None:
        return
    now = time.monotonic()
    if now >= AURA_WINDOW["until"] or OVER.is_set():
        end_aura_window(verbose)
        return
    if (AURA_WINDOW["last"] is not None
            and now - AURA_WINDOW["last"] < AURA_GAP):
        return
    use_point("aura", 0.0)
    AURA_WINDOW.update(last=now, clicks=AURA_WINDOW["clicks"] + 1)


def end_aura_window(verbose=True):
    if AURA_WINDOW["until"] is None:
        return
    ended = min(time.monotonic(), AURA_WINDOW["until"])
    if verbose:
        print(f"  aura {slot_way('aura')} {AURA_WINDOW['clicks']} time(s) in "
              f"{ended - AURA_WINDOW['since']:.1f}s alongside the kill")
    AURA_WINDOW["until"] = None


def bm_aura_step(dungeon_name, verbose=True):
    MAIN_RECAST.update(at=time.monotonic(), dungeon=dungeon_name)
    bm_aura(verbose, seconds=float(setting(dungeon_name, "bm_aura_for")),
            after=float(setting(dungeon_name, "aura_after")),
            spam=float(setting(dungeon_name, "aura_for")),
            main=float(setting(dungeon_name, "main_bm_after")),
            target=bool(setting(dungeon_name, "aura_target")))
    if DUNGEONS[dungeon_name].get("main_bm_recast") and not OVER.is_set():
        MAIN_BM_STATE["on"] = True


def main_bm_ran_out(over, verbose=True):
    if not MAIN_BM_STATE["on"] or time.monotonic() < over:
        return
    MAIN_BM_STATE["on"] = False
    if verbose:
        print("  the main BM has run out: the kills use the no-BM loop until "
              "it is cast again")


def main_bm_recast_before(dungeon_name, sequence, verbose=True):
    recast = DUNGEONS[dungeon_name].get("main_bm_recast")
    if not recast or MAIN_RECAST["at"] is None or OVER.is_set():
        return
    over = MAIN_RECAST["at"] + float(recast["over_after"])
    now = time.monotonic()
    if now < over:
        return
    main_bm_ran_out(over, verbose)
    left = max(0.0, over + float(recast["cooldown_wait"]) - now)
    if verbose:
        print(f"  the main BM ran out {now - over:.0f}s ago "
              f"({float(recast['over_after']):g}s after its cast), so "
              f"{left:.0f}s more of its cooldown, then the main BM and its "
              f"aura again before {sequence}")
    if left:
        wait_timer(left, verbose)
    bm_aura_step(dungeon_name, verbose)


def main_bm_recast_when_due(verbose=True):
    dungeon_name = MAIN_RECAST["dungeon"]
    recast = (DUNGEONS[dungeon_name].get("main_bm_recast")
              if dungeon_name is not None else None)
    if not recast or MAIN_RECAST["at"] is None or OVER.is_set():
        return
    over = MAIN_RECAST["at"] + float(recast["over_after"])
    main_bm_ran_out(over, verbose)
    if time.monotonic() < over + float(recast["cooldown_wait"]):
        return
    if verbose:
        print(f"  the main BM ran out {time.monotonic() - over:.0f}s ago and "
              f"its {float(recast['cooldown_wait']):g}s cooldown is over, so "
              f"the main BM and its aura again now, in the middle of the kill")
    bm_aura_step(dungeon_name, verbose)


def main_bm_when_due(verbose=True):
    due = MAIN_BM_DUE["at"]
    if due is None or OVER.is_set() or time.monotonic() < due:
        return
    main_bm_now(MAIN_BM_DUE["for"],
                f"{MAIN_BM_DUE['after']:g}s since the first BM: ", verbose)


def main_bm_now(seconds, reason="", verbose=True):
    MAIN_BM_DUE["at"] = None
    started, clicks = time.monotonic(), 0
    while not OVER.is_set():
        if not clicks:
            main_bm_on(verbose)
        use_point("main_bm", LOOP_GAP)
        clicks += 1
        if time.monotonic() >= started + seconds:
            break
    if verbose:
        print(f"  {reason}main BM {slot_way('main_bm')} {clicks} time(s) in "
              f"{time.monotonic() - started:.1f}s")


def cycle_cast(slot, verbose=True, then=None):
    cast = float(BM_CYCLE_SET["cast"][slot])
    AURA_DUE["at"] = None
    MAIN_BM_DUE["at"] = None
    BM_CYCLE["slot"] = slot
    started, presses = time.monotonic(), 0
    MAIN_BM_STATE["on"] = False
    if slot == "main_bm":
        main_bm_on(verbose)
    targeted = 0
    while not OVER.is_set() and time.monotonic() < started + cast:
        use_point(slot, 0.0)
        if not named_locked():
            auto_target()
            targeted += 1
            if then is not None and TARGET_WATCH["watch"] is None:
                TARGET_WATCH["watch"] = start_watch(*then)
        presses += 1
        OVER.wait(LOOP_GAP)
    BM_CYCLE.update(slot=slot, extended=False,
                    ends=started + cast + float(BM_CYCLE_SET["lasts"]))
    if verbose:
        print(f"  BM cycle: {slot} {slot_way(slot)} {presses} time(s) over its "
              f"{cast:g}s cast, {TARGET_KEY} after {targeted} of them; it "
              f"should run out "
              f"{BM_CYCLE_SET['lasts']:g}s after the cast"
              + "".join(f"; {key} is pressed as {swap}"
                        for key, swap in CYCLE_KEYS.get(slot, {}).items())
              + (f"; the HP bar is read for {then[0]!r} from the first "
                 f"{TARGET_KEY} on" if then is not None else ""))


def cycle_aura(verbose=True, target=False):
    started = time.monotonic()
    left = max(0.0, BM_CYCLE["ends"] - started)
    presses, targeted = 0, 0
    while (not OVER.is_set()
           and time.monotonic() < started + float(BM_CYCLE_SET["aura_for"])):
        use_point("aura", 0.0)
        if targets_often() or ((target or BM_CYCLE["aura_target"])
                               and not named_locked()):
            auto_target()
            targeted += 1
        presses += 1
        OVER.wait(AURA_GAP)
    keeps = (float(BM_CYCLE_SET["aura_base"])
             + float(BM_CYCLE_SET["aura_share"]) * left)
    BM_CYCLE.update(ends=started + keeps, extended=True)
    if verbose:
        print(f"  BM cycle: aura {slot_way('aura')} {presses} time(s) with "
              f"{left:.1f}s of {BM_CYCLE['slot']} left, {TARGET_KEY} after "
              f"{targeted} of them; it should now run out in {keeps:.1f}s")


def cancel_spam(verbose=True):
    span = float(BM_CYCLE_SET["cancel_for"])
    started, clicks = time.monotonic(), 0
    while not OVER.is_set() and time.monotonic() < started + span:
        right_click(*BM_ICON, float(BM_CYCLE_SET["cancel_gap"]))
        clicks += 1
    note_main_bm_off()
    MAIN_BM_STATE["on"] = False
    ALT_BM["on"] = False
    calibration.snap("cancel_spam")
    if verbose:
        print(f"  BM icon right-clicked {clicks} times at {list(BM_ICON)} in "
              f"{time.monotonic() - started:.1f}s")


def bm_cycle_when_due(verbose=True):
    if (BM_CYCLE["slot"] is None or BM_CYCLE["other"] is not None
            or OVER.is_set()):
        return
    now = time.monotonic()
    if now >= BM_CYCLE["ends"]:
        slots = BM_CYCLE_SET["slots"]
        following = slots[(slots.index(BM_CYCLE["slot"]) + 1) % len(slots)]
        if verbose:
            print(f"  BM cycle: {BM_CYCLE['slot']} is due to run out"
                  + ("" if BM_CYCLE["extended"] else " without its aura")
                  + f", so it is cancelled, then {following}")
        cancel_spam(verbose)
        BM_CYCLE["off"][BM_CYCLE["slot"]] = time.monotonic()
        cycle_cast(following, verbose)
        if BM_CYCLE["keep_aura"]:
            cycle_aura(verbose)
        return
    if (not BM_CYCLE["extended"]
            and now >= BM_CYCLE["ends"] - float(BM_CYCLE_SET["aura_left"])):
        cycle_aura(verbose)


def press_other_bm(verbose=True, with_aura=False):
    last, following = next_slot()
    BM_CYCLE.update(other=following, pressed=None, with_aura=with_aura,
                    aura_at=None)
    if verbose:
        print(f"  BM cycle: {following} {slot_way(following)} every "
              f"{OTHER_BM_EVERY:g}s"
              + (f", each press followed {OTHER_AURA_AFTER:g}s later by the "
                 f"aura {slot_way('aura')}, which never holds up a BM press"
                 if with_aura else "")
              + f" from here to the end of the run, with the "
              f"loop's keys and buffs, so it goes on the moment "
              f"{BM_CYCLE['slot'] or 'no BM'} runs out; no cancel and no "
              f"cast for {last or 'it'}")
    other_bm_when_on(verbose)


def other_bm_when_on(verbose=True):
    other = BM_CYCLE["other"]
    if other is None or OVER.is_set():
        return
    now = time.monotonic()
    if BM_CYCLE["slot"] != other and (BM_CYCLE["ends"] is None
                                      or now >= BM_CYCLE["ends"]):
        ran_out = BM_CYCLE["slot"]
        if ran_out is not None:
            BM_CYCLE["off"][ran_out] = now
        MAIN_BM_STATE["on"] = False
        ALT_BM["on"] = False
        if other == "main_bm":
            main_bm_on(verbose)
        BM_CYCLE.update(slot=other, extended=False,
                        ends=now + float(BM_CYCLE_SET["lasts"]))
        if verbose:
            print(f"  BM cycle: {ran_out or 'no BM'} is due to run out, so "
                  f"{other}, pressed all along, is the BM from here")
    if (BM_CYCLE["pressed"] is None
            or now >= BM_CYCLE["pressed"] + OTHER_BM_EVERY):
        use_point(other, 0.0)
        BM_CYCLE.update(pressed=now, aura_at=(now + OTHER_AURA_AFTER
                                              if BM_CYCLE["with_aura"]
                                              else None))
        return
    if BM_CYCLE["aura_at"] is not None and now >= BM_CYCLE["aura_at"]:
        use_point("aura", 0.0)
        BM_CYCLE["aura_at"] = None


def next_slot():
    slots = BM_CYCLE_SET["slots"]
    last = BM_CYCLE["slot"] or BM_CYCLE["last"]
    return last, (slots[(slots.index(last) + 1) % len(slots)]
                  if last in slots else slots[0])


def swap_bm_aura(dungeon_name, pause, verbose=True):
    last, following = next_slot()
    went_off = BM_CYCLE["off"].get(following)
    since = None if went_off is None else time.monotonic() - went_off
    if (BM_CYCLE["slot"] is not None and since is not None
            and since < float(BM_CYCLE_SET["cooldown"])):
        if verbose:
            print(f"  BM cycle: {following} went off {since:.0f}s ago, inside "
                  f"its {BM_CYCLE_SET['cooldown']:g}s cooldown, so {last} "
                  f"stays on"
                  + (" and already has its aura" if BM_CYCLE["extended"]
                     else " and gets its aura now"))
        if not BM_CYCLE["extended"]:
            cycle_aura(verbose)
        return
    if verbose:
        print(f"  BM cycle: {last or 'no BM'} is cancelled, {pause:g}s, then "
              f"{following} and its aura")
    cancel_bm(dungeon_name, verbose)
    wait_timer(pause, verbose)
    cycle_cast(following, verbose)
    cycle_aura(verbose)


def bm_cycle_before(dungeon_name, sequence, verbose=True):
    bm_cycle_when_due(verbose)
    if BM_CYCLE["slot"] is None or OVER.is_set():
        return
    length = recording_length(dungeon_name, sequence)
    if BM_CYCLE["extended"]:
        if time.monotonic() + length < BM_CYCLE["ends"]:
            return
        if verbose:
            print(f"  {sequence} runs {length:.1f}s and {BM_CYCLE['slot']} "
                  f"runs out in "
                  f"{max(0.0, BM_CYCLE['ends'] - time.monotonic()):.1f}s, "
                  f"during it, so it is cancelled now and the cycle starts "
                  f"again after the ride")
        cancel_bm(dungeon_name, verbose)
        BM_CYCLE["resume"] = True
        return
    due = BM_CYCLE["ends"] - float(BM_CYCLE_SET["aura_left"])
    if time.monotonic() + length < due:
        return
    if verbose:
        print(f"  {sequence} runs {length:.1f}s and the aura for "
              f"{BM_CYCLE['slot']} is due in "
              f"{max(0.0, due - time.monotonic()):.1f}s, so the aura goes "
              f"before it")
    cycle_aura(verbose)


def bm_cycle_after(verbose=True):
    if not BM_CYCLE["resume"] or OVER.is_set():
        return
    BM_CYCLE["resume"] = False
    last, following = next_slot()
    if verbose:
        print(f"  BM cycle: {last} was cancelled before the ride, so "
              f"{following} now")
    cycle_cast(following, verbose)
    if BM_CYCLE["keep_aura"]:
        cycle_aura(verbose)


def main_bm_on(verbose=True):
    if not MAIN_BM_STATE["on"]:
        MAIN_BM_CAST["at"] = time.monotonic()
    MAIN_BM_STATE["on"] = True
    ALT_BM["on"] = False


def spam_buff(point):
    for press in range(BUFF_PRESSES):
        calibration._button(_INPUT["MOUSEEVENTF_RIGHTDOWN"],
                            _INPUT["MOUSEEVENTF_RIGHTUP"], *point,
                            BUFF_PRESS_GAP if press + 1 < BUFF_PRESSES
                            else 0.0)
    calibration.snap(f"rightclick_{point[0]}_{point[1]}")


def named_locked():
    return ((HUNTING.is_set() or TARGET_WATCH["watch"] is not None)
            and LOCK["boss"] is not None and bool(LOCK["named"]))


def targets_often():
    return HUNTING.is_set() and not named_locked()


def need_target():
    return LOCK["boss"] is None or (current_bm() == TWIN_BM
                                   and LOCK["live"] < TWIN_TARGETS)


def target_every():
    return LOCK_ALL["twin"] if current_bm() == TWIN_BM else LOCK_ALL["every"]


def attack_keys():
    return [cycle_key(key) for key in BM_ATTACK.get(current_bm()) or []]


def attack_key(now):
    keys = attack_keys()
    if not keys or now < ATTACK_PRESS["last"] + BM_ATTACK_GAP:
        return
    key = keys[(keys.index(ATTACK_PRESS["key"]) + 1) % len(keys)
               if ATTACK_PRESS["key"] in keys else 0]
    tap(KEYS[key])
    ATTACK_PRESS.update(last=now, key=key)


def auto_target():
    tap(KEYS[TARGET_KEY])
    attack_key(time.monotonic())


def hunt_target():
    now = time.monotonic()
    if LOCK_ALL["on"]:
        if not HUNTING.is_set():
            return
        if need_target() and now - HUNT_TARGET["last"] >= target_every():
            auto_target()
            HUNT_TARGET["last"] = now
        else:
            attack_key(now)
        return
    if targets_often() and now - HUNT_TARGET["last"] >= HUNT_TARGET_EVERY:
        auto_target()
        HUNT_TARGET["last"] = now


def gap_until(until):
    while not ENDED.is_set() and time.monotonic() < until:
        hunt_target()
        other_bm_when_on()
        ENDED.wait(min(WAIT_SLICE, max(0.0, until - time.monotonic())))


def click_round(names, since):
    for entry in names or [None]:
        if ENDED.is_set():
            return
        if not focus_game():
            raise RuntimeError("the game is not in the foreground; the "
                               "right-click loop stopped.")
        aura_when_due()
        main_bm_when_due()
        main_bm_recast_when_due()
        bm_cycle_when_due()
        other_bm_when_on()
        aura_in_window()
        if entry is None:
            gap_until(time.monotonic() + LOOP_GAP)
            continue
        if entry == "aura" and AURA_DUE["at"] is not None:
            continue
        started = time.monotonic()
        if entry == DEFENSIVE:
            entry, point = DEFENSIVE_BUFFS[
                int((started - since) // DEFENSIVE_SWITCH)
                % len(DEFENSIVE_BUFFS)]
            spam_buff(point)
        elif entry in OFFENSIVE_BUFFS and entry not in KEYED_SLOTS:
            spam_buff(OFFENSIVE_BUFFS[entry])
        elif entry in CLICK_POINTS:
            use_point(entry, 0.0)
            if entry == "main_bm":
                main_bm_on()
            elif entry == "alternate_bm":
                ALT_BM["on"] = True
        elif entry in attack_keys():
            gap_until(ATTACK_PRESS["last"] + BM_ATTACK_GAP)
            if not ENDED.is_set():
                attack_key(time.monotonic())
        elif entry != TARGET_KEY or not LOCK_ALL["on"]:
            tap(KEYS[entry])
        gap_until(started + LOOP_GAP)


def loop_plan(names):
    defensive = "/".join(name for name, _point in DEFENSIVE_BUFFS)
    keys = [entry for entry in names
            if entry != DEFENSIVE and entry not in CLICK_POINTS]
    buffs = any(entry == DEFENSIVE or entry in OFFENSIVE_BUFFS
                for entry in names)
    return (" -> ".join(f"{defensive} (switching every {DEFENSIVE_SWITCH:g}s)"
                        if entry == DEFENSIVE else
                        f"{entry} (once per target)"
                        if entry in ONCE_PER_TARGET else entry
                        for entry in names)
            + (f" pressed {LOOP_GAP:g}s apart" if len(keys) == len(names)
               else f" one every {LOOP_GAP:g}s, keys {'/'.join(keys)} pressed "
               f"and the rest used from the hotbar" if keys else
               f" used from the hotbar {LOOP_GAP:g}s apart")
            + (f"; each buff right-clicked {BUFF_PRESSES} times "
               f"{BUFF_PRESS_GAP:g}s apart" if buffs else ""))


def kill_bm(name, words, verbose=True, loop=None, search=None,
            main_loop=None):
    loop = BM_LOOP if loop is None else loop
    LAST_KILL.update(name=name, words=words, loop=loop)
    start = next((at for at, entry in enumerate(loop)
                  if entry not in BM_AURA), 0) if loop is BM_LOOP else 0
    rounds = {"next": loop[start:], "target": None}

    def attack(since):
        if not loop:
            gap_until(time.monotonic() + LOOP_GAP)
            return
        names = bm_keys(main_loop if main_loop and MAIN_BM_STATE["on"]
                        else rounds["next"])
        if rounds["target"] == LOCK["targets"]:
            names = [entry for entry in names
                     if entry not in ONCE_PER_TARGET]
        rounds["target"] = LOCK["targets"]
        click_round([cycle_key(entry) for entry in names], since)
        rounds["next"] = loop

    return hunt(name, words, attack, "kill_bm",
                f"{loop_plan(loop)}, the first round from {loop[start]}"
                + (f"; while the main BM is on, {loop_plan(main_loop)}"
                   if main_loop else "")
                + (f"; {'/'.join(ATTACK_KEYS)} become "
                   + ", ".join(f"{' -> '.join(keys)} in the {slot}"
                               for slot, keys in BM_ATTACK.items())
                   if any(entry in ATTACK_KEYS
                          for entry in loop + (main_loop or [])) else "")
                + (f"; aura only from {AURA_DUE['after']:g}s after BM"
                   if AURA_DUE["at"] is not None else "")
                if loop else None, verbose, search)


def main_loop_for(dungeon_name, loop_name):
    swap = DUNGEONS[dungeon_name].get("main_bm_loops", {}).get(loop_name)
    return list(_DUNGEON[swap]) if swap else None


def boss_again(verbose=True):
    name = LAST_KILL["name"]
    if name is None or OVER.is_set():
        return False
    image = calibration.grab()
    seen = read_crops([white_text(image, TARGET_WINDOW)])[0]
    hits = [word for word in LAST_KILL["words"]
            if word.casefold() in seen.casefold()]
    if not hits:
        return False
    calibration.snap("target_again", image)
    if verbose:
        print(f"  the HP bar reads {seen!r}: {name} is targeted again "
              f"({', '.join(hits)}), so kill_bm again")
    kill_bm(name, LAST_KILL["words"], verbose, LAST_KILL["loop"])
    return True


def heal_loop(stop, kills_only, ride_every=None):
    last = None
    while (not stop.is_set() and not OVER.is_set()
           and not calibration.STOPPED.is_set()):
        now = time.monotonic()
        if PLAYING.is_set():
            if ride_every is not None and (last is None
                                           or now - last >= ride_every):
                with KEY_LOCK:
                    if PLAYING.is_set():
                        press(KEYS[HEAL_KEY])
                        last = now
        elif not kills_only or HUNTING.is_set():
            with KEY_LOCK:
                if not PLAYING.is_set():
                    press(KEYS[HEAL_KEY])
                    last = now
        stop.wait(HEAL["every"])


def run_result(dungeon_name, location, result):
    try:
        label = run_label(dungeon_name, location)
    except Exception:
        label = f"{dungeon_name} {location}"
    print(f"{label}: {result}")
    started = RUN_CLOCK["started"]
    ended = (END["at"] if result == "pass" and END["at"] is not None
             else time.monotonic())
    if LIVE["view"] is not None:
        LIVE["view"].result(result, ended)
    if started is None:
        return
    rooms = RUN_CLOCK["rooms"]
    ends = [at for _room, at in rooms[1:]] + [ended]
    print(f"  {label}: {ended - started:.1f}s from {CHALLENGE_WORD} to the "
          f"{result.split(',')[0]}; "
          + ", ".join(f"{room} {end - at:.1f}s"
                      for (room, at), end in zip(rooms, ends)))
    RUN_CLOCK.update(started=None, rooms=[])


def run_count(dungeon_name, location):
    found = LEVEL_TEXT.search(location_of(dungeon_name, location)["name"])
    if found is None:
        return (dungeon_name, runs_today(dungeon_name),
                DUNGEONS[dungeon_name].get("daily_limit"))
    return (location, ran_today(_state(), None).get(int(found.group(1)), 0),
            None)


def run_label(dungeon_name, location):
    name, count, _limit = run_count(dungeon_name, location)
    return f"{name} #{count}"


def view_rooms(dungeon_name):
    start = DUNGEONS[dungeon_name].get("start_room")
    return ([start] if start else []) + [
        step[len(ROOM_PREFIX):] for step in DUNGEONS[dungeon_name]["sequence"]
        if isinstance(step, str) and step.startswith(ROOM_PREFIX)]


def unique_items(texts):
    found, skip = [], False
    for index, line in enumerate(texts):
        if skip:
            skip = False
            continue
        match = UNIQUE_TEXT.search(line)
        if match is None and index + 1 < len(texts):
            joined = f"{line} {texts[index + 1]}"
            spanning = UNIQUE_TEXT.search(joined)
            if spanning and spanning.start() < len(line) < spanning.end():
                match, line, skip = spanning, joined, True
        if match:
            found.append((index, line, match.group(1).strip()))
    return found


def same_item(old, item):
    return difflib.SequenceMatcher(None, row_model._key(old),
                                   row_model._key(item)).ratio()


def read_chat(verbose=True, image=None):
    if image is None:
        with KEY_LOCK:
            if PLAYING.is_set():
                return
            image = calibration.grab()
    with CHAT_WORK:
        read_chat_lines(image, verbose)


def start_chat_reader():
    proc = subprocess.Popen(
        [str(row_model.BACKUP_PYTHON), str(row_model.BACKUP_READER),
         row_model.BACKUP_MODEL],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        creationflags=(subprocess.CREATE_NO_WINDOW
                       | subprocess.BELOW_NORMAL_PRIORITY_CLASS))
    lines = queue.Queue()

    def pump():
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    try:
        ready = lines.get(timeout=row_model.BACKUP_START_TIMEOUT)
    except queue.Empty:
        ready = None
    if not ready:
        proc.kill()
        raise RuntimeError(f"it did not start within "
                           f"{row_model.BACKUP_START_TIMEOUT}s")
    CHAT_READER.update(proc=proc, lines=lines)


def stop_chat_reader(why, run):
    proc = CHAT_READER["proc"]
    CHAT_READER.update(proc=None, lines=None, down=why, down_run=run)
    if proc is not None:
        try:
            proc.kill()
        except OSError:
            pass


def chat_reader_again(verbose=True):
    CHAT_READER["run"] += 1
    why = CHAT_READER["down"]
    if why is None:
        return
    CHAT_READER["down"] = None
    if verbose:
        print(f"  the chat reader stopped in an earlier run ({why}); a new "
              f"one starts with this run's first chat read")


def chat_texts(prepared, verbose=True):
    with CHAT_READER_LOCK:
        run = CHAT_READER["run"]
        if CHAT_READER["down"] is not None and CHAT_READER["down_run"] == run:
            return None
        try:
            proc = CHAT_READER["proc"]
            if proc is None or proc.poll() is not None:
                start_chat_reader()
                proc = CHAT_READER["proc"]
            crops = []
            for picture in (p for p in prepared if p is not None):
                buf = io.BytesIO()
                picture.convert("RGB").save(buf, "PNG")
                crops.append(base64.b64encode(buf.getvalue()).decode("ascii"))
            proc.stdin.write(json.dumps(crops) + "\n")
            proc.stdin.flush()
            answer = CHAT_READER["lines"].get(timeout=row_model.BACKUP_TIMEOUT)
            if not answer:
                raise RuntimeError("its process ended")
            read = iter(text for text, _score in json.loads(answer))
            return [(next(read) if p is not None else "").strip()
                    for p in prepared]
        except Exception as exc:
            why = (f"no answer within {row_model.BACKUP_TIMEOUT}s"
                   if isinstance(exc, queue.Empty)
                   else f"{type(exc).__name__}: {exc}")
            stop_chat_reader(why, run)
            if verbose:
                print(f"  the chat reader stopped ({why}); no unique items "
                      f"are read for the rest of this run, the run goes on, "
                      f"and a new reader starts with the next run")
            return None


def read_chat_lines(image, verbose=True):
    texts = chat_texts([row_model._backup_line(image, box, colour=True)
                        for box in CHAT_BOXES], verbose)
    if texts is None:
        return
    found = unique_items(texts)
    new = []
    if CHAT["seen"] is not None:
        left = list(CHAT["seen"])
        for row, line, item in found:
            above = [old for old in left if old[0] >= row
                     and same_item(old[1], item) >= CHAT_SAME]
            if above:
                left.remove(max(above, key=lambda old: same_item(old[1],
                                                                 item)))
            else:
                new.append((line, item))
    owner = CHAT["last"] if CHAT["fresh"] and CHAT["last"] else CHAT["run"]
    CHAT["seen"] = [(row, item) for row, _line, item in found]
    CHAT["fresh"] = False
    for line, item in new:
        if verbose:
            print(f"  chat {line!r}: unique item dropped in "
                  f"{owner['label']}: {item!r}")
        if LIVE["view"] is not None:
            LIVE["view"].unique(item)
        try:
            note_unique(owner, item, image)
        except BaseException as exc:
            if verbose:
                print(f"  {item!r} was not written to {UNIQUE_FILE.name} "
                      f"({type(exc).__name__}: {exc})")


def note_unique(owner, item, image):
    try:
        calibration.snap("unique_drop", image)
    except Exception:
        pass
    with UNIQUE_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"day": owner["day"], "item": item,
                                 "dungeon": owner["name"],
                                 "run": owner["label"],
                                 "time": report_at()}) + "\n")


def unique_days(day):
    last = datetime.date.fromisoformat(day)
    return [(last - datetime.timedelta(days=back)).isoformat()
            for back in range(UNIQUE_DAYS - 1, -1, -1)]


def unique_drops(day):
    found = []
    try:
        days = set(unique_days(day))
        for line in UNIQUE_FILE.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
                if (record["day"] in days
                        and not any(item in record["item"].casefold()
                                    for item in UNIQUE_IGNORE)):
                    found.append((record["day"], drop_dungeon(record),
                                  drop_item(record["item"])))
            except Exception:
                continue
    except Exception:
        pass
    return found


def drop_dungeon(record):
    return (str(record.get("run") or "").split(" #")[0].upper()
            or record["dungeon"])


def drop_item(item):
    return item + ")" * max(0, item.count("(") - item.count(")"))


def unique_lines(day):
    drops = unique_drops(day)
    span = (f"last {UNIQUE_DAYS} server days, {unique_days(day)[0]} to {day}"
            if day else f"last {UNIQUE_DAYS} server days")
    if not drops:
        return [f"UNIQUE ITEMS -- {span}: none"]
    head = ("server day", "dungeon", "item")
    widths = [max(len(row[column]) for row in [head, *drops])
              for column in range(len(head) - 1)]
    rows = ["  ".join(f"{value:<{width}}" for value, width
                      in zip(row, widths)) + f"  {row[-1]}"
            for row in [head, *drops]]
    return [f"UNIQUE ITEMS -- {span}, {len(drops)} so far; each server "
            f"day reset to reset", "", rows[0],
            "-" * max(len(row) for row in rows), *rows[1:]]


def jewel_counts(day):
    counts, first = dict.fromkeys(JEWELS["worth"], 0), None
    try:
        for line in UNIQUE_FILE.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
                first = min(first or record["day"], record["day"])
                if record["day"] == day:
                    for name in counts:
                        if name.casefold() in record["item"].casefold():
                            counts[name] += 1
            except Exception:
                continue
    except Exception:
        pass
    return counts if first is not None and first <= day else None


def jewel_alz(counts):
    return sum(count * JEWELS["worth"][name] for name, count in counts.items())


def dp_gained(successful):
    return sum(int(successful.get(name, 0)) * int(DUNGEONS[name]["dp"])
               for name in DUNGEONS if "dp" in DUNGEONS[name])


def day_counts(state, day, today):
    if day != today:
        return (state.get("history") or {}).get(day)
    won = state.get("dungeon_successful") or {}
    return {"entered": {**dungeon_counts(state, today),
                        **{f"CA{level}": count for level, count
                           in ran_today(state, today).items()}},
            "successful": {**(dict(won.get("counts") or {})
                              if won.get("day") == today else {}),
                           **{f"CA{level}": count for level, count
                              in ran_today(state, today,
                                           "successful").items()}}}


def week_lines(state, today):
    if today is None:
        return []
    last = datetime.date.fromisoformat(today)
    first = last - datetime.timedelta(days=SUMMARY_DAYS - 1)
    unit = JEWELS["unit"]
    head = (f"{'day':<26}{'DP gained':>11}{'Alz from jewels':>17}"
            f"{'dungeons run':>14}")
    rows, sums = [], [0, 0, 0]
    for back in range(SUMMARY_DAYS - 1, -1, -1):
        day = last - datetime.timedelta(days=back)
        kept = day_counts(state, day.isoformat(), today)
        jewels = jewel_counts(day.isoformat())
        values = [None if kept is None else dp_gained(kept["successful"]),
                  None if jewels is None else jewel_alz(jewels),
                  None if kept is None
                  else sum(int(count) for count in kept["entered"].values())]
        sums = [total + (value or 0) for total, value in zip(sums, values)]
        shown = ["-" if value is None else f"{value}{suffix}"
                 for value, suffix in zip(values, ["", unit, ""])]
        label = f"{day:%a %Y-%m-%d}" + ("" if back else " (so far)")
        rows.append(f"{label:<26}{shown[0]:>11}{shown[1]:>17}{shown[2]:>14}")
    return [f"LAST {SUMMARY_DAYS} DAYS -- {first} to {last}, each day reset "
            f"to reset, like the daily limits; DP from successful runs only",
            "", head, "-" * len(head),
            *rows, "=" * len(head),
            f"{f'{SUMMARY_DAYS} DAYS':<26}{sums[0]:>11}"
            f"{f'{sums[1]}{unit}':>17}{sums[2]:>14}"]


def jewel_lines(day):
    worth, unit = JEWELS["worth"], JEWELS["unit"]
    counts = jewel_counts(day) or dict.fromkeys(worth, 0)
    named = max(len(name) for name in worth)
    total = jewel_alz(counts)
    return (["Today Alz from jewels:"]
            + [f"{name:<{named}}  {count} x {worth[name]}{unit} = "
               f"{count * worth[name]}{unit}"
               for name, count in sorted(counts.items(),
                                         key=lambda pair: worth[pair[0]])]
            + [f"Today: {total}{unit}"])


def watch_chat(stop, verbose=True):
    told = set()
    try:
        while not calibration.STOPPED.is_set():
            final = stop.is_set()
            try:
                read_chat(verbose)
            except BaseException as exc:
                reason = f"{type(exc).__name__}: {exc}"
                if verbose and reason not in told:
                    told.add(reason)
                    print(f"  the chat could not be read ({reason}); the "
                          f"run goes on and the next read is in "
                          f"{CHAT_EVERY:g}s")
            if final:
                return
            stop.wait(CHAT_EVERY)
    except BaseException:
        pass
    finally:
        CHAT["last"] = CHAT["run"]


def chat_run(dungeon_name, location):
    return {"label": run_label(dungeon_name, location),
            "name": location_of(dungeon_name, location)["name"],
            "day": server_day()}


def own_chat(dungeon_name, location, verbose=True):
    try:
        chat_reader_again(verbose)
        CHAT["last"] = CHAT["run"]
        CHAT["run"] = chat_run(dungeon_name, location)
        CHAT["fresh"] = True
        if verbose:
            print(f"  the chat is read before each {CHAT_READS['before']} "
                  f"ride and while the {CHAT_READS['button']} banner is up, "
                  f"for unique item drops in {CHAT['run']['label']}, by a "
                  f"reader process of its own")
    except Exception as exc:
        if verbose:
            print(f"  the chat reads could not start ({type(exc).__name__}: "
                  f"{exc}); the run goes on without them")


def room_chat(verbose=True):
    try:
        with KEY_LOCK:
            if PLAYING.is_set():
                return
            image = calibration.grab()
        threading.Thread(target=room_chat_read, args=(image, verbose),
                         daemon=True).start()
    except Exception as exc:
        if verbose:
            print(f"  the chat could not be read ({type(exc).__name__}: "
                  f"{exc}); the run goes on")


def room_chat_read(image, verbose=True):
    try:
        read_chat(verbose, image)
    except Exception as exc:
        if verbose:
            print(f"  the chat could not be read ({type(exc).__name__}: "
                  f"{exc}); the run goes on")


def start_chat(phase, dungeon_name, location, verbose=True):
    try:
        chat_reader_again(verbose)
        CHAT["run"] = chat_run(dungeon_name, location)
        CHAT["fresh"] = True
        if verbose:
            print(f"  the chat is read every {CHAT_EVERY:g}s, never while a "
                  f"recording plays, for unique item drops in "
                  f"{CHAT['run']['label']}")
        phase["chat"].start()
    except Exception as exc:
        if verbose:
            print(f"  the chat reader did not start ({type(exc).__name__}: "
                  f"{exc}); the run goes on without it")


def watch_end(stop, failed, killing, timer, verbose=True):
    try:
        while not stop.is_set() and not calibration.STOPPED.is_set():
            if timer.get("deadline") is None:
                stop.wait(WAIT_SLICE)
                continue
            try:
                image = calibration.grab()
                defeat, quest, *banner = read_windows(
                    image, [tuple(DEFEAT_SIGN["window"]),
                            tuple(BUTTONS["quest_failed"]["window"])]
                    + ([CLEARED_WINDOW] if killing.is_set() else []))
            except Exception as exc:
                if verbose:
                    print(f"  the end screen could not be read "
                          f"({type(exc).__name__}: {exc}); nothing is seen "
                          f"this time, and it is read again in {END_POLL:g}s")
                stop.wait(END_POLL)
                continue
            if banner and CLEARED_TEXT.search(banner[0]):
                banner = banner[0]
                END["at"] = time.monotonic()
                OVER.set()
                ENDED.set()
                calibration.snap("dungeon_end", image)
                if verbose:
                    print(f"  {banner!r}: the dungeon is cleared; every action "
                          f"stops now, exit in "
                          f"{timer.get('end_wait', END_WAIT):g}s")
                return
            if row_model._key(DEFEAT_SIGN["word"]) in row_model._key(defeat):
                END["at"] = time.monotonic()
                END["defeated"] = True
                OVER.set()
                ENDED.set()
                calibration.snap("dungeon_defeated", image)
                if verbose:
                    print(f"  {defeat!r}: the dungeon is failed; every action "
                          f"stops now, then "
                          f"{' -> '.join(key for _action, key, _wait in DEFEAT)}"
                          f" and the manual exit")
                return
            if (row_model._key(BUTTONS["quest_failed"]["word"])
                    in row_model._key(quest)):
                END["at"] = time.monotonic()
                END["quest_failed"] = True
                OVER.set()
                ENDED.set()
                calibration.snap("dungeon_failed", image)
                if verbose:
                    print(f"  {quest!r}: the dungeon is failed; every action "
                          f"stops now, then its OK and the next dungeon in "
                          f"the list")
                return
            stop.wait(END_POLL)
    except Exception as exc:
        failed["error"] = exc
        OVER.set()
        ENDED.set()


def overtime(timer, verbose=True):
    deadline = timer.get("deadline")
    if (deadline is None or OVER.is_set()
            or time.monotonic() < deadline):
        return False
    END["at"], END["overtime"] = time.monotonic(), True
    OVER.set()
    ENDED.set()
    calibration.snap("dungeon_overtime")
    if verbose:
        print(f"  {timer.get('limit', HARD_LIMIT):g}s since {CHALLENGE_WORD} "
              f"and the dungeon is not finished; every action stops now, then "
              f"the manual exit")
    return True


def watch_limit(stop, timer, verbose=True):
    while not stop.is_set() and not OVER.is_set():
        if overtime(timer, verbose):
            return
        stop.wait(WAIT_SLICE)


def off_for_the_day(verbose=True):
    today = server_day()
    state = _state()
    state["off_day"] = today
    _save(state)
    if verbose:
        print(f"  no more dungeons on the dungeon day {today}; they start "
              f"again at the next reset")


def off_today(today):
    return today is not None and _state().get("off_day") == today


def allow_today():
    state = _state()
    gone = state.pop("off_day", None)
    if gone is not None:
        _save(state)
    return gone


def keep_failed(location, timer, first):
    calibration.keep_failed(location, [*timer.get("frames", []),
                                       (first, calibration.frames_taken())])


def before_step(dungeon_name, step):
    sequence = DUNGEONS[dungeon_name]["sequence"]
    names = [part if isinstance(part, str) else part[0] for part in sequence]
    if step not in names:
        raise SystemExit(f"{dungeon_name} has no step {step!r}; its steps are "
                         f"{', '.join(names)}")
    return sequence[:names.index(step)]


def record_from_here(step, verbose=True):
    import record_action
    if verbose:
        print(f"  stopped before {step}; nothing more is sent to the game. "
              f"The recorder takes over from here")
    with calibration._own_ctrl():
        return record_action.record()


def run_dungeon(dungeon_name, location, verbose=True, steps=None,
                timer=None, until=None):
    timer = {"until": None} if timer is None else timer
    timer["end_wait"] = end_wait(dungeon_name)
    RUN_OUTCOME.update(failed=False, inside=None)
    limit = DUNGEONS[dungeon_name].get("daily_limit")
    if limit is not None and steps is None:
        ran = dungeon_counts(_state(), server_day()).get(dungeon_name, 0)
        if ran >= int(limit):
            if verbose:
                print(f"{dungeon_name}: {ran} of {limit} entered on this "
                      f"dungeon day; nothing done until the next reset")
            return False
    if LIVE["view"] is not None and steps is None:
        name, count, cap = run_count(dungeon_name, location)
        LIVE["view"].prepare(name, count + 1, cap, view_rooms(dungeon_name))
    stop, failed, killing = threading.Event(), {}, threading.Event()
    phase = {"killing": killing,
             "healer": threading.Thread(
                 target=heal_loop,
                 args=(stop, bool(DUNGEONS[dungeon_name].get("heal_kills_only",
                                                             False)),
                       DUNGEONS[dungeon_name].get("heal_ride_every")),
                 daemon=True),
             "ender": threading.Thread(target=watch_end,
                                       args=(stop, failed, killing, timer,
                                             verbose),
                                       daemon=True),
             "limit": threading.Thread(target=watch_limit,
                                       args=(stop, timer, verbose),
                                       daemon=True),
             "chat": threading.Thread(target=watch_chat, args=(stop, verbose),
                                      daemon=True)}
    OVER.clear()
    END["at"], END["defeated"], END["overtime"] = None, False, False
    END["quest_failed"] = False
    BM_OFF["at"] = None
    ALT_BM["on"] = False
    BM_CYCLE.update(slot=None, ends=None, extended=False, resume=False,
                    keep_aura=False, other=None, pressed=None,
                    with_aura=False, aura_at=None,
                    aura_target=bool(setting(dungeon_name,
                                             "cycle_aura_target")))
    LOCK_ALL.update(on=bool(setting(dungeon_name, "lock_target")),
                    every=float(setting(dungeon_name, "lock_target_every")),
                    twin=float(setting(dungeon_name, "twin_target_every")))
    SEARCH_ATTACK["keys"] = list(setting(dungeon_name, "search_attack"))
    HEAL["every"] = float(setting(dungeon_name, "heal_every"))
    if steps is None:
        if not focus_game():
            raise RuntimeError("could not bring the game to the foreground.")
        off_bike(f"before {dungeon_name}", verbose)
    first = calibration.frames_taken() + 1
    RUN_START["frame"] = first
    crashed, stopped = None, False
    try:
        if not overtime(timer, verbose):
            phase["limit"].start()
        phase["ender"].start()
        run_sequence(dungeon_name, location, phase, verbose,
                     steps if until is None
                     else before_step(dungeon_name, until), timer=timer)
        if until is not None:
            OVER.set()
            return True
        if killing.is_set() and not OVER.is_set():
            again = bool(DUNGEONS[dungeon_name].get("kill_again"))
            if verbose:
                print(f"  the sequence is done; {TARGET_KEY} every "
                      f"{TARGET_EVERY:g}s until the dungeon end screen, up to "
                      f"{END_TIMEOUT:g}s"
                      + (f"; {LAST_KILL['name']!r} is killed again whenever "
                         f"the HP bar shows it"
                         if again and LAST_KILL["name"] else ""))
            until, presses = time.monotonic() + END_TIMEOUT, 0
            while not OVER.is_set():
                calibration.halt_if_stopped()
                if time.monotonic() >= until:
                    break
                if not focus_game():
                    raise RuntimeError("the game is not in the foreground; "
                                       "the wait for the end screen stopped.")
                if again and boss_again(verbose):
                    until = time.monotonic() + END_TIMEOUT
                    continue
                aura_when_due(verbose)
                main_bm_when_due(verbose)
                main_bm_recast_when_due(verbose)
                bm_cycle_when_due(verbose)
                other_bm_when_on(verbose)
                auto_target()
                presses += 1
                OVER.wait(TARGET_EVERY)
            if verbose:
                print(f"  {presses} {TARGET_KEY} press(es) after the last "
                      f"step")
        if failed:
            raise RuntimeError(f"the dungeon end screen could not be read: "
                               f"{failed['error']}. Stopped.")
        stop.set()
        for thread in (phase["healer"], phase["ender"], phase["limit"],
                       phase["chat"]):
            if thread.is_alive():
                thread.join()
        if END["overtime"] or END["quest_failed"]:
            why = ("Quest Dungeon Failed" if END["quest_failed"]
                   else f"past its {hard_limit(dungeon_name):g}s")
            manual = manual_exit(dungeon_name)
            if not manual:
                raise RuntimeError(f"{dungeon_name}: {why} and no manual "
                                   f"exit.")
            ENDED.clear()
            box_up = dismiss_quest_failed(
                verbose, float(BUTTONS["quest_failed"]["timeout"]))
            run_sequence(dungeon_name, location, phase, verbose,
                         setting(dungeon_name, "before_exit"), False)
            if not box_up:
                run_sequence(dungeon_name, location, phase, verbose, manual,
                             False)
            RUN_OUTCOME["inside"] = None
            if verbose:
                print(f"  {dungeon_name} {location}: {why}; left it by hand")
                run_result(dungeon_name, location, f"fail, {why}")
            RUN_OUTCOME["failed"] = True
            keep_failed(location, timer, first)
            keep_failed_frames(dungeon_name, location, why, verbose)
            arm_death_watch(dungeon_name, verbose)
            run_sequence(dungeon_name, location, phase, verbose,
                         DUNGEONS[dungeon_name].get("after_exit", []), False)
        elif END["defeated"]:
            press_buttons("defeat", DEFEAT, verbose)
            ENDED.clear()
            run_sequence(dungeon_name, location, phase, verbose,
                         setting(dungeon_name, "before_exit"), False)
            relog_home(verbose)
            RUN_OUTCOME["inside"] = None
            if verbose:
                print(f"  {dungeon_name} {location}: the dungeon is failed; "
                      f"relogged to the home channel")
                run_result(dungeon_name, location, "fail, defeated")
            RUN_OUTCOME["failed"] = True
            keep_failed(location, timer, first)
            keep_failed_frames(dungeon_name, location, "defeated", verbose)
            arm_death_watch(dungeon_name, verbose)
            run_sequence(dungeon_name, location, phase, verbose,
                         DUNGEONS[dungeon_name].get("after_exit", []), False)
        elif END["at"] is not None:
            count_success(dungeon_name, location, verbose)
            if verbose:
                run_result(dungeon_name, location, "pass")
            run_sequence(dungeon_name, location, phase, verbose,
                         setting(dungeon_name, "before_exit"), False)
            while time.monotonic() < END["at"] + timer["end_wait"]:
                calibration.halt_if_stopped()
                time.sleep(min(WAIT_SLICE,
                               max(0.0, END["at"] + timer["end_wait"]
                                   - time.monotonic())))
            exit_dungeon(dungeon_name, verbose)
            RUN_OUTCOME["inside"] = None
            ENDED.clear()
            arm_death_watch(dungeon_name, verbose)
            run_sequence(dungeon_name, location, phase, verbose,
                         DUNGEONS[dungeon_name].get("after_exit", []), False)
            if verbose:
                print(f"  {dungeon_name} {location}: the dungeon is over")
        elif killing.is_set():
            manual = manual_exit(dungeon_name)
            if not manual:
                raise RuntimeError(f"no dungeon end screen within "
                                   f"{END_TIMEOUT:g}s of the last step.")
            if verbose:
                print(f"  no dungeon end screen {END_TIMEOUT:g}s after the last "
                      f"step; leaving by hand: {manual}")
            ENDED.clear()
            box_up = dismiss_quest_failed(
                verbose, float(BUTTONS["quest_failed"]["timeout"]))
            run_sequence(dungeon_name, location, phase, verbose,
                         setting(dungeon_name, "before_exit"), False)
            if not box_up:
                run_sequence(dungeon_name, location, phase, verbose, manual,
                             False)
            RUN_OUTCOME["inside"] = None
            if verbose:
                print(f"  {dungeon_name} {location}: left the dungeon by hand")
                run_result(dungeon_name, location,
                           f"fail, no end screen within {END_TIMEOUT:g}s")
            RUN_OUTCOME["failed"] = True
            keep_failed(location, timer, first)
            keep_failed_frames(dungeon_name, location, "no end screen",
                               verbose)
            arm_death_watch(dungeon_name, verbose)
    except KeyboardInterrupt:
        stopped = True
        raise
    except calibration.Offline:
        crashed = "disconnected"
        run_result(dungeon_name, location, "fail, disconnected")
        raise
    except Exception:
        crashed = "crashed"
        raise
    finally:
        stop.set()
        drop_early_watch()
        AURA_DUE["at"] = None
        AURA_WINDOW["until"] = None
        MAIN_BM_DUE["at"] = None
        MAIN_RECAST.update(at=None, dungeon=None)
        LAST_KILL.update(name=None, words=None, loop=None)
        for thread in (phase["healer"], phase["ender"], phase["limit"],
                       phase["chat"]):
            if thread.is_alive():
                thread.join()
        if crashed == "crashed" and steps is None:
            bike_after_crash(dungeon_name, verbose)
        if crashed:
            keep_failed_frames(dungeon_name, location, crashed, verbose)
        end_run_film(dungeon_name, location,
                     bool(crashed) or stopped or RUN_OUTCOME["failed"],
                     verbose)
    for name in setting(dungeon_name, "after_run_buys"):
        try:
            buy_max(name, verbose)
        except RuntimeError as exc:
            calibration.snap("after_run_buy_failed")
            if verbose:
                print(f"  {PURCHASES[name]['name']} could not be bought "
                      f"({exc}); the buy is skipped and the loop goes on")
    if steps is None:
        off_bike(f"after {dungeon_name}", verbose)


def start_run_film(dungeon_name, verbose=True):
    end_run_film(dungeon_name, None, False, False)
    folder = calibration.LOG_DIR / FAILED_VIDEOS["folder"] / dungeon_name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / FAILED_VIDEOS["recording"]
    path.unlink(missing_ok=True)
    tape = calibration.film(path)
    RUN_FILM.update(tape=tape, path=path if tape is not None else None)
    if verbose and tape is not None:
        print(f"  recording the run to {path}; it is deleted if the run "
              f"passes and kept if it fails, the newest "
              f"{int(FAILED_VIDEOS['keep'])} per dungeon")


def end_run_film(dungeon_name, location, keep, verbose=True):
    tape, path = RUN_FILM["tape"], RUN_FILM["path"]
    RUN_FILM.update(tape=None, path=None)
    if tape is None:
        return
    calibration.film_done(tape)
    if not keep or location is None:
        path.unlink(missing_ok=True)
        if verbose:
            print("  the run passed; its video is deleted")
        return
    dest = path.with_name(failed_frames_name(dungeon_name, location)
                          + FAILED_VIDEOS["suffix"])
    try:
        path.replace(dest)
    except OSError as exc:
        if verbose:
            print(f"  the video of this run stays at {path}: {exc}")
        return
    videos = []
    for video in path.parent.glob(f"*{FAILED_VIDEOS['suffix']}"):
        parts = video.name.split("_")
        if video.name != FAILED_VIDEOS["recording"] and len(parts) > 2:
            videos.append((parts[1], parts[2], video))
    videos.sort()
    for _day, _time, old in videos[:max(0, len(videos)
                                        - int(FAILED_VIDEOS["keep"]))]:
        old.unlink(missing_ok=True)
    if verbose:
        print(f"  the video of this run is kept: {dest}")


def failed_frames_name(dungeon_name, location):
    from profit_summary import CLOCK
    now = internet_now()
    when = (now.astimezone(CLOCK).strftime(FAILED_FRAMES["time_format"])
            if now is not None else FAILED_FRAMES["no_time"])
    try:
        label = run_label(dungeon_name, location)
    except Exception:
        label = f"{dungeon_name} {location}"
    return re.sub(r"[^A-Za-z0-9.-]+", "_",
                  f"{COMMIT['sha'] or FAILED_FRAMES['no_commit']} {when} "
                  f"{label}").strip("_")


def keep_failed_frames(dungeon_name, location, why, verbose=True):
    try:
        first, source = RUN_START["frame"], calibration.RUN_FRAMES
        if not calibration.FRAMES_ON or source is None or first is None:
            return
        calibration.frames_written()
        root = calibration.LOG_DIR / FAILED_FRAMES["folder"]
        dest = root / failed_frames_name(dungeon_name, location)
        dest.mkdir(parents=True, exist_ok=True)
        kept = 0
        for png in sorted(source.glob("*.png")):
            number = png.name.split("_", 1)[0]
            if not number.isdigit() or int(number) < first:
                continue
            try:
                with Image.open(png) as image:
                    image.convert("RGB").save(
                        dest / f"{png.stem}.jpg",
                        quality=int(FAILED_FRAMES["quality"]))
                kept += 1
            except Exception:
                continue
        rows = []
        index = source / calibration.FRAME_INDEX
        if index.exists():
            for line in index.read_text(encoding="utf-8",
                                        errors="replace").splitlines():
                try:
                    if int(json.loads(line)["n"]) >= first:
                        rows.append(line)
                except Exception:
                    continue
            (dest / calibration.FRAME_INDEX).write_text(
                "".join(f"{row}\n" for row in rows), encoding="utf-8")
        offsets = [json.loads(row)["log"] for row in rows]
        offsets = [offset for offset in offsets if offset is not None]
        log = getattr(getattr(sys.stdout, "handle", None), "name", None)
        if log and offsets:
            with open(log, "rb") as handle:
                handle.seek(min(offsets))
                (dest / FAILED_FRAMES["log_name"]).write_bytes(handle.read())
        (dest / FAILED_FRAMES["why_name"]).write_text(f"{why}\n",
                                                     encoding="utf-8")
        runs = []
        for folder in root.iterdir():
            parts = folder.name.split("_")
            if folder.is_dir() and len(parts) > 2:
                runs.append((parts[1], parts[2], folder))
        runs.sort()
        for _day, _time, old in runs[:max(0, len(runs)
                                            - int(FAILED_FRAMES["keep"]))]:
            shutil.rmtree(old, ignore_errors=True)
        if verbose:
            print(f"  kept {kept} frame(s) of this failed run ({why}) in "
                  f"{dest}")
    except Exception as exc:
        if verbose:
            print(f"  the frames of this failed run were not kept: "
                  f"{type(exc).__name__}: {exc}")


def revive_if_dead(verbose=True):
    window, word = tuple(DEFEAT_SIGN["window"]), DEFEAT_SIGN["word"]
    up, _seen = wait_for(window, word, 0)
    if not up:
        return False
    if DEATH_WATCH["armed"]:
        raise calibration.Defeated(f"the death {word} is up between "
                                   f"dungeons")
    calibration.snap("dead_outside_dungeon")
    if verbose:
        print(f"  the death {word} is up outside the dungeon: revive, "
              f"{REVIVE_SETTLE:g}s for the town to load, the default state, "
              f"then the next dungeon entry")
    press_buttons("defeat", DEFEAT, verbose)
    wait_timer(REVIVE_SETTLE, False)
    calibration.close_everything(verbose=verbose)
    return True


def press_buttons(label, steps, verbose=True):
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    if verbose:
        print(f"  {label}: {' -> '.join(key for _action, key, _wait in steps)}")
    for action, key, seconds in steps:
        if action == "click":
            point = centre(tuple(BUTTONS[key]["window"]))
            if verbose:
                print(f"  {BUTTONS[key]['word']} at {point}, clicked without "
                      f"a read")
            click(*point, seconds)
            continue
        press_button(key, seconds, verbose, action != "claim")


def exit_dungeon(dungeon_name, verbose=True):
    steps = DUNGEONS[dungeon_name].get("exit")
    press_buttons("exit", EXIT if steps is None
                  else [tuple(step) for step in steps], verbose)


def exit_now(dungeon_name, verbose=True):
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    steps = [tuple(step)
             for step in DUNGEONS[dungeon_name].get("exit") or EXIT]
    action, key, _wait = steps[0]
    button = BUTTONS[key]
    if action == "button" and not wait_for(tuple(button["window"]),
                                           button["word"], 0)[0]:
        if verbose:
            print(f"  no {button['word']!r} in {button['window']}: that step "
                  f"is past, so the exit starts after it")
        steps = steps[1:]
    press_buttons("exit", steps, verbose)
    run_sequence(dungeon_name, only_location(dungeon_name), {}, verbose,
                 list(DUNGEONS[dungeon_name].get("after_exit", [])), False)


def escape_everything(verbose=True):
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    presses, pressed = 0, None
    while True:
        calibration.halt_if_stopped()
        up, seen = wait_for(MENU_WINDOW, MENU_WORD, 0)
        if up:
            press(_INPUT["VK_ESCAPE"])
            if verbose:
                print(f"  the menu is up after {presses} Escape(s); one "
                      f"more Escape closes it")
            return
        if pressed is None or time.monotonic() - pressed >= ESCAPE_GAP:
            if presses == ESCAPE_TRIES:
                calibration.snap("no_menu")
                raise RuntimeError(
                    f"no {MENU_WORD!r} in {list(MENU_WINDOW)} after "
                    f"{ESCAPE_TRIES} Escape(s); it reads {seen!r}. Nothing "
                    f"more pressed.")
            press(_INPUT["VK_ESCAPE"])
            presses, pressed = presses + 1, time.monotonic()


def with_entry_retries(attempt_once, verbose=True):
    for attempt in range(ENTRY_RETRIES + 1):
        try:
            return attempt_once(attempt)
        except RuntimeError as exc:
            if attempt == ENTRY_RETRIES:
                raise
            calibration.snap("entry_retry")
            if verbose:
                print(f"  {exc} Escape until the menu is up, one more "
                      f"Escape, then the entry again from buying it "
                      f"(retry {attempt + 1} of {ENTRY_RETRIES})")
            escape_everything(verbose)


def buy_and_open(dungeon_name, location, place, attempt, verbose=True):
    if attempt:
        get_entry(*place["entry"], verbose=verbose)
    else:
        REFILL["done"] = False
    open_dungeon_key(dungeon_name, location, verbose, refill=attempt > 0)


def with_challenge_retries(dungeon_name, location, place, verbose=True):
    for attempt in range(ENTRY_RETRIES + 1):
        try:
            return start_dungeon(verbose)
        except RuntimeError as exc:
            if attempt == ENTRY_RETRIES:
                raise
            calibration.snap("challenge_retry")
            if verbose:
                print(f"  {exc} Escape until the menu is up, one more "
                      f"Escape, {CHALLENGE_RETRY_WAIT:g}s wait, then the "
                      f"entry again from buying it (retry {attempt + 1} of "
                      f"{ENTRY_RETRIES})")
            escape_everything(verbose)
            wait_timer(CHALLENGE_RETRY_WAIT, verbose)
            with_entry_retries(lambda again: get_entry(
                *place["entry"], verbose=verbose), verbose)
            with_entry_retries(lambda again: buy_and_open(
                dungeon_name, location, place, again, verbose), verbose)


def hold_key(key, seconds, verbose=True):
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    if verbose:
        print(f"  hold {key} for {seconds:g}s")
    tap(KEYS[key], hold=seconds)
    calibration.snap(f"hold_{key}")


def look_kill_bm(dungeon_name, rest, verbose=True):
    looks, name, loop_name, search = rest[0], rest[1], rest[2], float(rest[3])
    words = target_words(dungeon_name, name)
    loop = list(_DUNGEON[loop_name])
    main_loop = main_loop_for(dungeon_name, loop_name)
    if kill_bm(name, words, verbose, loop, search, main_loop) or OVER.is_set():
        return
    for key, back, seconds in looks:
        if verbose:
            print(f"  {name!r} not targeted: hold {key} {float(seconds):g}s, "
                  f"{TARGET_KEY} again, then {back} {float(seconds):g}s back")
        hold_key(key, float(seconds), verbose)
        found = kill_bm(name, words, verbose, loop, search, main_loop)
        if OVER.is_set():
            return
        hold_key(back, float(seconds), verbose)
        if found:
            return
    if verbose:
        print(f"  {name!r} not targeted either way: {TARGET_KEY} from where it "
              f"started until it shows")
    kill_bm(name, words, verbose, loop, None, main_loop)


def first_hunt(dungeon_name, place, following):
    if not following:
        return None
    name, *rest = (following[0] if isinstance(following[0], list)
                   else [following[0]])
    rest = [place.get(part, part) if isinstance(part, str) else part
            for part in rest]
    if name in ("hold_kill_all", "ride_kill_all"):
        name, rest = "kill_all", rest[1:]
    if name in ("kill", "kill_bm", "kill_all"):
        return rest[0], target_words(dungeon_name, rest[0])
    if name == "kill_before_boss":
        return (f"{rest[0]} or {rest[1]}",
                target_words(dungeon_name, rest[0])
                + target_words(dungeon_name, rest[1]))
    return None


def kill_all(dungeon_name, rest, verbose=True):
    words = target_words(dungeon_name, rest[0])
    loop = list(_DUNGEON[rest[1]]) if len(rest) > 1 else None
    main_loop = (main_loop_for(dungeon_name, rest[1])
                 if len(rest) > 1 else None)
    search = (rest[2] if len(rest) > 2
              else float(setting(dungeon_name, "kill_all_search")))
    kills = 0
    while not OVER.is_set():
        if not kill_bm(rest[0], words, verbose, loop,
                       search if kills or not words else None,
                       main_loop):
            break
        if OVER.is_set():
            break
        kills += 1
    if verbose:
        print(f"  kill_all {rest[0]!r}: {kills} killed, "
              + ("until the dungeon ended" if search is None
                 else f"then none more targeted within {search:g}s"))
    end_aura_window(verbose)
    return kills


def run_sequence(dungeon_name, location, phase, verbose=True, steps=None,
                 stop_when_over=True, timer=None):
    place = location_of(dungeon_name, location)
    timer = {"until": None} if timer is None else timer
    steps = DUNGEONS[dungeon_name]["sequence"] if steps is None else steps
    stage = "opening"
    current = (None, None)
    for number, step in enumerate(steps, 1):
        name, *rest = step if isinstance(step, list) else [step]
        before, current = current, (name, time.monotonic())
        if stop_when_over and OVER.is_set():
            if verbose:
                print(f"  the dungeon is over; {name} and the rest are "
                      f"skipped")
            return
        rest = [place.get(part, part) if isinstance(part, str) else part
                for part in rest]
        if (SEQUENCES / dungeon_name / f"{name}.jsonl").exists():
            stage = name
        if verbose:
            print(f"{dungeon_name} {location} [{stage}, step {number} of "
                  f"{len(steps)}]: "
                  f"{' '.join(str(part) for part in [name, *rest])}")
        if name == "get_entry":
            with_entry_retries(lambda attempt: get_entry(
                *place["entry"], verbose=verbose), verbose)
        elif name == "open_dungeon_key":
            with_entry_retries(lambda attempt: buy_and_open(
                dungeon_name, location, place, attempt, verbose), verbose)
        elif name == "start_dungeon":
            timer["limit"] = hard_limit(dungeon_name)
            clicked = with_challenge_retries(dungeon_name, location, place,
                                             verbose)
            timer["deadline"] = clicked + timer["limit"]
            RUN_OUTCOME["inside"] = location
            RUN_CLOCK.update(started=clicked, rooms=[[
                DUNGEONS[dungeon_name].get("start_room") or "opening",
                clicked]])
            start_run_film(dungeon_name, verbose)
            timer["started"] = internet_now()
            if verbose:
                print(f"  the dungeon has {timer['limit']:g}s from the "
                      f"{CHALLENGE_WORD} click")
            count_challenge(dungeon_name, location, timer, verbose)
            if LIVE["view"] is not None:
                LIVE["view"].start(*run_count(dungeon_name, location),
                                   view_rooms(dungeon_name), clicked)
            if setting(dungeon_name, "start_cancel_bm"):
                cancel_bm(dungeon_name, verbose)
            wait_timer(max(0.0, clicked
                           + float(setting(dungeon_name, "challenge_wait"))
                           - time.monotonic()), verbose)
            start_room = DUNGEONS[dungeon_name].get("start_room")
            if start_room is not None:
                say_room(dungeon_name, location, start_room)
        elif name == "inventory":
            prepare(verbose)
            close_inventory(verbose)
        elif name == "kill":
            kill(rest[0], target_words(dungeon_name, rest[0]), verbose)
        elif name == "bm_aura":
            bm_aura_step(dungeon_name, verbose)
        elif name == "spam":
            bm_aura(verbose, list(rest[:-1]), float(rest[-1]))
        elif name == "aura":
            aura_now(verbose)
        elif name == "main_bm":
            main_bm_now(float(setting(dungeon_name, "bm_aura_for")),
                        verbose=verbose)
        elif name == "bm_cycle":
            cycle_cast(rest[0] if rest else BM_CYCLE_SET["slots"][0], verbose,
                       first_hunt(dungeon_name, place,
                                  steps[number:number + 1]))
        elif name == "heal_every":
            HEAL["every"] = (float(rest[0]) if rest
                             else float(setting(dungeon_name, "heal_every")))
            if verbose:
                print(f"  heal ({HEAL_KEY}) every {HEAL['every']:g}s while a "
                      f"kill is on")
        elif name in ("aura_on", "keep_aura"):
            if name == "keep_aura":
                BM_CYCLE["keep_aura"] = True
                if verbose:
                    print("  every BM cast from here to the end of the run "
                          "gets its aura right after it")
            if BM_CYCLE["slot"] is not None and not BM_CYCLE["extended"]:
                cycle_aura(verbose, "target" in rest)
            elif verbose:
                print(f"  {BM_CYCLE['slot'] or 'no BM'} "
                      + ("already has its aura" if BM_CYCLE["slot"]
                         else "is on, so no aura"))
        elif name == "swap_bm_aura":
            swap_bm_aura(dungeon_name, float(rest[0]), verbose)
        elif name == "press_other_bm":
            press_other_bm(verbose, "aura" in rest)
        elif name == "tab_two":
            if not focus_game():
                raise RuntimeError("could not bring the game to the "
                                   "foreground.")
            if verbose:
                print(f"  hotbar tab II at {list(TAB_TWO)}")
            click(*TAB_TWO, 0.0)
        elif name == "cancel_bm":
            cancel_bm(dungeon_name, verbose)
        elif name == "close_notification":
            close_notification(verbose)
        elif name == "disenchant_all":
            disenchant_all(verbose, setting(dungeon_name, "cooldown_loop"))
        elif name == "kill_phase":
            if verbose:
                ride_every = DUNGEONS[dungeon_name].get("heal_ride_every")
                rides = ("not in the recordings" if ride_every is None
                         else f"every {ride_every:g}s while a recording plays")
                print(f"  kill phase: heal ({HEAL_KEY}) every {HEAL['every']:g}s "
                      + (f"while a kill is on ({rides}, not in the opening), "
                         if DUNGEONS[dungeon_name].get("heal_kills_only")
                         else "except while a recording plays"
                         + ("" if ride_every is None else f" ({rides})")
                         + ", ")
                      + f"and the end screen read all the time, until the end "
                      f"screen has been up {end_wait(dungeon_name):g}s")
            phase["healer"].start()
            phase["killing"].set()
            if CHAT_EVERY is not None:
                start_chat(phase, dungeon_name, location, verbose)
            elif CHAT_READS is not None:
                own_chat(dungeon_name, location, verbose)
        elif name == "kill_bm":
            kill_bm(rest[0], target_words(dungeon_name, rest[0]), verbose,
                    list(_DUNGEON[rest[1]]) if len(rest) > 1 else None,
                    rest[2] if len(rest) > 2 else None,
                    main_loop_for(dungeon_name, rest[1])
                    if len(rest) > 1 else None)
            end_aura_window(verbose)
        elif name == "kill_before_boss":
            loop = list(_DUNGEON[rest[2]]) if len(rest) > 2 else None
            boss_words = target_words(dungeon_name, rest[1])
            if verbose:
                print(f"  {rest[0]!r} first, then {rest[1]!r}; if {rest[1]!r} "
                      f"is targeted before {rest[0]!r} is found, it is killed "
                      f"instead and the hunt for {rest[0]!r} ends")
            kill_bm(f"{rest[0]} or {rest[1]}",
                    target_words(dungeon_name, rest[0]) + boss_words,
                    verbose, loop)
            boss_dead = bool(set(LOCK["hits"]) & set(boss_words))
            if boss_dead and verbose:
                print(f"  {rest[1]!r} was the one killed, so {rest[0]!r} is "
                      f"not looked for any more")
            if not boss_dead and not OVER.is_set():
                kill_bm(rest[1], boss_words, verbose, loop)
            end_aura_window(verbose)
        elif name == "look_kill_bm":
            look_kill_bm(dungeon_name, rest, verbose)
            end_aura_window(verbose)
        elif name == "kill_all":
            kill_all(dungeon_name, rest, verbose)
        elif name == "hold_kill_all":
            for key, seconds in rest[0]:
                if key is not None:
                    hold_key(key, float(seconds), verbose)
                if kill_all(dungeon_name, rest[1:], verbose) or OVER.is_set():
                    break
        elif name == "ride_kill_all":
            if not kill_all(dungeon_name, rest[1:], verbose):
                for ride in rest[0]:
                    if OVER.is_set():
                        break
                    if verbose:
                        print(f"  nothing killed, so {ride} and kill_all again")
                    aura_before(dungeon_name, ride, verbose)
                    bm_cycle_before(dungeon_name, ride, verbose)
                    play(dungeon_name, ride, verbose)
                    bm_cycle_after(verbose)
                    if kill_all(dungeon_name, rest[1:], verbose) or OVER.is_set():
                        break
        elif name == "wait":
            seconds = float(rest[0])
            if before[0] in KILL_STEPS:
                since = time.monotonic() - max(before[1],
                                               KILL_DONE["at"] or before[1])
                if verbose:
                    print(f"  {since:.1f}s already since the last kill "
                          f"(its search counts toward the wait), so "
                          f"{max(0.0, seconds - since):.1f}s more")
                seconds = max(0.0, seconds - since)
            wait_timer(seconds, verbose)
        elif name == "timer":
            timer["until"] = time.monotonic() + float(rest[0])
            if verbose:
                print(f"  timer {float(rest[0]):g}s started")
        elif name == "wait_timer":
            lead = float(rest[0]) if rest else 0.0
            left = max(0.0, timer["until"] - lead - time.monotonic())
            if verbose:
                print(f"  {max(0.0, timer['until'] - time.monotonic()):.1f}s "
                      f"left on the timer"
                      + (f"; waiting until {lead:g}s are left" if lead else ""))
            wait_timer(left, False)
            if verbose:
                print(f"  {lead:g}s left on the timer" if lead
                      else "  timer up")
        elif name == "chat":
            chat(rest[0], verbose)
        elif name == "use":
            for index, slot in enumerate(rest):
                if index:
                    time.sleep(USE_GAP)
                use_slot(slot, verbose)
        elif name == "tap":
            if not focus_game():
                raise RuntimeError("could not bring the game to the "
                                   "foreground.")
            for key in rest:
                if verbose:
                    print(f"  tap {key}")
                tap(KEYS[key])
        elif name == "shout":
            shout(verbose)
        elif name == "replay" and dungeon_name in ("ic2", "tm"):
            replay_run(dungeon_name, rest[0], verbose)
            off_bike(f"after the {rest[0]} replay", verbose, False)
        elif name == "play":
            aura_before(rest[0], rest[1], verbose)
            play(rest[0], rest[1], verbose)
        else:
            if name.startswith(ROOM_PREFIX) and RUN_CLOCK["started"] is not None:
                RUN_CLOCK["rooms"].append([name[len(ROOM_PREFIX):],
                                           time.monotonic()])
            if name.startswith(ROOM_PREFIX) and LIVE["view"] is not None:
                LIVE["view"].moving(name[len(ROOM_PREFIX):])
            if (CHAT_READS is not None
                    and name.startswith(CHAT_READS["before"])
                    and name not in setting(dungeon_name, "chat_skip")):
                room_chat(verbose)
            main_bm_recast_before(dungeon_name, name, verbose)
            aura_before(dungeon_name, name, verbose)
            bm_cycle_before(dungeon_name, name, verbose)
            play(dungeon_name, name, verbose)
            if name.startswith(ROOM_PREFIX) and not OVER.is_set():
                say_room(dungeon_name, location, name[len(ROOM_PREFIX):])
            bm_cycle_after(verbose)


def close_notification(verbose=True):
    if NOTIFICATION_CLOSE is None:
        if verbose:
            print("  no notification_close point in this config; nothing "
                  "clicked")
        return
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    if verbose:
        print(f"  the bottom notification's close at "
              f"{list(NOTIFICATION_CLOSE)}, clicked without a read")
    click(*NOTIFICATION_CLOSE, 0.0)


def close_notifications(verbose=True):
    if NOTIFICATION_CLOSE is None:
        return
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    if verbose:
        print(f"  the bottom notifications' close at "
              f"{list(NOTIFICATION_CLOSE)}, clicked {NOTIFICATION_CLICKS} "
              f"times {NOTIFICATION_GAP:g}s apart without a read, before the "
              f"prep")
    for left in range(NOTIFICATION_CLICKS - 1, -1, -1):
        click(*NOTIFICATION_CLOSE, NOTIFICATION_GAP if left else 0.0)


def say_room(dungeon_name, location, room):
    try:
        label = run_label(dungeon_name, location)
    except Exception:
        label = f"{dungeon_name} {location}"
    print(f"{label}: room {room}")
    if LIVE["view"] is not None:
        LIVE["view"].room(room)


def use_slot(name, verbose=True):
    points = {**LOOP_POINTS, **dict(DEFENSIVE_BUFFS), **SLOTS}
    if name not in points:
        raise RuntimeError(f"no hotbar slot {name!r}; the named slots are "
                           f"{sorted(points)}. Nothing clicked.")
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    if name in KEYED_SLOTS:
        if verbose:
            print(f"  {name} {slot_way(name)}")
        use_point(name, 0.0)
        return
    if verbose:
        print(f"  right-click {name} at {list(points[name])}")
    right_click(*points[name], 0.0)


def chat(text, verbose=True):
    import recovery
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    if verbose:
        print(f"  Enter, {CHAT_OPEN:g}s, type {text!r}, Enter")
    tap(KEYS["Enter"])
    time.sleep(CHAT_OPEN)
    with KEY_LOCK:
        recovery._type(text)
    tap(KEYS["Enter"])


def start_block(dungeon_name, location, verbose=True):
    steps = setting(dungeon_name, "block_start")
    if steps:
        if verbose:
            print(f"  {dungeon_name}: the start of a block of runs")
        run_sequence(dungeon_name, location, {}, verbose, steps, False)


def bm_off_at():
    return BM_OFF["at"] if BM_OFF["at"] is not None else time.monotonic()


def cool_down(dungeon_name, rested, verbose=True):
    cooldown = float(setting(dungeon_name, "cooldown"))
    left = rested + cooldown - time.monotonic()
    if left <= 0:
        if verbose:
            print(f"  {dungeon_name}: the {cooldown:g}s cooldown after the BM "
                  f"cancel passed {-left:.1f}s ago, during the exit and the "
                  f"steps after it; the next run starts now")
        return
    pops = list(setting(dungeon_name, "cooldown_loop"))
    if verbose:
        print(f"  {dungeon_name}: {cooldown:g}s cooldown after the BM cancel; "
              f"{left:.1f}s to go before the next run"
              + (f", right-clicking {' -> '.join(pops)} {LOOP_GAP:g}s apart "
                 f"meanwhile" if pops else ""))
    if pops:
        bm_aura(False, pops, left)
    else:
        wait_timer(left, False)


def wait_timer(seconds, verbose=True):
    due = time.monotonic() + seconds
    if verbose:
        print(f"  timer {seconds:g}s started")
    while time.monotonic() < due:
        calibration.halt_if_stopped()
        time.sleep(min(WAIT_SLICE, max(0.0, due - time.monotonic())))
    if verbose:
        print(f"  timer {seconds:g}s up")


def target_words(dungeon_name, name):
    targets = DUNGEONS.get(dungeon_name, {}).get("targets", {})
    if name not in targets:
        raise RuntimeError(f"no target {name!r} in {dungeon_name}; it holds "
                           f"{sorted(targets)}. Nothing pressed.")
    return list(targets[name])


def start_dungeon(verbose=True):
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    up, seen = wait_for(CHALLENGE_WINDOW, CHALLENGE_WORD, TRANSFER_TIMEOUT)
    if not up:
        raise RuntimeError(
            f"no {CHALLENGE_WORD!r} in the dungeon window "
            f"{list(CHALLENGE_WINDOW)} within {TRANSFER_TIMEOUT:g}s; it reads "
            f"{seen!r}. Nothing clicked.")
    after, COOLDOWN["after"] = COOLDOWN["after"], None
    if after is not None:
        cool_down(*after, verbose)
    point = centre(CHALLENGE_WINDOW)
    if verbose:
        print(f"  {CHALLENGE_WORD} at {point}")
    click(*point, 0.0)
    clicked = time.monotonic()
    if verbose:
        print(f"  hotbar tab II at {list(TAB_TWO)}")
    click(*TAB_TWO, 0.0)
    return clicked


def _state():
    try:
        with STATE_LOCK:
            known = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(known, dict):
        return {}
    if not known.get("server_dated"):
        for key in ("day", "off_day"):
            if known.get(key):
                known[key] = (datetime.date.fromisoformat(known[key])
                              + datetime.timedelta(days=1)).isoformat()
        _save(known)
    return known


def internet_now():
    import online_clock
    try:
        return online_clock.online_utc()
    except OSError:
        return None


def day_of(when):
    if when is None:
        return None
    return ((when.astimezone(RESET_ZONE) - RESET_HOUR).date()
            + datetime.timedelta(days=1)).isoformat()


def server_day():
    return day_of(internet_now())


def new_day(day):
    today = server_day()
    return day is not None and today is not None and today != day


def dungeon_open():
    now = internet_now()
    if now is None:
        return False
    since = (now.astimezone(RESET_ZONE) - RESET_HOUR).time()
    return datetime.timedelta(hours=since.hour, minutes=since.minute,
                              seconds=since.second) >= START_AFTER


def war_hold():
    import war
    at = war.now()
    if at is None:
        return None
    start = war.next_war(at, WAR_QUIET)
    if at + WAR_LEAD <= start:
        return None
    return tuple((when - war.SERVER_UTC_OFFSET).replace(
        tzinfo=datetime.timezone.utc).astimezone(RESET_ZONE)
        for when in (start - WAR_LEAD, start, start + WAR_QUIET))


def _save(state):
    state["server_dated"] = True
    keep_history(state)
    with STATE_LOCK:
        STATE.write_text(json.dumps(state), encoding="utf-8")


def keep_history(state):
    history = state.setdefault("history", {})
    ran = state.get("dungeon_ran") or {}
    won = state.get("dungeon_successful") or {}
    snaps = []
    if ran.get("day"):
        snaps.append((ran["day"], ran.get("counts") or {},
                      (won.get("counts") or {})
                      if won.get("day") == ran["day"] else {}))
    if state.get("day"):
        snaps.append((state["day"],
                      {f"CA{level}": count
                       for level, count in (state.get("ran") or {}).items()},
                      {f"CA{level}": count for level, count
                       in (state.get("successful") or {}).items()}))
    for day, entered, successful in snaps:
        kept = history.setdefault(day, {"entered": {}, "successful": {}})
        kept["entered"].update(entered)
        kept["successful"].update(successful)
    for day in sorted(history)[:-SUMMARY_DAYS]:
        del history[day]


def remember_active(level, started, due=None, deadline=None):
    now = internet_now()
    if now is None or started is None:
        return
    wait = BOSS_WAIT if due is None else max(0.0, due - time.monotonic())
    state = _state()
    state["active"] = {
        "level": level,
        "due": (now + datetime.timedelta(seconds=wait)).isoformat(),
        "ends": (started + TIME_LIMIT).isoformat()}
    if deadline is not None:
        state["active"]["deadline"] = (now + datetime.timedelta(
            seconds=deadline - time.monotonic())).isoformat()
    _save(state)


def forget_active():
    state = _state()
    if state.pop("active", None) is not None:
        _save(state)


def resume():
    active = _state().get("active")
    if not active:
        return None
    now = internet_now()
    if now is None:
        return None
    if now >= datetime.datetime.fromisoformat(active["ends"]):
        forget_active()
        off_for_the_day()
        return None
    level = int(active["level"])
    LOCK["level"] = level
    left = (datetime.datetime.fromisoformat(active["due"]) - now)
    due = time.monotonic() + left.total_seconds()
    deadline = (None if "deadline" not in active else time.monotonic() + (
        datetime.datetime.fromisoformat(active["deadline"])
        - now).total_seconds())
    sequence = DUNGEONS[TRADING_DUNGEON]["sequence"]
    ACTIVE.update(level=level, until=due + timer_lead(
        sequence[step_index(sequence, "wait_timer")]), deadline=deadline)
    return level, due


def ran_today(state, today, key="ran"):
    if today is not None and state.get("day") != today:
        return {}
    return {int(level): int(count)
            for level, count in (state.get(key) or {}).items()}


def next_level(today):
    state = _state()
    ran = ran_today(state, today)
    group = [level for level in ORDER
             if ran.get(level, 0) < LIMITS.get(level, 0)][:ROTATE_COUNT]
    if not group:
        return None
    last = state.get("last")
    if last in group:
        return group[(group.index(last) + 1) % len(group)]
    return group[0]


def count_entry(level, rotate, today):
    state = _state()
    ran = ran_today(state, today)
    ran[level] = ran.get(level, 0) + 1
    state["ran"] = {str(lv): count for lv, count in sorted(ran.items())}
    state["successful"] = {
        str(lv): count for lv, count
        in sorted(ran_today(state, today, "successful").items())}
    if today is not None:
        state["day"] = today
    if rotate:
        state.pop("next", None)
        state["last"] = level
    _save(state)
    return ran[level], next_level(today)


class NotAtStorm(RuntimeError):
    pass


def enter_dungeon(level, verbose=True):
    if not 1 <= level <= len(LEVEL_WINDOWS):
        raise RuntimeError(f"the dungeon list has no level {level} row.")
    point = centre(LEVEL_WINDOWS[level - 1])
    enter_point = centre(ENTER_WINDOW)
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    calibration.snap("storm_before")
    if verbose:
        print(f"  the blue storm at {list(STORM)}")
    click(*STORM, STORM_SETTLE)
    box = LEVEL_WINDOWS[level - 1]
    up, seen = wait_for_level(box, level, BUTTON_TIMEOUT)
    if not up:
        raise NotAtStorm(
            f"the level {level} row {list(box)} reads {seen!r}; the storm "
            f"click opened no dungeon list. Nothing more clicked.")
    if verbose:
        print(f"  level {level} at {point}")
    click(*point, SELECT_SETTLE)
    up, seen = wait_for_enter()
    if not up:
        raise RuntimeError(
            f"with level {level} selected the {ENTER_WORD} window "
            f"{list(ENTER_WINDOW)} reads {seen!r}, so no level {level} key "
            f"is held. Nothing entered.")
    if verbose:
        print(f"  {ENTER_WORD} at {enter_point}")
    click(*enter_point, ENTER_SETTLE)
    LOCK["level"] = level
    if verbose:
        print(f"  the boss to lock on: {BOSSES.get(str(level))}")


def arrive_at_gate(verbose=True, rotate=False):
    point = centre(CHALLENGE_WINDOW)
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    up, seen = wait_for(CHALLENGE_WINDOW, CHALLENGE_WORD, TRANSFER_TIMEOUT)
    if not up:
        raise RuntimeError(
            f"the {CHALLENGE_WORD} window {list(CHALLENGE_WINDOW)} reads "
            f"{seen!r}; the dungeon dialog is not open. Nothing clicked.")
    if verbose:
        print(f"  {CHALLENGE_WORD} at {point}")
    click(*point, CHALLENGE_SETTLE)
    started = internet_now()
    level = LOCK["level"]
    ran, after = count_entry(level, rotate, day_of(started))
    if verbose:
        print(f"  level {level}: {ran} of {LIMITS.get(level)} entered on "
              f"this server day"
              + (f"; the rotation goes on at level {after}" if rotate
                 else ""))
    move(ARRIVE, verbose)
    due = time.monotonic() + BOSS_WAIT
    remember_active(level, started)
    shout(verbose)
    return due


def step_index(steps, name):
    for index, step in enumerate(steps):
        if (step[0] if isinstance(step, list) else step) == name:
            return index
    raise RuntimeError(f"the {TRADING_DUNGEON} sequence has no {name} step.")


def timer_lead(step):
    return float(step[1]) if isinstance(step, list) and len(step) > 1 else 0.0


def level_location(level):
    for location, place in DUNGEONS[TRADING_DUNGEON]["locations"].items():
        found = LEVEL_TEXT.search(place["name"])
        if found and int(found.group(1)) == level:
            return location
    raise RuntimeError(f"no {TRADING_DUNGEON} location is level {level}.")


def dungeon_counts(state, today):
    ran = state.get("dungeon_ran") or {}
    if today is not None and ran.get("day") != today:
        return {}
    return dict(ran.get("counts") or {})


def runs_today(dungeon_name):
    return dungeon_counts(_state(), server_day()).get(dungeon_name, 0)


def successes_today(dungeon_name):
    won = _state().get("dungeon_successful") or {}
    return (int((won.get("counts") or {}).get(dungeon_name, 0))
            if won.get("day") == server_day() else 0)


def open_after(dungeon_name):
    hours, minutes = (int(part) for part in DUNGEONS[dungeon_name].get(
        "start_at", START_AT).split(":"))
    return ((datetime.timedelta(hours=hours, minutes=minutes) - RESET_HOUR)
            % datetime.timedelta(days=1))


def since_reset(now=None):
    now = internet_now() if now is None else now
    if now is None:
        return None
    shifted = now.astimezone(RESET_ZONE) - RESET_HOUR
    return datetime.timedelta(hours=shifted.hour, minutes=shifted.minute,
                              seconds=shifted.second)


def until_open(dungeon_name):
    since = since_reset()
    if since is None:
        return None
    return max(0.0, (open_after(dungeon_name) - since).total_seconds())


def until_next_day(dungeon_name):
    since = since_reset()
    if since is None:
        return None
    return (datetime.timedelta(days=1) - since
            + open_after(dungeon_name)).total_seconds()


def count_dungeon(dungeon_name, today):
    state = _state()
    counts = dungeon_counts(state, today)
    counts[dungeon_name] = counts.get(dungeon_name, 0) + 1
    state["dungeon_ran"] = {"day": today, "counts": counts}
    _save(state)
    return counts[dungeon_name]


def count_challenge(dungeon_name, location, timer, verbose=True):
    found = LEVEL_TEXT.search(location_of(dungeon_name, location)["name"])
    if found is None:
        limit = DUNGEONS[dungeon_name].get("daily_limit")
        if limit is not None:
            ran = count_dungeon(dungeon_name, day_of(timer.get("started")))
            if verbose:
                print(f"  {dungeon_name}: {ran} of {limit} entered on this "
                      f"dungeon day")
        return
    level, rotate = int(found.group(1)), timer.get("rotate", False)
    ran, after = count_entry(level, rotate, day_of(timer.get("started")))
    if verbose:
        print(f"  level {level}: {ran} of {LIMITS.get(level)} entered on "
              f"this server day"
              + (f"; the rotation goes on at level {after}" if rotate
                 else ""))


def count_success(dungeon_name, location, verbose=True):
    found = LEVEL_TEXT.search(location_of(dungeon_name, location)["name"])
    if found is None:
        state = _state()
        day = (state.get("dungeon_ran") or {}).get("day")
        won = state.get("dungeon_successful") or {}
        counts = dict(won.get("counts") or {}) if won.get("day") == day else {}
        counts[dungeon_name] = counts.get(dungeon_name, 0) + 1
        state["dungeon_successful"] = {"day": day, "counts": counts}
        _save(state)
        if verbose:
            print(f"  {dungeon_name}: {counts[dungeon_name]} of "
                  f"{dungeon_counts(state, day).get(dungeon_name, 0)} entered "
                  f"were successful on this dungeon day")
        return
    level, state = int(found.group(1)), _state()
    won = ran_today(state, None, "successful")
    won[level] = won.get(level, 0) + 1
    state["successful"] = {str(lv): count for lv, count in sorted(won.items())}
    _save(state)
    if verbose:
        print(f"  level {level}: {won[level]} of "
              f"{ran_today(state, None).get(level, 0)} entered were successful "
              f"on this server day")


def start(level, verbose=True, rotate=False):
    location = level_location(level)
    sequence = DUNGEONS[TRADING_DUNGEON]["sequence"]
    waits = step_index(sequence, "wait_timer")
    timer = {"until": None, "rotate": rotate}
    LOCK["level"] = level
    first = calibration.frames_taken() + 1
    run_dungeon(TRADING_DUNGEON, location, verbose, sequence[:waits], timer)
    started = timer.get("started")
    if END["overtime"] or END["defeated"] or END["quest_failed"]:
        ACTIVE.update(level=None, until=None, deadline=None, frames=[])
        return None
    if timer["until"] is None:
        raise RuntimeError(f"the {TRADING_DUNGEON} sequence starts no timer "
                           f"before its first wait_timer step.")
    lead = timer_lead(sequence[waits])
    due = timer["until"] - lead
    ACTIVE.update(level=level, until=timer["until"],
                  deadline=timer.get("deadline"),
                  frames=[(first, calibration.frames_taken())])
    remember_active(level, started, due, timer.get("deadline"))
    if verbose:
        print(f"  {location}: trading until {lead:g}s before the timer is "
              f"up, in {max(0.0, due - time.monotonic()):.0f}s")
    return due


def kill_and_finish(verbose=True):
    if ACTIVE["until"] is None:
        raise RuntimeError("no dungeon is waiting to be finished.")
    forget_active()
    sequence = DUNGEONS[TRADING_DUNGEON]["sequence"]
    run_dungeon(TRADING_DUNGEON, level_location(ACTIVE["level"]), verbose,
                sequence[step_index(sequence, "wait_timer"):],
                {"until": ACTIVE["until"], "deadline": ACTIVE["deadline"],
                 "frames": ACTIVE["frames"]})
    ACTIVE.update(level=None, until=None, deadline=None, frames=[])


def fight(verbose=True):
    wait_for_boss(arrive_at_gate(verbose), verbose)
    forget_active()
    LOCK.update(boss=None, cleared=False)
    move([("do", "kill_boss", 0.0)], verbose)
    finish_dungeon(verbose)


def finish_dungeon(verbose=True):
    if LOCK["reading"] is not None:
        LOCK["reading"].join()
    if verbose:
        print("  the dungeon is cleared" if LOCK["cleared"] else
              "  hit_boss ran out without the cleared banner")
    LOCK["cleared"] = False
    move(FINISH, verbose)


def wait_for_boss(due, verbose=True):
    if verbose:
        print(f"  the gate is broken; the boss phase is due in "
              f"{max(0.0, due - time.monotonic()):.0f}s")
    while time.monotonic() < due:
        time.sleep(min(WAIT_SLICE, max(0.0, due - time.monotonic())))
    if verbose:
        print(f"  {BOSS_WAIT:g}s since the gate broke: the boss phase is due")


def move(steps, verbose=True, frames=True):
    for action, key, seconds in steps:
        if LOCK["cleared"] or LOCK["killed"] or OVER.is_set():
            return
        if not focus_game():
            raise RuntimeError("the game is not in the foreground; the "
                               "movement stopped.")
        if action == "hold":
            if verbose:
                print(f"  hold {key} for {seconds:g}s")
            press(KEYS[key], hold=seconds)
        elif action == "tap":
            if verbose:
                print(f"  tap {key}, then wait {seconds:g}s")
            tap(KEYS[key])
            ENDED.wait(seconds)
        elif action == "use":
            if verbose:
                print(f"  {key} {slot_way(key)}, then wait {seconds:g}s")
            use_point(key, 0.0)
            ENDED.wait(seconds)
        elif action == "wait":
            if verbose:
                print(f"  wait {seconds:g}s")
            time.sleep(seconds)
        elif action == "target":
            if verbose:
                print(f"  target with {key}, then wait {seconds:g}s")
            if LOCK["boss"] is None:
                tap(KEYS[key])
            ENDED.wait(seconds)
            reading = LOCK["reading"]
            if not LOCK["watching"] and (reading is None
                                         or not reading.is_alive()):
                LOCK["reading"] = threading.Thread(
                    target=_read_target, args=(calibration.grab(), key),
                    daemon=True)
                LOCK["reading"].start()
        elif action == "inventory":
            prepare(verbose)
            time.sleep(seconds)
        elif action == "click":
            point = _DUNGEON[key]
            if verbose:
                print(f"  click {key} at {list(point)}, then wait {seconds:g}s")
            click(*point, seconds)
        elif action == "rightclick":
            point = _DUNGEON[key]
            if verbose:
                print(f"  right-click {key} at {list(point)}, park the "
                      f"cursor at {list(PARK)}, then wait {seconds:g}s")
            right_click(*point, 0.0)
            ctypes.windll.user32.SetCursorPos(*PARK)
            time.sleep(seconds)
        elif action == "button":
            press_button(key, seconds, verbose)
        elif action == "type":
            import recovery
            text = random.choice(_DUNGEON[key])
            if verbose:
                print(f"  type {text!r}, then wait {seconds:g}s")
            recovery._type(text)
            time.sleep(seconds)
        elif action == "do":
            if verbose:
                print(f"  {key}:")
            move([tuple(step) for step in _DUNGEON[key]], verbose, frames)
            time.sleep(seconds)
            continue
        elif action == "repeat":
            rounds, started = 0, time.monotonic()
            until = started + seconds
            while time.monotonic() < until and not LOCK["cleared"]:
                move([tuple(step) for step in _DUNGEON[key]], False, False)
                rounds += 1
                calibration.snap(f"repeat_{key}_{rounds}")
            if LOCK["reading"] is not None:
                LOCK["reading"].join()
            if verbose:
                print(f"  {key}: {rounds} round(s) in "
                      f"{time.monotonic() - started:.1f}s of {seconds:g}s")
            continue
        else:
            raise RuntimeError(f"unknown movement step {action!r}")
        if frames:
            calibration.snap(f"move_{action}_{key or ''}")


def trade_window_open():
    reads = read_windows(calibration.grab(),
                         [window for window, _word in TRADE_TABS])
    return any(row_model._key(word) in row_model._key(seen)
               for (_window, word), seen in zip(TRADE_TABS, reads))


def shout(verbose=True):
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    if trade_window_open():
        if verbose:
            print("  the Agent Shop is open; no shout while the shop trades")
        return False
    move([("do", "shout", 0.0)], verbose)
    return True


def calibrate(verbose=True):
    image = calibration.grab()
    calibration.snap("dungeon_calibrate", image)
    *rows, enter, challenge, leave = read_windows(
        image, LEVEL_WINDOWS + [ENTER_WINDOW, CHALLENGE_WINDOW, EXIT_WINDOW])
    if verbose:
        for box, text in ((ENTER_WINDOW, enter), (CHALLENGE_WINDOW, challenge),
                          (EXIT_WINDOW, leave)):
            print(f"  {list(box)} reads {text!r}")
    if enter.lower() == ENTER_WORD.lower():
        return _calibrate_list(rows, verbose)
    if (challenge.lower() == CHALLENGE_WORD.lower()
            and leave.lower() == EXIT_WORD.lower()):
        points = {"challenge": centre(CHALLENGE_WINDOW),
                  "exit": centre(EXIT_WINDOW)}
        calibration.remember("dungeon", points)
        if verbose:
            print(f"  {CHALLENGE_WORD} at {points['challenge']}")
            print(f"  {EXIT_WORD} at {points['exit']}")
        return points
    raise RuntimeError(
        f"neither the dungeon list ({ENTER_WORD}) nor the dungeon dialog "
        f"({CHALLENGE_WORD}, {EXIT_WORD}) is open. Nothing calibrated.")


def _calibrate_list(rows, verbose=True):
    levels = {}
    for box, text in zip(LEVEL_WINDOWS, rows):
        found = LEVEL_TEXT.search(text)
        if verbose:
            print(f"  {list(box)} reads {text!r}")
        if found is None or found.group(1) in levels:
            raise RuntimeError(
                f"the window {list(box)} reads {text!r}, which names no "
                f"level of its own. Nothing calibrated.")
        levels[found.group(1)] = centre(box)
    calibration.remember("dungeon", {"levels": levels,
                                     "enter": centre(ENTER_WINDOW)})
    if verbose:
        for level, point in levels.items():
            print(f"  level {level} at {point}")
        print(f"  {ENTER_WORD} at {centre(ENTER_WINDOW)}")
    return levels


def limit_reached(dungeon_name):
    limit = DUNGEONS[dungeon_name].get("daily_limit")
    return limit is not None and runs_today(dungeon_name) >= int(limit)


def level_left(level):
    return LIMITS.get(level, 0) - ran_today(_state(), server_day()).get(
        level, 0)


def disenchant_all(verbose=True, pops=None):
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    open_agent_shop_premium.ensure_inventory_open(verbose=verbose,
                                                  tesseract=False)
    if verbose:
        print(f"  Disenchant at {list(DISENCHANT_ICON)}")
    click(*DISENCHANT_ICON, SELECT_SETTLE)
    try:
        press_button("item_extraction", 0.0, verbose)
    except RuntimeError:
        pending = BUTTONS["extraction_pending"]
        up, _seen = wait_for(tuple(pending["window"]), pending["word"], 0)
        if not up:
            raise
        calibration.snap("extraction_pending")
        if verbose:
            print(f"  the {pending['word']} window still holds an earlier "
                  f"extraction's items: Receive All, Close, then Disenchant "
                  f"again")
        press_button("extraction_receive", SELECT_SETTLE, verbose)
        press_button("extraction_pending_close", SELECT_SETTLE, verbose,
                     required=False)
        click(*DISENCHANT_ICON, SELECT_SETTLE)
        press_button("item_extraction", 0.0, verbose)
    agree = BUTTONS["extraction_agree"]
    up, seen = wait_for(tuple(agree["window"]), agree["word"], BUTTON_TIMEOUT)
    if up:
        press_button("extraction_agree", 0.0, verbose)
        ctypes.windll.user32.SetCursorPos(*TAB_TWO)
        if pops:
            if verbose:
                print(f"  {DISENCHANT_WAIT:g}s for the extraction, right-"
                      f"clicking {' -> '.join(pops)} {LOOP_GAP:g}s apart "
                      f"meanwhile")
            bm_aura(False, pops, DISENCHANT_WAIT)
        else:
            wait_timer(DISENCHANT_WAIT, verbose)
        calibration.park()
        press_button("extraction_close", 0.0, verbose)
    else:
        if verbose:
            print(f"  no {agree['word']!r} within {BUTTON_TIMEOUT:g}s of "
                  f"Item Extraction, so nothing is left to extract")
        press_button("extraction_cancel", 0.0, verbose)
    if verbose:
        print(f"  Sort all inventory at {list(SORT_ICON)}")
    click(*SORT_ICON, SELECT_SETTLE)


def get_to_wb_area(verbose=True):
    if WB_AREA_CHECK is None:
        return wb_area_route(verbose)
    tries = int(WB_AREA_CHECK["tries"])
    window, word = tuple(WB_AREA_CHECK["window"]), WB_AREA_CHECK["word"]
    for attempt in range(1, tries + 1):
        try:
            if not wb_area_route(verbose):
                return False
            if verbose:
                print("  every window closed before the minimap is read")
            escape_everything(verbose)
            up, seen = wait_for(window, word, float(WB_AREA_CHECK["timeout"]))
            why = (f"the minimap {list(window)} reads {seen!r}, not "
                   f"{word!r}")
        except RuntimeError as exc:
            up, why = False, f"the route failed: {exc}"
        if up:
            if verbose:
                print(f"  at the world boss stage: the minimap reads {seen!r}")
            return True
        calibration.snap("wb_area_missed")
        print(f"  not at the world boss stage: {why} (try {attempt} of "
              f"{tries}); the default state, then the route again")
        calibration.close_everything(verbose=verbose)
    raise RuntimeError(f"the world boss stage was not reached in {tries} "
                       f"tries.")


def wb_area_route(verbose=True):
    escape_everything(verbose)
    level = next((level for level in sorted(LIMITS) if level_left(level) > 0),
                 None)
    if level is None:
        if verbose:
            print("  world boss area: no CA level has an entry left today, "
                  "so the route there is skipped")
        return False
    location = level_location(level)
    place = location_of(TRADING_DUNGEON, location)
    if verbose:
        print(f"  world boss area: {location} has {level_left(level)} "
              f"entr(ies) left today; in, then out by {EXIT_WORD}")
    with_entry_retries(lambda attempt: get_entry(
        *place["entry"], verbose=verbose), verbose)
    with_entry_retries(lambda attempt: buy_and_open(
        TRADING_DUNGEON, location, place, attempt, verbose), verbose)
    up, seen = wait_for(EXIT_WINDOW, EXIT_WORD, TRANSFER_TIMEOUT)
    if not up:
        raise RuntimeError(
            f"no {EXIT_WORD!r} in the dungeon window {list(EXIT_WINDOW)} "
            f"within {TRANSFER_TIMEOUT:g}s; it reads {seen!r}. Nothing "
            f"clicked.")
    point = centre(EXIT_WINDOW)
    if verbose:
        print(f"  {EXIT_WORD} at {point}, not {CHALLENGE_WORD}")
    click(*point, 0.0)
    wait_timer(WB_AREA_WAIT, verbose)
    if verbose:
        print("  every window closed and off the bike before the walk")
    escape_everything(verbose)
    off_bike(f"before {WB_AREA_SEQUENCE}", verbose)
    play(TRADING_DUNGEON, WB_AREA_SEQUENCE, verbose)
    return True


def world_boss_on():
    return bool((_SHARED.get("world_boss") or {}).get("enabled"))


def world_boss_turn(dungeon_name, verbose=True):
    if not world_boss_on():
        return False
    import recovery
    import world_boss
    try:
        now = world_boss.online_utc()
    except RuntimeError as exc:
        if verbose:
            print(f"  world boss: {exc}")
        return False
    when = world_boss.next_spawn(now)
    left = (when - now).total_seconds()
    room = world_boss.TRADING_LEAD
    if dungeon_name is not None:
        room += (hard_limit(dungeon_name)
                 + float(setting(dungeon_name, "cooldown")))
    if WORLD_BOSS["done"] == when or left > room:
        return False
    WORLD_BOSS["done"] = when
    print("")
    print(f"WORLD BOSS: {when:%H:%M:%S %Z} is {left:.0f}s away, inside the "
          + (f"{room:g}s lead; the idle ends here" if dungeon_name is None
             else f"{room:g}s a {dungeon_name} run and the lead need; "
                  f"the dungeons stop here")
          + " and the world boss run starts")
    if LIVE["view"] is not None:
        LIVE["view"].boss(f"{when:{VIEW_SET['boss_clock']}}")
    failed = None
    calibration.close_everything(verbose=verbose)
    try:
        if not get_to_wb_area(verbose):
            print("WORLD BOSS: skipped, there is no way to the world boss "
                  "area today; " + ("back to the idle" if dungeon_name is None
                                    else "the dungeons go on"))
            if LIVE["view"] is not None:
                LIVE["view"].boss_end("skipped")
            return False
        world_boss.run(verbose=verbose, when=when, prepare_now=True)
    except Exception as exc:
        calibration.snap("world_boss_failed")
        failed = f"{type(exc).__name__}: {exc}"
        print(f"  WORLD BOSS failed: {failed}")
        if recovery.logged_out():
            with relogging():
                recover_retrying(lambda: recovery.recover(verbose=verbose),
                                 verbose)
    world_boss.pause(world_boss.RESUME_WAIT)
    calibration.close_everything(verbose=verbose)
    cancel_bm(dungeon_name or TRADING_DUNGEON, verbose)
    print("WORLD BOSS: over; " + ("back to the idle" if dungeon_name is None
                                   else "the dungeons go on"))
    if LIVE["view"] is not None:
        LIVE["view"].boss_end("fail" if failed else "pass", failed or "")
    return True


def report_at():
    from profit_summary import central_now
    return central_now().strftime("%H:%M:%S")


class LiveView:
    def __init__(self, stream):
        self.stream, self.lock = stream, threading.RLock()
        self.run, self.shown, self.closed = None, False, threading.Event()
        threading.Thread(target=self.tick, daemon=True).start()

    @staticmethod
    def open(stream):
        import record_action
        if not stream.isatty() or not record_action.console_colours():
            return None
        return LiveView(stream)

    def paint(self, colour, text):
        return f"{chr(27)}[{VIEW_SET['colours'][colour]}m{text}{chr(27)}[0m"

    def boss_segments(self, verdict, why):
        run, look = self.run, VIEW_SET
        words = {"pass": "boss_pass", "fail": "boss_fail",
                 "skipped": "boss_skipped", "stopped": "stopped"}
        width = max(len(look[key]) for key in ["boss_running",
                                               *words.values()])
        state, colour = ((look[words[verdict]],
                          "quiet" if verdict == "skipped" else verdict)
                         if verdict else (look["boss_running"], None))
        took = (run["ended"] or time.monotonic()) - run["started"]
        out = [(run["name"], "name"), (look["gap"] + run["when"], None),
               (look["spacing"] + f"{state:<{width}}", colour),
               (look["spacing"]
                + time.strftime(look["clock"], time.gmtime(took)), "quiet")]
        if why:
            out.append((look["spacing"] + why, "quiet"))
        return out

    def segments(self, verdict, why):
        if self.run.get("boss"):
            return self.boss_segments(verdict, why)
        run, look = self.run, VIEW_SET
        reached, rooms = run["reached"], run["rooms"]
        moving = run.get("moving")
        here = (rooms.index(moving) if moving in rooms
                else reached if moving is not None else reached - 1)
        number = f"#{run['number']}" + (f"/{run['limit']}" if run["limit"]
                                        else "")
        wide = (len(f"#{run['limit']}/{run['limit']}") if run["limit"]
                else len(number))
        out = [(f"{run['name']:<{look['name_width']}}", "name"),
               (f"{look['gap']}{number:<{wide}}", None)]
        if rooms:
            out.append((look["spacing"], None))
        for at in range(len(rooms)):
            if at:
                out.append((look["gap"], None))
            if verdict == "pass" or at < here:
                out.append((look["done"], "done"))
            elif at == here:
                out.append((look["left"] if moving is not None and not verdict
                            else look["current"], verdict or "current"))
            else:
                out.append((look["left"], "left"))
        states = [look[key] for key in ("entering", "opening", "pass", "fail",
                                        "stopped")]
        states += [f"{look[key]} {room}" for room in rooms
                   for key in ("room", "moving")]
        if verdict is not None:
            state, colour = look[verdict], verdict
        elif run["started"] is None:
            state, colour = look["entering"], "quiet"
        elif moving is not None:
            state, colour = f"{look['moving']} {moving}", None
        elif reached == 0:
            state, colour = look["opening"], None
        else:
            state, colour = f"{look['room']} {rooms[reached - 1]}", None
        out += [(look["spacing"], None),
                (f"{state:<{max(len(text) for text in states)}}", colour)]
        if run["started"] is not None:
            took = (run["ended"] or time.monotonic()) - run["started"]
            out.append((look["spacing"]
                        + time.strftime(look["clock"], time.gmtime(took)),
                        "quiet"))
        if why:
            out.append((look["spacing"] + why, "quiet"))
        return out

    def draw(self, verdict=None, why=""):
        parts = self.segments(verdict, why)
        plain = "".join(text for text, _colour in parts)
        columns = shutil.get_terminal_size().columns
        if why and len(plain) >= columns:
            keep = columns - len(plain) + len(why) - len(VIEW_SET["cut"]) - 1
            return self.draw(verdict, why[:keep] + VIEW_SET["cut"]
                             if keep > 0 else "")
        self.stream.write(f"\r{chr(27)}[2K" + "".join(
            self.paint(colour, text) if colour else text
            for text, colour in parts))
        self.stream.flush()
        self.shown = True

    def clear(self):
        if self.shown:
            self.stream.write(f"\r{chr(27)}[2K")
            self.stream.flush()
            self.shown = False

    def finish(self, verdict, why=""):
        self.draw(verdict, why)
        self.stream.write("\n")
        self.stream.flush()
        self.shown, self.run = False, None

    def prepare(self, name, number, limit, rooms):
        with self.lock:
            self.clear()
            self.run = {"name": name, "number": number, "limit": limit,
                        "rooms": rooms, "reached": 0, "started": None,
                        "ended": None}
            self.draw()

    def start(self, name, number, limit, rooms, clicked):
        with self.lock:
            if self.run is None or self.run["name"] != name:
                self.run = {"name": name, "limit": limit, "rooms": rooms}
            self.run.update(number=number, reached=0, moving=None,
                            started=clicked, ended=None)
            self.draw()

    def moving(self, room):
        with self.lock:
            if self.run is None:
                return
            self.run["moving"] = room
            self.draw()

    def room(self, room):
        with self.lock:
            if self.run is None:
                return
            rooms = self.run["rooms"]
            self.run["reached"] = (rooms.index(room) + 1 if room in rooms
                                   else self.run["reached"] + 1)
            self.run["moving"] = None
            self.draw()

    def result(self, result, ended):
        with self.lock:
            if self.run is None:
                return
            verdict, _comma, why = result.partition(", ")
            self.run["ended"] = ended if self.run["started"] else None
            self.finish("pass" if verdict == "pass" else "fail", why)

    def line(self, text):
        with self.lock:
            self.clear()
            self.stream.write(text + "\n")
            self.stream.flush()
            if self.run is not None:
                self.draw()

    def unique(self, item):
        self.line(self.paint("unique", f"{VIEW_SET['unique']} {item}"))

    def rotation(self, parts, done):
        look = VIEW_SET
        out = [(look["rotation"], "quiet")]
        for name, runs, limit, wins, complete in parts:
            text = f"{name} {runs}/{limit}" + (
                "" if wins is None else f" ({wins} {look['successful']})")
            out += [(look["spacing"], None), (text, "done" if complete else None)]
        if done:
            out += [(look["spacing"], None), (look["all_done"], "pass")]
        self.line("".join(self.paint(colour, text) if colour else text
                          for text, colour in out))

    def boss(self, when):
        with self.lock:
            self.clear()
            self.run = {"boss": True, "name": VIEW_SET["boss"], "when": when,
                        "started": time.monotonic(), "ended": None}
            self.draw()

    def boss_end(self, verdict, why=""):
        with self.lock:
            if self.run is None or not self.run.get("boss"):
                return
            self.run["ended"] = time.monotonic()
            self.finish(verdict, why)

    def tick(self):
        while not self.closed.wait(float(VIEW_SET["tick"])):
            with self.lock:
                if (self.run is not None and self.run["started"] is not None
                        and self.shown):
                    self.draw()

    def close(self):
        with self.lock:
            self.closed.set()
            if self.run is not None and self.run["started"] is not None:
                self.finish("stopped")
            else:
                self.clear()


class ConsoleErrors:
    def __init__(self, stream, view):
        self.stream, self.view = stream, view

    def write(self, text):
        with self.view.lock:
            self.view.clear()
            self.stream.write(text)
            self.stream.flush()
            if text.endswith("\n") and self.view.run is not None:
                self.view.draw()
        return len(text)

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return self.stream.isatty()

    def fileno(self):
        return self.stream.fileno()


class ConsoleLines:
    def __init__(self, stream, view=None):
        self.stream, self.part, self.view = stream, "", view

    def write(self, text):
        self.part += text
        *done, self.part = self.part.split("\n")
        for line in done:
            if self.view is not None and any(pattern.search(line)
                                             for pattern in VIEW_LIVE):
                continue
            if any(pattern.search(line) for pattern in CONSOLE_LINES):
                if self.view is not None:
                    self.view.line(line)
                    continue
                self.stream.write(line + "\n")
                self.stream.flush()
        return len(text)

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return self.stream.isatty()

    def fileno(self):
        return self.stream.fileno()


def running_commit():
    def git(*args):
        return subprocess.run(["git", *args], cwd=str(calibration.HERE.parent),
                              text=True, capture_output=True,
                              timeout=REPORT["tool_timeout"]).stdout
    try:
        sha = git("log", "-1", "--format=%h").strip()
        COMMIT["sha"] = sha or None
        changed = [line[3:].strip() for line in git(
            "status", "--porcelain", "--untracked-files=no", "--",
            calibration.HERE.name).splitlines()]
    except Exception as exc:
        return f"unknown ({type(exc).__name__}: {exc})"
    changed = [path for path in changed
               if os.path.basename(path) not in UNCOMMITTED_IGNORE]
    return sha + (f"; uncommitted: {', '.join(changed)}" if changed else "")


def dungeon_report():
    state, today = _state(), server_day()
    entered = dungeon_counts(state, today)
    won = state.get("dungeon_successful") or {}
    won = dict(won.get("counts") or {}) if won.get("day") == today else {}
    names = ROTATION_NAMES + [name for name in DUNGEONS
                              if name not in ROTATION_NAMES
                              and name != TRADING_DUNGEON
                              and entered.get(name)]
    rows = [(name.upper(),
             next(iter(DUNGEONS[name]["locations"].values()))["name"],
             int(entered.get(name, 0)), int(won.get(name, 0)),
             int(DUNGEONS[name]["daily_limit"]))
            for name in names if DUNGEONS[name].get("daily_limit") is not None]
    ran, wins = ran_today(state, today), ran_today(state, today, "successful")
    for level in sorted(LIMITS) if ORDER else []:
        place = location_of(TRADING_DUNGEON, level_location(level))
        rows.append((f"CA{level}", place["name"], ran.get(level, 0),
                     wins.get(level, 0), LIMITS[level]))
    totals = ("total", "", sum(row[2] for row in rows),
              sum(row[3] for row in rows), sum(row[4] for row in rows))
    named = max(len(row[1]) for row in rows + [totals])
    head = (f"{'dungeon':<9}{'name':<{named + 2}}{'attempts':>9}"
            f"{'successful':>12}{'daily limit':>13}{'left':>6}")
    week = week_lines(state, today)
    lines = [f"DUNGEON REPORT -- {calibration.config_name()}, server day "
             f"{today}, written {report_at()}", "", *unique_lines(today), "",
             *(week + [""] if week else []), *jewel_lines(today), "",
             f"Today DP gained: {dp_gained(won)}, from successful runs only",
             "", head]
    for short, name, tries, good, cap in rows + [totals]:
        lines.append(f"{short:<9}{name:<{named + 2}}{tries:>9}{good:>12}"
                     f"{cap:>13}{cap - tries:>6}")
    from profit_summary import world_boss_lines
    bosses = world_boss_lines()
    if bosses:
        lines += ["", "", *bosses]
    return "\n".join(lines) + "\n"


def write_dungeon_report():
    path = (calibration.HERE.parent / REPORT["report_dir"]
            / calibration.config_name() / REPORT["dungeon_report_name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dungeon_report(), encoding="utf-8")
    return path


def push_dungeon_report(path):
    root = calibration.HERE.parent
    rel = path.relative_to(root).as_posix()
    branch = REPORT["report_branch"]
    index = calibration.LOG_DIR / REPORT["dungeon_report_index"]

    def git(*args, env=None):
        return subprocess.run(["git", *args], cwd=str(root), text=True,
                              capture_output=True,
                              timeout=REPORT["tool_timeout"], env=env)

    for attempt in range(1, int(REPORT["report_tries"]) + 1):
        if git("fetch", "origin", branch).returncode:
            return "fetch failed"
        index.unlink(missing_ok=True)
        env = dict(os.environ, GIT_INDEX_FILE=str(index))
        if git("read-tree", f"origin/{branch}", env=env).returncode:
            return f"origin/{branch} would not read"
        blob = git("hash-object", "-w", str(path))
        if blob.returncode:
            return f"{rel} would not hash"
        added = git("update-index", "--add", "--cacheinfo",
                    f"{REPORT['report_mode']},{blob.stdout.strip()},{rel}",
                    env=env)
        if added.returncode:
            return added.stderr.strip()[:REPORT["reason_width"]]
        tree = git("write-tree", env=env)
        index.unlink(missing_ok=True)
        if tree.returncode:
            return "the tree would not write"
        if tree.stdout.strip() == git(
                "rev-parse", f"origin/{branch}^{{tree}}").stdout.strip():
            return "no change"
        made = git("commit-tree", tree.stdout.strip(), "-p",
                   git("rev-parse", f"origin/{branch}").stdout.strip(), "-m",
                   REPORT["dungeon_report_message"].format(
                       config=calibration.config_name(), at=report_at()))
        if made.returncode:
            return "the commit would not build"
        sent = git("push", "origin", f"{made.stdout.strip()}:{branch}")
        if not sent.returncode:
            return None
        if attempt == int(REPORT["report_tries"]):
            return sent.stderr.strip()[:REPORT["reason_width"]]
    return "gave up"


def push_report(verbose=True):
    try:
        path = write_dungeon_report()
        failed = push_dungeon_report(path)
    except Exception as exc:
        failed = f"{type(exc).__name__}: {exc}"[:REPORT["reason_width"]]
    if verbose:
        print(f"  dungeon report: "
              + ("pushed" if failed is None else f"not pushed ({failed})"))


def report_loop(verbose=True):
    every = float(REPORT["report_every"])
    while True:
        due = time.monotonic() + every
        push_report(verbose)
        time.sleep(max(0.0, due - time.monotonic()))


def start_reports(verbose=True):
    threading.Thread(target=report_loop, args=(verbose,), daemon=True).start()


def idle(verbose=True):
    import world_boss
    day, told = None, None
    while True:
        try:
            now = world_boss.online_utc()
        except RuntimeError as exc:
            if told != str(exc):
                print(f"  idle: {exc}")
                told = str(exc)
            wait_timer(world_boss.ONLINE_EVERY, False)
            continue
        day = day_of(now) if day is None else day
        if day_of(now) != day:
            print(f"idle over: server day {day_of(now)} has begun, so the "
                  f"dungeons have runs again")
            return
        if world_boss_turn(None, verbose):
            continue
        wake = (datetime.timedelta(days=1)
                - since_reset(now)).total_seconds()
        target = "the server-day reset"
        if world_boss_on():
            when = world_boss.next_spawn(now)
            boss = (when - now).total_seconds()
            if WORLD_BOSS["done"] != when:
                boss -= world_boss.TRADING_LEAD
            if boss < wake:
                wake = boss
                target = f"the world boss turn for {when:%H:%M:%S %Z}"
        at = (now + datetime.timedelta(seconds=wake)).astimezone(
            world_boss.ZONE)
        line = f"idle: nothing left to run; {target} at {at:%H:%M:%S %Z}"
        if line != told:
            print(f"{line}, {wake:.0f}s from now")
            told = line
        wait_timer(max(0.0, min(wake, float(REPORT["report_every"]))), False)


def online_now():
    import world_boss
    return world_boss.online_utc()


def pick_channel(moment, regions, night, choice):
    start, end = ((int(hhmm[:2]) * 60 + int(hhmm[3:])) for hhmm in night)
    day = 24 * 60
    at_night, distance = [], {}
    for region in regions:
        local = moment.astimezone(zoneinfo.ZoneInfo(region["timezone"]))
        minute = local.hour * 60 + local.minute
        if start <= minute < end:
            at_night.append(region)
        distance[region["name"]] = min((start - minute) % day, (minute - end) % day)
    chosen = at_night or [min(regions, key=lambda region: distance[region["name"]])]
    channels = [channel for region in chosen for channel in region["channels"]]
    return [region["name"] for region in chosen], choice(channels)


def relog_for_burst(verbose=True):
    import recovery
    if CHANNEL is not None:
        if BURST_STATE["on_channel"]:
            print(f"burst: staying on Channel {CHANNEL} (--channel)")
            return
        print(f"burst: --channel {CHANNEL}, so Channel {CHANNEL} for the "
              f"whole launch; relogging there")
        try:
            recovery.relog(verbose=verbose, channel=CHANNEL)
        except Exception as exc:
            print(f"  the relog into Channel {CHANNEL} failed "
                  f"({type(exc).__name__}: {exc}); tried again at the next "
                  f"burst")
            recover_retrying(lambda: recovery.recover(verbose=verbose,
                                                      channel=CHANNEL),
                             verbose, CHANNEL)
            return
        BURST_STATE["on_channel"] = True
        return
    try:
        now = online_now()
    except RuntimeError:
        print("  no time server has answered yet, so the burst stays on this "
              "channel")
        return
    names, channel = pick_channel(now, BURST["regions"], BURST["night"],
                                  random.choice)
    print(f"burst: {' + '.join(names)} night, so Channel {channel}; relogging there")
    try:
        recovery.relog(verbose=verbose, channel=channel)
        return
    except Exception as exc:
        print(f"  the relog into Channel {channel} failed ({type(exc).__name__}: {exc})")
    others = [other for region in BURST["regions"] if region["name"] in names
              for other in region["channels"] if other != channel]
    random.shuffle(others)
    for other in others:
        print(f"  Channel {other} instead, from the server list")
        try:
            recovery.recover(verbose=verbose, channel=other)
            return
        except Exception as exc:
            print(f"  Channel {other} failed too ({type(exc).__name__}: {exc})")
    print(f"  back to the home channel {recovery.HOME_CHANNEL}")
    recover_retrying(lambda: recovery.recover(verbose=verbose), verbose)


def start_burst(verbose=True):
    minutes = random.uniform(*(float(m) for m in BURST["run_minutes"]))
    BURST_STATE["until"] = time.monotonic() + minutes * 60
    if verbose:
        print(f"burst: {minutes:.1f} min of dungeons from now")


def burst_over():
    return (BURST is not None and BURST_STATE["until"] is not None
            and time.monotonic() >= BURST_STATE["until"])


def character_window_open():
    spec = BURST["character_window"]
    return wait_for(tuple(spec["window"]), spec["word"], 1.0)[0]


def open_character_window(verbose=True):
    if not BURST.get("character_window"):
        return
    press(int(BURST["character_window"]["key"]))
    if verbose:
        print("  C for the Character window: "
              + ("open" if character_window_open() else "not seen open"))


def close_character_window(verbose=True):
    if not BURST.get("character_window") or not character_window_open():
        return
    press(int(BURST["character_window"]["key"]))
    if verbose:
        print("  C again: the Character window is closed")


def rest_between_bursts(verbose=True):
    minutes = random.uniform(*(float(m) for m in BURST["rest_minutes"]))
    print(f"burst over; resting {minutes:.1f} min at the world boss stage, "
          f"checking for the world boss meanwhile")
    try:
        calibration.close_everything(verbose=verbose)
        get_to_wb_area(verbose)
    except RuntimeError as exc:
        print(f"  world boss stage not reached: {exc}; resting where it stands")
    open_character_window(verbose)
    end = time.monotonic() + minutes * 60
    every = float(BURST.get("check_every", 30))
    while time.monotonic() < end:
        if world_boss_turn(None, verbose):
            open_character_window(verbose)
        wait_timer(min(every, max(0.0, end - time.monotonic())), False)
    close_character_window(verbose)
    BURST_STATE["until"] = None


@contextlib.contextmanager
def relogging():
    RELOGGING.set()
    try:
        yield
    finally:
        RELOGGING.clear()


def offline_now():
    import recovery
    try:
        image = calibration.grab()
        words = calibration.ocr(image, recovery._box_frac(recovery.POPUP_F))
        panel = calibration.ocr(image,
                                recovery._box_frac(recovery.LOGIN_PANEL_F))
        return bool(recovery.disconnected(words=words)
                    or recovery.failed_to_connect(words=words)
                    or recovery._find(recovery.LOGIN_WORD, whole=True,
                                      words=panel))
    except Exception as exc:
        print(f"  the disconnect check could not read the screen "
              f"({type(exc).__name__}: {exc})")
        return False


def watch_disconnect(verbose=True):
    def watch():
        while True:
            time.sleep(DISCONNECT_GAP)
            if (RELOGGING.is_set() or calibration.OFFLINE.is_set()
                    or not offline_now()):
                continue
            calibration.snap("disconnect_seen")
            print(f"  the disconnect notice or the login screen is up; "
                  f"looking again in {DISCONNECT_RECHECK:g}s")
            time.sleep(DISCONNECT_RECHECK)
            if RELOGGING.is_set() or not offline_now():
                print("  it is gone again; nothing done")
                continue
            calibration.snap("disconnect_confirmed")
            print(f"  still disconnected {DISCONNECT_RECHECK:g}s on: every "
                  f"action stops and no more input is sent")
            OVER.set()
            ENDED.set()
            calibration.OFFLINE.set()

    threading.Thread(target=watch, daemon=True).start()
    if verbose:
        print(f"  the screen is checked for a disconnect every "
              f"{DISCONNECT_GAP:g}s and again {DISCONNECT_RECHECK:g}s after a "
              f"sighting, as trading's supervisor does")


def relog_after_disconnect(dungeon_name, exc, verbose=True):
    import recovery
    calibration.OFFLINE.clear()
    ENDED.clear()
    OVER.clear()
    calibration.snap("dungeon_disconnected")
    print(f"  {exc}: a relog into {recovery.HOME_CHANNEL}, then "
          + (f"the next dungeon after {dungeon_name}" if dungeon_name
             else "the loop goes on"))
    forget_active()
    with relogging():
        recover_retrying(lambda: recovery.recover(verbose=verbose), verbose)
    cancel_bm(dungeon_name or TRADING_DUNGEON, verbose)


def arm_death_watch(dungeon_name, verbose=True):
    if not DEATH_WATCH["on"] or not setting(dungeon_name, "death_watch"):
        return
    DEATH_WATCH["armed"] = True
    if verbose:
        print(f"  out of {dungeon_name}: the death {DEFEAT_SIGN['word']} is "
              f"looked for every {DEATH_WATCH_EVERY:g}s until the next "
              f"dungeon is entered")


def watch_death(verbose=True):
    window, word = tuple(DEFEAT_SIGN["window"]), DEFEAT_SIGN["word"]

    def watch():
        told = set()
        while True:
            time.sleep(DEATH_WATCH_EVERY)
            if (not DEATH_WATCH["armed"] or RELOGGING.is_set()
                    or calibration.OFFLINE.is_set()
                    or calibration.DEFEATED.is_set()):
                continue
            try:
                up, _seen = wait_for(window, word, 0)
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                if reason not in told:
                    told.add(reason)
                    print(f"  the death check could not read the screen "
                          f"({reason})")
                continue
            if not up:
                continue
            calibration.snap("dead_between_dungeons")
            with DEATH_LOCK:
                if not DEATH_WATCH["armed"] or RELOGGING.is_set():
                    continue
                print(f"  the death {word} is up between dungeons: every "
                      f"action stops and no more input is sent")
                calibration.DEFEATED.set()

    DEATH_WATCH["on"] = True
    threading.Thread(target=watch, daemon=True).start()
    if verbose:
        watched = [name for name in DUNGEONS if setting(name, "death_watch")]
        print(f"  from each {', '.join(watched)} exit until the next dungeon "
              f"is entered, the death {word} in {list(window)} is looked for "
              f"every {DEATH_WATCH_EVERY:g}s")


def dead_between_dungeons():
    return DEATH_WATCH["armed"] and wait_for(
        tuple(DEFEAT_SIGN["window"]), DEFEAT_SIGN["word"], 0)[0]


def relog_after_death(dungeon_name, exc, verbose=True):
    import recovery
    with DEATH_LOCK:
        DEATH_WATCH["armed"] = False
        calibration.DEFEATED.clear()
    DEATH_WATCH["again"] = (dungeon_name is not None
                            and not DEATH_WATCH["entered"])
    calibration.snap("dungeon_dead")
    print(f"  {exc}: the revive {DEFEAT_SIGN['word']}, a relog into "
          f"{recovery.HOME_CHANNEL}, then "
          + (f"{dungeon_name} again" if DEATH_WATCH["again"]
             else "the loop goes on"))
    forget_active()
    revive_if_dead(verbose)
    relog(verbose)
    cancel_bm(dungeon_name or TRADING_DUNGEON, verbose)
    DEATH_WATCH["armed"] = True


def run_seconds(dungeon_name):
    return float(DUNGEONS[dungeon_name].get("run_seconds",
                                            hard_limit(dungeon_name)))


def banner_wait(now, dungeon_name):
    start = now.replace(minute=int(BANNER["minute"]), second=0, microsecond=0)
    length = datetime.timedelta(minutes=float(BANNER["minutes"]))
    if now >= start + length:
        start += datetime.timedelta(hours=1)
    run = datetime.timedelta(seconds=float(BANNER["entry_allowance"])
                             + run_seconds(dungeon_name))
    left = (0.0 if now + run <= start
            else (start + length - now).total_seconds())
    return left, start, length, run


def run_cost(dungeon_name):
    return (float(BANNER["entry_allowance"]) + run_seconds(dungeon_name)
            + float(BANNER["exit_allowance"]))


def banner_plan(now, start, pool, depth):
    best = ([], (start - now).total_seconds())
    if depth <= 0:
        return best
    for other in pool:
        if banner_wait(now, other)[0]:
            continue
        rest, idle = banner_plan(
            now + datetime.timedelta(seconds=run_cost(other)), start, pool,
            depth - 1)
        if (idle, -len(rest) - 1) < (best[1], -len(best[0])):
            best = ([other] + rest, idle)
    return best


def banner_filler(dungeon_name, pool, verbose=True):
    if NO_BANNER or dungeon_name in BANNER_SAFE:
        return None
    try:
        now = online_now()
    except RuntimeError:
        return None
    left, start, _length, _run = banner_wait(now, dungeon_name)
    if not left or now >= start:
        return None
    plan, idle = banner_plan(now, start, [other for other in pool
                                          if not limit_reached(other)],
                             int(BANNER["plan_depth"]))
    if not plan:
        return None
    if verbose:
        print(f"{dungeon_name}: a run started now would still be on when the "
              f"Surprise Attack banner comes up at :{start:%M}, so "
              f"{' -> '.join(plan)} fill the time before it ({idle:.0f}s left "
              f"over); {plan[0]} now")
    return plan[0]


def banner_fill(dungeon_name, verbose=True):
    if NO_BANNER or not BANNER_FILL or dungeon_name in BANNER_SAFE:
        return None
    fill = BANNER_FILL["dungeon"]
    if limit_reached(fill):
        return None
    try:
        now = online_now()
    except RuntimeError:
        return None
    left, start, length, _run = banner_wait(now, dungeon_name)
    if not left:
        return None
    if BANNER_FILLS["window"] != start:
        BANNER_FILLS.update(window=start, done=0)
    if BANNER_FILLS["done"] >= int(BANNER_FILL["runs"]):
        return None
    BANNER_FILLS["done"] += 1
    if verbose:
        print(f"{dungeon_name}: the Surprise Attack banner keeps it out until "
              f":{start + length:%M} ({left:.0f}s), so {fill}, whose "
              f"recordings stay clear of the banner, runs in the wait "
              f"({BANNER_FILLS['done']} of {BANNER_FILL['runs']} for this "
              f"banner)")
    return fill


def banner_clear(dungeon_name, verbose=True):
    if NO_BANNER:
        return
    try:
        now = online_now()
    except RuntimeError:
        if verbose:
            print("  no time server has answered yet, so the Surprise Attack "
                  "banner hour is not checked before this run")
        return
    left, start, length, run = banner_wait(now, dungeon_name)
    if not left:
        return
    if dungeon_name in BANNER_SAFE:
        if verbose:
            print(f"  {dungeon_name}: its recordings stay clear of the "
                  f"Surprise Attack banner, so it runs now without waiting "
                  f"for :{start + length:%M}")
        return
    if verbose:
        print(f"  {dungeon_name}: the Surprise Attack banner is up from "
              f":{start:%M} to :{start + length:%M} every hour and a run "
              f"started now could last {run.total_seconds():.0f}s, so no "
              f"dungeon until :{start + length:%M} ({left:.0f}s)")
    wait_timer(left, False)


def run_or_relog(dungeon_name, location, after, verbose=True):
    DEATH_WATCH["again"] = False
    try:
        return run_in_turn(dungeon_name, location, after, verbose), False
    except calibration.Offline as exc:
        relog_after_disconnect(dungeon_name, exc, verbose)
        return None, True
    except calibration.Defeated as exc:
        relog_after_death(dungeon_name, exc, verbose)
        return None, False
    except RuntimeError as exc:
        if not dead_between_dungeons():
            raise
        relog_after_death(dungeon_name, exc, verbose)
        return None, False


def run_in_turn(dungeon_name, location, after, verbose=True):
    import recovery
    RUN_OUTCOME["failed"] = False
    DEATH_WATCH["entered"] = False
    if burst_over():
        if NO_BREAKS:
            print("burst over; no breaks, so straight on"
                  + (f", still on Channel {CHANNEL}" if CHANNEL is not None
                     else " to the next night channel"))
            BURST_STATE["until"] = None
        else:
            rest_between_bursts(verbose)
        after = None
    if BURST is not None and BURST_STATE["until"] is None:
        if BURST.get("regions"):
            with relogging():
                relog_for_burst(verbose)
        start_burst(verbose)
    if world_boss_turn(dungeon_name, verbose):
        after = (dungeon_name, time.monotonic())
    revive_if_dead(verbose)
    banner_clear(dungeon_name, verbose)
    pre_run_buys(dungeon_name, location, verbose)
    powerful_pot_due(dungeon_name, verbose)
    if after is None:
        start_block(dungeon_name, location, verbose)
    COOLDOWN["after"] = (after if after is not None or BM_OFF["at"] is None
                         else (dungeon_name, BM_OFF["at"]))
    if DEATH is not None and dead():
        wait_for_respawn(verbose)
        return None
    try:
        run_dungeon(dungeon_name, location, verbose)
    except RuntimeError as exc:
        if dead_between_dungeons():
            raise calibration.Defeated(f"{exc} The death "
                                       f"{DEFEAT_SIGN['word']} is up") from exc
        if not recovery.logged_out():
            if DEATH is None:
                raise
            survive_failed_run(dungeon_name, exc, verbose)
            return None
        raise calibration.Offline(f"{exc} The game is logged out") from exc
    if RUN_OUTCOME["failed"] and DEATH is not None:
        count_failure(dungeon_name, "the run failed (past its limit, no end "
                      "screen or defeated)", verbose)
    else:
        FAILED_RUNS["in_a_row"] = 0
    return dungeon_name, bm_off_at()


def hp_now():
    text = read_windows(calibration.grab(), [tuple(DEATH["hp_window"])])[0]
    found = HP_TEXT.search((text or "").replace(" ", ""))
    if not found:
        return None, text
    now, most = int(found.group(1)), int(found.group(2))
    if most <= 0 or now > most:
        return None, text
    return now, text


def dead():
    first, _text = hp_now()
    if first != 0:
        return False
    again, _text = hp_now()
    return again == 0


def wait_for_respawn(verbose=True):
    if DEATH_WATCH["armed"]:
        raise calibration.Defeated("the HP bar reads 0 between dungeons")
    calibration.snap("dead")
    if verbose:
        print("  the HP bar reads 0: the character is dead, so the revive "
              "Confirmation")
    press_buttons("defeat", DEFEAT, verbose)
    if verbose:
        print(f"  {float(DEATH['settle']):g}s for the town to load, then the "
              f"default state and the next run")
    wait_timer(float(DEATH["settle"]), False)
    calibration.close_everything(verbose=verbose)


def count_failure(dungeon_name, why, verbose=True):
    FAILED_RUNS["in_a_row"] += 1
    most = int(DEATH["failed_in_a_row"])
    if FAILED_RUNS["in_a_row"] >= most:
        raise RuntimeError(f"{most} runs in a row failed; the last: {why}")
    if verbose:
        print(f"  {dungeon_name}: {FAILED_RUNS['in_a_row']} of {most} "
              f"failures in a row")
    return most


def survive_failed_run(dungeon_name, exc, verbose=True):
    calibration.snap("run_failed")
    if dead():
        if verbose:
            print(f"  {dungeon_name}: {exc}")
        wait_for_respawn(verbose)
        count_failure(dungeon_name, f"died: {exc}", verbose)
        return
    most = count_failure(dungeon_name, exc, False)
    if verbose:
        print(f"  {dungeon_name}: {exc} Not dead and not logged out: the "
              f"default state, then the next run "
              f"({FAILED_RUNS['in_a_row']} of {most} failures in a row)")
    box_up = dismiss_quest_failed(verbose)
    calibration.close_everything(verbose=verbose)
    location = RUN_OUTCOME["inside"]
    if location is None:
        return
    if verbose:
        print(f"  {dungeon_name} {location}: the run failed after the "
              f"{CHALLENGE_WORD} click and never left; leaving by hand, as at "
              f"the time limit")
    ENDED.clear()
    run_sequence(dungeon_name, location, None, verbose,
                 setting(dungeon_name, "before_exit"), False)
    if not box_up:
        run_sequence(dungeon_name, location, None, verbose,
                     manual_exit(dungeon_name), False)
    RUN_OUTCOME["inside"] = None
    arm_death_watch(dungeon_name, verbose)
    run_sequence(dungeon_name, location, None, verbose,
                 DUNGEONS[dungeon_name].get("after_exit", []), False)


def dismiss_quest_failed(verbose=True, wait=0.0):
    box = BUTTONS["quest_failed"]
    up, _seen = wait_for(tuple(box["window"]), box["word"], wait)
    if not up:
        return False
    calibration.snap("quest_failed")
    if verbose:
        print("  'Quest Dungeon Failed' is up and blocks every other window: "
              "its OK first; the game then moves the character out")
    try:
        press_button("quest_failed_ok", float(LEAVE["settle"]), verbose)
    except RuntimeError:
        point = centre(tuple(BUTTONS["quest_failed_ok"]["window"]))
        if verbose:
            print(f"  its OK did not read; clicked where it sits, {point}")
        click(*point, float(LEAVE["settle"]))
    return True


def rotation_done(dungeon_name, successful):
    return limit_reached(dungeon_name) or (
        successful is not None
        and successes_today(dungeon_name) >= int(successful))


def rotate(names, after=None, verbose=True, group=None, pool=None,
           day=None):
    group = {} if group is None else group
    pool = names if pool is None else pool
    batch = group.get("batch", ROTATION_RUNS)
    successful = group.get("successful")
    while True:
        if new_day(day):
            return after
        turn = [name for name in names
                if not rotation_done(name, successful)]
        if verbose:
            print(f"rotation {' -> '.join(names)}: "
                  + ", ".join(f"{name} {runs_today(name)} of "
                              f"{DUNGEONS[name]['daily_limit']}"
                              + ("" if successful is None
                                 else f" ({successes_today(name)} of "
                                      f"{successful} successful)")
                              for name in names)
                  + ("" if turn
                     else "; every daily limit is reached"
                     if successful is None
                     else f"; each has {successful} successful or is at "
                          f"its daily limit"))
        if LIVE["view"] is not None:
            LIVE["view"].rotation(
                [(name, runs_today(name), DUNGEONS[name]["daily_limit"],
                  None if successful is None else successes_today(name),
                  rotation_done(name, successful)) for name in names],
                not turn)
        if not turn:
            return after
        for name in turn:
            runs = (int(DUNGEONS[name]["daily_limit"])
                    if DUNGEONS[name].get("all_in_turn") else batch)
            done = 0
            while runs is None or done < int(runs):
                if new_day(day):
                    return after
                if rotation_done(name, successful):
                    break
                filler = banner_filler(name, pool, verbose)
                if filler is None:
                    filler = banner_fill(name, verbose)
                if filler is not None:
                    after, _relogged = run_or_relog(
                        filler, only_location(filler), after, verbose)
                    continue
                after, relogged = run_or_relog(name, only_location(name),
                                               after, verbose)
                if DEATH_WATCH["again"]:
                    continue
                done += 1
                if relogged:
                    break
                if RUN_OUTCOME["failed"]:
                    if verbose:
                        print(f"{name}: the run failed, so the next dungeon "
                              f"in the list goes next")
                    break


def start_home(verbose=True):
    if BURST is not None and BURST.get("regions"):
        if verbose:
            print("the loop starts with the burst's own relog, through the "
                  "server list, before the first run")
        return
    if verbose:
        print("the loop starts with a full relog, out to the server list and "
              "into the home channel")
    forget_active()
    relog(verbose)
    cancel_bm(TRADING_DUNGEON, verbose)


def run_all(verbose=True):
    after = None
    start_reports(verbose)
    watch_disconnect(verbose)
    watch_death(verbose)
    start_home(verbose)
    while True:
        day = server_day()
        for group in ROTATION_GROUPS:
            after = rotate(group["dungeons"], after, verbose, group,
                           ROTATION_NAMES, day)
            if new_day(day):
                break
        for level in ORDER:
            if new_day(day):
                break
            location = level_location(level)
            while level_left(level) > 0 and not new_day(day):
                if verbose:
                    print(f"{location}: "
                          f"{LIMITS[level] - level_left(level)} of "
                          f"{LIMITS[level]} entered on this server day; "
                          f"the next one idles through its wait")
                after, relogged = run_or_relog(TRADING_DUNGEON, location,
                                               after, verbose)
                if relogged:
                    break
            if verbose and not new_day(day):
                print(f"{location}: {LIMITS[level]} of {LIMITS[level]} "
                      f"entered on this server day")
        if new_day(day):
            if verbose:
                print(f"server day {server_day()} has begun: the daily limits "
                      f"are reset, so the rotation starts again from its "
                      f"first group")
            continue
        if verbose:
            print("every dungeon in the loop is at its daily limit")
        try:
            idle(verbose)
        except calibration.Offline as exc:
            relog_after_disconnect(None, exc, verbose)
        except calibration.Defeated as exc:
            relog_after_death(None, exc, verbose)
        after = None


def command(args):
    if not args:
        run_all()
    elif args[:1] == ["calibrate"]:
        calibrate()
    elif args[:1] == ["enter"] and len(args) > 1:
        enter_dungeon(int(args[1]))
    elif args[:1] == ["arrive"]:
        fight()
    elif args[:1] == ["buy"] and len(args) > 1:
        prepare()
        buy_key(int(args[1]))
    elif args[:1] == ["get_entry"] and len(args) > 2:
        get_entry(int(args[1]), int(args[2]))
    elif args[:1] == ["open_dungeon_key"] and len(args) > 2:
        open_dungeon_key(args[1], args[2])
    elif args[:1] == ["start_dungeon"]:
        start_dungeon()
    elif args[:1] == ["play"] and len(args) > 2:
        play(args[1], args[2])
    elif args[:1] == ["kill"] and len(args) > 2:
        name = " ".join(args[2:])
        kill(name, target_words(args[1], name))
    elif args[:1] == ["bm_aura"]:
        bm_aura()
    elif args[:1] == ["kill_bm"] and len(args) > 2:
        name = " ".join(args[2:])
        kill_bm(name, target_words(args[1], name))
    elif args[:1] == ["run"] and len(args) > 1:
        due = start(int(args[1]))
        if due is not None:
            wait_for_boss(due)
            kill_and_finish()
    elif args[:1] == ["do"] and len(args) > 1:
        move([("do", args[1], 0.0)])
    elif args[:1] == ["shout"]:
        shout()
    elif args[:1] == ["disenchant"]:
        disenchant_all()
    elif args[:1] == ["close_notification"]:
        close_notification()
    elif args[:1] == ["off_bike"]:
        off_bike("by hand")
    elif args[:1] == ["exit"] and len(args) > 1:
        exit_now(args[1])
    elif args[:1] == ["get_to_wb_area"]:
        calibration.close_everything(verbose=True)
        get_to_wb_area()
    elif len(args) > 1 and all(
            DUNGEONS.get(arg, {}).get("daily_limit") is not None
            for arg in args):
        watch_disconnect()
        watch_death()
        rotate(args)
    elif args[:1] and args[0] in DUNGEONS and _from_step() is not None:
        location = args[1] if len(args) > 1 else only_location(args[0])
        skipped = before_step(args[0], _from_step())
        timer = {"until": None, "limit": hard_limit(args[0]),
                 "started": internet_now()}
        timer["deadline"] = time.monotonic() + timer["limit"]
        print(f"{args[0]} {location}: from {_from_step()}, as if "
              f"{CHALLENGE_WORD} was just clicked; {len(skipped)} step(s) "
              f"before it are skipped and the dungeon has {timer['limit']:g}s "
              f"from now")
        run_dungeon(args[0], location,
                    steps=DUNGEONS[args[0]]["sequence"][len(skipped):],
                    timer=timer)
    elif args[:1] and args[0] in DUNGEONS and _record_from() is not None:
        location = args[1] if len(args) > 1 else only_location(args[0])
        step = _record_from()
        before_step(args[0], step)
        pre_run_buys(args[0], location)
        start_block(args[0], location)
        if run_dungeon(args[0], location, until=step) is not False:
            record_from_here(step)
    elif args[:1] and args[0] in DUNGEONS:
        location = args[1] if len(args) > 1 else only_location(args[0])
        rested = None
        for run in range(_runs()):
            revive_if_dead()
            banner_clear(args[0])
            pre_run_buys(args[0], location)
            if not limit_reached(args[0]):
                powerful_pot_due(args[0])
            if run == 0:
                start_block(args[0], location)
            COOLDOWN["after"] = (None if rested is None
                                 else (args[0], rested))
            if run_dungeon(args[0], location) is False:
                break
            rested = bm_off_at()
    else:
        raise SystemExit(f"unknown command {' '.join(args)!r}; use "
                         f"nothing for the whole loop, "
                         f"DUNGEON [LOCATION] ({', '.join(sorted(DUNGEONS))}), "
                         f"DUNGEON --record-from STEP to play up to STEP and "
                         f"then record, "
                         f"DUNGEON DUNGEON ... in turn until each daily "
                         f"limit, "
                         f"run N, buy N, get_entry X Y, "
                         f"open_dungeon_key DUNGEON LOCATION, start_dungeon, "
                         f"play DUNGEON SEQUENCE, kill DUNGEON NAME, bm_aura, "
                         f"kill_bm DUNGEON NAME, "
                         f"enter N, arrive, "
                         f"calibrate, shout, disenchant, close_notification, "
                         f"off_bike, exit DUNGEON, get_to_wb_area "
                         f"or do ACTION")


if __name__ == "__main__":
    args = _plain_argv()
    row_model.start_backup_reader()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")
    calibration.log_to_file("dungeon")
    LIVE["view"] = LiveView.open(sys.stdout.stream)
    sys.stdout.stream = ConsoleLines(sys.stdout.stream, LIVE["view"])
    if LIVE["view"] is not None:
        sys.stderr.stream = ConsoleErrors(sys.stderr.stream, LIVE["view"])
    print(f"running on commit {running_commit()}")
    if NO_BANNER:
        print("  --no-banner: the Surprise Attack banner hour is ignored; a "
              "run starts at any time")
    calibration.frames_on(True if "--frames" in sys.argv else None)
    calibration.watch_for_stop()
    try:
        command(args)
    finally:
        calibration.frames_written()
        if LIVE["view"] is not None:
            LIVE["view"].close()
