//! 高速 2D 光栅化：直接写 RGBA8888 帧缓冲。
//!
//! 颜色打包格式与 Python 侧一致：0xAABBGGRR（低字节为 R），即
//! color = (a << 24) | (b << 16) | (g << 8) | r。
//!
//! 所有图元都是直接内存写入，无临时分配、无 Python 对象创建（除返回值）。

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

#[inline]
fn idx(x: i32, y: i32, w: i32) -> usize {
    ((y as usize) * (w as usize) + (x as usize)) * 4
}

#[inline]
fn put(data: &mut [u8], w: i32, x: i32, y: i32, color: u32) {
    let i = idx(x, y, w);
    let r = (color & 0xFF) as u8;
    let g = ((color >> 8) & 0xFF) as u8;
    let b = ((color >> 16) & 0xFF) as u8;
    let a = ((color >> 24) & 0xFF) as u8;
    if a == 255 {
        data[i] = r;
        data[i + 1] = g;
        data[i + 2] = b;
        data[i + 3] = 255;
    } else if a != 0 {
        let ia = 255 - a as u16;
        let a16 = a as u16;
        data[i] = ((r as u16 * a16 + data[i] as u16 * ia) / 255) as u8;
        data[i + 1] = ((g as u16 * a16 + data[i + 1] as u16 * ia) / 255) as u8;
        data[i + 2] = ((b as u16 * a16 + data[i + 2] as u16 * ia) / 255) as u8;
        data[i + 3] = 255;
    }
}

#[inline]
fn blend_px(data: &mut [u8], w: i32, x: i32, y: i32, r: u8, g: u8, b: u8, a: u8) {
    let color = ((a as u32) << 24) | ((b as u32) << 16) | ((g as u32) << 8) | (r as u32);
    put(data, w, x, y, color);
}

pub fn fill_rect_impl(data: &mut [u8], w: i32, h: i32, x: i32, y: i32, rw: i32, rh: i32, color: u32) {
    if rw <= 0 || rh <= 0 || w <= 0 || h <= 0 {
        return;
    }
    let x0 = x.max(0);
    let y0 = y.max(0);
    let x1 = (x + rw).min(w);
    let y1 = (y + rh).min(h);
    if x0 >= x1 || y0 >= y1 {
        return;
    }
    let a = ((color >> 24) & 0xFF) as u8;
    let r = (color & 0xFF) as u8;
    let g = ((color >> 8) & 0xFF) as u8;
    let b = ((color >> 16) & 0xFF) as u8;
    // 完整 4 字节写入的快速路径
    if a == 255 {
        let px = [r, g, b, 255u8];
        for yy in y0..y1 {
            let start = idx(x0, yy, w);
            let end = idx(x1, yy, w);
            for chunk in data[start..end].chunks_exact_mut(4) {
                chunk.copy_from_slice(&px);
            }
        }
    } else if a != 0 {
        for yy in y0..y1 {
            for xx in x0..x1 {
                put(data, w, xx, yy, color);
            }
        }
    }
}

pub fn line_impl(data: &mut [u8], w: i32, h: i32, x0: i32, y0: i32, x1: i32, y1: i32, color: u32, width: i32) {
    let thickness = width.max(1);
    let mut x = x0;
    let mut y = y0;
    let dx = (x1 - x0).abs();
    let dy = -(y1 - y0).abs();
    let sx = if x0 < x1 { 1 } else { -1 };
    let sy = if y0 < y1 { 1 } else { -1 };
    let mut err = dx + dy;
    let half = thickness / 2;
    loop {
        fill_rect_impl(data, w, h, x - half, y - half, thickness, thickness, color);
        if x == x1 && y == y1 {
            break;
        }
        let e2 = 2 * err;
        if e2 >= dy {
            err += dy;
            x += sx;
        }
        if e2 <= dx {
            err += dx;
            y += sy;
        }
    }
}

