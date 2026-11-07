// pages/LoginPage.ets
import { promptAction } from '@kit.ArkUI';
import router from '@ohos.router';
import { notificationManager } from '@kit.NotificationKit';
import http from '@ohos.net.http';
import { KeyCode } from '@kit.InputKit';
import { hilog } from '@kit.PerformanceAnalysisKit';

// 日志标签
const DOMAIN = 0x0001;
const PREFIX = 'LoginPage';

// API响应类型定义
interface ApiResponse {
  success: boolean;
  message: string;
  code?: string;
}

// HTTP响应类型定义
interface HttpResponse {
  responseCode: number;
  result: string;
}

@Entry
@Component
struct LoginPage {
  @State currentTab: number = 0; // 0-登录，1-注册
  @State phone: string = '13800138000'
  @State password: string = ''
  @State code: string = ''
  @State newCode: string = ''
  @State isAgreed: boolean = false
  @State count: number = 0 //验证码倒计时
  @State showCodeDialog: boolean = false // 是否显示验证码弹窗
  private timerId: number = -1 //验证码定时器
  @State baseUrl: string = 'http://43.198.237.51:3000' // 服务器地址

  // 切换Tab
  switchTab(index: number): void {
    this.currentTab = index;
    // 清空表单内容
    this.phone = '13800138000';
    this.password = '';
    this.code = '';
    this.newCode = '';
    this.isAgreed = false;
    // 清除验证码倒计时
    if (this.timerId !== -1) {
      clearInterval(this.timerId);
      this.timerId = -1;
      this.count = 0;
    }
  }

