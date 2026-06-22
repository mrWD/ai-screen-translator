"""Tray application wiring: hotkeys -> capture -> OCR -> translate -> overlay.

This module is the UI shell + composition root. The heavy lifting lives elsewhere:
the off-thread OCR/translate workers in `jobs.py`, their framework-free logic in
`pipeline.py`, and the single-in-flight-job state machine in `gating.py`.
"""

from __future__ import annotations

import logging
import sys

from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import QPoint, QRect, Qt, QThreadPool, QTimer, QUrl

from . import capture, changes, languages, log
from .config import Config, Region, history_dir
from .gating import BusyGate
from .history import HistoryWriter, build_index
from .hotkeys import HotkeyManager
from .jobs import Job, ScreenJob
from .ocr import OCRBackend, make_ocr
from .overlay import Overlay
from .region_selector import RegionSelector
from .screen_overlay import ScreenOverlay
from .settings_dialog import SettingsDialog
from .translate import TranslateBackend, make_translator

_log = logging.getLogger(__name__)

# How long a full-screen hold result stays up if the key was released before the
# (slow, e.g. offline) translation finished — so the work isn't wasted, but it
# still auto-hides instead of lingering forever.
_HOLD_LINGER_MS = 7000


class App:
    def __init__(self) -> None:
        log.setup()
        self.qt = QtWidgets.QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)

        self.cfg = Config.load()
        _log.info(
            "config: src=%s tgt=%s ocr=%s translate=%s suppress=%s accessory=%s region=%s",
            self.cfg.source, self.cfg.target, self.cfg.ocr_engine,
            self.cfg.translate_engine, self.cfg.suppress_hotkeys,
            self.cfg.accessory_mode, "set" if self.cfg.region else "none",
        )
        _log.info(
            "hotkeys: translate=%s hold(full screen)=%s reselect=%s hide=%s live=%s",
            self.cfg.hotkey_translate, self.cfg.hotkey_hold or "(none)",
            self.cfg.hotkey_reselect, self.cfg.hotkey_hide, self.cfg.hotkey_live,
        )
        self.pool = QThreadPool.globalInstance()
        self._ocr: OCRBackend | None = None
        self._translator: TranslateBackend | None = None
        self.history = HistoryWriter(self.cfg.history_keep_sessions, self.cfg.save_screenshots)
        self._last_capture_image = None  # most recent region/live frame (for history)
        self._last_fs_image = None       # most recent full-screen frame (for history)
        self._last_result = None         # (original, translation) for "Copy last result"
        self._fs_is_hold = False         # current full-screen request came from the hold key
        self._loading = False            # a "translating…" indicator is currently shown
        self._fs_linger = QTimer()       # auto-hide a hold result shown after the key was released
        self._fs_linger.setSingleShot(True)
        self._fs_linger.timeout.connect(self._linger_hide)
        # Single in-flight job, hold-key retry, and live-mode dedup: the boolean
        # policy lives in BusyGate; we only keep the QRunnable ref here so the
        # QThreadPool's C++ object isn't GC'd out from under the running job.
        self.gate = BusyGate()
        self._current_job = None

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
        self._warm_translator()  # pre-load a slow backend (offline) so the 1st translate is quick
        self._check_accessibility()  # global hotkeys silently get no events without this

    def _setup_hotkeys(self) -> None:
        self.gate.reset_hold()  # clean slate (a held key won't deliver a release)
        if getattr(self, "hotkeys", None) is not None:
            self.hotkeys.stop()  # stop the old listener thread before replacing it
        self.hotkeys = HotkeyManager(
            self.cfg.hotkey_translate,
            self.cfg.hotkey_reselect,
            self.cfg.hotkey_hide,
            self.cfg.hotkey_live,
            self.cfg.hotkey_hold,
            self.cfg.suppress_hotkeys,
        )
        self.hotkeys.translate.connect(self.translate_now)
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

    def _warm_translator(self) -> None:
        """Pre-load a slow translation backend in the background so the user's first
        hotkey press isn't a long wait. Only the offline (Argos) engine needs it: it
        spawns a subprocess and loads the model/torch on first use. Best-effort —
        if the engine/model isn't ready the warm-up just no-ops."""
        if self.cfg.translate_engine != "offline":
            return
        try:
            translator = self._get_translator()
        except Exception:
            return  # not installed yet — first real translate will report it
        src = self.cfg.source if self.cfg.source != "auto" else "en"
        tgt = self.cfg.target

        def _run() -> None:
            try:
                translator.translate("hello", src, tgt)  # spawns the child + loads the model
            except Exception:
                pass

        import threading

        threading.Thread(target=_run, daemon=True).start()

    # ---- "translating…" feedback (shown the instant a press is dispatched) ----
    def _show_loading(self, rect: QRect) -> None:
        self._loading = True
        self.overlay.show_text("⏳ Translating…", rect)

    def _clear_loading(self, hide: bool) -> None:
        if not self._loading:
            return
        self._loading = False
        if hide:  # result goes elsewhere (or failed) -> remove the indicator
            self.overlay.hide()

    def _cursor_rect(self) -> QRect:
        """A 1px anchor at the pointer, so full-screen feedback shows near the cursor."""
        pos = QtGui.QCursor.pos()
        return QRect(pos.x(), pos.y(), 1, 1)

    # ---- macOS activation policy (accessory / Dock-icon visibility) ----
    @staticmethod
    def _cocoa() -> bool:
        return sys.platform == "darwin" and QtGui.QGuiApplication.platformName() == "cocoa"

    def _apply_activation_policy(self) -> None:
        if not self._cocoa():
            _log.info("activation policy: not cocoa, skipping (accessory=%s)", self.cfg.accessory_mode)
            return
        try:
            from . import macos

            macos.set_activation_policy(self.cfg.accessory_mode)
            _log.info("activation policy set: accessory=%s", self.cfg.accessory_mode)
        except Exception:
            _log.exception("activation policy failed to apply")

    def _check_accessibility(self) -> None:
        """Global hotkeys need Accessibility trust; without it the event tap is
        created but receives no hardware key events (so hotkeys silently do
        nothing). Prompt + tell the user how to fix it. macOS only."""
        if not self._cocoa():
            return
        try:
            from . import macos

            trusted = macos.accessibility_trusted(prompt=True)
            _log.info("accessibility trusted: %s", trusted)
            if trusted:
                return
        except Exception:
            _log.exception("accessibility check failed")
            return
        _log.warning("NOT trusted for Accessibility — hotkeys will not receive events")
        self._notify(
            "Hotkeys need Accessibility",
            "Enable this app (or your terminal) in System Settings → Privacy & "
            "Security → Accessibility, then relaunch — otherwise the hotkeys do "
            "nothing.",
        )

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
        _log.info("translate_now (busy=%s region=%s)", self.gate.busy, bool(self.cfg.region))
        if self.gate.busy:
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
        self._show_loading(rect)  # instant feedback while OCR + translate run off-thread

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
        job = Job(ocr, image, rect, self.cfg.source, self.cfg.target, last_text, translator)
        job.signals.done.connect(self._on_job_done)
        job.signals.unchanged.connect(self._on_job_unchanged)
        job.signals.failed.connect(self._on_job_failed)
        self._current_job = job
        self.gate.try_start()
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
        if self.gate.busy or self.cfg.region is None:
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
        self._current_job = None
        replay = self.gate.finish()  # a hold waited for this job and the key is still down
        _log.info("job finished (gate.busy now=%s, replay_hold=%s)", self.gate.busy, replay)
        if replay:
            QTimer.singleShot(0, lambda: self.translate_fullscreen(is_hold=True))

    def _on_job_done(self, text: str, rect: QRect, ocr_text: str) -> None:
        self._last_ocr_text = ocr_text  # commit dedup baseline before clearing busy
        self._finish_job()
        self._loading = False  # replaced in place by the real result below
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
        _log.warning("job failed: %s", msg)
        self._finish_job()
        self._clear_loading(hide=True)
        self._notify("Error", msg)

    def _hide_overlays(self) -> None:
        self._fs_linger.stop()
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
        _log.info("translate_fullscreen (is_hold=%s busy=%s)", is_hold, self.gate.busy)
        if self.gate.busy:
            _log.info("  dropped: a job is already in flight")
            return
        self._fs_is_hold = is_hold
        self._hide_overlays()  # don't capture our own previous translations
        QTimer.singleShot(80, self._capture_fullscreen)

    def _on_hold_start(self) -> None:
        fire = self.gate.hold_start()  # not busy -> fire now; busy -> queued for _finish_job
        _log.info("hold_start (fire_now=%s busy=%s)", fire, self.gate.busy)
        if fire:
            self.translate_fullscreen(is_hold=True)

    def _on_hold_end(self) -> None:
        _log.info("hold_end")
        self.gate.hold_end()
        self._fs_linger.stop()
        self._clear_loading(hide=True)  # released before the result arrived -> drop the indicator
        self.screen_overlay.hide()  # release -> dismiss the translation

    def _linger_hide(self) -> None:
        _log.info("hold-result linger elapsed -> hiding full-screen overlay")
        self.screen_overlay.hide()

    def _capture_fullscreen(self) -> None:
        screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        geom = screen.geometry()
        dpr = screen.devicePixelRatio()
        region = Region(geom.x(), geom.y(), geom.width(), geom.height(), dpr)
        _log.info("capture full screen: geom=%s dpr=%s", (geom.x(), geom.y(), geom.width(), geom.height()), dpr)
        try:
            image = capture.grab(region)
        except Exception as exc:
            _log.exception("capture failed")
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
        job = ScreenJob(
            ocr, translator, image, geom.x(), geom.y(), geom.width(), geom.height(),
            self.cfg.source, self.cfg.target, self.cfg.overlay_inplace,
        )
        job.signals.done.connect(self._on_screen_done)
        job.signals.failed.connect(self._on_job_failed)
        self._current_job = job
        self.gate.try_start()
        self.pool.start(job)
        _log.info("full-screen job dispatched (is_hold=%s)", self._fs_is_hold)
        self._show_loading(self._cursor_rect())  # instant feedback near the pointer

    def _on_screen_done(self, blocks) -> None:
        _log.info("full-screen job done: %d blocks (is_hold=%s hold_active=%s)",
                  len(blocks) if blocks else 0, self._fs_is_hold, self.gate.hold_active)
        self._finish_job()
        self._clear_loading(hide=True)  # result goes to the full-screen overlay below
        is_hold = self._fs_is_hold
        if not blocks:
            if not is_hold:  # don't nag on every quick hold-peek
                self._notify("Full screen", "No text found on screen.")
            return
        if is_hold and not self.gate.hold_active:
            # Key released before the (slow) result arrived. Don't throw the work
            # away — show it, but auto-hide after a linger so nothing stays stuck.
            _log.info("hold released before result — showing with %dms auto-hide", _HOLD_LINGER_MS)
            self._fs_linger.start(_HOLD_LINGER_MS)
        overlay_blocks = [
            (rect, translated, fill_rgb, text_rgb)
            for (rect, _orig, translated, fill_rgb, text_rgb) in blocks
        ]
        if self._fs_screen is not None:
            _log.info("showing full-screen overlay with %d blocks on screen %s",
                      len(overlay_blocks), self._fs_screen.name())
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
        translator_changed = (
            new.translate_engine != old.translate_engine
            or new.deepl_api_key != old.deepl_api_key
            or new.offline_model_dir != old.offline_model_dir
        )
        if translator_changed:
            self._translator = None  # source isn't needed: the cache is keyed by it
        self.overlay.set_style(new.overlay_font_pt, new.overlay_opacity)
        self.screen_overlay.set_opacity(new.overlay_opacity)
        self.history.save_screenshots = new.save_screenshots
        if self._live_on:
            self._live_timer.setInterval(max(200, new.live_interval_ms))
        hotkeys_changed = (
            new.hotkey_translate != old.hotkey_translate
            or new.hotkey_hold != old.hotkey_hold
            or new.hotkey_reselect != old.hotkey_reselect
            or new.hotkey_hide != old.hotkey_hide
            or new.hotkey_live != old.hotkey_live
            or new.suppress_hotkeys != old.suppress_hotkeys
        )
        if hotkeys_changed:
            self._setup_hotkeys()  # stops the old listener internally
        if new.accessory_mode != old.accessory_mode:
            # The Dock/menubar native objects were created under the launch-time
            # policy; flipping it live leaves Qt's menu state inconsistent.
            self._notify("Accessory mode", "Relaunch the app to fully apply this change.")
        self._rebuild_menu()
        if translator_changed:
            self._warm_translator()  # newly-selected offline engine: pre-load in the background

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
