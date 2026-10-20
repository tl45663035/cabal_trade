# Recorded Dungeon Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inside the trading run, run each profile's enabled recorded
dungeons in rotation. Each run is: enter, click Challenge, replay the
recording, then cancel BM, claim and leave. Each dungeon has a timeout,
after which the bot plays that dungeon's recorded exit clip and goes on to
the next run.

**Architecture:**
- A new module, `src_1080p/recorded_dungeon.py`, holds the recorded path:
  settings and file lookup, one run, and the rotation.
- It reuses `dungeon.py` for entry, Challenge, the end-screen reads and the
  exit buttons, and `tools/record_all.py` for the replay.
- `driver.py` picks the recorded rotation when any recorded dungeon is
  enabled; otherwise it runs today's path, untouched.

**Tech Stack:** Python 3.13 and ctypes. Tests are plain scripts in
`src_1080p/tests/` (`check()` with an exit code), run by
`tests/run_tests.py`. Input is mocked with `tests/fake_input.py` and
`tests/no_input.py`.

**Spec:** `docs/superpowers/specs/2026-10-19-recorded-dungeons-design.md`

## Global Constraints

- **No bot input between Challenge and the end screen** except the replay
  itself: no healer, no targeting presses, no hotbar tab II click.
- **After the end screen:** `before_exit` (BM cancel), then `end_wait`, then
  `exit` (claim and Leave), then `after_exit`. There is no `block_start`
  and no `cooldown_loop`.
- **A timeout never calls `off_for_the_day`.** The run counts, the bot runs
  `before_exit` and then the exit recording, and the loop goes on.
- **The timeout clock starts at the Challenge click**, from
  `recorded.<name>.timeout_seconds`.
- **File lookup order:** a full path, then `src_1080p/dungeon/<dungeon>/`,
  then `src_1080p/logs/recordings/`.
- **Single-location dungeons only.** Any other is refused with a message.
- **The cooldown is shared** (it is the BM cooldown). It counts from the end
  of the last run of any dungeon, and its length is the cooldown of the
  dungeon about to run.
- **Turn order** (by turns): the open dungeon after the last one run, in
  config order, wrapping around. The last one run is saved as
  `recorded_last` in `dungeon_next.json`.
- **Log lines:** `RECORDED RUN start <dungeon>` and
  `RECORDED RUN end <dungeon> (<outcome>)`, each indented two spaces.
- **End-screen checks** run every `dungeon.end_check_every` seconds
  (default 0.5). Each check also confirms the game window exists and is in
  front.
- **Defaults stay off:** `config.json` `dungeon.recorded` is `{}`, so
  nothing changes for any profile until it turns a dungeon on.

## Review Focus

- **An end screen that shows the instant the replay finishes.** The bot
  must exit once, not twice. Test: `cleared_right_at_the_end` in Task 2.
- **The Ctrl stop during the BM cancel or exit buttons** (after the
  replay). It must not play the exit clip, and must stop at once. Test:
  `ctrl_stop_after_the_replay` in Task 2.
- **A profile with an enabled dungeon whose `start_at` is in the future
  while another is open.** Only the open one runs. Test:
  `start_times_gate_each_dungeon` in Task 3.
- **Every enabled dungeon refused at check time** (bad files). The bot goes
  back to trading with a message, and doesn't loop. Test:
  `all_refused_goes_back_to_trading` in Task 3.
- **An exit clip that itself raises after a timeout.** The run fails
  loudly and that dungeon turns off; the exception is not swallowed. Test:
  `exit_clip_fails` in Task 2.

---

### Task 1: Settings, file lookup and checks

**Files:**
- Create: `src_1080p/recorded_dungeon.py`
- Modify: `src_1080p/config.json` (in `dungeon`, add `"recorded": {}` and
  `"end_check_every": 0.5`)
- Test: `src_1080p/tests/recorded_dungeon_test.py`

**Interfaces:**
- Produces:
  - `enabled() -> list[str]`: the names under `dungeon.recorded` with
    `enabled` true, in config order.
  - `settings(name) -> dict`: the dungeon definition from
    `dungeon.DUNGEONS[name]`, overlaid with the recorded entry. Keys:
    `recording`, `exit_recording`, `timeout_seconds`, `daily_limit`,
    `start_at`, `cooldown`, `end_wait`, `before_exit`, `exit`,
    `after_exit`.
  - `find(name, file) -> Path | None`: looks the file up in the order set
    in Global Constraints.
  - `problem(name) -> str | None`: why a dungeon can't run. The reasons are
    not single-location, a missing recording or exit recording, a missing
    `timeout_seconds`, or `record_all.prepare` refusing either file.
    Otherwise `None`.

