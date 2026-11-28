"""组件 ABI 治理回归测试：全局冲突、显式覆盖、capability、精确回查、来源标记。

    PYTHONPATH=python python3 -m pytest python/tests/test_fix_component.py -q
"""

from __future__ import annotations

import os
import sys

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor import component  # noqa: E402
from Vapor import ui as V  # noqa: E402


def _provider(name, comp_names, *, caps=None, replace=False, default="x"):
    """构造一个符合 ABI 的提供者工厂。"""
    classes = []
    for cn in comp_names:
        classes.append(type(cn, (V.Component,), {
            "template": f'<Text text="{cn}" />',
            "props": {"title": default},
        }))

    class P:
        ABI = 1

        def manifest(self):
            m = {
                "abi": 1,
                "name": name,
                "components": [
                    {"name": c.__name__, "props": {"title": default}}
                    for c in classes
                ],
            }
            if caps is not None:
                m["capabilities"] = caps
            if replace:
                m["replace"] = True
            return m

        def register(self, host):
            for c in classes:
                host.register(c)

    return P(), classes


def _cleanup(names):
    for n in names:
        V.COMPONENTS.pop(n, None)


def test_validate_provider_is_static_no_global_conflict():
    """validate_provider 只做静态校验，不因组件已在全局表而误判冲突。"""
    p, classes = _provider("vp-static", ["VPStaticComp"])
    try:
        V.register(classes[0])  # 先注册进全局表
        names = component.validate_provider(p)  # 再静态校验，不应报冲突
        assert "VPStaticComp" in names
    finally:
        _cleanup(["VPStaticComp"])


def test_register_provider_rejects_duplicate_name():
    """同名组件与全局注册表冲突时默认拒绝。"""
    p, classes = _provider("dup-prov", ["DupWidget"])
    V.register(classes[0])  # 内置/先注册占据该名字
    try:
        try:
            component.register_provider(p)
        except component.ProviderError as exc:
            assert "冲突" in str(exc) and "DupWidget" in str(exc)
        else:
            raise AssertionError("同名冲突应被拒绝")
    finally:
        _cleanup(["DupWidget"])


def test_register_provider_replace_allows_override():
    """显式 replace=True 时允许覆盖同名组件。"""
    p, classes = _provider("replace-prov", ["ReplaceWidget"], replace=True)
    try:
        names = component.register_provider(p)
        assert names == ["ReplaceWidget"]
        assert V.COMPONENTS["ReplaceWidget"] is classes[0]
    finally:
        _cleanup(["ReplaceWidget"])


def test_register_provider_capability_rejected():
    """provider 需要的能力不在宿主支持集时拒绝加载。"""
    p, classes = _provider("cap-prov", ["CapWidget"], caps=["lifecycle.v99"])
    try:
        try:
            component.register_provider(p)
        except component.ProviderError as exc:
            assert "能力" in str(exc) and "lifecycle.v99" in str(exc)
        else:
            raise AssertionError("不支持的能力应被拒绝")
    finally:
        _cleanup(["CapWidget"])


def test_register_provider_capability_ok():
    """provider 需要的能力在宿主支持集内时正常加载。"""
    p, classes = _provider("capok-prov", ["CapOkWidget"], caps=["lifecycle.v1"])
    try:
        names = component.register_provider(p)
        assert names == ["CapOkWidget"]
    finally:
        _cleanup(["CapOkWidget"])


def test_register_provider_declared_but_not_registered_rollback():
    """manifest 声明的组件未真正注册时报错并回滚，不残留半成品。"""
    class Lazy:
        def manifest(self):
            return {"abi": 1, "name": "lazy2", "components": [
                {"name": "LazyNeverRegistered"},
            ]}

        def register(self, host):
            pass

    try:
        component.register_provider(Lazy())
    except component.ProviderError as exc:
        assert "没有注册" in str(exc)
    else:
        raise AssertionError("声明未注册应报错")
    assert "LazyNeverRegistered" not in V.COMPONENTS


def test_props_consistency_detected():
    """manifest 声明的 props 默认值与实现不一致时报错。"""
    class Mismatch:
        def manifest(self):
            return {"abi": 1, "name": "mismatch", "components": [
                {"name": "MismatchWidget", "props": {"title": "declared-value"}},
            ]}

        def register(self, host):
            cls = type("MismatchWidget", (V.Component,), {
                "template": '<Text text="x" />',
                "props": {"title": "impl-value"},
            })
            host.register(cls)

    try:
        try:
            component.register_provider(Mismatch())
        except component.ProviderError as exc:
            assert "props" in str(exc) and "不一致" in str(exc)
        else:
            raise AssertionError("props 不一致应报错")
    finally:
        _cleanup(["MismatchWidget"])


def test_component_sources_records_provider():
    """注册后来源表记录组件来自哪个提供者。"""
    p, classes = _provider("source-prov", ["SourceWidget"])
    try:
        component.register_provider(p)
        sources = component.component_sources()
        assert sources.get("SourceWidget") == "source-prov"
    finally:
        _cleanup(["SourceWidget"])


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
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
