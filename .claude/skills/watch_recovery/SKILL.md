---
name: watch_recovery
description: Keep the trading run alive unattended - watch it, and when it dies (disconnect, crash, stop condition) finish what it was doing, reset the game, relaunch, re-arm the watch, and repeat until the user stops it. Use when asked to run fully auto, babysit, or keep the run going overnight.
---

# Watch, recover, relaunch, repeat

This is `watch_run` plus what to do when the run dies. The loop is yours,
not a process: the Monitor wakes you on each event, you act, you re-arm.
Each death goes: read the state and the log (3) -> get back in and finish
what the run was doing (4) -> reset (5) -> relaunch and re-arm (6).
It runs until the user says stop, or stops the run themselves with
**Ctrl x4** (`stopped: interrupted from the keyboard` in the log) -- that is
a cancel, not a death. Do not relaunch after it.

## 0. The loop in code: `tools/supervise.py`

Since 2026-09-02 the whole loop below is a program. Prefer it:

```
Start-Process py -ArgumentList 'tools/supervise.py' -WorkingDirectory <repo> -WindowStyle Minimized
```

It attaches to the live `driver.py`, or **launches one at once** if none
is up (logging in first if the game dropped us, Escape / Confirmation for
a vendor or dialog, `close_everything`, no bag sweep on start). Then it
watches, and on death does steps 3-6 itself: screen read, `recovery.py`,
then the **dead log's interrupted task** (a `TASK {...}` marker with no
`DONE` for it, or on older logs a `row N: '<item>' xQ at P -> tab 4 slot
(r,c)` with no `relisted ... in row N` after it -> the slot's tooltip must
read the item, then `list r c 0 <tab> <item>`: panel price with the
item's Set-cost floor, the price the run itself would use, repeated while
the slot refills; a `-- <core>: ... --` resupply that reached `n/m ...
held` with no `resupply <core>: bought` -> `convert <slot>` or `craft
chaos`, then `list` of each tab-4 slot whose tooltip reads what the
resupply made; under-a-batch Chaos Cores are left as the run leaves
them), a check of tab 4 (unnamed stock there is pushed as `stranded stock
on tab 4` and the run relaunches beside it; a full tab stops), reset,
relaunch. A tooltip is read on every slot it lists from, and to find a
withdrawn item on another tab when its slot is empty (capped by
`supervise.hover_cap`). Every frame it acts
on is saved under `logs/supervise_frames/`. Every event is a Windows toast and a line in
`src_1080p/logs/supervise.log` (`<reason>,<time>,<alive|dead>`); its own
transcript is `logs/<ts>_supervise.log`. Relay the event file with
`Monitor("tail -n 0 -f .../src_1080p/logs/supervise.log")` and push each
line. Do **not** also arm `watch_run.sh` and act on deaths by hand while
it runs: two things recovering one game.

It stops itself, with a `supervisor stopped: ...,dead` line, on anything
it cannot classify (a slot whose tooltip is not the item it would list, tab 4 full, rows full, a dialog that
will not close, `recovery.py` refused, three runs dying within 5 min of
launch, 8 driver commands in one recovery). Then the hand loop below takes over. Ctrl x4 stops it and the
run together. Killing it: its pids are `py.exe` + `python.exe` with
`supervise.py` in the command line; the run is its **child**, so
`taskkill /T` on it kills the run too (2026-09-02 09:53). Kill the two
pids without `/T` to keep the run.

A console window over the game blinds the driver (`the Agent Shop would
not reopen before pass 1`, 2026-09-02 09:51). The supervisor minimises
its own and launches the driver's minimised; anything else started on
that desktop must stay off the game.

**No bug fixes while the loop runs.** A new way of dying gets noted in the
push and the summary; the code is not touched. The user reads the reasons
in the morning and decides.

## 1. Arm

Find the pid and newest `src_1080p/logs/*_run.log` exactly as `watch_run`
does, then:

```
Monitor(command: "bash /c/Users/tl456/Desktop/cabal_trade/tools/watch_run.sh <PID> <LOG>",
        description: "cabal_trade pid <PID> -- death, server lag, disconnect",
        persistent: true, timeout_ms: 3600000)
```

One watch per pid. When the run dies the watcher prints `RUN ENDED` and
exits, so there is nothing to retire.

## 2. On every event: push one line

`<reason>,<time>,<alive|dead>` -- every event, no filtering, as in
`watch_run`. Server lag, its recovery, the pass restart, war windows,
stops, crashes, `RUN ENDED`, and your own `relaunched (pid N),<time>,alive`.

