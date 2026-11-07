// Pet3DView.ets
// 3D宠物互动视图组件 - 支持用户生成的3D模型 + 默认模型
// 策略：先用原生组件秒开 → 后台加载3D模型 → 无缝切换

import { webview } from '@kit.ArkWeb';
import { fileIo } from '@kit.CoreFileKit';
import { util } from '@kit.ArkTS';

export enum PetTouchRegion {
  HEAD = 'head',
  MIDDLE = 'middle',
  BOTTOM_LEFT = 'bottom_left',
  BOTTOM_RIGHT = 'bottom_right'
}

@Component
export struct Pet3DView {
  @Prop @Watch('onActionCommandChange') actionCommand: string = '';
  @Prop petType: string = 'dog';
  onTouchRegion?: (region: PetTouchRegion) => void;
  @State modelPath: string = '';
  @State useCustomModel: boolean = false;
  @State showNativePet: boolean = true; // 默认显示原生宠物（秒开）
  @State showWebView: boolean = false;  // 3D加载完成后再显示
  @State loadingProgress: string = '正在准备...';
  private webController: webview.WebviewController = new webview.WebviewController();
  private isWebReady: boolean = false;
  private pendingAction: string = '';
  private isModelLoaded: boolean = false;

  aboutToAppear(): void {
    // 从全局存储获取模型配置
    this.modelPath = AppStorage.get<string>('petModelPath') || '';
    this.useCustomModel = AppStorage.get<boolean>('useCustomModel') || false;

    console.info(`[Pet3DView] 初始化:`);
    console.info(`  - useCustomModel: ${this.useCustomModel}`);
    console.info(`  - modelPath: ${this.modelPath}`);

    // 延迟初始化 WebView（避免阻塞UI）
    setTimeout(() => {
      this.initWebViewer();
    }, 100);
  }

  // ========== 异步初始化 WebView（不阻塞主线程）==========
  private async initWebViewer(): Promise<void> {
    try {
      console.info('[Pet3DView] 开始后台加载3D引擎...');
      this.loadingProgress = '正在加载3D引擎...';

      // 标记可以显示 WebView
      this.showWebView = true;

      // 给 WebView 初始化时间
      setTimeout(() => {
        if (!this.isModelLoaded) {
          console.info('[Pet3DView] 3D加载超时检查...');
          this.loadingProgress = this.useCustomModel ? '点击查看您的3D模型' : '点击查看3D效果';
        }
      }, 5000);

    } catch (e) {
      console.error('[Pet3DView] 初始化失败:', JSON.stringify(e));
    }
  }

  onPageEnd(): void {
    this.isWebReady = true;
    console.info('[Pet3DView] WebView就绪，开始加载模型');

    // 加载模型：优先使用自定义模型，否则加载默认模型
    if (this.useCustomModel && this.modelPath && this.modelPath.length > 0) {
      this.loadCustomModel();
    } else {
      this.tryLoadDefaultGLB();
    }

    // 处理待执行的动画命令
    if (this.pendingAction) {
      setTimeout(() => {
        this.onActionCommandChange();
        this.pendingAction = '';
      }, 500);
    }
  }

  // ========== 加载用户生成的自定义GLB模型 ==========
  private async loadCustomModel(): Promise<void> {
    try {
      console.info(`[Pet3DView] 正在加载用户生成的3D模型: ${this.modelPath}`);
      this.loadingProgress = '正在加载您的3D模型...';

      // 检查文件是否存在
      const exists = await this.fileExists(this.modelPath);
      if (!exists) {
        console.warn('[Pet3DView] 自定义模型文件不存在，回退到默认模型');
        this.tryLoadDefaultGLB();
        return;
      }

      // 读取文件并转换为Data URL
      const dataUrl = await this.localFileToDataUrl(this.modelPath, 'model/gltf-binary');
      
      if (dataUrl.length === 0) {
        throw new Error('文件读取失败或为空');
      }

      console.info(`[Pet3DView] ✅ 自定义模型读取完成, 大小: ${Math.round(dataUrl.length / 1024)} KB`);
      
      this.loadingProgress = '渲染您的3D模型...';
      
      // 调用WebView加载模型
      setTimeout(() => {
        this.webController.runJavaScript(`
          window._glbUrl='${dataUrl}';
          loadModelFromUrl(window._glbUrl);
          console.log('开始加载用户自定义GLB模型');
        `);
        
        // 检查加载结果
        setTimeout(() => {
          if (!this.isModelLoaded) {
            console.warn('[Pet3DView] 自定义模型加载可能较慢，继续等待...');
            this.loadingProgress = '3D模型渲染中...';
          }
        }, 3000);
        
      }, 1000);

    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      console.error(`[Pet3DView] ❌ 自定义模型加载失败: ${errorMsg}`);
      this.loadingProgress = '模型加载失败';
      
      // 回退到默认模型
      setTimeout(() => {
        console.log('[Pet3DView] 尝试回退到默认模型...');
        this.tryLoadDefaultGLB();
      }, 1500);
    }
  }

  // ========== 检查文件是否存在 ==========
  private async fileExists(filePath: string): Promise<boolean> {
    try {
      await fileIo.access(filePath);
      return true;
    } catch (e) {
      return false;
    }
  }

