import sys

import calibration
import open_agent_shop_premium as shop
import open_inventory as inv

ALZ_BOX = tuple(calibration.load()["inventory"]["alz_box"])


def read_balance(image=None):
    image = image if image is not None else calibration.grab()
    band = calibration._box(tuple(calibration._REG["alz_search"]))
    box = (min(band[0], ALZ_BOX[0]), min(band[1], ALZ_BOX[1]),
           max(band[2], ALZ_BOX[2]), max(band[3], ALZ_BOX[3]))
    return calibration.read_money(image, box)


def get_alz(verbose: bool = True):
    inv.focus_game()
    if not shop.panel_open(verbose=False):
        shop.ensure_inventory_open(verbose=verbose)
    return read_balance()


def main() -> None:
    value = get_alz()
    if value is None:
        print("Alz: could not read the balance.")
        sys.exit(1)
    print(f"Alz: {value:,}")


if __name__ == "__main__":
    main()
