//! Vapor._sdk —— 稳定 C ABI（include/vapor_abi.h）的运行时桥接。
//!
//! 关键点：**不链接 libvapor.so**。运行时 dlopen，按符号名取函数指针；
//! 若符号已由宿主（NativeActivity 的 System.loadLibrary / ctypes）导入，
//! 则回退到 dlsym(RTLD_DEFAULT)。因此同一份 _sdk.so 在 Android 与本机通用。

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use std::ffi::{c_char, c_void, CStr, CString};
use std::sync::OnceLock;

#[cfg(unix)]
use libc;

static LIB: OnceLock<Option<libloading::Library>> = OnceLock::new();

type AbiVerFn = unsafe extern "C" fn() -> i32;
type CpyVerFn = unsafe extern "C" fn() -> *const c_char;
type SetVmFn = unsafe extern "C" fn(*mut c_void) -> i32;
type GetVmFn = unsafe extern "C" fn() -> *mut c_void;
type GetEnvFn = unsafe extern "C" fn() -> *mut c_void;
type DetachFn = unsafe extern "C" fn() -> i32;
type JniCallFn = unsafe extern "C" fn(*const c_char, *mut c_char, i32) -> i32;
type ReleaseFn = unsafe extern "C" fn(i64) -> i32;

type FsReadFn = unsafe extern "C" fn(*const c_char, *mut *mut u8, *mut i32) -> i32;
type FsWriteFn = unsafe extern "C" fn(*const c_char, *const u8, i32) -> i32;
type FsExistsFn = unsafe extern "C" fn(*const c_char) -> i32;
type FsListFn = unsafe extern "C" fn(*const c_char, *mut c_char, i32) -> i32;
type FsFreeFn = unsafe extern "C" fn(*mut u8);
type DirFn = unsafe extern "C" fn() -> *const c_char;

/// 与 include/vapor_abi.h 的 VaporFramebuffer 严格对应。
#[repr(C)]
struct Fb {
    pixels: *mut u8,
    width: i32,
    height: i32,
    stride: i32,
    format: i32,
    fd: i32,
    generation: u64,
}
type FbLockFn = unsafe extern "C" fn(*mut Fb) -> i32;
type LogFn = unsafe extern "C" fn(i32, *const c_char);

fn library() -> Option<&'static libloading::Library> {
    LIB.get().and_then(|o| o.as_ref())
}

fn cname(name: &str) -> Vec<u8> {
    let mut v = Vec::with_capacity(name.len() + 1);
    v.extend_from_slice(name.as_bytes());
    v.push(0);
    v
}

/// 从已加载的库里取符号；失败回退 RTLD_DEFAULT。
fn sym<T: Copy>(name: &str) -> Option<T> {
    if let Some(lib) = library() {
        let bytes = cname(name);
        if let Ok(s) = unsafe { lib.get::<T>(&bytes) } {
            return Some(*s);
        }
    }
    #[cfg(unix)]
    unsafe {
        let c = CString::new(name).ok()?;
        let p = libc::dlsym(libc::RTLD_DEFAULT, c.as_ptr());
        if p.is_null() {
            return None;
        }
        return Some(std::mem::transmute_copy(&p));
    }
    #[cfg(not(unix))]
    {
        let _ = name;
        None
    }
}

fn open_library(path: Option<&str>) -> bool {
    let candidates: Vec<String> = match path {
        Some(p) => vec![p.to_string()],
        None => {
            let mut v = Vec::new();
            if let Ok(p) = std::env::var("VAPOR_NATIVE_LIB") {
                if !p.is_empty() {
                    v.push(p);
                }
            }
            v.push("libvapor.so".to_string());
            v.push("libvapor_packager.so".to_string());
            v
        }
    };
    for cand in candidates {
        if let Ok(lib) = unsafe { libloading::Library::new(&cand) } {
            let _ = LIB.set(Some(lib));
            return true;
        }
    }
    // 即使 dlopen 失败也要初始化 OnceLock，避免后续重复尝试
    let _ = LIB.set(None);
    false
}

fn ensure_open() -> bool {
    if LIB.get().is_some() {
        return library().is_some();
    }
    open_library(None)
}

#[inline]
fn to_cs(s: &str) -> PyResult<CString> {
    CString::new(s).map_err(|_| PyRuntimeError::new_err("字符串包含内嵌 NUL"))
}

// ---------------------------------------------------------------------------
// 生命周期 / 能力探测
// ---------------------------------------------------------------------------

#[pyfunction]
#[pyo3(signature = (path=None))]
fn open(path: Option<&str>) -> bool {
    let _ = LIB.set(None); // 允许重新打开（测试用）
    open_library(path)
}

#[pyfunction]
fn available() -> bool {
    ensure_open() && sym::<AbiVerFn>("vapor_abi_version").is_some()
}

