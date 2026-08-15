"""chaos_pass: collect first, count second, buy only if the margin clears.

The operator's rule is that Chaos has priority over everything: before any
non-Chaos row is relisted, a sold bundle is collected and replaced. This file
drives the real chaos_pass with the game replaced, and asserts the ORDER as
well as the outcome, because the order is where this can be quietly wrong.

Why collect-before-count is load-bearing: a sold bundle sits in `receive` until
it is collected, and until then it still occupies a row. Counting first sees
CHAOS_ROWS rows and concludes the shelf is full, so a sold-out shelf is never
refilled -- the exact failure the whole pass exists to prevent, arrived at from
the tidy-looking direction.
"""
import os
import pathlib
import sys
import tempfile

# ITS OWN DATABASE, set before the import: SALES_DB is resolved at module
# scope. chaos_pass records a cost lot for every bundle it lists, so without
# this the suite writes invented lots straight into the ledger the live script
# prices against -- and a lot with no bundle behind it floors a real future
# listing against a cost nothing is backing. This escaped notice only because
# sales_db() was returning None while the schema was broken.
_DB = pathlib.Path(tempfile.gettempdir()) / f"chaos_pass_test_{os.getpid()}.db"
if _DB.exists():
    _DB.unlink()
os.environ["CABAL_SALES_DB"] = str(_DB)

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m  # noqa: E402

assert str(m.SALES_DB) == str(_DB), (
    f"refusing to run against the real ledger at {m.SALES_DB}")

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


SET_NAME = "Chaos Core Set"


def row(index, name, action="change", qty=1, price=22_222_050):
    return m.Row(index=index, name=name, change=(1126, 300 + 76 * index),
                 top=0, bottom=0, action=action, price=price, qty=qty)


class Game:
    """The whole game, replaced. Records what the pass did, in order."""

    def __init__(self, listings, core_price=680_000, set_price=740_735,
                 set_pack=1, crafted=200, unreadable=False, held_slots=1):
        self.listings = list(listings)
        self.unreadable = unreadable
        # How many inventory slots the work tab holds AFTER compressing. One
        # means the merge worked; more means it did not.
        self.held_slots = held_slots
        self.core_price = core_price
        self.set_price = set_price
        self.set_pack = set_pack
        self.crafted = crafted
        self.events = []
        self.saved = {}
        # WHICH TAB THE CLIENT IS ON, modelled rather than waved through.
        #
        # The first version of this stub let every search succeed regardless of
        # tab, and was therefore blind to the bug it should have caught:
        # register_item leaves the client on the REGISTER tab, so the second
        # buy of a two-row resupply found nothing and the pass stopped one row
        # short of CHAOS_ROWS. A stub that cannot be on the wrong tab cannot
        # catch a wrong-tab bug. It starts on "register" because that is where
        # relist_rows has just been working when chaos_pass is called.
        self.tab = "register"
        # Set False to make the Purchase tab (and so the Low-to-High sort)
        # unreachable from the start.
        self.sort_ok = True
        # ...or after N successful trips, to fail it AFTER the margin gate has
        # already passed. Without this the pass never reaches its own
        # per-buy sort check, because the gate refuses first and the guard
        # inside the loop is never exercised.
        self.sort_fails_after = None
        self.purchase_tabs = 0
        # Every cost_floor register_item was handed.
        self.cost_floors = []

    def __enter__(self):
        names = ("await_rows", "relist", "open_purchase_tab",
                 "run_favourite_search", "buy_offer", "leave_shop",
                 "open_craft_window", "craft_chaos_sets", "compress_stack",
                 "ensure_shop_ready", "register_item", "inventory_origin",
                 "select_inventory_tab", "press_escape", "craft_window_open",
                 "record", "grab", "occupied_slots", "avoid_warlag",
                 "scroll_to_end", "shop_listing_pairs")
        for n in names:
            self.saved[n] = getattr(m, n)
        m.await_rows = lambda timeout=8.0, poll=0.5: list(self.listings)
        m.relist = self._relist
        m.open_purchase_tab = self._open_purchase
        m.run_favourite_search = self._search
        m.buy_offer = self._buy
        m.leave_shop = lambda verbose=True: True
        m.open_craft_window = self._open_craft
        m.craft_chaos_sets = self._craft
        m.compress_stack = self._compress
        m.ensure_shop_ready = lambda verbose=True: True
        m.register_item = self._register
        m.inventory_origin = lambda source=None: (100, 100)
        m.select_inventory_tab = lambda tab, origin=None, timeout=5.0: True
        m.press_escape = lambda *a, **k: None
        m.craft_window_open = lambda source=None: False
        m.record = lambda label, *a, **k: self.events.append(("record", label))
        # No screenshots. Without these two the pass reaches a real grab() and
        # this suite would read the operator's actual screen.
        m.avoid_warlag = self._warlag
        # chaos_pass re-counts the shelf from a FRESH read, and it now anchors
        # at the top first: await_rows numbers rows by SCREEN position while
        # the chaos scope is in ABSOLUTE row numbers, and the two coincide only
        # at the top of the table. Both the anchor and the ranged read have to
        # be stubbed or the re-count reaches the real game.
        m.scroll_to_end = lambda up, timeout=8.0, verbose=True: list(self.listings)
        m.shop_listing_pairs = lambda timeout=8.0, verbose=True, stop_after=None: [
            (r.index, r) for r in self.listings]
        m.grab = lambda *a, **k: None
        m.occupied_slots = lambda shot=None, origin=None: [
            (1, i + 1) for i in range(self.held_slots)]
        return self

    def __exit__(self, *exc):
        for n, v in self.saved.items():
            setattr(m, n, v)

    def _relist(self, index, **kw):
        self.events.append(("collect", index))
        row = next((r for r in self.listings if r.index == index), None)
        # Collecting removes the row, exactly as the game does.
        self.listings = [r for r in self.listings if r.index != index]

        # AND IT RETIRES THE LOT, because the real relist does.
        #
        # chaos_pass used to clear the lot itself; it now delegates to relist,
        # whose comment records why ("THE LOT IS RETIRED BY relist(), NOT
        # HERE" -- two retirements for one sale emptied the ledger after ~4
        # sales). This fake still did the old thing: returned RELISTED and
        # cleared nothing, so the assertion that a collected bundle drops its
        # cost floor could never pass however correct the code was.
        #
        # A fully sold row comes back SOLD_OUT, not RELISTED -- that is the
        # value the caller branches on.
        if row is not None and getattr(row, "action", None) == "receive":
            if m.is_chaos_set(row.name):
                m.clear_cheapest_chaos_lot()
            return m.SOLD_OUT
        return m.RELISTED

    def _warlag(self, allowance=0.0, verbose=True, dry_run=False):
        self.events.append(("warlag", allowance))
        return 0.0

    def _open_purchase(self, timeout=10.0, verbose=True):
        # The real one SETS the Low-to-High sort on every success path and
        # returns False if it could not confirm it, so the stub ties reaching
        # the Purchase tab and the sort together the same way.
        self.purchase_tabs += 1
        ok = self.sort_ok and not (
            self.sort_fails_after is not None
            and self.purchase_tabs > self.sort_fails_after)
        self.events.append(("purchase_tab", ok))
        if not ok:
            return False
        self.tab = "purchase"
        return True

    def _search(self, slot, **kw):
        if self.tab != "purchase":
            # purchase_ready() refuses to click Purchase-tab coordinates on
            # another tab, and run_favourite_search returns [] when it does.
            self.events.append(("search_refused", slot))
            return []
        self.events.append(("search", slot))
        if self.unreadable:
            # The market read failed -- the shop was slow, or the row scrolled.
            # Not "the margin is zero".
            return []
        if slot == m.CHAOS_CORE_SLOT:
            return [m.Offer(row=1, name="Chaos Core", price=self.core_price,
                            pack=1, y=340, available=500)]
        return [m.Offer(row=1, name="Chaos Core Set", price=self.set_price,
                        pack=self.set_pack, y=340, available=1)]

    def _buy(self, offer, want=1, timeout=8.0, report=None, verbose=True):
        self.events.append(("buy", want))
        if report is not None:
            report["take"] = want
            report["items"] = want
        return True, ""

    def _open_craft(self, timeout=8.0, verbose=True):
        self.events.append(("craft_window", None))
        return True

    def _craft(self, timeout=8.0, verbose=True):
        self.events.append(("craft", self.crafted))
        return self.crafted

    def _compress(self, r, c, verbose=True, tab=None):
        self.events.append(("compress", (r, c, tab)))
        return True

    def _register(self, r, c, **kw):
        # Registering happens on the Register tab and LEAVES the client there.
        # This is the whole reason the next buy needs the tab re-established.
        self.tab = "register"
        self.cost_floors.append(kw.get("cost_floor"))
        self.events.append(("list", (r, c)))
        rep = kw.get("report")
        if rep is not None:
            rep["price"] = self.set_price * self.crafted
            rep["qty"] = 1
        self.listings.append(row(90, SET_NAME))
        return True


