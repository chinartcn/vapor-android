"""Vapor.template —— 无 vdom 的模板编译器（Vue 3 Vapor 风格）。

编译期做静态分析，运行期只做「精确更新」：
  * 静态属性在创建时写入一次；
  * 每个动态绑定生成一个独立 effect，只改那一个属性 / 那一段文本；
  * v-if 生成条件分支，切换时只挂载/卸载那一棵子树；
  * v-for 生成 keyed 列表协调（复用相同 key 的子树，不做全量 diff）。

模板只负责「生成什么节点、绑定什么表达式」，节点由 host（Vapor.ui 的
Renderer）创建。这样模板层与渲染层解耦，也能单独测试。

Host 协议（Vapor.ui.Renderer 实现）::

    element(tag, props) -> node
    text(value) -> node
    fragment() -> node
    component_node(name, props, slots) -> node
    bind_attr(node, key, getter)
    bind_text(node, getter)
    bind_show(node, getter)
    bind_if(frag, branches)                 # branches: [(cond, build), ...]
    bind_for(frag, source_getter, build_item, key_getter)
    bind_model(node, getter, setter)
    on(node, event, handler, modifiers)
    slot(name) -> node
"""

from __future__ import annotations

import re
from collections.abc import Mapping

__all__ = ["TemplateError", "parse", "compile_template", "CompiledTemplate", "Scope", "render_template"]


class TemplateError(Exception):
    """模板语法 / 表达式错误，带行号与列号。"""


# ---------------------------------------------------------------------------
# 作用域：eval 的 locals
# ---------------------------------------------------------------------------

class Scope(Mapping):
    """链式作用域：组件数据 -> 父作用域 -> 全局。"""

    __slots__ = ("_vars", "_parent")

    def __init__(self, vars=None, parent: "Scope | None" = None):
        self._vars = dict(vars or {})
        self._parent = parent

    def child(self, vars=None) -> "Scope":
        return Scope(vars, self)

    def __getitem__(self, key):
        if key in self._vars:
            return self._vars[key]
        if self._parent is not None:
            return self._parent[key]
        raise KeyError(key)

    def __setitem__(self, key, value):
        # 子作用域赋值时，若父作用域已有同名绑定，交给父作用域处理
        # （这样 v-model 写回的是组件里的 Ref，而不是在子作用域里遮蔽它）
        if key not in self._vars and self._parent is not None and key in self._parent:
            self._parent[key] = value
            return
        self._vars[key] = value

    def __iter__(self):
        seen = set()
        for k in self._vars:
            seen.add(k)
            yield k
        if self._parent is not None:
            for k in self._parent:
                if k not in seen:
                    seen.add(k)
                    yield k

    def __len__(self):
        return len(set(self.__iter__()))

    def set(self, key, value):
        self._vars[key] = value
        return self

    def update(self, mapping):
        self._vars.update(mapping)
        return self

    def __repr__(self):
        return f"Scope({list(self._vars)})"


_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "filter": filter, "float": float, "int": int,
    "isinstance": isinstance, "len": len, "list": list, "map": map,
    "max": max, "min": min, "print": print, "range": range, "repr": repr,
    "reversed": reversed, "round": round, "set": set, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "type": type, "zip": zip,
    "True": True, "False": False, "None": None,
}

_GLOBALS = {"__builtins__": _SAFE_BUILTINS, "__name__": "vapor.template"}


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

class _Node:
    __slots__ = ()


class Text(_Node):
    __slots__ = ("value", "line")

    def __init__(self, value, line):
        self.value = value
        self.line = line

    def __repr__(self):
        return f"Text({self.value!r})"


class Element(_Node):
    __slots__ = ("tag", "attrs", "children", "line", "self_closing")

    def __init__(self, tag, attrs, children, line, self_closing=False):
        self.tag = tag
        self.attrs = attrs            # list[(name, value_or_None)]
        self.children = children
        self.line = line
        self.self_closing = self_closing

    def attr(self, name, default=None):
        for k, v in self.attrs:
            if k == name:
                return True if v is None else v
        return default

    def has(self, name):
        return any(k == name for k, _ in self.attrs)

    def __repr__(self):
        return f"Element(<{self.tag}> x{len(self.children)})"


