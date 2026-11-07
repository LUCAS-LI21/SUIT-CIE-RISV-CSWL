#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PC 摄像头视觉服务 —— 把电脑摄像头变成「与 K1 完全相同」的视觉接口。

为什么要它
    K1 板载 CSI 摄像头帧率偏低，且板端推理有固定开销。把「取帧」挪到电脑上、
    K1 只做推理，前端一行都不用改：它仍然只认 GET /v1/vision/latest，
    至于这个地址后面是谁，由配置决定。

两种推理位置，同一份接口
    默认            → 本机推理（K1 不在时先验前端与算法）
    --push <url>    → 本机只解码摄像头 → 编码 JPEG → POST /v1/vision/ingest
                      给 K1，由 K1 的 yolo 推理；K1 把结果回给本机，本机缓存后对外提供。

速度设计（本次重点）
    两个节奏，不要把两者混为一谈：
      · 检测节奏  —— 能跑多快跑多快。上限由推理耗时决定：
                    本机 CPU 实测 36.8 ms/帧 = 27.2 fps（1280×720，含 letterbox+NMS+跟踪），
                    K1 板端 int8 约 82 ms/帧，上限约 11 fps。
      · 行为节奏  —— 默认 0.5 s 一次（2 Hz，与历史标定一致），可用
                    --behavior-hz 提到 5 / 10 Hz。特征窗口（HISTORY_SEC /
                    SPEED_SEC / STABLE_SEC / FLOW_REF_DT …）已全部按「秒」定义，
                    光流按 FLOW_REF_DT 折算成 2 Hz 当量，所以提高这个频率
                    改的是**采样密度**，不是判定标准：稳态结论在 2/5/10 Hz 下
                    一致（scripts/behavior_lag_probe.py 有跨帧率用例），
                    只是起步/急停的延迟从 1.0 s 降到 0.6 s 左右。
    结果：在场/类型/框/人数按检测节奏更新（看起来是实时的），
          行为标签按 --behavior-hz 更新，两者都在返回体里标明时间。

字段契约（逐字对齐 k1-vision/vision_service.py 的 payload，见其 913-935 行）
    {"ok":true,"pet":"dog","label":"lying","action":"lying","action_conf":0.72,
     "basis":"…","conf":0.87,"duration_sec":42.0,"fps":1.97,
     "model":"yolov8n.onnx","ts":…,"count":1,"all":[…],"human":{…}}
    外加（前端不读也不影响）：
     "engine"       "pc"|"k1"
     "infer_ms"     本帧推理耗时
     "det_fps"      检测节奏实际帧率
     "label_age_ms" 行为标签距今多久（行为是 --behavior-hz，别把它当成检测延迟）
     "latency_ms"   从摄像头出帧到结果可用的延迟

启动
    .venv-train/Scripts/python.exe scripts/pc_cam_vision.py
    .venv-train/Scripts/python.exe scripts/pc_cam_vision.py --push http://192.168.88.10:11436
    .venv-train/Scripts/python.exe scripts/pc_cam_vision.py --max-fps 0     # 不限，跑满

自检
    curl http://127.0.0.1:11436/v1/vision/latest
    curl http://127.0.0.1:11436/health
