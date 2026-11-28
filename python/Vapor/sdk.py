"""Vapor.sdk —— 通过稳定 C ABI 调 JNI 的 Android SDK 封装。

分层：
    1. Vapor._sdk（Rust）：dlopen libvapor.so，拿到 vapor_jni_call 等符号
    2. 本模块：JSON 请求/响应 + 面向对象的 Android API（Toast / 振动 / 剪贴板 ...）

不在 Android 上时所有调用都会抛 SdkUnavailable；可以先 available() 判断。

    from Vapor.sdk import Toast, Vibrator, Clipboard
    Toast.show("保存成功")
    Vibrator.vibrate(30)
"""

from __future__ import annotations

import json

from . import _native

_sdk = _native.sdk

__all__ = [
    "SdkUnavailable", "available", "capabilities", "bind", "call", "find_class",
    "call_static", "call_method", "new_object", "get_string", "release",
    "Toast", "Vibrator", "Clipboard", "Device", "Battery", "Intent", "Prefs",
    "Context", "AndroidLog", "PackageInfo",
    "Permissions", "Location", "Telephony", "Notifications",
]


class SdkUnavailable(RuntimeError):
    """没有可用的原生桥接（非 Android 或 libvapor.so 未加载）。"""


def _require():
    if _sdk is None:
        raise SdkUnavailable("Vapor._sdk 原生扩展未安装（请用 maturin 构建 vapor-sdk）")
    return _sdk


def available() -> bool:
    """当前环境能否调用 JNI（Android + libvapor.so 已加载）。"""
    if _sdk is None:
        return False
    try:
        return bool(_sdk.available())
    except Exception:
        return False


def capabilities() -> dict:
    if _sdk is None:
        return {}
    try:
        return dict(_sdk.capabilities())
    except Exception:
        return {}


def bind(path=None) -> bool:
    """显式加载 libvapor.so（默认按 VAPOR_NATIVE_LIB / libvapor.so 查找）。"""
    if _sdk is None:
        return False
    return bool(_sdk.open(path))


def set_vm(addr: int) -> bool:
    return bool(_require().set_vm(int(addr)))


def get_env() -> int:
    return int(_require().get_env())


def release(handle) -> None:
    try:
        _require().release(int(handle))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 原始 JSON 协议
# ---------------------------------------------------------------------------

def call(req) -> dict:
    """执行一条 JNI 请求（dict 或 JSON 字符串），返回解析后的响应 dict。"""
    mod = _require()
    payload = req if isinstance(req, str) else json.dumps(req, ensure_ascii=False)
    raw = mod.call(payload)
    if not raw:
        return {"ok": False, "error": "空响应"}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"响应不是 JSON: {raw[:200]}"}


def _clean(req: dict) -> dict:
    return {k: v for k, v in req.items() if v is not None}


def _call(**fields):
    res = call(_clean(fields))
    if not res.get("ok"):
        raise SdkUnavailable(res.get("error") or "JNI 调用失败")
    return res


# ---- 参数构造 ----

def arg_int(v):
    return {"t": "i", "v": int(v)}


def arg_long(v):
    return {"t": "j", "v": int(v)}


def arg_bool(v):
    return {"t": "z", "v": bool(v)}


def arg_str(v):
    return {"t": "l", "v": "" if v is None else str(v)}


def arg_obj(handle):
    return {"t": "o", "v": int(handle)}


def arg_array(handles):
    return {"t": "a", "v": list(handles)}


# ---- 基础操作 ----

def find_class(name: str):
    """按斜杠类名查找类，返回句柄。"""
    return _call(op="find_class", **{"class": name})["handle"]


def call_static(target, name: str, sig: str, args=None, ret="v"):
    return _call(op="call_static", target=int(target), name=name, sig=sig, args=list(args or []), ret=ret)


def call_method(receiver, name: str, sig: str, args=None, ret="v"):
    return _call(op="call", target=int(receiver), name=name, sig=sig, args=list(args or []), ret=ret)


def new_object(class_handle, sig: str, args=None):
    return _call(op="new_object", target=int(class_handle), sig=sig, args=list(args or []), ret="o")


def new_string(text: str):
    return _call(op="new_string", value=text)["handle"]


def get_string(handle) -> str:
    res = _call(op="get_string", target=int(handle))
    value = res.get("v")
    return "" if value is None else str(value)


