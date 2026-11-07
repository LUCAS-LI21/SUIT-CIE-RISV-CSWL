// tabs/ControlTab.ets
// 监控页：环境监测控制 + 喂食监控（替换原对话 Tab）

import { promptAction } from '@kit.ArkUI';
import { MqttMessage } from '@ohos/mqtt';
import MQTTUtil from '../common/utils/MQTTUtil';
import { preferences } from '@kit.ArkData';
import { common } from '@kit.AbilityKit';
import { AppConstants } from '../common/constants/AppConstants';
import { PetDataHub } from '../common/services/PetDataHub';

interface EnvSnapshot {
  temp: number;
  hum: number;
  light: number;
  nh3: number;
  nh3Available: boolean;
  status: string;
}

interface FeedRecord {
  id: string;      // 唯一标识，用作 ForEach 稳定 key
  timeText: string;
  source: string;
}

interface MqttInbound {
  temp?: number;
  hum?: number;
  light?: number;
  nh3?: number;
  ammonia?: number;  // 硬件端实际发送的 key
  status?: string;
}

interface MotorCmd {
  motor: string;
}

interface LedCmd {
  led: string;
}

interface CtrlTopicMsg {
  motor?: string;
}

interface Ctrl3Inbound {
  water?: string;
  feng?: string;
  sle_data?: string;
  device?: string;
  led?: string;
}

interface Ctrl3FengCmd {
  feng: string;
}

interface Ctrl3LedCmd {
  led: string;
}

@Component
export struct ControlTab {
  @State env: EnvSnapshot = {
    temp: 0,
    hum: 0,
    light: 0,
    nh3: 0,
    nh3Available: false,
    status: ''
  };
  @State mqttReady: boolean = false;
  @State autoControl: boolean = false;
  // 阈值设定（自动控制时生效）
  @State nh3Threshold: number = 100;   // 氨气阈值 0-100 ppm
  @State lightThreshold: number = 100;  // 光照阈值 0-100（百分比）
  @State tempThreshold: number = 40;    // 温度阈值 0-40 ℃
  @State motorOn: boolean = false;
  @State ledOn: boolean = false;
  @State fanOn: boolean = false;    // 除味风扇开关状态
  @State feedRecords: FeedRecord[] = [];
  @State waterRecords: FeedRecord[] = [];
  @State fanRecords: FeedRecord[] = [];
  @State lightRecords: FeedRecord[] = [];
  @State roofRecords: FeedRecord[] = [];

  // 历史记录手风琴展开状态
  @State feedHistExpanded: boolean = false;
  @State waterHistExpanded: boolean = false;
  @State fanHistExpanded: boolean = false;
  @State lightHistExpanded: boolean = false;
  @State roofHistExpanded: boolean = false;

  // 定时控制 - 喂食
  @State feedScheduled: boolean = false;
  @State feedTargetHour: number = new Date().getHours();
  @State feedTargetMinute: number = new Date().getMinutes();
  @State feedRemaining: string = '';

  // 定时控制 - 喂水
  @State waterScheduled: boolean = false;
  @State waterTargetHour: number = new Date().getHours();
  @State waterTargetMinute: number = new Date().getMinutes();
  @State waterRemaining: string = '';

  private mqttUtil: MQTTUtil | null = null;
  private lastSelfSentWaterTime: number = 0; // 过滤自身发出的 ctrl3 led:water 回显
  private lastSelfSentFeedTime: number = 0; // 过滤自身发出的 ctrl motor:feed 回显
  private timerInterval: number = -1;
  private saveThresholdTimer: number = -1;

  aboutToAppear(): void {
    this.loadFeedPrefs();
    this.loadThresholdPrefs();
    this.loadHistoryPrefs();
    this.startMQTT();
    this.timerInterval = setInterval(() => {
      this.checkTimers();
    }, 1000);
  }

  aboutToDisappear(): void {
    if (this.timerInterval !== -1) {
      clearInterval(this.timerInterval);
      this.timerInterval = -1;
    }
    if (this.saveThresholdTimer !== -1) {
      clearTimeout(this.saveThresholdTimer);
      this.saveThresholdTimer = -1;
    }
    // 断开 MQTT 连接（参照 PetMonitorPage 的做法）
    this.cleanupMQTTConnection();
  }

  private GetContext(): common.UIAbilityContext {
    return getContext(this) as common.UIAbilityContext;
  }

  private async loadFeedPrefs(): Promise<void> {
    try {
      const prefs = await preferences.getPreferences(this.GetContext(), AppConstants.PREF_NAME);
      this.autoControl = (await prefs.get('envAutoControl', false)) as boolean;
    } catch (e) {
      console.error('[ControlTab] loadFeedPrefs failed', JSON.stringify(e));
    }
  }

  private async saveFeedPrefs(): Promise<void> {
    try {
      const prefs = await preferences.getPreferences(this.GetContext(), AppConstants.PREF_NAME);
      await prefs.put('envAutoControl', this.autoControl);
      await prefs.flush();
    } catch (e) {
      console.error('[ControlTab] saveFeedPrefs failed', JSON.stringify(e));
    }
  }

  private async loadThresholdPrefs(): Promise<void> {
    try {
      const prefs = await preferences.getPreferences(this.GetContext(), AppConstants.PREF_NAME);
      this.nh3Threshold = (await prefs.get('nh3Threshold', 100)) as number;
      this.lightThreshold = (await prefs.get('lightThreshold', 100)) as number;
      this.tempThreshold = (await prefs.get('tempThreshold', 40)) as number;
    } catch (e) {
      console.error('[ControlTab] loadThresholdPrefs failed', JSON.stringify(e));
    }
  }

  private scheduleThresholdSave(): void {
    if (this.saveThresholdTimer !== -1) {
      clearTimeout(this.saveThresholdTimer);
    }
    this.saveThresholdTimer = setTimeout(() => {
      this.saveThresholdPrefs();
      this.saveThresholdTimer = -1;
    }, 500);
  }

  private async saveThresholdPrefs(): Promise<void> {
    try {
      const prefs = await preferences.getPreferences(this.GetContext(), AppConstants.PREF_NAME);
      await prefs.put('nh3Threshold', this.nh3Threshold);
      await prefs.put('lightThreshold', this.lightThreshold);
      await prefs.put('tempThreshold', this.tempThreshold);
      await prefs.flush();
    } catch (e) {
      console.error('[ControlTab] saveThresholdPrefs failed', JSON.stringify(e));
    }
  }

