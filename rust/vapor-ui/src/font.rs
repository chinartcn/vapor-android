//! 字体光栅化：TTF / OTF / TTC 直接渲染到 RGBA8888 帧缓冲。
//!
//! 为什么用 ttf-parser 而不是 fontdue：Android 自带的 NotoSansCJK 是 **CFF 轮廓**
//! 的 OTF/TTC，fontdue 只支持 glyf 轮廓，会直接解析失败。ttf-parser 同时支持
//! glyf 与 CFF/CFF2，我们只用它取轮廓，自己做一个 3× 超采样的扫描线填充。
//!
//! 设计：
//!   * Face 持有字体字节 + Face<'static>（数据在 Box 里，地址稳定）；
//!   * 字形按 (字符, 字号) 缓存覆盖率位图（已 3× 降采样）；
//!   * FontStack 按字符回退：每个字符挑第一个有字形的字体；
//!   * 绘制是逐像素 α 混合，直接写帧缓冲。

use pyo3::exceptions::{PyIOError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use ttf_parser::{Face as TtFace, GlyphId, OutlineBuilder};

/// 超采样倍数：3×3 每个像素，降采样后得到覆盖率（够用的抗锯齿）。
const SS: usize = 3;

struct Glyph {
    advance: f32,
    mask: Vec<u8>,
    width: usize,
    height: usize,
    left: i32,
    top: i32,
}

pub struct Face {
    // 注意：data 必须在 face 之前声明？Rust 按声明顺序析构，face 的 Drop 是平凡的、
    // 不会解引用 data，所以这里顺序无关；真正的约束是「data 不能被移动」，
    // 用 Box<[u8]> 保证堆地址稳定。
    /// 只为保活：face 借用这块内存，不能释放也不能移动。
    #[allow(dead_code)]
    data: Box<[u8]>,
    face: TtFace<'static>,
    units_per_em: f32,
    ascent: f32,
    descent: f32,
    line_gap: f32,
    index: u32,
    cache: Mutex<HashMap<u64, Arc<Glyph>>>,
}

#[inline]
fn size_key(px: f32) -> u64 {
    (px.max(1.0) * 10.0).round() as u64
}

impl Face {
    fn load(bytes: &[u8], index: u32) -> Result<Face, String> {
        let data: Box<[u8]> = bytes.to_vec().into_boxed_slice();
        let ptr: *const [u8] = &*data;
        // SAFETY: data 是 Box<[u8]>，堆地址在移动 Box 时不变；face 的生命周期被
        // 人为延长到 'static，但只在本结构体内使用，结构体活着 data 就活着。
        let parsed = unsafe { TtFace::parse(&*ptr, index) }.map_err(|e| format!("{e:?}"))?;
        let face: TtFace<'static> = unsafe { std::mem::transmute(parsed) };
        let units_per_em = face.units_per_em() as f32;
        if units_per_em <= 0.0 {
            return Err("units_per_em 非法".to_string());
        }
        Ok(Face {
            data,
            face,
            units_per_em,
            ascent: face.ascender() as f32,
            descent: face.descender() as f32,
            line_gap: face.line_gap() as f32,
            index,
            cache: Mutex::new(HashMap::new()),
        })
    }

    #[inline]
    fn scale(&self, px: f32) -> f32 {
        px / self.units_per_em
    }

    fn has_glyph(&self, ch: char) -> bool {
        if ch == ' ' || ch == '\t' || ch == '\n' {
            return true;
        }
        self.face.glyph_index(ch).is_some()
    }

    fn advance_of(&self, ch: char, px: f32) -> f32 {
        if ch == '\n' {
            return 0.0;
        }
        match self.face.glyph_index(ch) {
            Some(gid) => self.face.glyph_hor_advance(gid).unwrap_or(0) as f32 * self.scale(px),
            None => px * 0.5,
        }
    }

    fn ascent_size(&self, px: f32) -> f32 {
        self.ascent * self.scale(px)
    }

    fn descent_size(&self, px: f32) -> f32 {
        self.descent * self.scale(px)
    }

    fn line_height_size(&self, px: f32) -> f32 {
        (self.ascent - self.descent + self.line_gap) * self.scale(px)
    }

    fn glyph(&self, ch: char, px: f32) -> Option<Arc<Glyph>> {
        let gid = self.face.glyph_index(ch)?;
        let key = ((ch as u64) << 20) | (size_key(px) & 0xF_FFFF);
        if let Ok(mut guard) = self.cache.lock() {
            if let Some(v) = guard.get(&key) {
                return Some(v.clone());
            }
            let v = Arc::new(self.rasterize_glyph(gid, px));
            guard.insert(key, v.clone());
            return Some(v);
        }
        Some(Arc::new(self.rasterize_glyph(gid, px)))
    }

    fn rasterize_glyph(&self, gid: GlyphId, px: f32) -> Glyph {
        let s = self.scale(px);
        let advance = self.face.glyph_hor_advance(gid).unwrap_or(0) as f32 * s;

        let mut fl = Flattener::new();
        let _ = self.face.outline_glyph(gid, &mut fl);
        fl.flush_contour();
        if fl.contours.is_empty() {
            return Glyph {
                advance,
                mask: Vec::new(),
                width: 0,
                height: 0,
                left: 0,
                top: 0,
            };
        }

        let ss = SS as f32;
        let mut polys: Vec<Vec<(f32, f32)>> = Vec::with_capacity(fl.contours.len());
        let mut minx = f32::MAX;
        let mut miny = f32::MAX;
        let mut maxx = f32::MIN;
        let mut maxy = f32::MIN;
        for contour in &fl.contours {
            let mut poly = Vec::with_capacity(contour.len());
            for &(x, y) in contour {
                // 转设备坐标：缩放 + y 轴翻转（字体 y 向上，屏幕 y 向下）
                let dx = x * s * ss;
                let dy = -y * s * ss;
                if dx < minx {
                    minx = dx;
                }
                if dx > maxx {
                    maxx = dx;
                }
                if dy < miny {
                    miny = dy;
                }
                if dy > maxy {
                    maxy = dy;
                }
                poly.push((dx, dy));
            }
            polys.push(poly);
        }

        let x0 = minx.floor() as i32;
        let y0 = miny.floor() as i32;
        let w = ((maxx.ceil() as i32 - x0).max(1) as usize) + 1;
        let h = ((maxy.ceil() as i32 - y0).max(1) as usize) + 1;

        let mut hi = vec![0u8; w * h];
        fill_nonzero(&polys, &mut hi, w, h, x0, y0);

        // 3× 降采样 -> 覆盖率
        let ow = (w + SS - 1) / SS;
        let oh = (h + SS - 1) / SS;
        let mut mask = vec![0u8; ow * oh];
        let total = (SS * SS) as u32;
        for oy in 0..oh {
            for ox in 0..ow {
                let mut sum = 0u32;
                for sy in 0..SS {
                    let y = oy * SS + sy;
                    if y >= h {
                        break;
                    }
                    for sx in 0..SS {
                        let x = ox * SS + sx;
                        if x >= w {
                            break;
                        }
                        if hi[y * w + x] != 0 {
                            sum += 255;
                        }
                    }
                }
                mask[oy * ow + ox] = (sum / total) as u8;
            }
        }

        Glyph {
            advance,
            mask,
            width: ow,
            height: oh,
            left: (x0 as f32 / ss).floor() as i32,
            top: (y0 as f32 / ss).floor() as i32,
        }
    }
}

/// 非零环绕规则填充（CJK 字形的重叠轮廓必须用 nonzero，even-odd 会挖错洞）。
fn fill_nonzero(polys: &[Vec<(f32, f32)>], mask: &mut [u8], w: usize, h: usize, x0: i32, y0: i32) {
    let mut xs: Vec<(f32, i32)> = Vec::with_capacity(16);
    for yy in 0..h {
        let scan = y0 as f32 + yy as f32 + 0.5;
        xs.clear();
        for poly in polys {
            let n = poly.len();
            if n < 2 {
                continue;
            }
            for i in 0..n {
                let (x1, y1) = poly[i];
                let (x2, y2) = poly[(i + 1) % n];
                if (y1 <= scan && y2 > scan) || (y2 <= scan && y1 > scan) {
                    let t = (scan - y1) / (y2 - y1);
                    xs.push((x1 + (x2 - x1) * t, if y2 > y1 { 1 } else { -1 }));
                }
            }
        }
        if xs.len() < 2 {
            continue;
        }
        xs.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
        let mut winding = 0i32;
        let mut start = 0.0f32;
        for (x, dir) in &xs {
            let prev = winding;
            winding += *dir;
            if prev == 0 && winding != 0 {
                start = *x;
            } else if prev != 0 && winding == 0 {
                let a = ((start - x0 as f32).floor() as i32).max(0);
                let b = ((*x - x0 as f32).ceil() as i32).min(w as i32);
                for px in a..b {
                    mask[yy * w + px as usize] = 1;
                }
            }
        }
    }
}

struct Flattener {
    contours: Vec<Vec<(f32, f32)>>,
    cur: Vec<(f32, f32)>,
    x: f32,
    y: f32,
}

impl Flattener {
    fn new() -> Self {
        Flattener {
            contours: Vec::new(),
            cur: Vec::new(),
            x: 0.0,
            y: 0.0,
        }
    }

    fn flush_contour(&mut self) {
        if self.cur.len() >= 3 {
            self.contours.push(std::mem::take(&mut self.cur));
        } else {
            self.cur.clear();
        }
    }
}

impl OutlineBuilder for Flattener {
    fn move_to(&mut self, x: f32, y: f32) {
        self.flush_contour();
        self.x = x;
        self.y = y;
        self.cur.push((x, y));
    }

    fn line_to(&mut self, x: f32, y: f32) {
        if self.cur.is_empty() {
            self.cur.push((self.x, self.y));
        }
        self.cur.push((x, y));
        self.x = x;
        self.y = y;
    }

    fn quad_to(&mut self, x1: f32, y1: f32, x: f32, y: f32) {
        if self.cur.is_empty() {
            self.cur.push((self.x, self.y));
        }
        let (x0, y0) = (self.x, self.y);
        for i in 1..=10 {
            let t = i as f32 / 10.0;
            let mt = 1.0 - t;
            self.cur.push((
                mt * mt * x0 + 2.0 * mt * t * x1 + t * t * x,
                mt * mt * y0 + 2.0 * mt * t * y1 + t * t * y,
            ));
        }
        self.x = x;
        self.y = y;
    }

    fn curve_to(&mut self, x1: f32, y1: f32, x2: f32, y2: f32, x: f32, y: f32) {
        if self.cur.is_empty() {
            self.cur.push((self.x, self.y));
        }
        let (x0, y0) = (self.x, self.y);
        for i in 1..=16 {
            let t = i as f32 / 16.0;
            let mt = 1.0 - t;
            let a = mt * mt * mt;
            let b = 3.0 * mt * mt * t;
            let c = 3.0 * mt * t * t;
            let d = t * t * t;
            self.cur.push((
                a * x0 + b * x1 + c * x2 + d * x,
                a * y0 + b * y1 + c * y2 + d * y,
            ));
        }
        self.x = x;
        self.y = y;
    }

    fn close(&mut self) {
        self.flush_contour();
    }
}

fn pick<'a>(faces: &'a [Arc<Face>], ch: char) -> &'a Face {
    for f in faces {
        if f.has_glyph(ch) {
            return f.as_ref();
        }
    }
    faces[0].as_ref()
}

