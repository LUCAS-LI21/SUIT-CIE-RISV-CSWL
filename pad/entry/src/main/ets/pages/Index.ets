// Index.ets - 修正版
// 应用主页 - 底部Tab导航容器

import { HomeTab } from '../tabs/HomeTab-Realistic';
import { ChatTab } from '../tabs/ChatTab';
import { MonitorTab } from '../tabs/MonitorTab';
import { MineTab } from '../tabs/MineTab';
import { ControlTab } from '../tabs/ControlTab';

interface TabItem {
  title: string;
  icon: ResourceStr;
}

@Entry
@Component
struct Index {
  @State currentTabIndex: number = 0;
  private tabsController: TabsController = new TabsController();
  // Tab数据
  // 顺序：首页 / 对话 / 监控 / 监测 / 我的
  // 「监控」是 MQTT 那页（连小板做环境与执行机构控制），
  // 「监测」是 K1 感知页，「对话」是 K1 算力分析页 —— 三者数据来源完全不同，别合并。
  private tabs: TabItem[] = [
    { title: '首页', icon: $r('app.media.home') },
    { title: '对话', icon: $r('app.media.chat') },
    { title: '监控', icon: $r('app.media.jiankong') },
    { title: '监测', icon: $r('app.media.jiance') },
    { title: '我的', icon: $r('app.media.mine') }
  ];

  // 自定义TabBar构建器
  @Builder
  TabBarBuilder() {
    Row() {
      ForEach(this.tabs, (item: TabItem, index: number) => {
        Column({ space: 4 }) {
          Image(item.icon)
            .width(24)
            .height(24)
            .fillColor(this.currentTabIndex === index ? '#6A82FB' : '#7F8C8D')
          Text(item.title)
            .fontSize(13)
            .fontColor(this.currentTabIndex === index ? '#6A82FB' : '#7F8C8D')
            .margin({ top: 4 })
        }
        .width('20%')
        .height(40)
        .justifyContent(FlexAlign.Center)
        .onClick(() => {
          this.currentTabIndex = index;
          this.tabsController.changeIndex(index); // 关键：通过控制器切换Tab
        })
      })
    }
    .width('100%')
    .height(50)
    .padding({ top: 10 })
    .backgroundColor('#FFFFFF')
    .padding({ bottom: 8, top: 12 })
  }

  build() {
    Column() {
      // 页面内容区
      Tabs({
        controller: this.tabsController,
        index: this.currentTabIndex
      }) {
        TabContent() {
          HomeTab()
        }
        .tabBar(this.TabBarBuilder)

        // 对话页：调 K1 算力分析四路数据，回答宠物当前状态。
        // 原来 ChatTab 只 import 没挂载（死代码），用户点不进去。
        TabContent() {
          ChatTab()
        }
        .tabBar(this.TabBarBuilder)

        TabContent() {
         ControlTab()
        }
        .tabBar(this.TabBarBuilder)

        TabContent() {
          MonitorTab()
        }
        .tabBar(this.TabBarBuilder)

        TabContent() {
          MineTab()
        }
        .tabBar(this.TabBarBuilder)
      }
      .scrollable(false)
      .barHeight(0) // 隐藏默认TabBar
      .onChange((index: number) => {
        this.currentTabIndex = index;
      })
      .width('100%')
      .layoutWeight(1)

      // 底部分隔线
      Divider()
        .color('#EEEEEE')
        .width('100%')
        .height(1)

      // 自定义底部导航栏
      this.TabBarBuilder()
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#F8F9FA')
  }
}