  // ===== 历史记录持久化 =====
  private async loadHistoryPrefs(): Promise<void> {
    try {
      const prefs = await preferences.getPreferences(this.GetContext(), AppConstants.PREF_NAME);
      const feedJson = await prefs.get('feedRecords', '[]') as string;
      const waterJson = await prefs.get('waterRecords', '[]') as string;
      const fanJson = await prefs.get('fanRecords', '[]') as string;
      const lightJson = await prefs.get('lightRecords', '[]') as string;
      const roofJson = await prefs.get('roofRecords', '[]') as string;
      this.feedRecords = JSON.parse(feedJson) as FeedRecord[];
      this.waterRecords = JSON.parse(waterJson) as FeedRecord[];
      this.fanRecords = JSON.parse(fanJson) as FeedRecord[];
      this.lightRecords = JSON.parse(lightJson) as FeedRecord[];
      this.roofRecords = JSON.parse(roofJson) as FeedRecord[];
    } catch (e) {
      console.error('[ControlTab] loadHistoryPrefs failed', JSON.stringify(e));
    }
  }

  private async saveHistoryPrefs(): Promise<void> {
    try {
      const prefs = await preferences.getPreferences(this.GetContext(), AppConstants.PREF_NAME);
      await prefs.put('feedRecords', JSON.stringify(this.feedRecords));
      await prefs.put('waterRecords', JSON.stringify(this.waterRecords));
      await prefs.put('fanRecords', JSON.stringify(this.fanRecords));
      await prefs.put('lightRecords', JSON.stringify(this.lightRecords));
      await prefs.put('roofRecords', JSON.stringify(this.roofRecords));
      await prefs.flush();
    } catch (e) {
      console.error('[ControlTab] saveHistoryPrefs failed', JSON.stringify(e));
    }
  }

  private async startMQTT(): Promise<void> {
    // 先断开旧连接，避免 clientId 冲突（两个页面同时连 pet 主题会互相踢）
    if (this.mqttUtil) {
      await this.mqttUtil.disconnect();
    }
    this.mqttUtil = new MQTTUtil();
    console.info('[ControlTab] MQTT start, calling connectPet...');
    this.mqttUtil.connectPet((err: Error, data: MqttMessage) => {
      if (err) {
        console.error('[ControlTab] MQTT callback error:', JSON.stringify(err));
        return;
      }
      try {
        // 根据 topic 分流
        const topic = data.topic ?? '';
        const payloadStr = String(data.payload);
        console.info('[ControlTab] MQTT msg received, topic:', topic, 'payload:', payloadStr);
        // 硬件端会把「没接传感器的氨气」发成 inf（Python float('inf') 的字符串形式），
        // 那不是合法 JSON——JSON.parse 整条抛错，环境数据会**全丢**（实测：
        // {"temp":28.7,...,"ammonia":inf} → parse failed → 看板一直停在旧值）。
        // 统一把 inf/-inf/Infinity/NaN 降级成 null，语义即「该传感器无读数」。
        const safePayload: string = ControlTab.sanitizeJson(payloadStr);

        if (topic === 'ctrl') {
          this.handleCtrlMessage(safePayload);
          return;
        }

        if (topic === 'ctrl3') {
          this.handleCtrl3Message(safePayload);
          return;
        }

        // 原有 pet 主题处理
        const parsed = JSON.parse(safePayload) as MqttInbound;
        if (!parsed || typeof parsed !== 'object') {
          return;
        }
        // 兼容硬件端两种 key：nh3 / ammonia
        const rawNh3: number | undefined = parsed.nh3 ?? parsed.ammonia;
        const hasNh3: boolean = rawNh3 !== undefined && rawNh3 !== null;
        this.env = {
          temp: parsed.temp ?? this.env.temp,
          hum: parsed.hum ?? this.env.hum,
          light: parsed.light ?? this.env.light,
          nh3: hasNh3 ? (rawNh3 as number) : this.env.nh3,
          nh3Available: hasNh3 ? true : this.env.nh3Available,
          status: parsed.status ?? this.env.status
        };
        this.mqttReady = true;
        // 把这一帧环境数据转交数据汇聚层，供「对话」页做现状分析。
        // 只做单向写入，不读回、不改控制逻辑——本页的阈值判定与指令下发
        // 完全不受影响。没有这一行，聊天页就永远拿不到小板的环境数据。
        PetDataHub.getInstance().updateEnv(
          this.env.temp, this.env.hum, this.env.light,
          this.env.nh3, this.env.nh3Available,
          this.env.status === undefined ? '' : this.env.status,
          true
        );
        console.info('[ControlTab] env updated:', JSON.stringify(this.env));
        if (this.autoControl) {
          this.applyAutoControl();
        }
      } catch (e) {
        console.error('[ControlTab] MQTT parse failed', JSON.stringify(e));
      }
    }, 'pet_app_ctrl');
  }

  /**
   * 硬件端的 JSON 里可能出现 inf / -inf / Infinity / NaN（Python 的
   * float('inf') 序列化结果），这些都不是合法 JSON token，会让
   * JSON.parse 整条抛错、把同一帧里的所有正常字段一起丢掉。
   * 这里只做词法降级：把这类 token 换成 null（语义=该传感器无读数），
   * 不碰其余任何字段。
   */
  private static sanitizeJson(text: string): string {
    if (text.length === 0) {
      return text;
    }
    let out: string = text.replace(/:-?inf(inity)?/gi, ':null');
    out = out.replace(/:-?nan/gi, ':null');
    return out;
  }

  private handleCtrlMessage(raw: string): void {
    try {
      const parsed = JSON.parse(raw) as CtrlTopicMsg;
      if (!parsed || typeof parsed !== 'object') {
        return;
      }
      const now = Date.now();
      // {"motor":"feed"} → 外部喂食记录（过滤掉自身发出的指令回显）
      if (parsed.motor === 'feed') {
        if (now - this.lastSelfSentFeedTime > 2000) {
          this.pushFeedRecord('外部');
          console.info('[ControlTab] ctrl 收到外部喂食记录');
        } else {
          console.info('[ControlTab] ctrl 忽略自身发出的喂食指令');
          this.lastSelfSentFeedTime = 0;
        }
      }
    } catch (e) {
      console.error('[ControlTab] ctrl parse failed', JSON.stringify(e));
    }
  }

