"""Replay a recording into a blank screen and listen the way a game does.

  py recorder/input_probe.py [RECORDING]      (default: the newest *_all.jsonl)

A black window covers every monitor and swallows the replay's clicks and
keys, so nothing real is touched; put the game in the background first. It
listens to raw keyboard and mouse input, as a game reading raw input does,
and once the replay ends it compares what arrived with the file:
  keys     each key down and up, by scan code and extended flag, in order
  buttons  each mouse button down and up, and the wheel
  turns    for each right-drag camera turn, the raw counts added up
  timing   how far each event landed from its place in the recording
Ctrl twice stops it early, and so does anything taking the foreground from the
black screen. Win and Alt keys in the recording are skipped (they could open
the Start menu or switch windows: Alt+Tab, Alt+F4)."""

import ctypes
import difflib
import json
import os
import statistics
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import recorder_settings
import record_action as ra
import record_all

W = ra.W
RI_KEY_BREAK, RI_KEY_E0 = 0x1, 0x2
RI_MOUSE_LEFT_BUTTON_DOWN = 0x1
RI_MOUSE_RIGHT_BUTTON_DOWN = 0x4
RI_MOUSE_WHEEL, RI_MOUSE_HWHEEL = 0x400, 0x800
BUTTON_BITS = [("left", 0x1, 0x2), ("right", 0x4, 0x8), ("middle", 0x10, 0x20),
               ("x1", 0x40, 0x80), ("x2", 0x100, 0x200)]
RIM_TYPEKEYBOARD, HID_USAGE_KEYBOARD = 1, 6
WIN_KEYS = {91, 92}
ALT_KEYS = {18, 164, 165}
SKIP_KEYS = WIN_KEYS | ALT_KEYS
GA_ROOT = 2


def playable(events):
    """The events the probe may replay into its black screen: no Win keys
    (the Start menu) and no Alt (Alt+Tab, Alt+F4 would switch away)."""
    plays = [e for e in events if not (e["kind"] == "key" and e.get("vk") in SKIP_KEYS)]
    return plays, len(events) - len(plays)


def should_stop(tapped, shield, foreground):
    """Why the replay must stop now, or None: Ctrl pressed twice, or anything
    other than the black screen in front (keys would go to that window)."""
    if tapped:
        return "Ctrl pressed twice"
    if foreground != shield:
        return ("the black screen lost the foreground, so the replay stopped before "
                "a key could reach another window")
    return None
JITTER_LIMIT_MS = 10.0


class RAWMOUSE(ctypes.Structure):
    _fields_ = [("usFlags", wintypes.USHORT), ("ulButtons", wintypes.ULONG),
                ("ulRawButtons", wintypes.ULONG), ("lLastX", wintypes.LONG),
                ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", wintypes.USHORT), ("Flags", wintypes.USHORT),
                ("Reserved", wintypes.USHORT), ("VKey", wintypes.USHORT),
                ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG)]


class _DATA(ctypes.Union):
    _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", record_all.RAWINPUTHEADER), ("data", _DATA)]


# -- decoding raw packets --

def key_event(kb):
    return {"scan": kb.MakeCode, "ext": bool(kb.Flags & RI_KEY_E0),
            "up": bool(kb.Flags & RI_KEY_BREAK)}


def mouse_events(ms):
    out = []
    flags = ms.ulButtons & 0xFFFF
    if ms.lLastX or ms.lLastY:
        out.append({"kind": "raw", "dx": ms.lLastX, "dy": ms.lLastY,
                    "absolute": bool(ms.usFlags & W["MOUSE_MOVE_ABSOLUTE"])})
    for name, down, up in BUTTON_BITS:
        if flags & down:
            out.append({"kind": "button", "button": name, "down": True})
        if flags & up:
            out.append({"kind": "button", "button": name, "down": False})
    for bit, horizontal in ((RI_MOUSE_WHEEL, False), (RI_MOUSE_HWHEEL, True)):
        if flags & bit:
            out.append({"kind": "wheel", "horizontal": horizontal,
                        "delta": ctypes.c_short(ms.ulButtons >> 16).value})
    return out


