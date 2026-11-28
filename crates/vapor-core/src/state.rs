//! 全局运行时状态。使用 `OnceLock<Mutex<State>>` 保证跨 ABI 调用与 Python GIL
//! 释放期间的状态安全。

use std::collections::VecDeque;
use std::ffi::CString;
use std::sync::{Mutex, OnceLock};
use vapor_abi::{VaporEvent, VaporFramebuffer, VAPOR_ERR_STATE, VAPOR_OK};

pub const DEFAULT_W: i32 = 720;
pub const DEFAULT_H: i32 = 1280;

pub struct State {
    pub initialized: bool,
    pub width: i32,
    pub height: i32,
    /// RGBA8888 像素缓冲，Python 通过 C ABI 直接写这里。
    pub framebuffer: Vec<u8>,
    pub generation: u64,
    pub bg_color: u32,
    pub title: String,
    pub events: VecDeque<VaporEvent>,
    pub app_dir: String,
    pub private_dir: String,
    pub external_dir: String,
    pub python_ready: bool,
    /// `vapor_fb_lock` 与 `vapor_fb_unlock` 之间的互斥标记。
    /// 加锁期间禁止 resize（会重新分配 Vec 并让已交出的指针悬垂）。
    pub fb_locked: bool,
    /// 设备像素密度（dp -> px 的倍数）。帧缓冲用逻辑尺寸，GL 视口用物理尺寸。
    pub density: f32,
}

impl Default for State {
    fn default() -> Self {
        Self {
            initialized: false,
            width: DEFAULT_W,
            height: DEFAULT_H,
            framebuffer: vec![0; (DEFAULT_W * DEFAULT_H * 4) as usize],
            generation: 0,
            bg_color: 0xFF101418, // 0xAARRGGBB：深灰蓝
            title: "Vapor".into(),
            events: VecDeque::with_capacity(128),
            app_dir: String::new(),
            private_dir: String::new(),
            external_dir: String::new(),
            python_ready: false,
            fb_locked: false,
            density: 1.0,
        }
    }
}

static STATE: OnceLock<Mutex<State>> = OnceLock::new();

pub fn state() -> &'static Mutex<State> {
    STATE.get_or_init(|| Mutex::new(State::default()))
}

/// 缓存的 CString 路径，避免每次 ABI 调用重复分配。
static PATHS: OnceLock<Mutex<[CString; 3]>> = OnceLock::new();
/// 旧路径字符串：`set_paths` 替换后仍保留，避免此前通过 `vapor_fs_*_dir()`
/// 返回给 C/Python 的指针悬垂。路径只在初始化/重建时设置，数量极少。
static RETIRED_PATHS: OnceLock<Mutex<Vec<CString>>> = OnceLock::new();

fn retired_paths() -> &'static Mutex<Vec<CString>> {
    RETIRED_PATHS.get_or_init(|| Mutex::new(Vec::new()))
}

fn empty_cstring() -> CString {
    CString::new("").unwrap()
}

pub fn set_paths(app: &str, private: &str, external: &str) {
    let mut s = state().lock().unwrap();
    s.app_dir = app.into();
    s.private_dir = private.into();
    s.external_dir = external.into();
    let arr = [
        CString::new(app).unwrap_or_else(|_| empty_cstring()),
        CString::new(private).unwrap_or_else(|_| empty_cstring()),
        CString::new(external).unwrap_or_else(|_| empty_cstring()),
    ];
    let slot = PATHS.get_or_init(|| Mutex::new([empty_cstring(), empty_cstring(), empty_cstring()]));
    let mut paths = slot.lock().unwrap();
    let old = std::mem::replace(&mut *paths, arr);
    drop(paths);
    // 保留旧 CString 直到进程结束：此前返回过的指针继续有效，不再悬垂。
    if old.iter().any(|p| !p.as_bytes().is_empty()) {
        if let Ok(mut retired) = retired_paths().lock() {
            retired.extend(old);
        }
    }
}

fn path_ptr(idx: usize) -> *const std::os::raw::c_char {
    PATHS
        .get_or_init(|| Mutex::new([empty_cstring(), empty_cstring(), empty_cstring()]))
        .lock()
        .unwrap()[idx]
        .as_ptr()
}

pub fn app_dir_ptr() -> *const std::os::raw::c_char {
    path_ptr(0)
}
pub fn private_dir_ptr() -> *const std::os::raw::c_char {
    path_ptr(1)
}
pub fn external_dir_ptr() -> *const std::os::raw::c_char {
    path_ptr(2)
}

/// 设置设备像素密度（Android DisplayMetrics.density）；限制在合理区间避免异常输入。
pub fn set_density(d: f32) {
    let d = if d.is_finite() { d.clamp(0.5, 4.0) } else { 1.0 };
    state().lock().unwrap().density = d;
}

pub fn density() -> f32 {
    state().lock().unwrap().density
}

/// 物理像素 -> 逻辑像素（帧缓冲尺寸 / 触摸坐标）。
pub fn to_logical(v: f32) -> f32 {
    let d = density();
    if d > 0.0 { v / d } else { v }
}

/// 按当前 density 把物理尺寸换算为帧缓冲逻辑尺寸并 resize。
pub fn resize_fb_physical(w: i32, h: i32) -> i32 {
    let d = density();
    let lw = ((w as f32 / d).round() as i32).max(1);
    let lh = ((h as f32 / d).round() as i32).max(1);
    resize_fb(lw, lh)
}

pub fn resize_fb(w: i32, h: i32) -> i32 {    if w <= 0 || h <= 0 || (w as i64) * (h as i64) > 16_777_216 {
        return vapor_abi::VAPOR_ERR_ARG;
    }
    let mut s = state().lock().unwrap();
    if s.fb_locked {
        // 帧缓冲已借出裸指针，此时 resize 会让调用方持有的指针悬垂。
        return vapor_abi::VAPOR_ERR_STATE;
    }
    s.framebuffer.resize((w * h * 4) as usize, 0);
    s.width = w;
    s.height = h;
    VAPOR_OK
}

pub fn unlock_framebuffer() -> i32 {
    let mut s = match state().lock() {
        Ok(g) => g,
        Err(_) => return VAPOR_ERR_STATE,
    };
    if !s.initialized {
        return VAPOR_ERR_STATE;
    }
    s.fb_locked = false;
    s.generation = s.generation.wrapping_add(1);
    VAPOR_OK
}

pub fn lock_framebuffer(out: *mut VaporFramebuffer) -> i32 {
    if out.is_null() {
        return vapor_abi::VAPOR_ERR_ARG;
    }
    let mut s = match state().lock() {
        Ok(g) => g,
        Err(_) => return VAPOR_ERR_STATE,
    };
    if !s.initialized {
        return VAPOR_ERR_STATE;
    }
    // 幂等：允许调用方重复加锁（例如重复取 framebuffer()），返回同一块内存。
    s.fb_locked = true;
    let stride = s.width * 4;
    unsafe {
        // 锁存续期间禁止 resize，因此这里交出的指针在 unlock 前保持有效。
        (*out).pixels = s.framebuffer.as_mut_ptr();
        (*out).width = s.width;
        (*out).height = s.height;
        (*out).stride = stride;
        (*out).format = vapor_abi::FMT_RGBA8888;
        (*out).fd = -1; // 同进程共享；跨进程扩展可替换为 ashmem
        (*out).generation = s.generation;
    }
    VAPOR_OK
}
