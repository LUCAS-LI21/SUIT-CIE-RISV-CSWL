#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K1 LLM 网关（三通道仲裁）—— 端口 11438

为什么需要这一层：
    实测（2026-11-02，MUSE Pi Pro）在视觉服务同时推理时，
    qwen3:0.6b 的一次 20-token 请求从 3.3 秒劣化到 90 秒超时（直接 500）。
    把 ollama 绑到 cpu4-7、视觉绑到 cpu0-3 都试过：无效。
    暂停视觉再测：稳定 3.27 / 3.30 秒。
    → 瓶颈是内存带宽争抢，不是 CPU 核数，加核/绑核都救不了，
      唯一有效的手段是时间片。

本网关做的事：
    收到 /v1/chat/completions
      → POST vision:11436/v1/vision/pause     （视觉推理循环空转）
      → 转发到 ollama:11434
      → POST vision:11436/v1/vision/resume    （无论成功失败都恢复）
      → 原样返回 ollama 的响应
    
    视觉服务在空转期间仍然响应 /v1/vision/latest（返回最后一帧），
    所以 App 的视觉卡片不会掉到 503、不会触发熔断、不会显示"模拟"。
    —— 这是"仲裁"而不是"关闭"的关键区别。

不改的部分：
    非 /v1/chat/completions 的请求一律直接透传，不做仲裁。
    知识库 /ask、视觉 /v1/vision/latest 都不经过这里。

用法：
    python3 llm_gateway.py --port 11438
