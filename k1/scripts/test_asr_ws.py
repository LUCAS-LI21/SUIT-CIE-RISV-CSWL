#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K1 离线 ASR WebSocket 服务的连通性/协议测试（在 K1 本地跑）。

协议（sherpa-onnx/csrc/offline-websocket-server-impl.h 原文）：
  前 4 字节 little-endian int32 = 采样率
  接下来的 4 字节 little-endian int32 = 后续音频字节数
  其余 = float32 单声道采样，归一化到 [-1, 1]
  第一条消息必须 >= 8 字节，否则服务端拿不到头部。
  发完音频后服务端返回识别结果（JSON 文本帧）；
  客户端发文本 "Done" 表示没有更多音频。

用法：
    python3 test_asr_ws.py /path/to/x.wav [ws://127.0.0.1:11440]
"""
import asyncio
import json
import struct
import sys
import wave

import numpy as np
import websockets


def load_wav(path):
    with wave.open(path, "rb") as f:
        ch = f.getnchannels()
        sr = f.getframerate()
        width = f.getsampwidth()
        raw = f.readframes(f.getnframes())
    if ch != 1:
        raise SystemExit(f"只支持单声道，实际 {ch} 声道")
    if width != 2:
        raise SystemExit(f"只支持 16bit，实际 {width * 8}bit")
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return sr, pcm


async def run(path, url):
    sr, pcm = load_wav(path)
    payload = pcm.astype(np.float32).tobytes()
    header = struct.pack("<ii", sr, len(payload))
    print(f"[client] {path}: {sr} Hz, {len(pcm)} samples, "
          f"{len(payload)} bytes, {len(pcm) / sr:.2f}s")

    async with websockets.connect(url, max_size=None) as ws:
        # 一条消息发完（header + audio），满足「首条 >= 8 字节」的约束
        await ws.send(header + payload)
        print("[client] 已发送，等待识别…")
        resp = await asyncio.wait_for(ws.recv(), timeout=120)
        print("[client] 原始响应:", resp)
        try:
            obj = json.loads(resp)
            print("[client] text =", repr(obj.get("text")))
            for k in ("lang", "emotion", "event"):
                if k in obj:
                    print(f"[client] {k} = {obj[k]}")
        except Exception as e:
            print("[client] 非 JSON:", e)
        await ws.send("Done")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    url = sys.argv[2] if len(sys.argv) > 2 else "ws://127.0.0.1:11440"
    asyncio.run(run(sys.argv[1], url))


if __name__ == "__main__":
    main()
