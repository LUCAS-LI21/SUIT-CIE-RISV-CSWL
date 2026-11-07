#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""panns_sidecar 的客户端与标定工具。

两种用法：
  1) 单文件测试（验证 HTTP 接口与端到端结果）
     python panns_sidecar_test.py --url http://127.0.0.1:11442 a.wav b.wav
  2) 阈值标定（跑一批样本，输出 7 类分数的分布，用来定 PET_THRESHOLD）
     python panns_sidecar_test.py --url ... --calib samples/*.wav

设计要点：
  · 一律按 wav 头的真实采样率上报，不做任何假定。初版把 44.1 kHz 当 16 kHz
    送进去，等于慢放 2.75 倍，模型判成 Music，差点误判成模型坏了。
  · 传的是原始 int16 PCM，服务端负责重采样。原因是板端没有 scipy，
    重采样逻辑只该存在于服务端一处，客户端不复制一份。
"""
import argparse
import glob
import json
import statistics
import sys
import urllib.request
import wave

# 本机环境带 http_proxy=127.0.0.1:xxxxx，不绕过会把 127.0.0.1 的请求发给代理，
# 表现为莫名的 502 Bad Gateway。服务端和客户端都在本机时必须显式禁用代理。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_wav(path):
    with wave.open(path, "rb") as w:
        sr, nch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise SystemExit(f"{path}: 只支持 16 bit PCM，实际 {sw * 8} bit")
    return raw, sr, nch


def post(url, path, timeout=30):
    raw, sr, nch = read_wav(path)
    req = urllib.request.Request(f"{url}/classify?sr={sr}&nch={nch}", data=raw,
                                 headers={"Content-Type": "application/octet-stream"})
    t0 = __import__("time").time()
    with _OPENER.open(req, timeout=timeout) as r:
        d = json.load(r)
    d["wall_ms"] = round((__import__("time").time() - t0) * 1000, 1)
    d["sr"] = sr
    d["dur_src"] = round(len(raw) / 2 / nch / sr, 2)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--url", default="http://127.0.0.1:11442")
    ap.add_argument("--calib", action="store_true", help="输出各类分布用于定阈值")
    args = ap.parse_args()

    paths = []
    for f in args.files:
        paths.extend(sorted(glob.glob(f)))

    rows = []
    print(f"{'样本':30s} {'sr':>6s} {'时长':>5s} {'判定':10s} {'分':>5s} "
          f"{'bark':>5s} {'meow':>5s} {'others':>16s} {'ms':>6s}")
    for p in paths:
        try:
            d = post(args.url, p)
        except Exception as e:                        # noqa: BLE001
            print(f"{p.split('/')[-1][:28]:30s} 失败 {e}")
            continue
        g = d["groups"]
        others = " ".join(f"{k[:4]}={v:.2f}" for k, v in g.items()
                          if k not in ("bark", "meow") and v >= 0.05)
        print(f"{p.split('/')[-1][:28]:30s} {d['sr']:6d} {d['dur_src']:5.2f} "
              f"{d['cls']:10s} {d['conf']:5.2f} {g['bark']:5.2f} {g['meow']:5.2f} "
              f"{others:16s} {d['ms']:6.0f}")
        rows.append(d)

    if not rows:
        raise SystemExit("没有成功的样本")

    ms = sorted(r["ms"] for r in rows)
    print(f"\n推理延迟 n={len(ms)}  p50 {statistics.median(ms):.0f} ms  "
          f"min {ms[0]:.0f}  max {ms[-1]:.0f}")

    if args.calib:
        print("\n各组最高分分布（用于定 PET_THRESHOLD）：")
        for k in ("bark", "growl", "howl", "meow", "purr", "hiss", "scream"):
            v = sorted(r["groups"][k] for r in rows)
            print(f"  {k:7s} p50 {statistics.median(v):.3f}  max {v[-1]:.3f}  "
                  f"min {v[0]:.3f}")

    pet = [r for r in rows if r["cls"] != "noise"]
    print(f"\n宠物类命中 {len(pet)}/{len(rows)}")
    for r in pet:
        print(f"  {r['cls']:8s} {r['conf']:.2f}")


if __name__ == "__main__":
    main()
