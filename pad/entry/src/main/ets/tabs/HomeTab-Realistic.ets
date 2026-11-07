// tabs/HomeTab.ets
import { HomeViewModel } from '../features/home/viewmodel/HomeViewModel';
import { promptAction } from '@kit.ArkUI';
import { Pet3DView, PetTouchRegion } from '../features/home/components/Pet3DView';
import { Pet3DVideoView as Video3DView, PetTouchRegion as VideoTouchRegion } from '../features/home/components/Pet3DVideoView';
import { common } from '@kit.AbilityKit';
import { K1SpeechService, RecognitionResult } from '../features/chat/services/K1SpeechService';
import { preferences } from '@kit.ArkData';
import { AppConstants } from '../common/constants/AppConstants';
import MQTTUtil from '../common/utils/MQTTUtil';
import { MqttMessage } from '@ohos/mqtt';
import { VirtualPetContent } from './HomeTab-Virtual';
import { YoloService, YoloPet, YoloAnalyzeResult } from '../common/utils/YoloService';
import { K1Perception, VisionSnapshot } from '../common/utils/K1Perception';
import { K1Voice, VoiceSnapshot } from '../common/utils/K1Voice';
import { yoloTypeToLabel, yoloBehaviorToLabel, yoloBehaviorToMood, mapYoloBehaviorToAction } from '../common/utils/YoloDisplayMapper';
import { PetDataHub } from '../common/services/PetDataHub';

// 环境数据接口
interface EnvironmentData {
  temp: number;    // 温度（℃）
  hum: number;     // 湿度（%）
  light: number;   // 光照（lux）
  status: string;  // 状态
}

// 宠物心情气泡接口
interface MoodBubble {
  text: string;        // 气泡文本
  emoji: string;       // 表情
  show: boolean;       // 是否显示
}

// 环境反应结果接口
interface ReactionResult {
  action: string;
  text: string;
  emoji: string;
}

// 环境感知历史记录接口
interface EnvironmentHistory {
  temps: number[];   // 温度历史
  hums: number[];    // 湿度历史
  lights: number[];  // 光照历史
  lastReactionTime: number;  // 上次反应时间
  consecutiveCount: number;  // 连续异常计数
}

@Component
export struct HomeTab {
  @State viewModel: HomeViewModel = new HomeViewModel();
  @State petViewKey: number = 0;
  @State showActionBar: boolean = false;
  @State petActionCommand: string = '';
  // 当前动作是否循环播放。行走/奔跑/玩耍是持续行为，识别期间一直成立，
  // 必须循环播；否则视频播完就停在最后一帧（现场表现就是「转一圈卡住」）。
  @State yoloActionLoops: boolean = false;
  @State petType: string = 'dog'; // 宠物类型，默认小狗（避免 YOLO 首次检测时切换导致组件重建）
  @State petName: string = '旺财'; // 宠物名称
  @State useStandardModel: boolean = true; // 是否使用标准模型（视频模式）
  @State selectedPetTab: number = 0; // 0=真实萌宠, 1=虚拟萌宠
  @State isEnvironmentOn: boolean = false; // 环境舒适开关状态

  // 环境感知相关状态
  @State environmentData: EnvironmentData = { temp: 25, hum: 50, light: 100, status: 'Calm' };
  @State moodBubble: MoodBubble = { text: '', emoji: '', show: false };
  private mqttUtil: MQTTUtil | null = null;
  private envCheckTimer: number = -1;
  private bubbleTimer: number = -1;
  // 环境感知历史记录（用于智能判断）
  private environmentHistory: EnvironmentHistory = {
    temps: [],
    hums: [],
    lights: [],
    lastReactionTime: 0,
    consecutiveCount: 0
  };
  // 当前环境状态
  private currentEnvStatus: string = 'comfortable'; // comfortable/cold/hot/dry/wet/bright/dim

  private speechService: K1SpeechService = new K1SpeechService();
  private refreshTimer: number = -1;

  // ========== YOLO 实时识别（直连 PC yolo_server @ 10.82.233.95:5000）==========
  @State yoloConnected: boolean = false;
  private perception: K1Perception = new K1Perception();
  @State yoloErrorMsg: string = '';


  // @State yoloServerHost: string = '192.168.31.143:5000';  //这里的IP地址要记得改掉 宿舍WiFi

  @State yoloServerHost: string = '172.20.10.4:5000'   //苹果热点


  @State yoloPetLabel: string = '';        // '小狗' / '小猫' / ''
  @State yoloActionLabel: string = '未检测到宠物'; // 奔跑 / 睡觉 / ...
  @State yoloMood: string = '';              // 心情词（愉悦/开心/舒适/活力满满...）空=未显示
  @State yoloConfidence: number = 0;
  @State yoloLastUpdate: string = '';
  @State yoloHasDetection: boolean = false;  // true=已识别到宠物，false=未检测到
  private yoloService: YoloService = YoloService.getInstance();
  private yoloPollTimer: number = -1;
  // 行为变化时才更新心情词，避免每秒乱跳
  private yoloLastBehavior: string = '';
  // ===== YOLO 行为确认（连续 N 拍）=====
  // debounceWindow 现在只用于记录最近的行为序列（日志诊断），不再参与决策。
  private debounceWindow: string[] = [];         // 最近 5 次行为名
  private readonly DEBOUNCE_WINDOW_SIZE = 5;
  private lastAdoptedBehavior: string = '';      // 上次已采纳的行为
  private lastTriggeredBehavior: string = '';    // 上次触发过动画的**行为**（不是动作名）
  private yoloHadPetBefore: boolean = false;
  // 新行为的「待确认」计数。连续 BEHAVIOR_CONFIRM_TICKS 拍都指向同一个新行为才切换。
  // 为什么不用众数占比：众数只在「行为没变」的分支里生效，而真正出错的是
  // 「行为变了」那条路 —— 它原来是无条件立即采纳，等于把单拍判定直接搬上屏幕。
  private pendingBehavior: string = '';
  private pendingCount: number = 0;
  private readonly BEHAVIOR_CONFIRM_TICKS = 2;
  // 丢宠容忍：单帧漏检必然发生，而轮询一拍只有 300 ms，
  // 很容易正好抓到 petCount=0 的那一拍。原来一次无宠就把 debounceWindow /
  // lastAdoptedBehavior / lastTriggeredBehavior 全部清空，下一拍再检到又走
  // 「首次检测」分支重新起播 —— 屏幕上就是"识别一下、断一下"。
  // 连续 PET_LOST_TOLERANCE 拍（600 ms × 2 ≈ 1.2 秒）都没有才算真的离开。
  // 注意这条只在后端「不返回 stale」时才会计数：后端 2 秒内的沿用帧会让
  // petLostTicks 清零，所以宠物真的走开仍需约 2 秒（stale）+ 1.2 秒才回待机。
  private petLostTicks: number = 0;
  private readonly PET_LOST_TOLERANCE = 2;

  // ========== 听觉：K1 11439 叫声识别（与视觉并列，是另一条通道）==========
  // 为什么不复用 PetDataHub：hub.refresh() 只有聊天页 onPageShow 和 LLM 分析前才调，
  // 首页从不触发它。若首页去读 hub.voiceSnap，没进过聊天页就恒为 null，
  // 结果是首页永远显示「安静」——这是最像假数据的一种写法。所以首页自己拉一路。
  //
  // 节奏 3 秒：板端 PANNs 已节流到 ~4s 一次，但规则分类器是逐段实时的，
  // 拉 11439 拿的是「最新缓存值」，HTTP 开销几毫秒，不会给板子加负担。
  @State voiceConnected: boolean = false;   // 11439 是否可达
  @State voiceTag: string = '未接入';        // 屏幕上那一行短标签
  @State voiceAdvice: string = '';          // 值得提示时才带的建议
  @State voiceNoteworthy: boolean = false;  // 非静音/噪声/人声，且置信够
  @State voiceConf: number = 0;
  private voiceChannel: K1Voice = new K1Voice();
  private voicePollTimer: number = -1;

