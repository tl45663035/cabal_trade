"""Reading text off the screen.

Spec: ocr.md

Tesseract is invoked as a subprocess and its TSV output is parsed. There is no
Python binding involved: the engine is a binary, the interface is a pipe, and
adding a wrapper library only adds a way for the two to disagree about which
binary is being used.

Every function here takes a REGION. Cropping before OCR is not an optimisation
-- it changes the answer. Tesseract segments a page before it recognises
anything, so a crop that includes a neighbouring column can merge two columns
into one line, and a crop that clips a word's last letter turns 'Refresh' into
'R' + 'efresh'. The region is part of the question being asked.
"""

from __future__ import annotations

import csv
import shutil
import subprocess
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

# Where Tesseract is looked for, in order. PATH first so an operator can point
# at a specific build without editing this file.
TESSERACT_CANDIDATES = (
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
)

# A wedged tesseract.exe would otherwise hang the caller for ever. Every read
# is bounded.
OCR_TIMEOUT = 30.0

# Page segmentation mode 11: "sparse text -- find as much text as possible in
# no particular order". The game's panels are not documents; they are scattered
# labels, and the document-oriented modes try to find columns and paragraphs
# that are not there.
PSM = "11"


@dataclass(frozen=True)
class Word:
    """One recognised word and where it sits, in SCREEN pixels."""
    text: str
    conf: float
    left: int
    top: int
    right: int
    bottom: int

    @property
    def centre(self) -> "tuple[int, int]":
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


class OcrUnavailable(RuntimeError):
    """Tesseract could not be found or would not run."""


_binary: "str | None" = None
_looked = False


def find_tesseract() -> "str | None":
    """The tesseract binary, or None. Cached after the first look."""
    global _binary, _looked
    if _looked:
        return _binary
    _looked = True
    found = shutil.which("tesseract")
    if found:
        _binary = found
        return _binary
    for candidate in TESSERACT_CANDIDATES:
        if candidate.exists():
            _binary = str(candidate)
            return _binary
    _binary = None
    return None


def engine_report() -> str:
    """A one-line description of the OCR engine, for diagnostics."""
    binary = find_tesseract()
    if not binary:
        return "tesseract: NOT FOUND"
    try:
        out = subprocess.run([binary, "--version"], capture_output=True,
                             text=True, timeout=OCR_TIMEOUT)
        first = (out.stdout or out.stderr or "").splitlines()[0]
        return f"tesseract: {binary} ({first.strip()})"
    except Exception as exc:  # noqa: BLE001 - diagnostic
        return f"tesseract: {binary} (would not run: {exc})"


def _run_tesseract(image: Image.Image) -> str:
    binary = find_tesseract()
    if not binary:
        raise OcrUnavailable(
            "Tesseract is not installed or not on PATH. Nothing that reads "
            "the screen can work without it.")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    try:
        out = subprocess.run(
            [binary, "stdin", "stdout", "--psm", PSM, "tsv"],
            input=buffer.getvalue(), capture_output=True, timeout=OCR_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise OcrUnavailable(f"tesseract did not return in {OCR_TIMEOUT}s") from exc
    return out.stdout.decode("utf-8", errors="replace")


def find_words(image: Image.Image,
               region: "tuple[int, int, int, int]",
               upscale: float = 1.0,
               min_conf: float = 0.0) -> "list[Word]":
    """Every word inside `region`, with SCREEN coordinates.

    `upscale` multiplies the crop before recognition. Game text is small and
    anti-aliased, and Tesseract was trained on scanned documents at roughly
    300dpi -- so the single most effective thing that can be done to a game
    screenshot is to make the glyphs bigger before showing them to it.

    The upscale must be chosen so the FINAL glyph height is constant across
    resolutions. A fixed multiplier applied to a crop that is itself smaller at
    1080p hands the engine smaller letters than the thresholds were tuned for,
    and the reads get quietly worse in a way that reads as a coordinate fault.

    Coordinates come back in screen space, not crop space. A caller that has to
    add the crop origin back on is a caller that will one day forget.
    """
    left, top, right, bottom = (int(v) for v in region)
    left, top = max(0, left), max(0, top)
    right = min(image.width, right)
    bottom = min(image.height, bottom)
    if right <= left or bottom <= top:
        return []
    crop = image.crop((left, top, right, bottom))
    scale = max(1.0, float(upscale))
    if scale != 1.0:
        crop = crop.resize((max(1, int(crop.width * scale)),
                            max(1, int(crop.height * scale))),
                           Image.LANCZOS)
    tsv = _run_tesseract(crop)

    words: list[Word] = []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t",
                            quoting=csv.QUOTE_NONE)
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row.get("conf") or -1)
            wl = int(row["left"]); wt = int(row["top"])
            ww = int(row["width"]); wh = int(row["height"])
        except (TypeError, ValueError, KeyError):
            continue
        if conf < min_conf:
            continue
        words.append(Word(
            text=text, conf=conf,
            left=left + int(wl / scale), top=top + int(wt / scale),
            right=left + int((wl + ww) / scale),
            bottom=top + int((wt + wh) / scale)))
    return words


