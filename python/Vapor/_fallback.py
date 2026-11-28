"""Vapor._core 的纯 Python 回退实现（无原生扩展时自动启用）。

接口与 Rust 版完全一致，用于：
  * 未编译 _core 的开发机 / 单元测试
  * 作为行为基准（Rust 版是性能实现，语义必须相同）

注意：回退版没有链表级 O(1) 依赖图，性能低 1~2 个数量级，仅用于开发期。
"""

from __future__ import annotations

FALLBACK = True

# ---------------------------------------------------------------------------
# 响应式：简化版推拉模型（语义与 Rust 版一致）
# ---------------------------------------------------------------------------

_tracking: list = []      # 追踪栈：栈顶为当前收集依赖的节点
_pending: list = []       # 待执行 effect
_batch_depth = 0
_flushing = False
_MAX_FLUSH = 100_000
_run_count = 0


class Signal:
    def __init__(self, value=None):
        self._value = value
        self._subs: list = []
        self._version = 0

    @property
    def value(self):
        _track(self)
        return self._value

    @value.setter
    def value(self, v):
        self._value = v
        self._version += 1
        _propagate(self)
        flush()

    def peek(self):
        return self._value

    @property
    def version(self):
        return self._version

    @property
    def subscriber_count(self):
        return len(self._subs)

    @property
    def id(self):
        return id(self)

    def __repr__(self):
        return f"Signal({self._value!r})"


class Computed:
    def __init__(self, getter):
        self._fn = getter
        self._value = None
        self._subs: list = []
        self._deps: list = []
        self._dirty = True
        self._version = 0
        self._computing = False

    def _refresh(self):
        if self._computing:            # 循环依赖保护
            return
        self._computing = True
        _clear_deps(self)
        _tracking.append(self)
        try:
            self._value = self._fn()
        finally:
            _tracking.pop()
            self._computing = False
        self._version += 1
        self._dirty = False

    @property
    def value(self):
        if self._dirty:
            self._refresh()
        _track(self)
        return self._value

    def peek(self):
        if self._dirty:
            self._refresh()
        return self._value

    @property
    def dirty(self):
        return self._dirty

    @property
    def id(self):
        return id(self)

    def __repr__(self):
        return f"Computed(dirty={self._dirty})"


class Effect:
    def __init__(self, fnc, scheduler=None):
        self._fn = fnc
        self._scheduler = scheduler
        self._deps: list = []
        self._subs: list = []
        self._paused = False
        self._queued = False
        _attach_scope(self)
        self.run()

    def run(self):
        if self._paused or self._fn is None:
            return
        self._queued = False
        _clear_deps(self)
        _tracking.append(self)
        global _run_count
        try:
            self._fn()
        finally:
            _tracking.pop()
            _run_count += 1

    def stop(self):
        _clear_deps(self)
        self._queued = False
        self._paused = True
        self._fn = None
        self._scheduler = None

    def pause(self):
        _clear_deps(self)
        self._queued = False
        self._paused = True

    def resume(self):
        self._paused = False
        self.run()

    @property
    def paused(self):
        return self._paused

    @property
    def id(self):
        return id(self)

    def __repr__(self):
        return f"Effect(id={id(self)}, paused={self._paused})"


class Scope:
    def __init__(self):
        self._effects: list[Effect] = []

    def stop(self):
        for e in self._effects:
            e.stop()
        self._effects.clear()

    @property
    def size(self):
        return len(self._effects)


_scope_stack: list[Scope] = []


def _attach_scope(effect):
    if _scope_stack:
        _scope_stack[-1]._effects.append(effect)


def _track(node):
    if not _tracking:
        return
    sub = _tracking[-1]
    if sub is node:
        return
    deps = getattr(sub, "_deps", None)
    if deps is None:
        return
    if node in deps:      # 依赖数量通常很小，线性查找足够
        return
    deps.append(node)
    node._subs.append(sub)


def _clear_deps(sub):
    for dep in getattr(sub, "_deps", []):
        try:
            dep._subs.remove(sub)
        except ValueError:
            pass
    if hasattr(sub, "_deps"):
        sub._deps = []


def _propagate(dep):
    stack = [dep]
    seen = set()
    while stack:
        node = stack.pop()
        for sub in list(node._subs):
            if id(sub) in seen:
                continue
            seen.add(id(sub))
            if isinstance(sub, Effect):
                if not sub._queued and not sub._paused:
                    sub._queued = True
                    _pending.append(sub)
            elif isinstance(sub, Computed):
                if not sub._dirty:
                    sub._dirty = True
                    stack.append(sub)


