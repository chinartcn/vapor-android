# Vapor 响应式框架（Vue 3 Vapor 风格，无 vdom）

> 目标：在 Vapor 渲染器之上提供一套**细粒度响应式 + 直接操作节点**的 UI 框架。
> 没有虚拟 DOM、没有 diff、没有组件级 re-render：写一个值，只有依赖它的那个
> effect 重跑，只改它负责的那个属性，绘制层再把当前值画成像素。

---

## 1. 分层

```
你的页面（Python）
   │  Vapor.ui       组件 / 布局 / 事件 / 页面栈 / SVG
   │  Vapor.template 模板编译（v-if / v-for / v-model / 插槽 / 插值）
   ▼
Vapor.core   ref / reactive / computed / watch / effectScope
   │            ▲ 细粒度依赖图（Rust，alien-signals 风格）
   ▼            │
Vapor._core（Rust）   signal 图 + 2D 光栅化（Canvas）
Vapor._ui  （Rust）   SVG path 解析/扁平化 · Mat2D · 文本度量
Vapor._sdk （Rust）   dlopen 稳定 C ABI → JNI / 文件系统
   │
   ▼
rust.Vapor（Python）  共享内存帧缓冲（memoryview）
   │
   ▼
libvapor.so（Rust）   GLES 3.0 上屏
```

三个原生模块都由 **maturin** 打包（PyO3 + abi3-py310），以混合布局安装进同一个
`Vapor` 包：

| crate | 模块 | 职责 |
|---|---|---|
| `rust/vapor-reactive` | `Vapor._core` | 响应式依赖图（双向链表，O(1) 增删）；Canvas 光栅化（矩形/线/圆/圆角/渐变/多边形/描边） |
| `rust/vapor-ui` | `Vapor._ui` | SVG path 全指令解析与扁平化（M/L/H/V/C/S/Q/T/A/Z）、仿射矩阵、文本度量与折行 |
| `rust/vapor-sdk` | `Vapor._sdk` | 运行时 `dlopen` `libvapor.so`，按符号调 `vapor_jni_*` / `vapor_fs_*` / `vapor_fb_*`，**不链接** libvapor |

没有原生扩展时，`Vapor/_fallback.py` 与 `Vapor/_fallback_ui.py` 提供**语义一致**的
纯 Python 实现，方便开发机直接跑测试（性能低 1~2 个数量级，仅供开发）。
`VAPOR_STRICT_NATIVE=1` 可禁止回退、强制使用 Rust 实现。

---

## 2. 响应式内核

与 Vue 3.6 / alien-signals 同一套模型：

* **统一节点 + 双向链表**：signal / computed / effect 是同一种 Node，依赖边是
  `Link`，同时挂在 dep 的 `subs` 链和 sub 的 `deps` 链上；追踪/解绑是 O(1)。
* **推送 + 拉取**：写 signal 时向下传播（computed 标脏、effect 入队）；读
  computed 时按脏标记惰性重算，计算只发生一次。
* **批次**：`batch(lambda: ...)` 内的多次写入只触发一次 effect。
* **作用域**：`effect_scope(...)` 返回 scope，`stop()` 一次性回收内部所有 effect
  ——组件卸载、页面出栈、列表项移除都靠它。

```python
from Vapor.core import ref, computed, watch, effect

count = ref(0)
double = computed(lambda: count.value * 2)
watch(count, lambda new, old: print(f"{old} -> {new}"))

effect(lambda: print("double =", double.value))   # 立即执行并追踪
count.value = 1                                    # 只有依赖 count 的 effect 重跑
```

Python 侧 API 与 Vue 3 对齐：

| Vue | Vapor |
|---|---|
| `ref` / `shallowRef` | `ref` / `shallow_ref` |
| `reactive` / `readonly` | `reactive` / `readonly` |
| `computed` | `computed` |
| `watch` / `watchEffect` | `watch` / `watch_effect` |
| `effectScope` | `effect_scope` |
| `nextTick` | `next_tick` / `flush_jobs` |
| `provide` / `inject` | `provide` / `inject` |
| `batch` / `untrack` | `batch` / `untrack` |

`reactive()` 给**每个属性**一个 signal：`state.user.name` 与 `state.user.age` 是
两条独立依赖，改 `age` 不会惊动只读 `name` 的 effect——这就是"粒度下沉到字段"。

---

## 3. 模板编译：从"找差异"到"直接改"

模板只做两件事：**静态分析** + **生成精确更新闭包**。

