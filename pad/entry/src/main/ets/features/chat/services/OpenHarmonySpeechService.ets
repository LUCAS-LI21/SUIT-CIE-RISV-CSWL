// features/chat/services/OpenHarmonySpeechService.ets
// 科大讯飞中英识别大模型 (Spark IAT) WebSocket 流式识别服务
import { common } from '@kit.AbilityKit';
import { abilityAccessCtrl, Permissions } from '@kit.AbilityKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { audio } from '@kit.AudioKit';
import { webSocket } from '@kit.NetworkKit';
import { cryptoFramework } from '@kit.CryptoArchitectureKit';
import { util } from '@kit.ArkTS';
import { iflytekConfig, defaultBusinessConfig } from './SpeechServiceConfig';

// ========== 对外接口（保持不变，调用方无需修改） ==========

export interface RecognitionResult {
  text: string;
  isFinal: boolean;
}

export interface SpeechCallbacks {
  onStart?: () => void;
  onResult?: (result: RecognitionResult) => void;
  onComplete?: (finalText: string) => void;
  onError?: (code: number, message: string) => void;
}

// ========== 大模型 API 请求接口定义 ==========

// 第一帧请求：包含 header + parameter + payload
interface IflytekRequestHeader {
  app_id: string;
  status: number; // 0=首帧, 1=中间帧, 2=最后一帧
  res_id?: string; // 应用级热词 id（可选）
}

interface IflytekIatResultParams {
  encoding: string; // "utf8"
  compress: string; // "raw"
  format: string; // "json"
}

interface IflytekIatParams {
  domain: string; // "slm" 大模型识别
  language: string; // "zh_cn"
  accent: string; // "mandarin"
  eos?: number; // 静音超时(ms)
  ltc?: number; // 语言筛选：1/2/3
  dwa?: string; // 动态修正："wpgs"
  dhw?: string; // 会话热词
  result?: IflytekIatResultParams; // 输出格式声明
}

interface IflytekRequestParameter {
  iat: IflytekIatParams;
}

interface IflytekRequestPayloadAudio {
  encoding: string; // "raw" (PCM) 或 "lame" (MP3)
  sample_rate: number; // 16000 或 8000
  channels: number; // 1
  bit_depth: number; // 16
  seq: number; // 数据包序号，从 1 开始递增
  status: number; // 0/1/2，同 header.status
  audio: string; // base64 编码的音频数据
}

interface IflytekRequestPayload {
  audio: IflytekRequestPayloadAudio;
}

interface IflytekFirstFrameRequest {
  header: IflytekRequestHeader;
  parameter: IflytekRequestParameter;
  payload: IflytekRequestPayload;
}

interface IflytekAudioFrameRequest {
  header: IflytekRequestHeader;
  payload: IflytekRequestPayload;
}

// ========== 大模型 API 响应接口定义 ==========

interface IflytekResponseHeader {
  code: number;
  message: string;
  sid: string;
  status: number; // 0/1/2
}

interface IflytekResponsePayloadResult {
  compress: string; // "raw"
  encoding: string; // "utf8"
  format: string; // "json"
  seq: number;
  status: number; // 0/1/2
  text: string; // base64 编码的识别结果 JSON
}

interface IflytekResponsePayload {
  result: IflytekResponsePayloadResult;
}

interface IflytekResponse {
  header: IflytekResponseHeader;
  payload: IflytekResponsePayload;
}

// base64 解码后的识别结果结构
interface IflytekCwItem {
  w: string; // 字词文本
  sc?: number; // 保留字段
}

interface IflytekWsItem {
  bg: number;
  cw: Array<IflytekCwItem>;
}

interface DecodedResultText {
  sn: number; // 结果序号
  ls: boolean; // 是否最后一片结果
  bg: number; // 起始帧偏移
  ed: number; // 保留字段
  pgs?: string; // "apd" 追加 / "rpl" 替换（dwa=wpgs 时出现）
  rg?: number[]; // 替换范围，如 [2,5]（dwa=wpgs 时出现）
  ws: Array<IflytekWsItem>;
}

// 结果缓冲区条目 — 用于动态修正(apd/rpl)的正确拼接
interface ResultBufferEntry {
  sn: number; // 结果序号（来自 decodedResult.sn）
  text: string; // 本段拼接后的文字
}

// ========== 语音识别服务核心类 ==========

