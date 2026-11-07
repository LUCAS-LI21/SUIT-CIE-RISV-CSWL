#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PANNs 轻量模型侧车服务（MobileNetV2 int8，HTTP 11442）。

═══ 为什么必须是一个独立进程 ═══

int8 量化 ONNX 在 K1 的 cpu4-7（cluster1）上 **一进推理就 SIGILL**：
    $ taskset -c 4-7 python3 x.py
    step2 session ok 937 ms      ← session 创建正常
    （执行 run() 后进程直接消失，dmesg cause=2 = illegal instruction）

而 petvoice_service 常驻 cpu4-7（纯 Python，不抢视觉的 AI 核）。两者放一起
必然撞车：要么 petvoice 一起搬到 0-3 抢 AI 核，要么把推理拆出去。这里选后者。

所以拓扑是：
    petvoice_service (cpu4-7) --HTTP--> panns_sidecar (cpu0-3) --> int8 ONNX

代价是推理那 ~0.5 s 占用 AI 核；收益是 sidecar 即使崩掉，11439 的规则分类
照常工作，只丢"模型确认"这一层，不会整条叫声链路挂掉。

═══ 输入约定（与导出时的 32 kHz 配置绑定，不能改）═══

    麦克风 16 kHz → 上采样 2 倍 → 取最响 2.0 s（64000 样本）→ int8 ONNX

官方 MobileNet 权重只有 32 kHz 配置（n_fft=1024 / hop=320 / fmax=14000）。
试过改 16 kHz 前端（n_fft=512 / fmax=8000）省掉重采样，宠物类分数全掉到
0.15 以下，不可行。16k→32k 用线性插值：原信号 8 kHz 以上本来就是空的，
插值不会伪造内容，实测与 scipy resample_poly 结果一致（Dog 0.35 vs 0.36）。

═══ 接口 ═══

  GET  /health                 → {ok, model, load_ms, calls, last_ms, cores}
  POST /classify?sr=16000      body = raw int16 LE PCM（单声道）
                               → {ok, cls, cls_cn, conf, groups, ms}

用法：
    taskset -c 0-3 python3 panns_sidecar.py --port 11442
