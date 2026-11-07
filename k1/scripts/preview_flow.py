#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复刻 PetMonitorPage.generateAIRecommendations 的完整逻辑，在 PC 上预演一遍
「模拟感知 -> 规则层 -> K1 模型 -> 最终界面内容」，用于在模拟器就绪前先确认效果。

重要：这里的规则判断与提示词必须与 ArkTS 代码逐字对应，否则预演没有意义。
对应文件：
  entry/src/main/ets/features/monitor/PetMonitorPage.ets  (generateAIRecommendations)
  entry/src/main/ets/common/utils/LocalLLMService.ets     (buildMessages)
"""
import json
import time
import urllib.request

ENDPOINT = "http://127.0.0.1:11434/v1/chat/completions"
MODEL = "qwen3:0.6b"
MAX_TOKENS = 24


# ---------------- 感知数据（与 PerceptionSource.ets 一致） ----------------
SCENES = [
    dict(visionLabel="趴卧休息", visionConfidence=0.91, visionDurationMin=42,
         audioText="旺财今天怎么一直趴着不动呀"),
    dict(visionLabel="来回踱步", visionConfidence=0.87, visionDurationMin=15,
         audioText="它是不是有点焦虑"),
    dict(visionLabel="频繁抓挠", visionConfidence=0.83, visionDurationMin=8,
         audioText="它一直在挠痒痒"),
    dict(visionLabel="正常活动", visionConfidence=0.94, visionDurationMin=20,
         audioText="今天精神不错"),
]

ENV = dict(temp=31.8, hum=46.0, light=128, status="Calm")   # 与 buildMockEnvironment 一致


# ---------------- 规则层（与 ArkTS 逐条对应） ----------------
def evaluate_rules(env):
    recs, facts = [], []

    if env["temp"] > 30:
        facts.append("温度偏高")
        recs.append(("warning", "增加玩具互动频率，当前环境温度偏高建议降温", "⚠️"))
    elif env["temp"] > 28:
        facts.append("温度略高")
    else:
        facts.append("温度适宜")

    if env["hum"] > 60:
        facts.append("湿度偏高")
        recs.append(("info", "环境湿度偏高建议除湿", "💧"))
    elif env["hum"] < 40:
        facts.append("湿度偏低")
        recs.append(("info", "环境干燥，建议增加宠物饮水或加湿", "💧"))
    else:
        facts.append("湿度适宜")

    if env["light"] > 120:
        facts.append("光照偏强")
        recs.append(("info", "光照较强，建议避免宠物长时间暴晒", "☀️"))
    else:
        facts.append("光照正常")

    if env["status"] == "Excited":
        recs.append(("attention", "宠物求关注，建议多陪伴互动", "🐾"))
    elif env["status"] == "Hyper":
        recs.append(("warning", "宠物状态不适，建议检查宠物健康状况", "😟"))

    if not recs:
        recs.append(("info", "宠物状态良好，继续保持当前环境", "😊"))

    return recs, " ".join(facts)


# ---------------- 模型层（与 LocalLLMService.buildMessages 一致） ----------------
def call_model(snapshot, facts):
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system",
             "content": "宠物健康助手。只输出一句不超过25字的建议，不重复输入，不加解释。"},
            {"role": "user",
             "content": f"{snapshot['temp']}度 湿度{int(snapshot['hum'])}% "
                        f"光照{snapshot['light']} "
                        f"{snapshot['visionLabel']}{snapshot['visionDurationMin']}分钟 {facts}"},
        ],
        "stream": False, "temperature": 0.2, "max_tokens": MAX_TOKENS,
    }
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
        method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    with opener.open(req, timeout=120) as r:
        d = json.loads(r.read().decode("utf-8"))
    return (time.time() - t0, d["choices"][0]["message"]["content"].strip(),
            d.get("usage", {}))


def render(scene, env, recs, facts, ai_text, elapsed, usage, ok):
    print("\n" + "═" * 74)
    print(f"  感知快照：{scene['visionLabel']} {scene['visionDurationMin']}分钟"
          f"（置信度 {scene['visionConfidence']}）  语音：「{scene['audioText']}」")
    print(f"  环境：{env['temp']}°C / 湿度 {int(env['hum'])}% / 光照 {env['light']} lux")
    print("─" * 74)
    print("  【界面实际会显示的内容】")
    print()
    if ok:
        print(f"   🤖 本地模型 · {int(elapsed * 1000)}ms · "
              f"输入{usage.get('prompt_tokens', 0)} / 输出{usage.get('completion_tokens', 0)} tok")
        print(f"   · {ai_text}")
        print(f"   · {recs[0][1]}")  # 其余规则建议
        for r in recs[1:]:
            print(f"   · {r[1]}")
    else:
        print(f"   🤖 规则兜底")
        for r in recs:
            print(f"   · {r[1]}")
    print()
    print(f"   规则结论：{facts}")
    print("═" * 74)


def main():
    print("╔" + "═" * 72 + "╗")
    print("║  本地算力链路预演：模拟感知 → 规则层 → K1 模型 → 界面内容" + " " * 16 + "║")
    print("╚" + "═" * 72 + "╝")

    for i, scene in enumerate(SCENES, 1):
        print(f"\n▶ 场景 {i}/{len(SCENES)}  请求 K1 中…", flush=True)
        snapshot = dict(ENV, **scene)
        recs, facts = evaluate_rules(ENV)
        try:
            elapsed, text, usage = call_model(snapshot, facts)
            render(scene, ENV, recs, facts, text, elapsed, usage, True)
        except Exception as e:
            print(f"  ✗ 模型调用失败：{type(e).__name__}: {e}")
            render(scene, ENV, recs, facts, "", 0, {}, False)

    print("\n全部场景完成。")


if __name__ == "__main__":
    main()
