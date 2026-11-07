// features/chat/services/K1SpeechService.ets
// 本地语音识别：「按住说话」交给 K1 的板载麦克风录音并当场识别（:11441）。
//
// 演进过程（两次都被实测推翻，别再走回头路）
//   1) 讯飞 Spark IAT：2026-11-06 返回 {"code":11201,"licc failed"}，
//      官方定义是「该 APPID 当日交互次数超限」。当天额度用完，语音控制是
//      首页唯一入口，一挂整个交互就断。且它依赖公网 + 第三方配额，
//      答辩现场断网就废。
//   2) 3568 采集 + 上传 K1（:11440 websocket）：链路是通的（服务端返回
//      <|Speech|> 事件），但 text 长期为空或只有「我」。把录音落盘分析后
//      真相大白 —— 3568 采到的是**恒定直流偏置**，根本不是声音：
//          rms/peak = 0.878（人声必在 0.1~0.3）  零交叉率 0.0003
//          0-100 Hz 占 43.7%                     前 40 个样本全是 0 / -1
//      同一时刻 K1 的 USB 声卡（C-Media）采到的是正常环境声：
//          rms/peak = 0.185   零交叉率 0.134   1k-4k Hz 占 40.5%
//      即 3568 那块板的麦克风输入是悬空的。采集端搬到 K1 才是正解。
//
// 现在的形态
//   按下 → POST /v1/asr/start（K1 上 arecord 开录，dsnoop 与叫声服务共享设备）
//   松开 → POST /v1/asr/stop （K1 停止录音 → 推本机 11440 → 返回文本）
//   好处：不再上传音频（省 100~180 KB 上行）、不再需要 WebSocket 协议、
//   不再需要 3568 的麦克风权限、识别在板内完成（实测 3.07 s 音频 → 3.25 s）。
//
// 交互语义
//   全链路是「收齐整段音频再识别」，松开按钮才出结果，
//   因此只调用 onComplete，不产生中间结果（onResult 保留但不触发）。
//   录音本身完全在 K1 上，startListening 立刻就绪，不会像采集器那样有启动抖动。

import { common } from '@kit.AbilityKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { http } from '@kit.NetworkKit';
import { RemoteConfig } from '../../../common/config/RemoteConfig';

// ========== 对外接口（与 OpenHarmonySpeechService 保持一致，调用方零改动） ==========

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

// K1 侧 /v1/asr/stop 的返回。rms/peak/zcr 是排查麦克风的依据，正常时不展示。
interface MicAsrResp {
  ok?: boolean;
  text?: string;
  err?: string;
  reason?: string;
  dur?: number;
  rms?: number;
  peak?: number;
  zcr?: number;
  asr_ms?: number;
}

export class K1SpeechService {
  // 录音是否处于「已按下」状态（用户视角）
  private listening: boolean = false;
  // 已经开始录音的请求。按下后立刻松开时，stop 必须等它落地，
  // 否则 K1 那边还没开录就被叫停，返回「当前没有在录音」。
  private startPromise: Promise<boolean> | null = null;
  // 本次按下是否真的在 K1 上开录成功
  private captured: boolean = false;

  private callbacks: SpeechCallbacks = {};
  private context: common.UIAbilityContext | null = null;

  // 识别本身实测 3.07 s 音频 → 3.25 s（RTF≈1.06）。给 45 秒覆盖板端负载抖动
  // （视觉服务占满 CPU 时会慢一档），但不能无限等，否则按钮会一直转圈。
  private readonly RESULT_TIMEOUT_MS: number = 45000;
  private readonly START_TIMEOUT_MS: number = 8000;

  constructor() {}

  // ========== 初始化 ==========
  // 采集在 K1 上完成，3568 不需要麦克风权限了。
  // 保留这个方法是因为四个调用方都写了 await init(context)，
  // 改成直接返回 true 比去改四处调用点风险小，也避免每次进首页弹权限框。
  async init(context: common.UIAbilityContext): Promise<boolean> {
    this.context = context;
    console.info(`[K1Speech] 初始化完成（录音在 K1 板端，地址 ${RemoteConfig.micAsrUrl('')}）`);
    return true;
  }

  setCallbacks(callbacks: SpeechCallbacks): void {
    this.callbacks = callbacks;
  }

