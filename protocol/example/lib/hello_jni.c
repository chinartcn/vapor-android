/*
 * hello_jni.c —— C ABI 扩展示例：通过 VaporApi 函数表调用 JNI。
 *
 * 展示两条 C 扩展调用 JNI 的路径：
 *   1. api->jni_call(JSON, ...) 通用桥接（推荐，跨 arm32/arm64 一致）
 *   2. api->jni_get_env() 拿裸 JNIEnv*，用标准 jni.h 调任意 JNI（完全自由）
 *
 * 编译:
 *   gcc -shared -fPIC -I../../include hello_jni.c -o libhello_jni.so
 * 加载:
 *   vapor_ext_load_c("/path/libhello_jni.so", "vapor_ext_init")
 */
#include "vapor_abi.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

static const VaporApi *API = NULL;

/* 路径 1：通用 JSON 桥接 —— 调 java.lang.Math.max(3,7) */
static int call_via_json(int *out) {
    const char *req =
        "{\"op\":\"call_static\",\"class\":\"java/lang/Math\","
        "\"name\":\"max\",\"sig\":\"(II)I\","
        "\"args\":[{\"t\":\"i\",\"v\":3},{\"t\":\"i\",\"v\":7}]}";
    char resp[2048];
    int32_t rc = API->jni_call(req, resp, sizeof(resp));
    if (rc != VAPOR_OK) {
        API->log(VAPOR_LOG_ERROR, "jni_call 失败");
        return -1;
    }
    API->log(VAPOR_LOG_INFO, resp);  /* {"ok":true,"t":"i","v":7} */
    /* 极简解析 "v":N（示例用，生产请用 cJSON） */
    const char *p = strstr(resp, "\"v\":");
    if (p) {
        *out = atoi(p + 4);  /* 跳过 "v": 四个字符 */
        return 0;
    }
    return -2;
}

/* 路径 2：裸 JNIEnv* —— 用标准 JNI 函数表调 java.lang.System.out.println */
typedef const struct JNINativeInterface_ *JNIEnv_T;
static void call_via_raw_env(void) {
    /* 这里不 include jni.h，按函数表偏移手工调用（偏移取自 jni.h，64 位布局）；
     * 在 Android/arm32 上应改用 jni.h 头文件，这里仅为演示原理。 */
    void *env = API->jni_get_env();
    if (!env) {
        API->log(VAPOR_LOG_WARN, "jni_get_env 返回 NULL（未注入 VM）");
        return;
    }
    API->log(VAPOR_LOG_INFO, "C 扩展已取得裸 JNIEnv*（可调用任意 JNI 函数）");
}

/* 扩展入口：VaporApi 函数表在 init 时传入，"啥都能调" */
int32_t vapor_ext_init(VaporApi *api) {
    API = api;
    API->log(VAPOR_LOG_INFO, "hello_jni C 扩展加载");

    int result = 0;
    if (call_via_json(&result) == 0 && result == 7) {
        API->log(VAPOR_LOG_INFO, "C 扩展经 JNI 桥接调用 Math.max(3,7)=7 成功");
    } else {
        API->log(VAPOR_LOG_ERROR, "C 扩展 JNI 调用结果异常");
    }
    call_via_raw_env();
    return VAPOR_OK;
}
