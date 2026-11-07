// tabs/ChatTab.ets
import { ChatViewModel, ChatMessage } from '../features/chat/viewmodel/ChatViewModel';
import { promptAction } from '@kit.ArkUI';
import { abilityAccessCtrl, PermissionRequestResult, Permissions, common } from '@kit.AbilityKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { K1SpeechService, RecognitionResult } from '../features/chat/services/K1SpeechService';
import { preferences } from '@kit.ArkData';
import { AppConstants } from '../common/constants/AppConstants';
import { PetDataHub } from '../common/services/PetDataHub';
import { RemoteConfig } from '../common/config/RemoteConfig';

@Component
export struct ChatTab {
  @State viewModel: ChatViewModel = new ChatViewModel();
  @State inputText: string = '';
  @State petType: string = 'cat'; // 宠物类型
  @State petName: string = '旺财'; // 宠物名称
  @State petBirthDate: string = ''; // 宠物出生日期
  @State petGender: string = 'female'; // 宠物性别
  @State petBreedName: string = ''; // 宠物品种名称
  @State showHistoryDialog: boolean = false; // 是否显示历史记录弹窗
  @State historyMessages: ChatMessage[] = []; // 历史记录
  // 四路数据的状态条。每 5 秒刷一次，只显示「有数据 / 未接入」，
  // 不在界面上编造任何没采到的数值。
  @State hubRows: string[] = ['环境 未接入', '视觉 未接入', '叫声 未接入'];
  @State hubUpdatedText: string = '尚未刷新';
  @State showStatusPanel: boolean = true;
  // LLM 网关的身份（k1 / local-ollama / pc-mock）。
  // 必须是 @State：探测是异步的，探测完成时气泡早就渲染过了；
  // 不做成状态就没有重绘触发，来源标注会一直停在首帧的旧值上（踩过）。
  @State llmEngine: string = '';
  private hub: PetDataHub = PetDataHub.getInstance();
  private statusTimer: number = -1;
  private scroller: Scroller = new Scroller();
  private context: common.UIAbilityContext = getContext(this) as common.UIAbilityContext;
  private speechService: K1SpeechService = new K1SpeechService();

  aboutToAppear(): void {
    // 消息发送后自动落盘，必须先挂上（开场那条也要保存）
    this.viewModel.onMessageSent = () => {
      this.saveChatHistory();
    };

    // 宠物信息和历史必须先加载完再分析，否则开场那条会用默认的「旺财 / cat」，
    // 用户建档写的明明是别的名字，宠物却自称旺财，一眼假。
    this.bootstrapChat();

    this.initSpeechAndPermission();

    // 状态条 1 秒刷新一次。检测端现在跑到 ~20 fps，界面跟不上就白搭。
    // 两个 GET 都是「拉缓存值」（板端只做序列化，几毫秒），1 Hz 的代价可以忽略。
    this.statusTimer = setInterval(() => {
      this.refreshStatusPanel();
    }, 1000);
  }

  private async bootstrapChat(): Promise<void> {
    await this.loadPetInfo();
    await this.loadChatHistory();

    await this.refreshStatusPanel();

    // 没有历史记录时主动做一次现状分析：既是引导，也是一次真实的 K1 推理，
    // 能当场证明算力链路通不通，而不是靠一句写死的欢迎语假装正常。
    const done: boolean = await this.viewModel.summarizeStatus();
    if (done) {
      await this.saveChatHistory();
      this.scrollToBottom();
    }
  }

