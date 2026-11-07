#!/usr/bin/env python3
"""用视频文件离线复验行为判定（绕开摄像头光照/角度/抖动）。

直接复用 k1-vision/vision_service.py 的 PetTracker（判定逻辑一字不改），
所以结果与线上一致，但可重复、可回放、可逐帧对照。

用法:
    python scripts/verify_behavior_video.py <视频路径> [--stride 1] [--limit 900]
    python scripts/verify_behavior_video.py video.mp4 --dump logs/verify.csv

输出:
    - 每帧 raw 判定 + basis（动作变化时打印）
    - 动作分布、各动作 basis 中位数、running/jumping 混淆明细
"""
import argparse
import csv
import math
import os
import sys
from collections import Counter, defaultdict

import cv2
import numpy as np
import onnxruntime as ort

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# VS_DIR 可指向另一份 vision_service.py（例如 git show 出来的旧版），
# 用来在完全相同的视频上做「改前 / 改后」对比，避免靠记忆里的数字对比。
VS_DIR = os.environ.get("VS_DIR") or os.path.join(ROOT, "k1-vision")
sys.path.insert(0, VS_DIR)

from vision_service import (  # noqa: E402
    MultiPetTracker, decode_yolov8, letterbox,
)

NUM_KEYS = ("体长速", "光流", "原地", "周期", "竖移", "峰峰", "横峰", "净移", "稳定", "径向")
STALE_KEEP = float(os.environ.get("STALE_KEEP", "2.0"))  # 与 vision_service.STALE_KEEP_SEC 对齐


def parse_basis(s):
    """'体长速0.42/s 光流0.31 ... 径向-0.55/s' -> dict。按已知键名切，避免正则误吞。"""
    import re
    out = {}
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,4})\s*([+-]?\d+\.?\d*)", s or ""):
        k, v = m.group(1), m.group(2)
        if k in NUM_KEYS:
            try:
                out[k] = float(v)
            except ValueError:
                pass
    return out


def med(xs):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--model", default=os.path.join(ROOT, "k1-vision", "yolov8n.onnx"))
    ap.add_argument("--stride", type=int, default=1, help="每 N 帧取 1 帧（默认全取）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少帧，0=不限")
    ap.add_argument("--dump", default="", help="逐帧 CSV 输出路径")
    ap.add_argument("--conf", type=float, default=0.25)
    args = ap.parse_args()

    if not os.path.exists(args.video):
        print(f"视频不存在: {args.video}")
        return 2
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print(f"打不开视频: {args.video}")
        return 2
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"[verify] {os.path.basename(args.video)}  {w}x{h} @ {fps:.1f}fps  "
          f"{total} 帧  stride={args.stride}")

    sess = ort.InferenceSession(args.model, providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0].name
    tracker = MultiPetTracker()

    rows = []
    last_key = None
    vframe = 0
    done = 0
    t_step = (1.0 / fps) * args.stride          # 相当于线上的真实帧间隔

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        vframe += 1
        if args.stride > 1 and (vframe - 1) % args.stride:
            continue
        done += 1
        if args.limit and done > args.limit:
            break

        now = done * t_step                     # 用视频时间轴当 monotonic
        canvas, scale, pw, ph = letterbox(frame)
        blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        outs = sess.run(None, {inp: blob})
        dets = decode_yolov8(outs[0], scale, pw, ph, w, h)
        dets = [d for d in dets if d[1] >= args.conf]

        res = tracker.update_or_create(now, dets, w, h, frame)
        tracker.cleanup(now)
        # 漏检时沿用上一次判定（与线上 payload 同逻辑），用来量化「3D 会不会回待机」
        stale = tracker.stale_result(now, STALE_KEEP) if not res else None

        if not res:
            act, raw, basis, conf, pid = "absent", "", "", 0.0, 0
            net = body_h = float("nan")
        else:
            r = res[0]
            act = r["action"]
            raw = r.get("action_raw", "")
            basis = r["basis"]
            conf = r["action_conf"]
            pid = r["pet_id"]
            # 诊断量：净位移（跳=往复≈0，跑=单向≈峰峰）与目标体高
            x1, y1, x2, y2 = r["bbox"]
            body_h = y2 - y1
            t = tracker.trackers.get(pid)
            net = t._net_disp_body(max(20.0, body_h)) if t else float("nan")

        b = parse_basis(basis)
        key = (act, raw)
        if key != last_key:
            kv = " ".join(f"{k}={v:g}" for k, v in b.items())
            print(f"{now:7.2f}s  raw={raw or '-':9s} pub={act:9s} "
                  f"conf={conf:.2f} id={pid}  {kv}")
            last_key = key

        rows.append({"t": round(now, 2), "frame": vframe, "action": act,
                     "raw": raw, "conf": conf, "net": round(net, 3) if net == net else "",
                     "body_h": round(body_h, 1) if body_h == body_h else "",
                     "stale_act": stale["action"] if stale else "",
                     **{f"b_{k}": v for k, v in b.items()}})

    cap.release()
    if not rows:
        print("[verify] 没读到帧")
        return 2

    print("\n" + "=" * 66)
    print(f"处理 {len(rows)} 帧 / {rows[-1]['t']:.1f}s")
    for tag, field in (("发布 action", "action"), ("稳定化前 raw", "raw")):
        cnt = Counter(r[field] for r in rows)
        print(f"\n{tag}:")
        for a, c in cnt.most_common():
            print(f"  {a or '(空)':<12}{c:>6}  {c/len(rows)*100:>5.1f}%")

    # 漏检沿用（stale）后的实际观感：这一行才代表前端会不会回待机
    pub = [(r["stale_act"] or r["action"]) for r in rows]
    cnt2 = Counter(pub)
    n_stale = sum(1 for r in rows if r["stale_act"])
    print(f"\n漏检沿用后（keep={STALE_KEEP:.1f}s，{n_stale} 帧沿用 "
          f"{n_stale/len(rows)*100:.1f}%）:")
    for a, c in cnt2.most_common():
        print(f"  {a or '(空)':<12}{c:>6}  {c/len(rows)*100:>5.1f}%")

    keys = sorted({k[2:] for r in rows for k in r if k.startswith("b_")})
    if keys:
        print("\n各动作 basis 中位数（raw 分组）:")
        # net = 1s 窗口净位移/体高（跳≈0，跑≈峰峰）；body_h = 目标框高像素（看丢检与目标大小关系）
        headers = ["净位移", "框高"] + keys
        print("动作".ljust(11) + "".join(h.rjust(9) for h in headers))
        by = defaultdict(list)
        for r in rows:
            by[r["raw"] or "(空)"].append(r)

        def pick(r, h):
            if h == "净位移":
                return r.get("net")
            if h == "框高":
                return r.get("body_h")
            return r.get(f"b_{h}")

        for a, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
            line = a[:10].ljust(11)
            for h in headers:
                vals = [pick(r, h) for r in rs if pick(r, h) not in (None, "")]
                line += (f"{med(vals):.2f}".rjust(9) if vals else "-".rjust(9))
            print(line)

    if args.dump:
        os.makedirs(os.path.dirname(os.path.abspath(args.dump)), exist_ok=True)
        cols = ["t", "frame", "action", "raw", "conf", "net", "body_h", "stale_act"] + \
               [f"b_{k}" for k in keys]
        with open(args.dump, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            wr.writeheader()
            wr.writerows(rows)
        print(f"\n[verify] CSV -> {args.dump}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
