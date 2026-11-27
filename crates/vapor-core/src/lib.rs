//! vapor-core —— Vapor 渲染器核心库。
//!
//! 导出稳定 C ABI（见 include/vapor_abi.h），负责：生命周期、共享内存帧缓冲、
//! GLES 上屏、基础 UI、文件系统、C/Python 扩展加载、内嵌 CPython。
//! 所有平台无关逻辑在本机 x86_64 也能编译验证；Android 专属代码条件编译。

pub mod state;
pub mod fs;
pub mod extension;
pub mod api;
pub mod log;
pub mod python;
pub mod renderer;
pub mod ui;
pub mod jni_table;
pub mod jni;

use std::ffi::{c_char, c_void, CStr};
use std::os::raw::c_uint;
use vapor_abi::*;

fn cstr(p: *const c_char) -> Option<&'static str> {
    if p.is_null() {
        None
    } else {
        unsafe { CStr::from_ptr(p) }.to_str().ok()
    }
}

// ---------------------------------------------------------------------------
// 版本与生命周期
// ---------------------------------------------------------------------------

#[no_mangle]
pub extern "C" fn vapor_abi_version() -> i32 {
    VAPOR_ABI_VERSION
}

#[no_mangle]
pub extern "C" fn vapor_version(major: *mut i32, minor: *mut i32, patch: *mut i32) {
    unsafe {
        if !major.is_null() { *major = VAPOR_ENGINE_MAJOR; }
        if !minor.is_null() { *minor = VAPOR_ENGINE_MINOR; }
        if !patch.is_null() { *patch = VAPOR_ENGINE_PATCH; }
    }
}

#[no_mangle]
pub extern "C" fn vapor_cpython_version() -> *const c_char {
    // 静态字符串，生命周期为整个进程。
    c"3.15.2".as_ptr()
}

#[no_mangle]
pub extern "C" fn vapor_init(config: *const VaporConfig) -> i32 {
    renderer::init_logging();
    if config.is_null() {
        return VAPOR_ERR_ARG;
    }
    let cfg = unsafe { &*config };
    let app = cstr(cfg.app_dir).unwrap_or("").to_string();
    let private = cstr(cfg.private_dir).unwrap_or("").to_string();
    let external = cstr(cfg.external_dir).unwrap_or("").to_string();
    state::set_paths(&app, &private, &external);

    if cfg.width > 0 && cfg.height > 0 {
        state::resize_fb(cfg.width, cfg.height);
    }

    {
        let mut s = state::state().lock().unwrap();
        s.initialized = true;
    }

    if cfg.enable_python != 0 {
        let app_c = std::ffi::CString::new(app.as_str()).unwrap_or_default();
        python::py_init(app_c.as_ptr());
        ui::install_builtin_package();
    }
    log::log_msg(LOG_INFO, &format!("vapor 初始化完成 {}.{}.{} / CPython {}",
        VAPOR_ENGINE_MAJOR, VAPOR_ENGINE_MINOR, VAPOR_ENGINE_PATCH, VAPOR_CPYTHON_VERSION));
    VAPOR_OK
}

#[no_mangle]
pub extern "C" fn vapor_shutdown() -> i32 {
    python::shutdown();
    extension::shutdown_all();
    jni::shutdown_clear();
    let mut s = state::state().lock().unwrap();
    s.initialized = false;
    s.python_ready = false;
    VAPOR_OK
}

// ---------------------------------------------------------------------------
// 共享内存帧缓冲
// ---------------------------------------------------------------------------

#[no_mangle]
pub extern "C" fn vapor_fb_lock(out: *mut VaporFramebuffer) -> i32 {
    state::lock_framebuffer(out)
}

#[no_mangle]
pub extern "C" fn vapor_fb_unlock() -> i32 {
    {
        let mut s = match state::state().lock() {
            Ok(g) => g,
            Err(_) => return VAPOR_ERR_STATE,
        };
        if !s.initialized { return VAPOR_ERR_STATE; }
        s.generation = s.generation.wrapping_add(1);
    }
    renderer::present();
    VAPOR_OK
}

#[no_mangle]
pub extern "C" fn vapor_fb_resize(w: i32, h: i32) -> i32 {
    state::resize_fb(w, h)
}

#[no_mangle]
pub extern "C" fn vapor_fb_clear(rgba: u32) -> i32 {
    let mut s = match state::state().lock() {
        Ok(g) => g,
        Err(_) => return VAPOR_ERR_STATE,
    };
    let r = (rgba & 0xFF) as u8;
    let g = ((rgba >> 8) & 0xFF) as u8;
    let b = ((rgba >> 16) & 0xFF) as u8;
    let a = ((rgba >> 24) & 0xFF) as u8;
    for px in s.framebuffer.chunks_exact_mut(4) {
        px[0] = r; px[1] = g; px[2] = b; px[3] = a;
    }
    VAPOR_OK
}

// ---------------------------------------------------------------------------
// 事件 / 窗口
// ---------------------------------------------------------------------------

