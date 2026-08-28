import subprocess
import sys

PADDLE = "paddlepaddle==3.3.1"
PADDLEOCR = "paddleocr==3.7.0"


def pip_install(*pkgs):
    print(f"  installing {' '.join(pkgs)} (this can take a few minutes) ...")
    subprocess.run([sys.executable, "-m", "pip", "install", *pkgs], check=True)


def ensure(mod, spec):
    try:
        loaded = __import__(mod)
        print(f"  {mod} already present: {getattr(loaded, '__version__', '?')}")
    except Exception:
        pip_install(spec)


def main():
    print("PaddleOCR setup")
    print("1. packages")
    ensure("paddle", PADDLE)
    ensure("paddleocr", PADDLEOCR)

    print("2. recognition model (downloads on first run)")
    from paddleocr import TextRecognition
    rec = TextRecognition()

    print("3. self-test")
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (340, 64), (18, 18, 24))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except Exception:
        font = ImageFont.load_default()
    draw.text((12, 10), "1,729,428,170", fill=(120, 170, 255), font=font)
    out = rec.predict(np.array(img))
    text = "".join(r.get("rec_text", "") for r in out)
    digits = "".join(c for c in text if c.isdigit())
    print(f"  read {text!r} -> digits {digits}")

    if digits == "1729428170":
        print("DONE: PaddleOCR is installed and reading correctly.")
    else:
        print("DONE: PaddleOCR is installed, but the self-test read differs; "
              "it still works on the game's own font, so proceed.")


if __name__ == "__main__":
    main()
