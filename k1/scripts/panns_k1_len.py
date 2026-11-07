"""K1 上 PANNs 的「输入长度 vs 耗时 vs 准确率」标定。

背景：Cnn14 在 K1 上 10s clip 要 8.5 秒（RISC-V 无向量加速）。PANNs 的
log-mel 帧数随输入长度线性增长，所以短窗能大幅省时。但训练分布是 10 秒，
太短会掉点。这里量出拐点在哪。

用法（板端）：python3 /tmp/panns_k1_len.py
"""
import csv
import time
import wave

import numpy as np
import onnxruntime as ort

BASE = "/root/k1-ai/models/panns"
SR = 16000


def read_wav(p):
    with wave.open(p, "rb") as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        raw = w.readframes(n)
    x = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    return x, sr


def tile_to(x, dur):
    need = int(SR * dur)
    if len(x) < need:
        x = np.tile(x, int(np.ceil(need / max(1, len(x)))))
    return x[:need]


def main():
    rows = list(csv.DictReader(open(f"{BASE}/labels.csv", encoding="utf-8")))
    rows.sort(key=lambda r: int(r["index"]))
    names = [r["display_name"] for r in rows]
    kd = {n: i for i, n in enumerate(names)}

    sess = ort.InferenceSession(f"{BASE}/Cnn14_16k.onnx",
                               providers=["CPUExecutionProvider"])
    w, sr = read_wav("/tmp/real_dog.wav")
    seg = w[int(2.0 * sr):int(2.6 * sr)]
    n = int(round(len(seg) * SR / sr))
    seg = np.interp(np.linspace(0, len(seg) - 1, n), np.arange(len(seg)), seg).astype("float32")

    print(f"样本 0.6 s 真狗叫 | {'窗口':>6} {'耗时':>8} {'Dog':>6} {'Bark':>6} {'Silence':>8}  top1")
    for dur in (1.0, 2.0, 3.0, 5.0, 10.0):
        x = tile_to(seg, dur)[None, :]
        sess.run(None, {"input_audio": x})          # 预热一次，避免首跑含初始化
        t = time.time()
        sc = sess.run(None, {"input_audio": x})[0][0]
        ms = (time.time() - t) * 1000
        top = int(np.argmax(sc))
        print(f"{'':20} {dur:>4.1f}s {ms:>7.0f}ms {sc[kd['Dog']]:>6.2f} "
              f"{sc[kd['Bark']]:>6.2f} {sc[kd['Silence']]:>8.2f}  {names[top]}")

    # 线程数影响（RISC-V 上未必是越多越快，TCM/缓存竞争可能反向）
    print("\n线程数 vs 耗时（2s 窗）：")
    x = tile_to(seg, 2.0)[None, :]
    for nt in (1, 2, 4, 8):
        so = ort.SessionOptions()
        so.intra_op_num_threads = nt
        so.inter_op_num_threads = 1
        s2 = ort.InferenceSession(f"{BASE}/Cnn14_16k.onnx", so,
                                  providers=["CPUExecutionProvider"])
        s2.run(None, {"input_audio": x})
        t = time.time()
        s2.run(None, {"input_audio": x})
        print(f"  threads={nt}: {(time.time()-t)*1000:.0f} ms")


if __name__ == "__main__":
    main()
