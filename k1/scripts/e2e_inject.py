# -*- coding: utf-8 -*-
"""端到端注入探针：把一张静态宠物图做成水平平移序列，逐帧 POST 给 K1 视觉服务。

为什么需要它
  真实猫狗素材至今为零（见 HANDOFF 未闭环 1），但「检测器 → tracker →
  行为规则 → HTTP 快照 → 3568 动画」这条链能不能通，不该等素材。
  用真实 COCO 图喂真实 int8 检测器，至少能把「检测 + 行为判定 + 协议」这一段钉死。

它做的事
  1. 读一张图，缩到目标高度，贴到 640×480 白底上
  2. 每帧整体水平平移若干像素（模拟行走），竖直位置不变
  3. 按 --hz 逐帧 POST 到 /v1/vision/ingest（body 就是 JPEG 字节）
  4. 每帧顺手取一次 /v1/vision/latest，把 label 打出来

用法
  python scripts/e2e_inject.py --img _e2e/cat.jpg --host 127.0.0.1:11436 --hz 5 --frames 30
注：注入前必须停掉 pc_cam_vision，否则两路帧互相覆盖，行为判不出来。
"""
import argparse
import io
import json
import time
import urllib.request

import cv2
import numpy as np


CANVAS_W, CANVAS_H = 640, 480

# 本机装了 HTTP_PROXY 时 urllib 会把 127.0.0.1 的请求也发给代理，
# 代理回 502 —— 表现成「板端挂了」，其实请求根本没出本机。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_OPENER)


def build_frames(img, n, dx, pet_h=300):
    """把图缩放后水平平移，返回 n 帧 JPEG 字节。

    平移幅度直接决定判出来的行为：板端 walking 的门槛是「体长速 ≥ 0.35/s」，
    而体长速 = 像素速 / 体高。所以体高越小、走的像素越多，速度越大。
    早期版本给小图留了 20px 边距、只铺半个画布，实测只有 0.10/s，
    被判成 standing —— 那不是识别错了，是注入的运动量本来就不够。
    """
    h, w = img.shape[:2]
    scale = pet_h / float(h)
    pet = cv2.resize(img, (int(w * scale), pet_h), interpolation=cv2.INTER_AREA)
    ph, pw = pet.shape[:2]
    span = max(1, CANVAS_W - pw)       # 铺满画布宽度，把位移拉满
    x0 = 0
    frames = []
    for i in range(n):
        x = int(x0 + (span * i / max(1, n - 1)))
        canvas = np.full((CANVAS_H, CANVAS_W, 3), 235, np.uint8)  # 浅灰白底
        y = (CANVAS_H - ph) // 2
        canvas[y:y + ph, x:x + pw] = pet
        ok, buf = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not ok:
            raise RuntimeError("JPEG 编码失败")
        frames.append(buf.tobytes())
    return frames


def post(host, path, body, ctype="image/jpeg", timeout=6):
    req = urllib.request.Request(f"http://{host}{path}", data=body,
                                 headers={"Content-Type": ctype}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get(host, path, timeout=6):
    with urllib.request.urlopen(f"http://{host}{path}", timeout=timeout) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", required=True)
    ap.add_argument("--host", default="127.0.0.1:11436")
    ap.add_argument("--hz", type=float, default=5.0)
    ap.add_argument("--frames", type=int, default=30)
    ap.add_argument("--dx", type=int, default=-1, help="总平移像素，默认自动铺满画布")
    ap.add_argument("--pet-h", type=int, default=300, help="宠物缩放后的高度（像素）")
    a = ap.parse_args()

    img = cv2.imread(a.img)
    if img is None:
        raise SystemExit(f"读不到图片: {a.img}")
    frames = build_frames(img, a.frames, a.dx, pet_h=a.pet_h)
    period = 1.0 / a.hz

    print(f"注入 {a.frames} 帧 @ {a.hz}Hz  目标 {a.host}", flush=True)
    labels = []
    t0 = time.time()
    for i, buf in enumerate(frames):
        try:
            post(a.host, "/v1/vision/ingest", buf)
        except Exception as e:
            print(f"  帧{i} 注入失败: {e}", flush=True)
        if i % 2 == 0:
            try:
                d = json.loads(get(a.host, "/v1/vision/latest"))
                lab = d.get("label", "?")
                labels.append(lab)
                print(f"  帧{i:02d} pet={d.get('pet') or '-':4s} label={lab:9s} "
                      f"conf={d.get('conf')} action={d.get('action')} "
                      f"basis={d.get('basis')}", flush=True)
            except Exception as e:
                print(f"  帧{i} 读快照失败: {e}", flush=True)
        time.sleep(max(0.0, period - ((time.time() - t0) % period)))

    # 末态再取一次，看动作锁存是否生效
    time.sleep(0.6)
    try:
        d = json.loads(get(a.host, "/v1/vision/latest"))
        print("末态:", json.dumps({k: d.get(k) for k in
                                 ("pet", "label", "action", "action_conf", "basis", "conf")},
                                ensure_ascii=False), flush=True)
    except Exception as e:
        print("末态读取失败:", e, flush=True)

    from collections import Counter
    print("label 分布:", dict(Counter(labels)), flush=True)


if __name__ == "__main__":
    main()
