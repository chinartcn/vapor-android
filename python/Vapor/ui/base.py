"""绘制节点与基础几何。"""

from __future__ import annotations

__all__ = ["Rect", "Node"]


class Rect:
    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x=0.0, y=0.0, w=0.0, h=0.0):
        self.x, self.y, self.w, self.h = float(x), float(y), float(w), float(h)

    def contains(self, px, py) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def __repr__(self):
        return f"Rect({self.x:.0f},{self.y:.0f},{self.w:.0f}x{self.h:.0f})"


class Node:
    """一个真实的绘制节点。没有 vnode，属性改了就是改了。"""

    __slots__ = (
        "tag", "props", "children", "parent", "box", "events", "text",
        "effects", "scroll_y", "clip", "component", "key", "extra", "visible",
        "pressed", "hover",
    )

    def __init__(self, tag, props=None, text=""):
        self.tag = tag
        self.props = props if props is not None else {}
        self.children = []
        self.parent = None
        self.box = Rect()
        self.events = {}
        self.text = text
        self.effects = []
        self.scroll_y = 0.0
        self.clip = False
        self.component = None
        self.key = None
        self.extra = {}
        self.visible = True
        self.pressed = False   # 手指按下期间为 True（press/release 事件同步维护）
        self.hover = False     # 鼠标/触控板悬停（桌面调试用）

    def add(self, *nodes):
        for node in nodes:
            if node is None:
                continue
            if isinstance(node, (list, tuple)):
                self.add(*node)
                continue
            node.parent = self
            self.children.append(node)
        return self

    def remove(self):
        if self.parent is not None:
            try:
                self.parent.children.remove(self)
            except ValueError:
                pass
            self.parent = None

    def dispose(self):
        for handle in self.effects:
            try:
                handle.stop()
            except Exception:
                pass
        self.effects.clear()
        for child in list(self.children):
            child.dispose()
        self.children.clear()

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def __repr__(self):
        return f"<{self.tag} {self.props if self.tag != '#component' else ''}>"


_FRAGMENT = "#fragment"
_TEXT = "#text"
_COMPONENT = "#component"


def _flow(node):
    """展开 fragment，得到真正参与布局的子节点。"""
    out = []
    for child in node.children:
        if child.tag == _FRAGMENT:
            out.extend(_flow(child))
        else:
            out.append(child)
    return out
