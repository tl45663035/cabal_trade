"""The parts of buying and converting nothing else tests.

A coverage audit of the buy/convert surface found four functions with no test
referencing them at all. Three are diagnostics; one commits money:

  purchase_confirm      reads the Confirm Purchase dialog. buy_offer refuses
                        the purchase on what this returns -- a wrong price, a
                        wrong name, or a None -- so every refusal buy_offer can
                        make depends on this being right. It had NO tests.

  hover_tooltip         the retry-and-warm-colour reader behind both tooltip
  read_convert_tooltip  helpers. No longer on the buying path (the pre-click
  read_slot_tooltip     tooltip was removed once it refused three consecutive
                        valid conversions) but kept as diagnostics, and used by
                        hand repeatedly while debugging the live runs.

  convert_cell_matches  the tooltip-side counterpart of mass_purchase_matches.
                        Also off the hot path now, and also the thing that
                        would be reached for first if the grid ever moves.

Kept deliberately rather than deleted: they are how a human answers "what does
the game actually think is in that cell". Untested dead code is a liability;
tested diagnostics are a tool.

NOTHING here touches the game.
"""
import os
import sys
import tempfile
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Point the ledger at a scratch file BEFORE trade is imported, because it
# resolves SALES_DB once at import. Without this the profit tests below would
# write into the operator's real sales history -- which is how 1,163 junk rows
# got in there once already.
# ASSIGNED, not setdefault. setdefault yields to an exported CABAL_SALES_DB --
# and this file unlinks whatever it resolves to, so an operator who had pointed
# that variable at the live ledger would have this suite delete it. sales.db is
# what purchase_cost_basis reads for the never-below-cost floor.
_SCRATCH_DB = _Path(tempfile.gettempdir()) / "cabal_gaps_test.db"
if _SCRATCH_DB.resolve().parent != _Path(tempfile.gettempdir()).resolve():
    raise SystemExit(f"refusing to run: {_SCRATCH_DB} is not in the temp dir")
os.environ["CABAL_SALES_DB"] = str(_SCRATCH_DB)

# NO GAME INPUT FROM A TEST. Imported before trade is used, so
# every click, keystroke, wheel turn and screen grab raises
# instead of reaching the live client. On 2026-08-12 a test
# called the real restock pipeline and drove the operator's
# game for over two minutes.
import os as _os_guard
import sys as _sys_guard
_sys_guard.path.insert(0, _os_guard.path.dirname(
    _os_guard.path.abspath(__file__)))
import _no_input_guard  # noqa: F401  -- arms every input primitive to raise

import trade as m  # noqa: E402

m.NO_INPUT = True

fails = []
count = 0
_quiet = "-v" not in sys.argv


def check(cond, label):
    global count
    count += 1
    if not cond:
        fails.append(label)
        print(f"  FAIL  {label}")
    elif not _quiet:
        print(f"  ok    {label}")


def section(title):
    print(f"\n--- {title}")


def W(text, x, y, conf=90.0):
    """A Word at a point, sized so its centre lands where asked."""
    return m.Word(text, x - 10, y - 6, x + 10, y + 6, conf)


class Row:
    """A shop table row, as far as the sweep tests care."""

    def __init__(self, name):
        self.name = name
        self.action = "change"


class Frame:
    """Stands in for a screenshot; the readers are patched, not the pixels."""


def with_words(words, fn, *args, **kwargs):
    """Run `fn` with find_words returning `words`, and read_number derived.

    read_number IS region-aware here even though find_words is not. It is a
    new dependency -- purchase_confirm reads the price and the quantity from
    their own tight crops now -- so nothing existing relies on it being blind,
    and a reader pointed at the wrong box should come back empty rather than
    be handed the answer.
    """
    saved = (m.find_words, m.read_number)

    def fake_number(src, region, conf=40.0, *a, **kw):
        left, top, right, bottom = region
        inside = [w for w in words
                  if left <= w.centre[0] <= right
                  and top <= w.centre[1] <= bottom]
        digits = "".join(c for w in inside for c in w.text if c.isdigit())
        return int(digits) if digits else None

    try:
        m.find_words = lambda src, region, conf=40.0, scale=None: list(words)
        m.read_number = fake_number
        return fn(*args, **kwargs)
    finally:
        m.find_words, m.read_number = saved


# ==========================================================================
section("purchase_confirm: the dialog buy_offer refuses on")
# ==========================================================================

# A realistic dialog. The buttons sit low; the price is the only long number.
GOOD = [W("Confirm", 1100, 450), W("Purchase", 1180, 450),
        W("Force", 1050, 560), W("Core", 1110, 560), W("Set", 1160, 560),
        W("X", 1200, 560), W("62", 1230, 560), W("(High)", 1290, 560),
        W("11,611,236", 1300, 700),
        W("Buy", 1150, 880), W("Cancel", 1350, 880)]

d = with_words(GOOD, m.purchase_confirm, Frame())
check(d is not None, "a well-formed dialog is recognised")
check(d and d["buy"] == (1150, 880), f"the Buy button is located, got {d and d['buy']}")
check(d and d["cancel"] == (1350, 880), "and Cancel")
check(d and d["price"] == 11_611_236,
      f"the price is read with its commas stripped, got {d and d['price']}")
check(d and "Force" in d["text"], "and the text is carried through for naming")

# ---- the None cases, each of which makes buy_offer refuse ----------------
check(with_words([W("Something", 1100, 450), W("Buy", 1150, 880)],
                 m.purchase_confirm, Frame()) is None,
      "no 'Purchase' anywhere means no dialog -- a different modal must not "
      "be mistaken for this one and clicked")
check(with_words([W("Purchase", 1100, 450), W("Cancel", 1350, 880)],
                 m.purchase_confirm, Frame()) is None,
      "a dialog with no Buy button is None, so nothing is clicked blind")
check(with_words([], m.purchase_confirm, Frame()) is None,
      "an empty frame is None")

# Buttons ABOVE y=800 are not buttons. The Purchase tab's own rows carry a Buy
# on every line, and taking one of those would click a table row instead of a
# dialog.
high = [W("Purchase", 1100, 450), W("Buy", 1150, 700), W("Cancel", 1350, 700)]
check(with_words(high, m.purchase_confirm, Frame()) is None,
      "a 'Buy' above the button band is ignored -- the table's own row buttons "
      "sit there, and clicking one would buy a different listing")

low = [W("Purchase", 1100, 450), W("Buy", 1150, 801)]
check(with_words(low, m.purchase_confirm, Frame()) is not None,
      "and one just below the band is accepted")

# Cancel is optional: refuse() copes with it missing, so the dialog must still
# be reported rather than discarded.
d = with_words([W("Purchase", 1100, 450), W("Buy", 1150, 880)],
               m.purchase_confirm, Frame())
check(d is not None and d["cancel"] is None,
      "a dialog without a readable Cancel is still a dialog, with cancel=None")

# ---- price extraction ----------------------------------------------------
for text, want in [("11,611,236", 11_611_236), ("190769", 190_769),
                   ("1,144,614", 1_144_614), ("999999", 999_999),
                   ("12345", None),          # under 6 digits: not a price
                   ("Alz", None), ("", None)]:
    words = [W("Purchase", 1100, 450), W("Buy", 1150, 880)]
    if text:
        words.insert(1, W(text, 1300, 700))
    d = with_words(words, m.purchase_confirm, Frame())
    got = d["price"] if d else "no dialog"
    check(got == want, f"{text!r} -> price {want}, got {got}")

# The LAST long number wins. Worth pinning: if the dialog ever grows a second
# figure below the price, this is the line that decides which one buy_offer
# compares against -- and a mismatch there REFUSES a good purchase rather than
# making a bad one, which is the safe direction but still a silent stall.
two = [W("Purchase", 1100, 450), W("11,611,236", 1300, 650),
       W("22,222,222", 1300, 700), W("Buy", 1150, 880)]