```html
<View :bg="theme.bg" padding="16">
  <Text :color="theme.fg">你好，{{ user.name }}</Text>
  <Button v-if="canEdit" text="编辑" @click="edit" />
  <Text v-for="(item, i) in items" :key="item.id">{{ i }}. {{ item.title }}</Text>
  <slot name="footer" />
</View>
```

编译后（概念上）：

* `bg`、`color` 各生成一个 effect：`node.props["bg"] = theme.bg`；
* `{{ user.name }}` 生成一个 effect：`node.text = f"你好，{user.name}"`；
* `v-if` 生成一个条件分支 effect：切换时挂载/卸载整棵子树；
* `v-for` 生成 keyed 列表协调：相同 `key` 复用节点，新增/删除只动变化项；
* `@click` 存成节点事件，命中测试时冒泡派发；
* `<slot>` 由子组件按名字取父作用域里的一段子树。

`v-for` 的 key 协调就是 Vapor 里的"列表特例"：不是通用 diff，而是
"按 key 找旧节点 → 复用或新建 → 删除消失的"，并且**每个列表项有自己的
effect bucket**，移除时整桶停止。

支持的指令：`v-if` / `v-else-if` / `v-else`、`v-for`（含 `(item, i)`、dict 迭代、
`:key`）、`v-show`、`v-model`、`v-text` / `v-html`、`:prop`、`@event`（支持
`.stop` / `.once`）、`v-slot` / `#name`、`{{ }}` 插值。表达式是真正的 Python
表达式（`item.price > 10 and user.vip`），不是受限子集。

---

## 4. 运行期

### 布局

精简 flex（够用且可预测）：

| 属性 | 含义 |
|---|---|
| `dir` | `"row"` / `"column"`（默认 column） |
| `w` / `h` | 数字、`"50%"`、`"auto"` |
| `padding` / `padding_x` / `padding_y` | 内边距（可传 `[上,右,下,左]`） |
| `margin` / `margin_x` / `margin_y` | 外边距 |
| `gap` | 主轴间距 |
| `grow` | 主轴剩余空间分配 |
| `align` | 交叉轴对齐：`start` / `center` / `end` / `stretch` |
| `justify` | 主轴对齐（无 grow 时） |
| `x` / `y` / `absolute` | 绝对定位 |
| `clip` / `scroll_y` | 裁剪与滚动 |

每帧 `layout(root, 0, 0, w, h)` 重算盒子，`_paint_node` 顺序绘制。

### 绘制

`Vapor.ui.Canvas` 把调用统一成整数坐标，原生可用时直接走 `Vapor._core.Canvas`：

```python
canvas.fill_rect(x, y, w, h, color)
canvas.line(x0, y0, x1, y1, color, width)
canvas.disc(cx, cy, r, color) / canvas.ring(...)
canvas.round_rect(x, y, w, h, radius, color)
canvas.gradient(x, y, w, h, c1, c2, vertical=True)
canvas.polygon(points, color) / canvas.polyline(points, color, width, closed=True)
```

节点属性里 `bg` 支持纯色、`#hex`、`rgb()`、命名色，也支持
`("linear", c1, c2, "v")` 渐变；`radius` 圆角；`border` 边框；
`paint=callback(canvas, box)` 自定义绘制（SVG / Icon 就是这么实现的）。

### 触控与事件

**完整链路**（Android）：

```
MotionEvent
  → RendererActivity.forwardTouch()       归一化成 down/up/move/cancel + 指针坐标
  → nativeTouch(JNI)                      推入 Rust 事件队列（VaporEvent）
  → App._poll_events()                    每帧 poll，换算时间轴
  → PointerRouter                         手势状态机（8px slop / 0.5s 长按 / 惯性）
  → App.dispatch()                        命中路径冒泡派发
```

**事件序列**（都可冒泡，`.stop` / `.once` 修饰符同样有效）：