def get_static_field(class_name: str, name: str, sig: str):
    return _call(op="get_static_field", **{"class": class_name}, name=name, sig=sig)


def get_field(target, name: str, sig: str):
    return _call(op="get_field", target=int(target), name=name, sig=sig)


# ---------------------------------------------------------------------------
# Context（应用上下文）
# ---------------------------------------------------------------------------

_APP_CONTEXT = None


class Context:
    """Android Application Context（通过 ActivityThread.currentApplication 取得）。"""

    @staticmethod
    def get():
        global _APP_CONTEXT
        if _APP_CONTEXT is None:
            cls = find_class("android/app/ActivityThread")
            res = call_static(cls, "currentApplication", "()Landroid/app/Application;", [], ret="o")
            _APP_CONTEXT = res["handle"]
        return _APP_CONTEXT

    @staticmethod
    def service(name: str):
        ctx = Context.get()
        res = call_method(ctx, "getSystemService", "(Ljava/lang/String;)Ljava/lang/Object;", [arg_str(name)], ret="o")
        return res["handle"]

    @staticmethod
    def start_activity(intent) -> None:
        call_method(Context.get(), "startActivity", "(Landroid/content/Intent;)V", [arg_obj(intent)], ret="v")

    @staticmethod
    def package_name() -> str:
        res = call_method(Context.get(), "getPackageName", "()Ljava/lang/String;", [], ret="l")
        return str(res.get("v") or "")


# ---------------------------------------------------------------------------
# 高层封装
# ---------------------------------------------------------------------------

class Permissions:
    """运行时权限：先 check，必要时 request 弹系统对话框，再执行真正的 SDK 调用。

    AndroidManifest 里一次性声明了全部可能用到的权限（声明≠授予），
    危险权限只在这里按需申请：用户点"允许"之前不会有任何越权行为。
    """

    CAMERA = "android.permission.CAMERA"
    RECORD_AUDIO = "android.permission.RECORD_AUDIO"
    ACCESS_FINE_LOCATION = "android.permission.ACCESS_FINE_LOCATION"
    ACCESS_COARSE_LOCATION = "android.permission.ACCESS_COARSE_LOCATION"
    ACCESS_BACKGROUND_LOCATION = "android.permission.ACCESS_BACKGROUND_LOCATION"
    READ_CONTACTS = "android.permission.READ_CONTACTS"
    WRITE_CONTACTS = "android.permission.WRITE_CONTACTS"
    READ_PHONE_STATE = "android.permission.READ_PHONE_STATE"
    CALL_PHONE = "android.permission.CALL_PHONE"
    SEND_SMS = "android.permission.SEND_SMS"
    READ_SMS = "android.permission.READ_SMS"
    READ_EXTERNAL_STORAGE = "android.permission.READ_EXTERNAL_STORAGE"
    WRITE_EXTERNAL_STORAGE = "android.permission.WRITE_EXTERNAL_STORAGE"
    POST_NOTIFICATIONS = "android.permission.POST_NOTIFICATIONS"
    BLUETOOTH_CONNECT = "android.permission.BLUETOOTH_CONNECT"
    BLUETOOTH_SCAN = "android.permission.BLUETOOTH_SCAN"
    BODY_SENSORS = "android.permission.BODY_SENSORS"
    ACTIVITY_RECOGNITION = "android.permission.ACTIVITY_RECOGNITION"
    VIBRATE = "android.permission.VIBRATE"
    INTERNET = "android.permission.INTERNET"

    @staticmethod
    def check(permission: str) -> bool:
        """只查询，不弹窗。"""
        ctx = Context.get()
        res = call_method(ctx, "checkSelfPermission", "(Ljava/lang/String;)I",
                          [arg_str(permission)], ret="i")
        value = res.get("v")
        return int(value if value is not None else -1) == 0

    @staticmethod
    def request(permission: str, timeout: float = 30.0) -> bool:
        """弹系统权限对话框并等待结果（阻塞，最长 timeout 秒）。"""
        if not available():
            raise SdkUnavailable("没有可用的 JNI 桥接，无法申请权限")
        from rust.Vapor import request_permission

        return bool(request_permission(permission, timeout))

    @staticmethod
    def ensure(permission: str, timeout: float = 30.0) -> bool:
        """已授予直接返回 True，否则弹窗申请。"""
        if Permissions.check(permission):
            return True
        return Permissions.request(permission, timeout)

    @staticmethod
    def granted(permission: str) -> bool:
        return Permissions.check(permission)


