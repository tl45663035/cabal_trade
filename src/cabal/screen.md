# screen - talking to Windows and to the Cabal client

Capture the screen, find the game's window, move and press. Nothing here knows
what a Trade window is.

## Why DPI comes first

Windows lies to a process that has not declared itself DPI-aware: it reports a
virtual screen size and silently rescales coordinates. A capture then comes
back at one size while the cursor lands somewhere else. It looks exactly like
bad calibration and no amount of re-measuring fixes it.

`make_dpi_aware()` is idempotent and runs before every capture and every move.

## Functions

| Function | Returns |
|---|---|
| `make_dpi_aware()` | - |
| `screen_size()` | `(w, h)` of the PRIMARY monitor in real pixels |
| `grab()` | a `PIL.Image` of the primary monitor |
| `find_game_window()` | window handle, or `None` |
| `client_rect()` | `(l, t, r, b)` of the client area, or `None` |
| `focus_game(timeout)` | `True` when the client holds the foreground |
| `move_mouse(x, y)` | `False` if Windows refused |
| `click(x, y, right=False)` | `False` if the move was refused |
| `press_escape()` | - |
| `wait_until(predicate, timeout)` | `True` if it became true in time |

## Rules

- **The primary monitor, not the virtual desktop.** The union of all monitors
  has its own origin, and capturing it shifts every pixel by that origin, so a
  coordinate measured in the capture no longer matches the one a click uses.
- **The client area, not the window rect.** Title bar and borders are not part
  of the rendered UI; including them shifts every derived region.
- **The foreground is verified by reading it back.** `SetForegroundWindow`
  returns success in cases where it has quietly done nothing.
- **A refused move usually means UIPI**, not bad coordinates: if the game runs
  elevated and this process does not, Windows drops injected input. The script
  has to run as Administrator too.
- **`click()` does not guarantee hover.** A move to the pixel the cursor
  already occupies raises no move event, so a control that arms on hover is
  never armed. Callers that need hover move somewhere else first.
