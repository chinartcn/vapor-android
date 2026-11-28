/*
 * Vapor 组件 ABI v1
 * ---------------------------------------------------------------------------
 * 目标：让「UI 组件」也能像渲染扩展一样，以 .so / .py 的形式分发并热插拔，
 * 不改 Rust、不重编 APK 的 Python 运行时。
 *
 * 两层契约：
 *   1. Python 侧（组件本体）：模块暴露 ABI / manifest() / register(host)，
 *      由 Vapor.component.register_provider() 校验并注册。见 docs/COMPONENT_ABI.md。
 *   2. C 侧（可选外壳）：.so 导出 vapor_component_init(VaporApi*)。
 *      C 不适合直接建 UI 节点，推荐做法是「找到自身目录 -> py_eval 注册同目录
 *      下的 Python 组件包」，把 C 当作分发容器（可以顺带打包资源、校验授权）。
 *
 * 与 vapor_abi.h 的关系：组件 ABI 复用 VaporApi 的 py_eval 能力，不新增函数表，
 * 因此 v1 扩展在旧宿主上仍然能加载（宿主不认识组件时会被忽略）。
 */
#ifndef VAPOR_COMPONENT_H
#define VAPOR_COMPONENT_H

#include "vapor_abi.h"

#define VAPOR_COMPONENT_ABI_VERSION 1
#define VAPOR_COMPONENT_ENTRY "vapor_component_init"

#ifdef __cplusplus
extern "C" {
#endif

/* 组件扩展入口。
 *
 * 返回 VAPOR_OK 表示注册成功；其它值表示失败（宿主会记录日志但继续运行）。
 *
 * 推荐实现：
 *   static char g_dir[4096];
 *   // dladdr((void*)vapor_component_init, &info) -> dirname
 *   api->py_eval("import sys; sys.path.insert(0, '<g_dir>'); "
 *                "from Vapor import component; "
 *                "component.load_provider('my_components')");
 */
typedef int32_t (*VaporComponentInitFn)(VaporApi* api);

#ifdef __cplusplus
}
#endif
#endif /* VAPOR_COMPONENT_H */
