//! 内嵌 CPython 3.15.2 解释器。
//!
//! 通过 `dlopen("libpython3.15.so")` + dlsym 动态绑定 CPython C API，
//! 好处：同一份 Rust 二进制在 arm32 / arm64 上自动加载对应 ABI 的 libpython，
//! 不做链接期路径绑定；只要两边 CPython 都是 3.15.2，符号集完全一致。
//!
//! 本模块只绑定最小必要 API：初始化、运行文件、eval、GIL、导入。
//! 复杂的 Python 逻辑都放在 `rust.Vapor` 包里（纯 Python），便于用扩展迭代。
//!
//! # 初始化方式（重要）
//!
//! CPython 3.11 起 `Py_SetPath` 被弃用，3.13 起从公开 API 移除（仅 stable ABI 保留），
//! 且其真实签名是 `void Py_SetPath(const wchar_t *)` —— 传入单字节 UTF-8 字符串会被
//! `wcsdup` 按 4 字节宽字符解读，导致 sys.path 乱码、找不到 `encodings`，最终
//! `Py_InitializeEx` 内部 `Py_ExitStatusException` → `Py_FatalError` → 整个进程 exit()。
//!
//! 因此这里改用 CPython 3.15 官方推荐的 **PyConfig** 初始化：
//!   - `PyConfig_InitPythonConfig` 初始化一个（对我们不透明的）PyConfig 缓冲区；
//!   - 通过 `PYTHONPATH` 环境变量注入 stdlib / rust 包 / app 路径（UTF-8，无 wchar_t 转换）；
//!   - `Py_InitializeFromConfig` 返回 `PyStatus`，可检查错误而不触发 fatal exit；
//!   - `PyConfig_Clear` 释放内部资源。
//!
//! PyConfig 结构体布局随版本变化，我们从不读写其字段，只把一段足够大的对齐缓冲区
//! 交给 CPython 自己的 API 初始化/清理，因此不依赖任何内部字段偏移，跨版本安全。

use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::os::raw::c_uint;
use std::sync::atomic::{AtomicBool, AtomicPtr, Ordering};
use std::sync::Mutex;

use vapor_abi::{VAPOR_ERR, VAPOR_ERR_ARG, VAPOR_ERR_STATE, VAPOR_OK};

// ---- CPython C API 函数签名（3.15 稳定子集） ----
type PyInitializeExFn = unsafe extern "C" fn(c_int);
type PyFinalizeExFn = unsafe extern "C" fn();
type PyGILStateEnsureFn = unsafe extern "C" fn() -> c_int;
type PyGILStateReleaseFn = unsafe extern "C" fn(c_int);
type PyRunSimpleStringFn = unsafe extern "C" fn(*const c_char) -> c_int;
type PyRunFileFn = unsafe extern "C" fn(*const c_char, *const c_char) -> *mut c_void;
type PyErrPrintFn = unsafe extern "C" fn();

// PyConfig 初始化三件套（PyConfig 对我们是不透明缓冲区）。
type PyConfigInitFn = unsafe extern "C" fn(*mut c_void);
type PyConfigClearFn = unsafe extern "C" fn(*mut c_void);
type PyInitializeFromConfigFn = unsafe extern "C" fn(*const c_void) -> PyStatus;
type PyStatusExceptionFn = unsafe extern "C" fn(PyStatus) -> c_int;

/// CPython `PyStatus` 的 ABI 镜像（Include/cpython/initconfig.h）：
/// ```c
/// typedef struct {
///     enum { _PyStatus_TYPE_OK=0, _PyStatus_TYPE_ERROR=1, _PyStatus_TYPE_EXIT=2 } _type;
///     const char *func;
///     const char *err_msg;
///     int exitcode;
/// } PyStatus;
/// ```
/// 用 repr(C) 保证按值传递/返回遵循平台 C ABI（x86_64/arm64 sret、arm32 寄存器）。
#[repr(C)]
#[derive(Clone, Copy)]
struct PyStatus {
    _type: c_int,
    func: *const c_char,
    err_msg: *const c_char,
    exitcode: c_int,
}

