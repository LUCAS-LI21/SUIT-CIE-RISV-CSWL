// common/utils/K1Voice.ets
// 听觉通道：拉 K1 上 petvoice_service（11439）的最新叫声判定。
//
// 与视觉是两条**并列**的通道，不是同一件事：
//   11436 视觉      —— 它在做什么
//   11439 叫声      —— 它在叫什么、可能是什么状态
//   11437 人声 ASR  —— 主人说了什么（与叫声识别互不替代）
//
// 板端协议
//   GET http://<k1>:11439/v1/voice/latest
//   200 {"ok":true,"cls":"bark","cls_cn":"吠叫","state":"alert","state_cn":"警戒",
//        "conf":0.8,"pet":"dog","advice":"它听到门外有动静…","source":"规则",
//        "repeat":2,"dur":0.44,"ts":…}
//
// 两个容易搞错的地方
//   1. 板端同时返回中文字段（cls_cn / state_cn），端侧不再维护映射表。
//      两处各写一份文案，正是「左边检测到人、右边写着无目标」那类 bug 的来源。
//   2. source 字段不是「假话」：它的语义是**建议来源**——"本地模型" 表示
//      这条建议走了 LLM，「规则」表示模板回退。它不是分类器来源，别当成假数据删掉。

import { HttpService, HttpResponse } from './HttpService';
import { RemoteConfig } from '../config/RemoteConfig';
import { http } from '@kit.NetworkKit';

interface K1VoiceResponse {
  ok?: boolean;
  cls?: string;
  cls_cn?: string;
  state?: string;
  state_cn?: string;
  conf?: number;
  pet?: string;
  advice?: string;
  source?: string;
  repeat?: number;
  dur?: number;
  ts?: number;
  cls_source?: string;
}

/** 叫声识别结果。全部字段有兜底，消费端不必判空。 */
export class VoiceSnapshot {
  cls: string = 'silence';
  clsCn: string = '';
  stateCn: string = '';
  conf: number = 0;
  advice: string = '';
  source: string = '规则';
  ts: number = 0;
  /** 同一类叫声在统计窗口内连续出现的次数（板端 StateMapper 统计，≥2 才算「反复叫」） */
  repeat: number = 1;
  /** 本次叫声持续时长（秒） */
  dur: number = 0;
  /** 是否值得上屏提示：静音 / 人声 / 噪声都不打扰用户 */
  noteworthy: boolean = false;

  /**
   * 一行短标签，直接上屏。
   *
   * ⚠️ 未走 noteworthy 的情况**不能一律写「安静」**：
   * 「有人说话」和「环境噪声」都是有效信息，写安静就是在说假话。
   * 这个字段会被首页状态条和聊天页底部状态条同时消费，改文案只改这一处。
   */
  shortTag(): string {
    if (!this.noteworthy) {
      if (this.cls === 'human') {
        return '人声，非宠物';
      }
      if (this.cls === 'noise') {
        return '环境噪声';
      }
      return '安静';
    }
    const rep: string = this.repeat >= 2 ? `×${this.repeat}` : '';
    return `${this.clsCn}${rep}·${this.stateCn}`;
  }
}

export class K1Voice {
  private httpService: HttpService = HttpService.getInstance();

  private numOf(v: number | undefined, def: number): number {
    return typeof v === 'number' && Number.isFinite(v) ? v : def;
  }

  private strOf(v: string | undefined, def: string): string {
    return typeof v === 'string' && v.length > 0 ? v : def;
  }

  /** 拉一次最新叫声结论。失败返回 null，不影响视觉与聊天。 */
  async fetch(): Promise<VoiceSnapshot | null> {
    let resp: HttpResponse;
    try {
      resp = await this.httpService.request({
        url: RemoteConfig.voiceLatestUrl(),
        method: http.RequestMethod.GET,
        headers: { 'Content-Type': 'application/json' },
        timeout: RemoteConfig.TIMEOUT_VOICE
      });
    } catch (e) {
      console.warn('[K1Voice] 请求异常:', JSON.stringify(e));
      return null;
    }

    if (!resp.success || !resp.data) {
      console.warn('[K1Voice] 不可达 code=', resp.code);
      return null;
    }

    try {
      const parsed: K1VoiceResponse | null =
        JSON.parse(resp.data as string) as K1VoiceResponse | null;
      if (parsed === null || parsed === undefined || parsed.ok !== true) {
        return null;
      }

      const out: VoiceSnapshot = new VoiceSnapshot();
      out.cls = this.strOf(parsed.cls, 'silence');
      out.clsCn = this.strOf(parsed.cls_cn, '');
      out.stateCn = this.strOf(parsed.state_cn, '');
      out.conf = this.numOf(parsed.conf, 0);
      out.advice = this.strOf(parsed.advice, '');
      out.source = this.strOf(parsed.source, '规则');
      out.ts = this.numOf(parsed.ts, 0);
      // repeat 兜底用 1 而不是 0：0 会拼出「吠叫×0」，无意义
      out.repeat = Math.max(1, this.numOf(parsed.repeat, 1));
      out.dur = Math.max(0, this.numOf(parsed.dur, 0));
      out.noteworthy = (out.cls !== 'silence' && out.cls !== 'noise' && out.cls !== 'human')
        && out.conf >= 0.5 && out.advice.length > 0;
      return out;
    } catch (e) {
      console.error('[K1Voice] 解析失败:', JSON.stringify(e));
      return null;
    }
  }
}
