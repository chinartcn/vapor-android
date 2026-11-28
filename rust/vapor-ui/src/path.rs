//! SVG path 解析与扁平化。
//!
//! 支持全部路径指令：M/m L/l H/h V/v C/c S/s Q/q T/t A/a Z/z。
//! 输出折线（polylines），供 Vapor._core 的 Canvas 直接填充/描边。

pub struct Polyline {
    pub points: Vec<(f64, f64)>,
    pub closed: bool,
}

struct Parser<'a> {
    b: &'a [u8],
    i: usize,
}

impl<'a> Parser<'a> {
    fn new(s: &'a str) -> Self {
        Parser { b: s.as_bytes(), i: 0 }
    }

    #[inline]
    fn eof(&self) -> bool {
        self.i >= self.b.len()
    }

    fn skip_sep(&mut self) {
        while self.i < self.b.len() {
            let c = self.b[self.i];
            if c == b' ' || c == b'\t' || c == b'\n' || c == b'\r' || c == b',' {
                self.i += 1;
            } else {
                break;
            }
        }
    }

    fn is_alpha(&mut self) -> bool {
        self.skip_sep();
        if self.eof() {
            return false;
        }
        self.b[self.i].is_ascii_alphabetic()
    }

    fn is_number_start(&mut self) -> bool {
        self.skip_sep();
        if self.eof() {
            return false;
        }
        let c = self.b[self.i];
        c.is_ascii_digit() || c == b'-' || c == b'+' || c == b'.'
    }

    fn next_cmd(&mut self) -> Option<char> {
        self.skip_sep();
        if self.eof() {
            return None;
        }
        let c = self.b[self.i];
        if c.is_ascii_alphabetic() {
            self.i += 1;
            Some(c as char)
        } else {
            None
        }
    }

    fn number(&mut self) -> Option<f64> {
        self.skip_sep();
        if self.eof() {
            return None;
        }
        let start = self.i;
        if self.b[self.i] == b'+' || self.b[self.i] == b'-' {
            self.i += 1;
        }
        let mut digits = false;
        while self.i < self.b.len() && self.b[self.i].is_ascii_digit() {
            self.i += 1;
            digits = true;
        }
        if self.i < self.b.len() && self.b[self.i] == b'.' {
            self.i += 1;
            while self.i < self.b.len() && self.b[self.i].is_ascii_digit() {
                self.i += 1;
                digits = true;
            }
        }
        if !digits {
            self.i = start;
            return None;
        }
        if self.i < self.b.len() && (self.b[self.i] == b'e' || self.b[self.i] == b'E') {
            let save = self.i;
            self.i += 1;
            if self.i < self.b.len() && (self.b[self.i] == b'+' || self.b[self.i] == b'-') {
                self.i += 1;
            }
            let mut exp_digits = false;
            while self.i < self.b.len() && self.b[self.i].is_ascii_digit() {
                self.i += 1;
                exp_digits = true;
            }
            if !exp_digits {
                self.i = save;
            }
        }
        let s = std::str::from_utf8(&self.b[start..self.i]).ok()?;
        s.parse::<f64>().ok()
    }

    /// 圆弧的 large-arc-flag / sweep-flag 可以紧邻（"A 10 10 0 0110 10"）。
    fn flag(&mut self) -> Option<bool> {
        self.skip_sep();
        if self.eof() {
            return None;
        }
        match self.b[self.i] {
            b'0' => {
                self.i += 1;
                Some(false)
            }
            b'1' => {
                self.i += 1;
                Some(true)
            }
            _ => None,
        }
    }
}

#[inline]
fn dist(a: (f64, f64), b: (f64, f64)) -> f64 {
    ((a.0 - b.0).powi(2) + (a.1 - b.1).powi(2)).sqrt()
}

fn cubic_at(p0: (f64, f64), p1: (f64, f64), p2: (f64, f64), p3: (f64, f64), t: f64) -> (f64, f64) {
    let mt = 1.0 - t;
    let a = mt * mt * mt;
    let b = 3.0 * mt * mt * t;
    let c = 3.0 * mt * t * t;
    let d = t * t * t;
    (
        a * p0.0 + b * p1.0 + c * p2.0 + d * p3.0,
        a * p0.1 + b * p1.1 + c * p2.1 + d * p3.1,
    )
}

