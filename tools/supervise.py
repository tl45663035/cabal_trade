import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src_1080p"
sys.path.insert(0, str(SRC))

import calibration

SW_MINIMIZE = calibration.load_shared()["input"]["SW_MINIMIZE"]


def hide_console():
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE)
    except Exception:
        pass


if "--plan" not in sys.argv:
    hide_console()

import convert
import recovery
import row_model
from open_inventory import VK_ESCAPE, focus_game, press

LOGS = SRC / "logs"
EVENTS = LOGS / "supervise.log"
FRAMES = LOGS / "supervise_frames"
DEAD = LOGS / "dead_runs"
TOAST = ROOT / "tools" / "toast.ps1"
DRIVER = SRC / "driver.py"
K = json.loads(calibration.CONFIG.read_text(encoding="utf-8"))["supervise"]
LAG = re.compile(r"not answering|answering again|does not count|"
                 r"starting the pass again|going to the default state|"
                 r"the server stalled")
WORK_TAB = row_model.WORK_TAB
KEY_TAB = calibration.load_shared()["game_facts"]["agent_shop_tab"]
BAG_TABS = sorted(int(t) for t in calibration.load()["inventory"]["tabs"])
OTHER_TABS = [t for t in BAG_TABS if t not in (WORK_TAB, KEY_TAB)]

RELIST = re.compile(r"^  row (\d+): '(.*?)' x(\d+) at ([\d,]+) -> tab (\d+) "
                    r"slot \((\d+), (\d+)\)$", re.M)
RELISTED = re.compile(r"^    relisted \d+ at [\d,]+ in row (\d+)", re.M)
SOLD_WHILE = re.compile(r"^  row (\d+) sold while it was being cancelled",
                        re.M)
RESUPPLY = re.compile(r"^-- (.+?): \d+ row\(s\), threshold \d+ --$", re.M)
BUYING = re.compile(r"^  \d+/\d+ .+ held$", re.M)
RESUPPLY_DONE = re.compile(r"^  resupply (.+?): bought |"
                           r"^\s+resupply of '(.+?)' stopped", re.M)
LEFT_ON_TAB = re.compile(r"slot\(s\) of (.+?) stay on tab (\d+)|"
                         r"\d+ of \d+ (.+?) are still unconverted", re.M)


class Stop(Exception):
    pass


def now():
    return datetime.datetime.now().strftime("%H:%M:%S")


def snap(name, image=None):
    try:
        FRAMES.mkdir(parents=True, exist_ok=True)
        path = FRAMES / f"{datetime.datetime.now():%Y-%m-%d_%H%M%S}_{name}.png"
        (image if image is not None else calibration.grab()).save(path)
        print(f"  frame {path.name}")
    except Exception as exc:
        print(f"  (frame {name} not saved: {type(exc).__name__}: {exc})")


def event(reason, state):
    line = f"{reason},{now()},{state}"
    print(f"* {line}", flush=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(EVENTS, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy",
                        "Bypass", "-File", str(TOAST), line],
                       timeout=K["tool_timeout"], capture_output=True)
    except Exception as exc:
        print(f"  (toast failed: {type(exc).__name__}: {exc})")


def driver_pids():
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.Name -like "
         "'py*' -and $_.CommandLine -like '*driver.py*' } | "
         "Select-Object -ExpandProperty ProcessId"],
        capture_output=True, text=True, timeout=K["tool_timeout"]).stdout
    return [int(p) for p in out.split() if p.strip().isdigit()]


def alive(pid):
    return pid in driver_pids()


def kill(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True, text=True, timeout=K["tool_timeout"])


