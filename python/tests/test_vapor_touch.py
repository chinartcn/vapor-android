"""触控链路测试：down / move / up / cancel -> 点击、拖动、长按、滚动、惯性。

    PYTHONPATH=python python3 python/tests/test_vapor_touch.py
"""

from __future__ import annotations

import os
import sys

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor import ui as V  # noqa: E402


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


def center(node):
    box = node.box
    return int(box.x + box.w / 2), int(box.y + box.h / 2)


# ---------------------------------------------------------------------------
# 点击 / 拖动 / 长按
# ---------------------------------------------------------------------------

def test_tap_triggers_click():
    calls = []

    class Root(V.Component):
        template = '<View w="300" h="200"><Button text="OK" :on_click="tap" /></View>'
        def setup(self, props, ctx):
            return {"tap": lambda e=None: calls.append(1)}

    app = app_for(Root)
    x, y = center(by_text(app.root, "OK"))
    app.feed("down", x, y, now=0.0)
    app.feed("up", x, y, now=0.08)
    assert calls == [1], calls
    app.unmount()


def test_small_move_still_click():
    calls = []

    class Root(V.Component):
        template = '<View w="300" h="200"><Button text="OK" :on_click="tap" /></View>'
        def setup(self, props, ctx):
            return {"tap": lambda e=None: calls.append(1)}

    app = app_for(Root)
    x, y = center(by_text(app.root, "OK"))
    app.feed("down", x, y, now=0.0)
    app.feed("move", x + 3, y + 2, now=0.03)
    app.feed("up", x + 3, y + 2, now=0.08)
    assert calls == [1], calls
    app.unmount()


def test_drag_dispatch():
    seen = []

    class Root(V.Component):
        template = '<View w="300" h="200" @dragstart="start" @drag="drag" @dragend="end"><Text>X</Text></View>'
        def setup(self, props, ctx):
            return {
                "start": lambda e: seen.append(("start", e.dx)),
                "drag": lambda e: seen.append(("drag", e.dx, e.dy)),
                "end": lambda e: seen.append(("end",)),
            }

    app = app_for(Root)
    app.feed("down", 40, 40, now=0.0)
    app.feed("move", 90, 40, now=0.05)
    app.feed("move", 140, 40, now=0.10)
    app.feed("up", 140, 40, now=0.15)
    kinds = [s[0] for s in seen]
    assert kinds == ["start", "drag", "drag", "end"], seen
    assert seen[1][1] == 50 and seen[1][2] == 0, seen[1]
    app.unmount()


def test_cancel_does_not_click():
    calls = []

    class Root(V.Component):
        template = '<View w="300" h="200"><Button text="OK" :on_click="tap" /></View>'
        def setup(self, props, ctx):
            return {"tap": lambda e=None: calls.append(1)}

    app = app_for(Root)
    x, y = center(by_text(app.root, "OK"))
    app.feed("down", x, y, now=0.0)
    app.feed("move", x + 40, y, now=0.05)
    app.feed("cancel", x + 40, y, now=0.09)
    assert calls == [], calls
    app.unmount()


def test_long_press_then_no_click():
    long_calls = []
    clicks = []

    class Root(V.Component):
        template = '<View w="300" h="200" @longpress="lp" @click="tap"><Text>X</Text></View>'
        def setup(self, props, ctx):
            return {"lp": lambda e: long_calls.append(1), "tap": lambda e: clicks.append(1)}

    app = app_for(Root)
    app.feed("down", 50, 50, now=0.0)
    app.tick(now=0.6)
    assert long_calls == [1], long_calls
    app.feed("up", 50, 50, now=0.7)
    assert clicks == [], clicks
    app.unmount()


def test_pointer_events_fire():
    seen = []

    class Root(V.Component):
        template = '<View w="300" h="200" @pointerdown="down" @pointerup="up"><Text>X</Text></View>'
        def setup(self, props, ctx):
            return {"down": lambda e: seen.append("down"), "up": lambda e: seen.append("up")}

    app = app_for(Root)
    app.feed("down", 30, 30, now=0.0)
    app.feed("up", 30, 30, now=0.05)
    assert seen == ["down", "up"], seen
    app.unmount()


# ---------------------------------------------------------------------------
# 滚动 / 惯性
# ---------------------------------------------------------------------------

def _scroll_app():
    class Root(V.Component):
        template = (
            '<View w="200" h="200">'
            '<Scroll h="100" w="200">'
            '<View h="400" w="200"><Text>CT</Text></View>'
            '</Scroll>'
            '</View>'
        )

    app = app_for(Root, 200, 200)
    viewport = find(app.root, lambda n: "scroll" in n.extra)
    assert viewport is not None
    return app, viewport