pub fn measure(faces: &[Arc<Face>], text: &str, px: f32) -> f32 {
    let mut w = 0.0f32;
    for ch in text.chars() {
        if ch == '\n' {
            continue;
        }
        w += pick(faces, ch).advance_of(ch, px);
    }
    w
}

#[inline]
fn blend(data: &mut [u8], w: i32, h: i32, x: i32, y: i32, color: u32, coverage: u8) {
    if coverage == 0 || x < 0 || y < 0 || x >= w || y >= h {
        return;
    }
    let i = ((y as usize) * (w as usize) + x as usize) * 4;
    if i + 3 >= data.len() {
        return;
    }
    let color_a = ((color >> 24) & 0xFF) as u32;
    let alpha = (color_a * coverage as u32) / 255;
    if alpha == 0 {
        return;
    }
    let inv = 255 - alpha;
    let r = (color & 0xFF) as u32;
    let g = ((color >> 8) & 0xFF) as u32;
    let b = ((color >> 16) & 0xFF) as u32;
    data[i] = ((r * alpha + data[i] as u32 * inv) / 255) as u8;
    data[i + 1] = ((g * alpha + data[i + 1] as u32 * inv) / 255) as u8;
    data[i + 2] = ((b * alpha + data[i + 2] as u32 * inv) / 255) as u8;
    data[i + 3] = 255;
}

