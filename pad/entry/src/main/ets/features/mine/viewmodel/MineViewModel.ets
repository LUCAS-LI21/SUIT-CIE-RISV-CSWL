// UserInfoPage.ets
// 用户信息页面

import { promptAction } from '@kit.ArkUI';
import { router } from '@kit.ArkUI';
import { preferences } from '@kit.ArkData';
import { AppConstants } from '../../../common/constants/AppConstants';
import { photoAccessHelper } from '@kit.MediaLibraryKit';
import { MqttMessage } from '@ohos/mqtt';
import MQTTUtil from '../../../common/utils/MQTTUtil';
import { hilog } from '@kit.PerformanceAnalysisKit';

const DOMAIN = 0x0001;
const TAG = 'MineViewModel';

interface Pet {
  id: string;
  name: string;
  gender: string;
  age: string;
  weight: string;
  avatar: string;
}

interface UserInfo {
  name: string;
  avatar: string;
  gender: string;
  posts: number;
  followers: number;
  following: number;
  likes: number;
}

// 硬件状态接口
interface HardwareStatus {
  temp?: number;
  hum?: number;
  light?: number;
  status?: string;
  led?: string;
  motor?: string;
}

// locker2 开锁指令接口
interface LockerCommand {
  sle_data?: string;
  device?: string;
}

// MQTT消息联合接口（覆盖locker2开锁指令与硬件状态两种格式）
interface MqttPayload {
  sle_data?: string;
  device?: string;
  temp?: number;
  hum?: number;
  light?: number;
  status?: string;
  led?: string;
  motor?: string;
}

@Preview
@Component
export struct MineViewModel {
  @State userInfo: UserInfo = {
    name: '泡芙喵',
    avatar: '🐱',
    gender: '女',
    posts: 58,
    followers: 4512,
    following: 23,
    likes: 929
  };
  @State petName: string = '旺财'; // 宠物名称
  @State petBirthDate: string = '2024-01-01'; // 宠物出生日期
  @State petGender: string = 'female'; // 宠物性别
  @State petType: string = 'cat'; // 宠物类型
  @State petWeight: number = 8; // 宠物体重(kg)
  @State daysWithPet: number = 99;
  @State showEditDialog: boolean = false;
  @State editName: string = '';
  @State editGender: string = '';
  private context = getContext(this);
  // 随机体重数组
  private weights: number[] = [8, 12, 7, 9, 10];
  // MQTT实例
  private mqttUtil: MQTTUtil | null = null;
  // 硬件状态（从MQTT接收）
  @State hardwareStatus: HardwareStatus = {};
  // 环境舒适开关状态
  @State isEnvironmentOn: boolean = false;

  // 调用相册拣选更换头像
  async changeAvatar(): Promise<void> {
    const photoPicker: photoAccessHelper.PhotoViewPicker = new photoAccessHelper.PhotoViewPicker()
    const options: photoAccessHelper.PhotoSelectOptions = new photoAccessHelper.PhotoSelectOptions()
    options.MIMEType = photoAccessHelper.PhotoViewMIMETypes.IMAGE_TYPE
    options.maxSelectNumber = 1
    const result: photoAccessHelper.PhotoSelectResult = await photoPicker.select(options)
    if (result.photoUris.length > 0) {
      this.userInfo.avatar = result.photoUris[0]
      // this.avatarChanged = true
    }
  }

  // 计算从出生日期到今天的天数
  private calculateDaysWithPet(birthDate: string): number {
    try {
      const birth = new Date(birthDate);
      const today = new Date();
      const diffTime = today.getTime() - birth.getTime();
      const diffDays = Math.floor(diffTime / (1000 * 60 * 60 * 24));
      return diffDays > 0 ? diffDays : 0;
    } catch (error) {
      console.error('[MineViewModel] 计算天数失败', JSON.stringify(error));
      return 0;
    }
  }

