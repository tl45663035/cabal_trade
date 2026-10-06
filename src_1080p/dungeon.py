import ctypes
import datetime
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
from open_inventory import focus_game, press

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
SERVER_UTC_OFFSET = datetime.timedelta(
    hours=float(_DUNGEON["server_utc_offset_hours"]))
TIME_LIMIT = datetime.timedelta(seconds=float(_DUNGEON["time_limit_seconds"]))
sys.path.append(str(calibration.HERE.parent / "tools"))
CLEARED_WINDOW = tuple(_DUNGEON["cleared_window"])
CLEARED_TEXT = re.compile(_DUNGEON["cleared_pattern"], re.IGNORECASE)
BUTTONS = _DUNGEON["buttons"]
BUTTON_TIMEOUT = float(_SHARED["timing"]["dialog_timeout"])
FINISH = [tuple(step) for step in _DUNGEON["finish"]]
KEY_CELLS = [tuple(cell) for cell in _DUNGEON["key_cells"]]
KEY_WINDOW = tuple(_DUNGEON["key_window"])
KEY_TEXT = re.compile(_DUNGEON["key_pattern"], re.IGNORECASE)
BUY_SETTLE = float(_DUNGEON["buy_settle"])
TRANSFER_TIMEOUT = float(_DUNGEON["transfer_timeout"])
INVENTORY_TAB = int(_DUNGEON["inventory_tab"])
KEY_TAB = int(_DUNGEON["key_tab"])
RELOG_CHANNELS = list(_DUNGEON["relog_channels"])
LOCK = {"boss": None, "reading": None, "cleared": False, "level": ORDER[0]}


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
    open_agent_shop_premium.ensure_inventory_open(verbose=verbose)
    point = calibration.inventory_tab_point(tab)
    if verbose:
        print(f"  inventory tab {tab} at {list(point)}")
    click(*point, SELECT_SETTLE)


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
    channel = random.choice(RELOG_CHANNELS)
    if verbose:
        print(f"  relogging into {channel}")
    recovery.relog_to(channel, verbose=verbose)


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


def _state():
    try:
        known = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return known if isinstance(known, dict) else {}


def internet_now():
    import online_clock
    try:
        found = online_clock.measure()
    except OSError:
        return None
    return datetime.datetime.fromtimestamp(time.time() + found["offset"],
                                           datetime.timezone.utc)


def day_of(when):
    return (when + SERVER_UTC_OFFSET).date().isoformat() if when else None


def server_day():
    return day_of(internet_now())


def _save(state):
    STATE.write_text(json.dumps(state), encoding="utf-8")


def remember_active(level, started):
    now = internet_now()
    if now is None or started is None:
        return
    state = _state()
    state["active"] = {
        "level": level,
        "due": (now + datetime.timedelta(seconds=BOSS_WAIT)).isoformat(),
        "ends": (started + TIME_LIMIT).isoformat()}
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
        return None
    level = int(active["level"])
    LOCK["level"] = level
    left = (datetime.datetime.fromisoformat(active["due"]) - now)
    return level, time.monotonic() + left.total_seconds()


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
    STATE.write_text(json.dumps(state), encoding="utf-8")
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


def start(level, verbose=True, rotate=False):
    before = key_tab_slots(verbose)
    buy_key(level, verbose)
    use_key(before, verbose)
    relog(verbose)
    prepare(verbose)
    enter_dungeon(level, verbose)
    return arrive_at_gate(verbose, rotate)


def kill_and_finish(verbose=True):
    forget_active()
    LOCK.update(boss=None, cleared=False)
    move([("do", "kill_boss", 0.0)], verbose)
    finish_dungeon(verbose)


def fight(verbose=True):
    wait_for_boss(arrive_at_gate(verbose), verbose)
    kill_and_finish(verbose)


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
        if LOCK["cleared"]:
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
            press(KEYS[key])
            time.sleep(seconds)
        elif action == "wait":
            if verbose:
                print(f"  wait {seconds:g}s")
            time.sleep(seconds)
        elif action == "target":
            if verbose:
                print(f"  target with {key}, then wait {seconds:g}s")
            if LOCK["boss"] is None:
                press(KEYS[key])
            time.sleep(seconds)
            reading = LOCK["reading"]
            if reading is None or not reading.is_alive():
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


def shout(verbose=True):
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    if calibration._trade_window_open():
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


if __name__ == "__main__":
    args = _plain_argv()
    calibration.log_to_file("dungeon")
    calibration.frames_on(True if "--frames" in sys.argv else None)
    calibration.watch_for_stop()
    try:
        if args[:1] == ["calibrate"]:
            calibrate()
        elif args[:1] == ["enter"] and len(args) > 1:
            enter_dungeon(int(args[1]))
        elif args[:1] == ["arrive"]:
            fight()
        elif args[:1] == ["buy"] and len(args) > 1:
            prepare()
            buy_key(int(args[1]))
        elif args[:1] == ["run"] and len(args) > 1:
            wait_for_boss(start(int(args[1])))
            kill_and_finish()
        elif args[:1] == ["do"] and len(args) > 1:
            move([("do", args[1], 0.0)])
        elif args[:1] == ["shout"]:
            shout()
        else:
            raise SystemExit(f"unknown command {' '.join(args)!r}; use "
                             f"run N, buy N, enter N, arrive, calibrate, "
                             f"shout or do ACTION")
    finally:
        calibration.frames_written()
