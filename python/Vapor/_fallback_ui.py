"""Vapor._ui 的纯 Python 回退实现：SVG path 扁平化 + 矩阵 + 文本度量。

接口与 Rust 版一致，用于无原生扩展时的开发与测试。
"""

from __future__ import annotations

import math

FALLBACK = True


# ---------------------------------------------------------------------------
# Mat2D
# ---------------------------------------------------------------------------

class Mat2D:
    __slots__ = ("a", "b", "c", "d", "e", "f")

    def __init__(self, a=1.0, b=0.0, c=0.0, d=1.0, e=0.0, f=0.0):
        self.a, self.b, self.c, self.d, self.e, self.f = a, b, c, d, e, f

    @staticmethod
    def identity():
        return Mat2D()

    @staticmethod
    def translate(tx, ty):
        return Mat2D(1, 0, 0, 1, tx, ty)

    @staticmethod
    def scale(sx, sy=None):
        return Mat2D(sx, 0, 0, sx if sy is None else sy, 0, 0)

    @staticmethod
    def rotate(deg):
        r = math.radians(deg)
        s, c = math.sin(r), math.cos(r)
        return Mat2D(c, s, -s, c, 0, 0)

    @staticmethod
    def skew(ax_deg, ay_deg=0.0):
        return Mat2D(1, math.tan(math.radians(ay_deg)), math.tan(math.radians(ax_deg)), 1, 0, 0)

    def mul(self, other):
        s, o = self, other
        return Mat2D(
            s.a * o.a + s.c * o.b,
            s.b * o.a + s.d * o.b,
            s.a * o.c + s.c * o.d,
            s.b * o.c + s.d * o.d,
            s.a * o.e + s.c * o.f + s.e,
            s.b * o.e + s.d * o.f + s.f,
        )

    def then(self, other):
        return other.mul(self)

    def apply(self, x, y):
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)

    def inverse(self):
        det = self.a * self.d - self.b * self.c
        if abs(det) < 1e-12:
            return Mat2D()
        inv = 1.0 / det
        return Mat2D(
            self.d * inv,
            -self.b * inv,
            -self.c * inv,
            self.a * inv,
            (self.c * self.f - self.d * self.e) * inv,
            (self.b * self.e - self.a * self.f) * inv,
        )

    def to_tuple(self):
        return (self.a, self.b, self.c, self.d, self.e, self.f)

    def __repr__(self):
        return f"Mat2D(a={self.a}, b={self.b}, c={self.c}, d={self.d}, e={self.e}, f={self.f})"


# ---------------------------------------------------------------------------
# SVG path
# ---------------------------------------------------------------------------

_SEP = " \t\r\n,"


class _Scan:
    __slots__ = ("s", "i", "n")

    def __init__(self, s):
        self.s = s
        self.i = 0
        self.n = len(s)

    def skip(self):
        while self.i < self.n and self.s[self.i] in _SEP:
            self.i += 1

    def eof(self):
        self.skip()
        return self.i >= self.n

    def is_cmd(self):
        self.skip()
        return self.i < self.n and self.s[self.i].isalpha()

    def is_number(self):
        self.skip()
        return self.i < self.n and (self.s[self.i].isdigit() or self.s[self.i] in "-+.")

    def cmd(self):
        self.skip()
        if self.i < self.n and self.s[self.i].isalpha():
            c = self.s[self.i]
            self.i += 1
            return c
        return None

    def num(self):
        self.skip()
        if self.i >= self.n:
            return None
        start = self.i
        if self.s[self.i] in "+-":
            self.i += 1
        digits = False
        while self.i < self.n and self.s[self.i].isdigit():
            self.i += 1
            digits = True
        if self.i < self.n and self.s[self.i] == ".":
            self.i += 1
            while self.i < self.n and self.s[self.i].isdigit():
                self.i += 1
                digits = True
        if not digits:
            self.i = start
            return None
        if self.i < self.n and self.s[self.i] in "eE":
            save = self.i
            self.i += 1
            if self.i < self.n and self.s[self.i] in "+-":
                self.i += 1
            exp = False
            while self.i < self.n and self.s[self.i].isdigit():
                self.i += 1
                exp = True
            if not exp:
                self.i = save
        try:
            return float(self.s[start:self.i])
        except ValueError:
            self.i = start
            return None

    def flag(self):
        self.skip()
        if self.i < self.n and self.s[self.i] in "01":
            v = self.s[self.i] == "1"
            self.i += 1
            return v
        return None


