#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轻量 PANNs（MobileNet 家族）加载与效果验证。

为什么单独一步：先把「权重能不能用」和「导出/量化有没有弄坏」分开。
否则最后结果不对，分不清是权重问题、导出问题还是量化问题。

关键事实（2026-11-06 核出来的，改之前先看）
    官方 MobileNet 版 PANNs **只有 32 kHz 配置**（n_fft=1024 / hop=320 /
    fmax=14000，melW 形状 513x64），没有 16k 版本。
    而仓库里现有的 Cnn14_16k.onnx 名字叫 16k，melW 却也是 513x64 —— 说明它
    其实是 32 kHz 前端，但 petvoice_service 一直把 16 kHz 波形直接喂进去。
    后果：STFT 的 bin 实际代表 0~8 kHz，melW 却按 0~14 kHz 解释，
    所有频率被低估一半（相当于整体降低一个八度）。
    本脚本两种喂法都跑，用同一批样本量化这个错位到底损失多少。

用法：
    python scripts/panns_light_build.py            # 只验证
    python scripts/panns_light_build.py --export   # 额外导出 ONNX
"""
import argparse
import csv
import sys
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PANNS = ROOT / "models" / "panns"
sys.path.insert(0, str(PANNS))

from models import MobileNetV2  # noqa: E402

# 32 kHz 官方配置。**不要**改成 16000/512，权重是按这套训的。
SR32 = 32000
CFG = dict(sample_rate=SR32, window_size=1024, hop_size=320, mel_bins=64,
           fmin=50, fmax=14000, classes_num=527)

PET_CLASSES = [72, 73, 74, 75, 78, 79, 80, 81, 82, 83, 84]


def load_labels():
    rows = list(csv.DictReader(open(PANNS / "labels.csv", encoding="utf-8")))
    rows.sort(key=lambda r: int(r["index"]))
    return [r["display_name"] for r in rows]


def load_weights(ckpt):
    from safetensors.torch import load_file
    sd = load_file(str(ckpt))
    # HF 存的整模型带 backbone. 前缀；models.py 的类是单个 backbone
    if all(k.startswith("backbone.") for k in sd):
        sd = {k[len("backbone."):]: v for k, v in sd.items()}
    return sd


def build(ckpt):
    model = MobileNetV2(**CFG)
    sd = load_weights(ckpt)
    missing, unexpected = model.load_state_dict(sd, strict=False)
    # num_batches_tracked 之类的 buffer 缺失无害；有权重缺失就必须报错
    real_missing = [k for k in missing if not k.endswith("num_batches_tracked")]
    if real_missing:
        print(f"!! 缺 {len(real_missing)} 个权重，前 5 个：{real_missing[:5]}")
    if unexpected:
        print(f"!! 多出 {len(unexpected)} 个张量，前 5 个：{unexpected[:5]}")
    model.eval()
    return model


def read_wav(path, target_sr):
    """读成单声道 float32，并按 target_sr 重采样（16k→32k 用多项式插值，
    不引入额外依赖；对时长几秒的片段足够）。"""
    import wave
    with wave.open(str(path), "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != target_sr:
        try:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(target_sr, sr)
            x = resample_poly(x, target_sr // g, sr // g).astype(np.float32)
        except ImportError:
            t = np.linspace(0, len(x) - 1, int(len(x) * target_sr / sr))
            x = np.interp(t, np.arange(len(x)), x).astype(np.float32)
    return x


def fit(x, sr, sec):
    """把波形铺/截到固定长度 sec。

    必须**平铺**而不是补零：petvoice_service 的实测结论是，补零后 Dog 从 0.51
    掉到 0.00 —— 零值在 log-mel 里是极小的负值，等于给模型一段"死寂"，
    把 clip-level 的 max 池化稀释掉了。平铺则维持能量密度。
    另外模型本身有最短帧数限制（T 太小时内部池化会算出 0 维），
    0.22 s 的短促吠叫直接喂会 RuntimeError，统一到 2 s 才稳。
    """
    want = int(sr * sec)
    if len(x) == 0:
        return np.zeros(want, dtype=np.float32)
    if len(x) >= want:
        return x[:want].astype(np.float32)
    reps = want // len(x) + 1
    return np.tile(x, reps)[:want].astype(np.float32)


@torch.no_grad()
def scores(model, x):
    """x: float32 波形（32 kHz）→ clip_scores [527]"""
    t = torch.from_numpy(x).unsqueeze(0)
    out = model(t)
    # PANNs 的 forward 返回 dict（clipwise_output / embedding），
    # MobileNet 与 Cnn14 一致；这里三种形态都兜住，免得换模型就得改脚本。
    if isinstance(out, dict):
        out = out["clipwise_output"]
    elif isinstance(out, (tuple, list)):
        out = out[0]
    return out[0].numpy()


def top(s, names, k=5):
    idx = np.argsort(-s)[:k]
    return [(names[i], float(s[i])) for i in idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=str(PANNS / "MobileNetV2_16k.safetensors"))
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--onnx", default=str(PANNS / "MobileNetV2_32k.onnx"))
    args = ap.parse_args()

    names = load_labels()
    model = build(args.ckpt)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"MobileNetV2 参数 {n_params / 1e6:.2f} M（Cnn14 约 80 M，比值 1/{80e6 / n_params:.1f}）")

    # 拿 Cnn14 做对照：同一段音频，两种喂法各跑一遍。
    # 用 int8 版：fp32 那 327 MB 在本机 8 GB 上 ORT 初始化直接 bad allocation，
    # 而且 int8 才是线上真正在跑的那份，对照才有意义。
    import onnxruntime as ort
    cnn14 = ort.InferenceSession(str(PANNS / "Cnn14_16k_int8.onnx"),
                                 providers=["CPUExecutionProvider"])

    cases = sorted((ROOT / "k1-vision" / "voice_samples").glob("*.wav"))
    print(f"\n{'样本':26s} {'轻量(32k正确)':26s} {'Cnn14(16k错位)':26s}")
    print("-" * 84)
    for p in cases:
        x32 = fit(read_wav(p, SR32), SR32, 2.0)      # 固定 2 s 窗口
        s = scores(model, x32)
        t3 = top(s, names, 3)

        # Cnn14：按现状喂 16k（错位），再按正确方式喂 32k
        x16 = fit(read_wav(p, 16000), 16000, 2.0)
        c16 = cnn14.run(None, {"input_audio": x16[None, :].astype(np.float32)})[0][0]
        c32 = cnn14.run(None, {"input_audio": x32[None, :].astype(np.float32)})[0][0]
        d16, d32 = top(c16, names, 1)[0], top(c32, names, 1)[0]

        st = f"{t3[0][0][:14]} {t3[0][1]:.2f}"
        sc = f"{d16[0][:10]} {d16[1]:.2f} / 32k:{d32[0][:8]} {d32[1]:.2f}"
        print(f"{p.stem:26s} {st:26s} {sc}")

    if args.export:
        dummy = torch.zeros(1, SR32 * 2, dtype=torch.float32)
        torch.onnx.export(model, dummy, args.onnx,
                          input_names=["input_audio"],
                          output_names=["clip_scores", "embedding"],
                          opset_version=17, dynamo=False)
        sz = Path(args.onnx).stat().st_size / 1e6
        print(f"\n导出 {args.onnx}  {sz:.1f} MB")


if __name__ == "__main__":
    main()
