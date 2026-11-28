//! APK v1 (JAR) 签名，纯 Rust 实现。
//!
//! 生成：
//!   META-INF/MANIFEST.MF —— 每个条目的 SHA-256 摘要（Base64）
//!   META-INF/<ALIAS>.SF  —— MANIFEST 整体 + 各条目摘要节的摘要
//!   META-INF/<ALIAS>.RSA —— 标准 PKCS#7 SignedData（含自签名 X.509 证书）
//! 使用内置测试密钥（首次可调用 generate_keypair 生成并持久化）。

use base64::Engine;
use rsa::pkcs1v15::SigningKey;
use rsa::pkcs8::DecodePrivateKey;
use rsa::sha2::{Digest, Sha256};
use rsa::signature::{SignatureEncoding, Signer};
use rsa::traits::PublicKeyParts;
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

/// JAR 签名相关条目（META-INF 下的签名文件与清单）不参与再签名。
fn is_signature_entry(name: &str) -> bool {
    name.ends_with('/') || name.starts_with("META-INF/")
}

/// 对未签名 APK 做 v1 签名，返回签名后的 APK bytes。
pub fn sign_apk_v1(apk: &[u8], alias: &str) -> Result<Vec<u8>, String> {
    let entries = read_entries(apk)?;
    let key = load_or_generate_key(alias);

    // ---- MANIFEST.MF —— 逐条目节（每节以 Name: 开头） ----
    let mut manifest = String::from("Manifest-Version: 1.0\r\nCreated-By: Vapor\r\n\r\n");
    let mut sections: Vec<String> = Vec::new();
    for (name, data) in &entries {
        if is_signature_entry(name) {
            continue;
        }
        let section = format!("Name: {name}\r\nSHA-256-Digest: {}\r\n\r\n", digest_b64(data));
        manifest.push_str(&section);
        sections.push(section);
    }

    // ---- .SF —— 整体摘要 + 每个 MANIFEST 节的摘要（不是原始文件摘要） ----
    let mut sf = format!(
        "Signature-Version: 1.0\r\nCreated-By: Vapor\r\nSHA-256-Digest-Manifest: {}\r\n\r\n",
        digest_b64(manifest.as_bytes())
    );
    for (name, data) in &entries {
        if is_signature_entry(name) {
            continue;
        }
        let section = format!("Name: {name}\r\nSHA-256-Digest: {}\r\n\r\n", digest_b64(data));
        sf.push_str(&format!(
            "Name: {name}\r\nSHA-256-Digest: {}\r\n\r\n",
            digest_b64(section.as_bytes())
        ));
    }

    // ---- .RSA —— 标准 PKCS#7 SignedData（自签名证书 + 对 .SF 的 RSA 签名） ----
    let rsa_block = build_pkcs7(&key, sf.as_bytes())?;

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
            (rsa_name.as_str(), rsa_block.as_slice()),
        ] {
            zw.start_file(name, opt).map_err(|e| e.to_string())?;
            std::io::Write::write_all(&mut zw, data).map_err(|e| e.to_string())?;
        }
        zw.finish().map_err(|e| e.to_string())?;
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// 最小 DER 编码器（只用于构造 X.509 证书与 PKCS#7 SignedData）
// ---------------------------------------------------------------------------

const OID_DATA: &[u8] = &[0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x07, 0x01];
const OID_SIGNED_DATA: &[u8] = &[0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x07, 0x02];
const OID_SHA256: &[u8] = &[0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01];
const OID_RSA: &[u8] = &[0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x01];
const OID_SHA256_RSA: &[u8] = &[0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x0B];
const OID_CN: &[u8] = &[0x55, 0x04, 0x03];

fn der_tag(tag: u8, body: &[u8]) -> Vec<u8> {
    let mut out = vec![tag];
    push_der_len(&mut out, body.len());
    out.extend_from_slice(body);
    out
}

fn der_cat(items: &[Vec<u8>]) -> Vec<u8> {
    let mut body = Vec::new();
    for item in items {
        body.extend_from_slice(item);
    }
    body
}

fn der_seq(items: &[Vec<u8>]) -> Vec<u8> {
    der_tag(0x30, &der_cat(items))
}

fn der_set(items: &[Vec<u8>]) -> Vec<u8> {
    der_tag(0x31, &der_cat(items))
}

fn der_int(bytes: &[u8]) -> Vec<u8> {
    let mut v = bytes.to_vec();
    while v.len() > 1 && v[0] == 0 {
        v.remove(0);
    }
    if v.is_empty() {
        v.push(0);
    }
    if v[0] & 0x80 != 0 {
        v.insert(0, 0);
    }
    der_tag(0x02, &v)
}

fn der_oid(content: &[u8]) -> Vec<u8> {
    der_tag(0x06, content)
}

fn der_null() -> Vec<u8> {
    vec![0x05, 0x00]
}

