#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端链路验证：视觉 + 语音 → K1 本地 LLM → 建议

仅用于链路联调验证，不是最终交付接口。
最终形态是把这段融合逻辑固化进服务端，供 3568 直接调用。
"""
import json
import time
import urllib.request

VISION = "http://127.0.0.1:11436/v1/vision/latest"
ASR = "http://127.0.0.1:11437/v1/asr/latest"
GATEWAY = "http://127.0.0.1:11438/v1/chat/completions"


def get(url, timeout=10):
    return json.loads(urllib.request.urlopen(url, timeout=timeout).read().decode())


def post(url, payload, timeout=180):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    return r, round((time.time() - t0) * 1000)


def main():
    v = get(VISION)
    print("[vision]", json.dumps(
        {k: v.get(k) for k in ("pet", "label", "conf", "count", "fps", "human")},
        ensure_ascii=False))

    a = get(ASR)
    print("[asr]", json.dumps(
        {k: a.get(k) for k in ("text", "seq", "age_sec")}, ensure_ascii=False))

    human = v.get("human") or {}
    ctx = []
    if v.get("pet"):
        ctx.append(f"画面中的宠物：{v['pet']}，姿态：{v.get('label')}，"
                   f"置信度 {v.get('conf')}")
    else:
        ctx.append("画面中暂未识别到猫或狗")
    if human.get("present"):
        ctx.append(f"画面中有人，共 {human.get('count')} 人")
    ctx.append(f"主人说：{a.get('text')}")

    prompt = ("你是一位宠物健康顾问。根据以下实时信息，用中文给主人一段不超过80字的建议，"
              "要具体、可执行。\n" + "\n".join(ctx))

    r, ms = post(GATEWAY, {
        "model": "qwen3:0.6b",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "stream": False,
    })
    print(f"[llm] {ms} ms")
    print("[advice]", r["choices"][0]["message"]["content"].strip())


if __name__ == "__main__":
    main()
