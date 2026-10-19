import datetime
import sys
import time

import calibration

sys.path.append(str(calibration.HERE.parent / "tools"))
import online_clock

_SHARED = calibration.load_shared()
_WAR = _SHARED["war"]
ENABLED = _WAR["enabled"]
START_HOURS = tuple(_WAR["start_hours"])
WAR_MINUTES = _WAR["war_minutes"]
QUIET_FROM_END = _WAR["quiet_from_end"]
QUIET_SECONDS = _WAR["quiet_seconds"]
LAG_POLL = _SHARED["timing"]["server_lag_poll"]
ONLINE_EVERY = float(_WAR["online_every"])
SERVER_UTC_OFFSET = datetime.timedelta(
    hours=float(_SHARED["game_facts"]["server_utc_offset_hours"]))

_ONLINE = None


def now(verbose=False):
    global _ONLINE
    if _ONLINE is None or time.monotonic() - _ONLINE[0] >= ONLINE_EVERY:
        try:
            found = online_clock.online_utc()
            _ONLINE = (time.monotonic(), found)
        except OSError as exc:
            if verbose:
                print(f"  {exc}"
                      + ("; the server time is not known" if _ONLINE is None
                         else f"; going on from the time server's answer "
                              f"{time.monotonic() - _ONLINE[0]:.0f}s ago"))
            if _ONLINE is None:
                return None
    at, found = _ONLINE
    return (found + datetime.timedelta(seconds=time.monotonic() - at)
            + SERVER_UTC_OFFSET).replace(tzinfo=None)


def quiet_window(after):
    best = None
    for day in (-1, 0, 1):
        midnight = (after + datetime.timedelta(days=day)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        for hour in START_HOURS:
            ends = midnight + datetime.timedelta(hours=hour,
                                                 minutes=WAR_MINUTES)
            start = ends + datetime.timedelta(seconds=QUIET_FROM_END)
            end = start + datetime.timedelta(seconds=QUIET_SECONDS)
            if end <= after:
                continue
            if best is None or start < best[0]:
                best = (start, end)
    return best


def next_war(after, lasts):
    best = None
    for day in (-1, 0, 1):
        midnight = (after + datetime.timedelta(days=day)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        for hour in START_HOURS:
            start = midnight + datetime.timedelta(hours=hour)
            if start + lasts > after and (best is None or start < best):
                best = start
    return best


def avoid(allowance=0.0, verbose=True, waiting=None):
    say = print if verbose else (lambda *a: None)
    if not ENABLED:
        return 0.0
    at = now(verbose=verbose)
    if at is None:
        say("  the server time is not known, so the war schedule cannot be "
            "followed this pass.")
        return 0.0
    start, end = quiet_window(at)
    if at < start and (start - at).total_seconds() > allowance:
        return 0.0
    wait = (end - at).total_seconds()
    if wait <= 0:
        return 0.0
    war_ends = start - datetime.timedelta(seconds=QUIET_FROM_END)
    reason = (f"a war ends in {(war_ends - at).total_seconds() / 60:.1f} min"
              if at < war_ends else
              f"a war ended {(at - war_ends).total_seconds() / 60:.1f} min ago")
    say("")
    say(f"WAR LAG: {reason} (server {at:%H:%M:%S}). Going to the default "
        f"state and waiting {wait / 60:.1f} min, until server {end:%H:%M:%S}.")
    try:
        calibration.close_everything(verbose=verbose)
    except Exception as exc:
        say(f"  could not reach the default state before waiting ({exc}); "
            f"waiting anyway.")
    calibration.table_lost()
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if waiting is not None:
            waiting()
        time.sleep(max(0.0, min(LAG_POLL, deadline - time.monotonic())))
    say(f"WAR LAG: done waiting; resuming.")
    return wait


def main():
    calibration.log_to_file("war")
    at = now(verbose=True)
    if at is None:
        sys.exit(1)
    start, end = quiet_window(at)
    print(f"  server now      {at:%H:%M:%S}")
    print(f"  war hours       {list(START_HOURS)}, {WAR_MINUTES} min long")
    print(f"  next quiet from {start:%H:%M:%S} to {end:%H:%M:%S}")
    print(f"  that is in      {(start - at).total_seconds() / 60:.1f} min")
    print(f"  enabled         {ENABLED}")


if __name__ == "__main__":
    main()
