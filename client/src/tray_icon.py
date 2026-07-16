"""Platform-specific tray icon helpers."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

import pystray

if sys.platform == "win32":
    from pystray._util import win32

    class TrayIcon(pystray.Icon):
        """Windows tray icon that opens the popup menu on both mouse buttons.

        pystray's default win32 backend runs the default menu item on left
        click and only shows the menu on right click. asobby wants the menu
        on either click instead.
        """

        def _on_notify(self, wparam, lparam):
            if self._menu_handle and lparam in (
                win32.WM_LBUTTONUP,
                win32.WM_RBUTTONUP,
            ):
                win32.SetForegroundWindow(self._hwnd)
                point = wintypes.POINT()
                win32.GetCursorPos(ctypes.byref(point))
                hmenu, descriptors = self._menu_handle
                index = win32.TrackPopupMenuEx(
                    hmenu,
                    win32.TPM_RIGHTALIGN
                    | win32.TPM_BOTTOMALIGN
                    | win32.TPM_RETURNCMD,
                    point.x,
                    point.y,
                    self._menu_hwnd,
                    None,
                )
                if index > 0:
                    descriptors[index - 1](self)
else:
    TrayIcon = pystray.Icon
