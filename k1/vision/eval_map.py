#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视觉检测 mAP 评测 —— 为评分表「识别准确性（9 分）」提供可复现的数字。

背景：`bench_pets.py` 给的是「逐图检出置信度 + 三段时延」，**不是 mAP**；
      而评分表要的是准确率（≥90% 得 9 分 / ≥80% 得 6 分 / ≥70% 得 3 分 / <70% 得 0 分）。
      置信度 0.87 只能说明「模型对这张图有把握」，不能说明「换 200 张图还能不能找对」。
      本脚本补的就是这个缺口。

设计原则（与 `eval_dataset.py` 一致）：
    复用线上服务的 `letterbox / decode_yolov8`，不另写一套前后处理 ——
    否则测的东西和跑的东西不是同一个，数字再漂亮也没意义。

用法（本地 CPU 冒烟测试）：
    python eval_map.py --model yolov8n-int8-static.onnx \
        --images datasets/coco8/images/val --labels datasets/coco8/labels/val

用法（板上，走 SpaceMIT EP）：
    python3 eval_map.py --model yolov8n.q.onnx \
        --images datasets/pets/images/val --labels datasets/pets/labels/val --json

参数说明：
    --conf  默认 0.001。mAP 必须在「高召回」的设置下算，不能用线上 0.25 的阈值，
            否则置信度 0.2 的真阳性会被当成漏检，mAP 被人为压低。
    --iou   默认 0.7，与 Ultralytics 校验默认一致。
    --classes  默认 15,16（COCO 的 cat/dog）。传 all 表示不筛类别。

注意：本脚本只评「检测」。「行为 12 类」与「叫声 7 类」是另外两套评测，不要混为一谈。
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

# 允许在 k1-vision/ 目录下直接运行，也允许从仓库根目录运行
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import onnxruntime as ort          # noqa: E402

import vision_service as V         # noqa: E402

try:
    import cv2
except ImportError:
    cv2 = None

from PIL import Image              # noqa: E402


IMG_EXTS = (".jpg", ".jpeg", ".png", ".bmp")


# ────────────────────────── 图像 IO / letterbox ──────────────────────────

def load_bgr(path):
    """读成 BGR ndarray，与 vision_service 里 frame 的通道序一致。"""
    if cv2 is not None:
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise IOError(f"读不到图: {path}")
        return img
    with Image.open(path) as im:
        rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
    return rgb[:, :, ::-1].copy()


