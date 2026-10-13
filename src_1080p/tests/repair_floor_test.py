import contextlib
import importlib.util
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_repair_floor_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot", "list-named", "1", "2", "0", "4", "Force Core Set (Highest)",
            "--floor=440437"]
import no_input
TRIPPED = no_input.arm()
import calibration
import convert
import driver
import ledger

ledger.DB = Path(os.environ["CABAL_SALES_DB"])
ledger._RUN = None
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


check("the driver takes the floor the supervisor hands it", driver.REPAIR_FLOOR == 440_437, str(driver.REPAIR_FLOOR))
check("the floor is no word of the command or the item's name",
      driver._plain_argv() == ["list-named", "1", "2", "0", "4", "Force Core Set (Highest)"], str(driver._plain_argv()))

CRASHED = "\n".join([
    'TASK {"kind": "resupply", "core": "Force Core(High)", "set": "Force Core Set (High)", "slot": 7, "tab": 4, "landing": [1, 1]}',
    'TASK {"kind": "resupply", "core": "Force Core(High)", "set": "Force Core Set (High)", "slot": 7, "tab": 4, "step": "convert", "holding": "Force Core Set (High)", "qty": 170, "price": 243844}',
    'TASK {"kind": "resupply", "core": "Force Core(High)", "set": "Force Core Set (High)", "slot": 7, "tab": 4, "step": "list", "holding": "Force Core(High)", "qty": 170, "price": 243844}',
    'TASK {"kind": "resupply", "core": "Force Core(High)", "set": "Force Core Set (High)", "slot": 7, "tab": 4, "step": "convert", "holding": "Force Core Set (High)", "qty": 0, "price": 243844}',
    'DONE {"kind": "resupply", "core": "Force Core(High)", "bought": 170, "listed": 170, "rows": [17]}',
    'TASK {"kind": "resupply", "core": "Force Core(Highest)", "set": "Force Core Set (Highest)", "slot": 1, "tab": 4, "landing": [1, 1]}',
    'TASK {"kind": "resupply", "core": "Force Core(Highest)", "set": "Force Core Set (Highest)", "slot": 1, "tab": 4, "step": "convert", "holding": "Force Core Set (Highest)", "qty": 193, "price": 440437}',
    'TASK {"kind": "resupply", "core": "Force Core(Highest)", "set": "Force Core Set (Highest)", "slot": 1, "tab": 4, "step": "list", "holding": "Force Core(Highest)", "qty": 193, "price": 440437}',
    'TASK {"kind": "resupply", "core": "Force Core(Highest)", "set": "Force Core Set (Highest)", "slot": 1, "tab": 4, "step": "convert", "holding": "Force Core Set (Highest)", "qty": 193, "price": 440437}',
])

spec = importlib.util.spec_from_file_location("supervise", SRC.parent / "tools" / "supervise.py")
sup = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(sup)

tasks = sup.interrupted(CRASHED)
check("the 12 Oct crashed run's log gives the Force Core(Highest) job and the 440,437 a unit it was bought at",
      tasks == [("resupply", {"core": "Force Core(Highest)", "holding": "Force Core Set (Highest)", "qty": 193,
                              "price": 440_437})], str(tasks))
check("what the log names on tab 4 carries that cost",
      sup.named_by_log(tasks) == ("holding", "Force Core Set (Highest)", 193, False, 440_437, None),
      str(sup.named_by_log(tasks)))

CHAOS_HEAD = [
    'TASK {"kind": "resupply", "core": "Chaos Core", "set": "Chaos Core Set", "slot": 3, "tab": 4}',
    'TASK {"kind": "resupply", "core": "Chaos Core", "set": "Chaos Core Set", "slot": 3, "tab": 4, "step": "craft", "holding": "Chaos Core", "qty": 72, "price": 722699}',
]
CHAOS_CRAFTED = "\n".join(CHAOS_HEAD + [
    'TASK {"kind": "resupply", "core": "Chaos Core", "set": "Chaos Core Set", "slot": 3, "tab": 4, "step": "list", "holding": "Chaos Core Set", "qty": 72, "price": 722699, "work": [1, 1]}',
])
CHAOS_10_51 = "\n".join([
    'TASK {"kind": "resupply", "core": "Chaos Core", "set": "Chaos Core Set", "slot": 3, "tab": 4}',
    'TASK {"kind": "resupply", "core": "Chaos Core", "set": "Chaos Core Set", "slot": 3, "tab": 4, "step": "craft", "holding": "Chaos Core", "qty": 72, "price": 0}',
    'TASK {"kind": "resupply", "core": "Chaos Core", "set": "Chaos Core Set", "slot": 3, "tab": 4, "step": "list", "holding": "Chaos Core Set", "qty": 72, "price": 0}',
])
chaos = sup.interrupted(CHAOS_CRAFTED)
check("a Chaos restock cut short after the craft gives the Set, its 72 cores, the 722,699 a core they cost and "
      "the slot the Set is in",
      chaos == [("resupply", {"core": "Chaos Core", "holding": "Chaos Core Set", "qty": 72, "price": 722_699,
                              "work": (1, 1)})], str(chaos))
