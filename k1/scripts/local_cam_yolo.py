#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地摄像头 YOLO 验证工具（不依赖 K1 板子）

用途：在没有 K1 / 不用板载 CSI 的情况下，拿电脑摄像头验证
      「宠物检测 + 动作判定」这条链路能不能正常工作。

三条设计铁律（照做，否则测出来的结论不算数）：

  1) **复用线上代码**。letterbox / decode_yolov8 / MultiPetTracker 全部从
     k1-vision/vision_service.py 导入，不另写一份判定。否则「本地能识别」
     证明不了「板上能识别」——两套逻辑各自跑通毫无意义。

  2) **分析帧率默认 2 fps，与板端一致**。行为阈值是按这个频率标定的：
       · HISTORY_FRAMES=10 在 2 fps 下是 5 秒窗口（30 fps 只剩 0.33 秒）
       · 光流阈值 0.12 的基线是「2 fps 下 300px ROI 位移 60px → 0.071」
     帧率一改，vb / 周期 / 光流 / 静止计时全部变形，标签会整体漂移。
     要提帧率，必须同时重标阈值（--analyze-fps 只用于观察趋势）。

  3) **光流必须传原始帧**。vision_service.py:901 有注释：bbox 是反 letterbox
     后的原图坐标，拿它去切 640×640 画布会把 x>640 的框整段 clamp 掉，
     prev_roi 永远写不进去、光流恒为 0，而 basis 里照样印「光流0.00」，
     看着像测过了。这里严格传 frame。

用法：
    # 默认：电脑摄像头 0，1280×720，2 fps 分析，带实时窗口
    python scripts/local_cam_yolo.py

    # 无人值守跑 60 秒，不出窗口，只出统计与日志
    python scripts/local_cam_yolo.py --no-display --duration 60

    # 用一段猫狗视频代替摄像头（可反复回放，验证动作最方便）
    python scripts/local_cam_yolo.py --video D:/pets/cat_walk.mp4

    # 换 int8 量化模型（与板上一致的量级）
    python scripts/local_cam_yolo.py --model k1-vision/yolov8n-int8-static.onnx

按键（有窗口时）：q 或 ESC 退出，s 立即存一张带框图
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

# ── 定位仓库根与 k1-vision，导入线上实现 ────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
VISION_DIR = ROOT / "k1-vision"
if not (VISION_DIR / "vision_service.py").exists():
    sys.exit(f"[FATAL] 找不到 {VISION_DIR / 'vision_service.py'}")
sys.path.insert(0, str(VISION_DIR))

try:
    import cv2
except ImportError:
    sys.exit("[FATAL] 缺 opencv：.venv-train/Scripts/python.exe -m pip install opencv-python")

try:
    import onnxruntime as ort
except ImportError:
    sys.exit("[FATAL] 缺 onnxruntime")

import vision_service as V  # noqa: E402  线上真身，含 letterbox/decode/tracker


# ── 行为标签的中文对照（仅用于终端汇报，不进 HUD，HUD 保持接口原生英文）──
LABEL_CN = {
    "sleeping": "睡觉",
    "lying": "趴卧",
    "sitting": "坐",
    "standing": "站立",
    "walking": "走动",
    "running": "奔跑",
    "playing": "玩耍",
    "jumping": "跳跃",
    "eating": "进食",
    "coquettish": "撒娇",
    "long_still": "长时间静止",
    "unknown": "未知",
}

PET_CN = {"cat": "猫", "dog": "狗"}


# ══════════════════════════ 采集端 ══════════════════════════

