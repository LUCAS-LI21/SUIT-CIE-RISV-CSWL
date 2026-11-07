// PetMonitorPage.ets
// 萌宠监护中心-环境看板区页面

import { promptAction } from '@kit.ArkUI';
import { LineChart, LineDataSet, LineData, JArrayList, EntryOhos } from '@ohos/mpchart';
import { MqttMessage } from '@ohos/mqtt';
import MQTTUtil from '../../common/utils/MQTTUtil';
import EnvironmentModel from '../../product/model/EnvironmentModel';

// 数据模型定义
interface PetMonitorData {
  temp: number; // 温度（℃）
  hum: number; // 湿度（%）
  light: number; // 光照（lux）
  status: string; // 状态：Calm/Excited/NeedAttention/Uncomfortable
  excitement: number; // 兴奋度值（0-100）- 南向新增参数
}

// AI建议接口
interface AIRecommendation {
  type: string; // 建议类型：warning/info/attention
  content: string; // 建议内容
  icon: string; // 图标
}

// 健康报告数据接口
interface HealthReportData {
  date: string; // 日期
  statusData: StatusData[]; // 状态数据
  measures: Measure[]; // 适宜措施
  waveData: number[]; // 波形图数据
}

// 状态数据接口
interface StatusData {
  name: string; // 状态名称
  count: number; // 出现次数
  color: string; // 状态颜色
}

// 适宜措施接口
interface Measure {
  status: string; // 状态名称
  measure: string; // 建议措施
  color: string; // 措施颜色
}

// 定义点的接口
interface Point {
  x: number;
  y: number;
}

interface GeneratedObjectLiteralInterface_1 {
  "motor": string;
}

@Entry
@Component
export struct PetMonitorPage {
  //mqtt数据
  @State item: EnvironmentModel = new EnvironmentModel();
  // 状态管理
  @State petMonitorData: PetMonitorData = {
    temp: this.item.temp,
    hum: this.item.hum,
    light: this.item.light,
    status: "Calm",
    excitement: 0
  };
  @State aiRecommendations: AIRecommendation[] = [];
  @State refreshing: boolean = false;
  @State updateInterval: number | null = null;
  @State mqttDataReceived: boolean = false; // 标记是否已收到MQTT数据
  private mqttUtil: MQTTUtil | null = null; // 保存MQTTUtil实例
  @State motorStatus: string = 'off'; // 电机状态：on/off
  // 图表数据
  @State barData: number[] = [80, 65, 95, 75];
  @State lineData: number[] = [35, 45, 85, 40];
  // 基于心情状态的波形图数据
  @State moodBasedData: number[] = [2, 2, 2, 2];
  // 健康报告数据
  @State healthReportData: HealthReportData | null = null;
  @State isLoading: boolean = false;
  @State error: string | null = null;

  // 页面加载时初始化数据
  aboutToAppear() {
    this.fetchHealthReportData();
    this.startMQTTConnection();
    this.startAutoUpdate();
  }

