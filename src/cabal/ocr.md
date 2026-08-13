# ocr - reading text off the screen

Tesseract is invoked as a subprocess and its TSV output is parsed. No Python
binding: the engine is a binary, the interface is a pipe, and a wrapper library
only adds a way for the two to disagree about which binary is in use.

## Why every read takes a region

Cropping before OCR is not an optimisation, it changes the answer. Tesseract
segments a page before it recognises anything, so:

- a crop that includes a neighbouring column merges two columns into one line
- a crop that clips a word's last letter turns `Refresh` into `R` + `efresh`

The region is part of the question.

## Why upscale is a parameter and not a constant

Game text is small and anti-aliased; Tesseract was trained on scanned documents
at roughly 300dpi. Making the glyphs bigger before showing them to it is the
single most effective thing available.

The upscale must be chosen so the **final glyph height is constant across
resolutions**. A fixed multiplier applied to a crop that is itself smaller at
1080p hands the engine smaller letters than the confidence thresholds were
tuned for, and the reads get quietly worse in a way that reads as a coordinate
fault. Callers divide by the layout scale.

## Functions

| Function | Returns |
|---|---|
| `find_tesseract()` | path to the binary, or `None` |
| `engine_report()` | one line naming the binary and version |
| `find_words(image, region, upscale, min_conf)` | `[Word]` in SCREEN coordinates |
| `text_lines(words, tolerance)` | words grouped into lines, each left to right |
| `find_phrase(words, phrase, tolerance)` | centre of the phrase, or `None` |
| `joined_text(words, tolerance)` | each line as one string |

`Word` carries `text`, `conf`, `left/top/right/bottom`, and a `centre`.

## Rules

- **Coordinates come back in screen space.** A caller that has to add the crop
  origin back on is a caller that will one day forget.
- **`tolerance` is passed, never defaulted.** It is a screen distance and must
  scale with the UI: at 0.74 the lines are ~12px apart, and a tolerance wider
  than the gap merges two lines into a run-on that matches nothing.
- **`find_phrase` narrows to the words that spell the phrase.** Measuring the
  centre of the whole line would put the answer wherever the neighbouring text
  happens to end.
- **Missing Tesseract raises `OcrUnavailable`**, and every subprocess call is
  bounded by a timeout so a wedged binary cannot hang the caller.
