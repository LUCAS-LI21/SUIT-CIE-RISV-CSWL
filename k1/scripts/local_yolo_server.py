#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地识别服务（在电脑上替代 K1，给 yolo-server/templates/index.html 供数）

为什么这样搭：
    项目的识别页面 `yolo-server/templates/index.html` 本来就是「浏览器抓帧
    → POST /analyze_frame → 后端返回 pets/history」的结构，跟板子在不在无关。
    所以这里不需要新写页面，只要在 localhost:5000 上实现它要的那几个接口，
    内部再调用 k1-vision/vision_service.py 的 letterbox / decode_yolov8 /
    MultiPetTracker —— 页面看到的结果就是板端那套算法的结果。

与板端的唯一差别：帧来自浏览器摄像头而不是 CSI，分析节奏由页面 POST 频率决定
（页面已改成 500 ms 一次 = 2 fps，与板端一致）。

启动：
    python scripts/local_yolo_server.py
    然后浏览器打开 http://localhost:5000

接口（与原版 Flask 版兼容）：
    GET  /               → 识别页面
    POST /analyze_frame  → multipart 字段 frame(jpeg) ；返回 pets + history
    POST /reset          → 清空跟踪器与历史
    POST /clear_history  → 只清历史
    GET  /health         → 健康检查
    POST /analyze_video  → 本版不支持，明确返回错误（页面会红字提示）
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
VISION_DIR = ROOT / "k1-vision"
PAGE = ROOT / "yolo-server" / "templates" / "index.html"
sys.path.insert(0, str(VISION_DIR))

try:
    import cv2
except ImportError:
    sys.exit("[FATAL] 缺 opencv：.venv-train/Scripts/python.exe -m pip install opencv-python")
import onnxruntime as ort  # noqa: E402

import vision_service as V  # noqa: E402

HOST = "127.0.0.1"
PORT = 5000

# ── 镜像模式的上游 ────────────────────────────────────────────────────
# 摄像头是**独占**设备。scripts/pc_cam_vision.py 一旦跑起来就占住了它，
# 浏览器再调 getUserMedia 必然失败（NotReadableError / TrackStartError），
# 这不是页面写错了，是硬件只有一路。
#
# 但「占着摄像头」不等于「页面就没法看」——上游已经在做推理了，页面直接
# 拿它的画面和结果就行，不用再抓一次、也不用再推理一次（重复推理还会让
# 两套 tracker 给出互相矛盾的行为标签）。
#
#   GET /live.mjpg    → MJPEG 流，直接转发上游的 /v1/vision/frame.jpg
#   GET /api/live     → 把上游 /v1/vision/latest 转成 /analyze_frame 的返回结构
#   GET /api/live/health → 探测上游在不在，页面据此决定用镜像还是自己抓帧
UPSTREAM = os.environ.get("PET_VISION_UPSTREAM", "http://127.0.0.1:11436")
# 本机 shell 常带 http_proxy（沙箱代理），不显式清空会把 127.0.0.1 的请求
# 也发给代理，结果连接超时且看不出原因。
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# ── 全局状态：单进程单模型，所有请求串行进锁 ──────────────────────────────
_lock = threading.Lock()
_tracker = V.MultiPetTracker()
_human = V.HumanWatcher()
_history: "OrderedDict[int, dict]" = OrderedDict()
_session = None
_input_name = None
_stats = {"frames": 0, "infer_ms": 0.0, "last_ts": 0.0}
MAX_HISTORY = 30


# ══════════════════════════ 历史记账 ══════════════════════════

def _update_history(results, wall_now):
    """把本帧结果写进历史。用 wall time（页面要显示 HH:MM:SS），
    与 tracker 的 monotonic 时间分开记，互不干扰。"""
    seen = set()
    for r in results:
        pid = r["pet_id"]
        seen.add(pid)
        e = _history.get(pid)
        if e is None:
            _history[pid] = {
                "id": pid,
                "type": r["pet"],
                "behavior": r["label"],
                "start_time": wall_now,
                "last_seen": wall_now,
                "samples": 1,
                "confidence": r["action_conf"],
                "status": "active",
                "end_time": None,
            }
        else:
            e["behavior"] = r["label"]
            e["last_seen"] = wall_now
            e["samples"] += 1
            e["confidence"] = max(e["confidence"], r["action_conf"])
            if e["status"] == "left":      # 走了又回来，续上
                e["status"] = "active"
                e["end_time"] = None
    for pid, e in _history.items():
        if pid not in seen and e["status"] == "active":
            e["status"] = "left"
            e["end_time"] = wall_now
    # 限制条数，避免长时间运行无限增长
    while len(_history) > MAX_HISTORY:
        _history.popitem(last=False)


