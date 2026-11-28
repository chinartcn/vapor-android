"""MD3 state layer / ripple 行为验收（文档 3）。"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor.ui.a11y import Role, get_a11y, set_a11y  # noqa: E402
from Vapor.ui.base import Node  # noqa: E402
from Vapor.ui.states import (  # noqa: E402
    STATE_LAYER_SIZE,
    STATE_OPACITY,
    TOUCH_TARGET_SIZE,
    Ripple,
    State,
    StateLayer,
    apply_state_layer,
    attach_ripple,
)


def test_opacity_values():
    assert STATE_OPACITY == {
        "hover": 0.08,
        "focused": 0.10,
        "pressed": 0.10,
        "drag": 0.16,
        "disabled": 0.38,
    }


def test_sizes():
    assert STATE_LAYER_SIZE == 40
    assert TOUCH_TARGET_SIZE == 48


def test_disabled_masks_hover():
    layer = StateLayer(0xFFFFFFFF, {"disabled", "hover"})
    assert layer.resolved_priority_text() == "disabled"
    assert "hover" not in layer.resolved_priority_text()
    assert layer.resolved() == (0xFFFFFFFF, 0.38)


def test_single_layer():
    layer = StateLayer(0xFFFFFFFF, {"pressed", "hover", "focused"})
    resolved = layer.resolved()
    assert resolved is not None
    assert resolved[1] == 0.10
    assert layer.priority == "pressed"


def test_priority_order():
    layer = StateLayer(0xFFFFFFFF, {"hover"})
    assert layer.priority == "hover"
    layer.set("focused", True)
    assert layer.priority == "focused"
    layer.set("pressed", True)
    assert layer.priority == "pressed"
    layer.set("drag", True)
    assert layer.priority == "drag"
    layer.set("disabled", True)
    assert layer.priority == "disabled"
    layer.set("disabled", False)
    assert layer.priority == "drag"


def test_enabled_has_no_layer():
    layer = StateLayer(0xFF000000, {State.ENABLED})
    assert layer.resolved() is None
    assert layer.priority == "enabled"


def test_apply_state_layer_composites():
    container = 0xFFFFFFFF
    out = apply_state_layer(container, 0xFF000000, {"hover"})
    assert out != container
    assert (out >> 24) == 0xFF            # 容器 alpha 保持不变
    assert 0xEB <= ((out >> 16) & 0xFF) <= 0xED  # 255 * 0.92 ≈ 235
    # enabled 不改变颜色
    assert apply_state_layer(container, 0xFF000000, set()) == container


def test_apply_state_layer_disabled():
    out = apply_state_layer(0xFFFFFFFF, 0xFF000000, {"disabled", "hover"})
    r = (out >> 16) & 0xFF
    assert 0x9D <= r <= 0x9F              # 255 * 0.62 ≈ 158


def test_ripple_lifecycle():
    node = Node("view")
    node.box.x, node.box.y, node.box.w, node.box.h = 10, 20, 100, 40
    ripple = attach_ripple(node, 0xFFFFFFFF)
    assert isinstance(ripple, Ripple)
    assert node.extra["ripple"] is ripple
    providers = node.events["press"]
    providers[0][0]()(SimpleNamespace(x=30, y=40))
    assert ripple.active
    providers = node.events["release"]
    providers[0][0]()(SimpleNamespace(x=30, y=40))
    assert not ripple.active


def test_a11y_roundtrip():
    node = Node("view")
    set_a11y(node, role=Role.BUTTON, label="提交", disabled=True)
    a = get_a11y(node)
    assert a is not None
    assert a.role == "button"
    assert "disabled" in a.announce().lower()
    assert "提交" in a.announce()
