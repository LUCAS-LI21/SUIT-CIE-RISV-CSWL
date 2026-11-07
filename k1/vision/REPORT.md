# YOLOv8n → ONNX（SpacemiT K1 / spacemit-ort）导出与算子兼容性评估

生成时间：2026-10-31
工作目录：`E:/CIE-RISC-V/k1-vision/`（未触碰 `pet-wellness/` 下任何文件）

---

## 0. 结论速览

| 问题 | 结论（全部为实测） |
|---|---|
| 能否在 PC 上导出合规 ONNX | 能。`yolov8n.onnx`，opset 12，IR 7，输入 `1x3x640x640`，输出 `1x84x8400` |
| Slice/Pad/Tile/Resize 是否踩坑 | 一个都没踩。全图 234 节点，受限算子 4 个（2×Resize + 2×Slice），**4/4 输入全是 initializer**，判定 fallback 节点数 = **0** |
| 量化能不能做 | 能，但 PC 侧的两种 int8 都不是理想形态（见第 5 节） |
| PC 单帧耗时 | FP32 **22.52 ms**（44.4 fps），int8 静态 **26.86 ms**，int8 动态 **55.64 ms** |
| 板端该用哪个 | **官方 model-zoo 的 `yolov8n_no_dfl.q.onnx`**（opset 17 + 标准 QDQ int8，官方 EP 验证过），自导出 FP32 作为"换自己权重"的通道 |

环境实测：
```
Python 3.13.12   torch 2.15.0+cpu   ultralytics 8.4.194
onnx 1.23.2      onnxruntime 1.26.0  onnxslim 0.1.98   torchvision 0.29.2+cpu
CPU: 13th Gen Intel Core i5-13500HX
```

> 注意：本机 `python`（PATH 上）是 WorkBuddy 自带的 3.13.12，`pip` 却指向 `E:\Python`，两者不是同一个环境。
> 所有命令实际使用的是 `/e/Python/python.exe`。直接敲 `python` 会报 `ModuleNotFoundError: No module named 'onnx'`。

---

## 1. 实际产出了什么

| 文件（绝对路径） | 大小（字节） | 说明 |
|---|---|---|
| `E:/CIE-RISC-V/k1-vision/yolov8n.onnx` | **12,851,145** | **主交付**。FP32，opset 12，IR 7，`images [1,3,640,640]` → `output0 [1,84,8400]` |
| `E:/CIE-RISC-V/k1-vision/yolov8n-int8.onnx` | **3,504,185** | int8 动态量化（`quantize_dynamic`, QUInt8）。64×`ConvInteger` + 59×`DynamicQuantizeLinear`，**纯 ai.onnx 域 opset 12** |
| `E:/CIE-RISC-V/k1-vision/yolov8n-int8-static.onnx` | **3,428,411** | int8 静态量化（QOperator, QUInt8）。64×`QLinearConv`，但**混入 com.microsoft 域算子**（见 5.3） |
| `E:/CIE-RISC-V/k1-vision/export_yolov8.py` | 9,922 | 可复现导出脚本（路径 A 原生导出 / 路径 B 下载+opset 降级），带逐步注释与自动校验 |
| `E:/CIE-RISC-V/k1-vision/quantize_int8.py` | 8,844 | 量化脚本（dynamic / static，QOperator/QDQ，可排除输出节点） |
| `E:/CIE-RISC-V/k1-vision/analyze_ops.py` | 10,770 | **算子统计 + spacemit-ort 约束逐节点判定**（本次最有价值的工具） |
| `E:/CIE-RISC-V/k1-vision/compare_int8.py` | 4,945 | 三模型在真实图片上的检测一致性对比（TP/FP/FN + IoU） |
| `E:/CIE-RISC-V/k1-vision/ops_yolov8n.md` / `.json` | 1,617 / 4,249 | 算子统计与约束判定的机器可读/可贴版结果 |
| `E:/CIE-RISC-V/k1-vision/yolov8n.pt` | 6,549,796 | 官方权重（复现导出用） |
| `E:/CIE-RISC-V/k1-vision/bus.jpg` | 137,419 | 评估/校准用真实图片（ultralytics 官方示例图） |
| `E:/CIE-RISC-V/k1-vision/official_model_zoo/yolov8n.q.onnx` | 3,535,915 | 官方 K1 模型（单输出 [1,84,8400]），仅作对比与建议依据 |
| `E:/CIE-RISC-V/k1-vision/official_model_zoo/yolov8n_no_dfl.q.onnx` | 3,367,042 | 官方 K1 模型（多分支，DFL 在图外），**推荐上板用这个** |

