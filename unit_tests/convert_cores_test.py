"""convert_cores(): the vendor grid, the Purchase Item dialog, and the refusals.

This is the only code in the file that clicks inside an NPC vendor window, and
that window is unlike every other surface the script touches:

    plain click   Immediate Purchase -- spends something AT ONCE, no dialog
    Alt + click   Mass Purchase, which asks for a quantity
    Ctrl + click  links the item into chat

Everywhere else, a misplaced click costs a wasted cycle. Here it costs items,
silently, with nothing on screen to undo. So the tests that matter most are not
the happy path -- they are the ones asserting that a click DOES NOT HAPPEN when
anything is unverified, and that the CORE -> SET cells (the same trade run
backwards, which would burn the whole margin) are unreachable by any input.

Three layers, none of which trust the layer below:

  1. the grid: names resolve to cells, Set names resolve to nothing at all
  2. the dialog: what it says is compared against what was intended, AFTER the
     click that opens it but BEFORE anything is spent
  3. the sequence: a simulated game records every click, and each scenario
     asserts on the recording rather than on a return value

Layer 3 is what earns its keep. A function can return the right answer having
clicked the wrong thing on the way, and this project has shipped exactly that
bug more than once -- the buying path bought row 2 while reporting row 1, and
an unguarded capture script walked the character across the map by clicking 80
times without re-checking where it was.

The dialog geometry here was measured off a live capture on 2026-08-07:

    +-------------- Purchase Item --------------+
    |  Force Core(High)                         |
    |  Purchase QTY   [   1   ] / 55   [^v]     |
    |  Purchase Price  Force Core Set (High) 55/1|
    |                        [ OK ]  [ Cancel ] |
    +-------------------------------------------+
"""
import sys
import time
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

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

# NOTHING in this suite may touch the game, and this is set at IMPORT so no
# section can forget it. A test that turned it off to reach a guard is how a
# mutation run -- which deletes that guard by design -- Alt+clicked four
# coordinates into the game world and walked the character across the map.
m.NO_INPUT = True

fails = []
count = 0
# Image sections that did not run because the frame was not on disk. The
# corpus is gitignored -- it is live session data -- so this is the normal
# state everywhere except the machine that captured it. Counted so the summary
# cannot imply the pixels were checked when they were not.
skipped = []
_quiet = "-v" not in sys.argv


def check(cond, label):
    """Record one assertion. Only failures print unless -v is given: this suite
    runs a few thousand cases and a wall of 'ok' hides the one line that matters.
    """
    global count
    count += 1
    if not cond:
        fails.append(label)
        print(f"  FAIL  {label}")
    elif not _quiet:
        print(f"  ok    {label}")


def section(title):
    print(f"\n--- {title}")


# The coordinates of every cell that converts the WRONG way. Nothing in this
# suite may ever produce a click at one of these, under any scenario.
FORBIDDEN_POINTS = {m.convert_cell_point(r, c) for (r, c) in m.CONVERT_TO_SET}


# ==========================================================================
section("grid geometry")
# ==========================================================================

check(len(m.CONVERT_TO_CORE) == 10,
      f"exactly 10 SET->CORE cells, got {len(m.CONVERT_TO_CORE)}")
check(len(m.CONVERT_TO_SET) == 10,
      f"exactly 10 CORE->SET cells, got {len(m.CONVERT_TO_SET)}")
check(not (set(m.CONVERT_TO_CORE) & m.CONVERT_TO_SET),
      "no cell is listed as converting in both directions")
check({r for r, _ in m.CONVERT_TO_CORE} == {2, 4},
      "the SET->CORE cells are rows 2 and 4")
check(m.CONVERT_TO_SET == {(1, c) for c in range(1, 6)} | {(3, c) for c in range(1, 6)},
      "the CORE->SET cells are rows 1 and 3")
check(m.CONVERT_QUANTITY == 250,
      f"CONVERT_QUANTITY is 250 (a shop row's maximum), got {m.CONVERT_QUANTITY}")
check(len(m.CONVERT_COLS) == 5 and len(m.CONVERT_GRADES) == 5,
      "five columns, five grades")
check(len(m.CONVERT_ROWS) == 4, "four rows")

points = {}
for r in range(1, 5):
    for c in range(1, 6):
        p = m.convert_cell_point(r, c)
        check(p == (m.CONVERT_COLS[c - 1], m.CONVERT_ROWS[r - 1]),
              f"r{r}c{c} point is the (col, row) intersection")
        check(0 < p[0] < 2560 and 0 < p[1] < 1440,
              f"r{r}c{c} {p} lies on screen")
        check(p not in points, f"r{r}c{c} {p} is a distinct point")
        points[p] = (r, c)

check(list(m.CONVERT_COLS) == sorted(m.CONVERT_COLS),
      "columns ascend left to right, so grade order is not scrambled")
check(list(m.CONVERT_ROWS) == sorted(m.CONVERT_ROWS),
      "rows ascend top to bottom")


# ==========================================================================
section("name resolution: cores in, sets never")
# ==========================================================================

def variants(name):
    """Spellings of one item that a reader might plausibly produce.

    Deliberately does NOT include a pack suffix ("... x 62"). That marker
    belongs to Agent Shop SET listings, never to a vendor Core, and the lookup
    is exact on purpose -- see the strictness checks below.
    """
    return [
        name,
        name.upper(),
        name.lower(),
        name.replace("(", " ("),
        name.replace(" ", ""),
        f"  {name}  ",
        f"* {name}",
    ]


for (row, col), (gives, costs) in sorted(m.CONVERT_TO_CORE.items()):
    for text in variants(gives):
        got = m.convert_cell_for(text)
        check(got == (row, col),
              f"{text!r} -> r{row}c{col}, got {got}")
    for text in variants(costs):
        got = m.convert_cell_for(text)
        check(got is None,
              f"the SET name {text!r} must resolve to nothing, got {got}")

# Nothing outside the grid resolves to anything.
for junk in ["", "   ", "Siena's Unbinding Stone", "Epic Booster (Highest)",
             "Force", "Core", "Set", "Force Core", "Upgrade Core",
             "Force Core Set", "Highest", "Bike", "Force Core(Legendary)",
             "Astral Bike Card", "SIGmetal Suit", "Force Core Set (High) X 62"]:
    got = m.convert_cell_for(junk)
    check(got is None, f"{junk!r} resolves to nothing, got {got}")

# Strictness, stated on purpose. A name carrying anything the grid does not
# recognise resolves to nothing rather than to its nearest neighbour: a
# refusal costs a cycle, a near-miss spends the wrong items.
for (row, col), (gives, _c) in sorted(m.CONVERT_TO_CORE.items()):
    for junk in [f"{gives} x 62", f"{gives} X 250", f"{gives} and something",
                 f"Superior {gives}"]:
        check(m.convert_cell_for(junk) is None,
              f"{junk!r} does not resolve to a cell by near-miss")

