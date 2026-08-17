import csv
import ctypes
import io
import json
import re
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE / "calibration.json"

_CACHE = None

DEFAULTS = {
    "timing": {
        "action_gap": 0.05,
        "key_hold": 0.02,
        "focus_settle": 0.35,
        "wheel_gap": 0.12,
    },
    "input": {
        "INPUT_MOUSE": 0,
        "INPUT_KEYBOARD": 1,
        "KEYEVENTF_KEYUP": 0x0002,
        "KEYEVENTF_SCANCODE": 0x0008,
        "MAPVK_VK_TO_VSC": 0,
        "MOUSEEVENTF_LEFTDOWN": 0x0002,
        "MOUSEEVENTF_LEFTUP": 0x0004,
        "MOUSEEVENTF_RIGHTDOWN": 0x0008,
        "MOUSEEVENTF_RIGHTUP": 0x0010,
        "MOUSEEVENTF_WHEEL": 0x0800,
        "WHEEL_DELTA": 120,
        "VK_I": 0x49,
        "VK_MENU": 0x12,
        "VK_ESCAPE": 0x1B,
        "INPUT_STRUCT_SIZE": 40,
    },
    "game": {
        "title_hint": "PlayCabal",
    },
    "panel_layout": {
        "slot_one": [-496, -596],
        "slot_pitch": [73.3, 73.4],
        "tab_one": [-524, -668],
        "tab_pitch": 69.6,
    },
    "game_facts": {
        "grid_size": 8,
        "agent_shop_tab": 8,
        "agent_shop_slot": [1, 7],
        "work_tab": 4,
        "shop_capacity": 30,
        "shop_visible": 10,
    },
}


def _merge_keeping_existing(fresh: dict, existing: dict) -> dict:
    out = dict(fresh)
    for section in DEFAULTS:
        merged = dict(DEFAULTS[section])
        merged.update(existing.get(section) or {})
        out[section] = merged
    return out


def screen_size() -> "tuple[int, int]":
    import mss
    with mss.MSS() as sct:
        m = sct.monitors[1]
    return m["width"], m["height"]


def resolution_key(size=None) -> str:
    w, h = size or screen_size()
    return f"{w}x{h}"