def kinds(events):
    return [e[0] for e in events if e[0] != "record"]


_saved_enabled = m.CHAOS_ENABLED
m.CHAOS_ENABLED = True
try:
    # -- at target: nothing happens -------------------------------------
    full = [row(1, "Epic Booster (Highest)"),
            row(2, SET_NAME), row(3, SET_NAME)]
    with Game(full) as g:
        ok = m.chaos_pass(verbose=False)
    check(ok is True, f"a full shelf is a clean pass, got {ok!r}")
    check("buy" not in kinds(g.events),
          f"and nothing is bought, got {kinds(g.events)}")

    # -- one sold: collect, then refill ---------------------------------
    sold = [row(1, "Epic Booster (Highest)"),
            row(2, SET_NAME, action="receive"), row(3, SET_NAME)]
    with Game(sold) as g:
        m.chaos_pass(verbose=False)
    ks = kinds(g.events)
    check("collect" in ks, f"a sold bundle is collected, got {ks}")
    check("buy" in ks, f"and replaced, got {ks}")
    check(ks.index("collect") < ks.index("buy"),
          f"COLLECT MUST COME FIRST -- counting before collecting sees the "
          f"sold row as occupying the shelf and never refills. got {ks}")

    # -- a sold bundle's cost floor dies with it ----------------------------
    # Asserted against the database rather than the event list, because the
    # damage is invisible at the shelf: a lot left behind goes on flooring the
    # NEXT bundle of the same price against Cores that are already sold, so one
    # run of dear Cores would keep every later cheap bundle off the market.
    conn = m.sales_db()
    conn.execute("DELETE FROM chaos_lots")
    conn.commit()
    conn.close()
    m.note_chaos_lot(680_000, 22_222_050, 30)   # the price row() lists at
    check(len(m.chaos_lots()) == 1, "the lot was seeded")

    sold_row = [row(1, "Epic Booster (Highest)"),
                row(2, SET_NAME, action="receive"), row(3, SET_NAME)]
    with Game(sold_row) as g:
        m.chaos_pass(verbose=False)
    check(not any(l[2] == 22_222_050 for l in m.chaos_lots()),
          f"collecting a sold chaos row must clear the cost floor held for it; "
          f"got {m.chaos_lots()}")

    # -- the whole chain runs in order ----------------------------------
    with Game([row(1, "Epic Booster (Highest)")]) as g:
        m.chaos_pass(verbose=False)
    ks = kinds(g.events)
    for earlier, later in (("buy", "craft"), ("craft", "compress"),
                           ("compress", "list")):
        check(earlier in ks and later in ks and ks.index(earlier) < ks.index(later),
              f"{earlier} must precede {later}; got {ks}")
    # K IS A MINIMUM, NOT A TARGET -- and the assertion has to say so.
    #
    # This used to require an order of exactly CHAOS_BUY_QUANTITY. The rule
    # changed at the operator's instruction ("Min floor is 200, i.e if not
    # reached yet continue buying. If reached, then compress and relist"), so
    # orders now take THE WHOLE ROW and the total is allowed to overshoot --
    # bounded by one row's depth, because the loop stops as soon as `got`
    # reaches the minimum. Demanding an exact K here was asserting the
    # behaviour the operator asked to have removed.
    bought = [e for e in g.events if e[0] == "buy"]
    check(bought, f"it buys Cores at all, got {g.events}")
    check(sum(qty for _, qty in bought) >= m.CHAOS_BUY_QUANTITY,
          f"buying continues until the K={m.CHAOS_BUY_QUANTITY} MINIMUM is "
          f"reached; got {bought}")
    # WITH TEETH, unlike `all(qty > 0)`, which holds for any purchase at all
    # and so could not tell the two rules apart.
    #
    # The fake offers a row of 500 against a K of 100. Taking the WHOLE ROW
    # means the first order is 500; computing the REMAINDER would make it 100.
    # Those numbers differ, so this check fails if the rule is ever changed
    # back -- which is the only reason to write it.
    check(bought and bought[0][1] == 500,
          f"the first order takes the whole 500-deep row, not the "
          f"{m.CHAOS_BUY_QUANTITY} still needed; got {bought}")

    # -- BOTH missing rows get stocked, not just the first ------------------
    # register_item leaves the client on the Register tab, so unless the pass
    # re-establishes the Purchase tab each time round, the second search comes
    # back empty and the loop stops one row short -- silently, because a failed
    # search is a normal outcome. With N=2 that is half the shelf.
    with Game([row(1, "Epic Booster (Highest)")]) as g:
        m.chaos_pass(verbose=False)
    ks = kinds(g.events)
    check(ks.count("list") == m.CHAOS_ROWS,
          f"an empty shelf must be filled to N={m.CHAOS_ROWS} rows, not "
          f"stop after the first; got {ks.count('list')} listing(s): {ks}")
    check("search_refused" not in ks,
          f"and no search may run on the wrong tab; got {ks}")
    check(ks.count("purchase_tab") >= ks.count("buy"),
          f"the Purchase tab is re-established at least once per buy -- it is "
          f"the ONLY thing that sets the Low-to-High sort, and buy_offer always "
          f"takes row 1. Sorted High to Low, row 1 is the DEAREST offer. "
          f"got {ks}")

    # If the sort cannot be confirmed, buy nothing at all.
    with Game([row(1, "Epic Booster (Highest)")]) as g:
        g.sort_ok = False
        ok = m.chaos_pass(verbose=False)
    ks = kinds(g.events)
    check("buy" not in ks,
          f"an unconfirmable Low-to-High sort must stop the buy, not proceed "
          f"and take row 1 of an unknown ordering; got {ks}")

    # A sort that stops being confirmable AFTER the margin gate. This is the
    # case the gate hides: it opens the Purchase tab once itself, so a
    # start-to-finish failure never reaches the guard inside the buy loop.
    with Game([row(1, "Epic Booster (Highest)")]) as g:
        g.sort_fails_after = 1        # the margin gate's trip succeeds, the buy's does not
        ok = m.chaos_pass(verbose=False)
    ks = kinds(g.events)
    check("buy" not in ks,
          f"once the Low-to-High sort cannot be re-confirmed, the buy must "
          f"STOP -- buy_offer takes row 1, and row 1 of a High-to-Low list is "
          f"the dearest offer on the board. got {ks}")
    check("chaos.sort_unconfirmed" in [e[1] for e in g.events
                                       if e[0] == "record"],
          "and it is recorded, so an unsorted market is visible afterwards")

    # -- THE WAR LAG ---------------------------------------------------------
    # A chaos row is the one sequence here that cannot be interrupted: stopping
    # between the buy and the listing leaves paid-for Cores in the work tab.
    # So the wait has to happen BEFORE any money moves, and has to ask for
    # enough time to finish the whole row rather than a relist row's worth.
    with Game([row(1, "Epic Booster (Highest)")]) as g:
        m.chaos_pass(verbose=False)
    ks = kinds(g.events)
    check("warlag" in ks,
          f"chaos must check the war lag like every other long action; got {ks}")
    check(ks.index("warlag") < ks.index("buy"),
          f"and it must check BEFORE buying -- waiting afterwards means the "
          f"Cores are already paid for and stranded. got {ks}")
    allowances = [e[1] for e in g.events if e[0] == "warlag"]
    # Against the DERIVED value, not the constant. They are equal at the
    # default K, so asserting the constant would pass by coincidence and go
    # on passing after the derivation stopped being used.
    check(allowances and all(a == m.chaos_row_allowance() for a in allowances),
          f"it must ask for the derived chaos allowance "
          f"({m.chaos_row_allowance():.0f}s), got {allowances}")
    # THE OPERATOR'S FLAT MARGIN, not a derived reservation.
    #
    # These used to assert that a chaos row reserved more than a relist row and
    # covered CRAFT_SETTLE_MAX -- i.e. that the allowance was sized to the work
    # so a started row could FINISH before the war window. The operator
    # overrode that on 2026-08-10 ("I want it to stop -1min only"), so every
    # allowance is now WAR_STOP_MARGIN.
    #
    # What is pinned instead is that the override is real and uniform: a
    # derived figure creeping back would change war behaviour silently. The
    # cost of the override is recorded in chaos_row_allowance's own comment --
    # the sequence actually needs ~660s at K=100, so a row CAN now be started
    # inside a window it cannot finish.
    check(m.chaos_row_allowance() == m.WAR_STOP_MARGIN,
          f"the chaos allowance is the operator's flat margin "
          f"({m.WAR_STOP_MARGIN:g}s), got {m.chaos_row_allowance():.0f}s")
    check(m.WAR_ROW_ALLOWANCE == m.WAR_STOP_MARGIN
          and m.WAR_RESTOCK_ALLOWANCE == m.WAR_STOP_MARGIN,
          "and every other allowance uses the same margin, so one knob moves "
          "them together")
    check(len([e for e in g.events if e[0] == "warlag"]) == ks.count("buy"),
          f"once per row started, got {allowances} for {ks.count('buy')} buys")

    # -- NEVER LIST BELOW WHAT THE CORES COST -------------------------------
    # The margin gate only proves the trade looked good at BUY time. A craft, a
    # compress and a shop reopen sit between that and the listing, and the
    # market can move underneath. The floor is a TOTAL because the price box
    # takes the price of the whole stack -- qty is entered first and the
    # panel's suggestion scales with it, which is why 30 Sets listed at
    # 22,222,050 and not at one Set's 740,735.
    with Game([row(1, "Epic Booster (Highest)")],
              core_price=680_000, crafted=100) as g:
        m.chaos_pass(verbose=False)
    check(g.cost_floors and all(f == 680_000 * 100 for f in g.cost_floors),
          f"the listing must carry a cost floor of what was paid x how many "
          f"are being listed (680,000 x 100 = 68,000,000), got {g.cost_floors}")

    # It tracks the price actually paid, not a constant.
    with Game([row(1, "Epic Booster (Highest)")],
              core_price=712_345, crafted=7) as g:
        m.chaos_pass(verbose=False)
    check(g.cost_floors and all(f == 712_345 * 7 for f in g.cost_floors),
          f"the floor follows the real purchase price and the real count, got "
          f"{g.cost_floors}")

    # -- the margin gate --------------------------------------------------
    # Set only 1,000 dearer than the Core: under the 20,000 floor.
    with Game([row(1, "Epic Booster (Highest)")],
              core_price=700_000, set_price=701_000) as g:
        ok = m.chaos_pass(verbose=False)
    check("buy" not in kinds(g.events),
          f"a margin under the floor must not buy, got {kinds(g.events)}")
    check(ok is True,
          "and a low margin is a clean pass, not a failure -- it must not "
          "spend the run's failure budget")

    # Exactly at the floor is NOT above it.
    with Game([row(1, "Epic Booster (Highest)")],
              core_price=700_000,
              set_price=700_000 + m.CHAOS_MARGIN_FLOOR) as g:
        m.chaos_pass(verbose=False)
    check("buy" not in kinds(g.events),
          f"a margin exactly AT the floor does not clear it, got "
          f"{kinds(g.events)}")

    # An UNREADABLE margin is not a measured one. Both end in "did not buy",
    # so the shelf cannot tell them apart -- which is exactly why this is
    # asserted on the report rather than on the absence of a purchase. The
    # difference is operational: a low margin is the market being unattractive
    # and needs no attention, while an unread one means the pass is blind and
    # will keep being blind until someone looks. Collapsing the two hides a
    # broken search behind a plausible "margin too low" line, and the shelf
    # quietly stays empty for the rest of the run.
    with Game([row(1, "Epic Booster (Highest)")], unreadable=True) as g:
        ok = m.chaos_pass(verbose=False)
    labels = [e[1] for e in g.events if e[0] == "record"]
    check("buy" not in kinds(g.events),
          f"an unread margin must never buy blind, got {kinds(g.events)}")
    check("chaos.margin_unread" in labels,
          f"it must be reported as UNREAD, not as a low margin; got {labels}")
    check("chaos.margin_low" not in labels,
          f"and must not be recorded as a measured low margin; got {labels}")
    check(ok is False,
          "and it returns False -- a blind pass is a fault the caller should "
          "see, unlike a genuinely low margin which is a clean no-op")

    # -- the pack trap ----------------------------------------------------
    # A Set listing of X 200 at 148,147,000 is 740,735 a unit -- a 60,735
    # margin. Compared whole it would look like 147,000,000 and buy anything.
    with Game([row(1, "Epic Booster (Highest)")],
              core_price=680_000, set_price=148_147_000, set_pack=200) as g:
        m.chaos_pass(verbose=False)
    check("buy" in kinds(g.events),
          "a bundled Set price is divided down before comparing")

    with Game([row(1, "Epic Booster (Highest)")],
              core_price=740_000, set_price=148_147_000, set_pack=200) as g:
        m.chaos_pass(verbose=False)
    check("buy" not in kinds(g.events),
          "and a bundle whose UNIT price is barely above the Core does not "
          "buy -- comparing the whole bundle would have")

    # -- nothing crafted: do not list --------------------------------------
    with Game([row(1, "Epic Booster (Highest)")], crafted=0) as g:
        m.chaos_pass(verbose=False)
    ks = kinds(g.events)
    check("list" not in ks,
          f"if nothing was crafted there is nothing to list, got {ks}")

    # The unmerged-stack scenario that used to live here is gone with the
    # verification it tested. Compression is guaranteed by the operator's rule,
    # so there is no "did it merge" state to be in -- and the checking was what
    # produced the failure: a glow-inflated slot count triggered a retry, and
    # the retry lifted the stack onto the cursor.

    # -- the switch --------------------------------------------------------
    m.CHAOS_ENABLED = False
    with Game([row(1, "Epic Booster (Highest)")]) as g:
        ok = m.chaos_pass(verbose=False)
    check(ok is True and not kinds(g.events),
          f"with --chaos off the pass does nothing at all, got {kinds(g.events)}")