  private handleCtrl3Message(raw: string): void {
    try {
      const parsed = JSON.parse(raw) as Ctrl3Inbound;
      if (!parsed || typeof parsed !== 'object') {
        return;
      }
      const now = Date.now();

      // {"sle_data":"key opening","device":"locker_sle_client"} → 外部喂食记录
      if (parsed.sle_data === 'key opening' && parsed.device === 'locker_sle_client') {
        this.pushFeedRecord('外部');
        console.info('[ControlTab] ctrl3 收到外部开锁喂食记录');
        return;
      }

      // {"led":"water"} → 外部喂水记录（过滤掉自身发出的指令回显）
      if (parsed.led === 'water') {
        if (now - this.lastSelfSentWaterTime > 2000) {
          this.pushWaterRecord('外部');
          console.info('[ControlTab] ctrl3 收到外部喂水记录');
        } else {
          console.info('[ControlTab] ctrl3 忽略自身发出的喂水指令');
          this.lastSelfSentWaterTime = 0;
        }
      }
    } catch (e) {
      console.error('[ControlTab] ctrl3 parse failed', JSON.stringify(e));
    }
  }

  // 清理 MQTT 连接
  private async cleanupMQTTConnection(): Promise<void> {
    if (this.mqttUtil) {
      await this.mqttUtil.disconnect();
      this.mqttUtil = null;
      console.info('[ControlTab] MQTT 连接已清理');
    }
  }

  private envSuitable(): boolean {
    const t = this.env.temp;
    const h = this.env.hum;
    const l = this.env.light;
    const nh3Ok = !this.env.nh3Available || this.env.nh3 <= this.nh3Threshold;
    const tempOk = t <= this.tempThreshold;
    const lightOk = l <= this.lightThreshold;
    return tempOk && h >= 40 && h <= 60 && lightOk && nh3Ok;
  }

  private suitabilityText(): string {
    if (!this.mqttReady) {
      return '等待环境数据';
    }
    return this.envSuitable() ? '环境适宜' : '环境不适宜';
  }

  private formatValue(v: number, digits: number, available: boolean = true): string {
    if (!this.mqttReady || !available) {
      return '--';
    }
    return v.toFixed(digits);
  }

  private async publishCtrl(payloadJson: string): Promise<boolean> {
    if (!this.mqttUtil) {
      promptAction.showToast({ message: 'MQTT 未连接' });
      return false;
    }
    try {
      return await this.mqttUtil.publish('ctrl', payloadJson, 1);
    } catch (e) {
      console.error('[ControlTab] publish failed', JSON.stringify(e));
      promptAction.showToast({ message: '指令发送失败' });
      return false;
    }
  }

  private async publishCtrl3(payloadJson: string): Promise<boolean> {
    if (!this.mqttUtil) {
      promptAction.showToast({ message: 'MQTT 未连接' });
      return false;
    }
    try {
      return await this.mqttUtil.publish('ctrl3', payloadJson, 1);
    } catch (e) {
      console.error('[ControlTab] publishCtrl3 failed', JSON.stringify(e));
      promptAction.showToast({ message: '指令发送失败' });
      return false;
    }
  }

  private async onAutoControlChange(on: boolean): Promise<void> {
    this.autoControl = on;
    await this.saveFeedPrefs();
  }

  // ========== 自动设备控制 ==========
  // 仅在自动控制开关开启时，根据传感器阈值自动下发指令
  private async applyAutoControl(): Promise<void> {
    if (!this.mqttReady || !this.autoControl) {
      return;
    }

    // 氨气超标 → 除味（风扇）
    const nh3Exceeded = this.env.nh3Available && this.env.nh3 > this.nh3Threshold;
    // 温度超标 → 除味 + 遮阳
    const tempExceeded = this.env.temp > this.tempThreshold;
    // 光照超标 → 亮灯
    const lightExceeded = this.env.light > this.lightThreshold;

    // 除味风扇：氨气或温度任一超标即开启
    const fanShouldOn = nh3Exceeded || tempExceeded;
    // 亮灯：光照超标即开启
    const ledShouldOn = lightExceeded;
    // 遮阳（电机）：温度超标即开启
    const motorShouldOn = tempExceeded;

    if (fanShouldOn !== this.fanOn) {
      const cmd: Ctrl3FengCmd = { feng: fanShouldOn ? 'on' : 'off' };
      const ok = await this.publishCtrl3(JSON.stringify(cmd));
      if (ok) {
        this.fanOn = fanShouldOn;
        this.pushFanRecord('自动');
        promptAction.showToast({ message: fanShouldOn ? '自动除味已开启' : '自动除味已关闭' });
      }
    }

    if (ledShouldOn !== this.ledOn) {
      const cmd: LedCmd = { led: ledShouldOn ? 'on' : 'off' };
      const ok = await this.publishCtrl(JSON.stringify(cmd));
      if (ok) {
        this.ledOn = ledShouldOn;
        this.pushLightRecord('自动');
        promptAction.showToast({ message: ledShouldOn ? '自动补光已开启' : '自动补光已关闭' });
      }
    }

    if (motorShouldOn !== this.motorOn) {
      const cmd: MotorCmd = { motor: motorShouldOn ? 'on' : 'off' };
      const ok = await this.publishCtrl(JSON.stringify(cmd));
      if (ok) {
        this.motorOn = motorShouldOn;
        this.pushRoofRecord('自动');
        promptAction.showToast({ message: motorShouldOn ? '自动遮阳已开启' : '自动遮阳已关闭' });
      }
    }
  }

  private formatFullTime(now: Date): string {
    const weekDays = ['日', '一', '二', '三', '四', '五', '六'];
    const year = now.getFullYear();
    const month = (now.getMonth() + 1).toString().padStart(2, '0');
    const day = now.getDate().toString().padStart(2, '0');
    const weekDay = weekDays[now.getDay()];
    const hour24 = now.getHours();
    const period = hour24 < 12 ? '上午' : '下午';
    const hour12 = hour24 % 12 === 0 ? 12 : hour24 % 12;
    const minute = now.getMinutes().toString().padStart(2, '0');
    return `${year}-${month}-${day} 星期${weekDay} ${period}${hour12}:${minute}`;
  }

