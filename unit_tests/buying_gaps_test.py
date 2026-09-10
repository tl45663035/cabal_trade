import os
import sys
import tempfile
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_SCRATCH_DB = _Path(tempfile.gettempdir()) / "cabal_gaps_test.db"
if _SCRATCH_DB.resolve().parent != _Path(tempfile.gettempdir()).resolve():
    raise SystemExit(f"refusing to run: {_SCRATCH_DB} is not in the temp dir")
os.environ["CABAL_SALES_DB"] = str(_SCRATCH_DB)

import os as _os_guard
import sys as _sys_guard
_sys_guard.path.insert(0, _os_guard.path.dirname(
    _os_guard.path.abspath(__file__)))
import _no_input_guard

import trade as m

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
    return m.Word(text, x - 10, y - 6, x + 10, y + 6, conf)


class Row:

    def __init__(self, name):
        self.name = name
        self.action = "change"


class Frame:
    pass


def with_words(words, fn, *args, **kwargs):
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


section("purchase_confirm: the dialog buy_offer refuses on")

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

check(with_words([W("Something", 1100, 450), W("Buy", 1150, 880)],
                 m.purchase_confirm, Frame()) is None,
      "no 'Purchase' anywhere means no dialog -- a different modal must not "
      "be mistaken for this one and clicked")
check(with_words([W("Purchase", 1100, 450), W("Cancel", 1350, 880)],
                 m.purchase_confirm, Frame()) is None,
      "a dialog with no Buy button is None, so nothing is clicked blind")
check(with_words([], m.purchase_confirm, Frame()) is None,
      "an empty frame is None")

high = [W("Purchase", 1100, 450), W("Buy", 1150, 700), W("Cancel", 1350, 700)]
check(with_words(high, m.purchase_confirm, Frame()) is None,
      "a 'Buy' above the button band is ignored -- the table's own row buttons "
      "sit there, and clicking one would buy a different listing")

low = [W("Purchase", 1100, 450), W("Buy", 1150, 801)]
check(with_words(low, m.purchase_confirm, Frame()) is not None,
      "and one just below the band is accepted")

d = with_words([W("Purchase", 1100, 450), W("Buy", 1150, 880)],
               m.purchase_confirm, Frame())
check(d is not None and d["cancel"] is None,
      "a dialog without a readable Cancel is still a dialog, with cancel=None")

for text, want in [("11,611,236", 11_611_236), ("190769", 190_769),
                   ("1,144,614", 1_144_614), ("999999", 999_999),
                   ("12345", None),
                   ("Alz", None), ("", None)]:
    words = [W("Purchase", 1100, 450), W("Buy", 1150, 880)]
    if text:
        words.insert(1, W(text, 1300, 700))
    d = with_words(words, m.purchase_confirm, Frame())
    got = d["price"] if d else "no dialog"
    check(got == want, f"{text!r} -> price {want}, got {got}")

two = [W("Purchase", 1100, 450), W("11,611,236", 1300, 650),
       W("22,222,222", 1300, 700), W("Buy", 1150, 880)]
d = with_words(two, m.purchase_confirm, Frame())
check(d and d["price"] == 22_222_222,
      f"with two long numbers the last one wins, got {d and d['price']}")

row = m.Offer(1, "Force Core Set (High) X 62", 11_611_236, 62, 340)
d = with_words(GOOD, m.purchase_confirm, Frame())
check(d["price"] == row.price,
      "the dialog price matches the row that was chosen, so no refusal")

wanted = m._floor_key(m.item_name(m._PACK_ANYWHERE.sub(" ", row.name)))
shown = m._floor_key(m.item_name(m._PACK_ANYWHERE.sub(" ", d["text"])))
check(wanted and wanted in shown,
      f"and it names the item, with the pack stripped from both sides "
      f"({wanted!r} in {shown!r})")

check("x62" not in wanted and "x62" not in shown,
      "the pack marker is gone from both, so its position cannot matter")

