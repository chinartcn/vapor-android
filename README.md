# Vapor — Rust Android NativeActivity 通用渲染器

> 这是一个基于 Rust 的通用 Android NativeActivity 渲染器，通过稳定的 C ABI 暴露共享内存接口供 Python 等语言调用，内置基础 UI、文件系统与扩展加载机制，并集成 pip 以支持动态安装 C/Python ABI 扩展。

- **目标架构**：arm32 (`armeabi-v7a`) + arm64 (`arm64-v8a`)，**同一个 fat APK 同时打包两个架构**。
- **CPython**：双架构统一锁定 **3.15.2**（最新稳定版）。arm64 用官方 Android 预编译包；arm32 官方没有对应包，**从 3.15.2 源码交叉编译**，绝不降级匹配低版本。`scripts/check_cpython_version.py` 强制校验两架构版本一致。
- **扩展理念**：Rust 只提供稳定 ABI 基座。**做完这一次后，加功能只需写一个 C/Python 扩展**，不改 Rust 源码。

---

## 1. 目录结构

```
vapor/
├── include/vapor_abi.h              # 稳定 C ABI 契约（唯一对外接口）
├── crates/
│   ├── vapor-abi/                   # ABI 类型的 Rust 镜像（repr(C) 严格对齐）
│   ├── vapor-core/                  # 核心：帧缓冲/GLES/文件系统/扩展/Python嵌入
│   ├── vapor-renderer/              # app1 纯渲染器 → libvapor.so
│   └── vapor-packager/              # app2 纯打包器 → libvapor_packager.so
│       ├── src/axml.rs              # 二进制 AndroidManifest.xml 改写器
│       ├── src/sign.rs              # 纯 Rust APK v1 签名
│       └── src/jni_bridge.rs        # Java ↔ Rust 桥接
├── rust/                            # 三个 maturin(PyO3) crate：响应式框架
│   ├── vapor-reactive/              # → Vapor._core  信号图 + 2D 光栅化
│   ├── vapor-ui/                    # → Vapor._ui    SVG path / 矩阵 / 文本度量
│   └── vapor-sdk/                   # → Vapor._sdk   dlopen C ABI → JNI
├── python/rust/                     # 内置 Python 包：import rust.Vapor
│   ├── Vapor.py                     # 基础渲染 API（共享内存绘图）
│   ├── _abi.py                      # ctypes C ABI 绑定 + 回退模式
│   ├── fs.py / extension.py / piputil.py / ui.py
├── python/Vapor/                    # 响应式框架：core / template / ui / sdk / svg
│   ├── core.py                      # ref / reactive / computed / watch / effectScope
│   ├── template.py                  # v-if / v-for / v-model / 插槽 编译器
│   ├── ui/                          # 组件 / 布局 / 事件 / 页面栈 / Canvas
│   │   ├── __init__.py              # 统一再导出（保持 import Vapor.ui as V）
│   │   ├── base.py colors.py text.py canvas.py renderer.py
│   │   ├── layout.py paint.py input.py app.py
│   │   └── navigation.py widgets.py
│   ├── svg.py                       # SVG 解析与矢量渲染
│   ├── sdk.py                       # Toast / Vibrator / Clipboard / Prefs ...
│   └── _fallback.py / _fallback_ui.py  # 无原生扩展时的纯 Python 实现
├── protocol/example/                # 逻辑包协议示例
│   ├── main.py                      # 启动入口（固定）
│   ├── main.json                    # 配置文件（固定）
│   └── lib/abi.json                 # 扩展加载清单
│       ├── hello_c.c                # C ABI 扩展示例
│       └── hello_py.py              # Python 扩展示例
├── android/{renderer,packager}/     # 两个 Android app 的 Manifest/Java/Gradle
├── scripts/
│   ├── build_cpython.sh             # CPython 3.15.2 双架构构建（版本强一致）
│   ├── check_cpython_version.py     # 版本一致性校验（禁止降级）
│   ├── build_all.sh                 # 一键构建
│   └── assemble_apk.sh              # aapt2/d8/apksigner 组装 fat APK
└── docs/ARCHITECTURE.md             # 架构详解
```

## 2. 核心概念

| 概念 | 说明 |
|---|---|
| 共享内存帧缓冲 | Rust 持有 RGBA8888 像素缓冲；`vapor_fb_lock()` 返回裸指针，Python 用 `memoryview` 零拷贝写像素；`vapor_fb_unlock()` 由 GLES 上屏 |
| 稳定 C ABI | `include/vapor_abi.h` 定义全部导出函数；`VaporApi` 函数表让 C 扩展拿到全部能力 |
| 扩展机制 | C 扩展导出 `int vapor_ext_init(VaporApi*)`；Python 扩展放 `lib/`；`abi.json` 管理加载顺序/架构/开关 |
| 逻辑包协议 | `main.py` 启动入口、`main.json` 配置、`lib/` 扩展目录、`abi.json` 加载清单 |
| 内置 UI | 默认显示 IMPORT / FILES / SETTINGS；Rust 与 Python 两套等价实现，可被扩展替换 |

