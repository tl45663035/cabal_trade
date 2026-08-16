"""The --buy restock pipeline: sold out -> buy Sets -> convert -> list.

Three stages that each spend something, chained. The tests here are mostly
about the CHAIN rather than the stages: each stage has its own suite, and what
is new -- and what can go expensively wrong -- is the order they run in and the
conditions under which the next one starts.

Two properties matter more than the rest:

  * nothing converts that was not bought, and nothing lists that was not
    converted. A stage that runs on an empty result is a stage acting on
    whatever the previous run left behind.

  * convert and list ALTERNATE. Cores do not stack, so 250 of them occupy 250
    inventory slots -- four tabs' worth -- and converting a 250-Set purchase in
    one go would fill the inventory and stall. Listing is what hands the slots
    back, so it has to happen between conversions rather than after all of
    them. A test that only checked "everything got converted and everything got
    listed" would pass on the arrangement that deadlocks.

And one that is easy to lose: --buy spends real money, so the pipeline must be
unreachable unless it is explicitly switched on.
"""
import sys
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

import inspect as _i  # noqa: E402
import trade as m  # noqa: E402

# NOTHING in this suite may touch the game. Every stage is stubbed, but a stub
# list is only as complete as the last person to add a call -- and when
# restock_core grew a shop_rows_used() call, the un-updated harness fell
# through to the real one, which enumerates the live shop by SCROLLING it. The
# suite hung for two minutes driving a real game window.
#
# NO_INPUT is checked inside the input primitives themselves, so it cannot be
# forgotten by a new code path the way a stub can.
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


class Row:
    """A shop table row, as far as the counters care."""

    def __init__(self, name):
        self.name = name


# ==========================================================================
section("which items the pipeline looks after")
# ==========================================================================

SLOTS = m.managed_core_slots()
check(SLOTS, "there is at least one managed Core")

# Stated literally rather than derived from the function under test. Deriving
# both sides from managed_core_slots() would pass no matter what it returned --
# including the Set slots, which would make the pipeline buy Sets to list Sets.
# Upgrade Core(Highest) removed 2026-08-09 at the operator's request, along
# with its slots 3/4. The remaining slot NUMBERS were deliberately left as
# they are -- they index the game's favourite bar -- so this list is shorter
# but the others are unmoved.
EXPECTED_MANAGED = sorted([
    "Force Core(High)", "Force Core(Highest)", "Force Core (Ultimate)",
    "Upgrade Core (Ultimate)",
])
check(sorted(m.FAVOURITE_SLOTS[s] for s in SLOTS) == EXPECTED_MANAGED,
      f"the managed Cores are exactly {EXPECTED_MANAGED}, got "
      f"{sorted(m.FAVOURITE_SLOTS[s] for s in SLOTS)}")
for slot in SLOTS:
    name = m.FAVOURITE_SLOTS[slot]
    check("set" not in m._floor_key(m.item_name(name)),
          f"slot {slot} ({name}) is a loose Core, not a Set")
    check(m.favourite_set_slot(slot) is not None,
          f"slot {slot} ({name}) has a paired Set slot")
    check(m.convert_cell_for(name) is not None,
          f"slot {slot} ({name}) resolves to a SET->CORE cell, so the "
          "pipeline can actually convert it")

# The Set slots are never managed: buying a Set is the MEANS, the Core is the
# product. Managing a Set would buy Sets in order to list Sets.
for slot, name in m.FAVOURITE_SLOTS.items():
    if "set" in m._floor_key(m.item_name(name)):
        check(slot not in SLOTS, f"slot {slot} ({name}) is a Set and is NOT managed")


# ==========================================================================
section("counting rows, where High and Highest must not blur")
# ==========================================================================

HIGH = m.favourite_for("Force Core(High)")
HIGHEST = m.favourite_for("Force Core(Highest)")
check(HIGH is not None and HIGHEST is not None, "both grades have a slot")

counts = m.core_row_counts([Row("Force Core(High)")] * 3
                           + [Row("Force Core(Highest)")]
                           + [Row("Siena's Unbinding Stone"), Row(""), Row("   ")])
check(counts[HIGH] == 3, f"three High rows counted, got {counts[HIGH]}")
check(counts[HIGHEST] == 1, f"one Highest row counted, got {counts[HIGHEST]}")
check(sum(counts.values()) == 4,
      f"unrelated and empty rows are not counted, got {sum(counts.values())}")

# The trap, stated directly. Counting Highest as High would report High as
# stocked when it has sold out, and the restock would never fire.
only_highest = m.core_row_counts([Row("Force Core(Highest)")] * 5)
check(only_highest[HIGH] == 0,
      "five Highest rows leave High at zero, not five")
check(HIGH in m.slots_needing_restock([Row("Force Core(Highest)")] * 5),
      "so a shop holding only Highest reads High as unlisted")

# A pack marker on the row must not make a stocked item read as sold out.
# Equality is what keeps the grades apart, but it is unforgiving of anything
# else on the line -- and a name that fails to match reads as SOLD OUT, which
# spends money restocking something that never ran out.
packed = m.core_row_counts([Row("Force Core(High) X 30"),
                            Row("Force Core(High) X 1,250"),
                            Row("Force Core(Highest) X 4")])
check(packed[HIGH] == 2, f"pack markers do not hide a High row, got {packed[HIGH]}")
check(packed[HIGHEST] == 1,
      f"nor a Highest row, got {packed[HIGHEST]}")
check(HIGH not in m.slots_needing_restock(
          [Row("Force Core(High) X 30")] * 2),
      "a packed row still counts as stock, so no needless restock")

# No row may ever count toward two slots at once. With equality that cannot
# happen; a looser comparison would double-count Highest as High as well, and
# this is what makes that visible rather than dependent on iteration order.
for probe in ["Force Core(High)", "Force Core(Highest)", "Force Core (Ultimate)",
              "Upgrade Core(Highest)", "Upgrade Core (Ultimate)",
              "Force Core(High) X 30"]:
    hit = m.core_row_counts([Row(probe)])
    check(sum(hit.values()) <= 1,
          f"{probe!r} counts toward at most one slot, got "
          f"{ {k: v for k, v in hit.items() if v} }")

# TWO rows each. One row is now a restock trigger, so a fixture giving every
# Core a single row would make "nothing needs restocking" impossible to state.
EVERY = [Row(m.FAVOURITE_SLOTS[s]) for s in SLOTS] * 2
check(m.slots_needing_restock(EVERY) == [],
      "a shop holding two rows of every Core needs no restock")
check(sorted(m.slots_needing_restock([])) == sorted(SLOTS),
      "an empty shop wants every Core restocked")

# A single remaining row IS a trigger: the rule is "at or below 1".
#
# It used to be "below 1" -- the last unit had to sell before anything was
# bought. Force Core(High) sat on one row of 7 units at 15.6% margin, the best
# in the shop, while chaos at 2.1% took 73% of the capital.
one_left = [Row(m.FAVOURITE_SLOTS[SLOTS[0]])]
check(SLOTS[0] in m.slots_needing_restock(one_left),
      "one row left is restocked now, not after it empties")
two_left = [Row(m.FAVOURITE_SLOTS[SLOTS[0]])] * 2
check(SLOTS[0] not in m.slots_needing_restock(two_left),
      "...but two rows is stocked, so the threshold cannot creep upward")

# -- absolute, and taken over the WHOLE shop -------------------------------
# The trigger is "this Core has no row anywhere", not "it had one and lost it".
# A transition could only ever restock something already seen, so a Core just
# switched on in ENABLE_BUYING would sit unlisted forever -- which is exactly
# the bootstrap case: enabled, never listed, wants stocking.
never_listed = [Row("Epic Booster (Highest)"), Row("Yekaterina VIP Membership")]
check(sorted(m.slots_needing_restock(never_listed)) == sorted(SLOTS),
      "a Core that has NEVER been listed still counts as unlisted, so a newly "
      "enabled one gets bootstrapped rather than ignored")

