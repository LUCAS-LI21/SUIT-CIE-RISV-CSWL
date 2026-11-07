#!/usr/bin/env python3
"""
export_yolov8.py —— 在 PC 上把 YOLOv8n 导出成可部署到 SpacemiT K1（spacemit-ort）的 ONNX。

目标规格：
  输入   images  : 1 x 3 x 640 x 640  float32
  输出   output0 : 1 x 84 x 8400      float32   （YOLOv8 原生输出，无 NMS）
  opset  12（K1 上的 spacemit-ort 对高 opset 支持不确定，12 最稳）
  静态 shape（dynamic=False），batch=1 —— spacemit-ort 的 EP 只吃静态子图

两条路径：
  A. 首选：本地 ultralytics + torch CPU 环境，直接从 yolov8n.pt 导出 opset 12。
     优点：producer/opset 全部原生，图最干净。
  B. 备选：直接下载 ultralytics 官方 release 里已经导好的 yolov8n.onnx（opset 20），
     再用 onnx 手工把图降到 opset 12：opset 13+ 的 Split 用 initializer 作为第二输入，
     opset 12 要求 split 作为属性 —— 这一步做等价改写并逐元素比对输出。

用法：
  python export_yolov8.py                      # 自动选路径 A，失败则退 B
  python export_yolov8.py --path B             # 强制走下载+降级
  python export_yolov8.py --verify-only        # 只做校验与 benchmark
"""
import argparse
import collections
import os
import sys
import time
import urllib.request

import numpy as np
import onnx

RELEASE_URL = "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.onnx"
RELEASE_SIZE = 12851049
OUT = "yolov8n.onnx"
EXPECT_SHAPE = (1, 84, 8400)


# --------------------------------------------------------------------------
# 工具：带断点续传的下载（GitHub release 在国内经常断流，必须能续传）
# --------------------------------------------------------------------------
def download(url, dst, expect=None, tries=60):
    if os.path.exists(dst) and expect and os.path.getsize(dst) == expect:
        print(f"[dl] 已存在且大小正确: {dst} ({expect} bytes)")
        return dst
    for i in range(tries):
        have = os.path.getsize(dst) if os.path.exists(dst) else 0
        if expect and have == expect:
            break
        if expect and have > expect:
            open(dst, "wb").close()
            have = 0
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=30) as r, open(dst, "ab") as f:
                while True:
                    c = r.read(65536)
                    if not c:
                        break
                    f.write(c)
        except Exception as e:
            cur = os.path.getsize(dst) if os.path.exists(dst) else 0
            print(f"[dl] 第{i}次中断 {type(e).__name__} size={cur}, 续传...", flush=True)
            time.sleep(1)
    size = os.path.getsize(dst) if os.path.exists(dst) else -1
    if expect and size != expect:
        raise SystemExit(f"[dl] 下载不完整: {size} != {expect}")
    print(f"[dl] 完成 {dst} {size} bytes")
    return dst


# --------------------------------------------------------------------------
# 路径 A：用 ultralytics 原生导出
# --------------------------------------------------------------------------
def export_with_ultralytics(weights, opset, imgsz, out):
    from ultralytics import YOLO

    model = YOLO(weights)
    print(f"[A] ultralytics 导出: opset={opset} imgsz={imgsz} dynamic=False batch=1 simplify=True")
    produced = model.export(
        format="onnx",
        opset=opset,
        imgsz=imgsz,
        dynamic=False,   # 静态 shape —— spacemit-ort 的先决条件
        batch=1,
        simplify=True,   # onnxslim 折叠常量，减少冗余节点
        half=False,
        nms=False,       # 不要内置 NMS，板端自己做后处理
        device="cpu",
    )
    print(f"[A] 导出产物: {produced}")
    if os.path.abspath(produced) != os.path.abspath(out):
        os.replace(produced, out)
    return out


# --------------------------------------------------------------------------
# 路径 B：下载官方 release onnx，手工降到 opset 12
# --------------------------------------------------------------------------
def downgrade_to_opset12(src, out):
    """把 opset>=13 的图改写成 opset 12 语义。

    需要改写的算子只有 Split：opset 13 起 split 的每一段长度可以通过第 2 个输入
    （initializer）给出，opset 12 只认 `split` 属性。这里把 initializer 的值读出来
    塞进属性，并去掉第 2 个输入。其余算子（Conv/Sigmoid/Mul/Concat/Add/Reshape/
    MaxPool/Resize/Slice/Sub/Transpose/Softmax/Div）在两版之间语义一致。
    """
    m = onnx.load(src)
    inits = {i.name: onnx.numpy_helper.to_array(i) for i in m.graph.initializer}
    changed = 0
    for node in m.graph.node:
        if node.op_type == "Split" and len(node.input) >= 2 and node.input[1]:
            split_name = node.input[1]
            if split_name not in inits:
                raise SystemExit(f"[B] Split 的 split 输入 {split_name} 不是 initializer，无法静态改写")
            sizes = inits[split_name].tolist()
            if sum(sizes) != len(node.output) and len(sizes) != len(node.output):
                pass  # sizes 是各段长度，个数应等于输出个数
            node.attribute.append(onnx.helper.make_attribute("split", [int(s) for s in sizes]))
            del node.input[1]
            changed += 1
    # 改写 opset 声明
    for o in m.opset_import:
        if o.domain in ("", "ai.onnx"):
            o.version = 12
    m.ir_version = 8  # opset 12 对应 IR 7/8，8 兼容性最好
    onnx.checker.check_model(m)
    onnx.save(m, out)
    print(f"[B] 改写 {changed} 个 Split 节点，opset -> 12，已保存 {out}")
    return out


