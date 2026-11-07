// common/services/PetDataHub.ets
// 数据汇聚层：把「四路真实数据」收在一处，供聊天页做现状分析与上屏。
//
// 为什么需要它
//   聊天要回答「宠物现在怎么样」，这句话必须建立在**已经采到的数据**上，
//   而不是让模型自由发挥。四路数据来源完全不同、更新节奏也不同：
//
//     环境（温度/湿度/光照/氨气） ← MQTT，由 3568 连的外部小板推送
//     视觉（行为/姿态/在场人数）   ← K1 11436
//     叫声（叫声类型/状态/建议）   ← K1 11439
//     宠物档案（名字/品种/年龄）   ← 本地 preferences
//
//   把这些散落在各页签里的值集中起来，聊天页才能一次取全，也才能
//   在界面上老老实实标出「这条数据是什么时候、从哪来的」。
//
// 一条硬规矩：**没有数据源的项必须显示「未接入」，不能填默认值冒充实时。**
//   这套系统里最容易犯的错就是给温度填 25.0、给氨气填 0，看起来一切正常，
//   实际一个字节都没从板子传过来。宁可在界面上写着「未接入」，也不要编。

import { K1Perception, VisionSnapshot } from '../utils/K1Perception';
import { K1Voice, VoiceSnapshot } from '../utils/K1Voice';
import { RemoteConfig } from '../config/RemoteConfig';
import { HttpService, HttpResponse } from '../utils/HttpService';
import { http } from '@kit.NetworkKit';

/** LLM 网关 /health 的响应。字段都可缺省——不同实现给的不一样，别假定齐全。 */
interface LlmGatewayHealth {
  engine?: string;
  model?: string;
  ok?: boolean;
}

/** 环境值。hasEnv=false 表示 MQTT 还没收到过任何一帧。 */
export class EnvValues {
  temp: number = 0;
  hum: number = 0;
  light: number = 0;
  nh3: number = 0;
  nh3Available: boolean = false;
  status: string = '';
  hasEnv: boolean = false;
  updatedAt: number = 0;
}

/**
 * 一条「规则结论」：由我们在端侧按阈值判出来的客观事实。
 *
 * 为什么不让模型去判阈值：
 *   0.6B 看到「温度 31」时，经常只回「保持环境舒适」这类空话，或者干脆
 *   判断错方向。规则层判事实、模型只负责把事实说成人话，是这套链路里
 *   唯一稳定的分工（实测差异很大，见 HANDOFF 的性能记录）。
 */
export class RuleFact {
  text: string = '';
  level: string = 'normal';   // 'normal' | 'warn' | 'alert'

  constructor(text: string, level: string) {
    this.text = text;
    this.level = level;
  }
}

export class PetDataHub {
  private static instance: PetDataHub | null = null;

  private perception: K1Perception = new K1Perception();
  private voice: K1Voice = new K1Voice();
  private httpService: HttpService = HttpService.getInstance();

  // 最近一次成功拿到的快照；null 表示「这一路没有数据」
  vision: VisionSnapshot | null = null;
  voiceSnap: VoiceSnapshot | null = null;
  env: EnvValues = new EnvValues();

  // 最近一次刷新的时间与结果，用于界面显示「X 秒前」
  lastRefreshAt: number = 0;
  lastVisionOk: boolean = false;
  lastVoiceOk: boolean = false;
  lastError: string = '';
  hostReady: boolean = false;

  // ── LLM 网关的「身份」──────────────────────────────────────────
  // K1 真机的 11438 返回 engine=k1；PC 替身返回 engine=pc-mock。
  // 界面上的来源标注必须说实话：把 PC 替身答出来的话说成「K1 本地模型」，
  // 就是拿假数据冒充实时算力，答辩时说不清。
  // 空串表示还没探到（此时按最保守的「未确认」处理）。
  llmEngine: string = '';
  private llmEngineProbed: boolean = false;

  private constructor() {
  }

  static getInstance(): PetDataHub {
    if (PetDataHub.instance === null) {
      PetDataHub.instance = new PetDataHub();
    }
    return PetDataHub.instance;
  }

