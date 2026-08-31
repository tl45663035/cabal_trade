import re
import time

import calibration

_SHARED = calibration.load_shared()
TAB_SETTLE = _SHARED["timing"]["tab_settle"]
ACTION_GAP = _SHARED["timing"]["action_gap"]
POLL_GAP = _SHARED["timing"]["poll_gap"]
DIALOG_TIMEOUT = _SHARED["timing"]["dialog_timeout"]
LOAD_ATTEMPTS = _SHARED["detect"]["load_attempts"]
SETTLE_PER_BLOCK = _SHARED["timing"]["craft_settle_per_block"]
SETTLE_BLOCK = _SHARED["timing"]["craft_settle_block"]
SETTLE_MAX = _SHARED["timing"]["craft_settle_max"]
CORES_PER_SET = calibration.CRAFT_CORES_PER_SET
UNKNOWN_HELD = 300
def _core_name(core=None):
    if core:
        return core
    slot = calibration._craft_slots()[0]
    return calibration.FAVOURITE_ITEMS[str(slot)] if slot else "the core"


class Refused(Exception):
    pass


def _cal():
    block = calibration.load().get("craft")
    if not block:
        raise Refused("the craft window has not been measured.")
    return block


def open_craft(verbose=True):
    say = print if verbose else (lambda *a: None)
    if calibration.craft_window_open():
        say("  the craft window is already open")
        return True
    if calibration.await_inventory(verbose=verbose) is None:
        raise Refused("the Inventory is not open, so the craft key cannot be "
                      "reached.")
    point = calibration.inventory_slot_point(*calibration.CRAFT_KEY_SLOT)
    for attempt in range(1, LOAD_ATTEMPTS + 1):
        calibration.click(
            *calibration.inventory_tab_point(calibration.CRAFT_TAB),
            settle=0.0)
        time.sleep(TAB_SETTLE)
        say(f"  right-clicking the craft key on tab {calibration.CRAFT_TAB} "
            f"slot {calibration.CRAFT_KEY_SLOT} at {point} "
            f"(attempt {attempt}/{LOAD_ATTEMPTS})")
        calibration.right_click(*point)
        deadline = time.monotonic() + DIALOG_TIMEOUT
        while time.monotonic() < deadline:
            if calibration.craft_window_open():
                return True
            time.sleep(POLL_GAP)
        calibration.snap(f"no_craft_window_{attempt}")
    raise Refused(
        f"the craft window did not open from tab {calibration.CRAFT_TAB} slot "
        f"{calibration.CRAFT_KEY_SLOT} in {LOAD_ATTEMPTS} attempt(s); the "
        f"panel was on another tab when the key was right-clicked.")


def select_recipe(core=None, verbose=True):
    say = print if verbose else (lambda *a: None)
    block = _cal()
    tier = block["tier"]
    recipes = block.get("recipes") or {}
    point = recipes.get(core) or block.get("recipe")
    if point is None:
        raise Refused(
            f"the craft window has no measured recipe for {_core_name(core)}; "
            f"run py src/calibration.py before crafting.")
    words = calibration.recipe_words(core) if core else calibration.CRAFT_RECIPE_WORDS
    say(f"  opening the {'-'.join(calibration.CRAFT_TIER_WORDS)} tier at "
        f"{tier}")
    calibration.click(*tier, settle=0.0)
    time.sleep(TAB_SETTLE)
    say(f"  choosing {' '.join(words)} at {point}")
    calibration.click(*point)
    time.sleep(TAB_SETTLE)
    if not calibration.craft_window_open():
        raise Refused("the craft window closed while the recipe was chosen.")
    return point


def request_all(verbose=True):
    say = print if verbose else (lambda *a: None)
    point = _cal().get("request")
    if point is None:
        raise Refused(
            f"the craft window has no measured "
            f"{' '.join(calibration.CRAFT_REQUEST_WORDS)} button; run "
            f"py src/calibration.py before crafting.")
    say(f"  {' '.join(calibration.CRAFT_REQUEST_WORDS)} at {point}")
    calibration.click(*point, settle=0.0)
    return point


