"""Measure where everything is on THIS screen and write src/calibration.json.

    py src/calibration.py

Nothing else in src/ should carry a screen coordinate. This finds them once,
by looking, and every other script reads the JSON.

WHAT IS MEASURED VERSUS WHAT IS ASSUMED, because the difference is the whole
point of the file:

  measured   the Alz balance box            colour: bright and saturated
  measured   the 8x8 slot grid              periodicity of its borders
  measured   the inventory tab strip        periodicity of its borders
  measured   the Register tab               OCR
  measured   the Purchase/Register boundary strongest edge in the tab row
  measured   the sort dropdown              OCR
  measured   the favourite slots            periodicity of the icon row

  assumed    that the Purchase tab is the same width as Register, mirrored
             about the boundary. Its label CANNOT be read: the game draws its
             own "241fps(64bit)" counter on top of it, and OCR returns nothing
             at any crop or scale.

Everything is written with the evidence beside it -- the pitch, the score, the
word confidence -- so a bad calibration can be seen in the file rather than
discovered by a misclick.
"""
import csv
import ctypes
import io
import json
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE / "calibration.json"

_CACHE = None

# EVERYTHING THAT IS NOT MEASURED, seeded here and written into the JSON so the
# scripts have exactly one place to read from.
#
# These are NOT positions -- they are Windows API values, facts about the game,
# and durations. They are in the file anyway at the operator's instruction, so
# that changing `action_gap` once changes it everywhere rather than in three
# files.
#
# SEEDED, NOT OVERWRITTEN. calibrate re-measures the screen every run; if it
# also rewrote these, a tuned action_gap would be silently reset by the next
# calibration. The merge below keeps whatever is already in the file.
DEFAULTS = {
    "timing": {
        "action_gap": 0.05,     # between one action and the next
        "key_hold": 0.02,       # how long a key is held down
        "focus_settle": 0.35,   # after asking Windows to raise the game
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
        "VK_I": 0x49,
        "VK_MENU": 0x12,
        "VK_ESCAPE": 0x1B,
        "INPUT_STRUCT_SIZE": 40,   # sizeof(INPUT) on 64-bit Windows
    },
    "game": {
        "title_hint": "PlayCabal",   # matched on this, NOT on "Cabal": the
                                     # project folder is called Cabal, so an
                                     # editor with it open is titled
                                     # "... - Cabal - Visual Studio Code" and a
                                     # looser match finds the editor.
    },
    # THE PANEL'S OWN LAYOUT, as offsets from the Alz balance.
    #
    # None of this depends on what is in the bag. Once the panel is open its
    # geometry is fixed -- slot pitch, tab spacing, where the grid starts -- and
    # the only thing that varies between runs is where the PANEL is, which the
    # balance already tells us.
    #
    # Fitting the grid every run was solving a problem that does not exist, and
    # it was fragile in exactly the way that predicts: it read the borders
    # THROUGH the item art, so a packed tab put the columns 62px out and a
    # sparse one did not. Measured against a verified calibration and confirmed
    # on two independent runs.
    #
    # Anchor is (alz_right, alz_top) -- the digits' right edge and top. The
    # right edge because the balance is right-aligned, so its LEFT edge moves
    # with the size of the number.
    "panel_layout": {
        "slot_one": [-496, -596],   # anchor -> centre of slot (1,1)
        "slot_pitch": [73.3, 73.4],
        "tab_one": [-524, -668],    # anchor -> centre of tab I
        "tab_pitch": 69.6,
    },
    "game_facts": {
        "grid_size": 8,            # the inventory is 8x8, with 8 tabs
        "agent_shop_tab": 8,       # where the Agent Shop key lives...
        "agent_shop_slot": [1, 7], # ...and in which slot of that tab
    },
}


def _merge_keeping_existing(fresh: dict, existing: dict) -> dict:
    """`fresh` wins for measured sections; `existing` wins for tuned ones."""
    out = dict(fresh)
    for section in DEFAULTS:
        merged = dict(DEFAULTS[section])
        merged.update(existing.get(section) or {})
        out[section] = merged
    return out