class Location:
    """定位：真正取位置时才申请定位权限。"""

    @staticmethod
    def last_known(provider: str = "gps"):
        """返回 {"provider", "latitude", "longitude"}，没有缓存位置时返回 None。"""
        Permissions.ensure(Permissions.ACCESS_COARSE_LOCATION)
        mgr = Context.service("location")
        try:
            res = call_method(
                mgr, "getLastKnownLocation",
                "(Ljava/lang/String;)Landroid/location/Location;",
                [arg_str(provider)], ret="o",
            )
            handle = res.get("handle")
            if not handle:
                return None
            try:
                lat = call_method(handle, "getLatitude", "()D", [], ret="d").get("v")
                lon = call_method(handle, "getLongitude", "()D", [], ret="d").get("v")
            finally:
                release(handle)
            return {
                "provider": provider,
                "latitude": float(lat or 0.0),
                "longitude": float(lon or 0.0),
            }
        finally:
            release(mgr)


class Telephony:
    """电话信息：读取设备标识需要 READ_PHONE_STATE（首次调用时申请）。"""

    @staticmethod
    def device_id() -> str:
        Permissions.ensure(Permissions.READ_PHONE_STATE)
        tm = Context.service("phone")
        try:
            res = call_method(tm, "getDeviceId", "()Ljava/lang/String;", [], ret="l")
            return str(res.get("v") or "")
        finally:
            release(tm)

    @staticmethod
    def network_operator() -> str:
        tm = Context.service("phone")
        try:
            res = call_method(tm, "getNetworkOperatorName", "()Ljava/lang/String;", [], ret="l")
            return str(res.get("v") or "")
        finally:
            release(tm)


class Notifications:
    """通知：Android 13+ 首次发通知时申请 POST_NOTIFICATIONS。"""

    CHANNEL_ID = "vapor"
    CHANNEL_NAME = "Vapor"

    @staticmethod
    def _ensure_channel(manager) -> None:
        cls = find_class("android/app/NotificationChannel")
        channel = new_object(
            cls, "(Ljava/lang/String;Ljava/lang/CharSequence;I)V",
            [arg_str(Notifications.CHANNEL_ID), arg_str(Notifications.CHANNEL_NAME), arg_int(3)],
        )
        try:
            call_method(manager, "createNotificationChannel",
                        "(Landroid/app/NotificationChannel;)V", [arg_obj(channel)], ret="v")
        finally:
            release(channel)

    @staticmethod
    def notify(title: str, text: str = "", nid: int = 1) -> bool:
        if not Permissions.check(Permissions.POST_NOTIFICATIONS):
            if not Permissions.ensure(Permissions.POST_NOTIFICATIONS):
                return False
        ctx = Context.get()
        manager = Context.service("notification")
        try:
            Notifications._ensure_channel(manager)
            builder_cls = find_class("android/app/Notification$Builder")
            builder = new_object(
                builder_cls, "(Landroid/content/Context;Ljava/lang/String;)V",
                [arg_obj(ctx), arg_str(Notifications.CHANNEL_ID)],
            )
            try:
                call_method(builder, "setContentTitle",
                            "(Ljava/lang/CharSequence;)Landroid/app/Notification$Builder;",
                            [arg_str(title)], ret="o")
                call_method(builder, "setContentText",
                            "(Ljava/lang/CharSequence;)Landroid/app/Notification$Builder;",
                            [arg_str(text)], ret="o")
                icon = get_static_field("android/R$drawable", "stat_notify_chat", "I").get("v")
                call_method(builder, "setSmallIcon", "(I)Landroid/app/Notification$Builder;",
                            [arg_int(int(icon or 17301620))], ret="o")
                notif = call_method(builder, "build", "()Landroid/app/Notification;", [], ret="o")["handle"]
                try:
                    call_method(manager, "notify", "(ILandroid/app/Notification;)V",
                                [arg_int(nid), arg_obj(notif)], ret="v")
                finally:
                    release(notif)
            finally:
                release(builder)
            return True
        finally:
            release(manager)


