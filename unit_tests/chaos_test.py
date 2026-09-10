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


check(m.FAVOURITE_SLOTS.get(m.CHAOS_CORE_SLOT) == "Chaos Core",
      f"slot {m.CHAOS_CORE_SLOT} must hold 'Chaos Core', got "
      f"{m.FAVOURITE_SLOTS.get(m.CHAOS_CORE_SLOT)!r}")
check(m.FAVOURITE_SLOTS.get(m.CHAOS_SET_SLOT) == "Chaos Core Set",
      f"slot {m.CHAOS_SET_SLOT} must hold 'Chaos Core Set', got "
      f"{m.FAVOURITE_SLOTS.get(m.CHAOS_SET_SLOT)!r}")
check(m.CHAOS_SET_SLOT == m.CHAOS_CORE_SLOT + 1,
      "the Set must sit immediately after its Core -- favourite_set_slot "
      "pairs on slot+1")
check(m.favourite_set_slot(m.CHAOS_CORE_SLOT) == m.CHAOS_SET_SLOT,
      f"the pairing must resolve, got "
      f"{m.favourite_set_slot(m.CHAOS_CORE_SLOT)}")
check(m.set_behind("Chaos Core") == "Chaos Core Set",
      f"set_behind, got {m.set_behind('Chaos Core')!r}")
check(m.core_behind("Chaos Core Set") == "Chaos Core",
      f"core_behind, got {m.core_behind('Chaos Core Set')!r}")


check(m.CHAOS_CORE_SLOT not in m.managed_core_slots(),
      f"Chaos Core must not be a MANAGED core -- the Set->Core pipeline does "
      f"not own it and cannot convert it. got {m.managed_core_slots()}")
check(m.CHAOS_SET_SLOT not in m.managed_core_slots(),
      "nor its Set")
check(m.CHAOS_SLOTS == frozenset({m.CHAOS_CORE_SLOT, m.CHAOS_SET_SLOT}),
      f"CHAOS_SLOTS must name exactly the pair, got {sorted(m.CHAOS_SLOTS)}")

check(m.CHAOS_CORE_SLOT not in m.enabled_buying_slots(),
      f"Chaos Core must NOT be in enabled_buying_slots() -- the Set->Core "
      f"restock would buy the dearer Set and convert it into the cheaper "
      f"Core, losing the spread on every unit. got "
      f"{m.enabled_buying_slots()}")
check("Chaos Core" not in m.ENABLE_BUYING,
      "and it must not be named in ENABLE_BUYING at all")
check("Chaos Core Set" not in m.ENABLE_BUYING,
      "nor its Set")

_saved = dict(m.ENABLE_BUYING)
try:
    m.ENABLE_BUYING["Chaos Core"] = True
    raised = ""
    try:
        m.enabled_buying_slots()
    except ValueError as exc:
        raised = str(exc)
    check("Chaos Core" in raised,
          f"naming Chaos in ENABLE_BUYING must RAISE and name the item, got "
          f"{raised!r}")
finally:
    m.ENABLE_BUYING.clear()
    m.ENABLE_BUYING.update(_saved)
check(m.CHAOS_CORE_SLOT not in m.enabled_buying_slots(),
      "and it is back out afterwards")


check(1 <= m.CHAOS_CORE_SLOT <= m.FAVOURITE_COUNT,
      "the Chaos slots are inside the favourite bar")

check(callable(getattr(m, "right_click", None)),
      "right_click must exist -- the Remote Request Card is opened with the "
      "other mouse button, which nothing else in this file uses")
check(callable(getattr(m, "_point_in_inventory_grid", None)),
      "the inventory-grid test alt_click now relies on must exist")

import inspect

alt_src = inspect.getsource(m.alt_click)
check("vendor_shop_open()" in alt_src,
      "alt_click must still require the vendor window for the vendor grid")
check("_point_in_inventory_grid" in alt_src,
      "and accept the inventory grid for the compress step")
check("raise Aborted" in alt_src,
      "and still REFUSE when neither is under the point -- an Alt+click on "
      "bare ground is click-to-move and walks the character away from the NPC")

right_src = inspect.getsource(m.right_click)
check("finally" in right_src and "_release_right_button" in right_src,
      "right_click must release in a finally: a right button left logically "
      "down is the camera-look control, so every later cursor move becomes a "
      "camera drag and the script clicks coordinates that no longer point at "
      "what it measured")


print(f"chaos_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