def _fmt_ts(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else None


def _history_payload():
    out = []
    for e in _history.values():
        end = e["end_time"] or e["last_seen"]
        out.append({
            "id": e["id"],
            "type": e["type"],
            "behavior": e["behavior"],
            "start_time": _fmt_ts(e["start_time"]),
            "start_timestamp": e["start_time"],
            "last_seen": _fmt_ts(e["last_seen"]),
            "status": e["status"],
            "duration_sec": round(max(0.0, end - e["start_time"]), 1),
            "samples": e["samples"],
            "confidence": round(float(e["confidence"]), 3),
        })
    # 新的在前
    return list(reversed(out))


# ══════════════════════════ 单帧分析 ══════════════════════════

def analyze_jpeg(jpeg_bytes):
    frame = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return {"status": "error", "error": "图片解码失败"}

    t0 = time.perf_counter()
    canvas, scale, pad_w, pad_h = V.letterbox(frame)
    blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    outs = _session.run(None, {_input_name: blob})
    h, w = frame.shape[:2]
    dets = V.decode_yolov8(outs[0], scale, pad_w, pad_h, w, h)

    mono = time.monotonic()
    results = _tracker.update_or_create(mono, dets, w, h, frame)  # 必须传原帧，见 vision_service:901
    _tracker.cleanup(mono)
    _human.update(dets, mono)
    human_snap = _human.snapshot(mono)

    infer_ms = (time.perf_counter() - t0) * 1000.0
    _stats["frames"] += 1
    _stats["infer_ms"] = _stats["infer_ms"] * 0.8 + infer_ms * 0.2
    _stats["last_ts"] = time.time()

    wall_now = time.time()
    _update_history(results, wall_now)

    pets = [{
        "id": r["pet_id"],
        "type": r["pet"],
        "behavior": r["label"],
        "confidence": r["action_conf"],
        "det_confidence": r["conf"],
        "bbox": r["bbox"],
        "speed": r["speed_px_s"],
        "basis": r["basis"],
        "duration_sec": r["duration_sec"],
    } for r in results]

    return {
        "status": "success",
        "timestamp": time.strftime("%H:%M:%S"),
        "pet_count": len(pets),
        "pets": pets,
        "history": _history_payload(),
        "human": human_snap,
        "inference_ms": round(infer_ms, 1),
        "frame_size": [w, h],
    }


# ══════════════════════════ 镜像上游（不抢摄像头） ══════════════════════════

def _upstream_get(path, timeout=3.0):
    url = UPSTREAM.rstrip("/") + path
    with _opener.open(url, timeout=timeout) as r:
        return r.read()


def _upstream_json(path, timeout=3.0):
    try:
        return json.loads(_upstream_get(path, timeout).decode("utf-8"))
    except Exception:
        return None


def upstream_health():
    """探测上游。返回 dict 或 None。"""
    snap = _upstream_json("/health", timeout=1.2)
    if not snap or not snap.get("ok"):
        return None
    return {
        "ok": True,
        "upstream": UPSTREAM,
        "det_fps": snap.get("det_fps"),
        "infer_ms": snap.get("infer_ms"),
        "model": snap.get("model"),
    }


def analyze_live():
    """把上游的 /v1/vision/latest 转成页面认识的 /analyze_frame 结构。

    为什么能直接复用 _update_history：
        上游 pc_cam_vision 用的就是 vision_service 里的同一个 MultiPetTracker，
        所以 latest 里的 all[...] 与本地 analyze_jpeg 的 results 字段完全一致
        （pet_id / pet / label / action_conf / conf / bbox / speed_px_s / basis）。
        历史记录因此不需要另写一套。
    """
    snap = _upstream_json("/v1/vision/latest")
    if not snap:
        return {"status": "error", "error": f"上游不可达：{UPSTREAM}"}
    if not snap.get("ok"):
        return {"status": "error", "error": f"上游未就绪：{snap.get('reason') or 'warming'}"}

    results = snap.get("all") or []
    wall_now = time.time()
    with _lock:
        _update_history(results, wall_now)
        hist = _history_payload()

    pets = [{
        "id": r.get("pet_id", 0),
        "type": r.get("pet", ""),
        "behavior": r.get("label", "unknown"),
        "confidence": r.get("action_conf", 0.0),
        "det_confidence": r.get("conf", 0.0),
        "bbox": r.get("bbox", []),
        "speed": r.get("speed_px_s", 0.0),
        "basis": r.get("basis", ""),
        "duration_sec": r.get("duration_sec", 0.0),
    } for r in results]

    return {
        "status": "success",
        "timestamp": time.strftime("%H:%M:%S"),
        "pet_count": len(pets),
        "pets": pets,
        "history": hist,
        "human": snap.get("human"),
        "inference_ms": round(float(snap.get("infer_ms") or 0.0), 1),
        "source": "mirror",
    }


# ══════════════════════════ HTTP ══════════════════════════

class ExclusiveServer(ThreadingHTTPServer):
    """禁止端口复用（Windows 上 SO_REUSEADDR 会让两个进程同时绑同一端口，
    第二个静悄悄跑起来，流量被分走 → 页面帧率看着掉一半）。"""

    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalPetYolo/1.0"

    def log_message(self, fmt, *args):
        # 页面每 0.5 秒一个请求，默认日志会刷屏，只留错误
        if args and str(args[0]).startswith(("4", "5")):
            sys.stderr.write("[http] %s\n" % (fmt % args))

    # ── 工具 ────────────────────────────────────────────────────
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            # 404 分支传的是 str，直接 write 会 TypeError（浏览器请求 favicon.ico 必踩）
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, obj)

    def _mjpeg(self):
        """转发上游的 JPEG 作为 MJPEG 流。

        为什么不让页面 <img> 定时刷新 frame.jpg：
            那样每帧一个完整 HTTP 请求，连接建立/关闭的开销比图片本身还大，
            而且画面会一顿一顿。multipart/x-mixed-replace 是一条长连接，
            浏览器自己按到达顺序渲染，最省。
        """
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            idle = 0
            while True:
                try:
                    jpeg = _upstream_get("/v1/vision/frame.jpg", timeout=2.0)
                except Exception:
                    jpeg = b""
                if not jpeg:
                    idle += 1
                    if idle > 120:      # 上游连续 12 秒无帧，断开让页面自己重连
                        break
                    time.sleep(0.1)
                    continue
                idle = 0
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(("Content-Length: %d\r\n\r\n" % len(jpeg)).encode())
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                # 上游出帧约 20~27 fps，这里压到 ~15 就够看，省带宽也省 CPU
                time.sleep(0.06)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass   # 用户切页签/关窗口，正常断开

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n > 0 else b""

    def _extract_frame(self, body, ctype):
        """从 multipart 里取出 frame 文件字节。页面用 FormData 上传，字段名 frame。"""
        m = re.search(r'boundary=([^;]+)', ctype or "")
        if m:
            boundary = m.group(1).strip().strip('"').encode()
            sep = b"--" + boundary
            for part in body.split(sep):
                if b'name="frame"' not in part:
                    continue
                i = part.find(b"\r\n\r\n")
                if i < 0:
                    continue
                data = part[i + 4:]
                if data.endswith(b"\r\n"):
                    data = data[:-2]
                return data
        # 兜底：直接把整个 body 当 jpeg
        return body if body[:2] == b"\xff\xd8" else None

    # ── 路由 ────────────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            if not PAGE.exists():
                return self._send(500, f"页面不存在：{PAGE}", "text/plain; charset=utf-8")
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/health":
            return self._json({
                "status": "ok",
                "frames": _stats["frames"],
                "infer_ms": round(_stats["infer_ms"], 1),
                "trackers": len(_tracker.trackers),
                "history": len(_history),
                "model": MODEL_NAME,
            })
        if path == "/history":
            return self._json({"history": _history_payload()})
        if path == "/api/live/health":
            h = upstream_health()
            return self._json(h or {"ok": False, "upstream": UPSTREAM}, 200 if h else 503)
        if path == "/api/live":
            # 注意别在外面再套 with _lock：analyze_live 内部已经加锁，
            # 而 _lock 是不可重入的 threading.Lock，套两层会直接死锁。
            try:
                return self._json(analyze_live())
            except Exception as e:
                import traceback
                traceback.print_exc()
                return self._json({"status": "error", "error": f"镜像失败: {e}"}, 500)
        if path == "/live.mjpg":
            return self._mjpeg()
        if path == "/analyze":
            # 兼容老页面的轮询写法：没有新帧就回最近一次状态
            return self._json({
                "status": "success",
                "timestamp": time.strftime("%H:%M:%S"),
                "pets": [],
                "history": _history_payload(),
                "inference_ms": round(_stats["infer_ms"], 1),
            })
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        ctype = self.headers.get("Content-Type", "")

        if path == "/analyze_frame":
            jpeg = self._extract_frame(body, ctype)
            if not jpeg:
                return self._json({"status": "error", "error": "未收到帧图片（字段名应为 frame）"}, 400)
            with _lock:
                try:
                    _ensure_model()
                    return self._json(analyze_jpeg(jpeg))
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    return self._json({"status": "error", "error": f"分析失败: {e}"}, 500)

        if path == "/reset":
            with _lock:
                global _tracker, _history
                _tracker = V.MultiPetTracker()
                _history.clear()
                _stats.update({"frames": 0, "infer_ms": 0.0})
            return self._json({"status": "success", "message": "已重置"})

        if path == "/clear_history":
            with _lock:
                _history.clear()
            return self._json({"status": "success", "message": "历史已清空"})

        if path == "/analyze_video":
            return self._json({
                "status": "error",
                "error": "本机验证版只支持摄像头模式；要验视频请用 scripts/local_cam_yolo.py --video <文件>",
            }, 200)

        return self._send(404, "not found", "text/plain")


