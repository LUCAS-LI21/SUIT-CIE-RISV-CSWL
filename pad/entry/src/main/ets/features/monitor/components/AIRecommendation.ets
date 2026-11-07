// AIRecommendation.ets
// AI建议组件

import { MonitorViewModel, AIRecommendation as AIRecommendationType } from '../viewmodel/MonitorViewModel';

interface AIRecommendationProps {
  viewModel: MonitorViewModel;
}

@Component
export struct AIRecommendation {
  private viewModel: MonitorViewModel = new MonitorViewModel();

  aboutToAppear() {
    // 初始化逻辑
  }

  build() {
    Column() {
      Row() {
        Text('AI建议')
          .fontSize(16)
          .fontWeight(FontWeight.Bold)
          .fontColor('#333333')
        Blank()
        Text('🔍')
          .fontSize(16)
      }
      .width('100%')
      .padding({ top: 16, left: 16, right: 16, bottom: 12 })

      // AI建议列表
      Column() {
        ForEach(this.viewModel.getAIRecommendations(), (recommendation: AIRecommendationType, index: number) => {
          Row() {
            Text(recommendation.type === 'home' ? '🏠' : '🏥')
              .fontSize(16)
              .margin({ right: 12 })
            Text(recommendation.content)
              .fontSize(14)
              .fontColor('#333333')
              .flexGrow(1)
            Text(recommendation.priority === 'high' ? '紧急' : recommendation.priority === 'medium' ? '重要' : '一般')
              .fontSize(12)
              .fontColor(recommendation.priority === 'high' ? '#F44336' : recommendation.priority === 'medium' ? '#FF9800' : '#4CAF50')
              .backgroundColor(recommendation.priority === 'high' ? '#FFEBEE' : recommendation.priority === 'medium' ? '#FFF3E0' : '#E8F5E8')
              .padding({ left: 8, right: 8, top: 2, bottom: 2 })
              .borderRadius(12)
          }
          .width('100%')
          .padding({ top: 8, bottom: 8, left: 16, right: 16 })
          .backgroundColor(index % 2 === 0 ? '#F8F9FA' : '#FFFFFF')
        })
      }
      .width('100%')

      // 交互按钮
      Button('查看详情')
        .fontSize(14)
        .fontWeight(FontWeight.Medium)
        .width('80%')
        .height(44)
        .borderRadius(22)
        .backgroundColor('#4CAF50')
        .fontColor('#FFFFFF')
        .margin({ top: 16, bottom: 16 })
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