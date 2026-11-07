#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K1 端到端延迟体检（比赛「响应延迟」项的测试数据来源）。

在 K1 本机跑，测的是纯板端耗时，不含 Wi-Fi / 交换机往返：

    python3 k1_latency_probe.py            # 全套
    python3 k1_latency_probe.py llm        # 只测大模型
    python3 k1_latency_probe.py asr        # 只测语音（会真录 2 秒）

每项都给出 p50 / 最小 / 最大，而不是只报一次——单次测量在 95℃ 降频的板子上
没有参考价值（实测同一请求可以在 800 ms 到 2.4 s 之间跳）。
"""

import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HOST = "127.0.0.1"
PORT_VISION = 11436
PORT_KB = 11435
PORT_LLM = 11438
PORT_VOICE = 11439
PORT_ASR = 11441

# 真实业务里最常问的那句话。用同一句才能横向比不同参数。
PROMPT = "我的猫现在怎么样？用一句话回答，不超过30个字。"


def _post(path, port, body, timeout=90):
    req = urllib.request.Request(
        f"http://{HOST}:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = r.read()
    return (time.perf_counter() - t0) * 1000.0, payload


def _get(path, port, timeout=10):
    t0 = time.perf_counter()
    with urllib.request.urlopen(f"http://{HOST}:{port}{path}", timeout=timeout) as r:
        payload = r.read()
    return (time.perf_counter() - t0) * 1000.0, payload


def _stat(name, samples):
    if not samples:
        return {"name": name, "n": 0}
    return {
        "name": name,
        "n": len(samples),
        "p50": round(statistics.median(samples), 1),
        "min": round(min(samples), 1),
        "max": round(max(samples), 1),
    }


# ---------------------------------------------------------------- 视觉
def probe_vision(rounds=12):
    """取一帧检测结果。fps 由服务自己数，这里测的是「要一次结果」的响应。"""
    samples, fps, frames = [], None, None
    for _ in range(rounds):
        try:
            ms, payload = _get("/v1/vision/latest", PORT_VISION)
            samples.append(ms)
            d = json.loads(payload)
            fps = d.get("fps", fps)
            frames = d.get("frames", frames)
        except Exception as e:
            samples.append(float("inf"))
            print(f"  vision 失败: {type(e).__name__} {e}")
    return _stat("视觉 /v1/vision/latest", samples), {"fps": fps, "frames": frames}


# ---------------------------------------------------------------- 知识库
def probe_kb(rounds=5):
    q = urllib.parse.quote("猫能喝牛奶吗")
    samples, hit = [], None
    for _ in range(rounds):
        try:
            ms, payload = _get(f"/ask?q={q}", PORT_KB, timeout=30)
            samples.append(ms)
            hit = json.loads(payload).get("hit")
        except Exception as e:
            samples.append(float("inf"))
            print(f"  kb 失败: {type(e).__name__} {e}")
    return _stat("知识库 /ask 命中", samples), {"hit": hit}


# ---------------------------------------------------------------- 大模型
def _llm_stream(body, timeout=120):
    """流式请求，返回 (首字毫秒, 总毫秒, token 数)。"""
    body = dict(body, stream=True)
    req = urllib.request.Request(
        f"http://{HOST}:{PORT_LLM}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    first = None
    chars = 0
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                break
            try:
                d = json.loads(chunk)
            except Exception:
                continue
            piece = d.get("choices", [{}])[0].get("delta", {}).get("content", "")
            if piece:
                chars += len(piece)
                if first is None:
                    first = (time.perf_counter() - t0) * 1000.0
    total = (time.perf_counter() - t0) * 1000.0
    return (first if first is not None else total), total, chars


def probe_llm(rounds=3, num_predict=64):
    """测三件事：首字延迟、整句延迟、以及 num_predict 对总时长的影响。

    首字延迟才是用户「按下就出字」的体感；总时长取决于让模型说多少字。
    0.6B 在 4 个 X60 核上 decode 只有 8~10 token/s，所以字数就是时间。
    """
    base = {
        "model": "qwen3:0.6b",
        "messages": [{"role": "user", "content": PROMPT}],
        "temperature": 0.3,
        "num_predict": num_predict,
        "keep_alive": "30m",
    }
    firsts, totals, tokens = [], [], []
    for i in range(rounds):
        try:
            f, t, c = _llm_stream(base)
            firsts.append(f)
            totals.append(t)
            tokens.append(c)
        except Exception as e:
            print(f"  llm 失败: {type(e).__name__} {e}")
    out = {
        "首字": _stat(f"LLM 首字 (num_predict={num_predict})", firsts),
        "整句": _stat(f"LLM 整句 (num_predict={num_predict})", totals),
        "chars": tokens,
    }
    return out


def probe_llm_cap():
    """num_predict 与总时长的关系。这是「把延迟压到 1 秒内」的唯一硬约束。"""
    rows = []
    for np_ in (24, 48, 96):
        r = probe_llm(rounds=2, num_predict=np_)
        rows.append({
            "num_predict": np_,
            "首字ms": r["首字"].get("p50"),
            "整句ms": r["整句"].get("p50"),
            "汉字数": r["chars"],
        })
    return rows


# ---------------------------------------------------------------- 语音
def probe_asr(seconds=2.0, rounds=2):
    """真录真识别。start → 等 N 秒 → stop，返回 stop 的往返时间。"""
    samples, texts = [], []
    for _ in range(rounds):
        try:
            _post("/v1/asr/start", PORT_ASR, {}, timeout=10)
            time.sleep(seconds)
            ms, payload = _post("/v1/asr/stop", PORT_ASR, {}, timeout=90)
            samples.append(ms)
            try:
                d = json.loads(payload)
                texts.append((d.get("text") or "")[:24])
            except Exception:
                texts.append("")
        except Exception as e:
            print(f"  asr 失败: {type(e).__name__} {e}")
    return _stat(f"ASR stop 往返 (录 {seconds}s)", samples), {"texts": texts}


# ---------------------------------------------------------------- 叫声
def probe_voice(rounds=8):
    samples = []
    for _ in range(rounds):
        try:
            ms, _ = _get("/v1/voice/latest", PORT_VOICE)
            samples.append(ms)
        except Exception as e:
            print(f"  voice 失败: {type(e).__name__} {e}")
    return _stat("叫声 /v1/voice/latest", samples)


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    report = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "items": []}

    def add(stat):
        report["items"].append(stat)

    if which in ("all", "vision"):
        s, extra = probe_vision()
        add(s)
        report["vision"] = extra
    if which in ("all", "kb"):
        s, extra = probe_kb()
        add(s)
        report["kb"] = extra
    if which in ("all", "llm"):
        r = probe_llm()
        add(r["首字"])
        add(r["整句"])
        report["llm_cap"] = probe_llm_cap()
        # 再测一次默认档，作为最终口径（cap 测试会把模型热起来）
        r2 = probe_llm(rounds=3, num_predict=64)
        report["llm_final"] = r2
    if which in ("all", "asr"):
        s, extra = probe_asr()
        add(s)
        report["asr"] = extra
    if which in ("all", "voice"):
        add(probe_voice())

    print("\n" + "=" * 62)
    for it in report["items"]:
        if it.get("n"):
            print(f"{it['name']:<34} p50 {it['p50']:>7.1f} ms  "
                  f"[{it['min']:.1f} ~ {it['max']:.1f}]  n={it['n']}")
        else:
            print(f"{it['name']:<34} 无数据")
    if report.get("vision"):
        print(f"视觉 fps={report['vision'].get('fps')} frames={report['vision'].get('frames')}")
    if report.get("llm_final"):
        f = report["llm_final"]
        print(f"LLM 首字 p50={f['首字'].get('p50')} ms  整句 p50={f['整句'].get('p50')} ms")
    if report.get("llm_cap"):
        print("num_predict 与总时长：")
        for row in report["llm_cap"]:
            print(f"  np={row['num_predict']:<4} 首字 {row['首字ms']} ms  "
                  f"整句 {row['整句ms']} ms  汉字 {row['汉字数']}")
    print("=" * 62)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
