import base64
import ctypes
import io
import json
import queue
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageStat

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
PRICE_RECHECKS = int(_SHARED["detect"]["price_rechecks"])
PRICE_SLACK = int(_SHARED["detect"]["price_agree_slack"])
PRICE_WITNESSES = int(_SHARED["detect"]["price_witnesses"])
_BACKUP = _SHARED["ocr"]
BACKUP_PYTHON = calibration.HERE / _BACKUP["backup_python"]
BACKUP_READER = calibration.HERE / _BACKUP["backup_reader"]
BACKUP_MODEL = _BACKUP["backup_model"]
BACKUP_SCALE = int(_BACKUP["backup_scale"])
BACKUP_START_TIMEOUT = _BACKUP["backup_start_timeout"]
BACKUP_TIMEOUT = _BACKUP["backup_timeout"]
PRICE_TRUST = int(_SHARED["run"]["price_trust_multiple"])
ITEM_CHECK = float(_SHARED["run"]["item_check_factor"])
PANEL_REREAD_GAP = _T["panel_reread_gap"]
RECEIPT_SETTLE = _T["receipt_settle"]
PANEL_POLL_GAP = _T["panel_poll_gap"]
PANEL_ITEM_HALF = int(_SHARED["detect"]["panel_item_half"])
NET_SALES_NUDGES = _SHARED["detect"]["net_sales_nudges"]
STALE_SWEEP = _T["stale_sweep"]
POLL_GAP = _T["poll_gap"]
CHANGE_RETRIES = int(_T["change_retries"])
_CHANGE_STALLS = {}
GAME_WAIT_RETRIES = int(_SHARED["run"]["game_wait_retries"])

_NOT_ALNUM = re.compile(r"[^a-z0-9]")


class Divergence(Exception):
    pass


class SlotNeverFilled(Divergence):
    pass


class GameSaysWait(Divergence):
    def __init__(self, what, answer=None):
        super().__init__(what)
        self.answer = answer


class NothingLoaded(Divergence):
    pass


class WrongItem(Divergence):
    pass


LAST_MARKET = {}
WORK_TAB_STALE = False


def note_market(name, unit, sure=False):
    if not name or not unit or int(unit) < MIN_PLAUSIBLE_PRICE:
        return
    if not sure and calibration.favourite_slot_of(name) is not None:
        return
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


def receipt_dismiss_point():
    seat = _receipt_seat()
    cancel = remembered(DISMISS_WORD)
    if seat is None or cancel is None:
        return None
    return (int(cancel[0]), seat[1])


def find_button(word, timeout=None, verbose=False, hover=False):
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
            image = calibration.grab()
            if hover:
                calibration.hover(*known)
            if button_here(word, known, image):
                return known
            if hover:
                calibration.park(settle=False)
                hover = False
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
    calibration.click(*point, settle=0.0, check_hovering=True, alongside=True)
    time.sleep(REFRESH_SETTLE)
    return tuple(point)


def show_work_tab(verbose=False, already=False, released=None):
    import open_agent_shop_premium as shop
    if not already and calibration.await_inventory(verbose=verbose) is None:
        raise Divergence(
            "no readable Alz balance; the Inventory panel is not open. "
            "Nothing cancelled.")
    point = shop.tab_point(WORK_TAB)
    if verbose:
        print(f"  inventory tab {WORK_TAB} at {point}, so the cancelled item "
              f"has nowhere else to land")
    calibration.click(*point, released=released)
    return point


MIN_PLAUSIBLE_PRICE = _SHARED["detect"]["min_plausible_price"]

def _panel():
    part = _shop().get("panel")
    if not part:
        raise Divergence(
            "the register panel has not been measured; run "
            "py src/calibration.py before listing anything.")
    return part


_LETTER = re.compile(r"[A-Za-z]")
_UNIT = re.compile(calibration._ALZ_WORD, re.IGNORECASE)


def _in_band(spans, box):
    here = sorted((span for span in spans
                   if box[1] <= span[2][1] <= box[3]
                   and box[0] <= span[2][0] <= box[2]),
                  key=lambda span: span[2][0])
    text = " ".join(text for text, _c, _p, _r in here)
    if _LETTER.search(_UNIT.sub("", text)):
        return None, text
    return calibration._digits(text), None


def warm_money(image, box):
    prepared = calibration.isolate_digits(image, tuple(box))
    if prepared is None:
        return None
    return calibration._digits(calibration._tesseract(
        prepared, calibration.ROW_PSM, calibration.DIGIT_WHITELIST))


def only_boxes(image, boxes):
    band = (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))
    fill = tuple(int(v) for v in ImageStat.Stat(image.crop(band)).median)
    kept = Image.new(image.mode, image.size, fill)
    for box in boxes:
        kept.paste(image.crop(tuple(box)), tuple(box[:2]))
    return kept


def _asking(image, panel):
    rows = panel["suggestion_boxes"]
    field = tuple(panel["price_field"])
    wanted = [(tuple(rows[-1]), False), (field, True),
              (tuple(panel["net_sales_box"]) if panel.get("net_sales_box")
               else None, False),
              (tuple(rows[0]) if len(rows) > 1 else None, False)]
    live = [box for box, _warm in wanted if box]
    band = (min(b[0] for b in live), min(b[1] for b in live),
            max(b[2] for b in live), max(b[3] for b in live))
    spans = calibration.ocr_spans(only_boxes(image, live), band)
    out = []
    for box, warm in wanted:
        if box is None:
            out.append(None)
            continue
        if warm:
            out.append(warm_money(image, box))
            continue
        value, mixed = _in_band(spans, box)
        if mixed is not None:
            out.append(None)
            continue
        if value is None or value < MIN_PLAUSIBLE_PRICE:
            value = calibration.read_money(image, box)
        out.append(value)
    return tuple(out)