# The grade prefixes, which are the trap. Low/Medium/High/Highest/Ultimate are
# prefixes of one another, so every one must resolve to ITSELF and no other.
for (row, col), (gives, _c) in sorted(m.CONVERT_TO_CORE.items()):
    got = m.convert_cell_for(gives)
    check(got == (row, col), f"{gives} resolves to its own cell, got {got}")
    for (other_rc, (other, _)) in sorted(m.CONVERT_TO_CORE.items()):
        if other_rc != (row, col):
            check(m.convert_cell_for(other) != (row, col),
                  f"{other} does not resolve to r{row}c{col}, which is {gives}")

# The safety property stated as a property, over everything tried above.
probes = []
for _, (gives, costs) in m.CONVERT_TO_CORE.items():
    probes += variants(gives) + variants(costs)
probes += ["", "Force Core Set", "Upgrade Core Set", "nonsense"]
for text in probes:
    got = m.convert_cell_for(text)
    check(got is None or got not in m.CONVERT_TO_SET,
          f"convert_cell_for({text!r}) never returns a CORE->SET cell")


# ==========================================================================
section("dialog identity: what it says vs what was meant")
# ==========================================================================

def detail_for(row, col, *, qty=1, qty_max=55, held=None, cost=1,
               item=None, price=None):
    """A Purchase Item dialog reading, shaped like the real one."""
    gives, costs = m.CONVERT_TO_CORE[(row, col)]
    held = qty_max if held is None else held
    return {
        "item": item if item is not None else f"* {gives}",
        "price_line": price if price is not None else f"{costs} {held} / {cost}",
        "held": held,
        "cost": cost,
        "qty": qty,
        "qty_max": qty_max,
    }


# Every dialog against every cell: it may match its own and nothing else.
for (row, col) in sorted(m.CONVERT_TO_CORE):
    d = detail_for(row, col)
    for r2 in range(1, 5):
        for c2 in range(1, 6):
            want = (r2, c2) == (row, col)
            got = m.mass_purchase_matches(r2, c2, d)
            check(got == want,
                  f"dialog for r{row}c{col} vs cell r{r2}c{c2}: "
                  f"expected {want}, got {got}")

# The dangerous confusion specifically: the cell one row up names the same
# item and is the reverse trade. It must not match on the name alone.
for col in range(1, 6):
    d = detail_for(2, col)
    check(not m.mass_purchase_matches(1, col, d),
          f"a Force dialog does not match the CORE->SET cell r1c{col}")
    d = detail_for(4, col)
    check(not m.mass_purchase_matches(3, col, d),
          f"an Upgrade dialog does not match the CORE->SET cell r3c{col}")

# A dialog naming the right Core but the wrong payment is not a match. This is
# the case the tooltip check alone would wave through.
for (row, col), (gives, costs) in sorted(m.CONVERT_TO_CORE.items()):
    wrong = detail_for(row, col, price="Something Else 5 / 1")
    check(not m.mass_purchase_matches(row, col, wrong),
          f"r{row}c{col} rejects a dialog paying with the wrong item")
    wrong = detail_for(row, col, item="* Astral Bike Card")
    check(not m.mass_purchase_matches(row, col, wrong),
          f"r{row}c{col} rejects a dialog giving the wrong item")
    blank = detail_for(row, col, item="", price="")
    check(not m.mass_purchase_matches(row, col, blank),
          f"r{row}c{col} rejects an empty dialog reading")

# An unmapped cell matches nothing, whatever it is handed.
for bad in [(0, 0), (5, 1), (2, 0), (2, 6), (1, 3), (3, 3)]:
    check(not m.mass_purchase_matches(bad[0], bad[1], detail_for(2, 3)),
          f"cell {bad} is not a SET->CORE cell and matches nothing")


# ==========================================================================
section("price-line parsing")
# ==========================================================================

class FakeShot:
    """Stands in for a screenshot; the readers are patched, not the pixels."""


def parse_price(text):
    """Run mass_purchase_details' parsing over one price line."""
    saved_words, saved_number = m.find_words, m.read_number
    try:
        m.find_words = lambda s, r, c=40.0: (
            [m.Word(text, 0, 0, 1, 1, 99.0)] if r == m.CONVERT_DLG_PRICE else [])
        m.read_number = lambda s, r, c=0.0: None
        return m.mass_purchase_details(FakeShot())
    finally:
        m.find_words, m.read_number = saved_words, saved_number


PRICE_CASES = [
    ("Force Core Set (High) 55 / 1", 55, 1),
    ("Force Core Set (High) 55/1", 55, 1),
    ("Force Core Set (High) 55  /  1", 55, 1),
    ("Upgrade Core Set (Low) 13 / 1", 13, 1),
    ("Upgrade Core Set (Ultimate) 1 / 1", 1, 1),
    ("Force Core Set (Medium) 1,250 / 1", 1250, 1),
    ("Force Core Set (Highest) 0 / 1", 0, 1),
    ("Force Core Set (High) 250 / 2", 250, 2),
    ("Force Core Set (High)", None, None),
    ("", None, None),
    ("Force Core Set (High) 55", None, None),
    ("garbage with no numbers at all", None, None),
]
for text, held, cost in PRICE_CASES:
    d = parse_price(text)
    check(d["held"] == held, f"{text!r} -> held {held}, got {d['held']}")
    check(d["cost"] == cost, f"{text!r} -> cost {cost}, got {d['cost']}")
    check(d["price_line"] == text, f"{text!r} round-trips as the price line")


# ==========================================================================
section("the sequence: a simulated vendor that records every click")
# ==========================================================================

