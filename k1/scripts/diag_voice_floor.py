#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：安静房间里到底有什么声音被判成 bark。

直接读 arecord 抓的 wav，按服务端的 0.96s 窗 / 0.48s 移复算 rms_max_db，
找出过 -38 dBFS 能量门的窗口，打印它们的频谱峰，判断是周期机械声、
人声、还是真的动物叫。
"""
import sys
import wave

import numpy as np

SR = 16000
WIN_SEC = 0.96
HOP_SEC = 0.48
N_FFT = 512


def main(path):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch)[:, 0]
    assert sr == SR, f"期望 {SR} 实际 {sr}"
    dur = len(x) / SR

    # ① 全局能量
    rms_all = float(np.sqrt(np.mean(x ** 2)))
    print(f"时长 {dur:.2f}s  全局 rms={20*np.log10(max(rms_all,1e-9)):.1f} dBFS  "
          f"峰值={20*np.log10(max(float(np.max(np.abs(x))),1e-9)):.1f} dBFS")

    # ② 每 0.1 s 的能量曲线（看是否周期性）
    hop = SR // 10
    n = len(x) // hop
    fr = x[:n * hop].reshape(n, hop)
    db100 = 20 * np.log10(np.maximum(np.sqrt((fr ** 2).mean(axis=1)), 1e-9))
    print("\n每 0.1s 能量（dBFS，行=秒）:")
    for s in range(int(np.ceil(n / 10))):
        seg = db100[s * 10:(s + 1) * 10]
        print(f"  {s:2d}s " + " ".join(f"{v:6.1f}" for v in seg))

    # ③ 按服务端窗复算 rms_max_db
    W = int(WIN_SEC * SR)
    Hs = int(HOP_SEC * SR)
    print(f"\n最响的一些 0.96s 窗（服务端口径 rms_max_db）:")
    rows = []
    for st in range(0, max(1, len(x) - W + 1), Hs):
        w_ = x[st:st + W]
        if len(w_) < W:
            break
        fl = len(w_) // hop
        sub = w_[:fl * hop].reshape(fl, hop)
        d = 20 * np.log10(np.maximum(np.sqrt((sub ** 2).mean(axis=1)), 1e-9))
        rows.append((float(d.max()), st / SR))
    rows.sort(reverse=True)
    for v, t in rows[:6]:
        print(f"  t={t:5.2f}s  rms_max_db={v:6.1f}  {'过 -38 门' if v > -38 else ''}")

    # ④ 最响窗的频谱峰
    if not rows:
        return
    v, t = rows[0]
    st = int(t * SR)
    w_ = x[st:st + W] * np.hanning(W)
    sp = np.abs(np.fft.rfft(w_, n=4096))
    fq = np.fft.rfftfreq(4096, 1.0 / SR)
    order = np.argsort(sp)[::-1]
    peaks, used = [], []
    for i in order:
        if fq[i] < 30:
            continue
        if any(abs(fq[i] - u) < 20 for u in used):
            continue
        used.append(fq[i])
        peaks.append((fq[i], sp[i]))
        if len(peaks) >= 8:
            break
    tot = sp.sum() + 1e-12
    print(f"\n最响窗 t={t:.2f}s 的频谱主峰:")
    for f, a in peaks:
        print(f"  {f:7.1f} Hz   相对能量 {a/tot*100:5.2f}%")

    # ⑤ 低频/谐波判断
    e_low = sp[fq < 200].sum() / tot * 100
    e_mid = sp[(fq >= 200) & (fq < 1000)].sum() / tot * 100
    e_high = sp[fq >= 1000].sum() / tot * 100
    print(f"\n能量分布: <200Hz {e_low:.1f}%  |  200-1000Hz {e_mid:.1f}%  |  >1kHz {e_high:.1f}%")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "_probe_quiet.wav")
