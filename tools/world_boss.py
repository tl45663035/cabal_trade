import _thread
import ctypes
import datetime
import json
import os
import random
import re
import sys
import threading
import time
import zoneinfo
from pathlib import Path


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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src_1080p"))

import mss
import numpy as np
from PIL import Image

import calibration
import online_clock
import row_model
from open_inventory import _user32, focus_game, press

_SHARED = calibration.load_shared()
_BOSS = _SHARED["world_boss"]
_INPUT = _SHARED["input"]
UTC = datetime.timezone.utc
ZONE = zoneinfo.ZoneInfo(_BOSS["timezone"])
TRADING_LEAD = float(_BOSS["trading_lead"])
RESUME_WAIT = float(_BOSS["resume_wait"])
TIME_LIMIT = float(_BOSS["time_limit"])
STOP_PRESSES = int(_BOSS["stop_presses"])
STOP_WINDOW = float(_BOSS["stop_window"])
SPAWNS = sorted(datetime.time.fromisoformat(at) for at in _BOSS["spawn_times"])
CHANNELS = [int(channel) for channel in _BOSS["channels"]]
HOME_CHANNELS = [int(channel) for channel in _BOSS["home_channels"]]
POT_DELAY = float(_BOSS["pot_delay"])
POT_GAP = float(_BOSS["pot_gap"])
BUFF_AFTER_SPAWN = float(_BOSS["buff_after_spawn"])
PREPARE_LEAD = float(_BOSS["prepare_lead"])
PREPARE_CHANNEL = int(_BOSS["prepare_channel"])
PREPARE_WAIT = float(_BOSS["prepare_wait"])
TAB_TWO = tuple(_BOSS["tab_two"])
POT_SLOTS = [tuple(point) for point in _BOSS["pot_slots"]]
KILL_DELAY = float(_BOSS["kill_delay"])
TARGET_VK = int(_BOSS["target_vk"])
TARGET_EVERY = float(_BOSS["target_every"])
HEAL_VK = int(_BOSS["heal_vk"])
HEAL_EVERY = float(_BOSS["heal_every"])
HEAL_OFFSET = float(_BOSS["heal_offset"])
KEY_SPACING = float(_BOSS["key_spacing"])
OFFENSIVE_BUFFS = {name: tuple(point)
                   for name, point in _BOSS["offensive_buffs"].items()}
DEFENSIVE = "defensive_buffs"
DEFENSIVE_BUFFS = [(name, tuple(point))
                   for name, point in _BOSS[DEFENSIVE].items()]
DEFENSIVE_SWITCH = float(_BOSS["defensive_switch"])
BUFF_NAMES = set(OFFENSIVE_BUFFS) | {DEFENSIVE}
BM_ICON = tuple(_BOSS["bm_icon"])
BM_LEAD = float(_BOSS["bm_lead"])
POWERFUL_POT = tuple(_BOSS["powerful_pot"])
POWERFUL_POT_AFTER = float(_BOSS["powerful_pot_after"])
POWERFUL_LEVELS = {int(level) for level in _BOSS["powerful_levels"]}
BURST_LEVELS = {int(level) for level in _BOSS["burst_levels"]}
BURST_CHANNEL = int(_BOSS["burst_channel"])
LOOP_GAP = float(_BOSS["loop_gap"])
LOOPS = {"main": list(_BOSS["main_loop"]),
         "alternate": list(_BOSS["alternate_loop"])}
WARMUP_FOR = float(_BOSS["warmup_for"])
WARMUPS = {"main": list(_BOSS["main_warmup"]),
           "alternate": list(_BOSS["alternate_warmup"])}
LOOP_START = {phase: next((at for at, name in enumerate(cycle)
                           if name not in WARMUPS[phase]), 0)
              for phase, cycle in LOOPS.items()}
LOOP_POINTS = {name: tuple(_BOSS[name])
               for cycle in [*LOOPS.values(), *WARMUPS.values()]
               for name in cycle
               if name not in BUFF_NAMES}
