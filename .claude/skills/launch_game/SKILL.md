---
name: launch_game
description: Start the Cabal game client when it is closed, maximize it on the 1080p monitor, start the trading supervisor, and keep relaunching both whenever the game crashes. Use when asked to launch, relaunch, reopen or start the game, or when a run dies with "could not bring the game to the foreground" and no CabalMain process exists.
---

# Launch the game

Run the tool (in the background when it starts a supervisor) and report what
it prints:

```
py tools/launch_game.py                          # open + maximize the game only
py tools/launch_game.py --config cang            # ... then run the supervisor
py tools/launch_game.py --config cang --keep     # ... and relaunch game and
                                                 #     supervisor after every crash
```

`--keep` is the standing setup the user asked for on 2026-11-16 ("relaunch
the script whenever the game crashed"). Use it instead of starting
`tools/supervise.py` directly. It replaces any separate CabalMain watcher.

## What it does

1. If `CabalMain.exe` is running, it skips straight to step 4.
2. Starts `C:\Games\PlayCabal\PlayCabal Launcher.exe` (or uses the open
   launcher window `PlayCabal`) and waits for the update check: PLAY is grey
   while it checks and turns red when ready (red score 0 vs ~58; threshold
   `launch_game.play_red_over` 40).
3. Clicks PLAY at (1010, 600) inside the launcher window and waits for the
   `PlayCabal EP39` window.
4. Maximizes the game window and checks its outer rect is
   `[-8, -8, 1928, 1048]`, the maximized window on the 1920x1080 monitor
   every calibration was measured on. The client opens as a ~1040x807
   window; in that state recovery reads `nothing recognisable on screen`.
5. With `--config`, starts `tools/supervise.py --config <profile>`. From the
   maximized login screen the supervisor presses Login (the client keeps the
   saved ID and password), picks character and channel, checks tab 4 against
   the dead run's log and launches the run.
6. With `--keep`, polls every 20 s. If the game disappears it kills the
   supervisor's process tree (nothing is left to drive) and starts again
   from step 2. If the supervisor exits while the game is still up -- a
   Ctrl x4 stop, a stop condition -- the tool exits too, so a deliberate stop
   is respected.

All numbers live in `config.json` under `launch_game`.

## Reading the result

- `PLAY never turned red` -- the launcher is still updating or failed;
  look at it (screenshot) before retrying.
- `expected [-8, -8, 1928, 1048] -- is it on the 1080p monitor?` -- the
  window maximized somewhere else; trading will not read the screen.
- `a supervisor or run is already up; not starting another` -- one is
  already live; leave it.
- `the supervisor exited (N) with the game up; stopping` -- the run was
  stopped on purpose or hit a stop condition; read the supervisor log before
  relaunching.

## Do not

- Do not start it while another supervisor or run is live (it refuses).
- Never type or store the password.
