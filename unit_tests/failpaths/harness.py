"""Failure-path harness for trade.py.

Stubs the whole click/capture layer so the state machine can be driven through
its recovery code without touching the game. NOTHING here writes to the project
directory, and no real input or capture is ever performed:

  * every input primitive (click, ctrl_click, type_number, press_escape,
    scroll_wheel, move_mouse, park_cursor, focus_game) is replaced by a
    recorder,
  * grab() returns a real corpus frame (loaded once, read-only) so the code
    under test still gets a PIL image, but every OCR reader is replaced by a
    model of the game state, so no Tesseract ever runs,
  * record() is replaced by a spy, so the corpus on disk is never appended to,
  * time is virtualised, so timeouts expire instantly and deterministically.

The functions actually under test -- cancel_item, register_item, relist,
_relist_cycle, relist_rows, run_sequence, run_loop, sanity_check, await_dialog,
await_dialog_button, await_rows, wait_for_table, refresh_table,
close_any_dialog, locate_row, RowRef -- all run for real.
"""

from __future__ import annotations


import sys as _sys
from pathlib import Path as _Path

# Redirect the sales ledger BEFORE trade is imported, because trade resolves
# SALES_DB once at import time from this variable.
#
# The docstring above says nothing here writes to the project directory. That
# was not true: these suites replay the collect path for real, note_sale()
# writes a row wherever SALES_DB points, and only t29 redirected it -- for its
# own cases only. Measured on 2026-08-07, the live ledger held 1,168 rows of
# which 1,163 were this suite, arriving in a recognisable burst of
# 2+6+1+80+18+18 rows inside 45 seconds. Every "what did I make today" total
# had been counting replayed corpus frames as income.
#
# Set HERE rather than in run_all.py so it holds for a suite run on its own,
# which is how the last 18 junk rows got in after run_all.py was fixed.
import os as _os
import tempfile as _tempfile

_os.environ.setdefault(
    "CABAL_SALES_DB",
    str(_Path(_tempfile.gettempdir()) / "cabal_test_sales.db"))
_ROOT = _Path(__file__).resolve().parent.parent.parent
_sys.path.insert(0, str(_ROOT))
import io
import sys
import traceback
from pathlib import Path

ROOT = _ROOT
CORPUS = ROOT / "unit_tests" / "corpus"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import trade  # noqa: E402

# trade.py is being edited while these tests run, so every result is stamped
# with the exact build it was produced against.
_SRC = ROOT / "trade.py"
import hashlib  # noqa: E402
VERSION = (f"trade.py {_SRC.stat().st_size} bytes, "
           f"mtime {_SRC.stat().st_mtime_ns}, "
           f"sha256 {hashlib.sha256(_SRC.read_bytes()).hexdigest()[:12]}")


# --------------------------------------------------------------------------
# Virtual time
# --------------------------------------------------------------------------

class FakeClock:
    """Stands in for the `time` module inside trade."""

    def __init__(self) -> None:
        self.t = 10_000.0
        self.slept = 0.0

    def monotonic(self) -> float:
        # Advance a hair on every read so a loop that only reads the clock
        # still terminates.
        self.t += 0.001
        return self.t

    def sleep(self, seconds) -> None:
        seconds = max(0.0, float(seconds))
        self.t += seconds
        self.slept += seconds

    def time(self) -> float:
        return self.t


# --------------------------------------------------------------------------
# Fixed geometry for the fake game
# --------------------------------------------------------------------------

DISMISS_XY = (1500, 900)      # the 'Cancel' button on any dialog
CONFIRM_XY = (1300, 900)      # the 'Confirmation' button
RECEIPT_XY = (1700, 900)      # the 'Receive' button on Confirm Receipt
REGISTER_XY = (140, 980)      # the panel's Register button
REFRESH_XY = (1100, 1000)     # the table's Refresh button
ROW_BUTTON_X = 1116           # REF_FUNCTION_COLUMN_X
ROW_TOP0 = 200
ROW_PITCH = 79


def word(text: str, centre, conf: float = 90.0):
    x, y = centre
    return trade.Word(text=text, left=x - 20, top=y - 8, right=x + 20,
                      bottom=y + 8, conf=conf)