other = [w for w in GOOD if w.text != "(High)"] + [W("(Highest)", 1290, 560)]
d2 = with_words(other, m.purchase_confirm, Frame())
shown2 = m._floor_key(m.item_name(m._PACK_ANYWHERE.sub(" ", d2["text"])))
check(wanted not in shown2,
      "a dialog naming (Highest) does not satisfy a (High) row -- containment "
      "runs the safe way here, since 'high' is a prefix of 'highest' and the "
      "test is wanted-in-shown")


section("hover_tooltip: retries, and the warm-colour second pass")

class _Warm:

    width = 500
    height = 400

    def __eq__(self, other):
        return other == "WARM"


class Hover:

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

h = Hover([["Force Core(High)", "Price"], ["Force Core(High)", "Price"], PRICED])
tip = h.run(need_price=True)
check(tip["held"] == 55,
      f"a half-drawn tooltip is retried until it completes, got {tip['held']}")
check(h.attempts == 3, f"three attempts, got {h.attempts}")

waits = [w for w in h.waits if w in m.CONVERT_TIP_SETTLES]
check(waits == sorted(waits) and len(set(waits)) > 1,
      f"and each retry waits LONGER than the last, got {waits} -- four tries "
      "at one delay reproduced the identical half-drawn frame four times")

h = Hover([["Force Core(High)", "Price"]])
tip = h.run(need_price=True)
check(tip["held"] is None, "an never-completing tooltip returns held=None")
check(h.attempts == len(m.CONVERT_TIP_SETTLES),
      f"after exactly {len(m.CONVERT_TIP_SETTLES)} attempts, got {h.attempts}")

h = Hover(plain=[["Force Core(High)", "Price", "@ Target"]],
          warm=[["Force Core(High)", "Price", "[ Force Core Set (High) 0/1"]])
tip = h.run(need_price=True)
check(tip["held"] == 0,
      f"the warm pass recovers a red price line, got {tip['held']}")
check(tip["cost"] == 1, "with its cost")
check(any("0/1" in line for line in tip["lines"]),
      "and its text is merged into the lines")

check(tip["held"] == 0 and tip["held"] is not None,
      "0 is distinguishable from unreadable, which is the whole point -- "
      "losing it turned a completed conversion into 'could not read'")

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


section("the two tooltip readers point at different places")

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


section("convert_cell_matches: the tooltip-side identity check")

def tip_for(row, col, held=55, cost=1, gives=None, costs=None):
    g, c = m.CONVERT_TO_CORE[(row, col)]
    g, c = gives or g, costs or c
    return {"lines": [g, "Price", f"{c} {held} / {cost}"],
            "price_line": f"{c} {held} / {cost}", "held": held, "cost": cost}


for (row, col) in sorted(m.CONVERT_TO_CORE):
    t = tip_for(row, col)
    for r2 in range(1, 5):
        for c2 in range(1, 6):
            want = (r2, c2) == (row, col)
            got = m.convert_cell_matches(r2, c2, t)
            check(got == want,
                  f"tooltip for r{row}c{col} vs cell r{r2}c{c2}: "
                  f"expected {want}, got {got}")

for col in range(1, 6):
    check(not m.convert_cell_matches(1, col, tip_for(2, col)),
          f"a Force tooltip does not match the reverse cell r1c{col}")
    check(not m.convert_cell_matches(3, col, tip_for(4, col)),
          f"an Upgrade tooltip does not match the reverse cell r3c{col}")

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


section("_convert_name_key: what it strips, and what it must not")

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

check(m._convert_name_key("[ Force Core Set (High)")
      != m._floor_key("[ Force Core Set (High)"),
      "leading noise is stripped BEFORE folding, or '[' would become 'i'")
check(not m._convert_name_key("[ Force Core Set (High)").startswith("i"),
      f"so the key does not start with a spurious 'i', got "
      f"{m._convert_name_key('[ Force Core Set (High)')!r}")

