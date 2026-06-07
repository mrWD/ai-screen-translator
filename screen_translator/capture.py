"""Screen capture.

On macOS we use Quartz CGDisplayCreateImageForRect, which returns NATIVE Retina
pixels (e.g. 3600x2338 for an 1800x1169 logical screen) — far better for OCR than
mss, which only captures at 1x on macOS. Elsewhere (and as a fallback) we use mss.

Coordinates passed in are LOGICAL (Qt) points. Callers that map OCR coordinates
back to the screen must derive the real scale from the returned image size vs. the
logical region, NOT assume a fixed devicePixelRatio.

Known limits:
- macOS needs Screen Recording permission (System Settings > Privacy & Security).
- Multi-display: the Quartz path selects the display whose bounds contain the
  region (by global coords) and captures from it. A region straddling two
  displays only yields the chosen display's portion.
- DRM video and true exclusive-fullscreen games come back black (by design).
  GeForce Now and borderless-windowed games capture fine.
"""

from __future__ import annotations

import sys

import mss
from PIL import Image

from .config import Region


class CaptureError(RuntimeError):
    pass


def grab(region: Region) -> Image.Image:
    if sys.platform == "darwin":
        try:
            return _grab_quartz(region)
        except Exception:
            pass  # fall back to mss if Quartz fails for any reason
    return _grab_mss(region)


def _grab_mss(region: Region) -> Image.Image:
    monitor = {
        "left": int(round(region.x)),
        "top": int(round(region.y)),
        "width": max(1, int(round(region.w))),
        "height": max(1, int(round(region.h))),
    }
    try:
        with mss.mss() as sct:
            shot = sct.grab(monitor)
    except Exception as exc:  # pragma: no cover - depends on platform/permission
        raise CaptureError(str(exc)) from exc
    return Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)


def _display_id_for_region(region: Region):
    """The CGDirectDisplayID whose bounds contain the region's center, so a
    capture on a secondary display works (CGMainDisplayID would be wrong there).

    The region is in GLOBAL coordinates (same space as CGDisplayBounds and Qt's
    geometry). CGDisplayCreateImageForRect, however, takes its rect in the target
    display's LOCAL space (origin at that display's top-left), so _grab_quartz
    subtracts the display's bounds origin before capturing."""
    import Quartz

    # Center is more robust than the corner, which can sit on a shared edge.
    cx = region.x + region.w / 2.0
    cy = region.y + region.h / 2.0
    try:
        err, ids, cnt = Quartz.CGGetDisplaysWithRect(Quartz.CGRectMake(cx, cy, 1, 1), 16, None, None)
        if err == 0 and cnt:
            return ids[0]
    except Exception:
        pass
    try:
        err, ids, cnt = Quartz.CGGetActiveDisplayList(16, None, None)
        if err == 0:
            for did in ids[:cnt]:
                b = Quartz.CGDisplayBounds(did)
                if (b.origin.x <= cx < b.origin.x + b.size.width
                        and b.origin.y <= cy < b.origin.y + b.size.height):
                    return did
    except Exception:
        pass
    return Quartz.CGMainDisplayID()


def _grab_quartz(region: Region) -> Image.Image:
    import Quartz

    did = _display_id_for_region(region)
    bounds = Quartz.CGDisplayBounds(did)
    # The rect is in the display's LOCAL space, so subtract its global origin. On
    # the main display the origin is (0,0), so this is a no-op there; on a secondary
    # display it maps the global region onto that display's own framebuffer.
    rect = Quartz.CGRectMake(
        float(region.x) - bounds.origin.x,
        float(region.y) - bounds.origin.y,
        float(max(1, region.w)),
        float(max(1, region.h)),
    )
    cgimg = Quartz.CGDisplayCreateImageForRect(did, rect)
    if cgimg is None:
        raise CaptureError("CGDisplayCreateImageForRect returned None (permission?)")
    return _cgimage_to_pil(cgimg)


def _cgimage_to_pil(cgimg) -> Image.Image:
    import Quartz

    width = Quartz.CGImageGetWidth(cgimg)
    height = Quartz.CGImageGetHeight(cgimg)
    bytes_per_row = Quartz.CGImageGetBytesPerRow(cgimg)  # may include row padding
    provider = Quartz.CGImageGetDataProvider(cgimg)
    raw = Quartz.CGDataProviderCopyData(provider)
    if raw is None:
        raise CaptureError("CGDataProviderCopyData returned None")
    data = bytes(raw)
    expected = height * bytes_per_row
    if len(data) < expected:  # guard against a truncated buffer + stride mismatch
        raise CaptureError(f"CGImage data truncated: {len(data)} < {expected}")
    # CGDisplayCreateImage is BGRA (little-endian ARGB). Respect the stride.
    image = Image.frombuffer("RGBA", (width, height), data, "raw", "BGRA", bytes_per_row, 1)
    return image.convert("RGB")


def is_black(image: Image.Image, threshold: int = 8) -> bool:
    """True if the frame is (near-)black — DRM content or missing permission.
    Sampled on a thumbnail so a 2x Retina frame doesn't cost a full-res scan."""
    thumb = image
    longest = max(image.width, image.height)
    if longest > 256:
        ratio = 256 / longest
        thumb = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))))
    _, brightest = thumb.convert("L").getextrema()
    return brightest < threshold
