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
FIRST_SEAT = "row_one_y"
LAST_SEAT = "row_last_y"

EMPTY_MARKER = _SHARED["text"]["empty_row"]
_TEXT = _SHARED["text"]
CHANGE_WORD = _TEXT["change_word"]
DISMISS_WORD = _TEXT["dismiss_word"]
CONFIRM_WORD = _TEXT["confirm_word"]
RECEIPT_WORD = _TEXT["receipt_word"]
REGISTER_WORD = _TEXT["register_word"]
STATUS_COMPLETE = _TEXT["status_complete"]
BUTTON_HALF = tuple(_SHARED["detect"]["dialog_button_half"])
RECEIPT_DROP_RATIO = _SHARED["detect"]["receipt_drop_ratio"]
WORD_ROW_SLACK = _SHARED["detect"]["word_row_slack"]
ROW_INSET = int(_SHARED["detect"]["row_inset"])
_PRICE_LIKE = re.compile(r"\d[\d,]{%d,}"
                         % _SHARED["detect"]["price_min_digits"])
NAME_LINE_LETTERS = int(_SHARED["detect"]["name_line_letters"])
DIALOG_TIMEOUT = _T["dialog_timeout"]
TAB_SETTLE = _T["tab_settle"]
REFRESH_SETTLE = _T["refresh_settle"]
CLEAR_PRESSES_QTY = _SHARED["detect"]["clear_presses_qty"]
CLEAR_PRESSES_PRICE = _SHARED["detect"]["clear_presses_price"]
KEY_GAP = _T["key_gap"]
CLEAR_GAP = _T["clear_gap"]
LOAD_ATTEMPTS = _SHARED["detect"]["load_attempts"]
PRICE_ATTEMPTS = _SHARED["detect"]["price_attempts"]
FIELD_SETTLE = _T["field_settle"]
SUGGESTION_RADIO_DX = _SHARED["detect"]["suggestion_radio_dx"]
PRICE_CHECK_FACTOR = _SHARED["run"]["price_check_factor"]
PANEL_REREADS = _SHARED["detect"]["panel_rereads"]
PRICE_TRUST = int(_SHARED["run"]["price_trust_multiple"])
ITEM_CHECK = float(_SHARED["run"]["item_check_factor"])
PANEL_REREAD_GAP = _T["panel_reread_gap"]
PANEL_POLL_GAP = _T["panel_poll_gap"]
PANEL_ITEM_HALF = int(_SHARED["detect"]["panel_item_half"])
NET_SALES_NUDGES = _SHARED["detect"]["net_sales_nudges"]
STALE_SWEEP = _T["stale_sweep"]
POLL_GAP = _T["poll_gap"]
GAME_WAIT_RETRIES = int(_SHARED["run"]["game_wait_retries"])

_NOT_ALNUM = re.compile(r"[^a-z0-9]")


class Divergence(Exception):
    pass


class SlotNeverFilled(Divergence):
    pass


class GameSaysWait(Divergence):
    pass


class NothingLoaded(Divergence):
    pass


class WrongItem(Divergence):
    pass


LAST_MARKET = {}
WORK_TAB_STALE = False


def note_market(name, unit):
    if name and unit and int(unit) >= MIN_PLAUSIBLE_PRICE:
        LAST_MARKET[item_key(name)] = int(unit)


def market_anchor(name):
    if not name:
        return 0
    known = LAST_MARKET.get(item_key(name))
    return int(known or calibration.market_unit(name) or 0)


def within(seen, expected):
    return bool(seen and expected
                and abs(int(seen) - int(expected))
                <= ITEM_CHECK * int(expected))


def identify_by_market(unit, names=None):
    names = (list(calibration.FAVOURITE_ITEMS.values()) if names is None
             else list(names))
    near = sorted((abs(market_anchor(n) - int(unit)), n) for n in names
                  if within(unit, market_anchor(n)))
    if not near:
        return None, False
    return near[0][1], len(near) == 1


