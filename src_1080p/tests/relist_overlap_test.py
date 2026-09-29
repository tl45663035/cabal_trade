import contextlib
import ctypes
import io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_relist_overlap_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
from PIL import Image
import calibration
import driver
import open_inventory
import row_model as m

fails = 0
HOVER = calibration.HOVER_SETTLE
DOWN = calibration._S["input"]["MOUSEEVENTF_LEFTDOWN"]
UP = calibration._S["input"]["MOUSEEVENTF_LEFTUP"]
MOUSE_KIND = calibration._S["input"]["INPUT_MOUSE"]


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


class Mouse:
    def __init__(self):
        self.at = (0, 0)
        self.log = []

    def SetCursorPos(self, x, y):
        self.at = (int(x), int(y))
        self.log.append(("move", self.at, time.monotonic()))
        return 1

    def MapVirtualKeyW(self, vk, kind):
        return 0

    def SendInput(self, n, ref, size):
        sent = ref._obj
        if sent.type == MOUSE_KIND:
            flags = sent.u.mi.dwFlags
            kind = "down" if flags == DOWN else "up" if flags == UP else f"mouse {flags}"
        else:
            kind = "key"
        self.log.append((kind, self.at, time.monotonic()))
        return 1

    def first(self, kind):
        return next((e for e in self.log if e[0] == kind), None)

    def kinds(self):
        return [e[0] for e in self.log]


mouse = Mouse()
guard = (open_inventory._user32, ctypes.windll.user32.SetCursorPos, calibration.cursor,
         calibration.hold_if_busy, calibration.snap)
open_inventory._user32 = mouse
ctypes.windll.user32.SetCursorPos = mouse.SetCursorPos
calibration.cursor = lambda: mouse.at
checks_at = []
calibration.hold_if_busy = lambda: checks_at.append(time.monotonic()) or time.sleep(0.03)
calibration.snap = lambda *a, **k: None


def fresh():
    mouse.log.clear()
    checks_at.clear()
    calibration._HOVERED["point"] = None


try:
    fresh()
    calibration.click(100, 200, settle=0.0)
    moved, down = mouse.first("move"), mouse.first("down")
    check("a plain click still checks the server first, then moves, then settles a full hover before pressing",
          mouse.kinds() == ["move", "down", "up"] and checks_at and checks_at[0] < moved[2]
          and down[2] - moved[2] >= HOVER, f"{mouse.kinds()}, hover {(down[2] - moved[2]) * 1000:.0f} ms")

    fresh()
    calibration.click(100, 200, settle=0.0, check_hovering=True)
    moved, down = mouse.first("move"), mouse.first("down")
    check("with the check while hovering, the cursor moves first and the check runs inside the hover",
          mouse.kinds() == ["move", "down", "up"] and len(checks_at) == 1
          and moved[2] <= checks_at[0] < down[2] and down[2] - moved[2] >= HOVER,
          f"hover {(down[2] - moved[2]) * 1000:.0f} ms against {HOVER * 1000:.0f}")
    check("and the press does not wait the check on top of the hover",
          down[2] - moved[2] < HOVER + 0.03 * 0.9, f"{(down[2] - moved[2]) * 1000:.0f} ms")

    fresh()
    calibration.hover(300, 400)
    time.sleep(HOVER)
    mouse.log.clear()
    calibration.click(300, 400, settle=0.0, hovered=True)
    down = mouse.first("down")
    check("a click on a point the cursor has hovered long enough presses right after its server check, without moving",
          mouse.kinds() == ["down", "up"] and down[2] - checks_at[-1] < HOVER / 3,
          f"{mouse.kinds()}, {(down[2] - checks_at[-1]) * 1000:.0f} ms after the check")

    fresh()
    arrived = calibration.hover(300, 400)
    calibration.click(300, 400, settle=0.0, hovered=True)
    down = mouse.first("down")
    check("a click right after the cursor arrived still waits out the rest of the hover",
          down[2] - arrived >= HOVER, f"{(down[2] - arrived) * 1000:.0f} ms")

    fresh()
    calibration.hover(300, 400)
    time.sleep(HOVER)
    mouse.SetCursorPos(10, 10)
    mouse.log.clear()
    calibration.click(300, 400, settle=0.0, hovered=True)
    moved, down = mouse.first("move"), mouse.first("down")
    check("if something moved the cursor away, the click moves back and hovers in full",
          moved is not None and moved[1] == (300, 400) and down[2] - moved[2] >= HOVER, f"{mouse.kinds()}")

    fresh()
    calibration.hover(300, 400)
    calibration.park(settle=False)
    check("parking forgets the hover", calibration.hovering((300, 400)) is None)
    calibration.hover(300, 400)
    calibration.click(300, 400, settle=0.0)
    check("and so does a press", calibration.hovering((300, 400)) is None)

    fresh()
    released = threading.Event()
    seen = {}
    pressing = threading.Thread(target=lambda: calibration.click(500, 500, settle=0.3, released=released))
    pressing.start()
    released.wait()
    seen["at"] = time.monotonic()
    pressing.join()
    up = mouse.first("up")
    check("a click says it is released right after the button comes up, before its settle",
          up is not None and up[2] <= seen["at"] < up[2] + 0.3, f"{(seen['at'] - up[2]) * 1000:.0f} ms after the release")

    fresh()
    calibration.ctrl_click(700, 236, check_hovering=True)
    moved, key = mouse.first("move"), mouse.first("key")
    check("the ctrl-click checks the server inside its hover, and holds Ctrl only after a full hover",
          len(checks_at) == 1 and moved[2] <= checks_at[0] < key[2] and key[2] - moved[2] >= HOVER,
          f"{(key[2] - moved[2]) * 1000:.0f} ms")

    calibration.inventory_tab_point(m.WORK_TAB)
    fresh()
    started = time.monotonic()
    driver.select_after_withdrawal()
    moved, down = mouse.first("move"), mouse.first("down")
    wait = driver.WITHDRAW_SETTLE - calibration.PARK_SETTLE
    check("tab 4 after the withdrawal is still pressed no sooner than the withdrawal settle",
          down[1] == calibration.inventory_tab_point(m.WORK_TAB) and down[2] - started >= wait,
          f"pressed {(down[2] - started) * 1000:.0f} ms in, the settle is {wait * 1000:.0f}")
    check("and its cursor settles and its server check runs inside that wait",
          down[2] - moved[2] >= HOVER and moved[2] <= checks_at[0] < down[2]
          and down[2] - started < wait + 0.03 * 0.9,
          f"hover {(down[2] - moved[2]) * 1000:.0f} ms, whole step {(down[2] - started) * 1000:.0f} ms")