class Toast:
    LENGTH_SHORT = 0
    LENGTH_LONG = 1

    @staticmethod
    def show(text: str, long: bool = False) -> None:
        ctx = Context.get()
        toast_cls = find_class("android/widget/Toast")
        sig = "(Landroid/content/Context;Ljava/lang/CharSequence;I)Landroid/widget/Toast;"
        toast = call_static(
            toast_cls, "makeText", sig,
            [arg_obj(ctx), arg_str(text), arg_int(Toast.LENGTH_LONG if long else Toast.LENGTH_SHORT)],
            ret="o",
        )["handle"]
        call_method(toast, "show", "()V", [], ret="v")
        release(toast)


class Vibrator:
    """振动器（API 31+ 需要 VIBRATE 权限；旧接口仍可用）。"""

    @staticmethod
    def _service():
        return Context.service("vibrator")

    @staticmethod
    def vibrate(ms: int = 50) -> None:
        Permissions.ensure(Permissions.VIBRATE)
        vib = Vibrator._service()
        try:
            call_method(vib, "vibrate", "(J)V", [arg_long(ms)], ret="v")
        finally:
            release(vib)

    @staticmethod
    def cancel() -> None:
        vib = Vibrator._service()
        try:
            call_method(vib, "cancel", "()V", [], ret="v")
        finally:
            release(vib)


class Clipboard:
    @staticmethod
    def _manager():
        return Context.service("clipboard")

    @staticmethod
    def set_text(text: str) -> None:
        mgr = Clipboard._manager()
        try:
            clip_cls = find_class("android/content/ClipData")
            clip = call_static(
                clip_cls, "newPlainText",
                "(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)Landroid/content/ClipData;",
                [arg_str("vapor"), arg_str(text)], ret="o",
            )["handle"]
            call_method(mgr, "setPrimaryClip", "(Landroid/content/ClipData;)V", [arg_obj(clip)], ret="v")
            release(clip)
        finally:
            release(mgr)

    @staticmethod
    def get_text() -> str:
        mgr = Clipboard._manager()
        try:
            res = call_method(mgr, "getPrimaryClip", "()Landroid/content/ClipData;", [], ret="o")
            clip = res.get("handle")
            if not clip:
                return ""
            item = call_method(clip, "getItemAt", "(I)Landroid/content/ClipData$Item;", [arg_int(0)], ret="o")["handle"]
            text = call_method(item, "getText", "()Ljava/lang/CharSequence;", [], ret="o")["handle"]
            out = call_method(text, "toString", "()Ljava/lang/String;", [], ret="l").get("v")
            for h in (item, text):
                release(h)
            return str(out or "")
        finally:
            release(mgr)


class Device:
    @staticmethod
    def _static(name: str) -> str:
        res = get_static_field("android/os/Build", name, "Ljava/lang/String;")
        return str(res.get("v") or "")

    @staticmethod
    def model() -> str:
        return Device._static("MODEL")

    @staticmethod
    def brand() -> str:
        return Device._static("BRAND")

    @staticmethod
    def sdk_int() -> int:
        res = get_static_field("android/os/Build$VERSION", "SDK_INT", "I")
        return int(res.get("v") or 0)

    @staticmethod
    def info() -> dict:
        return {"brand": Device.brand(), "model": Device.model(), "sdk": Device.sdk_int()}


class Battery:
    PROPERTY_CAPACITY = 4

    @staticmethod
    def capacity() -> int:
        """电量百分比（0-100）。"""
        mgr = Context.service("batterymanager")
        try:
            res = call_method(mgr, "getIntProperty", "(I)I", [arg_int(Battery.PROPERTY_CAPACITY)], ret="i")
            return int(res.get("v") or 0)
        finally:
            release(mgr)


