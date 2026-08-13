"""The standalone src/cabal stack: layout maths, calibration, offer parsing.

DRIVES NOTHING. Nothing here calls screen.grab, screen.click or the game. The
calibration tests render their own frames, so they are deterministic and need
no captured screenshot committed to the repo.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from PIL import Image, ImageDraw          # noqa: E402

from cabal import calibrate, geometry as geo, ocr, purchase   # noqa: E402
from cabal.layout import Layout, reference_layout             # noqa: E402

PASS = FAIL = 0


def check(ok, why):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {why}")


def rule(title):
    print("=" * 70)
    print(title)
    print("=" * 70)


# --------------------------------------------------------------------------
rule("layout: positions take the origin, distances do not")

L = Layout(screen=(1920, 1080), origin=(7, 28), scale=0.75,
           client=(0, 23, 1920, 1040))

check(L.x(100) == 7 + 75, "x() adds the origin and scales")
check(L.y(100) == 28 + 75, "y() adds the origin and scales")
check(L.length(100) == 75,
      "length() scales but does NOT add the origin -- a width is not a "
      "position, and adding the window's origin to one is how a column "
      "half-width becomes an absolute x and filters everything away")
check(L.point((100, 200)) == (82, 178), "point() is x() and y() together")
check(L.box((0, 0, 100, 100)) == (7, 28, 82, 103), "box() is two points")

ref = reference_layout()
check(ref.x(100) == 110 and ref.length(100) == 100,
      "at reference scale a distance is unchanged, and a position is offset "
      "by the window origin only")
check(all(reference_layout().length(n) == n for n in (2, 4, 16, 45, 60, 300)),
      "every distance is identity at scale 1.0")

clamped = L.clamp((-50, -50, 5000, 5000))
check(clamped == (0, 0, 1920, 1080),
      "clamp keeps a box on screen -- PIL pads an out-of-bounds crop with "
      "BLACK rather than refusing, and OCR then reads the padding")
check(L.clamp((100, 100, 50, 50))[2] > L.clamp((100, 100, 50, 50))[0],
      "an inverted box is corrected, not passed on to raise mid-sequence")

# --------------------------------------------------------------------------
rule("calibration recovers a known transform from a rendered frame")


def render(scale, origin, size=(1920, 1080), anchors=None):
    """Draw the anchor words where a window at (origin, scale) would put them."""
    img = Image.new("RGB", size, (14, 14, 16))
    draw = ImageDraw.Draw(img)
    for phrase, (rx, ry) in (anchors or geo.REF_ANCHORS):
        x = origin[0] + rx * scale
        y = origin[1] + ry * scale
        # Drawn centred on the anchor point, because that is what find_phrase
        # measures and what the reference coordinates mean.
        w = draw.textlength(phrase)
        draw.text((x - w / 2, y - 6), phrase, fill=(232, 232, 232))
    return img


for want_scale, want_origin in ((1.0, (10, 30)), (0.75, (7, 28)),
                                (0.6, (40, 60))):
    frame = render(want_scale, want_origin)
    pairs = calibrate.find_anchors(frame, upscale=max(1.0, 2.0 / want_scale))
    fit = calibrate._fit(pairs)
    if fit is None:
        check(False, f"no fit at scale {want_scale}")
        continue
    ox, oy, got = fit
    check(abs(got - want_scale) / want_scale < 0.02,
          f"scale {want_scale} recovered as {got:.4f} from {len(pairs)} anchors")
    check(abs(ox - want_origin[0]) < 6 and abs(oy - want_origin[1]) < 6,
          f"origin {want_origin} recovered as ({ox:.1f}, {oy:.1f})")

rule("calibration refuses rather than guessing")

# Anchors bunched at the top: a perfect fit that describes the rest of the
# window wrongly.
bunched = [a for a in geo.REF_ANCHORS if a[1][1] < 130]
frame = render(0.75, (7, 28), anchors=bunched)
pairs = calibrate.find_anchors(frame, upscale=2.7)
check(calibrate._spread(pairs) < geo.MIN_ANCHOR_SPREAD,
      f"{len(pairs)} anchors spanning {calibrate._spread(pairs):.0f}px are "
      f"below the {geo.MIN_ANCHOR_SPREAD:.0f}px bar")
check(calibrate._fit(pairs[:1]) is None, "one anchor cannot fit a transform")
check(calibrate._fit([]) is None, "no anchors cannot fit a transform")

# A scale outside SCALE_LIMITS is a bad fit, not a small window.
absurd = [((0, 0), (0, 0)), ((10000, 10000), (100, 100))]
check(calibrate._fit(absurd) is None,
      "an implausible scale is refused -- a wrong scale does not fail loudly, "
      "it clicks confidently in the wrong place")

rule("offer parsing")

O = purchase.Offer
check(purchase._pack_from_name("Chaos Core Set X 148") == 148,
      "a bundle carries its count in the NAME and nowhere else")
check(purchase._pack_from_name("Chaos Core Set X 1,024") == 1024,
      "a thousands separator in the count is read")
check(purchase._pack_from_name("Force Core(Highest)") == 1,
      "an unbundled item is a pack of one")
check(purchase._pack_from_name("Upgrade Core (Ultimate)") == 1,
      "a trailing bracket is not a count")

check(purchase._number("7,400,000") == 7_400_000, "a price parses with commas")
check(purchase._number("") is None, "an empty cell is None, not 0")
check(purchase._number("---") is None, "an unreadable cell is None, not 0")

check(O(1, "Item X 10", 7_400_000, 10, 1).unit_price == 740_000,
      "a 10-pack at 7,400,000 is 740,000 per unit")
check(O(1, "Item", 694_980, 1, 1).unit_price == 694_980,
      "a pack of 1 is divided too, and is unchanged by it")
check(O(1, "Item X 148", 109_628_780, 148, 1).unit_price == 740_735,
      "the bundle that makes raw subtraction wrong reads 740,735 per unit")
check(O(1, "Item", 0, 1, 1).unit_price is None,
      "a price of 0 is unreadable, not free")
check(O(1, "Item", 100, 0, 1).unit_price is None,
      "a pack of 0 is refused rather than treated as 1")

rule("the sort direction is the word after 'Price:', not a substring")

for text, want in (("By Price:Low to High", True),
                   ("By Price:High to Low", False),
                   ("Price: Low to High", True),
                   ("Price:High to Low", False),
                   ("Category", False),
                   ("", False)):
    match = purchase._SORT_DIRECTION.search(text)
    got = bool(match) and match.group(1).casefold() == "low"
    check(got == want,
          f"{text!r} -> low-to-high {got}, expected {want}. 'low' and 'high' "
          f"both appear in BOTH labels, so a substring test cannot tell them "
          f"apart -- and getting it wrong buys the dearest offer believing it "
          f"to be the cheapest")

rule("favourite slots")

check(purchase.favourite_point(L, 1) == L.point(geo.FAVOURITE_FIRST),
      "slot 1 is the first favourite")
step = (purchase.favourite_point(L, 2)[0] - purchase.favourite_point(L, 1)[0])
check(step == L.length(geo.FAVOURITE_PITCH),
      "the gap between slots is the SCALED pitch, not the raw one")
check(purchase.favourite_point(L, 1)[1] == purchase.favourite_point(L, 10)[1],
      "all ten sit on one row")
for bad in (0, 11, -1):
    try:
        purchase.favourite_point(L, bad)
        check(False, f"slot {bad} should have been refused")
    except ValueError:
        check(True, f"slot {bad} is refused")

check(purchase.offers_match_slot(4, [O(1, "Chaos Core Set X 148", 1, 148, 1)]),
      "results whose first row is the bound item match the slot")
check(not purchase.offers_match_slot(4, [O(1, "Force Core(Highest)", 1, 1, 1)]),
      "results from a DIFFERENT item do not match -- stale rows read as a "
      "real answer look exactly like a successful search of the wrong thing")
check(not purchase.offers_match_slot(4, []), "no results never match")

print()
print("-" * 70)
print(f"{PASS + FAIL} checks, {FAIL} failed")
sys.exit(1 if FAIL else 0)