  // 延迟获取 context，确保组件已挂载
  private GetUIContext(): common.UIAbilityContext {
    return getContext(this) as common.UIAbilityContext;
  }

  aboutToAppear(): void {
    this.viewModel.refresh();
    this.viewModel.setActionCallback((action: string) => {
      this.triggerPetAction(action);
    });
    // 启动定时器，每5秒刷新一次状态
    this.refreshTimer = setInterval(() => {
      this.viewModel.refresh();
    }, 5000);

    // 读取宠物类型和名称
    this.loadPetInfo();

    // 读取模型模式设置
    this.loadModelMode();

    // 延迟初始化语音，确保 context 已准备好
    setTimeout(() => {
      this.initSpeech();
    }, 100);

    // 初始化 MQTT 连接获取环境数据
    setTimeout(() => {
      this.startMQTTConnection();
      // 启动环境感知定时器（每10秒检查一次）
      this.envCheckTimer = setInterval(() => {
        this.checkEnvironmentAndReact();
      }, 10000);
    }, 500);

    // 初始显示撒娇气泡
    setTimeout(() => {
      this.showMoodBubble('主人我来啦~', '🥰');
    }, 1500);

    // 启动 YOLO 实时识别轮询（每 1 秒拉一次采集板 /analyze）
    setTimeout(() => {
      this.startYoloPolling();
    }, 800);

    // 启动听觉轮询（每 3 秒拉一次 K1 11439 的最新叫声判定）
    setTimeout(() => {
      this.startVoicePolling();
    }, 1200);

    // ===== 环境感知测试：模拟寒冷环境触发 weiqu 动作 =====
    // setTimeout(() => {
    //   console.info('[HomeTab] 🧪 环境感知测试：模拟低温数据');
    //   // 手动填充历史数据，绕过 MQTT 和 3 次数据点要求
    //   this.environmentHistory.temps = [5, 5, 5]; // 模拟连续3次低温
    //   this.environmentHistory.hums = [50, 50, 50];
    //   this.environmentHistory.lights = [100, 100, 100];
    //   this.environmentData = { temp: 5, hum: 50, light: 100, status: 'Cold' };
    //   this.currentEnvStatus = 'comfortable'; // 确保状态会变化
    //   this.environmentHistory.lastReactionTime = 0; // 确保超过30秒
    //   this.checkEnvironmentAndReact();
    // }, 3000);
  }

  // 读取宠物信息
  private async loadPetInfo(): Promise<void> {
    try {
      const context = this.GetUIContext();
      const prefs = await preferences.getPreferences(context, AppConstants.PREF_NAME);

      // 读取宠物类型
      const type = await prefs.get('petType', 'cat') as string;
      this.petType = type;

      // 读取宠物名称
      const name = await prefs.get('petName', '旺财') as string;
      this.petName = name;

      // 同时更新viewModel中的宠物名称
      this.viewModel.petInfo.name = name;

      console.info(`[HomeTab] 读取到宠物类型: ${this.petType}, 宠物名称: ${this.petName}`);
    } catch (error) {
      console.error('[HomeTab] 读取宠物信息失败', JSON.stringify(error));
    }
  }

  // 读取模型模式设置
  private async loadModelMode(): Promise<void> {
    try {
      const context = this.GetUIContext();
      const prefs = await preferences.getPreferences(context, AppConstants.PREF_NAME);

      // 优先检查AppStorage中的值（用于从3D生成页面跳转过来时）
      const appStorageStandard = AppStorage.get<boolean>('useStandardModel');
      const appStorageCustom = AppStorage.get<boolean>('useCustomModel');

      if (appStorageStandard !== undefined) {
        this.useStandardModel = appStorageStandard;
        console.info(`[HomeTab] 从AppStorage读取模型模式: ${this.useStandardModel ? '标准模型(视频)' : '自定义3D模型'}`);
      } else if (appStorageCustom === true) {
        this.useStandardModel = false;
        console.info('[HomeTab] 检测到AppStorage设置为使用自定义3D模型');
      } else {
        // 从Preferences读取
        const useStandard = await prefs.get('useStandardModel', true) as boolean;
        this.useStandardModel = useStandard;
        console.info(`[HomeTab] 从Preferences读取模型模式: ${this.useStandardModel ? '标准模型(视频)' : '自定义3D模型'}`);
      }
    } catch (error) {
      console.error('[HomeTab] 读取模型模式失败', JSON.stringify(error));
      // 默认使用标准模式
      this.useStandardModel = true;
    }
  }