---

## 2. 走了哪条路径，为什么

**走了"路径 A：本地 ultralytics 原生导出 opset 12"。**过程与关键判断：

1. **探测环境**：`python`/`pip` 指向两个不同环境（见第 0 节）。真正可用的是 `E:\Python\python.exe`，里面已有 numpy 2.4.6 / onnxruntime 1.26.0 / cv2 / PIL，缺 onnx、torch、ultralytics。
2. **装 onnx**（小包，秒级）：成功，1.23.2。
3. **并行探路**：先查 ultralytics GitHub release 有没有现成 ONNX —— 有，`yolov8n.onnx` 12,851,049 字节。**GitHub release 直连极不稳定**（多次 `curl: (56) schannel: server closed abruptly`、9.7MB/12.8MB 断流），四个 ghproxy 镜像全部 502 / 超时。最终用**带 Range 断点续传的循环**才拉完。
4. **发现该 release 模型是 opset 20**，不满足"opset 11 或 12"的要求。尝试 `onnx.version_converter` 降级 → **失败**：
   ```
   FAIL at 13 RuntimeError ... No Adapter From Version $18 for Split
   ```
   官方转换器根本没有 opset 18→19→…→12 的 Split 适配器，此路不通。
5. **于是装 torch（CPU 版）重新导出**。`pip install torch --index-url https://download.pytorch.org/whl/cpu` 下了 12 分 23 秒（126.6 MB，188.0 kB/s），**首次安装以 `WinError 5 拒绝访问` 结束**（写 `torch-2.15.0+cpu.dist-info\RECORD` 被拒），但包体已落盘，`import torch` 直接可用，绕过。
6. **torchvision 必须从 cpu 源单独装**（`--no-deps`），否则 pypi 会把 Windows 的 CUDA 版 torch（约 2.5GB）作为依赖重新拖一遍。
7. `ultralytics 8.4.194` + `onnxslim 0.1.98` 装好后，`export_yolov8.py --path A` **2.2 秒导出成功**，opset 12、静态 shape、无 NMS。

**没有走的路径**：手工构造最小检测图（不现实，未尝试）；也没必要走"只能交官方 model-zoo"的兜底，因为本地导出成功了。

---

## 3. 算子统计表（对照 spacemit-ort 限制清单）

数据源：`yolov8n.onnx`，234 个节点，141 个 initializer，opset 12，IR 7。

| 算子 | 出现次数 | 命中 spacemit-ort 限制清单 | 说明 |
|---|---|---|---|
| `Conv` | 64 | 否 | 主干 + 检测头，EP 的主力算子 |
| `Sigmoid` | 58 | 否 | 全部来自 SiLU（`x * sigmoid(x)`） |
| `Mul` | 58 | 否 | 同上，SiLU 的另一半 |
| `Concat` | 17 | 间接 | 非硬限制；过多会导致 EP 子图被切碎 |
| `Split` | 8 | 间接 | 非硬限制；C2f 分支处 |
| `Add` | 8 | 否 | 残差 |
| `Reshape` | 8 | 否 | 检测头 DFL |
| `MaxPool` | 3 | 否 | SPPF |
| `Resize` | **2** | **是** | 要求 roi/scales/sizes 为常量且 mode 受限 |
| `Transpose` | 2 | 否 | |
| `Slice` | **2** | **是** | 要求 starts/ends/axes/steps 为常量 |
| `Sub` | 2 | 否 | DFL 的 `-0.5` 偏移 |
| `Softmax` | 1 | 否 | DFL 内 |
| `Div` | 1 | 否 | |

**`Pad` 0 个，`Tile` 0 个，`Shape`/`NonZero`/`TopK` 全部 0 个**（即图里没有任何运行时 shape 推导）。

