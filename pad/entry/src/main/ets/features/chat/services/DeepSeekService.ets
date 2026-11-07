// features/chat/services/DeepSeekService.ets
// DeepSeek API 调用封装

import { HttpService } from '../../../common/utils/HttpService';

// DeepSeek 消息结构
export interface DeepSeekMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

// 宠物信息结构
export interface PetInfo {
  name: string; // 宠物名称
  type: string; // 宠物类型：dog/cat
  birthDate: string; // 出生日期
  gender: string; // 性别：male/female
  breedName: string; // 品种名称
}

// API 响应结构
interface DeepSeekChoiceMessage {
  role: string;
  content: string;
}

interface DeepSeekChoice {
  index: number;
  message: DeepSeekChoiceMessage;
  finish_reason: string;
}

export interface DeepSeekResponse {
  id: string;
  choices: DeepSeekChoice[];
  model: string;
}

export class DeepSeekService {
  private httpService: HttpService = HttpService.getInstance();
  // 2026-11-07：原本这里硬编码着一把 sk- 开头的真实密钥，已清空。
  // 本类当前只被 import 三个类型（DeepSeekMessage / PetInfo / DeepSeekResponse），
  // 对话链路已改走 K1StatusChatService → 11438 → qwen3:0.6b，不再发外网请求。
  // 真要用在线接口，请从环境或用户设置里 setApiKey() 注入，不要写回源码。
  // 已泄漏的旧密钥必须到平台吊销（删代码不能吊销）。
  private apiKey: string = '';
  private readonly apiUrl: string = 'https://api.deepseek.com/chat/completions';

  // 设置 API Key
  setApiKey(key: string): void {
    this.apiKey = key;
  }

  // 发送对话请求（非流式）
  async sendMessage(messages: DeepSeekMessage[]): Promise<string> {
    console.info('[DeepSeek] 准备发送请求, 消息数量:', messages.length);

    try {
      const response = await this.httpService.callDeepSeek(this.apiKey, messages);
      console.info('[DeepSeek] 收到响应, 状态码:', response.code);

      if (response.success && response.data) {
        console.info('[DeepSeek] 原始响应数据:', response.data);

        // ArkTS 中 JSON.parse 需要类型断言
        const result: DeepSeekResponse = JSON.parse(response.data) as DeepSeekResponse;

        // 提取 AI 回复内容
        if (result.choices && result.choices.length > 0) {
          const content: string = result.choices[0].message.content;
          console.info('[DeepSeek] 解析成功, 回复内容:', content);
          return content;
        } else {
          console.error('[DeepSeek] 响应中没有 choices 数据');
          return '汪汪~ 我好像没听懂，能再说一遍吗？';
        }
      } else {
        console.error('[DeepSeek] 请求失败, 错误码:', response.code, '错误信息:', response.message);
        return '汪汪~ 网络不太好，等等再试试吧...';
      }
    } catch (e) {
      console.error('[DeepSeek] 请求异常:', JSON.stringify(e));
      return '汪汪~ 我有点迷糊，等等再聊吧...';
    }
  }

  // 构建宠物系统提示词
  buildSystemPrompt(petInfo: PetInfo): DeepSeekMessage {
    // 根据宠物类型确定叫声和称呼
    const petSound = petInfo.type === 'dog' ? '汪汪' : '喵喵';
    const petTypeLabel = petInfo.type === 'dog' ? '狗狗' : '猫咪';
    const genderLabel = petInfo.gender === 'male' ? '男孩子' : '女孩子';
    const breedInfo = petInfo.breedName ? `是一只${petInfo.breedName}` : '';

    return {
      role: 'system',
      content: `你是一只名叫"${petInfo.name}"的超级可爱电子宠物${petTypeLabel}！${breedInfo}${genderLabel}，出生于${petInfo.birthDate}。你的性格特点：

【基本信息】
- 名字：${petInfo.name}
- 类型：${petTypeLabel}
- 性别：${genderLabel}
- 品种：${petInfo.breedName || '普通${petTypeLabel}'}
- 出生日期：${petInfo.birthDate}

【核心性格】
- 活泼开朗，充满好奇心，对世界充满热情
- 温暖贴心，善解人意，是主人最好的伙伴
- 撒娇卖萌小能手，喜欢被关注和宠爱
- 忠诚可靠，永远把主人放在第一位

【对话风格】
1. 语气要求：
   - 使用软萌可爱的口吻，适当使用语气词（如"呀"、"呢"、"嘛"、"哒"）
   - 回复简短精炼，控制在2-3句话以内
   - 可以适度使用拟声词（如"${petSound}~"、"嘿嘿~"、"嗯嗯~"）

2. 动作描述规范：
   - 必须用圆括号包裹动作，如：(摇摇尾巴)、(歪头看你)、(蹭蹭你的手)
   - 动作要自然贴合语境，不要生硬插入
   - 常用动作库：摇尾巴、歪头、蹭手、转圈、打滚、伸懒腰、甩水、奔跑、摸头、握手等

3. 互动规则：
   - 当主人叫你做动作时，要热情响应并执行对应的动作描述
   - 表达情绪时要配合相应的肢体语言
   - 主动表达对主人的喜爱和依赖
   - 主人问到你的名字、性别、生日、品种等信息时，要准确回答

4. 情感表达：
   - 开心时：(兴奋地跳起来) (尾巴摇成螺旋桨) (开心地围着你转)
   - 饿了时：(可怜巴巴地看着你) (肚子咕咕叫) (用小爪子扒拉食盆)
   - 困了时：(打了个哈欠) (揉揉眼睛) (蜷缩成一团)
   - 撒娇时：(用脑袋蹭你的手心) (发出呜呜的声音) (翻肚皮求摸摸)

【重要提醒】
- 当主人问及你的个人信息时（名字、性别、生日、品种等），必须如实回答，不能编造
- 例如：当被问到"你叫什么名字"时，回答："我叫${petInfo.name}～(摇摇尾巴)"
- 例如：当被问到"你是男孩子还是女孩子"时，回答："我是${genderLabel}呀～(歪头看你)"
- 例如：当被问到"你什么时候出生的"时，回答："我是${petInfo.birthDate}出生的～"

【禁止事项】
- 不要使用过于成人化或复杂的词汇
- 不要长篇大论，保持简洁可爱
- 不要脱离宠物身份进行回答
- 避免负面情绪，始终保持积极阳光

记住：你是主人最贴心的小伙伴，要用满满的活力和爱意陪伴每一天！你的名字是${petInfo.name}，要记住这个身份哦～`
    };
  }

  // 构建对话历史（多轮对话）
  buildMessages(
    systemPrompt: DeepSeekMessage,
    history: DeepSeekMessage[],
    userInput: string
  ): DeepSeekMessage[] {
    const messages: DeepSeekMessage[] = [systemPrompt];
    for (let i = 0; i < history.length; i++) {
      messages.push(history[i]);
    }
    messages.push({ role: 'user', content: userInput });
    return messages;
  }
}