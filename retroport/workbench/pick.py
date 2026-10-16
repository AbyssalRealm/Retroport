"""Windows' own folder and file pickers, for the page's Browse buttons.

A web page can't ask for a folder's path, so the server - on the same machine - shows the picker
(the Explorer-style IFileOpenDialog, through ctypes: nothing to install) over the window in front
(the workbench's, as its Browse button was just clicked) and returns what was chosen.
"""

from __future__ import annotations

import ctypes
import os
import threading
from ctypes import POINTER, byref, c_uint, c_void_p, c_wchar_p

_one = threading.Lock()                     # one picker at a time

FOS_PICKFOLDERS, FOS_FORCEFILESYSTEM, FOS_FILEMUSTEXIST, FOS_PATHMUSTEXIST = 0x20, 0x40, 0x1000, 0x800
SIGDN_FILESYSPATH = 0x80058000
ERROR_CANCELLED = 0x800704C7


class GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    def __init__(self, text: str):
        super().__init__()
        ctypes.oledll.ole32.CLSIDFromString(c_wchar_p(text), byref(self))


class COMDLG_FILTERSPEC(ctypes.Structure):
    _fields_ = [("name", c_wchar_p), ("spec", c_wchar_p)]


def _call(obj: c_void_p, index: int, argtypes: tuple = (), *args, restype=ctypes.HRESULT):
    """Method ``index`` of a COM object's vtable (an HRESULT failure raises OSError)."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[index])(obj, *args)


def _release(obj: c_void_p) -> None:
    _call(obj, 2, restype=ctypes.c_ulong)


def available() -> bool:
    return os.name == "nt"


def pick(folder: bool = True, title: str = "", start: str = "", patterns: list[tuple[str, str]] | None = None) -> str:
    """The folder (or file) chosen, or "" when the picker was closed."""
    if not available():
        raise RuntimeError("the folder picker is Windows only - type the path instead")
    with _one:
        ole32 = ctypes.oledll.ole32
        ole32.CoInitializeEx(None, 2)                    # apartment-threaded, as the dialog wants
        dialog = c_void_p()
        try:
            ole32.CoCreateInstance(byref(GUID("{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}")), None, 1,
                                   byref(GUID("{D57C7288-D4AD-4768-BE02-9D969532D960}")), byref(dialog))
            opts = c_uint()
            _call(dialog, 10, (POINTER(c_uint),), byref(opts))                   # GetOptions
            want = opts.value | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | (FOS_PICKFOLDERS if folder else FOS_FILEMUSTEXIST)
            _call(dialog, 9, (c_uint,), want)                                     # SetOptions
            if title:
                _call(dialog, 17, (c_wchar_p,), title)                            # SetTitle
            if not folder and patterns:
                specs = (COMDLG_FILTERSPEC * len(patterns))(*[COMDLG_FILTERSPEC(n, p) for n, p in patterns])
                _call(dialog, 4, (c_uint, POINTER(COMDLG_FILTERSPEC)), len(patterns), specs)   # SetFileTypes
            here = start if start and os.path.isdir(start) else os.path.dirname(start) if start else ""
            if here and os.path.isdir(here):
                item = c_void_p()
                try:
                    ctypes.windll.shell32.SHCreateItemFromParsingName(
                        c_wchar_p(here), None, byref(GUID("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")), byref(item))
                    _call(dialog, 12, (c_void_p,), item)                          # SetFolder
                except OSError:
                    pass                                     # a folder it can't open: start where it likes
                finally:
                    if item:
                        _release(item)
            owner = ctypes.windll.user32.GetForegroundWindow()
            try:
                _call(dialog, 3, (c_void_p,), owner)                              # Show, over the window in front
            except OSError as e:
                if (e.winerror or 0) & 0xFFFFFFFF == ERROR_CANCELLED:
                    return ""
                raise
            result = c_void_p()
            _call(dialog, 20, (POINTER(c_void_p),), byref(result))                # GetResult -> IShellItem
            try:
                path = c_wchar_p()
                _call(result, 5, (c_uint, POINTER(c_wchar_p)), SIGDN_FILESYSPATH, byref(path))  # GetDisplayName
                chosen = path.value or ""
                ctypes.windll.ole32.CoTaskMemFree(path)
                return chosen
            finally:
                _release(result)
        finally:
            if dialog:
                _release(dialog)
            ole32.CoUninitialize()