# And the reason it must see all thirty rows: on ten of them, a Core sitting on
# row 11 is indistinguishable from one that is absent. Measured on the live
# shop, three of five managed Cores read as absent from the visible table.
# TWO rows past the first screen, so the Core is genuinely stocked under the
# at-or-below-1 rule and the only thing being tested is whether the count saw
# past row 10.
deep = ([Row("Epic Booster (Highest)")] * 10
        + [Row(m.FAVOURITE_SLOTS[SLOTS[0]])] * 2)
check(SLOTS[0] not in m.slots_needing_restock(deep),
      "a Core on rows 11-12 of a thirty-row shop is stocked, which is why the "
      "count is taken over an enumeration rather than a screen")
check(SLOTS[0] in m.slots_needing_restock(deep[:10]),
      "...and reading only the first ten rows would have called it sold out, "
      "which is the mistake that buys 250 Sets of a stocked item")

# -- whole_shop_listings: a failed read is None, never an empty shop --------
# The difference decides whether an unreadable shop buys nothing or buys
# EVERYTHING. Returning [] would make every enabled Core read as unlisted, and
# the pipeline would stock all five on the strength of a read that failed.
# whole_shop_listings now insists on the Register tab before enumerating --
# enumerating scrolls, and scrolling on the Purchase tab moves the OFFERS. The
# stub has to satisfy that, or every case below reads as "could not switch tab"
# rather than testing what it means to.
_saved_enum = (m.enumerate_listings, m.register_tab_open)
try:
    m.register_tab_open = lambda source=None: True
    m.enumerate_listings = lambda timeout=8.0, verbose=True, stop_after=None: None
    check(m.whole_shop_listings(verbose=False) is None,
          "an unreadable shop is None, NOT an empty list -- [] would read as "
          "'nothing is listed' and buy every enabled Core at once")
    check(m.whole_shop_listings(verbose=False) != [],
          "and the two are distinguishable, which is the whole point")

    rows = [Row("Force Core(High)"), Row("Epic Booster (Highest)")]
    m.enumerate_listings = (
        lambda timeout=8.0, verbose=True, stop_after=None: list(enumerate(rows, start=1)))
    got = m.whole_shop_listings(verbose=False)
    check(got is not None and len(got) == 2,
          f"a good read returns the rows, got {got}")
    check([r.name for r in got] == [r.name for r in rows],
          "with the index stripped, since only the names are counted")

    m.enumerate_listings = lambda timeout=8.0, verbose=True, stop_after=None: []
    check(m.whole_shop_listings(verbose=False) == [],
          "a genuinely empty shop IS an empty list -- distinct from unreadable")
finally:
    m.enumerate_listings, m.register_tab_open = _saved_enum


# ==========================================================================
section("the chain: buy, then convert and list in alternating rounds")
# ==========================================================================

class Pipeline:
    """A simulated shop, vendor and inventory. Records every stage call.

    Models the one constraint that shapes the design: Cores do not stack, so a
    conversion is limited by FREE INVENTORY SLOTS, and only listing gives them
    back.
    """

    TAB_SLOTS = m.GRID_SIZE * m.GRID_SIZE

    def __init__(self, *, pack=62, sets_available=999, saving=25_000,
                 free_slots=None, vendor_opens=True, listing_works=True,
                 convert_raises=False, spills=False,
                 rows_used=0, purchase_tab=True,
                 inv_open=True, inv_tab=m.CONVERT_INVENTORY_TAB,
                 inv_tab_ok=True):
        self.pack = pack
        self.sets_available = sets_available
        self.saving = saving
        self.free = self.TAB_SLOTS - 1 if free_slots is None else free_slots
        self.vendor_opens = vendor_opens
        self.listing_works = listing_works
        self.convert_raises = convert_raises
        # When True, Cores overflow onto later inventory tabs. They still
        # exist and still get listed, but convert_cores counts slots on ONE
        # tab, so its figure is smaller than the truth.
        self.spills = spills
        # How full the shop is, and whether the Purchase tab can be
        # reached. Buying happens there; everything else works on
        # Register, and nothing used to switch.
        self.rows_used = rows_used
        self.purchase_tab = purchase_tab
        # Tab 4 is the default and every count is taken there: before
        # buying, after buying, and after converting.
        self.inv_open = inv_open
        self.inv_tab = inv_tab
        self.inv_tab_ok = inv_tab_ok

        self.log = []
        self.sets_held = 0
        self.cores_in_inventory = 0
        self.listed = 0

    # -- stage 0 ---------------------------------------------------------
    def rows(self, timeout=8.0, verbose=True):
        self.log.append(("count_rows",))
        # ONE ROW PER LISTING, not one fewer.
        #
        # This used to return listed - 1, on the reasoning that the row which
        # sold out is empty and the first listing refills it. Plausible, and
        # contradicted by production: across every occurrence in the logs --
        # twenty of them -- the listings made and the rows grown are equal.
        #
        #     shop went 25 -> 27 rows (2 listing(s), 2 of them new rows)
        #     shop went 13 -> 17 rows (4 listing(s), 4 of them new rows)
        #
        # The reasoning misses WHEN the count is taken. A Core counts as sold
        # out only when no row holds it at all -- a sold-but-uncollected row is
        # still `receive`, still occupied, still carrying the name. So by the
        # time a restock runs, that row has already been collected, and
        # rows_used (a count of change/receive rows) never included it. There
        # is no gap left to refill.
        listed = sum(1 for c in self.log if c[0] == "list")
        return self.rows_used + listed

    def purchase(self, timeout=10.0, verbose=True):
        self.log.append(("purchase_tab",))
        return self.purchase_tab

    def inv_origin(self, source=None, retries=3):
        return (1000, 200) if self.inv_open else None

    def pick_tab(self, tab, origin=None, timeout=5.0):
        self.log.append(("select_tab", tab))
        return bool(self.inv_tab_ok) and tab == self.inv_tab

    # -- stage 1 ---------------------------------------------------------
    def buy(self, item_slot, threshold=m.PRICE_DIFF_FLOOR, attempts=3,
            verbose=True, still_wanted=None):
        # still_wanted is how many Sets are left to reach the target; the real
        # function declines a row 1 bundle far larger than that.
        self.log.append(("buy", item_slot))
        # The real rule: the FIRST order of a restock may be any size (row 1
        # is the cheapest, and refusing a big bundle when nothing is held
        # means never trading); every order after it must keep the total
        # within the target.
        if still_wanted is not None and still_wanted > 0:
            first_order = still_wanted >= m.RESTOCK_TARGET
            if (m.BUY_NEVER_EXCEED_TARGET and not first_order
                    and self.pack > still_wanted):
                return {"bought": False,
                        "why": "bundle would take the total past the target",
                        "offer": None, "saving": self.saving,
                        "slot": item_slot}
        if self.saving < threshold:
            return {"bought": False, "why": "saving under threshold",
                    "offer": None, "saving": self.saving, "slot": item_slot}
        if self.sets_available <= 0:
            return {"bought": False, "why": "no Set offers", "offer": None,
                    "saving": self.saving, "slot": item_slot}
        pack = min(self.pack, self.sets_available)
        self.sets_available -= pack
        self.sets_held += pack
        offer = m.Offer(1, "Force Core Set (High) X %d" % pack,
                        pack * 187_278, pack, 340)
        return {"bought": True, "why": "", "offer": offer,
                "saving": self.saving, "slot": item_slot}

    # -- stage 2 ---------------------------------------------------------
    def open_vendor(self, timeout=10.0, verbose=True):
        self.log.append(("open_vendor",))
        return self.vendor_opens

    def close_vendor(self, verbose=True):
        self.log.append(("close_vendor",))
        return True

    def convert(self, core_name, quantity=m.CONVERT_QUANTITY, verbose=True,
                execute=True, require_layout=True):
        self.log.append(("convert", core_name, quantity, require_layout))
        if self.convert_raises:
            raise m.Aborted("simulated conversion failure")
        if self.spills:
            moved = min(quantity, self.sets_held)      # the truth
            counted = min(moved, self.free)            # what one tab can show
        else:
            moved = counted = min(quantity, self.sets_held, self.free)
        self.sets_held -= moved
        self.cores_in_inventory += moved
        self.free -= counted
        arrived = [(1, 2)] if counted else []
        return {"cell": (2, 3), "gives": core_name, "costs": "set",
                "expected": moved, "countable": counted, "converted": counted,
                "arrived": arrived,
                # The real convert_cores returns an ordered candidate list, so
                # the stub must too -- otherwise the chain tests pass against a
                # shape the function no longer produces.
                "candidates": arrived or [m.CONVERT_SET_SLOT],
                "landed": bool(counted), "verified": True}

    # -- stage 3 ---------------------------------------------------------
    # `expect_rows` mirrors list_cores: restock_core passes it so each round
    # requires one MORE matching row than the last, because every round lists
    # the same Core at the same price and round 1's row would otherwise vouch
    # for round 2. Without it here the double raises TypeError and the suite
    # dies mid-file, discarding 166 of its 194 checks with no failure shown.
    def list_them(self, core_name, slots, timeout=8.0, verbose=True,
                  expect_rows=None):
        self.log.append(("list", core_name, tuple(slots or ())))
        if not self.listing_works:
            return {"ok": False, "qty": 0, "why": "registration failed"}
        # Listing empties the inventory of that Core, handing the slots back.
        # The quantity the game reports counts EVERY matching item, across all
        # tabs -- which is why the pipeline measures progress by this and not
        # by the conversion's own per-tab slot count.
        qty = self.cores_in_inventory
        self.listed += qty
        self.free += qty
        self.cores_in_inventory = 0
        return {"ok": True, "qty": qty, "why": ""}

    # -- helpers ---------------------------------------------------------
    def stages(self):
        return [c[0] for c in self.log]


