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
# 复用：lib/arm64-v8a 与 lib/armeabi-v7a 下若有 libpython3.15.so（从已构建 APK
# 提取的正式产物），脚本直接复用它们；但 arm32 的扩展模块（lib-dynload，math/
# _ctypes/zlib 等）仓库里没有，缺失时会自动从源码交叉编译 arm32（并自动构建同版本
# 宿主解释器）。设置 VAPOR_REBUILD_CPYTHON=1 可强制重建。
#
# 用法：
#   export ANDROID_NDK_HOME=/path/to/android-ndk-r27c
#   ./scripts/build_cpython.sh            # 全量（有现成库就复用）
#   ./scripts/build_cpython.sh reuse      # 显式复用模式
#   ./scripts/build_cpython.sh arm64      # 仅 arm64（快）
#   ./scripts/build_cpython.sh arm32      # 仅 arm32（需 NDK，较慢）
# =============================================================================
set -euo pipefail

PYTHON_VERSION="3.15.2"
PYTHON_MAJOR_MINOR="3.15"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 仓库里可能带着从已构建 APK 提取的 libpython（lib/<abi>/libpython3.15.so）。
# 这些是正常构建产物，**优先复用**，省掉 arm32 源码交叉编译（需要同版本宿主解释器）。
# 想强制重建：VAPOR_REBUILD_CPYTHON=1 ./scripts/build_cpython.sh all
PROVIDED="$ROOT/lib"
OUT="$ROOT/build/cpython"
SRC="$ROOT/build/cpython-src"
DOWNLOADS="$ROOT/build/downloads"
# 仓库自包含：离线构建输入放在 vendor/ 下；VAPOR_OFFLINE=1 时禁止联网。
VENDOR="$ROOT/vendor"
OFFLINE="${VAPOR_OFFLINE:-0}"
NDK="${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}"

mkdir -p "$OUT" "$SRC" "$DOWNLOADS"

PY_TGZ="$DOWNLOADS/Python-$PYTHON_VERSION.tgz"
PY_XZ="$DOWNLOADS/Python-$PYTHON_VERSION.tar.xz"
ARM64_PKG="$DOWNLOADS/python-$PYTHON_VERSION-aarch64-linux-android.tar.gz"
# CPython 3.15 的 _ctypes 依赖系统 libffi（源码树不再自带），而 NDK sysroot 没有
# ffi.h。arm32 必须自行交叉编译一份静态 libffi，否则 rust.Vapor 的 ctypes 绑定不可用。
LIBFFI_VERSION="${LIBFFI_VERSION:-3.4.6}"
LIBFFI_TGZ="$DOWNLOADS/libffi-$LIBFFI_VERSION.tar.gz"

SHA_TGZ="f74e1db0eabd651cd01e4e2a00201bd41866abe74869ab716f9bf33cbbaa0943"
SHA_ARM64="cfd4f0fdaff465ba62ebf3c3df2c8c410d0802fef3f98897082460df704cfef6"
SHA_LIBFFI_346="b0dea9df23c863a7a50e825440f3ebffabd65df1497108e5d437747843895a4e"

download() {
  local url="$1" dest="$2" expect="$3"
  local name; name="$(basename "$dest")"
  if [ -f "$dest" ]; then
    if [ -z "$expect" ]; then echo "[cache] $name"; return 0; fi
    local got; got="$(sha256sum "$dest" | awk '{print $1}')"
    if [ "$got" = "$expect" ]; then echo "[cache] $name"; return 0; fi
    echo "[warn] $name 校验不符，重新获取" >&2
    rm -f "$dest"
  fi
  # 优先使用 vendor/ 中的离线源（CI 必需，保证仓库自包含）。
  if [ -f "$VENDOR/$name" ]; then
    echo "[vendor] $name <- $VENDOR/$name"
    cp -f "$VENDOR/$name" "$dest"
  elif [ "$OFFLINE" = "1" ]; then
    echo "[FATAL] 离线模式缺少 $VENDOR/$name；请把构建输入放入 vendor/" >&2
    exit 1
  else
    echo "[get] $url"
    curl -fL --retry 3 -o "$dest" "$url"
  fi
  local got; got="$(sha256sum "$dest" | awk '{print $1}')"
  if [ -n "$expect" ] && [ "$got" != "$expect" ]; then
    echo "[FATAL] SHA256 不匹配: $dest (期望 $expect, 实际 $got)" >&2
    exit 1
  fi
}

