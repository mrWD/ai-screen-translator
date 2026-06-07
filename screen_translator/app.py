"""Tray application wiring: hotkeys -> capture -> OCR -> translate -> overlay."""

from __future__ import annotations

import sys

from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import (
    QObject,
    QPoint,
    QRect,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
    Slot,
)

from . import capture, changes, languages
from .config import Config, Region, history_dir
from .history import HistoryWriter, build_index
from .hotkeys import HotkeyManager
from .ocr import OCRBackend, make_ocr
from .overlay import Overlay
from .region_selector import RegionSelector
from .screen_overlay import ScreenOverlay
from .settings_dialog import SettingsDialog
from .translate import TranslateBackend, make_translator


class _JobSignals(QObject):
    done = Signal(str, object, str)  # translated text, region QRect, ocr text
    unchanged = Signal()             # live mode: OCR text same as last -> no-op
    failed = Signal(str)


class _Job(QRunnable):
    """Runs OCR + translation off the UI thread (translation hits the network).

    In live mode (`dedup=True`) it short-circuits when the OCR'd text matches the
    last result, so we never re-translate or churn the overlay on an unchanged
    line even while the game's background animates."""

    def __init__(
        self,
        ocr: OCRBackend,
        image,
        region_rect: QRect,
        source: str,
        target: str,
        last_text: "str | None",
        translator: TranslateBackend,
    ) -> None:
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
            if not text:
                if self._last_text is None:
                    self.signals.done.emit("(no text found)", self._region_rect, "")
                else:
                    self.signals.unchanged.emit()  # live: text vanished, keep panel
                return
            if self._last_text is not None and text == self._last_text:
                self.signals.unchanged.emit()
                return
            translated = self._translator.translate(text, self._source, self._target)
            self.signals.done.emit(translated, self._region_rect, text)
        except Exception as exc:  # surfaced to the user via the tray
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")


def _sample_block_colors(image, bx, by, bw, bh):
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


class _ScreenJobSignals(QObject):
    # list of (screen-logical QRect, original text, translated text, fill_rgb, text_rgb);
    # the two rgb int-tuples are sampled for in-place mode, or None when it's off.
    done = Signal(object)
    failed = Signal(str)


class _ScreenJob(QRunnable):
    """Full-screen mode: OCR every text block, translate each, and map each
    block's image-pixel box to screen-logical coordinates for in-place overlay."""

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
            # Self-calibrate: map image pixels -> logical screen using the ACTUAL
            # captured image size, not an assumed dpr (mss may capture at 1x or 2x).
            img_w, img_h = self._image.size
            scale_x = img_w / self._gw if self._gw else 1.0
            scale_y = img_h / self._gh if self._gh else 1.0
            results = []
            for block in blocks:
                rect = QRect(
                    int(self._gx + block.x / scale_x),
                    int(self._gy + block.y / scale_y),
                    max(1, int(block.w / scale_x)),
                    max(1, int(block.h / scale_y)),
                )
                # junk filter: skip tiny blocks (icons/noise) and the macOS
                # menu-bar strip — done BEFORE translating to save network calls.
                if rect.height() < 8 or rect.width() < 6:
                    continue
                if sys.platform == "darwin" and self._gy == 0 and rect.y() < 24:
                    continue  # menu bar only exists on the primary display's top
                translated = self._translator.translate(block.text, self._source, self._target)
                if not translated:
                    continue
                fill_rgb = text_rgb = None
                if self._inplace:
                    try:
                        fill_rgb, text_rgb = _sample_block_colors(
                            self._image, block.x, block.y, block.w, block.h
                        )
                    except Exception:
                        fill_rgb = text_rgb = None  # degrade to the translucent box
                results.append((rect, block.text, translated, fill_rgb, text_rgb))
            self.signals.done.emit(results)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")