| 事件 | 触发时机 | 常用属性 |
|---|---|---|
| `pointerdown` | 按下 | `ev.x / ev.y / ev.pointer` |
| `press` | 按下（通用“被按住”通知，所有命中节点） | `node.pressed == True` |
| `release` | 抬起/取消（与 press 配对） | `node.pressed == False` |
| `hover` / `leave` | 鼠标/触控板悬停进入 / 离开 | `node.hover` |
| `move` | 任意移动 | `ev.dx / ev.dy` |
| `dragstart` | 位移超过 8px | `ev.dx / ev.dy / ev.axis` |
| `drag` | 拖动中 | `ev.dx / ev.dy / ev.axis` |
| `dragend` | 拖动后抬手 | — |
| `click` | 未移动、未长按、抬手 | `ev.x / ev.y` |
| `dblclick` | 300ms 内两次点击（相距 <24px） | `ev.x / ev.y` |
| `longpress` | 按住 ≥0.5s 且未移动 | `ev.x / ev.y` |
| `pinch` | 双指捏合/平移 | `ev.scale`、`ev.dx / ev.dy`（质心位移） |
| `wheel` | 滚轮 / 触控板 | `ev.dx / ev.dy`（滚动量） |
| `pointerup` | 抬手（含取消） | — |

要点：

* **指针捕获**：`pointerdown` 时固定命中路径，手指拖出节点后 `drag` 仍然派发给
  原来的节点链（Sliders 这类控件不会因为手指滑出而断掉）；
* **方向锁**：第一次超过 8px 的位移决定主轴（`ev.axis = "x" | "y"`），之后不再翻转；
  `Scroll` 只在 `axis == "y"` 时消费位移，横滑翻页类手势不会误滚动列表；
* **点击判定**：位移 ≤ 8px、时长 < 0.5s、没有触发长按，抬手才发 `click`；
  `ACTION_CANCEL`（来电/滑动冲突）只发 `pointerup` + `dragend`，不误触发点击；
* **多指**：`ACTION_POINTER_DOWN/UP` 映射为 `count=2` 的 `pointerdown/pointerup`；
  双指期间不判点击、不判长按，只发 `pinch`（`ev.scale` 相对缩放 + 质心位移），
  第二指抬起时手势继续但不会误触发点击；
* **惯性滚动**：拖动结束后按抬手速度做指数衰减（`0.94/帧`，<30px/s 停），
  全程只在 `Scroll` 的 `offset` 上加减，并按时内容/视口高度钳制；
* **滚轮**：Android 的 `ACTION_SCROLL`（鼠标/触控板）经 `nativeWheel` 进入
  `EVT_WHEEL`（v1.2 追加，事件结构不变），优先滚最内层 `Scroll`，同时派发 `wheel`；
* **时间轴**：同一帧内可能积压多个事件，用事件自带的 `timestamp_ms` 换算到单调时钟，
  速度与长按判定不会因为「同帧合并」而失真；
* **返回键**：Java 转发 `nativeKey`，Python `App.handle_back()` 先让
  `Navigator` 出栈，再交给 `app.on("back", fn)`；系统默认返回行为仍然保留；
* **无头/测试**：`app.feed("down", x, y, now=t)` / `app.feed_wheel(0, 1)` /
  `app.feed_key(4)` 可以直接注入事件，测试不需要真机。

**自定义手势**：如果不想用内置状态机，可以直接读归一化后的原始事件：

```python
import rust.Vapor as RV

for ev in RV.drain_touches():
    if ev.kind == "move" and ev.is_multi_touch:
        print(ev.pointer_id, ev.spread(), ev.center, ev.timestamp_ms)
    ev.to_dict()        # 完整字段（kind/action/x/y/x2/y2/pointer_id/count/keycode/ts）
```

`TouchEvent` 提供 `is_down/is_up/is_move/is_cancel/is_multi_touch`、`spread()`（两指距离）、
`center`（质心）、`distance_to(other)`，以及 `to_dict()`。注意它和
`app._poll_events()` 共享同一个事件队列，混用会互相抢事件。

控件已接好：`Button`（按下变暗）、`Slider`（按下定位 + 拖动连续更新）、
`Scroll`（拖动 + 方向锁 + 惯性 + 边界钳制 + 滚轮）、`Switch`（点击切换）。

---

## 5. 组件

```python
import Vapor.ui as V

class Card(V.Component):
    props = {"title": ""}
    template = '''
      <View bg="#141A22" radius="12" padding="16" gap="8">
        <Text :text="title" size="18" color="#E6EAF0" />
        <slot />
      </View>
    '''
    def setup(self, props, ctx):
        return {}          # 模板作用域 = props + setup 返回值

class Home(V.Component):
    template = '''
      <Page title="首页">
        <Card title="你好">
          <Button text="点我" :on_click="on_tap" />
        </Card>
      </Page>
    '''
    components = {"Card": Card}      # 局部注册
    def setup(self, props, ctx):
        return {"on_tap": lambda e=None: print("tap")}

V.create_app(Home).mount()
```

