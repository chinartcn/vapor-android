"""页面栈与导航。"""

from __future__ import annotations

import functools
import inspect

from .renderer import Component, register, _warn

__all__ = ["Navigator", "PageStack", "navigate", "go_back"]

# 导航器注册表：末尾为最近活跃的导航器。快捷函数优先按 App 上下文解析，
# 不再盲目依赖全局 Navigator.current（仅保留为兼容属性）。
_NAVIGATORS = []
_NO_RESULT = object()
_WARNED_AMBIGUOUS = False
_LIFECYCLE = ("on_show", "on_hide", "on_result")


def _is_live(nav):
    container = getattr(nav, "container", None)
    return container is not None and not getattr(container, "_disposed", False)


def _live_navigators():
    alive = [n for n in _NAVIGATORS if _is_live(n)]
    if len(alive) != len(_NAVIGATORS):
        _NAVIGATORS[:] = alive
        _sync_current()
    return list(alive)


def _sync_current():
    cur = Navigator.current
    if cur is not None and (not _is_live(cur) or cur not in _NAVIGATORS):
        Navigator.current = _NAVIGATORS[-1] if _NAVIGATORS else None


def _activate(nav):
    """把导航器移到活跃栈顶，并同步兼容属性 Navigator.current 与 App 关联。"""
    if nav in _NAVIGATORS:
        _NAVIGATORS.remove(nav)
    _NAVIGATORS.append(nav)
    Navigator.current = nav
    app = getattr(nav, "app", None)
    if app is not None:
        navs = getattr(app, "_navigators", None)
        if navs is None:
            navs = app._navigators = []
        if nav not in navs:
            navs.append(nav)
        app._navigator = nav


def _deactivate(nav):
    """导航器销毁/卸载：从注册表移除，current 回退到上一层 stack。"""
    if nav in _NAVIGATORS:
        _NAVIGATORS.remove(nav)
    app = getattr(nav, "app", None)
    if app is not None and getattr(app, "_navigator", None) is nav:
        siblings = [n for n in reversed(_NAVIGATORS) if n.app is app]
        app._navigator = siblings[0] if siblings else None
    if Navigator.current is nav:
        parent = getattr(nav, "parent", None)
        if parent is None or parent not in _NAVIGATORS or not _is_live(parent):
            parent = _NAVIGATORS[-1] if _NAVIGATORS else None
        Navigator.current = parent


def close_app_navigators(app):
    """App 卸载时调用：关闭该 App 的全部导航器，current 回退到上一层栈。"""
    for nav in list(reversed(_NAVIGATORS)):
        if nav.app is app:
            _deactivate(nav)


def _resolve_navigator(app=None):
    """按“当前活跃 App”解析导航器；多实例时给出告警并按上下文隔离。"""
    global _WARNED_AMBIGUOUS
    navs = _live_navigators()
    if app is None:
        try:
            from .app import App
            app = App.current
        except Exception:  # pragma: no cover - 导入异常时退回注册表
            app = None
    if app is not None:
        mine = [n for n in reversed(navs) if n.app is app]
        if mine:
            return mine[0]
    if not navs:
        return None
    if len(navs) > 1 and not _WARNED_AMBIGUOUS:
        _WARNED_AMBIGUOUS = True
        _warn("检测到多个 PageStack/App，导航目标不明确：请显式使用对应 Navigator 实例")
    cur = Navigator.current
    if cur is not None and cur in navs:
        return cur
    return navs[-1]


