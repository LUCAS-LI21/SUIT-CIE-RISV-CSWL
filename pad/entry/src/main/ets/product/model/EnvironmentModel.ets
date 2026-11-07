export default class EnvironmentModel {
  // 0正常，1警告，2危险
  stat: number = 0;
  // 空气温度
  temp: number = 0;
  // 光照强度
  light: number = 0;
  // 空气湿度
  hum: number = 0;
  // 状态文本（Calm/Warning/Danger）
  status: string = "";
}