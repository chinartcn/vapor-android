"""执行文档 4：应用接入、示例数据与总装验收测试。"""

from __future__ import annotations

import os
import re
import sys

import pytest

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Vapor.ui as V  # noqa: E402
from Vapor.theme import current_theme, set_theme  # noqa: E402
from Vapor.ui import Component  # noqa: E402
from Vapor.vos.apps import MdSettingsApp  # noqa: E402
from examples.vapor_os import app as boot  # noqa: E402
from examples.vapor_os import data  # noqa: E402
from examples.vapor_os.screens.home import HomeScreen  # noqa: E402

_EXPECTED = ("home", "settings", "clock", "gallery",
             "calculator", "shade", "lock", "recents")


@pytest.fixture(autouse=True)
def _restore_globals():
    """build_app 会装 provider / 切主题；用后还原，避免污染其他用例。"""
    snapshot = dict(V.COMPONENTS)
    theme = current_theme()
    ready = boot._READY
    try:
        yield
    finally:
        V.COMPONENTS.clear()
        V.COMPONENTS.update(snapshot)
        boot._READY = ready
        set_theme(theme)


def test_build_routes_complete():
    routes = data.build_routes()
    assert set(routes) == set(_EXPECTED)
    for name, entry in routes.items():
        assert isinstance(entry, type), name
        assert issubclass(entry, Component), name


def test_full_demo_smoke():
    app = boot.build_app(width=360, height=800, routes=data.build_routes())
    try:
        for _ in range(3):
            app.tick()
    finally:
        app.unmount()


def test_all_scenes_headless():
    routes = data.build_routes()
    for scene in _EXPECTED:
        # build_app 没有 initial_route 参数；传单场景路由表即可选中该场景。
        app = boot.build_app(width=360, height=800, routes={scene: routes[scene]})
        try:
            for _ in range(2):
                app.tick()
        finally:
            app.unmount()


def test_sample_data_shapes():
    apps = data.sample_apps()
    assert apps and all({"label", "route"} <= set(item) for item in apps)

    dock = data.sample_dock()
    assert dock and all({"label", "route"} <= set(item) for item in dock)

    notes = data.sample_notifications()
    assert 3 <= len(notes) <= 5
    assert all({"title", "body", "timestamp"} <= set(item) for item in notes)

    tiles = data.sample_qs_tiles()
    assert 6 <= len(tiles) <= 8
    assert all({"label", "on"} <= set(item) for item in tiles)
    assert all(isinstance(item["on"], bool) for item in tiles)

    tasks = data.sample_tasks()
    assert tasks and all({"id", "title", "preview"} <= set(item) for item in tasks)


def test_no_hardcoded_color():
    root = os.path.dirname(os.path.abspath(data.__file__))
    for name in ("data.py", "run_showcase.py"):
        with open(os.path.join(root, name), "r", encoding="utf-8") as fh:
            source = fh.read()
        assert not re.search(r"#[0-9A-Fa-f]{6}\b", source), name
        assert not re.search(r"0x[0-9A-Fa-f]{8}", source), name


def test_readonly_imports():
    routes = data.build_routes()
    # 总装复用原组件类（路由包装层透明代理），不做复制/替换。
    assert routes["settings"].wrapped is MdSettingsApp
    assert issubclass(data.HomeRoute, HomeScreen)

    # 挂载主屏后不得反写文档 2 的类级 props。
    before = dict(HomeScreen.props)
    app = boot.build_app(width=360, height=800, routes=routes)
    try:
        app.tick()
    finally:
        app.unmount()
    assert HomeScreen.props == before
    assert HomeScreen.props["apps"] == []
