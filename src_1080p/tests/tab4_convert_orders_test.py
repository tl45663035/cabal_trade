import contextlib
import io
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_tab4_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
from PIL import Image
import calibration
import convert
import driver
import buy
import get_alz
import row_model

FX = HERE / "fixtures"
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


def old_empty(image, row, col):
    p = calibration.inventory_slot_point(row, col)
    h = calibration.slot_half()
    d = list(image.crop((p[0] - h, p[1] - h, p[0] + h, p[1] + h)).convert("L").getdata())
    m = sum(d) / len(d)
    return (sum((v - m) ** 2 for v in d) / len(d)) ** 0.5 < calibration.SLOT_OCCUPIED_STDEV


def old_occupied(image):
    return {(r, c) for r in range(1, 9) for c in range(1, 9) if not old_empty(image, r, c)}


def frame(name):
    return Image.open(FX / f"fch_{name}").convert("RGB")


def run_frame(run, name):
    return Image.open(FX / f"{run[:17]}_{name}").convert("RGB")


glow_cases = [("2026-09-26_161255_run", name, slot) for name, slot in (
    ("00386_click_500_258.png", (1, 2)), ("00394_vendor_open_attempt_1.png", (2, 1)),
    ("00524_click_1613_181.png", (1, 2)), ("00527_click_112_425.png", (1, 2)),
    ("00769_click_1613_181.png", (1, 2)), ("00779_click_965_677.png", (1, 2)))]
for run, name, slot in glow_cases:
    im = run_frame(run, name)
    check(f"glow or sparkle on empty {slot} in {run[:17]} {name[:5]}: old detector says occupied, new says empty",
          old_empty(im, *slot) is False and calibration.slot_is_empty(im, *slot) is True)
for name in ("00395_click_143_158.png", "00519_click_1613_181.png", "00776_altclick_328_839.png",
             "00792_vendor_open_attempt_1.png"):
    im = run_frame("2026-09-26_161255_run", name)
    check(f"fainter glow on empty (1, 2) in {name[:5]}: new detector says empty",
          calibration.slot_is_empty(im, 1, 2) is True)
for name in ("00506_click_131_754.png", "01511_click_112_425.png", "01513_click_131_754.png"):
    white = run_frame("2026-09-26_161255_run", name)
    check(f"a glow-washed white item in (1,1) ({name[:5]}) reads occupied",
          not calibration.slot_is_empty(white, 1, 1))

before_img = frame("02448_click_1613_181.png")
after_img = frame("02452_click_1613_181.png")
check("frame 02448 (Set bought, before OK) reads only (1,1)", calibration.occupied_slots(before_img) == {(1, 1)},
      str(sorted(calibration.occupied_slots(before_img))))
check("frame 02452 (after OK) reads only (1,2), the Force Core (High)",
      calibration.occupied_slots(after_img) == {(1, 2)}, str(sorted(calibration.occupied_slots(after_img))))

peak = before_img.copy()
donor = run_frame("2026-09-26_161255_run", "00527_click_112_425.png")
p = calibration.inventory_slot_point(1, 2)
h = calibration.slot_half() + 4
peak.paste(donor.crop((p[0] - h, p[1] - h, p[0] + h, p[1] + h)), (p[0] - h, p[1] - h))
check("02448 with the brightest recorded glow pasted into (1,2): the old detector counts (1,2), as at 17:17",
      old_occupied(peak) == {(1, 1), (1, 2)}, str(sorted(old_occupied(peak))))
check("the same picture through the new detector: only (1,1)", calibration.occupied_slots(peak) == {(1, 1)})

dialog_img = frame("02447_click_905_456.png")
ok_img = frame("02449_click_965_677.png")


def run_convert(before_picture, detector):
    state = {"stage": "dialog"}

    def grab(*a, **k):
        return {"dialog": dialog_img, "tab4": before_picture, "ok": ok_img, "after": after_img}[state["stage"]]

    tab_point = tuple(calibration.inventory_tab_point(calibration.CONVERT_INVENTORY_TAB))

    def click(x, y, settle=None):
        if (x, y) == tab_point:
            state["stage"] = "tab4"
        elif state["stage"] in ("tab4", "ok"):
            state["stage"] = "after"

    saved = (calibration.grab, calibration.click, calibration.alt_click, calibration.park,
             row_model.type_number, time.sleep, calibration.occupied_slots)
    real_occupied = calibration.occupied_slots
    calibration.grab, calibration.click = grab, click
    calibration.alt_click = lambda *a, **k: None
    calibration.park = lambda *a, **k: None
    row_model.type_number = lambda *a, **k: None
    time.sleep = lambda s: None
    real_dialog_open = convert.dialog_open

    def dialog_open(image=None):
        if state["stage"] == "tab4":
            state["stage"] = "ok"
            return True
        return real_dialog_open(image)
    convert.dialog_open = dialog_open
    if detector == "old":
        calibration.occupied_slots = lambda image=None: old_occupied(image if image is not None else grab())
    else:
        calibration.occupied_slots = lambda image=None: real_occupied(image if image is not None else grab())
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            result = convert.convert("Force Core(High)")
        return result, out.getvalue()
    except convert.Refused as exc:
        return exc, out.getvalue()
    finally:
        (calibration.grab, calibration.click, calibration.alt_click, calibration.park,
         row_model.type_number, time.sleep, calibration.occupied_slots) = saved
        convert.dialog_open = real_dialog_open


