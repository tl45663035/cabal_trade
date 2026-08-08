"""Capture labelled golden frames for the buying and converting features.

    py unit_tests\\capture_goldens.py [SECONDS]

READ-ONLY. It grabs the screen and nothing else -- no clicks, no keys, no
scrolling -- so it is safe to run alongside a live trading session, which is
the point: the states worth capturing only occur while the script is working.

Frames are classified by what is actually on screen and saved under
unit_tests/corpus/goldens/<state>/, which is gitignored. A state is captured at
most CAP_PER_STATE times, and near-duplicates are skipped, so leaving it
running for an hour does not produce a thousand copies of an idle shop.

Why this exists: the suites for these two features lean on synthetic fixtures.
purchase_confirm -- the function buy_offer refuses a purchase on -- had no real
frame at all, so every assertion about it was made against word lists this
file's author invented. A fixture you wrote yourself cannot tell you the game
changed.
"""
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import trade as m  # noqa: E402

# Belt and braces. Nothing here calls an input primitive, but a future edit
# might, and this file is meant to be safe to run against a live session.
m.NO_INPUT = True

OUT = _ROOT / "unit_tests" / "corpus" / "goldens"
CAP_PER_STATE = 40
POLL = 1.5
# Two frames whose signature matches are near-duplicates; only the first is
# kept. The signature is deliberately coarse so a changed price or row still
# counts as new.
_seen: dict[str, set] = {}
_counts: dict[str, int] = {}


def classify(shot) -> list[tuple[str, str]]:
    """Every state this frame is an example of, as (state, signature).

    A frame can serve several suites at once -- a Purchase tab with a Confirm
    dialog over it is a fixture for both -- so this returns a list rather than
    picking one.
    """
    out = []

    # --- converting -------------------------------------------------------
    if m.vendor_shop_open(shot):
        tab = m.active_vendor_tab(shot)
        out.append((f"vendor_tab_{tab or 'unknown'}", f"tab={tab}"))
        buttons = m.mass_purchase_open(shot)
        if buttons:
            d = m.mass_purchase_details(shot)
            out.append(("convert_dialog",
                        f"{d.get('item')}|{d.get('qty')}|{d.get('qty_max')}"))
            # The states the readers were burned by: a maximum of 0 draws the
            # price line RED, and greyscale loses it entirely.
            if d.get("qty_max") == 0 or d.get("held") == 0:
                out.append(("convert_dialog_empty",
                            f"{d.get('item')}|{d.get('held')}"))

    # --- buying -----------------------------------------------------------
    if m.trade_window_open(shot):
        if m.purchase_tab_open(shot):
            rows = m.read_purchase_rows(shot)
            if rows:
                sig = "|".join(f"{r.name}:{r.price}" for r in rows[:3])
                out.append((f"purchase_rows_{len(rows)}", sig))
                # Rows carrying a pack marker are what the per-item arithmetic
                # is for; they are worth their own bucket.
                if any(m.pack_size(r.name) > 1 for r in rows):
                    out.append(("purchase_rows_packed", sig))
        confirm = m.purchase_confirm(shot)
        if confirm:
            out.append(("confirm_purchase",
                        f"{confirm.get('price')}|{confirm.get('text')[:40]}"))
        if m.register_tab_open(shot):
            out.append(("register_tab", "register"))

    # --- the inventory the counts are taken from --------------------------
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
        except Exception as exc:  # noqa: BLE001 - a capture must never disturb
            print(f"  (skipped a frame: {exc})", flush=True)
        time.sleep(POLL)

    print(f"\n{grabbed} frame(s) examined. Kept:")
    for state in sorted(_counts):
        print(f"  {state:28} {_counts[state]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
