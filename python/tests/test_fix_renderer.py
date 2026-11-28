"""renderer.py 契约修复的回归测试。

    PYTHONPATH=python python3 -m pytest python/tests/test_fix_renderer.py -q
"""

from __future__ import annotations

import functools

import pytest

import Vapor.ui as V
import Vapor.ui.renderer as R


def texts(node):
    return [n.text for n in node.walk() if n.tag == "#text"]


def surface():
    return V.Surface(240, 160, use_native=False)


def test_hotplug_component_after_mount():
    """挂载后 register_provider 加载的新组件，下一帧就能解析并渲染。"""

    class HotPlugFix(V.Component):
        name = "HotPlugFix"
        template = '<Text>HOT</Text>'

    holder = {}

    class Root(V.Component):
        template = '<View><HotPlugFix v-if="ready" /></View>'

        def setup(self, props, ctx):
            holder["ready"] = V.ref(False)
            return {"ready": holder["ready"]}

    try:
        app = V.App(Root, surface=surface()).mount()
        assert app.renderer.resolve("HotPlugFix") is None
        V.register(HotPlugFix)                       # 运行中加载
        assert app.renderer.resolve("hot-plug-fix") is HotPlugFix  # 归一化匹配
        holder["ready"].value = True
        app.tick()
        assert "HOT" in texts(app.root)
        app.unmount()
    finally:
        V.COMPONENTS.pop("HotPlugFix", None)


def test_missing_component_fallback_and_strict(capsys):
    """未注册组件默认降级为占位文本并告警一次；严格模式抛错。"""

    class Root(V.Component):
        template = '<View><NoSuchWidgetFix /></View>'

    R._WARNED_MISSING.clear()
    app = V.App(Root, surface=surface()).mount()
    assert "[缺少 NoSuchWidgetFix]" in texts(app.root)
    assert "未注册的组件" in capsys.readouterr().err
    app.tick()
    assert "未注册的组件" not in capsys.readouterr().err  # 一次性告警
    app.unmount()

    renderer = V.Renderer(strict=True)
    with pytest.raises(KeyError, match="未注册的组件"):
        renderer.component_node("StillMissingFix", {}, {})

    old = R.STRICT_MISSING_COMPONENTS
    R.STRICT_MISSING_COMPONENTS = True
    try:
        with pytest.raises(KeyError, match="未注册的组件"):
            V.Renderer().component_node("StrictGlobalFix", {}, {})
    finally:
        R.STRICT_MISSING_COMPONENTS = old


def test_lifecycle_hooks_and_cleanup():
    """mounted 在首帧树建好后触发，dispose 触发 unmounted + cleanups（幂等）。"""
    events = []
    holder = {}

    class LifeFix(V.Component):
        name = "LifeFix"
        template = '<Text>LIFE</Text>'

        def setup(self, props, ctx):
            ctx.on_mounted(lambda c: events.append("mounted"))
            ctx.on_unmounted(lambda c: events.append("unmounted"))
            ctx.on_cleanup(lambda: events.append("cleanup"))
            return {}

        def mounted(self):
            events.append("class-mounted")

        def unmounted(self, ctx):
            events.append("class-unmounted")

    class Root(V.Component):
        template = '<View><LifeFix v-if="show" /></View>'
        components = {"LifeFix": LifeFix}

        def setup(self, props, ctx):
            holder["show"] = V.ref(True)
            return {"show": holder["show"]}

    try:
        app = V.App(Root, surface=surface()).mount()
        assert events == ["class-mounted", "mounted"]

        life = next(n for n in app.root.walk()
                    if n.tag == "#component" and n.component is LifeFix)
        holder["show"].value = False
        app.tick()
        assert events == ["class-mounted", "mounted",
                          "class-unmounted", "unmounted", "cleanup"]

        life.dispose()
        life.dispose()
        assert events.count("cleanup") == 1          # 递归/重复 dispose 只清理一次
        app.unmount()
    finally:
        V.COMPONENTS.pop("LifeFix", None)


def test_props_type_validation():
    """默认值写法与 schema 写法都用，类型错误给出清晰报错。"""

    class TypedFix(V.Component):
        props = {"size": {"type": int, "default": 16}, "label": "x"}
        template = '<Text>{{ size }}</Text>'

    class RequiredFix(V.Component):
        props = {"size": {"type": int, "required": True}}
        template = '<Text>{{ size }}</Text>'

    node = TypedFix.instantiate(V.Renderer(), {"size": 20}, {})
    assert node.props["size"] == 20 and node.props["label"] == "x"

    with pytest.raises(TypeError, match=r"组件 TypedFix 的 prop size 类型错误"):
        TypedFix.instantiate(V.Renderer(), {"size": "big"}, {})
    with pytest.raises(TypeError, match=r"缺少必填 prop size"):
        RequiredFix.instantiate(V.Renderer(), {}, {})


