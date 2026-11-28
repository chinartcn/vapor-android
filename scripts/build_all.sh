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

echo "========== [1/6] 检查前置条件 =========="
[ -n "$ANDROID_HOME" ] || { echo "缺少 ANDROID_HOME" >&2; exit 1; }
[ -n "$NDK" ] || { echo "缺少 ANDROID_NDK_HOME" >&2; exit 1; }
[ -f "$PLATFORM" ] || { echo "缺少 $PLATFORM（sdkmanager \"platforms;android-35\"）" >&2; exit 1; }
[ -x "$BUILD_TOOLS/aapt2" ] || { echo "缺少 aapt2（sdkmanager \"build-tools;35.0.0\"）" >&2; exit 1; }
echo "SDK=$ANDROID_HOME"
echo "NDK=$NDK"

echo "========== [2/6] 构建 CPython $PYTHON_VERSION 双架构（版本严格一致） =========="
./scripts/build_cpython.sh all
python3 scripts/check_cpython_version.py

echo "========== [3/6] Rust 双架构交叉编译（cargo-ndk） =========="
rustup target add aarch64-linux-android armv7-linux-androideabi 2>/dev/null || true
command -v cargo-ndk >/dev/null || { echo "缺少 cargo-ndk（cargo install cargo-ndk）" >&2; exit 1; }
# 渲染器：两个架构都产出 libvapor.so
cargo ndk -t arm64-v8a -t armeabi-v7a -o "$BUILD/jniLibs/renderer" \
    -p vapor-renderer build --release
# 打包器：两个架构都产出 libvapor_packager.so
cargo ndk -t arm64-v8a -t armeabi-v7a -o "$BUILD/jniLibs/packager" \
    -p vapor-packager build --release

echo "========== [4/6] 把 CPython 运行库注入 jniLibs =========="
# arm64 官方包: libpython3.15.so 在 prefix/lib/ ; arm32 源码编译: 在输出根目录
for arch in arm64 arm32; do
  case "$arch" in
    arm64) jni="$BUILD/jniLibs/renderer/arm64-v8a"; src="$BUILD/cpython/arm64/prefix/lib" ;;
    arm32) jni="$BUILD/jniLibs/renderer/armeabi-v7a"; src="$BUILD/cpython/arm32" ;;
  esac
  mkdir -p "$jni"
  if [ -d "$src" ]; then
    find "$src" -maxdepth 1 -name 'libpython*.so' -exec cp -f {} "$jni/" \; 2>/dev/null || true
  fi
done
cp -r "$BUILD/jniLibs/renderer/." "$BUILD/jniLibs/packager/"

echo "========== [5/6] 把 rust.Vapor 内置 Python 包 + 逻辑示例放进 assets =========="
python3 -m pip wheel ./python -w "$OUT" --no-deps
# 解压 whl 取 rust/ 包，连同示例逻辑包放进渲染器 assets/python 与 assets/app
mkdir -p android/renderer/app/src/main/assets/python
cp -r python/rust android/renderer/app/src/main/assets/python/
mkdir -p android/renderer/app/src/main/assets/app
# 默认示例换成 Showcase（页面栈/预设组件/自定义组件/权限演示）
cp -r protocol/showcase/. android/renderer/app/src/main/assets/app/

echo "========== [6/7] PyO3 响应式框架扩展（Vapor._core / _ui / _sdk） =========="
./scripts/build_android_pyext.sh
# 纯 Python 包放进 assets/python/site-packages；原生扩展按 ABI 分目录（Java 侧按机型拷贝）
mkdir -p android/renderer/app/src/main/assets/python/site-packages
cp -r python/Vapor android/renderer/app/src/main/assets/python/site-packages/
for abi in arm64-v8a armeabi-v7a; do
  mkdir -p "android/renderer/app/src/main/assets/python/pyext/$abi"
  cp -r "build/pyext/$abi/." "android/renderer/app/src/main/assets/python/pyext/$abi/"
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
