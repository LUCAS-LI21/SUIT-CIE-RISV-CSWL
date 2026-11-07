#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K1 开机自检 + 运行期自愈。

一句话：断电重启之后，所有链路必须自己站起来，不靠人上去敲命令。

做四件事，每件都写进 /run/k1-selfcheck.json 供前端/排查用：

  1. 单元存活   —— systemd 里该 active 的没 active，就 restart 一次
  2. 端口可达   —— 六个 HTTP 口逐个探 /health，11440（WebSocket）单探 TCP
  3. 视觉在出帧 —— 隔 2 秒读两次 frames，必须递增；卡住就重启视觉
  4. 模型在内存 —— ollama ps 里必须有 qwen3:0.6b，没有就发一次预热请求

另外每 10 分钟做一次**真实解码探针**（推 5.59 s 中文音频给 11440 看有没有出字）。
理由：11440 那种「端口活着、一解码就 SIGILL」的故障，上面四项全都查不出来
（2026-11-06 实测踩过，藏了一整天）。探针限流是为了不长期抢视觉的 AI 核。

退出码：0 = 全部通过（或已自愈），1 = 有项目自愈失败，需要人看。

为什么不是「重启所有服务」：那会把正在跑的推理打断，也会让
STALE_KEEP_SEC 之类的短时状态清零，属于为了自愈反而制造故障。
只修坏的那一个。
"""

import asyncio
import json
import os
import socket
import struct
import subprocess
import time
import urllib.request

# 顺序有意义：ollama 起不来，网关和预热都没意义
UNITS = [
    "ollama",
    "k1-vision-csi",
    "k1-gateway",
    "petkb",
    "petvoice",
    "k1-mic-asr",
    "k1-asr-ws",
    # 叫声模型侧车。它挂了 11439 照常工作（规则分类是完整的一层），
    # 但"模型确认"这一层会静默消失——不查就永远发现不了，所以要列进来。
    "panns-sidecar",
]

# 端口 -> (单元名, 健康路径)
PORTS = [
    (11434, "ollama", "/api/tags"),
    (11435, "petkb", "/health"),
    (11436, "k1-vision-csi", "/health"),
    (11438, "k1-gateway", "/health"),
    (11439, "petvoice", "/health"),
    (11441, "k1-mic-asr", "/health"),
    (11442, "panns-sidecar", "/health"),
]

# 只有 TCP、没有 HTTP /health 的服务（websocket）。单独列，用 connect 探活。
#
# 为什么必须补这一项：11440（sherpa-onnx）崩了以后，11441 的 /health 仍然返回
# ok —— 它只报告「我自己活着、arecord 能开」，根本不知道下游死了。于是自检
# 全 PASS，用户一按语音键就报「识别服务异常: ConnectionClosedError」。
# 2026-11-06 就是这么藏了一整天（起因是核绑定，详见 pin-cores.sh 文件头）。
WS_PORTS = [
    (11440, "k1-asr-ws"),
]

# 真实解码探针用的样本：官方随包的 5.59 s 中文音频。
# 答案是「开饭时间早上九点至下午五点」，只要 text 非空就说明解码链路真的通。
# 只在 0-3 上跑（该单元已绑定 AI 核），单次约 2.2 s，所以限流到 10 分钟一次：
# 每 90 s 都解一次会把视觉的 AI 核抢掉一截，得不偿失。
ASR_WAV = ("/root/asr/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
           "/test_wavs/zh.wav")
ASR_STAMP = "/run/k1-selfcheck-asr.stamp"
ASR_INTERVAL = 600

OUT = "/run/k1-selfcheck.json"
MODEL = "qwen3:0.6b"


def sh(cmd, timeout=20):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as e:
        return 1, str(e)


def unit_active(unit):
    rc, _ = sh(f"systemctl is-active --quiet {unit}")
    return rc == 0


def http_ok(port, path, timeout=4):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}",
                                    timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def wait_port(port, path, seconds):
    """serving 有启动时间（ASR 要载 229 MB 模型，约 20 s），不能一探就下结论。"""
    end = time.time() + seconds
    while time.time() < end:
        if http_ok(port, path):
            return True
        time.sleep(1.0)
    return False


def tcp_ok(port, timeout=3):
    """websocket 服务只认得上层协议，普通 HTTP 会回 426；
    这里只要 TCP 能建立，就说明进程还在 listen。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except Exception:
        return False


