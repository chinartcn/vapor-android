/*
 * C ABI 扩展示例 —— 证明“C 扩展拿到 VaporApi 全功能表后啥都能调”。
 * 编译: gcc -shared -fPIC -I../../include hello_c.c -o libhello_c.so
 * 加载: 在 abi.json 声明 kind=c, symbol=vapor_ext_init
 */
#include <stdio.h>
#include "vapor_abi.h"

static VaporApi* API = 0;

int32_t vapor_ext_init(VaporApi* api) {
    API = api;
    /* 通过函数表调用渲染器全部能力：版本、帧缓冲、颜色、日志、文件系统 */
    int32_t major = 0, minor = 0, patch = 0;
    API->version(&major, &minor, &patch);

    VaporFramebuffer fb = {0};
    if (API->fb_lock(&fb) == VAPOR_OK) {
        /* 直接写共享内存画一个品红矩形 */
        for (int y = 10; y < 50; ++y) {
            for (int x = 10; x < 200 && x < fb.width; ++x) {
                int idx = (y * fb.width + x) * 4;
                fb.pixels[idx + 0] = 255; /* R */
                fb.pixels[idx + 1] = 0;   /* G */
                fb.pixels[idx + 2] = 255; /* B */
                fb.pixels[idx + 3] = 255; /* A */
            }
        }
        API->fb_unlock();
    }

    char msg[128];
    snprintf(msg, sizeof(msg), "C ext ok: engine %d.%d.%d, py=%s",
             major, minor, patch, API->cpython_version());
    API->log(VAPOR_LOG_INFO, msg);
    return VAPOR_OK;
}

/* 额外导出一个可被 Python 通过 ext.call_symbol 调用的函数 */
int32_t vapor_c_add(int32_t a, int32_t b) {
    return a + b;
}
