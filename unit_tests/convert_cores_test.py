import sys
import time
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import os as _os_guard
import sys as _sys_guard
_sys_guard.path.insert(0, _os_guard.path.dirname(
    _os_guard.path.abspath(__file__)))
import _no_input_guard

import trade as m

m.NO_INPUT = True

fails = []
count = 0
skipped = []
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


FORBIDDEN_POINTS = {m.convert_cell_point(r, c) for (r, c) in m.CONVERT_TO_SET}


section("grid geometry")

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


section("name resolution: cores in, sets never")

def variants(name):
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

for junk in ["", "   ", "Siena's Unbinding Stone", "Epic Booster (Highest)",
             "Force", "Core", "Set", "Force Core", "Upgrade Core",
             "Force Core Set", "Highest", "Bike", "Force Core(Legendary)",
             "Astral Bike Card", "SIGmetal Suit", "Force Core Set (High) X 62"]:
    got = m.convert_cell_for(junk)
    check(got is None, f"{junk!r} resolves to nothing, got {got}")

for (row, col), (gives, _c) in sorted(m.CONVERT_TO_CORE.items()):
    for junk in [f"{gives} x 62", f"{gives} X 250", f"{gives} and something",
                 f"Superior {gives}"]:
        check(m.convert_cell_for(junk) is None,
              f"{junk!r} does not resolve to a cell by near-miss")

for (row, col), (gives, _c) in sorted(m.CONVERT_TO_CORE.items()):
    got = m.convert_cell_for(gives)
    check(got == (row, col), f"{gives} resolves to its own cell, got {got}")
    for (other_rc, (other, _)) in sorted(m.CONVERT_TO_CORE.items()):
        if other_rc != (row, col):
            check(m.convert_cell_for(other) != (row, col),
                  f"{other} does not resolve to r{row}c{col}, which is {gives}")

probes = []
for _, (gives, costs) in m.CONVERT_TO_CORE.items():
    probes += variants(gives) + variants(costs)
probes += ["", "Force Core Set", "Upgrade Core Set", "nonsense"]
for text in probes:
    got = m.convert_cell_for(text)
    check(got is None or got not in m.CONVERT_TO_SET,
          f"convert_cell_for({text!r}) never returns a CORE->SET cell")


section("dialog identity: what it says vs what was meant")

def detail_for(row, col, *, qty=1, qty_max=55, held=None, cost=1,
               item=None, price=None):
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


for (row, col) in sorted(m.CONVERT_TO_CORE):
    d = detail_for(row, col)
    for r2 in range(1, 5):
        for c2 in range(1, 6):
            want = (r2, c2) == (row, col)
            got = m.mass_purchase_matches(r2, c2, d)
            check(got == want,
                  f"dialog for r{row}c{col} vs cell r{r2}c{c2}: "
                  f"expected {want}, got {got}")

for col in range(1, 6):
    d = detail_for(2, col)
    check(not m.mass_purchase_matches(1, col, d),
          f"a Force dialog does not match the CORE->SET cell r1c{col}")
    d = detail_for(4, col)
    check(not m.mass_purchase_matches(3, col, d),
          f"an Upgrade dialog does not match the CORE->SET cell r3c{col}")

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

for bad in [(0, 0), (5, 1), (2, 0), (2, 6), (1, 3), (3, 3)]:
    check(not m.mass_purchase_matches(bad[0], bad[1], detail_for(2, 3)),
          f"cell {bad} is not a SET->CORE cell and matches nothing")


section("price-line parsing")

class FakeShot:
    pass


def parse_price(text):
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


section("the sequence: a simulated vendor that records every click")

class Sim:

    OK = m.Word("OK", 1273, 902, 1297, 916, 96.0)
    CANCEL = m.Word("Cancel", 1438, 901, 1496, 917, 96.0)

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
        self.typing = typing
        self.focus = focus
        self.inv_open = inv_open
        self.inv_tab = inv_tab
        self.set_slot_filled = set_slot_filled
        self.core_slot_filled = core_slot_filled
        self.cores_land = cores_land
        self.free_slots = free_slots

        self.log = []
        self.field = 1
        self.dialog_up = False
        self.purchased = None
        self.clock = 1000.0

    def focus_game(self, settle=0.35):
        self.log.append(("focus",))
        return self.focus

    def vendor_shop_open(self, source=None):
        return self.shop_open

    def active_vendor_tab(self, source=None):
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
            self.field = value + 5
            return
        self.field = min(value, self.qty_max)

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

    def clicks(self):
        return [(a[1], a[2]) for a in self.log if a[0] in ("click", "alt_click")]

    def clicked(self, point):
        return point in self.clicks()

    def bought(self):
        return self.clicked(self.OK.centre)


