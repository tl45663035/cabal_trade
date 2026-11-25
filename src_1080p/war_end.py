"""The Mission War Report that opens when a war ends: take the WEXP boost
(WEXP UP -> Select -> 200 % -> Yes, paid in Force Gems), then OK to close it,
then back to the home channel through Escape -> Select Channel.
The Guild Treasure panel beside it is left alone.

    py war_end.py --config cang          # handle the Report if it is up
    py war_end.py --config cang --check  # only say what is on screen

Every box and word is in config.json "war_end" (measured 2026-11-13 at
1920x1080 from the 19:33 server-time war)."""
import os
import re
import sys
import time


def _arg(name, default=None):
    for index, arg in enumerate(sys.argv):
        if arg.startswith(f"{name}="):
            return arg.split("=", 1)[1]
        if arg == name and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return default


if __name__ == "__main__" and _arg("--config"):
    os.environ["CABAL_CONFIG"] = _arg("--config")

import calibration  # noqa: E402

_SHARED = calibration.load_shared()
_END = _SHARED["war_end"]
BOOST = str(_END["boost"])
POLL_GAP = _SHARED["timing"]["poll_gap"]
DIALOG_TIMEOUT = _SHARED["timing"]["dialog_timeout"]


def _fold(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _read(name, image=None):
    return calibration.read_line(image or calibration.grab(),
                                 tuple(_END[name]["window"]))


def _shows(name, image=None, word=None):
    want = _fold(word or _END[name]["word"])
    return want in _fold(_read(name, image))


def _centre(name):
    x1, y1, x2, y2 = _END[name]["window"]
    return (x1 + x2) // 2, (y1 + y2) // 2


def _press(name, say):
    point = _centre(name)
    say(f"  {_END[name].get('word', name)} at {point}")
    calibration.click(*point, settle=0.0)
    calibration.park()


def _await(test, timeout=DIALOG_TIMEOUT):
    deadline = time.monotonic() + timeout
    while True:
        if test():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_GAP)


def _keep(image, tag, box=None, pad=40):
    """A picture of the Report (or of one button box with some margin
    around it) for checking the buttons afterwards."""
    try:
        if box is not None:
            x1, y1, x2, y2 = box
            image = image.crop((x1 - pad, y1 - pad, x2 + pad, y2 + pad))
        path = calibration.LOG_DIR / (time.strftime("%Y-%m-%d_%H%M%S")
                                      + f"_war_end_{tag}.png")
        image.save(path)
        return path
    except Exception:
        return None


def report_up(image=None):
    return _shows("report", image) and _shows("ok", image)


def boost_up(image=None):
    return _shows("boost_title", image)


def _boost_chosen():
    image = calibration.grab()
    return (BOOST in _fold(_read("select", image))
            and _fold(_read("cost", image)).isdigit())


def take_boost(say):
    """The Increase WEXP window is open: choose the boost and pay."""
    if not _boost_chosen():
        _press("select", say)
        if not _await(lambda: _shows("option", word=_END["option"]["word"])):
            calibration.snap("war_end_no_option")
            say(f"  the WExp list never showed {_END['option']['word']}; "
                f"closing it with No")
            _press("no", say)
            return False
        _press("option", say)
        if not _await(_boost_chosen):
            calibration.snap("war_end_not_chosen")
            say(f"  the WExp box reads {_read('select')!r} with cost "
                f"{_read('cost')!r}, not {BOOST} %; closing it with No")
            _press("no", say)
            return False
    say(f"  {BOOST} % chosen, {_read('cost')} Force Gem(s)")
    _press("yes", say)
    if _await(lambda: not boost_up()):
        return True
    calibration.snap("war_end_yes_stuck")
    say("  the Increase WEXP window stayed open after Yes (too few Force "
        "Gems?); closing it with No")
    _press("no", say)
    return False


# WEXP UP on the Mission War Report at 1920x1080: where it was clicked when
# the boost was taken on 2026-11-17
WEXP_UP_CLICK = (700, 780)


def handle(verbose=True):
    """Returns None when no Report is up, else whether the boost was taken.
    The Report is closed either way."""
    say = print if verbose else (lambda *a: None)
    image = calibration.grab()
    if not (report_up(image) or boost_up(image)):
        return None
    say("")
    say("WAR END: the Mission War Report is up")
    boosted = False
    kept = _keep(image, "report")
    if kept is not None:
        say(f"  the Report is kept at {kept}")
    if not boost_up(image):
        # the button is always offered, so it is always clicked, nothing read
        say(f"  WEXP UP at {WEXP_UP_CLICK} (hard-coded)")
        calibration.click(*WEXP_UP_CLICK, settle=0.0)
        calibration.park()
        if not _await(boost_up):
            _keep(calibration.grab(), "no_boost_window")
    if boost_up():
        boosted = take_boost(say)
    else:
        say("  WEXP UP was clicked but no Increase WEXP window opened; the "
            "picture after the click is kept beside the Report's")
    _press("ok", say)
    if not _await(lambda: not report_up()):
        calibration.snap("war_end_report_stuck")
        say("  the Report is still up after OK")
    say(f"WAR END: Report closed; boost {'taken' if boosted else 'not taken'}")
    go_home(verbose)
    return boosted


HOME_CHANNEL = int(_SHARED["run"]["home_channel"])


def go_home(verbose=True):
    """Back to the home channel through Escape -> Select Channel (the war
    leaves the character outside it), not a full relog."""
    sys.path.insert(0, str(calibration.HERE.parent / "tools"))
    import world_boss
    if verbose:
        print(f"WAR END: back to the home channel, Channel {HOME_CHANNEL}, "
              f"through Select Channel")
    world_boss.quick_relog(HOME_CHANNEL, verbose)


def main():
    calibration.log_to_file("war_end")
    if "--check" in sys.argv:
        image = calibration.grab()
        for name in ("report", "wexp_up", "ok", "boost_title", "select",
                     "cost", "yes", "no"):
            print(f"  {name:12} {_read(name, image)!r}")
        print(f"  report up {report_up(image)}, boost up {boost_up(image)}")
        crop = _keep(image, "wexp_up_box", tuple(_END["wexp_up"]["window"]))
        print(f"  WEXP UP box {_END['wexp_up']['window']}, clicked at "
              f"{_centre('wexp_up')}; the box with 40px around it: {crop}")
        return
    import row_model
    row_model.start_backup_reader()
    print(f"  result: {handle()}")


if __name__ == "__main__":
    main()
