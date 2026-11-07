#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 GStreamer pipeline 从 IMX219 取帧，验证出图链路。

背景：
  K1 的摄像头不是标准 V4L2 直读设备，必须走 spacemitsrc 插件。
  配置文件由 cam-test 自动生成（csi3_camera_auto.json，sensor_name=imx219_spm）。

用法：
  python3 grab_frame.py [json路径] [输出jpg] [取帧数]
"""
import sys
import time

import cv2

JSON_PATH = sys.argv[1] if len(sys.argv) > 1 else "/usr/share/camera_json/csi3_camera_auto.json"
OUT_JPG = sys.argv[2] if len(sys.argv) > 2 else "/root/k1-ai/cam_frame.jpg"
N_FRAMES = int(sys.argv[3]) if len(sys.argv) > 3 else 20

# 官方文档给的模板；width/height 必须和 json 里 out_width/out_height 一致
gst = (
    f"spacemitsrc location={JSON_PATH} close-dmabuf=1 ! "
    "video/x-raw,format=NV12,width=1920,height=1080 ! "
    "appsink drop=true max-buffers=2 sync=false"
)

print("[gst]", gst)
cap = cv2.VideoCapture(gst, cv2.CAP_GSTREAMER)
if not cap.isOpened():
    print("[fail] 打不开 pipeline")
    print("[hint] 若报 GStreamer 相关错误，检查 cv2 是否编译了 GStreamer 支持：")
    print("       python3 -c \"import cv2; print(cv2.getBuildInformation())\" | grep -i gstreamer")
    sys.exit(1)

print("[ok] pipeline 已打开")

t0 = time.perf_counter()
got = 0
last = None
for i in range(N_FRAMES):
    ok, frame = cap.read()
    if not ok or frame is None:
        print(f"[warn] 第 {i+1} 帧读取失败")
        continue
    got += 1
    last = frame
dt = time.perf_counter() - t0

cap.release()

if last is None:
    print("[fail] 一帧都没取到")
    sys.exit(2)

if last.ndim == 2:
    bgr = cv2.cvtColor(last, cv2.COLOR_YUV2BGR_NV12)
else:
    bgr = last

cv2.imwrite(OUT_JPG, bgr)
print(f"[result] 取到 {got}/{N_FRAMES} 帧，用时 {dt:.2f}s -> {got/dt:.2f} fps")
print(f"[result] 帧尺寸 = {last.shape}，已保存 {OUT_JPG}")