- [ ] **Step 1: Write the failing tests:**
  - `enabled_in_config_order`: with `recorded = {"b": {enabled: true}, "a":
    {enabled: false}, "c": {enabled: true}}`, `enabled() == ["b", "c"]`.
  - `settings_overlay`: a recorded `cooldown: 10` beats the dungeon's 35;
    the dungeon's `exit` comes through unchanged.
  - `find_order`: with the same name in both folders, `find` returns the
    `dungeon/<name>/` one. A name only in `logs/recordings` returns that
    one. An absolute path returns itself. A missing file returns `None`.
  - `problem_reasons`: each of these returns a message containing the
    quoted word: a multi-location dungeon ("one location"), a missing
    recording ("recording"), a missing exit ("exit"), no timeout
    ("timeout"), a recording made on another screen ("would not line up").
    A good entry returns `None`.
  - Use a temporary folder for both lookup folders by patching
    `recorded_dungeon.SEQUENCES` and `record_action.OUT`.
- [ ] **Step 2: Run it:** `py src_1080p/tests/recorded_dungeon_test.py`.
  Expected: an ImportError (`recorded_dungeon`).
- [ ] **Step 3: Implement** the four functions. Add `"recorded": {}` and
  `"end_check_every": 0.5` to the `dungeon` section of `config.json`.
- [ ] **Step 4: Run it.** Expected: `N checks, 0 failed`.
- [ ] **Step 5: Commit:** "Recorded dungeons: settings, file lookup and checks".

### Task 2: One recorded run

**Files:**
- Modify: `src_1080p/recorded_dungeon.py`
- Test: `src_1080p/tests/recorded_dungeon_test.py`

**Interfaces:**
- Consumes:
  - from Task 1: `settings`, `find`
  - from `dungeon`: `run_sequence(name, location, {}, verbose, steps,
    False)` (for the `get_entry`, `open_dungeon_key`, `before_exit` and
    `after_exit` steps), `count_challenge`, `press_buttons`, `DEFEAT`,
    `exit_dungeon`, `read_windows`, `CLEARED_WINDOW`, `CLEARED_TEXT`,
    `DEFEAT_SIGN`, `CHALLENGE_WINDOW`, `CHALLENGE_WORD`, `wait_for`,
    `centre`, `click`, `focus_game`, `_replay_holds_ctrl`, `internet_now`,
    `only_location`
  - from `record_all`: `prepare`, `Replay`, `find_game`
  - from `calibration`: `STOPPED`, `grab`, `snap`, `halt_if_stopped`
- Produces:
  - `run(name, verbose=True) -> str`, returning `"cleared"`, `"defeated"` or
    `"timeout"`. It raises on anything else. On a Ctrl stop, it raises
    whatever `calibration.halt_if_stopped` raises.
  - `challenge(verbose) -> float`: the monotonic time of the Challenge
    click. It is `dungeon.start_dungeon` without the tab II click.
  - `watch(stop, outcome, deadline, every)`: a thread target. It sets
    `outcome["end"]` to `"cleared"`, `"defeated"`, `"timeout"` or
    `"lost_game"`, then sets `stop`.

