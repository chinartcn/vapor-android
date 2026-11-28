"""内建预设组件（Vapor.presets）测试：渲染、按压反馈、对话框、分段控件。

    PYTHONPATH=python python3 python/tests/test_vapor_presets.py
"""

from __future__ import annotations

import os
import sys

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor import component  # noqa: E402
from Vapor import presets  # noqa: E402
from Vapor import ui as V  # noqa: E402


def app_for(root, w=320, h=480):
    app = V.App(root, surface=V.Surface(w, h, use_native=False))
    app.mount()
    return app


def find(node, pred):
    for n in node.walk():
        if pred(n):
            return n
    return None


def pixel(app, x, y):
    i = (y * app.surface.width + x) * 4
    buf = app.surface.buffer
    return (buf[i], buf[i + 1], buf[i + 2], buf[i + 3])


def test_all_presets_registered():
    names = [c.name for c in presets.PRESETS]
    assert len(names) == 12, names
    for name in names:
        assert name in V.COMPONENTS, name


def test_card_renders_rounded_bg():
    class Root(V.Component):
        template = '<View w="200" h="120"><Card w="200" h="120" bg="#FF0000" :shadow="False" /></View>'

    app = app_for(Root)
    r, g, b, _ = pixel(app, 100, 60)
    assert (r, g, b) == (255, 0, 0), (r, g, b)
    # 圆角处保留背景色
    r2, g2, b2, _ = pixel(app, 0, 0)
    assert (r2, g2, b2) != (255, 0, 0), (r2, g2, b2)
    app.unmount()


def test_fab_is_circular():
    class Root(V.Component):
        template = '<View w="200" h="200"><Fab text="+" :size="80" :w="80" :h="80" bg="#00FF00" /></View>'

    app = app_for(Root)
    r, g, b, _ = pixel(app, 40, 40)
    assert (r, g, b) == (0, 255, 0), (r, g, b)
    r2, g2, b2, _ = pixel(app, 1, 1)
    assert (r2, g2, b2) != (0, 255, 0), (r2, g2, b2)
    app.unmount()


def test_chip_selected_color():
    class Root(V.Component):
        template = '<View w="200" h="80"><Chip text="ON" w="120" h="40" :selected="True" accent="#0000FF" /></View>'

    app = app_for(Root)
    r, g, b, _ = pixel(app, 30, 20)
    assert (r, g, b) == (0, 0, 255), (r, g, b)
    app.unmount()


def test_progress_ring_draws_arc():
    class Root(V.Component):
        template = '<View w="120" h="120"><ProgressRing :value="0.5" :size="100" :w="100" :h="100" color="#00FF00" /></View>'

    app = app_for(Root)
    # 圆环顶部（12 点方向，圆心 50,50，半径约 47）
    r, g, b, _ = pixel(app, 50, 4)
    assert (r, g, b) == (0, 255, 0), (r, g, b)
    app.unmount()


def test_press_feedback_and_callback():
    seen = []

    class Root(V.Component):
        template = '<View w="200" h="120"><Card w="200" h="120" :shadow="False" :on_press="pressed" /></View>'
        def setup(self, props, ctx):
            return {"pressed": lambda ev: seen.append(1)}

    app = app_for(Root)
    card = find(app.root, lambda n: n.tag == "view" and "paint" in n.props)
    assert card is not None
    app.feed("down", 100, 60, now=0.0)
    assert card.pressed is True
    assert seen == [1], seen
    app.feed("up", 100, 60, now=0.1)
    assert card.pressed is False
    app.unmount()


def test_dialog_visibility():
    holder = {}

    class Root(V.Component):
        template = '<View w="300" h="300"><Dialog :visible="show" title="T" /></View>'
        def setup(self, props, ctx):
            holder["show"] = V.ref(False)
            return {"show": holder["show"]}

    app = app_for(Root, 300, 300)
    r, g, b, _ = pixel(app, 150, 150)
    before = (r, g, b)
    holder["show"].value = True
    app.tick()
    r2, g2, b2, _ = pixel(app, 5, 5)
    assert (r2, g2, b2) != before, ((r, g, b), (r2, g2, b2))
    app.unmount()


def test_segmented_control_changes_value():
    values = []

    class Root(V.Component):
        template = '<View w="300" h="80"><SegmentedControl :items="items" :value="0" w="300" :on_change="changed" /></View>'
        def setup(self, props, ctx):
            return {"items": ["A", "B", "C"], "changed": lambda v, i: values.append((v, i))}

    app = app_for(Root, 300, 80)
    app.feed("down", 150, 16, now=0.0)
    app.feed("up", 150, 16, now=0.05)
    assert values and values[-1][1] == 1, values
    app.unmount()


def test_preset_manifest_is_valid_component_abi():
    names = component.validate_provider(presets)
    assert "Card" in names and "Dialog" in names
    assert set(names) == {c.name for c in presets.PRESETS}


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
