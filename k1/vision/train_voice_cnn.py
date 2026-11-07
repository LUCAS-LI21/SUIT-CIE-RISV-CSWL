#!/usr/bin/env python3
"""叫声分类模型训练（10 类，输入 1×1×64×64 log-mel）。

为什么自己训而不是用现成的大模型
--------------------------------
叫声不像人声有成熟的预训练权重可用。可选的公开叫声数据集（ESC-50、
AudioSet 的动物子集）标的是"狗叫声/猫叫声"这种大类，没有 bark/howl/
growl/meow/purr/hiss/scream 的细分，且录制条件与家用麦克风差异极大。
这类任务上，一个 30 万参数的小 CNN 用自采数据训练，效果通常优于把
大模型迁过来——因为判别信息在频谱包络的细节里，而不在语义层。

模型规模与实际约束
------------------
4 层卷积 + 全局平均池化 + 两层全连接，约 27 万参数：
  - fp32 ONNX 约 1.1 MB，int8 约 0.3 MB，占 K1 内存可忽略
  - 板上单窗推理 2~5 ms（K1 CPU），相对视觉 82 ms 完全不是瓶颈

用法
----
    # 训练 + 评估 + 导出 ONNX
    python k1-vision/train_voice_cnn.py --data k1-vision/datasets/voice \
        --epochs 60 --export

    # 只评估已有权重
    python k1-vision/train_voice_cnn.py --data k1-vision/datasets/voice \
        --ckpt runs/voice/best.pt --eval-only
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np


# ── 数据 ──────────────────────────────────────────────────────────────
def load_npz(data_dir: Path):
    p = data_dir / "voice.npz"
    if not p.exists():
        sys.exit(f"找不到 {p}。请先运行 audio_to_dataset.py 生成数据集。")
    z = np.load(p, allow_pickle=True)
    X, y, split = z["X"], z["y"], z["split"]
    classes = [str(c) for c in z["classes"]]
    return X, y, split, classes


def spec_augment(x: np.ndarray, rng: np.random.Generator, n_freq=2, n_time=2,
                 f_mask=8, t_mask=10):
    """SpecAugment：随机遮挡频带/时段。

    对叫声尤其有效——模型的捷径是记住某只动物的固定基频位置，遮掉
    几段频带后它必须依赖谱包络的形状，跨个体泛化会明显变好。
    """
    x = x.copy()
    n_mel = x.shape[0]
    for _ in range(n_freq):
        w = rng.integers(1, f_mask + 1)
        f0 = rng.integers(0, max(1, n_mel - w))
        x[f0:f0 + w, :] = 0.0
    for _ in range(n_time):
        w = rng.integers(1, t_mask + 1)
        t0 = rng.integers(0, max(1, n_mel - w))
        x[:, t0:t0 + w] = 0.0
    return x


class MelDataset:
    """轻量 Dataset：避免为几千个 64×64 样本引入 DataLoader 的开销。"""

    def __init__(self, X, y, train=False, seed=0):
        self.X, self.y, self.train = X, y, train
        self.rng = np.random.default_rng(seed)

    def __len__(self):
        return len(self.y)

    def batch(self, idx):
        xs, ys = [], []
        for i in idx:
            x = self.X[i]
            if self.train:
                x = spec_augment(x, self.rng)
                x = x + self.rng.normal(0, 0.05, x.shape).astype(np.float32)  # 加性噪声
                g = self.rng.uniform(0.85, 1.15)                             # 随机增益
                x = x * g
            xs.append(x)
            ys.append(self.y[i])
        return (np.stack(xs)[:, None, :, :].astype(np.float32),  # (B,1,64,64)
                np.array(ys, dtype=np.int64))


# ── 模型 ──────────────────────────────────────────────────────────────
def build_model(n_classes: int):
    import torch.nn as nn

    def block(cin, cout):
        return nn.Sequential(
            nn.Conv2d(cin, cout, 3, padding=1, bias=False),
            nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
        )

    return nn.Sequential(
        block(1, 32),      # 64 → 32
        block(32, 64),     # 32 → 16
        block(64, 128),    # 16 → 8
        block(128, 128),   # 8  → 4
        nn.AdaptiveAvgPool2d(1),
        nn.Flatten(),
        nn.Linear(128, 256), nn.ReLU(inplace=True), nn.Dropout(0.3),
        nn.Linear(256, n_classes),
    )


# ── 评估 ──────────────────────────────────────────────────────────────
def evaluate(model, X, y, classes, device, bs=128):
    import torch
    model.eval()
    preds = []
    with torch.no_grad():
        for i in range(0, len(y), bs):
            xb = torch.from_numpy(X[i:i + bs][:, None, :, :].astype(np.float32)).to(device)
            preds.append(model(xb).argmax(1).cpu().numpy())
    pred = np.concatenate(preds) if preds else np.array([], dtype=np.int64)

    n = len(classes)
    cm = np.zeros((n, n), dtype=np.int64)
    for t, p in zip(y, pred):
        cm[t, p] += 1

    acc = float((pred == y).mean()) if len(y) else 0.0
    lines = [f"{'类别':<10}{'样本':>6}{'精确率':>9}{'召回率':>9}{'F1':>8}"]
    f1s, precs, recs = [], [], []
    for i, c in enumerate(classes):
        tp = cm[i, i]
        fp = cm[:, i].sum() - tp
        fn = cm[i, :].sum() - tp
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        lines.append(f"{c:<10}{cm[i, :].sum():>6}{p:>9.3f}{r:>9.3f}{f:>8.3f}")
        if cm[i, :].sum() > 0:              # 无样本的类不参与 macro 平均
            f1s.append(f); precs.append(p); recs.append(r)
    macro_f1 = float(np.mean(f1s)) if f1s else 0.0
    lines.append(f"{'macro':<10}{len(y):>6}{np.mean(precs) if precs else 0:>9.3f}"
                 f"{np.mean(recs) if recs else 0:>9.3f}{macro_f1:>8.3f}")
    lines.append(f"\n总体准确率：{acc:.4f}    macro-F1：{macro_f1:.4f}")

    # 最容易混淆的两对——答辩时被问"错在哪"必须有答案
    off = cm.copy()
    np.fill_diagonal(off, 0)
    pairs = []
    for i in range(n):
        for j in range(n):
            if i != j and off[i, j] > 0:
                pairs.append((off[i, j], classes[i], classes[j]))
    pairs.sort(reverse=True)
    if pairs:
        lines.append("\n主要混淆（真实 → 误判，前 3）：")
        for cnt, a, b in pairs[:3]:
            lines.append(f"  {a} → {b}：{cnt} 次")
    return acc, macro_f1, "\n".join(lines), cm


# ── 主流程 ────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="叫声分类模型训练")
    ap.add_argument("--data", required=True, help="audio_to_dataset.py 的输出目录")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--bs", type=int, default=64)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--out", default="runs/voice", help="权重与报告输出目录")
    ap.add_argument("--ckpt", help="已有权重路径（配合 --eval-only）")
    ap.add_argument("--export", action="store_true", help="导出 ONNX")
    ap.add_argument("--eval-only", action="store_true", help="只评估，不训练")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    import torch
    import torch.nn as nn

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    X, y, split, classes = load_npz(Path(args.data))
    n_cls = len(classes)
    print(f"数据集：{len(y)} 个样本，{n_cls} 类，特征 {X.shape[1]}×{X.shape[2]}")
    for s in ("train", "val", "test"):
        m = split == s
        print(f"  {s:<6}{int(m.sum()):>6} 个  分布 {dict(Counter(y[m].tolist()))}")

    tr, va, te = split == "train", split == "val", split == "test"
    if tr.sum() == 0 or va.sum() == 0:
        sys.exit("训练集或验证集为空。请检查 audio_to_dataset.py 的切分是否正常。")
    if (y[tr].max() - y[tr].min() + 1) < 2:
        sys.exit("训练集只有一个类别，无法训练。")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"设备：{device}" + (f"（{torch.cuda.get_device_name(0)}）" if device.type == "cuda" else ""))

    model = build_model(n_cls).to(device)
    n_param = sum(p.numel() for p in model.parameters())
    print(f"模型参数：{n_param:,}（fp32 约 {n_param * 4 / 1e6:.2f} MB）")

    if args.eval_only:
        if not args.ckpt:
            sys.exit("--eval-only 需要 --ckpt 指定权重")
        model.load_state_dict(torch.load(args.ckpt, map_location=device))
        for name, mask in (("val", va), ("test", te)):
            if mask.sum() == 0:
                continue
            acc, f1, rep, _ = evaluate(model, X[mask], y[mask], classes, device, args.bs)
            print(f"\n=== {name} 集 ===")
            print(rep)
        return

    # 类别权重：缓解不平衡（如 silence 窗远多于 scream 窗）
    cnt = np.bincount(y[tr], minlength=n_cls).astype(np.float64)
    w = np.where(cnt > 0, cnt.sum() / (n_cls * np.maximum(cnt, 1)), 0.0)
    weight = torch.tensor(w, dtype=torch.float32, device=device)

    train_ds = MelDataset(X[tr], y[tr], train=True, seed=args.seed)
    ytr = y[tr]
    steps = max(1, int(np.ceil(len(ytr) / args.bs)))

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-2)
    sched = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=args.lr, total_steps=args.epochs * steps, pct_start=0.25)
    crit = nn.CrossEntropyLoss(weight=weight, label_smoothing=0.05)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    best_f1, best_ep = -1.0, -1
    rng = np.random.default_rng(args.seed)

    for ep in range(1, args.epochs + 1):
        model.train()
        order = rng.permutation(len(ytr))
        tot, correct = 0, 0
        run_loss = 0.0
        for i in range(0, len(order), args.bs):
            idx = order[i:i + args.bs]
            if len(idx) < 2:
                continue
            xb, yb = train_ds.batch(idx)
            xb = torch.from_numpy(xb).to(device)
            yb = torch.from_numpy(yb).to(device)
            opt.zero_grad(set_to_none=True)
            logits = model(xb)
            loss = crit(logits, yb)
            loss.backward()
            opt.step()
            sched.step()
            run_loss += float(loss) * len(idx)
            correct += int((logits.argmax(1) == yb).sum())
            tot += len(idx)

        acc_tr = correct / max(tot, 1)
        acc_va, f1_va, rep_va, _ = evaluate(model, X[va], y[va], classes, device, args.bs)
        flag = ""
        if f1_va > best_f1:
            best_f1, best_ep = f1_va, ep
            torch.save(model.state_dict(), out / "best.pt")
            flag = "  ← best"
        print(f"epoch {ep:>3}/{args.epochs}  loss {run_loss / max(tot, 1):.3f}  "
              f"train {acc_tr:.3f}  val {acc_va:.3f}  macro-F1 {f1_va:.3f}{flag}")

    print(f"\n最佳 epoch {best_ep}，val macro-F1 {best_f1:.4f}")
    model.load_state_dict(torch.load(out / "best.pt", map_location=device))

    report_parts = []
    for name, mask in (("val", va), ("test", te)):
        if mask.sum() == 0:
            continue
        acc, f1, rep, _ = evaluate(model, X[mask], y[mask], classes, device, args.bs)
        report_parts.append(f"=== {name} 集 ===\n{rep}")
        print(f"\n=== {name} 集 ===")
        print(rep)
        if name == "test":
            test_f1 = f1

    (out / "report.txt").write_text("\n\n".join(report_parts), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({
        "classes": classes, "n_params": n_param, "best_epoch": best_ep,
        "val_macro_f1": best_f1, "epochs": args.epochs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # 过拟合/泄漏体检
    if best_f1 > 0.97:
        print("\n[提示] val macro-F1 超过 0.97，请先排除数据泄漏再高兴：")
        print("       ① 同一段音频的窗是否跨了 train/val（看 manifest.csv 的 src 列）")
        print("       ② 同一只动物、同一次录音是否被切到两个集合")
        print("       audio_to_dataset.py 已按源文件切分，但手动拼数据时容易破坏这一点。")

    if args.export:
        onnx_path = out / "voice_cnn.onnx"
        model.eval().cpu()
        dummy = torch.randn(1, 1, 64, 64)

        # 优先走 legacy（TorchScript）导出路径，两个理由都是踩过的坑：
        #  ① 新版 dynamo 导出器会把权重外部化到 voice_cnn.onnx.data，
        #     主文件只剩 24 KB。部署时只拷 .onnx 就是一具空壳，
        #     而且报错发生在板上而不是这里，极难排查。
        #  ② dynamo 路径会把 opset 抬到 18，板上 onnxruntime 若较老会直接
        #     拒绝加载。本模型只有 Conv/BN/ReLU/MaxPool/GAP/Gemm，
        #     opset 13 完全够用，兼容性最好。
        def _export(opset: int, dynamo: bool | None):
            kw = dict(input_names=["input"], output_names=["logits"],
                      dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
                      opset_version=opset)
            if dynamo is not None:
                kw["dynamo"] = dynamo
            torch.onnx.export(model, dummy, str(onnx_path), **kw)

        exported = False
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                _export(13, False)
            exported = True
        except Exception as e:                          # noqa: BLE001
            print(f"[提示] legacy 导出不可用（{type(e).__name__}），回退到新导出器")
            try:
                _export(13, None)
                exported = True
            except ModuleNotFoundError as e2:
                print(f"\n[导出失败] 缺少依赖：{e2.name}")
                print("  .venv-train/Scripts/python.exe -m pip install onnx onnxscript onnxruntime")
            except Exception as e2:                     # noqa: BLE001
                print(f"\n[导出失败] {type(e2).__name__}: {e2}")

        if not exported:
            print(f"  权重已保存在 {out / 'best.pt'}，环境修好后可加 --export 重跑。")
            return

        # 无论走哪条路径，都校验一次「是不是单文件」——外部化的模型不能交付
        data_side = onnx_path.with_name(onnx_path.name + ".data")
        try:
            import onnx
            g = onnx.load(str(onnx_path))
            if data_side.exists():
                print(f"\n[修正] 检测到权重被外部化，正在内联为单文件…")
                tmp = onnx_path.with_name(onnx_path.stem + ".inline.onnx")
                onnx.save_model(g, str(tmp), save_as_external_data=False)
                tmp.replace(onnx_path)
                data_side.unlink(missing_ok=True)
            n_param = sum(onnx.numpy_helper.to_array(i).size for i in g.graph.initializer)
            opset = [o.version for o in g.opset_import if o.domain in ("", "ai.onnx")]
            print(f"\nONNX 已导出：{onnx_path}")
            print(f"  参数量 {n_param:,}，opset {opset[0] if opset else '?'}，"
                  f"体积 {onnx_path.stat().st_size / 1e6:.2f} MB，单文件")
            if n_param > 10000 and onnx_path.stat().st_size < n_param * 0.5:
                print("  [警告] 文件体积明显小于参数量，可能仍有外部权重，"
                      "部署前请确认同一目录下没有 .data 文件")
        except ImportError:
            print(f"\nONNX 已导出：{onnx_path}（未装 onnx，跳过结构校验）")

        try:
            import onnxruntime as ort
            s = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
            nin = s.get_inputs()[0].shape
            print(f"  输入 {nin}，输出维度 {s.get_outputs()[0].shape}")
            xb = X[va][:8][:, None, :, :].astype(np.float32) if va.sum() >= 8 else dummy.numpy()
            po = s.run(None, {"input": xb})[0]
            pt = model(torch.from_numpy(xb)).detach().numpy()
            same = float((po.argmax(1) == pt.argmax(1)).mean())
            print(f"  与 PyTorch 预测一致率：{same:.3f}（应为 1.000）")
        except ImportError:
            print("  （未安装 onnxruntime，跳过一致性校验）")
        print(f"\n下一步：python k1-vision/quant_voice_int8.py --onnx {onnx_path} "
              f"--data {args.data}")


if __name__ == "__main__":
    main()