LOOP_POINTS.update(OFFENSIVE_BUFFS)
MODE_DURATION = float(_BOSS["mode_duration"])
MAIN_BM_COOLDOWN = float(_BOSS["main_bm_cooldown"])
ONLINE_EVERY = float(_BOSS["online_every"])
FINAL_SYNC = float(_BOSS["final_sync"])
WAIT_SLICE = float(_SHARED["timing"]["stop_key_poll"])
ESCAPE_TRIES = int(_BOSS["escape_tries"])
ESCAPE_GAP = float(_BOSS["escape_gap"])
MENU_WINDOW = tuple(_BOSS["menu_window"])
MENU_WORD = _BOSS["menu_word"]
LIST_WINDOW = tuple(_BOSS["list_window"])
LINE_SLACK = int(_SHARED["detect"]["word_row_slack"])
CHANNEL_TEXT = re.compile(_BOSS["channel_pattern"], re.IGNORECASE)
LIST_TIMEOUT = float(_BOSS["list_timeout"])
SCROLL_POINT = tuple(_BOSS["scroll_point"])
SCROLL_TRIES = int(_BOSS["scroll_tries"])
HOVER_SETTLE = float(_BOSS["hover_settle"])
CLICK_HOLD = float(_BOSS["click_hold"])
DOUBLE_GAP = float(_BOSS["double_gap"])
DIALOG_TIMEOUT = float(_BOSS["dialog_timeout"])
YES_WINDOW = tuple(_BOSS["yes_window"])
YES_WORD = _BOSS["yes_word"]
REWARD_WINDOW = tuple(_BOSS["reward_window"])
REWARD_WORD = _BOSS["reward_word"]
RAID_RESULT = tuple(_BOSS["raid_result_window"])
REWARD_FRESH_FRAMES = int(_BOSS["reward_fresh_frames"])
REWARD_SHOT_TIMEOUT = float(_BOSS["reward_shot_timeout"])
DAMAGE_WINDOW = tuple(_BOSS["damage_window"])
DAMAGE_TEXT = re.compile(_BOSS["damage_pattern"])
REWARD_SLOT_WINDOW = tuple(_BOSS["reward_slot_window"])
EMPTY_REWARD = np.asarray(Image.open(
    calibration.HERE / _BOSS["reward_empty_image"]).convert("RGB")).astype(int)
REWARD_PIXEL_DIFF = int(_BOSS["reward_pixel_diff"])
REWARD_SHARE = float(_BOSS["reward_share"])
BOSS_BAR = tuple(_BOSS["boss_bar"])
BOSS_BAR_INK = int(_BOSS["boss_bar_ink"])
INK_MARGIN = int(_BOSS["ink_margin"])
BOSS_BAR_TEXT = re.compile(_BOSS["boss_bar_pattern"])
BOSS_TITLE = tuple(_BOSS["boss_title"])
BOSS_TITLE_INK = {name: int(value)
                  for name, value in _BOSS["boss_title_ink"].items()}
BOSS_WORD = re.compile(_BOSS["boss_word_pattern"])
BOSS_STOP = re.compile(_BOSS["boss_stop_pattern"])
BOSSES = list(_BOSS["bosses"])
BOSS_WORDS = {boss["name"]: set(BOSS_WORD.findall(boss["name"].casefold()))
              for boss in BOSSES}
SHARED_WORDS = {word for name, words in BOSS_WORDS.items() for word in words
                if any(word in other for rival, other in BOSS_WORDS.items()
                       if rival != name)}
UNIQUE_WORDS = {name: words - SHARED_WORDS
                for name, words in BOSS_WORDS.items()}
HISTORY = calibration.LOG_DIR / _BOSS["history_file"]
_ONLINE = {"asked": None, "at": None, "utc": None}
_SCREEN = {"sct": None}
_LAST_KEY = {"at": None}


def online_utc(fresh=False):
    if (fresh or _ONLINE["asked"] is None
            or time.monotonic() - _ONLINE["asked"] >= ONLINE_EVERY):
        _ONLINE["asked"] = time.monotonic()
        try:
            found = online_clock.online_utc()
            _ONLINE.update(at=time.monotonic(), utc=found)
        except OSError as exc:
            if _ONLINE["utc"] is None:
                raise RuntimeError(
                    f"{exc}; the time is not known, so the world boss "
                    f"cannot be timed. Nothing started.") from exc
            print(f"  {exc}; going on from the time server's answer "
                  f"{time.monotonic() - _ONLINE['at']:.0f}s ago")
    if _ONLINE["utc"] is None:
        raise RuntimeError("no time server has answered yet, so the world "
                           "boss cannot be timed.")
    return _ONLINE["utc"] + datetime.timedelta(
        seconds=time.monotonic() - _ONLINE["at"])