class Frame:
    """One screenshot, plus every OCR read taken from it, memoised.

    THE POINT IS THE MEMO, NOT THE IMAGE. A grab costs a few milliseconds; a
    read costs 70ms of process launch before it has looked at a single pixel.
    Two predicates that each take their own screenshot cannot share a read
    even when they ask the identical question of an identical screen.

    Keyed on (region, upscale, min_conf) because those three are the whole
    question. Identity of the image is not part of the key -- a Frame IS one
    image, and a new observation means a new Frame. That is the property that
    makes reuse safe: nothing can accidentally answer about a stale screen,
    because a stale screen is a different object.
    """

    __slots__ = ("image", "_reads")

    def __init__(self, image: Image.Image):
        self.image = image
        self._reads: dict = {}

    def words(self, region: "tuple[int, int, int, int]",
              upscale: float = 1.0, min_conf: float = 0.0) -> "list[Word]":
        key = (tuple(int(v) for v in region), round(float(upscale), 3),
               float(min_conf))
        cached = self._reads.get(key)
        if cached is None:
            cached = find_words(self.image, region, upscale=upscale,
                                min_conf=min_conf)
            self._reads[key] = cached
        return cached

    @property
    def reads(self) -> int:
        """How many OCR launches this frame has paid for. For diagnostics."""
        return len(self._reads)


def text_lines(words: "list[Word]", tolerance: int) -> "list[list[Word]]":
    """Group words into lines by vertical proximity, each ordered left to right.

    `tolerance` is a SCREEN distance and must be passed in, not defaulted. It
    has to scale with the UI: at 0.74 the lines are ~12px apart, and a
    tolerance wider than the gap merges two lines into one run-on string that
    matches nothing.
    """
    lines: "list[list[Word]]" = []
    for word in sorted(words, key=lambda w: w.centre[1]):
        for line in lines:
            if abs(line[0].centre[1] - word.centre[1]) <= tolerance:
                line.append(word)
                break
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.centre[0]) for line in lines]


def find_phrase(words: "list[Word]", phrase: str,
                tolerance: int) -> "tuple[int, int] | None":
    """The centre of `phrase` where it appears as consecutive words on a line.

    Matched across a joined line and then narrowed back to the words that
    actually spell it. Measuring the centre of the whole LINE would put the
    answer wherever the neighbouring text happens to end, which for an anchor
    means calibrating against the length of an item name.
    """
    want = phrase.casefold().split()
    if not want:
        return None
    for line in text_lines(words, tolerance):
        texts = [w.text.casefold().strip(":.,") for w in line]
        for i in range(len(texts) - len(want) + 1):
            if texts[i:i + len(want)] == want:
                span = line[i:i + len(want)]
                left = min(w.left for w in span)
                right = max(w.right for w in span)
                top = min(w.top for w in span)
                bottom = max(w.bottom for w in span)
                return ((left + right) // 2, (top + bottom) // 2)
    return None


def joined_text(words: "list[Word]", tolerance: int) -> "list[str]":
    """Each line's words joined with single spaces, top to bottom."""
    return [" ".join(w.text for w in line)
            for line in text_lines(words, tolerance)]
