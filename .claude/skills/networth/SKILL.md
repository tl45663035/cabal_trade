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
- **The board** -- the row table the run prints when it seeds, `1 Chaos Core
  Set X 84 x1 60,285,875`. The last table in the log wins.
- **Alz** -- the most recent `balance after N` line.

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
