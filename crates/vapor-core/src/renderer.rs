//! GLES 3.0 上屏渲染器。
//!
//! Android 目标：EGL 上下文 + 一个全屏纹理四边形，把帧缓冲（RGBA8888）作为
//! 纹理上传并绘制。Python 只写共享内存像素，GPU 绘制完全在 Rust 侧。
//! 非 Android 目标：空实现，用于本机验证 ABI / 逻辑（不链接 EGL/GLES）。

use crate::state::{State, DEFAULT_H, DEFAULT_W};
use crate::log::{log_msg, LOG_INFO};

#[cfg(target_os = "android")]
mod gles {
    use std::ffi::c_void;
    use crate::log::{log_msg, LOG_INFO};

    pub type EGLDisplay = *mut c_void;
    pub type EGLSurface = *mut c_void;
    pub type EGLContext = *mut c_void;

    static SWAP_ERR_LOGGED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

    #[link(name = "EGL")]
    extern "C" {
        pub fn eglGetDisplay(d: i32) -> EGLDisplay;
        pub fn eglInitialize(d: EGLDisplay, major: *mut i32, minor: *mut i32) -> i32;
        pub fn eglChooseConfig(d: EGLDisplay, attrs: *const i32, out: *mut EGLDisplay, n: i32, num: *mut i32) -> i32;
        pub fn eglCreateWindowSurface(d: EGLDisplay, cfg: EGLDisplay, win: *mut c_void, attrs: *const i32) -> EGLSurface;
        pub fn eglCreateContext(d: EGLDisplay, cfg: EGLDisplay, share: EGLContext, attrs: *const i32) -> EGLContext;
        pub fn eglMakeCurrent(d: EGLDisplay, draw: EGLSurface, read: EGLSurface, ctx: EGLContext) -> i32;
        pub fn eglSwapBuffers(d: EGLDisplay, s: EGLSurface) -> i32;
        pub fn eglGetError() -> i32;
        pub fn eglDestroySurface(d: EGLDisplay, s: EGLSurface) -> i32;
        pub fn eglDestroyContext(d: EGLDisplay, c: EGLContext) -> i32;
        pub fn eglTerminate(d: EGLDisplay) -> i32;
    }

    #[link(name = "android")]
    extern "C" {
        pub fn ANativeWindow_getWidth(w: *mut c_void) -> i32;
        pub fn ANativeWindow_getHeight(w: *mut c_void) -> i32;
    }

    #[link(name = "GLESv2")]
    extern "C" {
        pub fn glGenTextures(n: i32, out: *mut u32);
        pub fn glBindTexture(target: u32, tex: u32);
        pub fn glTexParameteri(target: u32, pname: u32, param: i32);
        pub fn glTexImage2D(target: u32, level: i32, internal: i32, w: i32, h: i32, border: i32, format: u32, ty: u32, data: *const c_void);
        pub fn glTexSubImage2D(target: u32, level: i32, x: i32, y: i32, w: i32, h: i32, format: u32, ty: u32, data: *const c_void);
        pub fn glClearColor(r: f32, g: f32, b: f32, a: f32);
        pub fn glClear(mask: u32);
        pub fn glViewport(x: i32, y: i32, w: i32, h: i32);
        pub fn glUseProgram(prog: u32);
        pub fn glCreateShader(ty: u32) -> u32;
        pub fn glShaderSource(shader: u32, count: i32, src: *const *const i8, len: *const i32);
        pub fn glCompileShader(shader: u32);
        pub fn glCreateProgram() -> u32;
        pub fn glAttachShader(prog: u32, shader: u32);
        pub fn glLinkProgram(prog: u32);
        pub fn glGetUniformLocation(prog: u32, name: *const i8) -> i32;
        pub fn glUniform1i(loc: i32, v: i32);
        pub fn glGenBuffers(n: i32, out: *mut u32);
        pub fn glBindBuffer(target: u32, buf: u32);
        pub fn glBufferData(target: u32, size: isize, data: *const c_void, usage: u32);
        pub fn glEnableVertexAttribArray(idx: u32);
        pub fn glVertexAttribPointer(idx: u32, size: i32, ty: u32, norm: u8, stride: i32, ptr: *const c_void);
        pub fn glDrawArrays(mode: u32, first: i32, count: i32);
        pub fn glBindAttribLocation(prog: u32, index: u32, name: *const i8);
        pub fn glGetShaderiv(shader: u32, pname: u32, out: *mut i32);
        pub fn glGetShaderInfoLog(shader: u32, cap: i32, len: *mut i32, log: *mut i8);
        pub fn glGetProgramiv(prog: u32, pname: u32, out: *mut i32);
        pub fn glGetProgramInfoLog(prog: u32, cap: i32, len: *mut i32, log: *mut i8);
        pub fn glDeleteShader(shader: u32);
        pub fn glDeleteProgram(prog: u32);
        pub fn glDeleteTextures(n: i32, tex: *const u32);
        pub fn glDeleteBuffers(n: i32, buf: *const u32);
    }

