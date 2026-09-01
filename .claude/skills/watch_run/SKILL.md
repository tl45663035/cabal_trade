---
name: watch_run
description: Watch the live trading run and push a notification on server lag, a stop condition, a disconnect, or the run dying. Use when asked to monitor, watch, or keep an eye on the run, or to be told when it stops.
---

# Watch the live run

Find the run, then hand `tools/watch_run.sh` to the **Monitor** tool.

```
Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*'
  -and $_.CommandLine -like '*driver.py*' } | Select-Object ProcessId
```

The log is the newest `src_1080p/logs/*_run.log`. Then arm it persistently:

```
Monitor(
  command: "bash /c/Users/tl456/Desktop/cabal_trade/tools/watch_run.sh <PID> <LOG>",
  description: "cabal_trade pid <PID> -- death, server lag, disconnect",
  persistent: true, timeout_ms: 3600000)
```

If no `driver.py` process is running, say so and stop -- do not arm a watch
on a dead pid, it will fire once and exit.

## Push a notification when it fires

Every event line ends with **`run ALIVE`** or **`run DEAD`**. Put that in the
notification, because the same words mean different things either side of it:
a stall on a live run is a pause, a stall on a dead one is how it died.

Keep it to one line, lead with what they would act on:

```
cabal_trade DEAD 20:10 -- STOPPED: no Cancel button after Change on row 9. Nothing cancelled.
cabal_trade ALIVE -- server stalled 60s at 15:21, recovered, row relisted
cabal_trade DEAD -- disconnected; py src_1080p/recovery.py is the way back
```

Do not push for the war-window line -- that one is the watcher telling you it
checked and nothing is wrong.

## What it watches

- **Server lag** -- the stall, and each layer that may catch it: `does not
  count` is the row-level retry, `starting the pass again` and `going to the
  default state` are the pass restart.
- **Stop conditions** -- `STOPPED:` as it is written, before the process
  finishes unwinding, so the reason arrives with the death rather than after.
- **Disconnect** -- two-stage. `recovery.disconnected()` costs about 15
  seconds of OCR, far too much to poll, so the free signal is the log going
  quiet for 150s; only then does it look at the screen.
- **Death** -- any cause. It dumps the stop reason, `crashed:`, traceback,
  `ran for` and the closing profit total, then ends.

## Two things it gets right that are easy to get wrong

**A war window looks exactly like a hang.** A quiet run is usually
`war.avoid()` parking the game for up to 6.5 minutes. The watcher checks for
an open `WAR LAG` in the log first and says so without touching the screen.

**`grep -c` prints `0` and exits 1 when nothing matches.** Writing
`$(grep -c ... || echo 0)` makes the value `"0\n0"`, and every `-gt` after it
fails with `integer expected`. A watch built that way runs, looks healthy, and
silently never fires -- it missed two FCU resupplies on 2026-08-31 before the
`count()` helper fixed it. Silence from a monitor is not evidence of calm.

## Do not

- Do not lower `QUIET_AFTER` below the war quiet window, or every war costs a
  15-second screen read to conclude nothing is wrong.
- Do not arm a second watch on a pid that already has one.
