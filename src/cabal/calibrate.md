# calibrate - measuring the Trade window

## The bootstrap problem

Reading the anchors needs an OCR upscale. Choosing the upscale needs to know
how big the UI is, which is what the anchors are for.

It is broken with a seed that involves **no OCR at all**: the ratio of this
client's width to the reference client's width, straight off the window rect.
Accurate to a fraction of a percent, which is far better than the upscale
needs.

`ocr_seed_scale()` takes the **minimum** of the width and height ratios. An
aspect ratio differing from the reference means the UI is letterboxed inside
the client, and the smaller ratio is the one it follows.

## The fit

A similarity transform, no rotation, uniform scale:

    screen = origin + reference * scale

Both axes are pooled into one scale because the UI scales uniformly. Fitting
x and y independently would let one bad anchor pull a single axis and produce a
layout subtly wrong in one direction only.

After the first fit, the **worst anchor is dropped once and the fit repeated**.
A single mis-located word - the same label appearing twice on screen, or a
partial match - drags the whole fit. If dropping it improves the worst residual
and enough anchors remain, the smaller set is the better measurement.

## Refusals

`measure_layout` returns `None` rather than a guess when:

| Condition | Why |
|---|---|
| Game window not found | there is nothing to measure against |
| Fewer than `MIN_ANCHORS` found | a transform cannot be fitted |
| Anchors span less than `MIN_ANCHOR_SPREAD` | they fit perfectly and still describe the rest of the window wrongly |
| Fitted scale outside `SCALE_LIMITS` | that is a bad fit, not a small window |

Every caller treats a layout as authoritative, so handing back a fit that did
not meet the bar would put a wrong number everywhere at once.

## Rules

- **Measured every run, never stored.** A saved layout cannot be trusted: the
  window is draggable within an unchanged client, so a staleness test on screen
  size and client rect misses the one number the layout is about - its origin.
  A stale layout does not fail loudly, it clicks confidently in the wrong place.
- **The reference fallback is narrow on purpose.** Matching the screen size is
  not enough; the built-in coordinates assume the reference CLIENT too, and the
  game can be windowed. A 1920x1080 window inside a 2560x1440 desktop matches
  the monitor while every coordinate is wrong.

## Validation

Against a real 1920x1080 frame captured on a second machine, this module fits
**12 anchors, worst residual 2.6px, scale 0.742757, origin (7.0, 27.9)** - which
agrees to **0.051%** with an independently written implementation that has been
run live on that machine.

The unit tests do not use that frame. They render their own, placing each
anchor where a window at a chosen origin and scale would put it, then assert
the fit recovers those values. Deterministic, and no captured frame has to be
committed.
