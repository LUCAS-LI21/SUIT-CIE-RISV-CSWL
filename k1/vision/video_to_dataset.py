#!/usr/bin/env python3
"""video_to_dataset.py —— 把网上的宠物视频转成行为分类数据集。

设计原则（三个防坑点）：
  1) **用文件夹名当标签**：videos/<类别>/*.mp4。不做标注 GUI，减少一步出错的机会。
  2) **按视频来源切分训练/验证/测试**，绝不随机切帧。同一段视频的相邻帧几乎相同，
     随机切会让验证集"见过"训练集，准确率虚高 10~20 个点——评委一问就露。
  3) **检测后再裁剪**：只用 YOLO 检出的猫/狗框内区域做分类样本，避免背景（沙发、地板）
     成为捷径特征。没有检出猫狗的帧直接丢弃，不硬凑数。

用法：
  # 目录结构（先把视频按动作分好类）
  #   videos/趴卧/a.mp4  videos/趴卧/b.mp4
  #   videos/奔跑/c.mp4 ...
  python video_to_dataset.py --src videos --out datasets/behavior

  # 只看计划不落地（先确认类别和视频数量对不对）
  python video_to_dataset.py --src videos --out datasets/behavior --dry-run

  # 视频少的时候把采样调密一点
  python video_to_dataset.py --src videos --out datasets/behavior --fps 4 --max-per-video 600

产物：
  datasets/behavior/train/<类>/*.jpg
  datasets/behavior/val/<类>/*.jpg
  datasets/behavior/test/<类>/*.jpg
  datasets/behavior/manifest.csv     ← 每张图的来源视频、时间戳、框、置信度，用于复盘
"""
import argparse
import csv
import os
import sys
import hashlib

try:
    import cv2
    import numpy as np
except ImportError:
    sys.exit("缺依赖：pip install opencv-python numpy")


CLASS_HINTS = {
    # 文件夹名 → 规范类名建议（不强制，仅提示）
    "趴卧": "lying", "趴": "lying", "lying": "lying",
    "坐": "sitting", "sitting": "sitting", "sit": "sitting",
    "站": "standing", "standing": "standing",
    "走": "walking", "walking": "walking",
    "跑": "running", "running": "running", "run": "running",
    "吃": "eating", "进食": "eating", "eating": "eating",
    "玩": "playing", "玩耍": "playing", "playing": "playing",
    "舔毛": "grooming", "grooming": "grooming",
    "睡": "sleeping", "sleeping": "sleeping",
}