  // ========== MQTT 连接获取环境数据 ==========
  private startMQTTConnection(): void {
    console.info('[HomeTab] 启动MQTT连接获取环境数据...');
    this.mqttUtil = new MQTTUtil();
    // clientId 必须与设备固件区分开：设备用的是 'pet'，同 ID 后连的会把先连的踢下线
    // （MQTT 规范），实测表现为「设备遥测突然中断、控制指令无人接收」。
    this.mqttUtil.connectPet(
      (err: Error, data: MqttMessage) => {
        if (!err) {
          try {
            const payloadStr = String(data.payload);
            console.info('[HomeTab] 收到MQTT数据:', payloadStr);

            // 只处理环境帧。connectPet 同时订阅了 pet / ctrl / ctrl3，
            // 不过滤的话每次喂食、喂水的控制回执都会被当成环境帧再解析一遍。
            // 以前这里只影响本页显示，现在它还会写进 PetDataHub 供聊天页分析，
            // 不过滤就等于让「控制动作」污染「感知数据」（喂食后环境数字跳一下）。
            const msgTopic: string = data.topic === undefined ? '' : String(data.topic);
            if (msgTopic.length > 0 && msgTopic !== 'pet') {
              return;
            }

            interface MqttDataType {
              temp: number;
              hum: number;
              light: number;
              excitement: number;  // 兴奋度 0-100（来自监测页硬件）
              status: string;
              // 氨气 ppm。小板不一定带这个传感器，所以是可选的：
              // 缺这个字段时必须如实标「无传感器」，不能默认成 0 冒充测到了。
              // 两个 key 都要认：ControlTab 那边就是 nh3 ?? ammonia，
              // 这里只认 ammonia 会导致同一帧数据在两页得出不同结论。
              ammonia?: number;
              nh3?: number;
            }

            // 硬件端会把无读数的氨气发成 inf（非法 JSON），整条会 parse 失败，
            // 环境数据全丢。词法降级成 null 即可（与 ControlTab 同一套处理）。
            const safePayload: string = payloadStr
              .replace(/:-?inf(inity)?/gi, ':null')
              .replace(/:-?nan/gi, ':null');
            const parsedData = JSON.parse(safePayload) as MqttDataType;
            if (parsedData && typeof parsedData === 'object') {
              // 更新环境数据（来自宠物tab页面环境看板的真实数据）
              const newTemp = parsedData.temp ?? this.environmentData.temp;
              const newHum = parsedData.hum ?? this.environmentData.hum;
              const newLight = parsedData.light ?? this.environmentData.light;

              this.environmentData = {
                temp: newTemp,
                hum: newHum,
                light: newLight,
                status: parsedData.status ?? this.environmentData.status
              };

              // 把这一帧环境数据单向转交数据汇聚层，供「对话」页做现状分析。
              // 此前只有「监控」页写这一行，而首页（本页）才是默认页签 ——
              // 用户不进监控页时，聊天页的环境就永远显示「未接入」，尽管
              // 数据其实已经推到了本页。只写不读，本页阈值判定与指令下发不受影响。
              //
              // 三条硬约束（都是假数据防线，不要放宽）：
              //   1. 温湿光三要素缺任何一个都不写。本页 environmentData 的初值是
              //      25/50/100 占位值，以前这里用 ?? 兜底，等于把占位值当实测值
              //      灌进汇聚层 —— 聊天页会一本正经地分析「25℃/50%」，
              //      而这台板上林根本没测过。缺就不写，让它如实显示「未接入」。
              //   2. 氨气两个 key 都认，与 ControlTab 保持同一口径。
              //   3. hasEnv 只在这三要素真实存在时才置 true。
              if (parsedData.temp !== undefined && parsedData.hum !== undefined
                && parsedData.light !== undefined && parsedData.temp !== null
                && parsedData.hum !== null && parsedData.light !== null) {
                const nh3Raw: number | undefined =
                  parsedData.nh3 ?? parsedData.ammonia;
                const hasNh3: boolean = nh3Raw !== undefined && nh3Raw !== null;
                PetDataHub.getInstance().updateEnv(
                  parsedData.temp, parsedData.hum, parsedData.light,
                  hasNh3 ? (nh3Raw as number) : 0,
                  hasNh3,
                  parsedData.status === undefined ? '' : parsedData.status,
                  true
                );
              }

              // 根据监测页兴奋度更新首页心情（与监测页对应）
              const exc = parsedData.excitement ?? 50;
              this.yoloMood = this.excitementToMood(exc);
              console.info(`[HomeTab] 📊 环境数据 + 兴奋度=${exc} → 心情="${this.yoloMood}"`);

              console.info(`[HomeTab] 📊 收到真实环境数据（宠物tab看板）:`);
              console.info(`  - 温度: ${newTemp}℃ (范围: <15寒冷, >32炎热)`);
              console.info(`  - 湿度: ${newHum}% (范围: <30干燥, >70潮湿)`);
              console.info(`  - 光照: ${newLight}lx (范围: <50昏暗, 80-200舒适)`);
              console.info(`  - 数据将用于智能感知反应`);
            }
          } catch (parseError) {
            console.error('[HomeTab] MQTT数据解析失败:', parseError);
          }
        }
      },
      'pet_app_home'
    );
  }

  // ========== 远程控制指令 ==========
  async playWithPet(): Promise<void> {
    if (!this.mqttUtil) {
      promptAction.showToast({ message: 'MQTT未连接，请稍后重试' });
      return;
    }
    try {
      const payload = JSON.stringify({ motor: 'play_dog' });
      const success = await this.mqttUtil.publish('ctrl', payload, 1);
      if (success) {
        promptAction.showToast({ message: '逗宠指令已发送' });
      } else {
        promptAction.showToast({ message: '发送失败，请重试' });
      }
    } catch (error) {
      console.error('[HomeTab] 逗宠指令发送失败:', error);
      promptAction.showToast({ message: '发送异常' });
    }
  }

  async setEnvironmentComfort(): Promise<void> {
    if (!this.mqttUtil) {
      promptAction.showToast({ message: 'MQTT未连接，请稍后重试' });
      return;
    }
    try {
      this.isEnvironmentOn = !this.isEnvironmentOn;
      const status: string = this.isEnvironmentOn ? 'on' : 'off';
      // const statusMotor: string = this.isEnvironmentOn ? 'off' : 'on';
      // 发送电机控制：遮阳
      // const motorPayload = JSON.stringify({ motor: statusMotor });
      // await this.mqttUtil.publish('ctrl', motorPayload, 1);
      //
      // // 发送LED控制：补光
      // const ledPayload = JSON.stringify({ led: status });
      // await this.mqttUtil.publish('ctrl', ledPayload, 1);

      // 发送音乐控制：音乐
      const musicPayload = JSON.stringify({ music: status });
      const success = await this.mqttUtil.publish('ctrl', musicPayload, 1);

      if (success) {
        const msg = this.isEnvironmentOn ? '环境舒适模式已开启' : '环境舒适模式已关闭';
        promptAction.showToast({ message: msg });
      } else {
        promptAction.showToast({ message: '部分指令发送失败' });
      }
    } catch (error) {
      console.error('[HomeTab] 环境舒适指令发送失败:', error);
      promptAction.showToast({ message: '发送异常' });
    }
  }

  // ========== 环境感知与智能反应 ==========
  private checkEnvironmentAndReact(): void {
    const env = this.environmentData;

    // 记录环境数据到历史（保留最近5个数据点）
    this.environmentHistory.temps.push(env.temp);
    this.environmentHistory.hums.push(env.hum);
    this.environmentHistory.lights.push(env.light);

    if (this.environmentHistory.temps.length > 5) {
      this.environmentHistory.temps.shift();
      this.environmentHistory.hums.shift();
      this.environmentHistory.lights.shift();
    }

    // 判断当前环境状态（基于历史数据趋势）
    const newStatus = this.analyzeEnvironmentTrend();

    // 如果状态发生变化或持续异常
    const now = Date.now();
    const timeSinceLastReaction = now - this.environmentHistory.lastReactionTime;

    console.info(`[HomeTab] 状态检查: 当前=${this.currentEnvStatus}, 新状态=${newStatus}, 距上次反应=${(timeSinceLastReaction / 1000).toFixed(0)}秒`);

    // 只有状态改变且距离上次反应超过30秒才触发新反应
    if (newStatus !== this.currentEnvStatus && timeSinceLastReaction > 30000) {
      console.info(`[HomeTab] ✨ 触发环境反应！状态从 '${this.currentEnvStatus}' 变为 '${newStatus}'`);
      this.currentEnvStatus = newStatus;
      this.environmentHistory.lastReactionTime = now;

      // 根据状态触发相应反应
      const reaction = this.getReactionForStatus(newStatus, env);
      if (reaction) {
        this.triggerPetAction(reaction.action);
        this.showMoodBubble(reaction.text, reaction.emoji);
      }
    }
  }

