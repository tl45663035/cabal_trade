"""Identical stacks must each be relisted once -- not one of them six times.

THE INCIDENT this reproduces, from logs/run_2026-08-09_002654.log:

    ##### 11/16: row 11 ##### -> already on screen -> Moved: now at row 10
    ##### 12/16: row 12 ##### -> already on screen -> Moved: now at row 10
    ##### 13/16 ... 14/16 ... 15/16 ... 16/16  -> all row 10

Six targets, one physical row relisted six times, five stacks never touched and
left to expire. Between 30% and 70% of every cancel in every run that day was a
repeat of a row already done.

WHY NOTHING CAUGHT IT. A RowRef for absolute row 11 is byte-identical to one
for row 10 when the two stacks share name, quantity and price -- so `holds()`
is satisfied by either. locate_row then narrows three twins to one by the qty
filter and returns it with an EMPTY note, so not even the "N rows are
identical" warning fired. And a relist against an unchanged market
re-registers at the same price, so the row never leaves the sibling pool.

bring_into_view_test.py has a duplicate case, and it passes both before and
after the fix, because it only ever asks whether the returned view CONTAINS a
matching row. That is the question that cannot distinguish twins. This file
asks WHICH row, which is the question that matters.
"""
import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m  # noqa: E402

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


def row(index, name, action="change", qty=None, price=None):
    return m.Row(index=index, name=name, change=(0, 0), top=0, bottom=0,
                 action=action, price=price, qty=qty)


# The shop as it stood in the incident: six indistinguishable stacks at
# absolute rows 10-15, unique listings above them.
SHOP = ([row(i, f"Unique {i:02d}") for i in range(1, 10)]
        + [row(i, "Upgrade Core (Ultimate)", qty=250, price=440_000)
           for i in range(10, 16)])
SCREEN = 10


# -- RowRef must admit that it cannot identify the row on its own ----------
refs = [m.RowRef.of(r, SHOP) for r in SHOP]
for r, ref in zip(SHOP, refs):
    if r.name.startswith("Unique"):
        check(ref.siblings == 1,
              f"{r.name} is unique, siblings should be 1, got {ref.siblings}")
    else:
        check(ref.siblings == 6,
              f"row {r.index} is one of six identical stacks, siblings should "
              f"be 6, got {ref.siblings}")

# The ordinals must be distinct, or two targets name the same stack.
twin_refs = [ref for r, ref in zip(SHOP, refs) if ref.siblings > 1]
check(sorted(x.ordinal for x in twin_refs) == list(range(6)),
      f"the six twins must carry ordinals 0..5, got "
      f"{sorted(x.ordinal for x in twin_refs)}")


# -- the walk must land on a view whose OFFSET is known -------------------
class Walk:
    """A shop that can be scrolled, reporting verified shifts."""

    def __init__(self, shop, screen=SCREEN):
        self.shop = shop
        self.screen = screen
        self.offset = 0          # absolute index of the top visible row, 0-based
        self.saved = {}
        self.early_reads = 0

    def view(self):
        return list(self.shop[self.offset:self.offset + self.screen])

    def __enter__(self):
        for n in ("await_rows", "scroll_to_end", "scroll_chunk",
                  "informative_step", "scroll_wheel", "park_cursor",
                  "table_scrollable", "record"):
            self.saved[n] = getattr(m, n)
        m.await_rows = self._await
        m.scroll_to_end = self._to_end
        m.scroll_chunk = self._chunk
        m.informative_step = lambda rows, want: want
        m.scroll_wheel = lambda x, y, n, settle=0.35: None
        m.park_cursor = lambda *a, **k: None
        m.table_scrollable = lambda verbose=True: True
        m.record = lambda *a, **k: None
        return self

    def __exit__(self, *exc):
        for n, v in self.saved.items():
            setattr(m, n, v)

    def _await(self, timeout=8.0, poll=0.5):
        self.early_reads += 1
        return self.view()

    def _to_end(self, up, timeout=8.0, verbose=True):
        self.offset = 0 if up else max(0, len(self.shop) - self.screen)
        return self.view()

    def _chunk(self, notches, before, timeout=8.0, verbose=True):
        want = min(notches, max(0, len(self.shop) - self.screen - self.offset))
        self.offset += want
        return self.view(), want


# Each of the six twins must resolve to its OWN absolute row.
resolved = []
for wanted in range(10, 16):
    ref = next(x for r, x in zip(SHOP, refs) if r.index == wanted)
    with Walk(SHOP) as w:
        rep: dict = {}
        view = m.bring_into_view(ref, verbose=False, hint=wanted, report=rep)
    check(view is not None, f"row {wanted}: a view was returned")
    top = rep.get("top_index")
    check(top is not None,
          f"row {wanted}: the walk must report the view's absolute offset, "
          f"got {top!r} -- without it the caller cannot tell twins apart")
    if view is not None and top is not None:
        offset = wanted - top
        check(0 <= offset < len(view),
              f"row {wanted}: offset {offset} must fall inside the view")
        if 0 <= offset < len(view):
            resolved.append(view[offset].index)

check(sorted(resolved) == list(range(10, 16)),
      f"each of the six identical stacks must resolve to its OWN row; got "
      f"{sorted(resolved)} -- repeats here ARE the incident")
check(len(set(resolved)) == 6,
      f"six distinct rows, got {len(set(resolved))} distinct from {resolved}")

# And the shortcut that caused the incident must be refused for twins: the
# already-on-screen return cannot know the offset, so it must not be taken.
ref11 = next(x for r, x in zip(SHOP, refs) if r.index == 11)
with Walk(SHOP) as w:
    m.bring_into_view(ref11, verbose=False, hint=11, report={})
check(w.early_reads == 0,
      f"for a twin, the already-on-screen shortcut must be skipped -- it "
      f"answers by identity and cannot tell twins apart; it ran "
      f"{w.early_reads} time(s)")

# A UNIQUE listing must still take the cheap path -- the saving is real and
# this fix must not throw it away.
uniq = next(x for r, x in zip(SHOP, refs) if r.index == 3)
with Walk(SHOP) as w:
    view = m.bring_into_view(uniq, verbose=False, hint=3, report={})
check(w.early_reads == 1,
      f"a unique listing still uses the on-screen shortcut, got "
      f"{w.early_reads} read(s)")


# -- locate_row alone cannot do this, which is why the caller must not ----
#    rely on it for twins.
twins_only = [r for r in SHOP if r.name == "Upgrade Core (Ultimate)"]

# TWO twins in view: locate_row does warn. Worth pinning, because it is the
# case people reason about and it is not the dangerous one.
pair = [r for r in twins_only if r.index in (12, 13)]
found, note = m.locate_row(pair, ref11)
check(found is not None and found.index in (12, 13),
      f"with two twins in view it returns one of them, got "
      f"{found.index if found else None}")
check("identical" in note,
      f"and it does warn when it can see more than one, got {note!r}")

# ONE twin in view: this is the invisible case, and the one that fired six
# times in the incident. match_rows finds a single row, locate_row short-
# circuits, and the warning never happens.
single = [r for r in SHOP if r.index in (1, 2, 12)]
found, note = m.locate_row(single, ref11)
check(found is not None and found.index == 12,
      f"a view holding ONE twin returns it as though it were the row asked "
      f"for, got {found.index if found else None}")
check(note == "",
      f"and says nothing at all -- no 'identical' warning, which is why six "
      f"wrong relists left no trace but 'Moved: now at row 10'. got {note!r}")


print(f"siblings_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