export class OpenHarmonySpeechService {
  private audioCapturer: audio.AudioCapturer | null = null;
  private ws: webSocket.WebSocket | null = null;
  private callbacks: SpeechCallbacks = {};
  private isListening: boolean = false;
  private context: common.UIAbilityContext | null = null;
  private finalText: string = '';
  private isWsConnected: boolean = false;
  private audioFrameCount: number = 0;

  // 结果缓冲区 — 按 sn 编号存储每段识别结果，支持动态修正(rpl)的正确替换
  private resultBuffer: Array<ResultBufferEntry> = [];
  private firstFrameSent: boolean = false; // 首帧是否已发送（首帧需携带首个音频包）

  // 音频缓冲与定时发送
  private audioChunkQueue: string[] = []; // base64 编码的音频数据队列
  private sendTimer: number = -1; // 定时发送的 interval ID
  private seqNumber: number = 0; // 数据包序号

  // 讯飞大模型 API 地址
  private readonly wsUrl: string = 'wss://iat.xf-yun.com/v1';
  private readonly authHost: string = 'iat.xf-yun.com';
  private readonly authRequestLine: string = 'GET /v1 HTTP/1.1';

  // 讯飞 API 配置
  private readonly appId: string = iflytekConfig.appId;
  private readonly apiKey: string = iflytekConfig.apiKey;
  private readonly apiSecret: string = iflytekConfig.apiSecret;
  private readonly businessConfig = defaultBusinessConfig;

  // 发送间隔与块大小
  private readonly chunkInterval: number = 40; // 40ms
  private readonly chunkSize: number = 1280; // 1280 bytes = 40ms * 16kHz * 16bit * 1ch

  constructor() {}

  // ========== 权限管理 ==========
  private async checkAndRequestPermission(context: common.UIAbilityContext): Promise<boolean> {
    const atManager = abilityAccessCtrl.createAtManager();
    const permission: Permissions = 'ohos.permission.MICROPHONE';

    try {
      const grantStatus = await atManager.checkAccessToken(
        context.applicationInfo.accessTokenId,
        permission
      );
      if (grantStatus === abilityAccessCtrl.GrantStatus.PERMISSION_GRANTED) {
        console.info('[SpeechService] 麦克风权限已授予');
        return true;
      }

      console.info('[SpeechService] 请求麦克风权限...');
      const requestResult = await atManager.requestPermissionsFromUser(context, [permission]);
      const isGranted = requestResult.authResults[0] === abilityAccessCtrl.GrantStatus.PERMISSION_GRANTED;
      console.info('[SpeechService] 权限请求结果:', isGranted);
      return isGranted;
    } catch (err) {
      const error = err as BusinessError;
      console.error(`[SpeechService] 权限处理失败: ${error.message}`);
      return false;
    }
  }

  // ========== 初始化 ==========
  async init(context: common.UIAbilityContext): Promise<boolean> {
    this.context = context;

    const hasPermission = await this.checkAndRequestPermission(context);
    if (!hasPermission) {
      console.error('[SpeechService] 麦克风权限未授予');
      this.callbacks.onError?.(-1, '麦克风权限未授予');
      return false;
    }

    console.info('[SpeechService] 初始化成功');
    return true;
  }

  setCallbacks(callbacks: SpeechCallbacks): void {
    this.callbacks = callbacks;
  }

  // ========== 开始识别 ==========
  async startListening(): Promise<void> {
    if (this.isListening) {
      console.warn('[SpeechService] 正在识别中');
      return;
    }

    console.info('[SpeechService] 开始语音识别 (大模型版)');
    this.isListening = true;
    this.finalText = '';
    this.resultBuffer = [];
    this.audioChunkQueue = [];
    this.audioFrameCount = 0;
    this.seqNumber = 1; // 文档要求 seq 从 1 开始
    this.firstFrameSent = false;

    // 1. 建立 WebSocket 连接
    const wsConnected = await this.connectWebSocket();
    if (!wsConnected) {
      this.isListening = false;
      this.callbacks.onError?.(-1, '连接语音服务失败');
      return;
    }

    // 2. 启动音频采集
    try {
      await this.startAudioCapture();
      this.callbacks.onStart?.();
    } catch (err) {
      const error = err as BusinessError;
      console.error(`[SpeechService] 启动录音失败: ${error.message}`);
      this.isListening = false;
      this.disconnectWebSocket();
      this.callbacks.onError?.(-1, '启动录音失败');
    }
  }