  /** 刷一次感知，更新底部状态条。任何一路失败都如实写「未接入」。 */
  private async refreshStatusPanel(): Promise<void> {
    try {
      await this.hub.refresh();
      // 直接赋新数组（而不是原地改），确保 @State 触发重绘
      this.hubRows = this.hub.statusRows();
      // 网关身份可能在首次探测后才就绪，这里持续同步，标注才会及时纠正
      this.llmEngine = this.hub.llmEngine;
      if (this.hub.lastRefreshAt > 0) {
        const sec: number = Math.max(0, Math.round((Date.now() - this.hub.lastRefreshAt) / 1000));
        this.hubUpdatedText = sec <= 1 ? '刚刚更新' : `${sec} 秒前更新`;
      } else {
        this.hubUpdatedText = '未取到数据';
      }
    } catch (e) {
      console.warn('[ChatTab] 状态刷新失败:', JSON.stringify(e));
    }
  }

  // 加载宠物信息（类型、名称、出生日期、性别、品种）
  private async loadPetInfo(): Promise<void> {
    try {
      const prefs = await preferences.getPreferences(this.context, AppConstants.PREF_NAME);
      // 加载宠物类型
      const type = await prefs.get('petType', 'cat') as string;
      this.petType = type;
      // 加载宠物名称
      const name = await prefs.get('petName', '旺财') as string;
      this.petName = name;
      // 加载宠物出生日期
      const birthDate = await prefs.get('petBirthDate', '') as string;
      this.petBirthDate = birthDate;
      // 加载宠物性别
      const gender = await prefs.get('petGender', 'female') as string;
      this.petGender = gender;
      // 加载宠物品种名称
      const breedName = await prefs.get('petBreedName', '') as string;
      this.petBreedName = breedName;
      // 将所有宠物信息传递给 ViewModel
      this.updateViewModelPetInfo();
      console.info(`[ChatTab] 读取到宠物信息 - 名称: ${this.petName}, 类型: ${this.petType}, 生日: ${this.petBirthDate}, 性别: ${this.petGender}, 品种: ${this.petBreedName}`);
    } catch (error) {
      console.error('[ChatTab] 读取宠物信息失败', JSON.stringify(error));
    }
  }

  // 更新 ViewModel 中的宠物信息
  private updateViewModelPetInfo(): void {
    this.viewModel.petName = this.petName;
    this.viewModel.petType = this.petType;
    this.viewModel.petBirthDate = this.petBirthDate;
    this.viewModel.petGender = this.petGender;
    this.viewModel.petBreedName = this.petBreedName;
  }

  // 加载聊天历史记录
  private async loadChatHistory(): Promise<void> {
    try {
      const prefs = await preferences.getPreferences(this.context, AppConstants.PREF_NAME);
      const historyJson = await prefs.get('chatHistory', '[]') as string;
      const historyData: ChatMessage[] = JSON.parse(historyJson) as ChatMessage[];
      this.historyMessages = historyData as ChatMessage[];
      // 将历史记录加载到 ViewModel
      this.viewModel.messages = [...this.historyMessages];
      console.info(`[ChatTab] 加载了 ${this.historyMessages.length} 条聊天历史记录`);
    } catch (error) {
      console.error('[ChatTab] 加载聊天历史记录失败', JSON.stringify(error));
    }
  }

  // 保存聊天历史记录
  private async saveChatHistory(): Promise<void> {
    try {
      const prefs = await preferences.getPreferences(this.context, AppConstants.PREF_NAME);
      const historyJson = JSON.stringify(this.viewModel.messages);
      await prefs.put('chatHistory', historyJson);
      await prefs.flush();
      this.historyMessages = [...this.viewModel.messages];
      // 顺手滚到底。以前消息确实进了数组（日志能看到「保存了 N 条」），
      // 但视图停在顶部，用户发完看不到自己那句话，会误判成「没发出去」。
      this.scrollToBottom();
      console.info(`[ChatTab] 保存了 ${this.viewModel.messages.length} 条聊天历史记录`);
    } catch (error) {
      console.error('[ChatTab] 保存聊天历史记录失败', JSON.stringify(error));
    }
  }

  // 清除聊天历史记录
  private async clearChatHistory(): Promise<void> {
    try {
      const prefs = await preferences.getPreferences(this.context, AppConstants.PREF_NAME);
      await prefs.delete('chatHistory');
      await prefs.flush();
      this.historyMessages = [];
      console.info('[ChatTab] 聊天历史记录已清除');
    } catch (error) {
      console.error('[ChatTab] 清除聊天历史记录失败', JSON.stringify(error));
    }
  }

