// features/chat/viewmodel/ChatViewModel.ets
// 聊天页的视图模型：把「用户问题」和「四路真实感知数据」一起交给 K1 算力，
// 由板端 qwen3:0.6b 给出宠物当前状态的判断。
//
// 与旧实现的区别（旧版调 DeepSeek 云端）：
//   旧：system 是一套 999 字符的人设 → 实测 666 token → 0.6B prefill 33 秒超时，
//       而且云端要公网，板子上根本走不通，失败时只显示「网络不太好」。
//   新：一句短指令 + 一行数据事实 + 用户原话，走 K1 11438 网关。
//
// 一条界面规矩：**每条宠物消息都带来源标注**（模型 / 规则）。
//   规则兜底是模板文案，如果混在模型回答里显示，就等于把假数据当实时分析。

import { common } from '@kit.AbilityKit';
import { K1SpeechService, RecognitionResult } from '../services/K1SpeechService';
import { PetInfo } from '../services/DeepSeekService';
import { K1StatusChatService, StatusReply } from '../services/K1StatusChatService';
import { PetDataHub } from '../../../common/services/PetDataHub';

export interface ChatMessage {
  id: string;
  content: string;
  role: 'user' | 'pet';
  timestamp: number;
  // 来源标注：'model' 模型产出 / 'rule' 规则兜底 / 'nodata' 无数据拒绝作答。
  // 用户消息为空串。
  source?: string;
  // 本条回答**产生时**，网关自报的引擎标识（'k1' / 'local-ollama' / 'pc-mock' …）。
  //
  // 为什么必须随消息落盘，而不是渲染时再去读当前探测值：
  //   llmEngine 是 PetDataHub 每隔一段时间探测一次的**当前**状态。
  //   一旦链路切换（PC 替身 → 板端），所有历史气泡都会被重新标注成「K1 本地模型」，
  //   而那些回答其实是替身给的 —— 等于事后伪造数据来源。
  //   实测就发生过：一条 PC 替身答的「1.1s」气泡，在 K1 接通后被标成「K1 本地模型」。
  //   所以出处只能记在消息上，渲染时只读不改。
  engine?: string;
  // 本次回答耗时（毫秒），用于界面上标「K1 · 1.8s」
  elapsedMs?: number;
  // source='rule' 时细分兜底原因：'nopet'（画面无宠物，压根没请求模型）
  // / 'modelfail'（真请求过，模型不可用）。不区分就只能一律显示
  // 「模型未响应」，把「没去问」说成「问了没答」。
  ruleReason?: string;
  // source='kb' 时命中的知识库条目 id，界面上标出处用（如 cat_milk）
  kbId?: string;
}

@Observed
export class ChatViewModel {
  messages: ChatMessage[] = [];
  recognizingText: string = '';
  isRecording: boolean = false;
  isThinking: boolean = false;
  // 宠物基本信息，将在加载时更新
  petName: string = '旺财';
  petType: string = 'cat';
  petBirthDate: string = '';
  petGender: string = 'female';
  petBreedName: string = '';
  private speechService: K1SpeechService = new K1SpeechService();
  private statusChat: K1StatusChatService = new K1StatusChatService();
  // 自动现状分析的冷却时间戳。实测过没有它会出现死循环：
  // 页面每次显示都调 summarizeStatus → LLM 超时 → 消息没加上（messages 仍空）
  // → 下次显示再调 → 网关每次都要暂停视觉 → 视觉长期冻结、K1 被拖垮。
  // 所以失败后 60 秒内不重试；真正需要重来就点垃圾桶（会清零这个时间戳）。
  private lastSummarizeAt: number = 0;
  private initialized: boolean = false;
  onRecognized?: (text: string) => void;
  onMessageSent?: () => void; // 消息发送成功的回调

  // 初始化语音服务，现在需要传入 context
  async initSpeech(context: common.UIAbilityContext): Promise<boolean> {
    if (this.initialized) {
      return true;
    }

    const success = await this.speechService.init(context);
    if (success) {
      this.speechService.setCallbacks({
        onStart: () => {
          this.isRecording = true;
          this.recognizingText = '';
        },
        onResult: (result: RecognitionResult) => {
          this.recognizingText = result.text;
        },
        onComplete: (finalText: string) => {
          this.isRecording = false;
          if (finalText && finalText.trim().length > 0 && this.onRecognized) {
            this.onRecognized(finalText);
          }
          this.recognizingText = '';
        },
        onError: (code: number, msg: string) => {
          this.isRecording = false;
          this.recognizingText = '';
          console.error(`[ChatViewModel] 识别错误: ${code} - ${msg}`);
        }
      });
      this.initialized = true;
    }
    return success;
  }

  // 开始录音（需要外部传入 context 用于首次初始化）
  async startRecording(context: common.UIAbilityContext): Promise<void> {
    if (!this.initialized) {
      await this.initSpeech(context);
    }
    if (!this.isRecording && !this.isThinking) {
      await this.speechService.startListening();
    }
  }

  stopRecording(): void {
    if (this.isRecording) {
      this.speechService.stopListening();
    }
  }

  private currentPetInfo(): PetInfo {
    const info: PetInfo = {
      name: this.petName,
      type: this.petType,
      birthDate: this.petBirthDate,
      gender: this.petGender,
      breedName: this.petBreedName
    };
    return info;
  }

