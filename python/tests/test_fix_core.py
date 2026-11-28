"""core.py 源码审查修复项的回归测试。

    PYTHONPATH=python python3 -m pytest python/tests/test_fix_core.py -q
"""

from __future__ import annotations

import os
import sys

try:  # 已安装（CI 的 wheel / maturin develop）时优先用安装版
    import Vapor  # noqa: F401
except ImportError:  # 本地源码运行
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from Vapor import core  # noqa: E402


def test_watch_fires_on_every_change():
    """scheduler 派发前必须复位 queued，否则回调只在第一次变化时触发。"""
    count = core.ref(0)
    calls = []
    stop = core.watch(count, lambda new, old: calls.append((new, old)))
    try:
        for value in (1, 2, 3, 4):
            count.value = value
            core.flush_jobs()
        assert calls == [(1, 0), (2, 1), (3, 2), (4, 3)], calls
    finally:
        stop()
        core.flush_jobs()


def test_watch_deep_nested_change():
    """deep 侦听应捕获嵌套属性的变化，且能连续触发。"""
    state = core.reactive({"n": 1, "inner": {"x": 1}})
    calls = []
    stop = core.watch(state, lambda new, old: calls.append(dict(new)))
    try:
        state.n = 2
        core.flush_jobs()
        state.inner.x = 3
        core.flush_jobs()
        assert calls == [
            {"n": 2, "inner": {"x": 1}},
            {"n": 2, "inner": {"x": 3}},
        ], calls
    finally:
        stop()
        core.flush_jobs()


def test_inject_required_raises_with_key():
    """required=True 找不到 provider 时抛错，且错误信息含 key 名。"""
    core.push_instance({"name": "child"})
    try:
        with pytest.raises(RuntimeError, match="theme"):
            core.inject("theme", required=True)
    finally:
        core.pop_instance()


def test_inject_required_finds_ancestor_and_default_is_compatible():
    """required 模式能沿实例栈向上查找；默认模式行为不变。"""
    core.push_instance({"name": "parent"})
    try:
        core.provide("theme", "dark")
        core.push_instance({"name": "child"})
        try:
            assert core.inject("theme", required=True) == "dark"
            assert core.inject("missing") is None
            assert core.inject("missing", "fallback") == "fallback"
        finally:
            core.pop_instance()
    finally:
        core.pop_instance()


def test_batch_coalesces_watch_callback():
    """batch 内多次写入只触发一次 watch 回调，batch 后仍可继续触发。"""
    count = core.ref(0)
    calls = []
    stop = core.watch(count, lambda new, old: calls.append((new, old)))
    try:
        def update(a, b):
            count.value = a
            count.value = b

        core.batch(lambda: update(1, 3))
        core.flush_jobs()
        assert calls == [(3, 0)], calls

        core.batch(lambda: update(4, 5))
        core.flush_jobs()
        assert calls == [(3, 0), (5, 3)], calls
    finally:
        stop()
        core.flush_jobs()


def test_batch_coalesces_effect_and_watch_together():
    """普通 effect 与 watch 在 batch 下都只合并为一次更新。"""
    a = core.ref(0)
    runs = []
    calls = []
    core.effect(lambda: runs.append(a.value))
    stop = core.watch(a, lambda new, old: calls.append((new, old)))
    try:
        def update():
            a.value = 1
            a.value = 2

        core.batch(update)
        assert runs == [0, 2], runs
        assert calls == [], "watch 回调应排队到 flush_jobs"
        core.flush_jobs()
        assert calls == [(2, 0)], calls
    finally:
        stop()
        core.flush_jobs()