fn quad_at(p0: (f64, f64), p1: (f64, f64), p2: (f64, f64), t: f64) -> (f64, f64) {
    let mt = 1.0 - t;
    (
        mt * mt * p0.0 + 2.0 * mt * t * p1.0 + t * t * p2.0,
        mt * mt * p0.1 + 2.0 * mt * t * p1.1 + t * t * p2.1,
    )
}

fn flatten_cubic(
    p0: (f64, f64),
    p1: (f64, f64),
    p2: (f64, f64),
    p3: (f64, f64),
    tol: f64,
    out: &mut Vec<(f64, f64)>,
) {
    let len = dist(p0, p1) + dist(p1, p2) + dist(p2, p3);
    let steps = ((len / tol.max(0.25)) as usize).clamp(4, 128);
    for i in 1..=steps {
        let t = i as f64 / steps as f64;
        out.push(cubic_at(p0, p1, p2, p3, t));
    }
}

fn flatten_quad(
    p0: (f64, f64),
    p1: (f64, f64),
    p2: (f64, f64),
    tol: f64,
    out: &mut Vec<(f64, f64)>,
) {
    let len = dist(p0, p1) + dist(p1, p2);
    let steps = ((len / tol.max(0.25)) as usize).clamp(4, 96);
    for i in 1..=steps {
        let t = i as f64 / steps as f64;
        out.push(quad_at(p0, p1, p2, t));
    }
}

/// SVG 椭圆弧（端点参数化 -> 中心参数化），按 SVG 规范 F.6.5/F.6.6 实现。
#[allow(clippy::too_many_arguments)]
fn flatten_arc(
    p0: (f64, f64),
    mut rx: f64,
    mut ry: f64,
    x_rot_deg: f64,
    large_arc: bool,
    sweep: bool,
    p1: (f64, f64),
    tol: f64,
    out: &mut Vec<(f64, f64)>,
) {
    if (p0.0 - p1.0).abs() < 1e-9 && (p0.1 - p1.1).abs() < 1e-9 {
        return;
    }
    rx = rx.abs();
    ry = ry.abs();
    if rx < 1e-9 || ry < 1e-9 {
        out.push(p1);
        return;
    }
    let phi = x_rot_deg.to_radians();
    let (sin_phi, cos_phi) = phi.sin_cos();
    let dx2 = (p0.0 - p1.0) / 2.0;
    let dy2 = (p0.1 - p1.1) / 2.0;
    let x1p = cos_phi * dx2 + sin_phi * dy2;
    let y1p = -sin_phi * dx2 + cos_phi * dy2;

    let lambda = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry);
    if lambda > 1.0 {
        let s = lambda.sqrt();
        rx *= s;
        ry *= s;
    }

    let rx2 = rx * rx;
    let ry2 = ry * ry;
    let num = (rx2 * ry2 - rx2 * y1p * y1p - ry2 * x1p * x1p).max(0.0);
    let den = rx2 * y1p * y1p + ry2 * x1p * x1p;
    let mut coef = if den <= 1e-12 { 0.0 } else { (num / den).sqrt() };
    if large_arc == sweep {
        coef = -coef;
    }
    let cxp = coef * rx * y1p / ry;
    let cyp = -coef * ry * x1p / rx;
    let cx = cos_phi * cxp - sin_phi * cyp + (p0.0 + p1.0) / 2.0;
    let cy = sin_phi * cxp + cos_phi * cyp + (p0.1 + p1.1) / 2.0;

    let ux = (x1p - cxp) / rx;
    let uy = (y1p - cyp) / ry;
    let vx = (-x1p - cxp) / rx;
    let vy = (-y1p - cyp) / ry;
    let theta1 = uy.atan2(ux);
    let mut delta = (ux * vy - uy * vx).atan2(ux * vx + uy * vy);
    if !sweep && delta > 0.0 {
        delta -= std::f64::consts::TAU;
    } else if sweep && delta < 0.0 {
        delta += std::f64::consts::TAU;
    }

    let max_r = rx.max(ry);
    let steps = ((delta.abs() * max_r / tol.max(0.25)) as usize).clamp(4, 256);
    for i in 1..=steps {
        let t = theta1 + delta * (i as f64 / steps as f64);
        let (st, ct) = t.sin_cos();
        let x = cx + rx * cos_phi * ct - ry * sin_phi * st;
        let y = cy + rx * sin_phi * ct + ry * cos_phi * st;
        out.push((x, y));
    }
}

