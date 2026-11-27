//! 轻量二进制 AXML（AndroidManifest.xml）修改器。
//!
//! Android APK 里的 AndroidManifest.xml 是二进制 XML。要改包名/版本名/应用名，
//! 本质是改其 string pool 中的字符串；versionCode 是属性 TypedValue 里的整数。
//! 本模块：
//!   1. 解析出 string pool，按原索引顺序替换字符串（索引不变 → XML 节点引用不失效）；
//!   2. 定位 <manifest> 的 android:versionCode 属性并改整数值；
//!   3. 重建 chunk 大小与偏移，序列化回合法 AXML。
//! 图标通过替换 res/mipmap*/ic_launcher*.png 实现（资源 ID 不变）。

use std::collections::HashMap;

const RES_STRING_POOL_TYPE: u16 = 0x0001;
const RES_XML_START_ELEMENT_TYPE: u16 = 0x0102;
const UTF8_FLAG: u32 = 0x00000100;
const TYPE_INT_DEC: u8 = 0x10;

fn u16at(b: &[u8], p: usize) -> u16 {
    u16::from_le_bytes([b[p], b[p + 1]])
}
fn u32at(b: &[u8], p: usize) -> u32 {
    u32::from_le_bytes([b[p], b[p + 1], b[p + 2], b[p + 3]])
}
fn put_u16(b: &mut [u8], p: usize, v: u16) {
    b[p..p + 2].copy_from_slice(&v.to_le_bytes());
}
fn put_u32(b: &mut [u8], p: usize, v: u32) {
    b[p..p + 4].copy_from_slice(&v.to_le_bytes());
}

fn decode_len8(b: &[u8], p: &mut usize) -> usize {
    let first = b[*p] as usize;
    *p += 1;
    if first & 0x80 != 0 {
        let v = ((first & 0x7F) << 8) | (b[*p] as usize);
        *p += 1;
        v
    } else {
        first
    }
}

fn encode_len8(len: usize, out: &mut Vec<u8>) {
    if len > 0x7F {
        out.push(0x80 | ((len >> 8) & 0x7F) as u8);
    }
    out.push((len & 0xFF) as u8);
}

#[derive(Clone, Debug)]
enum Str {
    Utf8(String),
    Utf16(String),
}

impl Str {
    fn as_str(&self) -> &str {
        match self {
            Str::Utf8(s) | Str::Utf16(s) => s,
        }
    }
}

/// 解析 string pool 的字符串列表。
fn parse_strings(chunk: &[u8]) -> Vec<Str> {
    let header_size = u16at(chunk, 2) as usize;
    let string_count = u32at(chunk, 8) as usize;
    let flags = u32at(chunk, 16);
    let strings_start = u32at(chunk, 20) as usize;
    let utf8 = flags & UTF8_FLAG != 0;
    let mut out = Vec::with_capacity(string_count);
    for i in 0..string_count {
        let off = u32at(chunk, header_size + i * 4) as usize;
        let mut p = strings_start + off;
        if p >= chunk.len() {
            out.push(if utf8 { Str::Utf8(String::new()) } else { Str::Utf16(String::new()) });
            continue;
        }
        if utf8 {
            let _char_len = decode_len8(chunk, &mut p);
            let byte_len = decode_len8(chunk, &mut p);
            let s = String::from_utf8_lossy(&chunk[p..p + byte_len]).into_owned();
            out.push(Str::Utf8(s));
        } else {
            let mut len = u16at(chunk, p) as usize;
            p += 2;
            if len & 0x8000 != 0 {
                len = ((len & 0x7FFF) << 16) | (u16at(chunk, p) as usize);
                p += 2;
            }
            let units: Vec<u16> = (0..len).map(|k| u16at(chunk, p + k * 2)).collect();
            out.push(Str::Utf16(String::from_utf16_lossy(&units)));
        }
    }
    out
}

fn encode_string(s: &Str, utf8: bool, out: &mut Vec<u8>) {
    if utf8 {
        let bytes = s.as_str().as_bytes();
        encode_len8(s.as_str().chars().count(), out);
        encode_len8(bytes.len(), out);
        out.extend_from_slice(bytes);
        out.push(0);
    } else {
        let units: Vec<u16> = s.as_str().encode_utf16().collect();
        let _ = s;
        let len = units.len();
        if len > 0x7FFF {
            out.extend_from_slice(&(0x8000u16 | ((len >> 16) as u16)).to_le_bytes());
            out.extend_from_slice(&(len as u16).to_le_bytes());
        } else {
            out.extend_from_slice(&(len as u16).to_le_bytes());
        }
        for u in units {
            out.extend_from_slice(&u.to_le_bytes());
        }
        out.extend_from_slice(&[0, 0]);
    }
    let _ = utf8;
}

