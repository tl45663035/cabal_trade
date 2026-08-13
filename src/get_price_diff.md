# Cabal Online - Get Trading Price Difference

Given item favorite slot A, B.
Go to Purchase tab, extract the price difference between A - B, and return the number.

This is a **cheapest-listing comparator**, normalised per unit.

---

## Functionality

```
get_price_diff(A, B, in_shop) -> int | None
```

where

| Argument  | Meaning |
|-----------|---------|
| `A`       | Favorite Slot A |
| `B`       | Favorite Slot B |
| `in_shop` | Boolean. The caller's belief that we are currently in the Agent Shop window. A HINT, not a fact - see Preconditions. |

Returns the **per-unit** price difference `A - B` as an integer, or `None`.

---

## Preconditions

This function should be called in the Agent Shop window, identified by flag
`in_shop=true`.

If `in_shop` is not true, execute `open_agent_shop()`.

**`in_shop` is a hint and is always verified.** A wrong flag does not produce a
wrong number, it produces a click into the game world: the Purchase tab
coordinates with no window under them are a move order that walks the
character. So the state is measured with `purchase_ready()` before every click,
never assumed from the argument. When the flag says open and the measurement
disagrees, the measurement wins and `open_agent_shop()` runs anyway.

---

## Algorithm

Else in shop:

1. Go to the Purchase tab. Location known from calibration in the init phase.
   **Cost = 0**
2. Click on Sort, a dropdown menu will drop down, select Low to High. Locations
   of Sort, the dropdown, and the Low to High option are all known from
   calibration. **Cost = 0 to click** - then read the sort back to confirm it
   took, see Rules.
3. Verify the tab, the sort and the window are all still good before clicking.
   This is one read of the current frame, and it is what makes "row 1 is the
   cheapest" true. **Re-checked before EACH slot, not once at the start** - see
   Rules.
4. Click on Favorite Slot A. **Cost = 0**, location known.
5. OCR the item name, the item quantity, and the item cost on **first row
   only**.
6. If the search failed, try again after OCR determines failed. Total retry = 5.
7. Repeat steps 3-6 for Favorite Slot B.
8. Extract the price and quantity of both A and B, return the per-unit
   difference. See Pricing.

---

## Pricing

The Price column holds the price of the **whole listing**, not of one unit. A
row reading `Chaos Core Set X 10` at `7,400,000` is 10 units at `740,000`
each. The quantity captured in step 5 is what makes the two sides comparable:

```
price_per_unit(row) = row.price // max(1, row.quantity)

return price_per_unit(A_row1) - price_per_unit(B_row1)
```

**Both sides are divided, always, even when the quantity reads 1.** Dividing by
1 costs nothing and keeps it uniform; making the division conditional is how
one side ends up normalised and the other not.

Raw `A.price - B.price` is wrong whenever the two slots hold different pack
sizes. It compares a bundle against a single unit: measured live on
2026-08-10, that is a `109,628,780` bundle against a `694,980` Core, reporting
a difference of about a hundred million where the real per-unit gap was five
figures.

### Sign convention

`A - B` is positive when A's unit price is **higher** than B's. For a
buy-low-craft-up trade, pass the crafted output as A and the raw input as B, so
a positive return is profit per unit.

---

## Return values

| Outcome | Return |
|---|---|
| Both slots read | `int` - the per-unit difference, may be negative |
| Either search never ran (5 attempts exhausted) | `None` |
| Either slot returned zero offers | `None` |
| Either row 1 price or quantity did not read | `None` |
| Shop could not be opened | `None` |

**Never return 0 for a failure.** Zero is a real, meaningful answer - two items
at the same unit price - and a caller comparing it against a threshold cannot
tell a measured zero from a failed read. Every failure path returns `None`, and
the caller decides what to do about not knowing.

**An empty result is not a failed search.** A slot with genuinely zero offers
on the market is a different condition from a search that never ran, and both
differ again from a search whose results have not refreshed yet. All three
return `None`, but they must be logged distinctly, because "the market is
empty" and "the tab was not ready" call for opposite responses.

---

## Rules

- **Searching item slot A, B will always select first row. DO NOT BREAK THIS
  RULE. Always operate on row 1.**
- **Price sort will always be Low to High.**
- The sort must be **confirmed by reading it back**, not assumed from the
  click. The dropdown selection can fail to take, and Low to High sorts by
  listing total, so an unconfirmed sort means row 1 may be the *dearest* offer
  on the board rather than the cheapest. One OCR rules this out; without it the
  function returns a confident number computed from the wrong row.
- **The tab, sort and window are re-verified before EVERY slot click**, not
  once at the start of the call. Setting the sort before slot A says nothing
  about the state when slot B is clicked seconds later: a window can close
  between two clicks, and a favourite coordinate with no window under it is a
  move order into the game world, not a search. Verifying once and then
  clicking on that authority is how a capture loop clicked favourite
  coordinates eighty times into the 3D world and walked the character away
  from the NPC.
- The result table must be read only after the server refresh has settled.
  Mid-refresh every row reads empty, which is indistinguishable from an empty
  market.
- The state of the game on return must match the state on entry. If this
  function opened the Agent Shop, it closes it before returning.

---

## OCR cost

Per successful call, happy path:

| Step | Passes |
|---|---|
| `purchase_ready()` before each slot click (tab, sort, window) | 2 |
| Read the result table, once per slot | 2 |
| **Total** | **4** |

A single table read yields name, quantity and price together - they are one
banded crop per row split by column, not three separate reads. Only row 1 is
consumed, but the read returns whatever is on screen.

Retries add one `purchase_ready()` and one table read each, up to 5 attempts
per slot. Worst case before giving up: 20.
