"""MAXIMISE_ALL_QUANTITIES must mean the same thing at every entry point.

The bug, found by listing six VIP passes by hand on 2026-08-04:

    py trade.py --register 1 1 --price 110000000
    Loaded: qty '1 /6' -> 1/6
    Registered (1,1) qty 1 at 110,000,000 Alz each

One listed, five left in the inventory, and no "Setting quantity" line at all.
MAXIMISE_ALL_QUANTITIES was True the whole time.

The two entry points disagreed about a global setting:

    relist path   maximise_qty=wants_max_quantity(name)   -> True
    CLI  path     maximise_qty=args.max_qty               -> False unless
                                                              --max-qty given

so `--register` silently overrode the setting with an argparse flag default.
maximise_qty is now tri-state: True/False for a caller that has decided, None
for "use the configured policy". The CLI passes None when the flag is absent.

The exclusion list is the wrinkle. wants_max_quantity() checks the item's NAME
against NO_MAX_QUANTITY_ITEMS, and `--register` reads an inventory slot, which
the script cannot name -- the same reason it refuses to auto-price there. With
an empty exclusion list the policy is unambiguous; with a non-empty one it is
not, and the ambiguous case is refused rather than guessed.
"""
from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade

ITEM = "Yekaterina VIP Membership Use Period: 30 days"
PRICE = 110_000_000


def fresh(qty=1, qty_max=6, **flags):
    """A slot holding `qty_max` of one item, with `qty` loaded into the panel."""
    h = Harness(rows=[make_row(1, ITEM, price=PRICE, qty=qty_max)],
                panel=empty_panel())
    h.load_as = {"qty": qty, "qty_max": qty_max,
                 "lowest": PRICE, "average": PRICE}
    h.register_name = ITEM
    for key, value in flags.items():
        setattr(h, key, value)
    return h


def typed_quantities(h):
    """Every value typed into the quantity field."""
    out = []
    for name, args, _ in h.calls:
        if name == "type_number" and args:
            out.append(args[0])
    return out


def listed_qty(h):
    """The quantity the game ended up registering."""
    return h.registered[-1]["qty"] if h.registered else None


def settings(maximise=None, exclude=None):
    """Temporarily set the two globals; returns the previous pair."""
    before = (trade.MAXIMISE_ALL_QUANTITIES, trade.NO_MAX_QUANTITY_ITEMS)
    if maximise is not None:
        trade.MAXIMISE_ALL_QUANTITIES = maximise
    if exclude is not None:
        trade.NO_MAX_QUANTITY_ITEMS = exclude
    return before


def restore(before):
    trade.MAXIMISE_ALL_QUANTITIES, trade.NO_MAX_QUANTITY_ITEMS = before


# ===========================================================================
section("the reported bug: --register must list the whole stack")

h = fresh()
with h:
    # Exactly what the CLI now passes when neither --qty nor --max-qty is given.
    ok, exc = run(trade.register_item, 1, 1, force_price=PRICE,
                  maximise_qty=None)
    check("no exception", exc is None, repr(exc))
    check("it typed the maximise value",
          trade.MAX_QTY_ENTRY in typed_quantities(h),
          f"typed {typed_quantities(h)} -- without this the stack lists as one")
    check("the whole stack was listed", listed_qty(h) == 6,
          f"listed {listed_qty(h)} of 6 -- this is the defect verbatim: six "
          f"VIP passes went out as one")
    check("it said so in the log", h.said("Setting quantity"), h.out()[-300:])


# ===========================================================================
section("an explicit decision still wins over the policy")

h = fresh()
with h:
    ok, exc = run(trade.register_item, 1, 1, force_price=PRICE,
                  maximise_qty=False)
    check("maximise_qty=False: nothing typed into the quantity field",
          trade.MAX_QTY_ENTRY not in typed_quantities(h),
          f"typed {typed_quantities(h)}")
    check("maximise_qty=False: only what was loaded is listed",
          listed_qty(h) == 1, f"listed {listed_qty(h)}")

h = fresh()
with h:
    ok, exc = run(trade.register_item, 1, 1, force_price=PRICE, force_qty=4)
    check("force_qty=4: types exactly 4", 4 in typed_quantities(h),
          f"typed {typed_quantities(h)}")
    check("force_qty=4: does not also maximise",
          trade.MAX_QTY_ENTRY not in typed_quantities(h),
          f"typed {typed_quantities(h)} -- an explicit quantity is an "
          f"instruction, not a suggestion")
    check("force_qty=4: lists 4", listed_qty(h) == 4, f"listed {listed_qty(h)}")

h = fresh()
with h:
    ok, exc = run(trade.register_item, 1, 1, force_price=PRICE,
                  maximise_qty=True)
    check("maximise_qty=True: maximises even if the policy were off",
          trade.MAX_QTY_ENTRY in typed_quantities(h),
          f"typed {typed_quantities(h)}")


# ===========================================================================
section("the policy is honoured, whichever way it is set")

before = settings(maximise=False, exclude=())
try:
    h = fresh()
    with h:
        ok, exc = run(trade.register_item, 1, 1, force_price=PRICE,
                      maximise_qty=None)
        check("MAXIMISE_ALL_QUANTITIES=False: does not maximise",
              trade.MAX_QTY_ENTRY not in typed_quantities(h),
              f"typed {typed_quantities(h)} -- the setting must work in both "
              f"directions or it is not a setting")
        check("MAXIMISE_ALL_QUANTITIES=False: lists what was loaded",
              listed_qty(h) == 1, f"listed {listed_qty(h)}")