def _near(a, b):
    return a and b and (a / PRICE_CHECK_FACTOR <= b <= a * PRICE_CHECK_FACTOR)


def _same_price(a, b):
    if not a or not b:
        return False
    if abs(a - b) <= PRICE_SLACK:
        return True
    high, low = (a, b) if a > b else (b, a)
    return bool(low) and high % low == 0


def _places_agree(asked, filled, net, say):
    core = [(v, w) for v, w in ((asked, "current min"),
                                (filled, "price field"),
                                (net, "net sales"))
            if v and v >= MIN_PLAUSIBLE_PRICE]
    if len(core) < PRICE_WITNESSES:
        say(f"    only {len(core)} of the three price places read; "
            f"{PRICE_WITNESSES} must agree before anything is typed")
        return False
    base = core[0][0]
    for value, where in core[1:]:
        if _same_price(base, value):
            continue
        say(f"    {core[0][1]} says {base:,} but {where} says {value:,}; "
            f"the price places must agree before anything is typed")
        return False
    return True


def _agreed(asked, filled, average, listed_at, verbose, net=None):
    say = print if verbose else (lambda *a: None)
    say("    read: " + ", ".join(
        f"{w} {v:,}" if v else f"{w} unread"
        for v, w in ((asked, "current min"), (filled, "price field"),
                     (net, "net sales"), (average, "week average"),
                     (listed_at, "listed at"))))
    if not _places_agree(asked, filled, net, say):
        return None
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
    if average and average >= MIN_PLAUSIBLE_PRICE and not _near(value,
                                                               average):
        say(f"    the week's average says {average:,} against the {value:,} "
            f"being asked; they are too far apart for the read to be trusted")
        return None
    odd = [o for o in seen if o not in with_it]
    if odd:
        say(f"    {', '.join(f'{o[1]} says {o[0]:,}' for o in odd)}, against "
            f"{value:,} from {agreeing} of the three; taking {value:,}")
    else:
        say(f"    the lowest listed price is {value:,}, from {where}"
            + (f" and {agreeing - 1} more" if agreeing > 1 else ""))
    return value


_reader = {"proc": None, "lines": None, "failed": None}
_reading = threading.Lock()
_told = set()


def _backup_reader():
    proc = _reader["proc"]
    if proc is not None and proc.poll() is None:
        return _reader["lines"]
    if _reader["failed"]:
        return None
    if not BACKUP_PYTHON.exists():
        _reader["failed"] = f"PaddleOCR is not set up at {BACKUP_PYTHON}"
        return None
    proc = subprocess.Popen(
        [str(BACKUP_PYTHON), str(BACKUP_READER), BACKUP_MODEL],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW)
    lines = queue.Queue()

    def pump():
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    try:
        ready = lines.get(timeout=BACKUP_START_TIMEOUT)
    except queue.Empty:
        ready = None
    if not ready:
        proc.kill()
        _reader["failed"] = (f"PaddleOCR did not start within "
                             f"{BACKUP_START_TIMEOUT}s")
        return None
    _reader["proc"], _reader["lines"] = proc, lines
    return lines


def start_backup_reader():
    def start():
        with _reading:
            _backup_reader()
    threading.Thread(target=start, daemon=True).start()


def backup_money(image, boxes):
    if _reader["failed"]:
        return None
    texts = _backup_texts([calibration.isolate_digits(image, tuple(box),
                                                      BACKUP_SCALE)
                           if box else None for box in boxes])
    return None if texts is None else [calibration._digits(t) for t in texts]


def _backup_line(image, box):
    ink = calibration.ink_box(image, tuple(box))
    if ink is None:
        return None
    crop = image.crop(ink)
    return crop.resize((crop.width * BACKUP_SCALE, crop.height * BACKUP_SCALE),
                       Image.LANCZOS)


def paddle_row(image, name_box, qty_box, price_box):
    if _reader["failed"]:
        return None
    return _backup_texts([_backup_line(image, name_box),
                          _backup_line(image, qty_box),
                          calibration.isolate_digits(image, tuple(price_box),
                                                     BACKUP_SCALE)])


def _backup_texts(prepared):
    crops = []
    for picture in (p for p in prepared if p is not None):
        buf = io.BytesIO()
        picture.convert("RGB").save(buf, "PNG")
        crops.append(base64.b64encode(buf.getvalue()).decode("ascii"))
    with _reading:
        lines = _backup_reader()
        if lines is None:
            return None
        proc = _reader["proc"]
        try:
            proc.stdin.write(json.dumps(crops) + "\n")
            proc.stdin.flush()
            answer = lines.get(timeout=BACKUP_TIMEOUT)
        except (OSError, queue.Empty):
            answer = None
        if not answer:
            proc.kill()
            _reader["proc"] = None
            _reader["failed"] = (f"PaddleOCR did not answer within "
                                 f"{BACKUP_TIMEOUT}s")
            return None
    read = iter(text for text, _score in json.loads(answer))
    return [next(read) if p is not None else None for p in prepared]


def _disputed(asked, filled, net):
    core = [v for v in (asked, filled, net)
            if v and v >= MIN_PLAUSIBLE_PRICE]
    return len(core) == PRICE_WITNESSES or (
        len(core) > 1 and not all(_same_price(core[0], v) for v in core[1:]))


