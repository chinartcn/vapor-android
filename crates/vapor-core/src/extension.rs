//! 扩展加载机制。
//!
//! - C 扩展：`.so` 导出 `int vapor_ext_init(VaporApi* api)`，加载时把整张函数表
//!   交给扩展，因此 C 扩展能调用渲染器的**全部**能力（"啥都能调"）。
//! - Python 扩展：标准 CPython 模块 `.so`（PEP 489 / 传统 init）或纯 `.py`，
//!   由内嵌解释器 import。
//! - abi.json：声明加载清单、顺序、架构过滤、启用状态。
//!
//! 本机（非 Android）编译时 C 扩展加载走 `libc::dlopen`，逻辑完全一致，
//! 便于用本地编译的示例扩展验证。

use std::collections::HashMap;
use std::ffi::{CStr, CString};
use std::os::raw::{c_char, c_void};
use std::path::{Component, Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use vapor_abi::{VAPOR_ERR, VAPOR_ERR_ARG, VAPOR_ERR_NOT_FOUND, VAPOR_OK};

/// 已加载的 C 扩展句柄（dlopen 返回的 handle 地址，用 usize 存以保证 Send），保持不卸载。
static LOADED: OnceLock<Mutex<HashMap<String, usize>>> = OnceLock::new();

fn loaded() -> &'static Mutex<HashMap<String, usize>> {
    LOADED.get_or_init(|| Mutex::new(HashMap::new()))
}

#[derive(serde::Deserialize, serde::Serialize, Debug, Clone)]
pub struct AbiManifest {
    pub schema: Option<i32>,
    pub description: Option<String>,
    #[serde(default)]
    pub extensions: Vec<AbiEntry>,
}

#[derive(serde::Deserialize, serde::Serialize, Debug, Clone)]
pub struct AbiEntry {
    /// 扩展名，同时是 Python 模块名 / C 库名（不含 lib 前缀与 .so 后缀）。
    pub name: String,
    /// "c" 或 "python"。
    pub kind: String,
    /// 相对 lib/ 的路径；缺省为 name + 平台后缀。
    pub path: Option<String>,
    /// C 扩展入口符号，缺省 vapor_ext_init。
    pub symbol: Option<String>,
    /// 架构过滤：arm64 / arm32 / all。
    #[serde(default = "default_arch")]
    pub arch: String,
    /// 是否启用，缺省 true。
    #[serde(default = "default_true")]
    pub enabled: bool,
}

fn default_arch() -> String {
    "all".into()
}
fn default_true() -> bool {
    true
}

fn current_arch() -> &'static str {
    #[cfg(target_arch = "aarch64")]
    {
        "arm64"
    }
    #[cfg(target_arch = "arm")]
    {
        "arm32"
    }
    #[cfg(not(any(target_arch = "aarch64", target_arch = "arm")))]
    {
        "all"
    }
}

fn lib_dir() -> String {
    let s = crate::state::state().lock().unwrap();
    let base = if !s.app_dir.is_empty() {
        &s.app_dir
    } else {
        &s.private_dir
    };
    format!("{}/lib", base.trim_end_matches('/'))
}

fn cstr(p: *const c_char) -> Option<&'static str> {
    if p.is_null() {
        None
    } else {
        unsafe { CStr::from_ptr(p) }.to_str().ok()
    }
}

/// 把相对路径限制在 `lib/` 目录内：拒绝绝对路径、`..` 与符号链接越界。
fn safe_lib_join(base: &str, rel: &str) -> Option<PathBuf> {
    if rel.is_empty() || rel.starts_with('/') || rel.contains('\0') {
        return None;
    }
    let mut p = PathBuf::from(base);
    for comp in Path::new(rel).components() {
        match comp {
            Component::Normal(c) => p.push(c),
            _ => return None,
        }
    }
    if let (Ok(root), Ok(real)) = (Path::new(base).canonicalize(), p.canonicalize()) {
        if !real.starts_with(&root) {
            return None;
        }
        return Some(real);
    }
    Some(p)
}

