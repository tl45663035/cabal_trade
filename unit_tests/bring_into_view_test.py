import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


def row(name, index=1, action="change", qty=None, price=None):
    return m.Row(index=index, name=name, change=(0, 0), top=0, bottom=0,
                 action=action, price=price, qty=qty)


def screen(*names, start=1):
    return [row(n, index=start + i) for i, n in enumerate(names)]


class Harness:

    def __init__(self, here, after_top=None, chunks=()):
        self.here = here
        self.after_top = after_top if after_top is not None else here
        self.chunks = list(chunks)
        self.scrolled_to_top = 0
        self.chunk_calls = 0
        self._saved = {}

    def __enter__(self):
        for name in ("await_rows", "scroll_to_end", "scroll_chunk",
                     "informative_step"):
            self._saved[name] = getattr(m, name)
        m.await_rows = lambda timeout=8.0, poll=0.5: list(self.here)
        m.scroll_to_end = self._to_end
        m.scroll_chunk = self._chunk
        m.informative_step = lambda rows, want: want
        return self

    def __exit__(self, *exc):
        for name, value in self._saved.items():
            setattr(m, name, value)

    def _to_end(self, up, timeout=8.0, verbose=True):
        if up:
            self.scrolled_to_top += 1
            return list(self.after_top)
        return list(self.after_top)

    def _chunk(self, notches, before, timeout=8.0, verbose=True):
        self.chunk_calls += 1
        if not self.chunks:
            return list(before), 0
        return list(self.chunks.pop(0)), notches


here = screen("Alpha", "Beta", "Gamma")
ref = m.RowRef.of(here[1], here)
with Harness(here) as h:
    out = m.bring_into_view(ref, verbose=False)
check(out is not None, "a visible listing is found")
check(out is not None and any(r.name == "Beta" for r in out),
      f"and the view returned holds it, got {[r.name for r in (out or [])]}")
check(h.scrolled_to_top == 0,
      f"and NOTHING scrolled to the top -- that is the whole saving, "
      f"got {h.scrolled_to_top} scroll(s)")
check(h.chunk_calls == 0,
      f"and no chunks were walked, got {h.chunk_calls}")

for want in ("Alpha", "Gamma"):
    target = m.RowRef.of([r for r in here if r.name == want][0], here)
    with Harness(here) as h:
        out = m.bring_into_view(target, verbose=False)
    check(h.scrolled_to_top == 0 and out is not None,
          f"{want} at the edge of the screen also needs no scrolling")

sold = [row("Alpha"), row("Beta", 2, action="receive")]
target = m.RowRef.of(sold[1], sold)
with Harness(sold) as h:
    out = m.bring_into_view(target, verbose=False)
check(h.scrolled_to_top == 0 and out is not None,
      "a sold row already on screen needs no scrolling either")

empties = [row("(empty)", 1, action="register"),
           row("(empty)", 2, action="register")]
ghost = m.RowRef("Delta")
with Harness(empties, after_top=empties) as h:
    m.bring_into_view(ghost, verbose=False)
check(h.scrolled_to_top == 1,
      f"a listing that is NOT on screen still triggers the walk, got "
      f"{h.scrolled_to_top}")


top = screen("Alpha", "Beta")
deep = screen("Yankee", "Zulu", start=11)
target = m.RowRef("Zulu")
with Harness(top, after_top=top, chunks=[deep]) as h:
    out = m.bring_into_view(target, verbose=False)
check(h.scrolled_to_top == 1,
      f"a listing further down re-establishes the top exactly once, got "
      f"{h.scrolled_to_top}")
check(h.chunk_calls >= 1,
      f"and walks down in verified chunks, got {h.chunk_calls}")
check(out is not None and any(r.name == "Zulu" for r in out),
      f"and returns the view holding it, got {[r.name for r in (out or [])]}")

class Blind(Harness):
    def __enter__(self):
        super().__enter__()
        m.await_rows = lambda timeout=8.0, poll=0.5: []
        m.scroll_to_end = lambda up, timeout=8.0, verbose=True: None
        return self


with Blind([]) as h:
    out = m.bring_into_view(m.RowRef("Anything"), verbose=False)
check(out is None,
      f"an unreadable table is None, not an empty view, got {out!r}")

with Harness([], after_top=screen("Alpha")) as h:
    m.bring_into_view(m.RowRef("Alpha"), verbose=False)
