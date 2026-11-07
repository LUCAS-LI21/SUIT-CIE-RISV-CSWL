#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 HarmonyOS SDK 编出的 HAP 改标为 OpenHarmony 编译，用于验证字节码是否兼容。

背景：
  设备是 OpenHarmony 5.0.0.71 / API 12，
  而 HAP 的 module.json 写着 compileSdkType=HarmonyOS、compileSdkVersion=6.0.1.116，
  bm 安装时按 SDK 类型校验并拒绝（error 9568282）。

注意：这只是绕过安装校验的临时验证手段。
      真正的解法是用 OpenHarmony 5.0.0 SDK 重新编译（后台下载中）。
      字节码 ABC 版本若高于设备运行时支持，应用启动会失败——那说明这条捷径不通。
"""
import json
import shutil
import sys
import zipfile

SRC = "entry-default-unsigned.hap"
DST = "entry-oh-patched-unsigned.hap"

# 必须保持 STORED 的媒体扩展名：这些格式运行时需要随机访问（seek）
MEDIA_EXT = (".mp4", ".m4a", ".mp3", ".wav", ".ogg", ".webm",
             ".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg")

TARGET = {
    "compileSdkType": "OpenHarmony",
    "compileSdkVersion": "5.0.0.71",
    # HarmonyOS SDK 把 API 版本编码成 50000012（5.0.0.12），
    # 而 OpenHarmony 的 bm 用整数 12 比较；改成 12 才能通过版本校验。
    "minAPIVersion": 12,
    "targetAPIVersion": 12,
}


def main():
    src_path = sys.argv[1] if len(sys.argv) > 1 else SRC
    dst_path = sys.argv[2] if len(sys.argv) > 2 else DST

    with zipfile.ZipFile(src_path) as zin:
        names = zin.namelist()
        with zipfile.ZipFile(dst_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "module.json":
                    d = json.loads(data)
                    app = d.setdefault("app", {})
                    before = {k: app.get(k) for k in TARGET}
                    app.update(TARGET)
                    data = json.dumps(d, ensure_ascii=False).encode("utf-8")
                    print("[patch] module.json before:", before)
                    print("[patch] module.json after :",
                          {k: app.get(k) for k in TARGET})
                # 保留原始信息，但不沿用 extra（避免残留 ZIP64/签名相关字段）
                zi = zipfile.ZipInfo(item.filename, date_time=item.date_time)
                # ── 压缩方式必须沿用原包，不能一律 DEFLATED ────────────────
                # 2026-11-02 踩坑：早期版本这里写死 ZIP_DEFLATED，把 rawfile 里
                # 46 个 mp4 从 STORED 改成了 DEFLATED。mp4 本身不可压缩，所以
                # 体积没变化、包也装得上，但 Video 组件从此全部 onError ——
                # AVPlayer 需要可随机寻址的 fd，DEFLATED 条目只能整体解压到内存，
                # 拿不到 seekable fd 就会在 60ms 内失败，且媒体框架日志一片空白。
                # 现在对媒体文件强制 STORED，其余沿用原始类型。
                if item.filename.lower().endswith(MEDIA_EXT):
                    zi.compress_type = zipfile.ZIP_STORED
                else:
                    zi.compress_type = item.compress_type
                zi.external_attr = item.external_attr
                zout.writestr(zi, data)
    print(f"[ok] 写出 {dst_path}，共 {len(names)} 个条目")


if __name__ == "__main__":
    main()
