// features/chat/services/K1StatusChatService.ets
// 聊天走 K1 本地算力（11438 LLM 网关），数据来自 PetDataHub 汇聚的四路真实感知。
//
// ── 为什么不用原来的 DeepSeekService ────────────────────────────
// 两个原因，都不是偏好问题：
//   1. 云端接口要公网。3568 板子是网线直连 K1 的孤立网络，模拟器也不一定有
//      公网出口，实测必然失败，界面只会显示「网络不太好」——而网络其实没问题。
//   2. 赛题要的是「调用 K1 算力」。打云端等于把算力这半分送回去。
//
// ── 为什么不打 11434 直连 ollama ────────────────────────────────
// 11438 是网关，它在转发前会让视觉推理循环暂停、转发后恢复。
// 直连 11434 会绕过这个仲裁：视觉与 LLM 同时跑时，实测单次请求从 3.3 秒
// 劣化到 90 秒超时（瓶颈是内存带宽，不是核数，绑核无效）。
//
// ── 提示词为什么这么短 ──────────────────────────────────────────
// qwen3:0.6b 实测 prefill ≈20 tok/s、decode ≈10 tok/s。
// 666 token 的人设提示词要 33 秒，直接超时；而且 0.6B 扛不住多段角色设定，
// 会被「动作描述规范」这类段落带跑，输出变成复述设定本身。
// 所以：一句 20 token 的指令 + 一行数据事实 + 原问题，输出限 40 token。

import { HttpService, HttpResponse, HttpRequestConfig } from '../../../common/utils/HttpService';
import { RemoteConfig } from '../../../common/config/RemoteConfig';
import { PetDataHub, RuleFact } from '../../../common/services/PetDataHub';
import { VisionSnapshot } from '../../../common/utils/K1Perception';
import { PetInfo } from './DeepSeekService';
import { http } from '@kit.NetworkKit';

export interface StatusReply {
  ok: boolean;
  text: string;
  elapsedMs: number;
  promptTokens: number;
  outputTokens: number;
  errorMsg: string;
  // 这条回答的来源标注。界面上必须显示，不能让人以为规则文案是模型生成的。
  //   'model'    → K1 本地模型产出
  //   'kb'       → 本地知识库检索（有出处，strict 模式下是原文）
  //   'rule'     → 规则兜底（模型不可用/输出不可用）
  //   'nodata'   → 没有任何数据，拒绝作答
  source: string;
  // source='rule' 时进一步区分是哪一种兜底。不给这个字段，界面只能一律
  // 写「模型未响应」，而「画面里没宠物所以压根没请求模型」是另一回事——
  // 实测用户看到那句话的第一反应是「链路断了」，其实什么都没坏。
  //   'nopet'     → 画面无宠物，主动跳过模型（省 10~30 秒）
  //   'modelfail' → 真请求过，模型不可用/输出不可用
  ruleReason?: string;
  // source='kb' 时，命中的条目 id 与严格模式标记，便于界面标明出处
  kbId?: string;
  kbMode?: string;
  kbWarn?: string;
}

// 知识库 /ask 的返回体（字段来自 petkb/serve_http 实测）
interface KbCandidate {
  id: string;
  score: number;
  q: string;
}

interface KbReply {
  question: string;
  hit: boolean;
  score: number;
  id: string;
  cat: string;
  title: string;
  source_answer: string;
  mode: string;
  answer: string;
  warn?: string | null;
  candidates: KbCandidate[];
}

interface OAIMessage {
  role: string;
  content: string;
}

interface OAIChoice {
  index: number;
  message: OAIMessage;
  finish_reason: string;
}

interface OAIUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

interface OAIResponse {
  id: string;
  model: string;
  choices: OAIChoice[];
  usage: OAIUsage;
}

export class K1StatusChatService {
  private httpService: HttpService = HttpService.getInstance();
  private hub: PetDataHub = PetDataHub.getInstance();