  // ========== 停止识别 ==========
  async stopListening(): Promise<void> {
    console.info('[SpeechService] 停止语音识别');

    if (!this.isListening) {
      return;
    }

    this.isListening = false;
    this.stopSendTimer();
    this.stopAudioCapture();

    console.info(`[SpeechService] 本次识别共发送 ${this.audioFrameCount} 个音频帧`);

    // 发送最后一帧（结束标识）
    if (this.ws && this.isWsConnected) {
      this.sendAudioEnd();
    }

    // 等待一段时间让服务端返回最终结果，然后断开连接
    setTimeout(() => {
      this.disconnectWebSocket();
      if (this.finalText.length > 0) {
        this.callbacks.onComplete?.(this.finalText);
      } else {
        if (this.audioFrameCount === 0) {
          console.warn('[SpeechService] 未检测到有效音频，可能是说话时间过短或静音');
          this.callbacks.onError?.(-1, '未检测到有效语音，请重新说话');
        } else {
          console.warn('[SpeechService] 音频已发送但未识别到文本');
        }
        this.callbacks.onComplete?.('');
      }
    }, 500);
  }

  // ========== 取消识别 ==========
  cancelListening(): void {
    console.info('[SpeechService] 取消语音识别');
    this.isListening = false;
    this.stopSendTimer();
    this.stopAudioCapture();
    this.disconnectWebSocket();
    this.audioChunkQueue = [];
  }

  // ========== 销毁服务 ==========
  destroy(): void {
    console.info('[SpeechService] 销毁服务');
    this.cancelListening();
    this.context = null;
    this.callbacks = {};
  }

