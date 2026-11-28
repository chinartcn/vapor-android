"""MD3 反馈/叠层类组件验收测试（内建组件库执行文档 4）。"""

from __future__ import annotations

import os
import re
from types import SimpleNamespace

import Vapor.md3.feedback as fb
from Vapor.theme import current_theme
from Vapor.ui import COMPONENTS as UI_COMPONENTS
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
    names = [c.name for c in fb.COMPONENTS]
    assert names == ["MdDialog", "MdSnackbar", "MdBadge",
                     "MdProgressIndicator", "MdTooltip", "MdBottomSheet"]
    assert len(names) == len(set(names))
    assert all(name not in UI_COMPONENTS for name in names)

    data = fb.manifest()
    assert data["abi"] == 1
    assert data["name"] == "vapor.md3.feedback"
    assert [c["name"] for c in data["components"]] == names


def test_dialog_open_visibility():
    _r, _s, closed = build(fb.MdDialog, open=False)
    assert closed.visible is False

    _r2, _s2, opened = build(fb.MdDialog, open=True, title="T", text="B")
    assert opened.visible is True
    assert "_scrim" in opened.props
    assert "_panel" in opened.props


def test_dialog_scrim_theme_color():
    _r, _s, root = build(fb.MdDialog, open=True)
    bg = root.props["_scrim"].props["bg"]
    assert (bg >> 24) == int(255 * 0.32)
    theme = current_theme()
    if "scrim" in theme.colors:
        assert bg == theme.with_opacity("scrim", 0.32)


def test_snackbar_auto_dismiss_cleanup(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(fb, "_now", lambda: clock["now"])

    seen = []
    _r, shell, root = build(fb.MdSnackbar, visible=True, duration=1000,
                            message="hi", on_dismiss=lambda: seen.append(1))
    tick = root.props["_state"]["tick"]

    clock["now"] = 100.5
    tick()
    assert seen == []

    clock["now"] = 102.0
    tick()
    assert seen == [1]

    # 卸载后计时不再回调
    seen.clear()
    _r2, shell2, root2 = build(fb.MdSnackbar, visible=True, duration=1000,
                               on_dismiss=lambda: seen.append(1))
    tick2 = root2.props["_state"]["tick"]
    shell2.dispose()
    clock["now"] = 999.0
    tick2()
    assert seen == []


def test_badge_max_and_dot():
    _r, _s, root = build(fb.MdBadge, value=150, max=99)
    assert root.props["_text"].text == "99+"
    assert root.props["bg"] == current_theme().role("error")

    _r2, _s2, dot = build(fb.MdBadge, value=None)
    assert dot.props["w"] == 6
    assert dot.props["_text"].visible is False


def test_progress_determinate_ratio():
    _r, _s, root = build(fb.MdProgressIndicator, kind="linear",
                         determinate=True, value=0.35)
    assert root.props["_fill"].props["w"] == "35.00%"
    assert root.props["_fill"].props["bg"] == current_theme().role("primary")


def test_progress_reduced_motion_static(monkeypatch):
    monkeypatch.setattr(fb, "reduced_motion", lambda: True)
    _r, _s, root = build(fb.MdProgressIndicator, kind="linear",
                         determinate=False, value=0.0)
    assert root.props["_fill"].props["x"] == "0.00%"
    assert root.props["_fill"].props["w"] == "33.33%"


def test_tooltip_visible_prop():
    seen = []
    _r, _s, root = build(fb.MdTooltip, text="tip", visible=True,
                         on_visible_change=seen.append)
    assert root.visible is True
    assert root.props["_text"].text == "tip"
    fire(root, "click")
    assert seen == [False]


def test_bottom_sheet_theme():
    _r, _s, root = build(fb.MdBottomSheet, open=True, title="Sheet")
    theme = current_theme()
    assert root.props["_panel"].props["bg"] == theme.role("surfaceContainerLow")
    assert root.props["_handle"].props["bg"] == theme.role("outlineVariant")


def test_a11y_on_all():
    cases = [
        (fb.MdDialog, fb.A11yRole.NAVIGATION, {"open": True}),
        (fb.MdSnackbar, fb.A11yRole.TEXT, {"visible": True, "message": "m"}),
        (fb.MdBadge, fb.A11yRole.TEXT, {"value": 3}),
        (fb.MdProgressIndicator, fb.A11yRole.TEXT, {"determinate": False}),
        (fb.MdTooltip, fb.A11yRole.TEXT, {"visible": True, "text": "t"}),
        (fb.MdBottomSheet, fb.A11yRole.NAVIGATION, {"open": True}),
    ]
    for cls, role, props in cases:
        _r, shell, _root = build(cls, **props)
        found = [fb.get_a11y(n) for n in shell.walk() if fb.get_a11y(n) is not None]
        assert found, f"{cls.name} 缺少无障碍信息"
        assert found[0].role == role

    _r, shell, _root = build(fb.MdProgressIndicator, determinate=False)
    a11y = [fb.get_a11y(n) for n in shell.walk() if fb.get_a11y(n) is not None][0]
    assert a11y.state.get("busy") is True


def test_no_hardcoded_hex():
    path = os.path.join(os.path.dirname(fb.__file__), "feedback.py")
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", source), "组件文件出现了硬编码颜色"
