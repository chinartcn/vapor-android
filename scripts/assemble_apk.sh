#!/usr/bin/env bash
# =============================================================================
# 用 Android SDK 工具链手动组装 APK（不依赖 gradle），支持 arm32+arm64 fat APK。
# 用法: ./scripts/assemble_apk.sh renderer|packager
# 前置: build_all.sh 的 [1-5] 已完成；jniLibs 与 assets 就位。
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
APP="${1:?用法: $0 renderer|packager}"

ANDROID_HOME="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
BT="$ANDROID_HOME/build-tools/35.0.0"
PLATFORM="$ANDROID_HOME/platforms/android-35/android.jar"
BUILD="$ROOT/build"
OUT="$ROOT/build/output"
mkdir -p "$OUT" "$BUILD/tmp/$APP"

SRC="$ROOT/android/$APP/app/src/main"
MANIFEST="$SRC/AndroidManifest.xml"
JNI="$BUILD/jniLibs/$APP"
RES="$SRC/res"
ASSETS="$SRC/assets"
TMP="$BUILD/tmp/$APP"
JAVAC="$(dirname "$(readlink -f "$(command -v javac)")")/javac"

echo "===== 组装 $APP APK ====="
[ -f "$MANIFEST" ] || { echo "缺少 $MANIFEST" >&2; exit 1; }
[ -f "$PLATFORM" ] || { echo "缺少 android.jar" >&2; exit 1; }

# 1) aapt2 编译资源（若有 res）→ compile → link，生成基础 APK（含 manifest/资源/assets）
BASE_APK="$TMP/base.apk"
RES_ZIP="$TMP/compiled_res.zip"
if [ -d "$RES" ] && [ -n "$(ls -A "$RES" 2>/dev/null)" ]; then
  "$BT/aapt2" compile --dir "$RES" -o "$RES_ZIP"
  RES_ARG="-R $RES_ZIP"
else
  RES_ARG=""
fi

AAPT_ARGS=(
  link -o "$BASE_APK"
  -I "$PLATFORM"
  -M "$MANIFEST"
  --java "$TMP/gen"
  --min-sdk-version 24
  --target-sdk-version 35
  --version-code 1
  --version-name 1.0.0
)
[ -n "$RES_ARG" ] && AAPT_ARGS+=($RES_ARG)
[ -d "$ASSETS" ] && AAPT_ARGS+=(-A "$ASSETS")
"$BT/aapt2" "${AAPT_ARGS[@]}"

# 2) javac 编译 Java 源（若 hasCode=false 的纯渲染器可跳过，但保留通用流程）
mkdir -p "$TMP/classes" "$TMP/gen"
JFILES=$(find "$SRC/java" -name '*.java' 2>/dev/null | tr '\n' ' ')
if [ -n "$JFILES" ]; then
  GFILES=$(find "$TMP/gen" -name '*.java' 2>/dev/null | tr '\n' ' ')
  "$JAVAC" -source 17 -target 17 -classpath "$PLATFORM" \
    -d "$TMP/classes" $JFILES $GFILES
fi

# 3) d8 生成 classes.dex（若有 class 文件）
DEX="$TMP/classes.dex"
CLASSFILES=$(find "$TMP/classes" -name '*.class' 2>/dev/null | tr '\n' ' ')
if [ -n "$CLASSFILES" ]; then
  "$BT/d8" --classpath "$PLATFORM" --output "$TMP" $CLASSFILES
else
  printf 'dex\n035\x00' > "$DEX"
fi

# 4) 合并 so + dex 到 APK（zip 更新）
cp "$BASE_APK" "$TMP/app-unsigned.apk"
( cd "$JNI" && zip -r "$TMP/app-unsigned.apk" . ) 2>/dev/null || true
[ -f "$DEX" ] && ( cd "$TMP" && zip -q "$TMP/app-unsigned.apk" classes.dex )

# 5) zipalign
ALIGNED="$TMP/app-aligned.apk"
"$BT/zipalign" -f -p 4 "$TMP/app-unsigned.apk" "$ALIGNED"

# 6) 签名（首次用 keytool 生成 JKS keystore，生产请替换为正式密钥）
KS="$ROOT/packager_assets/release.keystore"
if [ ! -f "$KS" ]; then
  keytool -genkeypair -keystore "$KS" -alias vapor \
    -keyalg RSA -keysize 2048 -validity 10000 \
    -storepass vapor -keypass vapor \
    -dname "CN=Vapor, OU=Vapor, O=Vapor, L=NA, ST=NA, C=CN" >/dev/null 2>&1
fi
case "$APP" in
  renderer) FINAL="$OUT/VaporRenderer.apk" ;;
  packager) FINAL="$OUT/VaporPackager.apk" ;;
  *) FINAL="$OUT/Vapor-${APP}.apk" ;;
esac
"$BT/apksigner" sign --ks "$KS" --ks-key-alias vapor \
  --ks-pass pass:vapor --key-pass vapor \
  --v1-signing-enabled true --v2-signing-enabled true \
  --out "$FINAL" "$ALIGNED"

echo "[ok] $APP -> $FINAL ($(du -h "$FINAL" | cut -f1))"
