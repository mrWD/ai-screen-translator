"""Settings dialog: edit languages, OCR/translation engine, overlay style and
hotkeys. Returns a new Config; the app applies and persists it.

Some config fields are intentionally NOT exposed here (kept at safe defaults):
`ocr_engine` (always "auto" — it routes correctly) and `accessory_mode` (kept ON
so the overlay floats over fullscreen games — exposing it as a toggle was a
footgun)."""

from __future__ import annotations

import sys
from dataclasses import replace

from PySide6 import QtWidgets
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal

from . import languages, offline_models
from .config import Config
from .hotkey_edit import HotkeyEdit


class _DownloadSignals(QObject):
    progress = Signal(str)
    finished = Signal(bool, str)


class _ModelDownloadWorker(QRunnable):
    """Downloads the Argos pack(s) off the UI thread so the modal Settings dialog
    stays responsive. Progress + the final result come back via queued signals."""

    def __init__(self, src: str, tgt: str, model_dir: str) -> None:
        super().__init__()
        self._src, self._tgt, self._model_dir = src, tgt, model_dir
        self.signals = _DownloadSignals()

    def run(self) -> None:
        try:
            offline_models.download_model(
                self._src, self._tgt, self._model_dir, log=self.signals.progress.emit
            )
        except Exception as exc:  # surface install/network/no-pack errors to the user
            self.signals.finished.emit(False, f"⚠️ {exc}")
            return
        self.signals.finished.emit(True, "✓ Offline model ready.")


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI Screen Translator — Settings")
        # App-modal so a tray-only app (no main window) gets proper modality/focus.
        self.setWindowModality(Qt.ApplicationModal)
        self._cfg = cfg
        form = QtWidgets.QFormLayout(self)

        self._source = QtWidgets.QComboBox()
        for lang in languages.SOURCE_LANGUAGES:
            self._source.addItem(lang.name, lang.code)
        self._select_code(self._source, cfg.source)
        form.addRow("Source language", self._source)

        self._target = QtWidgets.QComboBox()
        for lang in languages.TARGET_LANGUAGES:
            self._target.addItem(lang.name, lang.code)
        self._select_code(self._target, cfg.target)
        form.addRow("Target language", self._target)

        self._ocr_fast = QtWidgets.QCheckBox("Fast OCR (≈2× faster; uncheck for accuracy)")
        self._ocr_fast.setChecked(cfg.ocr_fast)
        form.addRow("OCR", self._ocr_fast)

        self._translate_engine = QtWidgets.QComboBox()
        # Offline first (it's the default/recommended). Data stays the engine id;
        # labels spell out the privacy trade-off so the choice is informed.
        self._translate_engine.addItem("Offline — on-device, private (no network)", "offline")
        self._translate_engine.addItem("Google — free, sends screen text online", "google")
        self._translate_engine.setToolTip(
            "Offline runs entirely on your machine. Google sends your on-screen text "
            "to Google's servers over the internet for translation."
        )
        self._select_code(self._translate_engine, cfg.translate_engine)
        form.addRow("Translation engine", self._translate_engine)

        # One-click setup for the "offline" engine: installs argostranslate (if
        # needed) and downloads the language pack for the languages selected above.
        offline_row = QtWidgets.QWidget()
        offline_layout = QtWidgets.QVBoxLayout(offline_row)
        offline_layout.setContentsMargins(0, 0, 0, 0)
        self._offline_btn = QtWidgets.QPushButton("Download model for the selected languages")
        self._offline_btn.setToolTip(
            "Installs Argos Translate and the language pack so the 'offline' "
            "engine works without internet."
        )
        self._offline_btn.clicked.connect(self._download_offline_model)
        self._offline_status = QtWidgets.QLabel("")
        self._offline_status.setWordWrap(True)
        self._offline_status.setStyleSheet("color: gray; font-size: 11px;")
        offline_layout.addWidget(self._offline_btn)
        offline_layout.addWidget(self._offline_status)
        form.addRow("Offline model", offline_row)

        self._font = QtWidgets.QSpinBox()
        self._font.setRange(8, 72)
        self._font.setValue(cfg.overlay_font_pt)
        form.addRow("Overlay font (pt)", self._font)

        self._opacity = QtWidgets.QDoubleSpinBox()
        self._opacity.setRange(0.1, 1.0)
        self._opacity.setSingleStep(0.05)
        self._opacity.setValue(cfg.overlay_opacity)
        form.addRow("Overlay opacity", self._opacity)

        self._save_history = QtWidgets.QCheckBox("Save originals + translations to disk")
        self._save_history.setChecked(cfg.save_history)
        form.addRow("History", self._save_history)

        self._save_screenshots = QtWidgets.QCheckBox("Also save screenshots")
        self._save_screenshots.setChecked(cfg.save_screenshots)
        form.addRow("", self._save_screenshots)

        self._hk_hold = HotkeyEdit(cfg.hotkey_hold)
        self._hk_hide = HotkeyEdit(cfg.hotkey_hide)
        form.addRow("Full screen: HOLD to show", self._hk_hold)
        form.addRow("Hotkey: hide", self._hk_hide)
        hint = QtWidgets.QLabel(
            "Click a field and press the key(s). Single keys like F6 work. "
            "“HOLD to show” shows the full-screen translation only while held."
        )
        hint.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("", hint)

        self._suppress = QtWidgets.QCheckBox("Block the key's normal action (single keys only)")
        self._suppress.setChecked(cfg.suppress_hotkeys)
        self._suppress.setToolTip(
            "Swallow a single-key hotkey's default action (e.g. F1 = Help) so only "
            "the translation runs. macOS needs Accessibility permission; F1/F2/media "
            "keys can't be caught — use a plain function key."
        )
        if sys.platform.startswith("linux"):
            # pynput exposes no per-event suppression hook on Linux (only macOS/Windows).
            self._suppress.setEnabled(False)
            self._suppress.setToolTip("Not supported on Linux (no global key-suppression hook).")
        form.addRow("Suppress", self._suppress)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @staticmethod
    def _select_code(combo: QtWidgets.QComboBox, code: str) -> None:
        index = combo.findData(code)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _download_offline_model(self) -> None:
        src = self._source.currentData()
        tgt = self._target.currentData()
        if src == "auto":
            self._offline_status.setText(
                "Pick an explicit source language (not Auto-detect) first."
            )
            return
        if src == tgt:
            self._offline_status.setText("Source and target are the same language.")
            return
        self._offline_btn.setEnabled(False)
        self._offline_status.setText("Starting…")
        worker = _ModelDownloadWorker(src, tgt, self._cfg.offline_model_dir)
        worker.signals.progress.connect(self._offline_status.setText)
        worker.signals.finished.connect(self._on_offline_done)
        self._dl_worker = worker  # keep a ref; the pool only holds a C++ pointer
        QThreadPool.globalInstance().start(worker)

    def _on_offline_done(self, _ok: bool, msg: str) -> None:
        self._offline_btn.setEnabled(True)
        self._offline_status.setText(msg)

    def result_config(self) -> Config:
        """A new Config reflecting the edits (everything else preserved)."""
        # Fields not in the dialog (ocr_engine, accessory_mode) are preserved from
        # self._cfg by replace().
        return replace(
            self._cfg,
            source=self._source.currentData() or self._cfg.source,
            target=self._target.currentData() or self._cfg.target,
            ocr_fast=self._ocr_fast.isChecked(),
            translate_engine=self._translate_engine.currentData() or self._cfg.translate_engine,
            overlay_font_pt=self._font.value(),
            overlay_opacity=round(self._opacity.value(), 2),
            save_history=self._save_history.isChecked(),
            save_screenshots=self._save_screenshots.isChecked(),
            hotkey_hold=self._hk_hold.hotkey() or self._cfg.hotkey_hold,
            hotkey_hide=self._hk_hide.hotkey() or self._cfg.hotkey_hide,
            suppress_hotkeys=self._suppress.isChecked(),
        )
