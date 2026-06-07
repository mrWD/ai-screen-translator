"""Headless verification of the core pipeline (no GUI, no screen permission).

Renders an image with text, runs OCR on it, then translates the result.
Run: python tools/smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from screen_translator.ocr import make_ocr  # noqa: E402
from screen_translator.translate import TranslateError, translate  # noqa: E402

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _make_image(text: str) -> Image.Image:
    img = Image.new("RGB", (640, 160), "white")
    ImageDraw.Draw(img).text((20, 50), text, fill="black", font=_font(48))
    return img


def main() -> int:
    source_text = "Hello, world"
    image = _make_image(source_text)

    print("1) OCR")
    ocr = make_ocr("auto", "en")
    print(f"   engine = {ocr.name}")
    recognized = ocr.recognize(image, "en")
    print(f"   recognized = {recognized!r}")
    ocr_ok = "hello" in recognized.lower()
    print(f"   OCR {'OK' if ocr_ok else 'MISMATCH (check engine/install)'}")

    print("2) Translate (free Google, en -> ru)")
    try:
        out = translate(recognized or source_text, "auto", "ru")
        print(f"   translated = {out!r}")
        print("   Translate OK" if out else "   Translate returned empty")
    except TranslateError as exc:
        print(f"   Translate FAILED (network?): {exc}")

    return 0 if ocr_ok else 1


if __name__ == "__main__":
    sys.exit(main())