class Intent:
    """Intent 构造与启动。"""

    ACTION_VIEW = "android.intent.action.VIEW"
    ACTION_SEND = "android.intent.action.SEND"
    ACTION_MAIN = "android.intent.action.MAIN"

    @staticmethod
    def create(action: str = None):
        cls = find_class("android/content/Intent")
        intent = new_object(cls, "()V", [])["handle"]
        if action:
            call_method(intent, "setAction", "(Ljava/lang/String;)Landroid/content/Intent;", [arg_str(action)], ret="o")
        return intent

    @staticmethod
    def open_url(url: str) -> None:
        intent = Intent.create(Intent.ACTION_VIEW)
        uri_cls = find_class("android/net/Uri")
        uri = call_static(uri_cls, "parse", "(Ljava/lang/String;)Landroid/net/Uri;", [arg_str(url)], ret="o")["handle"]
        call_method(intent, "setData", "(Landroid/net/Uri;)Landroid/content/Intent;", [arg_obj(uri)], ret="o")
        try:
            Context.start_activity(intent)
        finally:
            release(uri)
            release(intent)

    @staticmethod
    def share_text(text: str) -> None:
        intent = Intent.create(Intent.ACTION_SEND)
        call_method(intent, "setType", "(Ljava/lang/String;)Landroid/content/Intent;", [arg_str("text/plain")], ret="o")
        extra_key = new_string("android.intent.extra.TEXT")
        try:
            call_method(
                intent, "putExtra",
                "(Ljava/lang/String;Ljava/lang/CharSequence;)Landroid/content/Intent;",
                [arg_obj(extra_key), arg_str(text)], ret="o",
            )
            chooser_cls = find_class("android/content/Intent")
            chooser = call_static(
                chooser_cls, "createChooser",
                "(Landroid/content/Intent;Ljava/lang/CharSequence;)Landroid/content/Intent;",
                [arg_obj(intent), arg_str("分享")], ret="o",
            )["handle"]
            try:
                Context.start_activity(chooser)
            finally:
                release(chooser)
        finally:
            release(extra_key)
            release(intent)


class Prefs:
    """SharedPreferences：读写持久化键值。"""

    MODE_PRIVATE = 0

    def __init__(self, name: str = "vapor"):
        ctx = Context.get()
        self.handle = call_method(
            ctx, "getSharedPreferences",
            "(Ljava/lang/String;I)Landroid/content/SharedPreferences;",
            [arg_str(name), arg_int(0)], ret="o",
        )["handle"]

    def get_string(self, key: str, default: str = "") -> str:
        res = call_method(
            self.handle, "getString",
            "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
            [arg_str(key), arg_str(default)], ret="l",
        )
        return str(res.get("v") or "")

    def get_int(self, key: str, default: int = 0) -> int:
        res = call_method(
            self.handle, "getInt", "(Ljava/lang/String;I)I",
            [arg_str(key), arg_int(default)], ret="i",
        )
        return int(res.get("v") or 0)

    def get_bool(self, key: str, default: bool = False) -> bool:
        res = call_method(
            self.handle, "getBoolean", "(Ljava/lang/String;Z)Z",
            [arg_str(key), arg_bool(default)], ret="z",
        )
        return bool(res.get("v") or False)

    def put_string(self, key: str, value: str) -> None:
        editor = call_method(self.handle, "edit", "()Landroid/content/SharedPreferences$Editor;", [], ret="o")["handle"]
        call_method(
            editor, "putString",
            "(Ljava/lang/String;Ljava/lang/String;)Landroid/content/SharedPreferences$Editor;",
            [arg_str(key), arg_str(value)], ret="o",
        )
        call_method(editor, "apply", "()V", [], ret="v")
        release(editor)


class PackageInfo:
    @staticmethod
    def version_name() -> str:
        ctx = Context.get()
        pm = call_method(ctx, "getPackageManager", "()Landroid/content/pm/PackageManager;", [], ret="o")["handle"]
        try:
            info = call_method(
                pm, "getPackageInfo",
                "(Ljava/lang/String;I)Landroid/content/pm/PackageInfo;",
                [arg_str(Context.package_name()), arg_int(0)], ret="o",
            )["handle"]
            try:
                res = get_field(info, "versionName", "Ljava/lang/String;")
                return str(res.get("v") or "")
            finally:
                release(info)
        finally:
            release(pm)


class AndroidLog:
    """转发到 android.util.Log（在 logcat 里可见）。"""

    @staticmethod
    def _log(level: str, msg: str, tag: str = "Vapor") -> None:
        cls = find_class("android/util/Log")
        call_static(cls, level, "(Ljava/lang/String;Ljava/lang/String;)I", [arg_str(tag), arg_str(msg)], ret="i")

    @staticmethod
    def d(msg: str, tag: str = "Vapor") -> None:
        AndroidLog._log("d", msg, tag)

    @staticmethod
    def i(msg: str, tag: str = "Vapor") -> None:
        AndroidLog._log("i", msg, tag)

    @staticmethod
    def w(msg: str, tag: str = "Vapor") -> None:
        AndroidLog._log("w", msg, tag)

    @staticmethod
    def e(msg: str, tag: str = "Vapor") -> None:
        AndroidLog._log("e", msg, tag)