/// 解析并应用 abi.json（JSON 字符串），按顺序加载所有启用的扩展。
pub fn load_manifest(json: *const c_char) -> i32 {
    let text = match cstr(json) {
        Some(t) => t,
        None => return VAPOR_ERR_ARG,
    };
    let manifest: AbiManifest = match serde_json::from_str(text) {
        Ok(m) => m,
        Err(e) => {
            crate::log::log_msg(crate::log::LOG_ERROR, &format!("abi.json 解析失败: {e}"));
            return VAPOR_ERR_ARG;
        }
    };
    let arch = current_arch();
    let mut ok = 0;
    for e in &manifest.extensions {
        if !e.enabled {
            continue;
        }
        if e.arch != "all" && e.arch != arch {
            continue;
        }
        let rel = e.path.clone().unwrap_or_else(|| match e.kind.as_str() {
            "c" => format!("lib{}.so", e.name),
            _ => format!("{}.py", e.name),
        });
        let full = match safe_lib_join(&lib_dir(), &rel) {
            Some(p) => p,
            None => {
                crate::log::log_msg(
                    crate::log::LOG_WARN,
                    &format!("扩展路径非法（必须在 lib/ 内）: {} -> {}", e.name, rel),
                );
                continue;
            }
        };
        let full_c = CString::new(full.to_string_lossy().as_bytes()).unwrap_or_default();
        let sym_c = CString::new(e.symbol.as_deref().unwrap_or("vapor_ext_init")).unwrap_or_default();
        let name_c = CString::new(e.name.as_str()).unwrap_or_default();
        let res = match e.kind.as_str() {
            "c" => load_c(full_c.as_ptr(), sym_c.as_ptr()),
            "python" | "py" => load_py(name_c.as_ptr()),
            _ => VAPOR_ERR_ARG,
        };
        match res {
            VAPOR_OK => {
                ok += 1;
                crate::log::log_msg(
                    crate::log::LOG_INFO,
                    &format!("扩展已加载: {} ({})", e.name, e.kind),
                );
            }
            code => crate::log::log_msg(
                crate::log::LOG_WARN,
                &format!("扩展加载失败: {} code={}", e.name, code),
            ),
        }
    }
    let _ = ok;
    VAPOR_OK
}

fn so_name(name_or_path: &str) -> String {
    if name_or_path.contains('/') || name_or_path.ends_with(".so") {
        name_or_path.to_string()
    } else {
        format!("lib{}.so", name_or_path)
    }
}

/// 加载 C 扩展：dlopen → dlsym(vapor_ext_init) → 传入 VaporApi 全功能表。
pub fn load_c(path: *const c_char, symbol: *const c_char) -> i32 {
    let path = match cstr(path) {
        Some(p) => p,
        None => return VAPOR_ERR_ARG,
    };
    let sym = cstr(symbol).unwrap_or("vapor_ext_init");
    let full = so_name(path);

    unsafe {
        let cpath = match CString::new(full.as_str()) {
            Ok(s) => s,
            Err(_) => return VAPOR_ERR_ARG,
        };
        // 用 RTLD_GLOBAL 让扩展导出的符号进入全局作用域，
        // 这样 vapor_ext_symbol() 通过 dlsym(RTLD_DEFAULT) 可解析到任意扩展符号。
        let handle = libc::dlopen(cpath.as_ptr(), libc::RTLD_NOW | libc::RTLD_GLOBAL);
        if handle.is_null() {
            crate::log::log_msg(
                crate::log::LOG_ERROR,
                &format!("dlopen 失败: {full} ({:?})", std::io::Error::last_os_error()),
            );
            return VAPOR_ERR_NOT_FOUND;
        }
        let csym = match CString::new(sym) {
            Ok(s) => s,
            Err(_) => return VAPOR_ERR_ARG,
        };
        let fnptr = libc::dlsym(handle, csym.as_ptr());
        if fnptr.is_null() {
            libc::dlclose(handle);
            return VAPOR_ERR_NOT_FOUND;
        }
        let init: vapor_abi::VaporExtInitFn = std::mem::transmute(fnptr);
        let api = crate::api::api_table();
        let rc = init(api as *const _ as *mut _);
        if rc != VAPOR_OK {
            libc::dlclose(handle);
            return VAPOR_ERR;
        }
        loaded()
            .lock()
            .unwrap()
            .insert(full.clone(), handle as usize);
    }
    VAPOR_OK
}

/// 加载 Python 扩展：通过内嵌解释器的 `import name`（由 python 模块实现）。
pub fn load_py(module: *const c_char) -> i32 {
    let name = match cstr(module) {
        Some(n) => n,
        None => return VAPOR_ERR_ARG,
    };
    let code = format!(
        "import importlib, sys; sys.path.insert(0, {lib:?}); importlib.import_module({name:?})",
        lib = lib_dir(),
    );
    crate::python::py_eval_str(&code)
}

/// 让任意组件按名字取全局符号（"啥都能调"的兜底通道）。
pub fn symbol_by_name(name: *const c_char) -> *mut c_void {
    let name = match cstr(name) {
        Some(n) => n,
        None => return std::ptr::null_mut(),
    };
    unsafe {
        let c = match CString::new(name) {
            Ok(s) => s,
            Err(_) => return std::ptr::null_mut(),
        };
        // RTLD_DEFAULT 让调用方解析到本进程已导出的任意 vapor_* 符号。
        libc::dlsym(libc::RTLD_DEFAULT, c.as_ptr())
    }
}

/// 关闭所有已加载的 C 扩展句柄。
pub fn shutdown_all() {
    if let Ok(mut map) = loaded().lock() {
        for (_name, handle) in map.drain() {
            if handle != 0 {
                unsafe { libc::dlclose(handle as *mut c_void) };
            }
        }
    }
}