    pub const GL_TEXTURE_2D: u32 = 0x0DE1;
    pub const GL_RGBA: u32 = 0x1908;
    pub const GL_UNSIGNED_BYTE: u32 = 0x1401;
    pub const GL_TEXTURE_MIN_FILTER: u32 = 0x2801;
    pub const GL_TEXTURE_MAG_FILTER: u32 = 0x2800;
    pub const GL_LINEAR: i32 = 0x2601;
    pub const GL_CLAMP_TO_EDGE: u32 = 0x812F;
    pub const GL_TEXTURE_WRAP_S: u32 = 0x2802;
    pub const GL_TEXTURE_WRAP_T: u32 = 0x2803;
    pub const GL_COLOR_BUFFER_BIT: u32 = 0x4000;
    pub const GL_ARRAY_BUFFER: u32 = 0x8892;
    pub const GL_STATIC_DRAW: u32 = 0x88E4;
    pub const GL_FLOAT: u32 = 0x1406;
    pub const GL_TRIANGLE_STRIP: u32 = 0x0005;
    pub const GL_VERTEX_SHADER: u32 = 0x8B31;
    pub const GL_FRAGMENT_SHADER: u32 = 0x8B30;
    pub const GL_COMPILE_STATUS: u32 = 0x8B81;
    pub const GL_LINK_STATUS: u32 = 0x8B82;
    pub const EGL_OPENGL_ES3_BIT: i32 = 0x00000040;
    pub const EGL_OPENGL_ES2_BIT: i32 = 0x00000004;
    // EGL 属性 key（此前把 EGL_SURFACE_TYPE=0x3033 误当成 EGL_RENDERABLE_TYPE，
    // 导致 eglChooseConfig 选不到支持 window 的 config，surface 一直为 null）。
    pub const EGL_RENDERABLE_TYPE: i32 = 0x3040;
    pub const EGL_SURFACE_TYPE: i32 = 0x3033;
    pub const EGL_WINDOW_BIT: i32 = 0x0004;
    pub const EGL_RED_SIZE: i32 = 0x3024;
    pub const EGL_GREEN_SIZE: i32 = 0x3023;
    pub const EGL_BLUE_SIZE: i32 = 0x3022;
    pub const EGL_ALPHA_SIZE: i32 = 0x3021;
    pub const EGL_NONE: i32 = 0x3038;
    pub const EGL_CONTEXT_CLIENT_VERSION: i32 = 0x3098;
    pub const EGL_SUCCESS: i32 = 0x3000;

    pub struct GlState {
        pub display: EGLDisplay,
        pub surface: EGLSurface,
        pub context: EGLContext,
        pub window: *mut c_void,
        pub es2: bool,
        pub texture: u32,
        pub program: u32,
        pub vbo: u32,
        pub tex_w: i32,
        pub tex_h: i32,
    }
    // EGL 句柄是进程级不透明指针；GL_STATE 只在渲染线程访问，Mutex 已保证互斥。
    unsafe impl Send for GlState {}

    const VS_ES3: &str = "#version 300 es\n\
        in vec2 aPos;\n\
        in vec2 aUV;\n\
        out vec2 vUV;\n\
        void main(){ vUV=aUV; gl_Position=vec4(aPos,0.0,1.0); }";

    const FS_ES3: &str = "#version 300 es\n\
        precision mediump float;\n\
        in vec2 vUV;\n\
        uniform sampler2D uTex;\n\
        out vec4 frag;\n\
        void main(){ frag=texture(uTex, vUV); }";

    // ES2 回退着色器：ES3 上下文创建失败时必须用这套，否则 #version 300 es 无法编译。
    const VS_ES2: &str = "#version 100\n\
        attribute vec2 aPos;\n\
        attribute vec2 aUV;\n\
        varying vec2 vUV;\n\
        void main(){ vUV=aUV; gl_Position=vec4(aPos,0.0,1.0); }";

    const FS_ES2: &str = "#version 100\n\
        precision mediump float;\n\
        varying vec2 vUV;\n\
        uniform sampler2D uTex;\n\
        void main(){ gl_FragColor=texture2D(uTex, vUV); }";