  // 加载宠物信息
  async loadPetInfo(): Promise<void> {
    try {
      const prefs = await preferences.getPreferences(this.context, AppConstants.PREF_NAME);

      // 读取宠物信息
      this.petName = await prefs.get('petName', '旺财') as string;
      this.petBirthDate = await prefs.get('petBirthDate', '2024-01-01') as string;
      this.petGender = await prefs.get('petGender', 'female') as string;
      this.petType = await prefs.get('petType', 'cat') as string;

      // 读取体重,如果没有则随机生成
      const savedWeight = await prefs.get('petWeight', 0) as number;
      if (savedWeight > 0) {
        this.petWeight = savedWeight;
      } else {
        // 随机选择一个体重
        this.petWeight = this.weights[Math.floor(Math.random() * this.weights.length)];
        await prefs.put('petWeight', this.petWeight);
        await prefs.flush();
      }

      // 计算在一起的天数
      this.daysWithPet = this.calculateDaysWithPet(this.petBirthDate);

      console.info(`[MineViewModel] 读取宠物信息: 名称=${this.petName}, 生日=${this.petBirthDate}, 性别=${this.petGender}, 体重=${this.petWeight}kg, 相处天数=${this.daysWithPet}`);
    } catch (error) {
      console.error('[MineViewModel] 加载宠物信息失败', JSON.stringify(error));
    }
  }

  // 编辑宠物信息
  editPetInfo() {
    this.getUIContext().getPromptAction().showToast({ message: '编辑宠物信息功能开发中' });
  }

  // 编辑用户信息
  editUserInfo() {
    this.editName = this.userInfo.name;
    this.editGender = this.userInfo.gender;
    this.showEditDialog = true;
  }

  // 保存用户信息
  saveUserInfo() {
    if (!this.editName.trim()) {
      this.getUIContext().getPromptAction().showToast({ message: '姓名不能为空' });
      return;
    }
    this.userInfo.name = this.editName.trim();
    this.userInfo.gender = this.editGender;
    this.showEditDialog = false;
  }

  // 添加宠物
  addPet() {
    this.getUIContext().getPromptAction().showToast({ message: '添加宠物功能开发中' });
  }

  // 跳转到设置页面
  goToSettings() {
    this.getUIContext().getPromptAction().showToast({ message: '设置页面开发中' });
  }

  // 跳转到隐私中心
  goToPrivacy() {
    this.getUIContext().getPromptAction().showToast({ message: '隐私中心开发中' });
  }

  // 跳转到问题反馈页面
  goToFeedback() {
    this.getUIContext().getPromptAction().showToast({ message: '问题反馈页面开发中' });
  }

  // 退出登录
  logout() {
    this.getUIContext().getPromptAction().showToast({ message: '退出登录' });
    // 实际项目中这里应该跳转到登录页面
    router.replaceUrl({ url: 'pages/LoginPage' });
  }

  // ========== 宠物控制功能（MQTT - locker2主题） ==========

  // 初始化MQTT连接（订阅locker2主题）
  private initMQTT(): void {
    if (!this.mqttUtil) {
      hilog.info(DOMAIN, TAG, `[initMQTT] 开始初始化MQTT连接（locker2主题）`);
      this.mqttUtil = new MQTTUtil();
      this.mqttUtil.connectLocker((err: Error, data: MqttMessage) => {
        if (err) {
          hilog.error(DOMAIN, TAG, `[initMQTT] MQTT连接错误: %{public}s`, err.message);
          return;
        }
        try {
          if (data && data.payload) {
            const payloadStr = data.payload as string;
            hilog.info(DOMAIN, TAG, `[messageArrived] 收到locker2消息, topic: %{public}s, payload: %{public}s`,
              data.topic ?? 'unknown', payloadStr);
            const json: MqttPayload = JSON.parse(payloadStr) as MqttPayload;

            // 处理 locker2 开锁指令：{"sle_data":"key opening","device":"locker_sle_client"}
            if (json.device === 'locker_sle_client' && json.sle_data === 'key opening') {
              hilog.info(DOMAIN, TAG, `[locker2] ★ 检测到开锁指令 => 自动开启喂食功能`);
              this.autoFeedFromLocker();
              return;
            }

            hilog.info(DOMAIN, TAG, `[locker2] 收到非开锁消息, device: %{public}s, sle_data: %{public}s`,
              json.device ?? 'N/A', json.sle_data ?? 'N/A');
          }
        } catch (e) {
          hilog.error(DOMAIN, TAG, `[messageArrived] 解析locker2数据失败: %{public}s`, JSON.stringify(e));
        }
      });
    } else {
      hilog.info(DOMAIN, TAG, `[initMQTT] MQTT已连接，无需重复初始化`);
    }
  }

