// PetInfoFillPage.ets
// 宠物信息填写页面

import { promptAction, router } from '@kit.ArkUI';
import { preferences } from '@kit.ArkData';
import { createDefaultPet } from '../product/model/PetModel';
import { AppConstants } from '../common/constants/AppConstants';

interface PetData {
  id: string;
  name: string;
  type: string;
  level: number;
  hunger: number;
  mood: number;
  adoptDate: string;
}

@Entry
@Component
struct PetInfoFillPage {
  @State petName: string = '';
  @State petGender: string = 'female';
  @State showDatePicker: boolean = false;
  @State selectedDate: Date = new Date();
  @State petType: string = 'cat';
  @State petBreed: string = '';
  @State petBreedName: string = '';
  private context: Context = getContext(this);

  // 返回上一页
  goBack() {
    router.back();
  }

  aboutToAppear(): void {
    const params = router.getParams() as Record<string, Object>;
    if (params) {
      this.petType = (params.petType as string) || 'cat';
      this.petBreed = (params.petBreed as string) || '';
      this.petBreedName = (params.petBreedName as string) || '';
    }
  }

  // 保存宠物信息并跳转到首页
  async savePetInfo(): Promise<void> {
    if (!this.petName.trim()) {
      promptAction.showToast({ message: '请输入宠物名字' });
      return;
    }

    try {
      // 创建宠物数据
      const pet = createDefaultPet(this.petName, this.petType);

      // 格式化日期
      const year = this.selectedDate.getFullYear();
      const month = this.selectedDate.getMonth() + 1;
      const day = this.selectedDate.getDate();
      const birthDate = `${year}-${month.toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`;

      // 构建宠物信息对象用于传递
      const petData: PetData = {
        id: pet.id,
        name: pet.name,
        type: pet.type,
        level: pet.level,
        hunger: 80,
        mood: 80,
        adoptDate: birthDate
      };

      // 保存到本地存储
      const prefs = await preferences.getPreferences(this.context, AppConstants.PREF_NAME);
      await prefs.put(AppConstants.KEY_PET_INFO, JSON.stringify(petData));
      await prefs.flush();

      // 保存宠物类型（用于视频动态加载）
      await prefs.put('petType', this.petType);
      await prefs.flush();

      // 保存宠物名称（用于首页显示）
      await prefs.put('petName', this.petName);
      await prefs.flush();

      // 保存宠物出生日期
      await prefs.put('petBirthDate', birthDate);
      await prefs.flush();

      // 保存宠物性别
      await prefs.put('petGender', this.petGender);
      await prefs.flush();

      // 保存宠物品种
      await prefs.put('petBreed', this.petBreed);
      await prefs.flush();

      // 保存宠物品种名称
      await prefs.put('petBreedName', this.petBreedName);
      await prefs.flush();

      // 标记首次启动完成
      await prefs.put(AppConstants.KEY_IS_FIRST_LAUNCH, false);
      await prefs.flush();

      // 直接跳转到首页（删除中间的3D生成页面）
      router.replaceUrl({
        url: 'pages/Index',
        params: {
          petData: petData,
          petName: this.petName,
          petType: this.petType,
          petBirthDate: birthDate,
          petGender: this.petGender,
          petBreed: this.petBreed,
          petBreedName: this.petBreedName
        }
      }).catch(() => {
        console.info('跳转首页');
      });
    } catch (error) {
      console.error('保存宠物信息失败', JSON.stringify(error));
    }
  }

  // 打开日期选择器
  openDatePicker(): void {
    this.showDatePicker = true;
  }

  // 确认日期选择
  confirmDate(): void {
    this.showDatePicker = false;
  }

  // 格式化日期显示
  private getFormattedDate(): string {
    const year = this.selectedDate.getFullYear();
    const month = this.selectedDate.getMonth() + 1;
    const day = this.selectedDate.getDate();
    return `${year} 年 ${month} 月 ${day} 日`;
  }

  // 获取可选的起始日期（10年前）
  private getStartDate(): Date {
    const date = new Date();
    date.setFullYear(date.getFullYear() - 10);
    date.setMonth(0, 1);
    return date;
  }

  // 获取可选的结束日期（今天）
  private getEndDate(): Date {
    return new Date();
  }

