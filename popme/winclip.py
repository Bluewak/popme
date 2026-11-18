"""Windows 클립보드: 앱 암호·캘린더 주소를 「붙여넣기」 없이 꺼내 오기 (설정 창)."""
from popme.setup_data import CLIP


def _clipboard(clear=False):
    """Windows 클립보드 글자 (clear=True면 비우기만). 못 열면 ""."""
    import ctypes
    from ctypes import wintypes
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u32.GetClipboardData.restype = wintypes.HANDLE
    k32.GlobalLock.argtypes = k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalLock.restype = ctypes.c_void_p
    if not u32.OpenClipboard(None):
        return ""
    try:
        if clear:
            u32.EmptyClipboard()
            return ""
        h = u32.GetClipboardData(13)  # CF_UNICODETEXT
        p = k32.GlobalLock(h) if h else None
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def take_clipboard(kind, read=_clipboard):
    """클립보드가 CLIP[kind] 모양 하나뿐이면 꺼내고(정리해서) 클립보드를 비운다. 아니면 "" — 다른 내용은 화면에 넘기지 않는다."""
    pattern, tidy = CLIP[kind]
    text = read().strip()
    if not pattern.fullmatch(text):
        return ""
    if read().strip() == text:  # 그 사이 다른 걸 복사했으면 건드리지 않는다
        read(clear=True)
    return tidy(text)
