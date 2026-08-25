---
name: profit_summary
description: Report trading profit for every run launched since midnight, counting only cores and Chaos the script both bought and sold. Use when asked for profit, takings, margin, or how the trading is doing today.
---

# Profit summary

Run the tool and **paste its whole output back, verbatim, in a code block**,
before saying anything about it:

```
py tools/profit_summary.py
```

The full table is the answer. Summarising it in prose drops things the user
came for -- most of all the `by run` block and the **Alz an hour** line under
it, which is the headline number and has been asked for twice after being
buried in a sentence. Paste first, then comment on it if there is anything
worth adding.

It reads both ledgers read-only, so it is safe while a run is in progress:
`sales.db` at the repo root, written by `trade.py`, and `src/sales.db`, written
by `src/`. They are pooled into **one** summary -- the same stock in the same
game, so a Core one script bought and the other sold matches. Paths are derived
from where the tool sits, so it works on any machine.

## What it counts

Every run **launched since 00:00 today**, up to the moment it is called. The
filter is on the ledger's `run` column, not on transaction time, so a run that
started before midnight is left out even if it traded after.

Only units the script **both bought and sold within those runs** are counted.
Purchases are matched oldest-first against sales of the same item, so a sale is
priced against what that stock actually cost rather than a rolling average.

## Ledger conventions that bite

These are not consistent between the two tables. Get them wrong and the numbers
are nonsense rather than slightly off.

| ledger | `purchases` qty | `sales` qty |
|---|---|---|
| root `sales.db` | cores, already expanded | whatever was listed -- `Chaos Core Set X 261`, qty **1** |
| `src/sales.db` | cores | units, already expanded |

So a sale in the root ledger needs `qty x pack-from-the-name` and a sale in
`src/sales.db` must not be scaled -- `src` books a chaos bundle in Sets. The
tool carries a flag per ledger for this; getting it backwards double-counts and
turned a chaos line into minus 195% once. Purchases are never scaled. The
pack suffix on a purchase name is unreliable — `Force Core Set (Ultimate) X 1`
was logged with qty 64.

Take revenue from `proceeds`, which is a total under either convention, and
fall back to `price x qty` only when it is NULL. Deriving revenue from `price`
booked one Chaos sale as 1 unit at a 99.6% margin, because `price` there was
the whole pack.

`Force Core Set (High) X 435` and `Force Core(High)` are the same item for
matching: strip the pack suffix and the word `Set`.

## What it deliberately leaves out

- **Sold but not bought by those runs.** Stock predating them has no cost in the
  window, so counting it would book its whole price as profit. Listed
  separately, never in the totals.
- **Bought but not yet sold.** Reported as capital tied up, not as a loss.

An earlier version averaged all purchases into a per-item cost and applied it to
every sale. That inflated the day by about a third and made Chaos look like a
1.1% line when the matched figure for the same day was 8.2%.

## Reading the result

- `by run` splits matched profit across the runs in the window, with the hours
  each ran and what it earned an hour, and a rate for the day underneath.
- **The hours are launch to last trade**, taken from the ledger: the `run`
  column is the launch time and `MAX(at)` is the last thing that run bought or
  sold. A run still going is short by whatever it has not traded in yet, and a
  run that spent its last minutes calibrating or relisting without a sale is
  short by that too. It is time the script was *trading*, not time it was up.
- A line whose margin is far below the others is usually stock bought near its
  own selling price, not a reporting fault — check it against the floors in the
  run log.
- Revenue is gross. Whether the game's sales fee is deducted in the ledger is
  unconfirmed; if it is not, real margins are lower.

## Do not

- Do not open `sales.db` for writing. It is the live ledger and is gitignored.
- Do not hardcode a date. It caught fire once: `SINCE` was pinned to
  `2026-08-20T08:00:00` and silently reported 52 hours under a "SINCE 08:00"
  heading.
