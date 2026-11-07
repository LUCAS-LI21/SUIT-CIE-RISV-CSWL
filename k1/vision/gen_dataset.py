#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成叫声识别标定数据集。

产出：datasets/voice/<类别>/<类别>_NNN.wav  +  datasets/voice/manifest.jsonl

为什么要有这个：只靠 make_samples.py 的 8 个固定样本，既覆盖不全（原版没有
低吼 growl 和尖叫 scream），也无法说明任何稳定性——换一个基频、加一点混响
就可能判错，而 8 个样本永远发现不了。这里按参数网格生成，每条样本的
生成参数都写进 manifest，判错时能直接回查到是「哪个参数区间不work」。

类别与数量的选择依据：
    · 7 类宠物发声各 40 条 —— 每类都要有足够样本才能算出有意义的召回率
    · 人声 50 条（元音 + 连读，男女各半）—— 误报的主要来源，宁可多测
    · 环境噪声 40 条（白/粉/工频/风扇）—— 门控的下限
    · 静音 30 条（不同底噪）—— 底噪 -46 dBFS 以上容易误触发，必须覆盖

用法：
    python3 gen_dataset.py                # 默认每类 40 条
    python3 gen_dataset.py --per-class 20 # 快速跑一遍
"""
import argparse
import json
import os
import wave

import numpy as np

import synth_lib as S

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "datasets", "voice")

# 类别 → 合成函数。类别名与 petvoice_service.PET_CLASSES / CLS_* 严格一致，
# 不一致的话评估矩阵的行列会对不上，而且不会报错，只是数字全错。
GENERATORS = {
    "bark": S.bark,
    "howl": S.howl,
    "growl": S.growl,
    "meow": S.meow,
    "purr": S.purr,
    "hiss": S.hiss,
    "scream": S.scream,
    "human": None,      # 特殊：元音与连读交替
    "noise": S.noise_ambient,
    "silence": S.silence,
}

# 增强档位。attn 是距离衰减（dB，负值），不是「归一化到某个 dBFS」——
# 后者会把静音拉到和吠叫一样的电平，等于删掉声学门控赖以工作的能量维度。
AUG_LEVELS = [
    {"tag": "clean", "snr_db": None, "rt60": 0.05, "attn": 0.0},
    {"tag": "mild", "snr_db": 20.0, "rt60": 0.20, "attn": -3.0},
    {"tag": "field", "snr_db": 10.0, "rt60": 0.35, "attn": -7.0},
    {"tag": "harsh", "snr_db": 5.0, "rt60": 0.50, "attn": -12.0},
]


def save_wav(path, x):
    x = np.clip(x, -1.0, 1.0)
    pcm = (x * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(S.SR)
        w.writeframes(pcm.tobytes())


def make_one(cls, rng):
    """生成一条「干净」样本，并返回它的物理参数摘要（写进 manifest）。"""
    if cls == "human":
        if rng.random() < 0.5:
            f0 = rng.uniform(105, 150) if rng.random() < 0.5 else rng.uniform(175, 260)
            x = S.human_vowel(rng, f0=f0)
            return x, {"kind": "vowel", "f0": round(float(f0), 1)}
        f0 = rng.uniform(105, 150) if rng.random() < 0.5 else rng.uniform(175, 260)
        x = S.human_speech(rng, f0=f0)
        return x, {"kind": "speech", "f0": round(float(f0), 1)}

    gen = GENERATORS[cls]
    x = gen(rng)
    return x, {"dur": round(len(x) / S.SR, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=40,
                    help="每个类别的基准样本数（每条的增强档位见 AUG_LEVELS）")
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--out", default=OUT, help="输出目录（做留出集时换一个）")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    out = args.out
    os.makedirs(out, exist_ok=True)
    manifest = []

    for cls in GENERATORS:
        d = os.path.join(out, cls)
        os.makedirs(d, exist_ok=True)
        # 清掉上一轮产物，避免残留文件把统计脏掉
        for f in os.listdir(d):
            if f.endswith(".wav"):
                os.remove(os.path.join(d, f))

        n = 0
        for i in range(args.per_class):
            base, info = make_one(cls, rng)
            if len(base) < int(0.05 * S.SR):
                continue
            # 每条基准样本在 4 个增强档位各生成一份；档位之外再随机变速，
            # 模拟不同体型（体型直接改变基频，是最主要的人际差异）
            for lvl in AUG_LEVELS:
                speed = float(rng.uniform(0.88, 1.14)) if lvl["tag"] != "clean" else 1.0
                y = S.augment(base, rng, snr_db=lvl["snr_db"], rt60=lvl["rt60"],
                              speed=speed, atten_db=lvl["attn"])
                if len(y) < int(0.05 * S.SR):
                    continue
                name = f"{cls}_{i:03d}_{lvl['tag']}.wav"
                save_wav(os.path.join(d, name), y)
                manifest.append({
                    "file": f"{cls}/{name}", "label": cls, "aug": lvl["tag"],
                    "speed": round(speed, 3),
                    **info,
                })
                n += 1
        print(f"[{cls:8s}] {n} 条")

    with open(os.path.join(out, "manifest.jsonl"), "w", encoding="utf-8") as f:
        for m in manifest:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    print(f"\n合计 {len(manifest)} 条 → {out}")


if __name__ == "__main__":
    main()