"""

from __future__ import annotations

import argparse
import json
import http.client
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
VISION_DIR = ROOT / "k1-vision"
sys.path.insert(0, str(VISION_DIR))

try:
    import cv2
except ImportError:
    sys.exit("[FATAL] 缺 opencv：.venv-train/Scripts/python.exe -m pip install opencv-python")

import vision_service as V  # noqa: E402

# 行为判定的帧间隔。vision_service 的特征窗口已全部按「秒」定义
# （HISTORY_SEC / SPEED_SEC / STABLE_SEC / FLOW_REF_DT …），
# 因此这里改的是**采样密度**，不是判定标准：2 / 5 / 10 Hz 在稳态下
# 给出同一结论（scripts/behavior_lag_probe.py 有跨帧率一致性用例）。
# 默认 2 Hz 与历史标定值一致；本地 CPU 可开到 5~10 Hz 以减小起步延迟。
BEHAVIOR_DT = 0.5


# ══════════════════════════ 运行状态 ══════════════════════════

class FrameSlot:
    """最新的那一帧。只保留最新，故意不做队列。

    USB 摄像头 30 fps、推理 20 fps，如果排队列，每秒会积 10 帧，
    /latest 拿到的就是几百毫秒前的画面，「实时」直接变成录像。
    丢旧帧是这里唯一正确的策略。
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.frame = None
        self.ts = 0.0
        self.seq = 0

    def put(self, frame, ts):
        with self.lock:
            self.frame = frame
            self.ts = ts
            self.seq += 1

    def get(self):
        with self.lock:
            return self.frame, self.ts, self.seq


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest: dict = {"ok": False, "reason": "starting"}
        self.frame_jpeg: bytes = b""
        self.det_frames = 0
        self.det_fps = 0.0
        self.infer_ms = 0.0
        self.read_ms = 0.0
        self.label_ts = 0.0
        self.mode = "local"
        self.push_url = ""
        self.model_name = ""
        self.last_error = ""
        self.running = True
        # ── 推流模式的「最新帧」槽 ──────────────────────────────
        # 原来是：编码 → 同步 POST → 等 K1 返回 → 下一帧。K1 推理 169 ms，
        # 加上网络往返，整条链路被锁在 2.8 fps 左右，而 K1 侧行为判定要 5 Hz，
        # 于是 --behavior-hz 5 只写在纸面上（实测 behavior_hz=5.0 但 fps=2.79，
        # 真正推进 tracker 的还是 2.79 次/秒）。
        # 现在推流与编码解耦：编码线程只负责把最新帧放进槽里，
        # 发送线程一次只保留一个在途请求，新帧覆盖旧帧（与 FrameSlot 同一策略，
        # 排队会让画面变成录像）。这样 K1 永远有最新帧可推，帧率等于推理速度。
        self.push_lock = threading.Lock()
        self.push_jpeg: bytes = b""
        self.push_seq = 0
        self.push_sent = 0

    def put_push(self, jpeg: bytes):
        with self.push_lock:
            self.push_jpeg = jpeg
            self.push_seq += 1

    def take_push(self):
        """取「尚未发出过的最新帧」，没有新帧返回 None。"""
        with self.push_lock:
            if not self.push_jpeg or self.push_seq == self.push_sent:
                return None
            self.push_sent = self.push_seq
            return self.push_jpeg

    def publish(self, payload: dict):
        with self.lock:
            self.latest = payload

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.latest)


S = State()


def _probe(cap, n=6, seconds=0.6):
    """读几帧，返回 (是否出图, 平均亮度, 帧率)。全黑帧的亮度接近 0。

    前 n 帧只用来等自动曝光收敛，不参与亮度统计：刚打开时前一两帧
    往往还是上一轮的旧曝光值，拿它当亮度会把「还没调好」误判成「场景很黑」。
    """
    ok, frame = False, None
    for _ in range(n):
        cap.read()                      # 丢弃，等 AE 收敛
    for _ in range(4):
        ok, frame = cap.read()
        if ok and frame is not None:
            break
    if not ok or frame is None:
        return False, -1.0, 0.0
    bright = float(frame.mean())
    t0 = time.perf_counter()
    cnt = 0
    while time.perf_counter() - t0 < seconds:
        ok2, _f = cap.read()
        if ok2:
            cnt += 1
    fps = cnt / max(1e-6, time.perf_counter() - t0)
    return True, bright, fps