  private pushFeedRecord(source: string): void {
    const now = new Date();
    const timeText = this.formatFullTime(now);
    const record: FeedRecord = { id: `feed_${now.getTime()}`, timeText: timeText, source: source };
    const next: FeedRecord[] = [record].concat(this.feedRecords);
    this.feedRecords = next.slice(0, 8);
    this.saveHistoryPrefs();
  }

  private pushWaterRecord(source: string): void {
    const now = new Date();
    const timeText = this.formatFullTime(now);
    const record: FeedRecord = { id: `water_${now.getTime()}`, timeText: timeText, source: source };
    const next: FeedRecord[] = [record].concat(this.waterRecords);
    this.waterRecords = next.slice(0, 8);
    this.saveHistoryPrefs();
  }

  private pushFanRecord(source: string): void {
    const now = new Date();
    const timeText = this.formatFullTime(now);
    const record: FeedRecord = { id: `fan_${now.getTime()}`, timeText: timeText, source: source };
    const next: FeedRecord[] = [record].concat(this.fanRecords);
    this.fanRecords = next.slice(0, 8);
    this.saveHistoryPrefs();
  }

  private pushLightRecord(source: string): void {
    const now = new Date();
    const timeText = this.formatFullTime(now);
    const record: FeedRecord = { id: `light_${now.getTime()}`, timeText: timeText, source: source };
    const next: FeedRecord[] = [record].concat(this.lightRecords);
    this.lightRecords = next.slice(0, 8);
    this.saveHistoryPrefs();
  }

  private pushRoofRecord(source: string): void {
    const now = new Date();
    const timeText = this.formatFullTime(now);
    const record: FeedRecord = { id: `roof_${now.getTime()}`, timeText: timeText, source: source };
    const next: FeedRecord[] = [record].concat(this.roofRecords);
    this.roofRecords = next.slice(0, 8);
    this.saveHistoryPrefs();
  }

  private async feedNow(source: string): Promise<void> {
    this.lastSelfSentFeedTime = Date.now();
    const cmd: MotorCmd = { motor: 'feed' };
    const ok = await this.publishCtrl(JSON.stringify(cmd));
    if (ok) {
      this.pushFeedRecord(source);
      promptAction.showToast({ message: '已下发喂食指令' });
    }
  }

  private async waterNow(source: string): Promise<void> {
    this.lastSelfSentWaterTime = Date.now();
    const cmd: Ctrl3LedCmd = { led: 'water' };
    const ok = await this.publishCtrl3(JSON.stringify(cmd));
    if (ok) {
      this.pushWaterRecord(source);
      promptAction.showToast({ message: '已下发喂水指令' });
    }
  }

  private async toggleFan(): Promise<void> {
    this.fanOn = !this.fanOn;
    const status: string = this.fanOn ? 'on' : 'off';
    const cmd: Ctrl3FengCmd = { feng: status };
    const ok = await this.publishCtrl3(JSON.stringify(cmd));
    if (ok) {
      this.pushFanRecord('手动');
      const msg = this.fanOn ? '除味已开启' : '除味已关闭';
      promptAction.showToast({ message: msg });
    }
  }

  private async toggleLight(): Promise<void> {
    const newState = !this.ledOn;
    const cmd: LedCmd = { led: newState ? 'on' : 'off' };
    const ok = await this.publishCtrl(JSON.stringify(cmd));
    if (ok) {
      this.ledOn = newState;
      this.pushLightRecord('手动');
      promptAction.showToast({ message: newState ? '已下发开灯指令' : '已下发关灯指令' });
    }
  }

  private async toggleRoof(): Promise<void> {
    const newState = !this.motorOn;
    const cmd: MotorCmd = { motor: newState ? 'on' : 'off' };
    const ok = await this.publishCtrl(JSON.stringify(cmd));
    if (ok) {
      this.motorOn = newState;
      this.pushRoofRecord('手动');
      promptAction.showToast({ message: newState ? '已下发遮阳指令' : '已下发收起指令' });
    }
  }

  // ========== 定时控制 ==========
  private padZero(n: number): string {
    return n.toString().padStart(2, '0');
  }

  private calcRemaining(targetHour: number, targetMinute: number): string {
    const now = new Date();
    const target = new Date();
    target.setHours(targetHour, targetMinute, 0, 0);
    if (target.getTime() <= now.getTime()) {
      target.setDate(target.getDate() + 1); // 已过当天，顺延到明天
    }
    const diff = target.getTime() - now.getTime();
    // 用 ceil 保证分钟显示不会过早跳变：定时2分钟→显示"02"维持整2分钟
    const totalMin = Math.ceil(diff / 60000);
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    return `剩余 ${this.padZero(h)}:${this.padZero(m)}`;
  }

  private checkTimers(): void {
    if (this.feedScheduled && this.autoControl) {
      const now = new Date();
      if (now.getHours() === this.feedTargetHour &&
          now.getMinutes() === this.feedTargetMinute) {
        this.feedScheduled = false;
        this.feedRemaining = '';
        this.feedNow('定时');
      } else {
        this.feedRemaining = this.calcRemaining(this.feedTargetHour, this.feedTargetMinute);
      }
    }
    if (this.waterScheduled && this.autoControl) {
      const now = new Date();
      if (now.getHours() === this.waterTargetHour &&
          now.getMinutes() === this.waterTargetMinute) {
        this.waterScheduled = false;
        this.waterRemaining = '';
        this.waterNow('定时');
      } else {
        this.waterRemaining = this.calcRemaining(this.waterTargetHour, this.waterTargetMinute);
      }
    }
  }

  private startFeedTimer(): void {
    this.feedScheduled = true;
    this.feedRemaining = this.calcRemaining(this.feedTargetHour, this.feedTargetMinute);
    promptAction.showToast({
      message: `喂食定时已启动 ${this.padZero(this.feedTargetHour)}:${this.padZero(this.feedTargetMinute)}`
    });
  }

  private cancelFeedTimer(): void {
    this.feedScheduled = false;
    this.feedRemaining = '';
    promptAction.showToast({ message: '已取消喂食定时' });
  }

