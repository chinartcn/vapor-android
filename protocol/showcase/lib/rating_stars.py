"""示例组件（组件 ABI）：RatingStars —— 五角星评分。

演示三件事：
  1. 组件 ABI v1：ABI / manifest() / register(host)
  2. 用底层 Canvas 直接画多边形（不依赖图片/字体）
  3. 点击/按压反馈 + 受控 props（value 变化会重建星形）

被逻辑包 lib/abi.json 加载后，模板里可以直接写：

    <RatingStars :value="score" :size="28" :on_change="set_score" />
"""

from __future__ import annotations

import math

from Vapor import ui as V

ABI = 1


def _star_points(cx, cy, outer, inner, points=5):
    """五角星顶点（外/内半径交替）。"""
    pts = []
    for i in range(points * 2):
        radius = outer if i % 2 == 0 else inner
        angle = math.radians(-90 + i * 180.0 / points)
        pts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return pts


def _draw_star(canvas, box, color, filled, stroke_color=None):
    size = min(box.w, box.h)
    cx, cy = box.x + box.w / 2.0, box.y + box.h / 2.0
    outer = size * 0.48
    inner = outer * 0.46
    pts = _star_points(cx, cy, outer, inner)
    if filled:
        canvas.polygon(pts, color)
    else:
        canvas.polyline(pts, color, max(1, int(size * 0.06)), True)
    if stroke_color is not None:
        canvas.polyline(pts, stroke_color, max(1, int(size * 0.05)), True)


class RatingStars(V.Component):
    name = "RatingStars"
    props = {"value": 0, "stars": 5, "size": 28,
             "color": "#F5C542", "empty": "#2A3542"}

    @staticmethod
    def build(r, props, slots):
        row = r.element("view", {"dir": "row", "gap": 4})
        row.props["name"] = "rating-stars"

        def rebuild():
            for child in list(row.children):
                child.remove()
            count = int(props.get("stars") or 5)
            size = float(props.get("size") or 28)
            value = float(props.get("value") or 0)
            for index in range(count):
                star = r.element("view", {"w": size, "h": size})

                def paint(canvas, box, idx=index):
                    active = (idx + 1) <= float(props.get("value") or 0)
                    color = V.color(props.get("color", "#F5C542") if active
                                    else props.get("empty", "#2A3542"))
                    _draw_star(canvas, box, color, active)

                star.props["paint"] = paint

                def on_click(ev, idx=index):
                    new_value = idx + 1
                    callback = props.get("on_change")
                    if callable(callback):
                        callback(new_value, idx)
                    props["value"] = new_value

                r.on(star, "click", lambda f=on_click: f)
                row.add(star)

        r._new_effect(rebuild)
        return [row]


def manifest():
    return {
        "abi": ABI,
        "name": "vapor.rating",
        "version": "1.0.0",
        "components": [
            {
                "name": "RatingStars",
                "props": dict(RatingStars.props),
                "description": "五角星评分组件：点击设置分值，底层 Canvas 多边形绘制",
            },
        ],
    }


def register(host):
    host.register(RatingStars)