/// 把一行文本画进缓冲；y 是行顶部（不是基线）。返回推进宽度。
#[allow(clippy::too_many_arguments)]
pub fn draw_line(
    faces: &[Arc<Face>],
    data: &mut [u8],
    w: i32,
    h: i32,
    text: &str,
    x: f64,
    y: f64,
    px: f32,
    color: u32,
) -> f64 {
    let ascent = faces[0].ascent_size(px);
    let baseline = (y as f32 + ascent).round() as i32;
    let mut pen = x as f32;
    for ch in text.chars() {
        if ch == '\n' {
            continue;
        }
        let face = pick(faces, ch);
        match face.glyph(ch, px) {
            Some(glyph) => {
                let gx = pen.round() as i32 + glyph.left;
                let gy = baseline + glyph.top;
                for row in 0..glyph.height {
                    for col in 0..glyph.width {
                        let a = glyph.mask[row * glyph.width + col];
                        if a > 0 {
                            blend(data, w, h, gx + col as i32, gy + row as i32, color, a);
                        }
                    }
                }
                pen += glyph.advance;
            }
            None => pen += face.advance_of(ch, px),
        }
    }
    (pen - x as f32) as f64
}

pub fn wrap(faces: &[Arc<Face>], text: &str, px: f32, max_width: f64) -> Vec<String> {
    if max_width <= 0.0 {
        return vec![text.to_string()];
    }
    let mut lines = Vec::new();
    for paragraph in text.split('\n') {
        if paragraph.is_empty() {
            lines.push(String::new());
            continue;
        }
        let mut line = String::new();
        let mut width = 0.0f64;
        let chars: Vec<char> = paragraph.chars().collect();
        let mut i = 0usize;
        while i < chars.len() {
            let c = chars[i];
            let cjk = (c as u32) >= 0x2E80;
            let mut token = String::new();
            if cjk || c == ' ' {
                token.push(c);
                i += 1;
            } else {
                while i < chars.len() {
                    let ch = chars[i];
                    if ch == ' ' || (ch as u32) >= 0x2E80 {
                        break;
                    }
                    token.push(ch);
                    i += 1;
                }
            }
            let tw = measure(faces, &token, px) as f64;
            if width > 0.0 && width + tw > max_width {
                lines.push(line.trim_end().to_string());
                line.clear();
                width = 0.0;
                if token == " " {
                    continue;
                }
            }
            line.push_str(&token);
            width += tw;
        }
        lines.push(line.trim_end().to_string());
    }
    lines
}

