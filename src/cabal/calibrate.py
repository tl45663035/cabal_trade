"""Measuring where the Trade window is, and how big, on this screen.

Spec: calibrate.md

The bootstrap problem: reading the anchors needs an OCR upscale, and choosing
the upscale needs to know how big the UI is, which is what the anchors are for.
It is broken with a seed that involves no OCR at all -- the ratio of this
client's width to the reference client's width, straight off the window rect.
That is accurate to a fraction of a percent, which is far better than the
upscale needs.

MEASURED EVERY RUN, NEVER STORED. A saved layout cannot be trusted: the Trade
window is draggable within an unchanged client, so a staleness test on screen
size and client rect misses the one number the layout is actually about, its
origin. A stale layout does not fail loudly -- it clicks confidently in the
wrong place, which is the failure this whole module exists to prevent.
"""

from __future__ import annotations

from PIL import Image

from . import geometry as geo
from . import ocr
from . import screen
from .layout import (Layout, REF_CLIENT, REF_SCREEN, plausible_scale,
                     reference_layout)

# Upscales tried, in order, until enough anchors are found with enough spread.
# Starting at the seed and going up: too small and the glyphs are unreadable,
# too large and Tesseract starts splitting words -- the 1080p failure was
# 'Refresh' arriving as 'R' + 'efresh'.
UPSCALE_STEPS = (1.0, 1.5, 2.0, 3.0)

# Below this confidence a word is noise. Anchors are large, high-contrast panel
# labels; a low-confidence match on one of them is usually a different word.
ANCHOR_MIN_CONF = 40.0


def ocr_seed_scale() -> float:
    """How big this client is against the reference, from the window rect only.

    No OCR, so it cannot fail the way OCR fails. The MINIMUM of the width and
    height ratios: an aspect ratio that differs from the reference means the
    UI is letterboxed within the client, and the smaller ratio is the one the
    UI actually follows.
    """
    rect = screen.client_rect()
    if not rect:
        return 1.0
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    ref_w = REF_CLIENT[2] - REF_CLIENT[0]
    ref_h = REF_CLIENT[3] - REF_CLIENT[1]
    if width <= 0 or height <= 0:
        return 1.0
    return min(width / ref_w, height / ref_h)


def _fit(pairs: "list[tuple[tuple[int, int], tuple[float, float]]]"):
    """Least-squares origin and uniform scale for (measured, reference) pairs.

    A similarity transform with no rotation: screen = origin + reference*scale.
    Both axes are pooled into one scale because the UI scales uniformly --
    fitting x and y independently would let a bad anchor on one axis pull that
    axis alone and produce a layout that is subtly wrong in one direction.
    """
    n = len(pairs)
    if n < geo.MIN_ANCHORS:
        return None
    mx = sum(p[0][0] for p in pairs) / n
    my = sum(p[0][1] for p in pairs) / n
    rx = sum(p[1][0] for p in pairs) / n
    ry = sum(p[1][1] for p in pairs) / n
    num = sum((p[1][0] - rx) * (p[0][0] - mx) +
              (p[1][1] - ry) * (p[0][1] - my) for p in pairs)
    den = sum((p[1][0] - rx) ** 2 + (p[1][1] - ry) ** 2 for p in pairs)
    if den <= 0:
        return None
    scale = num / den
    if not plausible_scale(scale):
        return None
    return (mx - scale * rx, my - scale * ry, scale)


def _residual(pairs, origin_x, origin_y, scale) -> float:
    worst = 0.0
    for (sx, sy), (rx, ry) in pairs:
        dx = abs(origin_x + rx * scale - sx)
        dy = abs(origin_y + ry * scale - sy)
        worst = max(worst, dx, dy)
    return worst


def _spread(pairs) -> float:
    """The vertical distance the anchors span, in REFERENCE pixels."""
    if len(pairs) < 2:
        return 0.0
    ys = [p[1][1] for p in pairs]
    return max(ys) - min(ys)


def find_anchors(image: Image.Image, upscale: float,
                 verbose: bool = False) -> list:
    """Anchor words found in `image`, as (measured, reference) pairs."""
    # The whole screen, because before calibration there is no window box to
    # crop to -- that is the thing being measured.
    words = ocr.find_words(image, (0, 0, image.width, image.height),
                           upscale=upscale, min_conf=ANCHOR_MIN_CONF)
    # A line-grouping tolerance in SCREEN pixels. Ten reference px scaled by
    # roughly what the seed says, floored so it never collapses to zero.
    tolerance = max(4, int(round(10 * max(0.3, upscale and 1.0 or 1.0))))
    pairs = []
    for phrase, reference in geo.REF_ANCHORS:
        centre = ocr.find_phrase(words, phrase, tolerance)
        if centre is not None:
            pairs.append((centre, reference))
            if verbose:
                print(f"  anchor {phrase!r:<12} at {centre}  "
                      f"(reference {reference})")
        elif verbose:
            print(f"  anchor {phrase!r:<12} not found")
    return pairs