finally:
    m.CHAOS_ENABLED = _saved_enabled

# -- the fold-in itself ----------------------------------------------------
# Pulled out of register_item so it can be reached without driving a whole
# registration. It was buried behind ~400 lines of screen reading, and a
# mutation that made the cost floor LOSE to a smaller catalogue floor survived
# the suite untouched -- the rule was never executed by any test.
floor, why = m.effective_floor(1_000_000, "the floor set for this item",
                               68_000_000)
check(floor == 68_000_000,
      f"the higher of the two must win; the cost floor is what stops a stack "
      f"being sold under what was paid for it. got {floor:,}")
check("cost" in why.lower(),
      f"and the reason must name the cost floor, not the catalogue one, "
      f"because the two fail for different causes. got {why!r}")

floor, why = m.effective_floor(105_000_000, "the floor set for this item",
                               68_000_000)
check(floor == 105_000_000,
      f"a LARGER catalogue floor still wins -- a VIP floor is absolute and a "
      f"cost floor must never lower it. got {floor:,}")
check("cost" not in why.lower(),
      f"and the reason follows the rule that bound, got {why!r}")

floor, why = m.effective_floor(105_000_000, "the floor set for this item", 0)
check(floor == 105_000_000 and "cost" not in why.lower(),
      "no cost floor supplied leaves the catalogue floor exactly as it was")