  // 喂食
  async feedPet(): Promise<void> {
    try {
      this.initMQTT();
      const payload = JSON.stringify({ motor: 'feed' });
      console.info('[MineViewModel] 发送喂食指令:', payload);
      if (this.mqttUtil) {
        const success = await this.mqttUtil.publish('ctrl', payload, 1);
        if (success) {
          this.getUIContext().getPromptAction().showToast({ message: '喂食指令已发送' });
        } else {
          this.getUIContext().getPromptAction().showToast({ message: '发送失败，请重试' });
        }
      } else {
        this.getUIContext().getPromptAction().showToast({ message: 'MQTT未连接' });
      }
    } catch (error) {
      console.error('[MineViewModel] 喂食指令发送失败:', error);
      this.getUIContext().getPromptAction().showToast({ message: '发送异常' });
    }
  }

  // 收到locker2开锁指令后自动开启喂食
  async autoFeedFromLocker(): Promise<void> {
    try {
      this.initMQTT();
      const payload = JSON.stringify({ motor: 'feed' });
      hilog.info(DOMAIN, TAG, `[autoFeed] ★ locker2开锁触发自动喂食, payload: %{public}s`, payload);
      if (this.mqttUtil) {
        const success = await this.mqttUtil.publish('ctrl', payload, 1);
        if (success) {
          hilog.info(DOMAIN, TAG, `[autoFeed] ★ 自动喂食指令发布成功`);
          this.getUIContext().getPromptAction().showToast({ message: '检测到开锁，已自动喂食' });
        } else {
          hilog.error(DOMAIN, TAG, `[autoFeed] 自动喂食指令发布失败`);
          this.getUIContext().getPromptAction().showToast({ message: '自动喂食发送失败' });
        }
      } else {
        hilog.error(DOMAIN, TAG, `[autoFeed] MQTT未连接，无法发送`);
        this.getUIContext().getPromptAction().showToast({ message: 'MQTT未连接' });
      }
    } catch (error) {
      hilog.error(DOMAIN, TAG, `[autoFeed] 自动喂食异常: %{public}s`, JSON.stringify(error));
    }
  }

  // 逗宠
  async playWithPet(): Promise<void> {
    try {
      this.initMQTT();
      const payload = JSON.stringify({ motor: 'play_dog' });
      console.info('[MineViewModel] 发送逗宠指令:', payload);
      if (this.mqttUtil) {
        const success = await this.mqttUtil.publish('ctrl', payload, 1);
        if (success) {
          this.getUIContext().getPromptAction().showToast({ message: '逗宠指令已发送' });
        } else {
          this.getUIContext().getPromptAction().showToast({ message: '发送失败，请重试' });
        }
      } else {
        this.getUIContext().getPromptAction().showToast({ message: 'MQTT未连接' });
      }
    } catch (error) {
      console.error('[MineViewModel] 逗宠指令发送失败:', error);
      this.getUIContext().getPromptAction().showToast({ message: '发送异常' });
    }
  }

  // 环境舒适（点击切换 on/off）
  async setEnvironmentComfort(): Promise<void> {
    try {
      this.initMQTT();

      if (this.mqttUtil) {
        // 切换状态
        this.isEnvironmentOn = !this.isEnvironmentOn;
        const status: string = this.isEnvironmentOn ? 'on' : 'off';

        console.info(`[MineViewModel] 环境舒适模式切换为: ${status}`);

        // 第一次发送：电机控制 {"motor":"on/off"}
        const motorPayload = JSON.stringify({ motor: status });
        console.info('[MineViewModel] 发送电机指令:', motorPayload);
        const motorSuccess = await this.mqttUtil.publish('ctrl', motorPayload, 1);

        if (motorSuccess) {
          console.info('[MineViewModel] 电机指令发送成功');
        } else {
          console.error('[MineViewModel] 电机指令发送失败');
        }

        // 第二次发送：LED控制 {"led":"on/off"}
        const ledPayload = JSON.stringify({ led: status });
        console.info('[MineViewModel] 发送LED指令:', ledPayload);
        await this.mqttUtil.publish('ctrl', ledPayload, 1);

        // 第三次发送：音乐控制 {"music":"on/off"}
        const musicPayload = JSON.stringify({ music: status });
        console.info('[MineViewModel] 发送音乐指令:', musicPayload);
        const musicSuccess = await this.mqttUtil.publish('ctrl', musicPayload, 1);

        if (musicSuccess) {
          console.info('[MineViewModel] 音乐指令发送成功');
          const msg = this.isEnvironmentOn ? '环境舒适模式已开启' : '环境舒适模式已关闭';
          this.getUIContext().getPromptAction().showToast({ message: msg });
        } else {
          console.error('[MineViewModel] 音乐指令发送失败');
          this.getUIContext().getPromptAction().showToast({ message: '部分指令发送失败' });
        }
      } else {
        this.getUIContext().getPromptAction().showToast({ message: 'MQTT未连接' });
      }
    } catch (error) {
      console.error('[MineViewModel] 环境舒适指令发送失败:', error);
      this.getUIContext().getPromptAction().showToast({ message: '发送异常' });
    }
  }

