# geometry - where things are, in reference coordinates

Every number here is an observation of Cabal Online, measured at 2560x1440
with the Trade window at (10, 30). They are relative to the **window's**
top-left, not the screen's, so `layout.Layout` can convert them to any
resolution.

Only what `get_price_diff` needs is present: the two tabs, the sort control,
the ten favourite slots and the offer table. The Register tab's listings table,
the registration panel, the dialogs and the NPC are absent because this flow
never touches them.

## Contents

| Group | Constants |
|---|---|
| Window | `TRADE_SIZE`, `TRADE_REGION`, `TRADE_WINDOW_MARKERS` |
| Calibration | `REF_ANCHORS`, `MIN_ANCHORS`, `MIN_ANCHOR_SPREAD` |
| Tabs | `PURCHASE_TAB`, `REGISTER_TAB`, and the markers for each |
| Sort | `SORT_REGION`, `SORT_BUTTON`, `SORT_OPTIONS` |
| Favourites | `FAVOURITE_FIRST`, `FAVOURITE_PITCH`, `FAVOURITE_COUNT`, `FAVOURITE_SLOTS` |
| Offer table | `ROW_TOP`, `ROW_PITCH`, `ROW_BAND_X`, `ROW_HALF`, `NAME_MAX_X`, `PRICE_X` |

## Rules

- **Read the units.** A value used as a POSITION goes through
  `Layout.x/y/point/box`. A value used as a DISTANCE goes through
  `Layout.length`. `ROW_HALF`, `ROW_PITCH` and `FAVOURITE_PITCH` are distances.
- **`ROW_HALF` is why a row band must scale.** At 0.76 a row pitch is 58px, so
  a raw plus/minus 24 band would be 84% of a pitch and would straddle the rows
  either side, interleaving two rows' digits into one nonsense number.
- **Anchors are spread on purpose.** A fit from two anchors a few pixels apart
  is numerically meaningless however well it fits, so `MIN_ANCHOR_SPREAD`
  requires a real vertical span. `Trade` at the top and `Refresh` near the
  bottom are the ends of it.
- **Two markers per tab, not one.** A single word can be supplied by the 3D
  world behind the panel: an item name, a chat line, a player title.
- **`FAVOURITE_SLOTS` is account state**, not geometry. It is here because it
  is the only way to confirm the results on screen belong to the slot just
  pressed rather than to the previous search.
