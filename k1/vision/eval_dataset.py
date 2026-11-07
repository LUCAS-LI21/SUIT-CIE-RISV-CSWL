#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""叫声识别分类器标定：跑数据集，出混淆矩阵与分档指标。

它直接复用线上服务里的 features_of / Classifier，不另写一套——否则测的
东西和跑的东西不是同一个，数字再漂亮也没意义。

两个粒度都报：
    窗级（window）—— 每个 0.96 s 分析窗一个预测。这是服务真正执行判定的粒度。
    文件级（file）  —— 对整个文件的所有窗做多数表决。这是「一段录音判对没判对」，
                      更接近用户感知，但会掩盖短样本上的错误。
只报文件级会虚高，只报窗级会低估，所以两个都给。

分档（clean/mild/field/harsh）单独统计，是因为「准确率 92%」这种单一数字
没法回答「在客厅开着空调还能不能用」。分档曲线才能。

用法：
    python3 eval_dataset.py                     # 全量
    python3 eval_dataset.py --aug clean,field   # 只看两档
    python3 eval_dataset.py --dump-errors 20    # 列出典型错例
"""
import argparse
import json
import os
import wave
from collections import Counter, defaultdict

import numpy as np

import petvoice_service as P

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "datasets", "voice")

# 固定顺序：混淆矩阵的行列按这个排，改了要同步改文档里的表
LABELS = ["silence", "human", "noise", "bark", "howl",
          "growl", "meow", "purr", "hiss", "scream"]
CN = {"silence": "静音", "human": "人声", "noise": "噪声", "bark": "吠叫",
      "howl": "长嚎", "growl": "低吼", "meow": "喵叫", "purr": "呼噜",
      "hiss": "哈气", "scream": "尖叫"}


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
    """按线上同样的窗长/窗移切分。短样本补零，否则一窗都跑不到。"""
    win, hop = int(P.WIN_SEC * P.SR), int(P.HOP_SEC * P.SR)
    if len(x) < win:
        return [np.pad(x, (0, win - len(x)))]
    out = [x[i:i + win] for i in range(0, len(x) - win + 1, hop)]
    if len(x) - win - (len(out) - 1) * hop > hop // 2:
        out.append(np.pad(x[-win:], (0, 0)))
    return out


def predict_file(clf, x):
    """返回 (文件级标签, 窗级标签列表, 置信度列表)。"""
    wins = windows(x)
    preds, confs = [], []
    for w in wins:
        f = P.features_of(w)
        cls, conf, _ = clf.classify(f)
        preds.append(cls)
        confs.append(float(conf))
    # 多数表决；平票时取平均置信度更高的那个，避免 Counter 的插入顺序影响结果
    if not preds:
        return "noise", [], []
    cnt = Counter(preds)
    top = max(cnt.values())
    tied = [c for c, n in cnt.items() if n == top]
    if len(tied) == 1:
        return tied[0], preds, confs
    best, best_conf = tied[0], -1.0
    for c in tied:
        m = np.mean([cf for p, cf in zip(preds, confs) if p == c])
        if m > best_conf:
            best, best_conf = c, m
    return best, preds, confs


def prf(conf_mat, labels):
    """按混淆矩阵算每类 precision / recall / F1（文件级）。"""
    rows = {}
    for i, t in enumerate(labels):
        tp = conf_mat[i][i]
        fn = sum(conf_mat[i]) - tp
        fp = sum(conf_mat[r][i] for r in range(len(labels))) - tp
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        rows[t] = (tp, fp, fn, prec, rec, f1)
    return rows


def print_matrix(conf_mat, labels, title):
    short = {l: CN[l] for l in labels}
    w = max(len(short[l]) for l in labels)
    head = "真实＼预测".center(w + 2)
    print(f"\n{title}")
    print(head + "".join(f"{short[l]:>{w + 1}}" for l in labels) + "   合计")
    for i, t in enumerate(labels):
        row_total = sum(conf_mat[i])
        cells = "".join(
            f"{conf_mat[i][j]:>{w + 1}}" if conf_mat[i][j] else
            f"{'·':>{w + 1}}" for j in range(len(labels)))
        print(f"{short[t]:<{w + 2}}{cells}   {row_total:>4}")
    print("（对角线为判对数，· 表示 0）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DATA)
    ap.add_argument("--aug", default="", help="逗号分隔，如 clean,field")
    ap.add_argument("--dump-errors", type=int, default=0, help="列出前 N 个错例")
    ap.add_argument("--fast", action="store_true",
                    help="用 cache_feats.py 的特征缓存（秒级，调阈值时必用）")
    args = ap.parse_args()

    keep = set(a for a in args.aug.split(",") if a)
    if args.fast:
        return main_fast(args, keep)

    mpath = os.path.join(args.data, "manifest.jsonl")
    if not os.path.exists(mpath):
        print(f"[失败] 找不到 {mpath}，先跑 gen_dataset.py")
        return 1
    with open(mpath, encoding="utf-8") as f:
        manifest = [json.loads(line) for line in f if line.strip()]

    keep = set(a for a in args.aug.split(",") if a)
    if keep:
        manifest = [m for m in manifest if m.get("aug") in keep]

    clf = P.Classifier(None)
    print(f"分类器模式：{clf.mode}   样本数：{len(manifest)}")

    n = len(LABELS)
    idx = {l: i for i, l in enumerate(LABELS)}
    conf_all = [[0] * n for _ in range(n)]
    by_aug = defaultdict(lambda: [[0] * n for _ in range(n)])

    win_total = win_hit = 0
    file_total = file_hit = 0
    errors = []

    for m in manifest:
        path = os.path.join(args.data, m["file"])
        if not os.path.exists(path):
            continue
        x = read_wav(path)
        pred, preds, confs = predict_file(clf, x)
        truth = m["label"]
        if truth not in idx or pred not in idx:
            continue

        file_total += 1
        conf_all[idx[truth]][idx[pred]] += 1
        by_aug[m.get("aug", "?")][idx[truth]][idx[pred]] += 1
        if pred == truth:
            file_hit += 1
        elif args.dump_errors and len(errors) < args.dump_errors:
            errors.append((m["file"], truth, pred, confs))

        # 窗级：只统计「标签属于该文件真实类别」的窗是否判对
        for p in preds:
            win_total += 1
            if p == truth:
                win_hit += 1

    if not file_total:
        print("[失败] 一条样本都没读到")
        return 1

    return _render(conf_all, by_aug, win_hit, win_total,
                   file_hit, file_total, errors)


def _render(conf_all, by_aug, win_hit, win_total, file_hit, file_total, errors):
    """把统计结果打印成人能读的报告。两条路径（逐文件 / 特征缓存）共用。"""
    n = len(LABELS)
    print(f"\n窗级准确率：{win_hit}/{win_total} = {win_hit / win_total:.1%}")
    print(f"文件级准确率：{file_hit}/{file_total} = {file_hit / file_total:.1%}")

    print_matrix(conf_all, LABELS, "混淆矩阵（文件级，全档合计）")

    rows = prf(conf_all, LABELS)
    print("\n每类指标（文件级）")
    print(f"{'类别':<8}{'判对':>6}{'误报':>6}{'漏检':>6}{'精确率':>9}{'召回率':>9}{'F1':>8}")
    for l in LABELS:
        tp, fp, fn, prec, rec, f1 = rows[l]
        flag = "  ← 弱" if f1 < 0.75 and (tp + fn) > 0 else ""
        print(f"{CN[l]:<8}{tp:>6}{fp:>6}{fn:>6}{prec:>9.1%}{rec:>9.1%}{f1:>8.2f}{flag}")

    # 最该先修的不是 F1 最低的类，而是「错得最多」的那一对——
    # 单个类别的低分可能只是样本少，而固定的混淆对是明确的判据缺陷。
    print("\n主要混淆对（真实 → 误判，≥2 次）")
    pairs = []
    for i, t in enumerate(LABELS):
        for j, p in enumerate(LABELS):
            if i != j and conf_all[i][j] >= 2:
                pairs.append((conf_all[i][j], t, p))
    pairs.sort(reverse=True)
    if pairs:
        for c, t, p in pairs[:8]:
            print(f"  {CN[t]} → {CN[p]}  {c} 次")
    else:
        print("  无（没有出现 2 次以上的同一混淆方向）")

    if by_aug:
        print("\n按增强档位分解（文件级准确率）")
        order = ["clean", "mild", "field", "harsh"]
        for tag in order + [t for t in by_aug if t not in order]:
            if tag not in by_aug:
                continue
            cm = by_aug[tag]
            tot = sum(sum(r) for r in cm)
            hit = sum(cm[i][i] for i in range(n))
            if tot:
                print(f"  {tag:<6}{hit:>4}/{tot:<4} {hit / tot:>6.1%}")

    if errors:
        print("\n典型错例")
        for f, t, p, confs in errors:
            print(f"  {f}  真实={CN[t]} 判成={CN[p]}  各窗置信度="
                  + ",".join(f"{c:.2f}" for c in confs))

    return 0


def main_fast(args, keep):
    """走特征缓存：只重跑 classify()，不重算特征，秒级出结果。"""
    import cache_feats

    import os as _os
    X, keys, labels, augs, files = cache_feats.load(
        _os.path.join(args.data, "feats.npz"))
    clf = P.Classifier(None)
    print(f"分类器模式：{clf.mode}   窗数：{len(X)}（特征缓存，不重算特征）")

    idx = {l: i for i, l in enumerate(LABELS)}
    n = len(LABELS)
    conf_all = [[0] * n for _ in range(n)]
    by_aug = defaultdict(lambda: [[0] * n for _ in range(n)])

    # 先逐窗分类，再按文件聚合表决——聚合方式必须与逐文件路径一致，
    # 否则两条路径给出的准确率对不上，调阈值就成了盲调
    votes = defaultdict(list)
    for i in range(len(X)):
        if keep and augs[i] not in keep:
            continue
        f = {k: float(X[i][j]) for j, k in enumerate(keys)}
        cls, conf, _ = clf.classify(f)
        votes[files[i]].append((labels[i], augs[i], cls, float(conf)))

    win_total = win_hit = 0
    file_total = file_hit = 0
    for fn, vs in votes.items():
        truth = vs[0][0]
        aug = vs[0][1]
        preds = [v[2] for v in vs]
        confs = [v[3] for v in vs]
        cnt = Counter(preds)
        top = max(cnt.values())
        tied = [c for c, k in cnt.items() if k == top]
        if len(tied) == 1:
            pred = tied[0]
        else:
            pred = max(tied, key=lambda c: np.mean(
                [cf for p, cf in zip(preds, confs) if p == c]))

        file_total += 1
        if truth in idx and pred in idx:
            conf_all[idx[truth]][idx[pred]] += 1
            by_aug[aug][idx[truth]][idx[pred]] += 1
        if pred == truth:
            file_hit += 1
        for p in preds:
            win_total += 1
            if p == truth:
                win_hit += 1

    if not file_total:
        print("[失败] 缓存里没有可用样本，先跑 gen_dataset.py 与 cache_feats.py --force")
        return 1
    return _render(conf_all, by_aug, win_hit, win_total,
                   file_hit, file_total, [])


if __name__ == "__main__":
    raise SystemExit(main())