def test_scroll_drag_moves_content():
    app, viewport = _scroll_app()
    assert viewport.scroll_y == 0
    app.feed("down", 100, 80, now=0.0)
    app.feed("move", 100, 50, now=0.05)
    app.feed("move", 100, 20, now=0.10)
    assert viewport.scroll_y > 0, viewport.scroll_y
    app.feed("up", 100, 20, now=0.15)
    app.unmount()


def test_scroll_clamped_to_content():
    app, viewport = _scroll_app()
    app.feed("down", 100, 90, now=0.0)
    for i in range(1, 12):
        app.feed("move", 100, 90 - i * 40, now=i * 0.05)
    app.feed("up", 100, 90, now=0.6)
    # 内容 400 - 视口 100 = 最大 300
    assert viewport.scroll_y <= 300.5, viewport.scroll_y
    app.unmount()


def test_scroll_fling_continues():
    app, viewport = _scroll_app()
    app.feed("down", 100, 90, now=0.0)
    app.feed("move", 100, 60, now=0.02)
    app.feed("move", 100, 30, now=0.04)
    app.feed("up", 100, 30, now=0.05)
    after_up = viewport.scroll_y
    app.tick(now=0.20)
    assert viewport.scroll_y > after_up, (after_up, viewport.scroll_y)
    app.unmount()


# ---------------------------------------------------------------------------
# 控件
# ---------------------------------------------------------------------------

def test_slider_drag_updates_value():
    values = []

    class Root(V.Component):
        template = '<View w="200" h="100"><Slider :on_change="chg" w="200" /></View>'
        def setup(self, props, ctx):
            return {"chg": lambda v: values.append(round(v, 2))}

    app = app_for(Root, 200, 100)
    app.feed("down", 50, 16, now=0.0)
    assert values and abs(values[-1] - 0.25) < 0.02, values
    app.feed("move", 150, 16, now=0.05)
    assert abs(values[-1] - 0.75) < 0.02, values
    app.feed("up", 150, 16, now=0.1)
    app.unmount()


def test_button_pressed_opacity():
    class Root(V.Component):
        template = '<View w="300" h="200"><Button text="OK" /></View>'

    app = app_for(Root)
    button = find(app.root, lambda n: n.tag == "view" and "click" in n.events)
    assert button is not None
    x, y = center(by_text(app.root, "OK"))
    app.feed("down", x, y, now=0.0)
    assert abs(float(button.props.get("opacity", 1)) - 0.82) < 0.01, button.props.get("opacity")
    app.feed("up", x, y, now=0.05)
    assert abs(float(button.props.get("opacity", 1)) - 1.0) < 0.01, button.props.get("opacity")
    app.unmount()


# ---------------------------------------------------------------------------
# 按键 / 返回
# ---------------------------------------------------------------------------

def test_back_key_calls_handler():
    calls = []

    class Root(V.Component):
        template = '<View w="200" h="200" />'

    app = app_for(Root)
    app.on("back", lambda: calls.append(1))
    app.feed_key(4)
    assert calls == [1], calls
    app.feed_key(27)          # ESC 同样映射成返回
    assert calls == [1, 1], calls
    app.unmount()


# ---------------------------------------------------------------------------
# 第二阶段：双击 / 双指 / 方向锁 / 滚轮 / 完整触控 API
# ---------------------------------------------------------------------------

def test_double_click():
    calls = []

    class Root(V.Component):
        template = '<View w="300" h="200" @dblclick="dbl"><Text>X</Text></View>'
        def setup(self, props, ctx):
            return {"dbl": lambda e: calls.append(1)}

    app = app_for(Root)
    app.feed("down", 50, 50, now=0.0)
    app.feed("up", 50, 50, now=0.05)
    app.feed("down", 50, 50, now=0.15)
    app.feed("up", 50, 50, now=0.20)
    app.tick(now=0.21)
    assert calls == [1], calls

    # 两次点击间隔过长 -> 不算双击
    app.feed("down", 50, 50, now=1.0)
    app.feed("up", 50, 50, now=1.05)
    app.feed("down", 50, 50, now=2.0)
    app.feed("up", 50, 50, now=2.05)
    assert calls == [1], calls
    app.unmount()


def test_pinch_scale():
    scales = []

    class Root(V.Component):
        template = '<View w="300" h="300" @pinch="on_pinch"><Text>X</Text></View>'
        def setup(self, props, ctx):
            return {"on_pinch": lambda e: scales.append(round(e.scale, 2))}

    app = app_for(Root, 300, 300)
    app.feed("down", 150, 150, now=0.0)
    app.feed("pointerdown", 150, 150, count=2, now=0.02)
    app.feed("move", 100, 150, count=2, x2=200, y2=150, now=0.05)
    app.feed("move", 80, 150, count=2, x2=220, y2=150, now=0.10)
    assert scales, scales
    assert scales[-1] >= 1.3, scales
    app.feed("pointerup", 220, 150, count=2, now=0.12)
    app.feed("up", 80, 150, count=2, now=0.15)
    app.unmount()


