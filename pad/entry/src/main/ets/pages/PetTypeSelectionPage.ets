// PetTypeSelectionPage.ets
// 宠物种类选择页面

import router from '@ohos.router';

interface PetType {
  id: string;
  name: string;
  icon: string;
  image: Resource;
  description: string;
  gradientStart: string;
  gradientEnd: string;
}

@Entry
@Component
struct PetTypeSelectionPage {
  // 宠物种类数据
  private petTypes: PetType[] = [
    {
      id: 'dog',
      name: '狗狗',
      icon: '🐶',
      image: $r('app.media.type_dog'),
      description: '粘人汪星人',
      gradientStart: '#B0E0E6',
      gradientEnd: '#E0FFFF'
    },
    {
      id: 'cat',
      name: '猫咪',
      icon: '🐱',
      image: $r('app.media.type_cat'),
      description: '软萌喵星人',
      gradientStart: '#FFE4B5',
      gradientEnd: '#FFDAB9'
    },
    {
      id: 'hamster',
      name: '仓鼠',
      icon: '🐹',
      image: $r('app.media.type_hamster'),
      description: '贪吃小绒团',
      gradientStart: '#98FB98',
      gradientEnd: '#F0FFF0'
    },
    {
      id: 'bird',
      name: '小鸟',
      icon: '🦜', // 想用猫头鹰🦉也可以
      image: $r('app.media.type_bird'),
      description: '自由小天使',
      gradientStart: '#FFB6C1',
      gradientEnd: '#FFC0CB'
    }
  ];

  // 选择宠物种类
  private selectPetType(type: PetType): void {
    router.replaceUrl({
      url: 'pages/PetBreedSelectionPage',
      params: { petType: type.id }
    }).catch(() => {
      console.info('跳转宠物品种选择页面');
    });
  }

  @State selectedCard: string | null = null;

  // 宠物种类卡片
  @Builder
  PetTypeCard(type: PetType) {
    Column() {
      // 图片
      Image(type.image)
        .width(90)
        .height(90)
        .objectFit(ImageFit.Contain)
        .borderRadius(15)
        .margin({ bottom: 10 })
      
      // 文字
      Text(type.name)
        .fontSize(16)
        .fontWeight(FontWeight.Bold)
        .fontColor('#333333')
        .margin({ bottom: 5 })
      Text(type.description)
        .fontSize(14)
        .fontColor('#666666')
        .textAlign(TextAlign.Center)
        .padding({ left: 10, right: 10 })
    }
    .width('100%')
    .height(180)
    .justifyContent(FlexAlign.Center)
    .alignItems(HorizontalAlign.Center)
    .backgroundColor(this.selectedCard === type.id ? '#FFF9E6' : '#FFFFFF')
    .borderRadius(20)
    .border({ 
      width: 2, 
      color: this.selectedCard === type.id ? '#FF9500' : '#FFE4B5' 
    })
    .shadow({
      radius: 5,
      color: 'rgba(0, 0, 0, 0.1)',
      offsetY: 2
    })
    .onClick(() => {
      this.selectedCard = type.id;
      setTimeout(() => {
        this.selectPetType(type);
      }, 200);
    })
  }

  @State selectedPet: PetType | null = null;

  // 锁定爱宠
  lockPet() {
    if (this.selectedPet) {
      router.pushUrl({
        url: 'pages/PetBreedSelectionPage',
        params: { petType: this.selectedPet.id }
      }).catch(() => {
        console.info('跳转宠物品种选择页面');
      });
    } else {
      // 提示用户选择宠物
      this.getUIContext().getPromptAction().showToast({ message: '请选择一个宠物' });
    }
  }

  // 返回上一页
  goBack() {
    console.log('点击返回按钮');
    // 直接跳转到引导页面，确保能够返回
    router.pushUrl({
      url: 'pages/GuidePage'
    }).catch((error: Error) => {
      console.error('跳转引导页面失败:', error);
    });
  }

  build() {
    Stack() {
      // 背景图片
      Image($r('app.media.background_PetType'))
        .width('100%')
        .height('100%')
        .objectFit(ImageFit.Cover)
      
      Column() {
        // 顶部标题栏
        Row() {
          Text('←')
            .fontSize(24)
            .margin({ left: 20 })
            .onClick(() => this.goBack())
          Blank()
          Text('爱宠选择')
            .fontSize(20)
            .fontWeight(FontWeight.Bold)
            .fontColor('#333333')
            .margin({ right: 35})
          Blank()
          // Text('...')
          //   .fontSize(24)
          //   .margin({ right: 20 })
        }
        .width('100%')
        .height(60)
        .alignItems(VerticalAlign.Center)

        // 描述文字
        Column() {
          Text('你最想 pick 的小可爱是：专属萌宠')
            .fontSize(16)
            .fontWeight(FontWeight.Bold)
            .fontColor('#333333')
            .margin({ bottom: 8 })
          Text('语音 + 环境双监测，AI 帮你秒懂它的情绪与健康，电子宠物陪你一起守护真实的它～\n')
            .fontSize(14)
            .fontColor('#666666')
            .textAlign(TextAlign.Center)
            .padding({ left: 40, right: 40 })
            .margin({ top:4 })
        }
        .margin({ top: 20, bottom: 30 })

        // 宠物种类网格
        Grid() {
          ForEach(this.petTypes, (type: PetType) => {
            GridItem() {
              this.PetTypeCard(type)
            }
          })
        }
        .columnsTemplate('1fr 1fr')
        .columnsGap(20)
        .rowsGap(20)
        .padding({ left: 30, right: 30 })
        .width('100%')

        // 底部按钮
        Button('锁定我的爱宠')
          .fontSize(18)
          .fontWeight(FontWeight.Bold)
          .fontColor('#FFFFFF')
          .backgroundColor('#FF9500')
          .borderRadius(25)
          .width('80%')
          .height(50)
          .margin({ top: 40, bottom: 30 })
          .shadow({
            radius: 8,
            color: 'rgba(255, 149, 0, 0.3)',
            offsetY: 4
          })
          .onClick(() => this.lockPet())
      }
      .width('100%')
      .height('100%')
    }
    .width('100%')
    .height('100%')
  }
}