def next_spawn(now):
    here = now.astimezone(ZONE)
    for day in (here.date(), here.date() + datetime.timedelta(days=1)):
        for spawn in SPAWNS:
            at = datetime.datetime.combine(day, spawn, tzinfo=ZONE)
            if at > here:
                return at
    raise RuntimeError(f"no spawn time in {_BOSS['spawn_times']}.")


def wait_until(when, verbose=True):
    synced = False
    while True:
        left = (when - online_utc()).total_seconds()
        if not synced and left <= FINAL_SYNC:
            synced = True
            left = (when - online_utc(fresh=True)).total_seconds()
            if verbose:
                print(f"  the time servers say {left:.3f}s to go")
        if left <= 0:
            return
        time.sleep(min(left, WAIT_SLICE))


def centre(box):
    return ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)


def scaled(crop):
    return crop.resize((crop.width * row_model.BACKUP_SCALE,
                        crop.height * row_model.BACKUP_SCALE), Image.LANCZOS)


def read_windows(image, windows, origin=(0, 0)):
    texts = row_model._backup_texts(
        [scaled(image.crop((box[0] - origin[0], box[1] - origin[1],
                            box[2] - origin[0], box[3] - origin[1])))
         for box in windows])
    if texts is None:
        raise RuntimeError("the Paddle reader did not answer; nothing read.")
    return [(text or "").strip() for text in texts]


def look(box):
    if _SCREEN["sct"] is None:
        _SCREEN["sct"] = mss.MSS()
    sct = _SCREEN["sct"]
    screen = sct.monitors[1]
    raw = sct.grab({"left": screen["left"] + box[0],
                    "top": screen["top"] + box[1],
                    "width": box[2] - box[0], "height": box[3] - box[1]})
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def read_screen(windows):
    left = min(box[0] for box in windows)
    top = min(box[1] for box in windows)
    span = (left, top, max(box[2] for box in windows),
            max(box[3] for box in windows))
    return read_windows(look(span), windows, (left, top))


def channel_in(text):
    hit = CHANNEL_TEXT.search(text)
    return int(hit.group(1)) if hit is not None else None


def _mouse(flags):
    event = calibration._mouse_event(flags)
    _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))


def _tap(down, up):
    _mouse(_INPUT[down])
    try:
        time.sleep(CLICK_HOLD)
    finally:
        _mouse(_INPUT[up])


def watch_for_stop(verbose=True):
    def watch():
        seen, held = [], False
        while True:
            time.sleep(WAIT_SLICE)
            down = bool(_user32.GetAsyncKeyState(_INPUT["VK_CONTROL"])
                        & _INPUT["KEY_STATE_DOWN"])
            if down and not held:
                at = time.monotonic()
                seen = [t for t in seen if at - t <= STOP_WINDOW] + [at]
                if len(seen) >= STOP_PRESSES:
                    calibration.STOPPED.set()
                    print(f"\n  Ctrl {STOP_PRESSES} times: the world boss "
                          f"run stops; no more input is sent.")
                    _thread.interrupt_main()
                    return
            held = down

    threading.Thread(target=watch, daemon=True).start()
    if verbose:
        print(f"  press Ctrl {STOP_PRESSES} times within {STOP_WINDOW:g}s to "
              f"stop, from the game or anywhere else")


def _point_at(point):
    calibration.halt_if_stopped()
    _user32.SetCursorPos(*point)
    time.sleep(HOVER_SETTLE)


def click(point):
    _point_at(point)
    _tap("MOUSEEVENTF_LEFTDOWN", "MOUSEEVENTF_LEFTUP")


def right_click(point):
    _point_at(point)
    _tap("MOUSEEVENTF_RIGHTDOWN", "MOUSEEVENTF_RIGHTUP")