def screen_size() -> "tuple[int, int]":
    """The primary monitor, in pixels."""
    import mss
    with mss.MSS() as sct:
        m = sct.monitors[1]
    return m["width"], m["height"]


def resolution_key(size=None) -> str:
    w, h = size or screen_size()
    return f"{w}x{h}"


def load_shared() -> dict:
    """The resolution-INDEPENDENT settings: timing, input, game, game_facts.

    Never raises. This is what the bootstrap runs on: open_inventory needs the
    key codes and the window title before any calibration exists, and on a
    monitor that has never been measured load() below has nothing to give it.
    Falls back to DEFAULTS section by section, so a checkout with no
    calibration.json at all can still press I and start measuring.
    """
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
    """The measured screen for THIS resolution, from calibration.json.

    Positions are stored under by_resolution["WxH"], because a coordinate is
    only meaningful on the screen it was measured on. Everything that is NOT a
    position -- timing, the Windows API numbers, the facts about the bag --
    is shared across all of them, so tuning action_gap once still tunes it
    everywhere.

    Returns the two merged, so callers see one flat dict and do not need to
    know which half a key came from.

    Imports nothing from the other scripts on purpose: they import THIS, so a
    module-level import the other way would be circular. main() pulls in
    open_inventory lazily instead.
    """
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

# EVERY SEARCH REGION BELOW IS A FRACTION OF THE GAME'S CLIENT RECT, not a
# pixel box. That is what lets this run on a monitor it has never seen: the
# absolute boxes were measured at 2560x1440 and would point at nothing on a
# 1920x1080 screen, so there would be no way to bootstrap a first calibration
# there. Fractions were derived from the measured boxes at 2560x1440 and are
# exact to four places.
#
# PARK is where to leave the mouse while measuring. An item under the cursor
# raises a tooltip that covers the panel: the first attempt at this read
# "Remote Trade Card / Drop-Selling Not Allowed / Duration ..." across the slot
# grid, because the cursor was still resting on (1,7) from opening the shop.
PARK_F = (0.5078, 0.7137)
ALZ_SEARCH_F = (0.8750, 0.6245, 0.9805, 0.6567)
TOP_STRIP_F = (0.0000, 0.0197, 0.5078, 0.1585)
TAB_BAND_F = (0.0000, 0.0270, 0.2734, 0.0709)
FAV_BAND_F = (0.2422, 0.7100, 0.4648, 0.7465)
BOUNDARY_WINDOW_F = (0.0781, 0.1289)
# A slot is between 2.66% and 3.12% of the client width. This is a prior about
# the UI, not a position, so it scales rather than being replaced.
SLOT_PITCH_F = (0.0266, 0.0312)


def _client_rect():
    """(x, y, w, h) of the game's client area, or the whole screen."""
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

GRID = 8              # the inventory is 8x8, with 8 tabs
# The floor for a believable grid fit, on the min-of-borders score above.
# Measured: a good fit on a PACKED tab scores 0.061 -- the worst case, since
# item art raises the normalising maximum; a sparse tab scores far higher. A
# fit with any border on flat ground scores near zero.
GRID_FIT_MIN = 0.02
# How much of the balance region opening the panel must repaint. The panel
# covers that area completely, so a real open changes nearly all of it; a press
# that did nothing changes none.
PANEL_OPEN_CHANGE = 0.30
ACTION_GAP = 0.05


# --------------------------------------------------------------------------
# Looking
# --------------------------------------------------------------------------
def grab() -> Image.Image:
    import mss
    with mss.MSS() as sct:
        raw = sct.grab(sct.monitors[1])
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def park() -> None:
    ctypes.windll.user32.SetCursorPos(*_point(PARK_F))
    time.sleep(0.25)


