"""Vapor.sdk 在非 Android 环境下的安全降级测试。

    PYTHONPATH=python python3 python/tests/test_vapor_sdk.py
"""

from __future__ import annotations

import os
import sys

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor import sdk  # noqa: E402


def test_available_is_bool():
    assert isinstance(sdk.available(), bool)


def test_capabilities_dict():
    caps = sdk.capabilities()
    assert isinstance(caps, dict)


def test_bind_without_library_is_false_or_true():
    # 本机没有 libvapor.so 时应返回 False；有则 True。两种都合法，不能抛异常。
    assert isinstance(sdk.bind("definitely-not-a-library.so"), bool)


def test_unavailable_raises():
    if sdk.available():
        return  # 在 Android 上跑时跳过
    try:
        sdk.find_class("java/lang/String")
    except sdk.SdkUnavailable:
        pass
    else:
        raise AssertionError("不可用时应抛 SdkUnavailable")


def test_arg_builders():
    assert sdk.arg_int(3) == {"t": "i", "v": 3}
    assert sdk.arg_str("x") == {"t": "l", "v": "x"}
    assert sdk.arg_obj(7) == {"t": "o", "v": 7}
    assert sdk.arg_bool(True) == {"t": "z", "v": True}
    assert sdk.arg_array([1, 2]) == {"t": "a", "v": [1, 2]}


def test_high_level_classes_exist():
    for name in ("Toast", "Vibrator", "Clipboard", "Device", "Battery", "Intent", "Prefs", "AndroidLog"):
        assert hasattr(sdk, name), name


def test_permissions_constants():
    assert sdk.Permissions.CAMERA == "android.permission.CAMERA"
    assert sdk.Permissions.VIBRATE == "android.permission.VIBRATE"
    assert sdk.Permissions.POST_NOTIFICATIONS.startswith("android.permission.")


def test_request_permission_offline():
    from rust import Vapor as RV

    # 非 Android / 无原生库：应当快速返回 False，而不是抛异常或挂住
    assert RV.request_permission("android.permission.CAMERA", timeout=0.1) is False
    assert isinstance(RV.activity(), int)


def test_permission_check_requires_sdk():
    if sdk.available():
        return
    try:
        sdk.Permissions.check(sdk.Permissions.CAMERA)
    except sdk.SdkUnavailable:
        pass
    else:
        raise AssertionError("没有原生桥接时 check 应抛 SdkUnavailable")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"[ok] {fn.__name__}")
    print(f"\n{len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
