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
        "pressed", "hover", "instance", "_disposed",
        # 上一帧布局后的 box（Rect 或 None），供脏区 old/new 对比。
        "prev_box",
    )

    def __init__(self, tag, props=None, text=""):
        self.tag = tag
        self.props = props if props is not None else {}
        self.children = []
        self.parent = None
        self.box = Rect()
        self.prev_box = None
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
        self.instance = None   # 组件节点持有 _Instance，dispose 时触发卸载钩子
        self._disposed = False # dispose 幂等：递归/重复卸载只触发一次

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
        # 幂等：v-if 切换 / v-for 重排可能多次 dispose 同一棵子树，
        # 卸载钩子与清理函数只允许执行一次。
        if self._disposed:
            return
        self._disposed = True
        try:
            instance = self.instance
            if instance is not None:
                on_unmounted = getattr(instance, "on_unmounted", None)
                if callable(on_unmounted):
                    try:
                        on_unmounted(getattr(instance, "ctx", None))
                    except Exception as exc:  # 钩子异常不应阻断卸载
                        from .renderer import _warn

                        _warn(f"组件卸载钩子失败: {exc}")
            for handle in self.effects:
                try:
                    handle.stop()
                except Exception:
                    pass
            self.effects.clear()
            for child in list(self.children):
                child.dispose()
            self.children.clear()
        finally:
            self.instance = None

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