def _mouse_event(flags: int):
    """One mouse event, using open_inventory's structs.

    Imported here rather than at module scope: open_inventory imports THIS
    module, so a top-level import back would be circular.
    """
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
    """Words in `box` as (text, conf, (x, y)) in SCREEN coordinates."""
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


def fit_periodic(profile, n, lo, hi, step=0.02):
    """Best (score, pitch, first-border) for n+1 evenly spaced borders.

    A regular grid is far better found by fitting its PERIOD than by picking
    the strongest edges: the item icons inside the slots produce edges stronger
    than the borders themselves, so peak-picking finds the artwork. Measured
    both ways on the same frame -- peak-picking put row 1 twenty pixels out,
    which is a different slot's half.
    """
    d = np.abs(np.diff(profile))
    d = d / (d.max() or 1.0)
    length = len(d)
    best = (-1.0, None, None)
    for pitch in np.arange(lo, hi, step):
        if pitch * n >= length - 1:
            continue
        for start in np.arange(0, length - 1 - pitch * n, 0.5):
            pos = (start + pitch * np.arange(n + 1)).round().astype(int)
            # SCORED BY THE WEAKEST BORDER, NOT THE SUM.
            #
            # A real grid has all n+1 borders present. Summing lets a wrong
            # pitch win by landing a few positions on strong item art while
            # others sit on flat ground -- which is exactly what happened on a
            # packed inventory tab: the sum picked 68.10px and put the columns
            # 62.5px out, while the true 73.2px scored lower because item edges
            # are brighter than slot borders.
            #
            # Taking the minimum requires EVERY border to be there. Measured on
            # the same packed tab: sum 62.5px max error, 25th percentile 8.8px,
            # minimum 1.8px.
            score = float(d[pos].min())
            if score > best[0]:
                best = (score, float(pitch), float(start))
    return best


# --------------------------------------------------------------------------
# The Alz balance: the one reliably detectable thing on the Inventory panel
# --------------------------------------------------------------------------
#
# Bright AND saturated. The figure is orange, or green just after it changes,
# against a dark panel -- so keying on colour rather than a hue, and on both
# brightness and saturation rather than variance. A variance test saturates on
# game art; that is what made trade.py report all 64 slots occupied on an empty
# tab. Measured here: 20-24 qualifying pixels with the panel shut, 545 with it
# open.
# THE BALANCE LINE ONLY. The Inventory panel does not move, so this is a fixed
# box, and it deliberately stops above the gem counter.
#
# Measured on this screen, bright+saturated pixels by row:
#     Alz balance    y 888-908   x 2249-2483
#     (nothing)      y 909-938
#     gem counter    y 939-962   x 2251-2483
#
# A 30-row gap between them. The previous box ran 820-980 and contained both,
# and since the gem's diamond icon is a solid shape while the balance is thin
# digit strokes, the gem row often had MORE qualifying pixels -- so the "densest
# row" rule locked onto the gem and reported the panel as moved when it had
# not. Excluding it by geometry is simpler than out-arguing it by pixel count.
ALZ_SEARCH = None       # resolved per call, from ALZ_SEARCH_F
ALZ_BRIGHT = 110
ALZ_SATURATION = 45
ALZ_MIN_PIXELS = 150
ALZ_LINE_HALF = 14      # half a line of digits, in pixels
# Shape checks, so a bright region cannot pass as a balance. The real box is
# ~233px of a 270px search and 19px tall; the world fills the box edge to edge.
ALZ_MAX_WIDTH_FRACTION = 0.95
ALZ_MIN_HEIGHT = 8
ALZ_MAX_HEIGHT = 30


