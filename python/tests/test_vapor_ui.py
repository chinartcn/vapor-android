"""Vapor.ui / Vapor.template 端到端测试（无需原生扩展）。

    PYTHONPATH=python python3 python/tests/test_vapor_ui.py
"""

from __future__ import annotations

import os
import sys

try:  # 已安装（CI 的 wheel / maturin develop）时优先用安装版
    import Vapor  # noqa: F401
except ImportError:  # 本地源码运行
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor import ui as V           # noqa: E402
from Vapor import template as T     # noqa: E402
from Vapor import svg as SVG        # noqa: E402


def app_for(root, w=360, h=640):
    surface = V.Surface(w, h, use_native=False)
    app = V.App(root, surface=surface)
    app.mount()
    return app


def find(node, pred):
    for n in node.walk():
        if pred(n):
            return n
    return None


def by_text(node, text):
    return find(node, lambda n: n.tag == "#text" and n.text == text)


def pixel(app, x, y):
    i = (y * app.surface.width + x) * 4
    buf = app.surface.buffer
    return (buf[i], buf[i + 1], buf[i + 2], buf[i + 3])


# ---------------------------------------------------------------------------
# 模板解析 / 编译
# ---------------------------------------------------------------------------

def test_parse_basic():
    ast = T.parse('<View a="1"><Text>hi</Text></View>')
    assert ast.tag == "#root"
    assert len(ast.children) == 1
    el = ast.children[0]
    assert el.tag == "View"
    assert el.attr("a") == "1"
    assert el.children[0].tag == "Text"


def test_parse_error_unclosed():
    try:
        T.parse("<View><Text>x</Text>")
    except T.TemplateError as exc:
        assert "未闭合" in str(exc)
    else:
        raise AssertionError("应当报未闭合")


def test_parse_mismatch():
    try:
        T.parse("<View></Text>")
    except T.TemplateError:
        pass
    else:
        raise AssertionError("应当报标签不匹配")


def test_interpolation_and_binding():
    class Root(V.Component):
        template = '<View><Text :size="20">{{ greeting }}, {{ name }}</Text></View>'
        def setup(self, props, ctx):
            return {"greeting": "HI", "name": V.ref("ADA")}

    app = app_for(Root)
    node = by_text(app.root, "HI, ADA")
    assert node is not None, [n.text for n in app.root.walk() if n.tag == "#text"]
    assert node.props.get("size") == 20
    app.unmount()


def test_v_if_switch():
    holder = {}

    class Root(V.Component):
        template = '<View><Text v-if="show">ON</Text><Text v-else>OFF</Text></View>'
        def setup(self, props, ctx):
            holder["show"] = V.ref(True)
            return {"show": holder["show"]}

    app = app_for(Root)
    assert by_text(app.root, "ON") is not None
    holder["show"].value = False
    app.tick()
    assert by_text(app.root, "ON") is None
    assert by_text(app.root, "OFF") is not None
    app.unmount()


def test_v_for_keyed_reuse():
    holder = {}

    class Root(V.Component):
        template = '''
          <View>
            <Text v-for="item in items" :key="item">{{ item }}</Text>
          </View>
        '''
        def setup(self, props, ctx):
            holder["items"] = V.ref(["a", "b"])
            return {"items": holder["items"]}

    app = app_for(Root)
    first_a = by_text(app.root, "a")
    assert first_a is not None
    holder["items"].value = ["a", "b", "c"]
    app.tick()
    assert by_text(app.root, "a") is first_a, "相同 key 应复用节点"
    assert by_text(app.root, "c") is not None
    holder["items"].value = ["c"]
    app.tick()
    assert by_text(app.root, "a") is None
    assert by_text(app.root, "c") is not None
    app.unmount()


def test_v_for_index():
    class Root(V.Component):
        template = '<View><Text v-for="(item, i) in items" :key="i">{{ i }}-{{ item }}</Text></View>'
        def setup(self, props, ctx):
            return {"items": ["x", "y"]}

    app = app_for(Root)
    assert by_text(app.root, "0-x") is not None
    assert by_text(app.root, "1-y") is not None
    app.unmount()


