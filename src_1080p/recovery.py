import ctypes
import json
import os
import time
from pathlib import Path

import calibration

_SHARED = calibration.load_shared()
_T = _SHARED["timing"]
_IN = _SHARED["input"]
_R = _SHARED["recovery"]
_REG = _SHARED["regions"]
ACTION_GAP = _T["action_gap"]
POLL_GAP = _T["poll_gap"]
KEY_GAP = _T["key_gap"]

ACCOUNT = Path(__file__).resolve().parent / "account.json"
KEYEVENTF_UNICODE = _IN["KEYEVENTF_UNICODE"]
KEYEVENTF_KEYUP = _IN["KEYEVENTF_KEYUP"]
INPUT_KEYBOARD = _IN["INPUT_KEYBOARD"]

DISCONNECT_WORDS = tuple(_R["disconnect_words"])
FAILED_WORDS = tuple(_R["failed_words"])
DUAL_WORDS = tuple(_R["dual_words"])
LOGIN_WORD = _R["login_word"]
OK_WORD = _R["ok_word"]
CONFIRM_WORD = _R["confirm_word"]
YES_WORD = _R["yes_word"]
ENTER_WORD = _R["enter_word"]

SCREEN_TIMEOUT = _R["screen_timeout"]
WORLD_TIMEOUT = _R["world_timeout"]
SUB_PASSWORD_WAIT = _R["sub_password_wait"]
AFTER_TYPING_WAIT = _R["after_typing_wait"]
FAILED_RETRY_WAIT = _R["failed_retry_wait"]
LOGIN_TRIES = _R["login_tries"]
DIALOG_BUTTON_F = tuple(_REG["recovery_dialog_button"])
DUAL_YES_F = tuple(_REG["recovery_dual_yes"])
SELECT_PANEL_F = tuple(_REG["recovery_select_panel"])
ENTER_BUTTON_F = tuple(_REG["recovery_enter_button"])
LOGIN_PANEL_F = tuple(_REG["recovery_login_panel"])
KEYPAD_F = tuple(_REG["recovery_keypad"])
KEYPAD_COLUMNS = _R["keypad_columns"]
KEYPAD_ROWS = _R["keypad_rows"]
KEYPAD_OK_F = tuple(_REG["recovery_keypad_ok"])
POPUP_F = tuple(_REG["popup"])
RECONNECT_TRIES = _R["reconnect_tries"]
RECONNECT_SETTLE = _R["reconnect_settle"]
NOTICE_TRIES = _R["notice_tries"]
CLEAR_KEYS = _R["clear_keys"]
PASSWORD_ABOVE_LOGIN = _R["password_above_login"]
USERNAME_ABOVE_LOGIN = _R["username_above_login"]
PHRASE_GAP_X = _R["phrase_gap_x"]
PHRASE_GAP_Y = _R["phrase_gap_y"]
PANEL_REACH = _R["panel_reach"]
NEAR_ABOVE = _R["near_above"]
NOTICE_OK_WAIT = _R["notice_ok_wait"]
DISCONNECT_OK_WAIT = _R["disconnect_ok_wait"]
DUAL_YES_WAIT = _R["dual_yes_wait"]
FAILED_CONFIRM_WAIT = _R["failed_confirm_wait"]
DOUBLE_GAP = _R["double_gap"]


class Refused(Exception):
    pass