pub fn disc_impl(data: &mut [u8], w: i32, h: i32, cx: i32, cy: i32, r: i32, color: u32) {
    if r <= 0 {
        return;
    }
    let rr = r as f64 * r as f64;
    for dy in -r..=r {
        let yy = cy + dy;
        if yy < 0 || yy >= h {
            continue;
        }
        let inside = rr - (dy as f64) * (dy as f64);
        if inside < 0.0 {
            continue;
        }
        let dx = inside.sqrt() as i32;
        fill_rect_impl(data, w, h, cx - dx, yy, dx * 2 + 1, 1, color);
    }
}

pub fn ring_impl(data: &mut [u8], w: i32, h: i32, cx: i32, cy: i32, r: i32, width: i32, color: u32) {
    let inner = (r - width.max(1)).max(0);
    let ri = inner as f64 * inner as f64;
    let ro = r as f64 * r as f64;
    for dy in -r..=r {
        let yy = cy + dy;
        if yy < 0 || yy >= h {
            continue;
        }
        let d2 = (dy as f64) * (dy as f64);
        let outer = ro - d2;
        if outer < 0.0 {
            continue;
        }
        let ox = outer.sqrt() as i32;
        let ix = if ri - d2 > 0.0 { (ri - d2).sqrt() as i32 } else { 0 };
        // 左半环
        fill_rect_impl(data, w, h, cx - ox, yy, ox - ix, 1, color);
        // 右半环
        fill_rect_impl(data, w, h, cx + ix + 1, yy, ox - ix, 1, color);
    }
}

pub fn polygon_impl(data: &mut [u8], w: i32, h: i32, pts: &[(i32, i32)], color: u32) {
    if pts.len() < 3 {
        return;
    }
    let mut min_y = i32::MAX;
    let mut max_y = i32::MIN;
    for p in pts {
        min_y = min_y.min(p.1);
        max_y = max_y.max(p.1);
    }
    let y_start = min_y.max(0);
    let y_end = max_y.min(h - 1);
    let n = pts.len();
    let mut xs: Vec<f64> = Vec::with_capacity(16);
    for yy in y_start..=y_end {
        let scan = yy as f64 + 0.5;
        xs.clear();
        for i in 0..n {
            let (x1, y1) = pts[i];
            let (x2, y2) = pts[(i + 1) % n];
            let (y1f, y2f) = (y1 as f64, y2 as f64);
            if (y1f <= scan && y2f > scan) || (y2f <= scan && y1f > scan) {
                let t = (scan - y1f) / (y2f - y1f);
                xs.push(x1 as f64 + t * (x2 - x1) as f64);
            }
        }
        if xs.len() < 2 {
            continue;
        }
        xs.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let mut i = 0;
        while i + 1 < xs.len() {
            let a = xs[i].round() as i32;
            let b = xs[i + 1].round() as i32;
            fill_rect_impl(data, w, h, a, yy, b - a + 1, 1, color);
            i += 2;
        }
    }
}

pub fn round_rect_impl(data: &mut [u8], w: i32, h: i32, x: i32, y: i32, rw: i32, rh: i32, radius: i32, color: u32) {
    if rw <= 0 || rh <= 0 {
        return;
    }
    let r = radius.min(rw / 2).min(rh / 2).max(0);
    if r == 0 {
        fill_rect_impl(data, w, h, x, y, rw, rh, color);
        return;
    }
    for row in 0..rh {
        let yy = y + row;
        let inset = if row < r {
            let dy = r - row;
            r - ((r * r - dy * dy).max(0) as f64).sqrt() as i32
        } else if row >= rh - r {
            let dy = row - (rh - r - 1);
            r - ((r * r - dy * dy).max(0) as f64).sqrt() as i32
        } else {
            0
        };
        fill_rect_impl(data, w, h, x + inset, yy, rw - inset * 2, 1, color);
    }
}

