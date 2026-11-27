"""Vapor 逻辑包启动入口（协议约定：入口固定为 main.py）。

这个示例展示基础 UI 与一次绘制。真实项目里改成你的逻辑即可——新增功能只需
在 lib/ 放 C/Python 扩展并在 abi.json 声明，无需改 Rust 渲染器。
"""
import rust.Vapor as V
import rust.Vapor.ui as ui
import rust.Vapor.ext as ext
import rust.Vapor.fs as fs

# 加载 lib/ 下的 ABI 扩展（abi.json 管理）
ext.load_manifest("abi.json")

# 主循环示例：渲染器宿主已经在每帧 present 时上屏；这里绘制一次界面
W, H = V.size()
V.clear(V.rgb(16, 20, 24))
V.rect(0, 0, W, 72, V.rgb(32, 38, 46))
V.text("VAPOR APP", 24, 30, V.rgb(255, 255, 255), 3)
V.text("CPYTHON " + V.cpython_version(), 24, 110, V.rgb(138, 151, 166), 2)
V.text("HELLO FROM main.py", 24, 160, V.rgb(58, 123, 213), 2)
V.rect(24, 220, W - 48, 2, V.rgb(90, 104, 120))
V.present()
