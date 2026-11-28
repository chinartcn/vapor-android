"""MD3 Theme 运行时与动态取色验收测试（执行文档 2）。"""

from __future__ import annotations

import pytest

from Vapor import core
from Vapor.theme import (
    ContrastLevel,
    ROLE_NAMES,
    Theme,
    argb_from_hct,
    current_theme,
    hct_from_argb,
    seed_to_scheme,
    set_theme,
    use_theme,
)
from Vapor.theme.runtime import _FALLBACK_ROLES


def test_fallback_roles_match_authoritative():
    """fallback 必须与文档 1 的 26 角色逐字一致（B 类回归）。"""
    assert _FALLBACK_ROLES == ROLE_NAMES
    assert "surfaceVariant" not in ROLE_NAMES
    assert "surfaceContainerHighest" in ROLE_NAMES


def test_scheme_has_no_surface_variant():
    for dark in (False, True):
        scheme = seed_to_scheme(0xFF6750A4, dark=dark)
        assert "surfaceVariant" not in scheme.colors
        assert "surfaceContainerHighest" in scheme.colors


def test_role_roundtrip():
    t = Theme({n: 0xFF000000 for n in ROLE_NAMES})
    assert t.role("primary") == 0xFF000000
    with pytest.raises(KeyError):
        t.role("not_a_role")


def test_with_opacity():
    t = Theme({"primary": 0xFFFFFFFF})
    assert t.with_opacity("primary", 0.5) == 0x7FFFFFFF
    assert t.with_opacity("primary", 1.0) == 0xFFFFFFFF


def test_reactive_switch():
    base = seed_to_scheme(0xFF6750A4)
    hits = []
    core.effect(lambda: hits.append(current_theme().role("primary")))
    changed = Theme({**base.colors, "primary": 0xFFFF0000})
    set_theme(changed)
    core.flush_jobs()
    assert hits[-1] == 0xFFFF0000


def test_use_theme_restore():
    before = current_theme()
    tmp = Theme({n: 0xFF123456 for n in ROLE_NAMES}, name="tmp")
    with use_theme(tmp):
        assert current_theme().name == "tmp"
        assert current_theme().role("primary") == 0xFF123456
    assert current_theme() is before


def test_seed_to_scheme_complete():
    for dark in (False, True):
        for contrast in ("standard", "medium", "high"):
            t = seed_to_scheme(0xFF6750A4, dark=dark, contrast=contrast)
            assert set(ROLE_NAMES).issubset(t.colors)
            assert t.contrast == contrast
            assert len(t.colors) >= len(ROLE_NAMES)


def test_contrast_enum():
    t = seed_to_scheme(0xFF6750A4, contrast=ContrastLevel.HIGH)
    assert t.contrast == "high"


def test_hct_roundtrip():
    for argb in (0xFF6750A4, 0xFF000000, 0xFFFFFFFF, 0xFF00FF00):
        h, c, tone = hct_from_argb(argb)
        assert argb_from_hct(h, c, tone) == argb


def test_error_is_static():
    a = seed_to_scheme(0xFFFF0000)
    b = seed_to_scheme(0xFF0000FF)
    assert a.role("error") == b.role("error")
    assert a.role("errorContainer") == b.role("errorContainer")


def test_error_adapts_to_dark():
    light = seed_to_scheme(0xFF6750A4, dark=False)
    dark = seed_to_scheme(0xFF6750A4, dark=True)
    assert light.role("error") != dark.role("error")
