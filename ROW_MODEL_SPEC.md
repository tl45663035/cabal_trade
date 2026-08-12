# Shop row model — specification

Authoritative description of the 30-slot shop model in `trade.py`
(`ShopModel`, the `SHOP` singleton, `ShopDiverged`, `Row.occupied`,
`Row.empties_on_collect`, `status_column`, `costed_sales`).

Every rule below was **measured on the live game** before it was coded. Where a
number appears it is a measurement, not an example.

---

## Purpose

Replace per-cycle discovery sweeps with a maintained model of all 30 shop
slots. After a single initialisation walk, the script knows what every row
holds and where new content will land, without reading the table again.

Measured cost of the sweeps this replaces: 74–129 s each, 5 walks in 59
minutes, ~8% of wall clock.

---

## Ground truth

| Rule | Status | Evidence |
|---|---|---|
| Rows are independent slots; holes are legal and persist | **contiguity is FALSE** | operator's statement; 25 recorded cycles show an empty row above occupied ones; live shop observed as rows 1-2 full, 3-10 empty, 11-30 full |
| A registration lands in the lowest-numbered empty slot | confirmed | 259 cancel→re-register observations |
| Cancelling row N empties N in place, nothing shifts | confirmed | 259 pairs, **zero** landed below N |
| Collecting a fully-sold row empties it in place, nothing shifts | confirmed | frame pair `run_51882` → `run_51888`; rows 5-10 identical across the collection |
| Collecting a partially-sold row keeps its content, Function → `Change` | confirmed | frame pair `run_51787` → `run_51793`; qty 60 preserved |
| **Nothing ever renumbers** | follows from the above | — |
| Listings never expire | per operator — **not modelled, not OCR'd** | — |

The codebase previously asserted *"Collecting renumbers the table exactly as a
cancel."* That is wrong about **both** halves and has been removed.

---

## Function × Status

| Function | Status | Slot occupied? | Alz to collect? | After collecting |
|---|---|---|---|---|
| `Register` | — | no | no | stays empty |
| `Receive` | `Complete` | yes | yes | **slot empties in place** |
| `Receive` | `On Sale` | yes | yes | **slot keeps content**, Function → `Change` |
| `Change` | `On Sale` | yes | no | — |

`qty` corroborates (`0` ⇒ Complete, `>0` ⇒ On Sale) but **Status is the primary
signal**: it is a word, whereas a lone digit in the narrow qty cell is the OCR
failure that wedged two runs on 2026-08-11.

Status column bounds: `Status` header left − 6 … `Function` header left − 6.
Read from the same bulk OCR pass as everything else — **no extra Tesseract
launch**.

---

## Data structure

```
ROWS[1..30] = EMPTY | { name, qty, price, floor, cost }
occupied    = count of non-EMPTY slots        # a tally, NEVER a boundary
first_empty() = min{ i in 1..30 : ROWS[i] is EMPTY }
```

`occupied` must never be read as "slots 1..occupied are full". That inference is
what the removed walk-skip trim made, and holes make it false.

`floor` and `cost` are carried **per row**, not per item: two rows of the same
Core can hold stock bought at different prices (Force Core(Highest) was held
both at ~192,000 and at 333,329 on 2026-08-11).

---

## Ownership

**Model owns — divergence here is fatal**
- whether a slot is occupied
- the identity of what occupies it (name)

**Volatile, refreshed on every read — never a divergence**
- `function` and `status`: a buyer flips `Change` → `Receive` at will
- `qty`: falls as partial sales land

---

## Initialisation

One full walk of all 30 slots. Nothing is inferred beforehand — with holes
legal, rows 11-30 cannot be deduced from the first screen. A walk that does not
reach row 30 **must not** seed the model; `adopt()` treats unseen indices as
empty, and an empty slot that is really occupied is what makes a registration
land somewhere unpredicted.

The seeding walk publishes to the range cache (`note_range_view`) so nothing
sweeps again for the same rows in the same cycle.

---

## Transitions

| Action | Effect on the model |
|---|---|
| Register content C | `M = first_empty()`; `ROWS[M] = C`; `occupied++` |
| Cancel row N | `ROWS[N] = EMPTY`; `occupied--`; no shift |
| Relist row N | cancel N, then register → lands at `first_empty()`, which is N when every slot above N is full, otherwise the first empty M < N |
| Collect `Complete` at N | `ROWS[N] = EMPTY`; `occupied--`; no shift |
| Collect `On Sale` at N | Alz taken; content kept; `function → Change`; qty updated |
| Restock adding k rows | k registrations fill the k lowest empty slots in ascending order |

---

## Divergence

The model is trusted. It is **not** resynced.

| Observation | Action |
|---|---|
| model says EMPTY, screen shows content | **terminate and report** |
| model says row N holds X, screen shows Y | **terminate and report** |
| model says occupied, screen EMPTY, and we did not cancel or collect it | **terminate and report** |
| `Change` → `Receive`, or qty fell | absorb, continue |

**Where it is detected:** the script already reads the single row it is about to
act on (`identity confirmed: row 7 still holds 'Force Core(High)'`). That
one-row read is the check. No sweep, one row, and it guards the click that
spends money.

Names compare with `_model_key`: pack marker and table trailer stripped, then
folded. So `Force Core(Highest)` (the catalogue name a restock registers under)
matches `Force Core(Highest) X 250` (what the shop then shows), and
`Yekaterina VIP Membership` matches the same name carrying its
`Use Period: 30 days` second line.

**The pack COUNT is deliberately not compared.** `Chaos Core Set X 250` and
`X 135` are the same identity to the model. Comparing it made every row the run
listed itself diverge on its first relist. Different *items* are still told
apart (`Force Core(High)` ≠ `Force Core(Highest)`).

---

## Staging

`--row-model` enables enforcement. **Default is shadow**: the model still
tracks every action and still checks itself against every row read, but records
`shopmodel.diverged` and stands down instead of ending the run. Zero recorded
divergences across whole sessions is what earns the flag.

Rationale: `cached_rows_used()` was tried before, drifted because one path
forgot to update it, and was removed. Drift is **asymmetric** — over-counting
occupancy costs a wasted action; under-counting registers into a slot the batch
never reprices, leaving stock at one moment's market indefinitely.

---

## Scrolling

Discovery walks disappear. Navigation does not: reaching row 13 to click its
Change button still requires scrolling there, and the wheel is calibrated at one
notch per row.

---

## Removed with this work

The trim that skipped a walk when the last visible row read `(empty)`, reasoning
*"an empty row on the first screen is the end of the shop."* False once holes
are legal. Live cost, observed: a request for rows 1-11 was silently cut to rows
1-3, leaving 16 occupied rows unrepriced — including `Upgrade Core (Ultimate)`
listed ~12% over market on nine rows, and four VIP items worth ~2,190,000,000
on rows 27-30.

---

## Known-open

- The replay harness built to validate the model measured **itself**: the log's
  batch header row and the row actually acted on disagree 12% of the time (47
  of 391), and 31 rows were cancelled twice in one cycle. The 90.3% accuracy
  figure it produced is not a measurement of the model.
- Duplicate identical stacks cause the batch to re-resolve a target by identity
  and relist the same row twice while another goes untouched. The model is the
  intended fix, since it knows which slot holds which listing.