for noise in ["", "[ ", "* ", "| ", "  "]:
    for tail in ["", " 55 / 1", " 0/1", " 1,250 / 1"]:
        hi = m._convert_name_key(f"{noise}Force Core Set (High){tail}")
        hst = m._convert_name_key(f"{noise}Force Core Set (Highest){tail}")
        check(hi != hst,
              f"High and Highest stay distinct through {noise!r}+{tail!r}")

check(m._convert_name_key("Force Core Set (High) X 62")
      != m._convert_name_key("Force Core Set (High)"),
      "a pack marker is not a held/cost pair and is not stripped here -- "
      "core_row_counts strips that separately, on purpose")


section("the profit ledger: money out, not just money in")

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
    sales_n, gross, buys_n, spend, fees = totals
    check(sales_n == 1 and gross == 41_960_000,
          f"one sale of 41,960,000, got {sales_n}/{gross}")
    check(buys_n == 1 and spend == 11_611_236,
          f"one purchase of 11,611,236, got {buys_n}/{spend}")
    check(gross - spend == 30_348_764,
          f"the difference is 30,348,764, got {gross - spend}")
    check(isinstance(fees, int),
          f"and fees come back as their own figure, got {fees!r}")

m.SALES.clear()
m.PURCHASES.clear()

_saved_totals = m.all_time_totals
try:
    m.all_time_totals = lambda: None
    check(m.profit_report() == "",
          "with an empty ledger AND a quiet run, there is nothing to say")

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

m.SALES.clear()
m.PURCHASES.clear()
m.PURCHASES.append({"item": "Y", "price": 1, "spend": 9_000_000, "qty": 1})
out = m.profit_report()
check("-9,000,000" in out,
      f"a stocking-up run reports a negative net, got {out!r}")

m.SALES.clear()
m.PURCHASES.clear()
m.SALES.append({"item": "X", "price": 1, "proceeds": None, "qty": None})
out = m.profit_report()
check("1 collection" in out,
      f"a collection with unreadable proceeds is still counted, got {out!r}")

m.SALES.clear()
m.PURCHASES.clear()
m.SALES.append({"item": "X", "price": 1, "proceeds": 1_000, "qty": 1})
out = m.profit_report()
check("THIS RUN" in out and "STANDING POSITION" in out,
      f"both windows are shown, got {out!r}")
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


section("a Buy that names the wrong item stops buying for the run")



class _Buy:

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
    m.note_favourite_search(8, [offer])

_was_halt = (m.BUY_HALTED, m.BUY_HALT_REASON)
try:
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


section("the only sanctioned Buy: favourite slot -> row 1 -> Buy")

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

    m.note_favourite_search(8, [R1])
    m._LAST_SEARCH["at"] -= m.SEARCH_RECEIPT_SECONDS + 1
    check(m.search_receipt_for(R1),
          "a receipt older than its shelf life is refused")
    m.note_favourite_search(8, [R1])
    m._LAST_SEARCH["at"] -= m.SEARCH_RECEIPT_SECONDS - 5
    check(m.search_receipt_for(R1) == "", "one just inside it is still good")

    m.note_favourite_search(4, [OTHER])
    check(m.search_receipt_for(R1),
          "row 1 of a search for a DIFFERENT item is refused")
    check("Upgrade Core Set (Highest)" in m.search_receipt_for(R1),
          f"and the reason names what was actually found, got "
          f"{m.search_receipt_for(R1)!r}")

    m.note_favourite_search(8, [m.Offer(1, "Force Core Set (High)", 1, 1, 340)])
    check(m.search_receipt_for(R1) == "",
          "the same item with and without its pack marker still matches")

    m._LAST_SEARCH = None
    m.note_favourite_search(8, [R1])
    check(m._LAST_SEARCH is not None and m._LAST_SEARCH["slot"] == 8,
          "a search records which slot it was")
    check(m._LAST_SEARCH["first"] == R1.name,
          f"and what its row 1 held, got {m._LAST_SEARCH['first']!r}")

    m.note_favourite_search(8, [])
    check(m.search_receipt_for(R1),
          "a search that found nothing cannot authorise a buy")