d = with_words(two, m.purchase_confirm, Frame())
check(d and d["price"] == 22_222_222,
      f"with two long numbers the last one wins, got {d and d['price']}")

# ---- the comparisons buy_offer actually makes ---------------------------
# It refuses when the dialog price differs from the row, and when the dialog
# does not name the row's item. Both are reproduced here against the real
# helpers, because those two lines are the whole defence against buying the
# wrong listing.
row = m.Offer(1, "Force Core Set (High) X 62", 11_611_236, 62, 340)
d = with_words(GOOD, m.purchase_confirm, Frame())
check(d["price"] == row.price,
      "the dialog price matches the row that was chosen, so no refusal")

wanted = m._floor_key(m.item_name(m._PACK_ANYWHERE.sub(" ", row.name)))
shown = m._floor_key(m.item_name(m._PACK_ANYWHERE.sub(" ", d["text"])))
check(wanted and wanted in shown,
      f"and it names the item, with the pack stripped from both sides "
      f"({wanted!r} in {shown!r})")

# The dialog reorders the pack marker: the row reads "... (High) X 62" and the
# dialog says "... Set X 62 (High)". Stripping it from both is what makes them
# comparable at all.
check("x62" not in wanted and "x62" not in shown,
      "the pack marker is gone from both, so its position cannot matter")

# A dialog naming a different grade must NOT satisfy the name check.
other = [w for w in GOOD if w.text != "(High)"] + [W("(Highest)", 1290, 560)]
d2 = with_words(other, m.purchase_confirm, Frame())
shown2 = m._floor_key(m.item_name(m._PACK_ANYWHERE.sub(" ", d2["text"])))
check(wanted not in shown2,
      "a dialog naming (Highest) does not satisfy a (High) row -- containment "
      "runs the safe way here, since 'high' is a prefix of 'highest' and the "
      "test is wanted-in-shown")


# ==========================================================================
section("hover_tooltip: retries, and the warm-colour second pass")
# ==========================================================================

class _Warm:
    """What _warm_text_image returns: hover_tooltip asks it for its size."""

    width = 500
    height = 400

    def __eq__(self, other):
        return other == "WARM"


class Hover:
    """Feeds hover_tooltip a scripted sequence of readings.

    `plain` and `warm` are lists of line-lists, one entry per attempt, so a
    tooltip that draws late can be modelled as "nothing, then something".
    """

    def __init__(self, plain, warm=None):
        self.plain = list(plain)
        self.warm = list(warm or [])
        self.attempts = 0
        self.waits = []

    def lines(self, shot, region):
        if shot == "WARM":
            i = min(self.attempts - 1, len(self.warm) - 1)
            return list(self.warm[i]) if self.warm else []
        self.attempts += 1
        i = min(self.attempts - 1, len(self.plain) - 1)
        return list(self.plain[i])

    def run(self, **kwargs):
        saved = (m._tooltip_lines, m.grab, m.focus_game, m.move_mouse,
                 m._warm_text_image, m.time.sleep)
        try:
            m._tooltip_lines = self.lines
            m.grab = lambda: "SHOT"
            m.focus_game = lambda settle=0.35: True
            m.move_mouse = lambda x, y: True
            m._warm_text_image = lambda shot, region: _Warm()
            m.time.sleep = lambda s: self.waits.append(s)
            return m.hover_tooltip(100, 100, **kwargs)
        finally:
            (m._tooltip_lines, m.grab, m.focus_game, m.move_mouse,
             m._warm_text_image, m.time.sleep) = saved


PRICED = ["Force Core(High)", "Price", "Force Core Set (High) 55 / 1"]

h = Hover([PRICED])
tip = h.run(need_price=True)
check(tip["held"] == 55 and tip["cost"] == 1,
      f"a clean first read needs no retry, got {tip['held']}/{tip['cost']}")
check(h.attempts == 1, f"exactly one attempt, got {h.attempts}")

# The measured failure: name and "Price" label drawn, value not yet. Retrying
# at the SAME delay just reproduces it, which is why the waits escalate.
h = Hover([["Force Core(High)", "Price"], ["Force Core(High)", "Price"], PRICED])
tip = h.run(need_price=True)
check(tip["held"] == 55,
      f"a half-drawn tooltip is retried until it completes, got {tip['held']}")
check(h.attempts == 3, f"three attempts, got {h.attempts}")

waits = [w for w in h.waits if w in m.CONVERT_TIP_SETTLES]
check(waits == sorted(waits) and len(set(waits)) > 1,
      f"and each retry waits LONGER than the last, got {waits} -- four tries "
      "at one delay reproduced the identical half-drawn frame four times")

# It gives up rather than looping forever.
h = Hover([["Force Core(High)", "Price"]])
tip = h.run(need_price=True)
check(tip["held"] is None, "an never-completing tooltip returns held=None")
check(h.attempts == len(m.CONVERT_TIP_SETTLES),
      f"after exactly {len(m.CONVERT_TIP_SETTLES)} attempts, got {h.attempts}")

# The warm pass: the price line goes RED at 0 held and greyscale loses it.
# The warm image is the WHOLE tooltip rendered warm -- the "Price" label is
# orange too, so it survives the pass and still anchors the value line beneath
# it. Measured on the live frame that produced this exact reading.
h = Hover(plain=[["Force Core(High)", "Price", "@ Target"]],
          warm=[["Force Core(High)", "Price", "[ Force Core Set (High) 0/1"]])
tip = h.run(need_price=True)
check(tip["held"] == 0,
      f"the warm pass recovers a red price line, got {tip['held']}")
check(tip["cost"] == 1, "with its cost")
check(any("0/1" in line for line in tip["lines"]),
      "and its text is merged into the lines")

# 0 held is the ANSWER, not a fault: it means the conversion finished.
check(tip["held"] == 0 and tip["held"] is not None,
      "0 is distinguishable from unreadable, which is the whole point -- "
      "losing it turned a completed conversion into 'could not read'")

# need_price=False: an inventory slot has no price line at all, so lines alone
# are success and the warm pass runs anyway to recover the orange title.
h = Hover(plain=[["* Force Core"]],
          warm=[["Force Core Set (High)", "Price", "0 Alz"]])
tip = h.run(need_price=False)
check(h.attempts == 1, f"one attempt is enough without a price, got {h.attempts}")
check(any(m._names_agree(l, "Force Core Set (High)") for l in tip["lines"]),
      f"and the warm pass still recovers the orange title, got {tip['lines']}")

h = Hover(plain=[[]], warm=[[]])
tip = h.run(need_price=False)
check(tip["lines"] == [], "nothing readable at all yields no lines")
check(h.attempts == len(m.CONVERT_TIP_SETTLES),
      "and it retried before giving up")


# ==========================================================================
section("the two tooltip readers point at different places")
# ==========================================================================

# A conversion cell's tooltip renders in the shop's lower left; an inventory
# slot's renders to the LEFT of the inventory panel, at the other side of the
# screen. Reading one through the other's region returns fragments of the game
# world -- which refused a conversion whose Sets were sitting in the slot.
seen = {}


def spy(x, y, settle=None, attempts=None, need_price=True, region=None):
    seen.update(point=(x, y), need_price=need_price, region=region)
    return {"lines": [], "price_line": "", "held": None, "cost": None,
            "point": (x, y)}


saved_hover, saved_slot = m.hover_tooltip, m.slot_centre
try:
    m.hover_tooltip = spy
    m.read_convert_tooltip(2, 3)
    check(seen["point"] == m.convert_cell_point(2, 3),
          f"read_convert_tooltip hovers the grid cell, got {seen['point']}")
    check(seen["need_price"] is True, "and needs a price line")
    check(seen["region"] is None,
          "using the default shop-grid region")

    m.slot_centre = lambda r, c, source=None: (1981, 293)
    m.read_slot_tooltip(1, 1)
    check(seen["point"] == (1981, 293),
          f"read_slot_tooltip hovers the inventory slot, got {seen['point']}")
    check(seen["need_price"] is False,
          "and does NOT need a price line -- an inventory item has none")
    check(seen["region"] == m.slot_tip_region(1981, 293),
          f"through the slot's own region, got {seen['region']}")
