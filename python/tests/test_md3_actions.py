"""MD3 按钮 / 操作类组件验收（执行文档 1）。"""

from __future__ import annotations

import os
import re
import sys
from types import SimpleNamespace

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Vapor.ui as V  # noqa: E402
from Vapor import core  # noqa: E402
from Vapor.md3 import actions  # noqa: E402
from Vapor.theme import Theme, baseline_dark, current_theme, set_theme  # noqa: E402
from Vapor.ui.a11y import get_a11y  # noqa: E402
from Vapor.ui.renderer import Renderer  # noqa: E402
from Vapor.ui.states import STATE_OPACITY, StateLayer  # noqa: E402

_REG = {c.name: c for c in actions.COMPONENTS}


def build(cls, **props):
    r = Renderer(registry=_REG)
    node = r.component_node(cls.name, props, {})
    core.flush_jobs()
    return node.children[0]


def fire(node, event="click", **attrs):
    ev = SimpleNamespace(x=attrs.get("x", 0.0), y=attrs.get("y", 0.0))
    for provider, _modifiers in list(node.events.get(event, ())):
        handler = provider()
        if callable(handler):
            handler(ev)


def test_manifest_no_name_conflict():
    names = {item["name"] for item in actions.manifest()["components"]}
    assert len(names) == len(actions.COMPONENTS)
    existing = set(V.COMPONENTS)
    try:
        from Vapor import presets

        existing |= {c.name for c in presets.PRESETS}
    except Exception:
        pass
    assert names.isdisjoint(existing)


def test_button_variants_resolve_theme_roles():
    for variant, (container_role, content_role) in actions.BUTTON_ROLES.items():
        node = build(actions.MdButton, variant=variant, label=variant)
        if container_role is None:
            assert node.props["bg"] == "none"
        else:
            assert node.props["bg"] == current_theme().role(container_role)
        assert node.props["color"] == current_theme().role(content_role)


def test_button_rebinds_after_theme_change():
    old = current_theme()
    try:
        node = build(actions.MdButton, variant="filled", label="ok")
        before = node.props["bg"]
        set_theme(Theme(baseline_dark()))
        core.flush_jobs()
        assert node.props["bg"] != before
        assert node.props["bg"] == current_theme().role("primary")
    finally:
        set_theme(old)


def test_button_disabled_blocks_click():
    calls = []
    node = build(actions.MdButton, label="提交", enabled=False,
                 on_click=lambda ev: calls.append(ev))
    assert get_a11y(node).state["disabled"] is True
    fire(node, "click")
    assert calls == []


def test_state_layer_opacity():
    layer = StateLayer(0xFFFFFFFF, {"pressed"})
    resolved = layer.resolved()
    assert resolved is not None
    assert resolved[1] == STATE_OPACITY["pressed"]
    assert STATE_OPACITY == {
        "hover": 0.08, "focused": 0.10, "pressed": 0.10,
        "drag": 0.16, "disabled": 0.38,
    }


def test_reduced_motion_ripple_terminal(monkeypatch):
    monkeypatch.setenv("VAPOR_REDUCED_MOTION", "1")
    node = build(actions.MdButton, label="ok")
    ripple = node.extra["ripple"]
    assert ripple.active is False
    fire(node, "press", x=1, y=1)
    assert ripple.active is False


def test_fab_sizes():
    fab = build(actions.MdFAB)
    mini = build(actions.MdFAB, size="mini")
    ext = build(actions.MdExtendedFAB, label="新建")
    assert fab.props["h"] == 56
    assert fab.props["w"] == 56
    assert mini.props["h"] == 40
    assert ext.props["h"] == 56
    assert ext.props["min_w"] == 80


def test_segmented_single_vs_multi():
    segments = [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}]

    changes = []
    single = build(actions.MdSegmentedButton, segments=segments,
                   selected=["a"], on_change=changes.append)
    nodes = single.extra["segments"]
    assert len(nodes) == 2
    fire(nodes[1], "click")
    assert changes[-1] == ["b"]
    fire(nodes[1], "click")
    assert changes[-1] == ["b"]          # 单选不可取消

    multi_changes = []
    multi = build(actions.MdSegmentedButton, segments=segments, multiple=True,
                  selected=["a"], on_change=multi_changes.append)
    mnodes = multi.extra["segments"]
    fire(mnodes[1], "click")
    assert multi_changes[-1] == ["a", "b"]
    fire(mnodes[1], "click")
    assert multi_changes[-1] == ["a"]


def test_a11y_attached():
    for cls in actions.COMPONENTS:
        node = build(cls)
        a = get_a11y(node)
        assert a is not None, cls.name
        assert a.role, cls.name


def test_no_hardcoded_hex():
    path = os.path.join(os.path.dirname(Vapor.__file__), "md3", "actions.py")
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    assert not re.search(r"0xFF6750A4|#6750A4", source)
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", source)
