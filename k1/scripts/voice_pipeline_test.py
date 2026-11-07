#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""叫声链路端到端自测：侧车 + 规则分类器，同进程内跑完。

为什么不用两个后台进程：Windows 上后台进程会被会话清理杀掉，测试结果不可信；
板端又不好一边跑 systemd 一边改参数。所以这里在**一个进程内**把侧车的
HTTPServer 起在子线程，然后用真实的客户端类去调 —— 走的是和线上完全一致的
HTTP 路径（序列化、超时、代理绕过、返回值解析全都覆盖），只是不跨进程。

验证四件事：
  1) PannsRemote 能连上、能解析返回值、把 float32 正确转成 int16 发出去
  2) 侧车对真实狗叫给出 bark
  3) 规则分类器对同一段音频给什么（决定方案 A 的"筛子"会不会漏）
  4) 两者的耗时，以及 confirm 模式下的实际触发率

用法：
    python3 voice_pipeline_test.py                      # 只用仓库内的真狗叫
    python3 voice_pipeline_test.py a.wav b.wav          # 追加样本
"""
import importlib.util
import sys
import threading
import time
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "k1-vision"))


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sc = load_mod("panns_sidecar", ROOT / "k1-vision/deploy/panns_sidecar.py")
pv = load_mod("petvoice_service", ROOT / "k1-vision/petvoice_service.py")

MODEL = ROOT / "models/panns/MobileNetV2_32k_int8.onnx"
LABELS = ROOT / "models/panns/labels.csv"


def read_wav(path, want_sr=16000):
    with wave.open(str(path), "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != want_sr:
        n = int(round(x.size * want_sr / sr))
        x = np.interp(np.arange(n) * (sr / want_sr), np.arange(x.size), x)
    return x.astype(np.float32)


def main():
    extra = sys.argv[1:]
    # 必须用 16 kHz 版：麦克风原生就是 16 kHz，而 real_dog.wav 是 44.1 kHz。
    # 若让本脚本对它做 44.1→16 的 np.interp 降采样（无抗混叠），再叠加侧车的
    # 16→32 上采样，两次劣化会把一段清楚的犬吠变成 noise 0.01 —— 这是测试
    # 方法的问题，不是模型的问题。16k 版由 scipy resample_poly 生成一次，
    # 等价于板端 ALSA 的 plug: 重采样。
    samples = [("真实狗叫(16k)", ROOT / "models/panns/real_dog_16k.wav")]
    samples += [(Path(p).name, Path(p)) for p in extra]

    # ---- 起侧车（子线程）
    eng = sc.PannsEngine(str(MODEL), str(LABELS))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), sc.make_handler(eng))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"侧车已起 127.0.0.1:{port}  加载 {eng.load_ms:.0f} ms")

    # ---- 用线上同一个客户端类去连
    t = time.time()
    cli = pv.PannsRemote(f"http://127.0.0.1:{port}")
    print(f"客户端握手 {(time.time() - t) * 1000:.0f} ms  "
          f"model={cli.model} 阈值={cli.threshold}")

    clf = pv.Classifier()
    print(f"规则分类器 mode={clf.mode}\n")

    print(f"{'样本':14s} {'规则判定':>12s} {'规则分':>6s} {'侧车判定':>10s} "
          f"{'侧车分':>6s} {'bark组':>6s} {'ms':>6s}  confirm")
    print("-" * 84)
    fired = 0
    for name, path in samples:
        x = read_wav(path)
        # 上下文要取「能量最大的 2 s」，而不是文件最后 2 s。
        # 实时场景里，规则分类器判出叫声的瞬间，叫声就在最近 2 s 内；
        # 而测试文件（real_dog.wav）的吠叫落在 2~3 s，末尾 2 s 是静音——
        # 直接喂末尾会得出 noise 0.01 的假结论（踩过一次）。
        n = int(pv.SR * pv.PANNS_CTX_SEC)
        if x.size > n:
            c = np.concatenate(([0.0], np.cumsum(x.astype(np.float64) ** 2)))
            e = c[n:] - c[:-n]
            s0 = int(np.argmax(e))
            x = x[s0:s0 + n]
        f = pv.features_of(x[-int(pv.SR * pv.WIN_SEC):])
        r_cls, r_conf, _ = clf.classify(f)
        try:
            p_cls, p_conf, groups = cli.classify(x[-int(pv.SR * pv.PANNS_CTX_SEC):])
        except Exception as e:                        # noqa: BLE001
            print(f"{name:14s} 侧车失败 {e}")
            continue
        ok = p_cls == pv.CLS_NOISE
        will = (not ok) if True else False
        # 方案 A：规则判出宠物类才送模型。这里只看"会不会送"
        send = r_cls in pv.PET_CLASSES
        fired += send
        print(f"{name:14s} {r_cls:>12s} {r_conf:6.2f} {p_cls:>10s} {p_conf:6.2f} "
              f"{groups.get('bark', 0):6.2f} {cli.last_ms:6.0f}  "
              f"{'送' if send else '不送'}")

    n = len(samples)
    print(f"\n方案 A 触发率 {fired}/{n}   侧车调用 {cli.calls} 次  失败 {cli.fails} 次")
    srv.shutdown()


if __name__ == "__main__":
    main()
