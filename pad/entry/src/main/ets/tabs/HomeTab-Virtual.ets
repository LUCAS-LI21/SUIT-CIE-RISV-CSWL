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
export struct VirtualPetContent {
  @State viewModel: HomeViewModel = new HomeViewModel();
  @State petViewKey: number = 0;
  @State showActionBar: boolean = false;
  @State petActionCommand: string = '';
  @State petType: string = 'cat'; // 宠物类型，默认为cat
  @State petName: string = '旺财'; // 宠物名称
  @State useStandardModel: boolean = true; // 是否使用标准模型（视频模式）
  @Prop showTopBar: boolean = true; // 嵌入模式下隐藏TopStatusBar

  /**
   * 父组件（HomeTab）透传的行为动画命令。
   *
   * 虚拟萌宠这一页原来完全不接识别：petActionCommand 只在用户点触摸区或
   * 语音互动时才被 triggerPetAction() 改写，所以无论 K1 检测到跳还是跑，
   * 这一页的形象都不会自己动。父组件把命令透传进来后，两页共用同一套驱动。
   * 保留内部 @State：用户手动互动仍能立即覆盖外部命令。
   */
  @Prop @Watch('onExternalActionCommand') externalActionCommand: string = '';

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
    this.mqttUtil.connectPet(
      (err: Error, data: MqttMessage) => {
        if (!err) {
          try {
            const payloadStr = String(data.payload);
            console.info('[HomeTab] 收到MQTT数据:', payloadStr);

            interface MqttDataType {
              temp: number;
              hum: number;
              light: number;
              status: string;
            }

            // 与 ControlTab / HomeTab-Realistic 同一套容错：inf/NaN 非法 JSON 会让整条丢
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
      'pet_app_home_v'
    );
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
    // 清理MQTT连接
    if (this.mqttUtil) {
      this.mqttUtil = null;
    }
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
  private handlePetTouch(region: PetTouchRegion): void {
    // 检查饥饿度
    if (!this.viewModel.checkInteractAndPrompt()) {
      return;
    }

    let action: string = '';
    let bubbleText: string = '';
    switch (region) {
      case PetTouchRegion.HEAD:
        action = 'motou';
        bubbleText = '摸头好舒服~😆';
        break;
      case PetTouchRegion.MIDDLE:
        action = 'dagun';
        bubbleText = '打个滚~开心！🌀';
        break;
      case PetTouchRegion.BOTTOM_LEFT:
        action = 'sajiao'; // 摸手改为撒娇
        bubbleText = '握握手~最好的朋友🤝';
        break;
      case PetTouchRegion.BOTTOM_RIGHT:
        action = 'yaoweiba';
        bubbleText = '摇尾巴~喜欢喜欢🐕';
        break;
    }
    if (action !== '') {
      this.triggerPetAction(action);
      this.viewModel.play(); // 互动增加心情（内部消耗饥饿度）
      // 显示互动气泡
      this.showMoodBubble(bubbleText, '💕');
    }
  }

  // 顶部状态栏
  @Builder
  TopStatusBar() {
    Row() {
      Row() {
        Text(this.petName)
          .fontSize(20)
          .fontWeight(FontWeight.Bold)
          .fontColor('#2C3E50')
        // Text(`Lv.${this.viewModel.petInfo.level}`)
        //   .fontSize(14)
        //   .fontColor('#FFFFFF')
        //   .backgroundColor('#6A82FB')
        //   .borderRadius(12)
        //   .padding({
        //     left: 10,
        //     right: 10,
        //     top: 4,
        //     bottom: 4
        //   })
        //   .margin({ left: 10 })
      }

      Blank()
      Image($r('app.media.shezhi'))
        .width(25)
        .onClick(() => promptAction.showToast({ message: '设置功能开发中' }))
    }
    .width('100%')
    .padding({
      left: 20,
      right: 20,
      top: 12,
      bottom: 12
    })
    .backgroundColor('rgba(255, 255, 255, 0.95)')
  }

  // 紧凑状态条
  @Builder
  CompactStatusBar() {
    Column() {
      Row() {
        Text(this.viewModel.getMoodIcon()).fontSize(18)
        Text('心情').fontSize(13).fontColor('#5D6D7E').margin({ left: 4 })
        Progress({ value: this.viewModel.petInfo.mood, total: 100, type: ProgressType.Linear })
          .layoutWeight(1)
          .height(20)
          .color(this.getMoodColor())
          .backgroundColor('#E0E0E0')
          .margin({ left: 8 })
        Text(`${this.viewModel.petInfo.mood}/100`)
          .fontSize(11)
          .fontColor('#7F8C8D')
          .margin({ left: 4 })
        Blank()
      }
      .width('100%')
      .padding({ left: 20, right: 20 })
      .margin({ bottom: 8 })

      Row() {
        Text(this.viewModel.getHungerIcon()).fontSize(18)
        Text('饥饿').fontSize(13).fontColor('#5D6D7E').margin({ left: 4 })
        Progress({ value: this.viewModel.petInfo.hunger, total: 100, type: ProgressType.Linear })
          .layoutWeight(1)
          .height(5)
          .color(this.getHungerColor())
          .backgroundColor('#E0E0E0')
          .margin({ left: 8 })
        Text(`${this.viewModel.petInfo.hunger}/100`)
          .fontSize(11)
          .fontColor('#7F8C8D')
          .margin({ left: 4 })
        Blank()
      }
      .width('100%')
      .padding({ left: 20, right: 20 })
    }
    .width('100%')
    .padding({
      left: 16,
      right: 16,
      top: 8,
      bottom: 8
    })
    .backgroundColor('#FFFFFF')
    .borderRadius(12)
    .margin({ left: 12, right: 12, bottom: 8 })
  }

  // 录音指示器
  @Builder
  RecordingIndicator() {
    Row() {
      Text('🎤').fontSize(28).margin({ right: 8 })
      Text(this.viewModel.recognizingText || '正在聆听...')
        .fontSize(16)
        .fontColor('#6A82FB')
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
    }
    .padding({
      left: 16,
      right: 16,
      top: 8,
      bottom: 8
    })
    .backgroundColor('#F0F5FF')
    .borderRadius(20)
    .margin({ left: 12, right: 12, bottom: 8 })
  }

  // 动作栏
  @Builder
  ActionBar() {
    Scroll() {
      Row() {
        this.ActionItem('🥺', '撒娇', 'sajiao')
        this.ActionItem('🌀', '打滚', 'dagun')
        this.ActionItem('🔄', '转圈', 'zhuanquan')
        this.ActionItem('🐕', '摇尾巴', 'yaoweiba')
        this.ActionItem('🏃', '奔跑', 'xiaopao')
        this.ActionItem('🤔', '歪头', 'waitou')
        this.ActionItem('🙆', '伸懒腰', 'shenlanyao')
        this.ActionItem('💦', '甩水', 'shuaishui')
        this.ActionItem('🤝', '摸手', 'moshou')
        this.ActionItem('👋', '摸头', 'motou')
      }
      .padding({ left: 8, right: 8 })
    }
    .scrollable(ScrollDirection.Horizontal)
    .scrollBar(BarState.Off)
    .width('100%')
    .padding({ top: 8, bottom: 8 })
    .backgroundColor('#FFFFFF')
    .borderRadius(12)
    .margin({ left: 12, right: 12, bottom: 8 })
  }

  @Builder
  ActionItem(icon: string, label: string, action: string) {
    Column() {
      Text(icon).fontSize(28).margin({ bottom: 2 })
      Text(label).fontSize(12).fontColor('#5D6D7E')
    }
    .width(65)
    .padding({ top: 6, bottom: 6 })
    .backgroundColor('#F8F9FA')
    .borderRadius(20)
    .margin({ right: 8 })
    .onClick(() => {
      // 检查饥饿度
      if (!this.viewModel.checkInteractAndPrompt()) {
        return;
      }
      this.triggerPetAction(action);
      this.viewModel.play(); // 互动增加心情（内部消耗饥饿度）
      
      // 根据动作显示不同的气泡文本
      const actionMessages: Record<string, string> = {
        'sajiao': '撒个娇~主人抱抱🥺',
        'dagun': '打滚翻滚~好玩！🌀',
        'zhuanquan': '转圈圈~追尾巴🔄',
        'yaoweiba': '摇摇尾巴~开心🐕',
        'xiaopao': '奔跑奔跑~活力满满🏃',
        'waitou': '歪头歪头~疑惑？🤔',
        'shenlanyao': '伸懒腰~舒舒服服🙆',
        'shuaishui': '甩甩水~抖一抖💦',
        'moshou': '握握手~好朋友🤝',
        'motou': '摸摸头~好舒服👋'
      };
      
      this.showMoodBubble(actionMessages[action] || `宠物${label}了`, icon);
      this.showActionBar = false;
    })
  }

  // 底部功能按钮
  @Builder
  ActionButtons() {
    Row() {
      Column() {
        Text('🎤').fontSize(28).margin({ bottom: 4 })
        Text('语音').fontSize(12).fontColor('#7F8C8D')
      }
      .width(60)
      .height(70)
      .justifyContent(FlexAlign.Center)
      .borderRadius(12)
      .backgroundColor(this.viewModel.isRecording ? '#FC5C7D' : '#FFFFFF')
      .shadow({ radius: 6, color: 'rgba(0, 0, 0, 0.06)', offsetY: 2 })
      .gesture(
        LongPressGesture({ repeat: false })
          .onAction(() => this.startRecording())
          .onActionEnd(() => this.stopRecording())
      )

      this.ActionButton('🍖', '喂食', () => {
        const before = this.viewModel.petInfo.hunger;
        this.viewModel.feed();
        const after = this.viewModel.petInfo.hunger;
        const added = after - before;
        promptAction.showToast({ message: after === 100 ? '您的爱宠肚子圆滚滚啦' : `喂食成功！饥饿度 +${added}` });
        this.petViewKey++;
      })

      this.ActionButton('🤝', '互动', () => {
        this.showActionBar = !this.showActionBar;
      })

      this.ActionButton('🎒', '装扮', () => {
        promptAction.showToast({ message: '装扮功能开发中' });
      })

      this.ActionButton('📋', '更多', () => {
        promptAction.showToast({ message: '更多功能开发中' });
      })
    }
    .width('100%')
    .padding({
      left: 8,
      right: 8,
      top: 4,
      bottom: 16
    })
    .justifyContent(FlexAlign.SpaceEvenly)
  }

  @Builder
  ActionButton(icon: string, label: string, onClick: () => void) {
    Column() {
      Text(icon).fontSize(28).margin({ bottom: 4 })
      Text(label).fontSize(12).fontColor('#7F8C8D')
    }
    .width(60)
    .height(70)
    .justifyContent(FlexAlign.Center)
    .borderRadius(12)
    .backgroundColor('#FFFFFF')
    .shadow({ radius: 6, color: 'rgba(0, 0, 0, 0.06)', offsetY: 2 })
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
      if (this.showTopBar) {
        this.TopStatusBar()
      }

      // 宠物视图区域（包含心情气泡）
      Stack() {
        // 根据模型模式选择不同的视图组件
        if (this.useStandardModel) {
          // 标准模式：使用3D视频展示
          Video3DView({
            actionCommand: this.petActionCommand,
            onTouchRegion: (region: VideoTouchRegion) => this.handlePetTouch(region),
            petType: this.petType
          })
            .width('100%')
            .height('100%')
        } else {
          // 自定义模式：使用原来的3D WebView展示
          Pet3DView({
            actionCommand: this.petActionCommand,
            onTouchRegion: (region: PetTouchRegion) => this.handlePetTouch(region),
            petType: this.petType
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

      if (this.viewModel.isRecording) {
        this.RecordingIndicator()
      }

      if (this.showActionBar) {
        this.ActionBar()
      }

      Divider().color('#EEEEEE').width('100%').height(1)

      this.ActionButtons()
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#F8F9FA')
  }

  /** 父组件的识别行为命令 → 转成内部命令，交给 Video3DView 的 @Watch 播放 */
  onExternalActionCommand(): void {
    if (this.externalActionCommand && this.externalActionCommand.length > 0) {
      this.petActionCommand = this.externalActionCommand;
    }
  }
}