### 受限算子逐节点判定（全图 4 个需要判的节点）

| 算子 | 节点名 | 非常量输入检查 | 判定 |
|---|---|---|---|
| `Resize` | `/model.10/Resize` | in1(roi)=initializer; in2(scales)=initializer | ✅ 通过，mode=`nearest` |
| `Resize` | `/model.13/Resize` | in1(roi)=initializer; in2(scales)=initializer | ✅ 通过，mode=`nearest` |
| `Slice` | `/model.22/Slice` | in1=initializer; in2=initializer; in3=initializer | ✅ 通过 |
| `Slice` | `/model.22/Slice_1` | in1=initializer; in2=initializer; in3=initializer | ✅ 通过 |

常量值实际读出来核对过（不是靠名字猜）：

```
/model.22/Mul_output_0      | initializer: True | produced_by: None   value: [2]
/model.22/Mul_1_output_0    | initializer: True | produced_by: None   value: [4]
/model.22/Constant_7_output_0 | initializer: True | produced_by: None  value: [0]
/model.22/Constant_6_output_0 | initializer: True | produced_by: None  value: [1]
/model.10/Constant_output_0 | initializer: True | produced_by: None   value: [1. 1. 2. 2.]
```

即：两个 Slice 分别是 `[0:2, axis=1]` 和 `[2:4, axis=1]`（切 DFL 出来的 4 个距离分量，前 2 个和后 2 个），两个 Resize 的 scales 固定为 `[1,1,2,2]`（SPPF 后 2× 上采样）。**全部是编译期常量，EP 可以完全静态化。**

**判定会 fallback 到 CPU 的受限算子节点数：0。**

> 结论：YOLOv8n 是 spacemit-ort 的"好客户"。它没有自回归解码、没有动态 shape、没有 KV cache，Slice/Resize 的常量约束天然满足。之前担心的"Slice 动态参数"风险在 YOLOv8 上不成立（`end2end=False` 导出的头里没有基于 `Shape` 的切分）。

---

## 4. PC 实测

### 4.1 单帧耗时（ORT 默认优化级别，预热 5 次，测 20 次）

```
ORT 1.26.0 threads ['AzureExecutionProvider', 'CPUExecutionProvider']
yolov8n.onnx                   12.85 MB  mean= 22.52 ms  min= 20.16  p50= 22.40  fps= 44.4
yolov8n-int8.onnx               3.50 MB  mean= 55.64 ms  min= 45.39  p50= 55.16  fps= 18.0
yolov8n-int8-static.onnx        3.43 MB  mean= 26.86 ms  min= 22.87  p50= 26.01  fps= 37.2
```

- FP32：**22.52 ms / 44.4 fps**（vs 板端官方 68 ms / 14.7 fps，快约 3 倍，符合预期）
- int8 动态：**比 FP32 慢 2.5 倍**。原因是 `ConvInteger` 在 x86 上没有走 MLAS 的量化卷积快路径，还要额外插 59 个 `DynamicQuantizeLinear` + 64 个 `Cast`（节点数 234 → 611）。
- int8 静态（QLinearConv）：26.86 ms，仍然比 FP32 慢。x86 上 FP32 conv 已经用上 AVX-512/VNNI，int8 的收益要等 K1 的专用 NPU/向量单元才体现。

### 4.2 精度（真实图片 bus.jpg，conf>0.25 + NMS IoU 0.45）

```
=== yolov8n.onnx          全图最高类分数=0.8902  检出数=5
    person 0.8902 (477,226,560,521)   person 0.8833 (211,241,284,507)
    person 0.8784 (110,235,225,537)   bus    0.8434 (99,137,555,460)
    person 0.4363 (80,326,114,515)
=== yolov8n-int8.onnx     全图最高类分数=0.8973  检出数=5
=== yolov8n-int8-static.onnx 全图最高类分数=0.8901  检出数=5
=== 相对 FP32 的检测一致性 (IoU>0.5 且类别相同算命中) ===
yolov8n-int8.onnx: TP=5 FP=0 FN=0   与 fp32 框的最大 IoU: 均值=0.966 最小=0.901
yolov8n-int8-static.onnx: TP=5 FP=0 FN=0  均值=0.877 最小=0.788
```

