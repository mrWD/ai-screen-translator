"""Global hotkeys via pynput, marshalled onto the Qt main thread through signals.

A SINGLE pynput Listener drives everything (running two listeners on macOS
crashes — two Quartz event taps plus a race in pyobjc's lazy import of
AXIsProcessTrusted). We feed each key event to:
  - a set of pynput HotKey objects for the chord actions (this is exactly how
    pynput's own GlobalHotKeys works internally), and
  - hold-key detection that emits hold_start on press and hold_end on release.

pynput callbacks fire on the listener thread; emitting a Qt Signal from there is
delivered as a queued (thread-safe) call on the main thread.

**Optional suppression** (`suppress=True`): swallow a hotkey's *normal* OS/app
action (e.g. F1 = Help) and run only ours. pynput has no portable "suppress this
one key", so we use the per-platform hooks of the SAME listener (a second
listener would crash macOS):
  - macOS `darwin_intercept`: pynput calls on_press FIRST, then the intercept, so
    on_press records whether to swallow and the intercept returns None to drop it.
    Needs Accessibility trust (the active event tap). F1/F2/media keys delivered as
    system events can't be caught here — use a plain function key.
  - Windows `win32_event_filter`: runs BEFORE on_press and suppression skips
    on_press, so the filter itself dispatches the action, then returns False.
Only **single, modifier-free** keys are suppressed (F-keys, the hold key); chords
like Cmd+Shift+T pass through untouched. Off by default — when off, no hook is
installed and behaviour is exactly as before.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QObject, QTimer, Signal
from pynput import keyboard

_log = logging.getLogger(__name__)

# Windows low-level keyboard messages (winuser.h) seen by win32_event_filter.
_WM_KEYDOWN, _WM_KEYUP, _WM_SYSKEYDOWN, _WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105

# macOS event-tap "you've been disabled" pseudo-events (CGEventType).
_TAP_DISABLED_TIMEOUT, _TAP_DISABLED_USER_INPUT = 0xFFFFFFFE, 0xFFFFFFFF


def _install_tap_capture_patch() -> None:
    """Make pynput stash its Quartz event tap on the listener instance so we can
    watchdog it. macOS disables a tap whose process was slow/unresponsive
    (kCGEventTapDisabledByTimeout) and pynput NEVER re-enables it (it only calls
    CGEventTapEnable once at startup) — so after one heavy translate the hotkeys go
    dead. We re-enable it from a timer (see HotkeyManager._check_tap)."""
    if sys.platform != "darwin":
        return
    try:
        from pynput._util import darwin as ud
    except Exception:
        return
    if getattr(ud.ListenerMixin, "_st_tap_capture", False):
        return
    original = ud.ListenerMixin._create_event_tap

    def _patched(self):
        tap = original(self)
        self._st_event_tap = tap  # read by the watchdog
        return tap

    ud.ListenerMixin._create_event_tap = _patched
    ud.ListenerMixin._st_tap_capture = True


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


def _patch_darwin_keycode_context() -> None:
    """Work around a pynput crash on macOS 26.

    pynput resolves the keyboard layout with the Carbon TSM/TIS APIs
    (`keycode_context()`) inside `Listener._run` — i.e. on its background listener
    thread. Recent macOS hard-asserts that these APIs run on the main thread
    (`dispatch_assert_queue` → SIGTRAP in `islGetInputSourceListWithAdditions`),
    which the active event tap used for suppression reliably trips. So resolve the
    layout ONCE here (call on the main thread) and hand the listener a cached
    context; its thread then only calls the thread-safe `UCKeyTranslate`, never TSM.

    Must be called on the main thread before starting the Listener. No-op off macOS
    or if pynput's internals change shape."""
    if sys.platform != "darwin":
        return
    try:
        import contextlib

        from pynput._util import darwin as util
        from pynput.keyboard import _darwin as kbd

        with util.keycode_context() as ctx:  # the real TSM calls, on THIS (main) thread
            cached = ctx
    except Exception:
        return  # leave pynput untouched — no worse than before

    @contextlib.contextmanager
    def _cached_keycode_context():
        yield cached

    kbd.keycode_context = _cached_keycode_context  # used by Listener._run's `with`


