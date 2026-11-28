"""Renderer / Component：把模板与组件实例化为节点树。"""

from __future__ import annotations

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

__all__ = ["Renderer", "Component", "define_component", "register", "COMPONENTS"]


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


def register(component_cls):
    """把组件登记到全局注册表（模板里可直接用 <Name />）。"""
    COMPONENTS[component_cls.name or component_cls.__name__] = component_cls
    return component_cls
