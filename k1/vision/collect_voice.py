#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 K1 板子上采集真实宠物叫声，并按当前分类器现场打分。

为什么必须有这一步：
    gen_dataset.py 造的是**合成**样本，它的频谱比真实发声干净，所以
    eval_dataset.py 给出的 89%（clean 档）是**上界**，不是现场表现。
    只有用真猫真狗录下来的声音，才能回答「到现场到底准不准」。

两种用法：

  ① 采集
     python3 collect_voice.py --label meow --count 20
     每按一次回车录一条（默认 2.5 秒），录完自动存档到
     datasets/real/<label>/<label>_<序号>.wav

  ② 现场打分（采完之后跑）
     python3 collect_voice.py --check
     用当前判据跑一遍已采集的真实样本，输出每类通过率与错例，
     这是判断「阈值要不要按真实数据重标」的唯一依据。

标签取值与分类器一致：bark howl growl meow purr hiss scream human noise silence
（human / noise / silence 也要采：它们是误报的主要来源，不采就没法量化误报率。）

注意：麦克风是共享的。采集前建议先停掉 ASR 与叫声服务，避免两边抢设备；
本脚本用 dsnoop0，理论上可以共存，但采集时人别说话。
"""
import argparse
import os
import subprocess
import sys
import time
import wave
from collections import defaultdict

import numpy as np

import petvoice_service as P

HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.join(HERE, "datasets", "real")

LABELS = ["bark", "howl", "growl", "meow", "purr", "hiss", "scream",
          "human", "noise", "silence"]


def record(path, seconds, device):
    """用 arecord 抓一段存成 16 kHz 单声道 wav。"""
    cmd = ["arecord", "-D", device, "-f", "S16_LE", "-r", str(P.SR),
           "-c", "1", "-d", str(seconds), "-q", path]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "ignore").strip()
        raise RuntimeError(f"arecord 失败（{device}）：{err}")
    return path


def read_wav(path):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch)[:, 0]
    if sr != P.SR:
        t = np.linspace(0, len(x) - 1, int(len(x) * P.SR / sr))
        x = np.interp(t, np.arange(len(x)), x).astype(np.float32)
    return x


def windows(x):
    win, hop = int(P.WIN_SEC * P.SR), int(P.HOP_SEC * P.SR)
    if len(x) < win:
        return [np.pad(x, (0, win - len(x)))]
    return [x[i:i + win] for i in range(0, len(x) - win + 1, hop)]


def do_collect(args):
    d = os.path.join(REAL, args.label)
    os.makedirs(d, exist_ok=True)
    existing = [f for f in os.listdir(d) if f.endswith(".wav")]
    start = len(existing)

    print(f"标签：{args.label}   每条 {args.seconds} 秒   目标 {args.count} 条")
    print("提示：人别说话；宠物发声时按回车。输入 q 回车可提前结束。\n")

    got = 0
    while got < args.count:
        s = input(f"[{got + 1}/{args.count}] 回车开始录 {args.seconds}s（q 退出）: ")
        if s.strip().lower() == "q":
            break
        name = f"{args.label}_{start + got:03d}.wav"
        path = os.path.join(d, name)
        try:
            record(path, args.seconds, args.device)
        except RuntimeError as e:
            print(f"  [失败] {e}")
            print("  检查：arecord -l 看声卡；服务是否占着设备")
            return 1

        # 录完立刻用当前判据看一眼，方便现场判断这条样本有没有采到
        x = read_wav(path)
        f = P.features_of(windows(x)[0])
        cls, conf, _ = P.Classifier(None).classify(f)
        mark = "✓" if cls == args.label else "✗"
        print(f"  {mark} {name}  当前判为 {cls}({conf:.2f})  "
              f"db={f['rms_max_db']:.1f} f0={f['f0_med']:.0f} cen={f['centroid']:.0f}")
        got += 1

    print(f"\n已存 {got} 条 → {d}")
    print("采够各类之后跑：python3 collect_voice.py --check")
    return 0


def do_check(args):
    if not os.path.isdir(REAL):
        print(f"[失败] 还没有采集数据：{REAL} 不存在")
        print("先跑：python3 collect_voice.py --label meow --count 20")
        return 1

    clf = P.Classifier(None)
    ok = defaultdict(int)
    total = defaultdict(int)
    details = defaultdict(list)

    for label in sorted(os.listdir(REAL)):
        d = os.path.join(REAL, label)
        if not os.path.isdir(d):
            continue
        for fn in sorted(f for f in os.listdir(d) if f.endswith(".wav")):
            x = read_wav(os.path.join(d, fn))
            preds = [clf.classify(P.features_of(w))[0] for w in windows(x)]
            # 与线上一致：多窗多数表决
            top = max(set(preds), key=preds.count)
            total[label] += 1
            if top == label:
                ok[label] += 1
            else:
                details[label].append((fn, top, preds))

    if not total:
        print("[失败] 采集目录里没有 wav")
        return 1

    print(f"{'真实类别':<10}{'样本':>6}{'判对':>6}{'通过率':>9}")
    hit = sum(ok.values())
    cnt = sum(total.values())
    for label in sorted(total):
        r = ok[label] / total[label]
        flag = "  ← 需要重标阈值" if r < 0.7 else ""
        print(f"{label:<10}{total[label]:>6}{ok[label]:>6}{r:>9.1%}{flag}")
    print(f"{'合计':<10}{cnt:>6}{hit:>6}{hit / cnt:>9.1%}")

    bad = {k: v for k, v in details.items() if v}
    if bad:
        print("\n错例（用于判断该调哪条判据）")
        for label, items in bad.items():
            for fn, top, preds in items[:5]:
                print(f"  {label}/{fn} → 判成 {top}  各窗={','.join(preds)}")

    print("\n说明：通过率 < 70% 的类别，说明合成样本标出来的阈值在真实数据上不成立，"
          "\n      需要用这批真实样本重新看特征分布（eval_dataset.py 的分位数表同款方法）。")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="meow", choices=LABELS)
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--seconds", type=int, default=2, help="每条录制时长")
    ap.add_argument("--device", default="dsnoop0")
    ap.add_argument("--check", action="store_true", help="对已采集数据打分")
    args = ap.parse_args()

    if not os.path.exists(os.path.join(HERE, "petvoice_service.py")):
        print("[失败] 请把本脚本放在 petvoice_service.py 同目录")
        return 1
    return do_check(args) if args.check else do_collect(args)


if __name__ == "__main__":
    sys.exit(main())
