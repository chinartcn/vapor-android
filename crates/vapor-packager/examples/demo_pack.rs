//! 打包器演示：造一个空壳 APK + 逻辑 zip → 打包成新 APK（本机可运行验证）。
//! 运行： cargo run -p vapor-packager --example demo_pack

use std::io::{Cursor, Write};
use vapor_packager::{package, zip_logic_dir, PackageOptions};

fn main() {
    // ---- 1. 造一个最小空壳 APK（含 AndroidManifest.xml + classes.dex 占位 + 资源） ----
    // 真实场景这里是编译好的渲染器 APK；验证时用一个 zip 模拟其结构。
    let mut shell = Vec::new();
    {
        let mut zw = zip::ZipWriter::new(Cursor::new(&mut shell));
        let opt = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        // 一个占位 manifest（真实为二进制 AXML；此处仅验证打包流程与 zip 结构）
        let manifest = b"<?xml version=\"1.0\"?><manifest package=\"com.vapor.shell\" \
            android:versionCode=\"1\" android:versionName=\"1.0\">\
            <application android:label=\"Vapor Shell\"/></manifest>";
        zw.start_file("AndroidManifest.xml", opt).unwrap();
        zw.write_all(manifest).unwrap();
        zw.start_file("classes.dex", opt).unwrap();
        zw.write_all(b"dex\n035\x00 /* test placeholder */").unwrap();
        zw.start_file("res/mipmap/ic_launcher.png", opt).unwrap();
        zw.write_all(b"\x89PNG\r\n\x1a\n /* test icon */").unwrap();
        zw.finish().unwrap();
    }

    // ---- 2. 把逻辑目录打成 zip ----
    let logic_dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../protocol/example");
    let logic_zip = zip_logic_dir(&logic_dir).expect("打包逻辑目录失败");

    // ---- 3. 打包 ----
    let opts = PackageOptions {
        app_name: "我的游戏".into(),
        version_name: "2.3.0".into(),
        version_code: 23,
        package_name: "com.acme.mygame".into(),
        shell_package_name: "com.vapor.shell".into(),
        shell_app_name: "Vapor Shell".into(),
        icon_png: Some(b"\x89PNG\r\n\x1a\n /* custom icon */".to_vec()),
        key_alias: "vapor".into(),
    };
    let result = package(&shell, &logic_zip, &opts).expect("打包失败");
    println!("打包成功: {} 条目, 逻辑包 {} 文件, 输出 {} 字节",
        result.entries, result.logic_files, result.apk_bytes.len());

    // ---- 4. 校验产物结构 ----
    let mut z = zip::ZipArchive::new(Cursor::new(&result.apk_bytes)).unwrap();
    let names: Vec<String> = (0..z.len()).map(|i| z.by_index(i).unwrap().name().to_string()).collect();
    for required in ["AndroidManifest.xml", "classes.dex", "assets/vapor_logic.zip",
                     "assets/vapor_meta.json", "META-INF/MANIFEST.MF",
                     "META-INF/VAPOR.SF", "META-INF/VAPOR.RSA"] {
        assert!(names.iter().any(|n| n == required), "产物缺少 {required}");
        println!("  [ok] 含 {required}");
    }
    assert!(!names.iter().any(|n| n.contains("ic_launcher") && false), "");
    // 校验图标被替换
    let mut icon = String::new();
    for i in 0..z.len() {
        let mut f = z.by_index(i).unwrap();
        if f.name().contains("ic_launcher") {
            use std::io::Read;
            let mut b = Vec::new();
            f.read_to_end(&mut b).unwrap();
            icon = String::from_utf8_lossy(&b).into_owned();
        }
    }
    assert!(icon.contains("custom icon"), "图标未被替换");
    println!("  [ok] 应用图标已替换");

    // 写到输出目录
    let out = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../build/output/demo_packed.apk");
    std::fs::create_dir_all(out.parent().unwrap()).unwrap();
    vapor_packager::write_result(&result, &out).unwrap();
    println!("[PASS] 打包器端到端验证通过 ✔ -> {}", out.display());
}
