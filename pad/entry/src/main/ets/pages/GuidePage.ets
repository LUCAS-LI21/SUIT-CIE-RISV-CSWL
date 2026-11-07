// GuidePage.ets
// 三段式功能引导页 - 美化版

import router from '@ohos.router';
import { preferences } from '@kit.ArkData';

interface GuideItem {
  image: Resource;
  title: string;
  desc: string;
  gradientStart: string; // 渐变起始色
  gradientEnd: string; // 渐变结束色
}

@Entry
@Component
struct GuidePage {
  @State isFirstLaunch: boolean = false
  @State currentIndex: number = 0;
  private swiperController: SwiperController = new SwiperController();
  private context: Context = getContext(this);
  // 引导页数据 - 图片名称使用 fig1, fig2, fig3
  private guideData: GuideItem[] = [
    {
      image: $r('app.media.backgroundstart'),
      title: '欢迎来到宠物世界',
      desc: '领养一只属于你的AI宠物，开启陪伴之旅',
      gradientStart: '#FFE4B5',
      gradientEnd: '#FFDAB9'
    },
    {
      image: $r('app.media.backgroundtwo'),
      title: 'AI语音对话',
      desc: '和宠物聊天，它会用DeepSeek大模型回复你',
      gradientStart: '#B0E0E6',
      gradientEnd: '#E0FFFF'
    },
    {
      image: $r('app.media.backgroundthree'),
      title: '触摸互动',
      desc: '摸摸它的头、肚子或尾巴，会有不同的反应',
      gradientStart: '#98FB98',
      gradientEnd: '#F0FFF0'
    }
  ];

  async aboutToAppear(): Promise<void> {
    // 仅读取状态，不做任何跳转
    try {
      const prefs = await preferences.getPreferences(this.context, 'AppConfig');
      const isFirst = await prefs.get('isFirstLaunch', true);
      this.isFirstLaunch = isFirst as boolean;
    } catch (error) {
      console.error('读取首次启动标记失败', JSON.stringify(error));
    }
  }

  async markGuideCompletedAndGoHome(): Promise<void> {
    try {
      const prefs = await preferences.getPreferences(this.context, 'AppConfig');
      await prefs.put('isFirstLaunch', false);
      await prefs.flush();
    } catch (error) {
      console.error('保存首次启动标记失败', JSON.stringify(error));
    }

    router.replaceUrl({ url: 'pages/LoginPage' })

    //测试
    // router.replaceUrl({ url: 'pages/PetTypeSelectionPage' })

    // //测试
    // router.replaceUrl({ url: 'pages/Index' })


  }