class Sim:
    """A stand-in game. Records actions; nothing reaches the real screen.

    Defaults describe a healthy vendor holding 55 Force Core Set (High) --
    exactly the state captured on 2026-08-07. Each scenario overrides only the
    one thing it is about, so a test that breaks the tooltip is visibly a test
    about the tooltip.
    """

    OK = m.Word("OK", 1273, 902, 1297, 916, 96.0)          # centre (1285, 909)
    CANCEL = m.Word("Cancel", 1438, 901, 1496, 917, 96.0)  # centre (1467, 909)

    def __init__(self, cell=(2, 3), *, shop_open=True, dialog=True,
                 dialog_cell=None, qty_max=55,
                 dialog_cost=1, qty_max_readable=True, typing="works",
                 focus=True,
                 inv_open=True, inv_tab=m.CONVERT_INVENTORY_TAB,
                 set_slot_filled=True,
                 core_slot_filled=False, cores_land=True, free_slots=9999):
        self.cell = cell
        self.shop_open = shop_open
        self.dialog = dialog
        self.dialog_cell = dialog_cell or cell
        self.qty_max = qty_max
        self.dialog_cost = dialog_cost
        self.qty_max_readable = qty_max_readable
        self.typing = typing   # works | ignored | wrong | over | unreadable
        self.focus = focus
        self.inv_open = inv_open
        self.inv_tab = inv_tab
        self.set_slot_filled = set_slot_filled
        self.core_slot_filled = core_slot_filled
        self.cores_land = cores_land
        self.free_slots = free_slots

        self.log = []
        self.field = 1                # what the QTY field shows
        self.dialog_up = False
        self.purchased = None
        self.clock = 1000.0

    # -- the patched surface -------------------------------------------------
    def focus_game(self, settle=0.35):
        self.log.append(("focus",))
        return self.focus

    def vendor_shop_open(self, source=None):
        return self.shop_open

    def active_vendor_tab(self, source=None):
        # The conversion grid only exists on the Dungeon tab; on any other page
        # these coordinates point at something else entirely.
        return m.CONVERT_VENDOR_TAB if self.shop_open else None

    def grab(self):
        self.log.append(("grab",))
        return "screenshot"

    def inventory_origin(self, source=None, retries=3):
        return (1000, 200) if self.inv_open else None

    def select_inventory_tab(self, tab, origin=None, timeout=5.0):
        self.log.append(("select_tab", tab))
        if self.inv_tab is None or self.inv_tab != tab:
            return False
        return True

    def _landing_slots(self, n):
        """The first `n` free slots, which is where Cores go. They do not
        stack, so each one takes a slot of its own -- that is what makes the
        count at the end a measurement rather than an inference."""
        out = []
        for r in range(1, m.GRID_SIZE + 1):
            for c in range(1, m.GRID_SIZE + 1):
                if (r, c) == m.CONVERT_SET_SLOT:
                    continue
                if len(out) >= n:
                    return out
                out.append((r, c))
        return out

    def occupied_slots(self, image, origin):
        out = [m.CONVERT_SET_SLOT] if self.set_slot_filled else []
        if self.purchased is not None:
            landing = 0 if not self.cores_land else min(self.purchased,
                                                        self.free_slots)
        else:
            landing = 1 if self.core_slot_filled else 0
        return sorted(set(out + self._landing_slots(landing)))

    def alt_click(self, x, y, settle=0.15):
        self.log.append(("alt_click", x, y))
        if self.dialog:
            self.dialog_up = True
            self.field = 1

    def mass_purchase_open(self, source=None):
        return (self.OK, self.CANCEL) if self.dialog_up else None

    def mass_purchase_details(self, source=None):
        self.log.append(("details",))
        gives, costs = m.CONVERT_TO_CORE[self.dialog_cell]
        return {
            "item": f"* {gives}",
            "price_line": f"{costs} {self.qty_max} / {self.dialog_cost}",
            "held": self.qty_max, "cost": self.dialog_cost,
            "qty": self.field,
            "qty_max": self.qty_max if self.qty_max_readable else None,
        }

    def click(self, x, y, settle=0.15):
        self.log.append(("click", x, y))
        if (x, y) == self.OK.centre and self.dialog_up:
            self.purchased = self.field
            self.dialog_up = False
        elif (x, y) == self.CANCEL.centre:
            self.dialog_up = False

    def type_number(self, value, per_key=0.0, clear_first=True, clear=None):
        self.log.append(("type", value))
        if self.typing == "ignored":
            return
        if self.typing == "unreadable":
            self.field = None
            return
        if self.typing == "wrong":
            self.field = min(value, self.qty_max) + 1
            return
        if self.typing == "over":
            # MORE than was asked for. Not a clamp -- a clamp can only ever
            # reduce -- so the field holds something this round did not type.
            self.field = value + 5
            return
        self.field = min(value, self.qty_max)   # the game clamps

    def press_escape(self, settle=0.5):
        self.log.append(("escape",))
        self.dialog_up = False

    def park_cursor(self, settle=0.0):
        self.log.append(("park",))

    def sleep(self, seconds):
        self.clock += seconds

    def monotonic(self):
        self.clock += 0.01
        return self.clock

    # -- helpers -------------------------------------------------------------
    def clicks(self):
        return [(a[1], a[2]) for a in self.log if a[0] in ("click", "alt_click")]

    def clicked(self, point):
        return point in self.clicks()

    def bought(self):
        return self.clicked(self.OK.centre)


def run(sim, name="Force Core(High)", quantity=250, execute=True):
    """Drive the real convert_cores against a Sim. Returns (result, error)."""
    patches = {
        "focus_game": sim.focus_game,
        "vendor_shop_open": sim.vendor_shop_open,
        "active_vendor_tab": sim.active_vendor_tab,
        "alt_click": sim.alt_click,
        "mass_purchase_open": sim.mass_purchase_open,
        "mass_purchase_details": sim.mass_purchase_details,
        "click": sim.click,
        "type_number": sim.type_number,
        "press_escape": sim.press_escape,
        "park_cursor": sim.park_cursor,
        "grab": sim.grab,
        "inventory_origin": sim.inventory_origin,
        "select_inventory_tab": sim.select_inventory_tab,
        "occupied_slots": sim.occupied_slots,
    }
    saved = {k: getattr(m, k) for k in patches}
    real_sleep, real_mono = time.sleep, time.monotonic
    try:
        for k, v in patches.items():
            setattr(m, k, v)
        time.sleep, time.monotonic = sim.sleep, sim.monotonic
        try:
            return m.convert_cores(name, quantity=quantity, verbose=False,
                                   execute=execute), None
        except m.Aborted as exc:
            return None, exc
    finally:
        time.sleep, time.monotonic = real_sleep, real_mono
        for k, v in saved.items():
            setattr(m, k, v)


# -- the healthy path ------------------------------------------------------
sim = Sim()
result, err = run(sim)
check(err is None, f"a healthy vendor converts without aborting ({err})")
check(result is not None and result["converted"] == 55,
      f"250 typed against a maximum of 55 converts 55, got "
      f"{result and result['converted']}")
check(result is not None and result["verified"],
      "the conversion is verified against the tooltip afterwards")
check(sim.bought(), "OK was clicked")
check(sim.clicked(m.convert_cell_point(2, 3)),
      "the Force Core(High) cell was the one Alt+clicked")
check(("alt_click", 381, 1133) in sim.log,
      f"Alt+click landed on r2c3 (381, 1133); log={sim.clicks()}")

kinds = [a[0] for a in sim.log]
check(kinds.index("alt_click") < kinds.index("details"),
      "the dialog is read only after the click that opens it")
check(kinds.count("details") >= 2,
      "the dialog is read twice: once to identify it, once to check the typing")
ok_at = sim.log.index(("click",) + m.Word.centre.fget(Sim.OK))
check(ok_at > kinds.index("type"),
      "OK is clicked only after the quantity has been typed")
check(len([a for a in sim.log[:ok_at] if a[0] == "details"]) >= 2,
      "both dialog reads happen before OK is clicked")