组件规则：

* `template` 字符串 → 编译一次并缓存；
* `setup(props, ctx)` 返回值进入模板作用域，`Ref` 会被自动解包（`{{ count }}`）；
* `props` 声明默认值，父组件用 `:prop="expr"` 传值，响应式跨组件生效；
* `components` 局部注册，或 `V.register(cls)` 全局注册；
* `ctx["$emit"](name, payload)` → 父组件 `@name="handler"`；
* 组件定义的 `build(renderer, props, slots)` 允许直接用节点 API 写组件（内置组件走的这条路）。

内置组件：`View` `Row` `Column` `Center` `Stack` `Spacer` `Divider` `Text`
`Button` `Icon` `Svg` `ProgressBar` `Badge` `Switch` `Slider` `Scroll`
`ListView` `TextInput` `Page` `PageStack`。

---

## 5.5 内建预设组件（Vapor.presets）

导入即注册，全部用底层 Canvas 直接绘制（圆角/圆形/渐变/进度弧/多层阴影），
不依赖任何图片资源：

| 组件 | 说明 |
|---|---|
| @B@Card@B@ | 圆角卡片：柔和阴影 + 圆角面板 + 可选渐变/边框 + 默认插槽 |
| @B@Fab@B@ | 圆形浮动按钮：圆形阴影 + 按下高亮 |
| @B@Chip@B@ | 胶囊标签：可选中/可点击 |
| @B@SegmentedControl@B@ | 分段控件：圆角轨道 + 选中滑块 |
| @B@Dialog@B@ | 模态对话框：半透明遮罩 + 圆角面板 + 标题/内容/操作区 |
| @B@Snackbar@B@ | 底部提示条 |
| @B@Avatar@B@ | 圆形头像（首字母/文字 + 可选描边） |
| @B@ProgressRing@B@ | 环形进度（底层折线拟合圆弧） |
| @B@Skeleton@B@ | 骨架屏占位（圆角渐变块） |
| @B@ListItem@B@ | 列表项：前置/后置插槽 + 按下高亮 |
| @B@AppBar@B@ | 顶部应用栏 |
| @B@BottomSheet@B@ | 底部面板：上圆角 + 拖拽把手 + 遮罩 |

@F@python
import Vapor.presets            # 导入即注册 <Card/> <Fab/> <Chip/> ...
import Vapor.ui as V

class Home(V.Component):
    template = '''
      <View padding="20" gap="14">
        <Card>
          <Text size="18">今日步数</Text>
          <Text size="34">8,204</Text>
        </Card>
        <Row gap="10">
          <Chip text="全部" :selected="True" />
          <Chip text="关注" />
        </Row>
        <ProgressRing :value="0.62" :size="72" />
      </View>
    '''
@F@

这些预设同时是一个**组件 ABI 提供者**：@B@Vapor.presets.manifest()@B@ 列出了全部组件与
props，扩展可以声明依赖、继承或组合它们（见 [COMPONENT_ABI.md](COMPONENT_ABI.md)）。

按压反馈是通用的：路由层在按下时给命中链上每个节点置 @B@pressed = True@B@ 并派发
@B@press@B@，抬起时置回并派发 @B@release@B@；预设的 @B@Card/Fab/Chip/ListItem@B@ 据此做高亮，
自定义组件可以直接读 @B@node.pressed@B@ 或绑 @B@:on_press@B@ / @B@:on_release@B@。

---

## 6. Android 页面机制

```python
class Home(V.Page):
    template = '<Page title="首页"><Button text="详情" :on_click="go" /></Page>'
    def setup(self, props, ctx):
        def go(e=None):
            V.Navigator.current.push(Detail, {"id": 42})
        return {"go": go}

class Root(V.Component):
    template = '<PageStack :initial="Home" />'
```

* `PageStack` 是一个容器组件，创建 `Navigator`；
* `Navigator.push(page_cls, props)` / `pop()` / `replace()` / `clear()`；
* 默认带滑动转场（0.26s，旧页左移 35%），由 `App.animate` 驱动；
* `Page` 自带标题栏和返回按钮（‹），返回时自动 `pop`；
* 同一个 App 里同时只有一个 `Navigator.current`。

---

## 7. SVG 作为组件

```python
# 内置图标
<Icon name="home" :size="24" color="#E6EAF0" />

# 自定义 SVG 源码
<Svg :source="svg_text" :w="48" :h="48" color="#7BD88F" />
```

