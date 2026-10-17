import ctypes
import datetime
import zoneinfo
import json
import os
import random
import re
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
        if arg.startswith("--config=") or arg == "--frames":
            continue
        out.append(arg)
    return out


if __name__ == "__main__":
    os.environ["CABAL_CONFIG"] = _chosen_config()

from PIL import Image, ImageChops

import calibration
import open_agent_shop_premium
import row_model
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
TARGET_TEXT_MIN = int(_DUNGEON["target_text_min"])
BOSSES = _DUNGEON["bosses"]
ORDER = [int(level) for level in _DUNGEON["order"]]
ROTATE_COUNT = int(_DUNGEON["rotate_count"])
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
sys.path.append(str(calibration.HERE.parent / "tools"))
CLEARED_WINDOW = tuple(_DUNGEON["cleared_window"])
CLEARED_TEXT = re.compile(_DUNGEON["cleared_pattern"], re.IGNORECASE)
BUTTONS = _DUNGEON["buttons"]
BUTTON_TIMEOUT = float(_SHARED["timing"]["dialog_timeout"])
FINISH = [tuple(step) for step in _DUNGEON["finish"]]
DUNGEONS = _DUNGEON["dungeons"]
TRADING_DUNGEON = _DUNGEON["trading_dungeon"]
ACTIVE = {"level": None, "until": None, "deadline": None}
TAB_TWO = tuple(_DUNGEON["tab_two"])
TRADE_TABS = [(tuple(tab["window"]), tab["word"])
              for tab in _DUNGEON["trade_tabs"]]
SEQUENCES = calibration.HERE / _DUNGEON["sequences_dir"]
TARGET_KEY = _DUNGEON["kill_target_key"]
KILL_COMBO = [tuple(step) for step in _DUNGEON["kill_combo"]]
MAIN_BM = tuple(_DUNGEON["main_bm"])
AURA = tuple(_DUNGEON["aura"])
BM_POT = tuple(_DUNGEON["bm_pot"])
OFFENSIVE_BUFFS = {name: tuple(point)
                   for name, point in _DUNGEON["offensive_buffs"].items()}
DEFENSIVE = "defensive_buffs"
DEFENSIVE_BUFFS = [(name, tuple(point))
                   for name, point in _DUNGEON[DEFENSIVE].items()]
DEFENSIVE_SWITCH = float(_DUNGEON["defensive_switch"])
LOOP_GAP = float(_DUNGEON["loop_gap"])
USE_GAP = float(_DUNGEON["use_gap"])
BM_LOOP = list(_DUNGEON["bm_loop"])
BM_AURA = list(_DUNGEON["bm_aura"])
BM_AURA_FOR = float(_DUNGEON["bm_aura_for"])
LOOP_POINTS = {"main_bm": MAIN_BM, "aura": AURA, "bm_pot": BM_POT,
               **OFFENSIVE_BUFFS}
SLOTS = {name: tuple(point) for name, point in _DUNGEON["slots"].items()}
HEAL_KEY = _DUNGEON["heal_key"]
HEAL_EVERY = float(_DUNGEON["heal_every"])
END_WAIT = float(_DUNGEON["end_wait"])
END_TIMEOUT = float(_DUNGEON["end_timeout"])
OVER = threading.Event()
END = {"at": None, "defeated": False, "overtime": False}
EXIT = [tuple(step) for step in _DUNGEON["exit"]]
DEFEAT = [tuple(step) for step in _DUNGEON["defeat"]]
DEFEAT_SIGN = BUTTONS[DEFEAT[0][1]]
CHAT_OPEN = float(_DUNGEON["chat_open"])
TARGET_EVERY = float(_DUNGEON["target_every"])
KEY_LOCK = threading.Lock()


def tap(vk, hold=None):
    with KEY_LOCK:
        if hold is None:
            press(vk)
        else:
            press(vk, hold=hold)
