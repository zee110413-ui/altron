"""Where is a window on screen (physical pixels of its client area)?"""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor aware: real pixel coordinates
except Exception:
    user32.SetProcessDPIAware()


def client_rect(title):
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return None
    rc = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    return pt.x, pt.y, w - w % 2, h - h % 2   # even sizes for the encoder


def to_front(title):
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 9)                                    # SW_RESTORE
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)    # HWND_TOPMOST, no move/size
    return True


def untop(title):
    hwnd = user32.FindWindowW(None, title)
    if hwnd:
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)  # HWND_NOTOPMOST
