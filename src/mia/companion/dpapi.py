"""Windows CurrentUser DPAPI protection for companion device secrets.

The private Ed25519 key and short-lived device token are protected for the current Windows
user. This module deliberately does not fall back to plaintext storage on other platforms.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


class CompanionSecretProtectionError(RuntimeError):
    """A companion secret could not be protected or unprotected."""


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


class WindowsDPAPI:
    """CryptProtectData/CryptUnprotectData using the current Windows user profile."""

    ENTROPY = b"Mia.Companion.Identity.v1"

    @staticmethod
    def _apis():
        if sys.platform != "win32":
            raise CompanionSecretProtectionError(
                "companion device secrets require Windows DPAPI; plaintext fallback is disabled"
            )
        crypt32 = ctypes.WinDLL("Crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("Kernel32", use_last_error=True)
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.POINTER(_DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        return crypt32, kernel32

    def protect(self, value: bytes) -> bytes:
        if not value:
            raise CompanionSecretProtectionError("refusing to protect an empty secret")
        crypt32, kernel32 = self._apis()
        source, source_buffer = _blob(value)
        entropy, entropy_buffer = _blob(self.ENTROPY)
        result = _DataBlob()
        try:
            ok = crypt32.CryptProtectData(
                ctypes.byref(source), "Mia companion device secret",
                ctypes.byref(entropy), None, None, 0x1, ctypes.byref(result),
            )
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
            return ctypes.string_at(result.pbData, result.cbData)
        except OSError as exc:
            raise CompanionSecretProtectionError("Windows DPAPI could not protect the companion secret") from exc
        finally:
            if result.pbData:
                kernel32.LocalFree(result.pbData)
            # Keep input buffers alive until the native calls have completed.
            _ = source_buffer, entropy_buffer

    def unprotect(self, value: bytes) -> bytes:
        if not value:
            raise CompanionSecretProtectionError("protected companion secret is empty")
        crypt32, kernel32 = self._apis()
        source, source_buffer = _blob(value)
        entropy, entropy_buffer = _blob(self.ENTROPY)
        result = _DataBlob()
        try:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(source), None, ctypes.byref(entropy),
                None, None, 0x1, ctypes.byref(result),
            )
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
            return ctypes.string_at(result.pbData, result.cbData)
        except OSError as exc:
            raise CompanionSecretProtectionError(
                "Windows DPAPI could not unlock the companion secret for this user"
            ) from exc
        finally:
            if result.pbData:
                kernel32.LocalFree(result.pbData)
            _ = source_buffer, entropy_buffer
