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

DISCONNECT_WORDS = ("disconnect", "disconnected")
LOGIN_WORD = "login"
SERVER_WORD = "enter server"
START_WORD = "start"
OK_WORD = "ok"

SCREEN_TIMEOUT = 90.0
WORLD_TIMEOUT = 120.0
FIELD_ABOVE_LOGIN = 0.104


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


def _screen():
    x, y, w, h = calibration._client_rect()
    return (x, y, x + w, y + h)


def _words(image=None):
    image = image if image is not None else calibration.grab()
    return calibration.ocr(image, _screen())


def _find(want, words=None, whole=True):
    words = _words() if words is None else words
    want = want.strip().lower()
    for text, _conf, point in words:
        seen = text.strip().lower()
        if seen == want or (not whole and want in seen):
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


def _needed(want, timeout=SCREEN_TIMEOUT, whole=True, verbose=True):
    point = _wait_for(want, timeout=timeout, whole=whole, verbose=verbose)
    if point is None:
        calibration.snap(f"recovery_no_{want.replace(' ', '_')}")
        raise Refused(
            f"no {want!r} on screen within {timeout:g}s, so the login is not "
            f"where it was expected. Nothing further was clicked.")
    return point


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

    gone = disconnected()
    if gone is None:
        raise Refused(
            "no Disconnected window on screen, so there is nothing to "
            "recover from. Nothing clicked.")
    calibration.snap("recovery_disconnected")
    if verbose:
        print(f"  the disconnect notice at {list(gone)}")
    calibration.click(*_needed(OK_WORD, timeout=ACTION_GAP * 20,
                               verbose=verbose))

    sign_in = _needed(LOGIN_WORD, verbose=verbose)
    calibration.snap("recovery_login_screen")
    _x, _y, _w, height = calibration._client_rect()
    field = (sign_in[0], sign_in[1] - round(height * FIELD_ABOVE_LOGIN))
    if verbose:
        print(f"  the password field at {list(field)}")
    calibration.click(*field)
    _type(who["password"])
    time.sleep(ACTION_GAP)
    calibration.click(*_needed(LOGIN_WORD, verbose=False))

    calibration.click(*_needed(who["channel"], whole=False, verbose=verbose))
    calibration.click(*_needed(SERVER_WORD, whole=False, verbose=verbose))

    calibration.click(*_needed(who["character"], whole=False,
                               verbose=verbose))
    calibration.click(*_needed(START_WORD, whole=False, verbose=verbose))

    _needed(OK_WORD, verbose=False)
    calibration.snap("recovery_sub_password")
    pad = keypad(verbose=verbose)
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
