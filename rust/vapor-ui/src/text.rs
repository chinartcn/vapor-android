//! 文本度量：字宽估算 + 按行折行（中英混排）。
//!
//! 不做字形光栅化（那由 rust.Vapor 的位图字体负责），只做排版所需的度量，
//! 保证布局层可以在没有字体文件的情况下算出行高与折行点。

use pyo3::prelude::*;

/// 单个字符的宽度（以字号 size 为 1em 的倍数）。
fn char_em(c: char) -> f64 {
    let cp = c as u32;
    // CJK / 全角标点 / emoji：按 1em
    if cp >= 0x2E80 {
        return 1.0;
    }
    match c {
        ' ' => 0.28,
        'i' | 'l' | 'j' | '!' | '|' | '.' | ',' | '\'' | ';' | ':' => 0.28,
        'f' | 't' | 'r' | '(' | ')' | '[' | ']' | '{' | '}' | '/' | '\\' | '"' => 0.36,
        'm' | 'w' | 'M' | 'W' => 0.86,
        'A'..='Z' => 0.66,
        'a'..='z' => 0.55,
        '0'..='9' => 0.56,
        _ => 0.55,
    }
}

#[pyfunction]
#[pyo3(signature = (text, size, letter_spacing=0.0))]
pub fn text_width(text: &str, size: f64, letter_spacing: f64) -> f64 {
    let mut w = 0.0;
    let mut n = 0usize;
    for c in text.chars() {
        if c == '\n' {
            continue;
        }
        w += char_em(c) * size;
        n += 1;
    }
    w + letter_spacing * (n.saturating_sub(1)) as f64
}

#[pyfunction]
pub fn line_height(size: f64) -> f64 {
    size * 1.3
}

/// 贪心折行：英文按单词折，CJK 可按字符折，保留原有换行。
#[pyfunction]
#[pyo3(signature = (text, size, max_width, letter_spacing=0.0))]
pub fn wrap_text(text: &str, size: f64, max_width: f64, letter_spacing: f64) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    if max_width <= 0.0 {
        lines.push(text.to_string());
        return lines;
    }
    for paragraph in text.split('\n') {
        if paragraph.is_empty() {
            lines.push(String::new());
            continue;
        }
        let mut line = String::new();
        let mut width = 0.0f64;
        // 逐「块」处理：CJK 单字成块，拉丁词整体成块
        let chars: Vec<char> = paragraph.chars().collect();
        let mut i = 0usize;
        while i < chars.len() {
            let c = chars[i];
            let cjk = (c as u32) >= 0x2E80;
            let mut token = String::new();
            if cjk || c == ' ' {
                token.push(c);
                i += 1;
            } else {
                while i < chars.len() {
                    let ch = chars[i];
                    if ch == ' ' || (ch as u32) >= 0x2E80 {
                        break;
                    }
                    token.push(ch);
                    i += 1;
                }
            }
            let tw = text_width(&token, size, letter_spacing);
            if width > 0.0 && width + tw > max_width {
                lines.push(line.trim_end().to_string());
                line.clear();
                width = 0.0;
                if token == " " {
                    continue;
                }
            }
            line.push_str(&token);
            width += tw;
        }
        lines.push(line.trim_end().to_string());
    }
    lines
}

#[pyfunction]
pub fn ellipsize(text: &str, size: f64, max_width: f64, suffix: &str) -> String {
    if text_width(text, size, 0.0) <= max_width {
        return text.to_string();
    }
    let suffix_w = text_width(suffix, size, 0.0);
    let mut out = String::new();
    let mut w = 0.0;
    for c in text.chars() {
        let cw = char_em(c) * size;
        if w + cw + suffix_w > max_width {
            break;
        }
        out.push(c);
        w += cw;
    }
    out.push_str(suffix);
    out
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(text_width, m)?)?;
    m.add_function(wrap_pyfunction!(line_height, m)?)?;
    m.add_function(wrap_pyfunction!(wrap_text, m)?)?;
    m.add_function(wrap_pyfunction!(ellipsize, m)?)?;
    Ok(())
}
