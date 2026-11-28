"""MD3 Design Token 静态数据层验收（文档 1）。"""

from __future__ import annotations

import os
import sys

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor.theme import (  # noqa: E402
    ADD_ON_ROLES,
    ROLE_GROUPS,
    ROLE_NAMES,
    SHAPE_COMPONENT_DEFAULTS,
    SHAPES,
    SPACING,
    TYPE_SCALE,
    TYPE_SCALE_COMPOSE,
    TYPE_SCALE_EMPHASIZED,
    baseline_dark,
    baseline_light,
    get_role_names,
    get_shape,
    get_spacing,
    get_type_style,
)


def test_role_count():
    assert len(ROLE_NAMES) == 26
    assert len(set(ROLE_NAMES)) == 26


def test_role_groups():
    assert sum(len(v) for v in ROLE_GROUPS.values()) == 26
    assert set(ROLE_GROUPS) == {"primary", "secondary", "tertiary", "error", "surface", "outline"}
    assert len(ROLE_GROUPS["surface"]) == 8
    assert get_role_names() == ROLE_NAMES
    assert get_role_names("primary") == ROLE_GROUPS["primary"]
    assert get_role_names("outline") == ("outline", "outlineVariant")


def test_no_shadow_role():
    assert "shadow" not in ROLE_NAMES
    assert "scrim" not in ROLE_NAMES
    assert "scrim" in ADD_ON_ROLES["functional"]
    assert "background" not in ROLE_NAMES
    assert "onBackground" not in ROLE_NAMES


def test_add_on_roles():
    assert len(ADD_ON_ROLES["fixed_accents"]) == 12
    assert ADD_ON_ROLES["surface"] == ("surfaceDim", "surfaceBright")
    assert ADD_ON_ROLES["inverse"] == (
        "inverseSurface", "inverseOnSurface", "inversePrimary",
    )


def test_type_scale_30():
    assert len(TYPE_SCALE) == 15
    assert len(TYPE_SCALE_EMPHASIZED) == 15


def test_type_scale_values():
    assert TYPE_SCALE["displayLarge"]["fontSize"] == 57
    assert TYPE_SCALE["displayLarge"]["letterSpacing"] == -0.25
    assert TYPE_SCALE["bodyMedium"]["lineHeight"] == 20
    assert TYPE_SCALE["labelSmall"]["fontSize"] == 11
    assert TYPE_SCALE["titleMedium"]["fontWeight"] == 500


def test_compose_tracking_kept_separately():
    # 官网 0.25 / Compose 0.2 两套并存，不能互相覆盖。
    assert TYPE_SCALE["bodyMedium"]["letterSpacing"] == 0.25
    assert TYPE_SCALE_COMPOSE["bodyMedium"]["letterSpacing"] == 0.2
    assert TYPE_SCALE["titleMedium"]["letterSpacing"] == 0.15
    assert TYPE_SCALE_COMPOSE["titleMedium"]["letterSpacing"] == 0.2


def test_emphasized_no_size_change():
    for name, style in TYPE_SCALE.items():
        emph = TYPE_SCALE_EMPHASIZED[name + "Emphasized"]
        assert emph["fontSize"] == style["fontSize"]
        assert emph["lineHeight"] == style["lineHeight"]
        assert emph["fontWeight"] > style["fontWeight"]


def test_get_type_style():
    style = get_type_style("bodyMedium", lang_height_factor=1.0)
    assert style["fontSize"] == 14
    assert style["lineHeight"] == 20
    style["fontSize"] = 99                     # 返回副本，不污染常量
    assert TYPE_SCALE["bodyMedium"]["fontSize"] == 14
    emph = get_type_style("bodyMedium", emphasized=True, lang_height_factor=1.0)
    assert emph["fontWeight"] == 500


def test_cjk_line_height():
    assert get_type_style("bodyMedium", lang_height_factor=1.07)["lineHeight"] == round(20 * 1.07)
    # 中文默认系数 1.07
    assert get_type_style("bodyMedium")["lineHeight"] == round(20 * 1.07)


def test_shape_ten():
    assert len(SHAPES) == 10
    assert SHAPES["none"] == 0
    assert SHAPES["small"] == 8
    assert SHAPES["extraExtraLarge"] == 48
    assert SHAPES["full"] == "shortest-side / 2"


def test_shape_full():
    assert get_shape("full", 40) == 20
    assert get_shape("full", 33) == 16.5
    assert get_shape("small") == 8
    try:
        get_shape("full")
    except ValueError:
        pass
    else:
        raise AssertionError("full 缺少 shortest_side 应报错")


def test_shape_component_defaults():
    assert SHAPE_COMPONENT_DEFAULTS["button"] == "full"
    assert SHAPE_COMPONENT_DEFAULTS["chip"] == 8
    assert SHAPE_COMPONENT_DEFAULTS["dialog"] == 28
    assert SHAPE_COMPONENT_DEFAULTS["card"] == 12
    assert SHAPE_COMPONENT_DEFAULTS["button_pressed"] == [8, 8, 12, 16, 16]


def test_spacing():
    assert SPACING == {"xs": 4, "s": 8, "m": 12, "l": 16, "xl": 24, "xxl": 32}
    assert get_spacing("m") == 12


def test_schemes_complete():
    for scheme in (baseline_light(), baseline_dark()):
        assert set(ROLE_NAMES).issubset(scheme)
        assert len(scheme) == 26
        assert all(isinstance(v, int) and v >= 0 for v in scheme.values())


def test_schemes_light_dark_differ():
    light, dark = baseline_light(), baseline_dark()
    assert light["surface"] != dark["surface"]
    assert light["onSurface"] != dark["onSurface"]
    # error 是静态色，但两套都要有且按明暗适配。
    assert light["error"] != dark["error"]
