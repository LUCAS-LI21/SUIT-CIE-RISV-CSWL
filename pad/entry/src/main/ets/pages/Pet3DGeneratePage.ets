// Pet3DGeneratePage.ets
// 宠物3D模型生成页面 - 上传照片 → AI生成3D → 预览 → 确认使用

import { router, promptAction } from '@kit.ArkUI';
import { picker, fileIo } from '@kit.CoreFileKit';
import { Tripo3DService } from '../features/home/services/Tripo3DService';

@Entry
@Component
struct Pet3DGeneratePage {
  // 状态变量 - 照片选择
  @State selectedImageUri: string = '';
  @State selectedPetType: string = 'dog';
  @State hasSelectedPhoto: boolean = false;
  
  // 状态变量 - 3D生成流程
  @State isGenerating: boolean = false;        // 正在生成中
  @State generateProgress: number = 0;          // 进度百分比 (0-100)
  @State generateStatusText: string = '';       // 状态描述文字
  
  // 状态变量 - 模型预览
  @State generatedModelPath: string = '';       // 生成的GLB本地路径
  @State showPreviewMode: boolean = false;      // 是否显示预览模式
  @State previewImageUrl: string = '';          // 缩略图URL
  
  // 错误信息
  @State errorMessage: string = '';
  
  // 服务实例
  private tripoService: Tripo3DService = new Tripo3DService();

  aboutToAppear(): void {
    const params = router.getParams() as Record<string, string>;
    if (params?.petType) {
      this.selectedPetType = params.petType;
      AppStorage.setOrCreate('petType', params.petType);
    }
    
    console.info('[Pet3DGenerate] 页面初始化完成');
  }

  // ========== 选择照片 ==========
  async selectPhoto(): Promise<void> {
    try {
      const photoPicker = new picker.PhotoViewPicker();
      const result = await photoPicker.select({
        MIMEType: picker.PhotoViewMIMETypes.IMAGE_TYPE,
        maxSelectNumber: 1
      });
      
      if (result.photoUris && result.photoUris.length > 0) {
        const originalUri = result.photoUris[0];
        console.info(`[Pet3DGenerate] 已选择照片(原始URI): ${originalUri}`);
        
        // 将相册图片立即复制到应用沙箱（解决 photo:// URI 无法直接读取的问题）
        try {
          const sandboxPath = await this.copyPhotoToSandbox(originalUri);
          this.selectedImageUri = sandboxPath;
          console.info(`[Pet3DGenerate] ✅ 图片已复制到沙箱: ${sandboxPath}`);
        } catch (copyErr) {
          console.error(`[Pet3DGenerate] ❌ 复制图片到沙箱失败: ${copyErr.message}`);
          // 如果复制失败，仍使用原始URI（某些情况可能可用）
          this.selectedImageUri = originalUri;
        }
        
        this.hasSelectedPhoto = true;
        this.errorMessage = '';
        this.generatedModelPath = '';
        this.showPreviewMode = false;
      }
    } catch (error) {
      console.error('[Pet3DGenerate] 选择照片失败:', JSON.stringify(error));
      this.errorMessage = '选择照片失败，请重试';
    }
  }

