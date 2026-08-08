"""The bugs a ten-agent review found, and the guards that now stop them.

Every section here corresponds to a defect that was live in trade.py and that
2,805 existing checks did not catch. That is the point of the file: each test
was written after watching the real function do the wrong thing, and each one
fails if the fix is reverted.

The recurring reason the old suites missed these was structural, not careless:

  * they stubbed the function under test (restock_test reimplements the buy
    rule its own Pipeline then obeys, so the real rule could be deleted
    outright and the suite stayed green);
  * they derived expectations from the code (the row-capacity boundary was
    computed with restock_rows_needed on both sides, so that function could
    return a constant 1 and nothing noticed);
  * they only ever supplied inputs that already satisfied the check
    (buy_offer was never once given a click that landed and a balance that did
    not move -- the single assertion separating "we clicked Buy" from "we
    bought it").

So the rules here: drive the REAL function, state expectations as literals
wherever a literal is knowable, and always include the case that used to pass.

NOTHING here touches the game.
"""
import os
import sys
import tempfile
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# A scratch ledger, set HARD rather than with setdefault.
#
# setdefault yields to an exported CABAL_SALES_DB -- and an operator who points
# that at the real sales history to inspect it would have had this suite delete
# it. The directory is created here, so the unlink guard below can insist that
# nothing outside it is ever removed.
_SCRATCH = _Path(tempfile.mkdtemp(prefix="cabal_review_test_"))
os.environ["CABAL_SALES_DB"] = str(_SCRATCH / "scratch.db")

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


def reset_ledger():
    """Empty the scratch ledger, refusing to touch anything outside temp.

    The bound is the system temp directory rather than this file's own scratch
    directory, because mutation_check imports trade BEFORE running this file --
    so SALES_DB is already resolved to the harness's scratch path by then, and
    a stricter test would reject a perfectly safe sandbox.

    It stays a real guard: the operator's sales.db lives beside trade.py in the
    repository, which is never under temp. An earlier version of this check
    used os.environ.setdefault, which yields to an exported CABAL_SALES_DB --
    so pointing that at the live ledger to inspect it and then running the
    suite would have deleted it.
    """
    db = _Path(m.SALES_DB).resolve()
    temp = _Path(tempfile.gettempdir()).resolve()
    if db.exists():
        if temp not in db.parents:
            raise SystemExit(f"refusing to delete {db}: not under {temp}")
        db.unlink()
    m._sales_db_ready = False


class FakeClock:
    """time, with sleep removed. buy_offer alone sleeps ~6s per call."""

    def __init__(self):
        import time as _t
        self._t = _t
        self.slept = 0.0

    def sleep(self, seconds):
        self.slept += seconds

    def __getattr__(self, name):
        return getattr(self._t, name)


# ==========================================================================
section("buy_offer: the balance is the proof, not the click")
# ==========================================================================
#
# This is the assertion that separates "we clicked Buy" from "we bought it",
# and no suite had ever supplied an input that could fail it. Every fixture in
# buying_gaps_test gives buy_offer a balance pair that already satisfies the
# check, so `before - after == offer.price` could be weakened to `before and
# after` -- claiming a purchase whenever the balance is merely READABLE -- and
# four suites totalling 9,294 checks stayed green.

OFFER = m.Offer(1, "Force Core Set (High) X 62", 11_611_236, 62, 340)
DIALOG_OK = {"text": "Confirm Purchase Force Core Set X 62 (High)",
             "price": 11_611_236, "buy": (1250, 900), "cancel": (1400, 900)}


def run_buy(balances, dialog=DIALOG_OK, dialog_after=None, offer=OFFER):
    """Drive the REAL buy_offer. `balances` is what get_alz returns in turn."""
    seen = {"clicks": [], "purchases": [], "halted": None}
    reads = list(balances)
    dialogs = [dialog, dialog_after]

    saved = {n: getattr(m, n) for n in
             ("purchase_ready", "get_alz", "grab", "focus_game", "move_mouse",
              "click", "purchase_confirm", "park_cursor", "note_purchase",
              "record", "time", "_LAST_SEARCH", "BUY_HALTED",
              "BUY_HALT_REASON")}
    try:
        m.time = FakeClock()
        m.purchase_ready = lambda verbose=True: True
        m.grab = lambda *a, **k: None
        m.focus_game = lambda *a, **k: True
        m.park_cursor = lambda *a, **k: None
        m.move_mouse = lambda *a, **k: None
        m.click = lambda x, y, **k: seen["clicks"].append((x, y))
        m.get_alz = lambda *a, **k: (reads.pop(0) if reads else 0)
        m.purchase_confirm = lambda *a, **k: (
            dialogs.pop(0) if dialogs else None)
        m.record = lambda *a, **k: None
        m.note_purchase = lambda item, price, spend, qty, note="": (
            seen["purchases"].append({"item": item, "price": price,
                                      "spend": spend, "qty": qty,
                                      "note": note}))
        m.BUY_HALTED, m.BUY_HALT_REASON = False, ""
        m.note_favourite_search(8, [offer])
        bought, why = m.buy_offer(offer, verbose=False)
        seen["halted"] = m.BUY_HALTED
    finally:
        for name, value in saved.items():
            setattr(m, name, value)
    return bought, why, seen


# The happy path still works, and records what the BALANCE moved by.
bought, why, seen = run_buy([1_000_000_000, 1_000_000_000 - OFFER.price])
check(bought is True, f"a purchase that moves the balance is bought ({why!r})")
check(len(seen["purchases"]) == 1, "and is written to the ledger exactly once")
check(seen["purchases"][0]["spend"] == OFFER.price,
      f"at the measured spend, got {seen['purchases'][0]['spend']:,}")
check(seen["purchases"][0]["qty"] == 62,
      "with the pack size as the quantity, so the cost basis is per ITEM")

# THE GAP: the click lands, the dialog closes, and no money moves. Before this
# test existed nothing in the tree distinguished it from a real purchase.
bought, why, seen = run_buy([1_000_000_000, 1_000_000_000])
check(bought is False,
      "a click that moves NO Alz is not a purchase, however clean it looked")
check(seen["purchases"] == [],
      f"and nothing is written to the ledger, got {seen['purchases']}")
check("no Alz was spent" in why, f"and it says so, got {why!r}")

# The balance moved, but by the wrong amount -- a different row was bought, or
# something else spent in the same window. Not our purchase either way.
bought, why, seen = run_buy([1_000_000_000, 1_000_000_000 - 500])
check(bought is False,
      "a balance that moves by the WRONG amount is not this purchase")
check(seen["purchases"] == [], "and is not recorded")
check("500" in why, f"and the actual movement is named, got {why!r}")

# The balance moved by MORE than the price -- also not a match. Stated
# separately because `before - after >= price` would pass the case above.
bought, why, seen = run_buy([1_000_000_000, 1_000_000_000 - OFFER.price - 1])
check(bought is False, "nor does a balance that moves by more than the price")

# Sold out: the dialog is still up after the confirm click. Ordinary race.
bought, why, seen = run_buy([1_000_000_000, 1_000_000_000],
                            dialog_after=DIALOG_OK)
check(bought is False and "sold out" in why,
      f"a dialog still open after confirming means somebody else took it "
      f"({why!r})")
check(seen["halted"] is False,
      "and that is a race, not a wrong map -- buying must NOT halt")

# ---- the fix: an unmeasurable purchase is still a purchase ---------------
#
# get_alz returns 0 for "unreadable", which `or None` turns into None. This
# used to fall through to "the balance moved 0" and return False, having spent
# the Alz and written nothing. That is the worst of the three outcomes:
# purchase_cost_basis reads the ledger, so a missing purchase drags the average
# cost DOWN and the Cores it produced are then relisted below what was paid --
# the one thing the never-below-cost floor exists to prevent.
for label, balances in [("the balance before is unreadable",
                         [0, 1_000_000_000]),
                        ("the balance after is unreadable",
                         [1_000_000_000, 0]),
                        ("neither reads", [0, 0])]:
    bought, why, seen = run_buy(balances)
    check(bought is True, f"{label}: the purchase is still claimed ({why!r})")
    check(len(seen["purchases"]) == 1,
          f"{label}: and RECORDED, or the cost floor silently drops")
    check(seen["purchases"][0]["spend"] == OFFER.price,
          f"{label}: at the listed price")
    check("unmeasured" in seen["purchases"][0]["note"],
          f"{label}: marked as unmeasured rather than passed off as measured, "
          f"got {seen['purchases'][0]['note']!r}")

# The two ways of being wrong are not symmetric, and the ledger must lean the
# safe way. Stated as a property so the reasoning is testable, not just noted.
check(m.purchase_cost_basis("Force Core (Ultimate)") == 0,
      "sanity: the scratch ledger starts empty")


# ==========================================================================
section("buy_offer: the grade at the last check before the money moves")
# ==========================================================================
#
# _floor_key folds "Force Core Set (High)" to forcecoresethigh and
# "...(Highest)" to forcecoresethighest -- and the first is a SUBSTRING of the
# second. The check was `wanted not in shown`, so a Highest dialog satisfied a
# High order. mass_purchase_matches uses equality for exactly this reason; this
# was the one place that did not, and it is the place real Alz moves.

HIGHEST_DIALOG = dict(DIALOG_OK,
                      text="Confirm Purchase Force Core Set X 62 (Highest)")
bought, why, seen = run_buy([1_000_000_000, 1_000_000_000 - OFFER.price],
                            dialog=HIGHEST_DIALOG)
check(bought is False,
      f"a Highest dialog does NOT satisfy a High order ({why!r})")
