# vapor-ui

`Vapor._ui` —— Vapor 的 UI 几何内核（由 maturin 打包）。

* SVG path：M/L/H/V/C/S/Q/T/A/Z 全指令解析，贝塞尔与椭圆弧自适应扁平化
* Mat2D：2D 仿射矩阵（平移/缩放/旋转/斜切/求逆）
* 文本度量：字宽估算、按行折行（中英混排）

光栅化本身在 `Vapor._core` 的 Canvas 里，本模块只负责几何。
