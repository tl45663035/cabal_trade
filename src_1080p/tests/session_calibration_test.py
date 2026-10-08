import contextlib
import datetime
import importlib.util
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_session_cal_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import driver

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


saved_argv = sys.argv
sys.argv = ["driver.py", "--config", "bot", "--measured"]
check("--measured is a flag, not a command: the plain arguments stay empty", driver._plain_argv() == [],
      str(driver._plain_argv()))
sys.argv = saved_argv

measured = []
saved = (driver.row_model.start_backup_reader, driver.inv.focus_game, calibration.main, calibration.load,
         driver.war.ENABLED, driver._MEASURED, driver.REPAIR, driver.SESSION_MEASURED)
driver.row_model.start_backup_reader = lambda *a, **k: None
driver.inv.focus_game = lambda *a, **k: True
calibration.main = lambda *a, **k: measured.append("measured")
calibration.load = lambda *a, **k: {"resolution": "1920x1080", "measured_at": "2026-10-07T17:20:50"}
driver.war.ENABLED = False
try:
    for flag in (True, False):
        measured.clear()
        driver._MEASURED, driver.REPAIR, driver.SESSION_MEASURED = False, False, flag
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            driver.initialise(verbose=True)
        if flag:
            check("a run the supervisor starts with --measured does not walk the calibration again",
                  measured == [] and "using that calibration" in said.getvalue(), said.getvalue().strip()[:160])
        else:
            check("a run started without it measures the screen as before", measured == ["measured"],
                  said.getvalue().strip()[:160])
finally:
    (driver.row_model.start_backup_reader, driver.inv.focus_game, calibration.main, calibration.load,
     driver.war.ENABLED, driver._MEASURED, driver.REPAIR, driver.SESSION_MEASURED) = saved

spec = importlib.util.spec_from_file_location("supervise", SRC.parent / "tools" / "supervise.py")
sup = importlib.util.module_from_spec(spec)
sys.argv = ["supervise.py", "--config", "bot", "--plan"]
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(sup)
sys.argv = saved_argv
work = Path(tempfile.mkdtemp(prefix="cabal_session_logs_"))
got_going = work / "2026-10-07_171736_run.log"
got_going.write_text("  measuring this screen before touching anything\nrelisting rows 1-25 for 9999 minute(s)\n\n-- pass 1 --\n",
                     encoding="utf-8")
died_early = work / "2026-10-07_172000_run.log"
died_early.write_text("  measuring this screen before touching anything\nTraceback (most recent call last):\n",
                      encoding="utf-8")
started = datetime.datetime(2026, 10, 7, 17, 16, 55)
saved = (sup.SESSION_STARTED, calibration.load)
sup.SESSION_STARTED = started
try:
    calibration.load = lambda *a, **k: {"measured_at": "2026-10-07T17:20:50"}
    check("a relaunch after a run that got going, measured in this session, reuses the calibration",
          sup.driver_argv(got_going)[-1] == "--measured", str(sup.driver_argv(got_going)[-3:]))
    check("a relaunch after a run that died before its first pass measures again",
          "--measured" not in sup.driver_argv(died_early), str(sup.driver_argv(died_early)[-3:]))
    check("the first launch of a session, with no run before it, measures",
          "--measured" not in sup.driver_argv(None))
    calibration.load = lambda *a, **k: {"measured_at": "2026-10-07T12:07:40"}
    check("a calibration from before this supervisor started is not reused",
          "--measured" not in sup.driver_argv(got_going), str(sup.driver_argv(got_going)[-3:]))
    calibration.load = lambda *a, **k: {}
    check("no calibration on file means measuring", "--measured" not in sup.driver_argv(got_going))
finally:
    sup.SESSION_STARTED, calibration.load = saved

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