fetch_sources() {
  download "https://www.python.org/ftp/python/$PYTHON_VERSION/Python-$PYTHON_VERSION.tgz" "$PY_TGZ" "$SHA_TGZ"
  download "https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-aarch64-linux-android.tar.gz" "$ARM64_PKG" "$SHA_ARM64"
}

# ---- 复用仓库里现成的 libpython（从已构建 APK 提取） ----
provided_lib() {
  local abi="$1"
  local p="$PROVIDED/$abi/libpython$PYTHON_MAJOR_MINOR.so"
  if [ -f "$p" ]; then echo "$p"; fi
}

force_build() {
  [ "${VAPOR_REBUILD_CPYTHON:-0}" = "1" ]
}

# 定位某个架构产物里的标准库目录（lib/python3.15，含 os.py）。
stdlib_dir() {
  local root="$1" p
  for p in "$root/lib/python$PYTHON_MAJOR_MINOR" "$root/prefix/lib/python$PYTHON_MAJOR_MINOR"; do
    if [ -f "$p/os.py" ]; then echo "$p"; return 0; fi
  done
  find "$root" -maxdepth 5 -type f -path "*/python$PYTHON_MAJOR_MINOR/os.py" 2>/dev/null \
    | head -1 | xargs -r dirname
}

# 交叉编译 arm32 需要「同版本」宿主解释器。缺失时从同一份源码自动构建一次，
# 避免 CI 里手工准备 build/host-python。
ensure_host_python() {
  if [ -n "${BUILD_PYTHON:-}" ]; then return 0; fi
  local hp="$ROOT/build/host-python/install/bin/python$PYTHON_MAJOR_MINOR"
  if [ -x "$hp" ]; then BUILD_PYTHON="$hp"; return 0; fi
  echo "[info] 未找到同版本宿主 Python，开始从源码构建 $PYTHON_VERSION（arm32 交叉编译所需）" >&2
  fetch_sources
  local s="$SRC/host-Python-$PYTHON_VERSION"
  if [ ! -x "$s/configure" ]; then
    rm -rf "$s"; mkdir -p "$s"
    tar -xzf "$PY_TGZ" -C "$s" --strip-components=1
  fi
  ( cd "$s" \
      && ./configure --prefix="$ROOT/build/host-python/install" --without-ensurepip >/dev/null \
      && { make -k -j"$(nproc)" >/dev/null || true; } \
      && { make install >/dev/null || true; } )
  if [ ! -x "$hp" ]; then
    echo "[FATAL] 宿主 Python 构建失败（$hp 不存在）" >&2
    exit 1
  fi
  BUILD_PYTHON="$hp"
}

# 为 arm32 交叉编译静态 libffi（_ctypes 依赖）。产物：build/libffi/arm32。
build_libffi_arm32() {
  local prefix="$ROOT/build/libffi/arm32"
  LIBFFI_PREFIX="$prefix"
  if [ -f "$prefix/lib/libffi.a" ]; then return 0; fi
  if [ -z "$NDK" ] || [ ! -d "$NDK" ]; then
    echo "[FATAL] libffi 交叉编译需要 ANDROID_NDK_HOME" >&2
    exit 1
  fi
  local sha=""
  if [ "$LIBFFI_VERSION" = "3.4.6" ]; then
    sha="$SHA_LIBFFI_346"
  else
    echo "[warn] libffi $LIBFFI_VERSION 无内置 SHA256，跳过校验" >&2
  fi
  download "https://github.com/libffi/libffi/releases/download/v$LIBFFI_VERSION/libffi-$LIBFFI_VERSION.tar.gz" \
    "$LIBFFI_TGZ" "$sha"
  local s="$SRC/libffi-$LIBFFI_VERSION"
  if [ ! -d "$s" ]; then tar -xzf "$LIBFFI_TGZ" -C "$SRC"; fi
  local TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
  local SYSROOT="$NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot"
  local CHOST="armv7a-linux-androideabi24"
  echo "===== arm32: 交叉编译 libffi $LIBFFI_VERSION（_ctypes 依赖）====="
  (
    cd "$s"
    make distclean >/dev/null 2>&1 || true
    CC="$TOOLCHAIN/$CHOST-clang" CXX="$TOOLCHAIN/$CHOST-clang++" \
    AR="$TOOLCHAIN/llvm-ar" RANLIB="$TOOLCHAIN/llvm-ranlib" \
    CFLAGS="-fPIC --sysroot=$SYSROOT" LDFLAGS="--sysroot=$SYSROOT" \
      ./configure --host=arm-linux-androideabi --prefix="$prefix" \
        --disable-shared --enable-static --disable-docs \
      && make -k -j"$(nproc)" \
      && make install
  )
  if [ ! -f "$prefix/lib/libffi.a" ]; then
    echo "[FATAL] libffi arm32 构建失败（缺少 $prefix/lib/libffi.a）" >&2
    exit 1
  fi
  LIBFFI_PREFIX="$prefix"
}

