# Vapor OS 演示 · 执行文档 2：主屏与 App 抽屉

> 面向执行者的可并行实施文档。本系列共四份，**文件所有权零重叠，可同时开工**。
> 本份负责 Vapor OS 的**主屏（Home）场景**：壁纸/背景、桌面 App 图标网格、Widget 占位槽、底部 Dock、页面指示器，以及上滑进入 App 抽屉的交互。
> 复用已落地的 `vos.launcher`（`MdAppGrid`/`MdAppIcon`）、`vos.chrome`（`MdStatusBar`）、`md3`（`MdSearchBar` 等）与 `vos_base` 的形状变形/中文装配。禁止硬编码主题色。

---

## 0. 范围边界（先读，避免越界）

| 本份 **要做的** | 本份 **明确不做的** |
|---|---|
| `screens/home.py`：`HomeScreen` 组件（壁纸/网格/Dock/指示） | ❌ 通知中心/QuickSettings/锁屏/最近任务（文档 3） |
| 上滑进 App 抽屉、左右翻页、App 点击发 `on_open` | ❌ 内置应用内容 / routes 数据（文档 4） |
| Widget 占位槽（契约，不含具体 widget 实现） | ❌ 修改 `screens/__init__.py`（文档 1 独占） |
| 本场景测试 | ❌ 修改任何 `Vapor/*`、`md3/*`、`vos/*`、文档 1 的文件 |

**关键铁律**：本份只**新建** `vapor_os/screens/home.py` 和一个测试文件，不修改任何现有文件。

---

## 1. 文件所有权（本份独占）

```
python/examples/vapor_os/screens/
├── __init__.py                          # 文档 1 创建，本份只读、不改
└── home.py                              # 本份独占：HomeScreen 及私有 helper

python/tests/test_vapor_os_home.py       # 本份独占：验收测试
```

> 文档 3 只建 `screens/system.py`。两份共用 `screens/` 目录但各写各的文件。

---

## 2. 依赖契约（只读调用）

```python
from Vapor.ui.renderer import Component, queue_dirty
from Vapor.theme import current_theme, get_shape
from Vapor.vos_base.cjk import type_style
from Vapor.vos_base.shape_morph import MorphSpec, ShapeMorph
from Vapor.vos.launcher import MdAppGrid, MdAppIcon       # 复用桌面网格
from Vapor.vos.chrome import MdStatusBar                   # 顶部状态栏
from Vapor.ui.motion import spring, reduced_motion
from Vapor.ui.a11y import set_a11y, Role as A11yRole
```

- 颜色一律 `current_theme().role(...)`：壁纸底色走 `surface`/`surfaceContainer`，Dock 走 `surfaceContainerHigh`，图标标签走 `onSurface`。
- 壁纸**不硬编码图片**：用 `canvas.gradient` 从两个主题角色渐变，或纯色 `surfaceContainerLow`；可选 `wallpaper_seed` prop 走 `seed_to_scheme` 的色相派生（不引入外部资源文件）。

---

## 3. `HomeScreen` 规格

`props`：

| prop | 默认 | 说明 |
|---|---|---|
| `apps` | `[]` | `[{label, icon, on_open}]`，来自文档 4 的 routes 数据 |
| `widgets` | `[]` | Widget 占位数据 `[{id, span}]`，只渲染槽位 |
| `columns` | `4` | 每行列数 |
| `pages` | `[]` | 分页数据（每页一组 app）；空则单页显示 `apps` |
| `dock` | `[]` | 底部常驻 app（电话/短信/浏览器/相机） |
| `on_open` / `on_widget` | `None` | App/Widget 点击回调（路由归调用方） |
| `on_search` | `None` | 搜索框回调 |
| `wallpaper_seed` | `None` | 可选，派生壁纸渐变色 |

### 3.1 布局结构

```
Column (100% x 100%, bg=壁纸渐变)
├── MdStatusBar                       # 顶部（复用文档2）
├── 搜索栏区（MdSearchBar，点击 on_search）
├── 页面区（横向可翻页）
│   ├── App Grid（MdAppGrid，网格图标）
│   └── Widget 槽（占 span 格，画占位边框 + 标题）
├── 页面指示器（小圆点，随 page 变）
└── Dock（底部常驻栏，4~5 图标 + 背景 surfaceContainerHigh）
```