    fn info_log_shader(sh: u32) -> String {
        let mut buf = [0i8; 1024];
        let mut len = 0i32;
        unsafe { glGetShaderInfoLog(sh, buf.len() as i32, &mut len, buf.as_mut_ptr()); }
        let n = len.max(0) as usize;
        String::from_utf8_lossy(&buf[..n.min(buf.len())].iter().map(|b| *b as u8).collect::<Vec<u8>>()).to_string()
    }

    fn info_log_program(prog: u32) -> String {
        let mut buf = [0i8; 1024];
        let mut len = 0i32;
        unsafe { glGetProgramInfoLog(prog, buf.len() as i32, &mut len, buf.as_mut_ptr()); }
        let n = len.max(0) as usize;
        String::from_utf8_lossy(&buf[..n.min(buf.len())].iter().map(|b| *b as u8).collect::<Vec<u8>>()).to_string()
    }

    fn compile(ty: u32, src: &str) -> Result<u32, String> {
        unsafe {
            let sh = glCreateShader(ty);
            let p = src.as_ptr() as *const i8;
            glShaderSource(sh, 1, &p, std::ptr::null());
            glCompileShader(sh);
            let mut ok = 0i32;
            glGetShaderiv(sh, GL_COMPILE_STATUS, &mut ok);
            if ok == 0 {
                let log = info_log_shader(sh);
                glDeleteShader(sh);
                return Err(format!("着色器编译失败: {log}"));
            }
            Ok(sh)
        }
    }

    impl GlState {
        pub fn new(
            display: EGLDisplay,
            surface: EGLSurface,
            context: EGLContext,
            window: *mut c_void,
            es2: bool,
        ) -> Result<Self, String> {
            unsafe {
                eglMakeCurrent(display, surface, surface, context);
                let mut tex = 0u32;
                glGenTextures(1, &mut tex);
                glBindTexture(GL_TEXTURE_2D, tex);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE as i32);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE as i32);

                let (vs_src, fs_src) = if es2 { (VS_ES2, FS_ES2) } else { (VS_ES3, FS_ES3) };
                let vs = match compile(GL_VERTEX_SHADER, vs_src) {
                    Ok(s) => s,
                    Err(e) => {
                        glDeleteTextures(1, &tex);
                        return Err(e);
                    }
                };
                let fs = match compile(GL_FRAGMENT_SHADER, fs_src) {
                    Ok(s) => s,
                    Err(e) => {
                        glDeleteShader(vs);
                        glDeleteTextures(1, &tex);
                        return Err(e);
                    }
                };
                let prog = glCreateProgram();
                glAttachShader(prog, vs);
                glAttachShader(prog, fs);
                // 显式绑定属性位置，避免不同驱动分配 0/1 之外的位置。
                glBindAttribLocation(prog, 0, b"aPos\0".as_ptr() as *const i8);
                glBindAttribLocation(prog, 1, b"aUV\0".as_ptr() as *const i8);
                glLinkProgram(prog);
                let mut linked = 0i32;
                glGetProgramiv(prog, GL_LINK_STATUS, &mut linked);
                if linked == 0 {
                    let log = info_log_program(prog);
                    glDeleteShader(vs);
                    glDeleteShader(fs);
                    glDeleteProgram(prog);
                    glDeleteTextures(1, &tex);
                    return Err(format!("着色器链接失败: {log}"));
                }
                glDeleteShader(vs);
                glDeleteShader(fs);
                glUseProgram(prog);
                let loc = glGetUniformLocation(prog, b"uTex\0".as_ptr() as *const i8);
                glUniform1i(loc, 0);

                // x,y,u,v —— 两个三角形组成全屏四边形，注意 Y 翻转（帧缓冲左上原点）
                let verts: [f32; 16] = [
                    -1.0,  1.0, 0.0, 0.0,
                    -1.0, -1.0, 0.0, 1.0,
                     1.0,  1.0, 1.0, 0.0,
                     1.0, -1.0, 1.0, 1.0,
                ];
                let mut vbo = 0u32;
                glGenBuffers(1, &mut vbo);
                glBindBuffer(GL_ARRAY_BUFFER, vbo);
                glBufferData(GL_ARRAY_BUFFER, std::mem::size_of_val(&verts) as isize, verts.as_ptr() as *const _, GL_STATIC_DRAW);
                glEnableVertexAttribArray(0);
                glVertexAttribPointer(0, 2, GL_FLOAT, 0, 16, std::ptr::null());
                glEnableVertexAttribArray(1);
                glVertexAttribPointer(1, 2, GL_FLOAT, 0, 16, 8usize as *const _);

