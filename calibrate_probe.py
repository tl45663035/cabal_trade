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


def probe():
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
        show("upscale chosen", trade._ocr_upscale_for(seed)
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
        for phrase, ref in trade.REF_ANCHORS:
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
