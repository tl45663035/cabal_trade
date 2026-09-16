import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src_1080p"
sys.path.insert(0, str(SRC))


def _chosen_config():
    for index, arg in enumerate(sys.argv):
        if arg == "--config" and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
        if arg.startswith("--config="):
            return arg.split("=", 1)[1]
    return ""

os.environ["CABAL_CONFIG"] = _chosen_config()

import calibration

CONFIG = calibration.config_name()

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
REELS = LOGS / "recovery_video"
DRIVER = SRC / "driver.py"
K = calibration.load_shared()["supervise"]
LAG = re.compile(r"not answering|answering again|does not count|"
                 r"starting the pass again|going to the default state|"
                 r"the server stalled")
WORK_TAB = row_model.WORK_TAB


RELIST = re.compile(r"^  row (\d+): '(.*?)' x(\d+) at ([\d,]+) -> tab (\d+) "
                    r"slot \((\d+), (\d+)\)$", re.M)
RELISTED = re.compile(r"^    relisted \d+ at [\d,]+ in row (\d+)", re.M)
SOLD_WHILE = re.compile(r"^  row (\d+) sold while it was being cancelled",
                        re.M)
RESUPPLY = re.compile(r"^-- (.+?): \d+ row\(s\)(?:, threshold \d+)? --$", re.M)
BUYING = re.compile(r"^  \d+/\d+ .+ held$", re.M)
RESUPPLY_DONE = re.compile(r"^  resupply (.+?): bought |"
                           r"^\s+resupply of '(.+?)' stopped", re.M)
LEFT_ON_TAB = re.compile(r"slot\(s\) of (.+?) stay on tab (\d+)|"
                         r"\d+ of \d+ (.+?) are still unconverted", re.M)


class Stop(Exception):
    pass


class Held(Stop):
    pass


class Cancelled(Stop):
    pass


def held_reason(reason):
    return any(word in reason for word in K["held_reasons"])


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


def prune_before_today():
    today = datetime.date.today()
    gone = 0
    for folder in (FRAMES, DEAD, REELS):
        if not folder.exists():
            continue
        for item in folder.iterdir():
            if datetime.date.fromtimestamp(item.stat().st_mtime) < today:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
                gone += 1
    if gone:
        print(f"  pruned {gone} frame(s), reel(s) and dead run(s) from "
              f"before {today:%Y-%m-%d}")
    return f"{today:%Y-%m-%d}"


def watch(pid, log):
    print(f"watching pid {pid}, {log.name}", flush=True)
    day = prune_before_today()
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
            event(f"stop condition: {line[:K['reason_width']]}",
                  "alive" if alive(pid) else "dead")
            seen_stop = stops

        try:
            quiet = time.time() - log.stat().st_mtime
        except OSError:
            quiet = 0
        if time.time() - last_look >= K["screen_gap"]:
            last_look = time.time()
            state = read_state(popup_only=True)
            if state["disconnect"] or state["login"] or state["failed"]:
                if disconnect_since is None:
                    disconnect_since = time.time()
                    event("disconnected (run still up)", "alive")
                    snap("disconnect_seen", state["image"])
                    time.sleep(K["disconnect_kill"])
                    state = read_state(popup_only=True)
                if state["disconnect"] or state["login"] or state["failed"]:
                    event(f"disconnected {time.time() - disconnect_since:.0f}s "
                          f"on; killing pid {pid}", "dead")
                    kill(pid)
            else:
                disconnect_since = None
        if quiet > K["quiet_after"]:
            if not told_quiet:
                if war_open(text):
                    event(f"war window, log quiet {quiet:.0f}s", "alive")
                elif disconnect_since is None:
                    event(f"log quiet {quiet:.0f}s, not a disconnect",
                          "alive")
                told_quiet = True
        else:
            told_quiet = False

        if not alive(pid):
            reason = death_reason(read(log))
            event(reason[:K['reason_width']], "dead")
            return reason
        if f"{datetime.date.today():%Y-%m-%d}" != day:
            day = prune_before_today()
        time.sleep(K["poll"])