res, said = run_convert(peak, "old")
check("17:17 replayed with the old detector: 'Nothing converted'",
      isinstance(res, convert.Refused) and "Nothing converted" in str(res), str(res)[:90])
res, said = run_convert(peak, "new")
check("17:17 replayed with the new detector: the core is found in (1,2)",
      isinstance(res, dict) and res["slots"] == [(1, 2)], str(res)[:120])


class FakeModel:
    def __init__(self):
        self.placed, self.held, self.listed_from = {}, [], []

    def empty(self):
        return [i for i in range(1, 31) if i not in self.placed]

    def place(self, index, row):
        self.placed[index] = row

    def hold_work(self, slot, what=None):
        self.held.append((slot, what))

    def list_slot(self, row, col, **kw):
        self.listed_from.append(((row, col), kw.get("expect_item")))
        return {"item": kw.get("expect_item"), "qty": 1, "price": 185929, "resolved": False}


def job_at_convert(rounds, max_rounds):
    return {"slot": 7, "core": "Force Core(High)", "set": "Force Core Set (High)", "pair": 8,
            "diff": 5750, "landing": (1, 1), "want_min": 180, "want_max": 500, "room": 3000,
            "sells_at": 185930, "gap": 5000, "step": "convert", "orders": 1, "bought": 1,
            "paid": 180180, "floor": 180180, "why": "a Force Core Set (High) cost 180,180 this pass",
            "max_rounds": max_rounds, "rounds": rounds, "slots": [], "filled": 0, "rows": [], "listed": 0}


PATCHED = [(calibration, "close_everything"), (convert, "open_vendor"), (convert, "convert"),
           (driver, "back_to_the_shop"), (driver, "register_tab"), (calibration, "click"),
           (calibration, "park"), (calibration, "occupied_slots"), (time, "sleep"),
           (driver, "note_step"), (driver, "task_done"), (calibration, "phases_table")]


def run_finish(job, tab4, convert_raises=None):
    events = []
    saved = [(mod, name, getattr(mod, name)) for mod, name in PATCHED]
    calibration.close_everything = lambda *a, **k: events.append("close")
    convert.open_vendor = lambda *a, **k: events.append("vendor")

    def fake_convert(core, verbose=True):
        events.append("convert")
        if convert_raises:
            raise convert_raises
        return {"slots": [(1, 2)]}
    convert.convert = fake_convert
    driver.back_to_the_shop = lambda *a, **k: True
    driver.register_tab = lambda *a, **k: None
    calibration.click = lambda *a, **k: None
    calibration.park = lambda *a, **k: None
    calibration.occupied_slots = lambda image=None: events.append("read tab 4") or set(tab4)
    time.sleep = lambda s: None
    driver.note_step = lambda *a, **k: None
    driver.task_done = lambda *a, **k: events.append("done")
    calibration.phases_table = lambda *a, **k: None
    model = FakeModel()
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            result = driver.finish_resupply(model, job, 1, 30)
    finally:
        for mod, name, value in saved:
            setattr(mod, name, value)
    return result, model, events, out.getvalue()


job = job_at_convert(1, 2)
res, model, events, said = run_finish(
    job, {(1, 2)}, convert.NoOffer("no Purchase Item dialog appeared after Alt+click; nothing confirmed."))
check("pass 10 replayed: no dialog, so tab 4 is read and the Force Core (High) in (1,2) is listed",
      model.listed_from == [((1, 2), "Force Core(High)")] and job["listed"] == 1 and "done" in events,
      str(model.listed_from))

job = job_at_convert(2, 2)
res, model, events, said = run_finish(job, {(1, 2)})
check("pass 11 replayed: out of rounds, tab 4 is read and listed before the job ends, no conversion tried",
      model.listed_from == [((1, 2), "Force Core(High)")] and "convert" not in events and "done" in events,
      str(events))

job = job_at_convert(2, 2)
res, model, events, said = run_finish(job, set())
check("out of rounds and tab 4 empty: nothing listed, and the message says tab 4 holds nothing more",
      model.listed_from == [] and "holds nothing more to list" in said and events.count("read tab 4") == 1,
      said[-160:])

job = job_at_convert(1, 2)
res, model, events, said = run_finish(job, {(1, 2)})
check("a normal round still converts and lists the slot convert reported, with no extra tab read",
      model.listed_from == [((1, 2), "Force Core(High)")] and "read tab 4" not in events, str(events))

seq_img = run_frame("2026-09-26_184309_run", "01053_click_889_510.png")
buy.dialog_details(seq_img)
t = time.perf_counter()
together = buy.dialog_details(seq_img)
t_together = (time.perf_counter() - t) * 1000
t = time.perf_counter()
one_by_one = {"item": calibration.read_line(seq_img, buy._reg("buy_dialog_item")),
              "price": calibration.read_money(seq_img, buy._reg("buy_dialog_price")),
              "qty": calibration.read_money(seq_img, buy._reg("buy_dialog_qty")),
              "qty_max": calibration.read_money(seq_img, buy._reg("buy_dialog_qty_max"))}
