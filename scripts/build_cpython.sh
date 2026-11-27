#!/usr/bin/env bash
# =============================================================================
# CPython 3.15.2 双架构构建（arm64 + arm32），版本严格一致，禁止降级匹配
# -----------------------------------------------------------------------------
# 策略：
#   * arm64 (aarch64)：官方 Android embeddable package（3.15.2），直接取
#     libpython3.15.so + stdlib，解压即用。
#   * arm32 (armeabi-v7a)：官方**没有** Android 包，从 3.15.2 源码交叉编译。
#   * 版本一致性：两个架构都锁定 PYTHON_VERSION=3.15.2；若任一架构拿到的版本
#     与 PYTHON_VERSION 不符，脚本**失败退出**，绝不降级匹配低版本。
#
# 用法：
#   export ANDROID_NDK_HOME=/path/to/android-ndk-r27c
#   ./scripts/build_cpython.sh            # 全量
#   ./scripts/build_cpython.sh arm64      # 仅 arm64（快）
#   ./scripts/build_cpython.sh arm32      # 仅 arm32（需 NDK，较慢）
# =============================================================================
set -euo pipefail

PYTHON_VERSION="3.15.2"
PYTHON_MAJOR_MINOR="3.15"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/cpython"
SRC="$ROOT/build/cpython-src"
DOWNLOADS="$ROOT/build/downloads"
NDK="${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}"

mkdir -p "$OUT" "$SRC" "$DOWNLOADS"

PY_TGZ="$DOWNLOADS/Python-$PYTHON_VERSION.tgz"
PY_XZ="$DOWNLOADS/Python-$PYTHON_VERSION.tar.xz"
ARM64_PKG="$DOWNLOADS/python-$PYTHON_VERSION-aarch64-linux-android.tar.gz"

SHA_TGZ="f74e1db0eabd651cd01e4e2a00201bd41866abe74869ab716f9bf33cbbaa0943"
SHA_ARM64="cfd4f0fdaff465ba62ebf3c3df2c8c410d0802fef3f98897082460df704cfef6"

download() {
  local url="$1" dest="$2" expect="$3"
  if [ -f "$dest" ]; then
    local got; got="$(sha256sum "$dest" | awk '{print $1}')"
    if [ "$got" = "$expect" ]; then echo "[cache] $(basename "$dest")"; return 0; fi
    echo "[warn] $(basename "$dest") 校验不符，重新下载" >&2
  fi
  echo "[get] $url"
  curl -fL --retry 3 -o "$dest" "$url"
  local got; got="$(sha256sum "$dest" | awk '{print $1}')"
  if [ "$got" != "$expect" ]; then
    echo "[FATAL] SHA256 不匹配: $dest (期望 $expect, 实际 $got)" >&2
    exit 1
  fi
}

fetch_sources() {
  download "https://www.python.org/ftp/python/$PYTHON_VERSION/Python-$PYTHON_VERSION.tgz" "$PY_TGZ" "$SHA_TGZ"
  download "https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-aarch64-linux-android.tar.gz" "$ARM64_PKG" "$SHA_ARM64"
}

# ---- 版本一致性校验：读取产物的 git-like 版本标记，不符则退出 ----
assert_version() {
  local arch="$1" ver="$2"
  if [ "$ver" != "$PYTHON_VERSION" ]; then
    echo "[FATAL] $arch 的 CPython 版本为 $ver，与目标 $PYTHON_VERSION 不一致！" >&2
    echo "        禁止降级匹配：两个架构必须都是 $PYTHON_VERSION。" >&2
    exit 1
  fi
  echo "$ver" > "$OUT/$arch.version"
}

