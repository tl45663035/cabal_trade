import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import trade as m

m.NO_INPUT = True

OUT = _ROOT / "unit_tests" / "corpus" / "goldens"
CAP_PER_STATE = 40
POLL = 1.5
_seen: dict[str, set] = {}
_counts: dict[str, int] = {}


def classify(shot) -> list[tuple[str, str]]:
    out = []

    if m.vendor_shop_open(shot):
        tab = m.active_vendor_tab(shot)
        out.append((f"vendor_tab_{tab or 'unknown'}", f"tab={tab}"))
        buttons = m.mass_purchase_open(shot)
        if buttons:
            d = m.mass_purchase_details(shot)
            out.append(("convert_dialog",
                        f"{d.get('item')}|{d.get('qty')}|{d.get('qty_max')}"))
            if d.get("qty_max") == 0 or d.get("held") == 0:
                out.append(("convert_dialog_empty",
                            f"{d.get('item')}|{d.get('held')}"))

    if m.trade_window_open(shot):
        if m.purchase_tab_open(shot):
            rows = m.read_purchase_rows(shot)
            if rows:
                sig = "|".join(f"{r.name}:{r.price}" for r in rows[:3])
                out.append((f"purchase_rows_{len(rows)}", sig))
                if any(m.pack_size(r.name) > 1 for r in rows):
                    out.append(("purchase_rows_packed", sig))
        confirm = m.purchase_confirm(shot)
        if confirm:
            out.append(("confirm_purchase",
                        f"{confirm.get('price')}|{confirm.get('text')[:40]}"))
        if m.register_tab_open(shot):
            out.append(("register_tab", "register"))

    origin = m.inventory_origin(shot)
    if origin:
        tab = m.active_inventory_tab(shot)
        filled = len(m.occupied_slots(shot, origin))
        out.append((f"inventory_tab_{tab or 'unknown'}", f"{tab}:{filled}"))
    return out


def save(shot, state: str, signature: str) -> bool:
    seen = _seen.setdefault(state, set())
    if signature in seen:
        return False
    if _counts.get(state, 0) >= CAP_PER_STATE:
        return False
    seen.add(signature)
    n = _counts.get(state, 0) + 1
    _counts[state] = n
    folder = OUT / state
    folder.mkdir(parents=True, exist_ok=True)
    shot.save(folder / f"{state}_{n:03d}.png")
    return True


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 900.0
    deadline = time.monotonic() + seconds
    print(f"Capturing golden frames for {seconds / 60:.0f} min into {OUT}")
    print("READ-ONLY: no clicks, no keys. Safe alongside a live run.\n")
    grabbed = 0
    while time.monotonic() < deadline:
        try:
            shot = m.grab()
            grabbed += 1
            for state, signature in classify(shot):
                if save(shot, state, signature):
                    print(f"  + {state:26} ({_counts[state]}/{CAP_PER_STATE})",
                          flush=True)
        except Exception as exc:
            print(f"  (skipped a frame: {exc})", flush=True)
        time.sleep(POLL)

    print(f"\n{grabbed} frame(s) examined. Kept:")
    for state in sorted(_counts):
        print(f"  {state:28} {_counts[state]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
