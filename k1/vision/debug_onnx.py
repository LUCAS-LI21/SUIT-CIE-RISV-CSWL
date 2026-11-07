#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断 int8 模型在 K1 上的实际输出 —— 判断"检出 0 个"是模型问题还是解码问题。

用法：python3 debug_onnx.py <model.onnx> <image.jpg>
"""
import sys

import numpy as np

try:
    import onnxruntime as ort
    import spacemit_ort  # noqa: F401  激活 SpaceMIT EP
except ImportError as e:
    print("import 失败:", e)
    sys.exit(1)

import cv2

model = sys.argv[1] if len(sys.argv) > 1 else "./yolov8n.q.onnx"
img_path = sys.argv[2] if len(sys.argv) > 2 else "./bus.jpg"

sess = ort.InferenceSession(model, providers=["SpaceMITExecutionProvider", "CPUExecutionProvider"])
print("model:", model)
print("providers:", sess.get_providers())

ins = sess.get_inputs()
outs = sess.get_outputs()
print("\n--- 输入 ---")
for i in ins:
    print(f"  {i.name}  shape={i.shape}  type={i.type}")
print("--- 输出 ---")
for o in outs:
    print(f"  {o.name}  shape={o.shape}  type={o.type}")

# 预处理：与 vision_service.py 一致（letterbox 640 + BGR->RGB + /255 + CHW + NCHW）
img = cv2.imread(img_path)
if img is None:
    print("图片读取失败:", img_path)
    sys.exit(1)
print(f"\n原图 shape={img.shape}")

h, w = img.shape[:2]
scale = min(640 / w, 640 / h)
nw, nh = int(round(w * scale)), int(round(h * scale))
resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
canvas = np.full((640, 640, 3), 114, dtype=np.uint8)
pw, ph = (640 - nw) // 2, (640 - nh) // 2
canvas[ph:ph + nh, pw:pw + nw] = resized

x = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0

it = ins[0].type
if "uint8" in it:
    x = (x * 255).astype(np.uint8)
    print("输入类型 uint8，按 0-255 送入")
elif "float16" in it:
    x = x.astype(np.float16)
    print("输入类型 float16")

print(f"送入张量: shape={x.shape} dtype={x.dtype} min={x.min():.3f} max={x.max():.3f}")

y = sess.run(None, {ins[0].name: x})
print(f"\n输出个数: {len(y)}")
for idx, arr in enumerate(y):
    arr = np.asarray(arr)
    print(f"  out[{idx}] shape={arr.shape} dtype={arr.dtype} "
          f"min={arr.min():.4f} max={arr.max():.4f} mean={arr.mean():.4f}")
    if arr.size and arr.ndim == 3:
        # 常见布局 [1, 84, 8400] 或 [1, 8400, 84]
        a = arr[0]
        print(f"       首行前 8 个: {np.round(a[0][:8], 4) if a.shape[0] < a.shape[1] else np.round(a[:8, 0], 4)}")
        # 打印类别分数最大值（tx 前 4 行是框，后面是 80 类）
        if a.shape[0] == 84:
            cls = a[4:, :]
            print(f"       类别分数：max={cls.max():.4f}  >0.25 的个数={int((cls > 0.25).sum())}")
        elif a.shape[1] == 84:
            cls = a[:, 4:]
            print(f"       类别分数：max={cls.max():.4f}  >0.25 的个数={int((cls > 0.25).sum())}")
