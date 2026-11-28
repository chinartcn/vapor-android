#!/usr/bin/env python3
"""把 Vapor（纯 Python 包 + PyO3 原生扩展）安装进指定的 CPython 运行时树。

用法::

    # 装进 Android 用的 CPython（build/cpython/arm64 等）
    python3 scripts/install_to_cpython.py \
        --home build/cpython/arm64 --abi arm64 --pyext build/pyext

    # 只装到指定 site-packages（CI / 自定义布局）
    python3 scripts/install_to_cpython.py --dest /tmp/sp --abi arm64 --no-native

安装内容：
    <site-packages>/Vapor/     响应式框架（纯 Python + ABI 原生扩展）
    <site-packages>/rust/      底层 rust.Vapor（共享内存帧缓冲）
"""

from __future__ import annotations

import argparse
import compileall
import shutil
import sys
from pathlib import Path

ABI_DIR = {"arm64": "arm64-v8a", "arm32": "armeabi-v7a"}
PY_SUFFIXES = (".py", ".so", ".pyd", ".dylib")


def find_site_packages(home: Path) -> Path:
    candidates = sorted(home.glob("lib/python3.*/site-packages"))
    if candidates:
        return candidates[-1]
    return home / "site-packages"


def copy_tree(src: Path, dst: Path) -> int:
    if not src.is_dir():
        print("[skip] 缺少 " + str(src))
        return 0
    dst.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in src.rglob("*"):
        if item.is_dir() or "__pycache__" in item.parts:
            continue
        if item.suffix not in PY_SUFFIXES:
            continue
        target = dst / item.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        count += 1
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description="安装 Vapor Python 包到 CPython 运行时树")
    ap.add_argument("--home", default="", help="CPython 前缀目录（含 lib/python3.x）")
    ap.add_argument("--dest", default="", help="直接指定 site-packages 目录")
    ap.add_argument("--abi", default="arm64", choices=sorted(ABI_DIR), help="目标架构")
    ap.add_argument("--pyext", default="build/pyext", help="PyO3 扩展产物根目录")
    ap.add_argument("--python-src", default="python", help="Python 源码包根目录")
    ap.add_argument("--no-native", action="store_true", help="只装纯 Python 部分")
    args = ap.parse_args()

    if args.dest:
        site = Path(args.dest)
    elif args.home:
        site = find_site_packages(Path(args.home))
    else:
        print("[FATAL] 需要 --home 或 --dest", file=sys.stderr)
        return 2
    site.mkdir(parents=True, exist_ok=True)

    src_root = Path(args.python_src)
    total = 0
    total += copy_tree(src_root / "Vapor", site / "Vapor")
    total += copy_tree(src_root / "rust", site / "rust")

    if not args.no_native:
        ext_root = Path(args.pyext) / ABI_DIR[args.abi] / "Vapor"
        if not ext_root.is_dir():
            print("[warn] 找不到原生扩展 " + str(ext_root) + "（先运行 scripts/build_android_pyext.sh）")
        else:
            for so in sorted(ext_root.glob("*.so")):
                shutil.copy2(so, site / "Vapor" / so.name)
                total += 1
                print("[ok] " + so.name + " -> " + str(site / "Vapor" / so.name))

    compileall.compile_dir(str(site), quiet=1, force=False)

    if args.home:
        marker = Path(args.home) / "vapor-site-packages.path"
    else:
        marker = site / ".vapor-installed"
    try:
        marker.write_text(str(site), encoding="utf-8")
    except OSError:
        pass

    print("[done] 安装 " + str(total) + " 个文件 -> " + str(site))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