## 3. 本机已验证（本轮完成）

```bash
# Rust 编译 + 单元测试（ABI 布局、AXML 改写、签名）
cd vapor && cargo test --workspace

# Python 端到端：import rust.Vapor + 真实 libvapor.so 共享内存读写 + 文件系统
VAPOR_NATIVE_LIB=target/debug/libvapor.so \
PYTHONPATH=python python3 python/tests/test_e2e.py

# C 扩展经 VaporApi 全功能表调用 + 符号解析
VAPOR_NATIVE_LIB=target/debug/libvapor.so \
PYTHONPATH=python python3 python/tests/test_ext.py

# 打包器端到端：空壳 APK + 逻辑 zip → 签名 APK（包名/版本/名称/图标替换）
cargo run -p vapor-packager --example demo_pack
```

全部通过 ✔。

## 4. 构建可安装 APK（下一轮 / 需 Android SDK）

```bash
export ANDROID_HOME=/path/to/android-sdk
export ANDROID_NDK_HOME=/path/to/android-ndk-r27c
sdkmanager "platforms;android-35" "build-tools;35.0.0" "platform-tools"
rustup target add aarch64-linux-android armv7-linux-androideabi
cargo install cargo-ndk

./scripts/build_all.sh
# 产物：build/output/VaporRenderer.apk + VaporPackager.apk（均含 arm32+arm64）
```

> 本轮已在后台下载 commandline-tools 与 NDK r27c 到 artifact 目录；SDK platform/build-tools 留待下一轮安装后即可走完 APK 组装。
>
> **CPython 复用**：仓库 `lib/{arm64-v8a,armeabi-v7a}/libpython3.15.so` 是从已构建 APK
> 提取的正式产物，`scripts/build_cpython.sh` 会优先复用它们——arm32 不再从源码交叉编译
> （那需要同版本宿主解释器），标准库直接复用 arm64 的纯 Python 部分。需要强制重建时设置
> `VAPOR_REBUILD_CPYTHON=1`。

## 5. 两个 app

- **VaporRenderer（纯渲染器）**：`libvapor.so` + 内置 `rust.Vapor` + 默认 UI，加载逻辑包运行。可导入外部逻辑 zip。
- **VaporPackager（纯打包器）**：Android app（可引入依赖，已含 AndroidX/Material）。选空壳 APK + 逻辑 zip，填应用名/版本/包名/图标，输出 `/storage/emulated/0/rustoutputapk`，无权限降级私有目录。核心是 Rust（`libvapor_packager.so`），二进制 AXML 改写 + v1 签名纯 Rust 实现。

## 6. 加功能 = 写一个扩展

**Python 扩展**（`lib/hello_py.py` + `abi.json` 声明 kind=python）：
```python
import rust.Vapor as V
def on_load():
    V.text("MY FEATURE", 24, 100, V.rgb(255,255,255), 2)
on_load()
```

**C 扩展**（`lib/hello_c.c`，导出 `vapor_ext_init`）：
```c
int32_t vapor_ext_init(VaporApi* api) {
    VaporFramebuffer fb;
    if (api->fb_lock(&fb) == VAPOR_OK) {
        fb.pixels[0] = 255; /* 直接写共享内存 */
        api->fb_unlock();
    }
    return VAPOR_OK;  /* 通过 api 函数表可调用渲染器全部能力 */
}
```

无需重编译 Rust。详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 7. JNI 桥接（C / Python 扩展调用任意 JNI）

新增 JNI 桥接层，让扩展能触达 Java/Android 侧的一切能力。核心是 `vapor_jni_call(req_json, out_json, cap)` 一条稳定 C ABI，**不链接 libjvm**、arm32/arm64 通用（函数表偏移由编译器按指针宽度自动算）。

- **Python**：`from rust import jni`，`jni.call_static("java/lang/Math","max","(II)I",[jni.i(3),jni.i(7)])` → `7`；对象用句柄 + `with` 自动释放。
- **C 扩展**：`api->jni_call(JSON,...)` 或 `api->jni_get_env()` 拿裸 `JNIEnv*` 直接调标准 JNI。
- **VM 自动注入**：NativeActivity 入口从 `JNIEnv::GetJavaVM` 取出 `JavaVM*` 注入，扩展开箱即用。

示例：`protocol/example/lib/hello_jni.c`。完整文档与 18 项本机 JVM 验证见 [docs/JNI_BRIDGE.md](docs/JNI_BRIDGE.md)（`python/tests/test_jni.py`）。

---

## 8. 响应式 UI 框架（无 vdom）

在渲染器之上还有一层 **Vue 3 Vapor 风格**的响应式 UI 框架：没有虚拟 DOM、
没有 diff、没有组件级 re-render。写一个值，只有依赖它的那个 effect 重跑，
只改它负责的那个属性，绘制层再把当前值画成像素。