finally:
    (open_inventory._user32, ctypes.windll.user32.SetCursorPos, calibration.cursor,
     calibration.hold_if_busy, calibration.snap) = guard

queued = []
saved = (calibration.FRAMES_ON, calibration._frames_waiting, calibration.grab, calibration.RUN_FRAMES)
calibration.FRAMES_ON = True
calibration.RUN_FRAMES = Path(tempfile.mkdtemp(prefix="cabal_frames_"))
calibration._frames_waiting = lambda: type("Q", (), {"put_nowait": staticmethod(queued.append)})()
calibration.grab = lambda: time.sleep(0.2) or Image.new("RGB", (4, 4))
try:
    began = time.monotonic()
    out = calibration.snap("click_1_2", alongside=True)
    took = time.monotonic() - began
    calibration._ALONGSIDE.submit(lambda: None).result()
    check("a frame taken alongside returns at once and is still written, under its own number",
          took < 0.1 and len(queued) == 1 and queued[0][1] == out and queued[0][3]["file"] == out.name,
          f"{took * 1000:.0f} ms, {len(queued)} queued")
    queued.clear()
    began = time.monotonic()
    calibration.snap("click_3_4")
    check("a frame not taken alongside is still taken before the next step",
          time.monotonic() - began >= 0.2 and len(queued) == 1)
finally:
    calibration.FRAMES_ON, calibration._frames_waiting, calibration.grab, calibration.RUN_FRAMES = saved

