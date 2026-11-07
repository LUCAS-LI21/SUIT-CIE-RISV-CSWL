#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K1 语音识别服务（ASR）—— SenseVoice-Small int8 + silero-VAD

定位：与 vision_service.py 同构的"最新状态缓存"服务。
      常驻监听麦克风，VAD 检测到一句话就识别一次，把结果缓存起来；
      App 轮询 /v1/asr/latest 拿到最新一句话。

为什么是"缓存最新值"而不是"请求-响应"：
  1. sherpa-onnx 是独立二进制，每次调用要重新加载 239 MB 模型，
     实测冷加载约 10~17 秒，做成请求-响应会让每次提问都等半分钟；
  2. 宠物监护是持续场景，用户说话不需要先点按钮，VAD 自动切句更自然；
  3. 与视觉服务的契约完全一致（/v1/vision/latest 也是拉缓存），
     App 侧两套代码可以共用同一套轮询与熔断逻辑。

实测数据（2026-11-02，MUSE Pi Pro / Bianbu）：
  - 模型：sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17 / model.int8.onnx (239 MB)
  - zh.wav（5.592 s）识别耗时 5.375 s，RTF 0.961
  - 识别结果："开饭时间早上9点至下午5点。"（含 ITN，数字已归一化）
  - 期间 vision_service 同时在跑，无 TCM 冲突（ASR 走 CPU provider，不申请 TCM）

⚠️ 与 CV 的关键差异：ASR 必须用 --provider=cpu。
   SpaceMIT EP 对 CV 模型有加速，但 SenseVoice 的算子不被 EP 完整支持，
   且 EP 会申请 /dev/tcm —— 而 TCM 是独占的，与视觉服务共存时会直接崩。
   走 CPU 的代价是 RTF≈0.96（接近实时），对监护场景完全够用。

用法：
    python3 asr_service.py --port 11437
    python3 asr_service.py --device plughw:0,0 --lang zh --threads 4
