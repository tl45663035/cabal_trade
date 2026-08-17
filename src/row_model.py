import ctypes
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
ACTION_GAP = _T["action_gap"]
WHEEL_GAP = _T["wheel_gap"]

CAPACITY = _FACTS["shop_capacity"]
VISIBLE = _FACTS["shop_visible"]
WORK_TAB = _FACTS["work_tab"]
GRID = _FACTS["grid_size"]

MAX_TOP = CAPACITY - VISIBLE + 1


class Divergence(Exception):
    pass


class Row:
    __slots__ = ("name", "qty", "list_price", "buy_cost")

    def __init__(self, name, qty=1, list_price=0, buy_cost=0):
        self.name = name
        self.qty = int(qty)
        self.list_price = int(list_price)
        self.buy_cost = int(buy_cost)

    @property
    def list_total(self):
        return self.list_price * self.qty

    @property
    def cost_total(self):
        return self.buy_cost * self.qty

    @property
    def margin(self):
        return self.list_total - self.cost_total

    def same_identity(self, other):
        return (other is not None
                and self.name == other.name
                and self.qty == other.qty
                and self.list_price == other.list_price)

    def copy(self):
        return Row(self.name, self.qty, self.list_price, self.buy_cost)

    def __repr__(self):
        return (f"Row({self.name!r}, qty={self.qty}, "
                f"list={self.list_price:,}, cost={self.buy_cost:,})")


class RowModel:
    def __init__(self, enforce=False):
        self._slots = {}
        self._work = {}
        self._top = None
        self.ready = False
        self.enforce = enforce
        self.divergences = 0

    def seed(self, rows, top=1):
        self._slots = {}
        for index, row in (rows or {}).items():
            index = int(index)
            if not 1 <= index <= CAPACITY:
                raise ValueError(f"row {index} is outside 1..{CAPACITY}")
            if row is not None:
                self._slots[index] = row
        self._top = int(top)
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
        top = max(self._slots) if self._slots else 0
        return [i for i in range(1, top + 1) if i not in self._slots]

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

    def cancel(self, index):
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

    def scroll_to(self, index, verbose=True):
        plan = self.scroll_plan(index)
        if plan["notches"]:
            wheel(plan["notches"], verbose=verbose)
        self.note_scrolled(plan["to_top"])
        if verbose:
            seen = self.visible()
            print(f"  scrolled to row {plan['to_top']}: showing {seen[0]}-"
                  f"{seen[-1]}, row {index} at position "
                  f"{plan['reachable_at_row']}"
                  + ("  (clamped -- it cannot reach position 1)"
                     if plan["clamped"] else ""))
        return plan


def table_point():
    shop = calibration.load()["shop"]
    point = shop.get("table_point")
    if not point:
        raise Divergence(
            "shop.table_point is not in calibration.json, so there is nowhere "
            "to put the cursor before scrolling. Re-run py src/calibration.py "
            "once it measures the listing table.")
    return tuple(point)


def rows_per_notch():
    shop = calibration.load()["shop"]
    value = shop.get("rows_per_notch")
    if not value:
        raise Divergence(
            "shop.rows_per_notch is not in calibration.json, so a notch count "
            "cannot be turned into a row count. Re-run py src/calibration.py "
            "once it measures the wheel.")
    return value


def _wheel_event(direction):
    return inv._Input(
        type=INPUT_MOUSE,
        u=inv._InputUnion(mi=inv._MouseInput(
            0, 0, ctypes.c_ulong(direction * WHEEL_DELTA & 0xFFFFFFFF).value,
            MOUSEEVENTF_WHEEL, 0, None)))


def wheel(rows, verbose=True):
    if not rows:
        return 0
    x, y = table_point()
    per = rows_per_notch()
    notches = int(round(abs(rows) / per))
    if not notches:
        return 0
    direction = -1 if rows > 0 else 1
    inv._user32.SetCursorPos(int(x), int(y))
    time.sleep(ACTION_GAP)
    event = _wheel_event(direction)
    for _ in range(notches):
        sent = inv._user32.SendInput(1, ctypes.byref(event),
                                     ctypes.sizeof(inv._Input))
        if sent != 1:
            raise Divergence(
                f"SendInput sent {sent} of 1 wheel event "
                f"(GetLastError {ctypes.get_last_error()})")
        time.sleep(WHEEL_GAP)
    time.sleep(ACTION_GAP)
    if verbose:
        print(f"  wheel {notches} event(s) {'down' if rows > 0 else 'up'} "
              f"at ({x}, {y}) for {rows:+d} row(s)")
    return notches

    def compare(self, read, top=None):
        top = self._top if top is None else int(top)
        seen = self.visible(top)
        wrong = []
        for index in seen:
            mine = self._slots.get(index)
            theirs = (read or {}).get(index)
            if mine is None and theirs is None:
                continue
            if mine is None or theirs is None:
                wrong.append((index, mine, theirs))
                continue
            if not mine.same_identity(theirs):
                wrong.append((index, mine, theirs))
        if wrong:
            self.divergences += len(wrong)
            if self.enforce:
                first = wrong[0]
                raise Divergence(
                    f"row {first[0]}: the model holds {first[1]!r} but the "
                    f"screen reads {first[2]!r}. {len(wrong)} row(s) disagree.")
        return wrong

    def totals(self):
        rows = list(self._slots.values())
        return {
            "rows": len(rows),
            "units": sum(r.qty for r in rows),
            "listed": sum(r.list_total for r in rows),
            "cost": sum(r.cost_total for r in rows),
            "margin": sum(r.margin for r in rows),
        }

    def report(self):
        out = [f"  ROW MODEL -- {self.used()} of {CAPACITY} slot(s) in use, "
               f"next listing lands at row {self.next_slot()}"]
        if self._top is not None:
            seen = self.visible()
            out.append(f"  showing rows {seen[0]}-{seen[-1]} of {CAPACITY}")
        for index in self.occupied():
            row = self._slots[index]
            out.append(f"    {index:2}  {row.name[:34]:34} x{row.qty:<4} "
                       f"list {row.list_total:>14,}  cost {row.cost_total:>14,}"
                       f"  {row.margin:>+13,}")
        gaps = self.holes()
        if gaps:
            out.append(f"  holes at {gaps} - these persist, nothing renumbers")
        t = self.totals()
        out.append(f"  {t['units']:,} unit(s), listed {t['listed']:,}, "
                   f"cost {t['cost']:,}, margin {t['margin']:+,}")
        return "\n".join(out)
