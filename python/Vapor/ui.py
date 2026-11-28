"""Vapor.ui —— 无 vdom 的响应式 UI（Vue 3 Vapor 风格）。

三层：
    Node      真实的绘制节点（没有 vnode，也没有 diff）
    Renderer  把模板 / 组件变成 Node 树，动态绑定各自独立的 effect
    App       布局 → 绘制 → 上屏 → 事件分发 的主循环

坐标系左上角为原点，单位是像素。布局是一个精简的 flex：
    dir="row" | "column"、gap、padding、margin、grow、align、justify、
    w/h（数字或 "50%"）、absolute + x/y。

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

import time
from collections.abc import Mapping

from . import _native, core
from . import template as tpl_mod
from . import svg as svg_mod

_core = _native.core
_geom = _native.ui

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


# ---------------------------------------------------------------------------
# 颜色 / 工具
# ---------------------------------------------------------------------------

def rgb(r, g, b, a=255) -> int:
    return _core.rgb(int(r), int(g), int(b), int(a))


def color(value, default=None) -> int:
    """把颜色（int / #hex / rgb() / 命名色）转成 0xAABBGGRR。"""
    if value is None:
        return default if default is not None else 0
    if isinstance(value, int):
        if value <= 0xFFFFFF:  # 纯 RGB
            return 0xFF000000 | ((value & 0xFF) << 16) | (((value >> 8) & 0xFF) << 8) | ((value >> 16) & 0xFF)
        return value & 0xFFFFFFFF
    if isinstance(value, (tuple, list)):
        parts = list(value) + [255] * (4 - len(value))
        return rgb(*parts[:4])
    text = str(value).strip()
    if text.startswith("#") or text.startswith("rgb") or text.lower() in svg_mod.NAMED_COLORS:
        rgb_val = svg_mod.color_value(text, None)
        if rgb_val is None:
            return default or 0
        return svg_mod._pack(rgb_val, 255)
    try:
        return int(text, 0) & 0xFFFFFFFF
    except ValueError:
        return default or 0


def with_alpha(c: int, factor: float) -> int:
    """给颜色乘一个透明度系数（阴影/蒙层用）。"""
    return _with_alpha(c, factor)


def _with_alpha(c: int, factor: float) -> int:
    if factor >= 0.999:
        return c
    a = int(((c >> 24) & 0xFF) * max(0.0, min(1.0, factor)))
    return (a << 24) | (c & 0x00FFFFFF)


def _num(value, total=0.0, default=None):
    """把布局数值（像素 / 百分比）解析为浮点数；auto 返回 default。"""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s == "auto" or s == "":
        return default
    if s.endswith("%"):
        try:
            return total * float(s[:-1]) / 100.0
        except ValueError:
            return default
    try:
        return float(s)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# 文本
# ---------------------------------------------------------------------------

_TEXT_RENDERER = None
_ACTIVE_FONT = None   # 真实字体（Vapor._ui.Font / FontStack），由 Vapor.font.install() 设置

try:  # 复用 rust.Vapor 的内置 5x7 点阵（没有原生库时它自带回退实现）
    from rust.Vapor import _FONT as _BITMAP_FONT  # type: ignore
except Exception:  # pragma: no cover
    _BITMAP_FONT = {}


def set_text_renderer(fn):
    """替换文本绘制实现（扩展可接入真实字体 / 中文点阵）。

    fn(canvas, text, x, y, color, size) 每帧被调用。
    """
    global _TEXT_RENDERER
    _TEXT_RENDERER = fn


def set_active_font(font):
    """设置全局字体（测量 + 绘制）；传 None 恢复内置点阵。"""
    global _ACTIVE_FONT
    _ACTIVE_FONT = font


def active_font():
    return _ACTIVE_FONT


def text_width(text, size=16.0) -> float:
    if _ACTIVE_FONT is not None:
        return _ACTIVE_FONT.text_width(str(text), float(size))
    return _geom.text_width(str(text), float(size))


def line_height(size=16.0) -> float:
    if _ACTIVE_FONT is not None:
        return _ACTIVE_FONT.line_height(float(size))
    return _geom.line_height(float(size))


def wrap_text(text, size, max_width) -> list:
    if _ACTIVE_FONT is not None:
        return _ACTIVE_FONT.wrap_text(str(text), float(size), float(max_width))
    return _geom.wrap_text(str(text), float(size), float(max_width))


# ---------------------------------------------------------------------------
# Rect / Node
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------

def _buffer_address(buffer):
    """取可写 C 连续缓冲的 (地址, 长度)：原生 Canvas 直接按地址写内存。

    这样 Rust 侧不需要依赖缓冲协议绑定（PyO3 0.30 在 abi3 下不提供），
    Python 侧用 ctypes 拿地址，帧缓冲本身仍然由 Python 对象持有。
    """
    import ctypes

    view = memoryview(buffer)
    if view.readonly:
        raise TypeError("帧缓冲必须是可写缓冲区")
    if not view.c_contiguous:
        raise TypeError("帧缓冲必须是 C 连续缓冲区")
    return ctypes.addressof(ctypes.c_char.from_buffer(view)), view.nbytes


class Canvas:
    """帧缓冲绘制封装：坐标统一取整，原生可用时走 Rust 光栅化。"""

    __slots__ = ("_c", "width", "height", "buffer", "address", "length")

    def __init__(self, buffer, width, height):
        self.width, self.height = int(width), int(height)
        self.buffer = buffer
        if getattr(_core, "FALLBACK", False):
            self.address, self.length = 0, 0
            self._c = _core.Canvas(buffer, self.width, self.height)
        else:
            self.address, self.length = _buffer_address(buffer)
            self._c = _core.Canvas(self.address, self.length, self.width, self.height)

    # -- 图元 --
    def clear(self, c):
        self._c.clear(int(c))

    def fill_rect(self, x, y, w, h, c):
        self._c.fill_rect(int(x), int(y), int(w), int(h), int(c))

    rect = fill_rect

    def pixel(self, x, y, c):
        self._c.pixel(int(x), int(y), int(c))

    def line(self, x0, y0, x1, y1, c, width=1):
        self._c.line(int(x0), int(y0), int(x1), int(y1), int(c), int(width))

    def hline(self, x, y, w, c):
        self._c.hline(int(x), int(y), int(w), int(c))

    def vline(self, x, y, h, c):
        self._c.vline(int(x), int(y), int(h), int(c))

    def disc(self, cx, cy, r, c):
        self._c.disc(int(cx), int(cy), int(r), int(c))

    def ring(self, cx, cy, r, c, width=1):
        self._c.ring(int(cx), int(cy), int(r), int(c), int(width))

    def circle(self, cx, cy, r, c, width=1):
        self._c.circle(int(cx), int(cy), int(r), int(c), int(width))

    def polygon(self, points, c):
        self._c.polygon([(int(x), int(y)) for x, y in points], int(c))

    def polyline(self, points, c, width=1, closed=False):
        self._c.polyline([(int(x), int(y)) for x, y in points], int(c), int(width), bool(closed))

    def round_rect(self, x, y, w, h, radius, c):
        self._c.round_rect(int(x), int(y), int(w), int(h), int(radius), int(c))

    def gradient(self, x, y, w, h, c1, c2, vertical=False):
        self._c.gradient(int(x), int(y), int(w), int(h), int(c1), int(c2), bool(vertical))

    def text(self, s, x, y, c, size=16, scale=None):
        if _TEXT_RENDERER is not None:
            _TEXT_RENDERER(self, s, x, y, c, size)
            return
        sc = int(scale) if scale else max(1, int(round(size / 7.0)))
        cx = int(x)
        for ch in str(s):
            rows = _BITMAP_FONT.get(ch.upper())
            if rows is None:
                cx += 6 * sc
                continue
            for ry, row in enumerate(rows):
                for bit in range(5):
                    if (row >> (4 - bit)) & 1:
                        self._c.fill_rect(cx + bit * sc, int(y) + ry * sc, sc, sc, int(c))
            cx += 6 * sc


# ---------------------------------------------------------------------------
# Surface：帧缓冲
# ---------------------------------------------------------------------------