#[pyfunction]
fn capabilities(py: Python<'_>) -> PyResult<Py<PyAny>> {
    ensure_open();
    let d = pyo3::types::PyDict::new(py);
    let items: [(&str, bool); 12] = [
        ("abi_version", sym::<AbiVerFn>("vapor_abi_version").is_some()),
        ("jni_call", sym::<JniCallFn>("vapor_jni_call").is_some()),
        ("jni_set_vm", sym::<SetVmFn>("vapor_jni_set_vm").is_some()),
        ("jni_get_env", sym::<GetEnvFn>("vapor_jni_get_env").is_some()),
        ("jni_release", sym::<ReleaseFn>("vapor_jni_release").is_some()),
        ("fs_read", sym::<FsReadFn>("vapor_fs_read").is_some()),
        ("fs_write", sym::<FsWriteFn>("vapor_fs_write").is_some()),
        ("fs_exists", sym::<FsExistsFn>("vapor_fs_exists").is_some()),
        ("fs_list", sym::<FsListFn>("vapor_fs_list").is_some()),
        ("fb_lock", sym::<FbLockFn>("vapor_fb_lock").is_some()),
        ("log", sym::<LogFn>("vapor_log").is_some()),
        ("library", library().is_some()),
    ];
    for (k, v) in items {
        d.set_item(k, v)?;
    }
    Ok(d.into_any().unbind())
}

#[pyfunction]
fn abi_version() -> PyResult<i32> {
    ensure_open();
    match sym::<AbiVerFn>("vapor_abi_version") {
        Some(f) => Ok(unsafe { f() }),
        None => Err(PyRuntimeError::new_err("未找到 vapor_abi_version（libvapor.so 未加载）")),
    }
}

#[pyfunction]
fn cpython_version() -> PyResult<String> {
    ensure_open();
    match sym::<CpyVerFn>("vapor_cpython_version") {
        Some(f) => unsafe {
            let p = f();
            if p.is_null() {
                Ok(String::new())
            } else {
                Ok(CStr::from_ptr(p).to_string_lossy().into_owned())
            }
        },
        None => Err(PyRuntimeError::new_err("未找到 vapor_cpython_version")),
    }
}