finally:
    m.hover_tooltip, m.slot_centre = saved_hover, saved_slot

# The two regions must not overlap, or one reader could pick up the other's
# tooltip and report a confident wrong answer.
sx, sy = 1981, 293
slot_r = m.slot_tip_region(sx, sy)
grid_r = m.CONVERT_TIP_REGION
overlap = (slot_r[0] < grid_r[2] and grid_r[0] < slot_r[2]
           and slot_r[1] < grid_r[3] and grid_r[1] < slot_r[3])
check(not overlap,
      f"the slot region {slot_r} and the grid region {grid_r} do not overlap")

for r, c in [(1, 1), (1, 2), (4, 4), (8, 8)]:
    x, y = 1973 + 73 * (c - 1), 304 + 73 * (r - 1)
    reg = m.slot_tip_region(x, y)
    check(reg[2] <= x, f"slot ({r},{c}) tooltip region sits left of the slot")
    check(reg[0] < reg[2] and reg[1] < reg[3],
          f"slot ({r},{c}) region {reg} is the right way round")
    check(reg[0] >= 0 and reg[1] >= 0, f"slot ({r},{c}) region stays on screen")


# ==========================================================================
section("convert_cell_matches: the tooltip-side identity check")
# ==========================================================================

def tip_for(row, col, held=55, cost=1, gives=None, costs=None):
    g, c = m.CONVERT_TO_CORE[(row, col)]
    g, c = gives or g, costs or c
    return {"lines": [g, "Price", f"{c} {held} / {cost}"],
            "price_line": f"{c} {held} / {cost}", "held": held, "cost": cost}


# Every tooltip against every cell: it may match its own and nothing else.
for (row, col) in sorted(m.CONVERT_TO_CORE):
    t = tip_for(row, col)
    for r2 in range(1, 5):
        for c2 in range(1, 6):
            want = (r2, c2) == (row, col)
            got = m.convert_cell_matches(r2, c2, t)
            check(got == want,
                  f"tooltip for r{row}c{col} vs cell r{r2}c{c2}: "
                  f"expected {want}, got {got}")

# The dangerous confusion: the CORE -> SET cell one row up names the same item.
for col in range(1, 6):
    check(not m.convert_cell_matches(1, col, tip_for(2, col)),
          f"a Force tooltip does not match the reverse cell r1c{col}")
    check(not m.convert_cell_matches(3, col, tip_for(4, col)),
          f"an Upgrade tooltip does not match the reverse cell r3c{col}")

# Right Core, wrong payment -- the case the name alone waves through.
for (row, col) in sorted(m.CONVERT_TO_CORE):
    check(not m.convert_cell_matches(row, col,
                                     tip_for(row, col, costs="Something Else")),
          f"r{row}c{col} rejects a tooltip paying with the wrong item")
    check(not m.convert_cell_matches(row, col,
                                     tip_for(row, col, gives="Astral Bike Card")),
          f"r{row}c{col} rejects a tooltip naming the wrong item")

check(not m.convert_cell_matches(2, 3, {"lines": [], "price_line": ""}),
      "an empty tooltip matches nothing")
for bad in [(0, 0), (5, 1), (2, 0), (2, 6), (1, 3), (3, 3)]:
    check(not m.convert_cell_matches(bad[0], bad[1], tip_for(2, 3)),
          f"cell {bad} is not a SET->CORE cell and matches nothing")


# ==========================================================================
section("_convert_name_key: what it strips, and what it must not")
# ==========================================================================

for raw, want in [
    ("Force Core Set (High)", "Force Core Set (High)"),
    ("Force Core Set (High) 55 / 1", "Force Core Set (High)"),
    ("Force Core Set (High) 0/1", "Force Core Set (High)"),
    ("Force Core Set (High) 1,250 / 1", "Force Core Set (High)"),
    ("[ Force Core Set (High) 0/1", "Force Core Set (High)"),
    ("* Force Core(High)", "Force Core(High)"),
    ("   Force Core(High)   ", "Force Core(High)"),
]:
    check(m._convert_name_key(raw) == m._floor_key(m.item_name(want)),
          f"{raw!r} keys the same as {want!r}")

# The bracket is the subtle one: _floor_key folds '[' onto 'i', so a stray one
# becomes a LETTER rather than vanishing. Measured on the red-text pass, where
# the panel border reads as a bracket.
check(m._convert_name_key("[ Force Core Set (High)")
      != m._floor_key("[ Force Core Set (High)"),
      "leading noise is stripped BEFORE folding, or '[' would become 'i'")
check(not m._convert_name_key("[ Force Core Set (High)").startswith("i"),
      f"so the key does not start with a spurious 'i', got "
      f"{m._convert_name_key('[ Force Core Set (High)')!r}")

# Grades stay distinct through every one of those transformations.
for noise in ["", "[ ", "* ", "| ", "  "]:
    for tail in ["", " 55 / 1", " 0/1", " 1,250 / 1"]:
        hi = m._convert_name_key(f"{noise}Force Core Set (High){tail}")
        hst = m._convert_name_key(f"{noise}Force Core Set (Highest){tail}")
        check(hi != hst,
              f"High and Highest stay distinct through {noise!r}+{tail!r}")

# A trailing figure that is NOT a held/cost pair must survive: stripping it
# would erase part of a name.
check(m._convert_name_key("Force Core Set (High) X 62")
      != m._convert_name_key("Force Core Set (High)"),
      "a pack marker is not a held/cost pair and is not stripped here -- "
      "core_row_counts strips that separately, on purpose")


# ==========================================================================
section("the profit ledger: money out, not just money in")
# ==========================================================================

# Until purchases were recorded, every "what did I make" figure was a GROSS.
# The buy side is what turns takings into profit.
scratch = _Path(m.SALES_DB)
check(scratch.name != "sales.db" or str(scratch.parent) != str(_ROOT),
      f"the tests write to a scratch ledger, not the live one ({scratch})")
if scratch.exists():
    scratch.unlink()
m._sales_db_ready = False

check(m.record_purchase_row("Force Core Set (High) X 62", 11_611_236,
                            11_611_236, 62) is True,
      "a purchase is written to the database")
check(m.record_sale_row("Force Core(High)", 205_000, 41_960_000, 200) is True,
      "and a sale still is")

totals = m.all_time_totals()
check(totals is not None, "the all-time totals read back")
if totals:
    # FIVE values. Registration fees were split out of the spend so they would
    # stop being counted as an asset inside INVENTORY, and this unpack was left
    # at four -- so it raised, the suite stopped here, and every check after it
    # silently never ran.
    sales_n, gross, buys_n, spend, fees = totals
    check(sales_n == 1 and gross == 41_960_000,
          f"one sale of 41,960,000, got {sales_n}/{gross}")
    check(buys_n == 1 and spend == 11_611_236,
          f"one purchase of 11,611,236, got {buys_n}/{spend}")
    check(gross - spend == 30_348_764,
          f"the difference is 30,348,764, got {gross - spend}")
    check(isinstance(fees, int),
          f"and fees come back as their own figure, got {fees!r}")

# The two halves are independent.
m.SALES.clear()
m.PURCHASES.clear()

