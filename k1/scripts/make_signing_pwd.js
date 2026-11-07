#!/usr/bin/env node
/*
 * 为 DevEco/hvigor 生成签名密码的加密件（material/fd|ac|ce + 32+ 位 hex 密文）
 *
 * 背景：hvigor 的 build-profile.json5 -> app.signingConfigs[].material 里
 *       storePassword / keyPassword 不接受明文，长度必须 =32 且为偶数，
 *       并会用 <storeFile 所在目录>/material/{fd,ac,ce} 里的密钥材料做 AES-GCM 解密。
 *       （见 hvigor-ohos-plugin/src/utils/decipher-util.js）
 *
 *       DevEco Studio 的签名界面会自动生成这套材料；命令行/脚本场景没有，就自己造。
 *
 * 用法：
 *   node make_signing_pwd.js <storeFile 所在目录> <密码> [更多密码...]
 * 例：
 *   node make_signing_pwd.js <签名目录> <你的口令>
 * 输出：直接打印可粘进 build-profile.json5 的密文
 */
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

// hvigor 里硬编码的 component（Int8Array，转成无符号 Buffer）
const COMPONENT = Buffer.from(
  [49, 243, 9, 115, 214, 175, 91, 184, 211, 190, 177, 88, 101, 131, 192, 119].map(v => v & 0xff)
);

function xorAll(list) {
  const out = Buffer.alloc(16);
  for (const b of list) for (let i = 0; i < 16; i++) out[i] ^= b[i];
  return out;
}

// 与 decipher-util.js: getRootKey() 完全一致
function rootKey(components, salt) {
  const xored = xorAll(components);
  // 注意：原实现是 Buffer.toString()（默认 utf8，可能是有损的），这里必须同样有损才解得出
  const pwdStr = xored.toString();
  return crypto.pbkdf2Sync(pwdStr, salt, 10000, 16, 'sha256');
}

// 与 decipher-util.js: decrypt() 一致 —— [4B contentLen][12B iv][cipher][16B tag]
function aesGcmDecrypt(key, msg) {
  const contentLen = ((msg[0] & 0xff) << 24) | ((msg[1] & 0xff) << 16) | ((msg[2] & 0xff) << 8) | (msg[3] & 0xff);
  const ivLen = msg.length - 4 - contentLen;
  const iv = msg.slice(4, 4 + ivLen);
  const tag = msg.slice(msg.length - 16);
  const ct = msg.subarray(4 + ivLen, msg.length - 16);
  const d = crypto.createDecipheriv('aes-128-gcm', key, iv);
  d.setAuthTag(tag);
  return Buffer.concat([d.update(ct), d.final()]);
}

function aesGcmEncrypt(key, plain) {
  const iv = crypto.randomBytes(12);
  const c = crypto.createCipheriv('aes-128-gcm', key, iv);
  const ct = Buffer.concat([c.update(plain), c.final()]);
  const tag = c.getAuthTag();
  const body = Buffer.concat([iv, ct, tag]);           // iv + cipher + tag
  const len = Buffer.alloc(4);
  len.writeInt32BE(ct.length + 16, 0);                 // contentLen = 密文 + tag（不含 iv）
  return Buffer.concat([len, body]);
}

function writeOne(dir, name, bytes) {
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, name), bytes);
}

function main() {
  const [dir, ...passwords] = process.argv.slice(2);
  if (!dir || passwords.length === 0) {
    console.error('用法: node make_signing_pwd.js <storeFile 所在目录> <密码> [更多密码...]');
    process.exit(1);
  }
  const materialDir = path.resolve(dir, 'material');
  fs.rmSync(materialDir, { recursive: true, force: true });   // 旧材料必须清掉，否则目录个数对不上

  // 目录结构必须照抄 hvigor 的读法：
  //   material/fd/<3 个子目录>/<每个子目录里 1 个文件，16 字节>    <- readFd -> readDirBytes
  //   material/ac/<1 个文件>                                     <- 盐
  //   material/ce/<1 个文件>                                     <- 加密后的工作密钥
  const fd = [crypto.randomBytes(16), crypto.randomBytes(16), crypto.randomBytes(16)];
  const salt = crypto.randomBytes(16);
  const wk = crypto.randomBytes(16);                        // 工作密钥

  fd.forEach((b, i) => writeOne(path.join(materialDir, 'fd', `d${i + 1}`), 'v.bin', b));
  writeOne(path.join(materialDir, 'ac'), 's1.bin', salt);
  writeOne(path.join(materialDir, 'ce'), 'w1.bin', aesGcmEncrypt(rootKey([...fd, COMPONENT], salt), wk));

  // 自检：按 hvigor 的读取顺序重新解一遍
  const back = aesGcmDecrypt(rootKey([...fd, COMPONENT], salt), fs.readFileSync(path.join(materialDir, 'ce', 'w1.bin')));
  if (!back.equals(wk)) { console.error('自检失败：工作密钥回读不一致'); process.exit(2); }

  console.log(`# 材料目录: ${materialDir}`);
  passwords.forEach((p, i) => {
    const enc = aesGcmEncrypt(wk, Buffer.from(p, 'utf8')).toString('hex');
    const dec = aesGcmDecrypt(back, Buffer.from(enc, 'hex')).toString('utf8');
    if (dec !== p) { console.error(`自检失败：密码 ${i + 1} 回读不一致`); process.exit(3); }
    console.log(`password[${i + 1}] 明文=${p}`);
    console.log(`密文=${enc}`);
  });
}

main();
