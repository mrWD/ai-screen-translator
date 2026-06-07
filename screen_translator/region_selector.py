"""Fullscreen rubber-band region selector — drag to choose the area to translate
(e.g. the subtitle/dialogue box in the game window)."""

from __future__ import annotations

from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import QPoint, QRect, Qt, Signal


class RegionSelector(QtWidgets.QWidget):
    selected = Signal(QRect, float)  # global logical rect, devicePixelRatio
    cancelled = Signal()
    closed = Signal()  # fires whenever the selector hides, for any reason

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self._origin = QPoint()
        self._rubber = QRect()
        self._dragging = False
        self._screen = None

    def start(self) -> None:
        screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        self._screen = screen
        self._rubber = QRect()
        # A plain top-most frameless overlay rather than showFullScreen(), which
        # on macOS triggers a native fullscreen-Space transition (slow, its own
        # Space). mapToGlobal() still gives correct global coords either way.
        self.setGeometry(screen.geometry())
        self.show()
        self.raise_()
        self.activateWindow()

    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 90))
        if not self._rubber.isNull():
            # punch a clear hole so the user sees exactly what will be captured
            painter.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
            painter.fillRect(self._rubber, Qt.transparent)
            painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 200, 255), 2))
            painter.drawRect(self._rubber)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._origin = event.position().toPoint()
            self._rubber = QRect(self._origin, self._origin)
            self._dragging = True
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._rubber = QRect(self._origin, event.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            rect = self._rubber.normalized()
            self.hide()
            if rect.width() >= 5 and rect.height() >= 5:
                global_rect = QRect(self.mapToGlobal(rect.topLeft()), rect.size())
                self.selected.emit(global_rect, self._screen.devicePixelRatio())
            else:
                self.cancelled.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
            self.cancelled.emit()

    def hideEvent(self, event) -> None:
        # Any dismissal path (select, cancel, Esc, or being hidden some other way)
        # ends up here, so listeners can reliably undo per-selection setup.
        self.closed.emit()
        super().hideEvent(event)
