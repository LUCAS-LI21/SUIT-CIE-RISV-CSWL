#!/usr/bin/env python3
"""在 PC 上用 FP32 预筛宠物图，挑出主体清晰、置信度高的候选。

用途：为 K1 板端 int8 检出测试准备"主体清晰"的输入图。
候选图来源 Pexels（可商用授权），预筛只做筛选，不作为板端测量依据。

用法：
    export PYTHONPATH=E:/CIE-RISC-V/Pet-yolo/venv_x/yolo_server/venv/Lib/site-packages
    python screen_pets.py <图片目录>
"""
import sys
import glob
import os

from ultralytics import YOLO

CAT, DOG = 15, 16
PET_NAMES = {CAT: "cat", DOG: "dog"}


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "datasets/pets"
    files = sorted(glob.glob(os.path.join(d, "*.jpg")) + glob.glob(os.path.join(d, "*.jpeg")))
    if not files:
        print("目录下没有 jpg:", d)
        return 1

    model = YOLO("yolov8n.pt")
    rows = []
    for f in files:
        r = model.predict(f, imgsz=640, conf=0.05, verbose=False)[0]
        dets = []
        for b in r.boxes:
            cid = int(b.cls.item())
            dets.append((cid, float(b.conf.item()), [round(v, 1) for v in b.xyxy[0].tolist()]))
        dets.sort(key=lambda x: -x[1])

        pets = [d_ for d_ in dets if d_[0] in PET_NAMES]
        top = dets[0] if dets else None
        rows.append((f, pets, top, len(dets)))

    print("=" * 96)
    print(f"{'文件':<22} {'宠物检出':<46} {'全图 top-1':<26}")
    print("=" * 96)
    for f, pets, top, n in rows:
        if pets:
            ps = ", ".join(f"{PET_NAMES[c]}={cf:.3f}" for c, cf, _ in pets[:3])
        else:
            ps = "—"
        if top:
            c, cf, _ = top
            nm = PET_NAMES.get(c, model.names.get(c, str(c)))
            ts = f"{nm}={cf:.3f}"
        else:
            ts = "无检出"
        print(f"{os.path.basename(f):<22} {ps:<46} {ts:<26}")

    print("=" * 96)
    ranked = [r for r in rows if r[1]]
    ranked.sort(key=lambda r: -r[1][0][1])
    if not ranked:
        print("★ 没有任何一张检出猫狗，需要换图源")
        return 2
    print("★ 按宠物置信度排序（前 5）：")
    for f, pets, _, _ in ranked[:5]:
        c, cf, xy = pets[0]
        print(f"   {os.path.basename(f):<20} {PET_NAMES[c]} conf={cf:.3f} box={xy}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