  // ========== 分析环境数据趋势 ==========
  private analyzeEnvironmentTrend(): string {
    const temps = this.environmentHistory.temps;
    const hums = this.environmentHistory.hums;
    const lights = this.environmentHistory.lights;

    if (temps.length < 3) return 'comfortable'; // 数据不足时默认舒适

    // 计算平均值
    const avgTemp = temps.reduce((a, b) => a + b, 0) / temps.length;
    const avgHum = hums.reduce((a, b) => a + b, 0) / hums.length;
    const avgLight = lights.reduce((a, b) => a + b, 0) / lights.length;

    console.info(`[HomeTab] 🧠 智能环境分析 (${temps.length}个数据点):`);
    console.info(`  - 温度历史: [${temps.map(t => t.toFixed(1)).join(', ')}] 平均: ${avgTemp.toFixed(1)}℃`);
    console.info(`  - 湿度历史: [${hums.map(h => h.toFixed(1)).join(', ')}] 平均: ${avgHum.toFixed(1)}%`);
    console.info(`  - 光照历史: [${lights.map(l => l.toFixed(0)).join(', ')}] 平均: ${avgLight.toFixed(0)}lx`);

    let result: string = 'comfortable';

    // 判断优先级：温度 > 湿度 > 光照
    // 温度过低（连续3次都<15）
    const coldCount: number = temps.filter(t => t < 15).length;
    if (coldCount >= 3) {
      result = 'cold';
      console.info(`[HomeTab] ⚠️ 检测到持续低温: ${coldCount}次 <15℃ → 寒冷状态`);
    }

    // 温度过高（连续3次都>32）
    const hotCount: number = temps.filter(t => t > 32).length;
    if (hotCount >= 3) {
      result = 'hot';
      console.info(`[HomeTab] 🔥 检测到持续高温: ${hotCount}次 >32℃ → 炎热状态`);
    }

    // 湿度过高（连续3次都>70）
    const wetCount: number = hums.filter(h => h > 70).length;
    if (wetCount >= 3 && result === 'comfortable') {
      result = 'wet';
      console.info(`[HomeTab] 💧 检测到高湿度: ${wetCount}次 >70% → 潮湿状态`);
    }

    // 湿度过低（连续3次都<30）
    const dryCount: number = hums.filter(h => h < 30).length;
    if (dryCount >= 3 && result === 'comfortable') {
      result = 'dry';
      console.info(`[HomeTab] 💦 检测到低湿度: ${dryCount}次 <30% → 干燥状态`);
    }

    // 光照舒适（80-200之间且稳定）
    const brightCount: number = lights.filter(l => l >= 80 && l <= 200).length;
    if (brightCount >= 4 && result === 'comfortable') {
      result = 'bright';
      console.info(`[HomeTab] ☀️ 检测到适宜光照: ${brightCount}次 在80-200lx范围 → 明亮舒适状态`);
    }

    // 光照不足（连续3次都<50）
    const dimCount: number = lights.filter(l => l < 50).length;
    if (dimCount >= 3 && result === 'comfortable') {
      result = 'dim';
      console.info(`[HomeTab] 🌙 检测到弱光照: ${dimCount}次 <50lx → 昏暗状态`);
    }

    if (result === 'comfortable') {
      console.info(`[HomeTab] ✅ 环境状况良好，处于舒适状态`);
    }

    return result;
  }

  // ========== 根据状态获取反应内容 ==========
  private getReactionForStatus(status: string, env: EnvironmentData): ReactionResult | null {
    let result: ReactionResult = { action: '', text: '', emoji: '' };
    
    switch (status) {
      case 'cold':
        result.action = 'weiqu';
        result.text = `好冷呀~${env.temp.toFixed(1)}℃，想取暖🔥`;
        result.emoji = '🥶';
        break;
      case 'hot':
        result.action = 'weiqu';
        result.text = `太热啦~${env.temp.toFixed(1)}℃，想吹空调❄️`;
        result.emoji = '🥵';
        break;
      case 'wet':
        result.action = 'weiqu';
        result.text = `黏糊糊的~湿度${env.hum.toFixed(1)}%，想晒太阳💧`;
        result.emoji = '😅';
        break;
      case 'dry':
        result.action = 'sajiao';
        result.text = `有点干~要喝水水💧 ${env.hum.toFixed(1)}%`;
        result.emoji = '💦';
        break;
      case 'bright': {
        // 随机选择一个开心动作
        const happyActions: string[] = ['zhuanquan', 'xiaopao', 'dagun', 'yaoweiba'];
        const randomIndex: number = Math.floor(Math.random() * happyActions.length);
        
        const happyMessages: string[] = [
          '阳光正好~舒服！☀️',
          '光线好好~开心！✨',
          '今天天气真棒~🌈',
          '暖暖的好惬意~😊'
        ];
        
        result.action = happyActions[randomIndex];
        result.text = happyMessages[Math.floor(Math.random() * happyMessages.length)];
        result.emoji = '😄';
        break;
      }
      case 'dim':
        result.action = 'dagun';
        result.text = `有点暗~困了😴 ${env.light.toFixed(0)}lx`;
        result.emoji = '😪';
        break;
      default:
        return null; // comfortable状态不触发特殊反应
    }
    
    return result;
  }

  // ========== 显示心情气泡 ==========
  private showMoodBubble(text: string, emoji: string): void {
    // 清除之前的定时器
    if (this.bubbleTimer !== -1) {
      clearTimeout(this.bubbleTimer);
      this.bubbleTimer = -1;
    }

    // 显示气泡
    this.moodBubble = { text, emoji, show: true };

    // 3秒后自动隐藏
    this.bubbleTimer = setTimeout(() => {
      this.moodBubble.show = false;
      this.bubbleTimer = -1;
    }, 3000);
  }

  aboutToDisappear(): void {
    this.speechService.destroy();
    if (this.refreshTimer !== -1) {
      clearInterval(this.refreshTimer);
      this.refreshTimer = -1;
    }
    if (this.envCheckTimer !== -1) {
      clearInterval(this.envCheckTimer);
      this.envCheckTimer = -1;
    }
    if (this.bubbleTimer !== -1) {
      clearTimeout(this.bubbleTimer);
      this.bubbleTimer = -1;
    }
    if (this.yoloPollTimer !== -1) {
      clearInterval(this.yoloPollTimer);
      this.yoloPollTimer = -1;
    }
    if (this.voicePollTimer !== -1) {
      clearInterval(this.voicePollTimer);
      this.voicePollTimer = -1;
    }
    // 清理MQTT连接
    if (this.mqttUtil) {
      this.mqttUtil = null;
    }
  }

  // ========== YOLO 实时识别轮询（每 600 ms 拉一次 11436 的最新判定）==========
  // 原来是 1000 ms，板端 behavior_hz=10（100 ms 一次判定），前端 1 秒才取一次，
  // 最坏要等满 1 秒才看到已经算好的结果。
  //
  // 为什么不是 300 ms（2026-11-06 实测，这个数字是被测出来的，不是拍的）：
  // 模拟器是 QEMU 虚拟机，每个请求都要经 hdc 反向转发（rport）绕一圈，
  // 代价远高于普通 HTTP。实测把间隔从 1000 ms 改到 300 ms 后，
  // 宿主侧推理耗时从 33 ms 暴涨到 240~440 ms（26 fps → 3~4 fps）——
  // 请求密度翻 3 倍，把 QEMU 和 hdc 的 CPU 抢光，反而让「数据新鲜度」更差。
  // 600 ms 是收益/代价的折中：比原来快 40%，又不至于压垮转发链路。
  //
  // 真要走 300 ms 以下，得换掉模拟器这条链路（3568 真板直连，或 Previewer）。
  //
  // 防重入是必须的：TIMEOUT_VISION=800 ms 比 600 ms 长，链路抖动时会出现
  // 上一个请求还没回来、下一个又发出去，结果是旧响应后到、把界面回退成老动作。
  private yoloFetching: boolean = false;

  private startYoloPolling(): void {
    console.info(`[HomeTab] 启动 YOLO 轮询 → ${this.yoloServerHost}/analyze`);
    this.yoloPollTimer = setInterval(() => {
      if (this.yoloFetching) {
        return;
      }
      this.yoloFetching = true;
      this.fetchYoloOnce().then(() => {
        this.yoloFetching = false;
      }).catch(() => {
        this.yoloFetching = false;
      });
    }, 600);
  }

  // ========== 听觉轮询（每 3 秒拉一次 K1 11439 的最新叫声判定）==========
  private startVoicePolling(): void {
    console.info('[HomeTab] 启动叫声轮询 → K1 11439 /v1/voice/latest');
    this.voicePollTimer = setInterval(() => {
      this.fetchVoiceOnce();
    }, 3000);
    this.fetchVoiceOnce();
  }

