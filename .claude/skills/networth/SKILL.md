---
name: networth
description: Total net worth -- the Alz balance plus everything on the Register board valued at market price. Use when asked what the account is worth, total worth, holdings, or how much is on the board.
---

# Net worth

Run the tool and report what it prints:

```
py tools/networth.py
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

It reads the newest `src_1080p/logs/*_run.log` and touches nothing else. No
game, no clicking, no ledger.

## Where the numbers come from

- **The board** -- the row table the run prints, `1 Chaos Core Set X 84 x1
  60,285,875`. `seed()` prints one at launch; `board_report()` prints one
  at the end of **every pass** under `board after pass N:`, from the row
  model, with a per-item rows/units/listed summary. The last table in the
  log wins, so on current code the board is at most one pass old. A log
  with no `board after pass` block is from a run on older code: its only
  table is the launch one, a run up for hours has a board that old, and
  the total is not worth reporting as net worth; say so.
- **Value** -- since 2026-09-04 each row is valued at **what it is listed
  for**: a core row prints its unit price, so value is `price x qty`; a
  bundle row (`X N` in the name, qty 1) prints the whole row, so value is
  that figure and `listed/u` is it divided by the pack. The stock total
  therefore equals the board's own `listed` total, the freshest price the
  log has for each item. Before that date rows were valued at the launch
  market price, which drifted hours stale -- Chaos Sets read 738,500 at
  launch while listed at 799,6xx five hours later, 16M under.
- **Market** -- the `market prices:` block the run prints at startup, one
  line per favourite, shown beside `listed/u` **for reference only**. The
  last block in the log wins. It is the only per-item market read the run
  makes; it never re-measures.
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
  balance line is the board's own `balance now`. These are the one place
  the launch market price still sets a value, since the cores are on no
  row yet.

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
  moves constantly; the market column is from startup and can be hours
  stale, which is why it no longer sets the value.
- What it still cannot see: a row that sold but whose Alz the run has not
  collected yet (the game holds it until the row's turn in the pass). Until
  then it is on neither the board nor the balance, so the total dips by
  that row and comes back when it is collected.
- Value is the asking price, so it assumes every row clears at what it is
  listed for. The Register panel shows a 0.0% sales fee on this shop, so
  that is what a sale would pay.
- Rows the run could not read print as `UNREAD` and are listed separately,
  counted as **nothing**. The total is short by whatever they hold.
- An item with no market price shows `--` in the market column; its value
  is unaffected.
- `no balance line in this log` means the run never bought anything, so it
  never printed one. The total is then stock only -- say so rather than
  reporting it as net worth.

## Cross-check before trusting a number

The stock total should equal the `listed` figure on the board's own
per-item summary line, give or take the odd Alz the pass has since shaved
off each row. A wild gap means a row was parsed as a core when it is a
bundle or the other way round. `listed/u` far from `market` is not an
error -- it is the market having moved since launch, and the whole point of
valuing at the listed price.
