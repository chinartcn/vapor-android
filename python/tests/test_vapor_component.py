"""组件 ABI（Vapor.component）测试：模板生成、manifest 校验、注册与渲染。

    PYTHONPATH=python python3 python/tests/test_vapor_component.py
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys

try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor import component  # noqa: E402
from Vapor import ui as V  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "build", "component_test")


def _fresh_work():
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    return WORK


def _load_file(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generate_python_template():
    _fresh_work()
    path = component.new_component("TplCard", out_dir=WORK)
    assert os.path.isfile(path)
    mod = _load_file(path, "tpl_card_mod")
    assert callable(mod.manifest) and callable(mod.register)
    names = component.register_provider(mod)
    assert names == ["TplCard"]
    assert "TplCard" in V.COMPONENTS


def test_generate_c_template():
    _fresh_work()
    path = component.new_component("TplNative", kind="c", out_dir=WORK)
    text = open(path, encoding="utf-8").read()
    assert "vapor_component_init" in text
    assert "vapor_component.h" in text
    assert "py_eval" in text


def test_manifest_validation_rejects_bad_abi():
    class Bad:
        ABI = 99
        def manifest(self):
            return {"abi": 99, "name": "bad", "components": [{"name": "X"}]}
        def register(self, host):
            pass

    try:
        component.register_provider(Bad())
    except component.ProviderError as exc:
        assert "ABI" in str(exc)
    else:
        raise AssertionError("应当拒绝 ABI 过高的提供者")


def test_manifest_validation_rejects_missing_register():
    class NoRegister:
        def manifest(self):
            return {"abi": 1, "name": "x", "components": [{"name": "X"}]}

    try:
        component.validate_provider(NoRegister())
    except component.ProviderError as exc:
        assert "register" in str(exc)
    else:
        raise AssertionError("缺少 register 应当报错")


def test_manifest_validation_rejects_duplicates():
    class Dup:
        def manifest(self):
            return {"abi": 1, "name": "d", "components": [
                {"name": "Same"}, {"name": "Same"},
            ]}
        def register(self, host):
            pass

    try:
        component.validate_provider(Dup())
    except component.ProviderError as exc:
        assert "重复" in str(exc)
    else:
        raise AssertionError("重复组件名应当报错")


def test_declared_but_not_registered():
    class Lazy:
        def manifest(self):
            return {"abi": 1, "name": "lazy", "components": [
                {"name": "NeverRegistered"},
            ]}
        def register(self, host):
            pass

    try:
        component.register_provider(Lazy())
    except component.ProviderError as exc:
        assert "没有注册" in str(exc)
    else:
        raise AssertionError("声明未注册应当报错")


def test_example_statcard_renders():
    path = os.path.join(ROOT, "..", "vapor-android", "protocol", "example", "lib",
                        "hello_component.py")
    path = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "protocol",
        "example", "lib", "hello_component.py"))
    mod = _load_file(path, "hello_component_mod")
    names = component.register_provider(mod)
    assert "StatCard" in names

    class Root(V.Component):
        template = '<View w="240" h="160"><StatCard title="下载" value="1.2k" /></View>'
        components = {"StatCard": V.COMPONENTS["StatCard"]}

    app = V.App(Root, surface=V.Surface(240, 160, use_native=False)).mount()
    texts = [n.text for n in app.root.walk() if n.tag == "#text"]
    assert "下载" in texts and "1.2k" in texts, texts
    app.unmount()


def test_providers_recorded():
    assert isinstance(component.providers(), list)


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