def double_click(point):
    click(point)
    time.sleep(DOUBLE_GAP)
    _tap("MOUSEEVENTF_LEFTDOWN", "MOUSEEVENTF_LEFTUP")


def key_free():
    return (_LAST_KEY["at"] is None
            or time.monotonic() - _LAST_KEY["at"] >= KEY_SPACING)


def tap_key(vk):
    _LAST_KEY["at"] = time.monotonic()
    press(vk)


def pause(seconds):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        calibration.halt_if_stopped()
        time.sleep(max(0.0, min(WAIT_SLICE, until - time.monotonic())))


def open_menu(verbose=True):
    presses, pressed, seen = 0, None, ""
    while True:
        seen = read_screen([MENU_WINDOW])[0]
        if row_model._key(MENU_WORD) in row_model._key(seen):
            if verbose:
                print(f"  the menu is up after {presses} Escape(s)")
            return
        if ((pressed is None or time.monotonic() - pressed >= ESCAPE_GAP)
                and key_free()):
            if presses == ESCAPE_TRIES:
                calibration.snap("no_menu", alongside=True)
                raise RuntimeError(
                    f"no {MENU_WORD!r} in {list(MENU_WINDOW)} after "
                    f"{ESCAPE_TRIES} Escape(s); it reads {seen!r}. Nothing "
                    f"clicked.")
            tap_key(_INPUT["VK_ESCAPE"])
            presses += 1
            pressed = time.monotonic()


def menu_up():
    return row_model._key(MENU_WORD) in row_model._key(
        read_screen([MENU_WINDOW])[0])


def listed():
    image = look(LIST_WINDOW)
    lines = []
    for text, _conf, point in calibration.ocr(
            image, (0, 0, image.width, image.height)):
        if lines and abs(point[1] - lines[-1][0][1]) <= LINE_SLACK:
            lines[-1][1].append(text)
        else:
            lines.append((point, [text]))
    shown = {}
    for point, words in lines:
        number = channel_in(" ".join(words))
        if number is not None:
            shown[number] = (LIST_WINDOW[0] + point[0],
                             LIST_WINDOW[1] + point[1])
    return shown


def list_rows(deadline, before=None):
    while True:
        shown = {} if menu_up() else listed()
        if ((shown and set(shown) != set(before or {}))
                or time.monotonic() >= deadline):
            return shown


def scroll_list():
    calibration.halt_if_stopped()
    _user32.SetCursorPos(*SCROLL_POINT)
    event = row_model._wheel_event(-1)
    sent = _user32.SendInput(1, ctypes.byref(event),
                             ctypes.sizeof(row_model.inv._Input))
    if sent != 1:
        raise RuntimeError(f"SendInput sent {sent} of 1 wheel event over the "
                           f"channel list at {list(SCROLL_POINT)}.")


def find_channel(channel, verbose=True):
    shown = list_rows(time.monotonic() + LIST_TIMEOUT)
    for scrolls in range(SCROLL_TRIES + 1):
        if not shown:
            calibration.snap("no_channel_list", alongside=True)
            raise RuntimeError(
                f"the channel list shows no channel within "
                f"{LIST_TIMEOUT:g}s of Select Channel. Nothing clicked.")
        if channel in shown:
            if verbose:
                print(f"  the list shows channels {sorted(shown)}; Channel "
                      f"{channel} at {list(shown[channel])}")
            return shown[channel]
        if scrolls == SCROLL_TRIES:
            break
        if verbose:
            print(f"  the list shows channels {sorted(shown)}; scrolling "
                  f"down")
        scroll_list()
        shown = list_rows(time.monotonic() + LIST_TIMEOUT, shown)
    calibration.snap("channel_not_listed", alongside=True)
    raise RuntimeError(
        f"Channel {channel} is not in the list; it shows {sorted(shown)} "
        f"after {SCROLL_TRIES} scroll(s). Nothing clicked.")


