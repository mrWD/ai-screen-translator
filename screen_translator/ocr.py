"""Pluggable OCR.

- Apple Vision (macOS, via `ocrmac`): fast, on-device, no model download.
- RapidOCR (cross-platform ONNX PaddleOCR): used off-macOS, or when the source
  language is Cyrillic (Vision can't read ru/uk).

`make_ocr(engine, source)` returns a ready backend, falling back gracefully.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass

from PIL import Image

from .languages import get


@dataclass
class Block:
    """One recognized text block, in image PIXEL coordinates, top-left origin."""

    text: str
    x: float
    y: float
    w: float
    h: float


class OCRBackend:
    name = "base"

    def recognize(self, image: Image.Image, source: str) -> str:
        raise NotImplementedError

    def recognize_blocks(self, image: Image.Image, source: str) -> "list[Block]":
        """Per-block OCR with bounding boxes (for the full-screen overlay).
        Default: one block spanning the whole image."""
        text = self.recognize(image, source).strip()
        if not text:
            return []
        w, h = image.size
        return [Block(text, 0.0, 0.0, float(w), float(h))]


class VisionOCR(OCRBackend):
    name = "vision"

    def __init__(self, fast: bool = True) -> None:
        from ocrmac import ocrmac  # imported lazily so non-macOS never needs it

        self._ocrmac = ocrmac
        # "fast" ≈ half the time of "accurate" on a full screen, with no loss on
        # clean UI text; "accurate" helps small/stylised text. Configurable.
        self._level = "fast" if fast else "accurate"

    def _kwargs(self, source: str) -> dict:
        kwargs = {"recognition_level": self._level}
        vision_code = get(source).vision_code
        if vision_code:
            kwargs["language_preference"] = [vision_code]
        return kwargs

    def recognize(self, image: Image.Image, source: str) -> str:
        annotations = self._ocrmac.OCR(image, **self._kwargs(source)).recognize()
        # annotations: list of (text, confidence, bbox)
        lines = [text for (text, _conf, _bbox) in annotations if text and text.strip()]
        return "\n".join(lines)

    def recognize_blocks(self, image: Image.Image, source: str) -> "list[Block]":
        annotations = self._ocrmac.OCR(image, **self._kwargs(source)).recognize()
        width, height = image.size
        blocks = []
        for text, _conf, bbox in annotations:
            if not text or not text.strip():
                continue
            # Vision bbox: (x, y, w, h) normalized 0-1, origin BOTTOM-left -> flip Y.
            x, y, w, h = bbox
            blocks.append(
                Block(text.strip(), x * width, (1.0 - y - h) * height, w * width, h * height)
            )
        return blocks


class RapidOCRBackend(OCRBackend):
    name = "rapidocr"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._engine = RapidOCR()
        self._lock = threading.Lock()  # one shared engine; serialize inference

    def recognize(self, image: Image.Image, source: str) -> str:
        import numpy as np

        with self._lock:
            result, _elapsed = self._engine(np.array(image))
        if not result:
            return ""
        # result: list of [box, text, confidence]
        return "\n".join(item[1] for item in result)

    def recognize_blocks(self, image: Image.Image, source: str) -> "list[Block]":
        import numpy as np

        with self._lock:
            result, _elapsed = self._engine(np.array(image))
        if not result:
            return []
        blocks = []
        for box, text, _conf in result:
            if not text or not text.strip():
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            blocks.append(Block(text.strip(), x0, y0, x1 - x0, y1 - y0))
        return blocks


def _build(name: str, fast: bool = True) -> OCRBackend:
    if name == "vision":
        if sys.platform != "darwin":
            raise RuntimeError("Vision OCR is macOS-only")
        return VisionOCR(fast=fast)
    if name == "rapidocr":
        return RapidOCRBackend()
    raise RuntimeError(f"Unknown OCR engine: {name}")


def make_ocr(engine: str, source: str, fast: bool = True) -> OCRBackend:
    """Pick a backend. 'auto' = Vision on macOS (unless the source is Cyrillic,
    which Vision can't read), otherwise RapidOCR."""
    cyrillic_source = source != "auto" and get(source).vision_code is None

    if engine == "auto":
        preferred = "vision" if (sys.platform == "darwin" and not cyrillic_source) else "rapidocr"
    else:
        preferred = engine

    order = [preferred] + [e for e in ("vision", "rapidocr") if e != preferred]
    if cyrillic_source:
        # Vision can't read Cyrillic — never let it be the silent fallback,
        # or the user gets garbage instead of a clear "install rapidocr" error.
        order = [e for e in order if e != "vision"]
    errors = []
    for name in order:
        if name == "vision" and sys.platform != "darwin":
            continue
        try:
            return _build(name, fast=fast)
        except Exception as exc:  # pragma: no cover - depends on installed deps
            errors.append(f"{name}: {exc}")
    raise RuntimeError(
        "No OCR backend available. Tried: " + "; ".join(errors)
        + "  (install rapidocr-onnxruntime for a cross-platform engine)"
    )