class Surface:
    """帧缓冲来源。原生可用时用 rust.Vapor 的共享内存，否则用内存缓冲。"""

    def __init__(self, width=None, height=None, use_native=True):
        self._native = False
        self._rv = None
        buf = None
        w = h = 0
        if use_native:
            try:
                import rust.Vapor as rv

                if width and height:
                    try:
                        rv.resize(int(width), int(height))
                    except Exception:
                        rv.init(width=int(width), height=int(height))
                buf = rv.framebuffer()
                w, h = rv.size()
                self._rv = rv
                self._native = True
            except Exception:
                buf = None
        if buf is None:
            w = int(width or 360)
            h = int(height or 640)
            buf = bytearray(w * h * 4)
        self.width, self.height = int(w), int(h)
        self.buffer = buf

    def canvas(self) -> Canvas:
        return Canvas(self.buffer, self.width, self.height)

    def present(self):
        if self._native and self._rv is not None:
            try:
                self._rv.present()
            except Exception:
                pass

    @property
    def native(self) -> bool:
        return self._native


# ---------------------------------------------------------------------------
# 组件作用域
# ---------------------------------------------------------------------------

class _ComponentScope(tpl_mod.Scope):
    """组件内的名字解析：setup 绑定 -> props -> 父作用域。"""
    __slots__ = ("_bindings", "_props", "_ctx", "instance")

    def __init__(self, bindings=None, props=None, ctx=None, parent=None, instance=None):
        super().__init__({}, parent)
        self._bindings = bindings or {}
        self._props = props if props is not None else {}
        self._ctx = ctx or {}
        self.instance = instance

    def __getitem__(self, key):
        if key in self._bindings:
            return core.unref(self._bindings[key])
        if key in self._ctx:
            return self._ctx[key]
        if key in self._props:
            return self._props[key]
        if self._parent is not None:
            return self._parent[key]
        raise KeyError(key)

    def __setitem__(self, key, value):
        if key in self._bindings:
            target = self._bindings[key]
            if core.is_ref(target):
                target.value = value          # v-model 写回 Ref，而不是替换它
            else:
                self._bindings[key] = value
            return
        if key in self._props:
            self._props[key] = value
            return
        if self._parent is not None:
            try:
                self._parent[key] = value
                return
            except KeyError:
                pass
        self._bindings[key] = value

    def __iter__(self):
        seen = set()
        for src in (self._bindings, self._ctx, self._props):
            for k in src:
                if k not in seen:
                    seen.add(k)
                    yield k
        if self._parent is not None:
            for k in self._parent:
                if k not in seen:
                    seen.add(k)
                    yield k

    def __len__(self):
        return len(set(self.__iter__()))


# ---------------------------------------------------------------------------
# Renderer（模板 Host 的实现）
# ---------------------------------------------------------------------------

class Renderer:
    def __init__(self, registry=None):
        self.registry = dict(COMPONENTS)
        if registry:
            self.registry.update(registry)
        self._buckets = []          # 当前正在收集 effect 的列表栈
        self._instances = []        # 当前组件实例栈
        self._local_registries = []

    # ---- effect 管理 ----

    def _push_bucket(self, bucket):
        self._buckets.append(bucket)

    def _pop_bucket(self):
        if self._buckets:
            self._buckets.pop()

    def _new_effect(self, fn, scheduler=None):
        handle = _core.effect(fn, scheduler)
        if self._buckets:
            self._buckets[-1].append(handle)
        return handle

    # ---- Host 协议：创建 ----

    def element(self, tag, props=None) -> Node:
        return Node(tag, dict(props or {}))

    def text(self, value="") -> Node:
        return Node(_TEXT, {}, str(value))

    def fragment(self) -> Node:
        return Node(_FRAGMENT, {})

    def append_nodes(self, node, nodes):
        node.add(*nodes)
        return node

    def component_node(self, name, props, slots) -> Node:
        comp = self.resolve(name)
        if comp is None:
            raise KeyError(f"未注册的组件: <{name}>")
        return comp.instantiate(self, props or {}, slots or {})

    def resolve(self, name):
        if not name:
            return None
        for reg in reversed(self._local_registries):
            if name in reg:
                return reg[name]
        if name in self.registry:
            return self.registry[name]
        key = name.replace("-", "").lower()
        for k, v in self.registry.items():
            if k.replace("-", "").lower() == key:
                return v
        return None

    # ---- Host 协议：动态绑定 ----

    def bind_attr(self, node, key, getter):
        def apply():
            try:
                value = getter()
            except Exception as exc:  # 表达式错误不应该拖垮整帧
                _warn(f"绑定 {key} 求值失败: {exc}")
                return
            if isinstance(node.props, Mapping):
                node.props[key] = value
            else:  # pragma: no cover - Reactive 之外的容器
                node.props[key] = value

        self._new_effect(apply)

    def bind_text(self, node, getter):
        def apply():
            try:
                node.text = str(getter())
            except Exception as exc:
                _warn(f"文本插值失败: {exc}")

        self._new_effect(apply)

    def bind_show(self, node, getter):
        def apply():
            node.visible = bool(getter())

        self._new_effect(apply)

    def bind_if(self, frag, branches):
        current = {"nodes": []}

        def apply():
            target = None
            for cond, build in branches:
                try:
                    if cond():
                        target = build
                        break
                except Exception as exc:
                    _warn(f"v-if 求值失败: {exc}")
                    break
            for node in current["nodes"]:
                node.dispose()
                node.remove()
            current["nodes"] = []
            if target is not None:
                self._push_bucket(frag.effects)
                try:
                    nodes = target()
                finally:
                    self._pop_bucket()
                frag.add(*nodes)
                current["nodes"] = nodes

        self._new_effect(apply)

    def bind_for(self, frag, source_getter, build_item, key_getter):
        state = {"items": []}  # [(key, nodes, bucket)]

        def apply():
            try:
                source = source_getter()
            except Exception as exc:
                _warn(f"v-for 求值失败: {exc}")
                return
            keys = []
            for index, item in enumerate(source):
                try:
                    keys.append((key_getter(item, index), index, item))
                except Exception:
                    keys.append((index, index, item))

            old = {k: (nodes, bucket) for k, nodes, bucket in state["items"]}
            new_items = []
            ordered = []
            for key, index, item in keys:
                if key in old:
                    nodes, bucket = old.pop(key)
                else:
                    bucket = []
                    self._push_bucket(bucket)
                    try:
                        nodes = build_item(item, index)
                    finally:
                        self._pop_bucket()
                    for node in nodes:
                        node.key = key
                # 重新挂载，保证顺序
                for node in nodes:
                    if node.parent is not frag:
                        node.remove()
                        frag.add(node)
                new_items.append((key, nodes, bucket))
                ordered.extend(nodes)

            for key, (nodes, bucket) in old.items():
                for handle in bucket:
                    try:
                        handle.stop()
                    except Exception:
                        pass
                for node in nodes:
                    node.dispose()
                    node.remove()

            # 按新顺序重排 children
            frag.children[:] = ordered
            for node in ordered:
                node.parent = frag
            state["items"] = new_items

        self._new_effect(apply)

    def bind_model(self, node, getter, setter):
        def apply():
            try:
                value = getter()
            except Exception as exc:
                _warn(f"v-model 读取失败: {exc}")
                return
            node.props["value"] = value

        self._new_effect(apply)
        node.props["on_change"] = setter

    def on(self, node, event, handler_provider, modifiers=None):
        node.events.setdefault(event, []).append((handler_provider, modifiers or []))

    # ---- Host 协议：插槽 ----

    def slot(self, name="default", fallback=None, scope=None) -> Node:
        frag = self.fragment()
        owner = getattr(scope, "instance", None) if scope is not None else None
        inst = owner or (self._instances[-1] if self._instances else None)
        content = inst.slots.get(name) if inst is not None else None
        if content is not None:
            self._push_bucket(frag.effects)
            try:
                content.build(frag)
            finally:
                self._pop_bucket()
        elif fallback is not None:
            self._push_bucket(frag.effects)
            try:
                frag.add(*fallback())
            finally:
                self._pop_bucket()
        return frag


