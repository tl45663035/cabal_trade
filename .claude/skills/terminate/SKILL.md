---
name: terminate
description: Stop the live trading run cleanly at the next gift box, then put the game back to its default state. Use when asked to stop, terminate, halt or cancel the run, especially before relaunching on new code.
---

# Terminate the live run

Run the tool and report what it prints:

```
py tools/cleanly_terminate.py
```

It finds the running `driver.py` itself, so it takes no arguments.

## What it does, and why in that order

1. **Waits for the next gift box.** The run collects gifts inside
   `rest_the_game()`, which calls `close_everything()` *first* -- so when the
   gift box opens the Trade window is already shut and the character is
   parked. It is the quietest moment in the cycle. Stopping mid-listing can
   leave an item cancelled and un-relisted, which no later cycle picks up.
2. **Presses Ctrl four times inside the stop window.** That is the mechanism
   the run advertises at startup, and it unwinds through
   `_thread.interrupt_main()` exactly as Ctrl+C would: profit summary,
   banner, `stopped: interrupted from the keyboard`. It is not a kill.
3. **Waits for the process to exit** before touching the screen, so the tool
   never drives the game while the run might still be driving it.
4. **Returns the game to its default state** with `close_everything()`.

Every number comes from config: `run.stop_key_presses`,
`run.stop_key_window`, `timing.retry_gap`, `timing.dialog_timeout`,
`recovery.world_timeout`.

## Step 4 is the one that matters

The run's own shutdown does **not** restore the game state -- it writes the
profit summary and exits wherever it was. On 2026-08-31 a run was stopped by
hand during gift collection and relaunched immediately; the next run crashed
in 19 seconds with

```
right-clicked the Agent Shop key twice and the Trade window never appeared
```

because the screen was not at the default state. That is why the tool closes
down before it reports success, and why "stopped" alone is not enough before
a relaunch.

## Reading the result

- `no run is live; nothing to terminate` -- nothing was running. Not an error.
- `the run ended on its own before the gift box` -- it stopped or crashed
  while waiting. Check the log it names; do not assume it stopped cleanly.
- `pid N is still up after 120s` -- the stop key did not take. The tool
  deliberately does **not** restore the state in this case, because the run
  may still be clicking. Investigate before doing anything else.

A pass takes roughly nine minutes, so waiting for the gift box can take that
long. That wait is the point; do not shorten it by killing the process.

## Do not

- Do not `taskkill` the run. A hard kill can strand a cancelled item in the
  inventory, and the relist loop only ever reads table rows, so nothing picks
  it back up.
- Do not relaunch until the tool has reported the default state restored.