  build() {
    Stack() {
      // Swiper 轮播区域
      Swiper(this.swiperController) {
        ForEach(this.guideData, (item: GuideItem, index: number) => {
          Column() {
            if (index === 1) {
              // 第二个页面 - 字体放在圆点指示器上方一点点
              Column() {
                // 空白空间，将文字推到下方
                Blank()
                  .height('68%')

                // 文字内容
                Column() {
                  Text(item.title)
                    .fontSize(32)
                    .fontWeight(FontWeight.Bold)
                    .fontColor('#2C3E50')
                    .margin({ bottom: 12 })
                    .textShadow({
                      radius: 4,
                      color: 'rgba(255, 255, 255, 0.5)',
                      offsetY: 1
                    })

                  Text(item.desc)
                    .fontSize(16)
                    .fontColor('#5D6D7E')
                    .textAlign(TextAlign.Center)
                    .maxLines(2)
                    .padding({ left: 40, right: 40 })
                    .lineHeight(24)
                }

                // 空白空间，确保文字在圆点上方
                Blank()
                  .height('20%')
              }
              .width('100%')
              .height('100%')
              .alignItems(HorizontalAlign.Center)
            } else if (index === 0) {
              // 第一个页面 - 保持原有布局，文字在上，背景图片
              Column() {
                Text(item.title)
                  .fontSize(32)
                  .fontWeight(FontWeight.Bold)
                  .fontColor('#2C3E50')
                  .margin({ bottom: 12 })
                  .textShadow({
                    radius: 4,
                    color: 'rgba(255, 255, 255, 0.5)',
                    offsetY: 1
                  })

                Text(item.desc)
                  .fontSize(16)
                  .fontColor('#5D6D7E')
                  .textAlign(TextAlign.Center)
                  .maxLines(2)
                  .padding({ left: 40, right: 40 })
                  .lineHeight(24)
              }
              .width('100%')
              .height('20%')
              .justifyContent(FlexAlign.Start)
              .margin({ top: 40 })
              .alignItems(HorizontalAlign.Center)
            } else {
              // 第三个页面 - 图片在上，文字和按钮在下
              Column() {
                Blank()
                  .height('50%')

                // 文字和按钮区域 - 占据下方
                Column() {
                  Text(item.title)
                    .fontSize(32)
                    .fontWeight(FontWeight.Bold)
                    .fontColor('#2C3E50')
                    .margin({ bottom: 12 })
                    .textShadow({
                      radius: 4,
                      color: 'rgba(255, 255, 255, 0.5)',
                      offsetY: 1
                    })

                  Text(item.desc)
                    .fontSize(16)
                    .fontColor('#5D6D7E')
                    .textAlign(TextAlign.Center)
                    .maxLines(2)
                    .padding({ left: 40, right: 40 })
                    .lineHeight(24)

                  Button('立即体验')
                    .fontSize(20)
                    .fontWeight(FontWeight.Medium)
                    .width('70%')
                    .height(54)
                    .borderRadius(27)
                    .margin({ top: 24 })
                    .linearGradient({
                      angle: 135,
                      colors: [
                        ['#6A82FB', 0],
                        ['#FC5C7D', 1]
                      ]
                    })
                    .fontColor(Color.White)
                    .shadow({
                      radius: 12,
                      color: 'rgba(252, 92, 125, 0.3)',
                      offsetY: 4
                    })
                    .onClick(() => {
                      this.markGuideCompletedAndGoHome();
                    })
                }
                .width('100%')
                .layoutWeight(1)
                .justifyContent(FlexAlign.Center)
                .alignItems(HorizontalAlign.Center)
              }
              .width('100%')
              .height('100%')
            }
          }
          .width('100%')
          .height('100%')
          // 背景设置
          .backgroundImage(index === 0 ? $r('app.media.backgroundstart') :
            (index === 1 ? $r('app.media.background1') : $r('app.media.backgroundthree')))
          .backgroundImageSize(ImageSize.Cover)
          .backgroundImagePosition(Alignment.Center)

          // 对角线渐变背景
          // .linearGradient({
          //   angle: 145,
          //   colors: [
          //     [item.gradientStart, 0],
          //     [item.gradientEnd, 1]
          //   ]
          // })
        }, (item: GuideItem, index: number) => index.toString())
      }
      .width('100%')
      .layoutWeight(1)
      .loop(false)
      .indicator(false) // 隐藏默认指示器
      .onChange((index: number) => {
        this.currentIndex = index;
      })

      // 自定义圆点指示器 - 放大版
      Row() {
        ForEach(this.guideData, (item: GuideItem, index: number) => {
          Circle()
            .width(index === this.currentIndex ? 32 : 12)   // 当前页宽度从24改为32，其他从8改为12
            .height(12)                                      // 高度从8改为12
            .fill(index === this.currentIndex ? '#6A82FB' : '#CCCCCC')
            .borderRadius(6)                                 // 圆角保持高度的一半，实现胶囊效果
            .margin({ left: 5, right: 5 })                   // 间距稍微调大
            .animation({
              duration: 200,
              curve: Curve.EaseInOut
            })
        })
      }
      .width('100%')
      .height(40) // 容器高度适当增加
      .justifyContent(FlexAlign.Center)
      .position({ y: '90%' })
    }
    .width('100%')
    .height('100%')
    .backgroundColor(Color.Transparent)
  }
}