def _warn(msg):
    import sys

    print(f"[Vapor.ui] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 组件
# ---------------------------------------------------------------------------

COMPONENTS: dict = {}


class Component:
    """组件基类。

    * 有 template 属性 → 编译模板
    * 有 build(renderer, props, slots) → 直接建节点（内置组件用这条路径）
    * setup(props, ctx) 返回 dict，作为模板作用域
    """

    name = ""
    template = None
    props = {}
    components = {}
    setup = None
    build = None

    # -- 编译缓存 --
    _compiled = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._compiled = None

    @classmethod
    def compile(cls):
        if cls._compiled is None and cls.template:
            cls._compiled = tpl_mod.compile_template(cls.template)
        return cls._compiled

    @classmethod
    def instantiate(cls, renderer, props_in, slots) -> Node:
        node = Node(_COMPONENT)
        props = dict(cls.props or {})
        props.update(props_in or {})
        reactive_props = core.Reactive(props)
        node.props = reactive_props
        node.component = cls
        node.effects = []

        instance = _Instance(cls, node, slots)
        renderer._instances.append(instance)
        renderer._local_registries.append(dict(cls.components or {}))
        renderer._push_bucket(node.effects)
        core.push_instance(instance)
        try:
            bindings = instance.setup(props=reactive_props)
            instance.bindings = bindings
            children = instance.build(renderer, reactive_props, slots)
        finally:
            core.pop_instance()
            renderer._pop_bucket()
            renderer._local_registries.pop()
            renderer._instances.pop()

        node.add(*children)
        return node


class _Instance:
    def __init__(self, cls, node, slots):
        self.cls = cls
        self.node = node
        self.slots = dict(slots or {})
        self.bindings = {}
        self.ctx = {}

    def setup(self, props):
        raw = None
        for klass in self.cls.__mro__:
            if "setup" in klass.__dict__:
                raw = klass.__dict__["setup"]
                break
        if raw is None:
            return {}
        if isinstance(raw, staticmethod):
            fn = raw.__func__
            out = fn(props, self.ctx) if _arity(fn) >= 2 else fn(props)
            return dict(out or {})
        if callable(raw):
            if _arity(raw) >= 3:
                out = raw(self, props, self.ctx)
            elif _arity(raw) == 2:
                out = raw(self, props)
            else:
                out = raw(props)
            return dict(out or {})
        return {}

    def build(self, renderer, props, slots):
        own_build = self.cls.__dict__.get("build", None)
        if own_build is not None and own_build is not Component.build:
            out = self.cls.build(renderer, props, slots)
            return list(out) if isinstance(out, (list, tuple)) else [out]
        compiled = self.cls.compile()
        if compiled is None:
            raise RuntimeError(f"组件 {self.cls.__name__} 缺少 template 或 build()")
        scope = _ComponentScope(self.bindings, props, self.ctx, instance=self)
        return compiled.render(renderer, scope)


def _arity(fn) -> int:
    try:
        import inspect

        params = inspect.signature(fn).parameters
        return len([p for p in params.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)])
    except (TypeError, ValueError):
        return 1


def define_component(name, template=None, setup=None, props=None, components=None, base=Component):
    """用字典 / 关键字快速定义组件。"""
    if isinstance(name, Mapping):
        spec = dict(name)
        name = spec.pop("name", "Anonymous")
        template = spec.pop("template", template)
        setup = spec.pop("setup", setup)
        props = spec.pop("props", props)
        components = spec.pop("components", components)
    attrs = {"name": name, "template": template, "props": props or {}, "components": components or {}}
    if setup is not None:
        attrs["setup"] = staticmethod(setup)
    cls = type(name, (base,), attrs)
    COMPONENTS[name] = cls
    return cls


# ---------------------------------------------------------------------------
# 布局
# ---------------------------------------------------------------------------

def _padding(node):
    props = node.props
    base = props.get("padding", 0)
    if isinstance(base, (list, tuple)):
        vals = list(base)
        while len(vals) < 4:
            vals.append(vals[-1] if vals else 0)
        return tuple(float(v or 0) for v in vals[:4])
    p = float(base or 0)
    px = float(props.get("padding_x", p) or 0)
    py = float(props.get("padding_y", p) or 0)
    return (px, py, px, py)


def _margin(node):
    props = node.props
    base = props.get("margin", 0)
    if isinstance(base, (list, tuple)):
        vals = list(base)
        while len(vals) < 4:
            vals.append(vals[-1] if vals else 0)
        return tuple(float(v or 0) for v in vals[:4])
    m = float(base or 0)
    mx = float(props.get("margin_x", m) or 0)
    my = float(props.get("margin_y", m) or 0)
    return (mx, my, mx, my)


def _intrinsic(node, avail_w=0.0, avail_h=0.0):
    """节点在内容上的自然尺寸（不含 margin）。"""
    if node.tag == _TEXT:
        size = float(node.props.get("size", 16))
        if node.props.get("wrap") and avail_w > 0:
            lines = wrap_text(node.text, size, avail_w) or [""]
            return (avail_w, len(lines) * line_height(size))
        return (text_width(node.text, size), line_height(size))
    pad = _padding(node)
    kids = [k for k in _flow(node) if not k.props.get("absolute")]
    if not kids:
        return (pad[0] + pad[2], pad[1] + pad[3])
    row = node.props.get("dir", "column") == "row"
    gap = float(node.props.get("gap", 0) or 0)
    if row:
        w = sum(_measure(k, avail_w, avail_h)[0] for k in kids) + gap * (len(kids) - 1) + pad[0] + pad[2]
        h = max(_measure(k, avail_w, avail_h)[1] for k in kids) + pad[1] + pad[3]
    else:
        w = max(_measure(k, avail_w, avail_h)[0] for k in kids) + pad[0] + pad[2]
        h = sum(_measure(k, avail_w, avail_h)[1] for k in kids) + gap * (len(kids) - 1) + pad[1] + pad[3]
    return (w, h)


def _measure(node, avail_w=0.0, avail_h=0.0):
    """节点尺寸：显式 w/h 优先，否则用内容自然尺寸。"""
    if node.tag == _FRAGMENT:
        kids = _flow(node)
        if not kids:
            return (0.0, 0.0)
        return (max(_measure(k)[0] for k in kids), sum(_measure(k)[1] for k in kids))
    auto_w, auto_h = _intrinsic(node, avail_w, avail_h)
    w = _num(node.props.get("w"), avail_w or auto_w, None)
    h = _num(node.props.get("h"), avail_h or auto_h, None)
    return (auto_w if w is None else w, auto_h if h is None else h)


def _align_offset(align, free):
    if align == "center":
        return free / 2.0
    if align == "end":
        return free
    return 0.0


def layout(node, x=0.0, y=0.0, w=None, h=None):
    """递归计算并写回每个节点的 box。"""
    if w is None:
        w = _measure(node)[0]
    if h is None:
        h = _measure(node)[1]
    node.box = Rect(x, y, w, h)
    if node.tag in (_TEXT,):
        return

    pad = _padding(node)
    inner_x = x + pad[0]
    inner_y = y + pad[1]
    inner_w = max(0.0, w - pad[0] - pad[2])
    inner_h = max(0.0, h - pad[1] - pad[3])

    kids = _flow(node)
    if not kids:
        return

    flow_kids = [k for k in kids if not k.props.get("absolute")]
    abs_kids = [k for k in kids if k.props.get("absolute")]

    row = node.props.get("dir", "column") == "row"
    gap = float(node.props.get("gap", 0) or 0)
    main_total = inner_w if row else inner_h

    margins = [_margin(k) for k in flow_kids]
    sizes = []
    for i, kid in enumerate(flow_kids):
        mw, mh = _measure(kid, inner_w, inner_h)
        m = margins[i]
        sizes.append((mw + m[0] + m[2], mh + m[1] + m[3]))

    used = sum((s[0] if row else s[1]) for s in sizes) + gap * max(0, len(sizes) - 1)
    free = main_total - used

    grows = [float(k.props.get("grow", 0) or 0) for k in flow_kids]
    grow_total = sum(grows)
    if free > 0 and grow_total > 0:
        extra = [(free * g / grow_total) for g in grows]
    else:
        extra = [0.0] * len(flow_kids)

    justify = node.props.get("justify", "start")
    offset = 0.0
    if grow_total <= 0 and free > 0:
        offset = _align_offset(justify, free)

    cursor = (inner_x if row else inner_y) + offset
    for i, kid in enumerate(flow_kids):
        m = margins[i]
        sw, sh = sizes[i]
        kw = sw - m[0] - m[2]
        kh = sh - m[1] - m[3]
        if row:
            kw += extra[i]
        else:
            kh += extra[i]

        align = kid.props.get("align", node.props.get("align", "start"))
        if kid.props.get("stretch", False) or align == "stretch":
            if row:
                kh = inner_h - m[1] - m[3]
            else:
                kw = inner_w - m[0] - m[2]

        if kid.tag in (_FRAGMENT, _COMPONENT):
            # 容器壳：内部子节点按同样可用空间排布
            pass

        if row:
            kx = cursor + m[0]
            free_cross = inner_h - sh
            ky = inner_y + _align_offset(align, free_cross) + m[1]
            layout(kid, kx, ky, kw, kh)
            # 注意：主轴游标要加上 grow 分配到的空间，否则 grow 子节点会互相重叠
            cursor += sw + extra[i] + gap
        else:
            free_cross = inner_w - sw
            kx = inner_x + _align_offset(align, free_cross) + m[0]
            ky = cursor + m[1]
            layout(kid, kx, ky, kw, kh)
            cursor += sh + extra[i] + gap

    for kid in abs_kids:
        m = _margin(kid)
        aw = _num(kid.props.get("w"), inner_w, None)
        ah = _num(kid.props.get("h"), inner_h, None)
        ax = _num(kid.props.get("x"), inner_w, 0.0)
        ay = _num(kid.props.get("y"), inner_h, 0.0)
        if aw is None or ah is None:
            mw, mh = _measure(kid, inner_w, inner_h)
            aw = mw if aw is None else aw
            ah = mh if ah is None else ah
        layout(kid, inner_x + ax + m[0], inner_y + ay + m[1], aw, ah)

    if node.clip and node.scroll_y:
        _shift_children(node, 0.0, -node.scroll_y)


