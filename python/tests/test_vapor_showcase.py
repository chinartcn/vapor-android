"""Showcase 演示的交互测试：挂载、评分组件、页面栈、对话框。

    PYTHONPATH=python python3 python/tests/test_vapor_showcase.py
"""

from __future__ import annotations

import os
import sys

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "protocol", "showcase", "lib"))

import showcase_app  # noqa: E402
from Vapor import ui as V  # noqa: E402


def _app():
    return showcase_app.create_app(width=360, height=720, use_native=False)


def find(node, pred):
    for n in node.walk():
        if pred(n):
            return n
    return None


def by_text(node, needle):
    return find(node, lambda n: n.tag == "#text" and needle in (n.text or ""))


def click(app, node, now=0.0):
    x, y = int(node.box.x + node.box.w / 2), int(node.box.y + node.box.h / 2)
    app.feed("down", x, y, now=now)
    app.feed("up", x, y, now=now + 0.05)
    return x, y


def texts(app):
    return [n.text for n in app.root.walk() if n.tag == "#text"]


def test_mount_renders_home():
    app = _app()
    joined = " ".join(t for t in texts(app) if t)
    assert "Vapor Showcase" in joined
    assert "自定义组件 RatingStars" in joined
    app.unmount()


def test_rating_stars_click_updates_value():
    app = _app()
    row = find(app.root, lambda n: n.props.get("name") == "rating-stars")
    assert row is not None
    stars = V._flow(row)
    assert len(stars) == 5, len(stars)
    click(app, stars[4], now=0.0)
    app.tick()
    assert any("已评 5 星" in (t or "") for t in texts(app)), texts(app)
    app.unmount()


def test_open_detail_pushes_page():
    app = _app()
    item = by_text(app.root, "打开详情页")
    assert item is not None
    click(app, item, now=0.0)
    app.tick()
    nav = V.Navigator.current
    assert nav is not None and nav.depth == 2, nav.depth if nav else None
    assert any("申请相机权限" in (t or "") for t in texts(app))
    nav.pop()
    app.tick()
    assert nav.depth == 1
    app.unmount()


def test_dialog_opens():
    app = _app()
    item = by_text(app.root, "弹出对话框")
    assert item is not None
    click(app, item, now=0.0)
    app.tick()
    dialog = find(app.root, lambda n: n.props.get("paint") and n.box.w >= 340 and n.box.h >= 500)
    assert dialog is not None, "对话框遮罩应当铺满"
    assert dialog.visible is True
    app.unmount()


def test_chip_tab_switch_renders():
    app = _app()
    chip = by_text(app.root, "关注")
    assert chip is not None
    click(app, chip, now=0.0)
    app.tick()
    # 切换后仍能正常渲染（不崩），且文本还在
    assert any("关注" in (t or "") for t in texts(app))
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
