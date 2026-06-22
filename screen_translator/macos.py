"""macOS-only window tweaks via pyobjc.

Lets the overlay float across Spaces and above other apps' fullscreen windows —
this is what makes the translation appear over a GeForce Now window that is in
macOS native fullscreen. All calls are best-effort; callers wrap in try/except.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def make_overlay_join_all_spaces(win_id: int) -> None:
    """Set NSWindow collectionBehavior + level on the window backing `win_id`
    (the value returned by QWidget.winId(), i.e. an NSView pointer) so the overlay
    floats above other apps' native-fullscreen Spaces (e.g. a GeForce Now game)."""
    import objc
    from AppKit import (
        NSScreenSaverWindowLevel,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSWindowCollectionBehaviorStationary,
    )

    view = objc.objc_object(c_void_p=win_id)
    window = view.window()
    if window is None:
        _log.warning("overlay float tweak: NSWindow not ready for win_id=%s (skipped)", win_id)
        return
    behavior = (
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorStationary
    )
    window.setCollectionBehavior_(behavior)
    # Qt.Tool windows are NSPanel utility panels, which by DEFAULT hide whenever
    # another app is active. While a GeForce Now game owns the foreground/fullscreen
    # Space our app is inactive, so without this the overlay vanishes there and is
    # only visible back on the Desktop Space. Keep it on screen regardless.
    window.setHidesOnDeactivate_(False)

    # THE key bit for floating over another app's native-fullscreen Space: make the
    # panel NON-ACTIVATING. Otherwise ordering it front activates our app, and macOS
    # switches to our app's Space (Desktop) instead of drawing over the game. A
    # non-activating panel never brings the app forward, so the CanJoinAllSpaces
    # window appears on the *current* (game) Space in place.
    from AppKit import NSPanel
    try:
        from AppKit import NSWindowStyleMaskNonactivatingPanel as _NONACT
    except ImportError:  # older pyobjc
        from AppKit import NSNonactivatingPanelMask as _NONACT
    if window.isKindOfClass_(NSPanel):
        window.setStyleMask_(window.styleMask() | _NONACT)
        window.setFloatingPanel_(True)  # NB: this resets the level — set level AFTER
        window.setBecomesKeyOnlyIfNeeded_(True)
    # Set the level LAST: setFloatingPanel_ above drops it to NSFloatingWindowLevel
    # (3), which isn't above a fullscreen game. We need it high to sit on top.
    window.setLevel_(NSScreenSaverWindowLevel)
    _log.debug(
        "overlay float tweak applied (win_id=%s level=%s panel=%s)",
        win_id, int(window.level()), bool(window.isKindOfClass_(NSPanel)),
    )


def set_activation_policy(accessory: bool) -> None:
    """Accessory = menu-bar-only (no Dock icon, no app menu); Regular = normal.
    Effectively applied at launch; toggling later wants a relaunch."""
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSApplicationActivationPolicyRegular,
    )

    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory if accessory
        else NSApplicationActivationPolicyRegular
    )


def accessibility_trusted(prompt: bool = False) -> bool:
    """Whether this process is trusted for Accessibility — required for global
    hotkeys (the Quartz event tap only receives hardware key events when trusted;
    the *active* tap used for key suppression needs it especially). With
    prompt=True, macOS shows its 'grant Accessibility' dialog when not yet trusted.

    Best-effort: returns True if the check itself can't be performed, so a probe
    failure never blocks the app."""
    try:
        from ApplicationServices import (
            AXIsProcessTrusted,
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
    except Exception:
        return True
    try:
        if prompt:
            return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
        return bool(AXIsProcessTrusted())
    except Exception:
        return True