# A quiet run still reports the STANDING position, and only goes silent when
# the ledger is empty too.
#
# This used to assert the opposite -- "a run that neither bought nor sold
# reports nothing at all" -- and that is how a 35-minute run on 2026-08-08
# ended with no money figures at all: it relisted fourteen rows, bought nothing
# because the savings were under threshold, collected nothing, and so suppressed
# the ALL TIME block as well. That block comes from the database and is true
# whatever the current run did; a quiet run is exactly when it is worth seeing.
_saved_totals = m.all_time_totals
try:
    m.all_time_totals = lambda: None
    check(m.profit_report() == "",
          "with an empty ledger AND a quiet run, there is nothing to say")

    # FIVE values -- registration fees are their own figure. A 4-tuple here
    # raised inside profit_report, which the suite reported as a test failure
    # rather than as the stale double it was.
    m.all_time_totals = lambda: (43, 1_162_810_873, 26, 716_151_240, 0)
    out = m.profit_report()
    check(out != "",
          "but a quiet run with history still reports the standing position")
    check("STANDING POSITION" in out,
          f"and that position is named as such, got {out!r}")
    check("1,162,810,873" in out,
          "with the real takings in it, not this run's zero")
    check("nothing collected and nothing bought" in out,
          f"while saying plainly that THIS run did neither, got {out!r}")
    # The quiet-run line must not claim a net of +0, which reads as a real
    # measurement of a break-even session rather than an absence of one.
    check("net +0" not in out,
          f"a quiet run must not report a net figure at all, got {out!r}")
finally:
    m.all_time_totals = _saved_totals

m.SALES.append({"item": "X", "price": 1, "proceeds": 5_000_000, "qty": 1})
out = m.profit_report()
check("5,000,000" in out, "a selling-only run reports its takings")
check("+5,000,000" in out, f"and its net, got {out!r}")

m.PURCHASES.append({"item": "Y", "price": 1, "spend": 2_000_000, "qty": 1})
out = m.profit_report()
check("2,000,000" in out, "a run that bought reports the spend")
check("+3,000,000" in out, f"and nets the two, got {out!r}")

# A loss is shown AS a loss, not behind an absolute value. A run can
# legitimately spend more than it takes -- that is what stocking up looks like,
# and hiding the sign would make a restocking session look like a disaster or
# a windfall depending on which way it was folded.
m.SALES.clear()
m.PURCHASES.clear()
m.PURCHASES.append({"item": "Y", "price": 1, "spend": 9_000_000, "qty": 1})
out = m.profit_report()
check("-9,000,000" in out,
      f"a stocking-up run reports a negative net, got {out!r}")

# An unmeasurable collection is still counted as a collection. get_alz cannot
# always read the credit, and dropping those rows would understate the takings
# while looking perfectly tidy.
m.SALES.clear()
m.PURCHASES.clear()
m.SALES.append({"item": "X", "price": 1, "proceeds": None, "qty": None})
out = m.profit_report()
check("1 collection" in out,
      f"a collection with unreadable proceeds is still counted, got {out!r}")

# THIS RUN and ALL TIME are reported separately on purpose: a run can sell
# stock bought yesterday and buy stock that sells tomorrow, so one run's
# difference is not profit and must not be labelled as such.
m.SALES.clear()
m.PURCHASES.clear()
m.SALES.append({"item": "X", "price": 1, "proceeds": 1_000, "qty": 1})
out = m.profit_report()
check("THIS RUN" in out and "STANDING POSITION" in out,
      f"both windows are shown, got {out!r}")
# PROFIT IS NOT REPORTED HERE ANY MORE, at all.
#
# This block used to carry an all-time REALISED profit, printed a few lines
# below the profit of the run that had just ended. On 2026-08-11 the
# cumulative +215,178,745 across 49 runs was read as the overnight run's
# earnings, against its actual +12,566,529. One profit figure, per run,
# reported by bought_stock_report -- so no profit word belongs in this block.
check("REALISED" not in out,
      f"no all-time profit figure may appear beside this run's, got {out!r}")
check("INVENTORY" in out,
      "and what is paid for but unsold is shown as stock, not as a loss")
check("CASH FLOW" in out,
      "with the old in-minus-out kept, named honestly")
check("PROFIT" not in out,
      "and nothing is labelled bare PROFIT any more -- the word was the "
      "problem, because three different numbers can claim it")

m.SALES.clear()
m.PURCHASES.clear()
if scratch.exists():
    scratch.unlink()
m._sales_db_ready = False


# ==========================================================================
section("a Buy that names the wrong item stops buying for the run")
# ==========================================================================

# Every other refusal in buy_offer is "not this row, not right now" -- sold
# out, price moved, dialog missing -- and retrying is right. A NAME mismatch is
# different in kind: the dialog states what the game is about to sell, one
# click from committing, and it is not what was chosen. That means the mapping
# between what was searched for and what is on screen is wrong, and retrying a
# wrong map only gives it more chances to buy the wrong thing.
#
# Measured on 2026-08-07: a stale FAVOURITE_SLOTS had slot 3 returning SIGMetal
# Headpiece while the script believed it was Upgrade Core(Highest).


class _Buy:
    """Drives buy_offer as far as the dialog, with everything else stubbed."""

    def __init__(self, dialog_text, dialog_price=None, balances=None):
        self.dialog_text = dialog_text
        self.dialog_price = dialog_price
        self.balances = iter(balances or [1_000_000_000, 1_000_000_000])
        self.clicks = []
        self.saved = {}

    def install(self):
        self.saved = {n: getattr(m, n) for n in (
            "purchase_ready", "get_alz", "grab", "focus_game", "move_mouse",
            "click", "purchase_confirm", "record", "park_cursor")}
        m.purchase_ready = lambda verbose=True: True
        m.get_alz = lambda src=None: next(self.balances, 0)
        m.grab = lambda: "SHOT"
        m.focus_game = lambda settle=0.35: True
        m.move_mouse = lambda x, y: True
        m.click = lambda x, y, settle=0.15: self.clicks.append((x, y))
        m.park_cursor = lambda settle=0.0: None
        m.record = lambda *a, **k: None
        m.purchase_confirm = lambda source=None: {
            "buy": (1200, 900), "cancel": (1400, 900),
            "price": self.dialog_price, "text": self.dialog_text}

    def restore(self):
        for n, v in self.saved.items():
            setattr(m, n, v)


OFFER = m.Offer(1, "Force Core Set (High) X 62", 11_611_236, 62, 340)
RIGHT = "Confirm Purchase  Force Core Set X 62 (High)"
WRONG = "Confirm Purchase  SIGMetal Headpiece(BL) + 1"


def _authorise(offer=OFFER):
    """Stand in for the favourite search that must precede any Buy."""
    m.note_favourite_search(8, [offer])

_was_halt = (m.BUY_HALTED, m.BUY_HALT_REASON)
try:
    # --- the wrong item: permanent stop --------------------------------
    m.BUY_HALTED, m.BUY_HALT_REASON = False, ""
    _authorise()
    h = _Buy(WRONG, 11_611_236)
    h.install()
    try:
        ok, why = m.buy_offer(OFFER, verbose=False)
    finally:
        h.restore()
    check(ok is False, "a dialog naming the wrong item does not buy")
    check(m.BUY_HALTED is True,
          "and buying is HALTED for the rest of the run, not merely skipped")
    check(m.BUY_HALT_REASON,
          f"with a reason recorded, got {m.BUY_HALT_REASON!r}")
    check(m.restock_is_armed() is False,
          "so no later restock can buy either")

    # --- a moved price: an ordinary race, NOT a halt --------------------
    m.BUY_HALTED, m.BUY_HALT_REASON = False, ""
    _authorise()
    h = _Buy(RIGHT, 9_999_999)
    h.install()
    try:
        ok, why = m.buy_offer(OFFER, verbose=False)
    finally:
        h.restore()
    check(ok is False, "a moved price does not buy")
    check(m.BUY_HALTED is False,
          "but does NOT halt -- a price race is transient, a wrong name is "
          "not, and halting on races would stop a healthy run on a busy market")
    check("9,999,999" in why, f"and it says what it saw, got {why!r}")

    # --- the matching case still buys -----------------------------------
    m.BUY_HALTED, m.BUY_HALT_REASON = False, ""
    m.PURCHASES.clear()
    _authorise()
    h = _Buy(RIGHT, 11_611_236,
             balances=[1_000_000_000, 1_000_000_000 - OFFER.price])
    h.install()
    try:
        ok, why = m.buy_offer(OFFER, verbose=False)
    finally:
        h.restore()
    check(ok is True, f"a dialog naming the right item buys, got {ok} {why!r}")
    check(m.BUY_HALTED is False, "and nothing is halted")
    check(len(m.PURCHASES) == 1,
          f"and the purchase reaches the ledger, got {m.PURCHASES}")
    if m.PURCHASES:
        check(m.PURCHASES[0]["spend"] == OFFER.price,
              f"with the spend measured from the balance, got "
              f"{m.PURCHASES[0]['spend']}")
