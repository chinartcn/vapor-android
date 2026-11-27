//! 基础文件系统：逻辑包目录、私有目录、外部目录三处统一访问。
//! 读操作优先逻辑包，其次私有目录；写操作落私有目录（外部目录可选）。

use crate::state;
use std::ffi::{CStr, CString};
use std::fs;
use std::io::Write;
use std::os::raw::{c_char, c_void};
use std::path::{Path, PathBuf};
use vapor_abi::{VAPOR_ERR_ARG, VAPOR_ERR_NOT_FOUND, VAPOR_ERR_PERMISSION, VAPOR_OK};

fn cstr(p: *const c_char) -> Option<&'static str> {
    if p.is_null() {
        return None;
    }
    unsafe { CStr::from_ptr(p) }.to_str().ok()
}

/// 将相对路径解析到某个根目录，阻止 `..` 越界。
fn safe_join(root: &str, rel: &str) -> Option<PathBuf> {
    if rel.starts_with('/') || rel.contains("..") {
        return None;
    }
    let mut p = PathBuf::from(root);
    p.push(rel);
    Some(p)
}

/// 按优先级解析可读路径：逻辑包 → 私有目录 → 外部目录。
fn resolve_read(rel: &str) -> Option<PathBuf> {
    let s = state::state().lock().ok()?;
    for root in [&s.app_dir, &s.private_dir, &s.external_dir] {
        if root.is_empty() {
            continue;
        }
        if let Some(p) = safe_join(root, rel) {
            if p.is_file() {
                return Some(p);
            }
        }
    }
    None
}

fn resolve_write(rel: &str) -> Option<PathBuf> {
    let s = state::state().lock().ok()?;
    let root = if !s.private_dir.is_empty() {
        &s.private_dir
    } else {
        &s.external_dir
    };
    if root.is_empty() {
        return None;
    }
    safe_join(root, rel)
}

pub fn fs_read(path: *const c_char, out_buf: *mut *mut u8, out_len: *mut i32) -> i32 {
    if out_buf.is_null() || out_len.is_null() {
        return VAPOR_ERR_ARG;
    }
    let (rel, out_buf, out_len) = unsafe { (cstr(path), out_buf.as_mut(), out_len.as_mut()) };
    let (rel, out_buf, out_len) = match (rel, out_buf, out_len) {
        (Some(r), Some(b), Some(l)) => (r, b, l),
        _ => return VAPOR_ERR_ARG,
    };
    let full = match resolve_read(rel) {
        Some(p) => p,
        None => return VAPOR_ERR_NOT_FOUND,
    };
    let bytes = match fs::read(&full) {
        Ok(b) => b,
        Err(_) => return VAPOR_ERR_NOT_FOUND,
    };
    unsafe {
        // 用 malloc 分配，C/Python 侧通过 vapor_fs_free 释放。
        let ptr = libc::malloc(bytes.len().max(1)) as *mut u8;
        if ptr.is_null() {
            return vapor_abi::VAPOR_ERR;
        }
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), ptr, bytes.len());
        *out_buf = ptr;
        *out_len = bytes.len() as i32;
    }
    VAPOR_OK
}

pub fn fs_write(path: *const c_char, buf: *const u8, len: i32) -> i32 {
    if buf.is_null() {
        return VAPOR_ERR_ARG;
    }
    let (rel, buf) = unsafe { (cstr(path), buf.as_ref()) };
    let (rel, buf) = match (rel, buf) {
        (Some(r), Some(b)) if len > 0 => (r, b),
        _ => return VAPOR_ERR_ARG,
    };
    let full = match resolve_write(rel) {
        Some(p) => p,
        None => return VAPOR_ERR_PERMISSION,
    };
    if let Some(parent) = full.parent() {
        if fs::create_dir_all(parent).is_err() {
            return VAPOR_ERR_PERMISSION;
        }
    }
    let slice = unsafe { std::slice::from_raw_parts(buf, len as usize) };
    match fs::File::create(&full).and_then(|mut f| f.write_all(slice)) {
        Ok(_) => VAPOR_OK,
        Err(_) => VAPOR_ERR_PERMISSION,
    }
}

