#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K1 板载麦克风录音 + 本地识别（HTTP :11441）。

为什么不用 3568 的麦克风（2026-11-06 实测铁证）
    3568 的 AudioCapturer 采到的是**恒定直流偏置**，不是声音：
        rms/peak = 0.878   零交叉率 0.0003   0-100 Hz 占 43.7%
        波形前 40 个样本全是 0 / -1
    同一时刻 K1 的 USB 声卡（C-Media）采到的是正常环境声：
        rms/peak = 0.185   零交叉率 0.134   1k-4k Hz 占 40.5%
    人声的 rms/peak 必在 0.1~0.3；0.878 说明信号幅度几乎不变，
    是麦克风输入悬空后的 ADC 直流偏移。送进 ASR 必然返回空文本。
    → 采集端必须搬到 K1，这正是「按住说话」本该用的那个麦克风。

端口分工
    11440  sherpa-onnx offline websocket（模型常驻，收 float32 音频）
    11441  本服务（收开始/停止指令，录音在本机完成）
    两者都在 K1 上，中间走 127.0.0.1，不经过网络。

接口
    POST /v1/asr/start   开始录音
    POST /v1/asr/stop    停止录音 → 识别 → {"ok":true,"text":"...","dur":2.3}
    GET  /health

为什么录音写 raw 而不是 wav
    arecord 被 SIGTERM 打断时不会回填 wav 头里的 data 长度，产出坏文件；
    raw 只要字节数，头由本进程现拼，长度绝对正确。

为什么不每次起命令行识别
    bin/ 里只有 websocket 版本，没有 sherpa-onnx-offline 单体；
    而且每次启动都要重新加载 229 MB 模型（3~5 秒）。
    推到本机 11440 复用常驻模型，延迟只剩推理本身（RTF≈0.96）。