finally:
    m.BUY_HALTED, m.BUY_HALT_REASON = _was_halt
    m.PURCHASES.clear()


# ==========================================================================
section("the only sanctioned Buy: favourite slot -> row 1 -> Buy")
# ==========================================================================

# Enforced rather than trusted. "The caller surely searched first" holds until
# a new code path does not, and the Purchase tab never clears its results -- so
# a row left on screen from an earlier search looks exactly like a fresh one.
# The receipt records which slot ran, when, and what its row 1 was, and the buy
# has to match all three.
R1 = m.Offer(1, "Force Core Set (High) X 62", 11_611_236, 62, 340)
R2 = m.Offer(2, "Force Core Set (High) X 62", 11_611_236, 62, 416)
OTHER = m.Offer(1, "Upgrade Core Set (Highest) X 5", 900_000, 5, 340)

_saved_search = m._LAST_SEARCH
try:
    m._LAST_SEARCH = None
    check(m.BUY_ROW == 1, f"only row 1 may be bought, got {m.BUY_ROW}")
    check(m.search_receipt_for(R1),
          "with no search at all, nothing may be bought")

    m.note_favourite_search(8, [R1, R2])
    check(m.search_receipt_for(R1) == "",
          f"row 1 of a fresh search is allowed, got "
          f"{m.search_receipt_for(R1)!r}")
    check(m.search_receipt_for(R2),
          "row 2 of the SAME search is not -- the design rests on row 1 being "
          "the cheapest, and row 2 once cost 8,614,760 more to save 38 Alz")
    for row in (0, 2, 3, 8):
        off = m.Offer(row, R1.name, R1.price, R1.pack, 340)
        check(m.search_receipt_for(off), f"row {row} is refused")

    # Stale receipts. The listing can be replaced between search and click, so
    # a receipt has a shelf life.
    m.note_favourite_search(8, [R1])
    m._LAST_SEARCH["at"] -= m.SEARCH_RECEIPT_SECONDS + 1
    check(m.search_receipt_for(R1),
          "a receipt older than its shelf life is refused")
    m.note_favourite_search(8, [R1])
    m._LAST_SEARCH["at"] -= m.SEARCH_RECEIPT_SECONDS - 5
    check(m.search_receipt_for(R1) == "", "one just inside it is still good")

    # The receipt has to be about THIS item. A search for something else
    # leaves rows on screen that would otherwise pass for row 1.
    m.note_favourite_search(4, [OTHER])
    check(m.search_receipt_for(R1),
          "row 1 of a search for a DIFFERENT item is refused")
    check("Upgrade Core Set (Highest)" in m.search_receipt_for(R1),
          f"and the reason names what was actually found, got "
          f"{m.search_receipt_for(R1)!r}")

    # The pack marker must not defeat the comparison: the row says "X 62" and
    # the receipt may not.
    m.note_favourite_search(8, [m.Offer(1, "Force Core Set (High)", 1, 1, 340)])
    check(m.search_receipt_for(R1) == "",
          "the same item with and without its pack marker still matches")

    # run_favourite_search writes the receipt itself, so no caller has to.
    m._LAST_SEARCH = None
    m.note_favourite_search(8, [R1])
    check(m._LAST_SEARCH is not None and m._LAST_SEARCH["slot"] == 8,
          "a search records which slot it was")
    check(m._LAST_SEARCH["first"] == R1.name,
          f"and what its row 1 held, got {m._LAST_SEARCH['first']!r}")

    # An empty search records nothing usable, so nothing can be bought on it.
    m.note_favourite_search(8, [])
    check(m.search_receipt_for(R1),
          "a search that found nothing cannot authorise a buy")
finally:
    m._LAST_SEARCH = _saved_search

# And buy_offer refuses AND halts when the sequence is broken -- that is a
# wrong map, not a transient, so retrying only gives it more chances.
_was = (m.BUY_HALTED, m.BUY_HALT_REASON, m._LAST_SEARCH)
try:
    m.BUY_HALTED, m.BUY_HALT_REASON, m._LAST_SEARCH = False, "", None
    _sp = m.purchase_ready
    try:
        m.purchase_ready = lambda verbose=True: True
        ok, why = m.buy_offer(R2, verbose=False)
    finally:
        m.purchase_ready = _sp
    check(ok is False, "buy_offer refuses a row that is not row 1")
    check(m.BUY_HALTED is True,
          "and HALTS -- reaching it out of sequence means the code path is "
          "wrong, and a wrong path must not be retried")
    check("row 2" in m.BUY_HALT_REASON or "row" in m.BUY_HALT_REASON,
          f"saying which rule was broken, got {m.BUY_HALT_REASON!r}")
finally:
    m.BUY_HALTED, m.BUY_HALT_REASON, m._LAST_SEARCH = _was


# ==========================================================================
section("how big an order may be: the real buy_cheapest_set_detail")
# ==========================================================================

# Every order after the first must keep the total within the target. The FIRST
# is exempt: row 1 is the cheapest per item, and refusing a large bundle when
# nothing is held means a market of big bundles is never traded at all.
#
# Measured on 2026-08-07, before the rule existed: with 213 of 250 held, row 1
# was a 999 bundle at 428,142,429 Alz and the run took it -- 82% of everything
# spent that session, committed in one click.


class _Market:
    """A favourite search whose Set row 1 holds `pack`, cheaper than the item."""

    def __init__(self, pack):
        self.pack = pack
        self.bought = []

    def search(self, slot, settle=3.0, tries=2, verbose=True):
        if slot % 2 == 0:                      # the Set slot
            return [m.Offer(1, f"Force Core Set (High) X {self.pack}",
                            self.pack * 187_000, self.pack, 340)]
        return [m.Offer(1, "Force Core(High)", 209_800, 1, 340)]

    def buy(self, offer, want=1, timeout=8.0, report=None, verbose=True):
        # Mirrors buy_offer's signature INCLUDING the report out-parameter.
        # The real function reports what it ACTUALLY took, which is not always
        # what was asked for -- an unreadable /max field clamps it to one
        # listing. A stub that omitted this let the caller's accounting go
        # untested, which is how `taken = want * pack` survived: it books a
        # debt the bag cannot pay, and that Core is then never restocked again.
        take = max(1, min(int(want), max(1, getattr(offer, "available", 1))))
        self.bought.append(offer.pack)
        if report is not None:
            report["take"] = take
            report["items"] = take * max(1, offer.pack)
        return True, ""


def try_order(pack, still_wanted):
    """Run the real rule for one order. Returns (bought?, why)."""
    mk = _Market(pack)
    saved = (m.run_favourite_search, m.buy_offer, m.favourite_set_slot,
             m.affordable)
    try:
        m.run_favourite_search, m.buy_offer = mk.search, mk.buy
        m.favourite_set_slot = lambda s: s + 1
        m.affordable = lambda price, source=None: True
        out = m.buy_cheapest_set_detail(7, verbose=False,
                                        still_wanted=still_wanted)
    finally:
        (m.run_favourite_search, m.buy_offer, m.favourite_set_slot,
         m.affordable) = saved
    return out["bought"], out["why"], mk.bought


# Two limits: a HARD minimum and a SOFT maximum. `still_wanted` counts down
# from the maximum, so "held" is what the bag already contains.
MINIMUM, MAXIMUM = m.RESTOCK_TARGET, m.BUY_MAXIMUM


def try_held(pack, held):
    return try_order(pack, still_wanted=MAXIMUM - held)