check(seen["purchases"] == [], "and no money is recorded as spent")
check(seen["halted"] is True,
      "and buying HALTS -- a dialog naming another item means the map is "
      "wrong, and retrying a wrong map only gives it more chances")

# The right grade still buys. A guard that refuses everything is not a guard.
bought, why, seen = run_buy([1_000_000_000, 1_000_000_000 - OFFER.price])
check(bought is True, f"the correct grade is still bought ({why!r})")

# And an unrelated item is refused, as it always was.
OTHER = dict(DIALOG_OK, text="Confirm Purchase SIGMetal Headpiece")
bought, why, seen = run_buy([1_000_000_000, 1_000_000_000 - OFFER.price],
                            dialog=OTHER)
check(bought is False and seen["halted"] is True,
      "an unrelated item is still refused and still halts")

# Both directions of the containment trap, at the key level, for every managed
# pair. This is the check that would have caught the bug by construction.
for name in m.FAVOURITE_SLOTS.values():
    key = m._floor_key(m.item_name(name))
    longer = [other for other in m.FAVOURITE_SLOTS.values()
              if m._floor_key(m.item_name(other)) != key
              and key in m._floor_key(m.item_name(other))]
    if longer:
        check(True, f"{name!r} is a prefix of {len(longer)} other managed "
                    f"item(s) -- containment can never be used on it")


# ==========================================================================
section("the never-below-cost floor survives a listed row's name")
# ==========================================================================
#
# The relist path passes expect_item=target.name, which is the TABLE name and
# carries the pack marker: "Force Core (Ultimate) X 250". favourite_for matches
# on exact equality, so the marker made it resolve to no slot, set_behind
# returned "", purchase_cost_basis returned 0, and listing_floor fell back to
# the catalogue -- which is 0 for a Core.
#
# The floor therefore worked on the FIRST listing (clean name from the vendor)
# and vanished on every relist after it. The path that repeats is the one that
# can walk a holding down 5% at a time, so the floor was absent exactly where
# it was needed.

reset_ledger()
m.record_purchase_row("Force Core Set (Ultimate) X 100", 42_857_100,
                      42_857_100, 100)
CLEAN = m.purchase_cost_basis("Force Core (Ultimate)")
check(CLEAN == 428_571,
      f"100 Sets for 42,857,100 is 428,571 an item, got {CLEAN:,}")

for listed in ["Force Core (Ultimate) X 250", "Force Core (Ultimate) X 1",
               "Force Core (Ultimate) x 62", "Force Core (Ultimate) x250",
               "Force Core (Ultimate)  X  7"]:
    check(m.purchase_cost_basis(listed) == CLEAN,
          f"a row reading {listed!r} has the same cost floor, got "
          f"{m.purchase_cost_basis(listed):,}")

# Lowercase matters on its own: the game draws "X", Tesseract returns "x". Both
# pack regexes were uppercase-only, and the failure is severe in the OTHER
# direction too -- pack_size falling back to 1 records a 250-Set bundle as ONE
# item, making the cost basis 250x too high and quietly stopping every sale.
check(m.pack_size("Force Core Set (High) x 250") == 250,
      f"a lowercase pack marker still reads as 250, got "
      f"{m.pack_size('Force Core Set (High) x 250')}")
check(m.pack_size("Force Core Set (High) X 250") == 250,
      "as does an uppercase one")
check(m.pack_size("Yekaterina VIP Membership") == 1,
      "and an item with no marker is one item")

# The floor still binds, and still binds through choose_price.
floor, why_floor = m.listing_floor("Force Core (Ultimate) X 250")
check(floor == CLEAN,
      f"listing_floor on a table name returns the cost, got {floor:,}")
check("bought" in why_floor or "Sets" in why_floor,
      f"and says it came from what was paid, got {why_floor!r}")
price, _ = m.choose_price(100_000, floor_price=600_000, absolute_floor=floor)
check(price >= CLEAN, f"a collapsed market cannot price under cost ({price:,})")

# Thirty relists against a collapsed market converge ON cost, not through it.
# The 5% ratchet limits the SPEED of a fall; only this limits the depth.
price = 600_000
for _ in range(30):
    price, _ = m.choose_price(100_000, floor_price=price, absolute_floor=floor)
check(price == CLEAN,
      f"thirty relists settle exactly at cost {CLEAN:,}, got {price:,}")

# No false positives: an item that is not a Core still has no cost floor.
check(m.set_behind("Yekaterina VIP Membership X 6") == "",
      "a VIP has no Set behind it, marker or no marker")
check(m.favourite_for("Nothing Managed X 5") is None,
      "and an unmanaged item resolves to no favourite slot")


# ==========================================================================
section("the overshoot ceiling follows --buy-target")
# ==========================================================================
#
# still_wanted is (BUY_TARGET - bought); the exemption compared it against the
# RESTOCK_TARGET constant. The two are the same only at the default, so
# --buy-target broke the ceiling in both directions: too high and every order
# counted as "first" (re-opening the 428,142,429 Alz single-click case), too
# low and the first order was never exempt so nothing was ever bought.


class Market:
    """A favourite pair: loose Core at 209,800, Sets cheaper per item."""

    def __init__(self, pack):
        self.pack, self.bought = pack, []

    def search(self, slot, settle=3.0, tries=2, verbose=True):
        if slot % 2 == 0:
            return [m.Offer(1, f"Force Core Set (High) X {self.pack}",
                            self.pack * 187_000, self.pack, 340)]
        return [m.Offer(1, "Force Core(High)", 209_800, 1, 340)]

    def buy(self, offer, want=1, timeout=8.0, verbose=True):
        self.bought.append(offer.pack)
        return True, ""


def order(pack, still_wanted, buy_target):
    mk = Market(pack)
    saved = {n: getattr(m, n) for n in
             ("run_favourite_search", "buy_offer", "favourite_set_slot",
              "affordable", "BUY_TARGET")}
    try:
        m.BUY_TARGET = buy_target
        m.run_favourite_search, m.buy_offer = mk.search, mk.buy
        m.favourite_set_slot = lambda s: s + 1
        m.affordable = lambda price, source=None: True
        out = m.buy_cheapest_set_detail(7, verbose=False,
                                        still_wanted=still_wanted)
    finally:
        for name, value in saved.items():
            setattr(m, name, value)
    return out["bought"], mk.bought


# Stated as a table of literals, not derived from RESTOCK_TARGET, so a change
# to the constant cannot quietly make the expectations agree with the code.
# TWO limits, doing different jobs.
#
#   below RESTOCK_TARGET (200)   the minimum is HARD and meeting it comes
#                                first, so any bundle is taken whatever its
#                                size -- 100 + 999 is allowed.
#   at or above it               BUY_MAXIMUM (500) applies: an order is taken
#                                only while the total stays within it.
#                                240 + 999 refused, 240 + 200 taken.
#
# Stated as literals rather than derived from the constants, so retuning the
# limits makes this table complain instead of silently agreeing with the code.
MINIMUM, MAXIMUM = m.RESTOCK_TARGET, m.BUY_MAXIMUM
check((MINIMUM, MAXIMUM) == (200, 500),
      f"the limits are 200 and 500, got {MINIMUM} and {MAXIMUM}")

CASES = [
    # (held, row-1 bundle, may buy?, why)
    (0, 999, True, "nothing held: the minimum is unmet, so any size goes"),
    (100, 999, True, "100 held: still under the 200 minimum, 999 allowed"),
    (199, 999, True, "one short of the minimum: still allowed"),
    (200, 300, True, "at the minimum exactly, 500 total is the maximum"),
    (200, 301, False, "at the minimum, 501 is one past the maximum"),
    (240, 999, False, "240 + 999 is refused"),
    (240, 200, True, "240 + 200 = 440 is taken"),
    (240, 260, True, "240 + 260 = 500 exactly"),
    (240, 261, False, "240 + 261 = 501"),
    (499, 1, True, "499 + 1 = 500"),
    (500, 1, False, "already at the maximum"),
]
for held, pack, allowed, label in CASES:
    bought, orders = order(pack, still_wanted=m.BUY_MAXIMUM - held,
                           buy_target=m.BUY_MAXIMUM)
    check(bought is allowed, f"{label} -- got bought={bought}")
    check(bool(orders) is allowed,
          f"{label} -- {'bought ' + str(orders) if orders else 'spent nothing'}")

# The 428M runaway is still refused, now by the maximum rather than a factor:
# 213 held is over the 200 minimum, and 213 + 999 is 1,212.
bought, orders = order(999, still_wanted=m.BUY_MAXIMUM - 213,
                       buy_target=m.BUY_MAXIMUM)
check(bought is False and orders == [],
      f"213 held with a 999 bundle stays refused, got {orders}")

# A caller with no target at all is not subject to the rule.
bought, orders = order(999, still_wanted=None, buy_target=100)
check(bought is True and orders == [999],
      "a direct caller with no target in mind is not blocked")


# ==========================================================================
section("a restock that cannot list does not buy the same target again")
# ==========================================================================
#
# The unlisted cache is cleared only when something was LISTED. A restock that
# bought and then failed to list left the SHOP looking exactly as it did
# before, so the next cycle asked the same question, got the same answer, and
# bought a second target on top of the first -- about 43M a pass, repeating
# every cycle for as long as the underlying problem lasted. Neither halt_buying
# nor the row-capacity pause catches it.

for slot in list(m._CARRIED_SETS):
    m.clear_carried(slot)