def settle_seconds(made):
    blocks = -(-max(0, int(made)) // SETTLE_BLOCK)
    return min(SETTLE_MAX, SETTLE_PER_BLOCK * max(1, blocks))


def await_drain(before, verbose=True):
    say = print if verbose else (lambda *a: None)
    if before:
        wait = settle_seconds(before)
        say(f"  waiting {wait:.0f}s for {before} core(s) "
            f"({SETTLE_PER_BLOCK:.0f}s per {SETTLE_BLOCK}, rounded up)")
    else:
        wait = settle_seconds(UNKNOWN_HELD)
        say(f"  the caller did not say how many; waiting {wait:.0f}s, what "
            f"{UNKNOWN_HELD} core(s) would need "
            f"({SETTLE_PER_BLOCK:.0f}s per {SETTLE_BLOCK}, rounded up)")
    time.sleep(wait)
    return before


def complete_all(verbose=True):
    say = print if verbose else (lambda *a: None)
    point = _cal()["complete"]
    say(f"  Complete All at {point}")
    calibration.click(*point, settle=0.0)
    time.sleep(ACTION_GAP)
    return point


def compress(slot, verbose=True):
    say = print if verbose else (lambda *a: None)
    point = calibration.inventory_slot_point(*slot)
    say(f"  compressing tab {calibration.WORK_TAB} slot {tuple(slot)} at "
        f"{point}")
    calibration.alt_click(*point, settle=0.0)
    time.sleep(TAB_SETTLE)
    return point


def show_work_tab():
    calibration.click(*calibration.inventory_tab_point(calibration.WORK_TAB),
                      settle=0.0)
    time.sleep(TAB_SETTLE)


def close_craft():
    from open_inventory import VK_ESCAPE, press
    if calibration.craft_window_open():
        press(VK_ESCAPE)
        time.sleep(ACTION_GAP)
    return not calibration.craft_window_open()


def craft_sets(core=None, verbose=True, held=None):
    say = print if verbose else (lambda *a: None)
    CORE_NAME = _core_name(core)
    calibration.steps_reset()
    with calibration.step("open the craft window"):
        open_craft(verbose=verbose)
    with calibration.step("select the recipe"):
        select_recipe(core, verbose=verbose)
    before = int(held) if held else None
    if before:
        say(f"  {before} {CORE_NAME}(s) bought this pass, "
            f"{CORES_PER_SET} to a craft")
    else:
        say(f"  the caller did not say how many {CORE_NAME}(s) are held")
    with calibration.step(f"{' '.join(calibration.CRAFT_REQUEST_WORDS)}"):
        request_all(verbose=verbose)
    with calibration.step("wait for the queue"):
        used = await_drain(before, verbose=verbose)
    with calibration.step(f"select inventory tab {calibration.WORK_TAB} "
                          f"before completing"):
        show_work_tab()
    with calibration.step("read the slots before any Set arrives"):
        pre = calibration.occupied_slots()
    with calibration.step(f"{calibration.CRAFT_COMPLETE_WORD} All"):
        complete_all(verbose=verbose)
    arrived = sorted(calibration.occupied_slots() - pre)
    if arrived:
        landed = arrived[0]
        say(f"  the Sets arrived in {[tuple(a) for a in arrived]}")
    else:
        landed = tuple(calibration.WORK_SLOT)
        say(f"  no new slot filled, so the Sets can only have stacked into "
            f"{landed}")
    with calibration.step("compress the crafted Sets"):
        compress(landed, verbose=verbose)
    say(f"  {used if used is not None else 'an unknown number of'} "
        f"{CORE_NAME}(s) went into the queue")
    calibration.steps_table(f"craft from {used} {CORE_NAME}(s)")
    return {"held": before, "used": used, "slot": tuple(landed)}