/// PyConfig 对我们不透明。分配远大于其实际大小的对齐缓冲区（任何合理版本
/// 的 PyConfig 都远小于 4096 字节），只把指针交给 CPython 自己的 API。
#[repr(C, align(16))]
struct PyConfigBuf([u8; 4096]);

struct PyApi {
    initialize: PyInitializeExFn, // 降级用：Py_InitializeEx(0)
    finalize: PyFinalizeExFn,
    gil_ensure: PyGILStateEnsureFn,
    gil_release: PyGILStateReleaseFn,
    run_string: PyRunSimpleStringFn,
    #[allow(dead_code)] // 保留 FILE* 入口，未来可切换 off runpy 方案
    run_file: PyRunFileFn,
    err_print: PyErrPrintFn,
    // PyConfig 路径（3.15 首选）
    config_init: Option<PyConfigInitFn>,
    config_clear: Option<PyConfigClearFn>,
    initialize_from_config: Option<PyInitializeFromConfigFn>,
    status_exception: Option<PyStatusExceptionFn>,
}

static PY_HANDLE: AtomicPtr<c_void> = AtomicPtr::new(std::ptr::null_mut());
static PY_API: Mutex<Option<PyApi>> = Mutex::new(None);
static PY_READY: AtomicBool = AtomicBool::new(false);
/// 是否已经尝试过初始化（无论成功失败）。CPython 全进程只能安全初始化一次；
/// 若首次失败后再次 Py_InitializeFromConfig，会因全局状态残留触发
/// "error reading frozen getpath.py" 等连锁错误。故失败后不再重试。
static PY_ATTEMPTED: AtomicBool = AtomicBool::new(false);
static GIL: Mutex<()> = Mutex::new(());

unsafe fn resolve<T: Copy>(handle: *mut c_void, name: &str) -> Result<T, ()> {
    let c = CString::new(name).map_err(|_| ())?;
    let p = libc::dlsym(handle, c.as_ptr());
    if p.is_null() {
        return Err(());
    }
    Ok(std::mem::transmute_copy::<*mut c_void, T>(&p))
}

/// 候选库名：优先带 minor 的版本号，逐步回退。
fn candidate_libs() -> &'static [&'static str] {
    &[
        "libpython3.15.so",
        "libpython3.15.so.1.0",
        "libpython3.15m.so",
        "libpython3.15.so.1",
    ]
}

fn load_python() -> Result<*mut c_void, ()> {
    for name in candidate_libs() {
        let c = CString::new(*name).map_err(|_| ())?;
        let h = unsafe { libc::dlopen(c.as_ptr(), libc::RTLD_NOW | libc::RTLD_GLOBAL) };
        if !h.is_null() {
            crate::log::log_msg(crate::log::LOG_INFO, &format!("已加载 {name}"));
            return Ok(h);
        }
    }
    Err(())
}

