#!/usr/bin/env python3
"""
compare_int8.py —— 用同一张真实图片，对比 FP32 / 动态 int8 / 静态 int8 三种模型的
检测结果差异。判断标准不是"输出张量像不像"，而是"框还在不在"。

用法：
  python compare_int8.py bus.jpg yolov8n.onnx yolov8n-int8.onnx yolov8n-int8-static.onnx
"""
import sys

import cv2
import numpy as np
import onnxruntime as ort

COCO = ("person,bicycle,car,motorcycle,airplane,bus,train,truck,boat,traffic light,fire hydrant,"
        "stop sign,parking meter,bench,bird,cat,dog,horse,sheep,cow,elephant,bear,zebra,giraffe,"
        "backpack,umbrella,handbag,tie,suitcase,frisbee,skis,snowboard,sports ball,kite,"
        "baseball bat,baseball glove,skateboard,surfboard,tennis racket,bottle,wine glass,cup,"
        "fork,knife,spoon,bowl,banana,apple,sandwich,orange,broccoli,carrot,hot dog,pizza,donut,"
        "cake,chair,couch,potted plant,bed,dining table,toilet,tv,laptop,mouse,remote,keyboard,"
        "cell phone,microwave,oven,toaster,sink,refrigerator,book,clock,vase,scissors,teddy bear,"
        "hair drier,toothbrush").split(",")


def letterbox(img, new=(640, 640), color=(114, 114, 114)):
    h, w = img.shape[:2]
    r = min(new[0] / h, new[1] / w)
    nh, nw = int(round(h * r)), int(round(w * r))
    img = cv2.resize(img, (nw, nh))
    t, l = (new[0] - nh) // 2, (new[1] - nw) // 2
    return cv2.copyMakeBorder(img, t, new[0] - nh - t, l, new[1] - nw - l,
                              cv2.BORDER_CONSTANT, value=color)


def preprocess(path):
    img = letterbox(cv2.imread(path))
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(img.transpose(2, 0, 1)[None].astype(np.float32) / 255.0)


def nms(boxes, scores, iou_thr=0.45):
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
        iou = inter / (a1 + a2 - inter + 1e-9)
        order = rest[iou < iou_thr]
    return keep


def decode(y, conf=0.25):
    """(1,84,8400) -> [x1,y1,x2,y2,score,cls]"""
    p = y[0].T
    sc = p[:, 4:]
    cls = sc.argmax(1)
    best = sc.max(1)
    m = best > conf
    if not m.any():
        return np.zeros((0, 6))
    cx, cy, w, h = p[m, 0], p[m, 1], p[m, 2], p[m, 3]
    b = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1)
    keep = nms(b, best[m])
    return np.column_stack([b[keep], best[m][keep], cls[m][keep]])


def iou_mat(a, b):
    if not len(a) or not len(b):
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    ar = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    br = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / (ar[:, None] + br[None, :] - inter + 1e-9)


def main():
    img = sys.argv[1]
    models = sys.argv[2:]
    x = preprocess(img)
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL

    res = {}
    for p in models:
        s = ort.InferenceSession(p, so, providers=["CPUExecutionProvider"])
        y = s.run(None, {s.get_inputs()[0].name: x})[0]
        raw_max = float(y[0, 4:, :].max())
        d = decode(y)
        res[p] = (y, d, raw_max)
        print(f"\n=== {p}  输出{y.shape}  全图最高类分数={raw_max:.4f}  检出(det)数={len(d)}")
        order = np.argsort(-d[:, 4])[:8] if len(d) else []
        for i in order:
            print(f"    {COCO[int(d[i,5])]:12s} score={d[i,4]:.4f} box=({d[i,0]:.0f},{d[i,1]:.0f},{d[i,2]:.0f},{d[i,3]:.0f})")

    base = res[models[0]][1]
    print("\n=== 相对 FP32 的检测一致性 (IoU>0.5 且类别相同算命中) ===")
    for p in models[1:]:
        d = res[p][1]
        if not len(d) or not len(base):
            print(f"{p}: TP=0 FP={len(d)} FN={len(base)}  (原因: int8 侧或 fp32 侧无检出)")
            continue
        m = iou_mat(d, base)
        hit = (m > 0.5) & (d[:, 5][:, None] == base[:, 5][None, :])
        tp = int(hit.any(1).sum())
        print(f"{p}: TP={tp}  FP={len(d)-tp}  FN={len(base)-tp}  (fp32 基准 {len(base)} 框)")
        ious = m.max(1)
        print(f"    与 fp32 框的最大 IoU: 均值={ious.mean():.3f} 最小={ious.min():.3f}")


if __name__ == "__main__":
    main()
