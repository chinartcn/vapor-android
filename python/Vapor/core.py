"""Vapor.core —— Vue 3 风格的响应式原语。

    from Vapor.core import ref, reactive, computed, watch, watch_effect

    count = ref(0)
    state = reactive({"user": {"name": "Ada"}, "list": [1, 2, 3]})
    double = computed(lambda: count.value * 2)
    watch(count, lambda new, old: print(f"{old} -> {new}"))

底层是 Vapor._core 的 alien-signals 风格依赖图：读写即追踪，没有组件级
re-render，没有 vdom。ref 的读写是 O(1) 链表操作。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Callable

from . import _native

_core = _native.core

_MISSING = object()

__all__ = [
    "Ref", "ComputedRef", "Reactive", "ReactiveList",
    "ref", "shallow_ref", "computed", "reactive", "readonly",
    "is_ref", "unref", "trigger_ref",
    "to_raw", "to_reactive", "to_ref", "to_refs", "mark_raw",
    "watch", "watch_effect", "effect", "effect_scope", "batch", "untrack",
    "next_tick", "flush_jobs", "provide", "inject", "push_instance", "pop_instance",
    "stats",
]


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _eq(a, b) -> bool:
    if a is b:
        return True
    try:
        return bool(a == b)
    except Exception:  # 自定义 __eq__ 抛异常时不视为相等
        return False


def _snapshot(value, deep: bool):
    return to_raw(value) if deep else value


# ---------------------------------------------------------------------------
# ref / computed
# ---------------------------------------------------------------------------

class Ref:
    """响应式引用：.value 读会追踪，写会触发依赖它的 effect。"""

    __slots__ = ("_s",)
    __vapor_ref__ = True

    def __init__(self, value: Any = None):
        self._s = _core.Signal(to_reactive(value))

    @property
    def value(self):
        return self._s.value

    @value.setter
    def value(self, new_value):
        wrapped = to_reactive(new_value)
        if _eq(self._s.peek(), wrapped):
            return
        self._s.value = wrapped

    def peek(self):
        """不建立依赖地读取。"""
        return self._s.peek()

    def trigger(self):
        """强制通知依赖（即使值没变）。"""
        self._s.value = self._s.peek()

    @property
    def signal(self):
        return self._s

    def __repr__(self):
        return f"Ref({self.peek()!r})"


class ComputedRef:
    """只读派生值：惰性求值，依赖不变时不会重算。"""

    __slots__ = ("_s",)
    __vapor_ref__ = True

    def __init__(self, getter: Callable[[], Any]):
        self._s = _core.Computed(getter)

    @property
    def value(self):
        return self._s.value

    def peek(self):
        return self._s.peek()

    @property
    def dirty(self) -> bool:
        return self._s.dirty

    @property
    def signal(self):
        return self._s

    def __repr__(self):
        return f"ComputedRef(dirty={self.dirty})"


def ref(value: Any = None) -> Ref:
    return Ref(value)


def shallow_ref(value: Any = None) -> Ref:
    """浅层 ref：不对内部对象做深层响应式包装。"""
    r = Ref.__new__(Ref)
    r._s = _core.Signal(value)
    return r


def computed(getter: Callable[[], Any]) -> ComputedRef:
    return ComputedRef(getter)


def is_ref(value: Any) -> bool:
    return isinstance(value, (Ref, ComputedRef)) or getattr(value, "__vapor_ref__", False) is True


def unref(value: Any) -> Any:
    if isinstance(value, (Ref, ComputedRef)):
        return value.value
    return value


def trigger_ref(value: Any) -> None:
    if isinstance(value, Ref):
        value.trigger()


# ---------------------------------------------------------------------------
# reactive：属性级 signal
# ---------------------------------------------------------------------------

class Reactive(Mapping):
    """深层响应式对象：每个键一个 signal，读写粒度到属性。"""

    __slots__ = ("_signals",)

    def __init__(self, data: Mapping | None = None):
        object.__setattr__(self, "_signals", {})
        if data:
            for k, v in dict(data).items():
                self._signals[k] = _core.Signal(to_reactive(v))

    # -- 内部 --

    def _read(self, key):
        sig = self._signals.get(key, _MISSING)
        if sig is _MISSING:
            raise KeyError(key)
        return sig.value

    def _write(self, key, value):
        wrapped = to_reactive(value)
        sig = self._signals.get(key)
        if sig is None:
            self._signals[key] = _core.Signal(wrapped)
            return
        if _eq(sig.peek(), wrapped):
            return
        sig.value = wrapped

    # -- 属性访问 --

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            sig = object.__getattribute__(self, "_signals")[name]
        except KeyError:
            raise AttributeError(name) from None
        return sig.value

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        self._write(name, value)

    def __delattr__(self, name):
        if name.startswith("_"):
            object.__delattr__(self, name)
            return
        self._signals.pop(name, None)

    # -- 映射协议 --

    def __getitem__(self, key):
        return self._read(key)

    def __setitem__(self, key, value):
        self._write(key, value)

    def __delitem__(self, key):
        del self._signals[key]

    def __contains__(self, key):
        return key in self._signals

    def __iter__(self):
        return iter(list(self._signals))

    def __len__(self):
        return len(self._signals)

    def get(self, key, default=None):
        try:
            return self._read(key)
        except KeyError:
            return default

    def keys(self):
        return list(self._signals)

    def values(self):
        return [sig.value for sig in self._signals.values()]

    def items(self):
        return [(k, sig.value) for k, sig in self._signals.items()]

    def update(self, other=None, **kwargs):
        for k, v in dict(other or {}).items():
            self._write(k, v)
        for k, v in kwargs.items():
            self._write(k, v)

    def to_dict(self) -> dict:
        return to_raw(self)

    def __repr__(self):
        return f"Reactive({ {k: s.peek() for k, s in self._signals.items()} })"


class ReactiveList:
    """响应式数组：整体一个 signal，增删改都会通知。"""

    __slots__ = ("_s",)

    def __init__(self, items: Iterable | None = None):
        self._s = _core.Signal([to_reactive(i) for i in (items or [])])

    def _items(self):
        return self._s.peek()

    def _set(self, items):
        self._s.value = list(items)

    def __len__(self):
        return len(self._s.value)

    def __getitem__(self, index):
        return self._s.value[index]

    def __setitem__(self, index, value):
        items = list(self._items())
        items[index] = to_reactive(value)
        self._set(items)

    def __delitem__(self, index):
        items = list(self._items())
        del items[index]
        self._set(items)

    def __iter__(self):
        return iter(self._s.value)

    def __contains__(self, item):
        return item in self._s.value

    def __eq__(self, other):
        if isinstance(other, ReactiveList):
            return self._items() == other._items()
        return self._items() == other

    def __repr__(self):
        return f"ReactiveList({self._items()!r})"

    # -- 变更 --

    def append(self, item):
        self._set(self._items() + [to_reactive(item)])

    def extend(self, items):
        self._set(self._items() + [to_reactive(i) for i in items])

    def insert(self, index, item):
        items = list(self._items())
        items.insert(index, to_reactive(item))
        self._set(items)

    def pop(self, index=-1):
        items = list(self._items())
        out = items.pop(index)
        self._set(items)
        return out

    def remove(self, item):
        items = list(self._items())
        items.remove(item)
        self._set(items)

    def clear(self):
        self._set([])

    def reverse(self):
        items = list(self._items())
        items.reverse()
        self._set(items)

    def sort(self, key=None, reverse=False):
        items = list(self._items())
        items.sort(key=key, reverse=reverse)
        self._set(items)

    def index(self, item):
        return self._items().index(item)

    def to_list(self):
        return to_raw(self)


# ---------------------------------------------------------------------------
# 转换
# ---------------------------------------------------------------------------

def to_reactive(value):
    if isinstance(value, (Reactive, ReactiveList, Ref, ComputedRef)):
        return value
    if isinstance(value, Mapping) and not isinstance(value, Reactive):
        return Reactive(value)
    if isinstance(value, (list, tuple)) and not isinstance(value, ReactiveList):
        return ReactiveList(value)
    return value


def to_raw(value):
    if isinstance(value, Reactive):
        return {k: to_raw(sig.peek()) for k, sig in value._signals.items()}
    if isinstance(value, ReactiveList):
        return [to_raw(v) for v in value._s.peek()]
    if isinstance(value, (Ref, ComputedRef)):
        return to_raw(value.peek())
    return value


def _touch(value):
    """深度读取所有 signal，让 deep 侦听能建立完整依赖。"""
    if isinstance(value, Reactive):
        for sig in value._signals.values():
            _touch(sig.value)
    elif isinstance(value, ReactiveList):
        for item in value._s.value:
            _touch(item)
    elif isinstance(value, (Ref, ComputedRef)):
        value.value


def mark_raw(value):
    return value


def reactive(data) -> Any:
    if isinstance(data, Mapping):
        return Reactive(data)
    if isinstance(data, (list, tuple)):
        return ReactiveList(data)
    raise TypeError("reactive() 只接受 dict / list")


def readonly(data):
    """只读视图（Python 侧不做硬性拦截，仅返回深拷贝的响应式对象）。"""
    return to_reactive(to_raw(data))


def to_ref(obj, key) -> Ref:
    if isinstance(obj, Reactive):
        class _ProxyRef:
            __vapor_ref__ = True

            @property
            def value(self):
                return obj[key]

            @value.setter
            def value(self, v):
                obj[key] = v

        return _ProxyRef()  # type: ignore[return-value]
    raise TypeError("to_ref 仅支持 Reactive")


def to_refs(obj) -> dict:
    return {k: to_ref(obj, k) for k in obj.keys()}


# ---------------------------------------------------------------------------
# 副作用
# ---------------------------------------------------------------------------

class _Effect:
    __slots__ = ("_h",)

    def __init__(self, handle):
        self._h = handle

    def stop(self):
        self._h.stop()

    def pause(self):
        self._h.pause()

    def resume(self):
        self._h.resume()

    def run(self):
        self._h.run()

    @property
    def paused(self):
        return self._h.paused


def effect(fn: Callable[[], Any]) -> _Effect:
    """低级副作用：立即执行一次并收集依赖，之后依赖变化即重跑。"""
    return _Effect(_core.effect(fn))


def batch(fn: Callable[[], Any]):
    """批量更新：fn 内的多次写入只触发一次 effect 执行。"""
    return _core.batch(fn)


def untrack(fn: Callable[[], Any]):
    """在 fn 内不收集依赖（读取不追踪）。"""
    return _core.untrack(fn)


def effect_scope(fn: Callable[[], Any]):
    """创建一个副作用作用域；组件卸载时 stop() 一次性清理。"""
    return _core.effect_scope(fn)


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------

_jobs: list[Callable[[], None]] = []


def _queue_job(job: Callable[[], None]) -> None:
    if job not in _jobs:
        _jobs.append(job)


def flush_jobs() -> None:
    """执行排队的 watch 回调与 next_tick()。由渲染循环每帧调用。"""
    _core.flush()
    while _jobs:
        job = _jobs.pop(0)
        job()
    while _ticks:
        tick = _ticks.pop(0)
        tick._run()


def watch(source, cb: Callable[[Any, Any], None], immediate: bool = False, deep: bool = False):
    """侦听 ref / getter / reactive 对象；返回停止函数。

    source: Ref / ComputedRef / 可调用对象 / Reactive（等价于 deep 侦听）
    回调用异步队列（flush_jobs 时执行），同一 tick 内多次写入只回调一次。
    """
    if isinstance(source, (Ref, ComputedRef)):
        getter = lambda: source.value  # noqa: E731
    elif isinstance(source, (Reactive, ReactiveList)):
        getter = lambda: source  # noqa: E731
        deep = True
    elif callable(source):
        getter = source
    else:
        raise TypeError("watch 的 source 必须是 ref / 函数 / reactive 对象")

    old = _MISSING

    def job():
        nonlocal old
        new_value = getter()
        if deep:
            _touch(new_value)
        if old is _MISSING:
            old = _snapshot(new_value, deep)
            if immediate:
                cb(to_raw(new_value), None)
            return
        snapshot = _snapshot(new_value, deep)
        if _eq(snapshot, old):
            return
        previous = old
        old = snapshot
        cb(to_raw(new_value), previous)

    handle = _core.effect(job, lambda _e: _queue_job(job))

    def stop():
        handle.stop()
        if job in _jobs:
            _jobs.remove(job)

    stop.stop = stop  # 兼容 watch(...).stop 的写法
    return stop


def watch_effect(fn: Callable[[], Any]):
    """立即执行并追踪的副作用；返回停止函数。"""
    handle = _core.effect(fn)

    def stop():
        handle.stop()

    stop.stop = stop
    return stop


# ---------------------------------------------------------------------------
# next_tick
# ---------------------------------------------------------------------------

_ticks: list = []


class _Tick:
    __slots__ = ("_cbs",)

    def __init__(self):
        self._cbs = []

    def _run(self):
        cbs, self._cbs = self._cbs, []
        for cb in cbs:
            cb()

    def then(self, cb):
        self._cbs.append(cb)
        return self


def next_tick(cb: Callable[[], None] | None = None):
    """在下一帧（flush_jobs）执行回调，返回可链式 .then() 的句柄。"""
    tick = _Tick()
    if cb is not None:
        tick.then(cb)
    _ticks.append(tick)
    return tick


# ---------------------------------------------------------------------------
# provide / inject（与组件实例绑定）
# ---------------------------------------------------------------------------

_instance_stack: list[dict] = []


def push_instance(instance) -> None:
    _instance_stack.append({"instance": instance, "provides": {}})


def pop_instance() -> None:
    if _instance_stack:
        _instance_stack.pop()


def provide(key, value) -> None:
    if not _instance_stack:
        raise RuntimeError("provide() 必须在组件 setup 内调用")
    _instance_stack[-1]["provides"][key] = value


def inject(key, default=None):
    for frame in reversed(_instance_stack):
        if key in frame["provides"]:
            return frame["provides"][key]
    return default


def stats() -> dict:
    return _core.stats()
