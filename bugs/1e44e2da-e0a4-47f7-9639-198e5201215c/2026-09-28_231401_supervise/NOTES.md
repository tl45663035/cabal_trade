# trung crash loop, 2026-09-28 23:35 to 2026-09-29 00:20 (code d0ad626)

## 1. Stale read after Refresh clicked a sold row (the crash loop)
- refresh_settle is 0.05 s; the read after Refresh sees the table from before the refresh lands.
- Since 7b6f06c ("relist overlaps"), `_cancel` reuses relist_one's read when the view did not move,
  so nothing re-reads the row before clicking Change.
- 000956: at 00:12:51.68 (frame 00091) Divine Stone x2 in row 1 had already sold (sale popup on screen)
  but the table still read On Sale / Change. The read took it for Change. Frame 00092 (00:12:52.54)
  shows the refreshed row: Complete / Receive. 00:12:55 (00094) the run clicked the button, which was
  Receive, and Confirm Receipt opened.
- The run clicked Cancel at the cancel dialog's learned point (1101, 634); Confirm Receipt's Cancel is at
  (1101, ~654), so it missed. No Confirmation -> STOPPED with the dialog open (00096).
- 001352 / 001503: the Purchase tab would not open behind the dialog -> 3 short runs -> relog.
- The supervisor's "dialog with Cancel -> Cancel" used the same (1101, 634) and missed three times;
  Escape did not reach the menu; relog refused x3; supervisor stopped 00:20:27
  (supervise_frames 2026-09-29_001728_found.png, 001809_reset.png).
- Fix on trung, not yet pushed (d95abf6): relist_one reads the button again right before the relist;
  Receive/Register -> start the row over once (collect path), twice -> stop. Supervisor closes a
  Confirm Receipt at its own Cancel: (cancel.x, receipt seat y) = (1101, 653).

## 2. Panel loaded but read as empty (231419 Arionell 23:35, 233747 UC(U) 00:07)
- After the cancel, ctrl-click (1,1) loaded the item (233747 frame 01905: icon, 467,575, QTY 1/10) but
  the run read "0 of the three price places" and "loaded nothing", typed the price anyway, and misread
  net sales: 4,575,340 read as 44,575,340; 312,185,712 read as 312,131,218,571. STOPPED.
- Not fixed. 231419 has no frames left (its dead-run folder was pruned).