def flush():
    global _flushing
    if _batch_depth > 0 or _flushing:
        return
    _flushing = True
    try:
        iters = 0
        while _pending:
            if _batch_depth > 0:
                break
            effect = _pending.pop(0)
            iters += 1
            if iters > _MAX_FLUSH:
                _pending.clear()
                raise RuntimeError("响应式更新超过上限：疑似自激写入")
            if effect._paused:
                continue
            if effect._scheduler is not None:
                # 与 Rust 内核一致：交给调度器前先复位 _queued，
                # 否则该 effect 之后不会再被 _propagate 入队。
                effect._queued = False
                effect._scheduler(effect)
            else:
                effect.run()
    finally:
        _flushing = False


def signal(value):
    return Signal(value)


def computed(getter):
    return Computed(getter)


def effect(fnc, scheduler=None):
    return Effect(fnc, scheduler)


def batch(f):
    global _batch_depth
    _batch_depth += 1
    try:
        return f()
    finally:
        _batch_depth -= 1
        flush()


def untrack(f):
    saved = list(_tracking)
    _tracking.clear()
    try:
        return f()
    finally:
        _tracking.clear()
        _tracking.extend(saved)


def effect_scope(f):
    scope = Scope()
    _scope_stack.append(scope)
    try:
        f()
    finally:
        _scope_stack.pop()
    return scope


def stats():
    return {"nodes": 0, "links": 0, "effects": 0, "computeds": 0, "pending": len(_pending), "runs": _run_count}


# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------

def rgb(r, g, b, a=255):
    return ((a & 255) << 24) | ((r & 255) << 16) | ((g & 255) << 8) | (b & 255)


def parse_color(value: str) -> int:
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 6:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        return rgb(r, g, b)
    if len(s) == 8:
        a, r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(s[6:8], 16)
        return rgb(r, g, b, a)
    raise ValueError(f"非法颜色: {value}")


def blend(c1, c2, t):
    t = max(0.0, min(1.0, t))

    def mix(a, b):
        av, bv = a & 0xFF, b & 0xFF
        return int(av + (bv - av) * t)

    return (mix(c1 >> 24, c2 >> 24) << 24) | (mix(c1 >> 16, c2 >> 16) << 16) | (mix(c1 >> 8, c2 >> 8) << 8) | mix(c1, c2)


# ---------------------------------------------------------------------------
# Canvas（纯 Python 光栅化，语义同 Rust 版）
# ---------------------------------------------------------------------------