  /**
   * 将相册选择的图片复制到应用沙箱目录
   * 解决 photo:// / datashare:// 等 URI 无法直接用 fileIo 读取的问题
   */
  private async copyPhotoToSandbox(imageUri: string): Promise<string> {
    console.info(`[Pet3DGenerate] ===== 开始复制图片到沙箱: ${imageUri}`);
    
    const cacheDir = getContext(this).cacheDir;
    const destPath = `${cacheDir}/selected_photo_${Date.now()}.jpg`;
    
    // 方式1：尝试直接打开（file:// 或兼容 URI）
    try {
      console.info('[Pet3DGenerate] 尝试方式1: fileIo.openSync...');
      const srcFile = fileIo.openSync(imageUri, fileIo.OpenMode.READ_ONLY);
      console.info(`[Pet3DGenerate] ✅ 文件打开成功, fd=${srcFile.fd}`);
      
      // 获取文件大小
      const stat = fileIo.statSync(imageUri);
      console.info(`[Pet3DGenerate] 文件大小: ${stat.size} bytes`);
      
      // 复制到沙箱
      const destFile = fileIo.openSync(destPath, fileIo.OpenMode.CREATE | fileIo.OpenMode.READ_WRITE);
      const chunkSize = 4096;
      let offset = 0;
      
      while (offset < stat.size) {
        const readSize = Math.min(chunkSize, stat.size - offset);
        const buf = new ArrayBuffer(readSize);
        const readLen = fileIo.readSync(srcFile.fd, buf, { offset: offset });
        
        if (readLen > 0) {
          fileIo.writeSync(destFile.fd, buf.slice(0, readLen), { offset: offset });
          offset += readLen;
        } else {
          break;
        }
      }
      
      fileIo.closeSync(srcFile);
      fileIo.closeSync(destFile);
      
      if (offset > 0) {
        console.info(`[Pet3DGenerate] ✅✅✅ 图片复制成功! 路径: ${destPath}, 大小: ${offset} bytes`);
        return destPath;
      } else {
        throw new Error('文件内容为空');
      }
    } catch (e) {
      console.warn(`[Pet3DGenerate] 方式1失败: ${String(e)}`);
    }

    // 方式2：如果方式1失败，尝试使用 picker 的 PhotoViewPicker 再次选择（获取可读路径）
    throw new Error(
      `无法读取图片文件\n` +
      `原始URI: ${imageUri}\n` +
      `可能原因：相册权限不足或文件已被删除`
    );
  }

  // ========== 开始AI生成3D模型 ==========
  async startGenerate(): Promise<void> {
    if (!this.hasSelectedPhoto || !this.selectedImageUri) {
      promptAction.showToast({ message: '请先选择宠物照片' });
      return;
    }

    console.info('[Pet3DGenerate] ===== 开始AI 3D生成流程 =====');
    
    this.isGenerating = true;
    this.generateProgress = 0;
    this.generateStatusText = '正在准备...';
    this.errorMessage = '';
    this.showPreviewMode = false;

    try {
      // Step 1: 上传图片到Tripo服务器
      this.updateProgress(5, '正在上传图片...');
      console.info('[Pet3DGenerate] Step 1/4: 上传图片');
      
      let fileToken: string;
      try {
        fileToken = await this.tripoService.uploadImage(this.selectedImageUri);
        console.info(`[Pet3DGenerate] ✅ 图片上传成功, file_token: ${fileToken.substring(0, 20)}...`);
      } catch (uploadErr) {
        throw new Error('图片上传失败: ' + String(uploadErr));
      }

      // Step 2: 创建3D生成任务
      this.updateProgress(15, '正在创建3D生成任务...');
      console.info('[Pet3DGenerate] Step 2/4: 创建任务');
      
      let taskId: string;
      try {
        taskId = await this.tripoService.createImageTo3DTask(fileToken);
        console.info(`[Pet3DGenerate] ✅ 任务创建成功, task_id: ${taskId.substring(0, 20)}...`);
      } catch (taskErr) {
        throw new Error('创建任务失败: ' + String(taskErr));
      }

      // Step 3: 轮询等待生成完成
      this.updateProgress(25, 'AI正在生成3D模型...');
      console.info('[Pet3DGenerate] Step 3/4: 等待生成');
      
      let modelUrl: string;
      try {
        modelUrl = await this.tripoService.pollTaskStatus(taskId, (progress, status) => {
          // 更新进度 (25% ~ 85%)
          const adjustedProgress = Math.min(85, 25 + Math.floor(progress * 0.6));
          this.updateProgress(adjustedProgress, `AI生成中...${Math.floor(progress)}%`);
          
          if (status === 'success') {
            this.updateProgress(85, '生成完成！准备下载模型...');
          }
        });
        
        console.info(`[Pet3DGenerate] ✅ 3D模型生成成功`);
      } catch (pollErr) {
        throw new Error(String(pollErr));
      }

      // Step 4: 下载GLB模型到本地
      this.updateProgress(88, '正在下载3D模型...');
      console.info('[Pet3DGenerate] Step 4/4: 下载模型');
      
      const fileName = `pet_3d_model_${Date.now()}.glb`;
      let localPath: string;
      
      try {
        localPath = await this.tripoService.downloadModel(modelUrl, fileName);
        console.info(`[Pet3DGenerate] ✅ 模型下载成功: ${localPath}`);
      } catch (downloadErr) {
        throw new Error('模型下载失败: ' + String(downloadErr));
      }

      // 生成完成！进入预览模式
      this.updateProgress(100, '✅ 3D模型生成完成！');
      this.generatedModelPath = localPath;
      this.isGenerating = false;
      this.showPreviewMode = true;
      
      console.info(`[Pet3DGenerate] ===== 3D生成流程完成 =====`);
      console.info(`[Pet3DGenerate] 模型路径: ${localPath}`);
      
      // 延迟1秒让用户看到100%
      setTimeout(() => {
        this.showPreviewAndConfirm();
      }, 1000);

    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      console.error(`[Pet3DGenerate] ❌ 3D生成失败: ${errorMsg}`);
      
      this.isGenerating = false;
      this.errorMessage = errorMsg || '3D生成失败，请重试';
      this.generateProgress = 0;
      
      promptAction.showToast({ message: this.errorMessage });
    }
  }
  
