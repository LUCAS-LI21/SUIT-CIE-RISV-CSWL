#!/usr/bin/env python3
"""抓取视觉服务实时行为判定，用于复验 running / jumping 是否判对。

用法:
    python scripts/capture_behavior.py [持续秒数] [轮询间隔ms]

输出:
    - 实时：动作或 basis 变化时打印一行
    - 结束：各动作帧数统计 + 各动作 basis 数值中位数 + 写 CSV
"""
import json
import re
import sys
import time
import urllib.request

URL = "http://127.0.0.1:11436/v1/vision/latest"
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
INT = (float(sys.argv[2]) if len(sys.argv) > 2 else 120.0) / 1000.0
OUT = "logs/behavior_capture.csv"

# 本机 http_proxy 会污染 127.0.0.1
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

NUM = re.compile(r"([\u4e00-\u9fa5A-Za-z_]+)\s*[=:]\s*(-?\d+\.?\d*)")


def parse_basis(s):
    """把 basis 字符串拆成 {键: 浮点}，键保留中文。"""
    out = {}
    for k, v in NUM.findall(s or ""):
        try:
            out[k] = float(v)
        except ValueError:
            pass
    return out


def med(xs):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def main():
    print(f"[capture] {DUR:.0f}s @ {INT*1000:.0f}ms -> {OUT}", flush=True)
    rows = []
    last_key = None
    t0 = time.time()
    while time.time() - t0 < DUR:
        try:
            with _opener.open(URL, timeout=2.0) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print(f"[capture] 请求失败: {e}", flush=True)
            time.sleep(INT)
            continue

        el = time.time() - t0
        act = d.get("action") or d.get("label") or "?"
        basis = d.get("basis") or ""
        b = parse_basis(basis)
        key = (act, basis)
        # 只在动作变化或数值变化时打印，避免刷屏
        if key != last_key:
            kv = " ".join(f"{k}={v:g}" for k, v in b.items())
            print(f"{el:6.1f}s  {act:9s} conf={d.get('conf',0):.2f} "
                  f"n={d.get('count',0)}  {kv}", flush=True)
            last_key = key

        rows.append({
            "t": round(el, 2),
            "action": act,
            "label": d.get("label", ""),
            "conf": d.get("conf", 0),
            "count": d.get("count", 0),
            "basis": basis,
            "fps": d.get("det_fps", 0),
            **{f"b_{k}": v for k, v in b.items()},
        })
        time.sleep(INT)

    if not rows:
        print("[capture] 没抓到数据")
        return 1

    # 统计
    from collections import Counter, defaultdict
    cnt = Counter(r["action"] for r in rows)
    print("\n" + "=" * 62)
    print(f"采样 {len(rows)} 帧 / {rows[-1]['t']:.1f}s")
    print(f"{'动作':<12}{'帧数':>6}{'占比':>8}")
    for a, c in cnt.most_common():
        print(f"{a:<12}{c:>6}{c/len(rows)*100:>7.1f}%")

    # basis 关键量按动作分组
    keys = set()
    for r in rows:
        for k in r:
            if k.startswith("b_"):
                keys.add(k[2:])
    if keys:
        print("\nbasis 各动作中位数:")
        hdr = "动作".ljust(10) + "".join(k.rjust(10) for k in sorted(keys))
        print(hdr)
        by = defaultdict(list)
        for r in rows:
            by[r["action"]].append(r)
        for a, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
            line = a[:10].ljust(10)
            for k in sorted(keys):
                vals = [r.get(f"b_{k}") for r in rs if r.get(f"b_{k}") is not None]
                line += (f"{med(vals):.2f}".rjust(10) if vals else "-".rjust(10))
            print(line)

    # 写 CSV
    import csv
    import os
    os.makedirs("logs", exist_ok=True)
    cols = ["t", "action", "label", "conf", "count", "fps", "basis"] + \
           sorted(f"b_{k}" for k in keys)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\n[capture] CSV -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