  // 启动MQTT连接并获取初始数据
  startMQTTConnection() {
    console.info('========== Starting MQTT Connection ==========');

    this.mqttUtil = new MQTTUtil();
    this.mqttUtil.connectPet(
      (err: Error, data: MqttMessage) => {
        //如果没有出错就处理数据
        if (!err) {
          try {
            //将payload转换为字符串
            const payloadStr = String(data.payload);
            console.info('========== MQTT Debug ==========');
            console.info('Received MQTT payload:', payloadStr);
            console.info('Timestamp:', new Date().toLocaleTimeString());

            //定义接收数据的类型接口
            interface MqttDataType {
              temp: number;
              hum: number;
              light: number;
              excitement: number;  // 新增：兴奋度值
              status: string;
            }

            //解析接收到的数据为EnvironmentViewModel对象
            // 硬件端会把无读数的氨气发成 inf（非法 JSON），整条会 parse 失败 ——
            // 这一页原来没做容错，所以只有那条不带 inf 的 retained 旧报文能解析成功，
            // 实时报文全被丢掉，界面永远停在 25/50/300。词法降级成 null 即可。
            const safePayload: string = payloadStr
              .replace(/:-?inf(inity)?/gi, ':null')
              .replace(/:-?nan/gi, ':null');
            const parsedData = JSON.parse(safePayload) as MqttDataType;
            console.info('Parsed data:', JSON.stringify(parsedData));

            //验证解析后的数据结构并映射到EnvironmentViewModel
            if (parsedData && typeof parsedData === 'object') {
              // 更新item数据（用于其他模块）
              this.item.temp = parsedData.temp ?? 0;
              this.item.hum = parsedData.hum ?? 0;
              this.item.light = parsedData.light ?? 0;
              this.item.status = parsedData.status ?? "";

              // 根据status字符串映射到stat数字
              switch (this.item.status) {
                case 'Calm':
                  this.item.stat = 0;
                  break;
                case 'Warning':
                  this.item.stat = 1;
                  break;
                case 'Danger':
                  this.item.stat = 2;
                  break;
                default:
                  this.item.stat = 0;
              }

              // 更新图表显示的真实数据（保留原有逻辑，添加excitement）
              this.petMonitorData = {
                temp: parsedData.temp ?? 0,
                hum: parsedData.hum ?? 0,
                light: parsedData.light ?? 0,
                status: parsedData.status ?? 'Calm',
                excitement: parsedData.excitement ?? 0  // 接收南向兴奋度值
              };

              // 根据真实数据更新心情状态波形图
              this.updateMoodBasedData();

              // 每次收到新数据都实时更新AI建议（基于兴奋度动态变化）
              this.generateAIRecommendations();

              // 首次收到数据标记
              if (!this.mqttDataReceived) {
                this.mqttDataReceived = true;
                console.info('========== Initial MQTT Data Received ==========');
              }

              console.info('Updated item:', JSON.stringify(this.item));
              console.info('Updated petMonitorData:', JSON.stringify(this.petMonitorData));
            }
            console.info('===============================');
          } catch (parseError) {
            console.error('========== MQTT Parse Error ==========');
            console.error('Failed to parse MQTT message:', parseError);
            console.error('Payload was:', String(data.payload));
            console.error('======================================');
            //解析失败时使用默认值
            this.item = new EnvironmentModel();
          }
        } else {
          console.error('MQTT Error:', err);
        }
      },
      'pet_app_monitor'
    );
    console.info('MQTT Connection initiated, waiting for data...');
  }

  // 手动请求最新数据（从MQTT订阅获取）
  requestFreshData() {
    console.info('Requesting fresh MQTT data...');
    // 由于MQTT是推送模式,这里不需要主动请求
    // 连接建立后,硬件会每秒自动推送数据
    // 此函数可用于显示加载状态或触发界面更新
    this.refreshing = true;
    setTimeout(() => {
      this.refreshing = false;
    }, 500);
  }

  // 页面销毁时清理定时器和MQTT连接
  aboutToDisappear() {
    this.stopAutoUpdate();
    this.cleanupMQTTConnection();
  }

  // 清理MQTT连接
  cleanupMQTTConnection() {
    if (this.mqttUtil) {
      console.info('========== Cleaning up MQTT Connection ==========');
      // MQTTUtil可能提供断开连接的方法,如果没有则只需释放引用
      this.mqttUtil = null;
      this.mqttDataReceived = false;
      console.info('MQTT Connection cleaned up');
    }
  }

  // 模拟数据获取 - 现在使用MQTT真实数据，此函数仅用于生成AI建议
  fetchData() {
    this.refreshing = true;

    // 如果已经收到MQTT数据,生成基于真实数据的AI建议
    if (this.mqttDataReceived) {
      this.generateAIRecommendations();
    } else {
      // 如果还未收到数据,先请求一次
      this.requestFreshData();
    }
    this.refreshing = false;
  }

  // 随机生成宠物状态
  getRandomStatus(): string {
    const statuses: string[] = ["Calm", "Excited", "NeedAttention", "Uncomfortable"];
    return statuses[Math.floor(Math.random() * statuses.length)];
  }

  // 开始自动更新数据
  startAutoUpdate() {
    if (this.updateInterval === null) {
      console.info('========== Starting Auto Update ==========');
      this.updateInterval = 1;
      // 每秒更新一次,与硬件数据推送频率同步
      setInterval(() => {
        console.info('Auto update triggered at:', new Date().toLocaleTimeString());
        // MQTT会自动推送数据,这里主要用于确保UI及时响应
        // 如果需要主动触发刷新逻辑,可以在这里添加
        this.requestFreshData();
      }, 1000); // 1秒更新一次
      console.info('Auto update started (1 second interval)');
    }
  }

