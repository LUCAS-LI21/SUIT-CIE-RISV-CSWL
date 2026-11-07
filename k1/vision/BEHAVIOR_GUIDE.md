# 行为数据集采集指南（用网上视频）

> 适用场景：没有自己的猫狗，用公开视频/公开数据集构建训练集。
> 目标：10 类行为，每类 ≥ 600 张裁剪图，验证集按**视频来源**切分。

## 一、先把目录建好

```bash
cd k1-vision/datasets
mkdir -p behavior_videos/{lying,sitting,standing,walking,running,eating,playing,grooming,sleeping,unknown}
```

每个类别一个文件夹，**文件夹名就是标签**。视频放进去就行，脚本会自动检测猫狗并裁剪。

## 二、每类要多少视频

| 类别 | 中文 | 建议视频数 | 说明 |
|---|---|---|---|
| lying | 趴卧 | ≥ 8 | 最常见，也最容易被误判成 sleeping |
| sitting | 坐 | ≥ 8 | 与 lying 的混淆是主要难点 |
| standing | 站立 | ≥ 6 | |
| walking | 行走 | ≥ 8 | 需要连续行走，不要原地小幅挪动 |
| running | 奔跑 | ≥ 8 | |
| eating | 进食/饮水 | ≥ 6 | 头部下探动作要清楚 |
| playing | 玩耍/扑咬 | ≥ 8 | |
| grooming | 舔毛/抓挠 | ≥ 6 | 动作幅度小，最容易被裁歪 |
| sleeping | 睡眠 | ≥ 6 | 与 lying 的区别靠时长，单帧几乎不可分 |
| unknown | 不确定 | ≥ 6 | 遮挡、出画、背影——**必须留**，否则模型对垃圾输入会给高置信度 |

**总量要求**：≥ 70 段视频，总时长 ≥ 90 分钟。每段 30~90 秒即可，太长没必要（2 fps 采样下 90 秒就有 180 帧）。

## 三、挑视频的硬标准（比数量更重要）

1. **单一动作**：一段视频里只有一种行为。混着来的会让标签失真。
2. **宠物占画面 ≥ 20%**：太小的裁出来是模糊块，训练等于喂噪声。
3. **不同个体、不同来源**：每类至少来自 **4 个不同的动物**、**3 个不同拍摄者**。只用一个网红猫的素材，模型学到的是「这只猫的花纹」。
4. **覆盖差异**：室内/室外、白天/开灯、正面/侧面/俯视、近/中/远，各占一定比例。
5. **不要用动画/卡通**：与真实摄像头域差太大。
6. **避免明显后期**：大量滤镜、慢动作、剪辑特效能把运动线索改掉。

## 四、版权与合规（必须注意）

- 网上视频**仅用于内部训练与评测**，不随仓库分发、不打包进交付物、不在演示视频里直接播放他人素材。
- 优先使用**明确允许研究用途**的公开数据集；商业平台视频（短视频、影视片段）风险最高，能不用就不用。
- 训练产物（权重）通常不构成对原视频的复制，但**数据集本身不能进 git**——已加入 `.gitignore`。
- 比赛材料里描述数据来源时，写「公开视频与自采混合，仅用于研究」，不要写平台名称。

## 五、切分规则（这条最容易翻车）

脚本**按视频来源切分**，不按帧随机切分：

```
同一段视频的所有帧 → 只进 train、或只进 val、或只进 test 之一
```

原因：相邻帧几乎是同一张图。随机切帧会让验证集里出现「训练集帧的下一帧」，准确率虚高
10~20 个点，答辩时一被问「怎么划分的」就穿帮。

**每类至少 3 个视频来源**，否则无法切出 val（脚本会警告）。只有 1~2 个来源时，
该类指标不可信，宁可合并进相近类别。

## 六、跑一遍完整流程

```bash
# 0) 环境（一次性）
.venv-train/Scripts/python.exe -m pip install ultralytics onnx onnxruntime opencv-python

# 1) 视频 → 数据集（先 dry-run 看一眼类别统计）
.venv-train/Scripts/python.exe k1-vision/video_to_dataset.py \
    --src k1-vision/datasets/behavior_videos \
    --out k1-vision/datasets/behavior --dry-run

# 2) 正式抽帧裁剪（会调 YOLO 检测，有 GPU 时很快）
.venv-train/Scripts/python.exe k1-vision/video_to_dataset.py \
    --src k1-vision/datasets/behavior_videos \
    --out k1-vision/datasets/behavior --fps 2

# 3) 人工抽检（重要！）
#    打开 behavior/val/<类>/ 随机看 20 张：裁歪的、背景为主的、根本不是这个动作的，直接删

# 4) 训练
.venv-train/Scripts/python.exe k1-vision/train_behavior_cls.py \
    --data k1-vision/datasets/behavior --model yolov8n-cls.pt --epochs 40 --export

# 5) 量化
.venv-train/Scripts/python.exe k1-vision/quant_cls_int8.py \
    runs/classify/pet_behavior/weights/best.onnx --data k1-vision/datasets/behavior
```

## 七、预期结果与判读

| 指标 | 合理区间 | 说明 |
|---|---|---|
| top-1 | 78~88% | 网上视频训练、按来源切分，这个区间是正常的 |
| top-1 高于 95% | **先怀疑泄漏** | 多半是同一视频的相似帧跨了 train/val |
| top-1 低于 70% | 数据问题 | 先看混淆矩阵，通常是 lying↔sleeping、sitting↔lying |
| int8 与 FP32 一致率 | ≥ 97% | 低于此值查归一化参数与校准集 |

**可接受的混淆**：lying ↔ sleeping（单帧确实不可分，靠时长区分）、sitting ↔ lying（侧身时）。
**不可接受的混淆**：eating → running 这种，说明裁剪把食盆当成了主体。

## 八、迁移到家庭场景的注意

网上视频 → 家里摄像头存在域差（视角高度、光照色温、镜头畸变）。缓解办法：

1. 训练时开启 ultralytics 自带增强（默认已开 RandAugment/翻转）；
2. 上板后先用相机录 10 分钟自己的场景，人工标 50~100 帧做**域适应验证集**，
   看真实场景掉多少；掉超过 10 个点就补采真实数据再微调；
3. 推理端保留「置信度低于 0.5 → 降级为规则标签」的兜底，不要把模型错误直接暴露给用户。
