#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K1 板端宠物图检出批测 —— 复用 vision_service 的前处理 / 解码，只替换数据来源。

目的：
  1. 给出「主体清晰宠物图」在 int8 + SpaceMIT EP 下的真实检出结果
     （vision_service.py 本身是常驻服务，不方便逐次取数）
  2. 把端到端时延拆成 preprocess / infer / postprocess 三段，避免把
     "端到端 136ms" 和 "纯推理 74.67ms" 混为一谈
  3. 记录同一张图重复运行的置信度是否 bit 级一致（int8 确定性验证）

用法：
    python3 bench_pets.py --model ./yolov8n.q.onnx --runs 20 cat.jpg dog.jpg
    python3 bench_pets.py --model ./yolov8n.q.onnx --topk 5 --runs 1 cat.jpg

输出：
    每个 (图, 轮次) 一行 JSON；末尾一段 SUMMARY，可直接粘进文档。
退出码：0 正常；1 模型加载失败；2 全部图无宠物检出
"""
import argparse
import json
import os
import sys
import time

import numpy as np

import vision_service as vs   # 复用 letterbox / decode_yolov8 / PET_CLASSES / ort


COCO_NAMES = {
    0: "person", 15: "cat", 16: "dog", 29: "frisbee", 32: "sports ball",
    34: "baseball bat", 35: "baseball glove", 38: "tennis racket",
}
PET_NAMES = {15: "cat", 16: "dog"}


def make_session(model_path, force_cpu=False):
    if vs.ort is None:
        print("[fatal] onnxruntime 未安装", file=sys.stderr)
        return None
    so = vs.ort.SessionOptions()
    so.graph_optimization_level = vs.ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    avail = vs.ort.get_available_providers()
    if force_cpu:
        provs = ["CPUExecutionProvider"]
    else:
        provs = [p for p in avail if "SpaceMIT" in p] or avail
    sess = vs.ort.InferenceSession(model_path, so, providers=provs)
    print(f"[model] {model_path}")
    print(f"[model] spacemit_ep_imported = {vs.HAS_SPACEMIT_EP}")
    print(f"[model] available_providers  = {avail}")
    print(f"[model] session_providers    = {sess.get_providers()}")
    return sess


def raw_topk(out, k=5):
    """不经过任何过滤，直接看 80 类里分数最高的 k 个（含同类多 anchor 去重）"""
    pred = out[0]
    if pred.shape[0] < pred.shape[1]:
        pred = pred.T
    cls_scores = pred[:, 4:]
    flat = cls_scores.max(axis=0)          # 每个类的最高分
    order = np.argsort(-flat)[:k]
    return [(int(i), COCO_NAMES.get(int(i), f"cls{int(i)}"), float(flat[i])) for i in order]


def run_once(sess, input_name, img_path, topk):
    """跑一次，返回 (结果 dict, 各段耗时)

    耗时分段刻意把「磁盘读图」单独拆出去：
      io_ms  —— cv2.imread，真实摄像头链路里不存在，混进预处理会让数字虚高
      pre_ms —— letterbox + BGR→RGB + HWC→CHW + 归一化
      infer_ms —— session.run，纯推理
      post_ms —— decode + NMS + (统计用)raw_topk
      frame_ms —— pre + infer + post，这才是「一帧到结果」的可比口径
    """
    t0 = time.perf_counter()
    frame = vs.cv2.imread(img_path)
    if frame is None:
        raise RuntimeError(f"图片读取失败: {img_path}")

    t1 = time.perf_counter()
    h, w = frame.shape[:2]
    canvas, scale, pw, ph = vs.letterbox(frame)
    blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0

    t2 = time.perf_counter()
    outs = sess.run(None, {input_name: blob})
    t3 = time.perf_counter()

    dets = vs.decode_yolov8(outs[0], scale, pw, ph, w, h)
    dets.sort(key=lambda d: -d[1])
    tops = raw_topk(outs[0], topk)
    t4 = time.perf_counter()

    pets = [d for d in dets if d[0] in PET_NAMES]
    res = {
        "img": os.path.basename(img_path),
        "w": w, "h": h,
        "pet": PET_NAMES.get(pets[0][0], "") if pets else "",
        "conf": round(pets[0][1], 4) if pets else 0.0,
        "bbox": [round(v, 1) for v in pets[0][2]] if pets else None,
        "n_pet": len(pets),
        "raw_topk": [[c, n, round(s, 4)] for c, n, s in tops],
    }
    ms = {
        "io_ms": round((t1 - t0) * 1000, 2),
        "pre_ms": round((t2 - t1) * 1000, 2),
        "infer_ms": round((t3 - t2) * 1000, 2),
        "post_ms": round((t4 - t3) * 1000, 2),
        "frame_ms": round((t4 - t1) * 1000, 2),
    }
    return res, ms


def stats(vals):
    a = np.array(vals, dtype=np.float64)
    return {"mean": round(float(a.mean()), 2), "std": round(float(a.std(ddof=1)) if len(a) > 1 else 0.0, 2),
            "min": round(float(a.min()), 2), "max": round(float(a.max()), 2),
            "p50": round(float(np.percentile(a, 50)), 2),
            "p95": round(float(np.percentile(a, 95)), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="./yolov8n.q.onnx")
    ap.add_argument("--runs", type=int, default=20, help="每张图测量轮次")
    ap.add_argument("--warmup", type=int, default=3, help="预热轮次（不计入统计）")
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--cpu", action="store_true",
                    help="强制只用 CPUExecutionProvider，用于同口径基线对照")
    ap.add_argument("images", nargs="+")
    args = ap.parse_args()

    sess = make_session(args.model, force_cpu=args.cpu)
    if sess is None:
        return 1
    input_name = sess.get_inputs()[0].name
    print(f"[model] input = {input_name}, shape = {sess.get_inputs()[0].shape}")

    agg = {}
    any_pet = False

    for img in args.images:
        if not os.path.exists(img):
            print(f"[skip] 不存在: {img}")
            continue

        for _ in range(args.warmup):
            run_once(sess, input_name, img, args.topk)

        recs = []
        for i in range(args.runs):
            res, ms = run_once(sess, input_name, img, args.topk)
            recs.append((res, ms))
            if res["pet"]:
                any_pet = True
            print(json.dumps({"run": i + 1, **res, **ms}, ensure_ascii=False))

        confs = [r[0]["conf"] for r in recs]
        print(f"[determinism] {os.path.basename(img)} 置信度唯一值 = {sorted(set(confs))}  "
              f"({'bit 级一致' if len(set(confs)) == 1 else '存在波动'})")

        agg[img] = {
            "res0": recs[0][0],
            "io": stats([m["io_ms"] for _, m in recs]),
            "pre": stats([m["pre_ms"] for _, m in recs]),
            "infer": stats([m["infer_ms"] for _, m in recs]),
            "post": stats([m["post_ms"] for _, m in recs]),
            "frame": stats([m["frame_ms"] for _, m in recs]),
        }

    print("\n" + "=" * 78)
    print("SUMMARY（可直接引用）")
    print("=" * 78)
    for img, a in agg.items():
        r = a["res0"]
        print(f"\n图: {r['img']}  ({r['w']}x{r['h']})")
        if r["pet"]:
            print(f"  检出: {r['pet']}  conf={r['conf']}  bbox={r['bbox']}  数量={r['n_pet']}")
        else:
            print("  检出: 无宠物（absent）")
        print(f"  原始 top-{args.topk}: " +
              ", ".join(f"{n}={s}" for _, n, s in r["raw_topk"]))
        for k, label in (("io", "磁盘读图"), ("pre", "预处理"), ("infer", "纯推理"),
                         ("post", "后处理"), ("frame", "单帧端到端")):
            s = a[k]
            print(f"  {label:<6}: mean={s['mean']:>7.2f}ms  std={s['std']:>6.2f}  "
                  f"p50={s['p50']:>7.2f}  p95={s['p95']:>7.2f}  min={s['min']:>7.2f}  max={s['max']:>7.2f}")
        print(f"  折合: 纯推理 {1000.0 / a['infer']['mean']:.2f} fps | "
              f"单帧端到端 {1000.0 / a['frame']['mean']:.2f} fps（不含磁盘 I/O）")

    return 0 if any_pet else 2


if __name__ == "__main__":
    sys.exit(main())