- [ ] **Step 1: Write the failing tests.**
  - **Fakes:**
    - Patch `dungeon.run_sequence`, `count_challenge`, `press_buttons` and
      `exit_dungeon` to log their calls.
    - Patch `recorded_dungeon.challenge` to return the current time.
    - Patch the screen reads with a scripted function returning "",
      "Cleared dungeon", or the defeat word once a set time has passed.
    - Patch `record_all.focus_game`, `find_game` and the foreground check.
    - Use `fake_input.replay_session`'s patches: `ra.send` goes to
      `FakeInput`, and `ra.wait_until` to the fake clock.
    - Recordings are made with `fake_input.record_session`.
  - **Tests:**
    - `cleared`: the screen shows the banner partway through the
      recording. `run()` returns `"cleared"`. Then:
      - The calls are `get_entry, open_dungeon_key, challenge,
        before_exit, exit, after_exit`, in that order.
      - Input stops at the banner; no send happens after it.
      - The fake game holds nothing.
      - No input is sent between Challenge and the first replay event
        except the start-cursor move.
    - `cleared_right_at_the_end`: the banner shows only after the replay's
      last event. Exactly one `exit` call.
    - `replay_ends_first`: the banner shows 2 s after the last event.
      `"cleared"`, and no input in between.
    - `timeout`: there is no banner, and `timeout_seconds` is shorter than
      the recording. Returns `"timeout"`. Then:
      - The calls end `before_exit, <exit recording replayed>`.
      - `dungeon.off_for_the_day` is not called.
      - Nothing is held.
    - `defeated`: the defeat word shows. The calls end `press_buttons(DEFEAT),
      <exit recording>`.
    - `lost_game`: the game window goes away mid-replay. Input stops, `run`
      raises RuntimeError containing "game", and no exit clip is played.
    - `ctrl_stop_mid_replay`: `calibration.STOPPED` is set mid-replay.
      Nothing is held, and no exit clip is played.
    - `ctrl_stop_after_the_replay`: STOPPED is set during the `before_exit`
      call. No exit clip and no `exit`.
    - `raises_after_challenge`: the replay's prepare passes, but the screen
      reads raise. The exit clip is played (the game is in front), then the
      error re-raises.
    - `exit_clip_fails`: a timeout whose exit recording is refused by
      prepare. `run` raises RuntimeError naming the exit recording.
    - `log_lines`: stdout holds `  RECORDED RUN start fire` and
      `  RECORDED RUN end fire (cleared)`.
    - `checks_at_its_rate`: with `end_check_every` 0.5 over a 3 s run, the
      screen is read 5 to 7 times.
- [ ] **Step 2: Run it.** Expected: an AttributeError (no `run`).
- [ ] **Step 3: Implement `run`:**
  - First `problem(name)`; a problem raises before any input.
  - Log `RECORDED RUN start <name>`.
  - `run_sequence` for `["get_entry", "open_dungeon_key"]`.
  - `challenge()`, then `count_challenge(name, location, {"started":
    internet_now()})`.
  - Start `watch` with `deadline = clicked + timeout_seconds` and `every =
    end_check_every`.
  - Play `Replay(events, head["cursor"], head, base,
    on_ctrl=dungeon._replay_holds_ctrl)` on a worker thread whose `stop` is
    the watcher's stop. The main thread joins in 0.1 s slices, sets `stop`
    on KeyboardInterrupt, and also sets it when STOPPED is set.
  - After the replay, wait on `stop` until the deadline.
  - Then branch on `outcome["end"]`:
    - `"cleared"`: `before_exit`; sleep `end_wait`; `exit_dungeon`;
      `after_exit`.
    - `"defeated"`: `press_buttons("defeat", DEFEAT)`, then the exit
      recording.
    - `"timeout"`: `before_exit`, then the exit recording.
    - `"lost_game"`: raise.
  - A raise after the Challenge click: if `focus_game()` succeeds, play the
    exit recording, then re-raise.
  - Before every step after the replay, call `calibration.halt_if_stopped()`.
  - Log `RECORDED RUN end <name> (<outcome>)` in a `finally` with the
    outcome, or `failed` if the run raised.
- [ ] **Step 4: Run the tests.** Expected: `N checks, 0 failed`. Also run
  `py src_1080p/tests/record_replay_test.py`, which must still pass.
- [ ] **Step 5: Commit:** "Recorded dungeons: one run, with timeout, exit clip and end handling".

### Task 3: The rotation inside trading

**Files:**
- Modify: `src_1080p/recorded_dungeon.py`
- Modify: `src_1080p/driver.py` (`dungeon_turn`, around line 4597)
- Test: `src_1080p/tests/recorded_dungeon_test.py`

**Interfaces:**
- Consumes:
  - from Tasks 1 and 2: `enabled`, `settings`, `problem`, `run`
  - from `dungeon`: `until_open(name)` (it reads `start_at` from
    `DUNGEONS`, so pass the overlaid value through a small wrapper),
    `runs_today`, `until_next_day`, `_state`, `_save`, `wait_timer`
- Produces:
  - `pick(open_names, order, last) -> str | None`: the first name in `order`
    after `last` (wrapping) that is in `open_names`.
  - `turn(off: set, rested: dict, verbose=True) -> tuple[bool, float |
    None]`: returns whether it acted, and the monotonic due time of the
    next check.
    - It runs runs back to back while any enabled dungeon is open.
    - `off` holds the dungeons turned off for this trading run.
    - `rested["at"]` is the end of the last run.
  - In `driver.py`: `recorded_turn(verbose)`, called from `dungeon_turn`
    before the `back_to_back` check when `recorded_dungeon.enabled()` is
    non-empty. It keeps `off` and `rested` in `_DUNGEON`, and sets
    `_DUNGEON["due"]` from the returned due time.

