// Pet3DVideoView.ets
// 3D宠物视频视图组件 - 使用MP4视频展示3D效果
// 用于标准模型模式下的宠物展示和交互

export enum PetTouchRegion {
  HEAD = 'head',
  MIDDLE = 'middle',
  BOTTOM_LEFT = 'bottom_left',
  BOTTOM_RIGHT = 'bottom_right'
}

@Component
export struct Pet3DVideoView {
  @Prop @Watch('onActionCommandChange') actionCommand: string = '';
  @Prop @Watch('onPetTypeChange') petType: string = 'dog'; // 宠物类型：dog/cat，监听变化
  @Prop disableAutoRandom: boolean = false; // YOLO 未检测到宠物时关闭空闲随机循环（避免反复切视频造成卡顿）
  @State currentVideoSrc: Resource = $rawfile('3d/dog_sajiao.mp4'); // 默认视频（初始化前使用）
  @State videoLoop: boolean = true; // 是否循环播放
  private currentAction: string = 'idle';
  private isPlayingOneShot: boolean = false;
  private autoTimer: number = -1;
  private actionTimer: number = -1; // 动作超时检测定时器
  // 自动动作池
  private autoActionPool: string[] = [
    'sajiao', 'zhuanquan', 'xiaopao', 'dagun', 'yaoweiba', 'shuaishui', 'waitou', 'shenlanyao'
  ];
  // 视频控制器
  private videoController: VideoController = new VideoController();
  // 外部传入的触摸回调
  onTouchRegion?: (region: PetTouchRegion) => void;
  // 单次动作视频播完时回调（用于驱动 YOLO 串行播放下一个动作）；不传则走老逻辑回 idle
  onActionFinished?: () => void;
  // 记录动作开始时间
  private actionStartTime: number = 0;
  // 标记是否已经初始化播放
  private hasInitialized: boolean = false;


  private getVideoFileName(action: string): string {
    if (action === 'lie1') {
      return `3d/lie1.mp4`;
    }
    if (action === 'play') {
      return `3d/play.mp4`;
    }
    if (action === 'stand') {
      return `3d/stand.mp4`;
    }
    if (action === 'sleep') {
      return `3d/sleep.mp4`;
    }
    return `3d/${this.petType}_${action}.mp4`;
  }

  aboutToAppear(): void {
    console.info(`[Pet3DVideoView] aboutToAppear - 宠物类型: ${this.petType} actionCommand="${this.actionCommand}"`);
    // 直接初始化播放，此时 @Prop 已接收到 HomeTab 传入的 petType 值
    this.hasInitialized = true;
    this.playIdle();
    this.startAutoRandomLoop();
  }

  // 监听 petType 变化，切换宠物类型时重新播放
  onPetTypeChange(): void {
    console.info(`[Pet3DVideoView] petType 变化: ${this.petType}, 已初始化: ${this.hasInitialized}`);
    if (!this.hasInitialized) {
      // 首次初始化（兜底）
      this.hasInitialized = true;
      this.playIdle();
      this.startAutoRandomLoop();
    } else {
      // petType 切换（如从狗换成猫），立即播放新类型的首页视频
      console.info(`[Pet3DVideoView] 宠物类型切换为: ${this.petType}，重新播放视频`);
      this.playIdle();
    }
  }

  aboutToDisappear(): void {
    if (this.autoTimer !== -1) {
      clearInterval(this.autoTimer);
      this.autoTimer = -1;
    }
    if (this.actionTimer !== -1) {
      clearTimeout(this.actionTimer);
      this.actionTimer = -1;
    }
  }

  // 启动自动随机动作循环
  private startAutoRandomLoop(): void {
    this.autoTimer = setInterval(() => {
      // 当外部 YOLO 传入 disableAutoRandom=true 时（如未检测到宠物），停止空闲随机循环
      // 避免每 6 秒切一次视频造成卡顿
      if (this.disableAutoRandom) {
        return;
      }
      if (!this.isPlayingOneShot && this.currentAction === 'idle') {
        // 30%概率触发随机动作
        if (Math.random() < 0.3) {
          this.playRandomAutoAction();
        }
      }
    }, 6000); // 每6秒检查一次
  }

  // 播放随机自动动作
  private playRandomAutoAction(): void {
    // 自动动作只在空闲时播放，不打断用户操作
    if (this.isPlayingOneShot) {
      return; 
    }
    
    const randomIndex = Math.floor(Math.random() * this.autoActionPool.length);
    const action = this.autoActionPool[randomIndex];
    this.playAction(action, false); // 自动动作不需要中断模式
  }


