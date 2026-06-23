"""Tray application wiring: hotkeys -> capture -> OCR -> translate -> overlay.

This module is the UI shell + composition root. The heavy lifting lives elsewhere:
the off-thread OCR/translate workers in `jobs.py`, their framework-free logic in
`pipeline.py`, and the single-in-flight-job state machine in `gating.py`.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import QRect, Qt, QThreadPool, QTimer, QUrl

from . import capture, languages, log, offline_models
from .config import Config, Region, history_dir
from .gating import BusyGate
from .history import HistoryWriter, build_index
from .hotkeys import HotkeyManager
from .jobs import ScreenJob
from .ocr import OCRBackend, make_ocr
from .overlay import Overlay
from .screen_overlay import ScreenOverlay
from .settings_dialog import SettingsDialog, _ModelDownloadWorker
from .translate import TranslateBackend, make_translator

_log = logging.getLogger(__name__)

# How long a full-screen hold result stays up if the key was released before the
# (slow, e.g. offline) translation finished — so the work isn't wasted, but it
# still auto-hides instead of lingering forever.
_HOLD_LINGER_MS = 7000


def _is_wayland() -> bool:
    """True on a Linux Wayland session, where pynput's X11 global-hotkey backend
    receives no events. (XWayland still exposes DISPLAY, so require its absence.)"""
    if not sys.platform.startswith("linux"):
        return False
    return (
        os.environ.get("XDG_SESSION_TYPE") == "wayland"
        or (bool(os.environ.get("WAYLAND_DISPLAY")) and not os.environ.get("DISPLAY"))
    )


class App:
    def __init__(self) -> None:
        log.setup()
        self.qt = QtWidgets.QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)

        self.cfg = Config.load()
        _log.info(
            "config: src=%s tgt=%s ocr=%s translate=%s fast_ocr=%s suppress=%s accessory=%s",
            self.cfg.source, self.cfg.target, self.cfg.ocr_engine,
            self.cfg.translate_engine, self.cfg.ocr_fast, self.cfg.suppress_hotkeys,
            self.cfg.accessory_mode,
        )
        _log.info(
            "hotkeys: hold(full screen)=%s hide=%s",
            self.cfg.hotkey_hold or "(none)", self.cfg.hotkey_hide,
        )
        self.pool = QThreadPool.globalInstance()
        self._ocr: OCRBackend | None = None
        self._translator: TranslateBackend | None = None
        self._model_dl_worker = None  # in-flight Argos download; ref keeps the C++ QRunnable alive
        self._model_dl_busy = False
        self.history = HistoryWriter(self.cfg.history_keep_sessions, self.cfg.save_screenshots)
        # History writes (PNG encode + JSONL) run off the UI thread so showing a
        # result never stutters. Single worker = serialized, so HistoryWriter's
        # _seq/_session_dir stay race-free.
        self._history_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="history")
        self._last_fs_image = None       # most recent full-screen frame (for history)
        self._last_result = None         # (original, translation) for "Copy last result"
        self._fs_is_hold = False         # current full-screen request came from the hold key
        self._fs_t0 = 0.0                # time.monotonic() at the last full-screen key press
        self._loading = False            # a "translating…" indicator is currently shown
        self._fs_linger = QTimer()       # auto-hide a hold result shown after the key was released
        self._fs_linger.setSingleShot(True)
        self._fs_linger.timeout.connect(self._linger_hide)
        # Single in-flight job, hold-key retry, and live-mode dedup: the boolean
        # policy lives in BusyGate; we only keep the QRunnable ref here so the
        # QThreadPool's C++ object isn't GC'd out from under the running job.
        self.gate = BusyGate()
        self._current_job = None

        self.overlay = Overlay(self.cfg.overlay_font_pt, self.cfg.overlay_opacity)  # loading indicator
        self.screen_overlay = ScreenOverlay(self.cfg.overlay_opacity)
        self._fs_screen = None  # screen targeted by the in-flight full-screen job

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
            self.cfg.hotkey_hide,
            self.cfg.hotkey_hold,
            self.cfg.suppress_hotkeys,
        )
        self.hotkeys.hide.connect(self._hide_overlays)
        self.hotkeys.hold_start.connect(self._on_hold_start)
        self.hotkeys.hold_end.connect(self._on_hold_end)
        try:
            self.hotkeys.start()
        except Exception as exc:
            self._notify("Hotkeys unavailable", f"{exc}\nUse the menu-bar icon instead.")
        if _is_wayland():
            # pynput's X11 backend receives zero events under a pure-Wayland session,
            # so global hotkeys silently never fire. Tell the user to use the menu.
            _log.warning("Wayland session detected — global hotkeys won't fire (X11/XWayland only)")
            self._notify(
                "Global hotkeys need X11",
                "On Wayland the hold-to-translate key won't fire — use the tray menu's "
                "“Translate full screen” instead (or run an X11/XWayland session).",
            )

    # ---- OCR backend (lazily built, rebuilt when the source language changes) ----
    def _get_ocr(self) -> OCRBackend:
        if self._ocr is None:
            self._ocr = make_ocr(self.cfg.ocr_engine, self.cfg.source, fast=self.cfg.ocr_fast)
        return self._ocr

    def _ocr_name(self) -> str:
        return self._ocr.name if self._ocr is not None else "?"

    # ---- translation backend (lazily built, rebuilt on engine/key/source change) ----
    def _get_translator(self) -> TranslateBackend:
        if self._translator is None:
            self._translator = make_translator(
                self.cfg.translate_engine,
                offline_model_dir=self.cfg.offline_model_dir,
            )
        return self._translator

    def _translator_name(self) -> str:
        return self._translator.name if self._translator is not None else "?"

    def _warm_translator(self) -> None:
        """Pre-load the slow pieces in the background so the user's FIRST hotkey
        press isn't a long wait: the OCR backend (imports ocrmac + warms Vision)
        for any engine, and the offline (Argos) translator (subprocess + model +
        ctranslate2 batch cold-start). Best-effort — failures just no-op."""
        src = self.cfg.source if self.cfg.source != "auto" else "en"
        tgt = self.cfg.target
        warm_offline = self.cfg.translate_engine == "offline"

        def _run() -> None:
            try:  # OCR: import ocrmac + a tiny recognize to warm Vision's recognizer
                from PIL import Image

                ocr = self._get_ocr()
                ocr.recognize(Image.new("RGB", (48, 48), (0, 0, 0)), self.cfg.source)
            except Exception:
                pass
            if warm_offline:
                try:
                    translator = self._get_translator()
                    # A BATCH, not one string: the first full-screen translate
                    # otherwise pays ctranslate2's batch cold-start (~3s vs ~1s).
                    translator.translate_batch(
                        ["Start", "Continue", "Settings", "Exit", "Load", "Save", "Back", "Options"],
                        src, tgt,
                    )
                except Exception:
                    pass

        import threading

        threading.Thread(target=_run, daemon=True).start()

    # ---- offline model: offer to download when a language pair isn't installed ----
    def _ensure_offline_model_async(self) -> None:
        """When the offline engine is active, check that an Argos pack covers the
        current source→target; if not, OFFER to download it now. On success the
        translator is rebuilt so the new pack takes effect without an app restart.
        Cheap + safe on the UI thread (lists installed packs; no torch import)."""
        if self.cfg.translate_engine != "offline":
            return
        src, tgt = self.cfg.source, self.cfg.target
        try:
            if offline_models.model_installed(src, tgt):
                return
        except Exception:
            pass  # can't tell -> fall through and offer the download
        if self._model_dl_busy:
            return  # a download is already running; don't stack prompts/dialogs
        s, t = src.split("-")[0], tgt.split("-")[0]
        answer = QtWidgets.QMessageBox.question(
            None,
            "Offline model",
            f"No offline translation model for {s} → {t} is installed yet.\n\n"
            "Download it now? (one-time, a few tens of MB)",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self._start_model_download(src, tgt)

    def _start_model_download(self, src: str, tgt: str) -> None:
        self._model_dl_busy = True
        s, t = src.split("-")[0], tgt.split("-")[0]
        self._notify("Offline model", f"Downloading {s}→{t}…")
        worker = _ModelDownloadWorker(src, tgt, self.cfg.offline_model_dir)
        self._model_dl_worker = worker  # keep a ref so the C++ QRunnable isn't GC'd
        worker.signals.progress.connect(lambda m: _log.info("offline model: %s", m))
        worker.signals.finished.connect(self._on_model_download_done)
        self.pool.start(worker)

    def _on_model_download_done(self, ok: bool, message: str) -> None:
        self._model_dl_busy = False
        self._model_dl_worker = None
        self._notify("Offline model", message)
        if ok and self.cfg.translate_engine == "offline":
            # Rebuild the translator so the running Argos subprocess (which cached the
            # old installed-package set) picks up the just-downloaded pack — no restart.
            if self._translator is not None:
                self._translator.close()
                self._translator = None
            self._warm_translator()

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

    # ---- full-screen capture/translate ----
    def _finish_job(self) -> None:
        self._current_job = None
        replay = self.gate.finish()  # a hold waited for this job and the key is still down
        _log.info("job finished (gate.busy now=%s, replay_hold=%s)", self.gate.busy, replay)
        if replay:
            QTimer.singleShot(0, lambda: self.translate_fullscreen(is_hold=True))

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
        self._fs_t0 = time.monotonic()  # T0 = key press, for stage timing
        _log.info("translate_fullscreen (is_hold=%s busy=%s) T0", is_hold, self.gate.busy)
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

    def _ms(self) -> float:
        """Milliseconds since the current full-screen request's key press (T0)."""
        return (time.monotonic() - self._fs_t0) * 1000.0

    def _capture_fullscreen(self) -> None:
        screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        geom = screen.geometry()
        dpr = screen.devicePixelRatio()
        region = Region(geom.x(), geom.y(), geom.width(), geom.height(), dpr)
        _log.info("capture start (+%.0fms from press): geom=%s dpr=%s",
                  self._ms(), (geom.x(), geom.y(), geom.width(), geom.height()), dpr)
        try:
            t = time.monotonic()
            image = capture.grab(region)
            _log.info("grab done (+%.0fms; grab took %.0fms)", self._ms(), (time.monotonic() - t) * 1000)
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
        # Show the "Translating…" indicator as soon as the screen is grabbed (before
        # building OCR/translator backends), so feedback isn't held up by a first-run
        # backend build. Grab had to finish first or the indicator would be captured.
        self._show_loading(self._cursor_rect())
        _log.info("⏳ Translating shown (+%.0fms from press)", self._ms())
        try:
            t = time.monotonic()
            ocr = self._get_ocr()
            translator = self._get_translator()
            built_ms = (time.monotonic() - t) * 1000
            if built_ms > 50:
                _log.info("backends built (+%.0fms; took %.0fms)", self._ms(), built_ms)
        except Exception as exc:
            self._clear_loading(hide=True)
            self._notify("OCR/Translation unavailable", str(exc))
            return
        self._fs_screen = screen
        self._last_fs_image = image  # kept for history persistence in _on_screen_done
        job = ScreenJob(
            ocr, translator, image, geom.x(), geom.y(), geom.width(), geom.height(),
            self.cfg.source, self.cfg.target,
        )
        job.signals.done.connect(self._on_screen_done)
        job.signals.failed.connect(self._on_job_failed)
        self._current_job = job
        self.gate.try_start()
        self.pool.start(job)
        _log.info("job dispatched (+%.0fms from press, is_hold=%s)", self._ms(), self._fs_is_hold)

    def _on_screen_done(self, blocks) -> None:
        _log.info("RESULT shown (+%.0fms from press): %d blocks (is_hold=%s hold_active=%s)",
                  self._ms(), len(blocks) if blocks else 0, self._fs_is_hold, self.gate.hold_active)
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
        overlay_blocks = [(rect, translated) for (rect, _orig, translated) in blocks]
        if self._fs_screen is not None:
            _log.info("showing full-screen overlay with %d blocks on screen %s",
                      len(overlay_blocks), self._fs_screen.name())
            self.screen_overlay.show_blocks(overlay_blocks, self._fs_screen)
        pairs = [(orig, translated) for (_rect, orig, translated) in blocks]
        self._last_result = (
            "\n".join(o for o, _ in pairs),
            "\n".join(t for _, t in pairs),
        )
        # Persist explicit captures, but NOT transient hold-peeks (they'd flood
        # history with large full-screen screenshots).
        if not is_hold and self.cfg.save_history:
            self._history_pool.submit(
                self.history.add,
                pairs, self._last_fs_image, self.cfg.source, self.cfg.target,
                self._ocr_name(), "fullscreen",
            )

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
        menu.addAction("Translate full screen", self.translate_fullscreen)
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

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.cfg)
        dialog.raise_()
        dialog.activateWindow()
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        self._apply_settings(dialog.result_config())

    def _apply_settings(self, new: Config) -> None:
        old = self.cfg
        if new.translate_engine == "google" and old.translate_engine != "google":
            # Switching to the online engine means screen text leaves the machine —
            # get explicit consent; on decline keep the previous (offline) engine.
            answer = QtWidgets.QMessageBox.warning(
                None,
                "Send screen text to Google?",
                "The Google engine sends your on-screen text to Google's servers over "
                "the internet for translation.\n\n"
                "The offline engine keeps everything on your device. Use Google anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                new.translate_engine = old.translate_engine
        self.cfg = new
        self.cfg.save()
        # Apply changes that have live side effects.
        if (new.source != old.source or new.ocr_engine != old.ocr_engine
                or new.ocr_fast != old.ocr_fast):
            self._ocr = None
        translator_changed = (
            new.translate_engine != old.translate_engine
            or new.offline_model_dir != old.offline_model_dir
        )
        if translator_changed and self._translator is not None:
            self._translator.close()  # stop the old Argos subprocess before dropping it
            self._translator = None  # source isn't needed: the cache is keyed by it
        self.overlay.set_style(new.overlay_font_pt, new.overlay_opacity)
        self.screen_overlay.set_opacity(new.overlay_opacity)
        self.history.save_screenshots = new.save_screenshots
        hotkeys_changed = (
            new.hotkey_hold != old.hotkey_hold
            or new.hotkey_hide != old.hotkey_hide
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
        # If offline is active and the (possibly new) language pair has no pack, offer it.
        self._ensure_offline_model_async()

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
        self._ensure_offline_model_async()  # offer to fetch the pack for the new pair

    def _set_target(self, code: str) -> None:
        self.cfg.target = code
        self.cfg.save()
        self._ensure_offline_model_async()  # offer to fetch the pack for the new pair

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
        self.hotkeys.stop()
        if self._translator is not None:
            self._translator.close()  # don't leave the Argos subprocess running
        self._history_pool.shutdown(wait=False)
        self.qt.quit()

    def run(self) -> int:
        self._notify(
            "AI Screen Translator",
            f"Running in the menu bar. Hold {self.cfg.hotkey_hold} to translate the screen.",
        )
        return self.qt.exec()


def main() -> None:
    sys.exit(App().run())