/// 初始化解释器。
pub fn py_init(app_dir: *const c_char) -> i32 {
    if PY_READY.load(Ordering::Acquire) {
        return VAPOR_OK;
    }
    // 幂等保护：CPython 全进程只允许初始化一次。若上次尝试失败（Py_InitializeFromConfig
    // 返回错误），全局状态处于半初始化，再次初始化会因 frozen getpath 模块状态损坏而
    // 报 "error reading frozen getpath.py"。因此失败后标记 attempted，直接返回错误，
    // 不再重试，避免连锁崩溃。surface 反复创建时也能稳定不崩。
    if PY_ATTEMPTED.swap(true, Ordering::AcqRel) {
        crate::log::log_msg(
            crate::log::LOG_WARN,
            "CPython 已尝试过初始化，跳过重复初始化（避免全局状态残留崩溃）",
        );
        return VAPOR_ERR_STATE;
    }
    let handle = match load_python() {
        Ok(h) => h,
        Err(_) => {
            crate::log::log_msg(
                crate::log::LOG_WARN,
                "未找到 libpython3.15.so，Python 嵌入不可用（本机验证模式）",
            );
            PY_READY.store(true, Ordering::Release);
            return VAPOR_OK; // 降级：不阻断渲染器
        }
    };
    PY_HANDLE.store(handle, Ordering::Release);

    unsafe {
        macro_rules! sym {
            ($name:expr) => {
                match resolve(handle, $name) {
                    Ok(f) => f,
                    Err(_) => {
                        crate::log::log_msg(
                            crate::log::LOG_ERROR,
                            &format!("CPython 缺少符号 {}，请确认 libpython 为 3.15.x", $name),
                        );
                        return VAPOR_ERR_STATE;
                    }
                }
            };
        }
        let api = PyApi {
            initialize: sym!("Py_InitializeEx"),
            finalize: sym!("Py_FinalizeEx"),
            gil_ensure: sym!("PyGILState_Ensure"),
            gil_release: sym!("PyGILState_Release"),
            run_string: sym!("PyRun_SimpleString"),
            run_file: sym!("PyRun_SimpleFileExFlags"),
            err_print: sym!("PyErr_Print"),
            config_init: resolve(handle, "PyConfig_InitPythonConfig").ok(),
            config_clear: resolve(handle, "PyConfig_Clear").ok(),
            initialize_from_config: resolve(handle, "Py_InitializeFromConfig").ok(),
            status_exception: resolve(handle, "PyStatus_Exception").ok(),
        };
        *PY_API.lock().unwrap() = Some(api);
    }

    // 注入模块搜索路径：内置 rust.Vapor 包目录 + stdlib + lib-dynload + 逻辑包根 + lib/。
    let dir = if app_dir.is_null() {
        String::new()
    } else {
        unsafe { std::ffi::CStr::from_ptr(app_dir) }
            .to_string_lossy()
            .into_owned()
    };
    let builtin = bundled_python_home();
    let mut paths: Vec<String> = Vec::new();
    if !builtin.is_empty() {
        // 诊断：stdlib 缺失时明确告警（历史上 APK 只带了 libpython，没带标准库，
        // 导致 `import math` 直接 ModuleNotFoundError，整个 Vapor.ui 回退链崩溃）。
        let stdlib_os = format!("{builtin}/stdlib/os.py");
        if !std::path::Path::new(&stdlib_os).exists() {
            crate::log::log_msg(
                crate::log::LOG_ERROR,
                &format!("内置 CPython 标准库缺失（{stdlib_os} 不存在），请检查 APK 的 assets/python/stdlib"),
            );
        }
        // stdlib 必须显式加入：PYTHONPATH 会被前置到 sys.path。
        paths.push(format!("{builtin}/stdlib"));
        // lib-dynload 按当前进程 ABI 选择：fat APK 里 arm64/arm32 两套 .so 分开存放，
        // 若把另一个 ABI 的目录排在前面，arm32 设备会命中 arm64 的 math.so 等，
        // 报 wrong ELF class 或直接找不到模块。
        let dynload = if cfg!(target_pointer_width = "64") {
            "lib-dynload_arm64"
        } else {
            "lib-dynload_arm32"
        };
        paths.push(format!("{builtin}/stdlib/{dynload}"));
        // 兼容单架构部署 / 普通 CPython 安装树（Lib/lib-dynload）。
        paths.push(format!("{builtin}/stdlib/lib-dynload"));
        paths.push(builtin.clone()); // rust.Vapor 包所在目录
        paths.push(format!("{builtin}/site-packages"));
    }
    if !dir.is_empty() {
        paths.push(dir.clone());
        paths.push(format!("{}/lib", dir.trim_end_matches('/')));
    }
    let syspath = paths.join(":");

    // 关键修复：用 PYTHONPATH（UTF-8，无需 wchar_t 转换）注入路径。
    // PyConfig_InitPythonConfig 的 use_environment=1，会读取 PYTHONPATH 并前置到 sys.path。
    // 绝不再调用 Py_SetPath（其 const wchar_t* 签名与单字节 UTF-8 不兼容，会令 sys.path 乱码）。
    if !syspath.is_empty() {
        let key = CString::new("PYTHONPATH").unwrap_or_default();
        let val = CString::new(syspath.as_str()).unwrap_or_default();
        unsafe {
            libc::setenv(key.as_ptr(), val.as_ptr(), 1);
        }
        crate::log::log_msg(
            crate::log::LOG_INFO,
            &format!("PYTHONPATH 已注入 {} 条路径", paths.len()),
        );
    }

    // 用 PyConfig 初始化；返回 PyStatus 可检查失败，避免 fatal exit 杀死渲染器进程。
    let api_guard = PY_API.lock().unwrap();
    let api = api_guard.as_ref().unwrap();
    if let (Some(config_init), Some(config_clear), Some(initialize_from_config), Some(status_exception)) =
        (api.config_init, api.config_clear, api.initialize_from_config, api.status_exception)
    {
        let mut buf = PyConfigBuf([0u8; 4096]);
        let cfg_ptr = buf.0.as_mut_ptr() as *mut c_void;
        unsafe {
            config_init(cfg_ptr);
            let status = initialize_from_config(cfg_ptr as *const c_void);
            config_clear(cfg_ptr);
            if status_exception(status) != 0 {
                let detail = if status.err_msg.is_null() {
                    format!("type={}", status._type)
                } else {
                    CStr::from_ptr(status.err_msg).to_string_lossy().into_owned()
                };
                crate::log::log_msg(
                    crate::log::LOG_ERROR,
                    &format!("CPython 初始化失败（已拦截，不退出进程）: {detail}"),
                );
                return VAPOR_ERR_STATE;
            }
        }
        crate::log::log_msg(crate::log::LOG_INFO, "CPython 经 PyConfig 初始化成功");
    } else {
        // 降级：极少数精简版 libpython 无 PyConfig 符号，退回 Py_InitializeEx(0)。
        // 路径已通过 PYTHONPATH 注入，compat config 会读取，仍不会乱码。
        unsafe { (api.initialize)(0) };
        crate::log::log_msg(crate::log::LOG_WARN, "PyConfig 符号缺失，降级 Py_InitializeEx(0)");
    }
    drop(api_guard);

    // 启动引导：设置 argv、禁止写 pyc、打印版本。
    {
        let api = PY_API.lock().unwrap();
        if let Some(a) = api.as_ref() {
            let boot = format!(
                "import sys; sys.argv=['main.py']; sys.dont_write_bytecode=True; print('[vapor] python {} ready', sys.version.split()[0])",
                vapor_abi::VAPOR_CPYTHON_VERSION
            );
            let c = CString::new(boot).unwrap_or_default();
            unsafe { (a.run_string)(c.as_ptr()) };
        }
    }

    PY_READY.store(true, Ordering::Release);
    if let Ok(mut s) = crate::state::state().lock() {
        s.python_ready = true;
    }

    // 协议约定：启动后自动执行逻辑包入口 <app_dir>/main.py。
    // 此前只初始化了 Python 环境却从未运行 main.py，导致帧缓冲始终为空（黑屏）。
    // 用 try/except 包裹，逻辑脚本出错只打印、不阻断渲染器。
    if !dir.is_empty() {
        let main_py = format!("{}/main.py", dir.trim_end_matches('/'));
        let boot_code = format!(
            "import os,runpy,traceback\n\
             _mp={main_py:?}\n\
             if os.path.isfile(_mp):\n\
             \x20   try:\n\
             \x20       runpy.run_path(_mp, run_name='__main__')\n\
             \x20       print('[vapor] main.py executed OK')\n\
             \x20   except Exception:\n\
             \x20       traceback.print_exc()\n\
             \x20       print('[vapor] main.py FAILED')\n\
             else:\n\
             \x20   print('[vapor] no main.py at', _mp)"
        );
        let rc = py_eval_str(&boot_code);
        crate::log::log_msg(
            crate::log::LOG_INFO,
            &format!("main.py 自动执行完成 rc={rc} path={main_py}"),
        );
    }
    VAPOR_OK
}

