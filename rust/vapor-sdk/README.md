# vapor-sdk

`Vapor._sdk` —— Vapor 的 Android SDK 桥接层（maturin 打包）。

* 运行时 `dlopen` `libvapor.so`（**不链接**），只依赖 `include/vapor_abi.h` 的稳定 C ABI
* 解析并调用 `vapor_jni_call` / `vapor_fs_*` / `vapor_fb_*` / `vapor_jni_set_vm`
* 同一份二进制在 Android（ART）与本机（OpenJDK）都能用

高层的 Android 风格 API（Toast / 振动 / 剪贴板 / Intent / SharedPreferences）在
`Vapor/sdk.py` 里，由本模块的 JSON 协议驱动。
