---
name: networth
description: Total net worth -- the Alz balance plus everything on the Register board valued at market price. Use when asked what the account is worth, total worth, holdings, or how much is on the board.
---

# Net worth

Run the tool and report what it prints:

```
py tools/networth.py
```

It reads the newest `src_1080p/logs/*_run.log` and touches nothing else. No
game, no clicking, no ledger.

## Where the three numbers come from

- **Market prices** -- the `market prices:` block the run prints at startup,
  one line per favourite. The last block in the log wins, so a run that
  re-measures gets its newer prices.
- **The board** -- the row table the run prints, `1 Chaos Core Set X 84 x1
  60,285,875`. `seed()` prints one at launch; `board_report()` prints one
  at the end of **every pass** under `board after pass N:`, from the row
  model, with a per-item rows/units/listed summary. The last table in the
  log wins, so on current code the board is at most one pass old. A log
  with no `board after pass` block is from a run on older code: its only
  table is the launch one, a run up for hours has a board that old, and
  the total is not worth reporting as net worth; say so.
- **Alz** -- the most recent `balance now N` (end of each pass) or
  `balance after|before N` (around a buy) line, whichever is later.
- **Bought since that board** -- every `balance after ...; spent S bought
  P pack(s) = N core(s)` line after the last board table, named by the
  `TASK {"kind": "resupply", "core": ...}` line before it. A resupply runs at
  the start of a pass, so between the board and the next one the Alz line
  has already dropped by the spend while the cores sit in the bag or are
  being converted and listed, on no board yet. Without this block a call
  made mid-pass read low by exactly what was just bought (162M on
  2026-09-03 02:45). They are valued at the core's market price; if the
  run never read one, at what was spent. The block is empty when the last
  balance line is the board's own `balance now`.

## Set and Core are different items here

`profit_summary.key()` deliberately strips the word `Set` so a Core and its
Set match for cost accounting. **Do not reuse it for valuation.** With it,
`Force Core(Highest)` and `Force Core Set (Highest)` collapse to one key, the
later one wins, and every Core row gets priced at its Set price. That
understated the board by 22.3M the first time this tool ran. `networth.key()`
keeps them apart: it strips the `X N` pack suffix and nothing else.

The pack suffix still matters for quantity -- `Chaos Core Set X 84` listed at
qty 1 is 84 Sets, so units are `qty x pack`.

## Reading the result

- It is a **snapshot from when those lines were written**, not live. The board
  moves constantly; the market block is from startup unless the run
  re-measured. A long run's prices can be hours stale.
- What it still cannot see: a row that sold but whose Alz the run has not
  collected yet (the game holds it until the row's turn in the pass). Until
  then it is on neither the board nor the balance, so the total dips by
  that row and comes back when it is collected.
- Value is `units x market`, so it assumes every unit clears at the price the
  run last read. Real proceeds are lower: the board undercuts to sell, and
  whether the game's sales fee is deducted is unconfirmed.
- Rows the run could not read print as `UNREAD` and are listed separately,
  counted as **nothing**. The total is short by whatever they hold.
- An item with no market price is valued at what it is listed for instead,
  and said so under its own heading.
- `no balance line in this log` means the run never bought anything, so it
  never printed one. The total is then stock only -- say so rather than
  reporting it as net worth.

## Cross-check before trusting a number

A bundle row lists at roughly its market value, so `units x market` should
land within a few Alz of the listed price. Row 1 valuing at 60,285,960
against a listed 60,285,875 is right; a wild gap means the market block and
the board came from different times, or a name matched the wrong item.
