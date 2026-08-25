import ctypes
import re
import time

import calibration
import ledger
import open_inventory as inv

_SHARED = calibration.load_shared()
_FACTS = _SHARED["game_facts"]
_IN = _SHARED["input"]
_T = _SHARED["timing"]

INPUT_MOUSE = _IN["INPUT_MOUSE"]
MOUSEEVENTF_WHEEL = _IN["MOUSEEVENTF_WHEEL"]
WHEEL_DELTA = _IN["WHEEL_DELTA"]
DWORD_MASK = _IN["DWORD_MASK"]
ACTION_GAP = _T["action_gap"]
WHEEL_GAP = _T["wheel_gap"]

CAPACITY = _FACTS["shop_capacity"]
VISIBLE = _FACTS["shop_visible"]
WORK_TAB = _FACTS["work_tab"]
MAX_STACK = _FACTS["max_stack"]
GRID = _FACTS["grid_size"]

MAX_TOP = CAPACITY - VISIBLE + 1
HOME_NOTCHES = _SHARED["run"]["home_notches"]

EMPTY_MARKER = _SHARED["text"]["empty_row"]
_TEXT = _SHARED["text"]
CHANGE_WORD = _TEXT["change_word"]
DISMISS_WORD = _TEXT["dismiss_word"]
CONFIRM_WORD = _TEXT["confirm_word"]
RECEIPT_WORD = _TEXT["receipt_word"]
REGISTER_WORD = _TEXT["register_word"]
STATUS_COMPLETE = _TEXT["status_complete"]
BUTTON_HALF = tuple(_SHARED["detect"]["dialog_button_half"])
DIALOG_TIMEOUT = _T["dialog_timeout"]
TAB_SETTLE = _T["tab_settle"]
REFRESH_SETTLE = _T["refresh_settle"]
CLEAR_PRESSES_QTY = _SHARED["detect"]["clear_presses_qty"]
CLEAR_PRESSES_PRICE = _SHARED["detect"]["clear_presses_price"]
KEY_GAP = _T["key_gap"]
CLEAR_GAP = _T["clear_gap"]
LOAD_ATTEMPTS = _SHARED["detect"]["load_attempts"]
FIELD_SETTLE = _T["field_settle"]
SUGGESTION_RADIO_DX = _SHARED["detect"]["suggestion_radio_dx"]
PRICE_CHECK_FACTOR = _SHARED["run"]["price_check_factor"]
PANEL_REREADS = _SHARED["detect"]["panel_rereads"]
PANEL_REREAD_GAP = _T["panel_reread_gap"]
STALE_SWEEP = _T["stale_sweep"]
POLL_GAP = _T["poll_gap"]

_NOT_ALNUM = re.compile(r"[^a-z0-9]")


class Divergence(Exception):
    pass


class SlotNeverFilled(Divergence):
    pass


def _key(text):
    return _NOT_ALNUM.sub("", (text or "").lower())


def _shop():
    return calibration.load()["shop"]


def _need(name):
    value = _shop().get(name)
    if not value:
        raise Divergence(
            f"shop.{name} is not in calibration.json. Re-run "
            f"py src/calibration.py once it measures the listing table.")
    return value


def table_point():
    return tuple(_need("table_point"))


def rows_per_notch():
    return _need("rows_per_notch")


def row_one_box():
    shop = _shop()
    x0, x1 = _need("table_x")
    y = _need("row_one_y")
    half = _need("row_pitch") // 2
    return (x0, y - half, x1, y + half)


def button_point():
    return (_need("button_x"), _need("row_one_y"))


def popup_words(image=None):
    image = image if image is not None else calibration.grab()
    return calibration.ocr(image,
                           calibration._box(calibration.DIALOG_BUTTONS_F))


def _button_key(word):
    return "button_" + _key(word)


def remembered(word):
    point = _shop().get(_button_key(word))
    return tuple(point) if point else None


def button_here(word, point, image=None):
    image = image if image is not None else calibration.grab()
    dx, dy = BUTTON_HALF
    box = (point[0] - dx, point[1] - dy, point[0] + dx, point[1] + dy)
    want = _key(word)
    return any(_key(t) == want for t, _c, _p in calibration.ocr(image, box))


