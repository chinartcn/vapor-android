package com.vapor;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.widget.*;
import androidx.annotation.Nullable;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import java.io.*;
import java.nio.file.Files;

/**
 * app2 纯打包器。UI 用原生控件（可引入依赖，如 Material Components），
 * 打包核心通过 JNI 调用 Rust 的 libvapor_packager.so。
 *
 * 流程：请求存储权限 → 选空壳 APK + 逻辑 zip → 填应用名/版本/包名/图标 →
 *       调 Rust 打包 → 输出到 /storage/emulated/0/rustoutputapk，
 *       无权限则回退应用私有目录。
 */
public class PackagerActivity extends Activity {
    static { System.loadLibrary("vapor_packager"); }

    private static final int REQ_STORAGE = 1001;
    private static final int PICK_SHELL = 2001;
    private static final int PICK_LOGIC = 2002;
    private static final int PICK_ICON = 2003;

    private Uri shellUri, logicUri, iconUri;
    private EditText edName, edVersion, edPackage, edCode;
    private TextView tvStatus;

    // JNI：Rust 打包核心，返回 APK 字节；失败抛异常。
    private static native byte[] nativePackage(
            byte[] shellApk, byte[] logicZip, byte[] iconPng,
            String appName, String versionName, int versionCode,
            String packageName, String shellPackage, String shellAppName);

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(32, 32, 32, 32);

        tvStatus = new TextView(this);
        tvStatus.setText("Vapor Packager — 选择空壳 APK 与逻辑 zip");
        root.addView(tvStatus);

        Button btnShell = button("1. 选择空壳渲染器 APK");
        btnShell.setOnClickListener(v -> pick(PICK_SHELL, "*/*"));
        Button btnLogic = button("2. 选择逻辑 zip");
        btnLogic.setOnClickListener(v -> pick(PICK_LOGIC, "application/zip"));
        Button btnIcon = button("3. 选择应用图标 PNG（可选）");
        btnIcon.setOnClickListener(v -> pick(PICK_ICON, "image/png"));

        edName = edit("应用名称", "我的游戏");
        edVersion = edit("版本号", "1.0.0");
        edCode = edit("版本代码", "1");
        edPackage = edit("包名", "com.acme.mygame");

        Button btnPack = button("开始打包");
        btnPack.setOnClickListener(v -> doPack());
        root.addView(btnShell); root.addView(btnLogic); root.addView(btnIcon);
        root.addView(edName); root.addView(edVersion); root.addView(edCode); root.addView(edPackage);
        root.addView(btnPack);
        setContentView(root);

        requestStorage();
    }

    private Button button(String t) { Button b = new Button(this); b.setText(t); return b; }
    private EditText edit(String hint, String def) {
        EditText e = new EditText(this); e.setHint(hint); e.setText(def); return e;
    }

    private void requestStorage() {
        if (Build.VERSION.SDK_INT >= 33) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.READ_MEDIA_IMAGES)
                    != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(this,
                        new String[]{Manifest.permission.READ_MEDIA_IMAGES}, REQ_STORAGE);
            }
        } else if (ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this,
                    new String[]{Manifest.permission.READ_EXTERNAL_STORAGE,
                                 Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
        }
    }

    private void pick(int req, String type) {
        Intent i = new Intent(Intent.ACTION_GET_CONTENT);
        i.setType(type);
        i.addCategory(Intent.CATEGORY_OPENABLE);
        startActivityForResult(i, req);
    }

    @Override
    protected void onActivityResult(int req, int res, @Nullable Intent data) {
        super.onActivityResult(req, res, data);
        if (res != RESULT_OK || data == null || data.getData() == null) return;
        switch (req) {
            case PICK_SHELL: shellUri = data.getData(); tvStatus.setText("已选空壳 APK"); break;
            case PICK_LOGIC: logicUri = data.getData(); tvStatus.setText("已选逻辑 zip"); break;
            case PICK_ICON:  iconUri  = data.getData(); tvStatus.setText("已选图标"); break;
        }
    }

    private byte[] readUri(Uri u) throws IOException {
        try (InputStream in = getContentResolver().openInputStream(u)) {
            return readAll(in);
        }
    }

    private byte[] readAll(InputStream in) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192]; int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        return out.toByteArray();
    }

    private void doPack() {
        try {
            if (shellUri == null || logicUri == null) {
                tvStatus.setText("请先选择空壳 APK 与逻辑 zip"); return;
            }
            byte[] shell = readUri(shellUri);
            byte[] logic = readUri(logicUri);
            byte[] icon = iconUri == null ? null : readUri(iconUri);
            int code;
            try { code = Integer.parseInt(edCode.getText().toString()); }
            catch (NumberFormatException e) { code = 1; }

            byte[] apk = nativePackage(shell, logic, icon,
                    edName.getText().toString(), edVersion.getText().toString(), code,
                    edPackage.getText().toString(),
                    "com.vapor.shell", "Vapor Shell");

            // 输出路径：优先公共目录，失败回退私有目录
            File outFile = new File(Environment.getExternalStorageDirectory(), "rustoutputapk/app.apk");
            boolean ok = writeApk(outFile, apk);
            if (!ok) {
                outFile = new File(getExternalFilesDir(null), "rustoutputapk/app.apk");
                ok = writeApk(outFile, apk);
            }
            tvStatus.setText(ok ? "打包完成: " + outFile.getAbsolutePath() : "打包失败：无法写入输出目录");
        } catch (Exception e) {
            tvStatus.setText("打包失败: " + e.getMessage());
        }
    }

    private boolean writeApk(File f, byte[] data) {
        try {
            File parent = f.getParentFile();
            if (parent != null && !parent.exists() && !parent.mkdirs()) return false;
            Files.write(f.toPath(), data);
            return true;
        } catch (Exception e) { return false; }
    }
}