fn bundled_python_home() -> String {
    // Android 上 CPython 解压到 /data/user/0/<pkg>/files/python；
    // 本机验证时取环境变量或当前目录。
    #[cfg(target_os = "android")]
    {
        let s = crate::state::state().lock().unwrap();
        format!("{}/python", s.private_dir.trim_end_matches('/'))
    }
    #[cfg(not(target_os = "android"))]
    {
        std::env::var("VAPOR_PYTHON_HOME").unwrap_or_default()
    }
}

/// 运行逻辑包入口 main.py。
pub fn py_run_file(path: *const c_char) -> i32 {
    if path.is_null() {
        return VAPOR_ERR_ARG;
    }
    let p = unsafe { std::ffi::CStr::from_ptr(path) }.to_string_lossy().into_owned();
    // PyRun_SimpleFileExFlags 需要 FILE*，这里用等价的 exec() 方式，避免 libc FILE 布局差异。
    let code = format!("import runpy; runpy.run_path({p:?}, run_name='__main__')");
    py_eval_str(&code)
}

/// eval 一段 Python 代码，打印异常但不让渲染器崩溃。
pub fn py_eval_str(code: &str) -> i32 {
    let _guard = GIL.lock().unwrap_or_else(|e| e.into_inner());
    if !PY_READY.load(Ordering::Acquire) {
        return VAPOR_ERR_STATE;
    }
    let api = PY_API.lock().unwrap();
    let a = match api.as_ref() {
        Some(a) => a,
        None => return VAPOR_OK, // 降级模式：无 libpython，视为成功
    };
    let c = match CString::new(code) {
        Ok(c) => c,
        Err(_) => return VAPOR_ERR_ARG,
    };
    let rc = unsafe {
        let gil = (a.gil_ensure)();
        let rc = (a.run_string)(c.as_ptr());
        if rc != 0 {
            (a.err_print)();
        }
        (a.gil_release)(gil);
        rc
    };
    if rc != 0 {
        VAPOR_ERR
    } else {
        VAPOR_OK
    }
}

