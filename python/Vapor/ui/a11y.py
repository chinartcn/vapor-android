"""MD3 无障碍语义层（最小实现）。

本阶段只把语义数据挂到 ``ui.Node`` 上，供后续 SDK/TalkBack 阶段导出给
宿主；不接 JNI、不影响现有渲染。

基本规则：
- 焦点必须有可见环状指示（focus 10% state layer + 描边）；
- Escape 关闭 modal 并清除焦点；
- 状态变化至少要有两个视觉指示。
"""

from __future__ import annotations

__all__ = ["A11yNode", "Role", "set_a11y", "get_a11y"]


class Role:
    BUTTON = "button"
    CHECKBOX = "checkbox"
    SWITCH = "switch"
    TEXT = "text"
    IMAGE = "image"
    LIST_ITEM = "listItem"
    NAVIGATION = "navigation"


_ROLE_LABELS = {
    Role.BUTTON: "button",
    Role.CHECKBOX: "checkbox",
    Role.SWITCH: "switch",
    Role.TEXT: "text",
    Role.IMAGE: "image",
    Role.LIST_ITEM: "listItem",
    Role.NAVIGATION: "navigation",
}

_STATE_ORDER = ("checked", "disabled", "focused", "selected", "expanded", "busy")


class A11yNode:
    """一个节点的无障碍语义。"""

    __slots__ = ("role", "label", "state")

    def __init__(self, role: str, label: str = "", state: dict | None = None):
        self.role = str(role)
        self.label = str(label or "")
        self.state = dict(state or {})

    def announce(self) -> str:
        """生成供 TalkBack 朗读的文本（英文状态词，便于跨语言解析）。"""
        parts: list[str] = []
        if self.label:
            parts.append(self.label)
        parts.append(_ROLE_LABELS.get(self.role, self.role))
        for key in _STATE_ORDER:
            if self.state.get(key):
                parts.append(key)
        return ", ".join(parts)

    def __repr__(self) -> str:
        return f"A11yNode(role={self.role!r}, label={self.label!r}, state={self.state!r})"


def set_a11y(node, *, role: str, label: str = "", **state) -> None:
    """把语义数据挂到节点上（本阶段只存数据，不改渲染）。"""
    a11y = A11yNode(role, label, state)
    extra = getattr(node, "extra", None)
    if isinstance(extra, dict):
        extra["a11y"] = a11y
    else:  # 非标准节点（如测试替身）降级为普通属性。
        node.a11y = a11y


def get_a11y(node) -> A11yNode | None:
    """取节点的语义数据；没有则返回 None。"""
    extra = getattr(node, "extra", None)
    if isinstance(extra, dict):
        return extra.get("a11y")
    return getattr(node, "a11y", None)
