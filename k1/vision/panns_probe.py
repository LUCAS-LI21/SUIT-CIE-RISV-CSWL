#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PANNs 离线探针：不启 HTTP、不碰麦克风，直接对 wav 跑模型，打印 7 类判定。

用途：解耦验证「模型本身是否有效」，避免和服务链路的状态混杂。
"""
import sys
import wave

sys.path.insert(0, "/root/k1-ai")

import numpy as np  # noqa: E402

from petvoice_service import PannsLocal, SR  # noqa: E402


def load(path):
    with wave.open(path, "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch)[:, 0]
    if sr != SR:
        t = np.linspace(0, len(x) - 1, int(len(x) * SR / sr))
        x = np.interp(t, np.arange(len(x)), x).astype(np.float32)
    return x


CASES = [
    ("/root/k1-ai/real_dog.wav", 1.0, 2.5, "真实狗叫（含吠叫段）"),
    ("/root/k1-ai/real_dog.wav", 0.0, 1.0, "真实狗叫（静音段）"),
    ("/root/k1-ai/voice_samples/02_bark_single.wav", 0.0, 0.96, "合成单次吠叫"),
    ("/root/k1-ai/voice_samples/01_meow.wav", 0.0, 1.5, "合成猫叫"),
    ("/root/k1-ai/voice_samples/04_hiss.wav", 0.0, 1.4, "合成哈气"),
    ("/root/k1-ai/voice_samples/07_silence.wav", 0.0, 1.5, "纯静音"),
    ("/root/k1-ai/voice_samples/06_human_vowel.wav", 0.0, 1.5, "人声元音"),
]

p = PannsLocal()
print(f"模型加载 {p.load_ms:.0f} ms  窗口 {p.window}s")
print("-" * 78)
for path, a, b, note in CASES:
    try:
        x = load(path)
        seg = x[int(a * SR):int(b * SR)]
        cls, conf, pet = p.classify(seg)
        top = sorted(pet.items(), key=lambda kv: -kv[1])[:3]
        detail = "  ".join(f"{k}={v:.2f}" for k, v in top)
        name = path.split("/")[-1]
        print(f"{note:22s} {name:26s} -> {cls:7s} {conf:.2f}  {p.last_ms:5.0f}ms")
        print(f"{'':22s} top3: {detail}")
    except Exception as e:  # noqa: BLE001
        print(f"{note:22s} 失败: {e}")