  // ========== 更新进度UI ==========
  updateProgress(progress: number, statusText: string): void {
    this.generateProgress = progress;
    this.generateStatusText = statusText;
  }
  
  // ========== 显示预览和确认按钮 ==========
  showPreviewAndConfirm(): void {
    this.showPreviewMode = true;
    console.info('[Pet3DGenerate] 切换到预览模式');
  }
  
  // ========== 确认使用此模型 ==========
  confirmAndGoHome(): void {
    if (!this.generatedModelPath) {
      promptAction.showToast({ message: '没有可用的3D模型' });
      return;
    }
    
    console.info('[Pet3DGenerate] 确认使用生成的3D模型');
    console.info(`[Pet3DGenerate] 模型路径: ${this.generatedModelPath}`);
    
    // 设置全局状态：使用自定义3D模型模式
    AppStorage.setOrCreate('useCustomModel', true);
    AppStorage.setOrCreate('petModelPath', this.generatedModelPath);
    AppStorage.setOrCreate('useStandardModel', false);  // 关闭标准视频模式
    
    console.info('[Pet3DGenerate] 已设置AppStorage:');
    console.info(`  - useCustomModel: true`);
    console.info(`  - petModelPath: ${this.generatedModelPath}`);
    console.info(`  - useStandardModel: false`);
    
    // 跳转首页
    router.replaceUrl({ url: 'pages/Index' }).catch((e: Error) => {
      console.error('[Pet3DGenerate] 跳转首页失败:', e.message);
    });
  }
  
  // ========== 重新生成 ==========
  regenerate(): void {
    console.info('[Pet3DGenerate] 用户选择重新生成');
    
    // 重置状态
    this.generatedModelPath = '';
    this.showPreviewMode = false;
    this.isGenerating = false;
    this.generateProgress = 0;
    this.generateStatusText = '';
    this.errorMessage = '';
    
    // 重新调用生成
    this.startGenerate();
  }
  
  // ========== 使用默认模型 ==========
  useDefaultAndGoHome(): void {
    console.info('[Pet3DGenerate] 使用默认模型');
    
    AppStorage.setOrCreate('useCustomModel', false);
    AppStorage.setOrCreate('petModelPath', '');
    AppStorage.setOrCreate('useStandardModel', true);
    
    router.replaceUrl({ url: 'pages/Index' }).catch((e: Error) => {
      console.error('跳转失败', e);
    });
  }

