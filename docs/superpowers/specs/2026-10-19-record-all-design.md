# Record everything, replay it later: design

Date: 2026-10-19
Status: approved 2026-10-19

## Why

`tools/record_action.py` records keys and clicks, but it throws away
*injected* input, which is any input sent through Windows `SendInput`
(`LLKHF_INJECTED` / `LLMHF_INJECTED`, filtered in `hook()`). The bots
(`dungeon.py`, `driver.py` and the others) send all their input through
`SendInput` (see `calibration.py`, `open_inventory.py`). So recording
while `py dungeon.py fire --runs 2 --config cang` runs captures none of
the bot's actions.

The user wants one command that captures **every** keyboard and mouse
action (the bot's and their own), and a second command that plays it all
back later, the same way it happened, without the bot running.

## Goals

1. **Record everything.** Capture every key, mouse button, wheel and mouse
   move, injected or physical, with timestamps precise to the microsecond
   (`time.perf_counter`, as `record_action.py` does).
2. **Record a command's run.** One command starts recording, launches a
   given command (for example the dungeon bot), and stops recording when
   that command exits. The recording then covers exactly that run.
3. **Replay.** One command plays a recording back through `SendInput`
   with the original gaps between events, starting from the cursor
   position the recording began at.

## Non-goals

- **Watching the screen during replay.** Replay is a blind tape: it does
  not wait for loading screens, lag or monster positions. The user
  accepted that. Waiting until the screen matches what it showed when a
  step was recorded is a possible later step, not part of this work.
- Changing `record_action.py`'s behaviour. Its manual recording, `screen`
  mode, `replay` and `submit` stay as they are.
- Editing recordings, looping replays, or changing replay speed.
- Recording screen frames. `dungeon.py --frames` already does that if it
  is wanted alongside.

## Anti-goals (it would be a failure even if it technically worked)

- **The bot's own key presses start or stop the recorder.** The bot sends
  Ctrl itself (`calibration.ctrl_click`, which does Ctrl+click).
  Only physical Ctrl taps may control the recorder.
- **A replay leaves a key or button held down.** That includes a stopped
  or crashed replay, and a recording that ends mid-press (for example the
  bot killed during a Ctrl+click). Everything held must be released, as
  `record_action.py`'s replay already does.
- **A replay plays on the wrong screen layout.** Positions would land in
  the wrong places. Refuse, as `record_action.py` does.
- **A replay starts by itself.** It only starts on a physical Ctrl x2
  gesture, so the user has time to focus the game.
- **Anything is lost when the command crashes or is stopped.** If the
  launched command exits with an error, or the user stops the bot with
  its own Ctrl x4, the recording up to that point is still saved.

## Decisions

Made by the user:
- Replay is blind and time-based (see Non-goals).
- All three parts are in scope: record everything, record a command's
  run, and replay.

My calls (the user hasn't confirmed these, so they can overrule them):
- **New file `tools/record_all.py`.** It imports and reuses
  `record_action.py`'s pieces (`hook`, `pump`, `Gesture`, `save`,
  `Signal`, `play_one`, `Replay`, `wait_until`, the structs, and the
  `tools.recorder` config) instead of copying them. Where a shared piece
  needs a new option, for example a `hook()` that can keep injected
  events, add it with a default that leaves `record_action.py` behaving
  exactly as before.
- **Same file format** as `record_action.py`: a JSONL file with a `start`
  header line, then one event per line. Each event gets a new field,
  `"injected": true|false`, so the bot's input can be told apart from the
  user's. The header also stores the launched command line, if there was
  one. Files go to the same `src_1080p/logs/recordings/` folder, named
  `YYYY-MM-DD_HHMMSS_all.jsonl` so they are easy to tell apart from
  `_actions.jsonl` files.
- **Commands:**
  - `py tools/record_all.py` records right away and stops on a physical
    Ctrl x2 (within `toggle_window`, as configured). There is no Z
    segmenting: the bot types, so Z can't be a control key here.
  - `py tools/record_all.py run <command...>` starts recording, launches
    `<command...>` as a child process in the same console (the bot's
    output still shows), and stops and saves when the child exits. While
    a child is running, the recorder's Ctrl x2 gesture is off. The bot's
    own Ctrl x4 stop would otherwise trigger it early. The user stops
    the run with the bot's Ctrl x4, or with Ctrl+C. Either way the child
    exits and the recording is saved. The recorder exits with the child's
    exit code.
  - `py tools/record_all.py replay [file]` replays the newest `_all.jsonl`,
    or the given file. It checks the screen and desktop layout, moves the
    cursor to the recorded start position, waits for a physical Ctrl x2,
    then plays. A physical Ctrl x2 during playback stops it early.
    Injected and physical events are replayed alike.
- **Timing.** Replay schedules each event at `start + t`, using the
  existing `wait_until` and 1 ms timer resolution. It never sleeps for
  the gap after the previous event, because slept gaps add up to drift
  over a long run.
- **Cursor moves that bypass the hook.** The bots move the cursor with
  `SetCursorPos` (`calibration.py:495`, `:509`, `:999`; `dungeon.py:516`,
  `:526`, `:593`, `:629`, `:1676`; `buy.py:73`; `cashshop.py:381`). Then
  they click with no coordinates. `SetCursorPos` doesn't go through the
  low-level mouse hook, so these moves are invisible to it. Clicks still
  carry the right position, but hovers (parking on a tab, waiting for a
  tooltip) would be lost. "The same way it happened" includes hovers, so
  the recorder also runs a cursor-polling thread. It reads
  `GetCursorPos` about every 1 ms and records a `move` event, marked
  `"source": "poll"`, whenever the position changed and the hook didn't
  report it. Replay plays these like any other move. The poll interval
  goes in config.
- **Typed text.** The relog in `recovery.py` (around lines 271-281)
  types with `KEYEVENTF_UNICODE`. The hook sees that as `VK_PACKET`, with
  the character in the scan code. Replaying it as a normal scan-code key
  would type the wrong thing, so these events are replayed with
  `KEYEVENTF_UNICODE`.
- **Typed text never reaches disk** (the user left this to me). The
  only typed text in the codebase is the relog's username and password.
  Each typed character is written as `"secret": null` while recording.
  Once recording stops, each run of typed characters is compared with the
  account's username and password (from `CABAL_USERNAME` /
  `CABAL_PASSWORD`, or `src_1080p/account.json`, the same places
  `recovery.account()` reads). A match is labelled `"secret": "username"`
  or `"secret": "password"`, with `"i": <index>`; the character itself is
  never stored. Replay fills the characters from the same source. It
  refuses if a needed credential is missing, and skips (with a warning)
  any typed character it can't identify. The sub-password keypad is
  clicked, not typed, so those clicks are recorded as plain clicks.