pub fn gradient_impl(
    data: &mut [u8],
    w: i32,
    h: i32,
    x: i32,
    y: i32,
    rw: i32,
    rh: i32,
    c1: u32,
    c2: u32,
    vertical: bool,
) {
    if rw <= 0 || rh <= 0 {
        return;
    }
    let steps = if vertical { rh } else { rw };
    if steps <= 0 {
        return;
    }
    let r1 = (c1 & 0xFF) as i32;
    let g1 = ((c1 >> 8) & 0xFF) as i32;
    let b1 = ((c1 >> 16) & 0xFF) as i32;
    let a1 = ((c1 >> 24) & 0xFF) as i32;
    let r2 = (c2 & 0xFF) as i32;
    let g2 = ((c2 >> 8) & 0xFF) as i32;
    let b2 = ((c2 >> 16) & 0xFF) as i32;
    let a2 = ((c2 >> 24) & 0xFF) as i32;
    for i in 0..steps {
        let t = if steps <= 1 { 0.0 } else { i as f64 / (steps - 1) as f64 };
        let r = (r1 as f64 + (r2 - r1) as f64 * t) as u8;
        let g = (g1 as f64 + (g2 - g1) as f64 * t) as u8;
        let b = (b1 as f64 + (b2 - b1) as f64 * t) as u8;
        let a = (a1 as f64 + (a2 - a1) as f64 * t) as u8;
        let color = ((a as u32) << 24) | ((b as u32) << 16) | ((g as u32) << 8) | (r as u32);
        if vertical {
            fill_rect_impl(data, w, h, x, y + i, rw, 1, color);
        } else {
            fill_rect_impl(data, w, h, x + i, y, 1, rh, color);
        }
    }
}

pub fn stroke_polyline_impl(
    data: &mut [u8],
    w: i32,
    h: i32,
    pts: &[(i32, i32)],
    color: u32,
    width: i32,
    closed: bool,
) {
    if pts.len() < 2 {
        return;
    }
    for i in 0..pts.len() - 1 {
        line_impl(data, w, h, pts[i].0, pts[i].1, pts[i + 1].0, pts[i + 1].1, color, width);
    }
    if closed {
        let last = pts[pts.len() - 1];
        let first = pts[0];
        line_impl(data, w, h, last.0, last.1, first.0, first.1, color, width);
    }
}

/// 直接用 (地址, 长度) 写内存：Python 侧用 ctypes 取到帧缓冲地址后传进来。
/// 这样不依赖 pyo3 的 buffer 模块（0.30 在 abi3 下不再提供缓冲协议绑定）。
fn with_pixels<R>(addr: u64, len: usize, f: impl FnOnce(&mut [u8]) -> R) -> PyResult<R> {
    if addr == 0 || len == 0 {
        return Err(PyRuntimeError::new_err("帧缓冲地址/长度无效"));
    }
    let data = unsafe { std::slice::from_raw_parts_mut(addr as *mut u8, len) };
    Ok(f(data))
}

// ---------------------------------------------------------------------------
// Canvas：Python 侧每帧创建一次，复用同一帧缓冲
// ---------------------------------------------------------------------------

#[pyclass(module = "Vapor._core", name = "Canvas")]
pub struct Canvas {
    addr: u64,
    len: usize,
    width: i32,
    height: i32,
}

#[pymethods]
impl Canvas {
    #[new]
    fn new(addr: u64, len: usize, width: i32, height: i32) -> PyResult<Self> {
        if width <= 0 || height <= 0 {
            return Err(PyValueError::new_err("Canvas 宽高必须为正"));
        }
        let need = (width as usize) * (height as usize) * 4;
        if len < need {
            return Err(PyValueError::new_err(format!(
                "帧缓冲太小：需要 {need} 字节，实际 {len} 字节"
            )));
        }
        Ok(Canvas { addr, len, width, height })
    }

    #[getter]
    fn width(&self) -> i32 {
        self.width
    }

    #[getter]
    fn height(&self) -> i32 {
        self.height
    }

    fn size(&self) -> (i32, i32) {
        (self.width, self.height)
    }

    fn clear(&self, color: u32) -> PyResult<()> {
        let (w, h) = (self.width, self.height);
        with_pixels(self.addr, self.len, |data| {
            fill_rect_impl(data, w, h, 0, 0, w, h, color)
        })
    }

    #[pyo3(signature = (x, y, w, h, color))]
    fn fill_rect(&self, x: i32, y: i32, w: i32, h: i32, color: u32) -> PyResult<()> {
        let (cw, ch) = (self.width, self.height);
        with_pixels(self.addr, self.len, |data| fill_rect_impl(data, cw, ch, x, y, w, h, color))
    }