/// 重建 string pool chunk，保持字符串索引与顺序不变。
fn rebuild_pool(chunk: &[u8], strings: &[Str]) -> Vec<u8> {
    let header_size = u16at(chunk, 2) as usize;
    let string_count = strings.len();
    let style_count = u32at(chunk, 12) as usize;
    let flags = u32at(chunk, 16);
    let utf8 = flags & UTF8_FLAG != 0;
    let old_strings_start = u32at(chunk, 20) as usize;
    let old_size = u32at(chunk, 4) as usize;

    // 保留原 style 数据（若有）
    let old_offsets_end = header_size + string_count * 4;
    let old_styles_start = u32at(chunk, 24) as usize;
    let style_bytes: Vec<u8> = if style_count > 0 && old_styles_start >= old_strings_start {
        let start = old_styles_start.min(old_size);
        chunk[start..old_size].to_vec()
    } else {
        // 兼容 style 数据紧随字符串区但 styleCount=0 的情形：不保留。
        let _ = old_offsets_end;
        Vec::new()
    };

    let strings_start = header_size + string_count * 4;
    let mut data = Vec::new();
    let mut offsets = Vec::with_capacity(string_count);
    for s in strings {
        offsets.push(data.len() as u32);
        encode_string(s, utf8, &mut data);
    }
    let new_size = strings_start + data.len() + style_bytes.len();
    let mut out = vec![0u8; new_size];
    put_u16(&mut out, 0, RES_STRING_POOL_TYPE);
    put_u16(&mut out, 2, header_size as u16);
    put_u32(&mut out, 4, new_size as u32);
    put_u32(&mut out, 8, string_count as u32);
    put_u32(&mut out, 12, style_count as u32);
    put_u32(&mut out, 16, flags);
    put_u32(&mut out, 20, strings_start as u32);
    put_u32(&mut out, 24, (strings_start + data.len()) as u32);
    for (i, off) in offsets.iter().enumerate() {
        put_u32(&mut out, header_size + i * 4, *off);
    }
    out[strings_start..strings_start + data.len()].copy_from_slice(&data);
    if !style_bytes.is_empty() {
        let s = strings_start + data.len();
        out[s..s + style_bytes.len()].copy_from_slice(&style_bytes);
    }
    out
}

/// 遍历 AXML 的 START_ELEMENT 属性，修改 android:versionCode。
fn patch_version_code(chunk: &mut [u8], strings: &[Str], manifest_name_idx: usize, new_code: u32) {
    let mut p = 8usize; // 跳过 RES_XML_TYPE header
    let total = u32at(chunk, 4) as usize;
    while p + 8 <= total {
        let ty = u16at(chunk, p);
        let header_size = u16at(chunk, p + 2) as usize;
        let size = u32at(chunk, p + 4) as usize;
        if size < 8 || p + size > total {
            break;
        }
        if ty == RES_XML_START_ELEMENT_TYPE && size >= 36 {
            // ResXMLTree_attrExt 从 p+header_size 开始
            let a = p + header_size;
            let name_idx = u32at(chunk, a + 4) as usize;
            let attr_start = u16at(chunk, a + 8) as usize;
            let attr_size = u16at(chunk, a + 10) as usize;
            let attr_count = u16at(chunk, a + 12) as usize;
            if name_idx == manifest_name_idx && attr_size >= 20 {
                for i in 0..attr_count {
                    let ap = a + attr_start + i * attr_size;
                    if ap + 20 > p + size {
                        break;
                    }
                    let aname_idx = u32at(chunk, ap + 4) as usize;
                    let aname = strings.get(aname_idx).map(|s| s.as_str()).unwrap_or("");
                    if aname == "versionCode" {
                        // Res_value(typedValue) 在 attr 内偏移 12:
                        //   size(u16)@+12, res0(u8)@+14, dataType(u8)@+15, data(u32)@+16
                        put_u16(chunk, ap + 12, 8);
                        chunk[ap + 15] = TYPE_INT_DEC;
                        put_u32(chunk, ap + 16, new_code);
                    }
                }
            }
        }
        p += size;
    }
}