  /**
   * 查本地知识库（K1:11435 GET /ask?q=）。
   *
   * 返回 null = 「不接管这次回答」，调用方继续走模型流程。三种情况都返回 null：
   *   1. 知识库不可达（超时/连接失败）——知识库挂了不该拖垮整条链路；
   *   2. 未命中（hit=false）——知识库会明确回「没找到，不推测」，
   *      但那句话不该甩给用户，这时该让模型结合实时数据答；
   *   3. 返回体解析失败。
   *
   * 为什么值得插在模型前面：这是全链路唯一「有出处」的回答来源。
   * 实测「猫能喝牛奶吗」→ mode=strict，直接回知识库原文并带 warn，
   * 而 0.6B 自己答这类问题时没有任何依据。
   */
  private async askKnowledge(question: string, startedAt: number): Promise<StatusReply | null> {
    const q: string = question.trim();
    if (q.length === 0) {
      return null;
    }
    let resp: HttpResponse;
    try {
      resp = await this.httpService.request({
        url: RemoteConfig.kbAskUrl(q),
        method: http.RequestMethod.GET,
        timeout: RemoteConfig.TIMEOUT_KB
      });
    } catch (e) {
      console.warn('[K1Chat] 知识库不可达，降级到模型:', JSON.stringify(e));
      return null;
    }
    if (!resp.success || !resp.data) {
      console.warn('[K1Chat] 知识库返回失败 code=', resp.code);
      return null;
    }
    let parsed: KbReply;
    try {
      parsed = JSON.parse(resp.data as string) as KbReply;
    } catch (e) {
      console.warn('[K1Chat] 知识库返回体解析失败');
      return null;
    }
    if (!parsed.hit || parsed.answer.length === 0) {
      return null;
    }
    console.info('[K1Chat] 知识库命中 id=', parsed.id, 'mode=', parsed.mode, 'score=', parsed.score);
    return {
      ok: true,
      text: parsed.answer,
      elapsedMs: Date.now() - startedAt,
      promptTokens: 0,
      outputTokens: 0,
      errorMsg: '',
      source: 'kb',
      kbId: parsed.id,
      kbMode: parsed.mode,
      kbWarn: parsed.warn ? parsed.warn : ''
    };
  }

