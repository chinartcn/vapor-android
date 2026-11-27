//! JNI 桥接层 —— 让 C / Python 扩展通过稳定 C ABI 调用**任意** JNI。
//!
//! 设计要点：
//!   1. **不链接 libjvm.so**：JavaVM*/JNIEnv* 都是「指向函数表的指针」，本模块
//!      直接从函数表读取函数指针并调用。因此同一份 libvapor.so 在 Android 的 ART
//!      与本机 OpenJDK JVM 上都能工作，arm32/arm64 由 jni_table.rs 自动适配偏移。
//!   2. **句柄化**：jobject/jclass/jarray/jmethodID 跨调用以 int64 句柄持有，
//!      内部保存全局引用（NewGlobalRef），调用方用 vapor_jni_release 释放。
//!   3. **JSON 协议**：vapor_jni_call(req_json, out_json, cap) 一个函数覆盖全部
//!      JNI 操作（find_class / get_method / call / field / array / string / exception）。
//!      C 扩展用 snprintf 拼 JSON，Python 用 json.dumps，两边共用同一套语义。
//!   4. **线程安全**：JavaVM* 全局注入一次；每次调用自动 GetEnv 或 AttachCurrentThread。

use crate::jni_table::{JNIInvokeTable, JNINativeTable};
use crate::log;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::ffi::{c_char, c_void, CStr, CString};
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Mutex, OnceLock};
use vapor_abi::{VAPOR_ERR, VAPOR_ERR_ARG, VAPOR_OK};

const JNI_OK: i32 = 0;
const JNI_VERSION_1_6: i32 = 0x0001_0006;

// ---- JavaVM 全局注入 ----
static VM: OnceLock<Mutex<usize>> = OnceLock::new();
fn vm_cell() -> &'static Mutex<usize> {
    VM.get_or_init(|| Mutex::new(0))
}

// ---- 句柄表：i64 -> (种类, 指针) ----
#[derive(Clone, Copy)]
enum HandleKind {
    Obj,          // jobject/jclass/jstring —— 全局引用，需 DeleteGlobalRef
    Method,       // jmethodID/jfieldID —— 不需要释放引用
    Array(char),  // jarray，附元素类型
}
#[derive(Clone, Copy)]
struct Handle {
    kind: HandleKind,
    ptr: usize,
    class: usize, // Method 句柄携带所属 class，便于 call 直接复用
}
static HANDLES: OnceLock<Mutex<HashMap<i64, Handle>>> = OnceLock::new();
fn handles() -> &'static Mutex<HashMap<i64, Handle>> {
    HANDLES.get_or_init(|| Mutex::new(HashMap::new()))
}
static NEXT_HANDLE: AtomicI64 = AtomicI64::new(1);

fn new_handle(kind: HandleKind, ptr: *mut c_void) -> i64 {
    new_handle_with_class(kind, ptr, std::ptr::null_mut())
}

fn new_handle_with_class(kind: HandleKind, ptr: *mut c_void, class: *mut c_void) -> i64 {
    let id = NEXT_HANDLE.fetch_add(1, Ordering::Relaxed);
    handles().lock().unwrap().insert(
        id,
        Handle {
            kind,
            ptr: ptr as usize,
            class: class as usize,
        },
    );
    id
}

fn get_handle(id: i64) -> Option<Handle> {
    handles().lock().unwrap().get(&id).copied()
}