"""
import argparse
import csv
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import numpy as np

SR_MIC = 16000
SR_MODEL = 32000            # 模型前端固定的采样率，不要改
WIN_SEC = 2.0               # 64000 样本。1 s 窗实测宠物类分数全 <0.15，不可用
SPAN_SEC = 0.5              # 取最响的 0.5 s 再平铺到 2 s，依据见 loudest_window
THREADS = 2                 # 实测 1 线程 661 ms / 2 线程 492 ms / 4 线程更差（争抢）

DEFAULT_MODEL = "/root/k1-ai/MobileNetV2_32k_int8.onnx"
DEFAULT_LABELS = "/root/k1-ai/labels.csv"

# AudioSet 527 类 → 本项目 7 类。多标签 sigmoid 输出，每组取成员**最大分**
# （求和会虚高：同一事件在 Dog/Bark/Canidae 三个近义标签上会同时给高分）。
# 索引已逐条核对过 labels.csv，不是凭印象写的。
PANNS_GROUPS = {
    "bark":   [75, 76, 74, 122],      # Bark / Yip / Dog / Canidae
    "growl":  [79, 39],               # Growling / Grunt
    "howl":   [77],                   # Howl
    "meow":   [83, 85, 81],           # Meow / Caterwaul / Cat
    "purr":   [82],                   # Purr
    "hiss":   [84],                   # Hiss
    "scream": [14, 80],               # Screaming / Whimper (dog)
}
# 宠物类判定门限。**这个值是按 MobileNetV2 重新标的，不能沿用 Cnn14 的 0.20。**
# 依据见 /root/k1-ai/panns_calib.py 的实测分布：真狗叫 bark 组最高 0.35、
# 底噪全组 <0.05。取 0.15 是两者之间留足裕量的位置。
PET_THRESHOLD = 0.15

CLS_CN = {"bark": "吠叫", "growl": "低吼", "howl": "长嚎", "meow": "喵叫",
          "purr": "呼噜", "hiss": "哈气", "scream": "尖叫",
          "noise": "环境噪声", "silence": "静音"}


def load_labels(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    rows.sort(key=lambda r: int(r["index"]))
    return [r["display_name"] for r in rows]


def resample(x, sr, target=SR_MODEL):
    """任意采样率 → 32 kHz。板端没有 scipy，用 np.interp。

    **别假定输入就是 16 kHz**：初版把 44.1 kHz 的测试音频当 16k 处理，
    等于慢放 2.75 倍，同一个模型在 PC 上判 Dog 0.36、板上判成 Music，
    差点被误判成"板端推理有问题"。
    """
    x = np.asarray(x, dtype=np.float32).ravel()
    if sr == target or x.size == 0:
        return x
    n_out = int(round(x.size * target / sr))
    idx = np.arange(n_out) * (sr / target)
    return np.interp(idx, np.arange(x.size), x).astype(np.float32)


def loudest_window(x, want, span_sec=None):
    """从上下文里挑「最响 span 秒」→ 平铺（tile）铺满 want 点。

    ═══ 这段逻辑有实测依据，不要凭直觉改 ═══

    模型的输入长度是固定的 2.0 s（64000 样本 @32 kHz），但一段上下文里真正
    的吠叫只占 0.3~1 s。怎么挑这 2 s，直接决定识别成败：

      策略                        真狗叫 bark 组最高分
      A 能量最大的 2 s 连续窗          0.186
      B 最响 0.5 s → 平铺到 2 s       **0.603**
      C 最响 0.5 s → 补零到 2 s        0.129

    A 失败的原因是**把一次短促吠叫切在窗边界上**，模型看到的是残缺波形；
    能量最大不等于模型认得好（同一段音频换到 1.5 s 起点就从 0.145 跳到 0.362，
    这种对起点的强敏感本身就是脆弱信号）。
    C 失败是因为补零给模型看了一堵能量墙，与训练分布不符。

    span_sec 默认 0.5：标定表里 0.4/0.5/0.6 分别为 0.553/0.603/0.603，
    0.5 与 0.6 并列最优，取小的那个（更短 = 更快，也更贴合单次吠叫的实际时长）。
    """
    span_sec = SPAN_SEC if span_sec is None else span_sec
    span = max(1, int(SR_MODEL * span_sec))
    x = np.asarray(x, dtype=np.float32).ravel()
    if x.size == 0:
        return np.zeros(want, dtype=np.float32)

    if x.size <= span:
        seg = x
    else:
        # 滑动能量必须用累积和，**不能用 np.convolve**：核长接近窗长时 convolve
        # 是 O(n·k)，一段 6 s 音频要跑 10^10 次乘加，进程会直接卡死。
        step = max(1, int(SR_MODEL * 0.05))          # 5 ms 精度
        c = np.concatenate(([0.0], np.cumsum(x.astype(np.float64) ** 2)))
        e = c[span:] - c[:-span]
        best = min(int(np.argmax(e[::step])) * step, x.size - span)
        seg = x[best:best + span]

    if len(seg) >= want:
        return seg[:want].astype(np.float32)
    return np.tile(seg, want // len(seg) + 1)[:want].astype(np.float32)


class PannsEngine:
    def __init__(self, model_path, labels_path, threads=THREADS):
        import onnxruntime as ort
        self.names = load_labels(labels_path)
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        t = time.time()
        self.sess = ort.InferenceSession(model_path, so,
                                         providers=["CPUExecutionProvider"])
        self.load_ms = (time.time() - t) * 1000
        self.model = os.path.basename(model_path)
        self.want = int(SR_MODEL * WIN_SEC)
        self.lock = threading.Lock()      # 串行化：并发 run 只会互相抢核
        self.calls = 0
        self.last_ms = 0.0
        # 预热一次。首次 run 要建内存池，实测比后续慢 2~3 倍，
        # 不预热的话服务刚起来的第一个请求会假性很慢。
        self.sess.run(None, {"input_audio": np.zeros((1, self.want), np.float32)})

    def classify(self, raw, sr, nch=1):
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if nch > 1 and x.size >= nch:
            x = x.reshape(-1, nch).mean(axis=1)
        x = resample(x, sr)
        seg = loudest_window(x, self.want)
        t = time.time()
        with self.lock:
            sc = self.sess.run(None, {"input_audio": seg[None, :]})[0][0]
        self.last_ms = (time.time() - t) * 1000
        self.calls += 1
        groups = {g: max(float(sc[i]) for i in idx if i < len(sc))
                  for g, idx in PANNS_GROUPS.items()}
        cls, conf = max(groups.items(), key=lambda kv: kv[1])
        if conf < PET_THRESHOLD:
            cls = "noise"
        return cls, conf, groups


def make_handler(eng):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):        # 默认日志会把 journal 刷满
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/health", "/"):
                self._send(200, {
                    "ok": True, "model": eng.model, "load_ms": round(eng.load_ms),
                    "calls": eng.calls, "last_ms": round(eng.last_ms, 1),
                    "win_sec": WIN_SEC, "sr_model": SR_MODEL,
                    "threads": THREADS, "threshold": PET_THRESHOLD,
                    "cores": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else [],
                })
            else:
                self._send(404, {"ok": False, "err": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            if u.path != "/classify":
                self._send(404, {"ok": False, "err": "not found"})
                return
            q = parse_qs(u.query)
            sr = int(q.get("sr", [SR_MIC])[0])
            nch = int(q.get("nch", ["1"])[0])
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b""
            if not raw:
                self._send(400, {"ok": False, "err": "empty body"})
                return
            try:
                cls, conf, groups = eng.classify(raw, sr, nch)
            except Exception as e:                       # noqa: BLE001
                self._send(500, {"ok": False, "err": str(e)[:200]})
                return
            self._send(200, {
                "ok": True, "cls": cls, "cls_cn": CLS_CN.get(cls, cls),
                "conf": round(conf, 4),
                "groups": {k: round(v, 4) for k, v in groups.items()},
                "ms": round(eng.last_ms, 1),
                "dur": round(len(raw) / 2 / max(sr, 1), 2),
            })

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11442)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--labels", default=DEFAULT_LABELS)
    args = ap.parse_args()

    eng = PannsEngine(args.model, args.labels)
    cores = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []
    print(f"[panns] {eng.model} 加载 {eng.load_ms:.0f} ms  "
          f"窗口 {WIN_SEC}s 阈值 {PET_THRESHOLD} cores={cores}", flush=True)
    if cores and min(cores) >= 4:
        print("[panns] 警告：当前绑在 cpu4-7，int8 推理会 SIGILL，必须 taskset -c 0-3",
              flush=True)
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(eng))
    print(f"[panns] listening on {args.host}:{args.port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