class HotkeyManager(QObject):
    hide = Signal()
    hold_start = Signal()
    hold_end = Signal()

    def __init__(
        self,
        hide_hk: str,
        hold_hk: str,
        suppress: bool = False,
    ) -> None:
        super().__init__()
        self._chord_specs = [
            (hide_hk, self._logged("hide", self.hide.emit)),
        ]
        self._hold_hk = hold_hk
        self._suppress_enabled = suppress
        self._listener: "keyboard.Listener | None" = None
        self._hotkeys: list = []
        self._hold_norm: set = set()
        self._hold_active = False
        # Suppression bookkeeping (populated in start() when enabled):
        self._suppress_norms: set = set()       # normalized keys to swallow
        self._suppress_press: dict = {}          # norm -> [callbacks] (Windows path)
        self._suppress_current = False           # set by on_press, read by darwin intercept
        self._win_down: set = set()              # Windows: keys currently held (repeat de-dup)
        self._tap_watchdog = QTimer()            # re-enables a macOS tap disabled under load
        self._tap_watchdog.setInterval(1500)
        self._tap_watchdog.timeout.connect(self._check_tap)

    @staticmethod
    def _logged(name: str, emit):
        def fire():
            _log.info("hotkey fired: %s", name)
            emit()
        return fire

    def start(self) -> None:
        self._hold_active = False
        self._suppress_current = False
        self._win_down = set()
        _prewarm_trust()
        _patch_darwin_keycode_context()  # must run on the main thread, before the Listener
        _install_tap_capture_patch()

        self._hotkeys = []
        for spec, callback in self._chord_specs:
            try:
                self._hotkeys.append(keyboard.HotKey(keyboard.HotKey.parse(spec), callback))
            except Exception:
                _log.warning("could not parse hotkey %r — skipping", spec)

        try:
            self._hold_norm = {self._norm(k) for k in keyboard.HotKey.parse(self._hold_hk)}
        except Exception:
            self._hold_norm = set()

        self._build_suppress_tables()

        kwargs = {"on_press": self._on_press, "on_release": self._on_release}
        if self._suppress_enabled and self._suppress_norms:
            if sys.platform == "darwin":
                kwargs["darwin_intercept"] = self._darwin_intercept
            elif sys.platform == "win32":
                kwargs["win32_event_filter"] = self._win32_filter
            # Other platforms: pynput offers no per-event filter; suppression is a
            # no-op there (the action still fires via on_press).
        tap_mode = "active(suppress)" if "darwin_intercept" in kwargs or "win32_event_filter" in kwargs else "listen-only"
        _log.info(
            "starting hotkey listener: chords=%d hold=%r suppress=%s tap=%s",
            len(self._hotkeys), self._hold_hk, self._suppress_enabled, tap_mode,
        )
        if self._suppress_norms:
            _log.info("suppressed keys (norms): %s", sorted(map(str, self._suppress_norms)))
        listener = keyboard.Listener(**kwargs)
        self._listener = listener  # assign before start so canonical() is available
        listener.start()
        if sys.platform == "darwin":
            self._tap_watchdog.start()

    def _check_tap(self) -> None:
        """If macOS disabled our event tap (slow callback / heavy load), re-enable
        it — otherwise no more key events arrive and every hotkey silently dies."""
        if sys.platform != "darwin" or self._listener is None:
            return
        tap = getattr(self._listener, "_st_event_tap", None)
        if tap is None:
            return
        try:
            from Quartz import CGEventTapEnable, CGEventTapIsEnabled

            if not CGEventTapIsEnabled(tap):
                CGEventTapEnable(tap, True)
                _log.warning("event tap was disabled by macOS — re-enabled it (hotkeys restored)")
        except Exception:
            pass

    def _build_suppress_tables(self) -> None:
        """Pre-compute which keys to swallow. Only single, modifier-free hotkeys
        qualify — suppressing the trigger of a chord would mean eating that key
        whenever it's typed, and modifier tracking isn't worth the risk."""
        self._suppress_norms = set()
        self._suppress_press = {}
        if not self._suppress_enabled:
            return
        for spec, callback in self._chord_specs:
            try:
                keys = keyboard.HotKey.parse(spec)
            except Exception:
                continue
            if len(keys) != 1:
                continue
            norm = self._norm(keys[0])
            self._suppress_norms.add(norm)
            self._suppress_press.setdefault(norm, []).append(callback)
        if len(self._hold_norm) == 1:  # a single-key hold is swallowed too
            self._suppress_norms |= self._hold_norm

    # NOTE: pynput wraps these callbacks so that ANY exception they raise STOPS
    # the whole listener (see _util.AbstractListener._emitter) — after which no
    # hotkey works at all. So every handler swallows its own errors: one odd key
    # event must never tear the listener down.
    def _on_press(self, key) -> None:
        try:
            canon = self._listener.canonical(key) if self._listener else key
            for hotkey in self._hotkeys:
                hotkey.press(canon)
            norm = self._norm(key)
            _log.debug("key press: %r norm=%s", key, norm)
            if not self._hold_active and norm in self._hold_norm:
                self._hold_active = True
                _log.info("hold key down -> show full screen")
                self.hold_start.emit()
            # Read by _darwin_intercept, which pynput calls right after this.
            self._suppress_current = norm in self._suppress_norms
        except Exception:
            self._suppress_current = False  # never propagate -> never kill the listener

    def _on_release(self, key) -> None:
        try:
            canon = self._listener.canonical(key) if self._listener else key
            for hotkey in self._hotkeys:
                hotkey.release(canon)
            norm = self._norm(key)
            if self._hold_active and norm in self._hold_norm:
                self._hold_active = False
                _log.info("hold key up -> hide full screen")
                self.hold_end.emit()
            self._suppress_current = norm in self._suppress_norms
        except Exception:
            self._suppress_current = False

    def _darwin_intercept(self, event_type, event):
        """macOS active event tap: return the event to pass it on, None to drop it.
        Runs immediately after on_press/on_release, which set _suppress_current."""
        try:
            if int(event_type) in (_TAP_DISABLED_TIMEOUT, _TAP_DISABLED_USER_INPUT):
                self._check_tap()  # macOS just disabled us — re-enable immediately
                return event
            suppress = self._suppress_current
            self._suppress_current = False  # reset so events without on_press pass through
            return None if suppress else event
        except Exception:
            return event  # on any error, let the key through rather than swallow it

    def _win32_filter(self, msg, data) -> bool:
        """Windows low-level hook filter. Returning False swallows the event AND
        skips on_press, so we dispatch the action here for suppressed keys."""
        try:
            norm = ("vk", data.vkCode)
            if norm not in self._suppress_norms:
                return True  # not ours — let the normal on_press path handle it
            if msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                if norm not in self._win_down:  # ignore auto-repeat while held
                    self._win_down.add(norm)
                    for callback in self._suppress_press.get(norm, []):
                        callback()
                    if norm in self._hold_norm and not self._hold_active:
                        self._hold_active = True
                        self.hold_start.emit()
            elif msg in (_WM_KEYUP, _WM_SYSKEYUP):
                self._win_down.discard(norm)
                if norm in self._hold_norm and self._hold_active:
                    self._hold_active = False
                    self.hold_end.emit()
            return False  # swallow: don't pass to the OS / focused app
        except Exception:
            return True  # on error, don't swallow — let the key work normally

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
        self._tap_watchdog.stop()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        self._hold_active = False
        self._suppress_current = False
        self._win_down = set()
