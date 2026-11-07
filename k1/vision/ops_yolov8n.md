### 模型元信息

- 文件：`yolov8n.onnx`
- IR version：7，opset：ai.onnx=12
- producer：pytorch 2.15.0
- 节点数：234，initializer 数：141
- 输入：[{'name': 'images', 'shape': [1, 3, 640, 640]}]
- 输出：[{'name': 'output0', 'shape': [1, 84, 8400]}]

### 算子统计表

| 算子 | 出现次数 | 命中 spacemit-ort 限制清单 | 说明 |
|---|---|---|---|
| `Conv` | 64 | 否 |  |
| `Sigmoid` | 58 | 否 |  |
| `Mul` | 58 | 否 |  |
| `Concat` | 17 | 间接 | 非硬限制，但大量出现会削弱子图切分粒度 |
| `Split` | 8 | 间接 | 非硬限制，但大量出现会削弱子图切分粒度 |
| `Add` | 8 | 否 |  |
| `Reshape` | 8 | 否 |  |
| `MaxPool` | 3 | 否 |  |
| `Resize` | 2 | **是** | roi/scales/sizes 必须为常量，且 mode 受限 |
| `Transpose` | 2 | 否 |  |
| `Slice` | 2 | **是** | starts/ends/axes/steps 必须为常量 |
| `Sub` | 2 | 否 |  |
| `Softmax` | 1 | 否 |  |
| `Div` | 1 | 否 |  |

### 受限算子逐节点判定

| 算子 | 节点名 | 常量约束是否满足 | 判定 |
|---|---|---|---|
| `Resize` | `/model.10/Resize` | in1=initializer/Constant; in2=initializer/Constant | ✅ 通过 |
| `Resize` | `/model.13/Resize` | in1=initializer/Constant; in2=initializer/Constant | ✅ 通过 |
| `Slice` | `/model.22/Slice` | in1=initializer/Constant; in2=initializer/Constant; in3=initializer/Constant | ✅ 通过 |
| `Slice` | `/model.22/Slice_1` | in1=initializer/Constant; in2=initializer/Constant; in3=initializer/Constant | ✅ 通过 |

**受限算子中判定会 fallback 到 CPU 的节点数：0**