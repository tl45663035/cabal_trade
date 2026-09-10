import sys
import time

import calibration
import open_agent_shop_premium as shop
import open_inventory as inv


LAST = {}


def read_balance(image=None):
    image = image if image is not None else calibration.grab()
    alz = calibration.read_balance_from(image)
    if alz is not None:
        LAST["alz"] = int(alz)
        LAST["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return alz


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