                // 初始化在主线程完成；主动解绑，让渲染线程独占该 context，
                // 避免同一 context 绑到两个线程导致后续线程 makeCurrent 行为异常。
                eglMakeCurrent(display, std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut());

                Ok(GlState {
                    display, surface, context, window, es2,
                    texture: tex, program: prog, vbo, tex_w: 0, tex_h: 0,
                })
            }
        }

        /// 物理窗口尺寸（旋转/分屏后随 Surface 变化）；取不到时退回帧缓冲尺寸。
        fn viewport_size(&self, fb_w: i32, fb_h: i32) -> (i32, i32) {
            if self.window.is_null() {
                return (fb_w, fb_h);
            }
            let (w, h) = unsafe { (ANativeWindow_getWidth(self.window), ANativeWindow_getHeight(self.window)) };
            if w > 0 && h > 0 { (w, h) } else { (fb_w, fb_h) }
        }

        pub fn draw(&mut self, pixels: &[u8], w: i32, h: i32) {
            unsafe {
                // surface/context/display 必须全部有效。此前 config 属性错误导致 surface
                // 一直为 null，eglMakeCurrent(d, null, null, ctx) 在部分驱动上仍返回 1
                // （surfaceless 绑定），随后 eglSwapBuffers(d, null) 每帧报 EGL_BAD_SURFACE。
                if self.display.is_null() || self.surface.is_null() || self.context.is_null() {
                    return;
                }
                // EGL 上下文是「线程本地」的：init 在 surfaceCreated 回调（主线程）里完成，
                // present() 在独立 RenderThread。渲染线程每帧先绑定上下文（开销极小）。
                if eglMakeCurrent(self.display, self.surface, self.surface, self.context) == 0 {
                    return; // 绑定失败（surface 已销毁），跳过本帧
                }
                let (vw, vh) = self.viewport_size(w, h);
                glViewport(0, 0, vw, vh);
                glClearColor(0.05, 0.06, 0.08, 1.0);
                glClear(GL_COLOR_BUFFER_BIT);
                glBindTexture(GL_TEXTURE_2D, self.texture);
                if self.tex_w != w || self.tex_h != h {
                    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA as i32, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels.as_ptr() as *const _);
                    self.tex_w = w;
                    self.tex_h = h;
                } else {
                    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, pixels.as_ptr() as *const _);
                }
                glUseProgram(self.program);
                glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
                if eglSwapBuffers(self.display, self.surface) == 0 {
                    // 限流：只在首次失败记日志，避免每帧刷屏淹没其他信息。
                    if !SWAP_ERR_LOGGED.swap(true, std::sync::atomic::Ordering::Relaxed) {
                        log_msg(LOG_INFO, &format!(
                            "eglSwapBuffers 失败 err=0x{:x} surf={:p}",
                            eglGetError(), self.surface
                        ));
                    }
                }
            }
        }
    }
}

