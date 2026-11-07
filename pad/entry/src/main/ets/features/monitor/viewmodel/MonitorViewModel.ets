// MonitorViewModel.ets
// 宠物监护页面的视图模型

export interface EnvironmentData {
  temperature: number;
  humidity: number;
  lightIntensity: number;
  timestamp: string;
}

export interface PetHealthData {
  calm: number;
  excited: number;
  attention: number;
  discomfort: number;
  timestamp: string;
}

export interface AIRecommendation {
  type: 'home' | 'medical';
  content: string;
  priority: 'low' | 'medium' | 'high';
}

export interface EnvironmentStatus {
  status: 'normal' | 'warning' | 'danger';
  message: string;
}

export interface PetHealthStatus {
  status: 'normal' | 'warning' | 'danger';
  message: string;
}

export interface HardwareData {
  temp: number;
  hum: number;
  light: number;
  status: string;
}

export class MonitorViewModel {
  // 环境数据
  private environmentData: EnvironmentData[] = [];
  // 宠物健康数据
  private healthData: PetHealthData[] = [];
  // AI建议
  private aiRecommendations: AIRecommendation[] = [];
  // 硬件数据
  private hardwareData: HardwareData | null = null;

  // 从硬件获取数据
  async fetchHardwareData(): Promise<void> {
    try {
      // 模拟从硬件获取数据
      // 实际项目中这里应该调用硬件接口获取数据
      const mockData: HardwareData = {
        temp: 31.8,
        hum: 46,
        light: 128,
        status: "Calm"
      };
      
      this.hardwareData = mockData;
      this.updateEnvironmentData(mockData);
      this.updateHealthData(mockData);
      this.generateAIRecommendations();
    } catch (error) {
      console.error('获取硬件数据失败:', error);
    }
  }

  // 更新环境数据
  private updateEnvironmentData(hardwareData: HardwareData): void {
    const now = new Date();
    const timestamp = `${now.getHours()}:${now.getMinutes().toString().padStart(2, '0')}`;
    
    const newData: EnvironmentData = {
      temperature: hardwareData.temp,
      humidity: hardwareData.hum,
      lightIntensity: hardwareData.light,
      timestamp: timestamp
    };
    
    this.environmentData.push(newData);
    // 只保留最近7条数据
    if (this.environmentData.length > 7) {
      this.environmentData.shift();
    }
  }

  // 更新健康数据
  private updateHealthData(hardwareData: HardwareData): void {
    const now = new Date();
    const timestamp = `${now.getHours()}:${now.getMinutes().toString().padStart(2, '0')}`;
    
    let calm = 0, excited = 0, attention = 0, discomfort = 0;
    
    switch (hardwareData.status) {
      case "Calm":
        calm = 80;
        excited = 10;
        attention = 5;
        discomfort = 5;
        break;
      case "Excited":
        calm = 30;
        excited = 60;
        attention = 5;
        discomfort = 5;
        break;
      case "Attention":
        calm = 40;
        excited = 20;
        attention = 35;
        discomfort = 5;
        break;
      case "Discomfort":
        calm = 20;
        excited = 10;
        attention = 20;
        discomfort = 50;
        break;
      default:
        calm = 50;
        excited = 20;
        attention = 15;
        discomfort = 15;
    }
    
    const newData: PetHealthData = {
      calm: calm,
      excited: excited,
      attention: attention,
      discomfort: discomfort,
      timestamp: timestamp
    };
    
    this.healthData.push(newData);
    // 只保留最近7条数据
    if (this.healthData.length > 7) {
      this.healthData.shift();
    }
  }

