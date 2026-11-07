#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在应用层（OpenCV，与 vision_service.py 完全相同的取流方式）逐一试开
每个候选视频节点，判定哪个能真正出帧。

这是对"摄像头到底能不能用"的最终判定：v4l2-ctl 是内核层证据，
cv2.VideoCapture 是应用层证据，两者一致才敢下结论。
"""
import sys
import cv2
import time

CANDIDATES = [0, 1, 2, 3, 4, 50, 51]

print(f"[env] cv2 = {cv2.__version__}")
print(f"[env] 候选节点 = {CANDIDATES}")
print("=" * 68)

ok_list = []
for idx in CANDIDATES:
    dev = f"/dev/video{idx}"
    t0 = time.perf_counter()
    cap = cv2.VideoCapture(idx)
    opened = cap.isOpened()

    frame = None
    shape = None
    if opened:
        # 读 5 次，前几次可能是空帧（驱动初始化）
        for _ in range(5):
            ret, f = cap.read()
            if ret and f is not None:
                frame = f
                shape = f.shape
    dt = round((time.perf_counter() - t0) * 1000, 1)
    cap.release()

    if frame is not None:
        mean = float(frame.mean())
        # 纯黑/纯彩条也能读出帧，但用均值可以判断是不是"有内容的图"
        verdict = f"OK  shape={shape} mean={mean:.1f}  {dt}ms"
        ok_list.append((idx, shape, mean))
    elif opened:
        verdict = f"OPENED 但读不到帧（流未启动）  {dt}ms"
    else:
        verdict = f"打不开  {dt}ms"

    print(f"{dev:<14} {verdict}")

print("=" * 68)
if ok_list:
    print(f"[结论] 可出帧的节点: {[f'/dev/video{i}' for i, _, _ in ok_list]}")
else:
    print("[结论] 没有任何节点能出帧 —— 摄像头输入不可用")