  // 停止自动更新
  stopAutoUpdate() {
    if (this.updateInterval) {
      clearInterval(this.updateInterval);
      this.updateInterval = null;
    }
  }

  // 获取健康报告数据 - 使用MQTT真实数据
  fetchHealthReportData() {
    this.isLoading = true;
    this.error = null;

    // 基于MQTT实时数据生成健康报告
    setTimeout(() => {
      const realData: HealthReportData = {
        date: new Date().toLocaleDateString(),
        statusData: [
          { name: "平静", count: this.petMonitorData.excitement < 20 ? 1 : 0, color: "#FF6B6B" },
          { name: "活跃", count: (this.petMonitorData.excitement >= 20 && this.petMonitorData.excitement < 50) ? 1 : 0, color: "#FFD166" },
          { name: "兴奋", count: (this.petMonitorData.excitement >= 50 && this.petMonitorData.excitement < 75) ? 1 : 0, color: "#06D6A0" },
          { name: "狂躁", count: this.petMonitorData.excitement >= 75 ? 1 : 0, color: "#118AB2" }
        ],
        measures: [
          { status: "平静", measure: "保持当前环境", color: "#FF6B6B" },
          { status: "活跃", measure: "增加互动频率", color: "#FFD166" },
          { status: "兴奋", measure: "给予关注回应", color: "#06D6A0" },
          { status: "狂躁", measure: "检查宠物状况", color: "#118AB2" }
        ],
        waveData: this.moodBasedData
      };

      this.healthReportData = realData;
      this.isLoading = false;
    }, 500);
  }

  // 生成AI建议
  generateAIRecommendations() {
    const recommendations: AIRecommendation[] = [];

    // 温度建议
    if (this.item.temp > 30) {
      recommendations.push({
        type: "warning",
        content: "增加玩具互动频率，当前环境温度偏高建议降温",
        icon: "⚠️"
      });
    }

    // 湿度建议
    if (this.item.hum > 60) {
      recommendations.push({
        type: "info",
        content: "环境湿度偏高建议除湿",
        icon: "💧"
      });
    } else if (this.item.hum < 40) {
      recommendations.push({
        type: "info",
        content: "环境干燥，建议增加宠物饮水或加湿",
        icon: "💧"
      });
    }

    // 光照建议
    if (this.item.light > 120) {
      recommendations.push({
        type: "info",
        content: "光照较强，建议避免宠物长时间暴晒",
        icon: "☀️"
      });
    }

    // 状态建议
    if (this.item.status === "Excited") {
      recommendations.push({
        type: "attention",
        content: "宠物求关注，建议多陪伴互动",
        icon: "🐾"
      });
    } else if (this.item.status === "Hyper") {
      recommendations.push({
        type: "warning",
        content: "宠物状态不适，建议检查宠物健康状况",
        icon: "😟"
      });
    }

    // 基于加速度/兴奋度的宠物健康小知识（简短动态）
    const excite: number = this.petMonitorData.excitement;
    if (excite >= 0 && excite < 20) {
      // 平静状态 - 根据数值动态变化
      const calmTips: string[] = [
        `宠物当前平静（${excite}），适合进行日常护理如梳毛、剪指甲`,
        `兴奋度偏低(${excite})，建议用逗猫棒或飞盘引导轻度运动`,
        `宠物较安静，此时是训练简单指令的最佳时机`,
        `活动量不足，可安排10-15分钟散步唤醒活力`
      ];
      recommendations.push({
        type: "info",
        content: "🐾 " + calmTips[Math.floor(excite / 5) % calmTips.length],
        icon: "😊"
      });
    } else if (excite >= 20 && excite < 50) {
      // 活跃状态 - 根据数值动态变化
      const activeTips: string[] = [
        `宠物活跃度良好（${excite}），适合益智玩具互动`,
        `精神饱满！推荐嗅闻垫或藏食游戏消耗精力`,
        `${excite > 35 ? '高度' : '中度'}活跃期，可延长游戏时间至20分钟`,
        `活跃状态是学习新技巧的黄金时间`
      ];
      recommendations.push({
        type: "info",
        content: "⚡ " + activeTips[Math.floor((excite - 20) / 8) % activeTips.length],
        icon: "🎮"
      });
    } else if (excite >= 50 && excite < 75) {
      // 兴奋状态 - 根据数值动态变化
      const excitedTips: string[] = [
        `宠物兴奋度较高（${excite}），建议高强度拔河或奔跑游戏`,
        `能量过剩！设置障碍跑道引导释放多余精力`,
        `${excite > 65 ? '接近狂躁' : '兴奋'}状态，注意观察防止过度激动`,
        `适合进行15分钟以上持续性运动来平复情绪`
      ];
      recommendations.push({
        type: "attention",
        content: "🔥 " + excitedTips[Math.floor((excite - 50) / 8) % excitedTips.length],
        icon: "⚠️"
      });
    } else if (excite >= 75 && excite <= 100) {
      // 狂躁状态 - 根据数值动态变化
      const hyperTips: string[] = [
        `检测到狂躁状态（${excite}）！请立即调暗灯光、降低噪音安抚`,
        `兴奋度过高，将宠物转移到安静舒适的空间休息`,
        `${excite > 90 ? '极度' : ''}狂躁持续超15分钟建议检查健康状况`,
        `焦虑表现明显，用轻柔语调并提供熟悉玩具安抚`
      ];
      recommendations.push({
        type: "warning",
        content: "🚨 " + hyperTips[Math.floor((excite - 75) / 8) % hyperTips.length],
        icon: "😰"
      });
    }

    // 如果没有特殊建议，添加一个通用建议
    if (recommendations.length === 0) {
      recommendations.push({
        type: "info",
        content: "宠物状态良好，继续保持当前环境",
        icon: "😊"
      });
    }

    this.aiRecommendations = recommendations;
  }

