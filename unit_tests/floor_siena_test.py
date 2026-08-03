"""Two floors now. Prove the second one binds, and that it binds to ONE item.

Adding a floor is not just "does it fire" -- it is also "what else does it fire
on". A floor granted to the wrong item parks that item at a price nobody pays;
a floor missed on the right item is the failure the user has said twice must
never happen. So both directions get measured, on realistic OCR damage rather
than on clean strings.
"""

import random
import sys

from pathlib import Path as _Path  # noqa: E402
_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import trade as m  # noqa: E402

VIP = "Yekaterina VIP Membership"
SIENA = "Siena's Unbinding Stone"
# Read out of the table rather than restated, so raising a floor cannot leave
# this suite green while asserting the old number.
VIP_FLOOR = next(f for t, _, f in m.ITEM_PRICE_FLOORS if t == "vip")
SIENA_FLOOR = next(f for t, _, f in m.ITEM_PRICE_FLOORS if t == "siena")

fails = []


def check(cond, label):
    if not cond:
        fails.append(label)


print("=== 1. the table itself ===")
for token, catalogue, floor in m.ITEM_PRICE_FLOORS:
    print(f"  {token:8} {catalogue:28} {floor:>12,}")
check(len(m.ITEM_PRICE_FLOORS) == 2, "expected exactly two floors")
check(m.strictest_price_floor() == VIP_FLOOR,
      f"strictest floor is {m.strictest_price_floor():,}, expected {VIP_FLOOR:,}")
print(f"  strictest (used when the item cannot be named): "
      f"{m.strictest_price_floor():,}")

print("\n=== 2. clean names ===")
for name, want in ((VIP, VIP_FLOOR), (SIENA, SIENA_FLOOR),
                   ("Siena's Unbinding Stone\nUse Period: 30 days", SIENA_FLOOR)):
    got = m.item_price_floor(name)
    print(f"  {got:>12,}  {name!r}")
    check(got == want, f"{name!r} -> {got:,}, wanted {want:,}")

print("\n=== 3. items that must NOT get a floor ===")
# Real names read off the live table, plus the near-misses that matter.
innocent = [
    "Force Core(High)", "Force Core (Ultimate)", "Force Core(Highest)",
    "Upgrade Core (Ultimate)", "Upgrade Core(Highest)", "Upgrade Core(High)",
    "V|pgrade Core(High)",            # folds to contain 'vip'
    "Master's SIGMetal Headpiece (BL)", "Archridium Plate(GL)",
    "Sienna Powder",
    # Rejected on SIMILARITY (0.7368 against the 0.75 bar), not on length, so
    # it stays out regardless of FLOOR_LENGTH_RATIO.
    "Unbinding Stone (High)",
    "(empty)", "",
]

# Deliberately NOT in the list above. These are separate, cheaper items whose
# folded names sit inside "Siena's Unbinding Stone", and with
# FLOOR_LENGTH_RATIO disabled they collect its 71M floor.
#
# That is the accepted cost of the revert, not an oversight: a damaged read of
# the real item that loses its first word folds to exactly the same key, so no
# threshold can separate them. Flooring a cheap item means it does not sell;
# missing the floor on the real one means it sells for a fraction of its worth.
# Asserted in the direction the choice was made, so that flipping the constant
# back makes this test fail loudly rather than quietly.
ACCEPTED_OVER_MATCH = ["Unbinding Stone", "Binding Stone"]
for name in innocent:
    got = m.item_price_floor(name)
    flag = "" if got == 0 else f"   <-- GETS {got:,}"
    print(f"  {got:>12,}  {name!r}{flag}")
    check(got == 0, f"{name!r} wrongly floored at {got:,}")

print("\n=== 3b. the accepted over-match (asserted, so a flip-back fails) ===")
print(f"  FLOOR_LENGTH_RATIO = {m.FLOOR_LENGTH_RATIO}")
for name in ACCEPTED_OVER_MATCH:
    got = m.item_price_floor(name)
    print(f"  {got:>12,}  {name!r}")
    check(got == SIENA_FLOOR,
          f"{name!r} -> {got:,}; with FLOOR_LENGTH_RATIO disabled it should "
          f"take the {SIENA_FLOOR:,} floor. If this constant was raised again, "
          "that reintroduces the floor-loss this revert exists to prevent")

print("\n=== 4. the collision that actually worries me ===")
# Cabal has a plain 'Unbinding Stone' as a separate, cheap item. It is not in
# the innocent list above because it is genuinely ambiguous, so it is measured
# and reported rather than asserted either way.
from difflib import SequenceMatcher  # noqa: E402

for name in ("Unbinding Stone", "Unbinding Stone (High)"):
    key = m._floor_key(m.item_name(name))
    ref = m._floor_key(SIENA)
    ratio = SequenceMatcher(None, ref, key).ratio()
    got = m.item_price_floor(name)
    print(f"  {name!r}")
    print(f"    folded {key!r} vs {ref!r}")
    print(f"    similarity {ratio:.4f}  (bar is {m.FLOOR_NAME_SIMILARITY})")
    print(f"    token 'siena' present: {m._floor_key('siena') in key}")
    print(f"    -> floor {got:,}")

