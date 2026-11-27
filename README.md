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
├── python/rust/                     # 内置 Python 包：import rust.Vapor
│   ├── Vapor.py                     # 基础渲染 API（共享内存绘图）
│   ├── _abi.py                      # ctypes C ABI 绑定 + 回退模式
│   ├── fs.py / extension.py / piputil.py / ui.py
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