  // 设置LineChart图表
  setupChart() {
    if (!this.healthReportData || this.healthReportData.waveData.length === 0) {
      return;
    }

    // 创建数据点
    const entries = new JArrayList<EntryOhos>();

    // 使用健康报告的waveData数据
    const waveData = this.healthReportData.waveData;

    // 确保waveData长度为4，对应四个状态
    const dataLength = Math.max(waveData.length, 4);

    for (let i = 0; i < dataLength; i++) {
      const value = i < waveData.length ? waveData[i] : 0;
      // 归一化数据到0-5范围
      const normalizedValue = Math.abs(value) + 1;
      entries.add(new EntryOhos(i, normalizedValue));
    }

    // 创建数据集
    const dataSet = new LineDataSet(entries, '健康指数');
    // 注意：mpchart库的方法可能与预期不同，这里使用通用的设置方式
    // 实际使用时需要参考mpchart库的具体API文档

    // 创建数据
    const lineData = new LineData();
    lineData.addDataSet(dataSet);

    // 注意：实际使用时，需要通过LineChart组件的属性或方法设置数据
    // 这里只是演示如何准备数据
  }

  // 生成小图表的波形图路径
  generateWavePathSmall(data: number[]): string {
    if (!data || data.length === 0) {
      return '';
    }

    // 确保数据长度为4，对应四个状态
    const dataLength = Math.max(data.length, 4);
    const points: Point[] = [];

    for (let i = 0; i < dataLength; i++) {
      const value = i < data.length ? data[i] : 0;
      // 归一化数据到0-5范围
      const normalizedValue = Math.abs(value) + 1;
      // 计算坐标
      const x = 40 + i * 60;
      const y = 130 - normalizedValue * 20;
      points.push({ x, y });
    }

    // 生成路径命令
    let path = `M ${points[0].x} ${points[0].y}`;
    for (let i = 1; i < points.length; i++) {
      path += ` L ${points[i].x} ${points[i].y}`;
    }

    return path;
  }