    fn rect(&self, x: i32, y: i32, w: i32, h: i32, color: u32) -> PyResult<()> {
        self.fill_rect(x, y, w, h, color)
    }

    fn pixel(&self, x: i32, y: i32, color: u32) -> PyResult<()> {
        let (w, h) = (self.width, self.height);
        if x < 0 || y < 0 || x >= w || y >= h {
            return Ok(());
        }
        with_pixels(self.addr, self.len, |data| put(data, w, x, y, color))
    }

    fn get_pixel(&self, x: i32, y: i32) -> PyResult<u32> {
        let (w, h) = (self.width, self.height);
        if x < 0 || y < 0 || x >= w || y >= h {
            return Ok(0);
        }
        with_pixels(self.addr, self.len, |data| {
            let i = idx(x, y, w);
            (data[i] as u32) | ((data[i + 1] as u32) << 8) | ((data[i + 2] as u32) << 16) | ((data[i + 3] as u32) << 24)
        })
    }

    #[pyo3(signature = (x0, y0, x1, y1, color, width=1))]
    fn line(&self, x0: i32, y0: i32, x1: i32, y1: i32, color: u32, width: i32) -> PyResult<()> {
        let (w, h) = (self.width, self.height);
        with_pixels(self.addr, self.len, |data| line_impl(data, w, h, x0, y0, x1, y1, color, width))
    }

    fn hline(&self, x: i32, y: i32, w: i32, color: u32) -> PyResult<()> {
        self.fill_rect(x, y, w, 1, color)
    }

    fn vline(&self, x: i32, y: i32, h: i32, color: u32) -> PyResult<()> {
        self.fill_rect(x, y, 1, h, color)
    }

    fn disc(&self, cx: i32, cy: i32, r: i32, color: u32) -> PyResult<()> {
        let (w, h) = (self.width, self.height);
        with_pixels(self.addr, self.len, |data| disc_impl(data, w, h, cx, cy, r, color))
    }

    #[pyo3(signature = (cx, cy, r, color, width=1))]
    fn ring(&self, cx: i32, cy: i32, r: i32, color: u32, width: i32) -> PyResult<()> {
        let (w, h) = (self.width, self.height);
        with_pixels(self.addr, self.len, |data| ring_impl(data, w, h, cx, cy, r, width, color))
    }

    #[pyo3(signature = (cx, cy, r, color, width=1))]
    fn circle(&self, cx: i32, cy: i32, r: i32, color: u32, width: i32) -> PyResult<()> {
        self.ring(cx, cy, r, color, width)
    }

    fn polygon(&self, points: Vec<(i32, i32)>, color: u32) -> PyResult<()> {
        let (w, h) = (self.width, self.height);
        with_pixels(self.addr, self.len, |data| polygon_impl(data, w, h, &points, color))
    }

    #[pyo3(signature = (points, color, width=1, closed=false))]
    fn polyline(&self, points: Vec<(i32, i32)>, color: u32, width: i32, closed: bool) -> PyResult<()> {
        let (w, h) = (self.width, self.height);
        with_pixels(self.addr, self.len, |data| {
            stroke_polyline_impl(data, w, h, &points, color, width, closed)
        })
    }

    #[pyo3(signature = (x, y, w, h, radius, color))]
    fn round_rect(&self, x: i32, y: i32, w: i32, h: i32, radius: i32, color: u32) -> PyResult<()> {
        let (cw, ch) = (self.width, self.height);
        with_pixels(self.addr, self.len, |data| {
            round_rect_impl(data, cw, ch, x, y, w, h, radius, color)
        })
    }

    #[pyo3(signature = (x, y, w, h, c1, c2, vertical=false))]
    fn gradient(&self, x: i32, y: i32, w: i32, h: i32, c1: u32, c2: u32, vertical: bool) -> PyResult<()> {
        let (cw, ch) = (self.width, self.height);
        with_pixels(self.addr, self.len, |data| {
            gradient_impl(data, cw, ch, x, y, w, h, c1, c2, vertical)
        })
    }

