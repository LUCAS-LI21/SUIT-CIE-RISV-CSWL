#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静音闸门标定：用真实录音算出「说话」和「没人说话」的帧级能量分布。

背景：k1_mic_asr.py 原来的闸门是
    rms < 120 and peak < 900 and zcr < 0.02  → 拒识
三条**同时**成立才拒，实测环境底噪 rms 515~552、peak 2828~4468，
一条都不满足，于是 5.9 s 纯底噪被 SenseVoice 硬解成
「我记之下像最远的路」—— 标点齐全、语句通顺，人看不出是幻觉。
三条与关系本身就是错的：只要「没有持续的人声能量」就该拒。

这里不猜阈值，直接量：
  · floor   帧能量的 10 分位（底噪）
  · thr     max(floor*2.5, 350)  —— 高于这个算「有声帧」
  · ratio   有声帧占比
  · longest 最长连续有声段（秒）

用法（板端）：
    python3 /root/asr_gate_calib.py /tmp/k1_press.raw          # 裸 PCM 16k s16le
    python3 /root/asr_gate_calib.py <zh.wav>                   # wav 也行
"""
import sys
import wave

import numpy as np

FRAME = 400   # 25 ms @ 16 kHz


def load(path):
    if path.endswith(".wav"):
        with wave.open(path, "rb") as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return np.fromfile(path, dtype="<i2")


def stats(x, frame=FRAME):
    n = len(x) // frame
    if n < 4:
        return None
    f = x[: n * frame].reshape(n, frame).astype(np.float32)
    frms = np.sqrt((f ** 2).mean(axis=1))
    floor = float(np.percentile(frms, 10))
    for k in (2.0, 2.5, 3.0, 4.0):
        thr = max(floor * k, 350.0)
        above = frms > thr
        longest = cur = 0
        for a in above:
            cur = cur + 1 if a else 0
            longest = max(longest, cur)
        print(f"   k={k:.1f}  thr={thr:7.0f}  ratio={above.mean():5.3f}  "
              f"longest={longest * frame / 16000:5.2f}s")
    peak = int(np.abs(x).max())
    rms = float(np.sqrt((x.astype(np.float64) ** 2).mean()))
    zcr = float(np.mean(np.abs(np.diff(np.signbit(x).astype(np.int8)))))
    print(f"   全局  rms={rms:7.1f}  peak={peak:6d}  zcr={zcr:6.4f}  "
          f"dur={len(x) / 16000:5.2f}s  floor={floor:6.1f}  "
          f"帧能量 max={frms.max():6.0f} p90={np.percentile(frms, 90):6.0f}")


def main():
    for p in sys.argv[1:]:
        print(f"=== {p} ===")
        try:
            x = load(p)
        except Exception as e:
            print(f"   读不了: {e}")
            continue
        if x.size == 0:
            print("   空文件")
            continue
        stats(x)


if __name__ == "__main__":
    main()
