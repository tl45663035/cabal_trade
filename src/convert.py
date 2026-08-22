import re
import time

import calibration
import row_model

_SHARED = calibration.load_shared()
_TEXT = _SHARED["text"]
CONFIRM_WORD = _TEXT["convert_confirm_word"]
CANCEL_WORD = _TEXT["convert_cancel_word"]
TAB_SETTLE = _SHARED["timing"]["tab_settle"]
ACTION_GAP = _SHARED["timing"]["action_gap"]
POLL_GAP = _SHARED["timing"]["poll_gap"]
DIALOG_TIMEOUT = _SHARED["timing"]["dialog_timeout"]
CLEAR_PRESSES_QTY = _SHARED["detect"]["clear_presses_qty"]
FIELD_SETTLE = _SHARED["timing"]["field_settle"]
INVENTORY_TAB = calibration.CONVERT_INVENTORY_TAB
MAX_STACK = _SHARED["game_facts"]["max_stack"]


class Refused(Exception):
    pass


def _convert_cal():
    block = calibration.load().get("convert")
    if not block:
        raise Refused(
            "the conversion grid has not been measured.")
    return block


def cell_for(core_name):
    pairs = _convert_cal()["set_to_core"]
    want = re.sub(r"[^a-z0-9]", "", (core_name or "").lower())
    for core, entry in pairs.items():
        if re.sub(r"[^a-z0-9]", "", core.lower()) == want:
            return entry
    return None


def open_vendor(verbose=True):
    if calibration._trade_window_open():
        raise Refused(
            "the Agent Shop is open and the vendor will not open on top of "
            "it. Nothing pressed.")
    if not calibration.await_vendor(verbose=verbose):
        raise Refused("the vendor Shop did not open on N.")
    tab = _convert_cal()
    showing = calibration.vendor_tab_point(tab["tab"])
    calibration.click(*(showing or tab["tab_point"]))
    time.sleep(TAB_SETTLE)
    calibration.park()
    if not calibration.vendor_open():
        raise Refused("the vendor Shop closed while selecting its tab.")
    return True


def buttons(image=None):
    image = image if image is not None else calibration.grab()
    box = calibration._box(
        tuple(calibration._REG["convert_dialog_buttons"]))
    found = {}
    for text, _c, point in calibration.ocr(image, box):
        key = re.sub(r"[^a-z]", "", text.lower())
        if key:
            found.setdefault(key, point)
    return found


def dialog_open(image=None):
    return re.sub(r"[^a-z]", "", CONFIRM_WORD.lower()) in buttons(image)


def dialog_button(word, image=None):
    return buttons(image).get(re.sub(r"[^a-z]", "", word.lower()))


def await_dialog(timeout=None):
    deadline = time.monotonic() + (DIALOG_TIMEOUT if timeout is None
                                   else timeout)
    while time.monotonic() < deadline:
        if dialog_open():
            return True
        time.sleep(POLL_GAP)
    return False


def _read_qty_field(image):
    reg = calibration._REG
    left = calibration._box(tuple(reg["convert_dialog_qty"]))
    right = calibration._box(tuple(reg["convert_dialog_qty_max"]))
    box = (left[0], min(left[1], right[1]), right[2], max(left[3], right[3]))
    text = calibration.read_line(image, box, border=calibration.OCR_BORDER)
    found = [int(n) for n in re.findall(r"\d+", text)]
    if len(found) < 2:
        return None, None
    return found[0], found[-1]


def dialog_details(image=None):
    image = image if image is not None else calibration.grab()
    reg = calibration._REG
    read = lambda key: calibration.read_line(
        image, calibration._box(tuple(reg[key])))
    held, total = _read_qty_field(image)
    return {"item": read("convert_dialog_item"),
            "price": read("convert_dialog_price"),
            "qty": held,
            "qty_max": total}


def _await_arrivals(before, offered, timeout=None):
    deadline = time.monotonic() + (DIALOG_TIMEOUT if timeout is None
                                   else timeout)
    arrived = set()
    while time.monotonic() < deadline:
        arrived = calibration.occupied_slots() - before
        if len(arrived) >= offered:
            break
        time.sleep(POLL_GAP)
    return arrived


