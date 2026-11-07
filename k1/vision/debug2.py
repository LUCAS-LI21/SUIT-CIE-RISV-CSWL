#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小验证：只建 session + 跑一次，逐步打印，定位崩溃点。"""
import sys

import numpy as np
import cv2

print("[1] 准备 import", flush=True)
import onnxruntime as ort
print("[2] ort ok", ort.__version__, flush=True)
import spacemit_ort
print("[3] spacemit_ort ok", flush=True)
print("[4] providers:", ort.get_available_providers(), flush=True)

model = sys.argv[1] if len(sys.argv) > 1 else "./yolov8n.q.onnx"
img_path = sys.argv[2] if len(sys.argv) > 2 else "./bus.jpg"

print("[5] 建 session ...", flush=True)
sess = ort.InferenceSession(model, providers=["SpaceMITExecutionProvider", "CPUExecutionProvider"])
print("[6] session ok, providers =", sess.get_providers(), flush=True)

inp = sess.get_inputs()[0]
print("[7] 输入:", inp.name, inp.shape, inp.type, flush=True)
for i, o in enumerate(sess.get_outputs()):
    print("    输出%d:" % i, o.name, o.shape, o.type, flush=True)

img = cv2.imread(img_path)
print("[8] 图片:", None if img is None else img.shape, flush=True)

h, w = img.shape[:2]
scale = min(640 / w, 640 / h)
nw, nh = int(round(w * scale)), int(round(h * scale))
canvas = np.full((640, 640, 3), 114, dtype=np.uint8)
canvas[(640 - nh) // 2:(640 - nh) // 2 + nh, (640 - nw) // 2:(640 - nw) // 2 + nw] = \
    cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)

x = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
if "uint8" in inp.type:
    x = (x * 255).astype(np.uint8)
print("[9] 张量:", x.shape, x.dtype, float(x.min()), float(x.max()), flush=True)

print("[10] 开始推理 ...", flush=True)
y = sess.run(None, {inp.name: x})
print("[11] 推理完成，输出个数:", len(y), flush=True)

for i, arr in enumerate(y):
    a = np.asarray(arr)
    print("    out[%d] %s %s min=%.4f max=%.4f mean=%.4f"
          % (i, a.shape, a.dtype, a.min(), a.max(), a.mean()), flush=True)

a = np.asarray(y[0])
if a.ndim == 3:
    t = a[0]
    if t.shape[0] < t.shape[1]:
        cls = t[4:]
    else:
        cls = t[:, 4:]
    print("[12] 类别分数 max=%.4f  >0.25 个数=%d" % (cls.max(), int((cls > 0.25).sum())), flush=True)
print("[13] 全部完成", flush=True)
