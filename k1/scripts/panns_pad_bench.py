"""PANNs 前处理策略对比：短促叫声怎么补成 10 秒 clip 才判得准。

PANNs Cnn14 是 AudioSet 的 10 秒 clip 分类器。真实叫声往往只有 0.3~0.7 秒，
把它塞进 10 秒有三种做法，差别很大：
  A. tile      —— 循环铺满，会制造出「节奏性重复」，模型容易听成 Music
  B. zero-pad  —— 段放中间，其余静音，最接近「安静里叫一声」
  C. 短窗      —— 只补到 2 秒，计算量小 5 倍，但偏离训练分布

用法：python scripts/panns_pad_bench.py [wav] [起] [止]
"""
import csv
import sys

import numpy as np
import onnxruntime as ort
import soundfile as sf

SR = 16000
MODEL = "models/panns/Cnn14_16k.onnx"
KEYS = ("Dog", "Bark", "Howl", "Growling", "Cat", "Meow", "Purr", "Hiss",
        "Speech", "Silence", "Music", "Animal")


def load():
    rows = list(csv.DictReader(open("models/panns/labels.csv", encoding="utf-8")))
    rows.sort(key=lambda r: int(r["index"]))
    names = [r["display_name"] for r in rows]
    return names, {n: i for i, n in enumerate(names)}


def resample(x, sr):
    n = int(round(len(x) * SR / sr))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype("float32")


def tile_to(seg, dur):
    need = int(SR * dur)
    if len(seg) < need:
        seg = np.tile(seg, int(np.ceil(need / max(1, len(seg)))))
    return seg[:need]


def pad_to(seg, dur):
    need = int(SR * dur)
    out = np.zeros(need, "float32")
    s = max(0, (need - len(seg)) // 2)
    out[s:s + min(len(seg), need)] = seg[:need]
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "models/panns/real_dog.wav"
    a = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    b = float(sys.argv[3]) if len(sys.argv) > 3 else 2.6
    names, kd = load()
    sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    w, sr = sf.read(path, dtype="float32")
    if w.ndim > 1:
        w = w[:, 0]
    seg = resample(w[int(a * sr):int(b * sr)], sr)
    print(f"样本 {path} {a}-{b}s  -> {len(seg)/SR:.2f}s @16k")

    rows = [("A tile 10s", tile_to(seg, 10.0)),
            ("B zero-pad 10s", pad_to(seg, 10.0)),
            ("C tile 2s", tile_to(seg, 2.0)),
            ("D zero-pad 2s", pad_to(seg, 2.0))]
    for tag, x in rows:
        sc = sess.run(None, {"input_audio": x[None, :]})[0][0]
        idx = np.argsort(-sc)[:3]
        top = ", ".join(f"{names[i]}={sc[i]:.2f}" for i in idx)
        keys = " ".join(f"{k}={sc[kd[k]]:.2f}" for k in KEYS if k in kd)
        print(f"{tag:<16} {len(x)/SR:>4.1f}s | top3 {top}")
        print(f"{'':16}       {keys}")


if __name__ == "__main__":
    main()
