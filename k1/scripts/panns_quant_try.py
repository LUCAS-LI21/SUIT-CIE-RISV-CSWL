#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量化配置对比：找出既能压到 int8、又不把宠物类分数打掉的方案。

背景（2026-11-06）
    默认 QDQ 全图量化后 top1 一致率只有 5/12，真实狗叫 Bow-wow 0.27→0.09。
    怀疑是把 STFT + log-mel 前端一起量化了 —— 音频幅度动态范围极大，
    前端一压就失真，后面再准也没用。

对比四组：
    A 全图           基准，已知很差
    B 排除前端       按节点名排除 spectrogram_extractor / logmel_extractor / bn0
    C 只量化 Conv    前端天然不参与，且 MatMul(分类头) 保持 fp32
    D B + 百分位校准 用 99.99% 分位而非极值定 scale，抗离群点

用法：python scripts/panns_quant_try.py
"""
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from onnxruntime.quantization import (CalibrationDataReader, CalibrationMethod,
                                      QuantFormat, QuantType, quantize_static)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PANNS = ROOT / "models" / "panns"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PANNS))

from panns_light_build import PET_CLASSES, SR32, fit, load_labels, read_wav, top  # noqa: E402

FP32 = PANNS / "MobileNetV2_32k.onnx"
FRONT_KEYS = ("spectrogram_extractor", "logmel_extractor", "bn0")


class Reader(CalibrationDataReader):
    def __init__(self, inputs):
        self.it = iter([{"input_audio": x[None, :].astype(np.float32)}
                        for x in inputs])

    def get_next(self):
        return next(self.it, None)


def front_nodes():
    import onnx
    m = onnx.load(str(FP32), load_external_data=False)
    return [n.name for n in m.graph.node if any(k in n.name for k in FRONT_KEYS)]


def build_inputs():
    files = sorted((ROOT / "k1-vision" / "voice_samples").glob("*.wav"))
    for extra in ("real_dog.wav", "live_capture.wav"):
        if (PANNS / extra).exists():
            files.append(PANNS / extra)
    out = []
    for p in files:
        x = read_wav(p, SR32)
        start = 0
        while start < len(x):
            out.append(fit(x[start:], SR32, 2.0))
            if len(x) <= SR32 * 2:
                break
            start += SR32
    return out


def run(sess, x):
    return sess.run(None, {"input_audio": x[None, :].astype(np.float32)})[0][0]


def evaluate(tag, path, inputs, ref, names):
    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    same = 0
    dog = []
    for i, x in enumerate(inputs):
        b = run(sess, x)
        if top(b, names, 1)[0][0] == top(ref[i], names, 1)[0][0]:
            same += 1
        # 只看「真实狗叫」那几段的宠物类分数，这是有没有用的硬指标
        if i >= 8 and i <= 10:
            dog.append(round(max(float(b[j]) for j in PET_CLASSES), 2))
    for _ in range(2):
        run(sess, inputs[0])
    t = time.time()
    for _ in range(10):
        run(sess, inputs[0])
    ms = (time.time() - t) / 10 * 1000
    print(f"{tag:16s} 一致 {same:2d}/{len(inputs)}  狗叫宠物类 {dog}  "
          f"{ms:5.1f} ms  {path.stat().st_size / 1e6:.1f} MB")
    return same


def main():
    names = load_labels()
    inputs = build_inputs()
    f32 = ort.InferenceSession(str(FP32), providers=["CPUExecutionProvider"])
    ref = [run(f32, x) for x in inputs]
    print(f"参考：真实狗叫三段（#8-10）宠物类分数 "
          f"{[round(max(float(ref[i][j]) for j in PET_CLASSES), 2) for i in (8, 9, 10)]}\n")

    fr = front_nodes()
    print(f"前端节点 {len(fr)} 个将被排除\n")

    cfgs = [
        ("A 全图", dict(), None),
        ("B 排除前端", dict(nodes_to_exclude=fr), None),
        ("C 只量化Conv", dict(op_types_to_quantize=["Conv"]), None),
        ("D B+百分位", dict(nodes_to_exclude=fr), CalibrationMethod.Percentile),
    ]
    for tag, kw, method in cfgs:
        out = PANNS / f"_try_{tag.split()[0]}.onnx"
        t = time.time()
        quantize_static(
            str(FP32), str(out),
            calibration_data_reader=Reader(inputs),
            quant_format=QuantFormat.QDQ,
            activation_type=QuantType.QUInt8,
            weight_type=QuantType.QInt8,
            per_channel=True, reduce_range=False,
            calibrate_method=method or CalibrationMethod.MinMax,
            **kw)
        evaluate(tag, out, inputs, ref, names)
        out.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
