"""Vapor.component —— 组件 ABI：把 UI 组件做成可分发、可热插拔的扩展。

约定（ABI v1，Python 侧）
------------------------
一个「组件提供者」是一个模块（通常放在逻辑包的 lib/ 里），暴露：

    ABI = 1
    def manifest() -> dict:      # 组件清单：给宿主 / 应用市场看的自描述
        return {
            "abi": 1,
            "name": "my-ext",
            "version": "1.0.0",
            "components": [
                {"name": "MyCard", "props": {"title": ""}, "description": "卡片"},
            ],
        }
    def register(host) -> None:  # 把组件注册进宿主
        host.register(MyCard)

宿主提供 host.register(cls) / host.components / host.abi。加载器会：
  1. 校验 manifest（ABI 版本、组件名唯一、props 是 dict）
  2. 调 register(host)
  3. 回查清单里的组件是否真的注册成功

C 侧
----
C 扩展导出 `vapor_component_init(VaporApi*)`（见 include/vapor_component.h），
典型做法是先用 dladdr 找到自己所在目录，再 py_eval 一段注册脚本，
把同一目录下的 Python 组件包注册进来 —— 这样「组件」可以随 .so 一起分发，
不需要改 Rust、不需要重编 APK 的 Python 运行时。

生成模板::

    python -m Vapor.component MyCard                  # Python 组件
    python -m Vapor.component MyCard --kind c         # C 扩展骨架
"""

from __future__ import annotations

import importlib
import os
import sys

from . import ui

__all__ = [
    "COMPONENT_ABI_VERSION", "Host", "ProviderError",
    "validate_provider", "register_provider", "load_provider",
    "providers", "new_component", "TEMPLATES",
]

COMPONENT_ABI_VERSION = 1


class ProviderError(RuntimeError):
    """组件提供者不符合 ABI 约定。"""


class Host:
    """传给 provider.register(host) 的宿主句柄。"""

    __slots__ = ("abi",)

    def __init__(self, abi: int = COMPONENT_ABI_VERSION):
        self.abi = abi

    def register(self, component_cls):
        """注册一个 Vapor.ui.Component 子类，返回类本身。"""
        return ui.register(component_cls)

    @property
    def components(self) -> dict:
        return ui.COMPONENTS

    def __repr__(self):
        return f"Host(abi={self.abi}, components={len(self.components)})"


_PROVIDERS: list = []


def providers() -> list:
    return list(_PROVIDERS)


def _check_manifest(manifest) -> dict:
    if not isinstance(manifest, dict):
        raise ProviderError("manifest() 必须返回 dict")
    abi = manifest.get("abi", 1)
    if not isinstance(abi, int) or abi > COMPONENT_ABI_VERSION:
        raise ProviderError(
            f"组件 ABI 版本不兼容：提供者 {abi}，宿主 {COMPONENT_ABI_VERSION}"
        )
    name = manifest.get("name")
    if not name or not isinstance(name, str):
        raise ProviderError("manifest() 缺少 name")
    comps = manifest.get("components")
    if not isinstance(comps, list) or not comps:
        raise ProviderError("manifest() 的 components 必须是非空列表")
    seen = set()
    for item in comps:
        if not isinstance(item, dict) or not item.get("name"):
            raise ProviderError(f"组件条目非法: {item!r}")
        comp_name = item["name"]
        if not isinstance(comp_name, str):
            raise ProviderError(f"组件名必须是字符串: {comp_name!r}")
        if comp_name in seen:
            raise ProviderError(f"组件名重复: {comp_name}")
        seen.add(comp_name)
        props = item.get("props", {})
        if not isinstance(props, dict):
            raise ProviderError(f"组件 {comp_name} 的 props 必须是 dict")
    return manifest


def validate_provider(provider) -> list:
    """校验提供者是否符合 ABI，返回声明的组件名列表（不注册）。"""
    manifest_fn = getattr(provider, "manifest", None)
    register_fn = getattr(provider, "register", None)
    if not callable(manifest_fn):
        raise ProviderError("提供者缺少 manifest()")
    if not callable(register_fn):
        raise ProviderError("提供者缺少 register(host)")
    manifest = _check_manifest(manifest_fn())
    abi = getattr(provider, "ABI", manifest.get("abi", 1))
    if isinstance(abi, int) and abi > COMPONENT_ABI_VERSION:
        raise ProviderError(f"提供者 ABI={abi} 高于宿主 {COMPONENT_ABI_VERSION}")
    return [item["name"] for item in manifest["components"]]


def register_provider(provider, abi: int = COMPONENT_ABI_VERSION) -> list:
    """校验并注册一个组件提供者，返回注册的组件名列表。"""
    expected = validate_provider(provider)
    host = Host(abi)
    before = set(ui.COMPONENTS)
    provider.register(host)
    registered = set(ui.COMPONENTS) - before
    missing = [name for name in expected if name not in ui.COMPONENTS]
    if missing:
        raise ProviderError(
            "manifest 声明了但这些组件没有注册: " + ", ".join(missing)
        )
    _PROVIDERS.append(provider)
    return expected


