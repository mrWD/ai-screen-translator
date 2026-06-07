"""Global hotkeys via pynput, marshalled onto the Qt main thread through signals.

A SINGLE pynput Listener drives everything (running two listeners on macOS
crashes — two Quartz event taps plus a race in pyobjc's lazy import of
AXIsProcessTrusted). We feed each key event to:
  - a set of pynput HotKey objects for the chord actions (this is exactly how
    pynput's own GlobalHotKeys works internally), and
  - hold-key detection that emits hold_start on press and hold_end on release.

pynput callbacks fire on the listener thread; emitting a Qt Signal from there is
delivered as a queued (thread-safe) call on the main thread.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from pynput import keyboard


def _prewarm_trust() -> None:
    """Resolve HIServices.AXIsProcessTrusted once on the main thread so the
    listener thread doesn't race pyobjc's lazy import (KeyError / crash)."""
    for module in ("HIServices", "Quartz", "ApplicationServices"):
        try:
            mod = __import__(module)
            getattr(mod, "AXIsProcessTrusted")
            return
        except Exception:
            continue


class HotkeyManager(QObject):
    translate = Signal()
    fullscreen = Signal()
    reselect = Signal()
    hide = Signal()
    live = Signal()
    hold_start = Signal()
    hold_end = Signal()

    def __init__(
        self,
        translate_hk: str,
        fullscreen_hk: str,
        reselect_hk: str,
        hide_hk: str,
        live_hk: str,
        hold_hk: str,
    ) -> None:
        super().__init__()
        self._chord_specs = [
            (translate_hk, self.translate.emit),
            (fullscreen_hk, self.fullscreen.emit),
            (reselect_hk, self.reselect.emit),
            (hide_hk, self.hide.emit),
            (live_hk, self.live.emit),
        ]
        self._hold_hk = hold_hk
        self._listener: "keyboard.Listener | None" = None
        self._hotkeys: list = []
        self._hold_norm: set = set()
        self._hold_active = False

    def start(self) -> None:
        self._hold_active = False
        _prewarm_trust()

        self._hotkeys = []
        for spec, callback in self._chord_specs:
            try:
                self._hotkeys.append(keyboard.HotKey(keyboard.HotKey.parse(spec), callback))
            except Exception:
                pass  # skip an unparseable chord rather than break the whole listener

        try:
            self._hold_norm = {self._norm(k) for k in keyboard.HotKey.parse(self._hold_hk)}
        except Exception:
            self._hold_norm = set()

        listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener = listener  # assign before start so canonical() is available
        listener.start()

    def _on_press(self, key) -> None:
        canon = self._listener.canonical(key) if self._listener else key
        for hotkey in self._hotkeys:
            hotkey.press(canon)
        if not self._hold_active and self._norm(key) in self._hold_norm:
            self._hold_active = True
            self.hold_start.emit()

    def _on_release(self, key) -> None:
        canon = self._listener.canonical(key) if self._listener else key
        for hotkey in self._hotkeys:
            hotkey.release(canon)
        if self._hold_active and self._norm(key) in self._hold_norm:
            self._hold_active = False
            self.hold_end.emit()

    @staticmethod
    def _norm(key):
        """A representation that compares equal whether a key arrives as a Key
        enum (e.g. Key.f8), a parsed KeyCode (vk=100), or a character."""
        kc = key.value if isinstance(key, keyboard.Key) else key
        vk = getattr(kc, "vk", None)
        if vk is not None:
            return ("vk", vk)
        char = getattr(kc, "char", None)
        return ("char", char.lower()) if char else ("other", str(key))

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        self._hold_active = False