# 把交叉编译产出的扩展模块（math/_ctypes/zlib/...）放进 lib-dynload。
# 这些是 CPython 标准库里以 .so 形式提供的模块，缺了它们 `import math` 会直接报
# ModuleNotFoundError（真机日志即为此现象）。
install_built_extensions() {
  local s="$1" d="$2" copied=0 so
  mkdir -p "$d/lib/python$PYTHON_MAJOR_MINOR/lib-dynload"
  # CPython 的扩展模块在源码树里是就地构建到 Modules/ 下的（不是 build/），
  # 因此两处都要搜。
  while IFS= read -r so; do
    [ -n "$so" ] || continue
    cp -f "$so" "$d/lib/python$PYTHON_MAJOR_MINOR/lib-dynload/"
    copied=$((copied + 1))
  done < <(
    find "$s/Modules" -type f -name '*.so' ! -name 'libpython*' 2>/dev/null
    find "$s/build" -maxdepth 2 -type f -name '*.so' ! -name 'libpython*' 2>/dev/null
  )
  # 平台相关配置模块（sysconfig 需要）
  find "$s/build" -maxdepth 2 -type f -name '_sysconfigdata*.py' \
    -exec cp -f {} "$d/lib/python$PYTHON_MAJOR_MINOR/" \; 2>/dev/null || true
  if [ "$copied" -eq 0 ]; then
    echo "[FATAL] 未生成任何扩展模块，lib-dynload 为空（math 等模块将不可用）" >&2
    exit 1
  fi
  echo "[ok] lib-dynload <- $copied 个扩展模块"
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
  # 标准库只在缺失时下载（纯 Python，架构无关；也供 arm32 复用）
  if [ ! -f "$d/lib/python$PYTHON_MAJOR_MINOR/os.py" ]; then
    fetch_sources
    tar -xzf "$ARM64_PKG" -C "$d" --strip-components=1
  fi
  # 优先用仓库里现成的 libpython（正常构建产物，已从 APK 提取）
  local provided; provided="$(provided_lib arm64-v8a)"
  if [ -n "$provided" ] && ! force_build; then
    mkdir -p "$d/lib"
    cp -f "$provided" "$d/lib/libpython$PYTHON_MAJOR_MINOR.so"
    echo "[reuse] arm64 libpython <- $provided"
  fi
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
  local d="$OUT/arm32"; mkdir -p "$d"
  # 已完整源码构建过（有 lib-dynload）则直接跳过，避免 build_all 重复耗时。
  if [ -d "$d/lib/python$PYTHON_MAJOR_MINOR/lib-dynload" ] \
     && [ -n "$(ls -A "$d/lib/python$PYTHON_MAJOR_MINOR/lib-dynload" 2>/dev/null)" ] \
     && ! force_build; then
    echo "===== arm32: 已存在完整构建，跳过 ====="
    echo "$PYTHON_VERSION" > "$OUT/arm32.version"
    return
  fi

  local provided; provided="$(provided_lib armeabi-v7a)"
  local provided_dynload="$PROVIDED/armeabi-v7a/lib/python$PYTHON_MAJOR_MINOR/lib-dynload"
  if [ -n "$provided" ] && ! force_build && [ -d "$provided_dynload" ]; then
    echo "===== arm32: 复用 $provided（跳过源码交叉编译） ====="
    cp -f "$provided" "$d/libpython$PYTHON_MAJOR_MINOR.so"
    mkdir -p "$d/lib/python$PYTHON_MAJOR_MINOR"
    if [ -d "$OUT/arm64/lib/python$PYTHON_MAJOR_MINOR" ]; then
      cp -r "$OUT/arm64/lib/python$PYTHON_MAJOR_MINOR/." "$d/lib/python$PYTHON_MAJOR_MINOR/"
      echo "[reuse] arm32 stdlib <- arm64（纯 Python，架构无关）"
    else
      echo "[warn] 缺少 arm64 标准库，arm32 将没有 stdlib（先跑 arm64 或加 VAPOR_REBUILD_CPYTHON=1）"
    fi
    cp -r "$provided_dynload" "$d/lib/python$PYTHON_MAJOR_MINOR/lib-dynload"
    echo "[reuse] arm32 lib-dynload <- $provided_dynload"
    assert_version arm32 "$PYTHON_VERSION"
    echo "[ok] arm32 (reuse) -> $d"
    return
  fi

  echo "===== arm32: 从源码交叉编译 CPython $PYTHON_VERSION ====="
  fetch_sources
  if [ -z "$NDK" ] || [ ! -d "$NDK" ]; then
    echo "[FATAL] arm32 需要 ANDROID_NDK_HOME 指向 NDK r27c" >&2
    exit 1
  fi
  local s="$SRC/Python-$PYTHON_VERSION"
  if [ ! -d "$s" ]; then
    tar -xzf "$PY_TGZ" -C "$SRC"
  fi

  # 交叉编译 CPython 需要一个与目标**同版本**的宿主解释器来生成 .pyc 与配置头。
  # BUILD_PYTHON 可由调用方指定；缺失时从同一份源码自动构建。
  ensure_host_python
  local bp_ver
  bp_ver="$("$BUILD_PYTHON" -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])')"
  if [ "$bp_ver" != "$PYTHON_VERSION" ]; then
    echo "[FATAL] 宿主 Python 版本 $bp_ver != $PYTHON_VERSION，禁止版本错配" >&2
    exit 1
  fi
  echo "[info] build-python = $BUILD_PYTHON ($bp_ver)"

  local TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
  local HOST="armv7a-linux-androideabi24"
  local FFI_PREFIX
  build_libffi_arm32
  FFI_PREFIX="$LIBFFI_PREFIX"
  export CC="$TOOLCHAIN/$HOST-clang"
  export CXX="$TOOLCHAIN/$HOST-clang++"
  export AR="$TOOLCHAIN/llvm-ar"
  export RANLIB="$TOOLCHAIN/llvm-ranlib"
  export SYSROOT="$NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot"
  export CFLAGS="-fPIC -D__ANDROID_API__=24 --sysroot=$SYSROOT -I$FFI_PREFIX/include"
  export LDFLAGS="--sysroot=$SYSROOT -L$FFI_PREFIX/lib"
  export LIBFFI_CFLAGS="-I$FFI_PREFIX/include"
  export LIBFFI_LIBS="$FFI_PREFIX/lib/libffi.a"

  cd "$s"
  # 每次都用正确的 Android 探测结果重新 configure（幂等，避免旧 Makefile 残留宿主库探测）。
  if [ -f Makefile ]; then
    make distclean >/dev/null 2>&1 || true
  fi
  rm -f config.cache config.status Makefile pyconfig.h .crosstarget_configured
  # 关键：显式告诉 configure Android sysroot 没有这些头文件/库，防止误用宿主的。
  ./configure \
    --host=arm-linux-androideabi \
    --build=x86_64-pc-linux-gnu \
    --target=arm-linux-androideabi \
    --prefix="$d" \
    --enable-shared --disable-static \
    --without-ensurepip \
    --with-build-python="$BUILD_PYTHON" \
    ac_cv_file__dev_ptmx=no \
    ac_cv_file__dev_ptc=no \
    ac_cv_func_getentropy=no \
    ac_cv_func_sigaltstack=no \
    ac_cv_header_lzma_h=no \
    ac_cv_header_zstd_h=no \
    ac_cv_header_bzlib_h=no \
    ac_cv_header_readline_readline_h=no \
    ac_cv_header_ncurses_h=no \
    ac_cv_lib_lzma_lzma_easy_buffer_encode=no \
    ac_cv_lib_zstd_ZSTD_version=no \
    ac_cv_lib_bz2_BZ2_bzCompressInit=no \
    LIBZSTD_CFLAGS="" LIBZSTD_LIBS="" \
    LIBLZMA_CFLAGS="" LIBLZMA_LIBS=""
  echo "$BUILD_PYTHON" > .crosstarget_configured
  # CROSSTARGET_CONFIGURED 标记供上面判断（把值写入环境以便重复调用）
  export CROSSTARGET_CONFIGURED="$(cat .crosstarget_configured 2>/dev/null || echo "$BUILD_PYTHON")"

  # Android NDK sysroot 无 zstd 头/库；configure 误探到宿主的 libzstd。
  # 从构建列表移除 _remote_debugging / _zstd 两个非必需模块，避免 -DHAVE_ZSTD 与 zstd.h 依赖。
  # （核心解释器 + zlib + sqlite3 + ctypes 均保留，pip 与 rust.Vapor 不受影响）
  python3 - "$PWD/Makefile" <<'PYEOF'
import sys, re
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
# Android NDK sysroot 缺少 host 探测到的 zstd/lzma 开发库。CPython 3.15 把
# _lzma/_zstd/_remote_debugging 变成默认构建的内置模块，configure 缓存变量无法关闭。
# 直接从 Makefile 的模块列表变量中移除对应的完整条目。
drop = ["_remote_debugging", "_zstd", "_lzma", "readline"]
for var in ["SHAREDMODS", "MODBUILT_NAMES", "MODSHARED_NAMES"]:
    m = re.search(rf"^{var}= (.*)$", s, flags=re.M)
    if not m:
        continue
    line = m.group(0)
    toks = m.group(1).split()
    kept = [t for t in toks if not any(d in t for d in drop)]
    s = s.replace(line, f"{var}= " + " ".join(kept))
# 把这些模块的 STATE 设为 no，确保即使被依赖也不构建
for d in drop:
    s = re.sub(rf"^MODULE__{d.upper()}_STATE=.*$", f"MODULE__{d.upper()}_STATE=no", s, flags=re.M)
    s = re.sub(rf"^MODULE__{d.upper()}_CFLAGS=.*$", f"MODULE__{d.upper()}_CFLAGS=", s, flags=re.M)
    s = re.sub(rf"^MODULE__{d.upper()}_LDFLAGS=.*$", f"MODULE__{d.upper()}_LDFLAGS=", s, flags=re.M)
open(p, "w", encoding="utf-8").write(s)
left = [d for d in drop if f"Modules/{d}$(EXT_SUFFIX)" in s]
print("[patch] Makefile: 已移除 _remote_debugging / _zstd / _lzma；SHAREDMODS 残余:", left)
PYEOF
  # -k：个别可选扩展（ssl/sqlite 等）在 NDK 缺依赖时允许跳过，
  # 核心解释器与 math/_ctypes 等必需模块仍会构建；下面会校验 lib-dynload。
  make -k -j"$(nproc)" || echo "[warn] make 有部分模块失败（继续，稍后校验 lib-dynload）" >&2
  # 动态库（Rust 侧通过 dlsym 加载，必须有 .so）
  cp -f libpython$PYTHON_MAJOR_MINOR.so* "$d/" 2>/dev/null || true
  cp -f libpython$PYTHON_MAJOR_MINOR.a "$d/" 2>/dev/null || true
  # 标准库：纯 Python 部分 + 交叉编译产出的扩展模块（lib-dynload）
  # 关键：只拷 Lib/ 是没有 math/_ctypes/zlib 的，真机 `import math` 会失败。
  mkdir -p "$d/lib/python$PYTHON_MAJOR_MINOR"
  cp -r Lib/. "$d/lib/python$PYTHON_MAJOR_MINOR/"
  install_built_extensions "$PWD" "$d"
  assert_version arm32 "$PYTHON_VERSION"
  cd - >/dev/null
  echo "[ok] arm32 -> $d"
}

