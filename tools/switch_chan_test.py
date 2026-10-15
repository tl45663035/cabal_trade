import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src_1080p"
TEST = json.loads((SRC / "config.json").read_text(encoding="utf-8"))[
    "tools"]["switch_chan_test"]
os.environ["CABAL_CONFIG"] = TEST["config"]

import world_boss as wb
import calibration
import row_model

CHANNELS = [int(channel) for channel in TEST["channels"]]
ROUNDS = int(TEST["rounds"])
WAIT = float(TEST["wait"])


def run():
    row_model.start_backup_reader()
    tries = []
    for round_number in range(1, ROUNDS + 1):
        for channel in CHANNELS:
            if tries:
                wb.pause(WAIT)
            print(f"\nround {round_number} of {ROUNDS}: Channel {channel}")
            try:
                wb.quick_relog(channel)
                failure = None
            except RuntimeError as exc:
                failure = str(exc)
                print(f"  FAILED: {failure}")
            tries.append((round_number, channel, failure))
    return tries


def report(tries):
    failed = [one for one in tries if one[2]]
    print("")
    print(f"switch test: {len(tries) - len(failed)} of {len(tries)} "
          f"succeeded, {len(failed)} failed")
    for channel in CHANNELS:
        mine = [one for one in tries if one[1] == channel]
        good = [one for one in mine if not one[2]]
        print(f"  Channel {channel}: {len(good)} of {len(mine)}")
    for round_number, channel, failure in failed:
        print(f"  round {round_number}, Channel {channel}: {failure}")


if __name__ == "__main__":
    calibration.log_to_file("switch_chan_test")
    calibration.frames_on()
    wb.watch_for_stop()
    try:
        report(run())
    finally:
        calibration.frames_written()