def search_button(word, timeout=None):
    deadline = time.monotonic() + (DIALOG_TIMEOUT if timeout is None
                                   else timeout)
    want = _key(word)
    while time.monotonic() < deadline:
        for text, _conf, point in popup_words():
            if _key(text) == want:
                return point
        time.sleep(POLL_GAP)
    return None


def find_button(word, timeout=None, verbose=False):
    known = remembered(word)
    budget = DIALOG_TIMEOUT if timeout is None else timeout
    if known is None:
        point = search_button(word, timeout=budget)
    else:
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            if button_here(word, known):
                return known
            time.sleep(POLL_GAP)
        if verbose:
            print(f"  {word} never appeared at the calibrated {known} in "
                  f"{budget:.0f}s; one sweep in case the calibration is stale")
        point = search_button(word, timeout=STALE_SWEEP)
    if point is not None and point != known:
        calibration.remember_shop(_button_key(word), list(point))
        if verbose:
            print(f"  learned {word} at {point}")
    return point


def refresh_table(model=None, verbose=False):
    point = _shop().get("refresh_point")
    if not point:
        return None
    if verbose:
        print(f"  {calibration.REFRESH_WORD} at {tuple(point)}")
    calibration.click(*point, settle=0.0)
    time.sleep(REFRESH_SETTLE)
    return tuple(point)


def show_work_tab(verbose=False, already=False):
    import open_agent_shop_premium as shop
    import open_inventory as inv_panel
    if already:
        return shop.tab_point(WORK_TAB)
    if calibration.await_inventory(verbose=verbose) is None:
        raise Divergence(
            "no readable Alz balance; the Inventory panel is not open. "
            "Nothing cancelled.")
    point = shop.tab_point(WORK_TAB)
    if verbose:
        print(f"  inventory tab {WORK_TAB} at {point}, so the cancelled item "
              f"has nowhere else to land")
    calibration.click(*point)
    return point


MIN_PLAUSIBLE_PRICE = _SHARED["detect"]["min_plausible_price"]

def _panel():
    part = _shop().get("panel")
    if not part:
        raise Divergence(
            "the register panel has not been measured; run "
            "py src/calibration.py before listing anything.")
    return part


def _in_band(spans, box):
    here = sorted((span for span in spans if box[1] <= span[2][1] <= box[3]),
                  key=lambda span: span[2][0])
    return calibration._digits(" ".join(text for text, _c, _p, _r in here))


def _asking(image, panel):
    rows = panel["suggestion_boxes"]
    wanted = [tuple(rows[-1]), tuple(panel["price_field"]),
              tuple(rows[0]) if len(rows) > 1 else None]
    live = [box for box in wanted if box]
    band = (min(b[0] for b in live), min(b[1] for b in live),
            max(b[2] for b in live), max(b[3] for b in live))
    spans = calibration.ocr_spans(image, band)
    out = []
    for box in wanted:
        if box is None:
            out.append(None)
            continue
        value = _in_band(spans, box)
        if value is None or value < MIN_PLAUSIBLE_PRICE:
            value = calibration.read_money(image, box)
        out.append(value)
    return tuple(out)


def _near(a, b):
    return a and b and (a / PRICE_CHECK_FACTOR <= b <= a * PRICE_CHECK_FACTOR)


def _agreed(asked, filled, average, listed_at, verbose):
    say = print if verbose else (lambda *a: None)
    seen = [(v, w, exact) for v, w, exact in
            ((asked, "the row", True), (filled, "the price field", True),
             (average, "the week's average", False),
             (listed_at, "what it was listed at", False))
            if v and v >= MIN_PLAUSIBLE_PRICE]
    if not seen:
        return None
    camps = {frozenset(j for j, other in enumerate(seen)
                       if _near(pick[0], other[0]))
             for pick in seen}
    agreeing = max(len(camp) for camp in camps)
    biggest = [camp for camp in camps if len(camp) == agreeing]
    if len(biggest) > 1:
        say(f"    the witnesses split {' and '.join(
            ', '.join(f'{seen[j][1]} says {seen[j][0]:,}' for j in sorted(camp))
            for camp in biggest)}; taking none of them")
        return None
    with_it = [seen[j] for j in sorted(biggest[0])]
    carries = [o for o in with_it if o[2]]
    if not carries:
        say(f"    {agreeing} of {len(seen)} witnesses put it near "
            f"{with_it[0][0]:,}, and none of them carries the asking price")
        return None
    value, where, _ = carries[0]
    odd = [o for o in seen if o not in with_it]
    if odd:
        say(f"    {', '.join(f'{o[1]} says {o[0]:,}' for o in odd)}, against "
            f"{value:,} from {agreeing} of the three; taking {value:,}")
    else:
        say(f"    the lowest listed price is {value:,}, from {where}"
            + (f" and {agreeing - 1} more" if agreeing > 1 else ""))
    return value


