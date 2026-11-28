"""执行文档 1：Vapor OS 骨架与启动入口的冒烟测试。"""

from __future__ import annotations

import os
import sys

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Vapor.ui as V  # noqa: E402
import pytest  # noqa: E402
from Vapor.theme import baseline_light, current_theme, set_theme  # noqa: E402
from Vapor.ui.app import App  # noqa: E402

from examples.vapor_os import app as boot  # noqa: E402
from examples.vapor_os import main as cli  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_globals():
    """build_app 会把 39 个 provider 装进全局注册表；用后还原，避免污染其他用例。"""
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


def test_build_app_returns_mounted():
    app = boot.build_app(width=360, height=800)
    assert isinstance(app, App)
    assert app.root is not None
    assert app.surface.width == 360
    assert app.surface.height == 800


def test_providers_installed():
    boot.build_app(width=360, height=800)
    for name in ("MdButton", "MdStatusBar", "MdAppGrid", "MdSettingsApp"):
        assert name in V.COMPONENTS, name
    assert len(V.COMPONENTS) >= 39


def test_theme_seed_dark():
    boot.build_app(width=360, height=800, seed="0xFF6750A4", dark=True)
    assert current_theme().role("primary") != baseline_light()["primary"]


def test_dark_env(monkeypatch):
    boot.build_app(width=360, height=800, dark=False)
    light_primary = current_theme().role("primary")

    monkeypatch.setenv("VAPOR_OS_DARK", "1")
    boot.build_app(width=360, height=800)
    assert current_theme().role("primary") != light_primary


def test_no_font_graceful(monkeypatch):
    monkeypatch.setattr(boot, "_install_cjk", lambda: False)
    app = boot.build_app(width=360, height=800)
    assert app.root is not None


def test_no_routes_graceful():
    # 文档 4 未落地时 routes 为空，Shell 显示提示但不崩。
    app = boot.build_app(width=360, height=800, routes=None)
    assert app.root is not None


def test_headless_frames(monkeypatch):
    monkeypatch.setenv("VAPOR_DEMO_FRAMES", "2")
    assert cli.main() == 0


def test_no_lib_modified():
    vos_dir = os.path.join(os.path.dirname(Vapor.__file__), "vos")
    for fname in os.listdir(vos_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(vos_dir, fname), "r", encoding="utf-8") as fh:
            source = fh.read()
        assert "vapor_os" not in source, fname
        assert "build_app" not in source, fname
