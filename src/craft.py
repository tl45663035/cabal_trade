import re
import time

import calibration

_SHARED = calibration.load_shared()
TAB_SETTLE = _SHARED["timing"]["tab_settle"]
ACTION_GAP = _SHARED["timing"]["action_gap"]
POLL_GAP = _SHARED["timing"]["poll_gap"]
DIALOG_TIMEOUT = _SHARED["timing"]["dialog_timeout"]
PANEL_REREADS = _SHARED["detect"]["panel_rereads"]
PANEL_REREAD_GAP = _SHARED["timing"]["panel_reread_gap"]
SETTLE_PER_BLOCK = _SHARED["timing"]["craft_settle_per_block"]
SETTLE_BLOCK = _SHARED["timing"]["craft_settle_block"]
SETTLE_MAX = _SHARED["timing"]["craft_settle_max"]
CORES_PER_SET = calibration.CRAFT_CORES_PER_SET


class Refused(Exception):
    pass


def _cal():
    block = calibration.load().get("craft")
    if not block:
        raise Refused("the craft window has not been measured.")
    return block


def _words(region):
    return calibration.ocr(calibration.grab(),
                           calibration._box(tuple(calibration._REG[region])))


def _pair(words, first, second):
    want = re.sub(r"[^a-z]", "", first.lower())
    tail = re.sub(r"[^a-z]", "", second.lower())
    for text, _conf, point in words:
        if re.sub(r"[^a-z]", "", text.lower()) != want:
            continue
        after = [p for t, _c, p in words
                 if abs(p[1] - point[1]) <= 6 and p[0] > point[0]
                 and re.sub(r"[^a-z]", "", t.lower()) == tail]
        if after:
            return [(point[0] + after[0][0]) // 2, point[1]]
    return None


def open_craft(verbose=True):
    say = print if verbose else (lambda *a: None)
    if calibration.craft_window_open():
        say("  the craft window is already open")
        return True
    if calibration.await_inventory(verbose=verbose) is None:
        raise Refused("the Inventory is not open, so the craft key cannot be "
                      "reached.")
    calibration.click(*calibration.inventory_tab_point(calibration.CRAFT_TAB))
    time.sleep(TAB_SETTLE)
    point = calibration.inventory_slot_point(*calibration.CRAFT_KEY_SLOT)
    say(f"  right-clicking the craft key on tab {calibration.CRAFT_TAB} slot "
        f"{calibration.CRAFT_KEY_SLOT} at {point}")
    calibration.right_click(*point)
    deadline = time.monotonic() + DIALOG_TIMEOUT
    while time.monotonic() < deadline:
        if calibration.craft_window_open():
            return True
        time.sleep(POLL_GAP)
    raise Refused(
        f"the craft window did not open from tab {calibration.CRAFT_TAB} slot "
        f"{calibration.CRAFT_KEY_SLOT}.")


def select_recipe(verbose=True):
    say = print if verbose else (lambda *a: None)
    tier = _cal()["tier"]
    say(f"  opening the {'-'.join(calibration.CRAFT_TIER_WORDS)} tier at "
        f"{tier}")
    calibration.click(*tier)
    time.sleep(TAB_SETTLE)
    rows = {}
    for text, _conf, point in _words("craft_recipes"):
        if point[1] > tier[1] + 4:
            rows.setdefault(point[1], []).append(point[0])
    if not rows:
        raise Refused(
            f"the tier at {tier} opened no recipe rows beneath it.")
    at_y = min(rows)
    point = [min(rows[at_y]), at_y]
    say(f"  choosing the recipe at {point}")
    calibration.click(*point)
    time.sleep(TAB_SETTLE)
    if not calibration.craft_window_open():
        raise Refused("the craft window closed while the recipe was chosen.")
    return point


def material_held():
    box = tuple(_cal()["material_box"])
    return calibration.read_money(calibration.grab(), box)


def await_material(verbose=True):
    say = print if verbose else (lambda *a: None)
    for attempt in range(1, PANEL_REREADS + 2):
        held = material_held()
        if held:
            return held
        say(f"    read {attempt}: the material counter is empty")
        time.sleep(PANEL_REREAD_GAP)
    return None


def request_all(verbose=True):
    say = print if verbose else (lambda *a: None)
    point = _pair(_words("craft_buttons"), "Request", "All")
    if point is None:
        raise Refused("no Request All button on the craft window.")
    say(f"  Request All at {point}")
    calibration.click(*point, settle=0.0)
    return point


def settle_seconds(made):
    blocks = -(-max(0, int(made)) // SETTLE_BLOCK)
    return min(SETTLE_MAX, SETTLE_PER_BLOCK * max(1, blocks))


def await_drain(before, verbose=True):
    say = print if verbose else (lambda *a: None)
    wait = settle_seconds(before)
    say(f"  waiting {wait:.0f}s for {before} core(s) "
        f"({SETTLE_PER_BLOCK:.0f}s per {SETTLE_BLOCK}, rounded up)")
    time.sleep(wait)
    after = material_held()
    if after is None:
        say(f"  the material counter did not read back; taking the queue as "
            f"having used all {before}")
        return before
    say(f"  the queue consumed {before - after} of {before}")
    return max(0, before - after)


def complete_all(verbose=True):
    say = print if verbose else (lambda *a: None)
    point = _cal()["complete"]
    say(f"  Complete All at {point}")
    calibration.click(*point, settle=0.0)
    time.sleep(ACTION_GAP)
    return point


def close_craft():
    from open_inventory import VK_ESCAPE, press
    if calibration.craft_window_open():
        press(VK_ESCAPE)
        time.sleep(ACTION_GAP)
    return not calibration.craft_window_open()


def craft_sets(verbose=True):
    say = print if verbose else (lambda *a: None)
    calibration.steps_reset()
    with calibration.step("open the craft window"):
        open_craft(verbose=verbose)
    with calibration.step("select the recipe"):
        select_recipe(verbose=verbose)
    with calibration.step("read the material counter"):
        before = await_material(verbose=verbose)
    if not before:
        raise Refused("no Chaos Cores are held; nothing to craft.")
    say(f"  {before} Chaos Core(s) held, {CORES_PER_SET} to a Set")
    with calibration.step(f"select inventory tab {calibration.WORK_TAB}"):
        calibration.click(
            *calibration.inventory_tab_point(calibration.WORK_TAB), settle=0.0)
        time.sleep(TAB_SETTLE)
    with calibration.step("Request All"):
        request_all(verbose=verbose)
    with calibration.step("wait for the queue to drain"):
        used = await_drain(before, verbose=verbose)
    with calibration.step("Complete All"):
        complete_all(verbose=verbose)
    made = used // CORES_PER_SET
    say(f"  crafted {made} Set(s) from {used} Core(s)")
    calibration.steps_table(f"craft {made} Set(s)")
    return {"held": before, "used": used, "made": made}