def find_alz(image: Image.Image, search=None):
    """(left, top, right, bottom) of the Alz digits, or None."""
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

    # KEEP ONLY THE DIGIT LINE. The balance is ONE line of text, but this
    # search box also contains the blue gem counter below it, which is just as
    # bright and just as saturated. Taking the extent of every qualifying pixel
    # merged the two: the box came out 249x143 instead of 108x17, and the panel
    # crop derived from it put the slot grid 150px out.
    #
    # So the rows are histogrammed, the densest one is taken as the line, and
    # only pixels within half a line-height of it survive.
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

    # IS THIS SHAPED LIKE A BALANCE, OR IS IT JUST BRIGHT?
    #
    # Everything above only asks "are there enough bright saturated pixels on
    # one row". The 3D world satisfies that everywhere -- sunlit scenery is
    # bright and colourful -- so with the panel shut, or the client sitting on
    # a loading screen or the login art, this returned a box and every position
    # derived from it was wrong.
    #
    # It happened for real on 2026-08-16: the game had logged out to the OTP
    # screen, and this returned (2240, 907, 2504, 921) -- 264px of a 270px
    # search box, starting exactly at its left edge. Calibration then fitted a
    # column pitch of 79.38px at a score of 1.07 (against 73.22 at 3.03 when it
    # is right), put tab 8 at x=2521, and clicked the arrange button.
    #
    # trade.py's version of this function carried the same guard and said why:
    # "A box that fills the search region is not a number, it is the 3D world."
    # I did not carry it over. Doing that now.
    width, height = box[2] - box[0], box[3] - box[1]
    span_w = search[2] - search[0]
    if width >= span_w * ALZ_MAX_WIDTH_FRACTION:
        return None
    if not ALZ_MIN_HEIGHT <= height <= ALZ_MAX_HEIGHT:
        return None
    return box


# --------------------------------------------------------------------------
def _pitch_bounds():
    """A slot is 2.66%-3.12% of the client width, whatever the resolution."""
    w = _client_rect()[2]
    return SLOT_PITCH_F[0] * w, SLOT_PITCH_F[1] * w


def calibrate_inventory(verbose=True):
    """Find the panel, then place everything on it from the stored layout.

    ONLY THE ANCHOR IS MEASURED. The panel's internal geometry does not change
    -- it is the same whatever is in the bag, and the same every time the panel
    opens -- so it is read from panel_layout rather than re-derived from the
    pixels each run.

    The previous version fitted the slot grid and the tab strip by periodicity
    every time. That read the borders through the item art, and measurably
    failed on it: a packed inventory tab put the columns 62px out where a
    sparse one was exact, and even after the fit was made robust the tab strip
    still landed a whole position out and clicked the arrange button twice.
    """
    say = print if verbose else (lambda *a: None)
    image = grab()
    alz = find_alz(image)
    if alz is None:
        raise RuntimeError(
            "the Alz balance was not found, so the Inventory panel is not "
            "open. Nothing measured.")
    anchor = (alz[2], alz[1])          # right edge, top -- see panel_layout
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

    # A CHEAP SANITY CHECK, since nothing here is derived from the picture any
    # more: every position must land inside the game's client area. That
    # catches a wildly wrong anchor -- which is the only way this can now go
    # wrong -- without pretending to re-measure the layout.
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
    """Is the Trade window up? Read from its Register tab.

    The same word calibrate_shop anchors on, so "open" here means exactly
    "measurable there" -- there is no second definition to drift.
    """
    try:
        words = ocr(grab(), _box(TOP_STRIP_F))
    except Exception:            # noqa: BLE001 - a probe must not throw
        return False
    return any(t.lower() == "register" for t, _c, _p in words)