finally:
    restore(before)

before = settings(maximise=True, exclude=())
try:
    h = fresh()
    with h:
        ok, exc = run(trade.register_item, 1, 1, force_price=PRICE,
                      maximise_qty=None)
        check("MAXIMISE_ALL_QUANTITIES=True: maximises",
              trade.MAX_QTY_ENTRY in typed_quantities(h),
              f"typed {typed_quantities(h)}")
finally:
    restore(before)


# ===========================================================================
section("the exclusion list, when the item CAN be named")

before = settings(maximise=True, exclude=("yekaterina",))
try:
    h = fresh()
    with h:
        ok, exc = run(trade.register_item, 1, 1, force_price=PRICE,
                      maximise_qty=None, expect_item=ITEM)
        check("named + excluded: does NOT maximise",
              trade.MAX_QTY_ENTRY not in typed_quantities(h),
              f"typed {typed_quantities(h)} -- the exclusion list exists to "
              f"stop exactly this item being maximised")
        check("named + excluded: no exception", exc is None, repr(exc))

    h = fresh()
    with h:
        ok, exc = run(trade.register_item, 1, 1, force_price=PRICE,
                      maximise_qty=None, expect_item="Force Core(High)")
        check("named + NOT excluded: maximises",
              trade.MAX_QTY_ENTRY in typed_quantities(h),
              f"typed {typed_quantities(h)}")
finally:
    restore(before)


# ===========================================================================
section("the exclusion list, when the item CANNOT be named")

# --register reads an inventory slot. The script cannot name what is in it --
# the same reason it refuses to auto-price there -- so it cannot check the
# exclusion list either. Guessing would maximise an item the list exists to
# protect.
before = settings(maximise=True, exclude=("yekaterina",))
try:
    h = fresh()
    with h:
        ok, exc = run(trade.register_item, 1, 1, force_price=PRICE,
                      maximise_qty=None)
        check("unnameable + non-empty exclusion list: refuses",
              exc is not None or ok is False,
              f"ok={ok!r} exc={exc!r} -- silently maximising here defeats the "
              f"exclusion list entirely")
        check("...and says what to pass instead",
              h.said("--qty") or h.said("--max-qty")
              or (exc is not None and "qty" in str(exc)),
              f"{h.out()[-300:]} / {exc!r}")
        check("...and lists nothing", listed_qty(h) is None,
              f"listed {listed_qty(h)}")

    # The same case with the caller stating what it wants goes through.
    h = fresh()
    with h:
        ok, exc = run(trade.register_item, 1, 1, force_price=PRICE,
                      force_qty=6)
        # `ok is True`, not merely `exc is None`. register_item catches Aborted
        # and RETURNS False, so checking only the exception passes while the
        # call refused and listed nothing -- which is exactly what it was doing
        # here until this check was tightened.
        check("unnameable + explicit --qty: proceeds", ok is True,
              f"ok={ok!r} exc={exc!r}")
        check("unnameable + explicit --qty: lists that many",
              listed_qty(h) == 6, f"listed {listed_qty(h)}")

    h = fresh()
    with h:
        ok, exc = run(trade.register_item, 1, 1, force_price=PRICE,
                      maximise_qty=True)
        check("unnameable + explicit --max-qty: proceeds", ok is True,
              f"ok={ok!r} exc={exc!r}")
        check("unnameable + explicit --max-qty: maximises",
              trade.MAX_QTY_ENTRY in typed_quantities(h),
              f"typed {typed_quantities(h)}")
finally:
    restore(before)

# With an EMPTY exclusion list -- the shipped configuration -- there is nothing
# ambiguous about an unnameable item, so it must not refuse.
before = settings(maximise=True, exclude=())
try:
    h = fresh()
    with h:
        ok, exc = run(trade.register_item, 1, 1, force_price=PRICE,
                      maximise_qty=None)
        check("unnameable + EMPTY exclusion list: proceeds", ok is True,
              f"ok={ok!r} exc={exc!r} -- with nothing to exclude the policy is "
              f"unambiguous and refusing would break the shipped configuration")
        check("unnameable + EMPTY exclusion list: maximises",
              trade.MAX_QTY_ENTRY in typed_quantities(h),
              f"typed {typed_quantities(h)}")
finally:
    restore(before)


# ===========================================================================
section("the relist path is unchanged")

# It passes a resolved bool, so none of the above can alter it. Asserted
# because this is the path that has been working and must keep working.
for policy, excluded, expect_max in ((True, (), True),
                                     (True, ("yekaterina",), False),
                                     (False, (), False)):
    before = settings(maximise=policy, exclude=excluded)
    try:
        want = trade.wants_max_quantity(ITEM)
        h = fresh()
        with h:
            run(trade.register_item, 1, 1, force_price=PRICE,
                maximise_qty=want, expect_item=ITEM, expect_qty=6)
            got = trade.MAX_QTY_ENTRY in typed_quantities(h)
            check(f"relist path: policy={policy} exclude={excluded or '()'} "
                  f"-> maximise {expect_max}",
                  got is expect_max and want is expect_max,
                  f"wants_max_quantity={want} typed={typed_quantities(h)}")
    finally:
        restore(before)


check("globals restored after the suite",
      trade.MAXIMISE_ALL_QUANTITIES is True
      and trade.NO_MAX_QUANTITY_ITEMS == (),
      f"{trade.MAXIMISE_ALL_QUANTITIES} {trade.NO_MAX_QUANTITY_ITEMS} -- a "
      f"suite that leaves settings changed poisons every suite after it")


raise SystemExit(summary())
