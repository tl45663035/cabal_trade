import datetime as dt
import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import os as _os_guard
import sys as _sys_guard
_sys_guard.path.insert(0, _os_guard.path.dirname(
    _os_guard.path.abspath(__file__)))
import _no_input_guard

import trade as m

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


def at(text):
    hh, mm = text.split(":")[:2]
    ss = int(text.split(":")[2]) if text.count(":") == 2 else 0
    return dt.datetime(2026, 8, 12, int(hh), int(mm), ss)


check(m.WAR_START_HOURS == (1, 4, 7, 10, 13, 16, 19, 22),
      f"the published schedule is every 3h from 01:00, got "
      f"{m.WAR_START_HOURS}")
check(len(m.WAR_START_HOURS) == 8, "eight wars a day")
check(all((b - a) == 3 for a, b in zip(m.WAR_START_HOURS,
                                       m.WAR_START_HOURS[1:])),
      "every three hours with no gaps")
check(m.WAR_MINUTES == 30, f"a war lasts 30 min, got {m.WAR_MINUTES}")
check(m.WAR_QUIET_BEFORE_END == 60,
      f"stop 1 min before the end, got {m.WAR_QUIET_BEFORE_END}s")
check(m.WAR_QUIET_SECONDS == 300,
      f"stay stopped 5 min, got {m.WAR_QUIET_SECONDS}s")


END_PAD = m.SERVER_CLOCK_UNCERTAINTY


def window_end(start_text):
    return at(start_text) + dt.timedelta(seconds=m.WAR_QUIET_SECONDS + END_PAD)


CASES = [
    ("02:35", "04:29"),
    ("00:00", "01:29"),
    ("04:00", "04:29"),
    ("04:28:59", "04:29"),
    ("04:29", "04:29"),
    ("04:30", "04:29"),
    ("04:33:59", "04:29"),
    ("04:34", "04:29"),
    ("04:34:58", "04:29"),
    ("04:35", "07:29"),
    ("05:00", "07:29"),
    ("06:00", "07:29"),
    ("09:00", "10:29"),
    ("12:00", "13:29"),
    ("15:00", "16:29"),
    ("18:00", "19:29"),
    ("21:00", "22:29"),
]
for now, want_start in CASES:
    start, end = m.war_quiet_window(at(now))
    check(start == at(want_start),
          f"at server {now} the window should start {want_start}, got "
          f"{start:%H:%M:%S}")
    check(end == window_end(want_start),
          f"and end {window_end(want_start):%H:%M:%S}, got {end:%H:%M:%S}")
    war_ends = start + dt.timedelta(seconds=m.WAR_QUIET_BEFORE_END)
    check((end - war_ends).total_seconds()
          >= m.WAR_QUIET_SECONDS - m.WAR_QUIET_BEFORE_END,
          f"at {now} the cover must last at least "
          f"{(m.WAR_QUIET_SECONDS - m.WAR_QUIET_BEFORE_END) / 60:.0f} min "
          f"past the war end, got {(end - war_ends).total_seconds() / 60:.1f}")

for now in ("22:35", "23:00", "23:59:59"):
    start, end = m.war_quiet_window(at(now))
    check(start.day == at(now).day + 1 and start.hour == 1
          and start.minute == 29,
          f"at server {now} the next window is 01:29 TOMORROW, got "
          f"{start:%d %H:%M}")
    check((end - start).total_seconds() == m.WAR_QUIET_SECONDS + END_PAD,
          "and it still carries the full window plus the clock uncertainty")

for hour in m.WAR_START_HOURS:
    probe = dt.datetime(2026, 8, 12, hour, 0, 0)
    start, end = m.war_quiet_window(probe)
    war_ends = probe.replace(minute=m.WAR_MINUTES)
    check((end - start).total_seconds() == m.WAR_QUIET_SECONDS + END_PAD,
          f"the {hour:02d}:00 window is 5 min plus the clock uncertainty")
    check((war_ends - start).total_seconds() == m.WAR_QUIET_BEFORE_END,
          f"the {hour:02d}:00 window starts 1 min before the war ends")
    check(start < war_ends < end,
          f"the {hour:02d}:00 war ends INSIDE its quiet window")

