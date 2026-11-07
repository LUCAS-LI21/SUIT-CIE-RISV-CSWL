#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线验证 k1_mic_asr 的静音闸门：真人语音必须放行，纯底噪必须拒绝。

不通过 11441 跑，是因为没法让板子「现场说一句标准普通话」；
直接 import 闸门函数、喂已知音频，才能把判据本身单独验掉。

用法（板端）：
    python3 /root/gate_selftest.py
任一条不符合预期即非零退出。
"""
import sys
import wave

import numpy as np

sys.path.insert(0, "/root/asr")
from k1_mic_asr import _speech_gate  # noqa: E402

WAV = ("/root/asr/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
       "/test_wavs/zh.wav")
AMBIENT = "/tmp/k1_press.raw"


def load_wav(p):
    with wave.open(p, "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")


def load_raw(p):
    return np.fromfile(p, dtype="<i2")


def main():
    fail = 0

    x = load_wav(WAV)
    ok, info = _speech_gate(x)
    print(f"zh.wav（真人语音 5.6 s）→ {'放行' if ok else '拒绝'}  {info}")
    if not ok:
        print("  ✗ 真人语音被错杀")
        fail += 1

    # 缩到 1 s、再缩到 0.4 s：短句也必须放行，否则用户说「坐下」两个字就废了
    for sec in (1.0, 0.4):
        seg = x[int(16000 * 1.0): int(16000 * (1.0 + sec))]
        # 补一段底噪当留白，模拟真实「按下—说话—松开」
        pad = np.zeros(int(16000 * 0.5), dtype="<i2")
        ok, info = _speech_gate(np.concatenate([pad, seg, pad]))
        print(f"zh.wav 截取 {sec}s → {'放行' if ok else '拒绝'}  {info}")
        if not ok:
            print(f"  ✗ {sec}s 语音被错杀")
            fail += 1

    try:
        a = load_raw(AMBIENT)
        ok, info = _speech_gate(a)
        print(f"底噪 {len(a) / 16000:.2f}s → {'放行' if ok else '拒绝'}  {info}")
        if ok:
            print("  ✗ 纯底噪被放行（会幻觉出一句话）")
            fail += 1
    except FileNotFoundError:
        print(f"跳过底噪样本（{AMBIENT} 不存在）")

    # 数字静音：全 0
    ok, info = _speech_gate(np.zeros(16000 * 2, dtype="<i2"))
    print(f"全零 2 s → {'放行' if ok else '拒绝'}  {info}")
    if ok:
        print("  ✗ 全零被放行")
        fail += 1

    print("PASS" if fail == 0 else f"FAIL {fail} 项")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
