#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""叫声分类回归：跑 datasets/voice 下全部样本，输出每类的预测分布与中位 F0。

用途：改动 f0_track 或任何 classify 规则前后，用它对比是否漂移。
只读，不修改样本。

用法：
    python3 f0_regress.py                 # 打印结果
    python3 f0_regress.py before.json     # 同时写一份 json 便于 diff

判读方式：看两件事——
  1. 每类的 top（多数派）有没有从正确类别掉到 noise；
  2. 中位 F0 有没有出现 1600.0 这个值。出现过就说明 F0 伪造 bug 复发了
     （自相关选峰又退回到未归一化 + 全局 argmax）。
"""
import glob
import json
import os
import sys
import wave

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import petvoice_service as pv  # noqa: E402


def load(path):
    """读 wav 并重采样到 SR（最近邻，够用于回归）。"""
    with wave.open(path, "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch)[:, 0]
    if sr != pv.SR:
        t = np.linspace(0, len(x) - 1, int(len(x) * pv.SR / sr))
        x = np.interp(t, np.arange(len(x)), x).astype(np.float32)
    return x


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    voice_dir = os.path.join(ROOT, "datasets", "voice")
    if not os.path.isdir(voice_dir):
        print(f"[skip] 找不到 {voice_dir}（数据集未下载），无需回归")
        return

    clf = pv.Classifier(None)
    win, hop = int(pv.WIN_SEC * pv.SR), int(pv.HOP_SEC * pv.SR)
    out = {}
    warned = False
    print(f"{'label':10s} {'top':9s} {'中位F0':>8s}  分布")
    for d in sorted(glob.glob(os.path.join(voice_dir, "*") + os.sep)):
        label = os.path.basename(d.rstrip("\\/"))
        preds, f0s = {}, []
        for p in sorted(glob.glob(os.path.join(d, "*.wav"))):
            x = load(p)
            if len(x) < win:
                x = np.pad(x, (0, win - len(x)))
            i = 0
            while i + win <= len(x):
                f = pv.features_of(x[i:i + win])
                cls, _conf, _ = clf.classify(f)
                preds[cls] = preds.get(cls, 0) + 1
                f0s.append(f["f0_med"])
                i += hop
        if not preds:
            continue
        top = max(preds, key=preds.get)
        med = float(np.median(f0s))
        out[label] = {"top": top, "dist": preds, "f0_med": round(med, 1)}
        flag = ""
        if abs(med - 1600.0) < 1.0:
            flag = "  <-- F0 伪造复发"
            warned = True
        print(f"{label:10s} {top:9s} {med:8.1f}  {preds}{flag}")

    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        print(f"\n已写入 {out_path}")

    if warned:
        print("\n[警告] 有类别中位 F0 仍是 1600 Hz，说明自相关选峰退回"
              "「未归一化 + 全局 argmax」的老路，请检查 f0_track。")
        sys.exit(1)


if __name__ == "__main__":
    main()
