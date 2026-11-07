#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 8 个固定冒烟样本，用于人工听检与单窗回归。

它只做一件事：从 synth_lib 按固定 seed 取 8 条覆盖全部类别的样本。
真正的批量标定在 gen_dataset.py + eval_dataset.py（400 条、四档增强）。

为什么不再在这里写一套合成函数：这里曾经自带一份 meow/bark/purr 实现，
与 synth_lib 逐渐分叉——旧版 purr 用「噪声载波 × 25 Hz 方波」，谱重心跑到
2526 Hz、平坦度 0.39，物理上根本不是呼噜，分类器把它判成哈气反而是对的。
两套合成器意味着「验算用的样本」和「标定用的样本」不是同一个分布，
这种不一致只有把合成逻辑合并到一处才能根除。
"""
import os
import wave

import numpy as np

import synth_lib as S

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_samples")


def save(name, x):
    os.makedirs(OUT, exist_ok=True)
    x = np.clip(x, -1.0, 1.0)
    pcm = (x * 32767).astype(np.int16)
    with wave.open(os.path.join(OUT, name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(S.SR)
        w.writeframes(pcm.tobytes())
    print(f"[ok] {name}  {len(x) / S.SR:.2f}s")


def main():
    # 固定 seed：同一份代码在任何机器上产出同一批 wav，
    # 答辩现场重跑能复现，这一点比「样本更多样」重要。
    rng = np.random.default_rng(7)
    save("01_meow.wav", S.meow(rng, dur=0.75))
    save("02_bark_single.wav", S.bark(rng, n_bursts=1, dur=0.22))
    save("03_bark_triple.wav", S.bark(rng, n_bursts=3, dur=0.20, gap=0.18))
    save("04_hiss.wav", S.hiss(rng, dur=0.70))
    save("05_howl.wav", S.howl(rng, dur=1.60))
    save("06_human_vowel.wav", S.human_vowel(rng, f0=155.0, dur=1.00))
    save("07_silence.wav", S.silence(rng, dur=1.00, floor_db=-68.0))
    save("08_purr.wav", S.purr(rng, rate=25.0, dur=1.40))
    print(f"\n输出目录: {OUT}")


if __name__ == "__main__":
    main()
