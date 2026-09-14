"""Proteccion de credenciales con DPAPI en Windows."""

from __future__ import annotations

import base64
import ctypes
import os


def _protect_secret(value: str) -> str:
    raw = value.encode("utf-8")
    if os.name != "nt":
        return "plain:" + base64.b64encode(raw).decode("ascii")

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.c_uint32),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    input_buffer = ctypes.create_string_buffer(raw)
    input_blob = DataBlob(
        len(raw), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte))
    )
    output_blob = DataBlob()
    crypt32.CryptProtectData.restype = ctypes.c_bool
    ok = crypt32.CryptProtectData(
        ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
    return "dpapi:" + base64.b64encode(encrypted).decode("ascii")


def _unprotect_secret(value: str) -> str:
    if value.startswith("plain:"):
        return base64.b64decode(value[6:]).decode("utf-8")
    if not value.startswith("dpapi:"):
        raise ValueError("Formato de credencial no reconocido")
    if os.name != "nt":
        raise RuntimeError("La credencial DPAPI solo puede abrirse en Windows")

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.c_uint32),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    encrypted = base64.b64decode(value[6:])
    crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    input_buffer = ctypes.create_string_buffer(encrypted)
    input_blob = DataBlob(
        len(encrypted), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte))
    )
    output_blob = DataBlob()
    crypt32.CryptUnprotectData.restype = ctypes.c_bool
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        decrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
    return decrypted.decode("utf-8")
