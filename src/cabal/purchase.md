# purchase - the sort, the favourite slots, and the offer rows

Where prices are read. Two things gate every read and neither is optional.

**The sort.** "Row 1 is the cheapest" is only true under Price: Low to High,
and the control is a dropdown a human can change.

**The tab.** A favourite-slot coordinate with no Purchase tab under it is a
click into the listings table or into the 3D world.

## Offer

| Field | Meaning |
|---|---|
| `row` | 1-based, as shown on screen |
| `name` | the item name, including any `X 148` bundle count |
| `price` | the price of the WHOLE listing |
| `pack` | how many units the listing contains |
| `available` | how many such listings are on offer |
| `unit_price` | `price // pack`, or `None` |

`unit_price` returns `None` rather than a fallback when the row did not read. A
pack that did not read is not a pack of one: treating it as one would inflate a
148-unit bundle unit price 148-fold, in the direction that makes a bad trade
look good.

## The sort direction

The direction is the word straight after `Price:`, matched by regex, and
nothing else will do.

A substring test cannot do this job. Testing for both "low" and "price" in the
text is true of `By Price:High to Low` as well, because the two labels are
anagrams as far as substrings are concerned. Getting it wrong buys the most
expensive offer on the board believing it to be the cheapest.

Fails closed: an unread direction, or no `Price:` at all, is False.

The open menu is only read when a line names **both** directions. The offers
table shows through the same band when the menu is shut, and its own header row
names neither. Half a label is refused: clicking a row that was only partly
read is how a menu click lands on the table underneath it.

## Reading a row

One banded crop per row, split into cells by x. Name, quantity and price come
out of a single read - they are not three separate OCR passes.

`pack` comes from the **name**: bundled items carry their count there
(`Chaos Core Set X 148`) and it appears nowhere else on the row.

A row whose price is below `MIN_PLAUSIBLE_PRICE` is dropped as a clipped read.
Prices in this market are six figures and up.

Rows that cannot be parsed are **skipped, not padded**. That is why callers
must select row 1 by its `row` number rather than by list position: `offers[0]`
is the first row that parsed, which is not necessarily the first row on screen.

## The favourite search

`run_favourite_search` presses a slot and returns what it found, or `[]`.

- `purchase_ready()` is re-checked **before every attempt**, not once before
  the loop. The window can close between one click and the next.
- The slot is **approached from above** so the pointer enters the button. A
  move to the pixel the cursor already occupies raises no event, so a control
  that arms on hover is never armed.
- Results are confirmed to belong to the slot just pressed. Stale rows read as
  a real answer are worse than no answer: they look exactly like a successful
  search of a **different** item.
- `[]` covers three conditions - the tab was not ready, the results never
  refreshed, the market is empty - and each is logged distinctly, because they
  call for opposite responses from a human.

## purchase_ready

One screenshot, three questions: is the window open, is the Purchase tab
showing, is the sort Low to High. All three must hold before anything is
clicked.
