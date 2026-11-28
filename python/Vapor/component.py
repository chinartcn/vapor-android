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
    "providers", "new_component", "TEMPLATES", "component_sources",
]

COMPONENT_ABI_VERSION = 1

# 宿主默认支持的能力集。manifest 未声明 capabilities 的老提供者完全兼容。
DEFAULT_HOST_CAPABILITIES = frozenset({"lifecycle.v1", "slot.default"})

# 组件名 -> 来源标记。独立记录，不写入 COMPONENTS，保持其「名字 -> 类」的值语义。
_COMPONENT_SOURCES: dict = {}


def _warn(msg):
    print(f"[Vapor.component] {msg}", file=sys.stderr)


def _source_of(name):
    return _COMPONENT_SOURCES.get(name, "Vapor.ui（内置或早期注册的组件）")


def component_sources() -> dict:
    """返回组件名 -> 来源的快照，便于诊断同名冲突。"""
    return dict(_COMPONENT_SOURCES)


def _mark_builtin_sources():
    for _name in tuple(getattr(ui, "COMPONENTS", {})):
        _COMPONENT_SOURCES.setdefault(_name, "Vapor.ui（内置）")


_mark_builtin_sources()


class ProviderError(RuntimeError):
    """组件提供者不符合 ABI 约定。"""


class Host:
    """传给 provider.register(host) 的宿主句柄。"""

    __slots__ = ("abi", "capabilities", "_touched", "_replace_names")

    def __init__(self, abi: int = COMPONENT_ABI_VERSION, capabilities=None,
                 replace_names=()):
        self.abi = abi
        self.capabilities = frozenset(
            DEFAULT_HOST_CAPABILITIES if capabilities is None else capabilities
        )
        # 本次 register 实际触碰过的名字，供 register_provider 精确回查。
        self._touched = set()
        # 已通过宿主预检、允许覆盖的名字；provider 内部重复注册时走 replace 语义。
        self._replace_names = frozenset(replace_names or ())

    def register(self, component_cls, replace=False):
        """注册一个 Vapor.ui.Component 子类，返回类本身。

        默认沿用 ui.register 的冲突策略；只有经 register_provider 预检放行的
        名字才会自动以 replace=True 覆盖，避免 provider 内部重复注册误报。
        """
        name = getattr(component_cls, "name", "") or component_cls.__name__
        if name in self._replace_names:
            replace = True
        result = ui.register(component_cls, replace=replace)
        self._touched.add(name)
        return result

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
    caps = manifest.get("capabilities")
    if caps is not None:
        if not isinstance(caps, (list, tuple)) or any(
            not isinstance(c, str) or not c for c in caps
        ):
            raise ProviderError("manifest() 的 capabilities 必须是能力名字符串列表")
    return manifest


def _validate_provider_shape(provider):
    """仅做 ABI 静态校验，返回 manifest（replace/能力策略由调用方决定）。"""
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
    return manifest


def _allow_replace(manifest, item, replace=False):
    return bool(
        replace or manifest.get("replace", False) or item.get("replace", False)
    )


def _check_global_conflicts(manifest, replace=False):
    """清单组件名与全局注册表（内置 + 已注册扩展）比对，冲突拒绝并列出来源。"""
    for item in manifest["components"]:
        name = item["name"]
        if name in ui.COMPONENTS and not _allow_replace(manifest, item, replace):
            raise ProviderError(
                f"组件名冲突: {name} 已由 {_source_of(name)} 注册，"
                "使用 replace=True 覆盖"
            )


def _replace_names(manifest, replace=False):
    return {
        item["name"] for item in manifest["components"]
        if _allow_replace(manifest, item, replace)
    }


