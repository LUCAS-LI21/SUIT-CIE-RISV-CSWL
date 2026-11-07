# 灵眸慧伴 · SUIT-CIE-RISV-CSWL

面向宠物健康监护的边缘智能系统。算力跑在 RISC-V 开发板 **K1（MUSE Pi Pro / SpacemiT）** 上，
交互端是 **SC-3568HA 平板（OpenHarmony 5.0.0.71 / ArkTS）**，两者用一根网线直连（实测 0.88 ms）。

系统在本机完成四路感知与一次对话：

| 能力 | 实现 | 端口 |
|---|---|---|
| 视觉：宠物检测 + 动作判定 | YOLOv8n（int8 静态量化，SpaceMIT EP） | 11436 |
| 听觉：宠物叫声分类 | 规则分类器（mel/MFCC/F0）+ MobileNetV2 int8 复核 | 11439 / 11442 |
| 人声：语音识别 | SenseVoice-Small int8 + silero-VAD（sherpa-onnx） | 11440 / 11441 |
| 知识库：有出处的答复 | 纯 Python 检索（36 条问答，正则 + 打分） | 11435 |
| 对话：本地大模型 | qwen3:0.6b（Q4_K_M，ollama） | 11434 / 11438 |

> 全部推理在局域网内完成，不出网。

## 目录结构

```
k1/                      K1 边缘端（RISC-V，Bianbu Linux）
├── vision/              视觉与语音服务源码
│   ├── vision_service.py        视觉主服务（11436），YOLOv8n + 多目标跟踪 + 动作判定
│   ├── petvoice_service.py      叫声服务（11439），规则分类器 + 状态映射
│   ├── llm_gateway.py           LLM 网关（11438），时间片仲裁 + SSE 转发
│   ├── panns_infer.py           PANNs 推理封装（MobileNetV2 int8）
│   ├── yolov8n.onnx             检测模型（fp32，12.3 MB）
│   ├── yolov8n-int8-static.onnx 板端在用的量化模型（3.3 MB）
│   ├── deploy/                  systemd 单元、部署脚本、PANNs 侧车、麦克风 ASR 服务
│   └── *.md                     行为判据说明（BEHAVIOR_GUIDE）、叫声判据（VOICE_GUIDE）
├── kb/                  知识库服务（11435）：rag.py + pet_kb.json
└── scripts/             70+ 诊断/标定/部署脚本（端口转发、音频底噪、ASR 门限、触摸控制…）

device/                  南向固件（OpenHarmony 轻量系统 / 星闪）
├── rk2206/              环控固件：RK2206 板，LiteOS-M，MQTT 收 ctrl/ctrl3 驱动喂食与环境外设
│   ├── bsp/             板级支持包与 IoT HAL 适配（sdk_liteos、adapter/hals）
│   └── product/         产品定义、HDF 驱动、39 个样例（e1_iot_smart_home 为环控综合例程）
└── hi3863/              星闪固件：WS63V100 套件，SLE 客户端/服务端，经 MQTT 与 RK2206 联动

pad/                     3568 平板端（OpenHarmony ArkTS）
├── entry/src/main/ets/          应用源码（约 15.5k 行）
│   ├── pages/                   欢迎、登录、选宠、建档、3D 生成、主界面
│   ├── tabs/                    首页（真实/虚拟两套）、对话、监控、监测、我的
│   ├── common/config/           RemoteConfig —— 地址与端口的唯一真相
│   ├── common/services/         PetDataHub —— 应用侧数据中枢
│   └── features/                聊天、语音、3D 生成等服务
├── entry/src/main/resources/    资源（含 rawfile/3d 的 30 个动画资源）
└── tools/patch_sdktype.py       装机补丁：构建后、签名前必跑，否则 hdc install 报 9568282
```

## 硬件与环境

- **K1**：MUSE Pi Pro，SpacemiT K1，8 核（cpu0-3 带 ime AI 扩展 + 完整 RVV；cpu4-7 向量指令集不完整），
  4 GB LPDDR4X，Bianbu Linux，Python 3.10
- **3568**：SC-3568HA，OpenHarmony 5.0.0.71，API 12，720×1280
- **网络**：3568 eth0 ↔ K1 end0 网线直连，K1 静态 192.168.88.10
- **南向固件**（可选）：RK2206「通晓」开发板（OpenHarmony 3.0 轻量系统）+ WS63 星闪套件；
  两块板之间无 SLE 直连，靠 MQTT 云端中转，源码见 `device/`

## 快速开始

### K1 边缘端

```bash
# 1) 装 ollama 并拉模型
ollama pull qwen3:0.6b

# 2) 装服务（会写入 /etc/systemd/system/ 并 enable）
cd k1/vision/deploy
sudo ./install_k1_ai.sh

# 3) 核对
systemctl status k1-vision petvoice k1-gateway k1-mic-asr k1-asr-ws k1-warmup.timer
curl -s http://127.0.0.1:11436/health
```

部署脚本假定安装目录为 `/root/k1-ai`、`/root/k1-kb`，如不同请改 `*.service` 里的 `WorkingDirectory`。

### 3568 平板端

```bash
# 1) 用 DevEco Studio 打开 pad/，在 Project Structure → Signing Configs 配置签名
# 2) 构建 HAP
hvigorw --mode module -p product=default -p buildMode=release assembleHap

# 3) 装机前必跑（构建后、签名前；签名后再 patch 会破坏签名）
python pad/tools/patch_sdktype.py entry-default-unsigned.hap entry-oh.hap

# 4) 安装
hdc install entry-oh.hap
hdc shell aa start -a EntryAbility -b com.ljp.myapplication
```

改 IP 只改一处：`pad/entry/src/main/ets/common/config/RemoteConfig.ets`。