`Vapor.svg.parse_svg()` 支持：

* `path`（M/L/H/V/C/S/Q/T/A/Z 全指令，含椭圆弧）
* `rect` / `circle` / `ellipse` / `line` / `polyline` / `polygon`
* `g` 嵌套与 `transform`（matrix / translate / scale / rotate / skew）
* 填充、描边、透明度；颜色支持 `#rgb` / `#rrggbb` / `rgb()` / 命名色

渲染是**矢量**的：`Vapor._ui.flatten_path()` 把 path 扁平化成折线，
`Canvas.polygon/polyline` 直接光栅化，所以任意尺寸都清晰，没有位图缩放。

---

---

## 7.5 字体扩展（Vapor.font）

默认文本是 `rust.Vapor` 的 ASCII 5×7 点阵。接上真实字体后，测量与绘制都换成
字体自己的度量与字形：

```python
from Vapor import font

font.install()                     # 自动找系统字体（Android 的 NotoSansCJK 优先）
font.install(font.load("/sdcard/my.ttf"))
font.install(font.FontStack([font.load(cjk_ttc), font.load(latin_ttf)]))

print(font.active())               # 当前字体；None 表示仍用点阵
font.uninstall()
```

实现分三层：

| 层 | 作用 |
|---|---|
| `Vapor._ui` 的 `Font` / `FontStack`（Rust · ttf-parser + 自建光栅化） | TTF/OTF/TTC（含 CFF 轮廓）、3× 超采样扫描线填充、字形缓存、按字符回退、advance 累加、折行 |
| `Vapor.font`（Python） | 系统字体发现、`VAPOR_FONT` / `VAPOR_FONT_DIR`、`install()` / `uninstall()` |
| `Vapor.ui` | `set_active_font()` 让 `text_width` / `line_height` / `wrap_text` 走字体度量；`Canvas.text` 走字体光栅化 |

要点：

* **测量与绘制同源**：布局用的是同一字体同一字号的 advance，所以换行位置、
  右对齐、居中都准确；
* **按字符回退**：`FontStack` 给每个字符挑第一个有字形的字体，中英混排不用
  手动切字体；
* **TTC 集合**：`font.load("NotoSansCJK-Regular.ttc", 1)` 或
  `VAPOR_FONT=/system/fonts/NotoSansCJK-Regular.ttc:1` 选择具体 face；
* **`wrap` 属性**：`<Text :wrap="True" :w="312">` 会按真实字宽折行，节点高度
  随行数增长（`max_lines` 超出时加省略号）；
* **Android**：系统 CJK 字体基本都在 `/system/fonts/`，无需打包字体文件；
  需要用自带字体时把 ttf 放进 `assets/fonts/` 并设置 `VAPOR_FONT_DIR`；
* 也可以在逻辑包里写一个小扩展调用 `font.install()`（见
  `protocol/example/lib/font_ext.py`）。

---

## 8. Vapor.sdk：ABI → JNI

```python
from Vapor.sdk import Toast, Vibrator, Clipboard, Prefs, Intent, Device

Toast.show("保存成功")
Vibrator.vibrate(30)
Clipboard.set_text("hello")
Prefs("vapor").put_string("token", "abc")
Device.info()          # {"brand": "Xiaomi", "model": "...", "sdk": 34}
```

实现路径：`Vapor._sdk`（Rust）运行时 `dlopen("libvapor.so")`，取
`vapor_jni_call(req_json, out_json, cap)` 等符号；Python 侧构造 JSON 请求、
解析响应。**不链接 libvapor.so**，所以同一份 `_sdk.so` 在 Android 与本机通用。

常用封装：`Toast` / `Vibrator` / `Clipboard` / `Device` / `Battery` / `Intent`
/ `Prefs` / `PackageInfo` / `AndroidLog` / `Context` / `Permissions` /
`Location` / `Telephony` / `Notifications`。非 Android 环境下 `available()` 返回
`False`，调用会抛 `SdkUnavailable`。

### 运行时权限

AndroidManifest 一次性声明了 59 条权限（声明 ≠ 授予）；危险权限只在**真正调用对应
SDK 能力时**弹窗申请：

```python
from Vapor.sdk import Permissions, Location, Notifications

if Permissions.ensure(Permissions.CAMERA):      # 已授予返回 True，否则弹窗等待
    ...
Location.last_known()                           # 内部 ensure(ACCESS_COARSE_LOCATION)
Notifications.notify("标题", "内容")             # Android 13+ ensure(POST_NOTIFICATIONS)
```

