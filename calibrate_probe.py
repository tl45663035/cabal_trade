"""Report why calibration fails on this machine. Read-only: never clicks.

Run it on the machine that cannot calibrate, with Cabal open and the Agent
Shop showing the Register tab:

    py calibrate_probe.py

It prints every input calibration uses, the fit it computes, the point at
which it gives up, and what every derived coordinate would become. Alongside
it writes, into calibrate_probe_out/:

    screen.png          the full screen it measured
    trade_region.png    the area the Trade window is believed to occupy
    anchor_*.png        a crop around each anchor's EXPECTED position, so a
                        missing anchor can be seen rather than guessed at
    words.txt           every word OCR found on screen, with confidence
    report.txt          everything printed below

Send the folder back. The images are what make a remote diagnosis possible:
"the anchor was not found" has half a dozen causes and they look completely
different.

Nothing here moves the mouse, presses a key, or changes the game. The only
actions are screen captures and OCR.
"""
import io
import sys
import time
import traceback
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import trade  # noqa: E402

OUT = HERE / "calibrate_probe_out"
OUT.mkdir(exist_ok=True)


def rule(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def show(label, value, note=""):
    print(f"  {label:26} {value}" + (f"   {note}" if note else ""))


def env():
    """Everything about the DISPLAY, before trade.py's own view of it.

    This section exists because the machine that cannot calibrate differs from
    the one that can in three ways at once -- 1920x1080 instead of 2560x1440,
    ONE monitor instead of two, and very likely Windows display scaling -- and
    each of those breaks calibration differently. Guessing which is which from
    "calibration failed" is not possible; this prints all three.

    Read only. No clicks, no keys, no window changes.
    """
    rule("0. the display, measured three ways")

    # --- what Windows thinks, BEFORE we declare DPI awareness --------------
    import ctypes
    u32 = ctypes.windll.user32
    naive = (u32.GetSystemMetrics(0), u32.GetSystemMetrics(1))
    show("GetSystemMetrics (naive)", naive,
         "logical pixels; shrinks under display scaling")

    # --- and after ---------------------------------------------------------
    try:
        trade.make_dpi_aware()
        aware = (u32.GetSystemMetrics(0), u32.GetSystemMetrics(1))
        show("GetSystemMetrics (aware)", aware,
             "MATCHES naive" if aware == naive else "<-- DPI scaling IS on")
    except Exception as exc:            # noqa: BLE001 - diagnostic
        show("make_dpi_aware()", f"FAILED: {exc}")
        aware = naive

    # --- the scaling factor itself ----------------------------------------
    #
    # A 1080p laptop at 150% reports 1280x720 logical against 1920x1080
    # physical. mss captures PHYSICAL pixels; SetCursorPos takes LOGICAL ones.
    # Mixing them puts every click at two thirds of where it belongs, which
    # looks exactly like a bad calibration and is not one.
    try:
        hdc = u32.GetDC(0)
        LOGPIXELSX = 88
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
        u32.ReleaseDC(0, hdc)
        show("system DPI", f"{dpi}  ({dpi / 96 * 100:.0f}% scaling)",
             "100% is the only value this script was built against"
             if dpi != 96 else "")
    except Exception as exc:            # noqa: BLE001 - diagnostic
        show("system DPI", f"could not read: {exc}")

    # --- every monitor mss can see ----------------------------------------
    #
    # monitors[0] is the UNION of all displays; real ones start at index 1.
    # On a dual-monitor machine the union is wider than any single display and
    # a second monitor sits at a non-zero origin -- so a capture of it yields
    # image coordinates starting at (0,0) that do NOT match cursor
    # coordinates. On a single monitor at (0,0) the two spaces coincide, which
    # is why code can be silently wrong on one machine and right on the other.
    try:
        import mss
        with mss.mss() as sct:
            mons = sct.monitors
        show("monitors seen by mss", len(mons) - 1)
        for i, m in enumerate(mons):
            tag = ("UNION of all" if i == 0
                   else "PRIMARY" if (m["left"], m["top"]) == (0, 0)
                   else f"secondary, origin ({m['left']},{m['top']})")
            show(f"  monitors[{i}]",
                 f"{m['width']}x{m['height']} at ({m['left']},{m['top']})", tag)
        real = mons[1:]
        if len(real) == 1 and (real[0]["left"], real[0]["top"]) == (0, 0):
            show("verdict", "SINGLE monitor at origin",
                 "capture and cursor coordinates coincide")
        else:
            show("verdict", f"{len(real)} monitor(s)",
                 "<-- capture origin may differ from cursor origin")
    except Exception as exc:            # noqa: BLE001 - diagnostic
        show("mss", f"FAILED: {exc}  <-- fatal, nothing can be captured")

    # --- physical vs logical, the actual trap ------------------------------
    #
    # COMPARE THE PRIMARY, NOT THE UNION. monitors[0] is every display glued
    # together -- on a dual-monitor machine it is legitimately larger than the
    # screen user32 reports, and comparing the two flags a DPI problem that
    # does not exist. The real test is the PRIMARY display's physical pixels
    # against the logical size clicks are addressed in.
    try:
        import mss
        with mss.mss() as sct:
            mons = sct.monitors
        prim = next((m for m in mons[1:]
                     if (m["left"], m["top"]) == (0, 0)),
                    mons[1] if len(mons) > 1 else None)
        if prim:
            phys = (prim["width"], prim["height"])
            show("primary, physical (mss)", phys)
            show("primary, logical (user32)", aware)
            if phys != aware:
                show("MISMATCH", f"{phys} captured vs {aware} clicked",
                     "<-- DISPLAY SCALING. mss captures physical pixels, "
                     "SetCursorPos takes logical ones, so every click lands "
                     "short. Set Windows scaling to 100% and re-run.")
            else:
                show("physical == logical", "yes",
                     "capture and click coordinates agree")
    except Exception:                   # noqa: BLE001 - already reported
        pass

    # --- the game window ---------------------------------------------------
    #
    # The reference layout was measured with the Trade window at a FIXED pixel
    # size. If that holds, a different resolution only moves it, and
    # calibration is a translation. If the window is a different SIZE here,
    # the whole reference layout has to be rescaled and every raw pixel
    # constant in trade.py is wrong.
    try:
        rect = trade.client_rect()
        show("game client rect", rect)
        if rect:
            w, h = rect[2] - rect[0], rect[3] - rect[1]
            show("game client size", f"{w}x{h}")
        show("REF_SCREEN", trade.REF_SCREEN)
        show("REF_TRADE_ORIGIN", trade.REF_TRADE_ORIGIN)
        show("REF_TRADE_SIZE", trade.REF_TRADE_SIZE,
             "if the Trade window is this size here too, "
             "calibration is pure translation")
    except Exception as exc:            # noqa: BLE001 - diagnostic
        show("client_rect()", f"FAILED: {exc}")


def window_hunt():
    """Every visible top-level window, so a title mismatch cannot hide.

    GAME_TITLE_HINT is a single substring, and it is the single point of
    failure for the whole bootstrap: if it does not match, find_game_window
    returns None, client_rect returns None, the OCR upscale seed falls back to
    the built-in 1.0, and the upscale drops to the value that splits 'Refresh'
    into 'R' + 'efresh'. calibrate() then prints "game client area: not found"
    and carries on, so every downstream symptom looks like an anchor problem.

    Printing the real list turns that into a five-second diagnosis.
    """
    rule("0b. every window on this machine")
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    show("GAME_TITLE_HINT", repr(trade.GAME_TITLE_HINT),
         "find_game_window matches this, case-insensitively")
    rows = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        r = wintypes.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(r))
        rows.append((hwnd, buf.value, (r.left, r.top, r.right, r.bottom)))
        return True

    try:
        u32.EnumWindows(each, 0)
    except Exception as exc:              # noqa: BLE001 - diagnostic
        show("EnumWindows", f"FAILED: {exc}")
        return
    hint = (trade.GAME_TITLE_HINT or "").casefold()
    hits = [r for r in rows if hint and hint in r[1].casefold()]
    show("visible windows", len(rows))
    show("matching the hint", len(hits),
         "<-- ZERO means the bootstrap silently reverts" if not hits else "")
    for hwnd, title, rect in rows:
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w < 200 or h < 200:
            continue
        mark = "  <== MATCHES" if hint and hint in title.casefold() else ""
        print(f"    {hwnd:>10}  {w:>5}x{h:<5} at ({rect[0]},{rect[1]})"
              f"  {title[:44]!r}{mark}")
    if not hits:
        print("    None of these contain the hint. Either the client is not"
              " running, or its\n    title differs on this machine -- in which"
              " case GAME_TITLE_HINT needs\n    widening before anything else"
              " is worth trying.")


