"""Sitting out the lag around the end of a server war.

Wars start every three hours on the server clock and last 30 minutes. The rule
is: stop one minute before a war ENDS, stay stopped five minutes, resume. So a
war at 04:00 ends 04:30 and the quiet window is 04:29 -> 04:34.

The parts worth testing are the schedule arithmetic and the clock parsing, and
neither needs the game. The OCR strings below are real -- taken from a sweep of
100 saved 2560x1440 frames -- including the malformed ones, because those are
the common case rather than the exception.
"""
import datetime as dt
import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m  # noqa: E402

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


def at(text):
    """A server datetime on an arbitrary Wednesday."""
    hh, mm = text.split(":")[:2]
    ss = int(text.split(":")[2]) if text.count(":") == 2 else 0
    return dt.datetime(2026, 8, 12, int(hh), int(mm), ss)


# -- the schedule itself ---------------------------------------------------
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


# -- which window is in force ----------------------------------------------
# (server time now, expected window start, expected window end)
# The window END now carries the clock's own uncertainty. The believed time
# runs up to SERVER_CLOCK_UNCERTAINTY seconds AHEAD of the truth (a reading is
# stamped at the end of its minute), which is deliberate and safe for the
# START -- it stops early. Applied to the END it would resume that much EARLY,
# into the tail of the lag. So the window is extended by the same uncertainty
# it is measured with, and the operator's "5 minutes" is a floor, not a target.
END_PAD = m.SERVER_CLOCK_UNCERTAINTY


def window_end(start_text):
    """The expected end for a window starting at `start_text`."""
    return at(start_text) + dt.timedelta(seconds=m.WAR_QUIET_SECONDS + END_PAD)