floor, _ = m.effective_floor(0, "", 0)
check(floor == 0, "and no floor at all stays no floor, rather than becoming one")

# Equal is not greater: the catalogue reason is kept, so a log never claims the
# cost floor bound when it merely tied.
_, why = m.effective_floor(5_000, "the floor set for this item", 5_000)
check("cost" not in why.lower(),
      f"a tie keeps the catalogue reason, got {why!r}")


# The seam between the two, which no behavioural test here can reach: this
# suite replaces register_item wholesale, so the line that actually consults
# the cost floor is never executed. Deleting it would leave every check above
# green while chaos listings silently lost their floor.
#
# So it is asserted against the source, the same way chaos_test pins the guards
# inside alt_click. Weak evidence on its own -- it proves the call is written,
# not that it works -- which is why the live log prints the bound floor and its
# reason ("what its Cores cost") on every chaos listing. That line is the real
# confirmation; this only stops the wiring being removed unnoticed.
import inspect  # noqa: E402

reg_src = inspect.getsource(m.register_item)
check("effective_floor(" in reg_src,
      "register_item must consult effective_floor -- without it the cost "
      "floor chaos_pass computes is accepted and then ignored")
check("cost_floor" in reg_src.split("def register_item")[-1][:2000],
      "and cost_floor must reach it as a parameter, not be shadowed")


# -- and the floor must BIND once handed over ------------------------------
# Passing a cost floor is worthless if the pricing then ignores it. The
# behaviour wanted is SUBSTITUTION, not refusal: an item priced at cost still
# sits on the board and can sell later, whereas refusing strands the stack in
# the work tab, where the tab guard turns it into a stopped run.
priced, why = m.choose_price(500_000, 0, None, absolute_floor=68_000_000)
check(priced >= 68_000_000,
      f"a market suggestion below the cost floor must be lifted TO the floor, "
      f"got {priced:,}")
check(bool(why), "and it must say why it did not take the market price")

priced, _ = m.choose_price(90_000_000, 0, None, absolute_floor=68_000_000)
check(priced == 90_000_000,
      f"but a suggestion ABOVE the floor is left alone -- the floor is a "
      f"minimum, not a target. got {priced:,}")

# -- CHECKED BEFORE EVERY ROW, not once a batch ----------------------------
# The operator's priority: relist row 3 -> check chaos -> resume row 3 -> check
# chaos -> row 4, and so on. Once at the top of the batch is the wrong shape:
# a batch runs ~600s, so a bundle that sells at t=200 sat unnoticed until the
# next cycle -- ten minutes of a dead shelf slot and uncollected money.
SET_ROW = row(2, SET_NAME)
SOLD_ROW = row(2, SET_NAME, action="receive")
OTHER = row(3, "Force Core (Ultimate)")

# A SOLD chaos row is positive evidence wherever it is seen.
check(m.chaos_attention_needed([OTHER, SOLD_ROW, SET_ROW]) != "",
      "a chaos row showing Receive must always demand attention -- it is "
      "money uncollected and a shelf slot doing nothing")
check(m.chaos_attention_needed([OTHER, SOLD_ROW], trust_count=False) != "",
      "and that holds even when the view is scrolled, because the sold row is "
      "right there in it")

# A full shelf is not attention-worthy.
check(m.chaos_attention_needed([OTHER, SET_ROW, row(9, SET_NAME)]) == "",
      "N live bundles and none sold is a shelf that needs nothing")