def _boost_gain(cap, target: float = 70.0, max_gain: int = 200) -> float:
    """暗环境下用**增益**补亮度，而不是用曝光时间。

    这是 2026-11-06 实测暴露的问题：`--prefer-fps` 把曝光锁在 -3 档
    （27.9 fps / 亮度 26），帧率保住了，但画面接近全黑。亮度优先的 -2 档
    亮度 49 却只有 10.6 fps。两边都不理想，关键在于「亮度从哪来」：

      · 曝光时间 → 与帧率成反比，直接杀掉动作识别最缺的时间分辨率
      · 增益     → 只放大噪声，帧率一点不掉

    噪声对 YOLO 的影响远小于运动模糊和暗场，所以优先用增益补。

    set 之后**必须回读**：不少 UVC 摄像头把 GAIN 暴露成只读或做整数截断，
    set 会静默失败。回读不变就说明这条路不通，直接放弃，不假装成功。
    """
    try:
        cur = float(cap.get(cv2.CAP_PROP_GAIN))
    except Exception:
        return 0.0
    if cur < 0:
        print("[cam] 该摄像头不支持手动增益，亮度只能靠环境光")
        return 0.0

    cap.set(cv2.CAP_PROP_GAIN, cur)
    ok, b0, _f = _probe(cap, n=6, seconds=0.25)
    if not ok:
        return 0.0
    print(f"[cam]   当前 gain={cur:.0f} 亮度={b0:.0f}（目标 {target:.0f}）")

    best_g, best_b = cur, b0
    for g in (cur + 40, cur + 80, cur + 120, cur + 180, max_gain):
        g = min(g, max_gain)
        if g <= cur:
            continue
        cap.set(cv2.CAP_PROP_GAIN, g)
        ok, b, _f = _probe(cap, n=6, seconds=0.25)
        if not ok:
            break
        try:
            real = float(cap.get(cv2.CAP_PROP_GAIN))
        except Exception:
            real = g
        print(f"[cam]   gain {g} → 回读 {real:.0f} 亮度={b:.0f}")
        if b > best_b:
            best_g, best_b = g, b
        if b >= target:
            break
        if abs(real - g) > 1.0 and abs(real - cur) < 1.0:
            print("[cam] 增益写入无效（回读不变），放弃")
            break

    cap.set(cv2.CAP_PROP_GAIN, best_g)
    return best_b


def tune_exposure(cap, tag: str, prefer_fps: bool = False):
    """自动曝光在弱光下会把曝光时间拉到几百毫秒，帧率掉到 1~3 fps。

    这不是理论风险：2026-11-05 本机实测自动曝光 3.5 fps、亮度 193；
    锁到 -3 档后 23.5 fps、亮度 72。行为判定的时间分辨率直接由帧率决定，
    3.5 fps 下 0.3~0.6 s 的跳跃整段落在两帧之间，永远采不到。

    但反向也有坑：光不够时自动曝光会把帧率顶到 30 fps（曝光上限 1/30 s），
    画面却是亮度 10 的暗图 —— 实测就是这样，帧率很好看、YOLO 什么也看不见。
    所以判据是**两个都要**：fps ≥ 10 且亮度 ≥ 50 才认为不用管。
    否则从暗到亮扫档，取「fps ≥ 8 且亮度最高」的一档。

    写死某一档在换环境后必然翻车（亮房间过曝、暗房间欠曝），每次启动重测。

    prefer_fps=True 时改为「帧率优先」：只在 fps ≥ 25 的档里挑（同一档内仍取
    亮度最高的）。默认 False 保持「亮度优先」的旧行为。用途是在动作识别场景下
    用亮度换时间分辨率 —— 但暗帧会不会让 YOLO 失明必须实测，不能假设。
    """
    ok, bright, fps = _probe(cap)
    if not ok:
        return
    min_fps = 25.0 if prefer_fps else 8.0
    skip_fps = 25.0 if prefer_fps else 10.0
    if fps >= skip_fps and bright >= 50.0:
        print(f"[cam] 曝光自动，{fps:.1f} fps 亮度={bright:.0f}，无需干预")
        return

    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)      # 0.25 = 手动
    best = (None, -1.0, 0.0)                       # (档位, 亮度, fps)
    for expo in (-6, -5, -4, -3, -2):
        cap.set(cv2.CAP_PROP_EXPOSURE, expo)
        ok, b, f = _probe(cap, n=8, seconds=0.6)
        if not ok:
            continue
        print(f"[cam]   曝光 {expo}: {f:.1f} fps 亮度={b:.0f}")
        if f >= min_fps and b > best[1]:
            best = (expo, b, f)
    if best[0] is None:
        print(f"[cam] 曝光调优失败（各档都低于 {min_fps:.0f} fps 或全黑），保持自动曝光 "
              f"{fps:.1f} fps 亮度={bright:.0f}；把环境调亮会明显改善")
        return
    cap.set(cv2.CAP_PROP_EXPOSURE, best[0])
    print(f"[cam] 曝光已锁定 {best[0]}：{best[2]:.1f} fps 亮度={best[1]:.0f}"
          f"（自动曝光是 {fps:.1f} fps 亮度={bright:.0f}）")
    # 帧率定了之后，再用增益把亮度往目标推 —— 增益不动帧率，是这里唯一
    # 能同时保住 30 fps 和看清画面的手段。目标 70 是经验值：实测亮度 27 时
    # 人检测 conf 只有 0.45~0.77，而亮度 70 上下能稳定在 0.85 以上。
    if best[1] < 70.0:
        got = _boost_gain(cap, target=70.0)
        if got > 0:
            print(f"[cam] 增益补偿后亮度 {best[1]:.0f} → {got:.0f}（帧率不变）")