/// CPython 缓冲协议标志（与 CPython 头文件一致，避免依赖 pyo3-ffi 的常量导出）
const PYBUF_WRITABLE: i32 = 0x0001;
const PYBUF_C_CONTIGUOUS: i32 = 0x0020;

fn with_pixels<R>(py: Python<'_>, buf: &Py<PyAny>, f: impl FnOnce(&mut [u8]) -> R) -> PyResult<R> {
    // 直接用 CPython 缓冲协议（pyo3::ffi），不依赖 pyo3::buffer 模块（0.30 已不导出）。
    unsafe {
        let obj = buf.bind(py).as_ptr();
        let mut view: pyo3::ffi::Py_buffer = std::mem::zeroed();
        let flags = PYBUF_WRITABLE | PYBUF_C_CONTIGUOUS;
        if pyo3::ffi::PyObject_GetBuffer(obj, &mut view, flags) != 0 {
            pyo3::ffi::PyErr_Clear();
            return Err(PyRuntimeError::new_err(
                "需要可写的 C 连续缓冲区（memoryview / bytearray）",
            ));
        }
        let len = view.len.max(0) as usize;
        let ptr = view.buf as *mut u8;
        let result = if ptr.is_null() || len == 0 {
            Err(PyRuntimeError::new_err("帧缓冲为空"))
        } else {
            let data = std::slice::from_raw_parts_mut(ptr, len);
            Ok(f(data))
        };
        pyo3::ffi::PyBuffer_Release(&mut view);
        result
    }
}

// ---------------------------------------------------------------------------
// Python 侧：Font（单字体） / FontStack（按字符回退）
// ---------------------------------------------------------------------------

#[pyclass(module = "Vapor._ui", name = "Font", unsendable)]
pub struct Font {
    pub face: Arc<Face>,
}

#[pymethods]
impl Font {
    #[new]
    #[pyo3(signature = (data, collection_index=0))]
    fn new(data: &[u8], collection_index: u32) -> PyResult<Self> {
        let face = Face::load(data, collection_index)
            .map_err(|e| PyValueError::new_err(format!("字体解析失败: {e}")))?;
        Ok(Font { face: Arc::new(face) })
    }

    #[staticmethod]
    #[pyo3(signature = (path, collection_index=0))]
    fn from_path(path: &str, collection_index: u32) -> PyResult<Self> {
        let data = std::fs::read(path)
            .map_err(|e| PyIOError::new_err(format!("读取字体失败 {path}: {e}")))?;
        let face = Face::load(&data, collection_index)
            .map_err(|e| PyValueError::new_err(format!("字体解析失败 {path}: {e}")))?;
        Ok(Font { face: Arc::new(face) })
    }