  // ========== 开始：让 K1 开录 ==========
  async startListening(): Promise<void> {
    if (this.listening) {
      console.warn('[K1Speech] 正在录音中，忽略重复开始');
      return;
    }
    this.listening = true;
    this.captured = false;
    console.info(`[K1Speech] 开始录音 → ${RemoteConfig.micAsrUrl('/v1/asr/start')}`);

    // 先回调 onStart，UI 立刻进入录音态；网络请求在后台跑。
    this.callbacks.onStart?.();

    this.startPromise = this.postStart();
    const ok = await this.startPromise;
    if (!ok && this.listening) {
      // start 失败时用户可能已经松手了，这时候由 stopListening 统一收尾，
      // 只有在「还按着」的情况下才主动报错。
      // 文案刻意不提「麦克风」：录音设备在 K1 上，提麦克风会让人以为在说平板，
      // 实测用户第一次看到「麦克风没打开」就去查 3568 的麦克风了。
      this.listening = false;
      this.callbacks.onError?.(-1, '板端语音服务未就绪，请稍后重试');
    }
  }

  private async postStart(): Promise<boolean> {
    try {
      const raw = await this.postJson('/v1/asr/start', this.START_TIMEOUT_MS);
      const resp = JSON.parse(raw) as MicAsrResp;
      if (resp.ok === true) {
        this.captured = true;
        console.info('[K1Speech] ✅ K1 已开始录音');
        return true;
      }
      console.error(`[K1Speech] 开始录音失败: ${resp.err}`);
      return false;
    } catch (err) {
      const e = err as BusinessError;
      console.error(`[K1Speech] 开始录音异常: ${e.message}`);
      return false;
    }
  }

  // ========== 停止：让 K1 停止并识别 ==========
  async stopListening(): Promise<void> {
    if (!this.listening) {
      return;
    }
    this.listening = false;

    // 等 start 落地（用户可能 200 ms 就松手了）
    if (this.startPromise !== null) {
      await this.startPromise;
      this.startPromise = null;
    }
    if (!this.captured) {
      this.callbacks.onError?.(-1, '板端语音服务未就绪，请稍后重试');
      return;
    }
    this.captured = false;

    console.info('[K1Speech] 松开，等待板端识别…');
    try {
      const raw = await this.postJson('/v1/asr/stop', this.RESULT_TIMEOUT_MS);
      console.info(`[K1Speech] 板端返回: ${raw}`);
      const resp = JSON.parse(raw) as MicAsrResp;

      if (resp.ok !== true) {
        const msg: string = resp.err !== undefined && resp.err.length > 0 ? resp.err : '板端识别失败';
        this.callbacks.onError?.(-1, msg);
        return;
      }

      // 电平一直是判断「到底有没有听到人说话」的硬依据，
      // 3568 采集那次就是靠它才查清是直流偏置而不是距离太远。
      console.info(`[K1Speech] 电平 dur=${resp.dur}s rms=${resp.rms} peak=${resp.peak} `
        + `zcr=${resp.zcr} 耗时=${resp.asr_ms}ms`);

      const text: string = resp.text === undefined ? '' : resp.text;
      if (text.length === 0) {
        const why: string = resp.reason !== undefined && resp.reason.length > 0
          ? resp.reason : '没有听到声音';
        this.callbacks.onError?.(-1, `没听清（${why}），请再说一次`);
        return;
      }
      this.callbacks.onComplete?.(text);
    } catch (err) {
      const e = err as BusinessError;
      console.error(`[K1Speech] 停止录音异常: ${e.message}`);
      this.callbacks.onError?.(-1, `板端识别失败: ${e.message}`);
    }
  }

  // 用户中途取消（切页、销毁）：让 K1 停止，结果丢弃。
  cancelListening(): void {
    if (!this.listening && !this.captured) {
      return;
    }
    this.listening = false;
    this.captured = false;
    this.startPromise = null;
    // 不等结果，火一发就走：K1 那边会停止录音，识别结果没人收也无所谓。
    this.postJson('/v1/asr/stop', 5000).catch(() => {
      console.warn('[K1Speech] 取消时停止录音的请求失败，忽略');
    });
  }

  destroy(): void {
    console.info('[K1Speech] 销毁服务');
    this.cancelListening();
    this.context = null;
    this.callbacks = {};
  }

  // ========== HTTP ==========
  private async postJson(path: string, timeoutMs: number): Promise<string> {
    const req = http.createHttp();
    try {
      const resp = await req.request(RemoteConfig.micAsrUrl(path), {
        method: http.RequestMethod.POST,
        connectTimeout: 5000,
        readTimeout: timeoutMs,
        header: { 'Content-Type': 'application/json' },
        extraData: '{}'
      });
      if (resp.responseCode !== 200) {
        throw new Error(`HTTP ${resp.responseCode}`);
      }
      const r = resp.result;
      return typeof r === 'string' ? r as string : JSON.stringify(r);
    } finally {
      // 不复用连接池：一次识别一条请求，长连接留着反而会在板端超时后拿到脏连接
      req.destroy();
    }
  }
}