/// 修改 AndroidManifest.xml。
/// replacements: 原字符串 → 新字符串（如 "com.vapor.shell" → "com.acme.game"）。
pub fn patch_manifest(
    axml: &[u8],
    replacements: &HashMap<String, String>,
    version_code: Option<u32>,
) -> Result<Vec<u8>, String> {
    if axml.len() < 8 || u16at(axml, 0) != 0x0003 {
        // 明文 XML 回退（便于本机验证/工具链产出的未编译 manifest）
        let text = String::from_utf8_lossy(axml).to_string();
        let mut out = text;
        for (k, v) in replacements {
            out = out.replace(k.as_str(), v.as_str());
        }
        if let Some(code) = version_code {
            out = replace_attr(&out, "android:versionCode", &code.to_string());
        }
        return Ok(out.into_bytes());
    }
    // 找到 string pool chunk
    let total = u32at(axml, 4) as usize;
    let mut pool_pos = None;
    let mut p = 8usize;
    while p + 8 <= total {
        let ty = u16at(axml, p);
        let size = u32at(axml, p + 4) as usize;
        if size < 8 || p + size > total {
            break;
        }
        if ty == RES_STRING_POOL_TYPE {
            pool_pos = Some((p, size));
            break;
        }
        p += size;
    }
    let (pool_p, pool_size) = pool_pos.ok_or("未找到 string pool")?;
    let mut strings = parse_strings(&axml[pool_p..pool_p + pool_size]);

    // 找到 "manifest" 元素名索引（用于 versionCode 定位）
    let manifest_idx = strings.iter().position(|s| s.as_str() == "manifest").unwrap_or(usize::MAX);

    for s in strings.iter_mut() {
        if let Some(new) = replacements.get(s.as_str()) {
            *s = match s {
                Str::Utf8(_) => Str::Utf8(new.clone()),
                Str::Utf16(_) => Str::Utf16(new.clone()),
            };
        }
    }

    let new_pool = rebuild_pool(&axml[pool_p..pool_p + pool_size], &strings);
    let delta = new_pool.len() as i64 - pool_size as i64;
    let mut out = Vec::with_capacity((total as i64 + delta) as usize);
    out.extend_from_slice(&axml[..pool_p]);
    out.extend_from_slice(&new_pool);
    out.extend_from_slice(&axml[pool_p + pool_size..]);

    // 更新 RES_XML_TYPE 总大小
    let new_total = (total as i64 + delta) as u32;
    put_u32(&mut out, 4, new_total);

    if let Some(code) = version_code {
        if manifest_idx != usize::MAX {
            patch_version_code(&mut out, &strings, manifest_idx, code);
        }
    }
    Ok(out)
}