  // ── 地址探测 ──────────────────────────────────────────────────────
  /**
   * 确定 K1（或代理它的 PC）到底在哪一个地址上。
   *
   * 模拟器是 QEMU 虚拟机，127.0.0.1 是它自己；访问宿主机要用虚拟网络给的
   * 别名（常见 10.0.2.2），各版本不一定相同。所以按候选列表探活，
   * 第一个 /health 返回 200 的记住，后续所有请求都用它。
   * 真板形态只有网线直连一个地址，不走这条分支。
   */
  async ensureHost(): Promise<boolean> {
    if (RemoteConfig.target() === 'board') {
      RemoteConfig.resolvedHost = RemoteConfig.BOARD_K1_HOST;
      this.hostReady = true;
      return true;
    }
    if (RemoteConfig.resolvedHost.length > 0) {
      this.hostReady = true;
      return true;
    }

    const candidates: string[] = RemoteConfig.EMULATOR_CANDIDATES;
    for (let i = 0; i < candidates.length; i++) {
      const host: string = candidates[i];
      let resp: HttpResponse;
      try {
        resp = await this.httpService.request({
          url: RemoteConfig.probeUrl(host),
          method: http.RequestMethod.GET,
          timeout: RemoteConfig.TIMEOUT_PROBE
        });
      } catch (e) {
        continue;
      }
      if (resp.success) {
        RemoteConfig.resolvedHost = host;
        this.hostReady = true;
        console.info('[PetDataHub] 算力地址确定为', host);
        return true;
      }
    }
    this.hostReady = false;
    this.lastError = '算力地址全部探测失败';
    console.warn('[PetDataHub] 候选地址全部不通:', JSON.stringify(candidates));
    return false;
  }

  /**
   * 问一次 LLM 网关「你是谁」。
   *
   * 为什么必须问：界面上那行来源标注是要给人看的，写「K1 本地模型」就得真的是
   * K1 上 qwen3:0.6b 答的。K1 的 11438 返回 engine=k1，PC 侧的替身返回
   * engine=pc-mock / local-ollama。不区分就会出现「拿云端或替身冒充板端算力」，
   * 这在答辩现场是没法解释的。
   *
   * 只探一次：地址确定后不会变，每秒刷状态条时没必要重复请求。
   */
  private async probeLlmEngine(): Promise<void> {
    this.llmEngineProbed = true;
    try {
      const resp: HttpResponse = await this.httpService.request({
        url: `${RemoteConfig.base(RemoteConfig.PORT_LLM)}/health`,
        method: http.RequestMethod.GET,
        timeout: 1500
      });
      if (resp.success && resp.data) {
        const health: LlmGatewayHealth = JSON.parse(resp.data as string) as LlmGatewayHealth;
        this.llmEngine = health.engine === undefined ? '' : health.engine;
        console.info(`[PetDataHub] LLM网关 engine=${this.llmEngine} model=${health.model}`);
      }
    } catch (e) {
      this.llmEngine = '';
      console.warn('[PetDataHub] LLM网关探测失败（不影响视觉/叫声）');
    }
  }

  // ── 环境（MQTT 推来的，由 ControlTab / HomeTab 写入）─────────────
  updateEnv(temp: number, hum: number, light: number, nh3: number,
            nh3Available: boolean, status: string, hasEnv: boolean): void {
    this.env.temp = temp;
    this.env.hum = hum;
    this.env.light = light;
    this.env.nh3 = nh3;
    this.env.nh3Available = nh3Available;
    this.env.status = status;
    this.env.hasEnv = hasEnv;
    this.env.updatedAt = Date.now();
  }

  // ── 刷新感知通道 ──────────────────────────────────────────────────
  /**
   * 拉一次视觉 + 叫声。
   *
   * 两条通道互不阻塞：视觉挂了不影响叫声，反之亦然。任何一路失败都只把
   * 对应字段置空，不清掉另一路的真实数据。
   */
  async refresh(): Promise<void> {
    const hostOk: boolean = await this.ensureHost();
    if (!hostOk) {
      this.lastVisionOk = false;
      this.lastVoiceOk = false;
      this.vision = null;
      this.voiceSnap = null;
      return;
    }

    // 地址一旦确定就不会再变，所以网关身份只探一次。
    if (!this.llmEngineProbed) {
      await this.probeLlmEngine();
    }

    let visionResult: VisionSnapshot | null = null;
    let voiceResult: VoiceSnapshot | null = null;
    try {
      visionResult = await this.perception.fetch();
    } catch (e) {
      console.warn('[PetDataHub] 视觉拉取异常:', JSON.stringify(e));
    }
    try {
      voiceResult = await this.voice.fetch();
    } catch (e) {
      console.warn('[PetDataHub] 叫声拉取异常:', JSON.stringify(e));
    }

    this.vision = visionResult;
    this.lastVisionOk = visionResult !== null;
    // 叫声通道拿不到就保留上一次的有效值（叫声是事件，不是状态；
    // 一次吠叫结束后接口会回 silence，那也算真实结果，交给下面覆盖）。
    if (voiceResult !== null) {
      this.voiceSnap = voiceResult;
      this.lastVoiceOk = true;
    } else {
      this.lastVoiceOk = false;
    }
    this.lastRefreshAt = Date.now();
  }