# -- comparing --

def _wanted(events):
    keys, buttons, wheel = [], [], []
    for e in events:
        if e["kind"] == "key":
            if "secret" in e or e.get("vk") == record_all.VK_PACKET or e.get("vk") in SKIP_KEYS:
                continue
            keys.append((e["t"], (e.get("scan"), bool(e.get("extended")),
                                  e["action"] == "up")))
        elif e["action"] in ("down", "up"):
            buttons.append((e["t"], (e["button"], e["action"] == "down")))
        elif e["action"] in ("wheel", "hwheel"):
            wheel.append((e["t"], (e["delta"], e["action"] == "hwheel")))
    return keys, buttons, wheel


def _heard(log):
    keys = [(e["t"], (e["scan"], e["ext"], e["up"])) for e in log if e["kind"] == "key"]
    buttons = [(e["t"], (e["button"], e["down"])) for e in log if e["kind"] == "button"]
    wheel = [(e["t"], (e["delta"], e["horizontal"])) for e in log if e["kind"] == "wheel"]
    return keys, buttons, wheel


def _match(wanted, heard):
    matcher = difflib.SequenceMatcher(None, [d for _t, d in wanted],
                                      [d for _t, d in heard], autojunk=False)
    pairs = [(wanted[b.a + k][0], heard[b.b + k][0])
             for b in matcher.get_matching_blocks() for k in range(b.size)]
    first = next((wanted[i] for i in range(len(wanted))
                  if i not in {b.a + k for b in matcher.get_matching_blocks()
                               for k in range(b.size)}), None)
    return pairs, {"matched": len(pairs), "missing": len(wanted) - len(pairs),
                   "extra": len(heard) - len(pairs), "first_missing": first}


def _turns(events, log):
    recorded, held, total = [], False, [0, 0]
    has_raw = any(e["action"] == "raw" for e in events)
    for e in events:
        if e.get("button") in record_all.TURN and e.get("injected") is not True and has_raw:
            if e["action"] == "down" and not held:
                held, total = True, [0, 0]
            elif e["action"] == "up" and held:
                held = False
                recorded.append(tuple(total))
        elif held and e["action"] == "raw":
            total[0] += e["dx"]
            total[1] += e["dy"]
    heard, held, total, jumps = [], False, [0, 0], 0
    for e in log:
        if e["kind"] == "button" and e["button"] in record_all.TURN:
            if e["down"] and not held:
                held, total, jumps = True, [0, 0], 0
            elif not e["down"] and held:
                held = False
                heard.append((tuple(total), jumps))
        elif held and e["kind"] == "raw":
            if e["absolute"]:
                jumps += 1
            else:
                total[0] += e["dx"]
                total[1] += e["dy"]
    return [{"recorded": a, "heard": b[0] if b else None, "absolute": b[1] if b else 0}
            for a, b in zip(recorded, heard + [None] * (len(recorded) - len(heard)))]


def check(events, log):
    keys_w, buttons_w, wheel_w = _wanted(events)
    keys_h, buttons_h, wheel_h = _heard(log)
    out = {}
    pairs = []
    for name, wanted, heard in (("keys", keys_w, keys_h), ("buttons", buttons_w, buttons_h),
                                ("wheel", wheel_w, wheel_h)):
        found, out[name] = _match(wanted, heard)
        pairs += found
    if pairs:
        shift = [b - a for a, b in pairs]
        out["offset"] = statistics.median(shift)
        jitter = sorted(abs(s - out["offset"]) * 1000 for s in shift)
        out["jitter_ms"] = (statistics.median(jitter),
                            jitter[int(0.95 * (len(jitter) - 1))], jitter[-1])
    out["turns"] = _turns(events, log)
    return out