  private startWaterTimer(): void {
    this.waterScheduled = true;
    this.waterRemaining = this.calcRemaining(this.waterTargetHour, this.waterTargetMinute);
    promptAction.showToast({
      message: `喂水定时已启动 ${this.padZero(this.waterTargetHour)}:${this.padZero(this.waterTargetMinute)}`
    });
  }

  private cancelWaterTimer(): void {
    this.waterScheduled = false;
    this.waterRemaining = '';
    promptAction.showToast({ message: '已取消喂水定时' });
  }

  private adjustTime(target: 'feed' | 'water', field: 'hour' | 'minute', delta: number): void {
    if (target === 'feed') {
      if (field === 'hour') {
        this.feedTargetHour = (this.feedTargetHour + delta + 24) % 24;
      } else {
        this.feedTargetMinute = (this.feedTargetMinute + delta + 60) % 60;
      }
    } else {
      if (field === 'hour') {
        this.waterTargetHour = (this.waterTargetHour + delta + 24) % 24;
      } else {
        this.waterTargetMinute = (this.waterTargetMinute + delta + 60) % 60;
      }
    }
  }

  // ===== 卡片一：环境检测 =====
  @Builder
  EnvCard() {
    Column({ space: 12 }) {
      Text('环境检测')
        .fontSize(20)
        .fontWeight(FontWeight.Bold)
        .fontColor('#2C3E50')
        .width('100%')

      Row() {
        Column() {
          Text('温度')
            .fontSize(14)
            .fontColor('#FF3333')
            .margin({ bottom: 4 })
          Text(this.mqttReady ? `${this.env.temp.toFixed(1)}℃` : '--℃')
            .fontSize(14)
            .fontWeight(FontWeight.Bold)
            .fontColor('#2C3E50')
        }
        .layoutWeight(1)
        .alignItems(HorizontalAlign.Center)

        Column() {
          Text('湿度')
            .fontSize(14)
            .fontColor('#4FC3F7')
            .margin({ bottom: 4 })
          Text(this.mqttReady ? `${this.env.hum.toFixed(1)}%` : '--%')
            .fontSize(14)
            .fontWeight(FontWeight.Bold)
            .fontColor('#2C3E50')
        }
        .layoutWeight(1)
        .alignItems(HorizontalAlign.Center)

        Column() {
          Text('光照')
            .fontSize(14)
            .fontColor('#4CAF50')
            .margin({ bottom: 4 })
          Text(this.mqttReady ? `${this.env.light.toFixed(0)}lx` : '--lx')
            .fontSize(14)
            .fontWeight(FontWeight.Bold)
            .fontColor('#2C3E50')
        }
        .layoutWeight(1)
        .alignItems(HorizontalAlign.Center)

        Column() {
          Text('氨气')
            .fontSize(14)
            .fontColor('#9C27B0')
            .margin({ bottom: 4 })
          Text(this.mqttReady && this.env.nh3Available ? `${this.env.nh3.toFixed(1)}ppm` : '--ppm')
            .fontSize(14)
            .fontWeight(FontWeight.Bold)
            .fontColor('#2C3E50')
        }
        .layoutWeight(1)
        .alignItems(HorizontalAlign.Center)
      }
      .width('100%')

      // Text(this.suitabilityText())
      //   .fontSize(14)
      //   .fontWeight(FontWeight.Medium)
      //   .fontColor(this.envSuitable() ? '#27AE60' : '#E74C3C')
      //   .width('100%')
    }
    .width('100%')
    .padding(16)
    .backgroundColor('#FFFFFF')
    .borderRadius(16)
  }