// ---- jvalue 联合体（与 jni.h 的 union jvalue 布局一致） ----
#[repr(C)]
#[derive(Clone, Copy)]
union JValue {
    z: u8,
    b: i8,
    c: u16,
    s: i16,
    i: i32,
    j: i64,
    f: f32,
    d: f64,
    l: *mut c_void,
}
// ---- 函数指针类型 ----
type AttachFn = unsafe extern "C" fn(*mut c_void, *mut *mut c_void, *mut c_void) -> i32;
type DetachFn = unsafe extern "C" fn(*mut c_void) -> i32;
type GetEnvFn = unsafe extern "C" fn(*mut c_void, *mut *mut c_void, i32) -> i32;
type FindClassFn = unsafe extern "C" fn(*mut c_void, *const c_char) -> *mut c_void;
type GetMethodFn = unsafe extern "C" fn(*mut c_void, *mut c_void, *const c_char, *const c_char) -> *mut c_void;
type CallObjFn = unsafe extern "C" fn(*mut c_void, *mut c_void, *mut c_void, *const JValue) -> *mut c_void;
type CallPrimFn = unsafe extern "C" fn(*mut c_void, *mut c_void, *mut c_void, *const JValue) -> i64;
type CallFloatFn = unsafe extern "C" fn(*mut c_void, *mut c_void, *mut c_void, *const JValue) -> f32;
type CallDoubleFn = unsafe extern "C" fn(*mut c_void, *mut c_void, *mut c_void, *const JValue) -> f64;
type CallVoidFn = unsafe extern "C" fn(*mut c_void, *mut c_void, *mut c_void, *const JValue);
type NewObjFn = unsafe extern "C" fn(*mut c_void, *mut c_void, *mut c_void, *const JValue) -> *mut c_void;
type NewGlobalRefFn = unsafe extern "C" fn(*mut c_void, *mut c_void) -> *mut c_void;
type DeleteGlobalRefFn = unsafe extern "C" fn(*mut c_void, *mut c_void);
type DeleteLocalRefFn = unsafe extern "C" fn(*mut c_void, *mut c_void);
type NewStringUtfFn = unsafe extern "C" fn(*mut c_void, *const c_char) -> *mut c_void;
type GetStringUtfCharsFn = unsafe extern "C" fn(*mut c_void, *mut c_void, *mut u8) -> *const c_char;
type ReleaseStringUtfCharsFn = unsafe extern "C" fn(*mut c_void, *mut c_void, *const c_char);
type GetArrayLenFn = unsafe extern "C" fn(*mut c_void, *mut c_void) -> i32;
type NewArrayFn = unsafe extern "C" fn(*mut c_void, i32) -> *mut c_void;
type NewObjArrayFn = unsafe extern "C" fn(*mut c_void, i32, *mut c_void, *mut c_void) -> *mut c_void;
type SetArrayRegionFn = unsafe extern "C" fn(*mut c_void, *mut c_void, i32, i32, *const c_void);
type GetArrayRegionFn = unsafe extern "C" fn(*mut c_void, *mut c_void, i32, i32, *mut c_void);
type ExceptionCheckFn = unsafe extern "C" fn(*mut c_void) -> u8;
type ExceptionClearFn = unsafe extern "C" fn(*mut c_void);
type GetObjectClassFn = unsafe extern "C" fn(*mut c_void, *mut c_void) -> *mut c_void;
type IsSameObjFn = unsafe extern "C" fn(*mut c_void, *mut c_void, *mut c_void) -> u8;

unsafe fn cast<T>(slot: usize) -> T {
    std::mem::transmute_copy(&slot)
}

// ---- 取函数表 ----
unsafe fn vm_table(vm: *mut c_void) -> &'static JNIInvokeTable {
    &*(*(vm as *const *const JNIInvokeTable))
}
unsafe fn env_table(env: *mut c_void) -> &'static JNINativeTable {
    &*(*(env as *const *const JNINativeTable))
}

// ---------------------------------------------------------------------------
// 公共入口：VM 注入 / 取 Env
// ---------------------------------------------------------------------------

pub fn set_vm(vm: *mut c_void) -> i32 {
    if vm.is_null() {
        return VAPOR_ERR_ARG;
    }
    *vm_cell().lock().unwrap() = vm as usize;
    VAPOR_OK
}

pub fn get_vm() -> *mut c_void {
    *vm_cell().lock().unwrap() as *mut c_void
}

/// 取当前线程 JNIEnv*：已 attach 则 GetEnv，否则 AttachCurrentThread。
pub fn get_env() -> *mut c_void {
    let vm = get_vm();
    if vm.is_null() {
        return std::ptr::null_mut();
    }
    unsafe {
        let tbl = vm_table(vm);
        let getenv: GetEnvFn = cast(tbl.GetEnv);
        let attach: AttachFn = cast(tbl.AttachCurrentThread);
        let mut env: *mut c_void = std::ptr::null_mut();
        if getenv(vm, &mut env, JNI_VERSION_1_6) == JNI_OK && !env.is_null() {
            return env;
        }
        if attach(vm, &mut env, std::ptr::null_mut()) == JNI_OK {
            return env;
        }
    }
    std::ptr::null_mut()
}

pub fn detach() -> i32 {
    let vm = get_vm();
    if vm.is_null() {
        return VAPOR_ERR;
    }
    unsafe {
        let detach_fn: DetachFn = cast(vm_table(vm).DetachCurrentThread);
        if detach_fn(vm) == JNI_OK { VAPOR_OK } else { VAPOR_ERR }
    }
}

/// 供 NativeActivity 的 ANativeActivity->vm 自动注入。
#[cfg(target_os = "android")]
pub fn auto_set_vm_from_activity(vm: *mut c_void) {
    set_vm(vm);
}

// ---------------------------------------------------------------------------
// 内部工具
// ---------------------------------------------------------------------------

fn s(v: &Value, key: &str) -> Option<String> {
    v.get(key).and_then(|x| x.as_str()).map(|x| x.to_string())
}
fn b(v: &Value, key: &str) -> bool {
    v.get(key).and_then(|x| x.as_bool()).unwrap_or(false)
}

