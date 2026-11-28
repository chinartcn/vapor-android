"""MD3 无障碍语义层验收（文档 3）。"""

from __future__ import annotations

import os
import sys

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor.ui.a11y import A11yNode, Role, get_a11y, set_a11y  # noqa: E402
from Vapor.ui.base import Node  # noqa: E402


def test_roundtrip():
    node = Node("view")
    set_a11y(node, role=Role.BUTTON, label="提交", disabled=True)
    a = get_a11y(node)
    assert a is not None
    assert a.role == "button"
    assert "disabled" in a.announce().lower()


def test_default_label_and_role_labels():
    node = Node("view")
    set_a11y(node, role=Role.CHECKBOX, label="同意", checked=True)
    a = get_a11y(node)
    assert isinstance(a, A11yNode)
    text = a.announce()
    assert text.startswith("同意")
    assert "checkbox" in text
    assert "checked" in text
    assert "disabled" not in text


def test_missing_a11y():
    assert get_a11y(Node("view")) is None


def test_role_constants():
    assert Role.BUTTON == "button"
    assert Role.CHECKBOX == "checkbox"
    assert Role.SWITCH == "switch"
    assert Role.TEXT == "text"
    assert Role.IMAGE == "image"
    assert Role.LIST_ITEM == "listItem"
    assert Role.NAVIGATION == "navigation"


def test_state_dict_is_copied():
    node = Node("view")
    state = {"selected": True}
    set_a11y(node, role=Role.LIST_ITEM, label="项目", **state)
    state["selected"] = False
    assert get_a11y(node).state["selected"] is True


def test_duck_typed_node():
    class Fake:
        pass

    node = Fake()
    set_a11y(node, role=Role.TEXT, label="标题")
    assert get_a11y(node).label == "标题"
