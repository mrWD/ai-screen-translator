"""Full-screen, click-through overlay that paints many translated text boxes in
place — each translation is drawn over its original text. Used by "Translate full
screen" and hold-to-translate.

Boxes are laid out once in show_blocks(): each grows to fit its translation at a
readable font (anchored at the original position), then overlapping boxes are
nudged down so they don't stack on top of each other."""

from __future__ import annotations

import sys

from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import QRect, Qt


class ScreenOverlay(QtWidgets.QWidget):
    _PAD_X = 8
    _PAD_Y = 4
    _GAP = 4

    def __init__(self, opacity: float = 0.9) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput  # click-through to whatever's underneath
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._opacity = max(0.0, min(1.0, opacity))
        self._laid: list[tuple] = []  # (box QRect, text, font px)
        # Realize the NSWindow now and mark it CanJoinAllSpaces, so the very first
        # show() draws over the current (game) Space instead of switching Spaces.
        self._apply_macos_behavior()

    def set_opacity(self, opacity: float) -> None:
        self._opacity = max(0.0, min(1.0, opacity))
        self.update()

    def show_blocks(self, blocks, screen: QtGui.QScreen) -> None:
        """`blocks` are (screen-logical QRect, translated text). Positions are mapped
        into this window, which is sized to the given screen."""
        geom = screen.geometry()
        self.setGeometry(geom)
        local = [
            (QRect(r.x() - geom.x(), r.y() - geom.y(), r.width(), r.height()), text)
            for r, text in blocks
        ]
        self._laid = self._layout(local, geom.width(), geom.height())
        self.update()
        # Set CanJoinAllSpaces BEFORE showing: otherwise show() binds the window to
        # our home (Desktop) Space and pulls that Space forward instead of drawing
        # over the game's fullscreen Space. Re-assert after show too (Qt re-runs its
        # own window setup at show time and can drop the tweak).
        self._apply_macos_behavior()
        self.show()
        self.raise_()
        self._apply_macos_behavior()

    # ---- layout ----
    def _layout(self, blocks, screen_w: int, screen_h: int):
        placed: list[QRect] = []
        laid = []
        for rect, text in sorted(blocks, key=lambda b: (b[0].y(), b[0].x())):
            font_px = max(13, min(int(rect.height() * 0.72), 36))
            box = self._grow_box(text, rect, font_px, screen_w, screen_h)
            box = self._avoid_overlap(box, placed, screen_h)
            placed.append(box)
            laid.append((box, text, font_px))
        return laid

    def _grow_box(self, text, rect, font_px, screen_w, screen_h) -> QRect:
        font = QtGui.QFont()
        font.setPixelSize(font_px)
        metrics = QtGui.QFontMetrics(font)
        x, y = rect.x(), rect.y()
        # let the box grow past the original width up to ~half the screen
        desired_w = max(rect.width(), min(int(rect.width() * 2.2) + 60, int(screen_w * 0.5)))
        text_w = max(60, desired_w - 2 * self._PAD_X)
        bounds = metrics.boundingRect(0, 0, text_w, 100000, Qt.TextWordWrap, text)
        box_w = min(max(rect.width(), bounds.width() + 2 * self._PAD_X), screen_w - 8)
        box_h = max(rect.height(), bounds.height() + 2 * self._PAD_Y)
        # if it would run off the right/bottom, slide it back on-screen instead of
        # squeezing the text into a sliver near the edge
        if x + box_w > screen_w - 4:
            x = max(4, screen_w - 4 - box_w)
        if y + box_h > screen_h - 4:
            y = max(4, screen_h - 4 - box_h)
        return QRect(x, y, box_w, box_h)

    def _avoid_overlap(self, box: QRect, placed, screen_h: int) -> QRect:
        moved = QRect(box)
        for _ in range(60):  # bounded: nudge below the box we collide with
            collision = next((p for p in placed if moved.intersects(p)), None)
            if collision is None:
                break
            new_y = collision.bottom() + self._GAP
            if new_y + moved.height() > screen_h - 4:
                break  # no room below — accept the overlap rather than run off-screen
            moved.moveTop(new_y)
        return moved

    # ---- painting ----
    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing)
        font = painter.font()
        for box, text, font_px in self._laid:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QtGui.QColor(18, 18, 22, int(self._opacity * 255)))
            painter.drawRoundedRect(box.adjusted(-2, -2, 2, 2), 5, 5)
            text_box = box.adjusted(self._PAD_X, self._PAD_Y, -self._PAD_X, -self._PAD_Y)
            font.setPixelSize(font_px)
            painter.setFont(font)
            painter.setPen(QtGui.QColor("white"))
            painter.drawText(text_box, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, text)

    def _apply_macos_behavior(self) -> None:
        if sys.platform != "darwin":
            return
        if QtGui.QGuiApplication.platformName() != "cocoa":
            return
        wid = int(self.winId())
        if not wid:
            return
        # Re-apply on every show: Qt re-runs its own window setup across hide/show
        # cycles and can drop our collectionBehavior / hidesOnDeactivate tweaks.
        try:
            from .macos import make_overlay_join_all_spaces

            make_overlay_join_all_spaces(wid)
        except Exception:
            pass