def confirm_move(channel, verbose=True):
    deadline = time.monotonic() + DIALOG_TIMEOUT
    while True:
        yes = read_screen([YES_WINDOW])[0]
        if row_model._key(YES_WORD) in row_model._key(yes):
            break
        if time.monotonic() >= deadline:
            calibration.snap("no_move_dialog", alongside=True)
            raise RuntimeError(
                f"no Move Channel dialog within {DIALOG_TIMEOUT:g}s of the "
                f"double-click; {list(YES_WINDOW)} reads {yes!r}. Nothing "
                f"clicked.")
    click(centre(YES_WINDOW))
    clicked = time.monotonic()
    calibration.snap(f"yes_channel_{channel}", alongside=True)
    if verbose:
        print(f"  {YES_WORD} clicked")
    return clicked


def quick_relog(channel, verbose=True):
    started = time.monotonic()
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")
    if verbose:
        print(f"quick relog to Channel {channel}")
    open_menu(verbose)
    click(centre(MENU_WINDOW))
    calibration.snap("select_channel", alongside=True)
    point = find_channel(channel, verbose)
    double_click(point)
    calibration.snap(f"channel_{channel}_double_click", alongside=True)
    clicked = confirm_move(channel, verbose)
    if verbose:
        print(f"  Channel {channel}: {clicked - started:.2f}s from the first "
              f"Escape to Yes")
    return clicked


def reward_up():
    seen = read_screen([REWARD_WINDOW])[0]
    return row_model._key(REWARD_WORD) in row_model._key(seen)


def reward_shot():
    calibration.halt_if_stopped()
    shot = look(RAID_RESULT)
    _user32.SetCursorPos(*TAB_TWO)
    fresh, deadline = 0, time.monotonic() + REWARD_SHOT_TIMEOUT
    while fresh < REWARD_FRESH_FRAMES and time.monotonic() < deadline:
        seen = look(RAID_RESULT)
        if seen.tobytes() != shot.tobytes():
            fresh += 1
        shot = seen
    return shot, look(BOSS_BAR), look(BOSS_TITLE)


def inked_text(image, ink):
    inked = ink(np.asarray(image).astype(int))
    rows = np.nonzero(inked.any(axis=1))[0]
    cols = np.nonzero(inked.any(axis=0))[0]
    if not len(rows):
        return ""
    mask = Image.fromarray(np.where(inked, 0, 255).astype(np.uint8))
    return read_windows(mask.convert("RGB"), [(
        max(0, cols[0] - INK_MARGIN), max(0, rows[0] - INK_MARGIN),
        min(mask.width, cols[-1] + 1 + INK_MARGIN),
        min(mask.height, rows[-1] + 1 + INK_MARGIN))])[0]


def boss_name(bar_image, title_image):
    bar = BOSS_BAR_TEXT.search(inked_text(
        bar_image, lambda pixels: (pixels >= BOSS_BAR_INK).all(axis=2)))
    if bar is not None:
        return bar.group(1).strip()
    ink = BOSS_TITLE_INK
    words = []
    for word in inked_text(title_image, lambda pixels: (
            (pixels[..., 0] >= ink["red_min"])
            & (pixels[..., 1] >= ink["green_min"])
            & (pixels[..., 1] <= ink["green_max"])
            & (pixels[..., 2] <= ink["blue_max"]))).split():
        if BOSS_STOP.search(word):
            break
        if BOSS_WORD.fullmatch(word):
            words.append(word)
    return " ".join(words) or None


def recognise(text):
    seen = (text or "").casefold()
    found = [boss for boss in BOSSES
             if any(word in seen for word in UNIQUE_WORDS[boss["name"]])]
    return found[0] if len(found) == 1 else None


def spot_boss(plan, verbose=True):
    read = boss_name(look(BOSS_BAR), look(BOSS_TITLE))
    boss = recognise(read)
    if boss is None:
        return
    plan.update(boss=boss, powerful=boss["level"] in POWERFUL_LEVELS,
                burst=boss["level"] in BURST_LEVELS)
    calibration.snap("boss_recognised", alongside=True)
    if verbose:
        print(f"  spawn {time.monotonic() - plan['spawn']:+.1f}s: read "
              f"{read!r}: the world boss is {boss['name']}, level "
              f"{boss['level']}; "
              + ("the powerful pot in every channel" if plan["powerful"]
                 else f"the offensive buffs and the powerful pot only in "
                      f"Channel {BURST_CHANNEL}" if plan["burst"]
                 else "no powerful pot")
              + " for this session, and no more name reads")


