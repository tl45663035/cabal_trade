"""Dungeons and trading in turns: a batch of dungeon runs, then one trading
pass, over and over.

    py tools/combo.py --config cang [--batch 5]

A new, separate entry point. driver.py, dungeon.py and supervise.py are not
changed; this file only calls them:

  * a dungeon batch is `src_1080p/combo_dungeon.py`, its own process, which
    runs up to --batch runs (CA levels first, then the rotation) and exits;
  * a trading pass is `driver.py relist FROM TO 0.01`, its own process, which
    walks the board once (relist, collect, resupply) and exits; supervise's
    watch() looks after it while it runs (disconnects, profit reports);
  * when either dies, supervise's recover() puts the game back (relog, the
    work tab, the default state) and the loop carries on, trying for as long
    as supervise.recover_retries x recover_wait allows.

Once every dungeon is at its daily limit, the loop is trading passes only,
and a batch is tried again after each pass so the next server day picks the
dungeons up by itself. Ctrl pressed twice stops it, as it stops the others.
Events go to logs/supervise.log, marked "combo:".
"""
import json
import subprocess
import sys
import time

import supervise as S  # sets CABAL_CONFIG from --config and imports calibration

calibration = S.calibration
K = S.K
CONFIG = S.CONFIG
SRC = S.SRC
LOGS = S.LOGS
BATCHER = SRC / "combo_dungeon.py"
RESULT = LOGS / "combo_result.json"
RUN = calibration.load_shared()["run"]
DUNGEON = calibration.load_shared().get("dungeon") or {}
KEYBOARD = "interrupted from the keyboard"


def muted(proc_wait):
    """Combo's own Ctrl watcher is deaf while a child runs: the child sends
    Ctrl+number keys of its own, and its own watcher hears a Ctrl stop."""
    def run(*args, **kwargs):
        with calibration._own_ctrl():
            return proc_wait(*args, **kwargs)
    return run


def _recovery_child(*args, **kwargs):
    """supervise's recovery steps (list, collect, stock-resume ...) run
    here. Some of their failures are only logged ("carrying on"), so a Ctrl
    stop is raised the moment a child reports it."""
    code, out = muted(_RUN_CHILD)(*args, **kwargs)
    if code and KEYBOARD in (out or ""):
        raise KeyboardInterrupt(KEYBOARD)
    return code, out


_RUN_CHILD = S.run_child
S.run_child = _recovery_child


def cancel_if_keyboard(exc):
    """A child stopped with Ctrl ends combo, never a retry."""
    if KEYBOARD in str(exc):
        raise S.Cancelled(f"cancelled with Ctrl x{S.STOP_PRESSES}") from exc


def _arg(name, default=None):
    for index, arg in enumerate(sys.argv):
        if arg.startswith(f"{name}="):
            return arg.split("=", 1)[1]
        if arg == name and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return default


BATCH = int(_arg("--batch", DUNGEON.get("trade_every_runs", 5)))
# trading passes after each dungeon batch
PASSES = int(_arg("--passes", DUNGEON.get("trade_passes", 1)))
PASS_MINUTES = "0.01"
# no trading passes until every dungeon is at its daily limit once
DUNGEONS_FIRST = "--dungeons-first" in sys.argv


def event(reason, state):
    S.event(f"combo: {reason}", state)