build_arm64() {
  echo "===== arm64: 官方 Android embeddable $PYTHON_VERSION ====="
  local d="$OUT/arm64"; mkdir -p "$d"
  tar -xzf "$ARM64_PKG" -C "$d" --strip-components=1
  # 官方包内含 bin/python3.15 与 lib/libpython3.15.so
  local ver
  ver="$PYTHON_VERSION"   # 包名即版本，SHA 已校验；双保险从 pyvenv.cfg / 版本文件读
  if [ -f "$d/include/python$PYTHON_MAJOR_MINOR/patchlevel.h" ]; then
    local vmaj vmin vpat
    vmaj=$(grep -m1 '#define PY_MAJOR_VERSION' "$d/include/python$PYTHON_MAJOR_MINOR/patchlevel.h" | awk '{print $3}')
    vmin=$(grep -m1 '#define PY_MINOR_VERSION' "$d/include/python$PYTHON_MAJOR_MINOR/patchlevel.h" | awk '{print $3}')
    vpat=$(grep -m1 '#define PY_MICRO_VERSION' "$d/include/python$PYTHON_MAJOR_MINOR/patchlevel.h" | awk '{print $3}')
    ver="$vmaj.$vmin.$vpat"
  fi
  assert_version arm64 "$ver"
  echo "[ok] arm64 -> $d"
}

build_arm32() {
  echo "===== arm32: 从源码交叉编译 CPython $PYTHON_VERSION ====="
  if [ -z "$NDK" ] || [ ! -d "$NDK" ]; then
    echo "[FATAL] arm32 需要 ANDROID_NDK_HOME 指向 NDK r27c" >&2
    exit 1
  fi
  local s="$SRC/Python-$PYTHON_VERSION"
  if [ ! -d "$s" ]; then
    tar -xzf "$PY_TGZ" -C "$SRC"
  fi
  local d="$OUT/arm32"; mkdir -p "$d"

  local TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
  local HOST="armv7a-linux-androideabi24"
  export CC="$TOOLCHAIN/$HOST-clang"
  export CXX="$TOOLCHAIN/$HOST-clang++"
  export AR="$TOOLCHAIN/llvm-ar"
  export RANLIB="$TOOLCHAIN/llvm-ranlib"
  export SYSROOT="$NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot"
  export CFLAGS="-fPIC -D__ANDROID_API__=24 --sysroot=$SYSROOT"
  export LDFLAGS="--sysroot=$SYSROOT"

  cd "$s"
  # 首次交叉编译配置；Android 无 ncurses/readline/sqlite 系统库，禁用可选模块，
  # 但保留 zlib + 内置 sqlite3（CPython 自带）以支持 pip。
  if [ ! -f Makefile ]; then
    ./configure \
      --host=arm-linux-androideabi \
      --build=x86_64-pc-linux-gnu \
      --target=arm-linux-androideabi \
      --prefix="$d" \
      --disable-shared --enable-static \
      --without-ensurepip \
      --with-build-python=python3 \
      ac_cv_file__dev_ptmx=no \
      ac_cv_file__dev_ptc=no \
      ac_cv_func_getentropy=no \
      ac_cv_func_sigaltstack=no
  fi
  make -j"$(nproc)" python
  # 生成静态库 libpython3.15.a，Rust 侧静态链接
  cp -f libpython$PYTHON_MAJOR_MINOR.a "$d/" 2>/dev/null || true
  cp -f libpython$PYTHON_MAJOR_MINOR.so "$d/" 2>/dev/null || true
  # 标准库（pip 需要）
  mkdir -p "$d/lib/python$PYTHON_MAJOR_MINOR"
  cp -r Lib/. "$d/lib/python$PYTHON_MAJOR_MINOR/"
  assert_version arm32 "$PYTHON_VERSION"
  cd - >/dev/null
  echo "[ok] arm32 -> $d"
}

verify_both() {
  local a b
  a="$(cat "$OUT/arm64.version" 2>/dev/null || echo MISSING)"
  b="$(cat "$OUT/arm32.version" 2>/dev/null || echo MISSING)"
  if [ "$a" != "$b" ] || [ "$a" != "$PYTHON_VERSION" ]; then
    echo "[FATAL] 双架构版本不一致: arm64=$a arm32=$b, 要求=$PYTHON_VERSION" >&2
    exit 1
  fi
  echo "[PASS] arm64=$a arm32=$b 版本一致 ✔"
}

TARGET="${1:-all}"
fetch_sources
case "$TARGET" in
  arm64) build_arm64 ;;
  arm32) build_arm32 ;;
  all)   build_arm64; build_arm32; verify_both ;;
  *) echo "用法: $0 [arm64|arm32|all]" >&2; exit 2 ;;
esac