MODEL_NAME = ""
_MODEL_PATH = None


def _ensure_model():
    """按需加载模型。

    镜像模式下页面根本不 POST 帧（上游已经算过了），所以没必要一启动就把
    onnxruntime 和权重塞进内存 —— 这台机器同时跑着模拟器，提交内存本来就紧张，
    白占几百 MB 会直接导致别的进程构建/签名失败。
    """
    global _session, _input_name, MODEL_NAME
    if _session is not None:
        return
    if _MODEL_PATH is None or not _MODEL_PATH.exists():
        raise RuntimeError(f"模型不存在：{_MODEL_PATH}")

    providers = ort.get_available_providers()
    prov = "CUDAExecutionProvider" if "CUDAExecutionProvider" in providers else "CPUExecutionProvider"

    # 线程数必须显式设。默认会按逻辑核数（本机 20）开线程，
    # 对 yolov8n 这种小模型是纯负优化：实测 fp32 单帧 20 线程 600+ ms，
    # 4 线程只要 63 ms —— 线程争抢比并行收益大得多。
    so = ort.SessionOptions()
    so.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 4)))
    so.inter_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    _session = ort.InferenceSession(str(_MODEL_PATH), so, providers=[prov])
    _input_name = _session.get_inputs()[0].name
    MODEL_NAME = _MODEL_PATH.name

    # 预热：首帧会慢 3~5 倍，先跑一次把图优化掉
    dummy = np.zeros((1, 3, V.IMG_SIZE, V.IMG_SIZE), dtype=np.float32)
    t0 = time.perf_counter()
    _session.run(None, {_input_name: dummy})
    print(f"[model] {MODEL_NAME} 预热 {(time.perf_counter()-t0)*1000:.0f} ms  {prov}")