# ---------------------------------------------------------------------------
# 绘制
# ---------------------------------------------------------------------------

def _shift_children(node: Node, dx: float, dy: float):
    """把整棵子树平移（滚动用；每帧重新布局所以不会累积）。"""
    for child in node.children:
        child.box.x += dx
        child.box.y += dy
        _shift_children(child, dx, dy)


def _intersects(a: Rect, b: Rect) -> bool:
    return not (a.x + a.w <= b.x or a.x >= b.x + b.w or a.y + a.h <= b.y or a.y >= b.y + b.h)


def _paint_node(canvas: Canvas, node: Node):
    if not node.visible:
        return
    props = node.props
    if props.get("display") == "none":
        return

    # 容器壳（插槽 fragment / 组件节点）自己没有绘制，但必须继续向下遍历，
    # 否则插槽里的内容永远不会上屏。它们的 box 可能是 0x0，不能按尺寸提前返回。
    if node.tag in (_FRAGMENT, _COMPONENT):
        for child in node.children:
            _paint_node(canvas, child)
        return

    box = node.box
    if box.w <= 0 or box.h <= 0:
        return

    opacity = float(props.get("opacity", 1) or 1)

    if node.tag == _TEXT:
        size = float(props.get("size", 16))
        c = _with_alpha(color(props.get("color", "#FFFFFF")), opacity)
        lines = [node.text]
        if props.get("wrap") and box.w > 0:
            lines = wrap_text(node.text, size, box.w) or [""]
            max_lines = props.get("max_lines")
            if max_lines and len(lines) > int(max_lines):
                lines = lines[: int(max_lines)]
                lines[-1] = lines[-1] + "…"
        align = props.get("align")
        lh = line_height(size)
        for index, line in enumerate(lines):
            tx = box.x
            lw = text_width(line, size)
            if align == "center":
                tx = box.x + (box.w - lw) / 2
            elif align == "end":
                tx = box.x + box.w - lw
            canvas.text(line, int(tx), int(box.y + index * lh), c, size)
        return

    # 背景
    bg = props.get("bg")
    if bg is not None and not (isinstance(bg, str) and bg in ("none", "transparent")):
        if isinstance(bg, (tuple, list)) and bg and bg[0] == "linear":
            _, c1, c2, *rest = bg
            vertical = not rest or rest[0] != "h"
            radius = float(props.get("radius", 0) or 0)
            if radius > 0:
                canvas.round_rect(box.x, box.y, box.w, box.h, radius, _with_alpha(color(c1), opacity))
            canvas.gradient(box.x, box.y, box.w, box.h,
                            _with_alpha(color(c1), opacity), _with_alpha(color(c2), opacity), vertical)
        else:
            c = _with_alpha(color(bg), opacity)
            radius = float(props.get("radius", 0) or 0)
            if radius > 0:
                canvas.round_rect(box.x, box.y, box.w, box.h, radius, c)
            else:
                canvas.fill_rect(box.x, box.y, box.w, box.h, c)

    # 边框
    border = props.get("border")
    if border:
        bw = 1
        bc = "#3A4048"
        if isinstance(border, (tuple, list)):
            if len(border) >= 1 and isinstance(border[0], (int, float)):
                bw = int(border[0])
            if len(border) >= 2:
                bc = border[1]
        elif isinstance(border, Mapping):
            bw = int(border.get("width", 1))
            bc = border.get("color", bc)
        bc_px = _with_alpha(color(bc), opacity)
        radius = float(props.get("radius", 0) or 0)
        if radius > 0:
            for i in range(bw):
                canvas.round_rect(box.x + i, box.y + i, box.w - 2 * i, box.h - 2 * i, max(0, radius - i), bc_px)
        else:
            canvas.fill_rect(box.x, box.y, box.w, bw, bc_px)
            canvas.fill_rect(box.x, box.y + box.h - bw, box.w, bw, bc_px)
            canvas.fill_rect(box.x, box.y, bw, box.h, bc_px)
            canvas.fill_rect(box.x + box.w - bw, box.y, bw, box.h, bc_px)

    # 自定义绘制
    painter = props.get("paint")
    if callable(painter):
        try:
            painter(canvas, box)
        except Exception as exc:  # pragma: no cover
            _warn(f"paint 回调失败: {exc}")

    for child in node.children:
        if node.clip and not _intersects(child.box, box):
            continue
        _paint_node(canvas, child)


# ---------------------------------------------------------------------------
# 滚动 / 手势
# ---------------------------------------------------------------------------

def _scroll_max(viewport: Node) -> float:
    content = viewport.children[0] if viewport.children else None
    content_h = content.box.h if content is not None else 0.0
    return max(0.0, content_h - viewport.box.h)


def _scroll_by(viewport: Node, delta: float) -> float:
    """按像素滚动一个滚动容器：offset 0 表示顶部，正值表示内容上移（向下滚）。"""
    state = viewport.extra.get("scroll")
    if not state:
        return 0.0
    ref = state["offset"]
    max_off = _scroll_max(viewport)
    new_value = min(max_off, max(0.0, ref.value + delta))
    ref.value = new_value
    return new_value