_AUTO_EXPOSURE_CANDIDATES = (1, 0.75, 3)   # MSMF / DSHOW / V4L2 三种约定


def _reset_auto_exposure(cap) -> float:
    """把摄像头从「手动曝光」放回自动，返回恢复后的亮度。

    2026-11-06 事故复盘：上一次运行的 tune_exposure 会 `set(AUTO_EXPOSURE, 0.25)`
    切到手动并锁在 -6/-3 这类暗档。**这个状态留在摄像头设备里，Python 进程退出
    也不还原**（实测隔十几分钟、换后端、换进程都还是曝光 -6）。于是下一次启动：

        _probe 读到亮度 4.8 → `bright > 8.0` 不成立 → 三个后端全判「不可用」
        → open_camera 返回 None → FATAL 退出
        → 而唯一能调曝光的 tune_exposure 排在这个判断之后，永远跑不到

    越重启越暗，且看起来像「摄像头坏了」或「房间没开灯」。所以打开摄像头后的
    第一件事必须是解除手动曝光，让 AE 重新收敛，再谈亮度和帧率。

    不同后端 AUTO_EXPOSURE 的约定不一样（MSMF 1=自动、DSHOW 0.75=自动、
    V4L2 3=自动），设错值在 MSMF 上会被截断成 0 = 手动，等于没设。所以逐个
    尝试并**用实测亮度选**，而不是相信某一个魔法数字。

    注意：如果环境真的没光，所有候选值都会得到同样低的亮度，此时返回的就是
    真实亮度，后面的判断照常工作 —— 不会把「真黑」误判成「设置有残留」。
    """
    best_v, best_b = None, -1.0
    for v in _AUTO_EXPOSURE_CANDIDATES:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, v)
        ok, b, _f = _probe(cap, n=8, seconds=0.25)
        if not ok:
            continue
        print(f"[cam]   自动曝光尝试 {v}: 亮度={b:.1f}")
        if b > best_b:
            best_v, best_b = v, b
    if best_v is not None:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, best_v)
    return best_b


def open_camera(index: int, width: int, height: int, prefer_fps: bool = False):
    """按后端逐个试，并且**用画面亮度判断是否真的出图**。

    原实现直接优先 DSHOW 并只检查 read() 的返回值。2026-11-05 实测：
    这台机器 DSHOW 后端 read() 返回 True、但每一帧都是纯黑（亮度 0.0），
    帧率还被自动曝光拖到 1.5 fps；同一设备换 MSMF 立刻正常（亮度 193）。
    「能 read 到」不等于「有画面」，所以这里必须验亮度，不能只看返回值。
    """
    for api, tag in ((cv2.CAP_MSMF, "MSMF"), (cv2.CAP_ANY, "ANY"), (cv2.CAP_DSHOW, "DSHOW")):
        cap = cv2.VideoCapture(index, api)
        if not cap.isOpened():
            cap.release()
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # 必须先解除上一次运行留下的手动曝光，否则会读到上一轮的暗档亮度，
        # 把可用后端误判成不可用（详见 _reset_auto_exposure 的注释）。
        restored = _reset_auto_exposure(cap)
        ok, bright, fps = _probe(cap)
        if ok and bright > 8.0:
            print(f"[cam] 已打开 index={index} 后端={tag} "
                  f"{int(cap.get(3))}x{int(cap.get(4))} 亮度={bright:.0f} {fps:.1f} fps")
            tune_exposure(cap, tag, prefer_fps)
            return cap
        print(f"[cam] 后端 {tag} 不可用（ok={ok} 亮度={bright:.1f} "
              f"重置自动曝光后={restored:.1f}）")
        cap.release()
    return None


