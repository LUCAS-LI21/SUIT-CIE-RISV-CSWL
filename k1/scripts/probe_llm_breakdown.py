#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拆解 LLM 单次请求耗时：加载 / 预填充 / 解码各占多少。

用途：决定「把延迟压到 1 秒内」到底该削哪一段。
如果是预填充 —— 削提示词、复用 KV cache；
如果是解码 —— 削输出字数，或换更小的模型。

    python3 probe_llm_breakdown.py
"""

import json
import time
import urllib.request

URL = "http://127.0.0.1:11434/v1/chat/completions"

CASES = [
    ("短提示", "你好"),
    ("中等提示", "我的猫现在怎么样？用一句话回答，不超过20字"),
    (
        "长提示(带感知状态)",
        "你是宠物健康助手。画面中暂无宠物；叫声类别=人声；温度26度湿度50%。"
        "问题：我的猫现在怎么样？请用一句话回答，不超过20字。",
    ),
    ("短提示(复测)", "你好"),
]


def once(prompt, tag, np_=80):
    body = {
        "model": "qwen3:0.6b",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "num_predict": np_,
        "keep_alive": "30m",
        "options": {"temperature": 0.3},
    }
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read())
    tot = (time.perf_counter() - t0) * 1000

    pn = d.get("prompt_eval_count", 0)
    pd = d.get("prompt_eval_duration", 0) / 1e6
    en = d.get("eval_count", 0)
    ed = d.get("eval_duration", 0) / 1e6
    ld = d.get("load_duration", 0) / 1e6
    ans = (d.get("choices", [{}])[0].get("message", {}).get("content") or "")
    ans = ans.replace("\n", " ")[:34]

    print(f"{tag:16s} 总 {tot:6.0f} ms | load {ld:5.0f} | "
          f"prefill {pn:3d}t @ {pd:6.0f} ms | "
          f"decode {en:3d}t @ {ed:6.0f} ms "
          f"({en / (ed / 1000) if ed else 0:.1f} tok/s)")
    print(f"{'':16s} → {ans}")
    return {"tag": tag, "total": tot, "load": ld, "prompt_tokens": pn,
            "prefill_ms": pd, "eval_tokens": en, "decode_ms": ed}


def main():
    print("=" * 78)
    rows = [once(p, t) for t, p in CASES]
    print("=" * 78)
    # 预填充速率：长提示与短提示的差值就是「每 token 多少钱」
    a, b = rows[0], rows[2]
    dt = b["prefill_ms"] - a["prefill_ms"]
    dn = b["prompt_tokens"] - a["prompt_tokens"]
    if dn > 0:
        print(f"预填充边际成本：{dt / dn:.1f} ms/token "
              f"（{dn} token 多花 {dt:.0f} ms）")
    print(f"解码速率：{rows[-1]['eval_tokens'] / (rows[-1]['decode_ms'] / 1000):.1f} tok/s")
    print(json.dumps(rows, ensure_ascii=False))


if __name__ == "__main__":
    main()