def _check_capabilities(manifest, host):
    """加载前协商能力：provider 需要的能力必须全部在宿主支持集内。"""
    required = list(manifest.get("capabilities") or ())
    missing = [c for c in required if c not in host.capabilities]
    if missing:
        raise ProviderError(
            f"组件能力不受宿主支持: 提供者 {manifest['name']} 需要 "
            f"{', '.join(required)}，宿主缺少 {', '.join(missing)}"
            f"（宿主支持: {', '.join(sorted(host.capabilities)) or '无'}）"
        )


def _check_props_consistency(manifest):
    """清单声明的 props 默认值必须与实现的类属性 props 一致。"""
    for item in manifest["components"]:
        name = item["name"]
        declared = item.get("props") or {}
        if not declared:
            continue
        cls = ui.COMPONENTS.get(name)
        if cls is None:
            continue
        impl = getattr(cls, "props", None) or {}
        for key, default in declared.items():
            if key not in impl:
                raise ProviderError(
                    f"组件 {name} 的 props 与 manifest 不一致: 实现缺少 {key!r}"
                )
            if impl[key] != default:
                raise ProviderError(
                    f"组件 {name} 的 props 与 manifest 不一致: "
                    f"{key!r} 实现={impl[key]!r}，清单={default!r}"
                )
        extra = sorted(set(impl) - set(declared))
        if extra:
            _warn(f"组件 {name} 的实现 props 未在 manifest 声明: {', '.join(extra)}")


def _snapshot_state():
    return dict(ui.COMPONENTS), dict(_COMPONENT_SOURCES)


def _restore_state(components, sources):
    """注册后校验失败时回滚全局注册表与来源表。"""
    for name in list(ui.COMPONENTS):
        if name not in components:
            del ui.COMPONENTS[name]
    ui.COMPONENTS.update(components)
    _COMPONENT_SOURCES.clear()
    _COMPONENT_SOURCES.update(sources)


def validate_provider(provider, replace=False) -> list:
    """静态校验提供者是否符合 ABI，返回声明的组件名列表（不注册、不查全局冲突）。

    这里只做 ABI 静态形状校验。与全局注册表的同名冲突由 register_provider
    在真正注册前检查——因为 presets 等内置提供者是「导入即注册」的，
    对其调用 validate_provider 时组件本就在表里，不应被误判为冲突。
    replace 参数保留以兼容旧调用签名。
    """
    manifest = _validate_provider_shape(provider)
    return [item["name"] for item in manifest["components"]]


def register_provider(provider, abi: int = COMPONENT_ABI_VERSION,
                      replace: bool = False, capabilities=None) -> list:
    """校验并注册一个组件提供者，返回注册的组件名列表。

    replace 可由调用方传入，也可由 manifest 顶层或单个组件的 "replace": true 声明。
    capabilities 指定宿主能力集（缺省 DEFAULT_HOST_CAPABILITIES），加载前与
    manifest.capabilities 协商，宿主不支持则拒绝。
    """
    manifest = _validate_provider_shape(provider)
    expected = [item["name"] for item in manifest["components"]]
    _check_global_conflicts(manifest, replace=replace)

    host = Host(abi, capabilities=capabilities,
                replace_names=_replace_names(manifest, replace))
    _check_capabilities(manifest, host)

    components_before, sources_before = _snapshot_state()
    provider.register(host)

    # 精确回查：只认本次 register 真正引入/覆盖的名字。名字"本来就在全局表里"
    # 不再算注册成功；provider 内部重复注册由 _replace_names 消化，不误报。
    touched = set(host._touched)
    for name in expected:
        if ui.COMPONENTS.get(name) is not components_before.get(name):
            touched.add(name)
    missing = [name for name in expected if name not in touched]
    if missing:
        _restore_state(components_before, sources_before)
        raise ProviderError(
            "manifest 声明了但这些组件没有注册: " + ", ".join(missing)
            + "（本次 register 未引入）"
        )

    try:
        _check_props_consistency(manifest)
    except ProviderError:
        _restore_state(components_before, sources_before)
        raise

    provider_name = manifest.get("name") or "unknown"
    for name in expected:
        _COMPONENT_SOURCES[name] = provider_name
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