/// 把参数 JSON 数组转成 jvalue 数组；literal 字符串会创建临时 jstring 记录在 temps。
unsafe fn build_args(
    env: *mut c_void,
    args: Option<&Value>,
    temps: &mut Vec<*mut c_void>,
) -> Result<Vec<JValue>, String> {
    let mut out = Vec::new();
    if let Some(arr) = args.and_then(|a| a.as_array()) {
        for a in arr {
            let t = s(a, "t").unwrap_or_else(|| "i".into());
            let val = a.get("v");
            let jv = match t.as_str() {
                "z" => JValue { z: val.and_then(Value::as_bool).unwrap_or(false) as u8 },
                "b" => JValue { b: val.and_then(Value::as_i64).unwrap_or(0) as i8 },
                "c" => JValue { c: val.and_then(Value::as_i64).unwrap_or(0) as u16 },
                "s" => JValue { s: val.and_then(Value::as_i64).unwrap_or(0) as i16 },
                "i" => JValue { i: val.and_then(Value::as_i64).unwrap_or(0) as i32 },
                "j" => JValue { j: val.and_then(Value::as_i64).unwrap_or(0) },
                "f" => JValue { f: val.and_then(Value::as_f64).unwrap_or(0.0) as f32 },
                "d" => JValue { d: val.and_then(Value::as_f64).unwrap_or(0.0) },
                "l" => {
                    let text = val.and_then(Value::as_str).unwrap_or("");
                    let cs = CString::new(text).map_err(|_| "字符串含 NUL".to_string())?;
                    let tbl = env_table(env);
                    let new_s: NewStringUtfFn = cast(tbl.NewStringUTF);
                    let js = new_s(env, cs.as_ptr());
                    if js.is_null() {
                        return Err("NewStringUTF 失败".into());
                    }
                    temps.push(js);
                    JValue { l: js }
                }
                "o" | "a" => {
                    let id = val.and_then(Value::as_i64).unwrap_or(-1);
                    let h = get_handle(id).ok_or_else(|| format!("无效句柄 {}", id))?;
                    JValue { l: h.ptr as *mut c_void }
                }
                _ => return Err(format!("未知参数类型 {}", t)),
            };
            out.push(jv);
        }
    }
    Ok(out)
}

fn cleanup_temps(env: *mut c_void, temps: &[ *mut c_void ]) {
    if env.is_null() || temps.is_empty() {
        return;
    }
    unsafe {
        let del: DeleteLocalRefFn = cast(env_table(env).DeleteLocalRef);
        for &t in temps {
            del(env, t);
        }
    }
}

/// 从签名 "(I)Ljava/lang/String;" 解析返回类型字符。
/// JNI 描述符用大写（I/J/F/D/Z/B/C/S），统一转成内部小写类型码。
fn ret_from_sig(sig: &str) -> char {
    let c = sig
        .rfind(')')
        .and_then(|p| sig[p + 1..].chars().next())
        .unwrap_or('V');
    normalize_type(c)
}

/// JNI 类型描述符 → 内部类型码（大写转小写；L/[/对象 → o）。
fn normalize_type(c: char) -> char {
    match c {
        'Z' => 'z', 'B' => 'b', 'C' => 'c', 'S' => 's', 'I' => 'i',
        'J' => 'j', 'F' => 'f', 'D' => 'd', 'V' => 'v',
        'L' | '[' => 'o',
        other => other.to_ascii_lowercase(),
    }
}

fn ret_type(req: &Value, sig: &str) -> char {
    s(req, "ret")
        .and_then(|r| r.chars().next())
        .map(normalize_type)
        .unwrap_or_else(|| ret_from_sig(sig))
}

/// 把 JNI 调用结果包装为 JSON；对象类型创建全局引用并返回句柄。
unsafe fn wrap_result(env: *mut c_void, t: char, raw: i64, obj: *mut c_void) -> Value {
    match t {
        'v' => json!({"t":"v"}),
        'z' | 'b' | 'c' | 's' | 'i' => json!({"t": t.to_string(), "v": raw as i32}),
        'j' => json!({"t":"j","v":raw}),
        'f' => json!({"t":"f","v":(raw as i32 as f32)}),
        'd' => json!({"t":"d","v":f64::from_bits(raw as u64)}),
        'l' => {
            // jstring：读取 UTF-8 字符串直接返回
            if obj.is_null() {
                json!({"t":"l","v":null})
            } else {
                let tbl = env_table(env);
                let get_chars: GetStringUtfCharsFn = cast(tbl.GetStringUTFChars);
                let rel: ReleaseStringUtfCharsFn = cast(tbl.ReleaseStringUTFChars);
                let chars = get_chars(env, obj, std::ptr::null_mut());
                let text = if chars.is_null() {
                    String::new()
                } else {
                    CStr::from_ptr(chars).to_string_lossy().into_owned()
                };
                if !chars.is_null() {
                    rel(env, obj, chars);
                }
                json!({"t":"l","v":text})
            }
        }
        'o' | 'a' => {
            if obj.is_null() {
                json!({"t": t.to_string(), "handle": 0})
            } else {
                let new_g: NewGlobalRefFn = cast(env_table(env).NewGlobalRef);
                let g = new_g(env, obj);
                let h = new_handle(
                    if t == 'a' { HandleKind::Array('i') } else { HandleKind::Obj },
                    g,
                );
                json!({"t": t.to_string(), "handle": h})
            }
        }
        _ => json!({"t":"v"}),
    }
}