pub fn fs_exists(path: *const c_char) -> i32 {
    let rel = match cstr(path) {
        Some(r) => r,
        None => return 0,
    };
    if resolve_read(rel).is_some() {
        1
    } else {
        0
    }
}

/// 列出目录，输出 JSON 数组字符串：`[{"name":"x","dir":false,"size":12}, ...]`
pub fn fs_list(path: *const c_char, out_json: *mut c_char, cap: i32) -> i32 {
    if out_json.is_null() {
        return VAPOR_ERR_ARG;
    }
    let (rel, out) = unsafe { (cstr(path), out_json.as_mut()) };
    let (rel, out) = match (rel, out) {
        (Some(r), Some(o)) => (r, o),
        _ => return VAPOR_ERR_ARG,
    };
    let s = match state::state().lock() {
        Ok(g) => g,
        Err(_) => return vapor_abi::VAPOR_ERR_STATE,
    };
    let mut candidates: Vec<PathBuf> = Vec::new();
    for root in [&s.app_dir, &s.private_dir, &s.external_dir] {
        if root.is_empty() {
            continue;
        }
        if let Some(p) = safe_join(root, rel) {
            if p.is_dir() {
                candidates.push(p);
            }
        }
    }
    drop(s);

    let mut entries: Vec<serde_json::Value> = Vec::new();
    for dir in candidates {
        if let Ok(rd) = fs::read_dir(&dir) {
            for e in rd.flatten() {
                let name = e.file_name().to_string_lossy().into_owned();
                let md = match e.metadata() {
                    Ok(m) => m,
                    Err(_) => continue,
                };
                entries.push(serde_json::json!({
                    "name": name,
                    "dir": md.is_dir(),
                    "size": if md.is_dir() { 0u64 } else { md.len() },
                }));
            }
        }
    }
    entries.sort_by(|a, b| {
        a["name"]
            .as_str()
            .unwrap_or("")
            .cmp(b["name"].as_str().unwrap_or(""))
    });
    let json = serde_json::to_string(&entries).unwrap_or_else(|_| "[]".into());
    let bytes = CString::new(json).unwrap_or_else(|_| CString::new("[]").unwrap());
    let need = bytes.as_bytes_with_nul().len();
    if need > cap as usize {
        return VAPOR_ERR_ARG;
    }
    unsafe {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), out, need);
    }
    VAPOR_OK
}

pub fn fs_free(buf: *mut u8) {
    if !buf.is_null() {
        unsafe { libc::free(buf as *mut c_void) }
    }
}

/// 供 Rust 内部使用：把逻辑包整个 .zip 解压到 app_dir（由打包器/渲染器调用）。
pub fn unpack_logic_zip(zip_bytes: &[u8], target: &Path) -> Result<usize, String> {
    use std::io::Cursor;
    let mut archive = zip::ZipArchive::new(Cursor::new(zip_bytes))
        .map_err(|e| format!("zip 解析失败: {e}"))?;
    let mut count = 0usize;
    for i in 0..archive.len() {
        let mut file = archive.by_index(i).map_err(|e| e.to_string())?;
        let name = file.name().to_string();
        if name.contains("..") || name.starts_with('/') {
            continue; // 防目录穿越
        }
        let out = target.join(&name);
        if file.is_dir() {
            fs::create_dir_all(&out).map_err(|e| e.to_string())?;
        } else {
            if let Some(parent) = out.parent() {
                fs::create_dir_all(parent).map_err(|e| e.to_string())?;
            }
            let mut f = fs::File::create(&out).map_err(|e| e.to_string())?;
            std::io::copy(&mut file, &mut f).map_err(|e| e.to_string())?;
        }
        count += 1;
    }
    Ok(count)
}