  // ===== 卡片二：自动控制 =====
  @Builder
  AutoControlCard() {
    Column({ space: 12 }) {
      Text('自动设备控制')
        .fontSize(20)
        .fontWeight(FontWeight.Bold)
        .fontColor('#2C3E50')
        .width('100%')

      Row() {
        Text('自动控制开关')
          .fontSize(17)
          .fontColor('#34495E')
        Blank()
        Toggle({ type: ToggleType.Switch, isOn: this.autoControl })
          .selectedColor('#6A82FB')
          .onChange((on: boolean) => {
            this.onAutoControlChange(on);
          })
      }
      .width('100%')
      .alignItems(VerticalAlign.Center)

      Column({ space: 10 }) {
        Text('阈值设定')
          .fontSize(13)
          .fontColor(this.autoControl ? '#7F8C8D' : '#BDC3C7')
          .width('100%')

        // 氨气阈值 0-100
        Row({ space: 8 }) {
          Text('氨气')
            .fontSize(13)
            .fontColor(this.autoControl ? '#34495E' : '#BDC3C7')
            .constraintSize({ minWidth: 36 })
          Slider({
            value: this.nh3Threshold,
            min: 0,
            max: 100,
            step: 1,
            style: SliderStyle.OutSet
          })
            .blockColor(this.autoControl ? '#6A82FB' : '#BDC3C7')
            .trackColor('#E8ECF1')
            .selectedColor(this.autoControl ? '#6A82FB' : '#BDC3C7')
            .trackThickness(4)
            .enabled(this.autoControl)
            .layoutWeight(1)
            .onChange((val: number) => {
              this.nh3Threshold = val;
              this.scheduleThresholdSave();
            })
          Text(`${this.nh3Threshold.toFixed(0)}ppm`)
            .fontSize(12)
            .fontWeight(FontWeight.Medium)
            .fontColor(this.autoControl ? '#6A82FB' : '#BDC3C7')
            .constraintSize({ minWidth: 48 })
            .textAlign(TextAlign.End)
        }
        .width('100%')
        .alignItems(VerticalAlign.Center)

        // 光照阈值 0-100
        Row({ space: 8 }) {
          Text('光照')
            .fontSize(13)
            .fontColor(this.autoControl ? '#34495E' : '#BDC3C7')
            .constraintSize({ minWidth: 36 })
          Slider({
            value: this.lightThreshold,
            min: 0,
            max: 100,
            step: 1,
            style: SliderStyle.OutSet
          })
            .blockColor(this.autoControl ? '#6A82FB' : '#BDC3C7')
            .trackColor('#E8ECF1')
            .selectedColor(this.autoControl ? '#6A82FB' : '#BDC3C7')
            .trackThickness(4)
            .enabled(this.autoControl)
            .layoutWeight(1)
            .onChange((val: number) => {
              this.lightThreshold = val;
              this.scheduleThresholdSave();
            })
          Text(`${this.lightThreshold.toFixed(0)}lx`)
            .fontSize(12)
            .fontWeight(FontWeight.Medium)
            .fontColor(this.autoControl ? '#6A82FB' : '#BDC3C7')
            .constraintSize({ minWidth: 48 })
            .textAlign(TextAlign.End)
        }
        .width('100%')
        .alignItems(VerticalAlign.Center)

        // 温度阈值 0-40
        Row({ space: 8 }) {
          Text('温度')
            .fontSize(13)
            .fontColor(this.autoControl ? '#34495E' : '#BDC3C7')
            .constraintSize({ minWidth: 36 })
          Slider({
            value: this.tempThreshold,
            min: 0,
            max: 40,
            step: 1,
            style: SliderStyle.OutSet
          })
            .blockColor(this.autoControl ? '#6A82FB' : '#BDC3C7')
            .trackColor('#E8ECF1')
            .selectedColor(this.autoControl ? '#6A82FB' : '#BDC3C7')
            .trackThickness(4)
            .enabled(this.autoControl)
            .layoutWeight(1)
            .onChange((val: number) => {
              this.tempThreshold = val;
              this.scheduleThresholdSave();
            })
          Text(`${this.tempThreshold.toFixed(0)}℃`)
            .fontSize(12)
            .fontWeight(FontWeight.Medium)
            .fontColor(this.autoControl ? '#6A82FB' : '#BDC3C7')
            .constraintSize({ minWidth: 48 })
            .textAlign(TextAlign.End)
        }
        .width('100%')
        .alignItems(VerticalAlign.Center)
      }
      .width('100%')
      .opacity(this.autoControl ? 1.0 : 0.5)

      // 定时控制
      Column({ space: 10 }) {
        Text('定时控制')
          .fontSize(13)
          .fontColor(this.autoControl ? '#7F8C8D' : '#BDC3C7')
          .width('100%')

        // ---- 喂食定时 ----
        Column({ space: 8 }) {
          Row() {
            Text('喂食')
              .fontSize(14).fontWeight(FontWeight.Medium)
              .fontColor(this.autoControl ? '#2C3E50' : '#BDC3C7')
            if (this.feedScheduled) {
              Text(this.feedRemaining)
                .fontSize(12).fontColor('#27AE60').fontWeight(FontWeight.Medium)
                .layoutWeight(1).textAlign(TextAlign.End)
            }
          }
          .width('100%').alignItems(VerticalAlign.Center)

          Row() {
            this.TimeBtn('−', () => { if (this.autoControl) { this.adjustTime('feed', 'hour', -1); } })
            Text(this.padZero(this.feedTargetHour))
              .fontSize(24).fontWeight(FontWeight.Bold)
              .fontColor(this.autoControl ? '#2C3E50' : '#BDC3C7')
              .constraintSize({ minWidth: 44 }).textAlign(TextAlign.Center)
            this.TimeBtn('+', () => { if (this.autoControl) { this.adjustTime('feed', 'hour', 1); } })

            Text(':').fontSize(22).fontWeight(FontWeight.Bold)
              .fontColor(this.autoControl ? '#2C3E50' : '#BDC3C7')
              .constraintSize({ minWidth: 20 }).textAlign(TextAlign.Center)

            this.TimeBtn('−', () => { if (this.autoControl) { this.adjustTime('feed', 'minute', -1); } })
            Text(this.padZero(this.feedTargetMinute))
              .fontSize(24).fontWeight(FontWeight.Bold)
              .fontColor(this.autoControl ? '#2C3E50' : '#BDC3C7')
              .constraintSize({ minWidth: 44 }).textAlign(TextAlign.Center)
            this.TimeBtn('+', () => { if (this.autoControl) { this.adjustTime('feed', 'minute', 1); } })
          }
          .width('100%').justifyContent(FlexAlign.Center).alignItems(VerticalAlign.Center)

          if (this.feedScheduled) {
            Button('取消定时').width('100%').height(34).fontSize(14)
              .backgroundColor('#E74C3C').borderRadius(8).enabled(this.autoControl)
              .onClick(() => { this.cancelFeedTimer(); })
          } else {
            Button('确认定时').width('100%').height(34).fontSize(14)
              .backgroundColor(this.autoControl ? '#6A82FB' : '#BDC3C7').borderRadius(8)
              .enabled(this.autoControl)
              .onClick(() => { this.startFeedTimer(); })
          }
        }
        .width('100%')

        Divider().height(1).color('#E8ECF1')

        // ---- 喂水定时 ----
        Column({ space: 8 }) {
          Row() {
            Text('喂水')
              .fontSize(14).fontWeight(FontWeight.Medium)
              .fontColor(this.autoControl ? '#2C3E50' : '#BDC3C7')
            if (this.waterScheduled) {
              Text(this.waterRemaining)
                .fontSize(12).fontColor('#27AE60').fontWeight(FontWeight.Medium)
                .layoutWeight(1).textAlign(TextAlign.End)
            }
          }
          .width('100%').alignItems(VerticalAlign.Center)

          Row() {
            this.TimeBtn('−', () => { if (this.autoControl) { this.adjustTime('water', 'hour', -1); } })
            Text(this.padZero(this.waterTargetHour))
              .fontSize(24).fontWeight(FontWeight.Bold)
              .fontColor(this.autoControl ? '#2C3E50' : '#BDC3C7')
              .constraintSize({ minWidth: 44 }).textAlign(TextAlign.Center)
            this.TimeBtn('+', () => { if (this.autoControl) { this.adjustTime('water', 'hour', 1); } })

            Text(':').fontSize(22).fontWeight(FontWeight.Bold)
              .fontColor(this.autoControl ? '#2C3E50' : '#BDC3C7')
              .constraintSize({ minWidth: 20 }).textAlign(TextAlign.Center)

            this.TimeBtn('−', () => { if (this.autoControl) { this.adjustTime('water', 'minute', -1); } })
            Text(this.padZero(this.waterTargetMinute))
              .fontSize(24).fontWeight(FontWeight.Bold)
              .fontColor(this.autoControl ? '#2C3E50' : '#BDC3C7')
              .constraintSize({ minWidth: 44 }).textAlign(TextAlign.Center)
            this.TimeBtn('+', () => { if (this.autoControl) { this.adjustTime('water', 'minute', 1); } })
          }
          .width('100%').justifyContent(FlexAlign.Center).alignItems(VerticalAlign.Center)

          if (this.waterScheduled) {
            Button('取消定时').width('100%').height(34).fontSize(14)
              .backgroundColor('#E74C3C').borderRadius(8).enabled(this.autoControl)
              .onClick(() => { this.cancelWaterTimer(); })
          } else {
            Button('确认定时').width('100%').height(34).fontSize(14)
              .backgroundColor(this.autoControl ? '#4FC3F7' : '#BDC3C7').borderRadius(8)
              .enabled(this.autoControl)
              .onClick(() => { this.startWaterTimer(); })
          }
        }
        .width('100%')
      }
      .width('100%')
      .opacity(this.autoControl ? 1.0 : 0.5)
    }
    .width('100%')
    .padding(16)
    .backgroundColor('#FFFFFF')
    .borderRadius(16)
  }

