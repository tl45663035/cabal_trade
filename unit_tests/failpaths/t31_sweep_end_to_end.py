from harness import Harness, check, empty_panel, make_row, section, summary

import trade


class ScrollShop(Harness):

    def __init__(self, shop, **kw):
        self.shop = list(shop)
        self.view_top = 0
        super().__init__(rows=[], panel=empty_panel(), **kw)

    def _visible(self):
        window = self.shop[self.view_top:self.view_top + trade.EXPECTED_ROWS]
        return [make_row(n, it["name"], action=it["action"],
                         price=it["price"], qty=it["qty"])
                for n, it in enumerate(window, start=1)]

    def _read_rows(self, source=None, words=None, **_):
        self.n_rows += 1
        self.rows = self._visible()
        return list(self.rows)

    def _scroll(self, x, y, notches, **kw):
        self.log("scroll_wheel", x, y, notches)
        highest = max(0, len(self.shop) - trade.EXPECTED_ROWS)
        self.view_top = max(0, min(highest, self.view_top - notches))

    def install(self):
        out = super().install()
        trade.scroll_wheel = self._scroll
        return out


def listing(i, name=None, qty=None, price=None, action="change"):
    return {"name": name or f"Item {i:02d}", "action": action,
            "qty": 10 + i if qty is None else qty,
            "price": 100_000 + i if price is None else price}


def empty(i):
    return {"name": "(empty)", "action": "register", "qty": None, "price": None}


def shop_with_gap(total=30, gap_at=6, gap_len=9):
    out = []
    for i in range(1, total + 1):
        if gap_at <= i < gap_at + gap_len:
            out.append(empty(i))
        else:
            out.append(listing(i))
    return out


def sweep(shop):
    h = ScrollShop(shop, verbose=False)
    with h:
        found = trade.enumerate_listings(timeout=8.0, verbose=False)
    return h, found


section("a shop with a gap is enumerated whole")

for gap_len in (0, 1, 4, 9, 15, 19):
    shop = shop_with_gap(30, gap_at=6, gap_len=gap_len)
    h, found = sweep(shop)
    label = f"gap of {gap_len:2d}"

    check(f"{label}: the sweep succeeds", found is not None,
          "returned None -- the shop could not be enumerated at all")
    if found is None:
        continue
    check(f"{label}: all 30 slots came back", len(found) == 30,
          f"got {len(found)} -- anything short is the silent truncation that "
          f"hid eight listings on the live shop")

    wrong = [(i, r.name, shop[i - 1]["name"])
             for i, r in found if r.name != shop[i - 1]["name"]]
    check(f"{label}: every listing is at its true position", wrong == [],
          f"misplaced: {wrong[:4]}")

    live_expected = sum(1 for s in shop if s["name"] != "(empty)")
    live_found = sum(1 for _, r in found if r.action in ("change", "receive"))
    check(f"{label}: every live listing is present",
          live_found == live_expected,
          f"found {live_found} of {live_expected}")


section("the listings BELOW a gap are the ones that went missing")

shop = shop_with_gap(30, gap_at=6, gap_len=9)
shop[24] = listing(25, "Upgrade Core(Highest)", qty=0, price=98_000,
                   action="receive")
shop[29] = listing(30, "Shape Cartridge (Lv. 4) X 27", qty=0, price=27_000_000,
                   action="receive")
h, found = sweep(shop)
check("the sweep succeeds", found is not None, "")
if found:
    by_index = dict(found)
    check("row 25 below the gap is found",
          by_index.get(25) is not None
          and by_index[25].name == "Upgrade Core(Highest)",
          f"{by_index.get(25)}")
    check("row 30, the last slot, is found",
          by_index.get(30) is not None
          and by_index[30].name.startswith("Shape Cartridge"),
          f"{by_index.get(30)} -- the last row is the easiest one to lose")
    sold = [i for i, r in found if r.action == "receive"]
    check("both sold-but-uncollected rows are seen", sold == [25, 30],
          f"{sold} -- these carry proceeds; missing them leaves money on the "
          f"shop indefinitely")


section("a gap at the very bottom")

shop = [listing(i) for i in range(1, 16)] + [empty(i) for i in range(16, 31)]
h, found = sweep(shop)
check("a shop ending in 15 empty slots still enumerates", found is not None, "")
if found:
    live = sum(1 for _, r in found if r.action in ("change", "receive"))
    check("...with all 15 listings present", live == 15, f"got {live}")
    check("...at their true positions",
          all(r.name == shop[i - 1]["name"] for i, r in found[:15]),
          "a listing at the wrong index gets the wrong one cancelled")
    check("...and the slot count is sane", 15 <= len(found) <= 40,
          f"got {len(found)} -- a runaway count means the sweep never "
          f"recognised the bottom")


section("a one-screen shop needs no sweeping")

shop = [listing(i) for i in range(1, 8)] + [empty(i) for i in range(8, 11)]
h, found = sweep(shop)
check("a shop that fits on one screen enumerates", found is not None, "")
if found:
    check("...and reports exactly what is there", len(found) == 10,
          f"got {len(found)}")
    check("...without scrolling the whole shop repeatedly",
          h.names().count("scroll_wheel") <= 6,
          f"{h.names().count('scroll_wheel')} wheel events for a 10-row shop")


raise SystemExit(summary())
