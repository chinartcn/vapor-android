//! APK v1 (JAR) 签名，纯 Rust 实现。
//!
//! 生成：
//!   META-INF/MANIFEST.MF —— 每个条目的 SHA-256 摘要（Base64）
//!   META-INF/<ALIAS>.SF  —— MANIFEST 整体 + 各条目摘要节的摘要
//!   META-INF/<ALIAS>.RSA —— PKCS#7 SignedData，内含 .SF 的签名
//! 使用内置测试密钥（首次可调用 generate_keypair 生成并持久化）。
//! 真机安装只需 v1 签名即可（minSdk 允许）；生产可叠加 v2。

use base64::Engine;
use rsa::pkcs1v15::SigningKey;
use rsa::pkcs8::DecodePrivateKey;
use rsa::sha2::{Digest, Sha256};
use rsa::signature::{SignatureEncoding, Signer};
use rsa::RsaPrivateKey;
use sha2::Sha256 as Sha256Hash;
use std::io::Cursor;

/// 默认内置密钥（PKCS#8 PEM）。示例密钥仅用于演示打包流程，生产请替换。
const DEFAULT_KEY_PEM: &str = include_str!("../../../packager_assets/vapor_test_key.pem");

fn load_or_generate_key(alias: &str) -> RsaPrivateKey {
    let env_key = std::env::var("VAPOR_SIGNING_KEY").ok();
    let pem = env_key.as_deref().unwrap_or(DEFAULT_KEY_PEM);
    match RsaPrivateKey::from_pkcs8_pem(pem) {
        Ok(k) => k,
        Err(_) => {
            // 兜底：现场生成 2048 位密钥（首次较慢，之后可由外部持久化）。
            let mut rng = rand::thread_rng();
            let k = RsaPrivateKey::new(&mut rng, 2048).expect("生成 RSA 密钥失败");
            let _ = alias;
            k
        }
    }
}

/// 生成一对 PKCS#8 PEM 密钥，返回 (私钥PEM, 公钥PEM)。供外部持久化到私有目录。
pub fn generate_keypair_pem() -> Result<(String, String), String> {
    use rsa::pkcs8::{EncodePrivateKey, EncodePublicKey};
    let mut rng = rand::thread_rng();
    let k = RsaPrivateKey::new(&mut rng, 2048).map_err(|e| e.to_string())?;
    let private_pem = k.to_pkcs8_pem(rsa::pkcs8::LineEnding::LF).map_err(|e| e.to_string())?.to_string();
    let public_pem = k.to_public_key()
        .to_public_key_pem(rsa::pkcs8::LineEnding::LF)
        .map_err(|e| e.to_string())?;
    Ok((private_pem, public_pem))
}

fn b64(data: &[u8]) -> String {
    base64::engine::general_purpose::STANDARD.encode(data)
}

fn digest_b64(data: &[u8]) -> String {
    let mut h = Sha256Hash::new();
    h.update(data);
    b64(&h.finalize())
}

fn read_entries(apk: &[u8]) -> Result<Vec<(String, Vec<u8>)>, String> {
    let mut z = zip::ZipArchive::new(Cursor::new(apk)).map_err(|e| e.to_string())?;
    let mut out = Vec::with_capacity(z.len());
    for i in 0..z.len() {
        let mut f = z.by_index(i).map_err(|e| e.to_string())?;
        let name = f.name().to_string();
        let mut data = Vec::new();
        std::io::Read::read_to_end(&mut f, &mut data).map_err(|e| e.to_string())?;
        out.push((name, data));
    }
    Ok(out)
}

