#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K1 宠物叫声识别服务 —— 端口 11439

定位：把「宠物叫声」变成「结构化状态」，供 App 与 LLM 使用。
      与 asr_service.py（人声转写）并列，两者共用同一个麦克风（ALSA dsnoop）。

为什么不能直接用 ASR 做这件事（这是本服务存在的前提）：
    SenseVoice-Small 是人声 ASR，训练语料是人类语言（zh/en/ja/ko/yue），
    输出空间是「音素 → 汉字」的 CTC 对齐。它的解码器没有「非语音」类别，
    面对狗吠/猫叫只会两件事之一：
      ① VAD 判为非语音 → 直接丢弃，输出空；
      ② VAD 误判为语音 → 强行映射到最接近的音素，输出乱码汉字。
    实测（2026-11-02）把用户提问的原声喂进去，得到的是逐字正确的中文；
    而同样链路对犬吠样本输出为「（空）」或「嗯」这类无意义音节。
    → 所以宠物叫声需要的是「声学事件分类（AED）」，不是「语音识别（ASR）」。

本服务的做法（三段式，每段都可单独验证）：
    ① 声学门控：先判这段声音是 静音 / 人声 / 环境噪声 / 宠物发声
       —— 用 F0、谱平坦度、谐噪比、脉冲结构四个物理量区分，不用模型；
    ② 叫声细分：在「宠物发声」内部再分 吠叫/长嚎/低吼/喵叫/呼噜/哈气/尖叫
       —— 用基频轨迹、时长、能量包络、谱重心、调制深度；
    ③ 状态映射：把「叫声类型 + 重复次数 + 持续时长」映射到可解释的状态
       （警戒/焦虑/求食/疼痛/友好/威胁/发情），并给出置信度。

三个刻意的设计决定：
    1. 不用深度学习模型做第一版。理由：手上没有带标注的宠物叫声数据集，
       硬训一个模型只会得到一个「看起来很 AI 但没法验证」的黑盒。当前的
       物理量判据每一项都能在日志里看到数值，评委可以现场核对。
    2. 但保留 ONNX 模型插槽。--model petvoice.onnx 一旦存在就优先走模型
       （输入 1×1×64×64 log-mel，输出 7 类），接口与规则版完全一致，
       换成训练好的 CNN 不需要改任何调用方。
    3. 状态不交给 LLM 判断。LLM 只负责把结构化状态写成一句人话建议。
       依据是实测：0.6B 能照抄给定资料，但不能用资料做是非判断
       （给「葡萄可致肾衰竭」问能不能吃，它答「能」）。所以判断留在代码里。

与现有服务的端口分工：
    11436 视觉  11437 人声 ASR  11435 知识库  11438 LLM 网关  11439 本服务
    本服务 CPU 占用可忽略（每窗口约 3 ms numpy 运算），不参与时间片仲裁。

用法：
    python3 petvoice_service.py --device dsnoop0 --port 11439
    python3 petvoice_service.py --wav test.wav          # 离线验算，不起 HTTP
    python3 petvoice_service.py --device dsnoop0 --pet cat --verbose