  // ========== 本地文件 → Data URL ==========
  private async localFileToDataUrl(filePath: string, mimeType: string): Promise<string> {
    try {
      const file = fileIo.openSync(filePath, fileIo.OpenMode.READ_ONLY);
      const stat = fileIo.statSync(filePath);
      const buffer = new ArrayBuffer(stat.size);
      fileIo.readSync(file.fd, buffer);
      fileIo.closeSync(file);

      const uint8Array = new Uint8Array(buffer);
      const base64Helper = new util.Base64Helper();
      const base64 = base64Helper.encodeToStringSync(uint8Array);
      
      return `data:${mimeType};base64,${base64}`;
    } catch (err) {
      console.error('[Pet3DView] 文件转DataUrl失败:', JSON.stringify(err));
      return '';
    }
  }

  // ========== 加载默认GLB模型（rawfile）==========
  private async tryLoadDefaultGLB(): Promise<void> {
    try {
      console.info('[Pet3DView] 加载默认GLB模型...');
      const glbUrl = await this.fileToDataUrl('cute_puppy_dog_low_poly_rigged.glb', 'model/gltf-binary');
      console.info('[Pet3DView] GLB读取完成，大小:', Math.round(glbUrl.length / 1024), 'KB');

      this.loadingProgress = '渲染3D模型...';
      this.webController.runJavaScript(`window._glbUrl='${glbUrl}';loadModelFromUrl(window._glbUrl);`);

      setTimeout(() => {
        if (!this.isModelLoaded) {
          console.warn('[Pet3DView] 3D加载可能失败，保持原生显示');
        }
      }, 3000);

    } catch (e) {
      console.error('[Pet3DView] GLB加载失败:', JSON.stringify(e));
      this.loadingProgress = '使用简化模式';
    }
  }

  aboutToDisappear(): void {
    this.isWebReady = false;
  }

  // ========== 处理动画命令 ==========
  onActionCommandChange(): void {
    const cmd = this.actionCommand;
    if (!cmd || cmd.length === 0) return;

    let actionName = '';
    const actions = ['sajiao', 'dagun', 'zhuanquan', 'yaoweiba', 'xiaopao', 'waitou', 'shenlanyao', 'shuaishui', 'moshou', 'motou'];
    for (const action of actions) {
      if (cmd.startsWith(action)) { actionName = action; break; }
    }

    if (actionName && this.isWebReady) {
      this.webController.runJavaScript(`triggerAction('${actionName}')`);
    } else if (actionName) {
      this.pendingAction = cmd;
    }
  }

  // ========== rawfile → base64 ==========
  private async fileToDataUrl(rawPath: string, mimeType: string): Promise<string> {
    const resMgr = getContext(this).resourceManager;
    const rawBuffer: Uint8Array = await resMgr.getRawFileContent(rawPath) as Uint8Array;
    const base64: string = new util.Base64Helper().encodeToStringSync(rawBuffer);
    return `data:${mimeType};base64,${base64}`;
  }

  build() {
    Stack() {
      // 🐕 原生宠物视图（秒开）
      if (this.showNativePet) {
        this.NativePetView()
      }

      // 🌐 WebView（后台加载3D）
      if (this.showWebView) {
        Web({
          src: $rawfile('threejs_viewer.html'),
          controller: this.webController
        })
          .width('100%')
          .height('100%')
          .onPageEnd(() => this.onPageEnd())
          .onConsole((event) => {
            console.debug(`[Pet3DView-Console] ${event.message}`);
            
            // 监听Three.js的模型加载成功事件
            if (String(event.message).includes('MODEL_LOADED_SUCCESS')) {
              console.info('[Pet3DView] ✅ 3D模型加载成功！');
              this.isModelLoaded = true;
              // 隐藏原生视图，显示3D视图
              this.showNativePet = false;
            }
            
            return false;
          })
          .javaScriptAccess(true)
          .domStorageAccess(true)
          .fileAccess(true)
          .mixedMode(MixedMode.All)
          .cacheMode(CacheMode.Default)
          .backgroundColor(Color.Transparent)
          .opacity(this.isModelLoaded ? 1 : 0) // 3D加载完成前透明
      }
    }
    .width('100%')
    .height('100%')
  }

  // ========== 原生宠物组件（秒开）==========
  @Builder
  NativePetView() {
    Column() {
      Text(this.useCustomModel ? '🐕' : '🐕')
        .fontSize(80)
        .animation({
          duration: 1500,
          iterations: -1,
          curve: Curve.EaseInOut
        })

      Text(this.useCustomModel ? this.loadingProgress : this.loadingProgress)
        .fontSize(12)
        .fontColor('#999999')
        .margin({ top: 16 })
        
      // 如果是自定义模型模式，显示额外提示
      if (this.useCustomModel && !this.isModelLoaded) {
        Text('您的AI生成3D模型正在加载...')
          .fontSize(11)
          .fontColor('#FF9500')
          .margin({ top: 8 })
      }
    }
    .width('100%')
    .height('100%')
    .justifyContent(FlexAlign.Center)
    .alignItems(HorizontalAlign.Center)
    .backgroundColor('#F0F5FF')
    .borderRadius(16)
    .onClick(() => {
      if (this.showWebView && !this.isModelLoaded) {
        this.loadingProgress = this.useCustomModel ? '正在切换到您的3D模型...' : '正在切换到3D...';
      }
    })
  }
}