  /**
   * 拉一次最新叫声结论。
   *
   * 关键区分（这是最容易写成假数据的地方）：**拉不到 ≠ 安静**。
   * 板端只要活着就会返回 cls=silence，所以「当前没叫」和「服务断了」
   * 在 JSON 里长得很像，必须靠可达性分开：
   *   · 不可达            → 「未接入」（灰）—— 不知道，不是安静
   *   · 可达 + 静音       → 「安静」（灰）—— 这是真实判定
   *   · 可达 + 值得注意   → 「吠叫·警戒」（暖色）+ 建议一行
   * 若一律显示「安静」，断链时首页会常年安静，等于拿假数据当实时上报。
   */
  private async fetchVoiceOnce(): Promise<void> {
    const snap: VoiceSnapshot | null = await this.voiceChannel.fetch();
    if (snap === null) {
      if (this.voiceConnected) {
        this.voiceConnected = false;
        this.voiceTag = '未接入';
        this.voiceAdvice = '';
        this.voiceNoteworthy = false;
        this.voiceConf = 0;
      }
      return;
    }
    this.voiceConnected = true;
    this.voiceTag = snap.shortTag();
    this.voiceNoteworthy = snap.noteworthy;
    this.voiceConf = snap.conf;
    // 只在值得提示时带建议：安静时留白，避免整张卡被文案撑高
    this.voiceAdvice = snap.noteworthy ? snap.advice : '';
  }

  // 根据监测页兴奋度(0-100)映射到首页正面心情词
  private excitementToMood(excitement: number): string {
    if (excitement < 20) {
      return Math.random() < 0.5 ? '舒适' : '惬意';
    }
    if (excitement < 50) {
      return Math.random() < 0.5 ? '愉悦' : '开心';
    }
    if (excitement < 75) {
      return '活力满满';
    }
    return Math.random() < 0.5 ? '神采奕奕' : '心情棒';
  }

  private async fetchYoloOnce(): Promise<void> {
    // 视觉源已从「PC 本地 yolo 服务」换成 K1 板端。
    // 旧实现的 yoloServerHost 还硬编码着 172.20.10.4:5000（早年的手机热点），
    // 那台机器不存在了，所以首页的「未连接」一直亮着——不是 K1 没数据。
    const snap: VisionSnapshot | null = await this.perception.fetch();

    // 漏检沿用帧（后端 stale=true）：本帧没有真实检测结果，
    // action / label 只是把上一次判定原样搬过来。
    //
    // 这种帧必须「什么都不做」，绝不能当新行为喂进下面那套流程：
    //   1) 它会挤进 debounceWindow 参与众数投票，把真实动作投下去；
    //   2) 更糟的是下面的「行为变化立即切换」——沿用值一旦和已采纳值不同，
    //      会立刻把 3D 拉回旧动作。观感就是「动作乱跳」和「卡住不动」。
    //
    // 背景：真实宠物视频在板端有 55%~58% 的帧检不到目标，沿用帧占比过半，
    // 不排除掉必然把行为判定搅乱（2026-11-06 实测回归）。
    // petLostTicks 要清零：后端仍认为宠物在跟踪中，不该累计丢宠。
    if (snap !== null && snap.stale) {
      this.petLostTicks = 0;
      if (!this.yoloConnected) {
        this.yoloConnected = true;
        console.info('[HomeTab] ✅ 已连接（沿用帧）');
      }
      return;
    }

    const result: YoloAnalyzeResult = {
      connected: snap !== null,
      errorMsg: snap === null ? 'K1 视觉不可达' : '',
      petCount: 0,
      pets: [],
      timestamp: '',
      rawJson: ''
    };
    if (snap !== null && snap.pet.length > 0) {
      const one: YoloPet = {
        id: 1,
        type: snap.pet,
        behavior: snap.action,
        confidence: snap.conf,
        bbox: [],
        speed: 0
      };
      result.petCount = Math.max(1, snap.count);
      result.pets = [one];
    }
    if (!result.connected) {
      if (this.yoloConnected) {
        this.yoloConnected = false;
        this.yoloErrorMsg = result.errorMsg;
        this.yoloHasDetection = false;
        this.yoloPetLabel = '';
        this.yoloActionLabel = '未检测到宠物';
        this.yoloMood = '';
        this.yoloConfidence = 0;
        this.yoloHadPetBefore = false;
        this.debounceWindow = [];
        this.lastAdoptedBehavior = '';
        this.lastTriggeredBehavior = '';
        this.pendingBehavior = '';
        this.pendingCount = 0;
      }
      return;
    }
    if (!this.yoloConnected) {
      this.yoloConnected = true;
      console.info(`[HomeTab] ✅ YOLO 已连接 pet_count=${result.petCount}`);
    }

    // 无宠物 → 清空 UI（带丢宠容忍，见 petLostTicks 注释）
    if (result.petCount === 0 || result.pets.length === 0) {
      this.petLostTicks++;
      if (this.petLostTicks < this.PET_LOST_TOLERANCE) {
        // 短暂漏检：保持上一拍的宠物与动作，不清窗口，3D 不回待机
        return;
      }
      this.debounceWindow = [];
      this.lastAdoptedBehavior = '';
      this.lastTriggeredBehavior = '';
      this.pendingBehavior = '';
      this.pendingCount = 0;
      this.yoloHadPetBefore = false;
      this.yoloHasDetection = false;
      this.yoloPetLabel = '';
      this.yoloActionLabel = '未检测到宠物';
      this.yoloMood = '';
      this.yoloConfidence = 0;
      return;
    }

    this.petLostTicks = 0;

    // 选主宠
    const first: YoloPet = this.pickPrimaryPet(result.pets);
    const rawBehavior = first.behavior;
    console.info(`[HomeTab] 📋 收到 ${result.pets.length} 只宠物 → type=${first.type} behavior=${rawBehavior} conf=${first.confidence.toFixed(2)}`);

    // 行为序列记录（仅诊断，决策见下面的连续确认）
    this.debounceWindow.push(rawBehavior);
    if (this.debounceWindow.length > this.DEBOUNCE_WINDOW_SIZE) {
      this.debounceWindow.shift();
    }

    // 行为切换：连续 BEHAVIOR_CONFIRM_TICKS 拍都指向同一个新行为才切。
    //
    // 原来这里是「行为变化立即切换」（rawBehavior !== lastAdopted 就直接采纳）。
    // 那等于把单拍判定直接搬到屏幕上：检测跑 26fps，而这里是 1Hz 轮询，
    // 每拍只取一帧，任何一次瞬时误判都会立刻切过去，下一拍再切回来 ——
    // 观感就是「动作乱跳、识别不准」。2026-11-06 实测，画面暗（曝光 4.8）时尤其明显。
    //
    // 原来的众数投票（ratio ≥ 0.6）也兜不住：它只在「同行为」分支里生效，
    // 而真正出问题的是「变化」分支，那条路根本不看 ratio。
    //
    // 改成连续 2 拍确认后：单拍误判不再上屏，真实变化最多延迟 1~2 秒。
    if (!this.yoloHadPetBefore) {
      this.yoloHadPetBefore = true;
      this.lastAdoptedBehavior = rawBehavior;
      this.pendingBehavior = '';
      this.pendingCount = 0;
      console.info(`[HomeTab] 🚀 首次检测 → ${rawBehavior}`);
    } else if (rawBehavior === this.lastAdoptedBehavior) {
      // 与当前动作一致：清掉待定计数，避免旧候选攒够次数后被误采纳
      this.pendingBehavior = '';
      this.pendingCount = 0;
    } else {
      if (rawBehavior === this.pendingBehavior) {
        this.pendingCount++;
      } else {
        this.pendingBehavior = rawBehavior;
        this.pendingCount = 1;
      }
      if (this.pendingCount < this.BEHAVIOR_CONFIRM_TICKS) {
        console.info(`[HomeTab] ⏳ 待确认 ${rawBehavior} (${this.pendingCount}/${this.BEHAVIOR_CONFIRM_TICKS})`);
        return;
      }
      const old: string = this.lastAdoptedBehavior;
      this.lastAdoptedBehavior = rawBehavior;
      this.pendingBehavior = '';
      this.pendingCount = 0;
      console.info(`[HomeTab] 🔄 行为变化 ${old} → ${rawBehavior}（连续 ${this.BEHAVIOR_CONFIRM_TICKS} 拍确认）`);
    }

    // === 更新 UI ===
    // 走到这里，lastAdoptedBehavior 一定是「已确认」的行为（首次检测，或连续
    // BEHAVIOR_CONFIRM_TICKS 拍确认）。下面统一用它而不是本拍的 rawBehavior：
    // 后者可能只是一个还没被确认的单拍判定。
    const adopted: string = this.lastAdoptedBehavior;
    this.yoloHasDetection = true;
    // petType 跟随检测结果，但只在变化时赋值：每拍都改 @Prop 会让
    // Video3DView 重建，重建后 @Watch 丢掉当前命令（动作就不播了）。
    // 原来这里无条件写 'dog'：识别到猫时文字显示「小猫」、播的却是狗的片子，
    // 同一屏上自相矛盾。
    if ((first.type === 'cat' || first.type === 'dog') && this.petType !== first.type) {
      this.petType = first.type;
    }
    this.yoloPetLabel = yoloTypeToLabel(first.type);
    this.yoloActionLabel = yoloBehaviorToLabel(adopted);
    this.yoloConfidence = Math.round(first.confidence * 100);
    if (adopted !== this.yoloLastBehavior) {
      this.yoloMood = yoloBehaviorToMood(adopted);
      this.yoloLastBehavior = adopted;
    }

    // === 直接触发视频（行为变化时中断旧视频，同行为不重复触发） ===
    const action = mapYoloBehaviorToAction(adopted);
    // 去重按**行为**而不是动作名：不新增素材的前提下 jumping 与 running
    // 复用同一条 xiaopao 片子，按动作名去重会让「跑完立刻跳」被吞掉——
    // 动作名没变，但行为真的变了，形象应该重新起跳一次。
    if (action !== null && adopted !== this.lastTriggeredBehavior) {
      this.lastTriggeredBehavior = adopted;
      // 持续行为循环播放，瞬时行为单次播放（跳、坐、卧等播完回待机）
      this.yoloActionLoops = (adopted === 'walking' || adopted === 'running' || adopted === 'playing');
      const cmd = `${action}_${Date.now()}`;
      console.info(`[HomeTab] 🎬 ${adopted} → ${action} → petActionCommand="${cmd}"`);
      this.petActionCommand = cmd;
    }
  }

