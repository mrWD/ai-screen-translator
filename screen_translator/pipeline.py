"""Pure, framework-free pipeline logic shared by the worker jobs.

Everything here is plain Python (no Qt, no widgets) so it can be unit-tested
headlessly. These are exactly the spots that have bitten us before — the
image→screen scale (assuming dpr=2 once put translations at half height), the
macOS menu-bar strip filter, and the live-mode dedup decision — so they live in
one tested place instead of being inlined in the QRunnables.
"""

from __future__ import annotations

# Junk-block thresholds (logical px): smaller blocks are icons/noise, not text.
_MIN_W = 6
_MIN_H = 8
_MENU_BAR_H = 24  # macOS primary-display menu bar; skip text that lands in it


def compute_scale(img_w: int, img_h: int, geom_w: int, geom_h: int) -> "tuple[float, float]":
    """Image-pixels-per-logical-point, derived from the ACTUAL captured image size
    vs. the logical region — never an assumed devicePixelRatio (mss may capture at
    1x, Quartz at ~2x). Falls back to 1.0 on a zero-sized region."""
    scale_x = img_w / geom_w if geom_w else 1.0
    scale_y = img_h / geom_h if geom_h else 1.0
    return scale_x, scale_y


def map_block(
    bx: float, by: float, bw: float, bh: float,
    geom_x: int, geom_y: int, scale_x: float, scale_y: float,
) -> "tuple[int, int, int, int]":
    """Map an OCR block (image-pixel coords, top-left origin) to a logical-screen
    (x, y, w, h) rect tuple, clamping w/h to at least 1."""
    return (
        int(geom_x + bx / scale_x),
        int(geom_y + by / scale_y),
        max(1, int(bw / scale_x)),
        max(1, int(bh / scale_y)),
    )


def is_junk_block(x: int, y: int, w: int, h: int, geom_y: int, is_macos: bool) -> bool:
    """True for blocks we should drop BEFORE translating (saves network calls):
    sub-text-sized noise, and the macOS menu-bar strip on the primary display."""
    if h < _MIN_H or w < _MIN_W:
        return True
    if is_macos and geom_y == 0 and y < _MENU_BAR_H:
        return True
    return False


def dedup_outcome(text: str, last_text: "str | None") -> str:
    """Decide what a finished region OCR means, given the previous OCR text.

    `text` must already be stripped. `last_text` is None for a single-shot press
    (always translate) or the previous OCR text in live mode (dedup). Returns:
      - "no_text":   nothing recognised, single-shot  -> show "(no text found)"
      - "vanished":  nothing recognised, live mode     -> keep the existing panel
      - "unchanged": same text as last time, live mode -> don't re-translate
      - "translate": real new text                     -> translate it
    """
    if not text:
        return "no_text" if last_text is None else "vanished"
    if last_text is not None and text == last_text:
        return "unchanged"
    return "translate"


def sample_block_colors(image, bx, by, bw, bh):
    """For in-place mode: sample a background fill colour from the ring just
    outside the OCR box (median, robust to the text glyphs) and pick a contrasting
    text colour by luminance. Runs on the worker thread, so it returns plain int
    RGB tuples — QColor is constructed later on the UI thread."""
    import numpy as np

    arr = np.asarray(image.convert("RGB"))
    h, w = arr.shape[:2]
    bx0, by0, bx1, by1 = int(bx), int(by), int(bx + bw), int(by + bh)
    pad = max(2, int(min(bw, bh) * 0.3))
    ox0, oy0 = max(0, bx0 - pad), max(0, by0 - pad)
    ox1, oy1 = min(w, bx1 + pad), min(h, by1 + pad)
    outer = arr[oy0:oy1, ox0:ox1]
    if outer.size == 0:
        outer = arr
    # Mask out the inner text box so glyph pixels don't bias the background median.
    # Clamp to the outer slice so a block touching the image edge (negative mapped
    # origin, e.g. a Vision top-edge box) is still masked, not skipped.
    mask = np.ones(outer.shape[:2], dtype=bool)
    iy0, ix0 = max(0, by0 - oy0), max(0, bx0 - ox0)
    iy1, ix1 = min(outer.shape[0], by1 - oy0), min(outer.shape[1], bx1 - ox0)
    if iy1 > iy0 and ix1 > ix0:
        mask[iy0:iy1, ix0:ix1] = False
    ring = outer[mask]
    if ring.size == 0:
        ring = outer.reshape(-1, 3)
    fill = np.median(ring.reshape(-1, 3), axis=0)
    r, g, b = int(fill[0]), int(fill[1]), int(fill[2])
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    text = (20, 20, 24) if luma >= 140 else (240, 240, 245)
    return (r, g, b), text