def open_camera(index: int, width: int, height: int, backend: str):
    """按指定后端打开摄像头。Windows 上 DSHOW 比 MSMF 稳，打不开再降级。"""
    order = [backend] if backend != "auto" else ["dshow", "msmf", "any"]
    caps = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "any": cv2.CAP_ANY}
    problems = []
    for name in order:
        cap = cv2.VideoCapture(index, caps[name])
        if not cap.isOpened():
            cap.release()
            problems.append(f"{name}:打不开")
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # 只留最新帧，避免延迟堆积
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            problems.append(f"{name}:打不开流")
            continue
        real_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        real_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[cam] 已打开 index={index} backend={name} 实际分辨率={real_w}x{real_h}")
        if name != "dshow":
            print(f"[cam] 注意：用的是 {name} 后端，若卡顿可改 --backend dshow")
        return cap
    sys.exit(f"[FATAL] 摄像头打不开（index={index}）：" + "；".join(problems))


# ══════════════════════════ 单帧推理 ══════════════════════════

def infer_frame(session, input_name, frame):
    """完全复刻 vision_service.py:879-893 的推理段，只把 _grab() 换成外部帧。

    返回 (detections, infer_ms)。detections = [(cls_id, conf, (x1,y1,x2,y2)), ...]
    坐标是**原图**坐标（decode_yolov8 已做反 letterbox）。
    """
    t0 = time.perf_counter()
    canvas, scale, pad_w, pad_h = V.letterbox(frame)
    blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    outs = session.run(None, {input_name: blob})
    h, w = frame.shape[:2]
    dets = V.decode_yolov8(outs[0], scale, pad_w, pad_h, w, h)
    return dets, (time.perf_counter() - t0) * 1000.0


# ══════════════════════════ 画面叠加 ══════════════════════════

