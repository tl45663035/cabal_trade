import os
import pathlib
import sys
import tempfile

_DB = pathlib.Path(tempfile.gettempdir()) / f"chaos_pass_test_{os.getpid()}.db"
if _DB.exists():
    _DB.unlink()
os.environ["CABAL_SALES_DB"] = str(_DB)

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m

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

    def __init__(self, listings, core_price=680_000, set_price=740_735,
                 set_pack=1, crafted=200, unreadable=False, held_slots=1):
        self.listings = list(listings)
        self.unreadable = unreadable
        self.held_slots = held_slots
        self.core_price = core_price
        self.set_price = set_price
        self.set_pack = set_pack
        self.crafted = crafted
        self.events = []
        self.saved = {}
        self.tab = "register"
        self.sort_ok = True
        self.sort_fails_after = None
        self.purchase_tabs = 0
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
        m.avoid_warlag = self._warlag
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
        self.listings = [r for r in self.listings if r.index != index]

        if row is not None and getattr(row, "action", None) == "receive":
            if m.is_chaos_set(row.name):
                m.clear_cheapest_chaos_lot()
            return m.SOLD_OUT
        return m.RELISTED

    def _warlag(self, allowance=0.0, verbose=True, dry_run=False):
        self.events.append(("warlag", allowance))
        return 0.0

    def _open_purchase(self, timeout=10.0, verbose=True):
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
            self.events.append(("search_refused", slot))
            return []
        self.events.append(("search", slot))
        if self.unreadable:
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
    full = [row(1, "Epic Booster (Highest)"),
            row(2, SET_NAME), row(3, SET_NAME)]
    with Game(full) as g:
        ok = m.chaos_pass(verbose=False)
    check(ok is True, f"a full shelf is a clean pass, got {ok!r}")
    check("buy" not in kinds(g.events),
          f"and nothing is bought, got {kinds(g.events)}")

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

    conn = m.sales_db()
    conn.execute("DELETE FROM chaos_lots")
    conn.commit()
    conn.close()
    m.note_chaos_lot(680_000, 22_222_050, 30)
    check(len(m.chaos_lots()) == 1, "the lot was seeded")

    sold_row = [row(1, "Epic Booster (Highest)"),
                row(2, SET_NAME, action="receive"), row(3, SET_NAME)]
    with Game(sold_row) as g:
        m.chaos_pass(verbose=False)
    check(not any(l[2] == 22_222_050 for l in m.chaos_lots()),
          f"collecting a sold chaos row must clear the cost floor held for it; "
          f"got {m.chaos_lots()}")

    with Game([row(1, "Epic Booster (Highest)")]) as g:
        m.chaos_pass(verbose=False)
    ks = kinds(g.events)
    for earlier, later in (("buy", "craft"), ("craft", "compress"),
                           ("compress", "list")):
        check(earlier in ks and later in ks and ks.index(earlier) < ks.index(later),
              f"{earlier} must precede {later}; got {ks}")
    bought = [e for e in g.events if e[0] == "buy"]
    check(bought, f"it buys Cores at all, got {g.events}")
    check(sum(qty for _, qty in bought) >= m.CHAOS_BUY_QUANTITY,
          f"buying continues until the K={m.CHAOS_BUY_QUANTITY} MINIMUM is "
          f"reached; got {bought}")
    check(bought and bought[0][1] == 500,
          f"the first order takes the whole 500-deep row, not the "
          f"{m.CHAOS_BUY_QUANTITY} still needed; got {bought}")

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

    with Game([row(1, "Epic Booster (Highest)")]) as g:
        g.sort_ok = False
        ok = m.chaos_pass(verbose=False)
    ks = kinds(g.events)
    check("buy" not in ks,
          f"an unconfirmable Low-to-High sort must stop the buy, not proceed "
          f"and take row 1 of an unknown ordering; got {ks}")

    with Game([row(1, "Epic Booster (Highest)")]) as g:
        g.sort_fails_after = 1
        ok = m.chaos_pass(verbose=False)
    ks = kinds(g.events)
    check("buy" not in ks,
          f"once the Low-to-High sort cannot be re-confirmed, the buy must "
          f"STOP -- buy_offer takes row 1, and row 1 of a High-to-Low list is "
          f"the dearest offer on the board. got {ks}")
    check("chaos.sort_unconfirmed" in [e[1] for e in g.events
                                       if e[0] == "record"],
          "and it is recorded, so an unsorted market is visible afterwards")

    with Game([row(1, "Epic Booster (Highest)")]) as g:
        m.chaos_pass(verbose=False)
    ks = kinds(g.events)
    check("warlag" in ks,
          f"chaos must check the war lag like every other long action; got {ks}")
    check(ks.index("warlag") < ks.index("buy"),
          f"and it must check BEFORE buying -- waiting afterwards means the "
          f"Cores are already paid for and stranded. got {ks}")
    allowances = [e[1] for e in g.events if e[0] == "warlag"]
    check(allowances and all(a == m.chaos_row_allowance() for a in allowances),
          f"it must ask for the derived chaos allowance "
          f"({m.chaos_row_allowance():.0f}s), got {allowances}")
    check(m.chaos_row_allowance() == m.WAR_STOP_MARGIN,
          f"the chaos allowance is the operator's flat margin "
          f"({m.WAR_STOP_MARGIN:g}s), got {m.chaos_row_allowance():.0f}s")
    check(m.WAR_ROW_ALLOWANCE == m.WAR_STOP_MARGIN
          and m.WAR_RESTOCK_ALLOWANCE == m.WAR_STOP_MARGIN,
          "and every other allowance uses the same margin, so one knob moves "
          "them together")
    check(len([e for e in g.events if e[0] == "warlag"]) == ks.count("buy"),
          f"once per row started, got {allowances} for {ks.count('buy')} buys")

    with Game([row(1, "Epic Booster (Highest)")],
              core_price=680_000, crafted=100) as g:
        m.chaos_pass(verbose=False)
    check(g.cost_floors and all(f == 680_000 * 100 for f in g.cost_floors),
          f"the listing must carry a cost floor of what was paid x how many "
          f"are being listed (680,000 x 100 = 68,000,000), got {g.cost_floors}")

    with Game([row(1, "Epic Booster (Highest)")],
              core_price=712_345, crafted=7) as g:
        m.chaos_pass(verbose=False)
    check(g.cost_floors and all(f == 712_345 * 7 for f in g.cost_floors),
          f"the floor follows the real purchase price and the real count, got "
          f"{g.cost_floors}")

    with Game([row(1, "Epic Booster (Highest)")],
              core_price=700_000, set_price=701_000) as g:
        ok = m.chaos_pass(verbose=False)
    check("buy" not in kinds(g.events),
          f"a margin under the floor must not buy, got {kinds(g.events)}")
    check(ok is True,
          "and a low margin is a clean pass, not a failure -- it must not "
          "spend the run's failure budget")

    with Game([row(1, "Epic Booster (Highest)")],
              core_price=700_000,
              set_price=700_000 + m.CHAOS_MARGIN_FLOOR) as g:
        m.chaos_pass(verbose=False)
    check("buy" not in kinds(g.events),
          f"a margin exactly AT the floor does not clear it, got "
          f"{kinds(g.events)}")

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

    with Game([row(1, "Epic Booster (Highest)")], crafted=0) as g:
        m.chaos_pass(verbose=False)
    ks = kinds(g.events)
    check("list" not in ks,
          f"if nothing was crafted there is nothing to list, got {ks}")


    m.CHAOS_ENABLED = False
    with Game([row(1, "Epic Booster (Highest)")]) as g:
        ok = m.chaos_pass(verbose=False)
    check(ok is True and not kinds(g.events),
          f"with --chaos off the pass does nothing at all, got {kinds(g.events)}")