fn der_utf8(s: &str) -> Vec<u8> {
    der_tag(0x0C, s.as_bytes())
}

fn der_utctime(s: &str) -> Vec<u8> {
    der_tag(0x17, s.as_bytes())
}

fn der_octet(data: &[u8]) -> Vec<u8> {
    der_tag(0x04, data)
}

fn der_bitstring(data: &[u8]) -> Vec<u8> {
    let mut body = Vec::with_capacity(data.len() + 1);
    body.push(0x00); // unused bits
    body.extend_from_slice(data);
    der_tag(0x03, &body)
}

/// 上下文相关标签的「隐式/显式」包装：`[nr]` 构造类型。
fn der_explicit(nr: u8, inner: &[u8]) -> Vec<u8> {
    der_tag(0xA0 | nr, inner)
}

fn sha256_alg() -> Vec<u8> {
    der_seq(&[der_oid(OID_SHA256), der_null()])
}

fn rsa_alg() -> Vec<u8> {
    der_seq(&[der_oid(OID_RSA), der_null()])
}

fn sha256_rsa_alg() -> Vec<u8> {
    der_seq(&[der_oid(OID_SHA256_RSA), der_null()])
}

/// X.509 Name：CN=Vapor。
fn x509_name() -> Vec<u8> {
    let atv = der_seq(&[der_oid(OID_CN), der_utf8("Vapor")]);
    der_seq(&[der_set(&[atv])])
}

/// 用私钥构造一张自签名 X.509 v3 证书（有效期 2024-2034）。
fn build_self_signed_cert(key: &RsaPrivateKey) -> Result<Vec<u8>, String> {
    let public = key.to_public_key();
    let modulus = public.n().to_bytes_be();
    let exponent = public.e().to_bytes_be();
    let rsa_pub = der_seq(&[der_int(&modulus), der_int(&exponent)]);
    let spki = der_seq(&[rsa_alg(), der_bitstring(&rsa_pub)]);

    let serial = der_int(&[0x01]);
    let name = x509_name();
    let validity = der_seq(&[
        der_utctime("240101000000Z"),
        der_utctime("440101000000Z"),
    ]);
    // [0] EXPLICIT v3
    let version = der_explicit(0, &der_int(&[0x02]));
    let tbs = der_seq(&[
        version,
        serial,
        sha256_rsa_alg(),
        name.clone(),
        validity,
        name,
        spki,
    ]);

    let sig = SigningKey::<Sha256>::new(key.clone())
        .sign(&tbs)
        .to_bytes()
        .to_vec();
    Ok(der_seq(&[tbs, sha256_rsa_alg(), der_bitstring(&sig)]))
}

/// 构造 .RSA 文件内容：PKCS#7 ContentInfo(SignedData)。
///
/// SignerInfo 不带 authenticatedAttributes，`encryptedDigest` 直接是对 .SF
/// 字节的 RSA PKCS#1 v1.5 (SHA-256) 签名 —— 与 jarsigner 的 detached 形式一致。
fn build_pkcs7(key: &RsaPrivateKey, sf: &[u8]) -> Result<Vec<u8>, String> {
    let cert = build_self_signed_cert(key)?;
    let sig = SigningKey::<Sha256>::new(key.clone())
        .sign(sf)
        .to_bytes()
        .to_vec();

    let digest_algs = der_set(&[sha256_alg()]);
    let encap_content = der_seq(&[der_oid(OID_DATA)]);
    // [0] IMPLICIT CertificateSet：内容是若干 Certificate 的 DER 序列
    let certificates = der_tag(0xA0, &cert);
    let issuer_and_serial = der_seq(&[x509_name(), der_int(&[0x01])]);
    let signer_info = der_seq(&[
        der_int(&[0x01]), // version
        issuer_and_serial,
        sha256_alg(),
        rsa_alg(),
        der_octet(&sig),
    ]);
    let signer_infos = der_set(&[signer_info]);
    let signed_data = der_seq(&[
        der_int(&[0x01]), // version
        digest_algs,
        encap_content,
        certificates,
        signer_infos,
    ]);
    Ok(der_seq(&[der_oid(OID_SIGNED_DATA), der_explicit(0, &signed_data)]))
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

    #[test]
    fn pkcs7_has_sequence_and_cert() {
        let key = load_or_generate_key("test");
        let block = build_pkcs7(&key, b"Signature-Version: 1.0\r\n\r\n").unwrap();
        // ContentInfo SEQUENCE 开头
        assert_eq!(block[0], 0x30);
        // 内含 sha256 OID 与 signedData OID
        assert!(block.windows(OID_SIGNED_DATA.len()).any(|w| w == OID_SIGNED_DATA));
        assert!(block.windows(OID_SHA256.len()).any(|w| w == OID_SHA256));
    }
}