def load_shared() -> dict:
    data = {}
    if OUT.exists():
        try:
            data = json.loads(OUT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    out = {}
    for section, default in DEFAULTS.items():
        merged = dict(default)
        merged.update(data.get(section) or {})
        out[section] = merged
    return out


def load(force: bool = False) -> dict:
    global _CACHE
    if _CACHE is None or force:
        if not OUT.exists():
            raise RuntimeError(
                f"{OUT.name} is missing. Run `py src/calibration.py` once to "
                f"measure this screen; nothing else in src/ carries a "
                f"coordinate of its own.")
        _CACHE = json.loads(OUT.read_text(encoding="utf-8"))

    data = _CACHE
    key = resolution_key()
    per = (data.get("by_resolution") or {}).get(key)
    if per is None:
        known = sorted((data.get("by_resolution") or {}))
        raise RuntimeError(
            f"calibration.json has no measurements for {key}. It knows "
            f"{known or 'nothing'}. Run `py src/calibration.py` on this "
            f"monitor -- the positions from another resolution would be wrong "
            f"here, so they are not reused.")

    merged = dict(per)
    for shared in DEFAULTS:
        merged[shared] = data.get(shared) or DEFAULTS[shared]
    merged["resolution"] = key
    return merged

TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

PARK_F = (0.5078, 0.7137)
ALZ_SEARCH_F = (0.8750, 0.6245, 0.9805, 0.6567)
TOP_STRIP_F = (0.0000, 0.0197, 0.5078, 0.1585)
TAB_BAND_F = (0.0000, 0.0270, 0.2734, 0.0709)
FAV_BAND_F = (0.2422, 0.7100, 0.4648, 0.7465)
BOUNDARY_WINDOW_F = (0.0781, 0.1289)
SLOT_PITCH_F = (0.0266, 0.0312)


def _client_rect():
    win = find_game_window()
    if win is not None:
        x, y, w, h = win[2]
        if w > 100 and h > 100:
            return x, y, w, h
    w, h = screen_size()
    return 0, 0, w, h


def _box(frac, rect=None):
    x, y, w, h = rect or _client_rect()
    return (round(x + frac[0] * w), round(y + frac[1] * h),
            round(x + frac[2] * w), round(y + frac[3] * h))


def _point(frac, rect=None):
    x, y, w, h = rect or _client_rect()
    return (round(x + frac[0] * w), round(y + frac[1] * h))

GRID = 8
GRID_FIT_MIN = 0.02
PANEL_OPEN_CHANGE = 0.30
ACTION_GAP = 0.05


def grab() -> Image.Image:
    import mss
    with mss.MSS() as sct:
        raw = sct.grab(sct.monitors[1])
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def park() -> None:
    ctypes.windll.user32.SetCursorPos(*_point(PARK_F))
    time.sleep(0.25)


def _mouse_event(flags: int):
    from open_inventory import _Input, _InputUnion, _MouseInput
    return _Input(type=0, u=_InputUnion(mi=_MouseInput(0, 0, 0, flags, 0, None)))


def _button(down: int, up: int, x: int, y: int, settle: float) -> None:
    from open_inventory import _user32
    _user32.SetCursorPos(int(x), int(y))
    _user32.SendInput(1, ctypes.byref(_mouse_event(down)),
                      ctypes.sizeof(_mouse_event(down)))
    try:
        pass
    finally:
        _user32.SendInput(1, ctypes.byref(_mouse_event(up)),
                          ctypes.sizeof(_mouse_event(up)))
    time.sleep(settle)


def click(x: int, y: int, settle: float = None) -> None:
    shared = load_shared()
    _button(shared["input"]["MOUSEEVENTF_LEFTDOWN"],
            shared["input"]["MOUSEEVENTF_LEFTUP"], x, y,
            shared["timing"]["action_gap"] if settle is None else settle)


def right_click(x: int, y: int, settle: float = None) -> None:
    shared = load_shared()
    _button(shared["input"]["MOUSEEVENTF_RIGHTDOWN"],
            shared["input"]["MOUSEEVENTF_RIGHTUP"], x, y,
            shared["timing"]["action_gap"] if settle is None else settle)


def ocr(image: Image.Image, box, scale: int = 3, min_conf: float = 45.0):
    crop = image.crop(box)
    crop = crop.resize((crop.width * scale, crop.height * scale),
                       Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    run = subprocess.run(
        [TESSERACT, "stdin", "stdout", "--psm", "11", "tsv"],
        input=buf.getvalue(), capture_output=True, timeout=60)
    found = []
    for row in csv.DictReader(
            io.StringIO(run.stdout.decode("utf-8", "replace")), delimiter="\t"):
        try:
            conf = float(row["conf"])
        except (TypeError, ValueError):
            continue
        text = (row.get("text") or "").strip()
        if not text or conf < min_conf:
            continue
        x = box[0] + int(row["left"]) / scale + int(row["width"]) / scale / 2
        y = box[1] + int(row["top"]) / scale + int(row["height"]) / scale / 2
        found.append((text, round(conf), (round(x), round(y))))
    return found


DIGIT_PSM = "13"
DIGIT_WHITELIST = "0123456789,"
_NOT_DIGIT = re.compile(r"[^0-9]")


def read_digits(image: Image.Image, box, scale: int = 3):
    crop = image.crop(box)
    crop = crop.resize((crop.width * scale, crop.height * scale),
                       Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    run = subprocess.run(
        [TESSERACT, "stdin", "stdout", "--psm", DIGIT_PSM,
         "-c", "tessedit_char_whitelist=" + DIGIT_WHITELIST],
        input=buf.getvalue(), capture_output=True, timeout=60)
    digits = _NOT_DIGIT.sub("", run.stdout.decode("utf-8", "replace"))
    return int(digits) if digits else None


def fit_periodic(profile, n, lo, hi, step=0.02):
    d = np.abs(np.diff(profile))
    d = d / (d.max() or 1.0)
    length = len(d)
    best = (-1.0, None, None)
    for pitch in np.arange(lo, hi, step):
        if pitch * n >= length - 1:
            continue
        for start in np.arange(0, length - 1 - pitch * n, 0.5):
            pos = (start + pitch * np.arange(n + 1)).round().astype(int)
            score = float(d[pos].min())
            if score > best[0]:
                best = (score, float(pitch), float(start))
    return best


ALZ_SEARCH = None
ALZ_BRIGHT = 110
ALZ_SATURATION = 45
ALZ_MIN_PIXELS = 150
ALZ_LINE_HALF = 14
ALZ_MAX_WIDTH_FRACTION = 0.95
ALZ_MIN_HEIGHT = 8
ALZ_MAX_HEIGHT = 30


def find_alz(image: Image.Image, search=None):
    search = search or _box(ALZ_SEARCH_F)
    crop = image.crop(search)
    px = crop.load()
    xs, ys = [], []
    for y in range(crop.height):
        for x in range(crop.width):
            r, g, b = px[x, y]
            hi, lo = max(r, g, b), min(r, g, b)
            if hi > ALZ_BRIGHT and hi - lo > ALZ_SATURATION:
                xs.append(x)
                ys.append(y)
    if len(xs) < ALZ_MIN_PIXELS:
        return None

    rows = {}
    for y in ys:
        rows[y] = rows.get(y, 0) + 1
    peak = max(rows, key=rows.get)
    keep = [(x, y) for x, y in zip(xs, ys) if abs(y - peak) <= ALZ_LINE_HALF]
    if len(keep) < ALZ_MIN_PIXELS:
        return None
    kx = [x for x, _ in keep]
    ky = [y for _, y in keep]
    box = (search[0] + min(kx), search[1] + min(ky),
           search[0] + max(kx), search[1] + max(ky))

    width, height = box[2] - box[0], box[3] - box[1]
    span_w = search[2] - search[0]
    if width >= span_w * ALZ_MAX_WIDTH_FRACTION:
        return None
    if not ALZ_MIN_HEIGHT <= height <= ALZ_MAX_HEIGHT:
        return None
    return box


def _pitch_bounds():
    w = _client_rect()[2]
    return SLOT_PITCH_F[0] * w, SLOT_PITCH_F[1] * w


def calibrate_inventory(verbose=True):
    say = print if verbose else (lambda *a: None)
    image = grab()
    alz = find_alz(image)
    if alz is None:
        raise RuntimeError(
            "the Alz balance was not found, so the Inventory panel is not "
            "open. Nothing measured.")
    anchor = (alz[2], alz[1])
    say(f"  Alz box {alz}   anchor {anchor}")

    layout = load_shared()["panel_layout"]
    s1x, s1y = layout["slot_one"]
    spx, spy = layout["slot_pitch"]
    t1x, t1y = layout["tab_one"]
    tp = layout["tab_pitch"]

    tabs = {str(k + 1): [round(anchor[0] + t1x + tp * k),
                         round(anchor[1] + t1y)] for k in range(GRID)}
    slots = {}
    for row in range(1, GRID + 1):
        for col in range(1, GRID + 1):
            slots[f"{row}x{col}"] = [
                round(anchor[0] + s1x + spx * (col - 1)),
                round(anchor[1] + s1y + spy * (row - 1)),
            ]
    say(f"  tab I {tabs['1']}  tab VIII {tabs[str(GRID)]}")
    say(f"  slot (1,1) {slots['1x1']}  (1,7) {slots['1x7']}  "
        f"(8,8) {slots[f'{GRID}x{GRID}']}")

    cx, cy, cw, chh = _client_rect()
    every = list(slots.values()) + list(tabs.values())
    outside = [p for p in every
               if not (cx <= p[0] <= cx + cw and cy <= p[1] <= cy + chh)]
    if outside:
        raise RuntimeError(
            f"{len(outside)} of {len(every)} positions fall outside the game "
            f"window ({cx},{cy} {cw}x{chh}) -- e.g. {outside[0]}. The anchor "
            f"at {anchor} must be wrong. Nothing measured.")

    return {
        "alz_box": list(alz),
        "anchor": list(anchor),
        "tabs": tabs,
        "tab_pitch": tp,
        "slots": slots,
        "slot_pitch": [spx, spy],
        "placed_from": "panel_layout offsets, not fitted",
    }


def _trade_window_open() -> bool:
    try:
        words = ocr(grab(), _box(TOP_STRIP_F))
    except Exception:
        return False
    return any(t.lower() == "register" for t, _c, _p in words)


def calibrate_shop(verbose=True):
    say = print if verbose else (lambda *a: None)
    image = grab()

    top_strip = _box(TOP_STRIP_F)
    words = ocr(image, top_strip)
    named = {t.lower(): (c, p) for t, c, p in words}

    reg = next((p for t, c, p in words if t.lower() == "register"), None)
    if reg is None:
        raise RuntimeError(
            "the Register tab was not found, so the Trade window is not open "
            "on a tab this can measure. Nothing written.")
    say(f"  Register {reg}")

    band = np.asarray(image.crop(_box(TAB_BAND_F)).convert("L"), dtype=float)
    d = np.abs(np.diff(band.mean(axis=0)))
    edges = sorted(int(i) for i in np.argsort(d)[::-1][:40])
    picked = []
    for i in edges:
        if all(abs(i - p) > 15 for p in picked):
            picked.append(i)
    _cw = _client_rect()[2]
    _lo, _hi = BOUNDARY_WINDOW_F[0] * _cw, BOUNDARY_WINDOW_F[1] * _cw
    boundary = next((x for x in picked if _lo < x < _hi), None)
    if boundary is None:
        raise RuntimeError("could not find the Purchase/Register boundary.")
    purchase = [2 * boundary - reg[0], reg[1]]
    say(f"  Purchase {purchase}  (mirrored about the boundary at x={boundary})")

    sort = None
    for want in ("price", "low", "high", "to"):
        hits = [(t, c, p) for t, c, p in words
                if want in t.lower() and 150 < p[1] < 230]
        if hits:
            t, c, p = max(hits, key=lambda h: h[1])
            sort = p
            say(f"  sort dropdown anchored on {t!r} (conf {c})")
            break
    if sort is None:
        raise RuntimeError(
            "the sort dropdown was not found: none of 'price', 'low', 'high' "
            "or 'to' read anywhere on the filter row. Is the Trade window on "
            "the Purchase tab?")
    say(f"  sort dropdown at {sort}")

    FAV = _box(FAV_BAND_F)
    prof = np.asarray(image.crop(FAV).convert("L"), dtype=float).mean(axis=0)
    floor, ceiling = prof.min(), prof.max()
    cut = floor + (ceiling - floor) * 0.6
    peaks, run = [], []
    for i, v in enumerate(prof):
        if v >= cut:
            run.append(i)
        elif run:
            peaks.append(max(run, key=lambda j: prof[j]))
            run = []
    if run:
        peaks.append(max(run, key=lambda j: prof[j]))
    merged = []
    for i in peaks:
        if merged and i - merged[-1] < 40:
            if prof[i] > prof[merged[-1]]:
                merged[-1] = i
        else:
            merged.append(i)
    peaks = merged
    favourites = [[FAV[0] + i, FAV[1] + (FAV[3] - FAV[1]) // 2] for i in peaks]
    gaps = np.diff([p[0] for p in favourites]) if len(favourites) > 1 else []
    f_pitch = float(np.mean(gaps)) if len(gaps) else 0.0
    say(f"  favourites: {len(favourites)} found, pitch {f_pitch:.2f}px, "
        f"first {favourites[0] if favourites else None} "
        f"last {favourites[-1] if favourites else None}")
    if len(favourites) != 10:
        raise RuntimeError(
            f"expected 10 favourite slots, found {len(favourites)} at "
            f"{[p[0] for p in favourites]}. Not writing a calibration that "
            f"does not describe the row.")
    if len(gaps) and (max(gaps) - min(gaps)) > 4:
        raise RuntimeError(
            f"the favourite slots are not evenly spaced: gaps {list(gaps)}. "
            f"Something other than a slot was picked up.")

    return {
        "purchase_tab": purchase,
        "register_tab": list(reg),
        "tab_boundary_x": boundary,
        "sort_dropdown": list(sort),
        "favourites": favourites,
        "favourite_pitch": round(f_pitch, 2),
        "evidence": {
            "register_conf": named.get("register", (None,))[0],
            "favourite_pitch_spread": (int(max(gaps) - min(gaps))
                                      if len(gaps) else 0),
            "purchase_is_derived": True,
        },
    }


def find_game_window(title: str = "PlayCabal"):
    user32 = ctypes.windll.user32
    found = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if title.casefold() in buf.value.casefold():
            found.append((hwnd, buf.value))
            return False
        return True

    user32.EnumWindows(proto(cb), None)
    if not found:
        return None
    hwnd, name = found[0]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    r = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = (ctypes.c_long * 2)(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return hwnd, name, [pt[0], pt[1], r.right, r.bottom]


def main() -> None:
    from open_inventory import VK_I, VK_ESCAPE, focus_game, press

    shared = load_shared()
    gap = shared["timing"]["action_gap"]
    facts = shared["game_facts"]

    if not focus_game():
        raise RuntimeError(
            f"could not bring the {shared['game']['title_hint']!r} window to "
            f"the foreground. Nothing measured.")

    print("inventory:")

    for attempt in (1, 2):
        park()
        if find_alz(grab()) is not None:
            break
        press(VK_I)
        time.sleep(gap)
        park()
        if find_alz(grab()) is not None:
            if attempt == 2:
                print("  (the panel had been left open by an earlier run; "
                      "pressed I twice to get back to a known state)")
            break
        if attempt == 2:
            raise RuntimeError(
                "pressed I twice and the Alz balance never appeared, so the "
                "Inventory panel is not opening. Nothing measured.")

    inventory = calibrate_inventory()

    print("opening the Agent Shop:")
    tab = inventory["tabs"][str(facts["agent_shop_tab"])]
    row, col = facts["agent_shop_slot"]
    key = inventory["slots"][f"{row}x{col}"]
    print(f"  tab {facts['agent_shop_tab']} at {tab}")
    click(*tab)

    for attempt in (1, 2):
        if _trade_window_open():
            break
        print(f"  right-clicking slot ({row},{col}) at {key}")
        right_click(*key)
        time.sleep(gap)
        park()
        if _trade_window_open():
            if attempt == 2:
                print("  (the shop had been left open by an earlier run; "
                      "clicked twice to get back to a known state)")
            break
        if attempt == 2:
            raise RuntimeError(
                "right-clicked the Agent Shop key twice and the Trade window "
                "never appeared. Nothing written.")

    print("agent shop:")
    park()
    shop = calibrate_shop()

    win = find_game_window()
    measured = {
        "screen": list(screen_size()),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "game": {
            "title_seen": win[1] if win else None,
            "client_rect": win[2] if win else None,
        },
        "alz_detect": {
            "search": list(_box(ALZ_SEARCH_F)),
            "bright": ALZ_BRIGHT,
            "saturation": ALZ_SATURATION,
            "min_pixels": ALZ_MIN_PIXELS,
            "line_half": ALZ_LINE_HALF,
        },
        "inventory": inventory,
        "shop": shop,
    }

    existing = {}
    if OUT.exists():
        try:
            existing = json.loads(OUT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}

    out = dict(existing)
    for section in DEFAULTS:
        merged = dict(DEFAULTS[section])
        merged.update(existing.get(section) or {})
        out[section] = merged
    out.setdefault("by_resolution", {})[resolution_key()] = measured
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}  [{resolution_key()}]")
    print(f"  resolutions in the file: {sorted(out['by_resolution'])}")

    close_everything(verbose=True)


def close_everything(verbose: bool = False) -> None:
    from open_inventory import VK_I, VK_ESCAPE, focus_game, press
    gap = load_shared()["timing"]["action_gap"]
    if not focus_game():
        return
    if verbose:
        print("restoring the default state:")

    press(VK_ESCAPE)
    time.sleep(gap)
    if verbose:
        print("  Escape: Trade window closed")

    park()
    if find_alz(grab()) is None:
        if verbose:
            print("  Inventory already closed")
        return
    press(VK_I)
    time.sleep(gap)
    park()
    if verbose:
        if find_alz(grab()) is None:
            print("  I: Inventory closed")
        else:
            print("  I: pressed, but the balance is still visible -- the "
                  "panel did not close. Close it by hand.")


if __name__ == "__main__":
    main()