CASES = [
    # Well before a war ends: the next window is the one that war opens.
    ("02:35", "04:29"),   # the operator's own example
    ("00:00", "01:29"),
    ("04:00", "04:29"),   # war just STARTED; the window is its end
    ("04:28:59", "04:29"),
    # Inside the window.
    ("04:29", "04:29"),
    ("04:30", "04:29"),   # the moment the war ends
    ("04:33:59", "04:29"),
    # The window now closes at :34:59, so :34 is still INSIDE it -- the extra
    # second is the clock uncertainty being paid back at the end rather than
    # stolen from the cover.
    ("04:34", "04:29"),
    ("04:34:58", "04:29"),
    # The instant it truly closes, attention moves to the next war.
    ("04:35", "07:29"),
    ("05:00", "07:29"),
    # Every war in the day resolves to its own window.
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
    # The operator's rule, stated independently of how the end is computed:
    # cover must run to at least four minutes past the war ending.
    war_ends = start + dt.timedelta(seconds=m.WAR_QUIET_BEFORE_END)
    check((end - war_ends).total_seconds()
          >= m.WAR_QUIET_SECONDS - m.WAR_QUIET_BEFORE_END,
          f"at {now} the cover must last at least "
          f"{(m.WAR_QUIET_SECONDS - m.WAR_QUIET_BEFORE_END) / 60:.0f} min "
          f"past the war end, got {(end - war_ends).total_seconds() / 60:.1f}")

# Midnight wrap: after the last war of the day, the next window belongs to
# TOMORROW. Getting this wrong parks the script for 22 hours.
for now in ("22:35", "23:00", "23:59:59"):
    start, end = m.war_quiet_window(at(now))
    check(start.day == at(now).day + 1 and start.hour == 1
          and start.minute == 29,
          f"at server {now} the next window is 01:29 TOMORROW, got "
          f"{start:%d %H:%M}")
    check((end - start).total_seconds() == m.WAR_QUIET_SECONDS + END_PAD,
          "and it still carries the full window plus the clock uncertainty")

# The window is always exactly WAR_QUIET_SECONDS, and always ends
# WAR_QUIET_SECONDS - WAR_QUIET_BEFORE_END after the war finishes.
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

# Never returns a window that has already closed.
for now in ("04:35", "04:36"):
    start, end = m.war_quiet_window(at(now))
    check(end > at(now),
          f"at {now} the returned window must not be over ({end:%H:%M:%S})")


# -- reading the clock -----------------------------------------------------
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


# Real OCR output from the frame sweep. The trailing junk is not noise to be
# cleaned up later -- it is what this crop produces most of the time, and a
# whole-string comparison would reject the majority of GOOD readings.
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
    # Absurd readings must be refused, not believed: this one really happened
    # ('43:21' for 13:21), and an hour of 43 would move the whole schedule.
    ("43:21", None),
    ("29:99", None),
    # Nothing legible at all -- a dialog over the corner.
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

# Low-confidence words are dropped before parsing.
check(reads([word("23:58", conf=10.0)]) is None,
      "a low-confidence reading is not a clock")


# -- deciding whether to wait ----------------------------------------------
# avoid_warlag is driven with the clock and the sleep replaced, so the decision
# is tested without a five-minute wait or a game.
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

    # Far from any window: no wait, no shop closed.
    waited = drive("02:35")
    check(waited == 0.0, f"at 02:35 nothing should wait, got {waited}")
    check(not closed, "and the shop is not closed")

    # Inside the window: waits until it ends.
    waited = drive("04:30")
    check(abs(waited - (240.0 + END_PAD)) < 1.0,
          f"at 04:30 it waits until the window ends, got {waited}")
    check(closed, "and the shop is put back to its default state first")
    check(abs(sum(slept) - (240.0 + END_PAD)) < 1.0,
          f"and actually sleeps that long, got {sum(slept)}")

    # Right at the start of the window: the full five minutes.
    waited = drive("04:29")
    check(abs(waited - (m.WAR_QUIET_SECONDS + END_PAD)) < 1.0,
          f"at 04:29 it waits the whole window, got {waited}")

    # The allowance is the point: a row that takes ~150s must not START at
    # 04:27, because it would still be running when the window opens.
    check(drive("04:27", allowance=0.0) == 0.0,
          "with no allowance, 04:27 is fine to work at")
    check(drive("04:27", allowance=m.WAR_ROW_ALLOWANCE) > 0.0,
          f"with a {m.WAR_ROW_ALLOWANCE}s row allowance, 04:27 is too late to "
          f"start one")
    check(drive("04:20", allowance=m.WAR_ROW_ALLOWANCE) == 0.0,
          "but 04:20 leaves room for a full row")

    # Never waits past the end of the window.
    waited = drive("04:33:30")
    check(0 < waited <= 30.0 + END_PAD,
          f"at 04:33:30 only the tail of the window remains, got {waited}")

    # An unreadable clock must not silently disable the schedule, nor hang.
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


# -- the machine's clock must not matter -----------------------------------
# The operator's PC keeps bad time. A dependency on it here would be a SILENT
# one: a wrong wall clock does not raise, it just stops the script for a war
# that is not happening and works through one that is.
#
# So the schedule is driven off the game's HUD for the time of day and
# time.monotonic() for elapsed seconds. This proves it, by moving the wall
# clock to absurd values and checking nothing downstream shifts.
class _WrongClock:
    """A datetime whose now() is wrong by days."""
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
        # The game says 04:29. Whatever the PC believes.
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

    # ... and the window derived from it is the same too.
    _WrongClock.offset = dt.timedelta(days=+900)
    m.find_words = lambda shot, region, scale=20: [word("04:29")]
    m._SERVER_CLOCK_SYNC = None
    m.sync_server_clock(verbose=False)
    start, end = m.war_quiet_window(m.server_now(resync=False))
    check((start.hour, start.minute) == (4, 29)
          and (end.hour, end.minute) == (4, 34),
          f"the window is 04:29-04:34 regardless of the PC clock, got "
          f"{start:%H:%M}-{end:%H:%M}")

    # The epoch is deliberately not today.
    check(m.SERVER_CLOCK_EPOCH.year < 2026,
          f"SERVER_CLOCK_EPOCH must be a FIXED date, not today's - got "
          f"{m.SERVER_CLOCK_EPOCH}")
finally:
    m.datetime = _saved_dt
    m.find_words = _real_find_words2
    m._SERVER_CLOCK_SYNC = _saved_sync

check(m.datetime is _saved_dt, "datetime was restored")

# Elapsed time comes from the monotonic counter, so a wall clock that JUMPS
# mid-run (NTP, daylight saving, a manual fix) cannot move the schedule.
_saved_sync = m._SERVER_CLOCK_SYNC
_real_mono = m.time.monotonic
try:
    m.find_words = lambda shot, region, scale=20: [word("04:00")]
    clock = {"t": 1000.0}
    m.time.monotonic = lambda: clock["t"]
    m._SERVER_CLOCK_SYNC = None
    m.sync_server_clock(verbose=False)
    before = m.server_now(resync=False)
    clock["t"] += 600.0                     # ten monotonic minutes pass
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
