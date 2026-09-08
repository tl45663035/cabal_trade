---
name: profit_summary
description: Report the last 7 days a day at a time, then every run launched since midnight, each run closed on its own stock -- sold lots at what they made, unsold lots at the price they were bought against.
---

# Profit summary

Run the tool and report what it prints:

```
py tools/profit_summary.py
```

## Run it and hand over the output. Nothing else.

The whole job is: run the command, paste what it printed **verbatim inside
one fenced code block**, stop. No markdown tables, no re-typing the numbers
into another layout, no bold, no per-block headings of your own -- the tool
already formatted it, and re-typing it is what made the answer take minutes
to arrive when the terminal had shown it in seconds. **Do not add a
reading, a comparison with an earlier call, a "what moved" paragraph, a
market comment or advice.** The notes below exist so the numbers can be
explained when the user asks a question about them -- they are not a
licence to volunteer analysis after the output.

## Always report all four blocks, in this order

The tool prints four blocks. Report **all four, every time**, whatever was asked
for -- "profit", "how are we doing", "today", a bare `/profit_summary`:

1. **LAST 7 DAYS**, one row per day: hours, profit, realised, assumed, units,
   margin (**hours** is second: how long a run was alive that day, midnight to midnight,
   from the run logs' launch stamp and `ended HH:MM:SS` line, the live run
   to now) and **an hour** (the day's profit over those hours). Give every
   day its own line including the zero days, then the 7-day total. Do not
   collapse it to a total, do not drop the empty days, and do not skip the
   block because the question was about today. A lot sits on the day it
   was bought and hours sit where they fall, so stock bought late and sold
   after midnight shows its profit on the day it was bought.
2. **The day summary** that follows -- per-item lines, the Cores/Chaos
   split, the total, `by run`, what was sold but never bought, what left
   the board with no booked sale, and what is still on the board.

3. **ROWS** -- the live run's latest board, from the newest run log: the
   `board after pass N` table (index, name, qty, bought/u, listed/u, margin,
   row price), the per-item rows/units/listed summary, `balance now` and the
   pass line. Before any pass has finished it is the launch table. Quote it
   row by row; it is the "rows summary" the user asks for. It ends with the
   net worth: stock at its listed price, anything bought since that board
   was printed (paid for, not on a row yet), the **latest** balance line in
   the log (`balance now|after|before`, whichever came last -- newer than
   the board's own `balance now` when a resupply has run since) and
   `NET WORTH`, the same figures `tools/networth.py` prints, from
   `networth.summary()`, then `PROFIT IF SOLD`, the sum of the board's
   **profit if sold** column. That column is added to every board row by
   `row_total()`: `(listed/u - bought/u) x units`, a bundle row's units
   being its `X N` pack. A row whose `bought/u` is `-` (stock this run did
   not buy, so no cost) prints `-` and is left out of the total, and the
   line under it says how many such rows there are.

4. **MARKET** -- the live run's latest pass table, one line per core:
   rows held inside the counted range, `buy/u` (what a unit costs on the
   Purchase tab), `sell/u` (what the other half of the pair lists for),
   `margin` (sell minus buy, the figure `rows_by_margin` is fed), `margin %`
   (over sell), `wants` (rows that margin is worth) and `short?`. Since the
   driver started printing `buy/u` and `sell/u` in the pass table the prices
   are from that very pass; a log from before that prints only the margin
   each pass, so the tool fills the prices from the last `A p - B q = d`
   line the run printed for that core (its last resupply decision) or, if
   it never priced it, from the launch `market prices:` block, and the
   `priced` column says which. Only the margin is fresh in that case.

The 7-day block comes first. The day is read against it, not on its own.

It reads `src_1080p/sales.db` read-only, so it is safe while a run is in
progress.

## How the book is closed

One book, since 2026-09-08. Every purchase is a lot; a lot belongs to the
day it was bought and to the run that bought it, and the runs only share
it.

- **A sale is matched oldest-lot-first against every lot bought before
  it, whichever run sold it.** Profit on those units is what the collection
  actually paid minus what those lots cost. That is **realised**. A
  restart no longer breaks the chain: the next run selling the previous
  run's stock realises it at the real price, on the previous run's line.
  Before this the book was per run, the inheriting run's sales went under
  `sold by a run that did not buy it`, and the held stock was closed at
  `expect` -- the day's assumed ran 60M over the board's own `PROFIT IF
  SOLD` on 2026-09-08.
- **Stock still on the board is valued at the price it is listed at**, the
  units-weighted `listed/u` for the item on the newest run log's last
  board (`networth.read`). Only stock on no board falls back to `expect`,
  the unit price the core was selling at when it was bought (`sells_at` in
  `buy.py`, written by `ledger.bought`). That is **assumed**, and the day
  summary lists it under `still on the board from today's stock`.
- **The board is the truth of what is held.** The ledger misses sales (a
  row that sells while being cancelled, bundle rounding), so the book would
  hold Chaos Sets for ever -- 5,000 phantom units by 2026-09-08. At each
  midnight the book is checked against the last `board after pass` table
  printed before it (its time is the run's launch plus the budget minus the
  `minute(s) left` on the pass line) and again, now, against the newest
  board plus what was bought since it. Whatever the book holds beyond the
  board left without a booked sale; the oldest such lots close at the
  median booked sale price for the item on that day. Those units are also
  **assumed**, and print under `off the board with no sale in the ledger`.
  Logs from before 2026-09-04 print no `board after pass`, so the first
  checkpoint is 2026-09-04 23:51.
- The book is loaded from 7 days before the 7-day window so a sale at the
  window's edge finds the lots it belongs to; lots bought before the window
  are matched but never reported.
- **A sale with no lot left to match** is stock the script never bought,
  or a mis-booked sale: `sold today but matched to no lot the script
  bought`. The 20,000-unit core sales booked on 2026-09-06 to 08 are such
  rows; they consume every open lot of the item at their unit price and
  the rest shows here.
- `live` on a `by run` line means the run's log was written in the last
  10 minutes and has no `ran for` line.

`profit = realised + assumed`. `margin` is profit over realised revenue plus
what the held and gone stock is valued at.

## Ledger conventions that bite

- Purchase `qty` is in cores. Sale `qty` is in units too, already expanded
  from bundles by `round(proceeds / market_unit)` -- `Chaos Core Set X 165`
  books as 167 units, not 1. The tool corrects it to the name's `X N` times
  the bundles that qty amounts to (`units_sold`), because a Set sells above
  the Core and the rounding drifts a few percent; a bundle whose ledger name
  carries no pack keeps the booked qty.
- Revenue is `proceeds`, the balance delta the collection actually showed.
  Fall back to `price x qty` only when it is NULL.
- `Force Core Set (High) X 435` and `Force Core(High)` are the same item:
  strip the pack suffix and the word `Set`.
- Purchases from before 2026-09-01 18:20 have no `expect` (the column was
  added then). They close at the day's median sale price for the item, or
  at cost if the item never sold that day, and the tool says how many units
  that touched. `ledger.print_run_profit` at the end of a run closes those
  at cost.

## Known gaps in what the ledger records

- **A row that sells while it is being cancelled is collected but not
  booked** (`driver.py` `SlotNeverFilled` path calls `receive` after
  `note_cancel` dropped the slot, so `_book` has nothing to book against).
  The midnight and now checks against the board catch the stock; it
  closes at the day's median booked sale price, not the real one.
- **Bundle unit counts** in the ledger come from `round(price /
  market_unit)`, not the `X N` in the name; the tool re-derives them from
  the name where it has one, but some sale rows are booked without the
  pack (`Chaos Core Set`, 174 units for an X 162 on 2026-09-04) and those
  stay a few percent off.
- The Register panel shows `Sales Fee (%0.0%)` on this shop (screen,
  2026-09-04), and `proceeds` equals the price the game's chat reports the
  row sold for, so revenue is the full sale price.

## Reading the result

- `by run` gives units, realised, assumed, profit, hours and profit an
  hour, on the stock each run bought, whoever sold it. **Hours are launch
  to last trade** from the ledger, so a run still going is short by
  whatever it has not traded in yet.
- A run with big `assumed` and small `realised` bought stock that is still
  on the board; the profit is what the board asks for it now, not what it
  made.
- The day's `assumed` on stock still on the board should sit close to the
  board block's `PROFIT IF SOLD`; the gap is stock bought on an earlier
  day that is still listed.

## Do not

- Do not open `sales.db` for writing. It is the live ledger and is gitignored.
- Do not hardcode a date. It caught fire once: `SINCE` was pinned to
  `2026-08-20T08:00:00` and silently reported 52 hours under a "SINCE 08:00"
  heading.
- Do not close the book per run again. Runs share one book; a lot's profit
  belongs to the day and run that bought it whoever sells it.