def damage_done(shot):
    found = DAMAGE_TEXT.search(
        read_windows(shot, [DAMAGE_WINDOW], RAID_RESULT[:2])[0])
    return int(found.group(1).replace(",", "")) if found else None


def slot_change(shot):
    box = REWARD_SLOT_WINDOW
    region = np.asarray(shot.crop((
        box[0] - RAID_RESULT[0], box[1] - RAID_RESULT[1],
        box[2] - RAID_RESULT[0], box[3] - RAID_RESULT[1]))).astype(int)
    high, wide = EMPTY_REWARD.shape[:2]
    return min(
        (np.abs(region[top:top + high, left:left + wide] - EMPTY_REWARD)
         .max(axis=2) > REWARD_PIXEL_DIFF).mean()
        for top in range(region.shape[0] - high + 1)
        for left in range(region.shape[1] - wide + 1))


def note_kill(plan, channel, boss, damage, reward):
    with open(HISTORY, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"spawn": plan["when"], "channel": channel,
                                 "boss": boss, "damage": damage,
                                 "reward": reward}) + "\n")


def analyse(plan, verbose=True):
    if verbose and plan["kills"]:
        print("world boss results, read off the Raid Result screenshots")
    boss = plan["boss"]
    for channel, shot, bar, title in plan["kills"]:
        if boss is not None:
            break
        try:
            boss = recognise(boss_name(bar, title))
        except RuntimeError as exc:
            print(f"  Channel {channel}: {exc}")
    for channel, shot, bar, title in plan["kills"]:
        change = slot_change(shot)
        reward = bool(change > REWARD_SHARE)
        try:
            damage = damage_done(shot)
        except RuntimeError as exc:
            print(f"  Channel {channel}: {exc}")
            damage = None
        note_kill(plan, channel, boss and boss["name"], damage, reward)
        if not verbose:
            continue
        enough = (None if boss is None or damage is None
                  else damage >= boss["min_points"])
        print(f"  Channel {channel}: "
              + (f"{boss['name']}, level {boss['level']}, needs "
                 f"{boss['min_points']:,}" if boss else "boss unknown")
              + "; contribution "
              + (f"{damage:,}" if damage is not None else "unread")
              + ("" if enough is None else ", enough" if enough
                 else ", short")
              + "; personal reward "
              + ("received" if reward else "none")
              + f" ({change * 100:.1f}% of the slot differs from the empty "
                f"one)"
              + ("" if enough is None or enough == reward
                 else "; the reward slot and the contribution DISAGREE"))


def next_due(due, every):
    later = due + every
    now = time.monotonic()
    return later if later > now else now + every


def mode_plan(spawn_at, when):
    return {"phase": "main", "next": spawn_at - BM_LEAD,
            "index": LOOP_START["main"], "warm_index": 0, "warm_until": None,
            "timer": None, "cancelled": None, "spawn": spawn_at,
            "when": when.isoformat(), "kills": [], "boss": None,
            "powerful": False, "burst": False}


def switch_due(plan, now):
    if plan["phase"] == "main":
        return (plan["timer"] is not None
                and now >= plan["timer"] + MODE_DURATION)
    return now >= plan["cancelled"] + MAIN_BM_COOLDOWN


def switch_bm(plan, now, verbose=True):
    if verbose:
        print(f"  spawn {now - plan['spawn']:+.1f}s: cancel {plan['phase']} "
              f"BM")
    cancel_bm(verbose)
    if plan["phase"] == "main":
        plan.update(phase="alternate", cancelled=now)
    else:
        plan.update(phase="main", timer=None)
    plan.update(index=LOOP_START[plan["phase"]], warm_index=0,
                warm_until=None, next=now + LOOP_GAP)
    if verbose:
        print(f"  spawn {now - plan['spawn']:+.1f}s: {plan['phase']} loop: "
              f"{' -> '.join(LOOPS[plan['phase']])}, {LOOP_GAP:g}s apart")