def make_row(index: int, name: str, action: str = "change",
             price=410_000, qty=100):
    top = ROW_TOP0 + (index - 1) * ROW_PITCH
    return trade.Row(index=index, name=name,
                     change=(ROW_BUTTON_X, top + ROW_PITCH // 2),
                     top=top, bottom=top + ROW_PITCH, action=action,
                     price=price, qty=qty)


def empty_panel() -> dict:
    return {"prices": [], "price_rows": [], "typed": None, "qty": None,
            "qty_max": None, "qty_text": "", "net_sales": 0, "loaded": False,
            "slot_stdev": 3.0}


def loaded_panel(qty: int = 100, qty_max: int = 100,
                 lowest: int = 410_000, average: int = 500_000) -> dict:
    return {"prices": [average, lowest],
            "price_rows": [(average, trade.PRICE_TOP_Y),
                           (lowest, trade.PRICE_BOTTOM_Y)],
            "typed": None, "qty": qty, "qty_max": qty_max,
            "qty_text": f"{qty} / {qty_max}", "net_sales": 0, "loaded": True,
            "slot_stdev": 44.0}


# --------------------------------------------------------------------------
# The harness
# --------------------------------------------------------------------------

class Harness:
    """A model of the game plus a full set of stubs for trade's I/O layer."""

    _frame = None
    _frame_name = ""

    def __init__(self, rows=None, panel=None, dialog=None, *, verbose=False):
        self.calls: list[tuple] = []                 # every stubbed call
        self.records: list[tuple[str, dict]] = []    # every record() call
        self.printed: list[str] = []

        # --- game state ---------------------------------------------------
        self.rows = list(rows) if rows is not None else [
            make_row(1, "Upgrade Core (Ultimate)"),
            make_row(2, "Force Core (Ultimate)", price=1_500_000, qty=50),
        ]
        self.panel = dict(panel) if panel is not None else empty_panel()
        self.dialog = dialog
        self.loading = False
        self.trade_open = True
        self.inventory_tab = trade.WORK_TAB
        self.origin = (1800, 300)
        self.work_tab_empty = True
        self.returned_slots = [(1, 1)]
        self.register_button_present = True
        self.refresh_button_present = True

        # --- fault injection ----------------------------------------------
        self.suppress_extension = False       # Change click opens nothing
        self.extension_vanishes = False       # seen once, then gone
        self.no_confirm_button = False        # Confirmation never located
        self.no_cancel_button = False         # Cancel never located
        self.confirm_sticks = False           # Confirmation leaves it open
        self.commit_on_stick = False          # ...and the game took it anyway
        self.click_fault: dict = {}           # {nth click: exception}
        self.grab_fault: dict = {}            # {nth grab: exception}
        self.park_fault: dict = {}            # {nth park: exception}
        self.focus_fault: dict = {}           # {nth focus: exception | False}
        self.rows_fault: dict = {}            # {nth read_rows: exception}
        self.locked = False
        self.elevated = True
        self.load_fails = False               # ctrl+click loads nothing
        self.load_as: dict = {}               # kwargs for loaded_panel()
        self.register_commits = True
        self.post_commit_slot_sticks = False
        self.table_refreshes = True
        self.net_sales_extra = 0              # breaks divisibility
        self.price_never_takes = False        # net sales stays 0
        self._focus_field = None
        self.register_name = "Upgrade Core (Ultimate)"
        self.registered: list[dict] = []

        # Arm a fault to fire on the NEXT call to `stub` after record(label):
        #   h.arm_after = {"inventory.before_cancel": ("park_cursor", PermissionError(...))}
        self.arm_after: dict = {}
        self._armed: tuple | None = None

        self.n_click = self.n_grab = self.n_park = 0
        self.n_focus = self.n_rows = 0
        self.pending_register = None
        self._extension_seen = 0
        self._cancel_target = None
        self.verbose = verbose
        self.clock = FakeClock()
        self._saved: dict = {}
        self._had_print = hasattr(trade, "print")

    # ---------------------------------------------------------------- log
    def log(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def clicks(self) -> list[tuple]:
        return [(c[0], c[1]) for c in self.calls
                if c[0] in ("click", "ctrl_click")]

    def labels(self) -> list[str]:
        return [r[0] for r in self.records]

    def rec(self, label: str) -> dict | None:
        for name, ctx in self.records:
            if name == label:
                return ctx
        return None

    def out(self) -> str:
        return "\n".join(self.printed)

    def said(self, needle: str) -> bool:
        return needle.casefold() in self.out().casefold()

    # ------------------------------------------------------------- frames
    @classmethod
    def frame(cls):
        """One real corpus frame, loaded once, never modified."""
        if cls._frame is None:
            from PIL import Image
            picks = sorted(CORPUS.glob("run_*.png"))
            if picks:
                # Pinned, so a corpus that grows under a live run cannot make
                # these tests non-deterministic.
                chosen = picks[0]
                cls._frame = Image.open(io.BytesIO(chosen.read_bytes()))
                cls._frame.load()
                cls._frame_name = chosen.name
            else:
                cls._frame = Image.new("RGB", (2560, 1440))
                cls._frame_name = "<synthetic>"
        return cls._frame

    # --------------------------------------------------------------- stubs
    def _armed_fault(self, stub: str):
        """Fire a fault armed by a record() label, once."""
        if self._armed and self._armed[0] == stub:
            exc = self._armed[1]
            self._armed = None
            raise exc

    def _grab(self):
        self.n_grab += 1
        self.log("grab", self.n_grab)
        self._armed_fault("grab")
        fault = self.grab_fault.get(self.n_grab)
        if fault is not None:
            raise fault
        return self.frame()

    def _click(self, x, y, settle=0.15):
        self.n_click += 1
        self.log("click", x, y)
        self._armed_fault("click")
        fault = self.click_fault.get(self.n_click)
        if fault is not None:
            raise fault
        self._react(x, y)

    def _ctrl_click(self, x, y, settle=0.15):
        self.n_click += 1
        self.log("ctrl_click", x, y)
        self._armed_fault("ctrl_click")
        fault = self.click_fault.get(self.n_click)
        if fault is not None:
            raise fault
        self._react_ctrl(x, y)

    def _park(self, settle=0.0):
        self.n_park += 1
        self.log("park_cursor", self.n_park)
        self._armed_fault("park_cursor")
        fault = self.park_fault.get(self.n_park)
        if fault is not None:
            raise fault

    def _focus(self, settle=0.35):
        self.n_focus += 1
        self.log("focus_game", self.n_focus)
        self._armed_fault("focus_game")
        fault = self.focus_fault.get(self.n_focus)
        if isinstance(fault, BaseException):
            raise fault
        return fault is not False

    def _print(self, *args, **kwargs):
        text = " ".join(str(a) for a in args)
        self.printed.append(text)
        if self.verbose:
            sys.__stdout__.write(text + "\n")

    def _record(self, label, shot=None, /, **context):
        self.records.append((label, dict(context)))
        self.log("record", label)
        if label in self.arm_after:
            self._armed = self.arm_after[label]

    # ------------------------------------------------------- game reaction
    def _react(self, x, y):
        def near(p, tol=40):
            return abs(x - p[0]) <= tol and abs(y - p[1]) <= tol

        if near(DISMISS_XY):
            if self.dialog == "extension":
                self.dialog = "confirm"
            elif self.dialog == "confirm":
                self.dialog = None
                self.pending_register = None
            elif self.dialog == "receipt":
                # Confirm Receipt also has a Cancel, and it closes outright.
                # Modelling that click as a no-op made a receipt dialog
                # uncloseable here, which is not how the game behaves and
                # would hide a real failure to back out of one.
                self.dialog = None
            return
        if near(CONFIRM_XY):
            if self.dialog != "confirm":
                return
            if self.pending_register is not None:
                if self.register_commits:
                    self._commit_register()
                if not self.confirm_sticks:
                    self.dialog = None
                return
            if self.confirm_sticks:
                if self.commit_on_stick:
                    self._commit_cancel()
                return                        # the dialog stays up
            self._commit_cancel()
            self.dialog = None
            return
        if near(RECEIPT_XY):
            if self.dialog == "receipt":
                self.dialog = None
                self._collect()
            return
        if near(REGISTER_XY):
            self.pending_register = dict(self.panel)
            self.dialog = "confirm"
            return
        if near(REFRESH_XY):
            return

        # the price radio buttons, in the left rail of the Register panel
        if abs(x - trade.PANEL_RADIO_X) <= 25:
            for value, row_y in self.panel.get("price_rows", []):
                if abs(y - row_y) <= trade.PRICE_ROW_Y_TOL:
                    self._set_price(value)
                    return
            return
        # the free-text price field
        px = (trade.PRICE_FIELD[0] + trade.PRICE_FIELD[2]) // 2
        py = (trade.PRICE_FIELD[1] + trade.PRICE_FIELD[3]) // 2
        if abs(x - px) <= 90 and abs(y - py) <= 25:
            self._focus_field = "price"
            return
        if (abs(x - trade.QTY_INPUT[0]) <= 60
                and abs(y - trade.QTY_INPUT[1]) <= 25):
            self._focus_field = "qty"
            return

        for row in self.rows:                 # a table row button
            if abs(x - row.change[0]) <= 45 and abs(y - row.change[1]) <= 35:
                self._cancel_target = row
                if row.action == "change" and not self.suppress_extension:
                    self.dialog = "extension"
                elif row.action == "receive":
                    self.dialog = "receipt"
                return

    def _react_ctrl(self, x, y):
        if (abs(x - trade.SHOP_SLOT[0]) <= 30
                and abs(y - trade.SHOP_SLOT[1]) <= 30):
            self.panel = empty_panel()        # back to the inventory
            return
        if self.load_fails:
            return
        self.panel = loaded_panel(**self.load_as)

    def _set_price(self, value):
        """The game recomputes Net sales = price x quantity."""
        self.panel["chosen_price"] = value
        qty = self.panel.get("qty") or 0
        if self.price_never_takes:
            self.panel["net_sales"] = 0
            return
        self.panel["net_sales"] = value * qty + self.net_sales_extra

    def _press_escape(self, *a, **k):
        """Escape closes the Trade WINDOW, but not a dialog.

        Modelled as a pure no-op before, which made every close_shop() in the
        suite end with "the Trade window would not close with Escape" -- a
        failure message on the happy path, and it hid the fact that nothing
        was asserting the window ever closes at all.

        Dialogs are excluded deliberately: close_any_dialog's docstring records
        that the game ignores Escape there, which is why it walks the chain
        with clicks instead.
        """
        self.log("press_escape")
        if self.dialog is None:
            self.trade_open = False

    def _type_number(self, value, *a, **k):
        self.log("type_number", value)
        field = getattr(self, "_focus_field", None)
        if field == "qty":
            cap = self.panel.get("qty_max") or value
            new = min(int(value), int(cap))
            self.panel["qty"] = new
            self.panel["qty_text"] = f"{new} / {cap}"
            if self.panel.get("chosen_price"):
                self._set_price(self.panel["chosen_price"])
        elif field == "price":
            self._set_price(int(value))

    def _renumber(self):
        for n, row in enumerate(self.rows, start=1):
            row.index = n

    def _commit_cancel(self):
        row = self._cancel_target
        if row is not None and row in self.rows:
            self.rows.remove(row)
            self._renumber()

    def _collect(self):
        row = self._cancel_target
        if row is not None and row in self.rows:
            self.rows.remove(row)
            self._renumber()

    def _commit_register(self):
        panel = self.pending_register or {}
        price = panel.get("chosen_price")
        qty = panel.get("chosen_qty") or panel.get("qty") or 0
        self.registered.append({"price": price, "qty": qty})
        self.rows.append(make_row(len(self.rows) + 1, self.register_name,
                                  price=price, qty=qty))
        if not self.post_commit_slot_sticks:
            self.panel = empty_panel()
        self.pending_register = None

    # ------------------------------------------------------------ readers
    def _dialog_kind(self, source=None):
        kind = self.dialog
        if kind == "extension" and self.extension_vanishes:
            # seen once by await_dialog, then the game closes it again
            self._extension_seen += 1
            self.dialog = None
        return kind

    def _read_rows(self, source=None):
        self.n_rows += 1
        self._armed_fault("read_rows")
        fault = self.rows_fault.get(self.n_rows)
        if fault is not None:
            raise fault
        return [] if self.loading else list(self.rows)

    def _table_loading(self, source=None):
        return self.loading

    def _read_register_panel(self, source=None):
        return dict(self.panel)

    def _find_text(self, source, needle, region=None, min_conf=40.0):
        low = needle.casefold()
        if low == "register" and self.register_button_present:
            return [word("Register", (140, 300)), word("Register", REGISTER_XY)]
        if low == "refresh" and self.refresh_button_present:
            return [word("Refresh", REFRESH_XY)]
        if low == "waiting":
            return [word("Waiting", (800, 500))] if self.loading else []
        return []

    def _find_words(self, source, region=None, min_conf=40.0, scale=None):
        return [word("Confirmation", CONFIRM_XY, 31.0),
                word("Cancel", DISMISS_XY, 28.0)]

    def _dialog_button(self, source, w, min_conf=40.0):
        low = w.casefold()
        if self.dialog is None:
            return None
        if low == trade.DISMISS_WORD.casefold():
            return None if self.no_cancel_button else word(trade.DISMISS_WORD,
                                                           DISMISS_XY)
        if low == trade.CONFIRM_WORD.casefold():
            if self.no_confirm_button or self.dialog != "confirm":
                return None
            return word(trade.CONFIRM_WORD, CONFIRM_XY)
        if low == trade.RECEIPT_WORD.casefold():
            if self.dialog != "receipt":
                return None
            return word(trade.RECEIPT_WORD, RECEIPT_XY)
        return None

    def _trade_window_open(self, source=None):
        return self.trade_open

    def _register_tab_open(self, source=None):
        """The Register tab, which the bench treats as part of "window open".

        table_scrollable requires this as well as trade_window_open() and
        panel_covers_trade_area(): the wheel must be over the listings table,
        and the Purchase tab is a different page where scrolling is forbidden
        outright. The bench's open_trade_window puts the window on Register, so
        this follows trade_open. A suite modelling the wrong tab patches it.
        """
        return self.trade_open

    def _wait_for_table(self, timeout=20.0, poll=1.0):
        self.log("wait_for_table")
        return self.table_refreshes

    def _open_trade_window(self, timeout=15.0, verbose=True):
        self.log("open_trade_window")
        self.trade_open = True
        return True

    def _require_empty_work_tab(self, verbose=True):
        self.log("require_empty_work_tab")
        return self.work_tab_empty

    def _inventory_origin(self, source=None, retries=3):
        return self.origin

    def _active_inventory_tab(self, source=None, origin=None):
        return self.inventory_tab

    def _select_inventory_tab(self, tab, origin=None, timeout=5.0):
        self.log("select_inventory_tab", tab)
        self.inventory_tab = tab
        return True

    def _changed_slots(self, before, after, origin=None):
        return list(self.returned_slots)

    def _slot_centre(self, row, col, source=None):
        return (1700 + col * 74, 400 + row * 74)

    def _calibrate(self, verbose=True, save=True):
        self.log("calibrate")
        return True

    def _clear_shop_slot(self, timeout=15.0, verbose=True):
        self.log("clear_shop_slot")
        if self.panel.get("loaded"):
            self.panel = empty_panel()
        return True

    # -------------------------------------------------------- install/undo
    STUBS = {
        "grab": "_grab", "click": "_click", "ctrl_click": "_ctrl_click",
        "park_cursor": "_park", "focus_game": "_focus", "record": "_record",
        "dialog_kind": "_dialog_kind", "read_rows": "_read_rows",
        "table_loading": "_table_loading",
        "read_register_panel": "_read_register_panel",
        "find_text": "_find_text", "find_words": "_find_words",
        "dialog_button": "_dialog_button",
        "trade_window_open": "_trade_window_open",
        "register_tab_open": "_register_tab_open",
        "wait_for_table": "_wait_for_table",
        "open_trade_window": "_open_trade_window",
        "require_empty_work_tab": "_require_empty_work_tab",
        "inventory_origin": "_inventory_origin",
        "active_inventory_tab": "_active_inventory_tab",
        "select_inventory_tab": "_select_inventory_tab",
        "changed_slots": "_changed_slots", "slot_centre": "_slot_centre",
        "calibrate": "_calibrate", "clear_shop_slot": "_clear_shop_slot",
    }

    LAMBDAS = ("type_number", "press_escape", "scroll_wheel", "move_mouse",
               "make_dpi_aware", "release_modifiers", "keep_awake",
               "take_screenshot", "session_locked", "is_elevated",
               "find_game_window", "occupied_slots")

    def install(self):
        t = trade
        names = list(self.STUBS) + list(self.LAMBDAS) + ["time"]
        self._saved = {n: getattr(t, n) for n in names if hasattr(t, n)}
        if self._had_print:
            self._saved["print"] = t.print

        for public, private in self.STUBS.items():
            setattr(t, public, getattr(self, private))

        h = self
        t.type_number = self._type_number
        t.press_escape = self._press_escape
        t.scroll_wheel = lambda x, y, n, **k: h.log("scroll_wheel", x, y, n)
        t.move_mouse = lambda x, y: (h.log("move_mouse", x, y) or True)
        t.make_dpi_aware = lambda: None
        t.release_modifiers = lambda: h.log("release_modifiers")
        t.keep_awake = lambda enable=True: True
        t.take_screenshot = self._no_capture
        t.session_locked = lambda: (h.log("session_locked") or h.locked)
        t.is_elevated = lambda: h.elevated
        t.find_game_window = lambda: 0x1234
        t.occupied_slots = lambda *a, **k: []
        t.time = self.clock
        t.print = self._print          # trade's say() closures call print()
        return self

    def patch(self, name: str, fn):
        """Replace trade.<name> for the life of this harness, restorably."""
        if name not in self._saved:
            self._saved[name] = getattr(trade, name)
        setattr(trade, name, fn)
        return getattr(self, "_" + name, None)

    @staticmethod
    def _no_capture(*a, **k):
        raise AssertionError("take_screenshot() must never run in a test")

    def uninstall(self):
        for name, value in self._saved.items():
            setattr(trade, name, value)
        if not self._had_print:
            try:
                del trade.print
            except AttributeError:
                pass
        self._saved = {}

    def __enter__(self):
        return self.install()

    def __exit__(self, *exc):
        self.uninstall()
        return False


# --------------------------------------------------------------------------
# Tiny test runner
# --------------------------------------------------------------------------

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    ok = bool(condition)
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))
    print(("[  ok  ] " if ok else "[ FAIL ] ") + name
          + ("" if ok or not detail else f"\n           {detail}"))
    return ok


def note(name: str, detail: str) -> None:
    RESULTS.append((name, "NOTE", detail))
    print(f"[ note ] {name}: {detail}")


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def summary() -> int:
    failed = [r for r in RESULTS if r[1] == "FAIL"]
    notes = [r for r in RESULTS if r[1] == "NOTE"]
    print(f"\n{'-' * 74}")
    print(f"{len(RESULTS)} checks, {len(failed)} FAILED, {len(notes)} notes")
    for name, _, detail in failed:
        print(f"  FAIL {name}: {detail}")
    return 1 if failed else 0


def run(fn, *args, **kwargs):
    """Call fn, returning (value, exception)."""
    try:
        return fn(*args, **kwargs), None
    except BaseException as exc:            # noqa: BLE001 - that is the point
        return None, exc


def where(exc: BaseException) -> str:
    """The trade.py frames the exception passed through, innermost first."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__)
              if f.filename.lower().endswith("trade.py")]
    if not frames:
        return "<not in trade.py>"
    return " <- ".join(f"{f.name}:{f.lineno}" for f in reversed(frames))