def _cancel(why):
    point = dialog_button(CANCEL_WORD)
    if point is not None:
        calibration.click(*point)
        time.sleep(ACTION_GAP)
    if dialog_open():
        from open_inventory import press
        press(calibration.load_shared()["input"]["VK_ESCAPE"])
        time.sleep(ACTION_GAP)
    calibration.park()
    raise Refused(why)


def convert(core_name, verbose=True):
    say = print if verbose else (lambda *a: None)
    entry = cell_for(core_name)
    if entry is None:
        raise Refused(
            f"{core_name!r} is not a Set-to-Core conversion this grid offers. "
            f"It offers {sorted(_convert_cal()['set_to_core'])}.")
    if not calibration.vendor_open():
        raise Refused("the vendor Shop is not open. Nothing clicked.")

    calibration.steps_reset()
    x, y = entry["point"]
    say(f"  {entry['cell']} at ({x}, {y}): {entry['costs']} -> {core_name}")
    with calibration.step(f"alt-click the cell at ({x}, {y})"):
        calibration.alt_click(x, y, settle=0.0)
    with calibration.step("await the Purchase Item dialog"):
        appeared = await_dialog()
    if not appeared:
        raise Refused(
            "no Purchase Item dialog appeared after Alt+click; nothing "
            "confirmed.")

    with calibration.step("read the dialog"):
        detail = dialog_details()
    say(f"    dialog: item {detail['item']!r}  qty {detail['qty']} of "
        f"{detail['qty_max']}  price {detail['price']!r}")
    want = re.sub(r"[^a-z0-9]", "", core_name.lower())
    seen = re.sub(r"[^a-z0-9]", "", (detail["item"] or "").lower())
    if want not in seen:
        _cancel(f"the dialog offers {detail['item']!r}, not {core_name!r}. "
                f"Cancelled without converting.")
    if not detail["qty_max"]:
        _cancel(f"the dialog offers a maximum of {detail['qty_max']} "
                f"{entry['costs']} to convert. Cancelled.")

    offered = int(detail["qty_max"])
    with calibration.step(f"type the quantity {MAX_STACK}"):
        calibration.click(*calibration._centre(
            tuple(calibration._REG["convert_dialog_qty"])),
            settle=FIELD_SETTLE)
        row_model.type_number(MAX_STACK, CLEAR_PRESSES_QTY)
    say(f"    typed {MAX_STACK}; the dialog clamps it to what is held")

    point = dialog_button(CONFIRM_WORD)
    if point is None:
        _cancel(f"no {CONFIRM_WORD} button on the dialog. Cancelled.")
    with calibration.step(f"select inventory tab {INVENTORY_TAB}"):
        calibration.click(*calibration.inventory_tab_point(INVENTORY_TAB),
                          settle=0.0)
        time.sleep(TAB_SETTLE)
    with calibration.step(f"read tab {INVENTORY_TAB} before converting"):
        before = calibration.occupied_slots()
    say(f"    inventory tab {INVENTORY_TAB} holds {len(before)} slot(s) and "
        f"is showing, so the {core_name} lands there")
    if not dialog_open():
        _cancel(f"the dialog closed while selecting inventory tab "
                f"{INVENTORY_TAB}. Nothing converted.")
    point = dialog_button(CONFIRM_WORD) or point
    with calibration.step(f"click {CONFIRM_WORD}"):
        calibration.click(*point, settle=0.0)
    with calibration.step("park"):
        calibration.park()
    with calibration.step(f"read tab {INVENTORY_TAB} after converting"):
        arrived = _await_arrivals(before, offered)
    if not arrived:
        raise Refused(
            f"no slot on tab {INVENTORY_TAB} filled after {CONFIRM_WORD}. "
            f"Nothing converted.")
    calibration.steps_table(f"convert {offered} into {core_name}")
    where = sorted(arrived)
    say(f"    converted {len(where)} {entry['costs']} into {core_name}, "
        f"landing in {where[0]} to {where[-1]}")
    return {"core": core_name, "costs": entry["costs"],
            "converted": len(where), "slots": where,
            "offered": offered,
            "cell": entry["cell"]}