# THE DANGEROUS FALSE POSITIVE.
# bring_into_view walks the table to whatever row the batch is working on, so
# a view with no chaos rows in it usually means "not looking at them", not
# "the shelf is empty". Believing the count there would buy K Cores against a
# full shelf every time the batch touched a row past the first screen -- at
# K=100 and ~680,000 a Core, ~68,000,000 per false alarm.
check(m.chaos_attention_needed([OTHER], trust_count=False) == "",
      "a scrolled view showing no chaos rows must NOT read as an empty shelf")
check(m.chaos_attention_needed([OTHER], trust_count=True) != "",
      "but at the TOP of the table, absence really is absence and the shelf "
      "must be refilled")
check(m.chaos_attention_needed([], trust_count=False) == "",
      "an empty read is not evidence of anything")

# It must read nothing: this runs before every row, so a screen access here is
# multiplied by the batch length.
attn_src = inspect.getsource(m.chaos_attention_needed)
for forbidden in ("grab(", "await_rows(", "read_rows(", "find_words("):
    check(forbidden not in attn_src,
          f"chaos_attention_needed must not call {forbidden} -- it runs before "
          f"every row, and the caller has already read the table")

# And the hook must be wired into the row loop, re-reading after it fires.
loop_src = (inspect.getsource(m._relist_cycle)
                + inspect.getsource(m._relist_body)) \
    if hasattr(m, "_relist_cycle") else ""
rows_src = inspect.getsource(m.relist_rows)
check("chaos_attention_needed(" in rows_src,
      "relist_rows must consult it between rows -- that is the priority")
# A generous window, deliberately. This slice used to be 1400 characters and
# broke the moment a comment was added between the hook and its re-read -- the
# check is about what the hook DOES, not how tightly it is written, and a
# source-window assertion that fails on a comment is testing formatting.
# ANCHORED ON THE ESCALATION, NOT ON A CHARACTER COUNT.
#
# This was a fixed window after chaos_attention_needed( -- 1400 characters,
# then 2600 -- and it broke twice when comments and a retry cap were added
# between the check and the pass. A slice that fails because code moved is
# testing layout, not behaviour.
#
# The properties below are all about what happens AFTER the pass fires, so the
# slice starts at the pass itself and runs to the end of the function.
hook = rows_src.split("chaos_attention_needed(")[1]
_esc = hook.find("chaos_pass(")
check(_esc >= 0, "the hook must escalate to the full chaos pass")
hook_after = hook[_esc:] if _esc >= 0 else hook
check("chaos_pass(" in hook,
      "and escalate to the full pass when it fires")
check("ensure_work_tab_empty(" in hook_after,
      "and re-assert the work tab afterwards: chaos buys, crafts and lists on "
      "it, and the next row's cancel identifies its item by diffing that tab")
check("bring_into_view(" in hook or "await_rows(" in hook_after,
      "and RE-READ the table -- collecting and listing renumber it, so acting "
      "on the pre-chaos view would cancel whatever slid into that position")
check("trust_count=not scrolling" in rows_src,
      "and it must only believe the count when the view is the top of the "
      "table, never when bring_into_view has scrolled somewhere else")


# -- COMPRESSION IS NOT VERIFIED, DELIBERATELY -----------------------------
# The operator's rule: the Alt+click merges everything into (1,1), guaranteed,
# so the listing follows immediately.
#
# Reading it back was worse than useless. A selected slot glows, the glow
# bleeds into the slot below, and occupied_slots reports one more than is
# there. Measured 2026-08-09: ten crafted Sets merged into ONE stack on the
# first click, the count read 2, the caller retried -- and an Alt+click on a
# stack with nothing left to merge PICKS IT UP. The Chaos Core Set then rode
# the cursor to the NPC, where seven attempts to click Lady Yekaterina failed
# because the click was carrying an item. Every part of that chain started
# with verifying something that did not need verifying.
comp_src2 = inspect.getsource(m.compress_stack)
check("occupied_slots(" not in comp_src2,
      "compress_stack must not count slots -- the glow makes that count wrong, "
      "and acting on it lifts the stack onto the cursor")
check("alt_click(" in comp_src2, "it must still do the merge")
check("open_inventory(" in comp_src2,
      "and still open the panel it needs, since press_escape closed it")

# THE TAB, BEFORE THE SLOT. A slot number is meaningless without one.
#
# press_escape() closes the panel after the craft and reopening lands on TAB I,
# not the working tab. Measured 2026-08-09: (1,1) was the first slot of the
# GENERAL inventory, the Alt+click grabbed an unrelated item, put it on the
# cursor, and carried it to the NPC -- ten clicks on Lady Yekaterina failed
# because the cursor was holding someone's belongings.
check("select_inventory_tab(" in comp_src2,
      "compress_stack must select the tab before clicking a slot on it")
check(comp_src2.index("select_inventory_tab(") < comp_src2.index("alt_click("),
      "and BEFORE the click, not after it")
check("return False" in comp_src2.split("select_inventory_tab(")[1][:400],
      "and must refuse if it cannot reach that tab -- clicking a slot number "
      "on the wrong tab is how an unrelated item ends up on the cursor")
check("tab=CHAOS_WORK_TAB" in inspect.getsource(m.chaos_pass),
      "and the chaos pass must say WHICH tab, not leave it to whatever the "
      "panel happened to reopen on")

pass_src5 = inspect.getsource(m.chaos_pass)
# Generous slice: the cost-floor block sits between the compress and the
# register call, so a short window cut the register out and reported the
# listing as missing when it was simply further down.
# EVERYTHING after the compress, not a fixed 2,500 characters.
#
# The window was sized to the code as it stood. Adding the chaos cost-floor
# block between compress_stack and register_item pushed "register_item(" past
# 2,500 chars, so .index() raised ValueError, the suite died here, and 63 of
# its 133 checks never ran -- including three that were FAILING and are only
# printed in the end-of-file summary. A source window that has to be resized
# whenever the code grows is a tripwire on the test, not on the code.
after_compress = pass_src5.split("compress_stack(1, 1")[1]
check("occupied_slots(" not in after_compress,
      "and the caller must not re-count either -- it listed immediately, by "
      "instruction")
check("register_item(" in after_compress,
      "the listing follows the compress directly")

# THE SHOP MUST BE REOPENED BETWEEN THEM.
#
# The craft closes the Agent Shop. Without a reopen, register_item runs against
# the 3D WORLD: the shop-slot region shows snow and branches, whose texture has
# a standard deviation of 35.9 against a threshold of 20.0, so the slot reads
# as occupied and the run aborts claiming an item is in it. Measured live on
# 2026-08-09; there was no slot at all, and the message sent two people hunting
# a stranded item that never existed.
check("ensure_shop_ready(" in after_compress,
      "the Agent Shop must be reopened between the compress and the listing")
check(after_compress.index("ensure_shop_ready(")
      < after_compress.index("register_item("),
      "and before it, not after")
check("select_inventory_tab(CHAOS_WORK_TAB" in after_compress,
      "and the work tab reselected, since the bundle is on it")

