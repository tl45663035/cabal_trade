import ctypes
import json
import os
import time
from pathlib import Path

import calibration

_SHARED = calibration.load_shared()
ACTION_GAP = _SHARED["timing"]["action_gap"]
POLL_GAP = _SHARED["timing"]["poll_gap"]
KEY_GAP = _SHARED["timing"]["key_gap"]

ACCOUNT = Path(__file__).resolve().parent / "account.json"
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

DISCONNECT_WORDS = ("disconnect", "disconnected", "log-out", "logged")
FAILED_WORDS = ("failed to connect", "try later")
DUAL_WORDS = ("dual login", "already in use", "like to reconnect")
LOGIN_WORD = "login"
OK_WORD = "ok"
CONFIRM_WORD = "confirmation"
YES_WORD = "yes"

SCREEN_TIMEOUT = 25.0
WORLD_TIMEOUT = 120.0
SUB_PASSWORD_WAIT = 12.0
AFTER_TYPING_WAIT = 10.0
FAILED_RETRY_WAIT = 5.0
LOGIN_TRIES = 6
DIALOG_BUTTON_F = (0.5004, 0.5457)
DUAL_YES_F = (0.4766, 0.5457)
RECONNECT_TRIES = 4
RECONNECT_SETTLE = 60.0
NOTICE_TRIES = 3
CLEAR_KEYS = 32
PASSWORD_ABOVE_LOGIN = 86
USERNAME_ABOVE_LOGIN = 127
PHRASE_GAP_X = 260
PHRASE_GAP_Y = 16
DOUBLE_GAP = 0.08


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


def _wait_for(want, timeout=SCREEN_TIMEOUT, whole=True, verbose=True):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        point = _find(want, whole=whole)
        if point is not None:
            if verbose:
                print(f"  {want!r} at {list(point)}")
            return point
        time.sleep(POLL_GAP)
    return None


