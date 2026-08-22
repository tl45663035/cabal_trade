import datetime
import re
import sys
import time

import calibration

_SHARED = calibration.load_shared()
_WAR = _SHARED["war"]
ENABLED = _WAR["enabled"]
START_HOURS = tuple(_WAR["start_hours"])
WAR_MINUTES = _WAR["war_minutes"]
QUIET_BEFORE_END = _WAR["quiet_before_end"]
QUIET_SECONDS = _WAR["quiet_seconds"]
UNCERTAINTY = _WAR["clock_uncertainty"]
RESYNC = _WAR["clock_resync"]
CONFIRM_PAUSE = _WAR["clock_confirm_pause"]
MAX_DRIFT = _WAR["clock_max_drift"]
LAG_POLL = _SHARED["timing"]["server_lag_poll"]
_CLOCK = re.compile(_SHARED["text"]["server_clock"])
EPOCH = datetime.datetime.strptime(
    _SHARED["game_facts"]["clock_epoch"], "%Y-%m-%d")

_SYNC = None


def clock_box():
    return calibration._box(tuple(calibration._REG["server_clock"]))


def read_clock(image=None, verbose=False):
    image = image if image is not None else calibration.grab()
    box = clock_box()
    words = calibration.ocr(image, box)
    seen = " ".join(t for t, _c, _p in words)
    for text, _c, _p in words:
        found = _CLOCK.match(text.strip())
        if found is not None:
            hour, minute = int(found.group(1)), int(found.group(2))
            return datetime.time(hour, minute)
    if verbose:
        print(f"  the server clock at {box} did not read a time ({seen!r})")
    return None


def sync(verbose=False):
    global _SYNC
    reading = read_clock(verbose=verbose)
    if reading is None:
        return False
    stamped = EPOCH + datetime.timedelta(
        hours=reading.hour, minutes=reading.minute, seconds=UNCERTAINTY)
    if _SYNC is None:
        time.sleep(CONFIRM_PAUSE)
        second = read_clock(verbose=False)
        if second is None:
            if verbose:
                print("  the server clock did not read a second time; not "
                      "anchoring on one reading.")
            return False
        gap = abs((second.hour * 60 + second.minute)
                  - (reading.hour * 60 + reading.minute))
        if gap > 1:
            if verbose:
                print(f"  two readings disagree ({reading:%H:%M} then "
                      f"{second:%H:%M}); not anchoring on either.")
            return False
    running = now(resync=False)
    if running is not None:
        drift = abs((stamped - running).total_seconds())
        if drift > MAX_DRIFT:
            if verbose:
                print(f"  the clock read {reading:%H:%M}, {drift / 60:.1f} min "
                      f"from the running clock ({running:%H:%M:%S}); keeping "
                      f"the old anchor.")
            return False
    _SYNC = (time.monotonic(), stamped)
    if verbose:
        print(f"  server clock {reading:%H:%M} (+{UNCERTAINTY}s for the "
              f"unshown seconds)")
    return True


def now(resync=True, verbose=False):
    global _SYNC
    stale = _SYNC is None or time.monotonic() - _SYNC[0] > RESYNC
    if stale and resync:
        sync(verbose=verbose)
    if _SYNC is None:
        return None
    at, stamped = _SYNC
    return stamped + datetime.timedelta(seconds=time.monotonic() - at)


def quiet_window(after):
    best = None
    for day in (-1, 0, 1):
        midnight = (after + datetime.timedelta(days=day)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        for hour in START_HOURS:
            ends = midnight + datetime.timedelta(hours=hour,
                                                 minutes=WAR_MINUTES)
            start = ends - datetime.timedelta(seconds=QUIET_BEFORE_END)
            end = start + datetime.timedelta(
                seconds=QUIET_SECONDS + UNCERTAINTY)
            if end <= after:
                continue
            if best is None or start < best[0]:
                best = (start, end)
    return best


def seconds_until_quiet(verbose=False):
    at = now(verbose=verbose)
    if at is None:
        return None
    start, _end = quiet_window(at)
    return (start - at).total_seconds()


def avoid(allowance=0.0, verbose=True):
    say = print if verbose else (lambda *a: None)
    if not ENABLED:
        return 0.0
    at = now(verbose=verbose)
    if at is None:
        say("  the server clock has never read, so the war schedule cannot "
            "be followed this run.")
        return 0.0
    start, end = quiet_window(at)
    if at < start and (start - at).total_seconds() > allowance:
        return 0.0
    wait = (end - at).total_seconds()
    if wait <= 0:
        return 0.0
    war_ends = start + datetime.timedelta(seconds=QUIET_BEFORE_END)
    reason = (f"a war ends in {(war_ends - at).total_seconds() / 60:.1f} min"
              if at < start else "a war has just ended")
    say("")
    say(f"WAR LAG: {reason} (server {at:%H:%M:%S}). Going to the default "
        f"state and waiting {wait / 60:.1f} min, until server {end:%H:%M:%S}.")
    try:
        calibration.close_everything(verbose=verbose)
    except Exception as exc:
        say(f"  could not reach the default state before waiting ({exc}); "
            f"waiting anyway.")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(min(LAG_POLL, deadline - time.monotonic()))
    say(f"WAR LAG: done waiting; resuming.")
    return wait


def main():
    calibration.log_to_file("war")
    from open_inventory import focus_game
    if not focus_game():
        print("could not bring the game to the foreground.")
        sys.exit(1)
    print(f"server clock region {clock_box()}")
    if not sync(verbose=True):
        print("  the clock did not anchor; nothing to schedule against.")
        sys.exit(1)
    at = now()
    start, end = quiet_window(at)
    print(f"  server now      {at:%H:%M:%S}")
    print(f"  war hours       {list(START_HOURS)}, {WAR_MINUTES} min long")
    print(f"  next quiet from {start:%H:%M:%S} to {end:%H:%M:%S}")
    print(f"  that is in      {(start - at).total_seconds() / 60:.1f} min")
    print(f"  enabled         {ENABLED}")


if __name__ == "__main__":
    main()
