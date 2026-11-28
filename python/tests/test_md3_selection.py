"""MD3 选择/输入类组件验收测试（内建组件库执行文档 2）。"""

from __future__ import annotations

import os
import re
from types import SimpleNamespace

import pytest

import Vapor.md3.selection as sel
from Vapor.theme import current_theme
from Vapor.ui import COMPONENTS as UI_COMPONENTS
from Vapor.ui.base import Rect
from Vapor.ui.renderer import Renderer


def build(cls, **props):
    r = Renderer()
    shell = cls.instantiate(r, props, {})
    return r, shell, shell.children[0]


def fire(node, event, ev=None):
    ev = SimpleNamespace(x=0, y=0) if ev is None else ev
    handlers = []
    for n in node.walk():
        for provider, _modifiers in n.events.get(event, []):
            handlers.append(provider())
    assert handlers, f"没有找到事件处理器: {event}"
    for handler in handlers:
        handler(ev)


def test_manifest_no_conflict():
    names = [c.name for c in sel.COMPONENTS]
    assert len(names) == len(set(names))
    assert names == ["MdCheckbox", "MdRadioButton", "MdSwitch",
                     "MdSlider", "MdTextField", "MdChip"]
    assert all(name not in UI_COMPONENTS for name in names)

    manifests = sel.manifest()
    assert manifests["abi"] == 1
    assert [c["name"] for c in manifests["components"]] == names


def test_checkbox_cycles_and_callback():
    seen = []
    _r, shell, root = build(sel.MdCheckbox, checked=False, on_change=seen.append)
    fire(root, "click")
    assert seen == [True]
    # props 只读：有回调时不得被组件直接改写
    assert shell.props.get("checked") is False


def test_checkbox_callback_receives_toggle():
    seen = []
    _r, _shell, root = build(sel.MdCheckbox, checked=True, on_change=seen.append)
    fire(root, "click")
    assert seen == [False]


def test_radio_group_is_stateless():
    seen_a, seen_b = [], []
    _r1, shell_a, a = build(sel.MdRadioButton, group="g", value="a",
                            checked=True, on_change=seen_a.append)
    _r2, _shell_b, b = build(sel.MdRadioButton, group="g", value="b",
                             checked=False, on_change=seen_b.append)
    fire(b, "click")
    assert seen_b == ["b"]
    assert seen_a == []
    assert shell_a.props.get("checked") is True


def test_switch_theme_colors():
    theme = current_theme()
    _r, _s, off = build(sel.MdSwitch, checked=False)
    assert off.props["_track"].props["bg"] == theme.role("surfaceVariant")
    _r2, _s2, on = build(sel.MdSwitch, checked=True)
    assert on.props["_track"].props["bg"] == theme.role("primary")


def test_switch_reduced_motion(monkeypatch):
    monkeypatch.setattr(sel, "reduced_motion", lambda: True)
    _r, _s, on = build(sel.MdSwitch, checked=True)
    _r2, _s2, off = build(sel.MdSwitch, checked=False)
    assert on.props["_knob"].props["x"] == 24
    assert off.props["_knob"].props["x"] == 4


def test_slider_step_quantization():
    seen = []
    _r, _s, root = build(sel.MdSlider, min=0.0, max=1.0, step=0.25,
                         value=0.0, on_change=seen.append)
    root.box = Rect(0, 0, 100, 32)

    fire(root, "drag", SimpleNamespace(x=60, y=10))
    assert seen[-1] == 0.5

    fire(root, "drag", SimpleNamespace(x=200, y=10))
    assert seen[-1] == 1.0

    fire(root, "drag", SimpleNamespace(x=-20, y=10))
    assert seen[-1] == 0.0


def test_textfield_error_color():
    _r, _s, root = build(sel.MdTextField, value="x", error=True)
    assert root.props["_line"].props["bg"] == current_theme().role("error")


def test_textfield_label_float():
    _r, _s, root = build(sel.MdTextField, value="x", label="Name")
    label = root.props["_label"]
    assert label.props["y"] == 8
    assert label.props["size"] == 12


def test_chip_selected_variant():
    _r, _s, root = build(sel.MdChip, variant="filter", label="Tag", selected=True)
    assert root.props["bg"] == current_theme().role("secondaryContainer")


def test_a11y_on_all():
    expectations = {
        sel.MdCheckbox: sel.A11yRole.CHECKBOX,
        sel.MdRadioButton: sel.A11yRole.CHECKBOX,
        sel.MdSwitch: sel.A11yRole.SWITCH,
        sel.MdSlider: sel.A11yRole.SWITCH,
        sel.MdTextField: sel.A11yRole.TEXT,
        sel.MdChip: sel.A11yRole.BUTTON,
    }
    for cls, role in expectations.items():
        _r, shell, _root = build(cls)
        found = [sel.get_a11y(n) for n in shell.walk() if sel.get_a11y(n) is not None]
        assert found, f"{cls.name} 缺少无障碍信息"
        assert found[0]["role"] == role


def test_chip_filter_a11y_checkbox():
    _r, shell, _root = build(sel.MdChip, variant="filter", selected=True)
    found = [sel.get_a11y(n) for n in shell.walk() if sel.get_a11y(n) is not None]
    assert found[0]["role"] == sel.A11yRole.CHECKBOX
    assert found[0]["checked"] is True


def test_no_hardcoded_hex():
    path = os.path.join(os.path.dirname(sel.__file__), "selection.py")
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", source), "组件文件出现了硬编码颜色"