check(sim.log[-1][0] in ("park", "tooltip"),
      "the cursor is parked at the end")

# -- the Shop window closing part-way through ------------------------------
# A check is only true at the instant it is taken, and between the opening
# check and the click there is a hover plus up to four OCR passes -- seconds in
# which the window can be closed by hand, by a death, or by a stray Escape.
class ShopCloses(Sim):
    """The vendor window is open for the first `n` checks, then gone."""

    def __init__(self, *a, after=1, **kw):
        Sim.__init__(self, *a, **kw)
        self.after = after
        self.checks = 0

    def vendor_shop_open(self, source=None):
        self.checks += 1
        return self.checks <= self.after


s = ShopCloses(after=1)          # open at the top, closed by the click
res, err = run(s)
check(err is not None, "the shop closing before the Alt+click aborts")
check(not [a for a in s.log if a[0] == "alt_click"],
      f"the shop closing before the Alt+click means the grid is NEVER "
      f"clicked ({s.clicks()})")
check(not s.bought(), "and nothing is bought")
check(s.checks >= 2,
      f"the window is re-checked at the click, not just at the top "
      f"(only {s.checks} check(s))")

s = ShopCloses(after=2)          # open through the Alt+click, gone by OK
res, err = run(s)
check(err is not None, "the shop closing before OK aborts")
check(not s.bought(), "the shop closing before OK means OK is NEVER clicked")
check(s.clicked(Sim.CANCEL.centre) or ("escape",) in s.log,
      "and the dialog is closed rather than left up")
check(s.checks >= 3,
      f"the window is checked again before the confirm click "
      f"(only {s.checks} check(s))")

# The healthy path must check it at least three times: entry, click, confirm.
s = Sim()
saved = s.vendor_shop_open
s.checks = 0


def counting(source=None, _s=s, _f=saved):
    _s.checks += 1
    return _f(source)


s.vendor_shop_open = counting
run(s)
check(s.checks >= 3,
      f"a healthy run checks the Shop window at least 3 times, got {s.checks}")


# -- a dialog left open before we start ------------------------------------
class StaleDialog(Sim):
    """A Purchase Item dialog is already up when convert_cores is called."""

    def __init__(self, *a, closes=True, **kw):
        Sim.__init__(self, *a, **kw)
        self.dialog_up = True
        self.closes = closes

    def click(self, x, y, settle=0.15):
        if not self.closes and (x, y) == self.CANCEL.centre:
            self.log.append(("click", x, y))
            return                      # a modal that will not go away
        Sim.click(self, x, y, settle)

    def press_escape(self, settle=0.5):
        self.log.append(("escape",))
        if self.closes:
            self.dialog_up = False


s = StaleDialog()
res, err = run(s)
check(err is None, f"a leftover dialog is cleared and the run continues ({err})")
check(s.clicked(Sim.CANCEL.centre), "the leftover dialog is cancelled")
kinds = [a[0] for a in s.log]
check(kinds.index("click") < kinds.index("alt_click"),
      "the leftover dialog is cleared BEFORE the grid is clicked")
check(res is not None and res["converted"] == 55,
      "and the conversion still goes through")

s = StaleDialog(closes=False)
res, err = run(s)
check(err is not None, "a modal that will not close aborts")
check(not [a for a in s.log if a[0] == "alt_click"],
      "a modal that will not close means the grid is NEVER clicked")

# -- refusals: nothing may be clicked at all -------------------------------
NO_CLICK_CASES = [
    ("the vendor Shop window is not open", Sim(shop_open=False), "Force Core(High)"),
    ("Cabal will not come to the foreground", Sim(focus=False), "Force Core(High)"),
    ("the name is a Set, not a Core", Sim(), "Force Core Set (High)"),
    ("the name is not in the grid", Sim(), "Siena's Unbinding Stone"),
    ("the name is empty", Sim(), ""),
    # The inventory gate. Tab 4 holds the Sets, (1,1) is the stack, (1,2) is
    # where the Cores land -- and none of the slot checks mean anything on the
    # wrong tab, so the tab is verified before they are consulted.
    ("the Inventory panel is closed", Sim(inv_open=False), "Force Core(High)"),
    ("the Inventory is on the wrong tab", Sim(inv_tab=2), "Force Core(High)"),
    ("the active tab cannot be identified", Sim(inv_tab=None),
     "Force Core(High)"),
    ("slot (1,1) is empty", Sim(set_slot_filled=False), "Force Core(High)"),
    ("the landing slot (1,2) is already occupied",
     Sim(core_slot_filled=True), "Force Core(High)"),
]
for label, s, name in NO_CLICK_CASES:
    res, err = run(s, name=name)
    check(err is not None, f"{label}: aborts")
    check(res is None, f"{label}: returns nothing")
    check(not s.clicks(), f"{label}: NOTHING is clicked ({s.clicks()})")
    check(not s.bought(), f"{label}: nothing is bought")

# -- refusals after the dialog is open: cancel, never confirm ---------------
CANCEL_CASES = [
    ("the dialog names a different Core", Sim(dialog_cell=(2, 4))),
    ("the dialog names the Upgrade row", Sim(dialog_cell=(4, 3))),
    ("the dialog prices it at 2 per conversion", Sim(dialog_cost=2)),
    ("the dialog reports nothing to convert", Sim(qty_max=0)),
    ("the field settles HIGHER than was typed", Sim(typing="over")),
]
for label, s in CANCEL_CASES:
    res, err = run(s)
    check(err is not None, f"{label}: aborts")
    check(not s.bought(), f"{label}: OK is NEVER clicked")
    check(s.clicked(Sim.CANCEL.centre) or ("escape",) in s.log,
          f"{label}: the dialog is closed rather than left up")
    check(not s.dialog_up, f"{label}: no modal is left covering the shop")

# ---- THE QUANTITY IS TYPED BLINDLY AND THE DIALOG CLAMPS IT --------------
#
# These three used to cancel the round. They no longer do, and that is the
# point: the QTY maximum is a right-aligned number in a 70px box, and a
# single-digit maximum does not read at any confidence. On 2026-08-11 an
# unreadable "4" cancelled every conversion of 4 Force Core Set (Ultimate)
# that were already paid for; those Sets then blocked the work tab, every
# cycle failed, and the run died after 37 minutes.
#
# So the maximum is advisory, the quantity is typed blindly, and whatever the
# field settles at IS the round -- measured, not predicted. Converting fewer
# than asked costs one more round of a loop that already repeats until every
# Set is listed.
CLAMP_CASES = [
    ("the QTY maximum cannot be read", Sim(qty_max_readable=False), 55),
    ("the typed quantity never lands", Sim(typing="ignored"), 1),
    ("the field settles somewhere lower", Sim(typing="wrong"), 56),
    # Unreadable AFTER typing is the second half of the same wedge: the value
    # field settled on a lone "4" that would not read, so requiring it just
    # cancelled the round one line further down than before.
    ("the field cannot be read back", Sim(typing="unreadable"), None),
]
for label, s, want in CLAMP_CASES:
    res, err = run(s)
    check(err is None, f"{label}: the round proceeds ({err!r})")
    # `purchased`, not `bought()` -- the latter is a bool, and `True == 1`
    # would have let the "never lands" case pass for the wrong reason.
    check(s.purchased == want,
          f"{label}: confirms what the field shows -- {want}, got {s.purchased}")
    check(not s.dialog_up, f"{label}: no modal is left covering the shop")