#[no_mangle]
pub extern "C" fn vapor_poll_event(out: *mut VaporEvent) -> i32 {
    if out.is_null() { return VAPOR_ERR_ARG; }
    let mut s = match state::state().lock() {
        Ok(g) => g,
        Err(_) => return VAPOR_ERR_STATE,
    };
    if let Some(e) = s.events.pop_front() {
        unsafe { *out = e; }
        VAPOR_OK
    } else {
        VAPOR_NO_EVENT
    }
}

#[no_mangle]
pub extern "C" fn vapor_event_count() -> i32 {
    match state::state().lock() {
        Ok(s) => s.events.len() as i32,
        Err(_) => 0,
    }
}

/// 供 NativeActivity 输入回调把事件推入队列。
pub fn push_event(e: VaporEvent) {
    let mut s = state::state().lock().unwrap();
    if s.events.len() < 256 {
        s.events.push_back(e);
    }
}

#[no_mangle]
pub extern "C" fn vapor_set_title(title: *const c_char) -> i32 {
    let t = match cstr(title) {
        Some(t) => t.to_string(),
        None => return VAPOR_ERR_ARG,
    };
    state::state().lock().unwrap().title = t;
    VAPOR_OK
}

#[no_mangle]
pub extern "C" fn vapor_set_bg_color(rgba: u32) -> i32 {
    state::state().lock().unwrap().bg_color = rgba;
    VAPOR_OK
}

// ---------------------------------------------------------------------------
// 文件系统
// ---------------------------------------------------------------------------

#[no_mangle]
pub extern "C" fn vapor_fs_read(path: *const c_char, out_buf: *mut *mut u8, out_len: *mut i32) -> i32 {
    fs::fs_read(path, out_buf, out_len)
}
#[no_mangle]
pub extern "C" fn vapor_fs_write(path: *const c_char, buf: *const u8, len: i32) -> i32 {
    fs::fs_write(path, buf, len)
}
#[no_mangle]
pub extern "C" fn vapor_fs_exists(path: *const c_char) -> i32 {
    fs::fs_exists(path)
}
#[no_mangle]
pub extern "C" fn vapor_fs_list(dir: *const c_char, out_json: *mut c_char, cap: i32) -> i32 {
    fs::fs_list(dir, out_json, cap)
}
#[no_mangle]
pub extern "C" fn vapor_fs_free(buf: *mut u8) {
    fs::fs_free(buf)
}
#[no_mangle]
pub extern "C" fn vapor_fs_app_dir() -> *const c_char {
    state::app_dir_ptr()
}
#[no_mangle]
pub extern "C" fn vapor_fs_private_dir() -> *const c_char {
    state::private_dir_ptr()
}
#[no_mangle]
pub extern "C" fn vapor_fs_external_dir() -> *const c_char {
    state::external_dir_ptr()
}

// ---------------------------------------------------------------------------
// Python / 扩展
// ---------------------------------------------------------------------------

#[no_mangle]
pub extern "C" fn vapor_py_init(app_dir: *const c_char) -> i32 {
    python::py_init(app_dir)
}
#[no_mangle]
pub extern "C" fn vapor_py_run_file(path: *const c_char) -> i32 {
    python::py_run_file(path)
}
#[no_mangle]
pub extern "C" fn vapor_py_eval(code: *const c_char) -> i32 {
    match cstr(code) {
        Some(c) => python::py_eval_str(c),
        None => VAPOR_ERR_ARG,
    }
}
#[no_mangle]
pub extern "C" fn vapor_py_install_requirements(path: *const c_char) -> i32 {
    python::py_install_requirements(path)
}

#[no_mangle]
pub extern "C" fn vapor_ext_load(manifest_json: *const c_char) -> i32 {
    extension::load_manifest(manifest_json)
}
#[no_mangle]
pub extern "C" fn vapor_ext_load_c(path: *const c_char, symbol: *const c_char) -> i32 {
    extension::load_c(path, symbol)
}
#[no_mangle]
pub extern "C" fn vapor_ext_load_py(module: *const c_char) -> i32 {
    extension::load_py(module)
}
#[no_mangle]
pub extern "C" fn vapor_ext_symbol(name: *const c_char) -> *mut c_void {
    extension::symbol_by_name(name)
}

#[no_mangle]
pub extern "C" fn vapor_log(level: i32, msg: *const c_char) {
    log::vapor_log(level, msg)
}

// ---------------------------------------------------------------------------
// JNI 桥接：C / Python 扩展调用任意 JNI
// ---------------------------------------------------------------------------

#[no_mangle]
pub extern "C" fn vapor_jni_set_vm(java_vm: *mut c_void) -> i32 {
    jni::set_vm(java_vm)
}
#[no_mangle]
pub extern "C" fn vapor_jni_get_vm() -> *mut c_void {
    jni::get_vm()
}
#[no_mangle]
pub extern "C" fn vapor_jni_get_env() -> *mut c_void {
    jni::get_env()
}
#[no_mangle]
pub extern "C" fn vapor_jni_detach() -> i32 {
    jni::detach()
}
#[no_mangle]
pub extern "C" fn vapor_jni_call(
    req_json: *const c_char,
    out_json: *mut c_char,
    cap: i32,
) -> i32 {
    jni::call(req_json, out_json, cap)
}
#[no_mangle]
pub extern "C" fn vapor_jni_release(handle: i64) -> i32 {
    jni::release(handle)
}