def wait_tcp(port, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if tcp_ok(port):
            return True
        time.sleep(1.0)
    return False


def asr_decode_ok():
    """真解一段已知音频。这是唯一能抓出「SIGILL 只在 Decode 时触发」的检查——
    探端口是没用的：进程崩完被 Restart=always 拉起来，端口又通，看起来一切正常。"""
    try:
        import wave
        import websockets
        import numpy as np
    except Exception:
        return None, "缺依赖（需要 websockets / numpy）"
    try:
        with wave.open(ASR_WAV, "rb") as w:
            rate = w.getframerate()
            raw = w.readframes(w.getnframes())
        f32 = (np.frombuffer(raw, dtype="<i2").astype(np.float32)
               / 32768.0).tobytes()

        async def go():
            async with websockets.connect("ws://127.0.0.1:11440",
                                          max_size=None, open_timeout=5) as ws:
                await ws.send(struct.pack("<ii", rate, len(f32)) + f32)
                m = await asyncio.wait_for(ws.recv(), timeout=60)
                try:
                    await ws.send("Done")
                except Exception:
                    pass
                return m

        msg = asyncio.run(go())
        if isinstance(msg, bytes):
            msg = msg.decode("utf-8", "replace")
        text = json.loads(msg).get("text", "")
        return (bool(text), text or "(空文本)")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def asr_probe_due():
    """限流：距上次真实解码超过 ASR_INTERVAL 才做。reboot 后 /run 清空 → 必然做一次。"""
    try:
        return time.time() - os.path.getmtime(ASR_STAMP) >= ASR_INTERVAL
    except OSError:
        return True


def _touch(path):
    try:
        open(path, "w").close()
    except OSError:
        pass



def vision_frames():
    try:
        with urllib.request.urlopen("http://127.0.0.1:11436/health",
                                    timeout=5) as r:
            d = json.loads(r.read())
        return int(d.get("frames", -1)), d
    except Exception:
        return -1, {}


def main():
    report = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "actions": [],
              "items": {}, "ok": True}

    def act(msg):
        report["actions"].append(msg)
        print(f"[selfcheck] {msg}", flush=True)

    # ---- 1. 单元存活
    for u in UNITS:
        if unit_active(u):
            report["items"][u] = "active"
            continue
        act(f"{u} 未运行 → restart")
        sh(f"systemctl reset-failed {u} 2>/dev/null; systemctl restart {u}", 30)
        time.sleep(1)
        ok = unit_active(u)
        report["items"][u] = "restarted" if ok else "FAILED"
        report["ok"] = report["ok"] and ok

    # ---- 2. 端口可达
    for port, unit, path in PORTS:
        if http_ok(port, path):
            report["items"][f"port{port}"] = "ok"
            continue
        act(f":{port} 无响应 → 等 25 s / 必要时重启 {unit}")
        if wait_port(port, path, 25):
            report["items"][f"port{port}"] = "ok"
            continue
        sh(f"systemctl restart {unit}", 30)
        got = wait_port(port, path, 25)
        report["items"][f"port{port}"] = "ok" if got else "FAILED"
        report["ok"] = report["ok"] and got

    # ---- 2b. WebSocket 服务（11440）只探 TCP
    for port, unit in WS_PORTS:
        if tcp_ok(port):
            report["items"][f"tcp{port}"] = "ok"
            continue
        act(f":{port} 无监听（{unit}）→ 等 25 s / 必要时重启")
        if wait_tcp(port, 25):
            report["items"][f"tcp{port}"] = "ok"
            continue
        sh(f"systemctl restart {unit}", 30)
        got = wait_tcp(port, 25)
        report["items"][f"tcp{port}"] = "ok" if got else "FAILED"
        report["ok"] = report["ok"] and got

    # ---- 3. 视觉是否真的在出帧
    f1, d1 = vision_frames()
    if f1 >= 0:
        time.sleep(2.0)
        f2, d2 = vision_frames()
        if f2 > f1:
            report["items"]["vision_frames"] = f"advancing ({f1}->{f2})"
        else:
            act(f"视觉帧号不增长 ({f1}->{f2})，paused={d1.get('paused')} → restart")
            # 最常见的原因是上一次 LLM 请求把视觉 pause 了却没 resume
            sh("curl -s -m 3 -X POST http://127.0.0.1:11436/v1/vision/resume")
            time.sleep(3)
            f3, _ = vision_frames()
            if f3 > f2:
                report["items"]["vision_frames"] = f"recovered ({f2}->{f3})"
            else:
                sh("systemctl restart k1-vision-csi", 30)
                time.sleep(8)
                f4, _ = vision_frames()
                ok = f4 >= 0
                report["items"]["vision_frames"] = "restarted" if ok else "FAILED"
                report["ok"] = report["ok"] and ok

    # ---- 4. 模型是否常驻
    rc, out = sh("ollama ps", 15)
    if MODEL.split(":")[0] in out:
        report["items"]["model_resident"] = "yes"
    else:
        act(f"{MODEL} 不在内存 → 预热")
        payload = json.dumps({"model": MODEL, "prompt": "hi", "stream": False,
                              "options": {"num_predict": 1},
                              "keep_alive": "30m"}).encode()
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:11434/api/generate", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=120) as r:
                r.read()
            report["items"]["model_resident"] = "warmed"
        except Exception as e:
            report["items"]["model_resident"] = f"FAILED: {e}"
            report["ok"] = False

    # ---- 5. 真实解码探针（限流 10 分钟）
    # 端口通不代表能识别：SIGILL 只在 Decode 那一刻触发，崩溃后 Restart=always
    # 会立刻把它拉起来，端口又是通的。只有真推一段音频才知道能不能出字。
    if asr_probe_due() and report["items"].get("tcp11440") != "FAILED":
        ok, info = asr_decode_ok()
        if ok is None:
            report["items"]["asr_decode"] = f"skip（{info}）"
        elif ok:
            report["items"]["asr_decode"] = f"ok（{info}）"
            _touch(ASR_STAMP)
        else:
            act(f"ASR 解码失败（{info}）→ restart k1-asr-ws")
            sh("systemctl restart k1-asr-ws", 30)
            wait_tcp(11440, 30)
            ok2, info2 = asr_decode_ok()
            report["items"]["asr_decode"] = (f"retry-ok（{info2}）" if ok2
                                             else f"FAILED（{info2}）")
            if ok2:
                _touch(ASR_STAMP)
            else:
                report["ok"] = False

    # ---- 落盘
    try:
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"[selfcheck] 写 {OUT} 失败: {e}", flush=True)

    bad = [k for k, v in report["items"].items() if v == "FAILED"]
    print(f"[selfcheck] {'PASS' if report['ok'] else 'FAIL ' + str(bad)} "
          f"actions={len(report['actions'])}", flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
