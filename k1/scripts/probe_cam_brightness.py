"""诊断用：在不启动服务的前提下，看摄像头每个后端实际读到什么。

用途：区分三种「画面很暗」的原因 ——
  1. 环境真的没光（亮度会随曝光时间缓慢上升，且各后端一致）
  2. 摄像头被别的程序独占（打开成功但恒定黑帧，亮度方差≈0）
  3. 隐私盖/物理遮挡（同上，但所有机器上都会出现）

只读不写，跑完选一个后端把帧存到 output/live/probe_<tag>.jpg。
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

BACKENDS = [
    ("MSMF", cv2.CAP_MSMF),
    ("DSHOW", cv2.CAP_DSHOW),
    ("ANY", cv2.CAP_ANY),
]

OUT = Path(__file__).resolve().parent.parent / "output" / "live"
OUT.mkdir(parents=True, exist_ok=True)


def probe(tag, api):
    cap = cv2.VideoCapture(0, api)
    if not cap.isOpened():
        print(f"[{tag}] 打不开")
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    # 连读 30 帧，记录亮度序列：自动曝光在收敛时亮度会明显爬升，
    # 被独占时则是一串几乎相同的极小值。
    vals = []
    last = None
    for _ in range(30):
        ok, f = cap.read()
        if ok and f is not None:
            vals.append(float(f.mean()))
            last = f
        time.sleep(0.02)

    if last is None:
        print(f"[{tag}] 读不到帧")
        cap.release()
        return None

    a = np.asarray(vals)
    print(f"[{tag}] 亮度 首={a[0]:.1f} 末={a[-1]:.1f} 均值={a.mean():.1f} "
          f"最大={a.max():.1f} 标准差={a.std():.2f} n={len(a)}")
    print(f"       曝光={cap.get(cv2.CAP_PROP_EXPOSURE):.2f} "
          f"增益={cap.get(cv2.CAP_PROP_GAIN):.0f} "
          f"自动曝光={cap.get(cv2.CAP_PROP_AUTO_EXPOSURE):.2f} "
          f"fps={cap.get(cv2.CAP_PROP_FPS):.1f}")
    p = OUT / f"probe_{tag}.jpg"
    cv2.imwrite(str(p), last)
    cap.release()
    return a.mean()


if __name__ == "__main__":
    results = {}
    for tag, api in BACKENDS:
        m = probe(tag, api)
        if m is not None:
            results[tag] = m
        time.sleep(0.5)
    print("-" * 50)
    if not results:
        print("所有后端都读不到帧 —— 摄像头被独占或不可用")
        sys.exit(1)
    top = max(results.values())
    if top < 8:
        print(f"全部后端亮度 < 8（最高 {top:.1f}）：几乎可以确定没有进光 "
              f"—— 查隐私盖、镜头遮挡、房间灯")
    elif top < 40:
        print(f"最高亮度 {top:.1f}：很暗，能满足检测但会压低置信度")
    else:
        print(f"最高亮度 {top:.1f}：光照可用")