  /**
   * 回答「宠物现在怎么样」这类问题。
   *
   * @param question 用户原话
   * @param petInfo  宠物档案（名字/类型），只用于让人设不空洞
   */
  async ask(question: string, petInfo: PetInfo,
            onDelta?: (accumulated: string) => void): Promise<StatusReply> {
    const startedAt: number = Date.now();

    // ── 第一优先：本地知识库 ──────────────────────────────────────
    // 「猫能喝牛奶吗」「猫体温39.8正常吗」这类问题有标准答案，不该让
    // qwen3:0.6b 去编（它会编得很像），也不该被「画面里没宠物」拦住——
    // 没有宠物的人照样可以问知识。命中就直接返回原文并标明出处，
    // 顺便省掉 hub.refresh() 的三次网络往返。
    // 未命中/服务不可用时静默降级，不影响后面的模型流程。
    const kb: StatusReply | null = await this.askKnowledge(question, startedAt);
    if (kb !== null) {
      return kb;
    }

    // 先把三路感知刷到最新。用户问的是「现在」，用 30 秒前的缓存回答是错的。
    await this.hub.refresh();

    // 一路数据都没有时，不许让模型凭空编 —— 这是最容易出假话的地方。
    if (!this.hub.hasAnyData()) {
      return {
        ok: true,
        text: '现在还没有采到数据：环境、视觉、叫声三路都没接入，我没法判断它的状态。'
          + '请先确认小板与摄像头在推送数据。',
        elapsedMs: Date.now() - startedAt,
        promptTokens: 0,
        outputTokens: 0,
        errorMsg: '',
        source: 'nodata'
      };
    }

    let facts: string = this.hub.buildFactLine();

    // 画面里没有宠物时：
    //   · 问的是「它现在怎么样」——必须短路。实测 0.6B 拿到「无目标」会整句
    //     复述提示词（返回「宠物健康助手。」），两次重试都一样，最后还是落规则兜底。
    //   · 问的是通用养宠知识——放开给模型。它答得简短但正确（实测），
    //     而且这条路才让「LLM 作为智能决策核心」真正跑起来。
    const vsNow: VisionSnapshot | null = this.hub.vision;
    const noPet: boolean = (vsNow === null || vsNow.pet.length === 0);
    if (noPet && this.isStatusQuestion(question)) {
      return {
        ok: true,
        text: this.ruleAnswer(true),
        elapsedMs: Date.now() - startedAt,
        promptTokens: 0,
        outputTokens: 0,
        errorMsg: '',
        source: 'rule',
        // 这里**没有请求过模型**，界面不能写「模型未响应」。
        ruleReason: 'nopet'
      };
    }
    if (noPet) {
      // 通用问答不带状态数据：给空事实，buildMessages 会切到「直接回答」提示词。
      facts = '';
    }

    const first: StatusReply = await this.requestOnce(question, facts, petInfo,
      RemoteConfig.LLM_TEMPERATURE, onDelta);
    if (first.ok) {
      return first;
    }

    // 只有「复读退化 / 空输出 / 与规则冲突」才会走到这里：
    // 换更低温度再采一次。0.3 仍跑偏说明是采样噪声，0.15 下同一 prompt
    // 基本会收敛。正常请求只跑一次，不付这份额外延迟。
    // 重试不再走流式：上一次已经往界面推过文字了，再推一遍会让气泡闪回。
    console.warn('[K1Chat] 首次失败(' + first.errorMsg + ')，低温重试');
    const second: StatusReply = await this.requestOnce(question, facts, petInfo, 0.15);
    if (second.ok) {
      second.elapsedMs = Date.now() - startedAt;
      return second;
    }

    // 模型彻底不可用：退回规则结论。**必须标成 rule**，否则界面上
    // 一句模板话会被当成模型分析结果，答辩时说不清。
    return {
      ok: true,
      text: this.ruleAnswer(),
      elapsedMs: Date.now() - startedAt,
      promptTokens: 0,
      outputTokens: 0,
      errorMsg: first.errorMsg,
      source: 'rule',
      ruleReason: 'modelfail'
    };
  }

