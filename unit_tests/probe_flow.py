"""Every coordinate and every reader the trading flow uses, against this screen.

    py unit_tests\\probe_flow.py            # probe the live screen
    py unit_tests\\probe_flow.py FRAME.png  # probe a saved frame instead

READ-ONLY. It grabs the screen and runs OCR. No clicks, no keys, no scrolling,
no window is opened or closed -- so it is safe alongside a live trading run.

WHAT IT IS FOR. When something moves -- a resolution change, a patch, a dragged
window -- the script's failure is a click landing somewhere unintended, and the
symptom is a row that would not relist or a search that "did not run". This
prints what every region actually reads RIGHT NOW, so the broken one is visible
instead of inferred.

It walks the whole flow in the order the script does:

    0  calibration and the frame itself
    1  favourite slots        -- where a search is clicked
    2  purchase tab           -- what a buy reads and where Buy is
    3  confirm dialog         -- the last thing before Alz moves
    4  vendor and the grid    -- where a conversion is Alt+clicked
    5  mass purchase dialog   -- the typed quantity and its limit
    6  inventory              -- the counts the pipeline is measured by
    7  register / relist      -- the table, the panel, the buttons
    8  NPC                    -- the walk back
    9  floors and thresholds  -- what the money rules currently are

Each line says what was READ, not what was expected, and anything unreadable is
called out rather than skipped. A region that is off-screen is flagged
separately from one that is on-screen and simply empty: they have different
causes and different fixes.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import trade as m  # noqa: E402

# Belt and braces: nothing here calls an input primitive, but a future edit
# might, and this file must stay safe to run against a live session.
m.NO_INPUT = True

OK, BAD, WARN = "  ok  ", " MISS ", " warn "
_issues: list[str] = []


def note(level: str, label: str, detail: str = "") -> None:
    print(f"[{level}] {label}" + (f"   {detail}" if detail else ""))
    if level is BAD:
        _issues.append(label)


def head(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def on_screen(point, screen) -> bool:
    return 0 <= point[0] < screen[0] and 0 <= point[1] < screen[1]


def box_on_screen(box, screen) -> bool:
    return (0 <= box[0] < box[2] <= screen[0]
            and 0 <= box[1] < box[3] <= screen[1])


def words_in(shot, box, conf=40.0) -> str:
    try:
        got = [w for w in m.find_words(shot, box, 20) if w.conf >= conf]
    except Exception as exc:  # noqa: BLE001
        return f"<error {type(exc).__name__}: {exc}>"
    return " ".join(w.text for w in sorted(got, key=lambda w: (w.centre[1] // 12,
                                                              w.centre[0])))


def probe_box(shot, name, box, screen, conf=40.0) -> None:
    """Report a region: where it is, whether it is on screen, what it reads."""
    if not box_on_screen(box, screen):
        note(BAD, f"{name:26} {str(box):26}", "OFF SCREEN or inverted")
        return
    text = words_in(shot, box, conf)
    level = OK if text else WARN
    note(level, f"{name:26} {str(box):26}",
         (text[:64] if text else "reads nothing (may simply be empty now)"))


def main() -> int:
    if len(sys.argv) > 1:
        from PIL import Image
        path = Path(sys.argv[1])
        if not path.exists():
            print(f"no such frame: {path}")
            return 2
        shot = Image.open(path)
        source = str(path)
    else:
        shot = m.grab()
        source = "the live screen"

    screen = shot.size

    # ---------------------------------------------------------------- 0
    head(f"0. CALIBRATION -- probing {source}")
    note(OK, "frame size", f"{screen[0]}x{screen[1]}")
    lay = m.LAYOUT
    note(OK, "layout", f"origin={lay.origin} scale={lay.scale:.4f} "
                       f"measured_from={lay.measured_from!r}")
    if lay.measured_from.startswith("reference"):
        note(WARN, "layout is the REFERENCE default",
             "nothing has been measured this session; run --calibrate, or "
             "these coordinates are assumptions")
    note(OK, "reference", f"screen={m.REF_SCREEN} origin={m.REF_TRADE_ORIGIN}")
    if tuple(screen) != tuple(m.REF_SCREEN):
        note(WARN, "screen differs from the reference",
             f"{screen} vs {m.REF_SCREEN} -- every unscaled constant is wrong "
             f"by that ratio")
    note(OK, "constants under calibration",
         f"{len(m._TRADE_FRAME_GEOMETRY)} in the trade frame, "
         f"{len(m._INVENTORY_FRAME_GEOMETRY)} inventory, "
         f"{len(m._CLIENT_FRAME_GEOMETRY)} client")

    # what state is the game in at all?
    head("0b. WHAT IS ON SCREEN")
    states = {
        "trade window": m.trade_window_open(shot),
        "register tab": m.register_tab_open(shot),
        "purchase tab": m.purchase_tab_open(shot),
        "vendor shop": m.vendor_shop_open(shot),
        "inventory panel": m.inventory_origin(shot) is not None,
        "a dialog": m.dialog_present(shot) if hasattr(m, "dialog_present") else "?",
        "DISCONNECTED": m.game_disconnected(shot),
    }
    for label, value in states.items():
        note(OK if value else WARN, f"{label:26}", str(value))
    if states["DISCONNECTED"]:
        note(BAD, "the client is disconnected",
             "everything below reads a frozen screen")

    # ---------------------------------------------------------------- 1
    head("1. FAVOURITE SLOTS -- where a search is clicked")
    note(OK, "geometry", f"first={m.FAVOURITE_FIRST} pitch={m.FAVOURITE_PITCH}")
    for slot in sorted(m.FAVOURITE_SLOTS):
        name = m.FAVOURITE_SLOTS[slot]
        point = m.favourite_slot_point(slot)
        pair = m.favourite_set_slot(slot)
        back = m.favourite_for(name)
        bad = []
        if not on_screen(point, screen):
            bad.append("OFF SCREEN")
        if back != slot:
            bad.append(f"favourite_for round-trips to {back}, not {slot}")
        note(BAD if bad else OK,
             f"slot {slot:>2} {name[:28]:30}",
             f"click={point} set_slot={pair}" + ("  " + "; ".join(bad) if bad else ""))

    head("1b. THE SET PAIRING the arbitrage depends on")
    for slot in m.managed_core_slots():
        core = m.FAVOURITE_SLOTS[slot]
        set_slot = m.favourite_set_slot(slot)
        set_name = m.FAVOURITE_SLOTS.get(set_slot, "")
        behind = m.set_behind(core)
        agree = bool(set_name) and m._floor_key(m.item_name(set_name)) == \
            m._floor_key(m.item_name(behind))
        note(OK if agree else BAD, f"{core[:28]:30}",
             f"slot {slot} -> Set slot {set_slot} {set_name!r}; "
             f"set_behind says {behind!r}"
             + ("" if agree else "   THESE DISAGREE"))

    # ---------------------------------------------------------------- 2
    head("2. PURCHASE TAB -- what a buy reads")
    note(OK, "row geometry",
         f"top={m.PURCHASE_ROW_TOP} pitch={m.PURCHASE_ROW_PITCH} "
         f"rows={m.PURCHASE_ROWS} buy_x={m.PURCHASE_BUY_X}")
    probe_box(shot, "PURCHASE_SORT_REGION", m.PURCHASE_SORT_REGION, screen)
    note(OK, "name column ends", f"x={m.PURCHASE_NAME_MAX_X}")
    note(OK, "price column", f"x={m.PURCHASE_PRICE_X}")
    for i in range(m.PURCHASE_ROWS):
        y = m.PURCHASE_ROW_TOP + i * m.PURCHASE_ROW_PITCH
        buy = (m.PURCHASE_BUY_X, y)
        note(OK if on_screen(buy, screen) else BAD,
             f"row {i + 1} Buy button", f"{buy}")
    if m.purchase_tab_open(shot):
        rows = m.read_purchase_rows(shot)
        note(OK if rows else WARN, "rows read", f"{len(rows)}")
        for r in rows:
            note(OK, f"  row {r.row}",
                 f"{r.name[:34]:36} pack={r.pack:<5} avail={r.available:<4} "
                 f"price={r.price:>13,} unit={r.unit:>10,.0f} stock={r.stock}")
        if rows:
            best = m.cheapest_listing(rows)
            note(OK, "cheapest_listing picks", f"row {best.row}")
            outlier = [r for r in rows if r.unit < 1000]
            if outlier:
                note(WARN, "implausibly cheap row(s)",
                     f"rows {[r.row for r in outlier]} -- a clipped price read "
                     f"looks exactly like a bargain; only row 1 may be bought")
    else:
        note(WARN, "purchase tab is not open", "rows not probed")

    # ---------------------------------------------------------------- 3
    head("3. CONFIRM PURCHASE DIALOG -- the last thing before Alz moves")
    for name in ("PURCHASE_DIALOG_REGION", "PURCHASE_DLG_ITEM",
                 "PURCHASE_DLG_QTY_VALUE", "PURCHASE_DLG_QTY_MAX",
                 "PURCHASE_DLG_PRICE", "PURCHASE_DIALOG_BUTTONS"):
        probe_box(shot, name, getattr(m, name), screen)
    dlg = m.purchase_confirm(shot)
    if dlg:
        note(OK, "dialog is OPEN",
             f"qty={dlg.get('qty')} / {dlg.get('qty_max')} "
             f"price={dlg.get('price')} buy={dlg.get('buy')} "
             f"cancel={dlg.get('cancel')}")
        if dlg.get("qty_max") is None:
            note(BAD, "the quantity LIMIT did not read",
                 "buy_offer falls back to taking one listing")
        if dlg.get("price") is None:
            note(BAD, "the dialog PRICE did not read",
                 "the price check is what refuses a wrong row")
        direct = m.read_number(shot, m.PURCHASE_DLG_PRICE, 40.0)
        if direct != dlg.get("price"):
            note(WARN, "price crop vs whole-dialog fallback",
                 f"crop={direct} dialog={dlg.get('price')} -- the crop should "
                 f"carry it alone; a 9-digit price once fell outside it")
    else:
        note(WARN, "no Confirm Purchase dialog on screen", "regions only")

    # ---------------------------------------------------------------- 4
    head("4. VENDOR AND THE CONVERSION GRID -- where Alt+click lands")
    probe_box(shot, "SHOP_WINDOW_TITLE", m.SHOP_WINDOW_TITLE, screen)
    probe_box(shot, "VENDOR_TAB_REGION", m.VENDOR_TAB_REGION, screen)
    note(OK, "vendor tab band", f"{m.VENDOR_TAB_BAND} margin={m.VENDOR_TAB_MARGIN}")
    note(OK, "columns (grades)", f"{m.CONVERT_COLS}")
    note(OK, "rows (set/core)", f"{m.CONVERT_ROWS}")
    note(OK, "conversion quantity", f"{m.CONVERT_QUANTITY}")
    if m.vendor_shop_open(shot):
        tab = m.active_vendor_tab(shot)
        note(OK if tab == m.CONVERT_VENDOR_TAB else BAD, "active vendor tab",
             f"{tab!r} (the grid lives on {m.CONVERT_VENDOR_TAB!r})")
    else:
        note(WARN, "vendor shop is not open", "tab not probed")

    note(OK, "", "")
    note(OK, "every grid cell, and what it would do:", "")
    for row in range(1, len(m.CONVERT_ROWS) + 1):
        for col in range(1, len(m.CONVERT_COLS) + 1):
            point = m.convert_cell_point(row, col)
            to_core = m.CONVERT_TO_CORE.get((row, col))
            to_set = (row, col) in m.CONVERT_TO_SET
            if to_core:
                what = f"SET->CORE  {to_core[0][:24]}"
                level = OK
            elif to_set:
                what = "CORE->SET  (never clicked)"
                level = OK
            else:
                what = "unmapped"
                level = WARN
            if not on_screen(point, screen):
                level, what = BAD, what + "   OFF SCREEN"
            note(level, f"  r{row}c{col} {str(point):14}", what)

    head("4b. THE CELLS A CONVERSION WOULD ACTUALLY CLICK")
    for slot in m.managed_core_slots():
        core = m.FAVOURITE_SLOTS[slot]
        cell = m.convert_cell_for(core)
        if cell is None:
            note(WARN, f"{core[:30]:32}", "no conversion cell mapped")
            continue
        point = m.convert_cell_point(*cell)
        note(OK if on_screen(point, screen) else BAD, f"{core[:30]:32}",
             f"cell r{cell[0]}c{cell[1]} at {point}")

    # ---------------------------------------------------------------- 5
    head("5. MASS PURCHASE DIALOG -- the typed quantity")
    for name in ("CONVERT_DIALOG_REGION", "CONVERT_DLG_ITEM",
                 "CONVERT_DLG_PRICE", "CONVERT_DLG_QTY_VALUE",
                 "CONVERT_DLG_QTY_MAX", "CONVERT_DIALOG_BUTTONS",
                 "CONVERT_TIP_REGION"):
        probe_box(shot, name, getattr(m, name), screen)
    buttons = m.mass_purchase_open(shot)
    if buttons:
        det = m.mass_purchase_details(shot)
        note(OK, "dialog is OPEN",
             f"item={det.get('item')!r} qty={det.get('qty')}/"
             f"{det.get('qty_max')} held={det.get('held')} "
             f"cost={det.get('cost')}")
        if det.get("qty_max") is None:
            note(BAD, "the quantity LIMIT did not read",
                 "the conversion aborts rather than guess")
    else:
        note(WARN, "no Mass Purchase dialog on screen", "regions only")

    # ---------------------------------------------------------------- 6
    head("6. INVENTORY -- the counts the pipeline is measured by")
    note(OK, "work tab", f"{m.WORK_TAB}   convert tab {m.CONVERT_INVENTORY_TAB}")
    origin = m.inventory_origin(shot)
    if origin is None:
        note(BAD, "inventory panel not found",
             "every slot coordinate below is unresolvable")
    else:
        note(OK, "origin", f"{origin}")
        tab = m.active_inventory_tab(shot, origin)
        note(OK if tab else BAD, "active tab", f"{tab}")
        filled = m.occupied_slots(shot, origin)
        note(OK, "occupied slots", f"{len(filled)} of {m.GRID_SIZE ** 2}")
        for rc in ((1, 1), (1, m.GRID_SIZE), (m.GRID_SIZE, 1),
                   (m.GRID_SIZE, m.GRID_SIZE)):
            point = m.slot_centre_at(origin, *rc)
            note(OK if on_screen(point, screen) else BAD,
                 f"  slot {rc[0]},{rc[1]} centre", f"{point}")
        if filled:
            note(OK, "  first few occupied", f"{filled[:8]}")

    # ---------------------------------------------------------------- 7
    head("7. REGISTER / RELIST -- the table, the panel, the buttons")
    for name in ("TRADE_REGION", "REGISTER_PANEL", "PRICE_ROWS", "PRICE_FIELD",
                 "QTY_FIELD", "NET_SALES_ROWS", "SHOP_SLOT_BOX",
                 "POPUP_REGION"):
        if hasattr(m, name):
            probe_box(shot, name, getattr(m, name), screen)
    for name in ("SHOP_SLOT", "QTY_INPUT", "PARK_POINT"):
        if hasattr(m, name):
            point = getattr(m, name)
            note(OK if on_screen(point, screen) else BAD, f"{name:26}", f"{point}")
    if m.register_tab_open(shot):
        rows = m.read_rows(shot)
        note(OK if rows else BAD, "table rows read", f"{len(rows or [])}")
        for r in (rows or []):
            note(OK, f"  row {r.index}",
                 f"[{(r.action or '-'):>8}] {(r.name or '(empty)')[:30]:32} "
                 f"qty={r.qty} price={r.price}")
        panel = m.read_register_panel(shot)
        note(OK, "register panel",
             f"loaded={panel.get('loaded')} qty={panel.get('qty')}/"
             f"{panel.get('qty_max')} prices={panel.get('prices')} "
             f"net={panel.get('net_sales')}")
    else:
        note(WARN, "register tab is not open", "table not probed")

    # ---------------------------------------------------------------- 8
    head("8. THE NPC -- the walk back after converting")
    probe_box(shot, "NPC_SEARCH_REGION", m.NPC_SEARCH_REGION, screen)
    where = m.find_npc(shot, retries=1)
    note(OK if where else WARN, "find_npc", f"{where}")
    if where:
        for dx, dy in getattr(m, "NPC_CLICK_OFFSETS", [])[:4]:
            point = (where[0] + dx, where[1] + dy)
            note(OK if on_screen(point, screen) else BAD,
                 f"  click offset ({dx:+},{dy:+})", f"{point}")

    # ---------------------------------------------------------------- 9
    head("9. THE MONEY RULES currently in force")
    note(OK, "restock target (hard minimum)", f"{m.RESTOCK_TARGET}")
    note(OK, "buy maximum (soft)", f"{m.BUY_MAXIMUM}")
    note(OK, "runtime BUY_TARGET", f"{m.BUY_TARGET}")
    note(OK, "orders per restock", f"{m.RESTOCK_MAX_BUYS}")
    note(OK, "convert/list rounds", f"{m.RESTOCK_MAX_ROUNDS}")
    note(OK, "shop capacity", f"{m.SHOP_ROW_CAPACITY}")
    note(OK, "rows a restock reserves", f"{m.restock_rows_needed(m.BUY_TARGET)}")
    note(OK, "relative price floor", f"{m.RELATIVE_PRICE_FLOOR}")
    note(OK, "resupply order",
         "BEFORE relisting" if m.RESTOCK_BEFORE_RELIST else "after relisting")
    note(OK, "action / type cooldown",
         f"{m.ACTION_COOLDOWN}s / {m.TYPE_COOLDOWN}s")

    head("9b. PER ITEM")
    try:
        m.validate_price_diff_floors()
        note(OK, "price-diff table validates", "")
    except Exception as exc:  # noqa: BLE001
        note(BAD, "PRICE_DIFF_FLOOR_BY_ITEM", str(exc))
    print(f"\n  {'item':30} {'buy?':>5} {'saving':>9} {'cost':>11} "
          f"{'catalogue':>12} {'binding floor':>14}")
    for slot in m.managed_core_slots():
        name = m.FAVOURITE_SLOTS[slot]
        floor, why = m.listing_floor(name)
        print(f"  {name[:30]:30} {str(m.ENABLE_BUYING.get(name, False)):>5} "
              f"{m.price_diff_floor_for(name):>9,} "
              f"{m.purchase_cost_basis(name):>11,} "
              f"{m.item_price_floor(name):>12,} {floor:>14,}  ({why})")
    print(f"\n  {'catalogue item':34} {'floor':>14}")
    for _token, cat, _f in m.ITEM_PRICE_FLOORS:
        print(f"  {cat[:34]:34} {m.item_price_floor(cat):>14,}")

    # ---------------------------------------------------------------- end
    head("SUMMARY")
    if _issues:
        print(f"{len(_issues)} region(s)/reader(s) need attention:\n")
        for issue in _issues:
            print(f"   {issue}")
        print("\nA MISS on a region that should be visible in the current game "
              "state is the one to chase: it means a coordinate has moved, and "
              "a coordinate that has moved is a click somewhere unintended.")
    else:
        print("Nothing flagged. Every region that could be checked in the "
              "current game state read as expected.")
    print("\nStates not on screen were skipped, not verified. To cover the "
          "whole flow, run this again while the Purchase tab, the Confirm "
          "Purchase dialog, the vendor grid and the Mass Purchase dialog are "
          "each up -- the probe reports which were reachable.")
    return 1 if _issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
