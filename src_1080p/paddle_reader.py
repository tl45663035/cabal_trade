import base64
import io
import json
import sys

answer = sys.stdout
sys.stdout = sys.stderr

import numpy as np
from paddleocr import TextRecognition
from PIL import Image


def main():
    model = sys.argv[1]
    reader = TextRecognition(model_name=model)
    answer.write(json.dumps({"ready": model}) + "\n")
    answer.flush()
    for line in sys.stdin:
        crops = [np.array(Image.open(io.BytesIO(base64.b64decode(c))).convert("RGB"))[:, :, ::-1]
                 for c in json.loads(line)]
        found = reader.predict(crops) if crops else []
        answer.write(json.dumps([[r["rec_text"], float(r["rec_score"])] for r in found]) + "\n")
        answer.flush()


if __name__ == "__main__":
    main()