finally:
    m.CHAOS_ENABLED = _saved_enabled

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

_, why = m.effective_floor(5_000, "the floor set for this item", 5_000)
check("cost" not in why.lower(),
      f"a tie keeps the catalogue reason, got {why!r}")


import inspect

reg_src = inspect.getsource(m.register_item)
check("effective_floor(" in reg_src,
      "register_item must consult effective_floor -- without it the cost "
      "floor chaos_pass computes is accepted and then ignored")
check("cost_floor" in reg_src.split("def register_item")[-1][:2000],
      "and cost_floor must reach it as a parameter, not be shadowed")


priced, why = m.choose_price(500_000, 0, None, absolute_floor=68_000_000)
check(priced >= 68_000_000,
      f"a market suggestion below the cost floor must be lifted TO the floor, "
      f"got {priced:,}")
check(bool(why), "and it must say why it did not take the market price")

priced, _ = m.choose_price(90_000_000, 0, None, absolute_floor=68_000_000)
check(priced == 90_000_000,
      f"but a suggestion ABOVE the floor is left alone -- the floor is a "
      f"minimum, not a target. got {priced:,}")

SET_ROW = row(2, SET_NAME)
SOLD_ROW = row(2, SET_NAME, action="receive")
OTHER = row(3, "Force Core (Ultimate)")

check(m.chaos_attention_needed([OTHER, SOLD_ROW, SET_ROW]) != "",
      "a chaos row showing Receive must always demand attention -- it is "
      "money uncollected and a shelf slot doing nothing")
