"""Cabal Online automation: screen capture, Alz reading and Agent Shop trading.

Everything lives here -- capture, OCR, input and the trade automation -- so
there is one file to read and one to run.

Requires: pip install mss Pillow
          winget install UB-Mannheim.TesseractOCR

Cabal runs elevated, so this must be run from an Administrator terminal or
Windows silently discards every click.

    py trade.py --calibrate                measure this machine's layout
    py trade.py --shot                     capture the screen
    py trade.py --alz                      read the Alz balance
    py trade.py --list                     show the Agent Shop listings
    py trade.py --open                     open the shop via the NPC
    py trade.py --relist 3                 cancel row 3 and re-list it
    py trade.py --relist-rows 1-10         the same for the first ten rows
    py trade.py --repeat "relist-rows 1-10" --for 120 --every 30

FIRST RUN ON A NEW MACHINE
    Open the Agent Shop so the Trade window is visible, then:
        py trade.py --calibrate
    The coordinates built into this file were measured at 2560x1440. On any
    other screen they point at the wrong pixels, and a click that misses the UI
    lands in the game world -- which moves the character or an item. So every
    command that clicks refuses to run until a calibration exists that matches
    the current screen and game window. The result is saved to
    calibration.json and re-measured automatically whenever either changes.
"""

from __future__ import annotations

import argparse
import atexit
import csv
import ctypes
import ctypes.wintypes
import io
import itertools
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
import datetime as _dt
from pathlib import Path

# ==========================================================================
# SETTINGS -- the values you are most likely to want to change.
# ==========================================================================
#
# Everything here is a plain literal and is defined ONCE, in this block.
# The rest of the file reads these names; there is no second copy further
# down that would silently win. Derived geometry (regions, offsets, the
# calibration frame) is NOT here on purpose -- it is computed from the
# measured layout and editing it by hand would send clicks into the game
# world.
#

# --------------------------------------------------------------------------
# PRICE FLOORS -- the money numbers
# --------------------------------------------------------------------------

# Absolute per-item floors, matched against the listing name (case-insensitive).
# These bind no matter what the market suggests.
# (token, full catalogue name, floor). The token is a fast substring test; the
# full name is what a corrupted read is compared against, because a 3-character
# token is far too small a target for OCR. Measured over realistic single-glyph
# corruptions of the VIP name, the token alone lost the floor 4.8% of the time
# -- every one of V->Y, V->U, P->F, P->R, P->B, I->T -- while also matching
# 'V|pgrade Core(High)', which folds to contain "vip" and would have listed 158
# cores at 110,000,000 each.
#
# The catalogue name must be spelled as the GAME renders it, not as it is said
# in conversation: the row reads "Siena's Unbinding Stone", and dropping the
# "'s" would cost similarity on every single match for no reason.
ITEM_PRICE_FLOORS: tuple[tuple[str, str, int], ...] = (
    ("vip", "Yekaterina VIP Membership", 104_000_000),
    # 'siena' is the token rather than 'unbinding': the plain "Unbinding Stone"
    # is a different, far cheaper item, and 'unbinding' would hand it this
    # floor on the token route. 'siena' is 5 characters and unique to this
    # item, so it is both a stronger target than 'vip' and a narrower one.
    ("siena", "Siena's Unbinding Stone", 71_000_000),
    # 'gempack' rather than 'gem': plain "Force Gem" scores 0.593 against this
    # catalogue name, which clears the 0.4 token bar, so the shorter token
    # would hand a different item this floor. 'gempack' survives every damaged
    # read measured (a clipped trailer, a dropped letter, 0-for-O) because the
    # similarity route carries those.
    #
    # CAVEAT, measured not assumed: a DIFFERENT PACK SIZE inherits this floor
    # and no token can prevent it. "Force Gem Package (x100)" scores 0.947
    # against this name and "(x40)" scores 0.973, both far above the 0.75
    # similarity bar -- they are nearly the same string. Only the x400 has ever
    # been listed on this account, so it is latent; if a smaller pack is ever
    # listed it would go up at 180,000,000, never sell, and pay a percentage
    # fee on that figure. Give it its own entry before listing one.
    ("gempack", "Force Gem Package (x400)", 175_000_000),
    # "Epic Booster (High)" was catalogued here at 24,000,000 and was removed
    # on 2026-08-07 at the operator's request. Only the Highest grade is
    # listed now.
    #
    # What that changed, measured rather than assumed:
    #
    #     read                       was           now
    #     Epic Booster (High)        24,000,000    44,000,000
    #     Epic Booster (Highest)     44,000,000    44,000,000
    #     "Epic Booster (High"       24,000,000    44,000,000
    #     "Epic Booster (Highes)"    44,000,000    44,000,000
    #
    # Every Epic Booster read, clean or clipped, now takes 44,000,000. Two
    # consequences, one good and one to know about:
    #
    #  * the residual collision is gone. The two names differ by four
    #    characters and score 0.909 against each other, so they always matched
    #    each other's entry; a "(Highest)" clipped at exactly the character
    #    before "est)" read as "Epic Booster (High", which IS the (High) key --
    #    identical strings, separable by no name-based rule -- and took
    #    24,000,000 for a 44,000,000 item. With one entry there is nothing left
    #    to confuse it with.
    #
    #  * a genuine (High) listing would now carry a floor above its own value.
    #    It was reading 25,000,000 live on 2026-08-06, so at a 44,000,000 floor
    #    it would go up and never sell. That is the safe direction -- too high
    #    costs a cycle, too low sells the dearer grade 20,000,000 short.
    #
    #    That cost is accepted, not overlooked: the operator has retired the
    #    (High) grade and will not be listing it again. The entry is not
    #    waiting to be restored. If that ever changes, add it back at
    #    24,000,000 AND restore the best-match and collision tests from git
    #    history -- they are what proved the two grades could be told apart,
    #    and floor_booster_test fails on purpose if the entry returns without
    #    them.
    #
    # The token stays "epicboost", deliberately shorter than the shared prefix
    # "epicbooster": a read clipped to "Epic Boost" still contains it, and a
    # token exists to rescue exactly the clipped reads the similarity route
    # cannot. The catalogue spelling is the game's own, verified live.
    ("epicboost", "Epic Booster (Highest)", 44_000_000),
)

FALLBACK_PRICE = 10_000_000_000    # 10B, when the game suggests no price

# Below this, a table price is treated as a misread rather than a real figure.
# Every guard derives from that number, and the `if original` / `if price_floor`
# short-circuits mean a small value silently disables all of them at once.
MIN_PLAUSIBLE_PRICE = 1_000

# Sanity checks on relisting.
#
# There is NO relative floor against the previous price -- the rule is to take
# the lowest current price, whatever it is. A MAX_PRICE_DROP constant and a
# price_floor_for() helper implementing a 5% floor used to live here, called
# from nowhere, while relist()'s docstring described them as binding. Dead
# safety machinery is worse than none: it invites the one floor that does bind
# (ITEM_PRICE_FLOORS) to be relaxed against a backstop that was never there.
#
# A market price below this fraction of the listed price is reported as a NOTE
# and then listed at anyway.
SUSPECT_PRICE_FRACTION = 0.5

# A relist may not drop below this fraction of what the item is CURRENTLY
# listed at. Listed at 200,000, the lowest a relist can go is 198,000.
#
# This is the brake that was missing. "Take the lowest current price, whatever
# it is" is right when the reading is right, and catastrophic when it is not:
# an unfloored item whose market read clipped to 999 was listed at 999, and
# nothing in the chain questioned it -- MIN_PLAUSIBLE_PRICE guards the table
# read, not the market suggestion, and SUSPECT_PRICE_FRACTION only printed a
# note. Only items in ITEM_PRICE_FLOORS were protected at all.
#
# Deliberately a RATCHET, not a hard floor. A genuine crash is still followed,
# 1% per relist, converging on the market over many cycles -- 200,000 against a
# real market of 100,000 walks 198,000 -> 196,020 -> 194,060 and so on, and
# takes 69 relists to halve. That is the trade: a real drop costs more cycles
# of sitting over the market, and a misread costs 1% instead of everything.
#
# History, because the number has moved twice and the reasons differ:
#
#   0.90            the original allowance.
#   0.90 -> 0.95    2026-08-07, after a Yekaterina VIP went 115,988,564 ->
#                   104,999,999 in ONE relist. A 9.47% drop, so WITHIN the old
#                   allowance -- the guard did not fail, it was set wider than
#                   intended. At 0.95 that relist is refused at 110,189,136.
#   0.95 -> 0.99    2026-08-08, at the operator's request, and now carrying
#                   more weight than before: COST_FLOOR_ON_RELIST was turned
#                   off the same day, so the managed Cores have no absolute
#                   floor at all (their ITEM_PRICE_FLOORS entry is 0). This
#                   ratchet is the only thing left limiting a descent on them,
#                   and 1% a cycle is a slow enough walk to notice.
RELATIVE_PRICE_FLOOR = 0.99

# --------------------------------------------------------------------------
# WHAT GETS RELISTED
# --------------------------------------------------------------------------

# Relisting pushes the quantity to the maximum available for every item, so a
# listing of 3 does not come back as a listing of 1. Add name fragments here to
# exclude particular items from that.
MAXIMISE_ALL_QUANTITIES = True

NO_MAX_QUANTITY_ITEMS: tuple[str, ...] = ()

# What to type to fill the quantity field. The game clamps entry to the stack's
# maximum, so anything larger than a stack can hold maximises it -- no need to
# read the maximum off the panel first and type it back exactly.
MAX_QTY_ENTRY = 9999

# How far the panel's stack size may differ from the quantity the table showed
# before the load is treated as a different item rather than a misread digit.
# A different item differs by orders of magnitude; an OCR slip differs by one
# glyph. Sized from the incident that motivated it: the table read a 233-stack
# as 230, and exact equality aborted AFTER the cancel had committed -- which
# stranded the stack, failed the next three cycles on the empty-work-tab check,
# and stopped a five-hour run.
QTY_CROSSCHECK_ABSOLUTE = 5

QTY_CROSSCHECK_FRACTION = 0.10

# --------------------------------------------------------------------------
# PACE AND PATIENCE
# --------------------------------------------------------------------------

# Pace every input the script sends. The game is a live client with its own
# animations and server round-trips; acting faster than a person can leaves it
# behind and produces the "the click did nothing" failures.
#
# Lowered from 0.5 to 0.1 on 2026-08-07 at the operator's request. It is paid
# after EVERY move, click and key press -- roughly twenty per relisted row --
# so the saving is about eight seconds a row, a minute and a half on a ten-row
# cycle.
#
# The trade is real and this is the first knob to turn back if the symptoms
# return: a click that selects nothing, a Cancel or Register button that reads
# as missing because the panel is still animating, a quantity field that keeps
# its old value. Those are what the 0.5 was measured to avoid. Nothing here
# fails silently -- every one of them aborts the row with a reason -- so a bad
# value shows up as refusals rather than as wrong prices.
ACTION_COOLDOWN = 0.3  # after a move, a click or a key press

# Between keystrokes while entering a value. Lowered from 0.5 to 0.1 on
# 2026-08-07 with ACTION_COOLDOWN, at the operator's request.
#
# This is the riskier of the two -- type_number's docstring records that the
# field DROPS CHARACTERS when typed at machine speed, which is why it was
# paced at all. A price of 111,250,000 is nine keystrokes, so the saving is
# about 3.6 seconds per registration.
#
# What makes it survivable is that a dropped digit cannot become a wrong
# listing: the price is read back and the registration aborts with "price did
# not take" if it disagrees, and the quantity is cross-checked against the
# field before Register is pressed. So the failure mode is a refused row with a
# reason, not an item sold at a tenth of its price. If refusals with those two
# messages start appearing, this is the value to raise first.
# REVERTED to 0.5 on 2026-08-07, the same evening it was lowered to 0.1.
#
# It failed on the first live conversion: "typing 250 into a field that maxes
# at 53 -> expecting 53 ... but it reads 5". Two of the three keystrokes were
# lost. That is precisely what type_number's docstring says this value exists
# to prevent, and the measured-safe figure is 0.5.
#
# The guard behaved exactly as designed -- the field was read back, the
# mismatch was caught, and the conversion cancelled without spending the Sets
# -- so the cost was a lost round rather than a wrong purchase. But a value
# that aborts runs is not a saving: typing is a handful of numbers per row,
# while the abort cost the whole restock.
#
# ACTION_COOLDOWN stays at 0.1: every click, move and tab switch in that same
# run landed correctly. It is only the KEYSTROKE pacing this field cannot take.
TYPE_COOLDOWN = 0.3     # between keystrokes while entering a value

# Shortest turnaround between loop cycles, so `--every 0` cannot spin a core
# when a cycle happens to do no work at all.
MIN_CYCLE_SECONDS = 1.0

# A sold row must be collected before it can be relisted; the server needs a
# moment to settle before the table is worth re-reading.
RECEIVE_WAIT = 3.0

RELIST_ATTEMPTS = 3

# How many times a refused input is retried before it counts as blocked.
# SendInput returning 0 is usually transient (a hook, a momentary elevated
# foreground, a desktop switch). Treating one refusal as permanent ended an
# unattended run mid-cycle with no diagnosis; _release() already retried the
# identical call three times for a key-up, so this was an asymmetry, not a
# policy. Real UIPI blocking fails every attempt and still raises.
SEND_ATTEMPTS = 4

# How many "_2", "_3" suffixes a capture will try before giving up on finding
# an unused filename. See _free_path.
CAPTURE_SUFFIX_LIMIT = 100

# How many times a modifier key-up is re-sent before it is reported as stuck.
#
# The worst outcome in the file: a dropped Ctrl-up leaves every later click a
# Ctrl+Click, which is the item-MOVE gesture, and a dropped Alt-up leaves them
# Alt+Clicks. Retried rather than trusted, and the retry is cheap because it
# only ever runs when the first attempt already reported failure.
RELEASE_ATTEMPTS = 3

# How many times the NPC is looked for before the search is given up.
#
# She is found by OCR of her floating name, which the game animates and which
# other players walk in front of. A miss is usually transient.
NPC_FIND_RETRIES = 4

# How many dialogs deep close_any_dialog will go. A confirmation can be stacked
# on a confirmation, and each Escape reveals the next.
CLOSE_DIALOG_TRIES = 4

# How many times the inventory grid's anchor is re-measured before giving up.
# Found by the Alz box's COLOUR rather than by OCR, so a failure here usually
# means the panel is genuinely not open rather than that the read flaked.
INVENTORY_ORIGIN_RETRIES = 3

# Consecutive failed cycles before the loop gives up. Retrying only helps if
# something might change between attempts; a stranded item in the work tab, for
# instance, blocks every later cycle identically until a human clears it.
MAX_CONSECUTIVE_FAILURES = 3

# Every polling deadline in this file is measured around a Tesseract call, so
# without a timeout here a wedged tesseract.exe hangs the whole run.
TESSERACT_TIMEOUT = 30.0

# --------------------------------------------------------------------------
# GAME AND INVENTORY
# --------------------------------------------------------------------------

# A startup free-inventory-space check lived here and was removed on
# 2026-08-05.
#
# The intent was sound: the game refuses a cancellation outright when the
# returning stack will not fit -- a 250-item listing comes back as ~64 slots --
# and no amount of retrying clears it, so an hour can be spent failing the same
# row. But the implementation could not read the inventory reliably. From
# main() the panel was not open yet; moved into relist_rows after the shop was
# open, select_inventory_tab(1) still failed, though tab 4 reads fine for
# require_empty_work_tab.
#
# Two placements, two refusals of runs that were perfectly healthy. Removed
# rather than left half-working: a gate that stops good runs is worse than no
# gate, and this one stopped them at startup where it is most expensive.

GAME_TITLE_HINT = "PlayCabal"

# The tab cancelled items are expected to land in. It must be empty before a
# run: a large stack scatters across tabs, and pre-existing items there make it
# impossible to tell which slots the cancel actually filled.
# How long the Agent Shop may stay open before it is closed and reopened from
# the NPC. relist() used to close it after EVERY row, which cost a walk back to
# the NPC and a Register-tab open per listing.
#
# Measured over the 07:57 run of 2026-08-06 (1,093 recorded frames, 103.5 min,
# 44 relists): tab.register_open 21.6 min across 50 opens at 25.9s each, plus
# npc.found 6.5 min at 7.8s -- about 34s of pure overhead per row, a quarter of
# the whole run, spent re-entering a shop it had just left.
#
# The reopen was never what kept the table fresh. _relist_cycle calls
# refresh_table() on every attempt (that is what forces the client to REFETCH
# rather than re-read its own stale copy), and relist_rows carries a RowRef and
# re-locates the listing by identity before cancelling anything. The reopen sat
# on top of both. Retiring the session periodically keeps a bounded version of
# it -- a wedged or silently-closed window still gets rebuilt from the NPC --
# without paying for it 24 times a cycle.
SHOP_SESSION_SECONDS = 15 * 60
_shop_open_since: float | None = None

# --------------------------------------------------------------------------
# Saved searches on the Purchase tab ("Favorites")
# --------------------------------------------------------------------------
#
# Ten slots along the bottom of the Trade window. Clicking one runs its saved
# search immediately -- no typing, no autocomplete, no Search button.
#
# That last point is what makes them worth using. Typing a name leaves Search
# DISABLED until a suggestion is picked out of the autocomplete dropdown, and
# a failed search leaves the PREVIOUS results on screen, so "rows are present"
# proves nothing. A favourite is one click and cannot half-work.
#
# Positions are an exact arithmetic series -- 656 + 57n reproduces all ten
# measured centres to within 10px, and the ends (656 and 1169) land dead on.
# Held as a point plus a pitch so calibration scales them like every other
# coordinate; a hardcoded list would be wrong on any other resolution.
FAVOURITE_FIRST = (656, 1014)
FAVOURITE_PITCH = 57
FAVOURITE_COUNT = 10

# What each slot searches for, read off the live shop on 2026-08-07 by clicking
# every slot and recording the filters and the first result it returned.
#
# The layout is deliberate and pairs up: slot N is the item, slot N+1 is its
# SET version. Worth preserving if the favourites are ever re-saved, because
# the pairing is what lets a caller ask for "the Set of X" without a second
# lookup table.
#
# The spellings are the GAME's, transcribed exactly, including its own
# inconsistency about the space before the bracket: "Force Core(High)" has
# none, "Force Core (Ultimate)" does. Anything matching against these has to
# tolerate that -- item_price_floor's folding already does.
FAVOURITE_SLOTS: dict[int, str] = {
    1:  "Force Core(Highest)",
    2:  "Force Core Set (Highest)",
    3:  "Upgrade Core(Highest)",
    4:  "Upgrade Core Set (Highest)",
    5:  "Force Core (Ultimate)",
    6:  "Force Core Set (Ultimate)",
    7:  "Force Core(High)",
    8:  "Force Core Set (High)",
    9:  "Upgrade Core (Ultimate)",
    10: "Upgrade Core Set (Ultimate)",
}


def favourite_slot_point(slot: int) -> tuple[int, int]:
    """Screen position of favourite slot `slot` (1-based)."""
    if not 1 <= slot <= FAVOURITE_COUNT:
        raise ValueError(f"favourite slot {slot} is outside 1..{FAVOURITE_COUNT}")
    x, y = FAVOURITE_FIRST
    return (x + (slot - 1) * FAVOURITE_PITCH, y)


def favourite_for(item: str) -> int | None:
    """The slot that searches for `item`, or None.

    Matched on the same folded key the price floors use, so the game's own
    spacing inconsistency around the bracket cannot cause a miss.

    The pack marker is stripped FIRST. Table names carry it -- a listed row
    reads "Force Core (Ultimate) X 250", not "Force Core (Ultimate)" -- and
    this lookup is exact equality, so without the strip a listed row resolves
    to no slot at all. That silently deleted the never-below-cost floor on the
    only path that repeats: set_behind() returned "", purchase_cost_basis()
    returned 0, and listing_floor() fell back to the catalogue, which is 0 for
    a Core. The floor worked on the fresh listing (clean name from the vendor)
    and vanished on every relist after it -- so the holding could be walked
    below cost 5% at a time, which is exactly what the floor exists to stop.
    item_price_floor survives the marker because it matches fuzzily; this one
    does not, and every other name comparison in this file strips it first.
    """
    want = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", item)))
    if not want:
        return None
    for slot, name in FAVOURITE_SLOTS.items():
        if _floor_key(item_name(name)) == want:
            return slot
    return None


def favourite_set_slot(slot: int) -> int | None:
    """The slot holding the SET version of the item in `slot`, or None.

    The favourites are saved in pairs -- item, then its Set -- so this is the
    next slot along, confirmed rather than assumed.
    """
    partner = slot + 1
    name = FAVOURITE_SLOTS.get(slot, "")
    other = FAVOURITE_SLOTS.get(partner, "")
    if name and other and "set" in _floor_key(other) and \
            _floor_key(other).replace("set", "") == _floor_key(name):
        return partner
    return None


# --------------------------------------------------------------------------
# Converting Sets into Cores at the NPC vendor
# --------------------------------------------------------------------------
#
# This is the other half of the arbitrage. A Set converts to a Core ONE FOR
# ONE at the vendor, and on the Agent Shop a Set is reliably cheaper per item
# than the Core it becomes -- 187,278 against 209,800 on 2026-08-07. Buy Sets,
# convert, hold Cores.
#
# The vendor's Shop window carries a 4x5 block of exchange entries in its lower
# right. Read off the live window by hovering every cell:
#
#     row 1   Force Core SET     (paid for with Force Cores)      CORE -> SET
#     row 2   Force Core         (paid for with Force Core Sets)  SET -> CORE
#     row 3   Upgrade Core SET   (paid for with Upgrade Cores)    CORE -> SET
#     row 4   Upgrade Core       (paid for with Upgrade Core Sets) SET -> CORE
#
# Only rows 2 and 4 are the direction we want. Rows 1 and 3 are the same trade
# run backwards and would undo the profit, so they are recorded here precisely
# so nothing clicks them by accident.
#
# Columns are the grade, left to right. Confirmed individually: c1 "Upgrade
# Core(Low)" paying "Upgrade Core Set (Low) 13 / 1", c2 "(Medium) 7 / 1", c3
# "(High) 7 / 1", c4 "(Highest) 7 / 1", c5 Ultimate. The "13 / 1" is HOW MANY
# ARE HELD over the cost -- the cost is always 1, which is what makes it a
# one-for-one exchange.
#
# Clicking these is not like the Agent Shop:
#     plain click   Immediate Purchase -- buys ONE, at once
#     Alt + click   Mass Purchase, which asks for a quantity
#     Ctrl + click  links the item into chat
# So a stray click here spends something immediately. Nothing may click this
# grid without knowing exactly which cell it is on.
CONVERT_COLS = (252, 317, 381, 448, 512)      # Low, Medium, High, Highest, Ultimate
CONVERT_ROWS = (1066, 1133, 1197, 1258)       # set / core / set / core
CONVERT_GRADES = ("Low", "Medium", "High", "Highest", "Ultimate")
# The maximum a single Agent Shop row can hold, so it is the most worth
# converting in one go.
CONVERT_QUANTITY = 250

# (row, col) -> (what the cell gives you, what it costs). Rows 2 and 4 only:
# the SET -> CORE direction.
CONVERT_TO_CORE = {
    (2, i + 1): (f"Force Core({g})" if g != "Ultimate" else "Force Core (Ultimate)",
                 f"Force Core Set ({g})")
    for i, g in enumerate(CONVERT_GRADES)
}
CONVERT_TO_CORE.update({
    (4, i + 1): (f"Upgrade Core({g})" if g != "Ultimate"
                 else "Upgrade Core (Ultimate)",
                 f"Upgrade Core Set ({g})")
    for i, g in enumerate(CONVERT_GRADES)
})
# The reverse direction, recorded so it is never clicked by mistake.
CONVERT_TO_SET = {(1, i + 1) for i in range(5)} | {(3, i + 1) for i in range(5)}


def convert_cell_point(row: int, col: int) -> tuple[int, int]:
    """Screen position of a conversion cell (1-based row and column)."""
    if not 1 <= row <= len(CONVERT_ROWS) or not 1 <= col <= len(CONVERT_COLS):
        raise ValueError(f"conversion cell ({row},{col}) is off the grid")
    return (CONVERT_COLS[col - 1], CONVERT_ROWS[row - 1])


# The Shop window's title and its two tabs. Measured on a live capture: "Shop"
# at y~163, the "Normal"/"Repurchase" tabs at y~204, all three reading at 96%+.
# The first attempt at this stopped at y=200 and clipped the tabs off entirely,
# so the check found nothing on a frame where the window was plainly open.
SHOP_WINDOW_TITLE = (0, 150, 580, 240)
CONVERT_TIP_REGION = (0, 740, 900, 1400)     # where the item tooltip renders


def vendor_shop_open(source: "Image.Image | None" = None) -> bool:
    """True when the NPC vendor's Shop window is up, on the Normal tab.

    Distinct from the Agent Shop entirely. This is the window whose lower-right
    block exchanges Sets for Cores, and a plain click in it is an IMMEDIATE
    purchase with no confirmation -- so nothing may click here on the strength
    of coordinates alone.
    """
    shot = source if source is not None else grab()
    words = {w.text.casefold()
             for w in find_words(shot, SHOP_WINDOW_TITLE, 25) if w.conf >= 45}
    # All three, not any one. "Shop" alone appears in other windows; the
    # Normal/Repurchase pair is what makes this the NPC vendor rather than the
    # Agent Shop, and the vendor is the only window where a click spends
    # something without asking.
    return {"shop", "normal", "repurchase"} <= words


# The vendor tooltip does not appear all at once. Measured on a live hover: the
# item name and the "Price" LABEL draw first, and the price VALUE line arrives a
# beat later -- so a frame grabbed at 0.95s reads a tooltip that is plainly up
# and plainly missing the one line the check depends on. Four retries at 0.95s
# each all caught the same half-drawn state, because retrying at a delay that is
# too short just reproduces it.
#
# So the wait grows with each try instead of repeating. The same hover read
# cleanly at 1.2s.
CONVERT_TIP_SETTLES = (1.3, 1.8, 2.3, 3.0)


def _warm_text_image(image: "Image.Image",
                     region: tuple[int, int, int, int]) -> "Image.Image":
    """Just the RED and ORANGE text in `region`, bright-on-dark for OCR.

    Greyscale throws warm text away. Two measured cases, both of which refused
    a conversion that was perfectly valid:

      * an unaffordable price is drawn RED. "Force Core Set (High) 0 / 1" came
        back from OCR as 'ee', where the identical line in white a minute
        earlier read at 96%. Pure red has a luminance around 54 against a panel
        around 30, so autocontrast has almost nothing to stretch.
      * an item tooltip's TITLE is drawn ORANGE over a translucent panel with
        the game world showing through. "Force Core Set (High)" read as
        "Force Core" -- the grade, which is the one part that must not be lost,
        sat over the brightest branches.

    Subtracting the brighter of green and blue from red leaves both standing
    and flattens everything else: white and grey text have all three channels
    roughly equal and cancel to nothing.

    The 0-held case matters especially, because 0 is not an error, it is the
    ANSWER -- "there is nothing left to convert". Losing it turns a clean
    finish into "could not read the tooltip", which reads like a fault and
    hides a completed job.
    """
    r, g, b = image.crop(region).convert("RGB").split()
    return ImageChops.subtract(r, ImageChops.lighter(g, b))


def _price_from_lines(text: list[str]) -> tuple[str, int | None, int | None]:
    """The payment line and its held/cost figures, from tooltip lines."""
    for i, line in enumerate(text):
        if line.casefold().startswith("price") and i + 1 < len(text):
            price_line = text[i + 1]
            found = re.search(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)", price_line)
            if found:
                return (price_line,
                        int(found.group(1).replace(",", "")),
                        int(found.group(2).replace(",", "")))
            return price_line, None, None
    return "", None, None


def _tooltip_lines(shot: "Image.Image",
                   region: tuple[int, int, int, int]) -> list[str]:
    """Readable lines in `region`, top to bottom, each left to right."""
    words = [w for w in find_words(shot, region, 25) if w.conf >= 40]
    lines: dict = {}
    for w in words:
        lines.setdefault(round(w.centre[1] / 16), []).append(w)
    out = []
    for key in sorted(lines):
        joined = " ".join(w.text for w in sorted(lines[key],
                                                 key=lambda w: w.centre[0]))
        if joined.strip():
            out.append(joined.strip())
    return out


def hover_tooltip(x: int, y: int, settle: float | None = None,
                  attempts: int | None = None,
                  need_price: bool = True,
                  region: tuple[int, int, int, int] | None = None) -> dict:
    """Hover a conversion cell and read what it actually offers.

    Hovering is the whole safety story here. The Agent Shop asks before it
    takes money; this vendor does not, so the cell has to be identified BEFORE
    it is clicked rather than after. Returns what the tooltip says, and the
    caller compares that against what it meant to buy.

    'held' is the first half of the "13 / 1" on the price line -- how many of
    the paying item are in the inventory. 'cost' is the second, and it is 1 for
    every entry in this grid, which is what makes the exchange one-for-one.
    """
    focus_game()
    look = CONVERT_TIP_REGION if region is None else region

    waits = list(CONVERT_TIP_SETTLES) if settle is None else [settle]
    if attempts is not None:
        waits = (waits * attempts)[:max(1, attempts)]

    def attempt(wait: float) -> dict:
        # Approach from elsewhere: a move to the pixel the cursor already
        # occupies raises no event, and the tooltip never appears.
        move_mouse(x - 140, y - 140)
        time.sleep(0.18)
        move_mouse(x, y)
        time.sleep(wait)
        shot = grab()
        text = _tooltip_lines(shot, look)
        price_line, held, cost = _price_from_lines(text)

        # A second pass over the warm-coloured text, which greyscale loses. It
        # runs whenever the first pass could not answer the question being
        # asked: a missing price (drawn red when unaffordable), or an item
        # title (drawn orange) that the caller needs by name. Its coordinates
        # come back relative to the crop, which does not matter -- only the
        # text is wanted.
        if held is None or not need_price:
            warm = _warm_text_image(shot, look)
            warm_text = _tooltip_lines(warm, (0, 0, warm.width, warm.height))
            for line in warm_text:
                if line not in text:
                    text.append(line)
            if held is None:
                w_line, w_held, w_cost = _price_from_lines(warm_text)
                if w_held is not None:
                    price_line, held, cost = w_line, w_held, w_cost

        return {"lines": text, "price_line": price_line,
                "held": held, "cost": cost, "point": (x, y)}

    # Retry a miss on a fresh hover, waiting longer each time. A tooltip is not
    # a transient thing: while the cursor sits still the game keeps drawing it,
    # so "no price line" means the hover did not register or the frame was
    # caught mid-draw, not that the cell has nothing to say.
    #
    # Two real misses on one live run, both of which refused a conversion that
    # was perfectly valid:
    #   12:42  ['?', 'w.', '4 Zone']            -- no tooltip at all, world text
    #   12:47  [..., 'Force Core(High)', 'Price'] -- up, but half drawn
    # The second is why the waits escalate: four tries at the same 0.95s
    # reproduced the identical half-drawn frame four times.
    #
    # The same reasoning await_dialog_button already applies to dialogs, and
    # the asymmetry is the same: a retry costs a second, while giving up
    # strands a conversion the caller has to notice and restart by hand.
    def good(tip: dict) -> bool:
        # An inventory slot has no price line at all, so what counts as a
        # successful read depends on what is being hovered.
        if need_price:
            return bool(tip["price_line"]) and tip["held"] is not None
        return bool(tip["lines"])

    best = attempt(waits[0])
    for wait in waits[1:]:
        if good(best):
            break
        # Park between tries so the next move genuinely re-enters the cell.
        move_mouse(x - 300, y - 300)
        time.sleep(0.25)
        best = attempt(wait)
    return best


def read_convert_tooltip(row: int, col: int, settle: float | None = None,
                         attempts: int | None = None) -> dict:
    """Hover a conversion cell and read what it actually offers."""
    return hover_tooltip(*convert_cell_point(row, col),
                         settle=settle, attempts=attempts, need_price=True)


def convert_cell_matches(row: int, col: int, tip: dict) -> bool:
    """Does the tooltip describe the cell we think we are on?

    Both halves are checked. The name alone would pass on the CORE->SET cell
    directly above, which names the same item and is the losing side of the
    trade; the price line is what tells the two apart.
    """
    gives, costs = CONVERT_TO_CORE.get((row, col), ("", ""))
    if not gives:
        return False
    named = any(_names_agree(line, gives) for line in tip.get("lines", []))
    return named and _names_agree(tip.get("price_line", ""), costs)


def _convert_name_key(text: str) -> str:
    """An item name from the vendor UI, reduced to a comparable key.

    Removes the trailing "held / cost" figures the vendor appends to a payment
    line, and any decoration before the name, then folds the rest the way the
    floor lookup does.

    The leading strip is not cosmetic. _floor_key folds OCR lookalikes, and it
    maps '[' onto 'i' -- so a stray bracket picked up beside the text becomes a
    LETTER rather than vanishing, and '[ Force Core Set (High)' keys as
    'iforcecoresethigh'. Measured on the red-text pass, where the panel border
    reads as a bracket: the name was perfect and the comparison still failed.
    """
    cleaned = re.sub(r"\d[\d,]*\s*/\s*\d[\d,]*\s*$", "", text.strip())
    cleaned = re.sub(r"^[^A-Za-z]+", "", cleaned)
    return _floor_key(item_name(cleaned))


def _names_agree(observed: str, expected: str) -> bool:
    """Is `observed` the same item as `expected`? EQUALITY, never containment.

    "Force Core(Highest)" CONTAINS "Force Core(High)", so a containment test
    accepts a Highest dialog for a High request and converts the dearer item.
    The identical trap in the price-floor lookup once had a 24,000,000 item
    inheriting a 44,000,000 floor: the grades in this game are prefixes of one
    another by design -- Low, Medium, High, Highest, Ultimate -- so nothing
    that tells them apart may match on a substring.

    The cost of being strict is a refusal on a damaged read, which is the
    direction this file always chooses. A refusal wastes a cycle; a wrong match
    spends the wrong items, and there is no confirmation step to catch it.
    """
    return bool(observed) and _convert_name_key(observed) == _floor_key(
        item_name(expected))


def convert_cell_for(core_name: str) -> "tuple[int, int] | None":
    """The cell that turns Sets INTO `core_name`, or None.

    Only ever returns a SET -> CORE cell. Asking for a Set by name returns
    None rather than the cell that would make one, because converting the
    other way is the losing side of the same trade.
    """
    want = _floor_key(item_name(core_name))
    for (row, col), (gives, _costs) in CONVERT_TO_CORE.items():
        if _floor_key(item_name(gives)) == want:
            return (row, col)
    return None


# --------------------------------------------------------------------------
# The inventory panel, as a witness for the conversion
# --------------------------------------------------------------------------
#
# The tooltip says how many Sets are held; the inventory shows where they went.
# Those are independent, and the second one survives what the first does not --
# the vendor draws an unaffordable price in RED, which greyscale nearly erases,
# so the tooltip goes quiet at exactly the moment a conversion finishes. Pixels
# in a slot do not care what colour the text was.
#
# The convention is the operator's: the work tab holds the Sets, the stack sits
# in the first slot, and the Cores land in the second. So a conversion that
# worked leaves slot (1,2) occupied when it started empty -- a yes/no that owes
# nothing to OCR.
#
# The geometry deliberately lives elsewhere. inventory_origin() anchors the
# panel by the Alz box's COLOUR, with the ornate title only as a fallback, and
# slot_centre_at/tab_centre/occupied_slots/active_inventory_tab all hang off
# that anchor. An earlier draft of this section measured absolute coordinates
# off a screenshot instead, which was worse in the way that matters: it would
# have gone on clicking confidently after the window moved, and it silently
# redefined INVENTORY_TITLE_REGION out from under the layout scaler.
# NOTE: this is the same tab as WORK_TAB, which is defined further down and
# which require_empty_work_tab() insists is EMPTY before a relist run starts.
# The two uses do not overlap in time -- converting is a manual operation and
# relisting is the unattended loop -- but tab 4 must be cleared out before a
# relist run, or that check will refuse to start. Spelled as a literal rather
# than as WORK_TAB only because WORK_TAB is declared later in the file; the
# test suite asserts the two agree so they cannot drift apart silently.
CONVERT_INVENTORY_TAB = 4
CONVERT_SET_SLOT = (1, 1)
CONVERT_CORE_SLOT = (1, 2)


def slot_tip_region(x: int, y: int) -> tuple[int, int, int, int]:
    """Where an inventory slot's tooltip renders, relative to the slot.

    LEFT of the panel and slightly above the cursor -- nowhere near
    CONVERT_TIP_REGION, which covers the shop grid's tooltips in the opposite
    corner of the screen. Reading a slot through the shop's region returned
    fragments of the game world and refused a conversion whose Sets were
    plainly sitting in the slot.

    Derived from the hover point rather than fixed, so it follows whichever
    slot is being read. Measured on a live hover of slot (1,1): the tooltip
    occupied x 1538-1883, y 301-490 for a slot centred at (1981, 293).
    """
    return (max(0, x - 560), max(0, y - 70), max(1, x - 20), y + 430)


def read_slot_tooltip(row: int, col: int, settle: float | None = None) -> dict:
    """Hover an inventory slot and read what is in it.

    Identity, not just occupancy. occupied_slots() answers "is something
    there", which cannot tell a Set from anything else that happens to sit in
    that slot -- and running against the wrong tab would put SOMETHING in slot
    (1,1) every time. The name is what makes the check mean anything.
    """
    x, y = slot_centre(row, col)
    return hover_tooltip(x, y, settle=settle, need_price=False,
                         region=slot_tip_region(x, y))


# The Purchase Item dialog that Alt+click opens. Measured off a live capture at
# 2560x1440 on 2026-08-07:
#
#     +-------------- Purchase Item --------------+
#     |  Force Core(High)                         |   <- what you GET
#     |  Purchase QTY   [   1   ] / 55   [^v]     |   <- typed / maximum
#     |  Purchase Price  Force Core Set (High) 55/1|  <- what you PAY, held/cost
#     |                        [ OK ]  [ Cancel ] |
#     +-------------------------------------------+
#
# This dialog is worth far more than the tooltip that preceded it. It names the
# Core it will hand over AND the Set it will take, in the same "held / cost"
# form, and it does so AFTER the click has selected a cell but BEFORE anything
# is spent. That makes it the last and best place to check that the right cell
# was hit -- so the verification lives here rather than resting on the hover.
#
# The maximum beside the QTY field is how many of the paying Set are held, so
# it is also the answer to "how many can this convert", read from the game
# rather than assumed.
CONVERT_DIALOG_REGION = (975, 470, 1570, 945)
CONVERT_DLG_ITEM = (1000, 538, 1420, 576)
CONVERT_DLG_PRICE = (1160, 650, 1520, 692)
# The QTY field is split deliberately. Reading the whole field would take in
# the "/ 55" maximum, and then a quantity that never landed reads as a success
# whenever the typed value happens to equal the maximum -- which is the common
# case here, since 250 clamps to the maximum nearly every time. The typed value
# has to be read on its own for the check to mean anything.
CONVERT_DLG_QTY_VALUE = (1163, 592, 1252, 630)
CONVERT_DLG_QTY_MAX = (1268, 594, 1338, 628)
# Right of the "/", left of the spinner arrows: including the spinner turned
# "55" into "554" on a real frame.
# The buttons get their OWN crop. Tesseract's segmentation is crop-dependent,
# and the whole-dialog region reads the item, the QTY row and the price line
# and then simply stops before the buttons -- on one live frame it produced
# "... Force Core Set (High) 12/1" and found neither OK nor Cancel, while a
# whole-screen search found both at the coordinates the region covers.
#
# That made mass_purchase_open() report NO DIALOG with the dialog plainly up,
# so a perfectly good conversion aborted with "the Purchase Item dialog did not
# appear after Alt+click" -- twice, on two separate live runs.
#
# Verified on both the frame that failed and the original golden capture.
CONVERT_DIALOG_BUTTONS = (1150, 880, 1560, 940)
CONVERT_CONFIRM_WORD = "OK"
CONVERT_CANCEL_WORD = "Cancel"
CONVERT_DIALOG_TIMEOUT = 8.0


def mass_purchase_open(
    source: "Image.Image | None" = None,
) -> "tuple[Word, Word] | None":
    """(OK, Cancel) if the Purchase Item dialog is up, else None.

    Both buttons are required, and so is the "Purchase QTY" label. "OK" is two
    characters and turns up in ordinary UI text all over this game; a lone
    confirm word is not evidence of a dialog, and a false positive here clicks
    a coordinate inside a vendor window -- the one place in this file where a
    stray click spends something with no confirmation at all.

    The title is deliberately NOT what identifies it. It reads as
    "Purchaseltem" on a clean frame (ornate glyphs, capital-I as lowercase-l),
    and dialog_kind's docstring already records what happens when a modal is
    identified by a title this UI renders badly.
    """
    shot = source if source is not None else grab()
    ok = find_text(shot, CONVERT_CONFIRM_WORD, CONVERT_DIALOG_BUTTONS, 40.0)
    cancel = find_text(shot, CONVERT_CANCEL_WORD, CONVERT_DIALOG_BUTTONS, 40.0)
    qty = find_text(shot, "QTY", CONVERT_DIALOG_REGION, 40.0)
    if not ok or not cancel or not qty:
        return None
    return ok[-1], cancel[-1]


def mass_purchase_details(source: "Image.Image | None" = None) -> dict:
    """What the Purchase Item dialog says it is about to do.

    Returns the item it gives, the payment line, the typed quantity and the
    maximum. Every one of these is read from the dialog itself, so a caller can
    confirm the trade against what it intended without trusting the click that
    opened it.
    """
    shot = source if source is not None else grab()

    def line(region: tuple[int, int, int, int]) -> str:
        words = [w for w in find_words(shot, region, 25) if w.conf >= 30]
        return " ".join(w.text for w in sorted(words, key=lambda w: w.centre[0]))

    price_line = line(CONVERT_DLG_PRICE)
    held = cost = None
    match = re.search(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)", price_line)
    if match:
        held = int(match.group(1).replace(",", ""))
        cost = int(match.group(2).replace(",", ""))
    return {
        "item": line(CONVERT_DLG_ITEM),
        "price_line": price_line,
        "held": held,
        "cost": cost,
        "qty": read_number(shot, CONVERT_DLG_QTY_VALUE),
        "qty_max": read_number(shot, CONVERT_DLG_QTY_MAX),
    }


def mass_purchase_matches(row: int, col: int, detail: dict) -> bool:
    """Does the open dialog describe the cell we meant to click?

    Both halves again: the Core given and the Set taken. The CORE -> SET cell
    directly above names the same item, and only the payment line separates
    them.
    """
    gives, costs = CONVERT_TO_CORE.get((row, col), ("", ""))
    if not gives:
        return False
    # Equality on both, for the reason _names_agree spells out: the Highest
    # dialog contains the High name, and containment would convert the wrong
    # grade with nothing on screen to say so.
    return (_names_agree(detail.get("item", ""), gives)
            and _names_agree(detail.get("price_line", ""), costs))


def core_slot_candidates(before: set, after: set) -> list:
    """Slots on the work tab that may now hold the converted Cores, best first.

    Taken from the tab's occupancy before and after the conversion, because a
    Core cannot be told from a Set by looking at pixels -- but the SPACES can,
    and they are enough:

      1. occupied now, empty before. Nothing else was added during the
         conversion, so whatever is there is a Core. This is the normal case.

      2. occupied both before and after. Ordinarily this is a Set stack that
         merely shrank -- but it is ALSO what a fully consumed stack looks like
         once a Core drops into the slot it just freed.

    The second case is why a plain set difference is not enough. Sets stack to
    999, so a 1,250-Set purchase occupies TWO slots -- (1,1) with 999 and (1,2)
    with 251 -- and Cores start landing at (1,3). Convert the 999 and slot
    (1,1) empties and refills with a Core in the same breath: it is in both
    readings, so the difference misses it entirely. With the first stack gone,
    the next conversion's Cores can land in (1,1) with nothing new appearing at
    all, and the difference comes back EMPTY for a conversion that worked.

    Ordered rather than filtered: the caller loads a candidate and
    register_item refuses it if the name is wrong, so a wrong guess costs a
    retry rather than a mislisting.
    """
    fresh = sorted(after - before)
    persisted = sorted(after & before)
    return fresh + persisted


def convert_cores(core_name: str, quantity: int = CONVERT_QUANTITY,
                  verbose: bool = True, execute: bool = True,
                  require_layout: bool = True) -> dict:
    """Exchange Sets for Cores at the vendor, in one Mass Purchase.

    The trade is one-for-one and the vendor charges no Alz, so this is pure
    upside on top of the Agent Shop spread: a Set bought at 187,278 becomes a
    Core worth 209,800. `quantity` is typed as-is and the game clamps it to
    what is actually held, which is why CONVERT_QUANTITY is simply 250 -- the
    most a shop row can hold -- rather than something read off the panel first.

    Every step is verified before the next one commits, in this order:

      1. the vendor's Shop window is open, on the Normal tab
      2. the name resolves to a SET -> CORE cell (a Set name resolves to
         nothing, so the reverse trade cannot be reached through this function)
      3. the Inventory is on the work tab, the Sets are in slot (1,1), and the
         landing slot (1,2) is EMPTY -- an occupied one would make the count at
         the end read as success whatever happened
      4. the Shop window is still open, re-checked at the instant of the click
      5. Alt+click the cell, and the Purchase Item dialog must appear
      6. the dialog must name the Core being given AND the Set being taken --
         by EQUALITY, since "Force Core(Highest)" contains "Force Core(High)"
      7. the typed quantity must read back from the field as the clamped value
      8. the Shop window is checked once more, then OK
      9. the newly filled inventory slots are counted

    Steps 6, 7 and 9 are the ones that matter. The grid's coordinates are fixed
    furniture and are trusted as such; what is never trusted is that a click
    landed where it was aimed, which is why the dialog is read after the click
    and before the money moves.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise Aborted(reason)

    cell = convert_cell_for(core_name)
    require(cell is not None,
            f"{core_name!r} is not something the vendor converts Sets into. "
            "Set names resolve to nothing on purpose: converting Cores into "
            "Sets is the losing side of this trade.")
    row, col = cell
    require((row, col) not in CONVERT_TO_SET,
            f"cell r{row}c{col} is a CORE -> SET cell and must never be clicked")
    gives, costs = CONVERT_TO_CORE[(row, col)]

    require(focus_game(), "could not bring Cabal to the foreground")
    require(vendor_shop_open(),
            "the vendor's Shop window is not open on the Normal tab. This "
            "function clicks inside a shop, where a click spends something "
            "with no confirmation, so it will not act on an unrecognised "
            "screen.")

    say(f"Converting Sets -> {gives} (r{row}c{col}, paying with {costs})")

    # A Purchase Item dialog left over from a previous attempt -- or from a
    # human hand -- covers part of the window and would swallow the hover the
    # next step depends on. Clear it before starting rather than reading a
    # tooltip that cannot appear.
    stale = mass_purchase_open()
    if stale is not None:
        say("  a Purchase Item dialog was already open; cancelling it first")
        click(*stale[1].centre)
        time.sleep(0.5)
        if mass_purchase_open() is not None:
            press_escape()
            time.sleep(0.3)
        require(mass_purchase_open() is None,
                "a Purchase Item dialog is open and will not close. Refusing "
                "to click the grid underneath an unrecognised modal.")

    # ---- the inventory, checked before anything is spent -------------------
    # Tab 4 holds the Sets, slot (1,1) is the stack, slot (1,2) is where the
    # Cores arrive. Verified here so the after-check has a baseline, and so a
    # conversion never runs against a tab that is showing something else.
    origin = inventory_origin()
    require(origin is not None,
            "the Inventory panel is not open, so there is no way to see where "
            "the Cores end up. Open it before converting.")
    require(select_inventory_tab(CONVERT_INVENTORY_TAB, origin),
            f"could not put the Inventory on tab {CONVERT_INVENTORY_TAB}. The "
            "slot checks below only mean anything on the tab that holds the "
            "Sets, so there is nothing safe to do without it.")

    filled = set(occupied_slots(grab(), origin))
    if require_layout:
        # The hand-driven convention: Sets parked in the first slot, Cores
        # landing in the second. Checked because it is what the operator set
        # up, and a tab that does not look like it is a tab we do not
        # understand.
        require(CONVERT_SET_SLOT in filled,
                f"inventory slot {CONVERT_SET_SLOT} looks empty")
        require(CONVERT_CORE_SLOT not in filled,
                f"inventory slot {CONVERT_CORE_SLOT} is already occupied. It "
                "is the landing slot for the converted Cores, and an occupied "
                "one makes the after-check meaningless -- it would read as "
                "success whatever happened. Clear it first.")
        say(f"  inventory tab {CONVERT_INVENTORY_TAB}: {CONVERT_SET_SLOT} "
            f"holds {costs}, {CONVERT_CORE_SLOT} is clear")
    else:
        # Driven by the restock pipeline, where Sets land wherever the game put
        # them and the tab fills and empties as batches cycle through. The
        # layout cannot be asserted, but the MEASUREMENT still holds: whatever
        # is occupied now is the baseline, and anything new afterwards arrived
        # from this conversion.
        free = GRID_SIZE * GRID_SIZE - len(filled)
        # A FULL work tab is not a reason to refuse. The game fills the first
        # free slot in the whole inventory, so Cores simply land on a later tab
        # -- and list_cores looks there when the work tab yields nothing.
        #
        # It does cost the count its meaning: with nothing landing here,
        # `converted` reads 0 for a conversion that worked. Said out loud, and
        # progress is measured by what was LISTED anyway.
        if free <= 0:
            say(f"  inventory tab {CONVERT_INVENTORY_TAB} is full; the Cores "
                "will land on a later tab and this tab's count will read 0. "
                "The listing quantity is what counts them.")
        else:
            say(f"  inventory tab {CONVERT_INVENTORY_TAB}: {len(filled)} "
                f"slot(s) used, {free} free")

    # No pre-click reading. The grid is fixed furniture -- every cell has been
    # where the map says it is on every frame captured -- and the tooltip that
    # used to be consulted here was the least reliable thing in the sequence:
    # it draws its name and price label before its price VALUE, renders the
    # name in orange over moving game art, and turns the price red once the
    # last Set is spent. It refused three consecutive valid conversions, and
    # each refusal cost four OCR passes and ten seconds.
    #
    # What replaces it is not nothing. The Purchase Item dialog names both the
    # Core it will hand over and the Set it will take, and it does so AFTER the
    # click that selects a cell but BEFORE anything is spent -- which is a
    # strictly better place to check, because it describes the trade the game
    # is actually about to make rather than the one under the cursor.
    if not execute:
        return {"cell": (row, col), "gives": gives, "costs": costs,
                "would_convert": quantity, "converted": 0}

    # Re-checked HERE, immediately before the click, not only at the top of the
    # function -- a check is only true at the instant it is taken, and the
    # inventory work above takes seconds. The Shop window can be closed by
    # hand, by a death, or by an Escape meant for something else, and the click
    # about to be sent goes to whatever is underneath.
    #
    # This is the lesson purchase_ready() already encodes for the Purchase tab,
    # learned when a capture script guarded once and then clicked 80 times,
    # walking the character across the map.
    require(vendor_shop_open(),
            "the vendor's Shop window closed before the click. Nothing was "
            "clicked.")
    # And on the right PAGE. The grid coordinates describe the Dungeon tab; on
    # any other tab they point at whatever that page happens to show, and this
    # is a window where a plain click buys outright.
    showing = active_vendor_tab()
    if showing != CONVERT_VENDOR_TAB:
        # One re-read before refusing. active_vendor_tab returns None unless a
        # tab leads the median by VENDOR_TAB_MARGIN, and open_npc_shop had just
        # confirmed this tab moments earlier -- so a single ambiguous frame is
        # far likelier than the page having changed underneath us.
        #
        # Measured 2026-08-08 12:04: "Dungeon tab is showing" followed
        # immediately by "a tab I cannot identify", throwing away a conversion
        # of 270 Sets the run had already paid for.
        #
        # Still refuses if the second read disagrees too. This is the gate on a
        # window where a plain click buys outright, so the cost of being wrong
        # is not symmetric -- one retry, then stop.
        time.sleep(0.5)
        again = active_vendor_tab()
        if again == CONVERT_VENDOR_TAB:
            say(f"  the vendor tab read as {showing or 'unidentifiable'} and "
                f"then as {again!r} - taking the second reading.")
            showing = again
    require(showing == CONVERT_VENDOR_TAB,
            f"the vendor Shop is showing {showing or 'a tab I cannot identify'}"
            f", not {CONVERT_VENDOR_TAB}. The conversion grid is only on that "
            "tab; these coordinates mean something else here.")

    x, y = convert_cell_point(row, col)
    alt_click(x, y)

    deadline = time.monotonic() + CONVERT_DIALOG_TIMEOUT
    buttons = None
    while buttons is None and time.monotonic() < deadline:
        buttons = mass_purchase_open()
        if buttons is None:
            time.sleep(0.4)
    if buttons is None:
        # Nothing to cancel as far as we can tell, but Escape is free and the
        # alternative is leaving an unrecognised modal over the shop.
        press_escape()
        raise Aborted(
            "the Purchase Item dialog did not appear after Alt+click. Nothing "
            "was confirmed. If Alt+click is not the gesture on this client, "
            "the plain click is an IMMEDIATE purchase and must NOT be "
            "substituted for it.")
    confirm, cancel = buttons

    def bail(reason: str) -> None:
        """Cancel the dialog, then abort. Never leaves the modal up."""
        try:
            click(*cancel.centre)
            time.sleep(0.4)
            if mass_purchase_open() is not None:
                press_escape()
        except Exception:  # noqa: BLE001 - the abort reason matters more
            press_escape()
        park_cursor()
        raise Aborted(reason)

    # ---- the dialog's own account of the trade, before anything is spent ----
    detail = mass_purchase_details()
    if not mass_purchase_matches(row, col, detail):
        bail(f"the Purchase Item dialog is not the trade that was intended. "
             f"Expected {gives} paid for with {costs}; the dialog says "
             f"{detail.get('item')!r} paid for with "
             f"{detail.get('price_line')!r}. Cancelled without buying.")
    if detail.get("cost") not in (None, 1):
        bail(f"the dialog prices this at {detail['cost']} per conversion, not "
             "1. Every cell in this grid is a one-for-one exchange, so a "
             "different cost means it is not the trade it was mapped as.")

    limit = detail.get("qty_max")
    if limit is None:
        bail("could not read the QTY maximum from the dialog, so there is no "
             "way to know what the typed quantity will clamp to. Cancelled.")
    # The dialog's maximum IS the held count -- it is how many of the paying
    # Set the game will let you spend -- so this doubles as the "is there
    # anything to convert" check that used to come from the tooltip.
    # bail(), not require(). Everything from here on runs with the dialog OPEN,
    # and require() raises straight past the cancel -- leaving a modal over the
    # shop for the next caller to trip on. That is the exact failure that
    # turned one bad row into two dead cycles on 2026-08-04.
    if limit <= 0:
        bail(f"the dialog offers a maximum of {limit} - no {costs} to convert")

    expected = min(quantity, limit)
    # Cores do not stack, so each one needs a slot of its own -- 250 of them
    # need 250 free slots, and one tab holds 63 beyond the Set stack. The
    # overflow is not lost; it lands on a later tab. But it lands where the
    # count at the end cannot see it, so what is COUNTABLE here is capped at
    # the free space on this tab, and the run says so rather than reporting a
    # shortfall it caused itself.
    free = GRID_SIZE * GRID_SIZE - len(filled)
    countable = min(expected, free)
    if free <= 0:
        # Nothing can be counted here, so do not claim a shortfall that is only
        # a blind spot: the verdict comes from the listing instead.
        countable = 0
    # The Mass Purchase dialog as it OPENS, before anything is typed. This is
    # the frame that proves what the vendor was offering and at what limit --
    # the one thing no log line can reconstruct after the fact.
    record("convert.dialog", item=core_name, cell=f"r{row}c{col}",
           limit=limit, asked=quantity, expected=expected, free=free)

    say(f"  typing {quantity} into a field that maxes at {limit} "
        f"-> expecting {expected}")
    if expected > free:
        say(f"  note: only {free} free slot(s) on tab {CONVERT_INVENTORY_TAB}; "
            f"Cores beyond that land on a later tab and are not counted here")

    # Click the field before typing. The default value looks focused, but
    # "looks focused" is exactly the assumption that sends keystrokes to the
    # game world instead of the widget.
    click((CONVERT_DLG_QTY_VALUE[0] + CONVERT_DLG_QTY_VALUE[2]) // 2,
          (CONVERT_DLG_QTY_VALUE[1] + CONVERT_DLG_QTY_VALUE[3]) // 2)
    # Six backspaces clear any quantity this field holds without paying the
    # typing cooldown for a long tail of no-ops.
    type_number(quantity, clear_first=True, clear=6)
    time.sleep(0.35)

    landed = mass_purchase_details().get("qty")
    # After typing, before confirming. When the read-back disagrees this is the
    # evidence of what the field actually showed -- and "typed 250, it reads 5"
    # is a real failure that happened on 2026-08-07 at a 0.1s keystroke pace.
    record("convert.typed", item=core_name, typed=quantity,
           expected=expected, landed=landed)
    if landed != expected:
        bail(f"typed {quantity} expecting the field to settle at {expected}, "
             f"but it reads {landed}. The keystrokes may have gone somewhere "
             "else entirely. Cancelled without buying.")

    # And once more before the click that actually spends the Sets. The dialog
    # being up is not by itself proof the shop is still behind it.
    if not vendor_shop_open():
        bail("the vendor's Shop window is no longer open behind the dialog. "
             "Cancelled without buying.")

    say(f"  quantity {expected} confirmed in the field; purchasing")
    record("convert.confirming", item=core_name, quantity=expected)
    click(*confirm.centre)
    time.sleep(0.8)

    # The dialog should be gone. If it is not, something rejected the purchase
    # and the held count below will say so anyway -- but leaving it open would
    # strand every later step, so it gets closed either way.
    if mass_purchase_open() is not None:
        press_escape()
        time.sleep(0.3)

    # ---- what actually arrived, counted in slots ---------------------------
    # Cores do not stack, so each one takes a slot of its own -- which makes
    # "how many converted" a COUNT of newly occupied slots rather than anything
    # read off the screen. That is better than the tooltip it replaces in three
    # ways: it cannot be defeated by text colour, it measures the result rather
    # than inferring it from a before/after subtraction, and it is right even
    # when the game clamps the purchase to the free space available.
    # Back to the work tab before counting. The count is the whole verdict --
    # Cores do not stack, so newly filled slots ARE the number converted -- and
    # it only means anything on the tab the baseline was taken from. A purchase
    # can leave the panel showing somewhere else entirely.
    select_inventory_tab(CONVERT_INVENTORY_TAB, origin)
    filled_after = set(occupied_slots(grab(), origin))
    park_cursor()
    arrived = filled_after - filled
    converted = len(arrived)
    # "Cores visibly arrived in slots that were empty." Deliberately the
    # NARROW reading: a Core dropping into a slot the Sets just vacated is
    # indistinguishable from the Set stack merely shrinking, so counting that
    # as arrival would report success for a conversion that did nothing.
    #
    # It therefore UNDER-reports in exactly the freed-slot case, which is why
    # restock_core measures progress by what was LISTED rather than by this --
    # register_item's quantity counts every matching Core in the inventory and
    # cannot be fooled by where they landed.
    landed = bool(filled_after - filled)
    candidates = core_slot_candidates(filled, filled_after)

    if arrived:
        where = sorted(arrived)
        say(f"  new Cores landed in {len(where)} slot(s) on tab "
            f"{CONVERT_INVENTORY_TAB}: {where[0]} to {where[-1]}"
            + (f" -- e.g. {where[:6]}" if len(where) > 1 else ""))
    if converted == countable and landed:
        extra = "" if countable == expected else f" (+{expected - countable} on later tabs)"
        say(f"  converted {converted} x {costs} -> {gives}{extra}")
    elif converted:
        # Not an abort: the purchase has already happened, and raising here
        # would report zero progress for a conversion that partly went through.
        say(f"  WARNING: expected {countable} on this tab but {converted} "
            "slot(s) filled. Nothing further was clicked.")
    else:
        say("  WARNING: no new inventory slots were filled. The purchase may "
            "not have gone through.")

    return {"cell": (row, col), "gives": gives, "costs": costs,
            "expected": expected, "countable": countable,
            "converted": converted, "arrived": sorted(arrived),
            "candidates": candidates,
            "occupied_before": sorted(filled),
            "occupied_after": sorted(filled_after),
            "landed": landed, "verified": converted == countable and landed}


# --------------------------------------------------------------------------
# Buying on the Purchase tab
# --------------------------------------------------------------------------
#
# A Set is the same cores sold as one bundle, and it is usually cheaper PER
# CORE than the loose item. Measured on 2026-08-07: Force Core(High) at 209,800
# each against Force Core Set (High) at 187,278 each -- 22,522 a core.
#
# Two things about these rows have to be got right or the arithmetic is
# nonsense. The Set's name carries its size ("X 62") and the Price column is
# the price of the WHOLE bundle, so the per-core figure is price/N. And the
# sort is by that bundle total, NOT per core -- so the first row is the
# cheapest LISTING and need not be the cheapest core. Both were confirmed
# against the live shop, where every Set row divided to the same 187,278.
PURCHASE_ROW_TOP = 340
PURCHASE_ROW_PITCH = 76
PURCHASE_ROWS = 8
# The Confirm Purchase dialog, measured on the live client on 2026-08-07 and
# kept as unit_tests/corpus/goldens/purchase_confirm_qty48.png.
#
#     Confirm Purchase
#       Force Core Set (Highest) X 1
#       Purchase QTY   [1] / 48
#       Purchase Price      190,190 Alz
#             [ Buy ]   [ Cancel ]
#
# Each region is its own crop rather than one dialog-wide read. That is not
# tidiness: mass_purchase_open had to be given a separate button crop because
# the whole-dialog read returned every word EXCEPT the buttons, and the same
# thing happens here -- a read of the full dialog returns the words jumbled
# out of order, while the tight crops are exact.
#
# QTY_VALUE is the fussiest of them. It holds a single digit next to a text
# cursor, and Tesseract returns nothing at all from a crop a few pixels wider:
# (1150, 665, 1220, 705) reads '', (1152, 668, 1218, 702) reads '1'. Measured,
# not guessed -- four crops were tried and one worked. Do not "tidy" these
# numbers.
PURCHASE_DIALOG_REGION = (1000, 545, 1575, 895)
PURCHASE_DLG_ITEM = (1030, 600, 1330, 645)
PURCHASE_DLG_QTY_VALUE = (1152, 668, 1218, 702)
PURCHASE_DLG_QTY_MAX = (1215, 665, 1280, 705)
# Widened leftward on 2026-08-08. It was measured against a 190,190 price and
# was too narrow for a nine-digit one: on run_28604 a 450,000,000 dialog read
# as 50,000,000, the leading digit falling outside the crop.
#
# That failed CLOSED -- buy_offer compares the dialog against listings x row
# price and refuses on a mismatch -- so it blocked legitimate large purchases
# rather than allowing wrong ones. Still a bug, and one only a golden frame of
# a big-ticket dialog could show: every frame captured before that day had a
# six-digit price and passed.
#
# Left edge 1150 rather than 1230: 1200 was enough for 450,000,000, and the
# margin covers a ten-digit figure without reaching the "Purchase Price" label.
PURCHASE_DLG_PRICE = (1150, 712, 1380, 758)
PURCHASE_DIALOG_BUTTONS = (1190, 830, 1570, 880)

PURCHASE_NAME_MAX_X = 700          # the Name cell ends before the QTY column
PURCHASE_PRICE_X = (900, 1080)     # the Price cell
PURCHASE_BUY_X = 1124              # the per-row Buy button
# How much cheaper per item a Set must be before it is worth buying.
PRICE_DIFF_FLOOR = 10_000
SET_SAVING_THRESHOLD = PRICE_DIFF_FLOOR      # older name, kept for callers

# Per-item overrides of that floor, keyed by the game's own spelling.
#
# One threshold for every item assumes every item has the same opportunity
# cost, and they do not. Measured on 2026-08-08, Force Core(High)'s spread ran
# from -16,157 to +5,811 across ten checks: it never once cleared 10,000, so it
# never restocked, while being the biggest seller on the account -- 713 units
# and 148,141,000 Alz of revenue, all of it now gone from the shop.
#
# Set deliberately low for the two items where turnover matters more than
# margin. Stock that does not move earns nothing at all, and a 5,000 spread on
# an item that sells is worth more than a 10,000 spread on one that sits.
#
# The keys are checked against the real slot map at startup by
# validate_price_diff_floors(), because a typo here reads as "this item is
# back on the 10,000 floor" -- the quiet direction, which is exactly how the
# original ENABLE_BUYING typo would have silently disabled an item.
PRICE_DIFF_FLOOR_BY_ITEM: dict[str, int] = {
    "Upgrade Core(Highest)": 5_000,
    "Force Core(High)":      5_000,
}


def price_diff_floor_for(item: str) -> int:
    """The saving an item's Set must clear before it is worth buying.

    Matched on the same folded key the rest of the file uses, so the game's
    inconsistent spacing before the bracket -- and a pack marker on a table
    name -- cannot cause a miss and silently restore the default.
    """
    want = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", item or "")))
    if not want:
        return PRICE_DIFF_FLOOR
    for name, floor in PRICE_DIFF_FLOOR_BY_ITEM.items():
        if _floor_key(item_name(name)) == want:
            return int(floor)
    return PRICE_DIFF_FLOOR


def validate_price_diff_floors() -> None:
    """Refuse a key that names nothing, rather than ignoring it.

    An unmatched key is not a no-op: it means the operator asked for a lower
    threshold on an item that then quietly kept the 10,000 one, and the only
    evidence would be a restock that never fires.
    """
    known = {_floor_key(item_name(n)) for n in FAVOURITE_SLOTS.values()}
    for name in PRICE_DIFF_FLOOR_BY_ITEM:
        if _floor_key(item_name(name)) not in known:
            raise ValueError(
                f"PRICE_DIFF_FLOOR_BY_ITEM names {name!r}, which is not a "
                "favourite. Known: "
                + ", ".join(sorted(FAVOURITE_SLOTS.values())))
# A listing can sell to somebody else between reading it and clicking Buy. The
# confirm dialog then refuses to complete: it neither closes nor takes any Alz.
# That is not an error to stop on -- it is the ordinary race of a live market --
# so the buy cancels, re-runs the search and takes whatever is cheapest now.
BUY_RETRY_ATTEMPTS = 3

# How many times a favourite slot is clicked before its search is given up on.
#
# The Purchase tab does NOT clear its results when a click fails to take, so a
# search that did not run leaves the PREVIOUS item's rows on screen -- which is
# why run_favourite_search returns empty rather than whatever is displayed, and
# why it is worth retrying rather than accepting the first answer.
#
# Raised from 2 to 5 on 2026-08-08. Two was enough most of the time -- the live
# logs show "the search did not run (attempt 1/2)" followed by a clean read --
# but a slot that misses twice ends the whole restock for that Core, and the
# retry costs one click and a settle against a purchase worth tens of millions.
FAVOURITE_SEARCH_TRIES = 5
# A row priced below this fraction of the median is treated as a clipped read,
# not a bargain. Genuine rows in one Set search agreed to within 1.15 Alz.
PRICE_OUTLIER_FLOOR = 0.5

# Case-insensitive. The game draws "X 250", but Tesseract returns a
# lowercase "x" often enough to matter and both failures are severe. A
# missed marker here makes pack_size fall back to 1, so a 250-Set bundle
# is recorded as ONE item and purchase_cost_basis reads the whole bundle
# price as the per-item cost -- a floor 250x too high, which quietly stops
# everything from selling.
_PACK_SIZE = re.compile(r"\bX\s*([\d,]+)\s*$", re.IGNORECASE)
# The confirm dialog reorders the pack into the middle of the name -- "Force
# Core Set X 62 (High)" against the row's "Force Core Set (High) X 62" -- so
# comparing the two needs it stripped from anywhere, not just the end.
# Likewise case-insensitive. A missed marker here leaves "x 62" glued to
# the name, and every lookup that strips the pack before comparing --
# the cost floor via favourite_for, the dialog name check in buy_offer,
# the search receipt -- silently misses instead of matching.
_PACK_ANYWHERE = re.compile(r"\bX\s*[\d,]+", re.IGNORECASE)


def pack_size(name: str) -> int:
    """How many items one listing of `name` contains. 1 when it is not a Set."""
    m = _PACK_SIZE.search(name.strip())
    if not m:
        return 1
    try:
        return max(1, int(m.group(1).replace(",", "")))
    except ValueError:
        return 1


@dataclass(frozen=True)
class Offer:
    """One row of the Purchase results."""
    row: int                # 1-based screen row
    name: str
    price: int              # what ONE of these listings costs
    pack: int               # how many items one listing holds ("X 28" -> 28)
    y: int
    # How many identical listings are on offer at this price -- the table's
    # middle column. Defaults to 1 so every existing caller and test keeps
    # working, and because one is the only count that is certainly safe: it is
    # what the script assumed before this column was read at all.
    available: int = 1

    @property
    def unit(self) -> float:
        return self.price / self.pack

    @property
    def stock(self) -> int:
        """Items obtainable from this row: pack size x how many are offered."""
        return self.pack * max(1, self.available)


def read_purchase_rows(source: "Image.Image | None" = None) -> list[Offer]:
    """Every readable row of the Purchase results, top to bottom."""
    shot = source if source is not None else grab()
    offers: list[Offer] = []
    for i in range(PURCHASE_ROWS):
        y = PURCHASE_ROW_TOP + i * PURCHASE_ROW_PITCH
        # Starts at 250, not 240: the category tree on the left bleeds into the
        # band and prefixed names with its own text ("of L Force Core Set...").
        band = (250, y - 24, 1235, y + 24)
        words = [w for w in find_words(shot, band, 20) if w.conf >= 55]
        if not words:
            continue
        name = " ".join(w.text for w in sorted(
            (w for w in words if w.centre[0] < PURCHASE_NAME_MAX_X),
            key=lambda w: w.centre[0])).strip()
        price = None
        for w in words:
            if PURCHASE_PRICE_X[0] < w.centre[0] < PURCHASE_PRICE_X[1]:
                digits = re.sub(r"[^\d]", "", w.text)
                if digits.isdigit() and len(digits) >= 4:
                    price = int(digits)
        if not name or price is None or price < MIN_PLAUSIBLE_PRICE:
            continue

        # The count column, between the name and the price.
        #
        # Read from the words already fetched where possible, then rescued with
        # a targeted pass: a LONE DIGIT in this narrow cell returns no words
        # from the bulk pass at any confidence, which is the same failure
        # read_rows documents for the shop table's quantity column (~30% of
        # cells). Since most counts are 1, the rescue is the common path here
        # rather than the exception.
        cell = (PURCHASE_NAME_MAX_X, y - 24, PURCHASE_PRICE_X[0], y + 24)
        available = None
        digits = sorted((w for w in words
                         if PURCHASE_NAME_MAX_X < w.centre[0]
                         < PURCHASE_PRICE_X[0]),
                        key=lambda w: w.centre[0])
        if digits:
            joined = re.sub(r"[^\d]", "", "".join(w.text for w in digits))
            if joined.isdigit():
                available = int(joined)
        if available is None:
            available = read_number(shot, cell, 30.0)
        # Falls back to 1, never to 0 or None. This number decides how much is
        # bought, so an unreadable cell must mean "assume the least" -- which
        # is exactly the behaviour before the column was read.
        if not available or available < 1:
            available = 1

        offers.append(Offer(row=i + 1, name=name, price=price,
                            pack=pack_size(name), y=y, available=available))
    return offers


def cheapest_per_unit(offers: list[Offer]) -> "Offer | None":
    """The offer with the lowest price PER ITEM.

    Not offers[0]: the table sorts by the listing total, so on a Set search the
    first row is the smallest bundle rather than the best value.

    A row far below the rest is DISCARDED rather than taken as a bargain. A
    clipped price read looks exactly like the find of the day: on 2026-08-07
    row 8 read 444,281 for 39 items -- 11,391 each against 187,278 everywhere
    else -- because the leading "7," of 7,444,281 was lost. Every genuine row
    in that table agreed to within 1.15 Alz, so a row at 6% of its neighbours
    is a misread, and acting on it would put a real 7,444,281 through on the
    strength of a number that was never there.
    """
    if not offers:
        return None
    if len(offers) >= 3:
        units = sorted(o.unit for o in offers)
        median = units[len(units) // 2]
        credible = [o for o in offers if o.unit >= median * PRICE_OUTLIER_FLOOR]
        if credible:
            offers = credible
    return min(offers, key=lambda o: o.unit)


def credible_offers(offers: list[Offer]) -> list[Offer]:
    """Offers with a believable price. See cheapest_per_unit for why."""
    if len(offers) < 3:
        return list(offers)
    units = sorted(o.unit for o in offers)
    median = units[len(units) // 2]
    kept = [o for o in offers if o.unit >= median * PRICE_OUTLIER_FLOOR]
    return kept or list(offers)


def cheapest_listing(offers: list[Offer]) -> "Offer | None":
    """The FIRST row. The table is already sorted Price: Low to High.

    Deliberately no cleverness. Two earlier versions tried to improve on the
    game's own ordering and both were wrong:

      * "smallest total" bought the worst value in the table -- the totals ran
        11.6M, 23.2M, 29.8M ... then 5.7M last, because the sort is per ITEM;
      * "lowest per item" compared floats, and the per-item figure is a bundle
        price divided by a pack size, so it carries a rounding remainder that
        is not a price. 187,278.000 beat 187,278.226 and cost 8,614,760 Alz
        more outlay to save 38 Alz across the stack.

    The sort is the game's answer to "which is cheapest" and it does not need
    reinterpreting. What still protects the purchase is downstream, where it
    belongs: buy_offer compares the confirm dialog's own price against the row
    before confirming, so a clipped read -- which sorts to the top precisely
    BECAUSE it is too small -- is caught by the price that actually matters
    rather than by second-guessing the order here.
    """
    return offers[0] if offers else None


def purchase_confirm(source: "Image.Image | None" = None) -> dict | None:
    """The Confirm Purchase dialog, or None.

    Its own reader because dialog_kind() cannot see it -- the title reads
    'Confirm Purchase' and is not among DIALOG_KINDS, so dialog_present()
    returns False with it plainly on screen. Anything relying on that would
    read the table it covers as an empty shop.
    """
    shot = source if source is not None else grab()
    words = [w for w in find_words(shot, (700, 400, 1700, 950), 20)
             if w.conf >= 45]
    text = " ".join(w.text for w in words)
    if "Purchase" not in text:
        return None
    buttons = {}
    for w in words:
        label = w.text.strip().lower()
        if label in ("buy", "cancel") and w.centre[1] > 800:
            buttons[label] = w.centre
    if "buy" not in buttons:
        return None
    # The price from its OWN crop first. The sweep below takes the last
    # >=6-digit word anywhere in a 1000x550 region, which was fine while the
    # quantity was always 1 -- now that the quantity is typed, the total
    # changes and the figure has to come from the cell that shows it.
    price = read_number(shot, PURCHASE_DLG_PRICE, 40.0)
    if price is None:
        for w in words:
            digits = re.sub(r"[^\d]", "", w.text)
            if digits.isdigit() and len(digits) >= 6:
                price = int(digits)

    # "Purchase QTY [n] / max". Two crops, because they behave differently:
    # the max reads from an ordinary word pass, the value is a lone digit that
    # needs read_number's psm-7/psm-10 fallbacks.
    qty = read_number(shot, PURCHASE_DLG_QTY_VALUE, 30.0)
    qty_max = None
    max_words = [w.text for w in find_words(shot, PURCHASE_DLG_QTY_MAX, 20)
                 if w.conf >= 30]
    digits = re.sub(r"[^\d]", "", "".join(max_words))
    if digits.isdigit():
        qty_max = int(digits)

    return {"buy": buttons["buy"], "cancel": buttons.get("cancel"),
            "price": price, "text": text, "qty": qty, "qty_max": qty_max}


def offers_match_slot(slot: int, offers: list[Offer]) -> bool:
    """Do these rows actually belong to the item favourite `slot` searches for?

    The Purchase tab does NOT clear its results when a search fails to run, so
    "there are rows on screen" proves nothing about which search produced them.
    Measured on 2026-08-07: a click on the loose-item slot did not take, the
    previous Set results stayed up, and the comparison read the Set against
    ITSELF -- item 187,278.00/each, set 187,278.00/each, saving 0. It refused
    to buy for the right-looking reason and the wrong actual one, which is the
    failure mode that hides worst.

    A Set's name contains its parent's, so the direction matters: rows for
    "Force Core Set (High)" all contain "Force Core(High)" once folded. The
    test is therefore exact on the folded key, not a substring.
    """
    want = FAVOURITE_SLOTS.get(slot)
    if not want:
        return False
    key = _floor_key(item_name(want))
    hits = 0
    for offer in offers:
        # Strip the pack suffix before comparing: "... X 62" is the listing,
        # not the item.
        bare = _PACK_SIZE.sub("", offer.name).strip()
        if _floor_key(item_name(bare)) == key:
            hits += 1
    return hits >= max(1, len(offers) // 2)


PURCHASE_TAB_MARKERS = ("Category", "Function")
PURCHASE_SORT_REGION = (820, 178, 1080, 212)

# The sort dropdown itself, and the list it drops down.
#
# Measured on the reference display with the list open: the closed control sits
# at y~187, and the two options land at y~219 ("By Price:Low to High") and
# y~255 ("By Price:High to Low"), pitch 36. The list is drawn OVER the offers
# table -- the table's own header row sits at y~256, under the second option --
# which is why the options are located by reading rather than by that pitch.
PURCHASE_SORT_BUTTON = (930, 195)
# Starts exactly where PURCHASE_SORT_REGION ends. Overlapping the two by even a
# few pixels lets the CLOSED control's own label bleed into the "is the menu
# open" read, which would answer yes with the menu shut -- and then a click
# aimed at a menu row lands on the offers table underneath.
PURCHASE_SORT_OPTIONS = (790, 212, 1120, 285)

# The direction word immediately after "Price:". Both option labels contain
# BOTH "low" and "high", so a substring test cannot tell them apart; the order
# is the whole signal.
_SORT_DIRECTION = re.compile(r"price\s*:?\s*(low|high)", re.IGNORECASE)

# Open, pick, verify. Three tries because the failure this recovers from is a
# dropped click, and a dropped click is uncorrelated with the next one.
PURCHASE_SORT_TRIES = 3


def purchase_tab_open(source: "Image.Image | None" = None) -> bool:
    """True when the Trade window is showing the PURCHASE tab.

    Distinct from register_tab_open: the two tabs share a window, and clicking
    a Purchase-tab coordinate while the Register tab is up hits the listings
    table instead of the search controls.
    """
    shot = source if source is not None else grab()
    hits = sum(1 for marker in PURCHASE_TAB_MARKERS
               if find_text(shot, marker, TRADE_REGION))
    return hits >= len(PURCHASE_TAB_MARKERS)


def purchase_sorted_low_to_high(source: "Image.Image | None" = None) -> bool:
    """True when the results are sorted Price: Low to High.

    Every price decision assumes it -- "row 1 is the cheapest" is only true
    under this sort. Read rather than assumed, because the control is a
    dropdown a human can change and nothing else would notice.

    The direction is the word straight after "Price:", and nothing else will
    do. This used to ask `"low" in text and "price" in text`, which is true of
    "By Price:High to Low" as well -- the two labels are anagrams as far as a
    substring test is concerned. That check only ever refused the wrong sort
    because OCR clipped the trailing "Low" off the end of the control, so it
    was a crop doing the work, not the condition. The same shape of bug as
    "Force Core(Highest)" containing "Force Core(High)", in the one place where
    getting it wrong buys the most expensive offer on the board believing it to
    be the cheapest.

    Fails closed: an unread direction, or no "Price:" at all, is False.
    """
    shot = source if source is not None else grab()
    words = [w for w in find_words(shot, PURCHASE_SORT_REGION, 20)
             if w.conf >= 45]
    text = " ".join(w.text for w in words)
    match = _SORT_DIRECTION.search(text)
    if match is None:
        return False
    return match.group(1).casefold() == "low"


def _sort_option_rows(source: "Image.Image | None" = None) -> dict:
    """Where each sort option sits, read from the open dropdown.

    Returns {"low": (x, y), "high": (x, y)} for whichever options are legible,
    empty when the list is not open. The offers table shows through the same
    band, so the discriminator is a row naming BOTH directions -- the table's
    own header row is "QTY | Price | ...", which names neither.
    """
    shot = source if source is not None else grab()
    words = [w for w in find_words(shot, PURCHASE_SORT_OPTIONS, 20)
             if w.conf >= 45]
    rows: dict = {}
    for line in _text_lines(words):
        text = " ".join(w.text for w in line)
        match = _SORT_DIRECTION.search(text)
        if match is None:
            continue
        lowered = text.casefold()
        if "low" not in lowered or "high" not in lowered:
            # Half a label. Clicking a row we only partly read is how a menu
            # click lands on the offers table underneath it.
            continue
        left = min(w.left for w in line)
        right = max(w.right for w in line)
        top = min(w.top for w in line)
        bottom = max(w.bottom for w in line)
        rows[match.group(1).casefold()] = ((left + right) // 2,
                                           (top + bottom) // 2)
    return rows


# How long the menu is given to appear after the control is clicked. The menu
# is drawn by the game, not animated in, so this is a click-registration
# allowance rather than a fade.
SORT_OPTIONS_TIMEOUT = 2.0


def _wait_for_sort_options(timeout: float = SORT_OPTIONS_TIMEOUT) -> dict:
    """The sort menu once it is legible, or {} if it never becomes legible."""
    deadline = time.monotonic() + timeout
    while True:
        rows = _sort_option_rows()
        if "low" in rows:
            return rows
        if time.monotonic() >= deadline:
            return rows
        time.sleep(0.2)


def set_purchase_sort_low_to_high(verbose: bool = True) -> bool:
    """Put the sort on Price: Low to High, and confirm it landed.

    purchase_ready() has always CHECKED this and refused when it was wrong,
    which meant a dropdown left on "High to Low" -- by a human, or by the game
    remembering it across a session -- silently blocked every purchase for the
    rest of the run. That is what happened on 2026-08-08: fifteen buy attempts,
    every one refused at the sort check, zero Sets bought. A guard with no
    remedy is just a way to fail quietly for hours.

    Idempotent, and safe to call with the list already open: the state is read
    before anything is clicked, at every step.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    for attempt in range(1, PURCHASE_SORT_TRIES + 1):
        shot = grab()
        if not trade_window_open(shot):
            say("  the Trade window is not open - refusing to click the sort "
                "control, the game world is underneath.")
            record("sort.not_ready", reason="window_shut")
            return False
        if not purchase_tab_open(shot):
            say("  not on the Purchase tab - the sort control does not exist "
                "on the other tab.")
            record("sort.not_ready", reason="wrong_tab")
            return False

        rows = _sort_option_rows(shot)
        if not rows:
            # The list is shut. Whether the sort is already right does not
            # matter yet -- opening is how we get a legible answer, and the
            # click is a no-op if it turns out to be.
            if purchase_sorted_low_to_high(shot):
                say("  the sort is already Price: Low to High.")
                return True
            say(f"  the sort is not Price: Low to High; opening the dropdown "
                f"(try {attempt} of {PURCHASE_SORT_TRIES}).")
            if not panel_covers_trade_area():
                say("  the Trade area is still moving - that is the world, "
                    "not a panel. Refusing to click.")
                record("sort.not_ready", reason="area_animating")
                return False
            click(*PURCHASE_SORT_BUTTON)
            # Polled, not slept at. A single 0.4s wait read an empty menu on
            # the first live try and a populated one on the second, which is
            # the signature of a fixed delay sitting right on the boundary --
            # it would have been a coin flip on every run.
            rows = _wait_for_sort_options()

        target = rows.get("low")
        if target is None:
            say("  the dropdown did not open, or 'Price: Low to High' was not "
                "legible in it - not clicking a row I cannot read.")
            record("sort.option_unread", seen=sorted(rows))
            continue

        say(f"  selecting 'Price: Low to High' at {target}.")
        click(*target)
        time.sleep(0.4)
        if purchase_sorted_low_to_high():
            say("  the sort now reads Price: Low to High.")
            record("sort.set", attempt=attempt)
            return True
        say("  the sort still does not read Price: Low to High.")

    say(f"  could not set the sort to Price: Low to High in "
        f"{PURCHASE_SORT_TRIES} tries.")
    record("sort.failed", tries=PURCHASE_SORT_TRIES)
    return False


def purchase_ready(verbose: bool = True) -> bool:
    """Every precondition for clicking anything on the Purchase tab.

    Checked BEFORE each click, not once at the start of a sweep. On 2026-08-07
    a capture loop verified the Trade window once and then clicked favourite
    coordinates eighty times; the window closed partway through and every later
    click went into the 3D world as a move order. The character walked away
    from the NPC, an item tooltip opened, and the run was lost -- from clicks
    that were correct for a window that was no longer there.

    Both window signals are required. trade_window_open() is a text search and
    the world can supply those glyphs; panel_covers_trade_area() compares two
    frames and a UI panel does not animate. The pair is what open_trade_window
    itself trusts.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    shot = grab()
    if not trade_window_open(shot):
        say("  the Trade window is not open - refusing to click, the game "
            "world is underneath.")
        record("purchase.not_ready", reason="window_shut")
        return False
    if not panel_covers_trade_area():
        say("  the Trade window reads as open but the area is still moving - "
            "that is the world, not a panel. Refusing to click.")
        record("purchase.not_ready", reason="area_animating")
        return False
    if not purchase_tab_open(shot):
        say("  the Trade window is not on the Purchase tab - refusing to "
            "click Purchase-tab coordinates on another tab.")
        record("purchase.not_ready", reason="wrong_tab")
        return False
    if not purchase_sorted_low_to_high(shot):
        say("  the results are not sorted Price: Low to High, so 'row 1 is "
            "the cheapest' does not hold. Refusing.")
        record("purchase.not_ready", reason="wrong_sort")
        return False
    return True


# The ONLY sanctioned way to buy: press a favourite slot, take row 1, press
# Buy. Anything else is refused.
#
# Recorded as a receipt rather than assumed, because "the caller surely
# searched first" is the kind of assumption that holds until a new code path
# does not. The receipt carries which slot ran, when, and what its first row
# was -- so buy_offer can check that the row it is about to buy is the row that
# search actually produced, and not one left on screen from before. The
# Purchase tab never clears its results, so a stale row looks exactly like a
# fresh one.
BUY_ROW = 1
# How long a search stays good for. Long enough to cover the compare-and-click
# that follows it, short enough that a listing cannot have been replaced twice
# over in the meantime.
SEARCH_RECEIPT_SECONDS = 90.0
_LAST_SEARCH: dict | None = None


def note_favourite_search(slot: int, offers: list) -> None:
    """Record that `slot` was searched and what came back at row 1."""
    global _LAST_SEARCH
    _LAST_SEARCH = {
        "slot": slot,
        "at": time.monotonic(),
        "first": offers[0].name if offers else "",
    }


def search_receipt_for(offer) -> str:
    """"" if `offer` may be bought, else why it may not.

    Enforces the sequence rather than trusting it: the offer has to be ROW 1 of
    a favourite search that ran moments ago and produced this very listing.
    """
    if offer.row != BUY_ROW:
        return (f"row {offer.row} is not row {BUY_ROW}; only the first row of "
                "a favourite search may be bought")
    if _LAST_SEARCH is None:
        return "no favourite search has run, so there is no row 1 to speak of"
    age = time.monotonic() - _LAST_SEARCH["at"]
    if age > SEARCH_RECEIPT_SECONDS:
        return (f"the last favourite search was {age:.0f}s ago; too old to "
                "trust that these rows are still the ones it found")
    if not _LAST_SEARCH["first"]:
        # A search that found nothing must not authorise anything. Left as a
        # skipped comparison, a blank receipt read as "no disagreement" and
        # allowed the buy -- which is the failure this whole receipt exists to
        # prevent, arrived at from the other side.
        return "the last favourite search returned no rows at all"
    shown = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", _LAST_SEARCH["first"])))
    wanted = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", offer.name)))
    if not shown or not wanted:
        return "either the search or the offer has no readable name"
    if shown != wanted:
        return (f"row 1 of the last search was {_LAST_SEARCH['first']!r}, not "
                f"{offer.name!r}")
    return ""


def run_favourite_search(slot: int, settle: float = 3.0,
                         tries: int = FAVOURITE_SEARCH_TRIES,
                         verbose: bool = True) -> list[Offer]:
    """Click favourite `slot` and return what it found, or [] if it did not run.

    Returns EMPTY rather than whatever happens to be on screen when the search
    cannot be confirmed. Stale rows read as a real answer are worse than no
    answer: they look exactly like a successful search of a different item.
    """
    for attempt in range(1, tries + 1):
        # Re-checked EVERY time, not once per sweep. The window can close
        # between one click and the next, and a favourite coordinate with no
        # window under it is a move order into the game world.
        if not purchase_ready(verbose=verbose):
            return []
        x, y = favourite_slot_point(slot)
        focus_game()
        # Approach from above so the pointer ENTERS the button: a move to the
        # pixel the cursor already occupies raises no event, and the control is
        # then never armed.
        move_mouse(x, y - 45)
        time.sleep(0.2)
        click(x, y)
        time.sleep(settle)
        park_cursor()
        time.sleep(0.4)
        offers = read_purchase_rows()
        if offers and offers_match_slot(slot, offers):
            if verbose:
                print(f"  slot {slot} ({FAVOURITE_SLOTS.get(slot, '?')}): "
                      f"{len(offers)} offer(s)")
            note_favourite_search(slot, offers)
            return offers
        if verbose:
            sample = offers[0].name if offers else "(nothing)"
            print(f"  slot {slot}: the results still show {sample!r} - the "
                  f"search did not run (attempt {attempt}/{tries})")
    return []


def buy_offer(offer: Offer, want: int = 1, timeout: float = 8.0,
              verbose: bool = True) -> tuple[bool, str]:
    """Buy one listing. Returns (bought, why).

    The Alz balance is the proof, not the click: a listing can sell to somebody
    else between being read and being clicked, and the Confirm Purchase dialog
    then simply refuses -- it neither closes nor takes any money. Watching for
    the dialog to go away is not enough either, so the balance is compared
    before and after and a purchase is only claimed when it actually moved.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not purchase_ready(verbose=verbose):
        return False, "the Purchase tab was not ready to be clicked"

    # The sequence, enforced. A Buy is only ever allowed as the third step of
    # "favourite slot -> row 1 -> Buy", and a failure here means the code
    # reached this function some other way -- which is a wrong map, not a
    # transient, so buying stops for the run.
    wrong = search_receipt_for(offer)
    if wrong:
        halt_buying(f"a Buy was attempted outside the sanctioned sequence: "
                    f"{wrong}")
        return False, wrong
    before = get_alz(grab()) or None
    focus_game()
    # Select the row, then press its Buy. Approached from a different point so
    # the pointer genuinely ENTERS the button: move_mouse to a pixel the cursor
    # already occupies generates no move event, and the game then never arms
    # the control.
    move_mouse(500, offer.y - 40)
    time.sleep(0.2)
    click(500, offer.y)
    time.sleep(0.8)
    move_mouse(PURCHASE_BUY_X, offer.y - 40)
    time.sleep(0.2)
    click(PURCHASE_BUY_X, offer.y)
    time.sleep(1.5)

    dialog = purchase_confirm()
    if dialog is None:
        # No dialog and no purchase: whatever the Buy click hit, it was not the
        # button. Do NOT click again blind.
        record("buy.no_dialog", item=offer.name, price=offer.price)
        return False, "the Confirm Purchase dialog did not appear"

    # The last frame before real Alz moves, and the only step in the buying
    # path that was never captured -- the recorder fired at buy.completed,
    # AFTER the click. So the one dialog whose layout decides how much is spent
    # had no golden frame, and its quantity field could not be mapped without
    # driving the live game to look at it.
    record("buy.dialog", item=offer.name, price=offer.price,
           pack=offer.pack, available=offer.available)

    def refuse(why: str) -> tuple[bool, str]:
        say(f"  {why} - cancelling rather than buying it.")
        record("buy.refused", item=offer.name, price=offer.price, why=why)
        if dialog and dialog.get("cancel"):
            cx, cy = dialog["cancel"]
            move_mouse(cx, cy + 60)
            time.sleep(0.2)
            click(cx, cy)
            time.sleep(1.0)
        return False, why

    # ---- how many of this listing to take -------------------------------
    #
    # The dialog has a quantity field -- "Purchase QTY [1] / 48" -- and nothing
    # used it. Every purchase took ONE listing and searched again, so a row
    # offering 48 identical Sets at 190,190 each cost 48 searches and 48
    # dialogs to drain, and RESTOCK_MAX_BUYS stopped it long before the target.
    #
    # The field counts LISTINGS, not items. A row reading "X 28" with a max of
    # 2 is two bundles of 28, so the items obtained are `take * offer.pack` and
    # the price is `take * offer.price`.
    limit = dialog.get("qty_max")
    if not limit or limit < 1:
        # Unreadable max: take one. This decides how much money moves, so an
        # unreadable field must mean the smallest possible order, which is
        # exactly what the code did before the field was read at all.
        say("  the dialog's quantity limit did not read - taking one listing.")
        limit = 1
    take = max(1, min(int(want), int(limit), max(1, offer.available)))

    if take > 1:
        say(f"  taking {take} of the {limit} listing(s) on offer")
        # Click the field before typing. "It looks focused" is exactly the
        # assumption that sends keystrokes into the game world instead of the
        # widget -- the same reasoning as the vendor's Mass Purchase dialog.
        click((PURCHASE_DLG_QTY_VALUE[0] + PURCHASE_DLG_QTY_VALUE[2]) // 2,
              (PURCHASE_DLG_QTY_VALUE[1] + PURCHASE_DLG_QTY_VALUE[3]) // 2)
        type_number(take, clear_first=True, clear=6)
        time.sleep(0.35)

        dialog = purchase_confirm()
        if dialog is None:
            return False, ("the Confirm Purchase dialog vanished while the "
                           "quantity was being typed")
        landed = dialog.get("qty")
        if landed != take:
            # Do NOT fall back to buying whatever it does say. The keystrokes
            # may have gone somewhere else entirely, and the figure on screen
            # is what the game will charge for.
            return refuse(f"typed {take} into the quantity field but it reads "
                          f"{landed}")

    # What this order should now cost, which is NOT the row's listed price once
    # more than one listing is being taken.
    expected = offer.price * take

    # The dialog states the real price. If it disagrees with what the row and
    # the quantity imply, the row was misread or the table moved -- either way,
    # do not buy.
    if dialog["price"] and dialog["price"] != expected:
        # NOT permanent. A price that has moved is an ordinary race: the
        # listing changed between reading the row and opening the dialog, and
        # the next search sees the new one. Only a NAME mismatch means the map
        # is wrong.
        return refuse(f"the dialog says {dialog['price']:,} but {take} x "
                      f"{offer.price:,} is {expected:,}")

    # And it must name the item that was chosen. The price alone is not enough:
    # two different items can carry the same figure, and the row that was
    # clicked is not necessarily the row the game acted on -- a listing selling
    # underneath shifts everything up.
    #
    # Compared with the pack stripped from BOTH sides, because the dialog
    # reorders it: the row reads "Force Core Set (High) X 62" and the dialog
    # says "Force Core Set X 62 (High)".
    # The grade has to be checked EXACTLY, not by containment. _floor_key
    # folds "Force Core Set (High)" to forcecoresethigh and "...(Highest)" to
    # forcecoresethighest -- and the first is a SUBSTRING of the second, so
    # "wanted in shown" waves a Highest dialog through for a High order. That
    # is the grade-prefix trap _names_agree (see mass_purchase_matches) was
    # written to close, and this is the one place in the file where real Alz
    # moves with no further confirmation.
    #
    # Equality is not available here: dialog["text"] is the whole joined
    # dialog, not an item name. So instead every OTHER item on the favourites
    # whose key CONTAINS this one -- i.e. every grade this order could be
    # confused with -- is checked, and the order is refused if one of them is
    # what the dialog actually names.
    wanted = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", offer.name)))
    shown = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", dialog["text"])))
    confusable = sorted(
        {key for key in (_floor_key(item_name(other))
                         for other in FAVOURITE_SLOTS.values())
         if key != wanted and wanted and wanted in key},
        key=len, reverse=True)
    if any(key in shown for key in confusable):
        halt_buying(
            f"the Confirm Purchase dialog names a different grade than "
            f"{offer.name!r} -- the favourite slots and the shop disagree")
        return False, (f"the dialog names a longer grade than {offer.name!r}: "
                       f"{dialog['text'][:80]!r}")
    if wanted and wanted not in shown:
        # PERMANENT. Every other refusal in this function is a "not this row,
        # not right now" -- sold out, price moved, dialog missing -- and
        # retrying is the right answer. This one is different in kind: the
        # dialog names what the game is ABOUT to sell us, one click from
        # committing, and it is not what we chose. That means the mapping
        # between what we searched for and what we are looking at is wrong, and
        # no amount of retrying fixes a wrong map -- it just gives it more
        # chances to buy the wrong thing.
        #
        # Measured: a stale FAVOURITE_SLOTS had slot 3 returning SIGMetal
        # Headpiece while the script believed it was Upgrade Core(Highest).
        # That was caught earlier, at the search; if it ever gets this far,
        # buying stops for the run rather than being tried again.
        halt_buying(f"the Confirm Purchase dialog named something other than "
                    f"{offer.name!r} -- the item mapping cannot be trusted")
        return refuse(f"the dialog does not name {offer.name!r} "
                      f"(it reads {dialog['text'][:70]!r})")

    say(f"  confirming {take} x {offer.name!r} "
        f"({take * offer.pack} item(s)) at {expected:,} Alz")
    bx, by = dialog["buy"]
    move_mouse(bx, by + 60)
    time.sleep(0.25)
    click(bx, by)
    time.sleep(2.5)
    park_cursor()
    time.sleep(1.0)

    after = get_alz(grab()) or None
    if before and after and before - after == expected:
        record("buy.completed", item=offer.name, price=expected,
               pack=offer.pack * take, took=take)
        # The spend recorded is what the BALANCE moved by, which this branch
        # has just proved equals the listed price. Recording the measured
        # figure rather than the expected one keeps the ledger tied to the
        # account rather than to what the script believed.
        note_purchase(offer.name, expected, before - after,
                      offer.pack * take)
        return True, ""
    if purchase_confirm() is not None:
        # Still open: the listing went while we were deciding.
        say("  the dialog would not complete - the listing was taken by "
            "somebody else. Cancelling and searching again.")
        dialog = purchase_confirm()
        if dialog and dialog["cancel"]:
            click(*dialog["cancel"])
            time.sleep(1.0)
        return False, "sold out before the purchase completed"
    if before and after and before == after:
        return False, "the dialog closed but no Alz was spent"

    if not before or not after:
        # The dialog is GONE -- the branch above returned if it was still up --
        # so the confirm click was taken and the Alz has moved. What failed is
        # the measurement: get_alz returns 0 when the balance does not read,
        # which `or None` turns into None here, and one occluded frame or one
        # misread glyph in a twelve-digit figure is enough.
        #
        # This used to fall through to the "balance moved 0" refusal, which
        # spent the money and wrote NOTHING to the ledger. That is the worst of
        # the three possible outcomes, because purchase_cost_basis reads the
        # ledger: a purchase missing from it drags the average cost DOWN, and
        # the Cores it produced are then relisted below what was paid for them
        # -- the single thing the never-below-cost floor exists to prevent.
        #
        # So it is recorded, at the listed price, and marked as unmeasured. The
        # two ways to be wrong are not symmetric: recording a purchase that did
        # not happen puts the floor too HIGH, which stops a sale; failing to
        # record one that did puts it too LOW, which makes a loss. The ledger
        # errs toward the first.
        say("  the Alz balance did not read, so this purchase is recorded at "
            "its listed price rather than a measured one.")
        record("buy.unmeasured", item=offer.name, price=expected,
               pack=offer.pack * take, before=before, after=after)
        note_purchase(offer.name, expected, expected, offer.pack * take,
                      note="unmeasured: the Alz balance did not read")
        return True, ""

    return False, (f"the balance moved {(before or 0) - (after or 0):,}, "
                   f"not the {expected:,} expected")


def buy_cheapest_set_detail(item_slot: int,
                            threshold: "int | None" = None,
                            attempts: int = BUY_RETRY_ATTEMPTS,
                            verbose: bool = True,
                            still_wanted: int | None = None) -> dict:
    """Buy one Set listing of the item in `item_slot`, if it is worth it.

    Compares the loose item against its Set PER ITEM, and buys the cheapest
    Set listing only when the saving clears `threshold`.

    Returns the offer that was taken as well as the verdict, because a caller
    accumulating toward a target needs to know how many Sets the bundle held --
    a listing is "X 62", not one Set, so counting purchases would count wrong.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    # Resolved from the ITEM, not bound at import. A default argument is
    # evaluated once when the function is defined, so a per-item table could
    # never reach it -- every caller would silently get the global floor.
    if threshold is None:
        threshold = price_diff_floor_for(FAVOURITE_SLOTS.get(item_slot, ""))

    def outcome(bought: bool, why: str = "", offer=None, saving=None,
                taken: int = 0) -> dict:
        # `taken` is ITEMS, not listings and not orders. One order can now take
        # several identical listings from the same row -- "X 1" with 48 on
        # offer is 48 Sets in a single purchase -- so a caller accumulating
        # toward a target cannot infer it from the offer alone.
        return {"bought": bought, "why": why, "offer": offer,
                "saving": saving, "slot": item_slot, "taken": taken}

    set_slot = favourite_set_slot(item_slot)
    if set_slot is None:
        say(f"Slot {item_slot} has no paired Set slot; nothing to compare.")
        return outcome(False, "no paired Set slot")

    for attempt in range(1, attempts + 1):
        if attempt > 1:
            say(f"\n=== buy attempt {attempt}/{attempts} ===")

        # BOTH sides are searched again on every attempt, not just the Set.
        #
        # The loose-item price used to be read once, before the loop, and
        # reused. A retry only happens because the row we wanted was bought out
        # from under us -- which is precisely the moment the market is moving,
        # and precisely when a baseline measured a minute ago is least worth
        # trusting. Judging a fresh Set price against a stale item price can
        # invent a saving that no longer exists.
        #
        # The search is also what makes "row 1" mean anything: the Purchase tab
        # never clears its results, so rows left on screen from an earlier
        # search look exactly like fresh ones.
        item_best = cheapest_listing(
            run_favourite_search(item_slot, verbose=verbose))
        if item_best is None:
            say("No offers for the loose item, so there is nothing to compare "
                "against - refusing to buy blind.")
            return outcome(False, "no offers for the loose item")

        set_offers = run_favourite_search(set_slot, verbose=verbose)
        # Row 1 on both sides: the table is sorted Price: Low to High, so the
        # first row IS the cheapest, and comparing anything else would judge
        # the deal on a listing the buy is not going to take.
        set_best = cheapest_listing(set_offers)
        if set_best is None:
            say("No Set offers on screen.")
            return outcome(False, "no Set offers")

        saving = item_best.unit - set_best.unit
        say(f"  item {item_best.unit:>12,.2f}/each   "
            f"set {set_best.unit:>12,.2f}/each   "
            f"saving {saving:>10,.2f}/each")
        if saving < threshold:
            say(f"  saving is under the {threshold:,} threshold"
                + (" (a per-item floor)"
                   if threshold != PRICE_DIFF_FLOOR else "")
                + " - not buying.")
            return outcome(False, f"saving {saving:,.2f} under threshold",
                           saving=saving)

        target = set_best          # row 1, the same row the saving was judged on

        # Is this ONE order too big for what is left to buy? The answer is
        # to stop, not to take row 2.
        #
        # Measured against the EFFECTIVE target, not the RESTOCK_TARGET
        # constant. still_wanted is (BUY_TARGET - bought), and --buy-target
        # moves BUY_TARGET; reading the constant here put the two on different
        # scales and broke the ceiling in BOTH directions. With
        # --buy-target 250, still_wanted stays >= 100 until 150 are held, so
        # every order up to that point counted as "first" and the exemption
        # re-opened the 428,142,429 Alz single-click runaway this rule exists
        # to prevent. With --buy-target 50, still_wanted starts below 100 so
        # the first order was never exempt, and a market of large bundles
        # would never be traded at all.
        target_now = BUY_TARGET or RESTOCK_TARGET
        first_order = (still_wanted is not None
                       and still_wanted >= target_now)
        # Two limits, and which one applies depends on where the holding is.
        #
        #   below RESTOCK_TARGET  the minimum is not met, and meeting it comes
        #                         first. Any bundle is taken, whatever its
        #                         size: 100 held + a 999 bundle is allowed,
        #                         because refusing leaves the shop short and
        #                         the market may offer nothing smaller.
        #
        #   at or above it        size discipline applies: the total including
        #                         this order must stay within BUY_MAXIMUM.
        #                         240 + 999 is refused, 240 + 200 is taken.
        # `still_wanted` counts down from BUY_MAXIMUM (buy_sets_until runs to
        # the soft maximum, not to the minimum -- stopping at 200 would make
        # "at 240, next bundle is 200, buy it" unreachable), so `held` is
        # simply how many are already in the bag.
        held = (target_now - still_wanted) if still_wanted is not None else 0
        below_minimum = held < RESTOCK_TARGET
        if (BUY_NEVER_EXCEED_TARGET and still_wanted is not None
                and not below_minimum
                and held + target.pack > BUY_MAXIMUM):
            say(f"  {held} Sets held, which meets the {RESTOCK_TARGET} "
                f"minimum; "
                f"row 1 holds {target.pack} and {held + target.pack} would "
                f"pass the {BUY_MAXIMUM} maximum - stopping here.")
            return outcome(False,
                           f"row 1 bundle of {target.pack} would take the "
                           f"total to {held + target.pack}, past the "
                           f"{BUY_MAXIMUM} maximum",
                           saving=saving)

        # How many of this row's identical listings to take in one order.
        #
        # The Purchase table's middle column is a COUNT: "Force Core Set
        # (Highest) X 1" with 48 beside it is forty-eight separate one-Set
        # listings at the same price, and the Confirm Purchase dialog will sell
        # all of them in a single transaction. Taking one and searching again
        # cost 48 searches and 48 dialogs to drain that row, and the restock's
        # buy budget ran out long before the target.
        #
        # Rounded DOWN against the target, not up: `first_order` already allows
        # one deliberate overshoot when nothing is held, and past that the
        # never-exceed rule applies to the whole order rather than to the
        # bundle size alone.
        # How many of this row's identical listings to take.
        #
        # Rounded UP against what is still wanted, because the target is a
        # minimum: taking one listing FEWER than it takes to reach it just
        # means another search, another dialog and another cycle of the same
        # decision.
        #
        # Below the minimum nothing bounds this but the row itself -- that is
        # what "hard limit" means. At or above it, the order is trimmed so the
        # total lands within BUY_MAXIMUM.
        want = max(1, target.available)
        if still_wanted is not None:
            want = min(want, max(1, -(-still_wanted // max(1, target.pack))))
        if not below_minimum:
            room = max(0, BUY_MAXIMUM - held)
            want = max(1, min(want, max(1, room // max(1, target.pack))))

        # Can this actually be paid for? Checked before the click, because a
        # refusal from the game is harder to read than a number we already
        # have -- and because running out is permanent for this run.
        # Priced for the WHOLE order, not one listing of it. Checking
        # affordability against a single 190,190 listing and then spending
        # 9,129,120 on forty-eight of them is how a run discovers it is out of
        # Alz from the game's refusal rather than from a number it already had.
        order_price = target.price * want
        can_pay = affordable(order_price)
        if can_pay is False:
            halt_buying(f"cannot afford {want} x {target.name!r} at "
                        f"{order_price:,} Alz")
            return outcome(False, "out of Alz", saving=saving)

        say(f"  buying row {target.row}: {target.name!r} x{want} of "
            f"{target.available} on offer -> {want * target.pack} Sets for "
            f"{order_price:,} ({target.unit:,.2f} each)")
        bought, why = buy_offer(target, want=want, verbose=verbose)
        if bought:
            say(f"  BOUGHT {want * target.pack} x {target.name!r} for "
                f"{order_price:,} Alz.")
            return outcome(True, offer=target, saving=saving,
                           taken=want * target.pack)
        say(f"  not bought: {why}")
        if "sold out" not in why:
            return outcome(False, why, saving=saving)

    say(f"Gave up after {attempts} attempts - the listings kept selling first.")
    return outcome(False, "the listings kept selling first")


def buy_cheapest_set(item_slot: int,
                     threshold: "int | None" = None,
                     attempts: int = BUY_RETRY_ATTEMPTS,
                     verbose: bool = True) -> bool:
    """Whether a Set of `item_slot` was bought. See buy_cheapest_set_detail."""
    return buy_cheapest_set_detail(item_slot, threshold=threshold,
                                   attempts=attempts, verbose=verbose)["bought"]


# --------------------------------------------------------------------------
# RESTOCK: sold out -> buy Sets -> convert to Cores -> list them
# --------------------------------------------------------------------------
#
# Enabled by --buy. The three stages are deliberately separate functions, so a
# failure in one is visible as itself rather than as "restock failed", and so
# each can be driven by hand while the others are trusted.
#
#   1. a managed Core with no rows left in the shop has sold out, so buy Sets
#      until RESTOCK_TARGET are held -- always row 1, re-searching every time
#   2. leave the Agent Shop, press N for the vendor, convert Sets -> Cores
#   3. list the Cores, which is what FREES THE INVENTORY AGAIN
#
# Step 3 is not merely bookkeeping. Cores do not stack: 250 of them occupy 250
# slots, which is four tabs' worth, and the inventory would be full long before
# a 250-Set purchase finished converting. Listing after each batch returns
# those slots, which is what lets the loop run to completion at all -- so the
# convert/list pair repeats rather than converting everything up front.
# The HARD minimum. A restock keeps buying until it holds at least this
# many Sets, and while it is short of them NO order is refused for being too
# big -- see BUY_MAXIMUM. Being under-stocked is the failure that costs a
# trading day; being over-stocked costs some Alz sitting in Cores.
RESTOCK_TARGET = 200

# The SOFT maximum. Once the minimum is met, an order is only taken while the
# total including it stays within this. It is soft in exactly one sense: an
# order taken to REACH the minimum may carry the holding straight past it
# (100 + 999 = 1,099 is allowed), because the hard limit wins. Nothing else may.
BUY_MAXIMUM = 500
# How many Sets a restock accumulates before converting.
#
# Not the same as CONVERT_QUANTITY: a shop row holds 250 and a conversion asks
# for 250, but the amount of CAPITAL a single restock commits is a separate
# decision. 100 Sets is roughly 43M at the Ultimate price -- a position worth
# taking without tying up the balance, where 250 was 107M and a 999 bundle was
# 428M.
#
# The first order of a restock is exempt from the ceiling (see
# BUY_NEVER_EXCEED_TARGET): row 1 is the cheapest per item, and refusing a
# large bundle when nothing is held means never trading at all.

# Ceiling on the number of purchase TRANSACTIONS in one restock -- not on the
# quantity and not on the money, both of which BUY_MAXIMUM bounds.
#
# Largely vestigial since the Confirm Purchase dialog's quantity field was
# wired up on 2026-08-08. Each order used to take exactly ONE listing, so
# accumulating a target from bundles genuinely needed a dozen; now a single
# order can drain a whole row (48 listings at once, measured), and reaching the
# minimum usually takes one to four.
#
# What it bounds today is TIME: an order is two favourite searches, a dialog
# and a confirm, so fifteen of them is ten to fifteen minutes of a cycle. The
# case where it still binds is a market offering only tiny lots -- fifteen
# orders of two Sets each falls well short of the minimum. That is no longer a
# failure: the carry registry remembers what was bought and the next cycle
# resumes rather than starting over.
RESTOCK_MAX_BUYS = 15

# How far past what is still needed a SINGLE order may go.
#
# Buying stops at the first order that reaches the target, and it always takes
# row 1 whatever size that row is -- so a 999-Set bundle can land on top of an
# almost-met target. Measured on 2026-08-07: with 213 of 250 Sets held, row 1
# was a 999 bundle at 428,142,429 Alz and the run took it. Per item it was a
# fine trade (16,429 saving each); as a position it was 82% of everything spent
# that session, committed in one click.
#
# The rule, as it stands after 2026-08-08: RESTOCK_TARGET is a HARD MINIMUM
# and BUY_MAXIMUM a soft maximum. Below the minimum any bundle is taken --
# meeting it comes first, and refusing a large row can leave the shop empty.
# At or above it, an order is taken only while the total including it stays
# within BUY_MAXIMUM.
#
# The earlier version held every order to the target itself, which refused the
# order that would FINISH a restock: live that evening, 53 of 100 held and row
# 1 offering exactly the 76 needed, the run declined and stopped at 53.
#
# Declined rather than stepping to row 2, because "always buy row 1" is what
# keeps row 1 meaning the cheapest -- taking row 2 would quietly reintroduce
# the bug that once paid 8,614,760 more to save 38 Alz.
#
# Measured on 2026-08-07, before this existed: with 213 of 250 Sets held, row 1
# was a 999 bundle at 428,142,429 Alz and the run took it -- 1,212 Sets against
# a target of 250, and 82% of everything spent that session committed in one
# click. Per item it was a fine trade; as a position it was not one anybody
# chose.
#
# The cost is that a target is rarely met exactly: 62-Set bundles stop at 248.
# That is the intended trade -- slightly under is a rounding error, four
# hundred million over is not.
#
# ONE exception, and it is what makes the rule workable: the FIRST order of a
# restock is taken whatever its size. Row 1 is the cheapest per item, and if
# the market is only offering big bundles then refusing them means never
# trading at all -- the restock would decline every cycle and the shop would
# stay empty. Having nothing is worse than having a surplus. Every order after
# the first is held to the target, which is where the runaway actually
# happened: 213 held, then a 999 bundle on top.
BUY_NEVER_EXCEED_TARGET = True

# Superseded by RESTOCK_TARGET (hard minimum) and BUY_MAXIMUM (soft maximum),
# which say the same thing in absolute numbers instead of a multiplier. Kept
# defined because a factor is easier to reason about when the two limits are
# being retuned, and removing a name that tests import is a separate change.
BUY_OVERSHOOT_FACTOR = BUY_MAXIMUM / RESTOCK_TARGET

# Runaway guard on convert/list rounds -- NOT the expected number of them.
#
# What actually ends the loop is progress: it stops when every bought Set has
# been listed, or when a round converts nothing. Counting rounds ahead of time
# does not work, and the arithmetic that looked obvious was wrong twice over:
#
#   * a purchase OVERSHOOTS. Buying stops at the first order that reaches the
#     target and a Set stacks to 999, so a 250 target routinely lands near
#     1,250 and can be a single 999 bundle.
#   * a round does not convert CONVERT_QUANTITY. It converts as many as there
#     are free inventory slots, which is 63 on a tab -- so 999 Sets is sixteen
#     rounds, not four.
#
# A budget derived from "bought / 250" therefore truncated at 378 of 999 and
# left 621 Sets stranded in the bag, silently. This cap only exists so a round
# that makes one unit of progress forever cannot run all night.
RESTOCK_MAX_ROUNDS = 40

# Candidate inventory slots tried when listing the converted Cores. Occupancy
# cannot tell a Core from a Set, so the first guess is not always right -- see
# core_slot_candidates. Bounded because each miss costs a load and a refusal.
CORE_SLOT_TRIES = 4

# The most a single Set stack holds, which is why the overshoot happens at all.
SET_STACK_MAX = 999

# Whether the resupply runs BEFORE the relisting or after it.
#
# Before, as of 2026-08-08. Restocking last meant the rows it created were
# absent from the snapshot the relist worked from, so they were priced once at
# creation and never repriced -- which is the whole reason widen_for_restocks
# exists, a mechanism that anchors on max(rows) rather than where listings
# actually land and grows monotonically because nothing gave it back.
#
# The cost of going first: sold rows have not been collected yet, so they still
# hold a row in `receive` state and the capacity check sees the shop as fuller
# than it will be. That errs toward declining a restock that would have fitted,
# never the reverse, and core_row_counts already ignores `receive` rows so a
# sold-out Core is still correctly detected.
RESTOCK_BEFORE_RELIST = True

# --------------------------------------------------------------------------
# SERVER WAR SCHEDULE
# --------------------------------------------------------------------------
#
# Wars start every three hours on the SERVER clock, at the same times every day
# of the week. From the server's published schedule for LVL 200-200:
#
#     01:00 IP    04:00 TG    07:00 MC    10:00 TG
#     13:00 IP    16:00 TG    19:00 MC    22:00 TG
#
# (IP = Ingens Proelium, TG = Tierra Gloriosa, MC = Memoria Chrysos. Sunday
# 16:00 is a Flag War, still a TG.) Which war it is does not matter here -- the
# lag does, and it arrives when one ENDS and the server empties the
# battlefield at once.
WAR_START_HOURS = (1, 4, 7, 10, 13, 16, 19, 22)
WAR_MINUTES = 30

# Stop this long BEFORE the war ends, and stay stopped this long in total.
# So a war ending at 04:30 means quiet from 04:29 to 04:34.
WAR_QUIET_BEFORE_END = 60
WAR_QUIET_SECONDS = 300

# How long one relist row might take, so a row is not STARTED that would run
# into the quiet window. Measured on the 17:24 run of 2026-08-08: 10m35s for
# five rows, ~127s each. Rounded up, because overrunning the window is the
# thing this exists to prevent and finishing early costs nothing.
WAR_ROW_ALLOWANCE = 150.0

# The same question at a cycle boundary. A cycle is many rows, so waiting for
# a whole one to fit would idle for far longer than the window itself; the
# check before each ROW is what actually keeps work out of it, and this only
# stops a cycle beginning inside one.
WAR_CYCLE_ALLOWANCE = 0.0

# The server clock in the game HUD, bottom left: gold "HH:MM" on a dark panel.
#
# Narrow on purpose. Measured across 100 saved frames: this crop reads at
# conf 96, and widening it by ten pixels in any direction reads NOTHING --
# Tesseract's segmentation changes with the crop, the same way
# PURCHASE_DLG_QTY_VALUE does. Do not tidy the numbers.
SERVER_CLOCK_REGION = (20, 1275, 120, 1310)

# A reading is HH:MM with no seconds, so a clock that says 04:29 is anywhere in
# that minute. Every decision below therefore assumes the LATEST time the
# reading allows, which errs toward stopping early -- the safe direction, and
# at most one minute of lost work.
SERVER_CLOCK_UNCERTAINTY = 59

# How long a reading stays good before it is taken again. time.monotonic()
# does the timekeeping in between; this only guards against the server's clock
# moving under us, or a reading having been wrong.
SERVER_CLOCK_RESYNC = 1800.0

# An arbitrary fixed date for the server clock to hang off.
#
# NOT today's date, and that is the point: this machine keeps bad time, so the
# war schedule is built to never ask it. Only the time of day matters -- the
# schedule is the same every day of the week -- and this exists solely to give
# war_quiet_window a datetime it can add days to. A leap year with no daylight
# saving anywhere in it, so no date arithmetic below can land on a missing or
# repeated hour.
SERVER_CLOCK_EPOCH = _dt.datetime(2024, 1, 3)

# Does a relist refuse to price below what the stock COST?
#
# Off. Turned off deliberately on 2026-08-08: "if we bought at 100k and current
# lowest price is 99k, we use 100k floor -- remove this". The operator would
# rather move stock at the market than hold it waiting for a price that may not
# come back, which is the same call as "I rather move more revenue than not
# moving at all".
#
# What it protected against is real and is written up in purchase_cost_basis:
# Force Core (Ultimate) was bought at 428,571 a Set and the loose Core fell to
# 386,831 within the hour, and "take the lowest current price" would then sell
# the whole holding at a loss one relist at a time. With this off, that is an
# outcome the operator has chosen rather than one the script fell into. The 5%
# ratchet still limits how FAST a price can fall, and --min-price still sets a
# hard bound per run.
#
# What this does NOT touch, in either position:
#
#   ITEM_PRICE_FLOORS   the operator's own floors, VIP items included. Those
#                       are absolute and are not a flag. listing_floor still
#                       applies them, and the higher of the two still wins
#                       whenever this is on.
#   profit reporting    cost_of_goods_sold reads purchase_cost_basis directly.
#                       Accounting says what was paid regardless of what
#                       pricing does with it.
COST_FLOOR_ON_RELIST = False

BUY_ENABLED = False
# The runtime target, separate from the RESTOCK_TARGET constant so --buy
# can change it without shadowing the default the CLI advertises.
# The runtime accumulation target: the SOFT maximum, not the minimum.
# A restock keeps buying while the total is under this, and the hard minimum
# decides whether an over-sized bundle may be taken on the way.
BUY_TARGET = BUY_MAXIMUM

# Rows this run's restocks have ADDED to the shop.
#
# A restock lists Cores into the LOWEST EMPTY row. The row that sold out is
# empty -- that is why the restock ran -- so the first new listing usually
# lands back inside the range being swept. The rest do not: a 1,250-Set
# purchase makes five listings, and once the freed row is taken the remaining
# four go to the end of the shop.
#
# `--relist-rows 6-17` would then never touch them again: they would sit at
# whatever the market was the moment they were created, never repriced, for
# the rest of the run. So the sweep widens by however many rows were made.
BUY_ADDED_ROWS = 0

# What the last full sweep concluded, and when.
#
# One enumerate_listings is THREE traversals of the shop -- to the bottom to
# find the end, back to the top, then down again in steps -- and it doubles if
# the step has to fall back. Doing that every cycle to re-learn a fact that has
# not changed is most of the time an unattended run spends.
#
# So the answer is remembered. It is only re-taken when it might have moved:
# after a restock lists something, or after CORE_STOCK_TTL, whichever comes
# first. A Core VISIBLE on the first screen never needs a sweep at all -- that
# is checked before this cache is even consulted.
CORE_STOCK_TTL = 600.0
_UNLISTED_CACHE: dict | None = None


def note_unlisted(slots: list[int]) -> None:
    """Remember which Cores a full sweep found no row for."""
    global _UNLISTED_CACHE
    _UNLISTED_CACHE = {"slots": list(slots), "at": time.monotonic()}


def forget_unlisted() -> None:
    """Drop the cache: the shop has changed and the answer may have too."""
    global _UNLISTED_CACHE
    _UNLISTED_CACHE = None


# Sets a restock has PAID FOR but not yet turned into a listed Core.
#
# A restock that buys and then cannot list -- the vendor unreachable, the Agent
# Shop refusing to close, the round guard tripping, an exception anywhere in
# between -- leaves Sets in the bag. Nothing about the SHOP changes in that
# case: the Core is still unlisted, so the next cycle asks the same question,
# gets the same answer, and buys a SECOND target on top of the first. At about
# 43M a target and RESTOCK_MAX_BUYS orders a pass, that is the most expensive
# path in this file, and it repeats every cycle for as long as the underlying
# problem lasts. Neither halt_buying nor the row-capacity pause catches it --
# the first only fires on an unaffordable price or a bad name, and the second
# only counts rows the shop already has.
#
# So what was bought is remembered per favourite slot, and a restock that finds
# stock carried over CONVERTS AND LISTS IT instead of buying more. The bag is
# the record; this is just the script's knowledge of it.
#
# Deliberately process-lifetime and not persisted: a new run cannot know
# whether the bag was emptied by hand between runs, and a stale carry would
# stop a legitimate purchase forever. Within one run it is authoritative.
_CARRIED_SETS: dict[int, int] = {}


def carried_sets(slot: int) -> int:
    """Sets bought for `slot` that have not become listed Cores yet."""
    return max(0, int(_CARRIED_SETS.get(slot, 0)))


def note_carried_sets(slot: int, count: int) -> None:
    """Record that `count` Sets for `slot` are in the bag, unlisted."""
    if count > 0:
        _CARRIED_SETS[slot] = int(count)
    else:
        _CARRIED_SETS.pop(slot, None)


def clear_carried(slot: int) -> None:
    """Everything bought for `slot` has been listed."""
    _CARRIED_SETS.pop(slot, None)


def carried_total() -> int:
    """Sets held across every slot that a restock still owes a listing for."""
    return sum(carried_sets(slot) for slot in list(_CARRIED_SETS))


def cached_unlisted(missing: list[int]) -> "list[int] | None":
    """The remembered verdict for `missing`, or None if a sweep is needed.

    Only usable when the cache covers every Core being asked about: a Core the
    last sweep never considered has no remembered answer, and guessing one
    would be the difference between buying and not.
    """
    if _UNLISTED_CACHE is None:
        return None
    if time.monotonic() - _UNLISTED_CACHE["at"] > CORE_STOCK_TTL:
        return None
    known = set(_UNLISTED_CACHE["slots"])
    # Everything now missing from the first screen must have been accounted
    # for by that sweep -- either it said unlisted, or it said listed further
    # down. It can only say the former; the latter is "not in this list".
    return [s for s in missing if s in known]


def note_rows_added(count: int) -> None:
    """Widen future sweeps by `count` rows, so new listings keep being priced."""
    global BUY_ADDED_ROWS
    if count > 0:
        BUY_ADDED_ROWS += count


def note_shop_depth(rows_now: int, swept_to: int) -> None:
    """Give back widening the shop has since absorbed.

    BUY_ADDED_ROWS only ever grew, and nothing gave it back when the rows it
    was tracking sold. But listings CONSOLIDATE UPWARD -- this file measured
    Siena's Unbinding Stone going row 24 -> 17 -> 12 over three cycles -- so
    the rows a restock added are reabsorbed within a few cycles while the
    counter still claims them.

    The cost is not cosmetic. Once the widened range runs past the ten visible
    rows, `beyond` is non-empty, `scrolling` is True, and EVERY cycle pays a
    full enumerate_listings -- three traversals of the shop, about two minutes
    of OCR -- to address rows that no longer exist. The widened rows are then
    silently skipped as `added_rows`, so the cost never appears as an error.

    Called with what the shop actually holds after a sweep. Anything the sweep
    reached that is not there any more is not worth widening for.
    """
    global BUY_ADDED_ROWS
    if rows_now < 0 or swept_to <= 0:
        return
    # Only ever shrinks. Growth stays note_rows_added's job, so a bad read here
    # cannot invent extra rows to sweep -- it can only stop sweeping ones that
    # are gone, which the next restock re-adds anyway.
    spare = max(0, swept_to - rows_now)
    if spare and BUY_ADDED_ROWS:
        BUY_ADDED_ROWS = max(0, BUY_ADDED_ROWS - spare)


def widen_for_restocks(rows: "list[int] | None",
                       available: int) -> "tuple[list[int], int]":
    """Extend a row list to cover rows added by restocks. (rows, how many).

    Clamped to what the shop actually holds. Asking for a row that does not
    exist fails the WHOLE batch -- and a widening meant to catch new listings
    must never be the thing that stops the sweep.
    """
    if not rows or not BUY_ADDED_ROWS:
        return list(rows or []), 0
    top = max(rows)
    extra = [r for r in range(top + 1, top + 1 + BUY_ADDED_ROWS)
             if r <= available]
    if not extra:
        return list(rows), 0
    return sorted(set(rows) | set(extra)), len(extra)

# Which Cores the restock pipeline is allowed to buy. One line per type, and
# every one of them OFF unless it has been switched on deliberately.
#
# A table rather than a flag because this decides where money goes. Adding a
# favourite slot should not quietly enrol it in automatic buying, and reading
# this list should answer "what can this run spend on?" without tracing code.
#
# The keys are the game's own spellings, taken from FAVOURITE_SLOTS -- including
# its inconsistency about the space before the bracket. A key that does not
# match a managed Core is a mistake, not a no-op, and enabled_buying_slots()
# raises rather than silently ignoring it.
ENABLE_BUYING: dict[str, bool] = {
    "Force Core(High)":        True,
    "Force Core(Highest)":     True,
    "Force Core (Ultimate)":   True,
    "Upgrade Core(Highest)":   False,
    "Upgrade Core (Ultimate)": True,
}


def enabled_buying_slots() -> tuple[int, ...]:
    """Favourite slots ENABLE_BUYING switches on, as slot numbers.

    Resolved through favourite_for, so the table is checked against the real
    slot map every time rather than trusted. A typo in a key would otherwise
    read as "this Core is disabled", which is the quiet direction: the restock
    silently never fires and the shop just runs dry.
    """
    out = []
    for name, on in ENABLE_BUYING.items():
        slot = favourite_for(name)
        if slot is None or slot not in managed_core_slots():
            raise ValueError(
                f"ENABLE_BUYING names {name!r}, which is not a managed Core. "
                "Managed: "
                + ", ".join(FAVOURITE_SLOTS[s] for s in managed_core_slots()))
        if on:
            out.append(slot)
    return tuple(sorted(out))


# Set once the account cannot afford what it is trying to buy, and never
# cleared. Running out of Alz is not a transient condition the way a sold-out
# row is: the money only comes back when something SELLS, and until then every
# further attempt walks the whole pipeline -- switch tabs, run two searches,
# read the market -- to reach the same refusal. Worse, a half-funded restock
# buys some Sets and cannot buy the rest, leaving them in the bag for a later
# cycle to buy MORE on top of.
#
# Process-lifetime, deliberately. A run that stops buying says so once and
# keeps relisting, which is the useful half of the job; the next run starts
# fresh and re-checks.
BUY_HALTED = False
BUY_HALT_REASON = ""


def halt_buying(reason: str) -> None:
    """Stop all further buying for the rest of this run."""
    global BUY_HALTED, BUY_HALT_REASON
    if not BUY_HALTED:
        BUY_HALTED = True
        BUY_HALT_REASON = reason
        print(f"\nBUYING HALTED for the rest of this run: {reason}")
        # The advice has to follow the cause. It used to say "restart once
        # there is Alz again" unconditionally, which is nonsense for a halt
        # caused by the item mapping being wrong -- and following it would
        # restart straight back into buying the wrong thing.
        print("Relisting continues, untouched.")
        if "afford" in reason:
            print("Restart once something has sold and there is Alz again.")
        else:
            print("Check FAVOURITE_SLOTS against the game's saved searches "
                  "before restarting: a name mismatch means the script and "
                  "the shop disagree about what is being bought.")
        record("buy.halted", reason=reason)


def affordable(price: int, source: "Image.Image | None" = None) -> bool | None:
    """Whether `price` can be paid. None when the balance cannot be read.

    None rather than False on an unreadable balance, and the caller proceeds:
    halting is PERMANENT, so doing it on a misread would silently disable
    buying for a whole run. A genuinely short purchase fails at the game's own
    hands a moment later, which is recoverable; a false halt is not.
    """
    try:
        held = get_alz(source if source is not None else grab())
    except Exception:  # noqa: BLE001 - an unreadable balance is not a verdict
        return None
    if not held:
        return None
    return held >= price


def leave_for_restock(verbose: bool = True) -> None:
    """Put the game back in its default state before the refill starts.

    Relisting and refilling are different jobs in different windows: relisting
    works the Agent Shop's Register tab, refilling works its Purchase tab and
    then the NPC vendor two windows away. Running the second straight out of
    the first left the Agent Shop open for the whole restock -- and an open
    Trade window is exactly what makes a later find_npc() fail, because it
    covers the NPC.

    Everything downstream reopens what it needs: open_purchase_tab opens the
    Trade window when it is shut, and the conversion closes it again anyway to
    reach the vendor. So the only cost is one close, and what is bought is a
    known state at the boundary -- if the refill fails, it fails from the
    default state rather than from whatever the last row left behind.

    Never raises. A refill that cannot start is a missed opportunity; a relist
    batch turned into a failure by its own tidying is not.
    """
    try:
        if trade_window_open():
            leave_shop(verbose=verbose)
    except Exception as exc:  # noqa: BLE001 - tidying must not fail a batch
        if verbose:
            print(f"  (could not close the Agent Shop first: {exc})")


# The client's "Disconnected" modal. Measured from the live frame of the
# 2026-08-08 04:31 drop: the title reads at (1280, 576) and its OK button at
# (1281, 817), both at 96% confidence.
#
# Centred, so the region deliberately excludes the chat panel at the bottom
# left -- players type "disconnected" at each other constantly, and a chat line
# must never stop a run.
DISCONNECT_REGION = (980, 545, 1590, 860)


def game_disconnected(source: "Image.Image | None" = None) -> bool:
    """True when the client is showing its disconnect modal.

    TWO words are required, not one. "Disconnected" alone appears in the title
    AND in the body, but it also appears in the game's own system messages; the
    pairing with "server" is what makes this specific to the modal. Being wrong
    in this direction is expensive -- a false positive stops an otherwise
    healthy unattended run.
    """
    try:
        shot = source if source is not None else grab()
        words = [w.text.casefold()
                 for w in find_words(shot, DISCONNECT_REGION, 20)
                 if w.conf >= 60]
    except Exception:  # noqa: BLE001 - a detector must not raise
        return False
    text = " ".join(words)
    return "disconnected" in text and "server" in text


def restock_is_armed() -> bool:
    """Whether a relist sweep should do any restock work at all.

    Both halves matter, and the second is not redundant. With --buy given but
    every ENABLE_BUYING entry off there is nothing that COULD be bought, so the
    pass must cost NOTHING -- not "run and decide to do nothing", which still
    pays for a full table read every cycle. A shop of Cores, VIP memberships
    and Gem packs then relists at exactly the speed it did before this feature
    existed.
    """
    if BUY_HALTED:
        return False
    return bool(BUY_ENABLED) and bool(enabled_buying_slots())


def managed_core_slots() -> list[int]:
    """Favourite slots holding a loose Core that has a paired Set slot.

    These are the items the restock pipeline looks after. A Set slot is not one
    of them: buying a Set is the MEANS, and the Core is what gets listed.
    """
    out = []
    for slot, name in sorted(FAVOURITE_SLOTS.items()):
        if "set" in _floor_key(item_name(name)):
            # Belt and braces. favourite_set_slot() already returns None for a
            # Set slot -- a Set has no Set of its own -- so this line changes
            # nothing today. Kept because the cost of the pairing rule being
            # loosened later is that the pipeline buys Sets in order to list
            # Sets, which is the trade run backwards.
            continue
        if favourite_set_slot(slot) is not None:
            out.append(slot)
    return out


def core_row_counts(listings: list) -> dict[int, int]:
    """How many shop rows each managed Core occupies, keyed by favourite slot.

    Matched by EQUALITY on the folded name, never containment: "Force
    Core(Highest)" contains "Force Core(High)", and counting one as the other
    would report a sold-out item as still stocked -- or restock one that is
    fine. Every grade in this game is a prefix of another.
    """
    counts = {slot: 0 for slot in managed_core_slots()}
    for row in listings:
        # A row in `receive` state has SOLD. It carries its name until the
        # proceeds are collected, so counting it as stock reported a sold-out
        # Core as still listed -- and if the relist sweep does not reach that
        # row (a shop of 25 rows swept 6-17, say) it stays in `receive`
        # indefinitely and the Core is never restocked at all. rows_in_use,
        # which counts the same table for capacity, has always filtered on
        # action; this did not.
        #
        # Anything OTHER than an explicit "receive" counts, including an action
        # that did not read. That is the deliberate direction: treating an
        # unreadable row as sold would buy 250 Sets of something that never ran
        # out, while treating it as stocked costs at most one cycle of waiting.
        if getattr(row, "action", None) == "receive":
            continue
        # Strip any "X 30" pack marker before comparing. Equality is what keeps
        # the grades apart, but it is unforgiving of anything else on the line,
        # and a name that fails to match reads as SOLD OUT -- which spends
        # money restocking something that never ran out. The grade itself is
        # still compared exactly, so this cannot blur High into Highest.
        name = _PACK_ANYWHERE.sub(" ", getattr(row, "name", None) or "")
        # No `break` on the first hit. With equality at most one slot can
        # match, so breaking is only an optimisation -- but it also HIDES the
        # case where two match, which is exactly what a sloppier comparison
        # would cause. Counting them all turns that into a visible double
        # count instead of an answer that silently depends on dict order.
        for slot in counts:
            if _names_agree(name, FAVOURITE_SLOTS[slot]):
                counts[slot] += 1
    return counts


def unlisted_core_slots(listings: list) -> list[int]:
    """Managed Cores with no row anywhere in the shop.

    An ABSOLUTE count, and it must be taken over all thirty rows rather than
    the ten on screen -- "not in the top ten" is a completely different
    statement, and acting on it buys 250 Sets of something sitting on row 11.
    Measured on the live shop: three of five managed Cores read as absent from
    the visible table alone.

    Absolute rather than a before/after transition, because a Core that is
    enabled for buying and has NEVER been listed still wants stocking. The
    transition form could only ever restock something it had already seen, so a
    newly enabled Core would sit there forever.
    """
    return [slot for slot, n in sorted(core_row_counts(listings).items())
            if n < 1]


# The vendor's category tabs. Opening the shop with N lands on Normal, and the
# SET <-> CORE exchange block lives under DUNGEON -- so the grid coordinates
# describe a page that is not showing until this is clicked.
#
# Standing at Peddler Unon the strip has two tabs and the conversions are
# already up, which is how the grid was mapped in the first place and why this
# was missed: the first live conversion Alt+clicked a cell that was showing
# something else entirely.
VENDOR_TAB_REGION = (0, 150, 620, 240)
VENDOR_TABS = ("Normal", "Dungeon", "Repurchase")
CONVERT_VENDOR_TAB = "Dungeon"
# The raised top edge of the selected tab, same principle as the inventory
# strip. Measured on two live frames: the selected tab reads ~69 here against
# ~27-32 for the others, a 37-level gap, where a band over the labels
# themselves separates by about 12.
VENDOR_TAB_BAND = (176, 188)
VENDOR_TAB_MARGIN = 12.0


def vendor_tab_point(name: str,
                     source: "Image.Image | None" = None) -> "tuple[int, int] | None":
    """Where to click a vendor category tab, found by its label."""
    shot = source if source is not None else grab()
    hits = find_text(shot, name, VENDOR_TAB_REGION, 40.0)
    return hits[0].centre if hits else None


def active_vendor_tab(source: "Image.Image | None" = None) -> "str | None":
    """Which vendor category tab is selected, or None if it cannot be told.

    None rather than a guess: this gates a click into a shop where a plain
    click buys something outright.
    """
    shot = source if source is not None else grab()
    grey = shot.convert("L")
    px = grey.load()
    top, bottom = VENDOR_TAB_BAND
    scores = []
    for name in VENDOR_TABS:
        point = vendor_tab_point(name, shot)
        if point is None:
            continue
        cx = point[0]
        vals = [px[x, y]
                for x in range(max(0, cx - 25), min(grey.width, cx + 25))
                for y in range(top, bottom)]
        scores.append((sum(vals) / len(vals), name))
    if len(scores) < 2:
        return None
    scores.sort(reverse=True)
    if scores[0][0] - scores[1][0] < VENDOR_TAB_MARGIN:
        return None
    return scores[0][1]


def open_vendor_tab(name: str = CONVERT_VENDOR_TAB, timeout: float = 8.0,
                    verbose: bool = True) -> bool:
    """Select a vendor category tab and confirm it took.

    A tab click cannot buy anything -- it changes the page -- so this is the
    one click in the vendor window that is safe to make on a label match alone.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    # ALWAYS click, even when the tab already looks selected.
    #
    # The alternative is to trust active_vendor_tab() and skip -- but that
    # reading gates a click into a window where a PLAIN click buys outright,
    # and clicking a tab cannot buy anything at all. So the cheap, safe action
    # is unconditional and the expensive, unsafe one is what gets verified.
    # A misread that says "already on Dungeon" would otherwise leave the grid
    # showing the Normal page while the code believed otherwise, which is
    # exactly how the first live conversion Alt+clicked the wrong cell.
    # POLL for the label, do not read once.
    #
    # vendor_shop_open() is satisfied by the window's title, which draws before
    # its tabs do -- so a single find_text here reads a window that is open and
    # not yet finished. Measured on 2026-08-08: two consecutive cycles failed
    # at this line ("could not find the 'Dungeon' tab") on a window whose tabs
    # sit exactly where a golden frame of a WORKING conversion has them, at
    # (195, 205). Nothing had moved; the read was simply early.
    #
    # It cost 270 bought Sets two cycles of not being converted, and the tab
    # click itself is the one click in this window that cannot buy anything --
    # so waiting for it is free.
    point = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        point = vendor_tab_point(name)
        if point is not None:
            break
        time.sleep(0.4)
    if point is None:
        say(f"  could not find the {name!r} tab in the vendor Shop after "
            f"{timeout:g}s.")
        seen = [t for t in VENDOR_TABS if vendor_tab_point(t) is not None]
        say(f"  tabs that DID read: {seen or 'none'} -- if none, the window "
            f"was still drawing or is not the vendor Shop; if some, only this "
            f"label failed to OCR.")
        return False
    say(f"  selecting the {name} tab at {point}")
    click(*point)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if active_vendor_tab() == name:
            say(f"  {name} tab is showing.")
            return True
        time.sleep(0.4)
    say(f"  the {name} tab did not take.")
    return False


def open_npc_shop(timeout: float = 10.0, verbose: bool = True) -> bool:
    """Close the Agent Shop and open the vendor's Shop window with N.

    The vendor window is the same one, in the same place, whether it was opened
    by walking up to Peddler Unon or by the key -- which is what lets the
    conversion grid keep its fixed coordinates.

    The Agent Shop is closed FIRST and confirmed closed. Both windows open at
    once would have the Trade window covering the grid, and a click aimed at a
    conversion cell would land on whatever the Trade window is showing there.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    # Already open? Then do NOT press N. It is a TOGGLE: pressing it on an
    # open window closes it, and the poll below would then catch the window
    # mid-close, report it open, and hand a disappearing window to the tab
    # lookup.
    #
    # But close the Agent Shop first even so. The early return here used to
    # skip that entirely, leaving both windows up -- and this function's own
    # docstring says why that is wrong: the Trade window covers the grid, so a
    # click aimed at a conversion cell lands on whatever the Trade window
    # happens to be showing there. TRADE_REGION (10, 30, 1235, 1065) covers
    # the whole grid and the tab strip with it.
    if vendor_shop_open():
        if trade_window_open():
            say("The vendor Shop is already open, but so is the Agent Shop - "
                "closing that first; it covers the grid.")
            leave_shop(verbose=verbose)
            if trade_window_open():
                say("  the Agent Shop would not close; refusing to convert "
                    "underneath it.")
                return False
        return open_vendor_tab(verbose=verbose)

    if trade_window_open():
        say("Closing the Agent Shop before opening the vendor...")
        leave_shop(verbose=verbose)
        if trade_window_open():
            say("  the Agent Shop would not close; not opening the vendor on "
                "top of it.")
            return False

    say("Opening the vendor Shop (N)...")
    press_key(VK_N, settle=0.6, what="N")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if vendor_shop_open():
            say("  vendor Shop is open.")
            # The exchanges are under Dungeon, so select it -- but NOT
            # because N lands on Normal. It does not: measured on 2026-08-08,
            # pressing N opened the window already showing Dungeon, because the
            # game remembers the last tab used. open_vendor_tab clicks
            # unconditionally anyway, which is why that wrong assumption never
            # caused a failure.
            #
            # Worth stating because the tab lookup is the step that DID fail
            # twice that day, and the reason was the region rather than the
            # tab: TRADE_REGION (10, 30, 1235, 1065) covers VENDOR_TAB_REGION
            # (0, 150, 620, 240), and trade_window_open() is a text search that
            # reports "closed" while the panel is still fading. N then opens
            # the vendor underneath what is left of the Agent Shop, and the
            # tab label is read through it. open_vendor_tab polls for that
            # reason.
            return open_vendor_tab(verbose=verbose)
        time.sleep(0.4)
    say("  the vendor Shop did not open.")
    return False


def close_npc_shop(verbose: bool = True) -> bool:
    """Shut the vendor window and confirm it. Escape, then check."""
    for _ in range(ESCAPE_ATTEMPTS):
        if not vendor_shop_open():
            return True
        press_escape()
    closed = not vendor_shop_open()
    if not closed and verbose:
        print("  the vendor Shop would not close.")
    return closed


# The Agent Shop holds thirty rows. Every restock ADDS rows -- one per 250
# Cores listed -- so a shop that is nearly full has to stop buying before it
# buys something it cannot list.
SHOP_ROW_CAPACITY = 30


def whole_shop_listings(timeout: float = 8.0,
                        verbose: bool = True) -> list | None:
    # Reads the LISTINGS table, which only exists on the Register tab, and
    # reaches rows past the first screen by scrolling. Left on the Purchase
    # tab, that scroll moves the OFFERS instead -- and the whole buying design
    # rests on row 1 being the cheapest, so shifting the offers quietly breaks
    # it. table_scrollable() now refuses that outright; switching here is what
    # lets the work actually happen rather than merely being declined.
    """Every row in the shop, all thirty of them. None if it cannot be read.

    None rather than a short list: a partial read is exactly the input that
    makes a stocked Core look absent and buys 250 Sets of it.
    """
    if not register_tab_open() and not open_trade_window(
            timeout=max(timeout, 15.0), verbose=verbose):
        return None
    listings = enumerate_listings(timeout=timeout, verbose=verbose)
    if listings is None:
        return None
    return [row for _, row in listings]


def shop_rows_used(timeout: float = 8.0,
                   verbose: bool = True) -> int | None:
    """How many of the shop's thirty rows hold a live listing. None if unread.

    Enumerated rather than read off the screen: ten rows are visible and thirty
    exist, and the whole question here is how close the SHOP is to full.
    """
    # Same reason as whole_shop_listings: this enumerates, enumerating
    # scrolls, and scrolling belongs on the Register tab.
    if not register_tab_open() and not open_trade_window(
            timeout=max(timeout, 15.0), verbose=verbose):
        return None
    listings = enumerate_listings(timeout=timeout, verbose=verbose)
    if listings is None:
        return None
    return sum(1 for _, row in listings
               if getattr(row, "action", None) in ("change", "receive"))


def restock_rows_needed(target: int = RESTOCK_TARGET) -> int:
    """Rows a restock of `target` Sets could occupy, worst case.

    A listing holds CONVERT_QUANTITY, so the row count is the Set count divided
    by it -- but the Set count OVERSHOOTS the target, because buying stops at
    the first order that reaches it and a Set stacks to SET_STACK_MAX. Sized
    for that worst case: 250 asked for, 999 arriving on top, is five rows.
    """
    # Two worst cases, whichever is larger:
    #
    #   * one Set short of the minimum, then a full 999 stack on top -- the
    #     hard limit permits exactly this, and it is the bigger of the two;
    #   * the soft maximum, reached exactly.
    worst = max(RESTOCK_TARGET - 1 + SET_STACK_MAX, BUY_MAXIMUM)
    return max(1, -(-worst // max(1, CONVERT_QUANTITY)))


def open_purchase_tab(timeout: float = 10.0, verbose: bool = True) -> bool:
    """Put the Trade window on the Purchase tab, and confirm it got there.

    open_trade_window() is the Register-tab counterpart and every other caller
    in this file wants that one; buying is the only thing that needs this side,
    which is why it did not exist until a live restock refused with "the Trade
    window is not on the Purchase tab". The refusal was right -- purchase_ready
    would not click Purchase-tab coordinates on the Register tab -- but nothing
    had ever switched.

    The tab is found by its LABEL rather than a fixed point: the two tabs sit
    side by side and clicking a remembered coordinate that has drifted lands on
    the other one, which is the Register tab and a completely different set of
    buttons.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    if purchase_tab_open():
        return set_purchase_sort_low_to_high(verbose=verbose)

    if not trade_window_open():
        say("  the Trade window is shut; opening it first.")
        if not open_trade_window(timeout=max(timeout, 15.0), verbose=verbose):
            return False
        if purchase_tab_open():
            return set_purchase_sort_low_to_high(verbose=verbose)

    label = find_phrase(grab(), PURCHASE_TAB_WORD, TRADE_WINDOW_SEARCH)
    if label is None:
        say(f"  could not find the {PURCHASE_TAB_WORD!r} tab to click.")
        return False

    say(f"  switching to the {PURCHASE_TAB_WORD} tab at {label}")
    click(*label)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if purchase_tab_open():
            say("  Purchase tab is open.")
            return set_purchase_sort_low_to_high(verbose=verbose)
        time.sleep(0.4)
    say("  the Purchase tab did not open.")
    return False


def buy_sets_until(item_slot: int,
                   target: int = RESTOCK_TARGET,
                   max_buys: int = RESTOCK_MAX_BUYS,
                   threshold: "int | None" = None,
                   verbose: bool = True) -> dict:
    """Buy Sets of the item in `item_slot` until `target` of them are held.

    One listing is a bundle of `pack` Sets, so reaching 250 normally takes
    several purchases. Every one of them re-searches both favourites and takes
    ROW 1 -- see buy_cheapest_set for why that is not negotiable.

    Stops early, and says so, when the saving stops clearing `threshold`. A
    restock that cannot be done profitably is not worth doing: the whole point
    of the pipeline is the spread between a Set and the Core it becomes.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    # Resolved HERE as well as inside buy_cheapest_set_detail, so a concrete
    # number is what travels down. Passing None along works for the real
    # function -- it resolves its own default -- but it leaks an unresolved
    # sentinel into every caller and stub in between, and the first thing any
    # of them does with a threshold is compare it.
    if threshold is None:
        threshold = price_diff_floor_for(FAVOURITE_SLOTS.get(item_slot, ""))

    bought = 0
    orders = []
    for attempt in range(1, max_buys + 1):
        if bought >= target:
            break
        if BUY_HALTED:
            say(f"  buying is halted: {BUY_HALT_REASON}")
            break
        say(f"\n-- buy {attempt}/{max_buys}: {bought}/{target} Sets held --")
        result = buy_cheapest_set_detail(item_slot, threshold=threshold,
                                         verbose=verbose,
                                         still_wanted=target - bought)
        if not result["bought"]:
            say(f"  stopping: {result['why']}")
            break
        offer = result["offer"]
        # What the ORDER obtained, which is no longer the bundle size: one
        # order can take several identical listings from the same row.
        got = int(result.get("taken") or offer.pack)
        bought += got
        orders.append(offer)
        say(f"  +{got} Sets ({bought}/{target})")

    if bought < target:
        say(f"  bought {bought} of the {target} wanted "
            f"({len(orders)} order(s)).")
    else:
        say(f"  target met: {bought} Sets in {len(orders)} order(s).")
    return {"slot": item_slot, "bought": bought, "target": target,
            "orders": orders}


def list_cores(core_name: str, slots, timeout: float = 8.0,
               verbose: bool = True) -> dict:
    """Register every held Core of one type into an empty shop row.

    `slots` is an ordered list of candidate positions -- see
    core_slot_candidates. They are TRIED IN TURN because occupancy alone
    cannot tell a Core from a Set: the first candidate is usually right, and
    register_item refuses a slot whose item is not `core_name`, so a wrong
    guess costs a retry rather than listing the wrong thing.

    Registering maximises the quantity, and the panel's maximum counts every
    matching item in the INVENTORY -- not just the visible tab -- so one
    registration lists the whole batch as a single row, and hands all those
    slots back.

    Returns the quantity the game actually listed, which is the honest measure
    of how much of the batch is done. The alternative -- counting newly filled
    inventory slots -- only sees one tab, and 250 Cores do not fit on one tab:
    they spill onto later ones, where a slot count would miss them and report a
    finished conversion as barely started.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    if isinstance(slots, tuple) and len(slots) == 2 and isinstance(slots[0], int):
        slots = [slots]                       # a single (row, col) still works
    slots = list(slots or [])
    if not slots:
        return {"ok": False, "qty": 0, "why": "no candidate slot to list from"}

    if not open_trade_window(timeout=max(timeout, 15.0), verbose=verbose):
        say("  could not open the Agent Shop to list the Cores.")
        return {"ok": False, "qty": 0, "why": "the Agent Shop would not open"}

    why = ""
    tried_any = False
    for attempt, slot in enumerate(slots[:CORE_SLOT_TRIES], start=1):
        tried_any = True
        say(f"Listing {core_name} from inventory slot {slot} "
            f"(candidate {attempt}/{min(len(slots), CORE_SLOT_TRIES)})...")
        report: dict = {}
        try:
            ok = register_item(*slot, timeout=timeout, verbose=verbose,
                               maximise_qty=True, expect_item=core_name,
                               report=report)
        except Aborted as exc:
            # register_item refuses a slot holding something else, which is
            # exactly what a wrong candidate looks like. Try the next one.
            why = str(exc)
            say(f"  slot {slot} does not hold {core_name}: {exc}")
            continue
        qty = int(report.get("qty") or 0)
        if ok:
            say(f"  listed {qty} x {core_name}.")
            return {"ok": True, "qty": qty, "why": "", "slot": slot}
        why = "the registration did not complete"
    # Nothing on the work tab held them. Bought Sets do not always land on
    # CONVERT_INVENTORY_TAB -- the game fills the first free slot in the whole
    # inventory, so a full work tab sends them elsewhere, and the Cores they
    # become follow. Looking only where they were EXPECTED would fail a
    # conversion that worked perfectly.
    #
    # Searched last, and only on failure, because it costs a tab switch and a
    # slot read per tab.
    say(f"  not on tab {CONVERT_INVENTORY_TAB}; looking on the other tabs.")
    origin = inventory_origin()
    if origin is None:
        return {"ok": False, "qty": 0,
                "why": why or "the Inventory panel is not open"}
    for tab in range(1, TAB_COUNT + 1):
        if tab == CONVERT_INVENTORY_TAB:
            continue                      # already tried, above
        if not select_inventory_tab(tab, origin):
            continue
        here = occupied_slots(grab(), origin)
        if not here:
            continue
        for slot in here[:CORE_SLOT_TRIES]:
            report = {}
            try:
                ok = register_item(*slot, timeout=timeout, verbose=verbose,
                                   maximise_qty=True, expect_item=core_name,
                                   report=report)
            except Aborted as exc:
                why = str(exc)
                continue
            if ok:
                qty = int(report.get("qty") or 0)
                say(f"  listed {qty} x {core_name} from tab {tab} slot {slot}.")
                return {"ok": True, "qty": qty, "why": "", "slot": slot,
                        "tab": tab}
    return {"ok": False, "qty": 0,
            "why": why or ("no slot on any tab held the Cores"
                           if tried_any else "no candidate slot to list from")}


def restock_core(item_slot: int,
                 target: int = RESTOCK_TARGET,
                 max_rounds: int = RESTOCK_MAX_ROUNDS,
                 verbose: bool = True,
                 rows_used: int | None = None) -> dict:
    """The whole pipeline for one sold-out Core: buy, convert, list.

    Returns what happened at each stage rather than a bare bool, because the
    three stages fail for completely different reasons and a caller deciding
    whether to try again needs to know which one stopped.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    core = FAVOURITE_SLOTS.get(item_slot, "")
    result = {"slot": item_slot, "core": core, "bought": 0,
              "converted": 0, "listed": 0, "rounds": 0,
              # Registrations made, and how much the shop actually GREW. They
              # are NOT the same: a restock runs because something sold out, so
              # there is an empty row waiting, and the first listing refills it
              # rather than extending the shop. Five listings from a one-gap
              # start grow the shop by four.
              "rows_listed": 0, "rows_grown": 0, "why": ""}
    if not core or convert_cell_for(core) is None:
        result["why"] = f"slot {item_slot} ({core!r}) is not a convertible Core"
        say(f"  {result['why']}")
        return result

    say(f"\n{'=' * 70}\nRESTOCK {core} (favourite slot {item_slot})\n{'=' * 70}")

    # ---- 0. is there room to put the result? -----------------------------
    # Every restock ADDS rows: one per CONVERT_QUANTITY Cores listed, and up to
    # five once the purchase overshoots. Buying first and discovering the shop
    # is full afterwards strands the Cores in the inventory with nowhere to go
    # -- and the next cycle would see the same empty slot and buy MORE on top
    # of them. So the question is "will the result fit", not "is it full now".
    # Reuse the caller's count when it has one. Each enumeration scrolls the
    # whole shop up and down -- about twenty seconds of OCR -- and the caller
    # has just done exactly that to decide this restock was needed. Fetching it
    # again asks the same question twice and looks, from the outside, like the
    # script pacing the shop doing nothing.
    used = rows_used if rows_used is not None else shop_rows_used(verbose=False)
    need = restock_rows_needed(target)
    if used is None:
        result["why"] = ("could not count the shop's rows, so there is no way "
                         "to know whether the result would fit")
        say(f"  {result['why']}")
        return result
    if used + need > SHOP_ROW_CAPACITY:
        result["why"] = (f"paused: {used}/{SHOP_ROW_CAPACITY} rows used and a "
                         f"restock needs up to {need} more. Sell or clear some "
                         "rows first.")
        say(f"  {result['why']}")
        return result
    say(f"  shop has {used}/{SHOP_ROW_CAPACITY} rows used; a restock needs up "
        f"to {need} more")

    # ---- 1. buy ---------------------------------------------------------
    # Buying happens on the PURCHASE tab; everything else in this file works on
    # Register. Nothing switched, so the first live attempt refused with "the
    # Trade window is not on the Purchase tab" -- correctly, but only after
    # getting all the way here.
    # Already holding Sets this slot owes a listing for? Convert and list
    # THOSE, and buy nothing at all. See _CARRIED_SETS: a restock that bought
    # and then failed to list leaves the SHOP looking exactly as it did
    # before, so without this the next cycle asks the same question, gets the
    # same answer, and buys a second target on top of the first.
    carried = carried_sets(item_slot)
    if carried > 0:
        say(f"  {carried} Set(s) bought by an earlier restock are still in "
            "the bag and unlisted -- converting and listing those rather "
            "than buying more.")
        purchase = {"bought": carried}
        result["bought"] = carried
        result["resumed"] = True
    else:
        if not open_purchase_tab(verbose=verbose):
            result["why"] = "could not reach the Purchase tab to buy"
            say(f"  {result['why']}")
            return result

        # Buy onto the work tab, so the Sets arrive where the conversion step
        # already looks. convert_cores counts what lands on
        # CONVERT_INVENTORY_TAB and nowhere else, so Sets bought against a
        # different tab would be invisible to it -- the count would read as
        # "nothing arrived" for a purchase that went through perfectly.
        inv = inventory_origin()
        if inv is None:
            result["why"] = ("the Inventory panel is not open, so there is "
                             "nowhere known to buy onto")
            say(f"  {result['why']}")
            return result
        if not select_inventory_tab(CONVERT_INVENTORY_TAB, inv):
            result["why"] = (f"could not put the Inventory on tab "
                             f"{CONVERT_INVENTORY_TAB} before buying")
            say(f"  {result['why']}")
            return result
        say(f"  buying onto inventory tab {CONVERT_INVENTORY_TAB}")

        purchase = buy_sets_until(item_slot, target=target, verbose=verbose)
        result["bought"] = purchase["bought"]
        if purchase["bought"] <= 0:
            result["why"] = "no Sets were bought"
            return result

        # Banked BEFORE anything else can fail. Everything from here on --
        # opening the vendor, converting, listing -- can abort, and by this
        # point the Sets are paid for and in the bag. Recording the debt now
        # is what stops the next cycle repeating the purchase.
        note_carried_sets(item_slot, purchase["bought"])

    # ---- 2 and 3, interleaved -------------------------------------------
    # Convert a batch, list it, repeat, until every Set bought has become a
    # listed Core. The listing is what frees the slots the next batch needs, so
    # these cannot be separated: converting everything first would fill the
    # inventory and stall.
    #
    # The round budget comes from the purchase, not from a constant. Buying
    # stops at the first order that REACHES the target, and a Set stacks to
    # 999 -- so a 250 target routinely lands around 1,250, which is five rounds
    # at CONVERT_QUANTITY each. A fixed ceiling would strand the remainder in
    # the bag, unconverted and unlisted, with nothing saying so.
    # Back to the work tab before converting. Tab 4 is the default and every
    # count in this pipeline is taken there; buying can leave the panel
    # somewhere else, and a conversion measured against the wrong tab reads as
    # "nothing arrived" for a purchase that went through perfectly.
    inv = inventory_origin()
    if inv is not None:
        select_inventory_tab(CONVERT_INVENTORY_TAB, inv)

    say(f"  {purchase['bought']} Sets to convert, up to {CONVERT_QUANTITY} a "
        f"round, until they are all listed")
    for rnd in range(1, max_rounds + 1):
        result["rounds"] = rnd
        say(f"\n-- round {rnd}: convert then list --")
        if not open_npc_shop(verbose=verbose):
            result["why"] = "could not open the vendor Shop"
            break
        try:
            conv = convert_cores(core, quantity=CONVERT_QUANTITY,
                                 verbose=verbose, require_layout=False)
        except Aborted as exc:
            result["why"] = f"conversion stopped: {exc}"
            say(f"  {result['why']}")
            break
        result["converted"] += conv["converted"]
        candidates = conv.get("candidates") or []

        # NOT a terminator on its own. `converted` counts newly filled slots on
        # CONVERT_INVENTORY_TAB, and convert_cores says out loud that the count
        # reads 0 for a conversion that WORKED in two ordinary cases: a full
        # work tab (the Cores land on tab 5+, where this count cannot see them)
        # and a Set stack that empties into the slot its own Cores then fill.
        # Breaking here spent the Sets, produced the Cores, and never listed
        # them -- discarding exactly the cases core_slot_candidates exists to
        # survive, and reporting it as the benign "nothing converted".
        #
        # So the round only ends early when there is nothing even to attempt.
        # Whether progress was made is decided below, by the LISTING.
        if conv["converted"] <= 0 and not candidates:
            result["why"] = "nothing converted this round"
            break

        close_npc_shop(verbose=verbose)
        listing = list_cores(core, candidates, verbose=verbose)
        if not listing["ok"]:
            if conv["converted"] <= 0:
                # Nothing arrived on the work tab AND nothing could be listed
                # from the slots that were already there. The Sets are gone or
                # the inventory cannot be read; either way another round
                # repeats the same no-op.
                result["why"] = "nothing converted this round"
            else:
                result["why"] = ("the converted Cores could not be listed: "
                                 f"{listing['why']}")
            break
        result["listed"] += listing["qty"]
        result["rows_listed"] += 1
        say(f"  round {rnd}: converted {conv['converted']}, listed "
            f"{listing['qty']} -- {result['listed']}/{purchase['bought']} done")

        # Progress is measured by what was LISTED, not by what the conversion
        # counted. The conversion counts newly filled slots on one tab, and a
        # 250-Core batch spills onto later tabs where those slots are invisible
        # -- so it under-reports, and a loop trusting it would keep going after
        # the Sets had all been spent.
        if result["listed"] >= purchase["bought"]:
            break

    close_npc_shop(verbose=False)

    # Measure the growth rather than infer it. Counting registrations
    # over-states it by however many empty rows were waiting, and counting the
    # gaps would mean reasoning about where the game chose to put things --
    # whereas the occupied-row count answers it directly.
    if result["rows_listed"]:
        after_rows = shop_rows_used(verbose=False)
        if after_rows is not None:
            result["rows_grown"] = max(0, after_rows - used)
            say(f"  shop went {used} -> {after_rows} rows "
                f"({result['rows_listed']} listing(s), "
                f"{result['rows_grown']} of them new rows)")

    # Settle the debt. What was bought and not listed stays on the books, so
    # the next pass resumes here instead of buying the same target again; what
    # was listed is done with. This is the only place the carry is cleared, and
    # it is deliberately driven by `listed` -- the figure measured from the
    # SHOP -- rather than by the conversion count, which under-reports.
    outstanding = max(0, result["bought"] - result["listed"])
    note_carried_sets(item_slot, outstanding)
    if outstanding and not result["why"]:
        say(f"  {outstanding} Set(s) remain in the bag; the next restock pass "
            "will convert those rather than buying more.")

    if result["listed"] < result["bought"] and not result["why"]:
        # Hit the runaway guard with work still outstanding. Said out loud:
        # Sets left in the bag are invisible otherwise, and the next cycle
        # would see the shop still empty and buy MORE on top of them.
        result["why"] = (f"stopped at the {max_rounds}-round guard with "
                         f"{result['bought'] - result['listed']} Set(s) still "
                         "unconverted -- they are in the inventory, not lost")
    say(f"\n{core}: bought {result['bought']}, converted "
        f"{result['converted']}, listed {result['listed']}"
        + (f" -- {result['why']}" if result["why"] else ""))
    return result


def rows_in_use(listings: list) -> int:
    """How many of the given rows hold a live listing. No screen access."""
    return sum(1 for row in listings
               if getattr(row, "action", None) in ("change", "receive"))


def restock_sold_out_slots(slots: list[int], verbose: bool = True,
                           rows_used: "int | None" = None) -> list[dict]:
    """Restock these slots directly, without re-reading the shop.

    Used when the last sweep's verdict is still good: the expensive part is
    finding out WHICH Cores are unlisted, and that answer was already paid for.

    `rows_used` is passed in when the caller already counted -- which it must
    when the Agent Shop is closed between phases, because counting rows means
    reading the shop and a shut window cannot be read.
    """
    allowed = enabled_buying_slots()
    wanted = [s for s in slots if s in allowed]
    if not wanted:
        return []
    return _restock_each(wanted, rows_used=rows_used, verbose=verbose)


def restock_sold_out(listings: list, verbose: bool = True) -> list[dict]:
    """Restock every enabled Core the shop is not currently listing.

    `listings` must cover the WHOLE shop, not one screen -- see
    unlisted_core_slots for why that distinction costs money.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    allowed = enabled_buying_slots()
    empty = unlisted_core_slots(listings)
    slots = [s for s in empty if s in allowed]
    ignored = [s for s in empty if s not in allowed]
    if ignored:
        # Named rather than silently dropped. "Sold out but not bought" is a
        # decision, and a run that makes it without saying so looks identical
        # to one that never noticed.
        say("Sold out but buying is OFF for: "
            + ", ".join(FAVOURITE_SLOTS[s] for s in ignored))
    if not slots:
        return []
    say(f"\nSold out, restocking: "
        + ", ".join(FAVOURITE_SLOTS[s] for s in slots))
    return _restock_each(slots, rows_used=rows_in_use(listings),
                         verbose=verbose)


def _restock_each(slots: list[int], rows_used: int | None,
                  verbose: bool = True) -> list[dict]:
    """Restock each slot in turn, keeping the row count in step as it goes."""
    def say(message: str) -> None:
        if verbose:
            print(message)

    done = []
    # Each restock adds rows, so the count the next one sees has to include
    # them -- without going back to the shop to find out.
    shop_grew = 0
    for slot in slots:
        outcome = restock_core(
            slot, target=BUY_TARGET, verbose=verbose,
            rows_used=None if rows_used is None else rows_used + shop_grew)
        done.append(outcome)
        # Listing something changes the shop, so the remembered verdict about
        # what is unlisted no longer holds.
        if int(outcome.get("rows_listed") or 0):
            forget_unlisted()
        grew = int(outcome.get("rows_grown") or 0)
        shop_grew += grew
        if grew:
            # Each new row is one future sweeps must cover, or it is created
            # once and never repriced again -- see BUY_ADDED_ROWS.
            note_rows_added(grew)
            say(f"  the shop grew by {grew} row(s); sweeps now widen by "
                f"{BUY_ADDED_ROWS}")
    return done


WORK_TAB = 4
# Attempts to clear a stranded work tab before giving up. A strand is one
# cancelled stack, so one listing normally clears it; the extra attempts cover
# a stack the shop splits. Bounded because a strand that will not clear has to
# stop the run -- looping on it forever is worse than the abort it replaces.
STRAND_RECOVERY_ATTEMPTS = 3

EXPECTED_ROWS = 10

# --------------------------------------------------------------------------
# FRAME RECORDING
# --------------------------------------------------------------------------

RECORD_ENABLED = False

# How many frames to KEEP. Recording never stops; the oldest are pruned once
# there are enough over this to be worth a sweep.
#
# A hard stop was worse than it looked. Recording halted at 12,000 frames and
# said so once on stderr, so a 494-cycle run produced no diagnostic frames at
# all -- and reconstructing what a failure actually looked like, from the
# frames it left behind, is how every bug found on 2026-08-04/05 was found. The
# newest frames are also the useful ones: they match the current build and the
# current shop.
RECORD_KEEP = 1000
# Prune only when this many over the limit, so the index is rewritten in
# batches rather than on nearly every frame.
RECORD_PRUNE_SLACK = 100

# ==========================================================================
# END SETTINGS
# ==========================================================================


try:
    import mss
except ImportError:
    sys.exit("Missing dependency 'mss'. Install it with:  pip install mss")

try:
    from PIL import Image, ImageChops, ImageOps
except ImportError:
    sys.exit("Missing dependency 'Pillow'. Install it with:  pip install Pillow")

SCRIPT_DIR = Path(__file__).resolve().parent


# Reading a full 10-row table costs ~18s of OCR (measured), so any deadline
# that is meant to allow a retry has to be a multiple of that, not a few
# seconds. Deadlines are checked after each read, never mid-read.
TABLE_READ_BUDGET = 45.0

# ==========================================================================
# Screen capture, Alz reading and low-level input
# ==========================================================================
DEFAULT_OUTDIR = SCRIPT_DIR / "screenshots"

# mss >= 10 renamed the entry point; the lowercase alias is deprecated.
open_capture = getattr(mss, "MSS", None) or mss.mss

# Generous band around the Alz figure in the Inventory panel, verified against a
# 2560x1440 capture. The left edge stays right of the gold coin icon, which is
# also orange and would otherwise register as a digit blob.
ALZ_REGION = (2330, 872, 2525, 928)

TESSERACT_CANDIDATES = (
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
)


# --------------------------------------------------------------------------
# Alz reading
# --------------------------------------------------------------------------

def find_tesseract() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in TESSERACT_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return None


# The balance is one line of text. A mask box taller than this fraction of
# ALZ_REGION is an overlaid system message, not a balance -- measured at 34%
# for every clean read and 79% for every overlaid one.
ALZ_MAX_TEXT_HEIGHT = 0.5


def _isolate_digits(
    image: Image.Image, region: tuple[int, int, int, int]
) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    """Black-on-white image of just the orange Alz digits, plus the box those
    digits occupy in the source image. None if the region holds no digits."""
    crop = image.crop(region).convert("RGB")
    scale = 5
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)

    px = crop.load()
    mask = Image.new("L", crop.size, 255)
    m = mask.load()
    for y in range(crop.height):
        for x in range(crop.width):
            r, g, b = px[x, y]
            # The Alz figure is drawn in a saturated colour that varies -- it is
            # orange normally but green after a change -- while the "Alz" label
            # beside it is grey and the panel behind it is dark. So key on
            # "bright and colourful" rather than on a specific hue.
            hi, lo = max(r, g, b), min(r, g, b)
            if hi > 110 and hi - lo > 45:
                m[x, y] = 0

    # Trim to the actual glyphs so panel edges do not skew the OCR.
    bbox = ImageOps.invert(mask).getbbox()
    if not bbox:
        return None

    # The balance is ONE line of text. Anything taller is not the balance.
    #
    # The Agent Shop prints "[item] x250 registered on ... has been sold for
    # 50,000,000" straight over this region, in the same bright colour, and it
    # prints it exactly when a listing sells -- which is the window the sales
    # tally measures. Recorded on 2026-08-06: run_18730 read 103,000,000 and
    # run_18534 read 0 off that overlay, neither being any number in the
    # message; --psm 7 had mashed two lines together.
    #
    # The separation is geometric and clean. Every clean balance measured 19px
    # of this 56px region (34%); every overlaid frame measured 44px (79%).
    # find_alz already refuses a box that fills the region, but its bar is
    # "95% of BOTH axes" and the overlay is 94% x 79%, so it slipped under.
    #
    # This matters twice over: inventory_origin derives the slot grid from this
    # box, and the overlay moved it 32px against a 74px SLOT_PITCH -- which is
    # the "Ctrl+Clicks into the open world" failure find_alz's own comment
    # records.
    if (bbox[3] - bbox[1]) > (crop.height * ALZ_MAX_TEXT_HEIGHT):
        return None

    prepared = ImageOps.expand(mask.crop(bbox), border=60, fill=255).convert("RGB")
    # Map the upscaled crop's box back onto the source image.
    source_box = (
        region[0] + bbox[0] // scale,
        region[1] + bbox[1] // scale,
        region[0] + bbox[2] // scale,
        region[1] + bbox[3] // scale,
    )
    return prepared, source_box


# A per-CALL suffix for the scratch crop get_alz writes.
#
# The PID alone is not quite enough: two calls in flight at once inside
# one process share it, and the first to finish deletes the file the
# second is still reading. trade.py is single-threaded so that is latent
# rather than live -- but the name costs nothing and removes the shared
# resource entirely rather than narrowing it.
_ALZ_TMP_SEQ = itertools.count(1)

def get_alz(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int] | None = None,
    debug_path: Path | None = None,
) -> int:
    """Read the Alz balance from a screenshot.

    Returns 0 when the Inventory panel is closed, the region holds no digits,
    or Tesseract is unavailable -- this never raises on a failed read.
    """
    tesseract = find_tesseract()
    if tesseract is None:
        print(
            "Alz: skipped, Tesseract not found "
            "(winget install UB-Mannheim.TesseractOCR)",
            file=sys.stderr,
        )
        return 0

    image = source if isinstance(source, Image.Image) else Image.open(source)
    region = region if region is not None else ALZ_REGION

    # A region outside the image means a different resolution or layout.
    if region[2] > image.width or region[3] > image.height:
        print(
            f"Alz: skipped, region {region} falls outside the "
            f"{image.width}x{image.height} image",
            file=sys.stderr,
        )
        return 0

    found = _isolate_digits(image, region)
    if found is None:
        return 0
    prepared, _ = found

    # Unique per process. This was a single fixed path, so two processes
    # calling get_alz raced on one file: A writes it, B's `finally: unlink()`
    # deletes it, and A's Tesseract reports "cannot read input file ...
    # .alz_tmp.png: No such file or directory" -- or worse, A reads B's crop
    # and books a balance from a different moment.
    #
    # Observed for real on 2026-08-08: a test suite running alongside the live
    # script produced exactly that error twice, inside a registration, and the
    # balance reads it broke are the ones that decide whether a sale is
    # measured and what a registration fee cost. The window is small and it is
    # hit constantly, because every relist reads the balance several times.
    #
    # os.getpid() rather than a lock: the processes are independent runs, a
    # lock between them would serialise OCR for no benefit, and a per-process
    # name removes the shared resource entirely.
    tmp = debug_path or (SCRIPT_DIR /
                         f".alz_tmp_{os.getpid()}_{next(_ALZ_TMP_SEQ)}.png")
    try:
        prepared.save(tmp)
        result = subprocess.run(
            [
                tesseract, str(tmp), "stdout",
                "--psm", "7",
                "-c", "tessedit_char_whitelist=0123456789,",
            ],
            capture_output=True,
            text=True,
            timeout=TESSERACT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(f"Alz: skipped, Tesseract did not respond within "
              f"{TESSERACT_TIMEOUT:g}s", file=sys.stderr)
        return 0
    except OSError as exc:
        print(f"Alz: skipped, could not run Tesseract ({exc})", file=sys.stderr)
        return 0
    finally:
        # missing_ok: the exists() check above is a race of its own, and a
        # cleanup that raises would replace a readable balance with a
        # FileNotFoundError traceback out of a bookkeeping helper.
        if debug_path is None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    if result.returncode != 0:
        print(f"Alz: skipped, Tesseract failed ({result.stderr.strip()})", file=sys.stderr)
        return 0

    digits = re.sub(r"[^0-9]", "", result.stdout)
    return int(digits) if digits else 0


def find_alz(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int] | None = None,
    origin: tuple[int, int] = (0, 0),
) -> tuple[int, int, int, int] | None:
    """Screen box the Alz digits occupy, as (left, top, right, bottom).

    Returns None when the Inventory panel is not visible. `origin` is the
    captured monitor's top-left in virtual-desktop coordinates, so boxes from a
    secondary monitor's capture land in the right place on screen.
    """
    image = source if isinstance(source, Image.Image) else Image.open(source)
    # Resolved at call time, never as a default argument: a default is bound
    # when the function is defined, which is before calibrate() has run, so
    # these three functions would have kept searching the reference machine's
    # coordinates no matter what the calibration found.
    region = region if region is not None else ALZ_REGION
    if region[2] > image.width or region[3] > image.height:
        return None

    found = _isolate_digits(image, region)
    if found is None:
        return None

    _, box = found
    # A box that fills the search region is not a number, it is the 3D world.
    # _isolate_digits keys on "bright and colourful", which snow-lit scenery
    # satisfies everywhere, so with the Inventory panel SHUT this returned the
    # whole region on 13 of 13 frames -- and the docstring promises None. The
    # inventory anchor is derived from this box, so the consequence was a grid
    # offset by 43px and Ctrl+Clicks into the open world.
    width, height = region[2] - region[0], region[3] - region[1]
    if (box[2] - box[0]) >= width * 0.95 and (box[3] - box[1]) >= height * 0.95:
        return None

    ox, oy = origin
    return (box[0] + ox, box[1] + oy, box[2] + ox, box[3] + oy)


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def move_mouse(x: int, y: int) -> bool:
    """Move the cursor to a virtual-desktop pixel coordinate.

    make_dpi_aware() must have run first, otherwise Windows rescales these
    coordinates on a scaled display and the cursor lands short.

    Returns False if Windows refused the move. The usual cause is UIPI: Cabal
    runs elevated, so a normal-integrity process cannot inject input while the
    game holds the foreground. Running this script as Administrator fixes it.
    """
    # True, not False: a suppressed move has not been REFUSED, and callers
    # treat False as UIPI blocking them and raise PermissionError. Reporting a
    # dry run as a permissions failure would be a lie in the other direction.
    if _suppressed(f"move the cursor to ({x}, {y})"):
        return True
    make_dpi_aware()
    ctypes.windll.user32.SetCursorPos.restype = ctypes.c_int
    moved = bool(ctypes.windll.user32.SetCursorPos(int(x), int(y)))
    if moved:
        cooldown()
    return moved


def cursor_position() -> tuple[int, int]:
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    point = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


CURSOR_BLOCKED_HINT = (
    "Windows refused the cursor move. Cabal runs elevated, so a normal process "
    "cannot move the cursor while the game is in the foreground -- run this "
    "script as Administrator."
)


def move_mouse_to_alz(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int] | None = None,
    origin: tuple[int, int] = (0, 0),
) -> tuple[int, int] | None:
    """Park the cursor on the Alz figure. Returns where it went, or None if the
    panel was not visible (in which case the cursor is left alone).

    Raises PermissionError if Windows blocked the move (see CURSOR_BLOCKED_HINT).
    """
    box = find_alz(source, region, origin)
    if box is None:
        return None
    centre = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
    if not move_mouse(*centre):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    return centre


def human(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------

def make_dpi_aware() -> None:
    """Capture at native resolution on scaled displays."""
    if sys.platform != "win32":
        return
    try:
        # Per-monitor DPI aware v2, best result on mixed-DPI setups.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def list_monitors(sct) -> None:
    # sct.monitors[0] is the union of all monitors; real displays start at 1.
    for i, mon in enumerate(sct.monitors[1:], start=1):
        print(f"{i}: {mon['width']}x{mon['height']} at ({mon['left']},{mon['top']})")


def resolve_monitor(sct, choice: str) -> tuple[dict, str]:
    displays = sct.monitors[1:]

    if choice == "all":
        return sct.monitors[0], "all"
    if choice == "primary":
        # mss orders by OS enumeration; the primary display sits at (0, 0).
        for mon in displays:
            if mon["left"] == 0 and mon["top"] == 0:
                return mon, "primary"
        return displays[0], "primary"
    if choice.isdigit():
        index = int(choice)
        if not 1 <= index <= len(displays):
            sys.exit(
                f"Monitor {index} does not exist. Detected {len(displays)} "
                f"monitor(s). Run with --monitors to see them."
            )
        return displays[index - 1], f"monitor{index}"

    sys.exit(
        f"Invalid --monitor value {choice!r}. "
        "Use 'primary', 'all', or a 1-based monitor number."
    )


def unique_path(path: Path) -> Path:
    """Avoid clobbering an existing file: timestamps only resolve to a second,
    so two captures inside the same second would otherwise collide.

    Bounded by CAPTURE_SUFFIX_LIMIT rather than looping forever: a hundred
    collisions in one second means something is wrong with the clock or the
    path, and silently spinning is worse than returning a name that overwrites.
    """
    if not path.exists():
        return path
    for n in range(2, CAPTURE_SUFFIX_LIMIT):
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def prune_screenshots(outdir: Path, keep: int, protect: Path) -> int:
    """Delete all but the `keep` newest captures. Returns how many were removed.

    Only touches files matching the generated screenshot_*.png name, so
    unrelated files sharing the folder are left alone.
    """
    shots = sorted(
        outdir.glob("screenshot_*.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in shots[max(keep, 1):]:
        if old.resolve() == protect.resolve():
            continue
        try:
            old.unlink()
            removed += 1
        except OSError as exc:
            print(f"Could not delete {old.name}: {exc}", file=sys.stderr)
    return removed


def copy_to_clipboard(png_bytes: bytes) -> None:
    """Put the image on the Windows clipboard as CF_DIB."""
    import io

    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    with io.BytesIO() as buf:
        image.save(buf, "BMP")
        # A BMP file header is 14 bytes; CF_DIB expects everything after it.
        dib = buf.getvalue()[14:]

    CF_DIB, GMEM_MOVEABLE = 8, 0x0002
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
    if not handle:
        print("Skipping --clipboard: GlobalAlloc failed", file=sys.stderr)
        return

    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, dib, len(dib))
    kernel32.GlobalUnlock(handle)

    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(ctypes.c_void_p(handle))
        print("Skipping --clipboard: could not open the clipboard", file=sys.stderr)
        return
    try:
        user32.EmptyClipboard()
        # On success the system owns the handle, so it must not be freed here.
        if not user32.SetClipboardData(CF_DIB, handle):
            kernel32.GlobalFree(ctypes.c_void_p(handle))
            print("Skipping --clipboard: SetClipboardData failed", file=sys.stderr)
            return
    finally:
        user32.CloseClipboard()

    print("Copied to clipboard.")


def take_screenshot(monitor: str = "primary", delay: float = 0.0):
    """Capture and return (png_bytes, width, height, label, origin), where
    origin is the captured area's top-left on the virtual desktop."""
    make_dpi_aware()
    with open_capture() as sct:
        region, label = resolve_monitor(sct, monitor)
        if delay > 0:
            time.sleep(delay)
        shot = sct.grab(region)
    origin = (region["left"], region["top"])
    return mss.tools.to_png(shot.rgb, shot.size), shot.width, shot.height, label, origin


# ==========================================================================
# Layout calibration
# ==========================================================================
#
# Every coordinate below was measured on one machine: 2560x1440, the game
# maximised. None of it transfers. The Trade window is drawn at a position that
# depends on the client size, and the panel inside it may or may not scale with
# resolution -- so a constant like DIALOG_BUTTON_MIN_X = 1200, which exists to
# separate dialog buttons (x~1290) from the table's own buttons (x~1126), is
# simply wrong at 1920x1080 where the dialog sits near x~830. It does not
# misbehave subtly: it filters away EVERY dialog button, so nothing is ever
# clickable and the run fails with a misleading message.
#
# The fix is to keep these numbers as a REFERENCE frame -- relative to the
# Trade window's own top-left, at a known reference scale -- and to measure the
# real frame at runtime. `calibrate()` finds the Trade window, derives the
# offset and scale against this reference, and `LAYOUT` then answers every
# geometry question. The reference values are still exactly what was measured
# here, so on this machine calibration resolves to a no-op.
#
# Anything that cannot be measured is refused rather than guessed: clicking a
# coordinate derived from an unverified layout is how items get moved and
# listings get cancelled by accident.

# The display the reference numbers were measured on.
REF_SCREEN = (2560, 1440)
# The Trade window on that display: origin and size.
REF_TRADE_ORIGIN = (10, 30)
REF_TRADE_SIZE = (1225, 1035)

# Two words far apart inside the Trade window, used to measure the runtime
# offset and scale. Both OCR at high confidence and sit near opposite corners,
# which is what makes the baseline between them long enough to measure a scale
# from without amplifying a few pixels of OCR jitter into a large error.
# Every value below was MEASURED on six live captures and is stable to the
# pixel across all of them. Two earlier entries were not: "Function" was out by
# 74px in y, and "Purchase" was never found at all because the game's FPS
# overlay is drawn across that tab. A wrong reference point does not fail
# loudly -- it is absorbed into `scale`, so calibration reports success and
# every derived coordinate is quietly wrong.
# Every one of these was measured on BOTH a 2560x1440 fullscreen capture and a
# live 1920x1080 window, and fits with a worst residual of 1.7px. They are all
# chrome that exists on the Purchase tab as well as the Register tab, and each
# OCRs as a unique word on the screen.
#
# "Register Item" was dropped: it is a Register-tab panel label, so calibrating
# while the Purchase tab is showing found nothing. "Purchase" was dropped the
# other way round -- it reads at 1080p but never at 1440p, where the game's FPS
# overlay is drawn across that tab.
REF_ANCHORS: tuple[tuple[str, tuple[int, int]], ...] = (
    ("Trade", (608, 19)),       # window title, top centre
    ("Name", (492, 119)),       # column header, upper left
    ("Adjust", (919, 65)),      # "Adjust fee", upper right
    ("Function", (1126, 118)),  # column header, upper right
    ("Selling", (331, 982)),    # footer, bottom left
    ("Refresh", (1119, 981)),   # button, bottom right
)
# A SECOND TIER: chrome that only exists on the REGISTER tab.
#
# Calibration runs from prepare_for_actions right after open_trade_window,
# which lands on Register, so these are available in the normal case -- and
# absent, harmlessly, when calibrating from the Purchase tab. Never required:
# MIN_ANCHORS_AFTER_DROP counts the whole set, so a missing optional anchor
# costs nothing.
#
# Measured on twelve 2560x1440 reference-layout frames and validated against
# _anchor_centre itself. Each was found on 10 of 10 frames with 0px drift and
# lands exactly on the value below.
#
# Three candidates that looked perfect in a token scan were rejected by that
# validation, because the matcher is case-insensitive, matches by SUBSTRING,
# and discards a word seen twice:
#
#   "Quantity"  also matches "Quantity)" in "(Price x Quantity)"
#   "sales"     matches "Net sales" AND "Sales Fee"
#   "Sales"     likewise
#
# "VANGUARD" reads 10/10 at 0px too, and is rejected by judgement: it is the
# VALUE of "Adjust fee", not chrome, and a value can change.
#
# "Purchase" is the Purchase TAB LABEL, read from the Register tab. An older
# comment says it never reads at 1440p because the game draws its FPS overlay
# across that tab; it read 10/10 here. Optional is exactly the right tier for
# something that may or may not be covered -- if the overlay returns, it is
# simply not found.
REF_ANCHORS_EXTRA: tuple[tuple[str, tuple[int, int]], ...] = (
    ("Purchase", (128, 67)),     # tab label, top left
    ("Item", (142, 122)),        # "Register Item", panel header
    ("Status", (1010, 119)),     # column header, upper right
    ("Period", (55, 869)),       # panel label, lower left
    ("Expired", (503, 982)),     # footer
    ("Sold", (674, 980)),        # footer
    ("Total", (863, 980)),       # "Total Quantity", footer
)

# Both tiers together, which is what the fit actually consumes.
REF_ANCHORS_ALL: tuple[tuple[str, tuple[int, int]], ...] = (
    REF_ANCHORS + REF_ANCHORS_EXTRA)

# How far apart two anchors must be before their separation is trusted to
# measure a scale. Below this, OCR jitter of a few pixels dominates.
MIN_ANCHOR_BASELINE = 300.0
# ...and how far they must spread on EACH axis. The baseline above is the
# longest diagonal, which a row of anchors along one line satisfies while
# telling you nothing about the perpendicular direction: the fit is then
# extrapolated off that line. Measured, a set covering 430px of x and 100px of
# y produced 40px of click error at the far end of the window.
MIN_ANCHOR_SPREAD = 250.0
# Confidence an anchor must reach when it is matched only as a CLIPPED word
# (the font drops a leading glyph at smaller UI scales, so 'Trade' reads as
# 'rade'). Higher than the 40.0 used for a whole-word match, because a partial
# word is weaker evidence: the read that shifted the calibrated origin by 2px
# scored 40.1, clearing the ordinary bar by a tenth of a point.
NEAR_ANCHOR_MIN_CONF = 70.0
# How many anchors must survive outlier rejection. Higher than the absolute
# minimum of 3 on purpose: dropping is only sound while enough independent
# evidence remains to contradict the next bad one.
MIN_ANCHORS_AFTER_DROP = 4
# A measured scale outside this range means the anchors were misread, not that
# the UI is unusual.
SCALE_LIMITS = (0.4, 2.5)
# Where the calibration is remembered between runs.
CALIBRATION_FILE = SCRIPT_DIR / "calibration.json"


@dataclass(frozen=True)
class Layout:
    """The runtime geometry of the game's UI.

    `origin` is the Trade window's top-left in screen pixels and `scale` is its
    size relative to REF_TRADE_SIZE. Everything else is derived, so there is one
    place to be wrong rather than forty.
    """
    screen: tuple[int, int]
    origin: tuple[int, int]
    scale: float
    client: tuple[int, int, int, int] | None = None
    measured_from: str = "reference"

    # -- conversions ------------------------------------------------------
    def x(self, value: float) -> int:
        return int(round(self.origin[0] + value * self.scale))

    def y(self, value: float) -> int:
        return int(round(self.origin[1] + value * self.scale))

    def point(self, ref: tuple[float, float]) -> tuple[int, int]:
        return (self.x(ref[0]), self.y(ref[1]))

    def box(self, ref: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        left, top, right, bottom = ref
        return (self.x(left), self.y(top), self.x(right), self.y(bottom))

    def length(self, value: float) -> int:
        return int(round(value * self.scale))

    # -- derived regions ---------------------------------------------------
    @property
    def trade(self) -> tuple[int, int, int, int]:
        return self.box((0, 0, REF_TRADE_SIZE[0], REF_TRADE_SIZE[1]))

    def describe(self) -> str:
        return (f"screen {self.screen[0]}x{self.screen[1]}, Trade window at "
                f"{self.origin}, scale {self.scale:.3f} ({self.measured_from})")


# The Function column's x, relative to the Trade window origin (screen 1126 on
# the reference display). Used only to sanity-check that the dialog-button
# boundary still lands to the right of it after scaling.
REF_FUNCTION_COLUMN_X = 1116
# The dialog-button boundary as measured, in absolute reference-screen pixels.
# Kept as its own value rather than derived from the Function column: the
# derivation guessed "+40" and produced 1166 where the measurement says 1200.
REF_DIALOG_BUTTON_MIN_X = 1200

# Replaced by calibrate(). The default reproduces the measured reference frame
# exactly, so behaviour on the machine this was written on is unchanged.
LAYOUT = Layout(screen=REF_SCREEN, origin=REF_TRADE_ORIGIN, scale=1.0,
                measured_from="reference defaults")

# The Trade window and the dialog band, in screen pixels. Defined here rather
# than only inside apply_layout() because _capture_reference_geometry() reads
# them out of globals() to build the reference frame, and ~30 call sites read
# them directly. At the default layout these are exactly the values that were
# measured by hand: (10, 30, 1235, 1065) and the dialog band around it.
TRADE_REGION = LAYOUT.trade
# The popup is centred on the game window, whose size varies; search a band.
POPUP_REGION = (500, 350, 2100, 1150)


def _clamp_box(box: tuple[int, int, int, int],
               screen: tuple[int, int]) -> tuple[int, int, int, int]:
    """Keep a region inside the screen and the right way round.

    PIL pads an out-of-bounds crop with black rather than complaining, so an
    unclamped region reads as "nothing there" instead of failing -- and a box
    that scaling has inverted (right < left) makes crop raise mid-sequence,
    possibly after a cancel has already committed.
    """
    left, top, right, bottom = box
    left, top = max(0, left), max(0, top)
    right, bottom = min(screen[0], right), min(screen[1], bottom)
    return (left, top, max(left + 1, right), max(top + 1, bottom))


# ==========================================================================
# Agent Shop automation
# ==========================================================================

# The dialog reads "Cancel item registration" with [Confirmation] [Cancel].
# Confirmation performs the cancellation; Cancel merely closes the dialog.
CONFIRM_WORD = "Confirmation"
DISMISS_WORD = "Cancel"
# Collecting a sale raises "Confirm Receipt", whose accept button reads Receive
# rather than Confirmation.
RECEIPT_WORD = "Receive"
# Dialog buttons sit at x ~1290 / ~1471; the table's own Change/Receive buttons
# are at x ~1126. Filtering on x keeps a table button from being mistaken for a
# dialog button -- which matters for "Receive", a word that appears in both.
DIALOG_BUTTON_MIN_X = 1200
# Registering can raise more than one confirmation: pricing more than 25% below
# the weekly average adds an extra "are you sure" dialog on top of the usual one.
MAX_CONFIRM_STEPS = 3
# How long to keep looking for the Registration Extension dialog after the main
# wait has expired, before accepting that it never came.
#
# The main wait polls the TITLE, which Tesseract drops at POPUP_REGION scale on
# some frames, so "expired" routinely means "did not read it in time" rather
# than "it is not there". Only spent on a path that was about to abort.
EXTENSION_RECHECK_SECONDS = 4.0
# Dialog titles are large ornate glyphs that OCR poorly at the crop size this
# search uses; a 40% bar dropped them entirely. Junk admitted at 25% is
# filtered by having to match a specific title word, not by confidence.
DIALOG_TEXT_MIN_CONF = 25.0


@dataclass
class Word:
    text: str
    left: int
    top: int
    right: int
    bottom: int
    conf: float

    @property
    def centre(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


# --------------------------------------------------------------------------
# Text location
# --------------------------------------------------------------------------

def _prep_for_text(image: Image.Image, region: tuple[int, int, int, int], scale: int):
    """Cabal draws light text on dark panels; Tesseract wants the opposite."""
    crop = image.crop(region).convert("L")
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    return ImageOps.autocontrast(ImageOps.invert(crop))


_CALIBRATED = False


def _ocr_reference_scale() -> float:
    """The UI scale to pick an OCR upscale from, before calibration exists.

    LAYOUT.scale is the measured answer, but it is the built-in 1.0 until a
    calibration succeeds -- and a calibration needs OCR to succeed first. That
    circle is what made a 1080p screen uncalibratable: the first pass ran at
    the x2 intended for 1440p, which is documented as splitting 'Refresh' into
    'R' + 'efresh', and the upscale could only improve after reaching the state
    it was blocking.

    The window's own client rectangle breaks it, with no OCR at all. Measured
    on the machine that could not calibrate: client height 1041 against the
    reference 1369 gives 0.7604, where the anchors later measured 0.7607 --
    0.04% out, against upscale bins ~30% wide.

    `min` of the two axes, not the height alone, so a letterboxed client errs
    toward MORE upscale, which costs time rather than accuracy.

    This value only ever chooses an integer upscale. It must never reach
    LAYOUT.scale: a guessed scale that reaches apply_layout is exactly the
    "clicks confidently in the wrong place" failure the whole layer exists to
    prevent.
    """
    if _CALIBRATED:
        return LAYOUT.scale
    try:
        make_dpi_aware()          # GetClientRect returns logical px without it
        client = client_rect()
        if client:
            width = client[2] - client[0]
            height = client[3] - client[1]
            ref_w = REF_CLIENT[2] - REF_CLIENT[0]
            ref_h = REF_CLIENT[3] - REF_CLIENT[1]
            if width > 100 and height > 100:     # not minimised
                guess = min(width / ref_w, height / ref_h)
                if SCALE_LIMITS[0] <= guess <= SCALE_LIMITS[1]:
                    return guess
    except Exception:             # noqa: BLE001 - never break OCR over a guess
        pass
    return LAYOUT.scale


def find_words(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int],
    min_conf: float = 40.0,
    scale: int | None = None,
) -> list[Word]:
    """Every word Tesseract can see in `region`, in screen coordinates.

    `scale` upsamples before OCR and defaults to tracking the layout, so the
    glyphs Tesseract sees stay about the same size whatever the screen is. A
    fixed x2 was enough at 2560x1440 and not at 1920x1080: 'Refresh' came back
    split as 'R' + 'efresh', which find_text (a whole-word substring match)
    does not match -- so refresh_table() could never find its button and no
    relist cycle could complete, with a perfect calibration.
    """
    if scale is None:
        scale = max(2, min(6, int(round(2 / max(_ocr_reference_scale(), 0.34)))))
    tesseract = find_tesseract()
    if tesseract is None:
        # Degrade like the timeout and non-zero-exit paths below rather than
        # sys.exit. SystemExit derives from BaseException, so it is caught by
        # nothing here -- and this function is reachable from close_shop's
        # `finally`, where it would replace an in-flight FatalAbort and kill
        # the process after a withdrawal had already committed. main() checks
        # for the binary once at startup; that is where a hard stop belongs.
        print("Tesseract not found (winget install UB-Mannheim.TesseractOCR); "
              "treating this frame as unreadable.", file=sys.stderr)
        return []

    image = source if isinstance(source, Image.Image) else Image.open(source)
    region = (
        max(0, region[0]), max(0, region[1]),
        min(image.width, region[2]), min(image.height, region[3]),
    )
    prepared = _prep_for_text(image, region, scale)

    buf = io.BytesIO()
    prepared.save(buf, "PNG")
    # A timeout is essential, not defensive: every deadline in this file is
    # measured AROUND this call, so a wedged tesseract.exe would hang the run
    # forever with no timeout able to fire.
    try:
        result = subprocess.run(
            [tesseract, "stdin", "stdout", "--psm", "11", "tsv"],
            input=buf.getvalue(),
            capture_output=True,
            timeout=TESSERACT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(f"Tesseract did not respond within {TESSERACT_TIMEOUT:g}s; "
              "treating this frame as unreadable.", file=sys.stderr)
        return []
    except OSError as exc:
        # Every sibling OCR path catches this; this one did not. It matters
        # more than it looks: PermissionError is an OSError subclass, so an
        # antivirus lock or a bad exec bit on tesseract.exe escaped to
        # run_loop's `except PermissionError` and stopped the run with
        # "Input was refused" -- a confidently wrong diagnosis.
        print(f"Could not run Tesseract ({exc}); treating this frame as "
              "unreadable.", file=sys.stderr)
        return []
    if result.returncode != 0:
        # Degrade exactly as the timeout above does. sys.exit raises
        # SystemExit, which derives from BaseException and is therefore caught
        # by nothing in this file -- not `except Aborted`, not run_loop's
        # handler, not the back-out blocks. A single transient tesseract.exe
        # failure between a committed cancel and its relist killed the process
        # outright, leaving a dialog open and the item stranded, and it could
        # replace an in-flight FatalAbort on the way out.
        print(f"Tesseract failed ({result.stderr.decode(errors='replace').strip()}); "
              "treating this frame as unreadable.", file=sys.stderr)
        return []

    words: list[Word] = []
    # QUOTE_NONE: Tesseract emits a bare `"` as a word and never escapes it.
    # With default quoting that one word swallows every row after it into its
    # own text field, so those words vanish and the survivor carries the quote
    # glyph's coordinates -- clicks then land on the quote, not the button.
    reader = csv.DictReader(
        io.StringIO(result.stdout.decode("utf-8", errors="replace")),
        delimiter="\t", quoting=csv.QUOTE_NONE,
    )
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row["conf"])
            left, top = int(row["left"]), int(row["top"])
            width, height = int(row["width"]), int(row["height"])
        except (TypeError, ValueError, KeyError):
            continue
        if conf < min_conf:
            continue
        words.append(Word(
            text=text,
            left=region[0] + left // scale,
            top=region[1] + top // scale,
            right=region[0] + (left + width) // scale,
            bottom=region[1] + (top + height) // scale,
            conf=conf,
        ))
    return words


# Letters a lone digit is misread as, in this UI's font. Only unambiguous
# shapes are listed: 'D' is left out because it stood in for a 3 here, not a 0.
_DIGIT_LOOKALIKES = str.maketrans({
    "O": "0", "o": "0", "Q": "0",
    "I": "1", "i": "1", "l": "1", "|": "1",
    "Z": "2", "z": "2",
    "S": "5", "s": "5",
    "G": "6",
    "T": "7",
    "B": "8",
})


# Minimum light-to-dark range in a raw cell before it is believed to hold a
# glyph. Blank panel measures 33-110 on live frames, a real digit 231-232, so
# anything in that gap separates them; 160 is the middle of it.
INK_CONTRAST_MIN = 160


def _ink_box(prepared: Image.Image, threshold: int = 128):
    """Bounding box of the actual ink in a prepared (inverted) crop, or None.

    `ImageOps.invert(prepared).getbbox()` does NOT do this: getbbox() bounds
    every non-zero pixel, and _prep_for_text ends in autocontrast, so the
    background is near-white rather than white and inverts to near-zero rather
    than zero. Every background pixel therefore counts and the box is always
    the whole crop -- measured at 168x312 in 100% of ~240 samples, which made
    the guard that depends on it accept unconditionally.
    """
    return prepared.point(lambda v: 255 if v < threshold else 0).getbbox()


def read_number(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int],
    min_conf: float = 0.0,
) -> int | None:
    """The number written in a small cell, or None if there is none to read.

    `find_words` runs Tesseract in sparse-text mode, which needs something to
    segment: a lone digit in a narrow cell comes back with *no words at all*,
    at any confidence, so every single-digit quantity in the listings table
    read as None. Since quantity is what tells two stacks of the same item
    apart, that silently disabled the duplicate handling for small stacks.

    So: the ordinary read first, then a single-line, digits-only pass over the
    same pixels with blank margin added. The margin is padding, not more of the
    screenshot -- widening the crop would pull in the neighbouring column's
    digits, and this cell's neighbour is the price.
    """
    words = sorted(find_words(source, region, min_conf), key=lambda w: w.left)
    value = _digits("".join(w.text for w in words))
    if value is not None:
        return value

    tesseract = find_tesseract()
    if tesseract is None:
        return None
    image = source if isinstance(source, Image.Image) else Image.open(source)
    box = (max(0, region[0]), max(0, region[1]),
           min(image.width, region[2]), min(image.height, region[3]))
    if box[2] - box[0] < 4 or box[3] - box[1] < 4:
        return None

    # _prep_for_text inverts, so the background is white and the padding must
    # be white too, or the border reads as an inked edge.
    prepared = ImageOps.expand(_prep_for_text(image, box, 4), border=24, fill=255)

    # Is there anything here at all? Both rescue passes below return a glyph
    # for a completely blank crop -- measured, an empty strip of panel came
    # back as '1' in two cases out of three. That is worse than reading
    # nothing: an empty row reporting qty=1 is indistinguishable from a real
    # stack of 1, and locate_row narrows siblings on exactly that field.
    #
    # Tested on the RAW crop, not the prepared one: _prep_for_text ends in
    # autocontrast, which stretches the sensor noise in a blank panel across
    # the full range and manufactures ink out of nothing. Measured on live
    # frames the two populations do not overlap at all -- blank strips span
    # 33-110, every real digit cell 231-232.
    lo, hi = image.crop(box).convert("L").getextrema()
    if hi - lo < INK_CONTRAST_MIN:
        return None
    buf = io.BytesIO()
    prepared.save(buf, "PNG")
    try:
        result = subprocess.run(
            [tesseract, "stdin", "stdout", "--psm", "7",
             "-c", "tessedit_char_whitelist=0123456789,"],
            input=buf.getvalue(), capture_output=True, timeout=TESSERACT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode == 0:
        value = _digits(result.stdout.decode("utf-8", errors="replace"))
        if value is not None:
            return value

    # Last resort: the same crop with letters allowed. Tesseract reads this
    # font's '2' as 'Z', and the LSTM engine will not emit a character the
    # whitelist forbids -- it returns nothing instead. That is exactly why
    # every quantity of 2 read as None while 1 and 82 read fine.
    #
    # Only a single glyph that is *not already a digit* is accepted. A digit
    # that reads as a digit would have been found above, so seeing one here
    # means this pass is looking at a fragment of a longer number: '53' comes
    # back as '2)', and taking that 2 would report a stack of 53 as a stack of
    # 2. A wrong quantity is worse than an unread one -- it is what the
    # duplicate handling and sanity_check both key on.
    try:
        result = subprocess.run(
            [tesseract, "stdin", "stdout", "--psm", "10"],
            input=buf.getvalue(), capture_output=True, timeout=TESSERACT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None

    glyph = re.sub(r"[^0-9A-Za-z|]", "",
                   result.stdout.decode("utf-8", errors="replace"))
    if len(glyph) != 1 or glyph.isdigit():
        return None

    # psm 10 means "single character", so it returns ONE glyph whatever it is
    # shown -- given '53' it happily reports 'S'. Rejecting digits is not
    # enough, because the whole reason this pass exists is that this font's
    # digits come back as letters: 'S'->5 would turn a stack of 53 into 5.
    # So check the ink itself is one glyph wide before trusting it.
    ink = _ink_box(prepared)
    if ink is None:
        return None
    ink_w, ink_h = ink[2] - ink[0], ink[3] - ink[1]
    # 1.15, not 1.4: a real two-digit '23' measures 1.357 and slipped through.
    if ink_h <= 0 or ink_w > ink_h * 1.15:
        return None  # wider than one glyph: this is a fragment of a longer number
    return _digits(glyph.translate(_DIGIT_LOOKALIKES))


def find_text(
    source: Image.Image | Path | str,
    needle: str,
    region: tuple[int, int, int, int],
    min_conf: float = 40.0,
) -> list[Word]:
    """Words matching `needle` (case-insensitive), ordered top-to-bottom."""
    needle = needle.casefold()
    hits = [w for w in find_words(source, region, min_conf) if needle in w.text.casefold()]
    return sorted(hits, key=lambda w: w.top)


def _text_lines(words: list[Word], tolerance: int = 10) -> list[list[Word]]:
    """Group words into lines by vertical proximity, each ordered left to right."""
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.top, w.left)):
        for line in lines:
            if abs(word.centre[1] - line[0].centre[1]) <= tolerance:
                line.append(word)
                break
        else:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda w: w.left)
    return lines


def _minimal_window(line: list[Word], fragments: tuple[str, ...]) -> list[Word] | None:
    """Shortest run of words in `line` whose joined text holds every fragment.

    A "line" is only a band of similar y, so it collects whatever else happens
    to sit at that height. Taking its full extent as the match drags in
    unrelated text and shifts the centre by a hundred pixels or more: the Agent
    Shop nameplate shares a band with the Trade window's first row, and
    averaging the two put every click on the table instead of the NPC.
    """
    window = None
    for start in range(len(line)):
        joined = ""
        for end in range(start, len(line)):
            joined += line[end].text
            normalised = _normalise(joined)
            if all(fragment in normalised for fragment in fragments):
                span = line[start:end + 1]
                if window is None or len(span) < len(window):
                    window = span
                break
    return window


def _span_centre(words: list[Word]) -> tuple[int, int]:
    left = min(w.left for w in words)
    right = max(w.right for w in words)
    top = min(w.top for w in words)
    bottom = max(w.bottom for w in words)
    return ((left + right) // 2, (top + bottom) // 2)


def find_phrase(
    source: Image.Image | Path | str,
    phrase: str,
    region: tuple[int, int, int, int],
    min_conf: float = 40.0,
) -> tuple[int, int] | None:
    """Centre of `phrase`, tolerating OCR splitting it into several words.

    Ornate UI titles get chopped up -- "Inventory" comes back as 'I' plus
    'nventory' -- so whole-word matching misses them. This stitches each line
    back together before comparing.
    """
    target = _normalise(phrase)
    if not target:
        return None
    for line in _text_lines(find_words(source, region, min_conf)):
        if target not in _normalise("".join(w.text for w in line)):
            continue
        window = _minimal_window(line, (target,))
        if window is None:
            continue
        return _span_centre(window)
    return None


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------

class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long), ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KeyInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _InputUnion)]


INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
# One wheel notch. Windows sends multiples of this; how many LINES the target
# turns it into is the app's business, which is why the scroll offset is
# recovered by reading the table rather than by counting notches.
WHEEL_DELTA = 120
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12    # Alt. Module level: release_modifiers() needs it too.




# When set, every input primitive below refuses to act. --dry-run sets it, so
# a dry run cannot touch the game AT ALL rather than merely "not clicking".
#
# It was a per-call `dry_run` argument threaded through the acting functions,
# and the scroll primitives were never given one. `--relist-rows all --dry-run`
# therefore sent 40 wheel notches at the Trade window's centre. With that
# window closed, the wheel goes to the game WORLD as a camera zoom -- which
# moved the NPC out of the band find_npc searches and ended a live run that
# had been healthy for 48 minutes, two cycles later, on "Lady Yekaterina is
# not on screen".
#
# A flag checked inside the primitives cannot be forgotten by a new caller the
# way an argument can, which is exactly how that gap arose.
NO_INPUT = False


def _suppressed(what: str) -> bool:
    """True when input is being suppressed; says so once per action."""
    if not NO_INPUT:
        return False
    print(f"[dry run] would {what} - suppressed, the game is not touched")
    return True


def cooldown(seconds: float | None = None) -> None:
    """Wait after an input action. Centralised so the pace is tunable."""
    time.sleep(ACTION_COOLDOWN if seconds is None else seconds)


SEND_RETRY_PAUSE = 0.12

def _send(event: _Input, attempts: int = SEND_ATTEMPTS) -> None:
    """Inject one input event, retrying a refusal before giving up.

    SendInput returning 0 is usually TRANSIENT: a low-level hook, a moment
    where an elevated window owns the foreground, a desktop switch in
    progress. It was being treated as permanent -- one refusal raised
    PermissionError, which run_loop catches by BREAKING, so a single dropped
    event ended an unattended run. Measured in the recorded corpus: a 47-minute
    dead gap mid-cycle, with no abort recorded, because nothing retried and
    nothing survived to say why.

    The asymmetry was indefensible on its face: _release() already retries the
    same call three times for a key-UP, on the reasoning that a dropped release
    is dangerous. A dropped key-DOWN or click is no more permanent than that,
    and the cost of being wrong is a whole run.

    Genuine UIPI blocking does not clear on retry, so a real permission problem
    still raises -- it just has to fail every attempt first, which takes a
    fraction of a second and costs nothing.
    """
    for attempt in range(1, attempts + 1):
        try:
            if ctypes.windll.user32.SendInput(
                    1, ctypes.byref(event), ctypes.sizeof(_Input)) == 1:
                if attempt > 1:
                    print(f"  (input accepted on attempt {attempt})",
                          file=sys.stderr)
                return
        except OSError:
            pass
        if attempt < attempts:
            time.sleep(SEND_RETRY_PAUSE)
    raise PermissionError(CURSOR_BLOCKED_HINT)


def _release(event: _Input, what: str,
             attempts: int = RELEASE_ATTEMPTS) -> bool:
    """Release an input, retrying, and say so loudly if it will not go.

    Called from `finally` blocks, so it must not raise -- raising would mask
    the original error. But it must not stay SILENT either: SendInput reports
    a refusal in its return value, which these paths ignored entirely, so a
    dropped key-up looked exactly like a successful one.

    A dropped Ctrl-up is the worst case in this file. The game keeps believing
    Ctrl is held, and Ctrl+Click is the gesture that moves items into the shop
    slot -- so every ordinary click afterwards, on the NPC, on Change, on
    Confirmation, becomes an item move.
    """
    for _ in range(attempts):
        try:
            if ctypes.windll.user32.SendInput(
                    1, ctypes.byref(event), ctypes.sizeof(_Input)) == 1:
                return True
        except OSError:
            pass
        time.sleep(0.05)
    print(f"WARNING: could not release {what}; it may still be held down. "
          "Press it once by hand before continuing.", file=sys.stderr)
    return False


def _release_left_button() -> bool:
    return _release(_mouse_event(MOUSEEVENTF_LEFTUP), "the left mouse button")


def _release_key(vk: int) -> bool:
    return _release(_key_event(vk, up=True), f"key 0x{vk:02X}")


def release_modifiers() -> None:
    """Force every modifier up. Cheap insurance at the start of a cycle.

    If a previous cycle died with Ctrl held -- a refused key-up, a crash
    between press and release -- nothing else in the run would ever notice.
    """
    for vk in (VK_CONTROL, VK_MENU, VK_SHIFT):
        try:
            _release(_key_event(vk, up=True), f"modifier 0x{vk:02X}", attempts=1)
        except Exception:  # noqa: BLE001 - best effort only
            pass


def _mouse_event(flags: int) -> _Input:
    return _Input(type=INPUT_MOUSE,
                  u=_InputUnion(mi=_MouseInput(0, 0, 0, flags, 0, None)))


def _key_event(vk: int, up: bool) -> _Input:
    """Key event carrying both the virtual key and its scan code.

    Games that read the keyboard through DirectInput / raw input look at the
    scan code and ignore virtual-key-only events, so Ctrl appears unheld.
    """
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    return _Input(type=INPUT_KEYBOARD,
                  u=_InputUnion(ki=_KeyInput(vk, scan, flags, 0, None)))


VK_BACK = 0x08
VK_ESCAPE = 0x1B
# Backspaces sent before typing a value: enough to clear the widest price the
# field holds, without paying the typing cooldown for a long tail of no-ops.
CLEAR_KEYSTROKES = 13


VK_N = 0x4E     # opens the NPC Shop window from anywhere in the world


def press_key(vk: int, settle: float = 0.5, what: str = "") -> None:
    """Tap one key, guaranteeing the release.

    Same shape as press_escape, which is the only reason that function is not
    written in terms of this one: it is load-bearing on every abort path and
    not worth disturbing.

    A key left down triggers Windows auto-repeat into whatever gains focus
    next, so the release is in a `finally`.
    """
    if _suppressed(f"press {what or hex(vk)}"):
        return
    _send(_key_event(vk, up=False))
    try:
        time.sleep(0.05)
    finally:
        _release_key(vk)
    time.sleep(settle)
    cooldown()


def press_escape(settle: float = 0.5) -> None:
    """Tap Escape, which backs the game out to its default state.

    Sends a scan code, not just a virtual key -- the game ignores virtual-key
    only events, which is why an earlier attempt at this appeared to do nothing.
    """
    if _suppressed("press Escape"):
        return
    _send(_key_event(VK_ESCAPE, up=False))
    try:
        time.sleep(0.05)
    finally:
        _release_key(VK_ESCAPE)
    time.sleep(settle)
    cooldown()


def type_number(value: int, per_key: float = TYPE_COOLDOWN,
                clear_first: bool = True, clear: int | None = None) -> None:
    """Type digits into whatever field currently has focus.

    Paced at TYPE_COOLDOWN per keystroke -- slower than a burst, but the field
    drops characters when typed at machine speed.
    """
    if _suppressed(f"type {value}"):
        return

    def tap(vk: int) -> None:
        """Press and release, guaranteeing the release.

        A key left down triggers Windows auto-repeat, which would keep firing
        into whatever gains focus next.
        """
        _send(_key_event(vk, up=False))
        try:
            time.sleep(0.02)
        finally:
            _release_key(vk)
        time.sleep(per_key)

    if clear_first:
        # Enough backspaces to clear the widest price the field holds.
        # `clear` lets a caller size this to the field it is typing into.
        # CLEAR_KEYSTROKES is sized for the widest price; using it on the
        # 4-digit quantity field sent 9 no-op backspaces, costing 4.5s of
        # every relist -- and, if the field ignores Backspace when empty,
        # sending them to the game instead.
        for _ in range(CLEAR_KEYSTROKES if clear is None else clear):
            tap(VK_BACK)
    for ch in str(value):
        tap(0x30 + int(ch))
    cooldown()


def scroll_wheel(x: int, y: int, notches: int, settle: float = 0.35) -> None:
    """Turn the mouse wheel over (x, y). Negative scrolls DOWN (further into
    the list), positive scrolls UP, matching how Windows reports a real wheel.

    The cursor is moved first because the wheel goes to the window under the
    pointer, not to the focused one -- scrolling with the cursor parked
    somewhere else would scroll whatever is there instead.

    Sends one notch at a time with a pause between. A burst of notches in a
    single event is legal, but the game animates the list and coalescing them
    made the settle time unpredictable, which matters because the caller has
    to re-read the table afterwards to learn where it landed.
    """
    # Checked FIRST, before the cursor moves. The wheel is the most dangerous
    # primitive here precisely because it is not a click: with the Trade window
    # closed it goes to the game world as a camera zoom, and nothing in the
    # script would notice.
    if _suppressed(f"scroll {notches:+d} notch(es) at ({x}, {y})"):
        return

    # The guard lives HERE, in the primitive, not in the callers.
    #
    # table_scrollable's own docstring has always claimed it was "checked at
    # the wheel rather than at the callers" -- and it was not. Three of the
    # four call sites checked it; `--scroll` did not, and any new one would
    # have to remember. That is the same shape as the bug the docstring was
    # written about, where patching enumerate_listings and not bring_into_view
    # left half the rule uncovered.
    #
    # It refuses when the Trade window is shut (the wheel becomes a camera
    # zoom) and when the PURCHASE tab is showing (the wheel moves the offers,
    # so "always buy row 1" quietly stops meaning the cheapest listing).
    if not table_scrollable(verbose=True):
        raise Aborted(
            f"refusing to scroll {notches:+d} notch(es) at ({x}, {y}): the "
            "listings table is not what the wheel would reach")

    make_dpi_aware()
    if not move_mouse(x, y):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    # mouseData is unsigned in the struct, so a downward notch has to be sent
    # as its two's-complement value rather than a bare -120.
    step = (WHEEL_DELTA if notches > 0 else -WHEEL_DELTA) & 0xFFFFFFFF
    for _ in range(abs(notches)):
        _send(_Input(type=INPUT_MOUSE,
                     u=_InputUnion(mi=_MouseInput(0, 0, step,
                                                  MOUSEEVENTF_WHEEL, 0, None))))
        time.sleep(settle)
    cooldown()


def click(x: int, y: int, settle: float = 0.15) -> None:
    """Left-click at a screen coordinate.

    Raises PermissionError if Windows refuses the cursor move, which is the
    reliable signal that UIPI is blocking us (see CURSOR_BLOCKED_HINT).
    """
    if _suppressed(f"click ({x}, {y})"):
        return
    make_dpi_aware()
    if not move_mouse(x, y):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    time.sleep(settle)

    # The button-up must fire even if something goes wrong in between: a left
    # button left logically down turns every later cursor move into a drag.
    _send(_mouse_event(MOUSEEVENTF_LEFTDOWN))
    try:
        time.sleep(0.09)
    finally:
        _release_left_button()
    time.sleep(0.05)
    cooldown()


def ctrl_click(x: int, y: int, settle: float = 0.15) -> None:
    """Ctrl+Left-click, which is how Cabal moves an item into the shop slot."""
    if _suppressed(f"Ctrl+Click ({x}, {y})"):
        return
    make_dpi_aware()
    if not move_mouse(x, y):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    time.sleep(settle)

    # Hold Ctrl well before and after the click: the game samples modifier
    # state on its own frame tick, so a brief hold can be missed entirely.
    _send(_key_event(VK_CONTROL, up=False))
    try:
        time.sleep(0.25)
        _send(_mouse_event(MOUSEEVENTF_LEFTDOWN))
        try:
            time.sleep(0.12)
        finally:
            # Release the button too, not just Ctrl: releasing only the
            # modifier left the button down while reporting a clean error.
            _release_left_button()
        time.sleep(0.25)
    finally:
        _release_key(VK_CONTROL)
        time.sleep(0.08)
    cooldown()


def alt_click(x: int, y: int, settle: float = 0.15) -> None:
    """Alt+Left-click, which opens the vendor's Mass Purchase dialog.

    Same shape as ctrl_click, and the same reasoning: the modifier is held well
    either side of the button because the game samples modifier state on its own
    frame tick.

    Alt carries an extra hazard Ctrl does not. If the Alt goes down but the
    click is refused, Windows has a bare Alt press, which activates the window
    menu; and if Alt is dropped while down, every later click in this file
    becomes an Alt+click -- which in a vendor window is a purchase dialog rather
    than the intended button. Both releases are therefore in `finally`, and
    release_modifiers() at the start of a cycle is the backstop.
    """
    # The precondition is checked BEFORE the suppression early-return, so it
    # holds in a dry run too -- and, more importantly, so a TEST can exercise
    # it with input fully suppressed.
    #
    # With the order reversed, the only way to reach this guard was to turn
    # NO_INPUT off. That is fine while the guard exists and catastrophic when a
    # mutation run deletes it: the mutant then really does Alt+click four
    # coordinates in the game world, and the character walks off. That
    # happened. A safety check that can only be tested with the safety off is
    # not testable.
    #
    # The vendor window must be open, checked HERE rather than by the caller.
    #
    # Alt+click exists for exactly one thing -- the SET/CORE grid in the
    # vendor's Dungeon tab -- and those coordinates sit low and left, which
    # with no window there is bare ground. A click on the ground is
    # click-to-move, so the character walks off; the run then fails somewhere
    # unrelated, having also moved the NPC out of every position the rest of
    # the file expects.
    #
    # That happened: a diagnostic script PRINTED vendor_shop_open() and clicked
    # regardless, and the character walked away mid-test. convert_cores checks
    # this twice and would have refused -- but a guard a caller can skip is not
    # a guard, which is the same lesson NO_INPUT and the scroll_wheel camera
    # zoom already taught this file.
    if not vendor_shop_open():
        raise Aborted(
            f"refusing Alt+Click at ({x}, {y}): the vendor Shop is not open, "
            "so this would land in the game world and walk the character")

    if _suppressed(f"Alt+Click ({x}, {y})"):
        return

    make_dpi_aware()
    if not move_mouse(x, y):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    time.sleep(settle)

    _send(_key_event(VK_MENU, up=False))
    try:
        time.sleep(0.25)
        _send(_mouse_event(MOUSEEVENTF_LEFTDOWN))
        try:
            time.sleep(0.12)
        finally:
            _release_left_button()
        time.sleep(0.25)
    finally:
        _release_key(VK_MENU)
        time.sleep(0.08)
    cooldown()


def grab() -> Image.Image:
    global _last_shot
    png, _, _, _, _ = take_screenshot()
    _last_shot = Image.open(io.BytesIO(png))
    return _last_shot


# --------------------------------------------------------------------------
# Recording frames for the test corpus
# --------------------------------------------------------------------------
#
# A live run sees states no amount of sitting and capturing will reproduce:
# the Registration Extension dialog exists for about a second, an empty shop
# row only exists between a cancel and its register, and the genuinely useful
# frames are the ones where something went wrong. So the run records them.
#
# It reuses the frame the step already captured -- `record()` never takes a
# screenshot of its own -- so the cost is a PNG write, not a capture. Frames
# are written alongside a JSONL index carrying the label and whatever context
# the call site knew, which is what makes them usable as test fixtures rather
# than just a pile of images.

RECORD_DIR = SCRIPT_DIR / "unit_tests" / "corpus"
_last_shot: "Image.Image | None" = None
_record_seq = 0


def record(label: str, shot: "Image.Image | None" = None, /, **context) -> None:
    """Save the frame this step is already looking at, for later tests.

    Never raises and never captures: recording must not be able to change what
    a run does, or a failure this exists to capture would be caused by it.

    `label` and `shot` are positional-only so a caller can pass context named
    anything -- including `label=` or `shot=` -- without colliding with the
    signature. That is not hypothetical: `record("npc.found", label=...)`
    raised TypeError at argument-binding time, BEFORE the try below could
    swallow it, and killed three consecutive cycles. Argument binding is the
    one failure this function cannot catch, so it must be made impossible.
    """
    global _record_seq
    if not RECORD_ENABLED:
        return
    image = shot if shot is not None else _last_shot
    if image is None:
        return
    try:
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        if _record_seq == 0:
            # The HIGHEST existing number, not the count. Counting breaks the
            # moment a frame is deleted: with run_00002 removed, len() is 4
            # while run_00005 exists, so the next write silently OVERWRITES a
            # live frame -- and the index keeps the old entry, so the suite
            # then asserts one frame's recorded values against a different
            # image and reports confident, specific, wrong failures.
            #
            # That matters far more now that pruning is AUTOMATIC: frames are
            # deleted from the front on every run, so "count" and "highest"
            # diverge permanently rather than only when someone tidies up by
            # hand. Continuing from the highest surviving number is what keeps
            # a pruned corpus from having its newest frames overwritten.
            highest = 0
            for existing in RECORD_DIR.glob("run_*.png"):
                digits = existing.stem[4:]
                if digits.isdigit():
                    highest = max(highest, int(digits))
            _record_seq = highest
        _record_seq += 1
        name = f"run_{_record_seq:05d}.png"
        image.save(RECORD_DIR / name)
        entry = {"file": name, "label": label,
                 "at": datetime.now().isoformat(timespec="seconds"),
                 # The layout the frame was READ under, not just when it was
                 # taken. Every search region is derived from LAYOUT, and
                 # Tesseract's sparse-text segmentation is crop-dependent -- so
                 # replaying a frame under a different layout can legitimately
                 # return a different answer from the same pixels.
                 #
                 # That is not hypothetical: calibration lands on origin (9,29)
                 # or (10,30) from OCR jitter alone, 11 times and 13 times in
                 # one day's runs. Replaying (9,29) frames under (10,30) shifted
                 # the NPC nameplate centre by up to 5px and failed 4 of 378,764
                 # corpus assertions -- a comparison the frames carried no way
                 # to make fair. Now they do.
                 "layout": {"origin": list(LAYOUT.origin),
                            "scale": round(LAYOUT.scale, 6),
                            "screen": list(LAYOUT.screen)}}
        # Context must never overwrite the three fields the index is keyed on.
        # Making label/shot positional-only stopped `record(..., label=x)` from
        # RAISING, but update() then let that same kwarg clobber the label
        # anyway -- which is worse than the crash, because it is silent: 15
        # frames were written with a coordinate where their label should be,
        # and `at=` overwrote the timestamp on 15 more. A colliding key is
        # kept under a prefixed name rather than dropped, so the caller's
        # value still reaches the index.
        for key, value in context.items():
            if value is None:
                continue
            entry["ctx_" + key if key in ("file", "label", "at") else key] = value
        with (RECORD_DIR / "run_index.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
        prune_recordings()
    except Exception:  # noqa: BLE001 - recording is never worth a failure
        pass


def prune_recordings(keep: int = RECORD_KEEP,
                     slack: int = RECORD_PRUNE_SLACK) -> int:
    """Keep the newest `keep` frames; delete the rest and their index lines.

    Returns how many frames were removed.

    Frames and index MUST go together. A frame with no index line is an orphan
    that no test can interpret; an index line with no frame makes the corpus
    suite assert against a file that is not there, and the baseline report every
    pruned frame as "in baseline, unreadable now". So the index is rewritten in
    the same pass, keeping only the lines whose file survived.

    Ordered by the SEQUENCE NUMBER in the name, not by mtime. The sequence is
    the authority on age -- mtime is whatever the filesystem last recorded, and
    a copy or a restore rewrites it.

    Never raises: this runs inside record(), which must never be able to break
    a run.
    """
    try:
        frames = []
        for path in RECORD_DIR.glob("run_*.png"):
            digits = path.stem[4:]
            if digits.isdigit():
                frames.append((int(digits), path))
        if len(frames) <= keep + slack:
            return 0

        frames.sort()
        doomed = frames[:len(frames) - keep]
        gone = set()
        for _seq, path in doomed:
            try:
                path.unlink()
                gone.add(path.name)
            except OSError:
                pass                       # locked by a reader; try again later
        if not gone:
            return 0

        # Rewrite via a temp file and os.replace, which is atomic on Windows:
        # a suite reading the index concurrently sees the old file or the new
        # one, never a half-written one.
        index = RECORD_DIR / "run_index.jsonl"
        if index.exists():
            tmp = index.with_suffix(".jsonl.tmp")
            kept = 0
            with index.open("r", encoding="utf-8", errors="replace") as src, \
                    tmp.open("w", encoding="utf-8") as dst:
                for line in src:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        name = json.loads(stripped).get("file")
                    except Exception:      # noqa: BLE001 - keep unparseable
                        dst.write(line)
                        kept += 1
                        continue
                    if name not in gone:
                        dst.write(line)
                        kept += 1
            os.replace(tmp, index)
        return len(gone)
    except Exception:  # noqa: BLE001 - pruning is never worth a failure
        return 0


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
UOI_NAME = 2
DESKTOP_SWITCHDESKTOP = 0x0100


def session_locked() -> bool:
    """True when the workstation is locked or a secure desktop is up.

    Locking switches the input desktop to Winlogon: screen captures come back
    black and injected input goes to the secure desktop, so every action would
    fail in confusing ways. Detecting it lets us say so plainly instead.
    """
    user32 = ctypes.windll.user32
    handle = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not handle:
        return True
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = ctypes.c_ulong()
        if user32.GetUserObjectInformationW(
            handle, UOI_NAME, name, ctypes.sizeof(name), ctypes.byref(needed)
        ):
            return name.value.casefold() != "default"
        return False
    finally:
        user32.CloseDesktop(handle)


def keep_awake(enable: bool = True) -> bool:
    """Hold off display and system sleep while a long run is in progress.

    Does NOT stop a manual lock (Win+L) or a screensaver set to require sign-in;
    nothing running as a normal user can prevent those.
    """
    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState.restype = ctypes.c_ulong
    flags = ES_CONTINUOUS
    if enable:
        flags |= ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    return bool(kernel32.SetThreadExecutionState(flags))


# --------------------------------------------------------------------------
# Window focus
# --------------------------------------------------------------------------



def find_game_window() -> int | None:
    """HWND of the Cabal window, or None."""
    user32 = ctypes.windll.user32
    found: list[int] = []

    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if GAME_TITLE_HINT.casefold() in buf.value.casefold():
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(proto(callback), None)
    return found[0] if found else None


def focus_game(settle: float = 0.35) -> bool:
    """Bring Cabal to the foreground so clicks hit controls instead of merely
    activating the window. Returns True if it ended up foreground."""
    user32 = ctypes.windll.user32
    hwnd = find_game_window()
    if hwnd is None:
        return False

    if user32.GetForegroundWindow() == hwnd:
        return True

    # SW_RESTORE only when actually minimised -- calling it on a maximised
    # window un-maximises it, which moves every control we just measured.
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(settle)

    if user32.GetForegroundWindow() == hwnd:
        return True

    # SetForegroundWindow is refused unless the caller owns the foreground.
    # Two ways round it, tried in turn:
    #   1. A synthetic Alt tap. Windows lifts the restriction for a process
    #      that has just received input, which is what unsticks the common
    #      case of another console (often an elevated one) holding focus.
    #   2. Borrowing the foreground and target threads' input state, so we
    #      count as owning the foreground for the duration of the call.
    try:
        _send(_key_event(VK_MENU, up=False))
        try:
            time.sleep(0.03)
        finally:
            # Always release Alt. This path only runs when input is already
            # being refused, so a failed release here is the likeliest way to
            # leave a modifier stuck -- after which every click is Alt+click.
            _release_key(VK_MENU)
        time.sleep(0.05)
    except PermissionError:
        _release_key(VK_MENU)  # keydown may have landed before the refusal

    user32.SetForegroundWindow(hwnd)
    time.sleep(settle)
    if user32.GetForegroundWindow() == hwnd:
        return True

    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    foreground_thread = user32.GetWindowThreadProcessId(
        user32.GetForegroundWindow(), None)
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    for thread in {target_thread, foreground_thread} - {current_thread, 0}:
        user32.AttachThreadInput(current_thread, thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        for thread in {target_thread, foreground_thread} - {current_thread, 0}:
            user32.AttachThreadInput(current_thread, thread, False)
    time.sleep(settle)
    return user32.GetForegroundWindow() == hwnd


# --------------------------------------------------------------------------
# Trade actions
# --------------------------------------------------------------------------

NAME_COLUMN = (275, 715)  # fallback only; normally derived from the headers
# The game's own label for a VACANT premium listing slot, as it appears in the
# name column: "Premium Exclusive Slot". Matched on the leading word after
# _normalise, because the name column clips it to "Premium Ex".
#
# This exists because treating that label as a misread made read_rows discard
# the entire table, which stopped a live run: a sold-out row was collected, the
# slot it vacated became a labelled premium slot, and the script then read zero
# listings from a perfectly readable screen until the failure breaker fired.
PREMIUM_SLOT_MARKER = "premium"
# Row spacing on the reference display; scaled through LAYOUT at runtime.
REF_ROW_PITCH = 79
# Every table row carries one of these in the Function column. Rows that are
# sold show Receive and empty slots show Register, so counting only Change
# buttons would renumber the rows relative to what is on screen.
BUTTON_WORDS = ("Change", "Receive", "Register")
# A single digit in the QTY column reads at low confidence; junk is filtered by
# the column bounds rather than by the confidence bar.
QTY_COL_MIN_CONF = 15.0
# Somewhere harmless to leave the cursor: the Trade window's title bar. Hovering
# a listing pops a large item tooltip that covers the table and wrecks the OCR.
PARK_POINT = (600, 45)


@dataclass
class Row:
    index: int
    name: str
    change: tuple[int, int]
    top: int
    bottom: int
    action: str  # 'change', 'receive' or 'register'
    price: int | None = None
    qty: int | None = None

    @property
    def cancellable(self) -> bool:
        return self.action == "change"


def park_cursor(settle: float = 0.0) -> None:
    """Move the cursor off the listings so no tooltip covers the table.

    Raises PermissionError like every other input path. Swallowing a refused
    move here left the cursor over a listing, whose tooltip then covered the
    table, and the run reported "no listings visible" instead of "input is
    blocked -- run as Administrator".
    """
    if not move_mouse(*PARK_POINT):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    # move_mouse already ends in cooldown(), so the tooltip has ACTION_COOLDOWN
    # to clear. `settle` used to add another 0.45s on top, making a park cost
    # ~0.95s -- the one input path that was not on the standard cooldown.
    if settle:
        time.sleep(settle)


# --------------------------------------------------------------------------
# Reopening the Trade window
# --------------------------------------------------------------------------

# Matched as a fragment: the in-world label OCRs imperfectly ("YÃƒÂ©ekaterina"),
# but the middle of the name survives.
NPC_NAME_FRAGMENT = "katerina"
# BOTH fragments must appear on the same OCR line. The name alone was too weak:
# it matched something transient while the character was at the Warehouse, and
# the resulting blind click landed in the game world -- which moves the
# character or interacts with whatever happens to be under it.
NPC_TITLE_FRAGMENT = "agentshop"
# The same two things, matched per WORD rather than as one joined string.
#
# The joined form demands that no unrelated word interleaves and that no glyph
# slips, and live OCR breaks both: "(Agent" ... "Yekaterina" ... "Shop)" hides
# `agentshop`, and "Yekaterima" hides `katerina`. Each defeated a different
# half of the test on consecutive cycles.
NPC_NAME_WORD = "yekaterina"
NPC_TITLE_WORDS = ("agent", "shop")
# How close a single word must be to count. 0.80 accepts one slipped glyph in
# ten characters ("yekaterima" scores 0.90) and still rejects unrelated names:
# the Warehouse keeper this must never latch onto scores far below it.
NPC_WORD_SIMILARITY = 0.80


def _npc_label_words(line: "list[Word]") -> "tuple[bool, bool]":
    """(name seen, title seen) among the words of one OCR line.

    Per word and fuzzy, because the failure this replaces was not a missing
    nameplate -- it was a present one, read at 96% confidence, that the joined
    substring test could not see.
    """
    texts = [_normalise(w.text) for w in line]
    texts = [t for t in texts if t]
    name = _mentions(texts, NPC_NAME_WORD, NPC_WORD_SIMILARITY)
    # A word may carry the whole name with punctuation attached, or the OCR may
    # keep "ladyyekaterina" together; substring covers those without loosening
    # the fuzzy bar for everything else.
    if not name:
        name = any(NPC_NAME_FRAGMENT in t for t in texts)
    title = all(
        _mentions(texts, want, NPC_WORD_SIMILARITY)
        or any(want in t for t in texts)
        for want in NPC_TITLE_WORDS)
    return name, title
# Only the middle of the screen. The NPC's name also appears in the top
# target-nameplate banner, the chat log in the bottom-left and the sale
# notifications in the bottom-right -- none of which are the NPC.
NPC_SEARCH_REGION = (600, 150, 1900, 900)
# Belt and braces: reject a match landing in either bottom corner even if the
# search box is ever widened.
NPC_EXCLUDE_ZONES = (
    (0, 700, 900, 1440),      # bottom-left: chat
    (1850, 0, 2560, 1440),    # right: inventory panel and sale notifications
)
# The floating label sits above the model, centred on it, but how far above
# depends on the camera angle and how close you are standing -- a single fixed
# offset missed and the click went to the ground, which walks the character.
# So sweep a grid below the label, nearest its centre first, re-locating and
# verifying after each click.
NPC_CLICK_ATTEMPTS = 100
# Where the model usually sits relative to the centre of its name label.
NPC_BODY_OFFSET = (0, 120)
NPC_CLICK_OFFSET = NPC_BODY_OFFSET  # kept for callers wanting a single point


def _npc_click_offsets(attempts: int = NPC_CLICK_ATTEMPTS) -> tuple:
    """Click points around the model, ordered outward from its centre.

    Ordering matters more than coverage: the first few attempts should be the
    ones most likely to land on her, so the usual case still opens the shop in
    seconds and the long tail only costs anything when she is somewhere
    unexpected. Ties break towards the vertical centre line, then downward --
    a click below the model hits the ground, a click above it hits the label.
    """
    cx, cy = NPC_BODY_OFFSET
    # The grid is a distance below a nameplate, so it scales with the UI. On a
    # smaller screen the model is smaller and closer to its label, and an
    # unscaled grid would sweep past it entirely.
    step_y = max(4, LAYOUT.length(10))
    step_x = max(6, LAYOUT.length(15))
    span_y = LAYOUT.length(110), LAYOUT.length(160)
    span_x = LAYOUT.length(60)
    grid = [(cx + dx, cy + dy)
            for dy in range(-span_y[0], span_y[1] + 1, step_y)
            for dx in range(-span_x, span_x + 1, step_x)]
    # Vertical distance is discounted against horizontal. The label is drawn
    # centred over the model, so sideways error is small and fairly constant,
    # while how far above her it floats swings with the camera angle -- that is
    # the axis worth spending attempts on.
    grid.sort(key=lambda p: (4 * (p[0] - cx) ** 2 + (p[1] - cy) ** 2,
                             abs(p[0] - cx), p[1]))
    return tuple(grid[:attempts])


NPC_CLICK_OFFSETS = _npc_click_offsets()
# Per-attempt wait. Deliberately short: with a hundred candidates the sweep has
# to move on quickly, and a real open is detected by the panel probe below.
NPC_CLICK_WAIT = 1.5
# Wall clock for the whole sweep. At ~6s an attempt the full 100 points is
# ~10 minutes, and every miss is a move order that walks the character further
# from the NPC, so the attempt count alone is not a usable bound.
NPC_SWEEP_BUDGET = 120.0
# Consecutive attempts with no nameplate visible before giving up. The sweep
# falls back to the last known label, which is right for a one-frame OCR flake
# and wrong once the character has actually walked away.
NPC_LOST_LIMIT = 6
PURCHASE_TAB_WORD = "Purchase"
REGISTER_TAB_WORD = "Register"
# Detecting "the Trade window is up" must not hinge on one word OCRing: any of
# these appearing means it is open, whichever tab it opened on. Searched over a
# wider box than TRADE_REGION in case the window is not where the Register-tab
# layout puts it.
TRADE_OPEN_MARKERS = ("Purchase", "Adjust", "Register", "Function")
# Stops above the bottom-left chat log: a chat line containing "Register" or
# "Purchase" would otherwise report the window open while it is shut, so the
# NPC click is skipped and the run fails looking for the Register tab.
TRADE_WINDOW_SEARCH = (0, 0, 1700, 700)


def find_npc(
    source: Image.Image | None = None, retries: int = NPC_FIND_RETRIES,
    seen: dict | None = None,
) -> tuple[int, int] | None:
    """Centre of the Agent Shop NPC's floating name label, or None.

    Callers add one of NPC_CLICK_OFFSETS to reach the model. The label sits
    over moving scenery and OCRs intermittently ("Yekaterina" one frame,
    "Yeekaterina" the next), so this retries on fresh frames.

    Pass `seen` to get back the frame she was actually found in, under key
    'shot'. Because the retries grab NEW frames, the caller's `_last_shot` is
    not necessarily the one that matched -- a frame recorded from it can show
    the Trade window covering her, which is exactly what one corpus frame
    labelled npc.found turned out to contain.

    Requires the name AND the "(Agent Shop)" title on the same line. Matching
    the name alone once latched onto something while the character was at the
    Warehouse, and the click that followed went into the open world.
    """
    def excluded(point: tuple[int, int]) -> bool:
        x, y = point
        return any(left <= x <= right and top <= y <= bottom
                   for left, top, right, bottom in NPC_EXCLUDE_ZONES)

    def label_centre(image: Image.Image, region=None) -> tuple[int, int] | None:
        for line in _text_lines(find_words(image, region or NPC_SEARCH_REGION, 25)):
            joined = _normalise("".join(w.text for w in line))
            strict = (NPC_NAME_FRAGMENT in joined
                      and NPC_TITLE_FRAGMENT in joined)
            if not strict:
                # The joined test is the fast path and stays first. When it
                # fails, ask again per word before giving up: on 2026-08-08
                # three cycles refused a nameplate whose words had just been
                # read at 96% -- once because a glyph slipped, once because
                # OCR interleaved the name between "(Agent" and "Shop)".
                name_seen, title_seen = _npc_label_words(line)
                if not (name_seen and title_seen):
                    continue
            # Only the words spelling the label. The nameplate sits at the same
            # height as the Trade window's first row, so the OCR line also held
            # that row's price, status and Change button; measuring across all
            # of it reported a centre ~190px left of the NPC, and every click
            # in the sweep landed on the table.
            window = _minimal_window(line, (NPC_NAME_FRAGMENT,
                                            NPC_TITLE_FRAGMENT))
            if window is None:
                # Matched per word, so the joined-fragment span will not be
                # found either. Fall back to the span between the first and
                # last word that looks like part of the label -- still a
                # MINIMAL window, so the Trade window's first row (which shares
                # this y band) is excluded exactly as before.
                wanted = (NPC_NAME_WORD,) + NPC_TITLE_WORDS
                idx = [i for i, w in enumerate(line)
                       if any(_mentions([_normalise(w.text)], want,
                                        NPC_WORD_SIMILARITY)
                              or want in _normalise(w.text)
                              for want in wanted)]
                if not idx:
                    continue
                window = line[min(idx):max(idx) + 1]
            return _span_centre(window)
        return None

    # Tesseract's sparse-text segmentation depends on the exact crop, and this
    # search region moves with the calibrated layout -- which is re-measured
    # every cycle and legitimately lands on origin (9,29) or (10,30) depending
    # on OCR jitter. Measured on a real frame: NPC_SEARCH_REGION (600,150,...)
    # finds nothing while (599,149,...) finds her at (1340,247), with her
    # nameplate sitting at 90-96% confidence either way. One pixel.
    #
    # So a miss is retried against a slightly wider crop before believing it.
    # Only on failure, so the common path still costs one pass.
    def look(image):
        for pad in (0, 8):
            box = NPC_SEARCH_REGION if pad == 0 else (
                max(0, NPC_SEARCH_REGION[0] - pad),
                max(0, NPC_SEARCH_REGION[1] - pad),
                NPC_SEARCH_REGION[2] + pad, NPC_SEARCH_REGION[3] + pad)
            got = label_centre(image, box)
            if got is not None and not excluded(got):
                return got
        return None

    for _ in range(retries):
        image = source if source is not None else grab()
        label = look(image)
        if label is not None:
            if seen is not None:
                seen["shot"] = image
            return label
        if source is not None:
            break  # a fixed frame will not change between retries
        time.sleep(0.4)
    return None


def panel_covers_trade_area(gap: float = 0.35, threshold: float = 2.0) -> bool:
    """Fast probe: is a static UI panel covering the Trade window's area?

    Two frames a moment apart. The 3D world animates constantly, so with no
    window open the area differs noticeably between them; an opaque UI panel
    barely changes at all. Costs ~0.8s against ~5s for the OCR check, which is
    what makes sweeping many click points practical. Indicative only -- always
    confirm with trade_window_open() before acting on it.
    """
    first = grab().convert("L").crop(TRADE_REGION)
    time.sleep(gap)
    second = grab().convert("L").crop(TRADE_REGION)
    diff = ImageChops.difference(first, second)
    data = list(getattr(diff, "get_flattened_data", diff.getdata)())
    return (sum(data) / len(data)) < threshold


def trade_window_open(source: Image.Image | None = None) -> bool:
    """True when the Trade window is up, on either tab."""
    image = source if source is not None else grab()
    return any(find_text(image, marker, TRADE_WINDOW_SEARCH)
               for marker in TRADE_OPEN_MARKERS)


def register_tab_open(source: Image.Image | None = None) -> bool:
    """True when the Trade window is showing the Register tab."""
    image = source if source is not None else grab()
    return find_phrase(image, "Register Item", REGISTER_PANEL) is not None


def shop_session_age() -> float | None:
    """Seconds the Agent Shop has been continuously open, or None if closed."""
    if _shop_open_since is None:
        return None
    return time.monotonic() - _shop_open_since


def shop_session_expired() -> bool:
    """Whether the shop has been open long enough to be worth rebuilding.

    Fails OPEN (returns True) when no session is recorded: "I do not know how
    long this has been up" must mean "close it and start clean", never "keep
    it open indefinitely".
    """
    age = shop_session_age()
    return age is None or age >= SHOP_SESSION_SECONDS


def note_shop_opened() -> None:
    """Start the session clock, if one is not already running."""
    global _shop_open_since
    if _shop_open_since is None:
        _shop_open_since = time.monotonic()


def note_shop_closed() -> None:
    """The shop is closed; the next open starts a fresh session."""
    global _shop_open_since
    _shop_open_since = None


def open_trade_window(timeout: float = 15.0, verbose: bool = True) -> bool:
    """Make sure the Trade window is open on the Register tab.

    The game closes it on its own sometimes; this clicks the Agent Shop NPC to
    reopen it and switches to Register. Already-open is a no-op.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    def wait_until(predicate, limit: float) -> bool:
        deadline = time.monotonic() + limit
        while True:
            if predicate():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)

    if register_tab_open():
        note_shop_opened()
        return True

    # Bound BEFORE the branch, because the branch is skipped in the one case
    # this function most needs to handle: the Trade window already open but
    # showing the wrong tab. `index` used to be bound only inside the block
    # below, so record("shop.opened", attempts=index) raised UnboundLocalError
    # and killed the cycle -- and the Register-tab recovery immediately after
    # it, which is the entire fix for that state, was unreachable.
    #
    # Every cycle goes through here (prepare_for_actions, ensure_shop_ready,
    # _relist_cycle), so it stops three cycles in a row and trips the breaker.
    # Reached by opening the Purchase tab by hand, or by close_shop leaving the
    # window up when a dialog blocks Escape.
    index = 0
    if not trade_window_open():
        # Try each offset from the label in turn, re-locating the label every
        # time: a miss lands on the ground, which walks the character and moves
        # both the label and the NPC, so a stale point would miss again.
        # `seen` so the recorded frame is the one she was matched in. Without
        # it the fallback to _last_shot can save a later frame in which the
        # Trade window covers her -- a frame labelled npc.found that does not
        # contain the NPC is worse than no frame, because a test then asserts
        # against it.
        seen: dict = {}
        label = find_npc(seen=seen)
        if label is None:
            # RECORD THE FAILURE, not just the successes.
            #
            # This branch is the single most common way a run ends, and it
            # left no frame at all. On 2026-08-05 the recording ran to
            # 21:50:18 and the cycle died at 21:50:43 -- twenty-five seconds
            # of the only thing worth seeing, with nothing on disk. The
            # explanation had to be guessed from a screenshot taken after the
            # fact, and the guess was wrong.
            #
            # The words are captured too. "She is not on screen" is
            # indistinguishable between: she really is not there, the Trade
            # window is clipping her nameplate (the plate reads
            # "y Yekaterina (Agent Shop)" when it overlaps), the camera moved,
            # or a disconnect dialog is covering the world. Only the text can
            # tell those apart.
            probe = grab()
            words = sorted(find_words(probe, NPC_SEARCH_REGION, 25),
                           key=lambda w: -w.conf)[:12]
            strongest = ", ".join(f"{w.text!r}@{w.conf:.0f}" for w in words)
            record("npc.not_found", probe, region=str(NPC_SEARCH_REGION),
                   words=strongest, trade_open=trade_window_open(probe))
            say("Lady Yekaterina (Agent Shop) is not on screen - walk to "
                "her before running this. Nothing was clicked.")
            say(f"  strongest words where she should be: {strongest}")
            return False
        record("npc.found", seen.get("shot"), centre=str(label))
        say(f"Found 'Lady Yekaterina (Agent Shop)' at {label}; sweeping "
            f"{len(NPC_CLICK_OFFSETS)} points beneath it.")

        opened = False
        index = 0
        lost = 0
        # A miss is a move order, so an unbounded sweep does not just waste
        # time -- it walks the character away from the NPC, one click at a
        # time. Both limits below exist to stop that: a wall clock, because at
        # ~6s an attempt the full sweep is ~10 minutes of clicking into the
        # world, and a lost-nameplate count, because `or label` deliberately
        # keeps the last known point and would otherwise keep firing at a
        # coordinate the character has already left.
        sweep_deadline = time.monotonic() + NPC_SWEEP_BUDGET
        for index, offset in enumerate(NPC_CLICK_OFFSETS, start=1):
            if time.monotonic() >= sweep_deadline:
                say(f"  giving up after {index - 1} attempts "
                    f"({NPC_SWEEP_BUDGET:g}s budget spent).")
                break

            # Re-locate every time: a miss lands on the ground, which walks the
            # character, moving both the nameplate and the NPC. One frame only
            # -- find_npc's default retries poll on fresh frames, which is the
            # right trade for a single lookup but not a hundred of them.
            fresh = find_npc(retries=1)
            lost = 0 if fresh else lost + 1
            if lost >= NPC_LOST_LIMIT:
                say(f"  the nameplate has not been visible for {lost} attempts "
                    "- she is out of view, so stopping rather than clicking "
                    "the world blind.")
                break
            label = fresh or label

            point = (label[0] + offset[0], label[1] + offset[1])
            say(f"  try {index}/{len(NPC_CLICK_OFFSETS)}: {point}  "
                f"({offset[0]:+}, {offset[1]:+} from the name)")
            click(*point)
            time.sleep(NPC_CLICK_WAIT)

            # Cheap probe first; only pay for the OCR check when it looks open.
            if panel_covers_trade_area() and trade_window_open():
                opened = True
                break

        if not opened:
            say(f"The Trade window did not open after {index} attempt(s) "
                "beneath the nameplate. Stand closer so she is fully on "
                "screen, then retry.")
            return False
    record("shop.opened", attempts=index)

    if register_tab_open():
        note_shop_opened()
        return True

    tabs = find_text(grab(), REGISTER_TAB_WORD, TRADE_REGION)
    if not tabs:
        say("Could not find the Register tab.")
        return False
    tab = tabs[0]  # the tab sits above the table's Register buttons
    record("tab.before_register_click", centre=str(tab.centre))
    click(*tab.centre)
    if not wait_until(register_tab_open, timeout):
        say("The Register tab did not open.")
        return False
    record("tab.register_open")
    note_shop_opened()
    return True


def refresh_table(timeout: float = 20.0, verbose: bool = True) -> bool:
    """Click the Trade window's Refresh button and wait for the reload.

    Worth doing before acting: the client's copy of the table goes stale, so a
    row can read On Sale here while the server already has it sold.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    # One capture, used for both the button search and the record: the pair
    # before/after a refresh is the only evidence of what the reload changed,
    # which is what makes a stale-table bug reconstructable afterwards.
    shot = grab()
    record("refresh.before", shot)
    buttons = find_text(shot, "Refresh", TRADE_REGION)
    if not buttons:
        record("refresh.no_button", shot)
        say("Refresh button not found - is the Trade window open?")
        return False

    click(*buttons[-1].centre)
    time.sleep(0.6)
    if not wait_for_table(max(timeout, 20.0)):
        record("refresh.timeout")
        say("The table did not finish refreshing.")
        return False
    record("refresh.after")
    return True


def find_change_buttons(source: Image.Image | Path | str | None = None) -> list[Word]:
    """The visible 'Change' buttons, ordered top-to-bottom."""
    image = source if source is not None else grab()
    return find_text(image, "Change", TRADE_REGION)


def find_row_buttons(image: Image.Image,
                     words: "list[Word] | None" = None) -> list[Word]:
    """One button per visible table row, ordered top-to-bottom.

    `words` lets a caller that has already OCR'd the table pass the result in,
    instead of paying for three more full-region passes.
    """
    if words is None:
        hits: list[Word] = []
        for word in BUTTON_WORDS:
            hits.extend(find_text(image, word, TRADE_REGION))
    else:
        wanted = tuple(w.casefold() for w in BUTTON_WORDS)
        hits = [w for w in words
                if any(t in w.text.casefold() for t in wanted)
                and w.conf >= 40.0]
    if not hits:
        return []

    # The left rail has its own Register button; keep only the Function column,
    # located from the Change buttons (or whatever else is most common).
    anchors = [w for w in hits if "change" in w.text.casefold()] or hits
    xs = sorted(w.centre[0] for w in anchors)
    column_x = xs[len(xs) // 2]
    hits = [w for w in hits if abs(w.centre[0] - column_x) <= 45]

    # Two words can land on one row (OCR splitting); keep one per y band.
    hits.sort(key=lambda w: w.top)
    deduped: list[Word] = []
    for w in hits:
        if deduped and w.top - deduped[-1].top < 20:
            continue
        deduped.append(w)
    return deduped


def table_loading(source: Image.Image | Path | str) -> bool:
    """True while the Trade window shows 'Waiting for the server response'.

    During a refresh every row reads Register and the counts read 0, so reading
    the table then yields a confidently wrong answer.
    """
    return bool(find_text(source, "Waiting", TRADE_REGION))


def wait_for_table(timeout: float = 20.0, poll: float = 1.0) -> bool:
    """Block until the table has finished refreshing. False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not table_loading(grab()):
            return True
        time.sleep(poll)
    return False


def _header(words: "list[Word] | None", image: Image.Image, needle: str):
    """The topmost header word, from a pre-OCR'd list when one is supplied."""
    if words is None:
        hits = find_text(image, needle, TRADE_REGION)
    else:
        low = needle.casefold()
        hits = sorted((w for w in words
                       if low in w.text.casefold() and w.conf >= 40.0),
                      key=lambda w: w.top)
    return hits[0] if hits else None


def name_column(image: Image.Image,
                words: "list[Word] | None" = None) -> tuple[int, int]:
    """x-range of the Name column, derived from the Name and QTY headers so it
    tracks the Trade window being resized."""
    header = _header(words, image, "Name")
    qty_header = _header(words, image, "QTY")
    names = [header] if header else []
    qtys = [qty_header] if qty_header else []
    if not names or not qtys:
        return NAME_COLUMN
    header, qty = names[0], qtys[0]
    centre = (header.left + header.right) // 2
    half = qty.left - centre
    if half <= 0:
        return NAME_COLUMN
    left, right = max(0, centre - half + 4), qty.left - 6
    # A reversed box makes PIL raise mid-sequence, possibly after a cancel has
    # committed; fall back rather than crash.
    if right - left < 40:
        return NAME_COLUMN
    return (left, right)


def price_column(image: Image.Image,
                 words: "list[Word] | None" = None) -> tuple[int, int] | None:
    """x-range of the Price column, bounded by the QTY and Status headers."""
    qty_header = _header(words, image, "QTY")
    status_header = _header(words, image, "Status")
    if qty_header is None or status_header is None:
        return None
    qtys, statuses = [qty_header], [status_header]
    left, right = qtys[0].right + 4, statuses[0].left - 4
    return (left, right) if right > left else None


def await_rows(timeout: float = TABLE_READ_BUDGET, poll: float = 0.5) -> list[Row]:
    """Read the listings, retrying until the table is non-empty.

    A single read comes back empty often enough -- mid-refresh, a tooltip over
    the table, plain OCR flake -- that using one as a gate turns a working table
    into "the Trade window must be closed".

    The budget is measured in whole reads, not seconds: a full 10-row table
    costs ~18s of OCR, so any timeout under that permits exactly one attempt
    and the retry never happens.
    """
    # Park the cursor here rather than trusting every caller to. A tooltip over
    # the table produced a confidently wrong row on 23% of unparked captures --
    # a real listing reading as an empty slot, with all ten buttons present and
    # evenly spaced, so no guard fired. Only 2 of 8 call sites parked first,
    # and one that did not is the re-read that decides whether to withdraw a
    # listing. Best-effort: a refused move must not stop a read.
    try:
        park_cursor()
    except PermissionError:
        pass

    deadline = time.monotonic() + max(timeout, TABLE_READ_BUDGET)
    while True:
        shot = grab()
        # A table mid-refresh is not an empty table: every row reads "Register"
        # and the counts read 0, so read_rows returns ten confidently wrong
        # rows. Callers then either skip them all as empty slots and report
        # "All rows processed" having done nothing, or filter them out and
        # report live listings "already sold out". Only the empty result was
        # ever retried, so the wrong answer went straight through.
        if not table_loading(shot):
            rows = read_rows(shot)
            if rows:
                return rows
        if time.monotonic() >= deadline:
            return []
        time.sleep(poll)


def read_rows(source: Image.Image | Path | str) -> list[Row]:
    """Every visible table row, numbered top-to-bottom as displayed.

    Rows are anchored on the Function-column button, then the name is read from
    that button's vertical band.
    """
    image = source if isinstance(source, Image.Image) else Image.open(source)

    # ONE OCR pass for the whole table, then slice it locally.
    #
    # This used to run ~37 separate Tesseract invocations per read -- three for
    # the buttons, four for the column headers, and one per name, price and
    # quantity cell -- costing about 7s. A single pass over the same region
    # returns the same words in 0.66s, and the row/column logic below is
    # unchanged: it filters a list instead of cropping and re-OCRing.
    #
    # min_conf=0 here because the callers below apply their own bars: 40 for
    # names, prices and headers, QTY_COL_MIN_CONF for quantities. Collecting at
    # the lowest bar and filtering up keeps each of those exactly as it was.
    words = find_words(image, TRADE_REGION, 0.0)
    if not words:
        return []

    buttons = find_row_buttons(image, words)
    if not buttons:
        return []

    # The shop is a fixed EXPECTED_ROWS slots and every slot always carries one
    # of BUTTON_WORDS -- empty ones read "Register" -- so anything else is a
    # partial read, not a short table. This is the only check that catches a
    # button missed at the TOP or BOTTOM: those leave the remaining buttons
    # evenly spaced, so the gap guard below sees nothing wrong while every row
    # is numbered one out and "cancel row 7" hits row 8. await_rows() retries
    # on a fresh frame, so rejecting here costs a read, not the run.
    if len(buttons) != EXPECTED_ROWS:
        return []

    # Scaled, because it is a distance inside the Trade window. Only reached
    # when a single button is visible, which the cardinality gate above already
    # rejects -- kept so the value is never silently a reference-machine pixel.
    pitch = LAYOUT.length(REF_ROW_PITCH)
    if len(buttons) > 1:
        gaps = sorted(b.top - a.top for a, b in zip(buttons, buttons[1:]))
        pitch = gaps[len(gaps) // 2]  # median, not mean: one gap must not skew it
        # A gap of roughly two pitches means a button failed to OCR. Row
        # numbers are assigned by counting buttons, so a missed one shifts
        # every row below it and "cancel row 7" would hit row 8.
        if any(gap > pitch * 1.6 for gap in gaps):
            return []

    left, right = name_column(image, words)
    price_bounds = price_column(image, words)

    def cell(x0: int, y0: int, x1: int, y1: int, min_conf: float = 40.0):
        """The already-OCR'd words inside a cell, in place of a fresh pass.

        Matched on the word's centre, so a glyph box straddling a boundary
        belongs to exactly one cell -- the same rule a crop applied, since a
        crop cut the glyph and Tesseract then read whichever part it had.
        """
        return [w for w in words
                if w.conf >= min_conf
                and x0 <= w.centre[0] <= x1 and y0 <= w.centre[1] <= y1]

    rows: list[Row] = []
    for i, button in enumerate(buttons, start=1):
        cy = button.centre[1]
        top, bottom = cy - pitch // 2, cy + pitch // 2
        # Drop icon noise from the name cell. Items with option slots draw an
        # empty socket box inside the name, which Tesseract reads as a short
        # junk token -- 'an' at confidence 40.2 against a 40.0 bar, 'mm', '=o'.
        # Whether it lands above or below the bar changes the item's NAME, and
        # identity matching is exact, so a row would flip between matching and
        # "already sold out" frame to frame. A real name word is either longer
        # than two characters or read confidently.
        cell_words = [w for w in cell(left, top, right, bottom)
                      if len(w.text.strip()) > 2 or w.conf >= NAME_FRAGMENT_MIN_CONF]
        # Group into lines by proximity, as _text_lines does, then read each
        # line left to right. Bucketing on `w.top // 12` sorted by a fixed
        # 12-pixel grid instead: two words on the SAME line whose tops straddle
        # a bucket boundary -- routine, since Tesseract reports the glyph box
        # and capitals sit higher -- came out in the wrong order. That made
        # "Master's SIGMetal Headgear (FA)" read as "Master's Headgear
        # SIGMetal (FA)", which matches nothing, and the row was reported
        # "already sold out". Data-dependent, so it fired intermittently.
        name = " ".join(w.text for line in _text_lines(cell_words) for w in line)

        # An empty name on a row that is plainly a live listing means the bulk
        # pass could not segment it, NOT that the slot is vacant -- re-read
        # just that cell before believing it.
        #
        # Tesseract's sparse-text segmentation depends on the crop it is given.
        # Over the whole 2450x2070 table it drops names it reads perfectly from
        # a cell-sized image: measured on a real listing, the full-table pass
        # returned only the socket icon while the same pixels cropped to the
        # cell gave 'Dragonium Daikatana of Outrageous + 5' at 93% confidence.
        # That listing is worth 298,000,021 Alz, and read_rows called it
        # '(empty)' -- which relist_rows skips as a vacant slot, silently, while
        # reporting the cycle a success.
        #
        # Costs one extra OCR pass, and only for rows that would otherwise be
        # reported nameless. A genuinely empty slot has no button text either,
        # so this cannot resurrect one: it is gated on the row having a real
        # action word.
        if not name.strip() and button.text.strip().casefold() != "register":
            retry = [w for w in find_words(image, (left, top, right, bottom), 40.0)
                     if len(w.text.strip()) > 2 or w.conf >= NAME_FRAGMENT_MIN_CONF]
            if retry:
                name = " ".join(w.text for line in _text_lines(retry)
                                for w in line)

        # Join the words before parsing. Taking max() over separate words means
        # a price OCR splits -- "105," + "000,000" -- reads as the fragment 105.
        #
        # Take a single LINE, not everything in the band: the band is a full
        # row pitch tall and centred on the button rather than on the text, so
        # it catches the neighbouring row's price, whose digits then interleave
        # by x into one nonsense number.
        price = None
        if price_bounds:
            cell_lines = _text_lines(
                cell(price_bounds[0], top, price_bounds[1], bottom))
            if cell_lines:
                nearest = min(cell_lines,
                              key=lambda ln: abs(_span_centre(ln)[1] - cy))
                price = _digits("".join(w.text
                                        for w in sorted(nearest,
                                                        key=lambda w: w.left)))

        # QTY sits between the Name and Price columns. A lone digit there OCRs
        # at low confidence, so this needs a lower bar than the default.
        # Join, as with the price: "12" splitting into "1" and "2" read as 2,
        # which made sanity_check withdraw a correct listing for "wrong qty".
        qty = None
        if price_bounds and price_bounds[0] - (right + 2) >= 8:
            box = (right + 2, top, price_bounds[0] - 2, bottom)
            digits = sorted(cell(*box, QTY_COL_MIN_CONF), key=lambda w: w.left)
            qty = _digits("".join(w.text for w in digits))
            if qty is None:
                # The single-digit rescue still needs its own targeted passes:
                # a lone digit in this narrow cell returns NO words from the
                # bulk pass at any confidence -- measured at ~30% of quantity
                # cells -- which is exactly what read_number's psm-7/psm-10
                # fallbacks exist for. Only reached when the bulk pass found
                # nothing, so it costs a subprocess for those cells alone
                # rather than for all ten.
                qty = read_number(image, box, QTY_COL_MIN_CONF)

        action = button.text.strip().casefold()
        # An empty slot may or may not have an empty name cell, and the
        # difference matters enormously.
        #
        # A row saying "Register" whose name cell holds an ITEM name is a
        # misread: every caller treats action='register' as "nothing here", so
        # a live listing would be skipped silently. That is worth discarding
        # the whole frame over.
        #
        # But the game LABELS its own vacant premium slots "Premium Exclusive
        # Slot", in the name column, at 96% confidence. The original comment
        # here guessed that was "a screen overlay". It is not -- it is the
        # game, and treating it as a misread threw away every row in the table.
        #
        # Measured cost of that mistake: 522 of 2,575 recorded frames, and one
        # live run stopped. A sold-out row was collected, the slot it vacated
        # became a labelled premium slot, read_rows returned nothing for the
        # full 45-second budget, and three cycles later the failure breaker
        # ended the run. The table was perfectly readable the whole time.
        if action == "register" and name.strip():
            if PREMIUM_SLOT_MARKER in _normalise(name):
                name = ""            # a vacant slot, exactly as it says
            else:
                return []
        rows.append(Row(
            index=i, name=name.strip() or "(empty)", change=button.centre,
            top=top, bottom=bottom, action=action,
            price=price, qty=qty,
        ))
    return rows


def dialog_button(
    source: Image.Image | Path | str, word: str, min_conf: float = 40.0
) -> Word | None:
    """A dialog button by label. Takes the lowest match, since the title bar can
    repeat the word and the buttons sit along the bottom.

    The table's own Change/Receive buttons are excluded by DISTANCE FROM THE
    FUNCTION COLUMN, not by an absolute x. "Receive" appears on both a table
    row and the Confirm Receipt dialog, so they must be told apart -- but an
    absolute boundary only works while the dialog sits to the right of the
    table, which is true only if the dialog and the Trade window scale
    together. If the game keeps its UI at a fixed size on a smaller screen the
    dialog is centred on the client and lands LEFT of the table, and a
    boundary tuned for the reference machine filters out every real dialog
    button -- so a cancel aborts after its Change click has committed.
    """
    column_x = LAYOUT.x(REF_FUNCTION_COLUMN_X)
    keep_away = max(40, LAYOUT.length(60))
    hits = [w for w in find_text(source, word, POPUP_REGION, min_conf)
            if abs(w.centre[0] - column_x) > keep_away]
    return hits[-1] if hits else None


def await_dialog_button(
    word: str, timeout: float = 6.0, poll: float = 0.4
) -> Word | None:
    """Wait for a dialog button to be readable.

    A dialog that is up stays up, so a miss means OCR flaked on that frame, not
    that the button is absent -- retry on fresh frames, then once more with a
    lower confidence bar before giving up.
    """
    deadline = time.monotonic() + timeout
    while True:
        button = dialog_button(grab(), word)
        if button is not None:
            return button
        if time.monotonic() >= deadline:
            break
        time.sleep(poll)
    # Last resort: the same frame often does contain the word, just faintly.
    return dialog_button(grab(), word, min_conf=15.0)


def _mentions(texts: list[str], keyword: str, threshold: float = 0.85) -> bool:
    """True when one of `texts` is `keyword`, tolerating a slipped character.

    A dialog is identified by a single word, so one misread character decides
    whether the script thinks it is there: "Extension" came back as
    "Exteneion", the Registration Extension dialog read as "no dialog at all",
    and a cancel aborted after its Change click had already gone in.

    Fuzziness is dangerous in the other direction, though: "Register" sits on
    every empty row's button and all over the Register panel, a stem away from
    "Registration". Similarity alone does separate them (0.70 against a 0.85
    bar), but not by much once the word on screen is itself misread, so the
    length guard rejects the pairing outright rather than relying on that gap.
    """
    from difflib import SequenceMatcher

    for text in texts:
        if keyword in text:
            return True
        if abs(len(text) - len(keyword)) > 2:
            continue
        # A clipped fragment of the right word. Dialog titles render as light
        # text on a blue gradient, and _prep_for_text autocontrasts the whole
        # region at once -- so the title goes grey-on-grey while the body and
        # buttons stay crisp, and Tesseract clips its ends. Measured on a real
        # capture with a Confirm Receipt modal plainly up: the only usable
        # token was 'eceip', which scores 0.833 against 'receipt' and missed
        # the 0.85 bar by 0.017, so dialog_kind reported no dialog at all.
        #
        # Placed BELOW the length guard on purpose: that guard is what stops
        # 'register' (8) matching 'registration' (12), and this line inherits
        # it. Lowering the threshold instead would move toward 'receive'
        # (0.714), which sits on every sold row of the table.
        if text in keyword:
            return True
        if SequenceMatcher(None, keyword, text).ratio() >= threshold:
            return True
    return False


def dialog_kind(source: Image.Image | Path | str) -> str | None:
    """Which dialog is up: 'extension', 'confirm', 'receipt', or None.

    Clicking Change opens the Registration Extension dialog ([Register]
    [Cancel]); its Cancel leads to a confirmation dialog ([Confirmation]
    [Cancel]) that actually pulls the listing. Both put their buttons in the
    same place, so they must be told apart by their text, not geometry.
    """
    # Match against JOINED LINES as well as individual words, at a low
    # confidence bar.
    #
    # Two measured failures, both of which produced "the Registration
    # Extension dialog did not appear" while the dialog was plainly up:
    #
    #  * Tesseract's segmentation is crop-dependent. The Trade window's own
    #    title reads at 96% confidence in a tight crop and is NOT FOUND AT ALL
    #    when handed a POPUP_REGION-sized crop -- the size this function always
    #    uses. Ornate title glyphs are exactly what it drops at that scale.
    #  * A split title defeats whole-word matching: ['regi','stration',
    #    'exten','sion'] matches nothing. This UI splits ornate titles
    #    routinely -- find_phrase exists because "Inventory" arrives as "I" +
    #    "nventory" -- and dialog_kind was the only title-matching path in the
    #    file that did not stitch a line back together first.
    words = find_words(source, POPUP_REGION, DIALOG_TEXT_MIN_CONF)
    texts = [_normalise(w.text) for w in words]
    texts += [_normalise("".join(w.text for w in line))
              for line in _text_lines(words)]
    if _mentions(texts, "receipt"):
        return "receipt"
    if _mentions(texts, "confirmation"):
        return "confirm"
    # Either word of the title will do. They fail independently -- "Extension"
    # misread while "Registration" read at 96% confidence on the same frame --
    # so requiring one specific word makes the check as weak as its worst word.
    if _mentions(texts, "extension") or _mentions(texts, "registration"):
        return "extension"
    return None


def dialog_present(source: Image.Image | Path | str | None = None) -> bool:
    """Is ANY dialog on screen? Deliberately harder to fool than dialog_kind.

    dialog_kind decides by reading the TITLE, and its own docstring records why
    that is fragile: Tesseract's segmentation is crop-dependent and drops
    ornate title glyphs at POPUP_REGION scale, so it returns None with a modal
    plainly up. Every dialog also carries a Cancel button, which survives that
    failure -- close_any_dialog already prefers the button finder for exactly
    this reason, and says so.

    Trusting dialog_kind alone to say "nothing is open" is what turned one bad
    row into two dead cycles on 2026-08-04. The abort path asked it, got None
    while the diagnostic one line earlier had read 'extension', closed nothing,
    and left a modal covering the table. Escape could then not close the Trade
    window, and every read for the rest of that cycle and the whole of the next
    returned no rows.

    Answering "is something open" with a false NO is the expensive direction.
    A false YES only costs a harmless Cancel click.
    """
    shot = source if source is not None else grab()
    if dialog_kind(shot) is not None:
        return True
    return dialog_button(shot, DISMISS_WORD) is not None


def confirm_open_dialogs(settle: float = 0.8, verbose: bool = True) -> bool:
    """Click Confirmation through however many dialogs are stacked up.

    Recovery for a run that stopped mid-chain: the game can queue a second
    confirmation (a price warning) behind the first.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    confirmed = 0
    for step in range(1, MAX_CONFIRM_STEPS + 1):
        # The accept button is Confirmation on most dialogs but Receive on
        # Confirm Receipt, so try both before deciding nothing is open.
        button = await_dialog_button(CONFIRM_WORD, timeout=3.0)
        label = CONFIRM_WORD
        if button is None:
            button = await_dialog_button(RECEIPT_WORD, timeout=5.0)
            label = RECEIPT_WORD
        if button is None:
            say(f"Nothing left to confirm after {confirmed} click(s).")
            break
        say(f"Dialog {step}: clicking {label} at {button.centre}")
        click(*button.centre)
        confirmed += 1
        time.sleep(settle)
    # dialog_present(), not await_dialog(None). await_dialog compares
    # dialog_kind(shot) == None, and dialog_kind is documented as returning
    # None with a modal plainly on screen -- which is the entire reason
    # dialog_present exists. Confirming "the dialog is gone" with the reader
    # that cannot see dialogs made this return True over a covered table.
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        if not dialog_present():
            return True
        time.sleep(0.3)
    return False


def close_any_dialog(settle: float = 0.7,
                     tries: int = CLOSE_DIALOG_TRIES) -> bool:
    """Back out of any open dialog without changing anything.

    Both dialogs carry a Cancel button. On the confirmation dialog it closes
    outright; on the Extension dialog it advances to the confirmation dialog,
    whose Cancel then closes both. The game ignores Escape here, so this walks
    the chain with clicks instead.
    """
    # Driven by the button finder rather than dialog_kind(): a single flaked
    # read would otherwise report success with a dialog still on screen.
    for _ in range(tries):
        button = await_dialog_button(DISMISS_WORD, timeout=3.0)
        if button is None:
            break
        click(*button.centre)
        time.sleep(settle)
    # dialog_present(), not await_dialog(None). await_dialog compares
    # dialog_kind(shot) == None, and dialog_kind is documented as returning
    # None with a modal plainly on screen -- which is the entire reason
    # dialog_present exists. Confirming "the dialog is gone" with the reader
    # that cannot see dialogs made this return True over a covered table.
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        if not dialog_present():
            return True
        time.sleep(0.3)
    return False


def _normalise(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


# Characters OCR routinely swaps for one another in this UI. Folding them
# together lets "S1GMetal" and "SIGMetal" compare equal while leaving a real
# difference like "(FA)" vs "(FB)" intact.
# Only the confusions that cannot collide with a real item difference. Folding
# digits like 6/9 or 2/7 would make "+6 Blade" and "+9 Blade" compare EQUAL,
# and match_row's exact branch would then return the wrong item with full
# confidence, bypassing every threshold below.
_OCR_CONFUSIONS = str.maketrans({"0": "o", "1": "i", "l": "i"})

# Fuzzy fallback bar. It sits above the measured similarity of genuinely
# different items -- "SIGMetal Headgear(FA)" and "(FB)" score 0.944 -- because
# below that there is no value that separates a noisy read of the right item
# from a clean read of the wrong one.
FUZZY_NAME_THRESHOLD = 0.95
FUZZY_NAME_MARGIN = 0.03
# A row this similar to what we are looking for, which still failed the bar
# above, means "the name did not read cleanly" -- not "the listing is gone".
# Well below FUZZY_NAME_THRESHOLD on purpose: this is not a match, it is
# evidence that something very like the target is still on screen.
UNMATCHED_NAME_SIMILARITY = 0.70
# A one- or two-character token in a name cell is almost always an option-
# socket icon, not a word. Below this confidence it is dropped, so an item's
# identity does not change with the icon's OCR luck.
NAME_FRAGMENT_MIN_CONF = 60.0


def _canonical(text: str) -> str:
    """Normalised and with OCR-confusable characters folded together."""
    return _normalise(text).translate(_OCR_CONFUSIONS)


# Glyphs a letter can be misread as, folded BEFORE punctuation is stripped.
# Used only for matching an item against its price floor -- never for telling
# two items apart, where this much folding would be reckless.
#
# _canonical cannot serve here: _normalise deletes non-alphanumerics outright,
# so "V|P" becomes "vp" and stops matching "vip", and the floor silently
# vanishes. Folding towards a match is the safe direction for a floor: applying
# too high a floor to the wrong item only means it does not sell, while missing
# one means a 105M item can be listed for whatever the market says.
_FLOOR_LOOKALIKES = str.maketrans({
    "|": "i", "!": "i", "1": "i", "l": "i", "[": "i", "]": "i",
    "/": "i", "\\": "i", "j": "i", ":": "i", ";": "i", "¦": "i",
    "0": "o",
})


def _floor_key(text: str) -> str:
    """Canonical form for price-floor matching. See _FLOOR_LOOKALIKES."""
    folded = text.casefold().translate(_FLOOR_LOOKALIKES)
    return "".join(ch for ch in folded if ch.isalnum())


# A table cell carries a second line after the item name -- "Yekaterina VIP
# Membership" then "Use Period: 30 days" -- and a tooltip adds its own trailer.
# Everything from these markers on is descriptive, not part of the name.
_NAME_TRAILER = re.compile(
    r"\b(use\s*period|duration|grade\s*\d|drop\s*not\s*allowed|register\s*cost"
    r"|item\s*sold|remaining\s*time|sales?\s*price)\b.*",
    re.IGNORECASE | re.DOTALL,
)


def item_name(row_name: str) -> str:
    """The item's name with the descriptive trailer removed."""
    return _NAME_TRAILER.sub("", row_name).strip(" :-")


def match_rows(rows: list[Row], item: str,
               threshold: float = FUZZY_NAME_THRESHOLD) -> list[Row]:
    """Every row naming `item`, in table order.

    Several rows legitimately carry the same name -- two separate stacks of the
    same core -- so this returns all of them and leaves the choice to the
    caller (see `locate_row`). Exact match on the OCR-folded name first; only
    if nothing matches exactly does it fall back to similarity, and then it
    demands both a high score and a clear lead over the runner-up. Item names
    here differ by a single character, so a near-miss is far more likely to be
    the wrong item than a misread of the right one.
    """
    from difflib import SequenceMatcher

    needle = _canonical(item)
    if not needle:
        return []

    exact = [r for r in rows if _canonical(r.name) == needle]
    if exact:
        return exact

    scored = []
    for row in rows:
        candidate = _canonical(row.name)
        if candidate:
            scored.append((SequenceMatcher(None, needle, candidate).ratio(), row))
    if not scored:
        return []

    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]
    best_name = _canonical(best.name)

    # Measure the margin against the best DIFFERENTLY-named row, and return
    # every row sharing the winning name.
    #
    # Taking scored[1] blindly made fuzzy matching structurally impossible
    # exactly where it was needed most: with two rows of the same item, the
    # runner-up IS the identical twin, the margin is ~0, and this returned []
    # for both. locate_row read that as 'missing' and the batch reported two
    # perfectly good stacks "already sold out" -- the very misreport the
    # duplicate handling was built to end.
    runner_up = next((score for score, row in scored[1:]
                      if _canonical(row.name) != best_name), 0.0)

    # Scale the bar with the name's length. A single substituted character
    # scores about 1 - 1/n, so a fixed 0.95 only rejects names shorter than 20
    # characters: "Master's SIGMetal Headgear (FA)" vs "(FB)" scores 0.96 and
    # would otherwise pass as a match.
    required = max(threshold, 1.0 - 1.0 / max(len(needle), 1) + 0.005)
    if best_score < required or best_score - runner_up < FUZZY_NAME_MARGIN:
        return []
    return [row for _, row in scored if _canonical(row.name) == best_name]


def match_row(rows: list[Row], item: str,
              threshold: float = FUZZY_NAME_THRESHOLD) -> Row | None:
    """The one row naming `item`, or None when there is not exactly one.

    Use this only where a second row of the same name genuinely means "cannot
    proceed". Anywhere a duplicate name is normal -- the shop happily holds two
    stacks of the same item -- use `locate_row`, which tells them apart.
    """
    found = match_rows(rows, item, threshold)
    return found[0] if len(found) == 1 else None


@dataclass(frozen=True)
class RowRef:
    """Enough of a listing to find it again once the table has been redrawn.

    A name alone is not an identity: the shop happily holds two stacks of the
    same item, and matching on the name then finds two rows and gives up.
    Quantity, then price, then position among the same-named rows narrow it
    down -- in that order, because quantity survives a relist unchanged while
    price is exactly what a relist is meant to alter.
    """
    name: str
    qty: int | None = None
    price: int | None = None
    ordinal: int = 0  # 0-based position among rows sharing this name

    @classmethod
    def of(cls, row: Row, rows: list[Row] | None = None) -> "RowRef":
        # The ordinal counts only rows that are indistinguishable from this one
        # in every readable respect -- the same set `locate_row` is left
        # holding after its filters. Counting over a wider set (all rows
        # sharing the name) would index into a narrower pool and land on the
        # wrong sibling, or off the end of it.
        ordinal = 0
        if rows:
            for position, candidate in enumerate(cls._siblings(rows, row)):
                if candidate.index == row.index:
                    ordinal = position
                    break
        return cls(row.name, row.qty, row.price, ordinal)

    @staticmethod
    def _siblings(rows: list[Row], row: Row) -> list[Row]:
        """The pool locate_row will be left holding, narrowed the same way.

        It must mirror locate_row exactly, or the ordinal counts positions in
        one set and is then used to index another. Filtering unconditionally
        (including None == None) diverged whenever a quantity or price was
        unreadable -- routine for the QTY column -- and the ordinal then
        pointed at a different row than the one it was measured from.
        """
        pool = match_rows(rows, row.name)
        for value, attribute in ((row.qty, "qty"), (row.price, "price")):
            if value is None:
                continue
            narrowed = [r for r in pool if getattr(r, attribute) == value]
            if narrowed:
                pool = narrowed
        return pool


# --------------------------------------------------------------------------
# Scrolling the listings table
# --------------------------------------------------------------------------
#
# The shop holds more listings than the ten on screen. Scrolling is only safe
# if the script knows WHICH listings it is looking at afterwards, and read_rows
# numbers rows by screen position -- so after a scroll, "row 1" is a different
# listing with nothing to signal it. Every caller that acts on an index would
# be acting on the wrong one.
#
# Measured on the live shop:
#   * one wheel notch moves exactly one row
#   * the row bands do NOT move; only content scrolls, so a screen position's
#     click target is stable
#   * scrolling clamps at both ends, so over-scrolling is safe and idempotent
#
# The offset is therefore recovered by matching content across two reads, never
# by counting notches. Stepping ONE row at a time leaves nine of ten rows
# overlapping, which over-determines the answer; a seven-row step leaves
# exactly three, the bare minimum, and a twenty-row step leaves none.

# Beyond any plausible list length. Over-scrolling clamps, so this is how the
# view is driven to a known end rather than tracked with a running counter --
# a counter drifts, and re-deriving from a known end cannot.
SCROLL_TO_END_NOTCHES = 40
# Rows that must overlap before an offset is believed.
MIN_SCROLL_OVERLAP = 3
# When the exact test finds nothing, how much of the overlap must still agree
# before an offset is a candidate at all, and by how many rows the best
# candidate must beat the runner-up before it is believed.
#
# The margin is the safety-critical one. A threshold alone would accept the
# best of two near-identical candidates, and picking the wrong offset means
# cancelling the wrong listing -- much worse than losing a cycle. Requiring
# daylight between first and second place is what refuses instead.
SCROLL_MATCH_RATIO = 0.6
SCROLL_MATCH_MARGIN = 2
# Distinctive (non-empty) rows that must agree before an offset is believed.
SCROLL_MATCH_MIN_LIVE = 2


def _row_key(row: Row) -> tuple:
    """What makes two sightings the same listing, for scroll matching."""
    return (row.name, row.price, row.qty, row.action)


def measure_shift(before: list[Row], after: list[Row],
                  minimum: int = MIN_SCROLL_OVERLAP,
                  expected: int | None = None) -> int | None:
    """How many rows the view moved down, or None if it cannot be told.

    Returns EVERY offset that fits and refuses unless exactly one does. With
    duplicate listings more than one can fit -- 41 of 43 recorded tables carry
    rows identical in name, quantity and price -- and taking the first would
    silently pick the wrong one.
    """
    b = [_row_key(r) for r in before]
    a = [_row_key(r) for r in after]
    if not b or not a:
        return None

    # Two identical reads mean the content did not change. Reported honestly as
    # 0, and NOT overridden by the expected-shift rule below -- claiming the
    # view moved when the pixels say otherwise would advance the absolute index
    # past rows that never scrolled by, mislabelling every listing after them.
    #
    # What 0 does NOT mean is "the bottom". A screen of empty slots scrolled
    # within a run of empty slots reads identically to one that did not move at
    # all, so treating 0 as the end of the shop stops the sweep in the middle
    # of it. That is exactly what happened on 2026-08-06: enumerate_listings
    # reported 14 slots and 4 live while eight listings sat below the gap, four
    # of them sold and uncollected, and the run relisted the top of the shop
    # for forty minutes without ever seeing them -- silently, because a
    # truncated sweep and a complete one look identical to the caller.
    #
    # So the bottom is established by scrolling to it and comparing against it
    # (in _enumerate_at_step), and 0 only ever means "nothing moved". A 0 that
    # arrives before the bottom screen is a stuck view, and is reported as a
    # failure rather than as a finished sweep.
    if b == a:
        # Identical reads mean one of two things, and which one depends on what
        # is on the screen.
        #
        # If ANY row is nameable, a real move would have changed the view, so
        # nothing moved and 0 is the honest answer -- and it must not be
        # overridden by the wheel, or the absolute index advances past rows
        # that never scrolled by and every listing after them is mislabelled.
        #
        # If every row is an empty slot, the two reads are identical whether
        # the view moved or not, and content has no opinion at all. The wheel
        # does: a notch moves a row. Answering 0 there wedges the sweep in the
        # middle of the gap -- "the view stopped moving before the bottom
        # screen was reached" -- which is what this shop did at 14:5x with a
        # fifteen-row run of empty slots. Nothing is mislabelled by advancing,
        # because every row being stepped over is empty by construction, and
        # the sweep still stops at the measured bottom screen rather than here.
        if expected and all(r.name == "(empty)" for r in before):
            return expected
        return 0

    # Only offsets the wheel could actually have produced are candidates.
    #
    # A downward scroll of N notches moves the view between 0 and N rows. It
    # cannot move it UP, and it cannot overshoot. Searching the whole range
    # anyway invented candidates that then made a perfectly determined shift
    # look ambiguous -- recorded live on 2026-08-06 for a view that had moved
    # exactly 3 rows with seven of them agreeing:
    #
    #     exact fits: [(3, 7), (-6, 4), (-7, 3)]
    #
    # Shifts of minus six and minus seven are nonsense: the wheel was asked to
    # go down. They fit only the three or four mostly-empty rows at the screen
    # edge, and their presence alone was enough for this to refuse and for the
    # cycle to be lost. The bound already existed in scroll_chunk -- it checks
    # `0 <= shift <= notches` -- but only AFTER the answer had been discarded.
    candidates = (range(0, expected + 1) if expected is not None
                  else range(-len(b), len(b) + 1))

    # Pass 1: the exact test. When the table held still this is what answers,
    # and it is the strongest evidence available -- every row in the overlap
    # agreeing. Nothing below can override it.
    fits = []
    for shift in candidates:
        d = -shift
        overlap = [(i, i + d) for i in range(len(b)) if 0 <= i + d < len(a)]
        if len(overlap) >= minimum and all(b[i] == a[j] for i, j in overlap):
            fits.append(shift)
    if len(fits) == 1:
        return fits[0]

    if fits:
        # Several offsets fit perfectly. Usually that is real ambiguity and the
        # only safe answer is to refuse -- but there is one case where it is
        # not, and it is the case that stopped this shop dead.
        #
        # Once the view is inside a long run of empty slots, EVERY offset fits,
        # because empty rows are indistinguishable. Recorded live on
        # 2026-08-06, mid-sweep:
        #
        #     exact fits: [(7,3), (6,4), (5,5), (4,6), (3,7), (2,8), (1,9)]
        #
        # No step size helps; the rows carry no information at any scale. But
        # the wheel does: a notch moves one row, validated against the recorded
        # probes. So when the overlap is made up ENTIRELY of empty slots, the
        # content has no opinion and the mechanism does -- take the shift that
        # was asked for, provided it is one of the offsets that fits.
        #
        # This cannot mislabel a listing: every row it skips over is empty by
        # construction. The moment one nameable row enters the overlap, the
        # exact test discriminates again and this branch stops applying.
        if expected is not None and expected in fits:
            overlap = [i for i in range(len(b))
                       if 0 <= i - expected < len(a)]
            if overlap and all(before[i].name == "(empty)" for i in overlap):
                return expected
        return None          # genuinely ambiguous: refuse, do not fall through

    # Pass 2: the table moved under us.
    #
    # `all()` means ONE changed row anywhere in the overlap makes the true
    # offset match nothing, and then nothing matches at all -- not ambiguity,
    # zero candidates. Each of these is routine here and each was enough on its
    # own: a quantity misreading (140 -> 120 and 130 -> 30 both recorded on the
    # 07:57 run of 2026-08-06), a listing selling during the ~18s scroll, or
    # this script's own repricing between the two reads.
    #
    # It cost cycles 5 and 6 of that run outright, and the breaker stopped a
    # run that was otherwise working.
    #
    # So: score the offsets and take the best, but only when it is BOTH mostly
    # right and clearly ahead of the runner-up. Guessing between two close
    # candidates is how the wrong listing gets cancelled, which is far worse
    # than losing a cycle -- the margin below is what keeps that from
    # happening, and it is checked against the runner-up, not against a
    # threshold.
    # Only DISTINCTIVE rows vote. An empty slot is identical to every other
    # empty slot, so a block of them fits at any offset and manufactures
    # candidates out of nothing.
    #
    # This is not hypothetical. Scoring plain matches on the real cycle-6 shop
    # -- which had seven consecutive empty rows -- returned 5 for a view that
    # had actually moved 7, because five empties lined up against five others.
    # A wrong offset cancels the wrong listing, which is the one outcome worth
    # losing any number of cycles to avoid, so an empty row is worth no
    # evidence at all here.
    live_b = [name != "(empty)" for name in (r.name for r in before)]

    scored: list[tuple[int, int]] = []           # (informative matches, shift)
    for shift in candidates:
        d = -shift
        overlap = [(i, i + d) for i in range(len(b)) if 0 <= i + d < len(a)]
        if len(overlap) < minimum:
            continue
        agree = [(i, j) for i, j in overlap if b[i] == a[j]]
        if len(agree) < SCROLL_MATCH_RATIO * len(overlap):
            continue                             # mostly disagrees; not this offset
        speaking = sum(1 for i, _ in agree if live_b[i])
        if speaking < SCROLL_MATCH_MIN_LIVE:
            continue                             # nothing distinctive agreed
        scored.append((speaking, shift))
    if not scored:
        return None

    scored.sort(reverse=True)
    best, shift = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0
    if best - runner_up < SCROLL_MATCH_MARGIN:
        return None
    return shift


def table_scrollable(verbose: bool = True) -> bool:
    """Whether wheel notches will reach the listings rather than the camera.

    With the Trade window shut the wheel is a CAMERA ZOOM, and scroll_to_end
    sends forty notches. On 2026-08-06 that zoomed the view so far in that the
    NPC left the screen entirely, so the next two cycles could not find her,
    the breaker stopped the run, and the camera had to be wound back by hand --
    from one row whose scroll happened a moment after the window closed.

    Cheap, and checked at the wheel rather than at the callers: every scroll
    site has to be covered, and the earlier fix that patched only
    enumerate_listings and not bring_into_view is exactly how a half-covered
    rule fails. The one input in this script that damages state the script
    cannot see is worth a screen read before every use.
    """
    # BOTH signals, because they fail differently and the OCR one failed live.
    #
    # trade_window_open() is a text search: it looks for a marker word inside
    # TRADE_WINDOW_SEARCH. The 3D world can supply those glyphs, so it returns
    # True with no window there. On 2026-08-07 close_shop pressed Escape, asked
    # this same function, was told the window was still open, and warned "the
    # Trade window would not close with Escape" -- when in fact it had closed.
    # Two reads later this guard asked the same lying detector, let the scroll
    # through, and forty notches zoomed the camera until the NPC left the
    # screen. Two cycles then failed to find her and the breaker stopped the
    # run.
    #
    # panel_covers_trade_area() cannot be fooled that way: it compares two
    # frames a moment apart, and the world animates while an opaque panel does
    # not. It is the probe open_trade_window already pairs with the OCR check
    # before it will claim the shop is open, so requiring both here is the same
    # standard, applied to the one input that damages state the script can
    # neither see nor undo.
    #
    # Costs ~0.8s a scroll. A wrecked camera costs the rest of the run.
    if not (trade_window_open() and panel_covers_trade_area()):
        if verbose:
            print("  the Trade window is not open - refusing to scroll, the "
                  "wheel would zoom the camera instead of moving the "
                  "listings.")
        record("scroll.refused_window_shut")
        return False

    # And on the REGISTER tab specifically.
    #
    # The two checks above ask "is the Trade window up" and "is an opaque panel
    # covering the area" -- and the PURCHASE tab satisfies both. Nothing told
    # the tabs apart, so a listings-table scroll could fire while the buy tab
    # was showing, which scrolls the OFFERS instead.
    #
    # That is worse than wasted motion. The whole buying design rests on row 1
    # being the cheapest listing; scroll the offers and row 1 is whatever
    # happens to be at the top now, so "always buy row 1" silently starts
    # meaning something else. Seen live on 2026-08-07: a restock left the
    # Purchase tab showing, the next restock's capacity check enumerated the
    # shop, and the wheel went to the offers.
    #
    # Checked here rather than at the callers for the reason the docstring
    # already gives: every scroll site has to be covered, and a half-covered
    # rule is how the last one failed.
    if register_tab_open():
        return True
    if verbose:
        print("  the Trade window is on the Purchase tab - refusing to scroll, "
              "the wheel would move the OFFERS and row 1 would stop meaning "
              "the cheapest one.")
    record("scroll.refused_wrong_tab")
    return False


def scroll_to_end(up: bool, timeout: float = 8.0,
                  verbose: bool = True) -> list[Row] | None:
    """Drive the view to the top (up) or bottom, and return what is showing.

    Relies on the clamp: asking for more than the list can give is a no-op, so
    this needs no knowledge of how long the list is.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not table_scrollable(verbose=verbose):
        return None

    centre = ((TRADE_REGION[0] + TRADE_REGION[2]) // 2,
              (TRADE_REGION[1] + TRADE_REGION[3]) // 2)
    scroll_wheel(*centre, SCROLL_TO_END_NOTCHES if up else -SCROLL_TO_END_NOTCHES)
    park_cursor()
    rows = await_rows(timeout)
    if not rows:
        say("  the table could not be read after scrolling.")
        return None
    return rows


def scroll_one(down: bool, before: list[Row], timeout: float = 8.0,
               verbose: bool = True) -> tuple[list[Row] | None, int | None]:
    """Move the view exactly one row. Returns (rows_after, shift).

    `shift` is measured from content, and is 0 when the view is already at the
    end -- which is how the caller learns it has seen everything. Anything
    other than 0 or 1 means the wheel did something unexpected, and the caller
    must stop rather than reinterpret it.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not table_scrollable(verbose=verbose):
        return None, None

    centre = ((TRADE_REGION[0] + TRADE_REGION[2]) // 2,
              (TRADE_REGION[1] + TRADE_REGION[3]) // 2)
    scroll_wheel(*centre, -1 if down else 1)
    park_cursor()
    after = await_rows(timeout)
    if not after:
        say("  the table could not be read after scrolling.")
        return None, None
    # `expected` is how many rows the wheel was asked to move, consulted
    # only where the rows themselves cannot decide -- see measure_shift.
    shift = measure_shift(before, after, expected=1)
    if shift is None:
        say("  could not tell how far the view moved - refusing to guess "
            "which listing is which.")
        return after, None
    want = 1 if down else -1
    if shift not in (0, want):
        say(f"  one notch moved {shift} rows, expected 0 or {want} - stopping "
            "rather than reinterpreting it.")
        return after, None
    return after, shift


# Rows to move in one verified step. Seven leaves exactly MIN_SCROLL_OVERLAP
# rows in common between the two reads, which is the least that pins the offset
# down. Larger steps leave nothing to match against and the offset becomes a
# guess; one-at-a-time is safe but costs a full table read (~18s of OCR) per
# row, which is minutes per listing once the shop is thirty deep.
SCROLL_STEP = 7
# The fallback step when a 7-row sweep cannot be measured. Smaller steps leave
# more rows overlapping (3 -> 7 of a 10-row screen), which is what survives a
# drifted row or an overlap landing inside a block of empty slots.
SCROLL_STEP_FALLBACK = 3
# Enough chunks to walk a full shop top to bottom, with headroom.
MAX_SCROLL_CHUNKS = 8

# How many identical consecutive views mean bring_into_view has hit the bottom.
#
# Two, not one: a single repeat can be a table that had not finished redrawing,
# and re-reading is cheap next to the 28 minutes the unbounded loop costs.
BRING_INTO_VIEW_STALE = 2


def scroll_chunk(notches: int, before: list[Row], timeout: float = 8.0,
                 verbose: bool = True) -> tuple[list[Row] | None, int | None]:
    """Move the view down by up to `notches` rows, verified against content.

    Like scroll_one but in steps big enough to be affordable. `shift` is 0 when
    the view is already at the bottom, which is how the caller learns it has
    seen everything. A shift outside 0..notches means the wheel did something
    unexpected, and the caller must stop rather than reinterpret it.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not table_scrollable(verbose=verbose):
        return None, None

    centre = ((TRADE_REGION[0] + TRADE_REGION[2]) // 2,
              (TRADE_REGION[1] + TRADE_REGION[3]) // 2)
    scroll_wheel(*centre, -abs(notches))
    park_cursor()
    after = await_rows(timeout)
    if not after:
        say("  the table could not be read after scrolling.")
        return None, None
    # `expected` is how many rows the wheel was asked to move, consulted
    # only where the rows themselves cannot decide -- see measure_shift.
    shift = measure_shift(before, after, expected=abs(notches))
    if shift is None:
        say("  could not tell how far the view moved - refusing to guess "
            "which listing is which.")
        return after, None
    if not 0 <= shift <= abs(notches):
        say(f"  {abs(notches)} notch(es) moved the view {shift} rows - "
            "stopping rather than reinterpreting it.")
        return after, None
    return after, shift


def bring_into_view(ref: RowRef, timeout: float = 8.0,
                    verbose: bool = True) -> list[Row] | None:
    """Scroll until the listing `ref` names is on screen; return that view.

    Walks down from the top in verified chunks. Deliberately does NOT take an
    absolute position: a position measured when the batch started is stale by
    the time later rows are reached, because cancelling one listing renumbers
    everything below it. Identity does not go stale, so identity is what this
    searches by.

    It re-establishes the TOP first because that is a known origin for a
    verified walk, not because the view was reset for it. This used to say
    "relist() closes the Trade window after every row, so the view is back at
    the top each time" -- that stopped being true when close_shop() became
    bounded by shop_session_expired(), and the window now normally stays open
    across the whole batch. The comment outlived the behaviour and was still
    being quoted as a reason on 2026-08-08.

    Returns the view containing the listing. If the whole shop is walked
    without a match, returns the LAST view read rather than None, so the
    caller's own locate_row reports 'missing' -- which means the listing sold,
    a normal outcome. None is reserved for "the table could not be read",
    which is not.
    """
    rows = scroll_to_end(up=True, timeout=timeout, verbose=verbose)
    if not rows:
        return None

    def holds(view: list[Row]) -> bool:
        live = [r for r in view if r.action in ("change", "receive")]
        return locate_row(live, ref)[0] is not None

    # Same step rule as the sweep, and for the same reason: a screen whose tail
    # is empty gives measure_shift an overlap of interchangeable rows, several
    # offsets fit it equally, and it refuses.
    #
    # Fixing only enumerate_listings left this path unfixed, and it failed
    # identically on the very first cycle of the 11:50 run -- row 15, "could
    # not tell how far the view moved". Every scroll site needs the rule, not
    # just the one the failure was first traced to.
    steps = 0
    # Two terminators, because `shift == 0` alone cannot see the bottom of a
    # shop whose last screen is empty slots. measure_shift refuses to return 0
    # for a uniform screen -- it returns `expected` instead, deliberately --
    # so at the clamp the notch does nothing, the view does not change, and
    # the loop reports movement that did not happen. Left as it was, that runs
    # the full budget: a table read per step, roughly 28 minutes, for a row
    # that has already sold.
    #
    # So the view itself is watched. Two consecutive reads that are identical
    # mean the wheel is achieving nothing, whatever measure_shift says about
    # it. _enumerate_at_step has had this guard (its `barren` counter) since a
    # sweep truncated mid-shop; this path never got it.
    unchanged = 0
    previous = [_row_key(r) for r in rows]
    walked = 0
    while steps < MAX_SCROLL_CHUNKS * SCROLL_STEP:
        steps += 1
        if holds(rows):
            # Reported, because this walk used to be entirely SILENT and that
            # made it invisible in exactly the investigation it mattered for:
            # on 2026-08-08 a log grep found "0 scroll operations" while the
            # operator was watching the rows scroll on screen. The enumeration
            # announces its stepping; this path never did, so its cost was
            # attributed to whatever printed next.
            if verbose and walked:
                print(f"  walked {walked} row(s) down in {steps - 1} step(s) "
                      f"to reach {ref.name!r}.")
            return rows
        step = informative_step(rows, SCROLL_STEP)
        after, shift = scroll_chunk(step, rows, timeout=timeout,
                                    verbose=verbose)
        if after is None or shift is None:
            return None
        rows = after
        walked += shift

        current = [_row_key(r) for r in rows]
        if current == previous:
            unchanged += 1
            if unchanged >= BRING_INTO_VIEW_STALE:
                # The bottom, or a wheel that is not reaching the table. Either
                # way the listing is not going to appear, and the caller reads
                # a returned view with no match as "it sold" -- which is the
                # right answer here and a normal outcome.
                if verbose:
                    print(f"  the view stopped changing after {steps} step(s) "
                          "- treating this as the bottom of the shop.")
                break
        else:
            unchanged = 0
        previous = current

        if shift == 0:
            break                       # clamped: the bottom is on screen
    return rows


# The last COMPLETE enumeration, and when it was taken.
#
# One enumeration walks all thirty rows with a table read per step -- minutes,
# and more when the overlap thins and the step drops to 1. A cycle used to pay
# for two of them seconds apart: restock_sold_out sweeps to find sold-out
# Cores, then the relist enumerates again to reach rows past the first screen.
# When the restock buys nothing, the second walk returns exactly what the first
# did.
#
# Dropped by ANY commit. A cancellation shifts every row below it up by one, so
# a stale catalogue is not merely old -- it names row N and means row N+1, in
# the function that cancels listings. The TTL covers what invalidation cannot
# observe: somebody else buying a listing while we are between passes.
_SHOP_CATALOGUE: "dict | None" = None

# Long enough to span one cycle's two passes, short enough that it is never
# doing the work of a second cycle. This exists to remove a duplicate read, not
# to skip reads generally.
SHOP_CATALOGUE_TTL = 90.0


def note_shop_changed(why: str = "") -> None:
    """Forget the enumerated shop: a row has been added, removed, or moved."""
    global _SHOP_CATALOGUE
    if _SHOP_CATALOGUE is not None:
        _SHOP_CATALOGUE = None
        record("shop.catalogue_dropped", why=why or "unspecified")


def cached_shop_catalogue() -> "list[tuple[int, Row]] | None":
    """The last enumeration if it is still trustworthy, else None."""
    if not _SHOP_CATALOGUE:
        return None
    if time.monotonic() - _SHOP_CATALOGUE["at"] > SHOP_CATALOGUE_TTL:
        return None
    return list(_SHOP_CATALOGUE["rows"])


def note_shop_catalogue(rows: "list[tuple[int, Row]]") -> None:
    """Remember a COMPLETE enumeration. A partial read must never reach here.

    enumerate_listings returns None rather than a short list precisely so that
    a half-read shop cannot be mistaken for a whole one; caching is downstream
    of that check for the same reason.
    """
    global _SHOP_CATALOGUE
    _SHOP_CATALOGUE = {"rows": list(rows), "at": time.monotonic()}


def _rows_agree(seen: "list[Row]", kept: "list[Row]") -> bool:
    """Same listings, same order.

    Compared on name and action, never price. A price is re-read from the row
    before anything is done with it; a NAME in the wrong position is the
    failure that cancels a listing nobody named.
    """
    if len(seen) != len(kept):
        return False
    return all(a.name == b.name and a.action == b.action
               for a, b in zip(seen, kept))


def _catalogue_confirmed(remembered: "list[tuple[int, Row]]",
                         timeout: float,
                         say) -> bool:
    """Check a remembered catalogue against BOTH ends of the live table.

    Invalidation only knows about changes this process made. A listing bought
    by another player vanishes with no notification, and every row below it
    moves up one -- turning a remembered "row 7" into a pointer at row 8.

    The top screen alone cannot catch that, and the reason is worth stating
    because the first version of this check got it wrong: a thirty-row
    catalogue and a twelve-row shop have the SAME first ten rows, so comparing
    the overlap passes and the cache is believed. The length is the load-
    bearing part, and only the bottom of the table carries it.

    So both ends are read. Two scrolls and two table reads, against a sweep
    that walks all thirty rows a few at a time -- and it ends at the top, which
    is where enumerate_listings promises to leave the table.
    """
    kept = [row for _, row in remembered]

    bottom = scroll_to_end(up=False, timeout=timeout, verbose=False)
    if bottom is None:
        say("  the table would not scroll to the bottom, so a remembered "
            "shop read cannot be confirmed - re-reading.")
        return False
    if not _rows_agree(bottom, kept[-len(bottom):] if bottom else []):
        say("  the bottom of the shop no longer matches the remembered read - "
            "listings have moved. Re-reading rather than acting on stale "
            "positions.")
        return False

    top = scroll_to_end(up=True, timeout=timeout, verbose=False)
    if top is None:
        say("  the table would not scroll back to the top, so its row numbers "
            "cannot be trusted - re-reading.")
        return False
    if not _rows_agree(top, kept[:len(top)]):
        say("  the top of the shop no longer matches the remembered read - "
            "listings have moved. Re-reading rather than acting on stale "
            "positions.")
        return False
    return True


def enumerate_listings(timeout: float = 8.0,
                       verbose: bool = True,
                       allow_cache: bool = False) -> list[tuple[int, Row]] | None:
    """Every listing in the shop, paired with its absolute position.

    Walks from the top one row at a time. Absolute index 1 is the first
    listing in the shop, independent of what is on screen.

    Returns None rather than a partial list if the view is ever lost: a
    half-enumerated shop is indistinguishable from a complete one to the
    caller, and acting on it would act on the wrong listings.

    allow_cache is OPT-IN, and defaults to off, because the two ends of the
    table cannot actually prove a thirty-row catalogue: two shops with the same
    first ten and last ten rows can differ anywhere in between -- a block of
    empty slots in the middle is exactly that shape, and it is a shape this
    shop really takes. Verifying the middle IS the sweep.

    So the cache is not a general optimisation. It is passed by the ONE caller
    that has independent grounds to believe nothing changed: the relist,
    running seconds after the restock's own sweep in the same cycle. See
    _SHOP_CATALOGUE.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    if allow_cache:
        remembered = cached_shop_catalogue()
        if remembered is not None:
            # A remembered catalogue is never returned on trust. It is checked
            # against both ends of the live table first, and abandoned if it
            # disagrees -- see _catalogue_confirmed for why the top alone is
            # not enough.
            #
            # That check also restores this function's other promise: the
            # finally below leaves the table AT THE TOP, and callers act on
            # positions that assume it. Returning early without it would hand
            # back row numbers measured against a view somebody else had
            # scrolled, in the function whose results are used to CANCEL
            # listings.
            if _catalogue_confirmed(remembered, timeout, say):
                say(f"  reusing the shop read from moments ago "
                    f"({len(remembered)} row(s)); both ends of the table "
                    f"still match it.")
                record("shop.catalogue_reused", rows=len(remembered))
                return remembered
            note_shop_changed("the live table disagreed with the catalogue")

    # A failed chunk has already moved the view, so there is no recovering the
    # offset in place -- the only way back is the top. A step of 7 leaves just
    # 3 rows overlapping, and 3 is the whole evidence base: one row drifting
    # (an OCR misread, a sale landing mid-scroll) or an overlap that falls
    # inside a block of empty slots leaves measure_shift nothing to work with.
    #
    # That is what ended the 07:57 run of 2026-08-06 -- cycles 5 and 6 both
    # died here, and the breaker stopped a run that was otherwise fine. So the
    # cheap step is tried first and a smaller one is the fallback: 3 rows a
    # step leaves 7 overlapping, which survives drift and reaches across the
    # empties. It costs more reads, and it only costs them on a shop that has
    # already refused the cheap path.
    # Whatever happens below, the table is left AT THE TOP.
    #
    # The sweep walks downward and returns from wherever it stopped, which is
    # near the bottom. Every later read -- await_rows, read_rows, the snapshot
    # relist_rows builds its targets from -- reads the ten rows currently
    # DISPLAYED and numbers them 1..10. So after a sweep, "relist rows 1-5"
    # silently meant rows 21-25: it would cancel and re-price listings the
    # operator never named.
    #
    # Nothing else restored it: the only scroll_to_end(up=True) calls in this
    # file are inside this sweep's own internals, and the relist path has none.
    # Restoring it HERE, in a finally, means every exit -- success, failure,
    # or an exception on the way -- leaves the table where callers assume.
    try:
        for step in (SCROLL_STEP, SCROLL_STEP_FALLBACK):
            found = _enumerate_at_step(step, timeout, verbose, say)
            if found is not None:
                # Only a COMPLETE sweep is remembered. _enumerate_at_step
                # returns None rather than a short list when it loses the
                # view, and that distinction is the whole reason a cached
                # answer can be trusted at all.
                note_shop_catalogue(found)
                return found
            if step != SCROLL_STEP_FALLBACK:
                say(f"  retrying the sweep {SCROLL_STEP_FALLBACK} row(s) at a "
                    f"time, so more rows overlap to match on.")
        return None
    finally:
        try:
            scroll_to_end(up=True, timeout=timeout, verbose=False)
        except Exception:  # noqa: BLE001 - never mask the sweep's own outcome
            pass


def informative_step(rows: list[Row], want: int) -> int:
    """The largest step up to `want` that leaves something distinctive behind.

    The overlap is the only evidence measure_shift has, and empty slots are
    interchangeable -- an overlap made entirely of them fits at several offsets
    at once, so the sweep refuses and the cycle is lost.

    Measured on the live shop of 2026-08-06, whose top screen was six listings
    then four empty slots. A 7-row step left an overlap of three empty rows and

        exact fits (shift, overlap): [(7, 3), (6, 4)]

    -- two offsets, both perfectly consistent, no way to choose. Stepping 4
    instead keeps rows 5 and 6 in view, and they name themselves.

    Shrinking the step costs extra reads, so it is only shrunk as far as it has
    to be, and only on a screen whose tail is empty.
    """
    n = len(rows)
    ceiling = min(want, max(1, n - MIN_SCROLL_OVERLAP))
    for step in range(ceiling, 0, -1):
        overlap = rows[step:]
        if len(overlap) < MIN_SCROLL_OVERLAP:
            continue
        if sum(1 for r in overlap if r.name != "(empty)") >= SCROLL_MATCH_MIN_LIVE:
            return step
    # Nothing distinctive anywhere: the view is inside a dead stretch of empty
    # slots. Step small so that if the offset can be pinned down at all, it is
    # pinned down here rather than after skipping over the far edge.
    return 1


def _enumerate_at_step(step: int, timeout: float, verbose: bool,
                       say) -> list[tuple[int, Row]] | None:
    """One full sweep of the shop, stepping up to `step` rows at a time."""
    # Establish the bottom by GOING there, before sweeping down to it.
    #
    # The terminator used to be "measure_shift said the view did not move".
    # Inside a run of empty slots that is indistinguishable from the view
    # having moved, so the sweep stopped in the middle of the shop and reported
    # what it had as the whole thing. On 2026-08-06 that returned 14 slots and
    # 4 live while eight listings sat below the gap -- four of them sold and
    # uncollected, including 27,000,000 Alz of Shape Cartridge -- and the run
    # relisted the top of the shop for forty minutes without ever seeing them.
    #
    # A truncated sweep and a complete one look identical to the caller, so
    # this cannot be left to be noticed: the sweep now ends when it REACHES the
    # screen the shop actually ends on, which is measured, not inferred.
    tail = scroll_to_end(up=False, timeout=timeout, verbose=verbose)
    if not tail:
        return None
    tail_keys = [_row_key(r) for r in tail]

    rows = scroll_to_end(up=True, timeout=timeout, verbose=verbose)
    if not rows:
        return None

    found: list[tuple[int, Row]] = [(i + 1, r) for i, r in enumerate(rows)]
    top = 1                      # absolute index of screen row 1
    if [_row_key(r) for r in rows] == tail_keys:
        # The top read the same as the bottom. Normally that means the shop is
        # one screen deep -- but if every row on the screen is identical, a
        # thirty-deep shop reads exactly the same way, and returning ten rows
        # here would be the truncation this whole function exists to prevent.
        # Content cannot separate the two cases, so refuse rather than pick.
        if len(set(tail_keys)) < 2:
            say("  every row on screen reads alike, so the top and the bottom "
                "of the shop cannot be told apart - refusing to report this "
                "as the whole shop.")
            return None
        say(f"  {len(found)} listing(s) in the shop (all on the first screen)")
        return found
    # Stepped SCROLL_STEP rows at a time, not one.
    #
    # One row at a time costs a full table read -- ~18s of OCR -- per row, so a
    # thirty-deep shop spent ten minutes just working out what was in it, and
    # that was paid again on every cycle. A seven-row step still leaves the
    # three overlapping rows measure_shift needs to pin the offset down, so it
    # is exactly as verifiable and roughly five times cheaper: four reads for
    # thirty rows instead of twenty.
    #
    # Verified against the recorded scroll probes on this shop: from the top,
    # -7 notches moved the view exactly 7 rows, left 3 rows overlapping, and
    # measure_shift returned a single unambiguous fit.
    # Budgeted on the SMALLEST step the sweep might take, not the nominal one.
    # informative_step drops to 1 row inside a gap of empty slots, so a
    # nominally 7-row sweep can need twenty-odd iterations; budgeting for eight
    # made the first pass run out and fail, and the whole shop was then swept a
    # second time at step 3 to get the same answer -- eight wasted table reads,
    # about two and a half minutes, on every cycle.
    steps = 0
    barren = 0        # consecutive steps that revealed nothing new
    while steps < MAX_SCROLL_CHUNKS * SCROLL_STEP:
        steps += 1
        # Chosen per screen, not once: how far the view can move and still be
        # measurable depends on where the empty slots are, and that changes as
        # the sweep descends.
        this_step = informative_step(rows, step)
        if this_step != step:
            say(f"  stepping {this_step} instead of {step} - the last "
                f"{len(rows) - this_step} row(s) of this screen include "
                f"something nameable to match on.")
        after, shift = scroll_chunk(this_step, rows, timeout=timeout,
                                    verbose=verbose)
        if after is None or shift is None:
            return None
        top += shift
        rows = after
        # Every row the step brought into view is new, not just the last one:
        # a seven-row step reveals up to seven rows at once.
        was_named = sum(1 for _, r in found if r.name != "(empty)")
        for offset, row in enumerate(rows):
            index = top + offset
            if index > len(found):
                found.append((index, row))
        grew = sum(1 for _, r in found if r.name != "(empty)") > was_named

        # Two terminators, because neither covers the other.
        #
        # Content: the screen matches the measured bottom. Only trusted when
        # that bottom is DISTINCTIVE -- a shop ending in fifteen empty slots
        # has an all-empty bottom screen, and every all-empty screen on the way
        # down matches it, so this alone stopped five rows early and reported
        # 25 slots of 30.
        #
        # Growth: counted in LISTINGS, not rows. In a uniform tail the wheel
        # is trusted over the pixels, so `top` keeps advancing past the real
        # bottom and phantom empty rows keep being appended -- row growth never
        # stops and the sweep runs to its limit. New listings do stop, and they
        # are the only thing the caller acts on. Trailing empty slots past the
        # last listing are unknowable by content and cost nothing to miss:
        # relist skips them anyway. Three barren steps, so a lone unreadable
        # frame cannot end the sweep.
        barren = 0 if grew else barren + 1
        at_tail = [_row_key(r) for r in rows] == tail_keys
        # Reaching the measured bottom ends the sweep -- but only when that
        # bottom is DISTINCTIVE. A shop ending in empty slots has an all-empty
        # bottom screen, and every all-empty screen on the way down matches it,
        # which stopped the sweep five rows early and reported 25 slots of 30.
        #
        # When the bottom is featureless, "at the tail" is necessary but not
        # sufficient: also require that no new LISTING has appeared for three
        # steps. Crossing a gap mid-shop cannot satisfy both, because there the
        # tail still holds listings and an all-empty screen does not match it.
        if at_tail and (len(set(tail_keys)) >= 2 or barren >= 3):
            break
    else:
        say(f"  still scrolling after {steps} steps - refusing to continue.")
        return None

    say(f"  {len(found)} listing(s) in the shop "
        f"({len(found) - EXPECTED_ROWS} beyond the first screen)")
    return found


def listing_family(rows: list[Row], name: str,
                   price: int | None) -> list[Row]:
    """Live rows sharing a listing's name and price.

    The unit the collect check counts. Module level, not a closure inside
    relist(), so the tests can drive the real thing against real recorded
    tables instead of a reimplementation that can drift from it.
    """
    pool = [r for r in match_rows(rows, name)
            if r.action in ("change", "receive")]
    if price is not None:
        # Filtered STRICTLY, unlike locate_row. locate_row ignores a filter
        # that matches nothing, because there an empty pool means "lost the
        # row" and an unread price should not cause that. Here an empty family
        # is the correct answer: it means every stack at that price is gone.
        #
        # Copying the fallback was a real bug. With two stacks of one item at
        # DIFFERENT prices, collecting the only stack at its price left no row
        # matching, the filter was ignored, and the family widened to the
        # other stack -- so a stack that had never sold was read as the
        # remainder and relisted. That is a registration fee and a wrong price
        # on a listing nobody asked to touch.
        #
        # The failure this trades against is milder: if a price fails to OCR
        # on the later frame its row drops out, the stack reads as fully
        # collected, and a remainder waits for the next cycle.
        pool = [r for r in pool if r.price == price]
    return pool


def family_quantities(pool: list[Row]) -> list:
    """The family's quantities, ordered so two readings compare directly."""
    return sorted((r.qty for r in pool), key=lambda q: (q is None, q))


def collect_delta(before: list, after: list) -> tuple[list, list]:
    """(lost, gained): the multiset difference between two family readings.

    This is what tells a collect apart from a dropped click without needing to
    know WHICH row is which -- the question that has no answer when two stacks
    are identical in name, quantity and price.

        lost=[q], gained=[]    the stack went: fully sold and collected
        lost=[q], gained=[n]   partial sale, n is the remainder
        lost=[],  gained=[]    nothing moved: the click did not take
    """
    unmatched = list(before)
    gained = []
    for value in after:
        if value in unmatched:
            unmatched.remove(value)
        else:
            gained.append(value)
    return unmatched, gained


def locate_row(rows: list[Row], ref: RowRef,
               strict: bool = False) -> tuple[Row | None, str]:
    """The row `ref` points at, plus a note, tolerating duplicate names.

    Returns (row, note). `note` is '' when the row was identified outright,
    'missing' when nothing carries the name at all, and 'ambiguous' when
    several rows do and `strict` forbade guessing between them.

    Missing and ambiguous must never be conflated. "Missing" means the listing
    sold and was collected; "ambiguous" means the script cannot see which of
    two rows it is looking at. Reporting the latter as the former is what made
    an unsold stack look collected and skipped it.
    """
    same = match_rows(rows, ref.name)
    if not same:
        # "No confident match" is NOT "the listing is gone". match_rows also
        # returns nothing when a row is there but its name fell under the
        # fuzzy bar -- and that bar rejects a single substituted character by
        # construction. Callers treat 'missing' as "it sold", so conflating
        # the two turns one flaked glyph into a live stack being skipped and
        # the cycle reporting success.
        from difflib import SequenceMatcher

        needle = _canonical(ref.name)
        near = max((SequenceMatcher(None, needle, _canonical(r.name)).ratio()
                    for r in rows if _canonical(r.name)), default=0.0)
        if near >= UNMATCHED_NAME_SIMILARITY:
            return None, "unmatched"
        return None, "missing"
    if len(same) == 1:
        return same[0], ""

    pool = same
    for attribute, value in (("qty", ref.qty), ("price", ref.price)):
        if value is None:
            continue
        narrowed = [r for r in pool if getattr(r, attribute) == value]
        if len(narrowed) == 1:
            return narrowed[0], ""
        if narrowed:
            pool = narrowed

    if strict:
        return None, "ambiguous"

    # Identical in name, quantity and price. Take them in the order they were
    # first seen rather than refusing outright.
    #
    # Position is the only thing left to go on, and the table may have been
    # reordered since, so this can land on a sibling that was already relisted
    # earlier in the batch. That is survivable: a relist only moves a row out
    # of this pool by changing its price, so a sibling still sitting at the old
    # price is either untouched or was relisted at a price the market says is
    # already correct -- and relisting an already-correct listing changes
    # nothing. What must never happen is silently reporting it as sold.
    chosen = pool[ref.ordinal] if 0 <= ref.ordinal < len(pool) else pool[0]
    priced = f"at {ref.price:,} Alz" if ref.price is not None else "price unread"
    return chosen, (f"{len(pool)} rows are identical ({ref.name!r} x{ref.qty} "
                    f"{priced}); taking row {chosen.index} by position")


# --------------------------------------------------------------------------
# Inventory grid and the Register panel
# --------------------------------------------------------------------------

# Offset from the centre of the "Inventory" title to the centre of slot (1,1),
# and the slot pitch. Measured on the maximised 2560x1440 layout; anchoring on
# the title lets the panel be dragged without breaking.
SLOT_ONE_OFFSET = (-261, 120)
SLOT_PITCH = (73.9, 74.1)
GRID_SIZE = 8

# Register panel, left rail of the Trade window.
REGISTER_PANEL = (10, 120, 275, 1040)
# Starts right of the radio column: PANEL_RADIO_X is 39, and including it let
# the radio glyph OCR as a digit and prepend itself to the price -- a filled
# radio read as "8" turns 1,234,567 into 81,234,567.
PRICE_ROWS = (70, 460, 260, 530)   # the two suggested-price rows
PRICE_FIELD = (40, 545, 204, 573)  # the free-text price box
QTY_FIELD = (40, 634, 226, 667)
NET_SALES_ROWS = (30, 700, 265, 800)
SHOP_SLOT = (144, 290)             # the Register Item box, centre
SHOP_SLOT_BOX = (30, 179, 256, 399)  # the box itself, for occupancy checks
# An empty box is near-uniform dark; any item icon lifts the spread well past this.
SHOP_SLOT_STDEV = 20.0
QTY_INPUT = (90, 651)              # the editable number in "N / MAX"
# The separator in "N / MAX" OCRs badly -- '/' comes back as '[' at ~33%
# confidence -- so this field needs a lower bar than the rest of the UI.
QTY_MIN_CONF = 15.0
# How many times an inventory slot is Ctrl+Clicked to load it into the shop
# slot before the row is given up on.
#
# Raised from 3 to 5 on 2026-08-08. Safe to retry, and the loop says why: a
# Ctrl+Click commits nothing, the panel is re-read after each attempt, and the
# loop breaks the moment it reports loaded -- so a retry after a click that
# actually worked cannot happen. The click is swallowed fairly often when the
# game takes focus back after a dialog closes, and the cost of giving up is a
# failed row in a batch, three of which stop the run.
LOAD_ATTEMPTS = 5


def wants_max_quantity(name: str) -> bool:
    lowered = name.casefold()
    if any(token in lowered for token in NO_MAX_QUANTITY_ITEMS):
        return False
    return MAXIMISE_ALL_QUANTITIES
# Top row is the week's average price, bottom row the lowest currently listed.
# Identify the bottom row by where it sits, not by counting rows: OCR sometimes
# reads only one of the two, and a count check cannot tell which one it got.
PRICE_TOP_Y = 477
PRICE_BOTTOM_Y = 513
PRICE_ROW_Y_TOL = 14

# A corrupted name must still look like the catalogue entry to earn its floor.
FLOOR_NAME_SIMILARITY = 0.75
# ...and a token hit must not be wildly unlike it, which is what rejects
# 'V|pgrade Core(High)' (0.21) while accepting any real VIP read (>= 0.9).
FLOOR_TOKEN_MIN_SIMILARITY = 0.40
# How short a name may be, relative to the catalogue entry, and still earn that
# entry's floor by SIMILARITY alone. Similarity cannot separate a catalogue
# name from a shorter, different item whose name sits inside it: plain
# "Unbinding Stone" scores 0.8235 against "Siena's Unbinding Stone" -- clear of
# the 0.75 bar -- and would take its 71M floor, parking a far cheaper item at a
# price nobody pays. Real OCR damage barely changes length (a substitution not
# at all), so this costs nothing where it matters: measured over 8,000
# corruptions of both catalogue names, it loses zero floors.
#
# SET TO 0.0 ON PURPOSE -- this guard is disabled, and that is the decision.
#
# It was added to stop the plain "Unbinding Stone" (a separate, far cheaper
# item) claiming Siena's 71M floor: it folds to 'unbindingstone', 14 chars,
# scoring 0.8235 against the 20-char catalogue key. The guard worked for that.
#
# What it also did was lose the floor whenever CHARACTERS DROP OUT of a real
# name. Measured over deletion sweeps: at 5 lost characters, 12,482 of 12,501
# Siena floor losses were caused solely by this constant; at 6, 51,977 of
# 52,145. The justification comment claimed "8,000 corruptions, zero losses" --
# true only for substitutions, which barely move the length.
#
# The two cases are genuinely indistinguishable: "Siena's Unbinding Stone" with
# its first word lost folds to exactly the same key as the cheap item. No
# threshold separates them, so this is a choice about which way to fail:
#
#   guard ON  -> a 71,000,000 item can list at a cheap item's price. Money gone.
#   guard OFF -> a cheap item can list at 71,000,000. It does not sell.
#
# The standing instruction on floors is unambiguous, and the second failure
# costs nothing but a listing nobody buys. Raise this only if that changes.
FLOOR_LENGTH_RATIO = 0.0
# How much better one catalogue entry must score than the next before it is
# believed outright. Two entries within this of each other are treated as
# indistinguishable, and the HIGHEST of their floors is used.
#
# Sized against the pair it exists for: "Epic Booster (High)" scores 1.000
# against its own entry and 0.909 against "(Highest)" -- a gap of 0.091, so a
# clean read is decided correctly. A damaged "Epic Booster (Highes)" scores
# 0.971 and 0.938, a gap of 0.034, and is correctly refused as ambiguous.
FLOOR_MATCH_MARGIN = 0.05

# How many single Escape presses to try when backing out to a clean state.
ESCAPE_ATTEMPTS = 3

# relist() outcomes. "Fully sold" is a success, not a failure: there is simply
# nothing left to relist, and a batch should move on rather than stop.
RELISTED = "relisted"
SOLD_OUT = "sold_out"
FAILED = "failed"


def choose_price(
    suggested: int,
    price_floor: int = 0,
    floor_price: int | None = None,
    absolute_floor: int = 0,
) -> tuple[int, str]:
    """Decide the listing price. Returns (price, why) -- why is '' if the
    market's suggestion was used unchanged.

    Take the lowest currently listed price; when there is none, use
    FALLBACK_PRICE. Two things override that:

      * `absolute_floor` -- a per-item minimum from ITEM_PRICE_FLOORS that
        binds ALWAYS (VIP is never listed below 105M, whatever the market
        says). This is a standing requirement, not a tunable.
      * `price_floor` -- an explicit --floor for one command, which refuses
        rather than substituting a number.
    """
    if suggested <= 0:
        # No lowest-current-price to take.
        #
        # FALLBACK_PRICE is right for a FRESH listing: there is no previous
        # price, and parking it high beats guessing low. On a RELIST there IS a
        # previous price, and reaching for the fallback throws away the best
        # information available.
        #
        # Measured on 2026-08-05: a "Craftsman's SIGMetal Headpiece (BL) + 15"
        # the owner had listed at 85,000,000 was relisted at 10,000,000,000,
        # where it cannot sell. The panel read `suggested [0, 0]` because the
        # item is unique enough that nothing comparable was listed -- and an
        # item with no comparable listing is exactly the one whose owner-chosen
        # price is worth keeping.
        #
        # So: keep what it was listed at. The absolute floor still binds over
        # it, and MIN_PLAUSIBLE_PRICE keeps a misread previous price from
        # becoming the new one.
        if floor_price and floor_price >= MIN_PLAUSIBLE_PRICE:
            return max(floor_price, absolute_floor), \
                f"no market price; keeping the previous {floor_price:,}"
        return max(FALLBACK_PRICE, absolute_floor), \
            "no market price and no previous price; using the fallback"

    # An explicit --floor is the only thing that refuses outright.
    if price_floor and suggested < price_floor:
        raise Aborted(
            f"suggested {suggested:,} is below the --floor {price_floor:,}"
        )

    # A relist may not fall more than a set fraction below what the item is
    # listed at now -- see RELATIVE_PRICE_FLOOR for the size and why.
    #
    # Only meaningful when the previous price is itself trustworthy, so it is
    # gated on MIN_PLAUSIBLE_PRICE -- otherwise a misread previous price would
    # set the floor, and one bad read would poison the next.
    #
    # Rounded UP, so the result is never a hair under the intended fraction.
    relative = 0
    if floor_price and floor_price >= MIN_PLAUSIBLE_PRICE:
        relative = -(-floor_price * int(RELATIVE_PRICE_FLOOR * 100) // 100)

    # DO NOT REMOVE. Per-item absolute floors bind unconditionally -- a VIP is
    # never listed below ITEM_PRICE_FLOORS regardless of what the market says.
    # This outranks "always take the lowest current price": that rule decides
    # WHICH market figure to use, this one decides how low the listing may go.
    #
    # Whichever of the two bounds is higher wins, and the reason names the one
    # that actually bound -- an absolute floor and the relative ratchet fail
    # for different causes and the log has to tell them apart.
    guard = max(absolute_floor, relative)
    if guard and suggested < guard:
        if guard == absolute_floor and absolute_floor >= relative:
            return absolute_floor, (
                f"market {suggested:,} is below the {absolute_floor:,} floor "
                "for this item; listing at the floor"
            )
        return relative, (
            f"market {suggested:,} is more than "
            f"{100 - int(RELATIVE_PRICE_FLOOR * 100)}% below the listed "
            f"{floor_price:,}; listing at {relative:,}"
        )

    return suggested, ""


def set_behind(core_name: str) -> str:
    """The Set that converts into `core_name`, or "" if it is not a Core."""
    slot = favourite_for(core_name)
    if slot is None:
        return ""
    partner = favourite_set_slot(slot)
    return FAVOURITE_SLOTS.get(partner, "") if partner else ""


def purchase_cost_basis(name: str) -> int:
    """What was paid per item for the Sets behind `name`. 0 if none were.

    A relist may never price a Core below what its Sets cost. The market can
    move against a position -- Force Core (Ultimate) was bought at 428,571 a
    Set and the loose Core fell to 386,831 within the hour -- and "take the
    lowest current price" would then sell the whole holding at a loss, one
    relist at a time, with every other guard satisfied.

    Weighted by quantity rather than taking the last purchase or the largest:
    it is the average cost of the goods actually held, which is the figure a
    loss is measured against. Rounded UP, so the floor is never a hair under
    what was paid.

    Read from the ledger rather than memory so it spans runs -- stock bought
    yesterday is sold today, and a per-process tally would forget the cost the
    moment the run that paid it ended.
    """
    wanted = set_behind(name)
    if not wanted:
        return 0
    conn = sales_db()
    if conn is None:
        return 0
    try:
        rows = conn.execute(
            "SELECT item, price, qty FROM purchases "
            "WHERE price > 0 AND qty > 0").fetchall()
    except Exception:  # noqa: BLE001 - bookkeeping must never block a listing
        return 0
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    key = _floor_key(item_name(wanted))
    spent = held = 0
    for item, price, qty in rows:
        # The ledger stores the listing's name, which carries its pack marker
        # ("X 999"). Compare on the folded name with that stripped, the same
        # way every other name comparison in this file does.
        if _floor_key(item_name(_PACK_ANYWHERE.sub(" ", item))) == key:
            spent += int(price)
            held += int(qty)
    if not held:
        return 0
    return -(-spent // held)


def listing_floor(name: str) -> tuple[int, str]:
    """The lowest price `name` may be listed at, and which rule set it.

    Two independent minimums, and the higher wins:

      ITEM_PRICE_FLOORS    what the operator says the item is worth
      purchase_cost_basis  what was actually PAID for it

    The second exists because the market can move against a position after it
    is bought. Force Core (Ultimate) was bought at 428,571 a Set and the loose
    Core fell to 386,831 within the hour; without this, "take the lowest
    current price" would sell the whole holding below cost, one relist at a
    time, with every other guard satisfied. The 5% ratchet limits how FAST a
    price falls, not how far.

    Returned together with its reason so the log can say which rule bound --
    an operator floor and a cost floor fail for different causes.

    The two rules are NOT symmetric, and the asymmetry is the operator's,
    stated on 2026-08-08:

      "the absolute price floor for my unique items stay the same, and they
       always applied no matter what. no flags for those. The price floor of
       relisting/resupplying only apply for cores"

    So:

      ITEM_PRICE_FLOORS    always. Not behind COST_FLOOR_ON_RELIST, not behind
                           anything, and not a tunable. A VIP is never listed
                           below its floor whatever else is switched off.
      purchase_cost_basis  Cores only, and only when COST_FLOOR_ON_RELIST is
                           on (it ships OFF).

    The "Cores only" half is enforced by purchase_cost_basis via set_behind:
    a name resolves to a favourite slot, and only a Core's slot has a paired
    Set slot. That is a real constraint but an INDIRECT one -- it holds because
    of how FAVOURITE_SLOTS happens to be arranged, so putting a non-Core in a
    slot with a partner would quietly extend the cost floor to it.
    buying_gaps_test asserts both halves against the whole ITEM_PRICE_FLOORS
    catalogue rather than a sample, so adding an entry cannot add an untested
    one.
    """
    catalogue = item_price_floor(name)
    if not COST_FLOOR_ON_RELIST:
        return catalogue, "the floor set for this item"
    cost = purchase_cost_basis(name)
    if cost > catalogue:
        return cost, "what its Sets were bought for"
    return catalogue, "the floor set for this item"


def item_price_floor(name: str) -> int:
    """Absolute floor for a listing name, or 0 if none applies.

    Two independent routes, because neither alone is good enough:

      * the token ("vip") as a substring -- fast, but a 3-character target is
        tiny, so one bad glyph in V, I or P loses it entirely; and
      * similarity of the WHOLE name against the catalogue entry -- which
        survives any single corruption, because the other 22 characters carry
        the match.

    A token hit additionally has to clear a low similarity bar. That is what
    stops a folded 'V|pgrade Core(High)' -- which really does contain "vip" --
    from claiming a 110,000,000 floor and parking 158 cores nobody will buy.

    Compared against both the full folded name and its leading window, so a
    descriptive trailer that item_name() did not strip cannot dilute the score.
    """
    from difflib import SequenceMatcher

    key = _floor_key(item_name(name))
    if not key:
        return 0

    candidates: list[tuple[float, int]] = []      # (similarity, floor)
    for token, catalogue, floor in ITEM_PRICE_FLOORS:
        reference = _floor_key(catalogue)
        if not reference:
            continue
        ratio = max(
            SequenceMatcher(None, reference, key).ratio(),
            SequenceMatcher(None, reference, key[:len(reference)]).ratio(),
        )
        token_hit = _floor_key(token) in key
        # The length guard applies to the similarity route ONLY. The token
        # route is deliberately exempt: a name clipped by a misjudged column
        # keeps its leading token ("siena", "vip"), and rescuing exactly that
        # read is what the token route is for. Guarding both would trade a
        # cosmetic over-match for the one failure that must never happen.
        long_enough = len(key) >= len(reference) * FLOOR_LENGTH_RATIO
        if (ratio >= FLOOR_NAME_SIMILARITY and long_enough) or (
                token_hit and ratio >= FLOOR_TOKEN_MIN_SIMILARITY):
            candidates.append((ratio, floor, reference))
    if not candidates:
        return 0

    # An exact read decides outright. "epicboosterhigh" IS the (High) entry, so
    # the fact that it is also a prefix of the (Highest) entry is irrelevant.
    exact = [floor for _, floor, reference in candidates if key == reference]
    if exact:
        return max(exact)

    # A read that is a PREFIX of two entries is a truncation that cannot tell
    # them apart, and similarity actively misleads here: clipped to
    # "epicbooster", it scores 0.846 against (High) and 0.759 against
    # (Highest), so the margin rule below would confidently pick the CHEAPER
    # floor and list a 44,000,000 item at 24,000,000. The name column does clip,
    # so this is the read that must not be trusted.
    # More generally: when two catalogue entries are PREFIX-RELATED -- one name
    # begins with the other, as "Epic Booster (High)" does with
    # "(Highest)" -- an inexact read cannot choose between them, and the
    # similarity scores actively favour the wrong one. The leading-window
    # comparison above scores key[:len(reference)] against each entry, so a read
    # clipped to "Epic Booster (Highe" matches the SHORTER name perfectly
    # (1.000) and the longer one at 0.941, and would take the cheaper floor for
    # what is probably the dearer item.
    #
    # Any inexact read touching a prefix-related pair therefore takes the
    # higher floor.
    prefix_related = [
        floor for _, floor, reference in candidates
        if any(other != reference
               and (other.startswith(reference) or reference.startswith(other))
               for _, _, other in candidates)
    ]
    if len(prefix_related) >= 2:
        return max(prefix_related)

    # Several entries can match one name, and taking max() of their floors --
    # which this did -- is right only when they cannot be told apart.
    #
    # "Epic Booster (High)" and "Epic Booster (Highest)" differ by four
    # characters and score 0.909 against each other, so both always match. Under
    # max() the cheaper item inherited the dearer floor: the 24,000,000 item
    # would have gone up at 44,000,000 and never sold.
    #
    # So the clearly-better match wins. "Clearly" is the whole safety argument:
    # an exact read scores 1.000 against its own entry and 0.909 against its
    # twin, a gap of 0.091, while a DAMAGED read closes that gap -- "Epic
    # Booster (Highes)" scores 0.971 and 0.938, a gap of 0.034. Below the margin
    # the two are not distinguishable, and ambiguity falls back to the highest
    # floor. Too high only fails to sell; too low sells a 44M item for 24M.
    candidates.sort(key=lambda c: (-c[0], -c[1]))
    best_ratio, best_floor, _ = candidates[0]
    rivals = [floor for ratio, floor, _ in candidates[1:]
              if best_ratio - ratio < FLOOR_MATCH_MARGIN]
    if rivals:
        return max([best_floor] + rivals)
    return best_floor


def strictest_price_floor() -> int:
    """The highest floor any item carries.

    Used when the item cannot be named. Refusing to guess would block ordinary
    manual use, and guessing 0 is what let `--register` list a VIP unfloored,
    so an unidentified item takes the strictest floor on the books instead --
    too high never sells anything cheap, which is the direction to fail in.
    """
    # `*_, floor`, not `_, floor`: ITEM_PRICE_FLOORS carries (token, catalogue,
    # floor). Unpacking it as a pair raised ValueError on every explicitly
    # priced registration -- after the item had already been Ctrl+Clicked into
    # the shop slot, and reported by run_sequence as "non-numeric argument".
    return max((floor for *_, floor in ITEM_PRICE_FLOORS), default=0)
PANEL_RADIO_X = 39                 # x of the price radio buttons


INVENTORY_TITLE_REGION = (1400, 100, 2560, 300)
# Offset from the Alz box's (right, top) to the Inventory title centre. The Alz
# digits are right-aligned, so the right edge holds still as the number changes.
ALZ_TO_TITLE = (-241, -718)


def inventory_origin(
    source: Image.Image | None = None,
    retries: int = INVENTORY_ORIGIN_RETRIES,
) -> tuple[int, int] | None:
    """Anchor for the inventory grid, expressed as the title's centre.

    Prefers the Alz box: it is found by colour rather than OCR, and measured
    identical across frames, whereas the ornate "Inventory" title sits over
    moving game art and OCRs intermittently (sometimes as 'I' + 'nventory',
    sometimes not at all). The title is kept only as a fallback.

    Costs a scan, so callers needing many slots should resolve it once.
    """
    for _ in range(retries):
        image = source if source is not None else grab()

        box = find_alz(image)
        if box:
            return (box[2] + ALZ_TO_TITLE[0], box[1] + ALZ_TO_TITLE[1])

        centre = find_phrase(image, "Inventory", INVENTORY_TITLE_REGION)
        if centre is not None:
            return centre

        if source is not None:
            break  # a fixed frame will not change between retries
        time.sleep(0.4)
    return None


def slot_centre_at(origin: tuple[int, int], row: int, col: int) -> tuple[int, int]:
    """Screen centre of slot (row, col) given the panel anchor. Pure arithmetic."""
    if not (1 <= row <= GRID_SIZE and 1 <= col <= GRID_SIZE):
        raise ValueError(f"slot ({row},{col}) is outside the {GRID_SIZE}x{GRID_SIZE} grid")
    tx, ty = origin
    return (
        round(tx + SLOT_ONE_OFFSET[0] + SLOT_PITCH[0] * (col - 1)),
        round(ty + SLOT_ONE_OFFSET[1] + SLOT_PITCH[1] * (row - 1)),
    )


def slot_centre(row: int, col: int, source: Image.Image | None = None) -> tuple[int, int]:
    """Screen centre of inventory slot (row, col), both 1-based."""
    origin = inventory_origin(source)
    if origin is None:
        raise Aborted("could not find the Inventory panel - is it open?")
    return slot_centre_at(origin, row, col)


# Inventory tabs I..VIII, measured from the panel anchor.
TAB_ONE_OFFSET = (-281, 52)
TAB_PITCH = 69.2
TAB_COUNT = 8
# How far above the median a tab must sit to count as the selected one.
TAB_ACTIVE_MARGIN = 6.0
# An empty slot measures near 0; an item icon lifts the spread well past this.
SLOT_OCCUPIED_STDEV = 8.0

SLOT_INSET = 26          # half-width of the sampled area inside a slot
SLOT_CHANGE_MIN = 6.0    # mean per-pixel delta that counts as "something moved"
SLOT_CHANGE_MARGIN = 2.0 # the winner must beat the runner-up by this factor


def inventory_cells(
    image: Image.Image, origin: tuple[int, int]
) -> dict[tuple[int, int], Image.Image]:
    """Grayscale interior of every inventory slot, keyed by (row, col).

    Takes the anchor rather than finding it, so slicing 64 slots costs no OCR.
    """
    cells = {}
    for r in range(1, GRID_SIZE + 1):
        for c in range(1, GRID_SIZE + 1):
            cx, cy = slot_centre_at(origin, r, c)
            # Clamp to the image: PIL pads an out-of-bounds crop with black
            # instead of erroring, and the padding inflates the cell's spread
            # so an empty slot reads as occupied for ever.
            box = (max(0, cx - SLOT_INSET), max(0, cy - SLOT_INSET),
                   min(image.width, cx + SLOT_INSET),
                   min(image.height, cy + SLOT_INSET))
            cells[(r, c)] = image.crop(box).convert("L")
    return cells


def occupied_slots(
    image: Image.Image, origin: tuple[int, int]
) -> list[tuple[int, int]]:
    """Inventory slots holding an item, judged by pixel spread not by OCR."""
    found = []
    for key, cell in inventory_cells(image, origin).items():
        data = list(getattr(cell, "get_flattened_data", cell.getdata)())
        mean = sum(data) / len(data)
        stdev = (sum((p - mean) ** 2 for p in data) / len(data)) ** 0.5
        if stdev >= SLOT_OCCUPIED_STDEV:
            found.append(key)
    return sorted(found)


def require_empty_work_tab(verbose: bool = True) -> bool:
    """Tab WORK_TAB must be empty before a run.

    Cancelling a large stack scatters items across tabs; if the tab already
    holds something, the before/after diff cannot tell which slots the cancel
    filled, and the script could pick up an unrelated item.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    origin = inventory_origin()
    if origin is None:
        record("worktab.no_panel")
        say("The Inventory panel is not visible - open it and rerun.")
        return False

    if not select_inventory_tab(WORK_TAB, origin):
        record("worktab.tab_switch_failed", want=WORK_TAB)
        say(f"Could not switch to inventory tab {WORK_TAB}.")
        return False

    park_cursor()
    occupied = occupied_slots(grab(), origin)
    if occupied:
        where = ", ".join(f"{r},{c}" for r, c in occupied[:12])
        more = f" (+{len(occupied) - 12} more)" if len(occupied) > 12 else ""
        # THE blind step. This refusal is the most common cycle-killer after a
        # strand -- it is what every cycle hits once an item is left behind --
        # and it recorded nothing, so three consecutive deaths here left an
        # empty index and a five-hour outage with no attributable cause.
        record("worktab.not_empty", tab=WORK_TAB, occupied=len(occupied),
               slots=", ".join(f"{r},{c}" for r, c in occupied[:12]))
        say(f"Inventory tab {WORK_TAB} is not empty - {len(occupied)} slot(s) "
            f"in use: {where}{more}.\n"
            "Clear it before running: cancelled items land here, and leftover "
            "items make it impossible to tell which ones came back.")
        return False

    say(f"Inventory tab {WORK_TAB} is empty.")
    return True


def money(value: int | None, blank: str = "-") -> str:
    """A price for display: grouped digits, or `blank` when there is none.

    Exists because the inline form is a trap. Written as

        f"{row.price if row.price is None else format(row.price, ',') :>14}"

    the conditional yields None for an unpriced row and the width spec is then
    applied to it -- TypeError, and only for rows that HAVE no price. --listings
    scrolled the entire shop, enumerated all 30 rows, and then crashed on the
    first empty slot while printing them.
    """
    return blank if value is None else format(value, ",")


# --------------------------------------------------------------------------
# Sales tally
# --------------------------------------------------------------------------
#
# What sold, and for how much, measured the only way the game will tell us.
#
# The table cannot answer it. A row's QTY column shows what is STILL on sale,
# and collecting the proceeds does not change it -- the same fact behind the
# collect-by-action fix. So "how many sold" is not readable from the listing
# either before or after.
#
# The Alz balance is. Reading it either side of a Receive gives the credit for
# that sale exactly, and dividing by the listing's unit price recovers the
# quantity. The division is its own check: a remainder means the two readings
# do not describe one clean sale at that price, and the quantity is then left
# unclaimed rather than guessed.
SALES: list[dict] = []

# Money OUT, the other half of the ledger. Without it every "what did I make"
# figure was a gross rather than a profit -- the tally only ever counted
# collections.
PURCHASES: list[dict] = []

# Every collection is written to SQLite the moment it happens, not summed up
# and printed at the end.
#
# The end-of-run report is the wrong and only place this used to live. Of the
# runs on 2026-08-06, one was stopped by Ctrl+C, one by the failure breaker and
# one by a crash inside the tidy-up -- and a tally held in memory is worth
# nothing if the process does not reach its own last line. It also cannot
# answer "what did I make today", because each run only ever knew about itself.
#
# SQLite because it is in the standard library, survives a killed process, and
# can be read while a run is still going.
# Overridable by environment, so a test run cannot write into the real ledger.
#
# It could, and did. The failure-path suites replay the collect path for real,
# which calls note_sale(), which writes a row -- and only t29 redirected the
# database, for its own cases. Measured on 2026-08-07: of 1,168 rows in the
# live ledger, 1,163 were the regression suite, arriving in a recognisable
# burst of 2+6+1+80+18+18 rows within 45 seconds, eight times over. Five rows
# were real. Every "what did I make today" answer since has been counting
# recorded corpus frames as income.
#
# An environment variable rather than a module global because run_all.py starts
# each suite as a SUBPROCESS: a global set in the parent would not survive, and
# that is exactly the kind of gap that let this run for a day unnoticed.
SALES_DB = Path(os.environ.get("CABAL_SALES_DB") or (SCRIPT_DIR / "sales.db"))
_sales_db_ready = False


def sales_db() -> "sqlite3.Connection | None":
    """The sales database, created on first use. None if it cannot be opened.

    Never raises: bookkeeping must not be able to cost a listing.
    """
    global _sales_db_ready
    try:
        conn = sqlite3.connect(SALES_DB, timeout=5.0)
        if not _sales_db_ready:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sales (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    at       TEXT    NOT NULL,
                    run      TEXT,
                    item     TEXT    NOT NULL,
                    price    INTEGER,
                    proceeds INTEGER,
                    qty      INTEGER,
                    note     TEXT
                );
                CREATE INDEX IF NOT EXISTS sales_at  ON sales (at);
                CREATE INDEX IF NOT EXISTS sales_run ON sales (run);

                -- Purchases live in their own table rather than as a signed
                -- row in `sales`. A separate table needs no migration of the
                -- rows already there, and the two are genuinely different
                -- shapes: a sale has proceeds that may be unmeasurable, a
                -- purchase has a spend verified against the balance.
                CREATE TABLE IF NOT EXISTS purchases (
                    id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    at    TEXT    NOT NULL,
                    run   TEXT,
                    item  TEXT    NOT NULL,
                    price INTEGER,
                    spend INTEGER,
                    qty   INTEGER,
                    note  TEXT
                );
                CREATE INDEX IF NOT EXISTS purchases_at  ON purchases (at);
                CREATE INDEX IF NOT EXISTS purchases_run ON purchases (run);

                -- What this script put ON the market, and how much of it.
                --
                -- Needed because a sale is measured by the balance moving, and
                -- the only bound on how large that move could legitimately be
                -- is how much the listing held. The TABLE cannot supply it:
                -- the QTY column shows what is STILL on sale, so after a
                -- partial sale it is the remainder, not the original. Using it
                -- as the ceiling rejected every partial sale on the books.
                CREATE TABLE IF NOT EXISTS registrations (
                    id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    at    TEXT    NOT NULL,
                    run   TEXT,
                    item  TEXT    NOT NULL,
                    price INTEGER,
                    qty   INTEGER
                );
                CREATE INDEX IF NOT EXISTS reg_item ON registrations (item);
                """
            )
            conn.commit()
            _sales_db_ready = True
        return conn
    except Exception:  # noqa: BLE001 - a tally must never cost a listing
        return None


def record_sale_row(item: str, price: int | None, proceeds: int | None,
                    qty: int | None, note: str = "") -> bool:
    """Append one collection to the database. True if it was written.

    Committed immediately and the connection closed, so a process killed a
    second later still leaves the row behind. That is the entire point.
    """
    conn = sales_db()
    if conn is None:
        return False
    try:
        with conn:
            conn.execute(
                "INSERT INTO sales (at, run, item, price, proceeds, qty, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"),
                 _RUN_STARTED_AT.isoformat(timespec="seconds"),
                 item, price, proceeds, qty, note or None),
            )
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


# How many ledger rows a report will read, and the window it defaults to.
#
# A cap rather than no cap because the table grows without bound; named because
# a silent truncation looks exactly like a quiet week. sales_report says how
# many rows it summarised, so hitting the cap is visible rather than implied.
SALES_REPORT_HOURS = 24.0
SALES_REPORT_LIMIT = 500
# The --sales command asks for more, since a human ran it deliberately.
SALES_QUERY_LIMIT = 2000


def sales_since(hours: "float | None" = SALES_REPORT_HOURS,
                limit: int = SALES_REPORT_LIMIT) -> list[tuple]:
    """Rows from the database, newest first. Empty if it cannot be read."""
    conn = sales_db()
    if conn is None:
        return []
    try:
        if hours is None:
            rows = conn.execute(
                "SELECT at, item, price, proceeds, qty, note FROM sales"
                " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        else:
            cutoff = datetime.fromtimestamp(
                time.time() - hours * 3600).isoformat(timespec="seconds")
            rows = conn.execute(
                "SELECT at, item, price, proceeds, qty, note FROM sales"
                " WHERE at >= ? ORDER BY id DESC LIMIT ?",
                (cutoff, limit)).fetchall()
        return list(rows)
    except Exception:  # noqa: BLE001
        return []
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def record_purchase_row(item: str, price: int | None, spend: int | None,
                        qty: int | None, note: str = "") -> bool:
    """Append one purchase to the database. True if it was written."""
    conn = sales_db()
    if conn is None:
        return False
    try:
        with conn:
            conn.execute(
                "INSERT INTO purchases (at, run, item, price, spend, qty, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"),
                 _RUN_STARTED_AT.isoformat(timespec="seconds"),
                 item, price, spend, qty, note or None))
        return True
    except Exception:  # noqa: BLE001 - bookkeeping must not cost a purchase
        return False
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def note_purchase(item: str, price: int | None, spend: int | None,
                  qty: int | None, note: str = "") -> None:
    """Record one completed purchase -- database first, then memory.

    Same reasoning as note_sale: written at the moment it happens, so a run
    killed a second later still leaves the row behind. Without this half, the
    ledger only ever counted money coming IN, and every "what did I make"
    figure was a gross, not a profit.

    `spend` is the figure the BALANCE moved by, not the listed price. They are
    equal on the happy path -- buy_offer refuses to claim a purchase unless
    they match -- but recording the measured one means the ledger can never
    drift from the account.
    """
    PURCHASES.append({"item": item, "price": price, "spend": spend,
                      "qty": qty})
    try:
        stored = record_purchase_row(item, price, spend, qty, note)
    except Exception:  # noqa: BLE001
        stored = False
    try:
        record("buy.recorded", item=item, price=price, spend=spend, qty=qty,
               stored=stored)
    except Exception:  # noqa: BLE001
        pass


def note_sale(item: str, price: int | None, proceeds: int | None,
              note: str = "") -> None:
    """Record one collected sale -- to the database first, then in memory.

    Written to SQLite HERE, at the moment of the collect, rather than summed up
    and printed at the end. Every run on 2026-08-06 ended in a way that could
    have lost an in-memory tally: Ctrl+C, the failure breaker, and a crash
    inside the tidy-up itself. A row already committed survives all three.

    Never raises and never blocks a relist: a tally is bookkeeping, and losing
    a line of it must not cost a listing.
    """
    qty = None
    if proceeds and price and proceeds > 0 and price > 0:
        if proceeds % price == 0:
            qty = proceeds // price
    SALES.append({"item": item, "price": price, "proceeds": proceeds,
                  "qty": qty})
    try:
        stored = record_sale_row(item, price, proceeds, qty, note)
    except Exception:  # noqa: BLE001 - bookkeeping must not break a relist
        stored = False
    try:
        record("sale.collected", item=item, price=price, proceeds=proceeds,
               qty=qty, stored=stored)
    except Exception:  # noqa: BLE001
        pass


# Nothing here any more: see sale_rejection. An unregistered listing falls
# back to the STRICT rule rather than to a generous constant, because a
# generous one accepted the Epic Booster reading (876,764,416 = 16 x 54,797,776
# from a stack of 8), which is half the reason the ceiling exists.


def note_registration(item: str, price: int | None, qty: int | None) -> None:
    """Record that `qty` of `item` were put on the market at `price`."""
    if not item or not price or not qty:
        return
    conn = sales_db()
    if conn is None:
        return
    try:
        with conn:
            conn.execute(
                "INSERT INTO registrations (at, run, item, price, qty)"
                " VALUES (?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"),
                 _RUN_STARTED_AT.isoformat(timespec="seconds"),
                 item, int(price), int(qty)))
    except Exception:  # noqa: BLE001 - bookkeeping must not cost a listing
        pass
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def registered_qty(item: str, price: int | None) -> "int | None":
    """The largest quantity of `item` this script ever listed at `price`.

    Largest rather than latest: the same stack can be relisted several times as
    it sells down, and the ceiling has to cover the biggest it ever was.
    Matched on the folded key so the game's spacing around the bracket, and the
    pack marker on a table name, cannot cause a miss.
    """
    if not item or not price:
        return None
    wanted = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", item)))
    if not wanted:
        return None
    conn = sales_db()
    if conn is None:
        return None
    try:
        best = None
        for name, qty in conn.execute(
                "SELECT item, qty FROM registrations WHERE price = ? "
                "AND qty > 0", (int(price),)):
            if _floor_key(item_name(_PACK_ANYWHERE.sub(" ", name))) != wanted:
                continue
            if best is None or int(qty) > best:
                best = int(qty)
        return best
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def sale_rejection(proceeds: "int | None", price: "int | None",
                   still_listed: "int | None",
                   listed_units: "int | None" = None) -> str:
    """Why this collection cannot be a real sale of this listing. "" if it can.

    Pure arithmetic, no screen and no database, so the rule can be tested
    directly -- the reason the original version of this check went three months
    without anyone noticing it discarded every partial sale.

    Two independent tests, because the two bad readings on record fail
    differently:

      * A sale is a whole number of units at the listed price. The VIP that
        booked 1,662,294,744 against a 106,000,000 item divides to 15.68 --
        no quantity of it was ever sold.
      * A sale cannot be more units than the listing HELD. `still_listed` is
        not that number: the QTY column shows what REMAINS, so on a partial
        sale it is the leftovers, and using it as the ceiling rejected every
        partial sale on the books. `listed_units` -- what this script recorded
        registering -- is the real bound.

        Where there is no registration on file the bound falls back to
        `still_listed`, i.e. the old strict rule. That is deliberate and it is
        not symmetric with the case above: a generous fallback accepted the
        Epic Booster reading of 876,764,416 (exactly 16 x 54,797,776, from a
        stack that held 8), which is one of the two incidents this function
        exists for. So a listing the pipeline registered gets its partial
        sales measured; one made by hand, or before the registrations table,
        is treated as strictly as it always was.

    The figure is REJECTED, not clamped: it is evidence the reading was wrong,
    not evidence of a smaller sale. The sale still counts, unmeasured.
    """
    if not proceeds or proceeds <= 0:
        return ""
    if not price or price <= 0:
        return ""
    if proceeds % price:
        return (f"{proceeds:,} is not a whole number of units at "
                f"{price:,} each ({proceeds / price:.2f})")
    units = proceeds // price
    # The LARGER of the two, not one or the other. Both are lower bounds on
    # what the listing held, and the ceiling has to clear both:
    #
    #   * still_listed is what the row showed when it was read. On a PARTIAL
    #     sale that is the remainder and says little; on a FULL sale it is the
    #     whole stack, and everything on sale sold. Live on 2026-08-07 a row
    #     reading x250 sold exactly 250 x 209,999 -- so treating this as an
    #     upper bound rejected a real, complete sale.
    #
    #   * listed_units is what this script recorded registering. It is keyed on
    #     (name, price), which is NOT unique on this shop -- five identical
    #     Force Core (Ultimate) stacks at 445,000 was the ordinary state that
    #     day -- so a 200-stack registered earlier became the ceiling for the
    #     250-stack that actually sold, and 52,499,750 of real income was
    #     thrown away. Taking the max stops one stack capping another.
    #
    # Neither alone is right; the max of them is wrong only in the direction of
    # accepting a large sale, and the whole-units test above is what catches
    # the readings this function exists for.
    bound = max(max(0, still_listed or 0), listed_units or 0)
    if units > bound:
        return (f"{proceeds:,} is {units:,} units at {price:,}, more than the "
                f"{bound:,} this listing could have held")
    return ""


def all_time_totals() -> "tuple[int, int, int, int] | None":
    """(sales, proceeds, purchases, spend) over the whole database, or None.

    Read from SQLite rather than memory so it spans every run, which is the
    only window over which profit means much: stock bought on Monday is sold on
    Tuesday, so a single run's proceeds and spend are rarely about the same
    items.
    """
    conn = sales_db()
    if conn is None:
        return None
    try:
        sales_n, proceeds = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(proceeds), 0) FROM sales").fetchone()
        # COUNT only real purchases, SUM every outflow.
        #
        # Registration fees live in this table too -- they are money out and
        # belong in spend -- but they are written with qty = 0 so they stay out
        # of purchase_cost_basis, which selects on `qty > 0`. Counting them as
        # "purchases" would report one per listing per cycle and drown the
        # figure the operator actually wants.
        buys_n, = conn.execute(
            "SELECT COUNT(*) FROM purchases WHERE qty > 0").fetchone()
        spend, = conn.execute(
            "SELECT COALESCE(SUM(spend), 0) FROM purchases").fetchone()
        return int(sales_n), int(proceeds), int(buys_n), int(spend)
    except Exception:  # noqa: BLE001 - bookkeeping must never raise
        return None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def cost_of_goods_sold() -> "tuple[int, int, int]":
    """(cost of units sold, units priced, units with no recorded cost).

    Profit is takings less what the SOLD units cost -- not less everything ever
    spent. Buying 1,212 Sets that are still sitting on the shop is not a loss;
    it is stock. Conflating the two made the report worse the better the
    restocking worked.

    Units whose cost is unknown are counted separately rather than valued at
    zero and folded in silently. Most of what has sold on this account was
    bought before the purchases ledger existed, so treating those as free would
    overstate profit by exactly the amount nobody can account for -- and a
    figure that flatters itself where the data is missing is the same mistake
    in the other direction.
    """
    conn = sales_db()
    if conn is None:
        return 0, 0, 0
    try:
        cost = priced = unpriced = 0
        for item, qty in conn.execute(
                "SELECT item, qty FROM sales WHERE qty > 0 AND proceeds > 0"):
            unit = purchase_cost_basis(item)
            if unit > 0:
                cost += unit * int(qty)
                priced += int(qty)
            else:
                unpriced += int(qty)
        return cost, priced, unpriced
    except Exception:  # noqa: BLE001 - bookkeeping must never raise
        return 0, 0, 0
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def profit_report() -> str:
    """This run's money in, money out, and the difference. Empty if neither.

    Two figures are given and they answer different questions:

      THIS RUN   what this process collected and spent. Useful for "did that
                 session go well", but the two halves need not concern the same
                 items -- a run can sell stock bought yesterday and buy stock
                 that sells tomorrow, so the difference can be wildly negative
                 on a run that did nothing wrong.

      ALL TIME   the same over every run in the database. This is the figure
                 that actually means profit, because over enough runs the
                 buying and the selling are about the same goods.

    Said out loud rather than folded together, because reporting one run's
    difference AS profit would be a number that looks authoritative and is not.
    """
    gross = sum(s["proceeds"] or 0 for s in SALES)
    spend = sum(p["spend"] or 0 for p in PURCHASES)
    totals = all_time_totals()

    # Only silent when there is genuinely nothing anywhere -- a fresh ledger.
    #
    # This used to return here whenever THIS RUN was quiet, which suppressed
    # the ALL TIME block as well, and that block comes from the database and is
    # true regardless of what the current run did. So a 35-minute run that
    # relisted fourteen rows, bought nothing because the savings were under
    # threshold, and collected nothing, ended with no money figures at all --
    # on 2026-08-08 the operator asked where they had gone. A quiet run is
    # exactly when the standing position is worth seeing.
    if not SALES and not PURCHASES and not totals:
        return ""

    net = gross - spend
    if SALES or PURCHASES:
        lines = ["", "=" * 74,
                 f"THIS RUN: {len(SALES)} collection(s) in {gross:,} Alz, "
                 f"{len(PURCHASES)} purchase(s) out {spend:,} Alz",
                 f"          net {net:+,} Alz",
                 "=" * 74]
    else:
        lines = ["", "=" * 74,
                 "THIS RUN: nothing collected and nothing bought.",
                 "=" * 74]

    if totals:
        sales_n, all_gross, buys_n, all_spend = totals
        cogs, priced, unpriced = cost_of_goods_sold()
        # What has been paid for and NOT yet sold. Everything spent, less the
        # cost of the units that have left -- so a restock that just bought
        # 1,212 Sets shows as stock rather than as a loss.
        held = max(0, all_spend - cogs)
        lines += [
            f"  ALL TIME  {sales_n} collection(s)  in  {all_gross:>18,} Alz",
            f"            {buys_n} purchase(s)    out {all_spend:>18,} Alz",
            "  " + "-" * 70,
            f"  {'REALISED':22} {all_gross - cogs:>+29,} Alz",
            f"    takings {all_gross:,} less the {cogs:,} those units cost",
        ]
        if unpriced:
            lines.append(
                f"    NOTE: {unpriced:,} of the {priced + unpriced:,} units "
                f"sold have no recorded purchase, so their cost is not in "
                f"that figure. Most were bought before the ledger existed.")
        lines += [
            f"  {'INVENTORY (at cost)':22} {held:>29,} Alz",
            "    paid for and not yet sold - stock, not a loss",
            "  " + "-" * 70,
            f"  {'CASH FLOW':22} {all_gross - all_spend:>+29,} Alz",
            "    every Alz in less every Alz out, including stock still held",
        ]
        if len(SALES) and not len(PURCHASES):
            lines.append("  (this run bought nothing, so its own net is just "
                         "the takings)")
    lines.append("")
    return "\n".join(lines)


def sales_report() -> str:
    """The end-of-run tally, as printable text. Empty when nothing sold."""
    if not SALES:
        return ""

    by_item: dict[str, dict] = {}
    for sale in SALES:
        entry = by_item.setdefault(sale["item"],
                                   {"n": 0, "qty": 0, "gross": 0,
                                    "unmeasured": 0})
        entry["n"] += 1
        if sale["proceeds"]:
            entry["gross"] += sale["proceeds"]
        else:
            entry["unmeasured"] += 1
        if sale["qty"]:
            entry["qty"] += sale["qty"]

    gross = sum(e["gross"] for e in by_item.values())
    unmeasured = sum(e["unmeasured"] for e in by_item.values())

    lines = ["", "=" * 74,
             f"SOLD THIS RUN: {len(SALES)} collection(s), "
             f"{gross:,} Alz gross", "=" * 74,
             f"  {'item':42} {'sales':>5} {'qty':>7} {'gross':>14}",
             "  " + "-" * 70]
    for name, e in sorted(by_item.items(), key=lambda kv: -kv[1]["gross"]):
        shown = name if len(name) <= 42 else name[:39] + "..."
        qty = f"{e['qty']:,}" if e["qty"] else "-"
        lines.append(f"  {shown:42} {e['n']:>5} {qty:>7} {e['gross']:>14,}")
    lines.append("  " + "-" * 70)
    lines.append(f"  {'TOTAL':42} {len(SALES):>5} "
                 f"{sum(e['qty'] for e in by_item.values()):>7,} "
                 f"{gross:>14,}")
    if unmeasured:
        # Said out loud rather than folded into the total. An Alz reading needs
        # the Inventory panel open; when it is not, get_alz returns 0, and a
        # sale silently counted as 0 Alz would understate the gross with
        # nothing on screen to say so.
        lines.append(f"\n  {unmeasured} sale(s) could not be measured (the Alz "
                     f"balance was unreadable), so the gross above is a floor, "
                     f"not the whole of it.")
    return "\n".join(lines)


def recover_stranded_work_tab(timeout: float = 8.0,
                              verbose: bool = True) -> bool:
    """List whatever is sitting in the work tab back onto the shop.

    NO LONGER CALLED AUTOMATICALLY as of 2026-08-08. ensure_work_tab_empty now
    raises FatalAbort on a dirty tab instead of invoking this, because pricing
    an item that cannot be named means pricing it at the strictest floor on the
    books -- 175,000,000 -- and that is real money committed to a guess.

    Kept rather than deleted: it is the only code that knows how to clear a
    strand, and a future version could call it deliberately with a NAME to
    price against, which is the missing piece that made it dangerous. Left
    unreachable, not left running.

    The work tab is reserved for items a cancel returns, so anything in it at
    the START of a batch is a strand: a cancel that committed and whose re-list
    did not. Refusing to start on a dirty tab is correct -- the before/after
    diff cannot tell which slots a NEW cancel filled -- but it is also terminal,
    because nothing else the script does ever clears it. Three cycles later the
    breaker stops the run, and the item is still there.

    Priced at the STRICTEST floor on the books, deliberately not at the market.
    An item cannot be named from an inventory slot, so its own floor cannot be
    looked up, and listing an unnameable item at whatever the market says is
    exactly how a VIP goes out under its floor. Too high is the safe direction.

    Being too high is also temporary, which is what makes this work: once the
    stack is back in the shop, the ordinary relist path reads its name off the
    TABLE, looks up its real floor, and re-prices it at market on the next
    cycle. One overpriced cycle buys back a run that would otherwise have died.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    # Not if the restock pipeline is mid-flight. WORK_TAB and
    # CONVERT_INVENTORY_TAB are the same tab (4), chosen deliberately -- it is
    # the game's default and every count in the pipeline is taken there. The
    # comment on WORK_TAB used to say the two uses "do not overlap in time,
    # because converting is a manual operation"; that stopped being true when
    # --buy shipped, and restock_core now buys and converts on this tab inside
    # the unattended loop.
    #
    # So what looks like a strand may be a restock's working stock: a stack of
    # Force Core Sets bought at 187,278 each. This function prices what it
    # finds at strictest_price_floor(), which is 175,000,000 -- correct for an
    # unnameable strand, catastrophic for raw material. It would consume a shop
    # row, pay a registration fee on the inflated figure, and then the next
    # cycle would read the name off the table and offer the pipeline's own
    # Sets to the market at cost.
    #
    # The carry registry is what tells the two apart, and the restock resumes
    # from it on the next pass -- so refusing here loses nothing.
    outstanding = carried_total()
    if outstanding:
        say(f"  the work tab holds {outstanding} Set(s) a restock has already "
            "paid for and still owes a listing -- leaving them for the "
            "restock to convert rather than listing them as a strand.")
        return False

    origin = inventory_origin()
    if origin is None:
        say("  the Inventory panel is not visible, so the strand cannot be "
            "cleared automatically.")
        return False
    if not select_inventory_tab(WORK_TAB, origin):
        say(f"  could not switch to inventory tab {WORK_TAB}.")
        return False

    # A floor, never a market read. 0 floors configured is the only case with
    # nothing to fall back on, and there FALLBACK_PRICE parks it instead --
    # unsellable for one cycle, which the next cycle corrects.
    price = strictest_price_floor() or FALLBACK_PRICE
    say(f"  re-listing the stranded stack at {price:,} Alz (the strictest "
        "floor on the books) - it cannot be named from an inventory slot, so "
        "its own floor cannot be looked up. The next cycle reads the name off "
        "the table and re-prices it properly.")

    # Bounded, and requires PROGRESS. A strand that will not clear must stop
    # the run rather than be retried for ever -- which is the failure this is
    # replacing, not one to reproduce.
    for attempt in range(1, STRAND_RECOVERY_ATTEMPTS + 1):
        park_cursor()
        occupied = occupied_slots(grab(), origin)
        if not occupied:
            say(f"  inventory tab {WORK_TAB} is clear.")
            return True
        row, col = occupied[0]
        say(f"  attempt {attempt}/{STRAND_RECOVERY_ATTEMPTS}: "
            f"{len(occupied)} slot(s) in use; listing slot ({row},{col}).")
        record("strand.recovering", tab=WORK_TAB, occupied=len(occupied),
               slot=f"{row},{col}", price=price, attempt=attempt)
        try:
            listed = register_item(row, col, timeout=timeout, verbose=verbose,
                                   force_price=price, maximise_qty=True)
        except (Aborted, PermissionError) as exc:
            say(f"  could not list it: {exc}")
            return False
        if not listed:
            say("  the re-listing did not complete.")
            return False
        if not select_inventory_tab(WORK_TAB, origin):
            return False

    park_cursor()
    still = occupied_slots(grab(), origin)
    if still:
        say(f"  {len(still)} slot(s) still in use after "
            f"{STRAND_RECOVERY_ATTEMPTS} attempts - stopping rather than "
            "retrying for ever.")
        return False
    return True


def ensure_work_tab_empty(timeout: float = 8.0, verbose: bool = True) -> bool:
    """The work-tab precondition. A dirty tab STOPS THE RUN.

    This used to try to recover: list whatever was in the tab at
    strictest_price_floor() -- 175,000,000, because an inventory slot cannot be
    named -- and let the next cycle read the name off the table and re-price
    it. That is the only path in this file that can commit real money to a
    decision nobody made, and it fires exactly when the script is already
    confused about what is where.

    Measured on 2026-08-08: it reached for 175,000,000 twice against 54
    Upgrade Core (Ultimate) worth 469,469 each, and was saved only by the
    client being disconnected at the time.

    So it refuses instead, and the refusal is FATAL rather than per-cycle: a
    strand does not clear itself, so retrying it every cycle just spends the
    breaker's budget arriving at the same place. A human clears the tab in a
    minute; a wrong listing costs a row, a registration fee and a position
    nobody chose.

    Raises FatalAbort. Never returns False -- the only outcomes are "the tab is
    clean" and "stop".
    """
    if require_empty_work_tab(verbose=verbose):
        return True
    raise FatalAbort(
        f"inventory tab {WORK_TAB} is not empty. Everything in it is stock "
        "this script cannot name from a slot, so it cannot be priced safely "
        "-- clear it by hand (list it, or move it to another tab) and start "
        "again. Nothing has been listed or cancelled.")


def changed_slots(
    before: Image.Image,
    after: Image.Image,
    origin: tuple[int, int] | None = None,
) -> list[tuple[int, int]]:
    """Every inventory slot that changed between two frames, in reading order.

    Cancelling a listing of quantity N returns N separate items, filling N
    slots at once, so this must not assume a single winner.

    The panel does not move between the two frames, so one anchor serves both.
    """
    if origin is None:
        origin = inventory_origin(before) or inventory_origin(after) or inventory_origin()
    if origin is None:
        return []

    old, new = inventory_cells(before, origin), inventory_cells(after, origin)
    changed: list[tuple[int, int]] = []
    for key, cell in old.items():
        diff = ImageChops.difference(cell, new[key])
        # Pillow 14 renames getdata(); prefer the new name where available.
        flat = getattr(diff, "get_flattened_data", diff.getdata)()
        delta = sum(flat) / (diff.width * diff.height)
        if delta >= SLOT_CHANGE_MIN:
            changed.append(key)
    return sorted(changed)


def changed_slot(
    before: Image.Image,
    after: Image.Image,
    origin: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    """The first changed inventory slot, or None if nothing changed."""
    slots = changed_slots(before, after, origin)
    return slots[0] if slots else None


def tab_centre(origin: tuple[int, int], tab: int) -> tuple[int, int]:
    """Screen centre of inventory tab `tab` (1-based, I..VIII)."""
    if not 1 <= tab <= TAB_COUNT:
        raise ValueError(f"tab {tab} is outside I..{TAB_COUNT}")
    return (round(origin[0] + TAB_ONE_OFFSET[0] + TAB_PITCH * (tab - 1)),
            round(origin[1] + TAB_ONE_OFFSET[1]))


def active_inventory_tab(
    source: Image.Image | None = None, origin: tuple[int, int] | None = None
) -> int | None:
    """Which inventory tab is selected, by pixel brightness.

    The selected tab is drawn raised and lighter than the rest, which reads far
    more reliably than OCRing roman numerals (I/II/V come back as 'i', 'Vl',
    'vis' and worse).
    """
    image = source if source is not None else grab()
    if origin is None:
        origin = inventory_origin(image)
    if origin is None:
        return None

    brightness = []
    for tab in range(1, TAB_COUNT + 1):
        cx, cy = tab_centre(origin, tab)
        # Sample the RAISED TOP EDGE, not the numerals. The selected tab is
        # drawn taller, so this band is panel on the active tab and background
        # on every other one -- which is a much larger difference than the
        # slight brightening of the numerals themselves.
        #
        # Measured over the numerals, the active tab beat the median by 11.4 on
        # one frame and 7.1 on another; over this band, by 22.3 and 23.9. That
        # 7.1 cleared TAB_ACTIVE_MARGIN and returned tab 8 for a frame where
        # tab I was plainly the raised one -- a CONFIDENT wrong answer, which
        # select_inventory_tab would take as "already on the right tab" and
        # skip the click. Checked against 40 recorded run frames: identical
        # verdict on every one, with a wider margin on all of them.
        cell = image.crop((cx - 22, cy - 25, cx + 22, cy - 15)).convert("L")
        data = list(getattr(cell, "get_flattened_data", cell.getdata)())
        brightness.append((sum(data) / len(data), tab))

    # Compare against the median rather than the runner-up: the tab row's ends
    # are a little brighter than its middle, so the second-brightest tab can sit
    # close to the active one while the median stays well below both.
    values = sorted(v for v, _ in brightness)
    median = (values[TAB_COUNT // 2 - 1] + values[TAB_COUNT // 2]) / 2
    best = max(brightness)
    return best[1] if best[0] - median >= TAB_ACTIVE_MARGIN else None


def select_inventory_tab(
    tab: int, origin: tuple[int, int] | None = None, timeout: float = 5.0
) -> bool:
    """Click an inventory tab and confirm it became the active one."""
    if origin is None:
        origin = inventory_origin()
    if origin is None:
        return False
    if active_inventory_tab(origin=origin) == tab:
        return True

    click(*tab_centre(origin, tab))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if active_inventory_tab(origin=origin) == tab:
            return True
        time.sleep(0.4)
    return False


# Note: identifying the loaded item by hovering it for a tooltip was tried and
# removed. The tooltip has to be isolated by diffing frames, which proved
# unreliable in both directions -- loose enough to read the listings table
# behind it, tight enough to reject the real tooltip -- and every failure
# stranded the item in the shop slot. The listing is verified instead by
# sanity_check(), after it exists and can simply be read off the table.


def _digits(text: str) -> int | None:
    cleaned = re.sub(r"[^0-9]", "", text)
    return int(cleaned) if cleaned else None


# x beyond which the price rows hold the refresh button, not the figure.
PRICE_TEXT_MAX_X = 230
# The selected price row renders in gold and OCRs poorly -- around 33% where the
# unselected row scores 90 -- so this field needs a lower bar than the default.
# Junk that sneaks in is rejected by _price_value() rather than by confidence.
PRICE_MIN_CONF = 15.0


def _price_value(text: str) -> int | None:
    """Parse a price row's text. None when it is not a price at all.

    "0 Alz" comes back from OCR as 'OAlz' / 'OAIz' -- the digit zero read as a
    letter -- so a row with no digits but nothing else in it means zero, not
    unreadable. Getting that wrong turns "no market data" into "row missing".
    """
    cleaned = re.sub(r"a[il1]z", "", text, flags=re.IGNORECASE)
    digits = re.sub(r"[^0-9]", "", cleaned)
    if digits:
        return int(digits)
    # A zero renders as "0" but OCRs as "O"/"o"/"()" and similar. Require one
    # of those to actually be present: an empty or punctuation-only read means
    # the figure was not read at all, and reporting that as a real 0 sends the
    # caller down the "no market data" path and out to the 10B fallback.
    if re.fullmatch(r"[Oo0°©()\s.,]*", cleaned) and \
            re.search(r"[Oo0°©()]", cleaned):
        return 0
    return None


def read_register_panel(source: Image.Image | Path | str) -> dict:
    """Price and quantity currently shown on the Register Item panel.

    Returns a dict including 'prices', 'price_rows' (value with radio y, top
    row first), 'qty', 'qty_max', 'net_sales', and 'loaded'.

    Use 'loaded' -- not qty_max -- to tell whether an item is in the shop slot:
    the quantity's "/ MAX" separator OCRs unreliably, so qty_max is often None
    even when an item is sitting there.
    """
    image = source if isinstance(source, Image.Image) else Image.open(source)

    # Read the two suggested-price rows at their known positions rather than by
    # hunting for digits: a "0 Alz" row contains no digits at all, and a row
    # that is simply missed must not be confused with one that reads zero.
    words = find_words(image, PRICE_ROWS, PRICE_MIN_CONF)
    # A row that came back empty is re-read with the confidence bar dropped.
    #
    # The SELECTED row renders in gold, and Tesseract scores it 0.0 while
    # returning the text exactly right. On 2026-08-06 that discarded a
    # perfectly legible "1,500,000Alz" at y=513 twice, register_item aborted
    # AFTER the cancel had committed, and 'SIGmetal Suit (DM)' was stranded and
    # then re-listed by the strand recovery at 180,000,000 -- the strictest
    # floor -- against a real market price of 1,500,000. The run then died on
    # consecutive failures. Both recorded frames read:
    #
    #     conf>=15: [('1,822,160Alz', 91.9, 477)]
    #     conf>=0 : [('1,822,160Alz', 91.9, 477), ('1,500,000Alz', 0.0, 513)]
    #
    # Only consulted when the bar has already lost the row, so a frame that
    # reads correctly today cannot change. _price_value is the real validator
    # -- it rejects anything that is not a plausible price -- and the
    # confidence number was never doing that job.
    lenient: list | None = None
    prices: list[tuple[int, int]] = []
    for expected_y in (PRICE_TOP_Y, PRICE_BOTTOM_Y):
        on_row = [w for w in words
                  if abs(w.centre[1] - expected_y) <= PRICE_ROW_Y_TOL
                  and w.centre[0] < PRICE_TEXT_MAX_X]
        if not on_row:
            if lenient is None:
                lenient = find_words(image, PRICE_ROWS, 0.0)
            on_row = [w for w in lenient
                      if abs(w.centre[1] - expected_y) <= PRICE_ROW_Y_TOL
                      and w.centre[0] < PRICE_TEXT_MAX_X]
        if not on_row:
            continue
        text = "".join(w.text for w in sorted(on_row, key=lambda w: w.left))
        value = _price_value(text)
        if value is None:
            continue
        y = round(sum(w.centre[1] for w in on_row) / len(on_row))
        prices.append((value, y))

    typed = None
    for word in find_words(image, PRICE_FIELD):
        typed = _digits(word.text) or typed

    qty = qty_max = None
    qty_text = " ".join(
        w.text for w in sorted(find_words(image, QTY_FIELD, QTY_MIN_CONF),
                               key=lambda w: w.left)
    )
    # Pull the numbers out rather than insisting on a '/', which OCR mangles.
    numbers = [_digits(chunk) for chunk in re.findall(r"\d[\d,]*", qty_text)]
    numbers = [n for n in numbers if n is not None]
    if numbers:
        qty = numbers[0]
        if len(numbers) > 1:
            qty_max = numbers[-1]

    # Net sales is the game's own price x quantity; it stays 0 until a price is
    # actually selected, which makes it the reliable "ready to register" signal.
    # Join, do not max(): a split total ("1,260," + "000,000") read as 1,260
    # fails the price x quantity equality and aborts a correct listing.
    net_cell = sorted(find_words(image, NET_SALES_ROWS), key=lambda w: w.left)
    net = _digits("".join(w.text for w in net_cell)) or 0

    # Whether an item is sitting in the shop slot, judged by pixels rather than
    # text: an empty box is near-uniform dark, any icon is not. Text signals are
    # unreliable here -- the quantity's "/ MAX" often fails to OCR at all.
    box = image.crop(SHOP_SLOT_BOX).convert("L")
    pixels = list(getattr(box, "get_flattened_data", box.getdata)())
    mean = sum(pixels) / len(pixels)
    stdev = (sum((p - mean) ** 2 for p in pixels) / len(pixels)) ** 0.5
    loaded = stdev >= SHOP_SLOT_STDEV or bool(qty) or net > 0

    return {"prices": [p for p, _ in prices], "price_rows": prices, "typed": typed,
            "qty": qty, "qty_max": qty_max, "qty_text": qty_text,
            "net_sales": net, "loaded": loaded, "slot_stdev": round(stdev, 1)}


def await_dialog(kind: str | None, timeout: float = 8.0, poll: float = 0.35):
    """Poll until the dialog state equals `kind`. Returns the screenshot proving
    it, or None on timeout. `kind=None` waits for every dialog to be gone."""
    deadline = time.monotonic() + timeout
    while True:
        shot = grab()
        if dialog_kind(shot) == kind:
            return shot
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


class Aborted(Exception):
    """A step did not produce the state the sequence requires."""


class FatalAbort(Exception):
    """Something went wrong that must stop the whole run, not just this cycle.

    Raised when the script has listed something that does not match what was
    there before. Retrying cannot help and might list it wrong again, so the
    bad listing is pulled and everything stops for a human to look.
    """


class ShopEmpty(Exception):
    """Every row asked for is an empty slot: there is nothing left to relist.

    Deliberately NOT an Aborted subclass. Aborted means "this cycle did not
    work, try again"; this means "the work is finished". A sold-out shop is a
    success, and retrying it every cycle for the rest of a 500-minute run
    achieves nothing but keeps the game awake and the cursor moving.

    The loop catches this, closes the Agent Shop, and stops.
    """


def cancel_item(
    row: int,
    dry_run: bool = False,
    timeout: float = 8.0,
    verbose: bool = True,
    expect: "RowRef | None" = None,
    report: "dict | None" = None,
) -> bool:
    """Cancel the listing on table row `row` (1-based, as displayed).

    Rows are counted exactly as they appear, including sold rows (which show
    Receive) and empty slots (which show Register); those cannot be cancelled.

    Pass `expect` whenever the row number came from an earlier table read: this
    function re-reads the table, and the row that was chosen may not be the row
    that number now points at. Without it the only check is that the row says
    "Change", which any listing does.

    The sequence is strict: Change must produce the Registration Extension
    dialog, its Cancel must produce the confirmation dialog, and Confirmation
    must close it. Any deviation aborts, backs out of whatever is on screen,
    and returns False without committing anything.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise Aborted(reason)

    # Filled in for the caller, because False alone cannot be acted on.
    #
    # A cancel that failed WITHOUT committing left the listing on the market
    # and is safe to retry; one that committed and could not be verified is
    # not, because a second attempt would withdraw a second listing. Both
    # returned a bare False, so every caller had to assume the second -- and
    # two rows were skipped on 2026-08-08 for aborts that recorded
    # committed=False, meaning the Change click had produced no dialog at all.
    if report is not None:
        report.setdefault("committed", False)
        report.setdefault("reason", "")

    committed = False
    try:
        # ---- preconditions, before anything is clicked ----------------------
        if not dry_run:
            require(not session_locked(),
                    "the workstation is locked - screen capture is blank and "
                    "input goes to the secure desktop")
            require(focus_game(), "could not bring Cabal to the foreground")
            # Read with nothing hovered, or an item tooltip covers the table.
            park_cursor()
            require(
                not table_loading(grab()) or wait_for_table(max(timeout, 20.0)),
                "the table is still waiting for the server response",
            )
            require(
                dialog_kind(grab()) is None,
                "a dialog was already open before starting",
            )

        rows = await_rows(timeout)
        require(bool(rows),
                "no listings visible - is the Trade window open on Register?")
        require(1 <= row <= len(rows),
                f"row {row} is out of range; {len(rows)} row(s) visible")

        target = rows[row - 1]
        say(f"Row {row}: {target.name!r} [{target.action}] -> button at {target.change}")
        require(target.cancellable,
                f"row {row} shows '{target.action}', not 'Change'")

        # A row NUMBER is not an identity. The caller resolved this row from a
        # different table read, tens of seconds and one server refresh ago;
        # this function then re-read the table and indexed into it blind,
        # checking only that whatever sits there now says "Change". A row
        # collected or sold in between shifts everything up, and the wrong
        # listing gets cancelled -- with the previous row's name, quantity and
        # price floor then carried into the relist.
        if expect is not None:
            # Resolved NON-strictly, then checked against the index we were
            # given. Strict mode refuses whenever two rows are indistinguishable
            # -- exactly the duplicate-stack case the caller already resolved by
            # position before handing the identity down. Re-asking strictly here
            # meant two identical stacks could never be cancelled at all: the
            # caller picked a sibling, this refused it, the batch stopped, and
            # three cycles later the whole run gave up. This shop has two such
            # pairs today.
            #
            # `resolved.index == row` is the guard that matters anyway: it
            # catches the table having shifted, which is why this check exists.
            resolved, note = locate_row(rows, expect)
            moved = f"it is now at row {resolved.index}" if resolved else "it is gone"
            require(resolved is not None and resolved.index == row,
                    f"row {row} no longer holds {expect.name!r} "
                    f"({note or moved}) - the table changed since it was "
                    "chosen, so nothing was cancelled")
            if note:
                say(f"  {note}")
            say(f"  identity confirmed: row {row} still holds {expect.name!r}")

        if dry_run:
            say("[dry run] would click Change -> Cancel -> Confirmation")
            return True

        # ---- step 1: Change must open the Registration Extension dialog -----
        record("cancel.before_change", row=row, name=target.name,
               price=target.price, qty=target.qty)
        click(*target.change)
        shot = await_dialog("extension", timeout)
        # `shot` positionally, not shot=: it is a positional-only parameter, so
        # the keyword form quietly became *context* -- the frame saved was
        # whatever grab() last returned rather than this one, and a PIL object
        # was serialised into the index as a repr string.
        record("cancel.after_change", shot, row=row, name=target.name,
               dialog="extension" if shot else "none")
        if shot is None:
            # Say what WAS on screen. This failure has recurred, and "it did
            # not appear" is indistinguishable between three different causes:
            # the click missed, the dialog opened but was classified as another
            # kind, or its title would not OCR. Escape failing to close the
            # Trade window afterwards suggests a modal really is up, so the
            # words below are the evidence needed to tell them apart.
            probe = grab()
            say(f"  dialog_kind sees: {dialog_kind(probe)!r}")
            say(f"  trade window still open: {trade_window_open(probe)}")
            words = sorted(find_words(probe, POPUP_REGION, 25),
                           key=lambda w: -w.conf)[:12]
            say("  strongest words in the dialog area: "
                + ", ".join(f"{w.text!r}@{w.conf:.0f}" for w in words))
            # The diagnostic frame has caught the dialog arriving late. On
            # 2026-08-04 at 07:57 this probe read 'extension' on the line
            # AFTER the wait gave up, and the run aborted regardless -- then
            # left that dialog covering the table for the next two cycles.
            #
            # A fresh read of the exact dialog expected is evidence, not a
            # guess: clicking a row's Change button opens this one and nothing
            # else, and if the state is wrong anyway the next step's own
            # require() catches it before anything commits.
            if dialog_kind(probe) == "extension":
                say("  ...but it IS up on a fresh frame: it arrived after the "
                    "wait expired. Continuing rather than aborting.")
                shot = probe
            else:
                # One probe is a one-frame window, and the dialog does not
                # arrive on a schedule. A sweep of arrival times showed the
                # single recheck rescuing NONE of them: it happened to land on
                # the right frame at 07:57 and would not have next time.
                #
                # So look again properly. This only runs on a path that was
                # about to abort, so the cost is a few seconds on a failure
                # rather than on every cancel.
                shot = await_dialog("extension", EXTENSION_RECHECK_SECONDS)
                if shot is not None:
                    say("  ...it IS up on a fresh frame after a second look; "
                        "continuing rather than aborting.")
        require(shot is not None, "the Registration Extension dialog did not appear")

        cancel = await_dialog_button(DISMISS_WORD, timeout)
        require(cancel is not None,
                "no Cancel button on the Registration Extension dialog")

        # ---- step 2: Cancel must open the confirmation dialog ---------------
        say(f"{DISMISS_WORD} button at {cancel.centre} (conf {cancel.conf:.0f})")
        click(*cancel.centre)
        shot = await_dialog("confirm", timeout)
        require(shot is not None, "the confirmation dialog did not appear")

        confirm = await_dialog_button(CONFIRM_WORD, timeout)
        require(confirm is not None,
                "no Confirmation button on the confirmation dialog")

        # ---- step 3: Confirmation commits and must close the dialog ---------
        say(f"{CONFIRM_WORD} button at {confirm.centre} (conf {confirm.conf:.0f})")
        click(*confirm.centre)
        committed = True
        # A cancellation shifts every row below it up by one, so the remembered
        # catalogue now names rows it does not mean. Dropped on the click being
        # SENT, not on it being confirmed: if we are unsure whether the shop
        # changed, the safe answer is that it did.
        note_shop_changed("cancel committed")
        require(await_dialog(None, timeout) is not None,
                "the dialog stayed open after Confirmation")

        record("cancel.committed", row=row, name=target.name,
               price=target.price, qty=target.qty)
        say(f"Cancelled registration on row {row}: {target.name!r}.")
        return True

    except Aborted as exc:
        # The most valuable frame in the whole run: whatever was on screen when
        # the sequence refused to continue. Recorded before any recovery
        # clicking, so the corpus keeps the state that caused it rather than
        # the state after backing out of it.
        # Read the dialog BEFORE recording, so the record carries what was
        # determined rather than contradicting it. `committed` means only "the
        # Confirmation click was sent"; whether the game ACCEPTED it is a
        # different fact, and the corpus was storing the first while the log
        # printed the second -- three recorded aborts say committed=True for
        # cancellations the log calls refused. Reading costs one screenshot and
        # no clicks, so it still happens before any recovery input.
        still = dialog_kind(grab()) if committed else None
        if report is not None:
            report["committed"] = committed
            report["reason"] = str(exc)
        record("cancel.aborted", reason=str(exc), row=row, committed=committed,
               dialog_after=still,
               accepted=None if not committed else (still != "confirm"))
        say(f"ABORTED: {exc}.")
        if committed:
            # Past the point of no return; only report, never click further.
            #
            # But "clicked" is not "accepted". A cancellation the game took
            # closes the dialog; if the confirmation dialog is STILL up, the
            # game refused the action and the listing is intact. Reading that
            # costs one screenshot and no clicks, and it replaces a guess with
            # an observation -- the previous message said the cancel "may have
            # gone through" in exactly the case where it provably had not,
            # which sends you hunting for a stranded item that does not exist.
            if still == "confirm":
                # An inference, not an observation, and it can be wrong:
                # the game stacks confirmation dialogs (MAX_CONFIRM_STEPS
                # exists for that on the register side), so it can commit
                # AND still be showing one. Saying "still on the market"
                # as fact would send the operator away from a listing that
                # had in fact been withdrawn.
                say("A confirmation dialog is still open. USUALLY that "
                    "means the game refused the cancellation and the "
                    "listing is untouched - but the game can also stack "
                    "dialogs after accepting one, so this is not proof.")
                say("CHECK THE LISTING before retrying: if it is gone, the "
                    f"stack is in inventory tab {WORK_TAB}, unlisted.")
                # Deliberately hedged. The corpus contains three consecutive
                # refusals of the same listing where the work tab was verified
                # EMPTY on all three, so free space in tab 4 is demonstrably
                # not the whole story -- the game may check total space across
                # every tab, which one frame cannot see.
                say("A likely cause is not enough free inventory space to "
                    "receive the stack: a cancelled 250-item listing comes "
                    "back as ~64 separate slots, and the game refuses rather "
                    "than partially withdrawing. Note the check covers your "
                    "WHOLE inventory, not just the work tab -- this has been "
                    "observed refusing while the work tab was empty.")
                say("Retrying this row will refuse identically until space is "
                    "freed, so the run will stop after "
                    f"{MAX_CONSECUTIVE_FAILURES} attempts.")
            else:
                say("WARNING: Confirmation was already clicked and the dialog "
                    f"is now {still!r}, so the cancellation may have gone "
                    "through. Check the listing before retrying.")
            return False
        # --dry-run is exempt from the elevation gate precisely because it does
        # not click. This recovery path was not gated, so a dry run that
        # aborted early went on to click its way out of a dialog.
        if dry_run:
            say("[dry run] leaving the screen exactly as it is.")
            return False
        # dialog_present, not dialog_kind: a title that failed to OCR read as
        # "no dialog", so this branch announced "Nothing was changed" and left
        # a modal sitting over the table. Everything after it -- the rest of
        # the batch and the whole next cycle -- then failed to read any rows.
        if not dialog_present():
            say("Nothing was changed.")
        elif close_any_dialog():
            say("Backed out of the open dialog; nothing was changed.")
        else:
            say("WARNING: could not close the dialog still on screen - "
                "dismiss it manually before rerunning.")
        return False


def clear_shop_slot(timeout: float = 15.0, verbose: bool = True) -> bool:
    """Ctrl+Click the shop slot to send its item back to the inventory."""
    # Without the Trade window open, SHOP_SLOT_BOX samples the game world and
    # the verdict is meaningless -- either "clear" while an item sits there, or
    # a Ctrl+Click into the open world.
    if not trade_window_open():
        if verbose:
            print("The Trade window is not open, so the shop slot cannot be read.")
        return False

    panel = read_register_panel(grab())
    if not panel["loaded"]:
        return True
    if verbose:
        print(f"Returning the shop slot item (qty {panel['qty_text']!r}) to the inventory")
    ctrl_click(*SHOP_SLOT)
    # Move off the slot before polling: leaving the cursor there raises the
    # item tooltip, which renders over SHOP_SLOT_BOX and keeps the pixel spread
    # high, so "loaded" would never go false and every cycle would fail setup.
    park_cursor()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not read_register_panel(grab())["loaded"]:
            return True
        time.sleep(0.5)
    return False


def register_item(
    row: int,
    col: int,
    dry_run: bool = False,
    timeout: float = 8.0,
    verbose: bool = True,
    price_floor: int = 0,
    floor_price: int | None = None,
    floor_reason: str = "",
    maximise_qty: bool | None = None,
    force_price: int | None = None,
    force_qty: int | None = None,
    expect_item: str | None = None,
    expect_qty: int | None = None,
    report: dict | None = None,
) -> bool:
    """List the item in inventory slot (row, col) on the Agent Shop.

    Ctrl+Clicks the slot into the shop slot, selects the lowest currently
    listed price (the bottom of the two suggested rows; the top row is the
    week's average), or types FALLBACK_PRICE when that row reads 0, leaves the
    quantity at whatever the game defaulted to, and presses Register.

    Strict: every step must produce the expected state or the whole thing
    aborts and backs out without listing anything.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise Aborted(reason)

    committed = False
    try:
        # Gated on dry_run: focus_game() injects a synthetic Alt tap and
        # park_cursor() moves the physical cursor, so running these in a dry
        # run contradicts "locate everything but do not click" -- and the
        # elevation check is skipped for dry runs, so they were unguarded too.
        if not dry_run:
            require(focus_game(), "could not bring Cabal to the foreground")
            park_cursor()
            require(not table_loading(grab()) or wait_for_table(max(timeout, 20.0)),
                    "the table is still waiting for the server response")
            require(dialog_kind(grab()) is None, "a dialog was already open")

        panel = read_register_panel(grab())
        require(not panel["loaded"],
                f"the shop slot already holds an item "
                f"(qty {panel['qty_text']!r}, spread {panel['slot_stdev']})")

        centre = slot_centre(row, col)
        record("register.before_load", row=row, col=col, item=expect_item)
        say(f"Ctrl+Click inventory slot ({row},{col}) at {centre}")
        if dry_run:
            say("[dry run] would load the slot, set the price, then Register")
            return True

        # ---- step 1: load the item into the shop slot -----------------------
        # Ctrl+Click commits nothing, so retrying is safe. The click after a
        # dialog closes is sometimes swallowed while the game takes focus back.
        panel = {"loaded": False}
        for attempt in range(1, LOAD_ATTEMPTS + 1):
            ctrl_click(*centre)
            time.sleep(0.8)
            park_cursor()
            panel = read_register_panel(grab())
            if panel["loaded"]:
                break
            if attempt < LOAD_ATTEMPTS:
                say(f"Slot ({row},{col}) did not load on attempt {attempt}; retrying.")
                time.sleep(0.6)
        require(panel["loaded"],
                f"nothing loaded into the shop slot after {LOAD_ATTEMPTS} attempts "
                f"(slot ({row},{col}) may be empty, or the item moved)")
        say(f"Loaded: qty {panel['qty_text']!r} -> {panel['qty']}/{panel['qty_max']}, "
            f"suggested {panel['prices'] or 'none'}")

        # ---- quantity cross-check -------------------------------------------
        # The loaded stack should hold as many as the cancelled listing did.
        # This is a cheap consistency check, not proof of identity -- that is
        # sanity_check()'s job, after the listing exists and can be read back.
        # One re-read before concluding the field is unreadable. The comment
        # further down calls this "the worst OCR target on the panel", and a
        # second look costs a screenshot and no clicks -- far cheaper than
        # silently skipping the only identity check available before the
        # listing goes live.
        if (expect_item and expect_qty is not None
                and panel["qty_max"] is None):
            time.sleep(0.4)
            again = read_register_panel(grab())
            if again.get("loaded") and again.get("qty_max") is not None:
                say(f"  the quantity field re-read as {again['qty_max']} "
                    "(it was unreadable a moment ago).")
                panel = again

        if (expect_item and expect_qty is not None
                and panel["qty_max"] is None):
            # Loud, recorded, and NOT an abort.
            #
            # The cancel has already committed, so refusing here strands the
            # stack -- and a strand terminates the run. Trading a possible
            # mis-list for a guaranteed stop is the wrong way round.
            #
            # But a check that quietly does not happen is worse than one that
            # fails, because the output reads exactly like a verified listing.
            # sanity_check reads the result back off the table afterwards,
            # which is the check that actually proves identity; this one is
            # only a cheap early warning, and its absence is now visible.
            say(f"WARNING: the panel's quantity field did not read, so the "
                f"cross-check against the cancelled listing's {expect_qty} "
                f"could NOT be performed. The listing will still be verified "
                f"against the table afterwards.")
            record("register.qty_unverified", item=expect_item,
                   expect_qty=expect_qty, row=row, col=col,
                   qty_text=panel.get("qty_text"))
            if report is not None:
                report["qty_unverified"] = True

        if expect_item and expect_qty is not None and panel["qty_max"] is not None:
            # Exact equality here stranded a 233-item stack and stopped a
            # five-hour run. The table's QTY column read 233 as 230 -- one
            # glyph -- and this fired AFTER the cancel had committed, so the
            # item was already out of the shop and in the work tab. Three
            # cycles then failed the empty-work-tab check and the breaker
            # stopped everything.
            #
            # Which number to trust: `expect_qty` comes from the table's
            # cramped QTY column, `qty_max` from the panel's dedicated numeric
            # field. When they disagree slightly the PANEL is the better read,
            # and the quantity typed is MAX_QTY_ENTRY anyway, so the game
            # clamps to whatever is really there.
            #
            # The check's stated purpose is catching a completely different
            # item, and a different item does not differ by 1% -- it differs by
            # orders of magnitude. So tolerate OCR noise, refuse on a real
            # discrepancy, and never abort over a rounding error once the
            # cancel is irreversible.
            loaded = panel["qty_max"]
            slack = max(QTY_CROSSCHECK_ABSOLUTE,
                        int(expect_qty * QTY_CROSSCHECK_FRACTION))

            # A LOWER BOUND, not equality. These are two different quantities:
            #
            #   expect_qty  what the CANCELLED LISTING held
            #   loaded      what the panel offers, which is everything you own
            #               of this item across the WHOLE inventory -- a
            #               Ctrl+Click gathers matching items from every tab,
            #               not only the slots the cancel just filled
            #
            # They are equal only while nothing else of that item is held
            # anywhere. Measured on 2026-08-08 in a single run: Epic Booster
            # returned 6 slots and loaded 6, returned 8 and loaded 8 -- then
            # Force Core (Ultimate) returned 5 and loaded 12, because seven
            # more sat on later tabs. That is not an anomaly, it is what the
            # restock pipeline produces: a 250-Core conversion spills past
            # tab 4 by design.
            #
            # It aborted AFTER the cancel committed, stranding five Cores in
            # the work tab -- which now stops the following run outright.
            #
            # Owning MORE than the listing held is ordinary and is allowed.
            # Owning FEWER is the case worth refusing: the stack that was just
            # cancelled should be in the inventory, so a shortfall means it is
            # not all there, or the wrong slot was picked up.
            if loaded >= expect_qty:
                if loaded > expect_qty:
                    say(f"NOTE: the panel offers {loaded} but the cancelled "
                        f"listing held {expect_qty}. The extra are the same "
                        "item held elsewhere in the inventory, which the shop "
                        "slot gathers; continuing.")
                    # `if report is not None`, not `report and`: every caller
                    # passes a fresh {}, which is FALSY, so the truthiness form
                    # never executed once.
                    if report is not None:
                        report["qty_extra"] = (expect_qty, loaded)
            elif expect_qty - loaded <= slack:
                say(f"NOTE: the panel holds {loaded} but the table said "
                    f"{expect_qty} (short by {expect_qty - loaded}, within "
                    f"{slack}). The panel field is the more reliable read, so "
                    "continuing with it - the table's QTY column is narrow and "
                    "misreads a digit occasionally.")
                if report is not None:
                    report["qty_disagreement"] = (expect_qty, loaded)
            else:
                require(False,
                        f"the shop slot offers only {loaded} but the cancelled "
                        f"listing held {expect_qty} - short by "
                        f"{expect_qty - loaded}, more than the {slack} "
                        f"tolerated. The cancelled stack should be in the "
                        f"inventory, so either it is not all there or this is "
                        f"the wrong slot")

        # ---- step 2: quantity, settled before pricing -----------------------
        # The suggested price is per item and Net sales is price x quantity, so
        # the quantity has to be final before either figure means anything.
        # Type it and move on. The field clamps entry to the stack's maximum,
        # so anything larger than a stack can hold fills it, and nothing is
        # read back here: this field is the worst OCR target on the panel --
        # '158' came back as '15a', '2 / 2' as '[2' -- and every attempt to
        # verify it against a number read out of the same field aborted runs
        # whose quantity had gone in perfectly well.
        #
        # It is not unverified, only verified later and by a better number: the
        # quantity the game settles on is recovered below from Net sales, which
        # the game computes itself and which OCRs cleanly.
        # maximise_qty=None means "use the configured policy". Passing it
        # explicitly is for callers that have already decided.
        #
        # This defaulted to False, so the two entry points read
        # MAXIMISE_ALL_QUANTITIES differently: the relist path resolved it via
        # wants_max_quantity() and maximised, while `--register R C` silently
        # did not. A stack of six VIP passes listed as ONE, and the setting
        # that was supposed to govern it had no effect on that path at all.
        #
        # The exclusion list can only be applied to an item the script can
        # name. Where it cannot -- `--register` reads an inventory slot, not a
        # listing -- an empty list is unambiguous and a non-empty one is not,
        # so the ambiguous case is refused rather than guessed. That mirrors
        # how pricing already treats an unnameable item.
        if maximise_qty is None:
            if force_qty:
                # An explicit quantity settles it, so the policy and the
                # exclusion list are moot -- refusing here would reject
                # `--register R C --qty 6`, which states exactly what to do.
                maximise_qty = False
            elif expect_item:
                maximise_qty = wants_max_quantity(expect_item)
            else:
                require(not (MAXIMISE_ALL_QUANTITIES and NO_MAX_QUANTITY_ITEMS),
                        "cannot decide whether to maximise the quantity: the "
                        "item in this slot cannot be named, so "
                        f"NO_MAX_QUANTITY_ITEMS {NO_MAX_QUANTITY_ITEMS} cannot "
                        "be checked against it. Pass --qty N or --max-qty to "
                        "say which you want")
                maximise_qty = MAXIMISE_ALL_QUANTITIES

        entry = force_qty if force_qty else (MAX_QTY_ENTRY if maximise_qty else None)
        if entry is not None:
            record("qty.before_typing", entry=entry, item=expect_item)
            say(f"Setting quantity: typing {entry}"
                + ("" if force_qty else " - the game clamps it to the stack maximum"))
            click(*QTY_INPUT)
            # The quantity field holds at most len(str(MAX_QTY_ENTRY)) digits,
            # so clearing it needs that many backspaces plus a little slack --
            # not the price field's worth.
            type_number(entry, clear=len(str(MAX_QTY_ENTRY)) + 2)
            time.sleep(0.4)
            park_cursor()

        # ---- step 3: settle on a price --------------------------------------
        # Two rows: the top is the week's average price, the bottom is the
        # lowest currently listed. Always take the bottom one, by position --
        # never by value, which would drift between rows run to run.
        rows_seen = panel["price_rows"]

        # Resolve the floor BEFORE any branch can set a price. DO NOT REMOVE,
        # and do not move this inside a branch: it lived in the market-price
        # branch, so --price skipped it entirely and listed a VIP at whatever
        # the caller typed.
        #
        # An unidentified item takes the strictest floor rather than none. The
        # old `... if expect_item else 0` failed OPEN: every caller except the
        # relist cycle passes no name, so `--register` and `do register` listed
        # floor-bearing items with no floor at all.
        if expect_item:
            # See listing_floor: the operator's floor and what was paid,
            # whichever is higher.
            absolute_floor, floor_reason_text = listing_floor(expect_item)
            if absolute_floor:
                say(f"Floor for this item: {absolute_floor:,} Alz "
                    f"({floor_reason_text})")
        else:
            # The item cannot be named here, so its floor cannot be looked up.
            #
            # Substituting the strictest floor and listing at it is NOT the
            # safe direction, which an earlier version assumed. Nine of the ten
            # things on this account are worth 85,000-15,000,000; listing one
            # at 110,000,000 pays a percentage sales fee on that inflated
            # figure and takes the item off the market for the whole listing
            # period. Refusing costs nothing by comparison.
            #
            # So: auto-pricing an unidentified item is refused outright. A
            # human stating the price with --price is honoured -- that is an
            # explicit instruction, not a guess.
            absolute_floor = 0
            require(force_price is not None,
                    "cannot price an item the script cannot name, because its "
                    "price floor cannot be looked up. Use --relist, which "
                    "reads the name off the listing, or pass --price to state "
                    "the price yourself")
            # An unnamed item could be anything, including a VIP. A stated
            # price below the strictest floor on the books therefore needs the
            # item named -- otherwise `--register R C --price 410000` on a slot
            # that happens to hold a VIP dumps 110M of stock, and no
            # sanity_check runs on that path to catch it.
            strictest = strictest_price_floor()
            require(not strictest or force_price >= strictest,
                    f"--price {force_price:,} is below the strictest floor on "
                    f"the books ({strictest:,}) and the item cannot be named "
                    f"here, so it might be one the floor protects. Use "
                    f"--relist, which reads the name off the listing")
        if absolute_floor:
            say(f"Absolute floor for this item: {absolute_floor:,} Alz")

        # `is not None`, matching the guard above. Testing truthiness let a
        # price of 0 satisfy `require(force_price is not None, ...)` and then
        # fall through to MARKET pricing with absolute_floor still 0 -- exactly
        # the fail-open the guard exists to close.
        if force_price is not None:
            # An explicit price is a human instruction, so it is honoured --
            # but never below the floor of an item the script has identified.
            require(not (expect_item and absolute_floor
                         and force_price < absolute_floor),
                    f"--price {force_price:,} is below the {absolute_floor:,} "
                    f"floor for {expect_item!r}")
            suggested, price_y = force_price, None
            price, why = force_price, "forced by --price"
            say(f"Price forced to {force_price:,} Alz")
        else:
            require(bool(rows_seen), "no suggested-price rows could be read")

            # Take the row sitting at the bottom position, and verify by y that
            # it really is that row -- reading only the top row must not be
            # mistaken for reading the bottom one.
            suggested, price_y = min(rows_seen,
                                     key=lambda r: abs(r[1] - PRICE_BOTTOM_Y))
            require(abs(price_y - PRICE_BOTTOM_Y) <= PRICE_ROW_Y_TOL,
                    f"the lowest-current-price row was not found; read {rows_seen} "
                    f"(expected a row near y={PRICE_BOTTOM_Y})")

            average = next((p for p, y in rows_seen
                            if abs(y - PRICE_TOP_Y) <= PRICE_ROW_Y_TOL), None)
            record("price.suggestions", lowest=suggested, rows=str(rows_seen))
            say(f"Suggested: lowest current {suggested:,}, week average "
                + (f"{average:,}" if average else "unread"))
            if price_floor:
                say(f"--floor: {price_floor:,} Alz"
                    + (f" ({floor_reason})" if floor_reason else ""))

            # NOTE: absolute_floor is resolved above, before any branch. It was
            # recomputed here as `... if expect_item else 0`, which silently
            # undid that and handed every unnamed caller a floor of zero.
            price, why = choose_price(suggested, price_floor, floor_price,
                                      absolute_floor)
            if why and floor_reason:
                why = f"{why} ({floor_reason})"

            # A market far below the previous price is only reported, never
            # overridden: the rule is to take the lowest current price.
            if (floor_price and suggested > 0
                    and suggested < floor_price * SUSPECT_PRICE_FRACTION):
                # Reports the RAW market read, which is no longer what gets
                # listed -- RELATIVE_PRICE_FLOOR clamps the drop. This used to
                # end "listing at the market price anyway", which the ratchet
                # made untrue. The size is deliberately not restated here: it
                # has moved twice, and this comment said 10% while the constant
                # said 5%.
                say(f"NOTE: market {suggested:,} is only "
                    f"{suggested / floor_price:.1%} of the previous "
                    f"{floor_price:,} - a drop that large is as likely to be a "
                    f"misread as a real market, so it is listed at "
                    f"{price:,} instead.")

        # The last gate before anything is clicked, and it covers every branch
        # above. MIN_PLAUSIBLE_PRICE previously guarded only the price read off
        # the table, never the one about to be listed -- so a clipped market
        # read ("105,000,000" losing all but "105,") went straight through and
        # would have listed at 105 Alz.
        require(price >= MIN_PLAUSIBLE_PRICE,
                f"refusing to list at {price:,} Alz, below the "
                f"{MIN_PLAUSIBLE_PRICE:,} plausibility floor - the price was "
                "probably misread")
        require(not absolute_floor or price >= absolute_floor,
                f"refusing to list at {price:,} Alz, below the "
                f"{absolute_floor:,} floor for this item")

        if price == suggested and price_y is not None and price > 0:
            record("price.before_select", price=price, y=price_y)
            click(PANEL_RADIO_X, price_y)
        else:
            say(f"Overriding to {price:,} Alz - {why}")
            click((PRICE_FIELD[0] + PRICE_FIELD[2]) // 2,
                  (PRICE_FIELD[1] + PRICE_FIELD[3]) // 2)
            type_number(price)
        time.sleep(0.5)

        park_cursor()
        panel = read_register_panel(grab())
        require(panel["net_sales"] > 0,
                f"price did not take - net sales is still {panel['net_sales']}")

        # Net sales is the game's own price x quantity, so dividing it by the
        # price just set recovers the quantity the game actually holds. That is
        # the reliable direction: this figure is a long digit string that OCRs
        # at high confidence, while the quantity field beside it is a couple of
        # glyphs that routinely come back mangled.
        #
        # The division not being exact is itself the check -- a remainder means
        # the price on screen is not the price that was meant to be set.
        require(panel["net_sales"] % price == 0,
                f"net sales {panel['net_sales']:,} is not a whole multiple of the "
                f"{price:,} price that was set - the price did not take correctly")
        qty = panel["net_sales"] // price
        say(f"Net sales {panel['net_sales']:,} Alz = {price:,} x {qty}"
            f"  (field reads {panel['qty_text']!r})")

        # ---- step 4: Register -----------------------------------------------
        shot = grab()
        buttons = find_text(shot, "Register", REGISTER_PANEL)
        require(bool(buttons), "could not find the Register button")
        button = buttons[-1]  # lowest: below 'Register Item' and 'Register QTY'
        record("register.priced", shot, price=price, qty=panel.get("qty"),
               net_sales=panel.get("net_sales"), item=expect_item)
        say(f"Register button at {button.centre} (conf {button.conf:.0f})")

        # Register does not list anything yet: it raises a confirmation dialog.
        # Sometimes two -- pricing more than 25% under the weekly average adds
        # an extra "are you sure" step -- so confirm through the whole chain
        # rather than assuming a single dialog.
        click(*button.centre)
        shot = await_dialog("confirm", timeout)
        require(shot is not None, "no confirmation dialog appeared after Register")

        # ---- step 5: confirm through every dialog; this commits -------------
        # The balance, before the commit, so the registration FEE can be
        # measured. The game charges a percentage of the asking price to put
        # something on the market, and it charges it here rather than netting
        # it off the proceeds -- every measured sale in the ledger divides
        # exactly by its unit price, so what arrives on a sale is gross.
        #
        # It was never recorded anywhere, which made PROFIT overstate by the
        # whole fee bill -- and the bill is charged on every row of every
        # cycle, so it is the most FREQUENT outflow in the system rather than
        # a rounding error.
        alz_before_fee = None
        if not dry_run:
            try:
                alz_before_fee = get_alz(grab()) or None
            except Exception:  # noqa: BLE001 - a tally must not cost a listing
                alz_before_fee = None

        # Driven by the button finder, which polls: a single dialog_kind() read
        # can flake to None and end the chain with a dialog still up.
        for step in range(1, MAX_CONFIRM_STEPS + 1):
            # Check a dialog is actually up before hunting for its button.
            # await_dialog_button ends with a 15%-confidence sweep of a
            # 1600x800 region, most of which is 3D scenery once the dialogs
            # have closed -- junk matching "confirmation" there gets clicked
            # into the world, which walks the character away from the NPC.
            if dialog_kind(grab()) is None:
                break
            confirm = await_dialog_button(CONFIRM_WORD, timeout=4.0)
            if confirm is None:
                break  # nothing left to confirm
            say(f"{CONFIRM_WORD} {step} at {confirm.centre} "
                f"(conf {confirm.conf:.0f})")
            # Marked BEFORE the click, not after.
            #
            # `committed` answers one question for the caller: may this be
            # retried? Once a Confirmation click has been ATTEMPTED the answer
            # is no, because click() delivers the button-down and can then
            # raise -- a refused SendInput, a hook, a desktop switch -- landing
            # in the one-statement window between the click and the flag. The
            # listing exists, `committed` reads False, no register.aborted is
            # written, and the caller is handed an exception with no report at
            # all. Narrow, but it is the same shape as the cancel-side gap, and
            # both of them mislead in the dangerous direction.
            #
            # Setting it first is wrong only when the click was refused outright
            # and nothing was sent -- in which case input is blocked entirely
            # and the run is stopping anyway. Assume committed and be
            # occasionally over-cautious; never assume clean and re-list twice.
            if report is not None:
                report["committed"] = True
            click(*confirm.centre)
            committed = True
            # A new listing joins the table, so the remembered catalogue is
            # short by one and every index after it is wrong. Same reasoning as
            # the cancel path: dropped on the click being sent.
            note_shop_changed("registration committed")
            time.sleep(0.8)

        # Record what was committed BEFORE the checks below can abort. Both of
        # them fire after the listing is live, and returning a bare failure
        # made the caller skip sanity_check entirely -- leaving a live,
        # never-verified listing, which is the one thing that check exists for.
        if committed and report is not None:
            report["price"] = price
            report["qty"] = qty
            report["total"] = panel["net_sales"]
            report["committed"] = True

        if committed and not dry_run:
            # What went on the market. This is the only place that knows both
            # the quantity and the price, and sale_rejection needs it later to
            # tell a partial sale from a bad reading.
            note_registration(expect_item or "", price, qty)

            # And what it cost to put it there. Bounded before it is believed:
            # a balance that moved UP is a sale landing in the same window, and
            # a "fee" larger than the asking price is a misread, not a charge.
            try:
                alz_after_fee = get_alz(grab()) or None
            except Exception:  # noqa: BLE001
                alz_after_fee = None
            if alz_before_fee and alz_after_fee and alz_before_fee > alz_after_fee:
                fee = alz_before_fee - alz_after_fee
                asking = (price or 0) * (qty or 0)
                if asking and fee > asking:
                    say(f"  the balance fell {fee:,} during the commit, more "
                        f"than the {asking:,} asked - not booking that as a "
                        "fee.")
                else:
                    say(f"  registration fee: {fee:,} Alz")
                    # qty=0 and price=0 keep it OUT of purchase_cost_basis,
                    # which selects on `price > 0 AND qty > 0`. That is
                    # deliberate: the floor the operator asked for is "never
                    # below what the Sets cost", and fees are not part of what
                    # the goods cost. They are part of PROFIT, which sums
                    # spend over every row, so this reaches the report without
                    # moving the floor.
                    note_purchase(f"registration fee: {expect_item or 'item'}",
                                  0, fee, 0, note="Agent Shop registration fee")

        require(await_dialog(None, timeout) is not None,
                f"a confirmation dialog is still open after {MAX_CONFIRM_STEPS} steps")

        # The shop slot emptying is the evidence the listing went through.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            after = read_register_panel(grab())
            if not after["loaded"]:
                if report is not None:
                    report["price"] = price
                    report["qty"] = qty
                    report["total"] = panel["net_sales"]
                record("register.committed", row=row, col=col, price=price,
                       qty=qty, item=expect_item)
                say(f"Registered ({row},{col}) qty {qty} at {price:,} Alz "
                    f"each ({panel['net_sales']:,} total).")
                return True
            time.sleep(0.5)
        require(False, "the shop slot did not clear after Confirmation")
        return False

    except Aborted as exc:
        record("register.aborted", reason=str(exc), row=row, col=col,
               item=expect_item, committed=committed)
        say(f"ABORTED: {exc}.")
        if committed:
            say("WARNING: Confirmation was already clicked, so the listing may "
                "have gone through. It will be checked against the table.")
            return False
        if dry_run:
            say("[dry run] leaving the screen exactly as it is. Nothing was listed.")
            return False
        if dialog_kind(grab()) is not None and not close_any_dialog():
            say("WARNING: a dialog is still open - dismiss it manually.")
        if read_register_panel(grab())["loaded"]:
            say("NOTE: the item is still sitting in the shop slot; "
                "run --clear to put it back in the inventory.")
        say("Nothing was listed.")
        return False


def relist(
    row: int,
    inv_row: int | None = None,
    inv_col: int | None = None,
    dry_run: bool = False,
    timeout: float = 8.0,
    verbose: bool = True,
    attempts: int = RELIST_ATTEMPTS,
    expect: "RowRef | None" = None,
) -> str:
    """Cancel the listing on `row`, then re-list it from inventory (inv_row, inv_col).

    Cancelling returns the item to the inventory, so the second half picks it
    back up and lists it at the lowest currently listed price, subject to two
    floors:

      * per-item floors in ITEM_PRICE_FLOORS bind absolutely (VIP >= 105M), and
      * MIN_PLAUSIBLE_PRICE, below which the market read is treated as a
        misread rather than a price.

    There is deliberately NO relative floor against the previous price: the
    rule is to take the lowest current price, whatever it is. A large drop is
    reported (SUSPECT_PRICE_FRACTION) but never overridden. This docstring used
    to promise a 5% floor and a restore-the-original path, neither of which
    existed in the code -- which is worse than having no floor, because it
    invites the absolute floor to be relaxed on the strength of a backstop that
    is not there.

    Strictly sequential: if the cancel does not fully succeed, nothing is listed.

    If the row has sold (it shows Receive instead of Change), the proceeds are
    collected. If any quantity is still listed afterwards the relist restarts
    against it; if the listing has gone entirely, that is SOLD_OUT -- there is
    nothing left to relist and it is not a failure.

    Each call is a complete cycle that leaves the shop closed behind it:

        click the Agent Shop NPC -> Register tab -> Refresh
          -> cancel the row -> re-list it -> Escape

    so the next call starts from scratch rather than depending on whatever the
    previous one left on screen. Refresh matters: the client's copy of the
    table goes stale, and cancelling a stale row is how you cancel something
    that has already sold.

    Returns RELISTED, SOLD_OUT or FAILED.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    def close_shop() -> None:
        """Retire the shop session once it is old enough, else leave it open.

        This used to close unconditionally, so every row walked back to the NPC
        and reopened the Register tab: ~34s per listing, 25 of the 103 minutes
        measured on the 07:57 run of 2026-08-06. Freshness never depended on it
        -- _relist_cycle refreshes the table on every attempt, and the caller
        re-locates the listing by identity before cancelling.

        Keeping it BOUNDED rather than dropping it: a window that has been open
        for a quarter of an hour may have been closed by the game, moved to
        another tab, or wedged behind a dialog, and rebuilding from the NPC is
        the one recovery that fixes all three. Fifteen minutes of that risk
        costs one reopen; fifteen minutes of reopens costs about ten.

        Never raises. This runs in a `finally`, so an exception escaping here
        would REPLACE an in-flight FatalAbort -- the caller would then see an
        ordinary failure, retry, and re-list the very thing the FatalAbort was
        raised to stop.
        """
        if dry_run:
            return
        try:
            if not shop_session_expired():
                age = shop_session_age() or 0.0
                say(f"Leaving the Agent Shop open ({age / 60:.1f} min into a "
                    f"{SHOP_SESSION_SECONDS / 60:g} min session).")
                return
            for _ in range(ESCAPE_ATTEMPTS):
                if not trade_window_open():
                    note_shop_closed()
                    return
                press_escape()
            if trade_window_open():
                say("Note: the Trade window would not close with Escape.")
            note_shop_closed()
        except Exception as exc:  # noqa: BLE001 - must not mask the real error
            # The session clock is cleared even here. An unknown window state
            # must not be treated as a live session: the next open then starts
            # from the NPC, which is the safe direction.
            note_shop_closed()
            say(f"Note: could not close the Trade window ({exc}).")

    try:
        return _relist_cycle(row, inv_row, inv_col, dry_run, timeout,
                             verbose, attempts, say, expect)
    finally:
        close_shop()


def _relist_cycle(row, inv_row, inv_col, dry_run, timeout, verbose, attempts, say,
                  expect=None):
    """The body of relist(); relist() wraps this to always close the shop."""
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            say(f"\n=== relist attempt {attempt}/{attempts} ===")

        # Step 1 of the cycle: the shop, from the NPC. Then a fresh table --
        # the client's copy goes stale, and acting on a stale row is how you
        # cancel something that has already sold.
        if not dry_run:
            require_focus = focus_game()
            if not require_focus:
                say("Could not bring Cabal to the foreground.")
                return FAILED
            park_cursor()
            if not open_trade_window(verbose=verbose):
                say("Could not open the Agent Shop on the Register tab.")
                return FAILED
            if attempt == 1 and not ensure_work_tab_empty(timeout=timeout,
                                                          verbose=verbose):
                say("Aborting: the working inventory tab must be empty to start.")
                return FAILED
            if not refresh_table(timeout=max(timeout, 20.0), verbose=verbose):
                say("Could not refresh the table - stopping.")
                return FAILED
            park_cursor()

        # Read the price BEFORE cancelling; once cancelled the row is gone.
        rows = await_rows(timeout)
        if not 1 <= row <= len(rows):
            say(f"Row {row} is out of range; {len(rows)} row(s) visible.")
            return FAILED

        target = rows[row - 1]

        # This function reopened the shop and forced a server refresh since the
        # caller picked this row, so the number it was given may now point
        # somewhere else. Everything below -- the price floor, the expected
        # quantity, what sanity_check will look for -- is taken from `target`,
        # so a shift here means relisting one item under another's identity.
        if expect is not None:
            resolved, note = locate_row(rows, expect)
            if resolved is None:
                say(f"{expect.name!r} is no longer in the table "
                    f"({note or 'gone'}) - nothing was cancelled.")
                return FAILED
            if resolved.index != row:
                say(f"{expect.name!r} moved from row {row} to "
                    f"{resolved.index} since it was chosen - following it.")
                row = resolved.index
            target = resolved

        # The table as it stood when this row was chosen, with the choice
        # itself in the index. Without it a later frame shows what happened
        # but not which listing it was supposed to be happening to.
        # The whole table, not just the chosen row. read_rows read all ten, so
        # recording all ten costs one JSON line and gives the tests ten
        # ground-truth comparisons per frame instead of one -- and the nine
        # extra ones cover the rows a relist never targets, which are exactly
        # the rows no other check looks at.
        record("table.target", row=row, name=target.name, action=target.action,
               price=target.price, qty=target.qty, visible=len(rows),
               attempt=attempt,
               table=[[r.index, r.name, r.action, r.price, r.qty] for r in rows])

        # A sold listing shows Receive. Collect the proceeds, then see whether
        # any quantity is still on sale: if so relist that, if not the listing
        # is done. Collecting renumbers the table, so re-locate by name.
        if target.action == "receive":
            say(f"Row {row} is sold ({target.name!r}) - clicking Receive.")
            if dry_run:
                say("[dry run] would click Receive, then relist any remainder")
                return RELISTED
            if attempt == attempts:
                say(f"Still sold on the final attempt ({attempts}) - stopping.")
                return FAILED

            # The balance BEFORE the credit lands. get_alz returns 0 rather
            # than raising when the Inventory panel is closed or the digits do
            # not read, so 0 means "unknown" here and never "broke" -- treating
            # it as a real balance would invent a sale worth the whole purse.
            try:
                alz_before = get_alz(grab()) or None
            except Exception:  # noqa: BLE001 - a tally must not cost a listing
                alz_before = None

            click(*target.change)

            # Collecting raises "Confirm Receipt", whose accept button reads
            # Receive. Leaving it open would also cover the table and corrupt
            # the row read that follows.
            accept = await_dialog_button(RECEIPT_WORD, timeout=6.0)
            if accept is None:
                say("The Confirm Receipt dialog did not appear - stopping.")
                return FAILED
            say(f"Confirm Receipt: accepting at {accept.centre} "
                f"(conf {accept.conf:.0f})")
            click(*accept.centre)
            time.sleep(1.0)
            if await_dialog(None, timeout) is None:
                say("The Confirm Receipt dialog stayed open - stopping.")
                return FAILED

            say(f"Waiting {RECEIVE_WAIT:g}s for the sale to settle...")
            time.sleep(RECEIVE_WAIT)

            # Read the credit before the table work below: this is the only
            # window in which the delta is attributable to THIS sale and
            # nothing else. A registration fee later in the same relist would
            # otherwise be netted off it.
            proceeds = None
            reject = ""
            try:
                alz_after = get_alz(grab()) or None
                if alz_before and alz_after and alz_after > alz_before:
                    proceeds = alz_after - alz_before
            except Exception:  # noqa: BLE001 - a tally must not cost a listing
                proceeds = None

            # A sale cannot be worth more than the listing was. The row on
            # screen carries both numbers, so the ceiling is known exactly.
            #
            # Without it the report printed, from a live run on 2026-08-06:
            #
            #   Yekaterina VIP Membership   1 sale       -   1,662,294,744
            #   Epic Booster (Highest)      1 sale      16     876,764,416
            #   TOTAL                                        2,539,059,160
            #
            # The VIP sells for about 106,000,000 and the Booster stack held
            # EIGHT at 54,797,776 -- yet 876,764,416 divided by that price
            # exactly, so the report confidently claimed sixteen units from a
            # stack of eight. Both came from get_alz reading the shop's own
            # "...has been sold for N" overlay instead of the balance, which it
            # prints at the very moment of a sale. That root cause is fixed in
            # _isolate_digits; this is the second line of defence, because a
            # tally that cannot be trusted is worse than no tally.
            #
            # Rejected rather than clamped: the number is evidence the reading
            # was wrong, not evidence of a smaller sale. The sale is still
            # counted, and the report already says how many went unmeasured.
            # The ceiling is built from what was REGISTERED, not from the
            # quantity still on sale. target.qty is the remainder -- the
            # comment further down says so -- so using it made the ceiling
            # `leftovers x price`, which is unrelated to the credit that just
            # landed. Every partial sale failed it. Measured on the live
            # ledger before this was fixed: 3 of 18 sales rejected, worth
            # 129,813,000 Alz, and the run's PROFIT line showed a loss because
            # of it. A fully-sold listing passed, because then the remainder
            # IS the whole stack -- which is why it went unnoticed.
            listed_units = registered_qty(target.name, target.price)
            reject = sale_rejection(proceeds, target.price, target.qty,
                                    listed_units)
            if reject:
                say(f"  the Alz balance moved {proceeds:,}, which cannot be "
                    f"right for this listing: {reject}. Counting the sale but "
                    "not the figure.")
                record("sale.implausible", item=target.name,
                       proceeds=proceeds, why=reject,
                       listed_units=listed_units, still_listed=target.qty)
                proceeds = None
            note_sale(target.name, target.price, proceeds, reject)
            if proceeds:
                sold = (proceeds // target.price
                        if target.price and proceeds % target.price == 0
                        else None)
                say(f"Collected {proceeds:,} Alz"
                    + (f" ({sold:,} x {target.price:,})" if sold else ""))
            else:
                say("Collected (the Alz balance did not read, so this sale is "
                    "counted but not measured).")

            if not wait_for_table(max(timeout, 20.0)):
                say("The table did not finish refreshing after Receive - stopping.")
                return FAILED

            # What happened is decided by COUNTING the rows that look like this
            # one, not by identifying which row is which.
            #
            # Identity cannot answer this question. Two stacks may be identical
            # in name, quantity AND price -- routine on this shop, which
            # regularly holds two Force Core(High) at the same price -- and
            # after collecting one, the survivor matches the ref perfectly.
            # Asking "is my listing still there?" then gets a confident yes
            # about a DIFFERENT stack:
            #
            #   two identical stacks   -> exactly one match survives, read as
            #                             "the click did not take", so the
            #                             sibling is collected too. Two stacks
            #                             pulled off the market for one sale.
            #   three identical stacks -> two survive, locate_row says
            #                             'ambiguous', the run stops with the
            #                             collected item stranded in the work
            #                             tab, and every later cycle then fails
            #                             its empty-tab precondition.
            #
            # Both live runs on 2026-08-04 ended this way. Counting is immune:
            # collecting a stack removes exactly one row from the family
            # whether or not its siblings are distinguishable, and it still
            # works when the QTY column is unreadable, which is precisely when
            # identity matching is weakest.
            def family(table: list[Row]) -> list[Row]:
                return listing_family(table, target.name, target.price)

            def quantities(pool: list[Row]) -> list:
                return family_quantities(pool)

            before = quantities(family(rows))

            # Make the client REFETCH before counting. wait_for_table above
            # only waits for a reload to finish; it does not cause one, so the
            # poll below was reading the client's stale copy -- which still
            # shows the pre-sale quantity however long it is polled.
            #
            # Measured on the 08:27 run: 16 collects across rows 2-8 polled the
            # full budget, concluded "the click did not take", and retried. On
            # the retry -- which reopens the shop, and therefore refreshes --
            # the row read as [change] with the collected quantity already
            # gone. The collect had worked every time. That is ~45s of polling
            # plus a whole wasted attempt per sale, and it made a working
            # collect look like a failing one in the log.
            refresh_table(timeout=timeout, verbose=False)

            # "Receive" means there are proceeds waiting to be collected. It
            # does NOT mean the listing sold out, and the quantity column shows
            # what is STILL on sale -- which collecting does not change. Only
            # the action flips, receive -> change.
            #
            # So a partial sale leaves the family's quantities byte-identical,
            # collect_delta returns (lost=[], gained=[]), and the multiset test
            # below reads a collect that WORKED as "the click did not take".
            # Measured over the 07:57 run of 2026-08-06: 10 of 10 recorded
            # retries went receive -> change with the quantity identical either
            # side. The multiset never had the answer -- the action did, in the
            # same frame, unread.
            #
            # Checked at the same index rather than by name alone: with two
            # identical stacks a fully-sold row vanishes and its sibling shifts
            # up into this slot, and that sibling matches on every field. The
            # multiset test catches that case first (lost=[q]) and this one is
            # consulted only when nothing moved, so the shift cannot reach here
            # -- but the fields are compared anyway, because "nothing moved" is
            # exactly the reading a stale frame produces too.
            def collected(table: list[Row]) -> Row | None:
                if not 1 <= row <= len(table):
                    return None
                here = table[row - 1]
                if here.action != "change":
                    return None
                if (_canonical(here.name) != _canonical(target.name)
                        or here.price != target.price
                        or here.qty != target.qty):
                    return None
                return here

            after: list = []
            after_rows: list[Row] = []
            after_table: list[Row] = []
            saw_table = False
            deadline = time.monotonic() + max(timeout, TABLE_READ_BUDGET)
            while time.monotonic() < deadline:
                rows_now = read_rows(grab())
                if rows_now:
                    saw_table = True
                    after_table = rows_now
                    after_rows = family(rows_now)
                    after = quantities(after_rows)
                    # Either signal ends the wait. Without the second one a
                    # partial sale polls the entire budget for a change in the
                    # quantities that cannot happen, ~40s per collect, and then
                    # concludes the click failed.
                    if after != before or collected(rows_now) is not None:
                        break
                time.sleep(0.8)

            if not saw_table:
                say("The table could not be read while checking for a "
                    "remainder - stopping rather than assuming it sold out.")
                return FAILED

            # Multiset difference: what left the family, and what appeared.
            lost, gained = collect_delta(before, after)

            if not lost and not gained:
                # The quantities agreeing is the EXPECTED reading for a partial
                # sale, not evidence of anything. Ask the action.
                settled = collected(after_table)
                if settled is not None:
                    say(f"Collected. Row {row} went from Receive to Change "
                        f"with {settled.qty} still listed - relisting the "
                        f"remainder.")
                    continue

                # Says what was MEASURED. The old wording claimed the row
                # "still shows Receive", which this code never checked -- and
                # it was wrong every time it printed during the 08:27 run,
                # where the collect had in fact gone through and only the
                # client's copy of the table was stale.
                priced = (f"at {target.price:,} " if target.price is not None
                          else "")
                shows = (after_table[row - 1].action
                         if 1 <= row <= len(after_table) else "nothing")
                say(f"The {target.name!r} listings {priced}are unchanged "
                    f"after collecting ({before}) and row {row} still shows "
                    f"{shows!r} - the click did not take; retrying.")
                continue

            if len(lost) == 1 and not gained:
                say(f"{target.name!r} is no longer in the table - fully sold "
                    "and collected.")
                return SOLD_OUT

            if len(lost) == 1 and len(gained) == 1:
                # A partial sale: the stack shrank rather than vanishing. The
                # remainder is the row carrying the quantity that appeared.
                candidates = [r for r in after_rows if r.qty == gained[0]]
                if len(candidates) != 1:
                    say(f"A remainder of {gained[0]} appeared but "
                        f"{len(candidates)} rows carry it, so which one is the "
                        "remainder cannot be told - it will be picked up next "
                        "cycle rather than relisting the wrong stack.")
                    return FAILED
                row = candidates[0].index
                say(f"Partially sold: {lost[0]} -> {gained[0]} at row {row} "
                    "- relisting the remainder.")
                continue

            # Anything else means the family changed in a way one collect does
            # not explain -- another stack selling during the same few seconds,
            # most likely. Saying so beats guessing.
            say(f"The {target.name!r} listings changed in a way this collect "
                f"does not explain (was {before}, now {after}) - leaving them "
                "for the next cycle rather than acting on a table that moved "
                "underneath us.")
            return FAILED

        if target.action != "change":
            say(f"Row {row} shows '{target.action}', not 'Change' - nothing to relist.")
            return FAILED

        original = target.price
        if original is None:
            say(f"Could not read the current price of row {row} ({target.name!r}); "
                "refusing to relist without a price to sanity-check against.")
            return FAILED
        # A truncated read is worse than no read: every guard is derived from
        # this number, and `if original` / `if price_floor` short-circuit to
        # nothing when it is small, so a clipped 1,000,000,000 read as 0 would
        # disable the 5% floor, the absolute floor and the suspect check at once.
        if original < MIN_PLAUSIBLE_PRICE:
            say(f"Row {row} ({target.name!r}) priced at {original:,} Alz, below "
                f"the {MIN_PLAUSIBLE_PRICE:,} plausibility floor - the price "
                "column was probably misread. Refusing to relist it.")
            return FAILED

        # The NAME deserves the same treatment as the price, because the price
        # floor is derived from it. An unreadable name reads as "(empty)",
        # whose floor is 0 -- so a VIP whose name column blanked for one frame
        # would relist at whatever the market said, with nothing to stop it.
        if target.name == "(empty)" or len(_floor_key(item_name(target.name))) < 6:
            say(f"Row {row}'s name did not read ({target.name!r}). The price "
                "floor is looked up from the name, so relisting without one "
                "could list a floored item unprotected. Refusing.")
            return FAILED

        max_qty = wants_max_quantity(target.name)
        say(f"[relist 1/2] row {row}: {target.name!r} at {original:,} Alz")
        say("             will relist at the lowest current market price")
        if max_qty:
            say("             quantity will be maximised")

        # Snapshot the inventory so the item can be followed to wherever the
        # cancel drops it, instead of assuming a slot.
        before = origin = start_tab = None
        if not dry_run and (inv_row is None or inv_col is None):
            focus_game()
            park_cursor()
            before = grab()
            # Resolve the grid anchor now, while the panel is known-visible.
            origin = inventory_origin(before) or inventory_origin()
            if origin is None:
                say("The Inventory panel is not visible, so the returned item "
                    "could not be followed. Open it (or pass an explicit slot) "
                    "and rerun. Nothing has been cancelled yet.")
                return FAILED
            # Remember the tab so we can come back to it: cancelling a large
            # stack scatters items across tabs, and the game may switch away.
            start_tab = active_inventory_tab(before, origin)
            if start_tab is None:
                say("Could not tell which inventory tab is open, so the "
                    "returned items could not be followed reliably. "
                    "Nothing has been cancelled yet.")
                return FAILED
            # It must be the work tab. require_empty_work_tab verified tab
            # WORK_TAB was empty, but the diff is taken against whatever tab
            # happens to be showing -- so if they differ, "empty" was checked
            # on one tab and "what came back" measured on another, and every
            # item already sitting on the visible tab reads as newly returned.
            if start_tab != WORK_TAB:
                say(f"Inventory tab {start_tab} is open, but the work tab is "
                    f"{WORK_TAB} - the emptiness check and the returned-item "
                    "diff would be looking at different tabs. Nothing has been "
                    "cancelled yet.")
                return FAILED
            record("inventory.before_cancel", tab=start_tab, origin=str(origin))
            say(f"Inventory tab {start_tab} is open; will return to it after "
                "cancelling.")

        # Hand the identity down, not just the number: cancel_item re-reads the
        # table and would otherwise cancel whatever now sits at this index.
        cancel_report: dict = {}
        if not cancel_item(row, dry_run=dry_run, timeout=timeout, verbose=verbose,
                           expect=RowRef.of(target, rows),
                           report=cancel_report):
            if game_disconnected():
                say("The game has DISCONNECTED - the client is showing 'You "
                    "have been disconnected from the server'. Nothing below "
                    "is a script fault; log back in and start again.")
                return FAILED

            # A cancel that never COMMITTED is safe to try again: the Change
            # click produced no dialog, nothing was confirmed, and the listing
            # is still on the market exactly as it was.
            #
            # This used to be indistinguishable from "committed but could not
            # be verified", so every failure was treated as the dangerous one
            # and the row was abandoned. Measured on 2026-08-08: two rows were
            # skipped for aborts that recorded committed=False -- both of them
            # a missed dialog, both of them retryable.
            #
            # Retrying a COMMITTED cancel is what must never happen: it would
            # withdraw a SECOND listing. So the retry is gated on the positive
            # evidence of not-committed, never on the absence of evidence -- an
            # empty report means the old behaviour.
            if cancel_report.get("committed") is False and attempt < attempts:
                say("The cancel did not commit - nothing was withdrawn and the "
                    "listing is untouched, so this row can be tried again.")
                continue

            # Deliberately not "nothing will be listed": cancel_item returns
            # False both when nothing happened AND when it committed but could
            # not verify, and it has just printed which. Asserting the stronger
            # claim here contradicted its own warning two lines earlier.
            say("Cancel did not complete - see above for what state it left. "
                "Nothing further will be listed this cycle.")
            return FAILED

        if not dry_run and not wait_for_table(max(timeout, 20.0)):
            # The cancel committed one statement earlier, so this is a
            # strand too. The comment on the sibling exit below named this
            # very line as still missing its warning; here it is.
            record("relist.stranded", stage="table_refresh", row=row,
                   item=target.name, qty=target.qty, tab=start_tab)
            say("The table did not finish refreshing after the cancel.")
            say(f"IMPORTANT: row {row} was already cancelled, so "
                f"{target.name!r} x{target.qty} is in inventory tab "
                f"{start_tab}, UNLISTED. Later cycles will fail their "
                "empty-work-tab check until it is cleared.")
            return FAILED

        slot = (inv_row, inv_col) if inv_row and inv_col else None
        if slot is None:
            if dry_run:
                say("[dry run] would locate the returned item by diffing the inventory")
                slot = (1, 1)
            else:
                # The game can switch tabs when a big stack comes back; go back
                # to the one the diff's "before" frame was taken on, or the
                # comparison is meaningless.
                if not select_inventory_tab(start_tab, origin):
                    say(f"Could not return to inventory tab {start_tab}.\n"
                        f"IMPORTANT: row {row} has already been cancelled - "
                        f"{target.name!r} is in your inventory, unlisted.")
                    return FAILED
                park_cursor()

                # The single most useful frame in a failed cycle: it shows
                # where the item actually came back to. Captured once and
                # reused for the diff, so recording costs nothing.
                after = grab()
                record("inventory.after_cancel", after, tab=start_tab)
                returned = changed_slots(before, after, origin)
                if not returned:
                    record("inventory.diff_empty", after, tab=start_tab)
                    say(f"Could not tell which slot on tab {start_tab} "
                        f"{target.name!r} returned to, so refusing to list an "
                        "unidentified item.\n"
                        f"IMPORTANT: row {row} has already been cancelled - "
                        f"{target.name!r} is sitting in your inventory, unlisted.\n"
                        "Re-list it with: --register INV_ROW INV_COL")
                    return FAILED
                where = ", ".join(f"{r},{c}" for r, c in returned)
                record("inventory.returned", after, tab=start_tab, slots=where,
                       count=len(returned), taking=f"{returned[0]}")
                say(f"{target.name!r} returned to {len(returned)} slot(s) on "
                    f"tab {start_tab}: {where}")
                slot = returned[0]

        say(f"\n[relist 2/2] listing inventory slot ({slot[0]},{slot[1]})")
        report: dict = {}
        # floor_price is passed for reporting only -- it is the price the
        # listing had before, used to note a large drop, never to override one.
        listed = register_item(*slot, dry_run=dry_run,
                               timeout=timeout, verbose=verbose,
                               floor_price=original, maximise_qty=max_qty,
                               expect_item=target.name, expect_qty=target.qty,
                               report=report)
        if not listed and not report.get("committed"):
            # THE path that cost five hours. The cancel committed ~60 lines
            # above, so the item is out of the shop and sitting in the work
            # tab -- and this returned FAILED without a word. register_item
            # printed its own abort, but nothing said the consequence: every
            # later cycle now fails require_empty_work_tab identically until a
            # human clears it, which is exactly what the failure breaker is
            # for and exactly why it fired three cycles later.
            #
            # Two of the six post-commit exits already say this. This one and
            # the table-refresh exit below did not.
            record("relist.stranded", stage="register_failed", row=row,
                   item=target.name, qty=target.qty, tab=start_tab)
            say(f"\nIMPORTANT: row {row} was cancelled, so {target.name!r} "
                f"x{target.qty} is now in inventory tab {start_tab}, UNLISTED.")
            say("Every later cycle will fail its empty-work-tab check until "
                "that is cleared, so the run will stop after "
                f"{MAX_CONSECUTIVE_FAILURES} of them.")
            say(f"Re-list it by hand with:  --register INV_ROW INV_COL")
            return FAILED
        if dry_run:
            return RELISTED
        if not listed:
            # The listing IS live -- register_item committed and then failed a
            # post-commit check (a dialog slow to close, the shop slot slow to
            # clear). Returning here skipped sanity_check and left a live,
            # unverified listing on the market, so fall through and check it.
            say("The listing was committed before the failure, so it is on the "
                "market. Verifying it against the table rather than assuming.")

        # Verify the outcome, not just the panel we filled in. A mismatch means
        # something was listed that should not have been: pull it back off the
        # shop and stop everything rather than leaving it on the market.
        found: dict = {}
        if not sanity_check(target.name, report.get("price"), report.get("qty"),
                            timeout=timeout, verbose=verbose, found=found):
            bad = found.get("row")
            if bad is None:
                # Could not verify (table unreadable, refresh failed). That is
                # not evidence of a wrong listing, so it must not kill the run:
                # retry next cycle rather than raising.
                say(f"Could not verify the listing for {target.name!r}. "
                    "Nothing was withdrawn; will be checked again next cycle.")
                return FAILED
            # Format defensively: these strings are the only account of what
            # went wrong, and a None slipping into a ',' format raises
            # TypeError *instead of* the FatalAbort -- which escapes run_loop's
            # handler entirely and kills the process after the withdrawal has
            # already committed.
            def money(value: int | None) -> str:
                return f"{value:,}" if isinstance(value, int) else "an unreadable price"

            say(f"Withdrawing the mismatched listing on row {bad.index} "
                f"({bad.name!r})...")
            # The withdrawal must not be able to swallow the FatalAbort. It is
            # called before the raise, outside any handler, and it runs OCR and
            # input -- so an OSError or a refused click there meant the abort
            # was never constructed, the wrong listing stayed on the market,
            # and the unattended loop carried on.
            try:
                withdrawn = cancel_item(bad.index, expect=RowRef.of(bad, [bad]),
                                        timeout=timeout, verbose=verbose)
            except FatalAbort:
                raise
            except Exception as exc:  # noqa: BLE001 - must still report
                raise FatalAbort(
                    f"listed {bad.name!r} at {money(bad.price)}, which does not "
                    f"match what was registered, AND the withdrawal itself "
                    f"failed ({type(exc).__name__}: {exc}). It is still on the "
                    "shop - remove it by hand."
                ) from exc
            if withdrawn:
                raise FatalAbort(
                    f"listed {bad.name!r} at {money(bad.price)} which does not "
                    f"match the {money(report.get('price'))} that was "
                    "registered. It has been withdrawn from the shop and the "
                    "run stopped."
                )
            raise FatalAbort(
                f"listed {bad.name!r} at {money(bad.price)}, which does not "
                f"match what was registered, AND it could not be withdrawn. It "
                "is still on the shop - remove it by hand."
            )
        return RELISTED

    say(f"Gave up after {attempts} attempts - the listing kept coming back sold.")
    return FAILED


ALL_ROWS_SPEC = "all"


def wants_all_rows(specs: list[str] | None) -> bool:
    """Is this spec the literal 'all' rather than a set of row numbers?

    Separate from parse_row_spec because 'all' is not a row list that happens
    to be long: the count is not known until the shop has been swept, and a
    number written down now would be wrong the moment a listing sells.
    """
    return bool(specs) and len(specs) == 1 and \
        str(specs[0]).strip().casefold() == ALL_ROWS_SPEC


def parse_row_spec(specs: list[str]) -> list[int]:
    """Turn CLI row specs into row numbers: '1-10', '1,3,5' and '1 3 5' all work."""
    if wants_all_rows(specs):
        return []
    rows: list[int] = []
    for spec in specs:
        for chunk in str(spec).replace(",", " ").split():
            if "-" in chunk[1:]:
                lo, _, hi = chunk.partition("-")
                start, stop = int(lo), int(hi)
                if start > stop:
                    raise ValueError(f"range {chunk!r} runs backwards")
                rows.extend(range(start, stop + 1))
            else:
                rows.append(int(chunk))
    seen: set[int] = set()
    return [r for r in rows if not (r in seen or seen.add(r))]


def sanity_check(
    name: str,
    price: int | None,
    qty: int | None,
    timeout: float = 8.0,
    verbose: bool = True,
    found: dict | None = None,
) -> bool:
    """After relisting, confirm the table really holds what we meant to list.

    Everything up to this point verifies the *panel* -- the price selected, the
    quantity typed, the shop slot emptying. This checks the outcome instead:
    that a row now exists for `name`, priced at `price` for `qty`. It is the
    only step that would catch the whole sequence having acted on the wrong
    item or the wrong figure.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    record("sanity.start", name=name, price=price, qty=qty)
    if not refresh_table(timeout=max(timeout, 20.0), verbose=verbose):
        say("  could not refresh the table to check the listing.")
        return False
    park_cursor()

    rows = await_rows(timeout)
    if not rows:
        say("  the table could not be read.")
        return False

    # Identify by what was just registered, not by name alone: another stack of
    # the same item may well be listed too. `strict` stops it guessing between
    # them -- the failure path here withdraws a listing, so an unresolved
    # duplicate has to read as "cannot verify", never as "wrong item".
    ref = RowRef(name, qty, price)
    changeable = [r for r in rows if r.action == "change"]

    # Several rows all matching what was registered is not ambiguity, it is
    # confirmation: whichever one is ours, a row carrying this name at this
    # price and quantity exists, which is the whole question. Asking
    # locate_row first returned 'ambiguous' for two identical stacks and
    # stalled the batch on its first row, every cycle, with nothing wrong.
    witnesses = [r for r in changeable
                 if _canonical(r.name) == _canonical(name)
                 and (price is None or r.price == price)
                 and (qty is None or r.qty is None or r.qty == qty)]
    if witnesses:
        say(f"  row {witnesses[0].index} matches what was registered"
            + (f" ({len(witnesses)} identical rows)" if len(witnesses) > 1 else "")
            + ".")
        return True

    listed, note = locate_row(changeable, ref, strict=True)
    if listed is None:
        if note == "ambiguous":
            say(f"  several rows are named {name!r} and none carries the price "
                "and quantity just registered, so the new listing cannot be "
                "identified. Not withdrawing anything.")
            return False
        # 'missing' is not "cannot verify" -- it is the strongest evidence the
        # script has that the WRONG ITEM was listed: no row carries the name we
        # believe we just registered. Treating it as unverifiable left a
        # wrongly-listed item on the market and returned a retryable failure,
        # which with the tooltip check removed meant nothing detected it at all.
        #
        # So look for the row holding the figures we registered, whatever it is
        # called, and hand it back to be withdrawn.
        # Report it; do NOT withdraw anything.
        #
        # An earlier version cancelled the row carrying the registered price
        # and quantity, on the theory that a name that is not there means the
        # wrong item was listed. That is one explanation of 'missing'; the
        # others are that our own row's name flaked on this single frame, that
        # the new listing sold before the check, or that nothing listed at all.
        # match_rows deliberately rejects a single substituted character, so
        # ONE bad glyph reaches here -- and the row it then identified as the
        # "suspect" was our own correct listing, whose price and quantity read
        # fine. Cancelling a 119M listing over one character, off one frame, is
        # far worse than leaving a wrong listing up and saying so loudly.
        say(f"  {name!r} is not in the table after relisting it.")
        suspect = [r for r in changeable if price is not None and r.price == price]
        if suspect:
            say("  these rows carry the price just registered: "
                + ", ".join(f"row {r.index} ({r.name!r})" for r in suspect[:4]))
            say("  that is most likely this listing with a misread name. "
                "Nothing has been withdrawn.")
        else:
            say("  nothing carries that price either - the listing may have "
                "sold, or may never have been created.")
        say("  CHECK THE SHOP BY HAND before the next run: if the wrong item "
            "was listed, it is still on the market.")
        return False
    say(f"  row {listed.index} names {listed.name!r} - matches what was relisted.")

    # An unreadable figure is "cannot verify", not "mismatch". Treating it as a
    # mismatch both condemned a correct listing and crashed formatting None.
    if price is not None and listed.price is None:
        say("  the listed price could not be read, so this cannot be verified.")
        return False
    if qty is not None and listed.qty is None:
        say("  the listed quantity could not be read; checking the price only.")

    # Only a PRICE mismatch may lead to withdrawing a listing.
    #
    # The quantity on both sides of this comparison is unreliable: the expected
    # value is derived from net sales, and the observed one comes from the QTY
    # column, the worst OCR target in the table. A quantity difference also
    # cannot cost anything -- the game clamps entry to the stack, so listing
    # too many is impossible -- whereas acting on one destroys a good listing.
    # A stack of 250 reading as '25O' -> 25 is enough to trigger it, and
    # re-reading does not help because the misread is systematic, not a flake.
    if qty is not None and listed.qty is not None and listed.qty != qty:
        say(f"  note: quantity reads {listed.qty}, expected {qty} - not acting "
            "on that; the quantity column is not reliable enough to withdraw on.")

    problems = []
    if price is not None and listed.price is not None and listed.price != price:
        problems.append(f"price is {listed.price:,}, expected {price:,}")

    if problems:
        # Confirm before acting: the consequence is cancelling a listing, and
        # one flaked digit would destroy a correct one. Every other guard in
        # this file polls; this one used a single frame.
        say(f"  possible mismatch on row {listed.index}: " + "; ".join(problems))
        say("  re-reading to confirm before withdrawing anything...")
        time.sleep(1.0)
        recheck = await_rows(timeout)
        again, _ = locate_row([r for r in recheck if r.action == "change"],
                              ref, strict=True)
        if again is None:
            say("  the row could not be identified on the re-read; "
                "not withdrawing.")
            return False
        # Unreadable is not evidence of anything. The first read is guarded
        # this way already; the re-read was not, so a price cell that came back
        # empty fell through to "MISMATCH confirmed" and withdrew a correct
        # listing -- then crashed formatting None, destroying the FatalAbort
        # that was supposed to carry the story out.
        if again.price is None:
            say("  the price could not be read on the re-read; not withdrawing.")
            return False
        if again.price < MIN_PLAUSIBLE_PRICE:
            say(f"  the re-read price {again.price:,} is below the "
                f"{MIN_PLAUSIBLE_PRICE:,} plausibility floor, so it is a "
                "misread rather than a mismatch; not withdrawing.")
            return False
        if again.price == price:
            say(f"  second read agrees with what was registered "
                f"({again.price:,}) - the first read was a misread.")
            return True
        say(f"  MISMATCH confirmed on row {again.index} ({again.name!r}): "
            f"price {again.price:,}, quantity {again.qty}")
        if found is not None:
            found["row"] = again
        return False

    say(f"  row {listed.index}: {listed.name!r} at {listed.price:,} Alz"
        + (f" x{listed.qty}" if listed.qty is not None else "") + " - matches.")
    return True


def ensure_shop_ready(verbose: bool = True) -> bool:
    """Focus the game and get the Agent Shop open on the Register tab.

    Needed before any table read: each relist() closes the shop behind it, so
    a caller that reads the table between relists starts from a closed window.
    """
    if not focus_game():
        if verbose:
            print("Could not bring Cabal to the foreground.")
        return False
    park_cursor()
    return open_trade_window(verbose=verbose)


def relist_rows(
    rows: list[int],
    dry_run: bool = False,
    timeout: float = 8.0,
    verbose: bool = True,
    all_rows: bool = False,
) -> bool:
    """Relist several rows, tracking each by name rather than by position.

    Cancelling a listing empties its row, and registering fills the first empty
    row -- which is not always the one the item came from. Row numbers therefore
    shift during a batch, so each item is re-located by name immediately before
    it is relisted. Empty rows are skipped; any real failure stops the batch.

    Rows beyond the visible ten are supported: the shop holds thirty, and a
    sale sitting at row 25 was previously never collected because the loop
    could not see it. Asking for one enumerates the shop once, then scrolls
    each listing into view by identity before acting on it.

    A batch that asks only for rows 1-10 takes none of that: no enumeration, no
    scrolling, and the same single table read it always did. Scrolling costs a
    table read per chunk, and the common case should not pay for the rare one.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    # Open the shop before reading anything: relist() closes it after each row.
    if not dry_run:
        if not ensure_shop_ready(verbose=verbose):
            say("Could not open the Agent Shop to read the listings.")
            return False
        if not ensure_work_tab_empty(timeout=timeout, verbose=verbose):
            say("Aborting: the working inventory tab must be empty to start.")
            return False

        # Resupply FIRST, so anything it lists is in the table this batch then
        # reads -- priced with everything else instead of once at creation and
        # never again.
        #
        # It leaves the game in its default state on the way out and reopens
        # what it needs, so the shop has to be re-opened afterwards.
        if RESTOCK_BEFORE_RELIST:
            restock_pass(timeout=timeout, verbose=verbose)
            # Two attempts, because the resupply is OPPORTUNISTIC and must not
            # be able to fail the batch it runs before.
            #
            # That property was explicit while restocking ran last -- "a market
            # that will not sell must not turn a successful relist batch into a
            # failed one and trip the run's failure breaker" -- and moving it
            # first quietly lost it: one unreachable NPC and the whole cycle
            # failed with 270 bought Sets unlisted.
            if not ensure_shop_ready(verbose=verbose):
                say("The Agent Shop did not reopen after the resupply; "
                    "closing anything left over and trying once more.")
                try:
                    close_npc_shop(verbose=verbose)
                except Exception:  # noqa: BLE001
                    pass
                if not ensure_shop_ready(verbose=verbose):
                    say("Still could not reopen the Agent Shop - nothing can "
                        "be relisted without it.")
                    return False
            if not ensure_work_tab_empty(timeout=timeout, verbose=verbose):
                say("Aborting: the resupply left something in the work tab.")
                return False

    snapshot = await_rows(timeout)
    if not snapshot:
        say("No listings visible - is the Trade window open on the Register tab?")
        return False

    # Rows past the first screen need the whole shop enumerated first, because
    # their identity has to be taken from the full list: a RowRef built against
    # ten visible rows counts its ordinal in the wrong pool.
    #
    # Rows 1-10 keep the cheap path exactly as it was -- one table read, no
    # scrolling, no enumeration. Scrolling costs a table read per chunk (~18s
    # of OCR each), so making every batch pay for it would slow the common case
    # by minutes to serve a case it does not have.
    # Widen for anything this run's restocks added to the shop. Those listings
    # go to the LOWEST EMPTY row, so the first one usually lands back in range
    # -- but a restock makes up to five, and the rest go to the end. Without
    # this they are created once and never repriced again.
    #
    # Clamped to SHOP_ROW_CAPACITY rather than to the ten on screen: the widened
    # rows are past the first screen by definition, and the scrolling branch
    # below is what addresses them. An unreachable row would fail the batch, so
    # widening can only ever propose rows the shop could actually hold.
    added_rows: set[int] = set()
    if not all_rows and BUY_ADDED_ROWS:
        asked = set(rows or [])
        rows, widened = widen_for_restocks(rows, SHOP_ROW_CAPACITY)
        added_rows = set(rows) - asked
        if widened:
            say(f"Widening this sweep by {widened} row(s) to cover listings "
                f"the restocks added: now rows {min(rows)}-{max(rows)}.")

    beyond = [i for i in rows if i > len(snapshot)]
    scrolling = bool(beyond) or all_rows
    targets: list[tuple[int, RowRef, str]] = []

    if scrolling:
        if all_rows:
            # Every listing, however many there are. The shop holds thirty and
            # the table shows ten, so this sweeps the whole list by scrolling
            # and remembers what it saw, rather than being told a row count
            # that would be wrong the moment a listing sells.
            say("Relisting EVERY listing in the shop; sweeping it to see how "
                "many there are.")
        else:
            say(f"Row(s) {', '.join(str(i) for i in beyond)} are past the "
                f"first screen of {len(snapshot)}; enumerating the whole shop.")
        # The one place a remembered sweep is accepted. With
        # RESTOCK_BEFORE_RELIST the restock has just swept this same shop,
        # seconds ago, silently -- it calls whole_shop_listings(verbose=False),
        # which is why cycle 1 of the 14:02 run paid for two full traversals
        # and only printed one. When the restock bought nothing, the second
        # walk returns exactly what the first did.
        #
        # Still verified at both ends before it is believed, and dropped
        # outright by any registration or cancellation. See enumerate_listings.
        listings = enumerate_listings(timeout=timeout, verbose=verbose,
                                      allow_cache=True)
        if listings is None:
            say("The shop could not be enumerated, so rows past the first "
                "screen cannot be addressed safely - stopping rather than "
                "acting on a position that might be the wrong listing.")
            return False
        catalogue = [row for _, row in listings]

        # Give back widening the shop has since absorbed. Listings consolidate
        # upward as things sell, so rows a restock added are reabsorbed within
        # a few cycles -- and while BUY_ADDED_ROWS still claims them, every
        # cycle pays a full three-traversal enumeration to reach rows that are
        # no longer there. See note_shop_depth.
        note_shop_depth(rows_in_use(catalogue), len(catalogue))

        if all_rows:
            # Empty slots and Premium markers are carried through rather than
            # filtered here: the loop below skips them by action, and dropping
            # them now would renumber everything after them.
            targets = [(index, RowRef.of(row, catalogue), row.action)
                       for index, row in listings]
            live = sum(1 for _, _, a in targets
                       if a in ("change", "receive"))
            say(f"Found {len(targets)} slot(s), {live} of them live.")
        else:
            by_index = dict(listings)
            for index in rows:
                row = by_index.get(index)
                if row is None:
                    if index in added_rows:
                        # A widened row that turned out not to exist. The
                        # widening is a guess at where restocked listings went,
                        # bounded by SHOP_ROW_CAPACITY rather than by what the
                        # shop actually holds -- so it must be able to overshoot
                        # harmlessly. Failing here would let a bookkeeping
                        # guess stop a sweep the operator asked for.
                        continue
                    say(f"Row {index} is out of range; the shop holds "
                        f"{len(listings)} listing(s).")
                    return False
                targets.append((index, RowRef.of(row, catalogue), row.action))
    else:
        for index in rows:
            if not 1 <= index <= len(snapshot):
                if index in added_rows:
                    continue          # see the scrolling branch above
                say(f"Row {index} is out of range; {len(snapshot)} row(s) visible.")
                return False
            row = snapshot[index - 1]
            targets.append((index, RowRef.of(row, snapshot), row.action))

    # Listings consolidate upward, and RELISTING is what drives it -- not
    # sales. Cancelling frees a slot; the re-registration then lands in the
    # LOWEST empty slot rather than the one it came from. So every cycle pulls
    # listings toward the top and pushes the empties to the bottom, and a shop
    # asked for 1-24 that holds 19 listings settles at 1-19.
    #
    # Measured on the 07:57 run of 2026-08-06: "Siena's Unbinding Stone" went
    # row 24 -> 17 -> 12 over three cycles, and Force Core(Highest) went from
    # six slots to two. It converges over cycles rather than happening at once,
    # which is why a short window looks like scattered gaps instead of a clean
    # split.
    #
    # That convergence is exactly why only the TAIL is trimmed, never a gap
    # higher up. Mid-table empties are slots not yet reclaimed, with live
    # listings still below them: on cycle 3 the empties were rows 13, 14, 17,
    # 21 and 24, and cutting at the FIRST of those would have dropped nine live
    # listings. Everything after the LAST live row is empty for a structural
    # reason, and that is the only part safe to leave out.
    #
    # Recomputed from a fresh read every cycle, so this tracks the convergence
    # as it happens and is not a one-way ratchet: list something new and the
    # range grows back by itself.
    if targets:
        last_live = 0
        for position, (_, _, action) in enumerate(targets, start=1):
            if action in ("change", "receive"):
                last_live = position
        # last_live == 0 means every slot is empty. Left alone deliberately --
        # the sold-out check after the loop needs the full target list to tell
        # "the shop is finished" from "this batch had nothing in it".
        if last_live and last_live < len(targets):
            trimmed = targets[last_live:]
            targets = targets[:last_live]
            say(f"The shop has consolidated: the last {len(trimmed)} slot(s) "
                f"({trimmed[0][0]}-{trimmed[-1][0]}) are empty, so this batch "
                f"is rows {targets[0][0]}-{targets[-1][0]} instead.")
            record("batch.trimmed", kept=len(targets), dropped=len(trimmed),
                   last_live_row=targets[-1][0], first_empty=trimmed[0][0])

    say(f"Relisting {len(targets)} row(s), tracked by name, quantity and price:")
    for index, ref, action in targets:
        priced = f"{ref.price:,} Alz" if ref.price is not None else "price unread"
        say(f"  {index:2d}. [{action}] {ref.name} x{ref.qty} at {priced}")

    worked = 0

    failed_rows: list[str] = []
    for position, (index, ref, action) in enumerate(targets, start=1):
        name = ref.name

        # Checked BETWEEN rows, never inside one. A row is cancel-then-relist,
        # and the item sits loose in the inventory in between -- stopping there
        # would strand it, which is the failure ensure_work_tab_empty exists to
        # catch on the next cycle. The allowance keeps a row from being STARTED
        # that would still be running when the window opens.
        avoid_warlag(allowance=WAR_ROW_ALLOWANCE, verbose=verbose)

        say(f"\n########## {position}/{len(targets)}: row {index} - {name!r} ##########")

        if action == "register" or name == "(empty)":
            say("Empty slot - nothing to relist, skipping.")
            continue

        # The previous relist closed the shop, so reopen before reading, then
        # re-read: earlier relists in this batch may have moved things.
        # A sold row (Receive) is still a candidate -- relist() collects it.
        if not dry_run and not ensure_shop_ready(verbose=verbose):
            say("Could not reopen the Agent Shop - stopping.")
            return False
        # Past the first screen the listing has to be scrolled to. Identity,
        # not the index: the shop is reopened between rows so the view is back
        # at the top, and earlier relists in this batch have renumbered
        # everything below whatever they touched.
        live = (bring_into_view(ref, timeout=timeout, verbose=verbose)
                if scrolling else await_rows(timeout))
        # An unreadable table is not an empty shop. Without this guard the
        # chain launders "I cannot see the table" into "the item sold": an
        # empty read makes locate_row report 'missing', every row is skipped
        # with "already sold out", and the batch returns True -- so run_loop
        # counts a SUCCESS, resets the consecutive-failure counter, and an
        # eight-hour run reports every cycle green having touched nothing.
        if not live:
            say("The listings could not be read - stopping rather than "
                "treating an unreadable table as an empty shop.")
            return False
        current = [r for r in live if r.action in ("change", "receive")]
        match, note = locate_row(current, ref)
        if match is None and note == "unmatched":
            # Something very like this row is on screen but its name did not
            # read cleanly. Skipping would report a live listing as sold and
            # let the batch finish "successfully" having refreshed nothing.
            say(f"{name!r} is on the table but its name did not read clearly "
                "enough to act on. Stopping rather than skipping a live "
                "listing as though it had sold.")
            return False
        if match is None:
            say(f"{name!r} is no longer in the table - already sold out, skipping.")
            continue
        if note:
            say(f"  {note}")
        if match.index != index:
            say(f"Moved: now at row {match.index}.")

        # Pass the identity, not just the number: relist reopens the shop and
        # refreshes the table, so this index is resolved against a different
        # read than the one that produced it.
        outcome = relist(match.index, dry_run=dry_run, timeout=timeout,
                         verbose=verbose, expect=ref)
        if outcome == SOLD_OUT:
            # Collecting a sale IS work: the row was found, acted on, and the
            # proceeds taken. Only a row that was never located counts as a
            # no-op for the purposes of the check at the end.
            worked += 1
            say(f"{name!r} sold out - collected, nothing to relist. Moving on.")
            continue
        if outcome != RELISTED:
            # A failure on ONE row is not evidence about the other nine -- but
            # some failures leave debris that makes every later row fail too,
            # and those must stop the batch. The difference is decidable, not a
            # guess: if the work tab is still empty, the cancel either did not
            # happen or was undone, and the next row starts from a clean state.
            # If it is dirty, an item is stranded there and every subsequent
            # row would fail its precondition identically.
            #
            # Measured cost of not making this distinction: three consecutive
            # cycles each relisted row 1, failed on row 2, never attempted rows
            # 3-10, and still counted as total failures -- which tripped the
            # breaker on a run that was doing half its work. One poison row
            # froze 80% of the shop.
            left = len(targets) - position
            if not dry_run and left and require_empty_work_tab(verbose=False):
                # A clean work tab is not the only precondition. A dialog left
                # on screen covers the table, so every later row fails its read
                # and the whole cycle is lost to one bad row anyway.
                #
                # Measured on 2026-08-05: three cycles in a row aborted on the
                # same row with "the dialog stayed open after Confirmation",
                # continued because the work tab was clean, and then failed the
                # very next row on "the listings could not be read". Same
                # cascade every time, 11 minutes to the breaker.
                #
                # close_any_dialog clicks CANCEL, never Confirmation, so it
                # cannot commit anything the abort was unsure about -- which is
                # why leaving the dialog up was over-cautious rather than safe.
                if dialog_present():
                    say("A dialog is still open after that failure; backing "
                        "out of it before continuing.")
                    if not close_any_dialog():
                        say("...it would not close, and it covers the table, "
                            "so every later row would fail its read - "
                            "stopping instead.")
                        say(f"Relisting {name!r} failed - stopping; "
                            f"{left} row(s) not attempted.")
                        return False
                say(f"Relisting {name!r} failed, but inventory tab {WORK_TAB} "
                    f"is clean, so the failure is confined to this row - "
                    f"continuing with {left} row(s) still to go.")
                failed_rows.append(name)
                continue
            if not dry_run and left:
                say(f"Relisting {name!r} failed AND inventory tab {WORK_TAB} is "
                    "not clean, so every later row would fail the same way.")
            say(f"Relisting {name!r} failed - stopping; "
                f"{len(targets) - position} row(s) not attempted.")
            return False
        worked += 1

        if not dry_run and not wait_for_table(max(timeout, 20.0)):
            # This row's work is COMMITTED and already verified against the
            # table. The wait exists only so the NEXT row does not read a
            # mid-refresh table, so a timeout here says nothing about what was
            # just done -- and throwing the batch away over it discards
            # finished, confirmed relists.
            #
            # Measured cost: on 2026-08-04 rows 1 and 2 were relisted and both
            # confirmed ("row 2 matches what was registered"), then this
            # timeout abandoned rows 3-10 and scored the cycle a failure. The
            # run had done exactly what it was asked and moved one step closer
            # to the failure breaker for it.
            #
            # Same decidable test used for a failed row above: a clean work tab
            # means nothing is stranded and the next row starts fresh.
            left = len(targets) - position
            if not left:
                break                 # last row; there is nothing left to read
            if require_empty_work_tab(verbose=False):
                say(f"The table did not finish refreshing after {name!r}, but "
                    f"the relist completed and inventory tab {WORK_TAB} is "
                    f"clean - continuing with {left} row(s) still to go.")
                continue
            say(f"The table did not finish refreshing after {name!r} AND "
                f"inventory tab {WORK_TAB} is not clean - stopping.")
            return False

    # A batch that did no work is not a success. Every row reporting "already
    # sold out" returned True, which made run_loop count a green cycle and
    # reset the consecutive-failure counter -- so a run could report hundreds
    # of successful cycles having relisted nothing at all. Rows that were
    # genuinely empty in the snapshot do not count against this.
    actionable = sum(1 for _, _, action in targets
                     if action in ("change", "receive"))

    # Nothing left to sell. Confirmed against a FRESH read before acting on it,
    # because "every row is empty" is also what a table caught mid-refresh
    # looks like, and ending a run on one bad frame would be worse than the
    # pointless cycling this avoids.
    if not dry_run and not actionable:
        say(f"\nAll {len(targets)} row(s) read as empty slots. Re-reading to "
            "be sure before treating the shop as sold out...")
        again = await_rows(timeout)
        # An unreadable re-read is neither "sold out" nor "fine". It used to be
        # neither branch below, so it fell through to the success return at the
        # end: the cycle reported "All N row(s) processed (none relisted)",
        # counted as a green cycle, and reset the consecutive-failure breaker.
        # A modal or a tooltip over the table produces exactly this, for the
        # whole duration -- the run then claims success for hours having done
        # nothing, and exits 0.
        #
        # The same rule as the first read, 130 lines up: an unreadable table is
        # not an empty shop.
        if not again:
            say("  the re-read could not be read at all - treating this as a "
                "failed cycle rather than a sold-out shop.")
            return False
        live_now = [r for r in again
                    if r.action in ("change", "receive")]
        if not live_now:
            raise ShopEmpty(
                f"every one of the {len(targets)} row(s) is an empty slot - "
                f"the shop has sold out, so there is nothing left to relist")
        if live_now:
            say(f"  the re-read found {len(live_now)} live row(s) after all - "
                "the first read caught the table mid-refresh. Not stopping.")
            return False

    if actionable and not worked:
        say(f"\nNone of the {actionable} live row(s) were relisted - every one "
            "read as already sold. That is not a successful cycle; stopping so "
            "it is not reported as one.")
        return False

    if failed_rows:
        # A partial batch must not read as a clean one. It is still a success
        # -- work was done and the remaining rows were reached, which is the
        # whole point of continuing past a bad row -- but the rows that failed
        # have to be named, or "All 10 rows processed" hides them.
        say(f"\n{len(failed_rows)} row(s) failed and were skipped: "
            + ", ".join(repr(n) for n in failed_rows))
        say("Each was confined to its own row - the work tab stayed clean, so "
            "the rest of the batch continued. They will be retried next cycle.")

    if not RESTOCK_BEFORE_RELIST and not dry_run:
        restock_pass(timeout=timeout, verbose=verbose)
    say(f"\nAll {len(targets)} row(s) processed"
        + (f" ({worked} relisted" if worked else " (none relisted")
        + (f", {len(failed_rows)} failed)." if failed_rows else ")."))

    return True


def restock_pass(timeout: float = 8.0, verbose: bool = True) -> None:
    """Buy, convert and list whatever has sold out. Never raises.

    Split out of relist_rows so it can run BEFORE the relisting rather than
    after it. Restocking last meant the rows it created were absent from the
    snapshot the relist worked from, so they were priced once and never
    repriced -- which is the entire reason widen_for_restocks exists. Running
    first, they are simply in the table when it is read.

    Failures are reported and swallowed. Restocking is opportunistic extra
    work; a market that will not sell must not turn a successful relist batch
    into a failed one and trip the run's failure breaker.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    # Nothing enabled means nothing runs -- not even the extra table read.
    # See restock_is_armed.
    if not restock_is_armed():
        return
    try:
            # A cheap look first. The question is "is any enabled Core
            # missing", and a Core VISIBLE on the first screen is answer
            # enough -- no sweep can make it more listed than it already is.
            # Only a Core that is absent from those ten rows needs the whole
            # shop read, because only then is "not here" ambiguous between
            # "sold out" and "further down".
            #
            # Worth the extra table read: enumerating scrolls the shop end to
            # end, about twenty seconds, and doing it every cycle to confirm
            # something already on screen is the waste that made the run look
            # like it was pacing the rows doing nothing.
            visible = await_rows(timeout)
            missing = [slot for slot, n in core_row_counts(visible).items()
                       if n < 1 and slot in enabled_buying_slots()]
            if not missing:
                say("Every Core that can be bought is already listed on the "
                    "first screen; no shop sweep needed.")
                return
            say("Not on the first screen: "
                + ", ".join(FAVOURITE_SLOTS[s] for s in missing)
                + " - sweeping the shop to see if they are further down.")
            remembered = cached_unlisted(missing)
            if remembered is not None:
                say("Using the last shop sweep rather than repeating it "
                    f"({len(remembered)} Core(s) still unlisted).")
                # Counted while the shop is still open -- see leave_for_restock.
                rows_now = shop_rows_used(verbose=False)
                leave_for_restock(verbose=verbose)
                restock_sold_out_slots(remembered, verbose=verbose,
                                       rows_used=rows_now)
                return
            everything = whole_shop_listings(timeout=timeout, verbose=False)
            if everything is None:
                say("\nRestock skipped: the shop could not be enumerated, and "
                    "a partial read is what makes a stocked item look absent.")
            else:
                note_unlisted(unlisted_core_slots(everything))
                leave_for_restock(verbose=verbose)
                restock_sold_out(everything, verbose=verbose)
    except Exception as exc:          # noqa: BLE001 - opportunistic only
        say(f"\nRestock pass did not run: {exc}")
    finally:
        # The vendor window must not survive this, whatever went wrong inside.
        #
        # It covers nothing useful and the caller's next act is to look for the
        # NPC. On 2026-08-08 a conversion aborted, the vendor Shop stayed up,
        # Lady Yekaterina could not be found, and a batch that had just bought
        # 270 Sets relisted nothing at all.
        try:
            if vendor_shop_open():
                say("  closing the vendor Shop before handing back.")
                close_npc_shop(verbose=verbose)
        except Exception:  # noqa: BLE001 - tidying must not raise
            pass


def run_sequence(actions: list[str], dry_run: bool = False, verbose: bool = True) -> bool:
    """Run actions back to back, stopping at the first failure.

    Each action is a string: 'cancel ROW', 'register ROW COL', or 'clear'.

    Row numbers are resolved fresh for each action, immediately before it runs.
    That matters: cancelling a row renumbers everything below it, so a sequence
    written against the table as it looks now would otherwise hit wrong rows.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    # An empty list is not a successful run of nothing.
    #
    # It used to return True, so a cycle that did no work counted as a success,
    # reset the consecutive-failure breaker, and kept an unattended loop alive
    # indefinitely doing nothing at all -- the breaker exists precisely to stop
    # that. Reaching here with no actions means the caller built the list
    # wrong, which is a fault to report rather than a no-op to wave through.
    if not actions:
        say("No actions to run - that is a caller error, not a completed "
            "cycle, so it is reported as a failure rather than resetting the "
            "failure breaker.")
        return False

    for position, spec in enumerate(actions, start=1):
        parts = spec.split()
        if not parts:
            say(f"[{position}/{len(actions)}] empty action - stopping.")
            return False

        verb, args = parts[0].casefold(), parts[1:]
        say(f"\n[{position}/{len(actions)}] {spec}")

        try:
            if verb == "cancel" and len(args) == 1:
                ok = cancel_item(int(args[0]), dry_run=dry_run, verbose=verbose)
            elif verb == "register" and len(args) in (2, 3):
                # An optional PRICE, because register_item refuses to price an
                # item it cannot name -- and this verb has no way to name one.
                # Without this the verb could never succeed at all.
                forced = int(args[2]) if len(args) == 3 else None
                ok = register_item(int(args[0]), int(args[1]),
                                   dry_run=dry_run, verbose=verbose,
                                   force_price=forced)
            elif verb == "relist" and len(args) in (1, 3):
                slot = (int(args[1]), int(args[2])) if len(args) == 3 else (None, None)
                # Sold out is not a failure: there was simply nothing to relist.
                ok = relist(int(args[0]), *slot,
                            dry_run=dry_run, verbose=verbose) != FAILED
            elif verb in ("relist-rows", "relist_rows") and args:
                # 'all' has to be honoured HERE too, not only on the --relist-rows
                # flag: --repeat drives this path, and that is where an
                # unattended run lives. Parsing it as a row list would yield an
                # empty list and quietly relist nothing.
                ok = relist_rows(parse_row_spec(args), dry_run=dry_run,
                                 verbose=verbose,
                                 all_rows=wants_all_rows(args))
            elif verb == "clear" and not args:
                ok = True if dry_run else clear_shop_slot(verbose=verbose)
            else:
                say(f"Unknown action {spec!r}. Use 'cancel ROW', "
                    "'register ROW COL', 'relist ROW [R C]', "
                    "'relist-rows 1-10', or 'clear'.")
                return False
        except ValueError:
            say(f"Action {spec!r} has a non-numeric argument.")
            return False
        except Aborted as exc:
            # PermissionError is deliberately NOT caught here. Catching it
            # turned "input is being refused" into an ordinary retryable cycle
            # failure, so an unattended loop retried a blocked run every tick
            # for hours, printing the same message and achieving nothing. It
            # now propagates to run_loop, which stops.
            say(f"Stopped: {exc}")
            return False

        if not ok:
            say(f"Action {position} failed - stopping; "
                f"{len(actions) - position} action(s) not attempted.")
            return False

        # Both actions make the client refetch the table; the next action must
        # not read it mid-refresh.
        if not dry_run and not wait_for_table():
            say("The table did not finish refreshing - stopping.")
            return False

    say(f"\nAll {len(actions)} action(s) completed.")
    return True


# HH:MM anywhere in the crop, with whatever junk glyph OCR appended.
# Anchored on the colon rather than on the string ends: '23:48"' and '19:28 7'
# are correct readings with a trailing artefact, and they are the common case
# rather than the exception.
#
# The lookbehind is load-bearing. Without it, the misread '43:21' matches at
# the '3' and yields 03:21 -- a perfectly plausible time, in range, two hours
# from the truth, and enough to move the whole war schedule. The range check
# below cannot catch that because 03:21 IS a valid time. No matching lookahead
# on the minutes, though: '23:489' is a correct 23:48 with a junk digit stuck
# on the end, and that is a reading worth keeping.
_SERVER_CLOCK_TEXT = re.compile(r"(?<!\d)([0-2]?\d)\s*[:.]\s*([0-5]\d)")

# (monotonic when read, server datetime it read). The local clock does the
# timekeeping between syncs; this is only the anchor.
_SERVER_CLOCK_SYNC: "tuple[float, _dt.datetime] | None" = None


def read_server_clock(source: "Image.Image | None" = None,
                      verbose: bool = False) -> "_dt.time | None":
    """The server clock from the HUD, or None if it did not read cleanly.

    Range-checked rather than trusted: a misread digit turned 13:21 into 43:21
    on one of the saved frames, and an hour of 43 would move the whole war
    schedule if it were believed.
    """
    shot = source if source is not None else grab()
    words = [w for w in find_words(shot, SERVER_CLOCK_REGION, 20)
             if w.conf >= 40]
    text = " ".join(w.text for w in words)
    match = _SERVER_CLOCK_TEXT.search(text)
    if match is None:
        if verbose:
            print(f"  the server clock did not read ({text!r}).")
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        if verbose:
            print(f"  the server clock read {hour}:{minute:02d}, which is not "
                  f"a time - ignoring it.")
        record("warlag.clock_absurd", text=text, hour=hour, minute=minute)
        return None
    return _dt.time(hour, minute)


def sync_server_clock(verbose: bool = False) -> bool:
    """Anchor the server clock. True if it took.

    THE COMPUTER'S CLOCK IS NOT CONSULTED, deliberately. The operator's machine
    keeps bad time, and every dependency on it here would be a silent one: a
    wrong wall clock does not raise, it just makes the script stop for a war
    that is not happening and work through one that is.

    So there are exactly two inputs. The TIME OF DAY comes from the game's own
    HUD, which is the server's own clock and the only authority that matters.
    ELAPSED time between readings comes from time.monotonic(), which counts
    seconds since an arbitrary point and is unaffected by the wall clock being
    wrong, by NTP correcting it, by a manual change, or by daylight saving.

    The date attached below is a fixed arbitrary epoch, not today. Only the
    time of day means anything -- the war schedule is identical every day of
    the week -- and the epoch exists purely so the arithmetic in
    war_quiet_window has a datetime to add days to. Using date.today() here
    would drag the machine's calendar back into a calculation that does not
    need it.
    """
    global _SERVER_CLOCK_SYNC
    reading = read_server_clock(verbose=verbose)
    if reading is None:
        return False
    # The reading names a MINUTE, so the true second is unknown. Taking the
    # END of that minute makes every "have we reached the quiet window"
    # question answer YES slightly early rather than slightly late.
    stamped = SERVER_CLOCK_EPOCH + _dt.timedelta(
        hours=reading.hour, minutes=reading.minute,
        seconds=SERVER_CLOCK_UNCERTAINTY)
    _SERVER_CLOCK_SYNC = (time.monotonic(), stamped)
    if verbose:
        print(f"  server clock reads {reading.hour:02d}:{reading.minute:02d} "
              f"(read from the game, not from this machine).")
    record("warlag.clock_synced", server=stamped.strftime("%H:%M"))
    return True


def server_now(resync: bool = True,
               verbose: bool = False) -> "_dt.datetime | None":
    """Server time now, from the last sync plus elapsed local time.

    None only when the clock has never been read successfully. After one good
    reading this keeps working through every later OCR failure, which is the
    point: a dialog covering the corner must not silently switch the war
    schedule off.
    """
    global _SERVER_CLOCK_SYNC
    stale = (_SERVER_CLOCK_SYNC is None
             or time.monotonic() - _SERVER_CLOCK_SYNC[0] > SERVER_CLOCK_RESYNC)
    if stale and resync:
        sync_server_clock(verbose=verbose)
    if _SERVER_CLOCK_SYNC is None:
        return None
    at, stamped = _SERVER_CLOCK_SYNC
    return stamped + _dt.timedelta(seconds=time.monotonic() - at)


def war_quiet_window(after: "_dt.datetime") -> "tuple[_dt.datetime, _dt.datetime]":
    """The quiet window in force at `after`, or the next one to come.

    Returns (start, end) in the same frame as `after` -- server time. The
    window brackets the END of a war, not its start: that is when the server
    empties and the lag lands.
    """
    best: "tuple[_dt.datetime, _dt.datetime] | None" = None
    # Yesterday and tomorrow as well as today: a war ending at 22:30 has a
    # window running to 22:34, and one at 01:00 belongs to the next day when
    # asked at 23:50.
    for day in (-1, 0, 1):
        midnight = (after + _dt.timedelta(days=day)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        for hour in WAR_START_HOURS:
            ends = midnight + _dt.timedelta(hours=hour, minutes=WAR_MINUTES)
            start = ends - _dt.timedelta(seconds=WAR_QUIET_BEFORE_END)
            end = start + _dt.timedelta(seconds=WAR_QUIET_SECONDS)
            if end <= after:
                continue                      # already over
            if best is None or start < best[0]:
                best = (start, end)
    assert best is not None                   # the loop spans three days
    return best


def avoid_warlag(allowance: float = 0.0, verbose: bool = True) -> float:
    """Stop and wait if a war is about to end. Returns seconds waited.

    `allowance` is how long the caller is about to spend before it can stop
    again -- one relist row is about two minutes -- so work is not STARTED that
    would run into the window. Called at row and cycle boundaries only: a row
    is cancel-then-relist, and pausing between those halves would leave the
    item sitting in the inventory unlisted.

    The game is put back to its default state first, so nothing is left open
    across the lag.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    now = server_now(verbose=verbose)
    if now is None:
        say("  the server clock has never read, so the war schedule cannot be "
            "followed this run.")
        return 0.0

    start, end = war_quiet_window(now)
    if now < start and (start - now).total_seconds() > allowance:
        return 0.0

    wait = (end - now).total_seconds()
    if wait <= 0:
        return 0.0

    reason = ("a war ends in "
              f"{(start + _dt.timedelta(seconds=WAR_QUIET_BEFORE_END) - now).total_seconds() / 60:.1f} min"
              if now < start else "a war has just ended")
    say(f"\nWAR LAG: {reason} (server {now:%H:%M:%S}). Going to the default "
        f"state and waiting {wait / 60:.1f} min, until server "
        f"{end:%H:%M:%S}.")
    record("warlag.pausing", server=now.strftime("%H:%M:%S"),
           until=end.strftime("%H:%M:%S"), seconds=round(wait, 1),
           allowance=allowance)

    try:
        leave_shop(verbose=verbose)
    except Exception as exc:  # noqa: BLE001 - the wait matters more
        say(f"  could not close the shop before waiting ({exc}); waiting "
            "anyway.")

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(min(5.0, deadline - time.monotonic()))
    say(f"WAR LAG: done waiting; resuming where it left off.")
    record("warlag.resumed", seconds=round(wait, 1))
    return wait


def leave_shop(verbose: bool = True) -> bool:
    """Put the game back to its default state: no dialog, no Trade window.

    Called when a run is finishing on purpose rather than being retried. A run
    that stops with the shop open leaves the character parked in a UI the next
    thing to touch the machine has to clear -- and an open Trade window is
    exactly what makes a later find_npc fail, because it covers the NPC.

    Never raises: this runs at the end of a run that has already decided its
    outcome, and an exception here would replace that outcome with a crash.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    # Whatever happens below, the session is over. Leaving the clock running
    # would let the NEXT run inherit a session it never opened and skip the
    # rebuild from the NPC on its first row.
    note_shop_closed()
    try:
        if dialog_present():
            say("Closing the dialog left on screen...")
            close_any_dialog()
        for _ in range(ESCAPE_ATTEMPTS):
            if not trade_window_open():
                say("Agent Shop closed; the game is back to its default state.")
                return True
            press_escape()
        if trade_window_open():
            say("Note: the Trade window would not close with Escape - close it "
                "by hand before the next run.")
            return False
        say("Agent Shop closed; the game is back to its default state.")
        return True
    except Exception as exc:  # noqa: BLE001 - tidying must not become the story
        say(f"Note: could not tidy up the game window ({exc}).")
        return False


def prepare_for_actions(verbose: bool = True) -> bool:
    """Put the game into a state where a cycle can run.

    A failed cycle leaves debris -- a dialog part-way through, an item stranded
    in the shop slot, the Trade window closed by the game -- so this clears all
    of it before the next attempt rather than failing the same way again.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not focus_game():
        say("Could not bring Cabal to the foreground.")
        return False

    # Clear any modifier a previous cycle may have left held. Nothing else in
    # the run would ever detect a stuck Ctrl, and with it held every plain
    # click becomes a Ctrl+Click, which moves items into the shop slot.
    release_modifiers()
    park_cursor()

    # Escape back to the default state. Pressed one at a time and rechecked:
    # a blind second press would close a dialog and then open the system menu.
    for attempt in range(ESCAPE_ATTEMPTS):
        # dialog_present, not dialog_kind: this is the other place a flaked
        # title read let a modal survive into the next cycle, where it covered
        # the table and produced "No listings visible" before a single row had
        # been touched.
        if not dialog_present():
            break
        say(f"Dialog still open - pressing Escape ({attempt + 1}).")
        press_escape()
    else:
        # Escape did not clear it; fall back to backing out with Cancel.
        if not close_any_dialog():
            say("Could not close a dialog left open on screen.")
            return False

    if not open_trade_window(verbose=verbose):
        say("Could not open the Trade window on the Register tab.")
        return False

    # Re-measure the layout now the window is up, every cycle. The Trade window
    # can be dragged between cycles without the screen or client rect changing,
    # and every coordinate below is relative to where it sits -- so carrying a
    # measurement forward from the previous cycle is exactly the stale-layout
    # risk that reusing a saved one carries.
    if not calibrate(verbose=verbose, save=False):
        say("Could not measure the layout for this cycle - not acting on "
            "coordinates that have not been checked.")
        return False

    if not clear_shop_slot(verbose=verbose):
        say("Could not clear the shop slot.")
        return False

    return True


def run_loop(
    actions: list[str],
    minutes: float,
    every: float,
    dry_run: bool = False,
    verbose: bool = True,
) -> bool:
    """Repeat `actions` every `every` minutes for `minutes` minutes.

    The interval is measured from the start of each cycle, so a slow cycle eats
    into the wait rather than pushing everything later. A cycle that overruns
    the interval simply starts the next one immediately. `every=0` means never
    wait: cycles run back to back until the duration is up.

    Every cycle begins by reopening the Trade window and clearing any debris a
    previous failure left behind. A failed cycle is therefore not fatal: it is
    reported and retried on the next tick. Only a locked workstation stops the
    loop outright, since nothing can work through that.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    interval = every * 60.0
    end = time.monotonic() + minutes * 60.0
    cycle = succeeded = failures = consecutive = 0
    stopped = False
    # Stopped because the work is DONE, as opposed to stopped because something
    # broke. Only a sold-out shop sets it.
    finished = False

    if every:
        cadence = (f"every {every:g} min for {minutes:g} min "
                   f"(about {max(1, int(minutes / every))} cycles)")
    else:
        # How many fit depends on how long a cycle takes, so do not guess.
        cadence = f"back to back for {minutes:g} min"
    say(f"Looping {actions} {cadence}. A failed cycle is retried on the next "
        f"tick. Ctrl+C to quit.")

    if keep_awake(True):
        say("Holding off display/system sleep for the duration.")
    else:
        say("WARNING: could not inhibit sleep; the run may be cut short by it.")

    try:
        while time.monotonic() < end:
            cycle += 1
            started = time.monotonic()
            say(f"\n===== cycle {cycle} at {datetime.now():%H:%M:%S} =====")

            # Also at the cycle boundary, so a run that starts inside a window
            # waits rather than walking into it. The per-row check above is
            # what keeps work out of the window once a cycle is under way.
            avoid_warlag(allowance=WAR_CYCLE_ALLOWANCE, verbose=verbose)
            # A gap in the index is only diagnostic if the boundaries are in
            # it. A cycle.start with no cycle.end means it died mid-cycle; a
            # cycle.end with no following start means the loop exited, and
            # the loop.stopped beside it says why. Without these, three
            # cycles that each failed in a record()-free function left NO
            # frames at all -- which is what made a five-hour outage
            # unattributable.
            record("cycle.start", cycle=cycle, consecutive=consecutive,
                   succeeded=succeeded, failures=failures)

            # A locked workstation blanks captures and swallows input, so every
            # action would fail. Say why rather than emitting confusing errors.
            if session_locked():
                say("The workstation is locked - screen capture and input are "
                    "unavailable. Terminating the loop.")
                stopped = True
                break

            # Start from a known state: Trade window open on Register, no
            # dialog up, shop slot empty. This is what makes a failed cycle
            # recoverable on the next tick.
            try:
                if dry_run or prepare_for_actions(verbose=verbose):
                    if run_sequence(actions, dry_run=dry_run, verbose=verbose):
                        succeeded += 1
                        consecutive = 0
                    else:
                        failures += 1
                        consecutive += 1
                        say(f"\nCycle {cycle} failed - will retry next cycle.")
                else:
                    failures += 1
                    consecutive += 1
                    say(f"\nCycle {cycle}: could not get the game ready - "
                        "will retry next cycle.")
            except ShopEmpty as exc:
                # The work is FINISHED, not failed. Cycling an empty shop for
                # the rest of a 500-minute run relists nothing, keeps the
                # machine awake and the cursor moving, and buries the moment
                # the shop actually sold out in hours of identical log.
                record("loop.stopped", reason="sold_out", detail=str(exc),
                       cycle=cycle, consecutive=consecutive)
                say(f"\nSOLD OUT: {exc}")
                succeeded += 1
                consecutive = 0
                stopped = True
                finished = True
                leave_shop(verbose=verbose)
                break
            except FatalAbort as exc:
                record("loop.stopped", reason="fatal", detail=str(exc),
                       cycle=cycle, consecutive=consecutive)
                # Not retryable: the run listed something it should not have.
                say(f"\nFATAL: {exc}")
                say("Terminating the loop; nothing further will be attempted.")
                failures += 1
                stopped = True
                leave_shop(verbose=verbose)
                break
            except PermissionError as exc:
                record("loop.stopped", reason="permission", detail=str(exc),
                       cycle=cycle, consecutive=consecutive)
                # Input is being refused -- not elevated, or the foreground
                # went to an elevated window or the secure desktop. Retrying
                # cannot fix it, and it used to escape the loop entirely from
                # prepare_for_actions (which is inside this try but was not
                # covered), killing the run with a raw traceback and no summary.
                say(f"\nInput was refused: {exc}")
                say("Terminating the loop; nothing further can be clicked.")
                failures += 1
                stopped = True
                break
            except Exception as exc:  # noqa: BLE001 - an unattended run must not die
                # The traceback, not just type(exc).__name__. A NameError at
                # line 4200 and one at 3900 print identically otherwise, and
                # this is the only handler standing between an unattended run
                # and an unexplained stop.
                record("cycle.exception", cycle=cycle, exc=type(exc).__name__,
                       detail=str(exc), traceback=traceback.format_exc(),
                       consecutive=consecutive)
                say(traceback.format_exc())
                failures += 1
                consecutive += 1
                say(f"\nCycle {cycle} raised {type(exc).__name__}: {exc}")
                if session_locked():
                    record("loop.stopped", reason="locked", cycle=cycle)
                    say("The workstation is locked - terminating the loop.")
                    stopped = True
                    break
                say("Will retry next cycle.")

            # Checked out here, after the handlers, so a cycle that RAISES also
            # counts towards it. With the check inside the try and the handler
            # not touching `consecutive`, a recurring exception -- a bad
            # coordinate, a NameError, a changed Tesseract path -- retried for
            # the whole duration, which is the exact failure this breaker was
            # added to stop.
            record("cycle.end", cycle=cycle, consecutive=consecutive,
                   succeeded=succeeded, failures=failures)

            # A disconnect is not a failure to retry -- it is the end of the
            # session. Checked only after a cycle has FAILED, so a healthy run
            # never pays for the OCR: on 2026-08-08 the drop cost three cycles
            # of "the table could not be read" and "slot did not load", every
            # line true and none of them the reason.
            #
            # No click reaches the server while this is up, so the breaker's
            # remaining budget would be spent proving that three more times.
            if consecutive and game_disconnected():
                record("loop.stopped", reason="disconnected", cycle=cycle)
                say("\nThe GAME HAS DISCONNECTED - the client is showing "
                    "'You have been disconnected from the server'.")
                say("Nothing this script does can reach the server until you "
                    "click OK and log back in, so it is stopping here rather "
                    "than retrying.")
                say(f"Anything in inventory tab {WORK_TAB} is safe; the next "
                    "run will pick it up.")
                stopped = True
                break

            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                # THE entry that was missing. This breaker fired correctly at
                # 19:56 one night, printed an accurate diagnosis, and left no
                # trace on disk -- so a five-hour outage looked like an
                # unexplained crash rather than a deliberate, correct stop.
                record("loop.stopped", reason="consecutive_failures",
                       cycle=cycle, consecutive=consecutive)
                say(f"\n{consecutive} cycles have failed in a row - stopping "
                    "rather than repeating it for the rest of the run. Check "
                    f"the shop and inventory tab {WORK_TAB} by hand.")
                stopped = True
                break

            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            wait = min(max(0.0, interval - (time.monotonic() - started)), remaining)
            if wait > 0:
                say(f"Next cycle in {wait / 60:.1f} min "
                    f"({remaining / 60:.1f} min left overall).")
                time.sleep(wait)
            elif time.monotonic() - started < MIN_CYCLE_SECONDS:
                # Floor the turnaround. With --every 0 a cycle that does no
                # work -- a dry run, an unknown verb, an action list that
                # returns immediately -- costs microseconds, and the loop would
                # spin a core flat out for the whole duration.
                time.sleep(min(MIN_CYCLE_SECONDS - (time.monotonic() - started),
                               remaining))
    except KeyboardInterrupt:
        record("loop.stopped", reason="interrupt")
        say("\nInterrupted - stopping the loop.")
        stopped = True
    finally:
        keep_awake(False)
        # Tidy up on EVERY exit, not just a sold-out shop.
        #
        # A run that dies with the Trade window open leaves the character
        # parked in a UI, and an open Trade window covers the NPC -- so the
        # next run's find_npc fails and it dies before doing anything. That is
        # how a single failure became a dead afternoon: the 07:57 run of
        # 2026-08-06 stopped at the breaker and left the shop open and scrolled
        # mid-list, which is exactly the state the next run cannot start from.
        #
        # In the `finally` so it also covers Ctrl+C, the duration expiring, and
        # a FatalAbort.
        #
        # Guarded even though leave_shop is itself written never to raise. An
        # exception escaping HERE would replace an in-flight FatalAbort with an
        # ordinary crash, and the whole point of a FatalAbort is that a human
        # looks at it -- the tidying must never be able to hide why the run
        # stopped.
        try:
            leave_shop(verbose=verbose)
        except Exception as exc:      # noqa: BLE001 - must not mask the outcome
            say(f"Note: could not tidy up the game window ({exc}).")
        except BaseException:
            # A SECOND Ctrl+C, arriving while this very tidy-up runs. It is not
            # an Exception, so the clause above never saw it, and on 2026-08-06
            # at 19:32 it escaped mid-leave_shop and left the Agent Shop open
            # with nothing said about it.
            #
            # Honoured rather than swallowed -- someone pressing Ctrl+C during
            # the shutdown wants out now, and holding them here to tidy would be
            # the wrong way round. But it is announced first, because "the shop
            # may still be open" is the one fact that decides whether the next
            # run can find the NPC at all.
            say("Interrupted while closing the Agent Shop - it may still be "
                "open. Close it by hand, or the next run cannot see the NPC.")
            raise

    say(f"\nDone: {cycle} cycle(s) run, {succeeded} succeeded, {failures} failed"
        + (f"; stopped early at cycle {cycle}." if stopped else "."))
    # `finished` separates "stopped because the work is done" from "stopped
    # because something broke". Without it a sold-out shop -- the best possible
    # outcome -- exits non-zero, because every early stop looked alike.
    return finished or (succeeded > 0 and not stopped)


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------
#
# Every geometry constant above was measured on one 2560x1440 machine. This
# section captures those values as a REFERENCE frame, measures the real frame
# on whatever machine is running, and rewrites the constants accordingly.
#
# Design notes, because the failure modes here are expensive:
#
#  * Measurement beats assumption. The Trade window is located by OCR of words
#    inside it, not by assuming where it sits.
#  * Scale is measured from the LONGEST available baseline. Two anchors a few
#    pixels apart would turn OCR jitter into a large scale error, which is
#    worse than not scaling at all, so short baselines are rejected.
#  * Anything unmeasurable is refused, not guessed. A wrong coordinate here
#    does not produce a wrong answer, it produces a click somewhere in the game
#    world -- which moves items, walks the character, or cancels a listing.
#  * The reference machine must be unaffected. With scale 1.0 and the reference
#    origin, every derived value equals the original constant exactly.

# Constants that live in the Trade window's frame, and how each maps.
#   box      (l, t, r, b) inside the Trade window
#   point    (x, y) inside the Trade window
#   x / y    a single coordinate inside the Trade window
#   xpair    (x1, x2) both inside the Trade window
#   len      a distance, scaled but not translated
#   lenpair  two distances
_TRADE_FRAME_GEOMETRY = {
    "TRADE_REGION": "box", "REGISTER_PANEL": "box", "PRICE_ROWS": "box",
    "PRICE_FIELD": "box", "QTY_FIELD": "box", "NET_SALES_ROWS": "box",
    "SHOP_SLOT_BOX": "box",
    "SHOP_SLOT": "point", "QTY_INPUT": "point", "PARK_POINT": "point",
    "PRICE_TOP_Y": "y", "PRICE_BOTTOM_Y": "y",
    "PANEL_RADIO_X": "x", "PRICE_TEXT_MAX_X": "x",
    "NAME_COLUMN": "xpair",
    "DIALOG_BUTTON_MIN_X": "x",
    "PRICE_ROW_Y_TOL": "len",
    # These follow the game window rather than the Trade window, but they are
    # mapped through the same frame on purpose. Deriving them from the client
    # rect instead produced different regions on the REFERENCE machine -- e.g.
    # POPUP_REGION became (10, 30, 2135, 1185) against the measured
    # (500, 350, 2100, 1150) -- which silently changes behaviour on the one
    # setup known to work. Scaling the measured value is approximate elsewhere
    # and exact here; that is the right way round.
    "POPUP_REGION": "box",
    "NPC_SEARCH_REGION": "box",
    # The vendor Shop and the Purchase tab.
    #
    # These are not in the Trade window's frame -- the vendor window is its own
    # panel at the left edge, and the Purchase tab is a different page of the
    # Trade window -- but they are mapped through it for the same reason
    # POPUP_REGION is, above: scaling the MEASURED value is exact on the
    # reference machine and approximate elsewhere, which is the right way
    # round. Deriving them from the client rect instead would change behaviour
    # on the one setup known to work.
    #
    # Before this they were in no table at all, so apply_layout left them at
    # their 2560x1440 values on every machine. A successfully calibrated
    # 1920x1080 run therefore reached alt_click(252, 1066) -- a Mass Purchase
    # gesture at coordinates belonging to a different screen, in the one window
    # where a stray click spends Alz. The guards would most likely have failed
    # closed, because vendor_shop_open() reads an equally uncalibrated band,
    # but that is luck rather than design.
    "SHOP_WINDOW_TITLE": "box",
    "CONVERT_TIP_REGION": "box",
    "CONVERT_DIALOG_REGION": "box",
    "CONVERT_DLG_ITEM": "box",
    "CONVERT_DLG_PRICE": "box",
    "CONVERT_DLG_QTY_VALUE": "box",
    "CONVERT_DLG_QTY_MAX": "box",
    "CONVERT_DIALOG_BUTTONS": "box",
    "VENDOR_TAB_REGION": "box",
    "PURCHASE_SORT_REGION": "box",
    "PURCHASE_SORT_BUTTON": "point",
    "PURCHASE_SORT_OPTIONS": "box",
    "CONVERT_COLS": "xs",
    "CONVERT_ROWS": "ys",
    "VENDOR_TAB_BAND": "ypair",
    "FAVOURITE_FIRST": "point",
    "FAVOURITE_PITCH": "len",
    "PURCHASE_ROW_TOP": "y",
    "PURCHASE_ROW_PITCH": "len",
    "PURCHASE_BUY_X": "x",
    # The Confirm Purchase dialog, added when its quantity field was wired up.
    # Same reasoning as everything above it: these are absolute coordinates
    # measured on one 2560x1440 machine, and one of them is CLICKED to focus a
    # field before digits are typed into it.
    "PURCHASE_DIALOG_REGION": "box",
    "PURCHASE_DLG_ITEM": "box",
    "PURCHASE_DLG_QTY_VALUE": "box",
    "PURCHASE_DLG_QTY_MAX": "box",
    "PURCHASE_DLG_PRICE": "box",
    "PURCHASE_DIALOG_BUTTONS": "box",
    "DISCONNECT_REGION": "box",
    "TRADE_WINDOW_SEARCH": "box",
    "NPC_EXCLUDE_ZONES": "boxes",
    # Anchored on the Inventory panel, not the Trade window, but mapped the
    # same way for the same reason. Deriving ALZ_REGION from the client rect
    # replaced a tight 195x56 band with a whole screen quadrant -- and
    # find_alz works by taking the bounding box of every bright, saturated
    # pixel, so it then returned the entire quadrant instead of the digits.
    # Measured consequence at 1920x1080: the inventory anchor moved 139px, the
    # tab row was missed entirely, and slot (8,8) landed at x=1931 on a
    # 1920-wide screen -- a Ctrl+Click into the game world, which is precisely
    # what this layer exists to prevent.
}

# The reference machine's game client, and the regions that belong to the
# Inventory panel rather than the Trade window. Cabal docks the Inventory to
# the RIGHT edge of the client, so these must follow the client, not the Trade
# window: if the game keeps its UI at a fixed pixel size on a smaller screen
# (rather than scaling it), a Trade-window-anchored ALZ_REGION lands off the
# right edge, find_alz returns None, and slot_centre() raises -- so nothing can
# be registered at all. Anchoring to the client is identical on the reference
# machine and correct either way.
REF_CLIENT = (0, 23, 2560, 1392)
_CLIENT_FRAME_GEOMETRY = ("ALZ_REGION", "INVENTORY_TITLE_REGION")

# Constants measured in pixels but anchored on the Inventory panel, which is
# found separately (by the colour of the Alz figure). These only need scaling.
_INVENTORY_FRAME_GEOMETRY = {
    "SLOT_PITCH": "lenpair", "SLOT_ONE_OFFSET": "lenpair",
    "TAB_ONE_OFFSET": "lenpair", "ALZ_TO_TITLE": "lenpair",
    "TAB_PITCH": "len", "SLOT_INSET": "len",
}

# Captured at import, before anything can rewrite them.
_REFERENCE_GEOMETRY: dict[str, object] = {}


def _capture_reference_geometry() -> None:
    """Snapshot the constants, converted into the Trade window's own frame.

    The constants are written as ABSOLUTE screen coordinates on the reference
    display -- TRADE_REGION is literally (10, 30, 1235, 1065). Layout.box()
    translates by the live origin, so feeding it an absolute value adds the
    origin a second time: at the reference layout every region came out
    shifted by exactly (+10, +30), which is a silent, uniform, plausible-
    looking error. Subtracting the reference origin here is what makes
    apply_layout() an identity at scale 1.0.

    Distances (`len`, `lenpair`) are not translated -- only scaled -- so they
    are captured as they are.
    """
    ox, oy = REF_TRADE_ORIGIN
    for name, kind in _TRADE_FRAME_GEOMETRY.items():
        value = globals()[name]
        if kind == "box":
            value = (value[0] - ox, value[1] - oy, value[2] - ox, value[3] - oy)
        elif kind == "point":
            value = (value[0] - ox, value[1] - oy)
        elif kind == "x":
            value = value - ox
        elif kind == "y":
            value = value - oy
        elif kind == "xpair":
            value = (value[0] - ox, value[1] - ox)
        elif kind == "xs":
            value = tuple(v - ox for v in value)
        elif kind == "ys":
            value = tuple(v - oy for v in value)
        elif kind == "ypair":
            value = (value[0] - oy, value[1] - oy)
        elif kind == "boxes":
            value = tuple((b[0] - ox, b[1] - oy, b[2] - ox, b[3] - oy)
                          for b in value)
        # "len" is a distance: no translation.
        _REFERENCE_GEOMETRY[name] = value

    for name in _INVENTORY_FRAME_GEOMETRY:
        _REFERENCE_GEOMETRY[name] = globals()[name]

    # Client-anchored regions are stored as insets from the client's RIGHT and
    # TOP edges, so they follow the Inventory panel wherever the client is.
    cl, ct, cr, _cb = REF_CLIENT
    for name in _CLIENT_FRAME_GEOMETRY:
        left, top, right, bottom = globals()[name]
        _REFERENCE_GEOMETRY[name] = (cr - left, top - ct, cr - right, bottom - ct)


def apply_layout(layout: "Layout") -> None:
    """Rewrite every geometry constant into `layout`'s frame.

    Called once, before anything reads a coordinate. Rewriting globals is
    deliberate: these names are read from ~30 places, and threading a layout
    object through all of them would be a far larger change with far more
    opportunity to miss one -- and a missed one clicks the wrong pixel.
    """
    # A real measurement now exists, so _ocr_reference_scale stops
    # guessing from the client rect and uses LAYOUT.scale instead.
    global _CALIBRATED
    _CALIBRATED = True

    global LAYOUT
    LAYOUT = layout
    if not _REFERENCE_GEOMETRY:
        _capture_reference_geometry()

    for name, kind in _TRADE_FRAME_GEOMETRY.items():
        ref = _REFERENCE_GEOMETRY[name]
        if kind == "box":
            value = layout.box(ref)
        elif kind == "point":
            value = layout.point(ref)
        elif kind == "x":
            value = layout.x(ref)
        elif kind == "y":
            value = layout.y(ref)
        elif kind == "xpair":
            value = (layout.x(ref[0]), layout.x(ref[1]))
        elif kind == "xs":
            value = tuple(layout.x(v) for v in ref)
        elif kind == "ys":
            value = tuple(layout.y(v) for v in ref)
        elif kind == "ypair":
            value = (layout.y(ref[0]), layout.y(ref[1]))
        elif kind == "boxes":
            value = tuple(_clamp_box(layout.box(b), layout.screen) for b in ref)
        else:  # len
            value = layout.length(ref)
        if kind == "box":
            value = _clamp_box(value, layout.screen)
        globals()[name] = value

    for name, kind in _INVENTORY_FRAME_GEOMETRY.items():
        ref = _REFERENCE_GEOMETRY[name]
        if kind == "lenpair":
            # Keep sub-pixel precision: SLOT_PITCH is fractional and is
            # multiplied by up to 7, so rounding it early walks the grid off
            # by several pixels at the far corner.
            value = (ref[0] * layout.scale, ref[1] * layout.scale)
            if all(float(v).is_integer() for v in ref):
                value = (int(round(value[0])), int(round(value[1])))
        else:  # len
            value = ref * layout.scale
            if float(ref).is_integer():
                value = int(round(value))
        globals()[name] = value

    # Inventory regions, as insets from the client's right and top edges.
    client = layout.client or (0, 0, *layout.screen)
    cl, ct, cr, cb = client
    for name in _CLIENT_FRAME_GEOMETRY:
        left_in, top_in, right_in, bottom_in = _REFERENCE_GEOMETRY[name]
        globals()[name] = _clamp_box(
            (cr - layout.length(left_in), ct + layout.length(top_in),
             cr - layout.length(right_in), ct + layout.length(bottom_in)),
            layout.screen)

    # NPC_BODY_OFFSET and the sweep grid are distances below the nameplate.
    globals()["NPC_BODY_OFFSET"] = (
        int(round(_REFERENCE_NPC_BODY_OFFSET[0] * layout.scale)),
        int(round(_REFERENCE_NPC_BODY_OFFSET[1] * layout.scale)))
    globals()["NPC_CLICK_OFFSETS"] = _npc_click_offsets()


_REFERENCE_NPC_BODY_OFFSET = NPC_BODY_OFFSET

def client_rect() -> tuple[int, int, int, int] | None:
    """The game's client area in screen pixels, or None if not found.

    The client area, not the window rect: the title bar and borders are not
    part of the rendered UI, and including them shifts every derived region.
    """
    hwnd = find_game_window()
    if not hwnd:
        return None
    try:
        rect = ctypes.wintypes.RECT()
        if not ctypes.windll.user32.GetClientRect(ctypes.c_void_p(hwnd),
                                                  ctypes.byref(rect)):
            return None
        point = ctypes.wintypes.POINT(rect.left, rect.top)
        if not ctypes.windll.user32.ClientToScreen(ctypes.c_void_p(hwnd),
                                                   ctypes.byref(point)):
            return None
        return (point.x, point.y,
                point.x + (rect.right - rect.left),
                point.y + (rect.bottom - rect.top))
    except Exception:  # noqa: BLE001 - calibration must not crash the run
        return None


def _anchor_centre(phrase: str, words: list, lines: list):
    """Where an anchor phrase sits in one OCR pass, or None.

    Multi-word anchors are matched across a joined line and then narrowed to
    just the words that spell them -- measuring across the whole line puts the
    centre wherever the neighbouring text happens to end.
    """
    if " " in phrase:
        target = _normalise(phrase)
        for line in lines:
            if target in _normalise("".join(w.text for w in line)):
                window = _minimal_window(line, (target,))
                if window:
                    return _span_centre(window)
        return None
    needle = phrase.casefold()
    hits = sorted((w for w in words if needle in w.text.casefold()),
                  key=lambda w: w.top)

    # AMBIGUOUS MEANS UNKNOWN. If the word appears more than once on screen,
    # discard the anchor rather than pick one -- a missing anchor costs one
    # unit of drop budget, a wrong one poisons the shared fit and consumes a
    # drop to undo.
    #
    # The old rule took the topmost match, which is not a tie-break at all but
    # an unconditional pick that happens to work because the chat log is drawn
    # low. Measured over 3,019 frames with the Trade window open: 'Trade' has a
    # second match on 33 frames, 'Selling' on 52, 'Name' on 13 -- all chat
    # adverts ('SELLING Upgrade Core 85k', trade-channel tags, a player called
    # NoNameNeeded). Discarding every ambiguous match still calibrates
    # 3,019/3,019 and never leaves fewer than four anchors, so the safety costs
    # nothing measurable.
    #
    # It also closes a gap "topmost wins" cannot: chat renders OVER the Trade
    # window and grows upward. The highest chat line yet recorded sits at
    # y=1031 while 'Selling' is at y=1012 and 'Refresh' at y=1010 -- a 19px
    # margin, narrower than the 26px line pitch. One busier evening and the
    # advert IS the topmost match.
    if len(hits) > 1:
        return None
    if hits:
        return hits[0].centre
    # Nothing matched the whole word. At smaller UI scales this font clips its
    # leading glyph: measured at 1920x1080, the Trade window's title reads
    # 'rade' at 96% confidence on upscale 2, 3 AND 4 -- so a substring test can
    # never find it, at any upscale, and the only remaining word on screen
    # containing 'trade' is a chat line. That is how a decoy 858px away came to
    # be treated as the window title.
    #
    # So: accept a word that is the anchor missing its first or last character,
    # provided it is long enough that the match is still meaningful. Four
    # characters minimum -- 'rade' qualifies, a stray 'na' would not.
    if len(needle) >= 5:
        # A clipped word's CENTRE IS NOT THE FULL WORD'S CENTRE. 'rade' is
        # missing its leading glyph, so its box starts one character later and
        # its centre sits about half a glyph to the right. Taking it raw put
        # 'Trade' 9px off and shifted the whole calibrated origin by 2px --
        # the same drift this file already fixed once. Compensate by the
        # measured average glyph width of the text actually read.
        #
        # Confidence bar is higher than the 40.0 used for whole-word matches:
        # a partial word is weaker evidence, and the case that caused the
        # drift scored 40.1, i.e. it cleared the normal bar by a tenth.
        for w in sorted(words, key=lambda x: x.top):
            text = w.text.casefold().strip()
            if w.conf < NEAR_ANCHOR_MIN_CONF or not text:
                continue
            lost_first = text == needle[1:]
            lost_last = text == needle[:-1]
            if not (lost_first or lost_last):
                continue
            glyph = (w.right - w.left) / max(len(text), 1)
            cx, cy = w.centre
            # Losing the FIRST glyph shifts the observed centre right, so
            # correct left, and vice versa.
            return (int(round(cx - glyph / 2 if lost_first else cx + glyph / 2)),
                    cy)
    return None


def measure_layout(image: Image.Image | None = None,
                   verbose: bool = True) -> "Layout | None":
    """Measure the live Trade window and return a Layout, or None.

    Requires the Trade window to be open and visible. Returns None -- never a
    guess -- when the anchors cannot be found or disagree.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    shot = image if image is not None else grab()
    screen = shot.size
    client = client_rect()
    search = (0, 0, screen[0], screen[1])

    # Locate every anchor word we can. Each gives one (measured, reference)
    # pair; two of them give an offset and a scale.
    # ONE OCR pass for all the anchors, not one pass each.
    #
    # find_text/find_phrase each run find_words over the whole screen, so six
    # anchors meant six full-screen Tesseract invocations -- about 13s, paid on
    # every cycle now that the layout is measured live. The words are identical
    # whichever way they are collected; only the number of subprocess spawns
    # differs.
    # The upscale find_words picks is derived from LAYOUT.scale -- which, on
    # the FIRST calibration of a session, is still the built-in 1.0 whatever
    # the screen really is. On a smaller screen that means x2, and x2 is
    # exactly the setting find_words' own docstring says splits 'Refresh' into
    # 'R' + 'efresh' at 1920x1080. So the bootstrap loses anchors precisely
    # when it can least afford to, and cannot recover, because the upscale
    # only rises after a calibration succeeds.
    #
    # Retrying at a larger upscale costs one extra OCR pass and only happens
    # when anchors are missing. Anchors already found are kept: a second pass
    # cannot make a word that read cleanly read better.
    def collect(scale_override=None):
        got = find_words(shot, search, 40.0, scale=scale_override) \
            if scale_override else find_words(shot, search, 40.0)
        return got, _text_lines(got)

    words, lines = collect()
    attempts = [(words, lines)]
    hits = sum(1 for p, _ in REF_ANCHORS
               if _anchor_centre(p, words, lines) is not None)

    # Retry ONLY when there is not enough to fit safely -- never merely to
    # collect the full set.
    #
    # Retrying whenever fewer than six were found actively made calibration
    # worse: measured over 181 corpus frames, 24 of them found five anchors
    # that fit PERFECTLY (residual 0.0px, origin exactly (10,30)), and the
    # larger pass then contributed a sixth, 'Trade', 9px from where the other
    # five put it. That is inside the 11.7px bar, so it was accepted, and it
    # dragged the origin 2px. More anchors is not the goal; a consistent fit
    # is. 'Trade' is the least reliable of the six -- found on 237 of 286
    # frames, the lowest of any -- and was also the decoy that made a 1080p
    # screen uncalibratable.
    if hits < MIN_ANCHORS_AFTER_DROP:
        for bigger in (3, 4):
            words2, lines2 = collect(bigger)
            attempts.append((words2, lines2))
            if sum(1 for p, _ in REF_ANCHORS
                   if _anchor_centre(p, words2, lines2) is not None) >= len(REF_ANCHORS):
                break

    found: list[tuple[str, tuple[int, int], tuple[int, int]]] = []
    for phrase, ref_point in REF_ANCHORS_ALL:
        centre = None
        used = 0
        for n, (ws, ls) in enumerate(attempts):
            centre = _anchor_centre(phrase, ws, ls)
            if centre is not None:
                used = n
                break
        if centre is not None:
            found.append((phrase, centre, ref_point))
            say(f"  anchor {phrase!r:16} at {centre}  (reference {ref_point})"
                + ("" if used == 0 else "  [found on a larger OCR pass]"))
        else:
            say(f"  anchor {phrase!r:16} not found")

    # THREE, not two. A two-point fit has the same blind spot the old
    # pair-and-drift check had: with scale derived from |dm|/|dr|, any error
    # ALONG the baseline is absorbed entirely into the scale and the residual
    # is exactly 0. Measured, a 100px baseline-parallel displacement of one
    # anchor was accepted with "worst residual 0.0px" and produced 134px of
    # click error. The third anchor is what makes the residual mean anything.
    if len(found) < 3:
        # Say what was OBSERVED, then draw only the conclusions the evidence
        # supports. The old text named one cause ("not overlapped?") and
        # guessed it, which is useless when the real cause is one of the three
        # others -- and those need opposite fixes.
        say(f"\nCalibration could not measure the Trade window.")
        say(f"  display captured   {screen[0]}x{screen[1]}  (primary display only)")
        if client:
            say(f"  game client area   ({client[0]},{client[1]})-({client[2]},{client[3]})")
            # Provable, not guessed: screenshots only ever cover the primary
            # display, so a client rect outside it means the window was never
            # in frame. This is a real failure that previously read as
            # "the Trade window is not open".
            if (client[0] < 0 or client[1] < 0
                    or client[2] > screen[0] or client[3] > screen[1]):
                say("  >> The game window is NOT on the display being captured.")
                say("     Screenshots only ever cover the primary display, so the")
                say("     Trade window was never in the frame and nothing here can")
                say("     be measured. Move Cabal to the primary display.")
                return None
        say(f"  words read on screen {len(words)}")
        if not words:
            say("  >> OCR returned nothing at all, on the whole screen. That is")
            say("     Tesseract failing, not the game. Nothing about the window")
            say("     can be concluded from this frame -- check the messages")
            say("     above and that Tesseract is installed.")
            return None
        say(f"  anchors found      {len(found)} of {len(REF_ANCHORS_ALL)} "
            f"({len(REF_ANCHORS)} required, {len(REF_ANCHORS_EXTRA)} "
            f"Register-tab only)")
        for phrase, centre, ref in found:
            say(f"    {phrase!r:12} at {centre}   reference {ref}")
        missing = [p for p, _ in REF_ANCHORS if p not in {f[0] for f in found}]
        say(f"  anchors missing    {', '.join(repr(p) for p in missing)}")
        # Where the missing ones live tells the user which EDGE is the problem.
        low = [p for p in missing
               if dict(REF_ANCHORS)[p][1] > REF_SCREEN[1] // 2]
        if low and len(low) == len(missing):
            say("  >> Every missing anchor sits along the window's lower half.")
            say("     The bottom of the Trade window is off-screen or covered.")
        say("\n  Three anchors are the minimum: with two, an error along the line")
        say("  between them is indistinguishable from a different scale, so the")
        say("  result cannot be checked.")
        say("\n  Re-running --calibrate will read the same screen and fail the")
        say("  same way. Change one of these first:")
        say("    - move the Cabal window fully onto the primary display;")
        say("    - close any overlay covering the Trade window;")
        say("    - drag the Trade window so all four corners are visible.")
        return None

    # Fit origin and scale over EVERY anchor found, then reject on the worst
    # residual.
    #
    # The previous version took the pair with the longest baseline and checked
    # that the two implied origins agreed. That check cannot do what it looks
    # like it does: with `s = |dm| / |dr|`, the vectors `dm` and `s*dr` have
    # equal length by construction, so their difference measures only the ANGLE
    # between them. It validates no scale error and no error along the
    # baseline -- and it threw away every anchor outside the winning pair, so a
    # third anchor flatly contradicting the result was never consulted.
    #
    # A least-squares fit uses all of them and makes disagreement visible:
    # on a clean frame the residuals are 0.0px, and a decoy word pasted on
    # screen produces ~47px. There is no comparable separation available from
    # two points.
    def fit(anchors):
        """Least-squares similarity fit. Returns (data, reason).

        `data` is (scale, ox, oy, residuals, span, allowed); on failure it is
        None and `reason` says why.
        """
        refs = [r for _, _, r in anchors]
        obs = [m for _, m, _ in anchors]
        span = max(math.hypot(a[0] - b[0], a[1] - b[1])
                   for a in refs for b in refs)
        if span < MIN_ANCHOR_BASELINE:
            return None, (f"the anchors found span only {span:.0f}px in the "
                          "reference frame - too close together to measure a "
                          "scale from.")

        # Spread on BOTH axes, not just the longest diagonal. Anchors strung
        # along one line fit perfectly and then extrapolate wildly off it: a
        # set covering 430px of x but only 100px of y passes a max-pairwise
        # span test and was measured producing 40px of click error at the far
        # end of the window - two thirds of a row pitch, i.e. the wrong
        # listing. The diagonal cannot see that; per-axis spread can.
        x_spread = max(r[0] for r in refs) - min(r[0] for r in refs)
        y_spread = max(r[1] for r in refs) - min(r[1] for r in refs)
        if min(x_spread, y_spread) < MIN_ANCHOR_SPREAD:
            return None, (f"the anchors cover {x_spread:.0f}px horizontally "
                          f"and {y_spread:.0f}px vertically; at least "
                          f"{MIN_ANCHOR_SPREAD}px on BOTH axes is needed, or "
                          "the fit is extrapolated off the line they sit on.")

        rx = sum(r[0] for r in refs) / len(refs)
        ry = sum(r[1] for r in refs) / len(refs)
        mx = sum(m[0] for m in obs) / len(obs)
        my = sum(m[1] for m in obs) / len(obs)
        numerator = sum((r[0] - rx) * (m[0] - mx) + (r[1] - ry) * (m[1] - my)
                        for r, m in zip(refs, obs))
        denominator = sum((r[0] - rx) ** 2 + (r[1] - ry) ** 2 for r in refs)
        if denominator <= 0:
            return None, "the anchors found are all in the same place."
        s = numerator / denominator
        if not SCALE_LIMITS[0] <= s <= SCALE_LIMITS[1]:
            return None, (f"measured scale {s:.3f} is outside the plausible "
                          f"range {SCALE_LIMITS} - the anchors were misread.")

        ox_, oy_ = mx - s * rx, my - s * ry
        res = [(name, math.hypot(m[0] - (ox_ + s * r[0]),
                                 m[1] - (oy_ + s * r[1])))
               for (name, m, r) in anchors]
        return (s, ox_, oy_, res, span, max(8.0, 0.01 * span)), ""

    # Drop misread anchors one at a time rather than refusing outright.
    #
    # A single decoy poisons the whole fit: the shared origin and scale shift
    # to split the difference, so every CORRECT anchor is reported as wrong
    # too. Observed on a real 1080p screen -- a stray 'Trade' at (923,909)
    # against its true (478,52) made the four good anchors, which agree with
    # each other to half a pixel, read as 173-244px off. Calibration was
    # impossible with a perfectly measurable window on screen.
    #
    # Dropping is only safe because what remains is re-checked from scratch:
    # the survivors must still span both axes and still agree within the bar.
    # A set that only fits because the disagreeing evidence was discarded
    # fails the spread test instead.
    while True:
        data, reason = fit(found)

        # A GATE failure can also be caused by one bad anchor, so try dropping
        # before giving up -- not only a residual failure.
        #
        # This ordering was the real defect. A single decoy drags the shared
        # scale far enough to fail SCALE_LIMITS, fit() returns None, and the
        # outlier-rejection loop below never runs. Measured on a 1920x1080
        # screen: decoy + three good anchors fits at scale 0.250, outside the
        # (0.4, 2.5) range, so calibration aborted with a message naming no
        # anchor -- while the three good ones alone fit at 0.7607 with a 0.0px
        # residual. The code written to rescue exactly that case was
        # unreachable.
        #
        # Leave-one-out rather than "drop the worst residual", because a failed
        # fit has no residuals to rank. Each candidate is dropped in turn and
        # the survivors re-fitted; a drop is only accepted if what remains
        # passes every gate on its own.
        if data is None and len(found) - 1 >= MIN_ANCHORS_AFTER_DROP:
            best = None
            for candidate in found:
                trial = [e for e in found if e[0] != candidate[0]]
                t_data, _ = fit(trial)
                if t_data is None:
                    continue
                t_worst = max(t_data[3], key=lambda pair: pair[1])[1]
                if t_worst <= t_data[5] and (best is None or t_worst < best[0]):
                    best = (t_worst, candidate[0], trial)
            if best is not None:
                say(f"  {best[1]!r} is inconsistent with the others ({reason}); "
                    f"without it the remaining {len(best[2])} agree to "
                    f"{best[0]:.1f}px. Dropping it and continuing.")
                found = best[2]
                continue

        if data is None:
            # The geometric reason alone leaves the user with nothing to do.
            # Which anchors are MISSING is what tells them what to change, and
            # it is already known here.
            say(f"\nCalibration refused: {reason}")
            missing = [p for p, _ in REF_ANCHORS if p not in {f[0] for f in found}]
            if missing:
                say(f"\n  found   {', '.join(repr(f[0]) for f in found)}")
                say(f"  missing {', '.join(repr(p) for p in missing)}")
                lower = [p for p in missing
                         if dict(REF_ANCHORS)[p][1] > REF_TRADE_SIZE[1] // 2]
                if lower:
                    say(f"  >> {', '.join(repr(p) for p in lower)} sit along the "
                        "Trade window's bottom edge. Missing them is why the "
                        "anchors are all bunched at the top, and it usually "
                        "means the window's lower part is off-screen, covered "
                        "by another window, or below the visible area.")
                say("\n  Fix: make the WHOLE Trade window visible -- all four "
                    "corners -- then re-run --calibrate. Re-running without "
                    "moving anything will read the same screen and refuse "
                    "again.")
            return None
        scale, ox, oy, residuals, span, allowed = data
        worst_name, worst = max(residuals, key=lambda pair: pair[1])
        if worst <= allowed:
            break
        if len(found) - 1 < MIN_ANCHORS_AFTER_DROP:
            # A scalar distance for a two-dimensional problem tells the user
            # nothing they can act on. Everything below is already computed by
            # fit() and was being thrown away: where the anchor was found,
            # where the others say it should be, and whether it lies outside
            # the window they describe -- which is what distinguishes "a
            # DIFFERENT 'Trade' somewhere on screen" from "the window moved".
            say("\nThe anchors disagree about where the Trade window is.")
            say(f"\n  fitted from {len(found)} anchors: origin ({ox:.0f},{oy:.0f}), "
                f"scale {scale:.3f}, span {span:.0f}px")
            say(f"  tolerance {allowed:.0f}px\n")
            by_ref = {name: r for name, _, r in found}
            for name, residual in sorted(residuals, key=lambda p: p[1]):
                where = next(m_ for n_, m_, _ in found if n_ == name)
                say(f"    {name!r:12} off by {residual:7.1f}px   found at {where}")
            # Predict from a fit that EXCLUDES the outlier. Using the fit it
            # corrupted would report a fabricated position with total
            # confidence: on the real 1080p failure, the 4-anchor fit including
            # the decoy gave scale 0.250 and origin (528,293), where the four
            # good anchors alone give scale 0.761 and origin (15,37).
            ref = by_ref[worst_name]
            rest = [e for e in found if e[0] != worst_name]
            clean, _ = fit(rest) if len(rest) >= 3 else (None, "")
            if clean:
                c_scale, c_ox, c_oy = clean[0], clean[1], clean[2]
                say(f"\n  Without {worst_name!r}, the rest agree on origin "
                    f"({c_ox:.0f},{c_oy:.0f}) scale {c_scale:.3f}.")
            else:
                c_scale, c_ox, c_oy = scale, ox, oy
            predicted = (int(c_ox + c_scale * ref[0]),
                         int(c_oy + c_scale * ref[1]))
            say(f"  {worst_name!r} was found at "
                f"{next(m_ for n_, m_, _ in found if n_ == worst_name)}, but they "
                f"put it at {predicted}.")
            win = (int(c_ox), int(c_oy),
                   int(c_ox + c_scale * REF_TRADE_SIZE[0]),
                   int(c_oy + c_scale * REF_TRADE_SIZE[1]))
            actual = next(m_ for n_, m_, _ in found if n_ == worst_name)
            outside = not (win[0] <= actual[0] <= win[2]
                           and win[1] <= actual[1] <= win[3])
            if outside:
                say(f"  That is OUTSIDE the window the others describe "
                    f"({win[0]},{win[1]})-({win[2]},{win[3]}), so it is a "
                    f"different {worst_name!r} on screen -- a chat line, a quest "
                    "panel, another window -- not part of the Trade window.")
                say(f"\n  Fix: close or move whatever shows {worst_name!r} at "
                    f"{actual}, or drag the Trade window clear of it, THEN "
                    "re-run --calibrate.")
            else:
                say("  That is inside the window the others describe, so the "
                    "reading is inconsistent rather than a decoy -- the window "
                    "may have moved while it was being measured. Retry with "
                    "the window held still.")
            say(f"\n  Dropping it would leave {len(found) - 1} anchors, below the "
                f"{MIN_ANCHORS_AFTER_DROP} that must survive for the remaining "
                "fit to be checkable, so this refuses rather than calibrating "
                "from a misread.")
            return None
        say(f"  {worst_name!r} is {worst:.0f}px from where the other "
            f"{len(found) - 1} put it (bar {allowed:.0f}px) - treating it as a "
            "misread and refitting without it.")
        found = [entry for entry in found if entry[0] != worst_name]

    dropped = len(REF_ANCHORS) - len(found)
    return Layout(screen=screen, origin=(int(round(ox)), int(round(oy))),
                  scale=scale, client=client,
                  measured_from=f"{len(found)} anchors fitted, worst residual "
                                f"{worst:.1f}px over a {span:.0f}px span"
                                + (f" ({dropped} not used)" if dropped else ""))


def validate_layout(layout: "Layout", verbose: bool = True) -> bool:
    """Reject a layout whose numbers cannot be right."""
    def say(message: str) -> None:
        if verbose:
            print(message)

    left, top, right, bottom = layout.trade
    if left < 0 or top < 0 or right > layout.screen[0] or bottom > layout.screen[1]:
        say(f"  the Trade window would sit at {layout.trade}, partly off a "
            f"{layout.screen[0]}x{layout.screen[1]} screen.")
        return False
    if right <= left or bottom <= top:
        say("  the derived Trade window has no area.")
        return False
    # The dialog-button boundary must land inside the screen, and to the right
    # of the table's Function column -- excluding that column is its whole job.
    boundary = layout.x(REF_DIALOG_BUTTON_MIN_X - REF_TRADE_ORIGIN[0])
    if not layout.x(REF_FUNCTION_COLUMN_X) < boundary < layout.screen[0]:
        say(f"  the dialog-button boundary ({boundary}) does not sit between "
            f"the Function column and the right edge of the screen.")
        return False
    # Check the mapped regions have area. These come from the geometry table
    # now, not from Layout properties -- validate_layout still called a
    # `layout.npc_search` property after it was removed, which is an
    # AttributeError on the only path every clicking command goes through.
    for name in ("NPC_SEARCH_REGION", "POPUP_REGION", "TRADE_WINDOW_SEARCH"):
        ref = _REFERENCE_GEOMETRY.get(name)
        if ref is None:
            continue
        box = _clamp_box(layout.box(ref), layout.screen)
        if box[2] <= box[0] or box[3] <= box[1]:
            say(f"  the derived {name} has no area.")
            return False
    return True


def save_calibration(layout: "Layout", verbose: bool = True) -> bool:
    try:
        CALIBRATION_FILE.write_text(json.dumps({
            "screen": list(layout.screen),
            "origin": list(layout.origin),
            "scale": layout.scale,
            "client": list(layout.client) if layout.client else None,
            "measured_from": layout.measured_from,
        }, indent=2), encoding="utf-8")
        if verbose:
            print(f"Saved calibration to {CALIBRATION_FILE}")
        return True
    except OSError as exc:
        if verbose:
            print(f"Could not save the calibration ({exc}); it will have to be "
                  "measured again next run.", file=sys.stderr)
        return False


def load_calibration(verbose: bool = True) -> "Layout | None":
    """DEPRECATED and deliberately uncalled. See ensure_calibrated.

    Kept only so a saved calibration can be inspected by hand. Nothing in the
    run reads it: the layout is measured fresh every time, because a stored one
    cannot detect the Trade window having been dragged within an unchanged
    client, and a stale layout clicks confidently in the wrong place.
    """
    """The stored calibration, if it still matches this machine."""
    try:
        data = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
        layout = Layout(screen=tuple(data["screen"]), origin=tuple(data["origin"]),
                        scale=float(data["scale"]),
                        client=tuple(data["client"]) if data.get("client") else None,
                        measured_from=data.get("measured_from", "stored"))
    except (OSError, ValueError, KeyError, TypeError):
        return None

    # A stored calibration is only valid for the screen it was taken on, and
    # only while the game window has not moved or been resized. Using a stale
    # one is worse than having none: it clicks confidently in the wrong place.
    current = current_screen_size()
    if current and tuple(layout.screen) != current:
        if verbose:
            print(f"Stored calibration was taken at {layout.screen[0]}x"
                  f"{layout.screen[1]} but this screen is {current[0]}x"
                  f"{current[1]} - re-measuring.")
        return None
    now = client_rect()
    if layout.client and now and tuple(layout.client) != tuple(now):
        if verbose:
            print("The game window has moved or been resized since the last "
                  "calibration - re-measuring.")
        return None
    return layout


def current_screen_size() -> tuple[int, int] | None:
    """Physical pixels of the primary display, or None if it cannot be read."""
    try:
        make_dpi_aware()
        with mss.mss() as sct:
            region, _ = resolve_monitor(sct, "primary")
            return (int(region["width"]), int(region["height"]))
    except Exception:  # noqa: BLE001 - never let a probe stop the run
        return None


def calibrate(verbose: bool = True, save: bool = True) -> bool:
    """Measure this machine's layout and apply it. False if it could not be."""
    def say(message: str) -> None:
        if verbose:
            print(message)

    say("Calibrating against the current screen and game window...")
    screen = current_screen_size()
    if screen:
        say(f"  screen {screen[0]}x{screen[1]}")
    rect = client_rect()
    say(f"  game client area: {rect if rect else 'not found'}")

    if not trade_window_open():
        say("  the Trade window is not open. Calibration measures it, so open "
            "the Agent Shop first (or run --open) and try again.")
        return False

    layout = measure_layout(verbose=verbose)
    if layout is None:
        return False
    if not validate_layout(layout, verbose=verbose):
        return False

    apply_layout(layout)
    say(f"Calibrated: {layout.describe()}")
    say(f"  Trade window   {LAYOUT.trade}")
    say(f"  Register panel {REGISTER_PANEL}")
    say(f"  Dialog buttons right of x={DIALOG_BUTTON_MIN_X}")
    say(f"  NPC search     {NPC_SEARCH_REGION}")
    if save:
        save_calibration(layout, verbose=verbose)
    return True


def ensure_calibrated(verbose: bool = True, required: bool = True) -> bool:
    """Measure the layout fresh before anything is clicked.

    A stored calibration is deliberately NEVER reused. It cannot be trusted:
    the Trade window is draggable within an unchanged client, so the staleness
    test (screen size + client rect) misses the one number the layout is
    actually about -- its origin. A stale layout does not fail loudly, it
    clicks confidently in the wrong place, which is the failure this whole
    layer exists to prevent.

    Measuring costs a few seconds of OCR per run against that risk, so it is
    measured every time. `save_calibration` still records what was measured,
    for diagnosis; nothing reads it back.

    `required=False` is for read-only commands, which are allowed to run on the
    reference defaults and simply be wrong about where things are.
    """
    if calibrate(verbose=verbose):
        return True

    # The monitor matching is NOT enough: the built-in coordinates assume the
    # reference CLIENT too, and the game can be windowed. On a 1920x1080 window
    # inside a 2560x1440 desktop -- the setup this was actually run on -- the
    # monitor matches while every coordinate is wrong, and the failure is
    # silent: the table read adapts and looks healthy, but dialog_button's
    # distance filter then returns a TABLE ROW's Receive button, so --confirm
    # collects a sale on a row nobody asked about.
    screen = current_screen_size()
    client = client_rect()
    if (screen and tuple(screen) == REF_SCREEN
            and client and tuple(client) == REF_CLIENT):
        if verbose:
            print("Could not calibrate, but this screen and game window match "
                  "the reference the coordinates were measured on, so the "
                  "built-in values are used.")
        return True
    # DO NOT diagnose here. calibrate() has just run and printed exactly why it
    # failed; anything invented at this point is a guess layered over evidence.
    # The old text asserted a resolution mismatch as the cause, which produced
    # the literal "This screen is 2560x1440 but the built-in coordinates were
    # measured at 2560x1440" whenever the CLIENT rect was the thing that
    # differed -- self-contradictory, and it overwrote the accurate message
    # printed immediately above it.
    if not required:
        return False
    if not verbose:
        return False

    print("\nRefusing to click without a calibration.")
    if not screen:
        print("The screen size could not be determined.")
        return False

    if tuple(screen) == REF_SCREEN and client and tuple(client) != REF_CLIENT:
        # Specific, true, and different from "your resolution is wrong".
        print(f"The display matches the reference ({screen[0]}x{screen[1]}), but "
              f"the game window is {client[2] - client[0]}x{client[3] - client[1]} "
              f"at ({client[0]},{client[1]}) where the built-in coordinates "
              f"assume {REF_CLIENT[2] - REF_CLIENT[0]}x"
              f"{REF_CLIENT[3] - REF_CLIENT[1]} at "
              f"({REF_CLIENT[0]},{REF_CLIENT[1]}), so they do not apply.")
    elif tuple(screen) != REF_SCREEN:
        print(f"This screen is {screen[0]}x{screen[1]} and the built-in "
              f"coordinates were measured at {REF_SCREEN[0]}x{REF_SCREEN[1]}, "
              "so they point at the wrong pixels here.")
    print("A click that misses the UI lands in the game world, which moves "
          "your character -- so nothing will be clicked.")
    print("\nCalibration was attempted just now and failed for the reason "
          "printed above. Running --calibrate again WITHOUT CHANGING ANYTHING "
          "will read the same screen and fail identically.")
    return False


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Run logging
# --------------------------------------------------------------------------
#
# Every run writes its own file. This exists because a five-hour unattended run
# died in the small hours and left NOTHING to diagnose from: the frame index
# stopped mid-cycle, no abort was recorded, and the console output was gone
# with the terminal. The screenshots showed a completely healthy screen one
# second before the end, so the failure was in code -- and nothing had captured
# it.
#
# Three properties matter more than tidiness here:
#
#   * one file per run, named by start time, so a crash is never overwritten by
#     the restart that follows it;
#   * flushed on EVERY line, because the interesting content is whatever was
#     written immediately before the process stopped, and a buffer is exactly
#     what is lost when it stops;
#   * incapable of breaking the run. Logging that can raise would become a new
#     failure mode in a script whose whole problem is unexplained failures.

LOG_DIR = SCRIPT_DIR / "logs"
_log_handle = None
# Set at import, not in start_run_log, so the duration covers the whole process
# even on paths that never reach the logging setup -- an argument error, or an
# exception during import of something below this point.
_RUN_STARTED = time.monotonic()
_RUN_STARTED_AT = datetime.now()
_run_finished = False


class _Tee:
    """Write to the console and the run log at once.

    Wraps a stream rather than replacing it, so anything already holding a
    reference to sys.stdout keeps working. Every write is flushed: the lines
    that matter are the last ones before a crash.
    """

    def __init__(self, stream, handle):
        self._stream = stream
        self._handle = handle
        self._started = time.monotonic()
        self._last = self._started
        self._at_line_start = True

    def _prefix(self) -> str:
        """`[ elapsed +delta]` for the line about to be written.

        Both figures come from time.monotonic(), never the wall clock. This
        machine keeps bad time, and a duration computed from a clock that jumps
        is worse than no duration at all -- it looks authoritative and is
        wrong. monotonic() is unaffected by the clock being wrong, by NTP
        correcting it, or by daylight saving.

        `elapsed` is seconds since the log opened; `delta` is seconds since the
        previous line, which is what actually answers "how long did that step
        take" -- the gap BEFORE a line is the work that produced it.
        """
        now = time.monotonic()
        delta = now - self._last
        self._last = now
        return f"[{now - self._started:8.1f} +{delta:6.1f}] "

    def _stamped(self, text: str) -> str:
        """`text` with a timing prefix on each line that starts one.

        Applied to the FILE only, never to the console: the console is read
        live and the prefixes would just be noise, and -- more importantly --
        the failpath suites capture stdout and assert on exact wording. Adding
        eighteen characters to every captured line would break them for a
        reason that has nothing to do with what they test.

        Written a line at a time rather than per write() call because print()
        emits the text and the newline separately, so "is this the start of a
        line" is state, not something a single call can see.
        """
        out = []
        for piece in text.splitlines(keepends=True):
            if self._at_line_start and piece.strip():
                # Blank separator lines are left bare. There are a lot of them
                # and a stamped empty line is just a wall of brackets.
                out.append(self._prefix())
            out.append(piece)
            self._at_line_start = piece.endswith(("\n", "\r"))
        return "".join(out)

    def write(self, text):
        self._stream.write(text)
        try:
            self._stream.flush()
        except Exception:      # noqa: BLE001 - a console that will not flush
            pass               # must not stop the log being written
        try:
            self._handle.write(self._stamped(text))
            self._handle.flush()
        except Exception:      # noqa: BLE001 - logging never breaks the run
            pass
        return len(text)

    def flush(self):
        for target in (self._stream, self._handle):
            try:
                target.flush()
            except Exception:  # noqa: BLE001
                pass

    def isatty(self):
        try:
            return self._stream.isatty()
        except Exception:      # noqa: BLE001
            return False

    def __getattr__(self, name):
        return getattr(self._stream, name)


def start_run_log(argv: list[str]) -> "Path | None":
    """Begin a per-run log. Returns its path, or None if it could not start.

    Installs an excepthook as well as the tee: an uncaught exception is exactly
    the case this is for, and by default Python prints the traceback and exits
    without anything else seeing it.
    """
    global _log_handle
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        started = datetime.now()
        path = LOG_DIR / f"run_{started:%Y-%m-%d_%H%M%S}.log"
        # Never clobber: two runs started inside the same second would
        # otherwise share a file, and one of them is probably the crash.
        n = 1
        while path.exists():
            n += 1
            path = LOG_DIR / f"run_{started:%Y-%m-%d_%H%M%S}_{n}.log"
        _log_handle = path.open("w", encoding="utf-8", buffering=1)
        _log_handle.write(
            f"=== trade.py run log ===\n"
            f"started   {started.isoformat(timespec='seconds')}\n"
            f"command   {' '.join(argv)}\n"
            f"script    {Path(__file__).resolve()}\n"
            f"python    {sys.version.split()[0]}\n"
            f"cwd       {Path.cwd()}\n"
            f"timing    [elapsed +delta] seconds, from a MONOTONIC clock.\n"
            f"          delta is the gap since the previous line -- i.e. how\n"
            f"          long the step that produced this line took.\n"
            f"{'=' * 60}\n")
        _log_handle.flush()
        sys.stdout = _Tee(sys.stdout, _log_handle)
        sys.stderr = _Tee(sys.stderr, _log_handle)

        def log_uncaught(kind, value, tb):
            # The whole point: a crash must leave a traceback behind. Written
            # through the raw handle as well as stderr, in case the tee is the
            # thing that broke.
            try:
                text = "".join(traceback.format_exception(kind, value, tb))
                _log_handle.write(f"\n=== UNCAUGHT {kind.__name__} ===\n{text}")
                _log_handle.write(f"ended     {datetime.now().isoformat(timespec='seconds')}\n")
                _log_handle.flush()
            except Exception:  # noqa: BLE001
                pass
            sys.__excepthook__(kind, value, tb)

        sys.excepthook = log_uncaught
        return path
    except Exception:          # noqa: BLE001 - a run without a log is still a run
        return None


def _format_duration(seconds: float) -> str:
    """A duration a human reads at a glance: '2h 14m 07s', '3m 21s', '4.2s'."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def finish_run_log(note: str = "") -> None:
    """Report how long the run lasted, to the console AND the log.

    Runs on EVERY termination -- a clean finish, sys.exit, Ctrl+C, an uncaught
    exception -- because it is registered with atexit as well as being called
    from the __main__ guard. `_run_finished` makes it idempotent so the two
    routes cannot both print.

    The duration is printed to the console even when there is no log file: the
    question "how long did it actually run before it died?" is the first one
    asked after any unattended failure, and until now the only way to answer it
    was to subtract timestamps out of the frame index.

    Deliberately never raises. This is the last thing a dying process does, and
    an exception here would replace the real cause of death with its own.
    """
    global _run_finished
    if _run_finished:
        return
    _run_finished = True
    try:
        # The tally FIRST, and in its own try: it is the part most worth having
        # after a Ctrl+C, and a fault formatting it must not cost the duration
        # line that every post-mortem starts from.
        try:
            report = sales_report()
            if report:
                print(report)
            # Money out as well as in. Printed after the per-item breakdown so
            # the last thing on screen is the figure that matters.
            money = profit_report()
            if money:
                print(money)
        except Exception:      # noqa: BLE001
            pass

        ended = datetime.now()
        elapsed = time.monotonic() - _RUN_STARTED
        line = (f"Ran for {_format_duration(elapsed)}  "
                f"({_RUN_STARTED_AT:%H:%M:%S} -> {ended:%H:%M:%S})"
                + (f"  [{note}]" if note else ""))
        # print() already tees into the log, so writing `line` to the handle as
        # well duplicated it in every file. Only the closing marker -- which is
        # not printed to the console -- goes direct.
        print(f"\n{line}")
        if _log_handle is not None:
            _log_handle.write(f"{'=' * 60}\n"
                              f"ended     {ended.isoformat(timespec='seconds')}\n")
            _log_handle.flush()
    except Exception:          # noqa: BLE001
        pass


def main() -> None:
    p = argparse.ArgumentParser(
        description="Cabal Online automation: capture, Alz and the Agent Shop.")
    p.add_argument("--shot", action="store_true",
                   help="capture the screen to the screenshots folder")
    p.add_argument("--alz", action="store_true",
                   help="read the Alz balance from the screen")
    p.add_argument("--monitor", "-m", default="primary",
                   help="with --shot: 'primary', 'all', or a 1-based monitor number")
    p.add_argument("--monitors", action="store_true",
                   help="list the detected monitors and exit")
    p.add_argument("--outdir", type=Path, default=None,
                   help="with --shot: folder to save into")
    p.add_argument("--keep", type=int, default=1, metavar="N",
                   help="with --shot: how many recent captures to retain")
    p.add_argument("--no-prune", action="store_true",
                   help="with --shot: keep every capture instead of pruning")
    p.add_argument("--no-alz", action="store_true",
                   help="with --shot: skip reading the Alz balance")
    p.add_argument("--no-record", action="store_true",
                   help="do not save frames to unit_tests/corpus during the run")
    p.add_argument("--record", action="store_true",
                   help="save frames even for read-only commands")
    p.add_argument("--calibrate", action="store_true",
                   help="measure this machine's screen and game window, and "
                        "remember the layout (open the Agent Shop first)")
    p.add_argument("--no-calibrate", action="store_true",
                   help="skip the layout check and use the built-in 2560x1440 "
                        "coordinates as-is")
    p.add_argument("--cancel", type=int, metavar="ROW",
                   help="cancel the listing on this 1-based table row")
    p.add_argument("--list", action="store_true",
                   help="show the listings currently visible")
    p.add_argument("--relist-rows", nargs="+", metavar="SPEC",
                   help="relist several rows, e.g. --relist-rows 1-10 or 1 3 5; "
                        "each is tracked by name, since rows renumber as you "
                        "go. 'all' sweeps the whole shop by scrolling and "
                        "relists every listing, however many there are")
    p.add_argument("--relist", type=int, nargs="+", metavar="N",
                   help="cancel a row then re-list it: --relist ROW [INV_ROW INV_COL]; "
                        "without a slot it follows the item to wherever it lands")
    p.add_argument("--do", nargs="+", metavar="ACTION",
                   help="run actions back to back, e.g. --do \"cancel 3\" \"register 1 1\"")
    p.add_argument("--repeat", nargs="+", metavar="ACTION",
                   help="repeat actions on a timer; needs --for and --every, "
                        "e.g. --repeat \"relist 1\" --for 60 --every 5")
    p.add_argument("--for", dest="duration", type=float, metavar="MINUTES",
                   help="with --repeat, how long to keep looping")
    p.add_argument("--every", type=float, metavar="MINUTES",
                   help="with --repeat, how often to run the actions; "
                        "0 starts each cycle as soon as the last one finishes")
    p.add_argument("--register", type=int, nargs=2, metavar=("ROW", "COL"),
                   help="list the item in this inventory slot on the Agent Shop")
    p.add_argument("--load", type=int, nargs=2, metavar=("ROW", "COL"),
                   help="Ctrl+Click an inventory slot into the shop slot, then stop")
    p.add_argument("--panel", action="store_true",
                   help="read the Register panel's price and quantity")
    p.add_argument("--clear", action="store_true",
                   help="close any dialog and return the shop slot item to the inventory")
    p.add_argument("--confirm", action="store_true",
                   help="click Confirmation through any dialogs left open by a stopped run")
    p.add_argument("--open", action="store_true",
                   help="reopen the Trade window via the Agent Shop NPC, on the Register tab")
    p.add_argument("--reset", action="store_true",
                   help="Escape out, reopen the shop and clear the slot - the "
                        "recovery a failed cycle performs")
    p.add_argument("--words", action="store_true",
                   help="dump every word OCR sees in the trade window")
    p.add_argument("--sales", nargs="?", const="24", metavar="HOURS",
                   help="print collections recorded in sales.db over the last "
                        "N hours (default 24, 'all' for everything). Reads the "
                        "database only -- safe while a run is going.")
    p.add_argument("--listings", action="store_true",
                   help="scroll the whole shop and list every listing with " 
                        "its absolute position (reads and scrolls only)")
    p.add_argument("--scroll", type=int, metavar="NOTCHES",
                   help="probe: turn the wheel over the listings table by N "
                        "notches (negative scrolls down) and report how far "
                        "the rows actually moved. Nothing else uses scrolling "
                        "yet")
    p.add_argument("--max-qty", action="store_true",
                   help="with --register, set the quantity to the maximum available")
    # default=None, not 0: the numeric validation below rejects non-positive
    # values, and a default of 0 made it fire on EVERY invocation -- so every
    # command exited 2 with "--floor must be a positive number (got 0)".
    # Every consumer already short-circuits on a falsy floor.
    p.add_argument("--floor", type=int, default=None, metavar="ALZ",
                   help="with --register, abort if the suggested price is below this")
    p.add_argument("--price", type=int, default=None, metavar="ALZ",
                   help="with --register, list at exactly this price")
    p.add_argument("--qty", type=int, default=None, metavar="N",
                   help="with --register, list exactly this quantity")
    p.add_argument("--cost-floor", dest="cost_floor", action="store_true",
                   default=None,
                   help="refuse to relist below what the stock cost "
                        f"(default: {'on' if COST_FLOOR_ON_RELIST else 'off'})")
    p.add_argument("--no-cost-floor", dest="cost_floor", action="store_false",
                   help="allow relisting below what the stock cost, so a "
                        "position whose market has fallen still moves. Does "
                        "NOT affect ITEM_PRICE_FLOORS, which always apply")
    p.add_argument("--buy", action="store_true",
                   help="restock sold-out Cores: buy Sets, convert them, "
                        "list the result (see RESTOCK_TARGET)")
    p.add_argument("--buy-target", type=int, default=RESTOCK_TARGET,
                   metavar="N",
                   help=f"Sets to accumulate per restock (default "
                        f"{RESTOCK_TARGET}; NOT one row's worth -- a row holds "
                        f"{CONVERT_QUANTITY}, and buying overshoots because a "
                        f"Set stacks to {SET_STACK_MAX})")
    p.add_argument("--dry-run", action="store_true",
                   help="locate everything but do not click")
    args = p.parse_args()

    # Read-only commands first: these need no elevation and no game state.
    if args.sales:
        # Reads the database only -- no capture, no input. Safe to run while a
        # run is going, which is the point: the rows are committed as each
        # collection happens rather than at the end.
        spec = str(args.sales).strip().lower()
        hours = None if spec in ("all", "0") else float(spec)
        rows = sales_since(hours=hours, limit=SALES_QUERY_LIMIT)
        window = "all time" if hours is None else f"the last {hours:g}h"
        if not rows:
            print(f"No collections recorded in {window} ({SALES_DB}).")
            sys.exit(0)
        gross = sum(r[3] or 0 for r in rows)
        unmeasured = sum(1 for r in rows if not r[3])
        print(f"{len(rows)} collection(s) over {window}, "
              f"{gross:,} Alz measured")
        print(f"{'when':20} {'item':34} {'qty':>6} {'proceeds':>15}")
        print("-" * 78)
        for at, item, price, proceeds, qty, note in rows:
            shown = item if len(item) <= 34 else item[:31] + "..."
            print(f"{at:20} {shown:34} {(qty if qty else '-')!s:>6} "
                  f"{(format(proceeds, ',') if proceeds else '-'):>15}"
                  + (f"   [{note}]" if note else ""))
        if unmeasured:
            print(f"\n{unmeasured} of these could not be measured, so the "
                  f"gross above is a floor, not the whole of it.")
        sys.exit(0)

    if args.monitors:
        make_dpi_aware()
        with open_capture() as sct:
            list_monitors(sct)
        return

    if args.shot:
        make_dpi_aware()
        png, width, height, label, _ = take_screenshot(args.monitor)
        outdir = args.outdir or DEFAULT_OUTDIR
        outdir.mkdir(parents=True, exist_ok=True)
        path = unique_path(
            outdir / f"screenshot_{datetime.now():%Y-%m-%d_%H%M%S}_{label}.png")
        path.write_bytes(png)
        if not args.no_prune:
            removed = prune_screenshots(outdir, args.keep, path)
            if removed:
                print(f"Pruned {removed} older screenshot(s), keeping {args.keep}.")
        print(f"Saved {width}x{height} screenshot to: {path}")
        if not args.no_alz:
            alz = get_alz(path)
            print(f"Alz: {alz:,}  ({human(alz)})" if alz
                  else "Alz: 0 (Inventory panel not visible)")
        return

    if args.alz:
        alz = get_alz(grab())
        print(f"{alz:,} Alz  ({human(alz)})" if alz else "0 Alz (Inventory panel not visible)")
        return

    # Validate before the elevation check, so a bad argument reads as one.
    if args.repeat:
        if args.duration is None or args.every is None:
            p.error("--repeat needs both --for MINUTES and --every MINUTES")
        if args.duration <= 0:
            p.error("--for must be positive")
        if args.every < 0:
            p.error("--every cannot be negative; use --every 0 to start each "
                    "cycle as soon as the last one finishes")
        # 0 is the "no wait" case, not a too-short interval, so it skips the
        # comparison below -- which would otherwise never trigger anyway.
        if args.every and args.every > args.duration:
            p.error(f"--every {args.every:g} is longer than --for {args.duration:g}, "
                    "so the actions would run once at most")

    # Validate every numeric option here, before the elevation gate, so a bad
    # argument reads as a bad argument. These used to fail deep inside a run:
    # type_number(-5000) does int('-') and raises ValueError, which no handler
    # catches -- after the item was already sitting in the shop slot.
    for name, value in (("--price", args.price), ("--qty", args.qty),
                        ("--floor", args.floor), ("--cancel", args.cancel)):
        if value is not None and value <= 0:
            p.error(f"{name} must be a positive number (got {value})")
    for name, pair in (("--register", args.register), ("--load", args.load)):
        if pair and not all(1 <= v <= GRID_SIZE for v in pair):
            p.error(f"{name} takes ROW COL, each between 1 and {GRID_SIZE}")
    if args.relist is not None:
        if len(args.relist) not in (1, 3):
            p.error("--relist takes ROW, or ROW INV_ROW INV_COL")
        if args.relist[0] <= 0:
            p.error("--relist ROW must be positive")
        if len(args.relist) == 3 and not all(1 <= v <= GRID_SIZE
                                             for v in args.relist[1:]):
            p.error(f"--relist's INV_ROW and INV_COL must be 1..{GRID_SIZE}")
    if args.relist_rows is not None:
        try:
            parse_row_spec(args.relist_rows)
        except ValueError as exc:
            p.error(f"bad --relist-rows spec: {exc}")

    # These five have no dry-run mode -- they click unconditionally -- so they
    # must NOT be exempted by --dry-run. Exempting them dropped the elevation
    # check while the clicks still went through.
    # --calibrate belongs here: it focuses the game and parks the cursor, so it
    # injects input and must face the elevation check like every other command
    # that does. Without it the run died on an uncaught PermissionError instead
    # of printing the "run as Administrator" message.
    # `is not None` for --scroll: 0 is a legitimate no-op probe, and `or 0` is
    # falsy, which would let it through the gate ungated.
    always_clicks = (args.load is not None or args.clear or args.confirm
                     or args.open or args.reset or args.calibrate
                     or args.scroll is not None or args.listings)
    honours_dry_run = (args.cancel is not None or args.register is not None
                       or args.relist is not None
                       or args.relist_rows is not None
                       or args.do is not None
                       or args.repeat is not None)
    # Suppress input at the SOURCE for a dry run, not just at the call sites
    # that remembered to ask. Only commands that honour --dry-run at all are
    # covered: --shot, --calibrate and friends are read-only anyway, and the
    # ones that always click (--confirm, --clear) do not accept it.
    #
    # Without this, --dry-run meant "the acting functions do not click" while
    # the scroll primitives underneath them still drove the game. That is how
    # `--relist-rows all --dry-run` zoomed the camera and ended a live run.
    if honours_dry_run and args.dry_run:
        global NO_INPUT
        NO_INPUT = True

    # A module flag rather than a threaded argument, same reasoning as --buy
    # below. `default=None` on the pair is what makes "not mentioned" distinct
    # from "explicitly off", so the constant stays the single place the default
    # lives instead of being restated in the parser.
    # Assigned through globals() rather than a `global` statement because the
    # parser above already READS this constant to print its own default, and a
    # `global` declaration may not follow a use in the same function.
    if args.cost_floor is not None:
        globals()["COST_FLOOR_ON_RELIST"] = bool(args.cost_floor)

    # --buy is a module flag rather than an argument threaded through
    # relist_rows, for the same reason NO_INPUT is: a new caller cannot forget
    # to pass a global. It spends real money, so it is off unless asked for.
    if args.buy:
        global BUY_ENABLED, BUY_TARGET
        BUY_ENABLED = True
        BUY_TARGET = args.buy_target
        # Which Cores may be bought is ENABLE_BUYING's business, not a flag's.
        # Checked here so a typo in that table stops the run at the start,
        # rather than reading as "this Core is disabled" and quietly never
        # restocking anything.
        allowed = enabled_buying_slots()
        # Same reasoning for the per-item saving floors: an unmatched key there
        # reads as "this item is back on the default", which is invisible.
        validate_price_diff_floors()

        # Every managed Core, ON and off alike, with a count.
        #
        # The banner used to name only the enabled ones, which answers "what
        # will this spend on" but not "what did I think would be spending".
        # Those differ exactly when something is off by accident, and a Core
        # switched off reads identically to a Core that never sells -- the shop
        # simply runs dry and nothing says why. An off row costs one line and
        # makes that visible at the top of the log, where it is read.
        managed = managed_core_slots()
        print(f"--buy is ON: {len(allowed)} of {len(managed)} managed Core(s) "
              f"enabled for resupply. A sell-out triggers buy -> convert -> "
              f"list, holding a minimum of {RESTOCK_TARGET} Sets and no more "
              f"than {BUY_MAXIMUM}.")
        for slot in managed:
            name = FAVOURITE_SLOTS[slot]
            if slot in allowed:
                # The saving is per item and they differ, so it is spelled out
                # per row. A single figure in the banner while the code used
                # several would be worse than none.
                saving = price_diff_floor_for(name)
                print(f"    ON   slot {slot:>2}  {name:26} "
                      f"saving required {saving:>9,}"
                      + ("" if saving == PRICE_DIFF_FLOOR
                         else f"  (default {PRICE_DIFF_FLOOR:,})"))
            else:
                print(f"    off  slot {slot:>2}  {name:26} "
                      f"ENABLE_BUYING is False; it will never be restocked")
        if not allowed:
            print("    NOTHING will be bought: every entry in ENABLE_BUYING "
                  "is off, so --buy has no effect this run.")


    clicking = always_clicks or (honours_dry_run and not args.dry_run)
    if clicking and not is_elevated():
        sys.exit(
            "Refusing to click: not running as Administrator.\n"
            "Cabal runs elevated, so once it holds the foreground Windows blocks "
            "our input entirely -- the click would move the cursor and do nothing "
            "else, leaving a stray tooltip on screen.\n"
            "Open an Administrator PowerShell and rerun, or add --dry-run."
        )

    # Record by default whenever the run is going to act. Those are the frames
    # worth having -- a live run reaches states that sitting and capturing
    # never will, and the ones where it aborts are the most useful of all.
    global RECORD_ENABLED
    RECORD_ENABLED = (args.record or clicking) and not args.no_record
    if RECORD_ENABLED:
        print(f"Recording frames to {RECORD_DIR} (--no-record to turn off).")

    if args.calibrate:
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        sys.exit(0 if calibrate() else 1)

    # Establish the layout before ANY coordinate is read. Every region in this
    # file was measured at 2560x1440; on a different screen they point at the
    # wrong pixels, and for a clicking command that means hitting the game
    # world instead of the UI. Read-only commands are allowed to proceed
    # uncalibrated -- they can only report something wrong, not do something
    # wrong -- but they still try, so --list and --panel work on any machine.
    #
    # --open is exempt: calibration measures the Trade window, so it needs the
    # shop open, and --open is what opens it. Gating it made the two commands
    # refuse each other in a loop on any non-reference screen -- "run
    # --calibrate" / "the Trade window is not open, run --open" -- with no way
    # out. It only needs the NPC regions, and it is the bootstrap command.
    if not args.no_calibrate and not args.open:
        if not ensure_calibrated(required=clicking):
            sys.exit(1)

        # Free space, once, now that the layout is known and before anything is
        # touched. Only for commands that will actually cancel something: a
        # cancelled stack is what needs the room, and gating --list or --shot on
        # it would be nonsense.
        #
        # This is the check whose absence cost two runs on 2026-08-05. The game
        # refuses a cancellation outright when the stack will not fit, the
        # refusal is identical every time, and nothing the script does can free
        # a slot -- so it failed the same row every cycle until the breaker
        # stopped it. One line up front replaces an hour of that.
        # The free-space check does NOT live here.
        #
        # It did, and it refused every run outright: at this point only the
        # layout is known. The Inventory panel may not be open yet, so
        # select_inventory_tab has nothing to click, and the check failed
        # closed on a game that was merely not ready rather than short of
        # space. It now runs inside relist_rows, straight after
        # ensure_shop_ready -- the same place require_empty_work_tab has always
        # worked from, and the first moment the inventory is reliably there.
        pass
    elif args.no_calibrate and clicking:
        screen = current_screen_size()
        if screen and tuple(screen) != REF_SCREEN:
            print(f"WARNING: --no-calibrate on a {screen[0]}x{screen[1]} screen. "
                  f"The built-in coordinates were measured at "
                  f"{REF_SCREEN[0]}x{REF_SCREEN[1]}, so clicks will land in the "
                  "wrong place.", file=sys.stderr)

    if args.panel:
        print(read_register_panel(grab()))
        return

    if args.reset:
        ok = prepare_for_actions()
        print(f"trade open: {trade_window_open()}, "
              f"register tab: {register_tab_open()}, "
              f"shop slot: {read_register_panel(grab())['qty_text']!r}")
        sys.exit(0 if ok else 1)

    if args.open:
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        ok = open_trade_window()
        print(f"trade window open: {trade_window_open()}, "
              f"register tab: {register_tab_open()}")
        sys.exit(0 if ok else 1)

    if args.confirm:
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        ok = confirm_open_dialogs()
        print(f"shop slot: {read_register_panel(grab())['qty_text']!r}, "
              f"loaded={read_register_panel(grab())['loaded']}")
        sys.exit(0 if ok else 1)

    if args.clear:
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        if dialog_kind(grab()) is not None and not close_any_dialog():
            sys.exit("Could not close the open dialog.")
        if not clear_shop_slot():
            sys.exit("Could not clear the shop slot.")
        print(f"Shop slot clear: {read_register_panel(grab())['qty_text']}")
        return

    if args.load:
        row, col = args.load
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        before = read_register_panel(grab())
        if before["loaded"]:
            sys.exit(f"The shop slot already holds an item ({before}). Clear it first.")
        centre = slot_centre(row, col)
        print(f"Ctrl+Click inventory slot ({row},{col}) at {centre}")
        ctrl_click(*centre)
        time.sleep(0.8)
        park_cursor()
        after = read_register_panel(grab())
        print(f"panel after: {after}")
        if not after["loaded"]:
            sys.exit("Nothing was loaded into the shop slot.")
        return

    if args.listings:
        # Walks the whole shop one row at a time and prints it with ABSOLUTE
        # positions. Still read-only in effect: it scrolls and reads, and
        # touches nothing. This is the foundation relisting past row 10 needs,
        # exercised on its own before anything acts on it.
        found = enumerate_listings()
        if found is None:
            print("\nCould not enumerate the shop. Nothing was acted on.")
            sys.exit(1)
        floors = 0
        print(f"\n{'#':>3}  {'action':8} {'name':44} {'qty':>5} {'price':>14}")
        for index, row in found:
            floor = item_price_floor(row.name)
            if floor:
                floors += 1
            print(f"{index:3d}  {row.action:8} {row.name[:44]:44} "
                  f"{str(row.qty):>5} {money(row.price):>14}"
                  + (f"   floor {floor:,}" if floor else ""))
        live = [r for _, r in found if r.action in ("change", "receive")]
        print(f"\n{len(found)} listing(s), {len(live)} live, "
              f"{len(found) - EXPECTED_ROWS} beyond the first screen, "
              f"{floors} carrying a price floor")
        sys.exit(0)

    if args.scroll is not None:
        # Deliberately a probe, not a feature. Nothing in the relist path uses
        # scrolling yet, because the hard part is not turning the wheel -- it
        # is knowing WHICH listings you are looking at afterwards. read_rows
        # numbers rows by screen position, so once the view moves, "row 1"
        # is a different listing and every caller that acts on an index is
        # acting on the wrong one. This measures the behaviour first.
        before = grab()
        rows_before = read_rows(before)
        print(f"before: {len(rows_before)} rows")
        for r in rows_before:
            print(f"   {r.index:2d} [{r.action:8}] {r.name[:38]:40} "
                  f"x{str(r.qty):>4} {r.price}  band {r.top}-{r.bottom}")
        centre = ((TRADE_REGION[0] + TRADE_REGION[2]) // 2,
                  (TRADE_REGION[1] + TRADE_REGION[3]) // 2)
        print(f"\nscrolling {args.scroll:+d} notch(es) at {centre}")
        record("scroll.before", before, notches=args.scroll)
        scroll_wheel(*centre, args.scroll)
        time.sleep(0.8)
        park_cursor()
        after = grab()
        rows_after = read_rows(after)
        record("scroll.after", after, notches=args.scroll)
        print(f"\nafter: {len(rows_after)} rows")
        for r in rows_after:
            print(f"   {r.index:2d} [{r.action:8}] {r.name[:38]:40} "
                  f"x{str(r.qty):>4} {r.price}  band {r.top}-{r.bottom}")

        # How far did it move? Recovered from CONTENT, never from the notch
        # count -- the wheel-to-lines ratio is the game's business and may not
        # even be constant. Identity here is (name, price, qty).
        def key(r):
            return (r.name, r.price, r.qty)
        b = [key(r) for r in rows_before]
        a = [key(r) for r in rows_after]
        # EVERY offset that fits, not the first. With duplicate listings more
        # than one can fit, and taking the first would silently pick the wrong
        # one -- 41 of 43 recorded tables contain rows identical in name,
        # quantity AND price, so this is the normal case here, not an edge one.
        fits = []
        for d in range(-len(b), len(b) + 1):
            overlap = [(i, i + d) for i in range(len(b))
                       if 0 <= i + d < len(a)]
            if len(overlap) >= 3 and all(b[i] == a[j] for i, j in overlap):
                fits.append((-d, len(overlap)))
        if len(fits) == 1:
            shift = fits[0][0]
        elif len(fits) > 1:
            shift = None
            print(f"\nAMBIGUOUS: {len(fits)} offsets fit equally well "
                  f"{[f[0] for f in fits]} - duplicate listings make the view "
                  "position unrecoverable from content alone.")
        else:
            shift = None
        print(f"\nrows moved: {shift if shift is not None else 'COULD NOT TELL'}")
        if fits:
            print(f"  offsets that fit: {fits}  (offset, overlapping rows)")
        if shift == 0:
            print("  the view did not move - the list may be at its end, or the")
            print("  wheel event did not reach the table.")
        elif shift is None:
            print("  no consistent overlap of 3+ rows. Either the whole page")
            print("  changed, or a listing changed under us. Do NOT act on")
            print("  positions in this state.")
        return

    if args.words:
        for w in sorted(find_words(grab(), TRADE_REGION), key=lambda w: (w.top, w.left)):
            print(f"{w.centre}  conf={w.conf:5.1f}  {w.text!r}")
        return

    if args.list:
        rows = await_rows()
        if not rows:
            print("No listings visible.")
        for row in rows:
            print(f"row {row.index:2d}: [{row.action:8s}] button={row.change}  {row.name!r}")
        return

    if args.relist_rows:
        try:
            wanted = parse_row_spec(args.relist_rows)
        except ValueError as exc:
            p.error(f"bad --relist-rows spec: {exc}")
        every = wants_all_rows(args.relist_rows)
        if not wanted and not every:
            p.error("--relist-rows needs at least one row, or 'all'")
        # `finally`, so a failure tidies up exactly as a success does. Left
        # open, the Trade window covers the NPC and the NEXT run cannot even
        # find her -- one failed batch otherwise blocks every later one.
        try:
            ok = relist_rows(wanted, dry_run=args.dry_run, all_rows=every)
        except ShopEmpty as exc:
            # A sold-out shop is a successful outcome, so exit 0 -- a caller
            # scripting this must not read "finished" as "failed".
            print(f"SOLD OUT: {exc}")
            sys.exit(0)
        except FatalAbort as exc:
            sys.exit(f"FATAL: {exc}")
        except (PermissionError, Aborted) as exc:
            sys.exit(f"Blocked: {exc}")
        finally:
            if not args.dry_run:
                leave_shop()
        sys.exit(0 if ok else 1)

    if args.relist:
        if len(args.relist) not in (1, 3):
            p.error("--relist takes ROW, or ROW INV_ROW INV_COL")
        slot = tuple(args.relist[1:]) if len(args.relist) == 3 else (None, None)
        try:
            outcome = relist(args.relist[0], *slot, dry_run=args.dry_run)
        except FatalAbort as exc:
            sys.exit(f"FATAL: {exc}")
        except (PermissionError, Aborted) as exc:
            sys.exit(f"Blocked: {exc}")
        finally:
            # An open Trade window covers the NPC, and a find_npc that fails is
            # how one bad exit becomes a dead afternoon. --relist-rows has had
            # this since the incident; these paths never did.
            if not args.dry_run:
                leave_shop()
        print(f"outcome: {outcome}")
        sys.exit(0 if outcome != FAILED else 1)

    if args.repeat:
        sys.exit(0 if run_loop(args.repeat, args.duration, args.every,
                               dry_run=args.dry_run) else 1)

    if args.do:
        try:
            ok = run_sequence(args.do, dry_run=args.dry_run)
        except FatalAbort as exc:
            sys.exit(f"FATAL: {exc}")
        except (PermissionError, Aborted) as exc:
            sys.exit(f"Blocked: {exc}")
        finally:
            # An open Trade window covers the NPC, and a find_npc that fails is
            # how one bad exit becomes a dead afternoon. --relist-rows has had
            # this since the incident; these paths never did.
            if not args.dry_run:
                leave_shop()
        sys.exit(0 if ok else 1)

    if args.register is not None:
        try:
            ok = register_item(*args.register, dry_run=args.dry_run,
                               # None, not False: absent means "use the
                               # configured policy", which is what the relist
                               # path does. Passing False here was the bug --
                               # it overrode MAXIMISE_ALL_QUANTITIES with a
                               # flag default and listed one unit of a stack.
                               maximise_qty=True if args.max_qty else None,
                               price_floor=args.floor,
                               floor_reason="--floor" if args.floor else "",
                               force_price=args.price, force_qty=args.qty)
        except FatalAbort as exc:
            sys.exit(f"FATAL: {exc}")
        except (PermissionError, Aborted) as exc:
            sys.exit(f"Blocked: {exc}")
        finally:
            # An open Trade window covers the NPC, and a find_npc that fails is
            # how one bad exit becomes a dead afternoon. --relist-rows has had
            # this since the incident; these paths never did.
            if not args.dry_run:
                leave_shop()
        sys.exit(0 if ok else 1)

    if args.cancel is None:
        p.error("nothing to do. Capture: --shot, --alz, --monitors. Read: "
                "--list, --panel, --words. Act: --open, --relist, --relist-rows, "
                "--cancel, --register, --load, --clear, --confirm, --reset, "
                "--do, --repeat. See --help.")

    try:
        ok = cancel_item(args.cancel, dry_run=args.dry_run)
    except PermissionError as exc:
        sys.exit(f"Blocked: {exc}")
    finally:
        # See above: an open Trade window is what breaks the NEXT run.
        if not args.dry_run:
            leave_shop()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    # Logging is started here rather than inside main() so it covers argument
    # parsing too -- a bad argument that exits is still something worth having
    # a record of -- and so the `finally` cannot be skipped by any of main()'s
    # many sys.exit() calls.
    # atexit as well as the handlers below, so the duration is printed on
    # paths that never reach them: os.abort, a sys.exit deep inside a library,
    # or an exception raised while the __main__ guard itself is unwinding.
    # finish_run_log is idempotent, so whichever fires first wins.
    atexit.register(finish_run_log)

    _log_path = start_run_log(sys.argv)
    if _log_path:
        print(f"Logging this run to {_log_path}")
    try:
        main()
    except SystemExit as exc:
        finish_run_log(f"exit {exc.code}")
        raise
    except BaseException as exc:          # noqa: BLE001 - includes KeyboardInterrupt
        # sys.excepthook writes the traceback; this adds the closing line so a
        # log that stops without one means the process was killed outright
        # rather than having raised.
        finish_run_log(f"{type(exc).__name__}")
        raise
    else:
        finish_run_log()
