"""Python ABI 扩展示例。放到 lib/ 并在 abi.json 声明即可被加载。

意图：证明“做完这一次后只需要用扩展就可以加功能”。此扩展往帧缓冲画一个
横幅，完全用 rust.Vapor 的公共 API，不碰 Rust 源码。
"""
import rust.Vapor as V


def on_load():
    w, h = V.size()
    V.rect(24, h - 130, w - 48, 60, V.rgb(46, 139, 87))
    V.text("PY EXT LOADED", 40, h - 112, V.rgb(255, 255, 255), 2)


on_load()
