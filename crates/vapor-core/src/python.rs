//! 内嵌 CPython 3.15.2 解释器。
//!
//! 通过 `dlopen("libpython3.15.so")` + dlsym 动态绑定 CPython C API，
//! 好处：同一份 Rust 二进制在 arm32 / arm64 上自动加载对应 ABI 的 libpython，
//! 不做链接期路径绑定；只要两边 CPython 都是 3.15.2，符号集完全一致。
//!
//! 本模块只绑定最小必要 API：初始化、运行文件、eval、GIL、导入。
//! 复杂的 Python 逻辑都放在 `rust.Vapor` 包里（纯 Python），便于用扩展迭代。

use std::ffi::{c_char, c_int, c_void, CString};
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
type PySetPathFn = unsafe extern "C" fn(*const c_char) -> c_int;

struct PyApi {
    initialize: PyInitializeExFn,
    finalize: PyFinalizeExFn,
    gil_ensure: PyGILStateEnsureFn,
    gil_release: PyGILStateReleaseFn,
    run_string: PyRunSimpleStringFn,
    #[allow(dead_code)] // 保留 FILE* 入口，未来可切换 off runpy 方案
    run_file: PyRunFileFn,
    err_print: PyErrPrintFn,
    set_path: Option<PySetPathFn>,
}

static PY_HANDLE: AtomicPtr<c_void> = AtomicPtr::new(std::ptr::null_mut());
static PY_API: Mutex<Option<PyApi>> = Mutex::new(None);
static PY_READY: AtomicBool = AtomicBool::new(false);
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

/// 初始化解释器（Py_InitializeEx(0)：不注册信号处理器，避免抢占 NativeActivity 的信号）。
pub fn py_init(app_dir: *const c_char) -> i32 {
    if PY_READY.load(Ordering::Acquire) {
        return VAPOR_OK;
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
            set_path: resolve(handle, "Py_SetPath").ok(),
        };
        *PY_API.lock().unwrap() = Some(api);
    }

    // 注入 sys.path：内置 rust.Vapor 包目录 + 逻辑包根目录 + lib/ 目录。
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
        // stdlib 必须显式加入：Py_SetPath 会替换整个默认 sys.path。
        paths.push(format!("{builtin}/stdlib"));
        paths.push(format!("{builtin}/stdlib/lib-dynload_arm64"));
        paths.push(format!("{builtin}/stdlib/lib-dynload_arm32"));
        paths.push(builtin.clone());           // rust.Vapor 包所在目录
        paths.push(format!("{builtin}/site-packages"));
    }
    if !dir.is_empty() {
        paths.push(dir.clone());
        paths.push(format!("{}/lib", dir.trim_end_matches('/')));
    }
    let syspath = paths.join(":");

    unsafe {
        let api = PY_API.lock().unwrap();
        if let Some(a) = api.as_ref() {
            // Py_SetPath 必须在 Py_Initialize 之前调用才生效。
            if let Some(sp) = a.set_path {
                let p = CString::new(syspath.as_str()).unwrap_or_default();
                (sp)(p.as_ptr());
            }
            (a.initialize)(0);
            let boot = format!(
                "import sys; sys.argv=['main.py']; sys.dont_write_bytecode=True; print('[vapor] python {} ready', sys.version.split()[0])",
                vapor_abi::VAPOR_CPYTHON_VERSION
            );
            let c = CString::new(boot).unwrap_or_default();
            (a.run_string)(c.as_ptr());
        }
    }

    PY_READY.store(true, Ordering::Release);
    if let Ok(mut s) = crate::state::state().lock() {
        s.python_ready = true;
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
    let code = format!(
        "import runpy; runpy.run_path({p:?}, run_name='__main__')"
    );
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
    if !PY_READY.swap(false, Ordering::AcqRel) {
        return;
    }
    let api = PY_API.lock().unwrap();
    if let Some(a) = api.as_ref() {
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
