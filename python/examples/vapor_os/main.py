"""Vapor OS 演示 CLI 入口（执行文档 1）。

- 交互：``app.run()`` 进入帧循环。
- 无头/CI：``VAPOR_DEMO_FRAMES=N`` 渲染 N 帧后退出。
"""

from __future__ import annotations

import os

from .app import build_app


def main() -> int:
    app = build_app()
    width = getattr(app.surface, "width", None)
    height = getattr(app.surface, "height", None)
    print("[vapor-os] ready:", width, "x", height)
    frames = int(os.environ.get("VAPOR_DEMO_FRAMES", "0") or 0)
    if frames:
        for _ in range(frames):
            app.tick()
        print("[vapor-os] rendered", frames, "frames")
        return 0
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