  // 生成大图表的波形图路径
  generateWavePathLarge(data: number[]): string {
    if (!data || data.length === 0) {
      return '';
    }

    // 确保数据长度为4，对应四个状态
    const dataLength = Math.max(data.length, 4);
    const points: Point[] = [];

    for (let i = 0; i < dataLength; i++) {
      const value = i < data.length ? data[i] : 0;
      // 确保数值在0-5范围内
      const clampedValue = Math.max(0, Math.min(5, Math.abs(value)));
      // 计算坐标，确保在图表区域内
      const chartHeight = 130;
      const chartWidth = 100;
      const xOffset = 30;
      const yOffset = 145;
      const x = xOffset + (i / (dataLength - 1)) * chartWidth;
      const y = yOffset - (clampedValue / 5) * chartHeight;
      points.push({ x, y });
    }

    // 生成路径命令
    let path = `M ${points[0].x} ${points[0].y}`;
    for (let i = 1; i < points.length; i++) {
      path += ` L ${points[i].x} ${points[i].y}`;
    }

    return path;
  }

  // 根据心情状态更新波形图数据
  updateMoodBasedData(): void {
    // 根据心情状态调整数据
    switch (this.petMonitorData.status) {
      case 'Calm': // 平静
        // 保持相对平稳的曲线
        this.moodBasedData = [2, 2, 2, 2];
        break;
      case 'Active': // 兴奋
        // 生成波动较大的曲线
        this.moodBasedData = [3, 5, 4, 3];
        break;
      case 'Excited': // 求关注
        // 生成中等波动的曲线
        this.moodBasedData = [1, 3, 2, 4];
        break;
      case 'Hyper': // 不适
        // 生成较低且波动的曲线
        this.moodBasedData = [1, 2, 1, 0];
        break;
      default:
      // 默认数据
        this.moodBasedData = [2, 3, 2, 1];
    }
  }

  // 根据兴奋度获取状态文字（用于状态分析显示）
  getExcitementLevelText(): string {
    const excite: number = this.petMonitorData.excitement;
    if (excite < 20) return "平静";
    if (excite < 50) return "活跃";
    if (excite < 75) return "兴奋";
    return "狂躁";
  }

  // 计算心情数据点的位置
  getMoodDataPoints(): Point[] {
    const points: Point[] = [];
    for (let i = 0; i < this.moodBasedData.length; i++) {
      const value = this.moodBasedData[i];
      // 确保数值在0-5范围内
      const clampedValue = Math.max(0, Math.min(5, Math.abs(value)));
      // 计算坐标，确保在图表区域内
      const chartHeight = 130;
      const chartWidth = 100;
      const xOffset = 30;
      const yOffset = 145;
      const point: Point = {
        x: xOffset + (i / (this.moodBasedData.length - 1)) * chartWidth,
        y: yOffset - (clampedValue / 5) * chartHeight
      };
      points.push(point);
    }
    return points;
  }

