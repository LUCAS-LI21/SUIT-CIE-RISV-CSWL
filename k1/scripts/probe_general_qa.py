#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证：画面里没有宠物时，qwen3:0.6b 能不能回答「非状态类」的通用问题。

背景：现在的 ask() 只要 vision.pet 为空就整条跳过模型，回到规则模板，
于是「模型」这条线在实验室里从来没被真正调用过。要判断能不能放开，
必须看 0.6B 拿到「无目标」的事实行之后，是老实回答，还是复述提示词。

    python3 probe_general_qa.py
"""

import json
import time
import urllib.request

URL = "http://127.0.0.1:11438/v1/chat/completions"

SYSTEM = "宠物健康助手。依据给出的数据回答，不超过30字，只输出答案，不要编造数据。"

CASES = [
    ("通用问题/无宠物", "狗狗一天喂几次比较合适？"),
    ("通用问题/无宠物", "怎么让猫多喝水？"),
    ("状态问题/无宠物", "我的猫现在怎么样？"),
    ("通用问题/有宠物", "狗狗一天喂几次比较合适？"),
]

FACTS_NO_PET = "视觉:画面里暂无宠物 叫声:人声 环境:25.0℃ 50%"
FACTS_PET = "视觉:检测到猫1只 姿态站立 叫声:无 环境:25.0℃ 50%"


def run(facts, question):
    body = {
        "model": "qwen3:0.6b",
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"数据 {facts}\n问题 {question}"},
        ],
        "stream": False,
        "num_predict": 48,
        "keep_alive": "30m",
        "options": {"temperature": 0.3},
    }
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    ms = (time.perf_counter() - t0) * 1000
    txt = (d.get("choices", [{}])[0].get("message", {}).get("content") or "")
    txt = txt.replace("\n", " ").strip()
    pt = d.get("prompt_eval_count", 0)
    et = d.get("eval_count", 0)
    print(f"{ms:6.0f} ms  prefill {pt:3d}t  out {et:3d}t  → {txt[:60]}")
    return txt


def main():
    print("=" * 78)
    for tag, q in CASES:
        facts = FACTS_NO_PET if "无宠物" in tag else FACTS_PET
        print(f"[{tag}] {q}")
        run(facts, q)
        print()
    print("=" * 78)


if __name__ == "__main__":
    main()
