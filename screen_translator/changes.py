"""Cheap "is the frame essentially frozen?" check for live mode.

A "signature" is a tiny grayscale thumbnail of the captured region. This is a
coarse optimization ONLY: it skips OCR when the region is basically static
(mss captures losslessly, so an unchanged screen yields a diff of exactly 0).

It deliberately does NOT try to decide whether the *text* changed — averaging
can't tell a one-word edit from noise. The real "did the translation change"
decision is made downstream by comparing the OCR'd text, so the threshold here
is kept low to avoid ever skipping a genuine change.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

_SIG_SIZE = 24  # 24x24 grayscale is enough to notice a text change, ~cheap


def signature(image: Image.Image) -> np.ndarray:
    thumb = image.convert("L").resize((_SIG_SIZE, _SIG_SIZE))
    return np.asarray(thumb, dtype=np.int16)


def changed(a: "np.ndarray | None", b: "np.ndarray | None", threshold: float = 0.5) -> bool:
    """True unless the frames are essentially identical (or either is missing).
    The threshold is mean per-pixel difference on a 0-255 scale; kept low so only
    a near-frozen frame is treated as 'unchanged' (skips OCR), never a real edit."""
    if a is None or b is None or a.shape != b.shape:
        return True
    return float(np.abs(a - b).mean()) > threshold
