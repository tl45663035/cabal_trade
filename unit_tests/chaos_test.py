"""The Chaos pair trades the OTHER WAY ROUND, and must stay out of the restock.

Every other pair in FAVOURITE_SLOTS is "the Set is the cheap raw material":
restock_pass buys Sets, converts DOWN to Cores at the vendor, and lists the
Cores. Verified live on 2026-08-09, Chaos is the reverse --

    slot 3  Chaos Core        694,980 each          (pack 1, 192 available)
    slot 4  Chaos Core Set    740,735 a unit        (packs of 133-540)

-- so the Set is the DEARER side and the flow is inverted: buy Cores, craft UP
to Sets through the Remote Request window, compress, list the Sets.

That inversion is the whole hazard. If the ordinary restock ever picked this
pair up it would buy the expensive side, convert it into the cheap one, and
lose the spread on every single unit -- silently, because every guard in that
pipeline is about grades and quantities, and none of them knows which
direction is profitable.

The separation is enforced by ENABLE_BUYING not naming them. That is one line
away from being wrong at any time, so it is asserted here rather than trusted.
"""
import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m  # noqa: E402

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


# -- the pair is where the operator saved it ------------------------------
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


# -- THE ONE THAT MATTERS: the ordinary restock must never see it ---------
# Excluded at the SOURCE -- managed_core_slots() does not own the pair -- not
# merely absent from ENABLE_BUYING. restock_test's invariants are what forced
# this: with Chaos only missing from ENABLE_BUYING it still reported "slot 3
# resolves to a SET->CORE cell, so the pipeline can actually convert it", and
# that was false. There is no vendor cell for Chaos; it is crafted.
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

# Even if someone adds it, enabled_buying_slots is the gate every buying path
# goes through -- so prove that adding it is what would break the separation,
# rather than assuming the absence above is the only reason it is safe.
_saved = dict(m.ENABLE_BUYING)
try:
    m.ENABLE_BUYING["Chaos Core"] = True
    # Better than staying quietly out: enabled_buying_slots() RAISES, because
    # its own rule is "a key that does not match a managed Core is a mistake,
    # not a no-op". So enrolling Chaos by hand stops the run at startup with a
    # message naming the item, rather than silently buying the wrong side of
    # the trade -- which is the loud failure this file prefers everywhere.
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


# -- the craft geometry, measured live ------------------------------------
check(1 <= m.CHAOS_CORE_SLOT <= m.FAVOURITE_COUNT,
      "the Chaos slots are inside the favourite bar")

# right_click exists and is the primitive the craft key needs. Alt+click had to
# be widened for the compress step, and that widening must not have removed the
# reason the guard exists.
check(callable(getattr(m, "right_click", None)),
      "right_click must exist -- the Remote Request Card is opened with the "
      "other mouse button, which nothing else in this file uses")
check(callable(getattr(m, "_point_in_inventory_grid", None)),
      "the inventory-grid test alt_click now relies on must exist")

import inspect  # noqa: E402

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
