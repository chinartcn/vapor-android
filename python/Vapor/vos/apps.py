"""Vapor OS 内置应用：设置 / 时钟 / 相册 / 计算器。

四个应用都是复杂 UI SDK 验证场景：数据由 props 注入、交互走回调，
颜色全部来自 ``current_theme()``，中文排版走 ``vos_base.type_style``。
"""

from __future__ import annotations

from .. import core
from ..ui import Component
from ..ui.renderer import queue_dirty
from ..theme import current_theme
from ..md3._common import theme_color
from ..vos_base.cjk import type_style
from ..vos_base.shape_morph import MorphSpec, ShapeMorph

__all__ = [
    "MdSettingsApp",
    "MdClockApp",
    "MdGalleryApp",
    "MdCalculatorApp",
    "COMPONENTS",
    "manifest",
    "register",
]

_SETTINGS_KEY = "vapor.vos.apps.settings"
_CLOCK_KEY = "vapor.vos.apps.clock"
_GALLERY_KEY = "vapor.vos.apps.gallery"
_CALC_KEY = "vapor.vos.apps.calculator"


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

def _label(r, getter, style="bodyMedium", role="onSurface"):
    node = r.text("")
    r.bind_text(node, getter)
    spec = type_style(style, lang="zh")
    node.props["size"] = spec["fontSize"]
    r.bind_attr(node, "color", lambda: theme_color(role))
    return node


def _section(r, title):
    node = _label(r, lambda: title, "titleSmall", "primary")
    node.props["pad"] = [16, 12, 16, 4]
    return node


def _app_root(r, **props):
    root = r.element("view", {"dir": "column", "w": "100%", "h": "100%", **props})
    r.bind_attr(root, "bg", lambda: theme_color("surface"))
    return root


def _top_bar(r, title, variant="small"):
    return r.component_node("MdTopAppBar", {"title": title, "variant": variant}, {})


def _scroll(r, content, props=None):
    merged = {"w": "100%", "h": "100%"}
    merged.update(props or {})
    return r.component_node("Scroll", merged, {"default": _NodeSlot(content)})


class _NodeSlot:
    """把已构建的 Node 包成 Renderer.slot 需要的 build(frag) 协议。"""

    def __init__(self, node):
        self.node = node

    def build(self, frag):
        frag.add(self.node)


def _state_or(key):
    return core.inject(key, None) or {}


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------

class MdSettingsApp(Component):
    """设置：分组列表 + 开关/滑杆，主题色实时联动。"""

    name = "MdSettingsApp"
    props = {"on_open": None}

    @staticmethod
    def setup(props, ctx):
        state = {
            "wifi": core.ref(True),
            "bluetooth": core.ref(False),
            "notifications": core.ref(True),
            "brightness": core.ref(0.6),
        }
        core.provide(_SETTINGS_KEY, state)
        return {}

    @staticmethod
    def build(r, props, slots):
        state = _state_or(_SETTINGS_KEY)
        root = _app_root(r)
        root.add(_top_bar(r, "设置", "large"))

        content = r.element("view", {"dir": "column", "w": "100%", "gap": 2})
        content.add(_section(r, "网络与连接"))

        def switch_row(label, key):
            row = r.element("view", {
                "dir": "row", "align": "center", "justify": "space-between",
                "w": "100%", "h": 56, "pad": [16, 0],
            })
            row.add(_label(r, lambda: label, "bodyLarge"))
            node = r.component_node("MdSwitch", {
                "checked": state[key].value,
                "label": "",
                "on_change": lambda value, k=key: _set_ref(state[k], value),
            }, {})
            r.bind_attr(node, "checked", lambda k=key: state[k].value)
            row.add(node)
            return row

        content.add(switch_row("Wi-Fi", "wifi"))
        content.add(switch_row("蓝牙", "bluetooth"))
        content.add(switch_row("通知", "notifications"))

        content.add(_section(r, "显示"))
        bright_row = r.element("view", {"dir": "column", "w": "100%", "pad": [16, 8]})
        bright_row.add(_label(r, lambda: "亮度", "bodyLarge"))
        slider = r.component_node("MdSlider", {
            "value": state["brightness"].value,
            "min": 0.0, "max": 1.0, "step": 0.1,
            "label": "亮度",
            "on_change": lambda value: _set_ref(state["brightness"], value),
        }, {})
        r.bind_attr(slider, "value", lambda: state["brightness"].value)
        bright_row.add(slider)
        content.add(bright_row)

        accent = r.element("view", {"w": 64, "h": 8, "radius": 4, "margin": [16, 8]})
        r.bind_attr(accent, "bg", lambda: theme_color("primary"))
        content.add(accent)
        root.props["_accent"] = accent

        root.add(_scroll(r, content))
        return [root]