/// 明文 XML 属性替换：`android:versionCode="x"` → `android:versionCode="new"`。
fn replace_attr(xml: &str, attr: &str, new: &str) -> String {
    let mut out = xml.to_string();
    if let Some(start) = out.find(attr) {
        if let Some(eq) = out[start..].find('=') {
            let eq = start + eq + 1;
            let rest = &out[eq..];
            let quote = rest.trim_start().chars().next().unwrap_or('"');
            let qpos = eq + rest.find(quote).unwrap_or(0);
            let after_q = qpos + 1;
            if let Some(end_q) = out[after_q..].find(quote) {
                let end = after_q + end_q;
                out.replace_range(after_q..end, new);
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn len_roundtrip() {
        // UTF-8 AXML 长度字段最多 2 字节（0x7FFF=32767）
        for n in [0usize, 1, 127, 128, 300, 32767] {
            let mut v = Vec::new();
            encode_len8(n, &mut v);
            let mut p = 0;
            assert_eq!(decode_len8(&v, &mut p), n);
        }
    }

    /// 构造一个最小但合法的二进制 AXML：RES_XML_TYPE 头 + string pool +
    /// START_ELEMENT(<manifest android:versionCode=1>)。用于验证二进制路径。
    fn make_minimal_axml() -> Vec<u8> {
        let strings = ["manifest", "versionCode", "com.vapor.shell", "1.0", "Vapor Shell"];
        // --- string pool (UTF-8) ---
        let header_size = 28usize;
        let strings_start = header_size + strings.len() * 4;
        let mut data = Vec::new();
        let mut offsets = Vec::new();
        for s in &strings {
            offsets.push(data.len() as u32);
            encode_len8(s.chars().count(), &mut data);
            encode_len8(s.len(), &mut data);
            data.extend_from_slice(s.as_bytes());
            data.push(0);
        }
        let pool_size = strings_start + data.len();
        let mut pool = vec![0u8; pool_size];
        put_u16(&mut pool, 0, RES_STRING_POOL_TYPE);
        put_u16(&mut pool, 2, header_size as u16);
        put_u32(&mut pool, 4, pool_size as u32);
        put_u32(&mut pool, 8, strings.len() as u32); // stringCount
        put_u32(&mut pool, 12, 0);                   // styleCount
        put_u32(&mut pool, 16, UTF8_FLAG);
        put_u32(&mut pool, 20, strings_start as u32);
        put_u32(&mut pool, 24, 0);                   // stylesStart = 0 (无 style)
        for (i, off) in offsets.iter().enumerate() {
            put_u32(&mut pool, header_size + i * 4, *off);
        }
        pool[strings_start..].copy_from_slice(&data);

        // --- START_ELEMENT <manifest> with one attr versionCode=1 ---
        // node header(16) + attrExt(20) + attr(20)
        let elem_size = 16 + 20 + 20;
        let mut elem = vec![0u8; elem_size];
        put_u16(&mut elem, 0, RES_XML_START_ELEMENT_TYPE);
        put_u16(&mut elem, 2, 16); // node headerSize
        put_u32(&mut elem, 4, elem_size as u32);
        put_u32(&mut elem, 16, 0xffffffff); // ns = -1 (no namespace)
        put_u32(&mut elem, 20, 0);          // name = "manifest" (idx 0)
        put_u16(&mut elem, 24, 20);         // attributeStart (相对 attrExt 起点)
        put_u16(&mut elem, 26, 20);         // attributeSize
        put_u16(&mut elem, 28, 1);          // attributeCount
        // attr[0]: name = "versionCode" (idx 1), typedValue int 1
        let ap = 16 + 20;
        put_u32(&mut elem, ap + 4, 1);      // attr name idx
        put_u16(&mut elem, ap + 12, 8);     // typedValue size
        elem[ap + 15] = TYPE_INT_DEC;
        put_u32(&mut elem, ap + 16, 1);     // versionCode = 1

        // --- RES_XML_TYPE 总头 ---
        let total = 8 + pool_size + elem_size;
        let mut axml = vec![0u8; total];
        put_u16(&mut axml, 0, 0x0003);
        put_u16(&mut axml, 2, 8);
        put_u32(&mut axml, 4, total as u32);
        axml[8..8 + pool_size].copy_from_slice(&pool);
        axml[8 + pool_size..].copy_from_slice(&elem);
        axml
    }

    #[test]
    fn patch_binary_axml_strings_and_version() {
        let axml = make_minimal_axml();
        let mut reps = HashMap::new();
        reps.insert("com.vapor.shell".to_string(), "com.acme.game".to_string());
        reps.insert("Vapor Shell".to_string(), "我的游戏".to_string());
        let patched = patch_manifest(&axml, &reps, Some(42)).unwrap();

        // 解析回字符串，确认替换生效
        // 找到 string pool
        let pool_size = u32at(&patched, 8 + 4) as usize;
        let strings = parse_strings(&patched[8..8 + pool_size]);
        let vals: Vec<&str> = strings.iter().map(|s| s.as_str()).collect();
        assert!(vals.contains(&"com.acme.game"), "包名未替换: {vals:?}");
        assert!(vals.contains(&"我的游戏"), "应用名未替换: {vals:?}");
        assert!(!vals.contains(&"com.vapor.shell"), "旧包名仍存在");
        assert_eq!(u32at(&patched, 4) as usize, patched.len(), "AXML 总大小未更新");

        // 验证 versionCode 被改为 42（typedValue.data 在 attr 内偏移 16）
        let elem_start = 8 + pool_size;
        let ap = elem_start + 16 + 20;
        assert_eq!(u32at(&patched, ap + 16), 42, "versionCode 未修改");
    }
}