三种模型在真实图上都是 5/5 命中、0 误检、0 漏检。**注意：这只验证了 1 张图，mAP 没测**（没下 COCO 校准/验证集，按任务要求没有卡在这里）。

### 4.3 一个必须记住的坑：int8 静态量化会把置信度压成 0

第一次跑静态量化（QOperator，未做任何排除），结果是 **检出 0 个框**，输出张量全 0。查量化参数：

```
producer of output0: DequantizeLinear output0_DequantizeLinear
    output0_scale 2.4992456
    output0_zero_point 0
```

`scale = 2.4992 ≈ 640/255`。原因：YOLOv8 的 `output0 [1,84,8400]` 把 **0~640 的框坐标**和 **0~1 的类别分数**塞在同一张量里，per-tensor uint8 量化只能取覆盖 0~640 的 scale，于是 0.9 的分数被量化成 `round(0.9/2.5)=0`，反量化回来还是 0。

修法：排除产生图输出的那个节点（`--exclude-output`，本模型是 `/model/22/Concat_3`）。修完之后立刻恢复成 46 个候选框、5 个最终检出，与 FP32 一致。**板端如果自己做量化，这个坑 100% 会踩到。**

---

## 5. 三个明确建议

### ① 板端该用自己导出的还是官方 model-zoo 的？

**上板第一版用官方的 `yolov8n_no_dfl.q.onnx`。**理由全部来自实测：

| 对比项 | 自导出 FP32 | 官方 model-zoo |
|---|---|---|
| opset | 12 | 17（IR 10/9） |
| 量化 | 无 | 标准 QDQ int8（283×DequantizeLinear + 220×QuantizeLinear，PPQ 风格命名 `PPQ_Variable_*`），**全是 ai.onnx 域，无 com.microsoft 依赖** |
| 输出 | 单输出 `[1,84,8400]`（DFL 在图内） | `no_dfl` 版 9 个输出（3 尺度 × box/score/score_sum），DFL 留给后处理，少一层 conv |
| 体积 | 12.85 MB | 3.37 MB / 3.54 MB |
| 精度（bus.jpg） | 5 框基准 | **TP=5 FP=0 FN=0，IoU 均值 0.970**（比我们自己动态量化的 0.966 还好） |
| 常量约束 | 0 风险 | 0 风险（同样 4/4 通过） |
| 是否被官方 EP 验证过 | 否 | 是（仓库 example 默认 `providers: [SpaceMITExecutionProvider]`） |

自导出模型的定位应该是：**当你换成自己训练的权重（比如宠物行为模型）时，用 `export_yolov8.py` 走同一条链路导出 FP32，再在板端用 spacemit 的量化工具做 QDQ**。官方模型解不了"你自己的权重"这个问题。

### ② 量化用动态还是静态？

**都不要用 onnxruntime 在 PC 上做的这两个，改用 QDQ per-channel（opset ≥13），并且理想情况放到板端做。**

- **动态（`quantize_dynamic`）**：能跑、精度好（IoU 0.966），但产出的是 `ConvInteger` + `DynamicQuantizeLinear`。spacemit 的 EP 明确支持的是 `MatMulInteger`/`DynamicQuantizeMatMul`（矩阵乘），**而 YOLOv8 里 `MatMul` 数量是 0、`Conv` 是 64**。这 64 个卷积极可能整体 fallback 到 CPU —— PC 上已经实测比 FP32 慢 2.5 倍，板端只会更糟。**不建议上板。**
- **静态 QOperator**：`QLinearConv` 是对的形态，但 ORT 会给 `QLinearSigmoid`/`QLinearMul`/`QLinearConcat`/`QLinearAdd` 打到 **`com.microsoft` 域**（58/58/16/8 个）。spacemit-ort 若没编译这个域，**不是 fallback，是加载直接报错**。而且 opset 12 下 QDQ 格式根本非法（`DequantizeLinear` 的 `axis` 属性是 opset 13+ 才有，实测报 `INVALID_GRAPH ... Unrecognized attribute: axis`）。
- **QDQ（opset 13/17，per-channel）**：官方模型就是这么干的 —— 保留 `Conv` 原样，只在前后插 `QuantizeLinear`/`DequantizeLinear`，让 EP 自己去 fuse 成 int8 卷积。这是唯一被官方验证过的形态。**建议照抄官方形态。**
- 如果用静态量化，校准集**至少 300 张真实场景图**（本次只有 1 张 bus.jpg 的 8 种几何变体，属于勉强能跑通流程，不能作为精度结论）。