- [ ] **Step 1: Write the failing tests:**
  - `pick_rotates`: `pick({"a","b","c"}, ["a","b","c"], "a") == "b"`; `pick(...,
    "c") == "a"`; `pick({"a","c"}, ..., "a") == "c"`; `pick(set(), ...,
    None) is None`.
  - `rotation_runs_by_turns`: patch `run` to log names. The limits are a:2
    and b:3, both open. `turn()` runs `a, b, a, b, b`, then returns
    `(True, due)`. `recorded_last` in the state file is `"b"`.
  - `rotation_survives_restart`: the state has `recorded_last: "a"`, so a
    fresh `turn()` starts with `b`.
  - `start_times_gate_each_dungeon`: `a` is open and `b` opens in 600 s.
    Only `a` runs, and the due time is about 600 s away.
  - `shared_cooldown`: the cooldowns are a:10 and b:0. After a run ends at
    t, the next run of `b` starts at once and the next run of `a` waits
    until t+10. Use a fake `wait_timer` that records its seconds.
  - `timeout_counts_and_continues`: `run` returns `"timeout"` for `a`, and
    the next run still happens.
  - `failure_turns_one_off`: `run` raises for `a`. `a` goes into `off`
    with a screenshot taken, and `b` still runs.
  - `all_refused_goes_back_to_trading`: every dungeon has a `problem`.
    Returns `(False, None)` and prints each reason once, with no
    retrying loop.
  - `driver_picks_recorded`: with `recorded` enabled, `dungeon_turn`
    calls `recorded_turn` and never `back_to_back_turn` or `dungeon.start`.
    With none enabled, it takes today's path. Patch both turn functions.
- [ ] **Step 2: Run it.** Expected: failures for `pick` and `turn`.
- [ ] **Step 3: Implement `pick` and `turn`.**
  - **Before each run:** check `calibration.halt_if_stopped()`, run
    `world_boss_turn` between runs through a callback argument `between`,
    and wait out the shared cooldown with `dungeon.wait_timer` (no
    `cooldown_loop`).
  - **Recording the turn:** save `recorded_last` after each run.
  - **The driver side:** implement `recorded_turn` in `driver.py` and the
    one-line branch in `dungeon_turn`.
- [ ] **Step 4: Run the tests.** Expected: `N checks, 0 failed`. Also run
  `py src_1080p/tests/run_tests.py`; only the five known failures may
  remain (`absolute_rules`, `relist_fixes`, `relist_overlap`,
  `tab4_convert_orders`, `wrong_item`).
- [ ] **Step 5: Commit:** "Recorded dungeons: rotation by turns inside the trading run".

### Task 4: Clean stops, profile entries and recording notes

**Files:**
- Modify: `tools/cleanly_terminate.py:22-28` (the `ARENA_START` and
  `ARENA_END` patterns)
- Modify: `src_1080p/configs/cang.json`, `trung.json`, `bot.json`: add a
  disabled example under `dungeon.recorded`
- Modify: `src_1080p/dungeon.py` (the `command` help text: how to record a
  run and an exit)
- Test: `src_1080p/tests/clean_stop_arena_test.py`

**Interfaces:**
- Consumes: the log lines from Task 2.

- [ ] **Step 1: Write the failing tests** in `clean_stop_arena_test.py`:
  - `open_arena` is true for a log ending in `  RECORDED RUN start fire`,
    and false once `  RECORDED RUN end fire (timeout)` follows.
  - The existing Chaos Arena cases still pass.
- [ ] **Step 2: Run it.** Expected: FAIL on the recorded lines.
- [ ] **Step 3: Implement.**
  - Add `|^  RECORDED RUN start \S+` to `ARENA_START` and `|RECORDED RUN
    end \S+` to `ARENA_END`.
  - Add `"recorded": {"fire": {"enabled": false, "recording":
    "<profile>_fire_run.jsonl", "exit_recording":
    "<profile>_fire_exit.jsonl", "timeout_seconds": 300}}` to each profile,
    with `<profile>` replaced by `cang`, `trung` and `bot`.
  - Extend the help text.
- [ ] **Step 4: Run** `clean_stop_arena_test.py` and `recorded_dungeon_test.py`.
  Expected: both pass.
- [ ] **Step 5: Commit:** "Recorded dungeons: clean stop waits for a run; profile entries".
