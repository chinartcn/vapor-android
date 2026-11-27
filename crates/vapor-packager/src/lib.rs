//! vapor-packager —— app2 纯打包器核心。
//!
//! 把「空壳渲染器 APK」（真·只有渲染器，无业务逻辑）与「装有逻辑的 .zip」
//! 合并为一个新 APK：
//!   1. 解包空壳 APK（它本身是 zip）；
//!   2. 把逻辑 zip 作为 assets/vapor_logic.zip 写入，并写配置 assets/vapor_meta.json；
//!   3. 用 axml 模块改写 AndroidManifest.xml（包名 / 版本名 / 应用名 / versionCode）；
//!   4. 替换应用图标（可选，替换 res 下 ic_launcher*.png）；
//!   5. 重新打包为 APK（v1 JAR 签名，纯 Rust 实现，不依赖 apksigner）。
//!
//! 输出到调用方给定路径；Android 层负责：优先 /storage/emulated/0/rustoutputapk，
//! 无权限则回退私有目录。

pub mod axml;
pub mod sign;
#[cfg(target_os = "android")]
pub mod jni_bridge;

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::io::{Cursor, Read, Write};
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PackageOptions {
    pub app_name: String,
    pub version_name: String,
    pub version_code: u32,
    pub package_name: String,
    /// 原空壳 APK 里占位包名（用于 manifest 替换）
    pub shell_package_name: String,
    /// 原应用名占位
    pub shell_app_name: String,
    /// 图标 PNG 字节（可选）
    pub icon_png: Option<Vec<u8>>,
    /// 签名别名
    pub key_alias: String,
}

impl Default for PackageOptions {
    fn default() -> Self {
        Self {
            app_name: "Vapor App".into(),
            version_name: "1.0.0".into(),
            version_code: 1,
            package_name: "com.vapor.app".into(),
            shell_package_name: "com.vapor.shell".into(),
            shell_app_name: "Vapor Shell".into(),
            icon_png: None,
            key_alias: "vapor".into(),
        }
    }
}

pub struct PackageResult {
    pub apk_bytes: Vec<u8>,
    pub entries: usize,
    pub logic_files: usize,
}

/// 核心打包：空壳 APK bytes + 逻辑 zip bytes → 新 APK bytes（已签名）。
pub fn package(shell_apk: &[u8], logic_zip: &[u8], opts: &PackageOptions) -> Result<PackageResult, String> {
    // 1. 读空壳 APK 所有条目
    let mut shell = zip::ZipArchive::new(Cursor::new(shell_apk))
        .map_err(|e| format!("空壳 APK 不是合法 zip: {e}"))?;
    let mut entries: Vec<(String, Vec<u8>)> = Vec::with_capacity(shell.len());
    for i in 0..shell.len() {
        let mut f = shell.by_index(i).map_err(|e| e.to_string())?;
        let name = f.name().to_string();
        // 丢弃旧签名（重新签名时必须）
        if name.starts_with("META-INF/") && (name.ends_with(".RSA") || name.ends_with(".SF") || name.ends_with("MANIFEST.MF")) {
            continue;
        }
        let mut data = Vec::new();
        f.read_to_end(&mut data).map_err(|e| e.to_string())?;
        entries.push((name, data));
    }

    // 2. 统计逻辑 zip 文件数
    let logic_count = {
        let z = zip::ZipArchive::new(Cursor::new(logic_zip))
            .map_err(|e| format!("逻辑包不是合法 zip: {e}"))?;
        let n = z.len();
        drop(z);
        n
    };

    // 3. 改写 AndroidManifest.xml
    let mut replacements = HashMap::new();
    replacements.insert(opts.shell_package_name.clone(), opts.package_name.clone());
    replacements.insert(opts.shell_app_name.clone(), opts.app_name.clone());
    replacements.insert(opts.version_name.clone(), opts.version_name.clone()); // 版本名占位同值场景
    let mut manifest_done = false;
    for (name, data) in entries.iter_mut() {
        if name == "AndroidManifest.xml" {
            *data = axml::patch_manifest(data, &replacements, Some(opts.version_code))
                .map_err(|e| format!("改写 AndroidManifest 失败: {e}"))?;
            manifest_done = true;
        }
        // 4. 替换图标：覆盖所有 launcher 图标 png
        if let Some(icon) = &opts.icon_png {
            let lower = name.to_ascii_lowercase();
            if lower.contains("ic_launcher") && lower.ends_with(".png") {
                *data = icon.clone();
            }
        }
    }
    if !manifest_done {
        return Err("空壳 APK 缺少 AndroidManifest.xml".into());
    }

    // 5. 注入逻辑包与元数据
    entries.push(("assets/vapor_logic.zip".into(), logic_zip.to_vec()));
    let meta = serde_json::json!({
        "app_name": opts.app_name,
        "version_name": opts.version_name,
        "version_code": opts.version_code,
        "package_name": opts.package_name,
        "logic_entry": "assets/vapor_logic.zip",
        "schema": 1,
    });
    entries.push(("assets/vapor_meta.json".into(), meta.to_string().into_bytes()));

    // 6. 重新打包为 APK（stored + deflate）
    let mut out = Vec::new();
    {
        let mut zw = zip::ZipWriter::new(Cursor::new(&mut out));
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated)
            .unix_permissions(0o644);
        for (name, data) in &entries {
            zw.start_file(name, options).map_err(|e| e.to_string())?;
            zw.write_all(data).map_err(|e| e.to_string())?;
        }
        zw.finish().map_err(|e| e.to_string())?;
    }

    // 7. v1 JAR 签名
    let signed = sign::sign_apk_v1(&out, &opts.key_alias)
        .map_err(|e| format!("签名失败: {e}"))?;

    Ok(PackageResult {
        apk_bytes: signed,
        entries: entries.len(),
        logic_files: logic_count,
    })
}

/// 从逻辑目录（含 main.py/main.json/abi.json/lib/）打成 zip。
pub fn zip_logic_dir(dir: &Path) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    {
        let mut zw = zip::ZipWriter::new(Cursor::new(&mut out));
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        fn walk(zw: &mut zip::ZipWriter<Cursor<&mut Vec<u8>>>, base: &Path, dir: &Path, opt: zip::write::SimpleFileOptions) -> Result<(), String> {
            let mut items: Vec<_> = std::fs::read_dir(dir)
                .map_err(|e| e.to_string())?
                .filter_map(|e| e.ok())
                .collect();
            items.sort_by_key(|e| e.file_name());
            for e in items {
                let p = e.path();
                let rel = p.strip_prefix(base).map_err(|e| e.to_string())?;
                let rel_s = rel.to_string_lossy().replace('\\', "/");
                if p.is_dir() {
                    zw.add_directory(format!("{rel_s}/"), opt).map_err(|e| e.to_string())?;
                    walk(zw, base, &p, opt)?;
                } else {
                    zw.start_file(&rel_s, opt).map_err(|e| e.to_string())?;
                    let mut f = std::fs::File::open(&p).map_err(|e| e.to_string())?;
                    std::io::copy(&mut f, zw).map_err(|e| e.to_string())?;
                }
            }
            Ok(())
        }
        walk(&mut zw, dir, dir, options)?;
        zw.finish().map_err(|e| e.to_string())?;
    }
    Ok(out)
}

/// CLI / JNI 共用：把打包结果写到目标路径。
pub fn write_result(result: &PackageResult, path: &Path) -> Result<(), String> {
    let mut f = std::fs::File::create(path).map_err(|e| e.to_string())?;
    f.write_all(&result.apk_bytes).map_err(|e| e.to_string())?;
    Ok(())
}
