//! JNI 桥接：Java PackagerActivity.nativePackage(...) → Rust 打包核心。
//! 仅在 Android 目标编译。读取 Java 传入的 byte[]/String，调用 package()，
//! 把结果 APK 作为 jbyteArray 返回；失败抛 RuntimeException。

use crate::{package, PackageOptions};
use jni::objects::{JByteArray, JString};
use jni::sys::{jbyteArray, jint, jobject, jstring};
use jni::JNIEnv;

#[no_mangle]
#[cfg(target_os = "android")]
pub extern "system" fn Java_com_vapor_PackagerActivity_nativePackage(
    mut env: JNIEnv,
    _thiz: jobject,
    shell: jbyteArray,
    logic: jbyteArray,
    icon: jbyteArray,
    app_name: jstring,
    version_name: jstring,
    version_code: jint,
    package_name: jstring,
    shell_package: jstring,
    shell_app_name: jstring,
) -> jbyteArray {
    match inner(&mut env, shell, logic, icon, app_name, version_name,
                version_code, package_name, shell_package, shell_app_name) {
        Ok(bytes) => env
            .byte_array_from_slice(&bytes)
            .map(|a| a.into_raw())
            .unwrap_or(std::ptr::null_mut()),
        Err(e) => {
            let _ = env.throw_new("java/lang/RuntimeException", e);
            std::ptr::null_mut()
        }
    }
}

#[cfg(target_os = "android")]
fn inner(
    env: &mut JNIEnv,
    shell: jbyteArray,
    logic: jbyteArray,
    icon: jbyteArray,
    app_name: jstring,
    version_name: jstring,
    version_code: jint,
    package_name: jstring,
    shell_package: jstring,
    shell_app_name: jstring,
) -> Result<Vec<u8>, String> {
    let to_vec = |arr: jbyteArray| -> Result<Vec<u8>, String> {
        let b = JByteArray::from_raw(arr);
        env.convert_byte_array(b).map_err(|e| e.to_string())
    };
    let to_str = |s: jstring| -> Result<String, String> {
        let js = JString::from_raw(s);
        env.get_string(&js).map(|x| x.into()).map_err(|e| e.to_string())
    };

    let shell_bytes = to_vec(shell)?;
    let logic_bytes = to_vec(logic)?;
    let icon_bytes = if icon.is_null() { None } else { Some(to_vec(icon)?) };
    let opts = PackageOptions {
        app_name: to_str(app_name)?,
        version_name: to_str(version_name)?,
        version_code: version_code as u32,
        package_name: to_str(package_name)?,
        shell_package_name: to_str(shell_package)?,
        shell_app_name: to_str(shell_app_name)?,
        icon_png: icon_bytes,
        key_alias: "vapor".into(),
    };
    let result = package(&shell_bytes, &logic_bytes, &opts)?;
    Ok(result.apk_bytes)
}