  build() {
    Column() {
      // 顶部空白区域，用于向下移动整体内容
      Blank()
        .height(250)

      // 顶部标题
      // Text('欢迎！')
      //   .fontSize(28)
      //   .fontWeight(FontWeight.Bold)
      //   .fontColor('#FF8C69')
      //   .margin({ bottom: 20 })


      // // 猫咪图片
      // Image($r('app.media.Cat'))
      //   .width(200)
      //   .height(120)
      //   .margin({ bottom: 20 })

      // 表单容器（带外边框）
      Column() {
        // 切换Tab区域
        Row() {
          // 左侧登录Tab
          Column() {
            Text('账号登录')
              .fontSize(16)
              .fontColor(this.currentTab === 0 ? '#FF8C69' : '#999999')
              .fontWeight(FontWeight.Bold)
          }
          .width('50%')
          .height(40)
          .justifyContent(FlexAlign.Center)
          .backgroundColor('#FFFFFF')
          .borderRadius({
            topLeft: 10,
            topRight: 0,
            bottomLeft: 0,
            bottomRight: 0
          })
          .onClick(() => this.switchTab(0))

          // 右侧注册Tab
          Column() {
            Text('注册账号')
              .fontSize(16)
              .fontColor(this.currentTab === 1 ? '#FF8C69' : '#999999')
              .fontWeight(FontWeight.Bold)
          }
          .width('50%')
          .height(40)
          .justifyContent(FlexAlign.Center)
          .backgroundColor('#FFFFFF')
          .borderRadius({
            topLeft: 0,
            topRight: 10,
            bottomLeft: 0,
            bottomRight: 0
          })
          .onClick(() => this.switchTab(1))
        }

        // 表单区域（带外边框）
        Column() {
          // 登录表单
          if (this.currentTab === 0) {
            Column({ space: 15 }) {
              TextInput({ placeholder: '请输入手机号/用户名', text: this.phone })
                .width('85%')
                .height(45)
                .backgroundColor('#FFFFFF')
                .borderRadius(25)
                .padding({ left: 20, right: 20 })
                .border({ width: 1, color: '#E6C8A0' })
                .onChange((value: string) => this.phone = value)

              TextInput({ placeholder: '请输入密码', text: this.password })
                .width('85%')
                .height(45)
                .backgroundColor('#FFFFFF')
                .borderRadius(25)
                .padding({ left: 20, right: 20 })
                .border({ width: 1, color: '#E6C8A0' })
                .type(InputType.Password)
                .onChange((value: string) => this.password = value)

              Row() {
                Checkbox()
                  .width(15)
                  .height(15)
                  .select(true)
                  .onChange((value: boolean) => this.isAgreed = value)
                Text('保存密码')
                  .fontSize(14)
              }
              .width('85%')
              .justifyContent(FlexAlign.End)

              Button('登录')
                .width('85%')
                .height(45)
                .fontSize(18)
                .backgroundColor('#FF8C69')
                .borderRadius(25)
                .onClick(() => this.ll())
            }
            .width('100%')
            .padding({ top: 20, bottom: 20 })
            .alignItems(HorizontalAlign.Center)
          } else {
            // 注册表单
            Column({ space: 15 }) {
              TextInput({ placeholder: '请输入手机号/用户名' })  //, text: this.phone 后面要加上
                .width('85%')
                .height(45)
                .backgroundColor('#FFFFFF')
                .borderRadius(25)
                .padding({ left: 20, right: 20 })
                .border({ width: 1, color: '#E6C8A0' })
                .onChange((value: string) => this.phone = value)

              Row() {
                TextInput({ placeholder: '请输入验证码', text: this.code })
                  .layoutWeight(1)
                  .height(45)
                  .backgroundColor('#FFFFFF')
                  .borderRadius(25)
                  .padding({ left: 20, right: 20 })
                  .border({ width: 1, color: '#E6C8A0' })
                  .type(InputType.Number)
                  .onChange((value: string) => {
                    this.code = value
                  })
                Button(`${this.count > 0 ? this.count + 's' : '获取验证码'}`)
                  .width('40%')
                  .height(45)
                  .backgroundColor('#E6C8A0')
                  .borderRadius(25)
                  .fontColor('#FF8C69')
                  .fontSize(13)
                  .enabled(this.count === 0)
                  .onClick(() => {
                    this.sendCode()
                  })
              }
              .width('85%')

              TextInput({ placeholder: '请输入密码' }) //, text: this.password 后面要加上
                .width('85%')
                .height(45)
                .backgroundColor('#FFFFFF')
                .borderRadius(25)
                .padding({ left: 20, right: 20 })
                .border({ width: 1, color: '#E6C8A0' })
                .type(InputType.Password)
                .onChange((value: string) => this.password = value)


              Button('注册')
                .width('85%')
                .height(45)
                .fontSize(18)
                .backgroundColor('#FF8C69')
                .borderRadius(25)
                .onClick(() => this.register())

            }
            .width('100%')
            .padding({ top: 20, bottom: 20 })
            .alignItems(HorizontalAlign.Center)
          }
        }
        .width('100%')
        .backgroundColor('#FFF8F0')
        .border({ width: 2, color: '#E6C8A0' })
        .borderRadius({
          topLeft: 0,
          topRight: 0,
          bottomLeft: 10,
          bottomRight: 10
        })
      }
      .width('85%')
      .alignItems(HorizontalAlign.Center)
      .margin({ bottom: 30 })

      // 隐私协议
      Row({ space: 5 }) {
        Checkbox({ name: 'agreement', group: 'agreementGroup' })
          .select(true)
          .width(15)
          .height(15)
          .onChange((value: boolean) => this.isAgreed = value)
        Text() {
          Span('我已阅读并同意')
            .fontSize(14)
          Span('《隐私协议》')
            .fontSize(14)
            .fontColor('#FF8C69')
        }
      }
      .margin({ bottom: 30 })

      // 其他登录方式
      Column({ space: 15 }) {
        Text('其他登录方式')
          .fontSize(14)
          .fontColor('#666')
        Row({ space: 40 }) {
          Column({ space: 5 }) {
            Image($r('app.media.weixin'))
              .width(40).height(40).borderRadius(20)
            Text('微信')
              .fontSize(12)
              .fontColor('#666')
          }

          Column({ space: 5 }) {
            Image($r('app.media.zhifubao'))
              .width(40).height(40).borderRadius(20)
            Text('支付宝')
              .fontSize(12)
              .fontColor('#666')
          }
        }
      }
    }
    .width('100%')
    .height('100%')
    .backgroundImage($r('app.media.background2'))
    .backgroundImageSize(ImageSize.Cover)
    .backgroundImagePosition(Alignment.Center)
    .alignItems(HorizontalAlign.Center)
  }

  //发送验证码
  async sendCode() {
    // 手机号正则校验：11位数字，以1开头，第2位为3-9
    if (!this.phone.trim() || !/^1[3-9]\d{9}$/.test(this.phone)) {
      this.getUIContext().getPromptAction().showToast({ message: '请输入正确的手机号' })
      return
    }

    try {
      const httpRequest = http.createHttp()
      const response = await httpRequest.request(`${this.baseUrl}/api/send-code`, {
        method: http.RequestMethod.POST,
        header: {
          'Content-Type': 'application/json'
        },
        extraData: {
          phone: this.phone
        }
      }) as HttpResponse

      if (response.responseCode === 200) {
        const data = JSON.parse(response.result) as ApiResponse
        if (data.success) {
          // 存储后端返回的验证码
          if (data.code) {
            this.newCode = data.code
          }
          hilog.info(DOMAIN, PREFIX, `[验证码] 手机号: %{public}s, 验证码: %{public}s`, this.phone, this.newCode)
          await notificationManager.requestEnableNotification()
          const notification: notificationManager.NotificationRequest = {
            id: 1,
            content: {
              notificationContentType: notificationManager.ContentType.NOTIFICATION_CONTENT_BASIC_TEXT,
              normal: {
                title: '萌宠乐园',
                text: `您的验证码为${this.newCode}，有效期为5分钟，请勿泄露给他人`
              }
            }
          }
          await notificationManager.publish(notification)
          this.getUIContext().getPromptAction().showToast({ message: '您的验证码已发送，请注意查收' })
          // 开启倒计时
          this.count = 60
          this.timerId = setInterval(() => {
            this.count--
            if (this.count <= 0) {
              clearInterval(this.timerId)
            }
          }, 1000)
        } else {
          this.getUIContext().getPromptAction().showToast({ message: data.message })
        }
      } else {
        this.getUIContext().getPromptAction().showToast({ message: '验证码发送失败' })
      }
    } catch (error) {
      this.getUIContext().getPromptAction().showToast({ message: '网络错误，请稍后重试' })
    }
  }

