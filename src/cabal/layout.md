# layout - reference coordinates to this screen's pixels

    screen_x = origin_x + reference_x * scale

`origin` is where the Trade window's top-left sits. `scale` is how much bigger
or smaller the window is than the reference frame the constants were measured
in. At 1920x1080 maximised those come out near `(7, 28)` and `0.743`.

## Why a transform and not a second table of numbers

There is no such thing as "the 1080p numbers". The window moves when the client
resizes and can be windowed at any size on any monitor. A measured transform
covers all of it; a second table of literals covers one more machine and then
rots.

That the UI scales at all is measured, not assumed: on two machines the
client-rect ratio and an independent least-squares fit of a dozen OCR'd anchor
words agreed to 0.03%.

## Positions vs distances

This is the distinction the whole module exists for.

| Kind | Method | Example |
|---|---|---|
| Position | `x` `y` `point` `box` | a tab's centre, a region to OCR |
| Distance | `length` | a column width, a row pitch, an approach offset |

`length()` applies scale and **not** origin. Adding the window's origin to a
width is meaningless and turns a half-width into an absolute x, which filters
away everything it was meant to select.

## Rules

- **`clamp()` before cropping.** PIL pads an out-of-bounds crop with BLACK
  rather than refusing, and OCR reads the padding as part of the image. An
  inverted box raises mid-sequence.
- **`reference_layout()` is correct only on the reference machine.** It is not
  a general fallback; on any other machine it clicks the game world.
- **A scale outside `SCALE_LIMITS` is a bad fit, not a small window.**
