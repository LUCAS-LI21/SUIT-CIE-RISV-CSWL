#!/usr/bin/env python3
"""叫声分类模型 int8 静态量化与精度校验。

为什么必须单独一个脚本、不能复用检测模型的量化流程
--------------------------------------------------
检测模型（YOLOv8n）吃的是 640×640 RGB 三通道、0~255 或 0~1 归一化；
叫声分类模型吃的是 1×1×64×64 的单通道 log-mel，且做了逐样本标准化。
两者的数值分布差了一个数量级以上。拿检测的校准集去量化分类模型，
激活范围会被估错，实测会莫名掉 10 个点以上，而且不报错——只是精度变差。

量化收益
--------
权重量化到 int8 后模型从约 1.1 MB 降到约 0.3 MB。对 K1 的 4 GB 内存
意义不大，真正的收益是推理速度：int8 卷积在 ARM CPU 上有 2~3 倍加速，
单窗从 2~5 ms 降到 1~2 ms。它也便于和 YOLOv8n-int8 统一为全 int8 管线。

用法
----
    python k1-vision/quant_voice_int8.py \
        --onnx runs/voice/voice_cnn.onnx \
        --data k1-vision/datasets/voice
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


def load_eval_set(data_dir: Path, prefer="val"):
    p = data_dir / "voice.npz"
    if not p.exists():
        sys.exit(f"找不到 {p}")
    z = np.load(p, allow_pickle=True)
    X, y, split = z["X"], z["y"], z["split"]
    classes = [str(c) for c in z["classes"]]
    mask = split == prefer
    if mask.sum() == 0:
        mask = np.ones(len(y), dtype=bool)
        print(f"[注意] {prefer} 集为空，改用全量数据做评估（结果会偏乐观）")
    return X[mask], y[mask], classes


def to_input(X):
    """(N,64,64) → (N,1,64,64)，与板上推理端一致。"""
    return X[:, None, :, :].astype(np.float32)


class MelCalib:
    """校准数据读取器：喂 val 集的真实分布，不要用随机噪声。

    用随机噪声校准是常见错误——模型的激活分布由真实输入决定，
    噪声校准会让激活范围偏大，量化步长变粗，精度显著下降。
    """

    def __init__(self, X, input_name, limit=300):
        n = min(limit, len(X))
        self._items = [X[i][None, None, :, :].astype(np.float32) for i in range(n)]
        self._name = input_name
        self._i = 0

    def get_next(self):
        if self._i >= len(self._items):
            return None
        x = self._items[self._i]
        self._i += 1
        return {self._name: x}

    def rewind(self):
        self._i = 0


def predict(sess, X, input_name, bs=256):
    out = []
    for i in range(0, len(X), bs):
        out.append(sess.run(None, {input_name: X[i:i + bs]})[0])
    return np.concatenate(out) if out else np.zeros((0, 1), dtype=np.float32)


def main():
    ap = argparse.ArgumentParser(description="叫声模型 int8 静态量化")
    ap.add_argument("--onnx", required=True, help="fp32 ONNX 路径")
    ap.add_argument("--data", required=True, help="audio_to_dataset.py 输出目录")
    ap.add_argument("--out", help="int8 模型输出路径（默认同目录 _int8.onnx）")
    ap.add_argument("--calib", type=int, default=300, help="校准样本数")
    args = ap.parse_args()

    try:
        import onnxruntime as ort
        from onnxruntime.quantization import (CalibrationMethod, QuantFormat,
                                              QuantType, quantize_static)
    except ImportError:
        sys.exit("缺少 onnxruntime。请先："
                 ".venv-train/Scripts/python.exe -m pip install onnxruntime onnx")

    src = Path(args.onnx)
    if not src.exists():
        sys.exit(f"找不到 {src}")
    dst = Path(args.out) if args.out else src.with_name(src.stem + "_int8.onnx")

    X, y, classes = load_eval_set(Path(args.data))
    Xin = to_input(X)
    print(f"评估集：{len(y)} 个样本，{len(classes)} 类")

    # ── fp32 基线 ──
    s32 = ort.InferenceSession(str(src), providers=["CPUExecutionProvider"])
    in_name = s32.get_inputs()[0].name
    p32 = predict(s32, Xin, in_name)
    acc32 = float((p32.argmax(1) == y).mean())
    size32 = src.stat().st_size / 1e6
    print(f"fp32：准确率 {acc32:.4f}，体积 {size32:.2f} MB，输入名 '{in_name}'")

    # ── 量化 ──
    print(f"开始量化（校准集 {min(args.calib, len(X))} 个真实样本，QDQ 格式）…")
    try:
        quantize_static(
            model_input=str(src), model_output=str(dst),
            calibration_data_reader=MelCalib(X, in_name, args.calib),
            quant_format=QuantFormat.QDQ,
            activation_type=QuantType.QInt8,
            weight_type=QuantType.QInt8,
            per_channel=True,
            calibrate_method=CalibrationMethod.MinMax,
        )
    except TypeError:
        # 老版本 onnxruntime 没有 calibrate_method / per_channel 组合
        quantize_static(
            model_input=str(src), model_output=str(dst),
            calibration_data_reader=MelCalib(X, in_name, args.calib),
            quant_format=QuantFormat.QDQ,
            activation_type=QuantType.QInt8,
            weight_type=QuantType.QInt8,
        )

    s8 = ort.InferenceSession(str(dst), providers=["CPUExecutionProvider"])
    p8 = predict(s8, Xin, in_name)
    acc8 = float((p8.argmax(1) == y).mean())
    same = float((p8.argmax(1) == p32.argmax(1)).mean())
    size8 = dst.stat().st_size / 1e6

    print(f"\nint8：准确率 {acc8:.4f}，体积 {size8:.2f} MB（压缩 {size32 / max(size8, 1e-9):.1f}×）")
    print(f"与 fp32 预测一致率：{same:.4f}")
    print(f"准确率变化：{(acc8 - acc32) * 100:+.2f} 个百分点")

    # 逐类对比，定位是哪个类被量化打坏
    if len(classes) == p8.shape[1]:
        print(f"\n{'类别':<10}{'fp32':>8}{'int8':>8}{'变化':>8}")
        for i, c in enumerate(classes):
            m = y == i
            if m.sum() == 0:
                continue
            a, b = float((p32[m].argmax(1) == i).mean()), float((p8[m].argmax(1) == i).mean())
            print(f"{c:<10}{a:>8.3f}{b:>8.3f}{(b - a) * 100:>+7.1f}pp")

    # ── 判据 ──
    print()
    if same < 0.97 or acc8 < acc32 - 0.03:
        print("[警告] 量化损失偏大，按以下顺序排查：")
        print("  ① 校准集是否来自真实分布（本脚本已用 val 集，若手动改过请核对）")
        print("  ② 校准样本是否太少（>200 更稳，类别不均衡时按类抽样）")
        print("  ③ 改用 per_channel=True 或 QuantFormat.QOperator 再试")
        print("  ④ 若只有某一类掉得厉害，多半是它的激活范围窄，可考虑对该层跳过量化")
        rc = 1
    else:
        print("[通过] 量化损失在可接受范围内，可直接替换板上模型。")
        rc = 0

    print(f"\n部署：把 {dst.name} 拷到 K1 的模型目录，重启 petvoice 服务即可")
    print("      （petvoice_service.py 会自动加载，接口与规则版完全一致）")
    sys.exit(rc)


if __name__ == "__main__":
    main()
