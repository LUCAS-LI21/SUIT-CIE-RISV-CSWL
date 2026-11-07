// AppConstants.ets
// 应用全局常量

export class AppConstants {
  // 存储Key
  static readonly PREF_NAME = 'PetAppConfig';
  static readonly KEY_IS_FIRST_LAUNCH = 'isFirstLaunch';
  static readonly KEY_PET_INFO = 'petInfo';
  static readonly KEY_USER_SETTINGS = 'userSettings';
  // DeepSeek 在线接口：**已停用**，仅作为遗留常量保留地址，不参与任何调用。
  //
  // 2026-11-07：这里原本硬编码着一把 sk- 开头的真实密钥，已删除。
  // 删除不破坏编译与运行——全仓库检索确认没有任何代码读取这两个常量：
  // 对话链路早已改为 K1StatusChatService → K1 网关 11438 → qwen3:0.6b，
  // 剩下的引用只是 DeepSeekService 里导出的三个类型（DeepSeekMessage / PetInfo），
  // 属于纯类型依赖，不涉及网络。
  //
  // 密钥一旦进过 git 历史就等于泄漏，删代码不算吊销，必须到平台作废。
  // 交付物（含打包发给队友的压缩包）里不得再出现任何 sk- 字面量。
  static readonly DEEPSEEK_API_URL = 'https://api.deepseek.com/chat/completions';
  static readonly DEEPSEEK_API_KEY = '';
  // 默认宠物设定
  static readonly DEFAULT_PET_NAME = '布布';
  static readonly DEFAULT_PET_TYPE = 'cat';
  // // 颜色常量
  // static readonly COLOR_PRIMARY = '#6A82FB';
  // static readonly COLOR_SECONDARY = '#FC5C7D';
  // static readonly COLOR_BG_LIGHT = '#F8F9FA';
  // static readonly COLOR_TEXT_PRIMARY = '#2C3E50';
  // static readonly COLOR_TEXT_SECONDARY = '#7F8C8D';

  // 宠物状态相关常量
  static readonly PET_HUNGER_DECREASE_RATE = 2; // 每小时减少的饥饿度
  static readonly PET_MOOD_DECREASE_RATE = 1; // 每小时减少的心情值
  static readonly PET_HUNGER_FEED_AMOUNT = 30; // 喂食增加的饥饿度
  static readonly PET_MOOD_PLAY_AMOUNT = 30; // 玩耍增加的心情值

  // 宠物状态阈值
  static readonly PET_HUNGER_WARNING = 30; // 饥饿警告阈值
  static readonly PET_MOOD_WARNING = 30; // 心情警告阈值

  // 3D模型配置
  // static readonly PET_MODEL_FILE = 'dog_001.glb';   // 模型文件名

  //MQTT连接配置
  //ip可能要改为本机ipv4地址
  //gzm
  // static readonly HOST_IP = '10.102.84.110'
  //云端mqtt：IP
  // static readonly HOST_IP = '10.94.138.110'
  static readonly HOST_IP = '119.29.133.108'
  static readonly MQTT_URL = `${this.HOST_IP}:1883`
  // broker 账号（用户给定）。原来是占位串 "userName1"/"password"，
  // 匿名也能连上并收到 SUBACK，所以一直没暴露问题；
  // 但凡 broker 一侧收紧 ACL，占位凭据就会静默连不上。
  static readonly MQTT_USER = 'pet'
  static readonly MQTT_PASSWORD = '123456'
  static readonly WEB_BASE_URL = `http://${this.HOST_IP}:8080/`
}