  // 清理MQTT连接
  private cleanupMQTT(): void {
    if (this.mqttUtil) {
      this.mqttUtil = null;
      console.info('[MineViewModel] MQTT连接已清理');
    }
  }

  aboutToAppear(): void {
    this.loadPetInfo(); // 加载宠物信息
    this.initMQTT(); // 初始化MQTT连接，订阅pet与locker2主题，确保能接收开锁喂食指令
  }

  // 获取性别符号
  private getGenderSymbol(): string {
    return this.petGender === 'male' ? '♂' : '♀';
  }

  // 获取宠物头像
  private getPetAvatar(): Resource {
    return this.petType === 'dog' ? $r('app.media.type_dog') : $r('app.media.type_cat');
  }

  build() {
    Stack() {
      Column() {
          // 顶部标题栏
          Row() {
            Blank()
              .layoutWeight(1)
            Text('铲屎官')
              .fontSize(19)
              .fontWeight(FontWeight.Bold)
              .fontColor('#333333')
              .margin({ left: 30 })
            Blank()
              .layoutWeight(1)
            Row() {
              Image($r('app.media.sys'))
                .width(32)
                .height(32)
                .margin({ right: 14 })
              Image($r('app.media.daka'))
                .width(22)
                .height(22)
            }
            .margin({ right: 18 })
          }
          .width('100%')
          .height(50)
          .alignItems(VerticalAlign.Center)

          // 用户信息卡片
          Column() {
            Row() {
              // 头像
              Column() {
                Image(this.userInfo.avatar.startsWith('file://') || this.userInfo.avatar.startsWith('/') ?
                  this.userInfo.avatar : $r('app.media.user'))
                  .width(62)
                  .height(62)
                  .backgroundColor('#FFFFFF')
                  .borderRadius('50%')
                  .objectFit(ImageFit.Cover)

                Text('点击更换')
                  .fontSize(11)
                  .fontColor('#666666')
                  .margin({ top: 5 })
              }
              .margin({ right: 16 })
              .onClick(() => this.changeAvatar())

              // 用户信息
              Column() {
                //用户名和性别
                Row() {
                  Text(this.userInfo.name)
                    .fontSize(17)
                    .fontWeight(FontWeight.Bold)
                    .fontColor('#333333')
                  Text(this.userInfo.gender === '男' ? '♂' : '♀')
                    .fontSize(15)
                    .fontColor(this.userInfo.gender === '男' ? '#4169E1' : '#FF69B4')
                    .margin({ left: 8, right: 56 })
                  Text('编辑')
                    .fontSize(13)
                    .fontColor('#666666')
                    .onClick(() => this.editUserInfo())
                }
                .margin({ bottom: 10 })
                .width('100%')

                //数据
                Row({ space: 16 }) {
                  Column({ space: 5 }) {
                    Text(this.userInfo.posts.toString())
                      .fontSize(14)
                      .fontWeight(FontWeight.Bold)
                      .fontColor('#333333')
                    Text('动态')
                      .fontSize(11)
                      .fontColor('#666666')
                  }

                  Column({ space: 5 }) {
                    Text(this.userInfo.followers.toString())
                      .fontSize(14)
                      .fontWeight(FontWeight.Bold)
                      .fontColor('#333333')
                    Text('粉丝')
                      .fontSize(11)
                      .fontColor('#666666')
                  }

                  Column({ space: 5 }) {
                    Text(this.userInfo.following.toString())
                      .fontSize(14)
                      .fontWeight(FontWeight.Bold)
                      .fontColor('#333333')
                    Text('关注')
                      .fontSize(11)
                      .fontColor('#666666')
                  }

                  Column({ space: 5 }) {
                    Text(this.userInfo.likes.toString())
                      .fontSize(14)
                      .fontWeight(FontWeight.Bold)
                      .fontColor('#333333')
                    Text('喜欢')
                      .fontSize(11)
                      .fontColor('#666666')
                  }
                }
              }
              .alignItems(HorizontalAlign.Start)
              .width('100%')
            }
          }
          .width('90%')
          .padding({
            top: 10,
            bottom: 10,
            left: 10,
            right: 10
          })
          .margin({ top: 8, bottom: 6 })

          // 与宠物相处时间
          Stack() {
            Image($r('app.media.background_time'))
              .width('100%')
              .height(56)
              .objectFit(ImageFit.Fill)
            Row() {
              Blank()
                .layoutWeight(1)
              Text(`与${this.petName}在一起已经${this.daysWithPet}天了`)
                .fontSize(14)
                .fontColor('#333333')
                .margin({ right: 18 })
            }
            .width('100%')
            .height('100%')
          }
          .width('95%')
          .height(56)
          .margin({ bottom: 6, top: 0 })

          // 我的宠物信息卡片
          Stack() {
            Column() {
              Row() {
                Text('我的宠物')
                  .fontSize(15)
                  .fontWeight(FontWeight.Bold)
                  .fontColor('#333333')
                  .backgroundColor('#FFF9E6')
                  .padding({
                    left: 7,
                    right: 7,
                    top: 2,
                    bottom: 2
                  })
                Blank()
                Text('>')
                  .fontSize(15)
                  .fontColor('#999999')
              }
              .width('100%')
              .padding({
                left: 18,
                right: 18,
                top: 10,
                bottom: 7
              })

              // 单个宠物信息卡片
              Row() {
                Image(this.getPetAvatar())
                  .width(36)
                  .margin({ right: 12 })
                // 右侧信息
                Column() {
                  Row() {
                    Text(this.petName)
                      .fontSize(15)
                      .fontWeight(FontWeight.Bold)
                      .fontColor('#333333')
                    Text(this.getGenderSymbol())
                      .fontSize(15)
                      .fontColor(this.petGender === 'male' ? '#4169E1' : '#FF69B4')
                      .margin({ left: 8 })
                  }

                  Row({ space: 14 }) {
                    Text(`体重: ${this.petWeight}kg`)
                      .fontSize(12)
                      .fontColor('#666666')
                    Text(`已相处: ${this.daysWithPet}天`)
                      .fontSize(12)
                      .fontColor('#666666')
                  }
                  .margin({ top: 3 })

                  Text(`出生日期: ${this.petBirthDate}`)
                    .fontSize(12)
                    .fontColor('#666666')
                    .margin({ top: 3 })
                }
                .alignItems(HorizontalAlign.Start)
              }
              .width('100%')
              .alignItems(VerticalAlign.Center)
              .padding({
                top: 10,
                bottom: 10,
                left: 18,
                right: 18
              })

              Divider()
                .color('#EEEEEE')
                .width('100%')
                .height(1)
                .margin({ left: 18, right: 18 })

              // 添加宠物按钮
              Row() {
                Text('➕')
                  .fontSize(15)
                  .margin({ right: 7 })
                Text('添加宠物')
                  .fontSize(13)
                  .fontColor('#999999')
              }
              .width('100%')
              .height(40)
              .justifyContent(FlexAlign.Center)
              .onClick(() => this.addPet())
            }
            .width('90%')
            .backgroundColor('#FFFFFF')
            .borderRadius(18)
            .border({ width: 1, color: '#000000' })
            .margin({ bottom: 8 })
          }

          // 宠物控制按钮模块
          Stack() {
            Column() {
              Row() {
                Text('宠物互动')
                  .fontSize(15)
                  .fontWeight(FontWeight.Bold)
                  .fontColor('#333333')
                  .backgroundColor('#FFF9E6')
                  .padding({
                    left: 7,
                    right: 7,
                    top: 2,
                    bottom: 2
                  })
                Blank()
                Text('🎮')
                  .fontSize(15)
              }
              .width('100%')
              .padding({
                left: 18,
                right: 18,
                top: 10,
                bottom: 6
              })

              // 三个按钮
              Row({ space: 10 }) {
                // 喂食按钮
                Column({ space: 5 }) {
                  Image($r('app.media.type_dog'))
                    .width(24)
                    .height(24)
                  Text('喂食')
                    .fontSize(12)
                    .fontWeight(FontWeight.Medium)
                    .fontColor('#8B6914')
                }
                .width('30%')
                .height(52)
                .backgroundColor('#FFF3D6')
                .borderRadius(12)
                .justifyContent(FlexAlign.Center)
                .onClick(() => this.feedPet())

                // 逗宠按钮
                Column({ space: 5 }) {
                  Image($r('app.media.type_cat'))
                    .width(24)
                    .height(24)
                  Text('逗宠')
                    .fontSize(12)
                    .fontWeight(FontWeight.Medium)
                    .fontColor('#5A9B8C')
                }
                .width('30%')
                .height(52)
                .backgroundColor('#D4EDE9')
                .borderRadius(12)
                .justifyContent(FlexAlign.Center)
                .onClick(() => this.playWithPet())

                // 环境舒适按钮
                Column({ space: 5 }) {
                  Image($r('app.media.Settings'))
                    .width(24)
                    .height(24)
                  Text(this.isEnvironmentOn ? '已开启' : '环境舒适')
                    .fontSize(12)
                    .fontWeight(FontWeight.Medium)
                    .fontColor(this.isEnvironmentOn ? '#4CAF50' : '#6B7280')
                }
                .width('30%')
                .height(52)
                .backgroundColor(this.isEnvironmentOn ? '#C8E6C9' : '#E5E7EB')
                .borderRadius(12)
                .justifyContent(FlexAlign.Center)
                .onClick(() => this.setEnvironmentComfort())
              }
              .width('100%')
              .justifyContent(FlexAlign.Center)
              .padding({
                bottom: 10,
                left: 10,
                right: 10
              })
            }
            .width('90%')
            .backgroundColor('#FFFFFF')
            .borderRadius(18)
            .border({ width: 1, color: '#000000' })
            .margin({ bottom: 8 })
          }

          // 功能列表
          Stack() {
            Column() {
              // 设置
              Row() {
                Image($r('app.media.Settings'))
                  .width(20)
                  .height(20)
                  .margin({ right: 11 })
                Text('设置')
                  .fontSize(15)
                  .fontColor('#333333')
                Blank()
                Text('>')
                  .fontSize(15)
                  .fontColor('#999999')
              }
              .width('100%')
              .height(44)
              .alignItems(VerticalAlign.Center)
              .padding({ left: 18, right: 18 })
              .onClick(() => this.goToSettings())

              Divider()
                .color('#EEEEEE')
                .width('100%')
                .height(1)
                .margin({ left: 50 })

              // 隐私中心
              Row() {
                Image($r('app.media.Privacy'))
                  .width(20)
                  .height(20)
                  .margin({ right: 11 })
                Text('隐私中心')
                  .fontSize(15)
                  .fontColor('#333333')
                Blank()
                Text('>')
                  .fontSize(15)
                  .fontColor('#999999')
              }
              .width('100%')
              .height(44)
              .alignItems(VerticalAlign.Center)
              .padding({ left: 18, right: 18 })
              .onClick(() => this.goToPrivacy())

              Divider()
                .color('#EEEEEE')
                .width('100%')
                .height(1)
                .margin({ left: 50 })

              // 问题反馈
              Row() {
                Image($r('app.media.question'))
                  .width(20)
                  .height(20)
                  .margin({ right: 11 })
                Text('问题反馈')
                  .fontSize(15)
                  .fontColor('#333333')
                Blank()
                Text('>')
                  .fontSize(15)
                  .fontColor('#999999')
              }
              .width('100%')
              .height(44)
              .alignItems(VerticalAlign.Center)
              .padding({ left: 18, right: 18 })
              .onClick(() => this.goToFeedback())
            }
          }
          .width('90%')
          .backgroundColor('#FFFFFF')
          .borderRadius(18)
          .border({ width: 1, color: '#000000' })
          .margin({ bottom: 10 })

          // 退出登录按钮
          Button('退出登录')
            .fontSize(15)
            .fontColor('#FF6B6B')
            .backgroundColor('#FFFFFF')
            .border({ width: 1, color: '#FF6B6B' })
            .borderRadius(24)
            .width('90%')
            .height(42)
            .margin({ bottom: 12 })
            .onClick(() => this.logout())
        }
        .width('100%')
        .height('100%')
        .padding({ top: 8, bottom: 8 })
        .justifyContent(FlexAlign.SpaceEvenly)
      .backgroundColor('#F0F8F0')
        // .linearGradient({
        //   angle: 180,
        //   colors: [
        //     ['#FFF9E6', 0],
        //     ['#FFE4B5', 1]
        //   ]
        // })

      // 编辑弹窗
      this.editDialog()
    }
    .width('100%')
    .height('100%')
  }

