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

## On every event: push, and say nothing

The only response to a monitor event is a PushNotification. No summary, no
explanation, no "recovered, run alive" line in the terminal, no follow-up.
The user is not reading the terminal -- that is the whole point of the
watch -- and a wall of analysis after each event is noise they have to
scroll past later.

One line, three fields:

```
<reason>,<time>,<alive|dead>
```

```
server lag,00:18:48,alive
server answered,00:19:50,alive
stopped: no Cancel button on row 1,09:05:42,dead
crashed: inventory tab 4 is full,05:33:57,dead
disconnected,03:20:11,dead
war window,04:31:39,alive
```

The reason is the shortest phrase naming which watched condition fired. The
state is the word the watcher put at the end of its line. Say the state
again when a stall resolves.

Every event pushes. Server lag, its recovery, war windows, stop conditions,
disconnects, death -- all of them, no filtering, no judgement about which
are worth an interruption.

Analysis happens only when asked for it.

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
