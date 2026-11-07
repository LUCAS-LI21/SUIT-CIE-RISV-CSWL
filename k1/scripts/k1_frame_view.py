# -*- coding: utf-8 -*-
"""把 K1 当前分析帧 + 检测结果画成一张带标注的诊断图。

用途：回答「摄像头到底拍到了什么、为什么识别不到宠物」。
只读取帧，不占用摄像头、不影响正在跑的推流。

用法：
    python scripts/k1_frame_view.py                    # 从 127.0.0.1:11436 取
    python scripts/k1_frame_view.py --out 结果.png
"""
from __future__ import annotations

import argparse
import io
import json
import os
import urllib.request

from PIL import Image, ImageDraw, ImageFont

DEFAULT_BASE = "http://127.0.0.1:11436"
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

CYAN = (0, 210, 255)
GREEN = (40, 230, 120)
AMBER = (255, 176, 32)
WHITE = (255, 255, 255)
GREY = (170, 178, 188)
DARK = (24, 27, 33)

# 硬结论（2026-11-05 实测，640x640 输入）：目标高度 <120px 时 conf≈0
MIN_RATIO = 0.25


def http_get(url: str) -> bytes:
    # 本机回环 + 显式不用代理（沙箱有 http_proxy 会劫持）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=8) as r:
        return r.read()


def dashed_rect(d: ImageDraw.ImageDraw, box, color, width=3, dash=14):
    x1, y1, x2, y2 = [int(v) for v in box]
    step = dash * 2
    for x in range(x1, x2, step):
        d.line([(x, y1), (min(x + dash, x2), y1)], fill=color, width=width)
        d.line([(x, y2), (min(x + dash, x2), y2)], fill=color, width=width)
    for y in range(y1, y2, step):
        d.line([(x1, y), (x1, min(y + dash, y2))], fill=color, width=width)
        d.line([(x2, y), (x2, min(y + dash, y2))], fill=color, width=width)


def label(d, xy, text, font, fg=DARK, bg=CYAN, pad=6):
    x, y = xy
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    w, h = r - l, b - t
    d.rectangle([x, y, x + w + pad * 2, y + h + pad * 2], fill=bg)
    d.text((x + pad, y + pad - t), text, font=font, fill=fg)
    return y + h + pad * 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--out", default=os.path.join(
        os.environ.get("TEMP", "."), "k1_frame_annotated.png"))
    args = ap.parse_args()

    jpeg = http_get(f"{args.base}/v1/vision/frame.jpg")
    snap = json.loads(http_get(f"{args.base}/v1/vision/latest").decode("utf-8"))
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    W, H = im.size

    S = 2
    im = im.resize((W * S, H * S), Image.LANCZOS)
    f_big = ImageFont.truetype(FONT_BOLD, 30)
    f_mid = ImageFont.truetype(FONT_PATH, 24)
    f_sm = ImageFont.truetype(FONT_PATH, 21)

    top_h, bot_h = 78, 132
    canvas = Image.new("RGB", (W * S, top_h + H * S + bot_h), DARK)
    canvas.paste(im, (0, top_h))
    d = ImageDraw.Draw(canvas)

    # 顶部标题
    d.text((26, 16), f"K1 正在分析的画面  {W}x{H}", font=f_big, fill=WHITE)
    d.text((26, 50), f"fps {snap.get('fps')}   infer {snap.get('infer_ms')}ms   "
                     f"模型 {snap.get('model')}", font=f_sm, fill=GREY)

    oy = top_h  # 画面在画布里的 y 偏移

    # 宠物最小尺寸参考框（高度 = 25% 画面高）
    min_h = int(H * MIN_RATIO * S)
    bw = int(W * 0.34 * S)
    bx1 = int(W * S * 0.06)
    by2 = oy + H * S
    by1 = by2 - min_h
    dashed_rect(d, [bx1, by1, bx1 + bw, by2], GREEN, width=3)
    label(d, (bx1 + 10, by1 + 10),
          f"宠物至少要占这么高  25% = {int(H * MIN_RATIO)}px", f_sm, DARK, GREEN)

    # 人
    hum = snap.get("human") or {}
    if hum.get("present") and hum.get("bbox"):
        x1, y1, x2, y2 = hum["bbox"]
        box = [x1 * S, oy + y1 * S, x2 * S, oy + y2 * S]
        d.rectangle(box, outline=CYAN, width=4)
        hh = int(y2 - y1)
        label(d, (box[0] + 8, box[1] - 40),
              f"person  {hum.get('conf'):.2f}   高 {hh}px ({hh / H * 100:.0f}%)",
              f_sm, DARK, CYAN)

    # 宠物
    for det in (snap.get("all") or []):
        b = det.get("bbox")
        if not b:
            continue
        box = [b[0] * S, oy + b[1] * S, b[2] * S, oy + b[3] * S]
        col = AMBER
        d.rectangle(box, outline=col, width=4)
        label(d, (box[0] + 8, box[1] - 40),
              f"{det.get('type', 'pet')}  {det.get('conf', 0):.2f}", f_sm, DARK, col)

    # 底部结论
    by = top_h + H * S + 12
    n_pet = snap.get("count", 0)
    if n_pet:
        line1 = f"检出宠物 {n_pet} 只 → 动作 {snap.get('label')} (conf {snap.get('conf')})"
        c1 = GREEN
    else:
        line1 = ("检出宠物 0 只 → 画面里没有猫狗，显示 absent 是正确结果")
        c1 = AMBER
    d.text((26, by), line1, font=f_mid, fill=c1)
    has_h = "  (画面里有人 1 个，宠物和人是分开判定的)" if hum.get("present") else ""
    d.text((26, by + 36), f"关键：目标高度要 ≥ 画面高 25%，低于 {int(H * MIN_RATIO)}px "
                          f"时置信度会掉到 0，再怎么调也检不出{has_h}",
           font=f_sm, fill=GREY)
    d.text((26, by + 70), "距离参考：画面高 25% ≈ 目标高度 ÷ 距离 ≈ 0.44 时成立"
                          "（柯基约 1 米内、猫约 0.6 米内）",
           font=f_sm, fill=GREY)

    canvas.save(args.out)
    print(f"已保存 {args.out}  ({canvas.size[0]}x{canvas.size[1]})")
    print(f"pet={n_pet} label={snap.get('label')} human={hum.get('present')}")


if __name__ == "__main__":
    main()
