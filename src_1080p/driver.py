import datetime
import json
import os
import random
import re
import subprocess
import sys
import time

def _plain_argv():
    out, skip = [], False
    for arg in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if arg == "--config":
            skip = True
            continue
        if arg.startswith("--config=") or arg == "--frames":
            continue
        out.append(arg)
    return out


def _chosen_config():
    for index, arg in enumerate(sys.argv):
        if arg == "--config" and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
        if arg.startswith("--config="):
            return arg.split("=", 1)[1]
    return ""


os.environ["CABAL_CONFIG"] = _chosen_config()

import calibration

if __name__ == "__main__":
    calibration.log_to_file(next((a.lower() for a in _plain_argv()), "run"))

import buy
import cashshop
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
WITHDRAW_SETTLE = _T["withdraw_settle"]
_ROW = re.compile(_SHARED["text"]["purchase_row"])
_GROUPING = re.compile(_SHARED["text"]["row_grouping"])
MIN_PLAUSIBLE_PRICE = _SHARED["detect"]["min_plausible_price"]
CAPACITY = _SHARED["game_facts"]["shop_capacity"]
VISIBLE = _SHARED["game_facts"]["shop_visible"]
USE_LINE_F = tuple(_SHARED["regions"]["use_question_line"])
USE_YES_F = tuple(_SHARED["regions"]["use_yes"])
USE_QUESTIONS = _SHARED["text"]["use_question"]
if isinstance(USE_QUESTIONS, str):
    USE_QUESTIONS = [USE_QUESTIONS]
YES_WORD = _SHARED["text"]["yes_word"]


class NotReady(Exception):
    pass


_MEASURED = False
_PENDING = None


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


def trace_path():
    log = calibration.log_to_file()
    return log.with_name(f"{log.stem}_board.log")


def row_gain(row):
    if not row.buy_cost or not row.sell_unit:
        return None
    return (row.sell_unit - row.buy_cost) * row.units


def trace_line(index, row, here):
    gain = row_gain(row)
    mark = " <- here" if here else ""
    return (f"{board_line(index, row)}"
            f"{(f'{gain:,}' if gain is not None else '-'):>16}{mark}")


def trace_empty(index, here):
    mark = " <- here" if here else ""
    return (f"    {index:2}  {'(empty)':34} {'':5} {'-':>10} {'-':>10} "
            f"{'-':>7} {'-':>14}{'-':>16}{mark}")


def board_trace(model, passes, index, first, last):
    if not _SHARED["debug"]["board_trace"]:
        return
    try:
        _board_trace(model, passes, index, first, last)
    except Exception as exc:
        print(f"    the board trace failed ({type(exc).__name__}: {exc}); "
              f"the pass carries on")


def _board_trace(model, passes, index, first, last):
    at = time.strftime("%Y-%m-%dT%H:%M:%S")
    lines = [f"  board during pass {passes}, at row {index} of {first}-{last}, "
             f"read {at}", board_header() + f"{'profit if sold':>16}"]
    by_item, gains = {}, {}
    for seat in sorted(set(model.occupied()) | {index}):
        row = model.get(seat)
        if row is None:
            lines.append(trace_empty(seat, seat == index))
            continue
        lines.append(trace_line(seat, row, seat == index))
        item = row_model._PACK.sub("", row.name).strip()
        rows, units, listed = by_item.get(item, (0, 0, 0))
        by_item[item] = (rows + 1, units + row.units, listed + row.sell_total)
        gain = row_gain(row)
        if gain is not None:
            gains[item] = gains.get(item, 0) + gain
    if by_item:
        lines.append(f"    {'item':34} rows   units          listed"
                     f"{'profit if sold':>16}")
        for item, (rows, units, listed) in sorted(by_item.items()):
            gain = gains.get(item)
            lines.append(f"    {item[:34]:34} {rows:4} {units:7,} "
                         f"{listed:>15,}"
                         f"{(f'{gain:,}' if gain is not None else '-'):>16}")
        lines.append(f"    {'board':34} {model.used():4} "
                     f"{sum(u for _, u, _ in by_item.values()):7,} "
                     f"{sum(l for _, _, l in by_item.values()):>15,}"
                     f"{sum(gains.values()):>16,}")
    else:
        lines.append(f"    the board is empty")
    if get_alz.LAST.get("alz"):
        lines.append(f"    balance now {get_alz.LAST['alz']:,}, read "
                     f"{get_alz.LAST['at']}")
    held = [(slot, what) for slot, what in sorted(model._work.items())
            if what is not None]
    if held:
        lines.append(f"    held on tab {row_model.WORK_TAB}, bought and not "
                     f"listed yet:")
        for slot, what in held:
            name = getattr(what, "name", what)
            units = getattr(what, "units", None)
            cost = getattr(what, "buy_cost", None)
            lines.append(f"    {str(name)[:34]:34} slot {slot} "
                         f"{(f'{units:,}' if units else '-'):>9} unit(s) "
                         f"{(f'{cost:,}' if cost else '-'):>12} a unit")
    path = trace_path()
    path.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")


def board_report(model, passes):
    try:
        _board_report(model, passes)
    except Exception as exc:
        print(f"    the board report failed ({type(exc).__name__}: {exc}); "
              f"the pass carries on")


def board_header():
    return (f"    {'':2}  {'':34} {'':5} {'bought/u':>10} {'listed/u':>10} "
            f"{'margin':>7} {'row price':>14}")


def board_line(index, row):
    bought = f"{row.buy_cost:,}" if row.buy_cost else "-"
    margin = (f"{(row.sell_unit - row.buy_cost) / row.sell_unit:+.1%}"
              if row.buy_cost and row.sell_unit else "-")
    return (f"    {index:2}  {row.name[:34]:34} x{row.qty:<4} {bought:>10} "
            f"{row.sell_unit:>10,} {margin:>7} {row.price:>14,}")


def _board_report(model, passes):
    lines = ["", f"  board after pass {passes}:", board_header()]
    by_item = {}
    for index in model.occupied():
        row = model.get(index)
        lines.append(board_line(index, row))
        item = row_model._PACK.sub("", row.name).strip()
        rows, units, listed = by_item.get(item, (0, 0, 0))
        by_item[item] = (rows + 1, units + row.units, listed + row.sell_total)
    if by_item:
        lines.append(f"    {'item':34} rows   units          listed")
        for item, (rows, units, listed) in sorted(by_item.items()):
            lines.append(f"    {item[:34]:34} {rows:4} {units:7,} "
                         f"{listed:>15,}")
        lines.append(f"    {'board':34} {model.used():4} "
                     f"{sum(u for _, u, _ in by_item.values()):7,} "
                     f"{sum(l for _, _, l in by_item.values()):>15,}")
    else:
        lines.append(f"    the board is empty")
    for line in lines:
        print(line)
    alz = get_alz.read_balance()
    if alz is None:
        print(f"    balance now: could not be read")
    else:
        print(f"    balance now {alz:,}")


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


def price_by_voucher(row):
    if row is None or row.buy_cost:
        return row
    if calibration.voucher_floor_ratio(row.name)[1] <= 0:
        return row
    floor, _why = calibration.price_floor(row.name)
    if floor:
        row.buy_cost = floor
        row.floor_at = floor
    return row


def seed(verbose=True):
    register_tab(verbose=verbose)
    last = CAPACITY if row_model.last_seat_placed() else row_model.MAX_TOP
    model = row_model.RowModel().seed({})
    model.home(verbose=verbose)
    found = {}
    if verbose:
        print(board_header())
    for index in range(1, last + 1):
        model.scroll_to(index, verbose=False)
        if model.button() == row_model.REGISTER_WORD:
            continue
        text = model.read()
        if model.is_empty(text):
            continue
        text, row = row_from_screen(text, model)
        if row is None:
            if verbose:
                print(f"    {index:2}  UNREAD {text!r}")
            continue
        price_by_voucher(row)
        found[index] = row
        if verbose:
            print(board_line(index, row))
    model.seed(found, top=model.top)
    model.tracked = True
    model.save()
    model.home(verbose=verbose)
    if verbose:
        print(f"  seeded {len(found)} of rows 1-{last}")
        if last < CAPACITY:
            print(f"  rows {last + 1}-{CAPACITY} were not read; the last "
                  f"row is not placed in calibration.json, so position "
                  f"{VISIBLE} cannot be read")
    return model


def _parsed_row(text):
    found = _ROW.match(text)
    if found is None:
        return None
    seen = re.sub(r"[^0-9]", "", found.group("qty"))
    name = found.group("name")
    if not seen:
        name = f"{name} {found.group('qty')}"
        print(f"    the quantity column read {found.group('qty')!r}, which "
              f"holds no digits, so it is the end of the name and not a "
              f"count; reading the row as {name.strip(' |-)(')!r} and letting "
              f"the panel count what is there")
    qty = int(seen) if seen else 1
    price = int(re.sub(r"[^0-9]", "", found.group("price")))
    if price < MIN_PLAUSIBLE_PRICE:
        return None
    return row_model.Row(
        row_model.canonical(name.strip(" |-)(")),
        qty=qty if qty >= 1 else 1,
        price=price)


def _row_from(text):
    cleaned = (text or "").strip()
    row = _parsed_row(cleaned)
    if row is not None:
        return row
    regrouped = _GROUPING.sub(",", cleaned)
    return _parsed_row(regrouped) if regrouped != cleaned else None


def known_item(name):
    return (calibration.favourite_slot_of(name) is not None
            or calibration.voucher_floor_ratio(name)[0] is not None
            or any(cashshop.matches(item, name) for item in cashshop.items()))


def row_from_screen(text, model):
    row = _row_from(text)
    if row is not None and known_item(row.name):
        return text, row
    if row is None and model.is_empty(text):
        return text, None
    tries = 1 if row is not None else row_model.PANEL_REREADS + 1
    for attempt in range(1, tries + 1):
        stacked = model.read_stacked()
        again = _row_from(stacked)
        if again is not None and known_item(again.name):
            print(f"    one line read {text!r}; read as stacked lines it is "
                  f"{stacked!r}")
            return stacked, again
        if attempt < tries:
            time.sleep(row_model.PANEL_REREAD_GAP)
    return text, row


def cancel(model, index, verbose=True):
    return model.cancel(index, verbose=verbose)


def task(kind, **fields):
    print("TASK " + json.dumps({"kind": kind, **fields}), flush=True)


def task_done(kind, **fields):
    print("DONE " + json.dumps({"kind": kind, **fields}), flush=True)


def note_step(job, holding=None, qty=0, price=0, tab=None):
    task("resupply", core=job.get("core"), set=job.get("set"),
         slot=job.get("slot"), tab=tab or row_model.WORK_TAB,
         step=job.get("step"), holding=holding,
         qty=int(qty or 0), price=int(price or 0))


def _after_the_lag(nothing_done, verbose=True):
    try:
        return bool(calibration.wait_out_server_lag(verbose=verbose))
    except RuntimeError as exc:
        raise NotReady(f"{exc} {nothing_done}")


def pending_holds_stock():
    return bool(_PENDING is not None and _PENDING.get("bought")
                and _PENDING.get("step") != "buy")


def cash_item_of(name):
    for item in cashshop.items():
        if cashshop.rows_wanted(item) > 0 and cashshop.matches(item, name):
            return item
    return None


def restock_now(model, name, first, last, verbose=True):
    model.work_seen = None
    if not calibration.load_shared()["resupply"]["enabled"]:
        return
    item = cash_item_of(name)
    if item is not None:
        restock_cash_now(model, item, first, last, verbose=verbose)
        return
    if row_model.item_key(name) in special_names():
        print(f"  the {name} special row sold; buying it back now, not on "
              f"the next pass")
        special_pass(model, first, last, verbose=verbose)
        if not back_to_the_shop(verbose=verbose):
            raise NotReady(f"the Agent Shop is not open after the {name} "
                           f"special row.")
        register_tab(verbose=verbose)
        return
    slot = counts_toward().get(calibration.favourite_slot_of(name))
    if slot is None:
        return
    core = calibration.FAVOURITE_ITEMS[str(slot)]
    if craft_route(core) and buying_enabled(core):
        restock_core_now(model, slot, name, first, last, verbose=verbose)


def restock_cash_now(model, item, first, last, verbose=True):
    print(f"  {item} sold and is collected; resupplying it now, not on the "
          f"next pass")
    done = []
    try:
        resupply_cash_rows(model, item, cashshop.rows_wanted(item), first,
                           last, done, verbose=verbose)
    finally:
        if not back_to_the_shop(verbose=verbose):
            raise NotReady(f"the Agent Shop is not open after resupplying "
                           f"{item}.")
        register_tab(verbose=verbose)


def restock_core_now(model, slot, name, first, last, verbose=True):
    core = calibration.FAVOURITE_ITEMS[str(slot)]
    have = rows_by_core(model, first, last).get(slot, 0)
    most = calibration.rows_wanted_at_most(core)
    if most is not None and have >= most:
        return
    print(f"  a {name!r} row sold and is collected; {core} holds {have} "
          f"row(s), pricing and resupplying it now, not on the next pass")
    done = []
    try:
        war.avoid(allowance=PASS_ALLOWANCE, verbose=verbose)
        core_row, set_row, diff = price_gap(slot)
        if diff is None:
            return
        wants = calibration.rows_by_margin(core, diff)
        if have >= wants:
            print(f"  a margin of {diff:,} is worth {wants} row(s) of "
                  f"{core}; {have} held; not buying.")
            return
        resupply_core_rows(model, slot, have, wants, {slot: core_row},
                           first, last, done, verbose=verbose,
                           rows=(core_row, set_row))
    finally:
        if not back_to_the_shop(verbose=verbose):
            raise NotReady(f"the Agent Shop is not open after resupplying "
                           f"{core}.")
        register_tab(verbose=verbose)


def reconcile_work_tab(model, verbose=True):
    if not row_model.WORK_TAB_STALE:
        return
    model.work_seen = None
    held = work_tab_slots(verbose=verbose)
    if work_tab_in_transit(held, verbose=verbose):
        return
    new, gone = model.reconcile_work_tab(held)
    row_model.WORK_TAB_STALE = False
    if verbose:
        print(f"  tab {row_model.WORK_TAB} read from the screen after the "
              f"stall: {len(held)} slot(s) held"
              + (f", {new} new to the run" if new else "")
              + (f", {gone} the run thought it held are empty" if gone
                 else ""))


def tab_before_withdrawal(model, verbose=True):
    held = model.work_seen
    model.work_seen = None
    began = time.perf_counter()
    fresh = held is None
    if fresh:
        with calibration.phase(f"read tab {row_model.WORK_TAB} before the "
                               f"withdrawal"):
            held = work_tab_slots(verbose=verbose)
    full = row_model.GRID * row_model.GRID
    if len(held) >= full:
        time.sleep(row_model.TAB_SETTLE)
        held = calibration.occupied_slots()
    if len(held) >= full:
        print(f"  tab {row_model.WORK_TAB} reads full, which is how the client "
              f"draws a withdrawal in transit; the run trusts its own map "
              f"for this row")
        return None
    new, gone = model.reconcile_work_tab(held)
    if verbose:
        took = (time.perf_counter() - began) * 1000
        print(f"  tab {row_model.WORK_TAB} "
              + (f"read from the screen in {took:.0f} ms" if fresh
                 else "known from the last listing")
              + f": {len(held)} slot(s) held"
              + (f", {new} the run did not know of" if new else "")
              + (f", {gone} the run thought it held are empty" if gone
                 else ""))
    return set(held)


def sweep_work_tab(model, first, last, verbose, expect, record):
    name = expect["expect_item"]
    while True:
        held = sorted(work_tab_slots(verbose=verbose))
        if not held:
            print(f"  tab {row_model.WORK_TAB} is empty again")
            return
        free = [i for i in model.empty() if first <= i <= last]
        if not free:
            for slot in held:
                model.hold_work(slot, None)
            print(f"  rows {first}-{last} are full; {len(held)} slot(s) stay "
                  f"on tab {row_model.WORK_TAB} until a row frees")
            return
        slot, lands_in = held[0], min(free)
        print(f"  listing what tab {row_model.WORK_TAB} slot {slot} holds, "
              f"expecting the {name!r} that came off the row")
        with calibration.phase(f"list tab {row_model.WORK_TAB} slot {slot}"):
            out = model.list_slot(*slot, verbose=verbose, lands_in=lands_in,
                                  **expect)
        record(out, lands_in)
        task_done("list", tab=row_model.WORK_TAB, slot=list(slot),
                  qty=out["qty"], price=out["price"], item=out["item"])


