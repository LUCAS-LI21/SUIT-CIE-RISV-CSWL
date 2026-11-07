// common/utils/YoloDisplayMapper.ets
// YOLO 检测结果 → 首页展示文本 + 心情词
// 不依赖 PetBehavior/BehaviorPlaybackEngine，独立可用

/**
 * YOLO type → 中文显示。
 *
 * 上一版这里无条件 `return '小狗'`（注释写「统一显示小狗」），
 * 于是**识别到猫也显示成小狗**。检测层本来就输出 cat/dog 两类的置信度，
 * 前端把它抹掉，等于把一条真实信息变成了假信息；答辩现场拿猫去测就会穿帮。
 */
export function yoloTypeToLabel(type: string): string {
  if (type === 'cat') {
    return '小猫';
  }
  if (type === 'dog') {
    return '小狗';
  }
  // 未知类型不加戏：显示空串，由上层决定「未检测到宠物」
  return '';
}

/** YOLO behavior → 中文动作 */
export function yoloBehaviorToLabel(behavior: string): string {
  switch (behavior) {
    case 'sleeping': return '睡觉';
    case 'running': return '奔跑';
    case 'walking': return '行走';
    case 'playing': return '玩耍';
    case 'jumping': return '蹦跳';
    case 'coquettish': return '撒娇';
    case 'eating': return '进食';
    case 'sitting': return '坐下';
    case 'lying': return '卧下';
    case 'standing': return '站立';
    case 'long_still': return '久卧';
    // === 玩球/玩飞盘（yolo_server 位置关系检测） ===
    case 'playing_ball': return '玩球';
    case 'playing_frisbee': return '接飞盘';
    default: return '识别中';
  }
}

/** 积极心情词池（多个，刷新时随机切换） */
const POSITIVE_MOODS: string[] = ['愉悦', '开心', '舒适', '活力满满', '心情棒', '神采奕奕', '惬意'];

/** YOLO behavior → 心情词（多个正面心情中随机） */
export function yoloBehaviorToMood(behavior: string): string {
  // 高速/活跃 → 活力满满
  if (behavior === 'running' || behavior === 'playing' || behavior === 'jumping') {
    return '活力满满';
  }
  // 静止/睡觉 → 舒适 / 惬意
  if (behavior === 'sleeping' || behavior === 'long_still' || behavior === 'lying' || behavior === 'sitting') {
    return Math.random() < 0.5 ? '舒适' : '惬意';
  }
  // 撒娇/进食 → 开心 / 愉悦
  if (behavior === 'coquettish' || behavior === 'eating') {
    return Math.random() < 0.5 ? '开心' : '愉悦';
  }
  // 其他 → 随机正面
  const idx: number = Math.floor(Math.random() * POSITIVE_MOODS.length);
  return POSITIVE_MOODS[idx];
}

/**
 * YOLO behavior → 3D 视频动作名
 * 静态/无明确动作类返回 null（不触发 one-shot，让 random idle 自己玩）
 */
export function mapYoloBehaviorToAction(behavior: string): string | null {
  switch (behavior) {
    // 用户指定 3 个映射：
    // - 小狗奔跑 → 跑视频
    case 'running': return 'xiaopao';
    // 跳：现有素材里没有跳跃片源（12 对 cat_/dog_ + lie1/play/sleep/stand），
    // 按「不新增素材」的决定复用 xiaopao 作腾空动作。
    // 代价：跳与跑共用同一条片子，只能靠「重新触发一次」体现事件发生
    // （HomeTab 的去重必须按行为而非动作名，否则跑完立刻跳会被吞掉）。
    case 'jumping': return 'xiaopao';
    // - 卧 → lie1（注意：rawfile 里文件名是 lie1.mp4，无 petType 前缀）
    case 'lying': return 'lie1';
    // - 坐 → 歪头（waitou；不是摇尾巴 yaoweiba，之前注释写错了）
    case 'sitting': return 'waitou';
    // - 站 → stand（rawfile 里有 3d/stand.mp4 无前缀）
    case 'standing': return 'stand';
    // - 睡 → sleep（rawfile 里有 3d/sleep.mp4 无前缀）
    case 'sleeping': return 'sleep';
    // 其他动态行为
    case 'playing': return 'sajiao';
    case 'coquettish': return 'sajiao';
    case 'eating': return 'sajiao';
    // 玩球/接飞盘 → play（rawfile 里有 3d/play.mp4 无前缀）
    case 'playing_ball': return 'play';
    case 'playing_frisbee': return 'play';
    // 走路 → 转圈（动态）
    case 'walking': return 'zhuanquan';
    // 久卧 → lie1
    case 'long_still': return 'lie1';
    default:
      return null;
  }
}