def bundle_of(unit, name):
    anchor = market_anchor(name)
    if not anchor or not unit:
        return 0
    count = round(int(unit) / anchor)
    if count < 1:
        return 0
    return count if within(int(unit) // count, anchor) else 0


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


def seat_position(seat):
    return 1 if seat == FIRST_SEAT else VISIBLE


def last_seat_placed():
    return bool(_shop().get(LAST_SEAT))


def row_box(seat=FIRST_SEAT):
    x0, x1 = _need("table_x")
    y = _need(seat)
    half = max(1, _need("row_pitch") // 2 - ROW_INSET)
    return (x0, y - half, x1, y + half)


def button_point(seat=FIRST_SEAT):
    return (_need("button_x"), _need(seat))


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
    return any(calibration.button_word_matches(t, word)
               for t, _c, _p in calibration.ocr(image, box))


def search_button(word, timeout=None):
    deadline = time.monotonic() + (DIALOG_TIMEOUT if timeout is None
                                   else timeout)
    while time.monotonic() < deadline:
        for text, _conf, point in popup_words():
            if calibration.button_word_matches(text, word):
                return point
        time.sleep(POLL_GAP)
    return None


def _receipt_seat(verbose=False):
    seats = _shop()
    conf = seats.get(_button_key(CONFIRM_WORD))
    cancel = seats.get(_button_key(DISMISS_WORD))
    if not conf or not cancel:
        return None
    gap = abs(int(cancel[0]) - int(conf[0]))
    if gap <= 0:
        return None
    drop = round(RECEIPT_DROP_RATIO * gap)
    seat = (int(conf[0]), int(conf[1]) + drop)
    if verbose:
        print(f"  {RECEIPT_WORD} sits {drop} below the {CONFIRM_WORD} column "
              f"{list(conf)}, so it is at {list(seat)}")
    return seat


def find_button(word, timeout=None, verbose=False):
    if _key(word) == _key(RECEIPT_WORD):
        seat = _receipt_seat(verbose=verbose)
        if seat is not None:
            return seat
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
    from_cancel = False
    if point is None:
        calibration.snap(f"no_{_key(word)}_button_at_{known}")
    if point is not None and point != known and not from_cancel:
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
    if not already and calibration.await_inventory(verbose=verbose) is None:
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


def panel_item_point():
    measured = _shop().get("item_point")
    if measured:
        return (int(measured[0]), int(measured[1]))
    panel = _panel()
    x0, _y0, x1, _y1 = panel["price_field"]
    top = panel["panel_box"][1]
    first = panel["suggestion_boxes"][0][1]
    return ((x0 + x1) // 2, (top + first) // 2)


def panel_holds_item(image=None):
    image = image if image is not None else calibration.grab()
    x, y = panel_item_point()
    half = PANEL_ITEM_HALF
    crop = image.crop((x - half, y - half, x + half, y + half)).convert("L")
    data = list(crop.getdata())
    mean = sum(data) / len(data)
    stdev = (sum((v - mean) ** 2 for v in data) / len(data)) ** 0.5
    return stdev >= calibration.SLOT_OCCUPIED_STDEV


def _server_came_back(verbose=False):
    try:
        waited = bool(calibration.wait_out_server_lag(verbose=verbose))
    except RuntimeError as exc:
        raise Divergence(f"{exc} Nothing has been listed.")
    if waited:
        _back_in_the_shop(verbose=verbose)
    return waited


def _back_in_the_shop(verbose=False):
    import open_agent_shop_premium as shop
    if not calibration._trade_window_open():
        if verbose:
            print(f"  the shop was shut for the stall; reopening it before "
                  f"trying again")
        shop.open_agent_shop(verbose=False)
        time.sleep(TAB_SETTLE)
    calibration.click(*calibration.load()["shop"]["register_tab"])
    time.sleep(TAB_SETTLE)
    calibration.park()
    show_work_tab(verbose=verbose)


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
    bands = [(0, box)]
    for step in NET_SALES_NUDGES:
        bands.append((step, (box[0], box[1] + step,
                             box[2], box[3] + step)))
    for attempt in range(1, PANEL_REREADS + 2):
        image = calibration.grab()
        seen = []
        for step, band in bands:
            reading = calibration.read_money_all(image, band)
            if not step:
                seen = reading
            for net in reading:
                if net and net % want_price == 0:
                    qty = net // want_price
                    if verbose:
                        moved = "" if not step else (
                            f", from a band {abs(step)} "
                            f"{'lower' if step > 0 else 'higher'}")
                        print(f"    net sales {net:,} is {qty} x "
                              f"{want_price:,}{moved}")
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


def row_button_box(seat=FIRST_SEAT):
    half_x = BUTTON_HALF[0]
    half_y = max(1, int(_need("row_pitch")) // 2)
    x, y = int(_need("button_x")), int(_need(seat))
    return (x - half_x, y - half_y, x + half_x, y + half_y)


def row_button(image=None, seat=FIRST_SEAT):
    image = image if image is not None else calibration.grab()
    for text, _conf, _point in calibration.ocr(image, row_button_box(seat)):
        for word in (RECEIPT_WORD, CHANGE_WORD, REGISTER_WORD):
            if calibration.button_word_matches(text, word):
                return word
    return None


def row_button_text(image=None, seat=FIRST_SEAT):
    image = image if image is not None else calibration.grab()
    return " ".join(t for t, _c, _p in
                    calibration.ocr(image, row_button_box(seat)))


def row_function(text=None, seat=FIRST_SEAT):
    seen = row_button(seat=seat)
    if seen is not None:
        return seen
    text = read_row(seat) if text is None else text
    key = _key(text)
    for word in (RECEIPT_WORD, CHANGE_WORD, REGISTER_WORD):
        if _key(word) in key:
            return word
    return None


def row_complete(text=None, seat=FIRST_SEAT):
    text = read_row(seat) if text is None else text
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


def underprice_warning(image=None):
    return calibration.underprice_warning(image)


def underprice_warning_gone(timeout=None):
    return calibration.underprice_warning_gone(timeout)


def game_refused(done, timeout=None):
    deadline = time.monotonic() + (DIALOG_TIMEOUT if timeout is None
                                   else timeout)
    while time.monotonic() < deadline:
        image = calibration.grab()
        if done(image):
            return False
        if calibration.game_says_wait(image):
            return True
        time.sleep(POLL_GAP)
    return False


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


def read_row(seat=FIRST_SEAT):
    text = ""
    for attempt in range(PANEL_REREADS + 1):
        image = calibration.grab()
        box = row_box(seat)
        text = trim_borders(calibration.read_line(image, box))
        if text.strip():
            return text
        text = trim_borders(_row_words(image, box))
        if text.strip():
            return text
        if attempt < PANEL_REREADS:
            time.sleep(PANEL_REREAD_GAP)
    return text


def _row_words(image, box):
    lines = {}
    for text, _conf, (x, y) in calibration.ocr(image, box):
        seat = next((k for k in lines if abs(k - y) <= WORD_ROW_SLACK), y)
        lines.setdefault(seat, []).append((x, text))
    if not lines:
        return ""
    seats = sorted(lines)
    priced = [seat for seat in seats
              if _PRICE_LIKE.search(" ".join(w for _x, w in lines[seat]))]
    first_price = priced[0] if priced else None
    keep = []
    for seat in seats:
        text = " ".join(w for _x, w in lines[seat])
        if seat in priced:
            keep.append(seat)
        elif ((first_price is None or seat < first_price)
              and len(re.sub(r"[^A-Za-z]", "", text)) >= NAME_LINE_LETTERS):
            keep.append(seat)
    if not keep:
        keep = [seats[0]]
    out = []
    for seat in keep:
        out.extend(word for _x, word in sorted(lines[seat]))
    return " ".join(out)


def read_row_stacked(seat=FIRST_SEAT):
    return trim_borders(_row_words(calibration.grab(), row_box(seat)))


def row_is_empty(text=None, seat=FIRST_SEAT):
    text = read_row(seat) if text is None else text
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


def item_key(name):
    return _key(_PACK.sub("", name or ""))


def canonical(name):
    named = calibration.voucher_floor_ratio(name or "")[0]
    return named or (name or "")


def same_item(name, text):
    key = _key(name)
    if key and key in _key(text):
        return True
    named = calibration.voucher_floor_ratio(name or "")[0]
    return (named is not None
            and calibration.voucher_floor_ratio(text or "")[0] == named)


class Row:
    __slots__ = ("name", "qty", "price", "buy_cost", "units", "floor_at")

    def __init__(self, name, qty=1, price=0, buy_cost=0, units=None,
                 floor_at=0):
        self.name = name
        self.qty = int(qty)
        self.price = int(price)
        self.buy_cost = int(buy_cost)
        self.floor_at = int(floor_at or 0)
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
        return Row(self.name, self.qty, self.price, self.buy_cost, self.units,
                   self.floor_at)

    def __repr__(self):
        return (f"Row({self.name!r}, qty={self.qty}, units={self.units}, "
                f"price={self.price:,}, buy={self.buy_cost:,}, "
                f"floor_at={self.floor_at:,}, "
                f"sell_unit={self.sell_unit:,}, margin={self.margin:+,})")


class RowModel:
    def __init__(self, enforce=False):
        self._slots = {}
        self._work = {}
        self.work_seen = None
        self._floored = {}
        self._broken = set()
        self._top = None
        self._seat = FIRST_SEAT
        self.ready = False
        self.tracked = False
        self.enforce = enforce
        self.divergences = 0

    def save(self):
        if self.tracked:
            ledger.board_save(self._slots)

    def place(self, index, row):
        self._slots[int(index)] = row
        self.save()

    def drop(self, index):
        self._slots.pop(int(index), None)
        self.save()

    def forget_floor(self, index):
        self._floored.pop(int(index), None)
        self._broken.discard(int(index))

    def carry_floor(self, index, lands_in, broken, floored, parked):
        self.forget_floor(index)
        if broken:
            self._broken.add(int(lands_in))
        elif floored:
            self._floored[int(lands_in)] = parked + 1

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

    def reconcile_work_tab(self, held):
        held = {tuple(int(n) for n in slot) for slot in held}
        gone = sorted(slot for slot in self._work if slot not in held)
        for slot in gone:
            del self._work[slot]
        new = sorted(slot for slot in held if slot not in self._work)
        for slot in new:
            self._work[slot] = None
        return new, gone

    def _resolve_loaded(self, market, expect_item, qty_seen, seen, verbose):
        say = print if verbose else (lambda *a: None)
        say(f"    {seen}; this is not {expect_item!r}")
        for slot, what in sorted(self._work.items()):
            if not isinstance(what, Row):
                continue
            if qty_seen is not None and what.qty != qty_seen:
                continue
            if qty_seen is None and not within(
                    market, market_anchor(what.name) * what.pack):
                continue
            cost = what.floor_at or what.buy_cost
            floor = (cost or calibration.price_floor(what.name)[0]) * what.pack
            price = (max(calibration.undercut(market), floor) if market
                     else max(what.price, floor))
            del self._work[slot]
            say(f"    it is the {what.name!r} x{what.qty} the run set down in "
                f"tab {WORK_TAB} slot {slot} and never listed back; listing "
                f"it as that at {price:,}")
            return {"item": what.name, "price": price, "floor": floor,
                    "why": "it is what the run set down", "slot": slot}
        held = bundle_of(market, expect_item)
        if held > 1:
            floor = calibration.price_floor(expect_item)[0] * held
            price = max(calibration.undercut(market), floor)
            say(f"    at {market // held:,} a unit it is {expect_item!r} "
                f"after all, {held} of them and not the {qty_seen or 1} "
                f"the run put down; listing it at {price:,}"
                + (f", no lower than the {floor:,} they cost" if floor
                   else ""))
            return {"item": expect_item, "price": price, "floor": floor,
                    "why": "the market counts it", "slot": None}
        if market:
            named, sure = identify_by_market(market)
            floor = calibration.price_floor(named)[0] if named and sure else 0
            price = max(calibration.undercut(market), floor)
            label = repr(named) if named else "nothing the run trades"
            say(f"    the market {market:,} says {label}"
                + ("" if sure else ", or something priced like it")
                + f"; listing it at {price:,}, its own market")
            return {"item": named, "price": price, "floor": floor,
                    "why": "the market says so", "slot": None}
        raise Divergence(
            f"{seen}, the market would not price it, and nothing the run set "
            f"down in tab {WORK_TAB} matches; it is left loaded in the "
            f"panel. Nothing has been listed.")

    def next_work_slot(self):
        for row in range(1, GRID + 1):
            for col in range(1, GRID + 1):
                if (row, col) not in self._work:
                    return (row, col)
        return None

    def work_slots(self):
        return sorted(self._work)

    def hold_work(self, slot, what=None):
        slot = tuple(int(n) for n in slot)
        if slot not in self._work:
            print(f"    tab {WORK_TAB} slot {slot} is held for "
                  f"{what!r}; it frees when something is listed from it")
        self._work[slot] = what

    def release_work(self, slot):
        self._work.pop(tuple(int(n) for n in slot), None)

    def move_work(self, old, new):
        old = tuple(int(n) for n in old)
        new = tuple(int(n) for n in new)
        self._work[new] = self._work.pop(old, None)

    def note_cancel(self, index):
        index = int(index)
        row = self._slots.get(index)
        if row is None:
            raise ValueError(f"row {index} is already empty; nothing to cancel")
        del self._slots[index]
        self.save()
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
        point = button_point(self._seat)
        if verbose:
            print(f"  {RECEIPT_WORD} at {point}")
        calibration.click(*point)
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
            held = pack_size(listed.name)
        if held < 1:
            held = 1
        ledger.sold(listed.name, price // held, proceeds, sold * held)
        if verbose:
            print(f"  collected {proceeds:,} Alz for {sold} x "
                  f"{listed.name!r} at {price:,}"
                  + (f", {held} to a listing" if held > 1 else ""))

    def reopen_after_wait(self, verbose=True):
        import open_agent_shop_premium as shop
        calibration.close_everything()
        shop.open_agent_shop(verbose=False)
        time.sleep(TAB_SETTLE)
        calibration.click(*_shop()["register_tab"])
        time.sleep(TAB_SETTLE)
        calibration.park()
        self._top = None
        self._seat = FIRST_SEAT
        if verbose:
            print(f"  the Agent Shop is closed and open again on the Register "
                  f"tab")

    def again_after_wait(self, what, attempt, verbose=True):
        calibration.snap("game_says_wait")
        if attempt >= GAME_WAIT_RETRIES:
            raise GameSaysWait(
                f"the game answered 'please wait and try again' to {what} "
                f"{attempt + 1} time(s); nothing more is tried.")
        if verbose:
            print(f"  the game answered 'please wait and try again' to {what}; "
                  f"closing the Agent Shop and doing it again "
                  f"({attempt + 1} of {GAME_WAIT_RETRIES})")
        self.reopen_after_wait(verbose=verbose)

    def cancel(self, index, verbose=True, tab_ready=False):
        attempt = 0
        while True:
            try:
                return self._cancel(index, verbose=verbose,
                                    tab_ready=tab_ready and not attempt)
            except GameSaysWait:
                self.again_after_wait(f"cancelling row {index}", attempt,
                                      verbose=verbose)
                attempt += 1

    def _cancel(self, index, verbose=True, tab_ready=False):
        index = int(index)
        expected = self._slots.get(index)
        if expected is None:
            raise ValueError(f"row {index} is empty in the model; refusing to "
                             f"cancel a slot nothing is listed in")
        if self.scroll_to(index, verbose=verbose):
            time.sleep(TAB_SETTLE)

        seat = self._seat
        position = seat_position(seat)
        seen = read_row(seat)
        action = row_function(seen, seat)
        if action == RECEIPT_WORD:
            complete = row_complete(seen, seat)
            if verbose:
                print(f"  row {index} has SOLD "
                      f"({'fully' if complete else 'partially'}); collecting "
                      f"before anything else")
            self.receive(index, verbose=verbose)
            time.sleep(TAB_SETTLE)
            seen = read_row(seat)
            if complete or row_function(seen, seat) == REGISTER_WORD:
                if verbose:
                    print(f"  row {index} is empty after the collection; "
                          f"nothing left to cancel")
                return self.note_cancel(index)
            action = row_function(seen, seat)
        if action == REGISTER_WORD:
            raise Divergence(
                f"row {index} is empty on screen; nothing to cancel.")
        if not expected.key or not same_item(expected.name, seen):
            stacked = read_row_stacked(seat)
            if not expected.key or not same_item(expected.name, stacked):
                raise Divergence(
                    f"row {index} should hold {expected.name!r} but position "
                    f"{position} reads {seen!r} on one line, and {stacked!r} "
                    f"read as stacked lines. Not cancelling a row that is not "
                    f"the one the model names.")
            if verbose:
                print(f"  position {position} read {seen!r} on one line; "
                      f"read as stacked lines it is {expected.name!r}")
        if verbose:
            print(f"  row {index} at position {position}: {seen!r}")

        show_work_tab(verbose=verbose, already=tab_ready)

        point = button_point(seat)
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
        landing = self.next_work_slot()
        if landing is not None and game_refused(
                lambda image: not calibration.slot_is_empty(image, *landing)):
            raise GameSaysWait(f"cancelling row {index}")
        result = self.note_cancel(index)
        if verbose:
            print(f"  row {index} cancelled; {expected.name!r} lands in tab "
                  f"{result['lands_in_tab']} slot {result['lands_in_slot']}")
        return result

    def list_slot(self, row, col, verbose=True, **kw):
        attempt = 0
        while True:
            try:
                return self._list_slot(row, col, verbose=verbose, **kw)
            except GameSaysWait:
                self.again_after_wait(f"listing from ({row},{col})", attempt,
                                      verbose=verbose)
                attempt += 1

    def _list_slot(self, row, col, price=None, floor=0, why="", verbose=True,
                   lands_in=None, expect_item=None,
                   expect_price=None, unit_market=None, floor_each=0,
                   listed_at=None, wait_fill=True, price_each=None,
                   expect_qty=None, expect_market=None, resolve=True,
                   under=None):
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
            standing = panel_standing() if panel_holds_item() else None
        if standing is not None:
            raise Divergence(
                f"the shop slot already holds something the panel prices at "
                f"{standing:,}; clear it before listing another item.")

        deadline = time.monotonic() + DIALOG_TIMEOUT
        filled, lagged = not wait_fill, 0
        while not filled:
            while time.monotonic() < deadline:
                with calibration.step(f"wait for slot ({row},{col}) to fill"):
                    filled = not calibration.slot_is_empty(calibration.grab(),
                                                           int(row), int(col))
                if filled:
                    break
                time.sleep(POLL_GAP)
            if filled:
                break
            calibration.snap(f"slot_{row}x{col}_never_filled")
            if lagged < LOAD_ATTEMPTS and _server_came_back(verbose):
                lagged += 1
                deadline = time.monotonic() + DIALOG_TIMEOUT
                continue
            raise SlotNeverFilled(
                f"tab {WORK_TAB} slot ({row},{col}) is still empty "
                f"{DIALOG_TIMEOUT:g}s after the withdrawal. Nothing listed.")

        suggested = None
        attempt = lagged = 0
        while attempt < PRICE_ATTEMPTS:
            attempt += 1
            with calibration.step(f"ctrl-click ({row},{col}) attempt {attempt}"):
                calibration.ctrl_click(*point)
            with calibration.step("read the suggested price"):
                suggested = suggested_price(verbose, listed_at)
            if suggested is not None:
                break
            calibration.snap(f"nothing_loaded_{row}x{col}_{attempt}")
            if lagged < LOAD_ATTEMPTS and _server_came_back(verbose):
                lagged += 1
                attempt -= 1
                if verbose:
                    print(f"  the server was not answering, so that ctrl-click "
                          f"does not count against the {PRICE_ATTEMPTS} tries")
                continue
            if verbose:
                print(f"  ctrl-click {attempt}/{PRICE_ATTEMPTS} loaded nothing "
                      f"from ({row},{col})")
        market = suggested
        if suggested is None and listed_at:
            suggested = int(listed_at)
            price = suggested
            if verbose:
                print(f"  the market would not price it after "
                      f"{PRICE_ATTEMPTS} ctrl-click(s); listing at the "
                      f"{suggested:,} it came out of the row at")
        if suggested is None:
            raise NothingLoaded(
                f"nothing loaded into the shop slot from ({row},{col}) after "
                f"{PRICE_ATTEMPTS} ctrl-click(s). Nothing has been listed.")
        resolved = None
        if market and expect_market and not within(market, expect_market):
            seen = (f"the panel prices what loaded from ({row},{col}) at "
                    f"{market:,}, and {expect_item!r} sells near "
                    f"{int(expect_market):,}")
            if not resolve:
                raise WrongItem(f"{seen}. Nothing has been listed.")
            resolved = self._resolve_loaded(market, expect_item, None, seen,
                                            verbose)
            price, floor, why = (resolved["price"], resolved["floor"],
                                 resolved["why"])
            expect_item, unit_market, price_each, listed_at = (
                resolved["item"], None, None, None)
        elif (expect_item is None and price is None and price_each is None
              and market and resolve):
            named, sure = identify_by_market(market)
            if named:
                expect_item = named
                if sure:
                    floor = max(int(floor or 0),
                                calibration.price_floor(named)[0])
                    why = why or f"the market names it {named}"
                if verbose:
                    print(f"    the market {market:,} says {named!r}"
                          + ("" if sure else ", or something priced like it")
                          + (f"; its floor is {floor:,}" if sure and floor
                             else ""))
        count = None
        if unit_market:
            count = max(1, round(suggested / unit_market))
            if floor_each:
                floor = floor_each * count
            if verbose:
                print(f"  the panel prices the bundle at {suggested:,}, "
                      f"{count} x {unit_market:,}"
                      + (f"; the floor is {floor:,}" if floor else ""))
            if price is None and price_each:
                price = price_each * count
                if verbose:
                    print(f"  asking {price_each:,} each, {price:,} for the "
                          f"{count}")

        want = (price if price is not None
                else calibration.undercut(suggested, under))
        if want is None:
            raise Divergence(
                "no price was given and the panel suggests none, so there is "
                "nothing to list at. Nothing has been listed.")
        cap = calibration.max_drop(expect_item)
        each = count or (pack_size(expect_item) if expect_item else 1)
        held = listed_at - cap * each if cap and listed_at else 0
        if held and want and listed_at > want * PRICE_TRUST:
            if verbose:
                print(f"    our {listed_at:,} is over {PRICE_TRUST} times "
                      f"the {want:,} the market asks, so it is not a price "
                      f"this row can really be listed at; the fall limit "
                      f"does not hold it there")
            held = 0
        if held and want < held:
            if verbose:
                print(f"    the market asks {want:,}, {listed_at - want:,} "
                      f"under our {listed_at:,}; holding at {held:,}, the "
                      f"most this row may fall in one relist")
            want = held
        floored = bool(floor) and want < floor
        if floored:
            if verbose:
                print(f"    market {want:,} is under the {floor:,} floor"
                      + (f" ({why})" if why else "") + f"; listing at the floor")
            want = floor
        if want < MIN_PLAUSIBLE_PRICE:
            raise Divergence(
                f"refusing to list at {want:,}, under the "
                f"{MIN_PLAUSIBLE_PRICE:,} plausibility floor.")

        if verbose:
            if suggested is None:
                print(f"    no market price was read; typing {want:,}")
            else:
                apart = want - suggested
                print(f"    market {suggested:,}, typing {want:,}"
                      + (f", {abs(apart):,} "
                         f"{'over' if apart > 0 else 'under'} the market"
                         if apart else ", the market itself"))

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
        if expect_qty and qty < int(expect_qty) and resolved is None:
            seen = (f"the panel offers {qty} from ({row},{col}) and "
                    f"{int(expect_qty)} of {expect_item!r} came off the row")
            if not resolve:
                raise WrongItem(f"{seen}. Nothing has been listed.")
            resolved = self._resolve_loaded(market, expect_item, qty, seen,
                                            verbose)
            want, floor, expect_item = (resolved["price"], resolved["floor"],
                                        resolved["item"])
            with calibration.step(f"type the price {want:,} instead"):
                calibration.click(*panel["price_point"], settle=FIELD_SETTLE)
                type_number(want, CLEAR_PRESSES_PRICE)
                calibration.park()
            with calibration.step("take the quantity from the net sales "
                                  "again"):
                qty = panel_quantity(want, verbose)
            if qty is None:
                calibration.snap("panel_will_not_confirm")
                raise Divergence(
                    f"the panel will not price {want:,} against its net "
                    f"sales for what loaded from ({row},{col}). Nothing "
                    f"has been listed.")
            floored = bool(floor) and want <= floor
        with calibration.step("click Register"):
            calibration.click(*panel["register_button"], settle=0.0)
        with calibration.step(f"find {CONFIRM_WORD}"):
            confirm = find_button(CONFIRM_WORD)
        if confirm is None:
            raise Divergence(
                f"no {CONFIRM_WORD} appeared after Register. Nothing "
                f"committed.")

        with calibration.step("look for the underprice question"):
            warned = underprice_warning()
        if warned:
            calibration.snap("underprice_warning")
            if verbose:
                print(f"    the game asks again because {want:,} is at least "
                      f"25% under its average for this item; accepting")
            with calibration.step(f"click {CONFIRM_WORD} on the question"):
                calibration.click(*confirm, settle=0.0)
            with calibration.step("wait for the question to go"):
                if not underprice_warning_gone():
                    calibration.snap("underprice_warning_stays")
                    raise Divergence(
                        f"the underprice question stayed open after "
                        f"{CONFIRM_WORD}. Nothing committed.")
            with calibration.step(f"find {CONFIRM_WORD} on the real dialog"):
                confirm = find_button(CONFIRM_WORD)
            if confirm is None:
                calibration.snap("no_confirm_after_warning")
                raise Divergence(
                    f"no {CONFIRM_WORD} appeared after the underprice "
                    f"question was accepted. Nothing committed.")
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
        with calibration.step("wait for the panel to let the item go"):
            held = panel_holds_item()
            deadline = time.monotonic() + DIALOG_TIMEOUT
            stalled = None
            while held and time.monotonic() < deadline:
                if calibration.game_says_wait():
                    raise GameSaysWait(f"listing from ({row},{col})")
                if calibration.server_busy():
                    if stalled is None:
                        stalled = time.monotonic()
                        calibration.snap("server_busy_after_confirmation")
                    if (time.monotonic() - stalled
                            < calibration.SERVER_LAG_BUDGET):
                        deadline = time.monotonic() + DIALOG_TIMEOUT
                time.sleep(PANEL_POLL_GAP)
                held = panel_holds_item()
            standing = panel_standing() if held else None
        if standing is not None:
            calibration.snap("panel_kept_the_item")
            raise Divergence(
                f"the panel still holds the item, priced {standing:,}, "
                f"{DIALOG_TIMEOUT:g}s after {CONFIRM_WORD}"
                + (f" and {time.monotonic() - stalled:.0f}s of the server "
                   f"not answering" if stalled is not None else "")
                + f"; the registration did not go through. Nothing is "
                  f"listed.")
        if stalled is not None and verbose:
            print(f"  the server stopped answering after {CONFIRM_WORD}; the "
                  f"panel let the item go {time.monotonic() - stalled:.0f}s "
                  f"later, so the listing registered")
        self.release_work((row, col))
        with calibration.step(f"read tab {WORK_TAB} after the listing"):
            began = time.perf_counter()
            self.work_seen = calibration.occupied_slots()
            took = (time.perf_counter() - began) * 1000
        if verbose:
            print(f"  tab {WORK_TAB} read in {took:.0f} ms after the listing: "
                  f"{len(self.work_seen)} slot(s) held")
        if market:
            note_market(expect_item,
                        market if resolved else market // max(1, each))
        calibration.steps_table(f"list {qty} at {want:,}")
        if verbose:
            print(f"  listed {qty} at {want:,}"
                  + (f"; it lands in row {int(lands_in)}"
                     if lands_in is not None else ""))
        return {"item": expect_item, "resolved": resolved is not None,
                "slot": (int(row), int(col)), "qty": qty,
                "price": want, "row": lands_in, "floored": floored,
                "units": count}

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
        self.save()
        return {
            "row": index,
            "remaining": left,
            "shop_slot_now": left,
            "renumbered": [],
        }

    @property
    def top(self):
        return self._top

    @property
    def seat(self):
        return self._seat

    def visible(self, top=None):
        top = self._top if top is None else int(top)
        if top is None:
            return []
        return [i for i in range(top, min(top + VISIBLE, CAPACITY + 1))]

    def can_top(self, index):
        return 1 <= int(index) <= MAX_TOP

    def seat_of(self, index):
        index = int(index)
        if not 1 <= index <= CAPACITY:
            raise ValueError(f"row {index} is outside 1..{CAPACITY}")
        if index <= MAX_TOP:
            return FIRST_SEAT, index
        return LAST_SEAT, index - VISIBLE + 1

    def row_at_seat(self):
        if self._top is None:
            return None
        return self._top + seat_position(self._seat) - 1

    def scroll_plan(self, index):
        seat, want = self.seat_of(index)
        if self._top is None:
            raise Divergence(
                "the top visible row is unknown, so a scroll cannot be "
                "counted. Seed the model from a read first.")
        return {
            "from_top": self._top,
            "to_top": want,
            "notches": want - self._top,
            "seat": seat,
            "position": seat_position(seat),
        }

    def note_scrolled(self, to_top):
        self._top = int(to_top)
        return self._top

    def home(self, verbose=True):
        wheel(-HOME_NOTCHES, verbose=False)
        time.sleep(ACTION_GAP)
        self._top = 1
        self._seat = FIRST_SEAT
        if verbose:
            print(f"  scrolled to the top; row 1 is at position 1")
        return 1

    def bottom(self, verbose=True):
        wheel(HOME_NOTCHES, verbose=False)
        time.sleep(ACTION_GAP)
        self._top = MAX_TOP
        self._seat = LAST_SEAT
        if verbose:
            print(f"  scrolled to the bottom; row {CAPACITY} is at position "
                  f"{VISIBLE}")
        return MAX_TOP

    def scroll_to(self, index, verbose=True):
        seat, _want = self.seat_of(index)
        if seat == LAST_SEAT and (self._seat != LAST_SEAT
                                  or self._top is None):
            self.bottom(verbose=verbose)
        elif self._top is None:
            self.home(verbose=verbose)
        plan = self.scroll_plan(index)
        if plan["notches"]:
            wheel(plan["notches"], verbose=verbose)
        self.note_scrolled(plan["to_top"])
        self._seat = seat
        if verbose:
            print(f"  row {index} is now at position {plan['position']}")
        return plan

    def read(self):
        return read_row(self._seat)

    def read_stacked(self):
        return read_row_stacked(self._seat)

    def button(self, image=None):
        return row_button(image, self._seat)

    def button_text(self, image=None):
        return row_button_text(image, self._seat)

    def is_empty(self, text=None):
        return row_is_empty(text, self._seat)

    def function(self, text=None):
        return row_function(text, self._seat)

    def complete(self, text=None):
        return row_complete(text, self._seat)

    def at(self, index, verbose=False):
        self.scroll_to(index, verbose=verbose)
        return self.read()

    def verify(self, text=None, index=None):
        if self._top is None:
            raise Divergence("the top visible row is unknown; nothing to verify")
        here = self.row_at_seat()
        index = here if index is None else int(index)
        if index != here:
            raise Divergence(
                f"row {index} is not at position {seat_position(self._seat)} "
                f"(row {here} is). Scroll to it first.")
        text = self.read() if text is None else text
        mine = self._slots.get(index)
        read_empty = self.is_empty(text)
        if mine is None:
            agrees = read_empty
        else:
            agrees = (not read_empty) and mine.key in _key(text)
        if not agrees:
            self.divergences += 1
            if self.enforce:
                raise Divergence(
                    f"row {index}: the model holds {mine!r} but position "
                    f"{seat_position(self._seat)} "
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