  // ========== WebSocket 连接管理 ==========
  private async connectWebSocket(): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
      try {
        const authUrl = this.generateAuthUrl();

        this.ws = webSocket.createWebSocket();
        this.ws.on('open', (err: BusinessError, value: Object) => {
          console.info('[SpeechService] WebSocket 连接成功');
          this.isWsConnected = true;
          resolve(true);
          // 首帧不再在此处发送，改为等第一个音频包到来后一起发送（符合官方文档规范）
        });

        this.ws.on('message', (err: BusinessError, value: string | ArrayBuffer) => {
          if (err) {
            console.error(`[SpeechService] WebSocket 消息错误: ${err.message}`);
            return;
          }
          const message = value as string;
          this.handleWebSocketMessage(message);
        });

        this.ws.on('error', (err: BusinessError) => {
          console.error(`[SpeechService] WebSocket 错误: ${err.message}`);
          this.isWsConnected = false;
          if (this.isListening) {
            this.callbacks.onError?.(-1, `连接错误: ${err.message}`);
          }
        });

        this.ws.on('close', (err: BusinessError, value: webSocket.CloseResult) => {
          console.info(`[SpeechService] WebSocket 关闭, code: ${value.code}, reason: ${value.reason}`);
          this.isWsConnected = false;
        });

        console.info('[SpeechService] 正在连接 WebSocket...');
        this.ws.connect(authUrl, (err: BusinessError, value: boolean) => {
          if (err) {
            console.error(`[SpeechService] WebSocket 连接失败: ${err.message}`);
            resolve(false);
          }
        });
      } catch (err) {
        const error = err as BusinessError;
        console.error(`[SpeechService] 创建 WebSocket 失败: ${error.message}`);
        resolve(false);
      }
    });
  }

  private disconnectWebSocket(): void {
    if (this.ws) {
      try {
        this.ws.close((err: BusinessError) => {
          if (err) {
            console.error(`[SpeechService] 关闭 WebSocket 失败: ${err.message}`);
          }
        });
      } catch (e) {
        console.error('[SpeechService] 关闭 WebSocket 异常');
      }
      this.ws = null;
    }
    this.isWsConnected = false;
  }

  // ========== 生成鉴权 URL ==========
  private generateAuthUrl(): string {
    const date = new Date().toUTCString();

    // 构建签名原始字符串：host + date + request-line
    const signatureOrigin = `host: ${this.authHost}\ndate: ${date}\n${this.authRequestLine}`;

    // 使用 HMAC-SHA256 计算签名
    const signature = this.hmacSha256(signatureOrigin, this.apiSecret);

    // 构建 authorization 原始字符串
    const authOrigin =
      `api_key="${this.apiKey}", algorithm="hmac-sha256", headers="host date request-line", signature="${signature}"`;

    // Base64 编码
    const authorization = this.base64Encode(authOrigin);

    // 构建完整 URL
    const url =
      `${this.wsUrl}?authorization=${authorization}&date=${encodeURIComponent(date)}&host=${this.authHost}`;
    console.info('[SpeechService] 鉴权 URL 生成成功');
    return url;
  }

  // ========== HMAC-SHA256 签名 ==========
  private hmacSha256(message: string, secret: string): string {
    try {
      const secretData = this.stringToUint8Array(secret);
      const messageData = this.stringToUint8Array(message);

      const keyBlob: cryptoFramework.DataBlob = { data: secretData };
      const symKeyGenerator = cryptoFramework.createSymKeyGenerator('HMAC');
      const symKey = symKeyGenerator.convertKeySync(keyBlob);

      const mac = cryptoFramework.createMac('SHA256');
      mac.initSync(symKey);
      mac.updateSync({ data: messageData });
      const macOutput = mac.doFinalSync();

      return this.arrayBufferToBase64(macOutput.data);
    } catch (err) {
      console.error('[SpeechService] HMAC 计算失败:', JSON.stringify(err));
      return '';
    }
  }

  // ========== 编码工具 ==========
  private stringToUint8Array(str: string): Uint8Array {
    const textEncoder = util.TextEncoder.create();
    return textEncoder.encodeInto(str);
  }

  private arrayBufferToBase64(buffer: ArrayBuffer): string {
    const base64Helper = new util.Base64Helper();
    const uint8Array = new Uint8Array(buffer);
    return base64Helper.encodeToStringSync(uint8Array);
  }

  private base64Encode(str: string): string {
    const base64Helper = new util.Base64Helper();
    const uint8Array = this.stringToUint8Array(str);
    return base64Helper.encodeToStringSync(uint8Array);
  }

  // ========== 发送 WebSocket 数据（大模型 API 格式） ==========

  private buildIatParams(): IflytekIatParams {
    const domain = this.businessConfig.domain;
    const language = this.businessConfig.language;
    const accent = this.businessConfig.accent;
    const eos = this.businessConfig.eos;
    const ltc = this.businessConfig.ltc;
    const dwa = this.businessConfig.dwa;
    const dhw = this.businessConfig.dhw;
    const resultParams: IflytekIatResultParams = { encoding: 'utf8', compress: 'raw', format: 'json' };
    const iatParams: IflytekIatParams = {
      domain: domain,
      language: language,
      accent: accent,
      eos: eos,
      ltc: ltc,
      dwa: dwa,
      dhw: dhw,
      result: resultParams
    };
    return iatParams;
  }

  private buildRequestHeader(status: number): IflytekRequestHeader {
    const appId = this.appId;
    const header: IflytekRequestHeader = { app_id: appId, status: status };
    return header;
  }

  private buildAudioPayload(audioData: string, seq: number, status: number): IflytekRequestPayloadAudio {
    const audio: IflytekRequestPayloadAudio = {
      encoding: 'raw', sample_rate: 16000, channels: 1, bit_depth: 16,
      seq: seq, status: status, audio: audioData
    };
    return audio;
  }

  private sendFirstFrameWithAudio(firstAudio: string): void {
    const iatParams: IflytekIatParams = this.buildIatParams();
    const header: IflytekRequestHeader = this.buildRequestHeader(0);
    const parameter: IflytekRequestParameter = { iat: iatParams };
    const audioPayload: IflytekRequestPayloadAudio = this.buildAudioPayload(firstAudio, 1, 0);
    const payload: IflytekRequestPayload = { audio: audioPayload };
    const params: IflytekFirstFrameRequest = { header: header, parameter: parameter, payload: payload };

    this.sendWebSocketMessage(params);
    this.audioFrameCount++;
    console.info('[SpeechService] 首帧已发送(含首个音频包), domain=slm, eos=8000, ltc=2, dhw已配置');
  }

  private sendAudioChunk(audioData: string, seq: number): void {
    const header: IflytekRequestHeader = this.buildRequestHeader(1);
    const audio: IflytekRequestPayloadAudio = this.buildAudioPayload(audioData, seq, 1);
    const payload: IflytekRequestPayload = { audio: audio };
    const params: IflytekAudioFrameRequest = { header: header, payload: payload };

    this.sendWebSocketMessage(params);
  }

  private sendAudioEnd(): void {
    const endSeq: number = this.seqNumber + 1;
    const header: IflytekRequestHeader = this.buildRequestHeader(2);
    const audio: IflytekRequestPayloadAudio = this.buildAudioPayload('', endSeq, 2);
    const payload: IflytekRequestPayload = { audio: audio };
    const params: IflytekAudioFrameRequest = { header: header, payload: payload };

    this.sendWebSocketMessage(params);
    console.info('[SpeechService] 结束帧已发送');
  }

  private sendWebSocketMessage(params: IflytekFirstFrameRequest | IflytekAudioFrameRequest): void {
    if (this.ws && this.isWsConnected) {
      try {
        const message = JSON.stringify(params);
        this.ws.send(message, (err: BusinessError) => {
          if (err) {
            console.error(`[SpeechService] 发送消息失败: ${err.message}`);
          }
        });
      } catch (e) {
        console.error('[SpeechService] 发送消息异常');
      }
    }
  }

  // ========== 音频定时发送机制 ==========
  // 按 40ms 间隔从队列中取出数据发送，符合讯飞分包建议

  private startSendTimer(): void {
    if (this.sendTimer !== -1) {
      return; // 已在运行
    }
    this.sendTimer = setInterval(() => {
      if (this.isListening && this.isWsConnected && this.audioChunkQueue.length > 0) {
        const chunk = this.audioChunkQueue.shift();
        if (chunk) {
          this.seqNumber++;
          this.sendAudioChunk(chunk, this.seqNumber);
          this.audioFrameCount++;
        }
      }
    }, this.chunkInterval);
  }

  private stopSendTimer(): void {
    if (this.sendTimer !== -1) {
      clearInterval(this.sendTimer);
      this.sendTimer = -1;
    }
  }

  // ========== 处理 WebSocket 消息（大模型 API 响应格式） ==========
  // 核心：使用 resultBuffer 按 sn 编号存储，正确处理 pgs=apd(追加)/rpl(替换)

  private handleWebSocketMessage(message: string): void {
    try {
      const response: IflytekResponse = JSON.parse(message) as IflytekResponse;
      console.info('[SpeechService] 收到响应:', message);

      // 检查错误码
      if (response.header.code !== 0) {
        console.error(`[SpeechService] 服务端错误: ${response.header.code} - ${response.header.message}`);
        this.callbacks.onError?.(response.header.code, response.header.message);
        return;
      }

      // 首次成功响应可能不含 payload（仅确认连接），跳过
      if (!response.payload || !response.payload.result) {
        console.info('[SpeechService] 收到连接确认(无识别结果)');
        return;
      }

      const resultPayload = response.payload.result;

      // text 字段是 base64 编码的 UTF-8 JSON，需要先解码
      const decodedText = this.decodeBase64Utf8(resultPayload.text);
      console.info('[SpeechService] base64 解码后:', decodedText);

      const decodedResult: DecodedResultText = JSON.parse(decodedText) as DecodedResultText;
      console.info('[SpeechService] 解析识别结果: sn=' + decodedResult.sn +
        ', pgs=' + (decodedResult.pgs ?? 'null') +
        ', rg=' + (decodedResult.rg ? JSON.stringify(decodedResult.rg) : 'null') +
        ', ls=' + decodedResult.ls);

      // 从 ws.cw 中提取本段文字
      let segmentText = '';
      if (decodedResult.ws) {
        for (const wsItem of decodedResult.ws) {
          if (wsItem.cw) {
            for (const cwItem of wsItem.cw) {
              segmentText += cwItem.w;
            }
          }
        }
      }

      if (segmentText.length === 0) {
        console.warn('[SpeechService] 识别片段为空,可能是静音');
        return;
      }

      // 根据 pgs 策略更新结果缓冲区
      const pgs = decodedResult.pgs ?? 'apd'; // 无 pgs 时默认追加
      if (pgs === 'rpl' && decodedResult.rg) {
        // 替换模式：删除 rg 范围内的旧结果，插入当前结果
        const rgStart = decodedResult.rg[0];
        const rgEnd = decodedResult.rg[1];
        const newBuffer: Array<ResultBufferEntry> = [];
        for (const entry of this.resultBuffer) {
          // 保留不在替换范围内的条目
          if (entry.sn < rgStart || entry.sn > rgEnd) {
            newBuffer.push(entry);
          }
        }
        newBuffer.push({ sn: decodedResult.sn, text: segmentText });
        this.resultBuffer = newBuffer;
        console.info(`[SpeechService] rpl 替换: rg=[${rgStart},${rgEnd}], 当前片段 sn=${decodedResult.sn}`);
      } else {
        // 追加模式：直接添加到缓冲区
        this.resultBuffer.push({ sn: decodedResult.sn, text: segmentText });
        console.info(`[SpeechService] apd 追加: sn=${decodedResult.sn}`);
      }

      // 按 sn 排序并拼接所有缓冲区条目，生成最终文本
      this.finalText = this.computeFinalTextFromBuffer();
      console.info(`[SpeechService] 当前累计文本: "${this.finalText}"`);

      const isFinal = decodedResult.ls; // ls=true 表示最后一片结果
      const recognitionResult: RecognitionResult = { text: this.finalText, isFinal };
      this.callbacks.onResult?.(recognitionResult);
    } catch (err) {
      console.error('[SpeechService] 解析响应失败:', JSON.stringify(err));
    }
  }

  // 按 sn 升序排列缓冲区条目并拼接文本
  private computeFinalTextFromBuffer(): string {
    // 按 sn 排序
    const sorted = this.resultBuffer.sort((a: ResultBufferEntry, b: ResultBufferEntry) => a.sn - b.sn);
    let result = '';
    for (const entry of sorted) {
      result += entry.text;
    }
    return result;
  }

  // base64 解码 UTF-8 文本（内联解码，避免 TextDecoder ArkTS兼容问题）
  private decodeBase64Utf8(base64Str: string): string {
    const b64Helper = new util.Base64Helper();
    const rawBytes = b64Helper.decodeSync(base64Str);
    let decoded = '';
    let idx = 0;
    const len = rawBytes.length;
    while (idx < len) {
      const b = rawBytes[idx];
      let cp = 0;
      let step = 1;
      if ((b & 0x80) === 0) {
        cp = b;
      } else if ((b & 0xE0) === 0xC0) {
        cp = ((b & 0x1F) << 6) | (rawBytes[idx + 1] & 0x3F);
        step = 2;
      } else if ((b & 0xF0) === 0xE0) {
        cp = ((b & 0x0F) << 12) | ((rawBytes[idx + 1] & 0x3F) << 6) | (rawBytes[idx + 2] & 0x3F);
        step = 3;
      } else {
        cp = ((b & 0x07) << 18) | ((rawBytes[idx + 1] & 0x3F) << 12)
          | ((rawBytes[idx + 2] & 0x3F) << 6) | (rawBytes[idx + 3] & 0x3F);
        step = 4;
      }
      decoded += String.fromCodePoint(cp);
      idx += step;
    }
    return decoded;
  }

  // ========== 音频采集 ==========
  private async startAudioCapture(): Promise<void> {
    const audioStreamInfo: audio.AudioStreamInfo = {
      samplingRate: audio.AudioSamplingRate.SAMPLE_RATE_16000,
      channels: audio.AudioChannel.CHANNEL_1,
      sampleFormat: audio.AudioSampleFormat.SAMPLE_FORMAT_S16LE,
      encodingType: audio.AudioEncodingType.ENCODING_TYPE_RAW
    };

    const audioCapturerInfo: audio.AudioCapturerInfo = {
      source: audio.SourceType.SOURCE_TYPE_MIC,
      capturerFlags: 0
    };

    const options: audio.AudioCapturerOptions = {
      streamInfo: audioStreamInfo,
      capturerInfo: audioCapturerInfo
    };

    try {
      this.audioCapturer = await audio.createAudioCapturer(options);
      console.info('[SpeechService] 音频采集器创建成功');

      this.audioCapturer.on('readData', (data: ArrayBuffer) => {
        if (this.isListening && this.isWsConnected) {
          const base64Audio = this.arrayBufferToBase64(data);

          // 首帧：携带参数 + 第一个音频包一起发送（官方文档要求首帧带音频数据）
          if (!this.firstFrameSent) {
            this.sendFirstFrameWithAudio(base64Audio);
            this.firstFrameSent = true;
            this.startSendTimer(); // 首帧发出后启动定时器
          } else {
            // 后续音频包放入队列，由定时器按 40ms 间隔取出发送
            this.audioChunkQueue.push(base64Audio);
          }
        }
      });

      await this.audioCapturer.start();
      console.info('[SpeechService] 音频采集已启动');
    } catch (err) {
      const error = err as BusinessError;
      console.error(`[SpeechService] 音频采集启动失败: ${error.message}`);
    }
  }

  private stopAudioCapture(): void {
    if (this.audioCapturer) {
      try {
        this.audioCapturer.stop();
        this.audioCapturer.off('readData');
        this.audioCapturer.release();
        console.info('[SpeechService] 音频采集器已释放');
      } catch (err) {
        const error = err as BusinessError;
        console.warn(`[SpeechService] 释放音频采集器异常: ${error.message}`);
      } finally {
        this.audioCapturer = null;
      }
    }
  }
}
