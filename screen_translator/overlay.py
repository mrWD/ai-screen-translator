"""Translucent, always-on-top, click-through panel that shows the translation
just below (or above) the original text. Click-through (Qt.WindowTransparentForInput)
means mouse/keyboard pass straight to the game underneath.

The panel is anchored ADJACENT to the captured region, never over it, so live
mode can keep re-capturing the same region without grabbing our own translation
(which would create an OCR feedback loop) and the original text stays visible.
"""

from __future__ import annotations

import sys

from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import QPoint, QRect, Qt

_GAP = 8  # px between the captured region and the panel


class Overlay(QtWidgets.QWidget):
    def __init__(self, font_pt: int = 18, opacity: float = 0.85) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput  # click-through to the game
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)  # don't steal focus
        self._opacity = max(0.0, min(1.0, opacity))
        self._font_pt = font_pt
        self._radius = 10

        self._label = QtWidgets.QLabel(self)
        self._label.setWordWrap(True)
        self._label.setTextFormat(Qt.PlainText)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)
        self._restyle()
        # Realize the NSWindow now and mark it CanJoinAllSpaces, so the first show()
        # floats over the current Space instead of switching to our home Space.
        self._apply_macos_behavior()

    # ---- styling ----
    def _restyle(self) -> None:
        self._label.setStyleSheet(
            "color: white;"
            f"font-size: {self._font_pt}pt;"
            "font-family: -apple-system, 'Segoe UI', system-ui, sans-serif;"
            "padding: 10px;"
        )

    def set_style(self, font_pt: int, opacity: float) -> None:
        self._font_pt = font_pt
        self._opacity = max(0.0, min(1.0, opacity))
        self._restyle()
        self.update()

    # ---- painting ----
    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setBrush(QtGui.QColor(20, 20, 24, int(self._opacity * 255)))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(self.rect(), self._radius, self._radius)

    # ---- showing / updating ----
    def show_text(self, text: str, region: QRect) -> None:
        """Show/update the panel for a capture region. Idempotent — calling it
        again (e.g. each live tick) updates in place without flicker."""
        self._label.setText(text or "—")
        self._label.setFixedWidth(max(220, region.width()))
        self.adjustSize()
        self.move(self._anchor(region))
        # Apply CanJoinAllSpaces BEFORE show() so showing the panel doesn't switch
        # macOS to our home Space (winId() realizes the NSWindow without showing it).
        # Re-assert after show too, since Qt's show-time setup can drop the tweak.
        self._apply_macos_behavior()
        self.show()
        self.raise_()
        self._apply_macos_behavior()

    def _anchor(self, region: QRect) -> QPoint:
        """Place the panel just below the region, or above it if there's no room,
        clamped to the screen the region sits on."""
        screen = QtGui.QGuiApplication.screenAt(region.center())
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        w, h = self.width(), self.height()

        x = max(avail.left(), min(region.left(), avail.right() - w + 1))
        below = region.bottom() + _GAP
        if below + h <= avail.bottom():
            y = below
        else:
            above = region.top() - _GAP - h
            y = above if above >= avail.top() else below  # fall back below if cramped
        return QPoint(x, y)

    def _apply_macos_behavior(self) -> None:
        if sys.platform != "darwin":
            return
        # Only the real Cocoa platform exposes a valid NSView via winId(); under
        # the offscreen/test platform the handle is bogus and would crash pyobjc.
        if QtGui.QGuiApplication.platformName() != "cocoa":
            return
        wid = int(self.winId())
        if not wid:
            return
        # Re-apply on every show: Qt re-runs its window setup across hide/show
        # cycles and can drop our collectionBehavior / hidesOnDeactivate tweaks,
        # which would make the panel lose its float-over-fullscreen.
        try:
            from .macos import make_overlay_join_all_spaces

            make_overlay_join_all_spaces(wid)
        except Exception:
            pass  # best-effort; overlay still works on non-fullscreen content
