import base64
import importlib.metadata
import io
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OCR = json.loads((HERE / "config.json").read_text(encoding="utf-8"))["ocr"]
CHECK = OCR["backup_check"]


def installed(spec):
    name, _, want = spec.partition("==")
    try:
        return importlib.metadata.version(name) == want
    except importlib.metadata.PackageNotFoundError:
        return False


def packages():
    missing = [spec for spec in OCR["backup_packages"] if not installed(spec)]
    for spec in OCR["backup_packages"]:
        print(f"   {spec}: {'installing' if spec in missing else 'already there'}")
    if missing:
        print("   this takes a few minutes ...")
        subprocess.run([sys.executable, "-m", "pip", "install", *missing],
                       check=True)


def model():
    from paddleocr import TextRecognition
    TextRecognition(model_name=OCR["backup_model"])


def sample():
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(CHECK["font"], CHECK["size"])
    left, top, right, bottom = font.getbbox(CHECK["sample"])
    pad = CHECK["margin"]
    image = Image.new("RGB", (right - left + 2 * pad, bottom - top + 2 * pad),
                      "white")
    ImageDraw.Draw(image).text((pad - left, pad - top), CHECK["sample"],
                               fill="black", font=font)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def read_check():
    started = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, str(HERE / OCR["backup_reader"]), OCR["backup_model"]],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    lines = queue.Queue()

    def pump():
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    try:
        ready = lines.get(timeout=OCR["backup_start_timeout"])
        if not ready or json.loads(ready).get("ready") != OCR["backup_model"]:
            raise SystemExit(f"FAILED: the reader did not start: {ready!r}")
        up = time.monotonic() - started
        asked = time.monotonic()
        proc.stdin.write(json.dumps([sample()]) + "\n")
        proc.stdin.flush()
        answer = lines.get(timeout=OCR["backup_timeout"])
        took = time.monotonic() - asked
    except queue.Empty:
        proc.kill()
        raise SystemExit("FAILED: the reader did not answer in time")
    proc.stdin.close()
    proc.wait()
    if not answer:
        raise SystemExit("FAILED: the reader stopped without answering")
    read = "".join(text for text, _score in json.loads(answer))
    return up, read, took


def digits(text):
    return "".join(c for c in text if c.isdigit())


def main():
    print(f"PaddleOCR backup reader, inside {Path(sys.executable).parents[1]}")
    print("packages:")
    packages()
    print(f"model: {OCR['backup_model']}")
    model()
    print(f"read check through {OCR['backup_reader']}, "
          f"started the way the bot starts it:")
    up, read, took = read_check()
    print(f"   ready in {up:.1f} s; read {read!r} in {took:.1f} s")
    if digits(read) != digits(CHECK["sample"]):
        raise SystemExit(f"FAILED: it read {read!r}, not {CHECK['sample']!r}")
    print("DONE: the backup reader is ready.")


if __name__ == "__main__":
    main()