/// 由 NativeActivity 的 ANativeWindow 创建 EGL 上下文并初始化 GL。
#[cfg(target_os = "android")]
pub fn init_from_window(window: *mut std::ffi::c_void) {
    use gles::*;
    // Surface 重建（旋转/切后台再回来）时先释放上一份 EGL/GL 资源，避免泄漏与残留状态。
    destroy();
    unsafe {
        let d = eglGetDisplay(0);
        let mut maj = 0;
        let mut min = 0;
        if eglInitialize(d, &mut maj, &mut min) == 0 {
            log_msg(LOG_INFO, &format!("EGL eglInitialize 失败 err=0x{:x}", eglGetError()));
            return;
        }

        // 关键修复：正确区分 EGL_RENDERABLE_TYPE(0x3040) 与 EGL_SURFACE_TYPE(0x3033)。
        // 旧代码用 0x3033 + EGL_OPENGL_ES3_BIT(0x40) 作为唯一属性，等于请求一个
        // 值为 0x40 的 surface type（非法），eglChooseConfig 选不到 window config，
        // 后续 eglCreateWindowSurface 返回 EGL_NO_SURFACE → 黑屏 + EGL_BAD_SURFACE。
        let attrs_es3 = [
            EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
            EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT,
            EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
            EGL_NONE,
        ];
        let mut cfg: EGLDisplay = std::ptr::null_mut();
        let mut n = 0;
        let mut es3 = true;
        if eglChooseConfig(d, attrs_es3.as_ptr(), &mut cfg, 1, &mut n) == 0 || n < 1 || cfg.is_null() {
            // 老设备可能完全没有 ES3 可渲染配置，退到 ES2 再选一次。
            log_msg(LOG_INFO, &format!("EGL ES3 配置不可用 err=0x{:x}，回退 ES2", eglGetError()));
            let attrs_es2 = [
                EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
                EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
                EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
                EGL_NONE,
            ];
            es3 = false;
            if eglChooseConfig(d, attrs_es2.as_ptr(), &mut cfg, 1, &mut n) == 0 || n < 1 || cfg.is_null() {
                log_msg(LOG_INFO, &format!("EGL eglChooseConfig 失败 n={n} err=0x{:x}", eglGetError()));
                return;
            }
        }

        let surf = eglCreateWindowSurface(d, cfg, window, std::ptr::null());
        if surf.is_null() {
            log_msg(LOG_INFO, &format!("EGL eglCreateWindowSurface 失败 err=0x{:x}", eglGetError()));
            return;
        }
        let ctx_attrs = [EGL_CONTEXT_CLIENT_VERSION, if es3 { 3 } else { 2 }, EGL_NONE];
        let mut ctx = eglCreateContext(d, cfg, std::ptr::null_mut(), ctx_attrs.as_ptr());
        let mut es2 = !es3;
        if ctx.is_null() && es3 {
            // ES3 上下文创建失败，回退 ES2（部分老驱动只支持 ES2）。
            es3 = false;
            let ctx_attrs2 = [EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE];
            ctx = eglCreateContext(d, cfg, std::ptr::null_mut(), ctx_attrs2.as_ptr());
            es2 = true;
            log_msg(LOG_INFO, "EGL ES3 context 失败，回退 ES2");
        }
        if ctx.is_null() {
            log_msg(LOG_INFO, &format!("EGL eglCreateContext 失败 err=0x{:x}", eglGetError()));
            eglDestroySurface(d, surf);
            return;
        }
        // 诊断：确认 surface/context/display 均有效后再初始化 GL 资源。
        log_msg(LOG_INFO, &format!(
            "EGL 就绪 d={:p} surf={:p} ctx={:p} es={}x{} gles={}",
            d, surf, ctx, maj, min, if es2 { 2 } else { 3 }
        ));
        match GlState::new(d, surf, ctx, window, es2) {
            Ok(gl) => {
                GL_STATE.lock().unwrap().replace(gl);
            }
            Err(e) => {
                // 着色器编译/链接失败必须显式暴露，不能静默黑屏。
                log_msg(LOG_INFO, &format!("GL 初始化失败: {e}"));
                eglMakeCurrent(d, std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut());
                eglDestroyContext(d, ctx);
                eglDestroySurface(d, surf);
            }
        }
    }
}

/// 释放 EGL/GL 资源（Surface 销毁、旋转重建、进程退出时调用）。
pub fn destroy() {
    #[cfg(target_os = "android")]
    {
        use gles::*;
        let old = GL_STATE.lock().ok().and_then(|mut g| g.take());
        if let Some(gl) = old {
            unsafe {
                eglMakeCurrent(gl.display, gl.surface, gl.surface, gl.context);
                glDeleteBuffers(1, &gl.vbo);
                glDeleteTextures(1, &gl.texture);
                glDeleteProgram(gl.program);
                eglMakeCurrent(gl.display, std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut());
                eglDestroySurface(gl.display, gl.surface);
                eglDestroyContext(gl.display, gl.context);
                eglTerminate(gl.display);
            }
            log_msg(LOG_INFO, "EGL/GL 资源已释放");
        }
    }
}

#[cfg(target_os = "android")]
static GL_STATE: std::sync::Mutex<Option<gles::GlState>> = std::sync::Mutex::new(None);

/// 提交一帧：把当前帧缓冲上传并绘制。
pub fn present() {
    let s = match crate::state::state().lock() {
        Ok(g) => g,
        Err(_) => return,
    };
    #[cfg(target_os = "android")]
    {
        if let Some(gl) = GL_STATE.lock().unwrap().as_mut() {
            gl.draw(&s.framebuffer, s.width, s.height);
        }
    }
    #[cfg(not(target_os = "android"))]
    {
        // 本机验证：仅记录帧提交，不落盘（避免高频 IO）。
        let _ = (&s.framebuffer, s.width, s.height);
    }
}

pub fn init_logging() {
    #[cfg(target_os = "android")]
    {
        let _ = android_logger::init_once(
            android_logger::Config::default()
                .with_max_level(log::LevelFilter::Info)
                .with_tag("vapor"),
        );
    }
    let _ = (DEFAULT_W, DEFAULT_H, log_msg, LOG_INFO, std::mem::size_of::<State>());
}
