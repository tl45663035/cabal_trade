import re
import sys
import time

import buy
import calibration
import ledger
import convert
import craft
import get_alz
import get_price
import row_model
import war
import open_agent_shop_premium as shop
import open_inventory as inv

_SHARED = calibration.load_shared()
_T = _SHARED["timing"]
ACTION_GAP = _T["action_gap"]
TAB_SETTLE = _T["tab_settle"]
_ROW = re.compile(_SHARED["text"]["purchase_row"])
MIN_PLAUSIBLE_PRICE = _SHARED["detect"]["min_plausible_price"]
CAPACITY = _SHARED["game_facts"]["shop_capacity"]
VISIBLE = _SHARED["game_facts"]["shop_visible"]


class NotReady(Exception):
    pass


_MEASURED = False


def initialise(verbose=True):
    global _MEASURED
    if not inv.focus_game():
        raise NotReady("could not bring the game to the foreground.")
    if not _MEASURED:
        if verbose:
            print("  measuring this screen before touching anything")
        calibration.main(close=False)
        _MEASURED = True
    elif verbose:
        print("  already measured this start; not walking the actions again")
    cal = calibration.load(force=True)
    if verbose:
        print(f"  calibrated for {cal['resolution']}, measured "
              f"{cal.get('measured_at')}, shop open")
    if war.ENABLED:
        if war.sync(verbose=verbose):
            at = war.now()
            start, end = war.quiet_window(at)
            print(f"  server {at:%H:%M:%S}; the next war quiet runs "
                  f"{start:%H:%M:%S} to {end:%H:%M:%S}, in "
                  f"{(start - at).total_seconds() / 60:.1f} min")
        else:
            print("  the server clock would not anchor, so the war schedule "
                  "is not being followed this run.")
    return cal


def require_shop(verbose=True):
    if calibration._trade_window_open():
        return True
    if verbose:
        print("  the Trade window is shut; opening the Agent Shop.")
    shop.open_agent_shop(verbose=verbose)
    time.sleep(TAB_SETTLE)
    if not calibration._trade_window_open():
        raise NotReady(
            "the Trade window would not open.")
    return True


def register_tab(verbose=True):
    require_shop(verbose=verbose)
    calibration.click(*calibration.load()["shop"]["register_tab"])
    time.sleep(TAB_SETTLE)
    calibration.park()
    time.sleep(ACTION_GAP)


def balance(verbose=True):
    return get_alz.read_balance()


def market(slot, verbose=True):
    calibration.steps_reset()
    out = get_price.get_price(slot, verbose=verbose)
    name = calibration.FAVOURITE_ITEMS[str(int(slot))]
    calibration.steps_table(
        f"price slot {slot} {name!r}: "
        + (f"{out['unit_price']:,}/unit" if out else "UNREAD"))
    return out


def seed(verbose=True):
    register_tab(verbose=verbose)
    model = row_model.RowModel().seed({})
    model.home(verbose=verbose)
    found = {}
    for index in range(1, row_model.MAX_TOP + 1):
        model.scroll_to(index, verbose=False)
        text = row_model.read_row_one()
        if row_model.row_one_is_empty(text):
            continue
        row = _row_from(text)
        if row is None:
            if verbose:
                print(f"    {index:2}  UNREAD {text!r}")
            continue
        found[index] = row
        if verbose:
            print(f"    {index:2}  {row.name[:34]:34} x{row.qty:<4} "
                  f"{row.price:>14,}")
    model.seed(found, top=row_model.MAX_TOP)
    model.home(verbose=verbose)
    if verbose:
        print(f"  seeded {len(found)} of rows 1-{row_model.MAX_TOP}")
        print(f"  rows {row_model.MAX_TOP + 1}-{CAPACITY} are NOT reachable at "
              f"position 1 and were not read")
    return model


def _row_from(text):
    found = _ROW.match((text or "").strip())
    if found is None:
        return None
    seen = re.sub(r"[^0-9]", "", found.group("qty"))
    qty = int(seen) if seen else 1
    price = int(found.group("price").replace(",", ""))
    if price < MIN_PLAUSIBLE_PRICE:
        return None
    return row_model.Row(
        found.group("name").strip(" |-)("),
        qty=qty if qty >= 1 else 1,
        price=price)


