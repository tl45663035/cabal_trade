"""recorder/input_probe.py: the comparison between a recording and what a
game-like raw input listener received while it was replayed, and the raw
packet decoding. No input is sent."""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent / "src_1080p"
os.environ["CABAL_CONFIG"] = "bot"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(SRC.parent / "tools"))
import no_input
TRIPPED = no_input.arm()
import ctypes
import input_probe as probe

checks = 0
failures = []


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


def key(t, vk, scan, up, ext=False, **more):
    return {"t": t, "kind": "key", "action": "up" if up else "down", "vk": vk,
            "scan": scan, "extended": ext, "name": "", "repeat": False, **more}


def button(t, name, down, x=100, y=100):
    return {"t": t, "kind": "mouse", "action": "down" if down else "up",
            "button": name, "x": x, "y": y, "injected": False}


def raw(t, dx, dy):
    return {"t": t, "kind": "mouse", "action": "raw", "dx": dx, "dy": dy}


RECORDING = [
    key(0.10, 87, 17, False), key(0.30, 87, 17, True),
    key(0.40, 65, 30, False), key(0.45, 65, 30, True),
    button(0.50, "right", True),
    {"t": 0.51, "kind": "mouse", "action": "move", "x": 104, "y": 100, "injected": False},
    raw(0.511, 5, 0), raw(0.52, 7, -1),
    button(0.60, "right", False, 112, 99),
    button(0.70, "left", True), button(0.75, "left", False),
    {"t": 0.80, "kind": "mouse", "action": "wheel", "delta": -120, "x": 1, "y": 1,
     "injected": False},
    key(0.90, 38, 72, False, ext=True), key(0.95, 38, 72, True, ext=True),
]


def heard(shift=2.0, wobble=0.0, drop=None, turn=(5, 0, 7, -1), absolute_in_turn=False):
    """What the listener would hear for RECORDING, shifted in time."""
    out = []
    for n, e in enumerate(RECORDING):
        if drop is not None and n == drop:
            continue
        t = e["t"] + shift + (wobble if n % 2 else -wobble)
        if e["kind"] == "key":
            out.append({"t": t, "kind": "key", "scan": e["scan"], "ext": e["extended"],
                        "up": e["action"] == "up"})
        elif e["action"] in ("down", "up"):
            out.append({"t": t, "kind": "button", "button": e["button"],
                        "down": e["action"] == "down"})
            if e["button"] == "right" and e["action"] == "down":
                out.append({"t": t + 0.011, "kind": "raw", "dx": turn[0], "dy": turn[1],
                            "absolute": False})
                out.append({"t": t + 0.02, "kind": "raw", "dx": turn[2], "dy": turn[3],
                            "absolute": False})
                if absolute_in_turn:
                    out.append({"t": t + 0.03, "kind": "raw", "dx": 30000, "dy": 30000,
                                "absolute": True})
        elif e["action"] == "wheel":
            out.append({"t": t, "kind": "wheel", "delta": e["delta"], "horizontal": False})
    return sorted(out, key=lambda x: x["t"])


found = probe.check(RECORDING, heard(wobble=0.0004))
check(found["keys"]["missing"] == 0 and found["keys"]["extra"] == 0
      and found["keys"]["matched"] == 6,
      f"every key arrives, got {found['keys']}")
check(found["buttons"]["matched"] == 4 and found["wheel"]["matched"] == 1,
      f"every button and wheel turn arrives, got {found['buttons']} {found['wheel']}")
check(abs(found["offset"] - 2.0) < 1e-3 and found["jitter_ms"][2] <= 0.81,
      f"the start offset is taken out, leaving only the wobble, got {found}")
check(found["turns"] == [{"recorded": (12, -1), "heard": (12, -1), "absolute": 0},
                        {"recorded": (0, 0), "heard": (0, 0), "absolute": 0}],
      f"each turn's raw counts are summed on both sides, got {found['turns']}")
check(probe.verdict(found) == [], f"a faithful replay has no problems, got "
                                  f"{probe.verdict(found)}")

found = probe.check(RECORDING, heard(drop=2))
check(found["keys"]["missing"] == 1, f"a missing key is counted, got {found['keys']}")
check(any("key" in line for line in probe.verdict(found)), "and reported")

found = probe.check(RECORDING, heard(turn=(5, 0, 6, -1)))
check(found["turns"][0]["heard"] == (11, -1) and any("turn" in l for l in probe.verdict(found)),
      f"a turn that arrives short by one count is reported, got {found['turns']}")

found = probe.check(RECORDING, heard(absolute_in_turn=True))
check(found["turns"][0]["absolute"] == 1 and any("jump" in l for l in probe.verdict(found)),
      "an absolute jump during a turn is reported")

stopped = [e for e in heard() if e["t"] < 2.55]
found = probe.check(RECORDING, stopped)
check(any("missing" in l for l in probe.verdict(found)),
      "a replay stopped early shows what went missing")

check(probe.check(RECORDING, [])["keys"]["matched"] == 0, "nothing heard is handled")

# Raw packets, decoded as Windows hands them over.
kb = probe.RAWKEYBOARD(MakeCode=72, Flags=probe.RI_KEY_E0 | probe.RI_KEY_BREAK, VKey=38)
check(probe.key_event(kb) == {"scan": 72, "ext": True, "up": True},
      f"a raw extended key release decodes, got {probe.key_event(kb)}")
ms = probe.RAWMOUSE(usFlags=0, ulButtons=probe.RI_MOUSE_RIGHT_BUTTON_DOWN, lLastX=-3, lLastY=2)
check(probe.mouse_events(ms) == [{"kind": "raw", "dx": -3, "dy": 2, "absolute": False},
                                 {"kind": "button", "button": "right", "down": True}],
      f"a raw move with a right press decodes, got {probe.mouse_events(ms)}")
wheel = probe.RAWMOUSE(usFlags=0, ulButtons=probe.RI_MOUSE_WHEEL | ((-120 & 0xFFFF) << 16))
check(probe.mouse_events(wheel) == [{"kind": "wheel", "delta": -120, "horizontal": False}],
      f"a wheel turn decodes with its sign, got {probe.mouse_events(wheel)}")
check(ctypes.sizeof(probe.RAWINPUT) == 48, "the raw input layout matches Windows (64-bit)")

# What may be replayed into the probe's black screen: no Win or Alt keys, so the
# replay can't open the Start menu or switch away (Alt+Tab, Alt+F4).
mixed = [key(0.1, 87, 17, False), key(0.2, 91, 91, False, ext=True),
         key(0.3, 164, 56, False), key(0.35, 9, 15, False), key(0.4, 9, 15, True),
         key(0.45, 164, 56, True), key(0.5, 92, 92, True, ext=True), key(0.6, 87, 17, True)]
plays, skipped = probe.playable(mixed)
check([e["vk"] for e in plays] == [87, 9, 9, 87] and skipped == 4,
      f"Win and Alt are skipped, the rest kept, got {[e['vk'] for e in plays]} {skipped}")
check(probe.should_stop(False, 100, 100) is None, "the black screen in front: carry on")
check("Ctrl" in (probe.should_stop(True, 100, 100) or ""), "Ctrl twice stops it")
check("foreground" in (probe.should_stop(False, 100, 200) or ""),
      "anything else in front stops it at once")

check(not TRIPPED, f"tests sent real input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
