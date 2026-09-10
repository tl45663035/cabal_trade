import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


FLOORS = {
    "Yekaterina VIP Membership": 104_000_000,
    "Siena's Unbinding Stone":    71_000_000,
    "Force Gem Package (x400)":  175_000_000,
    "Epic Booster (Highest)":     44_000_000,
}

for name, want in FLOORS.items():
    got = m.item_price_floor(name)
    check(got == want,
          f"{name} must floor at exactly {want:,} Alz, got {got:,}")
    check(got != 0,
          f"{name} resolved to NO floor at all -- the catalogue entry is "
          f"missing or its name no longer matches")

catalogue = {label for _token, label, _floor in m.ITEM_PRICE_FLOORS}
check(catalogue == set(FLOORS),
      f"ITEM_PRICE_FLOORS and this file disagree about WHICH items have "
      f"floors.\n  only in trade.py: {sorted(catalogue - set(FLOORS))}\n"
      f"  only here:        {sorted(set(FLOORS) - catalogue)}")

for _token, label, floor in m.ITEM_PRICE_FLOORS:
    if label in FLOORS:
        check(floor == FLOORS[label],
              f"the ITEM_PRICE_FLOORS row for {label} says {floor:,}, this "
              f"file says {FLOORS[label]:,}")

check(m.strictest_price_floor() == max(FLOORS.values()),
      f"the strictest floor is {max(FLOORS.values()):,}, got "
      f"{m.strictest_price_floor():,}")

_saved = m.COST_FLOOR_ON_RELIST
try:
    for state in (True, False):
        m.COST_FLOOR_ON_RELIST = state
        for name, want in FLOORS.items():
            got, _why = m.listing_floor(name)
            check(got >= want,
                  f"listing_floor({name}) must be at least {want:,} with "
                  f"COST_FLOOR_ON_RELIST={state}, got {got:,}")
finally:
    m.COST_FLOOR_ON_RELIST = _saved


print(f"floor_amounts_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
