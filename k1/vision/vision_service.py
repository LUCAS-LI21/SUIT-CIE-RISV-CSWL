#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K1 视觉感知服务 —— 宠物行为识别

定位：把 yolo-server/yolo_server.py 里那套「检测框运动学 → 行为标签」的算法
      搬到 K1 (SpacemiT, RISC-V, Bianbu) 上，用 ONNX Runtime 跑 int8 模型，
      通过 HTTP 把「最新识别结果」供给鸿蒙 App。

与 App 的契约（见 pet-wellness/.../K1Perception.ets）：
    GET /v1/vision/latest
      200 {"ok":true,"pet":"dog","label":"lying","conf":0.87,
           "duration_sec":2520,"fps":1.4,"model":"yolov8n","ts":1730000000.0}
      200 {"ok":true,"pet":"","label":"absent",...}   视野内无宠物
      503 {"ok":false,"reason":"no_camera"}           摄像头/模型未就绪

    GET /health  → {"ok":true,"frames":N,"fps":x,"source":"camera0"}

相对原 yolo_server.py 修掉的三个硬伤（移植时必须处理，否则行为分布全错）：
    1. 原代码所有速度是「像素/分析帧」，没有 dt。30fps 下 Vn>0.15 相当于
       5760px/s，永远触发不了 running；long_still 的「10 帧」在 30fps 下只有
       0.33 秒。这里固定分析帧率（默认 2fps），并显式记录帧间隔。
    2. 原代码没有「行为持续时间」，visionDurationMin 无法生成。
       这里给 tracker 加 last_seen_ts / behavior_start_ts，输出 duration_sec。
    3. 原代码 MultiPetTracker.cleanup() 是空实现且无人调用，tracker 只增不删，
       宠物离开后位置历史还是旧的，回来时会算出巨大位移。这里按时间淘汰。

用法：
    python3 vision_service.py --model ./yolov8n.onnx --camera 0
    python3 vision_service.py --model ./yolov8n.onnx --image ./bus.jpg   # 无摄像头时测协议
    python3 vision_service.py --model ./yolov8n.onnx --camera 0 --port 11436 --fps 2
