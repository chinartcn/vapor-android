//! 日志：Android 上走 android_logger/log crate，本机上输出到 stderr。

use std::ffi::CStr;
use std::os::raw::c_char;
pub use vapor_abi::{LOG_DEBUG, LOG_ERROR, LOG_INFO, LOG_VERBOSE, LOG_WARN};

pub const LOG_VERBOSE_I: i32 = LOG_VERBOSE;
pub const LOG_DEBUG_I: i32 = LOG_DEBUG;
pub const LOG_INFO_I: i32 = LOG_INFO;
pub const LOG_WARN_I: i32 = LOG_WARN;
pub const LOG_ERROR_I: i32 = LOG_ERROR;

pub fn log_msg(level: i32, msg: &str) {
    #[cfg(target_os = "android")]
    {
        match level {
            LOG_VERBOSE => log::log!(target: "vapor", log::Level::Trace, "{msg}"),
            LOG_DEBUG => log::log!(target: "vapor", log::Level::Debug, "{msg}"),
            LOG_INFO => log::info!(target: "vapor", "{msg}"),
            LOG_WARN => log::warn!(target: "vapor", "{msg}"),
            LOG_ERROR => log::error!(target: "vapor", "{msg}"),
            _ => log::info!(target: "vapor", "{msg}"),
        }
    }
    #[cfg(not(target_os = "android"))]
    {
        let tag = match level {
            LOG_VERBOSE => "V",
            LOG_DEBUG => "D",
            LOG_INFO => "I",
            LOG_WARN => "W",
            LOG_ERROR => "E",
            _ => "I",
        };
        eprintln!("[{tag}/vapor] {msg}");
    }
}

pub extern "C" fn vapor_log(level: i32, msg: *const c_char) {
    if msg.is_null() {
        return;
    }
    let s = unsafe { CStr::from_ptr(msg) }.to_string_lossy();
    log_msg(level, &s);
}
