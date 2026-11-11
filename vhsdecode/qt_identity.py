"""Application identity for Qt windows.

Desktop environments (Cinnamon/Mint, GNOME, KDE, ...) match a running window
to its launcher/pinned icon through the window class (X11 ``WM_CLASS``) or the
desktop file name (Wayland ``app_id``).  Qt derives both from ``argv[0]``, and
in the self-contained builds that is ``decode.py`` inside a temporary AppImage
mount.  The window then matches no ``.desktop`` file, so the taskbar shows a
placeholder icon and the desktop generates a stray ``Decode.Py`` entry.

Calling :func:`apply_app_identity` right after the ``QApplication`` is created
pins the identity.  A self-contained build (AppImage or PyInstaller) always
uses ``vhs-decode``, which matches the bundled ``vhs-decode.desktop``
(``Icon=vhs-decode`` and ``StartupWMClass=vhs-decode``).

A pip/pipx install has no bundled desktop file, and each GUI there is usually
given its own launcher entry.  So the caller can name a per-tool reverse-DNS id
(``DECODE_LAUNCHER_IDENTITY``, ``HIFI_DECODE_IDENTITY``) to use outside a
bundle, so the decode launcher and the hifi-decode GUI each match their own
``.desktop`` file instead of both claiming ``vhs-decode``.
"""

import os
import sys

APP_IDENTITY = "vhs-decode"
DECODE_LAUNCHER_IDENTITY = "io.github.vhs-decode.decode-launcher"
HIFI_DECODE_IDENTITY = "io.github.vhs-decode.hifi-decode"


def _is_bundled():
    """True inside an AppImage (APPDIR) or a PyInstaller build (_MEIPASS)."""
    return bool(getattr(sys, "_MEIPASS", None)) or bool(os.environ.get("APPDIR"))


def resolve_app_identity(installed_identity=None):
    """The identity to use: always APP_IDENTITY in a bundle, else
    ``installed_identity`` when given, else APP_IDENTITY."""
    if installed_identity and not _is_bundled():
        return installed_identity
    return APP_IDENTITY


def apply_app_identity(app=None, installed_identity=None, display_name=None):
    """Set the application name / desktop file name on the running QApplication.

    ``installed_identity`` is used only outside a self-contained build (see the
    module docstring); ``display_name``, if given, sets the human-readable
    application name.

    Must be called after the QApplication exists and before any window is
    shown, because Qt reads the name when the native window is created.
    Never raises: a failure here must not stop the GUI from starting.
    """
    try:
        try:
            from PyQt6.QtCore import QCoreApplication
            from PyQt6.QtGui import QGuiApplication
        except ImportError:
            from PyQt5.QtCore import QCoreApplication
            from PyQt5.QtGui import QGuiApplication

        identity = resolve_app_identity(installed_identity)
        QCoreApplication.setApplicationName(identity)
        # Wayland app_id and the freedesktop desktop-file association.
        QGuiApplication.setDesktopFileName(identity)
        if display_name:
            QGuiApplication.setApplicationDisplayName(display_name)
    except Exception as exc:  # pragma: no cover - cosmetic only
        print(f"WARN: could not set application identity: {exc}")