def verdict(found):
    problems = []
    for name in ("keys", "buttons", "wheel"):
        part = found[name]
        if part["missing"] or part["extra"]:
            problems.append(f"{name}: {part['missing']} missing, {part['extra']} extra"
                            + (f"; the first missing is {part['first_missing'][1]} at "
                               f"{part['first_missing'][0]:.3f}s"
                               if part["first_missing"] else ""))
    if "jitter_ms" in found and found["jitter_ms"][2] > JITTER_LIMIT_MS:
        problems.append(f"timing: worst {found['jitter_ms'][2]:.1f}ms from its place")
    for n, turn in enumerate(found["turns"], 1):
        if turn["heard"] != turn["recorded"]:
            problems.append(f"turn {n}: recorded raw counts {turn['recorded']}, "
                            f"the listener heard {turn['heard']}")
        if turn["absolute"]:
            problems.append(f"turn {n}: {turn['absolute']} absolute cursor jump(s) "
                            f"while the camera button was held")
    return problems


def report(found):
    lines = []
    for name in ("keys", "buttons", "wheel"):
        part = found[name]
        lines.append(f"  {name}: {part['matched']} arrived in order, "
                     f"{part['missing']} missing, {part['extra']} extra")
    if "jitter_ms" in found:
        median, p95, worst = found["jitter_ms"]
        lines.append(f"  timing: within {median:.2f}ms of its place (95% within "
                     f"{p95:.2f}ms, worst {worst:.1f}ms)")
    turns = found["turns"]
    exact = sum(1 for t in turns if t["heard"] == t["recorded"] and not t["absolute"])
    lines.append(f"  camera turns: {exact} of {len(turns)} arrived with exactly the "
                 f"recorded raw counts")
    problems = verdict(found)
    lines += [f"  PROBLEM: {p}" for p in problems] or ["  everything arrived as recorded"]
    return lines


# -- listening --

class Listener:
    """Raw keyboard and mouse input, from any source, on its own thread."""

    def __init__(self):
        self.log, self.ready, self.hwnd = [], threading.Event(), None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.registered = False
        self.thread.start()
        self.ready.wait(5)
        if not self.registered:
            raise OSError(ctypes.get_last_error(), "raw input registration was refused")

    def _run(self):
        self.proc = record_all.WNDPROC(self._message)
        instance = ra.kernel32.GetModuleHandleW(None)
        name = f"input_probe_{os.getpid()}"
        self.cls = record_all.WNDCLASSW(lpfnWndProc=self.proc, hInstance=instance,
                                        lpszClassName=name)
        ra.user32.RegisterClassW(ctypes.byref(self.cls))
        self.hwnd = ra.user32.CreateWindowExW(
            0, name, None, 0, 0, 0, 0, 0, wintypes.HWND(W["HWND_MESSAGE"]), None,
            instance, None)
        devices = (record_all.RAWINPUTDEVICE * 2)(
            record_all.RAWINPUTDEVICE(1, W["HID_USAGE_GENERIC_MOUSE"],
                                      W["RIDEV_INPUTSINK"], self.hwnd),
            record_all.RAWINPUTDEVICE(1, HID_USAGE_KEYBOARD, W["RIDEV_INPUTSINK"],
                                      self.hwnd))
        self.registered = bool(ra.user32.RegisterRawInputDevices(
            devices, 2, ctypes.sizeof(record_all.RAWINPUTDEVICE)))
        self.ready.set()
        msg = wintypes.MSG()
        while ra.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            ra.user32.TranslateMessage(ctypes.byref(msg))
            ra.user32.DispatchMessageW(ctypes.byref(msg))

    def _message(self, hwnd, msg, wparam, lparam):
        if msg == W["WM_INPUT"]:
            now = time.perf_counter()
            data = RAWINPUT()
            size = wintypes.UINT(ctypes.sizeof(data))
            got = ra.user32.GetRawInputData(lparam, W["RID_INPUT"], ctypes.byref(data),
                                            ctypes.byref(size),
                                            ctypes.sizeof(record_all.RAWINPUTHEADER))
            if got not in (0, 0xFFFFFFFF):
                sent = not data.header.hDevice
                if data.header.dwType == RIM_TYPEKEYBOARD:
                    self.log.append({"t": now, "kind": "key", "sent": sent,
                                     **key_event(data.data.keyboard)})
                elif data.header.dwType == W["RIM_TYPEMOUSE"]:
                    for event in mouse_events(data.data.mouse):
                        self.log.append({"t": now, "sent": sent, **event})
        return ra.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def close(self):
        if self.hwnd:
            ra.user32.PostMessageW(self.hwnd, 0x0012, 0, 0)


