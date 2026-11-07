// PetBreedSelectionPage.ets
// 宠物品种选择页面

import { router } from '@kit.ArkUI';
import { preferences } from '@kit.ArkData';

interface PetBreed {
  id: string;
  name: string;
  color: string;
  description: string;
  image: Resource;
  petType: string;
}

interface BreedDataType {
  cat: PetBreed[];
  dog: PetBreed[];
  hamster: PetBreed[];
  bird: PetBreed[];
}



@Entry
@Component
struct PetBreedSelectionPage {
  @State petType: string = 'cat';
  private context: Context = getContext(this);

  // 不同宠物种类的品种数据
  private breedData: BreedDataType = {
    cat: [
      {
        id: 'cat_pink',
        name: '小粉猫',
        color: '粉色',
        description: '粉嫩嫩的小粉猫，看起来可爱极了',
        image: $r('app.media.type_cat'),
        petType: 'cat'
      },
      {
        id: 'cat_orange',
        name: '橘子猫',
        color: '橙色',
        description: '肉乎乎的大橘，乖巧，超级粘人',
        image: $r('app.media.type_cat'),
        petType: 'cat'
      },
      {
        id: 'cat_white',
        name: '小白猫',
        color: '白色',
        description: '雪白的毛发，优雅高贵',
        image: $r('app.media.type_cat'),
        petType: 'cat'
      },
      {
        id: 'cat_black',
        name: '小黑猫',
        color: '黑色',
        description: '神秘的小黑猫，聪明伶俐',
        image: $r('app.media.type_cat'),
        petType: 'cat'
      }
    ],
    dog: [
      {
        id: 'dog_golden',
        name: '金毛',
        color: '金色',
        description: '温顺友善，是人类的好朋友',
        image: $r('app.media.type_dog'),
        petType: 'dog'
      },
      {
        id: 'dog_poodle',
        name: '贵宾犬',
        color: '白色',
        description: '聪明活泼，毛发卷曲',
        image: $r('app.media.type_dog'),
        petType: 'dog'
      },
      {
        id: 'dog_husky',
        name: '哈士奇',
        color: '黑白',
        description: '精力充沛，有点调皮',
        image: $r('app.media.type_dog'),
        petType: 'dog'
      },
      {
        id: 'dog_corgi',
        name: '柯基',
        color: '黄白',
        description: '短腿可爱，性格开朗',
        image: $r('app.media.type_dog'),
        petType: 'dog'
      }
    ],
    hamster: [
      {
        id: 'hamster_golden',
        name: '黄金仓鼠',
        color: '金色',
        description: '金黄色的毛发，活泼可爱',
        image: $r('app.media.type_hamster'),
        petType: 'hamster'
      },
      {
        id: 'hamster_white',
        name: '白熊仓鼠',
        color: '白色',
        description: '雪白的毛发，温顺亲人',
        image: $r('app.media.type_hamster'),
        petType: 'hamster'
      },
      {
        id: 'hamster_black',
        name: '黑熊仓鼠',
        color: '黑色',
        description: '黑色的毛发，神秘可爱',
        image: $r('app.media.type_hamster'),
        petType: 'hamster'
      },
      {
        id: 'hamster_panda',
        name: '熊猫仓鼠',
        color: '黑白',
        description: '黑白相间的毛发，像小熊猫一样',
        image: $r('app.media.type_hamster'),
        petType: 'hamster'
      }
    ],
    bird: [
      {
        id: 'bird_parrot',
        name: '鹦鹉',
        color: '彩色',
        description: '色彩斑斓，能学人说话',
        image: $r('app.media.type_bird'),
        petType: 'bird'
      },
      {
        id: 'bird_canary',
        name: '金丝雀',
        color: '黄色',
        description: '歌声优美，小巧玲珑',
        image: $r('app.media.type_bird'),
        petType: 'bird'
      },
      {
        id: 'bird_sparrow',
        name: '麻雀',
        color: '棕色',
        description: '活泼好动，常见的小鸟',
        image: $r('app.media.type_bird'),
        petType: 'bird'
      },
      {
        id: 'bird_owl',
        name: '猫头鹰',
        color: '棕色',
        description: '夜间活动，眼神犀利',
        image: $r('app.media.type_bird'),
        petType: 'bird'
      }
    ]
  };

  aboutToAppear(): void {
    // 从路由参数中获取宠物种类
    const params = router.getParams() as Record<string, string>;
    if (params && params.petType) {
      this.petType = params.petType;
    }
  }

