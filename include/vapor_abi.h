/*
 * Vapor Stable C ABI  v1
 * ---------------------------------------------------------------------------
 * 这是 Rust 渲染器对外暴露的**稳定** C ABI。所有语言（Python/ctypes、C、C++、
 * 其它 FFI 语言）都通过这一层与渲染器交互。ABI 一旦发布不再破坏；新增能力
 * 只能通过追加函数或用 vAPI 函数表扩展。
 *
 * 设计要点：
 *   1. 共享内存帧缓冲：Rust 持有 RGBA8888 像素缓冲，vapor_fb_lock() 返回裸
 *      指针/尺寸/stride，调用方（Python）直接写像素，零拷贝；vapor_fb_unlock()
 *      上传为 GLES 纹理并绘制一帧。
 *   2. 全功能函数表 VaporApi：C 扩展 init 时拿到整张表，从而"啥都能调"。
 *   3. 扩展加载：C 扩展 dlopen + init(VaporApi*)，Python 扩展走嵌入式解释器。
 *   4. 所有字符串为 UTF-8，以 NUL 结尾；缓冲区由调用方按契约释放。
 */
#ifndef VAPOR_ABI_H
#define VAPOR_ABI_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define VAPOR_ABI_VERSION 1
#define VAPOR_ENGINE_MAJOR 1
#define VAPOR_ENGINE_MINOR 0
#define VAPOR_ENGINE_PATCH 0

/* ---------- 枚举 ---------- */
typedef enum VaporStatus {
    VAPOR_OK                =  0,
    VAPOR_ERR               = -1,
    VAPOR_ERR_ARG           = -2,
    VAPOR_ERR_STATE         = -3,
    VAPOR_ERR_NOT_FOUND     = -4,
    VAPOR_ERR_PERMISSION    = -5,
    VAPOR_ERR_NO_EVENT      =  1,
} VaporStatus;

typedef enum VaporPixelFormat {
    VAPOR_FMT_RGBA8888 = 0,   /* 默认，每像素 4 字节 */
    VAPOR_FMT_RGB565   = 1,
} VaporPixelFormat;

typedef enum VaporEventType {
    VAPOR_EVT_NONE       = 0,
    VAPOR_EVT_MOTION     = 1,  /* 触摸/鼠标移动 */
    VAPOR_EVT_BUTTON     = 2,  /* 按下/抬起 */
    VAPOR_EVT_KEY        = 3,
    VAPOR_EVT_RESIZE     = 4,
    VAPOR_EVT_RESUME     = 5,
    VAPOR_EVT_PAUSE      = 6,
    VAPOR_EVT_DESTROY    = 7,
    /* v1.2 追加：滚轮/触控板。事件结构不变，x=水平滚动量，y=垂直滚动量（像素） */
    VAPOR_EVT_WHEEL      = 8,
} VaporEventType;

typedef enum VaporLogLevel {
    VAPOR_LOG_VERBOSE = 2,
    VAPOR_LOG_DEBUG   = 3,
    VAPOR_LOG_INFO    = 4,
    VAPOR_LOG_WARN    = 5,
    VAPOR_LOG_ERROR   = 6,
} VaporLogLevel;

/* ---------- 结构体 ---------- */
typedef struct VaporConfig {
    const char* app_dir;        /* 逻辑包根目录（含 main.py/main.json） */
    const char* private_dir;    /* 应用私有可写目录 */
    const char* external_dir;   /* 外部可写目录（可能为空） */
    int32_t     width;
    int32_t     height;
    int32_t     enable_python;  /* 是否启动内嵌 CPython */
} VaporConfig;

/* 共享内存帧缓冲描述。pixels 指向 Rust 持有的 RGBA8888 缓冲，stride 为字节数。
 * fd 为可选的 ashmem 文件描述符（-1 表示同进程共享，不做跨进程映射）。*/
typedef struct VaporFramebuffer {
    uint8_t*    pixels;         /* 可写像素区 */
    int32_t     width;
    int32_t     height;
    int32_t     stride;         /* 每行字节数 = width * 4 */
    int32_t     format;         /* VaporPixelFormat */
    int32_t     fd;             /* ashmem fd 或 -1 */
    uint64_t    generation;     /* 每次 unlock 自增，用于脏检查 */
} VaporFramebuffer;

typedef struct VaporEvent {
    int32_t  type;              /* VaporEventType */
    int32_t  x;                 /* 主指针坐标 */
    int32_t  y;
    int32_t  x2;                /* 次指针/额外数据 */
    int32_t  y2;
    int32_t  button;            /* 0=主, 1=次, 2=返回 */
    int32_t  action;            /* 0=down, 1=up, 2=move */
    int32_t  keycode;
    uint64_t timestamp_ms;
} VaporEvent;