def listing_floor(what):
    unit_floor, pair = calibration.price_floor(what.name)
    cost = what.floor_at or what.buy_cost
    if cost:
        unit_floor, pair = cost, "what it cost"
    whole = calibration.voucher_floor_ratio(what.name)[1] > 0
    pack = 1 if whole else what.pack
    why = ""
    if unit_floor:
        why = (f"it is worth {pair}" if whole
               else f"it cost {unit_floor:,} each" if cost
               else f"a {pair} costs {unit_floor:,}")
        if pack > 1:
            why += f", and this listing carries {pack}"
    return unit_floor * pack, why


def work_tab_in_transit(held, verbose=True):
    full = row_model.GRID * row_model.GRID
    if len(held) < full:
        return False
    if verbose:
        print(f"  tab {row_model.WORK_TAB} reads full, which is how the "
              f"client draws a withdrawal in transit; it is read again "
              f"before anything is taken from it")
    row_model.WORK_TAB_STALE = True
    return True


def stranded_work(model):
    return [slot for slot, what in sorted(model._work.items())
            if isinstance(what, row_model.Row)]


def resume_work_tab(model, first, last, verbose=True):
    if not stranded_work(model):
        return 0
    with calibration.phase(f"read tab {row_model.WORK_TAB} before resuming"):
        held = work_tab_slots(verbose=verbose)
    if work_tab_in_transit(held, verbose=verbose):
        return 0
    new, gone = model.reconcile_work_tab(held)
    row_model.WORK_TAB_STALE = False
    model.work_seen = set(held)
    if verbose:
        print(f"  tab {row_model.WORK_TAB} read before resuming: "
              f"{len(held)} slot(s) held"
              + (f", {new} the run did not know of" if new else "")
              + (f", {gone} the run thought it held are empty" if gone
                 else ""))
    done = 0
    for slot in stranded_work(model):
        what = model._work[slot]
        free = [i for i in model.empty() if first <= i <= last]
        if not free:
            print(f"  rows {first}-{last} are full, so tab "
                  f"{row_model.WORK_TAB} slot {slot} waits for one to free")
            break
        lands_in = min(free)
        floor, why = listing_floor(what)
        print(f"  tab {row_model.WORK_TAB} slot {slot} still holds the "
              f"{what.name!r} x{what.qty} a row was cancelled into at "
              f"{what.price:,}; it goes back into row {lands_in} before "
              f"anything else is withdrawn")
        task("resume", tab=row_model.WORK_TAB, slot=list(slot),
             item=what.name, lands_in=lands_in)
        try:
            with calibration.phase(f"resume tab {row_model.WORK_TAB} slot "
                                   f"{slot}"):
                out = model.list_slot(
                    *slot, verbose=verbose, lands_in=lands_in, floor=floor,
                    why=why, expect_item=what.name, listed_at=what.price,
                    expect_qty=what.qty,
                    expect_market=(row_model.market_anchor(what.name)
                                   * what.pack) or None)
        except (row_model.SlotNeverFilled, row_model.NothingLoaded) as exc:
            model.release_work(slot)
            row_model.WORK_TAB_STALE = True
            print(f"  nothing came out of tab {row_model.WORK_TAB} slot "
                  f"{slot} ({exc}); the next withdrawal reads the tab again")
            continue
        mine = not out["resolved"]
        model.place(lands_in, row_model.Row(
            out["item"] or what.name, qty=out["qty"], price=out["price"],
            buy_cost=what.buy_cost if mine else 0,
            floor_at=what.floor_at if mine else 0))
        task_done("resume", tab=row_model.WORK_TAB, slot=list(slot),
                  lands_in=lands_in, qty=out["qty"], price=out["price"],
                  item=out["item"])
        done += 1
    return done


def relist_one(model, index, verbose=True, first=None, last=None,
               collect_only=False):
    run = calibration.load_shared()["run"]
    first = int(run["relist_from"] if first is None else first)
    last = int(run["relist_to"] if last is None else last)
    with calibration.phase(f"{calibration.REFRESH_WORD} the table"):
        row_model.refresh_table(model, verbose=False)
    with calibration.phase("scroll to the row and read it"):
        model.scroll_to(index, verbose=False)
        button = model.button() if model.get(index) is None else None
        if button == row_model.REGISTER_WORD:
            text, row = "", None
        else:
            text = model.read()
            text, row = row_from_screen(text, model)
            button = model.button()

    if button == row_model.RECEIPT_WORD:
        complete = model.complete(text)
        if verbose:
            print(f"  row {index} has SOLD "
                  f"({'fully' if complete else 'partly'}); collecting")
        held = model.get(index)
        sold = held.name if held is not None else (
            row.name if row is not None else text)
        with calibration.phase("collect what sold"):
            model.receive(index, verbose=False)
        with calibration.phase("read the row again after collecting"):
            text, row = row_at(model, index, verbose=False)
            button = model.button()
        if complete or button == row_model.REGISTER_WORD or row is None:
            model.drop(index)
            model.forget_floor(index)
            if verbose:
                print(f"    collected; row {index} is empty, nothing to "
                      f"relist")
            restock_now(model, sold, first, last, verbose=verbose)
            return None
        if verbose:
            print(f"    collected; {row.qty} left to relist")

    if model.is_empty(text) or button == row_model.REGISTER_WORD:
        model.drop(index)
        model.forget_floor(index)
        if verbose:
            print(f"  row {index} is empty; nothing to relist")
        return None

    if collect_only:
        return False

    if row is None or button != row_model.CHANGE_WORD:
        calibration.snap(f"row_{index}_button_disagrees")
        print(f"  row {index}: SKIPPED and it should not have been. The row "
              f"reads {text!r}")
        print(f"    parsed  {'nothing' if row is None else str(row.qty) + ' at ' + format(row.price, ',')}")
        print(f"    button  {button!r}, box reads "
              f"{model.button_text()!r}")
        return False

    reconcile_work_tab(model, verbose=verbose)
    tab_before_withdrawal(model, verbose=verbose)
    if model.work_slots():
        print(f"    the run still holds tab {row_model.WORK_TAB} slot(s) "
              f"{model.work_slots()} from earlier work, so a withdrawal "
              f"lands past them")
    landing = model.next_work_slot()
    if landing is None:
        raise NotReady(
            f"the run holds every slot of tab {row_model.WORK_TAB}, so row "
            f"{index} would come back with nowhere to go. Nothing cancelled.")

    if verbose:
        print(f"  row {index}: {row.name!r} x{row.qty} at {row.price:,} "
              f"-> tab {row_model.WORK_TAB} slot {landing}")
    held = model.get(index)
    if held is not None:
        same = row_model.item_key(held.name) == row_model.item_key(row.name)
        if same:
            row.buy_cost = held.buy_cost
            row.floor_at = held.floor_at
            if held.price and row.price != held.price:
                calibration.snap(f"row_{index}_disagrees")
                print(f"    the row reads {row.price:,}, and this run listed "
                      f"{held.name!r} at {held.price:,} there; nothing but "
                      f"this run changes a price, so the reading is wrong "
                      f"and what was listed stands")
                row = row_model.Row(held.name, qty=row.qty, price=held.price,
                                    buy_cost=held.buy_cost,
                                    floor_at=held.floor_at)
        else:
            calibration.snap(f"row_{index}_named_differently")
            print(f"    the row reads {row.name!r} at {row.price:,}, and this "
                  f"run recorded {held.name!r} there; the screen names the "
                  f"item, so it is relisted as {row.name!r} with no cost "
                  f"carried over")
    price_by_voucher(row)
    model._slots[index] = row
    unit_floor, pair = calibration.price_floor(row.name)
    cost = row.floor_at or row.buy_cost
    if cost:
        unit_floor, pair = cost, "what it cost"
    if not unit_floor:
        stacked = model.read_stacked()
        again = _row_from(stacked)
        if again is not None and again.name != row.name:
            floor_again, pair_again = calibration.price_floor(again.name)
            if floor_again:
                print(f"    one line read {row.name!r} and found no floor; "
                      f"read as stacked lines it is {again.name!r}, so the "
                      f"floor and the name are taken from that")
                unit_floor, pair = floor_again, pair_again
                row = row_model.Row(again.name, qty=row.qty, price=row.price,
                                    buy_cost=row.buy_cost,
                                    floor_at=row.floor_at)
                model._slots[index] = row
    if verbose:
        if not unit_floor:
            print(f"    no floor for {row.name!r}"
                  + (f"; {pair} did not price" if pair else ""))
        else:
            by_item = calibration.voucher_floor_ratio(row.name)[1] > 0
            whole = unit_floor if by_item else unit_floor * row.pack
            carries = "" if by_item else f" ({unit_floor:,} x {row.pack})"
            print(f"    floor {whole:,}{carries} from {pair}; listed at "
                  f"{row.price:,}, "
                  f"{'UNDER the floor' if row.price < whole else 'above it'}")
    with calibration.phase("check the shop slot is empty"):
        standing = (row_model.panel_standing()
                    if row_model.panel_holds_item() else None)
    if standing is not None:
        raise row_model.Divergence(
            f"the shop slot already holds something the panel prices at "
            f"{standing:,}; row {index} has NOT been cancelled. Clear the "
            f"slot first.")
    lands_in = min([i for i in model.empty() if i < index] + [index])
    if verbose and lands_in != index:
        print(f"    rows {[i for i in model.empty() if i < index]} are empty, "
              f"so it will come back in row {lands_in}")
    task("relist", row=index, item=row.name, qty=row.qty, price=row.price,
         tab=row_model.WORK_TAB, slot=list(landing), lands_in=lands_in)
    with calibration.phase("cancel the row and take it back"):
        model.cancel(index, verbose=False, tab_ready=True)
    with calibration.phase(f"select inventory tab {row_model.WORK_TAB}"):
        time.sleep(max(0.0, WITHDRAW_SETTLE - calibration.PARK_SETTLE))
        calibration.click(*calibration.inventory_tab_point(row_model.WORK_TAB))
    whole = calibration.voucher_floor_ratio(row.name)[1] > 0
    pack = 1 if whole else row.pack
    floor = unit_floor * pack
    why = ""
    if unit_floor:
        why = (f"it is worth {pair}" if whole
               else f"it cost {unit_floor:,} each" if cost
               else f"a {pair} costs {unit_floor:,}")
        if pack > 1:
            why += f", and this listing carries {pack}"
    break_after = int(calibration.load_shared()["run"]["floor_break_after"])
    parked = model._floored.get(index, 0)
    broken = index in model._broken
    holds = whole or bool(row.floor_at)
    breaking = (break_after > 0 and not holds
                and (broken or parked >= break_after))
    if holds and floor and parked >= break_after > 0:
        print(f"    row {index} has sat on its {floor:,} floor for {parked} "
              f"relist(s) and holds it; {pair} does not go to the market")
    if breaking:
        if broken:
            print(f"    row {index} broke its floor earlier and has not sold; "
                  f"staying at the market")
        else:
            print(f"    row {index} has sat on its {floor:,} floor for "
                  f"{parked} relist(s); letting it go at the market until it "
                  f"sells")
        floor, why = 0, ""
    special = special_of(row)
    under = None
    if special:
        under = special_undercut(special, special_ordinal(model, special, index,
                                                          first, last))
        floor, why = 0, ""
        print(f"    row {index} is the {special} special row; listing at "
              f"{under:,} under the market, no floor")
    try:
        with calibration.phase("list it back"):
            expect = dict(floor=floor, why=why, expect_item=row.name,
                          listed_at=None if special else row.price,
                          expect_qty=row.qty,
                          under=under,
                          expect_market=(row_model.market_anchor(row.name)
                                         * row.pack) or None)
            out = model.list_slot(*landing, verbose=verbose,
                                  lands_in=lands_in, **expect)
    except row_model.SlotNeverFilled:
        seen = model.read()
        if model.function(seen) != row_model.RECEIPT_WORD:
            raise
        print(f"  row {index} sold while it was being cancelled, so nothing "
              f"came back to tab {row_model.WORK_TAB}; collecting it instead")
        model.release_work(landing)
        model.receive(index, verbose=False)
        seen = model.read()
        if model.is_empty(seen):
            model.drop(index)
            model.forget_floor(index)
            print(f"    collected; row {index} is empty")
            task_done("relist", row=index, sold=True)
            restock_now(model, row.name, first, last, verbose=verbose)
            return None
        left = _row_from(seen)
        if left is not None:
            model.place(index, row_model.Row(left.name, qty=left.qty,
                                             price=left.price,
                                             buy_cost=row.buy_cost,
                                             floor_at=row.floor_at))
        print(f"    collected; row {index} still reads {seen!r}, so the row is "
              f"put back as {'unreadable' if left is None else str(left.qty)}"
              f"{'' if left is None else ' at ' + format(left.price, ',')}; "
              f"the cancel did not take and the row is not empty")
        task_done("relist", row=index, sold=True)
        return None
    task_done("relist", row=index, lands_in=lands_in, qty=out["qty"],
              price=out["price"], item=out["item"])
    model._slots.pop(index, None)

    def record(listed, at):
        if listed["resolved"]:
            if not listed["item"]:
                print(f"    row {at} holds what was in the slot; the "
                      f"screen names it on the next pass")
            model.place(at, row_model.Row(listed["item"] or row.name,
                                          qty=listed["qty"],
                                          price=listed["price"]))
            return
        model.place(at, row_model.Row(row.name, qty=listed["qty"],
                                      price=listed["price"],
                                      buy_cost=row.buy_cost,
                                      floor_at=row.floor_at))

    if out["resolved"]:
        model.forget_floor(index)
        record(out, lands_in)
        sweep_work_tab(model, first, last, verbose, expect, record)
        return out
    record(out, lands_in)
    model.carry_floor(index, lands_in, breaking, out["floored"], parked)
    if verbose:
        note = ""
        if breaking:
            note = ", under the floor until it sells"
        elif out["floored"]:
            note = f", {parked + 1} relist(s) on the floor now"
        print(f"    relisted {out['qty']} at {out['price']:,} in row "
              f"{lands_in}{note}")
    return out


PASS_ALLOWANCE = _SHARED["war"]["quiet_before_end"]


def recover_after_lag(verbose=True):
    print("  the server stalled; closing the shop rather than trusting what "
          "is on screen")
    row_model.WORK_TAB_STALE = True
    print(f"  whatever the stall left in tab {row_model.WORK_TAB} is read "
          f"from the screen before the next withdrawal")
    try:
        calibration.close_everything(verbose=verbose)
    except Exception as exc:
        print(f"  the shop would not close after the stall ({exc})")
        return False
    return True


calibration.on_recovered(recover_after_lag)


def shop_ready(why, verbose=True):
    if calibration._trade_window_open():
        return True
    calibration.snap(f"shop_shut_before_{why}")
    print(f"  the Agent Shop is not open before {why}; every row would read "
          f"the world behind it. Reopening.")
    _after_the_lag(f"Nothing has been read for {why}.", verbose=verbose)
    if not back_to_the_shop(verbose=verbose):
        raise NotReady(
            f"the Agent Shop would not reopen before {why}, so there is "
            f"nothing to read. Nothing touched.")
    register_tab(verbose=verbose)
    return calibration._trade_window_open()


