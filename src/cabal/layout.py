"""The transform between reference coordinates and this screen's pixels.

Spec: layout.md

Every coordinate in geometry.py was measured on one machine: 2560x1440, the
game maximised, the Trade window at (10, 30). None of it transfers directly.
The Trade window is drawn at a position that depends on the client size, and
its contents SCALE with the client -- proven twice, on two machines: the
client-rect ratio and an independent least-squares fit of a dozen OCR'd anchor
words agreed to 0.03%.

So a coordinate is stored as a reference value and converted on use:

    screen_x = origin_x + reference_x * scale

`origin` is where the Trade window's top-left sits on this screen. `scale` is
how much bigger or smaller the window is than the reference. At 1920x1080 with
the game maximised those come out around (7, 28) and 0.743.

WHY A TRANSFORM RATHER THAN A SECOND SET OF CONSTANTS: there is no such thing
as "the 1080p numbers". The window moves when the client resizes, and it can
be windowed at any size on any monitor. A measured transform covers all of
that; a second table of literals covers one more machine and then rots.
"""

from __future__ import annotations

from dataclasses import dataclass

# What the constants in geometry.py were measured against.
REF_SCREEN = (2560, 1440)
REF_CLIENT = (0, 23, 2560, 1392)
REF_TRADE_ORIGIN = (10, 30)

# A fitted scale outside this range is not a small window, it is a bad fit --
# two anchors mistaken for each other, or a stray word matched as one. Refusing
# is right: a wrong scale does not fail loudly, it clicks confidently in the
# wrong place.
SCALE_LIMITS = (0.4, 2.5)


@dataclass(frozen=True)
class Layout:
    """Where the Trade window is, and how big, on THIS screen."""

    screen: "tuple[int, int]"
    origin: "tuple[int, int]"
    scale: float
    client: "tuple[int, int, int, int] | None" = None
    measured_from: str = "built-in reference"

    # -- the conversions ---------------------------------------------------

    def x(self, value: float) -> int:
        return int(round(self.origin[0] + value * self.scale))

    def y(self, value: float) -> int:
        return int(round(self.origin[1] + value * self.scale))

    def point(self, pair: "tuple[float, float]") -> "tuple[int, int]":
        return (self.x(pair[0]), self.y(pair[1]))

    def box(self, rect: "tuple[float, float, float, float]") -> "tuple[int, int, int, int]":
        return (self.x(rect[0]), self.y(rect[1]),
                self.x(rect[2]), self.y(rect[3]))

    def length(self, value: float) -> int:
        """A DISTANCE, not a position. No origin, only scale.

        The distinction is the whole point of having two methods. A column
        width, a row pitch and a click's approach offset are all distances:
        adding the window's origin to them would move them by the position of
        the window, which is meaningless for a width.
        """
        return int(round(value * self.scale))

    def clamp(self, rect: "tuple[int, int, int, int]") -> "tuple[int, int, int, int]":
        """A box trimmed to the screen, and never inverted.

        PIL pads an out-of-bounds crop with BLACK rather than refusing, so an
        unclamped region silently gains a black margin that OCR reads as part
        of the image. An inverted box raises instead, mid-sequence.
        """
        w, h = self.screen
        left = max(0, min(int(rect[0]), w - 1))
        top = max(0, min(int(rect[1]), h - 1))
        right = max(left + 1, min(int(rect[2]), w))
        bottom = max(top + 1, min(int(rect[3]), h))
        return (left, top, right, bottom)

    def cropped(self, rect) -> "tuple[int, int, int, int]":
        """box() then clamp(), which is what a caller reading pixels wants."""
        return self.clamp(self.box(rect))


def reference_layout(screen: "tuple[int, int] | None" = None) -> Layout:
    """The built-in layout: correct only on the reference machine.

    Used before calibration and as the fallback when the screen and client
    match the reference exactly. It is deliberately NOT used as a general
    fallback: on any other machine it is wrong, and being wrong here means
    clicking the game world instead of the window.
    """
    return Layout(screen=screen or REF_SCREEN,
                  origin=REF_TRADE_ORIGIN,
                  scale=1.0,
                  client=REF_CLIENT,
                  measured_from="built-in reference")


def plausible_scale(scale: float) -> bool:
    return SCALE_LIMITS[0] <= scale <= SCALE_LIMITS[1]