check(m.chaos_attention_needed([OTHER, SOLD_ROW], trust_count=False) != "",
      "and that holds even when the view is scrolled, because the sold row is "
      "right there in it")

check(m.chaos_attention_needed([OTHER, SET_ROW, row(9, SET_NAME)]) == "",
      "N live bundles and none sold is a shelf that needs nothing")

check(m.chaos_attention_needed([OTHER], trust_count=False) == "",
      "a scrolled view showing no chaos rows must NOT read as an empty shelf")
check(m.chaos_attention_needed([OTHER], trust_count=True) != "",
      "but at the TOP of the table, absence really is absence and the shelf "
      "must be refilled")
check(m.chaos_attention_needed([], trust_count=False) == "",
      "an empty read is not evidence of anything")

attn_src = inspect.getsource(m.chaos_attention_needed)
for forbidden in ("grab(", "await_rows(", "read_rows(", "find_words("):
    check(forbidden not in attn_src,
          f"chaos_attention_needed must not call {forbidden} -- it runs before "
          f"every row, and the caller has already read the table")

loop_src = inspect.getsource(m._relist_cycle) \
    if hasattr(m, "_relist_cycle") else ""
rows_src = inspect.getsource(m.relist_rows)
check("chaos_attention_needed(" in rows_src,
      "relist_rows must consult it between rows -- that is the priority")
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


comp_src2 = inspect.getsource(m.compress_stack)
check("occupied_slots(" not in comp_src2,
      "compress_stack must not count slots -- the glow makes that count wrong, "
      "and acting on it lifts the stack onto the cursor")
check("alt_click(" in comp_src2, "it must still do the merge")
check("open_inventory(" in comp_src2,
      "and still open the panel it needs, since press_escape closed it")

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
after_compress = pass_src5.split("compress_stack(1, 1")[1]
check("occupied_slots(" not in after_compress,
      "and the caller must not re-count either -- it listed immediately, by "
      "instruction")
check("register_item(" in after_compress,
      "the listing follows the compress directly")

check("ensure_shop_ready(" in after_compress,
      "the Agent Shop must be reopened between the compress and the listing")
check(after_compress.index("ensure_shop_ready(")
      < after_compress.index("register_item("),
      "and before it, not after")
check("select_inventory_tab(CHAOS_WORK_TAB" in after_compress,
      "and the work tab reselected, since the bundle is on it")

reg_src2 = inspect.getsource(m.register_item)
check("trade_window_open()" in reg_src2,
      "register_item must check the Trade window is open before judging the "
      "slot -- the world is busier than an empty slot and reads as occupied")
check(reg_src2.index("trade_window_open()")
      < reg_src2.index('panel["loaded"]'),
      "and must check it BEFORE reading the panel, or the misleading message "
      "is still what comes out")

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


buy_src = inspect.getsource(m.chaos_pass)
_after_fail = buy_src[buy_src.index("if not bought:"):]
_after_fail = _after_fail[:_after_fail.index("got += items")]
check("continue" in _after_fail,
      "a failed order must start again rather than abandon the target")
_loop = buy_src[buy_src.index("for order in range(1, CHAOS_BUY_ORDERS"):]
_loop = _loop[:_loop.index("got += items")]
check("offers[0]" in _loop and "offers[1]" not in _loop,
      "every order must take ROW 1 of a fresh search, never a later row")
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

check("Item Sold" in inspect.getsource(m.buy_offer),
      "the sold-out refusal must say what actually happened, not describe the "
      "symptom")


IN, OUT = 2, 9
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

check("free_here" in pass_src6,
      "chaos must count the FREE rows inside the boundary and refill only that "
      "many -- listing outside it creates a row this batch never reprices")

rows_src3 = inspect.getsource(m.relist_rows)
check(rows_src3.count("scope=None if all_rows else list(rows)") >= 2,
      f"both chaos call sites must pass the batch's rows, got "
      f"{rows_src3.count('scope=None if all_rows else list(rows)')}")

main_src2 = inspect.getsource(m.main)
check("does not fit in the" in main_src2,
      "--chaos-rows larger than the relisted range must be refused at startup: "
      "before this it was 'met' by silently listing outside the boundary")


pass_src4 = inspect.getsource(m.chaos_pass)
check("CHAOS_BUY_ORDERS" in pass_src4,
      "the buy must be able to place several orders for one bundle")
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
check(buy_loop.count("Set/unit") >= 1,
      "each order must print its own Core/Set/margin line")
