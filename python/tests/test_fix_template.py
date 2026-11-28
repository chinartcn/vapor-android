"""template.py 安全修复回归测试：AST 沙箱 + exec 赋值回写。

    PYTHONPATH=python python3 -m pytest python/tests/test_fix_template.py -q
"""

from __future__ import annotations

import os
import sys

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from Vapor import template as T  # noqa: E402


class _Host:
    """只实现模板渲染所需的最小 host 协议。"""

    def __init__(self):
        self.nodes = []

    def element(self, tag, props):
        node = {"tag": tag, "props": dict(props), "children": []}
        self.nodes.append(node)
        return node

    def text(self, value):
        node = {"tag": "#text", "value": value}
        self.nodes.append(node)
        return node

    def fragment(self):
        node = {"tag": "#frag", "children": []}
        self.nodes.append(node)
        return node

    def append_nodes(self, node, children):
        node["children"].extend(children)

    def bind_text(self, node, getter):
        node["getter"] = getter

    def bind_attr(self, node, key, getter):
        node.setdefault("bound", {})[key] = getter

    def bind_show(self, node, getter):
        node["show"] = getter

    def bind_if(self, frag, branches):
        frag["branches"] = branches

    def bind_for(self, frag, source, build, key):
        frag["for"] = (source, build, key)

    def bind_model(self, node, getter, setter):
        node["getter"] = getter
        node["setter"] = setter

    def on(self, node, event, handler, modifiers):
        node.setdefault("events", {})[event] = handler

    def slot(self, name, build=None, scope=None):
        node = {"tag": "#slot", "name": name}
        self.nodes.append(node)
        return node

    def component_node(self, name, props, slots):
        node = {"tag": name, "props": dict(props), "slots": slots}
        self.nodes.append(node)
        return node


class _Obj:
    name = "vapor"


def _scope():
    return T.Scope({
        "x": 3,
        "flag": True,
        "obj": _Obj(),
        "fn": lambda v: v * 2,
    })


# ---------------------------------------------------------------------------
# D-1：dunder 逃逸与危险调用
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expr", [
    "().__class__",
    "().__class__.__base__",
    "(1).__class__.__base__.__subclasses__()",
    "[].__getattribute__('__class__')",
    "'x'.__class__.__mro__",
    "obj.__dict__",
    "obj.__class__",
    "(1).__class__.__base__.__subclasses__()[0]",
    "obj['__class__']",
])
def test_dunder_escape_rejected(expr):
    with pytest.raises(T.TemplateError):
        T._eval(expr, _scope())


@pytest.mark.parametrize("expr", [
    "__import__('os').system('echo hi')",
    "eval('1+1')",
    "exec('x=1')",
    "compile('1+1', '<x>', 'eval')",
    "getattr(obj, 'name')",
    "setattr(obj, 'name', 'x')",
    "vars(obj)",
    "globals()",
    "type('X', (), {})",
    "open('/etc/passwd')",
    "(lambda: __import__('os'))()",
    "(lambda x=__import__('os'): x)()",
    "(lambda: 1)()",
    "obj.name.__self__",
    "'{0.__class__}'.format(obj)",
    "'{0.__class__.__base__.__subclasses__}'.format(())",
])
def test_dangerous_calls_rejected(expr):
    with pytest.raises(T.TemplateError):
        T._eval(expr, _scope())


@pytest.mark.parametrize("expr", [
    "import os",
    "global x",
    "nonlocal x",
    "x := 1",
])
def test_forbidden_syntax_rejected(expr):
    with pytest.raises(T.TemplateError):
        T._compile_handler(expr, 7)


def test_syntax_error_has_clear_message():
    with pytest.raises(T.TemplateError, match="表达式非法"):
        T._eval("1 +", _scope())


def test_template_render_rejects_dunder():
    """校验必须接在真实编译路径上，而不只是 _eval 入口。"""
    tpl = T.compile_template("<view><text>{{ ().__class__ }}</text></view>")
    host = _Host()
    with pytest.raises(T.TemplateError):
        tpl.render(host, _scope())


# ---------------------------------------------------------------------------
# 合法表达式不被误伤
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expr, expected", [
    ("1 + 2 * 3", 7),
    ("2 ** 3 - 1", 7),
    ("-5 + 1", -4),
    ("1 < 2 and not False", True),
    ("[1, 2, 3]", [1, 2, 3]),
    ("{'a': 1}", {"a": 1}),
    ("(1, 2)", (1, 2)),
    ("obj.name", "vapor"),
    ("obj.name.upper()", "VAPOR"),
    ("fn(x)", 6),
    ("len([1, 2])", 2),
    ("sum([1, 2])", 3),
    ("max(1, 9)", 9),
    ("[i * 2 for i in range(3)]", [0, 2, 4]),
    ("f'N={x}'", "N=3"),
    ("'Y' if flag else 'N'", "Y"),
    ("{'a': 1}['a']", 1),
])
def test_safe_expressions_still_work(expr, expected):
    assert T._eval(expr, _scope()) == expected


def test_safe_interpolation_and_binding_render():
    tpl = T.compile_template(
        '<view :title="f\'N={x}\'"><text>{{ obj.name }} {{ 1 + 2 }}</text></view>'
    )
    host = _Host()
    tpl.render(host, _scope())
    view = next(n for n in host.nodes if n["tag"] == "view")
    text = next(n for n in host.nodes if n["tag"] == "#text")
    assert view["bound"]["title"]() == "N=3"
    assert text["getter"]() == "vapor 3"


# ---------------------------------------------------------------------------
# D-2：exec 赋值经 Scope.__setitem__ 回写父作用域
# ---------------------------------------------------------------------------

def test_exec_assignment_writes_back_to_scope():
    scope = T.Scope({"x": 1})
    handler = T._compile_handler("x = 7", 1)
    handler(None, scope)
    assert scope["x"] == 7
    assert "x" not in scope.child()._vars  # 写的是父作用域，不是临时 child


def test_exec_augmented_assignment_writes_back_to_scope():
    scope = T.Scope({"count": 1})
    handler = T._compile_handler("count += 2", 1)
    handler(None, scope)
    assert scope["count"] == 3


def test_exec_new_name_stays_in_child():
    scope = T.Scope({"count": 1})
    handler = T._compile_handler("fresh = 9", 1)
    handler(None, scope)
    # 父作用域没有该绑定，按 Scope 语义只留在临时 child，不污染父作用域。
    assert "fresh" not in scope._vars
    assert scope["count"] == 1


def test_exec_handler_rejects_dangerous_statement():
    """exec 语句在编译事件处理器时就被校验拦下。"""
    with pytest.raises(T.TemplateError):
        T._compile_handler("__import__('os').system('echo hi')", 1)


def test_vmodel_setter_writes_back_to_scope():
    """v-model 的 setter 也走 exec：必须写回组件绑定。"""
    tpl = T.compile_template('<view><input v-model="count" /></view>')
    host = _Host()
    scope = T.Scope({"count": 1})
    tpl.render(host, scope)
    node = next(n for n in host.nodes if n["tag"] == "input")
    assert node["getter"]() == 1
    node["setter"](5)
    assert scope["count"] == 5
    assert node["getter"]() == 5


def test_vmodel_rejects_dunder_expression():
    tpl = T.compile_template('<view><input v-model="obj.__class__" /></view>')
    with pytest.raises(T.TemplateError):
        tpl.render(_Host(), _scope())