check("what the log names on tab 4 carries the Chaos cost and the Set's slot",
      sup.named_by_log(chaos) == ("holding", "Chaos Core Set", 72, False, 722_699, (1, 1)),
      str(sup.named_by_log(chaos)))

sent, rows, said = [], [], []
saved = (sup.first_row, sup.run_driver, sup.event)
sup.first_row = lambda tab: rows.pop(0) if rows else []
sup.run_driver = lambda *args: sent.append(args)
sup.event = lambda text, *a, **k: said.append(text)
try:
    step = sup.step_for("holding", "Force Core Set (Highest)", 193, False)
    rows[:] = [[(1, 2)], [(1, 2)], [(1, 2)], [], []]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sup.clear_work_tab(8, tasks)
    check("the repair converts and then lists with the crashed run's cost as the floor",
          step is not None and step[0] == "convert"
          and sent == [("collect",), step + ("--floor=440437",),
                       ("list-named", "1", "2", "0", str(sup.WORK_TAB), "Force Core Set (Highest)",
                        "--floor=440437")], str(sent))
    check("the supervisor logs the price it was bought at",
          "bought at 440,437 a unit" in out.getvalue()
          and any("no lower than the 440,437 a unit it was bought at" in s for s in said), out.getvalue())

    sent.clear()
    said.clear()
    rows[:] = [[(1, 2)], [], []]
    relist = [("relist", {"row": 3, "item": "Force Core(Highest)", "qty": 193, "price": 999_999, "tab": 4,
                          "slot": (1, 2)})]
    with contextlib.redirect_stdout(io.StringIO()):
        sup.clear_work_tab(8, relist)
    check("a relist's price is what the row was listed at, not a cost, so it is no floor",
          sent == [("collect",), ("list-named", "1", "2", "0", str(sup.WORK_TAB), "Force Core(Highest)")],
          str(sent))

    sent.clear()
    rows[:] = [[(1, 1)], [], []]
    with contextlib.redirect_stdout(io.StringIO()):
        sup.clear_work_tab(8, [("resupply", {"core": "Chaos Core", "special": True, "qty": 1})])
    check("a log with no cost recorded lists with no floor, as before",
          sent == [("collect",), ("list-named", "1", "1", "0", str(sup.WORK_TAB), "Chaos Core")], str(sent))

    sent.clear()
    said.clear()
    rows[:] = [[(1, 1)], [], []]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sup.clear_work_tab(8, chaos)
    check("the 10:51 case with its cost logged: the Chaos Core Set X 72 goes out no lower than its 72 cores cost, "
          "72 x 722,699 = 52,034,328",
          sent == [("collect",), ("list-named", "1", "1", "0", str(sup.WORK_TAB), "Chaos Core Set",
                                  "--floor=52034328")], str(sent))
    check("the supervisor logs the Set's cost and where it came from",
          "so it goes out no lower than 52,034,328" in out.getvalue()
          and any("no lower than the 52,034,328 the 72 it was crafted from cost" in s for s in said),
          out.getvalue())

    sent.clear()
    rows[:] = [[(1, 1), (1, 2)], [(1, 2)], [(1, 2)], [], []]
    with contextlib.redirect_stdout(io.StringIO()):
        sup.clear_work_tab(8, chaos)
    check("a spare raw Chaos Core left beside the Set goes out no lower than the 722,699 one core cost, "
          "not at the Set's cost",
          sent == [("collect",),
                   ("list-named", "1", "1", "0", str(sup.WORK_TAB), "Chaos Core Set", "--floor=52034328"),
                   ("list-named", "1", "2", "0", str(sup.WORK_TAB), "Chaos Core Set", "--floor=722699")],
          str(sent))

    sent.clear()
    rows[:] = [[(1, 1)], [], []]
    crafting = sup.interrupted("\n".join(CHAOS_HEAD))
    with contextlib.redirect_stdout(io.StringIO()):
        sup.clear_work_tab(8, crafting)
    check("a Chaos restock cut short before the craft is crafted with the 722,699 a core it cost as the floor",
          sent == [("collect",), ("craft", "3", "--floor=722699")], str(sent))

    sent.clear()
    rows[:] = [[(1, 1)], [], []]
    with contextlib.redirect_stdout(io.StringIO()):
        sup.clear_work_tab(8, sup.interrupted(CHAOS_10_51))
    check("the 10:51 log itself, which logged a price of 0, lists with no floor, as it did",
          sent == [("collect",), ("list-named", "1", "1", "0", str(sup.WORK_TAB), "Chaos Core Set")], str(sent))

    favourites = dict(calibration.FAVOURITE_ITEMS)
    calibration.FAVOURITE_ITEMS.update({"11": "Divine Stone", "12": "Divine Stone Set"})
    try:
        divine_head = [
            'TASK {"kind": "resupply", "core": "Divine Stone", "set": "Divine Stone Set", "slot": 11, "tab": 4}',
            'TASK {"kind": "resupply", "core": "Divine Stone", "set": "Divine Stone Set", "slot": 11, "tab": 4, "step": "craft", "holding": "Divine Stone", "qty": 18, "price": 700000}',
        ]
        divine_crafted = divine_head + [
            'TASK {"kind": "resupply", "core": "Divine Stone", "set": "Divine Stone Set", "slot": 11, "tab": 4, "step": "list", "holding": "Divine Stone Set", "qty": 18, "price": 700000, "work": [1, 1]}',
        ]
        sent.clear()
        rows[:] = [[(1, 1)], [], []]
        with contextlib.redirect_stdout(io.StringIO()):
            sup.clear_work_tab(8, sup.interrupted("\n".join(divine_crafted)))
        check("a Divine Stone restock cut short after the craft lists the Divine Stone Set no lower than its "
              "18 stones cost, 18 x 700,000 = 12,600,000",
              sent == [("collect",), ("list-named", "1", "1", "0", str(sup.WORK_TAB), "Divine Stone Set",
                                      "--floor=12600000")], str(sent))
        sent.clear()
        rows[:] = [[(1, 1)], [], []]
        with contextlib.redirect_stdout(io.StringIO()):
            sup.clear_work_tab(8, sup.interrupted("\n".join(divine_head)))
        check("a Divine Stone restock cut short before the craft is crafted, as Chaos is, with the 700,000 a "
              "stone it cost as the floor",
              sent == [("collect",), ("craft", "11", "--floor=700000")], str(sent))
        sent.clear()
        rows[:] = [[(1, 1)], [], []]
        with contextlib.redirect_stdout(io.StringIO()):
            sup.clear_work_tab(8, [("resupply", {"core": "Divine Stone"})])
        check("a Divine Stone restock cut short while buying is crafted too, as Chaos is",
              sent == [("collect",), ("craft", "11")], str(sent))
    finally:
        calibration.FAVOURITE_ITEMS.clear()
        calibration.FAVOURITE_ITEMS.update(favourites)