  build() {
    Stack() {
      Column()
        .width('100%')
        .height('100%')
        .linearGradient({
          direction: GradientDirection.Bottom,
          colors: [[0xFFF5F0, 0], [0xFFF0E8, 0.5], [0xFFE8D5, 1]]
        })

      Column() {
        // 顶部导航栏
        Row() {
          Text(this.showPreviewMode ? '←' : '←')
            .fontSize(24)
            .fontColor('#333333')
            .margin({ left: 20 })
            .onClick(() => {
              if (this.showPreviewMode && !this.isGenerating) {
                // 从预览模式返回选择界面
                this.showPreviewMode = false;
                this.generatedModelPath = '';
              } else if (!this.isGenerating) {
                router.back();
              }
            })
          Blank()
          Text(this.showPreviewMode ? '3D模型预览' : '创建3D宠物')
            .fontSize(20)
            .fontWeight(FontWeight.Bold)
            .fontColor('#333333')
          Blank()
          Text('')
            .fontSize(14)
            .margin({ right: 20 })
        }
        .width('100%')
        .height(56)
        .alignItems(VerticalAlign.Center)

        Scroll() {
          Column() {
            if (!this.showPreviewMode) {
              // ========== 模式A：选择照片 + 生成 ==========
              this.BuildSelectAndGenerateSection();
            } else {
              // ========== 模式B：3D预览 + 确认/重新生成 ==========
              this.BuildPreviewAndConfirmSection();
            }
            
            Blank()
          }
          .width('100%')
        }
        .layoutWeight(1)
        .scrollBar(BarState.Off)
      }
      .width('100%')
      .height('100%')
    }
    .width('100%')
    .height('100%')
  }
  
