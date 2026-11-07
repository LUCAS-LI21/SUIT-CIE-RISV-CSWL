#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参数化宠物叫声合成库。

为什么需要它：真实标注样本拿不到（没有公开的、按「吠/嚎/吼/喵/呼噜/哈气/尖叫」
七分的标注集），而只靠 8 个固定合成样本无法说明任何泛化能力——
8 个样本连 7 个类别都覆盖不全（原 make_samples.py 里没有 growl 和 scream）。
用带参数的合成器可以生成数百个「物理参数已知」的样本，用来量分类器
在基频/时长/信噪比/混响变化下的稳定性，并定位具体是哪两类在互相混淆。

它不能替代真实样本：合成信号的频谱比真实发声干净，所以这里测出的
准确率是**上界**。真实标定见 collect_voice.py。

每个函数都接受 rng，同一 seed 可复现——评委按 README 跑能得到同一批数字。
"""
import numpy as np

SR = 16000


# ───────────────────────── 基础工具 ─────────────────────────


def env_ar(n, attack_ms=10.0, release_ms=120.0):
    """起音-衰减包络。真实发声的起音很少是瞬时跳变。"""
    a = max(1, min(n, int(SR * attack_ms / 1000)))
    r = max(1, min(n, int(SR * release_ms / 1000)))
    e = np.ones(n, dtype=np.float64)
    e[:a] = np.linspace(0.0, 1.0, a)
    e[n - r:] = np.minimum(e[n - r:], np.linspace(1.0, 0.0, r))
    return e


def _harm_series(f_curve, n_harm=5, weights=None):
    """按逐样本基频轨迹合成谐波堆叠。f_curve 长度 = 样本数。"""
    phase = 2 * np.pi * np.cumsum(f_curve) / SR
    if weights is None:
        weights = [1.0 / (k + 1) for k in range(n_harm)]
    out = np.zeros(len(f_curve))
    for k, w in enumerate(weights[:n_harm]):
        out += w * np.sin((k + 1) * phase)
    return out


def _norm(x, peak):
    m = np.abs(x).max()
    return x if m < 1e-9 else x * (peak / m)


def _resonance(n, freqs, gains, dur):
    """在若干中心频率上叠一层随时间衰减的窄带成分，模拟共振峰/气流声。"""
    t = np.arange(n) / SR
    out = np.zeros(n)
    for fc, g in zip(freqs, gains):
        out += g * np.sin(2 * np.pi * fc * t) * np.exp(
            -((t - dur / 2) ** 2) / (2 * (dur / 3) ** 2))
    return out


# ───────────────────────── 七类宠物发声 ─────────────────────────


def bark(rng, f0=None, dur=None, n_bursts=None, gap=None, noise_mix=None):
    """犬吠：极短促、爆发式起音、宽带噪声成分重、基频快速下滑。

    判据依赖：峰均比高（单个爆发）、起音早、调制深度低（不呈周期起伏）。
    """
    f0 = f0 if f0 is not None else rng.uniform(250, 650)
    dur = dur if dur is not None else rng.uniform(0.12, 0.34)
    n_bursts = n_bursts if n_bursts is not None else int(rng.integers(1, 5))
    gap = gap if gap is not None else rng.uniform(0.08, 0.26)
    noise_mix = noise_mix if noise_mix is not None else rng.uniform(0.45, 0.85)

    total = int(SR * (dur * n_bursts + gap * (n_bursts - 1))) + 1
    x = np.zeros(total)
    pos = 0
    for _ in range(n_bursts):
        n = int(SR * dur)
        if n < 8:
            break
        t = np.arange(n) / SR
        f = np.linspace(f0 * rng.uniform(1.15, 1.45), f0 * rng.uniform(0.62, 0.85), n)
        sig = _harm_series(f, 3, [1.0, 0.6, 0.3])
        sig += noise_mix * rng.standard_normal(n) * np.exp(-t / (dur * 0.35))
        sig *= env_ar(n, rng.uniform(3, 8), rng.uniform(60, 110))
        end = min(total, pos + n)
        x[pos:end] += sig[:end - pos]
        pos = end + int(SR * gap)
        if pos >= total:
            break
    return _norm(x, rng.uniform(0.35, 0.72))


def meow(rng, f_start=None, f_peak=None, f_end=None, dur=None):
    """喵叫：基频先升后降（拱形轨迹），带共振峰，谐波清晰。

    判据依赖：f0 在 280~1100、拱形度（后半段/前半段的下降比例）、
    时长 0.2~1.1s、谐噪比高。拱形轨迹是它与长嚎最关键的差别——
    长嚎是单调下滑，喵叫是先升后降。
    """
    dur = dur if dur is not None else rng.uniform(0.40, 1.10)
    base = rng.uniform(360, 620)
    f_start = f_start if f_start is not None else base * rng.uniform(0.85, 1.0)
    f_peak = f_peak if f_peak is not None else base * rng.uniform(1.35, 1.85)
    f_end = f_end if f_end is not None else base * rng.uniform(0.60, 0.85)

    n = int(SR * dur)
    if n < 32:
        return np.zeros(64)
    k1, k2 = int(0.30 * n), int(0.62 * n)
    f0 = np.concatenate([
        np.linspace(f_start, f_peak, k1),
        np.linspace(f_peak, f_end, max(1, k2 - k1)),
        np.linspace(f_end, f_end * rng.uniform(0.90, 0.97), max(1, n - k2)),
    ])[:n]
    sig = _harm_series(f0, 4, [1.0, 0.45, 0.18, 0.08])
    # 猫的共振峰大致在 1.2k / 2.6k，位置随机浮动一点
    sig += _resonance(n, [rng.uniform(1100, 1400), rng.uniform(2400, 2900)],
                      [rng.uniform(0.15, 0.30), rng.uniform(0.06, 0.14)], dur)
    sig *= env_ar(n, rng.uniform(40, 90), rng.uniform(160, 280))
    sig += rng.uniform(0.005, 0.02) * rng.standard_normal(n)
    return _norm(sig, rng.uniform(0.22, 0.48))


def purr(rng, rate=None, dur=None, buzz_f=None):
    """呼噜：脉冲率就是基频（20~30 Hz），谐波密集延伸到数百 Hz，能量低而稳。

    注意呼噜的物理本质：**呼噜的「基频」就是那个 25 Hz 的脉冲重复率本身**，
    不是叠在一个 120 Hz 载波上的调制。所以这里用 25 Hz 的谐波堆叠来合成，
    谱能量集中在 800 Hz 以下——早期版本用「120 Hz 载波 × 25 Hz 方波」，
    载波里还混了大量白噪，导致谱重心跑到 2467 Hz，被哈气/噪声分支抢走。

    判据依赖：调制深度大（18~45 Hz 带内）、脉冲数多、谱重心低、能量低。
    与低吼的关键差别：低吼有明确基频（90~180 Hz 谐波清晰、谐噪比高），
    呼噜的「基频」低于 F0_MIN，谐噪比低，且能量明显更弱。
    """
    rate = rate if rate is not None else rng.uniform(19, 30)
    dur = dur if dur is not None else rng.uniform(0.85, 2.20)
    buzz_f = buzz_f if buzz_f is not None else rng.uniform(180, 420)

    n = int(SR * dur)
    t = np.arange(n) / SR
    phase = 2 * np.pi * np.cumsum(np.full(n, rate) * (1 + 0.02 * np.sin(2 * np.pi * 0.7 * t))) / SR
    sig = np.zeros(n)
    k = 1
    while k * rate < buzz_f * 2.2 and k < 40:
        # 1/k 衰减 + 在 buzz_f 附近加一个共振峰，模拟胸腔共鸣
        g = (1.0 / k) * (1.0 + 1.4 * np.exp(-((k * rate - buzz_f) ** 2) / (2 * (buzz_f * 0.5) ** 2)))
        sig += g * np.sin(k * phase)
        k += 1
    sig += rng.uniform(0.05, 0.14) * rng.standard_normal(n)
    sig *= env_ar(n, 120, 260)
    return _norm(sig, rng.uniform(0.10, 0.22))


def hiss(rng, dur=None, tilt=None):
    """哈气：宽带噪声为主，几乎无谐波，谱平坦度极高，谱重心高。

    判据依赖：flatness >= 0.15、harmonicity < 0.5、无基频、谱重心 >= 3000。
    """
    dur = dur if dur is not None else rng.uniform(0.35, 1.20)
    tilt = tilt if tilt is not None else rng.uniform(0.6, 1.0)
    n = int(SR * dur)
    x = rng.standard_normal(n)
    # 一阶差分做高通倾斜，模拟气流噪声的高频占优
    x = np.diff(np.concatenate([[0.0], x]))
    x *= env_ar(n, rng.uniform(50, 110), rng.uniform(200, 380))
    if tilt < 0.75:
        x = np.convolve(x, np.ones(3) / 3.0, mode="same")
    return _norm(x, rng.uniform(0.25, 0.55))


def howl(rng, f_start=None, f_end=None, dur=None):
    """长嚎：长音、基频**单调下滑**、谐波丰富、能量平稳。

    判据依赖：时长 >= 0.6、f0 >= 240、desc_ratio（后段/前段基频比）>= 0.85、
    拱形度 < 0.55。与喵叫的区分全在这两点上。
    """
    dur = dur if dur is not None else rng.uniform(1.00, 2.50)
    f_end = f_end if f_end is not None else rng.uniform(250, 600)
    f_start = f_start if f_start is not None else f_end * rng.uniform(1.35, 2.30)

    n = int(SR * dur)
    if n < 64:
        return np.zeros(128)
    f = np.linspace(f_start, f_end, n)
    # 长嚎有轻微颤音，但不足以让基频回头
    f = f * (1 + 0.012 * np.sin(2 * np.pi * rng.uniform(3.5, 6.5) * np.arange(n) / SR))
    sig = _harm_series(f, 4, [1.0, 0.50, 0.25, 0.12])
    sig *= env_ar(n, rng.uniform(150, 300), rng.uniform(300, 520))
    sig += rng.uniform(0.008, 0.025) * rng.standard_normal(n)
    return _norm(sig, rng.uniform(0.22, 0.45))


def growl(rng, f0=None, dur=None, am_rate=None):
    """低吼：低频（90~180 Hz）、强幅度调制（20~40 Hz）、持续、谐波密集。

    这是原 8 个样本里缺失的类别之一，也是与呼噜最难分的一对：
    两者都是「低频 + 有调制」。区分点在于低吼有明确基频与大量谐波
    （harmonicity 高），呼噜几乎没有基频（harmonicity 低、能量更低）。
    """
    f0 = f0 if f0 is not None else rng.uniform(90, 180)
    dur = dur if dur is not None else rng.uniform(0.50, 1.60)
    am_rate = am_rate if am_rate is not None else rng.uniform(20, 40)

    n = int(SR * dur)
    t = np.arange(n) / SR
    # 低吼的基频本身在抖，这是「粗糙感」的来源
    f = f0 * (1 + 0.06 * rng.standard_normal(n).cumsum() / np.sqrt(n))
    f = np.clip(f, f0 * 0.7, f0 * 1.3)
    sig = _harm_series(f, 6, [1.0, 0.7, 0.5, 0.35, 0.22, 0.12])
    # 幅度调制必须落在 20~40 Hz 本身。早期写成 sin²(2πf t)，其实际频率是 2f
    # （60~80 Hz），跑出了分类器 18~45 Hz 的检测带，mod_depth 恒为 0，
    # 低吼因此在评估中 40 条全军覆没——这类错误只有靠批量评估才暴露得出来。
    sig *= (0.55 + 0.45 * np.sin(2 * np.pi * am_rate * t))
    sig *= env_ar(n, rng.uniform(80, 160), rng.uniform(180, 340))
    sig += rng.uniform(0.05, 0.15) * rng.standard_normal(n)
    return _norm(sig, rng.uniform(0.28, 0.55))


def scream(rng, f0=None, dur=None):
    """尖叫：高基频（700~1200）、高能量、长音，通常伴随疼痛。

    原 8 个样本里同样缺失。判据：f0 >= 700 且 rms > -26 dBFS 且时长 >= 0.3。
    """
    f0 = f0 if f0 is not None else rng.uniform(700, 1200)
    dur = dur if dur is not None else rng.uniform(0.35, 1.00)

    n = int(SR * dur)
    t = np.arange(n) / SR
    f = f0 * (1 + 0.03 * np.sin(2 * np.pi * rng.uniform(5, 9) * t))
    f = f * np.linspace(1.06, 0.94, n)          # 整体略下滑，但不到长嚎的程度
    sig = _harm_series(f, 5, [1.0, 0.55, 0.30, 0.15, 0.07])
    # 尖叫有明显的气声成分，但不足以压掉谐波
    sig += rng.uniform(0.10, 0.25) * rng.standard_normal(n)
    sig *= env_ar(n, rng.uniform(15, 40), rng.uniform(120, 260))
    return _norm(sig, rng.uniform(0.55, 0.88))


# ───────────────────────── 非宠物：人声与噪声 ─────────────────────────


def human_vowel(rng, f0=None, dur=None, formants=None):
    """人声元音：稳定基频 + 三个共振峰，无音节率调制。

    性别决定基频：成年男声 105~150，成年女声 175~260，儿童更高。
    人声门控就是靠「基频落在这个区间 + 音节率 2.5~8 Hz + MFCC 起伏大」判的。
    """
    if f0 is None:
        f0 = rng.uniform(105, 150) if rng.random() < 0.5 else rng.uniform(175, 260)
    dur = dur if dur is not None else rng.uniform(0.45, 1.30)
    if formants is None:
        # F1/F2/F3 在元音间大幅变化（/a/ /i/ /u/），这里随机取
        formants = [(rng.uniform(500, 850), 110),
                    (rng.uniform(1000, 1900), 150),
                    (rng.uniform(2300, 3000), 220)]

    n = int(SR * dur)
    phase = 2 * np.pi * f0 * np.arange(n) / SR
    sig = np.zeros(n)
    for h in range(1, 30):
        fh = h * f0
        if fh > SR / 2:
            break
        g = 1.0 / h
        for fc, bw in formants:
            # 指数里的分子可能很负，先夹一下再 exp，避免下溢告警
            g += 0.8 * float(np.exp(max(-50.0, -((fh - fc) ** 2) / (2 * bw ** 2))))
        sig += g * np.sin(h * phase)
    sig *= env_ar(n, rng.uniform(60, 110), rng.uniform(150, 260))
    return _norm(sig, rng.uniform(0.20, 0.40))


def human_speech(rng, f0=None, n_syll=None, syl_dur=None, gap=None):
    """连续说话：多个音节串起来，音节率落在 3~6 Hz（人声最强的判别特征）。"""
    if f0 is None:
        f0 = rng.uniform(105, 150) if rng.random() < 0.5 else rng.uniform(175, 260)
    n_syll = n_syll if n_syll is not None else int(rng.integers(2, 7))
    syl_dur = syl_dur if syl_dur is not None else rng.uniform(0.16, 0.30)
    gap = gap if gap is not None else rng.uniform(0.04, 0.12)

    parts = []
    for _ in range(n_syll):
        # 每个音节的基频随机偏移，模拟语调起伏
        parts.append(human_vowel(rng, f0=f0 * rng.uniform(0.88, 1.18),
                                 dur=syl_dur * rng.uniform(0.8, 1.2)))
        parts.append(np.zeros(int(SR * gap)))
    x = np.concatenate(parts) if parts else np.zeros(SR)
    return _norm(x, rng.uniform(0.20, 0.42))


def noise_ambient(rng, kind=None, dur=None):
    """环境噪声：白噪/粉噪/空调低频嗡鸣/风扇。"""
    kind = kind if kind is not None else rng.choice(["white", "pink", "hum", "fan"])
    dur = dur if dur is not None else rng.uniform(0.6, 2.0)
    n = int(SR * dur)
    t = np.arange(n) / SR

    if kind == "white":
        x = rng.standard_normal(n)
    elif kind == "pink":
        # 频域按 1/sqrt(f) 加权，得到粉噪
        spec = np.fft.rfft(rng.standard_normal(n))
        freqs = np.fft.rfftfreq(n, 1.0 / SR)
        spec[1:] /= np.sqrt(freqs[1:])
        x = np.fft.irfft(spec, n)
    elif kind == "hum":
        # 50 Hz 工频及其谐波，这是室内最常见的稳态噪声
        x = sum(0.5 / k * np.sin(2 * np.pi * 50 * k * t + rng.uniform(0, 6.28))
                for k in range(1, 5))
        x = np.asarray(x) + 0.05 * rng.standard_normal(n)
    else:  # fan
        x = rng.standard_normal(n) * (1 + 0.3 * np.sin(2 * np.pi * 12 * t))
        x = np.convolve(x, np.ones(5) / 5.0, mode="same")

    x = x * env_ar(n, 30, 60)
    return _norm(x, rng.uniform(0.02, 0.18))


def silence(rng, dur=None, floor_db=None):
    """静音：只有底噪。floor_db 是相对满量程的 dBFS（-70 ~ -45）。"""
    dur = dur if dur is not None else rng.uniform(0.5, 2.0)
    floor_db = floor_db if floor_db is not None else rng.uniform(-70, -46)
    n = int(SR * dur)
    amp = 10 ** (floor_db / 20.0)
    return (rng.standard_normal(n) * amp).astype(np.float64)


# ───────────────────────── 增强 ─────────────────────────


def add_noise(x, rng, snr_db):
    """按指定 SNR 叠环境噪声。SNR 越低越接近现场（宠物在客厅、有电视/空调）。"""
    noise = noise_ambient(rng, dur=len(x) / SR)
    if len(noise) < len(x):
        noise = np.pad(noise, (0, len(x) - len(noise)), mode="wrap")
    noise = noise[:len(x)]
    ps = np.mean(x ** 2) + 1e-12
    pn = np.mean(noise ** 2) + 1e-12
    target = ps / (10 ** (snr_db / 10.0))
    return x + noise * np.sqrt(target / pn)


def apply_reverb(x, rng, rt60=None):
    """简易指数衰减混响。现场几乎不可能是消声室，这一步让阈值更保守。"""
    rt60 = rt60 if rt60 is not None else rng.uniform(0.10, 0.45)
    n_tail = int(SR * rt60)
    if n_tail < 8:
        return x
    t = np.arange(n_tail) / SR
    ir = rng.standard_normal(n_tail) * np.exp(-6.9 * t / rt60)
    ir[0] = 1.0
    ir /= np.sqrt(np.sum(ir ** 2))
    return np.convolve(x, ir, mode="full")[:len(x)]


def resample_speed(x, rng, speed=None):
    """变速（同时改变基频与时长），模拟不同体型/品种的个体差异。"""
    speed = speed if speed is not None else rng.uniform(0.88, 1.14)
    n_out = max(16, int(len(x) / speed))
    idx = np.linspace(0, len(x) - 1, n_out)
    return np.interp(idx, np.arange(len(x)), x)


def apply_distance(x, rng, atten_db=None):
    """按距离衰减整段能量，**不重新归一化**。

    这里刻意不用「峰值归一化到固定 dBFS」：那会把静音、呼噜、吠叫的绝对
    能量拉到同一水平，而分类器的声学门控正是靠 rms_max_db（-48 / -38 dBFS）
    区分「有没有在发声」。归一化等于把这一维度直接删掉，静音会被判成噪声、
    呼噜会被判成吠叫——第一版评估器就是这么错的，静音 40 条全灭。

    真实世界里能量本来就是物理量：静音是 -55 dBFS，呼噜 -30，贴脸吠叫 -10。
    所以这里只做衰减（模拟距离与遮挡），绝不放大。
    """
    atten_db = atten_db if atten_db is not None else rng.uniform(-16.0, 0.0)
    return x * (10 ** (atten_db / 20.0))


def augment(x, rng, snr_db=None, rt60=None, speed=None, atten_db=None):
    """标准增强链：变速 → 混响 → 加噪 → 距离衰减。顺序有讲究：

    先变速（改变基频与时长）再混响（房间是对「已经发出的声音」起作用），
    最后加噪与衰减。反过来先衰减再加噪，等于把 SNR 隐式抬高了，
    测出来的鲁棒性是假的。
    """
    y = resample_speed(x, rng, speed)
    y = apply_reverb(y, rng, rt60)
    if snr_db is not None:
        y = add_noise(y, rng, snr_db)
    y = apply_distance(y, rng, atten_db)
    return y
