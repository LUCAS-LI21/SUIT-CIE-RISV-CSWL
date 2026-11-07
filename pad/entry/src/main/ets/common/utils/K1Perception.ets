// common/utils/K1Perception.ets
// 视觉通道：从 K1 的视觉服务拉最新一帧的识别结论（端口 11436）。
//
// 板端协议（与 k1-vision/vision_service.py 第 913-935 行逐字段对应）
//   GET http://<k1>:11436/v1/vision/latest
//   200 {"ok":true,"pet":"dog","label":"lying","action":"lying",
//        "action_conf":0.72,"basis":"体长速0.31/s 光流0.05 …",
//        "conf":0.87,"duration_sec":2520,"fps":1.97,
//        "model":"yolov8n.onnx","ts":…,"count":1,"all":[…],
//        "human":{"present":true,"count":2,…},"engine":"pc"}
//   200 {"ok":true,"pet":"","label":"absent",…}   视野内无宠物
//   503 {"ok":false,"reason":"…"}                 服务/模型未就绪
//
// 两条硬规矩（都是踩过的坑）
//   1. 板端只回英文标签，中文映射在端侧。板子不维护文案，改文案不用重刷板子。
//   2. 「画面里有人但没宠物」是**有效信息**，不能显示成「未知」。
//      K1 的 HumanWatcher 与宠物跟踪器是两条独立通道，只读 pet 会把它抹掉。

import { HttpService, HttpResponse } from './HttpService';
import { RemoteConfig } from '../config/RemoteConfig';
import { http } from '@kit.NetworkKit';

interface K1HumanInfo {
  present?: boolean;
  count?: number;
  conf?: number;
  bbox?: number[];
}

interface K1VisionResponse {
  ok?: boolean;
  pet?: string;
  label?: string;
  action?: string;
  action_raw?: string;
  action_conf?: number;
  basis?: string;
  // 漏检沿用标记：后端本帧没检测到目标，下面这些值是从上一次判定沿用的。
  // 缺少这个标记时前端会把沿用值当成本帧的真实结论，见 HomeTab 的处理。
  stale?: boolean;
  stale_age_ms?: number;
  conf?: number;
  duration_sec?: number;
  fps?: number;
  model?: string;
  ts?: number;
  count?: number;
  reason?: string;
  engine?: string;
  human?: K1HumanInfo;
}

/** 一帧视觉结论。字段全部有兜底，消费端不必判空。 */
export class VisionSnapshot {
  pet: string = '';            // 'cat' | 'dog' | ''（无目标）
  label: string = 'unknown';   // 板端英文标签
  labelCn: string = '识别中';   // 中文标签
  action: string = 'absent';   // 动作标签（当前与 label 同源）
  actionRaw: string = '';      // 稳定化之前的原始判定，排查用
  actionConf: number = 0;      // 动作置信度（三票投票得出）
  // true = 本帧没检测到目标，action/label 都是从上次判定沿用的。
  // 消费端必须区别对待：沿用值可以证明「宠物还在」，但不能证明「现在在做这个动作」。
  stale: boolean = false;
  staleAgeMs: number = 0;
  basis: string = '';          // 判定依据原文，答辩时可解释「凭什么这么判」
  conf: number = 0;            // 检测置信度（不是准确率）
  durationSec: number = 0;
  durationMin: number = 0;
  fps: number = 0;
  model: string = '';
  ts: number = 0;
  count: number = 0;
  humanPresent: boolean = false;
  humanCount: number = 0;
  possibleCause: string = '';
  // 这个行为值不值得调大模型。正常行为调了也是复述，白花 2 秒算力。
  needModel: boolean = false;
  // 感知行主体词：犬 / 猫 / 有人×2 / 无目标
  subject: string = '无目标';
  // 推理实际跑在哪：'k1' = 板端推理，'pc' = 电脑推理
  engine: string = '';

  /** 界面上那一行短描述，刻意压短以免折行 */
  shortLine(): string {
    return `${this.labelCn}·${Math.max(0, this.durationMin)}分`;
  }
}