#[pyfunction]
fn log(level: i32, msg: &str) -> PyResult<()> {
    ensure_open();
    if let Some(f) = sym::<LogFn>("vapor_log") {
        let c = to_cs(msg)?;
        unsafe { f(level, c.as_ptr()) };
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// JNI
// ---------------------------------------------------------------------------

#[pyfunction]
fn set_vm(addr: u64) -> bool {
    ensure_open();
    match sym::<SetVmFn>("vapor_jni_set_vm") {
        Some(f) => unsafe { f(addr as *mut c_void) == 0 },
        None => false,
    }
}

#[pyfunction]
fn get_vm() -> u64 {
    ensure_open();
    sym::<GetVmFn>("vapor_jni_get_vm")
        .map(|f| unsafe { f() as u64 })
        .unwrap_or(0)
}

#[pyfunction]
fn get_env() -> u64 {
    ensure_open();
    sym::<GetEnvFn>("vapor_jni_get_env")
        .map(|f| unsafe { f() as u64 })
        .unwrap_or(0)
}

#[pyfunction]
fn detach() -> bool {
    ensure_open();
    sym::<DetachFn>("vapor_jni_detach")
        .map(|f| unsafe { f() == 0 })
        .unwrap_or(false)
}

#[pyfunction]
fn release(handle: i64) -> bool {
    ensure_open();
    sym::<ReleaseFn>("vapor_jni_release")
        .map(|f| unsafe { f(handle) == 0 })
        .unwrap_or(false)
}

/// 通用 JNI 调用：请求/响应都是 JSON 字符串（协议见 docs/JNI_BRIDGE.md）。
#[pyfunction]
#[pyo3(signature = (req, cap=1_048_576))]
fn call(req: &str, cap: usize) -> PyResult<String> {
    ensure_open();
    let f = match sym::<JniCallFn>("vapor_jni_call") {
        Some(f) => f,
        None => return Err(PyRuntimeError::new_err("未找到 vapor_jni_call（libvapor.so 未加载）")),
    };
    let creq = to_cs(req)?;
    let cap = cap.max(64);
    let mut buf = vec![0u8; cap];
    let rc = unsafe { f(creq.as_ptr(), buf.as_mut_ptr() as *mut c_char, cap as i32) };
    if rc != 0 {
        return Err(PyRuntimeError::new_err(format!("vapor_jni_call 返回 {rc}")));
    }
    let n = buf.iter().position(|&b| b == 0).unwrap_or(buf.len());
    Ok(String::from_utf8_lossy(&buf[..n]).into_owned())
}

// ---------------------------------------------------------------------------
// 文件系统
// ---------------------------------------------------------------------------

#[pyfunction]
fn fs_read(path: &str) -> PyResult<Vec<u8>> {
    ensure_open();
    let f = sym::<FsReadFn>("vapor_fs_read")
        .ok_or_else(|| PyRuntimeError::new_err("未找到 vapor_fs_read"))?;
    let free = sym::<FsFreeFn>("vapor_fs_free");
    let cp = to_cs(path)?;
    let mut buf: *mut u8 = std::ptr::null_mut();
    let mut len: i32 = 0;
    let rc = unsafe { f(cp.as_ptr(), &mut buf, &mut len) };
    if rc != 0 || buf.is_null() || len < 0 {
        return Err(PyRuntimeError::new_err(format!("读取失败: {path} (rc={rc})")));
    }
    let data = unsafe { std::slice::from_raw_parts(buf, len as usize) }.to_vec();
    if let Some(free) = free {
        unsafe { free(buf) };
    }
    Ok(data)
}

#[pyfunction]
fn fs_write(path: &str, data: &[u8]) -> PyResult<i32> {
    ensure_open();
    let f = sym::<FsWriteFn>("vapor_fs_write")
        .ok_or_else(|| PyRuntimeError::new_err("未找到 vapor_fs_write"))?;
    let cp = to_cs(path)?;
    Ok(unsafe { f(cp.as_ptr(), data.as_ptr(), data.len() as i32) })
}

#[pyfunction]
fn fs_exists(path: &str) -> bool {
    ensure_open();
    let cp = match to_cs(path) {
        Ok(c) => c,
        Err(_) => return false,
    };
    sym::<FsExistsFn>("vapor_fs_exists")
        .map(|f| unsafe { f(cp.as_ptr()) != 0 })
        .unwrap_or(false)
}

#[pyfunction]
#[pyo3(signature = (path, cap=262_144))]
fn fs_list(path: &str, cap: usize) -> PyResult<String> {
    ensure_open();
    let f = sym::<FsListFn>("vapor_fs_list")
        .ok_or_else(|| PyRuntimeError::new_err("未找到 vapor_fs_list"))?;
    let cp = to_cs(path)?;
    let cap = cap.max(64);
    let mut buf = vec![0u8; cap];
    let rc = unsafe { f(cp.as_ptr(), buf.as_mut_ptr() as *mut c_char, cap as i32) };
    if rc != 0 {
        return Err(PyRuntimeError::new_err(format!("列目录失败: {path} (rc={rc})")));
    }
    let n = buf.iter().position(|&b| b == 0).unwrap_or(buf.len());
    Ok(String::from_utf8_lossy(&buf[..n]).into_owned())
}

fn dir_of(name: &str) -> String {
    sym::<DirFn>(name)
        .map(|f| unsafe {
            let p = f();
            if p.is_null() {
                String::new()
            } else {
                CStr::from_ptr(p).to_string_lossy().into_owned()
            }
        })
        .unwrap_or_default()
}

#[pyfunction]
fn app_dir() -> String {
    ensure_open();
    dir_of("vapor_fs_app_dir")
}

#[pyfunction]
fn private_dir() -> String {
    ensure_open();
    dir_of("vapor_fs_private_dir")
}

#[pyfunction]
fn external_dir() -> String {
    ensure_open();
    dir_of("vapor_fs_external_dir")
}

// ---------------------------------------------------------------------------
// 帧缓冲（返回裸地址，交给 Python 的 memoryview / ctypes 使用）
// ---------------------------------------------------------------------------

#[pyfunction]
fn fb_lock() -> PyResult<(u64, i32, i32, i32)> {
    ensure_open();
    let f = sym::<FbLockFn>("vapor_fb_lock")
        .ok_or_else(|| PyRuntimeError::new_err("未找到 vapor_fb_lock"))?;
    let mut fb = Fb {
        pixels: std::ptr::null_mut(),
        width: 0,
        height: 0,
        stride: 0,
        format: 0,
        fd: -1,
        generation: 0,
    };
    let rc = unsafe { f(&mut fb) };
    if rc != 0 || fb.pixels.is_null() {
        return Err(PyRuntimeError::new_err(format!("vapor_fb_lock 失败: {rc}")));
    }
    Ok((fb.pixels as u64, fb.width, fb.height, fb.stride))
}

#[pymodule]
fn _sdk(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(open, m)?)?;
    m.add_function(wrap_pyfunction!(available, m)?)?;
    m.add_function(wrap_pyfunction!(capabilities, m)?)?;
    m.add_function(wrap_pyfunction!(abi_version, m)?)?;
    m.add_function(wrap_pyfunction!(cpython_version, m)?)?;
    m.add_function(wrap_pyfunction!(log, m)?)?;
    m.add_function(wrap_pyfunction!(set_vm, m)?)?;
    m.add_function(wrap_pyfunction!(get_vm, m)?)?;
    m.add_function(wrap_pyfunction!(get_env, m)?)?;
    m.add_function(wrap_pyfunction!(detach, m)?)?;
    m.add_function(wrap_pyfunction!(release, m)?)?;
    m.add_function(wrap_pyfunction!(call, m)?)?;
    m.add_function(wrap_pyfunction!(fs_read, m)?)?;
    m.add_function(wrap_pyfunction!(fs_write, m)?)?;
    m.add_function(wrap_pyfunction!(fs_exists, m)?)?;
    m.add_function(wrap_pyfunction!(fs_list, m)?)?;
    m.add_function(wrap_pyfunction!(app_dir, m)?)?;
    m.add_function(wrap_pyfunction!(private_dir, m)?)?;
    m.add_function(wrap_pyfunction!(external_dir, m)?)?;
    m.add_function(wrap_pyfunction!(fb_lock, m)?)?;
    Ok(())
}
