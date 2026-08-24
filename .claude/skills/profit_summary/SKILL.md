---
name: profit_summary
description: Report trading profit for every run launched since midnight, counting only cores and Chaos the script both bought and sold. Use when asked for profit, takings, margin, or how the trading is doing today.
---

# Profit summary

Run the tool and report what it prints:

```
py tools/profit_summary.py
```

It reads `sales.db` read-only, so it is safe while a run is in progress.

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

| | `qty` means | worked example |
|---|---|---|
| `purchases` | cores, already expanded | `Force Core Set (High) X 435`, qty **435** |
| `sales` | whatever was listed | `Chaos Core Set X 261`, qty **1** |

So sales need `qty x pack-from-the-name`; purchases must not be scaled. The
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

- `by run` splits matched profit across the runs in the window.
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