KEY_CELLS = [tuple(cell) for cell in _DUNGEON["key_cells"]]
KEY_WINDOW = tuple(_DUNGEON["key_window"])
KEY_TEXT = re.compile(_DUNGEON["key_pattern"], re.IGNORECASE)
BUY_SETTLE = float(_DUNGEON["buy_settle"])
TRANSFER_TIMEOUT = float(_DUNGEON["transfer_timeout"])
INVENTORY_TAB = int(_DUNGEON["inventory_tab"])
KEY_TAB = int(_DUNGEON["key_tab"])
LOCK = {"boss": None, "reading": None, "cleared": False, "level": ORDER[0],
        "killed": False, "watching": False, "reads": 0}
ENDED = threading.Event()


def click(x, y, settle):
    calibration._button(_INPUT["MOUSEEVENTF_LEFTDOWN"],
                        _INPUT["MOUSEEVENTF_LEFTUP"], x, y, settle)
    calibration.snap(f"click_{x}_{y}")


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
        raise RuntimeError("the Paddle reader did not answer; nothing read.")
    return [(text or "").strip() for text in texts]


def read_windows(image, windows):
    return read_crops([scaled(image.crop(box)) for box in windows])


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


def press_button(name, settle, verbose=True):
    button = BUTTONS[name]
    window = tuple(button["window"])
    up, seen = wait_for(window, button["word"], BUTTON_TIMEOUT)
    if not up:
        raise RuntimeError(
            f"the {name} window {list(window)} never read {button['word']!r} "
            f"within {BUTTON_TIMEOUT:g}s; it reads {seen!r}. Nothing clicked.")
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
    recovery.relog(verbose=verbose)


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
    if wait_for(tuple(tab["window"]), tab["word"], 0)[0]:
        raise RuntimeError("the shop is still open after N.")
    if verbose:
        print("  bought; N closed the shop")
    if len(landed) != 1:
        raise RuntimeError(
            f"tab {KEY_TAB} gained {landed or 'no slot'} with the purchase, "
            f"not one entry. Nothing noted.")
    entry = note_entry(landed[0])
    if verbose:
        print(f"  the entry landed in tab {KEY_TAB} slot {landed[0]} at "
              f"{entry['point']}; noted in {STATE.name}")
    return entry


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


def find_in_list(name, timeout):
    deadline = time.monotonic() + timeout
    while True:
        image = calibration.grab()
        shown = read_windows(image, [ENTER_WINDOW])[0]
        reads = None
        if row_model._key(ENTER_WORD) in row_model._key(shown):
            reads = read_windows(image, LEVEL_WINDOWS)
            for box, seen in zip(LEVEL_WINDOWS, reads):
                if names(seen, name):
                    calibration.snap("dungeon_list", image)
                    return box, reads, shown
        if time.monotonic() >= deadline:
            calibration.snap("no_dungeon_list", image)
            return None, reads, shown
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


