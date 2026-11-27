# JNI 桥接层

Vapor 通过稳定 C ABI 暴露一套 JNI 桥接，使 **C 扩展、Python 扩展、Python 逻辑包** 都能调用任意 JNI。核心目标：加功能的扩展可以触达 Android/Java 侧的一切能力（Activity、ContentResolver、Toast、传感器、第三方 SDK……），无需改动 Rust 引擎。

## 设计

1. **不链接 libjvm**：`JavaVM*` / `JNIEnv*` 本质是「指向函数表的指针」。桥接层直接从函数表读函数指针调用（`src/jni_table.rs` 镜像了 OpenJDK/Android 的 `JNINativeInterface_`（231 项）与 `JNIInvokeInterface_`（5 项））。Rust 编译器按目标架构自动计算偏移——**arm32 用 4 字节、arm64 用 8 字节指针，同一份代码通用**，无需硬编码偏移、无需链接 JVM 库。
2. **VM 自动注入**：NativeActivity 的 JNI 入口 `Java_com_vapor_RendererActivity_nativeInit` 从 `JNIEnv::GetJavaVM` 取出 `JavaVM*` 自动注入；也可手动调用 `vapor_jni_set_vm(vm)`。
3. **句柄化对象**：jobject/jclass/jarray/jmethodID 跨调用以 `int64` 句柄持有（内部 `NewGlobalRef`），用 `vapor_jni_release(handle)` 释放。
4. **JSON 协议**：一条 `vapor_jni_call(req_json, out_json, cap)` 覆盖全部 JNI 操作，C 用 `snprintf` 拼 JSON、Python 用 `json.dumps`，共用同一套语义。

## C ABI（追加在 VaporApi 尾部，前向兼容 v1.0 老扩展）

```c
int32_t vapor_jni_set_vm(void* java_vm);
void*   vapor_jni_get_vm(void);
void*   vapor_jni_get_env(void);          /* 自动 attach 当前线程 */
int32_t vapor_jni_detach(void);
int32_t vapor_jni_call(const char* req_json, char* out_json, int32_t cap);
int32_t vapor_jni_release(int64_t handle);
```

`VaporApi` 函数表尾部追加同名的 6 个函数指针，C 扩展 init 时即可通过 `api->jni_call(...)` 调用。

## 请求 / 响应

请求字段（JSON 对象）：

| 字段 | 说明 |
|------|------|
| `op` | `find_class` / `get_method` / `get_static_method` / `call` / `call_static` / `new_object` / `new_string` / `get_string` / `new_array` / `get_array` / `set_array` / `array_length` / `get_field` / `set_field` / `get_static_field` / `set_static_field` / `exception_check` / `exception_clear` / `is_same` |
| `class` | JNI 类名，斜杠分隔，如 `java/lang/String` |
| `name` / `sig` | 方法/字段名与 JNI 签名，如 `(I)Ljava/lang/String;` |
| `static` | 静态方法/字段 |
| `target` | 接收者对象/类/数组句柄 |
| `args` | 参数数组，`{"t":"i\|j\|f\|d\|z\|b\|c\|s\|l\|o\|a","v":...}` |
| `ret` | 期望返回类型（缺省从 sig 解析）；`l`=读字符串，`v`=void |
| `value` / `index` | new_string/new_array 的值；数组索引 |

响应：`{"ok":true,"t":"i","v":7}` 或带 `"handle":N`（对象）；错误 `{"ok":false,"error":"..."}`。

## Python 用法

```python
from rust import jni

# 静态方法
r = jni.call_static("java/lang/Math", "max", "(II)I", [jni.i(3), jni.i(7)])  # 7

# 字符串（句柄用 with 自动释放）
with jni.new_string("hello 世界") as s:
    out = jni.call_static("java/lang/String", "valueOf",
                          "(Ljava/lang/Object;)Ljava/lang/String;", [jni.obj(s)])
    print(jni.get_string(out))   # hello 世界
    jni.release(out)

# 实例方法：StringBuilder("ab").append("cd").toString()
with jni.new_object("java/lang/StringBuilder", "(Ljava/lang/String;)V",
                    [jni.string("ab")]) as sb:
    jni.call(sb, "append", "(Ljava/lang/String;)Ljava/lang/StringBuilder;",
             [jni.string("cd")], ret="o")
    print(jni.call(sb, "toString", "()Ljava/lang/String;", ret="l"))  # abcd

# 数组 / 字段 / 异常
with jni.new_array("i", 3) as arr:
    jni.set_array(arr, 0, 11)
    assert jni.get_array(arr, 0) == 11
mv = jni.get_field(None, "MAX_VALUE", "I", static=True, class_name="java/lang/Integer")
jni.exception_check()
```

## C 扩展示例

`protocol/example/lib/hello_jni.c` 展示两条路径：

- `api->jni_call(JSON, ...)`：通用桥接（推荐，双架构一致）
- `api->jni_get_env()`：拿裸 `JNIEnv*`，用标准 `jni.h` 调任意 JNI（完全自由）

## 本机验证

```bash
JH=$JAVA_HOME
gcc -shared -fPIC -I$JH/include -I$JH/include/linux \
    python/tests/jni_host.c -o python/tests/libjni_host.so \
    -L$JH/lib/server -ljvm -Wl,-rpath,$JH/lib/server
cargo build -p vapor-renderer
VAPOR_NATIVE_LIB=$PWD/target/debug/libvapor.so \
PYTHONPATH=$PWD/python python3 python/tests/test_jni.py
```

`test_jni.py` 启动真实 JVM，验证 18 项：VM 注入、静态/实例方法、字符串、对象构造、数组、静态字段、异常路径、底层 C ABI JSON 调用。全部通过。
