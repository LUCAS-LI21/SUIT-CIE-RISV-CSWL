// 语音识别服务配置
// OpenHarmony 语音识别服务 - 科大讯飞中英识别大模型配置

export interface IFLYTEKConfig {
  appId: string;
  apiKey: string;
  apiSecret: string;
}

export interface SpeechAudioConfig {
  sampleRate: number; // 采样率，默认 16000
  channels: number; // 声道数，默认 1
  bitsPerSample: number; // 采样位数，默认 16
  maxDuration: number; // 最大录音时长（秒），默认 60
  chunkInterval: number; // 音频发送间隔(ms)，默认 40
  chunkSize: number; // 每次发送字节数，默认 1280 (40ms * 16kHz * 16bit * 1channel)
}

export interface SpeechBusinessConfig {
  domain: string; // 识别领域：slm=大模型(推荐), iat=传统听写
  language: string; // 语言：zh_cn
  accent: string; // 方言：mandarin=普通话
  eos: number; // 静音多久后自动结束识别(ms)，默认 6000
  ltc: number; // 语言筛选：1=不筛选, 2=只出中文, 3=只出英文
  dwa: string; // 动态修正：wpgs=开启流式词级修正
  dhw: string; // 会话热词，格式: "dhw=utf-8;词语1|词语2"
}

export interface SpeechServiceConfig {
  iflytek: IFLYTEKConfig;
  audio: SpeechAudioConfig;
  business: SpeechBusinessConfig;
}

// 默认音频配置
export const defaultAudioConfig: SpeechAudioConfig = {
  sampleRate: 16000,
  channels: 1,
  bitsPerSample: 16,
  maxDuration: 60,
  chunkInterval: 40,
  chunkSize: 1280
};

// 默认业务参数配置 - 中英识别大模型
export const defaultBusinessConfig: SpeechBusinessConfig = {
  domain: 'slm', // 大模型识别（准确率更高）
  language: 'zh_cn',
  accent: 'mandarin',
  eos: 8000, // 静音8秒后结束识别，避免句子中间停顿被截断
  ltc: 2, // 只输出中文，避免中英混杂
  dwa: 'wpgs', // 开启流式词级动态修正
  dhw: 'dhw=utf-8;撒娇|打滚|甩水|伸懒腰|歪头|小跑|摇尾巴|磨爪|摇头|宠物|猫咪|小狗|小狗|喵喵|汪汪'
};

// 科大讯飞语音识别配置
//
// 2026-11-07：这里原本硬编码着可用的 appId / apiKey / apiSecret 三件套，已清空。
// 原因有两条：①密钥随代码分发等于公开；②这条链路在 SC-3568HA 上本来就跑不通
// （本机没有可用的系统语音识别引擎，实测由 K1 的 11441 板载麦克风录音识别替代）。
// 需要恢复时请到讯飞开放平台自行申请，把三个值填在下面，不要把真实值提交进仓库。
//
// 旧配置（已失效，仅留档）：
//   三件套已全部置空，需要时从科大讯飞控制台重新申请后填入。
export const iflytekConfig: IFLYTEKConfig = {
  appId: '', // 在科大讯飞开放平台获取
  apiKey: '', // 在科大讯飞开放平台获取
  apiSecret: '' // 在科大讯飞开放平台获取
};

// 完整配置示例
export const speechServiceConfig: SpeechServiceConfig = {
  iflytek: iflytekConfig,
  audio: defaultAudioConfig,
  business: defaultBusinessConfig
};

/**
 * 配置说明
 *
 * 科大讯飞中英识别大模型（Spark IAT）
 *    - 访问 https://www.xfyun.cn/doc/spark/spark_zh_iat.html
 *    - 注册账号并创建应用，开通"中英识别大模型"产品
 *    - 获取 AppID、API Key 和 API Secret
 *    - 接口地址：wss://iat.xf-yun.com/v1
 *    - 优势：基于大模型训练，识别准确率更高，支持 202 种方言免切换
 *    - 适用场景：实时对话、语音助手、宠物指令识别
 *
 * 重要参数说明：
 *    - domain: "slm" 使用大模型识别引擎（比传统 iat 更准）
 *    - eos: 静音超时(ms)，建议 8000，说话停顿太久才判定结束，避免句子中间停顿被截断
 *    - ltc: 语言筛选，设为 2 强制只出中文，减少中英混杂误识别
 *    - dwa: "wpgs" 开启动态修正，实时修正识别结果
 *    - dhw: 会话热词，传入宠物相关词汇可提升识别准确率
 */
