"""Vapor OS 内置应用与外壳验收测试（执行文档 4）。"""

from __future__ import annotations

import os
import re
from types import SimpleNamespace

import pytest

import Vapor.vos as vos
import Vapor.vos.apps as apps
import Vapor.vos.shell as shell_mod
from Vapor import core
from Vapor.theme import current_theme, seed_to_scheme, set_theme
from Vapor.ui import COMPONENTS as UI_COMPONENTS
from Vapor.ui.layout import layout
from Vapor.ui.renderer import Renderer
from Vapor.vos_base.cjk import type_style

_MD3_READY = False


def _ensure_md3():
    global _MD3_READY
    if not _MD3_READY:
        from Vapor.md3 import install

        install()
        _MD3_READY = True


def build(cls, **props):
    _ensure_md3()
    r = Renderer()
    node = cls.instantiate(r, props, {})
    return r, node, node.children[0]


def fire(node, event, ev=None):
    ev = SimpleNamespace(x=0, y=0) if ev is None else ev
    handlers = []
    for n in node.walk():
        for provider, _modifiers in n.events.get(event, []):
            handlers.append(provider())
    assert handlers, f"没有找到事件处理器: {event}"
    for handler in handlers:
        handler(ev)


def test_four_apps_manifest():
    names = [c.name for c in apps.COMPONENTS]
    assert names == ["MdSettingsApp", "MdClockApp", "MdGalleryApp", "MdCalculatorApp"]
    assert all(name.startswith("Md") for name in names)
    assert len(names) == len(set(names))
    for name in names:
        assert name not in UI_COMPONENTS

    data = apps.manifest()
    assert data["abi"] == 1
    assert data["name"] == "vapor.vos.apps"
    assert [c["name"] for c in data["components"]] == names


def test_settings_theme_follow():
    _r, _node, root = build(apps.MdSettingsApp)
    accent = root.props["_accent"]
    before = current_theme()
    try:
        assert accent.props["bg"] == before.role("primary")
        set_theme(seed_to_scheme(0xFF6750A4, dark=True))
        assert accent.props["bg"] == current_theme().role("primary")
        assert accent.props["bg"] != before.role("primary")
    finally:
        set_theme(before)


def test_clock_emph_chinese():
    _r, _node, root = build(apps.MdClockApp)
    style = root.props["_clock_style"]
    expected = type_style("displaySmall", emphasized=True, lang="zh")
    assert style["fontSize"] == expected["fontSize"]
    assert style["fontWeight"] == expected["fontWeight"]
    assert style["lineHeight"] == expected["lineHeight"]


def test_gallery_grid_dirty(monkeypatch):
    calls = []
    monkeypatch.setattr(apps, "queue_dirty", lambda x, y, w, h: calls.append((x, y, w, h)))

    _r, _node, root = build(apps.MdGalleryApp)
    layout(root, 0, 0, 360, 720)
    cells = root.props["_cells"]
    assert len(cells) == 9

    fire(cells[4], "click")
    assert calls
    box = cells[4].box
    for x, y, w, h in calls:
        assert x >= box.x - 1 and y >= box.y - 1
        assert x + w <= box.x + box.w + 1
        assert y + h <= box.y + box.h + 1


def test_calculator_buttons():
    seen = []
    _r, _node, root = build(apps.MdCalculatorApp, on_change=seen.append)
    fire(root.props["_buttons"]["1"], "click")
    assert root.props["_display"].text == "1"
    assert seen == ["1"]

    fire(root.props["_buttons"]["+"], "click")
    fire(root.props["_buttons"]["2"], "click")
    fire(root.props["_buttons"]["="], "click")
    assert root.props["_display"].text == "3"
    assert seen[-1] == "3"


def test_shell_install_cjk_once(monkeypatch):
    calls = []
    monkeypatch.setattr(shell_mod, "install_cjk", lambda prefer_latin=False: calls.append(1) or True)
    monkeypatch.setattr(shell_mod, "_cjk_ready", False, raising=False)

    class Page(vos.shell.Component):
        name = "ShellPageCjk"

        @staticmethod
        def build(r, props, slots):
            return [r.text("page")]

    for _ in range(2):
        r = Renderer()
        vos.shell.VaporShell.instantiate(r, {"routes": {"home": Page}}, {})
    assert len(calls) == 1


def test_shell_route_callback():
    class PageA(vos.shell.Component):
        name = "ShellPageA"

        @staticmethod
        def build(r, props, slots):
            return [r.text("A")]

    class PageB(vos.shell.Component):
        name = "ShellPageB"

        @staticmethod
        def build(r, props, slots):
            return [r.text("B")]

    seen = []
    routes = {"home": PageA, "b": PageB}
    r = Renderer()
    root = vos.shell.VaporShell.instantiate(
        r, {"routes": routes, "on_route_change": seen.append}, {}).children[0]

    def texts(node):
        return [n.text for n in node.walk() if n.text]

    assert "A" in texts(root.props["_content"])
    root.props["_state"]["navigate"]("b")
    assert seen == ["b"]
    assert "B" in texts(root.props["_content"])
    assert routes == {"home": PageA, "b": PageB}


def test_install_all_providers():
    names = vos.install()
    expected = sum(len(getattr(m, "COMPONENTS", ())) for m in vos._MODULES + [apps])
    assert len(names) == expected
    assert len(set(names)) == len(names)
    assert all(name.startswith("Md") for name in names)


def test_no_hardcoded_color():
    root = os.path.dirname(apps.__file__)
    pattern = re.compile(r"0x(?:[0-9A-Fa-f]{2})?6750A4|#[0-9A-Fa-f]{3,8}\b")
    for name in ("apps.py", "shell.py", "__init__.py"):
        with open(os.path.join(root, name), "r", encoding="utf-8") as f:
            source = f.read()
        assert not pattern.search(source), f"{name} 出现了硬编码主题色"