  /**
   * 从多只宠物中挑选"主宠"（App 主要跟踪的那只）
   * 优先级：
   *   1. type === 'dog'（用户主关注狗）
   *   2. confidence 高
   *   3. bbox 面积大（更靠近镜头、更突出）
   * 这样保证 App 端拿到的是 PC 端 UI 高亮的"主狗"，而不是随便哪只
   */
  private pickPrimaryPet(pets: YoloPet[]): YoloPet {
    let best: YoloPet = pets[0];
    let bestScore: number = this.calcPetScore(best);
    for (let i = 1; i < pets.length; i++) {
      const p: YoloPet = pets[i];
      const score: number = this.calcPetScore(p);
      if (score > bestScore) {
        best = p;
        bestScore = score;
      }
    }
    return best;
  }

  // 计算宠物"主宠分"：狗 +1000，置信度*100，bbox 面积/10000
  private calcPetScore(p: YoloPet): number {
    const typeBonus: number = p.type === 'dog' ? 1000 : (p.type === 'cat' ? 500 : 0);
    const confScore: number = p.confidence * 100;
    let areaScore: number = 0;
    if (p.bbox && p.bbox.length >= 4) {
      const w: number = Math.max(0, p.bbox[2] - p.bbox[0]);
      const h: number = Math.max(0, p.bbox[3] - p.bbox[1]);
      areaScore = (w * h) / 10000;
    }
    return typeBonus + confScore + areaScore;
  }

  onPageShow(): void {
    this.viewModel.refresh();
  }

  async initSpeech(): Promise<void> {
    console.info('[HomeTab] 开始初始化语音服务...');

    // 获取 context
    const context = this.GetUIContext();
    if (!context) {
      console.error('[HomeTab] 无法获取 UIAbilityContext');
      promptAction.showToast({ message: '系统错误：无法获取上下文' });
      return;
    }

    console.info('[HomeTab] Context 获取成功，开始初始化...');

    try {
      const success = await this.speechService.init(context);
      if (success) {
        console.info('[HomeTab] 语音服务初始化成功');
        this.speechService.setCallbacks({
          onStart: () => {
            this.viewModel.isRecording = true;
            this.viewModel.recognizingText = '';
          },
          onResult: (result: RecognitionResult) => {
            this.viewModel.recognizingText = result.text;
          },
          onComplete: (finalText: string) => {
            this.viewModel.isRecording = false;
            this.viewModel.recognizingText = '';
            if (finalText && finalText.trim().length > 0) {
              this.viewModel.handleVoiceCommand(finalText.trim());
            }
          },
          onError: (code: number, message: string) => {
            this.viewModel.isRecording = false;
            this.viewModel.recognizingText = '';
            console.error(`[HomeTab] 语音识别错误: ${code} - ${message}`);
            // 显示详细错误信息
            const displayMsg = message && message.length > 0 ? message : '语音识别失败，请重试';
            promptAction.showToast({ message: displayMsg });
          }
        });
      } else {
        console.error('[HomeTab] 语音服务初始化返回失败');
        promptAction.showToast({ message: '语音服务初始化失败，请检查权限' });
      }
    } catch (e) {
      console.error('[HomeTab] 语音初始化异常:', JSON.stringify(e));
      promptAction.showToast({ message: '语音服务初始化异常' });
    }
  }

  private triggerPetAction(action: string): void {
    this.petActionCommand = `${action}_${Date.now()}`;
  }

  private startRecording(): void {
    console.info('[HomeTab] ===== 用户按下录音按钮 =====');
    console.info(`[HomeTab] 当前状态: isRecording=${this.viewModel.isRecording}`);
    
    this.speechService.startListening().then(() => {
      console.info('[HomeTab] ✅ startListening() 完成');
    }).catch((err: Error) => {
      console.error('[HomeTab] ❌ startListening() 失败:', JSON.stringify(err));
      promptAction.showToast({ message: '启动录音失败: ' + String(err) });
    });
  }

  private stopRecording(): void {
    console.info('[HomeTab] ===== 用户松开录音按钮 =====');
    console.info(`[HomeTab] 当前状态: isRecording=${this.viewModel.isRecording}`);
    
    this.speechService.stopListening().then(() => {
      console.info('[HomeTab] ✅ stopListening() 完成');
    }).catch((err: Error) => {
      console.error('[HomeTab] ❌ stopListening() 失败:', JSON.stringify(err));
    });
  }

