package com.vapor;

import android.app.Activity;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.MotionEvent;
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
    private native void nativeTouch(int action, float x, float y,
                                    int pointerId, int pointerCount, float x2, float y2);
    private native boolean nativeKey(int keycode, int action);
    private native void nativeWheel(float dx, float dy);
    private native void nativePermissionResult(int code, boolean granted);

    private RenderThread renderThread;
    private volatile boolean running = false;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        // 把内置 Python 运行时与逻辑包解压到私有目录，供 Rust 内嵌 CPython 加载。
        // 标准库有上千个文件，绝不能每次启动都全量重解压；用 APK 的修改时间做版本戳，
        // 仅在首次安装 / 升级后解压一次（否则低端机启动会被 IO 拖到 ANR）。
        try {
            File files = getFilesDir();
            File stamp = new File(files, ".vapor_assets_stamp");
            long apkTime = new File(getPackageCodePath()).lastModified();
            if (!assetsUpToDate(stamp, apkTime)) {
                long t0 = System.currentTimeMillis();
                copyAssets("python", new File(files, "python"));
                // 按当前 ABI 把 PyO3 扩展（Vapor._core/_ui/_sdk）复制到 site-packages，
                // 因为同一个 APK 里两个架构的 .so 同名，只能按机型选一份。
                copyAssets("python/pyext/" + abiTag(), new File(files, "python/site-packages"));
                copyAssets("app", new File(files, "app"));
                writeStamp(stamp, apkTime);
                android.util.Log.i("vapor", "assets 解压完成 " + (System.currentTimeMillis() - t0) + "ms");
            }
        } catch (IOException e) {
            android.util.Log.e("vapor", "解压 assets 失败", e);
        }
        SurfaceView view = new SurfaceView(this);
        view.getHolder().addCallback(this);
        view.setFocusable(true);
        view.setFocusableInTouchMode(true);
        view.setOnTouchListener((v, event) -> {
            forwardTouch(event);
            return true;
        });
        setContentView(view);
    }

    /**
     * 把 MotionEvent 归一化成 (down/up/move/cancel + 单指针坐标) 转发给 Rust。
     * 多指：按 actionIndex 取当前手指的 id 与坐标，第二根手指坐标一并带上。
     */
    private void forwardTouch(MotionEvent event) {
        int masked = event.getActionMasked();
        int action;
        switch (masked) {
            case MotionEvent.ACTION_DOWN:
                action = 0;
                break;
            case MotionEvent.ACTION_POINTER_DOWN:
                action = 4;   // 第二根手指按下（多指手势开始）
                break;
            case MotionEvent.ACTION_UP:
                action = 1;
                break;
            case MotionEvent.ACTION_POINTER_UP:
                action = 5;   // 第二根手指抬起
                break;
            case MotionEvent.ACTION_MOVE:
                action = 2;
                break;
            case MotionEvent.ACTION_CANCEL:
                action = 3;
                break;
            default:
                return;
        }
        int index = (masked == MotionEvent.ACTION_POINTER_DOWN
                || masked == MotionEvent.ACTION_POINTER_UP)
                ? event.getActionIndex() : 0;
        int pointerId = event.getPointerId(index);
        float x = event.getX(index);
        float y = event.getY(index);
        float x2 = event.getPointerCount() > 1 ? event.getX(1) : x;
        float y2 = event.getPointerCount() > 1 ? event.getY(1) : y;
        nativeTouch(action, x, y, pointerId, event.getPointerCount(), x2, y2);
    }

    /**
     * 供 Python 调用：声明过的危险权限在这里按需弹窗申请。
     * 必须切到 UI 线程（runOnUiThread），因为 requestPermissions 只能主线程调用。
     */
    public void requestPermissionFromNative(final String permission, final int code) {
        runOnUiThread(() -> {
            try {
                requestPermissions(new String[]{permission}, code);
            } catch (Throwable t) {
                nativePermissionResult(code, false);
            }
        });
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        boolean granted = grantResults.length > 0;
        for (int result : grantResults) {
            if (result != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                granted = false;
                break;
            }
        }
        nativePermissionResult(requestCode, granted);
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
    }

    /**
     * 鼠标 / 触控板：滚轮走 nativeWheel，悬停移动当成普通 move（方便桌面调试）。
     */
    @Override
    public boolean onGenericMotionEvent(MotionEvent event) {
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_SCROLL) {
            float dy = event.getAxisValue(MotionEvent.AXIS_VSCROLL);
            float dx = event.getAxisValue(MotionEvent.AXIS_HSCROLL);
            nativeWheel(dx, dy);
            return true;
        }
        if (action == MotionEvent.ACTION_HOVER_MOVE) {
            nativeTouch(2, event.getX(), event.getY(), 0, 1, event.getX(), event.getY());
            return true;
        }
        return super.onGenericMotionEvent(event);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        // 转发给 Python；返回 true 表示 Python 运行时接管（页面栈/back 处理器决定后续，
        // 未处理时由 Python 调 Activity.finish()）。没有 Python 时交回系统默认处理。
        boolean handled = nativeKey(keyCode, 0);
        if (handled) {
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    public boolean onKeyUp(int keyCode, KeyEvent event) {
        boolean handled = nativeKey(keyCode, 1);
        if (handled) {
            return true;
        }
        return super.onKeyUp(keyCode, event);
    }

    /** 当前设备的主 ABI（决定复制哪一份 PyO3 原生扩展）。 */
    private String abiTag() {
        String[] abis = android.os.Build.SUPPORTED_ABIS;
        if (abis != null) {
            for (String abi : abis) {
                if (abi != null && abi.startsWith("arm64")) {
                    return "arm64-v8a";
                }
            }
        }
        return "armeabi-v7a";
    }

    /** assets 是否已按当前 APK 版本解压过（用 APK 修改时间作为版本戳）。 */
    private boolean assetsUpToDate(File stamp, long apkTime) {
        if (!stamp.isFile()) return false;
        try (InputStream in = new java.io.FileInputStream(stamp)) {
            byte[] buf = new byte[64];
            int n = in.read(buf);
            if (n <= 0) return false;
            String got = new String(buf, 0, n, "UTF-8").trim();
            return got.equals(Long.toString(apkTime));
        } catch (IOException e) {
            return false;
        }
    }

    private void writeStamp(File stamp, long apkTime) {
        try (OutputStream out = new FileOutputStream(stamp)) {
            out.write(Long.toString(apkTime).getBytes("UTF-8"));
        } catch (IOException e) {
            android.util.Log.w("vapor", "写入 assets 版本戳失败", e);
        }
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
