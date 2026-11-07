# -*- coding: utf-8 -*-
"""PANNs Cnn14_16k 推理（AudioSet 527 类）。

为什么用它
  现有 petvoice_service 是规则分类器（mel/MFCC/F0），真实叫声召回不足。
  PANNs 在 AudioSet 上预训练，直接带 Cat(81)/Dog(74)/Bark(75)/Growling(79)/
  Purr(82)/Meow(83) 等类别，不用自己从零训。

模型输入
  该 ONNX 导出把 log-mel 前端**包进图里**了，输入就是原始波形
  （input_audio，float32，16 kHz 单声道），输出 clip_scores[1,527] + embedding[1,2048]。

用法
  python k1-vision/panns_infer.py <wav...>        # 单文件/多文件
  python k1-vision/panns_infer.py --dir datasets/voice/meow   # 整目录
"""
import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import soundfile as sf

HERE = Path(__file__).resolve().parent
MODEL = HERE.parent / "models" / "panns" / "Cnn14_16k.onnx"
LABELS = HERE.parent / "models" / "panns" / "labels.csv"

SR = 16000
CLIP_SEC = 10.0

# AudioSet 里跟宠物叫声相关的类别（index 来自 labels.csv）
PET_CLASSES = {
    72: "Animal",
    73: "Domestic animals, pets",
    74: "Dog",
    75: "Bark",
    78: "Bow-wow",
    79: "Growling",
    80: "Whimper (dog)",
    81: "Cat",
    82: "Purr",
    83: "Meow",
    84: "Hiss",
}


def load_labels() -> list:
    rows = list(csv.DictReader(open(LABELS, encoding="utf-8")))
    rows.sort(key=lambda r: int(r["index"]))
    return [r["display_name"] for r in rows]


def load_audio(path: Path) -> np.ndarray:
    wav, sr = sf.read(str(path), dtype="float32", always_2d=True)
    wav = wav.mean(axis=1)
    if sr != SR:
        # 简单线性重采样，避免为了这个再拉 librosa/soxr 依赖
        n = int(round(len(wav) * SR / sr))
        idx = np.linspace(0, len(wav) - 1, n)
        wav = np.interp(idx, np.arange(len(wav)), wav).astype("float32")
    need = int(SR * CLIP_SEC)
    if len(wav) < need:
        wav = np.pad(wav, (0, need - len(wav)))
    else:
        wav = wav[:need]
    return wav


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir")
    ap.add_argument("--topk", type=int, default=5)
    a = ap.parse_args()

    files = [Path(p) for p in a.files]
    if a.dir:
        files += sorted(Path(a.dir).glob("*.wav"))[:10]
    if not files:
        print("没有输入文件")
        return 1

    so = ort.SessionOptions()
    so.intra_op_num_threads = 4
    sess = ort.InferenceSession(str(MODEL), sess_options=so,
                                providers=["CPUExecutionProvider"])
    names = load_labels()

    for f in files:
        wav = load_audio(f)
        x = wav[None, :]
        t0 = time.perf_counter()
        out = sess.run(None, {"input_audio": x})
        dt = (time.perf_counter() - t0) * 1000
        scores = out[0][0]

        order = np.argsort(-scores)[:a.topk]
        print(f"\n=== {f.name}  ({dt:.0f} ms) ===")
        for i in order:
            print(f"  {names[i][:34]:34s} {scores[i]:.3f}")
        print("  -- 宠物相关 --")
        for i, name in sorted(PET_CLASSES.items()):
            if scores[i] > 0.05:
                print(f"  {name:22s} {scores[i]:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