  private async requestOnce(question: string, facts: string, petInfo: PetInfo,
                            temperature: number,
                            onDelta?: (accumulated: string) => void): Promise<StatusReply> {
    const startedAt: number = Date.now();
    const messages: OAIMessage[] = this.buildMessages(question, facts, petInfo);
    const streaming: boolean = onDelta !== undefined;

    const body: Record<string, Object> = {
      'model': RemoteConfig.LLM_MODEL,
      'messages': messages,
      // 只有给了 onDelta 才要流式：网关对 stream:true 走 SSE 边到边转发，
      // 首字实测 489 ms；不传则保持原来整包语义，老调用方行为不变。
      'stream': streaming,
      'temperature': temperature,
      'max_tokens': RemoteConfig.LLM_MAX_TOKENS
    };

    const cfg: HttpRequestConfig = {
      url: RemoteConfig.llmChatUrl(),
      method: http.RequestMethod.POST,
      headers: {
        'Content-Type': 'application/json',
        // 网关不校验 key，但部分实现要求该头存在，填占位值
        'Authorization': 'Bearer local'
      },
      body: JSON.stringify(body),
      timeout: RemoteConfig.TIMEOUT_LLM
    };

    let resp: HttpResponse;
    let rawAnswer: string = '';
    try {
      if (streaming) {
        // 流式失败（老固件没有 requestInStream / 网关不支持 chunked）时
        // 自动退回整包请求：多花 900 ms，但不会一条都答不出来。
        resp = await this.httpService.requestStream(cfg, (acc: string) => {
          if (onDelta !== undefined) {
            onDelta(acc);
          }
        });
        if (resp.success && resp.data !== null) {
          rawAnswer = resp.data as string;
        } else {
          console.warn('[K1Chat] 流式失败(' + resp.message + ')，退回整包请求');
          resp = await this.httpService.request(cfg);
        }
      } else {
        resp = await this.httpService.request(cfg);
      }
    } catch (e) {
      console.error('[K1Chat] 请求抛异常:', JSON.stringify(e));
      return {
        ok: false, text: '', elapsedMs: Date.now() - startedAt,
        promptTokens: 0, outputTokens: 0, errorMsg: '请求异常', source: 'rule'
      };
    }

    const elapsed: number = Date.now() - startedAt;

    if (!resp.success || !resp.data) {
      console.error('[K1Chat] 请求失败 code=', resp.code, 'msg=', resp.message);
      return {
        ok: false, text: '', elapsedMs: elapsed,
        promptTokens: 0, outputTokens: 0,
        errorMsg: `HTTP ${resp.code}`, source: 'rule'
      };
    }

    // 流式和整包在这里合流成同一个 rawContent，后面的校验逻辑只有一份。
    let rawContent: string = '';
    let promptTokens: number = 0;
    let outputTokens: number = 0;

    if (rawAnswer.length > 0) {
      rawContent = rawAnswer;
    } else {
      try {
        const parsed: OAIResponse = JSON.parse(resp.data as string) as OAIResponse;
        if (!parsed.choices || parsed.choices.length === 0) {
          return {
            ok: false, text: '', elapsedMs: elapsed,
            promptTokens: 0, outputTokens: 0, errorMsg: '响应无 choices', source: 'rule'
          };
        }
        rawContent = parsed.choices[0].message.content ?? '';
        promptTokens = parsed.usage ? parsed.usage.prompt_tokens : 0;
        outputTokens = parsed.usage ? parsed.usage.completion_tokens : 0;
      } catch (e) {
        console.error('[K1Chat] 解析失败:', JSON.stringify(e));
        return {
          ok: false, text: '', elapsedMs: elapsed,
          promptTokens: 0, outputTokens: 0, errorMsg: '响应解析失败', source: 'rule'
        };
      }
    }

    {
      const cleaned: string = this.cleanOutput(rawContent);

      if (cleaned.length === 0) {
        return {
          ok: false, text: '', elapsedMs: elapsed,
          promptTokens: promptTokens, outputTokens: outputTokens,
          errorMsg: '模型输出为空', source: 'rule'
        };
      }
      if (this.looksLikeLoop(cleaned)) {
        console.warn('[K1Chat] 检出复读退化:', cleaned);
        return {
          ok: false, text: '', elapsedMs: elapsed,
          promptTokens: promptTokens, outputTokens: outputTokens,
          errorMsg: '模型输出复读退化', source: 'rule'
        };
      }
      if (this.looksLikeLeak(cleaned)) {
        console.warn('[K1Chat] 输出复述提示词:', cleaned);
        return {
          ok: false, text: '', elapsedMs: elapsed,
          promptTokens: promptTokens, outputTokens: outputTokens,
          errorMsg: '模型输出复述提示词', source: 'rule'
        };
      }
      const conflict: string = this.checkConflict(cleaned);
      if (conflict.length > 0) {
        console.warn('[K1Chat] 输出与实测数据冲突:', cleaned, '原因:', conflict);
        return {
          ok: false, text: '', elapsedMs: elapsed,
          promptTokens: promptTokens, outputTokens: outputTokens,
          errorMsg: `模型输出与实测冲突（${conflict}）`, source: 'rule'
        };
      }

      console.info('[K1Chat] 成功', elapsed, 'ms:', cleaned);
      return {
        ok: true, text: cleaned, elapsedMs: elapsed,
        promptTokens: promptTokens, outputTokens: outputTokens,
        errorMsg: '', source: 'model'
      };
    }
  }