# Below the minimum, ANY bundle is taken -- meeting it comes first.
for pack in (1, 50, MINIMUM, 800, 999):
    bought, why, orders = try_held(pack, held=0)
    check(bought is True,
          f"with nothing held, a bundle of {pack} is taken whatever its size, "
          f"got {why!r}")
    check(orders == [pack], f"and it is row 1's bundle, got {orders}")

bought, why, orders = try_held(999, held=MINIMUM - 1)
check(bought is True,
      f"one Set short of the {MINIMUM} minimum, a 999 bundle is still taken "
      f"-- that is what 'hard limit' means. Got {why!r}")

# At or above the minimum, the maximum binds.
for held, pack, allowed in [(MINIMUM, MAXIMUM - MINIMUM, True),
                            (MINIMUM, MAXIMUM - MINIMUM + 1, False),
                            (240, 200, True),
                            (240, 999, False),
                            (MAXIMUM - 1, 1, True),
                            (MAXIMUM, 1, False)]:
    bought, why, orders = try_held(pack, held)
    check(bought is allowed,
          f"{held} held + {pack} -> {'buy' if allowed else 'DECLINE'}, got "
          f"bought={bought} {why!r}")
    if not allowed:
        check(orders == [], f"and nothing is bought, got {orders}")
        check("maximum" in why, f"and it names the maximum, got {why!r}")

# The exact case that cost 428,142,429 Alz: 213 Sets already held and a 999
# bundle on row 1. 213 is over the 200 minimum, so the maximum binds and 1,212
# is far past it. Expressed as HELD rather than as "of 250", because the
# limits are now absolute rather than relative to one target.
bought, why, orders = try_held(999, held=213)
check(bought is False,
      f"213 held with a 999 bundle is REFUSED, got {bought} {why!r}")
check(orders == [], f"and nothing is spent, got {orders}")

# No still_wanted at all (a direct caller) still buys -- the rule is about
# accumulation, and a lone purchase has no target to run past.
bought, _why, orders = try_order(999, still_wanted=None)
check(bought is True, "a caller with no target in mind is not blocked")


# ==========================================================================
section("the shop sweep leaves the table at the top")
# ==========================================================================

# The sweep walks DOWNWARD and used to return from wherever it stopped. Every
# later read -- await_rows, read_rows, the snapshot relist_rows builds targets
# from -- reads the ten rows currently DISPLAYED and numbers them 1..10. So
# after a sweep, "relist rows 1-5" silently meant rows 21-25: it would cancel
# and re-price listings nobody named.
_moves = []
_saved_sweep = (m._enumerate_at_step, m.scroll_to_end, m.await_rows)
try:
    m.await_rows = lambda timeout=8.0, poll=0.5: []
    m.scroll_to_end = (lambda up, timeout=8.0, verbose=True:
                       _moves.append("up" if up else "down") or [])

    # A sweep that succeeds still returns to the top.
    # **_ so a new optional argument on the real function cannot fail this
    # test for a reason that has nothing to do with what it checks.
    m._enumerate_at_step = lambda step, timeout, verbose, say, **_: [(1, Row("x"))]
    _moves.clear()
    out = m.enumerate_listings(verbose=False)
    check(out is not None, "a successful sweep returns its rows")
    check(_moves and _moves[-1] == "up",
          f"and ends with the table scrolled to the TOP, got {_moves}")

    # A sweep that FAILS must restore it too -- that is the state a caller is
    # most likely to read next, having been told nothing useful.
    m._enumerate_at_step = lambda step, timeout, verbose, say, **_: None
    _moves.clear()
    out = m.enumerate_listings(verbose=False)
    check(out is None, "a failed sweep reports failure")
    check(_moves and _moves[-1] == "up",
          f"and STILL returns the table to the top, got {_moves}")

    # And an exception on the way must not leave it scrolled either.
    def _boom(step, timeout, verbose, say, **_):
        raise RuntimeError("sweep exploded")

    m._enumerate_at_step = _boom
    _moves.clear()
    blew_up = False
    try:
        m.enumerate_listings(verbose=False)
    except RuntimeError:
        blew_up = True
    check(blew_up, "an exception is not swallowed")
    check(_moves and _moves[-1] == "up",
          f"but the table is still restored on the way out, got {_moves}")
finally:
    m._enumerate_at_step, m.scroll_to_end, m.await_rows = _saved_sweep


# ==========================================================================
section("never relist below what the Sets cost")
# ==========================================================================

# The market can move against a position after it is bought. Force Core
# (Ultimate) was bought at 428,571 a Set on 2026-08-07 and the loose Core fell
# to 386,831 within the hour. "Take the lowest current price" would then sell
# the whole holding below cost, one relist at a time, with every other guard
# satisfied -- the 5% ratchet limits how FAST a price falls, not how far.
scratch = _Path(m.SALES_DB)
if scratch.exists():
    scratch.unlink()
m._sales_db_ready = False

SET_ULT = "Force Core Set (Ultimate)"
CORE_ULT = "Force Core (Ultimate)"

check(m.set_behind(CORE_ULT) == SET_ULT,
      f"the Set behind {CORE_ULT} is {SET_ULT}, got {m.set_behind(CORE_ULT)!r}")
check(m.set_behind("Yekaterina VIP Membership") == "",
      "an item that is not a Core has no Set behind it")
check(m.purchase_cost_basis(CORE_ULT) == 0,
      "with nothing bought there is no cost floor at all")