# register_item must not be able to make that mistake readable as anything
# else: with no window there is no slot, and saying "the slot holds an item"
# is a diagnosis of the wrong problem.
reg_src2 = inspect.getsource(m.register_item)
check("trade_window_open()" in reg_src2,
      "register_item must check the Trade window is open before judging the "
      "slot -- the world is busier than an empty slot and reads as occupied")
check(reg_src2.index("trade_window_open()")
      < reg_src2.index('panel["loaded"]'),
      "and must check it BEFORE reading the panel, or the misleading message "
      "is still what comes out")

# -- --premium OPENS THE WINDOW, IT DOES NOT FINISH THE JOB ----------------
# The shop comes up on whichever tab it was last left on, and the chaos buy
# leaves it on PURCHASE. Opening the window is half the job; the Register-tab
# recovery is the other half.
#
# Measured 2026-08-09: the premium branch RETURNED as soon as the window was
# up, skipping that recovery. register_item then read the Purchase tab's
# category tree at the shop slot's coordinates, OCR'd "Craft Items" out of it
# (stdev 27.4 against a threshold of 20.0) and aborted with "the shop slot
# already holds an item". Frame run_38791.png.
otw = inspect.getsource(m.open_trade_window)
prem = otw[otw.index("PREMIUM_ENABLED and not trade_window_open()"):]
check("return open_shop_from_key" not in prem,
      "the premium branch must NOT return as soon as the window opens -- the "
      "tab recovery below it still has to run")
check("register_tab_open" in prem,
      "and the Register-tab recovery must still be reachable after it")

key_src = inspect.getsource(m.open_shop_from_key)
check("select_inventory_tab(PREMIUM_SHOP_KEY_TAB" in key_src,
      "the key lives on a specific tab and must be reached there, not clicked "
      "at a slot number on whatever tab happens to be showing")
check("right_click(" in key_src,
      "the key is opened with the other mouse button, like the craft card")
check(m.PREMIUM_ENABLED is False,
      "and it must be OFF by default -- the key only exists on a premium "
      "account, and right-clicking a slot that holds something else USES that "
      "item")


# -- A LOST RACE IS NOT THE END OF THE BUYING ------------------------------
# Another player taking the row between the search and the Buy click is
# ordinary: the game closes the dialog, reports "Item Sold", and spends
# nothing. Measured live on 2026-08-10 -- orders of 115 and 25 Cores went
# through and a single Core at 668,000 was sniped between them, which is what
# ruled out funds, inventory space and order size.
#
# Abandoning the target there left the shelf at 140 of 200 because a row of ONE
# Core was lost. The recovery is to click the favourite slot again and take ROW
# 1 of the fresh results -- never row 2. The sniped listing is simply gone from
# those results, so this is not retrying the same thing.
buy_src = inspect.getsource(m.chaos_pass)
_after_fail = buy_src[buy_src.index("if not bought:"):]
_after_fail = _after_fail[:_after_fail.index("got += items")]
check("continue" in _after_fail,
      "a failed order must start again rather than abandon the target")
# ROW 1, ALWAYS. The recovery re-runs the favourite search and takes offers[0];
# nothing in this loop may index past it, because the list is sorted Low to
# High and row 1 is the cheapest.
_loop = buy_src[buy_src.index("for order in range(1, CHAOS_BUY_ORDERS"):]
_loop = _loop[:_loop.index("got += items")]
check("offers[0]" in _loop and "offers[1]" not in _loop,
      "every order must take ROW 1 of a fresh search, never a later row")
# COUNTED AS "at least one, before offers[0]", not "exactly one".
#
# The rule being protected is that row 1 must come from a search that has JUST
# run -- not that the loop contains a single search call. A second call was
# added on 2026-08-10 to recover from the Trade window closing mid-order (the
# margin gate read eight offers, the next search found none because the window
# had gone, and a 180,975,459 Alz sale went unreplaced). That recovery
# re-searches before using offers[0], so it upholds the rule; an exact-count
# assertion failed it anyway, which is testing the shape of the code rather
# than what it guarantees.
check(_loop.count("run_favourite_search(CHAOS_CORE_SLOT") >= 1
      and _loop.index("run_favourite_search(CHAOS_CORE_SLOT") < _loop.index("offers[0]"),
      "and the favourite slot must be clicked again at the top of every "
      "order, so row 1 is row 1 of results that have just run")
check("CHAOS_BUY_LOST_LIMIT" in _after_fail,
      "but consecutive failures must stop it -- a market refusing repeatedly "
      "is saying something other than 'you were outbid'")
check(m.CHAOS_BUY_LOST_LIMIT >= 2,
      f"and the limit must allow at least one retry, got "
      f"{m.CHAOS_BUY_LOST_LIMIT}")
check("lost = 0" in buy_src,
      "the counter must reset on a success, or a scattering of losses across a "
      "long accumulation adds up to a false stop")

# The refusal must NAME the cause. "the dialog closed but no Alz was spent"
# reads like a fault in this script, and sent two people hunting for one.
check("Item Sold" in inspect.getsource(m.buy_offer),
      "the sold-out refusal must say what actually happened, not describe the "
      "symptom")


# -- CHAOS IS CONFINED TO THE BATCH'S ROWS ---------------------------------
# The operator's rule: "As the rows grow, we still only considered the
# boundary, and do not touch unspecified rows."
#
# chaos_pass used to count every Chaos Core Set in the visible TEN rows and
# list new ones into the first empty row anywhere. So "--chaos-rows 5" against
# a 1-4 batch created and managed rows 5, 6, 7 that the row loop would never
# reprice -- and bundles outside the range counted towards the target, so the
# shelf inside it could read as full while holding none at all.
IN, OUT = 2, 9          # inside the scope, and outside it
scoped = [row(IN, SET_NAME), row(OUT, SET_NAME), row(1, "Epic Booster (Highest)")]
check(len(m.chaos_rows_in(scoped, {1, 2, 3, 4})) == 1,
      "only bundles inside the scope are counted; one outside it belongs to "
      "no batch and must not satisfy the target")
check(len(m.chaos_rows_in(scoped, None)) == 2,
      "and with no scope -- a batch over all rows -- every bundle counts")

pass_src6 = inspect.getsource(m.chaos_pass)
check("scope" in inspect.signature(m.chaos_pass).parameters,
      "chaos_pass must take the batch's rows")
check("chaos_rows_in(" in pass_src6,
      "and count through the scoped helper, not chaos_shop_rows directly")

# It must also stop at the edge rather than spilling past it.
check("free_here" in pass_src6,
      "chaos must count the FREE rows inside the boundary and refill only that "
      "many -- listing outside it creates a row this batch never reprices")

rows_src3 = inspect.getsource(m.relist_rows)
check(rows_src3.count("scope=None if all_rows else list(rows)") >= 2,
      f"both chaos call sites must pass the batch's rows, got "
      f"{rows_src3.count('scope=None if all_rows else list(rows)')}")

# And an impossible target is refused at startup, not met by expanding.
main_src2 = inspect.getsource(m.main)
check("does not fit in the" in main_src2,
      "--chaos-rows larger than the relisted range must be refused at startup: "
      "before this it was 'met' by silently listing outside the boundary")


