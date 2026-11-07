// HealthReport.ets
// 健康报告组件

import { MonitorViewModel } from '../viewmodel/MonitorViewModel';

interface HealthReportProps {
  viewModel: MonitorViewModel;
}

@Component
export struct HealthReport {
  private viewModel: MonitorViewModel = new MonitorViewModel();
  @State healthStatusMessage: string = '';
  @State healthStatusColor: string = '#4CAF50';

  aboutToAppear() {
    if (!this.viewModel) {
      console.error('ViewModel not initialized');
      return;
    }
    const healthStatus = this.viewModel.checkPetHealthStatus();
    this.healthStatusMessage = healthStatus.message;
    this.healthStatusColor = healthStatus.status === 'danger' ? '#F44336' : healthStatus.status === 'warning' ? '#FF9800' : '#4CAF50';
  }

  build() {
    Column() {
      Row() {
        Text('健康报告')
          .fontSize(16)
          .fontWeight(FontWeight.Bold)
          .fontColor('#333333')
        Blank()
        Text('今日')
          .fontSize(12)
          .fontColor('#666666')
      }
      .width('100%')
      .padding({ top: 16, left: 16, right: 16, bottom: 12 })

      // 健康数据图表
      Stack() {
        // 背景渐变
        Stack()
          .width('100%')
          .height(200)
          .backgroundColor('#F3E5F5')

        // 模拟图表
        Column() {
          // 情绪数据
          Row() {
            Column() {
              Text('平静')
                .fontSize(12)
                .fontColor('#666666')
              Text(`${this.viewModel.getHealthData().length > 0 ? this.viewModel.getHealthData()[this.viewModel.getHealthData().length - 1].calm : 0}%`)
                .fontSize(14)
                .fontColor('#333333')
            }
            .margin({ right: 20 })
            
            Column() {
              Text('兴奋')
                .fontSize(12)
                .fontColor('#666666')
              Text(`${this.viewModel.getHealthData().length > 0 ? this.viewModel.getHealthData()[this.viewModel.getHealthData().length - 1].excited : 0}%`)
                .fontSize(14)
                .fontColor('#333333')
            }
            .margin({ right: 20 })
            
            Column() {
              Text('求关注')
                .fontSize(12)
                .fontColor('#666666')
              Text(`${this.viewModel.getHealthData().length > 0 ? this.viewModel.getHealthData()[this.viewModel.getHealthData().length - 1].attention : 0}%`)
                .fontSize(14)
                .fontColor('#333333')
            }
            .margin({ right: 20 })
            
            Column() {
              Text('不适')
                .fontSize(12)
                .fontColor('#666666')
              Text(`${this.viewModel.getHealthData().length > 0 ? this.viewModel.getHealthData()[this.viewModel.getHealthData().length - 1].discomfort : 0}%`)
                .fontSize(14)
                .fontColor('#333333')
            }
          }
          .width('90%')
          .margin({ bottom: 20 })

          // 健康状态
          Text(this.healthStatusMessage)
            .fontSize(14)
            .fontColor(this.healthStatusColor)
            .fontWeight(FontWeight.Medium)
        }
        .width('100%')
        .height('100%')
        .justifyContent(FlexAlign.Center)
        .alignItems(HorizontalAlign.Center)
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