class PointerRouter:
    """手势状态机：单指（点击/拖动/长按/惯性）、双指（捏合/平移）、滚轮。

    事件（都可冒泡，.stop / .once 有效）::

        pointerdown / pointerup / move
        dragstart / drag / dragend      （超过 8px 才判定拖动，ev.axis 是方向锁）
        click                            （未移动、未长按、抬手）
        dblclick                         （300ms 内两次点击，相距 <24px）
        longpress                        （按住 0.5s 未移动）
        pinch                            （双指：ev.scale 相对缩放，ev.dx/dy 质心位移）
        wheel                            （滚轮/触控板：ev.dx、ev.dy）
    """

    SLOP = 8.0          # 判定为拖动的位移阈值（px）
    LONG_PRESS = 0.5    # 长按时间（秒）
    FLING_STOP = 30.0   # 惯性停止速度（px/s）
    DOUBLE_TAP = 0.30   # 双击间隔（秒）
    DOUBLE_SLOP = 24.0  # 双击允许的位置偏差（px）
    WHEEL_STEP = 40.0   # 滚轮一格对应的像素

    def __init__(self, app):
        self.app = app
        self.state = None
        self.fling = None
        self.last_pos = None
        self._last_click = (0.0, 0.0, 0.0)
        self._hover_path = []

    # ---- 入口 ----

    def handle(self, kind, x, y, pointer=0, count=1, now=None, x2=None, y2=None):
        now = time.monotonic() if now is None else now
        self.last_pos = (x, y)
        if kind == "down":
            self._down(x, y, pointer, now, count)
        elif kind == "pointerdown":
            self._second_down(x, y, pointer, now, count)
        elif kind == "move":
            self._move(x, y, pointer, now, count, x2, y2)
        elif kind in ("up", "cancel", "pointerup"):
            self._up(x, y, pointer, now,
                     cancel=(kind == "cancel"), partial=(kind == "pointerup"))

    def handle_wheel(self, dx, dy, now=None):
        """滚轮/触控板：优先滚最内层的滚动容器，同时派发 wheel 事件。"""
        x, y = self.last_pos or (self.app.width / 2.0, self.app.height / 2.0)
        path = self.app.hit_test(x, y)
        scrolls = [n for n in path if "scroll" in n.extra]
        if scrolls:
            _scroll_by(scrolls[0], dy * self.WHEEL_STEP)
        return self.app.dispatch("wheel", x, y, None, dx, dy, 0, path)

    # ---- 状态机 ----

    def _down(self, x, y, pointer, now, count=1):
        self.fling = None
        path = self.app.hit_test(x, y)
        scrolls = [n for n in path if "scroll" in n.extra]
        self.state = {
            "id": pointer, "sx": x, "sy": y, "lx": x, "ly": y, "t0": now,
            "path": path, "dragging": False, "long": False,
            "multi": count > 1, "count": count, "axis": None,
            "scroll": scrolls[0] if scrolls else None,
            "vx": 0.0, "vy": 0.0, "last_t": now,
            "pinch": None, "centroid": None,
        }
        self.app.dispatch("pointerdown", x, y, None, 0.0, 0.0, pointer, path)
        # 组件"被按住"的通用通知：命中的整条链都能收到 press，同时维护 node.pressed
        for node in path:
            node.pressed = True
        self.app.dispatch("press", x, y, None, 0.0, 0.0, pointer, path)

    def _second_down(self, x, y, pointer, now, count):
        state = self.state
        if state is None:
            self._down(x, y, pointer, now, count)
            return
        state["multi"] = True
        state["count"] = count
        state["dragging"] = False
        state["long"] = True          # 多指不触发长按
        state["pinch"] = None
        state["centroid"] = None
        self.app.dispatch("pointerdown", x, y, None, 0.0, 0.0, pointer, state["path"])

    def _multi_move(self, state, x, y, pointer, x2, y2):
        state["multi"] = True
        state["dragging"] = False
        if x2 is None or y2 is None:
            return
        spread = max(1e-6, ((x - x2) ** 2 + (y - y2) ** 2) ** 0.5)
        cx, cy = (x + x2) / 2.0, (y + y2) / 2.0
        if state["pinch"] is None:
            state["pinch"] = spread
            state["centroid"] = (cx, cy)
            return
        scale = spread / max(1e-6, state["pinch"])
        pdx = cx - state["centroid"][0]
        pdy = cy - state["centroid"][1]
        if abs(scale - 1.0) > 0.004 or abs(pdx) > 0.5 or abs(pdy) > 0.5:
            self.app.dispatch("pinch", cx, cy, None, pdx, pdy, pointer,
                              state["path"], scale=scale)
            state["pinch"] = spread
            state["centroid"] = (cx, cy)

    def _move(self, x, y, pointer, now, count=1, x2=None, y2=None):
        state = self.state
        if state is None:
            self._hover(x, y, pointer, now)
            return
        if count > 1 or state["multi"]:
            self._multi_move(state, x, y, pointer, x2, y2)
            state["lx"], state["ly"] = x, y
            return

        dx = x - state["lx"]
        dy = y - state["ly"]
        state["lx"], state["ly"] = x, y
        moved = abs(x - state["sx"]) + abs(y - state["sy"])

        dt = max(1e-3, now - state["last_t"])
        state["vx"] = state["vx"] * 0.5 + (dx / dt) * 0.5
        state["vy"] = state["vy"] * 0.5 + (dy / dt) * 0.5
        state["last_t"] = now

        if not state["dragging"] and moved > self.SLOP:
            state["dragging"] = True
            # 方向锁：第一次超过阈值的位移决定主轴，后续不再翻转
            state["axis"] = "x" if abs(x - state["sx"]) > abs(y - state["sy"]) else "y"
            self._dispatch_axis("dragstart", x, y, dx, dy, pointer, state)

        if state["dragging"]:
            self._dispatch_axis("drag", x, y, dx, dy, pointer, state)
            if state["scroll"] is not None and state["axis"] == "y":
                _scroll_by(state["scroll"], -dy)

        self.app.dispatch("move", x, y, None, dx, dy, pointer, state["path"])

    def _hover(self, x, y, pointer, now):
        """无按键移动：维护 hover/leave（鼠标、触控板、桌面调试）。"""
        path = self.app.hit_test(x, y)
        if path == self._hover_path:
            return
        for node in self._hover_path:
            node.hover = False
        if self._hover_path:
            self.app.dispatch("leave", x, y, None, 0.0, 0.0, pointer, self._hover_path)
        for node in path:
            node.hover = True
        if path:
            self.app.dispatch("hover", x, y, None, 0.0, 0.0, pointer, path)
        self._hover_path = path

    def _dispatch_axis(self, kind, x, y, dx, dy, pointer, state):
        return self.app.dispatch(kind, x, y, None, dx, dy, pointer,
                                 state["path"], axis=state["axis"])

    def _up(self, x, y, pointer, now, cancel=False, partial=False):
        state = self.state
        if state is None:
            return
        path = state["path"]
        if partial:
            # 第二根手指抬起：保留手势状态，但不再判点击/长按
            state["pinch"] = None
            state["centroid"] = None
            state["multi"] = True
            state["long"] = True
            return

        for node in path:
            node.pressed = False
        self.app.dispatch("release", x, y, None, 0.0, 0.0, pointer, path)
        self.app.dispatch("pointerup", x, y, None, 0.0, 0.0, pointer, path)
        if cancel:
            if state["dragging"]:
                self.app.dispatch("dragend", x, y, None, 0.0, 0.0, pointer, path)
        elif state["multi"] or state["long"]:
            pass  # 多指 / 长按：不判点击
        elif state["dragging"]:
            self.app.dispatch("dragend", x, y, None, 0.0, 0.0, pointer, path)
            velocity = -state["vy"] * 1000.0   # 内容滚动速度（px/s）
            if state["scroll"] is not None and state["axis"] == "y" and abs(velocity) > 200.0:
                self.fling = {"node": state["scroll"], "v": velocity, "t": now}
        else:
            self.app.dispatch("click", x, y, None, 0.0, 0.0, pointer, path)
            last_t, lx, ly = self._last_click
            if (now - last_t <= self.DOUBLE_TAP
                    and abs(x - lx) <= self.DOUBLE_SLOP
                    and abs(y - ly) <= self.DOUBLE_SLOP):
                self.app.dispatch("dblclick", x, y, None, 0.0, 0.0, pointer, path)
                self._last_click = (0.0, 0.0, 0.0)
            else:
                self._last_click = (now, x, y)
        self.state = None

    # ---- 每帧推进（长按判定 + 惯性） ----

    def update(self, now):
        state = self.state
        if (
            state is not None
            and not state["dragging"]
            and not state["long"]
            and not state["multi"]
            and now - state["t0"] >= self.LONG_PRESS
        ):
            state["long"] = True
            self.app.dispatch("longpress", state["lx"], state["ly"], None, 0.0, 0.0,
                              state["id"], state["path"])

        fling = self.fling
        if fling is not None:
            dt = max(0.0, min(0.05, now - fling["t"]))
            fling["t"] = now
            if dt > 0:
                _scroll_by(fling["node"], fling["v"] * dt)
                fling["v"] *= 0.94 ** (dt * 60.0)
                if abs(fling["v"]) < self.FLING_STOP:
                    self.fling = None


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------

class Event:
    __slots__ = ("type", "x", "y", "raw", "target", "stopped", "dx", "dy",
                 "pointer", "scale", "axis")

    def __init__(self, type_, x, y, raw=None, dx=0.0, dy=0.0, pointer=0,
                 scale=1.0, axis=None):
        self.type = type_
        self.x = x
        self.y = y
        self.raw = raw
        self.target = None
        self.stopped = False
        self.dx = dx          # 本次 move/drag 的位移
        self.dy = dy
        self.pointer = pointer
        self.scale = scale    # 双指捏合的相对缩放
        self.axis = axis      # 拖动方向锁："x" / "y"

    def stop(self):
        self.stopped = True

    def __repr__(self):
        return f"Event({self.type}, {self.x}, {self.y})"


