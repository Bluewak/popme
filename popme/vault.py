"""앱 비밀번호 보관: Windows 자격 증명 관리자 (제어판 > 자격 증명 관리자 > Windows 자격 증명 > POPME/…).

Windows가 내 계정으로 암호화해 두므로 config.toml을 복사하거나 공유해도 비밀번호는 따라가지 않는다.
"""
import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger(__name__)
PREFIX = "POPME/"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
MAX_BLOB = 2560  # CRED_MAX_CREDENTIAL_BLOB_SIZE


class CREDENTIALW(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


adv = ctypes.WinDLL("advapi32", use_last_error=True)
adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
adv.CredWriteW.restype = wintypes.BOOL
adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
adv.CredReadW.restype = wintypes.BOOL
adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
adv.CredDeleteW.restype = wintypes.BOOL
adv.CredFree.argtypes = [ctypes.c_void_p]
adv.CredFree.restype = None


def put(name, value):
    """저장 → 성공하면 True. 못 하면(잠긴 세션 등) False — 부르는 쪽이 평문을 그대로 둔다."""
    blob = value.encode("utf-8")
    if len(blob) > MAX_BLOB:
        return False
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIALW(Type=CRED_TYPE_GENERIC, TargetName=PREFIX + name, CredentialBlobSize=len(blob),
                       CredentialBlob=buf, Persist=CRED_PERSIST_LOCAL_MACHINE, UserName="POPME")
    if adv.CredWriteW(ctypes.byref(cred), 0):
        return True
    log.warning("자격 증명 저장 실패 (%s): 오류 %s", name, ctypes.get_last_error())
    return False


def get(name):
    """저장된 값, 없으면 ""."""
    ptr = ctypes.POINTER(CREDENTIALW)()
    if not adv.CredReadW(PREFIX + name, CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        return ""
    try:
        c = ptr.contents
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-8")
    finally:
        adv.CredFree(ptr)


def delete(name):
    return bool(adv.CredDeleteW(PREFIX + name, CRED_TYPE_GENERIC, 0))