print("\n=== 4b. a CLIPPED read must keep its floor (the length guard's risk) ===")
# FLOOR_LENGTH_RATIO could plausibly reject a name the column cut short. It
# must not: the token route is exempt precisely so a clipped read survives.
for name, want in (
    ("Siena's Unbinding Ston", SIENA_FLOOR),
    ("Siena's Unbinding", SIENA_FLOOR),
    ("Siena's Unbind", SIENA_FLOOR),
    ("Siena's U", SIENA_FLOOR),
    ("Yekaterina VIP Membershi", VIP_FLOOR),
    ("Yekaterina VIP Member", VIP_FLOOR),
    ("Yekaterina VIP", VIP_FLOOR),
    ("Yekaterina Membership", VIP_FLOOR),   # token itself destroyed
):
    got = m.item_price_floor(name)
    key, ref = m._floor_key(name), m._floor_key(
        SIENA if "iena" in name else VIP)
    print(f"  {got:>12,}  {name!r:28} len {len(key)}/{len(ref)} "
          f"= {len(key)/len(ref):.2f}")
    check(got == want, f"clipped {name!r} lost its floor ({got:,})")

print("\n=== 4c. token-route over-matches, reported not asserted ===")
# These carry the token, so they keep the floor by design. Listing a cheap
# item too high only means it does not sell; losing a floor is the failure
# that matters, so the token route is left permissive on purpose.
for name in ("Siena Stone", "Siena's Powder", "Siena"):
    print(f"  {m.item_price_floor(name):>12,}  {name!r}")

print("\n=== 5. single-glyph damage must not lose a floor ===")
random.seed(20260803)
SWAPS = {"S": "5", "s": "5", "i": "l", "n": "m", "a": "o", "e": "c",
         "U": "V", "b": "h", "d": "cl", "g": "q", "t": "f", "o": "0",
         "V": "Y", "P": "F", "I": "T", "'": "", " ": ""}


def corrupt(name, rng):
    positions = [i for i, ch in enumerate(name) if ch in SWAPS]
    if not positions:
        return name
    i = rng.choice(positions)
    return name[:i] + SWAPS[name[i]] + name[i + 1:]


rng = random.Random(20260803)
for label, name, want in (("VIP", VIP, VIP_FLOOR), ("SIENA", SIENA, SIENA_FLOOR)):
    lost = []
    for _ in range(4000):
        damaged = corrupt(name, rng)
        if m.item_price_floor(damaged) < want:
            lost.append(damaged)
    rate = len(lost) / 4000 * 100
    print(f"  {label:6} floor lost on {len(lost):4d}/4000 corruptions ({rate:.2f}%)")
    for bad in sorted(set(lost))[:5]:
        print(f"      {bad!r} -> {m.item_price_floor(bad):,}")
    check(not lost, f"{label} floor lost on {len(lost)} corruptions")

print("\n=== 6. two-glyph damage (harder, reported not asserted) ===")
for label, name, want in (("VIP", VIP, VIP_FLOOR), ("SIENA", SIENA, SIENA_FLOOR)):
    lost = sum(1 for _ in range(4000)
               if m.item_price_floor(corrupt(corrupt(name, rng), rng)) < want)
    print(f"  {label:6} floor lost on {lost:4d}/4000 ({lost/40:.2f}%)")

print("\n=== 7. choose_price honours the new floor ===")
cases = [
    (60_000_000, SIENA_FLOOR, SIENA_FLOOR, "market below floor -> floor"),
    (81_476_025, SIENA_FLOOR, 81_476_025, "market above floor -> market"),
    (70_999_999, SIENA_FLOOR, SIENA_FLOOR, "one Alz below -> floor"),
    (71_000_000, SIENA_FLOOR, 71_000_000, "exactly the floor -> floor"),
    (1, SIENA_FLOOR, SIENA_FLOOR, "absurd market -> floor"),
    (0, SIENA_FLOOR, m.FALLBACK_PRICE, "no market -> fallback (above floor)"),
]
for suggested, floor, want, why in cases:
    price, note = m.choose_price(suggested, absolute_floor=floor)
    ok = price == want
    print(f"  {'ok ' if ok else 'FAIL'} suggested {suggested:>14,} -> "
          f"{price:>14,}   {why}")
    check(ok, f"choose_price({suggested}, floor={floor}) = {price}, wanted {want}")

print("\n=== 8. the floor a real Siena row would get, end to end ===")
listed = "Siena's Unbinding Stone"
f = m.item_price_floor(listed)
price, note = m.choose_price(60_000_000, absolute_floor=f)
print(f"  row {listed!r}, market says 60,000,000")
print(f"  floor {f:,} -> listing at {price:,}")
print(f"  note: {note}")
check(price == SIENA_FLOOR, "a real Siena row would list below its floor")

print(f"\nfailures: {len(fails)}")
for f_ in fails:
    print(f"  FAIL {f_}")
raise SystemExit(1 if fails else 0)