def open_dungeon_key(dungeon_name, location, verbose=True):
    name = location_of(dungeon_name, location)["name"]
    entry = _state().get("entry")
    if not entry:
        raise RuntimeError(f"no entry is noted in {STATE.name}; get_entry "
                           f"notes one when it buys. Nothing clicked.")
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    yes = BUTTONS["warp_yes"]
    ctypes.windll.user32.SetCursorPos(*TAB_TWO)
    box, reads, shown = find_in_list(name, 0)
    if box is None:
        if not wait_for(tuple(yes["window"]), yes["word"], 0)[0]:
            prepare(verbose, entry["tab"])
            if verbose:
                print(f"  right-click the entry in tab {entry['tab']} slot "
                      f"{tuple(entry['slot'])} at {entry['point']}")
            right_click(*entry["point"], 0.0)
        press_button("warp_yes", 0.0, verbose)
        ctypes.windll.user32.SetCursorPos(*TAB_TWO)
        box, reads, shown = find_in_list(name, TRANSFER_TIMEOUT)
        if box is None:
            seen = (f"its rows read {reads}" if reads is not None else
                    f"the {ENTER_WORD} window {list(ENTER_WINDOW)} reads "
                    f"{shown!r}, so the list never opened")
            raise RuntimeError(
                f"no {name!r} in the dungeon list within "
                f"{TRANSFER_TIMEOUT:g}s of {yes['word']}, read with the cursor "
                f"on tab II {list(TAB_TWO)}; {seen}. Nothing more clicked.")
    point = centre(box)
    if verbose:
        print(f"  with the cursor on tab II {list(TAB_TWO)} the dungeon list "
              f"reads {reads}")
        print(f"  {location} {name!r} at {point}")
    click(*point, 0.0)
    up, seen = wait_for(ENTER_WINDOW, ENTER_WORD, BUTTON_TIMEOUT)
    if not up:
        raise RuntimeError(
            f"with {location} selected the {ENTER_WORD} window "
            f"{list(ENTER_WINDOW)} reads {seen!r}. Nothing entered.")
    enter_point = centre(ENTER_WINDOW)
    if verbose:
        print(f"  {ENTER_WORD} at {enter_point}")
    click(*enter_point, 0.0)
    forget_entry()
    if verbose:
        print(f"  entered {location}; the entry note is cleared")


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
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    begin = head.get("cursor") or next(
        ([event["x"], event["y"]] for event in events if "x" in event), None)
    if begin is not None:
        begin = [min(max(begin[0], 0), width - 1),
                 min(max(begin[1], 0), height - 1)]
        ctypes.windll.user32.SetCursorPos(*begin)
    if verbose:
        print(f"  {dungeon_name}/{path.name}: {len(events)} events over "
              f"{events[-1]['t']:.1f}s, the cursor first put at {begin}"
              + (" (where the recording began)" if head.get("cursor")
                 else " (the recording's first mouse point)"))
    keys, buttons, played = {}, {}, 0
    record_action.winmm.timeBeginPeriod(record_action.TIMER_MS)
    zero = time.perf_counter()
    try:
        for event in events:
            record_action.wait_until(zero + event["t"], ENDED)
            if ENDED.is_set() or calibration.STOPPED.is_set():
                break
            if event["kind"] == "key":
                up = event["action"] == "up"
                record_action.send(record_action.key_input(event, up))
                if up:
                    keys.pop(event["vk"], None)
                else:
                    keys[event["vk"]] = event
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
        for event in list(keys.values()):
            record_action.send(record_action.key_input(event, True))
        for button in list(buttons):
            record_action.send(_mouse_input(
                record_action.BUTTON_FLAGS[("up", button)],
                record_action.X_DATA.get(button, 0)))
        record_action.winmm.timeEndPeriod(record_action.TIMER_MS)
    calibration.snap(f"played_{sequence}")
    if verbose:
        print(f"  played {played} of {len(events)} events; nothing is left "
              f"held down")


def _mouse_input(flags, data=0):
    import record_action
    return record_action.INPUT(
        type=record_action.W["INPUT_MOUSE"],
        u=record_action.INPUTUNION(mi=record_action.MOUSEINPUT(
            0, 0, ctypes.c_ulong(data).value, flags, 0, 0)))


def watch_target(name, words, stop, first, failed):
    try:
        while not stop.is_set():
            image = calibration.grab()
            seen = read_crops([white_text(image, TARGET_WINDOW)])[0]
            LOCK["reads"] += 1
            first.set()
            hits = [word for word in words
                    if word.casefold() in seen.casefold()]
            if hits and LOCK["boss"] is None:
                LOCK["boss"] = name
                calibration.snap("target_found", image)
                print(f"  the HP bar reads {seen!r}: {name} is targeted "
                      f"({', '.join(hits)}), so no more auto target")
            elif not hits and LOCK["boss"] is not None:
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


