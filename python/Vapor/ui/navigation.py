"""页面栈与导航。"""

from __future__ import annotations

from .renderer import Component, register

__all__ = ["Navigator", "PageStack", "navigate", "go_back"]


class Navigator:
    """页面栈（仿 Android Activity 栈：push / pop / replace + 滑动转场）。"""

    current = None

    def __init__(self, renderer, app, container):
        self.renderer = renderer
        self.app = app
        self.container = container
        self.stack = []  # [(wrapper, page_node, page_cls, props)]

    @property
    def depth(self):
        return len(self.stack)

    def _width(self):
        return float(getattr(self.app, "width", 360) or 360)

    def push(self, page_cls, props=None, animate=True):
        r = self.renderer
        wrapper = r.element(
            "view",
            {"absolute": True, "x": self._width() if animate else 0, "y": 0,
             "w": "100%", "h": "100%", "dir": "column"},
        )
        page_node = page_cls.instantiate(r, dict(props or {}), {})
        page_node.props["w"] = "100%"
        page_node.props["h"] = "100%"
        wrapper.add(page_node)
        self.container.add(wrapper)
        previous = self.stack[-1][0] if self.stack else None
        self.stack.append((wrapper, page_node, page_cls, dict(props or {})))

        if animate and self.app is not None:
            width = self._width()
            prev_x = 0.0

            def frame(t):
                wrapper.props["x"] = (1.0 - t) * width
                if previous is not None:
                    previous.props["x"] = -0.35 * width * t

            def done():
                if previous is not None:
                    previous.props["x"] = 0

            self.app.animate(0.26, frame, on_done=done)
        return page_node

    def pop(self, animate=True):
        if len(self.stack) <= 1:
            return None
        wrapper, page_node, _cls, _props = self.stack.pop()
        under = self.stack[-1][0] if self.stack else None
        if animate and self.app is not None:
            width = self._width()
            start = float(wrapper.props.get("x") or 0)

            def frame(t):
                wrapper.props["x"] = start + (width - start) * t
                if under is not None:
                    under.props["x"] = -0.35 * width * (1.0 - t)

            def done():
                page_node.dispose()
                wrapper.remove()

            self.app.animate(0.22, frame, on_done=done)
        else:
            page_node.dispose()
            wrapper.remove()
        return page_node

    def replace(self, page_cls, props=None, animate=False):
        if self.stack:
            wrapper, page_node, _cls, _props = self.stack.pop()
            page_node.dispose()
            wrapper.remove()
        return self.push(page_cls, props, animate=False)

    def clear(self):
        while self.stack:
            wrapper, page_node, _cls, _props = self.stack.pop()
            page_node.dispose()
            wrapper.remove()


class PageStack(Component):
    """页面栈容器：放在 App 根部，之后用 V.Navigator.current.push(...) 导航。"""

    name = "PageStack"
    props = {"initial": None}

    @staticmethod
    def build(r, props, slots):
        container = r.element("view", {"dir": "column", "w": "100%", "h": "100%"})
        app = getattr(r, "app", None)
        nav = Navigator(r, app, container)
        Navigator.current = nav
        initial = props.get("initial")
        if initial is not None:
            if isinstance(initial, type):
                nav.push(initial, {}, animate=False)
            elif callable(initial):
                page = initial()
                nav.push(page, {}, animate=False)
        return [container]


def navigate(page_cls, props=None, animate=True):
    """快捷导航：等价于 Navigator.current.push(page_cls, props)。"""
    nav = Navigator.current
    if nav is None:
        raise RuntimeError("当前没有 PageStack，无法导航")
    return nav.push(page_cls, props, animate)


def go_back(animate=True):
    nav = Navigator.current
    if nav is None:
        return None
    return nav.pop(animate)


register(PageStack)