def read_state(image=None, popup_only=False):
    image = image if image is not None else calibration.grab()
    words = (calibration.ocr(image, recovery._box_frac(recovery.POPUP_F))
             if popup_only else recovery._words(image))
    login = recovery._find(
        recovery.LOGIN_WORD, whole=True,
        words=calibration.ocr(image, recovery._box_frac(recovery.LOGIN_PANEL_F)))
    state = {
        "image": image,
        "disconnect": recovery.disconnected(words=words),
        "failed": recovery.failed_to_connect(words=words),
        "login": login,
    }
    if not popup_only:
        state.update({
            "select": (recovery.character_list(image)
                       or recovery.server_list(image)),
            "alz": (calibration.inventory_grid_shown(image)
                    and calibration.find_alz(image) is not None),
            "trade": calibration._trade_window_open(image),
            "vendor": calibration.vendor_open(image),
            "craft": calibration.craft_window_open(image),
            "buttons": row_model.dialog_buttons(image),
            "underprice": calibration.underprice_warning(image),
        })
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
    args = ("--config", CONFIG) + tuple(args)
    for attempt in range(1, K["stall_retries"] + 2):
        no_driver_alive()
        print(f"$ py src_1080p/driver.py {' '.join(args)}", flush=True)
        code, out = run_child([sys.executable, str(DRIVER), *args], ROOT,
                              K["command_timeout"])
        tail = [l for l in out.splitlines()
                if l.strip() and not l.startswith("#") and "%" not in l][-K['tail_lines']:]
        for line in tail:
            print("   " + line[:K['line_width']])
        if not code:
            return out
        reason = death_reason(out)
        if not held_reason(reason):
            raise Stop(f"driver.py {' '.join(args)} exited {code}: "
                       f"{reason[:K['reason_width']]}")
        if attempt > K["stall_retries"]:
            raise Held(f"driver.py {' '.join(args)} exited {code}: "
                       f"{reason[:K['reason_width']]}")
        event(f"driver.py {args[0]} held up: {reason[:K['reason_width']]}; waiting "
              f"{K['stall_wait']}s, then attempt {attempt + 1}", "dead")
        time.sleep(K["stall_wait"])
        state = read_state()
        if row_model.CONFIRM_WORD in state["buttons"]:
            dismiss_dialog(state)


def recover_login():
    no_driver_alive()
    print("$ py src_1080p/recovery.py", flush=True)
    code, out = run_child([sys.executable, str(SRC / "recovery.py")], SRC,
                          K["login_timeout"])
    for line in [l for l in out.splitlines() if l.strip()][-K['tail_lines']:]:
        print("   " + line[:K['line_width']])
    if code or "Refused" in out:
        raise Stop(f"recovery refused: {out.strip().splitlines()[-1][:K['reason_width']]}")
    event("already in the world; nothing to recover"
          if "already in the world" in out else "recovered: back in the world",
          "dead")


def relog():
    no_driver_alive()
    print("$ py src_1080p/recovery.py --relog", flush=True)
    code, out = run_child([sys.executable, str(SRC / "recovery.py"),
                           "--relog"], SRC, K["login_timeout"])
    for line in [l for l in out.splitlines() if l.strip()][-K['tail_lines']:]:
        print("   " + line[:K['line_width']])
    if code or "Refused" in out:
        raise Stop(f"relog refused: {out.strip().splitlines()[-1][:K['reason_width']]}")
    event("relogged: back in the world", "dead")