def shield(text):
    """A black window over every monitor that takes the replay's input."""
    import tkinter
    left, top, width, height = ra.desktop()
    root = tkinter.Tk()
    root.overrideredirect(True)
    root.geometry(f"{width}x{height}+{left}+{top}")
    root.configure(background="black", cursor="crosshair")
    root.attributes("-topmost", True)
    label = tkinter.Label(root, text=text, fg="white", bg="black",
                          font=("Segoe UI", 18))
    label.place(relx=0.18, rely=0.4)
    root.focus_force()
    return root, label


def run(path):
    ra.user32.SetProcessDPIAware()
    head, events, refused = record_all.read_recording(path)
    if refused:
        print(refused)
        return 1
    if head.get("screen") != list(ra.screen()) or head.get("desktop") != ra.desktop():
        print(f"{path.name} was recorded on another screen layout; nothing replayed")
        return 1
    plays, skipped = playable(events)
    if not plays:
        print(f"{path.name} has nothing the probe can replay")
        return 1
    head = {k: v for k, v in head.items() if k not in ("window", "start_shot")}
    print(f"{path.name}: {len(plays)} events over {plays[-1]['t']:.1f}s"
          + (f" ({skipped} Win or Alt key event(s) skipped)" if skipped else ""))
    listener = Listener()
    player = record_all.Replay(plays, head.get("cursor"), head, path.parent)
    taps = record_all.CtrlTaps(own=lambda: player.own_ctrl)
    root, label = shield(f"Input probe: replaying {path.name}\nHands off the mouse "
                         f"and keyboard. Ctrl twice stops it.\nStarting in 3 seconds...")
    state = {"started": None, "stopped": None, "worker": None}

    def front():
        return ra.user32.GetForegroundWindow()

    def begin():
        here = ra.user32.GetAncestor(root.winfo_id(), GA_ROOT)
        state["shield"] = here
        if front() != here:
            state["stopped"] = ("the black screen never got the foreground, so nothing "
                                "was replayed")
            root.destroy()
            return
        label.configure(text=f"Replaying {path.name} ({plays[-1]['t']:.0f}s)...\n"
                             f"Ctrl twice stops it.")
        state["started"] = time.perf_counter()
        state["worker"] = threading.Thread(target=player.play, daemon=True)
        state["worker"].start()
        poll()

    def poll():
        reason = should_stop(taps.step(time.perf_counter()), state["shield"], front())
        if reason and not player.stop.is_set():
            state["stopped"] = reason
            player.stop.set()
        if player.finished:
            root.after(300, root.destroy)
            return
        root.after(int(record_all.POLL_GAP * 1000), poll)

    root.after(3000, begin)
    try:
        root.mainloop()
    finally:
        player.stop.set()
        if state["worker"] is not None:
            state["worker"].join()
        listener.close()
    if state["stopped"]:
        print(f"stopped: {state['stopped']}")
    started = state["started"] or 0.0
    after = [e for e in listener.log if e["t"] >= started]
    heard = [e for e in after if e["sent"]]
    by_hand = len(after) - len(heard)
    found = check(plays, heard)
    out = path.with_name(path.stem + "_probe.jsonl")
    out.write_text("\n".join(json.dumps(e) for e in heard) + "\n", encoding="utf-8")
    print(f"replayed {player.played} of {len(plays)} events; the listener heard "
          f"{len(heard)} raw packets from the replay (saved in {out.name})"
          + (f"; {by_hand} from the real mouse or keyboard were left out" if by_hand else ""))
    print("\n".join(report(found)))
    return 1 if verdict(found) else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args:
        target = Path(args[0]) if Path(args[0]).exists() else ra.OUT / args[0]
    else:
        target = max(ra.OUT.glob(f"*{record_all.SUFFIX}"), default=None)
    if target is None or not target.exists():
        sys.exit(f"no recording {args[0] if args else 'in ' + str(ra.OUT)}")
    sys.exit(run(target))
