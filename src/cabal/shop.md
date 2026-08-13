# shop - the Trade window and its tabs

Everything here answers a question by **looking**, never by remembering.

A cached "the window is open" is worth nothing. The window can be closed by the
player, by a disconnect, or by the game itself, and a click sent at a
coordinate with no window under it is not a no-op - it is a move order into the
3D world that walks the character away.

## Functions

| Function | Returns |
|---|---|
| `trade_window_open(layout, source)` | True when the window is on screen |
| `purchase_tab_open(layout, source)` | True when the Purchase tab is showing |
| `register_tab_open(layout, source)` | True when the Register tab is showing |
| `open_purchase_tab(layout, ...)` | True when it is showing |
| `open_register_tab(layout, ...)` | True when it is showing |
| `close_shop(layout, ...)` | True when the window is gone |
| `open_agent_shop(layout, ...)` | **always False** - see below |

## Rules

- **Both markers, never one.** The two tabs share a window, so a single word
  proves nothing, and clicking a Purchase coordinate while Register is showing
  hits the listings table instead of the search controls.
- **The outcome is trusted, not the click.** A tab is fixed furniture at a
  known coordinate, so a wrong point costs a timeout rather than a wrong
  action - its only neighbour is the other tab, which is the state the call was
  made to leave.
- **`close_shop` uses Escape, not the close button.** The button moves with the
  window; the key does not. It never raises: a tidy-up that throws would
  replace the caller's result with a crash.

## open_agent_shop is a named gap

Opening the shop without walking to the NPC means right-clicking the Agent Shop
key in the inventory: toggle the panel, switch to the key's tab, right-click a
slot in a grid.

That last step is why it is not implemented. **A right-click on an inventory
slot USES what is in it.** If the panel geometry is one slot out - and the panel
is anchored to the client's right edge, so it moves with the window rather than
with the Trade frame - the click consumes whatever is actually there. There is
no undo, and the failure is silent: the shop simply does not open and something
in the bag is gone.

It fails closed and says so. Implementing it needs its own spec covering the
panel origin detection, the tab strip, the slot grid, and a way to confirm what
is under the cursor **before** pressing anything.

Until then this flow requires the Agent Shop to be open already.
