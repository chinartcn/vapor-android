//! Vapor._ui —— Vapor UI 几何内核。
//!
//! 只做几何：SVG path 解析与扁平化、2D 仿射矩阵、文本度量。
//! 光栅化在 Vapor._core 的 Canvas 中完成，分层清晰、可单独测试。

use pyo3::prelude::*;

pub mod font;
pub mod mat;
pub mod path;
pub mod text;

use mat::Mat2D;

/// 把 SVG path 的 d 字符串扁平化为折线。
/// 返回 [(closed, [(x, y), ...]), ...]，可直接交给 Canvas.polygon / polyline。
#[pyfunction]
#[pyo3(signature = (d, tolerance=0.3, transform=None))]
fn flatten_path(
    d: &str,
    tolerance: f64,
    transform: Option<PyRef<'_, Mat2D>>,
) -> Vec<(bool, Vec<(f64, f64)>)> {
    let mut polys = path::parse(d, tolerance);
    if let Some(m) = transform {
        for pl in polys.iter_mut() {
            for p in pl.points.iter_mut() {
                *p = m.transform_point(*p);
            }
        }
    }
    polys
        .into_iter()
        .map(|p| (p.closed, p.points))
        .collect()
}

#[pyfunction]
#[pyo3(signature = (d, tolerance=0.3))]
fn path_bounds(d: &str, tolerance: f64) -> (f64, f64, f64, f64) {
    let polys = path::parse(d, tolerance);
    path::bounds(&polys)
}

#[pyfunction]
fn path_segment_count(d: &str) -> usize {
    path::parse(d, 0.5).iter().map(|p| p.points.len()).sum()
}

#[pymodule]
fn _ui(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    font::register(m)?;
    mat::register(m)?;
    text::register(m)?;
    m.add_function(wrap_pyfunction!(flatten_path, m)?)?;
    m.add_function(wrap_pyfunction!(path_bounds, m)?)?;
    m.add_function(wrap_pyfunction!(path_segment_count, m)?)?;
    Ok(())
}