class App:
    """应用外壳：挂载、布局、绘制、事件、动画、帧循环。"""

    def __init__(self, root, width=None, height=None, background="#0B0E13",
                 surface=None, fps=60, registry=None, use_native=True):
        self.root_component = root
        self.width = width
        self.height = height
        self.background = background
        self.fps = fps
        self.surface = surface
        self.renderer = Renderer(registry)
        self.root = None
        self.pointer = PointerRouter(self)
        self._last_now = time.monotonic()
        self._last_ts = None
        self._running = False
        self._handlers = {}
        self._animations = []
        self._tick_hooks = []
        self._dirty = True

    # ---- 生命周期 ----

    def mount(self):
        if self.surface is None:
            self.surface = Surface(self.width, self.height)
        self.width, self.height = self.surface.width, self.surface.height
        self.registry_component(self.root_component)
        self.renderer.app = self
        self.root = self.root_component.instantiate(self.renderer, {}, {})
        self.root.key = "root"
        core.flush_jobs()
        self.tick()
        return self

    def registry_component(self, comp):
        name = getattr(comp, "name", "") or comp.__name__
        self.renderer.registry[name] = comp
        return name

    def unmount(self):
        self._running = False
        if self.root is not None:
            self.root.dispose()
            self.root = None

    # ---- 帧 ----

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        core.flush_jobs()
        self.pointer.update(now)
        self._run_animations(now)
        for hook in list(self._tick_hooks):
            try:
                hook(now)
            except Exception as exc:
                _warn(f"on_tick 回调失败: {exc}")
        if self.root is not None:
            layout(self.root, 0, 0, self.width, self.height)
            canvas = self.surface.canvas()
            canvas.clear(color(self.background))
            _paint_node(canvas, self.root)
            self.surface.present()
        self._dirty = False
        return self

    def run(self):
        self._running = True
        frame = 1.0 / max(1, self.fps)
        try:
            while self._running:
                start = time.monotonic()
                self._poll_events()
                self.tick(start)
                elapsed = time.monotonic() - start
                if elapsed < frame:
                    time.sleep(frame - elapsed)
        finally:
            self._running = False
        return self

    def stop(self):
        self._running = False

    def _poll_events(self):
        """把 rust.Vapor 的事件队列翻译成手势层事件（用 poll_touch 拿归一化数据）。"""
        try:
            import rust.Vapor as rv
        except Exception:
            return
        try:
            while True:
                ev = rv.poll_touch()
                if ev is None:
                    break
                now = self._event_time(ev.timestamp_ms)
                if ev.kind == "key":
                    if ev.action == 0:
                        self.feed_key(ev.keycode)
                    continue
                if ev.kind == "wheel":
                    self.feed_wheel(ev.x, ev.y, now=now)
                    continue
                multi = ev.pointer_count > 1
                self.feed(
                    ev.kind, ev.x, ev.y,
                    pointer=ev.pointer_id,
                    count=ev.pointer_count,
                    now=now,
                    x2=ev.x2 if multi else None,
                    y2=ev.y2 if multi else None,
                )
        except Exception:
            pass

    # ---- 手势 / 按键入口（原生事件、测试、桌面模拟都走这里） ----

    def _event_time(self, ts):
        """把事件时间戳（wall clock ms）换算到单调时间轴，保证同一帧内多个事件
        之间的 dt 仍然正确（速度 / 长按判定依赖它）。"""
        if not ts:
            return time.monotonic()
        if self._last_ts is not None:
            delta = (ts - self._last_ts) / 1000.0
            delta = max(0.0, min(0.1, delta))
            self._last_now = self._last_now + delta
        else:
            self._last_now = time.monotonic()
        self._last_ts = ts
        return self._last_now

    def feed(self, kind, x, y, pointer=0, now=None, x2=None, y2=None, count=1):
        """手工投递一个指针事件：kind = down / pointerdown / move / up / pointerup / cancel。"""
        self.pointer.handle(kind, x, y, pointer=pointer, count=count, now=now, x2=x2, y2=y2)

    def feed_wheel(self, dx, dy, now=None):
        """手工投递滚轮事件（Android 的 ACTION_SCROLL / 桌面鼠标滚轮）。"""
        return self.pointer.handle_wheel(dx, dy, now=now)

    def feed_key(self, keycode):
        """投递按键：4 = 返回（Android BACK），27 = ESC（桌面）。"""
        if keycode in (4, 27):
            self.handle_back()

    def handle_back(self):
        """返回键：先让页面栈出栈，再交给 app.on("back") 的处理器。"""
        nav = Navigator.current
        if nav is not None and nav.depth > 1:
            nav.pop()
            return True
        for fn in list(self._handlers.get("back", [])):
            try:
                if fn() is not False:
                    return True
            except Exception as exc:
                _warn(f"back 处理器失败: {exc}")
        return False

    # ---- 事件分发 ----

    def hit_test(self, x, y):
        path = []

        def walk(node):
            if not node.visible or node.props.get("display") == "none":
                return False
            box = node.box
            if node.clip and not box.contains(x, y):
                return False
            if node.tag == _FRAGMENT:
                for child in node.children:
                    if walk(child):
                        path.append(node)
                        return True
                return False
            if not box.contains(x, y):
                return False
            for child in reversed(node.children):
                if walk(child):
                    path.append(node)
                    return True
            path.append(node)
            return True

        if self.root is not None:
            walk(self.root)
        return path

    def dispatch(self, event_type, x, y, raw=None, dx=0.0, dy=0.0, pointer=0, path=None,
                 scale=1.0, axis=None):
        if path is None:
            path = self.hit_test(x, y)
        if not path:
            return None
        ev = Event(event_type, x, y, raw, dx, dy, pointer, scale, axis)
        for node in path:
            handlers = node.events.get(event_type)
            if not handlers:
                continue
            ev.target = node
            for provider, modifiers in handlers:
                try:
                    handler = provider()
                except Exception as exc:
                    _warn(f"事件处理器构建失败: {exc}")
                    continue
                if not callable(handler):
                    continue
                try:
                    handler(ev)
                except Exception as exc:
                    _warn(f"事件处理器异常: {exc}")
                if "once" in modifiers:
                    try:
                        handlers.remove((provider, modifiers))
                    except ValueError:
                        pass
                if ev.stopped or "stop" in modifiers:
                    return ev
        return ev

    # ---- 动画 ----

    def animate(self, duration, on_frame, easing=None, on_done=None):
        """注册一个时长动画；on_frame(t) 的 t 从 0 到 1。"""
        if easing is None:
            def easing(t):
                return 1 - (1 - t) * (1 - t) ** 2 if t < 1 else 1.0

        anim = {
            "start": time.monotonic(),
            "duration": max(0.001, float(duration)),
            "on_frame": on_frame,
            "easing": easing,
            "on_done": on_done,
        }
        self._animations.append(anim)
        try:
            on_frame(easing(0.0))
        except Exception as exc:
            _warn(f"动画首帧失败: {exc}")
        return anim

    def _run_animations(self, now):
        if not self._animations:
            return
        alive = []
        for anim in self._animations:
            t = (now - anim["start"]) / anim["duration"]
            t = max(0.0, min(1.0, t))
            try:
                anim["on_frame"](anim["easing"](t))
            except Exception as exc:
                _warn(f"动画帧失败: {exc}")
            if t >= 1.0:
                if anim["on_done"]:
                    try:
                        anim["on_done"]()
                    except Exception as exc:
                        _warn(f"动画结束回调失败: {exc}")
            else:
                alive.append(anim)
        self._animations = alive

    def on_tick(self, fn):
        self._tick_hooks.append(fn)
        return fn

    def on(self, event, fn):
        self._handlers.setdefault(event, []).append(fn)
        return fn


def on_tick(fn):
    """模块级快捷方式：等价于 app.on_tick，需要先在当前 App 上注册。"""
    app = App.current
    if app is None:
        raise RuntimeError("on_tick 只能在 App 挂载后使用")
    return app.on_tick(fn)


App.current = None


def create_app(root, width=None, height=None, **kwargs) -> App:
    app = App(root, width=width, height=height, **kwargs)
    App.current = app
    return app


def mount(root, width=None, height=None, **kwargs) -> App:
    return create_app(root, width=width, height=height, **kwargs).mount()


def _later(fn):
    """兼容别名：与 core.next_tick 相同。"""
    return core.next_tick(fn)


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


# ---------------------------------------------------------------------------
# 内置组件
# ---------------------------------------------------------------------------

# 这些属性描述「组件自身在父容器中的位置」，不应下传给组件内部的根元素
_LAYOUT_OWN = frozenset({
    "absolute", "x", "y", "grow", "align", "stretch",
    "margin", "margin_x", "margin_y", "justify",
})