def test_pinch_suppresses_click():
    clicks = []

    class Root(V.Component):
        template = '<View w="300" h="300" @click="tap" @pinch="on_pinch"><Text>X</Text></View>'
        def setup(self, props, ctx):
            return {"tap": lambda e: clicks.append(1), "on_pinch": lambda e: None}

    app = app_for(Root, 300, 300)
    app.feed("down", 150, 150, now=0.0)
    app.feed("pointerdown", 150, 150, count=2, now=0.02)
    app.feed("move", 120, 150, count=2, x2=180, y2=150, now=0.05)
    app.feed("pointerup", 180, 150, count=2, now=0.08)
    app.feed("up", 120, 150, count=2, now=0.10)
    assert clicks == [], clicks
    app.unmount()


def test_axis_lock_horizontal_no_scroll():
    app, viewport = _scroll_app()
    app.feed("down", 100, 80, now=0.0)
    app.feed("move", 160, 78, now=0.05)   # 横向为主 -> 方向锁 x
    app.feed("move", 200, 76, now=0.10)
    assert viewport.scroll_y == 0, viewport.scroll_y
    app.feed("up", 200, 76, now=0.15)
    app.unmount()


def test_drag_event_axis():
    axes = []

    class Root(V.Component):
        template = '<View w="300" h="200" @drag="drag"><Text>X</Text></View>'
        def setup(self, props, ctx):
            return {"drag": lambda e: axes.append(e.axis)}

    app = app_for(Root)
    app.feed("down", 50, 50, now=0.0)
    app.feed("move", 50, 100, now=0.05)   # 纵向
    app.feed("up", 50, 100, now=0.1)
    assert axes and axes[0] == "y", axes
    app.unmount()


def test_wheel_scrolls():
    app, viewport = _scroll_app()
    app.feed("move", 100, 50, now=0.0)   # 记录悬停位置
    app.feed_wheel(0, 1)
    assert abs(viewport.scroll_y - 40.0) < 0.01, viewport.scroll_y
    app.feed_wheel(0, -1)
    assert viewport.scroll_y == 0
    app.unmount()


def test_touch_event_api():
    from rust.Vapor import _normalize_event, EVT_MOTION, EVT_BUTTON, EVT_KEY

    class Raw:
        def __init__(self, t, a, k=1):
            self.type = t
            self.action = a
            self.x = 10
            self.y = 20
            self.x2 = 40
            self.y2 = 60
            self.button = 1
            self.keycode = k
            self.timestamp_ms = 123

    move = _normalize_event(Raw(EVT_MOTION, 2, 2))
    assert move.kind == "move" and move.is_multi_touch
    assert abs(move.spread() - 50.0) < 0.01
    assert move.center == (25.0, 40.0)

    down = _normalize_event(Raw(EVT_BUTTON, 0, 1))
    assert down.kind == "down" and down.is_down

    key = _normalize_event(Raw(EVT_KEY, 0, 4))
    assert key.kind == "key" and key.keycode == 4


def test_press_release_events():
    seen = []

    class Root(V.Component):
        template = '<View w="200" h="120" @press="p" @release="r"><Text>X</Text></View>'
        def setup(self, props, ctx):
            return {"p": lambda e: seen.append("press"), "r": lambda e: seen.append("release")}

    app = app_for(Root)
    node = find(app.root, lambda n: n.tag == "view")
    app.feed("down", 50, 50, now=0.0)
    assert seen == ["press"], seen
    assert node.pressed is True
    app.feed("up", 50, 50, now=0.10)
    assert seen == ["press", "release"], seen
    assert node.pressed is False
    app.unmount()


def test_hover_and_leave():
    seen = []

    class Root(V.Component):
        template = '<View w="200" h="120" @hover="h" @leave="l"><Text>X</Text></View>'
        def setup(self, props, ctx):
            return {"h": lambda e: seen.append("hover"), "l": lambda e: seen.append("leave")}

    app = app_for(Root)
    node = find(app.root, lambda n: n.tag == "view")
    app.feed("move", 20, 20, now=0.0)
    assert seen == ["hover"], seen
    assert node.hover is True
    app.feed("move", 500, 500, now=0.05)   # 移出组件
    assert seen == ["hover", "leave"], seen
    assert node.hover is False
    app.unmount()


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
