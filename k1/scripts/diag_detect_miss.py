#!/usr/bin/env python3
"""诊断「检测丢失」的真实原因：是模型完全没响应，还是分数低被阈值切掉。

对指定的视频时间点，直接读原始输出，打印 cat/dog 的最大分数（不受任何阈值影响），
再对比「全图推理」与「按上一帧框裁 ROI 后推理」两种方式的分数。

用法:
    python scripts/diag_detect_miss.py <视频> [时间点...]
"""
import os
import sys

import cv2
import numpy as np
import onnxruntime as ort

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "k1-vision"))
from vision_service import letterbox  # noqa: E402

CAT, DOG, PERSON = 15, 16, 0


def raw_scores(sess, name, img):
    """返回 (cat, dog, person, 全类 top1 id, top1 分数)。img 已是 BGR。"""
    canvas, _, _, _ = letterbox(img)
    blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    out = sess.run(None, {name: blob})[0]
    pred = out[0]
    if pred.shape[0] < pred.shape[1]:
        pred = pred.T
    scores = pred[:, 4:]
    return (float(scores[:, CAT].max()), float(scores[:, DOG].max()),
            float(scores[:, PERSON].max()),
            int(np.argmax(scores.max(axis=0))), float(scores.max()))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    video = sys.argv[1]
    times = [float(x) for x in sys.argv[2:]] or [3.5, 7.5, 12.3, 16.4]
    model = os.path.join(ROOT, "k1-vision", "yolov8n.onnx")
    sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name

    cap = cv2.VideoCapture(video)
    print(f"{'t':>6}  {'方式':<10}{'cat':>7}{'dog':>7}{'person':>8}   top1")
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, fr = cap.read()
        if not ok:
            print(f"{t:6.1f}  读帧失败")
            continue
        c, d, p, tid, tv = raw_scores(sess, name, fr)
        print(f"{t:6.1f}  {'全图':<10}{c:7.3f}{d:7.3f}{p:8.3f}   id={tid} {tv:.3f}")

        # 中心裁剪 70%：模拟「把画面中央放大」能不能救回来
        h, w = fr.shape[:2]
        cw, ch = int(w * 0.7), int(h * 0.7)
        crop = fr[(h - ch) // 2:(h + ch) // 2, (w - cw) // 2:(w + cw) // 2]
        c2, d2, p2, tid2, tv2 = raw_scores(sess, name, crop)
        print(f"{t:6.1f}  {'中心裁70%':<10}{c2:7.3f}{d2:7.3f}{p2:8.3f}   id={tid2} {tv2:.3f}")

        # 上下左右四分块：目标跨块时单块看不全，但能看出模型是否对局部有响应
        best = (0.0, "")
        for i in range(2):
            for j in range(2):
                tile = fr[i * h // 2:(i + 1) * h // 2, j * w // 2:(j + 1) * w // 2]
                ct, dt2, _, _, _ = raw_scores(sess, name, tile)
                m = max(ct, dt2)
                if m > best[0]:
                    best = (m, f"tile{i}{j}")
        print(f"{t:6.1f}  {'四分块最优':<10}{'':7}{best[0]:7.3f}{'':8}   {best[1]}")
    cap.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
