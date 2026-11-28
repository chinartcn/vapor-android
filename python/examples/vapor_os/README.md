# Vapor OS 演示

Vapor OS 是基于 Vapor UI SDK 的「仿 Android 16 / Material 3 Expressive」系统界面演示，
用来验证复杂 UI 场景下的组件能力：主题角色取色、Expressive 形状变形、中文字阶装配、
脏区局部刷新与无头出帧。

- **8 个场景**：主屏、设置、时钟、相册、计算器、通知中心、锁屏、最近任务
- **组件**：`md3`（24 个）+ `vos`（15 个），全部通过 provider 注册
- **零硬编码主题色**：颜色一律来自 `current_theme().role(...)`
- **中文排版**：`vos_base.install_cjk()` + `type_style(..., lang="zh")`

## 运行

在仓库的 `python/` 目录下执行：

```bash
# 桌面窗口（需宿主图形环境）
python3 -m examples.vapor_os.main

# 无头出帧验证：逐场景 build → tick → unmount
VAPOR_DEMO_FRAMES=2 python3 -m examples.vapor_os.run_showcase
```

`run_showcase.py` 不进入 `app.run()`，可直接在 CI 中执行；若 `Surface`
提供 `to_png()` / `save_image()`，会把每个场景落到 `_shots/<scene>.png`，
否则只验证不崩。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `VAPOR_DEMO_FRAMES` | `2` | 无头脚本每个场景渲染的帧数 |
| `VAPOR_OS_SCENES` | 全部 | 逗号分隔的场景子集，如 `home,settings` |
| `VAPOR_OS_DARK` | 关闭 | `1` 使用深色主题 |
| `VAPOR_OS_SEED` | 无 | 种子色（如 `0xFF6750A4`），派生整套配色 |
| `VAPOR_OS_W` / `VAPOR_OS_H` | `360` / `800` | 画布尺寸 |
| `VAPOR_REDUCED_MOTION` | 关闭 | `1` 关闭弹簧/变形动画，直接落终态 |

## 场景清单

| route | 内容 |
|---|---|
| `home` | 壁纸渐变、搜索栏、分页图标网格、Widget 占位、Dock、上滑 App 抽屉 |
| `settings` | 分组列表 + 开关/滑杆，主题色实时联动 |
| `clock` | 世界时钟/闹钟/计时器分页，中文强调大字 |
| `gallery` | 网格 + `ShapeMorph` 选中，局部脏区刷新 |
| `calculator` | 密集键盘 + 按压态 + 运算符使能 |
| `shade` | 通知中心 + Quick Settings 瓷砖 |
| `lock` | 锁屏时钟、通知预览、上滑解锁 |
| `recents` | 最近任务卡片、滑除与全部清除 |

## 目录结构

```
python/examples/vapor_os/
├── __init__.py          # 文档 1：包入口
├── app.py               # 文档 1：build_app() 统一装配
├── main.py              # 文档 1：桌面启动入口
├── data.py              # 文档 4：示例数据 + build_routes()
├── run_showcase.py      # 文档 4：无头多场景验证
├── README.md            # 文档 4
└── screens/
    ├── __init__.py      # 文档 1
    ├── home.py          # 文档 2：HomeScreen
    └── system.py        # 文档 3：Shade / Lock / Recents
```

对应执行文档位于 `docs/vapor-os/`（四份：项目骨架、主屏、系统交互、应用接入）。

## 验证

```bash
cd /workspace/vapor-android
PYTHONPATH=python python3 -m pytest python/tests -q
```

总装测试：`python/tests/test_vapor_os_app.py`；主屏：`test_vapor_os_home.py`；
启动：`test_vapor_os_boot.py`；系统组件：`test_vos_*.py`。