_NAME_STOP = set(" \t\r\n/>=")


class _Parser:
    def __init__(self, src: str):
        self.src = src
        self.i = 0
        self.n = len(src)

    def line_of(self, pos):
        return self.src.count("\n", 0, pos) + 1

    def parse(self):
        children = self.parse_children()
        return Element("#root", [], children, 0)

    def parse_children(self):
        out = []
        while self.i < self.n:
            if self.src.startswith("</", self.i):
                return out
            if self.src.startswith("<!--", self.i):
                end = self.src.find("-->", self.i)
                self.i = self.n if end < 0 else end + 3
                continue
            if self.src[self.i] == "<":
                el = self.parse_element()
                if el is None:
                    break
                out.append(el)
            else:
                start = self.i
                while self.i < self.n and not self.src.startswith("<", self.i):
                    self.i += 1
                raw = self.src[start:self.i]
                if raw.strip():
                    out.append(Text(raw, self.line_of(start)))
        return out

    def parse_element(self):
        start = self.i
        line = self.line_of(start)
        self.i += 1  # '<'
        if self.i < self.n and self.src[self.i] == "/":
            return None
        # 标签名
        name_start = self.i
        while self.i < self.n and self.src[self.i] not in _NAME_STOP:
            self.i += 1
        tag = self.src[name_start:self.i]
        if not tag:
            raise TemplateError(f"第 {line} 行：标签名为空")
        attrs = []
        self_closing = False
        while self.i < self.n:
            while self.i < self.n and self.src[self.i] in " \t\r\n":
                self.i += 1
            if self.i >= self.n:
                break
            if self.src.startswith("/>", self.i):
                self.i += 2
                self_closing = True
                break
            if self.src[self.i] == ">":
                self.i += 1
                break
            # 属性名
            aname_start = self.i
            while self.i < self.n and self.src[self.i] not in _NAME_STOP:
                self.i += 1
            aname = self.src[aname_start:self.i]
            if not aname:
                self.i += 1
                continue
            # = value
            while self.i < self.n and self.src[self.i] in " \t\r\n":
                self.i += 1
            value = None
            if self.i < self.n and self.src[self.i] == "=":
                self.i += 1
                while self.i < self.n and self.src[self.i] in " \t\r\n":
                    self.i += 1
                if self.i < self.n and self.src[self.i] in "\"'":
                    quote = self.src[self.i]
                    self.i += 1
                    vstart = self.i
                    while self.i < self.n and self.src[self.i] != quote:
                        self.i += 1
                    value = self.src[vstart:self.i]
                    self.i += 1
                else:
                    vstart = self.i
                    while self.i < self.n and self.src[self.i] not in _NAME_STOP:
                        self.i += 1
                    value = self.src[vstart:self.i]
            attrs.append((aname, value))

        children = []
        if not self_closing:
            children = self.parse_children()
            if self.src.startswith("</", self.i):
                end = self.src.find(">", self.i)
                close_name = self.src[self.i + 2:end].strip()
                if close_name and close_name != tag:
                    raise TemplateError(
                        f"第 {line} 行：<{tag}> 与 </{close_name}> 不匹配"
                    )
                self.i = end + 1
            else:
                raise TemplateError(f"第 {line} 行：<{tag}> 未闭合")
        return Element(tag, attrs, children, line, self_closing)


def parse(src: str) -> Element:
    return _Parser(src).parse()


# ---------------------------------------------------------------------------
# 表达式
# ---------------------------------------------------------------------------

_INTERP_RE = re.compile(r"\{\{(.+?)\}\}", re.S)

_COMPONENT_NAME = re.compile(r"^[A-Z]")


def _compile_expr(expr: str, line: int = 0):
    try:
        return compile(expr.strip(), f"<vapor:{line}>", "eval")
    except SyntaxError as exc:
        raise TemplateError(f"第 {line} 行：表达式非法 {expr!r}：{exc.msg}") from exc


def _eval(code, scope: Scope):
    return eval(code, _GLOBALS, scope)  # noqa: S307 —— 模板表达式是用户自己的代码


