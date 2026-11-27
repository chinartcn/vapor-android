//! vapor-abi —— Vapor 稳定 C ABI 的 Rust 侧类型镜像。
//!
//! 所有 `#[repr(C)]` 结构体与 `include/vapor_abi.h` 严格一一对应。
//! 本 crate 无平台依赖，可在任意目标（含本机 x86_64 验证环境）编译。

#![allow(non_camel_case_types)]

pub const VAPOR_ABI_VERSION: i32 = 1;
pub const VAPOR_ENGINE_MAJOR: i32 = 1;
pub const VAPOR_ENGINE_MINOR: i32 = 0;
pub const VAPOR_ENGINE_PATCH: i32 = 0;
/// 双架构统一锁定的 CPython 版本。
pub const VAPOR_CPYTHON_VERSION: &str = "3.15.2";

pub const VAPOR_OK: i32 = 0;
pub const VAPOR_ERR: i32 = -1;
pub const VAPOR_ERR_ARG: i32 = -2;
pub const VAPOR_ERR_STATE: i32 = -3;
pub const VAPOR_ERR_NOT_FOUND: i32 = -4;
pub const VAPOR_ERR_PERMISSION: i32 = -5;
pub const VAPOR_NO_EVENT: i32 = 1;

pub const FMT_RGBA8888: i32 = 0;
pub const FMT_RGB565: i32 = 1;

pub const EVT_NONE: i32 = 0;
pub const EVT_MOTION: i32 = 1;
pub const EVT_BUTTON: i32 = 2;
pub const EVT_KEY: i32 = 3;
pub const EVT_RESIZE: i32 = 4;
pub const EVT_RESUME: i32 = 5;
pub const EVT_PAUSE: i32 = 6;
pub const EVT_DESTROY: i32 = 7;

pub const LOG_VERBOSE: i32 = 2;
pub const LOG_DEBUG: i32 = 3;
pub const LOG_INFO: i32 = 4;
pub const LOG_WARN: i32 = 5;
pub const LOG_ERROR: i32 = 6;

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct VaporConfig {
    pub app_dir: *const std::os::raw::c_char,
    pub private_dir: *const std::os::raw::c_char,
    pub external_dir: *const std::os::raw::c_char,
    pub width: i32,
    pub height: i32,
    pub enable_python: i32,
}

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct VaporFramebuffer {
    pub pixels: *mut u8,
    pub width: i32,
    pub height: i32,
    pub stride: i32,
    pub format: i32,
    pub fd: i32,
    pub generation: u64,
}

#[repr(C)]
#[derive(Clone, Copy, Debug, Default)]
pub struct VaporEvent {
    pub event_type: i32,
    pub x: i32,
    pub y: i32,
    pub x2: i32,
    pub y2: i32,
    pub button: i32,
    pub action: i32,
    pub keycode: i32,
    pub timestamp_ms: u64,
}

/// 全功能函数表，与头文件 `VaporApi` 严格对齐。
#[repr(C)]
pub struct VaporApi {
    pub abi_version: unsafe extern "C" fn() -> i32,
    pub version: unsafe extern "C" fn(*mut i32, *mut i32, *mut i32),
    pub cpython_version: unsafe extern "C" fn() -> *const std::os::raw::c_char,
    pub init: unsafe extern "C" fn(*const VaporConfig) -> i32,
    pub shutdown: unsafe extern "C" fn() -> i32,

    pub fb_lock: unsafe extern "C" fn(*mut VaporFramebuffer) -> i32,
    pub fb_unlock: unsafe extern "C" fn() -> i32,
    pub fb_resize: unsafe extern "C" fn(i32, i32) -> i32,
    pub fb_clear: unsafe extern "C" fn(u32) -> i32,

    pub poll_event: unsafe extern "C" fn(*mut VaporEvent) -> i32,
    pub event_count: unsafe extern "C" fn() -> i32,
    pub set_title: unsafe extern "C" fn(*const std::os::raw::c_char) -> i32,
    pub set_bg_color: unsafe extern "C" fn(u32) -> i32,

    pub fs_read: unsafe extern "C" fn(*const std::os::raw::c_char, *mut *mut u8, *mut i32) -> i32,
    pub fs_write: unsafe extern "C" fn(*const std::os::raw::c_char, *const u8, i32) -> i32,
    pub fs_exists: unsafe extern "C" fn(*const std::os::raw::c_char) -> i32,
    pub fs_list: unsafe extern "C" fn(*const std::os::raw::c_char, *mut std::os::raw::c_char, i32) -> i32,
    pub fs_free: unsafe extern "C" fn(*mut u8),
    pub fs_app_dir: unsafe extern "C" fn() -> *const std::os::raw::c_char,
    pub fs_private_dir: unsafe extern "C" fn() -> *const std::os::raw::c_char,
    pub fs_external_dir: unsafe extern "C" fn() -> *const std::os::raw::c_char,

    pub py_init: unsafe extern "C" fn(*const std::os::raw::c_char) -> i32,
    pub py_run_file: unsafe extern "C" fn(*const std::os::raw::c_char) -> i32,
    pub py_eval: unsafe extern "C" fn(*const std::os::raw::c_char) -> i32,
    pub py_install_requirements: unsafe extern "C" fn(*const std::os::raw::c_char) -> i32,

    pub ext_load: unsafe extern "C" fn(*const std::os::raw::c_char) -> i32,
    pub ext_load_c: unsafe extern "C" fn(*const std::os::raw::c_char, *const std::os::raw::c_char) -> i32,
    pub ext_load_py: unsafe extern "C" fn(*const std::os::raw::c_char) -> i32,
    pub ext_symbol: unsafe extern "C" fn(*const std::os::raw::c_char) -> *mut std::os::raw::c_void,

    pub log: unsafe extern "C" fn(i32, *const std::os::raw::c_char),

    // ---- JNI 桥接（v1.1 追加在尾部，前向兼容） ----
    pub jni_set_vm: unsafe extern "C" fn(*mut std::os::raw::c_void) -> i32,
    pub jni_get_vm: unsafe extern "C" fn() -> *mut std::os::raw::c_void,
    pub jni_get_env: unsafe extern "C" fn() -> *mut std::os::raw::c_void,
    pub jni_detach: unsafe extern "C" fn() -> i32,
    pub jni_call: unsafe extern "C" fn(
        *const std::os::raw::c_char,
        *mut std::os::raw::c_char,
        i32,
    ) -> i32,
    pub jni_release: unsafe extern "C" fn(i64) -> i32,
}

/// C 扩展入口：`int vapor_ext_init(VaporApi* api)`。
pub type VaporExtInitFn = unsafe extern "C" fn(*mut VaporApi) -> i32;

#[cfg(test)]
mod tests {
    use super::*;
    use std::mem::size_of;

    #[test]
    fn abi_layout_matches_c_header() {
        // 与 C 端 `cc -sizeof` 结果一致；防止结构体漂移破坏 ABI。
        assert_eq!(size_of::<VaporFramebuffer>(), 40); // ptr8 + 4*5=20 + pad4 + u64 8 = 40
        assert_eq!(size_of::<VaporEvent>(), 40); // 4*8=32 + u64 8 = 40
        assert_eq!(size_of::<VaporConfig>(), 40);
        assert_eq!(VAPOR_ABI_VERSION, 1);
        assert_eq!(VAPOR_CPYTHON_VERSION, "3.15.2");
        // 函数表条目数 = 头文件 VaporApi 字段数（30 基础 + 6 JNI = 36）
        let api_size = size_of::<VaporApi>();
        assert_eq!(api_size, 36 * size_of::<usize>());
    }
}
