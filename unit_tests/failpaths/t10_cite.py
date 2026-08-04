"""Resolve the exact line numbers cited in the report, against the live file."""

import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent.parent.parent
_sys.path.insert(0, str(_ROOT))
import hashlib
import re
from pathlib import Path

SRC = Path(_ROOT / "trade.py")
raw = SRC.read_bytes()
lines = raw.decode("utf-8-sig").splitlines()
print(f"trade.py {len(raw)} bytes, {len(lines)} lines, "
      f"sha256 {hashlib.sha256(raw).hexdigest()[:12]}\n")

CITES = {
    "D1 table-refresh exit after a committed cancel":
        r'"The table did not finish refreshing after the cancel',
    "D1b the sibling exit that DOES warn":
        r"record\(\"relist\.stranded\"",
    "D2 'still open => the game refused it'":
        r'say\("The confirmation dialog is still open',
    "D3 cancel.aborted written with committed=":
        r'record\("cancel\.aborted"',
    "D3b the observation that contradicts it":
        r"still = dialog_kind\(grab\(\)\)",
    "D4 qty cross-check guard (fails open on qty_max=None)":
        r"if expect_item and expect_qty is not None and panel\[.qty_max.\] is not None:",
    "D5 dead report.update":
        r"report and report\.update\(qty_disagreement",
    "D6 require_empty_work_tab":
        r"^def require_empty_work_tab\(",
    "D6b its silent False returns":
        r"say\(f\"Inventory tab \{WORK_TAB\} is not empty",
    "D7 locked-session break with no record":
        r"say\(\"The workstation is locked - screen capture and input are",
    "D8 run_sequence returns True for an empty list":
        r"say\(f\"\\nAll \{len\(actions\)\} action\(s\) completed\.\"\)",
    "D8b run_loop counts it a success":
        r"succeeded \+= 1",
    "D9 cancel commit flag set after the click":
        r"^        click\(\*confirm\.centre\)",
    "D9b register commit flag":
        r'report\["committed"\] = True',
    "D10 cancel_item's only handler":
        r"^    except Aborted as exc:",
    "D11 focus_game swallows PermissionError":
        r"^    except PermissionError:",
    "D11b the elevation gate (main only)":
        r"if clicking and not is_elevated\(\)",
    "park_cursor in cancel_item's preconditions":
        r"^            park_cursor\(\)$",
    "record('inventory.before_cancel')":
        r'record\("inventory\.before_cancel"',
    "record('cancel.before_change')":
        r'record\("cancel\.before_change"',
    "move_mouse -> PermissionError in park_cursor":
        r"^        raise PermissionError\(CURSOR_BLOCKED_HINT\)",
    "record() swallows every failure":
        r"except Exception:  # noqa: BLE001 - recording is never worth a failure",
    "run_loop PermissionError handler":
        r"^            except PermissionError as exc:",
    "run_loop generic handler":
        r"^            except Exception as exc:",
    "run_sequence does NOT catch PermissionError":
        r"^        except Aborted as exc:",
}

for label, pattern in CITES.items():
    rx = re.compile(pattern)
    hits = [i + 1 for i, line in enumerate(lines) if rx.search(line)]
    print(f"{label:52} {hits[:6] if hits else 'NOT FOUND'}")