export class K1Perception {
  private httpService: HttpService = HttpService.getInstance();

  // JSON.parse 不受类型系统保护：板端若把数字写成字符串，
  // 直接 .toFixed() 会抛异常并丢掉整帧，所以逐个字段做类型守卫。
  private static numOf(v: number | undefined, def: number): number {
    return typeof v === 'number' && Number.isFinite(v) ? v : def;
  }

  private static strOf(v: string | undefined, def: string): string {
    return typeof v === 'string' && v.length > 0 ? v : def;
  }

  /** 拉一次最新视觉结论。任何失败都返回 null，由上层决定显示什么。 */
  async fetch(): Promise<VisionSnapshot | null> {
    let resp: HttpResponse;
    try {
      resp = await this.httpService.request({
        url: RemoteConfig.visionLatestUrl(),
        method: http.RequestMethod.GET,
        headers: { 'Content-Type': 'application/json' },
        timeout: RemoteConfig.TIMEOUT_VISION
      });
    } catch (e) {
      console.warn('[K1Vision] 请求异常:', JSON.stringify(e));
      return null;
    }

    if (!resp.success || !resp.data) {
      // 注意：503 时 HttpService 只给 code，body 被丢掉了，
      // 所以这里拿不到 reason，只能记 code。别在这里假装知道原因。
      console.warn('[K1Vision] 不可达 code=', resp.code);
      return null;
    }

    try {
      const parsed: K1VisionResponse | null =
        JSON.parse(resp.data as string) as K1VisionResponse | null;
      if (parsed === null || parsed === undefined || parsed.ok !== true) {
        return null;
      }

      const snap: VisionSnapshot = new VisionSnapshot();
      snap.pet = K1Perception.strOf(parsed.pet, '');
      snap.label = K1Perception.strOf(parsed.label, 'unknown');
      snap.labelCn = K1Perception.labelToCn(snap.label);
      snap.action = K1Perception.strOf(parsed.action, snap.label);
      snap.actionRaw = K1Perception.strOf(parsed.action_raw, '');
      snap.actionConf = K1Perception.numOf(parsed.action_conf, 0);
      // 严格只认布尔 true：板端老版本没有这个字段（undefined），
      // 不能把「没有标记」当成「一定是实时」，但也不能误判成沿用帧。
      snap.stale = parsed.stale === true;
      snap.staleAgeMs = K1Perception.numOf(parsed.stale_age_ms, 0);
      snap.basis = K1Perception.strOf(parsed.basis, '');
      snap.conf = K1Perception.numOf(parsed.conf, 0);
      snap.durationSec = K1Perception.numOf(parsed.duration_sec, 0);
      // 不足 60 秒四舍五入会得 0，进提示词变成「趴卧0分钟」，
      // 0.6B 对「0 分钟」的反应是直接说空话。一律按下限 1 分钟算。
      snap.durationMin = Math.max(1, Math.round(snap.durationSec / 60));
      snap.fps = K1Perception.numOf(parsed.fps, 0);
      snap.model = K1Perception.strOf(parsed.model, '');
      snap.ts = K1Perception.numOf(parsed.ts, 0);
      snap.count = K1Perception.numOf(parsed.count, 0);
      // engine 不要给默认值 'k1'：板端没标来源时若默认成 k1，
      // 界面上就会出现「PC 推理也写 K1推理」的假标注。
      // 空串交给 PetDataHub 显示「推理来源未标注」，宁缺毋滥。
      snap.engine = K1Perception.strOf(parsed.engine, '');
      snap.humanPresent = parsed.human !== undefined && parsed.human.present === true;
      snap.humanCount = parsed.human !== undefined
        ? K1Perception.numOf(parsed.human.count, snap.humanPresent ? 1 : 0)
        : 0;
      snap.possibleCause = K1Perception.causeOf(snap.label, snap.durationMin);
      snap.needModel = K1Perception.needModelOf(snap.label, snap.durationMin);
      snap.subject = K1Perception.subjectOf(snap.pet, snap.humanPresent, snap.humanCount);
      return snap;
    } catch (e) {
      console.error('[K1Vision] 解析失败:', JSON.stringify(e));
      return null;
    }
  }