def draw_overlay(frame, results, human_snap, head_lines):
    """把检测结果画到帧上。HUD 用英文键名，与 /latest 接口字段一致，避免中文乱码。"""
    out = frame
    for r in results:
        x1, y1, x2, y2 = [int(round(v)) for v in r["bbox"]]
        is_dog = r["pet"] == "dog"
        color = (60, 160, 255) if is_dog else (80, 220, 120)   # 狗偏橙，猫偏绿
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)

        tag = f'{r["pet"]}#{r["pet_id"]} {r["label"]} {r["action_conf"]:.2f}'
        (tw, th), base = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        ty = max(y1 - 6, th + 4)
        cv2.rectangle(out, (x1, ty - th - 4), (x1 + tw + 6, ty + base + 2), color, -1)
        cv2.putText(out, tag, (x1 + 3, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (20, 20, 20), 2, cv2.LINE_AA)

        # basis 太长，单独画在框下方小字，现场调阈值时最有用
        cv2.putText(out, r["basis"], (x1, min(y2 + 16, out.shape[0] - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)

    if human_snap and human_snap.get("present"):
        bx = human_snap.get("bbox") or []
        if len(bx) == 4:
            hx1, hy1, hx2, hy2 = [int(round(v)) for v in bx]
            cv2.rectangle(out, (hx1, hy1), (hx2, hy2), (200, 200, 200), 1, cv2.LINE_AA)
            cv2.putText(out, f'person x{human_snap["count"]}', (hx1, max(hy1 - 6, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)

    # 左上角状态块
    pad = 6
    line_h = 20
    box_w = 360
    box_h = line_h * len(head_lines) + pad * 2
    overlay = out.copy()
    cv2.rectangle(overlay, (6, 6), (6 + box_w, 6 + box_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, out, 0.45, 0, out)
    for i, (text, color) in enumerate(head_lines):
        cv2.putText(out, text, (6 + pad, 6 + pad + line_h * (i + 1) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 1, cv2.LINE_AA)
    return out


# ══════════════════════════ 主流程 ══════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="本地摄像头 YOLO 检测+动作判定验证（复用 k1-vision 线上逻辑）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=0, help="摄像头索引，默认 0")
    ap.add_argument("--video", default=None, help="用视频文件代替摄像头（可循环回放）")
    ap.add_argument("--loop", action="store_true", help="--video 时循环播放")
    ap.add_argument("--width", type=int, default=1280, help="请求采集宽度，默认 1280")
    ap.add_argument("--height", type=int, default=720, help="请求采集高度，默认 720")
    ap.add_argument("--backend", default="auto", choices=["auto", "dshow", "msmf", "any"],
                    help="采集后端，默认 auto（依次试 dshow/msmf）")
    ap.add_argument("--model", default=None,
                    help="ONNX 模型路径，默认 k1-vision/yolov8n.onnx（fp32，与板上同源）")
    ap.add_argument("--analyze-fps", type=float, default=2.0,
                    help="分析帧率，默认 2.0（与板端一致，行为阈值按此标定，勿随意调高）")
    ap.add_argument("--duration", type=float, default=0,
                    help="运行秒数，0 表示一直跑到按键退出")
    ap.add_argument("--no-display", action="store_true", help="不开窗口（无人值守/远程）")
    ap.add_argument("--save-dir", default=None,
                    help="存图与日志目录，默认 shots/local-cam-<时间戳>/")
    ap.add_argument("--save-every", type=float, default=0,
                    help="每隔 N 秒自动存一张带框图，0 表示不自动存")
    ap.add_argument("--min-conf", type=float, default=V.CONF_THRES,
                    help=f"检测置信度门槛，默认 {V.CONF_THRES}（与线上一致）")
    args = ap.parse_args()

    # ── 模型 ────────────────────────────────────────────────────────
    model_path = Path(args.model) if args.model else (VISION_DIR / "yolov8n.onnx")
    if not model_path.is_absolute():
        cand = (ROOT / model_path)
        model_path = cand if cand.exists() else (VISION_DIR / model_path.name)
    if not model_path.exists():
        sys.exit(f"[FATAL] 模型不存在：{model_path}")
    size_mb = model_path.stat().st_size / 1024 / 1024

    providers = ort.get_available_providers()
    want = "CUDAExecutionProvider" if "CUDAExecutionProvider" in providers else "CPUExecutionProvider"
    session = ort.InferenceSession(str(model_path), providers=[want])
    input_name = session.get_inputs()[0].name

    print("=" * 66)
    print("本地摄像头 YOLO 验证  |  复用 k1-vision 线上判定逻辑")
    print("=" * 66)
    print(f"模型      : {model_path.name}  ({size_mb:.2f} MB)  provider={want}")
    print(f"分析帧率  : {args.analyze_fps} fps  →  行为窗口 "
          f"{V.HISTORY_FRAMES / args.analyze_fps:.1f} 秒（板端同值）")
    print(f"阈值      : conf>={args.min_conf}  NMS IoU>={V.NMS_IOU}  输入 {V.IMG_SIZE}x{V.IMG_SIZE}")
    print(f"宠物类别  : {V.PET_CLASSES}   跟踪 TTL={V.TRACK_TTL}s  稳定确认={V.STABLE_NEED + 1} 次")
    if abs(args.analyze_fps - 2.0) > 1e-6:
        print(f"[警告] 分析帧率非 2 fps，行为标签会漂移，结论不能直接搬到板端")

    # ── 采集源 ──────────────────────────────────────────────────────
    cap = None
    video_fps = None
    if args.video:
        vp = Path(args.video)
        if not vp.exists():
            sys.exit(f"[FATAL] 视频不存在：{vp}")
        cap = cv2.VideoCapture(str(vp))
        if not cap.isOpened():
            sys.exit(f"[FATAL] 视频打不开：{vp}")
        video_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        print(f"视频源    : {vp.name}  {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
              f"{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}  {video_fps:.1f} fps  {n_frames} 帧")
    else:
        cap = open_camera(args.camera, args.width, args.height, args.backend)

    # ── 输出目录 ────────────────────────────────────────────────────
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    save_dir = Path(args.save_dir) if args.save_dir else (ROOT / "shots" / f"local-cam-{stamp}")
    save_dir.mkdir(parents=True, exist_ok=True)
    log_path = save_dir / "events.csv"

    # ── 线上判定器 ──────────────────────────────────────────────────
    tracker = V.MultiPetTracker()
    human = V.HumanWatcher()
    # 允许命令行放宽检出阈值：V.CONF_THRES 是模块级常量，decode_yolov8 直接读它
    if abs(args.min_conf - V.CONF_THRES) > 1e-9:
        print(f"[info] 已把模块常量 CONF_THRES 从 {V.CONF_THRES} 改为 {args.min_conf}")
        V.CONF_THRES = args.min_conf

    analyze_period = 1.0 / max(args.analyze_fps, 0.05)
    last_analyze = 0.0
    last_save = 0.0
    t_start = time.monotonic()

    results = []
    human_snap = {}
    infer_ms = 0.0
    analyze_count = 0
    frames_read = 0
    frames_with_pet = 0
    label_counter: Counter = Counter()
    conf_by_label: dict[str, float] = {}
    read_fail = 0
    playhead = 0.0        # 视频源的播放游标（按视频时间走，保证分析间隔正确）

    csv_f = open(log_path, "w", newline="", encoding="utf-8-sig")
    writer = csv.writer(csv_f)
    writer.writerow(["wall_time", "src_time_s", "analyze_idx", "infer_ms",
                     "pet", "pet_id", "label", "action_conf", "det_conf",
                     "speed_px_s", "duration_sec", "basis", "bbox", "human_present"])

    print(f"输出目录  : {save_dir}")
    print("-" * 66)
    print("开始识别（Ctrl+C 或 q 退出）。真宠物不在场时，用手机放猫狗视频对着镜头也能验。")
    print("-" * 66)

    exit_code = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                if args.video:
                    if args.loop:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        playhead = 0.0
                        continue
                    print("[src] 视频播放结束")
                    break
                read_fail += 1
                if read_fail > 60:
                    print("[FATAL] 连续 60 次读不到帧，摄像头可能被别的程序占用")
                    exit_code = 2
                    break
                time.sleep(0.05)
                continue
            read_fail = 0
            frames_read += 1

            now = time.monotonic()
            if args.video:
                playhead += 1.0 / video_fps
                src_now = playhead
            else:
                src_now = now - t_start

            # ── 到点才分析：中间帧只显示，不重复推理 ────────────────
            if (now - last_analyze) >= analyze_period:
                last_analyze = now
                mono = time.monotonic()
                dets, infer_ms = infer_frame(session, input_name, frame)
                h, w = frame.shape[:2]
                # 传原始帧（见文件头铁律 3）
                results = tracker.update_or_create(mono, dets, w, h, frame)
                tracker.cleanup(mono)
                human.update(dets, mono)
                human_snap = human.snapshot(mono)
                analyze_count += 1

                if results:
                    frames_with_pet += 1
                for r in results:
                    label_counter[r["label"]] += 1
                    prev = conf_by_label.get(r["label"], 0.0)
                    conf_by_label[r["label"]] = max(prev, r["action_conf"])
                    writer.writerow([
                        datetime.now().strftime("%H:%M:%S.%f")[:-3],
                        f"{src_now:.2f}", analyze_count, f"{infer_ms:.1f}",
                        r["pet"], r["pet_id"], r["label"],
                        f'{r["action_conf"]:.3f}', f'{r["conf"]:.3f}',
                        f'{r["speed_px_s"]:.1f}', f'{r["duration_sec"]:.1f}',
                        r["basis"], json.dumps(r["bbox"]),
                        int(bool(human_snap.get("present"))),
                    ])
                csv_f.flush()

                # 终端按行汇报：不做花哨，一帧一行，方便贴日志
                if not args.no_display:
                    if results:
                        head = results[0]
                        print(f'[{analyze_count:4d}] {src_now:7.1f}s  '
                              f'{head["pet"]}#{head["pet_id"]} '
                              f'{head["label"]:<10s} conf={head["action_conf"]:.2f} '
                              f'| {head["basis"]}  ({infer_ms:.0f}ms)')
                    else:
                        print(f'[{analyze_count:4d}] {src_now:7.1f}s  absent '
                              f'| 画面内无猫狗  ({infer_ms:.0f}ms)')

                # 自动存证
                if args.save_every > 0 and (now - last_save) >= args.save_every:
                    last_save = now
                    shot = save_dir / f"shot-{analyze_count:04d}.jpg"
                    cv2.imwrite(str(shot), draw_overlay(
                        frame.copy(), results, human_snap,
                        [("local cam yolo", (255, 255, 255))]))

            # ── 显示 ────────────────────────────────────────────────
            if not args.no_display:
                head_lines = [
                    (f't={src_now:6.1f}s  analyze={args.analyze_fps:.1f}fps  '
                     f'infer={infer_ms:.0f}ms', (255, 255, 255)),
                    (f'pets={len(results)}  hold={results[0]["pet"] if results else "-"} '
                     f'{results[0]["label"] if results else "absent"}', (120, 255, 160)),
                    (f'person={"yes" if human_snap.get("present") else "no"}  '
                     f'log={log_path.name}', (200, 200, 200)),
                ]
                shown = draw_overlay(frame, results, human_snap, head_lines)
                cv2.imshow("local cam yolo  [q]=quit  [s]=shot", shown)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    print("[ui] 按键退出")
                    break
                if key == ord("s"):
                    shot = save_dir / f"manual-{analyze_count:04d}.jpg"
                    cv2.imwrite(str(shot), shown)
                    print(f"[ui] 已存图 {shot}")
            else:
                # 无窗口时按拍摄节奏走，避免空转过快把摄像头缓冲抽干
                time.sleep(min(0.01, analyze_period / 4))

            # ── 时长限制 ────────────────────────────────────────────
            if args.duration > 0 and (time.monotonic() - t_start) >= args.duration:
                print(f"[run] 达到 {args.duration}s，停止")
                break

    except KeyboardInterrupt:
        print("\n[ui] Ctrl+C 退出")
    finally:
        elapsed = time.monotonic() - t_start
        cap.release()
        csv_f.close()
        if not args.no_display:
            cv2.destroyAllWindows()

        print("\n" + "=" * 66)
        print("统计")
        print("=" * 66)
        print(f"运行时长      : {elapsed:.1f} s")
        print(f"读帧数        : {frames_read}")
        print(f"分析帧数      : {analyze_count}"
              f"  （有效分析速率 {analyze_count / max(elapsed, 1e-6):.2f} fps）")
        print(f"平均推理耗时  : {infer_ms:.1f} ms（最后一帧）")
        if analyze_count:
            ratio = frames_with_pet / analyze_count * 100
            print(f"有宠物帧占比  : {frames_with_pet}/{analyze_count} = {ratio:.1f}%")
        if label_counter:
            print("行为分布（按分析帧计，一帧多宠会重复计数）:")
            for label, cnt in label_counter.most_common():
                print(f"  {label:<11s} {LABEL_CN.get(label, ''):<6s} "
                      f"{cnt:4d} 次  最高动作置信度 {conf_by_label[label]:.2f}")
        else:
            print("行为分布      : 无（全程未检出猫狗）")
        print(f"日志          : {log_path}")
        imgs = sorted(save_dir.glob("*.jpg"))
        if imgs:
            print(f"存图          : {len(imgs)} 张，最新 {imgs[-1].name}")

        # 给出人话结论，免得看数字自己猜
        print("-" * 66)
        if frames_with_pet == 0:
            print("结论：全程没检出猫狗 —— 不是算法坏了，是画面里没有可检出的目标。")
            print("      先让猫/狗（或手机里播放的猫狗视频）占画面 1/3 以上再跑。")
        else:
            distinct = len(label_counter)
            print(f"结论：检测链路正常（有宠物帧 {frames_with_pet}/{analyze_count}），"
                  f"共判出 {distinct} 种行为。")
            if distinct <= 1:
                print("      只有一种标签属正常：短时片段里宠物多半只做一件事。"
                      "要验多动作，换不同片段或让宠物真的动起来。")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