  // ── 规则层：把原始数值判成客观结论 ────────────────────────────────
  /**
   * 环境阈值。这些是**工程默认值**，不是医学结论。
   *
   * 单位口径务必注意（踩过）：
   *   温度 ℃、湿度 %、氨气 ppm、**光照 lux（不是百分比）**。
   *   小板经 MQTT 推上来的是 lux（HomeTab 日志实测 272lx、300lx），
   *   早期这里把光照当 0~100 的百分比判，于是 300lux 被显示成「300%」
   *   并且永远落在「偏强」区间，等于这个判据从来没work过。
   *
   *   光照舒适区间取 80~2000 lux：室内正常照明约 100~500 lux，
   *   <80 偏暗（幼宠活动受抑），>2000 通常是直射阳光或补光灯贴脸，
   *   对没有汗腺的小型动物偏热。
   */
  envFacts(): RuleFact[] {
    const out: RuleFact[] = [];
    if (!this.env.hasEnv) {
      return out;   // 没数据就不产出结论，绝不填默认值
    }
    const t: number = this.env.temp;
    if (t < 18) {
      out.push(new RuleFact(`温度${t.toFixed(1)}℃偏低`, 'warn'));
    } else if (t > 28) {
      out.push(new RuleFact(`温度${t.toFixed(1)}℃偏高`, 'warn'));
    }
    const h: number = this.env.hum;
    if (h < 40) {
      out.push(new RuleFact(`湿度${h.toFixed(0)}%偏干`, 'warn'));
    } else if (h > 70) {
      out.push(new RuleFact(`湿度${h.toFixed(0)}%偏湿`, 'warn'));
    }
    const l: number = this.env.light;
    if (l < 80) {
      out.push(new RuleFact(`光照${l.toFixed(0)}lux偏暗`, 'warn'));
    } else if (l > 2000) {
      out.push(new RuleFact(`光照${l.toFixed(0)}lux偏强`, 'warn'));
    }
    if (this.env.nh3Available) {
      const a: number = this.env.nh3;
      if (a > 25) {
        out.push(new RuleFact(`氨气${a.toFixed(1)}ppm超标`, 'alert'));
      } else if (a > 10) {
        out.push(new RuleFact(`氨气${a.toFixed(1)}ppm偏高`, 'warn'));
      }
    }
    return out;
  }

  /** 环境是否整体正常。模型输出的反向校验要用它（见 K1StatusChatService）。 */
  envIsNormal(): boolean {
    const facts: RuleFact[] = this.envFacts();
    for (let i = 0; i < facts.length; i++) {
      if (facts[i].level !== 'normal') {
        return false;
      }
    }
    return this.env.hasEnv;
  }