for now in ("04:35", "04:36"):
    start, end = m.war_quiet_window(at(now))
    check(end > at(now),
          f"at {now} the returned window must not be over ({end:%H:%M:%S})")


def word(text, left=26, top=1283, width=70, height=22, conf=96.0):
    return m.Word(text=text, conf=conf, left=left, top=top,
                  right=left + width, bottom=top + height)


_real_find_words = m.find_words


def reads(words):
    m.find_words = lambda shot, region, scale=20: list(words)
    try:
        return m.read_server_clock(source=object())
    finally:
        m.find_words = _real_find_words


REAL = [
    ("23:58", (23, 58)),
    ("01:48", (1, 48)),
    ('23:48"', (23, 48)),
    ("19:28 7", (19, 28)),
    ("23:59 *", (23, 59)),
    ("23:489", (23, 48)),
    ("00:00", (0, 0)),
    ("13:36 \ufffd", (13, 36)),
    ("06:24", (6, 24)),
    ("43:21", None),
    ("29:99", None),
    ("Ne", None),
    ("Co", None),
    ("", None),
]
for text, want in REAL:
    got = reads([word(t) for t in text.split()] if text else [])
    if want is None:
        check(got is None, f"{text!r} must not be read as a time, got {got}")
    else:
        check(got is not None and (got.hour, got.minute) == want,
              f"{text!r} should read {want}, got "
              f"{(got.hour, got.minute) if got else None}")

check(reads([word("23:58", conf=10.0)]) is None,
      "a low-confidence reading is not a clock")


_saved = {n: getattr(m, n) for n in
          ("server_now", "leave_shop", "record")}
slept = []
closed = []
try:
    m.leave_shop = lambda verbose=True: closed.append(1) or True
    m.record = lambda *a, **k: None

    def drive(now_text, allowance=0.0):
        slept.clear()
        closed.clear()
        m.server_now = lambda resync=True, verbose=False: at(now_text)
        real_sleep = m.time.sleep
        real_mono = m.time.monotonic
        clock = {"t": 0.0}
        m.time.sleep = lambda s: (slept.append(s),
                                  clock.__setitem__("t", clock["t"] + s))[0]
        m.time.monotonic = lambda: clock["t"]
        try:
            return m.avoid_warlag(allowance=allowance, verbose=False)
        finally:
            m.time.sleep = real_sleep
            m.time.monotonic = real_mono

    waited = drive("02:35")
    check(waited == 0.0, f"at 02:35 nothing should wait, got {waited}")
    check(not closed, "and the shop is not closed")

    waited = drive("04:30")
    check(abs(waited - (240.0 + END_PAD)) < 1.0,
          f"at 04:30 it waits until the window ends, got {waited}")
    check(closed, "and the shop is put back to its default state first")
    check(abs(sum(slept) - (240.0 + END_PAD)) < 1.0,
          f"and actually sleeps that long, got {sum(slept)}")

    waited = drive("04:29")
    check(abs(waited - (m.WAR_QUIET_SECONDS + END_PAD)) < 1.0,
          f"at 04:29 it waits the whole window, got {waited}")

    check(drive("04:27", allowance=0.0) == 0.0,
          "with no allowance, 04:27 is fine to work at")
    check(drive("04:27", allowance=m.WAR_ROW_ALLOWANCE) > 0.0,
          f"with a {m.WAR_ROW_ALLOWANCE}s row allowance, 04:27 is too late to "
          f"start one")
    check(drive("04:20", allowance=m.WAR_ROW_ALLOWANCE) == 0.0,
          "but 04:20 leaves room for a full row")

    waited = drive("04:33:30")
    check(0 < waited <= 30.0 + END_PAD,
          f"at 04:33:30 only the tail of the window remains, got {waited}")

    m.server_now = lambda resync=True, verbose=False: None
    check(m.avoid_warlag(verbose=False) == 0.0,
          "with no clock reading it proceeds rather than blocking")
