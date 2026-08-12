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


def probe():
    env()
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

    rule("3. what is on screen")
    shot = trade.grab()
    shot.save(OUT / "screen.png")
    show("captured", f"{shot.size[0]}x{shot.size[1]}", "-> screen.png")
    show("trade_window_open", trade.trade_window_open(shot))
    show("register_tab_open", trade.register_tab_open(shot))
    show("dialog_kind", repr(trade.dialog_kind(shot)))
    show("dialog_present", trade.dialog_present(shot))
    show("find_npc", trade.find_npc(shot, retries=1))
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
            print("\n  Highest-confidence words actually read, in case an anchor")
            print("  came back split (the known 1080p failure is 'Refresh' ->")
            print("  'R' + 'efresh' at the wrong upscale):")
            for w in sorted(words, key=lambda w: -w.conf)[:30]:
                print(f"    {w.text!r:24} conf {w.conf:5.1f} at {w.centre}")
    except Exception:
        traceback.print_exc()

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

    rule("6. what every derived coordinate would become")
    print("  Reference value on the left, what this machine would use on the")
    print("  right. A wrong number here is a click somewhere in the game world.\n")
    try:
        # Captured lazily on the first apply_layout, so on a probe run it is
        # still empty and this section printed nothing at all.
        if not trade._REFERENCE_GEOMETRY:
            trade._capture_reference_geometry()
        use = layout or trade.LAYOUT
        print(f"  (using {'the measured layout' if layout else 'the built-in layout'}: "
              f"origin {use.origin}, scale {use.scale})\n")
        for name, kind in sorted(trade._TRADE_FRAME_GEOMETRY.items()):
            ref = trade._REFERENCE_GEOMETRY.get(name)
            if ref is None:
                continue
            try:
                if kind == "box":
                    got = trade._clamp_box(use.box(ref), use.screen)
                elif kind == "point":
                    got = use.point(ref)
                elif kind == "x":
                    got = use.x(ref)
                elif kind == "y":
                    got = use.y(ref)
                elif kind == "len":
                    got = use.length(ref)
                elif kind == "xpair":
                    got = tuple(use.x(v) for v in ref)
                elif kind == "lenpair":
                    got = tuple(use.length(v) for v in ref)
                elif kind == "boxes":
                    got = tuple(trade._clamp_box(use.box(b), use.screen)
                                for b in ref)
                else:
                    got = f"(kind {kind!r} not mapped by this probe)"
            except Exception as exc:
                got = f"ERROR {exc}"
            print(f"    {name:26} {str(ref):>28} -> {got}")
    except Exception:
        traceback.print_exc()

    rule("7. full calibrate(), exactly as a run would call it")
    try:
        print(f"  calibrate(save=False) -> "
              f"{trade.calibrate(verbose=True, save=False)}")
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
