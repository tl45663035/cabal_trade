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
            score = float(d[pos].sum())
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
    return (search[0] + min(kx), search[1] + min(ky),
            search[0] + max(kx), search[1] + max(ky))


# --------------------------------------------------------------------------
def _pitch_bounds():
    """A slot is 2.66%-3.12% of the client width, whatever the resolution."""
    w = _client_rect()[2]
    return SLOT_PITCH_F[0] * w, SLOT_PITCH_F[1] * w


def calibrate_inventory(verbose=True):
    """Alz box, the 8 tabs, and all 64 slot centres."""
    say = print if verbose else (lambda *a: None)
    image = grab()
    alz = find_alz(image)
    if alz is None:
        raise RuntimeError(
            "the Alz balance was not found, so the Inventory panel is not "
            "open. Nothing measured.")
    say(f"  Alz box {alz}")

    # ANCHORED ON THE BOX'S RIGHT EDGE, NOT ITS LEFT. The digits end cleanly
    # before the "Alz" label, but to their LEFT sits a gold coin icon that is
    # just as bright and just as saturated, so the left edge lands on the coin
    # or on the digits depending on the balance. Measured: right edge 2482 on
    # two runs, left edge 2249 then 2374.
    left, top = alz[2] - 600, alz[1] - 760
    right, bottom = alz[2] + 90, alz[3] + 40
    panel = np.asarray(image.crop((left, top, right, bottom)).convert("L"),
                       dtype=float)
    say(f"  panel crop ({left}, {top}) {panel.shape[1]}x{panel.shape[0]}")

    # The tab strip, then the grid. The strip sits above the grid and its
    # borders are the only strong regular edges up there.
    # The tab strip is located AFTER the grid, from the grid, because it sits
    # directly above the first slot row. Sized as a fraction of the panel it
    # came out 96px tall instead of 47, swallowed the panel title and the top
    # of row 1, and fitted a pitch of 66.58px against a true 69.6 -- which put
    # tab VIII 26px off centre.
    t_score = t_pitch = t_x0 = tab_y = None
    # Columns over the grid band only, then rows sampled ONLY in the gaps
    # between columns -- item art dominates everywhere else.
    # THE GRID STARTS BELOW THE TAB STRIP, not at a fixed fraction of the
    # panel. A fraction put grid_top inside row 1, and the row fit then locked
    # onto row 2 -- every slot came out exactly one pitch (74px) low, which is
    # a different slot, not a rounding error.
    # Start ABOVE where the grid can begin, and let the fit find the phase.
    # strip_bottom sat BELOW the first border (136 against 127 on this screen),
    # so the fit could not see it and locked onto the second -- every slot came
    # out exactly one pitch (74px) low, which is a different slot.
    #
    # Starting at the tab strip's midline is safe: the tabs have their own
    # pitch (66.6px) and the search below is bounded to 68-80, so tab borders
    # cannot satisfy it.
    grid_top = int(len(panel) * 0.08)
    c_score, c_pitch, c_x0 = fit_periodic(
        panel[grid_top:, :].mean(axis=0), GRID, *_pitch_bounds())
    borders = [c_x0 + k * c_pitch for k in range(GRID + 1)]
    gaps = [c for b in borders
            for c in range(int(b) - 2, int(b) + 3)
            if 0 <= c < panel.shape[1]]
    r_score, r_pitch, r_y0 = fit_periodic(
        panel[:, gaps].mean(axis=1)[grid_top:], GRID, *_pitch_bounds())
    say(f"  columns pitch {c_pitch:.2f}px  score {c_score:.2f}")
    say(f"  rows    pitch {r_pitch:.2f}px  score {r_score:.2f}")

    first_border = grid_top + r_y0          # crop coords of the grid's top line
    strip_top = int(max(0, first_border - 62))
    strip_bottom = int(max(strip_top + 10, first_border - 8))
    strip = panel[strip_top:strip_bottom, :]
    t_score, t_pitch, t_x0 = fit_periodic(strip.mean(axis=0), GRID, 60, 80)
    tab_y = top + strip_top + strip.shape[0] // 2
    tabs = [(round(left + t_x0 + t_pitch / 2 + k * t_pitch), round(tab_y))
            for k in range(GRID)]
    say(f"  tabs   pitch {t_pitch:.2f}px  score {t_score:.2f}  y={tab_y} "
        f"(band {strip_top}-{strip_bottom} above the grid)")

    slots = {}
    for row in range(1, GRID + 1):
        for col in range(1, GRID + 1):
            slots[f"{row}x{col}"] = [
                round(left + c_x0 + c_pitch / 2 + c_pitch * (col - 1)),
                round(top + grid_top + r_y0 + r_pitch / 2 + r_pitch * (row - 1)),
            ]
    say(f"  slot (1,7) {slots['1x7']}   slot (1,8) {slots['1x8']}")

    return {
        "alz_box": list(alz),
        "tabs": {str(i + 1): list(p) for i, p in enumerate(tabs)},
        "tab_pitch": round(t_pitch, 2),
        "slots": slots,
        "slot_pitch": [round(c_pitch, 2), round(r_pitch, 2)],
        "evidence": {"tab_fit": round(t_score, 2),
                     "column_fit": round(c_score, 2),
                     "row_fit": round(r_score, 2)},
    }


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
    sort = next((p for t, c, p in words if "price" in t.lower()), None)
    if sort is None:
        raise RuntimeError("the sort dropdown was not found.")
    say(f"  sort dropdown {sort}")

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
    from open_inventory import VK_I, focus_game, press
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")

    print("inventory:")
    park()
    if find_alz(grab()) is None:
        press(VK_I)
        time.sleep(ACTION_GAP)
        park()
    inventory = calibrate_inventory()

    print("agent shop:")
    park()
    shop = calibrate_shop()

    win = find_game_window()
    measured = {
        "screen": list(screen_size()),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "game": {
            "title_hint": "PlayCabal",
            "title_seen": win[1] if win else None,
            "client_rect": win[2] if win else None,
        },
        # The colour test find_alz uses, written down so the scripts that read
        # this file test the panel the same way it was measured.
        "alz_detect": {
            "search": list(ALZ_SEARCH),
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
    print(f"  resolutions in the file: "
          f"{sorted(out['by_resolution'])}")


if __name__ == "__main__":
    main()
