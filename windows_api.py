"""Finds the open windows on your screen (Windows only) so she can stand on them."""
import os
import sys
from dataclasses import dataclass


@dataclass
class Win:
    hwnd: int
    rect: tuple  # left, top, right, bottom in physical pixels


if sys.platform != "win32":

    def visible_windows():
        return []

else:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi

    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    DWMWA_CLOAKED = 14
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    SKIP_CLASSES = {
        "Progman",
        "WorkerW",
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
        "Windows.UI.Core.CoreWindow",
    }
    MY_PID = os.getpid()

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    dwmapi.DwmGetWindowAttribute.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]

    def visible_windows():
        """Visible, normal windows from the top of the stack to the bottom."""
        found = []

        def callback(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                    return True
                if user32.GetWindowTextLengthW(hwnd) == 0:
                    return True
                if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                    return True

                cloaked = ctypes.c_int(0)
                dwmapi.DwmGetWindowAttribute(
                    hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), 4
                )
                if cloaked.value:
                    return True

                name = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, name, 256)
                if name.value in SKIP_CLASSES:
                    return True

                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == MY_PID:
                    return True

                rect = wintypes.RECT()
                hr = dwmapi.DwmGetWindowAttribute(
                    hwnd,
                    DWMWA_EXTENDED_FRAME_BOUNDS,
                    ctypes.byref(rect),
                    ctypes.sizeof(rect),
                )
                if hr != 0:
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                found.append(
                    Win(int(hwnd or 0), (rect.left, rect.top, rect.right, rect.bottom))
                )
            except Exception:
                pass
            return True

        user32.EnumWindows(EnumWindowsProc(callback), 0)
        return found