  build() {
    Column() {
      // 顶部导航栏
      Row() {
        Image($r('app.media.dog_cute'))
          .width(40)
          .height(40)
          .margin({ left: 20 })
        Text('萌宠监护中心')
          .fontSize(24)
          .fontWeight(FontWeight.Bold)
          .fontColor('#333')
          .layoutWeight(1)
          .textAlign(TextAlign.Center)
        Text('🔍')
          .fontSize(20)
          .margin({ right: 20 })
      }
      .width('100%')
      .height(60)
      .alignItems(VerticalAlign.Center)

      // .backgroundColor('#4CAF50')

      // 主内容区域 - 使用Scroll保证从上往下排列
      Scroll() {
        Column() {
          // 第一行：环境看板 + 健康报告 + 状态分析（同行显示）
          Row() {
            // 左侧：环境看板
            Column() {
              Row() {
                Text('环境看板')
                  .fontSize(16)
                  .fontWeight(FontWeight.Bold)
                  .fontColor('#333333')
                Text('⚠️')
                  .fontSize(16)
                  .margin({ left: 8 })
              }
              .width('100%')
              .margin({ bottom: 16 })

              // 模拟图表
              Stack() {
                // 背景容器
                Column() {
                  // Y轴标签
                  ForEach([0, 20, 40, 60, 80, 100], (value: number) => {
                    Row() {
                      Text(value.toString())
                        .fontSize(10)
                        .fontColor('#666666')
                        .width(25)
                        .textAlign(TextAlign.End)
                      Blank()
                    }
                    .width('100%')
                    .height(30)
                  })
                }
                .width('100%')
                .height(180)
                .position({ x: 0, y: 30 })

                // 图表绘图区
                Stack() {
                  // 背景渐变
                  Stack()
                    .width('80%')
                    .height(180)
                    .linearGradient({
                      angle: 0,
                      colors: [
                        ['#FFE135', 0],
                        ['#FF3333', 1]
                      ]
                    })
                    .opacity(0.7)
                    .position({ x: 30, y: 0 })

                  // 水平网格线
                  ForEach([20, 40, 60, 80, 100], (value: number) => {
                    Stack()
                      .width('80%')
                      .height(1)
                      .backgroundColor('rgba(0, 0, 0, 0.1)')
                      .position({ x: 30, y: 180 - value * 1.8 })
                  })

                  // 垂直网格线
                  ForEach([0, 1, 2, 3], (value: number) => {
                    Stack()
                      .width(1)
                      .height(180)
                      .backgroundColor('rgba(0, 0, 0, 0.1)')
                      .position({ x: 30 + value * 25 + 12.5, y: 0 })
                  })

                  // 温度曲线点
                  // Stack() {
                  //   Text('🌡️')
                  //     .fontSize(14)
                  // }
                  // .position({ x: 48, y: Math.max(0, Math.min(180, 180 - this.petMonitorData.temp * 1.8)) - 20 })

                  Stack()
                    .width(10)
                    .height(10)
                    .backgroundColor('#FF3333')
                    .borderRadius(5)
                    .position({ x: 55, y: Math.max(0, Math.min(180, 180 - this.petMonitorData.temp * 1.8)) })

                  // 湿度曲线点
                  // Stack() {
                  //   Text('💧')
                  //     .fontSize(14)
                  // }
                  // .position({ x: 73, y: Math.max(0, Math.min(180, 180 - this.petMonitorData.hum * 1.8)) - 20 })

                  Stack()
                    .width(10)
                    .height(10)
                    .backgroundColor('#FFE135')
                    .borderRadius(5)
                    .position({ x: 80, y: Math.max(0, Math.min(180, 180 - this.petMonitorData.hum * 1.8)) })

                  // 光照曲线点
                  // Stack() {
                  //   Text('☀️')
                  //     .fontSize(14)
                  // }
                  // .position({ x: 98, y: Math.max(0, Math.min(180, 180 - (this.petMonitorData.light / 10) * 1.8)) - 20 })

                  Stack()
                    .width(10)
                    .height(10)
                    .backgroundColor('#4CAF50')
                    .borderRadius(5)
                    .position({ x: 105, y: Math.max(0, Math.min(180, 180 - (this.petMonitorData.light / 10) * 1.8)) })

                  // 云朵提示框
                  Stack() {
                    Text('☁️')
                      .fontSize(20)
                    Text('实时数据')
                      .fontSize(10)
                      .fontColor('#333333')
                      .position({ x: 0, y: -5 })
                  }
                  .position({ x: 50, y: 10 })
                }
                .width('100%')
                .height(180)
                .position({ x: 0, y: 30 })

                // X轴标签
                Row() {
                  ForEach(['温度', '湿度', '光照'], (label: string) => {
                    Text(label)
                      .fontSize(10)
                      .fontColor('#666666')
                      .width(25)
                      .textAlign(TextAlign.Center)
                  })
                }
                .width('80%')
                .position({ x: 42.5, y: 210 })
              }
              .width('100%')
              .height(240)

              // 安全值提示
              Column() {
                Row() {
                  Column() {
                    Text('温度')
                      .fontSize(12)
                      .fontColor('#FF3333')
                      .margin({ bottom: 4 })
                    Text(`${this.petMonitorData.temp.toFixed(1)}℃`)
                      .fontSize(12)
                      .fontColor('#333333')
                      .fontWeight(FontWeight.Bold)
                  }
                  .alignItems(HorizontalAlign.Center)
                  .layoutWeight(1)

                  Column() {
                    Text('湿度')
                      .fontSize(12)
                      .fontColor('#FFE135')
                      .margin({ bottom: 4 })
                    Text(`${this.petMonitorData.hum.toFixed(1)}%`)
                      .fontSize(12)
                      .fontColor('#333333')
                      .fontWeight(FontWeight.Bold)
                  }
                  .alignItems(HorizontalAlign.Center)
                  .layoutWeight(1)

                  Column() {
                    Text('光照')
                      .fontSize(12)
                      .fontColor('#4CAF50')
                      .margin({ bottom: 4 })
                    Text(`${this.petMonitorData.light.toFixed(0)}lx`)
                      .fontSize(12)
                      .fontColor('#333333')
                      .fontWeight(FontWeight.Bold)
                  }
                  .alignItems(HorizontalAlign.Center)
                  .layoutWeight(1)
                }
                .width('100%')

                Text('安全范围: 温度18-28℃, 湿度40-60%, 光照50-200lx')
                  .fontSize(10)
                  .fontColor('#999999')
                  .margin({ top: 8 })
              }
              .width('100%')
              .margin({ top: 16 })
            }
            .width('48%')
            .padding(20)
            .backgroundColor('#FFFFFF')
            .borderRadius(16)
            .shadow({
              radius: 4,
              color: 'rgba(0, 0, 0, 0.1)',
              offsetY: 4
            })

            // 右侧：健康报告 + 状态分析
            Column() {
              // 健康报告
              Column() {
                if (this.isLoading) {
                  Text('加载中...')
                    .fontSize(14)
                    .fontColor('#666666')
                    .margin({ top: 20 })
                } else if (this.error) {
                  Text(`加载失败: ${this.error}`)
                    .fontSize(14)
                    .fontColor('#FF6B6B')
                    .margin({ top: 20 })
                } else if (this.healthReportData) {
                  // 头部
                  Row() {
                    Text('健康报告')
                      .fontSize(16)
                      .fontWeight(FontWeight.Bold)
                      .fontColor('#333333')
                    Blank()
                    Text('评价报告')
                      .fontSize(12)
                      .fontColor('#666666')
                      .backgroundColor('#FFFFFF')
                      .padding({
                        left: 8,
                        right: 8,
                        top: 2,
                        bottom: 2
                      })
                      .borderRadius(12)
                  }
                  .width('100%')
                  .margin({ bottom: 12 })

                  // 使用mpchart库的波形图
                  Stack() {
                    // 浅蓝背景
                    Stack()
                      .width('100%')
                      .height(160)
                      .backgroundColor('#F0F8FF')

                    // Y轴标签（0-5）
                    ForEach([0, 1, 2, 3, 4, 5], (value: number) => {
                      Text(value.toString())
                        .fontSize(9)
                        .fontColor('#666666')
                        .position({ x: 10, y: 145 - value * 25 })
                    })

                    // X轴标签（状态分析字段）
                    ForEach(['平静', '兴奋', '求关注', '狂躁'], (item: string, index: number) => {
                      Text(item)
                        .fontSize(9)
                        .fontColor('#666666')
                        .position({ x: 30 + index * 33.33, y: 155 })
                    })

                    // 水平网格线
                    ForEach([0, 1, 2, 3, 4, 5], (value: number) => {
                      Stack()
                        .width('80%')
                        .height(1)
                        .backgroundColor('rgba(0, 0, 0, 0.1)')
                        .position({ x: 25, y: 145 - value * 25 })
                    })

                    // 垂直网格线（4个数据点需要5条线）
                    ForEach([0, 1, 2, 3, 4], (value: number) => {
                      Stack()
                        .width(1)
                        .height(130)
                        .backgroundColor('rgba(0, 0, 0, 0.1)')
                        .position({ x: 30 + value * 33.33, y: 15 })
                    })

                    // 基于心情状态的波形图
                    Stack() {
                      // 使用状态变量moodBasedData

                      // 绘制曲线
                      Path()
                        .width('100%')
                        .height(160)
                        .commands(this.generateWavePathLarge(this.moodBasedData))
                        .stroke('#3498db')
                        .strokeWidth(2)

                      // 曲线下方填充
                      Path()
                        .width('100%')
                        .height(160)
                        .commands(this.generateWavePathLarge(this.moodBasedData) + ' L 130 145 L 30 145 Z')
                        .fill('#E3F2FD')

                      // 绘制数据点
                      ForEach(this.getMoodDataPoints(), (point: Point) => {
                        Stack()
                          .width(6)
                          .height(6)
                          .backgroundColor('#FFFFFF')
                          .border({ width: 2, color: '#3498db' })
                          .borderRadius(3)
                          .position({ x: point.x - 3, y: point.y - 3 })
                      })
                    }

                    // Add data按钮
                    Stack() {
                      Text('刷新数据')
                        .fontSize(11)
                        .fontColor('#3498db')
                        .fontWeight(FontWeight.Bold)
                    }
                    .width('60%')
                    .height(25)
                    .backgroundColor('#F5F5F5')
                    .borderRadius(12)
                    .position({ x: '20%', y: 165 }) // 向下移动，避免遮挡文字
                    .margin({ top: 10})
                  }
                  .width('100%')
                  .height(180)
                  .margin({ top: 20 })

                  // 状态分析模块（集成到健康报告内）
                  Column() {
                    Text('状态分析')
                      .fontSize(14)
                      .fontWeight(FontWeight.Bold)
                      .fontColor('#FFE135') // 改为与湿度相同的黄色
                      .margin({ bottom: 12 })

                    // 兴奋度显示（南向数据联动）
                    Row() {
                      Text('😈')
                        .fontSize(16)
                        .margin({ right: 8 })
                      Text(`当前兴奋度: ${this.petMonitorData.excitement} (${this.getExcitementLevelText()})`)
                        .fontSize(14)
                        .fontColor('#000000') // 改为黑色
                        .fontWeight(FontWeight.Medium)
                    }
                    .width('100%')
                    .margin({ bottom: 10 })
                  }
                  .width('100%')
                  .margin({ top: 60 })
                } else {
                  Text('暂无健康报告数据')
                    .fontSize(14)
                    .fontColor('#666666')
                    .margin({ top: 20 })
                }
              }
              .width('100%')
              .padding(20)
              .backgroundColor('#FFFFFF') // 改为白色背景
              .borderRadius(20)
              .shadow({
                radius: 4,
                color: 'rgba(0, 0, 0, 0.1)',
                offsetY: 4
              })
            }
            .width('48%')
          }
          .width('100%')
          .justifyContent(FlexAlign.SpaceBetween)
          .alignItems(VerticalAlign.Top) // 恢复顶部对齐
          .margin({ top: 10, bottom: 15 })


          // 第二行：今日建议（AI建议）
          Column() {
            // 标题栏
            Row() {
              Text('今日建议')
                .fontSize(16)
                .fontWeight(FontWeight.Bold)
                .fontColor('#FFFFFF')
                .backgroundColor('#FFD700')
                .padding({
                  left: 12,
                  right: 12,
                  top: 4,
                  bottom: 4
                })
                .borderRadius(4)
              Text('[宠物健康小知识]')
                .fontSize(14)
                .fontColor('#666666')
                .margin({ left: 8 })
              Blank()
            }
            .width('100%')
            .margin({ bottom: 20 })

            // 科普内容
            Stack() {
              // 左侧文本
              Column() {
                ForEach(this.aiRecommendations, (recommendation: AIRecommendation) => {
                  Text('· ' + recommendation.content)
                    .fontSize(14)
                    .fontColor('#333333')
                    .fontFamily('sans-serif')
                    .lineHeight(20)
                    .margin({ bottom: 12 })
                })
              }
              .width('80%')
              .margin({ left: 10, right: 10 })

              // 右侧图片
              Image($r('app.media.dog_cute'))
                .width(80)
                .height(80)
                .objectFit(ImageFit.Contain)
                .position({ x: '85%', y: '60%' })
            }
            .width('100%')
            .height(120)

            // 底部鱼图标
            Row() {
              // 左侧三个鱼图标
              Row() {
                Image($r('app.media.fish'))
                  .width(20)
                  .height(20)
                  .margin({ right: 12 })
                Image($r('app.media.fish'))
                  .width(20)
                  .height(20)
                  .margin({ right: 12 })
                Image($r('app.media.fish'))
                  .width(20)
                  .height(20)
              }

              Blank()
            }
            .width('100%')
            .margin({ top: 20 })
          }
          .width('100%')
          .padding(20)
          .backgroundColor('#FFFFFF')
          .borderRadius(16)
          .borderWidth(1)
          .borderColor('#000000')
          .shadow({
            radius: 4,
            color: 'rgba(0, 0, 0, 0.1)',
            offsetY: 4
          })
        }
        .width('95%')
        .margin({ top: 10, bottom: 30 })
      }
      .width('100%')
      .layoutWeight(1)
      .edgeEffect(EdgeEffect.Spring)
      .scrollable(ScrollDirection.Vertical)
      .scrollBar(BarState.Off)
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#F0F8F0')
  }
}