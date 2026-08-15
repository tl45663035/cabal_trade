"""record() must not be able to corrupt its own index entry.

Two separate failures came out of this one function, and the second was
caused by the fix for the first:

  1. `record("npc.found", label=...)` raised TypeError at argument-binding
     time -- before the internal try could swallow it -- and killed three
     consecutive cycles. Fixed by making label/shot positional-only.
  2. That fix let the same kwarg through as *context*, where entry.update()
     silently overwrote the label. Fifteen frames were written with a
     coordinate as their label and `npc.found` vanished from the index
     entirely. Silent corruption, strictly worse than the crash.

So this checks both the call sites and the function's own behaviour.
"""

import ast
import json
import sys
import tempfile
from pathlib import Path

from pathlib import Path as _Path  # noqa: E402
_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from PIL import Image  # noqa: E402

import trade as m  # noqa: E402

SRC = _ROOT / "trade.py"
# Keys the index is keyed on: context using these would clobber the entry.
RESERVED = ("file", "label", "at")
# The positional-only parameter names. Passing either as a KEYWORD is legal
# now but almost always a mistake: `record(..., shot=shot)` does not set the
# frame, it files the PIL object into the index as context and records
# whatever grab() last returned instead. Two call sites did exactly that.
POSITIONAL_ONLY = ("label", "shot")

fails = []


def check(cond, label):
    if not cond:
        fails.append(label)


# ---- 1. every call site binds, and none uses a reserved kwarg -------------
import inspect  # noqa: E402

tree = ast.parse(SRC.read_text(encoding="utf-8-sig"))
sig = inspect.signature(m.record)
sites = clashes = 0
for node in ast.walk(tree):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "record"):
        continue
    sites += 1
    kw = [k.arg for k in node.keywords if k.arg]
    try:
        sig.bind(*[None] * len(node.args), **{k: None for k in kw})
    except TypeError as exc:
        fails.append(f"line {node.lineno} does not bind: {exc}")
    bad = [k for k in kw if k in RESERVED]
    if bad:
        clashes += 1
        print(f"  line {node.lineno}: reserved kwarg {bad} "
              f"-- will be renamed to ctx_*, but say what you mean")
    misused = [k for k in kw if k in POSITIONAL_ONLY]
    check(not misused,
          f"line {node.lineno}: {misused} passed as a keyword -- these are "
          "positional-only, so this sets context, not the frame")
    check(isinstance(node.args[0], ast.Constant) if node.args else False,
          f"line {node.lineno}: label is not a literal")
print(f"call sites: {sites}   binding failures: "
      f"{len([f for f in fails if 'does not bind' in f])}   "
      f"reserved-kwarg uses: {clashes}")

# ---- 2. the function itself defends the three keyed fields ----------------
with tempfile.TemporaryDirectory() as tmp:
    m.RECORD_DIR = Path(tmp)
    m.RECORD_ENABLED = True
    m._record_full = False
    m._record_seq = 0
    shot = Image.new("RGB", (8, 8), "white")

    # The exact call that caused failure 2, plus every other collision.
    m.record("npc.found", shot, label="(1351, 248)")
    m.record("tab.before_register_click", shot, at="(392, 99)")
    m.record("some.step", shot, file="nonsense.png")
    m.record("all.three", shot, label="x", at="y", file="z")
    m.record("clean", shot, centre="(1,2)", row=3)
    m.record("dropped_none", shot, centre=None)

    lines = (Path(tmp) / "run_index.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(x) for x in lines if x.strip()]
    print(f"\nwrote {len(entries)} entries")
    for e in entries:
        print(f"  {e}")

    check(len(entries) == 6, f"expected 6 entries, got {len(entries)}")
    want = ["npc.found", "tab.before_register_click", "some.step",
            "all.three", "clean", "dropped_none"]
    got = [e.get("label") for e in entries]
    check(got == want, f"labels corrupted: {got}")

    for e in entries:
        check(e.get("file", "").startswith("run_") and e["file"].endswith(".png"),
              f"file field corrupted: {e.get('file')!r}")
        at = e.get("at", "")
        check(len(at) == 19 and at[4] == "-" and at[10] == "T",
              f"timestamp corrupted: {at!r}")

    # the caller's value must survive, just under a prefixed name
    check(entries[0].get("ctx_label") == "(1351, 248)",
          "colliding label value was lost instead of prefixed")
    check(entries[1].get("ctx_at") == "(392, 99)",
          "colliding at value was lost instead of prefixed")
    check(entries[3].get("ctx_label") == "x" and entries[3].get("ctx_at") == "y"
          and entries[3].get("ctx_file") == "z", "three-way collision lost data")
    check(entries[4].get("centre") == "(1,2)" and entries[4].get("row") == 3,
          "ordinary context did not survive")
    check("centre" not in entries[5], "a None context value was written")

    # ---- 3. recording must never raise, whatever it is handed ------------
    for bad_shot in (object(), "not an image", 42):
        try:
            m.record("hostile", bad_shot)          # type: ignore[arg-type]
        except Exception as exc:                   # noqa: BLE001
            fails.append(f"record raised on {type(bad_shot).__name__}: {exc!r}")
    print("\nhostile shot values: no exception raised")

print(f"\nfailures: {len(fails)}")
for f in fails:
    print(f"  FAIL {f}")
raise SystemExit(1 if fails else 0)