链路：`Permissions.request` → `rust.Vapor.request_permission` → JNI 句柄调用
`Activity.requestPermissionFromNative`（主线程 `requestPermissions`）→
`onRequestPermissionsResult` → `nativePermissionResult` → Rust 结果表 →
Python 轮询取回。

---

## 9. 构建与安装

### 9.1 本机开发（maturin）

```bash
# 一次装三个模块到当前虚拟环境
maturin develop --manifest-path rust/vapor-reactive/Cargo.toml
maturin develop --manifest-path rust/vapor-ui/Cargo.toml
maturin develop --manifest-path rust/vapor-sdk/Cargo.toml

python -c "import Vapor; print(Vapor.status())"   # core/ui 应为 rust
```

或者构建 wheel 再安装：

```bash
maturin build --release --manifest-path rust/vapor-reactive/Cargo.toml --out dist
maturin build --release --manifest-path rust/vapor-ui/Cargo.toml --out dist
maturin build --release --manifest-path rust/vapor-sdk/Cargo.toml --out dist
pip install --no-deps dist/*.whl
```

`VAPOR_STRICT_NATIVE=1` 禁止回退；不装原生扩展时框架会自动用纯 Python 实现
（`Vapor.status()` 里显示 `python-fallback`）。

### 9.2 Android 交叉编译

```bash
export ANDROID_NDK_HOME=/path/to/ndk
cargo install cargo-ndk
./scripts/build_android_pyext.sh
# 产物：build/pyext/{arm64-v8a,armeabi-v7a}/Vapor/{_core,_ui,_sdk}.so
```

三个 crate 都是 `abi3-py310` + `extension-module`，交叉编译**不需要**目标机
Python（脚本里设了 `PYO3_CROSS=1` / `PYO3_NO_PYTHON=1`）。

### 9.3 装进 CPython 运行时

```bash
python3 scripts/install_to_cpython.py \
    --home build/cpython/arm64 --abi arm64 --pyext build/pyext
```

它把 `Vapor/`（纯 Python + 原生扩展）与 `rust/` 复制到
`<home>/lib/python3.15/site-packages`，并 `compileall`。内嵌 CPython 的
`PYTHONPATH` 已经包含 `site-packages`（见 `crates/vapor-core/src/python.rs`）。

`scripts/build_all.sh` 会在第 6 步自动执行上面两步，并把：

* `python/Vapor` → `assets/python/site-packages/`
* `build/pyext/<abi>` → `assets/python/pyext/<abi>/`

打进 APK；`RendererActivity` 按设备 ABI 把对应 `.so` 复制进
`files/python/site-packages/Vapor/`（同一个 APK 两个架构同名，只能运行时选）。

---

## 10. CI

`.github/workflows/ci.yml`：

| job | 内容 |
|---|---|
| `fallback` | 3 OS × Python 3.10/3.13，纯 Python 回退层跑全部测试 |
| `native` | 3 OS × Python 3.10/3.13，`PyO3/maturin-action` 构建三个 wheel，安装后 `VAPOR_STRICT_NATIVE=1` 跑同一套测试 + 12 帧无头渲染冒烟 |
| `android-pyext` | NDK r27c + cargo-ndk 交叉编译双 ABI，校验产物并上传 artifact |
| `wheels` | tag 触发，按 manylinux/macos/windows 目标构建 wheel |

`.github/workflows/android.yml`：手动触发；`build_apk=true` 时安装 SDK 并跑
`scripts/build_all.sh` 产出两个 APK。

所有编译都在 CI 上完成，本地不需要 Android SDK / NDK。

---

## 11. 已知边界

* 未装字体时文本走 ASCII 5×7 点阵（复用 `rust.Vapor._FONT`）；`Vapor.font.install()`
  之后是真实字形（TTF/OTF/TTC，含中文），测量与绘制同源。
* 字体光栅化是 CPU 端逐字形 α 混合（带缓存）；超大字号/超多字形时仍建议减少
  同帧文本变化量。
* 滚动裁剪按"整节点是否在视口内"跳过，不做像素级裁剪（避免每帧额外分配）。
* 无位图解码（PNG/JPEG 需要扩展），`Image` 未内置；矢量优先。
* `v-for` keyed 协调按 key 复用，不移动 DOM（本来就是内存节点，顺序重排即可）。
* 动画是每帧重绘驱动的（`App.animate`），不是独立合成层。
