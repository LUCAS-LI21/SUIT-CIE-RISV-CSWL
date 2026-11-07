#!/usr/bin/env python3
"""quant_cls_int8.py —— 行为分类模型 int8 静态量化 + 量化前后精度对比。

为什么单独写一个：检测模型用的 quantize_int8.py 是 640×640 letterbox + /255，
分类模型是 **短边缩放 + 中心裁剪 + ImageNet 归一化**，预处理完全不同。
拿检测的校准读数去量化分类模型，量化参数是错的——精度会莫名其妙掉 10 个点。

用法：
  python quant_cls_int8.py runs/classify/pet_behavior/weights/best.onnx \
      --data datasets/behavior --out yolov8n-cls-int8.onnx

  # 如果怀疑归一化对不上（fp32 ONNX 的 top1 与训练日志差很多），试这组：
  python quant_cls_int8.py best.onnx --data datasets/behavior --mean 0.485,0.456,0.406 --std 0.229,0.224,0.225

产物：
  <out>                  int8 模型
  控制台打印 fp32 vs int8 的 top-1 与一致率（一致率低于 97% 说明量化掉点明显）
"""
import argparse
import os
import sys

import cv2
import numpy as np


def build_preprocess(imgsz, mean, std):
    def fn(path):
        img = cv2.imread(path)
        if img is None:
            return None
        # 短边缩放到 imgsz*1.14，再中心裁剪 —— 与 ultralytics 分类推理一致
        h, w = img.shape[:2]
        scale = imgsz * 1.14 / min(h, w)
        nh, nw = int(round(h * scale)), int(round(w * scale))
        img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        top = (nh - imgsz) // 2
        left = (nw - imgsz) // 2
        img = img[top:top + imgsz, left:left + imgsz]
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        img = (img - np.array(mean, dtype=np.float32)) / np.array(std, dtype=np.float32)
        return np.ascontiguousarray(img.transpose(2, 0, 1)[None])
    return fn


def collect_val(root):
    """返回 (图片路径列表, 类别名列表)。类别取 val/ 下的子目录名，按字典序。"""
    val_dir = os.path.join(root, "val")
    if not os.path.isdir(val_dir):
        sys.exit(f"找不到 {val_dir}")
    classes = sorted(d for d in os.listdir(val_dir) if os.path.isdir(os.path.join(val_dir, d)))
    items = []
    for idx, c in enumerate(classes):
        cdir = os.path.join(val_dir, c)
        for fn in sorted(os.listdir(cdir)):
            if fn.lower().endswith((".jpg", ".jpeg", ".png")):
                items.append((os.path.join(cdir, fn), idx))
    return items, classes


def evaluate(sess, items, preprocess, limit=0):
    in_name = sess.get_inputs()[0].name
    correct = preds = 0
    top1 = []
    for i, (path, label) in enumerate(items):
        if limit and i >= limit:
            break
        x = preprocess(path)
        if x is None:
            continue
        y = sess.run(None, {in_name: x})[0].reshape(-1)
        p = int(np.argmax(y))
        top1.append(p)
        preds += 1
        correct += int(p == label)
    return (correct / preds if preds else 0.0), top1, preds


def main():
    ap = argparse.ArgumentParser(description="分类模型 int8 静态量化")
    ap.add_argument("onnx", help="FP32 ONNX 模型")
    ap.add_argument("--data", required=True, help="数据集根目录（用 val/ 做校准与对比）")
    ap.add_argument("--out", default="", help="输出 int8 模型路径")
    ap.add_argument("--imgsz", type=int, default=224)
    ap.add_argument("--mean", default="0,0,0", help="归一化均值，逗号分隔（ultralytics 默认 0,0,0）")
    ap.add_argument("--std", default="1,1,1", help="归一化标准差（默认 1,1,1）")
    ap.add_argument("--calib", type=int, default=200, help="校准样本数")
    ap.add_argument("--limit", type=int, default=0, help="评测上限（0=全部）")
    args = ap.parse_args()

    try:
        import onnxruntime as ort
        from onnxruntime.quantization import (CalibrationDataReader, CalibrationMethod,
                                              QuantFormat, QuantType, quantize_static)
    except ImportError:
        sys.exit("缺依赖：pip install onnxruntime onnx")

    mean = [float(x) for x in args.mean.split(",")]
    std = [float(x) for x in args.std.split(",")]
    preprocess = build_preprocess(args.imgsz, mean, std)
    items, classes = collect_val(args.data)
    print(f"验证集 {len(items)} 张，{len(classes)} 类：{classes}")

    fp32 = ort.InferenceSession(args.onnx, providers=["CPUExecutionProvider"])
    acc32, pred32, n = evaluate(fp32, items, preprocess, args.limit)
    print(f"FP32 top-1 = {acc32 * 100:.2f}%（{n} 张）")
    print("提示：这个数字应与训练日志的 top-1 接近（差 3 个点以内）。差很多说明归一化参数不对。")

    class Reader(CalibrationDataReader):
        def __init__(self, paths, limit):
            self.paths = paths[:limit]
            self.i = 0

        def get_next(self):
            while self.i < len(self.paths):
                p = self.paths[self.i]
                self.i += 1
                x = preprocess(p)
                if x is not None:
                    return {fp32.get_inputs()[0].name: x}
            return None

    out = args.out or os.path.splitext(args.onnx)[0] + "-int8.onnx"
    calib_paths = [p for p, _ in items]
    quantize_static(
        model_input=args.onnx,
        model_output=out,
        calibration_data_reader=Reader(calib_paths, args.calib),
        quant_format=QuantFormat.QDQ,
        per_channel=True,
        activation_type=QuantType.QInt8,
        weight_type=QuantType.QInt8,
        calibrate_method=CalibrationMethod.MinMax,
    )
    print(f"int8 模型：{out}  （{os.path.getsize(out) / 1e6:.2f} MB）")

    int8 = ort.InferenceSession(out, providers=["CPUExecutionProvider"])
    acc8, pred8, n8 = evaluate(int8, items, preprocess, args.limit)
    agree = sum(1 for a, b in zip(pred32, pred8) if a == b) / max(1, len(pred32))
    print("=" * 60)
    print(f"FP32 top-1 = {acc32 * 100:.2f}%")
    print(f"int8 top-1 = {acc8 * 100:.2f}%   （掉点 {(acc32 - acc8) * 100:+.2f}）")
    print(f"预测一致率 = {agree * 100:.2f}%")
    print("=" * 60)
    if agree < 0.97:
        print("⚠ 一致率低于 97%：量化掉点明显。三种处理，按顺序试：")
        print("   1) 提高 --calib 到 500，覆盖更多光照/距离；")
        print("   2) 确认 --mean/--std 与训练一致；")
        print("   3) 改用 per_channel=False 或换成动态量化兜底。")
    else:
        print("一致率达标，可以上板验证（EP 需 0 次 fallback）：")
        print("   python3 -c \"import onnxruntime as ort;print(ort.get_available_providers())\"")


if __name__ == "__main__":
    main()
