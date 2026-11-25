"""Start the Cabal client if it is closed, maximize it on the 1080p monitor,
and optionally keep a trading supervisor running across game crashes.

  py tools/launch_game.py                      open + maximize the game only
  py tools/launch_game.py --config cang        ... then run the supervisor
  py tools/launch_game.py --config cang --keep ... and relaunch game and
                                               supervisor whenever the game
                                               crashes or closes

Never types or stores the password; the client keeps it, and the
supervisor presses Login itself from the maximized login screen.
With --keep the loop ends when the supervisor exits while the game is still
up (a Ctrl stop, a stop condition), so a deliberate stop is respected.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONF = json.loads((ROOT / "src_1080p" / "config.json")
                  .read_text(encoding="utf-8"))["launch_game"]
U = ctypes.windll.user32
SW_RESTORE, SW_MAXIMIZE = 9, 3
LEFT_DOWN, LEFT_UP = 2, 4


def say(text):
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def window(title):
    return U.FindWindowW(None, title) or None


def rect(hwnd):
    r = wt.RECT()
    U.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def game_running():
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {CONF['game_process']}"],
        capture_output=True, text=True).stdout
    return CONF["game_process"].lower() in out.lower()


def wait_for(test, seconds, gap=1.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = test()
        if found:
            return found
        time.sleep(gap)
    return None


def play_is_red(hwnd):
    from PIL import ImageGrab
    left, top, _r, _b = rect(hwnd)
    x, y = left + CONF["play_offset"][0], top + CONF["play_offset"][1]
    dx, dy = CONF["play_probe"]
    pixels = list(ImageGrab.grab((x - dx, y - dy, x + dx, y + dy)).getdata())
    red = sum(p[0] - max(p[1], p[2]) for p in pixels) / len(pixels)
    return red > CONF["play_red_over"]


def click(x, y):
    U.SetCursorPos(int(x), int(y))
    time.sleep(0.2)
    U.mouse_event(LEFT_DOWN, 0, 0, 0, 0)
    time.sleep(0.08)
    U.mouse_event(LEFT_UP, 0, 0, 0, 0)


def start_game():
    if game_running():
        say("the game is already running")
        return True
    launcher = window(CONF["launcher_title"])
    if launcher is None:
        say(f"starting {CONF['launcher']}")
        exe = Path(CONF["launcher"])
        subprocess.Popen([str(exe)], cwd=str(exe.parent))
        launcher = wait_for(lambda: window(CONF["launcher_title"]),
                            CONF["launcher_wait"])
        if launcher is None:
            say("the launcher window never appeared")
            return False
    U.ShowWindow(launcher, SW_RESTORE)
    U.SetForegroundWindow(launcher)
    say("waiting for the launcher's update check (PLAY turns red)")
    if not wait_for(lambda: play_is_red(launcher), CONF["launcher_wait"], 2):
        say("PLAY never turned red; the update may still be running")
        return False
    U.SetForegroundWindow(launcher)
    time.sleep(0.6)
    left, top, _r, _b = rect(launcher)
    click(left + CONF["play_offset"][0], top + CONF["play_offset"][1])
    say("clicked PLAY")
    if not wait_for(lambda: window(CONF["game_title"]), CONF["game_wait"]):
        say("the game window never appeared")
        return False
    return True


def maximize():
    hwnd = wait_for(lambda: window(CONF["game_title"]), CONF["game_wait"])
    if hwnd is None:
        say("no game window to maximize")
        return False
    U.ShowWindow(hwnd, SW_MAXIMIZE)
    time.sleep(CONF["settle"])
    U.SetForegroundWindow(hwnd)
    time.sleep(CONF["settle"])
    got = list(rect(hwnd))
    ok = got == CONF["maximized_rect"]
    say(f"game window {got}" + ("; maximized" if ok else
        f"; expected {CONF['maximized_rect']} -- is it on the 1080p monitor?"))
    return ok


def supervisor_alive():
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | ? { $_.Name -like 'py*' -and "
         "$_.CommandLine -match 'supervise\\.py|driver\\.py' } | "
         "select -Expand ProcessId"],
        capture_output=True, text=True).stdout.split()
    return [int(p) for p in out if p.isdigit()]


def kill_tree(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True)


def ready():
    return start_game() and maximize()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="profile to run the supervisor with")
    ap.add_argument("--keep", action="store_true",
                    help="relaunch game and supervisor after every crash")
    args = ap.parse_args()
    if args.keep and not args.config:
        ap.error("--keep needs --config")
    if not args.config:
        sys.exit(0 if ready() else 1)
    while True:
        if not ready():
            sys.exit(1)
        if supervisor_alive():
            say("a supervisor or run is already up; not starting another")
            sys.exit(1)
        say(f"starting the supervisor with --config {args.config}")
        sup = subprocess.Popen([sys.executable, "tools/supervise.py",
                                "--config", args.config], cwd=str(ROOT))
        while sup.poll() is None and game_running():
            time.sleep(CONF["poll"])
        if sup.poll() is not None and game_running():
            say(f"the supervisor exited ({sup.returncode}) with the game up; "
                f"stopping")
            sys.exit(sup.returncode)
        say("the game is gone; stopping the supervisor and relaunching")
        if sup.poll() is None:
            kill_tree(sup.pid)
        if not args.keep:
            sys.exit(1)
        time.sleep(CONF["settle"] * 2)


if __name__ == "__main__":
    main()
