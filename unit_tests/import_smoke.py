"""Regression guard: the module imports, every global its functions read
exists, and apply_layout() is an identity at the reference layout.

py_compile cannot catch a deleted module-level name -- the reference lives
inside a function body and only fails at call time. This already caught two
real breakages: TRADE_REGION/POPUP_REGION deleted during the calibration
refactor, and VK_MENU being function-local while release_modifiers() read it
as a global.
"""

import ast
import sys

from pathlib import Path as _Path  # noqa: E402
_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

PATH = _ROOT / "trade.py"

import trade as mod  # noqa: E402  - the import is itself the first test

print("import: OK")

tree = ast.parse(open(PATH, encoding="utf-8-sig").read())
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def bound_names(func):
    """Names bound anywhere in `func`'s own body (not nested functions)."""
    names = {a.arg for a in ast.walk(func) if isinstance(a, ast.arg)}
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (*FUNCS, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


# Parent chain, so a nested function can see its enclosing scopes -- `say`,
# `verbose`, `dry_run` and friends are closures, not missing globals.
parent = {}
for node in ast.walk(tree):
    for child in ast.iter_child_nodes(node):
        parent[child] = node

module_names = set(dir(mod)) | set(dir(__builtins__)) | {"__file__", "__name__"}

missing = {}
for func in [n for n in ast.walk(tree) if isinstance(n, FUNCS)]:
    visible = set(module_names)
    node = func
    while node is not None:
        if isinstance(node, FUNCS):
            visible |= bound_names(node)
        node = parent.get(node)
    for ref in ast.walk(func):
        if isinstance(ref, ast.Name) and isinstance(ref.ctx, ast.Load):
            if ref.id not in visible:
                missing.setdefault(ref.id, []).append(f"{func.name}:{ref.lineno}")

print(f"\nglobals referenced but not defined: {len(missing)}")
for name, where in sorted(missing.items()):
    print(f"  MISSING {name:24} {where[:4]}")

geometry = sorted(set(mod._TRADE_FRAME_GEOMETRY) | set(mod._INVENTORY_FRAME_GEOMETRY))
absent = [n for n in geometry if not hasattr(mod, n)]
print(f"\ngeometry constants: {len(geometry)}, absent: {absent or 'none'}")

print("\n--- apply_layout at the reference layout must be an identity ---")
before = {n: getattr(mod, n) for n in geometry}
mod.apply_layout(mod.Layout(screen=mod.REF_SCREEN, origin=mod.REF_TRADE_ORIGIN,
                            scale=1.0))
drift = {n: (before[n], getattr(mod, n)) for n in before
         if getattr(mod, n) != before[n]}
print(f"  changed: {len(drift)}")
for n, (a, b) in drift.items():
    print(f"  DRIFT {n}: {a!r} -> {b!r}")
print(f"  TRADE_REGION        {mod.TRADE_REGION}   expected (10, 30, 1235, 1065)")
print(f"  POPUP_REGION        {mod.POPUP_REGION}   expected (500, 350, 2100, 1150)")
print(f"  DIALOG_BUTTON_MIN_X {mod.DIALOG_BUTTON_MIN_X}   expected 1200")

print("\n--- a 1920x1080 layout must stay on-screen and ordered ---")
small = mod.Layout(screen=(1920, 1080), origin=(8, 22), scale=0.75,
                   client=(0, 0, 1920, 1080))
mod.apply_layout(small)
bad = []
for name in geometry:
    value = getattr(mod, name)
    if isinstance(value, tuple) and len(value) == 4 and all(
            isinstance(v, int) for v in value):
        if value[2] <= value[0] or value[3] <= value[1]:
            bad.append((name, value, "inverted or empty"))
        if value[2] > 1920 or value[3] > 1080 or value[0] < 0 or value[1] < 0:
            bad.append((name, value, "off-screen"))
print(f"  regions checked, problems: {len(bad)}")
for name, value, why in bad:
    print(f"  BAD {name}: {value} ({why})")
print(f"  TRADE_REGION        {mod.TRADE_REGION}")
print(f"  DIALOG_BUTTON_MIN_X {mod.DIALOG_BUTTON_MIN_X}  "
      f"(Function column at {mod.LAYOUT.x(mod.REF_FUNCTION_COLUMN_X)})")
assert mod.DIALOG_BUTTON_MIN_X > mod.LAYOUT.x(mod.REF_FUNCTION_COLUMN_X), \
    "the dialog boundary must stay right of the Function column"

# Leave the module on the reference layout for anything that imports after us.
mod.apply_layout(mod.Layout(screen=mod.REF_SCREEN, origin=mod.REF_TRADE_ORIGIN,
                            scale=1.0))

failures = len(missing) + len(absent) + len(drift) + len(bad)
print(f"\nfailures: {failures}")
if failures:
    raise SystemExit(1)
print("all good")
