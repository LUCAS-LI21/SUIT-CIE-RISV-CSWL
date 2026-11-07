#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证 vision_service.py 的 ONNX 前后处理是否正确。

思路：同一张图，两条路径各跑一次，比对检测框。
  A. ultralytics 的 YOLO('yolov8n.pt')  —— 黑盒基准，letterbox/解码/NMS 全部由库完成
  B. 我们的 letterbox + decode_yolov8   —— 手写实现

若两者框的坐标一致（IoU > 0.95）、类别一致，说明手写前后处理没有偏差。
这一步非常关键：如果 letterbox 的 padding 或反变换算错，框会整体偏移或缩放，
在"有猫狗"的图上表现为 bbox 位置错误，进而让宽高比 / 稳定性 / 速度全部失真，
而画面看上去"确实检测到了"，错误极难被发现。

用法：
    /e/Python/python.exe verify_decode.py --image ./bus.jpg --model ./yolov8n.onnx
"""
import argparse
import sys

import numpy as np

import vision_service as vs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="./bus.jpg")
    ap.add_argument("--model", default="./yolov8n.onnx")
    args = ap.parse_args()

    import cv2
    import onnxruntime as ort
    from ultralytics import YOLO

    img = cv2.imread(args.image)
    if img is None:
        print(f"图片读取失败: {args.image}")
        sys.exit(1)
    h, w = img.shape[:2]
    print(f"[image] {args.image}  {w}x{h}")

    # ---------- A: ultralytics 基准 ----------
    m = YOLO("yolov8n.pt")
    r = m(img, verbose=False)[0]
    ref = []
    for b in r.boxes:
        cid = int(b.cls.item())
        conf = float(b.conf.item())
        x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
        ref.append((cid, conf, (x1, y1, x2, y2)))
    ref.sort(key=lambda t: -t[1])
    print(f"\n[A] ultralytics 检出 {len(ref)} 个框")
    for cid, conf, bb in ref:
        print(f"    {m.names[cid]:<14} conf={conf:.3f} bbox={[round(v,1) for v in bb]}")

    # ---------- B: 我们的实现 ----------
    so = ort.SessionOptions()
    sess = ort.InferenceSession(args.model, so, providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name

    canvas, scale, pw, ph = vs.letterbox(img)
    blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    out = sess.run(None, {iname: blob})[0]
    print(f"\n[B] onnx 输出 shape = {out.shape}")

    # 放宽类别限制做对比（原始 decode 只保留猫狗/球，这里临时全量比较）
    saved_pet, saved_ball = dict(vs.PET_CLASSES), dict(vs.BALL_CLASSES)
    vs.PET_CLASSES = {i: str(i) for i in range(80)}
    vs.BALL_CLASSES = {}
    mine = vs.decode_yolov8(out, scale, pw, ph, w, h)
    vs.PET_CLASSES, vs.BALL_CLASSES = saved_pet, saved_ball

    mine.sort(key=lambda t: -t[1])
    print(f"[B] 手写解码得到 {len(mine)} 个框")
    names = m.names
    for cid, conf, bb in mine[:12]:
        print(f"    {names.get(cid, cid):<14} conf={conf:.3f} bbox={[round(v,1) for v in bb]}")

    # ---------- 比对 ----------
    print("\n=== 逐框比对（同类别中取 IoU 最大者）===")
    matched = 0
    worst_iou = 1.0
    offsets = []
    for cid, conf, bb in ref:
        best_iou, best = 0.0, None
        for cid2, conf2, bb2 in mine:
            if cid2 != cid:
                continue
            iou = vs.iou(bb, bb2)
            if iou > best_iou:
                best_iou, best = iou, bb2
        if best_iou > 0.5:
            matched += 1
            worst_iou = min(worst_iou, best_iou)
            # 中心点偏移比 IoU 更能暴露系统性偏移：
            # 公式写错会表现为整体平移/缩放，中心偏移量随坐标线性增大；
            # 只是取整差异则偏移量恒定在 1~2px。
            dx = ((bb[0] + bb[2]) / 2) - ((best[0] + best[2]) / 2)
            dy = ((bb[1] + bb[3]) / 2) - ((best[1] + best[3]) / 2)
            offsets.append((abs(dx), abs(dy)))
            flag = "OK  " if best_iou > 0.90 else "偏差"
            print(f"  {flag} {names[cid]:<12} IoU={best_iou:.4f}  "
                  f"中心偏移 dx={dx:+.1f} dy={dy:+.1f} px")
        else:
            print(f"  缺失 {names[cid]:<12} 最高IoU={best_iou:.4f} conf={conf:.3f}")
            worst_iou = 0.0

    if offsets:
        mx = sum(o[0] for o in offsets) / len(offsets)
        my = sum(o[1] for o in offsets) / len(offsets)
        print(f"\n平均中心偏移 = ({mx:.2f}, {my:.2f}) px  "
              f"最大 = ({max(o[0] for o in offsets):.1f}, {max(o[1] for o in offsets):.1f}) px")

    print(f"匹配 {matched}/{len(ref)} 个基准框，最差 IoU = {worst_iou:.4f}")

    # 阈值敏感性：缺失的框是不是刚好卡在 0.25 边界？
    if matched < len(ref):
        old = vs.CONF_THRES
        vs.CONF_THRES = 0.20
        vs.PET_CLASSES = {i: str(i) for i in range(80)}
        vs.BALL_CLASSES = {}
        loose = vs.decode_yolov8(out, scale, pw, ph, w, h)
        vs.CONF_THRES = old
        vs.PET_CLASSES, vs.BALL_CLASSES = saved_pet, saved_ball
        missing = [names[cid] for cid, conf, bb in ref
                   if not any(vs.iou(bb, bb2) > 0.5
                              for c2, _, bb2 in loose if c2 == cid)]
        print(f"把阈值放宽到 0.20 后仍缺失: {missing if missing else '无（说明是 0.25 边界效应）'}")

    ok = matched >= len(ref) - 1 and worst_iou > 0.90
    print("\n" + ("✅ 前后处理一致（误差在取整/边界范围），可以上板"
                  if ok else "❌ 存在偏差，检查 letterbox 的 pad_w/pad_h 或反变换公式"))


if __name__ == "__main__":
    main()
