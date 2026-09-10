import io
import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


def run(lines, clock=None):
    console, handle = io.StringIO(), io.StringIO()
    real = m.time.monotonic
    if clock is not None:
        m.time.monotonic = lambda: clock[0]
    try:
        tee = m._Tee(console, handle)
        for line in lines:
            tee.write(line)
    finally:
        m.time.monotonic = real
    return console.getvalue(), handle.getvalue()


TEXT = ["first action\n", "\n", "second action\n", "multi\nline\n",
        "partial", " continuation\n"]
console, logged = run(TEXT)
check(console == "".join(TEXT),
      f"the console text must be byte-identical, got {console!r}")

body = logged.splitlines()
check(all(l.startswith("[") for l in body if l.strip()),
      f"every non-blank log line is stamped, got {body}")
check(any(l == "" for l in body),
      "blank separator lines are left bare rather than stamped")

check(sum(1 for l in body if "continuation" in l) == 1
      and body[-1].count("[") == 1,
      f"a line split across writes is stamped once, got {body[-1]!r}")


clock = [1000.0]
console2, handle2 = io.StringIO(), io.StringIO()
real = m.time.monotonic
try:
    m.time.monotonic = lambda: clock[0]
    tee = m._Tee(console2, handle2)
    tee.write("start\n")
    clock[0] += 12.5
    tee.write("after twelve and a half\n")
    clock[0] += 100.0
    tee.write("after a hundred more\n")
finally:
    m.time.monotonic = real

rows = [l for l in handle2.getvalue().splitlines() if l.strip()]
check(len(rows) == 3, f"three stamped lines, got {rows}")


def nums(line):
    head = line[1:line.index("]")]
    elapsed, delta = head.split("+")
    return float(elapsed), float(delta)


e0, d0 = nums(rows[0])
e1, d1 = nums(rows[1])
e2, d2 = nums(rows[2])
check(abs(e0) < 0.05 and abs(d0) < 0.05,
      f"the first line is at zero elapsed, got {e0} +{d0}")
check(abs(e1 - 12.5) < 0.05,
      f"elapsed is measured from the log opening, got {e1}")
check(abs(d1 - 12.5) < 0.05,
      f"delta is the gap since the previous line, got {d1}")
check(abs(e2 - 112.5) < 0.05, f"elapsed accumulates, got {e2}")
check(abs(d2 - 100.0) < 0.05,
      f"delta is the LAST gap, not the total, got {d2}")

class _JumpingClock:
    @staticmethod
    def now():
        raise AssertionError(
            "the timing prefix must not consult the wall clock; this machine "
            "keeps bad time and that is the whole reason it uses monotonic()")


saved_dt = m.datetime
try:
    m.datetime = _JumpingClock
    console3, handle3 = run(["a\n", "b\n"])
    check(all(l.startswith("[") for l in handle3.splitlines() if l.strip()),
          "stamping works with the wall clock unavailable")
except AssertionError as exc:
    check(False, str(exc))
finally:
    m.datetime = saved_dt

check(m.datetime is saved_dt, "datetime was restored")


import inspect

source = inspect.getsource(m.start_run_log)
check("timing" in source and "delta" in source,
      "start_run_log writes a legend for the timing columns")
check("MONOTONIC" in source or "monotonic" in source,
      "and says the clock is monotonic, so nobody reads them as wall times")


print(f"log_timing_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
