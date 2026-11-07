#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证网关的流式转发：首字什么时候到，整句什么时候结束。

    python3 probe_stream.py
"""

import json
import time
import urllib.request

URL = "http://127.0.0.1:11438/v1/chat/completions"


def run(prompt, tag):
    body = {
        "model": "qwen3:0.6b",
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "num_predict": 64,
        "keep_alive": "30m",
        "options": {"temperature": 0.3},
    }
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    first = None
    text = []
    with urllib.request.urlopen(req, timeout=90) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                d = json.loads(payload)
            except Exception:
                continue
            piece = (d.get("choices", [{}])[0].get("delta", {}).get("content") or "")
            if piece:
                if first is None:
                    first = (time.perf_counter() - t0) * 1000
                text.append(piece)
    total = (time.perf_counter() - t0) * 1000
    print(f"{tag}: 首字 {first and round(first) or '—'} ms | 整句 {round(total)} ms | "
          f"{len(''.join(text))} 字")
    print(f"   → {''.join(text)[:40]}")


if __name__ == "__main__":
    for i in range(3):
        run("我的猫现在怎么样？用一句话回答，不超过20字", f"第{i + 1}次")
