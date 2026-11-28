"""脏区账本单元测试（纯 Python，无原生依赖）。

运行：PYTHONPATH=python python3 -m pytest python/tests/test_dirty.py
"""

from __future__ import annotations

import os
import sys

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor.ui.dirty import DirtyLedger, MAX_REGIONS, ledger  # noqa: E402


def test_mark_and_clip():
    d = DirtyLedger()
    d.set_canvas(100, 100)
    d.begin_frame()
    d.end_frame()                    # 复位 full
    d.begin_frame()
    d.mark_rect(5, 5, 10, 10)
    assert d.rects() == [(5, 5, 10, 10)]

    d.begin_frame()
    d.mark_rect(-5, -5, 10, 10)      # 越界裁剪
    assert d.rects() == [(0, 0, 5, 5)]

    d.begin_frame()
    d.mark_rect(90, 90, 50, 50)
    assert d.rects() == [(90, 90, 10, 10)]


def test_merge_adjacent():
    d = DirtyLedger()
    d.set_canvas(100, 100)
    d.end_frame()
    d.begin_frame()
    d.mark_rect(0, 0, 10, 10)
    d.mark_rect(11, 0, 10, 10)       # 相距 1px，应合并
    assert d.rects() == [(0, 0, 21, 10)]


def test_full_coverage_degrades_to_full():
    d = DirtyLedger()
    d.set_canvas(100, 100)
    d.end_frame()
    d.begin_frame()
    d.mark_rect(0, 0, 100, 100)
    assert d.is_full
    assert d.rects() == []


def test_set_canvas_resets_full():
    d = DirtyLedger()
    d.set_canvas(100, 100)
    d.end_frame()
    assert not d.is_full
    d.set_canvas(120, 100)
    assert d.is_full


def test_too_many_regions_degrades():
    d = DirtyLedger()
    d.set_canvas(2000, 1000)
    d.end_frame()
    d.begin_frame()
    for i in range(MAX_REGIONS * 4 + 1):
        d.mark_rect(i * 10, 0, 5, 5)
    assert d.is_full


def test_frame_sequence_resets():
    d = DirtyLedger()
    d.set_canvas(50, 50)
    d.begin_frame()
    d.mark_rect(1, 2, 3, 4)          # 首帧 full，标记无意义
    assert d.is_full
    d.end_frame()
    assert not d.is_full
    d.begin_frame()
    d.mark_rect(1, 2, 3, 4)
    assert d.rects() == [(1, 2, 3, 4)]
    d.end_frame()
    assert not d.is_full
    assert d.rects() == []


def test_singleton_contract():
    # 模块级单例必须暴露文档约定的接口。
    ledger.set_canvas(8, 8)
    ledger.begin_frame()
    ledger.mark_rect(0, 0, 1, 1)
    ledger.end_frame()
    assert isinstance(ledger.is_full, bool)
    assert ledger.rects() == []