class Canvas:
    def __init__(self, buf, width, height):
        if width <= 0 or height <= 0:
            raise ValueError("Canvas 宽高必须为正")
        self.buf = buf
        self._w = int(width)
        self._h = int(height)

    @property
    def width(self):
        return self._w

    @property
    def height(self):
        return self._h

    def size(self):
        return (self._w, self._h)

    # -- 底层 --

    def _put(self, x, y, color):
        if x < 0 or y < 0 or x >= self._w or y >= self._h:
            return
        i = (y * self._w + x) * 4
        r, g, b, a = (color >> 16) & 255, (color >> 8) & 255, color & 255, (color >> 24) & 255
        buf = self.buf
        if a == 255:
            buf[i] = r
            buf[i + 1] = g
            buf[i + 2] = b
            buf[i + 3] = 255
        elif a:
            ia = 255 - a
            buf[i] = (r * a + buf[i] * ia) // 255
            buf[i + 1] = (g * a + buf[i + 1] * ia) // 255
            buf[i + 2] = (b * a + buf[i + 2] * ia) // 255
            buf[i + 3] = 255

    def pixel(self, x, y, color):
        self._put(int(x), int(y), color)

    def get_pixel(self, x, y):
        if x < 0 or y < 0 or x >= self._w or y >= self._h:
            return 0
        i = (y * self._w + x) * 4
        buf = self.buf
        return (buf[i + 3] << 24) | (buf[i] << 16) | (buf[i + 1] << 8) | buf[i + 2]

    def clear(self, color):
        self.fill_rect(0, 0, self._w, self._h, color)

    def fill_rect(self, x, y, w, h, color):
        x, y, w, h = int(x), int(y), int(w), int(h)
        if w <= 0 or h <= 0:
            return
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self._w, x + w), min(self._h, y + h)
        if x0 >= x1 or y0 >= y1:
            return
        r, g, b, a = (color >> 16) & 255, (color >> 8) & 255, color & 255, (color >> 24) & 255
        buf = self.buf
        if a == 255:
            row = bytes((r, g, b, 255)) * (x1 - x0)
            for yy in range(y0, y1):
                start = (yy * self._w + x0) * 4
                buf[start:start + len(row)] = row
            return
        for yy in range(y0, y1):
            for xx in range(x0, x1):
                self._put(xx, yy, color)

    rect = fill_rect

    def hline(self, x, y, w, color):
        self.fill_rect(x, y, w, 1, color)

    def vline(self, x, y, h, color):
        self.fill_rect(x, y, 1, h, color)

    def line(self, x0, y0, x1, y1, color, width=1):
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        width = max(1, int(width))
        half = width // 2
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.fill_rect(x0 - half, y0 - half, width, width, color)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def disc(self, cx, cy, r, color):
        cx, cy, r = int(cx), int(cy), int(r)
        if r <= 0:
            return
        for dy in range(-r, r + 1):
            inside = r * r - dy * dy
            if inside < 0:
                continue
            dx = int(inside ** 0.5)
            self.fill_rect(cx - dx, cy + dy, dx * 2 + 1, 1, color)

    def ring(self, cx, cy, r, color, width=1):
        cx, cy, r, width = int(cx), int(cy), int(r), max(1, int(width))
        inner = max(0, r - width)
        for dy in range(-r, r + 1):
            outer = r * r - dy * dy
            if outer < 0:
                continue
            ox = int(outer ** 0.5)
            ii = inner * inner - dy * dy
            ix = int(ii ** 0.5) if ii > 0 else 0
            self.fill_rect(cx - ox, cy + dy, ox - ix, 1, color)
            self.fill_rect(cx + ix + 1, cy + dy, ox - ix, 1, color)

    def circle(self, cx, cy, r, color, width=1):
        self.ring(cx, cy, r, color, width)

    def polygon(self, points, color, fill_rule="nonzero"):
        pts = [(int(x), int(y)) for x, y in points]
        if len(pts) < 3:
            return
        nonzero = fill_rule not in ("evenodd", "even-odd")
        ys = [p[1] for p in pts]
        y0, y1 = max(0, min(ys)), min(self._h - 1, max(ys))
        n = len(pts)
        for yy in range(y0, y1 + 1):
            scan = yy + 0.5
            xs = []
            for i in range(n):
                x1, y1p = pts[i]
                x2, y2p = pts[(i + 1) % n]
                if (y1p <= scan < y2p) or (y2p <= scan < y1p):
                    t = (scan - y1p) / (y2p - y1p)
                    xs.append((x1 + t * (x2 - x1), 1 if y2p > y1p else -1))
            if len(xs) < 2:
                continue
            xs.sort()
            winding = 0
            start = None
            for xf, direction in xs:
                before = (winding != 0) if nonzero else (winding % 2 != 0)
                winding += direction if nonzero else 1
                after = (winding != 0) if nonzero else (winding % 2 != 0)
                x = int(round(xf))
                if not before and after:
                    start = x
                elif before and not after and start is not None:
                    self.fill_rect(start, yy, x - start, 1, color)
                    start = None

    def polyline(self, points, color, width=1, closed=False):
        pts = [(int(x), int(y)) for x, y in points]
        for i in range(len(pts) - 1):
            self.line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], color, width)
        if closed and len(pts) > 2:
            self.line(pts[-1][0], pts[-1][1], pts[0][0], pts[0][1], color, width)

    def round_rect(self, x, y, w, h, radius, color):
        x, y, w, h, r = int(x), int(y), int(w), int(h), max(0, int(radius))
        r = min(r, w // 2, h // 2)
        if r == 0:
            self.fill_rect(x, y, w, h, color)
            return
        for row in range(h):
            if row < r:
                dy = r - row
            elif row >= h - r:
                dy = row - (h - r - 1)
            else:
                dy = 0
            inset = r - int(max(0, r * r - dy * dy) ** 0.5) if dy else 0
            self.fill_rect(x + inset, y + row, w - inset * 2, 1, color)

    def gradient(self, x, y, w, h, c1, c2, vertical=False):
        steps = h if vertical else w
        if steps <= 0:
            return
        for i in range(steps):
            t = 0.0 if steps <= 1 else i / (steps - 1)
            c = blend(c1, c2, t)
            if vertical:
                self.fill_rect(x, y + i, w, 1, c)
            else:
                self.fill_rect(x + i, y, 1, h, c)

    def blit(self, src, sw, sh, dx, dy, alpha=255):
        for yy in range(int(sh)):
            for xx in range(int(sw)):
                si = (yy * int(sw) + xx) * 4
                a = src[si + 3] * alpha // 255
                if a:
                    color = (a << 24) | (src[si] << 16) | (src[si + 1] << 8) | src[si + 2]
                    self._put(int(dx) + xx, int(dy) + yy, color)
