#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K1 接帧推理服务 —— 接收电脑摄像头推来的 JPEG，在 K1 上跑 YOLO，把结果给前端。

为什么要这个
    K1 的 CSI 摄像头帧率不够（行为阈值按 2 fps 标定，CSI 实际出不到），
    所以把「取帧」这件事挪到电脑上，K1 只保留「推理」。
    对前端来说没有任何变化：它仍然只 GET /v1/vision/latest。

与 vision_service.py 的关系
    同一个推理内核（letterbox / decode_yolov8 / MultiPetTracker / HumanWatcher
    全部 import 复用，不另写一份判定），区别只在于「帧从哪来」：
        vision_service.py   —— 自己开 CSI 摄像头
        cam_ingest_service.py —— 等 POST /v1/vision/ingest 送帧进来
    所以两边判出来的行为是同一套标准，前端不需要区分。

端口
    占 11436，与 vision_service.py 相同 —— 两者互斥，同一时刻只能起一个。
    好处是前端/网关的地址一个字都不用改。

接口
    POST /v1/vision/ingest        body 直接是 JPEG 字节（Content-Type: image/jpeg）
                                  200 → 返回与 /v1/vision/latest 完全相同的载荷
    GET  /v1/vision/latest        最近一次推理结果
    GET  /v1/vision/frame.jpg     最近收到的原始帧（抓帧取证用）
    POST /v1/vision/pause         LLM 网关仲裁用：暂停推理（仍回最后一帧）
    POST /v1/vision/resume
    GET  /health

字段契约（逐字对齐 vision_service.py 913-935 行）
    {"ok":true,"pet","label","action","action_conf","basis","conf",
     "duration_sec","fps","model","ts","count","all","human"}
    未就绪：503 {"ok":false,"reason":"..."}

用法
    python3 cam_ingest_service.py --model ./yolov8n.q.onnx --port 11436
    # 电脑侧：scripts/pc_cam_vision.py --push http://192.168.88.10:11436
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import cv2
except ImportError:
    sys.exit("[FATAL] 缺 opencv（板端：apt install python3-opencv）")

try:
    import onnxruntime as ort
except ImportError:
    sys.exit("[FATAL] 缺 onnxruntime（板端：python3-spacemit-ort）")

import vision_service as V  # noqa: E402

# 行为判定的帧间隔（秒）。vision_service 里所有阈值都在 2 Hz 下标定，
# 但它从 2026-11-05 起把所有特征窗口改成**按秒定义**、光流按 FLOW_REF_DT
# 折算回 2 Hz 当量，因此帧率可以放开而不必重标阈值。
# 2 Hz 采不到 0.3~0.6 s 的跳跃（一笔动作整段落在两帧之间），默认取 5 Hz。
BEHAVIOR_DT = 0.2