def cancel(model, index, verbose=True):
    return model.cancel(index, verbose=verbose)


def relist_one(model, index, verbose=True):
    text, row = row_at(model, index, verbose=False)
    button = row_model.row_button()

    if button == row_model.RECEIPT_WORD:
        complete = row_model.row_complete(text)
        if verbose:
            print(f"  row {index} has SOLD "
                  f"({'fully' if complete else 'partly'}); collecting")
        model.receive(index, verbose=False)
        text, row = row_at(model, index, verbose=False)
        button = row_model.row_button()
        if complete or button == row_model.REGISTER_WORD or row is None:
            if verbose:
                print(f"    collected; row {index} is empty, nothing to "
                      f"relist")
            return None
        if verbose:
            print(f"    collected; {row.qty} left to relist")

    if row_model.row_one_is_empty(text):
        model._slots.pop(index, None)
        if verbose:
            print(f"  row {index} is empty; nothing to relist")
        return None

    if row is None or button != row_model.CHANGE_WORD:
        calibration.snap(f"row_{index}_button_disagrees")
        print(f"  row {index}: SKIPPED and it should not have been. The row "
              f"reads {text!r}")
        print(f"    parsed  {'nothing' if row is None else str(row.qty) + ' at ' + format(row.price, ',')}")
        print(f"    button  {button!r}, box reads "
              f"{row_model.row_button_text()!r}")
        return None

    landing = calibration.first_free_slot(row_model.WORK_TAB, verbose=False)
    if landing is None:
        raise NotReady(
            f"inventory tab {row_model.WORK_TAB} is full, so row {index} "
            f"would come back with nowhere to go. Nothing cancelled.")

    if verbose:
        print(f"  row {index}: {row.name!r} x{row.qty} at {row.price:,} "
              f"-> tab {row_model.WORK_TAB} slot {landing}")
    model._slots[index] = row
    unit_floor, pair = calibration.price_floor(row.name)
    if unit_floor is None:
        print(f"  row {index}: {row.name!r} is floored by a {pair}, which did "
              f"not price this run; left listed at {row.price:,}.")
        return None
    standing = row_model.suggested_price(False)
    if standing is not None:
        raise row_model.Divergence(
            f"the shop slot already holds something the panel prices at "
            f"{standing:,}; row {index} has NOT been cancelled. Clear the "
            f"slot first.")
    lands_in = min([i for i in model.empty() if i < index] + [index])
    if verbose and lands_in != index:
        print(f"    rows {[i for i in model.empty() if i < index]} are empty, "
              f"so it will come back in row {lands_in}")
    model.cancel(index, verbose=False, tab_ready=True)
    calibration.click(*calibration.inventory_tab_point(row_model.WORK_TAB))
    pack = row.pack
    floor = unit_floor * pack
    why = ""
    if unit_floor:
        why = f"a {pair} costs {unit_floor:,}"
        if pack > 1:
            why += f", and this listing carries {pack}"
    try:
        out = model.list_slot(*landing, floor=floor, why=why, verbose=verbose,
                              lands_in=lands_in, expect_item=row.name)
    except row_model.SlotNeverFilled:
        seen = row_model.read_row_one()
        if row_model.row_function(seen) != row_model.RECEIPT_WORD:
            raise
        print(f"  row {index} sold while it was being cancelled, so nothing "
              f"came back to tab {row_model.WORK_TAB}; collecting it instead")
        model.receive(index, verbose=False)
        seen = row_model.read_row_one()
        if row_model.row_one_is_empty(seen):
            model._slots.pop(index, None)
            print(f"    collected; row {index} is empty")
        else:
            print(f"    collected; row {index} still reads {seen!r}")
        return None
    model._slots.pop(index, None)
    model._slots[lands_in] = row_model.Row(row.name, qty=out["qty"],
                                           price=out["price"])
    if verbose:
        print(f"    relisted {out['qty']} at {out['price']:,} in row "
              f"{lands_in}")
    return out