def letterbox_np(img, new_size=V.IMG_SIZE, color=114):
    """与 vision_service.letterbox 同算法的实现，仅在无 cv2 时兜底。

    有 cv2 时优先用原函数，保证与线上完全一致（resize 插值实现细节会差几个像素，
    对 mAP 的影响在 0.1% 量级，但仍优先用原函数）。
    """
    if cv2 is not None:
        return V.letterbox(img, new_size, color)
    h, w = img.shape[:2]
    scale = min(new_size / w, new_size / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = np.asarray(
        Image.fromarray(img[:, :, ::-1]).resize((nw, nh), Image.BILINEAR)
    )[:, :, ::-1]
    canvas = np.full((new_size, new_size, 3), color, dtype=np.uint8)
    pad_w = (new_size - nw) // 2
    pad_h = (new_size - nh) // 2
    canvas[pad_h:pad_h + nh, pad_w:pad_w + nw] = resized
    return canvas, scale, pad_w, pad_h


# ────────────────────────── 标注读取 ──────────────────────────

def read_yolo_label(path, w, h):
    """YOLO txt → [(cls, x1, y1, x2, y2)] 原图绝对坐标。"""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 5:
                continue
            c, cx, cy, bw, bh = (float(v) for v in parts[:5])
            out.append((int(c),
                        (cx - bw / 2) * w, (cy - bh / 2) * h,
                        (cx + bw / 2) * w, (cy + bh / 2) * h))
    return out


# ────────────────────────── IoU / AP ──────────────────────────

def iou_xyxy(a, b):
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    denom = area_a + area_b - inter
    return inter / denom if denom > 0 else 0.0


def average_precision(recall, precision):
    """全点插值 AP（COCO 口径的简化版：把 precision 包络成单调不增再积分）。"""
    if len(recall) == 0:
        return 0.0
    mrec = np.concatenate(([0.0], recall, [1.0]))
    mpre = np.concatenate(([1.0], precision, [0.0]))
    for i in range(len(mpre) - 2, -1, -1):
        mpre[i] = max(mpre[i], mpre[i + 1])
    idx = np.where(mrec[1:] != mrec[:-1])[0]
    return float(np.sum((mrec[idx + 1] - mrec[idx]) * mpre[idx + 1]))


def eval_class(dets, gts, npos, iou_thr):
    """单个 IoU 阈值、单个类别的 AP。

    dets: [(conf, (x1,y1,x2,y2), img_id)]  已按本类过滤
    gts:  {img_id: [box, ...]}
    npos: 该类真值总数
    """
    if npos == 0:
        return None, 0, 0     # 该类无真值，按 COCO 规则不计入
    dets = sorted(dets, key=lambda d: -d[0])
    matched = {k: [False] * len(v) for k, v in gts.items()}
    tp = np.zeros(len(dets))
    fp = np.zeros(len(dets))
    for i, (conf, box, img_id) in enumerate(dets):
        best_iou, best_j = 0.0, -1
        for j, g in enumerate(gts.get(img_id, [])):
            iou = iou_xyxy(box, g)
            if iou > best_iou:
                best_iou, best_j = iou, j
        if best_iou >= iou_thr and best_j >= 0 and not matched[img_id][best_j]:
            matched[img_id][best_j] = True
            tp[i] = 1
        else:
            fp[i] = 1
    ctp = np.cumsum(tp)
    cfp = np.cumsum(fp)
    recall = ctp / npos
    precision = ctp / np.maximum(ctp + cfp, 1e-9)
    return average_precision(recall, precision), int(tp.sum()), int(fp.sum())


# ────────────────────────── 主流程 ──────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--images", required=True, help="图片目录")
    ap.add_argument("--labels", required=True, help="YOLO 标注目录（与图片同名 .txt）")
    ap.add_argument("--conf", type=float, default=0.001)
    ap.add_argument("--iou", type=float, default=0.7, help="NMS 的 IoU（与校验口径一致）")
    ap.add_argument("--classes", default="15,16", help="逗号分隔的类 id，或 all")
    ap.add_argument("--imgsz", type=int, default=V.IMG_SIZE)
    ap.add_argument("--json", action="store_true", help="额外输出一段 JSON")
    args = ap.parse_args()

    keep = None if args.classes.strip().lower() == "all" else \
        {int(x) for x in args.classes.split(",") if x.strip()}

    files = sorted(
        p for p in glob.glob(os.path.join(args.images, "*"))
        if p.lower().endswith(IMG_EXTS)
    )
    if not files:
        print(f"[error] {args.images} 下没有图片")
        return 2

    # 用 0.001 的高召回阈值算 mAP。decode_yolov8 读的是模块级常量，先替换再还原。
    old_conf = V.CONF_THRES
    V.CONF_THRES = args.conf
    old_iou = V.NMS_IOU
    V.NMS_IOU = args.iou
    try:
        sess = ort.InferenceSession(args.model, providers=ort.get_available_providers())
        input_name = sess.get_inputs()[0].name
        providers = sess.get_providers()

        gts, dets = {}, {c: [] for c in (keep or set())}
        dets_all = {}
        npos = {}
        n_img_with_gt = 0

        for img_id, path in enumerate(files):
            bgr = load_bgr(path)
            h, w = bgr.shape[:2]
            stem = os.path.splitext(os.path.basename(path))[0]
            label_path = os.path.join(args.labels, stem + ".txt")
            g = read_yolo_label(label_path, w, h)
            if keep is not None:
                g = [t for t in g if t[0] in keep]
            gts[img_id] = [t[1:] for t in g]
            if g:
                n_img_with_gt += 1
            for t in g:
                npos[t[0]] = npos.get(t[0], 0) + 1

            canvas, scale, pw, ph = letterbox_np(bgr, args.imgsz)
            blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
            outs = sess.run(None, {input_name: blob})
            preds = V.decode_yolov8(outs[0], scale, pw, ph, w, h)
            for cid, conf, box in preds:
                if keep is not None and cid not in keep:
                    continue
                dets_all.setdefault(cid, []).append((conf, box, img_id))
    finally:
        V.CONF_THRES = old_conf
        V.NMS_IOU = old_iou

    if not dets_all and not npos:
        print("[warn] 既没有检出也没有真值 —— 检查 --classes 与标注目录是否匹配")
        return 2

    cls_ids = sorted(set(list(dets_all.keys()) + list(npos.keys())))
    names = {**V.PET_CLASSES, 0: "person"}
    rows, aps50, aps_all = [], [], []
    for cid in cls_ids:
        aps_this = []
        for thr in np.arange(0.5, 1.0, 0.05):
            apv, tp_n, fp_n = eval_class(dets_all.get(cid, []), gts, npos.get(cid, 0), float(thr))
            if apv is None:
                aps_this = []
                break
            aps_this.append(apv)
            if abs(thr - 0.5) < 1e-6:
                rows.append({
                    "class_id": cid,
                    "class_name": names.get(cid, str(cid)),
                    "gt": npos.get(cid, 0),
                    "det": len(dets_all.get(cid, [])),
                    "ap50": round(apv, 4),
                    "tp": tp_n,
                    "fp": fp_n,
                })
        if aps_this:
            aps50.append(aps_this[0])
            aps_all.append(float(np.mean(aps_this)))

    print("=" * 68)
    print(f"模型      : {os.path.basename(args.model)}")
    print(f"provider  : {', '.join(providers)}")
    print(f"图片/有标注: {len(files)} / {n_img_with_gt}")
    print(f"阈值      : conf={args.conf}  nms_iou={args.iou}  classes={'all' if keep is None else sorted(keep)}")
    print("-" * 68)
    print(f"{'类':<10}{'GT':>6}{'检出':>7}{'AP@0.5':>10}{'TP':>7}{'FP':>7}")
    for r in rows:
        print(f"{r['class_name']:<10}{r['gt']:>6}{r['det']:>7}{r['ap50']:>10.4f}"
              f"{r['tp']:>7}{r['fp']:>7}")
    print("-" * 68)
    if aps50:
        m50 = float(np.mean(aps50))
        mall = float(np.mean(aps_all))
        print(f"mAP@0.5      : {m50:.4f}   → 评分表「识别准确性」参考 "
              f"{'9 分档(≥90%)' if m50 >= 0.9 else '6 分档(≥80%)' if m50 >= 0.8 else '3 分档(≥70%)' if m50 >= 0.7 else '0 分档(<70%)'}")
        print(f"mAP@0.5:0.95 : {mall:.4f}")
    else:
        m50 = mall = None
        print("[warn] 没有可评的类别（真值为 0 或标注为空）")
    print("=" * 68)

    if args.json:
        print(json.dumps({
            "model": os.path.basename(args.model),
            "providers": providers,
            "images": len(files),
            "images_with_gt": n_img_with_gt,
            "conf": args.conf,
            "nms_iou": args.iou,
            "per_class": rows,
            "mAP50": None if m50 is None else round(m50, 4),
            "mAP50_95": None if mall is None else round(mall, 4),
        }, ensure_ascii=False, indent=2))

    if m50 is not None and not n_img_with_gt:
        print("[warn] 所有图都没有标注，数字无意义")
    return 0


if __name__ == "__main__":
    sys.exit(main())
