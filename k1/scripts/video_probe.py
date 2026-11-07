#!/usr/bin/env python3
"""把视频文件逐帧直推板端，绕过「摄像头拍屏幕」这一环。

为什么需要这个：
  摄像头拍屏幕会让宠物在画面里缩得很小。实测板端模型对目标尺寸极敏感——
  高 90px 以下完全检不出（conf 0.000），120px 才 0.403，300px 才 0.850。
  笔记本摄像头拍整间屋子 + 手机屏幕只占一小块，等效尺寸远低于门槛，
  所以「怎么都认不出来」根本不是模型或动作逻辑的问题。

  直接把视频帧送进板端，帧是全分辨率的，宠物占幅面大，检测和行为才有意义。

用法：
    .venv-train/Scripts/python.exe scripts/video_probe.py --video "C:\\...\\dog.mp4"
    .venv-train/Scripts/python.exe scripts/video_probe.py --video a.mp4 --hz 5 --crop 0,0,432,700

输出每帧的 类别/置信度/框高/行为，最后给检测率与行为分布。
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from collections import Counter

import cv2

LOCAL = "http://127.0.0.1:11436"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ingest(jpeg, timeout=10):
    req = urllib.request.Request(
        f"{LOCAL}/v1/vision/ingest", data=jpeg,
        headers={"Content-Type": "application/octet-stream"}, method="POST")
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def bbox_h(snap):
    # all[] 里每项的类别字段是 "pet"，"label" 是行为标签——这里踩过一次，
    # 用错字段会让框高恒为 0，看起来像「尺寸不足」，实际只是解析错了。
    best = 0.0
    for d in snap.get("all") or []:
        if d.get("pet") in ("cat", "dog"):
            b = d.get("bbox") or []
            if len(b) == 4:
                best = max(best, b[3] - b[1])
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--hz", type=float, default=5.0, help="采样频率（与板端行为频率一致）")
    ap.add_argument("--crop", default="", help="裁剪 x1,y1,x2,y2，去掉手机UI/黑边")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少帧（0=全片）")
    a = ap.parse_args()

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        print(f"打不开视频: {a.video}")
        return 1
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, int(round(src_fps / a.hz)))
    print(f"视频 {os.path.basename(a.video)}  {total} 帧 @ {src_fps:.0f}fps")
    print(f"按 {a.hz}Hz 采样（每 {step} 帧取 1）\n")

    crop = None
    if a.crop:
        x1, y1, x2, y2 = (int(v) for v in a.crop.split(","))
        crop = (x1, y1, x2, y2)

    labels = Counter()
    heights = []
    hits = 0
    n = 0
    idx = 0
    t0 = time.time()
    interval = 1.0 / a.hz

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step:
            idx += 1
            continue
        idx += 1
        if a.limit and n >= a.limit:
            break

        if crop:
            x1, y1, x2, y2 = crop
            frame = frame[y1:y2, x1:x2]

        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            continue
        try:
            snap = ingest(buf.tobytes())
        except Exception as e:
            print(f"  送帧失败: {e}")
            break

        n += 1
        pet = snap.get("pet") or "-"
        lab = snap.get("label") or "absent"
        conf = float(snap.get("conf") or 0)
        h = bbox_h(snap)
        labels[lab] += 1
        if h > 0:
            hits += 1
            heights.append(h)
        flag = "" if h == 0 else ("  <-- 太小" if h < 120 else "")
        print(f"[{n:>3}] {n/a.hz:>5.1f}s  pet={pet:<5} conf={conf:.2f} "
              f"框高={h:>5.0f}px  行为={lab:<10}{flag}")

        # 按真实节奏发送，让 tracker 用真实时间差算速度
        el = time.time() - t0
        behind = n * interval - el
        if behind > 0:
            time.sleep(behind)

    cap.release()
    print("\n" + "=" * 60)
    print(f"采样 {n} 帧，检出宠物 {hits} 帧（{hits/max(1,n)*100:.0f}%）")
    if heights:
        hs = sorted(heights)
        print(f"检出时框高: 最小 {hs[0]:.0f} / 中位 {hs[len(hs)//2]:.0f} / 最大 {hs[-1]:.0f} px")
    print("行为分布:", ", ".join(f"{k}={v}" for k, v in labels.most_common()))
    print("=" * 60)
    print("判读：检出率低且框高集中在 120px 以下 → 是尺寸问题，不是模型问题")
    print("      检出率高但行为恒为 sitting/standing → 才是行为层的锅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