## 3. On death: read before touching

The watcher's `RUN ENDED` line carries the stop reason. Then:

```
py tools/game_state.py <scratchpad>/state_<HHMM>.png
```

One passive screenshot and OCR, no input. It prints the disconnect notice
point, login screen, whether the Alz balance is readable, Trade window,
vendor, what the centre dialog says, and the underprice question. Read the
png too.

Then read the dead run's log for **what it was in the middle of** -- the
screen shows the game, not the task. Two things to find, every time:

```
grep -n "^-- .*: [0-9]* row(s)\|resupply .*: bought\|resupply of .* stopped\|-- round\|held$" <LOG> | tail -n 12
grep -c "\-> tab 4 slot (1, 1)" <LOG>; grep "\-> tab 4 slot" <LOG> | tail -n 1
```

- A resupply block (`-- Force Core (Ultimate): 1 row(s) --`)
  that bought (`balance after ... bought N pack(s)`) with no
  `resupply <core>: bought N, ... listed` line after it was **cut off with
  stock in the bag**: Sets bought and not converted, or (Chaos) cores
  bought and not crafted. `resupply of '...' stopped: ... Cancelled.` is
  the same thing said plainly.
- Withdrawals landing at `tab 4 slot (1, 2)` instead of `(1, 1)` mean
  slot (1,1) was already occupied when that run started -- stock a run
  before it left behind. It has been sitting there the whole run: the live
  run never converts outside a resupply, never counts bag stock in its
  `short?` table, and resupplies only when a core holds fewer rows than
  its margin is worth (`rows_by_margin` in config.json: the core-minus-Set
  gap, or Set-minus-core for a crafted one, in steps of 5,000 Alz, read
  afresh at the start of every pass). Nothing picks it up but you.

**A "disconnect notice" is only real in the centre of the screen** (about
`(960, 430)`, its OK at `(961, 609)`). `recovery.disconnected()` matches the
words `disconnect`, `logged`, `log-out` anywhere, and the chat at the bottom
left says those all day: a point like `(149, 828)` is chat, not a notice.

## 4. Recover by case

The order is fixed: **get back into the game, finish the interrupted task,
then reset, then relaunch.** Resetting first throws the task away -- the
relaunched run does not know what the dead one was holding (see the
2026-09-02 note below) -- so step 5 never comes before this table is
worked through. Several rows can apply to one death: a disconnect during a
resupply is the first row and then the convert row.

Never send input while a `driver.py` process is alive. Check with the
process query from `watch_run` first; `tools/relaunch_run.sh` refuses to
launch over a live one for the same reason.

| what the screen says | interrupted task | do |
|---|---|---|
| disconnect notice in the centre, or the login screen | whatever the log's last lines say; the game has forgotten it | `cd src_1080p && py recovery.py` -- clicks OK, logs in with `account.json`, picks the channel and character, enters, answers the keypad if asked, and returns when the Alz balance reads. Then step 5. |
| bare screen after `ServerStalled` in startup calibration (`calibrate_voucher`, `hold_if_busy` in the traceback) | none -- the stall handler already shut the shop | nothing to reset; step 6 straight away |
| Confirm Registration or the underprice question left open (`centre dialog` reads `Register`, `Period`, `Quantity` / `than the average`) | a listing half done; the item is in the dialog | click Confirmation at `(968, 634)`; if the underprice question was up, the real dialog follows and needs a second click. The first click sometimes answers "Please try again in a few moment" -- wait 5s and click again. Confirm the row appeared before moving on. |
| stock withdrawn to tab 4 but never relisted (`STOPPED: tab 4 slot (r,c) ...`, no dialog) | the relist | `py src_1080p/driver.py list <r> <c>` lists that bag slot at the model's price. If the slot is empty (the withdrawal never landed), there is nothing to list -- say so. |
| a resupply cut off after buying (the log test above): Sets in the bag, e.g. `Force Core Set (Ultimate) X 404` bought, `round 1: convert` never finished | the conversion and the listing | `py src_1080p/driver.py convert <core slot>` -- `-- recovery: convert whatever <Set> is held into <core> --`: opens the vendor, converts in rounds of 250, lists each round into free rows 1-21. Core slots: 1 FC(Highest), 5 FC(Ultimate), 7 FC(High), 9 UC(Ultimate); the Set is the slot after. It stops on its own when the bag has nothing more (`nothing more to convert`). |
| a Chaos resupply cut off after buying: Chaos Core in the bag, not crafted into Sets | the craft and the listing | `py src_1080p/driver.py craft chaos` -- `-- recovery: craft whatever Chaos Core is held into Chaos Core Set --`, then lists. |
| `crashed: NotReady: inventory tab 4 is full` right after a stall, and the log's last withdrawal (`row N: '<item>' xQ ... -> tab 4 slot (r,c)`) was cut off by `the server is not answering` | the relist of that row: the cancel went through while the server was away and the stock came back as **dozens of small stacks** (63 on tab 4, more on tab 2 on 2026-09-02) instead of one stack | identify the item first -- hover a loose slot and read the tooltip (no driver alive, so the hover is allowed): `SetCursorPos(*calibration.inventory_slot_point(1, 2))`, grab, read. Then **one** `py src_1080p/driver.py list 1 2`: the game gathers every stack of that item, across tabs, into one registration (`typed 250; the net sales make it 250`). Check `occupied_slots()` on tabs 4 and 2 afterwards; run it again only if loose stacks remain. `calibrate_actions` skips the row-1 walk while tab 4 is full, so `initialise()` does not trip. |
| Trade window, vendor, or Inventory open, nothing else | none | step 5 |
| anything else (craft Request window, an unknown dialog) | read the frame | `close_everything` does not know these. Press Escape via `open_inventory.press(VK_ESCAPE)` once, re-read the state, and say what was there. Do not guess at buttons. |

