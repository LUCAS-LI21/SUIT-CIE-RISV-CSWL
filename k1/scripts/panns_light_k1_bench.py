#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轻量 PANNs 在 K1 板端的延迟与效果实测（板端运行）。

为什么要单独一个板端脚本：
    PC 上的 7 ms 完全不能外推到 K1 —— Cnn14 在 PC 上是几十毫秒，在 K1 上是 9.7 s，
    差了三个量级。"应当落在 120~150 ms"这种估计必须在板子上量过才算数。

输入约定（与导出时的 32 kHz 配置一致，不能改）：
    麦克风 16 kHz → 上采样 2 倍 → 截/铺到 2.0 s（64000 样本）→ int8 ONNX

用法（板端）：
    python3 /root/k1-ai/panns_light_bench.py
    python3 /root/k1-ai/panns_light_bench.py --repeat 20
"""
import argparse
import time
import wave
from pathlib import Path

import numpy as np
import onnxruntime as ort

MODEL = "/root/k1-ai/MobileNetV2_32k_int8.onnx"
LABELS = "/root/k1-ai/labels.csv"
SR_MIC = 16000
SR_MODEL = 32000
WIN_SEC = 2.0
PET_IDX = [72, 73, 74, 75, 78, 79, 80, 81, 82, 83, 84]


def load_labels(path=LABELS):
    import csv
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    rows.sort(key=lambda r: int(r["index"]))
    return [r["display_name"] for r in rows]


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def upsample2(x):
    """16k → 32k。线性插值就够：模型真正在乎的是 0~8 kHz 的能量分布，
    8 kHz 以上本来就是空的（原信号没有），插值不会伪造出内容。"""
    n = len(x)
    if n == 0:
        return x.astype(np.float32)
    idx = np.arange(0, n - 1 + 1e-9, 0.5)
    return np.interp(idx, np.arange(n), x).astype(np.float32)


def resample_to(x, sr, target=SR_MODEL):
    """通用重采样（板端没有 scipy，只能用 np.interp）。

    **这个函数存在的原因**：初版直接假定输入就是 16 kHz，把 44.1 kHz 的
    real_dog.wav 当 16k 处理，等于慢放 2.75 倍、音高降了一个多八度，
    结果同一个模型在 PC 上判 Dog 0.36，板上判成 Music 0.15 —— 差点误判成
    「板端推理有问题」。测效果前必须先确认采样率对得上。
    """
    if sr == target or len(x) == 0:
        return x.astype(np.float32)
    n_out = int(round(len(x) * target / sr))
    idx = np.arange(n_out) * (sr / target)
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


def fit(x, want):
    if len(x) == 0:
        return np.zeros(want, dtype=np.float32)
    if len(x) >= want:
        return x[:want].astype(np.float32)
    return np.tile(x, want // len(x) + 1)[:want].astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=10)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--threads", type=int, default=2)
    args = ap.parse_args()

    names = load_labels()
    so = ort.SessionOptions()
    so.intra_op_num_threads = args.threads
    so.inter_op_num_threads = 1
    t = time.time()
    sess = ort.InferenceSession(args.model, so, providers=["CPUExecutionProvider"])
    load_ms = (time.time() - t) * 1000
    print(f"模型加载 {load_ms:.0f} ms  线程 {args.threads}  "
          f"{Path(args.model).stat().st_size / 1e6:.1f} MB")

    want = int(SR_MODEL * WIN_SEC)

    # ---- 1. 纯延迟（固定输入，排除 IO 与重采样）
    dummy = np.zeros(want, dtype=np.float32)
    sess.run(None, {"input_audio": dummy[None, :]})
    ts = []
    for _ in range(args.repeat):
        t0 = time.time()
        sess.run(None, {"input_audio": dummy[None, :]})
        ts.append((time.time() - t0) * 1000)
    ts.sort()
    p50 = ts[len(ts) // 2]
    print(f"纯推理 {args.repeat} 次：p50 {p50:.0f} ms  最小 {ts[0]:.0f}  最大 {ts[-1]:.0f}")

    # ---- 2. 端到端（含 16k→32k 上采样 + 铺窗），这才是服务里真实发生的开销
    p16 = Path("/root/k1-ai/voice_samples/01_meow.wav")
    if p16.exists():
        x16, sr16 = read_wav(p16)
    else:                       # 没有 16k 样本就就地降采样一份，保证这一段能测
        x32, _ = read_wav("/root/k1-ai/real_dog_32k.wav")
        x16, sr16 = x32[::2], SR_MIC
    e2e = []
    for _ in range(max(3, args.repeat // 2)):
        t0 = time.time()
        seg = fit(upsample2(x16), want)
        out = sess.run(None, {"input_audio": seg[None, :]})[0][0]
        e2e.append((time.time() - t0) * 1000)
    e2e.sort()
    print(f"含上采样   p50 {e2e[len(e2e) // 2]:.0f} ms（输入 {sr16} Hz）")

    # ---- 3. 效果：用 PC 转好的 32 kHz 版本，排除重采样差异
    x32, sr32 = read_wav("/root/k1-ai/real_dog_32k.wav")
    print(f"\n真实狗叫分段（{sr32} Hz，应出现 Dog / Bark / Bow-wow）：")
    for a in (0.0, 1.0, 2.0):
        seg = fit(resample_to(x32[int(a * sr32):], sr32), want)
        s = sess.run(None, {"input_audio": seg[None, :]})[0][0]
        pet = {names[i]: round(float(s[i]), 2) for i in PET_IDX if s[i] > 0.10}
        top = np.argsort(-s)[:3]
        print(f"  起点{a:.0f}s  top3={[(names[i], round(float(s[i]), 2)) for i in top]}")
        print(f"           宠物类={pet}")


if __name__ == "__main__":
    main()