PASS_ALLOWANCE = _SHARED["war"]["quiet_before_end"]


def relist_pass(model, first, last, verbose=True):
    model.home(verbose=False)
    done = skipped = 0
    for index in range(first, last + 1):
        if relist_one(model, index, verbose=verbose):
            done += 1
        else:
            skipped += 1
    return done, skipped


def do_relist(first=None, last=None, minutes=None, verbose=True):
    run = calibration.load_shared()["run"]
    first = run["relist_from"] if first is None else int(first)
    last = run["relist_to"] if last is None else int(last)
    minutes = run["for_minutes"] if minutes is None else float(minutes)
    if first < 1 or last < first:
        raise NotReady(f"rows {first}-{last} is not a range to relist")
    initialise(verbose=verbose)
    register_tab(verbose=verbose)
    model = seed(verbose=verbose)

    deadline = time.monotonic() + minutes * 60
    print(f"relisting rows {first}-{last} for {minutes:g} minute(s)")
    passes = done = skipped = 0
    started = time.perf_counter()
    while True:
        war.avoid(allowance=PASS_ALLOWANCE, verbose=verbose)
        passes += 1
        print("")
        print(f"-- pass {passes} --")
        try:
            resupply_pass(model, first, last, verbose=verbose)
            made, missed = relist_pass(model, first, last, verbose=verbose)
        except row_model.Divergence as exc:
            print(f"  STOPPED: {exc}")
            break
        done += made
        skipped += missed
        rest_the_game(verbose=verbose)
        model.home(verbose=False)
        left = deadline - time.monotonic()
        if left <= 0:
            print(f"  {minutes:g} minute(s) are up after pass {passes}")
            break
        print(f"  pass {passes}: {made} relisted, {missed} skipped; "
              f"{left/60:.1f} minute(s) left")
    span = (time.perf_counter() - started) * 1000
    print("")
    print(f"{passes} pass(es), {done} relisted, {skipped} skipped "
          f"in {span/1000:.0f}s"
          + (f" ({span/done:.0f} ms a row)" if done else ""))
    return done


def do_list(row, col, price=None, verbose=True):
    initialise(verbose=verbose)
    register_tab(verbose=verbose)
    model = row_model.RowModel().seed({})
    started = time.perf_counter()
    out = model.list_slot(row, col, price=price, verbose=verbose)
    print(f"  done in {(time.perf_counter() - started) * 1000:.0f} ms")
    return out


def report(model):
    print(model.report())


def row_at(model, index, verbose=True):
    model.scroll_to(index, verbose=False)
    text = row_model.read_row_one()
    row = _row_from(text)
    if verbose:
        print(f"  row {index}: {text!r}")
        print(f"    function {row_model.row_function(text)!r}  "
              f"complete {row_model.row_complete(text)}")
    return text, row


def do_resupply(slot, verbose=True):
    run = calibration.load_shared()["run"]
    first, last = int(run["relist_from"]), int(run["relist_to"])
    initialise(verbose=verbose)
    register_tab(verbose=verbose)
    model = seed(verbose=verbose)
    held = rows_by_core(model, first, last).get(int(slot), 0)
    started = time.perf_counter()
    core = calibration.FAVOURITE_ITEMS[str(int(slot))]
    route = resupply_chaos if craft_route(core) else resupply_one
    out = route(model, int(slot), held, first, last, verbose=verbose)
    print(f"  done in {(time.perf_counter() - started) * 1000:.0f} ms")
    return out


def do_cancel(index, verbose=True):
    initialise(verbose=verbose)
    register_tab(verbose=verbose)
    model = row_model.RowModel().seed({})
    text, row = row_at(model, index, verbose=verbose)
    if row is None:
        print(f"  row {index} did not parse; nothing cancelled.")
        return None
    print(f"    target {row.name!r} x{row.qty} at {row.price:,}")
    model._slots[index] = row
    started = time.perf_counter()
    out = model.cancel(index, verbose=verbose)
    print(f"  done in {(time.perf_counter() - started) * 1000:.0f} ms")
    return out


