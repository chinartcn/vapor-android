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

/// 把 JNI 传入的 jstring（本质是 jobject 指针，不是 char*）正确转换为 Rust String。
///
/// 关键：Java 侧 nativeInit(..., String appDir, String privateDir) 传给 native 层的是
/// `jstring`（指向 VM 内部字符串的 jobject），绝不能当 `const char*` 直接 `CStr::from_ptr`
/// 读取——那会把 jobject 的内存当字符串，通常得到空串或乱码。这正是真机上 Python 路径
/// 变成 `/python/...`、encodings 找不到的根因。必须走 JNIEnv 函数表的 GetStringUTFChars。
#[cfg(target_os = "android")]
unsafe fn jstring_to_string(env: *mut c_void, s: *const c_char) -> String {
    if s.is_null() {
        return String::new();
    }
    let table = &*(*(env as *const *const jni_table::JNINativeTable));
    type GetCharsFn = unsafe extern "C" fn(*mut c_void, *mut c_void, *mut u8) -> *const c_char;
    type ReleaseFn = unsafe extern "C" fn(*mut c_void, *mut c_void, *const c_char);
    let get: GetCharsFn = std::mem::transmute_copy(&table.GetStringUTFChars);
    let rel: ReleaseFn = std::mem::transmute_copy(&table.ReleaseStringUTFChars);
    let chars = get(env, s as *mut c_void, std::ptr::null_mut());
    if chars.is_null() {
        return String::new();
    }
    let out = CStr::from_ptr(chars).to_string_lossy().into_owned();
    rel(env, s as *mut c_void, chars);
    out
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
    thiz: *mut c_void,
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
    // 1.5) 保存 Activity 句柄（全局引用）：Python 侧申请运行时权限要用它。
    let activity = jni::register_object(env, thiz);
    if activity != 0 {
        ACTIVITY_HANDLE.store(activity, std::sync::atomic::Ordering::Relaxed);
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
    // 关键修复：app_dir / private_dir 是 JNI jstring（jobject 指针），必须用
    // GetStringUTFChars 转换；直接当 char* 读会得到空串，导致 Python 路径错乱。
    let app = unsafe { jstring_to_string(env, app_dir) };
    let private = unsafe { jstring_to_string(env, private_dir) };
    log::log_msg(log::LOG_INFO, &format!("nativeInit 路径: app={app}, private={private}"));
    state::set_paths(&app, &private, "");
    // 4) 初始化引擎 + 启动内嵌 CPython 执行逻辑包 main.py
    // 用转换后的 Rust String 构造 CString，保证传入的是有效的 UTF-8 C 字符串。
    let app_c = std::ffi::CString::new(app.as_str()).unwrap_or_default();
    let private_c = std::ffi::CString::new(private.as_str()).unwrap_or_default();
    let cfg = VaporConfig {
        app_dir: app_c.as_ptr(),
        private_dir: private_c.as_ptr(),
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

/// Activity 的 JNI 句柄（nativeInit 时注册），供 Python 侧 requestPermissions 等使用。
#[cfg(target_os = "android")]
static ACTIVITY_HANDLE: std::sync::atomic::AtomicI64 = std::sync::atomic::AtomicI64::new(0);

/// 权限申请结果：requestCode -> 1/0（poll 时取走）。
#[cfg(target_os = "android")]
fn permission_results() -> &'static std::sync::Mutex<std::collections::HashMap<i32, i32>> {
    static RESULTS: std::sync::OnceLock<std::sync::Mutex<std::collections::HashMap<i32, i32>>> =
        std::sync::OnceLock::new();
    RESULTS.get_or_init(|| std::sync::Mutex::new(std::collections::HashMap::new()))
}

/// 取 Activity 句柄（0 表示不可用）。Python：rust.Vapor.activity()
#[cfg(target_os = "android")]
#[no_mangle]
pub extern "C" fn vapor_activity() -> i64 {
    ACTIVITY_HANDLE.load(std::sync::atomic::Ordering::Relaxed)
}

/// 取一条权限申请结果：-1=仍在等待，0=拒绝，1=授予（取走后清除）。
#[cfg(target_os = "android")]
#[no_mangle]
pub extern "C" fn vapor_permission_poll(code: i32) -> i32 {
    permission_results()
        .lock()
        .ok()
        .and_then(|mut map| map.remove(&code))
        .unwrap_or(-1)
}

/// JNI 入口：Java onRequestPermissionsResult 回调。
#[cfg(target_os = "android")]
#[no_mangle]
#[allow(non_snake_case)]
pub extern "C" fn Java_com_vapor_RendererActivity_nativePermissionResult(
    _env: *mut c_void,
    _thiz: *mut c_void,
    code: i32,
    granted: u8,
) {
    if let Ok(mut map) = permission_results().lock() {
        map.insert(code, granted as i32);
    }
}

#[cfg(target_os = "android")]
fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// JNI 入口：Java 侧把 MotionEvent 归一化后转发进来。
///
/// action: 0=DOWN 1=UP 2=MOVE 3=CANCEL（多指时 Java 已按 actionIndex 取当前手指）。
/// x2/y2 是第二根手指的坐标（单指时等于 x/y），keycode 字段复用为 pointer_count。
/// 这些事件进入 Rust 的事件队列，Python 的 PointerRouter 每帧 poll 出来做手势识别。
#[cfg(target_os = "android")]
#[no_mangle]
#[allow(non_snake_case)]
pub extern "C" fn Java_com_vapor_RendererActivity_nativeTouch(
    _env: *mut c_void,
    _thiz: *mut c_void,
    action: i32,
    x: f32,
    y: f32,
    pointer_id: i32,
    pointer_count: i32,
    x2: f32,
    y2: f32,
) {
    let (event_type, mapped) = match action {
        0 => (EVT_BUTTON, 0), // down
        1 => (EVT_BUTTON, 1), // up
        2 => (EVT_MOTION, 2), // move
        3 => (EVT_BUTTON, 3), // cancel
        4 => (EVT_BUTTON, 4), // 第二根手指按下
        5 => (EVT_BUTTON, 5), // 第二根手指抬起
        _ => return,
    };
    push_event(VaporEvent {
        event_type,
        x: x as i32,
        y: y as i32,
        x2: x2 as i32,
        y2: y2 as i32,
        button: pointer_id,
        action: mapped,
        keycode: pointer_count,
        timestamp_ms: now_ms(),
    });
}

/// JNI 入口：滚轮 / 触控板（Android 的 ACTION_SCROLL）。
/// x=水平滚动量、y=垂直滚动量（像素），走 EVT_WHEEL 事件。
#[cfg(target_os = "android")]
#[no_mangle]
#[allow(non_snake_case)]
pub extern "C" fn Java_com_vapor_RendererActivity_nativeWheel(
    _env: *mut c_void,
    _thiz: *mut c_void,
    dx: f32,
    dy: f32,
) {
    push_event(VaporEvent {
        event_type: EVT_WHEEL,
        x: dx as i32,
        y: dy as i32,
        timestamp_ms: now_ms(),
        ..Default::default()
    });
}

/// JNI 入口：按键（action: 0=down 1=up）。返回键 keycode=4，由 Python 决定是否处理。
#[cfg(target_os = "android")]
#[no_mangle]
#[allow(non_snake_case)]
pub extern "C" fn Java_com_vapor_RendererActivity_nativeKey(
    _env: *mut c_void,
    _thiz: *mut c_void,
    keycode: i32,
    action: i32,
) {
    push_event(VaporEvent {
        event_type: EVT_KEY,
        keycode,
        action,
        timestamp_ms: now_ms(),
        ..Default::default()
    });
}

// ANativeWindow_fromSurface 属于 libandroid.so，必须显式链接。
// 不能依赖构建工具（cargo-ndk / 裸 cargo build --target）的隐式行为：
// 一旦缺失 -landroid，System.loadLibrary 阶段就会抛 UnsatisfiedLinkError。
#[cfg(target_os = "android")]
#[link(name = "android")]
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