# ══════════════════════════ 推理 ══════════════════════════

def build_session(model_path: Path):
    import onnxruntime as ort
    providers = ort.get_available_providers()
    prov = "CUDAExecutionProvider" if "CUDAExecutionProvider" in providers else "CPUExecutionProvider"
    so = ort.SessionOptions()
    # 线程数必须显式限制：默认按逻辑核数开，对 yolov8n 是负优化
    # （实测 20 线程 600+ ms，4 线程 33 ms）。
    so.intra_op_num_threads = max(1, min(4, os.cpu_count() or 4))
    so.inter_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess = ort.InferenceSession(str(model_path), so, providers=[prov])
    return sess, sess.get_inputs()[0].name, prov


class LocalEngine:
    """在本机跑 YOLO。检测每帧都跑，行为每 0.5 s 推进一次。"""

    def __init__(self, model_path: Path):
        self.sess, self.input_name, self.prov = build_session(model_path)
        self.tracker = V.MultiPetTracker()
        self.human = V.HumanWatcher()
        self.last_track = 0.0
        self.last_payload: dict = {"ok": False, "reason": "warming"}
        dummy = np.zeros((1, 3, V.IMG_SIZE, V.IMG_SIZE), dtype=np.float32)
        t0 = time.perf_counter()
        self.sess.run(None, {self.input_name: dummy})
        print(f"[model] {model_path.name} 预热 {(time.perf_counter()-t0)*1000:.0f} ms  {self.prov}")

    def infer(self, frame, capture_ts: float, jpeg: bytes) -> dict:
        t0 = time.perf_counter()
        h, w = frame.shape[:2]
        canvas, scale, pad_w, pad_h = V.letterbox(frame)
        blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        out = self.sess.run(None, {self.input_name: blob})[0]
        dets = V.decode_yolov8(out, scale, pad_w, pad_h, w, h)

        now = time.monotonic()
        self.human.update(dets, now)

        # ── 行为节奏：按 BEHAVIOR_DT 采样 ──────────────────────────
        # 特征窗口按秒定义，所以这个间隔只决定采样密度：
        # 间隔越短，起步/急停的延迟越小，稳态结论不变。
        # 注意别和推理帧率混为一谈——推理可以跑满，行为判定是另一个节奏。
        do_behavior = (now - self.last_track) >= BEHAVIOR_DT
        if do_behavior:
            # 必须传原始帧（不是 canvas）：bbox 是反 letterbox 后的原图坐标，
            # 拿原图坐标去切 640 画布会让光流恒为 0（vision_service:895-903）。
            results = self.tracker.update_or_create(now, dets, w, h, frame)
            self.tracker.cleanup(now)
            self.last_track = now
            S.label_ts = time.time()

            # 漏检时沿用上一次判定（同 vision_service）：见 V.MultiPetTracker.stale_result。
            # results 为空不等于宠物走了 —— 目标过大/被裁切时必漏检。
            stale = self.tracker.stale_result(now, V.STALE_KEEP_SEC) if not results else None
            top_r = results[0] if results else stale

            payload = {
                "ok": True,
                "pet": top_r["pet"] if top_r else "",
                "label": top_r["label"] if top_r else "absent",
                "action": top_r["action"] if top_r else "absent",
                "action_raw": top_r.get("action_raw", "") if top_r else "",
                "action_conf": top_r["action_conf"] if top_r else 0.0,
                "basis": top_r["basis"] if top_r else "",
                "conf": top_r["conf"] if top_r else 0.0,
                "duration_sec": top_r["duration_sec"] if top_r else 0.0,
                "fps": 0.0,            # 下面统一填检测帧率
                "model": S.model_name,
                "ts": time.time(),
                "count": len(results) if results else (1 if stale else 0),
                "all": results if results else ([stale] if stale else []),
                # true 表示本帧没检出、字段沿用上次，前端不能当实时用
                "stale": bool(stale),
                "stale_age_ms": stale["stale_age_ms"] if stale else 0,
                "human": self.human.snapshot(now),
                "engine": "pc",
            }
            self.last_payload = payload
        else:
            # 两次行为判定之间，只刷新「检测级」字段：在场、类型、置信度、框。
            # 行为标签沿用上一次（它就是 2 Hz 的量），但会把 age 报出去。
            payload = dict(self.last_payload)
            if not payload.get("ok"):
                payload = {
                    "ok": True, "pet": "", "label": "unknown", "action": "unknown",
                    "action_conf": 0.0, "basis": "行为窗口未起步", "conf": 0.0,
                    "duration_sec": 0.0, "count": 0, "all": [],
                    "model": S.model_name, "ts": time.time(),
                    "human": self.human.snapshot(now), "engine": "pc",
                }
            else:
                payload["human"] = self.human.snapshot(now)
                payload["ts"] = time.time()
                if dets:
                    # 检测级刷新：类型与置信度按最新一帧走，行为标签仍用它自己的时间戳
                    top = max(dets, key=lambda d: d[1])
                    payload["conf"] = round(float(top[1]), 3)

        S.infer_ms = (time.perf_counter() - t0) * 1000.0
        payload["infer_ms"] = round(S.infer_ms, 1)
        payload["det_fps"] = round(S.det_fps, 1)
        payload["fps"] = round(S.det_fps, 2)
        payload["label_age_ms"] = int((time.time() - S.label_ts) * 1000) if S.label_ts else -1
        payload["latency_ms"] = int((time.monotonic() - capture_ts) * 1000)
        payload["behavior_hz"] = round(1.0 / BEHAVIOR_DT, 1)
        return payload