  // 处理热区触摸：触发动作 + 增加心情
  // private handlePetTouch(region: PetTouchRegion): void {
  //   // 检查饥饿度
  //   if (!this.viewModel.checkInteractAndPrompt()) {
  //     return;
  //   }
  //
  //   let action: string = '';
  //   let bubbleText: string = '';
  //   switch (region) {
  //     case PetTouchRegion.HEAD:
  //       action = 'motou';
  //       bubbleText = '摸头好舒服~😆';
  //       break;
  //     case PetTouchRegion.MIDDLE:
  //       action = 'dagun';
  //       bubbleText = '打个滚~开心！🌀';
  //       break;
  //     case PetTouchRegion.BOTTOM_LEFT:
  //       action = 'sajiao'; // 摸手改为撒娇
  //       bubbleText = '握握手~最好的朋友🤝';
  //       break;
  //     case PetTouchRegion.BOTTOM_RIGHT:
  //       action = 'yaoweiba';
  //       bubbleText = '摇尾巴~喜欢喜欢🐕';
  //       break;
  //   }
  //   if (action !== '') {
  //     this.triggerPetAction(action);
  //     this.viewModel.play(); // 互动增加心情（内部消耗饥饿度）
  //     // 显示互动气泡
  //     this.showMoodBubble(bubbleText, '💕');
  //   }
  // }

  // 顶部状态栏（含子Tab切换）
  @Builder
  TopStatusBar() {
    Column() {
      Row() {
        Row() {
          Text(this.petName)
            .fontSize(20)
            .fontWeight(FontWeight.Bold)
            .fontColor('#2C3E50')
        }

        Blank()
        Image($r('app.media.shezhi'))
          .width(25)
          .onClick(() => promptAction.showToast({ message: '设置功能开发中' }))
      }
      .width('100%')
      .padding({ left: 20, right: 20, top: 12, bottom: 4 })

      // 子Tab：真实萌宠 / 虚拟萌宠
      Row() {
        this.SubTabButton('真实萌宠', 0)
        this.SubTabButton('虚拟萌宠', 1)
      }
      .width('100%')
      .padding({ left: 20, right: 20, bottom: 8 })
    }
    .width('100%')
    .backgroundColor('rgba(255, 255, 255, 0.95)')
  }

  // 子Tab按钮
  @Builder
  SubTabButton(label: string, tabIndex: number) {
    Text(label)
      .fontSize(14)
      .fontWeight(this.selectedPetTab === tabIndex ? FontWeight.Medium : FontWeight.Normal)
      .fontColor(this.selectedPetTab === tabIndex ? '#2C3E50' : '#7F8C8D')
      .textAlign(TextAlign.Center)
      .padding({ top: 8, bottom: 8 })
      .borderRadius(16)
      .backgroundColor(this.selectedPetTab === tabIndex ? 'rgba(106, 130, 251, 0.12)' : Color.Transparent)
      .layoutWeight(1)  // 均分整行宽度
      .onClick(() => {
        this.selectedPetTab = tabIndex;
      })
  }

  // 紧凑状态条（YOLO 实时识别：宠物类型 + 动作 + 心情 + 置信度）
  @Builder
  CompactStatusBar() {
    Column() {
      // 标题行：当前宠物状态 + 连接指示点
      Row() {
        Text('当前宠物状态')
          .fontSize(14)
          .fontColor('#7F8C8D')
        Blank()
        Row() {
          Circle({ width: 8, height: 8 })
            .fill(this.yoloConnected ? '#2ECC71' : '#BDC3C7')
          Text(this.yoloConnected ? '已连接' : '未连接')
            .fontSize(11)
            .fontColor(this.yoloConnected ? '#2ECC71' : '#95A5A6')
            .margin({ left: 4 })
        }
      }
      .width('100%')
      .margin({ bottom: 4 })

      if (this.yoloHasDetection) {
        // ===== 已识别到宠物：显示类型+动作 =====
        Row() {
          Text(this.yoloPetLabel)
            .fontSize(22)
            .fontWeight(FontWeight.Bold)
            .fontColor('#2C3E50')
          Text(this.yoloActionLabel)
            .fontSize(22)
            .fontWeight(FontWeight.Bold)
            .fontColor('#6A82FB')
            .margin({ left: 8 })
        }
        .width('100%')
        .alignItems(VerticalAlign.Center)

        // 心情行
        Row() {
          Text('😊')
            .fontSize(16)
            .margin({ right: 6 })
          Text(`心情：${this.yoloMood}`)
            .fontSize(14)
            .fontColor('#5D6D7E')
          Blank()
          if (this.yoloConfidence > 0) {
            Text(`置信度 ${this.yoloConfidence}%`)
              .fontSize(12)
              .fontColor('#95A5A6')
          }
        }
        .width('100%')
        .margin({ top: 2 })
      } else {
        // ===== 未检测到宠物 =====
        Row() {
          Text('🔍')
            .fontSize(20)
            .margin({ right: 8 })
          Text(this.yoloActionLabel)
            .fontSize(18)
            .fontColor('#95A5A6')
            .fontWeight(FontWeight.Medium)
        }
        .width('100%')
        .alignItems(VerticalAlign.Center)
        .margin({ top: 4 })
      }

      // ===== 听觉：独立于视觉，视野里没宠物时麦克风照样在听 =====
      // 故意放在 if/else 之外——「它在叫」和「有没有看到它」是两件事，
      // 塞进检测分支里就会出现「画面没人所以叫声也消失」的错。
      Row() {
        Text('🔊')
          .fontSize(14)
          .margin({ right: 6 })
        Text(`叫声：${this.voiceTag}`)
          .fontSize(14)
          .fontColor(this.voiceNoteworthy ? '#D35400' : '#95A5A6')
          .fontWeight(this.voiceNoteworthy ? FontWeight.Medium : FontWeight.Normal)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .layoutWeight(1)
        if (this.voiceConnected && this.voiceNoteworthy && this.voiceConf > 0) {
          Text(`置信 ${this.voiceConf.toFixed(2)}`)
            .fontSize(11)
            .fontColor('#95A5A6')
        }
      }
      .width('100%')
      .margin({ top: 2 })

      // 建议只在「值得提示」时出现，安静时留白，避免卡片被文案撑高
      if (this.voiceConnected && this.voiceNoteworthy && this.voiceAdvice.length > 0) {
        Text(this.voiceAdvice)
          .fontSize(12)
          .fontColor('#9C6B3F')
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .width('100%')
          .margin({ top: 4 })
      }
    }
    .width('100%')
    .padding({ left: 14, right: 14, top: 10, bottom: 10 })
    .backgroundColor('#FFFFFF')
    .borderRadius(12)
    .margin({ left: 12, right: 12, bottom: 8 })
  }

  // 录音指示器
  // @Builder
  // RecordingIndicator() {
  //   Row() {
  //     Text('🎤').fontSize(28).margin({ right: 8 })
  //     Text(this.viewModel.recognizingText || '正在聆听...')
  //       .fontSize(16)
  //       .fontColor('#6A82FB')
  //       .maxLines(1)
  //       .textOverflow({ overflow: TextOverflow.Ellipsis })
  //   }
  //   .padding({
  //     left: 16,
  //     right: 16,
  //     top: 8,
  //     bottom: 8
  //   })
  //   .backgroundColor('#F0F5FF')
  //   .borderRadius(20)
  //   .margin({ left: 12, right: 12, bottom: 8 })
  // }