  // ========== 构建器：选择照片和生成区域 ==========
  @Builder
  BuildSelectAndGenerateSection() {
    Column() {
      Blank().height(16)

      // 标题说明
      Column() {
        Text('📸 为你的宠物创建3D形象')
          .fontSize(22)
          .fontWeight(FontWeight.Bold)
          .fontColor('#2C3E50')
          .margin({ bottom: 8 })
        Text('上传一张宠物照片，AI将生成专属3D模型\n也可以直接使用默认模型开始体验')
          .fontSize(14)
          .fontColor('#7F8C8D')
          .textAlign(TextAlign.Center)
          .lineHeight(20)
      }
      .width('100%')
      .padding({ top: 10, bottom: 24 })

      // 照片选择区域
      Column() {
        if (this.hasSelectedPhoto && this.selectedImageUri) {
          Stack({ alignContent: Alignment.TopEnd }) {
            Image(this.selectedImageUri)
              .width('100%')
              .height(280)
              .objectFit(ImageFit.Cover)
              .borderRadius(16)

            Text('✕')
              .fontSize(18)
              .fontColor('#FFFFFF')
              .width(28)
              .height(28)
              .borderRadius(14)
              .backgroundColor('rgba(0,0,0,0.5)')
              .textAlign(TextAlign.Center)
              .margin(10)
              .onClick(() => {
                this.selectedImageUri = '';
                this.hasSelectedPhoto = false;
                this.generatedModelPath = '';
              })
          }
        } else {
          Column() {
            Text('📷')
              .fontSize(48)
              .margin({ bottom: 12 })
            Text('点击选择宠物照片')
              .fontSize(16)
              .fontColor('#666666')
            Text('支持 JPG、PNG、WebP 格式')
              .fontSize(12)
              .fontColor('#BBBBBB')
              .margin({ top: 4 })
          }
          .width('100%')
          .height(260)
          .justifyContent(FlexAlign.Center)
          .border({ width: 2, color: '#FFD0A0', style: BorderStyle.Dashed })
          .borderRadius(16)
          .backgroundColor('#FFF9F5')
          .onClick(() => this.selectPhoto())
        }
      }
      .padding({ left: 24, right: 24 })
      .margin({ bottom: 16 })

      // 错误提示
      if (this.errorMessage && !this.isGenerating) {
        Row() {
          Text('⚠️ ')
            .fontSize(14)
          Text(this.errorMessage)
            .fontSize(13)
            .fontColor('#E74C3C')
            .layoutWeight(1)
        }
        .width('90%')
        .margin({ bottom: 12 })
        .padding(12)
        .backgroundColor('#FDECEC')
        .borderRadius(8)
      }

      // ====== 生成进度显示 ======
      if (this.isGenerating) {
        this.BuildProgressSection();
      } else {
        // 操作按钮区（非生成状态下）
        Column() {
          Button(this.hasSelectedPhoto ? '🪄 开始生成3D模型' : '📷 请先选择照片')
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor('#FFFFFF')
            .backgroundColor(this.hasSelectedPhoto ? '#FF9500' : '#CCCCCC')
            .borderRadius(26)
            .width('85%')
            .height(52)
            .shadow({
              radius: 10,
              color: this.hasSelectedPhoto ? 'rgba(255, 149, 0, 0.35)' : 'transparent',
              offsetY: 4
            })
            .enabled(this.hasSelectedPhoto)
            .onClick(() => this.startGenerate())

          Row() {
            Text('')
              .width(60)
              .height(1)
              .backgroundColor('#DDDDDD')
            Text('  或者  ')
              .fontSize(13)
              .fontColor('#BBBBBB')
            Text('')
              .width(60)
              .height(1)
              .backgroundColor('#DDDDDD')
          }
          .width('100%')
          .justifyContent(FlexAlign.Center)
          .margin({ top: 20, bottom: 16 })

          Button('🐾 使用默认模型体验')
            .fontSize(17)
            .fontColor('#FF9500')
            .backgroundColor('#FFFFFF')
            .borderRadius(24)
            .width('85%')
            .height(48)
            .border({ width: 2, color: '#FFD0A0' })
            .onClick(() => this.useDefaultAndGoHome())
        }
        .width('100%')
        .padding({ top: 16, bottom: 40 })
      }

      Blank()
    }
    .width('100%')
  }
  
  // ========== 构建器：进度条区域 ==========
  @Builder
  BuildProgressSection() {
    Column() {
      // 动画图标或文字
      Column() {
        Text(this.getProgressEmoji())
          .fontSize(64)
          .margin({ bottom: 16 })
        
        Text(this.generateStatusText || '处理中...')
          .fontSize(18)
          .fontWeight(FontWeight.Medium)
          .fontColor('#333333')
          .margin({ bottom: 24 })
        
        // 圆形进度指示器（如果支持）
        Progress({ value: this.generateProgress, total: 100, type: ProgressType.Ring })
          .width(120)
          .height(120)
          .color('#FF9500')
          .backgroundColor('#FFE8CC')
          .style({ strokeWidth: 8 })
          .margin({ bottom: 16 })
        
        Text(`${this.generateProgress}%`)
          .fontSize(24)
          .fontWeight(FontWeight.Bold)
          .fontColor('#FF9500')
      }
      .width('100%')
      .padding({ top: 40, bottom: 32 })
      .alignItems(HorizontalAlign.Center)

      // 提示信息
      Text('AI正在根据您的照片生成3D模型，通常需要30秒~3分钟\n请耐心等待...')
        .fontSize(13)
        .fontColor('#888888')
        .textAlign(TextAlign.Center)
        .lineHeight(18)
        .margin({ bottom: 24 })

      // 取消按钮（小字）
      Text('取消生成')
        .fontSize(14)
        .fontColor('#999999')
        .padding(12)
        .onClick(() => {
          this.isGenerating = false;
          this.generateProgress = 0;
          this.generateStatusText = '';
          promptAction.showToast({ message: '已取消生成' });
        })
    }
    .width('100%')
    .padding({ left: 24, right: 24 })
  }
  