  async sendMessage(text: string): Promise<void> {
    if (!text || text.trim().length === 0) {
      return;
    }

    const userMessage: ChatMessage = {
      id: `user_${Date.now()}`,
      content: text,
      role: 'user',
      timestamp: Date.now()
    };
    this.messages = [...this.messages, userMessage];

    this.isThinking = true;

    // 先放一个空气泡，流式收到第一个 token 就往里填。
    // 不这么做的话，用户按下发送后要盯着「正在思考...」整整 1.3 秒才知道有没有反应；
    // 而 K1 网关实测首字只要 489 ms，这 800 ms 完全是前端白等的。
    const petId: string = `pet_${Date.now()}`;
    const placeholder: ChatMessage = {
      id: petId,
      content: '',
      role: 'pet',
      timestamp: Date.now(),
      source: 'model'
    };
    this.messages = [...this.messages, placeholder];

    try {
      const result: StatusReply = await this.statusChat.ask(text, this.currentPetInfo(),
        (acc: string) => {
          this.patchMessage(petId, acc);
        });
      // 用同一个 id 收尾：ForEach 的 key 不变，气泡不会「闪一下重建」。
      const finalMsg: ChatMessage = this.toPetMessage(result);
      finalMsg.id = petId;
      this.messages = this.messages.map((m: ChatMessage) => m.id === petId ? finalMsg : m);
      if (this.onMessageSent) {
        this.onMessageSent();
      }
    } catch (e) {
      console.error('[ChatViewModel] 回复失败:', JSON.stringify(e));
      const errMsg: ChatMessage = {
        id: petId,
        content: '（本地算力暂时不可用）请检查 K1 是否在线、11438 网关是否已启动。',
        role: 'pet',
        timestamp: Date.now(),
        source: 'rule'
      };
      this.messages = this.messages.map((m: ChatMessage) => m.id === petId ? errMsg : m);
    } finally {
      this.isThinking = false;
    }
  }

  /** 最后一条宠物消息是否已经有字了（用于决定要不要显示「正在思考...」） */
  lastReplyHasText(): boolean {
    const n: number = this.messages.length;
    if (n === 0) {
      return false;
    }
    const last: ChatMessage = this.messages[n - 1];
    return last.role === 'pet' && last.content.length > 0;
  }

  /** 把流式增量写进指定气泡。整条数组换引用，否则 @State 收不到变更。 */
  private patchMessage(id: string, content: string): void {
    let hit: boolean = false;
    const next: ChatMessage[] = this.messages.map((m: ChatMessage) => {
      if (m.id !== id) {
        return m;
      }
      hit = true;
      // 必须造新对象：@Builder 按值传参，改原对象不会触发重绘。
      const copy: ChatMessage = {
        id: m.id, content: content, role: m.role, timestamp: m.timestamp,
        source: m.source, engine: m.engine, elapsedMs: m.elapsedMs,
        ruleReason: m.ruleReason, kbId: m.kbId
      };
      return copy;
    });
    if (hit) {
      this.messages = next;
    }
  }

  /**
   * 进页面时主动做一次现状分析。
   *
   * 存在的理由：
   *   1. 用户点进「对话」是想知道宠物现在怎么样，不该先逼他打字；
   *   2. 它是一次**真实的 K1 推理**，能当场证明算力链路通不通，
   *      而不是靠一句写死的欢迎语假装正常。
   * @returns 是否产生了消息（产生了就需要外部保存历史）
   */
  /** 清空对话时调用：解除冷却，让用户可以立刻重新要一次分析。 */
  resetSummarizeCooldown(): void {
    this.lastSummarizeAt = 0;
  }

  async summarizeStatus(): Promise<boolean> {
    if (this.messages.length > 0) {
      return false;
    }
    const nowMs: number = Date.now();
    if (nowMs - this.lastSummarizeAt < 60000) {
      console.info('[ChatViewModel] 现状分析冷却中，跳过（距上次 '
        + Math.round((nowMs - this.lastSummarizeAt) / 1000) + 's）');
      return false;
    }
    this.lastSummarizeAt = nowMs;
    this.isThinking = true;
    try {
      const result: StatusReply = await this.statusChat.ask('它现在怎么样？', this.currentPetInfo());
      this.messages = [...this.messages, this.toPetMessage(result)];
      return true;
    } catch (e) {
      console.error('[ChatViewModel] 现状分析异常:', JSON.stringify(e));
      return false;
    } finally {
      this.isThinking = false;
    }
  }

  private toPetMessage(result: StatusReply): ChatMessage {
    // 出处在**这一刻**定格：此刻网关自报是谁，就记谁。
    // 之后链路怎么变都不再改动这条记录。
    const eng: string = result.source === 'model'
      ? PetDataHub.getInstance().llmEngine : '';
    const msg: ChatMessage = {
      id: `pet_${Date.now()}`,
      content: result.text,
      role: 'pet',
      timestamp: Date.now(),
      source: result.source,
      engine: eng,
      elapsedMs: result.elapsedMs,
      ruleReason: result.ruleReason,
      kbId: result.kbId
    };
    return msg;
  }

  clearHistory(): void {
    this.messages = [];
  }

  destroy(): void {
    this.speechService.destroy();
    this.initialized = false;
  }
}
