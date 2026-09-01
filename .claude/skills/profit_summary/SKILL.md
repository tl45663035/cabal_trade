---
name: profit_summary
description: Report the last 7 days a day at a time, then every run launched since midnight, each run closed on its own stock -- sold lots at what they made, unsold lots at the price they were bought against.
---

# Profit summary

Run the tool and report what it prints:

```
py tools/profit_summary.py
```

## Always report both blocks, in this order

The tool prints two blocks. Report **both, every time**, whatever was asked
for -- "profit", "how are we doing", "today", a bare `/profit_summary`:

1. **LAST 7 DAYS**, one row per day: profit, realised, assumed, units,
   margin. Give every day its own line including the zero days, then the
   7-day total. Do not collapse it to a total, do not drop the empty days,
   and do not skip the block because the question was about today.
2. **The day summary** that follows -- per-item lines, the Cores/Chaos
   split, the total, `by run`, what was sold but not bought, and the live
   run's open stock.

The 7-day block comes first. The day is read against it, not on its own.

It reads `src_1080p/sales.db` read-only, so it is safe while a run is in
progress.

## How a run is closed

Every run is its own book. Nothing crosses from one run to the next.

- **Stock the run did not buy does not count for it**, even when the run
  sells it. That stock was closed by the run that bought it.
- **Every purchase carries `expect`**: the unit price the core was selling
  at when it was bought (`sells_at` in `buy.py`, written by
  `ledger.bought`). The margin is known the moment the stock is bought.
- **A sale is matched oldest-lot-first against what the same run bought.**
  Profit on those units is what the collection actually paid minus what
  those lots cost. That is **realised**.
- **When the run ends, every lot it still holds is taken as sold at its
  `expect`.** That is **assumed**. It closes the run.
- **The live run** is closed the same way, with its open stock listed
  separately under `open on the live run`, so realised and assumed can be
  told apart. `live` means the run's log was written in the last 10
  minutes and has no `ran for` line.

`profit = realised + assumed`. `margin` is profit over realised revenue plus
expected revenue.

## Ledger conventions that bite

- Purchase `qty` is in cores. Sale `qty` is in units too, already expanded
  from bundles -- `Chaos Core Set X 165` books as 167 units, not 1. Never
  scale a sale by the pack in its name.
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
  Fixed or not, under this model the stock simply stays "held" and closes
  at `expect` -- the profit is counted, at the bought-against price rather
  than the real one.
- **Bundle unit counts** come from `round(price / market_unit)`, not the
  `X N` in the name, so a bundle can book a few units off.
- Whether the game's sales fee is inside `proceeds` is unconfirmed; if it
  is not, real margins are lower.

## Reading the result

- `by run` gives units, realised, assumed, profit, hours and profit an
  hour. **Hours are launch to last trade** from the ledger, so a run still
  going is short by whatever it has not traded in yet.
- A run with big `assumed` and small `realised` bought late and was stopped
  before it sold; the profit is what the board said it would make, not what
  it made.
- `sold by a run that did not buy it` is information only. Big numbers
  there after a restart are normal: the previous run's stock clearing.

## Do not

- Do not open `sales.db` for writing. It is the live ledger and is gitignored.
- Do not hardcode a date. It caught fire once: `SINCE` was pinned to
  `2026-08-20T08:00:00` and silently reported 52 hours under a "SINCE 08:00"
  heading.
- Do not carry lots across runs, however tempting. The next run seeing the
  previous run's stock on the board is expected; it is already closed.