def main():
    global _MODEL_PATH, UPSTREAM

    model = ROOT / "k1-vision" / "yolov8n.onnx"
    # 位置参数：模型路径（可选）；--upstream <url> 覆盖镜像上游
    argv = sys.argv[1:]
    if "--upstream" in argv:
        i = argv.index("--upstream")
        if i + 1 < len(argv):
            UPSTREAM = argv[i + 1]
            del argv[i:i + 2]
    if argv and not argv[0].startswith("-"):
        model = Path(argv[0])
    _MODEL_PATH = model

    print("=" * 62)
    print("本地识别服务  |  页面: yolo-server/templates/index.html")
    print("=" * 62)
    print(f"模型      : {model.name if model.exists() else '(缺失，镜像模式仍可用)'}  （按需加载）")
    print(f"镜像上游  : {UPSTREAM}")
    print(f"行为窗口  : HISTORY_SEC={V.HISTORY_SEC} s（按秒定义，与页面帧率解耦）")
    print(f"监听      : http://{HOST}:{PORT}")
    print("-" * 62)
    print("摄像头被 pc_cam_vision.py 占用时，页面会自动走镜像模式（不再抢设备）。")
    print("两者都没跑时才会退回浏览器自己抓帧。Ctrl+C 停止服务。")
    print("-" * 62)

    try:
        srv = ExclusiveServer((HOST, PORT), Handler)
    except OSError as e:
        sys.exit(f"[FATAL] {HOST}:{PORT} 已被占用（{e}）；先结束上一个实例")
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[svc] 停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