def _dist(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _cubic(p0, p1, p2, p3, t):
    mt = 1 - t
    a, b, c, d = mt * mt * mt, 3 * mt * mt * t, 3 * mt * t * t, t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def _quad(p0, p1, p2, t):
    mt = 1 - t
    return (mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0], mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1])


def _flatten_cubic(p0, p1, p2, p3, tol, out):
    steps = max(4, min(128, int((_dist(p0, p1) + _dist(p1, p2) + _dist(p2, p3)) / max(tol, 0.25))))
    for i in range(1, steps + 1):
        out.append(_cubic(p0, p1, p2, p3, i / steps))


def _flatten_quad(p0, p1, p2, tol, out):
    steps = max(4, min(96, int((_dist(p0, p1) + _dist(p1, p2)) / max(tol, 0.25))))
    for i in range(1, steps + 1):
        out.append(_quad(p0, p1, p2, i / steps))


def _flatten_arc(p0, rx, ry, rot, large, sweep, p1, tol, out):
    if abs(p0[0] - p1[0]) < 1e-9 and abs(p0[1] - p1[1]) < 1e-9:
        return
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-9 or ry < 1e-9:
        out.append(p1)
        return
    phi = math.radians(rot)
    sin_phi, cos_phi = math.sin(phi), math.cos(phi)
    dx2, dy2 = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1p = cos_phi * dx2 + sin_phi * dy2
    y1p = -sin_phi * dx2 + cos_phi * dy2
    lam = x1p * x1p / (rx * rx) + y1p * y1p / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx *= s
        ry *= s
    rx2, ry2 = rx * rx, ry * ry
    num = max(0.0, rx2 * ry2 - rx2 * y1p * y1p - ry2 * x1p * x1p)
    den = rx2 * y1p * y1p + ry2 * x1p * x1p
    coef = 0.0 if den <= 1e-12 else math.sqrt(num / den)
    if large == sweep:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cos_phi * cxp - sin_phi * cyp + (p0[0] + p1[0]) / 2
    cy = sin_phi * cxp + cos_phi * cyp + (p0[1] + p1[1]) / 2
    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    theta1 = math.atan2(uy, ux)
    delta = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    steps = max(4, min(256, int(abs(delta) * max(rx, ry) / max(tol, 0.25))))
    for i in range(1, steps + 1):
        t = theta1 + delta * (i / steps)
        st, ct = math.sin(t), math.cos(t)
        out.append((cx + rx * cos_phi * ct - ry * sin_phi * st, cy + rx * sin_phi * ct + ry * cos_phi * st))


def _flush(out, poly, closed):
    if len(poly) >= 2:
        out.append((closed, list(poly)))
    poly.clear()


