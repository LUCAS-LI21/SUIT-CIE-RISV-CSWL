#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析 3568 语音识别上传的 wav，判断麦克风到底采到了什么。

背景
----
2026-11-06 现象：3568 → K1 的 ASR 链路通（服务端返回 <|Speech|>），
但 text 为空或只有一个「我」。前端电平日志给出 rms=1687 / peak=1922，
rms/peak=0.88 —— 人声的这个比值应该在 0.1~0.3，0.88 说明信号幅度近乎恒定，
不像语音。本脚本用于定性：到底是人声、环境噪声、固定频率干扰，还是驱动假数据。

判据
----
人声（正常近讲）：
  - 分帧能量起伏大（帧 RMS 的 标准差/均值 > 0.5）
  - 频谱能量集中在 100~4000 Hz，且有多个共振峰（不是单一尖峰）
  - 零交叉率 0.02~0.20
固定单音/自激/驱动假数据：
  - 频谱只有一根尖峰，其余频段几乎为零
  - 帧能量标准差极小
  - ZCR 与主频严格对应（ZCR ≈ 2*f0/fs）

用法
----
    python scripts/analyze_asr_wav.py asr_dump.wav
"""
import sys
import wave

import numpy as np


def load_wav(path):
    with wave.open(path, "rb") as w:
        ch = w.getnchannels()
        sw = w.getsampwidth()
        fs = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        raise SystemExit(f"只支持 16 bit，实际 {sw * 8} bit")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float64)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, fs, ch


def frame_stats(x, fs, win_ms=20.0):
    """分帧 RMS，用来看能量是否起伏（人声一定起伏）。"""
    win = int(fs * win_ms / 1000.0)
    hop = win // 2
    n = (len(x) - win) // hop + 1
    if n <= 0:
        return np.array([0.0])
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = x[idx]
    return np.sqrt((frames ** 2).mean(axis=1))


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    x, fs, ch = load_wav(sys.argv[1])
    dur = len(x) / fs

    peak = np.abs(x).max()
    rms = np.sqrt((x ** 2).mean())
    zcr = float(np.mean(np.abs(np.diff(np.signbit(x).astype(np.int8)))))

    print(f"文件      : {sys.argv[1]}")
    print(f"采样率    : {fs} Hz   声道: {ch}   时长: {dur:.2f} s   样本: {len(x)}")
    print(f"峰值      : {peak:.0f}  ({peak / 32768 * 100:.1f}% 满量程)")
    print(f"RMS       : {rms:.1f}  ({20 * np.log10(max(rms, 1) / 32768):.1f} dBFS)")
    print(f"rms/peak  : {rms / max(peak, 1):.3f}   (人声 0.1~0.3；≈1 表示幅度恒定)")
    print(f"零交叉率  : {zcr:.4f}   (白噪≈0.5，1 kHz 单音≈0.125，人声 0.02~0.20)")

    # 分帧能量起伏
    fr = frame_stats(x, fs)
    cv = float(fr.std() / max(fr.mean(), 1e-9))
    print(f"帧RMS     : 均值 {fr.mean():.1f}  标准差 {fr.std():.1f}  "
          f"变异系数 {cv:.3f}   (人声 >0.5；恒定信号 <0.1)")
    print(f"帧RMS范围 : {fr.min():.1f} ~ {fr.max():.1f}")

    # 频谱
    nfft = 1 << int(np.ceil(np.log2(len(x))))
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)), nfft))
    freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
    band = freqs <= 8000
    sp, fp = spec[band], freqs[band]
    tot = sp.sum() + 1e-9

    top = np.argsort(sp)[-5:][::-1]
    print("\n主频峰 (前 5):")
    for i in top:
        print(f"  {fp[i]:7.1f} Hz   占比 {sp[i] / tot * 100:6.2f}%")

    def band_energy(lo, hi):
        m = (fp >= lo) & (fp < hi)
        return sp[m].sum() / tot * 100

    print("\n频段能量占比:")
    for lo, hi, name in [(0, 100, "0-100 Hz 直流/电源纹波"),
                         (100, 300, "100-300 Hz 基频区"),
                         (300, 1000, "300-1k Hz 语音主区"),
                         (1000, 4000, "1k-4k Hz 语音清晰度区"),
                         (4000, 8000, "4k-8k Hz 齿音/噪声")]:
        print(f"  {name:28s} {band_energy(lo, hi):6.2f}%")

    # 前 0.05 秒的样本，直观看是不是常数
    print("\n前 40 个样本:", ", ".join(str(int(v)) for v in x[:40]))

    # ---- 结论 ----
    print("\n" + "=" * 52)
    peak_ratio = peak / 32768
    flat = cv < 0.15
    if peak_ratio < 0.02:
        print("结论: 近乎静音 —— 麦克风没拾到任何声音（可能没有麦克风硬件）")
    elif flat and peak_ratio < 0.15:
        print("结论: 幅度恒定的低幅信号，不是人声。")
        print("      最可能是驱动返回的固定噪声/纹波，或麦克风未接入后的底噪。")
    elif zcr > 0.4:
        print("结论: 宽带噪声（ZCR 过高），没有人声成分。")
    elif cv < 0.3:
        print("结论: 能量起伏太小，不像说话；疑似固定音/干扰。")
    else:
        print("结论: 有类似人声的起伏，需要听音频确认。")
    print("=" * 52)


if __name__ == "__main__":
    main()
