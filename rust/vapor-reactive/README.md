# vapor-reactive

`Vapor._core` —— Vapor 响应式框架的原生内核。

* **响应式**：alien-signals 风格的双向链表依赖图，signal / computed / effect / scope / batch / untrack。
* **光栅化**：直接写 RGBA8888 帧缓冲的 2D 图元（矩形 / 直线 / 圆 / 圆弧 / 多边形 / 圆角矩形 / 渐变 / 混合 / 描边路径）。

由 maturin 打包，与 `Vapor` Python 包混布安装（mixed layout）。