  aboutToDisappear(): void {
    // 页面切走要停掉定时器，否则 Tabs 缓存着子组件，
    // 后台还会每 5 秒发两次 HTTP，纯浪费板子的电和带宽。
    if (this.statusTimer !== -1) {
      clearInterval(this.statusTimer);
      this.statusTimer = -1;
    }
    this.speechService.destroy();
    this.viewModel.destroy();
  }

  async initSpeechAndPermission(): Promise<void> {
    // 请求麦克风权限
    this.requestMicrophonePermission();
    // 初始化语音服务
    const success = await this.speechService.init(this.context);
    if (success) {
      this.speechService.setCallbacks({
        onStart: () => {
          this.viewModel.isRecording = true;
          this.viewModel.recognizingText = '';
        },
        onResult: (result: RecognitionResult) => {
          console.info(`[ChatTab] 收到识别结果: "${result.text}", isFinal: ${result.isFinal}`);
          this.viewModel.recognizingText = result.text;
          console.info(`[ChatTab] recognizingText 已更新为: "${this.viewModel.recognizingText}"`);
        },
        onComplete: (finalText: string) => {
          console.info(`[ChatTab] 识别完成,最终文本: "${finalText}"`);
          this.viewModel.isRecording = false;
          this.viewModel.recognizingText = '';
          if (finalText && finalText.trim().length > 0) {
            console.info(`[ChatTab] 发送消息到聊天界面`);
            this.viewModel.sendMessage(finalText.trim());
          } else {
            console.warn('[ChatTab] 最终文本为空,不发送消息');
          }
        },
        onError: (code: number, message: string) => {
          this.viewModel.isRecording = false;
          this.viewModel.recognizingText = '';
          console.error(`[ChatTab] 语音识别错误: ${code} - ${message}`);
        }
      });
    } else {
      promptAction.showToast({ message: '语音服务初始化失败' });
    }
  }

  private requestMicrophonePermission(): void {
    const atManager = abilityAccessCtrl.createAtManager();
    const permissions: Array<Permissions> = ['ohos.permission.MICROPHONE'];
    atManager.requestPermissionsFromUser(getContext(this), permissions)
      .then((result: PermissionRequestResult) => {
        if (result.authResults[0] !== abilityAccessCtrl.GrantStatus.PERMISSION_GRANTED) {
          promptAction.showToast({ message: '需要麦克风权限才能语音对话' });
        }
      })
      .catch((err: BusinessError) => {
        console.error('[ChatTab] 权限申请失败:', err.message);
      });
  }

  private startRecording(): void {
    this.speechService.startListening();
  }

  private stopRecording(): void {
    this.speechService.stopListening();
  }

  sendTextMessage(): void {
    if (this.inputText.trim().length > 0 && !this.viewModel.isThinking) {
      this.viewModel.sendMessage(this.inputText.trim());
      this.inputText = '';
      this.scrollToBottom();
    }
  }

  private scrollToBottom(): void {
    setTimeout(() => {
      this.scroller.scrollEdge(Edge.Bottom);
    }, 100);
  }

