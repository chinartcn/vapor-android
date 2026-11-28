"""手势状态机与事件对象。"""

from __future__ import annotations

import time

from .paint import _scroll_by

__all__ = ["PointerRouter", "Event"]


class PointerRouter:
    """手势状态机：单指（点击/拖动/长按/惯性）、双指（捏合/平移）、滚轮。

    事件（都可冒泡，.stop / .once 有效）::

        pointerdown / pointerup / move
        dragstart / drag / dragend      （超过 8px 才判定拖动，ev.axis 是方向锁）
        click                            （未移动、未长按、抬手）
        dblclick                         （300ms 内两次点击，相距 <24px）
        longpress                        （按住 0.5s 未移动）
        pinch                            （双指：ev.scale 相对缩放，ev.dx/dy 质心位移）
        wheel                            （滚轮/触控板：ev.dx、ev.dy）
    """

    SLOP = 8.0          # 判定为拖动的位移阈值（px）
    LONG_PRESS = 0.5    # 长按时间（秒）
    FLING_STOP = 30.0   # 惯性停止速度（px/s）
    DOUBLE_TAP = 0.30   # 双击间隔（秒）
    DOUBLE_SLOP = 24.0  # 双击允许的位置偏差（px）
    WHEEL_STEP = 40.0   # 滚轮一格对应的像素

    def __init__(self, app):
        self.app = app
        self.state = None
        self.fling = None
        self.last_pos = None
        self._last_click = (0.0, 0.0, 0.0)
        self._hover_path = []

    # ---- 入口 ----

    def handle(self, kind, x, y, pointer=0, count=1, now=None, x2=None, y2=None):
        now = time.monotonic() if now is None else now
        self.last_pos = (x, y)
        if kind == "down":
            self._down(x, y, pointer, now, count)
        elif kind == "pointerdown":
            self._second_down(x, y, pointer, now, count)
        elif kind == "move":
            self._move(x, y, pointer, now, count, x2, y2)
        elif kind in ("up", "cancel", "pointerup"):
            self._up(x, y, pointer, now,
                     cancel=(kind == "cancel"), partial=(kind == "pointerup"))

    def handle_wheel(self, dx, dy, now=None):
        """滚轮/触控板：优先滚最内层的滚动容器，同时派发 wheel 事件。"""
        x, y = self.last_pos or (self.app.width / 2.0, self.app.height / 2.0)
        path = self.app.hit_test(x, y)
        scrolls = [n for n in path if "scroll" in n.extra]
        if scrolls:
            _scroll_by(scrolls[0], dy * self.WHEEL_STEP)
        return self.app.dispatch("wheel", x, y, None, dx, dy, 0, path)

    # ---- 状态机 ----

    def _down(self, x, y, pointer, now, count=1):
        self.fling = None
        path = self.app.hit_test(x, y)
        scrolls = [n for n in path if "scroll" in n.extra]
        self.state = {
            "id": pointer, "sx": x, "sy": y, "lx": x, "ly": y, "t0": now,
            "path": path, "dragging": False, "long": False,
            "multi": count > 1, "count": count, "axis": None,
            "scroll": scrolls[0] if scrolls else None,
            "vx": 0.0, "vy": 0.0, "last_t": now,
            "pinch": None, "centroid": None,
        }
        self.app.dispatch("pointerdown", x, y, None, 0.0, 0.0, pointer, path)
        # 组件"被按住"的通用通知：命中的整条链都能收到 press，同时维护 node.pressed
        for node in path:
            node.pressed = True
        self.app.dispatch("press", x, y, None, 0.0, 0.0, pointer, path)

    def _second_down(self, x, y, pointer, now, count):
        state = self.state
        if state is None:
            self._down(x, y, pointer, now, count)
            return
        state["multi"] = True
        state["count"] = count
        state["dragging"] = False
        state["long"] = True          # 多指不触发长按
        state["pinch"] = None
        state["centroid"] = None
        self.app.dispatch("pointerdown", x, y, None, 0.0, 0.0, pointer, state["path"])

    def _multi_move(self, state, x, y, pointer, x2, y2):
        state["multi"] = True
        state["dragging"] = False
        if x2 is None or y2 is None:
            return
        spread = max(1e-6, ((x - x2) ** 2 + (y - y2) ** 2) ** 0.5)
        cx, cy = (x + x2) / 2.0, (y + y2) / 2.0
        if state["pinch"] is None:
            state["pinch"] = spread
            state["centroid"] = (cx, cy)
            return
        scale = spread / max(1e-6, state["pinch"])
        pdx = cx - state["centroid"][0]
        pdy = cy - state["centroid"][1]
        if abs(scale - 1.0) > 0.004 or abs(pdx) > 0.5 or abs(pdy) > 0.5:
            self.app.dispatch("pinch", cx, cy, None, pdx, pdy, pointer,
                              state["path"], scale=scale)
            state["pinch"] = spread
            state["centroid"] = (cx, cy)

    def _move(self, x, y, pointer, now, count=1, x2=None, y2=None):
        state = self.state
        if state is None:
            self._hover(x, y, pointer, now)
            return
        if count > 1 or state["multi"]:
            self._multi_move(state, x, y, pointer, x2, y2)
            state["lx"], state["ly"] = x, y
            return

        dx = x - state["lx"]
        dy = y - state["ly"]
        state["lx"], state["ly"] = x, y
        moved = abs(x - state["sx"]) + abs(y - state["sy"])

        dt = max(1e-3, now - state["last_t"])
        state["vx"] = state["vx"] * 0.5 + (dx / dt) * 0.5
        state["vy"] = state["vy"] * 0.5 + (dy / dt) * 0.5
        state["last_t"] = now

        if not state["dragging"] and moved > self.SLOP:
            state["dragging"] = True
            # 方向锁：第一次超过阈值的位移决定主轴，后续不再翻转
            state["axis"] = "x" if abs(x - state["sx"]) > abs(y - state["sy"]) else "y"
            self._dispatch_axis("dragstart", x, y, dx, dy, pointer, state)

        if state["dragging"]:
            self._dispatch_axis("drag", x, y, dx, dy, pointer, state)
            if state["scroll"] is not None and state["axis"] == "y":
                _scroll_by(state["scroll"], -dy)

        self.app.dispatch("move", x, y, None, dx, dy, pointer, state["path"])

    def _hover(self, x, y, pointer, now):
        """无按键移动：维护 hover/leave（鼠标、触控板、桌面调试）。"""
        path = self.app.hit_test(x, y)
        if path == self._hover_path:
            return
        for node in self._hover_path:
            node.hover = False
        if self._hover_path:
            self.app.dispatch("leave", x, y, None, 0.0, 0.0, pointer, self._hover_path)
        for node in path:
            node.hover = True
        if path:
            self.app.dispatch("hover", x, y, None, 0.0, 0.0, pointer, path)
        self._hover_path = path

    def _dispatch_axis(self, kind, x, y, dx, dy, pointer, state):
        return self.app.dispatch(kind, x, y, None, dx, dy, pointer,
                                 state["path"], axis=state["axis"])

    def _up(self, x, y, pointer, now, cancel=False, partial=False):
        state = self.state
        if state is None:
            return
        path = state["path"]
        if partial:
            # 第二根手指抬起：保留手势状态，但不再判点击/长按
            state["pinch"] = None
            state["centroid"] = None
            state["multi"] = True
            state["long"] = True
            return

        for node in path:
            node.pressed = False
        self.app.dispatch("release", x, y, None, 0.0, 0.0, pointer, path)
        self.app.dispatch("pointerup", x, y, None, 0.0, 0.0, pointer, path)
        if cancel:
            if state["dragging"]:
                self.app.dispatch("dragend", x, y, None, 0.0, 0.0, pointer, path)
        elif state["multi"] or state["long"]:
            pass  # 多指 / 长按：不判点击
        elif state["dragging"]:
            self.app.dispatch("dragend", x, y, None, 0.0, 0.0, pointer, path)
            velocity = -state["vy"] * 1000.0   # 内容滚动速度（px/s）
            if state["scroll"] is not None and state["axis"] == "y" and abs(velocity) > 200.0:
                self.fling = {"node": state["scroll"], "v": velocity, "t": now}
        else:
            self.app.dispatch("click", x, y, None, 0.0, 0.0, pointer, path)
            last_t, lx, ly = self._last_click
            if (now - last_t <= self.DOUBLE_TAP
                    and abs(x - lx) <= self.DOUBLE_SLOP
                    and abs(y - ly) <= self.DOUBLE_SLOP):
                self.app.dispatch("dblclick", x, y, None, 0.0, 0.0, pointer, path)
                self._last_click = (0.0, 0.0, 0.0)
            else:
                self._last_click = (now, x, y)
        self.state = None

    # ---- 每帧推进（长按判定 + 惯性） ----

    def update(self, now):
        state = self.state
        if (
            state is not None
            and not state["dragging"]
            and not state["long"]
            and not state["multi"]
            and now - state["t0"] >= self.LONG_PRESS
        ):
            state["long"] = True
            self.app.dispatch("longpress", state["lx"], state["ly"], None, 0.0, 0.0,
                              state["id"], state["path"])

        fling = self.fling
        if fling is not None:
            dt = max(0.0, min(0.05, now - fling["t"]))
            fling["t"] = now
            if dt > 0:
                _scroll_by(fling["node"], fling["v"] * dt)
                fling["v"] *= 0.94 ** (dt * 60.0)
                if abs(fling["v"]) < self.FLING_STOP:
                    self.fling = None


class Event:
    __slots__ = ("type", "x", "y", "raw", "target", "stopped", "dx", "dy",
                 "pointer", "scale", "axis")

    def __init__(self, type_, x, y, raw=None, dx=0.0, dy=0.0, pointer=0,
                 scale=1.0, axis=None):
        self.type = type_
        self.x = x
        self.y = y
        self.raw = raw
        self.target = None
        self.stopped = False
        self.dx = dx          # 本次 move/drag 的位移
        self.dy = dy
        self.pointer = pointer
        self.scale = scale    # 双指捏合的相对缩放
        self.axis = axis      # 拖动方向锁："x" / "y"

    def stop(self):
        self.stopped = True

    def __repr__(self):
        return f"Event({self.type}, {self.x}, {self.y})"