  // 生成AI建议
  private generateAIRecommendations(): void {
    if (!this.hardwareData) return;
    
    const recommendations: AIRecommendation[] = [];
    
    // 根据温度生成建议
    if (this.hardwareData.temp > 30) {
      recommendations.push({
        type: 'home',
        content: '温度过高，建议开启空调降温，确保宠物有充足的饮用水',
        priority: 'high'
      });
    } else if (this.hardwareData.temp > 28) {
      recommendations.push({
        type: 'home',
        content: '温度偏高，建议适当通风，避免宠物长时间在阳光下暴晒',
        priority: 'medium'
      });
    }
    
    // 根据湿度生成建议
    if (this.hardwareData.hum > 70) {
      recommendations.push({
        type: 'home',
        content: '湿度过高，建议使用除湿设备，保持环境干燥',
        priority: 'medium'
      });
    } else if (this.hardwareData.hum < 30) {
      recommendations.push({
        type: 'home',
        content: '湿度过低，建议使用加湿器，增加空气湿度',
        priority: 'low'
      });
    }
    
    // 根据光照生成建议
    if (this.hardwareData.light > 100) {
      recommendations.push({
        type: 'home',
        content: '光照过强，建议拉上窗帘，避免强光直射宠物',
        priority: 'medium'
      });
    }
    
    // 根据宠物状态生成建议
    if (this.hardwareData.status === 'Discomfort') {
      recommendations.push({
        type: 'medical',
        content: '宠物状态异常，建议检查宠物健康状况，如有必要请及时就医',
        priority: 'high'
      });
    } else if (this.hardwareData.status === 'Attention') {
      recommendations.push({
        type: 'home',
        content: '宠物需要关注，建议多陪伴宠物，增加互动时间',
        priority: 'medium'
      });
    }
    
    // 如果没有特殊建议，添加一个通用建议
    if (recommendations.length === 0) {
      recommendations.push({
        type: 'home',
        content: '环境和宠物状态良好，继续保持当前的护理方式',
        priority: 'low'
      });
    }
    
    this.aiRecommendations = recommendations;
  }

  // 获取环境数据
  getEnvironmentData(): EnvironmentData[] {
    if (this.environmentData.length === 0) {
      // 初始化默认数据
      this.fetchHardwareData();
    }
    return this.environmentData;
  }

  // 获取健康数据
  getHealthData(): PetHealthData[] {
    if (this.healthData.length === 0) {
      // 初始化默认数据
      this.fetchHardwareData();
    }
    return this.healthData;
  }

  // 获取AI建议
  getAIRecommendations(): AIRecommendation[] {
    if (this.aiRecommendations.length === 0) {
      // 初始化默认数据
      this.fetchHardwareData();
    }
    return this.aiRecommendations;
  }

  // 检查环境是否异常
  checkEnvironmentStatus(): EnvironmentStatus {
    if (this.environmentData.length === 0) {
      return { status: 'normal', message: '环境正常' };
    }
    
    const latestData = this.environmentData[this.environmentData.length - 1];
    
    if (latestData.temperature > 30) {
      return { status: 'danger', message: '温度过高' };
    } else if (latestData.temperature > 28) {
      return { status: 'warning', message: '温度偏高' };
    } else if (latestData.humidity > 70) {
      return { status: 'danger', message: '湿度过高' };
    } else if (latestData.humidity > 60) {
      return { status: 'warning', message: '湿度偏高' };
    } else if (latestData.lightIntensity > 90) {
      return { status: 'danger', message: '光照过强' };
    } else if (latestData.lightIntensity > 80) {
      return { status: 'warning', message: '光照偏强' };
    }
    
    return { status: 'normal', message: '环境正常' };
  }

  // 检查宠物健康状态
  checkPetHealthStatus(): PetHealthStatus {
    if (this.healthData.length === 0) {
      return { status: 'normal', message: '宠物状态正常' };
    }
    
    const latestData = this.healthData[this.healthData.length - 1];
    
    if (latestData.discomfort > 30) {
      return { status: 'danger', message: '宠物可能不适' };
    } else if (latestData.discomfort > 15) {
      return { status: 'warning', message: '宠物情绪异常' };
    } else if (latestData.attention > 40) {
      return { status: 'warning', message: '宠物需要关注' };
    }
    
    return { status: 'normal', message: '宠物状态正常' };
  }
}