def relist_pass(model, first, last, passes=0, verbose=True,
                collect_only=False):
    shop_ready(f"rows {first}-{last}", verbose=verbose)
    model.home(verbose=False)
    calibration.phases_reset()
    done = skipped = empty = 0
    for index in range(first, last + 1):
        out = relist_one(model, index, verbose=verbose, first=first,
                         last=last, collect_only=collect_only)
        if out:
            done += 1
        elif out is False:
            skipped += 1
        else:
            empty += 1
        board_trace(model, passes, index, first, last)
        if pending_holds_stock():
            print(f"  tab {row_model.WORK_TAB} holds {_PENDING['bought']} "
                  f"bought {_PENDING['core']} waiting at "
                  f"{_PENDING['step']}; rows {index + 1}-{last} are not "
                  f"cancelled into that tab this pass, the next pass "
                  f"carries the job on first")
            break
    if not collect_only:
        special_pass(model, first, last, verbose=verbose)
    if done or skipped:
        calibration.phases_table(
            f"relisting rows {first}-{last}: {done} relisted, {empty} empty, "
            f"{skipped} skipped")
    return done, skipped, empty


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
    special_pass(model, first, last, verbose=verbose)

    deadline = time.monotonic() + minutes * 60
    print(f"relisting rows {first}-{last} for {minutes:g} minute(s)")
    passes = done = skipped = empty = 0
    stopped = None
    started = time.perf_counter()
    while True:
        war.avoid(allowance=PASS_ALLOWANCE, verbose=verbose)
        passes += 1
        print("")
        print(f"-- pass {passes} --")
        board_trace(model, passes, first, first, last)
        try:
            shop_ready(f"pass {passes}", verbose=verbose)
            model.work_seen = None
            reconcile_work_tab(model, verbose=verbose)
            resume_work_tab(model, first, last, verbose=verbose)
            resupply_pass(model, first, last, verbose=verbose)
            if pending_holds_stock():
                print(f"  tab {row_model.WORK_TAB} holds {_PENDING['bought']} "
                      f"bought {_PENDING['core']} waiting at "
                      f"{_PENDING['step']}; no row is cancelled into that tab "
                      f"this pass, the next pass carries the job on first")
                made = missed = bare = 0
            else:
                made, missed, bare = relist_pass(model, first, last, passes,
                                                 verbose=verbose)
            done += made
            skipped += missed
            empty += bare
            rest_the_game(verbose=verbose)
        except calibration.ServerStalled as exc:
            print(f"  {exc}")
            row_model.WORK_TAB_STALE = True
            if time.monotonic() >= deadline:
                print(f"  {minutes:g} minute(s) are up after pass {passes}")
                break
            continue
        except row_model.Divergence as exc:
            print(f"  STOPPED: {exc}")
            stopped = exc
            break
        model.home(verbose=False)
        board_report(model, passes)
        left = deadline - time.monotonic()
        if left <= 0:
            print(f"  {minutes:g} minute(s) are up after pass {passes}")
            break
        print(f"  pass {passes}: {made} relisted, {bare} empty, "
              f"{missed} skipped; {left/60:.1f} minute(s) left")
    span = (time.perf_counter() - started) * 1000
    print("")
    print(f"{passes} pass(es), {done} relisted, {empty} empty, "
          f"{skipped} skipped in {span/1000:.0f}s"
          + (f" ({span/done:.0f} ms a row)" if done else ""))
    if stopped is not None:
        raise stopped
    return done


def list_floor(item, verbose=True):
    unit_floor, pair = calibration.price_floor(item)
    if not unit_floor:
        if verbose:
            print(f"    no floor for {item!r}"
                  + (f"; {pair} did not price" if pair else ""))
        return 0, "", None
    whole = calibration.voucher_floor_ratio(item)[1] > 0
    why = f"it is worth {pair}" if whole else f"a {pair} costs {unit_floor:,}"
    unit_market = None if whole else (calibration.market_unit(item) or None)
    if verbose:
        print(f"    floor {unit_floor:,} a unit from {pair}"
              + ("" if whole or unit_market else
                 f"; {item!r} has no market price, so a bundle's count "
                 f"cannot be told and the floor stands per listing"))
    return unit_floor, why, unit_market


def do_list(row, col, price=None, verbose=True, tab=None, item=None):
    tab = row_model.WORK_TAB if tab is None else int(tab)
    initialise(verbose=verbose)
    register_tab(verbose=verbose)
    model = row_model.RowModel().seed({})
    started = time.perf_counter()
    floor, why, unit_market = (list_floor(item, verbose=verbose)
                               if item and price is None else (0, "", None))
    if verbose:
        print(f"  selecting inventory tab {tab} before reading slot "
              f"({row},{col})")
    calibration.click(*calibration.inventory_tab_point(tab), settle=0.0)
    time.sleep(row_model.TAB_SETTLE)
    out = model.list_slot(row, col, price=price, floor=floor, why=why,
                          unit_market=unit_market,
                          floor_each=floor if unit_market else 0,
                          verbose=verbose)
    task_done("list", tab=tab, slot=[int(row), int(col)], qty=out["qty"],
              price=out["price"])
    print(f"  done in {(time.perf_counter() - started) * 1000:.0f} ms")
    return out


def report(model):
    print(model.report())


def row_at(model, index, verbose=True):
    model.scroll_to(index, verbose=False)
    text = model.read()
    text, row = row_from_screen(text, model)
    if verbose:
        print(f"  row {index}: {text!r}")
        print(f"    function {model.function(text)!r}  "
              f"complete {model.complete(text)}")
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


def do_craft_chaos(verbose=True):
    run = calibration.load_shared()["run"]
    first, last = int(run["relist_from"]), int(run["relist_to"])
    core_slot, made_slot = calibration._craft_slots()
    if core_slot is None:
        print("  no favourite pair matches the measured recipe; there is "
              "nothing this craft window makes.")
        return None
    core = calibration.FAVOURITE_ITEMS[str(core_slot)]
    set_name = calibration.FAVOURITE_ITEMS[str(made_slot)]
    initialise(verbose=verbose)
    register_tab(verbose=verbose)
    model = seed(verbose=verbose)
    print("")
    print(f"-- recovery: craft whatever {core} is held into {set_name} --")

    calibration.phases_reset()
    with calibration.phase(f"price {core}"):
        core_row = get_price.get_price(core_slot, verbose=False)
    with calibration.phase(f"price {set_name}"):
        set_row = get_price.get_price(made_slot, verbose=False)
    if core_row is None or set_row is None:
        print(f"  {core if core_row is None else set_name} would not price, "
              f"so there is no market to list against.")
        return None
    free = [i for i in model.empty() if first <= i <= last]
    if not free:
        print(f"  rows {first}-{last} are full, and a crafted {set_name} with "
              f"nowhere to list stays on tab {row_model.WORK_TAB}; not "
              f"crafting.")
        return None
    print(f"  {len(free)} row(s) free inside {first}-{last}")

    started = time.perf_counter()
    with calibration.phase("close the Agent Shop"):
        calibration.close_everything()
    with calibration.phase(f"craft {core} into {set_name}"):
        made = craft.craft_sets(core, verbose=verbose)
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
    unit_cost, floor_pair = calibration.price_floor(set_name)
    unit_cost = unit_cost or core_row["unit_price"]
    why = f"a {floor_pair} costs {unit_cost:,}, a Set at a time"
    print(f"  listing the {set_name} compressed into {work} from "
          f"{made['used']} {core}(s)")
    print(f"  it costs {unit_cost:,} a Core today, so no Set goes out under "
          f"that")

    rows, listed_total = [], 0
    while True:
        empty = [i for i in model.empty() if first <= i <= last]
        if not empty:
            print(f"  rows {first}-{last} are full; what is left of the "
                  f"{set_name} stays on tab {row_model.WORK_TAB}.")
            model.hold_work(work, set_name)
            break
        lands_in = min(empty)
        try:
            with calibration.phase(f"list {set_name} from {work}"):
                listed = model.list_slot(*work, why=why, verbose=verbose,
                                         lands_in=lands_in,
                                         unit_market=set_row["unit_price"],
                                         floor_each=unit_cost,
                                         wait_fill=False,
                                         expect_item=set_name, expect_qty=1,
                                         expect_market=(
                                             int(set_row["unit_price"])
                                             * int(made["used"] or 0))
                                         or None)
        except row_model.NothingLoaded as exc:
            if rows:
                break
            model.hold_work(work, set_name)
            raise NotReady(
                f"{exc} Tab {row_model.WORK_TAB} slot {work} is left to the "
                f"{set_name} compressed there.")
        if listed["resolved"]:
            def record_set(out, at):
                model.place(at, row_model.Row(
                    out["item"] or set_name, qty=out["qty"],
                    price=out["price"], units=out["units"]))
                rows.append(at)
                nonlocal listed_total
                listed_total += out["qty"]
            record_set(listed, lands_in)
            sweep_work_tab(model, first, last, verbose, dict(
                why=why, expect_item=set_name, expect_qty=1,
                expect_market=(int(set_row["unit_price"])
                               * int(made["used"] or 0)) or None,
                unit_market=set_row["unit_price"], floor_each=unit_cost),
                record_set)
            break
        model.place(lands_in, row_model.Row(set_name, qty=listed["qty"],
                                            price=listed["price"],
                                            units=listed["units"]))
        rows.append(lands_in)
        listed_total += listed["qty"]
        if listed["qty"] < row_model.MAX_STACK:
            break
    calibration.phases_table(
        f"craft {core}: {made['used']} into the craft, listed "
        f"{listed_total} in rows {rows}")
    print(f"  done in {(time.perf_counter() - started) * 1000:.0f} ms")
    return {"core": core, "set": set_name, "crafted": made["used"],
            "listed": listed_total, "rows": rows}


def do_convert(slot, verbose=True):
    run = calibration.load_shared()["run"]
    first, last = int(run["relist_from"]), int(run["relist_to"])
    slot = int(slot)
    core = calibration.FAVOURITE_ITEMS.get(str(slot))
    pair = calibration.pair_slot(slot)
    if core is None or pair is None:
        print(f"  favourite slot {slot} is not a core with a Set beside it; "
              f"there is nothing to convert.")
        return None
    if not convert.cell_for(core):
        print(f"  {core} is not on the vendor grid; it is crafted rather "
              f"than converted. Use craft chaos.")
        return None
    set_name = calibration.FAVOURITE_ITEMS[str(pair)]
    initialise(verbose=verbose)
    register_tab(verbose=verbose)
    model = seed(verbose=verbose)
    print("")
    print(f"-- recovery: convert whatever {set_name} is held into {core} --")

    free = [i for i in model.empty() if first <= i <= last]
    if not free:
        print(f"  rows {first}-{last} are full, and a converted {core} with "
              f"nowhere to list stays on tab "
              f"{calibration.CONVERT_INVENTORY_TAB}; not converting.")
        return None
    print(f"  {len(free)} row(s) free inside {first}-{last}")

    floor, floor_pair = calibration.price_floor(core)
    floor = floor or 0
    why = f"a {floor_pair} costs {floor:,}" if floor else ""
    calibration.phases_reset()
    started = time.perf_counter()
    rows, listed_total, slots_filled, rounds = [], 0, 0, 0
    while True:
        rounds += 1
        print("")
        print(f"  -- round {rounds}: convert up to {row_model.MAX_STACK}, "
              f"then list --")
        with calibration.phase(f"round {rounds}: close the Agent Shop"):
            calibration.close_everything()
        with calibration.phase(f"round {rounds}: open the vendor"):
            convert.open_vendor(verbose=verbose)
        try:
            with calibration.phase(f"round {rounds}: convert into {core}"):
                out = convert.convert(core, verbose=verbose)
        except convert.Refused as exc:
            print(f"  nothing more to convert: {exc}")
            if not back_to_the_shop(verbose=verbose):
                raise NotReady("the Agent Shop would not reopen after the "
                               "vendor.")
            register_tab(verbose=verbose)
            break
        slots_filled += len(out["slots"])
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

        print(f"  listing {core} from {len(out['slots'])} slot(s), "
              f"{out['slots'][0]} to {out['slots'][-1]}")
        remaining, full = list(out["slots"]), False
        while remaining:
            empty = [i for i in model.empty() if first <= i <= last]
            if not empty:
                print(f"  rows {first}-{last} are full; {len(remaining)} "
                      f"slot(s) of {core} stay on tab "
                      f"{calibration.CONVERT_INVENTORY_TAB}.")
                for left in remaining:
                    model.hold_work(left, core)
                full = True
                break
            came_from = remaining.pop(0)
            lands_in = min(empty)
            try:
                with calibration.phase(f"round {rounds}: list {core} from "
                                       f"{came_from}"):
                    listed = model.list_slot(*came_from, floor=floor, why=why,
                                             verbose=verbose,
                                             lands_in=lands_in,
                                             expect_item=core)
            except (row_model.SlotNeverFilled, row_model.NothingLoaded) as exc:
                print(f"  nothing came out of tab "
                      f"{calibration.CONVERT_INVENTORY_TAB} slot {came_from} "
                      f"({exc}); the next slot is tried instead")
                continue
            model.place(lands_in, row_model.Row(core, qty=listed["qty"],
                                                price=listed["price"]))
            rows.append(lands_in)
            listed_total += listed["qty"]
        if full:
            break
    calibration.phases_table(
        f"convert {core}: {slots_filled} slot(s) filled, listed "
        f"{listed_total} in rows {rows}")
    print(f"  done in {(time.perf_counter() - started) * 1000:.0f} ms")
    return {"slot": slot, "core": core, "set": set_name,
            "slots": slots_filled, "listed": listed_total, "rows": rows}


def do_collect(verbose=True):
    run = calibration.load_shared()["run"]
    first, last = int(run["relist_from"]), int(run["relist_to"])
    initialise(verbose=verbose)
    register_tab(verbose=verbose)
    model = seed(verbose=verbose)
    _done, _skipped, empty = relist_pass(model, first, last, verbose=verbose,
                                         collect_only=True)
    print(f"  collected what sold in rows {first}-{last}; {empty} row(s) "
          f"empty now")
    return empty


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
    table = calibration.load_shared()["resupply"]["enable_buying"]
    want = re.sub(r"[^a-z0-9]", "", (core_name or "").lower())
    for name, on in table.items():
        if re.sub(r"[^a-z0-9]", "", name.lower()) == want:
            return bool(on)
    return False


def counts_toward():
    where = {}
    for slot in core_slots():
        where[slot] = slot
        if craft_route(calibration.FAVOURITE_ITEMS[str(slot)]):
            pair = calibration.pair_slot(slot)
            if pair is not None:
                where[pair] = slot
    return where


def special_conf():
    return calibration.load_shared()["resupply"].get("special_row") or {}


def special_qty():
    return int(special_conf().get("qty") or 1)


def special_amounts(core=None):
    table = special_conf().get("under_market") or 0
    value = (calibration._per_item_raw(table, core) if isinstance(table, dict)
             else table)
    if isinstance(value, list):
        return [int(v) for v in value] or [0]
    return [int(value or 0)]


def special_rows(core=None):
    amounts = special_amounts(core)
    if len(amounts) > 1:
        return len(amounts)
    table = special_conf().get("rows") or 1
    if isinstance(table, dict):
        return int(calibration._per_item_raw(table, core) or 1)
    return int(table)


def special_under(core=None, ordinal=0):
    amounts = special_amounts(core)
    return amounts[min(max(int(ordinal), 0), len(amounts) - 1)]


def special_spread():
    return int(special_conf().get("under_random_range") or 0)


def special_undercut(core=None, ordinal=0):
    spread = special_spread()
    return special_under(core, ordinal) + (random.randint(0, spread)
                                           if spread > 0 else 0)


def special_names():
    conf = special_conf()
    if not conf.get("enabled"):
        return {}
    out = {}
    for name in conf.get("cores") or []:
        if not buying_enabled(name):
            continue
        out[row_model.item_key(name)] = name
    return out


def special_slots():
    out = []
    for name in special_names().values():
        slot = calibration.favourite_slot_of(name)
        if slot is not None and slot not in out:
            out.append(slot)
    return out


def special_of(row):
    if row is None or row.pack != 1:
        return None
    name = special_names().get(row_model.item_key(row.name))
    if name is None:
        return None
    anchor = row_model.market_anchor(name)
    if anchor and row.price and row.price > anchor * row_model.PRICE_CHECK_FACTOR:
        return None
    return name


def is_special(row):
    return special_of(row) is not None


def special_seats(model, first, last):
    seats = {}
    for index, row in sorted((model._slots or {}).items()):
        if row is None or not first <= index <= last:
            continue
        name = special_of(row)
        if name is not None:
            seats.setdefault(name, []).append(index)
    return seats


def special_ordinal(model, name, index, first, last):
    mine = sorted(set(special_seats(model, first, last).get(name, []))
                  | {int(index)})
    return mine.index(int(index))


def special_wanted(model, first, last):
    seats = special_seats(model, first, last)
    short = []
    for name in special_names().values():
        if calibration.favourite_slot_of(name) is None:
            continue
        missing = special_rows(name) - len(seats.get(name, []))
        short.extend([name] * max(0, missing))
    return short


def buying_rows(model, first, last):
    free = [i for i in model.empty() if first <= i <= last]
    keep = len(special_wanted(model, first, last))
    return free[keep:] if keep else free


def rows_by_core(model, first, last):
    held = {slot: 0 for slot in core_slots()}
    where = counts_toward()
    for index, row in (model._slots or {}).items():
        if row is None or not first <= index <= last or is_special(row):
            continue
        slot = where.get(calibration.favourite_slot_of(row.name))
        if slot in held:
            held[slot] += 1
    return held


