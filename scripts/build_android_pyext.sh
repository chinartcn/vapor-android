#!/usr/bin/env bash
# =============================================================================
# 为 Android 交叉编译三个 PyO3 扩展（Vapor._core / Vapor._ui / Vapor._sdk）。
# -----------------------------------------------------------------------------
# 前置：ANDROID_NDK_HOME 指向 NDK（r27c+），cargo-ndk 已安装。
# 产物：build/pyext/<abi>/Vapor/{_core,_ui,_sdk}.so
#
# 三个 crate 都用 abi3（abi3-py310）+ extension-module，
# 因此交叉编译不需要目标机上有 Python 解释器（PYO3_NO_PYTHON=1）。
# 运行时由宿主 dlopen libpython3.15.so，ABI 稳定。
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

NDK="${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}"
API="${VAPOR_ANDROID_API:-24}"
OUT="$ROOT/build/pyext"
RAW="$OUT/raw"

if [ -z "$NDK" ] || [ ! -d "$NDK" ]; then
  echo "[FATAL] 需要 ANDROID_NDK_HOME 指向 Android NDK" >&2
  exit 1
fi
command -v cargo-ndk >/dev/null 2>&1 || {
  echo "[FATAL] 缺少 cargo-ndk（cargo install cargo-ndk）" >&2
  exit 1
}

export PYO3_CROSS=1
export PYO3_CROSS_PYTHON_VERSION=3.15
export PYO3_NO_PYTHON=1
export CARGO_TARGET_DIR="$ROOT/build/target-pyext"

echo "===== PyO3 扩展交叉编译（arm64-v8a + armeabi-v7a, API $API） ====="
rm -rf "$RAW"
for crate in vapor-reactive vapor-ui vapor-sdk; do
  echo "--- $crate ---"
  cargo ndk --platform "$API" -t arm64-v8a -t armeabi-v7a -o "$RAW" \
      build --release --manifest-path "rust/$crate/Cargo.toml"
done

for abi in arm64-v8a armeabi-v7a; do
  dst="$OUT/$abi/Vapor"
  mkdir -p "$dst"
  for lib in _core _ui _sdk; do
    src="$RAW/$abi/lib$lib.so"
    if [ ! -f "$src" ]; then
      echo "[FATAL] 缺少 $src" >&2
      exit 1
    fi
    cp -f "$src" "$dst/$lib.so"
  done
  echo "[ok] $abi -> $dst"
done

echo ""
echo "产物："
find "$OUT" -name '*.so' -exec ls -lh {} \; | awk '{print "  " $5 "  " $9}'