events = []
looks = []
images = iter(range(1, 100))
saved = (calibration.grab, calibration.hover, calibration.park, m.button_here, m.remembered, m.time.sleep)
calibration.grab = lambda: events.append(("grab", next(images))) or events[-1][1]
calibration.hover = lambda x, y: events.append(("hover", (x, y))) or time.monotonic()
calibration.park = lambda settle=True: events.append(("park", settle))
m.button_here = lambda word, point, image=None: events.append(("look", image)) or looks.pop(0)
m.remembered = lambda word: (968, 634)
m.time.sleep = lambda s: None
try:
    looks[:] = [True]
    point = m.find_button(m.CONFIRM_WORD, hover=True)
    check("finding a button with the hover: the screenshot first, then the cursor goes over, then the look",
          point == (968, 634) and [e[0] for e in events] == ["grab", "hover", "look"]
          and events[2][1] == events[0][1], str(events))
    events.clear()
    looks[:] = [False, True]
    point = m.find_button(m.CONFIRM_WORD, hover=True)
    check("when the button is not there yet, the cursor parks and later looks are as before",
          point == (968, 634) and [e[0] for e in events] == ["grab", "hover", "look", "park", "grab", "look"]
          and events[3][1] is False, str(events))
    events.clear()
    looks[:] = [True]
    m.find_button(m.CONFIRM_WORD)
    check("without the hover nothing moves", [e[0] for e in events] == ["grab", "look"], str(events))
finally:
    calibration.grab, calibration.hover, calibration.park, m.button_here, m.remembered, m.time.sleep = saved

READ, CLICK = 0.4, 0.3
timeline = []
answers = []
saved = (calibration.grab, m._read_and_agree, calibration.click, calibration.park)
calibration.grab = lambda: timeline.append(("grab", time.monotonic())) or "price screenshot"


def read_and_agree(panel, listed_at, verbose, image=None):
    timeline.append(("read starts", time.monotonic(), image))
    time.sleep(READ)
    timeline.append(("read ends", time.monotonic()))
    return answers.pop(0)


m._read_and_agree = read_and_agree
calibration.click = lambda x, y, settle=None, **k: (timeline.append(("radio down", time.monotonic())),
                                                     time.sleep(CLICK), timeline.append(("radio up", time.monotonic())))
calibration.park = lambda settle=True: timeline.append(("park", time.monotonic()))
try:
    answers[:] = [455000]
    began = time.monotonic()
    value = m.suggested_price(False, None, overlap=True)
    took = time.monotonic() - began
    at = {e[0]: e for e in timeline}
    check("the suggested price is read from the screenshot taken before the radio is clicked",
          value == 455000 and at["read starts"][2] == "price screenshot" and at["grab"][1] <= at["radio down"][1],
          str([e[0] for e in timeline]))
    check("and the radio click runs while that read is processed",
          at["radio down"][1] < at["read ends"][1] and took < READ + CLICK,
          f"{took * 1000:.0f} ms against {(READ + CLICK) * 1000:.0f} one after another")
    timeline.clear()
    answers[:] = [None, 455000]
    value = m.suggested_price(False, None, overlap=True)
    starts = [e for e in timeline if e[0] == "read starts"]
    park = next(e for e in timeline if e[0] == "park")
    check("if that read fails, the second read waits for the radio click and its settle, as before",
          value == 455000 and len(starts) == 2 and starts[1][2] is None
          and starts[1][1] >= park[1] + m.FIELD_SETTLE * 0.9, str([e[0] for e in timeline]))
    timeline.clear()
    answers[:] = [455000]
    m.suggested_price(False, None)
    order = [e[0] for e in timeline]
    check("without the overlap the order is unchanged: read, then the radio",
          order.index("read ends") < order.index("radio down"), str(order))
finally:
    calibration.grab, m._read_and_agree, calibration.click, calibration.park = saved

events = []
shown = []
saved = (calibration.grab, calibration.hover, calibration.park, m.warm_money, m.panel_quantity)
calibration.grab = lambda: events.append("grab") or "field screenshot"
calibration.hover = lambda x, y: events.append(f"hover {x},{y}") or time.monotonic()
calibration.park = lambda settle=True: events.append(f"park settle={settle}")
m.warm_money = lambda image, box: events.append(f"read {image}") or shown.pop(0)
m.panel_quantity = lambda want, verbose=False: events.append("net sales") or 3
try:
    shown[:] = [178898]
    m.check_price_field(178898, 3, next_point=(131, 754))
    check("the price read-back: its screenshot first, then the cursor to Register, then the read",
          events == ["grab", "hover 131,754", "read field screenshot"], str(events))
    events.clear()
    shown[:] = [978898]
    m.check_price_field(178898, 3, next_point=(131, 754))
    check("on a mismatch the cursor parks, with its settle, before the net sales are read again",
          events == ["grab", "hover 131,754", "read field screenshot", "park settle=True", "net sales"], str(events))
    events.clear()
    shown[:] = [178898]
    m.check_price_field(178898, 3)
    check("without a next point nothing moves", events == ["grab", "read field screenshot"], str(events))