def account():
    if ACCOUNT.exists():
        try:
            held = json.loads(ACCOUNT.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise Refused(f"{ACCOUNT.name} is not readable JSON: {exc}")
    else:
        held = {}
    out = {
        "username": os.environ.get("CABAL_USERNAME") or held.get("username"),
        "password": os.environ.get("CABAL_PASSWORD") or held.get("password"),
        "sub_password": (os.environ.get("CABAL_SUB_PASSWORD")
                         or held.get("sub_password")),
        "character": (os.environ.get("CABAL_CHARACTER")
                      or held.get("character")),
        "channel": os.environ.get("CABAL_CHANNEL") or held.get("channel"),
    }
    missing = [k for k, v in out.items() if not v]
    if missing:
        raise Refused(
            f"no {', '.join(missing)} in {ACCOUNT.name} or the environment, "
            f"so there is nothing to log in with. Nothing typed.")
    if not str(out["sub_password"]).isdigit():
        raise Refused("the sub password must be digits; the keypad has "
                      "nothing else on it.")
    return out


def _point(frac):
    x, y, w, h = calibration._client_rect()
    return (round(x + frac[0] * w), round(y + frac[1] * h))


def _screen():
    x, y, w, h = calibration._client_rect()
    return (x, y, x + w, y + h)


def _words(image=None):
    image = image if image is not None else calibration.grab()
    return calibration.ocr(image, _screen())


def _matches(seen, want, whole):
    return seen == want or (not whole and want in seen)


def _find(want, words=None, whole=True):
    words = _words() if words is None else words
    parts = want.strip().lower().split()
    if not parts:
        return None
    for text, _conf, point in words:
        if not _matches(text.strip().lower(), parts[0], whole):
            continue
        here = point
        for part in parts[1:]:
            here = next(
                (p for t, _c, p in words
                 if _matches(t.strip().lower(), part, whole)
                 and 0 < p[0] - here[0] <= PHRASE_GAP_X
                 and abs(p[1] - here[1]) <= PHRASE_GAP_Y), None)
            if here is None:
                break
        else:
            return point
    return None


def _find_in(want, region_frac, whole=False):
    box = _box_frac(region_frac)
    words = calibration.ocr(calibration.grab(), box)
    return _find(want, words=words, whole=whole)


def _box_frac(frac):
    x, y, w, h = calibration._client_rect()
    return (round(x + frac[0] * w), round(y + frac[1] * h),
            round(x + frac[2] * w), round(y + frac[3] * h))


def _wait_for(want, timeout=SCREEN_TIMEOUT, whole=True, verbose=True,
              region=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        point = (_find_in(want, region, whole=whole) if region
                 else _find(want, whole=whole))
        if point is not None:
            if verbose:
                print(f"  {want!r} at {list(point)}")
            return point
        time.sleep(POLL_GAP)
    return None


def _find_near(want, anchor, timeout=SCREEN_TIMEOUT, verbose=True):
    _x, _y, _w, height = calibration._client_rect()
    reach = round(height * PANEL_REACH)
    box = (anchor[0] - reach, anchor[1] - NEAR_ABOVE,
           anchor[0] + reach, anchor[1] + reach)
    want = want.strip().lower()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for text, _conf, point in calibration.ocr(calibration.grab(), box):
            if text.strip().lower() == want:
                if verbose:
                    print(f"  {want!r} at {list(point)}")
                return point
        time.sleep(POLL_GAP)
    return None


def _needed(want, timeout=SCREEN_TIMEOUT, whole=True, verbose=True,
            region=None):
    point = _wait_for(want, timeout=timeout, whole=whole, verbose=verbose,
                      region=region)
    if point is None:
        calibration.snap(f"recovery_no_{want.replace(' ', '_')}")
        raise Refused(
            f"no {want!r} on screen within {timeout:g}s, so the login is not "
            f"where it was expected. Nothing further was clicked.")
    return point


def _double_click(x, y):
    calibration.click(x, y, settle=DOUBLE_GAP)
    calibration.click(x, y)


def _enter_the_world(verbose=True):
    from open_inventory import press
    keys = calibration.load_shared()["input"]
    if verbose:
        print("  Enter to start")
    press(keys["VK_RETURN"])
    calibration.snap("recovery_pressed_enter")
    time.sleep(ACTION_GAP)


def _clear_field():
    from open_inventory import press
    keys = calibration.load_shared()["input"]
    press(keys["VK_END"])
    time.sleep(KEY_GAP)
    for _ in range(CLEAR_KEYS):
        press(keys["VK_BACK"])
        time.sleep(KEY_GAP)


def _type(text):
    from open_inventory import _user32, _Input, _InputUnion, _KeyInput
    for ch in str(text):
        for up in (False, True):
            flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
            event = _Input(type=INPUT_KEYBOARD,
                           u=_InputUnion(ki=_KeyInput(0, ord(ch), flags,
                                                      0, None)))
            _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input))
        time.sleep(KEY_GAP)