def _bind_pointer_callbacks(renderer, node, props):
    """把 on_press / on_release / on_hover / on_leave 绑到节点事件上。"""

    def make(key):
        def handler(ev):
            callback = props.get(key)
            if callable(callback):
                callback(ev)
        return handler

    for event, key in (
        ("press", "on_press"),
        ("release", "on_release"),
        ("hover", "on_hover"),
        ("leave", "on_leave"),
    ):
        if key in props:
            renderer.on(node, event, lambda k=key: make(k))


def _bind_all(renderer, node, props, skip=()):
    """把组件 props 上的动态值绑到元素节点（每个 key 一个 effect）。"""
    skip_keys = set(skip) | _LAYOUT_OWN
    for key in list(props.keys()):
        if key in skip_keys:
            continue
        renderer.bind_attr(node, key, lambda k=key: props.get(k))


_SVG_CACHE: dict = {}


def _svg_doc(source):
    """按 source / 图标名取 SvgDoc（缓存解析结果）。"""
    doc = _SVG_CACHE.get(source)
    if doc is not None:
        return doc
    if isinstance(source, str) and source.strip().startswith("<"):
        text = source
    elif source in svg_mod.ICONS:
        text = svg_mod.ICONS[source]
    else:
        raise ValueError(f"未知 SVG: {source!r}")
    doc = svg_mod.parse_svg(text)
    _SVG_CACHE[source] = doc
    return doc


class View(Component):
    """通用容器（Android 的 LinearLayout + 背景 + 圆角）。"""

    name = "View"
    props = {}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": props.get("dir", "column")})
        _bind_all(r, node, props)
        _bind_pointer_callbacks(r, node, props)
        node.add(r.slot("default"))
        return [node]


class Row(View):
    name = "Row"

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "row", "align": "center"})
        _bind_all(r, node, props)
        node.add(r.slot("default"))
        return [node]


class Column(View):
    name = "Column"

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "column"})
        _bind_all(r, node, props)
        node.add(r.slot("default"))
        return [node]


class Center(View):
    """居中容器（主轴与交叉轴都居中）。"""

    name = "Center"

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": props.get("dir", "column"), "justify": "center", "align": "center"})
        _bind_all(r, node, props)
        node.add(r.slot("default"))
        return [node]


class Stack(View):
    """层叠容器：子节点全部绝对定位在 (0,0)。"""

    name = "Stack"

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "column"})
        _bind_all(r, node, props)
        slot = r.slot("default")
        for child in list(slot.children):
            child.props.setdefault("absolute", True)
            child.props.setdefault("x", 0)
            child.props.setdefault("y", 0)
        node.add(slot)
        return [node]


class Spacer(View):
    name = "Spacer"

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"grow": 1})
        _bind_all(r, node, props)
        node.props["grow"] = props.get("grow", 1)
        return [node]


class Divider(View):
    name = "Divider"

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {})
        _bind_all(r, node, props)
        node.props.setdefault("h", props.get("h", 1))
        node.props.setdefault("w", props.get("w", "100%"))
        node.props.setdefault("bg", props.get("bg", "#252B33"))
        return [node]


class Text(Component):
    """文本节点：插值/绑定都直接写 node.text，不重建节点。"""

    name = "Text"
    props = {"text": "", "size": 16, "color": "#E6EAF0"}

    @staticmethod
    def build(r, props, slots):
        node = r.text("")
        slot = slots.get("default")
        slot_getter = slot.text_getter() if slot is not None else None
        if slot_getter is not None:
            # <Text>{{ msg }}</Text>：插槽里的纯文本直接作为文本绑定
            r.bind_text(node, slot_getter)
        else:
            r.bind_text(node, lambda: props.get("text", ""))
        for key in ("size", "color", "align", "wrap", "max_lines", "opacity", "letter_spacing"):
            if key in props:
                r.bind_attr(node, key, lambda k=key: props.get(k))
        # 有显式宽度时让文本节点填满宽度，wrap 才有折行依据
        r.bind_attr(node, "stretch", lambda: props.get("w") is not None)
        return [node]


class Button(Component):
    """按钮：背景 + 文本 + 点击；支持 default 插槽替换内容。"""

    name = "Button"
    props = {"text": "", "bg": "#2D7FF9", "color": "#FFFFFF", "radius": 12, "size": 16, "disabled": False}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "row", "align": "center", "justify": "center", "padding": [12, 20]})
        _bind_all(r, node, props, skip=("text", "size", "color"))

        if slots.get("default") is not None:
            node.add(r.slot("default"))
        else:
            label = r.text("")
            r.bind_text(label, lambda: props.get("text", ""))
            r.bind_attr(label, "size", lambda: props.get("size", 16))
            r.bind_attr(label, "color", lambda: props.get("color", "#FFFFFF"))
            node.add(label)

        def handler(ev):
            if props.get("disabled"):
                return
            callback = props.get("on_click")
            if callable(callback):
                callback(ev)

        pressed = core.ref(False)

        def on_down(ev):
            if not props.get("disabled"):
                pressed.value = True

        def on_up(ev):
            pressed.value = False

        r.on(node, "click", lambda: handler)
        r.on(node, "pointerdown", lambda: on_down)
        r.on(node, "pointerup", lambda: on_up)
        r.bind_attr(
            node,
            "opacity",
            lambda: 0.5 if props.get("disabled") else (0.82 if pressed.value else 1.0),
        )
        return [node]


class Icon(Component):
    """SVG 图标组件（内置 Material 风格图标，也可传 src 源码）。"""

    name = "Icon"
    props = {"name": "home", "size": 24, "color": "#E6EAF0"}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"w": props.get("size", 24), "h": props.get("size", 24)})
        r.bind_attr(node, "w", lambda: props.get("size", 24))
        r.bind_attr(node, "h", lambda: props.get("size", 24))

        def paint(canvas, box):
            try:
                doc = _svg_doc(props.get("src") or props.get("name") or "home")
            except ValueError as exc:
                _warn(f"Icon: {exc}")
                return
            doc.render(canvas, box.x, box.y, box.w, box.h, color=props.get("color", "#E6EAF0"))

        node.props["paint"] = paint

        def handler(ev):
            callback = props.get("on_click")
            if callable(callback):
                callback(ev)

        r.on(node, "click", lambda: handler)
        return [node]


class Svg(Component):
    """把任意 SVG 当组件渲染（矢量，非位图）。"""

    name = "Svg"
    props = {"source": "home", "w": 24, "h": 24, "color": None}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"w": props.get("w", 24), "h": props.get("h", 24)})
        r.bind_attr(node, "w", lambda: props.get("w", 24))
        r.bind_attr(node, "h", lambda: props.get("h", 24))

        def paint(canvas, box):
            try:
                doc = _svg_doc(props.get("source") or "home")
            except ValueError as exc:
                _warn(f"Svg: {exc}")
                return
            doc.render(canvas, box.x, box.y, box.w, box.h, color=props.get("color"))

        node.props["paint"] = paint
        return [node]


class ProgressBar(Component):
    name = "ProgressBar"
    props = {"value": 0.0, "color": "#2D7FF9", "bg": "#232A33", "h": 6, "radius": 3}

    @staticmethod
    def build(r, props, slots):
        track = r.element("view", {"w": "100%", "radius": props.get("radius", 3)})
        r.bind_attr(track, "h", lambda: props.get("h", 6))
        r.bind_attr(track, "bg", lambda: props.get("bg", "#232A33"))
        r.bind_attr(track, "radius", lambda: props.get("radius", 3))

        fill = r.element("view", {"absolute": True, "x": 0, "y": 0, "h": "100%", "radius": props.get("radius", 3)})
        r.bind_attr(fill, "bg", lambda: props.get("color", "#2D7FF9"))
        r.bind_attr(fill, "w", lambda: f"{max(0.0, min(1.0, float(props.get('value') or 0))) * 100:.2f}%")
        track.add(fill)
        return [track]


class Badge(Component):
    name = "Badge"
    props = {"text": "", "bg": "#E5484D", "color": "#FFFFFF", "size": 12}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "row", "align": "center", "justify": "center", "padding": [2, 8], "radius": 10})
        _bind_all(r, node, props, skip=("text", "size", "color"))
        label = r.text("")
        r.bind_text(label, lambda: props.get("text", ""))
        r.bind_attr(label, "size", lambda: props.get("size", 12))
        r.bind_attr(label, "color", lambda: props.get("color", "#FFFFFF"))
        node.add(label)
        return [node]


