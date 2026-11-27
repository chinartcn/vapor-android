//! 构造并静态持有 VaporApi 全功能函数表（传给 C 扩展）。
//! 函数指针全部指向 lib.rs 中导出的稳定 C ABI 函数。

use std::ffi::c_char;
use std::sync::OnceLock;
use vapor_abi::VaporApi;

extern "C" {
    fn vapor_abi_version() -> i32;
    fn vapor_version(major: *mut i32, minor: *mut i32, patch: *mut i32);
    fn vapor_cpython_version() -> *const c_char;
    fn vapor_init(config: *const vapor_abi::VaporConfig) -> i32;
    fn vapor_shutdown() -> i32;
    fn vapor_fb_lock(out: *mut vapor_abi::VaporFramebuffer) -> i32;
    fn vapor_fb_unlock() -> i32;
    fn vapor_fb_resize(w: i32, h: i32) -> i32;
    fn vapor_fb_clear(rgba: u32) -> i32;
    fn vapor_poll_event(out: *mut vapor_abi::VaporEvent) -> i32;
    fn vapor_event_count() -> i32;
    fn vapor_set_title(title: *const c_char) -> i32;
    fn vapor_set_bg_color(rgba: u32) -> i32;
    fn vapor_fs_read(path: *const c_char, out_buf: *mut *mut u8, out_len: *mut i32) -> i32;
    fn vapor_fs_write(path: *const c_char, buf: *const u8, len: i32) -> i32;
    fn vapor_fs_exists(path: *const c_char) -> i32;
    fn vapor_fs_list(dir: *const c_char, out_json: *mut c_char, cap: i32) -> i32;
    fn vapor_fs_free(buf: *mut u8);
    fn vapor_fs_app_dir() -> *const c_char;
    fn vapor_fs_private_dir() -> *const c_char;
    fn vapor_fs_external_dir() -> *const c_char;
    fn vapor_py_init(app_dir: *const c_char) -> i32;
    fn vapor_py_run_file(path: *const c_char) -> i32;
    fn vapor_py_eval(code: *const c_char) -> i32;
    fn vapor_py_install_requirements(path: *const c_char) -> i32;
    fn vapor_ext_load(manifest: *const c_char) -> i32;
    fn vapor_ext_load_c(path: *const c_char, symbol: *const c_char) -> i32;
    fn vapor_ext_load_py(module: *const c_char) -> i32;
    fn vapor_ext_symbol(name: *const c_char) -> *mut std::ffi::c_void;
    fn vapor_log(level: i32, msg: *const c_char);
    fn vapor_jni_set_vm(vm: *mut std::ffi::c_void) -> i32;
    fn vapor_jni_get_vm() -> *mut std::ffi::c_void;
    fn vapor_jni_get_env() -> *mut std::ffi::c_void;
    fn vapor_jni_detach() -> i32;
    fn vapor_jni_call(req: *const c_char, out: *mut c_char, cap: i32) -> i32;
    fn vapor_jni_release(handle: i64) -> i32;
}

static API: OnceLock<VaporApi> = OnceLock::new();

pub fn api_table() -> &'static VaporApi {
    API.get_or_init(|| VaporApi {
        abi_version: vapor_abi_version,
        version: vapor_version,
        cpython_version: vapor_cpython_version,
        init: vapor_init,
        shutdown: vapor_shutdown,
        fb_lock: vapor_fb_lock,
        fb_unlock: vapor_fb_unlock,
        fb_resize: vapor_fb_resize,
        fb_clear: vapor_fb_clear,
        poll_event: vapor_poll_event,
        event_count: vapor_event_count,
        set_title: vapor_set_title,
        set_bg_color: vapor_set_bg_color,
        fs_read: vapor_fs_read,
        fs_write: vapor_fs_write,
        fs_exists: vapor_fs_exists,
        fs_list: vapor_fs_list,
        fs_free: vapor_fs_free,
        fs_app_dir: vapor_fs_app_dir,
        fs_private_dir: vapor_fs_private_dir,
        fs_external_dir: vapor_fs_external_dir,
        py_init: vapor_py_init,
        py_run_file: vapor_py_run_file,
        py_eval: vapor_py_eval,
        py_install_requirements: vapor_py_install_requirements,
        ext_load: vapor_ext_load,
        ext_load_c: vapor_ext_load_c,
        ext_load_py: vapor_ext_load_py,
        ext_symbol: vapor_ext_symbol,
        log: vapor_log,
        jni_set_vm: vapor_jni_set_vm,
        jni_get_vm: vapor_jni_get_vm,
        jni_get_env: vapor_jni_get_env,
        jni_detach: vapor_jni_detach,
        jni_call: vapor_jni_call,
        jni_release: vapor_jni_release,
    })
}
