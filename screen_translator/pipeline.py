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