    #[pyo3(signature = (src_addr, src_len, sw, sh, dx, dy, alpha=255))]
    #[allow(clippy::too_many_arguments)]
    fn blit(
        &self,
        src_addr: u64,
        src_len: usize,
        sw: i32,
        sh: i32,
        dx: i32,
        dy: i32,
        alpha: u8,
    ) -> PyResult<()> {
        let need = (sw as usize) * (sh as usize) * 4;
        if src_addr == 0 || src_len < need {
            return Err(PyValueError::new_err("源缓冲为空或太小"));
        }
        let copy: Vec<u8> =
            unsafe { std::slice::from_raw_parts(src_addr as *const u8, need) }.to_vec();
        let (w, h) = (self.width, self.height);
        with_pixels(self.addr, self.len, |data| {
            for y in 0..sh {
                let ty = dy + y;
                if ty < 0 || ty >= h {
                    continue;
                }
                for x in 0..sw {
                    let tx = dx + x;
                    if tx < 0 || tx >= w {
                        continue;
                    }
                    let si = ((y as usize) * (sw as usize) + (x as usize)) * 4;
                    let a = ((copy[si + 3] as u32 * alpha as u32) / 255) as u8;
                    if a == 0 {
                        continue;
                    }
                    blend_px(data, w, tx, ty, copy[si], copy[si + 1], copy[si + 2], a);
                }
            }
        })
    }
}

// ---------------------------------------------------------------------------
// 独立函数版本（无 Canvas 对象，直接传缓冲）
// ---------------------------------------------------------------------------

#[pyfunction]
fn rgb(r: u8, g: u8, b: u8, a: u8) -> u32 {
    ((a as u32) << 24) | ((b as u32) << 16) | ((g as u32) << 8) | (r as u32)
}

#[pyfunction]
fn parse_color(value: &str) -> PyResult<u32> {
    // 支持 "#rgb" / "#rrggbb" / "#aarrggbb"
    let s = value.trim().trim_start_matches('#');
    let parse = |s: &str| -> PyResult<u32> {
        u32::from_str_radix(s, 16).map_err(|_| PyValueError::new_err(format!("非法颜色: {value}")))
    };
    match s.len() {
        3 => {
            let v = parse(s)?;
            let r = ((v >> 8) & 0xF) as u32;
            let g = ((v >> 4) & 0xF) as u32;
            let b = (v & 0xF) as u32;
            let (r, g, b) = (r * 17, g * 17, b * 17);
            Ok((255 << 24) | (b << 16) | (g << 8) | r)
        }
        6 => {
            let v = parse(s)?;
            let r = v >> 16 & 0xFF;
            let g = v >> 8 & 0xFF;
            let b = v & 0xFF;
            Ok((255 << 24) | (b << 16) | (g << 8) | r)
        }
        8 => {
            // #AARRGGBB（CSS 风格）-> 内部 0xAABBGGRR
            let v = parse(s)?;
            let a = (v >> 24) & 0xFF;
            let r = (v >> 16) & 0xFF;
            let g = (v >> 8) & 0xFF;
            let b = v & 0xFF;
            Ok((a << 24) | (b << 16) | (g << 8) | r)
        }
        _ => Err(PyValueError::new_err(format!("非法颜色: {value}"))),
    }
}

#[pyfunction]
fn blend(c1: u32, c2: u32, t: f64) -> u32 {
    let t = t.clamp(0.0, 1.0);
    let mix = |a: u32, b: u32| -> u32 {
        let av = (a & 0xFF) as f64;
        let bv = (b & 0xFF) as f64;
        (av + (bv - av) * t) as u32
    };
    (mix(c1 >> 24 & 0xFF, c2 >> 24 & 0xFF) << 24)
        | (mix(c1 >> 16 & 0xFF, c2 >> 16 & 0xFF) << 16)
        | (mix(c1 >> 8 & 0xFF, c2 >> 8 & 0xFF) << 8)
        | mix(c1 & 0xFF, c2 & 0xFF)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Canvas>()?;
    m.add_function(wrap_pyfunction!(rgb, m)?)?;
    m.add_function(wrap_pyfunction!(parse_color, m)?)?;
    m.add_function(wrap_pyfunction!(blend, m)?)?;
    Ok(())
}