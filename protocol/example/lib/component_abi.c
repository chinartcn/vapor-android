/*
 * 组件扩展示例（C）：把同目录下的 Python 组件包"贴"进宿主。
 *
 * 这演示了组件 ABI 的 C 外壳约定：
 *   - 导出 vapor_component_init(VaporApi*)
 *   - 用 dladdr 找到自己所在目录
 *   - py_eval 一段注册脚本，让 Vapor.component 校验并注册
 *
 * 编译：
 *   $CC -shared -fPIC -Iinclude protocol/example/lib/component_abi.c \
 *       -o protocol/example/lib/libcomponent_abi.so
 */

#include <dlfcn.h>
#include <libgen.h>
#include <stdio.h>
#include <string.h>

#include "vapor_abi.h"
#include "vapor_component.h"

static char g_dir[4096];

static void resolve_self_dir(void) {
    Dl_info info;
    if (dladdr((void *)vapor_component_init, &info) == 0 || info.dli_fname == NULL) {
        snprintf(g_dir, sizeof(g_dir), ".");
        return;
    }
    char buf[4096];
    snprintf(buf, sizeof(buf), "%s", info.dli_fname);
    snprintf(g_dir, sizeof(g_dir), "%s", dirname(buf));
}

int32_t vapor_component_init(VaporApi *api) {
    if (api == NULL || api->py_eval == NULL) {
        return VAPOR_ERR_ARG;
    }
    resolve_self_dir();

    char script[8192];
    int n = snprintf(
        script, sizeof(script),
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "from Vapor import component\n"
        "names = component.load_provider('hello_component')\n"
        "print('[component_abi] registered', names)\n",
        g_dir);
    if (n <= 0 || n >= (int)sizeof(script)) {
        return VAPOR_ERR;
    }
    return api->py_eval(script);
}
