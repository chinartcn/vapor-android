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

    pub type EGLDisplay = *mut c_void;
    pub type EGLSurface = *mut c_void;
    pub type EGLContext = *mut c_void;

    #[link(name = "EGL")]
    extern "C" {
        pub fn eglGetDisplay(d: i32) -> EGLDisplay;
        pub fn eglInitialize(d: EGLDisplay, major: *mut i32, minor: *mut i32) -> i32;
        pub fn eglChooseConfig(d: EGLDisplay, attrs: *const i32, out: *mut EGLDisplay, n: i32, num: *mut i32) -> i32;
        pub fn eglCreateWindowSurface(d: EGLDisplay, cfg: EGLDisplay, win: *mut c_void, attrs: *const i32) -> EGLSurface;
        pub fn eglCreateContext(d: EGLDisplay, cfg: EGLDisplay, share: EGLContext, attrs: *const i32) -> EGLContext;
        pub fn eglMakeCurrent(d: EGLDisplay, draw: EGLSurface, read: EGLSurface, ctx: EGLContext) -> i32;
        pub fn eglSwapBuffers(d: EGLDisplay, s: EGLSurface) -> i32;
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
    pub const EGL_OPENGL_ES3_BIT: i32 = 0x00000040;

    pub struct GlState {
        pub display: EGLDisplay,
        pub surface: EGLSurface,
        pub context: EGLContext,
        pub texture: u32,
        pub program: u32,
        pub vbo: u32,
        pub tex_w: i32,
        pub tex_h: i32,
    }

    const VS: &str = "#version 300 es\n\
        in vec2 aPos;\n\
        in vec2 aUV;\n\
        out vec2 vUV;\n\
        void main(){ vUV=aUV; gl_Position=vec4(aPos,0.0,1.0); }";

    const FS: &str = "#version 300 es\n\
        precision mediump float;\n\
        in vec2 vUV;\n\
        uniform sampler2D uTex;\n\
        out vec4 frag;\n\
        void main(){ frag=texture(uTex, vUV); }";

    fn compile(ty: u32, src: &str) -> u32 {
        unsafe {
            let sh = glCreateShader(ty);
            let p = src.as_ptr() as *const i8;
            glShaderSource(sh, 1, &p, std::ptr::null());
            glCompileShader(sh);
            sh
        }
    }

    impl GlState {
        pub fn new(display: EGLDisplay, surface: EGLSurface, context: EGLContext) -> Self {
            unsafe {
                eglMakeCurrent(display, surface, surface, context);
                let mut tex = 0u32;
                glGenTextures(1, &mut tex);
                glBindTexture(GL_TEXTURE_2D, tex);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE as i32);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE as i32);

                let vs = compile(GL_VERTEX_SHADER, VS);
                let fs = compile(GL_FRAGMENT_SHADER, FS);
                let prog = glCreateProgram();
                glAttachShader(prog, vs);
                glAttachShader(prog, fs);
                glLinkProgram(prog);
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

                GlState { display, surface, context, texture: tex, program: prog, vbo, tex_w: 0, tex_h: 0 }
            }
        }

        pub fn draw(&mut self, pixels: &[u8], w: i32, h: i32) {
            unsafe {
                glViewport(0, 0, w, h);
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
                eglSwapBuffers(self.display, self.surface);
            }
        }
    }
}

/// 由 NativeActivity 的 ANativeWindow 创建 EGL 上下文并初始化 GL。
#[cfg(target_os = "android")]
pub fn init_from_window(window: *mut std::ffi::c_void) {
    use gles::*;
    unsafe {
        let d = eglGetDisplay(0);
        let mut maj = 0;
        let mut min = 0;
        eglInitialize(d, &mut maj, &mut min);
        let attrs = [
            0x3033, EGL_OPENGL_ES3_BIT, // EGL_RENDERABLE_TYPE
            0x3024, 8, 0x3023, 8, 0x3022, 8, 0x3021, 8, 0x3038,
        ];
        let mut cfg: EGLDisplay = std::ptr::null_mut();
        let mut n = 0;
        eglChooseConfig(d, attrs.as_ptr(), &mut cfg, 1, &mut n);
        let surf = eglCreateWindowSurface(d, cfg, window, std::ptr::null());
        let ctx_attrs = [0x3098, 3, 0x3038]; // EGL_CONTEXT_CLIENT_VERSION=3
        let ctx = eglCreateContext(d, cfg, std::ptr::null_mut(), ctx_attrs.as_ptr());
        let gl = GlState::new(d, surf, ctx);
        GL_STATE.lock().unwrap().replace(gl);
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