def _fire(hook, *args):
    """调用生命周期回调，兼容零参 / 带参签名，异常只告警不中断导航。"""
    if not callable(hook):
        return
    try:
        sig = inspect.signature(hook)
        params = [p for p in sig.parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        variadic = any(p.kind is p.VAR_POSITIONAL for p in sig.parameters.values())
        if variadic or len(params) >= len(args):
            hook(*args)
        elif params:
            hook(*args[:len(params)])
        else:
            hook()
    except Exception as exc:
        _warn(f"页面生命周期回调失败: {exc}")


def _page_hooks(page_node, name):
    """收集页面回调：props 回调 + 组件类方法 + setup 注册的 ctx 回调。"""
    out = []
    if page_node is None:
        return out
    instance = getattr(page_node, "instance", None)
    cls = getattr(page_node, "component", None) or getattr(instance, "cls", None)
    seen = set()
    for klass in getattr(cls, "__mro__", ()) or ():
        raw = klass.__dict__.get(name)
        if raw is None or id(raw) in seen:
            continue
        seen.add(id(raw))
        if isinstance(raw, staticmethod):
            out.append(raw.__func__)
        elif isinstance(raw, classmethod):
            out.append(functools.partial(raw.__func__, cls))
        elif instance is not None:
            out.append(functools.partial(raw, instance))
        else:
            out.append(raw)
    props = getattr(page_node, "props", None)
    if props is not None:
        try:
            cb = props.get(name)
        except Exception:
            cb = None
        if callable(cb):
            out.append(cb)
    if instance is not None:
        ctx = getattr(instance, "ctx", None)
        if ctx is not None:
            for key in (name, name[3:]):  # on_show -> show
                try:
                    cb = ctx.get(key)
                except Exception:
                    cb = None
                if callable(cb) and cb not in out:
                    out.append(cb)
    return out


def _notify(page_node, name, *args):
    for hook in _page_hooks(page_node, name):
        _fire(hook, *args)


def _dispose_page(page_node, wrapper):
    """销毁页面：先停用页面内嵌套的导航器（current 回退），再 dispose 触发 cleanup。"""
    try:
        for node in page_node.walk():
            nav = (getattr(node, "extra", None) or {}).get("navigator")
            if nav is not None:
                _deactivate(nav)
    except Exception:
        pass
    page_node.dispose()
    wrapper.remove()


class Navigator:
    """页面栈（仿 Android Activity 栈：push / pop / replace + 滑动转场）。"""

    # 兼容属性：指向最近活跃的导航器。快捷函数不再盲目依赖它。
    current = None

    def __init__(self, renderer, app, container):
        self.renderer = renderer
        self.app = app
        self.container = container
        self.stack = []  # [(wrapper, page_node, page_cls, props)]
        _live_navigators()  # 清理已卸载的旧导航器
        parent = Navigator.current
        self.parent = parent if parent is not None and parent is not self and _is_live(parent) else None
        try:
            container.extra["navigator"] = self
        except Exception:
            pass
        _activate(self)

    @property
    def depth(self):
        return len(self.stack)

    def _width(self):
        return float(getattr(self.app, "width", 360) or 360)

    def push(self, page_cls, props=None, animate=True,
             on_show=None, on_hide=None, on_result=None):
        """压入新页；旧页收到 on_hide，新页收到 on_show。

        on_show/on_hide/on_result 既可作为页面类方法，也可通过 props 或本方法的
        可选参数传入；这些参数均为向后兼容的新增可选项。
        """
        _activate(self)
        r = self.renderer
        raw_props = dict(props or {})
        lifecycle = {k: raw_props.pop(k) for k in _LIFECYCLE if k in raw_props}
        for key, cb in (("on_show", on_show), ("on_hide", on_hide), ("on_result", on_result)):
            if cb is not None:
                lifecycle[key] = cb
        previous = self.stack[-1] if self.stack else None
        if previous is not None:
            _notify(previous[1], "on_hide")
        wrapper = r.element(
            "view",
            {"absolute": True, "x": self._width() if animate else 0, "y": 0,
             "w": "100%", "h": "100%", "dir": "column"},
        )
        page_node = page_cls.instantiate(r, raw_props, {})
        for key, cb in lifecycle.items():
            page_node.props[key] = cb
        page_node.props["w"] = "100%"
        page_node.props["h"] = "100%"
        wrapper.add(page_node)
        self.container.add(wrapper)
        prev_node = previous[0] if previous is not None else None
        self.stack.append((wrapper, page_node, page_cls, dict(props or {})))
        _notify(page_node, "on_show")

        if animate and self.app is not None:
            width = self._width()

            def frame(t):
                wrapper.props["x"] = (1.0 - t) * width
                if prev_node is not None:
                    prev_node.props["x"] = -0.35 * width * t

            def done():
                if prev_node is not None:
                    prev_node.props["x"] = 0

            self.app.animate(0.26, frame, on_done=done)
        return page_node

    def pop(self, animate=True, result=_NO_RESULT):
        """弹出页面；被弹页收到 on_hide，恢复的页收到 on_show 与 on_result(result)。"""
        _activate(self)
        if len(self.stack) <= 1:
            return None
        wrapper, page_node, _cls, _props = self.stack.pop()
        under = self.stack[-1] if self.stack else None
        _notify(page_node, "on_hide")
        if under is not None:
            _notify(under[1], "on_show")
            if result is not _NO_RESULT:
                _notify(under[1], "on_result", result)

        def finish():
            _dispose_page(page_node, wrapper)

        if animate and self.app is not None:
            width = self._width()
            start = float(wrapper.props.get("x") or 0)
            under_node = under[0] if under is not None else None

            def frame(t):
                wrapper.props["x"] = start + (width - start) * t
                if under_node is not None:
                    under_node.props["x"] = -0.35 * width * (1.0 - t)

            self.app.animate(0.22, frame, on_done=finish)
        else:
            finish()
        return page_node

    def replace(self, page_cls, props=None, animate=False):
        if self.stack:
            wrapper, page_node, _cls, _props = self.stack.pop()
            _notify(page_node, "on_hide")
            _dispose_page(page_node, wrapper)
        return self.push(page_cls, props, animate=False)

    def clear(self):
        while self.stack:
            wrapper, page_node, _cls, _props = self.stack.pop()
            _notify(page_node, "on_hide")
            _dispose_page(page_node, wrapper)

    def close(self):
        """显式关闭页面栈：清空页面并从注册表移除，current 回退到上一层栈。"""
        self.clear()
        _deactivate(self)


class PageStack(Component):
    """页面栈容器：放在 App 根部，之后用 V.Navigator.current.push(...) 导航。"""

    name = "PageStack"
    props = {"initial": None}

    @staticmethod
    def build(r, props, slots):
        container = r.element("view", {"dir": "column", "w": "100%", "h": "100%"})
        app = getattr(r, "app", None)
        nav = Navigator(r, app, container)  # 构造时自动注册为当前活跃栈
        initial = props.get("initial")
        if initial is not None:
            if isinstance(initial, type):
                nav.push(initial, {}, animate=False)
            elif callable(initial):
                page = initial()
                nav.push(page, {}, animate=False)
        return [container]


def navigate(page_cls, props=None, animate=True, app=None,
             on_show=None, on_hide=None, on_result=None):
    """快捷导航：优先发往当前活跃 App 的 navigator，而非全局单例。"""
    nav = _resolve_navigator(app)
    if nav is None:
        raise RuntimeError("当前没有 PageStack，无法导航")
    return nav.push(page_cls, props, animate,
                    on_show=on_show, on_hide=on_hide, on_result=on_result)


def go_back(animate=True, result=_NO_RESULT, app=None):
    """快捷返回：与 navigate 使用同一解析路径，支持结果回传。"""
    nav = _resolve_navigator(app)
    if nav is None:
        return None
    return nav.pop(animate, result)


register(PageStack)