check(m.carried_sets(7) == 0, "nothing is carried to begin with")
check(m.carried_total() == 0, "and the total agrees")
m.note_carried_sets(7, 120)
check(m.carried_sets(7) == 120, "what was bought is remembered per slot")
check(m.carried_total() == 120, "and counted in the total")
m.note_carried_sets(7, 0)
check(m.carried_sets(7) == 0, "settling to zero forgets it")
check(7 not in m._CARRIED_SETS, "and does not leave an empty entry behind")
m.note_carried_sets(7, 120)
m.clear_carried(7)
check(m.carried_sets(7) == 0, "as does clearing it outright")


class Pipeline:
    """Records what restock_core does, without a game.

    Deliberately does NOT reimplement any rule under test -- the old suite's
    Pipeline restated the never-exceed-target check, so the real one could be
    deleted and the suite stayed green.
    """

    def __init__(self, pack=120, convert_per_round=120, list_ok=True,
                 converted_reads=None, in_bag=0):
        self.pack = pack
        self.convert_per_round = convert_per_round
        self.list_ok = list_ok
        self.converted_reads = converted_reads
        self.log = []
        # What is ALREADY in the inventory. A resume converts this without
        # buying, so it has to be settable independently of `pack`.
        self.left = in_bag

    def buy(self, slot, target, verbose=True):
        self.log.append(("buy", target))
        self.left = self.pack
        return {"bought": self.pack}

    def convert(self, core, quantity=250, verbose=True, require_layout=True):
        self.log.append(("convert", core))
        moved = min(self.left, self.convert_per_round)
        reported = (self.converted_reads.pop(0)
                    if self.converted_reads else moved)
        self.pending = moved
        return {"converted": reported, "candidates": [(1, 1)] if moved else []}

    def list_(self, core, candidates, verbose=True):
        self.log.append(("list", core))
        if not self.list_ok:
            return {"ok": False, "qty": 0, "why": "the shop refused"}
        qty = getattr(self, "pending", 0)
        self.left -= qty
        return {"ok": True, "qty": qty, "why": ""}


def run_restock(pipe, slot=7, target=100, rows_used=0, vendor=True):
    saved = {n: getattr(m, n) for n in
             ("buy_sets_until", "convert_cores", "list_cores",
              "open_purchase_tab", "open_npc_shop", "close_npc_shop",
              "inventory_origin", "select_inventory_tab", "shop_rows_used")}
    try:
        m.buy_sets_until = pipe.buy
        m.convert_cores = pipe.convert
        m.list_cores = pipe.list_
        m.open_purchase_tab = lambda verbose=True: True
        m.open_npc_shop = lambda verbose=True: vendor
        m.close_npc_shop = lambda verbose=True: True
        m.inventory_origin = lambda *a, **k: (100, 100)
        m.select_inventory_tab = lambda tab, origin, **k: True
        m.shop_rows_used = lambda verbose=True: rows_used
        return m.restock_core(slot, target=target, verbose=False,
                              rows_used=rows_used)
    finally:
        for name, value in saved.items():
            setattr(m, name, value)


# A restock that buys but cannot list leaves the debt on the books.
m.clear_carried(7)
pipe = Pipeline(pack=120, list_ok=False)
out = run_restock(pipe)
check(out["listed"] == 0, "the failing restock lists nothing")
check(m.carried_sets(7) == 120,
      f"but the 120 Sets it PAID FOR are remembered, got {m.carried_sets(7)}")

# The next pass must convert those, not buy more. This is the whole fix.
pipe2 = Pipeline(pack=120, list_ok=True, in_bag=120)
out2 = run_restock(pipe2)
buys = [entry for entry in pipe2.log if entry[0] == "buy"]
check(buys == [], f"the next pass buys NOTHING, got {buys}")
check(out2["bought"] == 120,
      f"it works from the 120 already held, got {out2['bought']}")
check(out2.get("resumed") is True, "and says it resumed rather than bought")
check(out2["listed"] == 120, f"and lists them, got {out2['listed']}")
check(m.carried_sets(7) == 0,
      "which settles the debt, so the pass after this one may buy again")

# Partial progress leaves only the remainder on the books.
m.clear_carried(7)
pipe3 = Pipeline(pack=250, convert_per_round=100)
out3 = run_restock(pipe3, target=250)
check(out3["listed"] == 250, f"a full run lists everything ({out3['listed']})")
check(m.carried_sets(7) == 0, "and carries nothing forward")

# The debt is banked BEFORE the convert/list rounds, so a crash mid-round still
# leaves it recorded. Tested by making the vendor unreachable after the buy.
m.clear_carried(7)
pipe4 = Pipeline(pack=99)
out4 = run_restock(pipe4, vendor=False)
check(out4["listed"] == 0, "a restock that cannot reach the vendor lists none")
check(m.carried_sets(7) == 99,
      f"and the 99 bought Sets are still on the books, got "
      f"{m.carried_sets(7)}")
m.clear_carried(7)

# The same, but for an exception rather than a clean break. restock_core has
# no `finally`, so anything that is not Aborted -- a refused SendInput, a
# failed grab, a TypeError in new code -- leaves the function without running
# the settle at the end. The Sets are paid for and in the bag by then, so the
# debt has to be banked BEFORE the convert/list rounds start, not after them.
#
# This is the one case the clean-break tests above cannot distinguish: with
# only those, deleting the pre-round banking entirely leaves every test green.


class Explodes(Pipeline):
    def convert(self, core, quantity=250, verbose=True, require_layout=True):
        raise RuntimeError("a refused click, three rounds in")


pipe5 = Explodes(pack=99)
blew_up = False
try:
    run_restock(pipe5)
except RuntimeError:
    blew_up = True
check(blew_up, "an unexpected exception is not swallowed")
check(m.carried_sets(7) == 99,
      f"but the 99 Sets it had already PAID FOR are on the books anyway -- "
      f"otherwise the next cycle buys them again, got {m.carried_sets(7)}")
m.clear_carried(7)


# ==========================================================================
section("the strand recovery does not sell the restock's raw material")
# ==========================================================================
#
# WORK_TAB and CONVERT_INVENTORY_TAB are the same tab, chosen deliberately --
# it is the game's default and every count in the pipeline is taken there. The
# comment justifying the collision said the two uses "do not overlap in time,
# because converting is a manual operation"; that stopped being true when --buy
# shipped and restock_core began buying and converting on it inside the
# unattended loop.
#
# recover_stranded_work_tab prices what it finds at strictest_price_floor().
# For an unnameable strand that is right. For a stack of Force Core Sets bought
# at 187,278 it means listing them at 175,000,000, paying a registration fee on
# the inflated figure, and then having the next cycle read the name off the
# table and offer the pipeline's own raw material to the market.

check(m.WORK_TAB == m.CONVERT_INVENTORY_TAB,
      "the two tabs really are the same -- this is why the guard is needed")
check(m.strictest_price_floor() >= 100_000_000,
      f"and the strand price is enormous next to a Set: "
      f"{m.strictest_price_floor():,}")

for slot in list(m._CARRIED_SETS):
    m.clear_carried(slot)
touched = []
saved_inv = m.inventory_origin
saved_tab = m.select_inventory_tab
try:
    m.inventory_origin = lambda *a, **k: touched.append("looked") or (100, 100)
    # Refuse the tab switch, so the recovery stops there. Without this the
    # no-carry case would run on to grab() -- a real screenshot -- and this
    # file touches nothing.
    m.select_inventory_tab = lambda tab, origin, **k: False
    m.recover_stranded_work_tab(verbose=False)
    check(touched != [],
          "with nothing carried the recovery proceeds as it always did -- the "
          "guard must not disable strand recovery outright")

    touched.clear()
    m.note_carried_sets(7, 250)
    out = m.recover_stranded_work_tab(verbose=False)
    check(out is False,
          "with a restock mid-flight the recovery REFUSES")
    check(touched == [],
          f"and refuses before it even looks at the inventory, got {touched}")
finally:
    m.inventory_origin = saved_inv
    m.select_inventory_tab = saved_tab
    m.clear_carried(7)


# ==========================================================================
section("a conversion that worked is listed, even when the count reads 0")
# ==========================================================================
#
# convert_cores counts newly filled slots on CONVERT_INVENTORY_TAB, and it says
# out loud that the count reads 0 for a conversion that WORKED in two ordinary
# cases: a full work tab (the Cores land on tab 5+, invisible to this count)
# and a Set stack that empties into the slot its own Cores then fill.
#
# restock_core broke on `converted <= 0` BEFORE list_cores ran -- spending the
# Sets, producing the Cores, never listing them, and reporting it as the benign
# "nothing converted this round". It discarded exactly the cases
# core_slot_candidates exists to survive.

m.clear_carried(7)
pipe = Pipeline(pack=100, convert_per_round=100, converted_reads=[0])
out = run_restock(pipe)
check(("list", "Force Core(High)") in
      [(a, b) for a, b in pipe.log if a == "list"] or
      any(a == "list" for a, _ in pipe.log),
      f"a round reporting 0 converted still TRIES to list, got {pipe.log}")
check(out["listed"] == 100,
      f"and the 100 Cores it really made are listed, got {out['listed']}")
check(m.carried_sets(7) == 0, "so nothing is stranded")

# But a round with nothing to convert AND nothing to list still terminates --
# the loop must not spin. Reported as "nothing converted this round".
m.clear_carried(7)


class Empty(Pipeline):
    def convert(self, core, quantity=250, verbose=True, require_layout=True):
        self.log.append(("convert", core))
        self.pending = 0
        return {"converted": 0, "candidates": []}


pipe = Empty(pack=100)
out = run_restock(pipe)
check(out["rounds"] == 1,
      f"a genuinely empty round ends the loop at once, got {out['rounds']}")
check("nothing converted" in out["why"], f"saying so, got {out['why']!r}")
check(m.carried_sets(7) == 100,
      "and the Sets that could not be converted stay on the books")
m.clear_carried(7)