# The dialog-identity refusals must bail before typing anything at all.
for label, s in CANCEL_CASES[:3]:
    check(not [a for a in s.log if a[0] == "type"],
          f"{label}: refuses before typing a quantity")

# -- the dialog never appears ----------------------------------------------
s = Sim(dialog=False)
res, err = run(s)
check(err is not None, "no dialog after Alt+click: aborts")
check(not s.bought(), "no dialog after Alt+click: nothing is confirmed")
check(("escape",) in s.log, "no dialog after Alt+click: Escape is pressed")
check(s.clicks() == [m.convert_cell_point(2, 3)],
      f"no dialog: the Alt+click is the only input ({s.clicks()})")

# -- the reverse-direction cells are unreachable ---------------------------
every_sim = [Sim(), Sim(shop_open=False), Sim(dialog=False),
             Sim(dialog_cell=(2, 4)), Sim(typing="ignored"), Sim(qty_max=0),
             Sim(dialog_cell=(4, 3)), Sim(dialog_cost=2),
             Sim(qty_max_readable=False), Sim(typing="wrong"),
             Sim(inv_tab=2), Sim(inv_open=False), Sim(cores_land=False),
             Sim(set_slot_filled=False), Sim(core_slot_filled=True)]
for i, s in enumerate(every_sim):
    run(s)
    hit = [p for p in s.clicks() if p in FORBIDDEN_POINTS]
    check(not hit,
          f"scenario {i}: no click ever lands on a CORE->SET cell ({hit})")

# Asking for a Core by name can only ever click that Core's own cell.
for (row, col), (gives, _costs) in sorted(m.CONVERT_TO_CORE.items()):
    s = Sim(cell=(row, col))
    run(s, name=gives)
    check(s.clicked(m.convert_cell_point(row, col)),
          f"{gives} Alt+clicks r{row}c{col}")
    others = [p for p in s.clicks()
              if p in points and points[p] != (row, col)]
    check(not others, f"{gives} clicks no other grid cell ({others})")


# ==========================================================================
section("quantity: typed, clamped, verified")
# ==========================================================================

# The rule under test: whatever is typed, the game clamps to the maximum, and
# the number the script REPORTS must be the clamped one -- not the number it
# asked for. Reporting the request instead of the result is how the sales
# tally once claimed 2.5 billion Alz.
QUANTITIES = [1, 2, 7, 13, 55, 100, 249, 250, 251, 999, 9999]
LIMITS = [1, 2, 3, 7, 13, 25, 54, 55, 56, 100, 249, 250, 251, 400, 1000]
for q in QUANTITIES:
    for limit in LIMITS:
        s = Sim(qty_max=limit)
        res, err = run(s, quantity=q)
        want = min(q, limit)
        # One tab holds GRID_SIZE^2 slots, one of which is the Set stack, so
        # only this many Cores can be COUNTED here; the rest land on a later
        # tab. That ceiling is the game's, not the script's.
        countable = min(want, m.GRID_SIZE * m.GRID_SIZE - 1)
        check(err is None, f"qty={q} limit={limit}: no abort ({err})")
        check(res is not None and res["expected"] == want,
              f"qty={q} limit={limit}: asks for {want}, got "
              f"{res and res.get('expected')}")
        check(res is not None and res["converted"] == countable,
              f"qty={q} limit={limit}: counts {countable}, got "
              f"{res and res['converted']}")
        check(res is not None and res["verified"],
              f"qty={q} limit={limit}: verified")
        check(("type", q) in s.log,
              f"qty={q} limit={limit}: the full {q} is typed, and the game "
              "clamps it -- the script does not pre-compute the maximum")
        check(s.field == want,
              f"qty={q} limit={limit}: the field settles at {want}")

# The typed value is always the caller's number, never the clamp. This is what
# lets CONVERT_QUANTITY stay a flat 250 instead of reading the panel first.
for limit in (1, 7, 55, 250, 900):
    s = Sim(qty_max=limit)
    run(s, quantity=m.CONVERT_QUANTITY)
    typed = [a[1] for a in s.log if a[0] == "type"]
    check(typed == [250],
          f"limit={limit}: 250 is typed verbatim, got {typed}")

# A default call uses CONVERT_QUANTITY.
s = Sim(qty_max=999)
res, _ = run(s, quantity=m.CONVERT_QUANTITY)
check(res["expected"] == 250, "a default call asks for a full 250 when held")
check(res["countable"] == m.GRID_SIZE * m.GRID_SIZE - 1,
      "but only a tab's worth can be counted, because Cores do not stack and "
      "250 of them do not fit on one tab")
check(res["verified"], "and it verifies against what it could count")


# ==========================================================================
section("the after-check: counting what actually arrived")
# ==========================================================================

# Cores do not stack, so the number that arrived is the number of NEW slots.
# That is a measurement, not an inference from a before/after subtraction, and
# it cannot be defeated by the colour the vendor draws its text in -- which is
# what broke the tooltip this replaced.
s = Sim()
res, err = run(s)
check(err is None, f"a healthy conversion does not raise ({err})")
check(res["converted"] == 55, f"55 slots filled, got {res['converted']}")
check(res["landed"] is True, "the landing slot is occupied")
check(res["verified"], "count and landing slot agreeing is what verifies it")

s = Sim(cores_land=False)
res, err = run(s)
check(err is None, "Cores failing to arrive does not raise; money is spent")
check(res["converted"] == 0, f"nothing arrived, got {res['converted']}")
check(res["landed"] is False, "and the landing slot is still empty")
check(not res["verified"], "so nothing is claimed")

# The game clamps to the free space available, and counting slots gets that
# right for free -- where a subtraction of held counts would have reported the
# number ASKED FOR and been quietly wrong.
s = Sim(qty_max=55, free_slots=25)
res, err = run(s)
check(err is None, "a partial conversion does not raise")
check(res["converted"] == 25,
      f"25 free slots means 25 converted, got {res['converted']}")
check(res["expected"] == 55, "while 55 is what it asked for")
check(not res["verified"],
      "the gap between asked and arrived is reported, not hidden")