def _compile_handler(expr: str, line: int):
    """事件处理器工厂：返回 fn(event, scope)。

    表达式形式（on_tap / count + 1）按 eval 求值；若是语句（count += 1）按 exec 执行。
    """
    text = expr.strip()
    try:
        code = compile(text, f"<vapor-handler:{line}>", "eval")
    except SyntaxError:
        try:
            exec_code = compile(text, f"<vapor-handler:{line}>", "exec")
        except SyntaxError as exc:
            raise TemplateError(f"第 {line} 行：事件表达式非法 {expr!r}：{exc.msg}") from exc

        def run_exec(event, scope):
            local = scope.child({"$event": event})
            exec(exec_code, _GLOBALS, local)  # noqa: S102

        return run_exec

    def run_eval(event, scope):
        return _invoke_eval(code, scope.child({"$event": event}))

    return run_eval


def _invoke_eval(code, scope):
    value = _eval(code, scope)
    if callable(value):
        event = scope["$event"] if "$event" in scope else None
        return value(event)
    return value


# ---------------------------------------------------------------------------
# 属性分类
# ---------------------------------------------------------------------------

def _classify(attrs):
    """把属性分成：静态 / 绑定 / 事件 / 指令。"""
    static, bindings, events = [], [], []
    directives = {}
    for name, value in attrs:
        if name.startswith("v-") or name.startswith(":") or name.startswith("@") or name.startswith("#"):
            if name in ("v-if", "v-else-if", "v-else", "v-for", "v-show", "v-model", "v-text", "v-html"):
                directives[name] = value
            elif name in ("v-once", "v-cloak", "v-pre"):
                continue
            elif name == "v-bind":
                continue
            elif name.startswith("v-slot") or name.startswith("#"):
                key = "default"
                if ":" in name:
                    key = name.split(":", 1)[1]
                elif name.startswith("#"):
                    key = name[1:] or "default"
                directives["v-slot"] = (key, value)
            elif name.startswith("@"):
                ev, _, mods = name[1:].partition(".")
                events.append((ev, value, [m for m in mods.split(".") if m]))
            elif name.startswith("v-on:"):
                ev, _, mods = name[5:].partition(".")
                events.append((ev, value, [m for m in mods.split(".") if m]))
            elif name.startswith(":"):
                bindings.append((name[1:], value))
            elif name.startswith("v-bind:"):
                bindings.append((name[7:], value))
            else:
                raise TemplateError(f"不支持的指令: {name}")
        else:
            static.append((name, value))
    return static, bindings, events, directives


# ---------------------------------------------------------------------------
# 编译产物
# ---------------------------------------------------------------------------

class SlotContent:
    """父作用域里的一段子树，被渲染到子组件的 <slot> 处。"""

    __slots__ = ("children", "scope", "host")

    def __init__(self, children, scope, host):
        self.children = children
        self.scope = scope
        self.host = host

    def build(self, target):
        nodes = _compile_children(self.children, self.host, self.scope)
        self.host.append_nodes(target, nodes)
        return target

    def text_getter(self):
        """若插槽内容全是文本/插值，返回一个字符串 getter（供 <Text> 用）。"""
        for child in self.children:
            if not isinstance(child, Text):
                return None
        return _text_getter(self.children, self.scope)


class CompiledTemplate:
    __slots__ = ("ast", "source")

    def __init__(self, ast, source=""):
        self.ast = ast
        self.source = source

    def render(self, host, scope: Scope):
        """创建节点树（只调用一次），返回根节点列表。"""
        return _compile_children(self.ast.children, host, scope)


def compile_template(src: str) -> CompiledTemplate:
    """编译模板字符串（注意：模块内不要用 compile 作名字，会遮蔽内置 compile）。"""
    return CompiledTemplate(parse(src), src)


def render_template(src_or_template, host, scope: Scope):
    tpl = src_or_template if isinstance(src_or_template, CompiledTemplate) else compile_template(src_or_template)
    return tpl.render(host, scope)


# ---------------------------------------------------------------------------
# 代码生成（闭包版）
# ---------------------------------------------------------------------------

