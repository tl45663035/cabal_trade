# Minesta's Chaos Fighter Belt +15 listed at 455,980 (average 275,048,861), 2026-09-30 06:11:43

All from frames; the scores are calibration.slot_is_empty on the saved frames (threshold SLOT_OCCUPIED_BRIGHTNESS 42).

## 1. The tab-4 empty check cannot see the belt
Slot (1,1) disc brightness with the belt in it:
- 055721 00470 (06:09:57): 37.75 -> EMPTY. The belt had landed; the run stopped "tab 4 slot (1,1) is still empty 3s after the withdrawal".
- 061024 00002 (launch): 37.32 -> EMPTY.
- 061024 00033 (startup, before row 1): 49.88 -> occupied, so it chose (1,2). Arionell went there and back; fine.
- 061024 00044 (startup, before row 10): 35.52 -> EMPTY, so it chose (1,1) with the belt in it.
- Supervisor recovery 06:10:20 logged "verified: row 1 of tab 4 is clear" with the belt in (1,1)
  (its saved frame 061020_tab4_0slots scores 44.13), so the belt was never listed back.

## 2. Startup "actions at position 10" (calibration.py, withdraw-and-relist)
- 00045-00047: cancels row 10, Upgrade Core (Ultimate) x207 at 455,980.
- 00048: tab 4 shows the belt in (1,1) and an Upgrade Core icon in the other 63 slots.
- 00049: ctrl-click (1,1) loads the BELT. Panel suggests 273,771,417.
- It types `price = was`, 455,980, "the price row 10 was withdrawn at", without checking the loaded item
  or the panel's price.
- 00054: game asks "The price is at least 25% lower than the average ... Minesta's Chaos Fighter Belt + 15?
  Input 455,980, Average 275,048,861". The code accepts it unconditionally (00055/00056).
- The 207 Upgrade Cores withdrawn from row 10 were never listed back.

## 3. Knock-on
- Upgrade Core (Ultimate) then held 2 rows against 3 wanted, so a second resupply bought 310 Sets.
- Convert round 1 dialog 183/183; Sets 310 -> 127 (00093, 00096); no new tab-4 slot could appear,
  logged "Nothing converted". Rounds 2-3 dialog 0/0 (00113). Job ended "listed 0".
- Relist cancelled row 1 (Arionell); dialog stayed open; STOPPED 06:14:58.
- A manual cancel of the belt at 06:24 was refused: "Not enough space in the inventory".
  250 Upgrade Cores were then listed at 455,970 from tab 4 to free space. The belt is still listed.

## Other runs checked
Every startup relist since 09-28 (44) typed within 6% of the panel's price except this one; this is
the only underprice question accepted; no sale since 09-29 is under 70% of its weekly median.
