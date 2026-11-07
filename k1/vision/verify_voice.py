#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""叫声识别链路的接口合约自检（在 K1 板子上跑）。

它回答一个问题：站在 3568 前端的角度，11439 给出的这份 JSON 能不能直接用？

前端（K1Voice.ets）只认下面这些字段，少一个就会出现「界面上有卡片但没字」：
    ok / cls / cls_cn / state / state_cn / conf / pet / advice / source / ts

为什么单独写一个自检而不是靠日志：日志只能证明「服务在跑」，
证明不了「字段契约没变」。而字段名一改，前端是静默失效的——
卡片照常渲染，只是文字全空，这种问题在答辩现场极难临场解释。

用法：
    python3 verify_voice.py                # 查一次
    python3 verify_voice.py --watch 20     # 连续看 20 秒，确认采集帧数在涨

退出码：0 全过；1 有 FAIL（可直接接进 CI 或开机自检脚本）。
"""
import argparse
import json
import re
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:11439"

# 前端 noteworthy 判定的三个类别：这三种不该弹「异常」卡片
QUIET_CLASSES = ("silence", "noise", "human")

# 每种状态不该出现的处置词。与板端 advice_acceptable() 的黑名单同源，
# 这里再独立写一遍是有意的：两边共用一份清单就失去了交叉校验的意义。
BAD_WORDS = {
    "alert": ("喂食", "散步", "洗澡", "玩耍"),
    "threat": ("喂食", "抚摸", "抱它"),
    "anxiety": ("喂食", "玩耍"),
    "hunger": ("立即就医", "送医"),
    "pain": ("喂食", "玩耍", "散步"),
}


def get(path, timeout=5):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def check_latest(rec):
    """逐字段校验，返回 (fail_list, warn_list)。"""
    fails, warns = [], []

    # ---- 1. 必备字段存在且类型正确 ----
    need_str = ("cls", "cls_cn", "state", "state_cn", "pet", "advice", "source")
    for k in need_str:
        v = rec.get(k)
        if not isinstance(v, str) or not v.strip():
            fails.append(f"字段 {k} 缺失或为空（前端会显示空卡片）")

    if rec.get("ok") is not True:
        fails.append("ok 不为 true")
    if not isinstance(rec.get("conf"), (int, float)):
        fails.append("conf 不是数字")
    elif not (0.0 <= rec["conf"] <= 1.0):
        fails.append(f"conf={rec['conf']} 越界（应在 0~1）")
    if not isinstance(rec.get("ts"), (int, float)) or rec.get("ts", 0) <= 0:
        fails.append("ts 未填或为 0")

    # ---- 2. 中文标签不能退化成英文枚举 ----
    # 板端用 dict.get(cls, cls) 兜底，漏配一个键就会原样吐英文，
    # 前端照样渲染，只是写着「meow·hunger」。这类漏配必须查出来。
    if re.fullmatch(r"[a-z_]+", rec.get("cls_cn", "")):
        fails.append(f"cls_cn 未翻译（{rec.get('cls_cn')}），检查 CLS_CN 表")
    if re.fullmatch(r"[a-z_]+", rec.get("state_cn", "")):
        fails.append(f"state_cn 未翻译（{rec.get('state_cn')}），检查 STATE_CN 表")

    # ---- 3. 安静类不该带异常级置信度 ----
    # 静音时若给出 0.8 的 conf，前端虽然按 cls 过滤掉了，但 /v1/voice/events
    # 里会出现高置信度的 silence，事后统计会很难看。
    if rec.get("cls") in QUIET_CLASSES and rec.get("conf", 0) >= 0.95:
        warns.append(f"{rec.get('cls')} 的 conf={rec['conf']} 偏高，确认是否合理")

    # ---- 4. advice 与 state 不能自相矛盾 ----
    # 这是实测踩过的坑：0.6B 把「警戒」改写成「马上回家喂食」。
    # 前端现在会把这句话原样念给用户，错一句就是错一条处置建议。
    advice = rec.get("advice") or ""
    state = rec.get("state") or ""
    if rec.get("cls") not in QUIET_CLASSES:
        if len(advice) < 6:
            fails.append(f"advice 过短（{len(advice)} 字）：{advice!r}")
        elif len(advice) > 46:
            fails.append(f"advice 过长（{len(advice)} 字），会撑破卡片")
        for w in BAD_WORDS.get(state, ()):
            if w in advice:
                fails.append(f"建议与状态矛盾：state={state} 却建议「{w}」→ {advice}")

    # ---- 5. source 只能是这两个值 ----
    if rec.get("source") not in ("本地模型", "规则"):
        fails.append(f"source={rec.get('source')!r}，前端徽标只认「本地模型」/「规则」")

    return fails, warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0, help="连续观察 N 秒")
    args = ap.parse_args()

    print("== /health ==")
    try:
        h = get("/health")
    except Exception as e:
        print(f"[FAIL] 11439 不可达：{e}")
        print("       先看 systemctl status petvoice.service")
        return 1
    print(json.dumps(h, ensure_ascii=False))

    if h.get("mode") == "rules":
        print("[提示] 当前是纯规则模式（未加载 ONNX 分类器），属预期行为")

    # 采集是否真的在跑：帧数不涨说明线程卡死或设备被占，但 HTTP 仍然会回 200。
    # 只看 /health 返回 200 就下结论是漏的，这条必查。
    if args.watch > 0:
        f0, t0 = h.get("frames", 0), time.time()
        time.sleep(args.watch)
        f1 = get("/health").get("frames", 0)
        dt = max(1e-6, time.time() - t0)
        rate = (f1 - f0) / dt
        print(f"== 采集速率 == {f1 - f0} 帧 / {dt:.1f}s = {rate:.1f} fps")
        if f1 <= f0:
            print("[FAIL] 帧数没有增长，采集线程停了（检查 dsnoop0 是否被独占）")
            return 1
        if rate < 0.5:
            print("[FAIL] 帧率过低（<0.5 fps），分析窗跑不完，会漏掉短促吠叫")
            return 1

    print("== /v1/voice/latest ==")
    rec = get("/v1/voice/latest")
    print(json.dumps(rec, ensure_ascii=False))

    fails, warns = check_latest(rec)
    for w in warns:
        print(f"[WARN] {w}")
    if fails:
        for f in fails:
            print(f"[FAIL] {f}")
        print(f"\n结果：{len(fails)} 项不通过")
        return 1

    print("\n结果：全部通过，前端可直接消费")
    print(f"       预期上屏：cls={rec['cls']}({rec['cls_cn']}) "
          f"state={rec['state']}({rec['state_cn']}) "
          f"conf={rec['conf']} source={rec['source']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
