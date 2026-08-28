import time

import calibration

_SHARED = calibration.load_shared()
ACTION_GAP = _SHARED["timing"]["action_gap"]
DIALOG_TIMEOUT = _SHARED["timing"]["dialog_timeout"]
POLL_GAP = _SHARED["timing"]["poll_gap"]
GIFT_GAP = _SHARED["timing"]["gift_gap"]
RECEIVE_WORD = _SHARED["text"]["receipt_word"]
CLOSE_WORD = _SHARED["text"]["close_word"]
BUTTON_HALF = tuple(_SHARED["detect"]["dialog_button_half"])


class Refused(Exception):
    pass


def measured():
    return calibration.load().get("gifts") or None


def _word_at(word, point, image=None):
    image = image if image is not None else calibration.grab()
    dx, dy = BUTTON_HALF
    box = (point[0] - dx, point[1] - dy, point[0] + dx, point[1] + dy)
    want = word.strip().lower()
    return any(text.strip().lower() == want
               for text, _conf, _p in calibration.ocr(image, box))


def _window_open(close):
    deadline = time.monotonic() + DIALOG_TIMEOUT
    while time.monotonic() < deadline:
        if _word_at(CLOSE_WORD, close):
            return True
        time.sleep(POLL_GAP)
    return False


def collect_gifts(verbose=True):
    from open_inventory import focus_game
    if not focus_game():
        raise Refused("could not bring the game to the foreground.")

    gifts = measured()
    if not gifts:
        raise Refused(
            "the gift box has not been measured for this screen; run "
            "py src/calibration.py first. Nothing collected.")

    icon = tuple(gifts["icon"])
    close = tuple(gifts["close"])
    taking = [tuple(p) for p in gifts["receive"]]

    if verbose:
        print(f"  the gift box at {list(icon)}")
    calibration.click(*icon)
    time.sleep(ACTION_GAP)

    if not _window_open(close):
        calibration.snap("gift_window_never_opened")
        raise Refused(
            f"no {CLOSE_WORD} at the measured {list(close)} within "
            f"{DIALOG_TIMEOUT:g}s, so the gift window is not open where it "
            f"was measured. Nothing clicked.")

    for n, point in enumerate(taking, 1):
        if verbose:
            print(f"  {RECEIVE_WORD} {n} of {len(taking)} at {list(point)}")
        calibration.click(*point, settle=GIFT_GAP)

    if verbose:
        print(f"  {CLOSE_WORD} at {list(close)}")
    calibration.click(*close)
    return len(taking)


if __name__ == "__main__":
    import sys

    calibration.log_to_file("gifts")
    calibration.frames_on(True if "--frames" in sys.argv else None)
    print(f"  collected {collect_gifts()} gift(s)")