# Across a range of sizes, the count tracks the slots and nothing else.
for size in (1, 2, 7, 13, 36, 55, 63):
    s = Sim(qty_max=size)
    res, _ = run(s)
    check(res["converted"] == size,
          f"limit {size}: {size} slots filled, got {res['converted']}")
    check(res["verified"], f"limit {size}: verified")

# The Sets' own slot is never counted as an arrival: it was occupied before.
s = Sim(qty_max=3)
res, _ = run(s)
check(res["converted"] == 3,
      f"the pre-existing Set stack is not counted, got {res['converted']}")


# ==========================================================================
section("execute=False looks but does not touch")
# ==========================================================================

for (row, col), (gives, _c) in sorted(m.CONVERT_TO_CORE.items()):
    s = Sim(cell=(row, col))
    res, err = run(s, name=gives, execute=False)
    check(err is None, f"{gives}: a dry look does not abort")
    check(not s.clicks(), f"{gives}: a dry look clicks nothing")
    check(res["would_convert"] == 250,
          f"{gives}: reports the quantity it would type, got "
          f"{res['would_convert']}")
    check(res["converted"] == 0, f"{gives}: reports converting nothing")

# It still refuses the things that are wrong, rather than reporting a plan for
# a trade it would never make.
for label, s, name in NO_CLICK_CASES[:6]:
    res, err = run(s, name=name, execute=False)
    check(err is not None, f"execute=False still refuses when {label}")


# ==========================================================================
section("alt_click refuses on its own, without help from its caller")
# ==========================================================================

# The guard lives in the PRIMITIVE, not in the caller.
#
# Alt+click exists for one thing: the SET/CORE grid, whose coordinates sit low
# and left. With no vendor window there that is bare ground, and a click on the
# ground is click-to-move -- the character walks off, and the NPC every other
# part of this file looks for goes with it.
#
# It happened. A diagnostic script PRINTED vendor_shop_open() and clicked
# regardless; convert_cores checks it twice and would have refused, but a guard
# a caller can skip is not a guard. Same lesson as NO_INPUT, and as the
# scroll_wheel camera zoom before it.
_saved_open = m.vendor_shop_open
try:
    # NO_INPUT stays ON throughout. alt_click checks this precondition BEFORE
    # its suppression early-return precisely so it can be tested with input
    # suppressed -- see the comment there.
    m.vendor_shop_open = lambda source=None: False
    refused = False
    try:
        m.alt_click(*m.convert_cell_point(2, 3))
    except m.Aborted:
        refused = True
    except Exception:
        refused = False
    check(refused,
          "alt_click refuses when the vendor Shop is shut, even though the "
          "caller never asked it to check")

    # And it refuses BEFORE moving the cursor: a move alone is harmless, but
    # the refusal has to come from the state, not from the click failing.
    for point in [m.convert_cell_point(2, 3), m.convert_cell_point(4, 1),
                  (10, 10), (2000, 1300)]:
        raised = False
        try:
            m.alt_click(*point)
        except m.Aborted:
            raised = True
        except Exception:
            raised = False
        check(raised, f"alt_click at {point} is refused with the shop shut")

    # With the shop open it does NOT refuse -- the guard must not block the
    # ordinary work it exists to allow.
    m.vendor_shop_open = lambda source=None: True
    allowed = True
    try:
        m.alt_click(*m.convert_cell_point(2, 3))
    except m.Aborted:
        allowed = False
    check(allowed, "and it does not refuse when the vendor Shop IS open")
finally:
    m.vendor_shop_open = _saved_open


# ==========================================================================
section("golden frame (skipped if the capture is not present)")
# ==========================================================================

# Gitignored: unit_tests/corpus/*.png. The frame is a live session.
GOLDEN = _ROOT / "unit_tests" / "corpus" / "convert_dialog_force_high.png"
if GOLDEN.exists():
    from PIL import Image
    shot = Image.open(GOLDEN)

    check(m.vendor_shop_open(shot),
          "the NPC Shop window is recognised on the real frame")

    buttons = m.mass_purchase_open(shot)
    check(buttons is not None, "the Purchase Item dialog is recognised")
    if buttons:
        ok, cancel = buttons
        # Within a few pixels, not exactly. This is an OCR centroid, and it
        # moves by a pixel when the crop it was read from changes -- which it
        # did when the buttons were given their own tighter region. A click
        # tolerates that; an equality assertion does not, and would fail for a
        # reason that has nothing to do with correctness.
        def near(got, want, slack=4):
            return (abs(got[0] - want[0]) <= slack
                    and abs(got[1] - want[1]) <= slack)

        check(near(ok.centre, (1285, 909)),
              f"OK is within a few px of (1285, 909), got {ok.centre}")
        check(near(cancel.centre, (1467, 909)),
              f"Cancel is within a few px of (1467, 909), got {cancel.centre}")
        check(ok.centre[0] < cancel.centre[0], "OK sits left of Cancel")

    d = m.mass_purchase_details(shot)
    check("Force Core(High)" in d["item"], f"item reads {d['item']!r}")
    check(d["held"] == 55, f"held reads 55, got {d['held']}")
    check(d["cost"] == 1, f"cost reads 1, got {d['cost']}")
    check(d["qty"] == 1, f"the QTY field reads 1, got {d['qty']}")
    check(d["qty_max"] == 55, f"the QTY maximum reads 55, got {d['qty_max']}")
    check(d["qty_max"] != 554,
          "the maximum excludes the spinner arrow, which read as 554 when the "
          "region was too wide")
    check("Force Core Set (High)" in d["price_line"],
          f"the price line reads {d['price_line']!r}")

    check(m.mass_purchase_matches(2, 3, d),
          "the real dialog matches r2c3, Force Core(High)")
    for r in range(1, 5):
        for c in range(1, 6):
            if (r, c) != (2, 3):
                check(not m.mass_purchase_matches(r, c, d),
                      f"the real dialog does not match r{r}c{c}")
else:
    print(f"  (no golden frame at {GOLDEN}; image checks skipped)")
    skipped.append("golden frame at {GOLDEN}")