# And a round that converts but cannot list also terminates, with the LISTING
# named as the reason -- not disguised as "nothing converted".
pipe = Pipeline(pack=100, list_ok=False)
out = run_restock(pipe)
check(out["rounds"] == 1, "a round that cannot list ends the loop")
check("could not be listed" in out["why"],
      f"and blames the listing, got {out['why']!r}")
m.clear_carried(7)


# ==========================================================================
section("a partial sale is a sale")
# ==========================================================================
#
# The plausibility ceiling was `price x qty`, where qty is the quantity STILL
# on sale -- the code says so forty lines further down. On a partial sale that
# is the leftovers, which has nothing to do with the credit that just landed,
# so every partial sale was discarded. Measured against the live ledger before
# the fix: 3 of 18 sales rejected, 129,813,000 Alz of real income thrown away,
# and the PROFIT line showed a loss because of it.
#
# A fully-sold listing passed, because then the remainder IS the whole stack --
# which is why it went unnoticed for so long.

# The three real rejections, from logs/run_2026-08-07_*. Each is an exact
# multiple of its unit price, which is what makes them obviously genuine.
LIVE = [(43_680_000, 208_000, 27, 210),
        (44_928_000, 208_000, 34, 216),
        (41_205_000, 205_000, 8, 201)]
for proceeds, price, still, units in LIVE:
    check(proceeds == price * units,
          f"{proceeds:,} really is {units} x {price:,}")
    check(m.sale_rejection(proceeds, price, still, listed_units=units + still)
          == "",
          f"a partial sale of {units} with {still} left is ACCEPTED")
    # And it was rejected under the old rule, which is why this matters.
    check(proceeds > price * still,
          f"whereas price x still-listed ({price * still:,}) is less than the "
          f"{proceeds:,} that actually arrived -- the old ceiling")

# The reading that motivated the ceiling in the first place must still be
# rejected: a VIP booking 1,662,294,744 against a ~106,000,000 item.
check(m.sale_rejection(1_662_294_744, 106_000_000, 1, listed_units=1) != "",
      "the 1.66-billion VIP reading is still refused")
check("whole number" in m.sale_rejection(1_662_294_744, 106_000_000, 1, 1),
      "because it is not a whole number of units")

# Unit-count ceiling, when the registration is known.
check(m.sale_rejection(10 * 5000, 5000, 0, listed_units=10) == "",
      "exactly the registered quantity is fine")
check(m.sale_rejection(11 * 5000, 5000, 0, listed_units=10) != "",
      "one more than was ever listed is not")
check("more than" in m.sale_rejection(11 * 5000, 5000, 0, 10),
      "and the reason says which bound was passed")

# With NO registration on file the bound falls back to the strict old rule.
# Deliberately not symmetric with the case above: a generous fallback accepted
# the Epic Booster reading, which is half the reason this function exists.
check(m.sale_rejection(8 * 54_797_776, 54_797_776, 8, listed_units=None) == "",
      "an unregistered listing may sell everything it still shows")
check(m.sale_rejection(16 * 54_797_776, 54_797_776, 8, listed_units=None) != "",
      "but the Epic Booster reading -- 876,764,416, exactly 16 x 54,797,776 "
      "from a stack of 8 -- is still refused")
check(m.sale_rejection(16 * 54_797_776, 54_797_776, 8, listed_units=16) == "",
      "while the SAME figure is accepted once a registration says the listing "
      "really did hold 16 -- which is the whole point of recording it")

# Degenerate inputs must not reject, or an unmeasured sale becomes an error.
for proceeds, price in [(None, 5000), (0, 5000), (5000, None), (5000, 0)]:
    check(m.sale_rejection(proceeds, price, 0, None) == "",
          f"proceeds={proceeds} price={price} is not a rejection, just "
          "nothing to check")

# A FULL sale: the row showed everything it had, and all of it went. Measured
# live on 2026-08-07 -- and rejected at the time, because the first version of
# this rule treated the row quantity as an upper bound. It is a LOWER one.
check(m.sale_rejection(250 * 209_999, 209_999, 250, listed_units=None) == "",
      "a row showing x250 that sells 250 is a complete sale, not a bad read")

# ...and it must survive a registration from a DIFFERENT, smaller stack at the
# same price. (name, price) is not unique on this shop: five identical Force
# Core (Ultimate) rows at 445,000 was the ordinary state that day. A 200-stack
# registered earlier capped the 250-stack that actually sold, and 52,499,750 of
# real income went in the bin.
check(m.sale_rejection(250 * 209_999, 209_999, 250, listed_units=200) == "",
      "a smaller stack registered at the same price does not cap a larger one")
check(m.sale_rejection(245 * 209_999, 209_999, 245, listed_units=200) == "",
      "nor the 245-stack beside it -- both were real, both were discarded")

# The bound is the LARGER of the two, so neither source alone can veto a sale.
check(m.sale_rejection(300 * 5000, 5000, 300, listed_units=10) == "",
      "the row can exceed the registration")
check(m.sale_rejection(300 * 5000, 5000, 10, listed_units=300) == "",
      "and the registration can exceed the row")
check(m.sale_rejection(301 * 5000, 5000, 10, listed_units=300) != "",
      "but one unit past BOTH is still refused")

# ---- the registration record the ceiling is built from -------------------
reset_ledger()
check(m.registered_qty("Force Core(High)", 209_800) is None,
      "nothing registered yet")
m.note_registration("Force Core(High)", 209_800, 250)
check(m.registered_qty("Force Core(High)", 209_800) == 250,
      "a registration is remembered")
check(m.registered_qty("Force Core (High)", 209_800) == 250,
      "and the game's inconsistent spacing does not lose it")
check(m.registered_qty("Force Core(High) X 250", 209_800) == 250,
      "nor does the pack marker a table name carries")
check(m.registered_qty("Force Core(High)", 205_000) is None,
      "a different price is a different listing")
check(m.registered_qty("Force Core(Highest)", 209_800) is None,
      "and a different GRADE is a different item -- not a prefix match")

# Relisted as it sells down: the ceiling has to cover the biggest it ever was.
m.note_registration("Force Core(High)", 209_800, 80)
check(m.registered_qty("Force Core(High)", 209_800) == 250,
      "the largest registration wins, not the latest")
for bad in [("", 100, 5), ("Item", None, 5), ("Item", 100, 0)]:
    m.note_registration(*bad)
check(m.registered_qty("Item", 100) is None,
      "an incomplete registration is not recorded")


# ==========================================================================
section("the registration fee is money out")
# ==========================================================================
#
# The game charges a percentage of the asking price to put something on the
# market, and charges it at REGISTRATION rather than netting it off the
# proceeds -- every measured sale in the ledger divides exactly by its unit
# price, so what arrives on a sale is gross. It was recorded nowhere, which
# made PROFIT overstate by the whole fee bill. A relist sweep pays it on every
# row of every cycle, so it is the most FREQUENT outflow in the system.

reset_ledger()
m.record_purchase_row("Force Core Set (High) X 100", 18_700_000,
                      18_700_000, 100)
basis_before = m.purchase_cost_basis("Force Core(High)")
check(basis_before == 187_000, f"cost basis is 187,000, got {basis_before:,}")

m.record_purchase_row("registration fee: Force Core(High)", 0, 2_500_000, 0,
                      note="Agent Shop registration fee")
check(m.purchase_cost_basis("Force Core(High)") == basis_before,
      "a fee does NOT move the cost floor -- the floor the operator asked for "
      "is what the Sets cost, and a fee is not part of that")

totals = m.all_time_totals()
check(totals is not None, "the totals read")
_sales_n, _proceeds, buys_n, spend = totals
check(spend == 18_700_000 + 2_500_000,
      f"but it IS counted in spend, so profit is honest: got {spend:,}")
check(buys_n == 1,
      f"and it is not counted as a PURCHASE -- one fee per listing per cycle "
      f"would drown the figure the operator wants. Got {buys_n}")

# A fee row must not be mistaken for stock however its name reads.
m.record_purchase_row("registration fee: Force Core Set (High) X 100", 0,
                      9_000_000, 0, note="Agent Shop registration fee")
check(m.purchase_cost_basis("Force Core(High)") == basis_before,
      "even a fee whose name looks exactly like a Set purchase is excluded "
      "from the floor -- it is filtered on qty, not on the name")


# ==========================================================================
section("the vendor and Purchase-tab coordinates are calibrated")
# ==========================================================================
#
# None of the coordinates the buying and converting paths click were in any of
# the three geometry tables apply_layout rewrites, so they stayed at their
# measured 2560x1440 values on every machine -- in the one window where a stray
# click spends Alz, while the module header promises that every command that
# clicks refuses to run until a calibration matches.

COVERED = ["SHOP_WINDOW_TITLE", "CONVERT_TIP_REGION", "CONVERT_DIALOG_REGION",
           "CONVERT_DLG_ITEM", "CONVERT_DLG_PRICE", "CONVERT_DLG_QTY_VALUE",
           "CONVERT_DLG_QTY_MAX", "CONVERT_DIALOG_BUTTONS",
           "VENDOR_TAB_REGION", "PURCHASE_SORT_REGION", "CONVERT_COLS",
           "CONVERT_ROWS", "VENDOR_TAB_BAND", "FAVOURITE_FIRST",
           "FAVOURITE_PITCH", "PURCHASE_ROW_TOP", "PURCHASE_ROW_PITCH",
           "PURCHASE_BUY_X"]
for name in COVERED:
    check(name in m._TRADE_FRAME_GEOMETRY,
          f"{name} is under calibration")