def _compile_children(children, host, scope):
    nodes = []
    i = 0
    n = len(children)
    while i < n:
        child = children[i]
        if isinstance(child, Text):
            nodes.extend(_compile_text(child, host, scope))
            i += 1
            continue

        # v-if / v-else-if / v-else 分组
        if child.has("v-if"):
            branches = [(child.attr("v-if"), child)]
            j = i + 1
            while j < n and isinstance(children[j], Element) and children[j].has("v-else-if"):
                branches.append((children[j].attr("v-else-if"), children[j]))
                j += 1
            if j < n and isinstance(children[j], Element) and children[j].has("v-else"):
                branches.append((None, children[j]))
                j += 1
            frag = host.fragment()
            _bind_if(host, frag, branches, scope)
            nodes.append(frag)
            i = j
            continue

        if child.has("v-else") or child.has("v-else-if"):
            raise TemplateError(f"第 {child.line} 行：v-else / v-else-if 前缺少 v-if")

        nodes.extend(_compile_element(child, host, scope))
        i += 1
    return nodes


def _bind_if(host, frag, branches, scope):
    built = []

    def make_builders():
        builders = []
        for cond_expr, el in branches:
            if cond_expr is None:
                cond = lambda: True  # noqa: E731
            else:
                code = _compile_expr(cond_expr, el.line)
                cond = lambda code=code: bool(_eval(code, scope))  # noqa: E731
            builders.append((cond, lambda el=el: _compile_element(el, host, scope)))
        return builders

    builders = make_builders()
    host.bind_if(frag, builders)


def _text_getter(nodes, scope):
    """把若干 Text AST 节点合成一个返回字符串的 getter（插值在 effect 内求值）。"""
    codes = []
    for node in nodes:
        parts = _INTERP_RE.split(node.value)
        for idx, part in enumerate(parts):
            if idx % 2 == 1:
                codes.append(("expr", _compile_expr(part, node.line)))
            else:
                codes.append(("lit", part))

    def getter():
        out = []
        for kind, payload in codes:
            out.append(payload if kind == "lit" else str(_eval(payload, scope)))
        return "".join(out)

    return getter


def _compile_text(node: Text, host, scope):
    parts = _INTERP_RE.split(node.value)
    if len(parts) == 1:
        return [host.text(node.value)]

    codes = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            codes.append(("expr", _compile_expr(part, node.line)))
        else:
            codes.append(("lit", part))

    def getter():
        out = []
        for kind, payload in codes:
            out.append(payload if kind == "lit" else str(_eval(payload, scope)))
        return "".join(out)

    n = host.text("")
    host.bind_text(n, getter)
    return [n]


def _for_aliases(alias, item, index, is_map, scope):
    """把 v-for 的 item / index / key 绑定到子作用域。"""
    if isinstance(alias, tuple):
        names = alias
    else:
        names = (alias,)

    if is_map:
        key, value = item
        values = (value, key, index)
    else:
        values = (item, index, index)

    for i, name in enumerate(names):
        if i < len(values):
            scope.set(name, values[i])
    scope.set("$index", index)
    scope.set("$key", values[1])


def _compile_element(el: Element, host, scope):
    if el.has("v-for"):
        frag = host.fragment()
        alias, source_expr = _parse_for(el.attr("v-for"), el.line)
        source_code = _compile_expr(source_expr, el.line)
        key_code = _compile_expr(el.attr(":key"), el.line) if el.has(":key") else None
        state = {"is_map": False}

        def source_getter():
            value = _eval(source_code, scope)
            if isinstance(value, Mapping):
                state["is_map"] = True
                return list(value.items())
            if hasattr(value, "items") and not isinstance(value, (str, bytes)):
                state["is_map"] = True
                return list(value.items())
            if hasattr(value, "to_list"):
                value = value.to_list()
            state["is_map"] = False
            return list(value or [])

        def build_item(item, index):
            child_scope = scope.child()
            _for_aliases(alias, item, index, state["is_map"], child_scope)
            return _compile_element_body(el, host, child_scope)

        def key_getter(item, index):
            if key_code is None:
                return index
            child_scope = scope.child()
            _for_aliases(alias, item, index, state["is_map"], child_scope)
            try:
                return _eval(key_code, child_scope)
            except Exception:
                return index

        host.bind_for(frag, source_getter, build_item, key_getter)
        return [frag]

    return _compile_element_body(el, host, scope)


