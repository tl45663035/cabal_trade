# Recorded dungeon runs in the trading loop: design

Date: 2026-10-19
Status: draft, waiting for approval

## Why

Scripting each dungeon step by step in `dungeon.py` is slow work: walks,
kills, BM loops, healing and targeting all have to be written out. The
user can now record a whole run by hand with `tools/record_all.py`. The
replay is checked to be input-exact: see `record_replay_test.py` and
`check_recordings.py`.

They want those recordings to run the dungeon inside the normal bot,
which is kept alive by `tools/supervise.py` (it launches `driver.py`). The
bot keeps doing everything around the run that it does today: entering,
the key, the difficulty, Challenge, claiming the reward and leaving.

## Goals

1. **A recorded dungeon run.** The bot enters the dungeon and clicks
   Challenge exactly as `dungeon.py` does today (`get_entry`,
   `open_dungeon_key`, `start_dungeon`). It then replays that dungeon's
   recording. When the run ends, the existing end handling claims the
   reward and leaves.
2. **A timeout per dungeon.** If the dungeon is not cleared within its
   time limit (counted from the Challenge click), the replay stops, the
   bot leaves through that dungeon's recorded exit clip, the run counts,
   and the loop goes on to the next run.
3. **Per profile.** Each profile (`cang`, `trung`, `bot`) has its own list
   of recorded dungeons in `configs/<profile>.json`. Each entry has
   `enabled`, its recording, its exit recording and its time limit.

## Non-goals

- Changing how recordings are made or replayed: `record_all.py`'s
  record, replay, timing and turn handling stay as they are.
- Changing the scripted dungeons (Chaos Arena, scripted fire). A profile
  with no enabled recorded dungeon behaves exactly as today.
- Watching the screen to correct a replay mid-run. A replay stays a blind
  tape; the timeout is the safety net.
- A dungeon-only loop without trading.

## Anti-goals (a failure even if it technically works)

- **The bot adds input during a replay.** No healer, no targeting presses,
  no hotbar-tab click after Challenge: the recording is the only input
  between Challenge and the end screen.
- **A timeout stops dungeons for the day.** That is what the scripted path
  does today (`off_for_the_day`). For recorded runs, a timeout counts the
  run, leaves, and the loop continues.
- **Input keeps flowing after the end screen, a defeat, the timeout or the
  Ctrl stop.** The replay stops at once and lets go of every key and
  button it holds.
- **A broken recording sends input.** The replay refuses a recording made
  on another screen layout, or with the game window elsewhere (as
  `record_all.prepare` already does), and sends nothing.
- **A recording or exit clip is missing.** The failure is reported clearly,
  and no input is sent for that run.

## Decisions made by the user

- **Timeout exit:** a recorded exit clip per dungeon (like Chaos Arena's
  `exit_dungeon.jsonl`), played after the timeout.
- **Several enabled dungeons rotate each run** (A, B, A, B...), each until
  its own daily limit. Once one hits its limit, the others carry on.
- **One recording per dungeon.**
- **Recorded dungeons run inside the trading run.** The bot trades until a
  dungeon's start time, runs the rotation back to back with each
  dungeon's cooldown, and trades again once every enabled dungeon is at
  its daily limit for the dungeon day.