finally:
    sup.first_row, sup.run_driver, sup.event = saved

import craft
import json

offers = []


def fake_offers(job, want, batch, on_margin=True, verbose=True):
    if not offers:
        return False
    n, each = offers.pop(0)
    job["bought"] += n
    job["paid"] += n * each
    return True


def tasks_said(text):
    return [json.loads(line[len("TASK "):]) for line in text.splitlines() if line.startswith("TASK ")]


saved = (driver.take_offers, calibration.close_everything, craft.craft_sets, craft.close_craft)
driver.take_offers = fake_offers
calibration.close_everything = lambda *a, **k: None
craft.craft_sets = lambda core, verbose=True, held=None: {"slot": (1, 1), "used": 72}
craft.close_craft = lambda *a, **k: None
try:
    offers[:] = [(71, 722_596), (1, 730_000)]
    job = {"core": "Chaos Core", "set": "Chaos Core Set", "slot": 3, "target": 72, "want_max": None,
           "bought": 0, "paid": 0, "step": "buy"}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        bought = driver.buy_cores(job)
    said_task = tasks_said(out.getvalue())
    check("the 10:51 buy, 71 cores at 722,596 and 1 at 730,000, logs the 722,699 a core they cost before the craft",
          bought and said_task and said_task[-1]["step"] == "craft" and said_task[-1]["holding"] == "Chaos Core"
          and said_task[-1]["qty"] == 72 and said_task[-1]["price"] == 722_699, str(said_task))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        driver.craft_cores(None, job)
    said_task = tasks_said(out.getvalue())
    check("after the craft the log holds the Set, its 72 cores, the 722,699 a core they cost and its slot (1, 1)",
          said_task and said_task[-1]["step"] == "list" and said_task[-1]["holding"] == "Chaos Core Set"
          and said_task[-1]["qty"] == 72 and said_task[-1]["price"] == 722_699
          and said_task[-1]["work"] == [1, 1], str(said_task))
    job = {"core": "Chaos Core", "set": "Chaos Core Set", "slot": 3, "bought": 72, "paid": 0, "step": "craft"}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        driver.craft_cores(None, job)
    said_task = tasks_said(out.getvalue())
    check("cores held with nothing paid this job log no price, so the repair keeps its own floor",
          said_task and said_task[-1]["price"] == 0, str(said_task))
    offers[:] = [(18, 700_000)]
    craft.craft_sets = lambda core, verbose=True, held=None: {"slot": (1, 1), "used": 18}
    job = {"core": "Divine Stone", "set": "Divine Stone Set", "slot": 11, "target": 18, "want_max": None,
           "bought": 0, "paid": 0, "step": "buy"}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        driver.buy_cores(job)
        driver.craft_cores(None, job)
    said_task = tasks_said(out.getvalue())
    check("a Divine Stone restock logs the 700,000 a stone it paid before the craft, and after it the Set, its 18 "
          "stones and its slot",
          len(said_task) == 2 and said_task[0]["step"] == "craft" and said_task[0]["price"] == 700_000
          and said_task[1]["step"] == "list" and said_task[1]["holding"] == "Divine Stone Set"
          and said_task[1]["qty"] == 18 and said_task[1]["price"] == 700_000 and said_task[1]["work"] == [1, 1],
          str(said_task))