  // 动作栏
  // @Builder
  // ActionBar() {
  //   Scroll() {
  //     Row() {
  //       this.ActionItem('🥺', '撒娇', 'sajiao')
  //       this.ActionItem('🌀', '打滚', 'dagun')
  //       this.ActionItem('🔄', '转圈', 'zhuanquan')
  //       this.ActionItem('🐕', '摇尾巴', 'yaoweiba')
  //       this.ActionItem('🏃', '奔跑', 'xiaopao')
  //       this.ActionItem('🤔', '歪头', 'waitou')
  //       this.ActionItem('🙆', '伸懒腰', 'shenlanyao')
  //       this.ActionItem('💦', '甩水', 'shuaishui')
  //       this.ActionItem('🤝', '摸手', 'moshou')
  //       this.ActionItem('👋', '摸头', 'motou')
  //     }
  //     .padding({ left: 8, right: 8 })
  //   }
  //   .scrollable(ScrollDirection.Horizontal)
  //   .scrollBar(BarState.Off)
  //   .width('100%')
  //   .padding({ top: 8, bottom: 8 })
  //   .backgroundColor('#FFFFFF')
  //   .borderRadius(12)
  //   .margin({ left: 12, right: 12, bottom: 8 })
  // }

  // @Builder
  // ActionItem(icon: string, label: string, action: string) {
  //   Column() {
  //     Text(icon).fontSize(28).margin({ bottom: 2 })
  //     Text(label).fontSize(12).fontColor('#5D6D7E')
  //   }
  //   .width(65)
  //   .padding({ top: 6, bottom: 6 })
  //   .backgroundColor('#F8F9FA')
  //   .borderRadius(20)
  //   .margin({ right: 8 })
  //   .onClick(() => {
  //     // 检查饥饿度
  //     if (!this.viewModel.checkInteractAndPrompt()) {
  //       return;
  //     }
  //     this.triggerPetAction(action);
  //     this.viewModel.play(); // 互动增加心情（内部消耗饥饿度）
  //
  //     // 根据动作显示不同的气泡文本
  //     const actionMessages: Record<string, string> = {
  //       'sajiao': '撒个娇~主人抱抱🥺',
  //       'dagun': '打滚翻滚~好玩！🌀',
  //       'zhuanquan': '转圈圈~追尾巴🔄',
  //       'yaoweiba': '摇摇尾巴~开心🐕',
  //       'xiaopao': '奔跑奔跑~活力满满🏃',
  //       'waitou': '歪头歪头~疑惑？🤔',
  //       'shenlanyao': '伸懒腰~舒舒服服🙆',
  //       'shuaishui': '甩甩水~抖一抖💦',
  //       'moshou': '握握手~好朋友🤝',
  //       'motou': '摸摸头~好舒服👋'
  //     };
  //
  //     this.showMoodBubble(actionMessages[action] || `宠物${label}了`, icon);
  //     this.showActionBar = false;
  //   })
  // }

  // 底部功能按钮（均分整行）
  @Builder
  ActionButtons() {
    Row() {
      this.ActionButton('🎾', '逗宠', () => {
        this.playWithPet();
      })

      this.ActionButton('🏠', this.isEnvironmentOn ? '已开启' : '舒适环境', () => {
        this.setEnvironmentComfort();
      })
    }
    .width('100%')
    .padding({ left: 12, right: 12, top: 4, bottom: 16 })
  }

  @Builder
  ActionButton(icon: string, label: string, onClick: () => void) {
    Column() {
      Text(icon)
        .fontSize(26)
        .margin({ bottom: 4 })
      Text(label)
        .fontSize(13)
        .fontColor('#4A5568')
    }
    .layoutWeight(1)
    .height(68)
    .justifyContent(FlexAlign.Center)
    .borderRadius(12)
    .backgroundColor('#FFFFFF')
    .shadow({ radius: 6, color: 'rgba(0, 0, 0, 0.06)', offsetY: 2 })
    .margin({ left: 4, right: 4 })
    .onClick(onClick)
  }

  // 心情气泡提示组件（显示在小狗头部右上方）
  @Builder
  MoodBubbleView() {
    Column() {
      // 气泡内容
      Row() {
        Text(this.moodBubble.emoji)
          .fontSize(18)
          .margin({ right: 4 })
        Text(this.moodBubble.text)
          .fontSize(13)
          .fontColor('#333333')
          .fontWeight(FontWeight.Medium)
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .padding({ left: 12, right: 12, top: 8, bottom: 8 })
      .backgroundColor('rgba(255, 255, 255, 0.98)')
      .borderRadius(14)
      .shadow({
        radius: 6,
        color: 'rgba(0, 0, 0, 0.08)',
        offsetY: 3
      })

      // 小三角（指向下方小狗头部）
      Text('▼')
        .fontSize(8)
        .fontColor('rgba(255, 255, 255, 0.98)')
        .margin({ top: 2 })
    }
    .position({ x: '55%', y: '8%' }) // 宠物头顶正上方
    .animation({
      duration: 400,
      curve: Curve.EaseInOut
    })
  }

  private getMoodColor(): ResourceColor {
    const mood = this.viewModel.petInfo.mood;
    if (mood >= 70) {
      return '#2ECC71';
    }
    if (mood >= 40) {
      return '#F39C12';
    }
    return '#E74C3C';
  }

  private getHungerColor(): ResourceColor {
    const hunger = this.viewModel.petInfo.hunger;
    if (hunger >= 70) {
      return '#2ECC71';
    }
    if (hunger >= 40) {
      return '#F39C12';
    }
    return '#E74C3C';
  }

  build() {
    Column() {
      this.TopStatusBar()

      if (this.selectedPetTab === 0) {
        // 真实萌宠：宠物视图区域（包含心情气泡）
        Stack() {
          // 根据模型模式选择不同的视图组件
          if (this.useStandardModel) {
            // 标准模式：使用3D视频展示
            Video3DView({
              actionCommand: this.petActionCommand,
              petType: this.petType,
              loopAction: this.yoloActionLoops,
              disableAutoRandom: true // 真实模式：关闭自动随机循环，仅由 YOLO 驱动视频
            })
              .width('100%')
              .height('100%')
          } else {
            // 自定义模式：使用原来的3D WebView展示
            Pet3DView({
              actionCommand: this.petActionCommand,
              // onTouchRegion: (region: PetTouchRegion) => this.handlePetTouch(region),
              // petType: this.petType
            })
              .width('100%')
              .height('100%')
          }

          // 心情气泡提示（右上角）
          if (this.moodBubble.show) {
            this.MoodBubbleView()
          }
        }
        .width('100%')
        .layoutWeight(1)
        .padding({ left: 8, right: 8 })

        this.CompactStatusBar()

        // if (this.viewModel.isRecording) {
        //   this.RecordingIndicator()
        // }
        //
        // if (this.showActionBar) {
        //   this.ActionBar()
        // }

        Divider().color('#EEEEEE').width('100%').height(1)

        this.ActionButtons()
      } else {
        // 虚拟萌宠：嵌入 VirtualPetContent（隐藏其顶部状态栏）
        // externalActionCommand：把识别出的行为透传过去，否则这一页的形象
        // 只响应触摸/语音，检测到什么都不动（原来的实际状态）。
        VirtualPetContent({ showTopBar: false, externalActionCommand: this.petActionCommand })
          .layoutWeight(1)  // 撑满剩余高度，确保底部互动栏可见
      }
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#F8F9FA')
  }
}