  @Builder
  TitleBar() {
    Row() {
      Image($r('app.media.liaotian'))
        .width(22)
        .onClick(() => {
          this.showHistoryDialog = true;
        })
      Text('与宠物聊天')
        .fontSize(20)
        .fontWeight(FontWeight.Bold)
        .fontColor('#2C3E50')
        .layoutWeight(1)
        .width('100%')
        .textAlign(TextAlign.Center)
      // Blank()
      Image($r('app.media.shanchu'))
        .width(22)
        .onClick(() => {
          this.viewModel.clearHistory();
          this.viewModel.resetSummarizeCooldown();
          this.clearChatHistory();
          promptAction.showToast({ message: '对话已清空' });
          // 清空后立刻重新做一次现状分析。
          // 不能指望 aboutToAppear 再触发：Tabs 会缓存子组件，切走切回不走生命周期，
          // 删掉硬编码欢迎语之后，不补这一步界面就只剩空白。
          this.viewModel.summarizeStatus().then((done: boolean) => {
            if (done) {
              this.saveChatHistory();
              this.scrollToBottom();
            }
          });
        })
    }
    .width('100%')
    .padding({
      left: 20,
      right: 20,
      top: 12,
      bottom: 12
    })
    .backgroundColor('#FFFFFF')
    .expandSafeArea([SafeAreaType.KEYBOARD])
  }

  @Builder
  MessageBubble(message: ChatMessage) {
    Column() {
      Row() {
        if (message.role === 'user') {
          Blank()
          Text(message.content)
            .fontSize(16)
            .fontColor('#FFFFFF')
            .padding({
              left: 16,
              right: 16,
              top: 10,
              bottom: 10
            })
            .backgroundColor('#6A82FB')
            .borderRadius(20)
          Image($r('app.media.user'))
            .width(40)
            .margin({ left: 8 })
        } else {
          Row() {
            // 根据宠物类型显示不同头像
            Image(this.petType === 'dog' ? $r('app.media.type_dog') : $r('app.media.type_cat'))
              .width(40)
              .margin({ right: 8 })
            Column() {
              Text(message.content)
                .fontSize(16)
                .fontColor('#2C3E50')
                .padding({
                  left: 16,
                  right: 16,
                  top: 10,
                  bottom: 10
                })
                .backgroundColor('#F0F0F0')
                .borderRadius(20)
                .width('100%')
              // 来源标注：模型产出 / 规则兜底必须分得清。
              // 规则文案长得像人话，混在一起显示就等于把模板当分析结果。
              if (message.source !== undefined && message.source.length > 0) {
                Text(this.sourceLabel(message))
                  .fontSize(10)
                  .fontColor('#95A5A6')
                  .margin({ top: 4, left: 8 })
              }
            }
            .width('70%')
            .alignItems(HorizontalAlign.Start)
          }

          Blank()
        }
      }
      .width('100%')
    }
    .width('100%')
    .padding({
      left: 16,
      right: 16,
      top: 8,
      bottom: 8
    })
  }