  // ── 给模型的事实串 ────────────────────────────────────────────────
  /**
   * 拼成一行极短的中文事实，直接放进 user 消息。
   *
   * 长度是被实测逼出来的：0.6B 的 prefill 约 20 tok/s，
   * 每多 10 个 token 就多 0.5 秒等待，而且长提示词会让它开始复述输入。
   * 所以这里只保留「有问题的那几项 + 视觉 + 叫声」，正常项压缩成「环境正常」。
   */
  buildFactLine(): string {
    const parts: string[] = [];

    // 环境：有异常才逐条列出，全正常就一句话
    if (!this.env.hasEnv) {
      parts.push('环境数据未接入');
    } else {
      const facts: RuleFact[] = this.envFacts();
      if (facts.length === 0) {
        parts.push(`温度${this.env.temp.toFixed(0)}℃湿度${this.env.hum.toFixed(0)}%正常`);
      } else {
        for (let i = 0; i < facts.length; i++) {
          parts.push(facts[i].text);
        }
      }
    }

    // 视觉：把「无目标 / 有人 / 有宠物」三种情况分开说，不能混成一句
    if (this.vision === null) {
      parts.push('视觉未接入');
    } else if (this.vision.pet.length > 0) {
      parts.push(`${this.vision.pet === 'dog' ? '犬' : '猫'}${this.vision.labelCn}${this.vision.durationMin}分钟`);
      if (this.vision.possibleCause.length > 0) {
        parts.push(this.vision.possibleCause);
      }
    } else if (this.vision.humanPresent) {
      parts.push('视野内只有人没有宠物');
    } else {
      parts.push('视野内没有宠物');
    }

    // 叫声：只在「值得提示」时进事实串，静音不算信息
    if (this.voiceSnap !== null && this.voiceSnap.noteworthy) {
      parts.push(`叫声${this.voiceSnap.clsCn}${this.voiceSnap.stateCn}`);
    }

    return parts.join('，');
  }

  /** 界面状态条：四路各自一行，明确标出「有数据 / 未接入」。 */
  statusRows(): string[] {
    const rows: string[] = [];

    // 环境
    if (this.env.hasEnv) {
      const nh3Text: string = this.env.nh3Available
        ? `${this.env.nh3.toFixed(1)}ppm` : '无传感器';
      // 光照单位是 lux，不是百分比。写成 % 会把 300lux 显示成「300%」。
      rows.push(`环境 ${this.env.temp.toFixed(1)}℃ · ${this.env.hum.toFixed(0)}% · 光照${this.env.light.toFixed(0)}lux · 氨气${nh3Text}`);
    } else {
      rows.push('环境 未接入');
    }

    // 视觉
    if (this.vision !== null) {
      const vs: VisionSnapshot = this.vision;
      // 不再用三元「不等于 pc 就是 K1」：板端没标 engine 时那种写法
      // 会把来路不明的推理结果说成 K1 推理。
      let engine: string = '推理来源未标注';
      if (vs.engine === 'k1') {
        engine = 'K1推理';
      } else if (vs.engine === 'pc') {
        engine = '电脑推理';
      }
      rows.push(`视觉 ${vs.subject} · ${vs.labelCn} · ${vs.fps.toFixed(1)}fps · ${engine}`);
    } else {
      rows.push('视觉 未接入');
    }

    // 叫声
    if (this.voiceSnap !== null) {
      // 成员变量在方法调用之间可能被改，ArkTS 不收窄跨调用的 null，
      // 所以先落到局部常量上再用，否则 strictNullChecks 报 undefined。
      const vs: VoiceSnapshot = this.voiceSnap;
      rows.push(`叫声 ${vs.shortTag()} · 置信${vs.conf.toFixed(2)}`);
    } else {
      rows.push('叫声 未接入');
    }

    return rows;
  }

  /**
   * 有没有任何一路**可判定**的真实数据。
   *
   * 注意叫声这条路：它是事件而不是状态。接口只要活着就会返回
   * cls=silence，这时候 voiceSnap 非 null 但一个信息量都没有。
   * 早期这里写 `voiceSnap !== null`，结果是「环境未接入 + 视觉未接入 +
   * 只有一条静音」也判定为「有数据」，模型照样被调用并给出「正常」，
   * 这是最容易在答辩时被追问的假分析路径。
   * 所以叫声必须 noteworthy（不是静音/噪声/人声，且置信够）才算。
   */
  hasAnyData(): boolean {
    if (this.env.hasEnv) {
      return true;
    }
    if (this.vision !== null) {
      return true;
    }
    if (this.voiceSnap !== null && this.voiceSnap.noteworthy) {
      return true;
    }
    return false;
  }

  /** 数据来源清单，用于回答气泡下的来源标注 */
  sourceSummary(): string {
    const on: string[] = [];
    if (this.env.hasEnv) {
      on.push('环境');
    }
    if (this.vision !== null) {
      on.push('视觉');
    }
    if (this.voiceSnap !== null) {
      on.push('叫声');
    }
    if (on.length === 0) {
      return '无数据源';
    }
    return on.join('+');
  }
}