# -- BUYING REACHES K ACROSS ROWS ------------------------------------------
# Measured 2026-08-09 with K=200: row 1 held 95, the pass bought 95, crafted,
# and left the shelf short. A purchase can only take ROW 1 -- the table is
# sorted Low to High so row 1 is the cheapest -- but buying it out and
# searching again brings the next listing up, which is how the Set restock
# reaches its own target.
pass_src4 = inspect.getsource(m.chaos_pass)
check("CHAOS_BUY_ORDERS" in pass_src4,
      "the buy must be able to place several orders for one bundle")
# The REMAINDER rule is gone, deliberately: order_size is the whole row.
# Trimming to exactly what was still wanted meant every order had to compute a
# precise count, and getting that count wrong is what a flaky qty_max read
# does. What must remain true is that the loop STOPS at the minimum, so the
# overshoot is bounded by a single row's depth.
check("order_size = max(1, core.available)" in pass_src4,
      "each order takes the whole row -- K is a minimum, not a target")
check("if got >= CHAOS_BUY_QUANTITY" in pass_src4,
      "and the loop stops as soon as the minimum is reached, which is what "
      "bounds the overshoot to one row")
buy_loop = pass_src4.split("for order in range(1, CHAOS_BUY_ORDERS")[-1]
check("run_favourite_search(CHAOS_CORE_SLOT" in buy_loop,
      "and must re-search inside the loop -- buy_offer refuses a Buy that is "
      "not row 1 of a search that just ran, and buying out row 1 is what "
      "changes which listing IS row 1")
# THE MARGIN IS PRINTED ON EVERY ORDER, not only when it refuses. Each row
# down is dearer, so the margin differs per order -- a buy made without showing
# it is a buy at a price nobody saw.
check(buy_loop.count("Set/unit") >= 1,
      "each order must print its own Core/Set/margin line")
check("floor {CHAOS_MARGIN_FLOOR:,}" in buy_loop
      or "CHAOS_MARGIN_FLOOR:," in buy_loop,
      "and show the floor it is being judged against")
# An unreadable or absent margin must STOP, not wave the order through: the
# whole point of re-judging is that the deeper row was never priced by the gate.
check("chaos.margin_unreadable" in buy_loop,
      "a margin that will not compute must stop the buying, not be skipped")
check("chaos.margin_missing" in buy_loop,
      "and so must having no Set price to judge against at all")
check("CHAOS_MARGIN_FLOOR" in buy_loop,
      "the margin must be re-judged on each row bought: sorted Low to High "
      "means every row down is dearer, so a trade that cleared the floor on "
      "row 1 can stop clearing it two rows later")

# -- THE CRAFT IS COUNTED AFTER THE QUEUE, NOT DURING IT --------------------
# The counter was read two seconds after Request All and the drop called the
# craft. The queue consumes progressively, so that read caught only the first
# few: the window held 95, the read said 10, and "crafted 10" was reported
# while the queue was still working -- sizing the settle, the cost floor and
# the recorded lot to a tenth of the truth.
craft_src2 = inspect.getsource(m.craft_chaos_sets)
# Against the CLICKS, not the prose. The docstring names both steps, and
# matching that tested the comment rather than the code.
comp = craft_src2.index('say("  Complete All")')
held = craft_src2.index("craft_settle_seconds(")
after_read = craft_src2.rindex("after = craft_material_held()")
check(after_read > held,
      "the material must be re-read AFTER the settle wait, not during the queue")
check(after_read < comp,
      "and before Complete All, which is what clears the window")
# THE WORK TAB IS RESELECTED BEFORE COLLECTING.
#
# The crafted Sets land on whatever tab is showing when the craft COMPLETES,
# and the game moves the tab itself during Request All. Measured 2026-08-09
# frame by frame: tab 4 through the category and recipe clicks, tab 2 the
# instant Request All was pressed. The Sets landed on tab 2, the compress
# clicked an empty slot on tab 4, and the listing had nothing to load.
#
# So selecting the tab when the craft window opens is not enough -- by the time
# the output appears, that selection is stale.
_req = craft_src2.index('say("  Request All")')
_comp = craft_src2.index('say("  Complete All")')
_between = craft_src2[_req:_comp]
check("select_inventory_tab(CHAOS_WORK_TAB" in _between,
      "the work tab must be reselected between Request All and Complete All, "
      "because the game moves it during Request All and the output follows "
      "whatever tab is showing")

check("craft_settle_seconds(before)" in craft_src2,
      "the wait must be sized from what was HELD -- the most the queue can "
      "possibly craft -- not from a partial reading of what it has taken")

# -- COMPRESSING NEEDS THE PANEL, LIKE THE CRAFT KEY DID --------------------
# press_escape() closes the craft window and the Inventory panel with it, and
# the compress runs immediately after. Measured 2026-08-09: the compress
# refused, the listing was correctly refused after it, and 64 slots of crafted
# Sets sat in the work tab with the run dead.
comp_src = inspect.getsource(m.compress_stack)
check("open_inventory(" in comp_src,
      "compress_stack must OPEN the Inventory panel rather than refuse -- the "
      "same fix open_craft_window needed, which was not carried across")
check("return False" in comp_src,
      "and still refuse if it genuinely will not open")


# -- THE PANEL THAT WAS NEVER OPENED ---------------------------------------
# Measured live on 2026-08-09. chaos_pass calls leave_shop() to get the Agent
# Shop out of the way, leave_shop closes the Inventory panel, and the very next
# step reaches for the craft key inside that panel. It refused -- one step
# AFTER 100 Chaos Cores had been bought for 66,999,700 Alz, which were then
# left uncrafted and the run stopped.
#
# The constant for the hotkey had existed unused since the day it was added:
# every other path that needed the panel happened to run while it was already
# open, so nothing ever had to open it.
inv_src = inspect.getsource(m.open_inventory)
check("VK_I" in inv_src,
      "open_inventory must actually press the inventory key")
# Against the key PRESS, not the string "VK_I" -- the docstring names it first,
# so comparing raw indices tested the prose rather than the code.
# PRESSED FIRST, checked after -- the operator's instruction. The panel is
# opened straight after leave_shop, which closes it, so a pre-check could only
# ever confirm what was already known. I is a toggle, so if it WAS open the
# press shuts it; the wait below notices and toggles back, which costs a
# keypress instead of a screenshot on every call.
check(inv_src.index("_send(_key_event(VK_I")
      < inv_src.index("inventory_origin() is not None"),
      "the key must be pressed before anything is read")
check("toggling back" in inv_src,
      "and a press that CLOSED an already-open panel must be corrected, or the "
      "caller is left with no panel and no idea why")
check("record(" in inv_src,
      "a panel that will not open must be recorded -- it strands whatever the "
      "caller already paid for")

craft_open_src = inspect.getsource(m.open_craft_window)
check("open_inventory(" in craft_open_src,
      "open_craft_window must OPEN the panel rather than refuse: on the one "
      "path that matters, chaos_pass has just closed it via leave_shop, so "
      "refusing there means the Cores are already bought and cannot be crafted")
