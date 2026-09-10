import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "trade_row1_speedup.db")

SEQUENCE = [("Chaos", 4, 3), ("FCH", 8, 7), ("FCHH", 2, 1),
            ("FCU", 6, 5), ("UCU", 10, 9)]
STOP = _ROOT / "STOP"


def load_before(ref: str):
    src = subprocess.run(["git", "show", f"{ref}:trade.py"], cwd=_ROOT,
                         capture_output=True, text=True, encoding="utf-8")
    if src.returncode != 0:
        raise SystemExit(f"could not read trade.py at {ref}: {src.stderr}")
    path = Path(tempfile.gettempdir()) / "trade_before_row1.py"
    path.write_text(src.stdout, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("trade_before", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["trade_before"] = module
    saved = sys.argv[:]
    sys.argv = ["trade_before"]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = saved
    return module


BASELINE = _ROOT / "unit_tests" / ".row1_baseline.json"


def load_baseline(ref: str) -> "dict | None":
    if "--rebaseline" in sys.argv or not BASELINE.exists():
        return None
    try:
        blob = json.loads(BASELINE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if blob.get("ref") != ref:
        return None
    return {k: (v[0], v[1]) for k, v in blob.get("runs", {}).items()}


def save_baseline(ref: str, runs: dict) -> None:
    try:
        BASELINE.write_text(json.dumps(
            {"ref": ref, "runs": {k: list(v) for k, v in runs.items()}},
            indent=2), encoding="utf-8")
    except OSError:
        pass


def run(module, label: str) -> dict:
    out = {}
    for name, set_slot, core_slot in SEQUENCE:
        if STOP.exists():
            print(f"  {STOP.name} present - stopping.")
            break
        module.CHAOS_SET_SLOT = set_slot
        module.CHAOS_CORE_SLOT = core_slot
        t = time.perf_counter()
        got = module.chaos_margin_now(verbose=False)
        took = time.perf_counter() - t
        out[name] = (took, got)
        shown = f"{got:,}" if got is not None else "None"
        print(f"  {label:<7} {name:<6} {took:6.1f}s -> {shown:>14}")
    return out


def main(ref: str = "HEAD") -> int:
    import trade as after

    print("=" * 80)
    print(f"loading BEFORE from {ref}...")
    before = load_before(ref)

    for module in (before, after):
        module.PREMIUM_ENABLED = True

    if not after.ensure_shop_ready(verbose=True):
        print("Could not open the Agent Shop.")
        return 2

    if not after.open_trade_window(verbose=False):
        print("Could not get back to the Register tab to calibrate.")
        return 2

    if not after.calibrate(verbose=False):
        print("Could not calibrate with the shop open.")
        return 2
    for module in (before, after):
        module.apply_layout(after.LAYOUT)

    if not after.open_purchase_tab(verbose=True):
        print("Could not reach the Purchase tab.")
        return 2
    if not after.set_purchase_sort_low_to_high(verbose=True):
        print("Could not confirm the Price: Low to High sort.")
        return 2

    print()
    print(f"Sequence: {' -> '.join(n for n, _, _ in SEQUENCE)}, one pass each.")
    print("Hands off the mouse.")
    print("=" * 80)

    b = load_baseline(ref)
    if b is None:
        print(f"  no cached baseline for {ref} - measuring it once")
        b = run(before, "BEFORE")
        save_baseline(ref, b)
    else:
        print(f"  BEFORE from cache ({ref}):")
        for n, (secs, ans) in b.items():
            shown = f"{ans:,}" if ans is not None else "None"
            print(f"  BEFORE  {n:<6} {secs:6.1f}s -> {shown:>14}  (cached)")
    a = run(after, "AFTER")

    shared = [n for n, _, _ in SEQUENCE if n in b and n in a]
    if not shared:
        print("\nNo complete measurements.")
        return 1

    print()
    print("SPEED  (one pass, wall clock per call)")
    print("-" * 80)
    print(f"  {'item':<8}{'BEFORE':>10}{'AFTER':>10}{'faster':>10}{'saved':>10}")
    for n in shared:
        bt, at = b[n][0], a[n][0]
        print(f"  {n:<8}{bt:>9.1f}s{at:>9.1f}s"
              f"{(bt / at if at else 0):>9.1f}x{bt - at:>9.1f}s")
    tb = sum(b[n][0] for n in shared)
    ta = sum(a[n][0] for n in shared)
    print("-" * 80)
    print(f"  {'ALL':<8}{tb:>9.1f}s{ta:>9.1f}s"
          f"{(tb / ta if ta else 0):>9.1f}x{tb - ta:>9.1f}s")

    print()
    print("MARGIN  (Alz per unit, Set minus Core)")
    print("-" * 80)
    print(f"  {'item':<8}{'BEFORE (min/unit)':>20}{'AFTER (row 1)':>16}"
          f"{'delta':>12}")
    for n in shared:
        bv, av = b[n][1], a[n][1]
        d = (av - bv) if (bv is not None and av is not None) else None
        print(f"  {n:<8}{(f'{bv:,}' if bv is not None else 'None'):>20}"
              f"{(f'{av:,}' if av is not None else 'None'):>16}"
              f"{(f'{d:+,}' if d is not None else '-'):>12}")
    print()
    print("  A non-zero delta means row 1 was NOT the cheapest per unit for")
    print("  that item -- the two are answering different questions, and row 1")
    print("  is the rule.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "HEAD"))
