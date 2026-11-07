import router from '@ohos.router'

@Entry
@Component
struct WelcomePage {
  @StorageLink('token') token: string = ''

  aboutToAppear(): void {
    // 如果已经登录，直接跳转到首页
    if (this.token) {
      router.replaceUrl({ url: 'pages/Index' })
    }
  }

  // 跳转到登录注册页面
  goToLogin(): void {
    router.pushUrl({ url: 'pages/LoginPage' })
  }

  build() {
    Stack() {
      // 背景图（已有登录注册字体）
      Image($r('app.media.background1'))
        .width('100%')
        .height('100%')
        .objectFit(ImageFit.Cover)

      // 在背景图登录注册字体底部添加透明按钮
      Button()
        .width('100%')
        .height(80)
        .position({ x: 0, y: '75%' }) // 调整到登录注册文字底部区域
        .backgroundColor('rgba(0, 0, 0, 0.001)') // 完全透明
        .onClick(() => this.goToLogin())
    }
    .width('100%')
    .height('100%')
  }
}
