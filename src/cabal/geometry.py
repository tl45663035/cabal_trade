"""Where things are in the Trade window, in REFERENCE coordinates.

Spec: geometry.md

Every number here is an observation of Cabal Online, measured at 2560x1440
with the Trade window at (10, 30). They are relative to the WINDOW's top-left,
not the screen's, so layout.Layout can convert them to any resolution.

Only what get_price_diff needs is here: the two tabs, the sort control, the
ten favourite slots and the offer table. The Register tab's listings table,
the registration panel, the dialogs and the NPC are all absent because this
flow never touches them.

READ THE UNITS. A value used as a POSITION goes through Layout.x/y/point/box.
A value used as a DISTANCE goes through Layout.length. Mixing them adds the
window's origin to a width, which is how a column half-width becomes an
absolute x and filters away everything.
"""

from __future__ import annotations

# ALL COORDINATES BELOW ARE TRUE REFERENCES, relative to the Trade window's
# own top-left, with NO screen origin baked in.
#
# This is worth stating because getting it wrong is invisible. The first
# version of this file was collected by reading trade.py's globals -- which
# apply_layout had ALREADY rewritten at import, using the built-in origin
# (10, 30). Every box and point came across (+10, +30) out. Nothing failed
# loudly: the sort control was looked for 30px below where it is, the column
# headers fell outside the band that identifies the tab, and the flow simply
# reported "the Purchase tab did not open" about a tab that was plainly open.

# --------------------------------------------------------------------------
# The window itself
# --------------------------------------------------------------------------

# The Trade window's size at reference scale, from its own top-left.
TRADE_SIZE = (1225, 1035)

# The whole window as a box, for OCR that should not see the game world.
TRADE_REGION = (0, 0, 1225, 1035)

# Words present when the Trade window is open at all.
#
# 'Trade' is the window's own title. It is paired with a tab label because a
# single word can be supplied by the 3D world behind the panel -- an item name,
# a chat line, a player's title -- and the two tabs are the only labels
# guaranteed to be present whichever tab is showing.
# ANY of these, not all, and 'Trade' is NOT among them. The window's own
# title is set in a stylised serif that OCR does not read reliably: it was
# missing from the anchors on the 1080p frame AND from a live Purchase tab
# here, on a window that was plainly open. A marker that is absent half the
# time is not a marker.
#
# These four are plain UI text and at least one is present on either tab.
TRADE_WINDOW_MARKERS = ("Purchase", "Register", "Adjust", "Function")

# THE ONE BAND THAT ANSWERS EVERY STATE QUESTION.
#
# The window title, both tab labels, the column headers that identify which
# tab is showing, and the sort control all sit above y=220 in the reference
# frame. So "is the window open", "which tab", and "which sort" are one OCR
# rather than three -- and at ~70ms of process launch per read, that is the
# difference that matters, not the pixel count.
#
# Read the positions below and check for yourself before narrowing this:
#   Trade (608, 19)   Purchase (128, 67)   Register (382, 69)
#   Item (142, 122)   Name (492, 119)      Status (1010, 119)
#   Function (1126, 118)                   sort control y 178..212
# Measured live: the sort control sits at reference y 148..182 and the column
# headers -- Category, Name, QTY, Price, Function -- at y ~233. The band has
# to reach past the headers, because they are what distinguishes the two tabs.
# The first offer row is at y 310 with a half-height of 24, so it starts at
# 286 and nothing here overlaps it.
STATE_BAND = (0, 0, 1225, 260)

# --------------------------------------------------------------------------
# Calibration anchors
# --------------------------------------------------------------------------
#
# Words whose position in the reference frame is known. Calibration finds them
# on screen and fits an origin and a scale to the pairs.
#
# They are spread deliberately: a fit from two anchors a few pixels apart is
# numerically meaningless however well it fits, so calibrate.py refuses unless
# the ones it found span a real vertical distance. 'Trade' at the top and
# 'Refresh' near the bottom are the ends of that span.
REF_ANCHORS = (
    ("Trade", (608, 19)),
    ("Purchase", (128, 67)),
    ("Adjust", (919, 65)),
    ("Name", (492, 119)),
    ("Item", (142, 122)),
    ("Status", (1010, 119)),
    ("Function", (1126, 118)),
    ("Period", (55, 869)),
    ("Selling", (331, 982)),
    ("Expired", (503, 982)),
    ("Sold", (674, 980)),
    ("Total", (863, 980)),
    ("Refresh", (1119, 981)),
)

# A fit needs at least this many anchors, spanning at least this many
# reference pixels vertically. Two anchors 20px apart can be fitted perfectly
# and still be wrong about the rest of the window.
MIN_ANCHORS = 2
MIN_ANCHOR_SPREAD = 250.0

# --------------------------------------------------------------------------
# Tabs
# --------------------------------------------------------------------------

