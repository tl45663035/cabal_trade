import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TREE = ROOT / "src_1080p"
sys.path.insert(0, str(TREE))

import calibration
from open_inventory import press

_S = calibration.load_shared()
POLL = float(_S["timing"]["retry_gap"])
SETTLE = float(_S["recovery"]["world_timeout"])
PRESSES = int(_S["run"]["stop_key_presses"])
WINDOW = float(_S["run"]["stop_key_window"])
HOLD = float(_S["run"]["stop_key_hold"])
CALL_TIMEOUT = float(_S["timing"]["dialog_timeout"])
GIFT_MARKER = "the gift box at"


def newest_log():
    logs = sorted(TREE.glob("logs/*_run.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def live_pid():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process"
             " | Where-Object { $_.Name -like 'python*'"
             " -and $_.CommandLine -like '*driver.py*' }"
             " | Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=CALL_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        return None
    found = [int(w) for w in out.stdout.split() if w.strip().isdigit()]
    return found[0] if found else None


def gifts_opened(log):
    try:
        return log.read_text(encoding="utf-8", errors="replace").count(GIFT_MARKER)
    except OSError:
        return 0


def wait_for_gift_box(log, was, verbose=True):
    while True:
        if live_pid() is None:
            print("  the run ended on its own before the gift box")
            return False
        now = gifts_opened(log)
        if now > was:
            if verbose:
                print(f"  the gift box is open ({now} this run); "
                      f"the shop is shut and the character parked")
            return True
        time.sleep(POLL)


def press_the_stop_key(verbose=True):
    vk = _S["input"]["VK_CONTROL"]
    gap = WINDOW / (PRESSES + 2)
    for _ in range(PRESSES):
        press(vk, hold=HOLD)
        time.sleep(gap)
    if verbose:
        print(f"  Ctrl {PRESSES} times within {WINDOW:g}s, each held {HOLD:g}s "
              f"so the run's stop_key_poll cannot miss it")


def wait_for_exit(pid, verbose=True):
    deadline = time.monotonic() + SETTLE
    while time.monotonic() < deadline:
        if live_pid() != pid:
            if verbose:
                print(f"  pid {pid} is gone")
            return True
        time.sleep(POLL)
    print(f"  pid {pid} is still up after {SETTLE:g}s; not touching "
          f"the screen while it may still be driving")
    return False


def cleanly_terminate(verbose=True):
    log = newest_log()
    pid = live_pid()
    if pid is None or log is None:
        print("  no run is live; nothing to terminate")
        return False
    print("")
    print(f"stopping pid {pid} at the next gift box:")
    if not wait_for_gift_box(log, gifts_opened(log), verbose=verbose):
        return False
    press_the_stop_key(verbose=verbose)
    if not wait_for_exit(pid, verbose=verbose):
        return False
    print("")
    print("returning the game to its default state:")
    calibration.close_gift_window(verbose=verbose)
    calibration.close_everything(verbose=verbose)
    print(f"  done; the run wrote {log.name}")
    return True


if __name__ == "__main__":
    sys.exit(0 if cleanly_terminate() else 1)