finally:
    calibration.grab, calibration.hover, calibration.park, m.warm_money, m.panel_quantity = saved

calls = []
grabbed = []
seen_by = {}


def rec(name, value=None):
    return lambda *a, **k: calls.append((name, a, k)) or value


saved = (calibration.grab, m.panel_holds_item, calibration.slot_is_empty, calibration.ctrl_click,
         m.suggested_price, calibration.click, m.type_number, calibration.park, m.panel_quantity,
         m.check_price_field, m.find_button, m.underprice_warning, m.dialog_gone, calibration.occupied_slots,
         calibration.steps_table, m.time.sleep)
calibration.grab = lambda: grabbed.append(object()) or grabbed[-1]
m.panel_holds_item = lambda image=None: seen_by.setdefault("panel", []).append(image) or False
calibration.slot_is_empty = lambda image, r, c: seen_by.setdefault("slot", []).append(image) or False
calibration.ctrl_click = rec("ctrl-click")
m.suggested_price = rec("suggested price", 455000)
calibration.click = rec("click")
m.type_number = rec("type")
calibration.park = rec("park")
m.panel_quantity = rec("net sales", 1)
m.check_price_field = rec("read back")
m.find_button = rec("find", (968, 634))
m.underprice_warning = lambda image=None: False
m.dialog_gone = lambda timeout=None: True
calibration.occupied_slots = lambda image=None: set()
calibration.steps_table = lambda *a, **k: None
m.time.sleep = lambda s: None
panel = m._panel()
try:
    for overlap in (True, False):
        calls.clear()
        grabbed.clear()
        seen_by.clear()
        board = m.RowModel().seed({})
        with contextlib.redirect_stdout(io.StringIO()):
            out = board._list_slot(1, 1, verbose=False, overlap=overlap)
        clicks = {c[1][:2]: c[2] for c in calls if c[0] == "click"}
        if overlap:
            check("the relist listing: the panel check and the first look at the slot share one screenshot",
                  out["price"] and seen_by["panel"][0] is seen_by["slot"][0], f"{len(grabbed)} screenshot(s)")
            check("the ctrl-click checks the server while hovering",
                  next(c[2] for c in calls if c[0] == "ctrl-click") == {"check_hovering": True})
            check("the suggested price is read with the radio click alongside",
                  next(c[2] for c in calls if c[0] == "suggested price") == {"overlap": True})
            check("the quantity field checks the server while hovering and takes its frame alongside",
                  clicks[tuple(panel["qty_point"])].get("check_hovering") is True
                  and clicks[tuple(panel["qty_point"])].get("alongside") is True, str(clicks))
            check("the price field click is left as it was",
                  clicks[tuple(panel["price_point"])] == {"settle": m.FIELD_SETTLE}, str(clicks))
            check("the price read-back sends the cursor to Register, and Register is pressed as hovered",
                  next(c[2] for c in calls if c[0] == "read back") == {"next_point": panel["register_button"]}
                  and clicks[tuple(panel["register_button"])].get("hovered") is True, str(clicks))
            check("Confirmation is found with the hover and pressed as hovered",
                  next(c[2] for c in calls if c[0] == "find").get("hover") is True
                  and clicks[(968, 634)].get("hovered") is True, str(clicks))
        else:
            check("any other listing is as before: its own screenshot for each look and no overlap",
                  seen_by["panel"][0] is not seen_by["slot"][0]
                  and next(c[2] for c in calls if c[0] == "ctrl-click") == {"check_hovering": False}
                  and next(c[2] for c in calls if c[0] == "suggested price") == {"overlap": False}
                  and not any(k.get("hovered") or k.get("check_hovering") or k.get("alongside")
                              for k in clicks.values()), str(clicks))
finally:
    (calibration.grab, m.panel_holds_item, calibration.slot_is_empty, calibration.ctrl_click,
     m.suggested_price, calibration.click, m.type_number, calibration.park, m.panel_quantity,
     m.check_price_field, m.find_button, m.underprice_warning, m.dialog_gone, calibration.occupied_slots,
     calibration.steps_table, m.time.sleep) = saved

events = []
waits = []
saved = (m.read_row_and_button, m.find_button, m.dialog_gone, m.game_refused, calibration.click, calibration.park,
         calibration.hover, calibration.hovering, m.time.sleep, m.inv._user32)