PURCHASE_TAB = (128, 67)
REGISTER_TAB = (382, 69)

# Words present ONLY on the Purchase tab. Both are required: the two tabs
# share a window, and clicking a Purchase coordinate while Register is showing
# hits the listings table instead of the search controls.
PURCHASE_TAB_MARKERS = ("Category", "Function")

# Words present only on the REGISTER tab.
#
# NOT ('Register', 'Item'). Both of those appear on the Purchase tab too --
# 'Register' is the other tab's own label, and 'Item' turns up in the text
# search box ('search for item category') -- so that pair reported BOTH tabs
# open at once on a live Purchase tab.
#
# The column headers are the real discriminator, and the two tabs differ by
# exactly one: Purchase shows Category|Name|QTY|Price|Function, Register shows
# Name|QTY|Price|Status|Function. So 'Category' means Purchase and 'Status'
# means Register, with 'Function' on both to confirm the table is drawn.
#
# They sit at different heights, which is why STATE_BAND has to cover both:
# ~y119 on Register, ~y233 on Purchase, where the search box pushes them down.
REGISTER_TAB_MARKERS = ("Status", "Function")

# --------------------------------------------------------------------------
# The sort control
# --------------------------------------------------------------------------

# The closed control, showing the current sort. Inside STATE_BAND, which is
# why "which sort" costs nothing on top of "which tab".
SORT_REGION = (810, 148, 1070, 182)

# The DIRECTION is the word straight after "Price:", and nothing else will do.
#
# A substring test cannot do this job: checking for both "low" and "price" is
# true of "By Price:High to Low" as well, because the two labels are anagrams
# as far as substrings are concerned. Getting it wrong buys the most expensive
# offer on the board believing it to be the cheapest.
SORT_DIRECTION = r"price\s*:?\s*(low|high)"
# The control itself, clicked to open the menu.
SORT_BUTTON = (920, 165)
# Where the open menu's options are drawn.
SORT_OPTIONS = (780, 182, 1110, 255)

# --------------------------------------------------------------------------
# Favourite slots
# --------------------------------------------------------------------------
#
# Ten buttons along the bottom of the Purchase tab, each a saved search.
# Evenly pitched, so the first one and the pitch describe all ten.
FAVOURITE_FIRST = (646, 984)
FAVOURITE_PITCH = 57
FAVOURITE_COUNT = 10

# What each slot is bound to, on this account. Used to confirm that the
# results on screen belong to the slot that was just pressed rather than to
# the previous search.
FAVOURITE_SLOTS = {
    1: "Force Core(Highest)",
    2: "Force Core Set (Highest)",
    3: "Chaos Core",
    4: "Chaos Core Set",
    5: "Force Core (Ultimate)",
    6: "Force Core Set (Ultimate)",
    7: "Force Core(High)",
    8: "Force Core Set (High)",
    9: "Upgrade Core (Ultimate)",
    10: "Upgrade Core Set (Ultimate)",
}

# --------------------------------------------------------------------------
# The offer table
# --------------------------------------------------------------------------

# The first row's centre line, and the gap to the next.
ROW_TOP = 310
ROW_PITCH = 76
# How many rows fit on screen without scrolling. This flow never scrolls: it
# only ever reads row 1.
ROWS_VISIBLE = 10

# A row is OCR'd from a horizontal strip this wide, this far above and below
# its centre line. The half-height matters: at 0.76 a row pitch is 58px, so a
# raw +/-24 band would be 84% of a pitch and would straddle the rows either
# side, interleaving two rows' digits into one nonsense number.
ROW_BAND_X = (240, 1225)
ROW_HALF = 24

# Column boundaries within a row's strip, used to split the words by x.
NAME_MAX_X = 690          # the Name cell ends before the QTY column
PRICE_X = (890, 1070)     # the Price cell

# A listing below this is a misread, not a bargain. Prices in this market are
# six figures and up; a three-digit read is a clipped one.
MIN_PLAUSIBLE_PRICE = 1000

# --------------------------------------------------------------------------
# Opening the shop
# --------------------------------------------------------------------------

# The premium path: the Agent Shop key is an inventory item, right-clicked to
# open the shop from anywhere. The alternative is walking to the NPC, which
# this flow does not implement -- see shop.md.
PREMIUM_KEY_TAB = 8
PREMIUM_KEY_SLOT = (1, 7)   # row, column within that tab

# The inventory panel's grid, as insets from the CLIENT's right and top edges
# rather than from the Trade window -- the panel is pinned to the client edge
# and does not move with the Trade window.
INVENTORY_FIRST_SLOT_INSET = (300, 300)   # from client right, from client top
INVENTORY_SLOT_PITCH = 61.5
INVENTORY_TAB_ONE_INSET = (300, 232)
INVENTORY_TAB_PITCH = 69
