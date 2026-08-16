# src/

A fresh stack, built step by step, replacing nothing in `trade.py` yet.

**Calibration and opening the Agent Shop both work.**

## What runs

```
py src/calibration.py              measure this screen, write calibration.json
py src/open_inventory.py           focus the game, press I
py src/open_agent_shop_premium.py  inventory -> tab VIII -> right-click (1,7)
```

`calibration.py` assumes the game starts in its default state — nothing open —
and leaves it that way. It opens what it needs as it goes.

## Verified

Three consecutive runs from a **deleted** `calibration.json`, the fresh-monitor
path, produced byte-identical output:

```
tabs        pitch 69.57px   score 2.64
slot 1x7    [2425, 294]     slot 1x8 [2498, 294]
sort        (953, 194)
favourites  10 found, pitch 57.00, [651,1020]..[1164,1020]
```

Nine of nine positions land within 8px of the values the working scripts use,
and `open_agent_shop_premium` runs off the generated file in **218-225 ms**,
opening the Trade window every time.

Timing of the whole flow, once, per action:

| step | ms |
|---|---|
| `calibrate_inventory()` | 1850 |
| `calibrate_shop()` | 381 |
| park cursor (x2) | 501 |
| `ensure_inventory_open()` | 130 |
| `panel_open()` (one screenshot) | 31 |
| click tab | 54 |
| `right_click` | 3 |
| `tab_point` / `slot_point` | 0.0 |

Calibration is a one-off ~2.8s per monitor; opening the shop is ~250ms of real
work.

## Where things live

Nothing in `open_inventory.py` or `open_agent_shop_premium.py` is a literal
constant — not a position, not a Windows API number, not a duration. All of it
is read from `calibration.json`, so changing `action_gap` once changes it in
every script.

```
calibration.json
├── by_resolution
│   └── "2560x1440"   screen, game, alz_detect, inventory, shop
├── timing            action_gap, key_hold, focus_settle
├── input             VK_*, KEYEVENTF_*, MOUSEEVENTF_*, INPUT_STRUCT_SIZE
├── game              title_hint
└── game_facts        grid_size, agent_shop_tab, agent_shop_slot
```

Positions are per-resolution because a coordinate means nothing off the screen
it was measured on. An unmeasured resolution is **refused**, never approximated
from another one. Everything else is shared.

`calibration.py`'s own search regions are fractions of the game's client rect,
not pixels, so a monitor it has never seen still has somewhere to look.

## Known limits

- **Only 2560x1440 is proven.** The resolution keying and the fractional
  bootstrap are written and self-consistent, but the first run on a different
  monitor is where they get tested.
- **Only a default starting state is tested.** Calibration assumes nothing is
  open. A dirty state — shop already up, inventory on another tab — is
  untested, and the inventory tab matters because the tab strip's appearance is
  what the tab fit reads.
- **The sort dropdown anchors on whichever word survives OCR.** Its text is
  "Price: Low to High" and renders clipped, reading as `Price:High` + `to`, or
  a bare `to`, or nothing. All three runs anchored on `to` at (953, 194) — the
  last of four fallbacks. A future read landing on `Price` moves that point
  ~57px, to a different spot inside the same control. Fine to click, not a
  landmark to trust.
- `PARK`, where the cursor is left while measuring, sits 65px clear of the
  Trade window and 649px clear of the inventory. It is the one value that has
  to be somewhere nothing is, rather than somewhere something can be found, so
  it cannot be verified the way everything else here can.