  @Builder
  TimeBtn(label: string, onClick: () => void) {
    Text(label)
      .fontSize(14)
      .fontWeight(FontWeight.Bold)
      .fontColor(this.autoControl ? '#6A82FB' : '#BDC3C7')
      .width(28)
      .height(28)
      .textAlign(TextAlign.Center)
      .backgroundColor(this.autoControl ? '#EDF0FF' : '#F5F5F5')
      .borderRadius(14)
      .onClick(onClick)
  }

  @Builder
  MetricCell(title: string, value: string) {
    Column({ space: 4 }) {
      Text(title)
        .fontSize(12)
        .fontColor('#7F8C8D')
      Text(value)
        .fontSize(14)
        .fontWeight(FontWeight.Bold)
        .fontColor('#2C3E50')
    }
    .layoutWeight(1)
    .alignItems(HorizontalAlign.Center)
  }

  // ===== 卡片三：设备控制 =====
  @Builder
  DeviceControlCard() {
    Column({ space: 12 }) {
      Text('手动设备控制')
        .fontSize(20)
        .fontWeight(FontWeight.Bold)
        .fontColor('#2C3E50')
        .width('100%')

     Row({space: 10}){
       // 喂食
       Button('立即喂食')
         .layoutWeight(1)
         .width('100%')
         .height(44)
         .fontSize(16)
         .fontWeight(FontWeight.Medium)
         .backgroundColor('#6A82FB')
         .borderRadius(10)
         .onClick(() => {
           this.feedNow('手动');
         })

       // 喂水
       Button('立即喂水')
         .layoutWeight(1)
         .width('100%')
         .height(44)
         .fontSize(16)
         .fontWeight(FontWeight.Medium)
         .backgroundColor('#4FC3F7')
         .borderRadius(10)
         .onClick(() => {
           this.waterNow('手动');
         })
     }
     .width('100%')

      Row({space: 10}){
        // 风扇
        Button(this.fanOn ? '除味：开' : '除味：关')
          .layoutWeight(1)
          .width('100%')
          .height(44)
          .fontSize(16)
          .fontWeight(FontWeight.Medium)
          .backgroundColor(this.fanOn ? '#26A69A' : '#4DB6AC')
          .borderRadius(10)
          .onClick(() => {
            this.toggleFan();
          })

        // 亮灯
        Button(this.ledOn ? '亮灯：开' : '亮灯：关')
          .layoutWeight(1)
          .width('100%')
          .height(44)
          .fontSize(16)
          .fontWeight(FontWeight.Medium)
          .backgroundColor('#FFB74D')
          .borderRadius(10)
          .onClick(() => {
            this.toggleLight();
          })
      }
      .width('100%')

      // 顶棚
      Button(this.motorOn ? '遮阳：开' +
        '' +
        '' : '遮阳：关')
        .width('100%')
        .height(44)
        .fontSize(16)
        .fontWeight(FontWeight.Medium)
        .backgroundColor('#9575CD')
        .borderRadius(10)
        .onClick(() => {
          this.toggleRoof();
        })
    }
    .width('100%')
    .padding(16)
    .backgroundColor('#FFFFFF')
    .borderRadius(16)
  }

  // ===== 卡片四：历史记录（手风琴） =====
  @Builder
  HistoryCard() {
    Column({ space: 12 }) {
      Text('历史控制记录')
        .fontSize(20)
        .fontWeight(FontWeight.Bold)
        .fontColor('#2C3E50')
        .width('100%')

      // ---- 喂食记录 ----
      Column() {
        Row() {
          Text('喂食记录')
            .fontSize(15).fontWeight(FontWeight.Medium).fontColor('#2C3E50')
          Blank()
          Text(this.feedHistExpanded ? '▼' : '▶')
            .fontSize(12).fontColor('#7F8C8D')
        }
        .width('100%').padding({ top: 10, bottom: 10 })
        .onClick(() => { this.feedHistExpanded = !this.feedHistExpanded; })

        if (this.feedHistExpanded) {
          if (this.feedRecords.length === 0) {
            Text('暂无记录').fontSize(12).fontColor('#BDC3C7')
              .width('100%').padding({ left: 4, bottom: 8 })
          } else {
            Column({ space: 2 }) {
              ForEach(this.feedRecords, (item: FeedRecord) => {
                Row() {
                  Text(item.timeText)
                    .fontSize(12).fontColor('#2C3E50').maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis }).layoutWeight(1)
                  Text(item.source)
                    .fontSize(11).fontWeight(FontWeight.Medium)
                    .fontColor(item.source === '定时' ? '#6A82FB' : '#27AE60')
                    .padding({ left: 6, right: 6, top: 2, bottom: 2 })
                    .backgroundColor(item.source === '定时' ? '#EDF0FF' : '#E8F8F5')
                    .borderRadius(6)
                }
                .width('100%').padding({ top: 2, bottom: 2 })
              }, (item: FeedRecord) => item.id)
            }
            .width('100%').padding({ left: 4, bottom: 8 })
          }
        }
      }

      Divider().height(1).color('#F0F0F0')

