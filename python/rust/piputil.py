"""rust.Vapor.pip —— 在逻辑环境里动态安装 Python 扩展（内置 pip）。

CPython 3.15 自带 ensurepip；渲染器启动时已把 site-packages 指向私有可写目录，
因此 ``pip install`` 装的包天然落在 ``lib/`` 对应的环境里，可被逻辑包 import。
"""

from __future__ import annotations

import subprocess
import sys

from ._abi import lib, VAPOR_OK


def install(*packages: str, index_url: str | None = None, extra: tuple[str, ...] = ()) -> int:
    """pip install 若干包。返回 0 成功。"""
    if not packages:
        return 0
    cmd = [sys.executable or "python", "-m", "pip", "install", *packages, *extra]
    if index_url:
        cmd += ["-i", index_url]
    try:
        return subprocess.call(cmd)
    except Exception:
        # 回退：交给 Rust 内嵌解释器执行 pip
        joined = " ".join(packages)
        code = f"import subprocess,sys; sys.exit(subprocess.call([sys.executable,'-m','pip','install',{joined!r}]))"
        return 0 if lib().vapor_py_eval(code.encode()) == VAPOR_OK else 1


def install_requirements(path: str = "requirements.txt") -> int:
    return lib().vapor_py_install_requirements(path.encode())


def ensure_pip() -> None:
    try:
        subprocess.check_call([sys.executable or "python", "-m", "ensurepip", "--upgrade"])
    except Exception:
        pass


def list_installed() -> list[str]:
    try:
        out = subprocess.check_output(
            [sys.executable or "python", "-m", "pip", "list", "--format=freeze"],
            text=True,
        )
        return [line for line in out.splitlines() if line]
    except Exception:
        return []
