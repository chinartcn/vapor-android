"""Vapor 逻辑包启动入口（协议约定：入口固定为 main.py）。

渲染测试：在屏幕上绘制圆、正方形等几何图形，验证共享内存帧缓冲 ->
GLES 上屏链路是否正常工作。新增功能只需在 lib/ 放扩展并在 abi.json 声明。
"""
import rust.Vapor as V
from rust import ui, fs
from rust import extension as ext

# 加载 lib/ 下的 ABI 扩展（abi.json 管理）
ext.load_manifest("abi.json")

# ---------------------------------------------------------------------------
# 几何图形渲染测试
# ---------------------------------------------------------------------------
W, H = V.size()

# 背景 + 标题栏
V.clear(V.rgb(16, 20, 28))
V.rect(0, 0, W, 60, V.rgb(30, 38, 52))
V.text("VAPOR RENDER TEST", 16, 20, V.rgb(235, 240, 250), 3)

# 颜色定义
RED     = V.rgb(233, 84, 84)
GREEN   = V.rgb(102, 210, 122)
BLUE    = V.rgb(88, 156, 242)
YELLOW  = V.rgb(246, 200, 88)
CYAN    = V.rgb(86, 214, 214)
MAGENTA = V.rgb(206, 116, 216)
WHITE   = V.rgb(230, 236, 245)
DIM     = V.rgb(120, 134, 152)

# --- 第一行：正方形测试 ---
y0 = 84
V.text("SQUARES", 16, y0, DIM, 2)
s = 46  # 正方形边长
# 实心正方形（三种颜色）
V.rect(16,  y0 + 26, s, s, RED)
V.rect(16 + s + 14, y0 + 26, s, s, GREEN)
V.rect(16 + (s + 14) * 2, y0 + 26, s, s, BLUE)
# 空心正方形
V.square_outline(16 + (s + 14) * 3, y0 + 26, s, YELLOW, thickness=3)

# --- 第二行：圆测试 ---
y1 = y0 + 26 + s + 34
V.text("CIRCLES", 16, y1, DIM, 2)
r = 26  # 半径
# 实心圆（三种颜色）
V.disc(16 + r,      y1 + 26 + r, r, RED)
V.disc(16 + r * 3 + 14, y1 + 26 + r, r, GREEN)
V.disc(16 + r * 5 + 28, y1 + 26 + r, r, CYAN)
# 空心圆
V.circle(16 + r * 7 + 42, y1 + 26 + r, r, MAGENTA)

# --- 第三行：混合测试（同心圆 + 嵌套方框） ---
y2 = y1 + 26 + r * 2 + 34
V.text("NESTED", 16, y2, DIM, 2)
cx, cy = 70, y2 + 70
V.disc(cx, cy, 48, BLUE)
V.disc(cx, cy, 34, V.rgb(24, 32, 46))
V.circle(cx, cy, 44, WHITE)
V.circle(cx, cy, 20, YELLOW)
V.square_outline(cx - 56, cy - 56, 112, GREEN, thickness=3)
V.square_outline(cx - 40, cy - 40, 80, RED, thickness=2)

# 状态文字
V.text("CPYTHON 3.15.2 OK", 16, H - 90, GREEN, 2)
V.text("FB %dx%d" % (W, H), 16, H - 56, DIM, 2)

# 提交绘制（宿主渲染线程每帧也会把帧缓冲上屏）
V.present()
