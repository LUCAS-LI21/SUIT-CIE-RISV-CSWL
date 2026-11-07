// EnvironmentPanel.ets
// 环境看板组件

import { MonitorViewModel } from '../viewmodel/MonitorViewModel';

interface EnvironmentPanelProps {
  viewModel: MonitorViewModel;
}

@Component
export struct EnvironmentPanel {
  private viewModel: MonitorViewModel = new MonitorViewModel();
  @State showWarning: boolean = false;
  @State isDanger: boolean = false;

  aboutToAppear() {
    const envStatus = this.viewModel.checkEnvironmentStatus();
    this.showWarning = envStatus.status !== 'normal';
    this.isDanger = envStatus.status === 'danger';
  }

  build() {
    Column() {
      Row() {
        Text('环境看板')
          .fontSize(16)
          .fontWeight(FontWeight.Bold)
          .fontColor('#333333')
        Blank()
        Text('安全范围')
          .fontSize(12)
          .fontColor('#666666')
      }
      .width('100%')
      .padding({ top: 16, left: 16, right: 16, bottom: 12 })

      // 环境数据图表
      Stack() {
        // 背景渐变
        Stack()
          .width('100%')
          .height(200)
          .backgroundColor('#E8F5E8')

        // 模拟图表
        Column() {
          // 温度数据
          Row() {
            Text('温度')
              .fontSize(14)
              .fontColor('#333333')
            Blank()
            Text(`${this.viewModel.getEnvironmentData().length > 0 ? this.viewModel.getEnvironmentData()[this.viewModel.getEnvironmentData().length - 1].temperature : 0}°C`)
              .fontSize(14)
              .fontColor('#333333')
          }
          .width('90%')
          .margin({ bottom: 12 })

          // 湿度数据
          Row() {
            Text('湿度')
              .fontSize(14)
              .fontColor('#333333')
            Blank()
            Text(`${this.viewModel.getEnvironmentData().length > 0 ? this.viewModel.getEnvironmentData()[this.viewModel.getEnvironmentData().length - 1].humidity : 0}%`)
              .fontSize(14)
              .fontColor('#333333')
          }
          .width('90%')
          .margin({ bottom: 12 })

          // 光照强度数据
          Row() {
            Text('光照强度')
              .fontSize(14)
              .fontColor('#333333')
            Blank()
            Text(`${this.viewModel.getEnvironmentData().length > 0 ? this.viewModel.getEnvironmentData()[this.viewModel.getEnvironmentData().length - 1].lightIntensity : 0}%`)
              .fontSize(14)
              .fontColor('#333333')
          }
          .width('90%')
        }
        .width('100%')
        .height('100%')
        .justifyContent(FlexAlign.Center)
        .alignItems(HorizontalAlign.Center)

        // 环境状态
        if (this.showWarning) {
          Stack() {
            Circle()
              .width(60)
              .height(60)
              .fill(this.isDanger ? '#FFCDD2' : '#FFF3E0')
            Text(this.isDanger ? '⚠️' : '⚠')
              .fontSize(30)
          }
          .position({ x: '80%', y: '15%' })
        }
      }
      .width('100%')
      .height(200)
      .borderRadius(12)
      .margin({ left: 16, right: 16, bottom: 16 })
    }
    .width('100%')
    .backgroundColor('#FFFFFF')
    .borderRadius(16)
    .shadow({
      radius: 8,
      color: 'rgba(0, 0, 0, 0.1)',
      offsetY: 2
    })
  }
}