# The refusal must survive as the last resort, or a panel that genuinely will
# not open turns into clicking the craft-key coordinate over the game world.
check("return False" in craft_open_src,
      "and must still refuse when it cannot be opened at all")

# The ordering that caused it: money moves before the craft window is proven
# reachable. Worth stating so the risk is visible even now it is handled.
pass_src = inspect.getsource(m.chaos_pass)
buy_at = pass_src.index("buy_offer(")
craft_at = pass_src.index("open_craft_window(")
check(buy_at < craft_at,
      "chaos buys before it opens the craft window -- so every failure between "
      "those two points strands paid-for Cores. That is why open_craft_window "
      "must recover rather than refuse.")


# -- the craft settle scales with the queue --------------------------------
# The queue is per-item: Request All queues one craft per available material,
# so a wait measured against ten Cores under-waits a hundred by an order of
# magnitude. Clicking Complete All early collects a fraction and leaves the
# rest queued, which reads downstream as "the craft made 12 Sets" with no error
# anywhere -- the paid-for material is simply gone from the count.
settle_for = m.craft_settle_seconds

# The operator's rule, stated as examples rather than as a formula, because a
# formula can be reimplemented wrongly and still look like the intent:
#     10s per 100, ROUNDED UP. 100 -> 10s, 197 -> 20s, 201 -> 30s.
for made, want in ((1, 30), (99, 30), (100, 30), (101, 60), (197, 60),
                   (200, 60), (201, 90), (300, 90), (301, 120)):
    check(settle_for(made) == want,
          f"crafting {made} must wait {want}s, got {settle_for(made):g}s")

check(settle_for(0) == 30,
      f"and a queue of nothing still waits one block rather than none, got "
      f"{settle_for(0):g}s")
check(settle_for(-5) == 30,
      "a negative count is a failed read, not a reason to skip the wait")

# Rounding UP is the whole point: rounding down means clicking Complete All
# while the tail of the queue is still running, which leaves paid-for material
# behind and reports it as "the craft only made N", with no error anywhere.
check(settle_for(101) > settle_for(100),
      "one item past a block must cost a whole extra block, not nothing")

# Monotonic, so a larger queue can never wait less than a smaller one.
waits = [settle_for(n) for n in range(0, 1200, 37)]
check(all(b >= a for a, b in zip(waits, waits[1:])),
      f"the wait must never decrease as the queue grows, got {waits}")

check(settle_for(10**6) == m.CRAFT_SETTLE_MAX,
      "and it must be capped, so a stuck queue cannot hang a run outright")
check(m.CRAFT_SETTLE_MAX > settle_for(m.CHAOS_BUY_QUANTITY),
      f"the cap must not bite at K={m.CHAOS_BUY_QUANTITY}, or the scaling is "
      f"dead code: cap {m.CRAFT_SETTLE_MAX:g}s vs "
      f"{settle_for(m.CHAOS_BUY_QUANTITY):g}s needed for K")

# The budget must actually be spent. The first version polled the Required
# Material counter for a steady reading -- but Request All consumes the whole
# queue at once, so that counter is already 0 when the poll starts, goes steady
# immediately, and broke out after ~4s however large the queue was. The budget
# was computed, printed, and then ignored.
craft_src = inspect.getsource(m.craft_chaos_sets)
check("time.sleep(settle)" in craft_src,
      "the computed settle must actually be waited out")
check("craft_material_held()" not in craft_src.split("Request All")[-1]
      .split("Complete All")[0],
      "and the material counter must NOT be used as the completion signal "
      "between Request All and Complete All -- it reports material still held, "
      "which Request All has already taken to zero")

check(m.CHAOS_ENABLED is _saved_enabled, "the switch was restored")
# -- N AND K ARE ARGUMENTS ------------------------------------------------
# Defaults, and reachable from the command line so they can be changed without
# editing the file.
main_src = inspect.getsource(m.main)
check("--chaos-rows" in main_src, "N must be settable as --chaos-rows")
check("--chaos-quantity" in main_src, "K must be settable as --chaos-quantity")
check('globals()["CHAOS_ROWS"]' in main_src
      and 'globals()["CHAOS_BUY_QUANTITY"]' in main_src,
      "and both must actually be applied, not just parsed")

# REFUSED, not clamped. K is the size of every purchase the pass makes, so a
# silently corrected value is one the operator did not choose.
for bad in ("--chaos-rows must be at least 1",
            "--chaos-quantity must be at least 1",
            "is more rows than the shop"):
    check(bad in main_src, f"a bad value must be refused with: {bad!r}")

# THE WAR ALLOWANCE MUST FOLLOW K.
# A chaos row cannot be interrupted -- stopping between the buy and the listing
# strands paid-for Cores -- so the allowance has to cover the whole row. The
# craft alone scales with K, so a FIXED allowance silently stops covering it:
# at 30s per 100, K=1000 crafts for 300s, which is the entire old constant.
_saved_k = m.CHAOS_BUY_QUANTITY
try:
    # FLAT BY INSTRUCTION, AND THE COST IS NAMED.
    #
    # This block asserted the allowance grew with K, because the craft alone
    # does: at 30s per 100, K=1000 crafts for 300s. The operator's flat 60s
    # margin means it no longer does, so what is pinned is the flatness -- and
    # the gap is reported rather than hidden, because it is exactly how long a
    # chaos row can overrun the war window by.
    for k in (100, 250, 1000, 5000):
        m.CHAOS_BUY_QUANTITY = k
        check(m.chaos_row_allowance() == m.WAR_STOP_MARGIN,
              f"at K={k} the allowance is the flat margin, got "
              f"{m.chaos_row_allowance():.0f}s")
    m.CHAOS_BUY_QUANTITY = 1000
    _short_by = m.craft_settle_seconds(1000) - m.chaos_row_allowance()
    check(_short_by > 0,
          f"at K=1000 the craft alone ({m.craft_settle_seconds(1000):.0f}s) "
          f"exceeds the allowance by {_short_by:.0f}s -- recorded so the "
          f"override's cost is visible, not asserted away")
finally:
    m.CHAOS_BUY_QUANTITY = _saved_k

check("chaos_row_allowance()" in inspect.getsource(m.chaos_pass),
      "the chaos buy guard must use the DERIVED allowance, not the constant "
      "-- otherwise raising K silently outruns it")

check(m.CHAOS_ROWS == 2, f"N defaults to 2, got {m.CHAOS_ROWS}")
check(m.CHAOS_BUY_QUANTITY == 100, f"K is 100, got {m.CHAOS_BUY_QUANTITY}")
# Lowered to 10,000 at the operator's instruction on 2026-08-10, from 20,000.
# Pinned as a literal so a retune is a deliberate edit here as well as there.
check(m.CHAOS_MARGIN_FLOOR == 10_000,
      f"the margin floor is 10,000, got {m.CHAOS_MARGIN_FLOOR:,}")


print(f"chaos_pass_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