/// pip 集成：调用内嵌解释器的 ensurepip + pip install -r。
pub fn py_install_requirements(path: *const c_char) -> i32 {
    let req = if path.is_null() {
        "requirements.txt".to_string()
    } else {
        unsafe { std::ffi::CStr::from_ptr(path) }
            .to_string_lossy()
            .into_owned()
    };
    let code = format!(
        "import subprocess,sys; r=subprocess.call([sys.executable or 'python','-m','pip','install','-r',{req:?}]); assert r==0, r"
    );
    py_eval_str(&code)
}

pub fn shutdown() {
    // CPython 解释器作为「进程级单例」常驻，不在 surface 销毁时 finalize / dlclose。
    // 原因：Android SurfaceView 的 surface 会因横竖屏、前后台切换反复销毁重建；
    // Py_FinalizeEx 后再 Py_InitializeFromConfig 会损坏 frozen getpath 模块状态，
    // 第二次初始化报 "error reading frozen getpath.py"，且大多数扩展不支持重入式
    // finalize/re-init。保持解释器存活，surface 重建时 py_init 直接返回 OK 复用，
    // 既避免连锁崩溃，也保证重建后 Python 逻辑仍可用。
    //
    // 只有在「从未成功初始化」时才无需处理（PY_ATTEMPTED 会阻止危险的重试）。
    let _ = PY_ATTEMPTED.load(Ordering::Acquire);
    let _ = PY_READY.load(Ordering::Acquire);
}

/// 强制终结解释器（仅供显式 API 调用；正常运行路径不使用，见 shutdown 注释）。
/// 保留 finalize 符号引用，避免编译器将其优化掉。
#[allow(dead_code)]
pub fn py_finalize_forced() {
    if !PY_READY.swap(false, Ordering::AcqRel) {
        return;
    }
    if let Some(a) = PY_API.lock().unwrap().as_ref() {
        unsafe { (a.finalize)() };
    }
    let h = PY_HANDLE.swap(std::ptr::null_mut(), Ordering::AcqRel);
    if !h.is_null() {
        unsafe { libc::dlclose(h) };
    }
}

/// 供外部查询：是否真的加载了 libpython。
pub fn is_real_python() -> bool {
    PY_API.lock().unwrap().is_some()
}

// 保持 c_uint 导入被使用（CPython 某些 API 用无符号参数），避免警告。
#[allow(dead_code)]
fn _keep(_: c_uint) {}
