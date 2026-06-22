"""Settings dialog: edit languages, OCR engine, live interval, overlay style and
hotkeys. Returns a new Config; the app applies and persists it."""

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

        self._engine = QtWidgets.QComboBox()
        for engine in ("auto", "vision", "rapidocr"):
            self._engine.addItem(engine, engine)
        self._select_code(self._engine, cfg.ocr_engine)
        form.addRow("OCR engine", self._engine)

        self._translate_engine = QtWidgets.QComboBox()
        for engine in ("google", "deepl", "offline"):
            self._translate_engine.addItem(engine, engine)
        self._select_code(self._translate_engine, cfg.translate_engine)
        form.addRow("Translation engine", self._translate_engine)

        self._deepl_key = QtWidgets.QLineEdit(cfg.deepl_api_key)
        self._deepl_key.setEchoMode(QtWidgets.QLineEdit.Password)
        self._deepl_key.setPlaceholderText("DeepL API key (only for the deepl engine)")
        form.addRow("DeepL API key", self._deepl_key)

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

        self._interval = QtWidgets.QSpinBox()
        self._interval.setRange(200, 10000)
        self._interval.setSingleStep(100)
        self._interval.setSuffix(" ms")
        self._interval.setValue(cfg.live_interval_ms)
        form.addRow("Live interval", self._interval)

        self._font = QtWidgets.QSpinBox()
        self._font.setRange(8, 72)
        self._font.setValue(cfg.overlay_font_pt)
        form.addRow("Overlay font (pt)", self._font)

        self._opacity = QtWidgets.QDoubleSpinBox()
        self._opacity.setRange(0.1, 1.0)
        self._opacity.setSingleStep(0.05)
        self._opacity.setValue(cfg.overlay_opacity)
        form.addRow("Overlay opacity", self._opacity)

        self._inplace = QtWidgets.QCheckBox("Replace original text in place (full screen)")
        self._inplace.setChecked(cfg.overlay_inplace)
        form.addRow("In-place", self._inplace)

        self._save_history = QtWidgets.QCheckBox("Save originals + translations to disk")
        self._save_history.setChecked(cfg.save_history)
        form.addRow("History", self._save_history)

        self._save_screenshots = QtWidgets.QCheckBox("Also save screenshots")
        self._save_screenshots.setChecked(cfg.save_screenshots)
        form.addRow("", self._save_screenshots)

        if sys.platform == "darwin":
            self._accessory = QtWidgets.QCheckBox("Hide Dock icon (menu-bar only) — needs relaunch")
            self._accessory.setChecked(cfg.accessory_mode)
            form.addRow("Dock", self._accessory)

        self._hk_translate = HotkeyEdit(cfg.hotkey_translate)
        self._hk_hold = HotkeyEdit(cfg.hotkey_hold)
        self._hk_reselect = HotkeyEdit(cfg.hotkey_reselect)
        self._hk_hide = HotkeyEdit(cfg.hotkey_hide)
        self._hk_live = HotkeyEdit(cfg.hotkey_live)
        form.addRow("Hotkey: translate region", self._hk_translate)
        form.addRow("Full screen: HOLD to show", self._hk_hold)
        form.addRow("Hotkey: reselect", self._hk_reselect)
        form.addRow("Hotkey: hide", self._hk_hide)
        form.addRow("Hotkey: live mode", self._hk_live)
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
        """A new Config reflecting the edits (region and anything else preserved)."""
        return replace(
            self._cfg,
            source=self._source.currentData() or self._cfg.source,
            target=self._target.currentData() or self._cfg.target,
            ocr_engine=self._engine.currentData() or self._cfg.ocr_engine,
            translate_engine=self._translate_engine.currentData() or self._cfg.translate_engine,
            deepl_api_key=self._deepl_key.text().strip(),
            live_interval_ms=self._interval.value(),
            overlay_font_pt=self._font.value(),
            overlay_opacity=round(self._opacity.value(), 2),
            overlay_inplace=self._inplace.isChecked(),
            save_history=self._save_history.isChecked(),
            save_screenshots=self._save_screenshots.isChecked(),
            accessory_mode=(
                self._accessory.isChecked()
                if hasattr(self, "_accessory") else self._cfg.accessory_mode
            ),
            hotkey_translate=self._hk_translate.hotkey() or self._cfg.hotkey_translate,
            hotkey_hold=self._hk_hold.hotkey() or self._cfg.hotkey_hold,
            hotkey_reselect=self._hk_reselect.hotkey() or self._cfg.hotkey_reselect,
            hotkey_hide=self._hk_hide.hotkey() or self._cfg.hotkey_hide,
            hotkey_live=self._hk_live.hotkey() or self._cfg.hotkey_live,
            suppress_hotkeys=self._suppress.isChecked(),
        )