      // ---- 喂水记录 ----
      Column() {
        Row() {
          Text('喂水记录')
            .fontSize(15).fontWeight(FontWeight.Medium).fontColor('#2C3E50')
          Blank()
          Text(this.waterHistExpanded ? '▼' : '▶')
            .fontSize(12).fontColor('#7F8C8D')
        }
        .width('100%').padding({ top: 10, bottom: 10 })
        .onClick(() => { this.waterHistExpanded = !this.waterHistExpanded; })

        if (this.waterHistExpanded) {
          if (this.waterRecords.length === 0) {
            Text('暂无记录').fontSize(12).fontColor('#BDC3C7')
              .width('100%').padding({ left: 4, bottom: 8 })
          } else {
            Column({ space: 2 }) {
              ForEach(this.waterRecords, (item: FeedRecord) => {
                Row() {
                  Text(item.timeText)
                    .fontSize(12).fontColor('#2C3E50').maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis }).layoutWeight(1)
                  Text(item.source)
                    .fontSize(11).fontWeight(FontWeight.Medium)
                    .fontColor(item.source === '定时' ? '#4FC3F7' : '#27AE60')
                    .padding({ left: 6, right: 6, top: 2, bottom: 2 })
                    .backgroundColor(item.source === '定时' ? '#E0F7FA' : '#E8F8F5')
                    .borderRadius(6)
                }
                .width('100%').padding({ top: 2, bottom: 2 })
              }, (item: FeedRecord) => item.id)
            }
            .width('100%').padding({ left: 4, bottom: 8 })
          }
        }
      }

      Divider().height(1).color('#F0F0F0')

      // ---- 除味记录 ----
      Column() {
        Row() {
          Text('除味记录')
            .fontSize(15).fontWeight(FontWeight.Medium).fontColor('#2C3E50')
          Blank()
          Text(this.fanHistExpanded ? '▼' : '▶')
            .fontSize(12).fontColor('#7F8C8D')
        }
        .width('100%').padding({ top: 10, bottom: 10 })
        .onClick(() => { this.fanHistExpanded = !this.fanHistExpanded; })

        if (this.fanHistExpanded) {
          if (this.fanRecords.length === 0) {
            Text('暂无记录').fontSize(12).fontColor('#BDC3C7')
              .width('100%').padding({ left: 4, bottom: 8 })
          } else {
            Column({ space: 2 }) {
              ForEach(this.fanRecords, (item: FeedRecord) => {
                Row() {
                  Text(item.timeText)
                    .fontSize(12).fontColor('#2C3E50').maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis }).layoutWeight(1)
                  Text(item.source)
                    .fontSize(11).fontWeight(FontWeight.Medium)
                    .fontColor('#27AE60')
                    .padding({ left: 6, right: 6, top: 2, bottom: 2 })
                    .backgroundColor('#E8F8F5')
                    .borderRadius(6)
                }
                .width('100%').padding({ top: 2, bottom: 2 })
              }, (item: FeedRecord) => item.id)
            }
            .width('100%').padding({ left: 4, bottom: 8 })
          }
        }
      }

      Divider().height(1).color('#F0F0F0')

      // ---- 亮灯记录 ----
      Column() {
        Row() {
          Text('亮灯记录')
            .fontSize(15).fontWeight(FontWeight.Medium).fontColor('#2C3E50')
          Blank()
          Text(this.lightHistExpanded ? '▼' : '▶')
            .fontSize(12).fontColor('#7F8C8D')
        }
        .width('100%').padding({ top: 10, bottom: 10 })
        .onClick(() => { this.lightHistExpanded = !this.lightHistExpanded; })

        if (this.lightHistExpanded) {
          if (this.lightRecords.length === 0) {
            Text('暂无记录').fontSize(12).fontColor('#BDC3C7')
              .width('100%').padding({ left: 4, bottom: 8 })
          } else {
            Column({ space: 2 }) {
              ForEach(this.lightRecords, (item: FeedRecord) => {
                Row() {
                  Text(item.timeText)
                    .fontSize(12).fontColor('#2C3E50').maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis }).layoutWeight(1)
                  Text(item.source)
                    .fontSize(11).fontWeight(FontWeight.Medium)
                    .fontColor('#27AE60')
                    .padding({ left: 6, right: 6, top: 2, bottom: 2 })
                    .backgroundColor('#E8F8F5')
                    .borderRadius(6)
                }
                .width('100%').padding({ top: 2, bottom: 2 })
              }, (item: FeedRecord) => item.id)
            }
            .width('100%').padding({ left: 4, bottom: 8 })
          }
        }
      }

      Divider().height(1).color('#F0F0F0')

      // ---- 遮阳记录 ----
      Column() {
        Row() {
          Text('遮阳记录')
            .fontSize(15).fontWeight(FontWeight.Medium).fontColor('#2C3E50')
          Blank()
          Text(this.roofHistExpanded ? '▼' : '▶')
            .fontSize(12).fontColor('#7F8C8D')
        }
        .width('100%').padding({ top: 10, bottom: 10 })
        .onClick(() => { this.roofHistExpanded = !this.roofHistExpanded; })

        if (this.roofHistExpanded) {
          if (this.roofRecords.length === 0) {
            Text('暂无记录').fontSize(12).fontColor('#BDC3C7')
              .width('100%').padding({ left: 4, bottom: 8 })
          } else {
            Column({ space: 2 }) {
              ForEach(this.roofRecords, (item: FeedRecord) => {
                Row() {
                  Text(item.timeText)
                    .fontSize(12).fontColor('#2C3E50').maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis }).layoutWeight(1)
                  Text(item.source)
                    .fontSize(11).fontWeight(FontWeight.Medium)
                    .fontColor('#27AE60')
                    .padding({ left: 6, right: 6, top: 2, bottom: 2 })
                    .backgroundColor('#E8F8F5')
                    .borderRadius(6)
                }
                .width('100%').padding({ top: 2, bottom: 2 })
              }, (item: FeedRecord) => item.id)
            }
            .width('100%').padding({ left: 4, bottom: 8 })
          }
        }
      }
    }
    .width('100%')
    .padding(16)
    .backgroundColor('#FFFFFF')
    .borderRadius(16)
  }

  build() {
    Column() {
      Scroll() {
        Column({ space: 16 }) {
          this.EnvCard()
          this.AutoControlCard()
          this.DeviceControlCard()
          this.HistoryCard()
        }
        .width('100%')
        .padding({ left: 12, right: 12, top: 16, bottom: 24 })
      }
      .layoutWeight(1)
      .width('100%')
      .scrollBar(BarState.Off)
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#F8F9FA')
  }
}
