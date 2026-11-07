#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K1 本地算力批量压测探针。

目的：在不改 App 代码的前提下，直接把「模拟感知数据」和「聊天问句」
送进 K1 板子上的 ollama，拿到耗时与输出原文，据此判断 0.6B 的能力边界。

分两部分：
  A. 监护建议 —— 12 组场景，覆盖板端视觉服务真实能产出的全部行为标签
  B. 宠物聊天 —— 同一批问句，对比三种极简提示词写法的输出质量

用法：
    python scripts/k1_batch_probe.py              # 全部跑
    python scripts/k1_batch_probe.py --only advice
    python scripts/k1_batch_probe.py --only chat

依赖：仅标准库。K1 经 adb forward 暴露在 127.0.0.1:11434。
"""

import json
import sys
import time
import urllib.error
import urllib.request

# Windows 控制台默认 GBK，中文会炸，强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "http://127.0.0.1:11434/v1/chat/completions"
MODEL = "qwen3:0.6b"


# ---------------------------------------------------------------- 调用底层

def chat(messages, max_tokens=24, temperature=0.2, timeout=180):
    """打一次 ollama，返回 (耗时秒, prompt_tok, out_tok, 文本, 错误)。

    不抛异常：失败也要给出可判读的结果，否则批量跑会中断。
    """
    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(
        BASE, data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
        method="POST",
    )

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return time.time() - t0, 0, 0, "", f"URLError: {e}"
    except Exception as e:
        return time.time() - t0, 0, 0, "", f"{type(e).__name__}: {e}"

    elapsed = time.time() - t0
    try:
        d = json.loads(raw)
        text = d["choices"][0]["message"]["content"]
        usage = d.get("usage") or {}
        return (elapsed, usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0), text, "")
    except Exception as e:
        return elapsed, 0, 0, "", f"解析失败 {e}: {raw[:120]}"


def clean(text):
    """去掉 qwen3 的思考块，与 App 侧 cleanOutput 保持一致。"""
    s = text or ""
    for marker in ("<｜end▁of▁thinking｜>", "<｜end▁of▁thinking｜>"):
        idx = s.find(marker)
        if idx >= 0:
            s = s[idx + len(marker):]
    return " ".join(s.split()).strip()


# ---------------------------------------------------------------- A. 监护建议
# 行为标签严格对齐 k1-vision/vision_service.py 的 analyze_behavior() 输出，
# 不写「频繁抓挠」这类视觉服务根本产不出来的标签。
ADVICE_SCENES = [
    # (行为, 持续分钟, 可能成因, 备注)
    ("趴卧休息", 45, "可能缺乏活动或身体不适", "久卧+高温"),
    ("趴卧休息", 8, "可能只是休息", "短时趴卧，正常"),
    ("睡觉", 180, "睡眠时间偏长", "长睡"),
    ("长时间不动", 60, "可能精神萎靡或疼痛", "long_still 关键场景"),
    ("进食", 5, "正常进食", "正常"),
    ("走动", 30, "活动量适中", "正常活动"),
    ("奔跑", 10, "剧烈运动后需要补水", "高强度"),
    ("撒娇", 15, "寻求主人关注", "coquettish"),
    ("坐着", 25, "可能在等待或观察", "sit"),
    ("站立", 20, "可能紧张或警觉", "stand"),
    ("玩耍", 12, "情绪活跃", "playing"),
    ("未检测到宠物", 0, "宠物可能离开画面", "absent，应跳过模型"),
]

ADVICE_SYSTEM = "宠物健康助手。针对主要问题给一句建议，不超过25字，只输出建议。"


def run_advice():
    print("\n" + "=" * 78)
    print("A. 监护建议压测（12 组，对应 App 的 PetMonitorPage 推理链路）")
    print("=" * 78)
    print(f"{'#':<3}{'场景':<22}{'耗时':>8}{'in':>5}{'out':>5}  输出")
    print("-" * 78)

    rows = []
    for i, (label, dur, cause, note) in enumerate(ADVICE_SCENES, 1):
        msgs = [
            {"role": "system", "content": ADVICE_SYSTEM},
            {"role": "user", "content": f"宠物{label}{dur}分钟，{cause}。"},
        ]
        el, pin, pout, text, err = chat(msgs, max_tokens=24)
        out = clean(text) if not err else f"[失败] {err}"
        rows.append((label, note, el, pin, pout, out))
        print(f"{i:<3}{note:<22}{el:>7.2f}s{pin:>5}{pout:>5}  {out}")

    _summary(rows, "建议")
    return rows


# ---------------------------------------------------------------- B. 宠物聊天
# 三种提示词写法，同一批问句，看哪种 0.6B 能扛住。
CHAT_QUESTIONS = ["你好呀", "你叫什么名字", "你今天心情怎么样", "你饿了吗", "陪我玩好不好"]

CHAT_PROMPTS = {
    "A 极简人设": "你是一只叫布布的猫，用可爱语气回一句话，不超过20字。",
    "B 只有任务": "用宠物猫的口吻回一句话，不超过20字。",
    "C 无提示词": "",
}


def run_chat():
    print("\n" + "=" * 78)
    print("B. 宠物聊天压测（3 种提示词 × 5 个问句）")
    print("=" * 78)

    results = {}
    for pname, psys in CHAT_PROMPTS.items():
        print(f"\n--- {pname} ---")
        print(f"    system: {psys if psys else '(无 system)'}")
        outs = []
        for q in CHAT_QUESTIONS:
            msgs = []
            if psys:
                msgs.append({"role": "system", "content": psys})
            msgs.append({"role": "user", "content": q})
            el, pin, pout, text, err = chat(msgs, max_tokens=48)
            out = clean(text) if not err else f"[失败] {err}"
            outs.append((q, el, pin, pout, out))
            print(f"    Q: {q:<12} {el:>6.2f}s in={pin:<4} out={pout:<3} -> {out}")
        results[pname] = outs

    return results


def _summary(rows, kind):
    ok = [r for r in rows if not str(r[5]).startswith("[失败]")]
    if not ok:
        print(f"\n{kind}：全部失败")
        return
    times = sorted(r[2] for r in ok)
    pins = sorted(r[3] for r in ok)
    print(f"\n{kind}小结：成功 {len(ok)}/{len(rows)}"
          f" | 耗时 中位 {times[len(times)//2]:.2f}s 最快 {times[0]:.2f}s 最慢 {times[-1]:.2f}s"
          f" | 输入 token 中位 {pins[len(pins)//2]}")


def main():
    only = "all"
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]

    print(f"目标：{BASE}")
    print(f"模型：{MODEL}")

    # 预热：第一次请求含模型冷加载，单独标出，不计入统计
    print("\n[预热] 首次请求（若模型冷加载会明显偏慢）…")
    el, pin, pout, text, err = chat(
        [{"role": "user", "content": "你好"}], max_tokens=8, timeout=300)
    if err:
        print(f"  预热失败：{err}")
        print("  请确认：adb forward tcp:11434 tcp:11434 是否还在，K1 上 ollama 是否在跑")
        return 1
    print(f"  预热完成 {el:.2f}s  in={pin} out={pout}")

    if only in ("all", "advice"):
        run_advice()
    if only in ("all", "chat"):
        run_chat()

    print("\n" + "=" * 78)
    print("完成。耗时基线：prefill≈20 tok/s，decode≈10 tok/s")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