/// 调用后统一检查异常；有异常则清除并返回错误。
unsafe fn check_exception(env: *mut c_void) -> Option<String> {
    let tbl = env_table(env);
    let check: ExceptionCheckFn = cast(tbl.ExceptionCheck);
    if check(env) != 0 {
        let clear: ExceptionClearFn = cast(tbl.ExceptionClear);
        clear(env);
        Some("JNI 调用抛出异常（已清除）".into())
    } else {
        None
    }
}

fn find_class(env: *mut c_void, name: &str) -> Result<*mut c_void, String> {
    let cs = CString::new(name).map_err(|_| "类名含 NUL".to_string())?;
    unsafe {
        let f: FindClassFn = cast(env_table(env).FindClass);
        let c = f(env, cs.as_ptr());
        // 必须先查/清异常（短路求值会漏掉），再判空
        let exc = check_exception(env);
        if exc.is_some() || c.is_null() {
            Err(exc.unwrap_or_else(|| format!("找不到类 {}", name)))
        } else {
            Ok(c)
        }
    }
}

fn get_method(env: *mut c_void, class: *mut c_void, name: &str, sig: &str, static_: bool) -> Result<*mut c_void, String> {
    let n = CString::new(name).map_err(|_| "方法名含 NUL".to_string())?;
    let sg = CString::new(sig).map_err(|_| "签名含 NUL".to_string())?;
    unsafe {
        let tbl = env_table(env);
        let f: GetMethodFn = cast(if static_ { tbl.GetStaticMethodID } else { tbl.GetMethodID });
        let m = f(env, class, n.as_ptr(), sg.as_ptr());
        let exc = check_exception(env);
        if exc.is_some() || m.is_null() {
            Err(exc.unwrap_or_else(|| format!("找不到{}方法 {} {}", if static_ { "静态" } else { "" }, name, sig)))
        } else {
            Ok(m)
        }
    }
}

// ---------------------------------------------------------------------------
// 通用调用分发
// ---------------------------------------------------------------------------

pub fn call(req_json: *const c_char, out_json: *mut c_char, cap: i32) -> i32 {
    if req_json.is_null() || out_json.is_null() || cap <= 0 {
        return VAPOR_ERR_ARG;
    }
    let req: Value = match unsafe { CStr::from_ptr(req_json) }.to_str() {
        Ok(s) => match serde_json::from_str(s) {
            Ok(v) => v,
            Err(e) => return write_err(out_json, cap, &format!("JSON 解析失败: {}", e)),
        },
        Err(_) => return write_err(out_json, cap, "请求非 UTF-8"),
    };

    let env = get_env();
    if env.is_null() {
        return write_err(out_json, cap, "未注入 JavaVM*（vapor_jni_set_vm）或 attach 失败");
    }

    let op = s(&req, "op").unwrap_or_default();
    let result: Result<Value, String> = unsafe { dispatch(env, &op, &req) };

    let out = match result {
        Ok(mut v) => {
            if !v.get("ok").is_some() {
                v["ok"] = json!(true);
            }
            v
        }
        Err(e) => json!({"ok": false, "error": e}),
    };
    write_value(out_json, cap, &out)
}