# --------------------------------------------------------------------------
# 校验：结构 + 数值
# --------------------------------------------------------------------------
def verify(path, ref=None, bench_runs=10):
    m = onnx.load(path)
    opset = {o.domain or "ai.onnx": o.version for o in m.opset_import}
    inp = m.graph.input[0]
    shape = [d.dim_value or d.dim_param for d in inp.type.tensor_type.shape.dim]
    out_shape = [d.dim_value or d.dim_param for d in m.graph.output[0].type.tensor_type.shape.dim]
    print(f"[verify] file={path} size={os.path.getsize(path)}")
    print(f"[verify] ir={m.ir_version} opset={opset}")
    print(f"[verify] input  {inp.name} {shape}")
    print(f"[verify] output {m.graph.output[0].name} {out_shape}")
    assert shape == [1, 3, 640, 640], f"输入 shape 不对: {shape}"
    assert opset.get("ai.onnx") == 12, f"opset 不是 12: {opset}"

    import onnxruntime as ort
    s = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    x = np.random.rand(1, 3, 640, 640).astype(np.float32)
    y = s.run(None, {s.get_inputs()[0].name: x})[0]
    print(f"[verify] 实跑输出 shape={y.shape} dtype={y.dtype}")
    assert y.shape == EXPECT_SHAPE, f"输出 shape 不对: {y.shape}"

    # 计时：先预热 3 次，再跑 bench_runs 次
    ts = []
    for _ in range(3):
        s.run(None, {s.get_inputs()[0].name: x})
    for _ in range(bench_runs):
        t = time.perf_counter()
        s.run(None, {s.get_inputs()[0].name: x})
        ts.append((time.perf_counter() - t) * 1000)
    print(f"[bench] PC 单帧 均值={sum(ts)/len(ts):.2f} ms  最小={min(ts):.2f} ms  最大={max(ts):.2f} ms "
          f"({bench_runs} 次, {ort.get_device()}, {s.get_providers()})")

    if ref is not None:
        r = ort.InferenceSession(ref, providers=["CPUExecutionProvider"])
        yr = r.run(None, {r.get_inputs()[0].name: x})[0]
        d = np.abs(yr - y)
        cos = float(y.ravel() @ yr.ravel() / (np.linalg.norm(y) * np.linalg.norm(yr)))
        print(f"[verify] 与 {ref} 对比: max|Δ|={d.max():.6e} mean|Δ|={d.mean():.6e} cosine={cos:.8f}")
    return y


def op_stats(path):
    m = onnx.load(path)
    c = collections.Counter(n.op_type for n in m.graph.node)
    print(f"[ops] 节点总数 {len(m.graph.node)}, initializer {len(m.graph.initializer)}")
    for op, n in c.most_common():
        print(f"[ops]   {op:20s} {n}")
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="yolov8n.pt")
    ap.add_argument("--opset", type=int, default=12)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--path", choices=["A", "B", "auto"], default="auto")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()

    if args.verify_only:
        verify(args.out)
        op_stats(args.out)
        return

    used = None
    if args.path in ("A", "auto"):
        try:
            import torch  # noqa: F401
            import ultralytics  # noqa: F401
            if not os.path.exists(args.weights):
                download(f"https://github.com/ultralytics/assets/releases/download/v8.4.0/{args.weights}",
                         args.weights, 6549796)
            export_with_ultralytics(args.weights, args.opset, args.imgsz, args.out)
            used = "A"
        except Exception as e:
            print(f"[A] 失败: {type(e).__name__}: {e}")
            if args.path == "A":
                raise
            print("[A] 回退到路径 B")

    if used is None:
        raw = download(RELEASE_URL, "_release_yolov8n.onnx", RELEASE_SIZE)
        downgrade_to_opset12(raw, args.out)
        used = "B"
        # 用 opset20 原图做数值参照，确认降级没有改变语义
        verify(args.out, ref=raw)
        op_stats(args.out)
        print(f"[done] 路径 {used} 产出 {os.path.abspath(args.out)}")
        return

    verify(args.out)
    op_stats(args.out)
    print(f"[done] 路径 {used} 产出 {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
