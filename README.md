# Cabal Online — Agent Shop relister

Cancels your Agent Shop listings and re-lists them at the current lowest market
price, unattended, for hours. Built for one account on one machine; it reads the
screen with OCR and drives the game with synthetic mouse and keyboard input.

```
trade.py              the whole program — one file, no local imports
calibration.json      measured screen layout, rewritten automatically
unit_tests/           test suites and the recorded frame corpus
screenshots/          output of --shot
```

---

## Moving this to another machine

Copy the whole folder **except** these, which are machine-specific or huge:

| skip | why |
| --- | --- |
| `calibration.json` | measured for the old screen. It is rewritten on first use; copying it across is actively harmful. |
| `unit_tests/corpus/` | several GB of recorded frames from the old machine. The new one builds its own by running. |
| `unit_tests/baseline_rows.json` | frozen against frames that will not exist. Regenerate with `baseline_rows.py save` once a corpus exists. |
| `screenshots/`, `__pycache__/` | output and bytecode. |

Everything else — `trade.py`, `README.md`, `requirements.txt`, `unit_tests/*.py` — is self-contained and path-independent. After copying, follow *Before the first run* below, then read **Known issues** before starting an unattended run. The new machine will exercise the layout-calibration path, which the current machine never has.

---

## Before the first run

**1. Install the dependencies.**

```powershell
py -m pip install -r requirements.txt
```

**2. Install Tesseract OCR** to `C:\Program Files\Tesseract-OCR\`
(<https://github.com/UB-Mannheim/tesseract/wiki>). Everything the script reads
comes from Tesseract; nothing works without it.

**3. Run as Administrator.** The game runs elevated, so Windows silently
discards mouse and keyboard input from a non-elevated process. There is no
error — clicks simply do nothing. The script checks for this and refuses to
start rather than appear to work.

**4. Calibrate.** Open the Agent Shop so the Trade window is visible, then:

```powershell
py trade.py --calibrate
```

The coordinates built into the file were measured at 2560x1440. On any other
screen they point at the wrong pixels, and **a click that misses the UI lands in
the game world** — which walks your character or moves an item. Every command
that clicks refuses to run until a calibration matching the current screen and
game window exists. It is re-measured automatically whenever either changes.

---

## Running it

Relist rows 1–10, back to back, for five hours:

```powershell
py trade.py --repeat "relist-rows 1-10" --for 300 --every 0
```

| flag | meaning |
| --- | --- |
| `--for N` | how long to keep looping, in **minutes** |
| `--every N` | minutes between cycle **starts**; `0` starts each cycle as soon as the last finishes |
| `--dry-run` | locate everything, click nothing |

Rows are tracked **by name**, not by position. Cancelling empties a row and
re-registering fills the *first* empty row, so row numbers shift during a batch;
each item is re-located immediately before it is relisted.

Other commands:

```powershell
py trade.py --list                  show the current listings
py trade.py --open                  open the shop via the NPC
py trade.py --relist 3              cancel row 3 and re-list it
py trade.py --relist-rows 1-10      the same for the first ten rows
py trade.py --alz                   read the Alz balance
py trade.py --shot                  capture the screen to screenshots/
py trade.py --reset                 escape out, reopen the shop, clear the slot
```

Stop it with `Ctrl+C`. Three consecutive failed cycles also stop it, on the
grounds that retrying only helps if something might change between attempts —
a stranded item blocks every later cycle identically until a human clears it.

---

## Price floors

Some items must never be listed below a fixed price, whatever the market says.
These bind unconditionally and outrank "always take the lowest current price":
that rule decides *which* market figure to use, this one decides how low the
listing may go.

| item | floor |
| --- | --- |
| Yekaterina VIP Membership | 119,000,000 |
| Siena's Unbinding Stone | 71,000,000 |

Set in `ITEM_PRICE_FLOORS` in `trade.py`. Each entry is
`(token, full catalogue name, floor)`. Both routes matter:

- the **token** is a fast substring test, and
- the **full name** is what a corrupted OCR read is compared against, because a
  short token is too small a target — one bad glyph in `vip` loses the floor
  entirely, while the other 22 characters of the full name carry the match.

Spell the catalogue name exactly as the **game** renders it (`Siena's Unbinding
Stone`, with the apostrophe), or every match loses similarity for no reason.

When the market suggests nothing at all, the listing goes up at 10,000,000,000
rather than guessing.

---

## Preconditions the script enforces

- **The working inventory tab must be empty** at the start of a batch. A
  stranded item there blocks every cycle, so it aborts rather than proceeding.
- **A price below 1,000 Alz is treated as a misread**, not a price.
- **The item loaded into the shop slot must match** the one that was cancelled;
  a mismatch withdraws the listing and stops everything.

---

## Tests

```powershell
py unit_tests\run_all.py
```

Runs all eight suites, times each, and exits non-zero if any fail. Takes about
**15 minutes**, over 99% of which is OCR on real frames.

| suite | what it proves |
| --- | --- |
| `suite_corpus.py` | every reader against every recorded frame |
| `baseline_rows.py check` | `read_rows` is unchanged on every baselined frame |
| `suite_pure.py` | decision logic, no OCR |
| `record_guard_test.py` | frame recording cannot corrupt its own index |
| floor suites | both floors survive corrupted names; neither leaks to other items |
| `import_smoke.py` | every global resolves; layout constants are complete |

Individual runs:

```powershell
py unit_tests\suite_corpus.py            all frames, sampled determinism
py unit_tests\suite_corpus.py --full     determinism on every frame
py unit_tests\suite_corpus.py --limit 50 quick check
py unit_tests\suite_corpus.py --jobs 1   single process, for debugging a failure
py unit_tests\baseline_rows.py save      re-freeze after an intended change
```

### The corpus

`unit_tests/corpus/` holds frames the script recorded **while running against
the real game** — every step of every cycle, indexed in `run_index.jsonl` with
the values the script believed at that moment.

That last part is what makes them worth keeping. A screenshot can only be
checked against invariants; a recorded frame can be checked against **ground
truth** — re-reading it must reproduce the exact name, action, price and
quantity the running script acted on. Those are the assertions that catch a
reader drifting on the row that decides which listing gets cancelled.

Recording is on by default and stops at `RECORD_LIMIT` frames (3000) to avoid
filling the disk. Frames are ~3 MB each.

---

## Known issues

A ten-reviewer audit on 2026-08-03 found the following. They are listed because
you are more likely to meet them on a new machine than the old one, and because
a passing test suite does not mean they are absent — see *What the tests are
worth* below.

**Calibration is the biggest risk on a new machine, and it is the least tested
part of the program.** The current machine happens to sit at scale 1.0, so the
scaling path has never executed on real pixels anywhere. Four specific ways a
wrong layout can be accepted:

- The anchor fit accepts three anchors with no vertical spread (the span test is
  max-pairwise-distance only), then extrapolates over the window's full height.
  Simulated at 1080p: accepted 88% of the time, worst click error **40 px**
  against a 59 px row pitch — enough to hit the wrong listing.
- The OCR upscale is derived from the *previous* layout, so the first
  calibration on a 1080p screen runs at the setting documented as splitting
  `Refresh` into `R` + `efresh` — losing the anchor that matters most.
- `validate_layout()`'s region checks are dead on the first calibration of any
  process, because they read a table `apply_layout()` fills in afterwards.
- `_clamp_box` collapses an off-screen region to a 1-px sliver instead of
  failing, which silently deletes an NPC exclusion zone.

**Mitigation until these are fixed:** after `--calibrate` on a new machine, run
`--list` and `--open` and confirm they behave before anything that clicks
unattended. A misplaced click walks your character; it does not just fail.

**Other confirmed findings, unfixed at time of writing:**

- `open_trade_window` raises `UnboundLocalError` if the Trade window is open on
  the Purchase tab. Kills three cycles, then the run.
- `read_rows` can return `(empty)` for a real listing whose name only OCRs at a
  tighter crop. The row is then skipped as an empty slot and the cycle still
  reports success.
- A "Premium Exclusive Slot" row makes `read_rows` discard the whole table.
- Six code paths can leave an item cancelled and un-relisted; **only two of them
  print a warning**. A stranded item is invisible to every later cycle, because
  the relist loop only reads table rows.
- A cycle that relists one row and skips nine still counts as a success and
  resets the consecutive-failure breaker.
- `FLOOR_LENGTH_RATIO = 0.80` can lose an item's price floor if enough
  characters drop out of the name. For `Siena's Unbinding Stone` the margin is
  two characters, and `Siena's` is the lowest-confidence word in its row.