finally:
    for name, value in _saved.items():
        setattr(m, name, value)

check(m.server_now is _saved["server_now"], "the patched names were restored")

check(m.WAR_ROW_ALLOWANCE >= 127.0,
      f"the row allowance must cover a measured row (~127s), got "
      f"{m.WAR_ROW_ALLOWANCE}")


class _WrongClock:
    offset = dt.timedelta(days=-431, hours=7, minutes=23)

    @staticmethod
    def now():
        return dt.datetime(2026, 8, 8, 17, 35) + _WrongClock.offset


_saved_dt = m.datetime
_saved_sync = m._SERVER_CLOCK_SYNC
_real_find_words2 = m.find_words
try:
    m.datetime = _WrongClock
    readings = []

    for wrong in (dt.timedelta(days=-431, hours=7, minutes=23),
                  dt.timedelta(days=+900),
                  dt.timedelta(hours=-13, minutes=-47),
                  dt.timedelta(0)):
        _WrongClock.offset = wrong
        m.find_words = lambda shot, region, scale=20: [word("04:29")]
        m._SERVER_CLOCK_SYNC = None
        ok = m.sync_server_clock(verbose=False)
        check(ok, f"a clock reading syncs with the wall clock off by {wrong}")
        now = m.server_now(resync=False)
        check(now is not None, "and server_now answers")
        readings.append((now.hour, now.minute))

    check(len(set(readings)) == 1,
          f"server time must be identical however wrong the PC clock is, got "
          f"{readings}")
    check(readings[0] == (4, 29),
          f"and must be what the GAME said (04:29), got {readings[0]}")

    _WrongClock.offset = dt.timedelta(days=+900)
    m.find_words = lambda shot, region, scale=20: [word("04:29")]
    m._SERVER_CLOCK_SYNC = None
    m.sync_server_clock(verbose=False)
    start, end = m.war_quiet_window(m.server_now(resync=False))
    check((start.hour, start.minute) == (4, 29)
          and (end.hour, end.minute) == (4, 34),
          f"the window is 04:29-04:34 regardless of the PC clock, got "
          f"{start:%H:%M}-{end:%H:%M}")

    check(m.SERVER_CLOCK_EPOCH.year < 2026,
          f"SERVER_CLOCK_EPOCH must be a FIXED date, not today's - got "
          f"{m.SERVER_CLOCK_EPOCH}")
finally:
    m.datetime = _saved_dt
    m.find_words = _real_find_words2
    m._SERVER_CLOCK_SYNC = _saved_sync

check(m.datetime is _saved_dt, "datetime was restored")

_saved_sync = m._SERVER_CLOCK_SYNC
_real_mono = m.time.monotonic
try:
    m.find_words = lambda shot, region, scale=20: [word("04:00")]
    clock = {"t": 1000.0}
    m.time.monotonic = lambda: clock["t"]
    m._SERVER_CLOCK_SYNC = None
    m.sync_server_clock(verbose=False)
    before = m.server_now(resync=False)
    clock["t"] += 600.0
    after = m.server_now(resync=False)
    check(abs((after - before).total_seconds() - 600.0) < 1.0,
          f"ten monotonic minutes advance server time by ten minutes, got "
          f"{(after - before).total_seconds()}")
    check((after.hour, after.minute) == (4, 10),
          f"04:00 + 10 min = 04:10, got {after:%H:%M}")
finally:
    m.time.monotonic = _real_mono
    m.find_words = _real_find_words2
    m._SERVER_CLOCK_SYNC = _saved_sync


print(f"warlag_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