### ③ 最大的踩坑风险是什么？

按杀伤力排序：

1. **`com.microsoft` 域算子导致板端直接加载失败**（不是性能问题，是模型根本跑不起来）。别把 `yolov8n-int8-static.onnx` 直接拷上板。上板前先在板端跑一次 `ort.InferenceSession(...)` 确认能建 session。
2. **输出张量量化 scale 事故**：`output0` 混了 0~640 与 0~1 两个量纲，per-tensor 量化直接让置信度归零、检出数 0。板端自己量化时必须排除输出节点，或把 box/score 拆成两个输出。
3. **`Resize` 的 `coordinate_transformation_mode`**：本模型是 `asymmetric` + `nearest` + `floor`。EP 只支持受限模式，如果后续把输入尺寸改成非 640（触发动态 resize）或换成 bilinear，行为会和 PC 上不一致 —— 保持 640 静态输入。
4. **`Concat`×17 + `Split`×8 的子图切分**：不违反硬约束，但会让 EP 把图切成很多小段，段间张量要来回搬运，实际加速比会低于理论值。这是性能风险，不是正确性风险。
5. 环境层面：`python` 与 `pip` 指向不同环境；`pip install torch` 会因 `WinError 5` 装到一半失败（但包体可用）；GitHub release 直连会断流（必须断点续传）。

---

## 6. 原始命令输出（原文摘录，未转述）

### 6.1 环境探测

```
$ python --version
Python 3.13.14
$ pip --version
pip 25.3 from E:\Python\Lib\site-packages\pip (python 3.13)
$ python -c "import onnx, onnxruntime; ..."
ModuleNotFoundError: No module named 'onnx'

$ python -c "import sys; print(sys.executable)"
C:\Users\23968\.workbuddy\binaries\python\versions\3.13.12\python.exe
```

```
$ /e/Python/python.exe -c "..."
numpy OK 2.4.6
onnx MISSING
onnxruntime OK 1.26.0
torch MISSING
cv2 OK 4.13.0
ultralytics MISSING
PIL OK 12.2.0
onnxsim MISSING
```

### 6.2 官方 release ONNX（opset 20）与降级失败

```
ir 9 opset [('', 20)]
producer pytorch 2.11.0
IN  images [1, 3, 640, 640]
OUT output0 [1, 84, 8400]
nodes 233 inits 144
meta {'description': 'Ultralytics YOLOv8n model trained on coco.yaml', ..., 'args': "{'batch': 1, 'half': False, 'dynamic': False, 'simplify': True, 'opset': None, 'nms': False}", ...}
```

```
$ python -c "from onnx import version_converter; ... convert_version(m,13) ..."
FAIL at 13 RuntimeError D:\a\onnx\onnx\onnx/version_converter/BaseConverter.h:66: adapter_lookup: Assertion `false' failed: No Adapter From Version $18 for Split
```

### 6.3 torch 安装（首次失败，包体可用）

```
Downloading https://download-r2.pytorch.org/whl/cpu/torch-2.15.0%2Bcpu-cp313-cp313-win_amd64.whl (126.6 MB)
   ---------------------------------------- 126.6/126.6 MB 188.0 kB/s  0:10:20
Installing collected packages: fsspec, filelock, torch
ERROR: Could not install packages due to an OSError: [WinError 5] 拒绝访问。: 'E:\Python\Lib\site-packages\torch-2.15.0+cpu.dist-info\RECORDmispl95q.tmp' -> 'E:\Python\Lib\site-packages\torch-2.15.0+cpu.dist-info\RECORD'

