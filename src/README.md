# src/

A fresh stack, built step by step, replacing nothing in `trade.py` yet.

**Verified: calibration and open shop works.**

## What runs

```
py src/calibration.py              measure this screen, write calibration.json
py src/open_inventory.py           focus the game, press I
py src/open_agent_shop_premium.py  inventory -> tab VIII -> right-click (1,7)
```

`calibration.py` starts from the game's default state — nothing open — opens
what it needs, and puts it back. The two can be chained:

```
py src/calibration.py; py src/open_agent_shop_premium.py
```

## Verified

Fresh runs from a deleted `calibration.json`, on a **packed** inventory tab —
the case that used to fail — give 8 of 8 positions within 8px of the values the
working scripts use, byte-identical across three runs. The chained command
above then opens the Trade window, three times out of three.

```
tab I  [1958, 222]     tab VIII [2445, 222]
slot (1,1) [1986, 294]  (1,7) [2426, 294]  (8,8) [2499, 808]
sort dropdown (953, 194)
favourites 10 found, pitch 57.00, [651,1020]..[1164,1020]
```

Calibration is a one-off, a few seconds. Opening the shop, 10 runs per setting,
Escape and 500ms between trials:

| `action_gap` | mean | opened |
|---:|---:|---:|
| 500 ms | 1119.9 ms | 10/10 |
| 200 ms | 519.7 ms | 10/10 |
| 100 ms | 320.7 ms | 10/10 |
| **50 ms** | **219.5 ms** | 10/10 |
| 20 ms | 157.7 ms | 10/10 |

Total is `120ms + 2 x action_gap`: two gaps, one after `I` and one after the tab
click. The fixed 120ms is almost all `panel_open`, which takes a full-screen
screenshot to confirm the panel is up before anything gets clicked. Nothing here
is variable — 3.5ms of spread across a run of 10.

At 50ms it is 219.6ms mean from a fresh process, 10/10 open. 20ms also passed
10/10 but buys 60ms for no margin, so the setting stays at 50.

## How it decides where things are

**Only the anchor is measured.** Once the Inventory panel is open its geometry
is fixed — slot pitch, tab spacing, where the grid starts — and none of it
depends on what is in the bag. The only thing that varies is where the *panel*
is, and the Alz balance says that. Everything else is placed from offsets:

```
slot_one   [-496, -596]     anchor -> centre of slot (1,1)
slot_pitch [73.3, 73.4]
tab_one    [-524, -668]     anchor -> centre of tab I
tab_pitch  69.6
```

The anchor is `(alz_right, alz_top)` — the *right* edge, because the balance is
right-aligned, so its left edge moves with the size of the number.

An earlier version fitted the grid by periodicity every run. It read the slot
borders *through* the item art and failed exactly as that predicts: a packed
tab put the columns 62px out where a sparse tab was exact. It is gone.

**Every toggle is press → verify → press again if wrong.** `I` and the Agent
Shop key are both toggles, and a run that fails part-way leaves them in the
opposite state, so the next run closes what it meant to open.

**One implementation of each reader.** The balance is found by
`calibration.find_alz` and nowhere else. There used to be a second copy in
`open_agent_shop_premium`, and the two drifted the moment one was fixed —
calibration reporting "Inventory already closed" while the other reported
"inventory already open", one second apart on the same screen.

## Where things live

Nothing in `open_inventory.py` or `open_agent_shop_premium.py` is a literal
constant — not a position, not a Windows API number, not a duration. All of it
comes from `calibration.json`, so changing `action_gap` once changes it
everywhere.

```
calibration.json
├── by_resolution
│   └── "2560x1440"   screen, game, alz_detect, inventory, shop
├── timing            action_gap, key_hold, focus_settle
├── input             VK_*, KEYEVENTF_*, MOUSEEVENTF_*, INPUT_STRUCT_SIZE
├── game              title_hint
├── game_facts        grid_size, agent_shop_tab, agent_shop_slot
└── panel_layout      slot_one, slot_pitch, tab_one, tab_pitch
```

Positions are per-resolution because a coordinate means nothing off the screen
it was measured on. An unmeasured resolution is **refused**, never approximated
from another. Everything else is shared.

**`src/calibration.json` is committed**, which is why the keying matters — a
machine that is not 2560x1440 reads its own section or is refused, so the file
is safe to share. The root `calibration.json` is trade.py's, is flat, records
no resolution, and stays ignored.

It was ignored until 2026-08-16, by a first-commit rule written for the root
file with no leading slash. That hid a real fault rather than a cosmetic one:
`timing.action_gap` sat at 0.5 against a `DEFAULTS` of 0.05 — 10x every gap in
every script — and nothing could notice, because the one thing that would have
caught it is a diff against the committed value.

`calibration.py`'s own search regions are fractions of the game's client rect,
not pixels, so a monitor it has never seen still has somewhere to look.

## Known limits

- **Only 2560x1440 is proven.** The resolution keying and the fractional
  bootstrap are written and self-consistent, but the first run on a different
  monitor is where they get tested.
- **`panel_layout` was measured here, once.** If the game's UI scale changes,
  those offsets are wrong and nothing will notice — the only check is that
  placed positions land inside the game window.
- **The sort dropdown anchors on whichever word survives OCR.** Its text is
  "Price: Low to High" and renders clipped, reading as `Price:High` + `to`, or
  a bare `to`, or nothing. It currently anchors on `to` at (953, 194), the last
  of four fallbacks. A read landing on `Price` moves that point ~57px, to a
  different spot in the same control. Fine to click, not a landmark to trust.
- **`PARK`**, where the cursor is left while measuring, has 65px of clearance
  from the Trade window and 649px from the inventory. It is the one value that
  has to be somewhere nothing is, rather than somewhere something can be found,
  so it cannot be verified the way the rest can.