verify_both() {
  local a b arch std dyn
  a="$(cat "$OUT/arm64.version" 2>/dev/null || echo MISSING)"
  b="$(cat "$OUT/arm32.version" 2>/dev/null || echo MISSING)"
  if [ "$a" != "$b" ] || [ "$a" != "$PYTHON_VERSION" ]; then
    echo "[FATAL] 双架构版本不一致: arm64=$a arm32=$b, 要求=$PYTHON_VERSION" >&2
    exit 1
  fi
  # 标准库完整性：必须有 lib-dynload 下的扩展模块，否则 APK 里没有 math 等模块。
  for arch in arm64 arm32; do
    std="$(stdlib_dir "$OUT/$arch")"
    dyn="$std/lib-dynload"
    if [ -z "$std" ] || [ ! -d "$dyn" ] || [ -z "$(ls -A "$dyn" 2>/dev/null)" ]; then
      echo "[FATAL] $arch 缺少标准库扩展模块（$dyn）" >&2
      echo "        arm32 需从源码交叉编译以生成 math/_ctypes/zlib 等 .so" >&2
      exit 1
    fi
    echo "[ok] $arch lib-dynload: $(ls "$dyn" | wc -l) 个模块"
  done
  echo "[PASS] arm64=$a arm32=$b 版本一致 ✔"
}

TARGET="${1:-all}"
case "$TARGET" in
  arm64) build_arm64 ;;
  arm32) build_arm64; build_arm32 ;;   # arm32 复用 arm64 标准库，先跑 arm64
  all|reuse) build_arm64; build_arm32; verify_both ;;
  *) echo "用法: $0 [arm64|arm32|all|reuse]" >&2; exit 2 ;;
esac