$ /e/Python/python.exe -c "import torch; print(torch.__version__)"
torch 2.15.0+cpu
```

### 6.4 导出（路径 A）

```
$ /e/Python/python.exe export_yolov8.py --path A
[A] ultralytics 导出: opset=12 imgsz=640 dynamic=False batch=1 simplify=True
WARNING 'half' is deprecated and will be removed in the future. Use 'quantize' instead.
Ultralytics 8.4.194  Python-3.13.12 torch-2.15.0+cpu CPU (13th Gen Intel Core i5-13500HX)
WARNING This model has no one-to-one head; using one-to-many outputs.
YOLOv8n summary (fused): 72 layers, 3,151,904 parameters, 0 gradients

PyTorch: starting from 'yolov8n.pt' with input shape (1, 3, 640, 640) BCHW and output shape(s) (1, 84, 8400) (6.2 MB)

ONNX: starting export with onnx 1.23.2 opset 12...
ONNX: slimming with onnxslim 0.1.98...
ONNX: export success  2.2s, saved as 'yolov8n.onnx' (12.3 MB)

Export complete (2.3s)
Results saved to E:\CIE-RISC-V\k1-vision\yolov8n.onnx
[A] 导出产物: yolov8n.onnx
[verify] file=yolov8n.onnx size=12851145
[verify] ir=7 opset={'ai.onnx': 12}
[verify] input  images [1, 3, 640, 640]
[verify] output output0 [1, 84, 8400]
[verify] 实跑输出 shape=(1, 84, 8400) dtype=float32
[bench] PC 单帧 均值=21.80 ms  最小=20.23 ms  最大=23.11 ms (10 次, CPU, ['CPUExecutionProvider'])
[done] 路径 A 产出 E:\CIE-RISC-V\k1-vision\yolov8n.onnx
```

### 6.5 算子统计与约束判定

```
$ /e/Python/python.exe analyze_ops.py yolov8n.onnx --json ops_yolov8n.json --md ops_yolov8n.md
OP COUNTS  (total nodes: 234)
Conv                     64
Sigmoid                  58
Mul                      58
Concat                   17
Split                     8
Add                       8
Reshape                   8
MaxPool                   3
Resize                    2  <== 受限算子
Transpose                 2
Slice                     2  <== 受限算子
Sub                       2
Softmax                   1
Div                       1

CONSTRAINT CHECK (spacemit-ort SpaceMITExecutionProvider)
[OK  ] Resize  /model.10/Resize
         in1: CONST   /model.10/Constant_1_output_0    <- initializer/Constant
         in2: CONST   /model.10/Constant_output_0      <- initializer/Constant
         mode: nearest
[OK  ] Resize  /model.13/Resize
         in1: CONST   /model.10/Constant_1_output_0    <- initializer/Constant
         in2: CONST   /model.10/Constant_output_0      <- initializer/Constant
         mode: nearest
[OK  ] Slice   /model.22/Slice
         in1: CONST   /model.22/Constant_7_output_0    <- initializer/Constant
         in2: CONST   /model.22/Mul_output_0           <- initializer/Constant
         in3: CONST   /model.22/Constant_6_output_0    <- initializer/Constant
[OK  ] Slice   /model.22/Slice_1
         in1: CONST   /model.22/Mul_output_0           <- initializer/Constant
         in2: CONST   /model.22/Mul_1_output_0         <- initializer/Constant
         in3: CONST   /model.22/Constant_6_output_0    <- initializer/Constant

