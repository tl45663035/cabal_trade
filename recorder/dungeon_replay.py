import threading
from pathlib import Path

import recorder_settings
import record_action as ra
import record_all

JOIN_SLICE = 0.05


def load(path):
    path = Path(path)
    head, events, refused = record_all.prepare(path)
    if refused:
        raise RuntimeError(f"{path.name} is refused: {refused}")
    return head, events


def play(path, on_ctrl=None, halt=lambda: False, stop=None):
    path = Path(path)
    ra.user32.SetProcessDPIAware()
    head, events = load(path)
    player = record_all.Replay(events, head.get("cursor"), head, path.parent,
                               on_ctrl=on_ctrl)
    if stop is not None:
        player.stop = stop
    worker = threading.Thread(target=player.play, daemon=True)
    interrupted = None
    try:
        worker.start()
        while worker.is_alive():
            try:
                worker.join(JOIN_SLICE)
                if halt():
                    player.stop.set()
            except KeyboardInterrupt as exc:
                interrupted = exc
                player.stop.set()
    finally:
        if worker.is_alive():
            player.stop.set()
            worker.join()
    if interrupted is not None:
        raise interrupted
    if player.refused:
        raise RuntimeError(player.refused)
    return player, len(events), events[-1]["t"]


def holds_ctrl(calibration):
    def drop():
        calibration._OWN_CTRL -= 1

    def held(down):
        if down:
            calibration._OWN_CTRL += 1
        else:
            threading.Timer(record_all.OWN_GRACE, drop).start()
    return held


def run(dungeon, dungeon_name, file, verbose=True):
    calibration = dungeon.calibration
    path = dungeon.SEQUENCES / dungeon_name / file
    if not path.exists():
        raise RuntimeError(f"no recording {path}; nothing replayed.")
    if verbose:
        print(f"  {dungeon_name}/{file}: the whole run replays with the "
              f"recorder's replayer and nothing else is sent until it ends; "
              f"only a Ctrl stop cuts it short. The exit and the claims come "
              f"after it")
    with dungeon.KEY_LOCK:
        dungeon.PLAYING.set()
    try:
        player, total, length = play(
            path, on_ctrl=holds_ctrl(calibration),
            halt=calibration.STOPPED.is_set, stop=calibration.STOPPED)
    finally:
        dungeon.PLAYING.clear()
    calibration.snap(f"replayed_{dungeon_name}")
    if verbose:
        print(f"  replayed {player.played} of {total} events "
              f"({length:.1f}s); nothing is left held down")
        print(f"  {player.timing()}")
    if not dungeon.OVER.is_set() and verbose:
        print("  the replay is over before the end screen; waiting for it "
              "or the time limit")
    while not dungeon.OVER.is_set():
        calibration.halt_if_stopped()
        dungeon.OVER.wait(dungeon.WAIT_SLICE)