# ══════════════════════════ 推送（PC 帧 → K1 推理） ══════════════════════════

_PUSH_CONN = None      # 持久连接（见 push_frame 注释）
_PUSH_TARGET = ("", 0, "")   # (host, port, path)


def push_frame(url: str, jpeg: bytes, timeout: float = 3.0) -> dict | None:
    """把一帧 POST 给 K1。

    这里刻意不用 urllib：一是它默认吃系统代理（这台机器设了 HTTP_PROXY，
    127.0.0.1 的 POST 会被丢给代理，表现为本地看着正常、K1 一帧都收不到）；
    二是它不复用连接，而 PC→K1 要过 hdc fport + 3568 nc 中继 + SSH，
    新建连接 120~380 ms，比 K1 推理（160 ms）还贵。长连接把这段开销摊掉。
    实测同一帧端到端 271~542 ms → 约 180 ms，正是行为判定能否到 5 Hz 的关键。
    """
    global _PUSH_CONN, _PUSH_TARGET
    if not _PUSH_TARGET[0]:
        u = url.split("://", 1)[-1]
        hostport, _, path = u.partition("/")
        host, _, port = hostport.partition(":")
        _PUSH_TARGET = (host, int(port or 80), "/" + path)

    host, port, path = _PUSH_TARGET
    for attempt in (1, 2):
        try:
            if _PUSH_CONN is None:
                _PUSH_CONN = http.client.HTTPConnection(host, port, timeout=timeout)
            _PUSH_CONN.request("POST", path, body=jpeg,
                               headers={"Content-Type": "image/jpeg",
                                        "Content-Length": str(len(jpeg))})
            resp = _PUSH_CONN.getresponse()
            data = resp.read()
            return json.loads(data.decode("utf-8"))
        except (http.client.HTTPException, OSError, json.JSONDecodeError, TimeoutError) as e:
            S.last_error = f"{type(e).__name__}: {e}"
            try:
                if _PUSH_CONN:
                    _PUSH_CONN.close()
            except Exception:
                pass
            _PUSH_CONN = None
            # 长连接被对端关掉是常态（服务重启/超时），第一次失败重连再试一次
            if attempt == 2:
                return None
    return None


# ══════════════════════════ 采集线程 ══════════════════════════

def capture_loop(args, slot: FrameSlot):
    cap = open_camera(args.camera, args.width, args.height, args.prefer_fps)
    if cap is None:
        S.publish({"ok": False, "reason": "camera_open_failed"})
        print("[FATAL] 打不开摄像头。可能被别的程序占着（浏览器 getUserMedia 会独占）。")
        S.running = False
        return

    fail = 0
    while S.running:
        t0 = time.monotonic()
        ok, frame = cap.read()
        S.read_ms = (time.monotonic() - t0) * 1000.0
        if not ok or frame is None:
            fail += 1
            if fail >= 10:
                print("[cam] 连续 10 次读帧失败，重置采集器")
                cap.release()
                time.sleep(1.0)
                cap = open_camera(args.camera, args.width, args.height, args.prefer_fps)
                fail = 0
            continue
        fail = 0
        slot.put(frame, time.monotonic())

    cap.release()
    print("[cam] 已释放摄像头")


