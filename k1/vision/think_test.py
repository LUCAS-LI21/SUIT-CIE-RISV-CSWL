#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比 qwen3:0.6b 三种模式下的延迟与输出质量。

目的：确认「思考模式」是否是 21 秒延迟的主因，并选定最终调用方式。
"""
import json
import time
import urllib.request

URL = "http://127.0.0.1:11438/v1/chat/completions"
PROMPT = "主人说家狗今天一直抓耳朵。请用不超过60字给一条具体可执行的宠物护理建议。"


def post(payload, timeout=200):
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    return r, round((time.time() - t0) * 1000)


def show(tag, r, ms):
    msg = r["choices"][0]["message"]
    text = (msg.get("content") or "").replace("\n", " ")
    think = msg.get("reasoning_content") or ""
    print(f"{tag}: {ms} ms  text_len={len(text)}  think_len={len(think)}")
    print("   out>", text[:160])
    if think:
        print("   think>", think[:80])


def main():
    base = {"model": "qwen3:0.6b",
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": 200, "stream": False}

    show("A default", *post(base))
    show("B think=false", *post(dict(base, think=False)))
    show("C /no_think",
         *post({**base, "messages": [{"role": "user", "content": "/no_think " + PROMPT}]}))


if __name__ == "__main__":
    main()