def read_result():
    try:
        return json.loads(RESULT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def dungeon_batch(first):
    S.no_driver_alive()
    RESULT.unlink(missing_ok=True)
    argv = [sys.executable, str(BATCHER), "--config", CONFIG,
            "--batch", str(BATCH)]
    if first:
        argv.append("--first")
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = S.SW_MINIMIZE
    proc = subprocess.Popen(argv, cwd=SRC,
                            creationflags=subprocess.CREATE_NEW_CONSOLE,
                            startupinfo=info)
    code = muted(proc.wait)()
    logs = sorted(LOGS.glob("*_dungeon.log"), key=lambda q: q.stat().st_mtime)
    if logs and (f"Ctrl {S.STOP_PRESSES} times: stopping" in S.read(logs[-1])
                 or KEYBOARD in S.read(logs[-1])[-2000:]):
        raise KeyboardInterrupt("the dungeon batch was stopped with Ctrl")
    return code, read_result()


MEASURED = {"done": False}


def pass_logs():
    return set(LOGS.glob("*_relist.log"))


def trading_pass():
    S.no_driver_alive()
    before = pass_logs()
    argv = [sys.executable, str(S.DRIVER), "--config", CONFIG, "relist",
            str(RUN["relist_from"]), str(RUN["relist_to"]), PASS_MINUTES]
    if MEASURED["done"]:
        argv.append(S.MEASURED_FLAG)
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = S.SW_MINIMIZE
    proc = subprocess.Popen(argv, cwd=S.ROOT,
                            creationflags=subprocess.CREATE_NEW_CONSOLE,
                            startupinfo=info)
    with calibration._own_ctrl():
        return _watch_pass(proc, before)


def _watch_pass(proc, before):
    log = None
    for _ in range(int(K["launch_wait"])):
        time.sleep(K["launch_poll"])
        fresh = sorted(pass_logs() - before)
        if fresh:
            log = fresh[-1]
            break
        if proc.poll() is not None:
            raise S.Stop(f"driver.py exited {proc.returncode} before writing "
                         f"a run log")
    else:
        S.kill(proc.pid)
        raise S.Stop(f"no new run log {K['launch_wait']}s after launching "
                     f"driver.py; pid {proc.pid} killed")
    S._CHILD = proc
    event(f"trading pass started (pid {proc.pid}, {log.name})", "alive")
    reason = S.watch(proc.pid, log)
    text = S.read(log)
    if S.START_UP_DONE.search(text):
        MEASURED["done"] = True
    if proc.poll() == 0 and "minute(s) are up after pass" in text:
        S.report_due(log, force=True)
        return None, log
    return reason, log


def recover(reason, log):
    """supervise's own recovery, retried like supervise retries it."""
    held = failed = 0
    while True:
        try:
            S.recover(reason, S.read(log) if log else "", log=log,
                      watched=True)
            return
        except S.Cancelled:
            raise
        except S.Held as exc:
            cancel_if_keyboard(exc)
            held += 1
            if held > K["held_retries"]:
                raise
            event(f"recovery held up: {str(exc)[:K['reason_width']]}; "
                  f"waiting {K['held_wait']}s", "dead")
            time.sleep(K["held_wait"])
        except S.Stop as exc:
            cancel_if_keyboard(exc)
            failed += 1
            if failed > K["recover_retries"]:
                event("recovery failed "
                      f"{failed} times; one full relog", "dead")
                S.relog()
                return
            event(f"recovery attempt {failed} failed: "
                  f"{str(exc)[:K['reason_width']]}; again in "
                  f"{K['recover_wait']}s", "dead")
            time.sleep(K["recover_wait"])


def screen_recover(why):
    """After a dungeon batch died: get back in and to the default state."""
    event(f"dungeon batch: {why}; getting back in", "dead")
    for attempt in range(1, int(K["recover_retries"]) + 2):
        try:
            S.get_in()
            calibration.close_everything(True)
            return
        except S.Stop as exc:
            cancel_if_keyboard(exc)
            event(f"getting back in, attempt {attempt} failed: "
                  f"{str(exc)[:K['reason_width']]}; again in "
                  f"{K['recover_wait']}s", "dead")
            time.sleep(K["recover_wait"])
    S.relog()


def main():
    calibration.log_to_file("combo")
    print(f"  screen {calibration.require_screen()}")
    # its own Ctrl watcher covers recovery, relogs and the waits between
    # children; while a child runs it is muted (see muted()) and the child's
    # Ctrl stop, read from its log or exit, stops combo instead.
    calibration.watch_for_stop()
    event(f"started: batches of {BATCH} dungeon run(s), {PASSES} trading "
          f"pass(es) of rows {RUN['relist_from']}-{RUN['relist_to']} between "
          f"them",
          "alive")
    first = True
    done_day = None
    crashes = 0
    hold_until = 0.0
    try:
        if S.driver_pids():
            raise S.Stop("driver.py is already running; stop it (or "
                         "supervise) before starting combo")
        while True:
            if time.time() >= hold_until:
                code, result = dungeon_batch(first)
                first = False
                if code != 0 or result is None:
                    crashes += 1
                    screen_recover(f"exited {code} without a result "
                                   f"({crashes} in a row)")
                    if crashes >= 3:
                        hold_until = time.time() + 3600
                        crashes = 0
                        event("3 dungeon batches in a row died; trading "
                              "only for the next hour", "alive")
                else:
                    crashes = 0
                    if result["ran"] or result["day"] != done_day:
                        event(f"dungeon batch: {result['ran']} run(s), "
                              f"{'more left' if result['left'] else 'none left'}"
                              f" on server day {result['day']}", "alive")
                    if not result["left"]:
                        done_day = result["day"]
            if (DUNGEONS_FIRST and done_day is None
                    and time.time() >= hold_until):
                if result and result["left"] and not result["ran"]:
                    time.sleep(K["recover_wait"])
                continue
            for number in range(1, PASSES + 1):
                calibration.close_everything(True)
                if PASSES > 1:
                    event(f"trading pass {number} of {PASSES}", "alive")
                reason, log = trading_pass()
                if reason is not None and KEYBOARD in reason:
                    raise KeyboardInterrupt(KEYBOARD)
                if reason is not None:
                    event(f"trading pass died: {reason[:K['reason_width']]}",
                          "dead")
                    recover(reason, log)
    except KeyboardInterrupt:
        event(f"cancelled with Ctrl x{S.STOP_PRESSES}", "dead")
        return 0
    except S.Cancelled as exc:
        event(f"cancelled: {exc}", "dead")
        return 0
    except S.Stop as exc:
        event(f"stopped: {exc}", "dead")
        return 1
    except Exception as exc:
        import traceback
        traceback.print_exc()
        event(f"crashed: {type(exc).__name__}: {exc}"[:K["reason_width"]],
              "dead")
        return 1


if __name__ == "__main__":
    sys.exit(main())
