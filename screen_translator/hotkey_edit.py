"""A click-to-record hotkey field, so users set hotkeys by pressing keys instead
of typing pynput syntax. Converts a Qt key event to a pynput hotkey string
(e.g. F6 -> "<f6>", Cmd+Shift+T -> "<cmd>+<shift>+t")."""

from __future__ import annotations

import sys

from PySide6 import QtWidgets
from PySide6.QtCore import Qt

_SPECIAL = {
    Qt.Key_Space: "<space>",
    Qt.Key_Tab: "<tab>",
    Qt.Key_Return: "<enter>",
    Qt.Key_Enter: "<enter>",
    Qt.Key_Backspace: "<backspace>",
    Qt.Key_Delete: "<delete>",
    Qt.Key_Up: "<up>",
    Qt.Key_Down: "<down>",
    Qt.Key_Left: "<left>",
    Qt.Key_Right: "<right>",
    Qt.Key_Home: "<home>",
    Qt.Key_End: "<end>",
    Qt.Key_PageUp: "<page_up>",
    Qt.Key_PageDown: "<page_down>",
}


def _key_token(key: int) -> "str | None":
    if Qt.Key_F1 <= key <= Qt.Key_F35:
        return "<f%d>" % (key - Qt.Key_F1 + 1)
    if Qt.Key_A <= key <= Qt.Key_Z:
        return chr(ord("a") + (key - Qt.Key_A))
    if Qt.Key_0 <= key <= Qt.Key_9:
        return chr(ord("0") + (key - Qt.Key_0))
    return _SPECIAL.get(key)


def qt_key_to_pynput(event) -> "str | None":
    """Build a pynput hotkey string from a Qt key event, or None if unsupported."""
    mods = event.modifiers()
    tokens = []
    # macOS: Qt swaps Ctrl/Meta — ControlModifier is the Command key, Meta is Control.
    if sys.platform == "darwin":
        if mods & Qt.ControlModifier:
            tokens.append("<cmd>")
        if mods & Qt.MetaModifier:
            tokens.append("<ctrl>")
    else:
        if mods & Qt.ControlModifier:
            tokens.append("<ctrl>")
        if mods & Qt.MetaModifier:
            tokens.append("<cmd>")
    if mods & Qt.AltModifier:
        tokens.append("<alt>")
    if mods & Qt.ShiftModifier:
        tokens.append("<shift>")

    token = _key_token(event.key())
    if token is None:
        return None
    tokens.append(token)
    return "+".join(tokens)


class HotkeyEdit(QtWidgets.QLineEdit):
    def __init__(self, value: str, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setCursor(Qt.PointingHandCursor)
        self._value = value
        self._recording = False
        self.setText(self._display(value))

    @staticmethod
    def _display(value: str) -> str:
        return value if value else "(none)"

    def hotkey(self) -> str:
        return self._value

    def mousePressEvent(self, event) -> None:
        self._start_recording()
        super().mousePressEvent(event)

    def _start_recording(self) -> None:
        self._recording = True
        self.setText("press a key…  (⌫ to clear, Esc to cancel)")
        self.setFocus()

    def _stop_recording(self) -> None:
        self._recording = False
        self.setText(self._display(self._value))

    def keyPressEvent(self, event) -> None:
        if not self._recording:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            return  # a bare modifier — wait for the actual key
        if key == Qt.Key_Escape:
            self._stop_recording()
            return
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            self._value = ""  # clear -> disables this hotkey
            self._recording = False
            self.setText(self._display(""))
            return
        token = qt_key_to_pynput(event)
        if token:
            self._value = token
            self._recording = False
            self.setText(token)

    def focusOutEvent(self, event) -> None:
        if self._recording:
            self._stop_recording()
        super().focusOutEvent(event)
