"""App：挂载、帧循环、事件分发与动画。"""

from __future__ import annotations

import time

from .. import core
from .base import _FRAGMENT
from .canvas import Surface
from .colors import color
from .dirty import ledger
from .input import Event, PointerRouter
from .layout import layout
from .navigation import Navigator, _resolve_navigator, close_app_navigators
from .paint import _paint_node
from .renderer import Renderer, _warn, flush_dirty

__all__ = ["App", "on_tick", "create_app", "mount"]


class App:
    """应用外壳：挂载、布局、绘制、事件、动画、帧循环。"""

    def __init__(self, root, width=None, height=None, background="#0B0E13",
                 surface=None, fps=60, registry=None, use_native=True,
                 dirty_rects=False):
        self.root_component = root
        self.width = width
        self.height = height
        self.background = background
        # 脏区总开关：默认关闭，行为与旧版完全一致（每帧全帧上传）。
        self.dirty_rects = bool(dirty_rects)
        self.fps = fps
        self.surface = surface
        self.renderer = Renderer(registry)
        self.root = None
        self.pointer = PointerRouter(self)
        self._last_now = time.monotonic()
        self._last_ts = None
        self._running = False
        self._handlers = {}
        self._animations = []
        self._tick_hooks = []
        self._dirty = True

    # ---- 生命周期 ----

    def mount(self):
        if self.surface is None:
            self.surface = Surface(self.width, self.height)
        self.width, self.height = self.surface.width, self.surface.height
        # 账本是进程级单例：新 App 挂载时清掉上一个 App 的残留状态，
        # 并强制首帧全帧绘制（尺寸相同不会再触发 set_canvas 的 full）。
        ledger.set_canvas(self.width, self.height)
        ledger.begin_frame()
        ledger.mark_full()
        self.registry_component(self.root_component)
        self.renderer.app = self
        self.root = self.root_component.instantiate(self.renderer, {}, {})
        self.root.key = "root"
        core.flush_jobs()
        self.tick()
        return self

    def registry_component(self, comp):
        name = getattr(comp, "name", "") or comp.__name__
        self.renderer.registry[name] = comp
        return name

    def unmount(self):
        self._running = False
        if self.root is not None:
            self.root.dispose()
            self.root = None
        close_app_navigators(self)  # current 回退到上一层栈

    # ---- 帧 ----

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        if self.dirty_rects and self.width and self.height:
            ledger.set_canvas(self.width, self.height)
            ledger.begin_frame()
        core.flush_jobs()
        self.pointer.update(now)
        self._run_animations(now)
        for hook in list(self._tick_hooks):
            try:
                hook(now)
            except Exception as exc:
                _warn(f"on_tick 回调失败: {exc}")
        try:
            if self.root is not None:
                # effect 可能在帧外同步执行，先落账再布局（begin_frame 已清空过）。
                flush_dirty()
                layout(self.root, 0, 0, self.width, self.height)
                canvas = self.surface.canvas()
                bg = color(self.background)
                rects = ledger.rects() if self.dirty_rects else None
                if self.dirty_rects and not ledger.is_full and not rects:
                    # 本帧没有任何变化：不重绘、不上传（present([]) 会被当作全帧）。
                    pass
                else:
                    if rects is None or ledger.is_full:
                        # 全帧：清屏后整树重绘（首帧 / resize / clear 请求）。
                        canvas.clear(bg)
                    else:
                        # 局部：只把脏矩形重填背景擦除旧内容，随后整树重绘恢复
                        # 其中未变化的像素；最后仅上传这些脏矩形。
                        for x, y, w, h in rects:
                            canvas.fill_rect(x, y, w, h, bg)
                    _paint_node(canvas, self.root)
                    self.surface.present(rects)
        finally:
            if self.dirty_rects:
                ledger.end_frame()
        self._dirty = False
        return self

    def run(self):
        self._running = True
        frame = 1.0 / max(1, self.fps)
        try:
            while self._running:
                start = time.monotonic()
                self._poll_events()
                self.tick(start)
                elapsed = time.monotonic() - start
                if elapsed < frame:
                    time.sleep(frame - elapsed)
        finally:
            self._running = False
        return self

    def stop(self):
        self._running = False

    def _poll_events(self):
        """把 rust.Vapor 的事件队列翻译成手势层事件（用 poll_touch 拿归一化数据）。"""
        try:
            import rust.Vapor as rv
        except Exception:
            return
        try:
            while True:
                ev = rv.poll_touch()
                if ev is None:
                    break
                now = self._event_time(ev.timestamp_ms)
                if ev.kind == "key":
                    if ev.action == 0:
                        self.feed_key(ev.keycode)
                    continue
                if ev.kind == "wheel":
                    self.feed_wheel(ev.x, ev.y, now=now)
                    continue
                multi = ev.pointer_count > 1
                self.feed(
                    ev.kind, ev.x, ev.y,
                    pointer=ev.pointer_id,
                    count=ev.pointer_count,
                    now=now,
                    x2=ev.x2 if multi else None,
                    y2=ev.y2 if multi else None,
                )
        except Exception:
            pass

    # ---- 手势 / 按键入口（原生事件、测试、桌面模拟都走这里） ----

    def _event_time(self, ts):
        """把事件时间戳（wall clock ms）换算到单调时间轴，保证同一帧内多个事件
        之间的 dt 仍然正确（速度 / 长按判定依赖它）。"""
        if not ts:
            return time.monotonic()
        if self._last_ts is not None:
            delta = (ts - self._last_ts) / 1000.0
            delta = max(0.0, min(0.1, delta))
            self._last_now = self._last_now + delta
        else:
            self._last_now = time.monotonic()
        self._last_ts = ts
        return self._last_now

    def feed(self, kind, x, y, pointer=0, now=None, x2=None, y2=None, count=1):
        """手工投递一个指针事件：kind = down / pointerdown / move / up / pointerup / cancel。"""
        self.pointer.handle(kind, x, y, pointer=pointer, count=count, now=now, x2=x2, y2=y2)

    def feed_wheel(self, dx, dy, now=None):
        """手工投递滚轮事件（Android 的 ACTION_SCROLL / 桌面鼠标滚轮）。"""
        return self.pointer.handle_wheel(dx, dy, now=now)

    def feed_key(self, keycode):
        """投递按键：4 = 返回（Android BACK），27 = ESC（桌面）。"""
        if keycode in (4, 27):
            self.handle_back()

    def handle_back(self):
        """返回键：先让页面栈出栈，再交给 app.on("back") 的处理器。"""
        nav = _resolve_navigator(self)
        if nav is not None and nav.depth > 1:
            nav.pop()  # 与 go_back 同一路径：触发 on_hide / on_show / on_result
            return True
        for fn in list(self._handlers.get("back", [])):
            try:
                if fn() is not False:
                    return True
            except Exception as exc:
                _warn(f"back 处理器失败: {exc}")
        # Android 上 Java 会吞掉 BACK 交给 Python；没有页面可弹 / 没有处理器时
        # 由这里结束 Activity，避免返回键失效。
        _finish_activity()
        return False

    # ---- 事件分发 ----

    def hit_test(self, x, y):
        path = []

        def walk(node):
            if not node.visible or node.props.get("display") == "none":
                return False
            box = node.box
            if node.clip and not box.contains(x, y):
                return False
            if node.tag == _FRAGMENT:
                for child in node.children:
                    if walk(child):
                        path.append(node)
                        return True
                return False
            if not box.contains(x, y):
                return False
            for child in reversed(node.children):
                if walk(child):
                    path.append(node)
                    return True
            path.append(node)
            return True

        if self.root is not None:
            walk(self.root)
        return path

    def dispatch(self, event_type, x, y, raw=None, dx=0.0, dy=0.0, pointer=0, path=None,
                 scale=1.0, axis=None):
        if path is None:
            path = self.hit_test(x, y)
        if not path:
            return None
        ev = Event(event_type, x, y, raw, dx, dy, pointer, scale, axis)
        for node in path:
            handlers = node.events.get(event_type)
            if not handlers:
                continue
            ev.target = node
            for provider, modifiers in handlers:
                try:
                    handler = provider()
                except Exception as exc:
                    _warn(f"事件处理器构建失败: {exc}")
                    continue
                if not callable(handler):
                    continue
                try:
                    handler(ev)
                except Exception as exc:
                    _warn(f"事件处理器异常: {exc}")
                if "once" in modifiers:
                    try:
                        handlers.remove((provider, modifiers))
                    except ValueError:
                        pass
                if ev.stopped or "stop" in modifiers:
                    return ev
        return ev

    # ---- 动画 ----

    def animate(self, duration, on_frame, easing=None, on_done=None):
        """注册一个时长动画；on_frame(t) 的 t 从 0 到 1。"""
        if easing is None:
            def easing(t):
                return 1 - (1 - t) * (1 - t) ** 2 if t < 1 else 1.0

        anim = {
            "start": time.monotonic(),
            "duration": max(0.001, float(duration)),
            "on_frame": on_frame,
            "easing": easing,
            "on_done": on_done,
        }
        self._animations.append(anim)
        try:
            on_frame(easing(0.0))
        except Exception as exc:
            _warn(f"动画首帧失败: {exc}")
        return anim

    def _run_animations(self, now):
        if not self._animations:
            return
        alive = []
        for anim in self._animations:
            t = (now - anim["start"]) / anim["duration"]
            t = max(0.0, min(1.0, t))
            try:
                anim["on_frame"](anim["easing"](t))
            except Exception as exc:
                _warn(f"动画帧失败: {exc}")
            if t >= 1.0:
                if anim["on_done"]:
                    try:
                        anim["on_done"]()
                    except Exception as exc:
                        _warn(f"动画结束回调失败: {exc}")
            else:
                alive.append(anim)
        self._animations = alive

    def on_tick(self, fn):
        self._tick_hooks.append(fn)
        return fn

    def on(self, event, fn):
        self._handlers.setdefault(event, []).append(fn)
        return fn


def on_tick(fn):
    """模块级快捷方式：等价于 app.on_tick，需要先在当前 App 上注册。"""
    app = App.current
    if app is None:
        raise RuntimeError("on_tick 只能在 App 挂载后使用")
    return app.on_tick(fn)


def _finish_activity():
    """Android 宿主：结束当前 Activity（桌面/无原生环境静默忽略）。"""
    try:
        from rust import Vapor as rv

        act = rv.activity()
        if not act:
            return
        from rust import jni

        jni.call(int(act), "finish", "()V", [])
    except Exception:
        pass


App.current = None


def create_app(root, width=None, height=None, **kwargs) -> App:
    app = App(root, width=width, height=height, **kwargs)
    App.current = app
    return app


def mount(root, width=None, height=None, **kwargs) -> App:
    return create_app(root, width=width, height=height, **kwargs).mount()


def _later(fn):
    """兼容别名：与 core.next_tick 相同。"""
    return core.next_tick(fn)