/* 全功能函数表：C 扩展通过 init(VaporApi*) 拿到，实现"啥都能调" */
typedef struct VaporApi {
    int32_t (*abi_version)(void);
    void    (*version)(int32_t*, int32_t*, int32_t*);
    const char* (*cpython_version)(void);
    int32_t (*init)(const VaporConfig*);
    int32_t (*shutdown)(void);

    int32_t (*fb_lock)(VaporFramebuffer*);
    int32_t (*fb_unlock)(void);
    int32_t (*fb_resize)(int32_t, int32_t);
    int32_t (*fb_clear)(uint32_t);

    int32_t (*poll_event)(VaporEvent*);
    int32_t (*event_count)(void);
    int32_t (*set_title)(const char*);
    int32_t (*set_bg_color)(uint32_t);

    int32_t (*fs_read)(const char*, uint8_t**, int32_t*);
    int32_t (*fs_write)(const char*, const uint8_t*, int32_t);
    int32_t (*fs_exists)(const char*);
    int32_t (*fs_list)(const char*, char*, int32_t);
    void    (*fs_free)(uint8_t*);
    const char* (*fs_app_dir)(void);
    const char* (*fs_private_dir)(void);
    const char* (*fs_external_dir)(void);

    int32_t (*py_init)(const char*);
    int32_t (*py_run_file)(const char*);
    int32_t (*py_eval)(const char*);
    int32_t (*py_install_requirements)(const char*);

    int32_t (*ext_load)(const char*);
    int32_t (*ext_load_c)(const char*, const char*);
    int32_t (*ext_load_py)(const char*);
    void*   (*ext_symbol)(const char*);

    void    (*log)(int32_t, const char*);

    /* ---- JNI 桥接（v1.1 追加在尾部，前向兼容旧扩展） ---- */
    int32_t (*jni_set_vm)(void* java_vm);       /* 注入 JavaVM*（JNI_OnLoad / NativeActivity） */
    void*   (*jni_get_vm)(void);                /* 取 JavaVM* */
    void*   (*jni_get_env)(void);               /* 取当前线程 JNIEnv*（自动 attach），失败 NULL */
    int32_t (*jni_detach)(void);                /* 从 JVM 分离当前线程 */
    int32_t (*jni_call)(const char* req_json, char* out_json, int32_t cap); /* 通用 JNI 调用，见 docs */
    int32_t (*jni_release)(int64_t handle);     /* 释放 jni_call 返回的全局引用句柄 */
} VaporApi;

/* ---------- 稳定导出函数 ---------- */
int32_t     vapor_abi_version(void);
void        vapor_version(int32_t* major, int32_t* minor, int32_t* patch);
const char* vapor_cpython_version(void);
int32_t     vapor_init(const VaporConfig* config);
int32_t     vapor_shutdown(void);

int32_t     vapor_fb_lock(VaporFramebuffer* out);
int32_t     vapor_fb_unlock(void);
int32_t     vapor_fb_resize(int32_t w, int32_t h);
int32_t     vapor_fb_clear(uint32_t rgba);

int32_t     vapor_poll_event(VaporEvent* out);
int32_t     vapor_event_count(void);
int32_t     vapor_set_title(const char* title);
int32_t     vapor_set_bg_color(uint32_t rgba);

int32_t     vapor_fs_read(const char* path, uint8_t** out_buf, int32_t* out_len);
int32_t     vapor_fs_write(const char* path, const uint8_t* buf, int32_t len);
int32_t     vapor_fs_exists(const char* path);
int32_t     vapor_fs_list(const char* dir, char* out_json, int32_t cap);
void        vapor_fs_free(uint8_t* buf);
const char* vapor_fs_app_dir(void);
const char* vapor_fs_private_dir(void);
const char* vapor_fs_external_dir(void);

int32_t     vapor_py_init(const char* app_dir);
int32_t     vapor_py_run_file(const char* path);
int32_t     vapor_py_eval(const char* code);
int32_t     vapor_py_install_requirements(const char* path);

int32_t     vapor_ext_load(const char* manifest_json);
int32_t     vapor_ext_load_c(const char* path, const char* init_symbol);
int32_t     vapor_ext_load_py(const char* module);
void*       vapor_ext_symbol(const char* name);

void        vapor_log(int32_t level, const char* msg);

/* ---------- JNI 桥接：让 C / Python 扩展能调用任意 JNI ----------
 *
 * req_json 是一个调用描述对象，字段：
 *   "op"      : "find_class" | "get_method" | "get_static_method" | "call"
 *               | "call_static" | "new_string" | "get_string" | "new_array"
 *               | "get_array" | "set_array" | "array_length" | "new_object"
 *               | "get_field" | "set_field" | "get_static_field" | "set_static_field"
 *               | "exception_check" | "exception_clear" | "release"
 *   "class"   : 类全名，斜杠分隔，如 "java/lang/String"（find_class 必填）
 *   "name"    : 方法/字段名（get_method / call 系列 / field 系列必填）
 *   "sig"     : JNI 签名，如 "(I)Ljava/lang/String;"（call 系列 / get_method 必填）
 *   "static"  : true 表示静态方法/字段
 *   "target"  : 对象/类/数组句柄（call 的接收者、field 的对象）
 *   "args"    : 参数数组，每项 {"t":"i|j|f|d|z|b|c|s|l|o|a","v":...}
 *               t=l 字符串(literal)；t=o 对象句柄；t=a 句柄数组；其余为数值
 *   "ret"     : 期望返回类型，同 args 的 t；"v" 表示 void（默认）
 *   "value"   : new_string 的字符串；new_array 的 {"t":..,"n":长度}
 *   "index"   : 数组索引（get_array/set_array）
 *
 * out_json 返回 {"ok":true,"t":"..","v":..,"handle":int64}；出错时
 * {"ok":false,"error":"..."}。所有对象/类/数组通过 handle 跨调用持有，
 * 用 vapor_jni_release(handle) 释放。JNIEnv* 由桥接层自动 attach。
 */
int32_t     vapor_jni_set_vm(void* java_vm);
void*       vapor_jni_get_vm(void);
void*       vapor_jni_get_env(void);
int32_t     vapor_jni_detach(void);
int32_t     vapor_jni_call(const char* req_json, char* out_json, int32_t cap);
int32_t     vapor_jni_release(int64_t handle);

/* 取全功能函数表指针，供 C 扩展使用 */
const VaporApi* vapor_api(void);

/* C 扩展入口签名：int vapor_ext_init(VaporApi* api) */
typedef int32_t (*VaporExtInitFn)(VaporApi* api);

#ifdef __cplusplus
}
#endif
#endif /* VAPOR_ABI_H */
