#!/usr/bin/env python3
"""实时观测「检测 → 行为 → 3568 动作」每一跳。

用法：
    .venv-train/Scripts/python.exe scripts/live_watch.py            # 一直跑，Ctrl-C 停
    .venv-train/Scripts/python.exe scripts/live_watch.py --sec 120  # 跑 120 秒

输出只打印「有变化」的行，避免刷屏：
    [K1] pet=cat behavior=walking conf=0.61 human=1
    [APP] 🎬 walking → zou --> 已下发

判据：
- 只有 [K1] 没有 [APP]  → 断在 3568 轮询或映射
- [APP] 有但 3D 不动       → 断在 Video3DView
- 长时间 [K1] absent       → 检测器没看到宠物（不是行为层的问题）
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LOCAL = "http://127.0.0.1:11436"

# 绕开系统代理，否则 502
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_HDC_CANDIDATES = [
    r"E:\HarmonyOS\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe",
]


def hdc_path():
    for p in _HDC_CANDIDATES:
        if os.path.exists(p):
            return p
    for base in (r"E:\HarmonyOS", r"C:\Program Files\Huawei"):
        for root, _dirs, files in os.walk(base):
            if "hdc.exe" in files and "toolchains" in root:
                return os.path.join(root, "hdc.exe")
    return None


HDC = hdc_path()


def get_json(url, timeout=4.0):
    try:
        with _OPENER.open(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def hdc_shell(cmd, timeout=12):
    if not HDC:
        return ""
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"
    try:
        out = subprocess.run([HDC, "shell", cmd], capture_output=True,
                             timeout=timeout, env=env)
        return out.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def t():
    return time.strftime("%H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sec", type=float, default=0, help="运行秒数，0=不限")
    ap.add_argument("--hz", type=float, default=2.0, help="采样频率")
    a = ap.parse_args()

    print(f"[{t()}] live_watch 启动  hdc={HDC}")
    print(f"[{t()}] 只看变化行。Ctrl-C 结束。\n")

    last_k1 = None
    last_app = None
    t0 = time.time()
    n = 0

    while True:
        if a.sec and time.time() - t0 > a.sec:
            break
        n += 1

        d = get_json(f"{LOCAL}/v1/vision/latest")
        if d:
            human = d.get("human") or {}
            key = (d.get("pet"), d.get("label"), round(float(d.get("action_conf") or 0), 2),
                   human.get("count"))
            if key != last_k1:
                last_k1 = key
                print(f"[{t()}] [K1 ] pet={d.get('pet') or '-':<5} "
                      f"label={d.get('label'):<9} conf={float(d.get('conf') or 0):.2f} "
                      f"act_conf={float(d.get('action_conf') or 0):.2f} "
                      f"human={human.get('count', 0)} fps={d.get('fps')}")
        else:
            if last_k1 != "down":
                last_k1 = "down"
                print(f"[{t()}] [K1 ] !! 拿不到 /v1/vision/latest")

        # 前端的动作命令只在行为变化时打，抓最近 6 秒足够
        raw = hdc_shell("hilog -x 2>/dev/null | grep -E '🎬|收到 .* 只宠物' | tail -3")
        for line in raw.splitlines():
            m = re.search(r"\[HomeTab\].*?🎬.*", line) or re.search(r"📋.*收到.*", line)
            if not m:
                continue
            txt = m.group(0).strip()
            if txt != last_app:
                last_app = txt
                print(f"[{t()}] [APP] {txt}")

        time.sleep(max(0.2, 1.0 / a.hz))

    print(f"\n[{t()}] 结束，共采样 {n} 次")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止")