finally:
    driver.take_offers, calibration.close_everything, craft.craft_sets, craft.close_craft = saved


class FakeModel:
    def __init__(self):
        self.calls = []

    def seed(self, rows):
        return self

    def list_slot(self, row, col, **kwargs):
        self.calls.append(kwargs)
        return {"qty": 193, "price": 440_437}

    def place(self, *a, **k):
        pass

    def hold_work(self, *a, **k):
        pass


fake = FakeModel()
saved = (driver.initialise, driver.register_tab, driver.row_model.RowModel, calibration.click, driver.REPAIR_FLOOR)
driver.initialise = lambda **k: None
driver.register_tab = lambda **k: None
driver.row_model.RowModel = lambda: fake
calibration.click = lambda *a, **k: None
try:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        driver.do_list(1, 2, None, tab=4, named="Force Core Set (Highest)")
    got = fake.calls[-1]
    check("list-named with the crashed run's cost lists no lower than it, a unit, with no bundle count",
          got["floor"] == 440_437 and got["unit_market"] is None and got["floor_each"] == 0
          and "paid 440,437 a unit" in got["why"], str(got))
    check("the listing logs the floor and where it came from",
          "floor 440,437 a unit: what the run that bought Force Core Set (Highest) paid" in out.getvalue(),
          out.getvalue().strip().splitlines()[0])
    driver.REPAIR_FLOOR = 0
    with contextlib.redirect_stdout(io.StringIO()):
        driver.do_list(1, 2, None, tab=4, named="Force Core Set (Highest)")
    got = fake.calls[-1]
    check("list-named with no cost handed over keeps no floor of its own",
          got["floor"] == 0 and got["why"] == "", str(got))
    driver.REPAIR_FLOOR = 440_437
    with contextlib.redirect_stdout(io.StringIO()):
        driver.do_list(1, 2, 300_000, tab=4, named="Force Core Set (Highest)")
    got = fake.calls[-1]
    check("a listing given its own price is not floored by the handed cost", got["floor"] == 0, str(got))
finally:
    (driver.initialise, driver.register_tab, driver.row_model.RowModel, calibration.click,
     driver.REPAIR_FLOOR) = saved

rounds = []


def fake_convert(core, verbose=True):
    rounds.append(core)
    if len(rounds) > 1:
        raise convert.Refused("no more to convert")
    return {"slots": [(1, 2)]}


