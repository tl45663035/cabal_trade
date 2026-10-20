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

## Revision after the first dry run (2026-10-19)

The first hand recording (`2026-10-19_154052_all.jsonl`, 73 s, 5,513
events) replayed inaccurately. It began with the cursor on another
monitor (where the terminal was), and its first click, a right click at
(1114, 518), was the click that brought the game to the front. On replay
the game was already in front, so that click reached the game as a real
action, and everything after it started from a different state. Fixes:

- **A hand recording starts on Ctrl x2, not at launch.** The user clicks
  into the game, sets it up, then presses Ctrl twice. The focusing click
  and the start gesture are never recorded.
- **The game window is part of the recording.** The header stores the
  `PlayCabal` window rectangle (title from `calibration.json`). Replay
  refuses if the game is closed or the window has moved, because every
  click would land off by the difference. Replay brings the game to the
  front itself before sending anything, and sends nothing if it can't.
- **A start picture.** A screenshot of the game is saved as
  `<name>_start.png` when recording starts. Replay names it so the user
  can set up the same spot, facing, camera and zoom. Just before playing,
  replay prints how much the screen differs from it. A blind replay only
  follows if the game starts in the same state.
- **Timing report.** Replay measures how late each event went out against
  its recorded moment, and prints the mean, median and worst.
- Smaller fixes:
  - Events are stamped the moment the hook sees them.
  - The final file is sorted by time.
  - A file cut off mid-line by a crash still replays.
  - Two recordings in the same second get separate files.
  - The start screenshot is taken off the hook thread, so input never
    stalls (Windows drops a low-level hook that is too slow).

These were checked against the dry-run file and found sound:
- Positions map back to the same pixel on the 5360x1440 three-monitor
  desktop.
- Auto-repeat key downs are kept and replayed.
- Moves with no pixel change (13% of free moves; sub-pixel mouse motion)
  are harmless.
- No evidence was found that the game pins the cursor during a drag.

That last point is left open. If camera turning (right drag) turns out
not to replay, the game is reading raw mouse deltas, and the recorder
would also need Raw Input.

## Revision: long replays and camera turns (2026-10-19)

Long hand recordings drifted off course. Short ones stayed close. The
recordings show why: each 4-5 minute run turns the camera with the right
button 67-86 times, some turns lasting 3-7 seconds. Games turn the camera
by the mouse's raw counts, before Windows applies pointer speed and
acceleration, not by where the cursor lands. Replaying cursor positions
turns the camera by a different amount. Each turn leaves the facing a
little off, so W and ground clicks go somewhere else, and the errors add
up over a long run.

- **The recorder also listens to Raw Input.** It uses a message-only
  window with `RIDEV_INPUTSINK`. Each physical relative movement is saved
  as `{"action": "raw", "dx", "dy"}`. The bot's input (no device) and
  absolute movement are left out.
- **Turns replay from the raw counts.** While a turn button
  (`all_turn_buttons`, default `["right"]`) is held, replay sends the raw
  counts as relative moves and skips the cursor positions. On release it
  lets go of the button without moving, then puts the cursor back where
  the recording had it. Outside a turn, positions are used as before.
- **Older recordings still play as before.** A recording without raw
  counts replays drags from cursor positions.
- **Special keys (Esc, Enter, arrows, F-keys, Alt, Win, Tab, Backspace)**
  were already recorded and replayed. Replay sends the scan code plus the
  extended flag, the same way the bot sends Esc. The tests now cover them.

Still unverified in the game: whether Cabal turns by raw counts (likely)
or by cursor movement. If by cursor, a relative move still moves the
cursor, with the same pointer speed applied, so the turn stays close
either way.

## Revision: deep check of recorder against replayer (2026-10-19)

The new recording (`2026-10-19_185612_all.jsonl`, 282.6 s, 37,683
events), the 30-run log and the code were checked against each other.

**Checked and sound:**
- **Raw counts line up with cursor moves.** Every physical cursor move has
  its raw count (18,330 of each). The raw count arrives 0.09 ms later
  (median; worst 1.3 ms). None fall on the wrong side of a button press
  or release.
- **Pointer acceleration is off on this machine.** "Enhance pointer
  precision" is off and pointer speed is 8, so the cursor moves at 0.75x
  the raw counts with no curve. A replayed turn therefore turns the camera
  by the same raw counts and moves the cursor by the same pixels.
- **Replay timing is tight.** Events are sent a median of 0.00 ms after
  their recorded moment, worst 2-5 ms.

**Defects found and fixed:**
1. **The Ctrl stop could leave keys and buttons held.** The stop
   interrupts the main thread (`interrupt_main`), and the 19:01 log shows
   that interruption landing inside the replay's release. `dungeon.py`
   now plays on a worker thread, and the main thread only waits on it.
2. **The Ctrl stop reads the key state, which includes the replay's own
   Ctrl.** The recording uses Ctrl+1 and Ctrl+2. While the replay holds
   Ctrl, and for 0.1 s after (`OWN_GRACE`) while the key state catches
   up, it is marked as the bot's own (`calibration._OWN_CTRL`).
3. **The PaddleOCR reader loaded in the background during
   `dungeon.py replay`.** It took CPU from the game and the replay while
   reading nothing. It is no longer started for `replay`.
4. **`record_all.py replay` had a keyboard hook in the input path.** It
   watched for Ctrl, and every replayed key waited for that Python
   callback, which needed the GIL while the play thread was busy-waiting
   (up to about 5 ms). Ctrl is now read from the key state on a 10 ms
   poll (`CtrlTaps`), ignoring the replay's own Ctrl.
5. **Lateness was measured before sending.** It is now measured after.
6. **A turn's press went out with an absolute move in the same event.**
   The cursor now moves first, and the button is pressed with no movement
   of its own, so the game never sees an absolute jump while the button
   is held.
7. **`compare RECORDING CAPTURE` measures what the game actually got.**
   Make the capture with run mode, for example
   `record_all.py run py src_1080p\dungeon.py replay --runs 1 --config cang`.
   The two files are lined up by their keys, clicks and wheel turns. It
   reports anything missing or extra, timing spread with the start offset
   taken out, drift from start to end, and clicks landing more than
   `all_compare_click_px` away. Turn releases are reported separately.
   `dungeon.py replay` also takes `--runs N`.

**What input fidelity cannot fix.** The game reads input once per frame,
so a key held 100 ms is seen for 6 or 7 frames depending on where the
frames fall. Every walk, cast and turn can be off by one frame, and over
hundreds of presses that wanders. Monsters, other players and server lag
also differ between runs, and each loop starts where the last one ended.
If `compare` shows the input arriving intact and runs still differ, the
difference is the game's. Closing it means the replay watches the screen
and corrects course, which is a different design from replaying a tape.