- Only the first 10 listings are ever seen. If the shop holds more, the rest are
  never relisted and a scroll would renumber everything silently.

## What the tests are worth

`run_all.py` reports ~125,000 passing assertions. Read that number carefully.

Roughly 2,900 of them are called "ground truth": a recorded frame is re-read and
must reproduce the values the script believed at capture time. **Those values
were produced by the same functions the test checks**, from the same pixels, so
they detect *drift and non-determinism* — not correctness. A consistent misread
is recorded as truth and passes for ever. The `read_rows` bug above was found by
a reviewer, not by the suite, and the baseline had been frozen *after* the bug
was introduced.

Genuinely independent evidence in the corpus is small but real: `net_sales` is
rendered by the game and cross-checks two separately-read numbers
(`net_sales == price x qty`, 132/132), and prices round-trip through the game
(125/133). None of it is asserted yet.

Treat the suite as a strong regression harness and a weak correctness oracle.

## When something goes wrong

**"Input is being refused"** — not running as Administrator.

**"Could not calibrate"** — the Trade window must be open for calibration, since
that is what gets measured. Run `py trade.py --open` first.

**"the working inventory tab must be empty"** — an item is stranded there from a
previous failure. Clear it by hand, or list it with `--register ROW COL`.

**A cancelled item did not get re-listed** — it is in your inventory, unlisted.
The relist loop only ever acts on rows in the table, so it will not pick the
item back up on its own. Find it and use `--register ROW COL`.

**Clicks landing in the game world** — the calibration does not match the
current screen or window size. Re-run `--calibrate`.