# ══════════════════════════ 推理线程 ══════════════════════════

def push_loop(url: str):
    """把最新帧推给 K1，并把 K1 的返回发布成本地 /v1/vision/latest。

    一次只允许一个在途请求：K1 的 camingest 是串行推理（一把锁），
    并发 POST 只会在它那边排队，结果是延迟越积越大、画面越来越旧。
    """
    endpoint = url.rstrip("/") + "/v1/vision/ingest"
    win_start = time.monotonic()
    win_count = 0
    while S.running:
        jpeg = S.take_push()
        if jpeg is None:
            time.sleep(0.003)          # 没有新帧就让出 CPU
            continue
        payload = push_frame(endpoint, jpeg)
        if payload is None:
            payload = {"ok": False, "reason": "k1_unreachable",
                       "detail": S.last_error, "ts": time.time()}
        else:
            payload["engine"] = "k1"
        S.publish(payload)
        S.det_frames += 1
        win_count += 1
        now = time.monotonic()
        if now - win_start >= 1.0:
            S.det_fps = win_count / (now - win_start)
            win_start = now
            win_count = 0
            print(f"[push] {S.det_fps:.1f} fps  infer={payload.get('infer_ms', 0):.0f}ms  "
                  f"label={payload.get('label')}", flush=True)


def worker_loop(args, slot: FrameSlot, engine: LocalEngine | None):
    push_url = args.push.rstrip("/") + "/v1/vision/ingest" if args.push else ""
    min_interval = 0.0 if args.max_fps <= 0 else 1.0 / args.max_fps
    last_seq = -1
    win_start = time.monotonic()
    win_count = 0

    while S.running:
        frame, ts, seq = slot.get()
        if frame is None or seq == last_seq:
            time.sleep(0.002)          # 没有新帧就让出 CPU，别空转
            continue
        last_seq = seq

        tick = time.monotonic()

        ret, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        jpeg = enc.tobytes() if ret else b""
        with S.lock:
            S.frame_jpeg = jpeg

        try:
            if push_url:
                # 交给发送线程，本线程只编码，不等 K1 返回（见 State.put_push 注释）
                S.put_push(jpeg)
                continue
            payload = engine.infer(frame, ts, jpeg)
            S.publish(payload)
        except Exception as e:
            import traceback
            traceback.print_exc()
            S.publish({"ok": False, "reason": f"infer_error: {e}"})

        S.det_frames += 1
        win_count += 1
        now = time.monotonic()
        if now - win_start >= 1.0:
            S.det_fps = win_count / (now - win_start)
            win_start = now
            win_count = 0
            print(f"[det] {S.det_fps:.1f} fps  infer={S.infer_ms:.0f}ms  "
                  f"read={S.read_ms:.0f}ms  label={payload.get('label')}")

        # 限速（默认不限）：只在推理快过目标帧率时补睡眠
        if min_interval > 0:
            spent = time.monotonic() - tick
            if spent < min_interval:
                time.sleep(min_interval - spent)


# ══════════════════════════ HTTP ══════════════════════════

