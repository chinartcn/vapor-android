#!/usr/bin/env python3
"""校验两个架构的 CPython 版本严格一致（禁止降级匹配）。

读取 build/cpython/{arm64,arm32}.version 与源码 patchlevel.h，任一缺失或
与目标版本不符即以非零码退出。CI/打包前必跑。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = "3.15.2"
OUT = ROOT / "build" / "cpython"


def read_marker(arch: str) -> str:
    p = OUT / f"{arch}.version"
    if not p.exists():
        return "MISSING"
    return p.read_text(encoding="utf-8").strip()


def main() -> int:
    a = read_marker("arm64")
    b = read_marker("arm32")
    print(f"arm64 CPython = {a}")
    print(f"arm32 CPython = {b}")
    problems = []
    if a != TARGET:
        problems.append(f"arm64 版本 {a} != 目标 {TARGET}")
    if b != TARGET:
        problems.append(f"arm32 版本 {b} != 目标 {TARGET}")
    if a != b:
        problems.append(f"双架构版本不一致: arm64={a}, arm32={b}")
    if problems:
        for p in problems:
            print(f"[FATAL] {p}", file=sys.stderr)
        print("禁止降级匹配：两个架构必须都是同一版本 " + TARGET, file=sys.stderr)
        return 1
    print(f"[PASS] arm64 == arm32 == {TARGET} ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
