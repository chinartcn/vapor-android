"""Vapor Showcase —— 综合演示应用。

覆盖：页面栈导航 / 预设圆角组件 / 自定义 ABI 组件（RatingStars）/ 手势（点击、
长按、拖动滚动、按压反馈）/ 字体自动安装 / 运行时权限按钮。

同时被两个入口复用：
  * protocol/showcase/main.py     逻辑包入口（打进 APK）
  * python/examples/showcase.py   本机/CI 无头演示
"""

from __future__ import annotations

import os

from Vapor import ui as V

import Vapor.presets  # noqa: F401  —— 导入即注册 Card/Fab/Chip/...
import rating_stars  # noqa: E402  —— 自定义组件（组件 ABI）
from Vapor import component as _component  # noqa: E402

# 本机/CI 直接跑时不会经过扩展加载器，这里补一次注册（APK 里由 abi.json 负责）
if "RatingStars" not in V.COMPONENTS:
    _component.register_provider(rating_stars)

__all__ = ["Showcase", "Home", "Detail", "create_app", "main"]


def _try_font() -> bool:
    """有原生字体模块且系统里有字体时，自动装上（中文才能正常显示）。"""
    try:
        from Vapor import font

        if font.available() and font.default_font() is not None:
            return font.install()
    except Exception:
        pass
    return False


def _sdk():
    try:
        from Vapor import sdk

        return sdk
    except Exception:
        return None


class Detail(V.Component):
    """详情页：滚动列表 + 按压反馈 + 运行时权限按钮。"""

    template = '''
      <Page title="详情">
        <View padding="16" w="100%" h="100%">
          <Scroll h="100%" w="100%">
            <View gap="10" w="100%">
              <Text size="12" color="#8A94A6" :wrap="True" :w="320">
                手指按在列表项上会有 press 高亮；上下拖动可滚动；双指捏合会派发 pinch。
              </Text>
              <ListItem v-for="item in items" :key="item" :title="item"
                        :subtitle="'点击弹出提示：' + item" @click="notify" />
              <Button text="申请相机权限" :on_click="ask_camera" />
              <Button text="申请定位权限" :on_click="ask_location" />
              <Snackbar :visible="snack" :text="message" action="关闭" :on_action="close_snack" />
            </View>
          </Scroll>
        </View>
      </Page>
    '''

    def setup(self, props, ctx):
        snack = V.ref(False)
        message = V.ref("")

        def notify(ev=None):
            message.value = "你点击了列表项"
            snack.value = True

        def close_snack(ev=None):
            snack.value = False

        def ask_camera(ev=None):
            _ask(V, "CAMERA")

        def ask_location(ev=None):
            _ask(V, "ACCESS_COARSE_LOCATION")

        return {
            "items": [f"列表项 {i}" for i in range(1, 10)],
            "notify": notify,
            "ask_camera": ask_camera,
            "ask_location": ask_location,
            "snack": snack,
            "message": message,
            "close_snack": close_snack,
        }


def _ask(ui_module, permission_name: str) -> None:
    """真正点击时才弹权限窗（声明已在 Manifest 里）。"""
    sdk = _sdk()
    if sdk is None or not sdk.available():
        print(f"[showcase] 非 Android 环境，跳过权限 {permission_name}")
        return
    try:
        granted = sdk.Permissions.ensure(getattr(sdk.Permissions, permission_name))
        print(f"[showcase] {permission_name}: {'granted' if granted else 'denied'}")
    except Exception as exc:
        print(f"[showcase] 权限申请失败: {exc}")


class Home(V.Component):
    """首页：卡片 / 评分组件 / 分段控件 / 胶囊 / 列表 / 对话框 / 浮动按钮。"""

    template = '''
      <Page title="Vapor Showcase">
        <View padding="16" gap="12" w="100%" h="100%">
          <Card w="100%" h="116">
            <Row justify="space-between" align="center">
              <View gap="2">
                <Text size="13" color="#8A94A6">今日活跃</Text>
                <Text size="30" color="#E6EAF0">{{ progress_text }}</Text>
              </View>
              <ProgressRing :value="progress" :size="72" />
            </Row>
          </Card>

          <Card w="100%" gap="6">
            <Text size="15" color="#E6EAF0">自定义组件 RatingStars</Text>
            <RatingStars :value="rating" :size="30" :on_change="set_rating" />
            <Text size="12" color="#8A94A6">已评 {{ rating }} 星（点击星星试试）</Text>
          </Card>

          <SegmentedControl :items="ranges" :value="range" w="100%" :on_change="set_range" />

          <Row gap="8">
            <Chip text="全部" :selected="tab == 0" @click="tab = 0" />
            <Chip text="关注" :selected="tab == 1" @click="tab = 1" />
            <Chip text="收藏" :selected="tab == 2" @click="tab = 2" />
          </Row>

          <Card w="100%" padding="0">
            <ListItem title="打开详情页" subtitle="PageStack / Navigator.push"
                      @click="open_detail" />
            <ListItem title="弹出对话框" subtitle="Dialog + 遮罩 + 圆角面板"
                      @click="dialog = True" />
          </Card>

          <Spacer />
        </View>

        <Fab text="+" :size="56" absolute="true" x="276" y="500" @click="open_detail" />

        <Dialog :visible="dialog" title="关于 Vapor">
          <Text size="13" color="#C7D0DB" :wrap="True" :w="260">
            这是用 Vapor.ui 写的演示：预设圆角组件、自定义 ABI 组件、页面栈、
            手势反馈和运行时权限。全部绘制都走底层 Canvas。
          </Text>
          <template #actions>
            <Button text="知道了" :on_click="close_dialog" />
          </template>
        </Dialog>
      </Page>
    '''

    def setup(self, props, ctx):
        rating = V.ref(3)
        progress = V.computed(lambda: min(1.0, 0.3 + rating.value * 0.12))
        range_index = V.ref(1)
        tab = V.ref(0)
        dialog = V.ref(False)

        def set_rating(value, index=None):
            rating.value = value

        def set_range(value, index=None):
            range_index.value = index if index is not None else 0

        def open_detail(ev=None):
            nav = V.Navigator.current
            if nav is not None:
                nav.push(Detail, {})

        def close_dialog(ev=None):
            dialog.value = False

        return {
            "rating": rating,
            "progress": progress,
            "progress_text": V.computed(lambda: f"{int(progress.value * 100)}%"),
            "ranges": ["日", "周", "月"],
            "range": range_index,
            "tab": tab,
            "dialog": dialog,
            "set_rating": set_rating,
            "set_range": set_range,
            "open_detail": open_detail,
            "close_dialog": close_dialog,
        }


class Showcase(V.Component):
    """根组件：页面栈容器，初始页 Home。"""

    template = '<PageStack :initial="HomePage" />'
    components = {"PageStack": V.PageStack}

    def setup(self, props, ctx):
        return {"HomePage": Home}


def create_app(width=None, height=None, use_native=True):
    _try_font()
    app = V.create_app(Showcase, width=width, height=height, use_native=use_native)
    app.mount()
    return app


def main() -> int:
    app = create_app()
    print("[showcase] ready:", app.surface.width, "x", app.surface.height)
    try:
        print("[showcase] native:", V._native.status())
    except Exception:
        pass
    frames = int(os.environ.get("VAPOR_DEMO_FRAMES", "0") or 0)
    if frames:
        for _ in range(frames):
            app.tick()
        print("[showcase] rendered", frames, "frames")
        return 0
    app.run()
    return 0
