"""rust.Vapor.jni —— JNI 桥接的 Python 高层封装。

通过 vapor_jni_call(req_json, out_json, cap) 这一条稳定 C ABI 调用任意 JNI：
C/Python 扩展与 Python 逻辑包共用同一套桥接语义。对象用整数句柄持有，
需手动 close() 或用 with 语句释放（对应 DeleteGlobalRef）。

典型用法::

    from rust import jni

    cls = jni.find_class("java/lang/String")
    s = jni.new_string("hello 世界")
    n = jni.call_static("java/lang/String", "valueOf", "(Ljava/lang/Object;)Ljava/lang/String;",
                        args=[jni.obj(s)])
    print(jni.get_string(n))       # hello 世界
    jni.call_static("java/lang/System", "out", ...)  # 见 field API

    # 数学运算
    r = jni.call_static("java/lang/Math", "max", "(II)I", args=[jni.i(3), jni.i(7)])
    assert r == 7
"""

from __future__ import annotations

import ctypes
import json
from typing import Any

from . import _abi

_BUF_SIZE = 64 * 1024


def _call(req: dict) -> dict:
    lib = _abi.lib()
    req_b = json.dumps(req, ensure_ascii=False).encode("utf-8")
    out = ctypes.create_string_buffer(_BUF_SIZE)
    rc = lib.vapor_jni_call(req_b, out, _BUF_SIZE)
    if rc != _abi.VAPOR_OK:
        raise RuntimeError(f"vapor_jni_call 返回 {rc}")
    try:
        res = json.loads(out.value.decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"JNI 桥接返回非法 JSON: {out.value!r}") from e
    if not res.get("ok", False):
        raise JniError(res.get("error", "JNI 调用失败"))
    return res


class JniError(RuntimeError):
    """JNI 调用错误（类找不到、方法签名错误、异常等）。"""


class Ref:
    """跨调用持有的 JNI 对象/类/数组引用（全局引用）。

    用 ``with`` 自动释放，或显式调用 :meth:`close`。
    """

    __slots__ = ("handle", "_closed")

    def __init__(self, handle: int) -> None:
        self.handle = handle
        self._closed = False

    def close(self) -> None:
        if not self._closed and self.handle:
            _abi.lib().vapor_jni_release(self.handle)
            self._closed = True

    def __enter__(self) -> "Ref":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __int__(self) -> int:
        return self.handle

    def __repr__(self) -> str:
        return f"<jni.Ref {self.handle}{' closed' if self._closed else ''}>"


# ---- 参数构造辅助 ----
def z(v: bool) -> dict: return {"t": "z", "v": bool(v)}
def b(v: int) -> dict: return {"t": "b", "v": int(v)}
def c(v: int) -> dict: return {"t": "c", "v": int(v)}
def s(v: int) -> dict: return {"t": "s", "v": int(v)}
def i(v: int) -> dict: return {"t": "i", "v": int(v)}
def j(v: int) -> dict: return {"t": "j", "v": int(v)}
def f(v: float) -> dict: return {"t": "f", "v": float(v)}
def d(v: float) -> dict: return {"t": "d", "v": float(v)}
def string(v: str) -> dict: return {"t": "l", "v": str(v)}
def obj(ref: Ref | int) -> dict: return {"t": "o", "v": int(ref)}
def array(ref: Ref | int) -> dict: return {"t": "a", "v": int(ref)}


# ---- VM 管理 ----
def set_vm(vm_ptr: int) -> None:
    """注入 JavaVM*（一般由宿主自动完成，测试时可手动调用）。"""
    if _abi.lib().vapor_jni_set_vm(ctypes.c_void_p(vm_ptr)) != _abi.VAPOR_OK:
        raise JniError("注入 JavaVM* 失败")


def get_vm() -> int:
    return _abi.lib().vapor_jni_get_vm()


def get_env() -> int:
    env = _abi.lib().vapor_jni_get_env()
    if not env:
        raise JniError("取 JNIEnv* 失败：未注入 JavaVM* 或 attach 失败")
    return env


def detach() -> None:
    _abi.lib().vapor_jni_detach()


def available() -> bool:
    """JNI 桥接是否可用（已注入 JavaVM*）。"""
    return bool(get_vm())