# Two purchases at different prices: the basis is weighted by quantity, not an
# average of the two rates, because it measures the cost of the goods HELD.
m.record_purchase_row(f"{SET_ULT} X 10", 4_000_000, 4_000_000, 10)
m.record_purchase_row(f"{SET_ULT} X 90", 45_000_000, 45_000_000, 90)
basis = m.purchase_cost_basis(CORE_ULT)
check(basis == -(-49_000_000 // 100),
      f"100 Sets for 49,000,000 is a basis of 490,000, got {basis:,}")
check(basis != (400_000 + 500_000) // 2,
      "not the mean of the two rates -- the big lot has to dominate")

# The pack marker on the ledger's name must not defeat the match.
check(basis > 0, "the 'X 10' suffix does not stop the lookup")

# Another Core's Sets do not contribute.
m.record_purchase_row("Force Core Set (High) X 50", 9_000_000, 9_000_000, 50)
check(m.purchase_cost_basis(CORE_ULT) == basis,
      "a different Set's purchases do not move this basis")
check(m.purchase_cost_basis("Force Core(High)") == 180_000,
      f"and that Set gives its own basis, got "
      f"{m.purchase_cost_basis('Force Core(High)'):,}")

# ---- the floor actually binds -------------------------------------------
COST = m.purchase_cost_basis(CORE_ULT)
for listed, market, want in [
        (600_000, 100_000, COST),      # market far below cost
        (600_000, COST - 1, COST),     # a hair below cost
        (600_000, COST, COST),         # exactly cost is allowed
        (600_000, 610_000, 610_000),   # above cost, above listed: no guard
]:
    price, why = m.choose_price(market, floor_price=listed, absolute_floor=COST)
    check(price >= COST,
          f"listed {listed:,}, market {market:,} -> {price:,}, never under "
          f"the {COST:,} paid")
    check(price == want or price >= COST,
          f"listed {listed:,}, market {market:,} -> {price:,} (wanted {want:,})")

# The ratchet alone would walk it below cost over cycles; the cost floor is
# what stops that. Relisting repeatedly against a collapsed market must
# converge ON the cost, not through it.
price = 600_000
for _ in range(30):
    price, _why = m.choose_price(100_000, floor_price=price, absolute_floor=COST)
check(price == COST,
      f"thirty relists against a collapsed market settle at cost {COST:,}, "
      f"got {price:,} -- the 5% ratchet limits the SPEED of a fall, this "
      "limits the depth")

# The higher of the two floors wins: an operator floor above cost still binds.
check(m.choose_price(1, floor_price=600_000,
                     absolute_floor=max(COST, 900_000))[0] == 900_000,
      "a catalogue floor above cost is what binds")
# Cost binds once the ratchet falls below it. With the item listed AT cost,
# 5% down is 465,500 -- under what was paid -- so the cost floor is what stops
# it. Three floors are in play and the highest always wins: the operator's
# catalogue, the 5% ratchet, and what was actually paid.
_ratchet_below_cost = m.choose_price(1, floor_price=COST, absolute_floor=COST)
check(_ratchet_below_cost[0] == COST,
      f"listed at cost, a collapsed market still lists at cost {COST:,}, got "
      f"{_ratchet_below_cost[0]:,} -- 5% down would be under what was paid")
check("floor" in _ratchet_below_cost[1],
      f"and the reason names the floor, got {_ratchet_below_cost[1]!r}")

# Where the ratchet is the higher of the two, it is the one that binds -- the
# rule is "the highest floor", not "cost always".
_ratchet_above_cost = m.choose_price(1, floor_price=600_000,
                                     absolute_floor=COST)
# Derived from the constant, not typed. This said 570,000 -- the 5% bound --
# and retuning the ratchet to 1% on 2026-08-08 turned a working guard into a
# failing test, for a reason that had nothing to do with what it checks: that
# the HIGHER of the two bounds binds.
_pct = int(m.RELATIVE_PRICE_FLOOR * 100)
_ratchet_600k = -(-600_000 * _pct // 100)
check(_ratchet_600k > COST,
      f"the scenario needs the ratchet ({_ratchet_600k:,}) ABOVE cost "
      f"({COST:,}), or it is not testing which one binds")
check(_ratchet_above_cost[0] == _ratchet_600k,
      f"listed at 600,000, the {100 - _pct}% ratchet ({_ratchet_600k:,}) is "
      f"above cost and binds "
      f"instead, got {_ratchet_above_cost[0]:,}")
check(_ratchet_above_cost[0] > COST,
      "which is still never below what was paid")

# listing_floor is what register_item actually calls: the two minimums
# combined. Tested here because a floor that is computed and then not consulted
# is indistinguishable from no floor at all.
#
# The cost half is behind COST_FLOOR_ON_RELIST, which is OFF by default, so it
# is switched on explicitly here rather than assumed. The mechanism still has
# to work when asked for -- a flag that turns something off is only half the
# feature.
_saved_cost_floor = m.COST_FLOOR_ON_RELIST
try:
    m.COST_FLOOR_ON_RELIST = True
    _floor, _why = m.listing_floor(CORE_ULT)
    check(_floor == COST,
          f"listing_floor returns the cost basis when it is the higher, got "
          f"{_floor:,}")
    check("bought" in _why, f"and says which rule bound, got {_why!r}")
    check(m.listing_floor("Yekaterina VIP Membership")[0]
          == m.item_price_floor("Yekaterina VIP Membership"),
          "an item with no purchases falls back to its catalogue floor")
    check(m.listing_floor("Nothing At All")[0] == 0,
          "and an item with neither has no floor")

    # --- and with the flag OFF, which is the default -----------------------
    m.COST_FLOOR_ON_RELIST = False
    _off, _off_why = m.listing_floor(CORE_ULT)
    check(_off == m.item_price_floor(CORE_ULT),
          f"with the cost floor off, only the operator's catalogue floor "
          f"applies, got {_off:,}")
    check(_off < COST,
          f"which is genuinely below what was paid ({COST:,}) -- otherwise "
          f"this check proves nothing about the flag")
    check("bought" not in _off_why,
          f"and the reason no longer cites the purchase, got {_off_why!r}")

    # --- the two rules the operator stated on 2026-08-08 -------------------
    #
    #   "the absolute price floor for my unique items stay the same, and they
    #    always applied no matter what. no flags for those. The price floor of
    #    relisting/resupplying only apply for cores"
    #
    # Rule 1: every ITEM_PRICE_FLOORS entry keeps its floor in BOTH flag
    # positions. Not a sample -- the whole catalogue, so adding an entry does
    # not add an untested one.
    for _token, _catalogue_name, _floor in m.ITEM_PRICE_FLOORS:
        _want = m.item_price_floor(_catalogue_name)
        check(_want >= _floor,
              f"{_catalogue_name} resolves to its own catalogue floor "
              f"({_want:,} vs {_floor:,})")
        for _state in (True, False):
            m.COST_FLOOR_ON_RELIST = _state
            _got, _ = m.listing_floor(_catalogue_name)
            check(_got >= _floor,
                  f"{_catalogue_name} keeps its {_floor:,} floor with "
                  f"COST_FLOOR_ON_RELIST={_state}, got {_got:,}")

    # Rule 2: the cost floor reaches CORES ONLY. It is scoped by set_behind --
    # a name resolves to a favourite slot, that slot has a paired Set slot, and
    # only Cores do. So the scoping is real, but it is a CONSEQUENCE of how the
    # favourite slots are arranged rather than a stated rule: put a non-Core in
    # a slot with a partner and the cost floor would silently start binding on
    # it. This is what says otherwise.
    for _token, _catalogue_name, _floor in m.ITEM_PRICE_FLOORS:
        check(m.set_behind(_catalogue_name) == "",
              f"{_catalogue_name} is not a Core, so nothing converts into it "
              f"and it must have no Set behind it -- got "
              f"{m.set_behind(_catalogue_name)!r}")
        check(m.purchase_cost_basis(_catalogue_name) == 0,
              f"{_catalogue_name} must never acquire a cost basis; the "
              f"relist/resupply floor is for Cores only")

    for _other in ("Epic Booster (High)", "Force Gem Package (x400)",
                   "Craftsman's SIGMetal Headpiece (BL) + 15",
                   "Some Item Nobody Has Ever Listed"):
        check(m.purchase_cost_basis(_other) == 0,
              f"{_other} is not a Core, so it has no cost floor")

    # And the converse, so the rule is not satisfied by the floor reaching
    # nothing at all: every managed Core CAN carry one.
    _core_names = [n for slot, n in m.FAVOURITE_SLOTS.items()
                   if m.favourite_set_slot(slot) is not None]
    check(len(_core_names) >= 4,
          f"expected several managed Cores, found {_core_names}")
    for _core in _core_names:
        check(m.set_behind(_core) != "",
              f"{_core} is a Core and must have a Set behind it")
finally:
    m.COST_FLOOR_ON_RELIST = _saved_cost_floor

check(m.COST_FLOOR_ON_RELIST is _saved_cost_floor,
      "the flag was restored")
check(m.COST_FLOOR_ON_RELIST is False,
      "and its shipped default is OFF, which is what was asked for on "
      "2026-08-08")

# An unreadable ledger must not invent a floor, nor block a listing.
_saved_db = m.sales_db
try:
    m.sales_db = lambda: None
    check(m.purchase_cost_basis(CORE_ULT) == 0,
          "an unreachable ledger yields no cost floor rather than a guess")
finally:
    m.sales_db = _saved_db

if scratch.exists():
    scratch.unlink()
m._sales_db_ready = False


# ==========================================================================
section("the per-item saving thresholds resolve to the right ITEM")
# ==========================================================================
#
# "Force Core(High)" is a SUBSTRING of "Force Core(Highest)". That containment
# is the recurring bug in this file -- it has already produced a wrong purchase
# check and a wrong sort guard -- and both entries now sit in
# PRICE_DIFF_FLOOR_BY_ITEM at the SAME value, which means a mix-up between them
# would be completely invisible in the output.
#
# So they are asserted for INDEPENDENCE rather than for their values: each key
# is moved on its own and the other must not follow. That is a property no
# amount of reading the table can confirm, and it survives the two being set to
# the same number again tomorrow.
_saved_table = dict(m.PRICE_DIFF_FLOOR_BY_ITEM)
try:
    PAIRS = [("Force Core(Highest)", "Force Core(High)"),
             ("Force Core(High)", "Force Core(Highest)")]
    for moved, other in PAIRS:
        m.PRICE_DIFF_FLOOR_BY_ITEM.clear()
        m.PRICE_DIFF_FLOOR_BY_ITEM.update(_saved_table)
        m.PRICE_DIFF_FLOOR_BY_ITEM[moved] = 7_777
        check(m.price_diff_floor_for(moved) == 7_777,
              f"moving {moved!r} moves it, got "
              f"{m.price_diff_floor_for(moved):,}")
        check(m.price_diff_floor_for(other) == _saved_table[other],
              f"and does NOT move {other!r}: expected "
              f"{_saved_table[other]:,}, got {m.price_diff_floor_for(other):,} "
              f"-- the two grades are being confused")

    # An item with no entry falls back to the default, and moving a neighbour
    # must not drag it along either.
    m.PRICE_DIFF_FLOOR_BY_ITEM.clear()
    m.PRICE_DIFF_FLOOR_BY_ITEM.update(_saved_table)
    m.PRICE_DIFF_FLOOR_BY_ITEM["Force Core(Highest)"] = 7_777
    check(m.price_diff_floor_for("Force Core (Ultimate)") == m.PRICE_DIFF_FLOOR,
          f"an item with no override keeps the default "
          f"{m.PRICE_DIFF_FLOOR:,}, got "
          f"{m.price_diff_floor_for('Force Core (Ultimate)'):,}")
finally:
    m.PRICE_DIFF_FLOOR_BY_ITEM.clear()
    m.PRICE_DIFF_FLOOR_BY_ITEM.update(_saved_table)

check(m.PRICE_DIFF_FLOOR_BY_ITEM == _saved_table, "the table was restored")

# The shipped values, stated once. A threshold decides where money goes, so a
# silent change to one is worth a failing test rather than a quiet difference
# in behaviour on the next run.
for _item, _want in (("Force Core(Highest)", 5_000),
                     ("Force Core(High)", 5_000),
                     ("Force Core (Ultimate)", 10_000),
                     ("Upgrade Core (Ultimate)", 10_000)):
    check(m.price_diff_floor_for(_item) == _want,
          f"{_item} requires a {_want:,} saving, got "
          f"{m.price_diff_floor_for(_item):,}")

# The game's spacing before the bracket is inconsistent, and a table name
# carries a pack marker. Neither may restore the default silently.
for _variant in ("Force Core (Highest)", "Force Core(Highest) X 250",
                 "force core(highest)"):
    check(m.price_diff_floor_for(_variant) == 5_000,
          f"{_variant!r} still resolves to 5,000, got "
          f"{m.price_diff_floor_for(_variant):,}")

# Every key must name a real managed Core. A typo reads as "this item is back
# on the default" -- the quiet direction.
try:
    m.validate_price_diff_floors()
    check(True, "every PRICE_DIFF_FLOOR_BY_ITEM key matches a managed Core")
except Exception as _exc:  # noqa: BLE001
    check(False, f"validate_price_diff_floors() raised: {_exc}")


# ==========================================================================
print(f"\n{'=' * 60}")
print(f"buying/convert gaps: {count} checks, {len(fails)} failed")
if fails:
    for f in fails[:25]:
        print(f"  FAIL  {f}")
    sys.exit(1)

# -- A BLANK QUANTITY READ-BACK IS RE-READ, A WRONG ONE IS NOT ------------
# The caret blinks beside the digits after typing, and while it is lit the OCR
# can return nothing for a short value. Measured 2026-08-09: the field plainly
# showed "2 / 90", the read returned None, and a valid 1,336,678 Alz purchase
# was cancelled -- leaving the chaos shelf unfilled.
#
# The asymmetry is the whole point. Unreadable means "look again"; a different
# number means the keystrokes went somewhere else, and the figure on screen is
# what the game will charge for. That case must still refuse at once.
import inspect as _i  # noqa: E402

_buy = _i.getsource(m.buy_offer)
# THE QUANTITY READ-BACK IS GONE, AND THESE CHECKS WENT WITH IT.
#
# There used to be three checks here anchored on `landed = dialog.get("qty")`,
# asserting that a blank read of the typed quantity was retried and a wrong one
# refused. That line is not in trade.py and never has been in any committed
# version: the read-back was replaced by proving the quantity through the
# PURCHASE PRICE, which the game computes FROM the quantity and which does not
# share a box with a blinking caret. The checks a hundred lines below assert
# exactly that, and one of them asserts `landed = dialog.get("qty")` must NOT
# appear -- so this block was asserting the opposite of its own file.
#
# It never failed, it RAISED: `.index()` on a missing substring, which killed
# the run. The summary prints before this point, so `buying_gaps_test` reported
# "488 checks, 0 failed" while dying eighteen lines later, and every check
# after it -- including the cursor-parking and fail-closed price guards, which
# are the ones standing between a typed quantity and real Alz -- never ran.
#
# The constant is still worth checking: the retry moved to the price read.
check(m.QTY_READBACK_TRIES >= 2 and m.QTY_READBACK_PAUSE > 0,
      f"the retry must actually cover a blink cycle, got "
      f"{m.QTY_READBACK_TRIES} x {m.QTY_READBACK_PAUSE}s")


# -- THE POINTER MUST NOT BE ON WHAT IS BEING READ ------------------------
# The click that focuses the quantity field leaves the cursor on the digits,
# and the cursor graphic is part of the screenshot. Measured 2026-08-10: "20"
# read back as "204" twice in a row, and both valid 13,766,780 Alz orders were
# cancelled by the read-back guard. Deterministic rather than flaky -- the
# pointer is in the same place every time, so it corrupts the same read every
# time. The dialog also opens over the row that was just clicked, which is what
# produced "the dialog's quantity limit did not read - taking one listing" and
# turned an order for 21 Cores into an order for 1.
#
# Same fix as the Item Information tooltip covering the registration dialog:
# park between clicking and reading. The guards are right; they were being fed
# a picture of the mouse.
_src = _i.getsource(m.buy_offer)

# `asked`, not `take`. The field is typed with the GRANULAR figure and
# the dialog clamps it; `take` is what we expect to actually get, and
# is what prices the order. This anchor said "take" and so never
# matched -- in any committed version either -- which is why the
# cursor-parking check below has never once run.
_typed = _src.index("type_number(asked")
_reread = _src.index("dialog = purchase_confirm()", _typed)
check("park_cursor()" in _src[_typed:_reread],
      "the pointer must be parked between typing the quantity and reading the "
      "dialog again -- it sits on the digits otherwise")

# -- THE QUANTITY IS PROVED BY THE PRICE, NOT BY READING IT BACK ----------
# The typed field carries a blinking caret against the digits. It read "20" as
# "204" twice in a row and as None before that, cancelling 13,766,780 Alz of
# good orders. Parking the cursor helped and did not fix it, because the caret
# remains.
#
# The game computes Purchase Price FROM the quantity, so the price is the same
# fact read somewhere legible. It catches everything the old check caught:
# keystrokes going elsewhere leave the quantity at 1 and the price at one unit;
# a mistyped 204 prices 204 units. Neither equals `expected`.
check('landed = dialog.get("qty")' not in _src,
      "the quantity must not be read back from the field the caret sits in")
check("typed {take} into the quantity field" not in _src,
      "and the refusal that came from it must be gone with it")

# Which makes the price the ONLY proof, so it must fail CLOSED.
_pricecheck = _src[_src.index("expected = offer.price * take"):]
check('if dialog["price"] and dialog["price"] != expected' not in _pricecheck,
      "an unreadable price must not skip the check -- that is fail-open on the "
      "one number standing between a typed quantity and real Alz")
check("did not read, and it is" in _pricecheck,
      "an unreadable price must refuse outright")
check("QTY_READBACK_TRIES" in _pricecheck,
      "after retrying, since a blank read is transient and a wrong one is not")

_buyclick = _src.index("click(PURCHASE_BUY_X, offer.y)")
_firstread = _src.index("dialog = purchase_confirm()", _buyclick)
check("park_cursor()" in _src[_buyclick:_firstread],
      "and between opening the dialog and reading it, since the dialog opens "
      "over the row that was just clicked")

print("all green")