# -- the tooltip, in both colours -------------------------------------------
# The vendor draws an affordable price in white and an unaffordable one in RED,
# and red is the colour greyscale throws away: pure red sits at luminance ~54
# against a panel around 30, so the line that reads at 96% in white came back
# as 'ee'. That matters because 0 held is the ANSWER, not a fault -- it means
# the conversion finished. Both frames are from the live run on 2026-08-07,
# before and after converting 55 Sets.
CORPUS = _ROOT / "unit_tests" / "corpus"
TIP_FRAMES = [
    (CORPUS / "convert_tip_held55.png", 55, "white"),
    (CORPUS / "convert_tip_held0.png", 0, "red"),
]
if all(p.exists() for p, _, _ in TIP_FRAMES):
    from PIL import Image as _Img

    for path, want_held, colour in TIP_FRAMES:
        shot = _Img.open(path)
        text = m._tooltip_lines(shot, m.CONVERT_TIP_REGION)
        line, held, cost = m._price_from_lines(text)
        if held is None:
            red = m._warm_text_image(shot, m.CONVERT_TIP_REGION)
            rtext = m._tooltip_lines(red, (0, 0, red.width, red.height))
            line, held, cost = m._price_from_lines(rtext)
        check(held == want_held,
              f"{path.name} ({colour} price line): held reads {want_held}, "
              f"got {held}")
        check(cost == 1, f"{path.name}: cost reads 1, got {cost}")
        check(m._names_agree(line, "Force Core Set (High)"),
              f"{path.name}: the payment line names Force Core Set (High), "
              f"read {line!r}")
        check(not m._names_agree(line, "Force Core Set (Highest)"),
              f"{path.name}: and is NOT taken for the Highest grade")

    # The red pass must not be reached on a frame the normal pass can read, and
    # vice versa: each colour is legible to exactly one of them.
    shot = _Img.open(CORPUS / "convert_tip_held55.png")
    _, held, _ = m._price_from_lines(m._tooltip_lines(shot, m.CONVERT_TIP_REGION))
    check(held == 55, "the white line needs no red pass at all")
    shot = _Img.open(CORPUS / "convert_tip_held0.png")
    _, held, _ = m._price_from_lines(m._tooltip_lines(shot, m.CONVERT_TIP_REGION))
    check(held is None,
          "the red line is genuinely invisible to the normal pass, so the "
          "fallback is load-bearing rather than decorative")
else:
    print("  (no tooltip frames in the corpus; colour checks skipped)")
    skipped.append("tooltip frames in the corpus")