def hunt(name, words, attack, label, plan, verbose=True):
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    limit = TIME_LIMIT.total_seconds()
    LOCK.update(boss=None, killed=False, watching=True, reads=0)
    ENDED.clear()
    stop, first, failed = threading.Event(), threading.Event(), {}
    watcher = threading.Thread(target=watch_target,
                               args=(name, words, stop, first, failed),
                               daemon=True)
    if verbose:
        print(f"  {label} {name!r}: the HP bar is read all the time; "
              f"{TARGET_KEY} until it shows {' or '.join(map(repr, words))}, "
              f"then {plan} over and over, stopped the moment none of them "
              f"shows")
    presses, rounds, started = 0, 0, time.monotonic()
    watcher.start()
    try:
        first.wait(limit)
        while (LOCK["boss"] is None and not ENDED.is_set()
               and time.monotonic() - started < limit):
            if not focus_game():
                raise RuntimeError(f"the game is not in the foreground; the "
                                   f"{label} stopped.")
            tap(KEYS[TARGET_KEY])
            presses += 1
            pressed_at = LOCK["reads"]
            while (LOCK["reads"] < pressed_at + 2 and LOCK["boss"] is None
                   and not ENDED.is_set()):
                ENDED.wait(WAIT_SLICE)
        if verbose:
            print(f"  {presses} {TARGET_KEY} press(es) before {name!r} was "
                  f"targeted" if LOCK["boss"] is not None else
                  f"  {presses} {TARGET_KEY} press(es); {name!r} was never "
                  f"targeted")
        since = time.monotonic()
        while not ENDED.is_set() and time.monotonic() - started < limit:
            attack(since)
            rounds += 1
            calibration.snap(f"{label}_{rounds}")
    finally:
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
    if not killed:
        raise RuntimeError(f"{name!r} was not killed in {rounds} round(s) "
                           f"over {took:.0f}s. Stopped.")
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


def bm_aura(verbose=True):
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    if verbose:
        print(f"  {' -> '.join(BM_AURA)} right-clicked {LOOP_GAP:g}s apart, "
              f"over and over for {BM_AURA_FOR:g}s")
    started = time.monotonic()
    rounds = 0
    while time.monotonic() < started + BM_AURA_FOR and not ENDED.is_set():
        click_round(BM_AURA, started)
        rounds += 1
    if verbose:
        print(f"  {rounds} round(s) of {' -> '.join(BM_AURA)} in "
              f"{time.monotonic() - started:.1f}s")


