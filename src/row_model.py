import ctypes
import re
import time

import calibration
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
GRID = _FACTS["grid_size"]

MAX_TOP = CAPACITY - VISIBLE + 1
HOME_NOTCHES = MAX_TOP

EMPTY_MARKER = _SHARED["text"]["empty_row"]
_TEXT = _SHARED["text"]
CHANGE_WORD = _TEXT["change_word"]
DISMISS_WORD = _TEXT["dismiss_word"]
CONFIRM_WORD = _TEXT["confirm_word"]
RECEIPT_WORD = _TEXT["receipt_word"]
REGISTER_WORD = _TEXT["register_word"]
STATUS_COMPLETE = _TEXT["status_complete"]
DIALOG_BUTTON_MIN_X = _SHARED["detect"]["dialog_button_min_x"]
BUTTON_HALF = tuple(_SHARED["detect"]["dialog_button_half"])
DIALOG_TIMEOUT = _T["dialog_timeout"]
TAB_SETTLE = _T["tab_settle"]
TYPE_CLEAR_PRESSES = _SHARED["detect"]["type_clear_presses"]
KEY_GAP = _T["key_gap"]
PANEL_REREADS = _SHARED["detect"]["panel_rereads"]
STALE_SWEEP = _T.get("stale_sweep", 1.0)
POLL_GAP = _T.get("poll_gap", 0.0)

_NOT_ALNUM = re.compile(r"[^a-z0-9]")


class Divergence(Exception):
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
            if _key(text) == want and point[0] >= DIALOG_BUTTON_MIN_X:
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


def show_work_tab(verbose=False):
    import open_agent_shop_premium as shop
    import open_inventory as inv_panel
    if calibration.find_alz(calibration.grab()) is None:
        if verbose:
            print("  the Inventory panel is shut; opening it so the cancelled "
                  "item has a tab to land on")
        inv_panel.open_inventory()
        deadline = time.monotonic() + DIALOG_TIMEOUT
        while calibration.find_alz(calibration.grab()) is None:
            if time.monotonic() >= deadline:
                raise Divergence(
                    "the Inventory panel did not open, so there is no way to "
                    "say which tab a cancelled item would return to. Nothing "
                    "cancelled.")
            time.sleep(POLL_GAP)
    point = shop.tab_point(WORK_TAB)
    if verbose:
        print(f"  inventory tab {WORK_TAB} at {point}, so the cancelled item "
              f"has nowhere else to land")
    calibration.click(*point)
    return point


MIN_PLAUSIBLE_PRICE = _SHARED["detect"]["min_plausible_price"]

_QTY = re.compile(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)")


def _panel():
    part = _shop().get("panel")
    if not part:
        raise Divergence(
            "the register panel has not been measured; run "
            "py src/calibration.py before listing anything.")
    return part


def read_panel_qty():
    box = _panel()["qty_box"]
    text = calibration.read_line(calibration.grab(), tuple(box))
    nums = [int(re.sub(r"[^\d]", "", m)) for m in re.findall(r"\d[\d,]*", text)]
    if len(nums) < 2:
        return (0, 0)
    return (nums[0], nums[-1])


def await_panel_qty(timeout=None):
    deadline = time.monotonic() + (DIALOG_TIMEOUT if timeout is None
                                   else timeout)
    while time.monotonic() < deadline:
        held = read_panel_qty()
        if held[1]:
            return held
        time.sleep(POLL_GAP)
    return None


def read_panel_price():
    box = _panel()["price_field"]
    return calibration.read_number(calibration.grab(), tuple(box)) or 0


def suggested_price():
    prices = []
    for box in _panel()["suggestion_boxes"]:
        value = calibration.read_number(calibration.grab(), tuple(box))
        if value and value >= MIN_PLAUSIBLE_PRICE:
            prices.append(value)
    return min(prices) if prices else None


def read_panel_net():
    box = _panel().get("net_sales_box")
    if not box:
        return None
    return calibration.read_number(calibration.grab(), tuple(box)) or 0


