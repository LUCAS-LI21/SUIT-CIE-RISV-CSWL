"""K1 板端 PANNs 冒烟测试：加载模型 → 喂一段狗叫 → 打 top3 与关键类别分数。

用法（板端）：
    python3 /tmp/panns_k1_test.py /tmp/real_dog.wav 2.0 2.6
不带参数则跑默认两组用例（整段 / 出声段），用来看「静音稀释」效应。
"""
import csv
import sys
import time
import wave

import numpy as np
import onnxruntime as ort

BASE = "/root/k1-ai/models/panns"
SR = 16000


def load_labels():
    rows = list(csv.DictReader(open(f"{BASE}/labels.csv", encoding="utf-8")))
    rows.sort(key=lambda r: int(r["index"]))
    names = [r["display_name"] for r in rows]
    return names, {n: i for i, n in enumerate(names)}


def read_wav(p):
    with wave.open(p, "rb") as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        raw = w.readframes(n)
    x = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    return x, sr


def to_clip(x, sr, dur=10.0):
    """重采样到 16k 并补齐到 dur 秒（不足则 tile）。PANNs 是 10s clip 分类器。"""
    n = int(round(len(x) * SR / sr))
    x = np.interp(np.linspace(0, len(x) - 1, n),
                  np.arange(len(x)), x).astype("float32")
    need = int(SR * dur)
    if len(x) < need:
        x = np.tile(x, int(np.ceil(need / max(1, len(x)))))
    return x[:need]


def main() -> int:
    names, kd = load_labels()
    t = time.time()
    sess = ort.InferenceSession(f"{BASE}/Cnn14_16k.onnx",
                               providers=["CPUExecutionProvider"])
    print(f"模型加载 {(time.time() - t) * 1000:.0f} ms  {sess.get_providers()}")

    if len(sys.argv) >= 2:
        cases = [(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 0,
                  float(sys.argv[3]) if len(sys.argv) > 3 else 99)]
    else:
        cases = [("/tmp/real_dog.wav", 0, 99), ("/tmp/real_dog.wav", 2.0, 2.6)]

    for path, a, b in cases:
        x, sr = read_wav(path)
        seg = x[int(a * sr):int(b * sr)]
        if not len(seg):
            print(f"{path} {a}-{b}s: 空段，跳过")
            continue
        t = time.time()
        sc = sess.run(None, {"input_audio": to_clip(seg, sr)[None, :]})[0][0]
        ms = (time.time() - t) * 1000
        idx = np.argsort(-sc)[:3]
        top = ", ".join(f"{names[i]}={sc[i]:.2f}" for i in idx)
        keys = " ".join(f"{k}={sc[kd[k]]:.2f}" for k in
                        ("Dog", "Bark", "Growling", "Cat", "Meow", "Purr", "Hiss", "Speech", "Silence")
                        if k in kd)
        print(f"{a}-{b}s  {ms:.0f} ms | top3 {top}")
        print(f"        {keys}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