check("floor {CHAOS_MARGIN_FLOOR:,}" in buy_loop
      or "CHAOS_MARGIN_FLOOR:," in buy_loop,
      "and show the floor it is being judged against")
check("chaos.margin_unreadable" in buy_loop,
      "a margin that will not compute must stop the buying, not be skipped")
check("chaos.margin_missing" in buy_loop,
      "and so must having no Set price to judge against at all")
check("CHAOS_MARGIN_FLOOR" in buy_loop,
      "the margin must be re-judged on each row bought: sorted Low to High "
      "means every row down is dearer, so a trade that cleared the floor on "
      "row 1 can stop clearing it two rows later")

craft_src2 = inspect.getsource(m.craft_chaos_sets)
comp = craft_src2.index('say("  Complete All")')
held = craft_src2.index("craft_settle_seconds(")
after_read = craft_src2.rindex("after = craft_material_held()")
check(after_read > held,
      "the material must be re-read AFTER the settle wait, not during the queue")
check(after_read < comp,
      "and before Complete All, which is what clears the window")
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

comp_src = inspect.getsource(m.compress_stack)
check("open_inventory(" in comp_src,
      "compress_stack must OPEN the Inventory panel rather than refuse -- the "
      "same fix open_craft_window needed, which was not carried across")
check("return False" in comp_src,
      "and still refuse if it genuinely will not open")


inv_src = inspect.getsource(m.open_inventory)
check("VK_I" in inv_src,
      "open_inventory must actually press the inventory key")
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
check("return False" in craft_open_src,
      "and must still refuse when it cannot be opened at all")

pass_src = inspect.getsource(m.chaos_pass)
buy_at = pass_src.index("buy_offer(")
craft_at = pass_src.index("open_craft_window(")
check(buy_at < craft_at,
      "chaos buys before it opens the craft window -- so every failure between "
      "those two points strands paid-for Cores. That is why open_craft_window "
      "must recover rather than refuse.")


settle_for = m.craft_settle_seconds

for made, want in ((1, 30), (99, 30), (100, 30), (101, 60), (197, 60),
                   (200, 60), (201, 90), (300, 90), (301, 120)):
    check(settle_for(made) == want,
          f"crafting {made} must wait {want}s, got {settle_for(made):g}s")

check(settle_for(0) == 30,
      f"and a queue of nothing still waits one block rather than none, got "
      f"{settle_for(0):g}s")
check(settle_for(-5) == 30,
      "a negative count is a failed read, not a reason to skip the wait")

check(settle_for(101) > settle_for(100),
      "one item past a block must cost a whole extra block, not nothing")

waits = [settle_for(n) for n in range(0, 1200, 37)]
check(all(b >= a for a, b in zip(waits, waits[1:])),
      f"the wait must never decrease as the queue grows, got {waits}")

check(settle_for(10**6) == m.CRAFT_SETTLE_MAX,
      "and it must be capped, so a stuck queue cannot hang a run outright")
check(m.CRAFT_SETTLE_MAX > settle_for(m.CHAOS_BUY_QUANTITY),
      f"the cap must not bite at K={m.CHAOS_BUY_QUANTITY}, or the scaling is "
      f"dead code: cap {m.CRAFT_SETTLE_MAX:g}s vs "
      f"{settle_for(m.CHAOS_BUY_QUANTITY):g}s needed for K")

craft_src = inspect.getsource(m.craft_chaos_sets)
check("time.sleep(settle)" in craft_src,
      "the computed settle must actually be waited out")
check("craft_material_held()" not in craft_src.split("Request All")[-1]
      .split("Complete All")[0],
      "and the material counter must NOT be used as the completion signal "
      "between Request All and Complete All -- it reports material still held, "
      "which Request All has already taken to zero")

check(m.CHAOS_ENABLED is _saved_enabled, "the switch was restored")
main_src = inspect.getsource(m.main)
check("--chaos-rows" in main_src, "N must be settable as --chaos-rows")
check("--chaos-quantity" in main_src, "K must be settable as --chaos-quantity")
check('globals()["CHAOS_ROWS"]' in main_src
      and 'globals()["CHAOS_BUY_QUANTITY"]' in main_src,
      "and both must actually be applied, not just parsed")

for bad in ("--chaos-rows must be at least 1",
            "--chaos-quantity must be at least 1",
            "is more rows than the shop"):
    check(bad in main_src, f"a bad value must be refused with: {bad!r}")

_saved_k = m.CHAOS_BUY_QUANTITY
try:
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
check(m.CHAOS_MARGIN_FLOOR == 10_000,
      f"the margin floor is 10,000, got {m.CHAOS_MARGIN_FLOOR:,}")


print(f"chaos_pass_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