unsafe fn dispatch(env: *mut c_void, op: &str, req: &Value) -> Result<Value, String> {
    match op {
        "find_class" => {
            let name = s(req, "class").ok_or("缺少 class")?;
            let c = find_class(env, &name)?;
            let new_g: NewGlobalRefFn = cast(env_table(env).NewGlobalRef);
            let g = new_g(env, c);
            let del: DeleteLocalRefFn = cast(env_table(env).DeleteLocalRef);
            del(env, c);
            let h = new_handle(HandleKind::Obj, g);
            Ok(json!({"handle": h}))
        }
        "get_method" | "get_static_method" => {
            let id = req.get("target").and_then(Value::as_i64).ok_or("缺少 target(class 句柄)")?;
            let h = get_handle(id).ok_or("无效 class 句柄")?;
            let name = s(req, "name").ok_or("缺少 name")?;
            let sig = s(req, "sig").ok_or("缺少 sig")?;
            let class = h.ptr as *mut c_void;
            let m = get_method(env, class, &name, &sig, op == "get_static_method")?;
            let mh = new_handle_with_class(HandleKind::Method, m, class);
            Ok(json!({"handle": mh}))
        }
        "call" | "call_static" => {
            let static_ = op == "call_static" || b(req, "static");
            let sig = s(req, "sig").unwrap_or_default();
            let rt = ret_type(req, &sig);

            // 定位 class：优先用 method 句柄携带的 class，否则按 class 名 find
            let class = if let Some(mh) = req.get("method").and_then(Value::as_i64) {
                let h = get_handle(mh).ok_or("无效 method 句柄")?;
                if h.class != 0 {
                    h.class as *mut c_void
                } else {
                    resolve_class(env, req)?
                }
            } else {
                resolve_class(env, req)?
            };

            // 方法：优先用 method 句柄，否则 get_method_id
            let method = if let Some(mh) = req.get("method").and_then(Value::as_i64) {
                let h = get_handle(mh).ok_or("无效 method 句柄")?;
                h.ptr as *mut c_void
            } else {
                let name = s(req, "name").ok_or("缺少 name")?;
                if sig.is_empty() {
                    return Err("缺少 sig".into());
                }
                get_method(env, class, &name, &sig, static_)?
            };

            // 接收者：静态方法用 class；实例方法用 target 句柄
            let receiver = if static_ {
                class
            } else {
                let id = req.get("target").and_then(Value::as_i64).ok_or("缺少 target(对象句柄)")?;
                let h = get_handle(id).ok_or("无效 target 句柄")?;
                h.ptr as *mut c_void
            };

            let mut temps = Vec::new();
            let args = build_args(env, req.get("args"), &mut temps)?;
            let ap = args.as_ptr();

            let tbl = env_table(env);
            let raw: i64;
            let mut obj: *mut c_void = std::ptr::null_mut();
            if rt == 'v' {
                let f: CallVoidFn = cast(if static_ { tbl.CallStaticVoidMethodA } else { tbl.CallVoidMethodA });
                f(env, receiver, method, ap);
                raw = 0;
            } else if rt == 'l' || rt == 'o' || rt == 'a' {
                let f: CallObjFn = cast(if static_ { tbl.CallStaticObjectMethodA } else { tbl.CallObjectMethodA });
                obj = f(env, receiver, method, ap);
                raw = 0;
            } else if rt == 'f' {
                let f: CallFloatFn = cast(if static_ { tbl.CallStaticFloatMethodA } else { tbl.CallFloatMethodA });
                raw = f(env, receiver, method, ap) as i64;
            } else if rt == 'd' {
                let f: CallDoubleFn = cast(if static_ { tbl.CallStaticDoubleMethodA } else { tbl.CallDoubleMethodA });
                raw = f(env, receiver, method, ap).to_bits() as i64;
            } else {
                // z/b/c/s/i/j 统一按 i64 读取（小类型高位无意义，wrap 时截断）
                let f: CallPrimFn = cast(match (static_, rt) {
                    (true, 'z') => tbl.CallStaticBooleanMethodA,
                    (true, 'b') => tbl.CallStaticByteMethodA,
                    (true, 'c') => tbl.CallStaticCharMethodA,
                    (true, 's') => tbl.CallStaticShortMethodA,
                    (true, 'i') => tbl.CallStaticIntMethodA,
                    (true, _) => tbl.CallStaticLongMethodA,
                    (false, 'z') => tbl.CallBooleanMethodA,
                    (false, 'b') => tbl.CallByteMethodA,
                    (false, 'c') => tbl.CallCharMethodA,
                    (false, 's') => tbl.CallShortMethodA,
                    (false, 'i') => tbl.CallIntMethodA,
                    (false, _) => tbl.CallLongMethodA,
                });
                raw = f(env, receiver, method, ap);
            }
            cleanup_temps(env, &temps);
            if let Some(e) = check_exception(env) {
                return Err(e);
            }
            Ok(wrap_result(env, rt, raw, obj))
        }
        "new_object" => {
            let name = s(req, "class").ok_or("缺少 class")?;
            let sig = s(req, "sig").unwrap_or_else(|| "()V".into());
            let class = find_class(env, &name)?;
            let init = get_method(env, class, "<init>", &sig, false)?;
            let mut temps = Vec::new();
            let args = build_args(env, req.get("args"), &mut temps)?;
            let f: NewObjFn = cast(env_table(env).NewObjectA);
            let obj = f(env, class, init, args.as_ptr());
            cleanup_temps(env, &temps);
            let del: DeleteLocalRefFn = cast(env_table(env).DeleteLocalRef);
            del(env, class);
            let exc = check_exception(env);
            if exc.is_some() || obj.is_null() {
                return Err(exc.unwrap_or_else(|| "new_object 失败".into()));
            }
            let new_g: NewGlobalRefFn = cast(env_table(env).NewGlobalRef);
            let g = new_g(env, obj);
            del(env, obj);
            let h = new_handle(HandleKind::Obj, g);
            Ok(json!({"handle": h}))
        }
        "new_string" => {
            let text = s(req, "value").ok_or("缺少 value")?;
            let cs = CString::new(text).map_err(|_| "字符串含 NUL".to_string())?;
            let f: NewStringUtfFn = cast(env_table(env).NewStringUTF);
            let js = f(env, cs.as_ptr());
            if js.is_null() {
                return Err("NewStringUTF 失败".into());
            }
            let new_g: NewGlobalRefFn = cast(env_table(env).NewGlobalRef);
            let g = new_g(env, js);
            let del: DeleteLocalRefFn = cast(env_table(env).DeleteLocalRef);
            del(env, js);
            let h = new_handle(HandleKind::Obj, g);
            Ok(json!({"handle": h}))
        }
        "get_string" => {
            let id = req.get("target").and_then(Value::as_i64).ok_or("缺少 target")?;
            let h = get_handle(id).ok_or("无效句柄")?;
            let tbl = env_table(env);
            let get_chars: GetStringUtfCharsFn = cast(tbl.GetStringUTFChars);
            let rel: ReleaseStringUtfCharsFn = cast(tbl.ReleaseStringUTFChars);
            let chars = get_chars(env, h.ptr as *mut c_void, std::ptr::null_mut());
            let text = if chars.is_null() {
                String::new()
            } else {
                CStr::from_ptr(chars).to_string_lossy().into_owned()
            };
            if !chars.is_null() {
                rel(env, h.ptr as *mut c_void, chars);
            }
            Ok(json!({"v": text}))
        }
        "array_length" => {
            let id = req.get("target").and_then(Value::as_i64).ok_or("缺少 target")?;
            let h = get_handle(id).ok_or("无效句柄")?;
            let f: GetArrayLenFn = cast(env_table(env).GetArrayLength);
            Ok(json!({"v": f(env, h.ptr as *mut c_void)}))
        }
        "new_array" => {
            let spec = req.get("value").ok_or("缺少 value")?;
            let t = s(spec, "t").unwrap_or_else(|| "i".into());
            let n = spec.get("n").and_then(Value::as_i64).unwrap_or(0) as i32;
            let tbl = env_table(env);
            let arr = match t.as_str() {
                "z" => cast::<NewArrayFn>(tbl.NewBooleanArray)(env, n),
                "b" => cast::<NewArrayFn>(tbl.NewByteArray)(env, n),
                "c" => cast::<NewArrayFn>(tbl.NewCharArray)(env, n),
                "s" => cast::<NewArrayFn>(tbl.NewShortArray)(env, n),
                "i" => cast::<NewArrayFn>(tbl.NewIntArray)(env, n),
                "j" => cast::<NewArrayFn>(tbl.NewLongArray)(env, n),
                "f" => cast::<NewArrayFn>(tbl.NewFloatArray)(env, n),
                "d" => cast::<NewArrayFn>(tbl.NewDoubleArray)(env, n),
                "o" | "l" => {
                    let cls = find_class(env, &s(spec, "class").unwrap_or_else(|| "java/lang/Object".into()))?;
                    let f: NewObjArrayFn = cast(tbl.NewObjectArray);
                    let a = f(env, n, cls, std::ptr::null_mut());
                    let del: DeleteLocalRefFn = cast(tbl.DeleteLocalRef);
                    del(env, cls);
                    a
                }
                _ => return Err(format!("未知数组类型 {}", t)),
            };
            if arr.is_null() {
                return Err("new_array 失败".into());
            }
            let new_g: NewGlobalRefFn = cast(tbl.NewGlobalRef);
            let g = new_g(env, arr);
            let del: DeleteLocalRefFn = cast(tbl.DeleteLocalRef);
            del(env, arr);
            let h = new_handle(HandleKind::Array(t.chars().next().unwrap_or('i')), g);
            Ok(json!({"handle": h}))
        }
        "set_array" => {
            let id = req.get("target").and_then(Value::as_i64).ok_or("缺少 target")?;
            let h = get_handle(id).ok_or("无效句柄")?;
            let t = match h.kind {
                HandleKind::Array(t) => t,
                _ => return Err("target 不是数组句柄".into()),
            };
            let index = req.get("index").and_then(Value::as_i64).unwrap_or(0) as i32;
            let val = req.get("value").ok_or("缺少 value")?;
            let tbl = env_table(env);
            let temps: Vec<*mut c_void> = Vec::new();
            match t {
                'i' => {
                    let x = val.as_i64().unwrap_or(0) as i32;
                    let f: SetArrayRegionFn = cast(tbl.SetIntArrayRegion);
                    f(env, h.ptr as *mut c_void, index, 1, &x as *const i32 as *const c_void);
                }
                'j' => {
                    let x = val.as_i64().unwrap_or(0);
                    let f: SetArrayRegionFn = cast(tbl.SetLongArrayRegion);
                    f(env, h.ptr as *mut c_void, index, 1, &x as *const i64 as *const c_void);
                }
                'z' => {
                    let x = val.as_bool().unwrap_or(false) as u8;
                    let f: SetArrayRegionFn = cast(tbl.SetBooleanArrayRegion);
                    f(env, h.ptr as *mut c_void, index, 1, &x as *const u8 as *const c_void);
                }
                'b' => {
                    let x = val.as_i64().unwrap_or(0) as i8;
                    let f: SetArrayRegionFn = cast(tbl.SetByteArrayRegion);
                    f(env, h.ptr as *mut c_void, index, 1, &x as *const i8 as *const c_void);
                }
                'o' | 'l' => {
                    let id2 = val.as_i64().unwrap_or(0);
                    let h2 = get_handle(id2).ok_or("无效元素句柄")?;
                    let f: unsafe extern "C" fn(*mut c_void, *mut c_void, i32, *mut c_void) = cast(tbl.SetObjectArrayElement);
                    f(env, h.ptr as *mut c_void, index, h2.ptr as *mut c_void);
                }
                _ => return Err(format!("set_array 暂不支持类型 {}", t)),
            }
            cleanup_temps(env, &temps);
            check_exception(env).map_or(Ok(json!({"ok": true})), Err)
        }
        "get_array" => {
            let id = req.get("target").and_then(Value::as_i64).ok_or("缺少 target")?;
            let h = get_handle(id).ok_or("无效句柄")?;
            let t = match h.kind {
                HandleKind::Array(t) => t,
                _ => return Err("target 不是数组句柄".into()),
            };
            let index = req.get("index").and_then(Value::as_i64).unwrap_or(0) as i32;
            let tbl = env_table(env);
            match t {
                'i' => {
                    let mut x: i32 = 0;
                    let f: GetArrayRegionFn = cast(tbl.GetIntArrayRegion);
                    f(env, h.ptr as *mut c_void, index, 1, &mut x as *mut i32 as *mut c_void);
                    Ok(json!({"v": x}))
                }
                'j' => {
                    let mut x: i64 = 0;
                    let f: GetArrayRegionFn = cast(tbl.GetLongArrayRegion);
                    f(env, h.ptr as *mut c_void, index, 1, &mut x as *mut i64 as *mut c_void);
                    Ok(json!({"v": x}))
                }
                'o' | 'l' => {
                    let f: unsafe extern "C" fn(*mut c_void, *mut c_void, i32) -> *mut c_void = cast(tbl.GetObjectArrayElement);
                    let o = f(env, h.ptr as *mut c_void, index);
                    if o.is_null() {
                        Ok(json!({"handle": 0}))
                    } else {
                        let new_g: NewGlobalRefFn = cast(tbl.NewGlobalRef);
                        let g = new_g(env, o);
                        let del: DeleteLocalRefFn = cast(tbl.DeleteLocalRef);
                        del(env, o);
                        let nh = new_handle(HandleKind::Obj, g);
                        Ok(json!({"handle": nh}))
                    }
                }
                _ => Err(format!("get_array 暂不支持类型 {}", t)),
            }
        }
        "get_field" | "set_field" | "get_static_field" | "set_static_field" => {
            field_op(env, op, req)
        }
        "exception_check" => {
            let check: ExceptionCheckFn = cast(env_table(env).ExceptionCheck);
            Ok(json!({"v": check(env) != 0}))
        }
        "exception_clear" => {
            let clear: ExceptionClearFn = cast(env_table(env).ExceptionClear);
            clear(env);
            Ok(json!({"ok": true}))
        }
        "is_same" => {
            let a = get_handle(req.get("a").and_then(Value::as_i64).ok_or("缺少 a")?).ok_or("无效 a")?;
            let bb = get_handle(req.get("b").and_then(Value::as_i64).ok_or("缺少 b")?).ok_or("无效 b")?;
            let f: IsSameObjFn = cast(env_table(env).IsSameObject);
            Ok(json!({"v": f(env, a.ptr as *mut c_void, bb.ptr as *mut c_void) != 0}))
        }
        _ => Err(format!("未知 op: {}", op)),
    }
}

