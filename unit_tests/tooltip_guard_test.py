"""The cursor must leave the row before a dialog is looked for.

THE INCIDENT, from logs/run_2026-08-09_015103.log: 63 consecutive
"the Registration Extension dialog did not appear" aborts, almost all on one
row, three attempts per cycle for three cycles, which tripped the
consecutive-failure breaker and ended a four-hour run that had otherwise
collected 1,150,119,580 Alz.

THE CAUSE, read off the recorded frame (corpus run_35757.png, label
cancel.after_change): resting the cursor on a listing raises the game's "Item
Information" tooltip, and it is drawn straight over the region await_dialog
reads. The frame shows that tooltip carrying the row's own figures --
"Item in Sale: 136", "Sales Price: 430,650 Alz",
"Total Sales Price: 58,568,400 Alz" -- with the chat log behind it, which is
where the stray 'chat' in the OCR came from. The dialog was never covered by
anything the script could name; it was covered by a tooltip the script itself
summoned by leaving the mouse where it clicked.

park_cursor already existed and its docstring states this exact rule -- "Move
the cursor off the listings so no tooltip covers the table." The cancel and
collect paths were simply never given it.

What is asserted here is ORDER, not merely that park_cursor is called: parking
after the dialog read would satisfy a naive check and fix nothing.
"""
import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
# NO GAME INPUT FROM A TEST. cancel_item() below runs with dry_run=False and
# reaches the real focus_game(), which un-minimises Cabal, pulls it to the
# foreground and can inject a real Alt keydown. NO_INPUT does not gate that.
import os as _os_guard
import sys as _sys_guard
_sys_guard.path.insert(0, _os_guard.path.dirname(
    _os_guard.path.abspath(__file__)))
import _no_input_guard  # noqa: F401,E402
import trade as m  # noqa: E402

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


# -- the ordering, driven through the real cancel_item ---------------------
events: list[str] = []
_saved = {n: getattr(m, n) for n in
          ("click", "park_cursor", "await_dialog", "await_dialog_button",
           "await_rows", "read_rows", "grab", "record", "refresh_table",
           "dialog_kind", "trade_window_open", "find_words")}


def row(index, name, action="change", qty=None, price=None):
    return m.Row(index=index, name=name, change=(1126, 545), top=0, bottom=0,
                 action=action, price=price, qty=qty)


TABLE = [row(1, "Upgrade Core (Ultimate)", qty=136, price=430_650)]

try:
    m.click = lambda x, y, settle=0.15: events.append(f"click({x},{y})")
    m.park_cursor = lambda settle=0.0: events.append("park")
    m.grab = lambda: object()
    m.record = lambda *a, **k: None
    m.trade_window_open = lambda source=None: True
    m.dialog_kind = lambda shot=None, words=None: None
    m.find_words = lambda shot, region, scale=20: []
    m.await_rows = lambda timeout=8.0, poll=0.5: list(TABLE)
    m.read_rows = lambda shot=None, words=None: list(TABLE)
    m.refresh_table = lambda timeout=8.0, verbose=True: list(TABLE)

    def fake_dialog(kind, timeout=8.0, *a, **k):
        events.append(f"await_dialog({kind})")
        return None                      # reproduce the failure: no dialog

    m.await_dialog = fake_dialog
    m.await_dialog_button = lambda word, timeout=8.0, poll=0.4, source=None: None

    ref = m.RowRef.of(TABLE[0], TABLE)
    try:
        m.cancel_item(1, expect=ref, verbose=False)
    except Exception:                    # noqa: BLE001 - the abort is expected
        pass
finally:
    for name, value in _saved.items():
        setattr(m, name, value)

clicks = [i for i, e in enumerate(events) if e.startswith("click")]
parks = [i for i, e in enumerate(events) if e == "park"]
waits = [i for i, e in enumerate(events) if e.startswith("await_dialog")]

check(bool(clicks), f"the Change button was clicked, got {events}")
check(bool(waits), f"and a dialog was awaited, got {events}")
check(bool(parks),
      f"the cursor must be parked at all -- without it the Item Information "
      f"tooltip covers the dialog. events={events}")

if clicks and waits and parks:
    first_click = clicks[0]
    first_wait = min(w for w in waits if w > first_click)
    between = [p for p in parks if first_click < p < first_wait]
    check(bool(between),
          f"the park must fall BETWEEN the click and the dialog read -- "
          f"parking afterwards fixes nothing. click@{first_click} "
          f"wait@{first_wait} parks@{parks}  events={events}")


# -- park_cursor must actually move somewhere useful ----------------------
px, py = m.PARK_POINT
check(not (m.PURCHASE_DIALOG_REGION[0] <= px <= m.PURCHASE_DIALOG_REGION[2]
           and m.PURCHASE_DIALOG_REGION[1] <= py <= m.PURCHASE_DIALOG_REGION[3]),
      f"PARK_POINT {m.PARK_POINT} sits inside the dialog region "
      f"{m.PURCHASE_DIALOG_REGION} -- parking there would cover the thing it "
      f"is meant to uncover")

# Above the listing rows, so the cursor cannot rest on one.
check(py < m.PURCHASE_ROW_TOP,
      f"PARK_POINT y={py} must be above the first listing row "
      f"(y={m.PURCHASE_ROW_TOP}), or the tooltip simply moves to another row")


print(f"tooltip_guard_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