def test_v_show():
    holder = {}

    class Root(V.Component):
        template = '<View><Text v-show="visible">HIDDEN?</Text></View>'
        def setup(self, props, ctx):
            holder["v"] = V.ref(True)
            return {"visible": holder["v"]}

    app = app_for(Root)
    node = by_text(app.root, "HIDDEN?")
    host = node.parent              # <Text> 组件节点，v-show 绑在它上面
    assert host.visible is True
    holder["v"].value = False
    app.tick()
    assert host.visible is False
    assert any(n.tag == "view" for n in app.root.walk())
    app.unmount()


def test_v_model_switch():
    holder = {}

    class Root(V.Component):
        template = '<View><Switch v-model="on" /></View>'
        def setup(self, props, ctx):
            holder["on"] = V.ref(False)
            return {"on": holder["on"]}

    app = app_for(Root)
    switch = find(app.root, lambda n: n.tag == "view" and "radius" in n.props and n.box.w == 46)
    assert switch is not None, [n.tag for n in app.root.walk()]
    app.dispatch("click", switch.box.x + 5, switch.box.y + 5)
    assert holder["on"].value is True
    app.tick()
    app.dispatch("click", switch.box.x + 5, switch.box.y + 5)
    assert holder["on"].value is False
    app.unmount()


def test_event_bubbling_and_stop():
    calls = []

    class Root(V.Component):
        template = '''
          <View @click="outer">
            <View @click.stop="inner"><Text>HIT</Text></View>
          </View>
        '''
        def setup(self, props, ctx):
            return {
                "outer": lambda e: calls.append("outer"),
                "inner": lambda e: calls.append("inner"),
            }

    app = app_for(Root)
    text = by_text(app.root, "HIT")
    app.dispatch("click", text.box.x + 1, text.box.y + 1)
    assert calls == ["inner"], calls
    app.unmount()


def test_computed_drives_text():
    holder = {}

    class Root(V.Component):
        template = '<View><Text>{{ label }}</Text></View>'
        def setup(self, props, ctx):
            holder["count"] = V.ref(0)
            holder["label"] = V.computed(lambda: f"N={holder['count'].value}")
            return {"label": holder["label"]}

    app = app_for(Root)
    assert by_text(app.root, "N=0") is not None
    holder["count"].value = 3
    app.tick()
    assert by_text(app.root, "N=3") is not None
    app.unmount()


def test_slots_named_and_default():
    class Card(V.Component):
        template = '''
          <View>
            <slot name="header" />
            <slot />
          </View>
        '''

    class Root(V.Component):
        template = '''
          <Card>
            <template #header><Text>HEAD</Text></template>
            <Text>BODY</Text>
          </Card>
        '''
        components = {"Card": Card}

    app = app_for(Root)
    assert by_text(app.root, "HEAD") is not None
    assert by_text(app.root, "BODY") is not None
    app.unmount()


def test_slot_fallback():
    class Card(V.Component):
        template = '<View><slot><Text>FALLBACK</Text></slot></View>'

    class Root(V.Component):
        template = '<Card />'
        components = {"Card": Card}

    app = app_for(Root)
    assert by_text(app.root, "FALLBACK") is not None
    app.unmount()


def test_component_props_reactive():
    holder = {}

    class Label(V.Component):
        template = '<Text :text="text" />'
        props = {"text": ""}

    class Root(V.Component):
        template = '<View><Label :text="msg" /></View>'
        components = {"Label": Label}
        def setup(self, props, ctx):
            holder["msg"] = V.ref("A")
            return {"msg": holder["msg"]}

    app = app_for(Root)
    assert by_text(app.root, "A") is not None
    holder["msg"].value = "B"
    app.tick()
    assert by_text(app.root, "B") is not None
    app.unmount()


# ---------------------------------------------------------------------------
# 布局 / 绘制
# ---------------------------------------------------------------------------

def test_layout_padding_and_size():
    class Root(V.Component):
        template = '<View padding="10" w="200" h="100"><Text size="20">X</Text></View>'

    app = app_for(Root, 360, 640)
    root = app.root.children[0]
    assert root.box.x == 0 and root.box.y == 0
    assert root.box.w == 200 and root.box.h == 100, root.box
    app.unmount()


