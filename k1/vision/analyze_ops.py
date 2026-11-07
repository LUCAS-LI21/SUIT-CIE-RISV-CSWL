#!/usr/bin/env python3
"""
analyze_ops.py —— 遍历 ONNX 计算图，统计算子分布，并对照 spacemit-ort
（SpaceMITExecutionProvider，K1）的"静态子图 + 常量约束"限制清单，逐节点判定风险。

限制清单（据 spacemit-ort 的 EP 行为）：
  - Slice  : 除首输入(data)外，starts/ends/axes/steps 必须全部是常量 initializer
  - Pad    : pads 必须是常量 initializer
  - Tile   : repeats 必须是常量 initializer
  - Resize : 仅支持受限模式（nearest/linear，非动态尺寸）
  - 自回归解码类动态 shape / 增量 KV cache 不支持

判定方法：对每个受限算子，沿其非首输入向上追溯，判断该输入是否可归约为
常量（initializer / Constant 节点 / 由常量经过纯常量算子折叠出的值）。
只要追溯路径上出现 Shape / Gather(shape) / NonZero 等依赖运行时张量的算子，
就判定为"动态"，标记为会 fallback 到 CPU。

用法：
  python analyze_ops.py yolov8n.onnx [--json result.json] [--md table.md]
"""
import argparse
import collections
import json
import sys

import onnx

# 限制清单：算子类型 -> (必须为常量的输入下标列表, 人话说明)
CONSTRAINED = {
    "Slice": ([1, 2, 3, 4], "starts/ends/axes/steps 必须为常量"),
    "Pad": ([1], "pads 必须为常量"),
    "Tile": ([1], "repeats 必须为常量"),
    "Resize": ([1, 2, 3], "roi/scales/sizes 必须为常量，且 mode 受限"),
}

# 只要出现就说明图里存在运行时 shape 推导，EP 无法预先静态化子图
DYNAMIC_SOURCES = {"Shape", "NonZero", "TopK"}  # TopK 会产出动态 shape 依赖


def build_constant_index(graph):
    """收集所有"常量值"的名字：initializer 输出名 + Constant 节点输出名。"""
    const_names = set()
    for init in graph.initializer:
        const_names.add(init.name)
    for node in graph.node:
        if node.op_type == "Constant":
            for out in node.output:
                const_names.add(out)
    return const_names


def build_producer_map(graph):
    """张量名 -> 产生它的节点。"""
    prod = {}
    for node in graph.node:
        for out in node.output:
            if out:
                prod[out] = node
    return prod


# 可以把常量输入原样传递下去的算子（用于常量折叠追溯）
TRANSPARENT = {"Identity", "Cast", "Squeeze", "Unsqueeze", "Reshape", "Flatten", "Concat"}


def traces_to_constant(name, const_names, producer, seen=None, depth=0):
    """判断某个张量名是否可追溯为常量。

    返回 (is_const, reason)。reason 记录追溯终止原因，便于报告里写清楚。
    """
    if seen is None:
        seen = set()
    if name in const_names:
        return True, "initializer/Constant"
    if name in seen or depth > 64:
        return False, "cycle/depth"
    seen.add(name)

    node = producer.get(name)
    if node is None:
        # 既不是 initializer 也不是任何节点输出 —— 可能是图的输入（运行时数据）
        return False, "graph input / unknown"

    # 纯常量算子：只要它自身所有输入都是常量，输出就是常量（常量折叠）
    if node.op_type in TRANSPARENT:
        for inp in node.input:
            if not inp:
                continue
            ok, _ = traces_to_constant(inp, const_names, producer, seen, depth + 1)
            if not ok:
                return False, f"经 {node.op_type} 依赖动态输入"
        return True, f"常量折叠({node.op_type})"

    # 其他算子：若它由 Shape/Gather 等驱动，则输出是动态的
    if node.op_type in DYNAMIC_SOURCES:
        return False, f"来自 {node.op_type}（运行时 shape）"
    return False, f"来自 {node.op_type} 的计算结果"


