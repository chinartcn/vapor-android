//! 2D 仿射矩阵（列向量约定）：
//!   x' = a*x + c*y + e
//!   y' = b*x + d*y + f

use pyo3::prelude::*;

#[pyclass(module = "Vapor._ui", name = "Mat2D")]
#[derive(Clone, Copy, Debug)]
pub struct Mat2D {
    #[pyo3(get)]
    pub a: f64,
    #[pyo3(get)]
    pub b: f64,
    #[pyo3(get)]
    pub c: f64,
    #[pyo3(get)]
    pub d: f64,
    #[pyo3(get)]
    pub e: f64,
    #[pyo3(get)]
    pub f: f64,
}

impl Mat2D {
    /// 内部使用的点变换（Python 侧的 apply(x, y) 由 pymethods 提供）。
    pub fn transform_point(&self, p: (f64, f64)) -> (f64, f64) {
        (
            self.a * p.0 + self.c * p.1 + self.e,
            self.b * p.0 + self.d * p.1 + self.f,
        )
    }
}

#[pymethods]
impl Mat2D {
    #[new]
    #[pyo3(signature = (a=1.0, b=0.0, c=0.0, d=1.0, e=0.0, f=0.0))]
    fn new(a: f64, b: f64, c: f64, d: f64, e: f64, f: f64) -> Self {
        Mat2D { a, b, c, d, e, f }
    }

    #[staticmethod]
    fn identity() -> Mat2D {
        Mat2D { a: 1.0, b: 0.0, c: 0.0, d: 1.0, e: 0.0, f: 0.0 }
    }

    #[staticmethod]
    fn translate(tx: f64, ty: f64) -> Mat2D {
        Mat2D { a: 1.0, b: 0.0, c: 0.0, d: 1.0, e: tx, f: ty }
    }

    #[staticmethod]
    #[pyo3(signature = (sx, sy=None))]
    fn scale(sx: f64, sy: Option<f64>) -> Mat2D {
        let sy = sy.unwrap_or(sx);
        Mat2D { a: sx, b: 0.0, c: 0.0, d: sy, e: 0.0, f: 0.0 }
    }

    #[staticmethod]
    fn rotate(deg: f64) -> Mat2D {
        let r = deg.to_radians();
        let (s, c) = r.sin_cos();
        Mat2D { a: c, b: s, c: -s, d: c, e: 0.0, f: 0.0 }
    }

    #[staticmethod]
    #[pyo3(signature = (ax_deg, ay_deg=0.0))]
    fn skew(ax_deg: f64, ay_deg: f64) -> Mat2D {
        Mat2D {
            a: 1.0,
            b: ay_deg.to_radians().tan(),
            c: ax_deg.to_radians().tan(),
            d: 1.0,
            e: 0.0,
            f: 0.0,
        }
    }

    /// 复合：先应用 other，再应用 self。
    fn mul(&self, other: &Mat2D) -> Mat2D {
        Mat2D {
            a: self.a * other.a + self.c * other.b,
            b: self.b * other.a + self.d * other.b,
            c: self.a * other.c + self.c * other.d,
            d: self.b * other.c + self.d * other.d,
            e: self.a * other.e + self.c * other.f + self.e,
            f: self.b * other.e + self.d * other.f + self.f,
        }
    }

    fn then(&self, other: &Mat2D) -> Mat2D {
        other.mul(self)
    }

    fn apply(&self, x: f64, y: f64) -> (f64, f64) {
        Mat2D::transform_point(self, (x, y))
    }

    fn inverse(&self) -> Mat2D {
        let det = self.a * self.d - self.b * self.c;
        if det.abs() < 1e-12 {
            return Mat2D::identity();
        }
        let inv = 1.0 / det;
        let a = self.d * inv;
        let b = -self.b * inv;
        let c = -self.c * inv;
        let d = self.a * inv;
        let e = (self.c * self.f - self.d * self.e) * inv;
        let f = (self.b * self.e - self.a * self.f) * inv;
        Mat2D { a, b, c, d, e, f }
    }

    fn to_tuple(&self) -> (f64, f64, f64, f64, f64, f64) {
        (self.a, self.b, self.c, self.d, self.e, self.f)
    }

    fn __repr__(&self) -> String {
        format!(
            "Mat2D(a={}, b={}, c={}, d={}, e={}, f={})",
            self.a, self.b, self.c, self.d, self.e, self.f
        )
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Mat2D>()?;
    Ok(())
}