def run_restock(sim, slot=None, target=250, **kw):
    # `target` is the FLOOR now, and the buy loop runs to the CEILING.
    # These scenarios were written when one number meant both, so the
    # ceiling is pinned to it here -- otherwise every case would buy to
    # BUY_TARGET (500) and stop testing what it was written to test.
    kw.setdefault("ceiling", target)
    slot = SLOTS[0] if slot is None else slot
    names = {"shop_rows_used": sim.rows,
             "open_purchase_tab": sim.purchase,
             "inventory_origin": sim.inv_origin,
             "select_inventory_tab": sim.pick_tab,
             "buy_cheapest_set_detail": sim.buy,
             "open_npc_shop": sim.open_vendor,
             "close_npc_shop": sim.close_vendor,
             "convert_cores": sim.convert,
             "list_cores": sim.list_them}
    saved = {k: getattr(m, k) for k in names}
    try:
        for k, v in names.items():
            setattr(m, k, v)
        return m.restock_core(slot, target=target, verbose=False, **kw)
    finally:
        for k, v in saved.items():
            setattr(m, k, v)


# -- the healthy path ------------------------------------------------------
sim = Pipeline(pack=62)
res = run_restock(sim, target=250)
# 62-Set bundles against a 250 target: 62, 124, 186, 248 -- and the next would
# take it past, so it stops two short. Deliberate: slightly under is a rounding
# error, hundreds of millions over is not.
check(248 <= res["bought"] <= 250,
      f"buys up to the target without going over, got {res['bought']}")
check(res["converted"] == res["bought"],
      f"everything bought gets converted ({res['converted']} of {res['bought']})")
check(res["listed"] == res["converted"],
      f"everything converted gets listed ({res['listed']})")
check(sim.sets_held == 0, f"no Sets left over, got {sim.sets_held}")
check(sim.cores_in_inventory == 0,
      f"no Cores left sitting in the inventory, got {sim.cores_in_inventory}")

stages = sim.stages()
check(stages.count("buy") >= 4,
      f"250 at 62 per bundle needs several orders, got {stages.count('buy')}")
check(max(i for i, s in enumerate(stages) if s == "buy")
      < stages.index("open_vendor"),
      "every purchase happens before the vendor is opened -- buying is done on "
      "the Agent Shop's Purchase tab, and the vendor window would cover it")
check(set(stages[:stages.index("buy")])
      == {"count_rows", "purchase_tab", "select_tab"},
      f"and the only things preceding the first buy are the capacity check, "
      f"the Purchase tab and the inventory tab, got "
      f"{stages[:stages.index('buy')]}")
check(stages.index("buy") < stages.index("convert"),
      "nothing is converted before anything is bought")
check(stages.index("convert") < stages.index("list"),
      "nothing is listed before anything is converted")

