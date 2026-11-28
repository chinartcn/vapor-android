"""Vapor OS 演示 · 无头出帧 / 多场景验证（执行文档 4）。

用法::

    VAPOR_DEMO_FRAMES=2 python3 -m examples.vapor_os.run_showcase

逐场景 ``build_app()`` → ``tick()`` N 帧 → 卸载；不进入 ``app.run()``，
因此可在 CI 中直接执行。若 Surface 提供截图 API 则额外落盘到 ``_shots/``，
没有则只验证不崩。
"""

from __future__ import annotations

import os
import sys

from .app import build_app
from .data import build_routes

__all__ = ["SCENES", "main"]

SCENES = (
    "home",
    "settings",
    "clock",
    "gallery",
    "calculator",
    "shade",
    "lock",
    "recents",
)

_SHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")


def _env_frames(default=2):
    raw = os.environ.get("VAPOR_DEMO_FRAMES", "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _selected_scenes():
    raw = os.environ.get("VAPOR_OS_SCENES", "").strip()
    if not raw:
        return list(SCENES)
    wanted = [part.strip() for part in raw.split(",") if part.strip()]
    return [scene for scene in wanted if scene in SCENES]


def _save_shot(surface, scene):
    """Surface 支持截图时落盘；无能力时返回 None（跨版本兼容）。"""
    for name in ("to_png", "save_image"):
        method = getattr(surface, name, None)
        if not callable(method):
            continue
        os.makedirs(_SHOT_DIR, exist_ok=True)
        path = os.path.join(_SHOT_DIR, f"{scene}.png")
        try:
            result = method(path)
        except TypeError:
            try:
                result = method()
            except Exception:
                return None
        except Exception:
            return None
        if isinstance(result, (bytes, bytearray)):
            with open(path, "wb") as fh:
                fh.write(result)
        return path
    return None


def main() -> int:
    routes = build_routes()
    frames = _env_frames()
    scenes = _selected_scenes()
    failed = []

    for scene in scenes:
        # 单场景路由表：build_app 在缺少 home 时以第一个 route 作为初始页。
        app = build_app(routes={scene: routes[scene]})
        try:
            for _ in range(frames):
                app.tick()
            shot = _save_shot(app.surface, scene)
        except Exception as exc:  # 单场景失败不阻塞其余场景
            failed.append((scene, repr(exc)))
            print(f"[vapor-os] {scene}: FAIL {exc}", file=sys.stderr)
        else:
            suffix = f" shot={shot}" if shot else ""
            print(f"[vapor-os] {scene}: ok ({frames} frames){suffix}")
        finally:
            try:
                app.unmount()
            except Exception:
                pass

    if failed:
        print(f"[vapor-os] {len(failed)} 个场景失败", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
