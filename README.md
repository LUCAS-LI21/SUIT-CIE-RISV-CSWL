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

## 几个关键设计（都踩过坑）

1. **核绑定不是可选项**。cpu0-3 与 cpu4-7 的向量指令集不对称，任何原生二进制或 int8 卷积
   必须跑在 0-3，否则 `SIGILL`（status=4/ILL）直接崩溃；纯 Python 的放 4-7。
   见 `deploy/pin-cores.sh`。
2. **时间片仲裁，不是核数拆分**。视觉与 LLM 同时跑时，20 token 的请求会从 3.3 s 劣化到 90 s 超时，
   瓶颈是内存带宽而不是核数。网关在转发 Ollama 前暂停视觉、返回后恢复。
3. **`latest` 接口必须能回退**。所有 `latest` 都是「最后一次判定」而非「此刻状态」，
   一旦某次判错又缺少释放路径，状态会永久卡住（实测出现过安静房间里一直显示「吠叫」）。
   叫声服务用「连续 3 窗安静才释放」解决。
4. **动作判定的样本闸门要跟采样率走**。闸门写死「近 1 s 至少 3 个点」在 20 fps 下没问题，
   换到 2 Hz 采样就永远凑不满，动作恒为 `unknown`。现在窗口按实际采样间隔自适应。
5. **假数据不许标「实时」**。凡是沿用上一帧的结论，返回体里带 `stale` 标记并给出年龄；
   回放模式带 `replay: true`。

## 实测指标（K1 板端，2026-11 采）

| 项 | 数值 |
|---|---|
| 视觉 | 82.2 ms/帧（int8 + SpaceMIT EP，100% 节点无回退），服务端 5.5 fps |
| 叫声 | RTF 0.96（SenseVoice 侧）／规则分类器 0.48 s 一窗 |
| 大模型 | 首 token 0.36–0.66 s，decode 7.8–10.3 tok/s，常驻 722 MB |
| 热 | cluster0 84–85 ℃，cluster1 83 ℃ |

## 不包含

- 训练数据集（`datasets/`）与训练脚本的中间产物
- 任何证书、密钥库（`*.p12` / `*.pem` / `*.p7b`）与口令
- OpenHarmony SDK、固件包、构建产物
- 第三方 API 密钥（DeepSeek / 讯飞 / Tripo3D 均需自行申请后填入，且已从代码中清空）

## 已知限制

- 训练集是用合成音频做的，真实叫声环境下的召回率尚未达标
- 视觉动作层在 `playing` / `running` 上只有合成场景验证，缺少真实宠物长时序数据
- 账号服务仍是原型（验证码直接回传、无 token），仅用于演示
- 平板端的 MQTT 监控页连的是公网 broker，显示的是 retained 历史值，不是本机实时数据

## 许可证

MIT，见 [LICENSE](LICENSE)。
