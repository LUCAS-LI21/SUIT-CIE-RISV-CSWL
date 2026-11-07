"""本机 YOLO 端到端基准：完整走 LocalEngine（letterbox + 推理 + NMS + 跟踪 + JPEG 编码），
喂真实图片，按 30 fps 推进时间轴，测单帧端到端耗时。

用途：与 K1 板端的 infer_ms(158ms)/端到端 2.3~4.3 fps 做同口径对照。
不碰摄像头、不碰 K1，可随时跑。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "k1-vision"))

import pc_cam_vision as P  # noqa: E402

MODEL = ROOT / "k1-vision" / "yolov8n.onnx"
IMG = ROOT / "dog_test.jpg"


def main() -> int:
    frame = cv2.imread(str(IMG))
    if frame is None:
        print(f"读不到图片 {IMG}")
        return 1
    # 统一到摄像头实际出图尺寸，避免分辨率差异影响结论
    frame = cv2.resize(frame, (640, 480))

    eng = P.LocalEngine(MODEL)
    print(f"provider = {eng.prov}")
    print(f"输入 = {frame.shape[1]}x{frame.shape[0]}  模型 = {MODEL.name}")

    # 预热：tracker 首帧建轨迹，单独剔除
    ret, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    eng.infer(frame, time.monotonic(), enc.tobytes())

    n = 30
    ts = time.monotonic()
    costs: list[float] = []
    enc_costs: list[float] = []
    for _ in range(n):
        ts += 1.0 / 30.0                      # 模拟 30 fps 的采集时间轴
        e0 = time.perf_counter()
        ret, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        enc_costs.append((time.perf_counter() - e0) * 1000)

        t0 = time.perf_counter()
        out = eng.infer(frame, ts, enc.tobytes())
        costs.append((time.perf_counter() - t0) * 1000)

    costs.sort()
    enc_costs.sort()
    med = costs[len(costs) // 2]
    print(f"\n端到端单帧（letterbox+推理+NMS+跟踪）：")
    print(f"  中位 {med:.1f} ms   最快 {costs[0]:.1f}   最慢 {costs[-1]:.1f}")
    print(f"  JPEG 编码中位 {enc_costs[len(enc_costs)//2]:.1f} ms")
    print(f"  折算上限 {1000/med:.1f} fps（不含摄像头采集与网络）")
    print(f"\n最后一帧结论：pet={out.get('pet')} label={out.get('label')} "
          f"conf={out.get('conf')} count={out.get('count')} "
          f"human={len(out.get('human', {}).get('boxes', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