# -- the inventory readers, on real frames ----------------------------------
# Ground truth read off the tab strip by eye: the selected tab is drawn RAISED.
# Converting 55 Sets left tab II with 56 items (the 55 new Cores plus one that
# was already there) filling rows 1-7 solid, and row 8 empty.
INV_FRAMES = [
    (CORPUS / "convert_tip_held0.png", 2, "after converting 55"),
    (CORPUS / "convert_dialog_force_high.png", 1, "before converting"),
]
if all(p.exists() for p, _, _ in INV_FRAMES):
    from PIL import Image as _Img2

    for path, want_tab, when in INV_FRAMES:
        shot = _Img2.open(path)
        origin = m.inventory_origin(shot)
        check(origin is not None,
              f"{path.name}: the Inventory panel is anchored")
        got_tab = m.active_inventory_tab(shot)
        check(got_tab == want_tab,
              f"{path.name} ({when}): active tab reads {want_tab}, "
              f"got {got_tab}")

    # The tab test must not merely be right, it must be right by a MARGIN.
    # Reading the numerals instead of the raised edge produced a confident 8
    # for a frame showing tab I -- and select_inventory_tab treats a confident
    # answer as "already on the right tab" and skips the click.
    for path, want_tab, _when in INV_FRAMES:
        shot = _Img2.open(path)
        origin = m.inventory_origin(shot)
        levels = []
        for tab in range(1, m.TAB_COUNT + 1):
            cx, cy = m.tab_centre(origin, tab)
            cell = shot.crop((cx - 22, cy - 25, cx + 22, cy - 15)).convert("L")
            data = list(cell.getdata())
            levels.append(sum(data) / len(data))
        ranked = sorted(levels)
        median = (ranked[m.TAB_COUNT // 2 - 1] + ranked[m.TAB_COUNT // 2]) / 2
        margin = max(levels) - median
        check(margin >= 2 * m.TAB_ACTIVE_MARGIN,
              f"{path.name}: the active tab clears the median by {margin:.1f}, "
              f"comfortably past the {m.TAB_ACTIVE_MARGIN} bar")

    # The conversion's own result, read as pixels: 55 Cores arrived, so rows
    # 1-7 are solid and row 8 is bare. Nothing here asks what the items ARE --
    # that is the point, since the check has to work for an item it has never
    # seen.
    shot = _Img2.open(CORPUS / "convert_tip_held0.png")
    origin = m.inventory_origin(shot)
    filled = set(m.occupied_slots(shot, origin))
    check(len(filled) == 56,
          f"56 occupied slots after the conversion, got {len(filled)}")
    check(all(r <= 7 for r, _ in filled),
          f"every occupied slot is in rows 1-7, got rows "
          f"{sorted({r for r, _ in filled})}")
    check(not any((8, c) in filled for c in range(1, 9)),
          "row 8 is entirely empty")
    check(m.CONVERT_CORE_SLOT in filled,
          f"the landing slot {m.CONVERT_CORE_SLOT} holds the Cores")

    # An empty slot and a full one must not sit near the threshold: if they do,
    # the check passes today and flakes on the next item that happens to be
    # dark.
    def spread(im, org, r, c):
        cx, cy = m.slot_centre_at(org, r, c)
        cell = im.crop((cx - m.SLOT_INSET, cy - m.SLOT_INSET,
                        cx + m.SLOT_INSET, cy + m.SLOT_INSET)).convert("L")
        data = list(cell.getdata())
        mean = sum(data) / len(data)
        return (sum((v - mean) ** 2 for v in data) / len(data)) ** 0.5

    empties = [spread(shot, origin, 8, c) for c in range(1, 9)]
    fulls = [spread(shot, origin, r, c)
             for r in range(1, 8) for c in range(1, 9)]
    check(max(empties) < m.SLOT_OCCUPIED_STDEV,
          f"the busiest empty slot ({max(empties):.1f}) is under the bar "
          f"({m.SLOT_OCCUPIED_STDEV})")
    check(min(fulls) > m.SLOT_OCCUPIED_STDEV,
          f"the flattest occupied slot ({min(fulls):.1f}) is over the bar")
    check(min(fulls) - max(empties) > 8.0,
          f"and the two are separated by a real margin "
          f"({min(fulls) - max(empties):.1f} grey levels), not a hair")
else:
    print("  (no inventory frames in the corpus; slot checks skipped)")
    skipped.append("inventory frames in the corpus")

# -- an inventory slot's tooltip, which is ORANGE on a translucent panel -----
# The title is the only line carrying the GRADE, and greyscale loses it: a live
# hover of 36 x Force Core Set (High) read as "Force Core", dropping exactly
# the part that separates High from Highest. The warm-colour pass is what
# recovers it, and this frame is that hover.
SLOT_TIP = CORPUS / "slot_tip_force_set_high.png"
if SLOT_TIP.exists():
    from PIL import Image as _Img3

    shot = _Img3.open(SLOT_TIP)
    origin = m.inventory_origin(shot)
    check(origin is not None, "slot_tip frame: the Inventory is anchored")
    sx, sy = m.slot_centre_at(origin, 1, 1)
    reg = m.slot_tip_region(sx, sy)

    plain = m._tooltip_lines(shot, reg)
    check(not any(m._names_agree(l, "Force Core Set (High)") for l in plain),
          "greyscale alone genuinely cannot read the orange title, so the "
          "warm pass is load-bearing rather than decorative")

    warm = m._warm_text_image(shot, reg)
    warm_lines = m._tooltip_lines(warm, (0, 0, warm.width, warm.height))
    check(any(m._names_agree(l, "Force Core Set (High)") for l in warm_lines),
          f"the warm pass reads the title, got {warm_lines[:3]}")
    check(not any(m._names_agree(l, "Force Core Set (Highest)")
                  for l in warm_lines),
          "and it is not taken for the Highest grade")
    check(not any(m._names_agree(l, "Force Core Set (Medium)")
                  for l in warm_lines),
          "nor for any other grade")

    # The region has to actually contain the tooltip. A slot tooltip renders to
    # the LEFT of the panel; reading it through CONVERT_TIP_REGION, which
    # covers the shop grid in the opposite corner, returned fragments of the
    # game world and refused a conversion whose Sets were sitting in the slot.
    wrong = m._tooltip_lines(shot, m.CONVERT_TIP_REGION)
    check(not any(m._names_agree(l, "Force Core Set (High)") for l in wrong),
          "the shop grid's tooltip region does not see an inventory tooltip")
    check(reg[2] <= sx, "the slot tooltip region sits left of the slot")
    check(reg[0] < reg[2] and reg[1] < reg[3],
          f"the slot tooltip region {reg} is the right way round")
else:
    print("  (no slot tooltip frame in the corpus; title checks skipped)")
    skipped.append("slot tooltip frame in the corpus")

# The conversion tab and the relist work tab are the same tab. That is a real
# coupling -- require_empty_work_tab() refuses to start a relist run while tab
# 4 holds anything -- so it is asserted here rather than left to drift.
check(m.CONVERT_INVENTORY_TAB == m.WORK_TAB,
      f"the conversion tab ({m.CONVERT_INVENTORY_TAB}) is the work tab "
      f"({m.WORK_TAB})")
check(m.CONVERT_SET_SLOT != m.CONVERT_CORE_SLOT,
      "the Sets and the Cores that replace them use different slots")
for slot in (m.CONVERT_SET_SLOT, m.CONVERT_CORE_SLOT):
    check(1 <= slot[0] <= m.GRID_SIZE and 1 <= slot[1] <= m.GRID_SIZE,
          f"slot {slot} is inside the {m.GRID_SIZE}x{m.GRID_SIZE} grid")

# Leading decoration must not become a letter.

# Leading decoration must not become a letter. _floor_key folds '[' onto 'i',
# so a bracket picked up beside the name survives as part of the key unless it
# is stripped first -- which is exactly what the red pass produces.
for noise in ["[ ", "* ", "|", "• ", "  ", "] [", "~ "]:
    check(m._names_agree(f"{noise}Force Core Set (High) 0 / 1",
                         "Force Core Set (High)"),
          f"{noise!r} before the name does not change what it is")
    check(not m._names_agree(f"{noise}Force Core Set (High) 0 / 1",
                             "Force Core Set (Highest)"),
          f"{noise!r} before the name still does not match Highest")

# The other direction, which a positive-only test cannot see: a reader stuck at
# True passes every check above. These frames are the Agent Shop and the
# Purchase tab -- real screens, none of them the NPC vendor -- so the window
# check has to say no to them.
NEGATIVE = sorted((_ROOT / "unit_tests" / "corpus").glob("run_*.png"))[:4]
NEGATIVE += sorted((_ROOT / "unit_tests" / "corpus" / "buying").glob("*.png"))[:2]
if NEGATIVE:
    from PIL import Image as _Image
    for frame in NEGATIVE:
        shot = _Image.open(frame)
        check(not m.vendor_shop_open(shot),
              f"{frame.name} is not the NPC vendor window")
        check(m.mass_purchase_open(shot) is None,
              f"{frame.name} has no Purchase Item dialog on it")
else:
    print("  (no non-vendor frames in the corpus; negative checks skipped)")
    skipped.append("non-vendor frames in the corpus")


# ==========================================================================
section("the listings scroll never reaches the Purchase tab")
# ==========================================================================

# The wheel is the one input that damages state the script cannot see, and it
# had two guards: "is the Trade window up" and "is an opaque panel covering the
# area". The PURCHASE tab satisfies both, so a listings-table scroll could fire
# while the buy tab was showing -- which scrolls the OFFERS.
#
# That is worse than wasted motion. The entire buying design rests on row 1
# being the cheapest listing; move the offers and row 1 becomes whatever is at
# the top now, so "always buy row 1" silently starts meaning something else.
# Seen live on 2026-08-07: a restock left the Purchase tab showing and the next
# capacity check enumerated the shop.
_saved_scroll = (m.trade_window_open, m.panel_covers_trade_area,
                 m.register_tab_open, m.record)
try:
    m.record = lambda *a, **k: None
    m.trade_window_open = lambda src=None: True
    m.panel_covers_trade_area = lambda *a, **k: True

    m.register_tab_open = lambda src=None: True
    check(m.table_scrollable(verbose=False) is True,
          "the listings table scrolls on the Register tab, as it must")

    m.register_tab_open = lambda src=None: False
    check(m.table_scrollable(verbose=False) is False,
          "but NOT on the Purchase tab -- that wheel would move the offers "
          "and row 1 would stop meaning the cheapest one")

    # The older guard still holds: no window at all is still a camera zoom.
    m.trade_window_open = lambda src=None: False
    m.register_tab_open = lambda src=None: True
    check(m.table_scrollable(verbose=False) is False,
          "and a shut window is still refused -- the wheel would zoom the "
          "camera and lose the NPC")

    m.trade_window_open = lambda src=None: True
    m.panel_covers_trade_area = lambda *a, **k: False
    check(m.table_scrollable(verbose=False) is False,
          "and so is a window the motion probe cannot confirm")
finally:
    (m.trade_window_open, m.panel_covers_trade_area, m.register_tab_open,
     m.record) = _saved_scroll


# ==========================================================================
print(f"\n{'=' * 60}")
print(f"convert_cores: {count} checks, {len(fails)} failed"
      + (f", {len(skipped)} IMAGE SECTION(S) SKIPPED" if skipped else ""))
if skipped:
    # Named, not just counted. These are the only checks in the file that
    # touch real pixels, and a mutation to the tooltip geometry is caught by
    # nothing else -- so a green run without them is a weaker claim, and it
    # should say so rather than read the same as a full one.
    print("  no corpus frames for: " + "; ".join(skipped))
    print("  -> the OCR geometry was NOT exercised. Run "
          "unit_tests/capture_goldens.py to record frames.")
if fails:
    for f in fails[:25]:
        print(f"  FAIL  {f}")
    if len(fails) > 25:
        print(f"  ... and {len(fails) - 25} more")
    sys.exit(1)
print("all green")
