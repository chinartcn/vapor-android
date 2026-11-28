"""navigation.py 修复验证：多栈隔离、current 回退、生命周期与结果回传。

    PYTHONPATH=python python3 -m pytest python/tests/test_fix_navigation.py -q
"""

from __future__ import annotations

import os
import sys

try:  # 已安装时优先用安装版
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from Vapor import ui as V  # noqa: E402
from Vapor.ui import app as _app_mod  # noqa: E402
from Vapor.ui import navigation as _nav_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_nav_state():
    """每个测试独立：清空注册表 / App.current，避免全局状态互相污染。"""
    def reset():
        _nav_mod._NAVIGATORS[:] = []
        _nav_mod.Navigator.current = None
        _app_mod.App.current = None
    reset()
    yield
    reset()


class Home(V.Page):
    template = '<Page title="HOME"><Text>HOME</Text></Page>'


class Detail(V.Page):
    template = '<Page title="DETAIL"><Text>DETAIL</Text></Page>'


def _root(initial):
    class Root(V.Component):
        template = '<PageStack :initial="initial" />'
        components = {"Page": V.Page}

        def setup(self, props, ctx):
            return {"initial": initial}
    return Root


def _mount(initial):
    surface = V.Surface(360, 640, use_native=False)
    app = V.create_app(_root(initial), width=360, height=640, surface=surface)
    return app.mount()


# ---------------------------------------------------------------------------
# F-1：多 PageStack / 多 App 隔离
# ---------------------------------------------------------------------------

def test_multi_app_navigation_isolation():
    app1 = _mount(Home)
    nav1 = V.Navigator.current
    app2 = _mount(Home)
    nav2 = V.Navigator.current
    assert nav1 is not nav2
    assert V.App.current is app2

    # 快捷函数按当前 App 解析，不会发到第一个 App 的栈
    V.navigate(Detail, animate=False)
    assert (nav2.depth, nav1.depth) == (2, 1)

    # 显式指定 app 时发到对应栈，另一个栈不受影响
    V.navigate(Detail, animate=False, app=app1)
    assert (nav1.depth, nav2.depth) == (2, 2)

    # go_back 走同一解析路径
    V.go_back(animate=False, app=app1)
    assert (nav1.depth, nav2.depth) == (1, 2)
    V.go_back(animate=False)
    assert (nav1.depth, nav2.depth) == (1, 1)
    app2.unmount()
    app1.unmount()


def test_current_falls_back_after_app_unmount():
    app1 = _mount(Home)
    nav1 = V.Navigator.current
    app2 = _mount(Home)
    nav2 = V.Navigator.current
    assert nav2.parent is nav1

    app2.unmount()
    assert V.Navigator.current is nav1  # 回退到上一层 stack
    app1.unmount()
    assert V.Navigator.current is None


def test_current_falls_back_after_nested_stack_close():
    class Nested(V.Component):
        template = '<PageStack />'

    app = _mount(Home)
    parent = V.Navigator.current
    parent.push(Nested, animate=False)
    child = V.Navigator.current
    assert child is not parent and child.parent is parent

    child.close()
    assert V.Navigator.current is parent
    app.unmount()


def test_pop_nested_page_restores_parent_navigator():
    class Nested(V.Component):
        template = '<PageStack />'

    app = _mount(Home)
    parent = V.Navigator.current
    parent.push(Nested, animate=False)
    child = V.Navigator.current
    child.push(Detail, animate=False)
    assert V.Navigator.current is child

    parent.pop(animate=False)  # 弹出承载嵌套页面栈的页面
    assert V.Navigator.current is parent
    assert child not in _nav_mod._NAVIGATORS
    app.unmount()


# ---------------------------------------------------------------------------
# F-2：页面生命周期与结果回传
# ---------------------------------------------------------------------------

def test_push_pop_lifecycle_order():
    events = []

    class HomeLife(V.Page):
        template = '<Page title="HOME"><Text>HOME</Text></Page>'

        def on_show(self):
            events.append("home:show")

        def on_hide(self):
            events.append("home:hide")

    class DetailLife(V.Page):
        template = '<Page title="DETAIL"><Text>DETAIL</Text></Page>'

        def on_show(self):
            events.append("detail:show")

        def on_hide(self):
            events.append("detail:hide")

    app = _mount(HomeLife)
    nav = V.Navigator.current
    assert events == ["home:show"]

    nav.push(DetailLife, animate=False)
    assert events == ["home:show", "home:hide", "detail:show"]

    nav.pop(animate=False)
    assert events == ["home:show", "home:hide", "detail:show",
                      "detail:hide", "home:show"]
    app.unmount()


def test_pop_result_delivered_to_previous_page():
    events = []

    class HomeResult(V.Page):
        template = '<Page title="HOME"><Text>HOME</Text></Page>'

        def on_result(self, result):
            events.append(("result", result))

    app = _mount(HomeResult)
    nav = V.Navigator.current
    nav.push(Detail, animate=False)
    nav.pop(animate=False, result={"ok": True})
    assert events == [("result", {"ok": True})]

    # 不传 result 时不触发 on_result，保持向后兼容
    nav.push(Detail, animate=False)
    nav.pop(animate=False)
    assert events == [("result", {"ok": True})]

    # go_back(result=...) 走同一路径
    nav.push(Detail, animate=False)
    V.go_back(animate=False, result=42)
    assert events == [("result", {"ok": True}), ("result", 42)]
    app.unmount()


def test_optional_callbacks_via_props_and_push_kwargs():
    events = []

    class DetailCb(Detail):
        def on_show(self):
            events.append("class:show")

    app = _mount(Home)
    nav = V.Navigator.current
    nav.push(DetailCb, animate=False,
             on_show=lambda: events.append("kw:show"),
             on_hide=lambda: events.append("kw:hide"))
    assert events == ["class:show", "kw:show"]  # 类钩子先于外部回调

    nav.push(Detail, animate=False,
             props={"on_show": lambda: events.append("props:show")})
    assert events[-1] == "props:show"
    nav.pop(animate=False, result="x")
    assert events == ["class:show", "kw:show", "kw:hide", "props:show",
                      "class:show", "kw:show"]  # 恢复的页面重收 on_show（含外部回调）
    app.unmount()


def test_handle_back_uses_same_lifecycle_path():
    events = []

    class HomeBack(V.Page):
        template = '<Page title="HOME"><Text>HOME</Text></Page>'

        def on_show(self):
            events.append("home:show")

    app = _mount(HomeBack)
    nav = V.Navigator.current
    nav.push(Detail, animate=False)
    assert app.handle_back() is True
    assert nav.depth == 1
    assert events.count("home:show") == 2  # 首次入栈 + 返回后恢复
    app.unmount()


# ---------------------------------------------------------------------------
# F-3：pop 时 dispose 触发组件 cleanup
# ---------------------------------------------------------------------------

def test_pop_dispose_triggers_cleanup():
    cleaned = []

    class DetailClean(Detail):
        def setup(self, props, ctx):
            ctx.on_cleanup(lambda: cleaned.append("cleanup"))
            return {}

    app = _mount(Home)
    nav = V.Navigator.current
    page = nav.push(DetailClean, animate=False)
    assert cleaned == []
    nav.pop(animate=False)
    assert page._disposed is True
    assert cleaned == ["cleanup"]
    app.unmount()