resize modes: {'nearest': 2}
dynamic-shape source nodes: Counter()
RISKY NODES (will fall back to CPU): 0
```

### 6.6 量化

```
$ /e/Python/python.exe quantize_int8.py yolov8n.onnx -o yolov8n-int8.onnx --mode dynamic --eval bus.jpg
[quantize] mode=dynamic weight_type=QuantType.QUInt8 in=yolov8n.onnx out=yolov8n-int8.onnx
[size] fp32=12851145 bytes  int8=3504185 bytes
[ops] ConvInteger: 64
[ops] DynamicQuantizeLinear: 59
[ops] Cast: 64
[acc] 输出 shape fp32=(1, 84, 8400) int8=(1, 84, 8400)
[acc] max|Δ|=210.399780  mean|Δ|=0.205437  cosine=0.999291
[det] fp32 检出 46 个框, int8 检出 43 个框 (conf>0.25)
[bench] 单帧均值 (ORT_DISABLE_ALL): fp32=51.89 ms  int8=74.74 ms
```

```
$ /e/Python/python.exe quantize_int8.py yolov8n.onnx -o yolov8n-int8-static.onnx --mode static --format qoperator --eval bus.jpg
（未排除输出节点时的失败结果）
[ops] QuantizeLinear: 243
[ops] DequantizeLinear: 372
onnxruntime.capi.onnxruntime_pybind11_state.InvalidGraph: [ONNXRuntimeError] : 10 : INVALID_GRAPH :
Load model from yolov8n-int8-qdq.onnx failed:This is an invalid model. In Node,
("model.0.conv.bias_DequantizeLinear", DequantizeLinear, "", -1) : ... , Error Unrecognized attribute: axis for operator DequantizeLinear

（改成 QOperator 格式后，仍检出 0 框）
[det] fp32 检出 46 个框, int8 检出 0 个框 (conf>0.25)
```

```
（加 --exclude-output 后修复）
$ /e/Python/python.exe quantize_int8.py yolov8n.onnx -o yolov8n-int8-static.onnx --mode static --format qoperator --exclude-output --eval bus.jpg
[quantize] 排除输出节点: ['/model.22/Concat_3']
[size] fp32=12851145 bytes  int8=3428411 bytes
[ops] QLinearConv: 64
[acc] max|Δ|=190.861847  mean|Δ|=0.241495  cosine=0.999425
[det] fp32 检出 46 个框, int8 检出 46 个框 (conf>0.25)
[bench] 单帧均值 (ORT_DISABLE_ALL): fp32=64.42 ms  int8=47.25 ms
```

### 6.7 三模型统一计时 + 检测一致性

```
yolov8n.onnx                   12.85 MB  mean= 22.52 ms  min= 20.16  p50= 22.40  fps= 44.4
yolov8n-int8.onnx               3.50 MB  mean= 55.64 ms  min= 45.39  p50= 55.16  fps= 18.0
yolov8n-int8-static.onnx        3.43 MB  mean= 26.86 ms  min= 22.87  p50= 26.01  fps= 37.2
```

```
$ /e/Python/python.exe compare_int8.py bus.jpg yolov8n.onnx yolov8n-int8.onnx yolov8n-int8-static.onnx
=== yolov8n.onnx  输出(1, 84, 8400)  全图最高类分数=0.8902  检出(det)数=5
    person       score=0.8902 box=(477,226,560,521)
    person       score=0.8833 box=(211,241,284,507)
    person       score=0.8784 box=(110,235,225,537)
    bus          score=0.8434 box=(99,137,555,460)
    person       score=0.4363 box=(80,326,114,515)
=== yolov8n-int8.onnx  输出(1, 84, 8400)  全图最高类分数=0.8973  检出(det)数=5
=== yolov8n-int8-static.onnx  输出(1, 84, 8400)  全图最高类分数=0.8901  检出(det)数=5
=== 相对 FP32 的检测一致性 (IoU>0.5 且类别相同算命中) ===
yolov8n-int8.onnx: TP=5  FP=0  FN=0  (fp32 基准 5 框)
    与 fp32 框的最大 IoU: 均值=0.966 最小=0.901
yolov8n-int8-static.onnx: TP=5  FP=0  FN=0  (fp32 基准 5 框)
    与 fp32 框的最大 IoU: 均值=0.877 最小=0.788
```

### 6.8 官方 model-zoo 模型（用于第 5 节结论）

```
$ curl -sIL https://archive.spacemit.com/spacemit-ai/model_zoo/vision/yolov8/yolov8n.q.onnx
HTTP/1.1 200 OK
Content-Length: 3535915
Last-Modified: Thu, 04 Jun 2026 07:18:41 GMT
$ curl -sIL .../yolov8n_no_dfl.q.onnx
HTTP/1.1 200 OK
Content-Length: 3367042
```

```
==== official_model_zoo/yolov8n.q.onnx
 ir 10 opset [('ai.onnx', 17)]
 in  [('images', [1, 3, 640, 640])]
 out [('output0', [1, 84, 8400])]
 nodes 736
 ops {'DequantizeLinear': 283, 'QuantizeLinear': 220, 'Conv': 64, 'Sigmoid': 58, 'Mul': 58, 'Concat': 19, 'Split': 9, 'Add': 8, 'Reshape': 5, 'MaxPool': 3, 'Resize': 2, 'Slice': 2, 'Sub': 2, 'Transpose': 1, 'Softmax': 1, 'Div': 1}