  @Builder
  RecordingIndicator() {
    Row() {
      Text('🎤').fontSize(28)
      // Image($r('app.media.yuyin'))
      //   .width(25)
        .margin({ right: 8 })
      Text(this.viewModel.recognizingText || '正在聆听...')
        .fontSize(16).fontColor('#6A82FB')
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
    .margin({ left: 16, right: 16, bottom: 8 })
  }

  @Builder
  InputArea() {
    Row() {
      TextInput({ placeholder: '输入消息...', text: this.inputText })
        .placeholderColor('#CCCCCC')
        .fontSize(15)
        .backgroundColor('#F5F5F5')
        .borderRadius(20)
        .height(40)
        .layoutWeight(1)
        .padding({ left: 16, right: 16 })
        .onChange((value: string) => {
          this.inputText = value;
        })
        .onSubmit(() => {
          this.sendTextMessage();
        })

      // Text('📤').fontSize(24)
      Image($r('app.media.fasong'))
        .width(25)
        .margin({ left: 8, right: 8 })
        .onClick(() => {
          this.sendTextMessage();
        })
      // Image($r('app.media.yuyin'))
      //   .width(25)
      Text('🎤')
        .fontSize(25)
        .backgroundColor(this.viewModel.isRecording ? '#FC5C7D' : Color.Transparent)   //'#6A82FB'
        .fontColor('#FFFFFF')
        .width(50)
        .height(50)
        .borderRadius(25)
        .textAlign(TextAlign.Center)
        .shadow({ radius: 4, color: 'rgba(0,0,0,0.1)' })
        .gesture(
          LongPressGesture({ repeat: false })
            .onAction(() => this.startRecording())
            .onActionEnd(() => this.stopRecording())
        )
    }
    .width('100%')
    .padding({
      left: 16,
      right: 16,
      top: 12,
      bottom: 16
    })
    .backgroundColor('#FFFFFF')
  }

  @Builder
  HistoryDialog() {
    if (this.showHistoryDialog) {
      Column() {
        Column() {
          // 标题栏
          Row() {
            Text('📜 聊天历史')
              .fontSize(18)
              .fontWeight(FontWeight.Bold)
              .fontColor('#333333')
            Blank()
            Text('关闭')
              .fontSize(14)
              .fontColor('#666666')
              .onClick(() => {
                this.showHistoryDialog = false;
              })
          }
          .width('100%')
          .padding({ left: 20, right: 20, top: 20, bottom: 12 })
          .borderRadius({ topLeft: 20, topRight: 20 })
          .backgroundColor('#FFFFFF')

          // 历史记录列表
          if (this.historyMessages.length === 0) {
            Column() {
              Text('📭')
                .fontSize(48)
                .margin({ top: 40 })
              Text('暂无聊天记录')
                .fontSize(14)
                .fontColor('#999999')
                .margin({ top: 12 })
            }
            .width('100%')
            .height(300)
            .justifyContent(FlexAlign.Center)
          } else {
            Scroll(this.scroller) {
              Column() {
                ForEach(this.historyMessages, (message: ChatMessage) => {
                  Column() {
                    Row() {
                      Text(message.role === 'user' ? '👤 你' : `🐾 ${this.petName}`)
                        .fontSize(14)
                        .fontColor(message.role === 'user' ? '#6A82FB' : '#FF69B4')
                        .fontWeight(FontWeight.Bold)
                      Blank()
                      // 历史里也必须标来源。规则兜底是模板文案，和模型回答
                      // 混在同一个列表里而不加区分，等于把模板话当模型分析。
                      if (message.source !== undefined && message.source.length > 0) {
                        Text(this.sourceShort(message.source))
                          .fontSize(10)
                          .fontColor('#95A5A6')
                          .margin({ right: 8 })
                      }
                      Text(this.formatTimestamp(message.timestamp))
                        .fontSize(12)
                        .fontColor('#999999')
                    }
                    .width('100%')
                    .margin({ bottom: 8 })

                    Text(message.content)
                      .fontSize(14)
                      .fontColor('#333333')
                      .width('100%')
                      .maxLines(2)
                      .textOverflow({ overflow: TextOverflow.Ellipsis })

                    Divider()
                      .color('#F0F0F0')
                      .margin({ top: 12 })
                  }
                  .width('100%')
                  .padding({ left: 20, right: 20, top: 12, bottom: 12 })
                }, (message: ChatMessage) => message.id)
              }
              .width('100%')
              .padding({ bottom: 20 })
            }
            .width('100%')
            .height(300)
            .scrollBar(BarState.Auto)
            .edgeEffect(EdgeEffect.Spring)
            .scrollBar(BarState.Auto)
            .scrollBarColor('#999999')
            .scrollBarWidth(4)
          }

          // 底部统计
          Row() {
            Text(`共 ${this.historyMessages.length} 条记录`)
              .fontSize(12)
              .fontColor('#999999')
          }
          .width('100%')
          .padding({ left: 20, right: 20, top: 12, bottom: 20 })
          .borderRadius({ bottomLeft: 20, bottomRight: 20 })
          .backgroundColor('#FFFFFF')
        }
        .width('85%')
        .backgroundColor('#FFFFFF')
        .borderRadius(20)
        .shadow({
          radius: 20,
          color: 'rgba(0,0,0,0.2)',
          offsetX: 0,
          offsetY: 5
        })
      }
      .width('100%')
      .height('100%')
      .backgroundColor('rgba(0,0,0,0.5)')
      .justifyContent(FlexAlign.Center)
      .onClick(() => {
        this.showHistoryDialog = false;
      })
    }
  }

  // 格式化时间戳
  private formatTimestamp(timestamp: number): string {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const diffDays = Math.floor(diff / (1000 * 60 * 60 * 24));

    if (diffDays === 0) {
      // 今天
      const hours = date.getHours().toString().padStart(2, '0');
      const minutes = date.getMinutes().toString().padStart(2, '0');
      return `${hours}:${minutes}`;
    } else if (diffDays === 1) {
      // 昨天
      const hours = date.getHours().toString().padStart(2, '0');
      const minutes = date.getMinutes().toString().padStart(2, '0');
      return `昨天 ${hours}:${minutes}`;
    } else if (diffDays < 7) {
      // 本周
      const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
      const hours = date.getHours().toString().padStart(2, '0');
      const minutes = date.getMinutes().toString().padStart(2, '0');
      return `${weekdays[date.getDay()]} ${hours}:${minutes}`;
    } else {
      // 更早
      const month = (date.getMonth() + 1).toString().padStart(2, '0');
      const day = date.getDate().toString().padStart(2, '0');
      return `${month}-${day}`;
    }
  }

  /** 宠物消息的来源标注。三种来源必须能一眼分清。 */
  /** 历史弹窗里的极简来源标记（完整标注太长，这里只标"是谁答的"） */
  private sourceShort(src: string): string {
    if (src === 'model') {
      return '模型';
    }
    if (src === 'kb') {
      return '知识库';
    }
    if (src === 'rule') {
      return '规则兜底';
    }
    if (src === 'nodata') {
      return '无数据';
    }
    return '';
  }

  private sourceLabel(message: ChatMessage): string {
    const src: string = message.source === undefined ? '' : message.source;
    const ms: number = message.elapsedMs === undefined ? 0 : message.elapsedMs;
    const sec: string = (ms / 1000).toFixed(1);
    if (src === 'model') {
      // 只有 engine=k1 才配写「K1」——那是板端 qwen3:0.6b 真跑的。
      // PC 侧跑的是同一份权重，但算力不在板子上，写「本地模型」；
      // 云端替身必须点名，绝不能让它混进「K1 本地模型」这个标签里。
      //
      // 关键：读的是 message.engine（回答当时定格的出处），不是 this.llmEngine。
      // 读实时值会把「替身答的旧消息」在后来的链路切换后改标成「K1 本地模型」，
      // 那是事后伪造来源。实测已踩过：一条 1.1s 的替身气泡被标成了 K1。
      const eng: string = message.engine === undefined ? '' : message.engine;
      if (eng === 'k1') {
        return `K1 本地模型 · ${sec}s · ${RemoteConfig.LLM_MODEL}`;
      }
      if (eng.indexOf('mock') >= 0) {
        return `PC 替身·非板端 · ${sec}s · ${RemoteConfig.LLM_MODEL}`;
      }
      if (eng.length > 0) {
        return `本地模型 · ${sec}s · ${RemoteConfig.LLM_MODEL}`;
      }
      // 产生时没探到引擎（老消息，或那一刻网关不可达）——如实写「未标注」，
      // 不拿当前探测值去补，避免把不知道的事说成知道。
      return `来源未标注 · ${sec}s · ${RemoteConfig.LLM_MODEL}`;
    }
    if (src === 'nodata') {
      return '无数据源 · 未调用模型';
    }
    if (src === 'kb') {
      // 知识库是**检索**不是生成：给的是知识库里已有的条目原文，
      // 模型没参与。标出条目 id 是为了能当场核对（比如 cat_milk）。
      const kid: string = message.kbId === undefined ? '' : message.kbId;
      const tail: string = kid.length > 0 ? ` · ${kid}` : '';
      return `本地知识库 · ${sec}s · 原文未经模型改写${tail}`;
    }
    if (src === 'rule') {
      // 两种「规则兜底」必须分开说：
      //   nopet     → 画面里没宠物，代码直接跳过模型，一次请求都没发出去；
      //   modelfail → 真的请求过，模型超时/输出不可用/与实测数据冲突。
      // 以前一律写「模型未响应」，用户看到会以为算力链路断了，
      // 实际可能只是摄像头前没有宠物。
      const why: string = message.ruleReason === undefined ? '' : message.ruleReason;
      if (why === 'nopet') {
        return '规则兜底 · 画面无宠物，未请求模型';
      }
      return '规则兜底 · 模型未响应';
    }
    return '';
  }

  /**
   * 四路数据状态条。
   *
   * 存在的理由：聊天回答是「基于数据」的，用户必须能看到数据到底有没有来。
   * 这一条同时解决两个老问题——页面上不再有编造的 25.0℃，
   * 也不会出现「明明没数据却答得理直气壮」。
   */
  @Builder
  StatusPanel() {
    Column({ space: 3 }) {
      Row() {
        Text('数据源')
          .fontSize(12)
          .fontWeight(FontWeight.Bold)
          .fontColor('#2C3E50')
        Text(this.showStatusPanel ? '▾' : '▸')
          .fontSize(12)
          .fontColor('#7F8C8D')
          .margin({ left: 4 })
        Blank()
        Text(this.hubUpdatedText)
          .fontSize(10)
          .fontColor('#95A5A6')
      }
      .width('100%')
      .onClick(() => {
        this.showStatusPanel = !this.showStatusPanel;
      })

      if (this.showStatusPanel) {
        ForEach(this.hubRows, (row: string, index: number) => {
          Text(row)
            .fontSize(11)
            .fontColor(row.indexOf('未接入') >= 0 ? '#C0392B' : '#5D6D7E')
            .width('100%')
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
        }, (row: string, index: number) => `${index}_${row}`)
      }
    }
    .width('100%')
    .padding({ left: 16, right: 16, top: 8, bottom: 8 })
    .backgroundColor('#FFFFFF')
  }

  build() {
    Stack() {
      Column() {
        this.TitleBar()
        Divider().color('#EEEEEE').width('100%').height(1)

        Scroll(this.scroller) {
          Column() {
            // 这里原本有一条硬编码欢迎语（「哇～终于见到你啦！我是xxx！」）。
            // 已删除：它会在进页面头几秒显示写死的句子，模型回来才替换，
            // 断链时留下的也是同一句，导致「聊天到底有没有调 K1」从界面上
            // 完全看不出来。现在界面上每一句话都必须来自 messages，来源唯一。

            ForEach(this.viewModel.messages, (message: ChatMessage) => {
              this.MessageBubble(message)
            }, (message: ChatMessage) => `${message.id}|${message.content.length}`)

            if (this.viewModel.isThinking && !this.viewModel.lastReplyHasText()) {
              Row() {
                Image(this.petType === 'dog' ? $r('app.media.type_dog') : $r('app.media.type_cat'))
                  .width(40)
                  .margin({ right: 8 })
                Text('正在思考...').fontSize(14).fontColor('#7F8C8D')
              }
              .width('100%')
              .padding({ left: 16, top: 8, bottom: 8 })
            }
          }
          .width('100%')
          .alignItems(HorizontalAlign.Start)
        }
        .layoutWeight(1)
        .edgeEffect(EdgeEffect.Spring)
        .scrollBar(BarState.Off)
        .align(Alignment.Top)
        .expandSafeArea([SafeAreaType.KEYBOARD])

        if (this.viewModel.isRecording) {
          this.RecordingIndicator()
        }

        this.StatusPanel()

        Divider().color('#EEEEEE').width('100%').height(1)

        this.InputArea()
      }
      .width('100%')
      .height('100%')
      .backgroundColor('#F8F9FA')

      // 历史记录弹窗
      this.HistoryDialog()
    }
    .width('100%')
    .height('100%')
  }
}