t_seq = (time.perf_counter() - t) * 1000
check("the Purchase dialog's four fields read together give the same values as one by one",
      together == one_by_one and together["price"] is not None,
      f"{together}, together {t_together:.0f} ms, one by one {t_seq:.0f} ms")

calls = []
saved = (buy.show_work_tab, get_alz.read_balance, buy.get_price.get_price)
buy.show_work_tab = lambda: calls.append("tab 4")
get_alz.read_balance = lambda: calls.append("balance") or 1000
buy.get_price.get_price = lambda *a, **k: {"name": "Chaos Core", "qty": 5, "price": 729375,
                                           "unit_price": 729375}
try:
    for given, want in ((None, ["tab 4", "balance"]), (500, [])):
        calls.clear()
        seen = ""
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                buy._buy_row_one(3, 10, balance=given)
        except buy.Broke as exc:
            seen = str(exc)
        held = given if given else 1000
        check(f"balance {'not known' if given is None else 'carried over'}: tab 4 click and balance read = {want}",
              calls == want and f"Alz {held:,} held" in seen, f"{calls} {seen[:40]}")
finally:
    buy.show_work_tab, get_alz.read_balance, buy.get_price.get_price = saved

orders = []
saved = buy.buy_row_one
balances = iter([900_000_000, 850_000_000, 800_000_000])


def fake_buy(slot, want, **kw):
    orders.append(kw.get("balance"))
    return {"bought": 50, "spent": 50 * 729375, "balance": next(balances), "balance_seen": True,
            "packs": 50, "unit_price": 729375, "price": 729375}


buy.buy_row_one = fake_buy
job = {"core": "Chaos Core", "slot": 3, "target": 150, "want_max": 999, "sells_at": 743996, "gap": 5000,
       "leave": 1, "steps_max": 10, "take_all": 10, "orders": 0, "bought": 0, "paid": 0, "balance": 123}
try:
    with contextlib.redirect_stdout(io.StringIO()):
        driver.buy_cores(job)
finally:
    buy.buy_row_one = saved
check("three Chaos orders: the first reads the balance, the next two carry the last order's balance",
      orders == [None, 900_000_000, 850_000_000], str(orders))

m = row_model
cancel_events = []
saved = (m.read_row, m.row_button, m.show_work_tab, m.find_button, m.dialog_gone, m.game_refused,
         m.inv._user32, calibration.click, calibration.park, m.time.sleep)
m.read_row = lambda seat=None: "Force Core (Ultimate) 49 399,553 On Sale Change"
m.row_button = lambda image=None, seat=None: m.CHANGE_WORD
m.show_work_tab = lambda verbose=False, already=False: cancel_events.append("tab 4")
m.find_button = lambda word, timeout=None, verbose=False: cancel_events.append(f"find {word}") or (1, 1)
m.dialog_gone = lambda timeout=None: cancel_events.append("dialog gone") or True
m.game_refused = lambda done, timeout=None: cancel_events.append("landed") or False
m.inv._user32 = type("Hover", (), {"SetCursorPos": staticmethod(lambda *a: cancel_events.append("hover"))})()
calibration.click = lambda x, y, settle=None: cancel_events.append("click")
calibration.park = lambda settle=True: cancel_events.append(f"park{'' if settle else ' without the pause'}")
m.time.sleep = lambda s: None
try:
    fake = m.RowModel().seed({})
    fake._slots[5] = m.Row("Force Core (Ultimate", qty=49, price=399553)
    fake.scroll_to = lambda index, verbose=True: {"moved": False}
    fake._seat = m.FIRST_SEAT
    calibration.steps_reset()
    with contextlib.redirect_stdout(io.StringIO()):
        fake._cancel(5, verbose=False, tab_ready=True)
    labels = [s for s, _ms in calibration._STEPS]
finally:
    (m.read_row, m.row_button, m.show_work_tab, m.find_button, m.dialog_gone, m.game_refused,
     m.inv._user32, calibration.click, calibration.park, m.time.sleep) = saved
check("the cancel times every part in its own step, in the order the actions happen",
      labels == ["scroll to the row again", "read the row and its button again",
                 "check the row is the one the run listed", f"select inventory tab {m.WORK_TAB} before {m.CHANGE_WORD}",
                 f"hover over {m.CHANGE_WORD} and click it", f"find {m.DISMISS_WORD}", f"click {m.DISMISS_WORD}",
                 f"find {m.CONFIRM_WORD}", f"click {m.CONFIRM_WORD} and park", "wait for the dialog to close",
                 f"wait for the item to land in tab {m.WORK_TAB}"], str(labels))
check("and it still does the same actions in the same order, parking without the pause",
      cancel_events == ["tab 4", "hover", "click", f"find {m.DISMISS_WORD}", "click", f"find {m.CONFIRM_WORD}",
                        "click", "park without the pause", "dialog gone", "landed"], str(cancel_events))

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