def load_provider(module_name: str) -> list:
    """按模块名导入并注册（模块需要在 sys.path 上，通常由扩展机制注入 lib/）。"""
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ProviderError(f"无法导入组件模块 {module_name}: {exc}") from exc
    return register_provider(module)


# ---------------------------------------------------------------------------
# 模板
# ---------------------------------------------------------------------------

_PY_TEMPLATE = '''"""组件扩展：@NAME@（由 python -m Vapor.component @NAME@ 生成）。

放进逻辑包的 lib/ 目录，并在 abi.json 里登记：

    {"name": "@NAME@", "kind": "python", "path": "@MODULE@.py",
      "arch": "all", "enabled": true}

宿主加载后模板里就能直接用 <@NAME@ />。
"""

from __future__ import annotations

from Vapor import ui as V

ABI = 1


class @NAME@(V.Component):
    """组件实现：模板 + setup + 声明式 props。"""

    props = {"title": "", "accent": "#2D7FF9"}

    template = \'\'\'
      <View :bg="accent" radius="12" padding="14" gap="8">
        <Text :text="title" size="16" color="#FFFFFF" />
        <slot />
      </View>
    \'\'\'

    def setup(self, props, ctx):
        # 返回的字典进入模板作用域；Ref 会自动解包
        return {}


def manifest():
    return {
        "abi": ABI,
        "name": "@NAME@",
        "version": "1.0.0",
        "components": [
            {
                "name": "@NAME@",
                "props": dict(@NAME@.props),
                "description": "",
            },
        ],
    }


def register(host):
    host.register(@NAME@)
'''


_C_TEMPLATE = '''/*
 * 组件扩展（C）：@NAME@
 *
 * 由 python -m Vapor.component @NAME@ --kind c 生成。
 * 契约见 include/vapor_component.h：导出 vapor_component_init(VaporApi*)，
 * 典型实现是「找到自己所在目录 -> py_eval 注册同目录下的 Python 组件包」。
 *
 * 编译（arm64 示例）：
 *   $CC -shared -fPIC -Iinclude -o lib@MODULE@.so @MODULE@.c
 */

#include <dlfcn.h>
#include <libgen.h>
#include <stdio.h>
#include <string.h>

#include "vapor_abi.h"
#include "vapor_component.h"

static char g_lib_dir[4096];

static void resolve_self_dir(void) {
    Dl_info info;
    if (dladdr((void *)vapor_component_init, &info) == 0 || !info.dli_fname) {
        snprintf(g_lib_dir, sizeof(g_lib_dir), ".");
        return;
    }
    char buf[4096];
    snprintf(buf, sizeof(buf), "%s", info.dli_fname);
    snprintf(g_lib_dir, sizeof(g_lib_dir), "%s", dirname(buf));
}

int32_t vapor_component_init(VaporApi *api) {
    if (!api || !api->py_eval) {
        return VAPOR_ERR_ARG;
    }
    resolve_self_dir();

    char script[8192];
    int n = snprintf(
        script, sizeof(script),
        "import sys, importlib\\n"
        "sys.path.insert(0, %r)\\n"
        "from Vapor import component\\n"
        "mod = importlib.import_module(%r)\\n"
        "names = component.register_provider(mod)\\n"
        "print('[component_abi] registered', names)\\n",
        g_lib_dir, "@MODULE@");
    if (n <= 0 || n >= (int)sizeof(script)) {
        return VAPOR_ERR;
    }
    return api->py_eval(script);
}
'''


TEMPLATES = {"python": _PY_TEMPLATE, "c": _C_TEMPLATE}


def new_component(name: str, kind: str = "python", out_dir: str = "lib") -> str:
    """按模板生成一个组件扩展文件，返回写入的路径。"""
    if not name or not name.replace("_", "").isalnum():
        raise ValueError("组件名只能是字母/数字/下划线")
    if kind not in TEMPLATES:
        raise ValueError(f"kind 必须是 {sorted(TEMPLATES)}")
    cls_name = name[0].upper() + name[1:]
    module = name.replace("-", "_")
    text = TEMPLATES[kind].replace("@NAME@", cls_name).replace("@MODULE@", module)
    os.makedirs(out_dir, exist_ok=True)
    suffix = ".py" if kind == "python" else ".c"
    path = os.path.join(out_dir, module + suffix)
    if os.path.exists(path):
        raise FileExistsError(path)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m Vapor.component", description="生成组件扩展模板")
    ap.add_argument("name", help="组件名（如 MyCard）")
    ap.add_argument("--kind", choices=sorted(TEMPLATES), default="python")
    ap.add_argument("--out", default="lib", help="输出目录（默认 lib）")
    args = ap.parse_args(argv)
    path = new_component(args.name, args.kind, args.out)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(_main())