  // 根据进度返回不同的emoji动画
  getProgressEmoji(): string {
    if (this.generateProgress < 20) {
      return '📤'; // 上传
    } else if (this.generateProgress < 50) {
      return '⚙️'; // 处理中
    } else if (this.generateProgress < 80) {
      return '🎨'; // 生成中
    } else if (this.generateProgress < 95) {
      return '📥'; // 下载中
    } else {
      return '✅'; // 完成
    }
  }
  
  // ========== 构建器：预览和确认区域 ==========
  @Builder
  BuildPreviewAndConfirmSection() {
    Column() {
      Blank().height(16)

      // 成功提示
      Column() {
        Text('🎉 3D模型生成成功！')
          .fontSize(22)
          .fontWeight(FontWeight.Bold)
          .fontColor('#27AE60')
          .margin({ bottom: 8 })
        Text('这是根据您上传的照片生成的专属3D宠物模型\n可以360°旋转查看')
          .fontSize(14)
          .fontColor('#666666')
          .textAlign(TextAlign.Center)
          .lineHeight(20)
      }
      .width('100%')
      .padding({ top: 10, bottom: 24 })

      // 原始照片参考
      if (this.selectedImageUri) {
        Column() {
          Text('原始参考照片')
            .fontSize(13)
            .fontColor('#888888')
            .margin({ bottom: 8 })
          
          Image(this.selectedImageUri)
            .width(160)
            .height(160)
            .objectFit(ImageFit.Cover)
            .borderRadius(12)
            .border({ width: 2, color: '#FFD0A0', style: BorderStyle.Dashed })
        }
        .margin({ bottom: 20 })
        .alignItems(HorizontalAlign.Center)
      }

      // 3D模型占位提示（实际渲染在首页）
      Column() {
        Text('🐕')
          .fontSize(96)
          .margin({ bottom: 16 })
        
        Text('3D模型预览')
          .fontSize(18)
          .fontWeight(FontWeight.Medium)
          .fontColor('#333333')
          .margin({ bottom: 8 })
        
        Text('点击"确认使用此模型"\n即可在首页查看完整的3D效果')
          .fontSize(13)
          .fontColor('#888888')
          .textAlign(TextAlign.Center)
          .lineHeight(18)
      }
      .width('100%')
      .height(300)
      .justifyContent(FlexAlign.Center)
      .backgroundColor('#F8F9FA')
      .borderRadius(16)
      .border({ width: 2, color: '#E0E0E0', style: BorderStyle.Dashed })
      .margin({ bottom: 24 })

      // 按钮组
      Column() {
        // 主要按钮：确认使用此模型
        Button('✅ 确认使用此模型')
          .fontSize(18)
          .fontWeight(FontWeight.Bold)
          .fontColor('#FFFFFF')
          .backgroundColor('#27AE60')
          .borderRadius(26)
          .width('85%')
          .height(52)
          .shadow({
            radius: 10,
            color: 'rgba(39, 174, 96, 0.35)',
            offsetY: 4
          })
          .margin({ bottom: 16 })
          .onClick(() => this.confirmAndGoHome())

        // 次要按钮：重新生成
        Button('🔄 重新生成')
          .fontSize(17)
          .fontColor('#FF9500')
          .backgroundColor('#FFFFFF')
          .borderRadius(24)
          .width('85%')
          .height(48)
          .border({ width: 2, color: '#FFD0A0' })
          .margin({ bottom: 12 })
          .onClick(() => this.regenerate())

        // 返回修改
        Text('← 返回重新选择照片')
          .fontSize(14)
          .fontColor('#999999')
          .padding(12)
          .onClick(() => {
            this.showPreviewMode = false;
            this.generatedModelPath = '';
          })
      }
      .width('100%')

      Blank().height(20)
    }
    .width('100%')
  }
}