def _within(words, box):
    return [(text, conf, point) for text, conf, point in words
            if box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]]


def _notice(phrases, image=None, words=None):
    words = _within(_words(image) if words is None else words,
                    _box_frac(POPUP_F))
    for phrase in phrases:
        point = _find(phrase, words=words, whole=False)
        if point is not None:
            return point
    return None


def disconnected(image=None, words=None):
    return _notice(DISCONNECT_WORDS, image, words)


def failed_to_connect(image=None, words=None):
    return _notice(FAILED_WORDS, image, words)


def dual_login(image=None, words=None):
    return _notice(DUAL_WORDS, image, words)


def in_the_world():
    try:
        return calibration.inventory_open() is not None
    except Exception as exc:
        print(f"  no world check: {type(exc).__name__}: {exc}")
        return False


def _login_screen_gone():
    return _find_in(LOGIN_WORD, LOGIN_PANEL_F, whole=True) is None


def _keypad_cells(image=None):
    image = image if image is not None else calibration.grab()
    box = _box_frac(KEYPAD_F)
    wide = (box[2] - box[0]) / KEYPAD_COLUMNS
    tall = (box[3] - box[1]) / KEYPAD_ROWS
    seen = {}
    for row in range(KEYPAD_ROWS):
        for column in range(KEYPAD_COLUMNS):
            middle = (round(box[0] + (column + 0.5) * wide),
                      round(box[1] + (row + 0.5) * tall))
            cell = (round(middle[0] - wide / 2), round(middle[1] - tall / 2),
                    round(middle[0] + wide / 2), round(middle[1] + tall / 2))
            digit = calibration.read_line(image, cell).strip()
            if len(digit) == 1 and digit.isdigit():
                seen.setdefault(digit, []).append(middle)
    return seen


def keypad_if_asked(timeout=SUB_PASSWORD_WAIT, verbose=True):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        seen = _keypad_cells()
        if len(seen) >= 10 and all(len(p) == 1 for p in seen.values()):
            return keypad(verbose=verbose, seen=seen)
        if in_the_world():
            return None
        if not seen and calibration.await_inventory(
                timeout=ACTION_GAP) is not None:
            return None
        time.sleep(POLL_GAP)
    return None


def keypad(verbose=True, seen=None):
    deadline = time.monotonic() + SCREEN_TIMEOUT
    while True:
        read = _keypad_cells() if seen is None else seen
        doubled = sorted(d for d, points in read.items() if len(points) > 1)
        missing = sorted(str(d) for d in range(10) if str(d) not in read)
        if not missing and not doubled:
            break
        seen = None
        if time.monotonic() >= deadline:
            calibration.snap("recovery_keypad_unclear")
            raise Refused(
                f"the sub password keypad does not read as ten single "
                f"digits: "
                f"{'missing ' + ','.join(missing) if missing else ''}"
                f"{' and ' if missing and doubled else ''}"
                f"{'seen twice ' + ','.join(doubled) if doubled else ''}. "
                f"Nothing was clicked.")
        time.sleep(POLL_GAP)
    out = {digit: points[0] for digit, points in read.items()}
    if verbose:
        print(f"  keypad reads {' '.join(d + '@' + str(list(p)) for d, p in sorted(out.items()))}")
    return out


