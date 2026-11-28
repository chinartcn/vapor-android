"""脏区账本：渲染管线中唯一的脏矩形真相源。

默认 ``full=True``（全帧安全兜底）；只有显式 ``mark_rect`` 累加到一定精度后
才切换为局部脏区。任何未标记绘制的低层路径（如直接写 memoryview）都不会因
本机制而丢帧——只要没人精确标记，就维持 full 全帧上传。
"""

from __future__ import annotations

MAX_REGIONS = 32          # 超过则合并/退化，避免 draw call 爆炸
MERGE_PAD = 1             # 相距 <=1px 的矩形合并，减少碎片


class DirtyLedger:
    __slots__ = ("w", "h", "_full", "_rects")

    def __init__(self):
        self.w = self.h = 0
        self._full = True           # 默认安全：未配置前一律全帧
        self._rects = []

    def set_canvas(self, w, h):
        """画布尺寸变化时调用：尺寸变了纹理必须重建 → 强制全帧。"""
        w, h = int(w), int(h)
        if (w, h) != (self.w, self.h):
            self.w, self.h = w, h
            self._full = True

    def begin_frame(self):
        self._rects = []
        # 注意：不清 _full。full 由 set_canvas / mark_full 置位，由
        # end_frame 复位；保证"本帧一旦判定全帧就全程全帧"。

    def mark_full(self):
        self._full = True

    def mark_rect(self, x, y, w, h):
        """累加一个脏矩形。full 状态下调用无意义（直接返回）。"""
        if self._full or w <= 0 or h <= 0:
            return
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(self.w, int(x) + int(w))
        y1 = min(self.h, int(y) + int(h))
        if x1 <= x0 or y1 <= y0:
            return
        # 覆盖整块画布等价于全帧（避免逐行上传整屏）。
        if x0 == 0 and y0 == 0 and x1 == self.w and y1 == self.h:
            self.mark_full()
            return
        self._rects.append((x0, y0, x1 - x0, y1 - y0))
        # 区域过多时退化为全帧：多次小上传未必比一次全帧快。
        if len(self._rects) > MAX_REGIONS * 4:
            self.mark_full()

    @property
    def is_full(self):
        return self._full

    def rects(self):
        """返回合并、裁剪后的脏矩形列表。full 返回空（调用方按全帧处理）。"""
        if self._full:
            return []
        merged = _merge(self._rects, self.w, self.h)
        if len(merged) > MAX_REGIONS:
            self.mark_full()
            return []
        return merged

    def end_frame(self):
        self._full = False          # 复位到"待标记"状态；下一帧若无人标记则不上传
        self._rects = []


def _merge(rects, w, h):
    """简单的相邻矩形合并：反复两两合并相交或相距<=MERGE_PAD 的矩形。"""
    out = list(rects)
    changed = True
    while changed:
        changed = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                ax, ay, aw, ah = out[i]
                bx, by, bw, bh = out[j]
                if (ax - MERGE_PAD <= bx + bw and bx - MERGE_PAD <= ax + aw
                        and ay - MERGE_PAD <= by + bh and by - MERGE_PAD <= ay + ah):
                    nx = min(ax, bx)
                    ny = min(ay, by)
                    nx2 = max(ax + aw, bx + bw)
                    ny2 = max(ay + ah, by + bh)
                    out[i] = (nx, ny, nx2 - nx, ny2 - ny)
                    out.pop(j)
                    changed = True
                    break
            if changed:
                break
    return out


ledger = DirtyLedger()