def measure_layout(source: "Image.Image | None" = None,
                   verbose: bool = True) -> "Layout | None":
    """Measure the Trade window, or None if it cannot be measured.

    None rather than a guess. Every caller treats a layout as authoritative,
    so handing back a fit that did not meet the bar would put a wrong number
    everywhere at once.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    size = screen.screen_size()
    rect = screen.client_rect()
    if rect is None:
        say("  the game window was not found, so there is nothing to measure "
            "against.")
        return None
    say(f"  screen {size[0]}x{size[1]}")
    say(f"  game client area: {rect}")

    seed = ocr_seed_scale()
    shot = source if source is not None else screen.grab()

    best = None
    for step in UPSCALE_STEPS:
        upscale = max(1.0, step / max(0.2, seed))
        pairs = find_anchors(shot, upscale, verbose=False)
        if len(pairs) >= geo.MIN_ANCHORS and _spread(pairs) >= geo.MIN_ANCHOR_SPREAD:
            best = pairs
            break
        # KEPT ANYWAY IF IT IS THE BEST SO FAR. A run that never reaches the
        # bar should report the closest it came, not nothing -- the difference
        # between "found three anchors, all bunched at the top" and "found
        # none" is the difference between a crop problem and a shut window.
        if best is None or len(pairs) > len(best):
            best = pairs

    if not best or len(best) < geo.MIN_ANCHORS:
        say(f"  only {len(best or [])} anchor(s) read; a fit needs "
            f"{geo.MIN_ANCHORS}. Is the Trade window open?")
        return None
    if _spread(best) < geo.MIN_ANCHOR_SPREAD:
        say(f"  {len(best)} anchor(s) read, but they span only "
            f"{_spread(best):.0f} reference px vertically and a fit needs "
            f"{geo.MIN_ANCHOR_SPREAD:.0f}. Two anchors close together fit "
            f"perfectly and still describe the rest of the window wrongly.")
        return None

    fitted = _fit(best)
    if fitted is None:
        say(f"  {len(best)} anchor(s) read, but no plausible scale fits them.")
        return None
    origin_x, origin_y, scale = fitted

    # DROP THE WORST ANCHOR AND REFIT, once. A single mis-located word -- the
    # same label appearing twice on screen, or a partial match -- drags the
    # whole fit. If dropping it improves the worst residual and enough anchors
    # remain, the smaller set is the better measurement.
    worst_before = _residual(best, origin_x, origin_y, scale)
    if len(best) > geo.MIN_ANCHORS + 1 and worst_before > 4:
        scored = sorted(best, key=lambda p: (
            abs(origin_x + p[1][0] * scale - p[0][0]) +
            abs(origin_y + p[1][1] * scale - p[0][1])))
        trimmed = scored[:-1]
        again = _fit(trimmed)
        if again and _spread(trimmed) >= geo.MIN_ANCHOR_SPREAD:
            if _residual(trimmed, *again) < worst_before:
                best = trimmed
                origin_x, origin_y, scale = again

    residual = _residual(best, origin_x, origin_y, scale)
    layout = Layout(
        screen=size,
        origin=(int(round(origin_x)), int(round(origin_y))),
        scale=scale,
        client=rect,
        measured_from=(f"{len(best)} anchors fitted, worst residual "
                       f"{residual:.1f}px over a {_spread(best):.0f}px span"))
    say(f"Calibrated: screen {size[0]}x{size[1]}, Trade window at "
        f"{layout.origin}, scale {layout.scale:.3f} ({layout.measured_from})")
    return layout


def calibrated_layout(verbose: bool = True) -> "Layout | None":
    """A measured layout, or the built-in one when this IS the reference machine.

    The fallback is deliberately narrow. Matching the screen size is not
    enough -- the built-in coordinates assume the reference CLIENT too, and the
    game can be windowed. On a 1920x1080 window inside a 2560x1440 desktop the
    monitor matches while every coordinate is wrong, and the failure is silent.
    """
    layout = measure_layout(verbose=verbose)
    if layout is not None:
        return layout
    size = screen.screen_size()
    rect = screen.client_rect()
    if tuple(size) == REF_SCREEN and rect and tuple(rect) == REF_CLIENT:
        if verbose:
            print("  could not calibrate, but this screen and game window "
                  "match the reference the coordinates were measured on, so "
                  "the built-in values are used.")
        return reference_layout(size)
    return None
