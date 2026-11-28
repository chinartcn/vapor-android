"""Showcase 逻辑包入口（宿主通过 vapor_py_run_file 执行）。"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.join(_HERE, "lib") not in sys.path:
    sys.path.insert(0, os.path.join(_HERE, "lib"))

import showcase_app  # noqa: E402


def main() -> int:
    return showcase_app.main()


if __name__ == "__main__":
    raise SystemExit(main())