  // 处理触摸区域事件
  private handleTouchRegion(region: PetTouchRegion): void {
    // 检查状态，如果卡住则强制重置
    this.checkAndResetStuckState();

    // ✅ 移除 isPlayingOneShot 阻塞，允许触摸中断当前动作

    let action: string = '';
    switch (region) {
      case PetTouchRegion.HEAD:
        action = 'motou';
        break;
      case PetTouchRegion.MIDDLE:
        action = 'dagun';
        break;
      case PetTouchRegion.BOTTOM_LEFT:
        action = 'moshou';
        break;
      case PetTouchRegion.BOTTOM_RIGHT:
        action = 'yaoweiba';
        break;
    }

    if (action !== '') {
      this.playAction(action, true); // ✅ 触摸也支持中断模式
    }

    // 调用外部传入的回调
    if (this.onTouchRegion) {
      this.onTouchRegion(region);
    }
  }

  // 检查并重置卡住的状态
  private checkAndResetStuckState(): void {
    if (this.isPlayingOneShot && this.actionStartTime > 0) {
      const now = Date.now();
      const elapsed = now - this.actionStartTime;
      // 如果动作已经超过10秒，认为卡住了，强制恢复待机
      if (elapsed > 10000) {
        console.warn(`[Pet3DVideoView] 检测到视频卡住，强制重置，已运行 ${elapsed}ms`);
        this.forceResetToIdle();
      }
    }
  }

  // 强制重置到待机状态
  private forceResetToIdle(): void {
    this.isPlayingOneShot = false;
    this.actionStartTime = 0;
    if (this.actionTimer !== -1) {
      clearTimeout(this.actionTimer);
      this.actionTimer = -1;
    }
    // 延迟一下确保视频控制器重置
    setTimeout(() => {
      this.playIdle();
    }, 100);
  }

  // 监听外部动作命令变化
  onActionCommandChange(): void {
    // 检查状态，如果卡住则强制重置
    this.checkAndResetStuckState();

    const cmd = this.actionCommand;
    if (!cmd || cmd.length === 0) {
      return;
    }

    let actionName = '';
    if (cmd.startsWith('lie1')) {
      actionName = 'lie1';
    } else if (cmd.startsWith('stand')) {
      actionName = 'stand';
    } else if (cmd.startsWith('sleep')) {
      actionName = 'sleep';
    } else if (cmd.startsWith('play')) {
      actionName = 'play';
    } else if (cmd.startsWith('sajiao')) {
      actionName = 'sajiao';
    } else if (cmd.startsWith('dagun')) {
      actionName = 'dagun';
    } else if (cmd.startsWith('zhuanquan')) {
      actionName = 'zhuanquan';
    } else if (cmd.startsWith('yaoweiba')) {
      actionName = 'yaoweiba';
    } else if (cmd.startsWith('xiaopao')) {
      actionName = 'xiaopao';
    } else if (cmd.startsWith('waitou')) {
      actionName = 'waitou';
    } else if (cmd.startsWith('shenlanyao')) {
      actionName = 'shenlanyao';
    } else if (cmd.startsWith('shuaishui')) {
      actionName = 'shuaishui';
    } else if (cmd.startsWith('moshou')) {
      actionName = 'moshou';
    } else if (cmd.startsWith('motou')) {
      actionName = 'motou';
    } else if (cmd.startsWith('weiqu')) {
      actionName = 'weiqu'; // 委屈动作
    }

    if (actionName === '') {
      console.warn(`[Pet3DVideoView] ⚠️ 未匹配到动作 cmd=${cmd}`);
      return;
    }

    console.info(`[Pet3DVideoView] 📥 收到动作命令: ${cmd} → 动作: ${actionName}（isPlayingOneShot=${this.isPlayingOneShot}）`);
    // ✅ 允许中断当前动作，直接播放新动作
    this.playAction(actionName, true); // isInterrupt=true 表示中断模式
  }

  // 简单的视频存在性检查（实际通过错误处理）
  private videoExists(action: string): boolean {
    // 猫和狗3D视频完全对应：dagun, moshou, motou, sajiao, shenlanyao, shouye, shuaishui, waitou, weiqu, xiaopao, yaoweiba, zhuanquan
    // lie1 / play / stand / sleep 是 rawfile 里的特殊资源（无 petType 前缀）
    const existingActions = [
      'dagun', 'moshou', 'motou', 'sajiao', 'shenlanyao', 'shouye', 'shuaishui', 'waitou', 'weiqu', 'xiaopao', 'yaoweiba', 'zhuanquan',
      'lie1', 'play', 'stand', 'sleep'
    ];
    return existingActions.includes(action);
  }

