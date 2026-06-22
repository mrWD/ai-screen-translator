"""Off-the-UI-thread workers: OCR + network translation as QRunnables.

These run on the global QThreadPool and report back via Qt Signals delivered on
the UI thread. All the framework-free decision logic (scale, block mapping, junk
filter, dedup, colour sampling) lives in `pipeline`; these classes are just the
Qt/threading shell around it.

Keep a Python ref to a running job in the caller — QThreadPool only stores a C++
pointer, so a GC'd wrapper would tear down its C++ object mid-run.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QRect, QRunnable, Signal, Slot

from . import pipeline

# Full-screen translates many blocks; the free/DeepL endpoints are network-bound,
# so fan the requests out instead of paying N round-trips back to back. Capped so
# we never hammer a rate-limited endpoint, and dropped to 1 for backends that
# aren't safe to call concurrently (see TranslateBackend.parallel_safe).
_MAX_TRANSLATE_WORKERS = 8


class _JobSignals(QObject):
    done = Signal(str, object, str)  # translated text, region QRect, ocr text
    unchanged = Signal()             # live mode: OCR text same as last -> no-op
    failed = Signal(str)


class Job(QRunnable):
    """Runs OCR + translation off the UI thread (translation hits the network).

    In live mode (`last_text` is not None) it short-circuits when the OCR'd text
    matches the last result, so we never re-translate or churn the overlay on an
    unchanged line even while the game's background animates."""

    def __init__(self, ocr, image, region_rect: QRect, source, target, last_text, translator) -> None:
        super().__init__()
        self.signals = _JobSignals()
        self._ocr = ocr
        self._translator = translator
        self._image = image
        self._region_rect = region_rect
        self._source = source
        self._target = target
        self._last_text = last_text  # None in single-shot -> always translate

    @Slot()
    def run(self) -> None:
        try:
            text = self._ocr.recognize(self._image, self._source).strip()
            outcome = pipeline.dedup_outcome(text, self._last_text)
            if outcome == "no_text":
                self.signals.done.emit("(no text found)", self._region_rect, "")
                return
            if outcome in ("vanished", "unchanged"):
                self.signals.unchanged.emit()
                return
            translated = self._translator.translate(text, self._source, self._target)
            self.signals.done.emit(translated, self._region_rect, text)
        except Exception as exc:  # surfaced to the user via the tray
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")


class _ScreenJobSignals(QObject):
    # list of (screen-logical QRect, original text, translated text, fill_rgb, text_rgb);
    # the two rgb int-tuples are sampled for in-place mode, or None when it's off.
    done = Signal(object)
    failed = Signal(str)


class ScreenJob(QRunnable):
    """Full-screen mode: OCR every text block, translate each (concurrently), and
    map each block's image-pixel box to screen-logical coordinates."""

    def __init__(self, ocr, translator, image, geom_x, geom_y, geom_w, geom_h,
                 source, target, inplace=False) -> None:
        super().__init__()
        self.signals = _ScreenJobSignals()
        self._ocr = ocr
        self._translator = translator
        self._image = image
        self._gx = geom_x
        self._gy = geom_y
        self._gw = geom_w
        self._gh = geom_h
        self._source = source
        self._target = target
        self._inplace = inplace

    @Slot()
    def run(self) -> None:
        try:
            blocks = self._ocr.recognize_blocks(self._image, self._source)
            img_w, img_h = self._image.size
            scale_x, scale_y = pipeline.compute_scale(img_w, img_h, self._gw, self._gh)
            is_macos = sys.platform == "darwin"

            # Filter to real text blocks BEFORE translating, so we don't spend
            # network calls on icons/noise or the menu bar.
            candidates = []  # (QRect, Block)
            for block in blocks:
                x, y, w, h = pipeline.map_block(
                    block.x, block.y, block.w, block.h, self._gx, self._gy, scale_x, scale_y
                )
                if pipeline.is_junk_block(x, y, w, h, self._gy, is_macos):
                    continue
                candidates.append((QRect(x, y, w, h), block))

            translations = self._translate_all([b.text for _r, b in candidates])

            results = []
            for (rect, block), translated in zip(candidates, translations):
                if not translated:
                    continue
                fill_rgb = text_rgb = None
                if self._inplace:
                    try:
                        fill_rgb, text_rgb = pipeline.sample_block_colors(
                            self._image, block.x, block.y, block.w, block.h
                        )
                    except Exception:
                        fill_rgb = text_rgb = None  # degrade to the translucent box
                results.append((rect, block.text, translated, fill_rgb, text_rgb))
            self.signals.done.emit(results)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")

    def _translate_all(self, texts: "list[str]") -> "list[str]":
        """Translate each text, fanning out concurrent requests for network-bound
        backends. Order is preserved; the cache de-dupes repeats across the batch.
        A failure in any request propagates (caught by run -> failed signal)."""
        if not texts:
            return []
        parallel_safe = getattr(self._translator, "parallel_safe", True)
        if not parallel_safe:
            # Can't fan out (e.g. Argos) — send the whole batch in one shot so we pay
            # the per-call overhead (a subprocess round-trip) once, not N times.
            return self._translator.translate_batch(texts, self._source, self._target)
        workers = min(_MAX_TRANSLATE_WORKERS, len(texts))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(
                lambda t: self._translator.translate(t, self._source, self._target), texts
            ))