class IngestWorker:
    """单实例、串行推理。帧从 HTTP 送来，天然是 2 fps（由电脑侧节流）。"""

    def __init__(self, model_path: str, conf_thres: float | None = None):
        self.model_path = model_path
        self.lock = threading.Lock()
        self.tracker = V.MultiPetTracker()
        self.human = V.HumanWatcher()
        if conf_thres is not None:
            V.CONF_THRES = conf_thres

        providers = ort.get_available_providers()
        # 板端 SpaceMIT EP 优先；PC 上跑就是 CPU
        prov = ("SpaceMITExecutionProvider"
                if "SpaceMITExecutionProvider" in providers else "CPUExecutionProvider")
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, min(4, os.cpu_count() or 4))
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(model_path, so, providers=[prov])
        self.input_name = self.session.get_inputs()[0].name
        self.providers = self.session.get_providers()

        # 预热，避免第一帧被当成 8~10 倍慢
        dummy = np.zeros((1, 3, V.IMG_SIZE, V.IMG_SIZE), dtype=np.float32)
        t0 = time.perf_counter()
        self.session.run(None, {self.input_name: dummy})
        self.warm_ms = (time.perf_counter() - t0) * 1000.0

        self.latest: dict = {"ok": False, "reason": "waiting_first_frame"}
        self.frame_jpeg: bytes = b""
        self.frames = 0
        self.fps = 0.0
        self.infer_ms = 0.0
        self.paused = False
        # 暂停起始时刻。LLM 网关的「暂停→恢复」是一次握手：
        # 网关转发前 POST /pause，拿到上游结果后在 finally 里 POST /resume。
        # 一旦请求在中途断掉（前端超时走人、SSH/中继掉线、网关被重启），
        # 那个 finally 就跑不到，视觉会**永久冻在 paused**——
        # 实测踩过：paused=true 挂了几分钟，前端一直显示旧帧，
        # 而且 infer_ms 被排队帧灌到 1371 ms，看着像板子算不动。
        # 所以给暂停加超时自愈：超时后自己恢复，宁可多跑几帧也不冻住。
        self._paused_at = 0.0
        self.pause_timeout = 45.0
        self._t_prev = time.monotonic()
        # 行为节奏：严格 0.5 s 推进一次 tracker（阈值按 2 fps 标定）。
        # 检测每帧都跑，所以「在场/类型/框」是实时的，「行为标签」是 2 Hz。
        self._last_track = 0.0
        self._label_ts = 0.0
        self.model_name = Path(model_path).name

    def ingest(self, jpeg: bytes) -> dict:
        frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return {"ok": False, "reason": "jpeg_decode_failed"}

        with self.lock:
            self.frame_jpeg = jpeg

            if self.paused:
                # 仲裁：LLM 期间让出内存带宽。不跑模型，返回上一帧结果，
                # 前端不会熔断（与 vision_service 的处理一致）。
                # 但暂停不能没有尽头：超时即自愈，见 _paused_at 的注释。
                if self._paused_at <= 0.0:
                    # 兜底：paused 被直接置位而没记时刻，就地武装计时。
                    self._paused_at = time.monotonic()
                elif time.monotonic() - self._paused_at > self.pause_timeout:
                    self.paused = False
                    self._paused_at = 0.0
                    print("[cam] 暂停超时 %.0fs，自动恢复推理" % self.pause_timeout,
                          flush=True)
                else:
                    return self.latest

            t0 = time.perf_counter()
            canvas, scale, pad_w, pad_h = V.letterbox(frame)
            blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
            out = self.session.run(None, {self.input_name: blob})[0]
            h, w = frame.shape[:2]
            dets = V.decode_yolov8(out, scale, pad_w, pad_h, w, h)

            now = time.monotonic()
            self.human.update(dets, now)

            dt = now - self._t_prev
            self._t_prev = now
            self.fps = 1.0 / dt if dt > 0 else 0.0
            self.frames += 1
            self.infer_ms = (time.perf_counter() - t0) * 1000.0

            # ── 行为节奏：每 BEHAVIOR_DT 秒推进一次 tracker ──────────
            # 检测（帧/类别/框/人数）每帧都更新，只有行为标签按节奏更新。
            # 早期这里是硬编码 0.5 s 并在注释里写「每帧都喂 tracker 会毁掉判定」，
            # 那个结论只在特征窗口按**采样点数**定义时成立；2026-11-05 起窗口
            # 全部按秒定义、光流按 FLOW_REF_DT 折算，帧率只增加采样密度，
            # 判定本身不变（见 scripts/behavior_lag_probe.py 的跨帧率用例）。
            if (now - self._last_track) >= BEHAVIOR_DT:
                # 必须传原始帧：bbox 是反 letterbox 后的原图坐标，
                # 拿原图坐标去切 640 画布会让光流恒为 0（vision_service:895-903）。
                results = self.tracker.update_or_create(now, dets, w, h, frame)
                self.tracker.cleanup(now)
                self._last_track = now
                self._label_ts = time.time()
                self.latest = {
                    "ok": True,
                    "pet": results[0]["pet"] if results else "",
                    "label": results[0]["label"] if results else "absent",
                    "action": results[0]["action"] if results else "absent",
                    "action_conf": results[0]["action_conf"] if results else 0.0,
                    "basis": results[0]["basis"] if results else "",
                    "conf": results[0]["conf"] if results else 0.0,
                    "duration_sec": results[0]["duration_sec"] if results else 0.0,
                    "fps": round(self.fps, 2),
                    "model": self.model_name,
                    "ts": time.time(),
                    "count": len(results),
                    "all": results,
                    "human": self.human.snapshot(now),
                    "engine": "k1",
                    "infer_ms": round(self.infer_ms, 1),
                }
                if self.frames % 20 == 0:
                    print(f"[frame {self.frames}] det={self.fps:.1f}fps "
                          f"label={self.latest['label']} pets={self.latest['count']} "
                          f"infer={self.infer_ms:.0f}ms")
            else:
                # 两次行为判定之间只刷新检测级字段，行为标签沿用上一次，
                # 但把它的年龄报出去 —— 别让调用方以为标签是这一帧算的。
                payload = dict(self.latest)
                if payload.get("ok"):
                    payload["human"] = self.human.snapshot(now)
                    payload["ts"] = time.time()
                    if dets:
                        top = max(dets, key=lambda d: d[1])
                        payload["conf"] = round(float(top[1]), 3)
                    payload["fps"] = round(self.fps, 2)
                    payload["infer_ms"] = round(self.infer_ms, 1)
                    self.latest = payload

            self.latest["label_age_ms"] = (
                int((time.time() - self._label_ts) * 1000) if self._label_ts else -1)
            self.latest["behavior_hz"] = round(1.0 / BEHAVIOR_DT, 1)
            return self.latest

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.latest)