- **Saving as it goes.** A writer thread appends new events to the file
  about once a second, so a crash or a closed window loses at most about
  a second. On a clean stop the file is rewritten once with the final
  form: the stop gesture removed and the typed characters labelled.
- **Cursor start.** Replay moves the cursor to the recorded start
  position right when playback starts (after the Ctrl x2), not while it
  waits. That way the user can still use the mouse to focus the game.
- **Recording overhead.** The hook callbacks only stamp and append. They
  do no slow work such as I/O, because Windows drops slow low-level hooks
  and that would also slow the bot's input. `key_name` lookups may move
  to save time if they turn out to be slow.
- The old file's banner, its config section and its console style are
  reused. Any new tunables go in `tools.recorder` in `config.json`, not
  hard-coded.

## Left to the builder

- How `record_action.py` is refactored to share its code, as long as its
  command-line behaviour doesn't change.
- How the child process is launched and how Ctrl+C is passed to it
  (same console, so the bot gets Ctrl+C itself).
- Tests: the pure parts (gesture filtering of injected Ctrl, event
  stamping, file format, scheduling against the clock, releasing what's
  held) should get unit tests in `src_1080p/tests/` or `unit_tests/`,
  whichever matches the existing pattern. The hooks themselves are
  checked by hand on the real machine: record `dungeon.py fire --runs 1`,
  replay it, and check the game does the same.