unsafe fn resolve_class(env: *mut c_void, req: &Value) -> Result<*mut c_void, String> {
    if let Some(ch) = req.get("class_handle").and_then(Value::as_i64) {
        let h = get_handle(ch).ok_or("无效 class_handle")?;
        return Ok(h.ptr as *mut c_void);
    }
    if let Some(name) = s(req, "class") {
        return find_class(env, &name);
    }
    // 实例调用：从 target 对象的实际 class 取（GetObjectClass）
    if let Some(t) = req.get("target").and_then(Value::as_i64) {
        let h = get_handle(t).ok_or("无效 target 句柄")?;
        let f: GetObjectClassFn = cast(env_table(env).GetObjectClass);
        let cls = f(env, h.ptr as *mut c_void);
        if cls.is_null() {
            return Err("GetObjectClass 失败".into());
        }
        return Ok(cls);
    }
    Err("缺少 class / class_handle / target".into())
}

unsafe fn field_op(env: *mut c_void, op: &str, req: &Value) -> Result<Value, String> {
    let static_ = op.contains("static");
    let set = op.starts_with("set");
    let name = s(req, "name").ok_or("缺少 name")?;
    let sig = s(req, "sig").ok_or("缺少 sig")?;

    let obj = if static_ {
        resolve_class(env, req)?
    } else {
        let id = req.get("target").and_then(Value::as_i64).ok_or("缺少 target")?;
        let h = get_handle(id).ok_or("无效句柄")?;
        h.ptr as *mut c_void
    };
    // 用对象实际类取 fieldID（静态则用 class）
    let cls = if static_ {
        obj
    } else {
        let f: GetObjectClassFn = cast(env_table(env).GetObjectClass);
        f(env, obj)
    };
    let fid = {
        let n = CString::new(name.as_str()).unwrap();
        let sg = CString::new(sig.as_str()).unwrap();
        let tbl = env_table(env);
        let gf: GetMethodFn = cast(if static_ { tbl.GetStaticFieldID } else { tbl.GetFieldID });
        let id = gf(env, cls, n.as_ptr(), sg.as_ptr());
        if id.is_null() {
            return Err(format!("找不到字段 {}", name));
        }
        id
    };
    let t = sig.chars().next().map(normalize_type).unwrap_or('v');
    let tbl = env_table(env);

    if set {
        let val = req.get("value").ok_or("缺少 value")?;
        macro_rules! setf {
            ($fn_slot:expr, $ty:ty, $conv:expr) => {{
                let f: unsafe extern "C" fn(*mut c_void, *mut c_void, *mut c_void, $ty) = cast($fn_slot);
                f(env, obj, fid, $conv(val));
            }};
        }
        match t {
            'i' => setf!(if static_ { tbl.SetStaticIntField } else { tbl.SetIntField }, i32, |v: &Value| v.as_i64().unwrap_or(0) as i32),
            'j' => setf!(if static_ { tbl.SetStaticLongField } else { tbl.SetLongField }, i64, |v: &Value| v.as_i64().unwrap_or(0)),
            'z' => setf!(if static_ { tbl.SetStaticBooleanField } else { tbl.SetBooleanField }, u8, |v: &Value| v.as_bool().unwrap_or(false) as u8),
            'f' => setf!(if static_ { tbl.SetStaticFloatField } else { tbl.SetFloatField }, f32, |v: &Value| v.as_f64().unwrap_or(0.0) as f32),
            'd' => setf!(if static_ { tbl.SetStaticDoubleField } else { tbl.SetDoubleField }, f64, |v: &Value| v.as_f64().unwrap_or(0.0)),
            _ => return Err(format!("set_field 暂不支持类型 {}", t)),
        }
        check_exception(env).map_or(Ok(json!({"ok": true})), Err)
    } else {
        macro_rules! getf {
            ($fn_slot:expr, $ty:ty) => {{
                let f: unsafe extern "C" fn(*mut c_void, *mut c_void, *mut c_void) -> $ty = cast($fn_slot);
                f(env, obj, fid)
            }};
        }
        match t {
            'i' => Ok(json!({"v": getf!(if static_ { tbl.GetStaticIntField } else { tbl.GetIntField }, i32)})),
            'j' => Ok(json!({"v": getf!(if static_ { tbl.GetStaticLongField } else { tbl.GetLongField }, i64)})),
            'z' => Ok(json!({"v": getf!(if static_ { tbl.GetStaticBooleanField } else { tbl.GetBooleanField }, u8) != 0})),
            'f' => Ok(json!({"v": getf!(if static_ { tbl.GetStaticFloatField } else { tbl.GetFloatField }, f32)})),
            'd' => Ok(json!({"v": getf!(if static_ { tbl.GetStaticDoubleField } else { tbl.GetDoubleField }, f64)})),
            _ => Err(format!("get_field 暂不支持类型 {}", t)),
        }
    }
}