class Switch(Component):
    """开关：点击切换 value，并通过 on_change 回调新值。"""

    name = "Switch"
    props = {"value": False, "color": "#2D7FF9", "w": 46, "h": 26}

    @staticmethod
    def build(r, props, slots):
        track = r.element("view", {"radius": 13, "w": 46, "h": 26})
        r.bind_attr(track, "w", lambda: props.get("w", 46))
        r.bind_attr(track, "h", lambda: props.get("h", 26))
        r.bind_attr(track, "radius", lambda: float(props.get("h", 26)) / 2)
        r.bind_attr(track, "bg", lambda: props.get("color", "#2D7FF9") if props.get("value") else "#2A3038")

        knob = r.element("view", {"absolute": True, "y": 3, "radius": 10, "bg": "#FFFFFF"})
        r.bind_attr(knob, "w", lambda: float(props.get("h", 26)) - 6)
        r.bind_attr(knob, "h", lambda: float(props.get("h", 26)) - 6)
        r.bind_attr(knob, "radius", lambda: (float(props.get("h", 26)) - 6) / 2)
        r.bind_attr(knob, "x", lambda: (float(props.get("w", 46)) - float(props.get("h", 26)) + 3) if props.get("value") else 3)
        track.add(knob)

        def handler(ev):
            new_value = not bool(props.get("value"))
            callback = props.get("on_change")
            if callable(callback):
                callback(new_value)
            else:
                props["value"] = new_value

        r.on(track, "click", lambda: handler)
        return [track]


class Slider(Component):
    """滑块：点击轨道按位置取值（0..1）。"""

    name = "Slider"
    props = {"value": 0.0, "color": "#2D7FF9", "w": "100%", "h": 32}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"w": "100%", "h": 32})
        r.bind_attr(node, "w", lambda: props.get("w", "100%"))
        r.bind_attr(node, "h", lambda: props.get("h", 32))

        track = r.element("view", {"absolute": True, "x": 0, "y": 14, "w": "100%", "h": 4, "radius": 2, "bg": "#2A3038"})
        fill = r.element("view", {"absolute": True, "x": 0, "y": 14, "h": 4, "radius": 2})
        r.bind_attr(fill, "bg", lambda: props.get("color", "#2D7FF9"))
        r.bind_attr(fill, "w", lambda: f"{max(0.0, min(1.0, float(props.get('value') or 0))) * 100:.2f}%")

        knob = r.element("view", {"absolute": True, "y": 8, "w": 16, "h": 16, "radius": 8, "bg": "#FFFFFF"})
        r.bind_attr(knob, "x", lambda: f"{max(0.0, min(1.0, float(props.get('value') or 0))) * 100:.2f}%")
        node.props["_knob"] = knob

        node.add(track)
        node.add(fill)
        node.add(knob)

        def handler(ev):
            box = node.box
            if box.w <= 0:
                return
            ratio = max(0.0, min(1.0, (ev.x - box.x) / box.w))
            callback = props.get("on_change")
            if callable(callback):
                callback(ratio)
            else:
                props["value"] = ratio

        r.on(node, "click", lambda: handler)
        r.on(node, "pointerdown", lambda: handler)
        r.on(node, "drag", lambda: handler)
        return [node]


class Scroll(Component):
    """滚动容器：拖动滚动 + 抬手惯性（由 PointerRouter 驱动 offset）。

    也可以用 scroll_y 属性从外部控制（与手势叠加）；可滚动范围按
    「内容高度 - 视口高度」自动计算并做边界钳制。
    """

    name = "Scroll"
    props = {"scroll_y": 0}

    @staticmethod
    def build(r, props, slots):
        viewport = r.element("view", {"dir": "column", "clip": True})
        _bind_all(r, viewport, props, skip=("scroll_y",))
        viewport.clip = True

        content = r.element("view", {"dir": "column"})
        content.add(r.slot("default"))
        viewport.add(content)

        # offset 是手势层直接改的（0=顶部，正值=内容上移），scroll_y 属性可做外部控制
        offset = core.ref(0.0)
        viewport.extra["scroll"] = {"offset": offset}

        def apply_scroll():
            viewport.scroll_y = float(props.get("scroll_y") or 0) + offset.value

        r._new_effect(apply_scroll)
        return [viewport]


class ListView(Component):
    """列表（非 keyed 版本）。需要精确复用请用模板里的 v-for（keyed）。"""

    name = "ListView"
    props = {"items": [], "gap": 8, "dir": "column"}

    @staticmethod
    def build(r, props, slots):
        node = r.element("view", {"dir": "column"})
        r.bind_attr(node, "dir", lambda: props.get("dir", "column"))
        r.bind_attr(node, "gap", lambda: props.get("gap", 8))
        frag = r.fragment()
        node.add(frag)

        state = {"items": []}

        def apply():
            items = list(props.get("items") or [])
            render_item = props.get("render_item")
            if not callable(render_item):
                return
            for child in list(frag.children):
                child.dispose()
                child.remove()
            for index, item in enumerate(items):
                out = render_item(item, index, r)
                if out is None:
                    continue
                nodes = out if isinstance(out, (list, tuple)) else [out]
                frag.add(*nodes)
            state["items"] = items

        r._new_effect(apply)
        return [node]


class TextInput(Component):
    """单行输入框（展示型；输入法由扩展/宿主接入）。"""

    name = "TextInput"
    props = {"value": "", "placeholder": "请输入", "bg": "#171D25", "color": "#E6EAF0", "radius": 10}

    @staticmethod
    def build(r, props, slots):
        box = r.element("view", {"dir": "row", "align": "center", "padding": [12, 14], "radius": 10})
        _bind_all(r, box, props, skip=("value", "placeholder", "color"))
        label = r.text("")
        r.bind_text(label, lambda: props.get("value") or props.get("placeholder") or "")
        r.bind_attr(label, "size", lambda: props.get("size", 15))
        r.bind_attr(label, "color", lambda: props.get("color", "#E6EAF0") if props.get("value") else "#6B7683")
        box.add(label)

        def handler(ev):
            callback = props.get("on_focus")
            if callable(callback):
                callback(ev)

        r.on(box, "click", lambda: handler)
        return [box]


class Page(Component):
    """一个「Android 页面」：可选标题栏 + 返回按钮 + 内容区。"""

    name = "Page"
    props = {"title": "", "show_bar": True, "bg": "#0B0E13", "bar_bg": "#151A21"}

    @staticmethod
    def build(r, props, slots):
        root = r.element("view", {"dir": "column", "w": "100%", "h": "100%", "bg": "#0B0E13"})
        r.bind_attr(root, "bg", lambda: props.get("bg", "#0B0E13"))

        if props.get("show_bar", True):
            bar = r.element("view", {"dir": "row", "align": "center", "h": 56, "padding": [0, 12], "gap": 8})
            r.bind_attr(bar, "bg", lambda: props.get("bar_bg", "#151A21"))
            back = r.element("view", {"w": 40, "h": 40, "dir": "row", "align": "center", "justify": "center"})
            back_label = r.text("‹")
            back_label.props.update({"size": 26, "color": "#E6EAF0"})
            back.add(back_label)

            def on_back(ev):
                callback = props.get("on_back")
                if callable(callback):
                    callback(ev)
                navigator = Navigator.current
                if navigator is not None:
                    navigator.pop()

            r.on(back, "click", lambda: on_back)
            bar.add(back)

            title = r.text("")
            r.bind_text(title, lambda: props.get("title", ""))
            title.props.update({"size": 18, "color": "#E6EAF0", "grow": 1})
            bar.add(title)
            root.add(bar)

        content = r.element("view", {"dir": "column", "grow": 1, "w": "100%"})
        content.add(r.slot("default"))
        root.add(content)
        return [root]


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


def register(component_cls):
    """把组件登记到全局注册表（模板里可直接用 <Name />）。"""
    COMPONENTS[component_cls.name or component_cls.__name__] = component_cls
    return component_cls


for _c in (
    View, Row, Column, Center, Stack, Spacer, Divider, Text, Button, Icon, Svg,
    ProgressBar, Badge, Switch, Slider, Scroll, ListView, TextInput, Page, PageStack,
):
    register(_c)


# 内建现代化圆角组件（Card / Fab / Chip / Dialog ...）：导入即注册。
# 放在模块最后导入，避免循环依赖（presets 反向 import ui）。
try:  # pragma: no cover - 单纯为了让预设可用
    from . import presets as _presets  # noqa: F401,E402
except Exception as _exc:  # 预设出问题不应该拖垮整个 UI 框架
    import sys as _sys
    print(f"[Vapor.ui] 预设组件加载失败: {_exc}", file=_sys.stderr)