def panel_agrees(want_qty, want_price, verbose=False):
    box = _panel().get("net_sales_box")
    if not box:
        raise Divergence(
            "the net sales box was never measured, so a price cannot be "
            "checked three ways. Recalibrate before listing anything.")
    expect = want_qty * want_price
    for attempt in range(1, PANEL_REREADS + 2):
        qty = read_panel_qty()[0]
        price = read_panel_price()
        net = read_panel_net() or 0
        derived = net // want_qty if want_qty and net % want_qty == 0 else None
        checks = {
            "quantity field": qty == want_qty,
            "price field": price == want_price,
            "net sales": net == expect,
            "net / quantity": derived == want_price,
        }
        if all(checks.values()):
            if verbose:
                print(f"    panel agrees four ways: {qty} x {price:,} "
                      f"= {net:,}")
            return True
        if verbose:
            bad = ", ".join(k for k, ok in checks.items() if not ok)
            print(f"    read {attempt}: {qty} at {price:,}, net {net:,} "
                  f"-- disagrees on {bad}")
    return False


def type_number(value):
    from open_inventory import press
    keys = _SHARED["input"]
    for _ in range(TYPE_CLEAR_PRESSES):
        press(keys["VK_BACK"])
        time.sleep(KEY_GAP)
    for ch in str(int(value)):
        press(keys[f"VK_{ch}"])
        time.sleep(KEY_GAP)


def row_function(text=None):
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
        if any(_key(t) == want and p[0] >= DIALOG_BUTTON_MIN_X
               for t, _c, p in calibration.ocr(
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


def read_row_one():
    return calibration.read_line(calibration.grab(), row_one_box())


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
        return True

    def cancel(self, index, verbose=True):
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

        show_work_tab(verbose=verbose)

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

    def list_slot(self, row, col, price=None, verbose=True):
        import open_agent_shop_premium as shop
        panel = _shop().get("panel")
        if not panel:
            raise Divergence(
                "the register panel has not been measured; run "
                "py src/calibration.py before listing anything.")

        point = shop.slot_point(int(row), int(col))
        if verbose:
            print(f"  inventory slot ({row},{col}) at {point}")
        before = read_panel_qty()
        if before[1]:
            raise Divergence(
                f"the shop slot already holds {before[0]} of {before[1]}; "
                f"clear it before listing another item.")

        calibration.ctrl_click(*point)
        held = await_panel_qty()
        if held is None:
            raise Divergence(
                f"nothing loaded into the shop slot from ({row},{col}). "
                f"Nothing has been listed.")
        if verbose:
            print(f"  loaded {held[0]} of {held[1]}")

        want = price if price is not None else suggested_price()
        if want is None:
            raise Divergence(
                "no price was given and the panel suggests none, so there is "
                "nothing to list at. Nothing has been listed.")
        if want < MIN_PLAUSIBLE_PRICE:
            raise Divergence(
                f"refusing to list at {want:,}, under the "
                f"{MIN_PLAUSIBLE_PRICE:,} plausibility floor.")

        calibration.click(*panel["qty_point"])
        type_number(held[1])
        calibration.click(*panel["price_point"])
        type_number(want)
        calibration.click(*panel["qty_point"])
        calibration.park()

        if not panel_agrees(held[1], want, verbose):
            raise Divergence(
                f"the panel does not agree that it holds {held[1]} at "
                f"{want:,}. Nothing has been listed.")
        if verbose:
            print(f"  panel confirms {typed[0]} at {shown:,}")

        calibration.click(*panel["register_button"], settle=0.0)
        confirm = find_button(CONFIRM_WORD)
        if confirm is None:
            raise Divergence(
                f"no {CONFIRM_WORD} appeared after Register. Nothing "
                f"committed.")
        calibration.click(*confirm, settle=0.0)
        calibration.park()
        if not dialog_gone():
            raise Divergence(
                f"the dialog stayed open after {CONFIRM_WORD}. Whether the "
                f"listing committed is unknown -- check the shop by hand.")
        landed = read_row_one()
        digits = [int(re.sub(r"[^\d]", "", m))
                  for m in re.findall(r"\d[\d,]*", landed)]
        if want not in digits:
            raise Divergence(
                f"the listing went through but row 1 reads {landed!r}, which "
                f"does not show {want:,}. Check the shop by hand -- something "
                f"is on the board at a price nobody chose.")
        if verbose:
            print(f"  listed {held[1]} at {want:,}; row 1 confirms it")
        return {"slot": (int(row), int(col)), "qty": held[1], "price": want}

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
                f"row {index} cannot be brought to position 1: with "
                f"{CAPACITY} rows and {VISIBLE} visible the top can only "
                f"reach {MAX_TOP}. Row {index} sits at position "
                f"{plan['reachable_at_row']} when the table is scrolled to "
                f"the end, and this model only ever operates on row 1.")
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
