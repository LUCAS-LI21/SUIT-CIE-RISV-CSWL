#!/usr/bin/env python3
"""
quantize_int8.py —— 把 FP32 yolov8n.onnx 量化成 int8 ONNX，并做量化前后数值对比。

两种模式：
  1) dynamic（默认）：onnxruntime.quantization.quantize_dynamic，weight_type=QUInt8。
     不需要 torch、不需要校准集，只把 Conv 的权重压成 int8，激活值在运行时
     用 DynamicQuantizeLinear 逐张量动态量化，算子形态变成 ConvInteger。
  2) static：quantize_static（QDQ 格式），需要校准集。用 --calib 指定图片目录，
     默认退回用单张 bus.jpg 做 8 种几何变换当校准集（覆盖度很弱，仅验证流程）。

用法：
  python quantize_int8.py yolov8n.onnx -o yolov8n-int8.onnx                 # 动态
  python quantize_int8.py yolov8n.onnx -o yolov8n-int8-qdq.onnx --mode static
  python quantize_int8.py yolov8n.onnx -o ... --eval bus.jpg                # 带精度对比
"""
import argparse
import collections
import os
import time

import cv2
import numpy as np
import onnx
import onnxruntime as ort


# ---------------- 预处理（与 ultralytics 推理一致：letterbox + RGB + NCHW + /255） ----------------
def letterbox(img, new=(640, 640), color=(114, 114, 114)):
    h, w = img.shape[:2]
    r = min(new[0] / h, new[1] / w)
    nh, nw = int(round(h * r)), int(round(w * r))
    img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    top = (new[0] - nh) // 2
    bottom = new[0] - nh - top
    left = (new[1] - nw) // 2
    right = new[1] - nw - left
    return cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)


def preprocess(path):
    img = cv2.imread(path)
    if img is None:
        raise SystemExit(f"读不到图片: {path}")
    img = letterbox(img)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    x = img.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    return np.ascontiguousarray(x)


# ---------------- 静态量化用的校准数据读取器 ----------------
class CalibReader:
    """按 batch 吐出预处理好的张量。图片不足时用几何变换凑数。"""

    def __init__(self, img_path, n=8):
        base = cv2.imread(img_path)
        if base is None:
            raise SystemExit(f"校准图片读不到: {img_path}")
        self.samples = []
        variants = [
            lambda m: m,
            lambda m: cv2.flip(m, 1),
            lambda m: cv2.convertScaleAbs(m, alpha=1.15, beta=10),
            lambda m: cv2.convertScaleAbs(m, alpha=0.85),
            lambda m: m[::2, ::2],
            lambda m: cv2.GaussianBlur(m, (5, 5), 1.2),
            lambda m: cv2.flip(m, 0),
            lambda m: cv2.convertScaleAbs(m, alpha=1.3, beta=-20),
        ]
        for i in range(n):
            v = variants[i % len(variants)](base)
            img = letterbox(v)
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            self.samples.append(img.transpose(2, 0, 1)[None].astype(np.float32) / 255.0)
        self.i = 0

    def get_next(self):
        if self.i >= len(self.samples):
            return None
        d = {self.input_name: self.samples[self.i]}
        self.i += 1
        return d

    def rewind(self):
        self.i = 0


# ---------------- YOLOv8 输出解码（只取检测框/最高类分数，够做数值对比） ----------------
def decode(y, conf=0.25):
    """y: (1,84,8400) -> 每列的 cx,cy,w,h + 80 类分数。返回超过阈值的 (cls, score, box)。"""
    p = y[0].T                       # (8400, 84)
    scores = p[:, 4:]
    cls = scores.argmax(1)
    sc = scores.max(1)
    keep = sc > conf
    return np.column_stack([cls[keep], sc[keep], p[keep, :4]])


def op_inventory(path):
    m = onnx.load(path)
    c = collections.Counter(n.op_type for n in m.graph.node)
    return c


