#!/usr/bin/env python3
"""train_behavior_cls.py —— 训练宠物行为分类模型并导出 ONNX。

模型选择：YOLOv8n-cls（224×224，约 1.4 GFLOPs）。理由：
  - 检测已经在跑 YOLOv8n，分类头用同源骨干，量化与 EP 验证流程可以复用；
  - 224² 的输入在 IME 上单次约 10 ms，2 fps 采样下对 AI 核占用几乎无影响；
  - 更大的骨干（s/m）留给「AI 核占用不够 90%」时的备选，用 --model 切换即可。

用法：
  python train_behavior_cls.py --data datasets/behavior --epochs 40
  python train_behavior_cls.py --data datasets/behavior --model yolov8s-cls.pt --epochs 60
  python train_behavior_cls.py --data datasets/behavior --epochs 40 --export   # 训完导出 ONNX

产物：
  runs/classify/<name>/weights/best.pt
  runs/classify/<name>/confusion_matrix.png      ← 答辩必备，别只看 top1
  runs/classify/<name>/weights/best.onnx         ← 加 --export 才有
"""
import argparse
import os
import sys


def check_dataset(root):
    """最少限度的体检：类别双向一致、每类样本数、空 split。"""
    splits = ["train", "val"]
    classes = {}
    problems = []
    for sp in splits:
        d = os.path.join(root, sp)
        if not os.path.isdir(d):
            problems.append(f"缺目录 {d}")
            continue
        for cls in sorted(os.listdir(d)):
            cdir = os.path.join(d, cls)
            if not os.path.isdir(cdir):
                continue
            n = len([f for f in os.listdir(cdir) if f.lower().endswith((".jpg", ".jpeg", ".png"))])
            classes.setdefault(cls, {})[sp] = n

    if problems:
        for p in problems:
            print(f"✗ {p}")
        sys.exit("数据集结构不对：需要 <root>/train/<类>/ 与 <root>/val/<类>/")

    only_train = [c for c, v in classes.items() if v.get("val", 0) == 0]
    only_val = [c for c, v in classes.items() if v.get("train", 0) == 0]
    if only_train:
        problems.append(f"这些类没有验证样本：{only_train}")
    if only_val:
        problems.append(f"这些类只在验证集出现：{only_val}")

    print("=" * 64)
    print(f"{'类别':<12}{'train':>8}{'val':>8}")
    for c, v in classes.items():
        print(f"{c:<12}{v.get('train', 0):>8}{v.get('val', 0):>8}")
    total = sum(v.get("train", 0) for v in classes.values())
    print(f"{'合计':<12}{total:>8}")
    print("=" * 64)

    if total < 500:
        print("⚠ 训练样本少于 500 张，模型基本学不出东西。先把视频数量补上去。")
    for c, v in classes.items():
        if v.get("val", 0) < 20:
            print(f"⚠ {c} 的验证样本只有 {v.get('val', 0)} 张，该类指标波动会很大。")
    if len(classes) < 3:
        print("⚠ 类别少于 3 个，分类任务意义不大。")
    for p in problems:
        print(f"✗ {p}")
    if problems:
        sys.exit("先修数据集再训练。")
    return classes


def main():
    ap = argparse.ArgumentParser(description="训练宠物行为分类模型")
    ap.add_argument("--data", required=True, help="数据集根目录（含 train/ val/）")
    ap.add_argument("--model", default="yolov8n-cls.pt", help="yolov8n-cls.pt / yolov8s-cls.pt")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--imgsz", type=int, default=224)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--device", default="", help="留空自动选 GPU；'cpu' 强制 CPU")
    ap.add_argument("--name", default="pet_behavior", help="runs/classify/<name>")
    ap.add_argument("--export", action="store_true", help="训练后导出 ONNX")
    args = ap.parse_args()

    check_dataset(args.data)

    try:
        import torch
        from ultralytics import YOLO
    except ImportError as e:
        sys.exit(f"缺依赖：{e}. 见 k1-vision/requirements-train.txt")

    if torch.cuda.is_available():
        print(f"设备：{torch.cuda.get_device_name(0)}  "
              f"显存 {torch.cuda.get_device_properties(0).total_memory / 2**30:.1f} GB")
        device = args.device or "0"
    else:
        print("⚠ 没有检测到可用 CUDA，退回 CPU 训练（会慢 10~20 倍，但能跑）")
        device = "cpu"

    model = YOLO(args.model)
    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        name=args.name,
        project="runs/classify",
        pretrained=True,
        patience=10,          # 10 轮没提升就早停，别浪费电
        plots=True,           # 出混淆矩阵
    )

    # 训练完在验证集上复核，把关键数字直接打出来
    metrics = model.val(data=args.data, imgsz=args.imgsz, device=device)
    top1 = float(getattr(metrics, "top1", 0.0))
    top5 = float(getattr(metrics, "top5", 0.0))
    print("=" * 64)
    print(f"验证集 top-1 = {top1 * 100:.2f}%   top-5 = {top5 * 100:.2f}%")
    print(f"混淆矩阵：runs/classify/{args.name}/confusion_matrix.png")
    print("=" * 64)
    print("提醒：top-1 高于 95% 且数据来自网上视频时，先怀疑数据泄漏")
    print("（同一视频的相似帧同时进了 train 和 val），不是模型特别强。")

    if args.export:
        onnx_path = model.export(format="onnx", imgsz=args.imgsz, opset=12, simplify=True)
        print(f"ONNX 已导出：{onnx_path}")
        print("下一步：python quant_cls_int8.py " + str(onnx_path) + f" --data {args.data}")


if __name__ == "__main__":
    main()
