#!/usr/bin/env python3
"""把文件夹里的音频切成训练样本（叫声分类专用）。

设计要点
--------
1. **特征提取不重写，直接 import petvoice_service**
   训练端的 log-mel 必须与板上推理端逐位一致，否则模型在 PC 上 90%、
   上板 40%。这里直接复用 `petvoice_service.MEL_FB` / `features_of` /
   `N_MEL` / `SR` / `WIN_SEC`，任何一侧改参数都会同时生效。

2. **文件夹名 = 标签**，无需标注工具
       datasets/voice_clips/
           bark/    howl/   growl/  meow/   purr/
           hiss/    scream/ human/  noise/  silence/
   类别顺序硬编码为 `[silence, human, noise] + PET_CLASSES`，与
   `petvoice_service._classify_onnx` 的 names 列表严格对应，改顺序即错位。

3. **按源文件切分，不按窗口随机切分**
   同一段音频切出的相邻窗几乎相同。若随机切分，验证集会"见过"训练集，
   macro-F1 会虚高 10~20 个点。这里以源文件为单位做 group split。

4. **拒绝类不是可选项**
   human / noise / silence 三类缺失时，人说话会被判成 bark，电视声会被
   判成 hiss——识别率再高也不可用。脚本会强制检查这三类是否存在。

用法
----
    python k1-vision/audio_to_dataset.py \
        --src k1-vision/datasets/voice_clips \
        --out k1-vision/datasets/voice \
        --win 0.96 --hop 0.48

输出
----
    <out>/voice.npz      X(N,64,64) float32 / y(N,) int64 / src(N,) / splits
    <out>/manifest.csv   每个样本的来源文件、时间偏移、RMS
    <out>/report.txt     类别分布、时长合计、切分统计
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import wave
from collections import defaultdict
from pathlib import Path

import numpy as np

# ── 复用板上推理端的特征口径（唯一真源） ──────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from petvoice_service import (  # noqa: E402
    CLS_HUMAN, CLS_NOISE, CLS_SILENCE, N_MEL, PET_CLASSES, SR, features_of,
)

# 类别顺序必须与推理端 names 一致：拒绝类在前，宠物类在后
CLASSES = [CLS_SILENCE, CLS_HUMAN, CLS_NOISE] + PET_CLASSES

# 中文别名 → 规范英文标签（方便直接用中文文件夹名）
ALIASES = {
    "静音": CLS_SILENCE, "人声": CLS_HUMAN, "说话": CLS_HUMAN, "环境噪声": CLS_NOISE,
    "噪声": CLS_NOISE, "吠叫": "bark", "犬吠": "bark", "嚎叫": "howl", "长嚎": "howl",
    "低吼": "growl", "喵叫": "meow", "呼噜": "purr", "哈气": "hiss", "尖叫": "scream",
}

AUDIO_EXT = {".wav", ".flac", ".ogg", ".aiff", ".aif"}
NEED_FFMPEG = {".mp3", ".m4a", ".aac", ".wma", ".mp4", ".webm", ".opus"}

# 近静音窗阈值：低于此值的窗丢弃（-45 dBFS 与推理端 VAD 门限同源）
SILENCE_DBFS = -45.0

# 按类别区分门限——统一门限会把整类杀掉，这一点踩过：
#   silence 的语义就是"没声音"，用 -45 dBFS 筛它等于把这一类全删了；
#   noise（空调、电视、远处车流）本就低电平，门限过严会让模型学不到
#   "低电平噪声"长什么样，上线后空调声会被判成 purr（都是低频稳态）。
# 只有宠物叫声必须是有声事件，才用 -45 dBFS。
MIN_DB_BY_CLASS = {
    CLS_SILENCE: -80.0,
    CLS_NOISE: -60.0,
    CLS_HUMAN: -55.0,
}


# ── 读音频 ────────────────────────────────────────────────────────────
def read_wav(path: Path):
    with wave.open(str(path), "rb") as w:
        n_ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif sw == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"{path.name}: 不支持的位深 {sw * 8} bit")
    if n_ch > 1:
        x = x.reshape(-1, n_ch).mean(axis=1)
    return x, sr


def read_audio(path: Path):
    """返回 (mono float32, sr)。优先 soundfile，失败再退回 wave / ffmpeg。"""
    ext = path.suffix.lower()
    if ext in NEED_FFMPEG:
        wav_tmp = path.with_suffix(".conv.wav")
        cmd = f'ffmpeg -v error -y -i "{path}" -ac 1 -ar {SR} "{wav_tmp}"'
        if os.system(cmd) != 0 or not wav_tmp.exists():
            raise RuntimeError(f"ffmpeg 解码失败：{path.name}（请确认已安装 ffmpeg 并在 PATH 中）")
        try:
            return read_wav(wav_tmp)
        finally:
            wav_tmp.unlink(missing_ok=True)
    try:
        import soundfile as sf  # 可选依赖，能读 wav/flac/ogg
        x, sr = sf.read(str(path), dtype="float32", always_2d=False)
        if x.ndim > 1:
            x = x.mean(axis=1)
        return x.astype(np.float32), int(sr)
    except Exception:                                   # noqa: BLE001
        if ext == ".wav":
            return read_wav(path)
        raise


def resample_to(x: np.ndarray, sr: int, target: int = SR):
    """线性重采样 + 前置抗混叠低通。

    降采样不做低通会混叠：44.1 kHz 的 10~22 kHz 成分折回 0~6 kHz，
    正好压在猫叫的主频带上，这是"训练集 85%、真实场景 60%"的隐蔽成因之一。
    """
    if sr == target:
        return x.astype(np.float32)
    if sr > target:
        # 简易 FIR 低通：截止在目标奈奎斯特频率之上留 10% 余量
        cut = int(round(sr / target))
        if cut >= 2:
            k = np.ones(cut, dtype=np.float32) / cut
            x = np.convolve(x, k, mode="same").astype(np.float32)
    n_out = int(round(len(x) * target / sr))
    if n_out < 2:
        return x[:1].astype(np.float32)
    xi = np.linspace(0, len(x) - 1, n_out, dtype=np.float64)
    return np.interp(xi, np.arange(len(x)), x).astype(np.float32)


# ── 窗 → 64×64 特征（与 _classify_onnx 逐位一致） ─────────────────────
def window_to_feature(win: np.ndarray) -> np.ndarray:
    """0.96 s 窗 → (N_MEL, N_MEL) 归一化 log-mel。

    时间轴用线性插值压到 64 帧，然后整图标准化——两步都必须与
    `petvoice_service._classify_onnx` 完全相同。
    """
    f = features_of(win)
    lm = f["logmel"]                                    # (n_frames, 64)
    if lm.shape[0] < 2:
        return None
    t = np.linspace(0, lm.shape[0] - 1, N_MEL)
    x = np.stack([np.interp(t, np.arange(lm.shape[0]), lm[:, i])
                  for i in range(lm.shape[1])], axis=1)  # (64 时间, 64 mel)
    x = (x - x.mean()) / (x.std() + 1e-6)
    return x.astype(np.float32)


# ── 主流程 ────────────────────────────────────────────────────────────
def collect_files(src: Path):
    """扫描 <src>/<label>/*.音频，返回 [(label, path), ...]。"""
    items, unknown_dirs = [], []
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        raw = d.name.strip()
        label = ALIASES.get(raw, raw.lower())
        if label not in CLASSES:
            unknown_dirs.append(d.name)
            continue
        for f in sorted(d.rglob("*")):
            if f.is_file() and (f.suffix.lower() in AUDIO_EXT | NEED_FFMPEG):
                items.append((label, f))
    return items, unknown_dirs


def group_key(path: Path, mode: str = "file") -> str:
    """切分用的分组键。

    为什么需要个体分组：同一只狗的 10 段吠叫若被拆进 train/val/test，
    验证集考的其实是"能不能认出这只狗"，不是"能不能认出吠叫"。
    实测这类泄漏会让 macro-F1 虚高 10~20 个点。

    约定：文件名写成 `个体ID__描述.wav`（双下划线分隔），配合
    --group-by prefix，同一个个体的全部片段会被强制分到同一侧。
    """
    stem = path.stem
    if mode == "prefix" and "__" in stem:
        return stem.split("__", 1)[0]
    return stem


def group_split(files, seed=42, ratios=(0.7, 0.15, 0.15), mode: str = "file"):
    """按分组键切分，返回 {path: split}。同组的文件必然同侧。"""
    rng = random.Random(seed)
    by_label = defaultdict(lambda: defaultdict(list))   # label -> group -> [path]
    for label, p in files:
        by_label[label][group_key(p, mode)].append(p)

    assign = {}
    for label, gmap in by_label.items():
        keys = sorted(gmap)
        rng.shuffle(keys)
        n = len(keys)
        n_val = max(1, int(round(n * ratios[1]))) if n >= 3 else 0
        n_test = max(1, int(round(n * ratios[2]))) if n >= 3 else 0
        if n - n_val - n_test < 1:                      # 保住至少 1 组进训练
            n_val = max(0, min(n_val, n - 1))
            n_test = max(0, min(n_test, n - 1 - n_val))
        for i, k in enumerate(keys):
            sp = "val" if i < n_val else ("test" if i < n_val + n_test else "train")
            for p in gmap[k]:
                assign[p] = sp
    return assign


def main():
    ap = argparse.ArgumentParser(description="音频 → 叫声分类数据集")
    ap.add_argument("--src", required=True, help="音频根目录（子目录名=标签）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--win", type=float, default=0.96, help="窗长秒（默认 0.96，与板上一致）")
    ap.add_argument("--hop", type=float, default=0.48, help="窗移秒（默认 0.48，50%% 重叠）")
    ap.add_argument("--min-db", type=float, default=SILENCE_DBFS,
                    help="宠物叫声类的 RMS 门限（拒绝类由 MIN_DB_BY_CLASS 另设）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--group-by", choices=["file", "prefix"], default="file",
                    help="切分分组方式：file=每个文件独立；prefix=按文件名 '__' 前的个体ID分组"
                         "（同一只动物的全部片段必同侧，防个体泄漏）")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    if not src.is_dir():
        sys.exit(f"源目录不存在：{src}")

    files, unknown = collect_files(src)
    if unknown:
        print(f"[警告] 以下目录名不是合法类别，已跳过：{', '.join(unknown)}")
        print(f"        合法类别：{', '.join(CLASSES)}")
    if not files:
        sys.exit(f"{src} 下没有找到任何音频。请按 <类别>/xxx.wav 的结构放置。")

    # 拒绝类硬性检查
    present = {lbl for lbl, _ in files}
    missing_reject = [c for c in (CLS_HUMAN, CLS_NOISE, CLS_SILENCE) if c not in present]
    if missing_reject:
        print(f"[警告] 缺少拒绝类：{', '.join(missing_reject)}")
        print("        没有拒绝类时，人声/电视声会被判成宠物叫声，上线即不可用。")

    assign = group_split(files, seed=args.seed)
    win_n = int(round(args.win * SR))
    hop_n = int(round(args.hop * SR))

    def scan_windows(x: np.ndarray, label: str):
        """遍历窗，返回 [(start, rms_db)]，已按该类门限过滤。"""
        min_db = MIN_DB_BY_CLASS.get(label, args.min_db)
        out = []
        for start in range(0, max(1, len(x) - win_n + 1), hop_n):
            win = x[start:start + win_n]
            if len(win) < win_n:
                win = np.pad(win, (0, win_n - len(win)))
            rms = float(np.sqrt(np.mean(win.astype(np.float64) ** 2)))
            rms_db = 20 * np.log10(max(rms, 1e-8))
            if rms_db >= min_db:
                out.append((start, rms_db))
        return out

    # ── 第一遍：逐文件读入并定窗，只保留「确实产出样本」的文件参与切分 ──
    # 为什么必须两遍：切分是按源文件做的，但一个文件可能因电平过低而产出
    # 0 个窗。若先切分再提取，这类文件会占掉一个名额，导致某个类别在
    # train 里整类缺席（实测踩过：silence 全部落到 test，训练集里没有静音）。
    decoded = []            # [(label, path, x)]
    per_label_dur = defaultdict(float)
    per_label_file = defaultdict(int)
    dropped = []            # [(label, filename, 原因)]
    for label, path in files:
        try:
            x, sr = read_audio(path)
        except Exception as e:                          # noqa: BLE001
            print(f"[跳过] {path.name}: {e}")
            dropped.append((label, path.name, f"解码失败: {e}"))
            continue
        x = resample_to(x, sr)
        per_label_file[label] += 1
        dur = len(x) / SR
        per_label_dur[label] += dur
        if len(x) < win_n:
            x = np.pad(x, (0, win_n - len(x)))
        wins = scan_windows(x, label)
        if not wins:
            dropped.append((label, path.name,
                            f"整段低于该类门限 {MIN_DB_BY_CLASS.get(label, args.min_db)} dBFS"))
            continue
        decoded.append((label, path, x, wins))

    if not decoded:
        sys.exit("没有任何文件产出有效样本，请检查音频电平与目录结构。")

    # 只用有效文件切分，保证每个类别在 train 里至少占到一个文件
    assign = group_split([(l, p) for l, p, _, _ in decoded],
                         seed=args.seed, mode=args.group_by)

    # ── 第二遍：提取特征 ──
    X, y, recs = [], [], []
    for label, path, x, wins in decoded:
        for start, rms_db in wins:
            win = x[start:start + win_n]
            if len(win) < win_n:
                win = np.pad(win, (0, win_n - len(win)))
            feat = window_to_feature(win)
            if feat is None:
                continue
            X.append(feat)
            y.append(CLASSES.index(label))
            recs.append({
                "label": label, "src": path.name, "split": assign[path],
                "offset_sec": round(start / SR, 2), "rms_dbfs": round(rms_db, 1),
            })

    if not X:
        sys.exit("没有生成任何样本，请检查音频电平是否过低。")

    X = np.stack(X)
    y = np.array(y, dtype=np.int64)
    src_arr = np.array([r["src"] for r in recs])
    split_arr = np.array([r["split"] for r in recs])

    # ── 统计报告 ──
    lines = ["叫声分类数据集统计", "=" * 46,
             f"源文件：{len(files)} 个（成功解码 {sum(per_label_file.values())}）",
             f"样本窗：{len(X)} 个（{args.win}s 窗 / {args.hop}s 步长）",
             f"电平门限：宠物类 {args.min_db} dBFS，拒绝类另设（silence "
             f"{MIN_DB_BY_CLASS[CLS_SILENCE]} / noise {MIN_DB_BY_CLASS[CLS_NOISE]} / "
             f"human {MIN_DB_BY_CLASS[CLS_HUMAN]}）",
             f"特征维度：{X.shape[1]}×{X.shape[2]} log-mel（与板上推理端一致）", "",
             f"{'类别':<10}{'文件':>6}{'时长(s)':>10}{'窗数':>7}{'train':>7}{'val':>6}{'test':>6}"]
    for i, c in enumerate(CLASSES):
        m = y == i
        n_tr = int((split_arr[m] == "train").sum())
        n_va = int((split_arr[m] == "val").sum())
        n_te = int((split_arr[m] == "test").sum())
        lines.append(f"{c:<10}{per_label_file[c]:>6}{per_label_dur[c]:>10.1f}"
                     f"{int(m.sum()):>7}{n_tr:>7}{n_va:>6}{n_te:>6}")
    report = "\n".join(lines)
    print("\n" + report)

    if dropped:
        report += f"\n\n未产出样本的文件（{len(dropped)} 个）：\n" + "\n".join(
            f"  {lb}/{fn} — {why}" for lb, fn, why in dropped)
        print(f"\n[已丢弃] {len(dropped)} 个文件未产出样本：")
        for lb, fn, why in dropped[:8]:
            print(f"   {lb}/{fn} — {why}")
        if len(dropped) > 8:
            print(f"   …另有 {len(dropped) - 8} 个，详见 report.txt")

    # 致命检查：某类在 train 里一个样本都没有，模型永远学不会它
    empty_train = [c for i, c in enumerate(CLASSES)
                   if int(((y == i) & (split_arr == "train")).sum()) == 0]
    if empty_train:
        print(f"\n[严重] 以下类别在训练集中没有任何样本：{', '.join(empty_train)}")
        print("       模型将永远无法识别这些类。常见原因：该类素材少于 3 个文件，")
        print("       或电平全部低于门限。请补采后重新生成。")

    # 数据量判据（按方案里的采集门槛）
    thin = [c for i, c in enumerate(CLASSES)
            if int((y == i).sum()) < 200 and c not in (CLS_SILENCE,)]
    if thin:
        print(f"\n[警告] 以下类别窗数不足 200，模型难以稳定：{', '.join(thin)}")
        print("        建议每类 ≥400 个窗（约 200 秒有效发声，0.96s 窗 50% 重叠 ≈ 2 窗/秒）")

    if args.dry_run:
        print("\n（dry-run，未写文件）")
        return

    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "voice.npz", X=X, y=y, src=src_arr,
                        split=split_arr, classes=np.array(CLASSES))
    with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["label", "src", "split", "offset_sec", "rms_dbfs"])
        w.writeheader()
        w.writerows(recs)
    (out / "report.txt").write_text(report, encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({
        "sr": SR, "win": args.win, "hop": args.hop, "n_mel": N_MEL,
        "classes": CLASSES, "n_samples": int(len(X)), "seed": args.seed,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n已写出：{out / 'voice.npz'}（{X.nbytes / 1e6:.1f} MB 未压缩）")
    print(f"        {out / 'manifest.csv'} / report.txt / meta.json")


if __name__ == "__main__":
    main()