m.inv._user32 = type("Hover", (), {"SetCursorPos": staticmethod(lambda *a: events.append("hover as before"))})()
m.read_row_and_button = lambda seat=None: events.append("row read") or ("Force Core (Ultimate) 49 399,553 On Sale Change",
                                                                       m.CHANGE_WORD)
m.find_button = lambda word, timeout=None, verbose=False, hover=False: events.append(f"find {word} hover={hover}") or (1, 1)
m.dialog_gone = lambda timeout=None: True
m.game_refused = lambda done, timeout=None: False
calibration.click = lambda x, y, settle=None, **k: events.append(("click", (x, y), k))
calibration.park = lambda settle=True: events.append(f"park settle={settle}")
calibration.hover = lambda x, y: events.append("hover") or time.monotonic()
hovered_since = {}
calibration.hovering = lambda point: hovered_since.get("at")
m.time.sleep = lambda s: waits.append(s)
try:
    text = "Force Core (Ultimate) 49 399,553 On Sale Change"
    board = m.RowModel().seed({})
    board._slots[5] = m.Row("Force Core (Ultimate", qty=49, price=399553)
    board.scroll_to = lambda index, verbose=True: {"moved": False}
    board._seat = m.FIRST_SEAT
    hovered_since["at"] = time.monotonic() - m.ACTION_GAP / 2
    with contextlib.redirect_stdout(io.StringIO()):
        board.cancel(5, verbose=False, tab_ready=True, tab_selected=True, read=(text, m.CHANGE_WORD), overlap=True)
    clicks = [e for e in events if isinstance(e, tuple)]
    check("the relist cancel uses the relist's own read of the row, with no second read",
          "row read" not in events, str(events))
    check("the Change wait counts from when the cursor reached Change",
          waits and m.ACTION_GAP / 2 * 0.8 <= waits[0] <= m.ACTION_GAP / 2 * 1.2 and "hover" not in events,
          f"waited {waits[0] if waits else None} of {m.ACTION_GAP}")
    check("Change is pressed with the server check inside its hover",
          clicks[0][1] == m.button_point(m.FIRST_SEAT) and clicks[0][2].get("check_hovering") is True, str(clicks))
    check("Cancel and Confirmation are found with the hover and pressed as hovered",
          f"find {m.DISMISS_WORD} hover=True" in events and f"find {m.CONFIRM_WORD} hover=True" in events
          and clicks[1][2].get("hovered") is True and clicks[2][2].get("hovered") is True, str(clicks))
    events.clear()
    waits.clear()
    hovered_since.clear()
    board._slots[5] = m.Row("Force Core (Ultimate", qty=49, price=399553)
    with contextlib.redirect_stdout(io.StringIO()):
        board.cancel(5, verbose=False, tab_ready=True, tab_selected=True, read=(text, m.CHANGE_WORD), overlap=True)
    check("if the cursor is no longer on Change, it goes there and waits the full hover",
          "hover" in events and waits and waits[0] >= m.ACTION_GAP * 0.9, f"{events[:3]}, waited {waits[:1]}")
    events.clear()
    board._slots[5] = m.Row("Force Core (Ultimate", qty=49, price=399553)
    board.scroll_to = lambda index, verbose=True: {"moved": True}
    with contextlib.redirect_stdout(io.StringIO()):
        board.cancel(5, verbose=False, tab_ready=True, tab_selected=True, read=(text, m.CHANGE_WORD), overlap=True)
    check("if the table had to move, the row is read again before Change", "row read" in events, str(events))
    events.clear()
    board._slots[5] = m.Row("Force Core (Ultimate", qty=49, price=399553)
    board.scroll_to = lambda index, verbose=True: {"moved": False}
    with contextlib.redirect_stdout(io.StringIO()):
        board.cancel(5, verbose=False, tab_ready=True, tab_selected=True)
    clicks = [e for e in events if isinstance(e, tuple)]
    check("a cancel from anywhere else still reads the row again and clicks as before",
          "row read" in events and "hover as before" in events and m.ACTION_GAP in waits
          and all(not any(c[2].values()) for c in clicks) and f"find {m.DISMISS_WORD} hover=False" in events,
          str(events))
finally:
    (m.read_row_and_button, m.find_button, m.dialog_gone, m.game_refused, calibration.click, calibration.park,
     calibration.hover, calibration.hovering, m.time.sleep, m.inv._user32) = saved

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
