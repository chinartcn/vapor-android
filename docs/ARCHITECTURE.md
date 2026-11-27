# Vapor 架构详解

## 一、分层与数据流

```
Java Activity (NativeActivity / PackagerActivity)
        │ JNI: nativeInit / nativePackage
        ▼
libvapor.so (vapor-renderer) ── 重导出 ── vapor-core ── vapor-abi
        │                                    │
        │ 稳定 C ABI                          │ dlopen libpython3.15.so
        ▼                                    ▼
Python ctypes (rust._abi)              CPython 3.15.2 内嵌解释器
        │                                    │
        ▼                                    ▼
rust.Vapor (Vapor.py/fs/ext/pip/ui)     逻辑包 main.py + lib/ 扩展
```

**一帧的生命周期**：
1. Python `V.rect()` / `V.pixel()` 通过 `memoryview` 直接写 Rust 持有的 RGBA8888 共享内存（零拷贝）。
2. `V.present()` → `vapor_fb_unlock()` → Rust `renderer::present()` 把帧缓冲作为纹理 `glTexSubImage2D` 上传，GLES 3.0 全屏四边形绘制并 `eglSwapBuffers`。
3. 触摸事件由 NativeActivity 推入 Rust 事件队列，Python `V.poll_event()` 取出。

## 二、稳定 C ABI（`include/vapor_abi.h`）

分类导出：

| 类别 | 函数 |
|---|---|
| 版本/生命周期 | `vapor_abi_version`, `vapor_version`, `vapor_cpython_version`, `vapor_init`, `vapor_shutdown` |
| 共享内存帧缓冲 | `vapor_fb_lock`, `vapor_fb_unlock`, `vapor_fb_resize`, `vapor_fb_clear` |
| 事件/窗口 | `vapor_poll_event`, `vapor_event_count`, `vapor_set_title`, `vapor_set_bg_color` |
| 文件系统 | `vapor_fs_read/write/exists/list/free`, `vapor_fs_app_dir/private_dir/external_dir` |
| Python | `vapor_py_init/run_file/eval/install_requirements` |
| 扩展 | `vapor_ext_load/load_c/load_py/symbol` |
| 全功能表 | `vapor_api()` → `const VaporApi*`，`vapor_log` |

`VaporApi` 函数表包含上述所有函数指针，C 扩展 init 时拿到它即可调用渲染器全部能力。`crates/vapor-abi/src/lib.rs` 有布局单元测试保证与 C 头文件逐字节一致。

## 三、CPython 双架构一致性（硬约束）

- 目标版本：**3.15.2**（当前最新稳定版，2026-10-25 发布）。
- arm64：官方 `python-3.15.2-aarch64-linux-android.tar.gz`（SHA256 已校验）。
- arm32：官方**无** Android 包 → 从 `Python-3.15.2.tgz` 源码交叉编译（NDK clang，`--host=arm-linux-androideabi`）。
- `scripts/build_cpython.sh` 对每个架构写 `{arch}.version` 标记，`check_cpython_version.py` 比对两者与 3.15.2，**不一致即非零退出，禁止降级匹配**。
- Rust 侧通过 `dlopen("libpython3.15.so")` 动态绑定，同一份 so 自动适配对应架构的 libpython。

## 四、扩展机制

### C 扩展
- `.so` 导出 `int32_t vapor_ext_init(VaporApi* api)`。
- 加载用 `dlopen(RTLD_NOW|RTLD_GLOBAL)`，因此扩展导出的符号可通过 `vapor_ext_symbol()`（`dlsym(RTLD_DEFAULT)`）解析，实现"啥都能调"。
- 本机已验证：示例扩展经函数表画像素（RGB 255,0,255）+ 调用日志 + `vapor_c_add(3,4)=7`。

### Python 扩展
- 纯 `.py` 或 CPython 模块 `.so`，`importlib.import_module` 加载。
- 用 `rust.Vapor` 的公共 API 即可做任何渲染/文件/扩展操作。

### abi.json（lib/ 下）
```json
{
  "schema": 1,
  "extensions": [
    {"name": "mylib", "kind": "c", "path": "libmylib.so", "symbol": "vapor_ext_init", "arch": "all", "enabled": true},
    {"name": "myui", "kind": "python", "path": "myui.py", "arch": "arm64", "enabled": true}
  ]
}
```
`arch` 可限定 `arm32`/`arm64`/`all`；`enabled` 控制开关。

## 五、逻辑包协议

| 文件 | 作用 |
|---|---|
| `main.py` | 启动入口（宿主 `vapor_py_run_file` 用 `runpy.run_path(..., run_name='__main__')` 执行） |
| `main.json` | 配置（窗口尺寸/主题/权限开关） |
| `lib/` | C/Python ABI 扩展存放目录 |
| `lib/abi.json` | 扩展加载清单 |

## 六、打包器（app2）

把"真·空壳渲染器 APK"与"逻辑 zip"合并：
1. 解包空壳 APK（本身是 zip），丢弃旧签名。
2. `axml.rs` 改写二进制 `AndroidManifest.xml` 的 string pool（包名/应用名/版本名）+ `versionCode` 整数。
3. 替换 `res/**/ic_launcher*.png` 应用图标。
4. 注入 `assets/vapor_logic.zip` 与 `assets/vapor_meta.json`。
5. `sign.rs` 纯 Rust 生成 MANIFEST.MF / .SF / .RSA（v1 JAR 签名）。
6. Java 层输出到 `/storage/emulated/0/rustoutputapk`，无权限回退 `getExternalFilesDir()`。

本机已验证：明文 XML 回退路径 + 二进制 AXML 路径（单元测试构造最小合法 AXML）+ 图标替换 + 签名产物结构。

## 七、本机验证完整命令（可复现）

```bash
cd /workspace/vapor
# 1. Rust 编译与单元测试
cargo test --workspace

# 2. Python 端到端（真实原生库）
VAPOR_NATIVE_LIB=$PWD/target/debug/libvapor.so \
PYTHONPATH=$PWD/python python3 python/tests/test_e2e.py

# 3. C 扩展示例编译
gcc -shared -fPIC -Iinclude protocol/example/lib/hello_c.c \
    -o protocol/example/lib/libhello_c.so

# 4. C 扩展"啥都能调"验证（测试文件已固化在 python/tests/test_ext.py）
VAPOR_NATIVE_LIB=$PWD/target/debug/libvapor.so \
PYTHONPATH=$PWD/python python3 python/tests/test_ext.py

# 5. 打包器端到端
cargo run -p vapor-packager --example demo_pack
```

## 八、已知边界 / 下一轮

- 本轮交付完整工程 + 本机验证；**Android SDK platform/build-tools 未安装**，故未在本机产出真 APK。
- 后台已下载 commandline-tools 与 NDK r27c；下一轮安装 SDK 组件后 `./scripts/build_all.sh` 即可产出两个 fat APK。
- v1 签名为 JAR 标准；打包器内嵌密钥仅用于演示，生产请替换 `packager_assets/` 下密钥。
- Python 嵌入层已绑定 CPython C API 最小集；复杂逻辑全部放 Python 包，便于迭代。