def special_list(model, landing, core, first, last, verbose=True, cost=0,
                 ordinal=0):
    with calibration.phase("reopen the Agent Shop"):
        if not back_to_the_shop(verbose=verbose):
            raise NotReady(f"the Agent Shop would not reopen for the {core} "
                           f"special row.")
    with calibration.phase("select the Register tab"):
        register_tab(verbose=verbose)
    with calibration.phase(f"select inventory tab {row_model.WORK_TAB}"):
        calibration.click(*calibration.inventory_tab_point(row_model.WORK_TAB),
                          settle=0.0)
        time.sleep(row_model.TAB_SETTLE)
    empty = [i for i in model.empty() if first <= i <= last]
    if not empty:
        raise NotReady(f"rows {first}-{last} are full; the {core} stays on "
                       f"tab {row_model.WORK_TAB}.")
    lands_in = min(empty)
    try:
        with calibration.phase(f"list {core} from {landing}"):
            listed = model.list_slot(*landing, verbose=verbose,
                                     lands_in=lands_in,
                                     under=special_undercut(core, ordinal),
                                     floor=0, why="", wait_fill=False,
                                     expect_item=core,
                                     expect_market=row_model.market_anchor(
                                         core) or None, resolve=False)
    except row_model.Divergence as exc:
        raise NotReady(f"{exc} The {core} stays on tab "
                       f"{row_model.WORK_TAB}.") from exc
    model.release_work(landing)
    model.place(lands_in, row_model.Row(
        core, qty=listed["qty"], price=listed["price"],
        buy_cost=cost or None, units=listed.get("units")))
    print(f"  the {core} special row is listed at {listed['price']:,} in row "
          f"{lands_in}")
    return lands_in