def loop_click(plan, now, buffs_from, count, saving, verbose=True):
    phase = plan["phase"]
    if plan["warm_until"] is None:
        plan["warm_until"] = now + WARMUP_FOR
        if verbose:
            print(f"  spawn {now - plan['spawn']:+.1f}s: {phase} warm-up: "
                  f"{' -> '.join(WARMUPS[phase])} until spawn "
                  f"{plan['warm_until'] - plan['spawn']:+.1f}s, then the "
                  f"{phase} loop from {LOOPS[phase][LOOP_START[phase]]}")
    warming = now < plan["warm_until"]
    stage, slot = ("warmup", "warm_index") if warming else ("loop", "index")
    cycle = (WARMUPS if warming else LOOPS)[phase]
    for _ in cycle:
        name = cycle[plan[slot] % len(cycle)]
        plan[slot] += 1
        if name not in BUFF_NAMES or now >= buffs_from:
            break
    if saving and name in OFFENSIVE_BUFFS:
        plan["next"] = now + LOOP_GAP
        return
    if name == DEFENSIVE:
        name, point = DEFENSIVE_BUFFS[int((now - buffs_from) // DEFENSIVE_SWITCH)
                                      % len(DEFENSIVE_BUFFS)]
    else:
        point = LOOP_POINTS[name]
    right_click(point)
    count[name] = count.get(name, 0) + 1
    first = plan[slot] == 1 if warming else plan[slot] % len(cycle) == 1
    if first:
        calibration.snap(f"{phase}_{stage}_{name}", alongside=True)
    if name == "aura" and plan["timer"] is None:
        plan["timer"] = now
        if verbose:
            print(f"  spawn {now - plan['spawn']:+.1f}s: aura clicked; timer "
                  f"started, main BM cancel at "
                  f"+{MODE_DURATION:g}s")
    plan["next"] = now + LOOP_GAP


def kill_boss(channel, start, plan, deadline, pot_at, verbose=True,
              buffs_from=None, loop_from=None):
    buffs_from = start if buffs_from is None else buffs_from
    loop_from = plan["next"] if loop_from is None else loop_from
    due = {"target": start, "heal": start + HEAL_OFFSET}
    count = {}
    if verbose:
        print(f"kill world boss in Channel {channel}: target and heal from "
              f"spawn {start - plan['spawn']:+.1f}s, buffs in the "
              f"{plan['phase']} loop from spawn "
              f"{buffs_from - plan['spawn']:+.1f}s, the powerful pot once from "
              f"spawn {pot_at - plan['spawn']:+.1f}s for a level "
              f"{'/'.join(map(str, sorted(POWERFUL_LEVELS)))} boss (a level "
              f"{'/'.join(map(str, sorted(BURST_LEVELS)))} boss gets it and "
              f"the offensive buffs only in Channel {BURST_CHANNEL}), until "
              f"{REWARD_WORD!r} reads")
    while True:
        if plan["boss"] is None:
            spot_boss(plan, verbose)
        if reward_up():
            seen_at = time.monotonic()
            shot, bar, title = reward_shot()
            calibration.snap(f"raid_result_channel_{channel}", image=shot)
            plan["kills"].append((channel, shot, bar, title))
            if verbose:
                print(f"  Channel {channel}: the reward window is up at spawn "
                      f"{seen_at - plan['spawn']:+.1f}s; Raid Result "
                      f"screenshot with the cursor on tab II "
                      f"{time.monotonic() - seen_at:.2f}s later; "
                      + ", ".join(f"{name} x{n}" for name, n in count.items()))
            return True
        now = time.monotonic()
        if now >= deadline:
            calibration.snap(f"time_limit_channel_{channel}", alongside=True)
            if verbose:
                print(f"  Channel {channel}: spawn +{TIME_LIMIT:g}s, the time "
                      f"limit; the world boss is over, no reward window seen "
                      f"here; " + ", ".join(f"{name} x{n}"
                                             for name, n in count.items()))
            return False
        keys_free = key_free()
        if keys_free and now >= due["target"]:
            tap_key(TARGET_VK)
            count["target"] = count.get("target", 0) + 1
            due["target"] = next_due(due["target"], TARGET_EVERY)
        elif keys_free and now >= due["heal"]:
            tap_key(HEAL_VK)
            count["heal"] = count.get("heal", 0) + 1
            due["heal"] = next_due(due["heal"], HEAL_EVERY)
        elif now >= plan["next"] and now >= loop_from:
            burst_here = channel == BURST_CHANNEL
            if switch_due(plan, now):
                switch_bm(plan, now, verbose)
            elif (pot_at is not None and now >= pot_at
                  and (plan["powerful"] or (plan["burst"] and burst_here))):
                use_powerful_pot(plan, now, channel, count, verbose)
                pot_at = None
            else:
                loop_click(plan, now, buffs_from, count,
                           plan["burst"] and not burst_here, verbose)


def use_powerful_pot(plan, now, channel, count, verbose=True):
    right_click(POWERFUL_POT)
    count["powerful_pot"] = 1
    calibration.snap(f"powerful_pot_channel_{channel}", alongside=True)
    plan["next"] = now + LOOP_GAP
    if verbose:
        print(f"  spawn {now - plan['spawn']:+.1f}s: powerful pot "
              f"right-clicked at {list(POWERFUL_POT)}")


def cancel_bm(verbose=True):
    right_click(BM_ICON)
    calibration.snap("cancel_bm", alongside=True)
    if verbose:
        print(f"  BM icon right-clicked at {list(BM_ICON)}")


def prepare(verbose=True):
    if verbose:
        print("prepare world boss")
    quick_relog(PREPARE_CHANNEL, verbose)
    if verbose:
        print(f"  waiting {PREPARE_WAIT:g}s in Channel {PREPARE_CHANNEL}")
    pause(PREPARE_WAIT)
    quick_relog(CHANNELS[0], verbose)
    pause(POT_DELAY)
    click(TAB_TWO)
    for point in POT_SLOTS:
        pause(POT_GAP)
        right_click(point)
    calibration.snap("pots", alongside=True)
    if verbose:
        print(f"  {POT_DELAY:g}s after Yes: hotbar tab II at {list(TAB_TWO)}, "
              f"then pots {POT_GAP:g}s apart at "
              f"{[list(point) for point in POT_SLOTS]}")


def run(verbose=True, when=None):
    row_model.start_backup_reader()
    now = online_utc()
    when = next_spawn(now) if when is None else when
    start = when - datetime.timedelta(seconds=PREPARE_LEAD)
    print(f"  online time {now.astimezone(ZONE):%Y-%m-%d %H:%M:%S %Z} "
          f"({now:%H:%M:%S} UTC)")
    print(f"  the next world boss is {when:%Y-%m-%d %H:%M:%S %Z} "
          f"({when.astimezone(UTC):%H:%M:%S} UTC); preparing from "
          f"{start:%H:%M:%S %Z}, in "
          f"{str(max(start - now, datetime.timedelta(0))).split('.')[0]}")
    wait_until(start, verbose)
    spawn_at = time.monotonic() + (when - online_utc()).total_seconds()
    plan = mode_plan(spawn_at, when)
    deadline = spawn_at + TIME_LIMIT
    try:
        prepare(verbose)
        finished = kill_boss(CHANNELS[0], spawn_at, plan, deadline,
                             spawn_at + POWERFUL_POT_AFTER, verbose,
                             spawn_at + BUFF_AFTER_SPAWN)
        for channel in CHANNELS[1:]:
            if not finished or time.monotonic() >= deadline:
                break
            clicked = quick_relog(channel, verbose)
            start = clicked + KILL_DELAY
            finished = kill_boss(channel, start, plan, deadline,
                                 clicked + POWERFUL_POT_AFTER, verbose,
                                 loop_from=start)
        home = random.choice(HOME_CHANNELS)
        if verbose:
            print(f"the route is done; back to Channel {home}")
        quick_relog(home, verbose)
    finally:
        analyse(plan, verbose)


if __name__ == "__main__":
    args = _plain_argv()
    calibration.log_to_file("world_boss")
    calibration.frames_on(True if "--frames" in sys.argv else None)
    watch_for_stop()
    try:
        if not args:
            run()
        elif args[:1] == ["relog"] and len(args) > 1:
            row_model.start_backup_reader()
            quick_relog(int(args[1]))
        else:
            raise SystemExit(f"unknown command {' '.join(args)!r}; use no "
                             f"command to wait for the next world boss, or "
                             f"relog N")
    finally:
        calibration.frames_written()