def recover(verbose=True):
    from open_inventory import focus_game
    who = account()

    if not focus_game():
        raise Refused("could not bring the game to the foreground.")

    for _ in range(NOTICE_TRIES):
        notice = disconnected()
        if notice is None:
            break
        shut = _find_near(OK_WORD, notice, timeout=NOTICE_OK_WAIT,
                          verbose=False) or _point(DIALOG_BUTTON_F)
        calibration.snap("recovery_notice")
        if verbose:
            print(f"  a notice at {list(notice)}; {OK_WORD} at {list(shut)}")
        calibration.click(*shut)
        time.sleep(ACTION_GAP)

    gone = disconnected()
    if gone is not None:
        calibration.snap("recovery_disconnected")
        if verbose:
            print(f"  the disconnect notice at {list(gone)}")
        shut = _find_near(OK_WORD, gone, timeout=DISCONNECT_OK_WAIT,
                          verbose=verbose)
        if shut is None:
            shut = _point(DIALOG_BUTTON_F)
            if verbose:
                print(f"  {OK_WORD} would not read; clicking the dialog's "
                      f"button seat at {list(shut)}")
        calibration.click(*shut)
    elif _find_in(LOGIN_WORD, LOGIN_PANEL_F, whole=True) is None:
        if in_the_world():
            calibration.snap("recovery_already_in_world")
            if verbose:
                print("  no disconnect notice and no login screen; the Alz "
                      "balance reads, so the character is already in the "
                      "world")
            return True
        calibration.snap("recovery_nothing_to_recover")
        raise Refused(
            "no disconnect notice and no login screen, so there is nothing "
            "to recover from. Nothing clicked.")
    elif verbose:
        print("  no disconnect notice; the login screen is already up")

    for attempt in range(1, LOGIN_TRIES + 1):
        sign_in = _needed(LOGIN_WORD, verbose=verbose,
                          region=LOGIN_PANEL_F)
        calibration.snap("recovery_login_screen")

        name = (sign_in[0], sign_in[1] - USERNAME_ABOVE_LOGIN)
        if verbose:
            print(f"  the username field at {list(name)}")
        calibration.click(*name)
        _clear_field()
        calibration.snap("recovery_username_cleared")
        _type(who["username"])

        field = (sign_in[0], sign_in[1] - PASSWORD_ABOVE_LOGIN)
        if verbose:
            print(f"  the password field at {list(field)}")
        calibration.click(*field)
        _clear_field()
        _type(who["password"])
        if verbose:
            print(f"  typed; waiting {AFTER_TYPING_WAIT:g}s before Login")
        time.sleep(AFTER_TYPING_WAIT)
        calibration.click(*_needed(LOGIN_WORD, verbose=False,
                                   region=LOGIN_PANEL_F))

        outcome, where, reconnects = None, None, 0
        deadline = time.monotonic() + SCREEN_TIMEOUT
        while time.monotonic() < deadline:
            where = failed_to_connect()
            if where is not None:
                outcome = "failed"
                break
            where = dual_login()
            if where is not None:
                if reconnects >= RECONNECT_TRIES:
                    outcome = "dual"
                    break
                reconnects += 1
                calibration.snap(f"recovery_dual_login_{attempt}_{reconnects}")
                yes = _find_near(YES_WORD, where, timeout=DUAL_YES_WAIT,
                                 verbose=False) or _point(DUAL_YES_F)
                if verbose:
                    print(f"  the ID is still connected; clicking {YES_WORD} "
                          f"to reconnect ({reconnects}/{RECONNECT_TRIES}) at "
                          f"{list(yes)}")
                calibration.click(*yes)
                time.sleep(FAILED_RETRY_WAIT)
                deadline = time.monotonic() + RECONNECT_SETTLE
                continue
            if _find_in(who["channel"], SELECT_PANEL_F) is not None:
                outcome = "channel"
                break
            if in_the_world():
                outcome = "world"
                break
            if _login_screen_gone() and calibration.await_inventory(
                    timeout=ACTION_GAP) is not None:
                outcome = "world"
                break
            time.sleep(POLL_GAP)

        if outcome in ("channel", "world"):
            if outcome == "world":
                calibration.snap("recovery_back_in_world")
                if verbose:
                    print(f"  reconnected straight into the world as "
                          f"{who['character']}")
                return True
            break

        if outcome == "dual":
            raise Refused(
                f"the ID stayed connected through {RECONNECT_TRIES} reconnect "
                f"attempts; the server has not released the old session yet.")

        if outcome == "failed":
            calibration.snap(f"recovery_login_failed_{attempt}")
            if verbose:
                print(f"  the server refused the login (attempt {attempt}/"
                      f"{LOGIN_TRIES}); clicking {CONFIRM_WORD} and waiting "
                      f"{FAILED_RETRY_WAIT:g}s")
            ok = _find_near(CONFIRM_WORD, where, timeout=FAILED_CONFIRM_WAIT,
                            verbose=False)
            if ok is None:
                ok = _point(DIALOG_BUTTON_F)
                if verbose:
                    print(f"  {CONFIRM_WORD} would not read; clicking the "
                          f"dialog's button seat at {list(ok)}")
            calibration.click(*ok)
            time.sleep(FAILED_RETRY_WAIT)
            continue

        break
    else:
        raise Refused(
            f"the server refused the login {LOGIN_TRIES} times; it is not "
            f"taking connections. Nothing more to try now.")

    channel = None
    deadline = time.monotonic() + RECONNECT_SETTLE
    while time.monotonic() < deadline and channel is None:
        channel = _find_in(who["channel"], SELECT_PANEL_F)
        if channel is None:
            time.sleep(POLL_GAP)
    if channel is None:
        calibration.snap("recovery_no_channel_panel")
        raise Refused(
            f"no {who['channel']!r} in the server list within "
            f"{RECONNECT_SETTLE:g}s. Nothing entered.")
    calibration.snap("recovery_server_select")
    if verbose:
        print(f"  {who['channel']!r} at {list(channel)}; entering")
    _double_click(*channel)
    time.sleep(ACTION_GAP)
    enter = _find_in(ENTER_WORD, ENTER_BUTTON_F, whole=False)
    if enter is not None:
        calibration.click(*enter)

    who_at = None
    deadline = time.monotonic() + SCREEN_TIMEOUT
    while time.monotonic() < deadline and who_at is None:
        who_at = _find_in(who["character"], SELECT_PANEL_F)
        if who_at is None:
            time.sleep(POLL_GAP)
    if who_at is None:
        calibration.snap("recovery_no_character")
        raise Refused(
            f"no {who['character']!r} in the character list within "
            f"{SCREEN_TIMEOUT:g}s. Nothing entered.")
    calibration.snap("recovery_character_select")
    if verbose:
        print(f"  {who['character']!r} at {list(who_at)}; entering")
    calibration.click(*who_at)
    _enter_the_world(verbose=verbose)

    pad = keypad_if_asked(verbose=verbose)
    if pad is None:
        if verbose:
            print(f"  no sub password asked for within "
                  f"{SUB_PASSWORD_WAIT:g}s; going straight in")
    else:
        calibration.snap("recovery_sub_password")
        for digit in str(who["sub_password"]):
            calibration.click(*pad[digit])
        if verbose:
            print("  the sub password clicked in")
        calibration.click(*_needed(OK_WORD, verbose=verbose,
                                   region=KEYPAD_OK_F))

    deadline = time.monotonic() + WORLD_TIMEOUT
    while time.monotonic() < deadline:
        if calibration.await_inventory(timeout=ACTION_GAP) is not None:
            calibration.snap("recovery_back_in_world")
            if verbose:
                print(f"  back in the world as {who['character']}, the "
                      f"balance reads")
            return True
        time.sleep(POLL_GAP)
    calibration.snap("recovery_never_reached_world")
    raise Refused(
        f"logged through every screen but no Alz balance is readable after "
        f"{WORLD_TIMEOUT:g}s, so the character is not in the world yet.")


if __name__ == "__main__":
    import sys

    calibration.log_to_file("recovery")
    calibration.frames_on(True if "--frames" in sys.argv else None)
    if "--scan" in sys.argv:
        for text, conf, point in _words():
            if text.strip():
                print(f"  {text!r:<28} conf={round(conf):<4} at {list(point)}")
    else:
        recover()