  build() {
    Stack() {
      // 背景
      Column() {
        Blank()
      }
      .width('100%')
      .height('100%')
      .backgroundColor('#F5F6FA')

      // 主内容 - 垂直居中
      Column() {
        // 顶部装饰
        Column() {
          Text('🎉')
            .fontSize(50)
            .margin({ bottom: 10 })
          Text('即将成为铲屎官')
            .fontSize(22)
            .fontWeight(FontWeight.Bold)
            .fontColor('#2C3E50')
            .margin({ bottom: 6 })
          Text('为你的小可爱起个名字吧')
            .fontSize(14)
            .fontColor('#7F8C8D')
        }
        .margin({ bottom: 30, top: 20 })

        // 信息卡片
        Column() {
          // 宠物名字输入
          Column() {
            Text('宠物名字')
              .fontSize(14)
              .fontColor('#7F8C8D')
              .width('100%')
              .margin({ bottom: 8 })
            TextInput({ placeholder: '给它取个可爱的名字', text: this.petName })
              .width('100%')
              .height(48)
              .fontSize(16)
              .fontColor('#2C3E50')
              .placeholderColor('#BDC3C7')
              .backgroundColor('#F8F9FA')
              .borderRadius(12)
              .padding({ left: 16, right: 16 })
              .onChange((value: string) => this.petName = value)
          }
          .width('100%')
          .margin({ bottom: 20 })

          // 生日选择
          Column() {
            Text('出生日期')
              .fontSize(14)
              .fontColor('#7F8C8D')
              .width('100%')
              .margin({ bottom: 8 })
            Row() {
              Text(this.getFormattedDate())
                .fontSize(16)
                .fontColor('#2C3E50')
                .layoutWeight(1)
              Text('📅')
                .fontSize(20)
            }
            .width('100%')
            .height(48)
            .padding({ left: 16, right: 16 })
            .backgroundColor('#F8F9FA')
            .borderRadius(12)
            .onClick(() => this.openDatePicker())
          }
          .width('100%')
          .margin({ bottom: 20 })

          // 性别选择
          Column() {
            Text('宠物性别')
              .fontSize(14)
              .fontColor('#7F8C8D')
              .width('100%')
              .margin({ bottom: 12 })
            Row() {
              // 雌性
              Column() {
                Text('♀')
                  .fontSize(32)
                Text('小公主')
                  .fontSize(13)
                  .fontColor(this.petGender === 'female' ? '#6A82FB' : '#95A5A6')
                  .margin({ top: 6 })
              }
              .layoutWeight(1)
              .height(80)
              .justifyContent(FlexAlign.Center)
              .backgroundColor(this.petGender === 'female' ? '#EEF2FF' : '#F8F9FA')
              .borderRadius(16)
              .border({
                width: 2,
                color: this.petGender === 'female' ? '#6A82FB' : 'transparent',
                radius: 16
              })
              .onClick(() => this.petGender = 'female')

              // 雄性
              Column() {
                Text('♂')
                  .fontSize(32)
                Text('小王子')
                  .fontSize(13)
                  .fontColor(this.petGender === 'male' ? '#6A82FB' : '#95A5A6')
                  .margin({ top: 6 })
              }
              .layoutWeight(1)
              .height(80)
              .justifyContent(FlexAlign.Center)
              .backgroundColor(this.petGender === 'male' ? '#EEF2FF' : '#F8F9FA')
              .borderRadius(16)
              .margin({ left: 16 })
              .border({
                width: 2,
                color: this.petGender === 'male' ? '#6A82FB' : 'transparent',
                radius: 16
              })
              .onClick(() => this.petGender = 'male')
            }
            .width('100%')
          }
          .width('100%')
          .margin({ bottom: 30 })

          // 签订契约按钮
          Button('🤝 签订契约')
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .width('100%')
            .height(52)
            .borderRadius(26)
            .backgroundColor('#FC5C7D')
            .fontColor('#FFFFFF')
            .shadow({
              radius: 12,
              color: 'rgba(252, 92, 125, 0.3)',
              offsetY: 4
            })
            .onClick(() => this.savePetInfo())
        }
        .width('90%')
        .padding(24)
        .backgroundColor('#FFFFFF')
        .borderRadius(24)
        .shadow({
          radius: 20,
          color: 'rgba(0, 0, 0, 0.08)',
          offsetY: 4
        })
      }
      .width('100%')
      .height('100%')
      .justifyContent(FlexAlign.Center)
      .alignItems(HorizontalAlign.Center)

      // 日期选择器弹窗
      if (this.showDatePicker) {
        Stack() {
          // 遮罩层
          Column()
            .width('100%')
            .height('100%')
            .backgroundColor('rgba(0, 0, 0, 0.5)')
            .onClick(() => this.showDatePicker = false)

          // 日期选择器弹窗卡片
          Column() {
            // 标题栏
            Row() {
              Text('选择生日')
                .fontSize(18)
                .fontWeight(FontWeight.Bold)
                .fontColor('#2C3E50')
              Blank()
              Text('✕')
                .fontSize(22)
                .fontColor('#95A5A6')
                .onClick(() => this.showDatePicker = false)
            }
            .width('100%')
            .padding({
              left: 24,
              right: 16,
              top: 20,
              bottom: 16
            })

            Divider().color('#EEEEEE').width('90%')

            // 官方 DatePicker 组件
            DatePicker({
              start: this.getStartDate(),
              end: this.getEndDate(),
              selected: this.selectedDate
            })
              .width('100%')
              .height(200)
              .margin({ top: 10 })
              .onDateChange((value: Date) => {
                this.selectedDate = value;
              })

            // 确认按钮
            Button('确认')
              .fontSize(16)
              .fontWeight(FontWeight.Medium)
              .width('85%')
              .height(44)
              .borderRadius(22)
              .backgroundColor('#FC5C7D')
              .fontColor('#FFFFFF')
              .margin({ top: 20, bottom: 20 })
              .onClick(() => this.confirmDate())
          }
          .width('90%')
          .backgroundColor('#FFFFFF')
          .borderRadius(24)
        }
        .width('100%')
        .height('100%')
      }
    }
    .width('100%')
    .height('100%')
  }
}
