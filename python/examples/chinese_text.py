"""真实字体（含中文）渲染示例。

    PYTHONPATH=python python3 python/examples/chinese_text.py
    VAPOR_DEMO_FRAMES=3 PYTHONPATH=python python3 python/examples/chinese_text.py

没有原生 Vapor._ui 时仍可运行，只是回退到内置点阵（中文显示为空白）。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Vapor import font  # noqa: E402
from Vapor import ui as V  # noqa: E402


class ChineseDemo(V.Component):
    template = '''
      <View bg="#0B0E13" padding="24" gap="14" w="100%" h="100%">
        <Text size="24" color="#7BD88F">VAPOR 字体扩展</Text>
        <Text size="16" color="#E6EAF0" :wrap="True" :w="312">
          中文排版现在由真实字体的 advance 驱动：布局测量、换行位置、字形光栅化
          都来自 Vapor._ui 的 ttf-parser + 自建扫描线光栅化，任意字号都清晰。
        </Text>
        <View bg="#141A22" radius="10" padding="14" gap="6">
          <Text size="13" color="#8A94A6">字体信息</Text>
          <Text size="13" color="#E6EAF0" :wrap="True" :w="284">{{ info }}</Text>
        </View>
        <Text size="13" color="#6B7683">{{ hint }}</Text>
      </View>
    '''

    def setup(self, props, ctx):
        installed = font.install()
        stack = font.active()
        if installed and stack is not None:
            info = f"{stack}"
        elif not font.available():
            info = "未安装原生 Vapor._ui（纯 Python 回退），当前使用内置 5x7 点阵"
        else:
            info = "没有找到系统字体；设置 VAPOR_FONT=/path/to/font.ttf 可指定"
        return {
            "info": info,
            "hint": "中文需要 CJK 字体；Android 会自动使用 /system/fonts/NotoSansCJK-Regular.ttc",
        }


def main() -> int:
    app = V.create_app(ChineseDemo, width=360, height=640)
    app.mount()
    print("原生扩展:", V._native.status())
    print("字体:", font.active())
    frames = int(os.environ.get("VAPOR_DEMO_FRAMES", "0") or 0)
    if frames:
        for _ in range(frames):
            app.tick()
        print("已渲染", frames, "帧")
        return 0
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
