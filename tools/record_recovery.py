import collections
import json
import pathlib
import subprocess
import sys
import time

import cv2
import imageio_ffmpeg
import mss
import numpy

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src_1080p"
LOGS = SRC / "logs"
EVENTS = LOGS / "supervise.log"
OUT = LOGS / "recovery_video"
K = json.loads((SRC / "config.json").read_text(encoding="utf-8"))["supervise"]
R = K["record"]


def say(text):
    print(f"{time.strftime('%H:%M:%S')} {text}", flush=True)


def finish(out):
    raw = out.with_suffix(".raw.mp4")
    out.rename(raw)
    done = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel",
                           "error", "-i", str(raw)] + R["codec"] + [str(out)],
                          capture_output=True, text=True)
    if done.returncode == 0 and out.exists():
        raw.unlink()
        return out
    say(f"kept {raw.name} as recorded: {done.stderr.strip()[:120]}")
    return raw


class Tail:
    def __init__(self, path):
        self.path = path
        self.seen = path.stat().st_size if path.exists() else 0

    def lines(self):
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.seen:
            self.seen = 0
        if size == self.seen:
            return []
        with open(self.path, encoding="utf-8", errors="replace") as handle:
            handle.seek(self.seen)
            text = handle.read()
        self.seen = size
        return [line for line in text.splitlines() if line.strip()]


def main():
    fps = R["fps"]
    ring = collections.deque(maxlen=R["pre"] * fps)
    encode = [cv2.IMWRITE_JPEG_QUALITY, R["quality"]]
    tail = Tail(EVENTS)
    writer = None
    out = None
    started = 0.0
    dead_seen = False
    finish_at = None
    say(f"watching {EVENTS.name} for {R['on']}; {fps} fps, {R['pre']}s before")
    with mss.MSS() as sct:
        where = sct.monitors[1]
        gap = 1.0 / fps
        while True:
            due = time.monotonic() + gap
            frame = numpy.asarray(sct.grab(where))[:, :, :3]
            frame = cv2.resize(frame, None, fx=R["scale"], fy=R["scale"],
                               interpolation=cv2.INTER_AREA)
            for line in tail.lines():
                reason, _, state = line.rpartition(",")
                reason, _, stamp = reason.rpartition(",")
                if writer is None:
                    if any(word in reason for word in R["on"]):
                        OUT.mkdir(parents=True, exist_ok=True)
                        out = OUT / f"{time.strftime('%Y-%m-%d_%H%M%S')}_recovery.mp4"
                        writer = cv2.VideoWriter(
                            str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps,
                            (frame.shape[1], frame.shape[0]))
                        for kept in ring:
                            writer.write(cv2.imdecode(kept, cv2.IMREAD_COLOR))
                        started = time.monotonic()
                        dead_seen = False
                        finish_at = None
                        say(f"recording {out.name} on: {line}")
                    continue
                if state == "dead":
                    dead_seen = True
                if dead_seen and any(word in reason for word in R["done"]):
                    finish_at = time.monotonic() + R["tail"]
                    say(f"finishing in {R['tail']}s on: {line}")
            if writer is None:
                ring.append(cv2.imencode(".jpg", frame, encode)[1])
            else:
                writer.write(frame)
                ran = time.monotonic() - started
                if finish_at is not None and time.monotonic() >= finish_at:
                    writer.release()
                    say(f"saved {finish(out).name}, {ran:.0f}s")
                    writer = None
                elif not dead_seen and ran > R["abandon"]:
                    writer.release()
                    out.unlink(missing_ok=True)
                    say(f"dropped {out.name}: no death within {R['abandon']}s")
                    writer = None
                elif ran > R["cap"]:
                    writer.release()
                    say(f"saved {finish(out).name} at the {R['cap']}s cap")
                    writer = None
            left = due - time.monotonic()
            if left > 0:
                time.sleep(left)


if __name__ == "__main__":
    main()