  ll() {
    setTimeout(() => {
      // router.replaceUrl({ url: 'pages/Index' })
      //跳转到宠物选择页面
      router.replaceUrl({ url: 'pages/PetTypeSelectionPage' })
    }, 500)
  }

  //登录
  async login() {
    // 手机号正则校验：11位数字，以1开头，第2位为3-9
    if (!this.phone.trim() || !/^1[3-9]\d{9}$/.test(this.phone)) {
      this.getUIContext().getPromptAction().showToast({ message: '请输入正确的手机号' })
      return
    }
    // 密码正则校验：至少8位，包含大小写字母
    if (!this.password.trim() || !/^(?=.*[a-z])(?=.*[A-Z]).{8,}$/.test(this.password)) {
      this.getUIContext().getPromptAction().showToast({ message: '密码需至少8位，包含大小写字母' })
      return
    }
    if (!this.isAgreed) {
      this.getUIContext().getPromptAction().showToast({ message: '请同意隐私协议' })
      return
    }

    try {
      const httpRequest = http.createHttp()
      const response = await httpRequest.request(`${this.baseUrl}/api/login`, {
        method: http.RequestMethod.POST,
        header: {
          'Content-Type': 'application/json'
        },
        extraData: {
          phone: this.phone,
          password: this.password
        }
      }) as HttpResponse

      if (response.responseCode === 200) {
        const data = JSON.parse(response.result) as ApiResponse
        if (data.success) {
          this.getUIContext().getPromptAction().showToast({ message: '登录成功' })
          setTimeout(() => {
            // router.replaceUrl({ url: 'pages/Index' })
            //跳转到宠物选择页面
            router.replaceUrl({ url: 'pages/PetTypeSelectionPage' })
          }, 500)
        } else {
          this.getUIContext().getPromptAction().showToast({ message: data.message })
        }
      } else {
        this.getUIContext().getPromptAction().showToast({ message: '登录失败' })
      }
    } catch (error) {
      this.getUIContext().getPromptAction().showToast({ message: '网络错误，请稍后重试' })
    }
  }

  //注册
  async register() {
    // 手机号正则校验：11位数字，以1开头，第2位为3-9
    if (!this.phone.trim() || !/^1[3-9]\d{9}$/.test(this.phone)) {
      this.getUIContext().getPromptAction().showToast({ message: '请输入正确的手机号' })
      return
    }
    if (!this.code.trim()) {
      this.getUIContext().getPromptAction().showToast({ message: '请输入验证码' })
      return
    }
    // 密码正则校验：至少8位，包含大小写字母
    if (!this.password || !/^(?=.*[a-z])(?=.*[A-Z]).{8,}$/.test(this.password)) {
      this.getUIContext().getPromptAction().showToast({ message: '密码需至少8位，包含大小写字母' })
      return
    }

    if (!this.isAgreed) {
      this.getUIContext().getPromptAction().showToast({ message: '请同意隐私协议' })
      return
    }

    try {
      const httpRequest = http.createHttp()
      const response = await httpRequest.request(`${this.baseUrl}/api/register`, {
        method: http.RequestMethod.POST,
        header: {
          'Content-Type': 'application/json'
        },
        extraData: {
          phone: this.phone,
          password: this.password,
          code: this.code
        }
      }) as HttpResponse

      if (response.responseCode === 200) {
        const data = JSON.parse(response.result) as ApiResponse
        if (data.success) {
          this.getUIContext().getPromptAction().showToast({ message: '注册成功' })
          setTimeout(() => {
            this.switchTab(0)
          }, 500)
        } else {
          this.getUIContext().getPromptAction().showToast({ message: data.message })
        }
      } else {
        this.getUIContext().getPromptAction().showToast({ message: '注册失败' })
      }
    } catch (error) {
      this.getUIContext().getPromptAction().showToast({ message: '网络错误，请稍后重试' })
    }
  }
}