def panel_standing():
    asked, filled, _average = _asking(calibration.grab(), _panel())
    for value in (filled, asked):
        if value and value >= MIN_PLAUSIBLE_PRICE:
            return value
    return None


def suggested_price(verbose=False, listed_at=None):
    panel = _panel()
    box = tuple(panel["suggestion_boxes"][-1])
    radio = (box[0] - SUGGESTION_RADIO_DX, (box[1] + box[3]) // 2)
    value = _agreed(*_asking(calibration.grab(), panel), listed_at, verbose)
    calibration.click(*radio, settle=FIELD_SETTLE)
    if value is None:
        value = _agreed(*_asking(calibration.grab(), panel), listed_at,
                        verbose)
    if value is None and verbose:
        print(f"    the lowest listed price would not read")
    return value


def read_panel_net():
    box = _panel().get("net_sales_box")
    if not box:
        return None
    return calibration.read_money(calibration.grab(), tuple(box)) or 0


def panel_quantity(want_price, verbose=False):
    if not _panel().get("net_sales_box"):
        raise Divergence(
            "the net sales box was never measured, so a price cannot be "
            "checked. Recalibrate before listing anything.")
    box = tuple(_panel()["net_sales_box"])
    for attempt in range(1, PANEL_REREADS + 2):
        seen = calibration.read_money_all(calibration.grab(), box)
        for net in seen:
            if net and net % want_price == 0:
                qty = net // want_price
                if verbose:
                    print(f"    net sales {net:,} is {qty} x {want_price:,}")
                return qty
        if verbose:
            print(f"    read {attempt}: the net sales read "
                  f"{', '.join(f'{v:,}' for v in seen) or 'nothing'}, and no "
                  f"reading is a whole number of {want_price:,}")
        time.sleep(PANEL_REREAD_GAP)
    return None


def type_number(value, clear):
    from open_inventory import press
    keys = _SHARED["input"]
    for _ in range(clear):
        press(keys["VK_BACK"])
        time.sleep(CLEAR_GAP)
    for ch in str(int(value)):
        press(keys[f"VK_{ch}"])
        time.sleep(KEY_GAP)


def row_button_box():
    shop = _shop()
    half_x = BUTTON_HALF[0]
    half_y = max(1, int(shop["row_pitch"]) // 2)
    x, y = int(shop["button_x"]), int(shop["row_one_y"])
    return (x - half_x, y - half_y, x + half_x, y + half_y)


def row_button(image=None):
    image = image if image is not None else calibration.grab()
    for text, _conf, _point in calibration.ocr(image, row_button_box()):
        key = _key(text)
        for word in (RECEIPT_WORD, CHANGE_WORD, REGISTER_WORD):
            if _key(word) == key:
                return word
    return None


def row_button_text(image=None):
    image = image if image is not None else calibration.grab()
    return " ".join(t for t, _c, _p in calibration.ocr(image, row_button_box()))


def row_function(text=None):
    seen = row_button()
    if seen is not None:
        return seen
    text = read_row_one() if text is None else text
    key = _key(text)
    for word in (RECEIPT_WORD, CHANGE_WORD, REGISTER_WORD):
        if _key(word) in key:
            return word
    return None


def row_complete(text=None):
    text = read_row_one() if text is None else text
    return _key(STATUS_COMPLETE) in _key(text)


def dialog_buttons(image=None):
    image = image if image is not None else calibration.grab()
    seen = []
    for word in (DISMISS_WORD, CONFIRM_WORD, RECEIPT_WORD):
        known = remembered(word)
        if known is not None:
            if button_here(word, known, image):
                seen.append(word)
            continue
        want = _key(word)
        if any(_key(t) == want
               for t, _c, _p in calibration.ocr(
                   image, calibration._box(calibration.DIALOG_BUTTONS_F))):
            seen.append(word)
    return seen


def dialog_gone(timeout=None):
    deadline = time.monotonic() + (DIALOG_TIMEOUT if timeout is None
                                   else timeout)
    while time.monotonic() < deadline:
        if not dialog_buttons():
            return True
        time.sleep(POLL_GAP)
    return False


def trim_borders(text):
    parts = (text or "").strip().split()
    while parts and len(parts[0]) == 1:
        parts.pop(0)
    while parts and len(parts[-1]) == 1:
        parts.pop()
    return " ".join(parts)


def read_row_one():
    text = ""
    for attempt in range(PANEL_REREADS + 1):
        text = trim_borders(
            calibration.read_line(calibration.grab(), row_one_box()))
        if text.strip():
            return text
        if attempt < PANEL_REREADS:
            time.sleep(PANEL_REREAD_GAP)
    return text


def row_one_is_empty(text=None):
    text = read_row_one() if text is None else text
    key = _key(text)
    return (not key) or EMPTY_MARKER in key


def _wheel_event(direction):
    return inv._Input(
        type=INPUT_MOUSE,
        u=inv._InputUnion(mi=inv._MouseInput(
            0, 0, ctypes.c_ulong(direction * WHEEL_DELTA & DWORD_MASK).value,
            MOUSEEVENTF_WHEEL, 0, None)))


def wheel(rows, verbose=True):
    if not rows:
        return 0
    x, y = table_point()
    notches = int(round(abs(rows) / rows_per_notch()))
    if not notches:
        return 0
    direction = -1 if rows > 0 else 1
    inv._user32.SetCursorPos(int(x), int(y))
    event = _wheel_event(direction)
    for _ in range(notches):
        sent = inv._user32.SendInput(1, ctypes.byref(event),
                                     ctypes.sizeof(inv._Input))
        if sent != 1:
            raise Divergence(
                f"SendInput sent {sent} of 1 wheel event "
                f"(GetLastError {ctypes.get_last_error()})")
        time.sleep(WHEEL_GAP)
    calibration.park()
    if verbose:
        print(f"  wheel {notches} event(s) {'down' if rows > 0 else 'up'} "
              f"at ({x}, {y}) for {rows:+d} row(s)")
    return notches


_PACK = re.compile(_SHARED["text"]["pack_marker"], re.IGNORECASE)


def pack_size(name):
    found = _PACK.search((name or "").strip())
    return int(found.group(1)) if found else 1


class Row:
    __slots__ = ("name", "qty", "price", "buy_cost", "units")

    def __init__(self, name, qty=1, price=0, buy_cost=0, units=None):
        self.name = name
        self.qty = int(qty)
        self.price = int(price)
        self.buy_cost = int(buy_cost)
        self.units = int(units) if units is not None \
            else int(qty) * pack_size(name)

    @property
    def pack(self):
        return pack_size(self.name)

    @property
    def sell_total(self):
        return self.price * self.qty

    @property
    def sell_unit(self):
        return self.sell_total // max(1, self.units)

    @property
    def cost_total(self):
        return self.buy_cost * self.units

    @property
    def margin(self):
        return self.sell_total - self.cost_total

    @property
    def margin_unit(self):
        return self.sell_unit - self.buy_cost

    @property
    def key(self):
        return _key(self.name)

    def copy(self):
        return Row(self.name, self.qty, self.price, self.buy_cost, self.units)

    def __repr__(self):
        return (f"Row({self.name!r}, qty={self.qty}, units={self.units}, "
                f"price={self.price:,}, buy={self.buy_cost:,}, "
                f"sell_unit={self.sell_unit:,}, margin={self.margin:+,})")


class RowModel:
    def __init__(self, enforce=False):
        self._slots = {}
        self._work = {}
        self._top = None
        self.ready = False
        self.enforce = enforce
        self.divergences = 0

    def seed(self, rows, top=None):
        self._slots = {}
        for index, row in (rows or {}).items():
            index = int(index)
            if not 1 <= index <= CAPACITY:
                raise ValueError(f"row {index} is outside 1..{CAPACITY}")
            if row is not None:
                self._slots[index] = row
        self._top = None if top is None else int(top)
        self.ready = True
        return self

    def seed_work_tab(self, slots):
        self._work = {tuple(k): v for k, v in (slots or {}).items()}
        return self

    def get(self, index):
        return self._slots.get(int(index))

    def occupied(self):
        return sorted(self._slots)

    def empty(self):
        return [i for i in range(1, CAPACITY + 1) if i not in self._slots]

    def used(self):
        return len(self._slots)

    def next_slot(self):
        free = self.empty()
        return free[0] if free else None

    def holes(self):
        highest = max(self._slots) if self._slots else 0
        return [i for i in range(1, highest + 1) if i not in self._slots]

    def register(self, row):
        index = self.next_slot()
        if index is None:
            raise ValueError(f"the shop is full: all {CAPACITY} slots in use")
        self._slots[index] = row
        return index

    def next_work_slot(self):
        for row in range(1, GRID + 1):
            for col in range(1, GRID + 1):
                if (row, col) not in self._work:
                    return (row, col)
        return None

    def note_cancel(self, index):
        index = int(index)
        row = self._slots.get(index)
        if row is None:
            raise ValueError(f"row {index} is already empty; nothing to cancel")
        del self._slots[index]
        landing = self.next_work_slot()
        if landing is not None:
            self._work[landing] = row.copy()
        return {
            "row": index,
            "item": row,
            "shop_slot_now": None,
            "renumbered": [],
            "lands_in_tab": WORK_TAB,
            "lands_in_slot": landing,
        }

    def receive(self, index, verbose=True):
        import get_alz
        listed = self._slots.get(index)
        before_alz = get_alz.read_balance()
        point = button_point()
        if verbose:
            print(f"  {RECEIPT_WORD} at {point}")
        calibration.click(*point)
        calibration.park()
        accept = find_button(RECEIPT_WORD)
        if accept is None:
            raise Divergence(
                f"no Confirm Receipt dialog appeared after {RECEIPT_WORD} on "
                f"row {index}. Nothing has been collected.")
        if verbose:
            print(f"  Confirm Receipt: accepting at {accept}")
        calibration.click(*accept)
        calibration.park()
        if not dialog_gone():
            raise Divergence(
                f"the Confirm Receipt dialog stayed open on row {index}. "
                f"Whether the Alz was taken is unknown -- check by hand.")
        self._book(index, listed, before_alz, verbose)
        return True

    def _book(self, index, listed, before_alz, verbose=True):
        import get_alz
        if listed is None or before_alz is None:
            return
        after_alz = get_alz.read_balance()
        if after_alz is None or after_alz <= before_alz:
            return
        proceeds = after_alz - before_alz
        price = int(listed.price or 0)
        sold = proceeds // price if price else 0
        if not sold:
            return
        each = calibration.market_unit(listed.name)
        held = round(price / each) if each else 1
        if held < 1 or not (each and abs(price - each * held)
                            <= each * (PRICE_CHECK_FACTOR - 1)):
            held = 1
        ledger.sold(listed.name, price // held, proceeds, sold * held)
        if verbose:
            print(f"  collected {proceeds:,} Alz for {sold} x "
                  f"{listed.name!r} at {price:,}"
                  + (f", {held} to a listing" if held > 1 else ""))

    def cancel(self, index, verbose=True, tab_ready=False):
        index = int(index)
        expected = self._slots.get(index)
        if expected is None:
            raise ValueError(f"row {index} is empty in the model; refusing to "
                             f"cancel a slot nothing is listed in")
        if self.scroll_to(index, verbose=verbose):
            time.sleep(TAB_SETTLE)

        seen = read_row_one()
        action = row_function(seen)
        if action == RECEIPT_WORD:
            complete = row_complete(seen)
            if verbose:
                print(f"  row {index} has SOLD "
                      f"({'fully' if complete else 'partially'}); collecting "
                      f"before anything else")
            self.receive(index, verbose=verbose)
            time.sleep(TAB_SETTLE)
            seen = read_row_one()
            if complete or row_function(seen) == REGISTER_WORD:
                if verbose:
                    print(f"  row {index} is empty after the collection; "
                          f"nothing left to cancel")
                return self.note_cancel(index)
            action = row_function(seen)
        if action == REGISTER_WORD:
            raise Divergence(
                f"row {index} is empty on screen; nothing to cancel.")
        if not expected.key or expected.key not in _key(seen):
            raise Divergence(
                f"row {index} should hold {expected.name!r} but position 1 "
                f"reads {seen!r}. Not cancelling a row that is not the one "
                f"the model names.")
        if verbose:
            print(f"  row {index} at position 1: {seen!r}")

        show_work_tab(verbose=verbose, already=tab_ready)

        point = button_point()
        if verbose:
            print(f"  {CHANGE_WORD} at {point}")
        inv._user32.SetCursorPos(*point)
        time.sleep(ACTION_GAP)
        calibration.click(*point, settle=0.0)

        dismiss = find_button(DISMISS_WORD)
        if dismiss is None:
            raise Divergence(
                f"no {DISMISS_WORD} button appeared after clicking "
                f"{CHANGE_WORD} on row {index}. Nothing has been cancelled.")
        if verbose:
            print(f"  {DISMISS_WORD} at {dismiss}")
        calibration.click(*dismiss, settle=0.0)

        confirm = find_button(CONFIRM_WORD)
        if confirm is None:
            raise Divergence(
                f"no {CONFIRM_WORD} button appeared after {DISMISS_WORD} on "
                f"row {index}. The dialog is still open; nothing committed.")
        if verbose:
            print(f"  {CONFIRM_WORD} at {confirm}")
        calibration.click(*confirm, settle=0.0)
        calibration.park()

        if not dialog_gone():
            raise Divergence(
                f"the dialog stayed open after {CONFIRM_WORD} on row {index}. "
                f"Whether the cancel committed is unknown -- check by hand.")
        result = self.note_cancel(index)
        if verbose:
            print(f"  row {index} cancelled; {expected.name!r} lands in tab "
                  f"{result['lands_in_tab']} slot {result['lands_in_slot']}")
        return result

    def list_slot(self, row, col, price=None, floor=0, why="", verbose=True,
                  lands_in=None, expect_item=None,
                  expect_price=None, unit_market=None, floor_each=0,
                  listed_at=None):
        import open_agent_shop_premium as shop
        panel = _shop().get("panel")
        if not panel:
            raise Divergence(
                "the register panel has not been measured; run "
                "py src/calibration.py before listing anything.")

        calibration.steps_reset()
        point = shop.slot_point(int(row), int(col))
        if verbose:
            print(f"  inventory slot ({row},{col}) at {point}")
        with calibration.step("read the panel before loading"):
            standing = panel_standing()
        if standing is not None:
            raise Divergence(
                f"the shop slot already holds something the panel prices at "
                f"{standing:,}; clear it before listing another item.")

        deadline = time.monotonic() + DIALOG_TIMEOUT
        while time.monotonic() < deadline:
            with calibration.step(f"wait for slot ({row},{col}) to fill"):
                filled = not calibration.slot_is_empty(calibration.grab(),
                                                       int(row), int(col))
            if filled:
                break
            time.sleep(POLL_GAP)
        else:
            calibration.snap(f"slot_{row}x{col}_never_filled")
            raise SlotNeverFilled(
                f"tab {WORK_TAB} slot ({row},{col}) is still empty "
                f"{DIALOG_TIMEOUT:g}s after the withdrawal. Nothing listed.")

        suggested = None
        for attempt in range(1, LOAD_ATTEMPTS + 1):
            with calibration.step(f"ctrl-click ({row},{col}) attempt {attempt}"):
                calibration.ctrl_click(*point)
            with calibration.step("read the suggested price"):
                suggested = suggested_price(verbose, listed_at)
            if suggested is not None:
                break
            calibration.snap(f"nothing_loaded_{row}x{col}_{attempt}")
            if verbose:
                print(f"  ctrl-click {attempt}/{LOAD_ATTEMPTS} loaded nothing "
                      f"from ({row},{col})")
        if suggested is None:
            raise Divergence(
                f"nothing loaded into the shop slot from ({row},{col}) after "
                f"{LOAD_ATTEMPTS} ctrl-click(s). Nothing has been listed.")
        if unit_market:
            count = round(suggested / unit_market)
            if count < 1:
                raise Divergence(
                    f"the panel prices what loaded from ({row},{col}) at "
                    f"{suggested:,}, under the {unit_market:,} a single one "
                    f"goes for. Nothing has been listed.")
            whole = unit_market * count
            if not (whole / PRICE_CHECK_FACTOR <= suggested
                    <= whole * PRICE_CHECK_FACTOR):
                calibration.snap(f"panel_prices_{suggested}")
                raise Divergence(
                    f"the panel prices what loaded from ({row},{col}) at "
                    f"{suggested:,}, which is not a whole number of "
                    f"{unit_market:,} -- {count} would be {whole:,}. Nothing "
                    f"has been listed.")
            if floor_each:
                floor = floor_each * count
            if verbose:
                print(f"  the panel prices the bundle at {suggested:,}, "
                      f"{count} x {unit_market:,}"
                      + (f"; the floor is {floor:,}" if floor else ""))
        if expect_item or expect_price:
            expected = expect_price or (calibration.market_unit(expect_item)
                                        * max(1, pack_size(expect_item)))
            named = expect_item or "what the board asks"
            if expected:
                if not (expected / PRICE_CHECK_FACTOR <= suggested
                        <= expected * PRICE_CHECK_FACTOR):
                    calibration.snap(f"panel_prices_{suggested}")
                    raise Divergence(
                        f"the panel prices what loaded from ({row},{col}) at "
                        f"{suggested:,}, and a {named} goes for about "
                        f"{expected:,}. That is not the same item. Nothing "
                        f"has been listed.")
                if verbose:
                    print(f"  the panel prices it at {suggested:,}, and a "
                          f"{named} goes for about {expected:,}")

        want = price if price is not None else calibration.undercut(suggested)
        if want is None:
            raise Divergence(
                "no price was given and the panel suggests none, so there is "
                "nothing to list at. Nothing has been listed.")
        if floor and want < floor:
            if verbose:
                print(f"    market {want:,} is under the {floor:,} floor"
                      + (f" ({why})" if why else "") + f"; listing at the floor")
            want = floor
        if want < MIN_PLAUSIBLE_PRICE:
            raise Divergence(
                f"refusing to list at {want:,}, under the "
                f"{MIN_PLAUSIBLE_PRICE:,} plausibility floor.")

        with calibration.step(f"type the price {want:,}"):
            calibration.click(*panel["price_point"], settle=FIELD_SETTLE)
            type_number(want, CLEAR_PRESSES_PRICE)
        with calibration.step(f"type the quantity {MAX_STACK}"):
            calibration.click(*panel["qty_point"], settle=FIELD_SETTLE)
            type_number(MAX_STACK, CLEAR_PRESSES_QTY)
            calibration.park()
        with calibration.step("take the quantity from the net sales"):
            qty = panel_quantity(want, verbose)
        if qty is None:
            calibration.snap("panel_will_not_confirm")
            raise Divergence(
                f"the panel will not price {want:,} against its net sales "
                f"after typing {MAX_STACK}. Nothing has been listed.")
        if verbose:
            print(f"  typed {MAX_STACK}; the net sales make it {qty}")
        with calibration.step("click Register"):
            calibration.click(*panel["register_button"], settle=0.0)
        with calibration.step(f"find {CONFIRM_WORD}"):
            confirm = find_button(CONFIRM_WORD)
        if confirm is None:
            raise Divergence(
                f"no {CONFIRM_WORD} appeared after Register. Nothing "
                f"committed.")
        with calibration.step(f"click {CONFIRM_WORD}"):
            calibration.click(*confirm, settle=0.0)
        with calibration.step("park"):
            calibration.park()
        with calibration.step("confirm the dialog is gone"):
            gone = dialog_gone()
        if not gone:
            raise Divergence(
                f"the dialog stayed open after {CONFIRM_WORD}. Whether the "
                f"listing committed is unknown -- check the shop by hand.")
        calibration.steps_table(f"list {qty} at {want:,}")
        if verbose:
            print(f"  listed {qty} at {want:,}"
                  + (f"; it lands in row {int(lands_in)}"
                     if lands_in is not None else ""))
        return {"slot": (int(row), int(col)), "qty": qty,
                "price": want, "row": lands_in}

    def collect(self, index, remaining=0):
        index = int(index)
        row = self._slots.get(index)
        if row is None:
            raise ValueError(f"row {index} is empty; nothing to collect")
        remaining = int(remaining)
        if remaining <= 0:
            del self._slots[index]
            left = None
        else:
            row.qty = remaining
            left = row
        return {
            "row": index,
            "remaining": left,
            "shop_slot_now": left,
            "renumbered": [],
        }

    @property
    def top(self):
        return self._top

    def visible(self, top=None):
        top = self._top if top is None else int(top)
        if top is None:
            return []
        return [i for i in range(top, min(top + VISIBLE, CAPACITY + 1))]

    def can_top(self, index):
        return 1 <= int(index) <= MAX_TOP

    def scroll_plan(self, index):
        index = int(index)
        if not 1 <= index <= CAPACITY:
            raise ValueError(f"row {index} is outside 1..{CAPACITY}")
        if self._top is None:
            raise Divergence(
                "the top visible row is unknown, so a scroll cannot be "
                "counted. Seed the model from a read first.")
        want = min(index, MAX_TOP)
        return {
            "from_top": self._top,
            "to_top": want,
            "notches": want - self._top,
            "clamped": want != index,
            "reachable_at_row": index - want + 1,
        }

    def note_scrolled(self, to_top):
        self._top = int(to_top)
        return self._top

    def home(self, verbose=True):
        wheel(-HOME_NOTCHES, verbose=False)
        time.sleep(ACTION_GAP)
        self._top = 1
        if verbose:
            print(f"  scrolled to the top; row 1 is at position 1")
        return 1

    def scroll_to(self, index, verbose=True):
        if self._top is None:
            self.home(verbose=verbose)
        plan = self.scroll_plan(index)
        if plan["clamped"]:
            raise Divergence(
                f"row {index} cannot reach position 1: {CAPACITY} rows, "
                f"{VISIBLE} visible, top reaches {MAX_TOP}; it sits at "
                f"{plan['reachable_at_row']}.")
        if plan["notches"]:
            wheel(plan["notches"], verbose=verbose)
        self.note_scrolled(plan["to_top"])
        if verbose:
            print(f"  row {index} is now at position 1")
        return plan

    def at(self, index, verbose=False):
        self.scroll_to(index, verbose=verbose)
        return read_row_one()

    def verify(self, text=None, index=None):
        if self._top is None:
            raise Divergence("the top visible row is unknown; nothing to verify")
        index = self._top if index is None else int(index)
        if index != self._top:
            raise Divergence(
                f"row {index} is not at position 1 (row {self._top} is). "
                f"Scroll to it first.")
        text = read_row_one() if text is None else text
        mine = self._slots.get(index)
        read_empty = row_one_is_empty(text)
        if mine is None:
            agrees = read_empty
        else:
            agrees = (not read_empty) and mine.key in _key(text)
        if not agrees:
            self.divergences += 1
            if self.enforce:
                raise Divergence(
                    f"row {index}: the model holds {mine!r} but position 1 "
                    f"reads {text!r}")
        return {"row": index, "agrees": agrees, "model": mine, "read": text}

    def totals(self):
        rows = list(self._slots.values())
        return {
            "rows": len(rows),
            "items": sum(r.qty for r in rows),
            "units": sum(r.units for r in rows),
            "listed": sum(r.sell_total for r in rows),
            "cost": sum(r.cost_total for r in rows),
            "margin": sum(r.margin for r in rows),
        }

    def report(self):
        out = [f"  ROW MODEL -- {self.used()} of {CAPACITY} slot(s) in use, "
               f"next listing lands at row {self.next_slot()}"]
        if self._top is not None:
            out.append(f"  row {self._top} is at position 1")
        for index in self.occupied():
            row = self._slots[index]
            out.append(f"    {index:2}  {row.name} x{row.qty} "
                       f"({row.units} unit) sell {row.sell_total:,} "
                       f"@ {row.sell_unit:,}/u  cost {row.cost_total:,} "
                       f"@ {row.buy_cost:,}/u  margin {row.margin:+,} "
                       f"({row.margin_unit:+,}/u)")
        gaps = self.holes()
        if gaps:
            out.append(f"  holes at {gaps} - these persist, nothing renumbers")
        t = self.totals()
        out.append(f"  {t['units']:,} unit(s), listed {t['listed']:,}, "
                   f"cost {t['cost']:,}, margin {t['margin']:+,}")
        return "\n".join(out)