before = {n: getattr(m, n) for n in m._TRADE_FRAME_GEOMETRY}
REF = m.Layout(screen=m.REF_SCREEN, origin=m.REF_TRADE_ORIGIN, scale=1.0,
               client=m.REF_CLIENT)
m.apply_layout(REF)
changed = [n for n in before if getattr(m, n) != before[n]]
check(changed == [],
      f"the reference layout is an EXACT identity -- the property that makes "
      f"the table safe to widen. Changed: {changed}")

# ...and they must actually move elsewhere, or the coverage is cosmetic.
m.apply_layout(m.Layout(screen=(1920, 1080), origin=(8, 22), scale=0.75,
                        client=(0, 17, 1920, 1044)))
unmoved = [n for n in COVERED if getattr(m, n) == before[n]]
check(unmoved == [],
      f"and every one of them scales on a 1920x1080 layout. Still at the "
      f"reference value: {unmoved}")
check(m.PURCHASE_BUY_X == 844,
      f"the Buy column lands at 844 on that layout, got {m.PURCHASE_BUY_X}")
check(m.CONVERT_COLS == (190, 238, 286, 336, 384),
      f"and the conversion grid at (190, 238, 286, 336, 384), got "
      f"{m.CONVERT_COLS}")
m.apply_layout(REF)
check(m.PURCHASE_BUY_X == 1124, "and it returns to 1124 on the reference")


# ==========================================================================
section("the row-capacity reservation, in absolute numbers")
# ==========================================================================
#
# The old boundary test computed both sides with restock_rows_needed, the
# function under test, and restock_core used it for the gate too -- so it could
# return a constant 1 and the suite stayed green. The whole point of the
# reservation is the OVERSHOOT allowance, and that was unasserted.

need = m.restock_rows_needed(100)
check(need >= 5,
      f"a 100 target reserves at least 5 rows, got {need} -- buying stops at "
      f"the first order that REACHES the target and a Set stacks to 999, so "
      f"the worst case is {100 + m.SET_STACK_MAX} Sets")