def row_count():
    """How many rows the Agent Shop table shows.

    The one thing calibration cannot fix. EXPECTED_ROWS is a hard gate --
    `if len(buttons) != EXPECTED_ROWS: return []` -- so if this screen shows a
    different number, read_rows returns nothing on every frame for ever,
    await_rows burns its whole budget each call, and three cycles later the
    breaker stops the run with a message about the Trade window being closed.
    """
    rule("4b. how many rows the table actually shows")
    show("EXPECTED_ROWS", trade.EXPECTED_ROWS, "what the code demands")
    try:
        shot = trade.grab()
        buttons = trade.find_row_buttons(shot)
        show("buttons found", len(buttons),
             "MATCH" if len(buttons) == trade.EXPECTED_ROWS
             else "<-- MISMATCH: read_rows will return [] on every frame")
        if buttons:
            tops = [b.top for b in buttons]
            gaps = [b - a for a, b in zip(tops, tops[1:])]
            if gaps:
                mid = sorted(gaps)[len(gaps) // 2]
                show("measured row pitch", f"{mid}px",
                     f"reference {trade.REF_ROW_PITCH}"
                     f"  -> implied scale {mid / trade.REF_ROW_PITCH:.4f}")
        else:
            print("    No Change/Receive/Register buttons read at all. Either"
                  " the Register tab\n    is not showing, or the OCR upscale"
                  " is wrong for this resolution.")
    except Exception as exc:              # noqa: BLE001 - diagnostic
        show("find_row_buttons", f"FAILED: {exc}")


def scale_agreement():
    """The client-rect ratio against the anchor-fitted scale.

    Two independent measurements of the same thing: one from Win32 geometry
    with no OCR at all, one from a least-squares fit of a dozen OCR'd words.
    On the machine that could not calibrate they agreed to 0.04% -- which is
    what proved the UI scales with the client rather than staying a fixed
    pixel size, and therefore that the whole transform model is sound.

    A large disagreement here means something this port has not accounted for
    -- most likely the game's own UI-scale setting differing between machines.
    """
    rule("5b. do the two independent scale measurements agree?")
    seed = None
    try:
        seed = trade._ocr_reference_scale()
        show("client-rect ratio", f"{seed:.4f}", "no OCR involved")
    except Exception as exc:              # noqa: BLE001 - diagnostic
        show("client-rect ratio", f"FAILED: {exc}")
    try:
        layout = trade.measure_layout(verbose=False)
    except Exception as exc:              # noqa: BLE001 - diagnostic
        show("anchor fit", f"FAILED: {exc}")
        return
    if layout is None:
        show("anchor fit", "REFUSED", "see section 5 for the reason")
        return
    show("anchor-fitted scale", f"{layout.scale:.4f}")
    show("fitted origin", layout.origin)
    if seed:
        drift = abs(layout.scale - seed) / seed * 100
        show("disagreement", f"{drift:.2f}%",
             "the two agree; the model holds" if drift < 5
             else "<-- OVER 5%: the game's UI scale may differ here")


_BEFORE: dict = {}


def tesseract_selftest():
    """Prove the OCR engine works before blaming the geometry.

    Every section from 3 onward is OCR. If the binary is missing, the version
    differs, or eng.traineddata is absent, all of them fail at once and the
    symptoms read as anchor and scale problems -- which is where the last two
    days of this port were spent looking. Ten lines here name the cause.
    """
    rule("0c. is Tesseract itself working?")
    try:
        import pytesseract
    except Exception as exc:                      # noqa: BLE001 - diagnostic
        show("pytesseract", f"NOT IMPORTABLE: {exc}")
        return
    show("pytesseract", getattr(pytesseract, "__version__", "?"))
    show("binary", pytesseract.pytesseract.tesseract_cmd)
    try:
        show("tesseract version", str(pytesseract.get_tesseract_version()))
    except Exception as exc:                      # noqa: BLE001 - diagnostic
        show("tesseract version", f"FAILED: {exc}",
             "<-- nothing below this line can work")
        return
    try:
        langs = pytesseract.get_languages(config="")
        show("languages", ",".join(sorted(langs)[:8]) or "NONE",
             "eng present" if "eng" in langs
             else "<-- 'eng' MISSING: every read returns empty")
    except Exception as exc:                      # noqa: BLE001 - diagnostic
        show("languages", f"could not list: {exc}")
    # A synthetic round-trip, so a failure here is unambiguously the engine.
    try:
        from PIL import Image as _I, ImageDraw as _D
        card = _I.new("RGB", (320, 60), (12, 12, 12))
        _D.Draw(card).text((10, 18), "Register Item 250", fill=(235, 235, 235))
        got = pytesseract.image_to_string(card).strip()
        show("synthetic read", repr(got),
             "engine is healthy" if "Register" in got
             else "<-- ENGINE PROBLEM: it cannot read its own test card")
    except Exception as exc:                      # noqa: BLE001 - diagnostic
        show("synthetic read", f"FAILED: {exc}")


def anchor_stability(frames: int = 6, gap: float = 1.2):
    """Read the anchors over several frames instead of one.

    A single frame cannot tell a permanent failure from a passing one. The
    game animates: tooltips fade, the target nameplate comes and goes, a sale
    notification slides across the bottom-right, and the mouse cursor sits on
    top of whatever it is over. Any of those can cover an anchor for the one
    frame the probe happened to grab, and the report then says MISSING about a
    word that is on screen 95% of the time.

    That distinction changes the fix. An anchor missing on every frame is
    wrong text, wrong upscale, or a word this build does not have. An anchor
    missing on one frame in six is noise, and calibrate()'s own retry already
    handles it -- chasing it wastes the time the real fault needed.
    """
    rule(f"4c. are the anchors STABLE? ({frames} frames, ~{gap}s apart)")
    print("  A single frame cannot separate a permanent failure from a passing")
    print("  one. Anything below 100% here was on screen for some frames and")
    print("  not others, and only 0% is worth chasing.\n")
    seen = {phrase: 0 for phrase, _ in trade.REF_ANCHORS_ALL}
    where: dict = {}
    scales = []
    for n in range(frames):
        if n:
            time.sleep(gap)
        try:
            shot = trade.grab()
            words = trade.find_words(shot, (0, 0, *shot.size), 40.0)
            lines = trade._text_lines(words)
        except Exception as exc:                  # noqa: BLE001 - diagnostic
            print(f"    frame {n + 1}: FAILED {exc}")
            continue
        for phrase, _ref in trade.REF_ANCHORS_ALL:
            centre = trade._anchor_centre(phrase, words, lines)
            if centre:
                seen[phrase] += 1
                where.setdefault(phrase, []).append(centre)
        try:
            fitted = trade.measure_layout(verbose=False, source=shot)
        except TypeError:
            fitted = trade.measure_layout(verbose=False)
        except Exception:                         # noqa: BLE001 - diagnostic
            fitted = None
        if fitted is not None:
            scales.append(fitted.scale)
        print(f"    frame {n + 1}: {sum(1 for p_ in seen if seen[p_] > n)}"
              f" anchors, scale "
              f"{f'{fitted.scale:.4f}' if fitted else 'REFUSED'}")
    print()
    always = never = 0
    for phrase, ref in trade.REF_ANCHORS_ALL:
        hits = seen[phrase]
        pts = where.get(phrase, [])
        jitter = ""
        if len(pts) > 1:
            xs = [c[0] for c in pts]
            ys = [c[1] for c in pts]
            jx, jy = max(xs) - min(xs), max(ys) - min(ys)
            if jx or jy:
                jitter = f"  moved {jx}x{jy}px between frames"
        if hits == frames:
            always += 1
            mark = "always"
        elif hits == 0:
            never += 1
            mark = "NEVER   <-- real: wrong text, wrong upscale, or absent"
        else:
            mark = f"{hits}/{frames}  <-- intermittent, something covers it"
        print(f"    {phrase:<12} {mark}{jitter}")
    print("")
    print(f"  {always} on every frame, {never} on none, "
          f"{len(seen) - always - never} intermittent")
    if scales:
        spread = max(scales) - min(scales)
        show("fitted scale range", f"{min(scales):.4f} .. {max(scales):.4f}",
             f"spread {spread:.5f}" + ("  stable" if spread < 0.005
                                       else "  <-- UNSTABLE, the fit is moving"))
    elif frames:
        show("fitted scale", "REFUSED on every frame",
             "<-- calibration cannot run in this state")


def probe():
    env()
    tesseract_selftest()
    window_hunt()
    rule("1. the machine")
    trade.make_dpi_aware()
    screen = trade.current_screen_size()
    client = trade.client_rect()
    show("python", sys.version.split()[0])
    show("tesseract", trade.find_tesseract() or "NOT FOUND  <-- fatal")
    show("screen size", screen)
    show("reference screen", trade.REF_SCREEN,
         "MATCH" if screen and tuple(screen) == trade.REF_SCREEN else "differs")
    show("game window handle", trade.find_game_window())
    show("client rect", client)
    show("reference client", trade.REF_CLIENT,
         "MATCH" if client and tuple(client) == trade.REF_CLIENT else "differs")
    if client:
        w, h = client[2] - client[0], client[3] - client[1]
        rw = trade.REF_CLIENT[2] - trade.REF_CLIENT[0]
        rh = trade.REF_CLIENT[3] - trade.REF_CLIENT[1]
        show("client size", f"{w} x {h}", f"reference {rw} x {rh}")
        show("width ratio", f"{w / rw:.4f}")
        show("height ratio", f"{h / rh:.4f}")
        show("min of the two", f"{min(w / rw, h / rh):.4f}",
             "<- what the OCR upscale is chosen from")
    else:
        print("  !! the Cabal window was not found. Nothing below can work:")
        print("     find_game_window() looks for the window by class/title.")

    rule("2. the OCR upscale seed (no OCR involved)")
    print("  This is the bootstrap: the upscale has to be right BEFORE any")
    print("  anchor can be read, so it comes from the window's own rectangle.")
    try:
        seed = trade._ocr_reference_scale()
        show("_ocr_reference_scale()", f"{seed:.4f}")
        show("LAYOUT.scale", trade.LAYOUT.scale, "built-in until calibrated")
        show("LAYOUT.origin", trade.LAYOUT.origin)
        show("_CALIBRATED", trade._CALIBRATED)
        show("upscale chosen", getattr(trade, '_ocr_upscale_for', None) or trade._ocr_reference_scale(seed)
             if hasattr(trade, "_ocr_upscale_for") else "(derived inside find_words)")
    except Exception:
        traceback.print_exc()

    rule("3. what is on screen (BEFORE calibration -- see 7b)")
    print("  Every check here crops a region that has NOT been calibrated yet,")
    print("  so a False is not evidence about this machine. On the 1080p port")
    print("  the uncalibrated REGISTER_PANEL starts at y=120 and the words")
    print("  'Register Item' sit at y=118 -- clipped by two pixels, reported as")
    print("  a closed tab. Section 7b repeats all of it after calibrating; that")
    print("  is the pair to trust.\n")
    shot = trade.grab()
    shot.save(OUT / "screen.png")
    show("captured", f"{shot.size[0]}x{shot.size[1]}", "-> screen.png")
    _BEFORE["trade_window_open"] = trade.trade_window_open(shot)
    _BEFORE["register_tab_open"] = trade.register_tab_open(shot)
    _BEFORE["dialog_kind"] = repr(trade.dialog_kind(shot))
    _BEFORE["dialog_present"] = trade.dialog_present(shot)
    _BEFORE["find_npc"] = trade.find_npc(shot, retries=1)
    for _name, _value in _BEFORE.items():
        show(_name, _value)
    try:
        shot.crop(trade.TRADE_REGION).save(OUT / "trade_region.png")
        show("TRADE_REGION crop", trade.TRADE_REGION, "-> trade_region.png")
    except Exception as exc:
        print(f"  could not crop TRADE_REGION: {exc}")
    if not trade.trade_window_open(shot):
        print("\n  !! The Trade window is not open. Calibration measures words")
        print("     INSIDE it, so open the Agent Shop on the Register tab and")
        print("     run this again. Everything below will fail without it.")

    rule("4. the anchors calibration needs")
    print("  Each anchor gives one (measured, reference) pair. Two of them, far")
    print("  enough apart, give an origin and a scale.\n")
    words = []
    try:
        words = trade.find_words(shot, (0, 0, shot.size[0], shot.size[1]), 40.0)
        lines = trade._text_lines(words)
        (OUT / "words.txt").write_text(
            "\n".join(f"{w.conf:6.1f}  {str(w.centre):>16}  {w.text!r}"
                      for w in sorted(words, key=lambda w: (w.centre[1], w.centre[0]))),
            encoding="utf-8")
        show("words on screen", len(words), "-> words.txt")
        found = missing = 0
        for phrase, ref in trade.REF_ANCHORS_ALL:
            centre = trade._anchor_centre(phrase, words, lines)
            if centre:
                found += 1
                dx, dy = centre[0] - ref[0], centre[1] - ref[1]
                print(f"    OK      {phrase:<12} at {str(centre):>14}  "
                      f"reference {str(ref):>14}  offset ({dx:+5d},{dy:+5d})")
            else:
                missing += 1
                print(f"    MISSING {phrase:<12} {'':>14}  "
                      f"reference {str(ref):>14}")
            # A crop around where it SHOULD be, at the reference position
            # scaled by the seed -- so a missing anchor can be looked at.
            try:
                seed = trade._ocr_reference_scale()
                cx, cy = int(ref[0] * seed), int(ref[1] * seed)
                box = (max(0, cx - 160), max(0, cy - 40),
                       min(shot.size[0], cx + 160), min(shot.size[1], cy + 40))
                shot.crop(box).resize(((box[2] - box[0]) * 2,
                                       (box[3] - box[1]) * 2)).save(
                    OUT / f"anchor_{phrase.replace(' ', '_')}.png")
            except Exception:
                pass
        print(f"\n  {found} found, {missing} missing "
              f"(a fit needs at least two, far apart)")
        print("  Crops of each expected position are in anchor_*.png -- look at")
        print("  them: 'missing' can mean the word is absent, split in two,")
        print("  covered by a dialog, or rendered where nobody looked.")
        if missing:
            print("\n  What OCR actually read NEAR each missing anchor. This is")
            print("  the evidence: a split word ('Refresh' -> 'R' + 'efresh' at")
            print("  the wrong upscale) looks identical to an absent one in the")
            print("  MISSING line above, and the two need opposite fixes.")
            try:
                seed = trade._ocr_reference_scale()
            except Exception:                     # noqa: BLE001 - diagnostic
                seed = 1.0
            for phrase, ref in trade.REF_ANCHORS_ALL:
                if trade._anchor_centre(phrase, words, lines) is not None:
                    continue
                cx, cy = int(ref[0] * seed), int(ref[1] * seed)
                near = sorted(
                    (w for w in words
                     if abs(w.centre[0] - cx) < 220 and abs(w.centre[1] - cy) < 60),
                    key=lambda w: abs(w.centre[0] - cx) + abs(w.centre[1] - cy))
                print(f"\n    {phrase!r} expected near ({cx}, {cy}):")
                if not near:
                    print("      NOTHING readable within 220x60px -- the word is"
                          " absent, covered,\n      or the seed put this box in"
                          " the wrong place entirely.")
                for w in near[:8]:
                    print(f"      {w.text!r:22} conf {w.conf:5.1f} at {w.centre}")
            print("\n  Highest-confidence words anywhere on screen:")
            for w in sorted(words, key=lambda w: -w.conf)[:20]:
                print(f"    {w.text!r:24} conf {w.conf:5.1f} at {w.centre}")
    except Exception:
        traceback.print_exc()

    row_count()
    anchor_stability()
    rule("5. the fit")
    layout = None
    try:
        layout = trade.measure_layout(shot, verbose=True)
        print(f"\n  measure_layout -> {layout}")
        if layout is not None:
            ok = trade.validate_layout(layout, verbose=True)
            print(f"  validate_layout -> {ok}")
    except Exception:
        traceback.print_exc()

    scale_agreement()
    rule("6. what every derived coordinate would become")
    print("  Reference value on the left, what this machine would use on the")
    print("  right. A wrong number here is a click somewhere in the game world.\n")
    print("  These are read back from the module AFTER applying the fitted")
    print("  layout -- not recomputed here. A probe that re-implements the")
    print("  transform can disagree with the code it is meant to be checking,")
    print("  and this one did: it had no case for the 'xs', 'ys' or 'ypair")
    print("  kinds, so CONVERT_COLS, CONVERT_ROWS and VENDOR_TAB_BAND printed")
    print("  \'not mapped by this probe\' while apply_layout was scaling them")
    print("  correctly all along. Three coordinate families, invisible to")
    print("  exactly the review that should have caught an error in them.\n")
    try:
        if not trade._REFERENCE_GEOMETRY:
            trade._capture_reference_geometry()

        tables = [("Trade frame", trade._TRADE_FRAME_GEOMETRY),
                  ("Inventory (scale only)", trade._INVENTORY_FRAME_GEOMETRY),
                  ("Client frame (inset from right/top edges)",
                   {n: "box" for n in trade._CLIENT_FRAME_GEOMETRY})]

        if layout is not None:
            # Apply for real, then read the globals the run itself would use.
            # calibrate() in section 7 applies the identical layout moments
            # later, so this leaves nothing behind that section does not.
            trade.apply_layout(layout)
            print(f"  (applied the measured layout: origin {layout.origin}, "
                  f"scale {layout.scale})\n")
            read = lambda n: getattr(trade, n, "<missing>")   # noqa: E731
        else:
            print("  (calibration REFUSED, so these are the built-in reference")
            print("   values, unchanged -- the run would not have started)\n")
            read = lambda n: trade._REFERENCE_GEOMETRY.get(n, "<missing>")  # noqa: E731

        total = 0
        for title, table in tables:
            names = [n for n in sorted(table) if n in trade._REFERENCE_GEOMETRY]
            if not names:
                continue
            print(f"  -- {title} --")
            for name in names:
                ref = trade._REFERENCE_GEOMETRY[name]
                got = read(name)
                same = "   (unchanged)" if str(ref) == str(got) else ""
                print(f"    {name:26} {str(ref):>28} -> {got}{same}")
                total += 1
            print()
        show("coordinates reported", total)

        # Nothing may be registered in a table yet absent from the dump.
        missing = [n for _, t in tables for n in t
                   if n not in trade._REFERENCE_GEOMETRY]
        if missing:
            show("REGISTERED BUT NOT CAPTURED", ", ".join(sorted(missing)),
                 "<-- these never get rewritten; they stay at reference")

        # And nothing may sit in the module looking like geometry while being
        # registered nowhere -- that is the constant that never scales.
        known = {n for _, t in tables for n in t}
        known |= {"NPC_BODY_OFFSET", "NPC_CLICK_OFFSETS", "LAYOUT",
                  "REF_SCREEN", "REF_CLIENT", "REF_TRADE_ORIGIN",
                  "REF_TRADE_SIZE", "REF_ROW_PITCH", "SCALE_LIMITS"}
        suspects = []
        for name in dir(trade):
            if name in known or not name.isupper() or name.startswith("_"):
                continue
            if name.startswith("REF_") or "COLOUR" in name or "COLOR" in name:
                continue
            v = getattr(trade, name)
            if isinstance(v, tuple) and 2 <= len(v) <= 5 and \
                    all(isinstance(e, (int, float)) for e in v) and \
                    any(abs(e) > 40 for e in v):
                suspects.append(f"{name}={v}")
        if suspects:
            print("\n  Tuples that look like coordinates but are registered in")
            print("  no table, so calibration never touches them. Most will be")
            print("  legitimate (thresholds, colours, ratios) -- but this is")
            print("  where a fixed pixel position hides:")
            for t in sorted(suspects):
                print(f"    {t}")
    except Exception:
        traceback.print_exc()

    rule("7. full calibrate(), exactly as a run would call it")
    try:
        print(f"  calibrate(save=False) -> "
              f"{trade.calibrate(verbose=True, save=False)}")
    except Exception:
        traceback.print_exc()

    rule("7b. the same state checks, now calibrated")
    print("  Section 3 ran against reference-sized regions. These run against")
    print("  the fitted ones. A line that changes here was never a fact about")
    print("  the screen -- it was the uncalibrated crop landing in the wrong")
    print("  place, and only this column means anything.\n")
    try:
        after = trade.grab()
        checks = (
            ("trade_window_open", lambda im: trade.trade_window_open(im)),
            ("register_tab_open", lambda im: trade.register_tab_open(im)),
            ("dialog_kind", lambda im: repr(trade.dialog_kind(im))),
            ("dialog_present", lambda im: trade.dialog_present(im)),
            ("find_npc", lambda im: trade.find_npc(im, retries=1)),
        )
        for name, fn in checks:
            try:
                now = fn(after)
            except Exception as exc:              # noqa: BLE001 - diagnostic
                now = f"FAILED: {exc}"
            was = _BEFORE.get(name, "?")
            moved = "  <== CHANGED, section 3 was wrong" if str(was) != str(now) else ""
            print(f"    {name:<20} before {str(was):<18} now {now}{moved}")
        # The region that matters most, cropped with the fitted numbers.
        try:
            after.crop(trade.TRADE_REGION).save(OUT / "trade_region_calibrated.png")
            show("TRADE_REGION crop", trade.TRADE_REGION,
                 "-> trade_region_calibrated.png")
            print("    Open that PNG: it should contain the Trade window and"
                  " almost nothing else.\n    If it is off-centre or clipped,"
                  " the fit is wrong no matter what the residual says.")
        except Exception as exc:                  # noqa: BLE001 - diagnostic
            print(f"    could not crop TRADE_REGION: {exc}")
        if not trade.find_row_buttons(after):
            print("\n    !! No row buttons read even after calibrating. read_rows"
                  " returns [] on\n       every frame in this state -- this is the"
                  " one failure that stops a run.")
        else:
            n = len(trade.find_row_buttons(after))
            print(f"\n    row buttons after calibration: {n}"
                  f"  (EXPECTED_ROWS {trade.EXPECTED_ROWS})"
                  + ("" if n == trade.EXPECTED_ROWS
                     else "  <== MISMATCH, read_rows will return [] for ever"))
    except Exception:
        traceback.print_exc()

    rule("8. what to send back")
    print(f"  The whole folder: {OUT}")
    print("  It contains screen.png, trade_region.png, anchor_*.png, words.txt")
    print("  and report.txt. Nothing was clicked; the game is untouched.")


buffer = io.StringIO()
try:
    with redirect_stdout(buffer):
        probe()
finally:
    text = buffer.getvalue()
    sys.stdout.write(text)
    try:
        (OUT / "report.txt").write_text(text, encoding="utf-8")
    except Exception as exc:
        print(f"(could not write report.txt: {exc})")