def flatten_path(d, tolerance=0.3, transform=None):
    """把 SVG path 扁平化为 [(closed, [(x, y), ...]), ...]。"""
    sc = _Scan(d or "")
    out, poly = [], []
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    last_cubic = last_quad = None
    cmd = None
    while True:
        if sc.is_cmd():
            cmd = sc.cmd()
        elif sc.is_number():
            if cmd is None:
                break
            if cmd in "Mm":
                cmd = "L" if cmd == "M" else "l"
        else:
            break
        c = cmd
        rel = c.islower()
        off = cur if rel else (0.0, 0.0)

        if c in "Mm":
            x, y = sc.num(), sc.num()
            if x is None or y is None:
                break
            pt = (off[0] + x, off[1] + y) if rel else (x, y)
            _flush(out, poly, False)
            poly.append(pt)
            cur = start = pt
            last_cubic = last_quad = None
            cmd = "l" if rel else "L"
        elif c in "Ll":
            x, y = sc.num(), sc.num()
            if x is None or y is None:
                break
            pt = (off[0] + x, off[1] + y) if rel else (x, y)
            if not poly:
                poly.append(cur)
            poly.append(pt)
            cur = pt
            last_cubic = last_quad = None
        elif c in "Hh":
            x = sc.num()
            if x is None:
                break
            pt = (off[0] + x, cur[1]) if rel else (x, cur[1])
            if not poly:
                poly.append(cur)
            poly.append(pt)
            cur = pt
        elif c in "Vv":
            y = sc.num()
            if y is None:
                break
            pt = (cur[0], off[1] + y) if rel else (cur[0], y)
            if not poly:
                poly.append(cur)
            poly.append(pt)
            cur = pt
        elif c in "CcSs":
            if c in "Cc":
                vals = [sc.num() for _ in range(6)]
                if any(v is None for v in vals):
                    break
                p1 = (off[0] + vals[0], off[1] + vals[1])
                p2 = (off[0] + vals[2], off[1] + vals[3])
                p3 = (off[0] + vals[4], off[1] + vals[5])
            else:
                vals = [sc.num() for _ in range(4)]
                if any(v is None for v in vals):
                    break
                p1 = (2 * cur[0] - last_cubic[0], 2 * cur[1] - last_cubic[1]) if last_cubic else cur
                p2 = (off[0] + vals[0], off[1] + vals[1])
                p3 = (off[0] + vals[2], off[1] + vals[3])
            if not poly:
                poly.append(cur)
            _flatten_cubic(cur, p1, p2, p3, tolerance, poly)
            cur = p3
            last_cubic, last_quad = p2, None
        elif c in "QqTt":
            if c in "Qq":
                vals = [sc.num() for _ in range(4)]
                if any(v is None for v in vals):
                    break
                p1 = (off[0] + vals[0], off[1] + vals[1])
                p2 = (off[0] + vals[2], off[1] + vals[3])
            else:
                x, y = sc.num(), sc.num()
                if x is None or y is None:
                    break
                p1 = (2 * cur[0] - last_quad[0], 2 * cur[1] - last_quad[1]) if last_quad else cur
                p2 = (off[0] + x, off[1] + y)
            if not poly:
                poly.append(cur)
            _flatten_quad(cur, p1, p2, tolerance, poly)
            cur = p2
            last_quad, last_cubic = p1, None
        elif c in "Aa":
            rx, ry = sc.num(), sc.num()
            rot = sc.num()
            large = sc.flag()
            sweep = sc.flag()
            x, y = sc.num(), sc.num()
            if None in (rx, ry, rot, x, y) or large is None or sweep is None:
                break
            pt = (off[0] + x, off[1] + y) if rel else (x, y)
            if not poly:
                poly.append(cur)
            _flatten_arc(cur, rx, ry, rot, large, sweep, pt, tolerance, poly)
            cur = pt
            last_cubic = last_quad = None
        elif c in "Zz":
            if poly:
                poly.append(start)
                _flush(out, poly, True)
            cur = start
            last_cubic = last_quad = None
        else:
            break

    _flush(out, poly, False)
    if transform is not None:
        out = [(closed, [transform.apply(x, y) for x, y in pts]) for closed, pts in out]
    return out


def path_bounds(d, tolerance=0.3):
    pts = [p for _, pl in flatten_path(d, tolerance) for p in pl]
    if not pts:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def path_segment_count(d):
    return sum(len(pl) for _, pl in flatten_path(d, 0.5))


# ---------------------------------------------------------------------------
# 文本度量
# ---------------------------------------------------------------------------

def _char_em(c):
    if ord(c) >= 0x2E80:
        return 1.0
    if c == " ":
        return 0.28
    if c in "ilj!|.,';:":
        return 0.28
    if c in 'ftr()[]{} /\\"':
        return 0.36
    if c in "mwMW":
        return 0.86
    if c.isupper():
        return 0.66
    if c.isdigit():
        return 0.56
    return 0.55


def text_width(text, size, letter_spacing=0.0):
    n = 0
    w = 0.0
    for c in text:
        if c == "\n":
            continue
        w += _char_em(c) * size
        n += 1
    return w + letter_spacing * max(0, n - 1)


def line_height(size):
    return size * 1.3


def wrap_text(text, size, max_width, letter_spacing=0.0):
    if max_width <= 0:
        return [text]
    lines = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        line = ""
        width = 0.0
        i = 0
        while i < len(para):
            c = para[i]
            if ord(c) >= 0x2E80 or c == " ":
                token = c
                i += 1
            else:
                token = ""
                while i < len(para) and para[i] != " " and ord(para[i]) < 0x2E80:
                    token += para[i]
                    i += 1
            tw = text_width(token, size, letter_spacing)
            if width > 0 and width + tw > max_width:
                lines.append(line.rstrip())
                line, width = "", 0.0
                if token == " ":
                    continue
            line += token
            width += tw
        lines.append(line.rstrip())
    return lines


def ellipsize(text, size, max_width, suffix="…"):
    if text_width(text, size) <= max_width:
        return text
    suffix_w = text_width(suffix, size)
    out = ""
    w = 0.0
    for c in text:
        cw = _char_em(c) * size
        if w + cw + suffix_w > max_width:
            break
        out += c
        w += cw
    return out + suffix