def _set_ref(ref, value):
    ref.value = value


def _cell_side(cell):
    box = getattr(cell, "box", None)
    if box is not None and box.w > 0 and box.h > 0:
        return min(box.w, box.h)
    return 96.0


# ---------------------------------------------------------------------------
# 时钟
# ---------------------------------------------------------------------------

class MdClockApp(Component):
    """时钟：标签页切换，世界时钟用中文强调字阶大字。"""

    name = "MdClockApp"
    props = {"on_open": None}

    @staticmethod
    def setup(props, ctx):
        state = {"tab": core.ref(0), "alarm": core.ref(True)}
        core.provide(_CLOCK_KEY, state)
        return {}

    @staticmethod
    def build(r, props, slots):
        state = _state_or(_CLOCK_KEY)
        root = _app_root(r)
        root.add(_top_bar(r, "时钟", "small"))

        tabs = r.component_node("MdTabs", {
            "tabs": [
                {"label": "世界时钟", "value": 0},
                {"label": "闹钟", "value": 1},
                {"label": "计时器", "value": 2},
            ],
            "active": state["tab"].value,
            "on_change": lambda value: _set_ref(state["tab"], value),
        }, {})
        r.bind_attr(tabs, "active", lambda: state["tab"].value)
        root.add(tabs)

        style = type_style("displaySmall", emphasized=True, lang="zh")

        world = r.element("view", {"dir": "column", "align": "center", "gap": 8,
                                   "pad": [16, 32]})
        clock = _label(r, lambda: "10:24", "displaySmall", "primary")
        clock.props["size"] = style["fontSize"]
        clock.props["weight"] = style["fontWeight"]
        world.add(clock)
        world.add(_label(r, lambda: "北京 · 周四", "bodyMedium", "onSurfaceVariant"))
        root.props["_clock_style"] = dict(style)

        alarms = r.element("view", {"dir": "column", "w": "100%", "gap": 12,
                                    "pad": 16})
        alarms.add(_label(r, lambda: "07:30", "headlineMedium", "onSurface"))
        alarm_row = r.element("view", {
            "dir": "row", "align": "center", "justify": "space-between",
            "w": "100%", "h": 56,
        })
        alarm_row.add(_label(r, lambda: "工作日闹钟", "bodyLarge"))
        alarm_switch = r.component_node("MdSwitch", {
            "checked": state["alarm"].value,
            "label": "",
            "on_change": lambda value: _set_ref(state["alarm"], value),
        }, {})
        r.bind_attr(alarm_switch, "checked", lambda: state["alarm"].value)
        alarm_row.add(alarm_switch)
        alarms.add(alarm_row)

        timer = r.element("view", {"dir": "column", "align": "center", "gap": 16,
                                   "pad": 24})
        timer.add(_label(r, lambda: "00:09:41", "displayMedium", "onSurface"))
        progress = r.component_node("MdProgressIndicator", {
            "kind": "circular", "determinate": True, "value": 0.42, "size": 96,
        }, {})
        timer.add(progress)

        for index, page in ((0, world), (1, alarms), (2, timer)):
            r.bind_show(page, lambda i=index: state["tab"].value == i)
            root.add(page)

        return [root]


# ---------------------------------------------------------------------------
# 相册
# ---------------------------------------------------------------------------

class MdGalleryApp(Component):
    """相册：3 列网格 + ShapeMorph 选中反馈，局部脏区刷新。"""

    name = "MdGalleryApp"
    props = {"on_open": None}

    @staticmethod
    def setup(props, ctx):
        state = {"selected": core.ref(None)}
        core.provide(_GALLERY_KEY, state)
        return {}

    @staticmethod
    def build(r, props, slots):
        state = _state_or(_GALLERY_KEY)
        root = _app_root(r)
        root.add(_top_bar(r, "相册", "small"))

        grid = r.element("view", {"dir": "column", "w": "100%", "pad": 8, "gap": 8})
        cells = []

        def select(index):
            previous = state["selected"].value
            state["selected"].value = index
            for position, cell in enumerate(cells):
                morph = cell.extra.get("shape_morph")
                if morph is None:
                    continue
                if position == index:
                    morph.press()
                elif position == previous:
                    morph.release()
                if position not in (index, previous):
                    continue
                box = cell.box
                if box.w > 0 and box.h > 0:
                    queue_dirty(box.x, box.y, box.w, box.h)

        for row_index in range(3):
            row = r.element("view", {"dir": "row", "w": "100%", "gap": 8})
            for column in range(3):
                index = row_index * 3 + column
                cell = r.element("view", {
                    "grow": 1, "h": 96, "radius": 8,
                    "bg": theme_color("surfaceContainerHigh"),
                })
                r.bind_attr(cell, "bg", lambda i=index: theme_color(
                    "primaryContainer" if state["selected"].value == i
                    else "surfaceContainerHigh"))
                morph = ShapeMorph(MorphSpec("small", "largeIncreased", "fast"))
                cell.extra["shape_morph"] = morph
                r.bind_attr(cell, "radius", lambda c=cell: c.extra["shape_morph"].radius(
                    _cell_side(c)))

                def on_click(ev, i=index):
                    select(i)

                r.on(cell, "click", lambda handler=on_click: handler)
                cells.append(cell)
                row.add(cell)
            grid.add(row)

        root.add(grid)
        root.props["_cells"] = cells
        root.props["_grid"] = grid
        return [root]