def _parse_for(expr: str, line: int):
    if not expr:
        raise TemplateError(f"第 {line} 行：v-for 缺少表达式")
    m = re.match(r"^\s*(?:\(\s*(.+?)\s*\)|([A-Za-z_$][\w$]*))\s+(?:in|of)\s+(.+?)\s*$", expr, re.S)
    if not m:
        raise TemplateError(f"第 {line} 行：v-for 语法错误 {expr!r}，应为 'item in items'")
    if m.group(1):
        alias = tuple(a.strip() for a in m.group(1).split(",") if a.strip())
        if len(alias) == 1:
            alias = alias[0]
    else:
        alias = m.group(2)
    return alias, m.group(3)


def _compile_element_body(el: Element, host, scope):
    """创建一个元素/组件节点并绑定它的动态部分（不含 v-for）。"""
    if el.tag == "slot":
        name = el.attr("name") or "default"
        return [host.slot(name, lambda: _compile_children(el.children, host, scope), scope)]

    static, bindings, events, directives = _classify(el.attrs)
    props = {}
    for name, value in static:
        props[name] = _coerce(value)

    is_component = bool(_COMPONENT_NAME.match(el.tag))

    if is_component:
        # 组件在构建时就需要拿到动态属性的初值：先求值一次塞进 props，
        # 之后仍由 bind_attr 的 effect 持续更新（否则首帧只能读到默认值）。
        for key, expr in bindings:
            try:
                props[key] = _eval(_compile_expr(expr, el.line), scope)
            except Exception:
                pass
        slots = _collect_slots(el, host, scope)
        node = host.component_node(el.tag, props, slots)
    else:
        node = host.element(el.tag, props)

    for key, expr in bindings:
        code = _compile_expr(expr, el.line)
        host.bind_attr(node, key, lambda code=code: _eval(code, scope))

    for ev, expr, mods in events:
        handler_expr = expr or ""
        if not handler_expr:
            continue
        handler_fn = _compile_handler(handler_expr, el.line)
        host.on(node, ev, lambda fn=handler_fn, sc=scope: (lambda event: fn(event, sc)), mods)

    if "v-show" in directives:
        code = _compile_expr(directives["v-show"], el.line)
        host.bind_show(node, lambda code=code: bool(_eval(code, scope)))

    if "v-text" in directives or "v-html" in directives:
        expr = directives.get("v-text") or directives.get("v-html")
        code = _compile_expr(expr, el.line)
        host.bind_text(node, lambda code=code: str(_eval(code, scope)))

    if "v-model" in directives:
        expr = directives["v-model"]
        code = _compile_expr(expr, el.line)
        setter_code = compile(f"{expr.strip()} = __model_value", f"<vapor-model:{el.line}>", "exec")
        host.bind_model(
            node,
            lambda code=code: _eval(code, scope),
            lambda value, code=setter_code: exec(code, _GLOBALS, scope.child({"__model_value": value})),  # noqa: S102
        )

    # 子节点（组件用插槽，普通元素用子节点）
    if not is_component:
        children = _compile_children(el.children, host, scope)
        if children:
            host.append_nodes(node, children)

    return [node]


def _collect_slots(el: Element, host, scope):
    slots = {}
    plain = []
    for child in el.children:
        if isinstance(child, Element):
            slot_directive = None
            for name, value in child.attrs:
                if name == "v-slot" or name.startswith("v-slot:") or name.startswith("#"):
                    if name == "v-slot":
                        slot_directive = value or "default"
                    elif name.startswith("#"):
                        slot_directive = name[1:] or "default"
                    else:
                        slot_directive = name.split(":", 1)[1]
                    break
            if slot_directive is not None:
                # <template #name>...</template> 或直接带插槽指令的元素
                content = child.children if child.tag == "template" else [child]
                slots[slot_directive] = SlotContent(content, scope, host)
                continue
        if isinstance(child, Text):
            if not child.value.strip():
                continue
            plain.append(child)
        else:
            plain.append(child)
    if plain:
        slots["default"] = SlotContent(plain, scope, host)
    return slots


def _coerce(value):
    if value is None:
        return True
    low = value.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


_COMPONENT_NAME = re.compile(r"^[A-Z]")