def _find_near(want, anchor, timeout=SCREEN_TIMEOUT, verbose=True):
    _x, _y, _w, height = calibration._client_rect()
    reach = round(height * 0.16)
    box = (anchor[0] - reach, anchor[1] - 10,
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


def _needed(want, timeout=SCREEN_TIMEOUT, whole=True, verbose=True):
    point = _wait_for(want, timeout=timeout, whole=whole, verbose=verbose)
    if point is None:
        calibration.snap(f"recovery_no_{want.replace(' ', '_')}")
        raise Refused(
            f"no {want!r} on screen within {timeout:g}s, so the login is not "
            f"where it was expected. Nothing further was clicked.")
    return point


def _double_click(x, y):
    calibration.click(x, y, settle=DOUBLE_GAP)
    calibration.click(x, y)


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


def disconnected(image=None):
    words = _words(image)
    for word in DISCONNECT_WORDS:
        point = _find(word, words=words, whole=False)
        if point is not None:
            return point
    return None


def failed_to_connect(image=None):
    words = _words(image)
    for phrase in FAILED_WORDS:
        point = _find(phrase, words=words, whole=False)
        if point is not None:
            return point
    return None


def dual_login(image=None):
    words = _words(image)
    for phrase in DUAL_WORDS:
        point = _find(phrase, words=words, whole=False)
        if point is not None:
            return point
    return None


def in_the_world(timeout=None):
    span = ACTION_GAP if timeout is None else timeout
    try:
        return calibration.await_inventory(timeout=span) is not None
    except Exception:
        return False


def keypad_if_asked(timeout=SUB_PASSWORD_WAIT, verbose=True):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        words = _words()
        digits = {t.strip() for t, _c, _p in words
                  if len(t.strip()) == 1 and t.strip().isdigit()}
        if len(digits) >= 10:
            return keypad(verbose=verbose)
        if calibration.await_inventory(timeout=ACTION_GAP) is not None:
            return None
        time.sleep(POLL_GAP)
    return None


def keypad(verbose=True):
    words = _words()
    seen = {}
    for text, _conf, point in words:
        digit = text.strip()
        if len(digit) == 1 and digit.isdigit():
            seen.setdefault(digit, []).append(point)
    doubled = sorted(d for d, points in seen.items() if len(points) > 1)
    missing = sorted(str(d) for d in range(10) if str(d) not in seen)
    if missing or doubled:
        calibration.snap("recovery_keypad_unclear")
        raise Refused(
            f"the sub password keypad does not read as ten single digits: "
            f"{'missing ' + ','.join(missing) if missing else ''}"
            f"{' and ' if missing and doubled else ''}"
            f"{'seen twice ' + ','.join(doubled) if doubled else ''}. "
            f"Nothing was clicked.")
    out = {digit: points[0] for digit, points in seen.items()}
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
        shut = _find_near(OK_WORD, notice, timeout=ACTION_GAP * 4,
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
        shut = _find_near(OK_WORD, gone, timeout=ACTION_GAP * 20,
                          verbose=verbose)
        if shut is None:
            shut = _point(DIALOG_BUTTON_F)
            if verbose:
                print(f"  {OK_WORD} would not read; clicking the dialog's "
                      f"button seat at {list(shut)}")
        calibration.click(*shut)
    elif _find(LOGIN_WORD) is None:
        calibration.snap("recovery_nothing_to_recover")
        raise Refused(
            "no disconnect notice and no login screen, so there is nothing "
            "to recover from. Nothing clicked.")
    elif verbose:
        print("  no disconnect notice; the login screen is already up")

    for attempt in range(1, LOGIN_TRIES + 1):
        sign_in = _needed(LOGIN_WORD, verbose=verbose)
        calibration.snap("recovery_login_screen")

        name = (sign_in[0], sign_in[1] - USERNAME_ABOVE_LOGIN)
        if verbose:
            print(f"  the username field at {list(name)}")
        calibration.click(*name)
        _clear_field()
        _type(who["username"])

        field = (sign_in[0], sign_in[1] - PASSWORD_ABOVE_LOGIN)
        if verbose:
            print(f"  the password field at {list(field)}")
        calibration.click(*field)
        _clear_field()
        _type(who["password"])
        calibration.snap("recovery_credentials_typed")
        if verbose:
            print(f"  typed; waiting {AFTER_TYPING_WAIT:g}s before Login")
        time.sleep(AFTER_TYPING_WAIT)
        calibration.click(*_needed(LOGIN_WORD, verbose=False))

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
                yes = _find_near(YES_WORD, where, timeout=ACTION_GAP * 4,
                                 verbose=False) or _point(DUAL_YES_F)
                if verbose:
                    print(f"  the ID is still connected; clicking {YES_WORD} "
                          f"to reconnect ({reconnects}/{RECONNECT_TRIES}) at "
                          f"{list(yes)}")
                calibration.click(*yes)
                time.sleep(FAILED_RETRY_WAIT)
                deadline = time.monotonic() + RECONNECT_SETTLE
                continue
            if _find(who["channel"], whole=False) is not None:
                outcome = "channel"
                break
            if in_the_world():
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
            ok = _find_near(CONFIRM_WORD, where, timeout=ACTION_GAP * 4,
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

    channel = _needed(who["channel"], whole=False, verbose=verbose)
    if verbose:
        print(f"  double-clicking {who['channel']!r} to enter")
    _double_click(*channel)

    who_at = _needed(who["character"], whole=False, verbose=verbose)
    if verbose:
        print(f"  double-clicking {who['character']!r} to enter")
    _double_click(*who_at)

    pad = keypad_if_asked(verbose=verbose)
    if pad is None:
        if verbose:
            print(f"  no sub password asked for within "
                  f"{SUB_PASSWORD_WAIT:g}s; going straight in")
    else:
        calibration.snap("recovery_sub_password")
        for digit in str(who["sub_password"]):
            if verbose:
                print(f"  sub password digit at {list(pad[digit])}")
            calibration.click(*pad[digit])
        calibration.click(*_needed(OK_WORD, verbose=verbose))

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
