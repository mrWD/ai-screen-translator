"""macOS-only window tweaks via pyobjc.

Lets the overlay float across Spaces and above other apps' fullscreen windows —
this is what makes the translation appear over a GeForce Now window that is in
macOS native fullscreen. All calls are best-effort; callers wrap in try/except.
"""

from __future__ import annotations


def make_overlay_join_all_spaces(win_id: int) -> None:
    """Set NSWindow collectionBehavior + level on the window backing `win_id`
    (the value returned by QWidget.winId(), i.e. an NSView pointer)."""
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
        return
    behavior = (
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorStationary
    )
    window.setCollectionBehavior_(behavior)
    window.setLevel_(NSScreenSaverWindowLevel)


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


def activate_app() -> None:
    """Force the process foreground so a frameless overlay can become key window
    (the region selector needs keyboard focus, which Accessory apps don't get by
    default)."""
    from AppKit import NSApplication

    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
