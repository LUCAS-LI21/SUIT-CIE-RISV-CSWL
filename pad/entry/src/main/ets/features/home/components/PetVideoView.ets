// features/home/components/PetVideoView.ets
// 自动随机动作 + 四区域触摸（头：摸头，中：打滚，左下：摸手，右下：摇尾巴）
// 使用视频播放组件替代2D帧动画

export enum PetTouchRegion {
  HEAD = 'head',
  MIDDLE = 'middle',
  BOTTOM_LEFT = 'bottom_left',
  BOTTOM_RIGHT = 'bottom_right'
}

@Component
export struct PetVideoView {
  @Prop @Watch('onActionCommandChange') actionCommand: string = '';
  @Prop @Watch('onPetTypeChange') petType: string = 'cat'; // 宠物类型：dog/cat，监听变化
  @State currentVideoSrc: Resource = $rawfile('idle_cat.mp4'); // 当前视频路径
  @State videoLoop: boolean = true; // 视频是否循环播放
  private currentAction: string = 'idle';
  private isPlayingOneShot: boolean = false;
  private autoTimer: number = -1;
  private actionTimer: number = -1; // 动作超时检测定时器
  private autoActionPool: string[] = [
    'sajiao', 'zhuanquan', 'xiaopao', 'waitou', 'shenlanyao', 'shuaishui', 'dagun'
  ];
  // 视频控制器
  private videoController: VideoController = new VideoController();
  // 外部传入的触摸回调
  onTouchRegion?: (region: PetTouchRegion) => void;
  // 记录动作开始时间
  private actionStartTime: number = 0;
  // 标记是否已经初始化播放
  private hasInitialized: boolean = false;

  // 动作对应的视频文件名映射（动态拼接宠物类型）
  private getVideoFileName(action: string): string {
    const baseName = action;
    const suffix = `_${this.petType}.mp4`;
    return `${baseName}${suffix}`;
  }

  aboutToAppear(): void {
    console.info(`[PetVideoView] aboutToAppear - 宠物类型: ${this.petType}`);
    // 不立即播放，等待 petType 更新后再播放
  }

  // 监听 petType 变化，更新后播放视频
  onPetTypeChange(): void {
    console.info(`[PetVideoView] petType 变化: ${this.petType}, 已初始化: ${this.hasInitialized}`);
    if (!this.hasInitialized && this.petType) {
      this.hasInitialized = true;
      this.playIdle();
      this.startAutoRandomLoop();
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

  private startAutoRandomLoop(): void {
    this.autoTimer = setInterval(() => {
      if (!this.isPlayingOneShot && this.currentAction === 'idle') {
        if (Math.random() < 0.5) {
          this.playRandomAutoAction();
        }
      }
    }, 5000);
  }

  private playRandomAutoAction(): void {
    const randomIndex = Math.floor(Math.random() * this.autoActionPool.length);
    const action = this.autoActionPool[randomIndex];
    this.playAction(action);
  }

  // 内部触摸处理：播放动画 + 调用外部回调
  private handleTouchRegion(region: PetTouchRegion): void {
    // 检查状态，如果卡住则强制重置
    this.checkAndResetStuckState();

    if (this.isPlayingOneShot) {
      return;
    }

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
      this.playAction(action);
    }

    // 调用外部传入的回调，让 HomeTab 知道哪个区域被触摸
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
        console.warn(`[PetVideoView] 检测到视频卡住，强制重置，已运行 ${elapsed}ms`);
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

  onActionCommandChange(): void {
    // 检查状态，如果卡住则强制重置
    this.checkAndResetStuckState();

    const cmd = this.actionCommand;
    if (!cmd || cmd.length === 0) {
      return;
    }

    let actionName = '';
    if (cmd.startsWith('sajiao')) {
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
    }

    if (actionName === '' || this.isPlayingOneShot) {
      return;
    }

    this.playAction(actionName);
  }

  playIdle(): void {
    // 清除所有定时器
    if (this.actionTimer !== -1) {
      clearTimeout(this.actionTimer);
      this.actionTimer = -1;
    }

    this.currentAction = 'idle';
    this.isPlayingOneShot = false;
    this.actionStartTime = 0;
    // 设置待机视频(循环播放) - 根据宠物类型动态加载
    const idleVideo = this.getVideoFileName('idle');
    this.currentVideoSrc = $rawfile(idleVideo);
    this.videoLoop = true;
    // 启动视频播放
    this.videoController.start();
    console.info(`[PetVideoView] 播放待机视频: ${idleVideo}`);
  }

  playAction(action: string): void {
    if (this.isPlayingOneShot) {
      return;
    }

    // 清除之前的超时定时器
    if (this.actionTimer !== -1) {
      clearTimeout(this.actionTimer);
      this.actionTimer = -1;
    }

    this.currentAction = action;
    this.isPlayingOneShot = true;
    this.actionStartTime = Date.now(); // 记录动作开始时间
    // 设置动作视频(单次播放) - 根据宠物类型动态加载
    const actionVideo = this.getVideoFileName(action);
    this.currentVideoSrc = $rawfile(actionVideo);
    this.videoLoop = false;

    // 启动视频播放，视频结束后会自动触发 onFinish 恢复待机
    this.videoController.start();
    console.info(`[PetVideoView] 播放动作视频: ${actionVideo}`);

    // 添加10秒超时保护，如果视频卡住自动恢复
    this.actionTimer = setTimeout(() => {
      console.warn(`[PetVideoView] 动作 ${action} 超时(10秒)，强制恢复待机`);
      this.forceResetToIdle();
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
        .objectFit(ImageFit.Cover)
        .width('100%')
        .height('100%')
        .onFinish(() => {
          // 视频播放结束后，如果不是待机状态，则恢复待机
          if (this.isPlayingOneShot && this.currentAction !== 'idle') {
            this.playIdle();
          }
        })
        .onError(() => {
          // 视频播放错误，强制恢复待机
          console.error(`[PetVideoView] 视频播放错误`);
          this.forceResetToIdle();
        })

      // 热区触摸层
      Column() {
        Row()
          .width('100%')
          .height('40%')
          .backgroundColor('rgba(0,0,0,0)')
          .onClick(() => this.handleTouchRegion(PetTouchRegion.HEAD))

        Row()
          .width('100%')
          .height('40%')
          .backgroundColor('rgba(0,0,0,0)')
          .onClick(() => this.handleTouchRegion(PetTouchRegion.MIDDLE))

        Row() {
          Row()
            .width('50%')
            .height('100%')
            .backgroundColor('rgba(0,0,0,0)')
            .onClick(() => this.handleTouchRegion(PetTouchRegion.BOTTOM_LEFT))

          Row()
            .width('50%')
            .height('100%')
            .backgroundColor('rgba(0,0,0,0)')
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