class ExclusiveServer(ThreadingHTTPServer):
    """禁止端口复用。

    Windows 上 SO_REUSEADDR 不是「快速重启」，而是允许**两个进程绑同一端口**，
    结果是两个服务都在监听、流量被平分或全落到其中一个旧实例上。
    实测症状：11436 出现两条 LISTENING，其中一个 det_frames 恒为 0、不出图，
    页面显示的帧率被压到 11 fps（正常 24）。
    Python 的 HTTPServer 默认 allow_reuse_address=1，所以必须显式关掉：
    这样第二次启动会直接抛 OSError，而不是静悄悄跑起来。
    """

    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    server_version = "PcCamVision/2.0"

    def log_message(self, fmt, *a):
        if a and str(a[0]).startswith(("4", "5")):
            sys.stderr.write("[http] %s\n" % (fmt % a))

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/v1/vision/latest":
            snap = S.snapshot()
            return self._send(200 if snap.get("ok") else 503, snap)
        if path == "/v1/vision/frame.jpg":
            with S.lock:
                jpeg = S.frame_jpeg
            if not jpeg:
                return self._send(503, {"ok": False, "reason": "no_frame"})
            return self._send(200, jpeg, "image/jpeg")
        if path == "/health":
            return self._send(200, {
                "ok": True,
                "mode": S.mode,
                "push": S.push_url,
                "det_frames": S.det_frames,
                "det_fps": round(S.det_fps, 2),
                "behavior_hz": round(1.0 / BEHAVIOR_DT, 1),
                "infer_ms": round(S.infer_ms, 1),
                "read_ms": round(S.read_ms, 1),
                "model": S.model_name,
                "last_error": S.last_error,
            })
        return self._send(404, {"ok": False, "reason": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--max-fps", type=float, default=0.0,
                    help="检测帧率上限；0=跑满（默认）。行为判定由 --behavior-hz 决定")
    ap.add_argument("--behavior-hz", type=float, default=2.0,
                    help="行为判定采样率（默认 2，与历史标定一致）。"
                         "特征窗口按秒定义，调到 5/10 只减小起步延迟、不改稳态结论。"
                         "本地 CPU 推理约 27 fps，5~10 均可跟得上")
    ap.add_argument("--prefer-fps", action="store_true",
                    help="曝光选档改「帧率优先」（只在 ≥25 fps 的档里挑）。"
                         "动作识别的时间分辨率由帧率决定，但暗帧可能让 YOLO 失明，"
                         "用前先确认亮度够用。默认关闭=亮度优先")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=11436)
    ap.add_argument("--model", default=str(ROOT / "k1-vision" / "yolov8n.onnx"))
    ap.add_argument("--push", default="", help="把帧推给 K1 推理，例如 http://192.168.88.10:11436")
    args = ap.parse_args()

    global BEHAVIOR_DT
    if args.behavior_hz <= 0:
        sys.exit("[FATAL] --behavior-hz 必须 > 0")
    BEHAVIOR_DT = 1.0 / args.behavior_hz

    model_path = Path(args.model)
    if not model_path.exists():
        sys.exit(f"[FATAL] 模型不存在：{model_path}")

    S.model_name = model_path.name
    S.mode = "push" if args.push else "local"
    S.push_url = args.push

    engine = None
    if args.push:
        print(f"[mode] 帧推送到 {args.push} 由 K1 推理")
    else:
        engine = LocalEngine(model_path)
        print("[mode] 本机推理")

    slot = FrameSlot()
    threading.Thread(target=capture_loop, args=(args, slot), daemon=True).start()
    threading.Thread(target=worker_loop, args=(args, slot, engine), daemon=True).start()
    if args.push:
        threading.Thread(target=push_loop, args=(args.push,), daemon=True).start()

    try:
        srv = ExclusiveServer((args.host, args.port), Handler)
    except OSError as e:
        sys.exit("[FATAL] %s:%d 已被占用（%s）。\n"
                 "        多半是上一个实例没退干净；先看 netstat -ano | findstr %d，"
                 "结束对应进程再启动。" % (args.host, args.port, e, args.port))
    srv.daemon_threads = True
    print("=" * 66)
    print(f"PC 摄像头视觉服务  http://{args.host}:{args.port}")
    print(f"  检测节奏  : {'跑满（受推理耗时限制）' if args.max_fps <= 0 else f'上限 {args.max_fps} fps'}")
    if args.push:
        # 推流模式下行为判定跑在 K1 上，本机这一行没有意义，别印出来误导
        print(f"  行为节奏  : 由 K1 决定（cam_ingest_service --behavior-hz，当前默认 5 Hz）")
    else:
        print(f"  行为节奏  : {1.0/BEHAVIOR_DT:.1f} Hz（--behavior-hz，特征窗口按秒定义）")
    print(f"  GET /v1/vision/latest     ← 前端拉这个")
    print(f"  GET /v1/vision/frame.jpg  ← 当前帧")
    print("=" * 66)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[svc] 停止")
    finally:
        S.running = False
        srv.server_close()


if __name__ == "__main__":
    main()
