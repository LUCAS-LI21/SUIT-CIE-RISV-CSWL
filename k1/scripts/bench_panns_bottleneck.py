#!/usr/bin/env python3
"""PANNs 在 K1 上的真实瓶颈定位。

背景：petvoice.service 用 AllowedCPUs=4-7 + Nice=5，实测 last_ms=9717ms，
而 scripts/panns_k1_len.py 当年测出 1.5s 窗只要 1529ms。差 6.4 倍。
本脚本把「核绑定」和「执行提供者」两个变量拆开，逐个测。

用法（板端）：
    taskset -c 0-3 python3 bench_panns_bottleneck.py     # AI 核
    taskset -c 4-7 python3 bench_panns_bottleneck.py     # 非 AI 核
    SPACEMIT_ORT=1 LD_LIBRARY_PATH=/root/k1-ai/spacemit-ort.riscv64.2.0.6/lib \
        python3 bench_panns_bottleneck.py                # 试 SpaceMIT EP
"""
import os
import sys
import time

import numpy as np

MODEL = "/root/k1-ai/models/panns/Cnn14_16k.onnx"
WIN_SEC = 1.5
SR = 16000

import onnxruntime as ort

# SpaceMIT EP 是插件式的：不 import 这个包，get_available_providers() 里
# 永远只有 CPUExecutionProvider。vision_service.py 就是这么做的，
# 而 petvoice 的 PannsLocal 硬编码了 CPU，所以一直没吃到 AI 核。
if os.environ.get("NO_SPACEMIT") != "1":
    try:
        import spacemit_ort  # noqa: F401
    except ImportError:
        pass

print("=" * 62)
print("PID", os.getpid(), " 允许的核:", sorted(os.sched_getaffinity(0)))
print("ort", ort.__version__, " EP:", ort.get_available_providers())
print("LD_LIBRARY_PATH:", os.environ.get("LD_LIBRARY_PATH", "(空)"))


def build(threads, ep):
    so = ort.SessionOptions()
    so.intra_op_num_threads = threads
    so.inter_op_num_threads = 1
    t = time.time()
    sess = ort.InferenceSession(MODEL, so, providers=[ep])
    return sess, (time.time() - t) * 1000


# 1.5 s 窗口，tile 铺满（与线上 classify() 的终局输入一致）
n = int(SR * WIN_SEC)
rng = np.random.default_rng(0)
# 用带包络的噪声近似吠叫：纯噪声会让 log-mel 分布失真，但对测时足够
x = (rng.standard_normal(n) * 0.1).astype(np.float32)

for ep in ["CPUExecutionProvider", "SpaceMITExecutionProvider"]:
    if ep not in ort.get_available_providers() and ep != "CPUExecutionProvider":
        print(f"\n[{ep}] 不可用，跳过")
        continue
    for threads in (4, 8):
        try:
            sess, load_ms = build(threads, ep)
        except Exception as e:
            print(f"\n[{ep} x{threads}] 建会话失败: {type(e).__name__}: {e}")
            continue
        # 预热一次，排除首跑内存布局开销
        sess.run(None, {"input_audio": x[None, :]})
        ts = []
        for _ in range(3):
            t = time.time()
            sess.run(None, {"input_audio": x[None, :]})
            ts.append((time.time() - t) * 1000)
        print(f"\n[{ep} x{threads}线程] 加载 {load_ms:.0f} ms  "
              f"推理 中位 {sorted(ts)[1]:.0f} ms  三次 {[round(v) for v in ts]}")
        del sess

print("=" * 62)