def run(sim, name="Force Core(High)", quantity=250, execute=True):
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

class ShopCloses(Sim):

    def __init__(self, *a, after=1, **kw):
        Sim.__init__(self, *a, **kw)
        self.after = after
        self.checks = 0

    def vendor_shop_open(self, source=None):
        self.checks += 1
        return self.checks <= self.after


s = ShopCloses(after=1)
res, err = run(s)
check(err is not None, "the shop closing before the Alt+click aborts")
check(not [a for a in s.log if a[0] == "alt_click"],
      f"the shop closing before the Alt+click means the grid is NEVER "
      f"clicked ({s.clicks()})")
check(not s.bought(), "and nothing is bought")
check(s.checks >= 2,
      f"the window is re-checked at the click, not just at the top "
      f"(only {s.checks} check(s))")

s = ShopCloses(after=2)
res, err = run(s)
check(err is not None, "the shop closing before OK aborts")
check(not s.bought(), "the shop closing before OK means OK is NEVER clicked")
check(s.clicked(Sim.CANCEL.centre) or ("escape",) in s.log,
      "and the dialog is closed rather than left up")
check(s.checks >= 3,
      f"the window is checked again before the confirm click "
      f"(only {s.checks} check(s))")

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


class StaleDialog(Sim):

    def __init__(self, *a, closes=True, **kw):
        Sim.__init__(self, *a, **kw)
        self.dialog_up = True
        self.closes = closes

    def click(self, x, y, settle=0.15):
        if not self.closes and (x, y) == self.CANCEL.centre:
            self.log.append(("click", x, y))
            return
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