"""

import argparse
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ────────────────────────── 路径常量 ──────────────────────────
ASR_ROOT = "/root/asr"
SHERPA_BIN = os.path.join(
    ASR_ROOT, "sherpa-onnx-v2.0.1-linux-riscv64-spacemit-shared/bin")
VAD_ASR = os.path.join(SHERPA_BIN, "sherpa-onnx-vad-alsa-offline-asr")
OFFLINE_ASR = os.path.join(SHERPA_BIN, "sherpa-onnx-offline")
MODEL_DIR = os.path.join(
    ASR_ROOT, "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17")
VAD_MODEL = os.path.join(ASR_ROOT, "silero_vad.onnx")

# 日志里提取识别结果。sherpa 的示例程序在不同版本里输出过三种格式：
#   {"text": "...", "lang": ...}      ← AsJsonString()
#   text: ...                          ← 纯文本打印
#   0: 所以麦克风到底能不能用？          ← sherpa-onnx-vad-alsa-offline-asr 实际格式
# 三种都兼容，避免升级 sherpa 后解析静默失效。
#
# ⚠️ 2026-11-02 实测教训：最初只写了前两种，导致 vad-alsa 工具的结果
#    100% 被丢弃（结果只出现在 tail 里，utterances 恒为 0）。
#    第 3 种「序号: 文本」才是本工具的主格式，必须优先匹配。
RE_JSON_TEXT = re.compile(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"')
RE_PLAIN_TEXT = re.compile(r'^\s*text\s*:\s*(.+?)\s*$', re.M)
RE_SEQ_TEXT = re.compile(r'^\s*(\d+):\s*(\S.*)$')

# 配置回显行的特征：含 "Config(" 或大量 "="。这类行绝不能被当成识别结果。
def _looks_like_config(line):
    return "Config(" in line or line.count("=") >= 3


class AsrWorker:
    """常驻 VAD+ASR 子进程，并把输出解析成最新结果。"""

    def __init__(self, device, lang="zh", threads=4, itn=True):
        self.device = device
        self.lang = lang
        self.threads = threads
        self.itn = itn
        self.proc = None
        self.lock = threading.Lock()
        self.latest = {"ok": False, "reason": "starting", "text": ""}
        self.utterances = 0          # 累计识别句数
        self.ready = False
        self.reason = "starting"
        self.tail = []               # 最近若干行原始输出，供排查
        self._stop = False

    def build_cmd(self):
        return [
            VAD_ASR,
            f"--silero-vad-model={VAD_MODEL}",
            f"--tokens={os.path.join(MODEL_DIR, 'tokens.txt')}",
            f"--sense-voice-model={os.path.join(MODEL_DIR, 'model.int8.onnx')}",
            f"--sense-voice-language={self.lang}",
            "--sense-voice-use-itn=true" if self.itn else "--sense-voice-use-itn=false",
            f"--num-threads={self.threads}",
            "--provider=cpu",       # 必须 CPU：EP 抢 TCM 会与视觉服务冲突
            self.device,
        ]

    def _publish(self, text, source="vad"):
        text = text.strip()
        if not text:
            return
        with self.lock:
            self.utterances += 1
            seq = self.utterances
            self.latest = {
                "ok": True,
                "text": text,
                "seq": seq,
                "ts": time.time(),
                "lang": self.lang,
                "model": "sense-voice-small-int8",
                "source": source,
                "duration_ms": 0,
            }
        print(f"[asr] #{seq} {text}", flush=True)

    def run(self):
        if not os.path.exists(VAD_ASR):
            self.reason = f"找不到识别程序: {VAD_ASR}"
            with self.lock:
                self.latest = {"ok": False, "reason": self.reason, "text": ""}
            return
        if not os.path.exists(VAD_MODEL):
            self.reason = f"找不到 VAD 模型: {VAD_MODEL}"
            with self.lock:
                self.latest = {"ok": False, "reason": self.reason, "text": ""}
            return

        cmd = self.build_cmd()
        print("[asr] 启动:", " ".join(cmd), flush=True)

        while not self._stop:
            try:
                self.proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    bufsize=1, universal_newlines=True, errors="replace")
            except Exception as e:
                self.reason = f"启动失败: {e}"
                time.sleep(3)
                continue

            # 读到 "Recognizer created" 才算真正就绪（模型加载完成）
            for line in self.proc.stdout:
                if self._stop:
                    break
                line = line.rstrip("\n")
                self.tail.append(line)
                if len(self.tail) > 200:
                    del self.tail[:-200]

                if "Recognizer created" in line:
                    self.ready = True
                    self.reason = ""
                    print("[asr] 模型就绪", flush=True)
                    continue

                if _looks_like_config(line):
                    continue

                # 格式 3（本工具主格式）: " 0: 所以麦克风到底能不能用？"
                m_seq = RE_SEQ_TEXT.match(line)
                if m_seq:
                    self._publish(m_seq.group(2))
                    continue

                # 格式 1: {"text": "..."}
                m_json = RE_JSON_TEXT.search(line)
                if m_json:
                    raw = m_json.group(1)
                    try:
                        raw = json.loads('"' + raw + '"')   # 还原 \uXXXX 转义
                    except Exception:
                        pass
                    self._publish(raw)
                    continue

                # 格式 2: "text: ..."
                m_plain = RE_PLAIN_TEXT.search(line)
                if m_plain:
                    self._publish(m_plain.group(1))

            rc = self.proc.wait()
            self.ready = False
            if self._stop:
                break
            self.reason = f"识别进程退出 (rc={rc})，3 秒后重启"
            print(f"[asr] {self.reason}", flush=True)
            time.sleep(3)

    def stop(self):
        self._stop = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()

    def snapshot(self):
        with self.lock:
            data = dict(self.latest)
            data["utterances"] = self.utterances
            # age_sec 让 App 能判断"这条是不是刚说的"
            if data.get("ts"):
                data["age_sec"] = round(time.time() - data["ts"], 1)
            return data


class Handler(BaseHTTPRequestHandler):
    worker = None

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/asr/latest"):
            data = self.worker.snapshot()
            self._send(200 if data.get("ok") else 503, data)
        elif self.path.startswith("/health"):
            self._send(200, {
                "ok": self.worker.ready,
                "device": self.worker.device,
                "utterances": self.worker.utterances,
                "reason": self.worker.reason,
                "tail": self.worker.tail[-6:],
            })
        else:
            self._send(404, {"ok": False, "reason": "not found"})

    def log_message(self, fmt, *args):
        return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="plughw:0,0",
                    help="ALSA 采集设备（USB 麦克风一般是 plughw:0,0）")
    ap.add_argument("--lang", default="zh", help="zh / en / ja / ko / yue / auto")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--port", type=int, default=11437)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    worker = AsrWorker(args.device, args.lang, args.threads)
    Handler.worker = worker
    threading.Thread(target=worker.run, daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[http] listening on {args.host}:{args.port}")
    print("[http] GET /v1/asr/latest   GET /health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[exit] stopping")
        worker.stop()
        server.shutdown()


if __name__ == "__main__":
    main()