  /**
   * 组装提示词。
   *
   * 四条约束（都是实测调出来的，改动前先看清楚）：
   *   1. 数据必须在问题**前面**：0.6B 对序列末尾更敏感，问题放最后才答得准。
   *   2. 只给「有问题的那几项 + 视觉 + 叫声」，正常项压成一句，否则注意力被环境吸走。
   *   3. 明确要求「不要编造数据」：0.6B 见到半张表时会自行补全缺失项。
   *   4. 不写人设故事、不写示例。每多一个字都在增加等待。
   */
  private buildMessages(question: string, facts: string, petInfo: PetInfo): OAIMessage[] {
    const typeLabel: string = petInfo.type === 'dog' ? '狗' : '猫';
    const name: string = petInfo.name && petInfo.name.length > 0 ? petInfo.name : '宠物';
    // 提示词保持一句：0.6B 对多段指令会整句复述（实测），宁短勿长。
    // 无宠物的场景已在 ask() 里短路，这里不再需要「画面里没有宠物」的元指令。
    let rule: string = '宠物健康助手。依据给出的数据回答，不超过30字，只输出答案，不要编造数据。';
    if (facts.length === 0) {
      // 通用问答（「狗狗一天喂几次」这类）不需要事实约束，
      // 拿到空数据栏反而会让 0.6B 去复述「数据：」这几个字。
      rule = '宠物健康助手。直接回答用户的问题，不超过30字，只输出答案。';
    }
    const system: OAIMessage = {
      role: 'system',
      content: rule
    };
    // 问题截断：0.6B 对超长输入只会复述。30 字足够表达一个健康问题。
    const q: string = question.length > 30 ? question.substring(0, 30) : question;
    const user: OAIMessage = {
      role: 'user',
      content: facts.length > 0
        ? `宠物${typeLabel}${name}。数据：${facts}。问：${q}`
        : q
    };
    return [system, user];
  }

  /**
   * 这个问题是不是在问「宠物此刻的状态」。
   *
   * 这个判断决定要不要用视觉数据，进而决定「没有宠物时该不该问模型」。
   * 之前的实现只要 vision.pet 为空就一律短路，结果是：**实验室里 LLM 从来没被
   * 调用过**，界面上永远写着「规则兜底」，智能决策这一项等于没有。
   * 实测（probe_general_qa.py，2026-11-06）：
   *   「狗狗一天喂几次比较合适？」无宠物 → 「1-2次/天」       ✅ 可以放开
   *   「怎么让猫多喝水？」      无宠物 → 「提供充足的水」      ✅ 可以放开
   *   「我的猫现在怎么样？」    无宠物 → 「宠物健康助手。」    ❌ 复述提示词，必须短路
   * 所以闸门要卡在「问题类型」上，而不是「画面里有没有宠物」。
   */
  private isStatusQuestion(question: string): boolean {
    const keys: string[] = ['现在', '怎么样', '状态', '还好', '在干嘛', '在做什么',
      '正常吗', '有事吗', '情况', '没事吧', '在不在'];
    for (let i = 0; i < keys.length; i++) {
      if (question.indexOf(keys[i]) >= 0) {
        return true;
      }
    }
    return false;
  }

  /** 规则兜底回答。只用已经判出的事实，不添加任何推断。 */
  private ruleAnswer(noPetReason: boolean = false): string {
    const facts: RuleFact[] = this.hub.envFacts();
    const parts: string[] = [];
    for (let i = 0; i < facts.length; i++) {
      parts.push(facts[i].text);
    }
    if (this.hub.vision !== null) {
      parts.push(`当前${this.hub.vision.labelCn}`);
    }
    if (parts.length === 0) {
      // 这里以前写的是「数据显示各项正常」。那是**无依据的断言**：
      // 走到这个分支恰恰说明一条可用事实都没有，凭什么说正常。
      return '（本地模型暂时没响应）现在只有画面在更新，环境和叫声都还没采到，'
        + '暂时判不出状态，请先确认小板和麦克风在推送数据。';
    }
    // 两种来源必须分开说：无宠物是**设计上就不问模型**，不是模型出故障。
    // 写错会让人以为算力链路断了（实测踩过：画面没人或没宠物时一直显示「模型未响应」）。
    const prefix: string = noPetReason
      ? '（画面里没有宠物，按实测数据直接判断）'
      : '（本地模型暂时没响应，以下是规则结论）';
    return `${prefix}${parts.join('，')}。`;
  }