# ---- 类 / 方法 ----
def find_class(name: str) -> Ref:
    """按 JNI 类名（斜杠分隔）查找类。"""
    res = _call({"op": "find_class", "class": name})
    return Ref(int(res["handle"]))


def get_method(cls: Ref | int, name: str, sig: str, static: bool = False) -> int:
    res = _call({
        "op": "get_static_method" if static else "get_method",
        "target": int(cls), "name": name, "sig": sig,
    })
    return int(res["handle"])


def call(target: Ref | int | None, name: str, sig: str, args: list[dict] | None = None,
         ret: str | None = None) -> Any:
    """调用实例方法。target 为对象句柄。"""
    req = {"op": "call", "name": name, "sig": sig, "args": args or []}
    if target is not None:
        req["target"] = int(target)
    if ret:
        req["ret"] = ret
    return _unwrap(_call(req))


def call_static(class_name: str, name: str, sig: str, args: list[dict] | None = None,
                ret: str | None = None) -> Any:
    """调用静态方法，class_name 为斜杠分隔的类名。"""
    req = {
        "op": "call_static", "class": class_name, "name": name, "sig": sig,
        "args": args or [],
    }
    if ret:
        req["ret"] = ret
    return _unwrap(_call(req))


def call_method(method: int, target: Ref | int | None, sig: str,
                args: list[dict] | None = None, ret: str | None = None,
                static: bool = False, class_name: str | None = None) -> Any:
    """用已获取的 jmethodID 调用（省去每次查方法）。"""
    req = {
        "op": "call_static" if static else "call", "method": method,
        "sig": sig, "args": args or [],
    }
    if target is not None:
        req["target"] = int(target)
    if class_name:
        req["class"] = class_name
    if ret:
        req["ret"] = ret
    return _unwrap(_call(req))


def new_object(class_name: str, sig: str = "()V", args: list[dict] | None = None) -> Ref:
    res = _call({"op": "new_object", "class": class_name, "sig": sig, "args": args or []})
    return Ref(int(res["handle"]))


def new_string(value: str) -> Ref:
    res = _call({"op": "new_string", "value": value})
    return Ref(int(res["handle"]))


def get_string(ref: Ref | int) -> str:
    res = _call({"op": "get_string", "target": int(ref)})
    return res.get("v") or ""


# ---- 数组 ----
def new_array(t: str, n: int, class_name: str | None = None) -> Ref:
    spec = {"t": t, "n": n}
    if class_name:
        spec["class"] = class_name
    res = _call({"op": "new_array", "value": spec})
    return Ref(int(res["handle"]))


def array_length(ref: Ref | int) -> int:
    return int(_call({"op": "array_length", "target": int(ref)})["v"])


def set_array(ref: Ref | int, index: int, value: Any) -> None:
    _call({"op": "set_array", "target": int(ref), "index": index, "value": value})


def get_array(ref: Ref | int, index: int) -> Any:
    res = _call({"op": "get_array", "target": int(ref), "index": index})
    if "handle" in res:
        return Ref(int(res["handle"]))
    return res.get("v")


# ---- 字段 / 异常 ----
def get_field(target: Ref | int, name: str, sig: str, static: bool = False,
              class_name: str | None = None) -> Any:
    req = {"op": "get_static_field" if static else "get_field", "name": name, "sig": sig}
    if static:
        req["class"] = class_name or ""
    else:
        req["target"] = int(target)
    return _unwrap(_call(req))


def set_field(target: Ref | int, name: str, sig: str, value: Any, static: bool = False,
              class_name: str | None = None) -> None:
    req = {"op": "set_static_field" if static else "set_field", "name": name,
           "sig": sig, "value": value}
    if static:
        req["class"] = class_name or ""
    else:
        req["target"] = int(target)
    _call(req)


def exception_check() -> bool:
    return bool(_call({"op": "exception_check"})["v"])


def exception_clear() -> None:
    _call({"op": "exception_clear"})


def release(ref: Ref | int) -> None:
    if isinstance(ref, Ref):
        ref.close()
    else:
        _abi.lib().vapor_jni_release(ref)


def _unwrap(res: dict) -> Any:
    """把桥接返回的 {t,v,handle} 转成 Python 原生值或 Ref。"""
    if "handle" in res:
        return Ref(int(res["handle"]))
    return res.get("v")
