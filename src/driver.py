import re
import sys
import time

import buy
import calibration
import convert
import get_alz
import get_price
import row_model
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
            "the Trade window would not open. Refusing to click Trade window "
            "coordinates while the game world is underneath them.")
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
    return get_price.get_price(slot, verbose=verbose)


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
                print(f"    {index:2}  UNREAD {text[:56]!r}")
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
    qty = int(found.group("qty").replace(",", ""))
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
        if verbose:
            print(f"  row {index} is empty; nothing to relist")
        return None

    if row is None or button != row_model.CHANGE_WORD:
        calibration.snap(f"row_{index}_button_disagrees")
        print(f"  row {index}: SKIPPED and it should not have been. The row "
              f"reads {text[:60]!r}")
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
    model.cancel(index, verbose=False, tab_ready=True)
    calibration.click(*calibration.inventory_tab_point(row_model.WORK_TAB))
    unit_floor, pair = calibration.price_floor(row.name)
    if unit_floor is None:
        print(f"  row {index}: {row.name!r} is floored by a {pair}, whose "
              f"price did not read this run. Leaving it listed at "
              f"{row.price:,} rather than relisting with no floor.")
        return None
    pack = row.pack
    floor = unit_floor * pack
    why = ""
    if unit_floor:
        why = f"a {pair} costs {unit_floor:,}"
        if pack > 1:
            why += f", and this listing carries {pack}"
    out = model.list_slot(*landing, floor=floor, why=why, verbose=verbose)
    if verbose:
        print(f"    relisted {out['qty']} at {out['price']:,}")
    return out


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
    model = row_model.RowModel().seed({})

    deadline = time.monotonic() + minutes * 60
    print(f"relisting rows {first}-{last} for {minutes:g} minute(s)")
    passes = done = skipped = 0
    started = time.perf_counter()
    while True:
        passes += 1
        print("")
        print(f"-- pass {passes} --")
        try:
            made, missed = relist_pass(model, first, last, verbose=verbose)
        except row_model.Divergence as exc:
            print(f"  STOPPED: {exc}")
            break
        done += made
        skipped += missed
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
        print(f"  row {index}: {text[:66]!r}")
        print(f"    function {row_model.row_function(text)!r}  "
              f"complete {row_model.row_complete(text)}")
    return text, row


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


def rows_by_core(model, first, last):
    held = {slot: 0 for slot in core_slots()}
    for index, row in (model._slots or {}).items():
        if row is None or not first <= index <= last:
            continue
        slot = calibration.favourite_slot_of(row.name)
        if slot in held:
            held[slot] += 1
    return held


def resupply_one(slot, held, verbose=True):
    run = calibration.load_shared()["resupply"]
    core = calibration.FAVOURITE_ITEMS[str(slot)]
    pair = calibration.pair_slot(slot)
    set_name = calibration.FAVOURITE_ITEMS[str(pair)]
    print("")
    print(f"-- {core}: {held} row(s), threshold {run['rows_threshold']} --")

    core_row = get_price.get_price(slot, verbose=False)
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

    calibration.click(*calibration.inventory_tab_point(
        calibration.CONVERT_INVENTORY_TAB))
    time.sleep(row_model.TAB_SETTLE)

    bought = 0
    while bought < run["buy_min"]:
        print(f"  {bought}/{run['buy_min']} {set_name} held")
        try:
            got = buy.buy_row_one(pair, run["buy_min"] - bought,
                                  verbose=verbose)
        except buy.Refused as exc:
            print(f"  stopping: {exc}")
            break
        if got["bought"] <= 0:
            print(f"  the last order bought nothing; stopping.")
            break
        bought += got["bought"]
    if bought <= 0:
        print(f"  nothing bought; not opening the vendor.")
        return None
    if bought < run["buy_min"]:
        print(f"  bought {bought} of the {run['buy_min']} wanted.")

    print(f"  closing the Agent Shop to open the vendor")
    calibration.close_everything()
    convert.open_vendor(verbose=verbose)
    out = convert.convert(core, bought, verbose=verbose)
    return {"slot": slot, "core": core, "set": set_name, "diff": diff,
            "bought": bought, "converted": out["converted"]}


def do_resupply(first=None, last=None, verbose=True):
    shared = calibration.load_shared()
    run, rows = shared["resupply"], shared["run"]
    if not run["enabled"]:
        print("  resupply is off in config.json (resupply.enabled). The "
              "conversion vendor is not measured when it is off, so there is "
              "nothing to convert with.")
        return []
    first = rows["relist_from"] if first is None else int(first)
    last = rows["relist_to"] if last is None else int(last)
    initialise(verbose=verbose)
    model = seed(verbose=verbose)
    held = rows_by_core(model, first, last)
    print("")
    print(f"  counting only rows {first}-{last}; rows outside it are not "
          f"repriced and do not count")
    print(f"  {'core':<30}{'rows':>6}   short of {run['rows_threshold']}")
    for slot, count in sorted(held.items()):
        core = calibration.FAVOURITE_ITEMS[str(slot)]
        mark = ""
        if count < run["rows_threshold"]:
            mark = "YES" if convert.cell_for(core) else "not convertible"
        print(f"  {core:<30}{count:>6}   {mark}")
    short = [slot for slot, count in sorted(held.items())
             if count < run["rows_threshold"]
             and convert.cell_for(calibration.FAVOURITE_ITEMS[str(slot)])]
    if not short:
        print("")
        print(f"  nothing inside rows {first}-{last} is both short of "
              f"{run['rows_threshold']} row(s) and convertible.")
        return []
    done = []
    for slot in short:
        out = resupply_one(slot, held[slot], verbose=verbose)
        if out:
            done.append(out)
    return done


def do_scan(verbose=True):
    initialise(verbose=verbose)
    print(f"  balance {balance() or 'unreadable'}")
    model = seed(verbose=verbose)
    report(model)
    return model


def usage():
    print("usage:")
    print("  py src/driver.py                 open the shop, read the balance,")
    print("                                   walk rows 1-21, print the model")
    print("  py src/driver.py cancel N        cancel row N (collects it first")
    print("                                   if it has sold)")
    print("  py src/driver.py relist [N M [MIN]]  cancel and relist rows N-M,")
    print("                                   looping for MIN minutes;")
    print("                                   the run block in config.json")
    print("                                   if no range is given")
    print("  py src/driver.py list R C [PRICE] list inventory slot (R,C); the")
    print("                                   panel's own suggestion if no PRICE")
    print("  py src/driver.py resupply        buy Sets for any core short of")
    print("                                   rows and convert them; the")
    print("                                   resupply block in config.json")
    print("  py src/driver.py row N           read row N without touching it")
    print("  py src/driver.py price N         market price for favourite slot N")
    print("  py src/driver.py alz             read the balance")


def main():
    args = [a for a in sys.argv[1:] if a != "--frames"]
    calibration.log_to_file(args[0].lower() if args else "scan")
    calibration.frames_on(True if "--frames" in sys.argv[1:] else None)
    if not args:
        do_scan()
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
    elif what == "resupply":
        do_resupply(args[1] if len(args) > 1 else None,
                    args[2] if len(args) > 2 else None)
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
