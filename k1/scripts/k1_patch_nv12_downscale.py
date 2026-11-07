#!/usr/bin/env python3
"""把 K1 vision_service 的 CSI 取帧改成「NV12 域先降采样，再转 BGR」。

背景
----
模型输入固定 640，但 CSI 给的是 1920x1080 NV12。原实现直接
    cv2.cvtColor(frame, COLOR_YUV2BGR_NV12)
等于把 3.1MB 的 NV12 撑成 6.2MB 的 BGR，再在下游 letterbox 里缩回 640。
这一步实测占单帧预处理的绝大部分（约 40ms / 端到端 153ms），
也是 vision_service 常驻 ~280% CPU、板子跑到 94℃ 的主要原因之一。

改法
----
NV12 的内存布局是 (h*3/2, w)：上 2/3 是 Y 平面，下 1/3 是 UV 交错平面。
在 NV12 域内分别把 Y 和 UV resize 到目标尺寸再拼回去，最后只对 640x480
做一次 cvtColor，写放大降约 8 倍。

回滚
----
cp /root/k1-ai/vision_service.py.bak-nv12 /root/k1-ai/vision_service.py
systemctl restart k1-vision-csi
"""
import shutil
import sys

P = "/root/k1-ai/vision_service.py"

OLD = """        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_NV12)"""

NEW = """        if frame.ndim == 2:
            # NV12 布局 (h*3/2, w)：上 2/3 是 Y，下 1/3 是 UV 交错。
            # 模型输入固定 640，没必要把 1920x1080 全量撑成 BGR（6.2MB 写放大，
            # 实测占预处理大头，也是 vision 常驻 ~280% CPU 的主因）。
            # 先在 NV12 域按平面降采样到 640x480，再转 BGR，成本降约 8 倍。
            fh, fw = frame.shape
            if fw > 640:
                yh = fh * 2 // 3
                y_s = cv2.resize(frame[:yh], (640, 480), interpolation=cv2.INTER_AREA)
                # UV 平面是交错存的（U0V0U1V1…），行宽与 Y 相同（=640），
                # 只有高度减半；写成 (320,240) 会让 vstack 维度不匹配。
                uv_s = cv2.resize(frame[yh:], (640, 240), interpolation=cv2.INTER_AREA)
                frame = np.vstack([y_s, uv_s])
            frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_NV12)"""

src = open(P).read()

if NEW in src:
    print("already patched, nothing to do")
    sys.exit(0)

if OLD not in src:
    print("ANCHOR NOT FOUND - 源码与预期不符，已中止（未做任何修改）")
    sys.exit(1)

if "import numpy as np" not in src:
    print("WARN: 未见 import numpy as np，需人工确认")

shutil.copy(P, P + ".bak-nv12")
open(P, "w").write(src.replace(OLD, NEW, 1))
print("patched OK; backup at %s.bak-nv12" % P)
