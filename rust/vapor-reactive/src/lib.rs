//! Vapor._core —— Vapor 响应式框架的原生内核。
//!
//! 两个子系统：
//!   * signals —— alien-signals 风格的细粒度响应式（signal/computed/effect/scope/batch）
//!   * draw    —— 直接写 RGBA8888 帧缓冲的高速 2D 光栅化
//!
//! 这不是 vdom：没有虚拟节点、没有 diff。effect 直接改目标值，
//! 绘制层直接把目标值变成像素。

use pyo3::prelude::*;

pub mod draw;
pub mod signals;

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add("ABI_VERSION", 2i32)?;
    signals::register(m)?;
    draw::register(m)?;
    Ok(())
}