def _read_and_agree(panel, listed_at, verbose, image=None):
    image = calibration.grab() if image is None else image
    asked, filled, net, average = _asking(image, panel)
    value = _agreed(asked, filled, average, listed_at, verbose, net)
    if value is not None or not _disputed(asked, filled, net):
        return value
    rows = panel["suggestion_boxes"]
    boxes = [rows[-1], panel["price_field"], panel.get("net_sales_box"),
             rows[0] if len(rows) > 1 else None]
    started = time.perf_counter()
    again = backup_money(image, boxes)
    if again is None:
        if verbose:
            print(f"    the reads disagree and "
                  f"{_reader['failed'] or 'PaddleOCR did not answer'}; "
                  f"nothing is typed from this read")
        return None
    if verbose:
        print(f"    the reads disagree; PaddleOCR read all four again "
              f"in {(time.perf_counter() - started) * 1000:.0f} ms")
    p_asked, p_filled, p_net, p_average = again
    return _agreed(p_asked, p_filled, p_average, listed_at, verbose, p_net)


def panel_standing():
    asked, filled, _net, _average = _asking(calibration.grab(), _panel())
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


_PICKING = ThreadPoolExecutor(max_workers=1)


def _pick_suggestion(radio):
    calibration.click(*radio, settle=0.0)
    calibration.park(settle=False)
    time.sleep(FIELD_SETTLE)