### 3.2 交互

- **App 点击**：`MdAppIcon` 的 `on_open` → 上层 `props["on_open"](app)` → 由文档 4 的 Shell routes 执行切页。**本份不调用 `navigate()`**。
- **上滑进抽屉**：垂直上滑超过阈值 → 展开 App 抽屉（全屏 app 列表 alphabet 排序）；下限定阈值回弹。弹簧用 `spring("expressive","default","spatial")`，`reduced_motion()` 时瞬时。
- **左右翻页**：横向滑动切换 `pages`，页面指示器同步。跟手位移 + 阈值吸附。
- **搜索**：点击搜索栏触发 `on_search`（调用方打开搜索场景）。
- **长按图标**：透传 `MdAppIcon` 的 `on_long_press`（拖拽排序由 launcher 组件提供，本份只接线）。

### 3.3 分页与脏区

- `pages` 为空时单页；非空时只渲染当前页（避免一次构建所有页的图像开销）。
- 翻页/抽屉展开期间 `queue_dirty`；静止后停止，避免空转。
- 图标按压的 `ShapeMorph`（`MdAppIcon` 已自带）在跟手时每帧更新半径，同样触发脏区。

---

## 4. 实现要点与坑

1. **不重复造网格**：直接用 `MdAppGrid`/`MdAppIcon`，不要自己画图标布局；本份只负责把它们装进 Home 的分页/Dock 结构。
2. **路由归调用方**：所有 open/search/widget 事件都只发回调（`on_open`/`on_search`/`on_widget`），不 `navigate()`、不改 `apps` props。
3. **壁纸渐变取色**：`canvas.gradient(x,y,w,h,c1,c2)` 的 c1/c2 从 `current_theme().role("surfaceContainerLow"/"surfaceContainerHigh")` 取；主题切换时 effect 重跑更新（在 paint/effect 里取色，不在模块顶层）。
4. **Widget 槽是契约**：只画占位边框 + 标题（用 `type_style("labelSmall", lang="zh")`），`on_widget` 回调把 id 交给调用方；不实现具体 widget 内容（Android 16 锁屏/桌面 widget 回归的演示接口预留）。
5. **翻页吸附**：跟手位移用指针事件累计，`release` 时按 0.3×宽阈值决定翻页或回弹；过冲用 expressive spring 的 spatial 特性。
6. **中文**：所有标签 `type_style(..., lang="zh")`，Dock 文字单行省略。

---

## 5. 验收测试（`test_vapor_os_home.py`）

- `test_home_renders_grid`：给定 8 个 apps + columns=4，`HomeScreen.instantiate` 后网格节点数与 apps 匹配。
- `test_app_click_fires_callback_only`：点击图标触发 `on_open`，且未调用 `V.ui.navigate`（mock 断言）。
- `test_swipe_up_opens_drawer`：`dy` 超阈值 → 抽屉展开；小位移 → 回弹不展开。
- `test_paging_updates_indicator`：横滑切页后页面指示器状态同步。
- `test_reduced_motion_snap`：`VAPOR_REDUCED_MOTION=1` 时抽屉/翻页瞬时到终态。
- `test_widget_slot_contract`：widget 占位只发 `on_widget(id)`，不含具体内容节点。
- `test_wallpaper_from_theme`：壁纸渐变色来自主题角色（切主题后 paint 取色变化）。
- `test_no_hardcoded_color`：`home.py` 无主题色字面量。

---

## 6. 交付与合入

本份完成 `screens/home.py`，对外暴露 `HomeScreen`（`Component` 子类，`name="HomeScreen"`），由文档 1 的 `build_app(routes=...)` 通过 routes 注入。`HomeScreen` 通过 `props["on_open"]` 把 App 点击交给 Shell 路由，遵守高自由约定。合入由编排层统一验收（见文档 4 附录）。