def test_layout_row_gap():
    class Root(V.Component):
        template = '''
          <View dir="row" gap="10" w="300" h="40">
            <View w="50" h="40" /><View w="50" h="40" />
          </View>
        '''

    app = app_for(Root)
    row = find(app.root, lambda n: n.tag == "view" and n.props.get("dir") == "row")
    assert row is not None
    kids = [k for k in V._flow(row) if k.tag in ("view", "#component")]
    assert kids[0].box.x == 0, kids[0].box
    assert kids[1].box.x == 60, kids[1].box
    app.unmount()


def test_background_pixels():
    class Root(V.Component):
        template = '<View bg="#FF0000" w="100" h="50" />'

    app = app_for(Root, 200, 200)
    r, g, b, a = pixel(app, 10, 10)
    assert (r, g, b) == (0xFF, 0, 0), (r, g, b)
    assert a == 255
    app.unmount()


def test_text_inside_slot_is_painted():
    class Card(V.Component):
        template = '<View bg="#000000" w="120" h="60"><slot /></View>'

    class Root(V.Component):
        template = '<View w="200" h="120"><Card><Text color="#FFFFFF" size="24">AB</Text></Card></View>'
        components = {"Card": Card}

    app = app_for(Root, 200, 120)
    # 插槽里的文本必须真的画到帧缓冲（历史上容器壳 0x0 会导致整棵子树不绘制）
    painted = 0
    buf = app.surface.buffer
    for i in range(0, len(buf), 4):
        if buf[i] or buf[i + 1] or buf[i + 2]:
            painted += 1
    assert painted > 50, painted
    app.unmount()


def test_round_rect_and_border():
    class Root(V.Component):
        template = '<View bg="#00FF00" radius="8" w="60" h="60" />'

    app = app_for(Root, 100, 100)
    r, g, b, _ = pixel(app, 30, 30)
    assert (r, g, b) == (0, 0xFF, 0), (r, g, b)
    # 圆角处不应是绿色
    r2, g2, b2, _ = pixel(app, 0, 0)
    assert (r2, g2, b2) != (0, 0xFF, 0), (r2, g2, b2)
    app.unmount()


def test_text_wrap_grows_height():
    class Root(V.Component):
        template = (
            '<View w="140">'
            '<Text :w="140" :wrap="True">AAAA BBBB CCCC DDDD EEEE FFFF GGGG</Text>'
            '</View>'
        )

    app = app_for(Root)
    node = find(app.root, lambda n: n.tag == "#text")
    assert node is not None
    assert node.box.h > V.line_height(16) * 1.5, node.box
    app.unmount()


def test_text_no_wrap_single_line():
    class Root(V.Component):
        template = '<View w="80"><Text>AAAA BBBB CCCC DDDD</Text></View>'

    app = app_for(Root)
    node = find(app.root, lambda n: n.tag == "#text")
    assert node is not None
    assert abs(node.box.h - V.line_height(16)) < 1.0, node.box
    app.unmount()


def test_absolute_positioning():
    class Root(V.Component):
        template = '''
          <View w="200" h="200">
            <View absolute="true" x="20" y="30" w="50" h="50" bg="#FFFFFF" />
          </View>
        '''

    app = app_for(Root)
    abs_node = find(app.root, lambda n: n.tag == "view" and n.box.x == 20 and n.box.y == 30)
    assert abs_node is not None
    app.unmount()


def test_progress_bar_width():
    class Root(V.Component):
        template = '<View w="200"><ProgressBar :value="0.5" w="200" /></View>'

    app = app_for(Root)
    fill = find(app.root, lambda n: n.tag == "view" and n.box.w > 0 and n.props.get("bg") == "#2D7FF9"
                and n.props.get("absolute"))
    assert fill is not None
    assert abs(fill.box.w - 100) <= 2, fill.box
    app.unmount()