def close_craft_window(state):
    for attempt in range(1, K["dialog_tries"] + 1):
        if not state["craft"]:
            snap("craft_window_closed", state["image"])
            return state
        print(f"  Escape on the craft window (attempt {attempt})")
        calibration.park()
        press(VK_ESCAPE)
        time.sleep(K["escape_settle"])
        state = read_state()
    raise Held(f"the craft window stayed open after {K['dialog_tries']} "
               f"Escapes; nothing can reopen the Agent Shop over it")


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
    print(f"  {K['dialog_tries']} {row_model.CONFIRM_WORD} clicks did nothing; "
          f"the game is refusing it, so {row_model.DISMISS_WORD} instead and "
          f"the row stays listed for the next run to retry")
    point = row_model.find_button(row_model.DISMISS_WORD,
                                  timeout=K["dialog_settle"])
    if point is not None:
        print(f"  {row_model.DISMISS_WORD} at {list(point)}")
        calibration.click(*point)
        calibration.park()
        time.sleep(K["dialog_settle"])
        state = read_state()
    if row_model.CONFIRM_WORD in state["buttons"]:
        press(VK_ESCAPE)
        time.sleep(K["escape_settle"])
        state = read_state()
    if row_model.CONFIRM_WORD in state["buttons"]:
        raise Held(f"the dialog stayed up after {K['dialog_tries']} "
                   f"{row_model.CONFIRM_WORD} clicks, {row_model.DISMISS_WORD} "
                   f"and Escape")
    snap("dialog_dismissed", state["image"])
    return state


