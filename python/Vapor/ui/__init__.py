"""Vapor.ui —— 无 vdom 的响应式 UI（Vue 3 Vapor 风格）。

三层：
    Node      真实的绘制节点（没有 vnode，也没有 diff）
    Renderer  把模板 / 组件变成 Node 树，动态绑定各自独立的 effect
    App       布局 → 绘制 → 上屏 → 事件分发 的主循环

坐标系左上角为原点，单位是像素。布局是一个精简的 flex：
    dir="row" | "column"、gap、padding、margin、grow、align、justify、
    w/h（数字或 "50%"）、absolute + x/y。

实现按职责拆分在同包的子模块里，这里统一再导出，保持
``import Vapor.ui as V`` 的公开用法不变：

    base.py        Rect / Node / 片段标记 / _flow
    colors.py      rgb / color / with_alpha / _num
    text.py        文本度量、字体切换、点阵回退绘制
    canvas.py      Canvas / Surface / 帧缓冲地址
    renderer.py    Renderer / Component / define_component / 注册表
    layout.py      精简 flex 布局
    paint.py       节点绘制与滚动辅助
    input.py       PointerRouter / Event 手势状态机
    navigation.py  Navigator / PageStack / navigate / go_back
    widgets.py     内置组件（View / Text / Button / Scroll / Page ...）
    app.py         App / create_app / mount / 帧循环

示例::

    import Vapor.ui as V

    class Home(V.Component):
        template = '''
          <View :bg="bg" :padding="24" dir="column" gap="12">
            <Text :text="title" size="28" color="#FFFFFF" />
            <Button text="TAP ME" :on_click="tap" />
          </View>
        '''
        def setup(self, props, ctx):
            count = V.ref(0)
            return {
                "title": V.computed(lambda: f"COUNT = {count.value}"),
                "bg": "#101418",
                "tap": lambda e=None: count.value += 1,
            }

    V.create_app(Home).mount()
"""

from __future__ import annotations

from .. import _native, core

_core = _native.core
_geom = _native.ui

from .base import Rect, Node, _flow, _FRAGMENT, _TEXT, _COMPONENT
from .colors import rgb, color, with_alpha, _with_alpha, _num
from .text import (
    set_text_renderer, set_active_font, active_font,
    text_width, line_height, wrap_text,
)
from .canvas import Canvas, Surface, _buffer_address
from .renderer import (
    Renderer, Component, _ComponentScope, _Instance, _arity,
    define_component, register, COMPONENTS, _warn,
)
from .layout import layout, _padding, _margin, _intrinsic, _measure, _align_offset, _shift_children
from .paint import _paint_node, _intersects, _scroll_max, _scroll_by
from .input import PointerRouter, Event
from .navigation import Navigator, PageStack, navigate, go_back
from .widgets import (
    View, Row, Column, Center, Stack, Spacer, Divider, Text, Button, Icon, Svg,
    ProgressBar, Badge, Switch, Slider, Scroll, ListView, TextInput, Page,
    _LAYOUT_OWN, _bind_all, _bind_pointer_callbacks, _svg_doc, _SVG_CACHE,
)
from .app import App, on_tick, create_app, mount, _later

__all__ = [
    "Node", "Rect", "Canvas", "Surface", "Renderer", "App",
    "Component", "define_component", "create_app", "mount",
    "View", "Text", "Button", "Row", "Column", "Stack", "Center", "Spacer",
    "Divider", "Scroll", "ProgressBar", "Switch", "Slider", "Icon", "Svg",
    "Page", "PageStack", "Navigator", "ListView", "Badge", "TextInput",
    "ref", "reactive", "computed", "watch", "watch_effect", "effect", "effect_scope",
    "next_tick", "provide", "inject", "batch", "unref", "is_ref",
    "rgb", "color", "with_alpha", "on_tick", "set_text_renderer",
    "set_active_font", "active_font",
]

# 重新导出 core 常用 API，方便 import Vapor.ui as V 后直接使用
ref = core.ref
shallow_ref = core.shallow_ref
reactive = core.reactive
computed = core.computed
watch = core.watch
watch_effect = core.watch_effect
effect = core.effect
effect_scope = core.effect_scope
next_tick = core.next_tick
provide = core.provide
inject = core.inject
batch = core.batch
unref = core.unref
is_ref = core.is_ref
readonly = core.readonly


# 内建现代化圆角组件（Card / Fab / Chip / Dialog ...）：导入即注册。
# 放在模块最后导入，避免循环依赖（presets 反向 import ui）。
try:  # pragma: no cover - 单纯为了让预设可用
    from .. import presets as _presets  # noqa: F401,E402
except Exception as _exc:  # 预设出问题不应该拖垮整个 UI 框架
    import sys as _sys
    print(f"[Vapor.ui] 预设组件加载失败: {_exc}", file=_sys.stderr)