```python
import Vapor.ui as V

class Counter(V.Component):
    template = '''
      <View bg="#0B0E13" padding="24" gap="16" w="100%" h="100%">
        <Text :size="64" :color="accent" align="center">{{ count }}</Text>
        <Button text="+1" :on_click="inc" />
      </View>
    '''
    def setup(self, props, ctx):
        count = V.ref(0)
        def inc(e=None): count.value += 1
        return {
            "count": count,
            "accent": V.computed(lambda: "#7BD88F" if count.value > 0 else "#E6EAF0"),
            "inc": inc,
        }

V.create_app(Counter, width=360, height=640).mount()
```

| 层 | 内容 |
|---|---|
| `Vapor.core` | `ref` / `reactive` / `computed` / `watch` / `effect_scope` / `provide` / `inject`（Vue 3 API 对齐） |
| `Vapor.template` | `v-if` / `v-else-if` / `v-else`、`v-for`（keyed）、`v-show`、`v-model`、`v-text`、`:prop`、`@event`、插槽、`{{ }}` 插值 |
| `Vapor.ui` | 组件、flex 布局、Canvas 绘制、触控手势（点击/拖动/长按/惯性滚动/双指）、事件冒泡、`Page` / `PageStack` / `Navigator`、`Scroll` |
| `Vapor.svg` | SVG 当组件渲染（path/rect/circle/ellipse/line/polygon + transform，矢量光栅化） |
| `Vapor.sdk` | 经稳定 C ABI 调 JNI：Toast / 振动 / 剪贴板 / Intent / SharedPreferences / 设备信息 |
| `Vapor.font` | 字体扩展：TTF/OTF/TTC 真实字形（含中文）、按字符回退、测量与绘制同源 |
| `Vapor.component` | 组件 ABI：组件提供者（manifest/register）+ C 外壳 + 模板生成，可热插拔分发 |
| `Vapor.presets` | 内建现代化圆角组件：Card / Fab / Chip / SegmentedControl / Dialog / ProgressRing …（底层 Canvas 直绘） |

三个原生模块由 **maturin** 打包（PyO3 + abi3-py310）：

```bash
maturin develop --manifest-path rust/vapor-reactive/Cargo.toml   # Vapor._core
maturin develop --manifest-path rust/vapor-ui/Cargo.toml         # Vapor._ui
maturin develop --manifest-path rust/vapor-sdk/Cargo.toml        # Vapor._sdk
python -c "import Vapor; print(Vapor.status())"                  # 应为 rust
```

没有原生扩展时会自动回退到纯 Python 实现（`Vapor.status()` 显示
`python-fallback`），所以开发机上不编译 Rust 也能跑：

```bash
PYTHONPATH=python python3 python/tests/test_vapor_core.py
PYTHONPATH=python python3 python/tests/test_vapor_ui.py
PYTHONPATH=python python3 python/tests/test_vapor_sdk.py
VAPOR_DEMO_FRAMES=3 PYTHONPATH=python python3 python/examples/counter.py
```

Android 侧（不装 SDK 也能做，全部走 CI）：

```bash
./scripts/build_android_pyext.sh                 # 交叉编译双 ABI 的 _core/_ui/_sdk
python3 scripts/install_to_cpython.py --home build/cpython/arm64 --abi arm64
```

`scripts/build_all.sh` 的第 6 步会自动执行上面两步，并把扩展按 ABI 放进 APK 的
`assets/python/pyext/`，由 `RendererActivity` 按机型复制进 `site-packages`。

内建预设组件（导入即注册，全部用底层 Canvas 画圆角/阴影/圆/进度弧）：

```python
import Vapor.presets           # 注册 <Card/> <Fab/> <Chip/> <Dialog/> <ProgressRing/> ...
```

按压反馈是通用的：手指按住任意组件时命中链上的节点都会收到 `press` 并把
`node.pressed` 置真，抬起时收到 `release`；预设组件据此做高亮，自定义组件可以直接读
`node.pressed` 或绑 `:on_press` / `:on_release` / `:on_hover`。

**权限策略**：`AndroidManifest.xml` 一次性声明全部可能用到的权限（59 条，声明≠授予），
危险权限只在真正调用对应 SDK 能力时由 `Vapor.sdk.Permissions.ensure(...)` 弹窗申请
（`Location` / `Notifications` 等已内置懒申请）。

组件扩展（ABI v1，模板生成 + 校验 + 热插拔）：

```bash
python -m Vapor.component MyCard              # 生成 lib/MyCard.py（含 manifest/register）
python -m Vapor.component MyCard --kind c     # 生成 C 外壳骨架
```

组件本体是 `Vapor.ui.Component` 子类，提供者模块暴露 `ABI` / `manifest()` / `register(host)`；
C 扩展导出 `vapor_component_init(VaporApi*)`，用 `dladdr` 定位自身目录后注册同目录的 Python
组件包。契约与校验规则见 [docs/COMPONENT_ABI.md](docs/COMPONENT_ABI.md)。

CI：`.github/workflows/ci.yml`（3 OS × Python 3.10/3.13 的 maturin 构建与测试、
Android 双 ABI 交叉编译、tag 触发的 wheel 构建）、`.github/workflows/android.yml`
（手动触发，可产出两个 APK）。完整设计见 [docs/REACTIVE.md](docs/REACTIVE.md)。