"""

import argparse
import json
import math
import os
import struct
import subprocess
import threading
import time
import urllib.request
import wave
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

SR = 16000                 # 采样率
WIN_SEC = 0.96             # 分析窗长（96 帧 × 10 ms，便于 mel 对齐）
HOP_SEC = 0.48             # 窗移，50% 重叠
N_FFT = 512
N_MEL = 64
N_MFCC = 20
FRAME = 400                # 25 ms
HOP = 160                  # 10 ms
F0_MIN, F0_MAX = 120.0, 1600.0   # 覆盖犬吠低基频到猫叫高基频
# 基频估计专用窗：必须比 25 ms 的谱特征帧长得多。
# 25 ms 只装得下 3.5 个 140 Hz 周期，自相关在长滞后处的重叠段太短、
# 又被汉明窗进一步压低，结果凡基频低于约 175 Hz 的声音都测不出来
# （被 argmax 顶到 lag_min，输出伪造的 F0_MAX）。40 ms 能装 5.6 个周期。
F0_FRAME = 640             # 40 ms
F0_HOP = HOP               # 时间分辨率与谱特征保持一致

# ── 叫声类型 ──────────────────────────────────────────────────────────
CLS_SILENCE = "silence"
CLS_HUMAN = "human"
CLS_NOISE = "noise"
CLS_BARK = "bark"        # 犬吠：短促、爆发式起音、宽带
CLS_HOWL = "howl"        # 长嚎：长音、基频缓慢下滑
CLS_GROWL = "growl"      # 低吼：低频、强幅度调制、持续
CLS_MEOW = "meow"        # 喵叫：中高频、基频先升后降
CLS_PURR = "purr"        # 呼噜：低频周期脉冲、能量低而稳
CLS_HISS = "hiss"        # 哈气：宽带噪声、谱平坦度高、无基频
CLS_SCREAM = "scream"    # 尖叫：高基频、高能量、长音

PET_CLASSES = ["bark", "howl", "growl", "meow", "purr", "hiss", "scream"]

# 中文名，供日志与前端展示
CLS_CN = {
    CLS_SILENCE: "静音", CLS_HUMAN: "人声", CLS_NOISE: "环境噪声",
    "bark": "吠叫", "howl": "长嚎", "growl": "低吼", "meow": "喵叫",
    "purr": "呼噜", "hiss": "哈气", "scream": "尖叫",
}

# ── 状态映射表（叫声 → 可能状态，按先验概率排序） ─────────────────────
# 注意：一张表只能给出「倾向」，最终状态还要叠加重复次数与持续时长，
# 见 StateMapper.map()
STATE_RULES = {
    "bark":   [("alert", 0.55), ("threat", 0.25), ("play", 0.20)],
    "howl":   [("anxiety", 0.65), ("alert", 0.25), ("lonely", 0.10)],
    "growl":  [("threat", 0.80), ("alert", 0.20)],
    "meow":   [("hunger", 0.45), ("attention", 0.35), ("pain", 0.20)],
    "purr":   [("comfort", 0.85), ("attention", 0.15)],
    "hiss":   [("threat", 0.85), ("fear", 0.15)],
    "scream": [("pain", 0.75), ("fear", 0.25)],
}

STATE_CN = {
    "alert": "警戒", "threat": "威胁/驱赶", "play": "邀请玩耍",
    "anxiety": "分离焦虑", "lonely": "孤独呼唤", "hunger": "求食",
    "attention": "寻求关注", "pain": "疼痛", "comfort": "舒适",
    "fear": "恐惧", "quiet": "安静", "talking": "人声",
}

# 状态 → 规则兜底建议（LLM 不可用时使用，保证界面永远有话说）
STATE_ADVICE = {
    "alert": "它听到门外有动静，先别急着安抚，观察是不是有陌生人靠近。",
    "threat": "它在警告，先撤开让它不适的东西，不要伸手靠近。",
    "play": "它在邀请你玩，陪它玩五分钟消耗一下精力。",
    "anxiety": "长时间嚎叫通常是分离焦虑，出门前留件有你气味的衣物。",
    "lonely": "它在叫你，回应一声或过去看看能明显缓解。",
    "hunger": "可能是饿了，检查一下食盆和水碗。",
    "attention": "它在讨关注，摸摸它或说句话就够。",
    "pain": "尖叫要重视，检查有没有外伤，持续尖叫请尽快就医。",
    "comfort": "它很放松，不用打扰。",
    "fear": "它在害怕，降低音量、给它一个能躲的角落。",
    "quiet": "没有异常声音。",
}


# ══════════════════════════ 特征提取 ══════════════════════════


def _mel_filterbank(n_mel=N_MEL, n_fft=N_FFT, sr=SR, fmin=20.0, fmax=None):
    """三角 mel 滤波器组（HTK mel 标度），返回 (n_mel, n_fft//2+1)。"""
    fmax = fmax or sr / 2
    def hz2mel(f):
        return 2595.0 * math.log10(1.0 + f / 700.0)
    def mel2hz(m):
        return 700.0 * (10 ** (m / 2595.0) - 1.0)
    mels = np.linspace(hz2mel(fmin), hz2mel(fmax), n_mel + 2)
    hz = mel2hz(mels)
    bins = np.floor((n_fft + 1) * hz / sr).astype(int)
    fb = np.zeros((n_mel, n_fft // 2 + 1), dtype=np.float32)
    for i in range(n_mel):
        l, c, r = bins[i], bins[i + 1], bins[i + 2]
        if c == l:
            c = l + 1
        if r <= c:
            r = c + 1
        for k in range(l, min(c, fb.shape[1])):
            fb[i, k] = (k - l) / max(1, (c - l))
        for k in range(c, min(r, fb.shape[1])):
            fb[i, k] = (r - k) / max(1, (r - c))
    return fb.astype(np.float32)


MEL_FB = _mel_filterbank()
# DCT-II 基矩阵（正交归一），用于取 MFCC
def _dct_matrix(n_out, n_in):
    k = np.arange(n_out)[:, None]
    n = np.arange(n_in)[None, :]
    m = np.cos(math.pi * k * (2 * n + 1) / (2.0 * n_in)) * math.sqrt(2.0 / n_in)
    m[0, :] *= 1.0 / math.sqrt(2.0)
    return m.astype(np.float32)


DCT = _dct_matrix(N_MFCC, N_MEL)


def frames_of(x):
    """(n_samples,) → (n_frames, FRAME) 汉明窗分帧。"""
    if len(x) < FRAME:
        x = np.pad(x, (0, FRAME - len(x)))
    n = 1 + (len(x) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    fr = x[idx]
    return fr * np.hamming(FRAME).astype(np.float32)


def f0_track(x, sr=SR):
    """自相关法逐帧基频。返回 (f0_hz 数组, 浊音强度数组)。

    用自相关而不是倒谱：犬吠的宽带成分会让倒谱峰变糊，自相关在
    「有谐波但混噪声」的信号上更稳，正好是本场景的形态。
    """
    # 自己分帧，不用 frames_of：基频需要更长的窗（F0_FRAME），
    # 而且不能加汉明窗——汉明窗把帧边缘压到 0.08，等于给长滞后的
    # 自相关再乘一次衰减，低基频会被彻底压没。
    if len(x) < F0_FRAME:
        x = np.pad(x, (0, F0_FRAME - len(x)))
    n = 1 + (len(x) - F0_FRAME) // F0_HOP
    idx = np.arange(F0_FRAME)[None, :] + F0_HOP * np.arange(n)[:, None]
    fr = x[idx]
    f0 = np.zeros(n, dtype=np.float32)
    strength = np.zeros(n, dtype=np.float32)
    lag_min = int(sr / F0_MAX)
    lag_max = int(sr / F0_MIN)
    for i in range(n):
        f = fr[i] - fr[i].mean()
        e = float(np.dot(f, f))
        if e < 1e-6:
            continue
        ac = np.correlate(f, f, mode="full")[len(f) - 1:]
        if ac[0] <= 0:
            continue
        # ── 选峰必须用「偏置已归一化」的自相关 ──────────────────────
        # 未归一化的 R[k] 在长滞后处参与相乘的样本数少（N-k），幅值被系统性
        # 压低，argmax 因此偏爱短滞后。实测：50/60/80/100/120/140 Hz 的纯正弦
        # 全部被判成 lag_min=10 → f0=1600 Hz，而 180 Hz 以上才正确。也就是说
        # 「凡基频低于约 175 Hz 的声音，F0 一律被抬到 F0_MAX」，呼噜、低吼、
        # 男低音人声全部中招。除以重叠长度才是无偏估计。
        fl = len(f)
        acn = ac / np.arange(fl, 0, -1, dtype=np.float32)
        seg = acn[lag_min:lag_max]
        if len(seg) == 0:
            continue
        # 取「第一个显著局部峰」，而不是全局 argmax。
        # 纯正弦的自相关在每个整数倍周期处都接近 1，全局 argmax 会被浮点噪声
        # 带到 2×/3× 周期：实测 800 Hz 读成 400 Hz、1500 Hz 读成 500 Hz。
        # 从 lag_min 往后扫、取第一个达到 0.9×峰值 的局部极大，才是真实周期。
        vmax = float(seg.max())
        thr = 0.9 * vmax
        k = -1
        for kk in range(len(seg)):
            if seg[kk] < thr:
                continue
            if kk > 0 and seg[kk] < seg[kk - 1]:
                continue
            if kk < len(seg) - 1 and seg[kk] < seg[kk + 1]:
                continue
            k = kk
            break
        if k < 0:
            k = int(np.argmax(seg))
        # 峰落在搜索下界 = 真实周期长于 lag_max，即基频低于 F0_MIN。
        # 单调递减段的 seg[0] 天然最大，照抄就会输出 F0_MAX 这个伪造值。
        if k == 0:
            f0[i] = 0.0
            continue
        lag = lag_min + k
        # strength 仍用原始 R 计算：harmonicity 的阈值（0.45/0.60/0.75/
        # 0.80/0.85）是按这套数值标定的，换成归一化值会全部失真。
        strength[i] = float(ac[lag] / ac[0])
        # 强度不足视为无基频：静音/噪声下自相关没有明显峰
        if strength[i] < 0.32:
            f0[i] = 0.0
            continue
        # 抛物线插值提升基频分辨率（1 个采样点在 400 Hz 上是 30 Hz 误差）
        if 0 < k < len(seg) - 1:
            a, b, c = seg[k - 1], seg[k], seg[k + 1]
            den = (a - 2 * b + c)
            if abs(den) > 1e-9:
                lag = lag + 0.5 * (a - c) / den
        f0[i] = sr / lag if lag > 0 else 0.0
    return f0, strength


def _frame_arrays(x):
    """分帧并算频谱/能量，供切段前后各算一次。"""
    fr = frames_of(x)
    spec = np.abs(np.fft.rfft(fr, n=N_FFT, axis=1)).astype(np.float32) + 1e-10
    power = spec ** 2
    rms = np.sqrt(np.mean(fr.astype(np.float64) ** 2, axis=1))
    return fr, spec, power, rms


def _active_mask(rms_db):
    """有效发声帧：高于本段峰值 18 dB 以内，且不低于 -45 dBFS。"""
    act = rms_db > max(-45.0, float(rms_db.max()) - 18.0)
    if not act.any():
        act = np.ones_like(rms_db, dtype=bool)
    return act


def _longest_run(act):
    """最长连续有效段（帧索引区间）。"""
    best, cur = (0, 0), None
    for i, a in enumerate(act):
        if a and cur is None:
            cur = i
        elif not a and cur is not None:
            if i - cur > best[1] - best[0]:
                best = (cur, i)
            cur = None
    if cur is not None and len(act) - cur > best[1] - best[0]:
        best = (cur, len(act))
    return best


def features_of(x):
    """先切出有效发声段，再在段内统计特征。

    为什么必须先切段：0.96 s 的分析窗经常是「半段静音 + 半段叫声」。
    整窗统计时，峰均比、调制深度、谱平坦度都会被静音帧稀释，于是出现
    「喵叫开头判成吠叫」「呼噜判成吠叫」这类系统性误判（实测：7 次喵叫
    夹 4 次 bark，8 次呼噜夹 7 次 bark）。切段后同样的阈值立刻稳定。
    代价是每个窗多做一次分帧，实测约 1.5 ms，可忽略。
    """
    x = np.asarray(x, dtype=np.float32)
    if x.ndim > 1:
        x = x[:, 0]
    if len(x) < FRAME * 2:
        x = np.pad(x, (0, FRAME * 2 - len(x)))

    # ---- 第一遍：整窗，只用来定位发声段与统计「占比」类特征 ----
    _, _, _, rms0 = _frame_arrays(x)
    rms_db0 = 20 * np.log10(np.maximum(rms0, 1e-8))
    act0 = _active_mask(rms_db0)
    active_ratio = float(act0.mean())
    n_tail = max(2, len(act0) // 5)
    tail_active = float(act0[-n_tail:].mean())

    s, e = _longest_run(act0)
    if e - s >= 4:
        seg = x[max(0, s * HOP - FRAME // 2): min(len(x), e * HOP + FRAME)]
    else:
        seg = x
    if len(seg) < FRAME * 2:
        seg = x
    seg_dur = len(seg) / SR

    # ---- 第二遍：只在这个发声段内统计 ----
    fr, spec, power, rms = _frame_arrays(seg)
    mel = power @ MEL_FB.T
    logmel = np.log(np.maximum(mel, 1e-10))
    mfcc = logmel @ DCT.T

    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR).astype(np.float32)
    psum = power.sum(axis=1) + 1e-12
    centroid = (power * freqs).sum(axis=1) / psum
    bandwidth = np.sqrt((power * (freqs[None, :] - centroid[:, None]) ** 2).sum(axis=1) / psum)
    cum = np.cumsum(power, axis=1)
    rolloff = freqs[np.argmax(cum >= 0.85 * cum[:, -1:], axis=1)]
    flatness = (np.exp(np.mean(np.log(np.maximum(power, 1e-12)), axis=1))
                / (np.mean(power, axis=1) + 1e-12))
    flux = float(np.sqrt(((np.diff(spec, axis=0)) ** 2).sum(axis=1)).mean()) if len(spec) > 1 else 0.0

    rms_db = 20 * np.log10(np.maximum(rms, 1e-8))
    zcr = np.mean(np.abs(np.diff(np.signbit(fr).astype(np.int8), axis=1)), axis=1)
    act = _active_mask(rms_db)

    f0, harm = f0_track(seg)
    f0v = f0[f0 > 0]
    voiced = f0 > 0

    # 包络类特征必须用整窗：峰均比、脉冲数、调制深度描述的是
    # 「这一窗里声音有多起伏」，若只在发声段内统计，单次吠叫的峰均比
    # 会被压平（实测 6.35 → 1.2），吠叫就无法与喵叫区分。
    env = rms0 / (rms0.max() + 1e-9)
    peak_mean_ratio = float(rms0.max() / (rms0.mean() + 1e-9))
    onset = float(np.argmax(env) / max(1, len(env)))
    above = env > 0.35
    pulses = int(np.sum(np.diff(above.astype(np.int8)) == 1))

    # 20–45 Hz 幅度调制深度：呼噜的典型特征（周期约 25 Hz）
    mod_depth = 0.0
    if len(env) >= 8:
        e_ = env - env.mean()
        sp = np.abs(np.fft.rfft(e_, n=256))
        mfreqs = np.fft.rfftfreq(256, HOP / SR)
        band = (mfreqs >= 18) & (mfreqs <= 45)
        if band.any():
            mod_depth = float(sp[band].max() / (np.abs(sp).sum() + 1e-9))

    f0_slope = 0.0
    if len(f0v) >= 3:
        t = np.arange(len(f0v), dtype=np.float32)
        f0_slope = float(np.polyfit(t, f0v, 1)[0] / (np.mean(f0v) + 1e-9))

    # 音节率调制（1–12 Hz）：人声 3–8 Hz 有稳定音节节拍，动物长音没有
    am_freq, am_peak = 0.0, 0.0
    if len(env) >= 12:
        e_ = env - env.mean()
        sp = np.abs(np.fft.rfft(e_, n=512))
        fr_am = np.fft.rfftfreq(512, HOP / SR)
        band = (fr_am >= 1.0) & (fr_am <= 12.0)
        if band.any():
            k = int(np.argmax(sp[band]))
            am_freq = float(fr_am[band][k])
            am_peak = float(sp[band][k] / (np.abs(sp).sum() + 1e-9))

    c = mfcc[:, 1:6]
    mfcc_std = float(np.mean(np.std(c, axis=0))) if len(c) > 1 else 0.0

    f0_arch, desc_ratio = 0.0, 0.0
    if len(f0v) >= 4:
        f0_arch = float((f0v.max() - f0v.min()) / (np.median(f0v) + 1e-9))
        desc_ratio = float(np.mean(np.diff(f0v) < 0))

    # 浊音不足时把基频类特征归零：否则静音段会残留一个恒为 F0_MAX 的
    # 假基频（实测 1600 Hz），日志与前端都会看到不存在的数值
    if float(voiced.mean()) < 0.15:
        f0v = np.zeros(0, dtype=np.float32)
        f0_arch, desc_ratio, f0_slope = 0.0, 0.0, 0.0

    return {
        "rms_db": float(20 * np.log10(max(1e-8, float(np.sqrt(np.mean(seg ** 2)))))),
        "rms_max_db": float(rms_db.max()),
        "seg_dur": float(seg_dur),
        "peak_mean_ratio": peak_mean_ratio,
        "onset": onset,
        "pulses": pulses,
        "active_ratio": active_ratio,
        "tail_active": tail_active,
        "centroid": float(centroid[act].mean()),
        "bandwidth": float(bandwidth[act].mean()),
        "rolloff": float(rolloff[act].mean()),
        "flatness": float(flatness[act].mean()),
        "flux": flux,
        "zcr": float(zcr[act].mean()),
        "f0_med": float(np.median(f0v)) if len(f0v) else 0.0,
        "f0_min": float(np.percentile(f0v, 10)) if len(f0v) else 0.0,
        "f0_max": float(np.percentile(f0v, 90)) if len(f0v) else 0.0,
        "f0_slope": f0_slope,
        "f0_arch": f0_arch,
        "desc_ratio": desc_ratio,
        "harmonicity": float(harm[harm > 0].mean()) if np.any(harm > 0) else 0.0,
        "voiced_ratio": float(voiced.mean()),
        "mod_depth": mod_depth,
        "am_freq": am_freq,
        "am_peak": am_peak,
        "mfcc_std": mfcc_std,
        "logmel": logmel,
        "mfcc": mfcc,
    }


# ══════════════════════ PANNs 声音事件模型（侧车，HTTP） ══════════════════════
#
# 模型从前几版的「同进程 Cnn14」换成了「独立进程 MobileNetV2 int8」，原因有两条，
# 都是实测撞出来的：
#
# 1) **速度**。Cnn14 在 K1 上单段 1.5 s 窗口要 1529 ms（10 s 窗口 9357 ms），
#    而且 100% 占空比会把板温顶到 94℃、同板 YOLO 的 infer 从 175 ms 恶化到
#    2561 ms（11.8 倍），停掉服务 60 s 才回到 217 ms —— 因果已钉死。
#    换成 MobileNetV2（5.16 M 参数 = Cnn14 的 1/15.5）int8 后 p50 492 ms。
#
# 2) **指令集**。int8 量化 ONNX 的卷积内核在 cpu4-7（cluster1）上 run() 直接
#    SIGILL（core dumped，dmesg cause=2），而 session 创建却正常（931 ms）。
#    本服务常驻 4-7 不抢 AI 核，所以推理必须拆到独立进程绑 0-3。
#
# 换来的是：侧车挂了主链路照常工作（规则分类器是完整的一层，不是兜底），
# 代价是多一次 127.0.0.1 的 HTTP 往返（实测 <3 ms，可忽略）。
PANNS_CTX_SEC = 2.0       # 送给侧车的上下文长度。侧车自己取最响 0.5 s 再铺满 2 s，
                          # 所以这里只决定"看多长的一段历史"，不决定模型输入
PANNS_GATE_DB = -38.0     # 低于这个能量连侧车都不问（与规则分类器的能量门限一致）
# 规则判"噪声"时，还要多强才值得问模型。实测依据（8 个样本，见
# scripts/voice_pipeline_test.py）：
#   真实狗叫 -13.2 dBFS、单次吠叫 -15.0 → 规则都判 noise 0.30（=它自己说"不确定"）
#   环境底噪约 -52、静音 -71            → 规则判 silence/noise 且能量低
#   人声 -27.1                          → 规则判 human 0.88，根本不该进这个分支
# 取 -30 落在"底噪"和"叫声"之间。宁可多算一次（模型会判回 noise，无害），
# 不能让真实叫声漏掉——这是方案 A 最容易踩空的地方。
PANNS_TRIGGER_DB = -30.0
# 两次推理的最小间隔（秒）。这不是性能优化，是**热设计约束**：
# Cnn14 时代实测持续推理把板温顶到 94℃，触发 AI 核热降频，同板 YOLO 的 infer
# 从 175 ms 恶化到 2561 ms。现在单次只有 492 ms，占空比已降到 ~12%，
# 但这条间隔仍然保留 —— 它保护的是同一块板上的视觉链路，不是本服务自己。
PANNS_MIN_INTERVAL_SEC = 4.0
# 模型结论的保护期（秒）。模型 0.5 s 才回来一次，而规则每 0.48 s 就发布一次；
# 不加保护期的话，模型刚确认"这是吠叫"，下一窗规则立刻用 noise 0.30 把它盖掉，
# 确认结果在界面上活不过半秒，等于白算。
# 取 2.0 s：够覆盖 4 个规则窗，又不至于让叫声停了还挂着"吠叫"标签。
PANNS_HOLD_SEC = 2.0
# 「无宠物叫声」结论的回落确认窗数。见 _analyse 里的门控注释：
# 判 noise 的置信度恒为 0.40，低于 0.45 的防抖门限，所以历史上它永远发不出去，
# 一旦 latest 落到某个宠物类就再也回不来。给 silence/noise 留一条经过确认的
# 回落通路，取 3 窗 ≈ 1.44 s：短于 PANNS_HOLD_SEC 后残留的抖动，长于单帧毛刺。
QUIET_RELEASE_WINS = 3


class PannsRemote:
    """PANNs 轻量模型客户端（HTTP → panns_sidecar，:11442）。

    ═══ 为什么是 HTTP 客户端而不是在本进程里加载模型 ═══

    本服务常驻 cpu4-7（纯 Python，不抢视觉的 AI 核）。而 int8 量化 ONNX 的
    卷积内核在 cluster1 上 **一执行 run() 就 SIGILL**：
        taskset -c 4-7 python3 ... run(int8模型)
        → 非法指令（核心已转储），dmesg cause=2
    同一份模型在 0-3 上 p50 492 ms 正常。session 创建却都成功（931 ms），
    只在真正的推理内核上炸 —— 这条差异让问题很容易被误判成"模型文件坏了"。

    两条路都试过：把 petvoice 整个搬到 0-3（会跟 YOLO/LLM 抢 AI 核，
    视觉 fps 直接掉），或者把推理拆出去。选后者。

    拆出去还有一个额外好处：**侧车崩了不影响主链路**。规则分类器照常逐窗跑，
    只是少了"模型确认"这一层，11439 不会整条挂掉。

    输入约定：16 kHz float32 → int16 PCM 原样发走，重采样在侧车里做。
    板端没有 scipy，重采样逻辑只该存在一处，客户端不复制一份。
    """

    def __init__(self, url="http://127.0.0.1:11442", timeout=4.0):
        import urllib.request
        self.url = url.rstrip("/")
        self.timeout = timeout
        # 本机 http_proxy 会把 127.0.0.1 的请求发给代理，表现为莫名的 502。
        # 服务端和客户端都在板上时必须显式禁用代理。
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}))
        self.window = 2.0
        self.load_ms = 0.0
        self.last_ms = 0.0
        self.calls = 0
        self.fails = 0
        h = self._get("/health")
        self.window = float(h.get("win_sec", 2.0))
        self.load_ms = float(h.get("load_ms", 0))
        self.model = h.get("model", "?")
        self.threshold = float(h.get("threshold", 0.15))

    def _get(self, path):
        import json
        with self._opener.open(self.url + path, timeout=self.timeout) as r:
            return json.load(r)

    def classify(self, w):
        """w: float32 波形（16 kHz）。返回 (cls, conf, groups)。

        失败时抛异常，由调用方计入 fails 并跳过这一段 —— 绝不能让侧车的网络
        抖动把主分析循环拖住。
        """
        import json
        import urllib.request
        x = np.asarray(w, dtype=np.float32).ravel()
        pcm = np.clip(x * 32767.0, -32768, 32767).astype("<i2").tobytes()
        req = urllib.request.Request(
            f"{self.url}/classify?sr={SR}&nch=1", data=pcm,
            headers={"Content-Type": "application/octet-stream"})
        t = time.time()
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                d = json.load(r)
        except Exception:
            self.fails += 1
            raise
        self.last_ms = (time.time() - t) * 1000
        self.calls += 1
        return d["cls"], float(d["conf"]), d.get("groups", {})


# ══════════════════════════ 分类与状态映射 ══════════════════════════


class Classifier:
    """规则分类器（默认）或 ONNX 分类器（--model 存在时）。"""

    def __init__(self, model_path=None):
        self.sess = None
        self.mode = "rules"
        if model_path and os.path.exists(model_path):
            try:
                import onnxruntime as ort
                self.sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
                self.mode = "onnx"
            except Exception as e:                      # noqa: BLE001
                print(f"[warn] ONNX 模型加载失败，回退规则分类: {e}", flush=True)

    # ---- ① 声学门控 + ② 叫声细分 ----
    def classify(self, f):
        if self.sess is not None:
            return self._classify_onnx(f)
        return self._classify_rules(f)

    def _classify_onnx(self, f):
        lm = f["logmel"]
        # 归一化到 64×64（时间轴插值），与训练时的输入约定一致
        t = np.linspace(0, lm.shape[0] - 1, N_MEL)
        x = np.stack([np.interp(t, np.arange(lm.shape[0]), lm[:, i]) for i in range(lm.shape[1])], axis=1)
        x = ((x - x.mean()) / (x.std() + 1e-6)).astype(np.float32)[None, None, :, :]
        out = self.sess.run(None, {self.sess.get_inputs()[0].name: x})[0].ravel()
        e = np.exp(out - out.max())
        p = e / e.sum()
        idx = int(np.argmax(p))
        names = [CLS_SILENCE, CLS_HUMAN, CLS_NOISE] + PET_CLASSES
        return names[idx], float(p[idx]), {n: float(v) for n, v in zip(names, p)}

    def _classify_rules(self, f):
        """优先级链式判别，而不是单纯 argmax。

        为什么用链式：多个类别的打分天然会撞车（例如长嚎的谐波结构与喵叫
        几乎一样），argmax 会在 0.65 与 0.66 之间随机翻转，导致界面标签抖动。
        链式的好处是「先排除不可能」，每一步的排除理由都能在日志里核对。

        每一步的阈值都标了依据；阈值需要在真实样本上再标定，工具见
        collect_voice.py / calibrate_voice.py。
        """
        db = f["rms_max_db"]
        flat = f["flatness"]
        harm = f["harmonicity"]
        f0 = f["f0_med"]
        cen = f["centroid"]
        dur = f["seg_dur"]                        # 有效发声段时长（切段后实测）
        mod = f["mod_depth"]
        vr = f["voiced_ratio"]

        # ① 静音：绝对能量门限。实测本底约 -52 dBFS，说话/叫都在 -30 以上
        if db < -48.0:
            return CLS_SILENCE, 0.95, {CLS_SILENCE: 0.95}

        # ①b 能量门限：低于 -38 dBFS 一律不判宠物类。
        # 实测教训：安静时偶发杂波的峰均比可以很高，早期版本在 -42 dBFS 的
        # 杂波上判出「吠叫」，这正是纯规则分类器最需要的一道硬闸。
        if db < -38.0:
            return CLS_NOISE, 0.40, {CLS_NOISE: 0.40}

        # ①c 强势冲击音：必须放在人声门控之前。
        #
        # 这条是真实声学测试逼出来的。拿 PC 扬声器放犬吠、由板上 USB 麦克风
        # 拾音，f0 落到 191 Hz（正落在成人男声区间）、desc_ratio 只有 0.39，
        # 于是被人声门控吞掉——前端显示「人声」，真正的吠叫就漏报了。
        #
        # 为什么不能靠「人声要求持续音」来区分：试过给 ② 加
        # `seg_dur >= 0.30 or 峰均比 <= 2.6`，人声召回从 83% 直接掉到 45%
        # （噪声下人声的有效段被切得很短）。改成人声门控前面插一条高门槛的
        # 冲击音判定，代价小得多。
        #
        # 门槛取 2.8 / 0.35 是实测定的：真实人声峰均比 1.14、犬吠 3.06；
        # 哈气 2.20、喵叫 1.58 都在门槛之下，不会被抢。
        # f0 <= 600 是补的：尖叫同样是「短 + 冲击」，不加这条会被抢走，
        # 实测尖叫召回因此掉 12 个点。犬吠基频 190~400，尖叫在 750 以上。
        if (dur <= 0.35 and f["peak_mean_ratio"] >= 2.8
                and harm >= 0.60 and mod < 0.040 and f0 <= 600):
            return "bark", 0.72, {"bark": 0.72}

        # ② 人声：浊音连续 + 基频在成人语音区间 + 谱重心不低 + 无脉冲调制
        #    + 基频稳定（desc_ratio 低）
        #
        # 这一版用 voiced_ratio / centroid / desc_ratio 替换了原来的
        # 「音节率 + MFCC 起伏」。依据是 400 条样本的实测分布：人声 voiced_ratio
        # 稳定 1.00，噪声只有 0.38、哈气 0.07；谱重心人声 1378、低吼只有 463。
        # 原来的 am_freq 中位数实测只有 2.34 Hz（低于阈值 2.5），漏掉六成人声——
        # 「音节率落在 2.5~8 Hz」这种连续量做门控本来就脆。
        #
        # desc_ratio <= 0.70 是后补的：把谱重心门槛从 800 降到 350 之后，
        # 噪声下的吠叫（f0 估计掉到 330 以下）被当成男声吞掉了 11 条。
        # 人声基频稳定（desc 0.54），犬吠快速下滑（desc 1.00），这条一分就开。
        if (vr >= 0.85 and 95 <= f0 <= 330 and mod < 0.020
                and cen >= 350 and flat < 0.08 and f["desc_ratio"] <= 0.70):
            return CLS_HUMAN, 0.88, {CLS_HUMAN: 0.88}

        # ③ 呼噜：谱重心极低是它最干净的判据（实测 103 Hz，其余类别 >= 463）
        #    呼噜的基频就是 20~30 Hz 的脉冲率本身，低于 F0_MIN，所以 f0 会饱和到
        #    F0_MAX（1600），绝不能拿 f0 判它。真正稳的是「能量全在低频 +
        #    大量脉冲」这两条同时成立。
        if cen <= 320 and f["pulses"] >= 3 and mod >= 0.030:
            return "purr", 0.84, {"purr": 0.84}

        # ④ 哈气：宽带噪声为主（平坦度高）且几乎没有谐波结构
        if flat >= 0.15 and harm < 0.45 and cen >= 2500:
            return "hiss", 0.76, {"hiss": 0.76}

        # ⑤ 尖叫：高基频 + 高能量 + 谐波清晰（疼痛信号，优先级靠前）
        #    补上 harm >= 0.80：呼噜的 f0 会饱和到 1600，只用 f0 判会把呼噜
        #    当尖叫；而谱重心 103 与 1805 差了两个数量级，加一条就彻底分开。
        if f0 >= 750 and harm >= 0.80 and db > -30 and cen >= 1200:
            return "scream", 0.80, {"scream": 0.80}

        # ⑥ 低吼：低频 + 持续 + 谐波中等（谐噪比介于呼噜与长嚎之间）+ 谱重心低
        #    原来写 mod >= 0.05 太紧：实测低吼的 mod 中位数正好 0.05，
        #    一半样本卡在阈值上，40 条里只认出 1 条。
        if (0.40 <= harm <= 0.78 and cen <= 900 and flat < 0.12
                and dur >= 0.45 and mod >= 0.025):
            return "growl", 0.70, {"growl": 0.70}

        # ⑦ 吠叫：爆发式**短**音。判定必须是「短 + 基频陡降」，不能再用
        #    「pulses >= 2」兜底——喵叫的 pulses 中位数也是 2，那条兜底
        #    等于把一半喵叫送进了吠叫（实测 40 条喵叫里 18 条判成吠叫）。
        #    短样本的唯一稳定特征就是 seg_dur：吠叫 0.28 s、喵叫 0.70 s。
        if (dur <= 0.48 and f["peak_mean_ratio"] >= 1.9
                and f["desc_ratio"] >= 0.70 and harm >= 0.45 and mod < 0.040):
            return "bark", 0.80, {"bark": 0.80}

        # ⑧ 长嚎：单调下滑且轨迹拱高小。desc_ratio 阈值从 0.85 放宽到 0.62——
        #    实测长嚎的中位数是 0.83，硬卡 0.85 会把一半长嚎漏掉；
        #    防止误吞喵叫靠的是 f0_arch < 0.55（喵叫实测 0.86）。
        if (dur >= 0.60 and f0 >= 240 and harm >= 0.85
                and f["desc_ratio"] >= 0.55 and f["f0_arch"] < 0.55):
            return "howl", 0.78, {"howl": 0.78}

        # ⑨ 喵叫：基频轨迹呈明显拱形（先升后降），这是猫叫最稳定的特征
        if (280 <= f0 <= 1100 and f["f0_arch"] >= 0.60
                and harm >= 0.75 and dur >= 0.25):
            return "meow", 0.84, {"meow": 0.84}

        # ⑩ 兜底：归噪声，置信度给低——这类样本正是应该被采集标定的对象
        return CLS_NOISE, 0.30, {CLS_NOISE: 0.30}


class StateMapper:
    """把「单帧分类」提升为「时序状态」。

    单帧只能说明「它在叫」，加上重复次数与持续时长才能说明「它在焦虑」。
    这一层是纯规则，可解释、可复现，也是 LLM 不该碰的部分。
    """

    def __init__(self):
        self.recent = deque(maxlen=12)      # 最近 12 个窗口（约 6 秒）

    def push(self, cls, conf, f, ts):
        if cls in PET_CLASSES and conf >= 0.45:
            self.recent.append((ts, cls, conf, f["seg_dur"]))

    def map(self, cls, conf, f, ts):
        if cls in (CLS_SILENCE, CLS_NOISE):
            return "quiet", 0.9, {"repeat": 0, "dur": 0.0}
        if cls == CLS_HUMAN:
            return "talking", 0.8, {"repeat": 0, "dur": 0.0}

        now = ts
        window = [e for e in self.recent if now - e[0] <= 3.0]
        repeat = sum(1 for e in window if e[1] == cls)
        dur = sum(e[3] for e in window)

        base, p = STATE_RULES.get(cls, [("quiet", 0.5)])[0]

        # 规则修正：这些修正全部来自行为学常识，不含模型
        if cls == "bark" and repeat >= 2:            # 连续吠叫 → 警戒权重上调
            base, p = "alert", min(0.95, 0.60 + 0.1 * repeat)
        elif cls == "howl" and dur >= 1.2:           # 持续长嚎 → 分离焦虑
            base, p = "anxiety", min(0.95, 0.60 + 0.15 * dur)
        elif cls == "meow" and repeat >= 2:          # 连续短喵 → 求食/呼唤
            base, p = "hunger", 0.70
        elif cls == "scream":                        # 尖叫优先级最高
            base, p = "pain", 0.80
        elif cls == "hiss":
            base, p = "threat", 0.85

        return base, float(p), {"repeat": repeat, "dur": round(dur, 2)}


# ══════════════════════════ LLM 建议（可选） ══════════════════════════


def llm_advice(pet, cls, state, base_advice, gateway="http://127.0.0.1:11438",
               timeout=9.0):
    """让本地 0.6B 把**已经判定好的建议**改写成一句更像人说的话。

    为什么不直接让它「判断并给建议」：实测 0.6B 会把状态与处置错配
    （把「警戒」答成「马上回家喂食」，把「吠叫」答成「带猫出门散心」）。
    它能做的是改写，不能做的是判断——这与人设测试的结论一致。
    所以这里只让它润色，判断留在 StateMapper 里。

    提示词压到 40 token 以内：实测 prefill 27.4 tok/s，长提示会拖到十几秒。
    """
    prompt = ("把下面这句宠物监护提示改写得更口语、更短，只改说法，"
              "不要改变意思，不要新增任何内容：" + base_advice)
    body = json.dumps({
        "model": "qwen3:0.6b",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": 48,
    }).encode("utf-8")
    req = urllib.request.Request(
        gateway + "/v1/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            txt = data["choices"][0]["message"]["content"].strip()
            return txt or None
    except Exception:                                  # noqa: BLE001
        return None


# 与状态矛盾的行为词：出现即判为「模型在自作主张」，回退规则建议
_ADVICE_BLACKLIST = {
    "alert": ("喂", "吃", "出门", "散步"),
    "threat": ("摸", "抱", "靠近"),
    "anxiety": ("喂", "吃"),
    "comfort": ("喂", "出门"),
    "fear": ("靠近", "抱"),
    "hunger": (),
    "pain": (),
    "play": (),
}


def advice_acceptable(text, state, base):
    """改写结果的守门员。四条全过才采用，否则回退原文。

    0.6B 的典型故障是「加戏」：凭空补一个动作、补一个数字、把一句话写成两段。
    这些都能用廉价的结构化检查拦住，不需要再上第二个模型。
    """
    if not text:
        return False
    t = text.strip().strip('“”"')
    if not (6 <= len(t) <= 46):
        return False
    if t.count("。") > 1:
        return False
    for w in ("宠物猫发出", "宠物狗发出", "判断为", "改写"):
        if w in t:                      # 把提示词吐出来了
            return False
    for w in _ADVICE_BLACKLIST.get(state, ()):
        if w in t and w not in base:
            return False
    # 不许凭空造数字（原文里没有的数字，一律视为编造）
    import re as _re
    if set(_re.findall(r"\d+", t)) - set(_re.findall(r"\d+", base)):
        return False
    return True


# ══════════════════════════ 采集与主循环 ══════════════════════════


class _InjectedStream:
    """把内存里的 PCM 伪装成 arecord 的 stdout，使主循环无需分支。"""

    def __init__(self, engine):
        self.e = engine

    def read(self, n):
        pcm = self.e._inject_pcm
        pos = self.e._inject_pos
        n_samp = n // 2
        idx = (pos + np.arange(n_samp)) % len(pcm)
        self.e._inject_pos = (pos + n_samp) % len(pcm)
        return pcm[idx].astype(np.int16).tobytes()

    def poll(self):
        return None

    def terminate(self):
        return


class VoiceEngine:
    def __init__(self, device, pet="auto", model=None, use_llm=True, verbose=False,
                 inject=None, panns_url=None, panns_trigger="confirm"):
        self.device = device
        self.pet = pet
        self.verbose = verbose
        self.use_llm = use_llm
        self.inject = inject
        self.clf = Classifier(model)
        # PANNs 是**异步**的：单段 1.5 秒，而分析节拍 0.48 秒。同步调用会把
        # arecord 的管道读空（丢音频），所以主线程只投递，独立线程算完再回填。
        self.panns = None
        self.panns_trigger = panns_trigger
        self._panns_url = panns_url
        # 重连节流。为什么要重连、而不是启动时连一次就定死：
        # 实测冷启动时 petvoice 在 23:56:40 起、panns-sidecar 在 23:56:39 起，
        # 但侧车要 943 ms 加载模型+预热才 listen，于是 petvoice 第一次握手被拒
        # （Connection refused），此后 panns 永久 off —— 自检 PASS、界面正常、
        # 只是模型确认这一层静默消失，非常难发现。
        self._panns_next_try = 0.0
        self._panns_thread_on = False
        self._panns_q = deque(maxlen=1)     # 只留最新一段，绝不积压
        self._panns_cv = threading.Condition()
        self._panns_done = 0
        self._panns_drop = 0
        self._panns_ms = 0.0
        self._panns_hold = None      # (cls, conf, ts) 模型刚确认的结论
        self._pub_lock = threading.Lock()   # mapper 有内部状态，不能并发进
        self.mapper = StateMapper()
        self.lock = threading.Lock()
        self.paused = False
        self.proc = None
        self.frames = 0
        self.events = deque(maxlen=50)
        self._advice_cache = {}
        self._last_llm = 0.0
        self._hist = deque(maxlen=3)
        self._quiet_run = 0      # 连续「无宠物叫声」窗计数，用于从宠物类回落
        self.latest = {
            "ok": True, "ts": 0, "cls": CLS_SILENCE, "cls_cn": "静音",
            "state": "quiet", "state_cn": "安静", "conf": 0.0,
            "pet": pet, "advice": STATE_ADVICE["quiet"], "source": "规则",
            "features": {}, "repeat": 0, "dur": 0.0,
        }
        self._stop = False
        self._t = None
        # 必须放在 __init__ 末尾：_try_panns 会启动 _panns_loop 线程，
        # 而该线程第一行就读 self._stop。放在前面会抛
        # AttributeError: 'VoiceEngine' object has no attribute '_stop'，
        # 线程静默死掉、panns 却显示 on（2026-11-06 实测踩到）。
        if panns_url:
            self._try_panns()

    def _try_panns(self):
        """尝试连接侧车；失败则记账并在 30 s 后重试。返回当前的 self.panns。"""
        if not self._panns_url or self.panns is not None:
            return self.panns
        now = time.time()
        if now < self._panns_next_try:
            return None
        self._panns_next_try = now + 30.0
        try:
            self.panns = PannsRemote(self._panns_url)
        except Exception as e:                              # noqa: BLE001
            # 侧车没起来不该拦住主链路：规则分类器是完整的一层。
            print(f"[warn] PANNs 侧车暂不可用（{self._panns_url}），30 s 后重试，"
                  f"期间只用规则分类: {e}", flush=True)
            return None
        if not self._panns_thread_on:
            threading.Thread(target=self._panns_loop, daemon=True,
                             name="panns").start()
            self._panns_thread_on = True
        print(f"[voice] PANNs 侧车就绪 {self.panns.model} "
              f"加载 {self.panns.load_ms:.0f} ms 窗口 {self.panns.window:.1f}s "
              f"阈值 {self.panns.threshold} 触发={self.panns_trigger}", flush=True)
        return self.panns

    # ---- 音频读取：arecord → 原始 PCM → float32 ----
    def _open_stream(self):
        if self.inject:
            # 注入模式：用 wav 循环喂数据，替代麦克风。
            # 用于无宠物在场的演示与回归测试——链路（分析→状态→建议→HTTP）
            # 与真实采集完全一致，只有数据来源不同。
            self._inject_pcm = self._load_wav_pcm(self.inject)
            self._inject_pos = 0
            self.proc = _InjectedStream(self)
            if self.verbose:
                print(f"[voice] 注入模式: {self.inject} "
                      f"({len(self._inject_pcm)/SR:.2f}s 循环)", flush=True)
            return
        cmd = ["arecord", "-D", self._plugin_device(self.device),
               "-f", "S16_LE", "-r", str(SR),
               "-c", "1", "-t", "raw", "-q"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, bufsize=0)
        if self.verbose:
            print(f"[voice] arecord 启动: {' '.join(cmd)}", flush=True)

    @staticmethod
    def _plugin_device(device):
        """给采集设备套一层 plug:，让 ALSA 负责重采样。

        为什么必须这么做：板上的麦克风是 USB 声卡（card 0），原生只支持
        44100 Hz；而 dsnoop 不做采样率转换，`arecord -r 16000` 会被静默
        降级成 44100，又因为这里用 -t raw（没有 wav 头），服务端完全察觉
        不到——整条频域特征会拿 16000 去解释 44100 的样本，所有频率整体
        偏高 2.76 倍。实测真实环境里谱重心只有 29 Hz、基频恒为 1600 Hz，
        就是这个 bug 的直接表现，而合成 wav 回归测试（数据本身就是 16k）
        永远暴露不出来。

        plug: 由 ALSA 完成重采样，实测能稳定交出 16000 Hz。
        """
        if "plug" in device:
            return device
        return f"plug:{device}"

    @staticmethod
    def _load_wav_pcm(path):
        with wave.open(path, "rb") as w:
            sr, ch = w.getframerate(), w.getnchannels()
            raw = w.readframes(w.getnframes())
        x = np.frombuffer(raw, dtype=np.int16)
        if ch > 1:
            x = x.reshape(-1, ch)[:, 0]
        if sr != SR:
            t = np.linspace(0, len(x) - 1, int(len(x) * SR / sr))
            x = np.interp(t, np.arange(len(x)), x.astype(np.float32)).astype(np.int16)
        # 每轮之间插 1.2 s 静音，让状态机看到「叫声 → 间隔 → 再叫」的节奏，
        # 否则连续循环会被判成一次超长发声，重复计数失真
        gap = np.zeros(int(SR * 1.2), dtype=np.int16)
        return np.concatenate([x, gap])

    def _raw_read(self, n):
        """统一读取入口：真实 arecord 走 proc.stdout，注入模式走 _InjectedStream.read。"""
        src = getattr(self.proc, "stdout", self.proc)
        return src.read(n)

    def _read(self, nbytes):
        buf = b""
        while len(buf) < nbytes and not self._stop:
            chunk = self._raw_read(nbytes - len(buf))
            if not chunk:
                break
            buf += chunk
        if len(buf) < nbytes:
            return None
        return np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0

    def start(self):
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        self._open_stream()
        win = int(WIN_SEC * SR)
        hop = int(HOP_SEC * SR)
        # 环形缓冲：每轮只读一个 hop（0.48 s），从缓冲区尾部取一个完整窗做分析。
        # 早期实现每轮读满一个窗再丢弃，实际只有 0.7 次/秒，状态机的
        # 「3 秒内叫了几次」完全失真（实测 2026-11-02）。
        buf = np.zeros(0, dtype=np.float32)
        while not self._stop:
            if self.paused:
                time.sleep(0.1)
                continue
            x = self._read(hop * 2)          # int16 → 2 字节/样本
            if x is None:
                time.sleep(0.2)
                if self.inject:
                    continue
                if self.proc.poll() is not None:
                    self._open_stream()      # 掉线自愈
                continue
            # 规则分类只吃 win（0.96 s），但 PANNs 需要更长上下文来找最响段，
            # 所以缓冲按需要留长一点，_analyse 里再按各自长度切。
            keep = max(win, int(SR * PANNS_CTX_SEC)) if self.panns is not None else win
            buf = np.concatenate([buf, x])[-keep:]
            if len(buf) < win:
                continue
            try:
                self._analyse(buf.copy())
            except Exception as e:           # noqa: BLE001
                print(f"[voice] 分析异常: {e}", flush=True)
            # 注入模式不受采集速率约束，必须自己按实时节拍走，
            # 否则「3 秒内叫了几次」这类时序判据会全部失真
            if self.inject:
                time.sleep(HOP_SEC)

    def _analyse(self, x):
        self.frames += 1
        ts = time.time()
        # 规则特征固定只看最近 0.96 s（阈值都是按这个窗标定的）；
        # x 可能更长（给 PANNs 的上下文），所以这里显式截尾。
        win = int(SR * WIN_SEC)
        f = features_of(x[-win:])
        cls, conf, scores = self.clf.classify(f)

        # 有实际声音就投递给 PANNs 线程（非阻塞）。它在约 1.5 秒后把结论回填，
        # 期间界面先用规则结果。绝不能同步等模型：单段 1.5 秒会把 arecord
        # 管道读空（分析节拍才 0.48 秒），后面整段音频直接丢。
        # 传的是整段上下文 x，让模型自己找最响的 0.5 s（切好的碎片喂不动它）。
        # ── 方案 A：并联赛跑 ────────────────────────────────────────
        # 规则分类器每窗都跑（0.48 s 一次，纯 numpy，几毫秒），负责它真正擅长的
        # 两件事：**排除静音、排除人声**（实测人声 0.88、静音 0.95，都很准）。
        # 剩下的"有声音但说不清是什么"，才是交给模型的。
        #
        # ⚠ 这里不能用"规则判出宠物类才送"。实测 8 个样本：
        #   real_dog 真狗叫  → 规则判 noise 0.30  ← 真叫声，纯 confirm 会漏
        #   02_bark_single  → 规则判 noise 0.30  ← 会漏
        #   01_meow/03_bark/04_hiss/05_howl/08_purr → 规则判得对，会送
        # 也就是说规则对真实叫声的召回并不好（它自己给 0.30 就是在说"不确定"），
        # 拿它当硬筛子等于让模型永远没机会纠正 —— 那就失去了并联的意义。
        #
        # 所以判据是「宠物类 或（判 noise 但能量够强）」：
        #   静音 -71 / 底噪约 -52 → energy 不够，不送（占空比保住）
        #   人声 -27.1            → 类别是 human，不进这个分支（不送）
        #   真狗叫 -13.2          → 送，模型给出 bark 0.60
        # 实测 8/8 宠物样本全部送检，静音与人声全部不送。
        #
        # 为什么必须保住占空比：模型跑在 cpu0-3，和 YOLO/LLM 抢 AI 核。
        # Cnn14 时代 100% 占空比把板温顶到 94℃、YOLO infer 恶化 11.8 倍，
        # 这个教训写进了 PANNS_MIN_INTERVAL_SEC 的注释。
        if self._panns_url and self.panns is None:
            self._try_panns()               # 侧车晚于本服务启动 / 中途重启的自愈
        if self.panns is not None and f["rms_max_db"] > PANNS_GATE_DB:
            want = cls in PET_CLASSES or (
                cls == CLS_NOISE and f["rms_max_db"] > PANNS_TRIGGER_DB)
            if self.panns_trigger == "any" or want:
                self._submit_panns(ts, x, f)

        # ── 模型结论保护期 ───────────────────────────────────────────
        # 必须放在"置信度不足就 return"**之前**，否则不生效：规则判 noise 的
        # 置信度恒为 0.30 < 0.45，会被那道门直接挡掉并原样返回，而保护逻辑
        # 根本没机会执行。实测症状就是模型刚发布 bark 0.55、界面刚亮起
        # "吠叫/警觉"，下一个 0.48 s 的规则窗立刻把它改回"环境噪声"。
        h = self._panns_hold
        if (h and time.time() - h[2] < PANNS_HOLD_SEC
                and cls in (CLS_SILENCE, CLS_NOISE)):
            return

        # 置信度不足时不推翻上一条结论，避免界面抖动。
        #
        # 但**必须给「没有宠物叫声」的结论留一条回落通路**，否则一次误判会把
        # latest 永久钉死。这是 2026-11-07 实测到的真实事故：
        #   安静房间实测底噪 rms_max_db ≈ -39 dBFS（USB 麦克风 + plug 重采样，
        #   不是注释里假设的 -52），落在 silence 门限(-48)之上、noise 门限(-38)
        #   之下 → 每一窗都判 noise 0.40；而 0.40 < 0.45，且 latest 已是 bark，
        #   于是每帧都在这条 return 上掉头，最新结论直接冻结。
        #   症状：engine.frames 一直涨（1359）、latest.frames 卡死（963），
        #   接口把 3 分钟前那一声 bark 反复吐给前端，界面永远显示「吠叫·警戒」。
        # 所以 silence/noise 这类「确认没有宠物叫声」的结论，连续
        # QUIET_RELEASE_WINS 窗就允许发布回落；宠物类与人声仍按原规则防抖。
        if conf < 0.45 and self.latest.get("cls") not in (CLS_SILENCE,):
            if cls in (CLS_SILENCE, CLS_NOISE):
                self._quiet_run += 1
                if self._quiet_run < QUIET_RELEASE_WINS:
                    return
            else:
                return
        else:
            self._quiet_run = 0

        # 三窗多数表决（防抖）：取最近 3 个分析窗的众数，票数 ≥2 才发布。
        # 依据是实测：声音起止的过渡窗（0.96 s 窗里半段静音 + 半段叫声）
        # 特征会被两种声音污染，单窗判定必然出现「喵叫开头判成吠叫」这类跳变
        # （实测 7 次喵叫里夹 4 次 bark）。多数表决后跳变消失，代价是首次
        # 判定延后约 0.5 s——对监护场景可接受。
        #
        # 顺序很关键：必须先投票、再算状态。早期版本先 map() 后投票，
        # 结果发布出去的 cls 是投票结果、state 却来自原始帧——实测出现过
        # 「cls=环境噪声 配 state=舒适，建议写『它很放松』」，两者根本不是
        # 同一帧的结论。置信度也必须跟着胜出的类别走，否则会出现
        # 「cls=noise、conf=0.84（其实是呼噜的 0.85）」这种自相矛盾。
        self._hist.append((cls, conf))
        if len(self._hist) < 2:
            return
        cls_vote, votes = Counter(c for c, _ in self._hist).most_common(1)[0]
        if votes < 2:
            return
        cls = cls_vote
        conf = max(cf for c, cf in self._hist if c == cls_vote)

        return self._publish(ts, cls, conf, f, "规则")

    def _publish(self, ts, cls, conf, f, cls_source="规则"):
        """把一次分类结论落成 latest / events。规则线程和 PANNs 线程共用。

        两条通道都会调它，所以 mapper 的 push/map 必须在 _pub_lock 里串行——
        StateMapper 有 repeat 计数等内部状态，并发会算出自相矛盾的建议。
        """
        with self._pub_lock:
            self.mapper.push(cls, conf, f, ts)
            state, p, extra = self.mapper.map(cls, conf, f, ts)

            # 宠物类型：未指定时按叫声反推（吠/嚎/吼→犬，喵/呼噜/哈气→猫）
            pet = self.pet
            if pet == "auto":
                pet = ("dog" if cls in ("bark", "howl", "growl")
                       else "cat" if cls in ("meow", "purr", "hiss", "scream")
                       else "auto")

            base = STATE_ADVICE.get(state, STATE_ADVICE["quiet"])
            advice, source = base, "规则"
            if self.use_llm and cls in PET_CLASSES and conf >= 0.55:
                advice, source = self._advised(state, base, pet, cls)

            rec = {
                "ok": True, "ts": ts,
                "cls": cls, "cls_cn": CLS_CN.get(cls, cls),
                "state": state, "state_cn": STATE_CN.get(state, state),
                "conf": round(conf, 3), "pet": pet,
                "advice": advice, "source": source,
                "cls_source": cls_source,
                "repeat": extra["repeat"], "dur": extra["dur"],
                "frames": self.frames,
                # seg_wave 是 numpy 数组，不能进 JSON；logmel/mfcc 太大也排除
                "features": {k: (round(v, 3) if isinstance(v, float) else v)
                             for k, v in f.items()
                             if k not in ("logmel", "mfcc", "seg_wave")},
            }
            with self.lock:
                self.latest = rec
            if cls in PET_CLASSES and conf >= 0.45:
                self.events.append(rec)
        if self.verbose:
            print(f"[voice] {cls:6s} {conf:.2f} state={state:9s} "
                  f"f0={f['f0_med']:6.1f} flat={f['flatness']:.2f} "
                  f"db={f['rms_max_db']:6.1f} src={cls_source} advice={advice}",
                  flush=True)
        return rec

    # ---- PANNs 异步通道 ----
    def _submit_panns(self, ts, wave, f):
        """投递一段给模型线程。队列 maxlen=1，新段直接顶掉没算完的旧段——
        积压只会让结论越来越滞后，不如只算最新的。
        """
        with self._panns_cv:
            if len(self._panns_q) >= 1:
                self._panns_drop += 1
            self._panns_q.append((ts, wave, f))
            self._panns_cv.notify()

    def _panns_loop(self):
        last_end = 0.0                      # 上一段推理结束的时刻，用于热节流
        while not self._stop:
            with self._panns_cv:
                while not self._panns_q and not self._stop:
                    self._panns_cv.wait(timeout=0.5)
                if self._stop:
                    return
                ts, wave, f = self._panns_q.pop()
                self._panns_q.clear()
            # 热节流：距上一段算完不足 PANNS_MIN_INTERVAL_SEC 就丢掉本段。
            # 不能直接 continue 空转（会变成忙等），先睡够剩下的间隔再取新段，
            # 这样永远只算"醒来时最新的那一段"，不会补算中间积压。
            gap = time.time() - last_end
            if gap < PANNS_MIN_INTERVAL_SEC:
                self._panns_drop += 1
                with self._panns_cv:
                    self._panns_cv.wait(timeout=PANNS_MIN_INTERVAL_SEC - gap)
                continue
            try:
                cls, conf, scores = self.panns.classify(wave)
            except Exception as e:                          # noqa: BLE001
                print(f"[voice] PANNs 推理异常: {e}", flush=True)
                continue
            last_end = time.time()
            self._panns_ms = self.panns.last_ms
            self._panns_done += 1
            if self.verbose:
                print(f"[voice] PANNs -> {cls} {conf:.2f} "
                      f"({self._panns_ms:.0f}ms, ctx={len(wave)/SR:.1f}s)",
                      flush=True)
            self._apply_panns(ts, cls, conf, f)

    def _apply_panns(self, ts, cls, conf, f):
        """模型结论回填。只在「它确实是新结论」时才覆盖，避免旧结果盖掉新状态。"""
        with self.lock:
            cur = self.latest
        if ts < cur.get("ts", 0.0) - 3.0:
            return                                  # 过期，丢弃
        if cls in (CLS_SILENCE, CLS_NOISE):
            # 模型没听出宠物类，而规则原本也不是宠物类 -> 不动（静音/噪声规则判得更稳）
            if cur.get("cls") not in PET_CLASSES:
                return
            # 规则说是叫声、模型说不是：保留规则结论，但如实标注未经模型确认
            with self.lock:
                self.latest = dict(cur, cls_source="规则(模型未确认)")
            return
        self._panns_hold = (cls, conf, time.time())
        self._publish(ts, cls, conf, f, "panns(MobileNetV2)")

    def _advised(self, state, base, pet, cls):
        """取建议：缓存 → LLM（节流）→ 守门 → 回退。返回 (文本, 来源)。

        节流是必须的：分析节拍约 2 次/秒，若不节流，一次连续吠叫会把网关
        打爆，反而拖垮 LLM 通道（0.6B 单请求 4 秒，排队会越排越长）。

        ═══ 2026-11-06 实测修掉的一个真实事故 ═══
        原实现只在"LLM 输出通过守门"时才写缓存。一旦守门连续拒绝（模型给出
        与状态不符的建议），就会变成**每 6 秒打一次、每次 5 秒**的循环：
            [gate] LLM 4978 ms / 516 ms / 5339 ms ...  每 4~5 秒一条
        实测 ollama 因此常驻 342% CPU（8 核占 3.4 核），把 AI 核吃满，
        同板视觉 fps 从 6.6 掉到 0.53、PANNs 侧车从 492 ms 涨到 965 ms。
        被拒也是"已知结论"，必须缓存——只是用更短的 TTL（60 s）以便早日重试。
        """
        now = time.time()
        hit = self._advice_cache.get(state)
        if hit:
            text, ts, ok = hit
            if now - ts < (180.0 if ok else 60.0):
                return text, ("本地模型" if ok else "规则")
        if now - self._last_llm < 6.0:
            return base, "规则"
        self._last_llm = now
        got = llm_advice(pet, cls, state, base)
        if got and advice_acceptable(got, state, base):
            self._advice_cache[state] = (got, now, True)
            return got, "本地模型"
        # 被拒 / 调用失败：缓存"这次不行"，否则会以 6 s 的节奏反复打 LLM
        self._advice_cache[state] = (base, now, False)
        return base, "规则"

    def stop(self):
        self._stop = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


# ══════════════════════════ HTTP 接口 ══════════════════════════


def make_handler(engine):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            p = self.path.split("?")[0]
            if p == "/health":
                with engine.lock:
                    self._send(200, {
                        "ok": True, "device": engine.device, "frames": engine.frames,
                        "mode": engine.clf.mode, "paused": engine.paused,
                        "events": len(engine.events),
                        "last": engine.latest.get("cls"),
                        "cls_source": engine.latest.get("cls_source"),
                        # PANNs 通道状态：off / 已跑多少段 / 上次耗时 / 被顶掉多少段
                        "panns": ("off" if engine.panns is None else {
                            "window_sec": engine.panns.window,
                            "calls": engine.panns.calls,
                            "last_ms": round(engine._panns_ms, 0),
                            "dropped": engine._panns_drop,
                        }),
                    })
            elif p == "/v1/voice/latest":
                with engine.lock:
                    self._send(200, engine.latest)
            elif p == "/v1/voice/events":
                with engine.lock:
                    self._send(200, {"ok": True, "events": list(engine.events)[-20:]})
            else:
                self._send(404, {"ok": False, "reason": "not_found"})

        def do_POST(self):
            p = self.path.split("?")[0]
            if p == "/v1/voice/pause":
                engine.paused = True
                self._send(200, {"ok": True, "paused": True})
            elif p == "/v1/voice/resume":
                engine.paused = False
                self._send(200, {"ok": True, "paused": False})
            else:
                self._send(404, {"ok": False, "reason": "not_found"})

        def log_message(self, fmt, *args):
            return

    return H


# ══════════════════════════ 离线验算 ══════════════════════════


def run_wav(path, pet="auto", model=None):
    """离线验算：把 wav 按 0.48 s 节拍切窗跑一遍，打印每窗结论。

    这条路径用于标定与回归，不启动 HTTP，不碰麦克风。
    """
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch)[:, 0]
    if sr != SR:
        # 最近邻重采样，够用且不引入依赖
        t = np.linspace(0, len(x) - 1, int(len(x) * SR / sr))
        x = np.interp(t, np.arange(len(x)), x).astype(np.float32)

    clf = Classifier(model)
    win, hop = int(WIN_SEC * SR), int(HOP_SEC * SR)
    # 短于一个分析窗的样本（如单次吠叫 0.2 s）必须补零，否则一窗都跑不到
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    print(f"# {os.path.basename(path)}  {len(x)/SR:.2f}s  mode={clf.mode}")
    i = 0
    while i + win <= len(x):
        f = features_of(x[i:i + win])
        cls, conf, _ = clf.classify(f)
        print(f"  t={i/SR:5.2f}s  {cls:8s} {conf:.2f}  f0={f['f0_med']:6.1f}Hz "
              f"flat={f['flatness']:.3f} harm={f['harmonicity']:.2f} "
              f"db={f['rms_max_db']:6.1f} cen={f['centroid']:6.0f} "
              f"pulse={f['pulses']} mod={f['mod_depth']:.3f}")
        i += hop
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="dsnoop0")
    ap.add_argument("--port", type=int, default=11439)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--pet", default="auto", choices=["auto", "cat", "dog"])
    ap.add_argument("--model", default=None, help="可选 ONNX 分类模型（1×1×64×64 log-mel）")
    ap.add_argument("--wav", default=None, help="离线验算模式")
    ap.add_argument("--inject", default=None, help="用指定 wav 循环驱动整条链路（无麦克风演示）")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--panns-url", default=None,
                    help="PANNs 侧车地址（如 http://127.0.0.1:11442）；给了就启用"
                         "模型确认通道。侧车必须绑 cpu0-3（int8 在 4-7 上 SIGILL）")
    ap.add_argument("--panns-trigger", default="confirm",
                    choices=["confirm", "any"],
                    help="confirm=规则判出宠物叫声才送模型确认（默认，方案 A）；"
                         "any=只要有声就送（用于评估规则漏判）")
    args = ap.parse_args()

    if args.wav:
        run_wav(args.wav, args.pet, args.model)
        return

    eng = VoiceEngine(args.device, args.pet, args.model,
                      use_llm=not args.no_llm, verbose=args.verbose,
                      inject=args.inject, panns_url=args.panns_url,
                      panns_trigger=args.panns_trigger)
    eng.start()
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(eng))
    print(f"[voice] listening on {args.host}:{args.port}  device={args.device} "
          f"mode={eng.clf.mode} pet={args.pet} "
          f"panns={'on' if eng.panns else 'off'}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        eng.stop()


if __name__ == "__main__":
    main()