- **Bot steps around a run.** The recording covers Challenge to the end
  screen, including the user's own buffs (A+9/A+0). After the end screen,
  the bot cancels BM itself (`cancel_bm`, the dungeon's `before_exit`),
  claims the reward and leaves, then waits out the BM cooldown before the
  next run. It does not run `block_start` or `cooldown_loop` (the BM pot
  right-clicks).

## My calls (the user can overrule these)

**Configuration.** Per profile, under `dungeon` in `configs/<profile>.json`
(deep-merged onto `config.json` like every other setting):

```json
"dungeon": {
  "enabled": true,
  "recorded": {
    "fire": {
      "enabled": true,
      "recording": "cang_run.jsonl",
      "exit_recording": "cang_exit.jsonl",
      "timeout_seconds": 330
    }
  }
}
```

- Each key under `recorded` names a dungeon already defined in
  `config.json` `dungeon.dungeons`. That definition supplies:
  - the entry, location, difficulty and key purchase
  - `daily_limit`, `start_at`, `cooldown`
  - `before_exit` (BM cancel), `exit` (claim and leave), `after_exit`,
    `end_wait`

  A recorded entry may override `daily_limit`, `start_at`, `cooldown`,
  `end_wait` and `before_exit`.
- **Single-location dungeons only, for now.** A dungeon with more than one
  location (Chaos Arena's levels) is refused with a clear message.
- **Finding the files.** A name is looked up first in
  `src_1080p/dungeon/<dungeon>/` (tracked by git, so other machines get
  it), then in `src_1080p/logs/recordings/` (handy while trying a fresh
  recording), and can also be a full path.
- `config.json` gets `"recorded": {}`, so nothing changes until a profile
  turns a dungeon on.
- **When the recorded path is used.** If any recorded dungeon is enabled
  for the profile, `driver.py` uses the recorded rotation instead of
  `trading_dungeon`. Otherwise it runs today's path, unchanged.

**One recorded run**, in `dungeon.py` as `run_recorded(name, ...)`:
1. Load and check the recording with `record_all.prepare` before any
   input. A refusal fails the run and sends nothing.
2. Buy the difficulty purchase if due, then `get_entry`,
   `open_dungeon_key` and `start_dungeon` exactly as today, except without
   the hotbar tab II click: that click is not in the recording.
3. Start the end watcher (`watch_end`) in the mode that reads the
   "Cleared dungeon" banner and the defeat sign. Start the time limit at
   the dungeon's `timeout_seconds` from the Challenge click.
4. Replay the recording on a worker thread with `record_all.Replay`, its
   start cursor first. The replay's stop event is set by any of: the end
   screen, a defeat, the timeout, or the Ctrl stop. The main thread only
   waits, so a Ctrl stop interruption cannot cut short the release of
   held keys (the same pattern as `dungeon.replay_recording`). The
   replay's own Ctrl is kept from the Ctrl stop (`_replay_holds_ctrl`).
5. **If the replay ends before the end screen**, wait for the end screen
   until the time limit, sending nothing.
6. **Then, by outcome:**
   - **Cleared:** `before_exit` (BM cancel), then the existing finish.
     Wait `end_wait`, press `exit` (the confirmation that claims the
     reward, then Leave), then `after_exit`.
   - **Defeated:** press `revive` (`DEFEAT`), then play the exit
     recording.
   - **Timed out:** `before_exit`, then the exit recording. The run
     counts; there is no `off_for_the_day`.
   - **Ctrl stop:** stop as the bot does today; nothing more is sent.
7. **The run is counted at the Challenge click**, as today
   (`count_challenge`), so a timed-out or failed run still uses up one of
   the daily limit.

**Reading the end screen during the replay.** `watch_end` reads the
"Cleared dungeon" banner and the defeat sign every `end_check_every`
seconds (a new setting, default 0.5), to keep the CPU load on the game
light.

**A lost game stops the replay.** Each check also confirms the game window
still exists and is in front. If it doesn't, the replay stops at once,
lets go of everything held, and the run raises. Recovery is then left to
the driver and `supervise.py` (relog and relaunch), so a blind tape never
types into a login screen or another window.

**The rotation**, in `driver.py` as `recorded_turn()`, which replaces
`back_to_back_turn` when any recorded dungeon is enabled:
- **Which dungeons are open:** the enabled dungeons whose `start_at` has
  passed and which are under their daily limit, in config order.
- **Taking turns:** the next run goes to the open dungeon after the last
  one run, wrapping around. "Last one run" is saved in `dungeon_next.json`
  (the existing state file), so the rotation survives a restart.
- **Cooldown:** it is the character's BM cooldown, so there is one, shared
  by every dungeon. It counts from the end of the last run of any
  dungeon, and the bot waits out the cooldown of the dungeon about to
  run.
- **Between runs:** the world boss check runs between runs, as today.
- **Back to trading** when no dungeon is open. The next wake-up is the
  earliest `start_at` or the next dungeon day.
- **A run that raises** (not a timeout, which is handled):
  - It is logged with a screenshot, as today.
  - If it raised after the Challenge click and the game is still in front,
    the bot first plays that dungeon's exit clip, so the character isn't
    left inside the dungeon.
  - That one dungeon is turned off for the rest of this trading run. The
    rotation carries on with the others.
  - If it was the last one left, the bot goes back to trading.
- **A missing recording, or one `prepare` refuses,** turns that dungeon
  off for the run, with the reason in the log.

**Recording a run:** click Challenge, press Ctrl twice, play the dungeon,
and press Ctrl twice once the "Cleared dungeon" banner shows. **Recording
the exit clip:** in the dungeon, press Ctrl twice, leave by hand, and press
Ctrl twice once outside. Both go in the design notes and in `dungeon.py`'s
help text.

**Clean stops.** A recorded run logs `RECORDED RUN start <dungeon>` and
`RECORDED RUN end <dungeon>`. `tools/cleanly_terminate.py` waits for the
end line before its Ctrl stop, as it already does for the Chaos Arena
lines.

## Left to the builder

- The exact split of functions between `dungeon.py` and `driver.py`, and
  how much of `run_dungeon` is shared rather than copied. The scripted
  path must not change behaviour.
- **Tests come first**, in `src_1080p/tests/`, with the game mocked:
  - fake `get_entry`, `open_dungeon_key`, `start_dungeon` and the end
    screen reads
  - a fake clock or fast timers
  - `fake_input.FakeInput` taking the replay's input
- **Scenarios to cover:**
  - a cleared run; a run where the replay ends first and the end screen
    follows; a timeout; a defeat; a Ctrl stop mid-replay
  - a missing recording; a recording refused by `prepare`; a missing exit
    recording
  - the rotation across two or three dungeons with different daily limits
    and start times, plus its state surviving a restart
  - cooldowns; a profile with nothing enabled (today's path, unchanged)
  - no bot input between Challenge and the end screen; nothing left held
    in every ending

## Revision after the final review (2026-10-19)

- **Losing focus vs losing the game.**
  - If the game window still exists but another window took focus, the
    bot brings the game back, cancels BM and plays the exit clip. The run
    ends as `lost_focus`.
  - If the game window is gone, or won't come back to the front, the run
    raises `GameLost`. That stops the rotation, so it never starts the
    next dungeon with the character possibly inside one.
- **The exit clip is guarded too:** it stops the moment the game loses
  focus or goes away.
- **The exit clip plays at most once,** and never after the character has
  left. Leaving is marked done before the exit clip plays and as soon as
  the Leave button is pressed.
- **The day is read before the Challenge click,** and the recording is
  loaded before it too, so nothing slow sits between the click and the
  replay. With no online clock the run is not counted (it is never
  counted against no day, which would wipe the day's counts).
- **A "Cleared" banner up at the deadline counts as a clear, not a
  timeout,** so the reward is claimed.
- **A file that can't be read** (wrong encoding, damaged) is a refusal, so
  that dungeon is turned off rather than crashing the bot.
- **A recorded dungeon must have a daily limit.** Without one, its runs
  would go on back to back and trading would never resume.
- **`after_exit` can be set per recorded dungeon,** like `before_exit`.
- **The input probe** stops the moment its black screen loses the
  foreground, and skips Alt as well as Win keys.