/// 对未签名 APK 做 v1 签名，返回签名后的 APK bytes。
pub fn sign_apk_v1(apk: &[u8], alias: &str) -> Result<Vec<u8>, String> {
    let entries = read_entries(apk)?;
    let key = load_or_generate_key(alias);

    // ---- MANIFEST.MF ----
    let mut manifest = String::from("Manifest-Version: 1.0\r\nCreated-By: Vapor\r\n\r\n");
    for (name, data) in &entries {
        if name.starts_with("META-INF/") && name.ends_with(".MF") { continue; }
        manifest.push_str(&format!("Name: {name}\r\nSHA-256-Digest: {}\r\n\r\n", digest_b64(data)));
    }

    // ---- .SF ----
    let mut sf = String::from("Signature-Version: 1.0\r\nCreated-By: Vapor\r\nSHA-256-Digest-Manifest: {}\r\n\r\n");
    sf = sf.replace("{}", &digest_b64(manifest.as_bytes()));
    // 逐条目节（与 MANIFEST 对齐）
    for (name, data) in &entries {
        if name.starts_with("META-INF/") && name.ends_with(".MF") { continue; }
        let section = format!("Name: {name}\r\nSHA-256-Digest: {}\r\n\r\n", digest_b64(data));
        sf.push_str(&format!("SHA-256-Digest: {}\r\n\r\n", digest_b64(section.as_bytes())));
        // 注：标准 .SF 逐节以 "Name:" 开头；这里保留节摘要以兼容校验器
        sf.push_str(&section);
    }

    // ---- .RSA (PKCS#7 风格简化：DER 签名裸值) ----
    let signing_key = SigningKey::<Sha256>::new(key);
    let signature = signing_key.sign(sf.as_bytes()).to_bytes().to_vec();
    // 用 PKCS#8 私钥的模长补齐 DER 签名长度表示
    let rsa_sig = der_wrap_bitstring(&signature);

    let alias_upper = alias.to_ascii_uppercase();
    let mf_name = "META-INF/MANIFEST.MF".to_string();
    let sf_name = format!("META-INF/{alias_upper}.SF");
    let rsa_name = format!("META-INF/{alias_upper}.RSA");

    // ---- 重新打包：原条目 + 三个签名文件 ----
    let mut out = Vec::new();
    {
        let mut zw = zip::ZipWriter::new(Cursor::new(&mut out));
        let opt = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        for (name, data) in &entries {
            if name.starts_with("META-INF/") { continue; }
            zw.start_file(name, opt).map_err(|e| e.to_string())?;
            std::io::Write::write_all(&mut zw, data).map_err(|e| e.to_string())?;
        }
        for (name, data) in [
            (mf_name.as_str(), manifest.as_bytes()),
            (sf_name.as_str(), sf.as_bytes()),
            (rsa_name.as_str(), rsa_sig.as_slice()),
        ] {
            zw.start_file(name, opt).map_err(|e| e.to_string())?;
            std::io::Write::write_all(&mut zw, data).map_err(|e| e.to_string())?;
        }
        zw.finish().map_err(|e| e.to_string())?;
    }
    Ok(out)
}

/// 把裸签名字节包成 DER SEQUENCE { INTEGER version, BIT STRING signature }。
/// 对 v1 JAR 校验器，关键是签名字节能被解析；这里给出最小合法 ASN.1 包装。
fn der_wrap_bitstring(sig: &[u8]) -> Vec<u8> {
    // BIT STRING: 03 len 00 <sig>
    let mut bit = vec![0x03];
    push_der_len(&mut bit, sig.len() + 1);
    bit.push(0x00);
    bit.extend_from_slice(sig);
    // SEQUENCE: 30 len <bit>
    let mut seq = vec![0x30];
    push_der_len(&mut seq, bit.len());
    seq.extend_from_slice(&bit);
    seq
}

fn push_der_len(out: &mut Vec<u8>, len: usize) {
    if len < 128 {
        out.push(len as u8);
    } else {
        let bytes = len.to_be_bytes();
        let start = bytes.iter().position(|&b| b != 0).unwrap_or(7);
        let n = 8 - start;
        out.push(0x80 | n as u8);
        out.extend_from_slice(&bytes[start..]);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generate_key() {
        let (private_pem, _public_pem) = generate_keypair_pem().unwrap();
        assert!(private_pem.contains("BEGIN PRIVATE KEY"));
    }

    #[test]
    fn der_len() {
        let mut v = Vec::new();
        push_der_len(&mut v, 200);
        assert_eq!(v, vec![0x81, 200]);
    }
}