def click_round(names, since):
    for entry in names:
        if ENDED.is_set():
            return
        if not focus_game():
            raise RuntimeError("the game is not in the foreground; the "
                               "right-click loop stopped.")
        started = time.monotonic()
        if entry == DEFENSIVE:
            entry, point = DEFENSIVE_BUFFS[
                int((started - since) // DEFENSIVE_SWITCH)
                % len(DEFENSIVE_BUFFS)]
        else:
            point = LOOP_POINTS[entry]
        right_click(*point, 0.0)
        ENDED.wait(max(0.0, started + LOOP_GAP - time.monotonic()))


def loop_plan(names):
    defensive = "/".join(name for name, _point in DEFENSIVE_BUFFS)
    return (" -> ".join(f"{defensive} (switching every {DEFENSIVE_SWITCH:g}s)"
                        if entry == DEFENSIVE else entry for entry in names)
            + f" right-clicked {LOOP_GAP:g}s apart")


def kill_bm(name, words, verbose=True, loop=None):
    loop = BM_LOOP if loop is None else loop
    start = next((at for at, entry in enumerate(loop)
                  if entry not in BM_AURA), 0)
    rounds = {"next": loop[start:]}

    def attack(since):
        click_round(rounds["next"], since)
        rounds["next"] = loop

    return hunt(name, words, attack, "kill_bm",
                f"{loop_plan(loop)}, the first round from {loop[start]}",
                verbose)


def heal_loop(stop):
    while (not stop.is_set() and not OVER.is_set()
           and not calibration.STOPPED.is_set()):
        tap(KEYS[HEAL_KEY])
        stop.wait(HEAL_EVERY)


def watch_end(stop, failed, killing, timer, verbose=True):
    try:
        while not stop.is_set() and not calibration.STOPPED.is_set():
            if timer.get("deadline") is None:
                stop.wait(WAIT_SLICE)
                continue
            image = calibration.grab()
            defeat, *banner = read_windows(
                image, [tuple(DEFEAT_SIGN["window"])]
                + ([CLEARED_WINDOW] if killing.is_set() else []))
            if banner and CLEARED_TEXT.search(banner[0]):
                banner = banner[0]
                END["at"] = time.monotonic()
                OVER.set()
                ENDED.set()
                calibration.snap("dungeon_end", image)
                if verbose:
                    print(f"  {banner!r}: the dungeon is cleared; every action "
                          f"stops now, exit in {END_WAIT:g}s")
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
        print(f"  {HARD_LIMIT:g}s since {CHALLENGE_WORD} and the dungeon is "
              f"not finished; every action stops now, then the manual exit")
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


def run_dungeon(dungeon_name, location, verbose=True, steps=None,
                timer=None):
    timer = {"until": None} if timer is None else timer
    stop, failed, killing = threading.Event(), {}, threading.Event()
    phase = {"killing": killing,
             "healer": threading.Thread(target=heal_loop, args=(stop,),
                                        daemon=True),
             "ender": threading.Thread(target=watch_end,
                                       args=(stop, failed, killing, timer,
                                             verbose),
                                       daemon=True),
             "limit": threading.Thread(target=watch_limit,
                                       args=(stop, timer, verbose),
                                       daemon=True)}
    OVER.clear()
    END["at"], END["defeated"], END["overtime"] = None, False, False
    try:
        if not overtime(timer, verbose):
            phase["limit"].start()
        phase["ender"].start()
        run_sequence(dungeon_name, location, phase, verbose, steps,
                     timer=timer)
        if killing.is_set() and not OVER.is_set():
            if verbose:
                print(f"  the sequence is done; {TARGET_KEY} every "
                      f"{TARGET_EVERY:g}s until the dungeon end screen, up to "
                      f"{END_TIMEOUT:g}s")
            until, presses = time.monotonic() + END_TIMEOUT, 0
            while not OVER.is_set():
                calibration.halt_if_stopped()
                if time.monotonic() >= until:
                    break
                if not focus_game():
                    raise RuntimeError("the game is not in the foreground; "
                                       "the wait for the end screen stopped.")
                tap(KEYS[TARGET_KEY])
                presses += 1
                OVER.wait(TARGET_EVERY)
            if verbose:
                print(f"  {presses} {TARGET_KEY} press(es) after the last "
                      f"step")
        if failed:
            raise RuntimeError(f"the dungeon end screen could not be read: "
                               f"{failed['error']}. Stopped.")
        stop.set()
        for thread in (phase["healer"], phase["ender"], phase["limit"]):
            if thread.is_alive():
                thread.join()
        if END["overtime"]:
            manual = DUNGEONS[dungeon_name].get("manual_exit")
            if not manual:
                raise RuntimeError(f"the dungeon ran past its {HARD_LIMIT:g}s "
                                   f"and has no manual exit.")
            ENDED.clear()
            run_sequence(dungeon_name, location, phase, verbose, manual, False)
            off_for_the_day(verbose)
            if verbose:
                print(f"  {dungeon_name} {location}: past its {HARD_LIMIT:g}s; "
                      f"left it by hand")
        elif END["defeated"]:
            manual = DUNGEONS[dungeon_name].get("manual_exit")
            if not manual:
                raise RuntimeError("the dungeon is failed and has no manual "
                                   "exit.")
            press_buttons("defeat", DEFEAT, verbose)
            ENDED.clear()
            run_sequence(dungeon_name, location, phase, verbose, manual, False)
            if verbose:
                print(f"  {dungeon_name} {location}: the dungeon is failed; "
                      f"left it by hand")
        elif END["at"] is not None:
            while time.monotonic() < END["at"] + END_WAIT:
                calibration.halt_if_stopped()
                time.sleep(min(WAIT_SLICE,
                               max(0.0, END["at"] + END_WAIT
                                   - time.monotonic())))
            exit_dungeon(verbose)
            ENDED.clear()
            run_sequence(dungeon_name, location, phase, verbose,
                         DUNGEONS[dungeon_name].get("after_exit", []), False)
            if verbose:
                print(f"  {dungeon_name} {location}: the dungeon is over")
        elif killing.is_set():
            manual = DUNGEONS[dungeon_name].get("manual_exit")
            if not manual:
                raise RuntimeError(f"no dungeon end screen within "
                                   f"{END_TIMEOUT:g}s of the last step.")
            if verbose:
                print(f"  no dungeon end screen {END_TIMEOUT:g}s after the last "
                      f"step; leaving by hand: {manual}")
            ENDED.clear()
            run_sequence(dungeon_name, location, phase, verbose, manual, False)
            if verbose:
                print(f"  {dungeon_name} {location}: left the dungeon by hand")
    finally:
        stop.set()
        for thread in (phase["healer"], phase["ender"], phase["limit"]):
            if thread.is_alive():
                thread.join()


def press_buttons(label, steps, verbose=True):
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    if verbose:
        print(f"  {label}: {' -> '.join(key for _action, key, _wait in steps)}")
    for _action, key, seconds in steps:
        press_button(key, seconds, verbose)


def exit_dungeon(verbose=True):
    press_buttons("exit", EXIT, verbose)


def run_sequence(dungeon_name, location, phase, verbose=True, steps=None,
                 stop_when_over=True, timer=None):
    place = location_of(dungeon_name, location)
    timer = {"until": None} if timer is None else timer
    for step in (DUNGEONS[dungeon_name]["sequence"] if steps is None
                 else steps):
        name, *rest = step if isinstance(step, list) else [step]
        if stop_when_over and OVER.is_set():
            if verbose:
                print(f"  the dungeon is over; {name} and the rest are "
                      f"skipped")
            return
        rest = [place.get(part, part) if isinstance(part, str) else part
                for part in rest]
        if verbose:
            print(f"{dungeon_name} {location}: "
                  f"{' '.join(str(part) for part in [name, *rest])}")
        if name == "get_entry":
            get_entry(*place["entry"], verbose=verbose)
        elif name == "open_dungeon_key":
            open_dungeon_key(dungeon_name, location, verbose)
        elif name == "start_dungeon":
            timer["deadline"] = start_dungeon(verbose) + HARD_LIMIT
            timer["started"] = internet_now()
            if verbose:
                print(f"  the dungeon has {HARD_LIMIT:g}s from the "
                      f"{CHALLENGE_WORD} click")
            count_challenge(dungeon_name, location, timer, verbose)
        elif name == "inventory":
            prepare(verbose)
            close_inventory(verbose)
        elif name == "kill":
            kill(rest[0], target_words(dungeon_name, rest[0]), verbose)
        elif name == "bm_aura":
            bm_aura(verbose)
        elif name == "kill_phase":
            if verbose:
                print(f"  kill phase: heal ({HEAL_KEY}) every {HEAL_EVERY:g}s "
                      f"and the end screen read all the time, until the end "
                      f"screen has been up {END_WAIT:g}s")
            phase["healer"].start()
            phase["killing"].set()
        elif name == "kill_bm":
            kill_bm(rest[0], target_words(dungeon_name, rest[0]), verbose,
                    list(_DUNGEON[rest[1]]) if len(rest) > 1 else None)
        elif name == "wait":
            wait_timer(float(rest[0]), verbose)
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
        elif name == "shout":
            shout(verbose)
        else:
            play(dungeon_name, name, verbose)


def use_slot(name, verbose=True):
    points = {**LOOP_POINTS, **dict(DEFENSIVE_BUFFS), **SLOTS}
    if name not in points:
        raise RuntimeError(f"no hotbar slot {name!r}; the named slots are "
                           f"{sorted(points)}. Nothing clicked.")
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
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


def dungeon_open():
    now = internet_now()
    if now is None:
        return False
    since = (now.astimezone(RESET_ZONE) - RESET_HOUR).time()
    return datetime.timedelta(hours=since.hour, minutes=since.minute,
                              seconds=since.second) >= START_AFTER


def _save(state):
    state["server_dated"] = True
    STATE.write_text(json.dumps(state), encoding="utf-8")


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


def ran_today(state, today):
    if today is not None and state.get("day") != today:
        return {}
    return {int(level): int(count)
            for level, count in (state.get("ran") or {}).items()}


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
    up, seen = wait_for(ENTER_WINDOW, ENTER_WORD, BUTTON_TIMEOUT)
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


def count_challenge(dungeon_name, location, timer, verbose=True):
    found = LEVEL_TEXT.search(location_of(dungeon_name, location)["name"])
    if found is None:
        return
    level, rotate = int(found.group(1)), timer.get("rotate", False)
    ran, after = count_entry(level, rotate, day_of(timer.get("started")))
    if verbose:
        print(f"  level {level}: {ran} of {LIMITS.get(level)} entered on "
              f"this server day"
              + (f"; the rotation goes on at level {after}" if rotate
                 else ""))


def start(level, verbose=True, rotate=False):
    location = level_location(level)
    sequence = DUNGEONS[TRADING_DUNGEON]["sequence"]
    waits = step_index(sequence, "wait_timer")
    timer = {"until": None, "rotate": rotate}
    LOCK["level"] = level
    run_dungeon(TRADING_DUNGEON, location, verbose, sequence[:waits], timer)
    started = timer.get("started")
    if END["overtime"] or END["defeated"]:
        ACTIVE.update(level=None, until=None, deadline=None)
        return None
    if timer["until"] is None:
        raise RuntimeError(f"the {TRADING_DUNGEON} sequence starts no timer "
                           f"before its first wait_timer step.")
    lead = timer_lead(sequence[waits])
    due = timer["until"] - lead
    ACTIVE.update(level=level, until=timer["until"],
                  deadline=timer.get("deadline"))
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
                {"until": ACTIVE["until"], "deadline": ACTIVE["deadline"]})
    ACTIVE.update(level=None, until=None, deadline=None)


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


def command(args):
    if args[:1] == ["calibrate"]:
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
    elif args[:1] and args[0] in DUNGEONS and len(args) > 1:
        run_dungeon(args[0], args[1])
    else:
        raise SystemExit(f"unknown command {' '.join(args)!r}; use "
                         f"DUNGEON LOCATION ({', '.join(sorted(DUNGEONS))}), "
                         f"run N, buy N, get_entry X Y, "
                         f"open_dungeon_key DUNGEON LOCATION, start_dungeon, "
                         f"play DUNGEON SEQUENCE, kill DUNGEON NAME, bm_aura, "
                         f"kill_bm DUNGEON NAME, "
                         f"enter N, arrive, "
                         f"calibrate, shout or do ACTION")


if __name__ == "__main__":
    args = _plain_argv()
    row_model.start_backup_reader()
    calibration.log_to_file("dungeon")
    calibration.frames_on(True if "--frames" in sys.argv else None)
    calibration.watch_for_stop()
    try:
        command(args)
    finally:
        calibration.frames_written()
