#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标定 PANNs 侧车的「取窗策略」：从一段上下文里怎么挑给模型的 2 s。

背景（这是实测撞出来的坑，不要凭直觉改）：
    MobileNetV2 的输入必须是固定 2.0 s（64000 样本 @32 kHz）。而一段 5 s 的
    上下文里，真正的吠叫只占 0.5~1 s。于是要决定"挑哪一段铺成 2 s"。

    最初按"能量最大的 2 s 连续窗"挑，结果同一段真狗叫：
        起点 0.75 s（能量最大）→ Bark 0.145
        起点 1.00 s           → Bow-wow 0.331
        起点 1.50 s           → Bow-wow 0.362
    能量最大 ≠ 模型认得好：2 s 连续窗会把一次短促吠叫切在边界上，
    模型看到的是残缺波形。Cnn14 时代就踩过一次（整窗 0.96 s 含尾部静音
    → 一律判 noise 0.00）。

本脚本对比三种策略：
    A  最响 2 s 连续窗（原实现）
    B  最响 L 秒 → 平铺（tile）铺满 2 s
    C  最响 L 秒 → 补零（zero-pad）
并扫 L。判据是「宠物类（bark 组）分数」，不是 top1 类别名。

用法（板端或 PC 均可，需能读 onnx）：
    python3 panns_window_calib.py --model MobileNetV2_32k_int8.onnx real_dog.wav
"""
import argparse
import wave

import numpy as np
import onnxruntime as ort

SR_MODEL = 32000
WIN_SEC = 2.0
GROUPS = {
    "bark":   [75, 76, 74, 122],
    "growl":  [79, 39],
    "howl":   [77],
    "meow":   [83, 85, 81],
    "purr":   [82],
    "hiss":   [84],
    "scream": [14, 80],
}


def load_labels(path):
    import csv
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    rows.sort(key=lambda r: int(r["index"]))
    return [r["display_name"] for r in rows]


def read_wav(path):
    with wave.open(path, "rb") as w:
        sr, nch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise SystemExit("只支持 16 bit PCM")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if nch > 1:
        x = x.reshape(-1, nch).mean(axis=1)
    return x, sr


def resample(x, sr, target=SR_MODEL):
    x = np.asarray(x, dtype=np.float32).ravel()
    if sr == target or x.size == 0:
        return x
    n_out = int(round(x.size * target / sr))
    return np.interp(np.arange(n_out) * (sr / target),
                     np.arange(x.size), x).astype(np.float32)


def loudest_start(x, span):
    """返回能量最大的 span 点窗起点。用累积和，不能用 convolve（O(n·k) 会卡死）。"""
    if x.size <= span:
        return 0
    c = np.concatenate(([0.0], np.cumsum(x.astype(np.float64) ** 2)))
    e = c[span:] - c[:-span]
    step = max(1, int(SR_MODEL * 0.05))          # 5 ms 精度，标定时不怕慢
    return min(int(np.argmax(e[::step])) * step, x.size - span)


def build(x, want, span_sec, mode):
    span = int(SR_MODEL * span_sec)
    if x.size <= span:
        seg = x
    else:
        s = loudest_start(x, span)
        seg = x[s:s + span]
    if len(seg) >= want:
        return seg[:want].astype(np.float32)
    if mode == "tile":
        return np.tile(seg, want // len(seg) + 1)[:want].astype(np.float32)
    out = np.zeros(want, dtype=np.float32)
    out[:len(seg)] = seg
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--model", default="/root/k1-ai/MobileNetV2_32k_int8.onnx")
    ap.add_argument("--labels", default="/root/k1-ai/labels.csv")
    args = ap.parse_args()

    names = load_labels(args.labels)
    so = ort.SessionOptions()
    so.log_severity_level = 3
    so.intra_op_num_threads = 2
    sess = ort.InferenceSession(args.model, so, providers=["CPUExecutionProvider"])
    want = int(SR_MODEL * WIN_SEC)

    def petscore(seg):
        sc = sess.run(None, {"input_audio": seg[None, :]})[0][0]
        g = {k: max(float(sc[i]) for i in idx)for k, idx in GROUPS.items()}
        best = max(g.items(), key=lambda kv: kv[1])
        return best[0], best[1], g

    spans = (0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.5, 2.0)
    for p in args.files:
        x, sr = read_wav(p)
        v = resample(x, sr)
        print(f"\n=== {p}  {sr} Hz  {len(v) / SR_MODEL:.2f}s ===")
        print(f"{'span':>6s} | {'A 最响连续窗':>22s} | {'B tile':>22s} | {'C 补零':>22s}")
        for s in spans:
            row = []
            for mode in ("cont", "tile", "zero"):
                if mode == "cont" and s < WIN_SEC:
                    row.append(" " * 22)
                    continue
                cls, conf, _ = petscore(build(v, want, min(s, WIN_SEC), mode))
                row.append(f"{cls:10s} {conf:.3f}".rjust(22))
            print(f"{s:6.1f} | " + " | ".join(row))


if __name__ == "__main__":
    main()
