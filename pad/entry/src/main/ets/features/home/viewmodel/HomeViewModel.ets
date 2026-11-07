// features/home/viewmodel/HomeViewModel.ets
import { PetInfo, createDefaultPet, updateHunger, updateMood } from '../../../product/model/PetModel';
import { AppConstants } from '../../../common/constants/AppConstants';
import { promptAction } from '@kit.ArkUI';

@Observed
export class HomeViewModel {
  petInfo: PetInfo;
  showLoading: boolean = false;
  lastInteractionTime: number = Date.now();
  // 语音识别相关状态（仅用于UI显示）
  isRecording: boolean = false;
  recognizingText: string = '';
  // 动作回调
  private onActionTriggered?: (action: string) => void;

  constructor() {
    this.petInfo = createDefaultPet('旺财', 'dog');
  }

  setPetInfo(info: PetInfo): void {
    this.petInfo = info;
  }

  setActionCallback(callback: (action: string) => void): void {
    this.onActionTriggered = callback;
  }

  // 检查是否可以进行互动（饥饿度>0）
  canInteract(): boolean {
    return this.petInfo.hunger > 0;
  }

  // 统一的互动检查，如果不能互动则提示并返回false
  checkInteractAndPrompt(): boolean {
    if (!this.canInteract()) {
      promptAction.showToast({ message: '您的爱宠饿肚子啦，请先喂食吧~' });
      return false;
    }
    return true;
  }

  // 处理语音指令
  handleVoiceCommand(text: string): string | null {
    // 饥饿度为0时禁止语音互动
    if (!this.checkInteractAndPrompt()) {
      return null;
    }
    const action = this.mapTextToAction(text);
    console.info('[HomeViewModel] 匹配结果 action:', action);
    if (action) {
      promptAction.showToast({ message: `宠物收到指令：${text}` });
      if (this.onActionTriggered) {
        this.onActionTriggered(action);
      }
      // 语音指令触发动作也增加心情
      this.increaseMood(20);
      return action;
    } else {
      promptAction.showToast({ message: `未识别的指令："${text}"` });
      return null;
    }
  }

  private mapTextToAction(text: string): string | null {
    const lowerText = text.toLowerCase();
    
    // 撒娇 - 支持：撒个娇、撒娇一下、想撒娇等
    if (this.containsAll(lowerText, ['撒', '娇'])) {
      return 'sajiao';
    }
    
    // 打滚 - 支持：打个滚、打滚一下、滚一滚等
    if (this.containsAll(lowerText, ['打', '滚']) || this.containsAll(lowerText, ['滚', '一', '滚'])) {
      return 'dagun';
    }
    
    // 转圈 - 支持：转个圈、转一圈、转圈圈等
    if (lowerText.includes('转') && lowerText.includes('圈')) {
      return 'zhuanquan';
    }
    
    // 摇尾巴 - 支持：摇摇尾巴、甩尾巴、尾巴摇一摇等
    if ((lowerText.includes('摇') || lowerText.includes('甩') || lowerText.includes('摆')) && lowerText.includes('尾巴')) {
      return 'yaoweiba';
    }
    
    // 奔跑 - 支持：跑起来、快跑、跑一跑、奔跑到等
    if (lowerText.includes('跑') || (lowerText.includes('奔') && lowerText.includes('跑'))) {
      return 'xiaopao';
    }
    
    // 歪头 - 支持：歪个头、歪一下头、把头歪一等
    if (lowerText.includes('歪') && lowerText.includes('头')) {
      return 'waitou';
    }
    
    // 伸懒腰 - 支持：伸个懒腰、伸伸懒腰、懒腰等
    if (lowerText.includes('伸') && lowerText.includes('懒腰')) {
      return 'shenlanyao';
    }
    if (lowerText.includes('懒腰')) {
      return 'shenlanyao';
    }
    
    // 甩水 - 支持：甩甩水、把水甩干、抖抖水等
    if ((lowerText.includes('甩') || lowerText.includes('抖')) && lowerText.includes('水')) {
      return 'shuaishui';
    }
    
    // 摸手 - 支持：摸摸手、握手、牵牵手等
    if ((lowerText.includes('摸') || lowerText.includes('握') || lowerText.includes('牵')) && lowerText.includes('手')) {
      return 'moshou';
    }
    
    // 摸头 - 支持：摸摸头、摸摸脑袋、拍拍头等
    if ((lowerText.includes('摸') || lowerText.includes('拍') || lowerText.includes('揉')) && 
        (lowerText.includes('头') || lowerText.includes('脑袋') || lowerText.includes('脑壳'))) {
      return 'motou';
    }
    
    return null;
  }
  
