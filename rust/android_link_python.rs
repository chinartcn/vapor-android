// 仅 Android 目标生效：把 PyO3 扩展显式链接到目标架构的 libpython3.15.so。
//
// 背景（真机日志）：
//   ImportError: dlopen failed: cannot locate symbol "PyBaseObject_Type"
//   referenced by ".../site-packages/Vapor/_ui.so"
// Android 的 bionic 不会像桌面 glibc 那样把解释器符号导出到全局，而 PyO3 的
// extension-module 模式默认不链接 libpython，于是扩展里未定义的 Py* 符号在
// dlopen 时没有提供者。给扩展自身写入 DT_NEEDED(libpython3.15.so) 后，bionic
// 会在加载扩展时一并载入 libpython 并完成符号解析。
//
// 该文件由三个 crate 的 build.rs `include!` 使用。

pub fn android_link_python() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("android") {
        return;
    }
    println!("cargo:rerun-if-env-changed=CARGO_CFG_TARGET_ARCH");

    let manifest = match std::env::var("CARGO_MANIFEST_DIR") {
        Ok(m) => m,
        Err(_) => return,
    };
    let arch = std::env::var("CARGO_CFG_TARGET_ARCH").unwrap_or_default();
    let root = std::path::Path::new(&manifest).join("../..");

    // 优先使用本次构建产出的 libpython，其次退回复库里预置的正式产物。
    let candidates: Vec<std::path::PathBuf> = match arch.as_str() {
        "aarch64" => vec![
            root.join("build/cpython/arm64/lib"),
            root.join("build/cpython/arm64/prefix/lib"),
            root.join("lib/arm64-v8a"),
        ],
        "arm" => vec![
            root.join("build/cpython/arm32/lib"),
            root.join("build/cpython/arm32"),
            root.join("lib/armeabi-v7a"),
        ],
        _ => Vec::new(),
    };

    for dir in candidates {
        println!("cargo:rerun-if-changed={}", dir.display());
        if dir.join("libpython3.15.so").exists() {
            println!("cargo:rustc-link-search=native={}", dir.display());
            println!("cargo:rustc-link-lib=dylib=python3.15");
            return;
        }
    }
    println!(
        "cargo:warning=未找到 {} 的 libpython3.15.so，PyO3 扩展未链接 libpython（真机可能解析不到 Py* 符号）",
        arch
    );
}