def core_slots():
    return [int(slot) for slot in sorted(calibration.FAVOURITE_ITEMS, key=int)
            if calibration.pair_slot(int(slot)) is not None
            and "set" not in calibration.FAVOURITE_ITEMS[slot].lower()]


def buying_enabled(core_name):
    table = calibration.load_shared()["resupply"].get("enable_buying") or {}
    want = re.sub(r"[^a-z0-9]", "", (core_name or "").lower())
    for name, on in table.items():
        if re.sub(r"[^a-z0-9]", "", name.lower()) == want:
            return bool(on)
    return False


def rows_by_core(model, first, last):
    held = {slot: 0 for slot in core_slots()}
    for index, row in (model._slots or {}).items():
        if row is None or not first <= index <= last:
            continue
        slot = calibration.favourite_slot_of(row.name)
        if slot in held:
            held[slot] += 1
    return held


def resupply_one(model, slot, held, first, last, verbose=True):
    run = calibration.load_shared()["resupply"]
    core = calibration.FAVOURITE_ITEMS[str(slot)]
    pair = calibration.pair_slot(slot)
    set_name = calibration.FAVOURITE_ITEMS[str(pair)]
    print("")
    want_rows = calibration.rows_threshold(core)
    want_min = calibration.buy_min(core)
    want_max = calibration.buy_max(core)
    print(f"-- {core}: {held} row(s), threshold {want_rows} --")

    calibration.phases_reset()
    with calibration.phase(f"price {core}"):
        core_row = get_price.get_price(slot, verbose=False)
    with calibration.phase(f"price {set_name}"):
        set_row = get_price.get_price(pair, verbose=False)
    if core_row is None or set_row is None:
        print(f"  {core if core_row is None else set_name} would not price; "
              f"not buying blind.")
        return None
    threshold = calibration.price_diff_threshold(core)
    if threshold is None:
        print(f"  {core} has no price_diff_threshold in config.json, so "
              f"there is no gap it is worth buying at. Not buying.")
        return None
    diff = core_row["unit_price"] - set_row["unit_price"]
    print(f"  {core} {core_row['unit_price']:,} - {set_name} "
          f"{set_row['unit_price']:,} = {diff:,} "
          f"(threshold {threshold:,})")
    if diff <= threshold:
        print(f"  the gap does not clear the threshold; not buying.")
        return None

    rounds_needed = max(1, -(-int(want_max) // row_model.MAX_STACK))
    free_rows = [i for i in model.empty() if first <= i <= last]
    if len(free_rows) < rounds_needed:
        print(f"  rows {first}-{last} have {len(free_rows)} free and a "
              f"resupply can need {rounds_needed}; not buying, because a "
              f"Core with nowhere to list stays on tab "
              f"{calibration.CONVERT_INVENTORY_TAB}.")
        return None
    print(f"  {len(free_rows)} row(s) free inside {first}-{last}; a resupply "
          f"needs up to {rounds_needed}")

    with calibration.phase(f"find the free slot on tab "
                           f"{calibration.CONVERT_INVENTORY_TAB}"):
        landing = calibration.first_free_slot(
            calibration.CONVERT_INVENTORY_TAB, verbose=False)
    if landing is None:
        raise NotReady(
            f"inventory tab {calibration.CONVERT_INVENTORY_TAB} is full.")
    print(f"  tab {calibration.CONVERT_INVENTORY_TAB} is showing and free "
          f"from {landing}, so the {set_name} and the {core} land there")

    bought = orders = 0
    while bought < want_min:
        print(f"  {bought}/{want_min} {set_name} held")
        orders += 1
        got = None
        for attempt in range(1, int(run["buy_retries"]) + 1):
            try:
                with calibration.phase(f"buy order {orders}"):
                    got = buy.buy_row_one(pair, want_min - bought,
                                          verbose=verbose, held=bought,
                                          floor_qty=want_min,
                                          ceiling=want_max)
                break
            except buy.Refused as exc:
                if not getattr(exc, "retryable", False):
                    print(f"  stopping: {exc}")
                    break
                print(f"  attempt {attempt}/{run['buy_retries']}: {exc}")
                if attempt == int(run["buy_retries"]):
                    print(f"  the board kept moving through "
                          f"{run['buy_retries']} attempt(s); giving up on "
                          f"{set_name} this cycle.")
        if got is None:
            break
        if got["bought"] <= 0:
            print(f"  the last order bought nothing; stopping.")
            break
        bought += got["bought"]
    if bought <= 0:
        print(f"  nothing bought; not opening the vendor.")
        return None
    if bought < want_min:
        print(f"  bought {bought} of the {want_min} wanted.")

    unit_floor, floor_pair = calibration.price_floor(core)
    floor = 0 if unit_floor is None else unit_floor
    why = f"a {floor_pair} costs {unit_floor:,}" if unit_floor else ""
    rows, listed_total, converted_total = [], 0, 0
    left_to_convert = bought
    max_rounds = max(1, -(-bought // row_model.MAX_STACK)) + 1
    rounds = 0
    while left_to_convert > 0 and rounds < max_rounds:
        rounds += 1
        print("")
        print(f"  -- round {rounds}: convert up to {row_model.MAX_STACK}, "
              f"then list -- {left_to_convert} of {bought} left --")
        with calibration.phase(f"round {rounds}: close the Agent Shop"):
            calibration.close_everything()
        with calibration.phase(f"round {rounds}: open the vendor"):
            convert.open_vendor(verbose=verbose)
        with calibration.phase(f"round {rounds}: convert into {core}"):
            out = convert.convert(core, verbose=verbose)
        converted_total += out["converted"]
        left_to_convert -= out["converted"]

        with calibration.phase(f"round {rounds}: reopen the Agent Shop"):
            if not back_to_the_shop(verbose=verbose):
                raise NotReady("the Agent Shop would not reopen after the "
                               "vendor.")
        with calibration.phase(f"round {rounds}: select the Register tab"):
            register_tab(verbose=verbose)
        with calibration.phase(f"round {rounds}: select inventory tab "
                               f"{calibration.CONVERT_INVENTORY_TAB}"):
            calibration.click(*calibration.inventory_tab_point(
                calibration.CONVERT_INVENTORY_TAB), settle=0.0)
            time.sleep(row_model.TAB_SETTLE)

        print(f"  listing {out['converted']} {core} from slot(s) "
              f"{out['slots'][0]} to {out['slots'][-1]}")
        remaining = list(out["slots"])
        while remaining:
            empty = [i for i in model.empty() if first <= i <= last]
            if not empty:
                print(f"  rows {first}-{last} are full; {len(remaining)} "
                      f"slot(s) of {core} stay on tab "
                      f"{calibration.CONVERT_INVENTORY_TAB}.")
                left_to_convert = 0
                break
            lands_in = min(empty)
            with calibration.phase(f"round {rounds}: list {core} from "
                                   f"{remaining[0]}"):
                listed = model.list_slot(*remaining[0], floor=floor, why=why,
                                         verbose=verbose, lands_in=lands_in,
                                         expect_item=core)
            model._slots[lands_in] = row_model.Row(core, qty=listed["qty"],
                                                   price=listed["price"])
            rows.append(lands_in)
            listed_total += listed["qty"]
            still = calibration.occupied_slots()
            remaining = [w for w in remaining if w in still]
        left_to_convert = max(0, bought - listed_total)
    if left_to_convert > 0:
        print(f"  {left_to_convert} of {bought} {set_name} are still "
              f"unconverted after {rounds} round(s); they are on tab "
              f"{calibration.CONVERT_INVENTORY_TAB}.")
    calibration.phases_table(
        f"resupply {core}: bought {bought}, converted {converted_total}, "
        f"listed {listed_total} in rows {rows}")
    return {"slot": slot, "core": core, "set": set_name, "diff": diff,
            "bought": bought, "converted": converted_total,
            "listed": listed_total, "rows": rows}


def craft_route(core):
    if convert.cell_for(core):
        return False
    if calibration.pair_slot(calibration.favourite_slot_of(core)) is None:
        return False
    return bool(calibration.load().get("craft"))


def resupply_chaos(model, slot, held, first, last, verbose=True):
    run = calibration.load_shared()["resupply"]
    core = calibration.FAVOURITE_ITEMS[str(slot)]
    pair = calibration.pair_slot(slot)
    set_name = calibration.FAVOURITE_ITEMS[str(pair)]
    batch = calibration.CRAFT_CORES_PER_SET
    print("")
    want_rows = calibration.rows_threshold(core)
    want_min = calibration.buy_min(core)
    want_max = calibration.buy_max(core)
    print(f"-- {core}: {held} row(s), threshold {want_rows} --")

    calibration.phases_reset()
    with calibration.phase(f"price {core}"):
        core_row = get_price.get_price(slot, verbose=False)
    with calibration.phase(f"price {set_name}"):
        set_row = get_price.get_price(pair, verbose=False)
    if core_row is None or set_row is None:
        print(f"  {core if core_row is None else set_name} would not price; "
              f"not buying blind.")
        return None
    threshold = calibration.price_diff_threshold(core)
    if threshold is None:
        print(f"  {core} has no price_diff_threshold in config.json, so "
              f"there is no gap it is worth buying at. Not buying.")
        return None
    diff = set_row["unit_price"] - core_row["unit_price"]
    print(f"  {set_name} {set_row['unit_price']:,} - {core} "
          f"{core_row['unit_price']:,} = {diff:,} "
          f"(threshold {threshold:,})")
    if diff <= threshold:
        print(f"  the gap does not clear the threshold; not buying.")
        return None

    free_rows = [i for i in model.empty() if first <= i <= last]
    if not free_rows:
        print(f"  rows {first}-{last} are full, and a crafted {set_name} with "
              f"nowhere to list stays on tab {row_model.WORK_TAB}; not "
              f"buying.")
        return None
    print(f"  {len(free_rows)} row(s) free inside {first}-{last}")

    target = -(-int(want_min) // batch) * batch
    if target != want_min:
        print(f"  buy_min {want_min} is not a whole number of batches of "
              f"{batch}; buying {target}")
    bought = orders = paid = 0

    def order(want):
        nonlocal orders
        orders += 1
        for attempt in range(1, int(run["buy_retries"]) + 1):
            try:
                with calibration.phase(f"buy order {orders}"):
                    return buy.buy_row_one(slot, want, verbose=verbose,
                                           held=bought, floor_qty=target,
                                           ceiling=want_max)
            except buy.Refused as exc:
                if not getattr(exc, "retryable", False):
                    print(f"  stopping: {exc}")
                    return None
                print(f"  attempt {attempt}/{run['buy_retries']}: {exc}")
        print(f"  the board kept moving through {run['buy_retries']} "
              f"attempt(s); giving up on {core} this cycle.")
        return None

    while bought < target:
        print(f"  {bought}/{target} {core} held")
        got = order(target - bought)
        if got is None or got["bought"] <= 0:
            break
        bought += got["bought"]
        paid += got["spent"]

    while bought % batch:
        short = batch - (bought % batch)
        print(f"  {bought} {core} is {short} short of a whole batch of "
              f"{batch}; topping up")
        got = order(short)
        if got is None or got["bought"] <= 0:
            break
        bought += got["bought"]
        paid += got["spent"]

    if bought <= 0:
        print(f"  nothing bought; not opening the craft window.")
        return None
    if bought % batch:
        print(f"  {bought} {core} does not divide by {batch} and could not be "
              f"topped up; they stay on tab {row_model.WORK_TAB}.")
        return None

    with calibration.phase("close the Agent Shop"):
        calibration.close_everything()
    with calibration.phase(f"craft {core} into {set_name}"):
        made = craft.craft_sets(verbose=verbose)
    with calibration.phase("close the craft window"):
        craft.close_craft()

    with calibration.phase("reopen the Agent Shop"):
        if not back_to_the_shop(verbose=verbose):
            raise NotReady("the Agent Shop would not reopen after crafting.")
    with calibration.phase("select the Register tab"):
        register_tab(verbose=verbose)
    with calibration.phase(f"select inventory tab {row_model.WORK_TAB}"):
        calibration.click(*calibration.inventory_tab_point(row_model.WORK_TAB),
                          settle=0.0)
        time.sleep(row_model.TAB_SETTLE)

    work = tuple(made["slot"])
    if work not in calibration.occupied_slots():
        raise NotReady(
            f"tab {row_model.WORK_TAB} slot {work}, where the {set_name} was "
            f"compressed, is empty; there is nothing to list.")
    unit_cost = -(-paid // bought) if bought else core_row["unit_price"]
    why = f"the {unit_cost:,} a {core} cost, a Set at a time"
    print(f"  listing the {set_name} compressed into {work}")
    print(f"  the board's cheapest is {set_row['unit_price']:,} a Set, and the "
          f"panel scales that to whatever the bundle holds")
    print(f"  it cost {unit_cost:,} a Core, so no Set goes out under that")

    rows, listed_total = [], 0
    while work in calibration.occupied_slots():
        empty = [i for i in model.empty() if first <= i <= last]
        if not empty:
            print(f"  rows {first}-{last} are full; what is left of the "
                  f"{set_name} stays on tab {row_model.WORK_TAB}.")
            break
        lands_in = min(empty)
        with calibration.phase(f"list {set_name} from {work}"):
            listed = model.list_slot(*work, why=why, verbose=verbose,
                                     lands_in=lands_in,
                                     unit_market=set_row["unit_price"],
                                     floor_each=unit_cost)
        model._slots[lands_in] = row_model.Row(set_name, qty=listed["qty"],
                                               price=listed["price"])
        rows.append(lands_in)
        listed_total += listed["qty"]
    calibration.phases_table(
        f"resupply {core}: bought {bought}, crafted {made['made']}, "
        f"listed {listed_total} in rows {rows}")
    return {"slot": slot, "core": core, "set": set_name, "diff": diff,
            "bought": bought, "crafted": made["made"],
            "listed": listed_total, "rows": rows}


def rest_the_game(verbose=True):
    print("")
    print(f"  returning the game to its default state")
    calibration.close_everything(verbose=verbose)
    if not back_to_the_shop(verbose=verbose):
        raise NotReady("the Agent Shop would not reopen after resting.")
    register_tab(verbose=verbose)


def back_to_the_shop(verbose=True):
    from open_inventory import VK_ESCAPE, press
    vendor = calibration.vendor_open()
    if vendor:
        press(VK_ESCAPE)
        time.sleep(row_model.ACTION_GAP)
    if calibration._trade_window_open():
        return True
    if verbose:
        print(f"  the Agent Shop is shut"
              + (" after the vendor" if vendor else
                 " and the vendor is not open either")
              + "; reopening it")
    shop.open_agent_shop(verbose=False)
    time.sleep(row_model.TAB_SETTLE)
    return calibration._trade_window_open()


def resupply_pass(model, first, last, verbose=True):
    run = calibration.load_shared()["resupply"]
    if not run["enabled"]:
        return []
    held = rows_by_core(model, first, last)
    print("")
    print(f"  counting only rows {first}-{last}; rows outside it are not "
          f"repriced and do not count")
    print(f"  {'core':<30}{'rows':>6}{'wants':>7}   short?")
    short = []
    for slot, count in sorted(held.items()):
        core = calibration.FAVOURITE_ITEMS[str(slot)]
        wants = calibration.rows_threshold(core)
        mark = ""
        if wants is not None and count < wants:
            if not convert.cell_for(core) and not craft_route(core):
                mark = "neither convertible nor craftable"
            elif not buying_enabled(core):
                mark = "buying off"
            else:
                mark = "YES"
                short.append(slot)
        print(f"  {core:<30}{count:>6}{('-' if wants is None else wants):>7}"
              f"   {mark}")
    if not short:
        print("")
        print(f"  nothing inside rows {first}-{last} is short of its row "
              f"threshold and convertible.")
        return []
    done = []
    try:
        for slot in short:
            war.avoid(allowance=PASS_ALLOWANCE, verbose=verbose)
            try:
                core_here = calibration.FAVOURITE_ITEMS[str(slot)]
                route = (resupply_chaos if craft_route(core_here)
                         else resupply_one)
                out = route(model, slot, held[slot], first, last,
                            verbose=verbose)
            except (convert.Refused, craft.Refused, buy.Refused,
                    NotReady) as exc:
                print(f"  resupply of "
                      f"{calibration.FAVOURITE_ITEMS[str(slot)]!r} stopped: "
                      f"{exc}")
                out = None
            if out:
                done.append(out)
    finally:
        if not back_to_the_shop(verbose=verbose):
            raise NotReady("the Agent Shop is not open after resupplying.")
        register_tab(verbose=verbose)
    return done


def do_scan(verbose=True):
    initialise(verbose=verbose)
    print(f"  balance {balance() or 'unreadable'}")
    model = seed(verbose=verbose)
    report(model)
    return model


def usage():
    print("usage:")
    print("  py src/driver.py                 relist rows N-M for the minutes")
    print("                                   in config.json, resupplying any")
    print("                                   core that runs short if")
    print("                                   resupply.enabled is on")
    print("  py src/driver.py resupply N      price favourite slot N against")
    print("                                   its Set and, if the gap clears,")
    print("                                   buy, convert and list it; skips")
    print("                                   the row count and enable_buying")
    print("  py src/driver.py scan            read the balance, walk rows 1-21")
    print("                                   and print the model, no changes")
    print("  py src/driver.py cancel N        cancel row N (collects it first")
    print("                                   if it has sold)")
    print("  py src/driver.py relist [N M [MIN]]  cancel and relist rows N-M,")
    print("                                   looping for MIN minutes;")
    print("                                   the run block in config.json")
    print("                                   if no range is given")
    print("  py src/driver.py list R C [PRICE] list inventory slot (R,C); the")
    print("                                   panel's own suggestion if no PRICE")
    print("  py src/driver.py row N           read row N without touching it")
    print("  py src/driver.py price N         market price for favourite slot N")
    print("  py src/driver.py alz             read the balance")


def main():
    import traceback
    args = [a for a in sys.argv[1:] if a != "--frames"]
    calibration.log_to_file(args[0].lower() if args else "run")
    print(f"  ledger {ledger.DB} run {ledger.start()}")
    calibration.frames_on(True if "--frames" in sys.argv[1:] else None)
    outcome, note = "FINISHED", ""
    try:
        _dispatch(args)
    except KeyboardInterrupt:
        outcome, note = "STOPPED", "interrupted from the keyboard"
    except BaseException as exc:
        outcome = "CRASHED"
        note = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        try:
            ledger.print_run_profit()
        except BaseException as exc:
            print(f"  the profit summary could not be built: "
                  f"{type(exc).__name__}: {exc}")
        calibration.end_banner(outcome, note)
    if outcome == "CRASHED":
        sys.exit(1)


def _dispatch(args):
    if not args:
        do_relist()
        return
    what = args[0].lower()
    if what == "cancel" and len(args) > 1:
        do_cancel(int(args[1]))
    elif what == "relist":
        do_relist(args[1] if len(args) > 1 else None,
                  args[2] if len(args) > 2 else None,
                  args[3] if len(args) > 3 else None)
    elif what == "list" and len(args) > 2:
        do_list(int(args[1]), int(args[2]),
                int(args[3]) if len(args) > 3 else None)
    elif what == "row" and len(args) > 1:
        initialise()
        register_tab()
        row_at(row_model.RowModel().seed({}), int(args[1]))
    elif what == "resupply" and len(args) > 1:
        do_resupply(int(args[1]))
    elif what == "chaos":
        crafted = [n for n in core_slots()
                   if craft_route(calibration.FAVOURITE_ITEMS[str(n)])]
        if not crafted:
            print("  nothing in the favourites is crafted rather than "
                  "converted; there is no chaos resupply to run.")
            return
        do_resupply(crafted[0])
    elif what == "scan":
        do_scan()
    elif what == "price" and len(args) > 1:
        initialise()
        market(int(args[1]))
    elif what == "alz":
        initialise()
        print(f"  balance {balance() or 'unreadable'}")
    else:
        usage()
        sys.exit(2)


if __name__ == "__main__":
    main()