# The property the whole design turns on: convert and list ALTERNATE.
pairs = [s for s in stages if s in ("convert", "list")]
check(pairs == ["convert", "list"] * (len(pairs) // 2),
      f"convert and list alternate strictly, got {pairs}")
check(len(pairs) >= 4,
      f"more than one round, so the alternation is actually exercised "
      f"({len(pairs) // 2} round(s))")

# The vendor must be shut before the Agent Shop is used to list.
for i, stage in enumerate(stages):
    if stage == "list":
        before = stages[:i]
        check("close_vendor" in before and
              before[len(before) - 1 - before[::-1].index("close_vendor")] ==
              "close_vendor",
              "the vendor window is closed before each listing")
        break
# The vendor must be shut before the run ends -- but it is no longer the LAST
# thing that happens: the shop's row count is measured afterwards, which needs
# the Agent Shop rather than the vendor. What matters is that nothing touches
# the vendor again once it is closed.
check("close_vendor" in stages, "the vendor is closed")
_last_close = len(stages) - 1 - stages[::-1].index("close_vendor")
check(not any(st in ("open_vendor", "convert") for st in stages[_last_close + 1:]),
      f"and nothing touches it afterwards, got {stages[_last_close + 1:]}")


# -- the inventory ceiling, which is why the rounds exist ------------------
# 63 free slots on a tab, 250 Sets bought: a single conversion cannot do it.
sim = Pipeline(pack=250, free_slots=63)
res = run_restock(sim, target=250)
rounds = sim.stages().count("convert")
check(rounds > 1,
      f"250 Cores into 63 slots needs more than one round, got {rounds}")
check(res["listed"] == res["bought"],
      f"and all {res['bought']} still end up listed, got {res['listed']}")
check(max(0, sim.TAB_SLOTS - 1 - sim.free) == 0,
      "the tab is handed back empty at the end")


# -- the tab spill, which is why progress is measured by what was LISTED ---
# 250 Cores do not fit on one tab. They land on later ones, where
# convert_cores' slot count cannot see them -- so its figure UNDER-reports
# while the listing, whose quantity counts every matching item in the whole
# inventory, is right. A pipeline that trusted the conversion count would think
# it had barely started after spending every Set.
sim = Pipeline(pack=250, free_slots=63, spills=True)
res = run_restock(sim, target=250)
check(res["bought"] == 250, f"250 Sets bought, got {res['bought']}")
check(res["listed"] == 250,
      f"all 250 Cores end up listed, got {res['listed']}")
check(res["converted"] < res["listed"],
      f"and the conversion count is genuinely smaller ({res['converted']}) "
      "than the truth, which is the whole point of this case")
check(sim.stages().count("convert") == 1,
      f"one conversion is enough; the loop does not keep going after the Sets "
      f"are spent, got {sim.stages().count('convert')} round(s)")
check(sim.sets_held == 0, "no Sets left over")


# -- how far a purchase may go past the target -----------------------------
# Buying stops at the first order that REACHES the target and always takes row
# 1, so the last bundle overshoots. The rule: every order after the first must
# keep the total within the target; the FIRST order may be any size, because
# row 1 is the cheapest per item and refusing a big bundle when nothing is held
# would mean never trading at all.
#
# Measured on 2026-08-07, before this existed: with 213 of 250 held, row 1 was
# a 999 bundle at 428,142,429 Alz and the run took it -- 82% of everything
# spent that session, in one click.
check(m.BUY_NEVER_EXCEED_TARGET is True,
      "later orders are held to the target")

# A first order of any size is taken, and all of it is converted and listed.
for pack in (37, 62, 100, 250, 999):
    sim = Pipeline(pack=pack)
    res = run_restock(sim, target=m.RESTOCK_TARGET)
    check(res["bought"] >= min(pack, m.RESTOCK_TARGET),
          f"pack {pack}: something is bought, got {res['bought']}")
    check(res["listed"] == res["bought"],
          f"pack {pack}: ALL {res['bought']} Sets end up listed, got "
          f"{res['listed']}")
    check(sim.sets_held == 0,
          f"pack {pack}: none stranded in the bag, got {sim.sets_held}")

# The first order is exempt: a bundle far bigger than the target is still taken
# when nothing is held, or a market of big bundles would never be traded.
sim = Pipeline(pack=999)
res = run_restock(sim, target=m.RESTOCK_TARGET)
check(res["bought"] == 999,
      f"a 999 bundle as the FIRST order is allowed, got {res['bought']}")
check(res["listed"] == 999, f"and all of it listed, got {res['listed']}")

# But not once something is held. This is the case that actually happened.
_saved_bought = None
sim = Pipeline(pack=37)
res = run_restock(sim, target=m.RESTOCK_TARGET)
check(res["bought"] <= m.RESTOCK_TARGET + 37,
      f"small bundles accumulate without running away, got {res['bought']}")
check(res["bought"] >= 37, f"and at least one is taken, got {res['bought']}")


# ==========================================================================
section("row capacity: pause before buying what cannot be listed")
# ==========================================================================

# Every restock ADDS rows -- one per CONVERT_QUANTITY Cores listed. Buying
# first and finding the shop full afterwards strands the Cores with nowhere to
# go, and the next cycle sees the same empty slot and buys MORE on top.
check(m.SHOP_ROW_CAPACITY == 30, f"the shop holds 30 rows, got {m.SHOP_ROW_CAPACITY}")
check(m.restock_rows_needed(250) >= 1, "a restock needs at least one row")

NEED = m.restock_rows_needed(250)
for used, allowed in [(0, True), (20, True), (30 - NEED, True),
                      (31 - NEED, False), (30, False)]:
    sim = Pipeline(rows_used=used)
    res = run_restock(sim, target=250)
    check(bool(res["bought"]) is allowed,
          f"{used}/30 used + {NEED} needed -> "
          f"{'buys' if allowed else 'PAUSES'}, got bought={res['bought']}")
    if not allowed:
        check("buy" not in sim.stages(),
              f"{used}/30: nothing is bought when paused ({sim.stages()})")
        check("paused" in res["why"], f"{used}/30: and says so, got {res['why']!r}")

sim = Pipeline(rows_used=0)
run_restock(sim, target=250)
stages = sim.stages()
check(stages[0] == "count_rows", f"the row count comes first, got {stages[0]!r}")
check(stages.index("count_rows") < stages.index("buy"),
      "and before anything is spent")


class NoCount(Pipeline):
    def rows(self, timeout=8.0, verbose=True):
        self.log.append(("count_rows",))
        return None


sim = NoCount()
res = run_restock(sim)
check(res["bought"] == 0 and "buy" not in sim.stages(),
      "an uncountable shop buys nothing -- it is not treated as an empty one")
check("count" in res["why"], f"and says why, got {res['why']!r}")


# ==========================================================================
section("buying happens on the Purchase tab, onto the work tab")
# ==========================================================================

# The first live restock refused here: purchase_ready would not click
# Purchase-tab coordinates while the window showed Register, and nothing had
# ever switched.
sim = Pipeline()
run_restock(sim)
stages = sim.stages()
check("purchase_tab" in stages, "the Purchase tab is opened")
check(stages.index("purchase_tab") < stages.index("buy"),
      "before the first purchase")
check(stages.index("count_rows") < stages.index("purchase_tab"),
      "and after the capacity check, so a paused restock does not even switch")

sim = Pipeline(purchase_tab=False)
res = run_restock(sim)
check(res["bought"] == 0 and "buy" not in sim.stages(),
      "an unreachable Purchase tab buys nothing")
check("Purchase tab" in res["why"], f"and says so, got {res['why']!r}")

# Sets are bought onto the work tab, because that is where the conversion
# counts what lands.
sim = Pipeline()
run_restock(sim)
picked = [a[1] for a in sim.log if a[0] == "select_tab"]
check(picked and all(t == m.CONVERT_INVENTORY_TAB for t in picked),
      f"the inventory is put on tab {m.CONVERT_INVENTORY_TAB}, got {picked}")
check(sim.stages().index("select_tab") < sim.stages().index("buy"),
      "before the first purchase")

sim = Pipeline(inv_tab_ok=False)
res = run_restock(sim)
check(res["bought"] == 0 and "buy" not in sim.stages(),
      "if the work tab cannot be selected, nothing is bought")


# ==========================================================================
section("running out of Alz halts buying for the rest of the run")
# ==========================================================================

# Not transient: the money only returns when something SELLS, so every further
# attempt walks the whole pipeline to reach the same refusal -- and a
# half-funded restock leaves Sets in the bag for a later cycle to buy more on
# top of.
_saved_halt = (m.BUY_HALTED, m.BUY_HALT_REASON, m.BUY_ENABLED)
try:
    m.BUY_HALTED, m.BUY_HALT_REASON = False, ""
    m.BUY_ENABLED = True
    check(m.restock_is_armed() is True, "armed while there is money")
    m.halt_buying("cannot afford 'Force Core Set (High)' at 1,870,000 Alz")
    check(m.BUY_HALTED is True, "the halt latches")
    check(m.restock_is_armed() is False, "and disarms every later restock")
    first = m.BUY_HALT_REASON
    m.halt_buying("some later, less useful reason")
    check(m.BUY_HALT_REASON == first,
          "the FIRST reason is kept -- it is the one that explains the run")
    m.ENABLE_BUYING["Force Core(High)"] = True
    check(m.restock_is_armed() is False,
          "switching a Core on does not revive it; only a restart does")

    # Relisting is untouched: the halt is read in one place, and relist_rows
    # consults it only to decide whether to do the OPTIONAL restock pass.
    m.BUY_ENABLED = False
    check(m.restock_is_armed() is False,
          "and with buying off it behaves exactly as before --buy existed")
finally:
    m.BUY_HALTED, m.BUY_HALT_REASON, m.BUY_ENABLED = _saved_halt

# affordable() must not halt on an unreadable balance: a halt is permanent, so
# a misread would silently disable buying for a whole run.
_saved_alz = m.get_alz
try:
    def _boom(src):
        raise RuntimeError("no panel")

    m.get_alz = _boom
    check(m.affordable(1_000) is None, "an unreadable balance is None, not False")
    m.get_alz = lambda src: 0
    check(m.affordable(1_000) is None, "and a zero reading is also undecided")
    m.get_alz = lambda src: 5_000
    check(m.affordable(5_000) is True, "5,000 affords exactly 5,000")
    check(m.affordable(5_001) is False, "but not 5,001")
finally:
    m.get_alz = _saved_alz


# ==========================================================================
section("finding the Cores: before/after spaces on the work tab")
# ==========================================================================

# A Core cannot be told from a Set by pixels, but the SPACES can -- provided
# both halves are used. Sets stack to 999, so a large purchase occupies two
# slots and Cores start landing after them; convert the first stack and its
# slot empties and refills with a Core in the same breath, so it appears in
# BOTH readings and a plain set difference misses it entirely.
LAYOUTS = [
    ("Cores into empty slots", {(1, 1)}, {(1, 1), (1, 2), (1, 3)}, [(1, 2), (1, 3)]),
    ("two Set stacks, Cores from (1,3)",
     {(1, 1), (1, 2)}, {(1, 1), (1, 2), (1, 3), (1, 4)}, [(1, 3), (1, 4)]),
    ("first stack consumed, a Core in the freed (1,1)",
     {(1, 1), (1, 2)}, {(1, 1), (1, 2)}, []),
    ("stack consumed, nothing new appears", {(1, 1)}, {(1, 1)}, []),
    ("tab emptied entirely", {(1, 1), (1, 2)}, set(), []),
]
for label, before, after, fresh in LAYOUTS:
    cands = m.core_slot_candidates(before, after)
    check(cands[:len(fresh)] == fresh,
          f"{label}: newly filled slots come first, got {cands}")
    check(sorted(set(cands)) == sorted(after),
          f"{label}: every occupied slot is a candidate, got {cands}")
    check(len(cands) == len(set(cands)), f"{label}: no slot twice, got {cands}")

for label, before, after, _f in LAYOUTS[2:4]:
    check(not (after - before), f"{label}: the set difference really is empty")
    check(m.core_slot_candidates(before, after),
          f"{label}: but there IS still a candidate, which is the whole point")

check(m.core_slot_candidates({(1, 1)}, set()) == [],
      "an empty tab offers no candidate rather than a fallback")


class _Register:
    """register_item that only accepts the slot actually holding the Cores."""

    def __init__(self, holder, qty=250, opens=True):
        self.holder, self.qty, self.opens = holder, qty, opens
        self.tried = []

    def open_window(self, timeout=15.0, verbose=True):
        return self.opens

    def register(self, row, col, **kw):
        self.tried.append((row, col))
        if (row, col) != self.holder:
            raise m.Aborted(f"slot ({row},{col}) does not hold "
                            f"{kw.get('expect_item')!r}")
        if kw.get("report") is not None:
            kw["report"]["qty"] = self.qty
        return True


def run_list(reg, slots, tab_ok=True):
    """Drive list_cores with the register and the inventory both stubbed.

    The inventory has to be modelled now: list_cores selects the WORK TAB
    before clicking any slot, because a (row, col) pair means nothing without
    one. It used to trust that nothing had moved the tab -- true only while the
    NPC route was the only way to open the shop. The --premium key switches to
    tab 8 to reach the Agent Shop key, and on 2026-08-09 the same slot numbers
    then addressed tab 8: two empty slots aborted and the third held 348
    crystals, which were listed at 18,026,400 Alz as a Force Core.
    """
    saved = (m.open_trade_window, m.register_item, m.inventory_origin,
             m.select_inventory_tab)
    try:
        m.open_trade_window, m.register_item = reg.open_window, reg.register
        m.inventory_origin = lambda source=None, retries=3: (1000, 200)
        m.select_inventory_tab = (lambda tab, origin=None, timeout=5.0:
                                  bool(tab_ok))
        return m.list_cores("Force Core(High)", slots, verbose=False)
    finally:
        (m.open_trade_window, m.register_item, m.inventory_origin,
         m.select_inventory_tab) = saved


# THE TAB IS A PRECONDITION, not an assumption. If the working tab cannot be
# reached, the slot numbers address some other tab -- so nothing is clicked at
# all. This is the guard that would have stopped 348 crystals being listed at
# 18,026,400 Alz as a Force Core.
reg = _Register(holder=(1, 3))
out = run_list(reg, [(1, 3)], tab_ok=False)
check(not out["ok"], f"an unreachable work tab must refuse, got {out}")
check("tab" in (out.get("why") or ""),
      f"and say why, got {out.get('why')!r}")
check(reg.tried == [],
      f"and must click NOTHING -- a slot number on the wrong tab is how "
      f"someone else's item gets listed. got {reg.tried}")

reg = _Register(holder=(1, 3))
out = run_list(reg, [(1, 3), (1, 4), (1, 1)])
check(out["ok"] and out["qty"] == 250,
      f"the first candidate holding the Cores is used, got {out}")
check(reg.tried == [(1, 3)], f"and nothing else is tried, got {reg.tried}")

reg = _Register(holder=(1, 1))
out = run_list(reg, m.core_slot_candidates({(1, 1), (1, 2)}, {(1, 1), (1, 2)}))
check(out["ok"], f"a Core in a freed slot is still found, got {out}")
check((1, 1) in reg.tried, f"by trying persisted slots too, got {reg.tried}")

reg = _Register(holder=(1, 4))
out = run_list(reg, [(1, 1), (1, 2), (1, 4)])
check(out["ok"], f"it works through refusals to the right slot, got {out}")

reg = _Register(holder=(8, 8))
out = run_list(reg, [(1, c) for c in range(1, 9)])
check(not out["ok"], "a candidate list with no Cores in it fails")
# Phase 1 tries the candidates it was GIVEN, capped at CORE_SLOT_TRIES.
check(reg.tried[:m.CORE_SLOT_TRIES] == [(1, c) for c in range(1, 5)],
      f"the given candidates are tried first, capped at {m.CORE_SLOT_TRIES}; "
      f"got {reg.tried[:m.CORE_SLOT_TRIES]}")
# Then phase 2 searches the OTHER tabs, because a conversion overflowing the
# work tab really does put Cores on later ones ("+186 on later tabs" is a real
# log line). That fallback was invisible to this test before: inventory_origin
# was stubbed to None, which returned it early, so the whole second phase went
# untested.
check(len(reg.tried) > m.CORE_SLOT_TRIES,
      f"and the other tabs are then searched, got {len(reg.tried)} attempts")

reg = _Register(holder=(1, 1))
out = run_list(reg, [])
check(not out["ok"] and not reg.tried,
      "no candidates means nothing is loaded at all")


# ==========================================================================
section("the vendor's Dungeon tab, and open_npc_shop driven for real")
# ==========================================================================

check(m.CONVERT_VENDOR_TAB == "Dungeon",
      f"the conversions live under Dungeon, got {m.CONVERT_VENDOR_TAB!r}")
check(m.CONVERT_VENDOR_TAB in m.VENDOR_TABS, "and it is a tab the reader knows")


class _Vendor:
    def __init__(self, showing="Normal", point=(195, 206), takes=True):
        self.showing, self.point, self.takes = showing, point, takes
        self.clicks = []

    def active(self, source=None):
        return self.showing

    def where(self, name, source=None):
        return self.point

    def click(self, x, y, settle=0.15):
        self.clicks.append((x, y))
        if self.takes:
            self.showing = m.CONVERT_VENDOR_TAB


def run_tab(v, name=m.CONVERT_VENDOR_TAB):
    saved = (m.active_vendor_tab, m.vendor_tab_point, m.click)
    try:
        m.active_vendor_tab, m.vendor_tab_point, m.click = (
            v.active, v.where, v.click)
        return m.open_vendor_tab(name, timeout=1.0, verbose=False)
    finally:
        m.active_vendor_tab, m.vendor_tab_point, m.click = saved


v = _Vendor(showing="Normal")
check(run_tab(v) is True, "selecting Dungeon from Normal succeeds")
check(v.clicks == [(195, 206)], f"and clicks the tab, got {v.clicks}")

v = _Vendor(showing=m.CONVERT_VENDOR_TAB)
check(run_tab(v) is True, "already-on-Dungeon still succeeds")
check(len(v.clicks) == 1,
      f"and STILL clicks rather than trusting the reading -- a tab click "
      f"cannot buy anything, a misread can, got {v.clicks}")

v = _Vendor(showing="Normal", takes=False)
check(run_tab(v) is False, "a tab that does not take is a failure")

v = _Vendor(showing="Normal")
v.where = lambda name, source=None: None
check(run_tab(v) is False, "an unfindable tab is a refusal")
check(v.clicks == [], f"and nothing is clicked, got {v.clicks}")


class _Npc:
    def __init__(self, open_now=False, opens=True, trade_open=False,
                 tab_takes=True):
        self.open_now, self.opens = open_now, opens
        self.trade_open, self.tab_takes = trade_open, tab_takes
        self.log = []

    def vendor_open(self, source=None):
        return self.open_now

    def trade_window(self, source=None):
        return self.trade_open

    def leave(self, verbose=True):
        self.log.append("leave_shop")
        self.trade_open = False
        return True

    def key(self, vk, settle=0.5, what=""):
        self.log.append(f"press:{what or vk}")
        if self.opens:
            self.open_now = True

    def tab(self, name=m.CONVERT_VENDOR_TAB, timeout=8.0, verbose=True):
        self.log.append(f"tab:{name}")
        return self.tab_takes


def run_open(npc):
    saved = (m.vendor_shop_open, m.trade_window_open, m.leave_shop,
             m.press_key, m.open_vendor_tab)
    try:
        (m.vendor_shop_open, m.trade_window_open, m.leave_shop,
         m.press_key, m.open_vendor_tab) = (
            npc.vendor_open, npc.trade_window, npc.leave, npc.key, npc.tab)
        return m.open_npc_shop(timeout=1.0, verbose=False)
    finally:
        (m.vendor_shop_open, m.trade_window_open, m.leave_shop,
         m.press_key, m.open_vendor_tab) = saved


npc = _Npc(open_now=False)
check(run_open(npc) is True, "a shut vendor is opened")
check(f"tab:{m.CONVERT_VENDOR_TAB}" in npc.log,
      f"and the Dungeon tab selected, got {npc.log}")
check(npc.log.index("press:N") < npc.log.index(f"tab:{m.CONVERT_VENDOR_TAB}"),
      "in that order")

npc = _Npc(open_now=True)
check(run_open(npc) is True, "an already-open vendor succeeds")
check(f"tab:{m.CONVERT_VENDOR_TAB}" in npc.log,
      "and STILL selects Dungeon -- the window remembers whichever tab was "
      "last used, so 'already open' says nothing about which page shows")

npc = _Npc(open_now=False, trade_open=True)
check(run_open(npc) is True, "the Agent Shop is closed and the vendor opened")
check(npc.log[0] == "leave_shop",
      f"the Agent Shop is closed FIRST -- both windows open would put the "
      f"Trade window over the grid, got {npc.log}")

npc = _Npc(open_now=False, opens=False)
check(run_open(npc) is False, "a vendor that will not open is a refusal")
check(not any(e.startswith("tab:") for e in npc.log),
      f"and no tab is selected, got {npc.log}")

npc = _Npc(open_now=True, tab_takes=False)
check(run_open(npc) is False,
      "a Dungeon tab that will not take fails the whole open -- the grid "
      "coordinates mean something else on any other page")


# ==========================================================================
section("widening: a restock's new rows keep being repriced")
# ==========================================================================

# A restock lists into the LOWEST EMPTY row. It runs because something sold
# out, so a gap is already waiting and the first listing refills it -- the shop
# grows by one FEWER than the listings made. The rest go to the end, outside
# whatever range was swept, and would never be repriced again.
_saved_added = m.BUY_ADDED_ROWS
try:
    m.BUY_ADDED_ROWS = 0
    rows, extra_n = m.widen_for_restocks([1, 2, 3, 4, 5], available=30)
    check(rows == [1, 2, 3, 4, 5] and extra_n == 0,
          f"no restocks yet means no widening, got {rows}")

    m.note_rows_added(4)
    rows, extra_n = m.widen_for_restocks([1, 2, 3, 4, 5], available=30)
    check(rows == list(range(1, 10)) and extra_n == 4,
          f"relist 1-5 plus a 4-row growth becomes 1-9, got {rows}")

    m.note_rows_added(5)
    rows, _ = m.widen_for_restocks([1, 2, 3, 4, 5], available=30)
    check(max(rows) == 14, f"growth accumulates across restocks, got {rows}")

    m.BUY_ADDED_ROWS = 9
    rows, extra_n = m.widen_for_restocks([1, 2, 3, 4, 5], available=7)
    check(max(rows) == 7 and extra_n == 2,
          f"widening stops at what the shop holds, got {rows} -- asking for a "
          "row that does not exist fails the WHOLE batch")
    rows, extra_n = m.widen_for_restocks([1, 2, 3, 4, 5], available=5)
    check(rows == [1, 2, 3, 4, 5] and extra_n == 0,
          f"and adds nothing when there is no room, got {rows}")

    m.BUY_ADDED_ROWS = 3
    rows, _ = m.widen_for_restocks([6, 7, 8], available=30)
    check(rows == [6, 7, 8, 9, 10, 11], f"it extends upward only, got {rows}")
    check(m.widen_for_restocks(None, 30) == ([], 0),
          "no row list means nothing to widen")
finally:
    m.BUY_ADDED_ROWS = _saved_added

# Growth is COUNTED from the listings, not measured by walking the table.
# Measuring cost 213.6 seconds in one restock -- four traversals of the shop --
# to produce a number identical to the registrations already made.
sim = Pipeline(pack=250, rows_used=20)
res = run_restock(sim, target=250)
check(res["rows_listed"] >= 1, f"listings were made, got {res['rows_listed']}")
check(res["rows_grown"] == res["rows_listed"],
      f"and the shop grew by exactly one row per listing -- see rows() for why "
      f"there is no gap to refill "
      f"({res['rows_listed']} listings, {res['rows_grown']} new rows)")


# ==========================================================================
section("whole_shop_listings: a failed read is None, never an empty shop")
# ==========================================================================

# The difference decides whether an unreadable shop buys nothing or buys
# EVERYTHING: [] would make every enabled Core read as unlisted.
_saved_enum = (m.enumerate_listings, m.register_tab_open)
try:
    m.register_tab_open = lambda source=None: True
    m.enumerate_listings = lambda timeout=8.0, verbose=True, stop_after=None: None
    check(m.whole_shop_listings(verbose=False) is None,
          "an unreadable shop is None, NOT an empty list")

    rows = [Row("Force Core(High)"), Row("Epic Booster (Highest)")]
    m.enumerate_listings = (
        lambda timeout=8.0, verbose=True, stop_after=None: list(enumerate(rows, start=1)))
    got = m.whole_shop_listings(verbose=False)
    check(got is not None and len(got) == 2, f"a good read returns rows, got {got}")

    m.enumerate_listings = lambda timeout=8.0, verbose=True, stop_after=None: []
    check(m.whole_shop_listings(verbose=False) == [],
          "a genuinely empty shop IS an empty list -- distinct from unreadable")
finally:
    m.enumerate_listings, m.register_tab_open = _saved_enum


# ==========================================================================
section("refusals: a stage that fails must not start the next one")
# ==========================================================================

# Nothing bought -> nothing converted, nothing listed.
sim = Pipeline(saving=0)                    # the deal is not worth taking
res = run_restock(sim)
check(res["bought"] == 0, "a dead deal buys nothing")
check("convert" not in sim.stages(), "and converts nothing")
check("list" not in sim.stages(), "and lists nothing")
check("open_vendor" not in sim.stages(),
      "and does not even open the vendor")
check(res["why"], f"and says why: {res['why']!r}")

# No Sets on the market at all.
sim = Pipeline(sets_available=0)
res = run_restock(sim)
check(res["bought"] == 0 and "convert" not in sim.stages(),
      "no Sets available buys nothing and converts nothing")

# The vendor will not open -> nothing is converted or listed.
sim = Pipeline(vendor_opens=False)
res = run_restock(sim)
check(res["bought"] > 0, "the purchase still happened")
check("convert" not in sim.stages(),
      "but nothing is converted when the vendor will not open")
check("list" not in sim.stages(), "and nothing is listed")
check("vendor" in res["why"], f"and it says so: {res['why']!r}")

# The conversion aborts -> nothing is listed from it.
sim = Pipeline(convert_raises=True)
res = run_restock(sim)
check(res["converted"] == 0, "an aborted conversion converts nothing")
check("list" not in sim.stages(), "and lists nothing")
check("conversion" in res["why"], f"and says so: {res['why']!r}")

# Listing fails -> the loop stops rather than converting into a full bag.
sim = Pipeline(listing_works=False)
res = run_restock(sim)
check(sim.stages().count("convert") == 1,
      f"a failed listing stops after one conversion, got "
      f"{sim.stages().count('convert')}")
check(res["listed"] == 0, "nothing is recorded as listed")
check(res["why"], f"and it says why: {res['why']!r}")

# An item that is not convertible is refused outright.
set_slot = m.favourite_set_slot(SLOTS[0])
sim = Pipeline()
res = run_restock(sim, slot=set_slot)
check(res["bought"] == 0 and not sim.log,
      f"a Set slot is refused before anything happens, log={sim.log}")
check("convertible" in res["why"], f"and says why: {res['why']!r}")


# ==========================================================================
section("--buy is off unless asked for")
# ==========================================================================

check(m.BUY_ENABLED is False,
      "BUY_ENABLED defaults to False -- this pipeline spends real money")

# -- the ENABLE_BUYING table ------------------------------------------------
# One line per Core, and every line is a decision about where money goes. The
# table has to stay in step with the favourites: a key that matches nothing
# would read as "this Core is disabled", which is the quiet direction -- the
# restock silently never fires and the shop just runs dry.
for name in m.ENABLE_BUYING:
    slot = m.favourite_for(name)
    check(slot is not None,
          f"ENABLE_BUYING key {name!r} resolves to a favourite slot")
    check(slot in SLOTS,
          f"ENABLE_BUYING key {name!r} is a managed Core, not a Set")
    check(isinstance(m.ENABLE_BUYING[name], bool),
          f"ENABLE_BUYING[{name!r}] is a bool, so it cannot be truthy by accident")

# Every managed Core appears, so switching one on is editing a line rather than
# discovering the table was missing it.
for slot in SLOTS:
    check(any(m.favourite_for(k) == slot for k in m.ENABLE_BUYING),
          f"{m.FAVOURITE_SLOTS[slot]} has a line in ENABLE_BUYING")

check(m.enabled_buying_slots() == tuple(sorted(
          m.favourite_for(k) for k, v in m.ENABLE_BUYING.items() if v)),
      "enabled_buying_slots resolves exactly the True entries")
# Deliberately NOT asserting which entries are on: that is an operator
# setting, changed between runs, and a test that pins it fails for a reason
# that has nothing to do with correctness. What must hold is that the table
# and the resolver agree, whatever it is set to.
check(set(m.enabled_buying_slots()) <= set(SLOTS),
      "every enabled entry resolves to a managed Core")
check(len(m.enabled_buying_slots()) == sum(1 for v in m.ENABLE_BUYING.values() if v),
      "and the count matches the number of True entries")

# A bad key must stop the run, not read as "off".
_saved_table = dict(m.ENABLE_BUYING)
try:
    m.ENABLE_BUYING["Nonsense Core (Imaginary)"] = True
    raised = False
    try:
        m.enabled_buying_slots()
    except ValueError:
        raised = True
    check(raised,
          "an ENABLE_BUYING key that names nothing raises, rather than being "
          "silently ignored -- a typo must not read as 'disabled'")
finally:
    m.ENABLE_BUYING.clear()
    m.ENABLE_BUYING.update(_saved_table)
check(m.ENABLE_BUYING == _saved_table, "the table is restored after that probe")
check(m.RESTOCK_TARGET > 0,
      f"a restock accumulates a positive number of Sets, got {m.RESTOCK_TARGET}")
check(m.RESTOCK_TARGET <= m.CONVERT_QUANTITY,
      f"and no more than one conversion can handle at a time "
      f"({m.RESTOCK_TARGET} vs {m.CONVERT_QUANTITY}) -- the target is about "
      "how much CAPITAL one restock commits, not how much a row holds")
# BUY_TARGET IS GONE -- it was always BUY_MAXIMUM, and exporting both into
# config.json made it possible to set them apart, which is silently incoherent
# (the buy loop runs to one, the ceiling checks read the other).
check(not hasattr(m, "BUY_TARGET"),
      "BUY_TARGET must not exist as a second name for BUY_MAXIMUM")
check("BUY_MAXIMUM or RESTOCK_TARGET" in _i.getsource(m.buy_sets_until),
      f"the accumulator runs to the SOFT MAXIMUM ({m.BUY_MAXIMUM}), not to "
      f"the hard minimum ({m.RESTOCK_TARGET}) -- stopping at the minimum "
      f"would make 'at 240, next bundle is 200, buy it' unreachable")
check(m.RESTOCK_TARGET < m.BUY_MAXIMUM,
      f"and the minimum is below the maximum: {m.RESTOCK_TARGET} < "
      f"{m.BUY_MAXIMUM}")
check(m.RESTOCK_MAX_BUYS > 0 and m.RESTOCK_MAX_ROUNDS > 0,
      "both loops are bounded, because a market that never settles would "
      "otherwise spend forever")
check(m.RESTOCK_MAX_BUYS <= 30 and m.RESTOCK_MAX_ROUNDS <= 60,
      f"and bounded: {m.RESTOCK_MAX_BUYS} buys, {m.RESTOCK_MAX_ROUNDS} rounds")
# The round cap is a runaway guard, not the expected count -- it has to clear
# the worst realistic case comfortably. 1,250 Sets (a 999 bundle on top of an
# almost-met 250 target) at 63 free slots a round is ~20 rounds.
check(m.RESTOCK_MAX_ROUNDS >= 20,
      f"the round cap ({m.RESTOCK_MAX_ROUNDS}) clears the worst realistic "
      "purchase, which is a 999 stack converted 63 slots at a time")
check(m.SET_STACK_MAX == 999,
      f"a Set stacks to 999, which is why a 250 target overshoots, got "
      f"{m.SET_STACK_MAX}")

# restock_sold_out is the only thing relist_rows calls, and it must do nothing
# when the shop still has stock.
calls = []
_saved = m.restock_core
try:
    m.restock_core = lambda slot, **kw: calls.append(slot) or {"slot": slot}
    # TWO rows each: one row is at the restock threshold, not above it.
    stocked = [Row(m.FAVOURITE_SLOTS[s]) for s in SLOTS] * 2
    out = m.restock_sold_out(stocked, verbose=False)
    check(out == [] and not calls,
          f"a fully stocked shop restocks nothing, got {calls}")
    m.restock_sold_out([], verbose=False)
    allowed = set(m.enabled_buying_slots())
    check(set(calls) == allowed,
          f"an emptied window restocks exactly what ENABLE_BUYING allows: "
          f"wanted {sorted(allowed)}, got {sorted(calls)}")
    check(set(calls) <= set(SLOTS), "and nothing outside the managed Cores")
finally:
    m.restock_core = _saved


# ==========================================================================
section("a real mixed shop: only ENABLE_BUYING decides what is rebought")
# ==========================================================================

# What rows 1-N actually hold: every Core grade, plus everything else the shop
# carries. The pipeline has to be indifferent to all of it except the Cores it
# has been switched on for.
MIXED = [
    Row("Yekaterina VIP Membership"),
    Row("Force Gem Pack"),
    Row("Siena's Unbinding Stone"),
    Row("Epic Booster (Highest)"),
    Row("Epic Booster (High)"),
    Row("Force Core(High)"),
    Row("Force Core(High)"),
    Row("Force Core(Highest)"),
    Row("Force Core (Ultimate)"),
    Row("Upgrade Core(Highest)"),
    Row("Upgrade Core (Ultimate)"),
    Row("Astral Bike Card"),
    Row(""),
]


def restocked(listings, table=None):
    """Which slots a restock pass would act on, with ENABLE_BUYING = `table`."""
    calls = []
    saved_core, saved_table = m.restock_core, dict(m.ENABLE_BUYING)
    try:
        if table is not None:
            m.ENABLE_BUYING.clear()
            m.ENABLE_BUYING.update(table)
        m.restock_core = lambda slot, **kw: calls.append(slot) or {"slot": slot}
        m.restock_sold_out(listings, verbose=False)
    finally:
        m.restock_core = saved_core
        m.ENABLE_BUYING.clear()
        m.ENABLE_BUYING.update(saved_table)
    return sorted(calls)


# Everything sells at once. Exactly the enabled Cores are rebought -- read
# from the table rather than hardcoded, since which ones are on is an operator
# setting that changes between runs.
gone = restocked([])
check(sorted(gone) == sorted(m.enabled_buying_slots()),
      f"a shop that empties entirely rebuys exactly the enabled Cores "
      f"{[m.FAVOURITE_SLOTS[s] for s in m.enabled_buying_slots()]}, got "
      f"{[m.FAVOURITE_SLOTS[s] for s in gone]}")
check(all(s in SLOTS for s in gone),
      "and nothing outside the managed Cores")

# The non-Core items can never trigger anything, whatever happens to them.
non_cores = [Row("Yekaterina VIP Membership"), Row("Force Gem Pack"),
             Row("Siena's Unbinding Stone"), Row("Epic Booster (Highest)"),
             Row("Astral Bike Card")]
# The property that matters: those items are never COUNTED as a Core, so they
# can neither satisfy a Core's stock nor stand in for one. (Under the absolute
# rule a shop holding only these does restock every enabled Core -- correctly:
# none of them are listed.)
check(m.core_row_counts(non_cores) == {s: 0 for s in SLOTS},
      "VIP, Gem Packs, Boosters and Bike Cards are not counted as Cores")
check(restocked(non_cores, table={k: False for k in m.ENABLE_BUYING}) == [],
      "and with buying off, a shop of them restocks nothing at all")
check(sorted(restocked(non_cores, table={k: True for k in m.ENABLE_BUYING}))
      == sorted(SLOTS),
      "while with buying on, every unlisted Core is stocked -- which is the "
      "bootstrap case, not a false trigger")

# Every table setting, over the same mixed shop selling out completely.
for name in m.ENABLE_BUYING:
    only = {k: (k == name) for k in m.ENABLE_BUYING}
    want = [m.favourite_for(name)]
    got = restocked([], table=only)
    check(got == want,
          f"with only {name!r} enabled, exactly that is rebought, got "
          f"{[m.FAVOURITE_SLOTS[s] for s in got]}")

all_on = {k: True for k in m.ENABLE_BUYING}
check(restocked([], table=all_on) == sorted(SLOTS),
      "with everything enabled, every Core in the window is rebought")

all_off = {k: False for k in m.ENABLE_BUYING}
check(restocked([], table=all_off) == [],
      "with everything disabled, nothing is rebought at all")

# Cores that stay put are not rebought even while their neighbours vanish.
# With ONLY High enabled, High surviving means no purchase at all -- even
# though every other Core in the window vanished.
only_high = {k: (m.favourite_for(k) == HIGH) for k in m.ENABLE_BUYING}
kept = [Row("Force Core(High)"), Row("Force Core(High)")]
check(restocked(kept, table=only_high) == [],
      "the one enabled Core surviving means no purchase, though everything "
      "else in the window went")
# And with everything enabled, the ones that DID vanish are restocked while
# the survivor is not.
gone_but_high = restocked(kept, table={k: True for k in m.ENABLE_BUYING})
check(HIGH not in gone_but_high,
      f"a surviving Core is never restocked, got {gone_but_high}")
check(set(gone_but_high) == set(SLOTS) - {HIGH},
      f"while every Core that vanished is, got {sorted(gone_but_high)}")

# With the table all off, the pass is not merely quiet -- it must not run.
# Otherwise every cycle pays for a table read to reach a foregone conclusion.
_saved = dict(m.ENABLE_BUYING)
try:
    m.ENABLE_BUYING.update(all_off)
    check(m.enabled_buying_slots() == (), "no slots are enabled")
    _was = m.BUY_ENABLED
    try:
        m.BUY_ENABLED = True
        check(m.restock_is_armed() is False,
              "--buy with an all-off table is NOT armed, so relist_rows skips "
              "the extra table read entirely and behaves exactly as it did "
              "before --buy existed")
        m.ENABLE_BUYING["Force Core(High)"] = True
        check(m.restock_is_armed() is True,
              "switching one Core on arms it")
        m.BUY_ENABLED = False
        check(m.restock_is_armed() is False,
              "and without --buy it is never armed, whatever the table says")
    finally:
        m.BUY_ENABLED = _was
finally:
    m.ENABLE_BUYING.clear()
    m.ENABLE_BUYING.update(_saved)


# ==========================================================================
section("buy_sets_until accumulates bundles, not orders")
# ==========================================================================

for pack, target in [(1, 10), (62, 250), (250, 250), (7, 20), (300, 250)]:
    sim = Pipeline(pack=pack)
    _saved = m.buy_cheapest_set_detail
    try:
        m.buy_cheapest_set_detail = sim.buy
        got = m.buy_sets_until(SLOTS[0], target=target, verbose=False)
    finally:
        m.buy_cheapest_set_detail = _saved
    # The first order is exempt from the ceiling, every later one is held to
    # it -- so the run stops at the last bundle that FITS, which is target //
    # pack orders (at least one, since the first is always allowed).
    orders = max(1, target // pack)
    check(len(got["orders"]) == orders,
          f"pack {pack}, target {target}: {orders} order(s) expected, "
          f"got {len(got['orders'])}")
    check(got["bought"] == pack * orders,
          "the count is the sum of the BUNDLE sizes, not the order count -- "
          "a listing is 'X 62', and counting purchases would count wrong")
    check(got["bought"] <= target or len(got["orders"]) == 1,
          f"pack {pack}, target {target}: only a FIRST order may exceed the "
          f"target, got {got['bought']} in {len(got['orders'])} order(s)")

# It stops at the ceiling rather than buying forever.
sim = Pipeline(pack=1)
_saved = m.buy_cheapest_set_detail
try:
    m.buy_cheapest_set_detail = sim.buy
    got = m.buy_sets_until(SLOTS[0], target=10_000, verbose=False)
finally:
    m.buy_cheapest_set_detail = _saved
check(len(got["orders"]) == m.RESTOCK_MAX_BUYS,
      f"an unreachable target stops at RESTOCK_MAX_BUYS "
      f"({m.RESTOCK_MAX_BUYS}), got {len(got['orders'])}")
check(got["bought"] < got["target"], "and reports that it fell short")


# ==========================================================================
print(f"\n{'=' * 60}")
print(f"restock: {count} checks, {len(fails)} failed")
if fails:
    for f in fails[:25]:
        print(f"  FAIL  {f}")
    sys.exit(1)
print("all green")