fn flush(out: &mut Vec<Polyline>, poly: &mut Vec<(f64, f64)>, closed: bool) {
    if poly.len() >= 2 {
        out.push(Polyline {
            points: std::mem::take(poly),
            closed,
        });
    } else {
        poly.clear();
    }
}

pub fn parse(d: &str, tol: f64) -> Vec<Polyline> {
    let mut p = Parser::new(d);
    let mut out: Vec<Polyline> = Vec::new();
    let mut poly: Vec<(f64, f64)> = Vec::new();
    let mut cur = (0.0f64, 0.0f64);
    let mut start = (0.0f64, 0.0f64);
    let mut last_cubic_ctrl: Option<(f64, f64)> = None;
    let mut last_quad_ctrl: Option<(f64, f64)> = None;
    let mut cmd: Option<char> = None;

    loop {
        if let Some(c) = p.next_cmd() {
            cmd = Some(c);
        } else if p.is_number_start() {
            if cmd.is_none() {
                break;
            }
            // 隐式重复上一条命令：M/m 之后按 L/l 处理
            cmd = match cmd {
                Some('M') => Some('L'),
                Some('m') => Some('l'),
                other => other,
            };
        } else {
            break;
        }
        let c = match cmd {
            Some(c) => c,
            None => break,
        };

        match c {
            'M' | 'm' => {
                let rel = c == 'm';
                let x = match p.number() { Some(v) => v, None => break };
                let y = match p.number() { Some(v) => v, None => break };
                let pt = if rel { (cur.0 + x, cur.1 + y) } else { (x, y) };
                flush(&mut out, &mut poly, false);
                poly.push(pt);
                cur = pt;
                start = pt;
                last_cubic_ctrl = None;
                last_quad_ctrl = None;
                cmd = Some(if rel { 'l' } else { 'L' });
            }
            'L' | 'l' => {
                let rel = c == 'l';
                let Some(x) = p.number() else { break };
                let Some(y) = p.number() else { break };
                let pt = if rel { (cur.0 + x, cur.1 + y) } else { (x, y) };
                if poly.is_empty() {
                    poly.push(cur);
                }
                poly.push(pt);
                cur = pt;
                last_cubic_ctrl = None;
                last_quad_ctrl = None;
            }
            'H' | 'h' => {
                let rel = c == 'h';
                let Some(x) = p.number() else { break };
                let pt = if rel { (cur.0 + x, cur.1) } else { (x, cur.1) };
                if poly.is_empty() {
                    poly.push(cur);
                }
                poly.push(pt);
                cur = pt;
                last_cubic_ctrl = None;
                last_quad_ctrl = None;
            }
            'V' | 'v' => {
                let rel = c == 'v';
                let Some(y) = p.number() else { break };
                let pt = if rel { (cur.0, cur.1 + y) } else { (cur.0, y) };
                if poly.is_empty() {
                    poly.push(cur);
                }
                poly.push(pt);
                cur = pt;
                last_cubic_ctrl = None;
                last_quad_ctrl = None;
            }
            'C' | 'c' => {
                let rel = c == 'c';
                let mut vals = [0.0f64; 6];
                let mut ok = true;
                for v in vals.iter_mut() {
                    match p.number() {
                        Some(x) => *v = x,
                        None => {
                            ok = false;
                            break;
                        }
                    }
                }
                if !ok {
                    break;
                }
                let off = if rel { cur } else { (0.0, 0.0) };
                let p1 = (off.0 + vals[0], off.1 + vals[1]);
                let p2 = (off.0 + vals[2], off.1 + vals[3]);
                let p3 = (off.0 + vals[4], off.1 + vals[5]);
                if poly.is_empty() {
                    poly.push(cur);
                }
                flatten_cubic(cur, p1, p2, p3, tol, &mut poly);
                cur = p3;
                last_cubic_ctrl = Some(p2);
                last_quad_ctrl = None;
            }
            'S' | 's' => {
                let rel = c == 's';
                let mut vals = [0.0f64; 4];
                let mut ok = true;
                for v in vals.iter_mut() {
                    match p.number() {
                        Some(x) => *v = x,
                        None => {
                            ok = false;
                            break;
                        }
                    }
                }
                if !ok {
                    break;
                }
                let off = if rel { cur } else { (0.0, 0.0) };
                let p1 = match last_cubic_ctrl {
                    Some(cp) => (2.0 * cur.0 - cp.0, 2.0 * cur.1 - cp.1),
                    None => cur,
                };
                let p2 = (off.0 + vals[0], off.1 + vals[1]);
                let p3 = (off.0 + vals[2], off.1 + vals[3]);
                if poly.is_empty() {
                    poly.push(cur);
                }
                flatten_cubic(cur, p1, p2, p3, tol, &mut poly);
                cur = p3;
                last_cubic_ctrl = Some(p2);
                last_quad_ctrl = None;
            }
            'Q' | 'q' => {
                let rel = c == 'q';
                let mut vals = [0.0f64; 4];
                let mut ok = true;
                for v in vals.iter_mut() {
                    match p.number() {
                        Some(x) => *v = x,
                        None => {
                            ok = false;
                            break;
                        }
                    }
                }
                if !ok {
                    break;
                }
                let off = if rel { cur } else { (0.0, 0.0) };
                let p1 = (off.0 + vals[0], off.1 + vals[1]);
                let p2 = (off.0 + vals[2], off.1 + vals[3]);
                if poly.is_empty() {
                    poly.push(cur);
                }
                flatten_quad(cur, p1, p2, tol, &mut poly);
                cur = p2;
                last_quad_ctrl = Some(p1);
                last_cubic_ctrl = None;
            }
            'T' | 't' => {
                let rel = c == 't';
                let Some(x) = p.number() else { break };
                let Some(y) = p.number() else { break };
                let off = if rel { cur } else { (0.0, 0.0) };
                let p2 = (off.0 + x, off.1 + y);
                let p1 = match last_quad_ctrl {
                    Some(cp) => (2.0 * cur.0 - cp.0, 2.0 * cur.1 - cp.1),
                    None => cur,
                };
                if poly.is_empty() {
                    poly.push(cur);
                }
                flatten_quad(cur, p1, p2, tol, &mut poly);
                cur = p2;
                last_quad_ctrl = Some(p1);
                last_cubic_ctrl = None;
            }
            'A' | 'a' => {
                let rel = c == 'a';
                let Some(rx) = p.number() else { break };
                let Some(ry) = p.number() else { break };
                let Some(rot) = p.number() else { break };
                let Some(large) = p.flag() else { break };
                let Some(sweep) = p.flag() else { break };
                let Some(x) = p.number() else { break };
                let Some(y) = p.number() else { break };
                let pt = if rel { (cur.0 + x, cur.1 + y) } else { (x, y) };
                if poly.is_empty() {
                    poly.push(cur);
                }
                flatten_arc(cur, rx, ry, rot, large, sweep, pt, tol, &mut poly);
                cur = pt;
                last_cubic_ctrl = None;
                last_quad_ctrl = None;
            }
            'Z' | 'z' => {
                if !poly.is_empty() {
                    poly.push(start);
                    flush(&mut out, &mut poly, true);
                }
                cur = start;
                last_cubic_ctrl = None;
                last_quad_ctrl = None;
            }
            _ => break,
        }
    }
    flush(&mut out, &mut poly, false);
    out
}

pub fn bounds(paths: &[Polyline]) -> (f64, f64, f64, f64) {
    let mut minx = f64::INFINITY;
    let mut miny = f64::INFINITY;
    let mut maxx = f64::NEG_INFINITY;
    let mut maxy = f64::NEG_INFINITY;
    for pl in paths {
        for &(x, y) in &pl.points {
            if x < minx {
                minx = x;
            }
            if y < miny {
                miny = y;
            }
            if x > maxx {
                maxx = x;
            }
            if y > maxy {
                maxy = y;
            }
        }
    }
    if !minx.is_finite() {
        return (0.0, 0.0, 0.0, 0.0);
    }
    (minx, miny, maxx, maxy)
}