  /**
   * 检查文本是否包含所有关键词（按任意顺序）
   */
  private containsAll(text: string, keywords: string[]): boolean {
    return keywords.every(keyword => text.includes(keyword));
  }

  // 喂食
  feed(): void {
    const currentHunger = this.petInfo.hunger;
    const newHunger = Math.min(100, currentHunger + 20);
    this.petInfo = updateHunger(this.petInfo, newHunger - currentHunger);
    this.lastInteractionTime = Date.now();

    if (newHunger === 100) {
      promptAction.showToast({ message: '您的爱宠已吃饱' });
    } else {
      promptAction.showToast({ message: `喂食成功！饥饿度 +${newHunger - currentHunger}` });
    }
  }

  // 玩耍（互动按钮或热区触摸调用）
  play(): void {
    // 直接调用 increaseMood，Toast 会在内部统一处理
    this.increaseMood(20);
  }

  // 增加心情值（可指定数量，默认20）
  increaseMood(amount: number = 20): void {
    const currentMood = this.petInfo.mood;
    const newMood = Math.min(100, currentMood + amount);
    const actualIncrease = newMood - currentMood;

    if (actualIncrease > 0) {
      this.petInfo = updateMood(this.petInfo, actualIncrease);
      this.lastInteractionTime = Date.now();
    }

    if (newMood === 100 && currentMood < 100) {
      promptAction.showToast({ message: '您的爱宠心情美滋滋~' });
    } else if (actualIncrease > 0) {
      promptAction.showToast({ message: `心情值 +${actualIncrease}` });
    } else {
      promptAction.showToast({ message: '心情已经满啦！' });
    }

    // 互动消耗饥饿度10点
    const currentHunger = this.petInfo.hunger;
    const newHunger = Math.max(0, currentHunger - 10);
    const hungerDecrease = currentHunger - newHunger;
    if (hungerDecrease > 0) {
      this.petInfo = updateHunger(this.petInfo, -hungerDecrease);
      promptAction.showToast({ message: `互动消耗饥饿度 -${hungerDecrease}` });
    }
  }

  // 刷新状态（自然衰减）
  refresh(): void {
    const now = Date.now();
    //每小时心情减10：现实时间
    // const hoursPassed = (now - this.lastInteractionTime) / (1000 * 60 * 60);
    //
    // if (hoursPassed >= 1) {
    //   const hungerDecrease = Math.floor(hoursPassed * AppConstants.PET_HUNGER_DECREASE_RATE);
    //   const moodDecrease = Math.floor(hoursPassed * AppConstants.PET_MOOD_DECREASE_RATE);
    //   this.petInfo = updateHunger(this.petInfo, -hungerDecrease);
    //   this.petInfo = updateMood(this.petInfo, -moodDecrease);
    //   this.lastInteractionTime = now;
    // }

    //每30秒心情减10
    const secondsPassed = (now - this.lastInteractionTime) / 1000;
    const moodDecrease = Math.floor(secondsPassed / 30) * 10; // 每30秒减10点
    if (moodDecrease > 0) {
      this.petInfo = updateMood(this.petInfo, -moodDecrease);
      this.lastInteractionTime = now;

      // 随机提示消息 - 使用 try-catch 防止 UI 上下文错误
      try {
        const messages = [
          '主人快来陪我玩呀~',
          '有点无聊了，想和你一起玩耍',
          '心情不太好，来安慰安慰我吧',
          '好想你呀，快来摸摸我',
          '孤独感来袭，需要你的陪伴'
        ];
        const randomIndex = Math.floor(Math.random() * messages.length);
        promptAction.showToast({ message: messages[randomIndex] });
      } catch (e) {
        console.error('showToast failed', e);
      }
    }
  }

  getMoodIcon(): string {
    if (this.petInfo.mood >= 70) {
      return '😊';
    }
    if (this.petInfo.mood >= 40) {
      return '😐';
    }
    return '😢';
  }

  getHungerIcon(): string {
    if (this.petInfo.hunger >= 70) {
      return '🍖';
    }
    if (this.petInfo.hunger >= 40) {
      return '🍗';
    }
    return '🍽️';
  }
}