def test_scroll_offsets_children():
    class Root(V.Component):
        template = '''
          <Scroll :scroll_y="offset" h="100" w="200">
            <View h="400" w="200" bg="#123456" />
          </Scroll>
        '''

    app = app_for(Root)
    class Holder:
        offset = V.ref(0)
    h = Holder()
    app.unmount()

    class Root2(V.Component):
        template = '<Scroll :scroll_y="offset" h="100" w="200"><View h="400" w="200" bg="#123456" /></Scroll>'
        def setup(self, props, ctx):
            self.offset = V.ref(0)
            _HOLDER["r"] = self.offset
            return {"offset": self.offset}

    _HOLDER = {}
    app = app_for(Root2)
    content = find(app.root, lambda n: n.tag == "view" and n.box.h == 400)
    assert content is not None
    y0 = content.box.y
    _HOLDER["r"].value = 50
    app.tick()
    assert abs(content.box.y - (y0 - 50)) < 1, (content.box.y, y0)
    app.unmount()


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

def test_svg_parse_icons():
    doc = SVG.parse_svg(SVG.ICONS["home"])
    assert doc.shapes, "home 图标应有形状"
    assert doc.view_box == (0, 0, 24, 24)


def test_svg_primitives_to_paths():
    doc = SVG.parse_svg(
        '<svg viewBox="0 0 100 100">'
        '<rect x="10" y="10" width="30" height="20"/>'
        '<circle cx="60" cy="60" r="10"/>'
        '<line x1="0" y1="0" x2="50" y2="50"/>'
        '<polygon points="0,0 10,0 10,10"/>'
        '</svg>'
    )
    assert len(doc.shapes) == 4, len(doc.shapes)
    assert doc.shapes[0].d.startswith("M10")
    assert "A10" in doc.shapes[1].d


def test_svg_transform():
    m = SVG.parse_transform("translate(10,20) scale(2)")
    assert m.apply(1, 1) == (12.0, 22.0), m.apply(1, 1)


def test_svg_render_pixels():
    class Root(V.Component):
        template = '<Svg source="home" :w="48" :h="48" color="#FFFFFF" />'

    app = app_for(Root, 100, 100)
    r, g, b, _ = pixel(app, 24, 24)
    assert (r + g + b) > 0, "SVG 应画出像素"
    app.unmount()


def test_svg_color_value():
    assert SVG.color_value("#FF0000") == 0xFF0000
    assert SVG.color_value("red") == 0xFF0000
    assert SVG.color_value("none") is None
    assert SVG.color_value("#0F0") == 0x00FF00


# ---------------------------------------------------------------------------
# 页面栈
# ---------------------------------------------------------------------------

def test_page_stack_navigation():
    class Home(V.Page):
        template = '<Page title="HOME"><Text>HOME BODY</Text></Page>'

    class Detail(V.Page):
        template = '<Page title="DETAIL"><Text>DETAIL BODY</Text></Page>'

    class Root(V.Component):
        template = '<PageStack :initial="initial" />'
        components = {"Page": V.Page}
        def setup(self, props, ctx):
            return {"initial": Detail}

    app = app_for(Root)
    nav = V.Navigator.current
    assert nav is not None
    assert nav.depth == 1
    nav.push(Home, {})
    app.tick()
    assert nav.depth == 2
    nav.pop()
    app.tick()
    assert nav.depth == 1
    app.unmount()


def test_list_view_render():
    class Root(V.Component):
        template = '<View><ListView :items="items" :render_item="render" /></View>'
        def setup(self, props, ctx):
            def render(item, index, r):
                return r.text(f"{index}:{item}")
            return {"items": ["a", "b"], "render": render}

    app = app_for(Root)
    assert by_text(app.root, "0:a") is not None
    assert by_text(app.root, "1:b") is not None
    app.unmount()


def test_component_lifecycle_scope_stop():
    runs = []

    class Root(V.Component):
        template = '<View><Text :text="label" /></View>'
        def setup(self, props, ctx):
            count = V.ref(0)
            V.effect(lambda: runs.append(count.value))
            return {"label": "X"}

    app = app_for(Root)
    before = len(runs)
    app.unmount()
    assert before >= 1


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failures = []
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures.append((fn.__name__, exc))
            print(f"[FAIL] {fn.__name__}: {exc}")
        else:
            passed += 1
            print(f"[ok] {fn.__name__}")
    print(f"\n{passed}/{len(tests)} passed")
    if failures:
        import traceback
        for name, exc in failures:
            print(f"\n--- {name} ---")
            traceback.print_exception(type(exc), exc, exc.__traceback__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