"""

import argparse
import json
import math
import os
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import onnxruntime as ort
    # SpaceMIT EP 是插件式的：必须 import spacemit_ort，它才会 patch
    # InferenceSession.initialize_session 并注册 SpaceMITExecutionProvider。
    # 不 import 的话 get_available_providers() 里只有 CPUExecutionProvider，
    # 推理会静默退回 CPU（能跑但慢 3 倍以上），很容易误以为已经用上 AI 核。
    try:
        import spacemit_ort  # noqa: F401
        HAS_SPACEMIT_EP = True
    except ImportError:
        HAS_SPACEMIT_EP = False
except ImportError:
    ort = None
    HAS_SPACEMIT_EP = False


# ────────────────────────── 可调参数 ──────────────────────────

IMG_SIZE = 640          # 导出模型时的输入尺寸，必须与 onnx 一致
CONF_THRES = 0.25       # 与 Ultralytics 默认一致。注意原 yolo_server.py 的 0.05 后过滤通常不生效
NMS_IOU = 0.7
PET_CLASSES = {15: "cat", 16: "dog"}   # COCO id
BALL_CLASSES = {29: "frisbee", 32: "sports ball", 34: "baseball bat",
                35: "baseball glove", 38: "tennis racket"}
# 人类识别通道：COCO person=0。与宠物逻辑完全隔离——
#   decode 阶段保留该类，但 MultiPetTracker 只认 PET_CLASSES，
#   person 会在 update_or_create 里被 `cls_name is None` 跳过，因此对宠物结果零影响。
HUMAN_CLASSES = {0: "person"}
HUMAN_CONF_THRES = 0.35   # 比宠物阈值高，避免暗光下人影误触发

# ── 时间窗口（全部按「秒」定义，不按采样点数）─────────────────────────
#
# 为什么必须按秒：原来这些窗口是「点数」（10 点、5 点、3 步、3 次观测），
# 在 2 Hz 下等价于固定秒数，一旦把 --fps 提高，同一个窗口覆盖的时间就缩短，
# 特征数值整体系数级偏移，阈值全部失效。改成秒之后，帧率只影响采样密度，
# 不影响判定语义 —— 2 Hz 下各窗口的秒数刻意选成与旧点数完全等价，
# 因此历史行为逐位不变（scripts/behavior_lag_probe.py 有跨帧率一致性用例）。
HISTORY_SEC = 4.5       # 位置历史窗口（原 HISTORY_FRAMES=10 → 2 Hz 下 10 点）
STABLE_SEC = 2.0        # 位置稳定度 S 的统计窗口（原 min(5,n) 点 → 2 Hz 下 5 点）
STABLE_SAMPLES = 5      # S 在该窗口内取几个等距采样点（2 Hz 下正好等于全部点）
SPEED_SEC = 1.5         # 中位数速度的统计窗口（原 SPEED_WINDOW=3 步 → 2 Hz 下 3 步）
SPEED_BUCKETS = 3       # 速度切成几个等长时间片（1.5s/3 = 0.5s，与 2 Hz 原步长一致）
NET_DISP_SEC = 1.0      # 兜底净位移的跨距（原 span=3 点 → 2 Hz 下 3 点）
PERIOD_SEC = 4.0        # 位移自相关的统计窗口（原 8 个步长 → 2 Hz 下 4 s）
PERIOD_BUCKETS = 8      # 自相关序列切几个时间片（4.0s/8 = 0.5s）
PERIOD_LAG_BUCKETS = 2  # 自相关滞后片数（2 片 = 1.0 s，与旧固定滞后 2 步一致）

# 去抖确认时长（原 STABLE_NEED=2 / MOVE_NEED=1，按观测次数）：
#   运动→静息需要 3 次观测 @2Hz = 1.0 s；静息→运动只需 2 次 @2Hz = 0.5 s。
# 非对称是刻意的：起步常在 3 s 内穿过画面，对称去抖会让整段经过都停在
# sitting/sleeping 上（实测见 scripts/behavior_lag_probe.py 的改前输出）。
STABLE_CONFIRM_SEC = 1.0
MOVE_CONFIRM_SEC = 0.5

# 事件型行为：跳跃、玩耍本身只持续 0.3~0.6 s，用上面的确认窗口一定等不到。
# 实测（scripts/behavior_lag_probe.py 的 jump 用例）：0.4 s 的跳在 10 Hz 下
# 命中 6 次 raw=jumping，发布 0 次；2 Hz 下连采样都没落进滞空期。
# 这类行为必须走「立即发布 + 锁存」，否则 3568 上的虚拟形象永远收不到「跳」。
EVENT_LABELS = ("jumping", "playing")
EVENT_HOLD_SEC = 2.0   # 锁存时长：保证板端 2 Hz 快照与 App 1 Hz 轮询各取到两次

# 跳的峰峰值判据。用峰峰值而不是速度：跳跃升上去又落回来，按 0.5 s 时间片
# 取净位移会自我抵消（vb≈0），判定掉进「静止」分支，永远走不到 jumping。
JUMP_PEAK_SEC = 1.0    # 竖直峰峰值的统计窗口（一次跳的滞空就在这个量级）
MIN_BEHAVIOR_POINTS = 3  # 近 1 s 内至少要这么多个有效检测点，才允许给出行为判定。
                       # 2026-11-06 补。峰峰、净位移、原地比、周期这几个量全部
                       # 在「1 s 窗口里取若干点」的基础上算，点数不足 2 个时它们
                       # 一律退化成 0，而判定会一路落到最后的「姿态兜底」，
                       # 用 bbox 的宽高比猜一个 sitting / standing —— 等于拿
                       # 无效数据编结论。实测用户举手机播金毛奔跑视频时检测率
                       # 只有约 13%，1 s 窗口经常只剩 1 个点，6 次判定全部是
                       # 「运动量全 0」的 sitting/standing，而画面里狗明明在跑。
                       # 宁可返回 unknown 让上层保持原状，也不要给假结论。
JUMP_PEAK_BODY = 0.35  # 竖直峰峰值 ≥ 0.35 体高才算跳；趴卧呼吸起伏远小于此
JUMP_DOMINANCE = 1.8   # 竖直峰峰必须 ≥1.8 倍横向峰峰。原为 1.2，太松：
                       # 斜向跑、朝镜头跑的竖直分量本来就接近横向分量，
                       # 真跳是接近垂直的，竖直位移该远大于横向。
JUMP_MAX_HPEAK = 0.60  # 横向峰峰 >0.6 体高就不可能是跳（那是在跑）。
                       # 补这条是因为 1.2 倍只约束了比值，没约束绝对值：
                       # 两只比例同为 1.3 的情形，横向 0.2（原地颠）和 1.5（在跑）
                       # 也都满足，需要绝对值兜底。
JUMP_NET_RATIO = 2.0   # 竖直峰峰必须 ≥2.0 倍「同窗口净位移」才算跳。
                       # 2026-11-06 补。峰峰大 + 净位移也大 = **单向平移**，那是在跑或
                       # 被拍摄者摇镜头，不是跳。真跳是上下往复，一个窗口内回到原处，
                       # 净位移→0，比值发散；跑动则是净位移≈峰峰，比值≈1。
                       # 实测（微信奔跑视频 407 帧）：15 帧被误判 jumping 的比值全在
                       # 0.92~1.78，没有一例 ≥2；而合成跳测试远大于此。
                       # 前三条判据都只约束「形状」（竖直多大、横向多小），
                       # 只有这条约束「是否往复」——形状在跑动里同样满足，
                       # 这就是「奔跑被判成蹦跳」长期修不干净的根本原因。

# 光流能量的归一化基准帧间隔。flow_energy 原本是「每帧移动几个 ROI 高度」，
# 是逐帧量，帧率一高数值就等比缩小（10 Hz 时只有 2 Hz 的 1/5），
# 阈值 0.12 会整体失效。这里统一折算到 2 Hz 当量，帧率变化不影响该值。
FLOW_REF_DT = 0.5

STALE_DISP_BODY = 0.22  # 速度中位数失效时的兜底判据：NET_DISP_SEC 窗口内的**净位移**
                        # 超过 0.22 个体高，就认为确实在移动（而不是像素抖动）。
                        # 用体高归一而不是固定像素，避免判定随分辨率/目标大小漂移：
                        # 1280×720 与 1920×1080 下同一个动作必须给同一个答案。
# 径向运动（朝镜头/背离镜头）判据。中心点位移表达不了这种运动：狗从远处冲向
# 镜头时 cx/cy 几乎不动，只有身体在放大，_robust_speed_px_s() 恒为 0，
# 判定会一路掉进「静止」分支按宽高比猜成 sitting —— 实测柯基跑向镜头的视频
# 82 帧里 standing 30 / sitting 24，running 一次都没有。
# 体高的相对变化率 (dh/h/dt) 单位是 1/s，与 vb（体长/秒）同量纲，可直接比阈值。
SCALE_SEC = 1.0         # 体高变化率的统计窗口
SCALE_MOVE_BODY = 0.35  # ≥0.35 体高/秒算在动（与 walking 门槛 0.35 对齐）
SCALE_RUN_BODY = 1.00   # ≥1.0 体高/秒算跑（1 秒内身体放大一倍）
PLAY_MAX_NET_BODY = 0.20  # 判 playing（原地扑/甩/挠）要求 1s 窗口净位移 ≤0.2 体高。
                          # 2026-11-06 补。原判据是「光流大 + 原地比高 + 无周期」，
                          # 但**目标在移动时 ROI 跟着目标走，像素同样不出 ROI**，
                          # 原地比必然偏高 —— 这个指标天生分不开「原地动作」和
                          # 「整体平移」。实测狗在跑的两个视频里，playing 的净位移
                          # 中位数 0.27 / 0.46，根本不是原地；而真正的原地动作净位移≈0。
                          # 收紧后跑动会落回 walking/running，代价是「往前扑」这种
                          # 带位移的玩耍会被算成 walking —— 宁可少报也不要把跑动
                          # 说成撒娇（前端 playing 映射的是 sajiao 动作）。
TRACK_TTL = 8.0         # tracker 超过该秒数未更新则删除（原代码永不删除）
STALE_KEEP_SEC = 2.0    # 本帧没检测到时，沿用上一次判定的最长时间。
                        # 实测丢检段：最长 2.29s，多数 0.6~1.3s（目标过大/被裁切，
                        # 见 MultiPetTracker.stale_result 的说明）。取 2.0s 覆盖大多数，
                        # 代价是「宠物真走出画面」最多晚 2s 才报 absent —— 可接受。
                        # 不可取更大：位置历史不动，动作会僵在一个标签上。
                        # 也不可取更小：1.0s 只能覆盖一半丢检段，3D 照样回待机。
HUMAN_HOLD_SEC = 3.0    # 人短暂丢失时保持 present 的宽限，避免画面抖动导致状态闪烁

# CSI 摄像头走 GStreamer，不能用 cv2.VideoCapture(0)：
#   K1 的 sensor 不是标准 V4L2 直读设备，必须经 spacemitsrc 插件。
#   该 pipeline 已在 grab_frame.py 实测（20/20 帧、18.56 fps、1920x1080）。
CSI_GST_TPL = ("spacemitsrc location={json_path} close-dmabuf=1 ! "
               "video/x-raw,format=NV12,width={w},height={h} ! "
               "appsink drop=true max-buffers=2 sync=false")


# ────────────────────────── 预处理 / 后处理 ──────────────────────────

def letterbox(img, new_size=IMG_SIZE, color=114):
    """等比缩放到 new_size×new_size，居中填充。返回 (图, scale, pad_w, pad_h)"""
    h, w = img.shape[:2]
    scale = min(new_size / w, new_size / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((new_size, new_size, 3), color, dtype=np.uint8)
    pad_w = (new_size - nw) // 2
    pad_h = (new_size - nh) // 2
    canvas[pad_h:pad_h + nh, pad_w:pad_w + nw] = resized
    return canvas, scale, pad_w, pad_h


def nms(boxes, scores, iou_thres):
    """标准贪心 NMS，boxes 为 xyxy"""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_thres]
    return keep


def decode_yolov8(output, scale, pad_w, pad_h, orig_w, orig_h):
    """
    解码 YOLOv8 原生输出 (1, 4+nc, N)。
    返回 [(cls_id, conf, (x1,y1,x2,y2)), ...]，坐标已还原到原图。

    ⚠️ 若换成官方 model-zoo 的 yolov8n_no_dfl.q.onnx（DFL 在图外、多分支输出），
       此函数不适用，需要按该模型的输出结构改写。
    """
    pred = output[0]                 # (4+nc, N)
    if pred.shape[0] < pred.shape[1]:
        pred = pred.T                # → (N, 4+nc)
    boxes_xywh = pred[:, :4]
    cls_scores = pred[:, 4:]

    cls_ids = np.argmax(cls_scores, axis=1)
    confs = cls_scores[np.arange(cls_scores.shape[0]), cls_ids]

    mask = confs >= CONF_THRES
    if not np.any(mask):
        return []
    boxes_xywh = boxes_xywh[mask]
    cls_ids = cls_ids[mask]
    confs = confs[mask]

    # 保留猫狗 + 球类（行为判定用）+ 人（独立的人类在场通道）。
    # 注意：人只是"解码后保留"，下游 MultiPetTracker 不认识 person，
    #       会在 cls_name is None 处跳过，所以宠物链路的行为与计数都不受影响。
    keep_cls = np.array([c in PET_CLASSES or c in BALL_CLASSES or c in HUMAN_CLASSES
                         for c in cls_ids])
    boxes_xywh = boxes_xywh[keep_cls]
    cls_ids = cls_ids[keep_cls]
    confs = confs[keep_cls]
    if len(boxes_xywh) == 0:
        return []

    # cxcywh → xyxy（当前在 640 输入尺度）
    cx, cy, bw, bh = (boxes_xywh[:, 0], boxes_xywh[:, 1],
                      boxes_xywh[:, 2], boxes_xywh[:, 3])
    boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)

    # 反 letterbox：先减 padding 再除 scale
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad_w) / scale
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad_h) / scale
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, orig_w)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, orig_h)

    out = []
    # 按类别分别 NMS（class-aware），与 Ultralytics 默认一致
    for cid in np.unique(cls_ids):
        idx = np.where(cls_ids == cid)[0]
        keep = nms(boxes[idx], confs[idx], NMS_IOU)
        for k in keep:
            i = idx[k]
            out.append((int(cls_ids[i]), float(confs[i]),
                        tuple(float(v) for v in boxes[i])))
    return out


# ────────────────────────── 跟踪与行为判定 ──────────────────────────

def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


class PetTracker:
    """
    移植自 yolo_server.py:243-461，但补了三样东西：
      · last_seen_ts   —— 用于淘汰（原代码没有，cleanup 是空的）
      · behavior_start_ts —— 用于算 duration_sec（原代码没有）
      · 速度按秒归一     —— 原代码是「像素/帧」，帧率一变全错
    """

    def __init__(self, pet_id, now):
        self.pet_id = pet_id
        self.class_name = ""
        # 缓冲区按最大可能帧率留足（400 点 ≈ 20 Hz × 20 s），真正的窗口
        # 由 update() 里的按秒裁剪决定，不再依赖 maxlen。
        self.position_history = deque(maxlen=400)  # [(ts, cx, cy)]
        # 体高历史 [(ts, bbox_h)]，专供径向运动判定。中心点位移看不见
        # 「朝镜头跑」，只有身体在变大 —— 见 _scale_vel()。
        self.scale_history = deque(maxlen=400)
        self.behavior = "unknown"
        self.behavior_stable_count = 0
        self.last_behavior = "unknown"
        self.latest_behavior = "unknown"
        self.latest_confidence = 0.5
        self.latest_basis = ""
        self.last_conf = 0.0
        self.last_bbox = None
        self.bbox_stable = True
        self.last_seen_ts = now
        # 行为计时：稳定行为一变就重置起点
        self.behavior_start_ts = now
        # 静止起点：与行为标签解耦，专供 long_still 判定。
        #
        # ⚠️ 不能用 behavior_start_ts 判「长时间静止」：
        #    long_still 的成立条件之一是 (now - behavior_start_ts) > 60，
        #    但它一旦成立，get_stable_behavior 就会把 behavior_start_ts 重置成 now，
        #    于是下一帧条件立刻不满足、退回 lying，计时器再被重置一次。
        #    实测复现：静态画面下 duration 一直卡在几十秒，服务跑 11 分钟
        #    也涨不到 60 秒——「宠物趴卧 45 分钟」这种数据永远产生不出来。
        #    用独立的 still_since_ts 后，静止计时不再被标签切换打断。
        self.still_since_ts = None
        self._last_confirmed = "unknown"
        # 事件型行为（跳跃/玩耍）的锁存状态，见 EVENT_LABELS
        self._event_label = ""
        self._event_until = 0.0
        # ── L0 动作识别所需的运动特征（每帧由 update_motion 更新）──────────
        # prev_roi：上一帧的 ROI 灰度图，缩到 64×64，用于算光流
        self.prev_roi = None
        # flow_energy：光流平均幅值 / ROI 高度，即「每帧移动几个身体高度」
        self.flow_energy = 0.0
        # inplace_ratio：竖向光流占总光流的比例，用来区分「整体平移」和「原地动作」
        self.inplace_ratio = 0.0
        # 最近一次 update 的时间戳与上一次算光流的时间戳，用于把逐帧量折算成
        # 「2 Hz 当量」。两者都为 None/0 时（离线单测只调 update_motion 的情形）
        # 折算系数取 1.0，行为与改造前一致。
        self._now = 0.0
        self._motion_ts = None
        # 去抖用：当前连续相同标签串的起点时间，配合 *CONFIRM_SEC 做按秒确认
        self._streak_ts = None

    def update(self, now, cx, cy, cls_name, bbox, conf=0.0):
        self.class_name = cls_name
        self.last_seen_ts = now
        self.last_conf = conf
        self._now = now
        # 原来存的是纯坐标，这里带上时间戳，便于按秒而非按帧归一
        self.position_history.append((now, cx, cy))
        # 按秒裁剪：窗口时长与采样帧率解耦，改 --fps 不会改变判定语义
        cutoff = now - HISTORY_SEC
        while self.position_history and self.position_history[0][0] < cutoff:
            self.position_history.popleft()
        if bbox is not None:
            self.bbox_stable = (self.last_bbox is None
                                or iou(bbox, self.last_bbox) > 0.6)
            self.last_bbox = bbox
            bh = float(bbox[3] - bbox[1])
            if bh > 1.0:
                self.scale_history.append((now, bh))
                s_cut = now - HISTORY_SEC
                while self.scale_history and self.scale_history[0][0] < s_cut:
                    self.scale_history.popleft()

        # 维护静止起点：窗口内径向偏差极小就持续累积，一旦动起来立刻清零。
        # _position_stability() 依赖 position_history，必须在 append 之后调用。
        s_now = self._position_stability()
        if s_now < 2.0:
            if self.still_since_ts is None:
                self.still_since_ts = now
        else:
            self.still_since_ts = None

    def _sample_dt(self):
        """最近几个位置点的中位采样间隔（秒）。

        只用于「够不够点数做判定」这一道闸门，不参与任何运动量计算。
        单点/异常时兜底 0.5 s（= 2 Hz，也就是最常见的 --behavior-hz 2.0）。
        """
        pts = list(self.position_history)
        if len(pts) < 2:
            return 0.5
        gaps = []
        for i in range(max(1, len(pts) - 5), len(pts)):
            g = pts[i][0] - pts[i - 1][0]
            if g > 0:
                gaps.append(g)
        if not gaps:
            return 0.5
        gaps.sort()
        return gaps[len(gaps) // 2]

    def _window(self, sec):
        """返回最近 sec 秒内的位置点（最老在前）。窗口按秒定义，与采样帧率无关。"""
        pts = list(self.position_history)
        if not pts:
            return []
        t_end = pts[-1][0]
        # 容差放在「包含」一侧：ts 恰好落在窗口边界上时必须被计入，
        # 否则 2 Hz 下 STABLE_SEC=2.0 会只剩 4 个点（少了一个身位的散布），
        # 位置稳定度 S 系统性偏小，和改造前对不上。
        lo = t_end - sec - 1e-9
        return [p for p in pts if p[0] >= lo]

    def _history_full(self):
        """历史窗口是否已填满（用于 long_still 的「窗口满」条件）。"""
        pts = list(self.position_history)
        return len(pts) >= 2 and (pts[-1][0] - pts[0][0]) >= HISTORY_SEC * 0.999

    def _bucketed(self, sec, k):
        """把最近 sec 秒切成 k 个等长时间片，每片用首尾点算位移。

        返回 [(dt, dx, dy)]，每项覆盖的外推时长都相同。

        为什么按时间切片而不是「取最近 N 步的中位数」：后者在帧率提高后，
        同样的窗口里步数变多、单步时长变短，「中位数越过 0」的时刻会随帧率
        漂移（实测 10 Hz 比 2 Hz 晚 0.2 s），阈值就得跟着重标。
        按时间切片后，2/5/10 Hz 切出的片边界落在同一批物理时刻上，
        速度序列本身完全相同，只有片内采样密度变化 —— 阈值不用重标。
        """
        pts = self._window(sec)
        if len(pts) < 2:
            return []
        t0, t1 = pts[0][0], pts[-1][0]
        span = t1 - t0
        if span <= 1e-6:
            return []
        out = []
        for b in range(k):
            lo = t0 + span * b / k
            hi = t0 + span * (b + 1) / k
            seg = [p for p in pts if lo - 1e-9 <= p[0] <= hi + 1e-9]
            if len(seg) < 2:
                continue
            dtt = seg[-1][0] - seg[0][0]
            if dtt <= 1e-6:
                continue
            out.append((dtt, seg[-1][1] - seg[0][1], seg[-1][2] - seg[0][2]))
        return out

    def _robust_speed_px_s(self):
        """
        鲁棒速度：每个时间片的首尾位移/时长，取中位数。

        三个时间片中位数 = 抗单个离群片（跟踪跳点、误匹配），
        且片长固定 0.5 s，与采样帧率无关。
        """
        segs = self._bucketed(SPEED_SEC, SPEED_BUCKETS)
        if not segs:
            return 0.0
        speeds = sorted(math.hypot(dx, dy) / dt for dt, dx, dy in segs)
        return speeds[len(speeds) // 2]

    def _net_disp_body(self, body):
        """NET_DISP_SEC 秒窗口内的净位移，按体高归一（单位：体高）。

        与 _position_stability() 的区别：后者是**像素 RMS**，量纲没归一，
        阈值 3.0/1.5/1.0 会随分辨率和目标大小漂移；这里专供「速度估计是否
        滞后」的判据使用，要求尺度无关，所以除以体高。
        """
        pts = self._window(NET_DISP_SEC)
        if len(pts) < 2 or body <= 0:
            return 0.0
        _, x0, y0 = pts[0]
        _, x1, y1 = pts[-1]
        return math.hypot(x1 - x0, y1 - y0) / body

    def _peak_travel(self, body, sec=JUMP_PEAK_SEC):
        """最近 sec 秒内竖直 / 水平的**峰峰值**（单位：体高）。

        为什么不能只靠速度判跳：一次跳的滞空约 0.3~0.6 s，而速度是按 0.5 s
        时间片取净位移算的 —— 升上去再落回来，一个片内净位移≈0，vb 直接归零，
        判定掉进「静止」分支。实测 0.4 s 的跳在 5 Hz 下 raw 全被算成 sleeping。
        峰峰值对采样相位不敏感：只要有一个采样点落在滞空弧顶就能测出来。
        """
        pts = self._window(sec)
        if len(pts) < 2 or body <= 0:
            return 0.0, 0.0
        ys = [p[2] for p in pts]
        xs = [p[1] for p in pts]
        return (max(ys) - min(ys)) / body, (max(xs) - min(xs)) / body

    def _scale_vel(self):
        """体高的相对变化率（1/s），正=在接近镜头，负=在远离。

        专门解决「朝镜头跑」：狗从远处冲到镜头前时 cx/cy 几乎不动，中心点速度
        恒为 0，旧判定把这种运动当成静止。只有 bbox 高度在持续变大。
        分母取首尾体高的几何平均：直接除 h0 会在远距离（h0 很小）时把变化率
        放大到离谱，几何平均对「变大」和「变小」对称。
        """
        if len(self.scale_history) < 2:
            return 0.0
        t_end = self.scale_history[-1][0]
        lo = t_end - SCALE_SEC - 1e-9
        pts = [p for p in self.scale_history if p[0] >= lo]
        if len(pts) < 2:
            return 0.0
        t0, h0 = pts[0]
        t1, h1 = pts[-1]
        dt = t1 - t0
        if dt < 0.15 or h0 < 1.0 or h1 < 1.0:
            return 0.0
        base = math.sqrt(h0 * h1)
        return (h1 - h0) / base / dt

    def _position_stability(self):
        """STABLE_SEC 秒窗口内位置点的二维径向 RMS 偏差（像素）。

        在窗口内取 STABLE_SAMPLES 个**等距时刻**的采样点，而不是取全部点：
        帧率越高点越密，线性运动下 RMS 会系统性偏小（连续采样是 0.289L，
        5 个离散点是 0.316L，实测差 15%），阈值 3.0/1.5/1.0 就会随帧率漂移。
        等距重采样后，2 Hz 下取到的正好是原来的 5 个点，数值逐位一致。
        """
        pts = self._window(STABLE_SEC)
        if len(pts) < 2:
            return float("inf")
        t0, t1 = pts[0][0], pts[-1][0]
        if t1 - t0 > 1e-6 and len(pts) > STABLE_SAMPLES:
            samples = []
            for i in range(STABLE_SAMPLES):
                tt = t0 + (t1 - t0) * i / (STABLE_SAMPLES - 1)
                samples.append(min(pts, key=lambda p: abs(p[0] - tt)))
        else:
            samples = pts
        n = len(samples)
        mx = sum(p[1] for p in samples) / n
        my = sum(p[2] for p in samples) / n
        s = sum((p[1] - mx) ** 2 + (p[2] - my) ** 2 for p in samples) / n
        return math.sqrt(s)

    def _y_speed_norm(self, img_h):
        """Y 方向平均绝对变化 / 图像高度 / 秒"""
        if len(self.position_history) < 2 or img_h <= 0:
            return 0.0
        pts = list(self.position_history)
        total = 0.0
        cnt = 0
        for i in range(1, len(pts)):
            t0, _, y0 = pts[i - 1]
            t1, _, y1 = pts[i]
            dt = t1 - t0
            if dt <= 1e-6:
                continue
            total += abs(y1 - y0) / dt
            cnt += 1
        return (total / cnt) / img_h if cnt else 0.0

    # ══════════════ L0 动作识别：给几何规则补上运动学证据 ══════════════
    #
    # 旧实现只有三个特征：框的高宽比 A、像素速度 Vn、位置稳定性 S。
    # 其中 Vn 用「图像长边」归一化 —— 宠物离镜头近时同样速度算出来更大，
    # 同一只猫走远走近会被判成两种行为。这是最要命的一条。
    #
    # 现在补四样东西，全部可在板端实时算，且每一项都能写进 basis 供现场核对：
    #   1) 速度按**目标体高**归一化 → 体长/秒，与距离无关
    #   2) ROI 光流能量 → 区分「整体平移」和「原地动作」（挠、甩头、舔毛）
    #   3) 位移序列自相关 → 区分「来回踱步（有周期）」和「随机跑动」
    #   4) 竖直位移比 → 跳上跳下的关键特征

    def update_motion(self, canvas, bbox):
        """在当前帧的 ROI 上算光流。ROI 缩到 64×64，Farneback 在这个尺寸是微秒级，
        对 2fps 的分析循环几乎没有成本（整帧跑光流才是负担）。"""
        if canvas is None:
            return

        def _reset():
            """任何提前退出都必须把状态清空。
            否则 prev_roi 会停在几帧之前的图像上，下一帧算的是「跨帧光流」，
            幅值凭空翻倍，静息会被判成 playing。"""
            self.prev_roi = None
            self.flow_energy = 0.0
            self.inplace_ratio = 0.0

        try:
            x1, y1, x2, y2 = [int(round(v)) for v in bbox]
            H, W = canvas.shape[:2]
            x1 = max(0, min(W - 1, x1))
            y1 = max(0, min(H - 1, y1))
            x2 = max(x1 + 1, min(W, x2))
            y2 = max(y1 + 1, min(H, y2))
            if x2 - x1 < 8 or y2 - y1 < 8:
                _reset()
                return
            roi = canvas[y1:y2, x1:x2]
            g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            g = cv2.resize(g, (64, 64), interpolation=cv2.INTER_AREA)
        except Exception as e:
            print(f"[motion] ROI 预处理失败: {e}", flush=True)
            _reset()
            return

        prev = self.prev_roi
        self.prev_roi = g
        if prev is None or prev.shape != g.shape:
            # 冷启动帧：没有上一帧可比，光流按 0 计，不能沿用上一轮的残值
            self.flow_energy = 0.0
            self.inplace_ratio = 0.0
            return

        # 记录本次光流的时间戳与上一次的间隔：两次光流之间的真实 dt 决定折算系数。
        # 只有真正算了光流才推进时间戳（canvas 为 None / 冷启动不改），
        # 否则下一次会用一个偏小的 dt，把光流放大。
        prev_motion_ts = self._motion_ts
        self._motion_ts = self._now

        try:
            f = cv2.calcOpticalFlowFarneback(prev, g, None, 0.5, 2, 9, 2, 5, 1.1, 0)
        except Exception as e:
            print(f"[motion] 光流计算失败: {e}", flush=True)
            self.flow_energy = 0.0
            self.inplace_ratio = 0.0
            return

        mag = np.sqrt(f[..., 0] ** 2 + f[..., 1] ** 2)
        m = float(mag.mean())
        vy = float(np.abs(f[..., 1]).mean())
        self.inplace_ratio = vy / (m + 1e-6)
        # 归一化成「每帧移动几个 ROI 高度」，再折算到 FLOW_REF_DT（2 Hz 当量）。
        #
        # 这一步是提高帧率的关键：光流是**逐帧**量，10 Hz 下每帧位移只有
        # 2 Hz 的 1/5，不折算的话 0.12 这个阈值直接把所有动作判成静止。
        # 折算后同一个物理运动在任意帧率下给出同一个数，阈值不用重标。
        scale = 1.0
        if prev_motion_ts is not None and self._now > 0:
            dt = self._now - prev_motion_ts
            if dt > 1e-3:
                scale = FLOW_REF_DT / dt
        self.flow_energy = (m / 64.0) * scale

    def _vy_px_s(self):
        """竖直方向速度（px/s），取 SPEED_SEC 内各时间片首尾位移的中位数。"""
        segs = self._bucketed(SPEED_SEC, SPEED_BUCKETS)
        if not segs:
            return 0.0
        ds = sorted(abs(dy) / dt for dt, _dx, dy in segs)
        return ds[len(ds) // 2]

    def _periodicity(self):
        """位移序列的自相关。来回踱步的步幅序列有强周期性，随机跑动没有。
        返回 0~1，越大越像固定节奏。

        序列按时间切片重采样（PERIOD_BUCKETS 片），滞后按片数固定：
        帧率一变，原来「滞后 2 步」对应的物理时间是 2/fps，周期性判据会整体错位。
        切片后滞后 2 片恒等于 1.0 s，与帧率无关。
        """
        segs = self._bucketed(PERIOD_SEC, PERIOD_BUCKETS)
        if len(segs) < PERIOD_LAG_BUCKETS + 2:
            return 0.0
        d = [math.hypot(dx, dy) for _dt, dx, dy in segs]
        lag = PERIOD_LAG_BUCKETS
        a = np.asarray(d[:-lag], dtype=np.float32)
        b = np.asarray(d[lag:], dtype=np.float32)
        if a.std() < 1e-6 or b.std() < 1e-6:
            return 0.0
        r = float(np.corrcoef(a, b)[0, 1])
        if math.isnan(r):
            return 0.0
        return max(0.0, r)

    def analyze_behavior(self, bbox_w, bbox_h, img_w, img_h, now):
        """返回 (label, conf, basis)。basis 是给人看的判定依据，直接进接口。"""
        n = len(self.position_history)
        if n == 0:
            return ("unknown", 0.0, "无历史帧")

        # 有效样本闸门（2026-11-06）：近 1 s 内有效检测点不足时直接说不知道。
        # 理由见 MIN_BEHAVIOR_POINTS 的注释 —— 点数不足会让下面所有运动量归零，
        # 然后被最后的「姿态兜底」按 bbox 宽高比猜成一个坐/站。那种结论比
        # unknown 更有害：它会真的把 3D 切过去，看起来就是「识别不准」。
        #
        # ⚠️ 2026-11-07 修：闸门窗口必须跟着**采样率**走，不能写死 1.0 s。
        # 板端每帧都往 position_history 记点（20+ fps），1 s 内有 20 个点，
        # 写死 1.0 看不出问题；但 PC 端行为判定跑在 --behavior-hz 2.0，
        # 1 s 内最多只有 2 个点，MIN_BEHAVIOR_POINTS=3 永远达不到，
        # 于是动作恒为 unknown —— 网页与模拟器只剩「识别到宠物」。
        # 现场实测：16 个采样点连续跑 8 s，basis 一直是「近1s仅2帧」。
        gate_sec = max(JUMP_PEAK_SEC, MIN_BEHAVIOR_POINTS * self._sample_dt() * 1.15)
        _w = self._window(gate_sec)
        if len(_w) < MIN_BEHAVIOR_POINTS:
            return ("unknown", 0.0,
                    f"近{gate_sec:.1f}s仅{len(_w)}帧有效检测，不足以判定动作")

        A = bbox_h / bbox_w if bbox_w > 0 else 1.0     # 高/宽
        v_px_s = self._robust_speed_px_s()
        body = max(20.0, bbox_h)                      # 体高，作为长度基准
        vb = v_px_s / body                            # 体长/秒（距离无关）
        vy = self._vy_px_s() / body                   # 竖移/体高
        S = self._position_stability()
        B = self.bbox_stable
        Yr = self.position_history[-1][2] / img_h if img_h else 0.5
        flow = self.flow_energy
        ip = self.inplace_ratio
        period = self._periodicity()
        vpeak, hpeak = self._peak_travel(body)
        sv = self._scale_vel()          # 径向速度（体高/秒）：朝镜头跑时只有它在动

        # 净位移与峰峰同窗口（都是 1.0 s），可直接比值判定「是否往复」
        net_disp = self._net_disp_body(body)

        # 阈值的物理含义（体长/秒）：
        #   <0.10 静止或微动；0.10~0.35 慢速（吃、嗅、坐）；0.35~1.5 走动；
        #   >1.5 奔跑；竖移峰峰 >0.35 体高且压过水平 → 跳
        #
        # ⚠ 顺序在 2026-11-06 调整过，原因见下：「朝镜头跑」必须排在 jumping 之前。
        if vb < 0.10 and abs(sv) >= SCALE_MOVE_BODY:
            # 中心点几乎不动、身体却在放大/缩小 —— 朝镜头跑或往后退。
            #
            # **必须排在 jumping 之前**（原来排在后面，导致必然误判）：
            # 狗朝镜头跑来时，透视让它同时往画面下方移动，于是「竖直峰峰值大、
            # 横向峰峰值小」，正好命中下面那条 jumping 判据；而它的 vb（中心点
            # 速度）≈0，本该由这里用 sv 判成 running。实测用户播奔跑视频时
            # 整段输出 jumping，就是这条顺序造成的。
            #
            # 反过来不会误伤：真跳时身体离镜头距离不变、bbox 高度不变，
            # sv ≈ 0 < SCALE_MOVE_BODY，根本进不了这个分支。
            label = "running" if abs(sv) >= SCALE_RUN_BODY else "walking"
        elif (vpeak >= JUMP_PEAK_BODY
              and vpeak >= JUMP_DOMINANCE * max(hpeak, 1e-6)
              and hpeak <= JUMP_MAX_HPEAK
              and vpeak >= JUMP_NET_RATIO * max(net_disp, 1e-6)):
            # 跳要排在速度判定之前：它的净位移在时间片内自我抵消，速度≈0，
            # 放到后面会被当成静止（实测 5 Hz 下 0.4 s 的跳全判 sleeping）。
            #
            # 但原来只要「竖直 ≥1.2 倍水平」就够，太松 —— 斜向跑、朝镜头跑、
            # 甚至是画面抖动都能满足。真跳的物理特征是**横向几乎不动**，
            # 所以再加两条硬约束：竖直必须显著压过水平（1.8 倍），
            # 且横向峰峰不超过 0.6 个体高（跑到半个身位以外的绝不是跳）。
            #
            # 2026-11-06 再加第四条：**峰峰必须显著大于净位移**。
            # 前三条全是"形状"判据，而"朝镜头跑"在形状上与跳完全一致：
            # 竖直峰峰大（透视让它往下掉）、横向峰峰小（正对镜头没有横向分量）。
            # 实测 15 帧误判的比值全在 0.92~1.78 —— 那是**单向平移**，
            # 峰峰就等于净位移。真跳在一个窗口内上下往复、回到原处，
            # 净位移→0，比值会远大于 2。这条是唯一区分「往复」与「平移」的判据。
            label = "jumping"
        elif vb < 0.10:
            if n >= 3 and self._net_disp_body(body) >= STALE_DISP_BODY:
                # 最近 3 个位置点的净位移已超过 0.22 个体高，中位数速度却是 0 ——
                # 两者自相矛盾，说明速度估计滞后（或 tracker 刚重建、历史不足），
                # 不是宠物静止。原来这种情况会一路落到按宽高比兜底的分支：
                # 正面视角 A 落在 1.0~1.1 就输出 sitting，侧身 A<1.0 输出 sleeping。
                # 更糟的是置信度投票读的是同一个过时的 vb，会给出 0.90 的满票。
                # 这里直接按「已在移动」处理；速度不可测就不细分成走/跑。
                label = "walking"
            elif S < 3.0:
                if n >= 3 and (S < 1.5 or A < 1.0):
                    label = "sleeping"
                elif A < 0.9:
                    label = "lying"
                else:
                    label = "standing"
            else:
                # 3 ≤ S < 8 像素：轻微晃动，仍按姿态细分，但票数不足拿不到高置信度
                if n >= 3 and A < 1.0:
                    label = "sleeping"
                elif A < 1.1:
                    label = "sitting"
                else:
                    label = "standing"
        # 跳：竖直位移占主导才算跳。代码里的系数是 0.9，不是注释原先写的 70%——
        # 以代码为准，注释跟着改，避免下次调阈值时按错的那份改。
        # 原来只要求「框不稳」，但宠物跑向镜头时框同样不稳，会被误判成跳；
        # 加上「竖直主导」这个条件后，横向跑动不会再被当成跳跃。
        #
        # 2026-11-06 再加横向上限：这条是 vy（中位数绝对竖直速度）触发的，
        # 而画面抖动、手持手机晃动都会让 vy 虚高。真跳横向不会走出半个身位，
        # 所以横向峰峰超限的一律按「在跑」处理，交给下面的 vb 分支。
        # 净位移比同样要加：朝镜头冲过来时 vy 会很大（整个框在往下压），
        # 实测 2.33 s 那两帧 vy=0.81/1.08 却被判成跳，而体长速已经到 2.13/3.04
        # ——那是明确的奔跑。加上净位移比之后它们落回下面的 vb>=1.5 分支。
        elif vy > 0.60 and hpeak <= JUMP_MAX_HPEAK \
                and vpeak >= JUMP_NET_RATIO * max(net_disp, 1e-6) \
                and (not B or vy >= 0.9 * max(vb, 1e-6)):
            label = "jumping"
        elif vb >= 1.5:
            label = "running"
        elif vb >= 0.35:
            # 高光流 + 竖向占优 + 无周期 → 原地动作（扑、甩、挠），归到 playing。
            # 必须再加净位移上限：光流大只说明画面在动，平移时 ROI 跟着目标走，
            # 光流和原地比同样满足，只有净位移能把「原地」和「在走」分开。
            if flow > 0.12 and ip > 0.55 and period < 0.40 \
                    and net_disp <= PLAY_MAX_NET_BODY:
                label = "playing"
            else:
                label = "walking"
        elif vb >= 0.10:
            if A < 0.85 and Yr > 0.5:
                label = "coquettish"
            elif A < 0.9:
                label = "eating"
            else:
                label = "sitting"
        else:
            # 只剩 n<3 且 S≥8 的冷启动情形：历史不足，按姿态兜底
            if A < 1.1:
                label = "sitting"
            else:
                label = "standing"

        # long_still 覆盖：窗口满 + 几乎不动 + 真的静止超过 60 秒
        still_dur = (now - self.still_since_ts) if self.still_since_ts is not None else 0.0
        if self._history_full() and S < 2 and still_dur > 60:
            label = "long_still"

        # 置信度不再用拍脑袋的公式，而是数「有几个独立特征支持当前判断」。
        # 三票全中 0.90，全不中 0.45 —— 宁可报低，也不给一个看着自信的错答案。
        votes = 0
        if n >= 4:
            votes += 1
        if (label in ("walking", "running", "playing") and vb >= 0.35) \
                or (label == "jumping" and vpeak >= JUMP_PEAK_BODY) \
                or (label in ("lying", "sleeping", "sitting", "standing") and vb < 0.35):
            votes += 1
        if (label == "playing" and flow > 0.12 and net_disp <= PLAY_MAX_NET_BODY) or \
           (label in ("lying", "sleeping") and flow < 0.08) or \
           (label in ("walking", "running") and flow > 0.05):
            votes += 1
        conf = 0.45 + 0.15 * votes

        basis = (f"体长速{vb:.2f}/s 光流{flow:.2f} 原地{ip:.2f} "
                 f"周期{period:.2f} 竖移{vy:.2f} 峰峰{vpeak:.2f} 横峰{hpeak:.2f} "
                 f"净移{net_disp:.2f} 稳定{S:.1f} 径向{sv:+.2f}/s")
        self.latest_behavior = label
        self.latest_confidence = conf
        self.latest_basis = basis      # 供检测丢失时的 stale 沿用，别丢依据
        return (label, round(conf, 3), basis)

    def get_stable_behavior(self, new_behavior, now):
        """稳定化：去抖计数达标才切换，切换时重置行为计时起点。

        阈值非对称：静息组 → 运动组只需 MOVE_CONFIRM_SEC，反向仍需
        STABLE_CONFIRM_SEC。原因是起步/奔跑通常在 3 秒内穿过画面，对称去抖
        会让整段经过都停在 sitting/sleeping 上（scripts/behavior_lag_probe.py
        可复现）；而运动回落静息保持较慢的确认，避免停下瞬间标签闪烁。
        """
        if new_behavior == self.last_behavior:
            self.behavior_stable_count += 1
        else:
            self.behavior_stable_count = 1
            self.last_behavior = new_behavior
            self._streak_ts = now
        if self._streak_ts is None:
            self._streak_ts = now

        # 按「秒」确认而不是按观测次数：帧率提高后观测变密，但确认延迟
        # 必须保持同样的物理时长，否则 10 Hz 下去抖只要 0.3 s，标签会闪。
        rest_group = ("lying", "long_still", "sleeping")
        need_sec = STABLE_CONFIRM_SEC
        if self.behavior in rest_group and new_behavior not in rest_group:
            need_sec = MOVE_CONFIRM_SEC

        if (now - self._streak_ts) >= need_sec - 1e-9:
            if self.behavior != new_behavior:
                # 静息组内部互相切换（lying ↔ long_still ↔ sleeping）不是真的换行为，
                # 不能重置起点，否则界面上的「持续时间」会一直归零。
                # 从静止转为走动/奔跑才需要重新计时。
                if not (self.behavior in rest_group and new_behavior in rest_group):
                    self.behavior_start_ts = now
                self.behavior = new_behavior
            # 一旦确认过就不再退回 unknown
        # 事件型行为（跳跃/玩耍）不等确认：raw 一出现就锁存 EVENT_HOLD_SEC 秒。
        #
        # 为什么必须绕开 confirming：这两个动作物理上只持续 0.3~0.6 s，
        # 而跳→跳的确认窗口是 STABLE_CONFIRM_SEC=1.0 s，等于永远等不到；
        # 2 Hz 采样还会让整段滞空落在两帧之间。App 端按 1 Hz 拉快照，
        # 不做锁存的话「识别到跳」这个事件在传输前就被过滤掉了。
        if new_behavior in EVENT_LABELS:
            if self._event_label != new_behavior:
                self._event_label = new_behavior
                self.behavior_start_ts = now   # 事件从首次出现起算持续时间
            self._event_until = now + EVENT_HOLD_SEC
        if self._event_label:
            if now >= self._event_until:
                self._event_label = ""
            elif self.behavior == self._event_label:
                return self.behavior
            elif (now - self._streak_ts) < STABLE_CONFIRM_SEC:
                # 后续行为还没被确认，先把事件顶着，别让 0.5 s 后的 sleeping 抢走
                return self._event_label
            else:
                self._event_label = ""
        if self.behavior == "unknown" and self.latest_behavior != "unknown":
            return self.latest_behavior
        return self.behavior

    def duration_sec(self, now):
        return max(0.0, now - self.behavior_start_ts)


class MultiPetTracker:
    """贪心最近邻 + IoU 兜底，并补上「本帧已占用」集合（原代码缺这个会合并 ID）"""

    def __init__(self):
        self.trackers = {}
        self.next_id = 1

    def cleanup(self, now):
        """原代码这里是 pass，且从不调用。这里真正淘汰过期 tracker。"""
        dead = [tid for tid, t in self.trackers.items()
                if now - t.last_seen_ts > TRACK_TTL]
        for tid in dead:
            del self.trackers[tid]
        # 上限保护，室内场景不会有用
        if len(self.trackers) > 16:
            oldest = sorted(self.trackers.items(), key=lambda kv: kv[1].last_seen_ts)
            for tid, _ in oldest[:len(self.trackers) - 16]:
                del self.trackers[tid]

    def stale_result(self, now, keep):
        """本帧没检测到，但 tracker 仍在 keep 秒内活跃 → 返回它上一次的判定。

        为什么需要：实测微信奔跑视频 407 帧里 **235 帧（57.7%）完全检不到狗**，
        而丢检段里狗明明占满画面。抽帧 + 原始分数诊断确认两类原因：
          · 目标跨块 / 被放大后裁切 —— 四分块重试能把 dog 分数从 0.06 拉回 0.60
          · 只剩狗头一块 —— 模型以 0.74~0.81 的**高分**认成 sheep/cow（id 18/19），
            这类切图也救不回来（0.03~0.05）
        切图在板端要 4 倍推理（89ms→356ms，直接掉到 2fps 以下），不可接受。
        所以改成零成本的短时沿用：判定沿用上一次的稳定行为，**但要打 stale 标记**，
        绝不冒充本帧实时结果（本项目铁律：假数据不许标「实时」）。

        返回 None 表示真的没有宠物（不在 keep 窗口内，或从没检测到）。
        """
        best = None
        for t in self.trackers.values():
            if t.last_bbox is None or not t.class_name:
                continue
            age = now - t.last_seen_ts
            if age > keep:
                continue
            if best is None or age < best[0]:
                best = (age, t)
        if best is None:
            return None
        age, t = best
        # behavior 是 get_stable_behavior 的确认结果。tracker 刚建立、或刚被重建时
        # 它仍是 "unknown"，此时回落到最近一次原始判定 —— 总比报 unknown 有用
        # （实测直接取 behavior 会让 55.8% 的沿用帧都变成 unknown，等于白沿用）。
        last_label = t.behavior if t.behavior != "unknown" \
            else (t.latest_behavior or "unknown")
        return {
            "pet_id": t.pet_id,
            "pet": t.class_name,
            # behavior 是 get_stable_behavior 的结果，丢失期间没有被改写，
            # 正好就是「最后一次确认的动作」——前端据此继续播 3D，不会回待机。
            "label": last_label,
            "action": last_label,
            "action_raw": t.latest_behavior,
            "action_conf": t.latest_confidence,
            "basis": f"{t.latest_basis} ［本帧未检出，沿用 {age * 1000:.0f} ms 前］",
            # 置信度给 0：本帧确实没有检测结果，不拿上次的 conf 冒充
            "conf": 0.0,
            "last_conf": round(t.last_conf, 3),
            "duration_sec": round(t.duration_sec(now), 1),
            "bbox": [round(v, 1) for v in t.last_bbox],
            "speed_px_s": 0.0,
            "stale": True,
            "stale_age_ms": round(age * 1000),
        }

    def update_or_create(self, now, detections, img_w, img_h, canvas=None):
        diag = math.hypot(img_w, img_h) if img_w and img_h else 600
        max_dist = 0.5 * diag
        claimed = set()
        results = []

        for cls_id, conf, bbox in detections:
            cls_name = PET_CLASSES.get(cls_id)
            if cls_name is None:
                continue
            x1, y1, x2, y2 = bbox
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2

            best, best_dist = None, max_dist
            for tid, t in self.trackers.items():
                if tid in claimed or not t.position_history:
                    continue
                if t.class_name != cls_name:
                    continue
                _, lx, ly = t.position_history[-1]
                d = math.hypot(cx - lx, cy - ly)
                if d < best_dist:
                    best, best_dist = t, d

            if best is None:   # IoU 兜底（阈值 0.15，与原代码一致）
                for tid, t in self.trackers.items():
                    if tid in claimed or t.last_bbox is None or t.class_name != cls_name:
                        continue
                    if iou(bbox, t.last_bbox) > 0.15:
                        best = t
                        break

            if best is None:
                best = PetTracker(self.next_id, now)
                self.next_id += 1
                self.trackers[best.pet_id] = best

            claimed.add(best.pet_id)
            best.update(now, cx, cy, cls_name, bbox, conf)
            w = max(0.0, x2 - x1)
            h = max(0.0, y2 - y1)
            # L0：先补运动特征（光流/原地比），再做判定
            best.update_motion(canvas, bbox)
            raw, act_conf, basis = best.analyze_behavior(w, h, img_w, img_h, now)
            stable = best.get_stable_behavior(raw, now)
            results.append({
                "pet_id": best.pet_id,
                "pet": cls_name,
                "label": stable,
                # 新增：动作字段与判定依据。label 保留，App 老版本照常可用；
                # action 与 label 同值，只是语义更明确，便于前端直接展示。
                "action": stable,
                # 稳定化前的原始判定。action 会被去抖/事件锁存改写，
                # 排查「为什么判成 X」时必须看 raw，否则稳定化会把错的 raw 盖住。
                "action_raw": raw,
                "action_conf": act_conf,
                "basis": basis,
                "conf": round(conf, 3),
                "duration_sec": round(best.duration_sec(now), 1),
                "bbox": [round(v, 1) for v in bbox],
                "speed_px_s": round(best._robust_speed_px_s(), 1),
            })

        # 未被本帧匹配到的 tracker：清掉光流状态。
        #
        # 不清的话，宠物离开 2~8 秒（TRACK_TTL 内 tracker 不删除）再回来，
        # 第一帧算的是「几秒前 vs 现在」的光流，幅值必然爆炸，
        # 直接被判成 playing。这是子代理审查发现的真实缺陷。
        for tid, t in self.trackers.items():
            if tid not in claimed:
                t.prev_roi = None
                t.flow_energy = 0.0
                t.inplace_ratio = 0.0

        # 与 App 侧一致：优先狗，其次高置信度，其次大框
        results.sort(key=lambda r: (0 if r["pet"] == "dog" else 1,
                                    -r["conf"],
                                    -(r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1])))
        return results


# ────────────────────────── 人类在场通道 ──────────────────────────

class HumanWatcher:
    """
    人类在场检测。与宠物链路完全隔离的独立通道。

    为什么不复用 PetTracker：
      PetTracker 输出的行为标签（lying / sitting / running / …）是给宠物设计的。
      人的姿态语义不同——站着、坐着看手机、蹲下喂猫，被同一套运动学判成
      同一个标签，会向 App 传出语义错误的结果。所以这里只回答三个问题：
      有没有人、有几个人、置信度最高的那个人在画面哪里。

    隔离保证（"不影响原项目功能"的具体含义）：
      1. 不读写 MultiPetTracker 的任何状态，两个对象互不引用；
      2. 输出全部放在 payload 的新增 human 字段里，
         既有字段 pet / label / conf / duration_sec / count / all 的
         取值路径与语义一字未改；
      3. 人不会被计入 count（count 仍等于宠物数），
         也不会顶替 pet 字段，App 侧不做改造也能继续工作。

    宽限期（hold_sec）的必要性：低帧率下（默认 2 fps）人一个转身就可能连续
    两帧漏检。若立即置 present=False，App 会看到"有人—没人—有人"的闪烁。
    这里用 3 秒宽限吸收抖动，duration_sec 也因此是"连续在场"而非"累计在场"。
    """

    def __init__(self, conf_thres=HUMAN_CONF_THRES, hold_sec=HUMAN_HOLD_SEC):
        self.conf_thres = conf_thres
        self.hold_sec = hold_sec
        self.lock = threading.Lock()
        self.first_seen = None
        self.last_seen = None
        self.count = 0
        self.best_conf = 0.0
        self.best_bbox = []
        self.frames_with_human = 0
        self.frames_total = 0

    def update(self, detections, now):
        """detections 是 decode_yolov8 的原始输出 [(cls_id, conf, bbox), ...]"""
        persons = [d for d in detections
                   if d[0] in HUMAN_CLASSES and d[1] >= self.conf_thres]
        with self.lock:
            self.frames_total += 1
            if persons:
                self.frames_with_human += 1
                self.last_seen = now
                self.count = len(persons)
                persons.sort(key=lambda d: d[1], reverse=True)
                self.best_conf = persons[0][1]
                self.best_bbox = [round(v, 1) for v in persons[0][2]]
                if self.first_seen is None or (now - self.first_seen) > 86400:
                    self.first_seen = now
                elif self._expired(now):
                    # 中间断档超过宽限期，重新起算连续在场
                    self.first_seen = now
            elif self._expired(now):
                self.count = 0
                self.best_conf = 0.0
                self.best_bbox = []
                self.first_seen = None

    def _expired(self, now):
        return self.last_seen is not None and (now - self.last_seen) > self.hold_sec

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            present = (self.last_seen is not None
                       and (now - self.last_seen) <= self.hold_sec)
            if not present:
                self.count = 0
                self.best_conf = 0.0
                self.best_bbox = []
            duration = 0.0
            if present and self.first_seen is not None:
                duration = max(0.0, now - self.first_seen)
            return {
                "present": bool(present),
                "count": int(self.count) if present else 0,
                "conf": round(float(self.best_conf), 3) if present else 0.0,
                "bbox": list(self.best_bbox) if present else [],
                "duration_sec": round(duration, 1),
                "last_seen_sec": (round(now - self.last_seen, 1)
                                  if self.last_seen is not None else -1.0),
                "conf_thres": self.conf_thres,
            }


# ────────────────────────── 推理服务 ──────────────────────────

class VisionWorker:
    def __init__(self, model_path, source_kind, source_arg, target_fps,
                 csi_w=1920, csi_h=1080):
        self.model_path = model_path
        self.source_kind = source_kind
        self.source_arg = source_arg
        self.csi_w = csi_w
        self.csi_h = csi_h
        self.interval = 1.0 / max(0.1, target_fps)
        self.lock = threading.Lock()
        self.tracker = MultiPetTracker()
        self.human_watcher = HumanWatcher()   # 独立通道，与 tracker 零共享状态
        # 仲裁开关：默认 False。为 True 时推理循环空转（不跑模型），
        # 但 HTTP 服务照常返回上一帧结果 —— 这是「LLM 优先」的实现方式。
        # 为什么需要它：CV 与 LLM 同时跑时，实验证据是 LLM 从 3.3 s 劣化到 90 s 超时，
        #   且把两边的 CPU 亲和性分开也无效 → 瓶颈是内存带宽而不是核数。
        #   唯一有效的手段是时间片：LLM 推理期间让 CV 让出带宽。
        self.paused = False
        self.pause_count = 0
        self.session = None
        self.input_name = None
        self.ready = False
        self.reason = "starting"
        self.frames = 0
        self.last_fps = 0.0
        # 对外发布的最新结果
        self.latest = {"ok": False, "reason": "starting"}
        self._stop = False
        # 按需抓帧：摄像头 pipeline 只能被一个进程持有，
        # 外部脚本 grab_frame.py 想抓图必须先 stop 服务（会中断前端）。
        # 这里复用循环里已经解码好的 frame，收到请求后下一帧顺手编码一张 JPEG，
        # 既不重开 pipeline、也不每帧都编码（1080p JPEG 约 20~30 ms，会吃掉 2fps 的余量）。
        self._snap_req = threading.Event()
        self._snap_jpeg = None
        self._snap_ts = 0.0

    def load_model(self):
        if ort is None:
            self.reason = "onnxruntime 未安装：pip install onnxruntime 或安装 spacemit-ort"
            return False
        try:
            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            # 只挑 SpaceMIT EP，跑不起来才退回 CPU —— 顺序即优先级
            avail = ort.get_available_providers()
            provs = [p for p in avail if "SpaceMIT" in p] or avail
            self.session = ort.InferenceSession(self.model_path, so, providers=provs)
            self.input_name = self.session.get_inputs()[0].name
            print(f"[model] loaded {self.model_path}")
            print(f"[model] providers = {self.session.get_providers()}")
            print(f"[model] spacemit_ep_imported = {HAS_SPACEMIT_EP}")
            print(f"[model] input = {self.input_name}")
            return True
        except Exception as e:
            self.reason = f"模型加载失败: {e}"
            print(f"[model] FAILED: {e}")
            return False

    def _grab(self):
        """取一帧 BGR 图；失败返回 None"""
        if cv2 is None:
            self.reason = "opencv 未安装：apt install python3-opencv"
            return None
        if self.source_kind == "image":
            img = cv2.imread(self.source_arg)
            if img is None:
                self.reason = f"图片读取失败: {self.source_arg}"
            return img
        if self.source_kind == "csi":
            return self._grab_csi()

        cap = getattr(self, "_cap", None)
        if cap is None:
            cap = cv2.VideoCapture(self.source_arg)
            if not cap.isOpened():
                self.reason = f"摄像头打开失败: {self.source_arg}"
                self._cap = None
                return None
            self._cap = cap
            print(f"[camera] opened {self.source_arg}")
        ok, frame = cap.read()
        if not ok:
            self.reason = "摄像头读帧失败"
            return None
        return frame

    def _grab_csi(self):
        """
        CSI 摄像头取帧（GStreamer spacemitsrc）。

        source_arg 是 camera_json 的路径，例如
            /usr/share/camera_json/csi3_camera_auto.json
        该 json 里的 out_width/out_height 必须与 pipeline 的 caps 一致，
        当前实测配置为 1920x1080。

        NV12 的处理：appsink 吐出的是单通道 NV12 buffer，
        cv2 返回 shape=(h*3/2, w) 的二维数组（1080*1.5=1620 行）。
        直接当灰度图送进模型会得到一条 1620 行、下半截是色度的错图，
        必须显式 cvtColor(YUV2BGR_NV12) 还原。
        """
        cap = getattr(self, "_csi_cap", None)
        if cap is None:
            if not os.path.exists(self.source_arg):
                self.reason = f"camera json 不存在: {self.source_arg}"
                return None
            pipe = CSI_GST_TPL.format(json_path=self.source_arg,
                                      w=self.csi_w, h=self.csi_h)
            cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)
            if not cap.isOpened():
                self.reason = "GStreamer pipeline 打不开（检查 camclk / cam-test 是否就绪）"
                print(f"[csi] FAILED: {pipe}")
                return None
            self._csi_cap = cap
            self._csi_fail = 0
            print(f"[csi] opened: {pipe}")

        ok, frame = cap.read()
        if not ok or frame is None:
            self._csi_fail += 1
            self.reason = "CSI 读帧失败"
            # 连续失败说明 pipeline 已死（sensor 掉电 / 时钟丢失），释放后重开
            if self._csi_fail >= 5:
                print("[csi] 连续 5 次读帧失败，重建 pipeline")
                cap.release()
                self._csi_cap = None
            return None
        self._csi_fail = 0

        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_NV12)
        self.reason = ""
        return frame

    def run(self):
        if not self.load_model():
            with self.lock:
                self.latest = {"ok": False, "reason": self.reason}
            # 模型起不来就不要空转，但 HTTP 服务继续提供 503，方便排查
            return

        self.ready = True
        self.reason = ""
        t_prev = time.monotonic()

        while not self._stop:
            loop_start = time.monotonic()
            # 仲裁：被 LLM 请求占用期间不跑推理，但仍保持 HTTP 可响应。
            # 注意这里不能 return，也不能跳过 payload 构造 —— 否则 App 会拿到
            # 503，触发 PerceptionRouter 的熔断，界面退回"模拟"。
            # 正确行为是"停在最后一帧"：数据不变、来源仍是 K1。
            if self.paused:
                self.pause_count += 1
                time.sleep(0.2)
                continue
            frame = self._grab()
            if frame is None:
                with self.lock:
                    self.latest = {"ok": False, "reason": self.reason}
                time.sleep(1.0)
                continue

            h, w = frame.shape[:2]
            # 有人请求抓帧就顺手存一张（只在有请求时编码，平时零开销）
            if self._snap_req.is_set():
                try:
                    okj, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                    if okj:
                        self._snap_jpeg = buf.tobytes()
                        self._snap_ts = time.time()
                except Exception as e:
                    print(f"[snap] 编码失败: {e}")
                self._snap_req.clear()
            canvas, scale, pw, ph = letterbox(frame)
            blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0

            try:
                outs = self.session.run(None, {self.input_name: blob})
            except Exception as e:
                print(f"[infer] 失败: {e}")
                time.sleep(1.0)
                continue

            detections = decode_yolov8(outs[0], scale, pw, ph, w, h)
            now = time.monotonic()
            # 注意：必须传原始帧 frame，不能传 canvas。
            # bbox 是 decode_yolov8 反 letterbox 之后的**原图坐标**（1920×1080），
            # 而 canvas 是 640×640 的 letterbox 画布。拿原图坐标去切画布，
            # x>640 的框会被整段 clamp 掉、x2-x1<8 直接跳过，
            # 结果是 prev_roi 永远写不进去、光流恒为 0——L0 的两条判定全部失效，
            # 而 basis 里还照样打印「光流0.00」，看起来像测过了。
            results = self.tracker.update_or_create(now, detections, w, h, frame)
            self.tracker.cleanup(now)
            # 人类通道独立更新：只读 detections，不碰 tracker 的状态
            self.human_watcher.update(detections, now)

            dt = now - t_prev
            t_prev = now
            self.last_fps = 1.0 / dt if dt > 0 else 0.0
            self.frames += 1

            # 本帧没检出宠物时，若 tracker 仍在窗口内活跃，沿用它上一次的判定
            # 并打 stale 标记（见 MultiPetTracker.stale_result）。
            # ⚠️ results 为空**不等于宠物走了**：实测 57.7% 的帧是「目标过大/
            #    被裁切」导致的漏检，狗明明占满画面。这里若直接报 absent，
            #    前端「连续 2 拍无宠即判离开」几乎每轮都满足，3D 就反复回待机。
            stale = self.tracker.stale_result(now, STALE_KEEP_SEC) if not results else None
            top = results[0] if results else stale

            payload = {
                "ok": True,
                "pet": top["pet"] if top else "",
                "label": top["label"] if top else "absent",
                # 新增：动作 + 判定依据 + 动作置信度。前端 K1Vision.ets 已在解析，
                # 老前端不读也不影响。
                "action": top["action"] if top else "absent",
                "action_raw": top.get("action_raw", "") if top else "",
                "action_conf": top["action_conf"] if top else 0.0,
                "basis": top["basis"] if top else "",
                "conf": top["conf"] if top else 0.0,
                "duration_sec": top["duration_sec"] if top else 0.0,
                "fps": round(self.last_fps, 2),
                "model": self.model_path.split("/")[-1],
                "ts": time.time(),
                "count": len(results) if results else (1 if stale else 0),
                "all": results if results else ([stale] if stale else []),
                # 本帧是否「沿用上次判定」。为 true 时上面所有字段都不是本帧实测，
                # 前端必须据此区分显示（铁律：假数据不许标「实时」）。
                "stale": bool(stale),
                "stale_age_ms": stale["stale_age_ms"] if stale else 0,
                # ↓ 新增字段。以上全部字段的取值路径与语义均未改动，
                #   App 侧不做改造也能照常工作；做了改造则能显示"画面里有几个人"。
                "human": self.human_watcher.snapshot(now),
            }
            with self.lock:
                self.latest = payload

            if self.frames % 20 == 0:
                print(f"[frame {self.frames}] fps={self.last_fps:.2f} "
                      f"pets={len(results)} top={payload['label']}"
                      f"{' (stale %d ms)' % payload['stale_age_ms'] if payload['stale'] else ''}")

            # 固定分析帧率：这是行为阈值成立的前提
            elapsed = time.monotonic() - loop_start
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)

    def snapshot(self):
        with self.lock:
            return dict(self.latest)


class Handler(BaseHTTPRequestHandler):
    worker = None

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/vision/frame.jpg"):
            # 按需抓帧，不中断服务（pipeline 独占，外部 grab_frame 必须先 stop）
            # 触发后等最多 1.5 s（2 fps 下最坏 0.5 s 出一帧，留足余量）
            self.worker._snap_req.set()
            deadline = time.monotonic() + 1.5
            jpg = None
            while time.monotonic() < deadline:
                jpg = self.worker._snap_jpeg
                if jpg is not None and time.time() - self.worker._snap_ts < 1.0:
                    break
                time.sleep(0.05)
            if not jpg or time.time() - self.worker._snap_ts > 1.5:
                self._send(503, {"ok": False, "reason": "抓帧超时（推理循环未跑帧？）"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg)))
            self.send_header("X-Snap-Ts", f"{self.worker._snap_ts:.3f}")
            self.end_headers()
            self.wfile.write(jpg)
        elif self.path.startswith("/v1/vision/latest"):
            data = self.worker.snapshot()
            if not data.get("ok"):
                self._send(503, data)
            else:
                self._send(200, data)
        elif self.path.startswith("/health"):
            hw = self.worker.human_watcher
            self._send(200, {
                "ok": self.worker.ready,
                "frames": self.worker.frames,
                "fps": round(self.worker.last_fps, 2),
                "source": f"{self.worker.source_kind}:{self.worker.source_arg}",
                "reason": self.worker.reason,
                "human_frames": hw.frames_with_human,
                "human_frames_total": hw.frames_total,
                "paused": self.worker.paused,
                "pause_loops": self.worker.pause_count,
            })
        else:
            self._send(404, {"ok": False, "reason": "not found"})

    def do_POST(self):
        # 仲裁接口：供 llm_gateway 在转发 LLM 请求前后调用。
        # 语义上 pause 不改任何状态机，只是让推理循环空转，
        # 因此 resume 之后行为追踪不会因为"时间跳变"而算错位移——
        # 空转期间 now 不推进到 tracker（循环直接 continue）。
        if self.path.startswith("/v1/vision/pause"):
            self.worker.paused = True
            self._send(200, {"ok": True, "paused": True})
        elif self.path.startswith("/v1/vision/resume"):
            self.worker.paused = False
            self._send(200, {"ok": True, "paused": False})
        else:
            self._send(404, {"ok": False, "reason": "not found"})

    # 关掉默认的每请求一行日志，板端串口会被刷屏
    def log_message(self, fmt, *args):
        return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="./yolov8n.onnx")
    ap.add_argument("--camera", default=None, help="V4L2 摄像头索引，如 0（USB 摄像头）")
    ap.add_argument("--csi", default=None,
                    help="CSI 摄像头：camera_json 路径，如 "
                         "/usr/share/camera_json/csi3_camera_auto.json")
    ap.add_argument("--csi-size", default="1920x1080",
                    help="CSI pipeline 的 caps 尺寸，必须与 camera_json 的 out_* 一致")
    ap.add_argument("--image", default=None, help="固定图片路径（无摄像头时测协议用）")
    ap.add_argument("--port", type=int, default=11436)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--fps", type=float, default=2.0,
                    help="分析帧率。特征窗口已全部按秒定义，改这个值只改采样密度、"
                         "不改判定语义（阈值不需要重标）。K1 板端 int8 单帧约 82 ms，"
                         "实测上限 ~11 fps；留出光流与 IO 余量后建议 ≤5 fps")
    args = ap.parse_args()

    if args.csi is not None:
        kind, arg = "csi", args.csi
    elif args.camera is not None:
        kind, arg = "camera", int(args.camera)
    elif args.image is not None:
        kind, arg = "image", args.image
    else:
        kind, arg = "csi", "/usr/share/camera_json/csi3_camera_auto.json"

    try:
        cw, ch = (int(v) for v in args.csi_size.lower().split("x"))
    except ValueError:
        cw, ch = 1920, 1080

    worker = VisionWorker(args.model, kind, arg, args.fps, csi_w=cw, csi_h=ch)
    Handler.worker = worker

    t = threading.Thread(target=worker.run, daemon=True)
    t.start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[http] listening on {args.host}:{args.port}")
    print(f"[http] GET /v1/vision/latest   GET /health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[exit] stopping")
        worker._stop = True
        server.shutdown()


if __name__ == "__main__":
    main()