saved = (driver.initialise, driver.register_tab, driver.seed, driver.open_rows, driver.back_to_the_shop,
         calibration.close_everything, calibration.click, convert.open_vendor, convert.convert, driver.REPAIR_FLOOR)
driver.initialise = lambda **k: None
driver.register_tab = lambda **k: None
driver.seed = lambda **k: fake
driver.open_rows = lambda model, first, last: [5]
driver.back_to_the_shop = lambda **k: True
calibration.close_everything = lambda *a, **k: None
calibration.click = lambda *a, **k: None
convert.open_vendor = lambda **k: None
convert.convert = fake_convert
try:
    slot = int(step[1])
    core = calibration.FAVOURITE_ITEMS[str(slot)]
    driver.REPAIR_FLOOR = 440_437
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        driver.do_convert(slot)
    got = fake.calls[-1]
    check(f"the convert repair lists the {core} no lower than the crashed run's cost",
          got["floor"] == 440_437 and "paid 440,437 a unit" in got["why"]
          and "floor 440,437 a unit: what the run that bought it paid" in out.getvalue(), str(got))
    driver.REPAIR_FLOOR = 0
    rounds.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        driver.do_convert(slot)
    got = fake.calls[-1]
    check("the convert repair with no cost handed over keeps the floor on file, as before",
          got["floor"] == (calibration.price_floor(core)[0] or 0), str(got))
finally:
    (driver.initialise, driver.register_tab, driver.seed, driver.open_rows, driver.back_to_the_shop,
     calibration.close_everything, calibration.click, convert.open_vendor, convert.convert,
     driver.REPAIR_FLOOR) = saved


class SetModel(FakeModel):
    def list_slot(self, row, col, **kwargs):
        self.calls.append(kwargs)
        return {"qty": 1, "price": 52_783_127, "resolved": False, "units": 72, "item": None}


sets = SetModel()
saved = (driver.initialise, driver.register_tab, driver.seed, driver.open_rows, driver.back_to_the_shop,
         driver.get_price.get_price, calibration.close_everything, calibration.click, craft.craft_sets,
         craft.close_craft, driver.REPAIR_FLOOR)
driver.initialise = lambda **k: None
driver.register_tab = lambda **k: None
driver.seed = lambda **k: sets
driver.open_rows = lambda model, first, last: [5]
driver.back_to_the_shop = lambda **k: True
driver.get_price.get_price = lambda slot, verbose=True: {"unit_price": 733_098}
calibration.close_everything = lambda *a, **k: None
calibration.click = lambda *a, **k: None
craft.craft_sets = lambda core, verbose=True, held=None: {"slot": (1, 1), "used": 72}
craft.close_craft = lambda *a, **k: None
favourites = dict(calibration.FAVOURITE_ITEMS)
crafted_with = []
try:
    driver.REPAIR_FLOOR = 722_699
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        driver.do_craft()
    got = sets.calls[-1]
    check("the craft repair lists the Set no lower than the 722,699 a core the crashed run paid, a Set at a time",
          got["floor_each"] == 722_699 and got["unit_market"] == 733_098
          and "paid 722,699 a Chaos Core" in got["why"]
          and "the run that bought them paid 722,699 a Chaos Core" in out.getvalue(), str(got))
    driver.REPAIR_FLOOR = 0
    with contextlib.redirect_stdout(io.StringIO()):
        driver.do_craft()
    got = sets.calls[-1]
    check("the craft repair with no cost handed over keeps the cost on file, as before",
          got["floor_each"] == (calibration.price_floor("Chaos Core Set")[0] or 733_098), str(got))
    driver.REPAIR_FLOOR = 722_699
    with contextlib.redirect_stdout(io.StringIO()):
        driver.do_craft(3)
    got = sets.calls[-1]
    check("craft 3 crafts the Chaos Core of favourite slot 3, as craft chaos does",
          got["expect_item"] == "Chaos Core Set" and got["floor_each"] == 722_699, str(got))

    calibration.FAVOURITE_ITEMS.update({"11": "Divine Stone", "12": "Divine Stone Set"})
    craft.craft_sets = lambda core, verbose=True, held=None: crafted_with.append(core) or {"slot": (1, 1),
                                                                                          "used": 18}
    driver.REPAIR_FLOOR = 700_000
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        driver.do_craft(11)
    got = sets.calls[-1]
    check("craft 11 crafts the Divine Stones into a Divine Stone Set and lists it no lower than the 700,000 a "
          "stone the crashed run paid, as Chaos is",
          crafted_with == ["Divine Stone"] and got["expect_item"] == "Divine Stone Set"
          and got["floor_each"] == 700_000 and "paid 700,000 a Divine Stone" in got["why"]
          and "the run that bought them paid 700,000 a Divine Stone" in out.getvalue(), str(got))
    listed = len(sets.calls)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        driver.do_craft(1)
    check("craft of a favourite that is not crafted crafts and lists nothing",
          len(sets.calls) == listed and crafted_with == ["Divine Stone"]
          and "not a crafted core" in out.getvalue(), out.getvalue().strip())
    asked = []
    saved_craft = driver.do_craft
    driver.do_craft = lambda slot=None, verbose=True: asked.append(slot)
    try:
        driver._dispatch(["craft", "chaos"])
        driver._dispatch(["craft", "11"])
    finally:
        driver.do_craft = saved_craft
    check("craft chaos still crafts the Chaos Core, and craft 11 the core of favourite 11", asked == [None, 11],
          str(asked))
