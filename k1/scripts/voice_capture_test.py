# -*- coding: utf-8 -*-
"""录 10 秒环境音 → PANNs 分类，快速验证模型在真实声音上的表现。

用法：
    python scripts/voice_capture_test.py 10
    python scripts/voice_capture_test.py 10 --wav out.wav   # 同时存 wav

为什么单独一个脚本
  仓库里 datasets/voice 那批是**合成**音频（1 秒左右，大部分是静音），
  拿它测 PANNs 只会得出「Silence」这种荒谬结论。要验证模型必须用真实录音。
"""
import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import sounddevice as sd
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "models" / "panns" / "Cnn14_16k.onnx"
LABELS = ROOT / "models" / "panns" / "labels.csv"
SR = 16000
CLIP = 10.0

PET = {72: "Animal", 73: "Domestic animals, pets", 74: "Dog", 75: "Bark",
       78: "Bow-wow", 79: "Growling", 80: "Whimper (dog)", 81: "Cat",
       82: "Purr", 83: "Meow", 84: "Hiss"}


def labels() -> list:
    rows = list(csv.DictReader(open(LABELS, encoding="utf-8")))
    rows.sort(key=lambda r: int(r["index"]))
    return [r["display_name"] for r in rows]


def resample(w: np.ndarray, sr_in: int, sr_out: int = SR) -> np.ndarray:
    if sr_in == sr_out:
        return w.astype("float32")
    n = int(round(len(w) * sr_out / sr_in))
    return np.interp(np.linspace(0, len(w) - 1, n),
                     np.arange(len(w)), w).astype("float32")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("seconds", nargs="?", type=float, default=10.0)
    ap.add_argument("--wav")
    ap.add_argument("--file", help="直接对一个已有 wav 分类，跳过录音（验证模型用）")
    a = ap.parse_args()

    if a.file:
        w, sr = sf.read(a.file, dtype="float32")
        if getattr(w, "ndim", 1) > 1:
            w = w[:, 0]
        rms = float(np.sqrt((w ** 2).mean()))
        print(f"读取 {a.file}  {len(w)/sr:.1f}s @ {sr}Hz  rms={rms:.4f}", flush=True)
    else:
        info = sd.query_devices(kind="input")
        sr = int(info["default_samplerate"])
        print(f"录音 {a.seconds:.0f}s  设备={info['name']}  {sr} Hz ...", flush=True)
        print("   （请现在播放宠物叫声）", flush=True)

        rec = sd.rec(int(a.seconds * sr), samplerate=sr, channels=1, dtype="float32")
        sd.wait()
        w = rec[:, 0]
        rms = float(np.sqrt((w ** 2).mean()))
        print(f"录音完成  rms={rms:.4f} peak={float(np.abs(w).max()):.2f}", flush=True)

        if a.wav:
            sf.write(a.wav, w, sr)
            print(f"已保存 {a.wav}")

    w16 = resample(w, sr)
    need = int(SR * CLIP)
    x = np.pad(w16, (0, need - len(w16))) if len(w16) < need else w16[:need]

    so = ort.SessionOptions()
    so.intra_op_num_threads = 4
    sess = ort.InferenceSession(str(MODEL), sess_options=so,
                                providers=["CPUExecutionProvider"])
    t0 = time.perf_counter()
    scores = sess.run(None, {"input_audio": x[None, :]})[0][0]
    dt = (time.perf_counter() - t0) * 1000

    names = labels()
    print(f"\n推理 {dt:.0f} ms   top-8：")
    for i in np.argsort(-scores)[:8]:
        print(f"  {names[i][:34]:34s} {scores[i]:.3f}")
    print("  -- 宠物类 --")
    for i, n in sorted(PET.items()):
        if scores[i] > 0.03:
            print(f"  {n:22s} {scores[i]:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