finally:
    m._LAST_SEARCH = _saved_search

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


section("how big an order may be: the real buy_cheapest_set_detail")



class _Market:

    def __init__(self, pack):
        self.pack = pack
        self.bought = []

    def search(self, slot, settle=3.0, tries=2, verbose=True):
        if slot % 2 == 0:
            return [m.Offer(1, f"Force Core Set (High) X {self.pack}",
                            self.pack * 187_000, self.pack, 340)]
        return [m.Offer(1, "Force Core(High)", 209_800, 1, 340)]

    def buy(self, offer, want=1, timeout=8.0, report=None, verbose=True):
        take = max(1, min(int(want), max(1, getattr(offer, "available", 1))))
        self.bought.append(offer.pack)
        if report is not None:
            report["take"] = take
            report["items"] = take * max(1, offer.pack)
        return True, ""


def try_order(pack, still_wanted):
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


MINIMUM, MAXIMUM = m.RESTOCK_TARGET, m.BUY_MAXIMUM


def try_held(pack, held):
    return try_order(pack, still_wanted=MAXIMUM - held)


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

bought, why, orders = try_held(999, held=213)
check(bought is False,
      f"213 held with a 999 bundle is REFUSED, got {bought} {why!r}")
check(orders == [], f"and nothing is spent, got {orders}")

bought, _why, orders = try_order(999, still_wanted=None)
check(bought is True, "a caller with no target in mind is not blocked")


section("the shop sweep leaves the table at the top")

_moves = []
_saved_sweep = (m._enumerate_at_step, m.scroll_to_end, m.await_rows)
try:
    m.await_rows = lambda timeout=8.0, poll=0.5: []
    m.scroll_to_end = (lambda up, timeout=8.0, verbose=True:
                       _moves.append("up" if up else "down") or [])

    m._enumerate_at_step = lambda step, timeout, verbose, say, **_: [(1, Row("x"))]
    _moves.clear()
    out = m.enumerate_listings(verbose=False)
    check(out is not None, "a successful sweep returns its rows")
    check(_moves and _moves[-1] == "up",
          f"and ends with the table scrolled to the TOP, got {_moves}")

    m._enumerate_at_step = lambda step, timeout, verbose, say, **_: None
    _moves.clear()
    out = m.enumerate_listings(verbose=False)
    check(out is None, "a failed sweep reports failure")
    check(_moves and _moves[-1] == "up",
          f"and STILL returns the table to the top, got {_moves}")

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


section("never relist below what the Sets cost")

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

