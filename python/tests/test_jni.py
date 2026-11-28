"""JNI 桥接端到端验证：启动真实 JVM → 注入 VM → 通过稳定 C ABI 调用任意 JNI。

运行:
    VAPOR_NATIVE_LIB=$PWD/target/debug/libvapor.so \
    PYTHONPATH=$PWD/python python3 python/tests/test_jni.py
"""
from __future__ import annotations

import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rust.Vapor as V
from rust import _abi, jni

PASS = 0


def check(cond: bool, msg: str) -> None:
    global PASS
    assert cond, f"[FAIL] {msg}"
    PASS += 1
    print(f"[ok] {msg}")


def main() -> None:
    # 1) 启动 JVM 并注入 VM（模拟 Android NativeActivity 的自动注入）
    host_path = os.path.join(os.path.dirname(__file__), "libjni_host.so")
    host = ctypes.CDLL(host_path)
    host.host_start_jvm.restype = ctypes.c_void_p
    vm = host.host_start_jvm()
    check(vm is not None, "启动本机 JVM，取得 JavaVM*")

    jni.set_vm(vm)
    check(jni.available(), "JavaVM* 已注入 libvapor")
    check(jni.get_env() != 0, "取到当前线程 JNIEnv*（自动 attach）")

    # 2) 静态方法调用：java.lang.Math.max(int,int) 与 abs
    r = jni.call_static("java/lang/Math", "max", "(II)I", [jni.i(3), jni.i(7)])
    check(r == 7, f"call_static Math.max(3,7) = {r}")
    r = jni.call_static("java/lang/Math", "abs", "(I)I", [jni.i(-42)])
    check(r == 42, f"call_static Math.abs(-42) = {r}")

    # 2b) 浮点返回值不能丢精度：float 与 double
    r = jni.call_static("java/lang/Math", "abs", "(F)F", [jni.f(-3.5)])
    check(isinstance(r, float) and abs(r - 3.5) < 1e-6,
          f"call_static Math.abs(-3.5f) = {r}")
    r = jni.call_static("java/lang/Math", "sqrt", "(D)D", [jni.d(2.0)])
    check(abs(r - 1.4142135623730951) < 1e-9,
          f"call_static Math.sqrt(2.0) = {r}")

    # 3) 字符串：new_string → 静态方法 valueOf(Object) → get_string
    with jni.new_string("hello 世界") as s:
        v = jni.call_static(
            "java/lang/String", "valueOf", "(Ljava/lang/Object;)Ljava/lang/String;",
            args=[jni.obj(s)],
        )
        check(isinstance(v, jni.Ref), "String.valueOf 返回对象句柄")
        check(jni.get_string(v) == "hello 世界", f"get_string = {jni.get_string(v)}")
        jni.release(v)

    # 4) find_class + get_method + 实例调用：String.length()
    with jni.find_class("java/lang/String") as cls:
        mid = jni.get_method(cls, "length", "()I")
        check(mid != 0, "get_method 取得 String.length 的 jmethodID")
        with jni.new_string("12345") as s:
            n = jni.call_method(mid, s, "()I", ret="i")
            check(n == 5, f"call_method String.length() = {n}")

    # 5) new_object + 实例方法：StringBuilder("ab").append("cd").toString()
    with jni.new_object("java/lang/StringBuilder", "(Ljava/lang/String;)V",
                        [jni.string("ab")]) as sb:
        jni.call(sb, "append", "(Ljava/lang/String;)Ljava/lang/StringBuilder;",
                 [jni.string("cd")], ret="o")
        out = jni.call(sb, "toString", "()Ljava/lang/String;", ret="l")
        check(out == "abcd", f"StringBuilder.toString() = {out}")

    # 6) 数组：new int[3] → set → get → length
    with jni.new_array("i", 3) as arr:
        check(jni.array_length(arr) == 3, "new_array int[3] 长度正确")
        jni.set_array(arr, 0, 11)
        jni.set_array(arr, 2, 33)
        check(jni.get_array(arr, 0) == 11, "get_array[0] = 11")
        check(jni.get_array(arr, 2) == 33, "get_array[2] = 33")

    # 7) 静态字段：Integer.MAX_VALUE
    mv = jni.get_field(None, "MAX_VALUE", "I", static=True,
                       class_name="java/lang/Integer")
    check(mv == 2**31 - 1, f"get_static_field Integer.MAX_VALUE = {mv}")

    # 8) 异常路径：找不存在的类应报错而非崩溃
    try:
        jni.find_class("no/such/Class")
        check(False, "不存在的类应抛 JniError")
    except jni.JniError:
        check(True, "不存在的类正确抛 JniError（异常已清除）")
    check(not jni.exception_check(), "异常已清除，JNI 状态干净")

    # 9) 底层 C ABI 直接发 JSON 请求（证明 C 扩展走同一条路）
    lib = _abi.lib()
    req = b'{"op":"call_static","class":"java/lang/Math","name":"max","sig":"(II)I","args":[{"t":"i","v":10},{"t":"i","v":20}]}'
    out = ctypes.create_string_buffer(4096)
    rc = lib.vapor_jni_call(req, out, 4096)
    check(rc == 0, "底层 vapor_jni_call 返回 OK")
    import json
    res = json.loads(out.value)
    check(res.get("ok") and res.get("v") == 20, f"底层 C ABI JSON 结果 = {res}")

    print(f"\n[PASS] 全部 {PASS} 项 JNI 桥接验证通过")
    jni.detach()
    host.host_destroy_jvm()


if __name__ == "__main__":
    main()