  /**
   * 板端英文标签 → 中文。
   * 标签集来自 vision_service 的行为判定树：12 个行为 + unknown，
   * 另有服务补的 absent（视野无宠物）。
   * 注意：这套算法**没有「抓挠」标签**，原因见 causeOf 的注释。
   */
  private static labelToCn(label: string): string {
    switch (label) {
      case 'lying': return '趴卧';
      case 'standing': return '站立';
      case 'sitting': return '坐着';
      case 'sleeping': return '睡觉';
      case 'walking': return '走动';
      case 'running': return '奔跑';
      case 'jumping': return '蹦跳';
      case 'playing': return '玩耍';
      case 'eating': return '进食';
      case 'coquettish': return '蹭人撒娇';
      case 'long_still': return '长时间静止';
      case 'absent': return '未检测到';
      default: return '识别中';
    }
  }

  private static petToCn(pet: string): string {
    if (pet === 'dog') {
      return '犬';
    }
    if (pet === 'cat') {
      return '猫';
    }
    return '无目标';
  }

  /** 感知行主体词。三种现场情况必须能区分开，否则界面会显得「识别没工作」。 */
  private static subjectOf(pet: string, humanPresent: boolean, humanCount: number): string {
    if (pet === 'dog' || pet === 'cat') {
      if (humanPresent && humanCount > 1) {
        return `${K1Perception.petToCn(pet)}+人×${humanCount}`;
      }
      return K1Perception.petToCn(pet);
    }
    if (humanPresent) {
      return humanCount > 1 ? `有人×${humanCount}` : '有人';
    }
    return '无目标';
  }

  /**
   * 行为 → 可能成因。
   *
   * 这一项是整条链路效果的关键：0.6B 只拿到「趴卧 42 分钟」时只会回
   * 「保持环境舒适」这类空话；补上「可能缺乏活动或身体不适」之后，
   * 才能给出「增加互动、观察精神」这种有指向的建议。
   *
   * ⚠️ 能力边界（答辩会被问，先说清楚）：
   *   这套行为判定是**基于检测框运动学特征的启发式规则**，不是行为分类器。
   *   · 没有「抓挠」—— 需要关键点检测或自训行为模型；
   *   · 「进食」只是「框偏扁 + 移动慢」的推断，没有食盆检测；
   *   · 「睡觉」只是「静止 + 稳定性阈值」，没有眼睛/呼吸检测。
   *   所以下面是「该类行为的常见可能」，不是诊断结论。
   *   这也正是为什么要让规则层判事实、模型只负责表达。
   */
  private static causeOf(label: string, durationMin: number): string {
    switch (label) {
      case 'lying':
        return durationMin >= 30 ? '可能缺乏活动或身体不适' : '正在休息';
      case 'long_still':
        return '长时间不动，可能身体不适或处于深睡';
      case 'sleeping':
        return '正在睡眠，属正常生理需求';
      case 'sitting':
        return '短暂休息或等待指令';
      case 'standing':
        return '短暂站立，可能在观察环境';
      case 'walking':
        return '正常活动，可能需要排泄或探索环境';
      case 'running':
        return '精力旺盛，活动量大';
      case 'jumping':
        return '情绪兴奋或希望引起注意';
      case 'playing':
        return '精神状态良好，正在自主玩耍';
      case 'eating':
        return '正常进食';
      case 'coquettish':
        return '寻求关注或撒娇，也可能皮肤瘙痒需要查看';
      case 'absent':
        return '当前视野内没有发现宠物';
      default:
        return '行为识别中';
    }
  }

  /** 该行为是否值得调用大模型。正常行为只放行规则文案，省一次 2 秒推理。 */
  private static needModelOf(label: string, durationMin: number): boolean {
    switch (label) {
      case 'long_still':
        return true;
      case 'lying':
        return durationMin >= 30;
      case 'sleeping':
        return durationMin >= 120;
      case 'running':
        return true;
      case 'standing':
        return durationMin >= 20;
      default:
        return false;
    }
  }
}
