package com.vapor;

import android.app.Activity;
import android.os.Bundle;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.WindowManager;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * app1 纯渲染器入口。
 *
 * 自己持有 SurfaceView，在 Surface 就绪后把底层 ANativeWindow 交给 Rust：
 *   - nativeInit(Surface, w, h)：从 Surface 取 ANativeWindow、初始化 EGL/GLES，
 *     启动内嵌 CPython 执行逻辑包 main.py，并从 JNIEnv 取 JavaVM* 注入 JNI 桥接层。
 *   - nativeResize(w, h)：Surface 尺寸变化时调整帧缓冲。
 *   - nativeRender()：把共享内存帧缓冲上传 GLES 并交换缓冲。
 *   - nativeShutdown()：释放渲染与 Python 资源。
 */
public class RendererActivity extends Activity implements SurfaceHolder.Callback {
    static {
        System.loadLibrary("vapor");
    }

    private native void nativeInit(Surface surface, int w, int h, String appDir, String privateDir);
    private native void nativeResize(int w, int h);
    private native void nativeRender();
    private native void nativeShutdown();

    private RenderThread renderThread;
    private volatile boolean running = false;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        // 把内置 Python 运行时与逻辑包解压到私有目录，供 Rust 内嵌 CPython 加载。
        try {
            File files = getFilesDir();
            copyAssets("python", new File(files, "python"));
            copyAssets("app", new File(files, "app"));
        } catch (IOException e) {
            android.util.Log.e("vapor", "解压 assets 失败", e);
        }
        SurfaceView view = new SurfaceView(this);
        view.getHolder().addCallback(this);
        setContentView(view);
    }

    /** 递归复制 assets 子目录到目标路径。 */
    private void copyAssets(String assetPath, File dest) throws IOException {
        String[] items = getAssets().list(assetPath);
        if (items == null || items.length == 0) {
            // 普通文件
            try (InputStream in = getAssets().open(assetPath);
                 OutputStream out = new FileOutputStream(dest)) {
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            }
            return;
        }
        if (!dest.exists()) dest.mkdirs();
        for (String item : items) {
            String child = assetPath.equals("") ? item : assetPath + "/" + item;
            copyAssets(child, new File(dest, item));
        }
    }

    @Override
    public void surfaceCreated(SurfaceHolder holder) {
        Surface surface = holder.getSurface();
        int w = holder.getSurfaceFrame().width();
        int h = holder.getSurfaceFrame().height();
        File files = getFilesDir();
        nativeInit(surface, w, h,
                   new File(files, "app").getAbsolutePath(),
                   files.getAbsolutePath());
        running = true;
        renderThread = new RenderThread();
        renderThread.start();
    }

    @Override
    public void surfaceChanged(SurfaceHolder holder, int format, int w, int h) {
        nativeResize(w, h);
    }

    @Override
    public void surfaceDestroyed(SurfaceHolder holder) {
        running = false;
        if (renderThread != null) {
            try {
                renderThread.join();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            renderThread = null;
        }
        nativeShutdown();
    }

    /** 渲染线程：约 60fps 驱动 Rust 把帧缓冲上屏。 */
    private class RenderThread extends Thread {
        @Override
        public void run() {
            while (running) {
                nativeRender();
                try {
                    Thread.sleep(16);
                } catch (InterruptedException e) {
                    break;
                }
            }
        }
    }
}