    fn text_width(&self, text: &str, size: f64) -> f64 {
        measure(std::slice::from_ref(&self.face), text, size as f32) as f64
    }

    fn line_height(&self, size: f64) -> f64 {
        self.face.line_height_size(size as f32) as f64
    }

    fn ascent(&self, size: f64) -> f64 {
        self.face.ascent_size(size as f32) as f64
    }

    fn descent(&self, size: f64) -> f64 {
        self.face.descent_size(size as f32) as f64
    }

    fn has_glyph(&self, ch: &str) -> bool {
        ch.chars().next().map(|c| self.face.has_glyph(c)).unwrap_or(false)
    }

    fn coverage(&self, text: &str) -> f64 {
        let mut total = 0usize;
        let mut hit = 0usize;
        for ch in text.chars() {
            if ch.is_whitespace() {
                continue;
            }
            total += 1;
            if self.face.has_glyph(ch) {
                hit += 1;
            }
        }
        if total == 0 {
            1.0
        } else {
            hit as f64 / total as f64
        }
    }

    #[pyo3(signature = (buf, width, height, text, x, y, size, color))]
    #[allow(clippy::too_many_arguments)]
    fn draw(
        &self,
        py: Python<'_>,
        buf: Py<PyAny>,
        width: i32,
        height: i32,
        text: &str,
        x: f64,
        y: f64,
        size: f64,
        color: u32,
    ) -> PyResult<f64> {
        with_pixels(py, &buf, |data| {
            draw_line(
                std::slice::from_ref(&self.face),
                data,
                width,
                height,
                text,
                x,
                y,
                size as f32,
                color,
            )
        })
    }

    fn wrap_text(&self, text: &str, size: f64, max_width: f64) -> Vec<String> {
        wrap(std::slice::from_ref(&self.face), text, size as f32, max_width)
    }

    fn __repr__(&self) -> String {
        format!("Font(index={})", self.face.index)
    }
}

#[pyclass(module = "Vapor._ui", name = "FontStack", unsendable)]
pub struct FontStack {
    pub faces: Vec<Arc<Face>>,
}

#[pymethods]
impl FontStack {
    #[new]
    fn new(fonts: Vec<PyRef<'_, Font>>) -> PyResult<Self> {
        if fonts.is_empty() {
            return Err(PyValueError::new_err("FontStack 至少需要一个字体"));
        }
        let faces = fonts.iter().map(|f| f.face.clone()).collect();
        Ok(FontStack { faces })
    }

    fn text_width(&self, text: &str, size: f64) -> f64 {
        measure(&self.faces, text, size as f32) as f64
    }

    fn line_height(&self, size: f64) -> f64 {
        self.faces[0].line_height_size(size as f32) as f64
    }

    fn ascent(&self, size: f64) -> f64 {
        self.faces[0].ascent_size(size as f32) as f64
    }

    fn has_glyph(&self, ch: &str) -> bool {
        match ch.chars().next() {
            Some(c) => self.faces.iter().any(|f| f.has_glyph(c)),
            None => false,
        }
    }

    fn coverage(&self, text: &str) -> f64 {
        let mut total = 0usize;
        let mut hit = 0usize;
        for ch in text.chars() {
            if ch.is_whitespace() {
                continue;
            }
            total += 1;
            if self.faces.iter().any(|f| f.has_glyph(ch)) {
                hit += 1;
            }
        }
        if total == 0 {
            1.0
        } else {
            hit as f64 / total as f64
        }
    }

    #[pyo3(signature = (buf, width, height, text, x, y, size, color))]
    #[allow(clippy::too_many_arguments)]
    fn draw(
        &self,
        py: Python<'_>,
        buf: Py<PyAny>,
        width: i32,
        height: i32,
        text: &str,
        x: f64,
        y: f64,
        size: f64,
        color: u32,
    ) -> PyResult<f64> {
        with_pixels(py, &buf, |data| {
            draw_line(
                &self.faces,
                data,
                width,
                height,
                text,
                x,
                y,
                size as f32,
                color,
            )
        })
    }

    fn wrap_text(&self, text: &str, size: f64, max_width: f64) -> Vec<String> {
        wrap(&self.faces, text, size as f32, max_width)
    }

    fn __len__(&self) -> usize {
        self.faces.len()
    }

    fn __repr__(&self) -> String {
        format!("FontStack({} faces)", self.faces.len())
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Font>()?;
    m.add_class::<FontStack>()?;
    Ok(())
}