The `convert` / `craft` recovery runs the full startup calibration first
(it cancels and relists row 1 to learn the buttons, like a run launch), so
it needs the game logged in and the Trade window closed: after a disconnect
it comes **after** `recovery.py`, and always **before** step 5 and the
relaunch. Rows 1-21 full means it refuses to convert (`rows 1-21 are full
... not converting`) -- then relaunch anyway and say the stock is still in
the bag.

This case was missed on 2026-09-02: pid 10388's last act was buying a 404
Force Core Set (Ultimate) pack whose convert dialog died with the server;
the leftover sat in tab 4 slot (1,1) through the whole 8-hour run that
followed, with every withdrawal stepping around it to (1,2).

`recovery.py` raises `Refused` when it cannot get in (login refused N
times, dual login not released, no channel/character, never reached the
world). That is a stop: push it as `dead` and wait for the user; do not
loop on the login screen.

## 5. Reset

```
cd src_1080p && py -c "import calibration; calibration.close_everything(True)"
```

Escape for a Trade window or vendor, I for the Inventory, park. It prints
what it did; if it says the panel did not close, read the state again.

## 6. Relaunch and re-arm

```
bash tools/relaunch_run.sh
```

Prints `pid=` and `log=`, then the first log lines (`code <hash>` should be
the current HEAD). Arm the watch on that pid (step 1), push
`relaunched (pid N),<time>,alive`, and check the log once it is past
`opening the Agent Shop` -- a run that dies inside startup calibration
(the 23:41 `ServerStalled` on 2026-09-01 did, 2m32s in) comes back through
this same loop.

## 7. Report only when asked

Between events say nothing but the push. When asked "run status" give:
pid, up since, log age, relists/purchases/collections counted from the log,
last balance, stalls and war windows survived, and whether the watch is
armed. `profit_summary` books a run on its **launch** day, so an overnight
run's trades sit on yesterday's line; say so rather than reporting 0.

## Lessons this was built on (2026-09-01/02)

- Background Bash does not keep the working directory: run project scripts
  with `cd src_1080p &&` or `PYTHONPATH=.../src_1080p`, or the import of
  `calibration` fails silently in a background task.
- `debug_frames/` is pruned by the live run; copy a frame out before
  studying it.
- A 60s stall inside the pass restarts the pass (`ServerStalled` is caught
  in `do_relist`); the same stall inside startup calibration is a crash.
  Both are relaunch-and-carry-on under this loop.
- "Tab 4 is full" after a stall is not a full bag, it is one cancelled row
  come back in pieces. The 08:45 crash on 2026-09-02 was 250 Upgrade Core
  (Ultimate) in 63+12 single-looking slots; one `list 1 2` took all 250.
  Identify the item by tooltip before listing -- the icon does not say.
- A disconnect can present as a stop condition first (`tab 4 slot (1,2) is
  still empty 8s after the withdrawal`) -- the run was reading a screen the
  server had already dropped. The screenshot tells the truth; the log's
  reason does not.
