"""Vapor OS Expressive 基础层验收测试（执行文档 1）。"""

from __future__ import annotations

import os
import re
from types import SimpleNamespace

import pytest

import Vapor.vos_base.cjk as cjk
from Vapor.theme import get_shape, get_type_style
from Vapor.theme.tokens import TYPE_SCALE, TYPE_SCALE_EMPHASIZED
from Vapor.ui.states import StateLayer
from Vapor.vos_base import MorphSpec, ShapeMorph, morph_radius


def test_morph_radius_endpoints():
    spec = MorphSpec("full", "large")
    assert get_shape("full", 40) == 20
    assert morph_radius(spec, 40, 0.0) == 20
    assert morph_radius(spec, 40, 1.0) == 16


def test_morph_overshoot_clamped():
    spec = MorphSpec("full", "large", speed="slow")
    for t in (1.2, 1.5, 2.0):
        radius = morph_radius(spec, 40, t)
        assert radius >= 0
        assert radius <= 40 / 2 * 1.2


def test_reduced_motion_snap(monkeypatch):
    monkeypatch.setenv("VAPOR_REDUCED_MOTION", "1")
    morph = ShapeMorph(MorphSpec("full", "large"))
    morph.press()
    assert morph.radius(40) == 16
    assert morph.active is False


def test_shape_morph_independent_of_state_layer():
    node = SimpleNamespace(extra={}, props={})
    layer = StateLayer(0xFFFFFFFF)
    layer.set("pressed", True)
    morph = ShapeMorph(MorphSpec("full", "large"))
    node.extra["state_layer"] = layer
    node.extra["shape_morph"] = morph

    morph.press(now=0.0)
    assert 0 <= morph.radius(40, now=0.0) <= 24
    assert layer.states == {"pressed"}


def test_type_style_cjk_height():
    baseline = get_type_style("bodyMedium", lang_height_factor=1.0)
    zh = cjk.type_style("bodyMedium", lang="zh")
    en = cjk.type_style("bodyMedium", lang="en")
    assert zh["lineHeight"] == round(baseline["lineHeight"] * cjk.CJK_HEIGHT_FACTOR)
    assert en["lineHeight"] == baseline["lineHeight"]


def test_type_style_emphasized():
    base = cjk.type_style("bodyMedium", lang="zh")
    emphasized = cjk.type_style("bodyMedium", emphasized=True, lang="zh")
    token = TYPE_SCALE_EMPHASIZED["bodyMediumEmphasized"]
    assert emphasized["fontSize"] == token["fontSize"]
    assert emphasized["fontWeight"] == token["fontWeight"]
    assert emphasized != base
    assert TYPE_SCALE["bodyMedium"] != token


def test_install_cjk_idempotent_no_font(monkeypatch):
    calls = []
    monkeypatch.setattr(cjk.font, "available", lambda: True)
    monkeypatch.setattr(cjk.font, "default_font", lambda: None)
    monkeypatch.setattr(cjk.font, "install", lambda f: calls.append(f) or True)
    monkeypatch.setattr(cjk, "_installed", False, raising=False)

    assert cjk.install_cjk() is False
    assert cjk.install_cjk() is False
    assert calls == []


def test_install_cjk_idempotent_success(monkeypatch):
    class _FakeStack:
        pass

    calls = []
    monkeypatch.setattr(cjk.font, "available", lambda: True)
    monkeypatch.setattr(cjk.font, "default_font", lambda: _FakeStack())
    monkeypatch.setattr(cjk.font, "install", lambda f: calls.append(f) or True)
    monkeypatch.setattr(cjk, "_installed", False, raising=False)

    assert cjk.install_cjk() is True
    assert cjk.install_cjk() is True
    assert len(calls) == 1
    monkeypatch.setattr(cjk, "_installed", False, raising=False)


def test_no_hardcoded_color():
    root = os.path.dirname(cjk.__file__)
    pattern = re.compile(r"0x(?:[0-9A-Fa-f]{2})?6750A4|#[0-9A-Fa-f]{3,8}\b")
    for name in os.listdir(root):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(root, name), "r", encoding="utf-8") as f:
            source = f.read()
        assert not pattern.search(source), f"{name} 出现了硬编码主题色"


@pytest.mark.parametrize("name", ["bodyMedium", "labelLarge", "headlineSmall"])
def test_type_style_returns_copy(name):
    first = cjk.type_style(name)
    first["fontSize"] = -1
    assert cjk.type_style(name)["fontSize"] > 0
