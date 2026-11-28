"""MD3 motion 行为验收（文档 3）。"""

from __future__ import annotations

import math
import os
import sys

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor.ui.motion import (  # noqa: E402
    DURATION,
    EASING,
    SPRING,
    cubic_bezier,
    reduced_motion,
    spring,
)

_EXPECTED_KEYS = {
    "fastSpatial", "defaultSpatial", "slowSpatial",
    "fastEffects", "defaultEffects", "slowEffects",
}


def test_spring_12():
    assert set(SPRING) == {"standard", "expressive"}
    for scheme in SPRING.values():
        assert set(scheme) == _EXPECTED_KEYS
    assert SPRING["standard"]["fastSpatial"]["dampingRatio"] == 0.9
    assert SPRING["standard"]["fastSpatial"]["stiffness"] == 1400
    assert SPRING["expressive"]["fastSpatial"]["dampingRatio"] == 0.6
    assert SPRING["expressive"]["slowSpatial"]["stiffness"] == 200


def test_effects_no_overshoot():
    for scheme in ("standard", "expressive"):
        for speed in ("fast", "default", "slow"):
            assert SPRING[scheme][speed + "Effects"]["dampingRatio"] == 1.0


def test_effects_schemes_identical():
    for speed in ("fast", "default", "slow"):
        assert SPRING["standard"][speed + "Effects"] == SPRING["expressive"][speed + "Effects"]


def test_spatial_may_overshoot():
    f = spring("expressive", "default", "spatial")
    peak = max(f(i / 100) for i in range(101))
    assert peak > 1.0
    assert peak < 1.1


def test_effects_monotonic_no_overshoot():
    for scheme in ("standard", "expressive"):
        for speed in ("fast", "default", "slow"):
            f = spring(scheme, speed, "effects")
            values = [f(i / 200) for i in range(201)]
            assert abs(values[0]) < 1e-9
            assert abs(values[-1] - 1.0) < 1e-9
            assert all(b >= a - 1e-9 for a, b in zip(values, values[1:]))
            assert max(values) <= 1.0 + 1e-9


def test_spring_unknown_token():
    for args in (("bogus", "fast", "spatial"), ("standard", "bogus", "spatial"),
                 ("standard", "fast", "bogus")):
        try:
            spring(*args)
        except KeyError:
            pass
        else:
            raise AssertionError(f"应报错: {args}")


def test_easing_endpoints():
    assert set(EASING) >= {
        "standard", "standardDecelerate", "standardAccelerate",
        "emphasizedDecelerate", "emphasizedAccelerate", "emphasized",
    }
    for name in EASING:
        f = cubic_bezier(*EASING[name])
        assert abs(f(0)) < 1e-6
        assert abs(f(1) - 1) < 1e-6
        # 0..1 之间应单调不减（这几条官方曲线均满足）。
        values = [f(i / 50) for i in range(51)]
        assert all(b >= a - 1e-6 for a, b in zip(values, values[1:])), name


def test_cubic_bezier_clamps():
    f = cubic_bezier(0.2, 0.0, 0.0, 1.0)
    assert f(-1.0) == 0.0
    assert f(2.0) == 1.0
    assert 0.0 <= f(0.5) <= 1.0


def test_duration_16():
    assert len(DURATION) == 16
    assert DURATION["short1"] == 50
    assert DURATION["short4"] == 200
    assert DURATION["medium1"] == 250
    assert DURATION["long4"] == 600
    assert DURATION["extraLong4"] == 1000
    assert all(isinstance(v, int) for v in DURATION.values())


def test_reduced_motion_default(monkeypatch):
    monkeypatch.delenv("VAPOR_REDUCED_MOTION", raising=False)
    assert reduced_motion() is False
    monkeypatch.setenv("VAPOR_REDUCED_MOTION", "1")
    assert reduced_motion() is True
