#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把数据集的特征抽取结果缓存成 npz，供快速调阈值。

为什么需要：一次全量特征抽取（400 条、每条 1~4 个窗）要 2~3 分钟，而调阈值
是「改一个数 → 重看混淆矩阵」的高频动作。缓存之后 classify() 只需要几微秒，
一轮从分钟级降到秒级，才可能真的按数据把阈值调对。

特征本身只跟音频有关，与分类规则无关，所以缓存是安全的：
改规则不必重算特征，只有换数据集或改 features_of 时才需要 --force。

用法：
    python3 cache_feats.py            # 没有缓存才建
    python3 cache_feats.py --force    # 强制重建
"""
import argparse
import json
import os

import numpy as np

import petvoice_service as P
import eval_dataset as E

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "datasets", "voice")
CACHE = os.path.join(DATA, "feats.npz")


def build(data=DATA, cache=None):
    mpath = os.path.join(data, "manifest.jsonl")
    with open(mpath, encoding="utf-8") as f:
        manifest = [json.loads(line) for line in f if line.strip()]

    rows, labels, augs, files = [], [], [], []
    for m in manifest:
        path = os.path.join(data, m["file"])
        if not os.path.exists(path):
            continue
        x = E.read_wav(path)
        for w in E.windows(x):
            rows.append(P.features_of(w))
            labels.append(m["label"])
            augs.append(m.get("aug", "?"))
            files.append(m["file"])

    # features_of 的返回值里混了非标量项（logmel 是 2D 矩阵，给 ONNX 分类器用的）。
    # 缓存只保留标量特征：那些才是规则分类器真正读的，也才能压成一张表。
    keys = sorted(k for k, v in rows[0].items()
                  if isinstance(v, (int, float, bool))
                  or (isinstance(v, np.generic) and np.ndim(v) == 0))
    X = np.array([[float(r.get(k, 0.0)) for k in keys] for r in rows], dtype=np.float32)
    cache = cache or os.path.join(data, "feats.npz")
    np.savez_compressed(cache, X=X, keys=np.array(keys),
                        label=np.array(labels), aug=np.array(augs),
                        fpath=np.array(files))
    print(f"[ok] 缓存 {len(rows)} 个窗 × {len(keys)} 维 → {cache}")
    return cache


def load(cache=None):
    d = np.load(cache or CACHE, allow_pickle=False)
    return d["X"], list(d["keys"]), d["label"], d["aug"], d["fpath"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--data", default=DATA)
    a = ap.parse_args()
    if a.force or not os.path.exists(os.path.join(a.data, "feats.npz")):
        build(a.data)
    else:
        X, keys, *_ = load()
        print(f"缓存已存在：{X.shape[0]} 窗 × {len(keys)} 维")
