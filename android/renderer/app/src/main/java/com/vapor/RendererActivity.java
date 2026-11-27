package com.vapor;

import android.app.NativeActivity;
import android.os.Bundle;
import android.view.WindowManager;

/**
 * app1 纯渲染器入口。几乎不含逻辑——全部渲染与业务来自 Rust (libvapor.so)
 * 与逻辑包 (assets/vapor_logic.zip 解压后的 main.py)。
 * NativeActivity 在 Surface 创建后由系统回调 nativeInit（见 Rust 侧 JNI 导出）。
 */
public class RendererActivity extends NativeActivity {
    static {
        System.loadLibrary("vapor");
    }

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }
}
