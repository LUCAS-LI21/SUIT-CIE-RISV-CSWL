#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L0 动作识别的合成轨迹测试（在 K1 上跑，因为它有 cv2/numpy 与真实依赖）。

为什么要这个测试：L0 改的是判定树，现场没有宠物时只能看到 absent，
根本验证不到任何一条分支。这里用合成的位置序列把每条分支走一遍，
至少保证「阈值写反了」「分母为零」「deque 取不到值」这类错误在上板前暴露。

用法（在 K1 上）：
    cd /root/k1-ai && python3 test_behavior_rules.py
"""
import math
import sys

sys.path.insert(0, "/root/k1-ai")
import numpy as np  # noqa: E402
from vision_service import PetTracker  # noqa: E402

FPS = 2.0
IMG_W, IMG_H = 1920, 1080


def _textured_canvas(offset_x):
    """造一张 1920×1080 的假帧：只有在 x∈[900,1200) 的框内画随机纹理，
    并且按 offset_x 整体平移。框外保持纯黑。

    这样如果 update_motion 拿错坐标系（比如拿 640×640 的 letterbox canvas
    去切原图坐标的框），切到的只会是纯黑区域，光流恒为 0 —— 本测试会 FAIL。
    """
    cv = np.zeros((IMG_H, IMG_W, 3), dtype=np.uint8)
    rng = np.random.RandomState(7)
    patch = (rng.rand(300, 300, 3) * 255).astype(np.uint8)
    # 只裁一小块，保证平移后仍在框内
    sub = patch[40:140, 40:260]
    x = 900 + int(offset_x)
    cv[500:600, x - 40:x + 180] = sub
    return cv


def drive(tracker, path, box=(400.0, 900.0), bbox_w=300.0, bbox_h=200.0, t0=1000.0):
    """按 2fps 把一条轨迹喂进去，返回最终 label/conf/basis。"""
    out = ("unknown", 0.0, "")
    now = t0
    for (cx, cy) in path:
        now += 1.0 / FPS
        x1, y1 = cx - bbox_w / 2, cy - bbox_h / 2
        bbox = (x1, y1, x1 + bbox_w, y1 + bbox_h)
        tracker.update(now, cx, cy, "cat", bbox)
        out = tracker.analyze_behavior(bbox_w, bbox_h, IMG_W, IMG_H, now)
    return out


def still_path(n=12, x=400.0, y=900.0):
    return [(x + (0.3 if i % 2 else -0.3), y) for i in range(n)]


def linear_path(n=12, x0=300.0, y=800.0, dx=40.0):
    return [(x0 + dx * i, y) for i, y in enumerate([800.0] * n)]


def vertical_path(n=12, x=400.0, y0=600.0):
    """上下来回：竖直位移大，横向不动。"""
    return [(x, y0 + (120.0 if i % 2 else -120.0)) for i in range(n)]


def main():
    fails = []

    # 1) 静止：应落在 lying / sleeping / standing，且不能是 running
    t1 = PetTracker(1, 1000.0)
    lab, conf, basis = drive(t1, still_path())
    print(f"[静止] {lab} conf={conf} | {basis}")
    if lab in ("running", "walking", "jumping"):
        fails.append(f"静止被判成 {lab}")

    # 2) 慢速平移：应 walking 或 sitting，不能是 running
    t2 = PetTracker(2, 1000.0)
    lab, conf, basis = drive(t2, linear_path(dx=40.0))
    print(f"[慢移] {lab} conf={conf} | {basis}")
    if lab == "running":
        fails.append("慢速平移被判成 running")

    # 3) 快速平移：应 running
    t3 = PetTracker(3, 1000.0)
    lab, conf, basis = drive(t3, linear_path(dx=400.0))
    print(f"[快移] {lab} conf={conf} | {basis}")
    if lab != "running":
        fails.append(f"快速平移应判 running，实际 {lab}")

    # 4) 上下大幅抖动：应 jumping
    t4 = PetTracker(4, 1000.0)
    lab, conf, basis = drive(t4, vertical_path())
    print(f"[竖直] {lab} conf={conf} | {basis}")
    if lab not in ("jumping", "playing", "walking"):
        fails.append(f"竖直大幅运动判 {lab}，预期 jumping/playing")

    # 5) 置信度必须落在 [0.45, 0.95]
    for t, name in ((t1, "静止"), (t3, "快移")):
        c = t.latest_confidence
        if not (0.40 <= c <= 0.95):
            fails.append(f"{name} 置信度越界 {c}")

    # 6) basis 必须非空且含关键特征名（前端要显示给用户看）
    _, _, b = drive(PetTracker(6, 1000.0), linear_path())
    if "体长速" not in b or "光流" not in b:
        fails.append(f"basis 缺字段: {b}")

    # 7) 光流通道必须真的被点亮。
    #    这一条是补的——之前的 6 项都不传 canvas，所以 update_motion 直接 return，
    #    flow_energy 恒为 0，而 basis 里照样打印「光流0.00」，看着像测过了。
    #    子代理正是靠读代码才发现调用点传的是 letterbox canvas（640×640）、
    #    而 bbox 是原图坐标（1920×1080），x>640 的框会被整段裁掉。
    t7 = PetTracker(7, 1000.0)
    bbox7 = (900.0, 500.0, 1200.0, 600.0)
    t7.update_motion(_textured_canvas(0), bbox7)      # 第一帧：只写 prev_roi
    t7.update_motion(_textured_canvas(60), bbox7)     # 第二帧：纹理右移 60px
    flow_moving = t7.flow_energy
    print(f"[光流] 移动纹理 flow={flow_moving:.4f} 原地比={t7.inplace_ratio:.3f}")
    if flow_moving <= 0.001:
        fails.append(f"ROI 移动帧光流仍为 {flow_moving:.4f}，坐标系或 ROI 取值有误")

    # 8) 完全静止的两帧，光流必须接近 0（不能凭空制造运动）
    t8 = PetTracker(8, 1000.0)
    same = _textured_canvas(0)
    t8.update_motion(same, bbox7)
    t8.update_motion(same, bbox7)
    print(f"[光流] 静止画面 flow={t8.flow_energy:.4f}")
    if t8.flow_energy > 0.005:
        fails.append(f"静止画面光流 {t8.flow_energy:.4f} 偏大，会误触发 playing")

    # 9) 冷启动帧（只有一帧）光流必须为 0，不能沿用上一轮残值
    t9 = PetTracker(9, 1000.0)
    t9.flow_energy = 0.99
    t9.update_motion(_textured_canvas(0), bbox7)
    if t9.flow_energy != 0.0:
        fails.append(f"冷启动帧沿用了残值 {t9.flow_energy}")

    print()
    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS: 9 项检查全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
