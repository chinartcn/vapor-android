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
    // 用普通函数而非闭包，避免 &mut env 被多个闭包同时捕获导致借用冲突。
    fn to_vec(env: &mut JNIEnv, arr: jbyteArray) -> Result<Vec<u8>, String> {
        if arr.is_null() {
            return Ok(Vec::new());
        }
        let b = unsafe { JByteArray::from_raw(arr) };
        env.convert_byte_array(b).map_err(|e| e.to_string())
    }
    fn to_str(env: &mut JNIEnv, j: jstring) -> Result<String, String> {
        if j.is_null() {
            return Ok(String::new());
        }
        let js = unsafe { JString::from_raw(j) };
        env.get_string(&js).map(|x| x.into()).map_err(|e| e.to_string())
    }

    let shell_bytes = to_vec(env, shell)?;
    let logic_bytes = to_vec(env, logic)?;
    let icon_bytes = if icon.is_null() { None } else { Some(to_vec(env, icon)?) };
    let opts = PackageOptions {
        app_name: to_str(env, app_name)?,
        version_name: to_str(env, version_name)?,
        version_code: version_code as u32,
        package_name: to_str(env, package_name)?,
        shell_package_name: to_str(env, shell_package)?,
        shell_app_name: to_str(env, shell_app_name)?,
        icon_png: icon_bytes,
        key_alias: "vapor".into(),
    };
    let result = package(&shell_bytes, &logic_bytes, &opts)?;
    Ok(result.apk_bytes)
}