def newest_log():
    logs = sorted(LOGS.glob("*_run.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def read(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def no_driver_alive():
    pids = driver_pids()
    if pids:
        raise Stop(f"driver.py still alive ({pids}); not sending input")


def war_open(text):
    return text.count("WAR LAG: a war") > text.count("WAR LAG: done")


def death_reason(text):
    for pattern in (r"^\s*stopped: (.*)$", r"^\s*crashed: (.*)$",
                    r"^\s*STOPPED: (.*)$"):
        found = re.findall(pattern, text, flags=re.MULTILINE)
        if found:
            return found[-1].strip()
    return "ended without a reason line"


def watch(pid, log):
    print(f"watching pid {pid}, {log.name}", flush=True)
    text = read(log)
    seen_lag = len(LAG.findall(text))
    seen_stop = text.count("STOPPED:")
    told_quiet = False
    disconnect_since = None
    last_look = 0.0
    while True:
        text = read(log)
        lag = len(LAG.findall(text))
        if lag > seen_lag:
            last = LAG.findall(text)[-1]
            event(f"server lag: {last}", "alive" if alive(pid) else "dead")
            seen_lag = lag
        stops = text.count("STOPPED:")
        if stops > seen_stop:
            line = re.findall(r"STOPPED: (.*)", text)[-1].strip()
            event(f"stop condition: {line[:80]}",
                  "alive" if alive(pid) else "dead")
            seen_stop = stops

        try:
            quiet = time.time() - log.stat().st_mtime
        except OSError:
            quiet = 0
        if quiet > K["quiet_after"]:
            if war_open(text):
                if not told_quiet:
                    event(f"war window, log quiet {quiet:.0f}s", "alive")
                    told_quiet = True
            elif time.time() - last_look >= K["screen_gap"]:
                last_look = time.time()
                state = read_state(popup_only=True)
                if state["disconnect"] or state["login"] or state["failed"]:
                    if disconnect_since is None:
                        disconnect_since = time.time()
                        event("disconnected (run still up)", "alive")
                        snap("disconnect_seen", state["image"])
                    elif time.time() - disconnect_since > K["disconnect_kill"]:
                        event(f"disconnected {K['disconnect_kill']}s and the "
                              f"run has not died; killing pid {pid}", "dead")
                        kill(pid)
                elif not told_quiet:
                    event(f"log quiet {quiet:.0f}s, not a disconnect",
                          "alive")
                    told_quiet = True
        else:
            told_quiet = False
            disconnect_since = None

        if not alive(pid):
            reason = death_reason(read(log))
            event(reason[:110], "dead")
            return reason
        time.sleep(K["poll"])


def read_state(image=None, popup_only=False):
    image = image if image is not None else calibration.grab()
    words = (calibration.ocr(image, popup_box()) if popup_only
             else recovery._words(image))
    login = recovery._find(
        recovery.LOGIN_WORD, whole=True,
        words=calibration.ocr(image, recovery._box_frac(recovery.LOGIN_PANEL_F)))
    state = {
        "image": image,
        "disconnect": recovery.disconnected(words=words),
        "failed": recovery.failed_to_connect(words=words),
        "login": login,
        "alz": calibration.find_alz(image) is not None,
        "trade": calibration._trade_window_open(image),
        "vendor": calibration.vendor_open(image),
        "buttons": row_model.dialog_buttons(image),
        "underprice": calibration.underprice_warning(image),
    }
    state["summary"] = ", ".join(
        f"{k}={v if not isinstance(v, tuple) else list(v)}"
        for k, v in state.items() if k != "image")
    return state


def run_child(argv, cwd, timeout):
    proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    try:
        out, _ = proc.communicate(timeout=timeout)
    except (KeyboardInterrupt, subprocess.TimeoutExpired):
        kill(proc.pid)
        raise
    return proc.returncode, out


def run_driver(*args):
    no_driver_alive()
    print(f"$ py src_1080p/driver.py {' '.join(args)}", flush=True)
    code, out = run_child([sys.executable, str(DRIVER), *args], ROOT,
                          K["command_timeout"])
    tail = [l for l in out.splitlines()
            if l.strip() and not l.startswith("#") and "%" not in l][-8:]
    for line in tail:
        print("   " + line[:140])
    if code:
        raise Stop(f"driver.py {' '.join(args)} exited {code}: "
                   f"{death_reason(out)[:100]}")
    return out


def recover_login():
    no_driver_alive()
    print("$ py src_1080p/recovery.py", flush=True)
    code, out = run_child([sys.executable, str(SRC / "recovery.py")], SRC,
                          K["login_timeout"])
    for line in [l for l in out.splitlines() if l.strip()][-6:]:
        print("   " + line[:140])
    if code or "Refused" in out:
        raise Stop(f"recovery refused: {out.strip().splitlines()[-1][:100]}")
    event("already in the world; nothing to recover"
          if "already in the world" in out else "recovered: back in the world",
          "dead")


def dismiss_dialog(state):
    for attempt in range(1, K["dialog_tries"] + 1):
        if row_model.CONFIRM_WORD not in state["buttons"]:
            return state
        point = row_model.find_button(row_model.CONFIRM_WORD,
                                      timeout=K["dialog_settle"])
        if point is None:
            raise Stop("a dialog is open but Confirmation would not read")
        print(f"  Confirmation at {list(point)} (attempt {attempt})")
        calibration.click(*point)
        calibration.park()
        time.sleep(K["dialog_settle"])
        state = read_state()
    raise Stop(f"the dialog stayed up after {K['dialog_tries']} "
               f"Confirmation clicks")


def squash(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def favourite_of(name):
    want = squash(name)
    items = [(int(slot), item, squash(item))
             for slot, item in calibration.FAVOURITE_ITEMS.items()]
    exact = [i for i in items if i[2] == want]
    longer = sorted((i for i in items if i[2].startswith(want)),
                    key=lambda i: len(i[2]))
    shorter = sorted((i for i in items if want.startswith(i[2])),
                     key=lambda i: -len(i[2]))
    for found in (exact, longer, shorter):
        if found:
            return found[0][0], found[0][1]
    return None


def tooltip_at(row, col):
    import ctypes
    x, y = calibration.inventory_slot_point(row, col)
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    time.sleep(K["tooltip_settle"])
    image = calibration.grab()
    calibration.park()
    return image


def item_in(image):
    box = calibration._box(calibration._REG["tooltip_band"])
    seen = squash(" ".join(t for t, _c, _p in calibration.ocr(image, box)))
    best = None
    for slot, name in calibration.FAVOURITE_ITEMS.items():
        key = squash(name)
        if key in seen and (best is None or len(key) > len(squash(best[1]))):
            best = (int(slot), name)
    return best


def item_at(row, col):
    return item_in(tooltip_at(row, col))


def reading_at(tab, row, col):
    image = tooltip_at(row, col)
    found = item_in(image)
    print(f"  tab {tab} slot ({row},{col}) reads "
          f"{found[1] if found else 'no favourite'}")
    return image, found


def expect_at(tab, row, col, want, seen=None):
    image, found = reading_at(tab, row, col) if seen is None else seen
    if found is None or found[0] != want[0]:
        snap(f"tab{tab}_{row}x{col}_not_{squash(want[1])}", image)
        raise Stop(f"tab {tab} slot ({row},{col}) reads "
                   f"{found[1] if found else 'no favourite'}, not "
                   f"{want[1]}; not listing it")


def tab_slots(tab):
    if calibration.await_inventory() is None:
        raise Stop("the Inventory panel would not open; no Alz readable")
    calibration.click(*calibration.inventory_tab_point(tab), settle=0.0)
    time.sleep(row_model.TAB_SETTLE)
    calibration.park()
    slots = sorted(calibration.occupied_slots(calibration.grab()))
    snap(f"tab{tab}_{len(slots)}slots")
    return slots


MARK = re.compile(r"^(TASK|DONE) (\{.*\})$|"
                  r"^  (nothing bought); not opening the craft window\.$|"
                  r"^\s+resupply of '(.+?)' (stopped)|"
                  r"^  the server stalled for \d+s; (nothing was clicked) and "
                  r"the shop is shut\.", re.M)


def task_key(info):
    return info.get("row") if info["kind"] == "relist" else info.get("core")


def marked(text):
    open_tasks = []
    for m in MARK.finditer(text):
        if m.group(3) or m.group(5):
            key = ("resupply", m.group(4)) if m.group(5) else None
            for i in range(len(open_tasks) - 1, -1, -1):
                if open_tasks[i][0][0] == "resupply" and \
                        key in (None, open_tasks[i][0]):
                    del open_tasks[i]
                    break
            continue
        if m.group(6):
            open_tasks = [t for t in open_tasks if t[0][0] != "relist"]
            continue
        try:
            info = json.loads(m.group(2))
        except ValueError:
            continue
        key = (info["kind"], task_key(info))
        if m.group(1) == "TASK":
            open_tasks.append((key, info))
            continue
        for i in range(len(open_tasks) - 1, -1, -1):
            if open_tasks[i][0] == key:
                del open_tasks[i]
                break
    seen, out = set(), []
    for (kind, _key), info in open_tasks:
        if kind == "relist":
            task = ("relist", {"row": info["row"], "item": info["item"],
                               "qty": info["qty"], "price": info["price"],
                               "tab": info["tab"],
                               "slot": tuple(info["slot"])})
        else:
            task = ("resupply", {"core": info["core"]})
        mark = (task[0], tuple(sorted(task[1].items())))
        if mark not in seen:
            seen.add(mark)
            out.append(task)
    return out


def interrupted(text):
    if any(m.group(1) for m in MARK.finditer(text)):
        return marked(text)
    tasks = []
    starts = list(RELIST.finditer(text))
    if starts:
        last = starts[-1]
        after = text[last.end():]
        row = last.group(1)
        if not any(m.group(1) == row for m in RELISTED.finditer(after)) and \
                not any(m.group(1) == row for m in SOLD_WHILE.finditer(after)):
            tasks.append(("relist", last.start(), {
                "row": int(row), "item": last.group(2),
                "qty": int(last.group(3)),
                "price": int(last.group(4).replace(",", "")),
                "tab": int(last.group(5)),
                "slot": (int(last.group(6)), int(last.group(7)))}))
    starts = list(RESUPPLY.finditer(text))
    if starts:
        last = starts[-1]
        if BUYING.search(text, last.end()) and \
                not RESUPPLY_DONE.search(text, last.end()):
            tasks.append(("resupply", last.start(), {"core": last.group(1)}))
    for m in LEFT_ON_TAB.finditer(text):
        core = m.group(1) or m.group(3)
        tasks.append(("resupply", m.start(), {"core": core}))
    seen, out = set(), []
    for kind, pos, info in sorted(tasks, key=lambda t: t[1]):
        key = (kind, tuple(sorted(info.items())))
        if key not in seen:
            seen.add(key)
            out.append((kind, info))
    return out


def describe(kind, info):
    if kind == "relist":
        return (f"relist row {info['row']}: {info['item']!r} x{info['qty']} "
                f"at {info['price']:,} withdrawn to tab {info['tab']} slot "
                f"{info['slot']}, never listed back")
    return f"resupply of {info['core']}: bought stock may be on tab {WORK_TAB}"


def find_in_bag(item):
    want = favourite_of(item)
    hovered = 0
    for tab in OTHER_TABS:
        slots = tab_slots(tab)
        for n, (row, col) in enumerate(slots):
            if hovered >= K["hover_cap"]:
                event(f"gave up on {item}: {hovered} slot(s) hovered, "
                      f"{len(slots) - n} left on tab {tab} and tabs "
                      f"{[t for t in OTHER_TABS if t > tab]} unread", "dead")
                return None
            hovered += 1
            found = item_at(row, col)
            print(f"  tab {tab} slot ({row},{col}) reads "
                  f"{found[1] if found else 'no favourite'}")
            if found and want and found[0] == want[0]:
                return tab, row, col
    return None


def finish_relist(info, budget):
    tab, (row, col) = info["tab"], info["slot"]
    slots = tab_slots(tab)
    if (row, col) in slots:
        where = (tab, row, col)
    else:
        print(f"  tab {tab} slot ({row},{col}) is empty; looking on tabs "
              f"{OTHER_TABS} for {info['item']!r}")
        where = find_in_bag(info["item"])
    if where is None:
        print(f"  {info['item']!r} is not in the bag; the row kept it or it "
              f"sold. The next run reads the board.")
        return budget
    tab, row, col = where
    want = favourite_of(info["item"])
    if want is None:
        raise Stop(f"{info['item']!r} is not a favourite, so tab {tab} slot "
                   f"({row},{col}) cannot be checked before listing it")
    before = tab_slots(tab)
    while (row, col) in before:
        expect_at(tab, row, col, want)
        budget = spend(budget, f"list {row} {col} 0 {tab} {want[1]}")
        run_driver("list", str(row), str(col), "0", str(tab), want[1])
        after = tab_slots(tab)
        if after == before:
            raise Stop(f"list {row} {col} on tab {tab} changed nothing; "
                       f"{info['item']!r} is still there")
        event(f"finished: listed {info['item']} from tab {tab} ({row},{col}); "
              f"{len(after)} slot(s) left", "dead")
        before = after
    return budget


def finish_resupply(info, budget):
    core = info["core"]
    fav = favourite_of(core)
    if fav is None:
        raise Stop(f"{core!r} is not a favourite; cannot finish its resupply")
    slot, name = fav
    if not tab_slots(WORK_TAB):
        print(f"  tab {WORK_TAB} is clear; nothing of {name} to finish")
        return budget
    if convert.cell_for(name):
        budget = spend(budget, f"convert {slot}")
        out = run_driver("convert", str(slot))
        if "not converting" in out or "stay on tab" in out:
            raise Stop(f"rows are full; {name} stays on tab {WORK_TAB}")
        event(f"finished: convert {slot} ({name})", "dead")
        made = fav
    else:
        budget = spend(budget, "craft chaos")
        out = run_driver("craft", "chaos")
        if "not crafting" in out or "stays on tab" in out:
            raise Stop(f"rows are full; {name} stays on tab {WORK_TAB}")
        event(f"finished: craft chaos ({name})", "dead")
        pair = calibration.pair_slot(slot)
        if pair is None:
            raise Stop(f"{name} has no Set beside it in the favourites; "
                       f"cannot tell what the craft made")
        made = (pair, calibration.FAVOURITE_ITEMS[str(pair)])
    for row, col in tab_slots(WORK_TAB):
        seen = reading_at(WORK_TAB, row, col)
        if made != fav and seen[1] is not None and seen[1][0] == slot:
            event(f"left {name} on tab {WORK_TAB} ({row},{col}); under a "
                  f"whole batch, as the run leaves it", "dead")
            continue
        expect_at(WORK_TAB, row, col, made, seen)
        budget = spend(budget, f"list {row} {col} 0 {WORK_TAB} {made[1]}")
        run_driver("list", str(row), str(col), "0", str(WORK_TAB), made[1])
        event(f"finished: listed {made[1]} from tab {WORK_TAB} "
              f"({row},{col})", "dead")
    return budget


def spend(budget, what):
    if budget <= 0:
        raise Stop(f"{K['task_cap']} driver commands already; not running "
                   f"{what}")
    return budget - 1


def verify():
    left = tab_slots(WORK_TAB)
    if len(left) >= calibration.GRID * calibration.GRID:
        raise Stop(f"tab {WORK_TAB} is full; a run would have nowhere to "
                   f"withdraw a row to")
    if left:
        event(f"stranded stock on tab {WORK_TAB}: {len(left)} slot(s) the "
              f"log does not name, from {left[0]}; the run lands beside it",
              "dead")
        return
    print(f"  verified: tab {WORK_TAB} is clear")


def get_in(plan=False):
    if not plan and not focus_game():
        raise Stop("could not bring the game to the foreground; "
                   "nothing read, nothing sent")
    state = read_state()
    print(f"  screen: {state['summary']}")
    if not plan:
        snap("found", state["image"])
    if state["disconnect"] or state["login"] or state["failed"]:
        print("  case: disconnect / login screen -> recovery.py")
        if not plan:
            recover_login()
            state = read_state()
            snap("after_login", state["image"])

    if state["vendor"]:
        print("  case: vendor open -> Escape")
        if not plan:
            press(VK_ESCAPE)
            time.sleep(K["escape_settle"])
            state = read_state()

    if row_model.CONFIRM_WORD in state["buttons"]:
        print("  case: a registration dialog is open -> Confirmation")
        if not plan:
            state = dismiss_dialog(state)

    if not state["alz"] and not state["trade"] and not plan:
        if calibration.await_inventory() is None:
            raise Stop(f"nothing recognisable on screen: {state['summary']}")
    return state


def keep_evidence(log):
    into = DEAD / log.stem
    kept = []
    for folder, pattern, count in ((calibration.FRAME_DIR, "*.png",
                                    K["keep_frames"]),
                                   (calibration.VIDEO_DIR, "*.mp4",
                                    K["keep_reels"])):
        files = sorted(folder.glob(pattern), key=lambda f: f.name)[-count:]
        if files:
            into.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.move(str(f), str(into / f.name))
        kept.append(f"{len(files)} {pattern[2:]}")
    print(f"  kept {' and '.join(kept)} from the dead run in {into}")


def recover(reason, text, plan=False, log=None, watched=True):
    if watched and "interrupted from the keyboard" in reason:
        raise Stop("cancelled with Ctrl x4")
    if log is not None and not plan:
        keep_evidence(log)
    get_in(plan)
    tasks = interrupted(text)
    if not tasks:
        print("  the log shows no task cut short")
    for kind, info in tasks:
        print(f"  interrupted: {describe(kind, info)}")
    if plan:
        print(f"  then: check tab {WORK_TAB} is clear, close_everything, "
              f"relaunch")
        return
    budget = K["task_cap"]
    for kind, info in tasks:
        if kind == "relist":
            budget = finish_relist(info, budget)
        else:
            budget = finish_resupply(info, budget)
    verify()
    calibration.close_everything(True)
    snap("reset")


def launch():
    no_driver_alive()
    before = newest_log()
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = SW_MINIMIZE
    proc = subprocess.Popen([sys.executable, str(DRIVER)], cwd=ROOT,
                            creationflags=subprocess.CREATE_NEW_CONSOLE,
                            startupinfo=info)
    for waited in range(1, K["launch_wait"] + 1):
        time.sleep(1)
        log = newest_log()
        if log is not None and log != before:
            break
        if proc.poll() is not None:
            raise Stop(f"driver.py exited {proc.returncode} {waited}s after "
                       f"launch, before writing a run log")
    else:
        raise Stop(f"no new run log {K['launch_wait']}s after launching "
                   f"driver.py (pid {proc.pid} still up)")
    time.sleep(K["launch_settle"])
    for line in read(log).splitlines()[:4]:
        print("   " + line)
    event(f"relaunched (pid {proc.pid})", "alive")
    return proc.pid, log


def plan(log_path=None, png=None):
    log = Path(log_path) if log_path else newest_log()
    text = read(log) if log else ""
    pids = driver_pids()
    print(f"driver.py alive: {pids or 'none'}")
    print(f"log: {log.name if log else 'none'}; last reason: "
          f"{death_reason(text)[:120]}")
    for kind, info in interrupted(text):
        print(f"  interrupted: {describe(kind, info)}")
    if png:
        from PIL import Image
        state = read_state(Image.open(png))
        print(f"  frame {png}: {state['summary']}")
        return
    if pids:
        state = read_state()
        print(f"  screen: {state['summary']}")
        print("  a run is alive, so nothing would be touched")
        return
    recover(death_reason(text), text, plan=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--log")
    ap.add_argument("--png")
    args = ap.parse_args()
    if args.plan:
        plan(args.log, args.png)
        return 0

    calibration.log_to_file("supervise")
    calibration.watch_for_stop()
    try:
        pids = driver_pids()
        if pids:
            pid, log = pids[0], newest_log()
            event(f"attached (pid {pid})", "alive")
        else:
            log = newest_log()
            if log is None:
                print("no run alive and no run log; launching one")
                get_in()
                calibration.close_everything(True)
            else:
                text = read(log)
                reason = death_reason(text)
                event(f"no run alive; {log.name} ended: {reason[:80]}",
                      "dead")
                recover(reason, text, log=log, watched=False)
            pid, log = launch()
            if args.once:
                return 0
        short = 0
        while True:
            launched = time.time()
            reason = watch(pid, log)
            short = short + 1 if time.time() - launched < K["short_run"] else 0
            if short >= K["short_runs"]:
                raise Stop(f"{K['short_runs']} runs in a row died within "
                           f"{K['short_run']}s of launch; last: {reason[:80]}")
            recover(reason, read(log), log=log)
            pid, log = launch()
            if args.once:
                return 0
    except KeyboardInterrupt:
        event("supervisor cancelled with Ctrl x4", "dead")
        return 0
    except Stop as exc:
        event(f"supervisor stopped: {exc}", "dead")
        return 1
    except Exception as exc:
        import traceback
        traceback.print_exc()
        event(f"supervisor crashed: {type(exc).__name__}: {exc}"[:150], "dead")
        return 1


if __name__ == "__main__":
    sys.exit(main())
