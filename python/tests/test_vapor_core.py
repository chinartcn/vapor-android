"""Vapor.core 响应式原语测试（可在纯 Python 回退下运行）。

    PYTHONPATH=python python3 python/tests/test_vapor_core.py
"""

from __future__ import annotations

import os
import sys

try:  # 已安装（CI 的 wheel / maturin develop）时优先用安装版
    import Vapor  # noqa: F401
except ImportError:  # 本地源码运行
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor import core  # noqa: E402


def test_ref_tracks_and_updates():
    count = core.ref(1)
    seen = []
    core.effect(lambda: seen.append(count.value))
    count.value = 2
    count.value = 3
    assert seen == [1, 2, 3], seen


def test_ref_skips_same_value():
    count = core.ref(1)
    seen = []
    core.effect(lambda: seen.append(count.value))
    count.value = 1
    assert seen == [1], seen


def test_computed_is_lazy_and_cached():
    count = core.ref(2)
    calls = []

    def getter():
        calls.append(1)
        return count.value * 10

    double = core.computed(getter)
    assert calls == []
    assert double.value == 20
    assert double.value == 20
    assert len(calls) == 1, calls
    count.value = 3
    assert double.value == 30
    assert len(calls) == 2, calls


def test_computed_drives_effect():
    first = core.ref("a")
    last = core.ref("b")
    full = core.computed(lambda: f"{first.value} {last.value}")
    seen = []
    core.effect(lambda: seen.append(full.value))
    first.value = "x"
    last.value = "y"
    assert seen == ["a b", "x b", "x y"], seen


def test_batch_coalesces_updates():
    a = core.ref(0)
    b = core.ref(0)
    runs = []
    core.effect(lambda: runs.append((a.value, b.value)))

    def update():
        a.value = 1
        b.value = 2

    core.batch(update)
    assert runs == [(0, 0), (1, 2)], runs


def test_untrack_does_not_collect():
    a = core.ref(1)
    b = core.ref(10)
    runs = []
    core.effect(lambda: runs.append(a.value + core.untrack(lambda: b.value)))
    b.value = 20
    assert runs == [11], runs          # b 不在依赖里，不触发
    a.value = 2
    assert runs == [11, 22], runs      # 重跑时读到 b 的当前值 20


def test_effect_stop():
    a = core.ref(0)
    seen = []
    handle = core.effect(lambda: seen.append(a.value))
    a.value = 1
    handle.stop()
    a.value = 2
    assert seen == [0, 1], seen


def test_effect_scope_dispose():
    a = core.ref(0)
    seen = []
    scope = core.effect_scope(lambda: core.effect(lambda: seen.append(a.value)))
    a.value = 1
    scope.stop()
    a.value = 2
    assert seen == [0, 1], seen


def test_reactive_tracks_only_used_field():
    state = core.reactive({"user": {"name": "Ada", "age": 30}})
    seen = []
    core.effect(lambda: seen.append(state.user.name))
    state.user.age = 31
    assert seen == ["Ada"], seen
    state.user.name = "Grace"
    assert seen == ["Ada", "Grace"], seen


def test_reactive_setitem_and_keys():
    state = core.reactive({"a": 1})
    state["b"] = 2
    assert state["b"] == 2
    assert set(state.keys()) == {"a", "b"}
    assert core.to_raw(state) == {"a": 1, "b": 2}


def test_reactive_list_operations():
    items = core.reactive([1, 2, 3])
    seen = []
    core.effect(lambda: seen.append(len(items)))
    items.append(4)
    items.pop()
    assert seen == [3, 4, 3], seen
    assert items[0] == 1
    assert core.to_raw(items) == [1, 2, 3]


def test_watch_async_and_values():
    count = core.ref(0)
    calls = []
    core.watch(count, lambda new, old: calls.append((new, old)))
    count.value = 1
    assert calls == [], "watch 回调应该排队到 flush_jobs"
    core.flush_jobs()
    assert calls == [(1, 0)], calls
    count.value = 1
    core.flush_jobs()
    assert calls == [(1, 0)], calls


def test_watch_multiple_distinct_changes():
    """回归：scheduler 分支必须在派发前复位 queued，否则只有第一次变化回调。"""
    count = core.ref(0)
    calls = []
    core.watch(count, lambda new, old: calls.append((new, old)))
    for value in (1, 2, 3):
        count.value = value
        core.flush_jobs()
    assert calls == [(1, 0), (2, 1), (3, 2)], calls


def test_watch_immediate():
    count = core.ref(7)
    calls = []
    core.watch(count, lambda new, old: calls.append((new, old)), immediate=True)
    assert calls == [(7, None)], calls


def test_watch_deep_reactive():
    state = core.reactive({"n": 1})
    calls = []
    core.watch(state, lambda new, old: calls.append(dict(new)))
    state.n = 2
    core.flush_jobs()
    assert calls == [{"n": 2}], calls


def test_watch_effect_runs_eagerly():
    a = core.ref(1)
    seen = []
    core.watch_effect(lambda: seen.append(a.value))
    a.value = 2
    assert seen == [1, 2], seen


def test_refs_and_unref():
    r = core.ref(5)
    assert core.is_ref(r)
    assert core.unref(r) == 5
    assert not core.is_ref(5)


def test_trigger_ref_force():
    r = core.ref(1)
    seen = []
    core.effect(lambda: seen.append(r.value))
    r.trigger()
    assert seen == [1, 1], seen


def test_to_raw_deep():
    state = core.reactive({"user": {"name": "Ada"}, "list": [1, {"x": 2}]})
    raw = core.to_raw(state)
    assert raw == {"user": {"name": "Ada"}, "list": [1, {"x": 2}]}, raw
    assert isinstance(raw["user"], dict)


def test_next_tick():
    order = []
    core.next_tick(lambda: order.append("tick"))
    core.effect(lambda: None)
    order.append("sync")
    core.flush_jobs()
    assert order == ["sync", "tick"], order


def test_stats():
    core.ref(1)
    s = core.stats()
    assert isinstance(s, dict)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in tests:
        fn()
        passed += 1
        print(f"[ok] {fn.__name__}")
    print(f"\n{passed} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