  // 获取当前宠物类型的品种列表
  private getCurrentBreeds(): PetBreed[] {
    if (this.petType === 'cat') {
      return this.breedData.cat;
    } else if (this.petType === 'dog') {
      return this.breedData.dog;
    } else if (this.petType === 'hamster') {
      return this.breedData.hamster;
    } else if (this.petType === 'bird') {
      return this.breedData.bird;
    }
    return this.breedData.cat;
  }

  // 选择宠物品种 - 跳转到宠物信息填写页面
  private selectPetBreed(breed: PetBreed): void {
    interface RouterParams {
      petType: string;
      petBreed: string;
      petBreedName: string;
    }
    
    const params: RouterParams = {
      petType: this.petType,
      petBreed: breed.id,
      petBreedName: breed.name
    };
    router.replaceUrl({
      url: 'pages/PetInfoFillPage',
      params: params
    }).catch(() => {
      console.info('跳转宠物信息填写页面');
    });
  }



  // 获取卡片背景图片
  private getCardBackground(petType: string): Resource {
    switch (petType) {
      case 'cat':
        return $r('app.media.card_cat');
      case 'dog':
        return $r('app.media.card_dog');
      case 'hamster':
        return $r('app.media.card_hamster');
      default:
        return $r('app.media.card_bird');
    }
  }

  // 宠物品种卡片
  @Builder
  PetBreedCard(breed: PetBreed) {
    Stack() {
      // 卡片背景
      Image(this.getCardBackground(breed.petType))
        .width('120%')
        .height('118%')
        .objectFit(ImageFit.Cover)
        .align(Alignment.Center)
      
      Column() {
        // 顶部标题栏 - 位于背景图的黄色区域
        Row() {
          Blank()
          Text(breed.name)
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor('#333333')
          Blank()
        }
        .width('100%')
        .height(30)
        .alignItems(VerticalAlign.Center)
        .margin({ top: 10 })

        // 卡身内容 - 位于背景图的白色区域
        Row() {
          // 左侧图片
          Column() {
            Image(breed.image)
              .width(100)
              .height(100)
              .objectFit(ImageFit.Contain)
              .borderRadius(10)
              .padding({left:10,bottom:10, top:5})
              .margin({bottom:10})
          }
          .width('25%')
          .justifyContent(FlexAlign.Center)
          .alignItems(HorizontalAlign.Center)

          // 右侧说明和按钮
          Column() {
            // 宠物说明
            Text(breed.description)
              .fontSize(13)
              .fontColor('#666666')
              .textAlign(TextAlign.Start)
              .margin({ bottom: 0 })
            Text(`花色: ${breed.color}`)
              .fontSize(12)
              .fontColor('#999999')
              .margin({ bottom: 4 })
              .textAlign(TextAlign.Start)
            
            // 选择按钮
            Button('去选择')
              .fontSize(14)
              .fontWeight(FontWeight.Medium)
              .width('70%')
              .height(32)
              .borderRadius(16)
              .backgroundColor('#FF9500')
              .fontColor(Color.White)
              .onClick(() => this.selectPetBreed(breed))
          }
          .width('60%')
          .padding({ left: 10, right: 10 })
        }
        .width('100%')
        .flexGrow(1)
        .alignItems(VerticalAlign.Center)
        .margin({ top: 5, bottom: 10 })
      }
      .width('100%')
      .height('100%')
    }
    .width('100%')
    .height(150)
    .margin({ bottom: 10 })
  }

  // 返回上一页
  goBack() {
    console.log('点击返回按钮');
    // 直接跳转到宠物选择页面，确保能够返回
    router.pushUrl({
      url: 'pages/PetTypeSelectionPage'
    }).catch((error: Error) => {
      console.error('跳转宠物选择页面失败:', error);
    });
  }

  build() {
    Stack() {
      Column() {
        // 顶部标题栏
        Row() {
          Text('←')
            .fontSize(24)
            .margin({ left: 20 })
            .onClick(() => this.goBack())
          Blank()
          Text('选择宠物品种')
            .fontSize(20)
            .fontWeight(FontWeight.Bold)
            .fontColor('#333333')
          Blank()
          Text('')
            .fontSize(24)
            .margin({ right: 20 })
        }
        .width('100%')
        .height(50)
        .alignItems(VerticalAlign.Center)
        .backgroundColor('#FFF9E6')

        // 宠物品种列表
        Scroll() {
          Column() {
            ForEach(this.getCurrentBreeds(), (breed: PetBreed) => {
              this.PetBreedCard(breed)
            })
          }
          .padding({ left: 20, right: 20, top: 20, bottom: 20 })
        }
        .width('100%')
        .layoutWeight(1)
        .scrollBar(BarState.Off)
      }
      .width('100%')
      .height('100%')
      .backgroundColor('#F8F9FA')


    }
    .width('100%')
    .height('100%')
  }
}