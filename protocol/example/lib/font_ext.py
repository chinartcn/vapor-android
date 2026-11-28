"""字体扩展示例：给逻辑包接上真实字体（含中文）。

这是「加功能 = 写一个扩展」的示例之一：不改 Rust、不重编译，
只要在 lib/abi.json 里把本扩展 enabled 设为 true，宿主启动时就会执行它。

    {
      "name": "font_ext", "kind": "python", "path": "font_ext.py",
      "arch": "all", "enabled": true
    }

行为：
* 优先用 VAPOR_FONT 指定的字体；
* 否则扫描系统字体（Android 的 NotoSansCJK / DroidSansFallback 等）；
* 装上后 Vapor.ui 的文本测量与绘制都走真实字形，中文不再显示为空白。
"""

from __future__ import annotations

import os

try:
    from Vapor import font
    from Vapor import ui as V
except Exception as exc:  # Vapor 包还没装好时不要让宿主崩溃
    font = None
    V = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def on_load() -> int:
    if font is None:
        print(f"[font_ext] 无法导入 Vapor.font: {_IMPORT_ERROR}")
        return -1
    if not font.available():
        print("[font_ext] 没有原生 Vapor._ui，保持内置点阵字体")
        return -1

    target = os.environ.get("VAPOR_FONT")
    stack = None
    if target:
        try:
            stack = font.load(target)
        except Exception as exc:
            print(f"[font_ext] 加载 {target} 失败: {exc}")

    if stack is None:
        stack = font.default_font()
    if stack is None:
        print("[font_ext] 没有找到可用字体（可设置 VAPOR_FONT 指定 ttf/ttc）")
        return -1

    if not font.install(stack):
        print("[font_ext] 安装字体失败")
        return -1

    # 示例：验证中文字形覆盖率
    if hasattr(stack, "coverage"):
        ratio = stack.coverage("中文字体测试 Vapor")
        print(f"[font_ext] 已安装 {stack!r}，示例文本字形覆盖率 {ratio:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(on_load())