def analyze(path):
    model = onnx.load(path)
    graph = model.graph
    const_names = build_constant_index(graph)
    producer = build_producer_map(graph)

    ops = collections.Counter(n.op_type for n in graph.node)

    findings = []          # 每个受限算子的明细
    risky_nodes = []       # 判定为会 fallback 的节点
    for node in graph.node:
        if node.op_type not in CONSTRAINED:
            continue
        idxs, note = CONSTRAINED[node.op_type]
        entry = {
            "op": node.op_type,
            "name": node.name or "<unnamed>",
            "output": node.output[0] if node.output else "",
            "constraint": note,
            "inputs": [],
            "status": "OK",
        }
        for i in idxs:
            if i >= len(node.input) or node.input[i] == "":
                # 可选输入未提供：不构成风险（例如 Slice 的 steps 省略）
                entry["inputs"].append({"idx": i, "tensor": "<absent>", "const": True, "reason": "未提供(可选)"})
                continue
            tname = node.input[i]
            is_const, reason = traces_to_constant(tname, const_names, producer)
            entry["inputs"].append({"idx": i, "tensor": tname, "const": is_const, "reason": reason})
            if not is_const:
                entry["status"] = "RISK"
        # Resize 额外检查 mode
        if node.op_type == "Resize":
            mode = "nearest"
            for a in node.attribute:
                if a.name == "mode":
                    mode = a.s.decode()
            entry["resize_mode"] = mode
        findings.append(entry)
        if entry["status"] == "RISK":
            risky_nodes.append(entry)

    # Resize 模式统计
    resize_modes = collections.Counter(f.get("resize_mode") for f in findings if f["op"] == "Resize")

    # 动态 shape 算子（决定 EP 能否静态化子图）
    dyn_nodes = [n for n in graph.node if n.op_type in DYNAMIC_SOURCES]

    # 全部节点输入里出现的 graph input（用于判断是否存在数据依赖的动态分支）
    graph_inputs = {i.name for i in graph.input}
    uses_graph_input = collections.Counter()
    for node in graph.node:
        for inp in node.input:
            if inp in graph_inputs:
                uses_graph_input[node.op_type] += 1

    summary = {
        "file": path,
        "ir_version": model.ir_version,
        "opset": [{"domain": o.domain or "ai.onnx", "version": o.version} for o in model.opset_import],
        "producer": f"{model.producer_name} {model.producer_version}",
        "num_nodes": len(graph.node),
        "num_initializers": len(graph.initializer),
        "inputs": [
            {"name": i.name, "shape": [d.dim_value or d.dim_param for d in i.type.tensor_type.shape.dim]}
            for i in graph.input
        ],
        "outputs": [
            {"name": o.name, "shape": [d.dim_value or d.dim_param for d in o.type.tensor_type.shape.dim]}
            for o in graph.output
        ],
        "op_counts": dict(ops.most_common()),
        "constrained_ops_present": {k: ops.get(k, 0) for k in CONSTRAINED},
        "resize_modes": dict(resize_modes),
        "dynamic_shape_nodes": collections.Counter(n.op_type for n in dyn_nodes),
        "risky_node_count": len(risky_nodes),
        "risky_detail": risky_nodes,
        "findings": findings,
    }
    return summary


def to_markdown(s):
    lines = []
    lines.append(f"### 模型元信息\n")
    lines.append(f"- 文件：`{s['file']}`")
    lines.append(f"- IR version：{s['ir_version']}，opset：{', '.join('%s=%d' % (o['domain'], o['version']) for o in s['opset'])}")
    lines.append(f"- producer：{s['producer']}")
    lines.append(f"- 节点数：{s['num_nodes']}，initializer 数：{s['num_initializers']}")
    lines.append(f"- 输入：{s['inputs']}")
    lines.append(f"- 输出：{s['outputs']}")
    lines.append("")
    lines.append("### 算子统计表\n")
    lines.append("| 算子 | 出现次数 | 命中 spacemit-ort 限制清单 | 说明 |")
    lines.append("|---|---|---|---|")
    for op, cnt in s["op_counts"].items():
        hit = "**是**" if op in CONSTRAINED else "否"
        note = CONSTRAINED[op][1] if op in CONSTRAINED else ""
        if op in ("Concat", "Split"):
            note = "非硬限制，但大量出现会削弱子图切分粒度"
            hit = "间接"
        lines.append(f"| `{op}` | {cnt} | {hit} | {note} |")
    lines.append("")
    lines.append("### 受限算子逐节点判定\n")
    lines.append("| 算子 | 节点名 | 常量约束是否满足 | 判定 |")
    lines.append("|---|---|---|---|")
    for f in s["findings"]:
        detail = "; ".join(f"in{r['idx']}={r['reason']}" for r in f["inputs"] if r["tensor"] != "<absent>")
        if not detail:
            detail = "无可选输入"
        mark = "✅ 通过" if f["status"] == "OK" else "❌ 不满足 → fallback CPU"
        lines.append(f"| `{f['op']}` | `{f['name']}` | {detail} | {mark} |")
    lines.append("")
    lines.append(f"**受限算子中判定会 fallback 到 CPU 的节点数：{s['risky_node_count']}**")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--json", default=None)
    ap.add_argument("--md", default=None)
    args = ap.parse_args()

    s = analyze(args.model)

    print("=" * 70)
    print("OP SET / SHAPES")
    print("=" * 70)
    print("opset:", [(o["domain"], o["version"]) for o in s["opset"]])
    print("inputs:", s["inputs"])
    print("outputs:", s["outputs"])
    print("\n" + "=" * 70)
    print("OP COUNTS  (total nodes: %d)" % s["num_nodes"])
    print("=" * 70)
    for op, cnt in s["op_counts"].items():
        flag = "  <== 受限算子" if op in CONSTRAINED else ""
        print(f"{op:22s} {cnt:4d}{flag}")

    print("\n" + "=" * 70)
    print("CONSTRAINT CHECK (spacemit-ort SpaceMITExecutionProvider)")
    print("=" * 70)
    if not s["findings"]:
        print("图中没有 Slice/Pad/Tile/Resize 节点。")
    for f in s["findings"]:
        print(f"[{f['status']:4s}] {f['op']:7s} {f['name']}")
        for r in f["inputs"]:
            if r["tensor"] == "<absent>":
                continue
            print(f"         in{r['idx']}: {'CONST' if r['const'] else 'DYNAMIC':7s} {r['tensor'][:60]:60s}  <- {r['reason']}")
        if f.get("resize_mode"):
            print(f"         mode: {f['resize_mode']}")

    print("\nresize modes:", s["resize_modes"])
    print("dynamic-shape source nodes:", s["dynamic_shape_nodes"])
    print("RISKY NODES (will fall back to CPU):", s["risky_node_count"])

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
        print("json ->", args.json)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(to_markdown(s))
        print("markdown ->", args.md)


if __name__ == "__main__":
    main()
