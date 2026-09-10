import os
import sys
from pathlib import Path

os.environ.setdefault("CABAL_SALES_DB",
                      str(Path(os.environ.get("TEMP", ".")) / "shop_model_test.db"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import trade as m

FAILED = []


def check(ok, why):
    if not ok:
        FAILED.append(why)
        print(f"  FAIL  {why}")


class FakeRow:

    def __init__(self, name, action="change", qty=None, status="On Sale"):
        self.name = name
        self.action = action
        self.qty = qty
        self.status = status

    @property
    def occupied(self):
        return self.action in ("change", "receive")


class EmptyRow:

    name, qty, price, status, action = "(empty)", None, None, "", "register"

    @property
    def occupied(self):
        return False


def full_sweep(pairs):
    have = dict(pairs)
    return [(i, have.get(i) or EmptyRow())
            for i in range(1, m.SHOP_ROW_CAPACITY + 1)]


def model_of(*names):
    s = m.ShopModel()
    s.adopt(full_sweep([(i, FakeRow(n)) for i, n in enumerate(names, 1) if n]))
    return s


print("\n--- placement: the lowest empty slot, always")

s = model_of("A", "B", "C")
check(s.first_empty() == 4, f"three occupied -> next lands at 4, got {s.first_empty()}")
check(s.register("D") == 4, "and register returns that row")
check(s.occupied_count() == 4, f"occupied is a tally, got {s.occupied_count()}")

s = model_of("A", "B", None, "D", "E")
check(s.first_empty() == 3, f"a hole at 3 is where content goes, got {s.first_empty()}")
check(s.occupied_count() == 4,
      f"the tally counts slots, not the highest index, got {s.occupied_count()}")
check(s.register("X") == 3, "the hole is filled before the end of the shop")
check(s.first_empty() == 6, "and the next lands after the last occupied slot")

s = model_of("A", None, None, None, "E")
check(s.occupied_count() == 2 and not s.is_empty(5),
      "two occupied, but row 5 is one of them: count is not a boundary")


print("\n--- cancel: empties in place, nothing moves")

s = model_of("A", "B", "C", "D")
s.cancel(3)
check(s.is_empty(3), "the cancelled slot is empty")
check(s.content(4) is not None and s.content(4)["name"] == "D",
      "and row 4 still holds what it held -- nothing renumbers")
check(s.register("C") == 3, "relisting goes back to the same row it came from")

s = model_of("A", "B", "C", "D", "E")
s.cancel(2)
s.cancel(5)
check(s.register("E") == 2,
      "with a hole above, relisted content lands in the FIRST empty row")
check(s.is_empty(5), "and the row it came from stays empty")


print("\n--- collect: Complete empties, On Sale keeps its remainder")

s = model_of("A", "B", "C", "D", "E")
before_below = s.content(5)["name"]
s.collect(4, empties=True)
check(before_below == "E", "the row below held E going in")

check(not s.ready,
      "a slot-freeing collection stands the model down for a re-seed")
check(s.occupied_count() == 0, "and it holds nothing until re-seeded")

s = model_of("A", "B")
s.collect(1, empties=False, qty_left=60)
check(s.ready, "a partial sale does not stand the model down")

s = model_of("A", "B")
s.collect(1, empties=False, qty_left=60)
check(not s.is_empty(1), "a partial sale leaves the slot occupied")
check(s.content(1)["qty"] == 60, f"with the remainder, got {s.content(1)['qty']}")


print("\n--- Row.empties_on_collect: Status decides, qty corroborates")

def real_row(action, qty, status, name="X"):
    return m.Row(index=1, name=name, change=(0, 0), top=0, bottom=10,
                 action=action, price=None, qty=qty, status=status)


check(real_row("receive", 0, "Complete").empties_on_collect is True,
      "Receive + Complete empties")
check(real_row("receive", 175, "On Sale").empties_on_collect is False,
      "Receive + On Sale does not")
check(real_row("change", 250, "On Sale").empties_on_collect is False,
      "a live row is not collectable at all")
check(real_row("register", None, "").occupied is False,
      "a Register row is an empty slot")
check(real_row("receive", 0, "Complete").occupied is True,
      "a sold-and-uncollected row still HOLDS its listing")
check(real_row("receive", 0, "").empties_on_collect is True,
      "no Status, qty 0 -> fully sold")
check(real_row("receive", None, "").empties_on_collect is False,
      "no Status and no qty -> assume it stays, the safe direction")


print("\n--- divergence terminates; sales do not")

s = model_of("A", "B", "C")
try:
    s.check(2, FakeRow("B", "receive", 40, "On Sale"))
    ok = True
except m.ShopDiverged:
    ok = False
check(ok, "a row that SOLD is not a divergence -- buyers act when they choose")

try:
    s.check(2, FakeRow("B", "change", 250, "On Sale"))
    ok = True
except m.ShopDiverged:
    ok = False
check(ok, "nor is a quantity that fell")

for label, index, row in (
        ("content in a slot the model calls empty", 9, FakeRow("Z")),
        ("a different item than the model holds", 2, FakeRow("ZZZ")),
        ("an empty row the model calls occupied", 3,
         FakeRow("(empty)", "register"))):
    s = model_of("A", "B", "C")
    s.enforce = True
    try:
        s.check(index, row)
        raised = False
    except m.ShopDiverged:
        raised = True
    check(raised, f"{label}: terminates when enforcing")

s = model_of("A", "B", "C")
s.enforce = False
s.check(9, FakeRow("Z"))
check(s.divergences == 1, f"shadow mode counts it, got {s.divergences}")
check(not s.ready, "and stands the model down rather than continuing on it")

s = model_of("Upgrade Core (Ultimate)")
try:
    s.check(1, FakeRow("Upgrade Core(Ultimate)"))
    ok = True
except m.ShopDiverged:
    ok = False
check(ok, "spacing differences are not a divergence")

s = model_of("Chaos Core Set X 250")
s.enforce = True
try:
    s.check(1, FakeRow("Chaos Core Set X 135"))
    raised = False
except m.ShopDiverged:
    raised = True
check(not raised, "a different bundle COUNT is not a divergence (see above)")

s = model_of("Force Core(High)")
s.enforce = True
try:
    s.check(1, FakeRow("Force Core(Highest)"))
    raised = False
except m.ShopDiverged:
    raised = True
check(raised, "but a different item still terminates")

s = model_of("Force Core(Highest)")
s.enforce = True
try:
    s.check(1, FakeRow("Force Core(Highest) X 250"))
    ok = True
except m.ShopDiverged:
    ok = False
check(ok, "a catalogue name matches the shop's pack-marked name")


print("\n--- nothing is inferred before the init walk")

s = m.ShopModel()
check(not s.ready, "a fresh model is not ready")
try:
    s.check(1, FakeRow("anything"))
    ok = True
except m.ShopDiverged:
    ok = False
check(ok, "and it refuses to judge anything until a full walk adopts it")

s.adopt(full_sweep([(1, FakeRow("A"))]))
check(s.ready, "adopting a sweep makes it ready")


print("\n--- per-row floor and cost")

s = m.ShopModel()
s.adopt(full_sweep([]))
r1 = s.register("Force Core(Highest)", qty=250, price=343_035,
                floor=192_212, cost=192_212)
r2 = s.register("Force Core(Highest)", qty=5, price=343_035,
                floor=333_329, cost=333_329)
check(s.floor_of(r1) == 192_212 and s.floor_of(r2) == 333_329,
      f"each row keeps its OWN floor, got {s.floor_of(r1)} and {s.floor_of(r2)}")
check(s.cost_of(r1) != s.cost_of(r2),
      "and its own cost, so dear stock is not priced off cheap stock")

check(s.below_floor(r2, 300_000) is True,
      "listing the dear row at 300,000 is under its floor")
check(s.below_floor(r1, 300_000) is False,
      "...while the same price clears the cheap row's floor")
check(s.below_floor(r2, 333_329) is False,
      "AT the floor is not below it")

r3 = s.register("Epic Booster (Highest)", qty=1, price=50_000_000)
check(s.floor_of(r3) == 0 and s.below_floor(r3, 1) is False,
      "no floor recorded means no floor claim")

seeded = m.ShopModel()
seeded.adopt(full_sweep([(1, FakeRow("Force Core(High)"))]))
check(seeded.content(1) is not None
      and "floor" in seeded.content(1) and "cost" in seeded.content(1),
      "a seeded row still carries both fields, even when the ledger has no cost")


print("\n--- the shop can fill up")

s = m.ShopModel()
s.enforce = True
s.adopt(full_sweep([(i, FakeRow(f"item{i}"))
                        for i in range(1, m.SHOP_ROW_CAPACITY + 1)]))
check(s.first_empty() is None, "a full shop has nowhere to land")
try:
    s.register("one more")
    raised = False
except m.ShopDiverged:
    raised = True
check(raised, "and registering into it is a divergence, not a silent no-op")


print("\n" + "=" * 60)
if FAILED:
    print(f"shop model: {len(FAILED)} failure(s)")
    raise SystemExit(1)
print("shop model: all green")