  // 播放待机动画
  playIdle(): void {
    // 如果正在播放 one-shot 动作视频，不覆盖（等它自然结束）
    if (this.isPlayingOneShot) {
      console.info(`[Pet3DVideoView] ⏭️ 跳过 playIdle（正在播放 one-shot: ${this.currentAction}）`);
      return;
    }
    // 清除所有定时器
    if (this.actionTimer !== -1) {
      clearTimeout(this.actionTimer);
      this.actionTimer = -1;
    }

    this.currentAction = 'idle';
    this.isPlayingOneShot = false;
    this.actionStartTime = 0;

    // 设置待机视频（循环播放） - 用首页视频作为待机，猫狗都有
    const idleVideo = this.getVideoFileName('shouye');
    this.currentVideoSrc = $rawfile(idleVideo);
    this.videoLoop = true;

    // 启动视频播放
    this.videoController.start();
    console.info(`[Pet3DVideoView] 播放待机视频: ${idleVideo}`);
  }

  // 播放指定动作视频
  playAction(action: string, isInterrupt: boolean = false): void {
    // ✅ 移除 isPlayingOneShot 阻塞检查，允许随时中断

    // 清除之前的超时定时器
    if (this.actionTimer !== -1) {
      clearTimeout(this.actionTimer);
      this.actionTimer = -1;
    }

    this.currentAction = action;
    this.isPlayingOneShot = true;
    this.actionStartTime = Date.now();

    // 设置动作视频(单次播放)
    const videoFile = this.getVideoFileName(action);
    this.currentVideoSrc = $rawfile(videoFile);
    this.videoLoop = false;

    // 启动视频播放（先停止再启动，确保切换流畅）
    if (isInterrupt) {
      console.info(`[Pet3DVideoView] 🔄 中断当前动作，立即切换到新动作: ${videoFile}`);
      try {
        this.videoController.stop();
      } catch (e) {
        console.warn('[Pet3DVideoView] 停止视频时出错:', e);
      }
    }
    
    this.videoController.start();
    console.info(`[Pet3DVideoView] ▶️ 播放动作视频: ${videoFile}`);

    // 添加10秒超时保护，如果视频卡住也通知父组件继续
    this.actionTimer = setTimeout(() => {
      console.warn(`[Pet3DVideoView] 动作 ${action} 超时(10秒)`);
      if (this.onActionFinished) {
        this.onActionFinished();
      } else {
        this.forceResetToIdle();
      }
    }, 10000);
  }

  build() {
    Stack() {
      // 播放视频
      Video({
        src: this.currentVideoSrc,
        controller: this.videoController
      })
        .muted(true)
        .autoPlay(true)
        .controls(false)
        .loop(this.videoLoop)
        .objectFit(ImageFit.Contain) // 使用Contain保持比例
        .width('100%')
        .height('100%')
        .backgroundColor('#F0F5FF') // 浅蓝色背景
        .onFinish(() => {
          // 视频播放结束后，如果不是待机状态，优先回调给父组件
          if (this.isPlayingOneShot && this.currentAction !== 'idle') {
            console.info(`[Pet3DVideoView] 单次动作视频播放完成: ${this.currentAction}`);
            if (this.onActionFinished) {
              this.onActionFinished();
            } else {
              this.playIdle();
            }
          }
        })
        .onError(() => {
          // 视频播放错误处理（OpenHarmony Video组件onError不支持参数）
          console.error('[Pet3DVideoView] 视频播放发生错误');
          // 如果是某个动作视频不存在，尝试播放默认视频
          if (this.currentAction !== 'idle') {
            console.warn(`[Pet3DVideoView] 动作 ${this.currentAction} 视频可能不存在，恢复待机`);
          }
          this.forceResetToIdle();
          // 通知父组件当前动作已结束，避免 yolo3DIsPlaying 永远为 true 导致死锁
          if (this.onActionFinished) {
            this.onActionFinished();
          }
        })

      // 热区触摸层（透明覆盖层）
      Column() {
        // 头部触摸区域（40%高度）
        Row()
          .width('100%')
          .height('40%')
          .backgroundColor(Color.Transparent)
          .onClick(() => this.handleTouchRegion(PetTouchRegion.HEAD))

        // 中间触摸区域（40%高度）
        Row()
          .width('100%')
          .height('40%')
          .backgroundColor(Color.Transparent)
          .onClick(() => this.handleTouchRegion(PetTouchRegion.MIDDLE))

        // 底部触摸区域（20%高度），分为左右两部分
        Row() {
          // 左下区域
          Row()
            .width('50%')
            .height('100%')
            .backgroundColor(Color.Transparent)
            .onClick(() => this.handleTouchRegion(PetTouchRegion.BOTTOM_LEFT))

          // 右下区域
          Row()
            .width('50%')
            .height('100%')
            .backgroundColor(Color.Transparent)
            .onClick(() => this.handleTouchRegion(PetTouchRegion.BOTTOM_RIGHT))
        }
        .width('100%')
        .height('20%')
      }
      .width('100%')
      .height('100%')
    }
    .width('100%')
    .height('100%')
  }
}