def suggested_price(verbose=False, listed_at=None, overlap=False):
    panel = _panel()
    box = tuple(panel["suggestion_boxes"][-1])
    radio = (box[0] - SUGGESTION_RADIO_DX, (box[1] + box[3]) // 2)
    if overlap:
        image = calibration.grab()
        picking = _PICKING.submit(_pick_suggestion, radio)
        try:
            value = _read_and_agree(panel, listed_at, verbose, image)
        finally:
            picking.result()
    else:
        value = _read_and_agree(panel, listed_at, verbose)
        _pick_suggestion(radio)
    if value is None:
        value = _read_and_agree(panel, listed_at, verbose)
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


def check_price_field(want, qty, verbose=False, next_point=None):
    image = calibration.grab()
    if next_point is not None:
        calibration.hover(*next_point)
    shown = warm_money(image, tuple(_panel()["price_field"]))
    if verbose:
        print(f"    the panel holds {shown:,} against the {want:,} typed"
              if shown is not None else
              f"    the panel price would not read back")
    if shown is None or shown == want:
        return
    if next_point is not None:
        calibration.park()
    with calibration.step("let the net sales decide"):
        settled = panel_quantity(want, verbose) == qty
    if not settled:
        calibration.snap("price_field_disagrees")
        raise WrongItem(
            f"{want:,} was typed but the panel shows {shown:,}, and the net "
            f"sales are not {qty} x {want:,} either; the two must match "
            f"before anything is registered. Nothing has been listed.")
    if verbose:
        print(f"    the net sales are {qty} x {want:,}, so the field holds "
              f"what was typed and {shown:,} is a misread of it")


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


def register_boxes(seat=FIRST_SEAT):
    columns = _shop().get("register_columns")
    if not columns:
        return None
    y = int(_need(seat))
    return {name: (x0, y - columns["up"], x1 + 1, y + columns["down"] + 1)
            for name, (x0, x1) in columns["x"].items()}


def _button_word(text):
    for word in (RECEIPT_WORD, CHANGE_WORD, REGISTER_WORD):
        if calibration.button_word_matches(text, word):
            return word
    return None


def _paddle_button(image, seat):
    boxes = register_boxes(seat)
    if boxes is None or _reader["failed"]:
        return None
    return _backup_texts([_backup_line(image, boxes["button"])])


def _paddle_row(image, seat):
    boxes = register_boxes(seat)
    if boxes is None or _reader["failed"]:
        return None
    texts = _backup_texts([
        _backup_line(image, boxes["name"]),
        _backup_line(image, boxes["qty"]),
        calibration.isolate_digits(image, boxes["price"], BACKUP_SCALE),
        _backup_line(image, boxes["status"]),
        _backup_line(image, boxes["button"])])
    if texts is None:
        return None
    name, qty, price, status, button = (t or "" for t in texts)
    word = _button_word(button)
    if word == REGISTER_WORD:
        return REGISTER_WORD, word
    value = calibration._digits(price)
    return " ".join(part for part in (
        name.strip(" |"), re.sub(r"[^0-9]", "", qty),
        f"{value:,}" if value else "", status.strip(),
        word or button.strip()) if part), word


def _tesseract_button(image, seat):
    for text, _conf, _point in calibration.ocr(image, row_button_box(seat)):
        word = _button_word(text)
        if word is not None:
            return word
    return None


def row_button(image=None, seat=FIRST_SEAT):
    image = image if image is not None else calibration.grab()
    texts = _paddle_button(image, seat)
    if texts is not None and _button_word(texts[0]):
        return _button_word(texts[0])
    return _tesseract_button(image, seat)


def row_button_text(image=None, seat=FIRST_SEAT):
    image = image if image is not None else calibration.grab()
    texts = _paddle_button(image, seat)
    if texts is not None and texts[0]:
        return texts[0]
    return " ".join(t for t, _c, _p in
                    calibration.ocr(image, row_button_box(seat)))


def _function_in(text):
    key = _key(text)
    for word in (RECEIPT_WORD, CHANGE_WORD, REGISTER_WORD):
        if _key(word) in key:
            return word
    return None


def row_function(text=None, seat=FIRST_SEAT):
    seen = row_button(seat=seat)
    if seen is not None:
        return seen
    return _function_in(read_row(seat) if text is None else text)


def row_complete(text=None, seat=FIRST_SEAT):
    text = read_row(seat) if text is None else text
    return _key(STATUS_COMPLETE) in _key(text)


def _dialog_button_seen(word, image):
    known = remembered(word)
    if known is not None:
        return button_here(word, known, image)
    want = _key(word)
    return any(_key(t) == want
               for t, _c, _p in calibration.ocr(
                   image, calibration._box(calibration.DIALOG_BUTTONS_F)))


def dialog_buttons(image=None):
    image = image if image is not None else calibration.grab()
    words = (DISMISS_WORD, CONFIRM_WORD, RECEIPT_WORD)
    with ThreadPoolExecutor(max_workers=len(words)) as pool:
        found = list(pool.map(lambda word: _dialog_button_seen(word, image),
                              words))
    return [word for word, here in zip(words, found) if here]


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


def _read_row(seat=FIRST_SEAT):
    text = ""
    for attempt in range(PANEL_REREADS + 1):
        image = calibration.grab()
        box = row_box(seat)
        read = _paddle_row(image, seat)
        if read is not None and read[0]:
            return read[0], read[1], image
        if read is None and "rows" not in _told:
            _told.add("rows")
            why = _reader["failed"] or "the Register columns were not measured"
            print(f"  {why}; Register rows are read whole by Tesseract instead")
        text = trim_borders(calibration.read_line(image, box))
        if text.strip():
            return text, None, None
        text = trim_borders(_row_words(image, box))
        if text.strip():
            return text, None, None
        if attempt < PANEL_REREADS:
            time.sleep(PANEL_REREAD_GAP)
    return text, None, None


def read_row(seat=FIRST_SEAT):
    return _read_row(seat)[0]


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


def listings_left(after, seat=FIRST_SEAT):
    if after is None:
        return None
    text, action = after
    if action == REGISTER_WORD or row_is_empty(text or "", seat):
        return 0
    found = calibration._ROW_TEXT.match((text or "").strip())
    if found is None:
        return None
    digits = re.sub(r"[^0-9]", "", found.group("qty"))
    return int(digits) if digits else None


_START_COST = {}


def cost_basis(row):
    if row.buy_cost:
        return int(row.buy_cost)
    if row.name not in _START_COST:
        each = 0
        if calibration.voucher_floor_ratio(row.name)[1] > 0:
            each = int(calibration.price_floor(row.name)[0] or 0)
        else:
            slot = calibration.favourite_slot_of(row.name)
            pair = calibration.pair_slot(slot) if slot is not None else None
            if pair is not None:
                each = int(calibration.market_unit(
                    calibration.FAVOURITE_ITEMS[str(pair)]) or 0)
        _START_COST[row.name] = each
    return _START_COST[row.name]


def row_is_empty(text=None, seat=FIRST_SEAT):
    text = read_row(seat) if text is None else text
    return (not _key(text)) or _function_in(text) == REGISTER_WORD


def read_row_and_button(seat=FIRST_SEAT):
    if register_boxes(seat) is not None and not _reader["failed"]:
        seen, action, image = _read_row(seat)
        if image is not None:
            return seen, (action or _tesseract_button(image, seat)
                          or _function_in(seen))
        return seen, row_button(None, seat) or _function_in(seen)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(read_row, seat)
        pressing = pool.submit(row_button, None, seat)
        seen = reading.result()
        action = pressing.result()
    if action is None:
        action = _function_in(seen)
    return seen, action


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


SET_WORD = re.compile(r"\b%s\b" % re.escape(_FACTS["set_word"]),
                      re.IGNORECASE)


def is_set(name):
    return bool(name and SET_WORD.search(name))


def hard_floor_each(name):
    if not name:
        return 0
    table = calibration.load_shared()["run"].get("hard_min_per_unit") or {}
    key = item_key(name)
    return next((int(each) for item, each in table.items()
                 if item_key(item) == key), 0)


def _run_table_each(table_name, name):
    if not name:
        return 0
    table = calibration.load_shared()["run"].get(table_name) or {}
    key = item_key(name)
    return next((int(each) for item, each in table.items()
                 if item_key(item) == key), 0)


def never_buy_above(*names):
    caps = [c for c in (_run_table_each("never_buy_above", n) for n in names)
            if c]
    return min(caps) if caps else 0


def never_list_below(name):
    return _run_table_each("never_list_below", name)


def special_single(name, qty):
    conf = calibration.load_shared()["resupply"].get("special_row") or {}
    if not conf.get("enabled") or is_set(name) or pack_size(name) != 1:
        return False
    cores = {item_key(core) for core in conf.get("cores") or []}
    return item_key(name) in cores and int(qty) == int(conf.get("qty") or 1)


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

    def _keep_verified_cost(self, name, paid, market_each, verbose):
        if not within(paid, market_each):
            if verbose:
                print(f"    the {paid:,} a unit this resupply paid is not "
                      f"near the {market_each:,} the panel verified, so it "
                      f"is not kept as the floor for {name!r}")
            return
        if calibration.note_verified_cost(name, paid) and verbose:
            print(f"    {name!r} was bought at {paid:,} a unit and listed "
                  f"against a verified {market_each:,}; a row of it with no "
                  f"cost of its own is floored at {paid:,} from now on")

    def _hold_core_floor(self, want, each, whole, name, verbose):
        if not each or whole or want >= each:
            return want
        if verbose:
            print(f"    {want:,} is under the hard minimum of {each:,} a core "
                  f"for {name!r}; listing at {each:,} instead")
        return each

    def _price_again(self, market, expect_market, listed_at, verbose):
        for look in range(PRICE_RECHECKS):
            again = suggested_price(False, listed_at)
            if again is None:
                continue
            if verbose:
                print(f"    the panel said {market:,} where "
                      f"{int(expect_market):,} was expected; look "
                      f"{look + 2} reads {again:,}")
            if within(again, expect_market):
                return again
        return market

    def _resolve_loaded(self, market, expect_item, qty_seen, seen, verbose,
                        cost=0):
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
            if named is None and expect_item:
                named, sure = expect_item, True
                floor = calibration.price_floor(expect_item)[0]
                say(f"    {market:,} matches nothing else the run trades, so "
                    f"it goes out as {expect_item!r} at its own market")
            price = max(calibration.undercut(market), floor)
            if cost and price < cost:
                say(f"    {price:,} is under the {cost:,} the stock in tab "
                    f"{WORK_TAB} cost; it goes out at its own market all the "
                    f"same")
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

    def receive(self, index, verbose=True, complete=False, settle=False,
                listed=None):
        import get_alz
        saved = list(calibration._STEPS)
        calibration.steps_reset()
        started = time.perf_counter()
        try:
            listed = listed or self._slots.get(index)
            with calibration.step("read the balance before collecting"):
                before_alz = get_alz.read_balance()
            attempt = 0
            while True:
                try:
                    after = self._receive(index, verbose=verbose,
                                          complete=complete,
                                          again=attempt > 0)
                    break
                except GameSaysWait as refused:
                    with calibration.step("close and open the Agent Shop "
                                          "again"):
                        self.again_after_wait(
                            f"the collection on row {index}", attempt,
                            answer=refused.answer)
                    attempt += 1
            if after is None:
                with calibration.step("read the row again after collecting"):
                    if settle:
                        time.sleep(TAB_SETTLE)
                    if self.scroll_to(index, verbose=verbose)["moved"]:
                        time.sleep(TAB_SETTLE)
                    after = read_row_and_button(self._seat)
            with calibration.step("read the balance and book the sale"):
                self._book(index, listed, before_alz, after, complete,
                           verbose)
            if complete:
                total = (time.perf_counter() - started) * 1000
                inside = sum(ms for _label, ms in calibration._STEPS)
                calibration._STEPS.append(("not inside any step",
                                           total - inside))
                calibration.steps_table(f"collect row {index} in full")
        finally:
            calibration._STEPS[:] = saved
        return after

    def _receive(self, index, verbose=True, complete=False, again=False):
        if again:
            with calibration.step("scroll to the row again"):
                if self.scroll_to(index, verbose=verbose)["moved"]:
                    time.sleep(TAB_SETTLE)
            with calibration.step("read the row and its button again"):
                seen, action = read_row_and_button(self._seat)
            if action != RECEIPT_WORD:
                print(f"  row {index} reads {seen!r} with the Agent Shop open "
                      f"again; {RECEIPT_WORD} is no longer on it")
                if complete:
                    self._emptied(index, seen, action)
                return seen, action
        point = button_point(self._seat)
        if verbose:
            print(f"  {RECEIPT_WORD} at {point}")
        with calibration.step(f"click {RECEIPT_WORD} on the row"):
            calibration.click(*point)
        with calibration.step(f"find {RECEIPT_WORD} in the Confirm Receipt "
                              f"dialog"):
            accept = find_button(RECEIPT_WORD)
        if accept is None:
            raise Divergence(
                f"no Confirm Receipt dialog appeared after {RECEIPT_WORD} on "
                f"row {index}. Nothing has been collected.")
        if verbose:
            print(f"  Confirm Receipt: accepting at {accept}")
        with calibration.step(f"click {RECEIPT_WORD} in the Confirm Receipt "
                              f"dialog"):
            calibration.click(*accept)
        if not complete:
            calibration.park()
            if not dialog_gone():
                raise Divergence(
                    f"the Confirm Receipt dialog stayed open on row {index}. "
                    f"Whether the Alz was taken is unknown -- check by hand.")
            with calibration.step("look for 'please wait and try again'"):
                waiting = calibration.game_says_wait()
            if waiting:
                raise GameSaysWait(f"collecting row {index}")
            return None
        with calibration.step("let the collection settle"):
            calibration.park(settle=False)
            time.sleep(RECEIPT_SETTLE)
        with calibration.step("read the row and its button after collecting"):
            seen, action = read_row_and_button(self._seat)
        if action == RECEIPT_WORD:
            with calibration.step("look for 'please wait and try again'"):
                waiting = calibration.game_says_wait()
            print(f"  row {index} still reads {seen!r} after {RECEIPT_WORD}; "
                  f"nothing was collected")
            raise GameSaysWait(f"collecting row {index}",
                               answer=None if waiting else "did not take")
        self._emptied(index, seen, action)
        return seen, action

    def _emptied(self, index, seen, action):
        if action == REGISTER_WORD or row_is_empty(seen, self._seat):
            return
        calibration.snap(f"row_{index}_not_empty_after_collecting")
        raise Divergence(
            f"row {index} reads {seen!r} after collecting a sale that was "
            f"complete, so it is not marked empty.")

    def _book(self, index, listed, before_alz, after, complete, verbose=True):
        import get_alz
        if listed is None:
            return
        price = int(listed.price or 0)
        if not price:
            return
        after_alz = get_alz.read_balance() if before_alz is not None else None
        proceeds = (after_alz - before_alz
                    if after_alz is not None and before_alz is not None
                    else None)
        left = 0 if complete else listings_left(after, self._seat)
        if left is not None and 0 <= left < listed.qty:
            sold = listed.qty - left
        elif proceeds and proceeds > 0:
            sold = max(1, min(listed.qty, proceeds // price))
            print(f"  row {index}'s count after collecting did not read; "
                  f"{sold} of {listed.qty} booked from the {proceeds:,} Alz "
                  f"the balance moved")
        else:
            print(f"  row {index}: neither the row count nor the balance says "
                  f"how many sold; nothing is booked")
            return
        if (proceeds and proceeds > 0 and proceeds % price == 0
                and proceeds // price != sold):
            print(f"  row {index}: the balance moved {proceeds:,} Alz, exactly "
                  f"{proceeds // price} x {price:,}, and the row count makes it "
                  f"{sold}; the sale is booked from the balance")
            sold = proceeds // price
        revenue = sold * price
        if proceeds is not None and abs(proceeds - revenue) > price:
            print(f"  row {index}: the balance moved {proceeds:,} Alz and the "
                  f"row count makes it {sold} x {price:,} = {revenue:,}; the "
                  f"sale is booked from the row count")
        held = listed.units // listed.qty if listed.qty else 0
        if held <= 1 and is_set(listed.name):
            each = calibration.market_unit(listed.name)
            held = round(price / each) if each else 1
            if held < 1 or not (each and abs(price - each * held)
                                <= each * (PRICE_CHECK_FACTOR - 1)):
                held = pack_size(listed.name)
        if held < 1:
            held = 1
        units = sold * held
        each_sold = revenue // units
        basis = cost_basis(listed)
        cost = basis * units if basis else revenue
        ledger.sold(listed.name, each_sold, revenue, units, cost=cost)
        print(f"  booked {sold} x {listed.name!r} at {price:,} = {revenue:,}"
              + (f", {held} to a listing, {units:,} unit(s) at {each_sold:,}"
                 if held > 1 else "")
              + f"; cost {cost:,}"
              + (f" at {basis:,} each" if basis else ", not an item the run "
                 f"prices, so no profit is counted")
              + f"; profit {revenue - cost:+,}"
              + (f", {(revenue - cost) // units:+,} a unit" if basis else ""))

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

    def again_after_wait(self, what, attempt, verbose=True, answer=None):
        calibration.snap("game_says_wait")
        said = answer or "answered 'please wait and try again' to"
        if attempt >= GAME_WAIT_RETRIES:
            raise GameSaysWait(
                f"the game {said} {what} {attempt + 1} time(s); nothing more "
                f"is tried.")
        if verbose:
            print(f"  the game {said} {what}; closing the Agent Shop and doing "
                  f"it again ({attempt + 1} of {GAME_WAIT_RETRIES})")
        self.reopen_after_wait(verbose=verbose)

    def cancel(self, index, verbose=True, tab_ready=False, tab_selected=False,
               read=None, overlap=False):
        attempt = 0
        while True:
            try:
                return self._cancel(index, verbose=verbose,
                                    tab_ready=tab_ready and not attempt,
                                    tab_selected=tab_selected and not attempt,
                                    read=None if attempt else read,
                                    overlap=overlap)
            except GameSaysWait:
                self.again_after_wait(f"cancelling row {index}", attempt,
                                      verbose=verbose)
                attempt += 1

    def _cancel(self, index, verbose=True, tab_ready=False,
                tab_selected=False, read=None, overlap=False):
        index = int(index)
        expected = self._slots.get(index)
        if expected is None:
            raise ValueError(f"row {index} is empty in the model; refusing to "
                             f"cancel a slot nothing is listed in")
        with calibration.step("scroll to the row again"):
            moved = self.scroll_to(index, verbose=verbose)["moved"]
            if moved:
                time.sleep(TAB_SETTLE)

        seat = self._seat
        position = seat_position(seat)
        if read is not None and not moved:
            seen, action = read
        else:
            with calibration.step("read the row and its button again"):
                seen, action = read_row_and_button(seat)
        if action == RECEIPT_WORD:
            complete = row_complete(seen, seat)
            if verbose:
                print(f"  row {index} has SOLD "
                      f"({'fully' if complete else 'partially'}); collecting "
                      f"before anything else")
            seen, action = self.receive(index, verbose=verbose,
                                        complete=complete, settle=True)
            if complete or action == REGISTER_WORD:
                if verbose:
                    print(f"  row {index} is empty after the collection; "
                          f"nothing left to cancel")
                return self.note_cancel(index)
        if action == REGISTER_WORD:
            raise Divergence(
                f"row {index} is empty on screen; nothing to cancel.")
        checking = time.perf_counter()
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
        calibration._STEPS.append(("check the row is the one the run listed",
                                   (time.perf_counter() - checking) * 1000))

        if not tab_selected:
            with calibration.step(f"select inventory tab {WORK_TAB} before "
                                  f"{CHANGE_WORD}"):
                show_work_tab(verbose=verbose, already=tab_ready)

        point = button_point(seat)
        if verbose:
            print(f"  {CHANGE_WORD} at {point}")
        with calibration.step(f"click {CHANGE_WORD} and park"):
            calibration.park(settle=False)
            calibration.click(*point, settle=0.0)
            calibration.park(settle=False)
            lagging = calibration.server_busy()

        with calibration.step(f"find {DISMISS_WORD}"):
            dismiss = find_button(DISMISS_WORD, hover=overlap)
        if dismiss is None:
            if lagging or calibration.server_busy():
                stalls = _CHANGE_STALLS[index] = _CHANGE_STALLS.get(index, 0) + 1
                if stalls <= CHANGE_RETRIES:
                    if not calibration.wait_out_server_lag(verbose=verbose):
                        calibration.table_lost()
                        calibration._recovered()
                    raise calibration.ServerStalled(
                        f"the server lagged after {CHANGE_WORD} on row {index} "
                        f"and no {DISMISS_WORD} came; nothing was cancelled "
                        f"and the shop is shut. The pass starts again at row "
                        f"{index}, which clicks {CHANGE_WORD} once more if it "
                        f"is still on sale.")
            raise Divergence(
                f"no {DISMISS_WORD} button appeared after clicking "
                f"{CHANGE_WORD} on row {index}. Nothing has been cancelled.")
        _CHANGE_STALLS.pop(index, None)
        if verbose:
            print(f"  {DISMISS_WORD} at {dismiss}")
        with calibration.step(f"click {DISMISS_WORD}"):
            calibration.click(*dismiss, settle=0.0, hovered=overlap)

        with calibration.step(f"find {CONFIRM_WORD}"):
            confirm = find_button(CONFIRM_WORD, hover=overlap)
        if confirm is None:
            raise Divergence(
                f"no {CONFIRM_WORD} button appeared after {DISMISS_WORD} on "
                f"row {index}. The dialog is still open; nothing committed.")
        if verbose:
            print(f"  {CONFIRM_WORD} at {confirm}")
        with calibration.step(f"click {CONFIRM_WORD} and park"):
            calibration.click(*confirm, settle=0.0, hovered=overlap)
            calibration.park(settle=False)

        with calibration.step("wait for the dialog to close"):
            gone = dialog_gone()
        if not gone:
            raise Divergence(
                f"the dialog stayed open after {CONFIRM_WORD} on row {index}. "
                f"Whether the cancel committed is unknown -- check by hand.")
        landing = self.next_work_slot()
        with calibration.step(f"wait for the item to land in tab {WORK_TAB}"):
            refused = landing is not None and game_refused(
                lambda image: not calibration.slot_is_empty(image, *landing))
        if refused:
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
                   under=None, floor_item=None, floor_units=0,
                   fallback=None,
                   resupply_cost=0, overlap=False, rule_item=None,
                   special=False):
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
            loading = calibration.grab()
            standing = (panel_standing() if panel_holds_item(loading)
                        else None)
        if standing is not None:
            raise Divergence(
                f"the shop slot already holds something the panel prices at "
                f"{standing:,}; clear it before listing another item.")

        deadline = time.monotonic() + DIALOG_TIMEOUT
        filled, lagged = not wait_fill, 0
        shared = loading if overlap else None
        while not filled:
            while time.monotonic() < deadline:
                with calibration.step(f"wait for slot ({row},{col}) to fill"):
                    image = shared if shared is not None else calibration.grab()
                    shared = None
                    filled = not calibration.slot_is_empty(image, int(row),
                                                           int(col))
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
                calibration.ctrl_click(*point, check_hovering=overlap)
            with calibration.step("read the suggested price"):
                suggested = suggested_price(verbose, listed_at,
                                            overlap=overlap)
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
        elif suggested is None and fallback:
            suggested = int(fallback)
            price = suggested
            if verbose:
                print(f"  no market price read after {PRICE_ATTEMPTS} "
                      f"ctrl-click(s), so nothing else is listed to follow; "
                      f"listing at the first-listing price {suggested:,}")
        if suggested is None:
            raise NothingLoaded(
                f"nothing loaded into the shop slot from ({row},{col}) after "
                f"{PRICE_ATTEMPTS} ctrl-click(s). Nothing has been listed.")
        meant = expect_item
        if not expect_market and expect_item:
            anchor = market_anchor(expect_item)
            if anchor:
                expect_market = anchor * pack_size(expect_item)
                if verbose:
                    print(f"    nothing said what {expect_item!r} should "
                          f"fetch; its own market says {expect_market:,}")
        hard_name = meant or floor_item
        hard_each = hard_floor_each(hard_name)
        hard_set = is_set(hard_name)
        crafted = (round(int(expect_market) / int(unit_market))
                   if expect_market and unit_market else 0)
        hard_total = max(pack_size(hard_name) if hard_name else 0, crafted,
                         int(floor_units or 0))
        cost_each = int(floor_each or 0) if crafted else 0
        cost_guard = cost_each * max(1, crafted)
        if market and expect_market and not within(market, expect_market):
            with calibration.step("read the price again against what was "
                                  "expected"):
                market = self._price_again(market, expect_market, listed_at,
                                           verbose)
            suggested = market
        resolved = None
        if market and expect_market and not within(market, expect_market):
            seen = (f"the panel prices what loaded from ({row},{col}) at "
                    f"{market:,}, and {expect_item!r} sells near "
                    f"{int(expect_market):,}")
            if not resolve:
                if verbose:
                    print(f"    {seen}; listing what loaded at its own "
                          f"market all the same")
            else:
                resolved = self._resolve_loaded(market, expect_item, None,
                                                seen, verbose, cost_guard)
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
        want = self._hold_core_floor(want, hard_each, hard_set, hard_name,
                                     verbose)
        priced = calibration.at_least_market(want, market, under)
        if priced != want:
            if verbose:
                print(f"    {want:,} is under the {market:,} the panel prices "
                      f"what loaded at; every listing follows its own "
                      f"market, so typing {priced:,}")
            want = priced
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
            calibration.click(*panel["qty_point"], settle=FIELD_SETTLE,
                              check_hovering=overlap, alongside=overlap)
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
                                            verbose, cost_guard)
            want, floor, expect_item = (resolved["price"], resolved["floor"],
                                        resolved["item"])
            want = self._hold_core_floor(want, hard_each, hard_set,
                                         hard_name, verbose)
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
        if (cost_guard and want * qty < cost_guard
                and (resolved is None or resolved["item"] in (None, meant))):
            raise WrongItem(
                f"refusing to list {meant!r}: {qty} x {want:,} is "
                f"{want * qty:,}, under the {cost_guard:,} that went into "
                f"it. Nothing has been listed.")
        set_floor = (hard_each * hard_total
                     if hard_set and hard_total > 1 else 0)
        if set_floor and want * qty < set_floor:
            need = -(-set_floor // qty)
            if verbose:
                print(f"    {qty} x {want:,} is {want * qty:,}, under the "
                      f"hard minimum of {hard_each:,} a unit x {hard_total} "
                      f"= {set_floor:,}; listing at {need:,} each instead")
            with calibration.step(f"type the price {need:,} for the floor"):
                calibration.click(*panel["price_point"], settle=FIELD_SETTLE)
                type_number(need, CLEAR_PRESSES_PRICE)
                calibration.park()
            with calibration.step("take the quantity from the net sales "
                                  "again"):
                qty = panel_quantity(need, verbose)
            if qty is None:
                calibration.snap("panel_will_not_confirm")
                raise Divergence(
                    f"the panel will not price {need:,} against its net sales "
                    f"after raising it to the hard minimum. Nothing has been "
                    f"listed.")
            want = need
        named = expect_item or meant or floor_item or rule_item
        lowest = never_list_below(named)
        if lowest and not (special and special_single(named, qty)):
            same = item_key(named) == item_key(hard_name)
            each = max(pack_size(named), int(count or 0),
                       hard_total if same else 0,
                       bundle_of(suggested, named) if is_set(named) else 0, 1)
            need = lowest * each
            if want < need:
                if verbose:
                    print(f"    {want:,} is under the {lowest:,} a unit "
                          f"{named!r} is never listed below"
                          + (f", x {each} in each" if each > 1 else "")
                          + f"; listing at {need:,} instead")
                with calibration.step(f"type the price {need:,} for the "
                                      f"never-below rule"):
                    calibration.click(*panel["price_point"],
                                      settle=FIELD_SETTLE)
                    type_number(need, CLEAR_PRESSES_PRICE)
                    calibration.park()
                with calibration.step("take the quantity from the net sales "
                                      "again"):
                    again = panel_quantity(need, verbose)
                if again is None:
                    calibration.snap("panel_will_not_confirm")
                    raise Divergence(
                        f"the panel will not price {need:,} against its net "
                        f"sales after raising it to the {lowest:,} a unit "
                        f"{named!r} is never listed below. Nothing has been "
                        f"listed.")
                qty, want = again, need
        with calibration.step("read the price back before Register"):
            check_price_field(want, qty, verbose,
                              next_point=(panel["register_button"]
                                          if overlap else None))
        with calibration.step("click Register"):
            calibration.click(*panel["register_button"], settle=0.0,
                              hovered=overlap)
        with calibration.step(f"find {CONFIRM_WORD}"):
            confirm = find_button(CONFIRM_WORD, hover=overlap)
        if confirm is None:
            raise Divergence(
                f"no {CONFIRM_WORD} appeared after Register. Nothing "
                f"committed.")

        with calibration.step("look for the underprice question"):
            warned = underprice_warning()
        if warned:
            calibration.snap("underprice_warning")
            if (expect_market and not within(want, expect_market)
                    and (resolved is None
                         or resolved["item"] in (None, meant))):
                with calibration.step(f"click {DISMISS_WORD} on the question"):
                    refuse = find_button(DISMISS_WORD)
                    if refuse is not None:
                        calibration.click(*refuse, settle=0.0)
                        calibration.park()
                raise WrongItem(
                    f"the game says {want:,} is far under its own average, "
                    f"and {meant!r} was expected to sell near "
                    f"{int(expect_market):,}; the question was declined. "
                    f"Nothing has been listed.")
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
            with calibration.step("park before the dialog takes another "
                                  "click at the same point"):
                calibration.park()
                time.sleep(ACTION_GAP)
            with calibration.step(f"find {CONFIRM_WORD} on the real dialog"):
                confirm = find_button(CONFIRM_WORD)
            if confirm is None:
                calibration.snap("no_confirm_after_warning")
                raise Divergence(
                    f"no {CONFIRM_WORD} appeared after the underprice "
                    f"question was accepted. Nothing committed.")
        with calibration.step(f"click {CONFIRM_WORD}"):
            calibration.click(*confirm, settle=0.0, hovered=overlap)
        with calibration.step("park"):
            calibration.park(settle=False)
        with calibration.step("confirm the dialog is gone"):
            gone = dialog_gone()
        if not gone:
            calibration.snap("dialog_stays_after_confirmation")
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
        if (resupply_cost and market and hard_name
                and (resolved is None or resolved["item"] == meant)):
            self._keep_verified_cost(hard_name, int(resupply_cost),
                                     (market * qty // hard_total
                                      if hard_set and hard_total > 1
                                      else market), verbose)
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
        calibration.take_table_lost()
        self._top = 1
        self._seat = FIRST_SEAT
        if verbose:
            print(f"  scrolled to the top; row 1 is at position 1")
        return 1

    def bottom(self, verbose=True):
        wheel(HOME_NOTCHES, verbose=False)
        time.sleep(ACTION_GAP)
        calibration.take_table_lost()
        self._top = MAX_TOP
        self._seat = LAST_SEAT
        if verbose:
            print(f"  scrolled to the bottom; row {CAPACITY} is at position "
                  f"{VISIBLE}")
        return MAX_TOP

    def scroll_to(self, index, verbose=True):
        if calibration.take_table_lost() and self._top is not None:
            print(f"  the shop was shut or the game stalled since the table "
                  f"was last scrolled; scrolling all the way first, then to "
                  f"row {index}")
            self._top = None
        seat, _want = self.seat_of(index)
        jumped = False
        if seat == LAST_SEAT and (self._seat != LAST_SEAT
                                  or self._top is None):
            self.bottom(verbose=verbose)
            jumped = True
        elif self._top is None:
            self.home(verbose=verbose)
            jumped = True
        plan = self.scroll_plan(index)
        if plan["notches"]:
            wheel(plan["notches"], verbose=verbose)
        self.note_scrolled(plan["to_top"])
        self._seat = seat
        if verbose:
            print(f"  row {index} is now at position {plan['position']}")
        return dict(plan, moved=jumped or bool(plan["notches"]))

    def read(self):
        return read_row(self._seat)

    def read_with_button(self):
        return read_row_and_button(self._seat)

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
