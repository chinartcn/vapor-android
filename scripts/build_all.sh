#!/usr/bin/env bash
# =============================================================================
# Vapor 一键构建：CPython 3.15.2 双架构 + Rust 双架构 + 两个 APK
# -----------------------------------------------------------------------------
# 前置：export ANDROID_HOME=/path/to/android-sdk
#       export ANDROID_NDK_HOME=/path/to/android-ndk-r27c
#       （SDK 需装 platform-tools, platforms;android-35, build-tools;35.0.0）
#       rustup target add aarch64-linux-android armv7-linux-androideabi
#       cargo install cargo-ndk
#
# 产物：
#   build/output/VaporRenderer.apk   纯渲染器（arm32+arm64 fat APK）
#   build/output/VaporPackager.apk   纯打包器（arm32+arm64 fat APK）
#   build/output/rust.Vapor.whl      内置 Python 包
#   build/pyext/<abi>/Vapor/*.so     Vapor._core / _ui / _sdk 原生扩展
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON_VERSION="3.15.2"
OUT="$ROOT/build/output"
BUILD="$ROOT/build"
mkdir -p "$OUT"

ANDROID_HOME="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
NDK="${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}"
BUILD_TOOLS="$ANDROID_HOME/build-tools/35.0.0"
PLATFORM="$ANDROID_HOME/platforms/android-35/android.jar"

echo "========== [1/7] 检查前置条件 =========="
[ -n "$ANDROID_HOME" ] || { echo "缺少 ANDROID_HOME" >&2; exit 1; }
[ -n "$NDK" ] || { echo "缺少 ANDROID_NDK_HOME" >&2; exit 1; }
[ -f "$PLATFORM" ] || { echo "缺少 $PLATFORM（sdkmanager \"platforms;android-35\"）" >&2; exit 1; }
[ -x "$BUILD_TOOLS/aapt2" ] || { echo "缺少 aapt2（sdkmanager \"build-tools;35.0.0\"）" >&2; exit 1; }
echo "SDK=$ANDROID_HOME"
echo "NDK=$NDK"

echo "========== [2/7] 构建 CPython $PYTHON_VERSION 双架构（版本严格一致） =========="
./scripts/build_cpython.sh all
python3 scripts/check_cpython_version.py

echo "========== [3/7] Rust 双架构交叉编译（cargo-ndk） =========="
rustup target add aarch64-linux-android armv7-linux-androideabi 2>/dev/null || true
command -v cargo-ndk >/dev/null || { echo "缺少 cargo-ndk（cargo install cargo-ndk）" >&2; exit 1; }
# 渲染器：两个架构都产出 libvapor.so
# 注意：新版 cargo-ndk 要求 -p 放在 build 之后（转发给 cargo）
cargo ndk -t arm64-v8a -t armeabi-v7a -o "$BUILD/jniLibs/renderer" \
    build --release -p vapor-renderer
# 打包器：两个架构都产出 libvapor_packager.so
cargo ndk -t arm64-v8a -t armeabi-v7a -o "$BUILD/jniLibs/packager" \
    build --release -p vapor-packager

echo "========== [4/7] 把 CPython 运行库注入 jniLibs =========="
# 不同来源的 libpython 位置不同：
#   arm64 官方包: lib/libpython3.15.so（不是 prefix/lib）
#   arm32 源码构建: build/cpython/arm32（根目录）或 arm32/lib
#   仓库预置: lib/<abi>/libpython3.15.so
copy_libpython() {
  local arch="$1" jni="$2"; shift 2
  local dir found=""
  mkdir -p "$jni"
  for dir in "$@"; do
    if compgen -G "$dir/libpython*.so*" >/dev/null 2>&1; then
      cp -fL "$dir"/libpython*.so* "$jni/"
      found="$dir"
      break
    fi
  done
  if [ -z "$found" ]; then
    echo "[FATAL] 找不到 $arch 的 libpython3.15.so（检查 build/cpython 或 lib/）" >&2
    exit 1
  fi
  echo "[ok] $arch libpython <- $found"
}
copy_libpython arm64 "$BUILD/jniLibs/renderer/arm64-v8a" \
  "$BUILD/cpython/arm64/lib" "$BUILD/cpython/arm64/prefix/lib" "$ROOT/lib/arm64-v8a"
copy_libpython arm32 "$BUILD/jniLibs/renderer/armeabi-v7a" \
  "$BUILD/cpython/arm32/lib" "$BUILD/cpython/arm32" "$ROOT/lib/armeabi-v7a"
cp -r "$BUILD/jniLibs/renderer/." "$BUILD/jniLibs/packager/"