m.record_purchase_row(f"{SET_ULT} X 10", 4_000_000, 4_000_000, 10)
m.record_purchase_row(f"{SET_ULT} X 90", 45_000_000, 45_000_000, 90)
basis = m.purchase_cost_basis(CORE_ULT)
check(basis == -(-49_000_000 // 100),
      f"100 Sets for 49,000,000 is a basis of 490,000, got {basis:,}")
check(basis != (400_000 + 500_000) // 2,
      "not the mean of the two rates -- the big lot has to dominate")

check(basis > 0, "the 'X 10' suffix does not stop the lookup")

m.record_purchase_row("Force Core Set (High) X 50", 9_000_000, 9_000_000, 50)
check(m.purchase_cost_basis(CORE_ULT) == basis,
      "a different Set's purchases do not move this basis")
check(m.purchase_cost_basis("Force Core(High)") == 180_000,
      f"and that Set gives its own basis, got "
      f"{m.purchase_cost_basis('Force Core(High)'):,}")

COST = m.purchase_cost_basis(CORE_ULT)
for listed, market, want in [
        (600_000, 100_000, COST),
        (600_000, COST - 1, COST),
        (600_000, COST, COST),
        (600_000, 610_000, 610_000),
]:
    price, why = m.choose_price(market, floor_price=listed, absolute_floor=COST)
    check(price >= COST,
          f"listed {listed:,}, market {market:,} -> {price:,}, never under "
          f"the {COST:,} paid")
    check(price == want or price >= COST,
          f"listed {listed:,}, market {market:,} -> {price:,} (wanted {want:,})")

price = 600_000
for _ in range(30):
    price, _why = m.choose_price(100_000, floor_price=price, absolute_floor=COST)
check(price == COST,
      f"thirty relists against a collapsed market settle at cost {COST:,}, "
      f"got {price:,} -- the 5% ratchet limits the SPEED of a fall, this "
      "limits the depth")

check(m.choose_price(1, floor_price=600_000,
                     absolute_floor=max(COST, 900_000))[0] == 900_000,
      "a catalogue floor above cost is what binds")
_ratchet_below_cost = m.choose_price(1, floor_price=COST, absolute_floor=COST)
check(_ratchet_below_cost[0] == COST,
      f"listed at cost, a collapsed market still lists at cost {COST:,}, got "
      f"{_ratchet_below_cost[0]:,} -- 5% down would be under what was paid")
check("floor" in _ratchet_below_cost[1],
      f"and the reason names the floor, got {_ratchet_below_cost[1]!r}")

_ratchet_above_cost = m.choose_price(1, floor_price=600_000,
                                     absolute_floor=COST)
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
check(m.COST_FLOOR_ON_RELIST is True,
      "the cost floor ships ON: a Core row must not relist below what its "
      "stock cost. --no-cost-floor and the config knob both still turn it "
      "off for a position that has to move.")

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


section("the per-item saving thresholds resolve to the right ITEM")
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

for _item, _want in (("Force Core(Highest)", 5_000),
                     ("Force Core(High)", 5_000),
                     ("Force Core (Ultimate)", 10_000),
                     ("Upgrade Core (Ultimate)", 10_000)):
    check(m.price_diff_floor_for(_item) == _want,
          f"{_item} requires a {_want:,} saving, got "
          f"{m.price_diff_floor_for(_item):,}")

for _variant in ("Force Core (Highest)", "Force Core(Highest) X 250",
                 "force core(highest)"):
    check(m.price_diff_floor_for(_variant) == 5_000,
          f"{_variant!r} still resolves to 5,000, got "
          f"{m.price_diff_floor_for(_variant):,}")

try:
    m.validate_price_diff_floors()
    check(True, "every PRICE_DIFF_FLOOR_BY_ITEM key matches a managed Core")
except Exception as _exc:
    check(False, f"validate_price_diff_floors() raised: {_exc}")


print(f"\n{'=' * 60}")
print(f"buying/convert gaps: {count} checks, {len(fails)} failed")
if fails:
    for f in fails[:25]:
        print(f"  FAIL  {f}")
    sys.exit(1)

import inspect as _i

_buy = _i.getsource(m.buy_offer)
check(m.QTY_READBACK_TRIES >= 2 and m.QTY_READBACK_PAUSE > 0,
      f"the retry must actually cover a blink cycle, got "
      f"{m.QTY_READBACK_TRIES} x {m.QTY_READBACK_PAUSE}s")


_src = _i.getsource(m.buy_offer)

_typed = _src.index("type_number(asked")
_reread = _src.index("dialog = purchase_confirm()", _typed)
check("park_cursor()" in _src[_typed:_reread],
      "the pointer must be parked between typing the quantity and reading the "
      "dialog again -- it sits on the digits otherwise")

check('landed = dialog.get("qty")' not in _src,
      "the quantity must not be read back from the field the caret sits in")
check("typed {take} into the quantity field" not in _src,
      "and the refusal that came from it must be gone with it")

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