def test_register_conflict_and_replace():
    """同名注册默认抛 ValueError，replace=True 覆盖。"""

    class A(V.Component):
        name = "ConflictFix"

    class B(V.Component):
        name = "ConflictFix"

    try:
        assert V.register(A) is A
        with pytest.raises(ValueError, match=r"组件名冲突: ConflictFix 已注册，使用 replace=True 覆盖"):
            V.register(B)
        assert V.register(B, replace=True) is B
        assert V.COMPONENTS["ConflictFix"] is B

        V.define_component("ConflictQuickFix", template="<View />")
        with pytest.raises(ValueError, match=r"组件名冲突"):
            V.define_component("ConflictQuickFix", template="<View />")
        assert V.define_component("ConflictQuickFix", template="<View />", replace=True)
    finally:
        V.COMPONENTS.pop("ConflictFix", None)
        V.COMPONENTS.pop("ConflictQuickFix", None)


def test_setup_binding_variants():
    """setup(props, ctx) / setup(self, props, ctx) / setup(props) 严格绑定。"""
    try:
        two = V.define_component(
            "SetupTwoFix", template='<Text>{{ v }}</Text>',
            setup=lambda props, ctx: {"v": "TWO"},
        )
        assert "TWO" in texts(two.instantiate(V.Renderer(), {}, {}))

        class MethodFix(V.Component):
            template = '<Text>{{ v }}</Text>'

            def setup(self, props, ctx):
                return {"v": "METHOD"}

        assert "METHOD" in texts(MethodFix.instantiate(V.Renderer(), {}, {}))

        one = V.define_component(
            "SetupOneFix", template='<Text>{{ v }}</Text>',
            setup=lambda props: {"v": "ONE"},
        )
        assert "ONE" in texts(one.instantiate(V.Renderer(), {}, {}))

        # partial / 默认值 / *args 也能绑定
        def with_default(props, ctx=None):
            return {"v": ctx or "DEFAULT"}

        partial = V.define_component(
            "SetupPartialFix", template='<Text>{{ v }}</Text>',
            setup=functools.partial(with_default, ctx="PARTIAL"),
        )
        assert "PARTIAL" in texts(partial.instantiate(V.Renderer(), {}, {}))

        class BadFix(V.Component):
            template = '<Text>x</Text>'

            def setup(self, props, ctx, extra):
                return {}

        with pytest.raises(TypeError, match=r"setup 签名不兼容"):
            BadFix.instantiate(V.Renderer(), {}, {})
    finally:
        for name in ("SetupTwoFix", "SetupOneFix", "SetupPartialFix"):
            V.COMPONENTS.pop(name, None)


def test_keyed_reuse_keeps_setup_snapshot():
    """key 复用：节点与 effect 复用，props 由 bind_attr 更新，setup 快照不重算。"""
    from Vapor import core

    holder = {"items": ["a"]}

    class ItemFix(V.Component):
        props = {"label": ""}
        template = '<Text>{{ snapshot }}-{{ label }}</Text>'

        def setup(self, props, ctx):
            return {"snapshot": str(props["label"]).upper()}

    renderer = V.Renderer()
    frag = renderer.fragment()

    def build(item, index):
        return [ItemFix.instantiate(renderer, {"label": item}, {})]

    renderer.bind_for(frag, lambda: list(holder["items"]), build, lambda item, i: i)
    core.flush_jobs()
    first = frag.children[0]
    assert "A-a" in texts(first)

    holder["items"] = ["b"]
    core.flush_jobs()
    assert frag.children[0] is first                 # 同 key 复用节点
    assert first.props["label"] == "a"               # 复用不重跑 build / 绑定

    # 后续动态属性更新走 bind_attr 的 effect，setup 派生值不会跟着重算
    renderer.bind_attr(first, "label", lambda: "b")
    core.flush_jobs()
    assert first.props["label"] == "b"
    assert "A-b" in texts(first)     # props 响应式更新，但 snapshot 仍是 setup 时的 A


def test_scope_props_readonly_and_shadow_warning(capsys):
    """props 只读：写入告警并走 on_change；bindings 遮蔽 props 时告警。"""
    R._WARNED_SHADOW.clear()
    R._WARNED_PROPS.clear()
    written = []
    scope = V._ComponentScope(
        bindings={"x": "bound"},
        props={"x": "prop", "y": "value", "on_change": lambda value: written.append(value)},
    )
    assert scope["x"] == "bound"
    assert "遮蔽" in capsys.readouterr().err

    scope["y"] = "new"
    assert written == ["new"]                        # 回写父级
    assert scope["y"] == "value"                     # 本地 props 没被改写
    assert "props 只读" in capsys.readouterr().err
