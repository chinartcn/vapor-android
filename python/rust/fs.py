"""rust.Vapor.fs —— 基础文件系统访问（逻辑包 / 私有目录 / 外部目录）。"""

from __future__ import annotations

import ctypes
import json

from ._abi import lib, VAPOR_OK, VAPOR_ERR_ARG, VAPOR_ERR_NOT_FOUND


def app_dir() -> str:
    v = lib().vapor_fs_app_dir()
    return v.decode() if isinstance(v, bytes) else ""


def private_dir() -> str:
    v = lib().vapor_fs_private_dir()
    return v.decode() if isinstance(v, bytes) else ""


def external_dir() -> str:
    v = lib().vapor_fs_external_dir()
    return v.decode() if isinstance(v, bytes) else ""


def exists(path: str) -> bool:
    return lib().vapor_fs_exists(path.encode()) != 0


def read(path: str) -> bytes:
    """从逻辑包/私有/外部目录读取文件，返回 bytes。失败抛 FileNotFoundError。"""
    buf = ctypes.POINTER(ctypes.c_uint8)()
    length = ctypes.c_int32(0)
    rc = lib().vapor_fs_read(path.encode(), ctypes.byref(buf), ctypes.byref(length))
    if rc != VAPOR_OK:
        raise FileNotFoundError(path)
    try:
        return ctypes.string_at(buf, length.value)
    finally:
        lib().vapor_fs_free(buf)


def read_text(path: str, encoding: str = "utf-8") -> str:
    return read(path).decode(encoding)


def write(path: str, data: bytes | str) -> int:
    if isinstance(data, str):
        data = data.encode()
    arr = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
    return lib().vapor_fs_write(path.encode(), arr, len(data))


def list_dir(path: str = "") -> list[dict]:
    cap = 65536
    out = ctypes.create_string_buffer(cap)
    rc = lib().vapor_fs_list(path.encode(), out, cap)
    if rc != VAPOR_OK:
        return []
    try:
        return json.loads(out.value.decode())
    except Exception:
        return []


def read_json(path: str) -> dict:
    return json.loads(read_text(path))