def finish_special(model, job, first, last, verbose=True):
    core, slot, want = job["core"], job["slot"], special_qty()
    if job["step"] == "buy":
        with calibration.phase(f"read tab {row_model.WORK_TAB} before buying"):
            before = work_tab_slots(verbose=False)
        with calibration.phase(f"buy {want} {core}"):
            taken = take_offers(job, want, 1, on_margin=False,
                                verbose=verbose)
        bought = int(job["bought"])
        if not taken or bought <= 0:
            raise buy.Refused(f"nothing was bought for the {core} special row.")
        job["step"] = "list"
        model.hold_work(job["landing"], core)
        with calibration.phase(f"wait for the {core} to land on tab "
                               f"{row_model.WORK_TAB}"):
            arrived = await_arrival(before, verbose=False)
        if arrived:
            landed = sorted(arrived)[0]
            if landed != tuple(job["landing"]):
                model.move_work(job["landing"], landed)
                job["landing"] = landed
        print(f"  the {core} landed in tab {row_model.WORK_TAB} slot "
              f"{tuple(job['landing'])}")
        task("resupply", core=core, slot=slot, tab=row_model.WORK_TAB,
             landing=list(job["landing"]), step="list", holding=core,
             qty=bought, special=True)
    if job["step"] == "list":
        cost = -(-job["paid"] // job["bought"]) if job["paid"] else 0
        lands_in = special_list(model, job["landing"], core, first, last,
                                verbose=verbose, cost=cost,
                                ordinal=job.get("ordinal", 0))
        job["rows"], job["listed"], job["step"] = [lands_in], 1, "listed"
    task_done("resupply", core=core, bought=job["bought"],
              listed=job["listed"], rows=job["rows"], special=True)
    return {"item": core, "bought": job["bought"], "listed": job["listed"],
            "rows": job["rows"]}


def resupply_special(model, first, last, verbose=True):
    global _PENDING
    if _PENDING is not None and _PENDING.get("kind") != "special":
        return None
    job = _PENDING
    if job is None:
        wanted = special_wanted(model, first, last)
        if not wanted:
            return None
        core = wanted[0]
        slot = calibration.favourite_slot_of(core)
        if not [i for i in model.empty() if first <= i <= last]:
            print(f"  rows {first}-{last} are full, so the {core} special "
                  f"row waits for one to free")
            return None
        print("")
        ordinal = len(special_seats(model, first, last).get(core, []))
        run = calibration.load_shared()["resupply"]
        leave = int(calibration.buy_leave_behind(core))
        steps_max = int(run["buy_scroll_limit"])
        take_all = int(run["buy_take_all_after"])
        print(f"-- {core} special row {ordinal + 1} of "
              f"{special_rows(core)}: {special_qty()} at "
              f"{special_under(core, ordinal):,} to "
              f"{special_under(core, ordinal) + special_spread():,} under "
              f"the market --")
        if leave:
            print(f"  {leave} stays behind on every row bought until an "
                  f"order is {take_all} step(s) down with nothing spare, and "
                  f"from there every row is taken whole; each order prices "
                  f"the favourite afresh, then wheels down, up to "
                  f"{steps_max} step(s)")
        if not shop_ready(f"the {core} special row", verbose=verbose):
            return None
        war.avoid(allowance=PASS_ALLOWANCE, verbose=verbose)
        landing = model.next_work_slot()
        if landing is None:
            raise NotReady(
                f"the run holds every slot of tab {row_model.WORK_TAB}; "
                f"nowhere for the {core} special row to land.")
        task("resupply", core=core, slot=slot, tab=row_model.WORK_TAB,
             landing=list(landing), special=True)
        job = {"kind": "special", "core": core, "slot": slot,
               "landing": landing, "step": "buy", "bought": 0, "paid": 0,
               "listed": 0, "rows": [], "ordinal": ordinal,
               "target": special_qty(), "want_max": None, "sells_at": 0,
               "gap": None, "leave": leave, "steps_max": steps_max,
               "take_all": take_all, "take_all_on": False, "orders": 0}
    else:
        print("")
        print(f"-- {job['core']} special row: carrying on at {job['step']} "
              f"where it stopped; {job['bought']} bought --")
        task("resupply", core=job["core"], slot=job["slot"],
             tab=row_model.WORK_TAB, landing=list(job["landing"]),
             resumed=job["step"], special=True)
    _PENDING = job
    calibration.phases_reset()
    try:
        out = finish_special(model, job, first, last, verbose=verbose)
    except calibration.ServerStalled:
        print(f"  the {job['core']} special row keeps its place at "
              f"{job['step']}; the next pass carries on there")
        raise
    except (NotReady, buy.Refused, buy.TooThin, buy.Broke,
            row_model.Divergence):
        if job["step"] != "buy" and job["bought"] > 0:
            print(f"  the {job['core']} special row keeps its place at "
                  f"{job['step']}; the core is in the bag and the next pass "
                  f"carries on there")
            raise
        _PENDING = None
        raise
    except BaseException:
        _PENDING = None
        raise
    _PENDING = None
    return out


def special_pass(model, first, last, verbose=True):
    try:
        return resupply_special(model, first, last, verbose=verbose)
    except (NotReady, buy.Refused, buy.TooThin, buy.Broke,
            row_model.Divergence) as exc:
        print(f"  the special row stopped: {exc}")
        return None
    finally:
        if not back_to_the_shop(verbose=verbose):
            raise NotReady("the Agent Shop is not open after the special row.")
        register_tab(verbose=verbose)


def price_gap(slot, say=True, rows=None):
    core = calibration.FAVOURITE_ITEMS[str(slot)]
    pair = calibration.pair_slot(slot)
    set_name = calibration.FAVOURITE_ITEMS[str(pair)]
    if rows is not None:
        core_row, set_row = rows
    else:
        calibration.phases_reset()
        with calibration.phase(f"price {core}"):
            core_row = get_price.get_price(slot, verbose=False)
        with calibration.phase(f"price {set_name}"):
            set_row = get_price.get_price(pair, verbose=False)
    if core_row is None or set_row is None:
        if say:
            print(f"  {core if core_row is None else set_name} would not "
                  f"price; not buying blind.")
        return core_row, set_row, None
    if craft_route(core):
        high, low = (set_name, set_row), (core, core_row)
    else:
        high, low = (core, core_row), (set_name, set_row)
    diff = high[1]["unit_price"] - low[1]["unit_price"]
    if say:
        print(f"  {high[0]} {high[1]['unit_price']:,} - {low[0]} "
              f"{low[1]['unit_price']:,} = {diff:,}")
    return core_row, set_row, diff


def margin_says_buy(core, held, diff):
    wants = calibration.rows_by_margin(core, diff)
    print(f"  a margin of {diff:,} is worth {wants} row(s) of {core}; "
          f"{held} held" + ("" if held < wants else "; not buying."))
    if held >= wants:
        return None
    return calibration.margin_for_rows(core, held + 1)


def alz_covers(what, units, unit_price, verbose=True):
    balance = get_alz.read_balance()
    cost = int(units) * int(unit_price or 0)
    if balance is None:
        print(f"  the Alz balance would not read; not buying {what} blind.")
        return False
    if balance < cost:
        print(f"  Alz {balance:,} held; even {units} {what} at {unit_price:,} "
              f"needs {cost:,}; not buying.")
        return False
    print(f"  Alz {balance:,} held covers {balance // max(1, int(unit_price or 1)):,} "
          f"{what} at {unit_price:,}; buying what it covers")
    return True


def start_resupply(model, slot, held, first, last, verbose=True, rows=None):
    core = calibration.FAVOURITE_ITEMS[str(slot)]
    pair = calibration.pair_slot(slot)
    set_name = calibration.FAVOURITE_ITEMS[str(pair)]
    print("")
    want_min = calibration.buy_min(core)
    want_max = calibration.buy_max(core)
    print(f"-- {core}: {held} row(s) --")

    rounds_needed = max(1, -(-int(want_max) // row_model.MAX_STACK))
    free_rows = buying_rows(model, first, last)
    if len(free_rows) < rounds_needed:
        print(f"  rows {first}-{last} have {len(free_rows)} free and a "
              f"resupply can need {rounds_needed}; not pricing or buying, "
              f"because a Core with nowhere to list stays on tab "
              f"{calibration.CONVERT_INVENTORY_TAB}.")
        return None
    print(f"  {len(free_rows)} row(s) free inside {first}-{last}; a resupply "
          f"needs up to {rounds_needed}")
    room = len(free_rows) * row_model.MAX_STACK

    core_row, set_row, diff = price_gap(slot, rows=rows)
    threshold = None if diff is None else margin_says_buy(core, held, diff)
    if threshold is None:
        return None

    with calibration.phase(f"find the free slot on tab "
                           f"{calibration.CONVERT_INVENTORY_TAB}"):
        landing = calibration.first_free_slot(
            calibration.CONVERT_INVENTORY_TAB, verbose=False)
    if landing is None:
        raise NotReady(
            f"inventory tab {calibration.CONVERT_INVENTORY_TAB} is full.")
    print(f"  tab {calibration.CONVERT_INVENTORY_TAB} is showing and free "
          f"from {landing}, so the {set_name} and the {core} land there")
    task("resupply", core=core, set=set_name, slot=slot,
         tab=calibration.CONVERT_INVENTORY_TAB, landing=list(landing))
    return {"slot": slot, "core": core, "set": set_name, "pair": pair,
            "diff": diff, "landing": landing, "want_min": want_min,
            "want_max": want_max, "room": room,
            "sells_at": core_row["unit_price"],
            "gap": threshold, "step": "buy", "orders": 0, "bought": 0,
            "paid": 0, "floor": 0, "why": "", "max_rounds": 0, "rounds": 0,
            "slots": [], "filled": 0, "rows": [], "listed": 0}


def buy_sets(job, verbose=True):
    run = calibration.load_shared()["resupply"]
    core, set_name, want_min = job["core"], job["set"], job["want_min"]
    rows_max = int(run["core_offer_rows"])
    considered, searched = 0, False
    TOO_BIG = object()
    while job["bought"] < want_min:
        print(f"  {job['bought']}/{want_min} {set_name} held")
        job["orders"] += 1
        got = None
        for attempt in range(1, int(run["buy_retries"]) + 1):
            try:
                with calibration.phase(f"buy order {job['orders']}"):
                    got = buy.buy_row_one(job["pair"],
                                          want_min - job["bought"],
                                          verbose=verbose,
                                          held=job["bought"],
                                          floor_qty=want_min,
                                          ceiling=job["want_max"],
                                          sells_at=job["sells_at"],
                                          gap=job["gap"],
                                          room=job["room"],
                                          search=not searched)
                searched = True
                break
            except buy.TooBig as exc:
                searched = True
                print(f"  {exc}")
                got = TOO_BIG
                break
            except buy.Refused as exc:
                searched = False
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
        considered += 1
        if got is TOO_BIG:
            if considered >= rows_max:
                print(f"  {rows_max} offer(s) considered and no more fit; "
                      f"leaving {set_name} at {job['bought']} of {want_min}")
                break
            with calibration.phase(f"step down to the next offer "
                                   f"({considered})"):
                buy.scroll_down(1, verbose=verbose)
            continue
        if got["bought"] <= 0:
            print(f"  the last order bought nothing; stopping.")
            break
        job["bought"] += got["bought"]
        job["paid"] += got["spent"]
        if considered >= rows_max and job["bought"] < want_min:
            print(f"  {rows_max} offer(s) considered; leaving {set_name} at "
                  f"{job['bought']} of {want_min}")
            break
    bought, paid = job["bought"], job["paid"]
    if bought <= 0:
        print(f"  nothing bought; not opening the vendor.")
        return False
    if bought < want_min:
        print(f"  bought {bought} of the {want_min} wanted.")

    unit_floor, floor_pair = calibration.price_floor(core)
    if bought and paid:
        job["floor"] = -(-paid // bought)
        job["why"] = f"a {set_name} cost {job['floor']:,} this pass"
    else:
        job["floor"] = 0 if unit_floor is None else unit_floor
        job["why"] = (f"a {floor_pair} costs {unit_floor:,}"
                      if unit_floor else "")
    job["max_rounds"] = max(1, -(-bought // row_model.MAX_STACK)) + 1
    job["step"] = "convert"
    note_step(job, holding=job.get("set"), qty=bought,
              price=job.get("floor"),
              tab=calibration.CONVERT_INVENTORY_TAB)
    return True


def convert_round(job, verbose=True):
    core, bought = job["core"], job["bought"]
    job["rounds"] += 1
    rounds = job["rounds"]
    print("")
    print(f"  -- round {rounds}: convert up to {row_model.MAX_STACK}, "
          f"then list -- {max(0, bought - job['listed'])} of {bought} left --")
    with calibration.phase(f"round {rounds}: close the Agent Shop"):
        calibration.close_everything()
    with calibration.phase(f"round {rounds}: open the vendor"):
        convert.open_vendor(verbose=verbose)
    with calibration.phase(f"round {rounds}: convert into {core}"):
        out = convert.convert(core, verbose=verbose)
    job["slots"] = list(out["slots"])
    job["filled"] += len(out["slots"])
    job["step"] = "list"
    note_step(job, holding=core, qty=max(0, bought - job.get("listed", 0)),
              price=job.get("floor"),
              tab=calibration.CONVERT_INVENTORY_TAB)


def list_round(model, job, first, last, verbose=True):
    core, rounds = job["core"], job["rounds"]
    tab = calibration.CONVERT_INVENTORY_TAB
    floor, why = job["floor"], job["why"]
    with calibration.phase(f"round {rounds}: reopen the Agent Shop"):
        if not back_to_the_shop(verbose=verbose):
            raise NotReady("the Agent Shop would not reopen after the "
                           "vendor.")
    with calibration.phase(f"round {rounds}: select the Register tab"):
        register_tab(verbose=verbose)
    with calibration.phase(f"round {rounds}: select inventory tab {tab}"):
        calibration.click(*calibration.inventory_tab_point(tab), settle=0.0)
        time.sleep(row_model.TAB_SETTLE)

    remaining = list(job["slots"])
    if remaining:
        print(f"  listing {core} from {len(remaining)} slot(s), "
              f"{remaining[0]} to {remaining[-1]}")
    full = False
    while remaining:
        empty = [i for i in model.empty() if first <= i <= last]
        if not empty:
            print(f"  rows {first}-{last} are full; {len(remaining)} "
                  f"slot(s) of {core} stay on tab {tab}.")
            for left in remaining:
                model.hold_work(left, core)
            full = True
            break
        came_from = remaining.pop(0)
        lands_in = min(empty)
        try:
            with calibration.phase(f"round {rounds}: list {core} from "
                                   f"{came_from}"):
                listed = model.list_slot(*came_from, floor=floor, why=why,
                                         verbose=verbose, lands_in=lands_in,
                                         expect_item=core)
        except (row_model.SlotNeverFilled, row_model.NothingLoaded) as exc:
            print(f"  nothing came out of tab {tab} slot {came_from} "
                  f"({exc}); the next slot is tried instead")
            continue
        model.place(lands_in, row_model.Row(
            core, qty=listed["qty"], price=listed["price"],
            buy_cost=job["floor"] if job["paid"] else 0))
        job["rows"].append(lands_in)
        job["listed"] += listed["qty"]
    job["slots"] = []
    job["step"] = "convert"
    note_step(job, holding=job.get("set"),
              qty=max(0, job.get("bought", 0) - job.get("listed", 0)),
              price=job.get("floor"),
              tab=calibration.CONVERT_INVENTORY_TAB)
    return full


def finish_resupply(model, job, first, last, verbose=True):
    if job["step"] == "buy" and not buy_sets(job, verbose=verbose):
        return None
    full = False
    while not full:
        if job["step"] == "convert":
            if (job["listed"] >= job["bought"]
                    or job["rounds"] >= job["max_rounds"]):
                break
            convert_round(job, verbose=verbose)
        full = list_round(model, job, first, last, verbose=verbose)
    core, set_name, bought = job["core"], job["set"], job["bought"]
    left_to_convert = 0 if full else max(0, bought - job["listed"])
    if left_to_convert > 0:
        print(f"  {left_to_convert} of {bought} {set_name} are still "
              f"unconverted after {job['rounds']} round(s); they are on tab "
              f"{calibration.CONVERT_INVENTORY_TAB}.")
    else:
        task_done("resupply", core=core, bought=bought,
                  listed=job["listed"], rows=job["rows"])
    calibration.phases_table(
        f"resupply {core}: bought {bought}, {job['filled']} slot(s) "
        f"filled, "
        f"listed {job['listed']} in rows {job['rows']}")
    return {"slot": job["slot"], "core": core, "set": set_name,
            "diff": job["diff"], "bought": bought, "slots": job["filled"],
            "listed": job["listed"], "rows": job["rows"]}


def resupply_one(model, slot, held, first, last, verbose=True, rows=None):
    global _PENDING
    job = _PENDING if _PENDING and _PENDING["slot"] == slot else None
    if job is None:
        job = start_resupply(model, slot, held, first, last, verbose=verbose,
                             rows=rows)
        if job is None:
            return None
    else:
        print("")
        print(f"-- {job['core']}: carrying on at {job['step']} where it "
              f"stopped; {job['bought']} bought, {job['listed']} listed --")
        task("resupply", core=job["core"], set=job["set"], slot=slot,
             tab=calibration.CONVERT_INVENTORY_TAB,
             landing=list(job["landing"]), resumed=job["step"])
    _PENDING = job
    try:
        out = finish_resupply(model, job, first, last, verbose=verbose)
    except calibration.ServerStalled:
        print(f"  {job['core']} keeps its place at {job['step']}; the next "
              f"pass carries on there")
        raise
    except (NotReady, convert.Refused, buy.Refused):
        if job["step"] != "buy" and job["bought"] > 0:
            print(f"  {job['core']} keeps its place at {job['step']}; "
                  f"{job['bought']} bought are in the bag and the next pass "
                  f"carries on there")
            raise
        _PENDING = None
        raise
    except BaseException:
        _PENDING = None
        raise
    _PENDING = None
    return out


def craft_route(core):
    if convert.cell_for(core):
        return False
    if calibration.pair_slot(calibration.favourite_slot_of(core)) is None:
        return False
    return bool(calibration.load().get("craft"))


def start_craft_resupply(model, slot, held, first, last, verbose=True,
                         rows=None):
    run = calibration.load_shared()["resupply"]
    core = calibration.FAVOURITE_ITEMS[str(slot)]
    pair = calibration.pair_slot(slot)
    set_name = calibration.FAVOURITE_ITEMS[str(pair)]
    batch = calibration.CRAFT_CORES_PER_SET
    print("")
    want_min = calibration.buy_min(core)
    want_max = calibration.buy_max(core)
    print(f"-- {core}: {held} row(s) --")

    free_rows = buying_rows(model, first, last)
    if not free_rows:
        print(f"  rows {first}-{last} are full, and a crafted {set_name} with "
              f"nowhere to list stays on tab {row_model.WORK_TAB}; not "
              f"pricing or buying.")
        return None
    print(f"  {len(free_rows)} row(s) free inside {first}-{last}")

    core_row, set_row, diff = price_gap(slot, rows=rows)
    threshold = None if diff is None else margin_says_buy(core, held, diff)
    if threshold is None:
        return None

    jitter = int(run["buy_random_range"])
    rolled = int(want_min)
    if jitter > 0:
        rolled = max(batch, int(want_min) + random.randint(-jitter, jitter))
    target = -(-rolled // batch) * batch
    if jitter > 0:
        print(f"  buy_min {want_min} rolled to {rolled} (+/-{jitter}); buying "
              f"{target} in whole batches of {batch}")
    elif target != want_min:
        print(f"  buy_min {want_min} is not a whole number of batches of "
              f"{batch}; buying {target}")
    leave = int(calibration.buy_leave_behind(core))
    steps_max = int(run["buy_scroll_limit"])
    take_all = int(run["buy_take_all_after"])
    if leave:
        print(f"  {leave} stays behind on every row bought until an order is "
              f"{take_all} step(s) down with nothing spare, and from there "
              f"every row is taken whole until {core} is done; each order "
              f"prices the favourite afresh, then wheels down, up to "
              f"{steps_max} step(s)")
    task("resupply", core=core, set=set_name, slot=slot,
         tab=row_model.WORK_TAB)
    return {"slot": slot, "core": core, "set": set_name, "diff": diff,
            "target": target, "want_max": want_max,
            "sells_at": set_row["unit_price"],
            "core_price": core_row["unit_price"], "gap": threshold,
            "leave": leave, "steps_max": steps_max, "take_all": take_all,
            "take_all_on": False, "step": "buy",
            "orders": 0, "bought": 0, "paid": 0, "crafted": 0, "work": None,
            "rows": [], "listed": 0}


def take_offers(job, want, batch, on_margin=True, verbose=True):
    run = calibration.load_shared()["resupply"]
    core, slot, target = job["core"], job["slot"], job["target"]
    take_all = int(job.get("take_all", run["buy_take_all_after"]))
    job.setdefault("take_all_on", False)
    steps = 0
    searched = False
    THIN = object()

    def order(want, on_margin=True):
        nonlocal searched
        job["orders"] += 1
        if steps >= take_all and not job["take_all_on"]:
            job["take_all_on"] = True
            print(f"  {steps} step(s) down with nothing spare; taking rows "
                  f"whole from here until {core} is done")
        for attempt in range(1, int(run["buy_retries"]) + 1):
            try:
                with calibration.phase(f"buy order {job['orders']}"):
                    out = buy.buy_row_one(slot, want, verbose=verbose,
                                          held=job["bought"],
                                          floor_qty=target,
                                          ceiling=job["want_max"],
                                          sells_at=job["sells_at"],
                                          gap=job["gap"] if on_margin
                                          else None,
                                          leave_behind=(
                                              0 if job["take_all_on"]
                                              else job["leave"]),
                                          search=not searched,
                                          batch=batch)
                searched = True
                return out
            except buy.TooThin as exc:
                searched = True
                print(f"  {exc}")
                return THIN
            except buy.Broke as exc:
                searched = True
                job["broke"] = True
                print(f"  stopping: {exc}")
                return None
            except buy.Refused as exc:
                searched = True
                if not getattr(exc, "retryable", False):
                    print(f"  stopping: {exc}")
                    return None
                print(f"  attempt {attempt}/{run['buy_retries']}: {exc}")
                if not step_down():
                    return None
        print(f"  the board kept moving through {run['buy_retries']} "
              f"attempt(s); giving up on {core} this cycle.")
        return None

    def step_down():
        nonlocal steps
        if steps >= job["steps_max"]:
            print(f"  {job['steps_max']} step(s) down this order and nothing "
                  f"worth buying; leaving {core} at {job['bought']} of "
                  f"{target}")
            return False
        steps += 1
        with calibration.phase(f"step down to the next offer ({steps})"):
            buy.scroll_down(1, verbose=verbose)
        return True

    while True:
        got = order(want, on_margin=on_margin)
        if got is THIN:
            if not step_down():
                return False
            continue
        if got is None or got["bought"] <= 0:
            return False
        job["bought"] += got["bought"]
        job["paid"] += got["spent"]
        return True


def buy_cores(job, verbose=True):
    core, target = job["core"], job["target"]
    batch = calibration.CRAFT_CORES_PER_SET

    while job["bought"] < target:
        print(f"  {job['bought']}/{target} {core} held")
        if not take_offers(job, target - job["bought"], batch,
                           verbose=verbose):
            break

    while job["bought"] % batch and not job.get("broke"):
        short = batch - (job["bought"] % batch)
        print(f"  {job['bought']} {core} is {short} short of a whole batch "
              f"of {batch}; topping up whatever the margin says, because a "
              f"remainder crafts into nothing")
        if not take_offers(job, short, batch, on_margin=False,
                           verbose=verbose):
            break

    bought = job["bought"]
    if bought <= 0:
        print(f"  nothing bought; not opening the craft window.")
        return False
    spare = bought % batch
    if bought < batch:
        print(f"  {bought} {core} is under a whole batch of {batch}; there is "
              f"nothing to craft and they stay on tab {row_model.WORK_TAB}.")
        return False
    if spare:
        print(f"  {bought} {core} is {spare} over whole batches of {batch}; "
              f"crafting {bought - spare} and leaving {spare} on tab "
              f"{row_model.WORK_TAB}.")
    job["step"] = "craft"
    note_step(job, holding=job.get("core"), qty=job.get("bought", 0),
              price=job.get("floor"))
    return True


def craft_cores(model, job, verbose=True):
    core, set_name, bought = job["core"], job["set"], job["bought"]
    spare = bought % calibration.CRAFT_CORES_PER_SET
    with calibration.phase("close the Agent Shop"):
        calibration.close_everything()
    with calibration.phase(f"craft {core} into {set_name}"):
        made = craft.craft_sets(core, verbose=verbose, held=bought - spare)
    job["work"] = tuple(made["slot"])
    job["crafted"] = made["used"]
    job["step"] = "list"
    note_step(job, holding=set_name, qty=made["used"],
              price=job.get("floor"))
    with calibration.phase("close the craft window"):
        craft.close_craft()


def list_sets(model, job, first, last, verbose=True):
    core, set_name = job["core"], job["set"]
    bought, paid, work = job["bought"], job["paid"], job["work"]
    with calibration.phase("reopen the Agent Shop"):
        if not back_to_the_shop(verbose=verbose):
            raise NotReady("the Agent Shop would not reopen after crafting.")
    with calibration.phase("select the Register tab"):
        register_tab(verbose=verbose)
    with calibration.phase(f"select inventory tab {row_model.WORK_TAB}"):
        calibration.click(*calibration.inventory_tab_point(row_model.WORK_TAB),
                          settle=0.0)
        time.sleep(row_model.TAB_SETTLE)

    unit_floor, floor_pair = calibration.price_floor(set_name)
    if bought and paid:
        unit_cost = -(-paid // bought)
        why = f"a {core} cost {unit_cost:,} this pass, a Set at a time"
    else:
        unit_cost = unit_floor or job["core_price"]
        why = f"a {floor_pair} costs {unit_cost:,}, a Set at a time"
    print(f"  listing the {set_name} compressed into {work} from "
          f"{job['crafted']} {core}(s)")
    print(f"  the board's cheapest is {job['sells_at']:,} a Set, and the "
          f"panel scales that to whatever the bundle holds")
    print(f"  it cost {unit_cost:,} a Core, so no Set goes out under that")

    full = False
    while True:
        empty = [i for i in model.empty() if first <= i <= last]
        if not empty:
            print(f"  rows {first}-{last} are full; what is left of the "
                  f"{set_name} stays on tab {row_model.WORK_TAB}.")
            model.hold_work(work, set_name)
            full = True
            break
        lands_in = min(empty)
        try:
            with calibration.phase(f"list {set_name} from {work}"):
                listed = model.list_slot(*work, why=why, verbose=verbose,
                                         lands_in=lands_in,
                                         unit_market=job["sells_at"],
                                         floor_each=unit_cost,
                                         wait_fill=False,
                                         expect_item=set_name, expect_qty=1,
                                         expect_market=(
                                             int(job["sells_at"])
                                             * int(job.get("crafted") or 0))
                                         or None)
        except row_model.NothingLoaded as exc:
            if job["rows"]:
                break
            model.hold_work(work, set_name)
            raise NotReady(
                f"{exc} Tab {row_model.WORK_TAB} slot {work} is left to the "
                f"{set_name} compressed there.")
        if listed["resolved"]:
            def record_set(out, at):
                named = out["item"] or set_name
                mine = not out["resolved"]
                model.place(at, row_model.Row(
                    named, qty=out["qty"], price=out["price"],
                    buy_cost=unit_cost if mine else 0,
                    units=out["units"],
                    floor_at=unit_cost if mine else 0))
                job["rows"].append(at)
                job["listed"] += out["qty"]
            record_set(listed, lands_in)
            sweep_work_tab(model, first, last, verbose, dict(
                why=why, expect_item=set_name, expect_qty=1,
                expect_market=(int(job["sells_at"])
                               * int(job.get("crafted") or 0)) or None,
                unit_market=job["sells_at"], floor_each=unit_cost),
                record_set)
            break
        model.place(lands_in, row_model.Row(
            set_name, qty=listed["qty"], price=listed["price"],
            buy_cost=unit_cost if bought and paid else 0,
            units=listed["units"]))
        job["rows"].append(lands_in)
        job["listed"] += listed["qty"]
        if listed["qty"] < row_model.MAX_STACK:
            break
    return full


def finish_craft_resupply(model, job, first, last, verbose=True):
    if job["step"] == "buy" and not buy_cores(job, verbose=verbose):
        return None
    if job["step"] == "craft":
        craft_cores(model, job, verbose=verbose)
    full = list_sets(model, job, first, last, verbose=verbose)
    core, set_name, bought = job["core"], job["set"], job["bought"]
    if not full:
        task_done("resupply", core=core, bought=bought, listed=job["listed"],
                  rows=job["rows"])
    calibration.phases_table(
        f"resupply {core}: bought {bought}, {job['crafted']} into the craft, "
        f"listed {job['listed']} in rows {job['rows']}")
    return {"slot": job["slot"], "core": core, "set": set_name,
            "diff": job["diff"], "bought": bought, "crafted": job["crafted"],
            "listed": job["listed"], "rows": job["rows"]}


def resupply_chaos(model, slot, held, first, last, verbose=True, rows=None):
    global _PENDING
    job = _PENDING if _PENDING and _PENDING["slot"] == slot else None
    if job is None:
        job = start_craft_resupply(model, slot, held, first, last,
                                   verbose=verbose, rows=rows)
        if job is None:
            return None
    else:
        print("")
        print(f"-- {job['core']}: carrying on at {job['step']} where it "
              f"stopped; {job['bought']} bought, {job['listed']} listed --")
        task("resupply", core=job["core"], set=job["set"], slot=slot,
             tab=row_model.WORK_TAB, resumed=job["step"])
    _PENDING = job
    try:
        out = finish_craft_resupply(model, job, first, last, verbose=verbose)
    except calibration.ServerStalled:
        print(f"  {job['core']} keeps its place at {job['step']}; the next "
              f"pass carries on there")
        raise
    except (NotReady, craft.Refused, buy.Refused):
        if job["step"] != "buy" and job["bought"] > 0:
            print(f"  {job['core']} keeps its place at {job['step']}; "
                  f"{job['bought']} bought are in the bag and the next pass "
                  f"carries on there")
            raise
        _PENDING = None
        raise
    except BaseException:
        _PENDING = None
        raise
    _PENDING = None
    return out


def cash_seats(model, item, first, last):
    return [(index, row.name) for index, row in sorted(
        (model._slots or {}).items())
        if row is not None and first <= index <= last
        and cashshop.matches(item, row.name)]


def cash_rows(model, item, first, last):
    return len(cash_seats(model, item, first, last))


def cash_status(model, item, first, last, wants):
    seats = cash_seats(model, item, first, last)
    where = (", ".join(f"row {index} {name!r}" for index, name in seats)
             if seats else "none")
    print(f"  {item}: {len(seats)} row(s) inside {first}-{last}, {wants} "
          f"wanted; {where}")
    measured = int((calibration._read(calibration.OUT).get("voucher")
                    or {}).get("unit_price") or 0)
    voucher = calibration.voucher_unit()
    source = ("read at launch" if measured >= calibration.MIN_PLAUSIBLE_PRICE
              else "the config default")
    _name, ratio = calibration.voucher_floor_ratio(item)
    if ratio:
        floor = voucher * ratio // calibration.VOUCHER_FLOOR_PARTS
        print(f"    floor {floor:,}: {ratio}/{calibration.VOUCHER_FLOOR_PARTS} "
              f"of the {voucher:,} voucher, {source}")
    else:
        print(f"    floor: its Cash price over {calibration.VOUCHER_FLOOR_PARTS} "
              f"of the {voucher:,} voucher ({source}), read off the Cash "
              f"Shop when it buys")
    return len(seats)


def work_tab_slots(verbose=True):
    if calibration.await_inventory(verbose=verbose) is None:
        raise NotReady(f"the Inventory panel would not open, so tab "
                       f"{row_model.WORK_TAB} cannot be read.")
    calibration.click(*calibration.inventory_tab_point(row_model.WORK_TAB),
                      settle=0.0)
    time.sleep(row_model.TAB_SETTLE)
    calibration.park()
    return calibration.occupied_slots()


def await_arrival(before, verbose=True):
    arrived = work_tab_slots(verbose=verbose) - before
    deadline = time.monotonic() + calibration.DIALOG_TIMEOUT
    while not arrived and time.monotonic() < deadline:
        time.sleep(row_model.POLL_GAP)
        arrived = calibration.occupied_slots() - before
    return arrived


def start_cash(model, item, held, first, last, verbose=True):
    count = int(calibration.buy_min(item) or 1)
    print("")
    print(f"-- {item}: {held} row(s) --")
    free = [i for i in model.empty() if first <= i <= last]
    if not free:
        print(f"  rows {first}-{last} are full, and a bought {item} with "
              f"nowhere to list stays on tab {row_model.WORK_TAB}; not "
              f"buying.")
        return None
    print(f"  {len(free)} row(s) free inside {first}-{last}; buying {count} "
          f"at the Cash Shop, one at a time")
    task("resupply", core=item, tab=row_model.WORK_TAB)
    return {"kind": "cash", "core": item, "slot": None, "step": "buy",
            "count": count, "bought": 0, "price": 0, "cc_seen": None,
            "cc_before_voucher": None, "voucher_paid": 0,
            "voucher_before": [], "voucher_slot": None, "before": [],
            "after_voucher": None, "gem_before": [], "gem_paid": 0,
            "gem_slot": None, "work": [], "rows": [], "listed": 0}


def cash_floor(job, verbose=True):
    item, price = job["core"], int(job["price"] or 0)
    if price <= 0:
        raise NotReady(f"the Cash price of {item!r} is unknown, so its floor "
                       f"cannot be set; not buying.")
    funds = cashshop.currency_of(item)
    if funds:
        each = int(job.get("gem_paid") or 0) or int(
            calibration.voucher_floor_ratio(funds["from"])[1])
        if each <= 0:
            raise NotReady(f"the Cash a {funds['from']} costs is unknown, so "
                           f"{item!r} cannot be floored; not buying.")
        price = price * each // int(funds["per"])
    paid = int(job.get("voucher_paid") or 0)
    voucher = paid or calibration.voucher_unit()
    if voucher < calibration.MIN_PLAUSIBLE_PRICE:
        raise NotReady(f"no Gold voucher price to floor {item!r} against; "
                       f"not buying.")
    floor = voucher * price // calibration.VOUCHER_FLOOR_PARTS
    why = (f"{price}/{calibration.VOUCHER_FLOOR_PARTS} of the {voucher:,} "
           f"voucher " + ("bought this pass" if paid else "priced at launch"))
    if verbose:
        print(f"    floor {floor:,}: {why}")
    return floor, why


def _leave_cash_shop():
    try:
        cashshop.close_cash_shop(verbose=False)
    except Exception as exc:
        print(f"  the Cash Shop would not close on the way out: {exc}")


def cash_buy_step(job, confirm=True, verbose=True):
    item, tab = job["core"], row_model.WORK_TAB
    answer_pending_use_question(verbose=verbose)
    with calibration.phase("close the Agent Shop"):
        calibration.close_everything()
    with calibration.phase(f"select inventory tab {tab}"):
        before = work_tab_slots(verbose=verbose)
    job["before"] = sorted(before)
    print(f"  tab {tab} holds {len(before)} slot(s) and is showing, so the "
          f"{item} lands there")
    with calibration.phase("open the Cash Shop"):
        cashshop.open_cash_shop(verbose=verbose)
    try:
        word = cashshop.tab_for(item)
        with calibration.phase(f"select the {word} tab"):
            cashshop.select_tab(word, verbose=verbose)
        funds = cashshop.currency_of(item)
        with calibration.phase("read the price and what pays for it"):
            cell = cashshop.cell_for(item, verbose=verbose)
            cc = cashshop.cc_now(verbose=verbose)
            held = cashshop.gems_now(verbose=verbose) if funds else cc
        if cc is None or held is None:
            raise NotReady("the balance would not read; not buying blind.")
        job["price"] = int(cell["price"])
        if job["cc_before_voucher"] is not None:
            print(f"  Cash {job['cc_before_voucher']:,} before the voucher, "
                  f"{cc:,} after, up {cc - job['cc_before_voucher']:,}")
            job["cc_before_voucher"] = None
        job["cc_seen"] = cc
        coin = f"gem(s) from a {funds['from']}" if funds else "Cash"
        print(f"  {item} costs {job['price']:,} {coin}; {held:,} held")
        want = max(1, int(job["count"]) - job["bought"])
        need = job["price"] * want
        paid = job["gem_paid"] if funds else job["voucher_paid"]
        if held < need and not paid:
            whole = (f"the {need:,} that {want} of them cost" if want > 1
                     else f"the {job['price']:,} it costs")
            if funds:
                print(f"  {held:,} is under {whole}; buying a "
                      f"{funds['from']} and using it first, so the row goes "
                      f"out in one purchase")
                job["step"] = "gem"
                return
            print(f"  {cc:,} Cash is under {whole}; buying a Gold voucher "
                  f"and using it first, so the row goes out in one purchase")
            job["cc_before_voucher"] = cc
            job["step"] = "voucher"
            return
        if held < job["price"]:
            if funds:
                raise NotReady(
                    f"{held:,} is still under the {job['price']:,} a "
                    f"{item} costs after a {funds['from']} was bought "
                    f"and used; not buying another blind.")
            raise NotReady(
                f"{cc:,} Cash is still under the {job['price']:,} a "
                f"{item} costs after a voucher was bought and used; not "
                f"buying another voucher blind.")
        floor, why = cash_floor(job, verbose=verbose)
        with calibration.phase("buy at the Cash Shop"):
            got = cashshop.purchase(item, confirm=confirm, verbose=verbose,
                                    count=int(job["count"]) - job["bought"])
    except BaseException:
        _leave_cash_shop()
        raise
    finally:
        if job["step"] in ("voucher", "gem"):
            with calibration.phase("close the Cash Shop"):
                cashshop.close_cash_shop(verbose=verbose)
    with calibration.phase("close the Cash Shop"):
        cashshop.close_cash_shop(verbose=verbose)
    if not got["bought"]:
        print(f"  cancelled at the confirmation as asked; nothing bought, "
              f"nothing to list")
        job["step"] = "cancelled"
        return
    took = max(1, int(got["bought"]))
    job["bought"] += took
    ledger.bought(item, floor, floor * took, took)
    for _ in range(took):
        job["work"].append({"slot": None, "floor": floor, "why": why})
    print(f"  bought {took} {item} for {got['price']:,}, "
          f"{got['balance'] - got['price']:,} left; the floor on each is "
          f"{floor:,}")
    job["step"] = "land"


def cash_voucher_step(job, confirm=True, verbose=True):
    tab = row_model.WORK_TAB
    with calibration.phase("reopen the Agent Shop"):
        if not back_to_the_shop(verbose=verbose):
            raise NotReady("the Agent Shop would not reopen for the "
                           "voucher.")
    with calibration.phase(f"select inventory tab {tab}"):
        before = work_tab_slots(verbose=verbose)
    if not alz_covers(f"{calibration.VOUCHER_WORD} voucher", 1,
                      calibration.voucher_unit(), verbose=verbose):
        raise NotReady(f"the Alz on hand does not cover a "
                       f"{calibration.VOUCHER_WORD} voucher at "
                       f"about {calibration.voucher_unit():,}; no Cash can "
                       f"be made for {job['core']}.")
    job["voucher_before"] = sorted(before)
    with calibration.phase("buy the cheapest Gold voucher"):
        out = voucher_buy_flow(confirm=confirm, verbose=verbose)
    if not out["bought"]:
        print(f"  cancelled at the voucher dialog as asked; nothing bought, "
              f"nothing to list")
        job["step"] = "cancelled"
        return
    job["voucher_paid"] = int(out["price"])
    job["step"] = "use"


def cash_use_step(job, verbose=True):
    slot = voucher_use({tuple(s) for s in job["voucher_before"]},
                       verbose=verbose)
    job["voucher_slot"] = list(slot) if slot else None
    job["step"] = job.get("after_voucher") or "buy"
    job["after_voucher"] = None


def cash_gem_step(job, confirm=True, verbose=True):
    item, tab = job["core"], row_model.WORK_TAB
    funds = cashshop.currency_of(item)
    pack = funds["from"]
    answer_pending_use_question(verbose=verbose)
    with calibration.phase("close the Agent Shop"):
        calibration.close_everything()
    with calibration.phase(f"select inventory tab {tab}"):
        before = work_tab_slots(verbose=verbose)
    job["gem_before"] = sorted(before)
    print(f"  tab {tab} holds {len(before)} slot(s) and is showing, so the "
          f"{pack} lands there")
    with calibration.phase("open the Cash Shop"):
        cashshop.open_cash_shop(verbose=verbose)
    try:
        word = cashshop.tab_for(pack)
        with calibration.phase(f"select the {word} tab"):
            cashshop.select_tab(word, verbose=verbose)
        with calibration.phase("read the price and the Cash"):
            cell = cashshop.cell_for(pack, verbose=verbose)
            cc = cashshop.cc_now(verbose=verbose)
        if cc is None:
            raise NotReady("the Cash balance would not read; not buying "
                           "blind.")
        cost = int(cell["price"])
        if job["cc_before_voucher"] is not None:
            print(f"  Cash {job['cc_before_voucher']:,} before the voucher, "
                  f"{cc:,} after, up {cc - job['cc_before_voucher']:,}")
            job["cc_before_voucher"] = None
        job["cc_seen"] = cc
        print(f"  a {pack} costs {cost:,} Cash; {cc:,} Cash held")
        if cc < cost:
            if job["voucher_paid"]:
                raise NotReady(
                    f"{cc:,} Cash is still under the {cost:,} a {pack} costs "
                    f"after a voucher was bought and used; not buying "
                    f"another voucher blind.")
            print(f"  {cc:,} Cash is under the {cost:,} a {pack} costs; "
                  f"buying a Gold voucher and using it first")
            job["cc_before_voucher"] = cc
            job["after_voucher"] = "gem"
            job["step"] = "voucher"
            return
        with calibration.phase("buy at the Cash Shop"):
            got = cashshop.purchase(pack, confirm=confirm, verbose=verbose)
    except BaseException:
        _leave_cash_shop()
        raise
    finally:
        if job["step"] == "voucher":
            with calibration.phase("close the Cash Shop"):
                cashshop.close_cash_shop(verbose=verbose)
    with calibration.phase("close the Cash Shop"):
        cashshop.close_cash_shop(verbose=verbose)
    if not got["bought"]:
        print(f"  cancelled at the confirmation as asked; no {pack} bought")
        job["step"] = "cancelled"
        return
    job["gem_paid"] = int(got["price"])
    print(f"  bought 1 {pack} for {got['price']:,} Cash, "
          f"{got['balance'] - got['price']:,} Cash left")
    job["step"] = "usegem"


def cash_usegem_step(job, verbose=True):
    pack = cashshop.currency_of(job["core"])["from"]
    slot = voucher_use({tuple(s) for s in job["gem_before"]},
                       verbose=verbose, what=pack)
    job["gem_slot"] = list(slot) if slot else None
    job["step"] = "buy"


def cash_land_step(job, verbose=True):
    item, tab = job["core"], row_model.WORK_TAB
    with calibration.phase(f"wait for the {item} to land on tab {tab}"):
        arrived = await_arrival({tuple(s) for s in job["before"]},
                                verbose=verbose)
    if not arrived:
        calibration.snap("cash_item_never_landed")
        raise NotReady(
            f"no slot on tab {tab} filled after the purchase; the {item} is "
            f"not where it was expected. Nothing listed.")
    landed = sorted(arrived)
    waiting = [entry for entry in job["work"] if entry["slot"] is None]
    for entry, slot in zip(waiting, landed):
        entry["slot"] = list(slot)
    print(f"  the {item} landed in tab {tab} slot(s) "
          + ", ".join(str(s) for s in landed))
    job["step"] = "buy" if job["bought"] < job["count"] else "list"


def cash_list_step(model, job, first, last, verbose=True):
    item, tab = job["core"], row_model.WORK_TAB
    with calibration.phase("reopen the Agent Shop"):
        if not back_to_the_shop(verbose=verbose):
            raise NotReady("the Agent Shop would not reopen after the Cash "
                           "Shop.")
    with calibration.phase("select the Register tab"):
        register_tab(verbose=verbose)
    with calibration.phase(f"select inventory tab {tab}"):
        calibration.click(*calibration.inventory_tab_point(tab), settle=0.0)
        time.sleep(row_model.TAB_SETTLE)
    full = False
    for entry in job["work"]:
        if entry["slot"] is None or entry.get("listed"):
            continue
        slot = tuple(entry["slot"])
        if slot not in calibration.occupied_slots():
            if job["listed"]:
                print(f"  tab {tab} slot {slot} is empty; the {item} there "
                      f"went out with the {job['listed']} already listed")
                entry["listed"] = True
                continue
            again = 0
            while again < row_model.PANEL_REREADS:
                again += 1
                time.sleep(row_model.PANEL_REREAD_GAP)
                if slot in calibration.occupied_slots():
                    print(f"  tab {tab} slot {slot} read empty and holds the "
                          f"{item} on read {again + 1}; listing it")
                    break
            else:
                calibration.snap(f"cash_slot_empty_{slot[0]}x{slot[1]}")
                print(f"  tab {tab} slot {slot} reads empty in "
                      f"{again + 1} read(s) and nothing has been listed, so "
                      f"the {item} bought for it is not where it landed; the "
                      f"frame is kept")
                entry["listed"] = True
                continue
        empty = [i for i in model.empty() if first <= i <= last]
        if not empty:
            print(f"  rows {first}-{last} are full; the {item} stays on "
                  f"tab {tab} slot {slot}.")
            model.hold_work(slot, item)
            full = True
            continue
        lands_in = min(empty)
        opening = calibration.first_list_price(item)
        if opening and verbose:
            print(f"  the first listing of a {item} bought from the "
                  f"Cash Shop goes out at {opening:,}, whatever the "
                  f"market reads")
        with calibration.phase(f"list {item} from {slot}"):
            listed = model.list_slot(*slot, floor=entry["floor"],
                                     why=entry["why"], verbose=verbose,
                                     lands_in=lands_in, expect_item=item,
                                     price=opening or None,
                                     listed_at=opening or None)
        model.place(lands_in, row_model.Row(item, qty=listed["qty"],
                                            price=listed["price"],
                                            buy_cost=entry["floor"],
                                            floor_at=entry["floor"]))
        entry["listed"] = True
        job["rows"].append(lands_in)
        job["listed"] += listed["qty"]
    return full


def finish_cash(model, job, first, last, confirm=True, verbose=True):
    item = job["core"]
    while job["step"] != "list":
        if job["step"] == "buy":
            cash_buy_step(job, confirm=confirm, verbose=verbose)
        elif job["step"] == "voucher":
            cash_voucher_step(job, confirm=confirm, verbose=verbose)
        elif job["step"] == "gem":
            cash_gem_step(job, confirm=confirm, verbose=verbose)
        elif job["step"] == "usegem":
            cash_usegem_step(job, verbose=verbose)
        elif job["step"] == "use":
            cash_use_step(job, verbose=verbose)
        elif job["step"] == "land":
            cash_land_step(job, verbose=verbose)
        elif job["step"] == "cancelled":
            task_done("resupply", core=item, bought=job["bought"])
            return None
        else:
            raise NotReady(f"the {item} job is at an unknown step "
                           f"{job['step']!r}.")
    full = cash_list_step(model, job, first, last, verbose=verbose)
    if not full:
        task_done("resupply", core=item, bought=job["bought"],
                  listed=job["listed"], rows=job["rows"])
    funds = cashshop.currency_of(item)
    calibration.phases_table(
        f"resupply {item}: bought {job['bought']} at {job['price']:,} "
        + (f"gem(s) from a {funds['from']}" if funds else "Cash")
        + (f" bought for {job['gem_paid']:,} Cash" if job["gem_paid"] else "")
        + (f" after a {job['voucher_paid']:,} voucher" if job["voucher_paid"]
           else "")
        + f", listed {job['listed']} in rows {job['rows']}")
    return {"item": item, "bought": job["bought"], "listed": job["listed"],
            "rows": job["rows"]}


def resupply_cash(model, item, held, first, last, confirm=True,
                  verbose=True):
    global _PENDING
    job = (_PENDING if _PENDING and _PENDING.get("kind") == "cash"
           and _PENDING.get("core") == item else None)
    if job is None:
        job = start_cash(model, item, held, first, last, verbose=verbose)
        if job is None:
            return None
    else:
        print("")
        print(f"-- {item}: carrying on at {job['step']} where it stopped --")
        task("resupply", core=item, tab=row_model.WORK_TAB,
             resumed=job["step"])
    _PENDING = job
    try:
        out = finish_cash(model, job, first, last, confirm=confirm,
                          verbose=verbose)
    except calibration.ServerStalled:
        print(f"  {item} keeps its place at {job['step']}; the next pass "
              f"carries on there")
        raise
    except (NotReady, cashshop.Refused, buy.Refused):
        if job["voucher_paid"] or job["bought"]:
            print(f"  {item} keeps its place at {job['step']}; a voucher or "
                  f"the item is in the bag and the next pass carries on there")
            raise
        _PENDING = None
        raise
    except BaseException:
        _PENDING = None
        raise
    _PENDING = None
    return out


def do_cash(item=None, cancel=False, verbose=True):
    run = calibration.load_shared()["run"]
    first, last = int(run["relist_from"]), int(run["relist_to"])
    item = item or (cashshop.items()[0] if cashshop.items() else None)
    if not item:
        raise NotReady("no cash shop item was named and none is configured "
                       "under resupply.cash_shop.rows; nothing to do.")
    initialise(verbose=verbose)
    register_tab(verbose=verbose)
    model = seed(verbose=verbose)
    print("")
    held = cash_status(model, item, first, last, cashshop.rows_wanted(item))
    started = time.perf_counter()
    if cancel:
        print(f"  dry run: whichever dialog comes first gets Cancel, not "
              f"{cashshop.OK_WORD}")
    try:
        out = resupply_cash(model, item, held, first, last,
                            confirm=not cancel, verbose=verbose)
    finally:
        if not back_to_the_shop(verbose=verbose):
            raise NotReady("the Agent Shop is not open after the Cash Shop.")
        register_tab(verbose=verbose)
    print(f"  done in {(time.perf_counter() - started) * 1000:.0f} ms")
    return out


def voucher_buy_flow(confirm=True, verbose=True):
    word = get_price.VOUCHER_WORD
    print("")
    print(f"-- {word} voucher: buy the cheapest on offer --")
    if not confirm:
        print(f"  dry run: the {buy.DIALOG_MARKER} dialog gets "
              f"{buy.CANCEL_WORD}, not {buy.CONFIRM_WORD}")
    with calibration.phase("search the voucher"):
        row = get_price.get_voucher_price(verbose=verbose)
    if row is None:
        raise NotReady(f"the {word} voucher would not price; nothing to buy.")
    print(f"  row 1: {row['name']!r} at {row['unit_price']:,} a voucher")
    with calibration.phase("confirm the sort"):
        try:
            sort = get_price.confirm_sort_low_to_high("voucher",
                                                      verbose=verbose)
        except get_price.NotReady as exc:
            raise NotReady(f"{exc} Not buying.")
    if sort != "ok":
        raise NotReady(f"the offers table went away while the sort was "
                       f"being read ({sort}); not buying.")
    with calibration.phase("buy row 1"):
        try:
            out = buy.buy_voucher(confirm=confirm, verbose=verbose)
        except buy.Refused as exc:
            raise NotReady(f"{exc}")
    print(f"  {word} voucher: "
          + (f"bought 1 at {out['price']:,}" if out["bought"]
             else "cancelled at the dialog"))
    return out


def use_question(image=None):
    image = image if image is not None else calibration.grab()
    fold = lambda v: re.sub(r"[^a-z0-9]", "", (v or "").lower())
    line = calibration.read_line(image, calibration._box(USE_LINE_F))
    if not any(fold(word) in fold(line) for word in USE_QUESTIONS):
        return None
    button = calibration.read_line(image, calibration._box(USE_YES_F))
    if not fold(button).startswith(fold(YES_WORD)):
        return None
    return calibration._centre(USE_YES_F)


def answer_use_question(point, verbose=True):
    if verbose:
        print(f"  the game asks whether to use it; clicking {YES_WORD} "
              f"at {list(point)}")
    calibration.snap("use_question")
    calibration.click(*point)
    calibration.park()


def answer_pending_use_question(verbose=True):
    asked = use_question()
    if asked is None:
        return False
    print(f"  the game is already asking whether to use it; answering that "
          f"before anything else")
    answer_use_question(asked, verbose=verbose)
    with calibration.phase("wait for the question to go"):
        deadline = time.monotonic() + calibration.DIALOG_TIMEOUT
        while time.monotonic() < deadline:
            if use_question() is None:
                return True
            time.sleep(row_model.POLL_GAP)
    calibration.snap("use_question_stayed")
    raise NotReady(f"the question about using it stayed on screen "
                   f"{calibration.DIALOG_TIMEOUT:g}s after {YES_WORD}. "
                   f"Nothing more clicked.")


def voucher_use(before, verbose=True, what=None):
    tab = row_model.WORK_TAB
    what = what or "voucher"
    if answer_pending_use_question(verbose=verbose):
        print(f"  the question was answered; the {what} is used")
        return None
    with calibration.phase("close the Agent Shop"):
        calibration.close_everything()
    with calibration.phase(f"find the {what} on tab {tab}"):
        arrived = await_arrival(set(before), verbose=verbose)
    if not arrived:
        calibration.snap("voucher_never_landed")
        raise NotReady(
            f"no slot on tab {tab} filled after buying the {what}; it is "
            f"not where it was expected. Nothing used.")
    slot = min(arrived)
    point = calibration.inventory_slot_point(*slot)
    print(f"  the {what} landed in tab {tab} slot {slot}; right-clicking "
          f"it at {point} to use it")
    with calibration.phase(f"right-click the {what}"):
        calibration.right_click(*point)
        calibration.park()
    with calibration.phase("wait for the question"):
        deadline = time.monotonic() + calibration.DIALOG_TIMEOUT
        asked = None
        while asked is None and time.monotonic() < deadline:
            asked = use_question()
            if asked is None:
                time.sleep(row_model.POLL_GAP)
    if asked is None:
        calibration.snap("voucher_not_asked")
        raise NotReady(
            f"no question about using it showed "
            f"{calibration.DIALOG_TIMEOUT:g}s after right-clicking the "
            f"{what} in tab {tab} slot {slot}. Nothing more clicked.")
    with calibration.phase("answer the question"):
        answer_use_question(asked, verbose=verbose)
    print(f"  {YES_WORD} clicked on slot {slot}; the {what} is used")
    return slot


def use_voucher_in_bag(verbose=True):
    tab = row_model.WORK_TAB
    if not inv.focus_game():
        raise NotReady("could not bring the game to the foreground.")
    calibration.load(force=True)
    calibration.phases_reset()
    if use_question() is None:
        held = work_tab_slots(verbose=verbose)
        if len(held) != 1:
            raise NotReady(
                f"tab {tab} holds {len(held)} slot(s); the voucher to use "
                f"must be the only thing there. Nothing clicked.")
    slot = voucher_use(set(), verbose=verbose)
    value = cashshop.get_cc(verbose=verbose)
    print(f"  Cash after using the voucher"
          + (f" from slot {slot}" if slot else "") + ": "
          + (f"{value:,}" if value is not None else "unreadable"))
    calibration.phases_table("use the voucher in the bag")
    return value


def buy_and_use_voucher(confirm=True, verbose=True):
    tab = row_model.WORK_TAB
    initialise(verbose=verbose)
    started = time.perf_counter()
    calibration.phases_reset()
    with calibration.phase(f"select inventory tab {tab}"):
        before = work_tab_slots(verbose=verbose)
    out = voucher_buy_flow(confirm=confirm, verbose=verbose)
    if out["bought"]:
        out["slot"] = voucher_use(before, verbose=verbose)
        with calibration.phase("read the Cash after the voucher"):
            out["cc"] = cashshop.get_cc(verbose=verbose)
        print(f"  Cash after the voucher: "
              + (f"{out['cc']:,}" if out["cc"] is not None else "unread"))
    calibration.phases_table(
        f"{get_price.VOUCHER_WORD} voucher: "
        + (f"bought 1 at {out['price']:,} and used" if out["bought"]
           else "cancelled at the dialog"))
    print(f"  done in {(time.perf_counter() - started) * 1000:.0f} ms")
    return out


def do_cc(verbose=True):
    if not inv.focus_game():
        raise NotReady("could not bring the game to the foreground.")
    calibration.load(force=True)
    if calibration._trade_window_open():
        calibration.close_everything(verbose=verbose)
    started = time.perf_counter()
    value = cashshop.get_cc(verbose=verbose)
    print(f"  Cash {value:,}" if value is not None else "  Cash: unreadable")
    print(f"  done in {(time.perf_counter() - started) * 1000:.0f} ms")
    return value


def our_set_price(model, set_name):
    prices = [row.sell_unit for row in (model._slots or {}).values()
              if row is not None
              and row_model.item_key(row.name) == row_model.item_key(set_name)]
    return min(prices) if prices else None


def buy_sets_under(job, verbose=True):
    run = calibration.load_shared()["resupply"]
    pair, set_name, want_min = job["pair"], job["set"], job["want_min"]
    attempt = 0
    while job["bought"] < want_min:
        if job["set_row"] is None:
            with calibration.phase(f"price {set_name} again"):
                job["set_row"] = get_price.get_price(pair, verbose=False)
            if job["set_row"] is None:
                print(f"  {set_name} would not price again; not buying "
                      f"blind.")
                job["more"] = False
                break
        cheapest = job["set_row"]["unit_price"]
        if cheapest >= job["under"]:
            print(f"  the cheapest {set_name} is {cheapest:,} a Set, not under "
                  f"{job['under']:,}; nothing left to buy under ours")
            job["more"] = False
            break
        print(f"  {job['bought']}/{want_min} {set_name} held; row 1 asks "
              f"{cheapest:,} a Set against ours at {job['ours']:,}")
        job["orders"] += 1
        try:
            with calibration.phase(f"buy order {job['orders']}"):
                got = buy.buy_row_one(pair, want_min - job["bought"],
                                      verbose=verbose, held=job["bought"],
                                      floor_qty=want_min,
                                      ceiling=job["want_max"],
                                      sells_at=job["ours"], gap=None,
                                      search=False)
        except buy.Refused as exc:
            job["set_row"] = None
            if not getattr(exc, "retryable", False):
                print(f"  stopping: {exc}")
                job["more"] = False
                break
            attempt += 1
            print(f"  attempt {attempt}/{run['buy_retries']}: {exc}")
            if attempt >= int(run["buy_retries"]):
                print(f"  the board kept moving through "
                      f"{run['buy_retries']} attempt(s); giving up on "
                      f"{set_name} this cycle.")
                job["more"] = False
                break
            continue
        attempt = 0
        job["set_row"] = None
        if got["bought"] <= 0:
            print(f"  the last order bought nothing; stopping.")
            job["more"] = False
            break
        job["bought"] += got["bought"]
        job["paid"] += got["spent"]
    if job["bought"] <= 0:
        print(f"  nothing bought; nothing to compress.")
        return False
    if job["bought"] < want_min:
        print(f"  bought {job['bought']} of the {want_min} wanted; listing "
              f"what there is")
    if job["set_row"] is None:
        with calibration.phase(f"price {set_name} after buying"):
            job["set_row"] = get_price.get_price(pair, verbose=False)
    job["step"] = "list"
    return True


def list_sets_under(model, job, first, last, verbose=True):
    set_name, work = job["set"], job["work"]
    bought, paid = job["bought"], job["paid"]
    unit_cost = -(-paid // bought)
    with calibration.phase("select the Register tab"):
        register_tab(verbose=verbose)
    with calibration.phase(f"select inventory tab {row_model.WORK_TAB}"):
        calibration.click(*calibration.inventory_tab_point(row_model.WORK_TAB),
                          settle=0.0)
        time.sleep(row_model.TAB_SETTLE)
    market = (job["set_row"] or {}).get("unit_price") or job["under"]
    print(f"  listing the {set_name} that landed in {work}: {bought} "
          f"bought at {unit_cost:,} a Set; asking ours, {job['ours']:,} a Set, "
          f"no lower than {unit_cost:,}")
    empty = [i for i in model.empty() if first <= i <= last]
    if not empty:
        print(f"  rows {first}-{last} are full; the {set_name} stays on tab "
              f"{row_model.WORK_TAB}.")
        model.hold_work(work, set_name)
        return None
    lands_in = min(empty)
    try:
        with calibration.phase(f"list {set_name} from {work}"):
            listed = model.list_slot(*work, verbose=verbose,
                                     why=f"a {set_name} cost {unit_cost:,} "
                                         f"this pass",
                                     lands_in=lands_in, unit_market=market,
                                     floor_each=unit_cost,
                                     price_each=job["ours"], wait_fill=False)
    except row_model.NothingLoaded as exc:
        model.hold_work(work, set_name)
        raise NotReady(
            f"{exc} Tab {row_model.WORK_TAB} slot {work} is left to the "
            f"{set_name} that landed there.")
    job["step"] = "listed"
    model.place(lands_in, row_model.Row(
        set_name, qty=listed["qty"], price=listed["price"],
        buy_cost=unit_cost, units=listed["units"], floor_at=unit_cost))
    if listed["units"] != bought:
        print(f"  the panel counted {listed['units']} in the bundle against "
              f"the {bought} bought; the slot holds every {set_name} the "
              f"game stacked into it")
    return lands_in


def buy_under_lister(model, slot, first, last, verbose=True):
    core = calibration.FAVOURITE_ITEMS[str(slot)]
    pair = calibration.pair_slot(slot)
    cap = int(calibration.buy_under_lister(core))
    if cap <= 0 or pair is None or not buying_enabled(core):
        return [], None
    set_name = calibration.FAVOURITE_ITEMS[str(pair)]
    print("")
    print(f"-- {set_name} under our listing: up to {cap} row(s) --")
    ours = our_set_price(model, set_name)
    if ours is None:
        print(f"  no {set_name} of ours on the board, so there is no listing "
              f"of ours to buy under; not buying")
        return [], None
    core_row, set_row, diff = price_gap(slot)
    if diff is None:
        return [], None
    read = (core_row, set_row)
    core_at = core_row["unit_price"]
    gap = int(calibration.buy_under_gap(core))
    under = min(ours, core_at) - gap
    print(f"  ours is listed at {ours:,} a Set and a {core} sells at "
          f"{core_at:,}; buying every Set under {under:,}, {gap:,} a Set "
          f"below the lower of the two")
    want_min = calibration.buy_min(core)
    want_max = calibration.buy_max(core)
    rows = []
    while len(rows) < cap:
        if set_row is not None and set_row["unit_price"] >= under:
            print(f"  the cheapest {set_name} is {set_row['unit_price']:,} a "
                  f"Set, not under {under:,}; nothing to buy under ours")
            break
        if set_row is not None and not alz_covers(
                set_name, 1, set_row["unit_price"], verbose=verbose):
            break
        if not [i for i in model.empty() if first <= i <= last]:
            print(f"  rows {first}-{last} are full; a bought {set_name} with "
                  f"nowhere to list stays on tab {row_model.WORK_TAB}; not "
                  f"buying.")
            break
        war.avoid(allowance=PASS_ALLOWANCE, verbose=verbose)
        landing = model.next_work_slot()
        if landing is None:
            raise NotReady(f"the run holds every slot of tab "
                           f"{row_model.WORK_TAB}; nowhere for a bought "
                           f"{set_name} to land.")
        with calibration.phase(f"select inventory tab {row_model.WORK_TAB}"):
            calibration.click(*calibration.inventory_tab_point(
                row_model.WORK_TAB), settle=0.0)
            time.sleep(TAB_SETTLE)
        task("resupply", core=set_name, slot=pair, tab=row_model.WORK_TAB,
             under=under, landing=list(landing))
        job = {"pair": pair, "set": set_name, "ours": ours, "under": under,
               "want_min": want_min, "want_max": want_max, "set_row": set_row,
               "work": landing, "orders": 0, "bought": 0, "paid": 0,
               "more": True, "step": "buy"}
        calibration.phases_reset()
        try:
            if not buy_sets_under(job, verbose=verbose):
                task_done("resupply", core=set_name, bought=0)
                break
            lands_in = list_sets_under(model, job, first, last,
                                       verbose=verbose)
        except BaseException:
            if job["bought"] > 0 and job["step"] != "listed":
                model.hold_work(landing, set_name)
            raise
        if lands_in is None:
            break
        rows.append(lands_in)
        task_done("resupply", core=set_name, bought=job["bought"],
                  rows=[lands_in])
        calibration.phases_table(
            f"{set_name} under ours: bought {job['bought']} at "
            f"{-(-job['paid'] // job['bought']):,}, listed in row {lands_in}")
        print(f"  {set_name}: {len(rows)} of {cap} row(s) bought under ours "
              f"this pass")
        set_row = None
        if not job["more"]:
            break
    return rows, (None if rows else read)


def gifts_at_the_end(verbose=True):
    import collect_gifts
    try:
        taken = collect_gifts.collect_gifts(verbose=verbose)
    except (collect_gifts.Refused, RuntimeError) as exc:
        print(f"  the gift box was left alone: {exc}")
        return 0
    if verbose:
        print(f"  took {taken} gift(s)")
    return taken


def rest_the_game(verbose=True):
    print("")
    print(f"  returning the game to its default state")
    calibration.close_everything(verbose=verbose)
    with calibration.phase("collect the gift box"):
        gifts_at_the_end(verbose=verbose)
    if not back_to_the_shop(verbose=verbose):
        raise NotReady("the Agent Shop would not reopen after resting.")
    register_tab(verbose=verbose)


def back_to_the_shop(verbose=True):
    from open_inventory import VK_ESCAPE, press
    answer_pending_use_question(verbose=verbose)
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


def resupply_order(jobs):
    fold = lambda v: re.sub(r"[^a-z0-9]", "", (v or "").lower())
    listed = [fold(name)
              for name in calibration.load_shared()["resupply"]["order"]]

    def rank(job):
        kind, key, name = job
        first = (listed.index(fold(name)) if fold(name) in listed
                 else len(listed))
        return (first, 0, 0) if kind == "cash" else (first, 1, int(key))
    return sorted(jobs, key=rank)


def price_table(model, first, last, verbose=True):
    free = buying_rows(model, first, last)
    read = {}
    for slot in core_slots():
        if not free or not craft_route(calibration.FAVOURITE_ITEMS[str(slot)]):
            continue
        war.avoid(allowance=PASS_ALLOWANCE, verbose=verbose)
        try:
            _bought, pair = buy_under_lister(model, slot, first, last,
                                             verbose=verbose)
            if pair is not None:
                read[slot] = pair
        except (craft.Refused, buy.Refused, NotReady) as exc:
            print(f"  buying under our listing stopped: {exc}")
    held = rows_by_core(model, first, last)
    priced, wanted, short = {}, {}, []
    print("")
    print(f"  counting only rows {first}-{last}; rows outside it are not "
          f"repriced and do not count; {len(free)} row(s) free")
    print(f"  {'core':<30}{'rows':>6}{'buy/u':>10}{'sell/u':>10}"
          f"{'margin':>10}{'wants':>7}   short?")
    for slot, count in sorted(held.items()):
        core = calibration.FAVOURITE_ITEMS[str(slot)]
        mark, diff, wants = "", None, None
        buy_at = sell_at = None
        most = calibration.rows_wanted_at_most(core)
        if not convert.cell_for(core) and not craft_route(core):
            mark = "neither convertible nor craftable"
        elif not buying_enabled(core):
            mark = "buying off"
        elif most is not None and count >= most:
            mark = f"holds the most it can want, {most}; not priced"
        elif not free:
            mark = "no row free; not priced"
        else:
            core_row, set_row, diff = price_gap(slot, say=False,
                                                rows=read.get(slot))
            if diff is None:
                mark = "would not price"
            else:
                buy_at, sell_at = ((core_row, set_row) if craft_route(core)
                                   else (set_row, core_row))
                priced[slot] = buy_at
                read[slot] = (core_row, set_row)
                wants = calibration.rows_by_margin(core, diff)
                if count < wants:
                    mark = "YES"
                    short.append(slot)
                    wanted[slot] = wants
        gap = '-' if diff is None else f'{diff:,}'
        print(f"  {core:<30}{count:>6}"
              f"{('-' if buy_at is None else f'{buy_at['unit_price']:,}'):>10}"
              f"{('-' if sell_at is None else f'{sell_at['unit_price']:,}'):>10}"
              f"{gap:>10}{('-' if wants is None else wants):>7}   {mark}")
    if not short:
        print("")
        print(f"  nothing inside rows {first}-{last} holds fewer rows "
              f"than its margin is worth.")
    return held, priced, short, wanted, read


def resupply_core_rows(model, slot, have, wants, priced, first, last, done,
                       verbose=True, rows=None):
    core_here = calibration.FAVOURITE_ITEMS[str(slot)]
    route = resupply_chaos if craft_route(core_here) else resupply_one
    bought_name = (core_here if craft_route(core_here)
                   else calibration.FAVOURITE_ITEMS[
                       str(calibration.pair_slot(slot))])
    while have < wants:
        war.avoid(allowance=PASS_ALLOWANCE, verbose=verbose)
        least = (calibration.CRAFT_CORES_PER_SET
                 if craft_route(core_here) else 1)
        if _PENDING is None and slot in priced and not alz_covers(
                bought_name, least, priced[slot]["unit_price"],
                verbose=verbose):
            break
        try:
            out = route(model, slot, have, first, last, verbose=verbose,
                        rows=rows)
        except (convert.Refused, craft.Refused, buy.Refused,
                NotReady) as exc:
            print(f"  resupply of {core_here!r} stopped: {exc}")
            out = None
        rows = None
        if not out or not out.get("rows"):
            break
        done.append(out)
        have += len(out["rows"])
        print(f"  {core_here}: {have} of {wants} row(s) after that one")


def resupply_cash_rows(model, item, wants, first, last, done, verbose=True):
    print("")
    have = cash_status(model, item, first, last, wants)
    while have < wants:
        war.avoid(allowance=PASS_ALLOWANCE, verbose=verbose)
        try:
            out = resupply_cash(model, item, have, first, last,
                                verbose=verbose)
        except (cashshop.Refused, NotReady) as exc:
            print(f"  resupply of {item!r} stopped: {exc}")
            out = None
        if not out or not out.get("rows"):
            break
        done.append(out)
        have += len(out["rows"])
        print(f"  {item}: {have} of {wants} row(s) after that one")


def resupply_pass(model, first, last, verbose=True):
    run = calibration.load_shared()["resupply"]
    if not run["enabled"] and _PENDING is None:
        return []
    done = []
    try:
        if _PENDING is not None:
            core_here = _PENDING["core"]
            war.avoid(allowance=PASS_ALLOWANCE, verbose=verbose)
            try:
                if _PENDING.get("kind") == "cash":
                    out = resupply_cash(model, _PENDING["core"], 0, first,
                                        last, verbose=verbose)
                elif _PENDING.get("kind") == "special":
                    out = resupply_special(model, first, last,
                                           verbose=verbose)
                else:
                    route = (resupply_chaos if craft_route(core_here)
                             else resupply_one)
                    out = route(model, _PENDING["slot"], 0, first, last,
                                verbose=verbose)
            except (convert.Refused, craft.Refused, buy.Refused,
                    cashshop.Refused, NotReady) as exc:
                print(f"  resupply of {core_here!r} stopped: {exc}")
                out = None
            if out and out.get("rows"):
                done.append(out)
        if not run["enabled"]:
            return done
        jobs = resupply_order(
            [("cash", item, item) for item in cashshop.items()
             if cashshop.rows_wanted(item) > 0]
            + [("core", slot, calibration.FAVOURITE_ITEMS[str(slot)])
               for slot in core_slots()])
        print("")
        print("  resupply order: "
              + ", ".join(name for _kind, _key, name in jobs))
        table = None
        for kind, key, name in jobs:
            if kind == "cash":
                resupply_cash_rows(model, name, cashshop.rows_wanted(name),
                                   first, last, done, verbose=verbose)
                continue
            if table is None:
                table = price_table(model, first, last, verbose=verbose)
            held, priced, short, wanted, read = table
            if key not in short:
                continue
            resupply_core_rows(model, key, held[key], wanted[key], priced,
                               first, last, done, verbose=verbose,
                               rows=read.get(key))
    finally:
        if not back_to_the_shop(verbose=verbose):
            raise NotReady("the Agent Shop is not open after resupplying.")
        register_tab(verbose=verbose)
    return done


def do_collect_gifts(verbose=True):
    import collect_gifts
    started = time.perf_counter()
    taken = collect_gifts.collect_gifts(verbose=verbose)
    print(f"  collected {taken} gift(s) in "
          f"{(time.perf_counter() - started) * 1000:.0f} ms")
    return taken


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
    print("  RECOVERY -- after a run stopped part way through a resupply")
    print("  py src/driver.py collect         collect what sold in the rows,")
    print("                                   cancelling nothing")
    print("  py src/driver.py craft chaos     craft the Chaos Core already")
    print("                                   in the bag into Sets and list")
    print("                                   them")
    print("  py src/driver.py convert N       convert the Set already in ")
    print("                                   the bag into favourite N's ")
    print("                                   core and list it")
    print("  neither buys; both price only to know what to list at")
    print("")
    print("  py src/driver.py cash [ITEM] [cancel]  resupply one cash shop")
    print("                                   item (the first under")
    print("                                   resupply.cash_shop.rows if none is")
    print("                                   named): Cash checked, a Gold")
    print("                                   voucher bought and used if short,")
    print("                                   the item bought and listed at its")
    print("                                   Cash price over 1000 of the")
    print("                                   voucher; 'cancel' cancels at the")
    print("                                   first dialog it reaches")
    print("  py src/driver.py scan            read the balance, walk rows 1-21")
    print("                                   and print the model, no changes")
    print("  py src/driver.py cancel N        cancel row N (collects it first")
    print("                                   if it has sold)")
    print("  py src/driver.py relist [N M [MIN]]  cancel and relist rows N-M,")
    print("                                   looping for MIN minutes;")
    print("                                   the run block in config.json")
    print("                                   if no range is given")
    print("  py src/driver.py list R C [PRICE [TAB]]  list inventory slot (R,C)")
    print("                                   of TAB (default the work tab); the")
    print("                                   panel's own suggestion if PRICE is")
    print("                                   0 or absent")
    print("  py src/driver.py row N           read row N without touching it")
    print("  py src/driver.py price N         market price for favourite slot N")
    print("  py src/driver.py alz             read the balance")
    print("  py src/driver.py buy_voucher [cancel]  search the Gold voucher,")
    print("                                   check the sort is Price: Low to")
    print("                                   High, buy row 1, then right-click")
    print("                                   it on tab 4, answer Yes to the")
    print("                                   game's question and so turn it")
    print("                                   into Cash; 'cancel' walks the")
    print("                                   same way and cancels the dialog")
    print("  py src/driver.py use_voucher     use the voucher already on tab 4")
    print("                                   (the only thing there): answer")
    print("                                   the question if it is on screen,")
    print("                                   else right-click and answer it,")
    print("                                   then read the Cash")
    print("  py src/driver.py cc              open the Cash Shop, read the Cash")
    print("                                   balance in its bottom right corner")
    print("                                   and close it again")
    print("  py src/driver.py voucher         search the Agent Shop for the")
    print("                                   CABAL Gift Voucher (Gold) and")
    print("                                   read what one costs")
    print("  py src/driver.py gifts           open the gift box, take the")
    print("                                   gifts on offer and close it")


def _elapsed(seconds):
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


def _git_id():
    here = os.path.dirname(os.path.abspath(__file__))

    def git(*a):
        try:
            done = subprocess.run(("git", "-C", here) + a,
                                  capture_output=True, text=True,
                                  timeout=_SHARED["timing"]["git_timeout"])
        except Exception:
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    commit = git("rev-parse", "--short", "HEAD")
    if not commit:
        return "no-git"
    status = git("status", "--porcelain", "--", here) or ""
    edited = any(line[3:].strip().endswith(".py")
                 for line in status.splitlines() if line)
    return commit + ("+edits" if edited else "")


def main():
    import traceback
    began = datetime.datetime.now()
    started = time.monotonic()
    args = _plain_argv()
    calibration.log_to_file(args[0].lower() if args else "run")
    print(f"  code {_git_id()}")
    outcome, note = "FINISHED", ""
    try:
        print(f"  screen {calibration.require_screen()}")
        print(f"  ledger {ledger.DB} run {ledger.start()}")
        calibration.frames_on(True if "--frames" in sys.argv[1:] else None)
        calibration.watch_for_stop()
        _dispatch(args)
    except KeyboardInterrupt:
        outcome, note = "STOPPED", "interrupted from the keyboard"
    except row_model.Divergence as exc:
        outcome, note = "STOPPED", f"{exc}"
    except BaseException as exc:
        outcome = "CRASHED"
        note = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        try:
            calibration.frames_written()
        except BaseException as exc:
            print(f"  the frames could not be flushed: "
                  f"{type(exc).__name__}: {exc}")
        try:
            calibration.recording_off()
        except BaseException as exc:
            print(f"  the recording could not be closed: "
                  f"{type(exc).__name__}: {exc}")
        try:
            ledger.print_run_profit()
        except BaseException as exc:
            print(f"  the profit summary could not be built: "
                  f"{type(exc).__name__}: {exc}")
        print(f"  started {began:%H:%M:%S}, ended "
              f"{datetime.datetime.now():%H:%M:%S}, ran for "
              f"{_elapsed(time.monotonic() - started)}")
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
    elif what == "collect":
        do_collect()
    elif what == "relist":
        do_relist(args[1] if len(args) > 1 else None,
                  args[2] if len(args) > 2 else None,
                  args[3] if len(args) > 3 else None)
    elif what == "list" and len(args) > 2:
        do_list(int(args[1]), int(args[2]),
                int(args[3]) if len(args) > 3 and int(args[3]) else None,
                tab=int(args[4]) if len(args) > 4 else None,
                item=" ".join(args[5:]) if len(args) > 5 else None)
    elif what == "row" and len(args) > 1:
        initialise()
        register_tab()
        row_at(row_model.RowModel().seed({}), int(args[1]))
    elif what == "resupply" and len(args) > 1:
        do_resupply(int(args[1]))
    elif what == "craft" and len(args) > 1 and args[1].lower() == "chaos":
        do_craft_chaos()
    elif what == "convert" and len(args) > 1:
        do_convert(int(args[1]))
    elif what in ("cash", "vip"):
        rest = [a for a in args[1:] if a.lower() != "cancel"]
        do_cash(item=" ".join(rest) if rest else None,
                cancel=any(a.lower() == "cancel" for a in args[1:]))
    elif what == "cc":
        do_cc()
    elif what == "buy_voucher":
        buy_and_use_voucher(
            confirm=not (len(args) > 1 and args[1].lower() == "cancel"))
    elif what == "use_voucher":
        use_voucher_in_bag()
    elif what == "chaos":
        crafted = [n for n in core_slots()
                   if craft_route(calibration.FAVOURITE_ITEMS[str(n)])]
        if not crafted:
            print("  nothing in the favourites is crafted rather than "
                  "converted; there is no chaos resupply to run.")
            return
        do_resupply(crafted[0])
    elif what == "gifts":
        do_collect_gifts()
    elif what == "scan":
        do_scan()
    elif what == "price" and len(args) > 1:
        initialise()
        market(int(args[1]))
    elif what == "alz":
        initialise()
        print(f"  balance {balance() or 'unreadable'}")
    elif what == "voucher":
        if not inv.focus_game():
            raise NotReady("could not bring the game to the foreground.")
        calibration.load(force=True)
        out = get_price.get_voucher_price()
        if out is None:
            print("  the voucher would not price.")
        else:
            print(f"  1 {out['name']} = {out['unit_price']:,} Alz")
    else:
        usage()
        sys.exit(2)


if __name__ == "__main__":
    main()