/// 返回全功能函数表指针，C 扩展拿它即可调用渲染器全部能力。
#[no_mangle]
pub extern "C" fn vapor_api() -> *const VaporApi {
    api::api_table() as *const VaporApi
}

// ---------------------------------------------------------------------------
// NativeActivity 桥接（由 vapor-renderer / vapor-packager 的 JNI 调用）
// ---------------------------------------------------------------------------

/// JNI 入口：由 Java 层 RendererActivity 在 Surface 创建后调用。
/// 参数 surface 是 android.view.Surface 对象，用 ANativeWindow_fromSurface 取原生窗口。
/// 同时从 JNIEnv::GetJavaVM 取出 JavaVM* 注入桥接层，使 C/Python 扩展开箱即调 JNI。
#[cfg(target_os = "android")]
#[no_mangle]
#[allow(non_snake_case)]
pub extern "C" fn Java_com_vapor_RendererActivity_nativeInit(
    env: *mut c_void,
    _thiz: *mut c_void,
    surface: *mut c_void,
    w: i32,
    h: i32,
    app_dir: *const c_char,
    private_dir: *const c_char,
) {
    // 1) 注入 JavaVM*（JNIEnv::GetJavaVM 见 jni_table.rs）
    unsafe {
        let table = &*(*(env as *const *const jni_table::JNINativeTable));
        type GetJavaVMFn = unsafe extern "C" fn(*mut c_void, *mut *mut c_void) -> i32;
        let get_vm: GetJavaVMFn = std::mem::transmute_copy(&table.GetJavaVM);
        let mut vm: *mut c_void = std::ptr::null_mut();
        if get_vm(env, &mut vm) == 0 && !vm.is_null() {
            jni::set_vm(vm);
        }
    }
    // 2) 从 Surface 取 ANativeWindow 并初始化 EGL/GLES
    unsafe {
        let window = android_native_window_from_surface(env, surface);
        if !window.is_null() {
            renderer::init_from_window(window);
        }
    }
    state::resize_fb(w, h);
    // 3) 设置路径（供 bundled_python_home 定位解压后的 Python 运行时）
    let app = cstr(app_dir).unwrap_or("").to_string();
    let private = cstr(private_dir).unwrap_or("").to_string();
    state::set_paths(&app, &private, "");
    // 4) 初始化引擎 + 启动内嵌 CPython 执行逻辑包 main.py
    let cfg = VaporConfig {
        app_dir: app_dir,
        private_dir: private_dir,
        external_dir: std::ptr::null(),
        width: w,
        height: h,
        enable_python: 1,
    };
    vapor_init(&cfg);
}

#[cfg(target_os = "android")]
#[no_mangle]
#[allow(non_snake_case)]
pub extern "C" fn Java_com_vapor_RendererActivity_nativeResize(
    _env: *mut c_void, _thiz: *mut c_void, w: i32, h: i32) {
    state::resize_fb(w, h);
}

#[cfg(target_os = "android")]
#[no_mangle]
#[allow(non_snake_case)]
pub extern "C" fn Java_com_vapor_RendererActivity_nativeRender(_env: *mut c_void, _thiz: *mut c_void) {
    renderer::present();
}

#[cfg(target_os = "android")]
#[no_mangle]
#[allow(non_snake_case)]
pub extern "C" fn Java_com_vapor_RendererActivity_nativeShutdown(_env: *mut c_void, _thiz: *mut c_void) {
    vapor_shutdown();
}

#[cfg(target_os = "android")]
extern "C" {
    // ANativeWindow_fromSurface(JNIEnv*, jobject) -> ANativeWindow*
    #[link_name = "ANativeWindow_fromSurface"]
    fn android_native_window_from_surface(env: *mut c_void, surface: *mut c_void) -> *mut c_void;
}

#[cfg(not(target_os = "android"))]
#[allow(non_snake_case)]
pub extern "C" fn Java_com_vapor_RendererActivity_nativeInit(
    _env: *mut c_void, _thiz: *mut c_void, _surface: *mut c_void, _w: i32, _h: i32,
    _app_dir: *const c_char, _private_dir: *const c_char) {}
#[cfg(not(target_os = "android"))]
#[allow(non_snake_case)]
pub extern "C" fn Java_com_vapor_RendererActivity_nativeResize(
    _env: *mut c_void, _thiz: *mut c_void, _w: i32, _h: i32) {}
#[cfg(not(target_os = "android"))]
#[allow(non_snake_case)]
pub extern "C" fn Java_com_vapor_RendererActivity_nativeRender(_env: *mut c_void, _thiz: *mut c_void) {}
#[cfg(not(target_os = "android"))]
#[allow(non_snake_case)]
pub extern "C" fn Java_com_vapor_RendererActivity_nativeShutdown(_env: *mut c_void, _thiz: *mut c_void) {}

#[allow(dead_code)]
fn _use_c_uint(_: c_uint) {}