==== official_model_zoo/yolov8n_no_dfl.q.onnx
 ir 9 opset [('ai.onnx', 17)]
 producer pytorch 2.8.0
 out [('output0',[1,64,80,80]),('onnx::ReduceSum_329',[1,80,80,80]),('331',[1,1,80,80]), ... ]  共 9 个输出
 nodes 718
 ops {'DequantizeLinear': 283, 'QuantizeLinear': 220, 'Conv': 63, 'Sigmoid': 60, 'Mul': 57, 'Concat': 13, 'Split': 8, 'Add': 6, 'MaxPool': 3, 'ReduceSum': 3, 'Resize': 2}
 meta {'args': "{'batch': 1, 'half': False, 'dynamic': False, 'simplify': True, 'opset': 17, 'nms': False}", 'xslim_version': '2.0.6', ...}
```

```
（官方模型的约束判定）
resize modes: {'nearest': 2}
dynamic-shape source nodes: Counter()
RISKY NODES (will fall back to CPU): 0
（Resize 的 scales 来自 PPQ_Variable_5 / PPQ_Variable_6，均为 initializer）

（官方 q 模型在 bus.jpg 上）
=== official_model_zoo/yolov8n.q.onnx  全图最高类分数=0.8754  检出(det)数=5
official_model_zoo/yolov8n.q.onnx: TP=5  FP=0  FN=0  (fp32 基准 5 框)
    与 fp32 框的最大 IoU: 均值=0.970 最小=0.933
```

### 6.9 官方示例里对模型的说明（原文，来自 model-zoo-vision 的 `examples/yolov8/README.md`）

```
| 模型文件 | 导出格式 | ONNX 输出 | 后处理路径 |
| `yolov8n_no_dfl.q.onnx`（默认）| 多分支头，DFL 解码层未内置、留给后处理端 | 多个（box/score/score_sum 每尺度一组）| 多分支 DFL 解码 |
| `yolov8n.q.onnx` | 官方 Ultralytics 单输出（含内置解码）| 1 个，形如 `[1, 84, 8400]` | 单输出解析 |

- **默认模型文件**：`~/.cache/models/vision/yolov8/yolov8n_no_dfl.q.onnx`
- `default_params.providers`：`[SpaceMITExecutionProvider]`
```

下载地址（`examples/yolov8/scripts/download_models.sh` 原文，已验证可访问）：
```
BASE_URL="https://archive.spacemit.com/spacemit-ai/model_zoo/vision/yolov8"
download "yolov8n_no_dfl.q.onnx"
download "yolov8n.q.onnx"
```

---

## 7. 复现方式

```bash
cd /e/CIE-RISC-V/k1-vision
PY=/e/Python/python.exe

# 1. 导出 FP32（opset 12，静态 shape，无 NMS）
$PY export_yolov8.py --path A

# 2. 算子统计 + spacemit-ort 约束逐节点判定
$PY analyze_ops.py yolov8n.onnx --json ops_yolov8n.json --md ops_yolov8n.md

# 3. 量化（动态 / 静态）
$PY quantize_int8.py yolov8n.onnx -o yolov8n-int8.onnx --mode dynamic --eval bus.jpg
$PY quantize_int8.py yolov8n.onnx -o yolov8n-int8-static.onnx --mode static \
    --format qoperator --exclude-output --eval bus.jpg

# 4. 检测一致性对比
$PY compare_int8.py bus.jpg yolov8n.onnx yolov8n-int8.onnx yolov8n-int8-static.onnx
```

依赖：`onnx onnxruntime numpy opencv-python onnxslim`；路径 A 额外需要 `torch(cpu) torchvision(cpu) ultralytics`。
