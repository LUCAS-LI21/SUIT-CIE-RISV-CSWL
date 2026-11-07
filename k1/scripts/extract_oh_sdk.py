#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解压 OpenHarmony 5.0.0 SDK（Windows 组件）。

为什么要用 Python 而不是 tar：
  Git Bash 的 tar 处理 >2GB 的 gz 会在结尾报 "unexpected end of file"
  （gzip -t 却通过），改用 tarfile 模块可正常读取。

只解压编译 ArkTS 必需的四件套：ets / toolchains / js / previewer。
native（1108 MB）是 C++ NDK，本工程只有预编译 .so，用不到。
"""
import os
import sys
import tarfile
import zipfile

TAR = "E:/CIE-RISC-V/sdk-dl/sdk.tar.gz"
OUT = "E:/OpenHarmonySDK5"
TMP = "E:/CIE-RISC-V/sdk-dl"
WANT = [
    "windows/ets-windows-x64-5.0.0.71-Release.zip",
    "windows/toolchains-windows-x64-5.0.0.71-Release.zip",
    "windows/js-windows-x64-5.0.0.71-Release.zip",
    "windows/previewer-windows-x64-5.0.0.71-Release.zip",
]


def main():
    os.makedirs(OUT, exist_ok=True)

    print("[1/2] 从 tar 提取 Windows 组件 zip ...")
    with tarfile.open(TAR, "r:gz") as tf:
        for name in WANT:
            m = tf.getmember(name)
            dst = os.path.join(TMP, os.path.basename(name))
            if os.path.exists(dst) and os.path.getsize(dst) == m.size:
                print("  已存在，跳过:", os.path.basename(name))
                continue
            with tf.extractfile(m) as src, open(dst, "wb") as out:
                while True:
                    chunk = src.read(8 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
            print(f"  提取 {os.path.basename(name)}  {m.size/1e6:.0f} MB")

    print("[2/2] 解压各组件到 SDK 目录 ...")
    for name in WANT:
        zpath = os.path.join(TMP, os.path.basename(name))
        with zipfile.ZipFile(zpath) as z:
            z.extractall(OUT)
        print("  解压完成:", os.path.basename(name))

    print("\n=== SDK 目录结构 ===")
    for entry in sorted(os.listdir(OUT)):
        full = os.path.join(OUT, entry)
        if os.path.isdir(full):
            print(f"  {entry}/  ({len(os.listdir(full))} 项)")
        else:
            print(f"  {entry}")


if __name__ == "__main__":
    sys.exit(main())