  /** 清掉 qwen3 可能带出的思考块与引导词 */
  private cleanOutput(raw: string): string {
    let s: string = raw;
    const thinkEnd: number = s.indexOf('<｜end▁of▁thinking｜>');
    if (thinkEnd >= 0) {
      s = s.substring(thinkEnd + 8);
    }
    s = s.trim();
    const prefixes: string[] = ['回答：', '回答:', '回复：', '回复:', '答案：', '答案:'];
    for (let i = 0; i < prefixes.length; i++) {
      if (s.startsWith(prefixes[i])) {
        s = s.substring(prefixes[i].length).trim();
      }
    }
    return s.replace(/\s+/g, ' ').trim();
  }

  /** 连续同一字符出现 5 次以上判为复读（覆盖「喵喵喵喵喵」「aaaaa」这类退化） */
  private looksLikeLoop(s: string): boolean {
    if (s.length < 5) {
      return false;
    }
    let run: number = 1;
    for (let i = 1; i < s.length; i++) {
      if (s.charAt(i) === s.charAt(i - 1)) {
        run++;
        if (run >= 5) {
          return true;
        }
      } else {
        run = 1;
      }
    }
    return false;
  }

  /**
   * 复述提示词检测。
   * 0.6B 偶尔会把指令原样当回复生成（「依据给出的数据回答」之类）。
   * 这些特征词只会出现在 system prompt 里，正常回答不会说「不超过30字」。
   */
  private looksLikeLeak(s: string): boolean {
    const metaWords: string[] = ['不超过30字', '只输出答案', '不要编造数据', '宠物健康助手', '依据给出的数据'];
    for (let i = 0; i < metaWords.length; i++) {
      if (s.indexOf(metaWords[i]) >= 0) {
        return true;
      }
    }
    return false;
  }

  /**
   * 一致性校验：规则说「正常」时，模型不该反过来说「过高/危险」。
   *
   * 只在前置条件成立时才校验：如果规则本身已经判出「温度偏高」，
   * 模型回答「建议降温」是正确答案，这时候拦下来就是 bug（早期踩过）。
   */
  private checkConflict(output: string): string {
    // 视觉侧优先拦：画面里根本没有宠物，输出却在评价它的状态。
    // 这条比环境冲突更严重 —— 它把「没看见」说成「看见了而且挺好」。
    // 实测放过一次：「视觉 无目标·未检测到」同时气泡写「当前宠物状态良好，正常」。
    // 只认「对着宠物下结论」的说法，不用单字「正常」，否则会误伤
    // 「环境正常，但看不到宠物」这种本来就正确的回答。
    const vs: VisionSnapshot | null = this.hub.vision;
    const petInView: boolean = vs !== null && vs.pet.length > 0;
    if (!petInView) {
      const praise: string[] = ['状态良好', '精神良好', '状态正常', '状态不错',
        '一切正常', '很健康', '身体健康', '状况良好', '精神不错'];
      for (let i = 0; i < praise.length; i++) {
        if (output.indexOf(praise[i]) >= 0) {
          return `视野内无宠物，输出却在评价其状态（「${praise[i]}」）`;
        }
      }
    }
    const facts: RuleFact[] = this.hub.envFacts();
    let hasAbnormal: boolean = false;
    for (let i = 0; i < facts.length; i++) {
      if (facts[i].level !== 'normal') {
        hasAbnormal = true;
      }
    }
    if (hasAbnormal) {
      return '';
    }
    if (!this.hub.envIsNormal()) {
      return '';
    }
    const badWords: string[] = ['过高', '偏高', '过低', '危险', '异常', '立即就医', '严重'];
    for (let i = 0; i < badWords.length; i++) {
      if (output.indexOf(badWords[i]) >= 0) {
        return `规则为正常，输出含「${badWords[i]}」`;
      }
    }
    return '';
  }
}
