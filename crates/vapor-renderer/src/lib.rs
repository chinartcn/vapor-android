//! app1 —— 纯渲染器。产出 libvapor.so，导出全部稳定 C ABI。
//! Rust 侧只做渲染与扩展宿主；所有业务逻辑来自逻辑包 main.py。
//! nativeInit 由 Java 层 RendererActivity 在创建 Surface 后调用。

pub use vapor_core::*;