def list_videos(root):
    """返回 [(class_name, video_path), ...]，class_name 取自一级子目录名。"""
    exts = (".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".m4v")
    items = []
    for cls in sorted(os.listdir(root)):
        cdir = os.path.join(root, cls)
        if not os.path.isdir(cdir):
            continue
        for fn in sorted(os.listdir(cdir)):
            if fn.lower().endswith(exts):
                items.append((cls, os.path.join(cdir, fn)))
    return items


def split_by_source(videos_per_class, ratios=(0.8, 0.1, 0.1)):
    """按视频（来源）切分，保证同一视频的所有帧只落在一个 split。"""
    n = len(videos_per_class)
    if n == 0:
        return {"train": [], "val": [], "test": []}
    if n == 1:
        # 单个视频：只能全给训练，验证/测试为空，训练脚本会报警——这是诚实的做法
        return {"train": videos_per_class, "val": [], "test": []}
    if n == 2:
        return {"train": [videos_per_class[0]], "val": [videos_per_class[1]], "test": []}

    n_val = max(1, int(round(n * ratios[1])))
    n_test = max(1, int(round(n * ratios[2])))
    while n_val + n_test >= n:            # 训练集至少要留一半
        if n_val >= n_test and n_val > 1:
            n_val -= 1
        elif n_test > 1:
            n_test -= 1
        else:
            break
    n_train = n - n_val - n_test
    return {
        "train": videos_per_class[:n_train],
        "val": videos_per_class[n_train:n_train + n_val],
        "test": videos_per_class[n_train + n_val:],
    }


def crop_with_margin(img, box, margin=0.15):
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    x1 = max(0, int(x1 - bw * margin))
    y1 = max(0, int(y1 - bh * margin))
    x2 = min(w, int(x2 + bw * margin))
    y2 = min(h, int(y2 + bh * margin))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return img[y1:y2, x1:x2]


def frame_hash(gray_small):
    """极轻量的近似去重：8×8 灰度图的均值哈希。"""
    return hashlib.md5(gray_small.tobytes()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser(description="宠物视频 → 行为分类数据集")
    ap.add_argument("--src", required=True, help="视频根目录，一级子目录名 = 类别")
    ap.add_argument("--out", required=True, help="输出数据集目录")
    ap.add_argument("--weights", default="yolov8n.pt", help="检测模型（默认 yolov8n.pt）")
    ap.add_argument("--fps", type=float, default=2.0, help="每秒采样几帧（默认 2）")
    ap.add_argument("--max-per-video", type=int, default=400, help="单个视频最多取多少帧")
    ap.add_argument("--margin", type=float, default=0.15, help="裁剪外扩比例")
    ap.add_argument("--min-box", type=int, default=40, help="最小框边长（像素），小框放大后模糊，丢弃")
    ap.add_argument("--conf", type=float, default=0.35, help="检测置信度门限")
    ap.add_argument("--dedup", type=float, default=2.0, help="相邻帧平均像素差低于此值则丢弃（0 关闭）")
    ap.add_argument("--ratios", default="0.8,0.1,0.1", help="train,val,test 比例（按视频数）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划")
    args = ap.parse_args()

    ratios = tuple(float(x) for x in args.ratios.split(","))
    items = list_videos(args.src)
    if not items:
        sys.exit(f"在 {args.src} 下没找到视频。目录结构应为 {args.src}/<类别>/<视频文件>")

    # 按类汇总视频
    by_class = {}
    for cls, path in items:
        by_class.setdefault(cls, []).append(path)

    print("=" * 66)
    print(f"类别数 {len(by_class)}，视频总数 {len(items)}")
    for cls, vids in by_class.items():
        hint = CLASS_HINTS.get(cls, "")
        print(f"  {cls:<10} 视频 {len(vids):>3} 个" + (f"   → 建议类名 {hint}" if hint else ""))
    print("=" * 66)

    plan = {cls: split_by_source(vids, ratios) for cls, vids in by_class.items()}
    for cls, sp in plan.items():
        print(f"{cls:<10} train {len(sp['train'])} 视频 / val {len(sp['val'])} / test {len(sp['test'])}")
        if not sp["val"]:
            print(f"  ⚠ {cls} 没有验证视频（只有 {len(sp['train'])} 个来源），该类指标不可信")

    if args.dry_run:
        print("\n--dry-run：未写入任何文件。")
        return

    # 加载检测模型
    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("缺依赖：pip install ultralytics")
    model = YOLO(args.weights)

    os.makedirs(args.out, exist_ok=True)
    manifest_path = os.path.join(args.out, "manifest.csv")
    mf = open(manifest_path, "w", newline="", encoding="utf-8")
    writer = csv.writer(mf)
    writer.writerow(["split", "class", "source_video", "frame_idx", "t_sec",
                     "pet", "det_conf", "x1", "y1", "x2", "y2", "file"])

    total_saved = 0
    total_frames = 0
    total_no_pet = 0

    for cls, sp in plan.items():
        for split, videos in sp.items():
            outdir = os.path.join(args.out, split, cls)
            os.makedirs(outdir, exist_ok=True)
            for vpath in videos:
                stem = os.path.splitext(os.path.basename(vpath))[0]
                stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)[:60]
                cap = cv2.VideoCapture(vpath)
                if not cap.isOpened():
                    print(f"  ✗ 打不开 {vpath}")
                    continue
                src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                step = max(1, int(round(src_fps / max(0.1, args.fps))))
                saved = 0
                last_small = None
                fidx = 0
                while saved < args.max_per_video:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    if fidx % step == 0:
                        total_frames += 1
                        res = model.predict(frame, conf=args.conf, verbose=False)[0]
                        best = None
                        for b in res.boxes:
                            cid = int(b.cls.item())
                            if cid not in (15, 16):          # COCO: 15=cat 16=dog
                                continue
                            x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
                            if min(x2 - x1, y2 - y1) < args.min_box:
                                continue
                            if best is None or b.conf.item() > best[0]:
                                best = (float(b.conf.item()), cid, (x1, y1, x2, y2))
                        if best is None:
                            total_no_pet += 1
                        else:
                            conf, cid, box = best
                            crop = crop_with_margin(frame, box, args.margin)
                            if crop is not None:
                                small = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (8, 8),
                                                   interpolation=cv2.INTER_AREA)
                                if args.dedup > 0 and last_small is not None:
                                    if np.abs(small.astype(np.int16) - last_small).mean() < args.dedup:
                                        fidx += 1
                                        continue
                                last_small = small.astype(np.int16)
                                fn = f"{stem}_{fidx:05d}.jpg"
                                fp = os.path.join(outdir, fn)
                                cv2.imwrite(fp, crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
                                writer.writerow([split, cls, os.path.basename(vpath), fidx,
                                                 round(fidx / src_fps, 2),
                                                 "cat" if cid == 15 else "dog",
                                                 round(conf, 3),
                                                 *[round(v, 1) for v in box], fn])
                                saved += 1
                                total_saved += 1
                    fidx += 1
                cap.release()
                print(f"  [{split}] {cls}/{os.path.basename(vpath)} → {saved} 张")
    mf.close()

    print("=" * 66)
    print(f"采样帧 {total_frames}，落盘 {total_saved}，无宠物丢弃 {total_no_pet}")
    print(f"清单：{manifest_path}")
    print("=" * 66)
    print("下一步：")
    print(f"  python train_behavior_cls.py --data {args.out} --model yolov8n-cls.pt --epochs 40")
    print("提示：先看 val 集里有没有把猫/狗裁歪的图，脏数据会直接拉低上限。")


if __name__ == "__main__":
    main()