"""

import asyncio
import json
import os
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

# dsnoop 允许多客户端共享同一个 capture 设备，petvoice（11439）正在用它，
# 本服务并发读不会把叫声识别挤掉。实测两块服务同时在跑不冲突。
DEVICE = os.environ.get("MIC_DEV", "plug:dsnoop0")
RATE = 16000
PORT = int(os.environ.get("PORT", "11441"))
RAW_PATH = "/tmp/k1_press.raw"
ASR_WS = os.environ.get("ASR_WS", "ws://127.0.0.1:11440")

# 一次只有一个人在按按钮，用锁把 start/stop 串起来就够了。
_lock = threading.Lock()
_state = {"proc": None, "t0": 0.0}


def _start_capture():
    with _lock:
        # 幂等：上一次如果因为识别异常/网络断开没走到 stop，这里必须能把
        # 僵尸 arecord 收掉再重开，否则前端会一直看到「正在录音中」，
        # 而且那个进程会一直占着设备。实测踩过一次（stop 被 501 挡掉后，
        # 之后每次按下都失败）。
        if _state["proc"] is not None:
            old = _state["proc"]
            _state["proc"] = None
            try:
                old.terminate()
                old.wait(timeout=1)
            except Exception:
                try:
                    old.kill()
                except Exception:
                    pass

        if os.path.exists(RAW_PATH):
            try:
                os.remove(RAW_PATH)
            except OSError:
                pass
        try:
            p = subprocess.Popen(
                ["arecord", "-D", DEVICE, "-f", "S16_LE", "-r", str(RATE),
                 "-c", "1", "-t", "raw", "-q", RAW_PATH],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            return {"ok": False, "err": f"arecord 启动失败: {e}"}

        # arecord 打开设备失败会立刻退出，这里给它一点时间暴露问题，
        # 否则前端会以为在录音、实际什么也没采到。
        time.sleep(0.08)
        if p.poll() is not None:
            return {"ok": False, "err": f"arecord 退出码 {p.returncode}（设备被占用？）"}

        _state["proc"] = p
        _state["t0"] = time.time()
        return {"ok": True}


def _get_levels(x):
    """电平判据，与前端 dump 用同一套阈值，方便两边对照。"""
    if x.size == 0:
        return 0.0, 0, 0.0
    peak = int(np.abs(x).max())
    rms = float(np.sqrt((x.astype(np.float64) ** 2).mean()))
    zcr = float(np.mean(np.abs(np.diff(np.signbit(x).astype(np.int8)))))
    return rms, peak, zcr


# 静音闸门用的帧长：25 ms。再短底噪抖动大，再长会把短音节抹平。
_GATE_FRAME = 400


def _speech_gate(x):
    """判断这段录音里到底有没有人说话。返回 (是否放行, 诊断字典)。

    为什么要重写（2026-11-06 实测推翻旧判据）
        旧判据是 `rms<120 and peak<900 and zcr<0.02`，三条**同时**成立才拒识。
        实测环境底噪 rms 515~552、peak 2828~4468、zcr 0.056，一条都不满足，
        于是 5.88 s 纯底噪被 SenseVoice 解成「我记之下像最远的路」——
        标点齐全、语句通顺，人眼根本看不出是幻觉。三条与关系本身就是错的：
        只要「全程没有持续的人声能量」就该拒，不需要三个条件一起凑。

    新判据只用两个物理量，都从帧能量算（用整段录音自身做参照，不看绝对音量，
    这样麦克风远近、增益大小都不影响结论）：
        floor   帧能量的 10 分位 —— 相当于这段录音的底噪
        longest 高于 max(floor*3, 400) 的最长连续段（秒）
        dyn     最大帧能量 / floor —— 动态范围

        真人说话：必有 0.2 s 以上的连续有声段，且 voiced 段比底噪响一个量级。
        稳态噪声：能量处处差不多，两样都不满足。

    拒绝条件写成「两个都不满足」，而不是「满足其一就拒」：
        宁可把一段噪音放过去（顶多幻觉一句），也不能把真实说话错杀
        （用户会看到「没听清」，比幻觉更像功能坏了）。
    实测标定（scripts/asr_gate_calib.py）：
        纯底噪 5.88 s   longest 0.15 s   dyn  4.6
        官方语音 5.59 s longest 1.18 s   dyn  722
    """
    n = x.size // _GATE_FRAME
    if n < 4:
        return True, {"gate": "too_short"}      # 太短无法判断，交给后面的时长检查

    f = x[: n * _GATE_FRAME].reshape(n, _GATE_FRAME).astype(np.float32)
    frms = np.sqrt((f ** 2).mean(axis=1))
    floor = float(np.percentile(frms, 10))
    thr = max(floor * 3.0, 400.0)
    above = frms > thr

    longest = cur = 0
    for a in above:
        cur = cur + 1 if a else 0
        longest = max(longest, cur)
    longest_s = longest * _GATE_FRAME / RATE
    dyn = float(frms.max()) / floor if floor > 1e-3 else 999.0

    info = {"floor": round(floor, 1), "longest": round(longest_s, 2),
            "dyn": round(dyn, 1)}

    # 数字静音：连 ADC 抖动都没有，直接拒（这种音频喂模型纯属浪费 2 秒）
    if float(frms.max()) < 60.0:
        info["gate"] = "digital_silence"
        return False, info

    if longest_s < 0.20 and dyn < 6.0:
        info["gate"] = "no_speech"
        return False, info
    return True, info



async def _recognize(pcm16: bytes) -> dict:
    """把 int16 PCM 推给本机 11440，拿 SenseVoice 的 JSON。"""
    import websockets

    f32 = (np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0).tobytes()
    # 协议：4B 采样率 + 4B 音频字节数（都是 little-endian int32）+ float32 样本
    header = struct.pack("<ii", RATE, len(f32))

    async with websockets.connect(ASR_WS, max_size=None, open_timeout=5) as ws:
        await ws.send(header + f32)
        # 顺序必须是「发音频 → 等结果 → 再发 Done 关闭」（2026-11-06 实测）。
        # 如果发完音频就紧跟一条 "Done"，服务端会把它当成会话结束直接
        # close(1000)，于是本地 recv 抛出 ConnectionClosedOK，拿不到 JSON。
        msg = await asyncio.wait_for(ws.recv(), timeout=40)
        try:
            await ws.send("Done")
        except Exception:
            pass
    if isinstance(msg, bytes):
        msg = msg.decode("utf-8", "replace")
    return json.loads(msg)


def _stop_capture_and_recognize():
    with _lock:
        p = _state["proc"]
        _state["proc"] = None
        if p is None:
            return {"ok": False, "err": "当前没有在录音"}
        p.terminate()
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        dur = time.time() - _state["t0"]

    raw = b""
    if os.path.exists(RAW_PATH):
        with open(RAW_PATH, "rb") as f:
            raw = f.read()

    n = len(raw) // 2
    if n < RATE * 0.2:
        return {"ok": True, "text": "", "dur": round(dur, 2), "rms": 0, "peak": 0,
                "zcr": 0, "reason": f"录音太短（{dur:.2f}s / {n} 采样）"}

    x = np.frombuffer(raw[: n * 2], dtype="<i2")
    rms, peak, zcr = _get_levels(x)

    # 静音闸门：没说话就别让模型「听」出话来。
    # SenseVoice 对纯底噪会给出结构化但完全虚构的句子——实测 5.88 s 环境音识别到
    # 「我记之下像最远的路」，标点齐全、语气通顺，人一眼看不出是幻觉。
    # 判据的推导与标定数据见 _speech_gate 的注释。
    ok, gate = _speech_gate(x)
    if not ok:
        # 文案要短：前端把它套进「没听清（…），请再说一次」里，
        # 这里写长了会变成「没听清（没听到人声（整段都是稳态噪声…），已跳过识别）」，
        # 嵌套括号一眼看不懂。判据细节留在 gate 字段里给排查用。
        why = ("没听到人说话" if gate.get("gate") == "no_speech"
               else "麦克风只采到静音")
        return {
            "ok": True, "text": "", "dur": round(dur, 2),
            "rms": round(rms, 1), "peak": peak, "zcr": round(zcr, 4),
            "gate": gate, "reason": why,
        }

    t0 = time.time()
    try:
        res = asyncio.run(_recognize(raw[: n * 2]))
    except Exception as e:  # 识别失败要单独报，别和「没听到」混在一起
        return {"ok": False, "err": f"识别服务异常: {type(e).__name__}: {e}",
                "dur": round(dur, 2), "rms": round(rms, 1), "peak": peak, "zcr": round(zcr, 4)}

    return {
        "ok": True,
        "text": res.get("text", ""),
        "lang": res.get("lang", ""),
        "emotion": res.get("emotion", ""),
        "dur": round(dur, 2),
        "rms": round(rms, 1),
        "peak": peak,
        "zcr": round(zcr, 4),
        "asr_ms": int((time.time() - t0) * 1000),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # 默认实现会把每条请求写 stderr，journalctl 里全是噪音
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send({
                "ok": True,
                "service": "k1-mic-asr",
                "device": DEVICE,
                "rate": RATE,
                "asr_ws": ASR_WS,
                "recording": _state["proc"] is not None,
            })
        else:
            self._send({"ok": False, "err": "not found"}, 404)

    def do_POST(self):
        # 【必须】把请求体读完再处理。
        # protocol_version 是 HTTP/1.1（长连接），如果 body 不消费，剩余字节
        # 会被 next 请求的起始行解析吃掉：前端 http 模块发 POST 时带
        # extraData='{}'，正好残留 2 字节，于是紧跟的 /v1/asr/stop 被解析成
        # 非法请求行 → BaseHTTPRequestHandler 直接回 501 Not Implemented。
        # 2026-11-06 实测现象：start 成功、stop 报 "HTTP 501"，
        # 之后每次按下都提示「麦克风没打开」（因为服务端还卡在录音中）。
        try:
            n = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            n = 0
        if n > 0:
            try:
                self.rfile.read(n)
            except Exception:
                pass

        try:
            if self.path.startswith("/v1/asr/start"):
                self._send(_start_capture())
            elif self.path.startswith("/v1/asr/stop"):
                self._send(_stop_capture_and_recognize())
            else:
                self._send({"ok": False, "err": "not found"}, 404)
        except Exception as e:
            self._send({"ok": False, "err": f"{type(e).__name__}: {e}"}, 500)


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"k1-mic-asr listening on {PORT}, device={DEVICE}, rate={RATE}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
