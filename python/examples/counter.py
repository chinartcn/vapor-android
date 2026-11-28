"""Vapor 响应式 UI 最小示例：无 vdom 的计数器。

    # 直接跑（没有原生扩展时自动用纯 Python 回退）
    PYTHONPATH=python python3 python/examples/counter.py

    # 只跑若干帧后退出（CI / 无头验证）
    VAPOR_DEMO_FRAMES=3 PYTHONPATH=python python3 python/examples/counter.py
"""

from __future__ import annotations

import os
import sys

# 已安装（wheel / maturin develop）时优先用安装版，避免源码目录遮蔽原生扩展
try:
    import Vapor  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor import ui as V  # noqa: E402


class Counter(V.Component):
    template = '''
      <View bg="#0B0E13" padding="24" gap="16" w="100%" h="100%">
        <Text size="22" color="#8A94A6">VAPOR / NO VDOM</Text>
        <Text :size="72" :color="accent" align="center">{{ count }}</Text>
        <Row gap="12">
          <Button text="-1" :on_click="dec" bg="#2A3038" />
          <Button text="+1" :on_click="inc" />
        </Row>
        <Text size="13" color="#6B7683">只有依赖 count 的 effect 会重跑</Text>
      </View>
    '''

    def setup(self, props, ctx):
        count = V.ref(0)

        def inc(event=None):
            count.value += 1

        def dec(event=None):
            count.value -= 1

        return {
            "count": count,
            "accent": V.computed(lambda: "#7BD88F" if count.value > 0 else "#E6EAF0"),
            "inc": inc,
            "dec": dec,
        }


def main() -> int:
    app = V.create_app(Counter, width=360, height=640)
    app.mount()
    print("原生扩展:", V._native.status())
    frames = int(os.environ.get("VAPOR_DEMO_FRAMES", "0") or 0)
    if frames:
        for _ in range(frames):
            app.tick()
        print("已渲染", frames, "帧，缓冲尺寸:", app.surface.width, "x", app.surface.height)
        return 0
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
