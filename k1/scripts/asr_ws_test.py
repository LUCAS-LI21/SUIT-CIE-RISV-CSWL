#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直接压测 K1 的 11440（sherpa-onnx offline websocket），验证「不崩溃」之外
还能真的把语音转成文字。

为什么单独写这个：11441 的 /v1/asr/stop 只能识别刚从麦克风采到的声音，
没法喂一段已知答案的音频。排查「点了没反应/报错」时，必须把两件事分开：
  ① 采集链路（arecord + dsnoop）是否采到人声
  ② 识别链路（11440）是否能出字
只测 11441 会把两者混在一起，看到 ConnectionClosedError 也不知道是谁的错。

协议（与 k1_mic_asr.py 保持一致，别改）：
    连接 ws://127.0.0.1:11440
    发  4B 采样率 + 4B 音频字节数（little-endian int32）+ float32 单声道样本
    收  一条 JSON（text / lang / emotion / timestamps ...）
    再发 "Done" 收尾

用法（在 K1 板端跑）：
    python3 /root/asr_ws_test.py                      # 默认用官方 zh.wav
    python3 /root/asr_ws_test.py /path/to/x.wav       # 指定 wav
    python3 /root/asr_ws_test.py x.wav 127.0.0.1 11440
"""
import asyncio
import struct
import sys
import wave

import numpy as np

DEFAULT_WAV = ("/root/asr/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
               "/test_wavs/zh.wav")


def read_wav(path):
    """只要 16k 单声道 S16_LE。官方 test_wavs 就是这个规格，不做重采样。"""
    with wave.open(path, "rb") as w:
        ch = w.getnchannels()
        rate = w.getframerate()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise SystemExit(f"只支持 16bit，当前 {width * 8}bit")
    x = np.frombuffer(raw, dtype="<i2")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return rate, x


async def run(path, host, port):
    import websockets

    rate, x = read_wav(path)
    f32 = (x.astype(np.float32) / 32768.0).tobytes()
    dur = len(x) / rate
    print(f"音频 {path}  {dur:.2f}s  {rate}Hz  {len(x)} 采样", flush=True)

    url = f"ws://{host}:{port}"
    async with websockets.connect(url, max_size=None, open_timeout=5) as ws:
        await ws.send(struct.pack("<ii", rate, len(f32)) + f32)
        msg = await asyncio.wait_for(ws.recv(), timeout=40)
        try:
            await ws.send("Done")
        except Exception:
            pass
    if isinstance(msg, bytes):
        msg = msg.decode("utf-8", "replace")
    print(msg, flush=True)
    return 0 if '"text"' in msg else 1


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WAV
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    port = int(sys.argv[3]) if len(sys.argv) > 3 else 11440
    return asyncio.run(run(path, host, port))


if __name__ == "__main__":
    sys.exit(main())