W: IngestWorker | None = None


class Handler(BaseHTTPRequestHandler):
    server_version = "K1CamIngest/1.0"
    # 必须显式声明 HTTP/1.1：默认的 HTTP/1.0 每个请求结束就关连接，
    # 而 PC 到 K1 中间隔着 hdc fport + 3568 nc 中继 + SSH 三跳，
    # 新建一次连接要 120~380 ms（2026-11-05 实测），比推理本身还贵。
    # 保持长连接后同一帧的端到端从 271~542 ms 降到接近推理耗时。
    # 前提是所有响应都带 Content-Length —— 本文件的 _send 一直有，不会挂连接。
    protocol_version = "HTTP/1.1"

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

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n > 0 else b""

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/v1/vision/latest":
            snap = W.snapshot()
            return self._send(200 if snap.get("ok") else 503, snap)
        if path == "/v1/vision/frame.jpg":
            with W.lock:
                jpeg = W.frame_jpeg
            if not jpeg:
                return self._send(503, {"ok": False, "reason": "no_frame_yet"})
            return self._send(200, jpeg, "image/jpeg")
        if path == "/health":
            return self._send(200, {
                "ok": True,
                "frames": W.frames,
                "fps": round(W.fps, 2),
                "infer_ms": round(W.infer_ms, 1),
                "model": W.model_name,
                "providers": W.providers,
                "warm_ms": round(W.warm_ms, 1),
                "paused": W.paused,
                "source": "pc_push",
            })
        return self._send(404, {"ok": False, "reason": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/v1/vision/ingest":
            body = self._body()
            if len(body) < 100 or body[:2] != b"\xff\xd8":
                return self._send(400, {"ok": False, "reason": "expect_jpeg_body"})
            try:
                return self._send(200, W.ingest(body))
            except Exception as e:
                import traceback
                traceback.print_exc()
                return self._send(500, {"ok": False, "reason": f"infer_error: {e}"})
        if path == "/v1/vision/pause":
            W.paused = True
            W._paused_at = time.monotonic()   # 记起始时刻，超时自愈的基准
            return self._send(200, {"ok": True, "paused": True})
        if path == "/v1/vision/resume":
            W.paused = False
            W._paused_at = 0.0
            return self._send(200, {"ok": True, "paused": False})
        return self._send(404, {"ok": False, "reason": "not found"})


def main():
    global W
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("--model", default=str(here / "yolov8n.q.onnx"))
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=11436)
    ap.add_argument("--conf", type=float, default=None, help="覆盖检测置信度阈值")
    ap.add_argument("--behavior-hz", type=float, default=5.0,
                    help="行为判定频率（默认 5）。0.3~0.6 s 的跳跃在 2 Hz 下采不到，"
                         "特征窗口已按秒定义，提高此项不会改变判定标准")
    args = ap.parse_args()

    if args.behavior_hz <= 0:
        sys.exit("[FATAL] --behavior-hz 必须 > 0")

    global BEHAVIOR_DT
    BEHAVIOR_DT = 1.0 / args.behavior_hz

    if not Path(args.model).exists():
        sys.exit(f"[FATAL] 模型不存在：{args.model}")

    W = IngestWorker(args.model, args.conf)
    print("=" * 64)
    print(f"K1 接帧推理服务  http://{args.host}:{args.port}")
    print(f"  模型      : {W.model_name}")
    print(f"  Providers : {W.providers}")
    print(f"  预热      : {W.warm_ms:.0f} ms")
    print(f"  等帧方式  : PC 推 POST /v1/vision/ingest")
    print(f"  行为节奏  : {args.behavior_hz:.1f} Hz")
    print("=" * 64)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[svc] 停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