finally:
    calibration.FAVOURITE_ITEMS.clear()
    calibration.FAVOURITE_ITEMS.update(favourites)
    (driver.initialise, driver.register_tab, driver.seed, driver.open_rows, driver.back_to_the_shop,
     driver.get_price.get_price, calibration.close_everything, calibration.click, craft.craft_sets,
     craft.close_craft, driver.REPAIR_FLOOR) = saved

import open_agent_shop_premium
import row_model as m
from PIL import Image


class Typed(Exception):
    pass


def typed(value, presses):
    raise Typed(value)


def replay(reads, floor, rule_item="Force Core Set (Highest)"):
    saved = (m._panel, m._asking, m._disputed, m._pick_suggestion, m.type_number, m.panel_holds_item,
             calibration.grab, calibration.ctrl_click, calibration.click, open_agent_shop_premium.slot_point)
    m._panel = lambda: {"suggestion_boxes": [[30, 350, 160, 365], [30, 376, 160, 391]]}
    m._asking = lambda image, panel: reads
    m._disputed = lambda *a: False
    m._pick_suggestion = lambda point: None
    m.type_number = typed
    m.panel_holds_item = lambda *a, **k: False
    calibration.grab = lambda *a, **k: Image.new("RGB", (1920, 1080))
    calibration.ctrl_click = lambda *a, **k: None
    calibration.click = lambda *a, **k: None
    open_agent_shop_premium.slot_point = lambda row, col: (1535, 236)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            m.RowModel()._list_slot(1, 2, floor=floor, why=f"the run that bought it paid {floor:,} a unit",
                                    unit_market=None, floor_each=0, floor_item=None,
                                    rule_item=rule_item, wait_fill=False)
    except Typed as exc:
        return exc.args[0], out.getvalue()
    finally:
        (m._panel, m._asking, m._disputed, m._pick_suggestion, m.type_number, m.panel_holds_item,
         calibration.grab, calibration.ctrl_click, calibration.click, open_agent_shop_premium.slot_point) = saved
    return None, out.getvalue()


price, said = replay((274_834, 274_834, 274_834, 195_232), 440_437)
check("the 21:53 repair's reads, market 274,834 and week 195,232, now type 440,437, not 274,833",
      price == 440_437 and "under the 440,437 floor" in said, said.strip().splitlines()[-1])
price, said = replay((274_834, 274_834, 274_834, 195_232), 0)
check("the same reads with no cost handed over type 274,833, as the repair did", price == 274_833,
      said.strip().splitlines()[-1])
price, said = replay((1_000_000, 1_000_000, 1_000_000, 195_232), 440_437)
check("the crashed run's own read, 1,000,000 against a 195,232 week, types 999,999 with the cost as floor",
      price == 999_999, said.strip().splitlines()[-1])
price, said = replay((52_783_128, 52_783_128, 52_783_128, 52_841_736), 52_034_328, "Chaos Core Set")
check("the 10:53 repair's reads of the Chaos Core Set X 72, market 52,783,128, still type 52,783,127 with its "
      "52,034,328 cost as floor", price == 52_783_127, said.strip().splitlines()[-1])
price, said = replay((51_500_000, 51_500_000, 51_500_000, 52_841_736), 52_034_328, "Chaos Core Set")
check("had that market fallen to 51,500,000, the Set would go out at its 52,034,328 cost, not 51,499,999",
      price == 52_034_328, said.strip().splitlines()[-1])

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