def bench(sess, x, runs=10):
    name = sess.get_inputs()[0].name
    for _ in range(3):
        sess.run(None, {name: x})
    ts = []
    for _ in range(runs):
        t = time.perf_counter()
        sess.run(None, {name: x})
        ts.append((time.perf_counter() - t) * 1000)
    return sum(ts) / len(ts), min(ts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--mode", choices=["dynamic", "static"], default="dynamic")
    ap.add_argument("--weight-type", default="QUInt8", choices=["QUInt8", "QInt8"])
    ap.add_argument("--calib", default="bus.jpg", help="静态量化的校准图片")
    ap.add_argument("--calib-n", type=int, default=8)
    ap.add_argument("--format", choices=["qoperator", "qdq"], default="qoperator",
                    help="静态量化的算子形态。opset12 只能用 qoperator："
                         "qdq 会给 DequantizeLinear 加 axis 属性，那是 opset13+ 才有的，"
                         "ORT 会直接判 INVALID_GRAPH")
    ap.add_argument("--exclude-output", action="store_true",
                    help="静态量化时排除产生图输出的那个节点。YOLOv8 的输出张量 84x8400 "
                         "把 0~640 的框坐标和 0~1 的类别分数混在一起，per-tensor uint8 量化会 "
                         "取到 scale≈2.5，直接把 0.9 的置信度压成 0（实测检出数归零）")
    ap.add_argument("--eval", default=None, help="评估用图片，默认用 --calib 那张")
    args = ap.parse_args()

    from onnxruntime.quantization import (QuantFormat, QuantType, CalibrationMethod,
                                          quantize_dynamic, quantize_static)

    wt = QuantType.QUInt8 if args.weight_type == "QUInt8" else QuantType.QInt8

    print(f"[quantize] mode={args.mode} weight_type={wt} in={args.model} out={args.out}")
    if args.mode == "dynamic":
        # 动态量化：只重写权重，不插校准。Conv -> ConvInteger，权重 int8，激活运行时量化。
        quantize_dynamic(args.model, args.out, weight_type=wt)
    else:
        # 静态量化：QDQ 格式，Conv -> QLinearConv/ConvInteger + 显式 scale/zero_point。
        reader = CalibReader(args.calib, args.calib_n)
        # 校准器需要知道输入名，从模型里读
        m = onnx.load(args.model)
        reader.input_name = m.graph.input[0].name
        qf = QuantFormat.QOperator if args.format == "qoperator" else QuantFormat.QDQ
        exclude = None
        if args.exclude_output:
            gout = m.graph.output[0].name
            exclude = [n.name for n in m.graph.node if gout in n.output]
            print(f"[quantize] 排除输出节点: {exclude}")
        quantize_static(
            args.model, args.out, reader,
            quant_format=qf,
            activation_type=QuantType.QInt8 if args.weight_type == "QInt8" else QuantType.QUInt8,
            weight_type=wt,
            per_channel=(args.format == "qdq"),  # qoperator 下 per-channel 走 QLinearConv 内部，无需 axis
            calibrate_method=CalibrationMethod.MinMax,
            nodes_to_exclude=exclude,
        )

    print(f"[size] fp32={os.path.getsize(args.model)} bytes  int8={os.path.getsize(args.out)} bytes")

    # 量化后图里的量化算子
    c = op_inventory(args.out)
    for k in ["ConvInteger", "DynamicQuantizeLinear", "QLinearConv", "QuantizeLinear",
              "DequantizeLinear", "MatMulInteger", "Cast"]:
        if c.get(k):
            print(f"[ops] {k}: {c[k]}")

    if not args.eval:
        return
    img = args.eval if os.path.exists(args.eval) else args.calib
    if not os.path.exists(img):
        print("[eval] 没有可用图片，跳过精度对比")
        return

    x = preprocess(img)
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL  # 关优化，纯算子对比
    sf = ort.InferenceSession(args.model, so, providers=["CPUExecutionProvider"])
    sq = ort.InferenceSession(args.out, so, providers=["CPUExecutionProvider"])
    name = sf.get_inputs()[0].name

    yf = sf.run(None, {name: x})[0]
    yq = sq.run(None, {name: x})[0]

    diff = np.abs(yf - yq)
    cos = float(yf.ravel() @ yq.ravel() / (np.linalg.norm(yf) * np.linalg.norm(yq)))
    print(f"[acc] 输出 shape fp32={yf.shape} int8={yq.shape}")
    print(f"[acc] max|Δ|={diff.max():.6f}  mean|Δ|={diff.mean():.6f}  cosine={cos:.6f}")

    # 检测层面：比较置信度>0.25 的框数量与最高分
    df, dq = decode(yf), decode(yq)
    print(f"[det] fp32 检出 {len(df)} 个框, int8 检出 {len(dq)} 个框 (conf>0.25)")
    if len(df):
        print(f"[det] fp32 top5 score={np.sort(df[:,1])[::-1][:5].round(4)}")
    if len(dq):
        print(f"[det] int8 top5 score={np.sort(dq[:,1])[::-1][:5].round(4)}")

    mf, _ = bench(sf, x)
    mq, _ = bench(sq, x)
    print(f"[bench] 单帧均值 (ORT_DISABLE_ALL): fp32={mf:.2f} ms  int8={mq:.2f} ms")


if __name__ == "__main__":
    main()
