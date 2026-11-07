#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 PC 上验证部署用的那份 int8 静态 YOLO 是否正常。

不依赖 K1：用的就是 k1-vision/yolov8n-int8-static.onnx 这一份权重，
预处理/后处理与 vision_service.py 保持一致，只把执行后端从 SpaceMIT EP
换成 CPUExecutionProvider。目的是分清「模型坏了」还是「板子环境坏了」。

用法：
  .venv-train/Scripts/python.exe scripts/verify_vision_local.py k1-vision/datasets/pets/*.jpg
"""
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

COCO = (
    "person,bicycle,car,motorcycle,airplane,bus,train,truck,boat,traffic light,"
    "fire hydrant,stop sign,parking meter,bench,bird,cat,dog,horse,sheep,cow,"
    "elephant,bear,zebra,giraffe,backpack,umbrella,handbag,tie,suitcase,frisbee,"
    "skis,snowboard,sports ball,kite,baseball bat,baseball glove,skateboard,"
    "surfboard,tennis racket,bottle,wine glass,cup,fork,knife,spoon,bowl,banana,"
    "apple,sandwich,orange,broccoli,carrot,hot dog,pizza,donut,cake,chair,couch,"
    "potted plant,bed,dining table,toilet,tv,laptop,mouse,remote,keyboard,"
    "cell phone,microwave,oven,toaster,sink,refrigerator,book,clock,vase,"
    "scissors,teddy bear,hair drier,toothbrush"
).split(",")

PET_CLS = {15: "cat", 16: "dog"}


def letterbox(im, size=640, color=(114, 114, 114)):
    w, h = im.size
    r = min(size / w, size / h)
    nw, nh = int(round(w * r)), int(round(h * r))
    im = im.resize((nw, nh), Image.BILINEAR)
    canvas = Image.new("RGB", (size, size), color)
    canvas.paste(im, ((size - nw) // 2, (size - nh) // 2))
    return canvas


def nms(boxes, scores, thr=0.45):
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        a1 = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        a2 = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        order = rest[inter / (a1 + a2 - inter + 1e-9) < thr]
    return keep


def decode(y, conf=0.25):
    p = y[0].T
    sc = p[:, 4:]
    cls = sc.argmax(1)
    best = sc.max(1)
    m = best > conf
    if not m.any():
        return np.zeros((0, 6))
    cx, cy, w, h = p[m, 0], p[m, 1], p[m, 2], p[m, 3]
    box = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1)
    keep = nms(box, best[m])
    return np.column_stack([box[keep], best[m][keep], cls[m][keep]])


def main():
    model = Path("k1-vision/yolov8n-int8-static.onnx")
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    sess = ort.InferenceSession(str(model), so, providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name
    print(f"模型: {model}  ({model.stat().st_size/1e6:.1f} MB)")
    print(f"输入: {sess.get_inputs()[0].shape}  输出: {sess.get_outputs()[0].shape}")

    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.exists():
            continue
        im = Image.open(p).convert("RGB")
        x = np.asarray(letterbox(im), dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
        x = np.ascontiguousarray(x)
        t0 = time.perf_counter()
        y = sess.run(None, {iname: x})[0]
        dt = (time.perf_counter() - t0) * 1000
        d = decode(y)
        print(f"\n=== {p.name}  {im.size[0]}x{im.size[1]}  推理 {dt:.1f} ms  "
              f"检出 {len(d)} 个目标")
        if not len(d):
            print("    （无检出，分数未过 0.25 阈值）")
            continue
        order = np.argsort(-d[:, 4])[:6]
        for i in order:
            cid = int(d[i, 5])
            tag = " ← 宠物" if cid in PET_CLS else ""
            print(f"    {COCO[cid]:14s} conf={d[i,4]:.3f} "
                  f"box=({d[i,0]:.0f},{d[i,1]:.0f},{d[i,2]:.0f},{d[i,3]:.0f}){tag}")


if __name__ == "__main__":
    main()