"""

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VISION_PAUSE = "http://127.0.0.1:11436/v1/vision/pause"
VISION_RESUME = "http://127.0.0.1:11436/v1/vision/resume"
OLLAMA = "http://127.0.0.1:11434"

# 视觉收到 pause 后不会立刻停：它要跑完手上这一帧（6 fps 时约 167 ms）。
# 只等 0.15 s 会让 LLM 的前几个 token 和这最后一帧撞在一起。
# 这个值是折中：再大就开始明显拉长首字延迟，再小则抢带宽。
PAUSE_SETTLE = 0.15

# 串行化：K1 只有 4 GB 内存，两个 LLM 请求并发会互相拖死。
# 这里用锁而不是队列：第二个请求等第一个结束，而不是被拒绝。
# 前端本来就有并发锁（请求中禁点），这层是兜底。
_arb_lock = threading.Lock()

STATS = {"requests": 0, "vision_paused": 0, "errors": 0,
         "first_token_ms": 0, "last_total_ms": 0}


def _post(url, timeout=3.0):
    """内部调用：只关心是否送达，失败不抛，返回 bool。"""
    try:
        req = urllib.request.Request(url, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception as e:
        print(f"[gate] {url} 失败: {e}", flush=True)
        return False


def _pause_vision():
    ok = _post(VISION_PAUSE)
    if ok:
        STATS["vision_paused"] += 1
    return ok


def _resume_vision():
    _post(VISION_RESUME)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream = OLLAMA
    # 必须小于前端 K1Config.LLM_TIMEOUT（50s）。
    # 原来这里是 130，而前端 45s 就放弃了：前端走人、网关还在等上游，
    # 视觉循环就一直停在 pause 状态，最长连冻 130 秒——期间 11436 不出新帧，
    # 前端看到的是「数据过期」。现在网关先失败先 resume，前端拿到的是一条
    # 明确的 502，而不是一个冻住的摄像头。
    llm_timeout = 40

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy_stream(self, body, t0):
        """把上游的 SSE 边到边转发。

        为什么必须做这一层：原来的 _proxy 用 r.read() 一次性读完再回给前端，
        于是「首字延迟」永远等于「整句延迟」——实测 1358 ms 的首字和整句一模一样。
        用户按下发送后要盯着空屏幕等 1.3 秒才看到第一个字，这就是体感上的卡。
        改成流式之后，第一个 token 通常在 400~600 ms 就到界面上，整句仍在
        1.3 秒左右结束，但「有反应」的时刻提前了一大半。

        用 chunked 编码而不是 Content-Length：SSE 的长度事先不知道，
        而 HTTP/1.1 下不给长度就必须 chunked，否则客户端会读到连接关闭。
        """
        url = self.upstream + self.path
        headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        resp = urllib.request.urlopen(req, timeout=self.llm_timeout)
        first_ms = 0
        try:
            self.send_response(resp.status)
            self.send_header("Content-Type",
                             resp.headers.get("Content-Type", "text/event-stream"))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            for raw in resp:  # 逐行读，SSE 的每一行都是一条事件
                if not raw:
                    continue
                if not first_ms:
                    first_ms = (time.monotonic() - t0) * 1000
                    STATS["first_token_ms"] = int(first_ms)
                self.wfile.write(f"{len(raw):x}\r\n".encode())
                self.wfile.write(raw)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return True, first_ms
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def _proxy(self, method, body, arbitrate):
        url = self.upstream + self.path
        headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
        req = urllib.request.Request(url, data=body if method != "GET" else None,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.llm_timeout) as r:
                data = r.read()
                self._send(r.status, data,
                           r.headers.get("Content-Type", "application/json"))
                return True
        except urllib.error.HTTPError as e:
            data = e.read()
            self._send(e.code, data,
                       e.headers.get("Content-Type", "application/json"))
            return False
        except Exception as e:
            STATS["errors"] += 1
            print(f"[gate] 转发失败 {self.path}: {e}", flush=True)
            self._send(502, json.dumps(
                {"ok": False, "reason": f"upstream unreachable: {e}"},
                ensure_ascii=False).encode("utf-8"))
            return False

    def do_POST(self):
        body = self._read_body()
        is_llm = self.path.startswith("/v1/chat/completions")
        if not is_llm:
            self._proxy("POST", body, arbitrate=False)
            return

        # 前端传 stream:true 才走转发流。默认 False 时保持原来的整包语义，
        # 老调用方（不带这个字段）行为完全不变。
        want_stream = False
        try:
            if body:
                want_stream = bool(json.loads(body).get("stream"))
        except Exception:
            want_stream = False

        STATS["requests"] += 1
        t0 = time.monotonic()
        with _arb_lock:
            paused = _pause_vision()
            # 给视觉循环一个让出的机会：它是 6 fps，一帧 167 ms，
            # 不 sleep 的话新请求会和最后一帧推理重叠，带宽还是被抢。
            if paused:
                time.sleep(PAUSE_SETTLE)
            try:
                if want_stream:
                    self._proxy_stream(body, t0)
                else:
                    self._proxy("POST", body, arbitrate=True)
            except urllib.error.HTTPError as e:
                self._send(e.code, e.read(),
                           e.headers.get("Content-Type", "application/json"))
            except Exception as e:
                STATS["errors"] += 1
                print(f"[gate] 转发失败 {self.path}: {e}", flush=True)
                try:
                    self._send(502, json.dumps(
                        {"ok": False, "reason": f"upstream unreachable: {e}"},
                        ensure_ascii=False).encode("utf-8"))
                except Exception:
                    pass  # 流式下响应头可能已经发出去了，此时只能断连接
            finally:
                if paused:
                    _resume_vision()
        dt = (time.monotonic() - t0) * 1000
        STATS["last_total_ms"] = int(dt)
        print(f"[gate] LLM {dt:.0f} ms "
              f"(vision_paused={paused} stream={want_stream} "
              f"first={STATS['first_token_ms']} ms)", flush=True)

    def do_GET(self):
        # /health 是前端 PetDataHub 用来确认「这个网关是谁」的探针。
        # 必须明确回 engine=k1：前端只有在拿到这个值时才敢在气泡下标
        # 「K1 本地模型」，否则会退成泛化的「本地模型」——
        # 这样 PC 替身和板端真身就不可能互相冒充。
        # 同时保留原有的 /gate/health（带统计），不破坏既有调用方。
        if self.path.startswith("/gate/health") or self.path.split("?")[0] == "/health":
            self._send(200, json.dumps(
                {"ok": True, "engine": "k1", "model": "qwen3:0.6b",
                 "stats": STATS, "upstream": self.upstream},
                ensure_ascii=False).encode("utf-8"))
            return
        url = self.upstream + self.path
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                self._send(r.status, r.read(),
                           r.headers.get("Content-Type", "application/json"))
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read())
        except Exception as e:
            self._send(502, json.dumps({"ok": False, "reason": str(e)},
                                       ensure_ascii=False).encode("utf-8"))

    def log_message(self, fmt, *args):
        return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11438)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--upstream", default=OLLAMA)
    args = ap.parse_args()
    Handler.upstream = args.upstream

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[http] gateway listening on {args.host}:{args.port} -> {args.upstream}")
    print("[http] POST /v1/chat/completions (arbitrated)  GET /gate/health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
