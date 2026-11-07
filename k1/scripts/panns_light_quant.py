#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轻量 PANNs 的 int8 静态量化 + 量化前后对照。

为什么必须做「前后对照」而不是只跑通
    Cnn14 那份 int8 就是这个坑的活例：模型能加载、能出 527 个数、不报任何错，
    但在真实狗叫上一个宠物类都不给（全判成 Silence/Music）。只看"不崩"完全发现不了。
    所以这里逐样本比对 fp32 与 int8 的 top1 是否一致、宠物类分数掉了多少。

校准数据为什么混三类
    只用合成叫声校准 → 量化 scale 偏向那几个音色，真实环境底噪一进来就崩。
    这里用「合成叫声 + 真实狗叫 + 真实环境音(live_capture)」混合，覆盖实际输入分布。

用法：
    python scripts/panns_light_quant.py
产物：
    models/panns/MobileNetV2_32k_int8.onnx
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

from panns_light_build import (PET_CLASSES, SR32, fit, load_labels,  # noqa: E402
                               read_wav, top)

FP32 = PANNS / "MobileNetV2_32k.onnx"
INT8 = PANNS / "MobileNetV2_32k_int8.onnx"
WIN_SEC = 2.0


def sample_files():
    """校准 + 评估用的音频清单。故意混入真实录音。"""
    fs = sorted((ROOT / "k1-vision" / "voice_samples").glob("*.wav"))
    for extra in ("real_dog.wav", "live_capture.wav"):
        p = PANNS / extra
        if p.exists():
            fs.append(p)
    return fs


def build_inputs(sec=WIN_SEC):
    """每个文件切/铺成若干个固定长度窗口（长录音按 1 s 步长滑窗多取几段）。"""
    out = []
    for p in sample_files():
        x = read_wav(p, SR32)
        stride = int(SR32 * 1.0)
        start = 0
        while start < len(x):
            out.append(fit(x[start:], SR32, sec))
            if len(x) <= int(SR32 * sec):
                break
            start += stride
    return out


class Reader(CalibrationDataReader):
    def __init__(self, inputs):
        self.it = iter([{"input_audio": x[None, :].astype(np.float32)}
                        for x in inputs])

    def get_next(self):
        return next(self.it, None)


def run(sess, x):
    return sess.run(None, {"input_audio": x[None, :].astype(np.float32)})[0][0]


def bench(sess, x, n=10):
    for _ in range(2):
        run(sess, x)
    t = time.time()
    for _ in range(n):
        run(sess, x)
    return (time.time() - t) / n * 1000


def main():
    if not FP32.exists():
        print(f"缺 {FP32}，先跑 scripts/panns_light_build.py --export")
        return 1

    names = load_labels()
    inputs = build_inputs()
    print(f"样本窗口 {len(inputs)} 段（每段 {WIN_SEC}s @ {SR32}Hz）")

    t = time.time()
    quantize_static(
        str(FP32), str(INT8),
        calibration_data_reader=Reader(inputs),
        quant_format=QuantFormat.QDQ,
        activation_type=QuantType.QUInt8,
        weight_type=QuantType.QInt8,
        per_channel=True,
        reduce_range=False,
        calibrate_method=CalibrationMethod.MinMax,
    )
    print(f"量化完成 {time.time() - t:.1f}s  "
          f"{INT8.stat().st_size / 1e6:.1f} MB（fp32 {FP32.stat().st_size / 1e6:.1f} MB）")

    f32 = ort.InferenceSession(str(FP32), providers=["CPUExecutionProvider"])
    i8 = ort.InferenceSession(str(INT8), providers=["CPUExecutionProvider"])

    print(f"\n{'#':3s} {'fp32 top1':24s} {'int8 top1':24s} 一致  宠物类分数")
    print("-" * 80)
    same = 0
    worst_pet = 0.0
    n = min(12, len(inputs))
    for i, x in enumerate(inputs[:n]):
        a, b = run(f32, x), run(i8, x)
        ta, tb = top(a, names, 1)[0], top(b, names, 1)[0]
        ok = ta[0] == tb[0]
        same += ok
        pa = max(float(a[j]) for j in PET_CLASSES)
        pb = max(float(b[j]) for j in PET_CLASSES)
        worst_pet = max(worst_pet, abs(pa - pb))
        print(f"{i:<3d} {ta[0][:22]:24s} {tb[0][:22]:24s} {'✓' if ok else '✗'}   "
              f"{pa:.2f}->{pb:.2f}")

    print(f"\ntop1 一致率 {same}/{n}  宠物类最大偏移 {worst_pet:.3f}")
    lat = bench(i8, inputs[0])
    print(f"int8 单次推理 {lat:.1f} ms（PC，单线程场景未限核）")
    return 0 if same >= n * 0.9 else 1


if __name__ == "__main__":
    sys.exit(main())