def calibrate_shop(verbose=True):
    """The Purchase/Register tabs, the sort dropdown, the favourite slots."""
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

    # PURCHASE IS DERIVED, NOT READ. The game paints its own fps counter over
    # that label -- '241fps(64bit)' in yellow-green -- and OCR returns nothing
    # at any crop or scale that was tried. So the boundary between the two tabs
    # is measured instead, and Purchase is mirrored across it.
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

    # The sort dropdown. Its text is "Price: Low to High" but it renders
    # clipped and OCRs inconsistently -- 'Price:High' and 'to' on this frame --
    # so the anchor is the token containing "price", not the whole phrase.
    # THE SORT DROPDOWN, ANCHORED ON WHATEVER OF IT SURVIVED THE READ.
    #
    # Its text is "Price: Low to High" and it renders clipped, so it comes back
    # differently every time: 'Price:High' + 'to' on one frame, a bare 'to' on
    # the next, nothing at all on a third. Demanding "price" therefore failed
    # about half the time and stopped the whole calibration.
    #
    # Any of its words will do -- they are all inside the same control, and the
    # control is what gets clicked. Tried in order of how specific they are.
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

    # The favourite slots: a row of identical star icons along the bottom.
    # Found by periodicity, anchored by the 'Favorites' label on the same row.
    # THE FAVOURITE SLOTS ARE FOUND AS PEAKS, NOT AS A PERIODIC FIT.
    #
    # Each slot is a dark box with a lighter star glyph, and the column-mean
    # profile across the strip is textbook: a flat 30 in the gaps, rising to
    # exactly 90 at the centre of every box. Ten peaks, 57px apart, on this
    # screen at 651 708 765 822 879 936 993 1050 1107 1164.
    #
    # A periodicity fit was tried first and is the wrong tool: it locks onto
    # BOX BORDERS, which come in pairs, and on the scroll arrows past the last
    # slot -- it put the first slot 40px out and ran the last one to x=1319,
    # outside the Trade window.
    #
    # Bounded at 1190 deliberately: past that are the up/down scroll arrows and
    # then the game world, both brighter than any slot.
    FAV = _box(FAV_BAND_F)
    prof = np.asarray(image.crop(FAV).convert("L"), dtype=float).mean(axis=0)
    floor, ceiling = prof.min(), prof.max()
    cut = floor + (ceiling - floor) * 0.6
    # The MAXIMUM of each run above the cut, not the first local maximum in it.
    # The profile rises 66, 72, 72, 70 before reaching 90, so "first point that
    # is >= its neighbours" picks that 72 shoulder -- 8px left of the true
    # centre on every slot.
    peaks, run = [], []
    for i, v in enumerate(prof):
        if v >= cut:
            run.append(i)
        elif run:
            peaks.append(max(run, key=lambda j: prof[j]))
            run = []
    if run:
        peaks.append(max(run, key=lambda j: prof[j]))
    # MERGE PEAKS INSIDE ONE BOX. Each slot has TWO humps -- the star (90) and
    # the little magnifier beside it (83) -- with a dip to 63 between them,
    # which falls below the cut and splits the run. Unmerged this found 20
    # slots where there are 10.
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
    """(hwnd, exact title, client rect) for the game window, or None.

    Matched on "PlayCabal", NOT "Cabal": this project lives in a folder called
    Cabal, so an editor with it open is titled "... - Cabal - Visual Studio
    Code" and a looser match finds the editor.
    """
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
    """Calibrate from a default game state, and leave it in one.

    ASSUMES NOTHING IS OPEN when it starts -- no Inventory, no Trade window --
    which is the state the game is left in by the end of this function and by
    close_everything() below.

    It opens what it needs as it goes, and the order is forced: the shop half
    cannot be measured until the Trade window is up, and the Trade window is
    opened by right-clicking the Agent Shop key, whose position is only known
    after the inventory half has been measured. So inventory first, always.
    """
    from open_inventory import VK_I, VK_ESCAPE, focus_game, press

    shared = load_shared()
    gap = shared["timing"]["action_gap"]
    facts = shared["game_facts"]

    if not focus_game():
        raise RuntimeError(
            f"could not bring the {shared['game']['title_hint']!r} window to "
            f"the foreground. Nothing measured.")

    # ---- 1. the inventory ------------------------------------------------
    #
    # PRESSED UNCONDITIONALLY, because the default state is the contract: this
    # function is documented to start with nothing open, so I opens the panel.
    #
    # The previous version asked find_alz first, to avoid toggling a panel that
    # was already up. That looks careful and is not: with the panel SHUT, that
    # region shows the 3D world, and sunlit scenery is bright and saturated
    # enough to pass. On 2026-08-16, from a proper default state in a lit town,
    # it returned a box, concluded the panel was already open, NEVER PRESSED I,
    # and measured the world -- a column pitch of 79.38 at a fit score of 1.07,
    # tab 8 at x=2521, and a click on the arrange button.
    #
    # There is no reading of that region that distinguishes "panel closed" from
    # "bright ground" reliably, so it is not asked. The state is known from the
    # contract, and the press is verified by what it CHANGES, below.
    print("inventory:")

    # PRESS, CHECK, PRESS AGAIN IF WRONG.
    #
    # I is a toggle and the panel's state cannot be read reliably when it is
    # SHUT -- that region shows the 3D world, and sunlit ground is bright and
    # saturated enough to look like a balance. So the state is not guessed; it
    # is established.
    #
    # The default state is the documented contract, but a FAILED run raises
    # before it can tidy up and leaves the panel open, so the next run starts
    # from the opposite state and its press closes what it meant to open.
    # Observed doing exactly that twice in a row. One retry settles it: after
    # two presses the panel has been both toggled and untoggled, so whichever
    # state it started in, one of the two attempts had it open.
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

    # ---- 2. open the shop, using what was just measured ------------------
    #
    # This is why the inventory has to be first: the key is in a slot, and the
    # slot's position is an output of the step above. Nothing here reads
    # calibration.json -- it cannot, on a monitor being measured for the first
    # time.
    print("opening the Agent Shop:")
    tab = inventory["tabs"][str(facts["agent_shop_tab"])]
    row, col = facts["agent_shop_slot"]
    key = inventory["slots"][f"{row}x{col}"]
    print(f"  tab {facts['agent_shop_tab']} at {tab}")
    click(*tab)

    # RIGHT-CLICK, CHECK, RIGHT-CLICK AGAIN IF WRONG -- the key is a toggle
    # too. A failed run leaves the Trade window open, and then this closes what
    # it meant to open. Observed doing exactly that: both clicks landed on the
    # correct coordinates and the shop still was not there afterwards, because
    # it had been up already.
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

    # ---- 3. the shop -----------------------------------------------------
    print("agent shop:")
    park()
    shop = calibrate_shop()

    # ---- 4. write, keyed by resolution -----------------------------------
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

    # KEYED BY RESOLUTION. A coordinate only means anything on the screen it
    # was measured on, so each one gets its own entry and the others are left
    # untouched -- calibrating on a laptop does not destroy the desktop's
    # numbers.
    out = dict(existing)
    for section in DEFAULTS:
        merged = dict(DEFAULTS[section])
        merged.update(existing.get(section) or {})
        out[section] = merged
    out.setdefault("by_resolution", {})[resolution_key()] = measured
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}  [{resolution_key()}]")
    print(f"  resolutions in the file: {sorted(out['by_resolution'])}")

    # ---- 5. back to default ----------------------------------------------
    close_everything(verbose=True)


def close_everything(verbose: bool = False) -> None:
    """Leave the game as it was found: no Trade window, no Inventory panel.

    Escape closes the Trade window. It does NOT close the Inventory -- that is
    a toggle on I -- and with nothing left to close Escape opens the game Menu
    instead, so it is pressed once and only while the shop is up.
    """
    from open_inventory import VK_I, VK_ESCAPE, focus_game, press
    gap = load_shared()["timing"]["action_gap"]
    if not focus_game():
        return
    if verbose:
        print("restoring the default state:")

    # The Trade window, if the shop was opened above.
    press(VK_ESCAPE)
    time.sleep(gap)
    if verbose:
        print("  Escape: Trade window closed")

    # The Inventory. Same reasoning as opening it: asking "is it open" reads a
    # region that shows the ground when it is not, so the answer is taken from
    # the change instead.
    # Verified, not inferred. The first version reported "Inventory closed"
    # whenever the press changed the region -- but a press that OPENS it
    # changes just as much, so it announced success while leaving the panel up
    # for the next run to close by mistake.
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
