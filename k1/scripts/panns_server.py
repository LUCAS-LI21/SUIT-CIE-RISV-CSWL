"""PANNs 声音事件分类服务（PC 侧，默认 8802）。

为什么模型不放在 K1：Cnn14 在 K1（RISC-V，无向量加速）上 8.5 秒/clip，
在 PC 上 21 ms/clip —— 差 400 倍。所以分工是：
    K1 负责采集 + VAD 切段（本地、轻量）
    PC 负责 PANNs 推理（重、但有算力）
    拿不到结果时 K1 回退规则分类器，界面如实标来源

接口：
    GET  /health                → 服务与模型状态
    POST /classify              → body = raw int16 LE mono 16k PCM，返回分类
    POST /classify_wav          → body = wav 文件字节（调试用）
返回：
    {"ok":1,"cls":"bark","conf":0.51,"source":"panns","ms":21,
     "top":[["Animal",0.60],["Dog",0.51]],"scores":{"bark":0.51,...}}

前处理的关键结论（scripts/panns_pad_bench.py 实测）：短促叫声必须 **tile**
铺满 10 秒，不能补零。补零会让 Dog 从 0.51 掉到 0.00 —— PANNs 的 log-mel
被大片静音帧稀释后，动物类特征直接消失。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import onnxruntime as ort

SR = 16000
CLIP_SEC = 10.0

# PANNs AudioSet 527 类 → 本项目的 7 类宠物叫声。
# 每类取成员的**最大分**（多标签 sigmoid，不是互斥概率，求和会虚高）
GROUPS = {
    "bark":   [75, 76, 74, 122],      # Bark / Yip / Dog / Canidae
    "growl":  [79, 39],               # Growling / Grunt
    "howl":   [77],                   # Howl
    "meow":   [83, 85, 81],           # Meow / Caterwaul / Cat
    "purr":   [82],                   # Purr
    "hiss":   [84],                   # Hiss
    "scream": [14, 80],               # Screaming / Whimper (dog)
}
HUMAN = [0, 1, 2, 3, 7, 70]
SILENCE = [500]
NOISE = [513, 514, 520, 521, 285, 327]

PET_THRESHOLD = 0.20   # 低于这个分数不认宠物类，交给 human/silence/noise 判
DIAG_KEYS = ("Dog", "Bark", "Howl", "Growling", "Cat", "Meow", "Purr", "Hiss",
             "Animal", "Speech", "Silence", "Music")


class Panns:
    def __init__(self, model_dir: str):
        self.dir = model_dir
        self.rows = list(csv.DictReader(
            open(os.path.join(model_dir, "labels.csv"), encoding="utf-8")))
        self.rows.sort(key=lambda r: int(r["index"]))
        self.names = [r["display_name"] for r in self.rows]
        self.kd = {n: i for i, n in enumerate(self.names)}
        t = time.time()
        self.sess = ort.InferenceSession(
            os.path.join(model_dir, "Cnn14_16k.onnx"),
            providers=["CPUExecutionProvider"])
        self.load_ms = (time.time() - t) * 1000
        self.calls = 0
        self.last_ms = 0.0

    # ---- 前处理 ----
    @staticmethod
    def pcm16_to_float(buf: bytes) -> np.ndarray:
        return np.frombuffer(buf, dtype="<i2").astype("float32") / 32768.0

    @staticmethod
    def tile_to(x: np.ndarray, dur: float = CLIP_SEC) -> np.ndarray:
        need = int(SR * dur)
        if not len(x):
            return np.zeros(need, "float32")
        if len(x) < need:
            x = np.tile(x, int(np.ceil(need / len(x))))
        return x[:need].astype("float32")

    # ---- 推理 + 映射 ----
    def classify(self, pcm: np.ndarray) -> dict:
        t = time.time()
        sc = self.sess.run(None, {"input_audio": self.tile_to(pcm)[None, :]})[0][0]
        ms = (time.time() - t) * 1000
        self.calls += 1
        self.last_ms = ms

        pet = {g: max(float(sc[i]) for i in idx if i < len(sc))
               for g, idx in GROUPS.items()}
        cls, conf = max(pet.items(), key=lambda kv: kv[1])

        if conf < PET_THRESHOLD:
            human = max(float(sc[i]) for i in HUMAN if i < len(sc))
            sil = max(float(sc[i]) for i in SILENCE if i < len(sc))
            noi = max(float(sc[i]) for i in NOISE if i < len(sc))
            alt = max((("human", human), ("silence", sil), ("noise", noi)),
                      key=lambda kv: kv[1])
            cls, conf = alt

        top = np.argsort(-sc)[:3]
        diag = {k: round(float(sc[self.kd[k]]), 3) for k in DIAG_KEYS if k in self.kd}
        return {
            "ok": 1, "cls": cls, "conf": round(conf, 3), "source": "panns",
            "ms": round(ms, 1),
            "top": [[self.names[i], round(float(sc[i]), 3)] for i in top],
            "scores": {g: round(v, 3) for g, v in pet.items()},
            "diag": diag,
        }


P: Panns | None = None


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, fmt, *a):        # 压掉每帧访问日志
        pass

    def do_GET(self):
        if self.path.startswith("/health"):
            return self._json({
                "ok": 1, "service": "panns", "model": "Cnn14_16k (AudioSet 527)",
                "device": "cpu", "load_ms": round(P.load_ms, 0),
                "calls": P.calls, "last_ms": round(P.last_ms, 1),
                "pet_threshold": PET_THRESHOLD,
                "pad": "tile-10s",
            })
        self._json({"ok": 0, "err": "GET /health | POST /classify"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        try:
            if self.path.startswith("/classify_wav"):
                with wave.open(io.BytesIO(body), "rb") as w:
                    sr, ch = w.getframerate(), w.getnchannels()
                    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
                x = x.astype("float32") / 32768.0
                if ch > 1:
                    x = x.reshape(-1, ch).mean(1)
                if sr != SR:
                    m = int(round(len(x) * SR / sr))
                    x = np.interp(np.linspace(0, len(x) - 1, m),
                                  np.arange(len(x)), x).astype("float32")
            elif self.path.startswith("/classify"):
                x = P.pcm16_to_float(body)
            else:
                return self._json({"ok": 0, "err": "unknown path"}, 404)
            return self._json(P.classify(x))
        except Exception as e:                                    # noqa: BLE001
            return self._json({"ok": 0, "err": f"{type(e).__name__}: {e}"}, 500)


def main() -> int:
    global P
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8802)
    ap.add_argument("--model-dir", default="models/panns")
    a = ap.parse_args()

    P = Panns(a.model_dir)
    print(f"[panns] 模型已加载 {P.load_ms:.0f} ms  {len(P.names)} 类  "
          f"{P.sess.get_providers()}", flush=True)
    srv = ThreadingHTTPServer((a.host, a.port), H)
    print(f"[panns] 监听 http://{a.host}:{a.port}  前处理=tile {CLIP_SEC:.0f}s  "
          f"宠物阈值={PET_THRESHOLD}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