  // 编辑用户信息弹窗
  @Builder
  editDialog() {
    if (this.showEditDialog) {
      Column() {
        Column() {
          Text('编辑用户信息')
            .fontSize(20)
            .fontWeight(FontWeight.Bold)
            .fontColor('#333333')
            .margin({ top: 24, bottom: 24 })

          Column({ space: 20 }) {
            // 姓名输入
            Column({ space: 8 }) {
              Text('姓名')
                .fontSize(14)
                .fontColor('#666666')
                .alignSelf(ItemAlign.Start)
              TextInput({ placeholder: '请输入姓名', text: this.editName })
                .fontSize(16)
                .height(50)
                .onChange((value: string) => {
                  this.editName = value;
                })
            }
            .width('100%')
            .alignItems(HorizontalAlign.Start)

            // 性别选择
            Column({ space: 8 }) {
              Text('性别')
                .fontSize(14)
                .fontColor('#666666')
                .alignSelf(ItemAlign.Start)
              Row({ space: 20 }) {
                Row() {
                  Radio({ value: '男', group: 'gender' })
                    .checked(this.editGender === '男')
                    .onChange((isChecked: boolean) => {
                      if (isChecked) {
                        this.editGender = '男';
                      }
                    })
                  Text('男')
                    .fontSize(16)
                    .fontColor('#333333')
                    .margin({ left: 8 })
                }
                .onClick(() => {
                  this.editGender = '男';
                })

                Row() {
                  Radio({ value: '女', group: 'gender' })
                    .checked(this.editGender === '女')
                    .onChange((isChecked: boolean) => {
                      if (isChecked) {
                        this.editGender = '女';
                      }
                    })
                  Text('女')
                    .fontSize(16)
                    .fontColor('#333333')
                    .margin({ left: 8 })
                }
                .onClick(() => {
                  this.editGender = '女';
                })
              }
            }
            .width('100%')
            .alignItems(HorizontalAlign.Start)
          }
          .margin({ left: 24, right: 24, bottom: 24 })

          // 按钮
          Row({ space: 12 }) {
            Button('取消')
              .fontSize(16)
              .fontColor('#666666')
              .backgroundColor('#F5F5F5')
              .layoutWeight(1)
              .height(48)
              .borderRadius(24)
              .onClick(() => {
                this.showEditDialog = false;
              })

            Button('保存')
              .fontSize(16)
              .fontColor('#FFFFFF')
              .backgroundColor('#fff3576d')
              .layoutWeight(1)
              .height(48)
              .borderRadius(24)
              .onClick(() => {
                this.saveUserInfo();
              })
          }
          .width('100%')
          .padding({ left: 24, right: 24, bottom: 24 })
        }
        .width('80%')
        .backgroundColor('#FFFFFF')
        .borderRadius(20)
        .shadow({
          radius: 20,
          color: 'rgba(0,0,0,0.15)',
          offsetX: 0,
          offsetY: 5
        })
      }
      .width('100%')
      .height('100%')
      .backgroundColor('rgba(0,0,0,0.5)')
      .justifyContent(FlexAlign.Center)
      .onClick(() => {
        this.showEditDialog = false;
      })
    }
  }
}