NO_CLICK_CASES = [
    ("the vendor Shop window is not open", Sim(shop_open=False), "Force Core(High)"),
    ("Cabal will not come to the foreground", Sim(focus=False), "Force Core(High)"),
    ("the name is a Set, not a Core", Sim(), "Force Core Set (High)"),
    ("the name is not in the grid", Sim(), "Siena's Unbinding Stone"),
    ("the name is empty", Sim(), ""),
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

CLAMP_CASES = [
    ("the QTY maximum cannot be read", Sim(qty_max_readable=False), 55),
    ("the typed quantity never lands", Sim(typing="ignored"), 1),
    ("the field settles somewhere lower", Sim(typing="wrong"), 56),
    ("the field cannot be read back", Sim(typing="unreadable"), None),
]
for label, s, want in CLAMP_CASES:
    res, err = run(s)
    check(err is None, f"{label}: the round proceeds ({err!r})")
    check(s.purchased == want,
          f"{label}: confirms what the field shows -- {want}, got {s.purchased}")
    check(not s.dialog_up, f"{label}: no modal is left covering the shop")

for label, s in CANCEL_CASES[:3]:
    check(not [a for a in s.log if a[0] == "type"],
          f"{label}: refuses before typing a quantity")

s = Sim(dialog=False)
res, err = run(s)
check(err is not None, "no dialog after Alt+click: aborts")
check(not s.bought(), "no dialog after Alt+click: nothing is confirmed")
check(("escape",) in s.log, "no dialog after Alt+click: Escape is pressed")
check(s.clicks() == [m.convert_cell_point(2, 3)],
      f"no dialog: the Alt+click is the only input ({s.clicks()})")

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

for (row, col), (gives, _costs) in sorted(m.CONVERT_TO_CORE.items()):
    s = Sim(cell=(row, col))
    run(s, name=gives)
    check(s.clicked(m.convert_cell_point(row, col)),
          f"{gives} Alt+clicks r{row}c{col}")
    others = [p for p in s.clicks()
              if p in points and points[p] != (row, col)]
    check(not others, f"{gives} clicks no other grid cell ({others})")


section("quantity: typed, clamped, verified")

QUANTITIES = [1, 2, 7, 13, 55, 100, 249, 250, 251, 999, 9999]
LIMITS = [1, 2, 3, 7, 13, 25, 54, 55, 56, 100, 249, 250, 251, 400, 1000]
for q in QUANTITIES:
    for limit in LIMITS:
        s = Sim(qty_max=limit)
        res, err = run(s, quantity=q)
        want = min(q, limit)
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

for limit in (1, 7, 55, 250, 900):
    s = Sim(qty_max=limit)
    run(s, quantity=m.CONVERT_QUANTITY)
    typed = [a[1] for a in s.log if a[0] == "type"]
    check(typed == [250],
          f"limit={limit}: 250 is typed verbatim, got {typed}")

s = Sim(qty_max=999)
res, _ = run(s, quantity=m.CONVERT_QUANTITY)
check(res["expected"] == 250, "a default call asks for a full 250 when held")
check(res["countable"] == m.GRID_SIZE * m.GRID_SIZE - 1,
      "but only a tab's worth can be counted, because Cores do not stack and "
      "250 of them do not fit on one tab")
check(res["verified"], "and it verifies against what it could count")


section("the after-check: counting what actually arrived")

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

s = Sim(qty_max=55, free_slots=25)
res, err = run(s)
check(err is None, "a partial conversion does not raise")
check(res["converted"] == 25,
      f"25 free slots means 25 converted, got {res['converted']}")
check(res["expected"] == 55, "while 55 is what it asked for")
check(not res["verified"],
      "the gap between asked and arrived is reported, not hidden")

for size in (1, 2, 7, 13, 36, 55, 63):
    s = Sim(qty_max=size)
    res, _ = run(s)
    check(res["converted"] == size,
          f"limit {size}: {size} slots filled, got {res['converted']}")
    check(res["verified"], f"limit {size}: verified")

s = Sim(qty_max=3)
res, _ = run(s)
check(res["converted"] == 3,
      f"the pre-existing Set stack is not counted, got {res['converted']}")


section("execute=False looks but does not touch")

for (row, col), (gives, _c) in sorted(m.CONVERT_TO_CORE.items()):
    s = Sim(cell=(row, col))
    res, err = run(s, name=gives, execute=False)
    check(err is None, f"{gives}: a dry look does not abort")
    check(not s.clicks(), f"{gives}: a dry look clicks nothing")
    check(res["would_convert"] == 250,
          f"{gives}: reports the quantity it would type, got "
          f"{res['would_convert']}")
    check(res["converted"] == 0, f"{gives}: reports converting nothing")

for label, s, name in NO_CLICK_CASES[:6]:
    res, err = run(s, name=name, execute=False)
    check(err is not None, f"execute=False still refuses when {label}")


section("alt_click refuses on its own, without help from its caller")

_saved_open = m.vendor_shop_open
try:
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

    m.vendor_shop_open = lambda source=None: True
    allowed = True
    try:
        m.alt_click(*m.convert_cell_point(2, 3))
    except m.Aborted:
        allowed = False
    check(allowed, "and it does not refuse when the vendor Shop IS open")
finally:
    m.vendor_shop_open = _saved_open


section("golden frame (skipped if the capture is not present)")

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

    wrong = m._tooltip_lines(shot, m.CONVERT_TIP_REGION)
    check(not any(m._names_agree(l, "Force Core Set (High)") for l in wrong),
          "the shop grid's tooltip region does not see an inventory tooltip")
    check(reg[2] <= sx, "the slot tooltip region sits left of the slot")
    check(reg[0] < reg[2] and reg[1] < reg[3],
          f"the slot tooltip region {reg} is the right way round")
else:
    print("  (no slot tooltip frame in the corpus; title checks skipped)")
    skipped.append("slot tooltip frame in the corpus")

check(m.CONVERT_INVENTORY_TAB == m.WORK_TAB,
      f"the conversion tab ({m.CONVERT_INVENTORY_TAB}) is the work tab "
      f"({m.WORK_TAB})")
check(m.CONVERT_SET_SLOT != m.CONVERT_CORE_SLOT,
      "the Sets and the Cores that replace them use different slots")
for slot in (m.CONVERT_SET_SLOT, m.CONVERT_CORE_SLOT):
    check(1 <= slot[0] <= m.GRID_SIZE and 1 <= slot[1] <= m.GRID_SIZE,
          f"slot {slot} is inside the {m.GRID_SIZE}x{m.GRID_SIZE} grid")


for noise in ["[ ", "* ", "|", "• ", "  ", "] [", "~ "]:
    check(m._names_agree(f"{noise}Force Core Set (High) 0 / 1",
                         "Force Core Set (High)"),
          f"{noise!r} before the name does not change what it is")
    check(not m._names_agree(f"{noise}Force Core Set (High) 0 / 1",
                             "Force Core Set (Highest)"),
          f"{noise!r} before the name still does not match Highest")

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


section("the listings scroll never reaches the Purchase tab")

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


print(f"\n{'=' * 60}")
print(f"convert_cores: {count} checks, {len(fails)} failed"
      + (f", {len(skipped)} IMAGE SECTION(S) SKIPPED" if skipped else ""))
if skipped:
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