// ---- 输出序列化 ----
fn write_value(out: *mut c_char, cap: i32, v: &Value) -> i32 {
    let text = v.to_string();
    if text.len() + 1 > cap as usize {
        log::log_msg(vapor_abi::LOG_ERROR, "vapor_jni_call: 输出缓冲区不足");
        return VAPOR_ERR;
    }
    unsafe {
        std::ptr::copy_nonoverlapping(text.as_ptr() as *const c_char, out, text.len());
        *out.add(text.len()) = 0;
    }
    VAPOR_OK
}

fn write_err(out: *mut c_char, cap: i32, msg: &str) -> i32 {
    write_value(out, cap, &json!({"ok": false, "error": msg}))
}

pub fn release(handle: i64) -> i32 {
    let h = match handles().lock().unwrap().remove(&handle) {
        Some(h) => h,
        None => return VAPOR_ERR,
    };
    match h.kind {
        HandleKind::Obj | HandleKind::Array(_) => {
            let env = get_env();
            if env.is_null() {
                return VAPOR_ERR;
            }
            unsafe {
                let del: DeleteGlobalRefFn = cast(env_table(env).DeleteGlobalRef);
                del(env, h.ptr as *mut c_void);
            }
        }
        HandleKind::Method => {}
    }
    VAPOR_OK
}

/// 关闭时清理所有句柄（不删全局引用，JVM 即将销毁）。
pub fn shutdown_clear() {
    handles().lock().unwrap().clear();
}