class App:
    def __init__(self) -> None:
        self.qt = QtWidgets.QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)

        self.cfg = Config.load()
        self.pool = QThreadPool.globalInstance()
        self._ocr: OCRBackend | None = None
        self._translator: TranslateBackend | None = None
        self.history = HistoryWriter(self.cfg.history_keep_sessions, self.cfg.save_screenshots)
        self._last_capture_image = None  # most recent region/live frame (for history)
        self._last_fs_image = None       # most recent full-screen frame (for history)
        self._last_result = None         # (original, translation) for "Copy last result"
        self._fs_is_hold = False         # current full-screen request came from the hold key
        self._hold_active = False        # the hold key is currently held down
        self._hold_pending = False       # hold pressed while busy -> retry when free
        # One in-flight job at a time. Holding the reference keeps the QRunnable
        # alive (QThreadPool only stores a C++ pointer), and the busy flag stops
        # overlapping captures from racing on the shared OCR engine / cache.
        self._current_job: _Job | None = None
        self._busy = False

        # Live mode: periodically re-capture the saved region and re-translate
        # only when its content changes.
        self._live_on = False
        self._last_sig = None        # last frame signature (cheap frozen-frame skip)
        self._last_ocr_text = None   # last OCR'd text (real change gate in live mode)
        self._enable_live_after_select = False
        self._live_timer = QTimer()
        self._live_timer.timeout.connect(self._live_tick)

        self.overlay = Overlay(self.cfg.overlay_font_pt, self.cfg.overlay_opacity)
        self.screen_overlay = ScreenOverlay(self.cfg.overlay_opacity)
        self._fs_screen = None  # screen targeted by the in-flight full-screen job
        self.selector = RegionSelector()
        self.selector.selected.connect(self._on_region_selected)
        self.selector.cancelled.connect(self._on_select_cancelled)
        self.selector.closed.connect(self._selector_focus_release)  # restore policy on any dismissal
        self._focus_bracketed = False  # accessory: was the selection bracket applied?

        self._build_tray()
        self._setup_hotkeys()
        self._apply_activation_policy()  # after the tray/menubar native objects exist

    def _setup_hotkeys(self) -> None:
        self._hold_active = False  # clean slate (a held key won't deliver a release)
        self._hold_pending = False
        if getattr(self, "hotkeys", None) is not None:
            self.hotkeys.stop()  # stop the old listener thread before replacing it
        self.hotkeys = HotkeyManager(
            self.cfg.hotkey_translate,
            self.cfg.hotkey_fullscreen,
            self.cfg.hotkey_reselect,
            self.cfg.hotkey_hide,
            self.cfg.hotkey_live,
            self.cfg.hotkey_hold,
        )
        self.hotkeys.translate.connect(self.translate_now)
        self.hotkeys.fullscreen.connect(self.translate_fullscreen)
        self.hotkeys.reselect.connect(self.reselect_region)
        self.hotkeys.hide.connect(self._hide_overlays)
        self.hotkeys.live.connect(self.toggle_live)
        self.hotkeys.hold_start.connect(self._on_hold_start)
        self.hotkeys.hold_end.connect(self._on_hold_end)
        try:
            self.hotkeys.start()
        except Exception as exc:
            self._notify("Hotkeys unavailable", f"{exc}\nUse the menu-bar icon instead.")

    # ---- OCR backend (lazily built, rebuilt when the source language changes) ----
    def _get_ocr(self) -> OCRBackend:
        if self._ocr is None:
            self._ocr = make_ocr(self.cfg.ocr_engine, self.cfg.source)
        return self._ocr

    def _ocr_name(self) -> str:
        return self._ocr.name if self._ocr is not None else "?"

    # ---- translation backend (lazily built, rebuilt on engine/key/source change) ----
    def _get_translator(self) -> TranslateBackend:
        if self._translator is None:
            self._translator = make_translator(
                self.cfg.translate_engine,
                deepl_api_key=self.cfg.deepl_api_key,
                offline_model_dir=self.cfg.offline_model_dir,
            )
        return self._translator

    def _translator_name(self) -> str:
        return self._translator.name if self._translator is not None else "?"

    # ---- macOS activation policy (accessory / Dock-icon visibility) ----
    @staticmethod
    def _cocoa() -> bool:
        return sys.platform == "darwin" and QtGui.QGuiApplication.platformName() == "cocoa"

    def _apply_activation_policy(self) -> None:
        if not self._cocoa():
            return
        try:
            from . import macos

            macos.set_activation_policy(self.cfg.accessory_mode)
        except Exception:
            pass  # best-effort; app still runs as a normal Dock app

    def _selector_focus_acquire(self) -> None:
        """Accessory apps can't make a frameless overlay the key window, so briefly
        become a Regular foreground app while the region selector is up — otherwise
        the selector gets no keyboard focus (Esc to cancel) or mouse drag."""
        # Decide once, here, so a mid-selection Settings toggle can't unbalance the
        # acquire/release pair (release acts only on this captured flag).
        self._focus_bracketed = bool(self.cfg.accessory_mode and self._cocoa())
        if not self._focus_bracketed:
            return
        try:
            from . import macos

            macos.set_activation_policy(False)  # Regular
            macos.activate_app()
        except Exception:
            pass

    def _selector_focus_release(self) -> None:
        if not self._focus_bracketed:
            return
        self._focus_bracketed = False
        if not self._cocoa():
            return
        try:
            from . import macos

            macos.set_activation_policy(True)  # back to Accessory
        except Exception:
            pass

    # ---- actions ----
    def translate_now(self) -> None:
        if self._busy:
            return  # a translation is already in flight
        if self.cfg.region is None:
            self.reselect_region()
            return
        self.overlay.hide()
        # let the overlay actually disappear before we grab the screen
        QTimer.singleShot(60, self._capture_and_dispatch)

    def _capture_and_dispatch(self) -> None:
        region = self._region_with_live_dpr(self.cfg.region)
        rect = QRect(region.x, region.y, region.w, region.h)
        try:
            image = capture.grab(region)
        except Exception as exc:
            self._notify("Capture failed", str(exc))
            return

        if capture.is_black(image):
            self.overlay.show_text(
                "⚠️ Black frame. Grant Screen Recording permission, or this is "
                "DRM-protected / exclusive-fullscreen content (switch the game to "
                "Borderless Windowed).",
                rect,
            )
            return

        self._last_sig = changes.signature(image)  # seed the live-mode baseline
        self._dispatch(image, rect, dedup=False)  # explicit press always shows

    def _dispatch(self, image, rect: QRect, dedup: bool) -> None:
        try:
            ocr = self._get_ocr()
        except Exception as exc:
            self._notify("OCR unavailable", str(exc))
            return
        try:
            translator = self._get_translator()
        except Exception as exc:
            self._notify("Translation unavailable", str(exc))
            return
        self._last_capture_image = image  # kept for history persistence in _on_job_done
        last_text = self._last_ocr_text if dedup else None
        job = _Job(ocr, image, rect, self.cfg.source, self.cfg.target, last_text, translator)
        job.signals.done.connect(self._on_job_done)
        job.signals.unchanged.connect(self._on_job_unchanged)
        job.signals.failed.connect(self._on_job_failed)
        self._current_job = job
        self._busy = True
        self.pool.start(job)

    # ---- live mode ----
    def toggle_live(self) -> None:
        if self._live_on:
            self._stop_live()
            return
        if self.cfg.region is None:
            self._enable_live_after_select = True
            self.reselect_region()
            return
        self._start_live()

    def _start_live(self) -> None:
        self._live_on = True
        self._last_sig = None
        self._last_ocr_text = None
        self._live_timer.setInterval(max(200, self.cfg.live_interval_ms))
        self._live_timer.start()
        self._update_live_action()
        self._notify("Live mode ON", "Auto-translating the selected region.")

    def _stop_live(self) -> None:
        self._live_on = False
        self._live_timer.stop()
        self._update_live_action()
        self._notify("Live mode OFF", "")

    def _live_tick(self) -> None:
        if self._busy or self.cfg.region is None:
            return
        region = self._region_with_live_dpr(self.cfg.region)
        try:
            image = capture.grab(region)
        except Exception:
            return  # transient capture hiccup; try again next tick
        if capture.is_black(image):
            return
        sig = changes.signature(image)
        if not changes.changed(self._last_sig, sig):
            return  # frame is essentially frozen — skip OCR entirely
        self._last_sig = sig
        self._dispatch(image, QRect(region.x, region.y, region.w, region.h), dedup=True)

    def _region_with_live_dpr(self, region: Region) -> Region:
        """Recompute devicePixelRatio from the screen under the region now, so a
        region selected on one display still captures correctly if the content
        later sits on a display with a different scale factor."""
        screen = QtGui.QGuiApplication.screenAt(QPoint(region.x, region.y))
        if screen is None:
            return region
        return Region(region.x, region.y, region.w, region.h, screen.devicePixelRatio())

    def _finish_job(self) -> None:
        self._busy = False
        self._current_job = None
        if self._hold_pending and self._hold_active:  # a hold waited for this job
            self._hold_pending = False
            QTimer.singleShot(0, lambda: self.translate_fullscreen(is_hold=True))

    def _on_job_done(self, text: str, rect: QRect, ocr_text: str) -> None:
        self._last_ocr_text = ocr_text  # commit dedup baseline before clearing busy
        self._finish_job()
        self.overlay.show_text(text, rect)
        if ocr_text.strip():
            self._last_result = (ocr_text, text)
            if self.cfg.save_history:
                mode = "live" if self._live_on else "region"
                self.history.add(
                    [(ocr_text, text)], self._last_capture_image,
                    self.cfg.source, self.cfg.target, self._ocr_name(), mode,
                )

    def _on_job_unchanged(self) -> None:
        self._finish_job()  # live: nothing changed, leave the panel as-is

    def _on_job_failed(self, msg: str) -> None:
        self._finish_job()
        self._notify("Error", msg)

    def _hide_overlays(self) -> None:
        self.overlay.hide()
        self.screen_overlay.hide()

    # ---- history (save / review / copy) ----
    def copy_last_result(self) -> None:
        if self._last_result is None:
            self._notify("Copy", "Nothing translated yet.")
            return
        original, translation = self._last_result
        self.qt.clipboard().setText(f"{original}\n\n{translation}")
        self._notify("Copied", "Original + translation copied to clipboard.")

    def open_history(self) -> None:
        session = self.history.session_dir
        if session is None:
            self._notify("History", "No translations saved yet this session.")
            return
        index = build_index(session)  # regenerate the HTML from the JSONL log
        QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(str(index)))

    def open_history_folder(self) -> None:
        folder = history_dir()
        if not folder.exists():
            self._notify("History", "No history saved yet.")
            return
        QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ---- full-screen translation ----
    def translate_fullscreen(self, is_hold: bool = False) -> None:
        if self._busy:
            return
        self._fs_is_hold = is_hold
        self._hide_overlays()  # don't capture our own previous translations
        QTimer.singleShot(80, self._capture_fullscreen)

    def _on_hold_start(self) -> None:
        self._hold_active = True
        if self._busy:
            self._hold_pending = True  # something's running; fire as soon as it frees
        else:
            self.translate_fullscreen(is_hold=True)

    def _on_hold_end(self) -> None:
        self._hold_active = False
        self._hold_pending = False
        self.screen_overlay.hide()  # release -> dismiss the translation

    def _capture_fullscreen(self) -> None:
        screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        geom = screen.geometry()
        dpr = screen.devicePixelRatio()
        region = Region(geom.x(), geom.y(), geom.width(), geom.height(), dpr)
        try:
            image = capture.grab(region)
        except Exception as exc:
            self._notify("Capture failed", str(exc))
            return
        if capture.is_black(image):
            self._notify(
                "Black frame",
                "Grant Screen Recording, or the content is DRM/exclusive-fullscreen.",
            )
            return
        try:
            ocr = self._get_ocr()
        except Exception as exc:
            self._notify("OCR unavailable", str(exc))
            return
        try:
            translator = self._get_translator()
        except Exception as exc:
            self._notify("Translation unavailable", str(exc))
            return
        self._fs_screen = screen
        self._last_fs_image = image  # kept for history persistence in _on_screen_done
        job = _ScreenJob(
            ocr, translator, image, geom.x(), geom.y(), geom.width(), geom.height(),
            self.cfg.source, self.cfg.target, self.cfg.overlay_inplace,
        )
        job.signals.done.connect(self._on_screen_done)
        job.signals.failed.connect(self._on_job_failed)
        self._current_job = job
        self._busy = True
        self.pool.start(job)

    def _on_screen_done(self, blocks) -> None:
        self._finish_job()
        is_hold = self._fs_is_hold
        if not blocks:
            if not is_hold:  # don't nag on every quick hold-peek
                self._notify("Full screen", "No text found on screen.")
            return
        if is_hold and not self._hold_active:
            return  # key released before the result arrived — don't flash it
        overlay_blocks = [
            (rect, translated, fill_rgb, text_rgb)
            for (rect, _orig, translated, fill_rgb, text_rgb) in blocks
        ]
        if self._fs_screen is not None:
            self.screen_overlay.show_blocks(
                overlay_blocks, self._fs_screen, inplace=self.cfg.overlay_inplace
            )
        pairs = [(orig, translated) for (_rect, orig, translated, _f, _t) in blocks]
        self._last_result = (
            "\n".join(o for o, _ in pairs),
            "\n".join(t for _, t in pairs),
        )
        # Persist explicit captures, but NOT transient hold-peeks (they'd flood
        # history with large full-screen screenshots).
        if not is_hold and self.cfg.save_history:
            self.history.add(
                pairs, self._last_fs_image, self.cfg.source, self.cfg.target,
                self._ocr_name(), "fullscreen",
            )

    def reselect_region(self) -> None:
        self.overlay.hide()
        self._selector_focus_acquire()
        try:
            self.selector.start()
        except Exception as exc:  # don't strand the app in Regular policy
            self._selector_focus_release()
            self._notify("Select region", str(exc))

    def _on_region_selected(self, rect: QRect, dpr: float) -> None:
        self._selector_focus_release()
        self.cfg.region = Region(rect.x(), rect.y(), rect.width(), rect.height(), dpr)
        self.cfg.save()
        self._last_sig = None       # new region -> drop stale change/dedup state
        self._last_ocr_text = None
        if self._enable_live_after_select:
            self._enable_live_after_select = False
            QTimer.singleShot(150, self._start_live)
        else:
            QTimer.singleShot(150, self.translate_now)  # translate right after selecting

    def _on_select_cancelled(self) -> None:
        self._selector_focus_release()
        self._enable_live_after_select = False

    # ---- menus ----
    def _build_tray(self) -> None:
        self.tray = QtWidgets.QSystemTrayIcon(self._make_icon())
        self.tray.setToolTip("AI Screen Translator")
        # A parentless QMenuBar becomes the shared macOS application menu bar, so
        # there's always a visible "Translator" menu at the top — no hunting for
        # the menu-bar icon. (Goes away later if we switch to an accessory app.)
        self._menubar = QtWidgets.QMenuBar()
        self._rebuild_menu()
        self.tray.show()

    def _rebuild_menu(self) -> None:
        # Keep explicit Python refs to everything we create — PySide6's GC can
        # otherwise collect menu/group wrappers and tear down their C++ objects.
        self._lang_groups = []   # QActionGroups
        self._live_actions = []  # "Live mode" checkable actions (kept in sync)
        self._submenus = []      # all QMenus (top + language submenus)

        if getattr(self, "_menu", None) is not None:
            self._menu.deleteLater()  # release the previous tray menu + its actions
        self._menu = QtWidgets.QMenu()
        self._populate_menu(self._menu)
        self.tray.setContextMenu(self._menu)

        self._menubar.clear()
        top = self._menubar.addMenu("Translator")
        self._submenus.append(top)
        self._populate_menu(top)

    def _populate_menu(self, menu) -> None:
        menu.addAction("Translate now", self.translate_now)
        menu.addAction("Translate full screen", self.translate_fullscreen)
        live = menu.addAction("Live mode", self.toggle_live)
        live.setCheckable(True)
        live.setChecked(self._live_on)
        self._live_actions.append(live)
        menu.addAction("Select region…", self.reselect_region)
        menu.addAction("Hide overlay", self._hide_overlays)
        menu.addAction("Copy last result", self.copy_last_result)
        menu.addSeparator()
        menu.addAction("Open translation log", self.open_history)
        menu.addAction("Open history folder", self.open_history_folder)
        menu.addSeparator()

        src_menu = menu.addMenu("Source language")
        self._submenus.append(src_menu)
        self._add_lang_actions(src_menu, languages.SOURCE_LANGUAGES, self._set_source, self.cfg.source)
        tgt_menu = menu.addMenu("Target language")
        self._submenus.append(tgt_menu)
        self._add_lang_actions(tgt_menu, languages.TARGET_LANGUAGES, self._set_target, self.cfg.target)

        menu.addAction("Settings…", self.open_settings)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)

    def _update_live_action(self) -> None:
        for action in getattr(self, "_live_actions", []):
            action.setChecked(self._live_on)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.cfg)
        dialog.raise_()
        dialog.activateWindow()
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        self._apply_settings(dialog.result_config())

    def _apply_settings(self, new: Config) -> None:
        old = self.cfg
        self.cfg = new
        self.cfg.save()
        # Apply changes that have live side effects.
        if new.source != old.source or new.ocr_engine != old.ocr_engine:
            self._ocr = None
        if (new.translate_engine != old.translate_engine
                or new.deepl_api_key != old.deepl_api_key
                or new.offline_model_dir != old.offline_model_dir):
            self._translator = None  # source isn't needed: the cache is keyed by it
        self.overlay.set_style(new.overlay_font_pt, new.overlay_opacity)
        self.screen_overlay.set_opacity(new.overlay_opacity)
        self.history.save_screenshots = new.save_screenshots
        if self._live_on:
            self._live_timer.setInterval(max(200, new.live_interval_ms))
        hotkeys_changed = (
            new.hotkey_translate != old.hotkey_translate
            or new.hotkey_fullscreen != old.hotkey_fullscreen
            or new.hotkey_hold != old.hotkey_hold
            or new.hotkey_reselect != old.hotkey_reselect
            or new.hotkey_hide != old.hotkey_hide
            or new.hotkey_live != old.hotkey_live
        )
        if hotkeys_changed:
            self._setup_hotkeys()  # stops the old listener internally
        if new.accessory_mode != old.accessory_mode:
            # The Dock/menubar native objects were created under the launch-time
            # policy; flipping it live leaves Qt's menu state inconsistent.
            self._notify("Accessory mode", "Relaunch the app to fully apply this change.")
        self._rebuild_menu()

    def _add_lang_actions(self, menu, langs, setter, current) -> None:
        group = QtGui.QActionGroup(menu)
        group.setExclusive(True)
        self._lang_groups.append(group)
        for lang in langs:
            action = QtGui.QAction(lang.name, menu, checkable=True)
            action.setChecked(lang.code == current)
            action.triggered.connect(lambda _checked=False, code=lang.code: setter(code))
            group.addAction(action)
            menu.addAction(action)

    def _set_source(self, code: str) -> None:
        self.cfg.source = code
        self.cfg.save()
        self._ocr = None  # source script may change which engine/hint we need
        # translator NOT reset: its cache is keyed by source, so one backend serves all

    def _set_target(self, code: str) -> None:
        self.cfg.target = code
        self.cfg.save()

    def _make_icon(self) -> QtGui.QIcon:
        pixmap = QtGui.QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setBrush(QtGui.QColor(0, 150, 230))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
        painter.setPen(QtGui.QColor("white"))
        font = painter.font()
        font.setPointSize(26)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "文")
        painter.end()
        return QtGui.QIcon(pixmap)

    def _notify(self, title: str, message: str) -> None:
        if self.tray.supportsMessages():
            self.tray.showMessage(title, message)
        else:
            print(f"{title}: {message}", file=sys.stderr)

    def quit(self) -> None:
        self._live_timer.stop()
        self.hotkeys.stop()
        self.qt.quit()

    def run(self) -> int:
        self._notify(
            "AI Screen Translator",
            f"Running in the menu bar. Press {self.cfg.hotkey_translate} to translate.",
        )
        return self.qt.exec()


def main() -> None:
    sys.exit(App().run())