def squash(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


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
    if best is None:
        named = calibration.voucher_floor_ratio(seen)[0]
        if named is None:
            rows = (calibration.load_shared()["resupply"].get("cash_shop")
                    or {}).get("rows") or {}
            named = next((item for item in rows if squash(item) in seen),
                         None)
        if named:
            best = (None, named)
    return best


def reading_at(tab, row, col):
    image = tooltip_at(row, col)
    found = item_in(image)
    print(f"  tab {tab} slot ({row},{col}) reads "
          f"{found[1] if found else 'no favourite'}")
    return image, found


def tab_slots(tab):
    if calibration.await_inventory() is None:
        raise Stop("the Inventory panel would not open; no Alz readable")
    calibration.click(*calibration.inventory_tab_point(tab), settle=0.0)
    time.sleep(row_model.TAB_SETTLE)
    calibration.park()
    slots = set()
    for _ in range(K["slot_reads"]):
        slots |= calibration.occupied_slots(calibration.grab())
        time.sleep(K["slot_read_gap"])
    slots = sorted(slots)
    snap(f"tab{tab}_{len(slots)}slots")
    return slots


MARK = re.compile(r"^(TASK|DONE) (\{.*\})$|"
                  r"^  (nothing bought); not opening the craft window\.$", re.M)


def task_key(info):
    return info.get("row") if info["kind"] == "relist" else info.get("core")


def marked(text):
    open_tasks = []
    for m in MARK.finditer(text):
        if m.group(3):
            for i in range(len(open_tasks) - 1, -1, -1):
                if open_tasks[i][0][0] == "resupply":
                    del open_tasks[i]
                    break
            continue
        try:
            info = json.loads(m.group(2))
        except ValueError:
            continue
        key = (info["kind"], task_key(info))
        if m.group(1) == "TASK":
            open_tasks = [t for t in open_tasks if t[0] != key]
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


def action_for(row, col, slot, name):
    if slot is None or convert.cell_for(name):
        return ("list", str(row), str(col), "0", str(WORK_TAB), name)
    pair = calibration.pair_slot(slot)
    if pair is not None and             convert.cell_for(calibration.FAVOURITE_ITEMS[str(pair)]):
        return ("convert", str(pair))
    if slot == calibration._craft_slots()[0]:
        return ("craft", "chaos")
    return ("list", str(row), str(col), "0", str(WORK_TAB), name)


def first_row(tab):
    held = tab_slots(tab)
    below = [s for s in held if s[0] != 1]
    if below:
        print(f"  tab {tab} holds {len(below)} slot(s) below row 1, from "
              f"{below[0]}; the run never puts anything there, so they are "
              f"not touched")
    return [s for s in held if s[0] == 1]


def clear_work_tab(budget):
    hovered, left_behind, collected = 0, set(), False
    while True:
        left = first_row(WORK_TAB)
        if not left:
            print(f"  verified: row 1 of tab {WORK_TAB} is clear")
            return budget
        todo = [s for s in left if s not in left_behind]
        if not todo:
            print(f"  tab {WORK_TAB} holds only what the run leaves behind: "
                  f"{len(left)} slot(s)")
            return budget
        if not collected:
            collected = True
            budget = spend(budget, "collect")
            try:
                run_driver("collect")
            except Stop as exc:
                event(f"collect failed: {str(exc)[:K['reason_width']]}; "
                      f"carrying on", "dead")
        found = None
        for row, col in todo:
            if hovered >= K["hover_cap"]:
                break
            hovered += 1
            _image, item = reading_at(WORK_TAB, row, col)
            if item is not None:
                found = (row, col, item)
                break
        if found is None:
            row, col = todo[0]
            name = "whatever is there"
            args = ("list", str(row), str(col), "0", str(WORK_TAB))
            event(f"tab {WORK_TAB} slot ({row},{col}) has no tooltip name; "
                  f"listing it at the panel's price and the full quantity",
                  "dead")
        else:
            row, col, (slot, name) = found
            args = action_for(row, col, slot, name)
        what = " ".join(args)
        budget = spend(budget, what)
        run_driver(*args)
        after = first_row(WORK_TAB)
        if after == left:
            if args[0] == "craft":
                event(f"left {name} on tab {WORK_TAB} ({row},{col}); under a "
                      f"whole batch, as the run leaves it", "dead")
                left_behind.add((row, col))
                continue
            raise Stop(f"{what} changed nothing on tab {WORK_TAB}; {name} is "
                       f"still there")
        event(f"finished: {what} ({name}); {len(after)} slot(s) left", "dead")


def spend(budget, what):
    if budget <= 0:
        raise Stop(f"{K['task_cap']} driver commands already; not running "
                   f"{what}")
    return budget - 1


def get_in(plan=False):
    if not plan and not focus_game():
        raise Stop("could not bring the game to the foreground; "
                   "nothing read, nothing sent")
    state = read_state()
    print(f"  screen: {state['summary']}")
    if not plan:
        snap("found", state["image"])
    if (state.get("select") and not plan
            and not (state["disconnect"] or state["login"] or state["failed"])):
        if recovery.world_answers():
            print(f"  the name reads at {list(state['select'])} but the "
                  f"Inventory opens and the Alz reads: the world, not the "
                  f"select screen; not recovering")
            state = read_state()
            print(f"  screen: {state['summary']}")
    if (state["disconnect"] or state["login"] or state["failed"]
            or state.get("select")):
        print("  case: disconnect / login / select screen -> recovery.py")
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

    if state["craft"]:
        print("  case: the craft window is open -> Escape")
        if not plan:
            state = close_craft_window(state)

    for _attempt in range(K["dialog_tries"]):
        if row_model.CONFIRM_WORD in state["buttons"]:
            print("  case: a registration dialog is open -> Confirmation")
            if plan:
                break
            state = dismiss_dialog(state)
        elif row_model.DISMISS_WORD in state["buttons"]:
            print(f"  case: a dialog with {row_model.DISMISS_WORD} is open "
                  f"-> {row_model.DISMISS_WORD}")
            if plan:
                break
            point = row_model.find_button(row_model.DISMISS_WORD,
                                          timeout=K["dialog_settle"])
            if point is None:
                break
            calibration.click(*point)
            calibration.park()
            time.sleep(K["dialog_settle"])
            state = read_state()
        else:
            break

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
            try:
                shutil.move(str(f), str(into / f.name))
            except PermissionError:
                shutil.copy2(str(f), str(into / f.name))
                print(f"  {f.name} is held open by another process; copied "
                      f"rather than moved")
        kept.append(f"{len(files)} {pattern[2:]}")
    print(f"  kept {' and '.join(kept)} from the dead run in {into}")


def recover(reason, text, plan=False, log=None, watched=True):
    if watched and "interrupted from the keyboard" in reason:
        raise Cancelled("cancelled with Ctrl x4")
    if log is not None and not plan:
        keep_evidence(log)
    get_in(plan)
    tasks = interrupted(text)
    if not tasks:
        print("  the log shows no task cut short")
    for kind, info in tasks:
        print(f"  interrupted: {describe(kind, info)}")
    if plan:
        print(f"  then: list, convert or craft whatever row 1 of tab "
              f"{WORK_TAB} holds, close_everything, relaunch")
        return
    calibration.close_everything(True)
    clear_work_tab(K["task_cap"])
    for kind, info in tasks:
        if kind == "relist":
            print(f"  relist row {info['row']}: {info['item']!r} is handled above "
                  f"if it came back to tab {WORK_TAB}; otherwise the row kept it, "
                  f"it sold, or it was listed by hand. The next run reads the "
                  f"board.")
    calibration.close_gift_window(True)
    calibration.close_everything(True)
    snap("reset")


def launch():
    no_driver_alive()
    before = newest_log()
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = SW_MINIMIZE
    proc = subprocess.Popen([sys.executable, str(DRIVER),
                             "--config", CONFIG], cwd=ROOT,
                            creationflags=subprocess.CREATE_NEW_CONSOLE,
                            startupinfo=info)
    for waited in range(1, K["launch_wait"] + 1):
        time.sleep(K['launch_poll'])
        log = newest_log()
        if log is not None and log != before:
            break
        if proc.poll() is not None:
            raise Stop(f"driver.py exited {proc.returncode} {waited}s after "
                       f"launch, before writing a run log")
    else:
        kill(proc.pid)
        raise Stop(f"no new run log {K['launch_wait']}s after launching "
                   f"driver.py; pid {proc.pid} killed")
    time.sleep(K["launch_settle"])
    for line in read(log).splitlines()[:K['head_lines']]:
        print("   " + line)
    event(f"relaunched (pid {proc.pid})", "alive")
    return proc.pid, log


def plan(log_path=None, png=None):
    log = Path(log_path) if log_path else newest_log()
    text = read(log) if log else ""
    pids = driver_pids()
    print(f"driver.py alive: {pids or 'none'}")
    print(f"log: {log.name if log else 'none'}; last reason: "
          f"{death_reason(text)[:K['reason_width']]}")
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


def recover_and_launch(reason, log, watched=True, relog_first=False):
    held = failed = 0
    while True:
        try:
            recover(reason, read(log), log=log, watched=watched)
            if relog_first:
                relog()
            return launch()
        except Held as exc:
            held += 1
            if held > K["held_retries"]:
                raise Stop(str(exc))
            event(f"recovery held up: {str(exc)[:K['reason_width']]}; waiting "
                  f"{K['held_wait']}s, then attempt {held + 1}", "dead")
            time.sleep(K["held_wait"])
        except Cancelled:
            raise
        except Stop as exc:
            failed += 1
            if failed > K["recover_retries"]:
                raise
            event(f"recovery attempt {failed} failed: {str(exc)[:K['reason_width']]}; "
                  f"attempt {failed + 1} in {K['recover_wait']}s", "dead")
            time.sleep(K["recover_wait"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--log")
    ap.add_argument("--png")
    ap.add_argument("--config", required=True)
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
                pid, log = launch()
            else:
                reason = death_reason(read(log))
                event(f"no run alive; {log.name} ended: {reason[:K['reason_width']]}",
                      "dead")
                pid, log = recover_and_launch(reason, log, watched=False)
            if args.once:
                return 0
        short = relogs = 0
        while True:
            launched = time.time()
            reason = watch(pid, log)
            if held_reason(reason):
                event(f"the server took the run down: {reason[:K['reason_width']]}; "
                      f"not counted as a short run", "dead")
            elif time.time() - launched < K["short_run"]:
                short += 1
            else:
                short = relogs = 0
            if short < K["short_runs"]:
                pid, log = recover_and_launch(reason, log)
            elif relogs < K["relog_tries"]:
                relogs += 1
                short = 0
                event(f"{K['short_runs']} runs in a row died within "
                      f"{K['short_run']}s of launch; relogging "
                      f"({relogs} of {K['relog_tries']})", "dead")
                pid, log = recover_and_launch(reason, log, relog_first=True)
            else:
                raise Stop(f"{K['short_runs']} runs in a row died within "
                           f"{K['short_run']}s of launch after {relogs} "
                           f"relog(s); last: {reason[:K['reason_width']]}")
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
        event(f"supervisor crashed: {type(exc).__name__}: {exc}"[:K['reason_width']], "dead")
        return 1


if __name__ == "__main__":
    sys.exit(main())
