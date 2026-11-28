"""Renderer / Component：把模板与组件实例化为节点树。"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Mapping

from .. import _native, core
from .. import template as tpl_mod
from .base import Node, _TEXT, _FRAGMENT, _COMPONENT

try:  # 文档 A 尚未合入时的空账本：与 ledger.is_full=True 行为一致。
    from .dirty import ledger
except ImportError:  # pragma: no cover - A 合入后走真实账本
    class _NullLedger:
        is_full = True

        def mark_rect(self, x, y, w, h):
            pass

        def mark_full(self):
            pass

        def rects(self):
            return ()

    ledger = _NullLedger()

_core = _native.core

__all__ = [
    "Renderer", "Component", "define_component", "register", "COMPONENTS",
    "STRICT_MISSING_COMPONENTS",
]

# 缺失组件的策略：默认渲染占位文本并告警一次；StrictMode 才抛 KeyError。
# 单个 Renderer 可用 strict=True/False 覆盖这个模块级开关。
STRICT_MISSING_COMPONENTS = False

# 一次性告警去重集合：按组件名 / 属性名记录，避免每帧刷屏。
_WARNED_MISSING = set()
_WARNED_SHADOW = set()
_WARNED_PROPS = set()
_MISSING = object()


def _warn_once(cache, key, msg):
    if key in cache:
        return
    cache.add(key)
    _warn(msg)


def _lookup(registry, name):
    """先精确匹配，再按去掉连字符的小写形式匹配（<My-Btn> -> MyBtn）。"""
    if not name or not registry:
        return None
    if name in registry:
        return registry[name]
    key = str(name).replace("-", "").lower()
    for k, v in registry.items():
        if str(k).replace("-", "").lower() == key:
            return v
    return None


class _SetupContext(dict):
    """setup(props, ctx) 的 ctx：既是普通 dict，也是生命周期注册入口。"""

    __slots__ = ("_instance",)

    def __init__(self, instance):
        super().__init__()
        self._instance = instance

    def on_mounted(self, fn):
        """注册挂载后回调 fn(ctx)，返回 fn 以支持装饰器写法。"""
        self._instance._mounted_hooks.append(fn)
        return fn

    def on_unmounted(self, fn):
        """注册卸载时回调 fn(ctx)。"""
        self._instance._unmounted_hooks.append(fn)
        return fn

    def on_cleanup(self, fn):
        """注册卸载后执行的无参清理函数。"""
        return self._instance.on_cleanup(fn)


class _ComponentScope(tpl_mod.Scope):
    """组件内的名字解析：bindings -> ctx -> props -> 父作用域。

    props 是父级传入的只读数据；子组件要向上写值必须通过
    node.props["on_change"] 回调（v-model 即走这条路径）。"""

    __slots__ = ("_bindings", "_props", "_ctx", "instance")

    def __init__(self, bindings=None, props=None, ctx=None, parent=None, instance=None):
        super().__init__({}, parent)
        self._bindings = bindings or {}
        self._props = props if props is not None else {}
        self._ctx = ctx or {}
        self.instance = instance

    def __getitem__(self, key):
        if key in self._bindings:
            if key in self._props:
                _warn_once(
                    _WARNED_SHADOW, key,
                    f"名字 {key!r} 同时存在于 setup 绑定与 props：绑定优先，props 被遮蔽",
                )
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
            # 单向数据流：props 归父级所有，子组件不得直接改。
            # 有 on_change 时回写父级（v-model 的约定），否则仅告警忽略。
            _warn_once(
                _WARNED_PROPS, key,
                f"props 只读：子组件写入 {key!r} 被拦截，请用 node.props['on_change'] 回写",
            )
            on_change = self._props.get("on_change")
            if callable(on_change) and key != "on_change":
                try:
                    on_change(value)
                except Exception as exc:
                    _warn(f"props on_change 回写失败: {exc}")
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


class Renderer:
    def __init__(self, registry=None, strict=None):
        self.registry = dict(COMPONENTS)
        if registry:
            self.registry.update(registry)
        # strict=None 时跟随模块级开关；显式传值可单独控制某个 Renderer。
        self.strict = STRICT_MISSING_COMPONENTS if strict is None else bool(strict)
        self._buckets = []          # 当前正在收集 effect 的列表栈
        self._instances = []        # 当前组件实例栈
        self._local_registries = []

    # ---- effect 管理 ----

    def _push_bucket(self, bucket):
        self._buckets.append(bucket)

    def _pop_bucket(self):
        if self._buckets:
            self._buckets.pop()

    def _new_effect(self, fn, scheduler=None, node=None):
        def wrapped():
            # effect 在 flush_jobs 时执行，此时 node.box 仍是上一帧的位置：
            # 标记的是“变化前占据的区域”，用于擦除；变化后的新区域由 layout 对比覆盖。
            if node is not None:
                b = node.box
                if b.w > 0 and b.h > 0:
                    ledger.mark_rect(b.x, b.y, b.w, b.h)
            return fn()

        handle = _core.effect(wrapped, scheduler)
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
        """实例化组件节点；未注册的组件按策略降级，而不是拖垮整次 mount。"""
        comp = self.resolve(name)
        if comp is None:
            if self.strict:
                raise KeyError(f"未注册的组件: <{name}>")
            _warn_once(
                _WARNED_MISSING, name,
                f"未注册的组件 <{name}>，已渲染占位节点",
            )
            return self.text(f"[缺少 {name}]")
        return comp.instantiate(self, props or {}, slots or {})

    def resolve(self, name):
        """名字解析：局部注册 -> 实时全局 COMPONENTS -> 构造时快照。

        全局表每次现查，已挂载的 App 在 register_provider() 加载新组件后
        下一帧即可解析到；self.registry 仍保留显式传入 / App 注册的组件。
        """
        if not name:
            return None
        for reg in reversed(self._local_registries):
            if name in reg:
                return reg[name]
        for reg in (COMPONENTS, self.registry):
            comp = _lookup(reg, name)
            if comp is not None:
                return comp
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

        self._new_effect(apply, node=node)

    def bind_text(self, node, getter):
        def apply():
            try:
                node.text = str(getter())
            except Exception as exc:
                _warn(f"文本插值失败: {exc}")

        self._new_effect(apply, node=node)

    def bind_show(self, node, getter):
        def apply():
            node.visible = bool(getter())

        self._new_effect(apply, node=node)

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
                _mark_subtree(node)
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
        """key 复用契约：key 相同则复用节点及其 effect，不重跑组件 setup。

        复用期间变化的 props 只由 bind_attr 的 effect 更新到节点上；setup 里
        从 props 计算出的派生快照不会自动重算。派生值若需跟随 props 变化，
        请返回 computed(...) 或响应式绑定，不要在 setup 中固化。
        """
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
                    _mark_subtree(node)
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

        self._new_effect(apply, node=node)
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


def _mark_subtree(node):
    """节点从树上移除前，把它与整棵子树的旧区域标脏（用于擦除）。"""
    if node is None:
        return
    for n in node.walk():
        b = n.box
        if b.w > 0 and b.h > 0:
            ledger.mark_rect(b.x, b.y, b.w, b.h)


def _warn(msg):
    import sys

    print(f"[Vapor.ui] {msg}", file=sys.stderr)


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
        """构建组件节点树。

        props 按 Component.props 的轻量 schema 校验；模板后续的 props 更新
        走 Renderer.bind_attr（不重跑 setup，见 bind_for 的复用契约）。
        """
        node = Node(_COMPONENT)
        props = _normalize_props(cls, props_in or {})
        reactive_props = core.Reactive(props)
        node.props = reactive_props
        node.component = cls
        node.effects = []

        instance = _Instance(cls, node, slots)
        node.instance = instance    # Node.dispose 时触发卸载钩子
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
        # 首帧节点树构建完成：分发 mounted 钩子。
        instance.on_mounted(instance.ctx)
        return node


# 布局数值类属性：既接受数字像素，也接受 "100%" / "auto" 等字符串。
# 默认值若为字符串不做类型推断校验（布局层 _num 负责兜底解析）。
_LAYOUT_NUM_PROPS = frozenset({
    "w", "h", "width", "height", "x", "y", "left", "top", "right", "bottom",
    "min_w", "max_w", "min_h", "max_h", "pad", "padding", "margin", "gap",
    "radius", "size", "spacing", "offset", "inset",
})


def _prop_type_ok(value, expected):
    if isinstance(expected, tuple):
        return isinstance(value, expected)
    # int/float 数值互通，避免默认值 0.0 与传入 0 互相误报
    if expected in (int, float) and isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    try:
        return isinstance(value, expected)
    except TypeError:
        return type(value) is expected


def _normalize_props(cls, props_in):
    """归一化并校验 Component.props，兼容两种写法：

        props = {"size": 16}
        props = {"size": {"type": int, "default": 16, "required": True}}
    """
    schema = {}
    for name, spec in (cls.props or {}).items():
        described = (
            isinstance(spec, Mapping)
            and ("type" in spec or "default" in spec or "required" in spec)
            and ("type" not in spec or isinstance(spec["type"], (type, tuple))
                 or spec["type"] is None)
        )
        if described:
            schema[name] = (
                spec.get("type"), spec.get("default", _MISSING),
                bool(spec.get("required", False)),
            )
        else:
            # 从默认值推断类型只作宽松约束，不锁死：
            #  * 布局数值属性（w/h/x/y/padding/gap/radius...）既接受像素数字，
            #    也接受 "100%" / "auto" 字符串，由布局层 _num 统一解析。
            #  * 字符串默认值（如 text="", label="x"）常被模板数字插值复用，
            #    例如 :text="count" / {{ count }} 会传入 int，绘制层会 str() 化。
            # 这些都不要按 str 强制校验，否则会出现 "prop text 期望 str 得到 int"
            # 的误报。只有「显式 schema 写了 type」才做强制类型校验。
            if isinstance(spec, str) or name in _LAYOUT_NUM_PROPS:
                schema[name] = (None, spec, False)
            else:
                schema[name] = (type(spec) if spec is not None else None, spec, False)

    out = {}
    errors = []
    for name, (expected, default, required) in schema.items():
        if name in props_in:
            value = props_in[name]
        elif default is not _MISSING:
            value = default
        elif required:
            errors.append(f"组件 {cls.__name__} 缺少必填 prop {name}")
            continue
        else:
            continue
        if expected is not None and not _prop_type_ok(value, expected):
            label = getattr(expected, "__name__", str(expected))
            errors.append(
                f"组件 {cls.__name__} 的 prop {name} 类型错误: "
                f"期望 {label}，得到 {type(value).__name__}"
            )
        out[name] = value
    # 未声明的键原样保留，兼容动态 props
    for name, value in props_in.items():
        if name not in out:
            out[name] = value
    if errors:
        raise TypeError("；".join(errors))
    return out


def _looks_like_method(fn):
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return False
    positional = [p for p in params
                  if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    return bool(positional and positional[0].name == "self") or len(positional) >= 3


def _call_setup(raw, instance, props, ctx):
    """用 inspect.signature().bind() 严格绑定 setup 参数。

    明确支持 setup(props, ctx)、实例方法 setup(self, props, ctx)、
    setup(props)，并兼容默认值、*args、关键字参数与 functools.partial。
    """
    if raw is None:
        return {}
    if not callable(raw) and not isinstance(raw, (staticmethod, classmethod)):
        raise TypeError(f"组件 {instance.cls.__name__} 的 setup 不可调用: {raw!r}")
    if isinstance(raw, classmethod):
        fn = functools.partial(raw.__func__, instance.cls)
    elif isinstance(raw, staticmethod):
        fn = raw.__func__
    elif inspect.ismethod(raw):
        fn = raw                      # 已绑定，不再传 instance
    else:
        fn = functools.partial(raw, instance) if _looks_like_method(raw) else raw

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"组件 {instance.cls.__name__} 的 setup 无法解析签名: {exc}") from exc

    bound = None
    last_error = None
    for n in (2, 1, 0):               # 位置绑定优先，覆盖 POSITIONAL_ONLY
        try:
            bound = sig.bind(*(props, ctx)[:n])
            break
        except TypeError as exc:
            last_error = exc
    if bound is None:                 # 关键字兜底：keyword-only / 自定义参数名
        for kwargs in ({"props": props, "ctx": ctx}, {"props": props}, {"ctx": ctx}):
            try:
                bound = sig.bind(**kwargs)
                break
            except TypeError as exc:
                last_error = exc
    if bound is None:
        raise TypeError(
            f"组件 {instance.cls.__name__} 的 setup 签名不兼容: {sig}；"
            "支持 setup(props, ctx) / setup(self, props, ctx) / setup(props)；"
            f"绑定失败: {last_error}"
        )
    out = fn(*bound.args, **bound.kwargs)
    return dict(out or {})


class _Instance:
    def __init__(self, cls, node, slots):
        self.cls = cls
        self.node = node
        self.slots = dict(slots or {})
        self.bindings = {}
        self.ctx = _SetupContext(self)
        self._mounted_hooks = []
        self._unmounted_hooks = []
        self._cleanups = []
        self._mounted = False
        self._unmounted = False

    def setup(self, props):
        raw = None
        for klass in self.cls.__mro__:
            if "setup" in klass.__dict__:
                raw = klass.__dict__["setup"]
                if raw is not None:
                    break
        return _call_setup(raw, self, props, self.ctx)

    def on_cleanup(self, fn):
        """注册卸载时执行的清理函数；返回 fn 以支持装饰器写法。"""
        if callable(fn):
            self._cleanups.append(fn)
        return fn

    def on_mounted(self, ctx=None):
        """首帧节点树构建完成后调用：分发 mounted 钩子（幂等）。"""
        if self._mounted:
            return
        self._mounted = True
        ctx = self.ctx if ctx is None else ctx
        for fn in self._hooks(self._mounted_hooks, "mounted", "on_mounted"):
            self._call_hook(fn, ctx)

    def on_unmounted(self, ctx=None):
        """Node.dispose 时调用：分发 unmounted 钩子并执行所有 cleanups。"""
        if self._unmounted:
            return
        self._unmounted = True
        ctx = self.ctx if ctx is None else ctx
        try:
            for fn in self._hooks(self._unmounted_hooks, "unmounted", "on_unmounted"):
                self._call_hook(fn, ctx)
        finally:
            for fn in self._cleanups:
                try:
                    fn()
                except Exception as exc:
                    _warn(f"组件清理函数失败: {exc}")
            self._cleanups.clear()

    def _hooks(self, registered, *names):
        """MRO 上的组件类钩子先执行，再执行 setup 里注册的回调。"""
        found = []
        for name in names:
            for klass in self.cls.__mro__:
                raw = klass.__dict__.get(name)
                if raw is None:
                    continue
                if isinstance(raw, (staticmethod, classmethod)):
                    found.append(raw.__func__)
                elif inspect.ismethod(raw):
                    found.append(raw)
                else:
                    found.append(functools.partial(raw, self))
                break
        return found + list(registered)

    def _call_hook(self, fn, ctx):
        try:
            params = list(inspect.signature(fn).parameters.values())
        except (TypeError, ValueError):
            params = []
        positional = [p for p in params
                      if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                    inspect.Parameter.POSITIONAL_OR_KEYWORD)]
        varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
        try:
            if len(positional) >= 2 or varargs:
                fn(self, ctx)
            elif len(positional) == 1:
                fn(ctx)
            else:
                fn()
        except Exception as exc:
            _warn(f"组件生命周期钩子失败: {exc}")

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


def define_component(name, template=None, setup=None, props=None, components=None,
                     base=Component, replace=False):
    """用字典 / 关键字快速定义组件。同名默认拒绝，replace=True 才覆盖。"""
    if isinstance(name, Mapping):
        spec = dict(name)
        name = spec.pop("name", "Anonymous")
        template = spec.pop("template", template)
        setup = spec.pop("setup", setup)
        props = spec.pop("props", props)
        components = spec.pop("components", components)
        replace = spec.pop("replace", replace)
    if name in COMPONENTS and not replace:
        raise ValueError(f"组件名冲突: {name} 已注册，使用 replace=True 覆盖")
    attrs = {"name": name, "template": template, "props": props or {}, "components": components or {}}
    if setup is not None:
        attrs["setup"] = staticmethod(setup)
    cls = type(name, (base,), attrs)
    COMPONENTS[name] = cls
    return cls


def register(component_cls, replace=False):
    """把组件登记到全局注册表（模板里可直接用 <Name />）。

    名字已存在时默认抛 ValueError，replace=True 才覆盖（与 define_component 同契约）。
    """
    name = component_cls.name or component_cls.__name__
    if name in COMPONENTS and not replace:
        raise ValueError(f"组件名冲突: {name} 已注册，使用 replace=True 覆盖")
    COMPONENTS[name] = component_cls
    return component_cls
