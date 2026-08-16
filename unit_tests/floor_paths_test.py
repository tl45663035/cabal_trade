"""The VIP floor must bind on every path into register_item's pricing.

Exercises the pricing block's logic directly -- no input, no game.
"""

import sys

from pathlib import Path as _Path  # noqa: E402
_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import trade as m  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    if not ok:
        fails.append(label)
    print(f"{'OK  ' if ok else 'FAIL'} {label:62} -> {got!r}")


VIP = "Yekaterina VIP Membership Use Period: 30 days"
CORE = "Upgrade Core(High)"


def price_for(expect_item, force_price=None, suggested=90_000_000,
              price_floor=0):
    """Mirror of register_item's pricing block, including the final gates."""
    if expect_item:
        absolute_floor = m.item_price_floor(expect_item)
    else:
        absolute_floor = 0
        if force_price is None:
            return "REFUSED: unidentified item cannot be auto-priced"
    if force_price is not None:
        if expect_item and absolute_floor and force_price < absolute_floor:
            return "REFUSED: --price below the item's floor"
        price = force_price
    else:
        price, _ = m.choose_price(suggested, price_floor, None, absolute_floor)
    if price < m.MIN_PLAUSIBLE_PRICE:
        return "REFUSED: below the plausibility floor"
    if absolute_floor and price < absolute_floor:
        return "REFUSED: below the absolute floor"
    return price


FLOOR = m.item_price_floor("Yekaterina VIP Membership")   # derived, never restated
print(f"VIP floor: {FLOOR:,}\n")
check("item_price_floor(VIP)", m.item_price_floor(VIP), FLOOR)
# Call the REAL helper, not a reimplementation. This test mirrored the pricing
# logic inline and therefore missed strictest_price_floor() raising ValueError
# after ITEM_PRICE_FLOORS grew a third field -- a crash on every --price run.
# The HIGHEST floor in the table, which is not necessarily the VIP's -- it was
# when this was written and stopped being so the moment a third, dearer item
# was added. Derived, so the next addition does not turn this red for a reason
# that has nothing to do with what it is testing.
check("strictest_price_floor() runs and returns the table's highest",
      m.strictest_price_floor(), max(f for *_, f in m.ITEM_PRICE_FLOORS))

print("\n--- named VIP: the floor binds however low the market goes ---")
# Relative to FLOOR, not literals: 118,999,999 was written here as "above the
# floor", which was true at 105M and false the moment the floor moved to 119M.
for market in (FLOOR - 1, 90_000_000, 1_000_000, 1):
    check(f"relist VIP, market {market:,}", price_for(VIP, suggested=market),
          FLOOR)
check("relist VIP, market exactly at the floor",
      price_for(VIP, suggested=FLOOR), FLOOR)
check("relist VIP, market above the floor",
      price_for(VIP, suggested=FLOOR + 1), FLOOR + 1)

print("\n--- named VIP with --price ---")
check("--price below the floor is refused",
      price_for(VIP, force_price=90_000_000),
      "REFUSED: --price below the item's floor")
check("--price at the floor is allowed", price_for(VIP, force_price=FLOOR), FLOOR)

print("\n--- UNNAMED item (--register / do register) ---")
check("auto-pricing an unnamed item is refused",
      price_for(None), "REFUSED: unidentified item cannot be auto-priced")
check("auto-pricing an unnamed item at a low market is refused too",
      price_for(None, suggested=1_000),
      "REFUSED: unidentified item cannot be auto-priced")
check("--price on an unnamed item is honoured (human instruction)",
      price_for(None, force_price=500_000), 500_000)
check("--price on an unnamed item still needs a plausible figure",
      price_for(None, force_price=105), "REFUSED: below the plausibility floor")

print("\n--- a non-VIP is never forced up to the VIP floor ---")
check("named core takes the market price", price_for(CORE, suggested=85_000),
      85_000)
check("named core is not floored at 105M",
      price_for(CORE, suggested=85_000) != FLOOR, True)

print("\n--- the market read must still be plausible ---")
check("a clipped market read is refused", price_for(CORE, suggested=105),
      "REFUSED: below the plausibility floor")
check("no market price at all -> the 10B fallback",
      price_for(CORE, suggested=0), m.FALLBACK_PRICE)

print("\n--- OCR corruption of the VIP name (floor must survive) ---")
survives = 0
variants = ["Yekaterina VIP Membership", "Yekaterina V1P Membership",
            "Yekaterina VlP Membership", "Yekaterina V|P Membership",
            "Yekaterina V!P Membership", "Yekaterina V/P Membership"]
for name in variants:
    got = m.item_price_floor(name)
    if got == FLOOR:
        survives += 1
    else:
        print(f"     LOST  {name!r} -> {got}")
check(f"punctuation lookalikes keep the floor ({survives}/{len(variants)})",
      survives, len(variants))

print(f"\nfailures: {len(fails)}")
if fails:
    raise SystemExit(1)