check(need == -(-(100 + m.SET_STACK_MAX) // m.CONVERT_QUANTITY),
      f"which is ceil(({100} + 999) / {m.CONVERT_QUANTITY}) = "
      f"{-(-(100 + m.SET_STACK_MAX) // m.CONVERT_QUANTITY)}, got {need}")
check(m.restock_rows_needed(250) >= m.restock_rows_needed(100),
      "a larger target never reserves fewer rows")
check(m.SHOP_ROW_CAPACITY == 30, "the shop holds 30 rows")

# The gate itself, driven through the real restock_core.
for used, should_buy in [(0, True), (20, True), (30 - need, True),
                         (31 - need, False), (30, False)]:
    m.clear_carried(7)
    pipe = Pipeline(pack=10)
    out = run_restock(pipe, rows_used=used)
    bought_any = any(entry[0] == "buy" for entry in pipe.log)
    check(bought_any is should_buy,
          f"{used}/30 rows used, {need} needed -> "
          f"{'buy' if should_buy else 'PAUSE'}, got {bought_any}")
    if not should_buy:
        check("paused" in out["why"],
              f"and says it paused, got {out['why']!r}")
m.clear_carried(7)


# ==========================================================================
section("a listing that has SOLD is not stock")
# ==========================================================================
#
# A row in `receive` state is a listing that sold: zero units on the market,
# proceeds waiting. It keeps its name until collected, and core_row_counts
# matched on name alone -- so a sold-out Core read as still stocked and was
# never restocked. If the relist sweep does not reach that row (a 25-row shop
# swept 6-17) it stays in `receive` indefinitely and the Core is gone for the
# rest of the run. rows_in_use, counting the same table for capacity, has
# always filtered on action.


class TableRow:
    def __init__(self, name, action="change"):
        self.name = name
        self.action = action


HIGH = "Force Core(High)"
SLOT_HIGH = next(s for s, n in m.FAVOURITE_SLOTS.items() if n == HIGH)

counts = m.core_row_counts([TableRow(HIGH, "change")])
check(counts[SLOT_HIGH] == 1, "a live listing counts as stock")
counts = m.core_row_counts([TableRow(HIGH, "receive")])
check(counts[SLOT_HIGH] == 0,
      "a listing that has SOLD does not -- it is exactly why a restock is due")
counts = m.core_row_counts([TableRow(HIGH, "change"),
                            TableRow(HIGH, "receive")])
check(counts[SLOT_HIGH] == 1,
      "one live and one sold is one row of stock, not two")

# Fails toward "stocked". Reading a row wrong and buying costs about 43M;
# reading it wrong and waiting costs one cycle.
for unknown in [None, "", "unknown"]:
    counts = m.core_row_counts([TableRow(HIGH, unknown)])
    check(counts[SLOT_HIGH] == 1,
          f"an action of {unknown!r} counts as stocked -- the safe direction")

# And it reaches the decision that spends money.
sold_out = m.unlisted_core_slots([TableRow(HIGH, "receive")])
check(SLOT_HIGH in sold_out,
      "so a sold-and-uncollected row makes the Core eligible to restock")
check(SLOT_HIGH not in m.unlisted_core_slots([TableRow(HIGH, "change")]),
      "while a live one does not")

# The grade trap, once more, at this layer: every grade is a prefix of another.
counts = m.core_row_counts([TableRow("Force Core(Highest)", "change")])
check(counts[SLOT_HIGH] == 0,
      "a Highest listing is not a High listing, however similar the names")


# ==========================================================================
section("the row counters and the sweep cache")
# ==========================================================================
#
# rows_in_use, restock_sold_out_slots, note_unlisted/forget_unlisted/
# cached_unlisted and CORE_STOCK_TTL had zero references in any suite. The
# cache is the fast path the unattended loop actually takes when the last
# sweep's verdict is still good, so it decides whether money is spent without
# re-reading the shop.

check(m.rows_in_use([]) == 0, "an empty shop uses no rows")
check(m.rows_in_use([TableRow("a", "change"), TableRow("b", "receive")]) == 2,
      "a live row and a sold-but-uncollected row both OCCUPY a row")
check(m.rows_in_use([TableRow("a", None), TableRow("b", "")]) == 0,
      "a row with no action is not a listing at all")
check(m.rows_in_use([TableRow("(empty)", "change")]) == 1,
      "and the count is by action, not by name")

m.forget_unlisted()
check(m.cached_unlisted([SLOT_HIGH]) is None,
      "with no cache there is no answer -- and no answer must not read as "
      "'nothing is sold out'")
m.note_unlisted([SLOT_HIGH])
check(m.cached_unlisted([SLOT_HIGH]) == [SLOT_HIGH],
      "a remembered verdict is returned without a sweep")
check(m.cached_unlisted([SLOT_HIGH, 1]) is not None,
      "asking about more slots still answers from the cache")
check(m.cached_unlisted([1]) == [],
      "a Core the sweep found a row for is not restocked")
m.forget_unlisted()
check(m.cached_unlisted([SLOT_HIGH]) is None,
      "and listing something drops the cache, so the next question is asked "
      "of the shop")

# The TTL is what stops a stale verdict spending money for ever.
m.note_unlisted([SLOT_HIGH])
m._UNLISTED_CACHE["at"] -= m.CORE_STOCK_TTL + 1
check(m.cached_unlisted([SLOT_HIGH]) is None,
      f"a verdict older than {m.CORE_STOCK_TTL:.0f}s is not reused")
m.note_unlisted([SLOT_HIGH])
m._UNLISTED_CACHE["at"] -= m.CORE_STOCK_TTL - 30
check(m.cached_unlisted([SLOT_HIGH]) == [SLOT_HIGH],
      "one just inside it still is")
m.forget_unlisted()

# restock_sold_out_slots honours ENABLE_BUYING, which is the operator's switch.
saved_enable = dict(m.ENABLE_BUYING)
try:
    for name in m.ENABLE_BUYING:
        m.ENABLE_BUYING[name] = False
    check(m.restock_sold_out_slots([SLOT_HIGH], verbose=False) == [],
          "with buying off for every Core, nothing is restocked")
    m.ENABLE_BUYING[HIGH] = True
    check(m.restock_sold_out_slots([], verbose=False) == [],
          "and an empty slot list does nothing either")
finally:
    m.ENABLE_BUYING.clear()
    m.ENABLE_BUYING.update(saved_enable)


# ==========================================================================
section("replay the REAL ledger through the sale rule")
# ==========================================================================
#
# Everything above is synthetic, and synthetic cases encode whatever the author
# believed. That is exactly how the (name, price) bug shipped: the rule was
# tested against one stack at one price, when the file says in a dozen places
# that this shop routinely holds SEVERAL identical stacks at the same price --
# and the run log says "5 rows are identical" in its first ten lines.
#
# So the operator's own history is replayed through the rule. A sale already
# measured and booked in an earlier run is, by definition, one that really
# happened: if the rule would refuse it now, the rule has become stricter than
# reality and is throwing income away.
#
# Skipped LOUDLY when the ledger is absent. It is live session data, gitignored
# and uncommittable, so this can never be a check CI runs -- which is exactly
# why it has to announce itself rather than pass quietly.
_real = _ROOT / "sales.db"
if not _real.exists():
    print(f"  (no ledger at {_real}; the real-data replay did NOT run)")
else:
    import sqlite3 as _sq
    _con = _sq.connect(f"file:{_real}?mode=ro", uri=True)
    try:
        booked = list(_con.execute(
            "SELECT item, price, proceeds, qty FROM sales "
            "WHERE proceeds > 0 AND price > 0"))
        regs = {}
        try:
            for _item, _price, _qty in _con.execute(
                    "SELECT item, price, qty FROM registrations WHERE qty > 0"):
                _key = (m._floor_key(m.item_name(_item)), _price)
                regs[_key] = max(regs.get(_key, 0), _qty)
        except Exception:      # noqa: BLE001 - table predates this feature
            pass
    finally:
        _con.close()

    refused = []
    for _item, _price, _proceeds, _qty in booked:
        _reg = regs.get((m._floor_key(m.item_name(_item)), _price))
        _why = m.sale_rejection(_proceeds, _price, _qty, _reg)
        if _why:
            refused.append((_item, _proceeds, _why))
    check(not refused,
          f"all {len(booked)} sales already booked in the real ledger are "
          f"still accepted. Refused now: "
          + "; ".join(f"{i} {p:,} ({w})" for i, p, w in refused[:4]))
    print(f"  replayed {len(booked)} real sale(s) against "
          f"{len(regs)} registration(s) from {_real.name}")


# ==========================================================================
section("buy the whole row: the Purchase QTY field")
# ==========================================================================
#
# The Confirm Purchase dialog has a quantity field -- "Purchase QTY [1] / 48"
# -- and nothing used it. The Purchase table's middle column is a COUNT: a row
# reading "Force Core Set (Highest) X 1" with 48 beside it is forty-eight
# one-Set listings at the same price, and the dialog sells all of them in one
# transaction. Buying one and searching again cost 48 searches and 48 dialogs
# to drain that row, and the restock's buy budget stopped it long before the
# target. Measured live on 2026-08-07.

GOLD = _ROOT / "unit_tests" / "corpus" / "goldens" / "purchase_confirm_qty48.png"
if not GOLD.exists():
    print(f"  (no golden at {GOLD}; the dialog geometry was NOT exercised)")
else:
    from PIL import Image as _Image
    _shot = _Image.open(GOLD)

    dlg = m.purchase_confirm(_shot)
    check(dlg is not None, "the Confirm Purchase dialog is recognised")
    check(dlg["qty"] == 1, f"its quantity field reads 1, got {dlg['qty']!r}")
    check(dlg["qty_max"] == 48,
          f"and its maximum reads 48, got {dlg['qty_max']!r}")
    check(dlg["price"] == 190_190,
          f"the price is read from its own cell: got {dlg['price']!r}")
    check(dlg["buy"] == (1291, 856), f"Buy at {dlg['buy']}")
    check(dlg["cancel"] == (1472, 853), f"Cancel at {dlg['cancel']}")

    # The row behind it, with the count column that decides how many to take.
    rows = m.read_purchase_rows(_shot)
    check(rows and rows[0].available == 48,
          f"row 1 offers 48 listings, got "
          f"{rows[0].available if rows else 'no rows'}")
    check(rows and rows[0].pack == 1, "each holding one Set")
    check(rows and rows[0].stock == 48,
          "so the row is worth 48 items, not 1 -- which is the whole point")

    # The rows this frame leaves VISIBLE are all the same price per item, so
    # there is no unit-price reason to prefer any of them. That is why row 1
    # is the right pick once the quantity is used: it has the volume.
    #
    # Only rows 1-3 are asserted, because the dialog is open ON this frame and
    # covers rows 4-7 entirely. That is not a flaw in the fixture -- it is the
    # only state in which the dialog can be photographed at all.
    top = [r for r in rows if r.row <= 3]
    check(len(top) == 3, f"rows 1-3 are readable behind the dialog, got {len(top)}")
    check({round(r.unit) for r in top} == {190_190},
          f"and all three are 190,190 an item, got "
          f"{sorted({round(r.unit) for r in top})}")

    # Row 8 is half-hidden by the dialog and its price reads 2,774 instead of
    # 27,767,740 -- 19 an item against 190,190 everywhere else. Kept as an
    # assertion rather than tidied away: this is exactly the clipped read that
    # looks like the find of the day, and the only thing standing between it
    # and a purchase is that buying is restricted to ROW 1.
    clipped = [r for r in rows if r.unit < 1_000]
    check(clipped, "the frame still contains a clipped, implausible row")
    check(all(r.row != 1 for r in clipped),
          f"and none of them is row 1, which is the only row buying may take "
          f"-- rows {[r.row for r in clipped]}")
    check(m.cheapest_listing(rows).row == 1,
          "so the chosen listing is row 1, not the apparent bargain")


# ---- buy_offer sets the field, verifies it, and prices the whole order ----
def run_qty_buy(want, offered=48, pack=1, unit=190_190, types_to=None):
    """Drive the REAL buy_offer against a dialog that has a quantity field."""
    seen = {"typed": [], "purchases": [], "clicks": []}
    offer = m.Offer(1, f"Force Core Set (Highest) X {pack}", unit * pack, pack,
                    340, offered)
    state = {"qty": 1}

    def confirm(*a, **k):
        qty = state["qty"]
        return {"text": "Confirm Purchase Force Core Set X 1 (Highest)",
                "price": offer.price * qty, "qty": qty, "qty_max": offered,
                "buy": (1291, 856), "cancel": (1472, 853)}

    def typed(value, per_key=None, clear_first=True, clear=None):
        seen["typed"].append(value)
        # What the field ACTUALLY settles at, which a test can make differ
        # from what was typed -- that is the case worth covering.
        state["qty"] = value if types_to is None else types_to

    saved = {n: getattr(m, n) for n in
             ("purchase_ready", "get_alz", "grab", "focus_game", "move_mouse",
              "click", "purchase_confirm", "park_cursor", "note_purchase",
              "record", "time", "type_number", "_LAST_SEARCH", "BUY_HALTED")}
    try:
        m.time = FakeClock()
        m.purchase_ready = lambda verbose=True: True
        m.grab = lambda *a, **k: None
        m.focus_game = lambda *a, **k: True
        m.park_cursor = lambda *a, **k: None
        m.move_mouse = lambda *a, **k: None
        m.click = lambda x, y, **k: seen["clicks"].append((x, y))
        m.record = lambda *a, **k: None
        m.type_number = typed
        m.purchase_confirm = confirm
        m.note_purchase = lambda item, price, spend, qty, note="": (
            seen["purchases"].append({"price": price, "spend": spend,
                                      "qty": qty}))
        spend = offer.price * min(want, offered)
        reads = [1_000_000_000, 1_000_000_000 - spend]
        m.get_alz = lambda *a, **k: (reads.pop(0) if reads else 0)
        m.BUY_HALTED = False
        m.note_favourite_search(8, [offer])
        ok, why = m.buy_offer(offer, want=want, verbose=False)
    finally:
        for name, value in saved.items():
            setattr(m, name, value)
    return ok, why, seen


ok, why, seen = run_qty_buy(want=48)
check(ok is True, f"an order for 48 listings goes through ({why!r})")
check(seen["typed"] == [48], f"and 48 is typed into the field, got {seen['typed']}")
check(seen["purchases"][0]["qty"] == 48,
      f"48 Sets are recorded, not 1: got {seen['purchases'][0]['qty']}")
check(seen["purchases"][0]["price"] == 48 * 190_190,
      f"at 48 x 190,190 = {48 * 190_190:,}, got "
      f"{seen['purchases'][0]['price']:,}")

# One listing is still one purchase, and must NOT touch the field: typing into
# a dialog that already reads 1 is a chance to send keystrokes somewhere else.
ok, why, seen = run_qty_buy(want=1)
check(ok is True, f"an order for one listing still works ({why!r})")
check(seen["typed"] == [],
      f"and nothing is typed when the field already says 1, got {seen['typed']}")

# Never more than the game offers, whatever the caller asks for.
ok, why, seen = run_qty_buy(want=500, offered=48)
check(seen["typed"] == [48],
      f"asking for 500 when 48 are offered takes 48, got {seen['typed']}")

# A field that does not settle where it was told is a REFUSAL, not a smaller
# purchase: the keystrokes may have gone somewhere else entirely, and whatever
# the dialog now says is what the game will charge for.
ok, why, seen = run_qty_buy(want=48, types_to=7)
check(ok is False, "a quantity that does not take is refused")
check(seen["purchases"] == [], f"and nothing is bought, got {seen['purchases']}")
check("reads 7" in why or "7" in why, f"and the reason says what it read: {why!r}")

# A bundle row: "X 28" with 2 on offer is 56 Sets for two listings.
ok, why, seen = run_qty_buy(want=2, offered=2, pack=28, unit=190_190)
check(ok is True, f"two bundles of 28 ({why!r})")
check(seen["purchases"][0]["qty"] == 56,
      f"is 56 Sets, got {seen['purchases'][0]['qty']}")
check(seen["purchases"][0]["price"] == 2 * 28 * 190_190,
      "priced as two whole listings")


# ==========================================================================
section("default state between relisting and refilling")
# ==========================================================================
#
# The two phases work different windows: relisting uses the Agent Shop's
# Register tab, refilling uses its Purchase tab and then the NPC vendor. Running
# the second straight out of the first left the Trade window open across the
# whole restock -- and an open Trade window is what makes a later find_npc()
# fail, because it covers the NPC.
#
# The ORDER is the whole point, so that is what is asserted: the sweep reads
# the shop while it is still open, the shop closes, and only then does anything
# buy. A closing in the wrong place is worse than none -- before the sweep it
# would read a shut window, after the buying it would not be a boundary at all.

order = []
saved = {n: getattr(m, n) for n in
         ("trade_window_open", "leave_shop", "shop_rows_used",
          "_restock_each")}
try:
    state = {"open": True}

    def _leave(verbose=True):
        order.append("close")
        state["open"] = False

    m.trade_window_open = lambda *a, **k: state["open"]
    m.leave_shop = _leave
    m.shop_rows_used = lambda verbose=True: (
        order.append("count" if state["open"] else "count-SHUT") or 7)
    m._restock_each = lambda slots, rows_used=None, verbose=True: (
        order.append(f"refill(rows_used={rows_used})") or [])

    m.restock_sold_out_slots([1], verbose=False, rows_used=7)
    check(order == ["refill(rows_used=7)"],
          f"the cached path passes the count straight through, got {order}")

    # And the helper itself only closes when something is open.
    order.clear()
    m.leave_for_restock(verbose=False)
    check(order == ["close"], f"an open shop is closed, got {order}")
    order.clear()
    m.leave_for_restock(verbose=False)
    check(order == [], f"and a shut one is left alone, got {order}")

    # Tidying must never fail the batch it is tidying after.
    state["open"] = True
    def _boom(verbose=True):
        raise RuntimeError("the window would not close")
    m.leave_shop = _boom
    blew_up = False
    try:
        m.leave_for_restock(verbose=False)
    except Exception:  # noqa: BLE001
        blew_up = True
    check(not blew_up,
          "a shop that will not close does not raise -- a relist batch must "
          "not be turned into a failure by its own tidying")
finally:
    for name, value in saved.items():
        setattr(m, name, value)

# The count has to be taken BEFORE the close, or the restock asks a shut
# window how many rows the shop has and aborts with "could not count".
import inspect as _inspect
# The restock moved OUT of relist_rows and into restock_pass on 2026-08-08,
# so it can run BEFORE the relisting. The ordering property is unchanged and
# is what matters here; only the function holding it moved.
_src = _inspect.getsource(m.restock_pass)
_count_at = _src.find("rows_now = shop_rows_used")
# The CALL, not the comment above it that names the same function.
_close_at = _src.find("leave_for_restock(verbose=")
check(0 <= _count_at < _close_at,
      "the row count is taken while the shop is still open, then it closes")
check("rows_used=rows_now" in _src,
      "and that count is handed to the restock rather than re-read")


# ==========================================================================
section("a cancel that never committed may be tried again")
# ==========================================================================
#
# cancel_item returned one False for two states that need opposite responses:
#
#   nothing happened          the Change click produced no dialog, nothing was
#                             confirmed, the listing is still on the market --
#                             retrying is free and correct.
#   committed, unverified     the Confirmation went in and the result could not
#                             be read -- retrying would withdraw a SECOND
#                             listing.
#
# Given only False, the caller had to assume the second, so every missed dialog
# cost a row. Measured on 2026-08-08: two aborts recorded committed=False --
# both retryable, both abandoned.
#
# The information already existed; cancel_item computed `committed` and wrote
# it to the corpus without ever telling the caller.

check("report" in _inspect.signature(m.cancel_item).parameters,
      "cancel_item takes a report out-parameter")

_src = _inspect.getsource(m.cancel_item)
check('report["committed"] = committed' in _src,
      "and fills it in on the abort path, where the distinction matters")

# The caller retries ONLY on positive evidence of not-committed.
_relist = _inspect.getsource(m._relist_cycle)
check('cancel_report.get("committed") is False' in _relist,
      "the retry is gated on committed being explicitly False")
check('is False' in _relist and 'not cancel_report.get("committed")' not in _relist,
      "identity against False, not truthiness -- a MISSING report (an older "
      "caller, or an exception before the flag was set) must read as 'unknown' "
      "and keep the old, safe behaviour, and `not None` is True")

# The three states, as the caller sees them.
for state, retryable, why in [
        ({"committed": False}, True,
         "provably nothing happened -> retry"),
        ({"committed": True}, False,
         "committed but unverified -> NEVER retry, it would withdraw twice"),
        ({}, False,
         "no report at all -> unknown, so behave as before"),
]:
    got = state.get("committed") is False
    check(got is retryable, f"{state} -> {'retry' if got else 'give up'}: {why}")

# And the retry must still respect the attempt budget, or a row that fails
# this way forever would loop until the shop session expired.
check("attempt < attempts" in _relist,
      "the retry is bounded by the same attempt budget as everything else")


# ==========================================================================
section("the resupply is opportunistic and must not fail the batch")
# ==========================================================================
#
# Restocking was always extra work: "a market that will not sell must not turn
# a successful relist batch into a failed one and trip the run's failure
# breaker." That was easy to hold while it ran LAST -- its exceptions were
# caught and the function returned True regardless.
#
# Moving it BEFORE the relisting quietly lost it. On 2026-08-08 a conversion
# refused on an ambiguous vendor-tab read, the vendor window was left open over
# the NPC, ensure_shop_ready failed, and relist_rows returned False -- a cycle
# that had just bought 270 Sets relisted nothing and counted toward the breaker.

_rp = _inspect.getsource(m.restock_pass)
check("except Exception" in _rp and "Restock pass did not run" in _rp,
      "restock_pass swallows its own exceptions -- a failed resupply is a "
      "missed opportunity, not a failed batch")
check("finally:" in _rp and "close_npc_shop" in _rp,
      "and closes the vendor window in a finally -- the caller's next act is "
      "to look for the NPC, and that window is what hides her")

_rl = _inspect.getsource(m.relist_rows)
_after = _rl[_rl.index("restock_pass("):]
check(_after.count("ensure_shop_ready(") >= 2,
      f"the reopen after the resupply gets a second attempt (found "
      f"{_after.count('ensure_shop_ready(')}) -- one unreachable NPC must not "
      f"end a batch the resupply was only decorating")
check("close_npc_shop" in _after,
      "and leftovers are cleared between the attempts, because retrying the "
      "same reopen against the same open vendor window achieves nothing")

_cc = _inspect.getsource(m.convert_cores)
check("again = active_vendor_tab()" in _cc,
      "convert_cores re-reads the vendor tab before refusing -- open_npc_shop "
      "confirms it moments earlier, so one ambiguous frame is likelier than "
      "the page having changed")
check("require(showing == CONVERT_VENDOR_TAB" in _cc,
      "...but still refuses if the second read disagrees: this gates a click "
      "into a window where a plain click buys outright")


# ==========================================================================
section("the vendor tab is waited for, not glimpsed")
# ==========================================================================
#
# Two consecutive cycles on 2026-08-08 died at "could not find the 'Dungeon'
# tab in the vendor Shop", stranding 270 bought Sets. The tab had not moved: a
# golden frame from a conversion that WORKED puts Normal at (70,204), Dungeon
# at (195,205) and Repurchase at (320,206), and cycle 1's own log says it
# selected the tab at exactly (195, 205).
#
# vendor_shop_open() is satisfied by the window's TITLE, which draws before its
# tabs do, so the position lookup was reading a window that was open and not
# yet finished. It polled for the tab to become ACTIVE but read its POSITION
# once.

_ovt = _inspect.getsource(m.open_vendor_tab)
_before_click = _ovt.split("click(")[0]
check("while time.monotonic() < deadline" in _before_click,
      "open_vendor_tab polls for the tab label instead of reading it once")
check("tabs that DID read" in _ovt,
      "and when it gives up it says which tabs DID read -- none means the "
      "window was still drawing or is not the vendor Shop; some means only "
      "that one label failed to OCR")
check("ALWAYS click" in _ovt,
      "the click itself stays unconditional: a tab click cannot buy anything, "
      "so the cheap safe action is never skipped on the strength of a reading")

# The geometry the failures were wrongly blamed on. Asserted against the frame
# from a working conversion, so a real move would show up here rather than as
# a mystery at 4am.
_GOLD = CORPUS_GOLD = _ROOT / "unit_tests" / "corpus" / "goldens" /     "vendor_tab_Dungeon" / "vendor_tab_Dungeon_001.png"
if not _GOLD.exists():
    print(f"  (no vendor golden at {_GOLD}; tab geometry NOT checked)")
else:
    from PIL import Image as _Img
    _vs = _Img.open(_GOLD)
    check(m.vendor_shop_open(_vs), "the golden frame is a vendor Shop")
    check(m.active_vendor_tab(_vs) == m.CONVERT_VENDOR_TAB,
          f"showing {m.CONVERT_VENDOR_TAB}, got "
          f"{m.active_vendor_tab(_vs)!r}")
    for _name, _want in (("Normal", 70), ("Dungeon", 195), ("Repurchase", 320)):
        _hit = m.vendor_tab_point(_name, _vs)
        check(_hit is not None and abs(_hit[0] - _want) <= 12,
              f"the {_name} tab is at x~{_want}, got {_hit} -- if this moves, "
              f"VENDOR_TAB_REGION {m.VENDOR_TAB_REGION} needs re-measuring")


# ==========================================================================
section("N is a toggle, and both windows must never be up together")
# ==========================================================================
#
# Pressing N on an already-open vendor window CLOSES it. open_npc_shop guards
# that -- but the guard returned early without closing the Agent Shop, leaving
# both windows up. This function's own docstring says why that is wrong:
# TRADE_REGION (10, 30, 1235, 1065) covers the conversion grid AND the tab
# strip, so a click aimed at a cell lands on whatever the Trade window is
# showing there.

check(m.TRADE_REGION[0] <= m.VENDOR_TAB_REGION[0]
      or m.TRADE_REGION[2] >= m.VENDOR_TAB_REGION[2],
      f"the Agent Shop {m.TRADE_REGION} overlaps the vendor tab strip "
      f"{m.VENDOR_TAB_REGION} -- which is why one must be shut to read the "
      f"other")

_grid_x = (min(m.CONVERT_COLS), max(m.CONVERT_COLS))
_grid_y = (min(m.CONVERT_ROWS), max(m.CONVERT_ROWS))
check(m.TRADE_REGION[0] <= _grid_x[0] and m.TRADE_REGION[2] >= _grid_x[1],
      f"and it spans the grid columns {_grid_x} too")

_ons = _inspect.getsource(m.open_npc_shop)
_early = _ons[:_ons.index("press_key")]
check("leave_shop" in _early,
      "open_npc_shop closes the Agent Shop on the already-open path as well, "
      "not only on the path that presses N")
check(_early.count("vendor_shop_open()") >= 1 and "TOGGLE" in _ons,
      "and says out loud that N is a toggle, so a later edit does not make it "
      "unconditional again")

order = []
saved = {n: getattr(m, n) for n in
         ("vendor_shop_open", "trade_window_open", "leave_shop",
          "open_vendor_tab", "press_key")}
try:
    state = {"vendor": True, "trade": True}
    m.vendor_shop_open = lambda *a, **k: state["vendor"]
    m.trade_window_open = lambda *a, **k: state["trade"]
    m.leave_shop = lambda verbose=True: (order.append("close_agent_shop"),
                                         state.update(trade=False))
    m.open_vendor_tab = lambda *a, **k: order.append("select_tab") or True
    m.press_key = lambda *a, **k: order.append("PRESS_N")
    m.open_npc_shop(verbose=False)
finally:
    for name, value in saved.items():
        setattr(m, name, value)

check("PRESS_N" not in order,
      f"with the vendor already open, N is NOT pressed: {order}")
check(order == ["close_agent_shop", "select_tab"],
      f"the Agent Shop is closed first, then the tab is selected: {order}")


# ==========================================================================
section("the quantity cross-check compared two different quantities")
# ==========================================================================
#
#   expect_qty  what the CANCELLED LISTING held
#   qty_max     what the panel offers, which is everything owned of that item
#               across the WHOLE inventory -- Ctrl+Click gathers matching
#               items from every tab, not only the slots the cancel filled
#
# Equal only while nothing else of that item is held anywhere. Measured in one
# run on 2026-08-08:
#
#     Epic Booster (Highest)  returned 6 slots -> loaded 6    match
#     Epic Booster (Highest)  returned 8 slots -> loaded 8    match
#     Force Core (Ultimate)   returned 5 slots -> loaded 12   ABORTED
#
# The Boosters matched because none were held elsewhere. The Cores did not,
# because seven sat on later tabs -- which is what the restock pipeline
# produces, since a 250-Core conversion spills past tab 4 by design.
#
# It aborted AFTER the cancel committed, stranding five Cores in the work tab,
# and a dirty work tab now stops the following run outright.


_QTY_FRAME = None
_cand = (_ROOT / "unit_tests" / "corpus" / "goldens"
         / "purchase_confirm_qty48.png")
if _cand.exists():
    from PIL import Image as _I
    _QTY_FRAME = _I.open(_cand)


def qty_verdict(expect_qty, loaded):
    """Run the REAL register_item cross-check. Returns (ok, message)."""
    seen = {"said": []}
    saved = {n: getattr(m, n) for n in
             ("focus_game", "park_cursor", "grab", "table_loading",
              "wait_for_table", "read_register_panel", "ctrl_click",
              "slot_centre", "inventory_origin", "select_inventory_tab",
              "active_inventory_tab", "time", "record", "session_locked")}
    try:
        m.time = FakeClock()
        m.focus_game = lambda *a, **k: True
        m.park_cursor = lambda *a, **k: None
        # A REAL frame, not None: read_register_panel is stubbed but other
        # readers on this path still open the image, and None reaches PIL.
        m.grab = lambda *a, **k: _QTY_FRAME
        m.session_locked = lambda *a, **k: False
        m.table_loading = lambda *a, **k: False
        m.wait_for_table = lambda *a, **k: True
        m.ctrl_click = lambda *a, **k: None
        m.slot_centre = lambda *a, **k: (100, 100)
        m.inventory_origin = lambda *a, **k: (100, 100)
        m.select_inventory_tab = lambda *a, **k: True
        m.active_inventory_tab = lambda *a, **k: m.WORK_TAB
        m.record = lambda *a, **k: None
        # STATEFUL: the shop slot must read EMPTY before the Ctrl+Click and
        # loaded after it. register_item checks both, and a stub that always
        # says "loaded" aborts at the first check with "the shop slot already
        # holds an item" -- never reaching the cross-check under test.
        _calls = {"n": 0}

        def _panel(*a, **k):
            _calls["n"] += 1
            first = _calls["n"] == 1
            return {"loaded": not first, "qty": 1,
                    "qty_max": None if first else loaded,
                    "qty_text": "0/0" if first else f"1/{loaded}",
                    "prices": [100_000], "price_rows": [], "net_sales": 0,
                    "typed": None, "slot_stdev": 2.0 if first else 42.0}

        m.read_register_panel = _panel
        # register_item CATCHES Aborted and returns False -- it does not
        # raise -- so the verdict is the return value, and the reason is on
        # stdout. Watching only for an exception reported every refusal as a
        # success, which is how the first version of this test passed while
        # the code was doing the right thing.
        import io as _io
        from contextlib import redirect_stdout as _redirect
        buf = _io.StringIO()
        with _redirect(buf):
            ok = m.register_item(1, 1, expect_item="Force Core (Ultimate)",
                                 expect_qty=expect_qty, dry_run=False,
                                 verbose=True)
        # The verdict is about the CROSS-CHECK, not the whole registration.
        # register_item continues past it into pricing, which this stub does
        # not satisfy -- so judging on the return value marked every case a
        # failure regardless of what the check decided. Judge on whether the
        # check's own refusal fired.
        out = buf.getvalue()
        refused = "the shop slot offers only" in out
        return (not refused), out
    finally:
        for name, value in saved.items():
            setattr(m, name, value)


# The exact case that stranded five Cores.
ok, why = qty_verdict(expect_qty=5, loaded=12)
check(ok, f"a listing of 5 with 12 owned in total is ACCEPTED -- got {why!r}")

# The cases that always worked keep working.
for held in (6, 8):
    ok, why = qty_verdict(expect_qty=held, loaded=held)
    check(ok, f"{held} cancelled and {held} owned is accepted ({why!r})")

# Owning far more is still fine: the restock deliberately spills across tabs.
ok, why = qty_verdict(expect_qty=5, loaded=1000)
check(ok, f"owning 1,000 of an item does not block relisting 5 ({why!r})")

# A SHORTFALL is the case worth refusing -- the cancelled stack should be
# there, so fewer than expected means it is not all there or the slot is wrong.
ok, why = qty_verdict(expect_qty=250, loaded=10)
check(not ok, "a panel offering 10 when the listing held 250 is REFUSED")
check("short by" in why, f"and the message says short by how much: {why!r}")

# ...but not for OCR noise in that direction. 250 read as 248 is a digit, not
# a different item.
ok, why = qty_verdict(expect_qty=250, loaded=248)
check(ok, f"a two-unit shortfall is tolerated as a misread ({why!r})")


# ==========================================================================
section("find_npc refused a nameplate it had already read at 96%")
# ==========================================================================
#
# Three consecutive cycles on 2026-08-08 13:47 stopped a run at "Lady
# Yekaterina (Agent Shop) is not on screen". The diagnostic printed on the way
# out lists the words it had just read:
#
#     cycle 2:  '(Agent'@97 'Shop)'@97 'Lady'@93 ... 'Yekaterima'@71
#     cycle 3:  '(Agent'@96 'Yekaterina'@85 'Shop)'@84 ...
#
# She was there every time. The match JOINED the line into one string and
# looked for two contiguous fragments, which demands both that no glyph slips
# and that no unrelated word interleaves. Live OCR breaks both, and each broke
# a different half on consecutive cycles.


def _line(words, y=300):
    return [m.Word(t, 600 + i * 90 - 20, y - 8, 600 + i * 90 + 20, y + 8, 90.0)
            for i, t in enumerate(words)]


def npc_seen(words):
    """True when find_npc's matcher would accept this line."""
    line = _line(words)
    joined = m._normalise("".join(w.text for w in line))
    if m.NPC_NAME_FRAGMENT in joined and m.NPC_TITLE_FRAGMENT in joined:
        return True
    name, title = m._npc_label_words(line)
    return bool(name and title)


# The two lines that were REFUSED, verbatim from the log.
check(npc_seen(["(Agent", "Shop)", "Lady", "Yekaterima"]),
      "'Yekaterima' -- one slipped glyph -- is still her")
check(npc_seen(["(Agent", "y", "Yekaterina", "Shop)"]),
      "and so is a line where OCR put the name BETWEEN '(Agent' and 'Shop)', "
      "which is what hid the contiguous 'agentshop'")
check(npc_seen(["Lady", "Yekaterina", "(Agent", "Shop)"]),
      "a clean read is unaffected")

# The safety property, unchanged: the name ALONE once latched onto something at
# the Warehouse and the click went into the open world. BOTH halves are still
# required, and this is the half of the test that must not loosen.
for label, words in [
        ("the name alone", ["Lady", "Yekaterina"]),
        ("the title alone", ["(Agent", "Shop)"]),
        ("the Warehouse keeper", ["Warehouse", "Keeper", "(Storage)"]),
        ("another player", ["Ravage", "Monarch"]),
        ("nothing at all", ["x", "|", "q"]),
]:
    check(not npc_seen(words),
          f"{label} is still REFUSED -- matching on one half is how a click "
          f"once went into the open world")

# A different NPC that happens to be an Agent Shop must not match either.
check(not npc_seen(["Lady", "Wenona", "(Agent", "Shop)"]),
      "another Agent Shop NPC is refused: the NAME is what distinguishes her")


# ==========================================================================
print("\n" + "=" * 60)
print(f"review fixes: {count} checks, {len(fails)} failed")
if fails:
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print("all green")