echo "========== [5/7] 把 rust.Vapor 内置 Python 包 + 逻辑示例放进 assets =========="
python3 -m pip wheel ./python -w "$OUT" --no-deps
ASSETS="$ROOT/android/renderer/app/src/main/assets"
ASSETS_PY="$ASSETS/python"
mkdir -p "$ASSETS_PY"
rm -rf "$ASSETS_PY/rust" "$ASSETS_PY/site-packages"
cp -r python/rust "$ASSETS_PY/"
rm -rf "$ASSETS/app"
mkdir -p "$ASSETS/app"
# 默认示例换成 Showcase（页面栈/预设组件/自定义组件/权限演示）
cp -r protocol/showcase/. "$ASSETS/app/"

echo "========== [6/7] 打包 CPython 标准库（含 lib-dynload）+ PyO3 扩展 =========="
# 定位某架构产物中的标准库目录（lib/python3.15，含 os.py）。
find_stdlib_dir() {
  local root="$1" p
  for p in "$root/lib/python3.15" "$root/prefix/lib/python3.15"; do
    [ -f "$p/os.py" ] && { echo "$p"; return 0; }
  done
  find "$root" -maxdepth 5 -type f -path '*/python3.15/os.py' 2>/dev/null | head -1 | xargs -r dirname
}
ARM64_STD="$(find_stdlib_dir "$BUILD/cpython/arm64")"
ARM32_STD="$(find_stdlib_dir "$BUILD/cpython/arm32")"
[ -n "$ARM64_STD" ] || { echo "[FATAL] 缺少 arm64 标准库（先运行 scripts/build_cpython.sh）" >&2; exit 1; }
[ -n "$ARM32_STD" ] || { echo "[FATAL] 缺少 arm32 标准库（arm32 需源码交叉编译）" >&2; exit 1; }

# 清掉上一轮产物，避免残留旧 stdlib / 旧 .so。
rm -rf "$ASSETS_PY/stdlib"
mkdir -p "$ASSETS_PY/stdlib"
# 纯 Python 标准库用 arm64 的（架构无关）；先整体复制再剔除架构相关的 lib-dynload。
cp -r "$ARM64_STD/." "$ASSETS_PY/stdlib/"
rm -rf "$ASSETS_PY/stdlib/lib-dynload" "$ASSETS_PY/stdlib/lib-dynload_"*
find "$ASSETS_PY/stdlib" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# 各自的 lib-dynload 按 ABI 分目录；Rust 按当前进程指针宽度选择（python.rs）。
if [ ! -d "$ARM64_STD/lib-dynload" ]; then
  echo "[FATAL] arm64 标准库缺少 lib-dynload（math 等模块不可用）" >&2; exit 1
fi
cp -r "$ARM64_STD/lib-dynload" "$ASSETS_PY/stdlib/lib-dynload_arm64"
if [ ! -d "$ARM32_STD/lib-dynload" ]; then
  echo "[FATAL] arm32 标准库缺少 lib-dynload；请用源码交叉编译生成（VAPOR_REBUILD_CPYTHON=1）" >&2
  exit 1
fi
cp -r "$ARM32_STD/lib-dynload" "$ASSETS_PY/stdlib/lib-dynload_arm32"

# 关键校验：真机报 ModuleNotFoundError: math 的根因就是这两处为空。
for abi in arm64 arm32; do
  if ! compgen -G "$ASSETS_PY/stdlib/lib-dynload_$abi/math.*.so" >/dev/null 2>&1; then
    echo "[FATAL] stdlib/lib-dynload_$abi 缺少 math 扩展模块，APK 会启动失败" >&2
    exit 1
  fi
done
echo "[ok] stdlib: $(du -sh "$ASSETS_PY/stdlib" | cut -f1)，lib-dynload arm64/arm32 均就绪"

# PyO3 扩展（Vapor._core / _ui / _sdk）
./scripts/build_android_pyext.sh
# 纯 Python 包放进 assets/python/site-packages；原生扩展按 ABI 分目录（Java 侧按机型拷贝）
mkdir -p "$ASSETS_PY/site-packages"
cp -r python/Vapor "$ASSETS_PY/site-packages/"
for abi in arm64-v8a armeabi-v7a; do
  rm -rf "$ASSETS_PY/pyext/$abi"
  mkdir -p "$ASSETS_PY/pyext/$abi"
  cp -r "build/pyext/$abi/." "$ASSETS_PY/pyext/$abi/"
done
# 同时装进本机 CPython 运行时树，便于本机/模拟器直接验证
for arch in arm64 arm32; do
  if [ -d "build/cpython/$arch" ]; then
    python3 scripts/install_to_cpython.py --home "build/cpython/$arch" --abi "$arch" || true
  fi
done

echo "========== [7/7] 组装两个 APK（aapt2 + javac + d8 + aapt + apksigner） =========="
./scripts/assemble_apk.sh renderer
./scripts/assemble_apk.sh packager

echo ""
echo "========== 构建完成 =========="
ls -lh "$OUT"/*.apk 2>/dev/null || true
echo "提示：APK 打包/签名细节见 scripts/assemble_apk.sh；真机 adb install 即可。"
