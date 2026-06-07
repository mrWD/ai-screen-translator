"""Settings dialog: edit languages, OCR engine, live interval, overlay style and
hotkeys. Returns a new Config; the app applies and persists it."""

from __future__ import annotations

import sys
from dataclasses import replace

from PySide6 import QtWidgets
from PySide6.QtCore import Qt

from . import languages
from .config import Config
from .hotkey_edit import HotkeyEdit


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
        self._hk_fullscreen = HotkeyEdit(cfg.hotkey_fullscreen)
        self._hk_hold = HotkeyEdit(cfg.hotkey_hold)
        self._hk_reselect = HotkeyEdit(cfg.hotkey_reselect)
        self._hk_hide = HotkeyEdit(cfg.hotkey_hide)
        self._hk_live = HotkeyEdit(cfg.hotkey_live)
        form.addRow("Hotkey: translate region", self._hk_translate)
        form.addRow("Hotkey: full screen", self._hk_fullscreen)
        form.addRow("Hold to translate screen", self._hk_hold)
        form.addRow("Hotkey: reselect", self._hk_reselect)
        form.addRow("Hotkey: hide", self._hk_hide)
        form.addRow("Hotkey: live mode", self._hk_live)
        hint = QtWidgets.QLabel("Click a field and press the key(s). Single keys like F6 work.")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("", hint)

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
            hotkey_fullscreen=self._hk_fullscreen.hotkey() or self._cfg.hotkey_fullscreen,
            hotkey_hold=self._hk_hold.hotkey() or self._cfg.hotkey_hold,
            hotkey_reselect=self._hk_reselect.hotkey() or self._cfg.hotkey_reselect,
            hotkey_hide=self._hk_hide.hotkey() or self._cfg.hotkey_hide,
            hotkey_live=self._hk_live.hotkey() or self._cfg.hotkey_live,
        )