# ---------------------------------------------------------------------------
# 计算器
# ---------------------------------------------------------------------------

_KEYS = [
    ["C", "±", "%", "÷"],
    ["7", "8", "9", "×"],
    ["4", "5", "6", "−"],
    ["1", "2", "3", "+"],
    ["0", ".", "="],
]
_OPERATORS = {"÷": "/", "×": "*", "−": "-", "+": "+"}


class MdCalculatorApp(Component):
    """计算器：密集键盘 + 显示区，点击反馈与数字排版。"""

    name = "MdCalculatorApp"
    props = {"on_open": None, "on_change": None}

    @staticmethod
    def setup(props, ctx):
        state = {
            "display": core.ref("0"),
            "acc": 0.0,
            "op": core.ref(None),
            "fresh": True,
        }
        core.provide(_CALC_KEY, state)
        return {}

    @staticmethod
    def build(r, props, slots):
        state = _state_or(_CALC_KEY)
        root = _app_root(r)
        root.add(_top_bar(r, "计算器", "small"))

        display = _label(r, lambda: state["display"].value, "displayMedium", "onSurface")
        display.props["pad"] = 24
        root.props["_display"] = display
        root.add(display)

        keypad = r.element("view", {"dir": "column", "w": "100%", "gap": 8, "pad": 12})
        buttons = {}
        for row_keys in _KEYS:
            row = r.element("view", {"dir": "row", "w": "100%", "gap": 8})
            for key in row_keys:
                variant = "filled" if key in _OPERATORS or key == "=" else "tonal"
                button = r.component_node("MdButton", {
                    "label": key, "variant": variant, "h": 56,
                    "on_click": lambda ev, k=key: _calc_press(state, props, k),
                }, {})
                r.bind_attr(button, "enabled", lambda k=key: (
                    state["op"].value is not None if k == "=" else True))
                button.props["grow"] = 1
                buttons[key] = button
                row.add(button)
            keypad.add(row)
        root.add(keypad)
        root.props["_buttons"] = buttons
        return [root]


def _calc_press(state, props, key):
    display = state["display"]
    text = display.value

    if key == "C":
        state["acc"] = 0.0
        state["op"].value = None
        state["fresh"] = True
        display.value = "0"
    elif key == "±":
        display.value = text[1:] if text.startswith("-") else "-" + text
    elif key == "%":
        try:
            display.value = _format(float(text) / 100.0)
        except ValueError:
            pass
    elif key in _OPERATORS:
        try:
            state["acc"] = float(text)
        except ValueError:
            state["acc"] = 0.0
        state["op"].value = _OPERATORS[key]
        state["fresh"] = True
    elif key == "=":
        op = state["op"].value
        if op is not None:
            try:
                value = float(text)
                result = _apply(state["acc"], op, value)
                display.value = _format(result)
            except (ValueError, ZeroDivisionError):
                display.value = "错误"
            state["op"].value = None
            state["fresh"] = True
    elif key == ".":
        if "." not in text:
            display.value = text + "."
        state["fresh"] = False
    else:
        if state["fresh"] or text == "0":
            display.value = key
        else:
            display.value = text + key
        state["fresh"] = False

    callback = props.get("on_change")
    if callable(callback):
        callback(display.value)


def _apply(left: float, op: str, right: float) -> float:
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/":
        if right == 0:
            raise ZeroDivisionError
        return left / right
    return right


def _format(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")


COMPONENTS = [MdSettingsApp, MdClockApp, MdGalleryApp, MdCalculatorApp]


def manifest():
    return {
        "abi": 1,
        "name": "vapor.vos.apps",
        "version": "1.0.0",
        "components": [
            {
                "name": c.name,
                "props": dict(c.props),
                "description": (c.__doc__ or "").strip().splitlines()[0],
            }
            for c in COMPONENTS
        ],
    }


def register(host):
    for cls in COMPONENTS:
        host.register(cls)
