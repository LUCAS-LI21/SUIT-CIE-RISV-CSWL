#!/usr/bin/env python3
"""对比 fp32 与 int8 模型在「画质逐级劣化」下的检测置信度。

背景：用户用手机/显示器播宠物视频，再用摄像头拍屏幕送进 K1。这条链路
相比「直接喂视频文件」多了：分辨率损失、对焦模糊、屏幕摩尔纹、刷新拍频、
噪点、色偏。int8 量化模型对这类低质量输入的鲁棒性远低于 fp32 —— 这就是
「原来（PC 上 fp32）能认，现在（板端 int8）认不出」的量化解释。

用法：
    .venv-train/Scripts/python.exe scripts/bench_precision.py --img _e2e/cat.jpg
    .venv-train/Scripts/python.exe scripts/bench_precision.py --img a.jpg --img b.jpg
"""
import argparse
import os
import sys

import cv2
import numpy as np
import onnxruntime as ort

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = [
    ("fp32", os.path.join(ROOT, "k1-vision", "yolov8n.onnx")),
    ("int8", os.path.join(ROOT, "k1-vision", "yolov8n-int8-static.onnx")),
    ("board", os.path.join(ROOT, "k1-vision", "official_model_zoo", "yolov8n.q.onnx")),
]
CAT, DOG = 15, 16
NMS_IOU = 0.45


def letterbox(img, size=640):
    h, w = img.shape[:2]
    r = min(size / h, size / w)
    nh, nw = int(round(h * r)), int(round(w * r))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, np.uint8)
    top, left = (size - nh) // 2, (size - nw) // 2
    canvas[top:top + nh, left:left + nw] = resized
    return canvas, r, (left, top)


def preprocess(img):
    lb, r, pad = letterbox(img)
    x = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return np.transpose(x, (2, 0, 1))[None, ...], r, pad


def nms(boxes, scores, iou_thres=NMS_IOU):
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1).clip(0) * (y2 - y1).clip(0)
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = (xx2 - xx1).clip(0) * (yy2 - yy1).clip(0)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_thres]
    return keep


def detect(sess, img, conf_thres=0.25):
    blob, r, (px, py) = preprocess(img)
    out = sess.run(None, {sess.get_inputs()[0].name: blob})[0]
    pred = out[0].T                                    # [8400, 84]
    cls_scores = pred[:, 4:]
    cls = cls_scores.argmax(1)
    conf = cls_scores.max(1)
    mask = conf > conf_thres
    if not mask.any():
        return {}
    p, c, s = pred[mask], cls[mask], conf[mask]
    cx, cy, bw, bh = p[:, 0], p[:, 1], p[:, 2], p[:, 3]
    boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], 1)
    res = {}
    for k in np.unique(c):
        sel = c == k
        idx = nms(boxes[sel], s[sel])
        if idx:
            res[int(k)] = float(s[sel][idx[0]])
    return res


# ------------------------------------------------- 画质劣化（模拟"拍屏幕"）
def degrade(img, level):
    """level 0=原图，4=最差。模拟手机/显示器播放 + 摄像头翻拍的累积损伤。"""
    h, w = img.shape[:2]
    if level == 0:
        return img
    x = img
    if level >= 1:                       # 分辨率塌陷：1080p 屏 → 720p 采样
        s = max(0.35, 1.0 - 0.18 * level)
        x = cv2.resize(x, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        x = cv2.resize(x, (w, h), interpolation=cv2.INTER_LINEAR)
    if level >= 2:                       # 轻微失焦
        k = 3 + 2 * (level - 2)
        x = cv2.GaussianBlur(x, (k | 1, k | 1), 0)
    if level >= 3:                       # 屏幕摩尔纹 + 传感器噪点
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        moire = 9.0 * np.sin(xx * 0.62) + 7.0 * np.sin(yy * 0.48 + xx * 0.11)
        x = np.clip(x.astype(np.float32) + moire[..., None], 0, 255).astype(np.uint8)
        noise = np.random.default_rng(7).normal(0, 6.0, x.shape)
        x = np.clip(x.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    if level >= 4:                       # 室内暗光 + 低对比
        x = cv2.convertScaleAbs(x, alpha=0.72, beta=-18)
        x = cv2.GaussianBlur(x, (5, 5), 0)
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", action="append", required=True)
    a = ap.parse_args()

    sessions = {}
    for tag, path in MODELS:
        if not os.path.exists(path):
            print(f"跳过 {tag}：{path} 不存在")
            continue
        sessions[tag] = ort.InferenceSession(path, providers=["CPUExecutionProvider"])

    names = {CAT: "cat", DOG: "dog", 0: "person"}
    for img_path in a.img:
        img = cv2.imread(img_path)
        if img is None:
            print(f"读不到 {img_path}")
            continue
        print(f"\n{'='*66}\n图: {os.path.basename(img_path)}  {img.shape[1]}x{img.shape[0]}\n{'='*66}")
        head = f"{'画质':<22}" + "".join(f"{t:>10}" for t in sessions)
        print(head)
        print("-" * 66)
        labels = ["L0 原图", "L1 分辨率塌陷", "L2 +失焦", "L3 +摩尔纹/噪点", "L4 +暗光低对比"]
        for lv in range(5):
            d = degrade(img, lv)
            row = f"{labels[lv]:<22}"
            for tag, sess in sessions.items():
                r = detect(sess, d)
                pet = max(r.get(CAT, 0.0), r.get(DOG, 0.0))
                row += f"{pet:>10.3f}" if pet > 0 else f"{'—':>10}"
            print(row)
        # 细节：原图各模型看到了什么
        print("-" * 66)
        for tag, sess in sessions.items():
            r = detect(sess, img)
            detail = ", ".join(f"{names.get(k, k)}={v:.3f}" for k, v in
                               sorted(r.items(), key=lambda kv: -kv[1])[:4])
            print(f"{tag:<8} 原图: {detail or '无检测'}")

    print("\n判读：同一档画质下 fp32 与 int8 差得越多，说明「量化」在低质量输入下")
    print("      掉点越快 —— 这就是「PC 上能认、板端认不出」的量化解释。")


if __name__ == "__main__":
    sys.exit(main())
