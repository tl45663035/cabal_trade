"""chaos_recipe: two fixed clicks, no OCR, and a per-tier craft wait.

Re-applied on 2026-08-15 after the whole day's work was reverted to last
night's clean build. The feature is the operator's, restated here so it cannot
drift back:

  * "i dont want any OCR, i want precise coordinate, click on the craft tier,
    then click on recipe, then click Request all." An earlier version read the
    recipe tree to find the row and was rejected.
  * "wait time for tier 1 is 30s per 100 chaos, rounding up to nearest
    granularity, tier 2 is 10s per 100 chaos."
  * "Lets do in the granularity of 50. i.e. if we have 230 chaos core, we need
    to wait 25s."

NOT re-applied with it: the "remainder sweep", which switched CHAOS_RECIPE
behind the operator's back to craft leftovers with the other recipe. That was
never asked for. See [[no-invented-features]].

DRIVES NOTHING. Constants and arithmetic.
"""
import dataclasses
import inspect
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "chaos_recipe_test.db")

sys.argv = ["chaos_recipe_test"]
import trade as m  # noqa: E402

PASS = FAIL = 0


def check(ok, why):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {why}")


def section(title):
    print("=" * 74)
    print(title)
    print("=" * 74)


_WAS = m.CHAOS_RECIPE

section("two recipes, two fixed clicks each, and no reader anywhere")

check(sorted(m.CRAFT_RECIPES) == [1, 2],
      f"1 is the x1 and 2 is the x3, got {sorted(m.CRAFT_RECIPES)}")
for key, cost in ((1, 1), (2, 3)):
    tier, recipe, label = m.CRAFT_RECIPES[key]
    check(len(tier) == 2 and len(recipe) == 2,
          f"recipe {key} is a tier point and a recipe point, got {tier} {recipe}")
    check(tier != recipe, f"recipe {key}: the two clicks are different points")
    m.CHAOS_RECIPE = key
    check(m.craft_material_cost() == cost,
          f"recipe {key} consumes {cost} Core(s) a craft, got "
          f"{m.craft_material_cost()}")

# The x3 sits BELOW the x1 in the tree, so both of its points are lower.
t1, r1, _ = m.CRAFT_RECIPES[1]
t2, r2, _ = m.CRAFT_RECIPES[2]
check(t2[1] > t1[1] and r2[1] > r1[1],
      f"the 2000-2999 tier is below the 1000-1999 one, got {t1}/{t2}")
check(t1[0] == t2[0] and r1[0] == r2[0],
      "and the columns line up -- a tier node and a recipe row are each at a "
      "fixed x")

src = inspect.getsource(m.craft_chaos_sets)
check("find_words" not in src.split("Request All")[0],
      "nothing OCRs the tree before Request All -- the operator asked for "
      "coordinates, not a reader")

section("an unknown setting crafts NOTHING, rather than guessing a recipe")

check(m.CRAFT_RECIPES.get(9) is None, "9 is not a recipe")
check("recipe_unknown" in src,
      "an unrecognised chaos_recipe is recorded and refused -- clicking a "
      "guessed point would queue whatever happened to be under it")

section("the operator's own wait figures, to the second")

# "tier 1 is 30s per 100 ... tier 2 is 10s per 100", at granularity 50.
CASES = [
    (1, 230, 75.0),   # the worked example, doubled rate: 5 blocks x 15s
    (2, 230, 25.0),   # "if we have 230 chaos core, we need to wait 25s"
    (1, 100, 30.0),
    (2, 100, 10.0),
    (2, 200, 20.0),
    (1, 50, 15.0),
    (2, 1, 5.0),      # never below one block
    (2, 0, 5.0),      # nor for nothing at all
]
for recipe, cores, want in CASES:
    m.CHAOS_RECIPE = recipe
    got = m.craft_settle_seconds(cores)
    check(got == want,
          f"tier {recipe}, {cores} Cores waits {want:.0f}s, got {got:.0f}s")

check(m.CRAFT_SETTLE_BLOCK == 50,
      f"the block is 50, not 100, got {m.CRAFT_SETTLE_BLOCK}")

# An unknown recipe waits the SLOWER rate. Waiting too long costs seconds;
# collecting early strands paid-for material in the queue.
m.CHAOS_RECIPE = 9
check(m.craft_settle_rate() == max(m.CRAFT_SETTLE_PER_BLOCK_BY_RECIPE.values()),
      f"an unknown recipe falls back to the slowest rate, got "
      f"{m.craft_settle_rate()}")

# And the ceiling still binds, so a stuck queue cannot wait forever.
m.CHAOS_RECIPE = 1
check(m.craft_settle_seconds(100_000) == m.CRAFT_SETTLE_MAX,
      f"capped at {m.CRAFT_SETTLE_MAX}s, got {m.craft_settle_seconds(100_000)}")

section("the points scale with the window like every other coordinate")

# CRAFT_RECIPES is a DICT of points, so apply_layout needs its own case for it.
# Unregistered, these were the only craft coordinates that never scaled.
check(m._TRADE_FRAME_GEOMETRY.get("CRAFT_RECIPES") == "recipe_points",
      "CRAFT_RECIPES is registered in the geometry table")

REF = m.LAYOUT                       # apply_layout REASSIGNS the global
start = {k: (v[0], v[1], v[2]) for k, v in m.CRAFT_RECIPES.items()}
m.apply_layout(dataclasses.replace(REF, scale=0.75))
scaled = {k: (v[0], v[1]) for k, v in m.CRAFT_RECIPES.items()}
check(scaled[1][0] != start[1][0],
      f"at 0.75 the points move, got {scaled[1][0]} from {start[1][0]}")
check(all(isinstance(v[2], str) for v in m.CRAFT_RECIPES.values()),
      "and the LABEL is carried through unscaled -- it is text, not a "
      "coordinate")
m.apply_layout(REF)                  # the ORIGINAL, not the mutated global
back = {k: (v[0], v[1], v[2]) for k, v in m.CRAFT_RECIPES.items()}
check(back == start,
      f"and the reference layout restores them exactly, got {back}")

section("config.json drives it")

check("CHAOS_RECIPE" in m.LIVE_KNOBS,
      "chaos_recipe is a live knob, so it is re-read every cycle")
check(m.LIVE_KNOBS["CHAOS_RECIPE"][0] is int,
      "and is a whole number")

m.CHAOS_RECIPE = _WAS

print()
print("-" * 74)
print(f"{PASS + FAIL} checks, {FAIL} failed")
sys.exit(1 if FAIL else 0)