check(h.scrolled_to_top == 1,
      f"an empty current view falls through to the walk, got "
      f"{h.scrolled_to_top}")


dupes = [row("Twin", 1, qty=8, price=54_000_000),
         row("Twin", 2, qty=8, price=54_000_000)]
second = m.RowRef.of(dupes[1], dupes)
with Harness(dupes) as h:
    out = m.bring_into_view(second, verbose=False)
check(h.scrolled_to_top == 0 and out is not None,
      "identical stacks already on screen need no scrolling")
live = [r for r in (out or []) if r.action == "change"]
found, note = m.locate_row(live, second)
check(found is not None, f"and one of the pair is identified, got {note!r}")
check(found is not None and found is live[1],
      f"and it is the SECOND twin, the one the ref names, got index "
      f"{live.index(found) if found in live else '?'} -- note {note!r}")
check("taking row 2" in note,
      f"and the disambiguation is reported rather than silent, got {note!r}")


class Jumper:

    def __init__(self, top, views, scrollable=True):
        self.top = top
        self.views = list(views)
        self.scrollable = scrollable
        self.wheel = []
        self.tops = 0
        self.chunks = 0
        self._saved = {}

    def __enter__(self):
        for name in ("await_rows", "scroll_to_end", "scroll_chunk",
                     "informative_step", "scroll_wheel", "park_cursor",
                     "table_scrollable", "record"):
            self._saved[name] = getattr(m, name)
        m.await_rows = self._next_view
        m.scroll_to_end = self._to_end
        m.scroll_chunk = self._chunk
        m.informative_step = lambda rows, want: want
        m.scroll_wheel = lambda x, y, notches, settle=0.35: self.wheel.append(notches)
        m.park_cursor = lambda *a, **k: None
        m.table_scrollable = lambda verbose=True: self.scrollable
        m.record = lambda *a, **k: None
        return self

    def __exit__(self, *exc):
        for name, value in self._saved.items():
            setattr(m, name, value)

    def _next_view(self, timeout=8.0, poll=0.5):
        return list(self.views.pop(0)) if self.views else []

    def _to_end(self, up, timeout=8.0, verbose=True):
        if up:
            self.tops += 1
        return list(self.top)

    def _chunk(self, notches, before, timeout=8.0, verbose=True):
        self.chunks += 1
        return list(before), 0


TOP = screen(*[f"Row{i:02d}" for i in range(1, 11)])
DEEP = screen("Row11", "Row12", "Target", start=11)

with Jumper(top=TOP, views=[[], DEEP]) as j:
    out = m.bring_into_view(m.RowRef("Target"), verbose=False, hint=13)
check(out is not None and any(r.name == "Target" for r in out),
      f"the hint finds the listing, got {[r.name for r in (out or [])]}")
check(j.wheel == [-3],
      f"one wheel movement of exactly (13 - 10) rows DOWN, got {j.wheel}")
check(j.chunks == 0,
      f"and no stepping at all -- that is the saving, got {j.chunks} chunk(s)")
check(j.tops == 1, f"the top is established once, got {j.tops}")

with Jumper(top=TOP, views=[[], TOP]) as j:
    m.bring_into_view(m.RowRef("Row04"), verbose=False, hint=4)
check(j.wheel == [],
      f"a hint inside the first screen never scrolls, got {j.wheel}")

with Jumper(top=TOP, views=[[], screen("Nope", "Nothing", start=11)]) as j:
    j.chunks = 0
    out = m.bring_into_view(m.RowRef("Target"), verbose=False, hint=13)
check(j.wheel == [-3], f"the jump is still attempted, got {j.wheel}")
check(j.tops == 2,
      f"a missed jump returns to a known origin before walking, got "
      f"{j.tops} top(s)")
check(j.chunks >= 1,
      f"and the verified walk still runs, got {j.chunks} chunk(s)")

with Jumper(top=TOP, views=[[], DEEP]) as j:
    m.bring_into_view(m.RowRef("Target"), verbose=False)
check(j.wheel == [],
      f"no hint means no jump, got {j.wheel}")
check(j.chunks >= 1, "and the walk runs as before")

with Jumper(top=TOP, views=[[], DEEP], scrollable=False) as j:
    m.bring_into_view(m.RowRef("Target"), verbose=False, hint=13)
check(j.wheel == [],
      f"an unscrollable table is never wheeled, got {j.wheel}")


print(f"bring_into_view_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
