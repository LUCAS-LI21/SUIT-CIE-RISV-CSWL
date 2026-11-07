// PetModel.ets
// 宠物数据模型

export interface PetInfo {
  id: string;           // 唯一标识
  name: string;         // 宠物名字
  type: string;         // 品种（cat/dog等）
  level: number;        // 等级
  exp: number;          // 当前经验
  expToNextLevel: number; // 升级所需经验
  hunger: number;       // 饥饿度 0-100
  mood: number;         // 心情值 0-100
  intimacy: number;     // 亲密度 0-100
  adoptDate: number;    // 领养时间戳
  accessories: string[]; // 已拥有的装扮ID列表
}

// 默认宠物数据（用于新建）
export function createDefaultPet(name: string = '布布', type: string = 'cat'): PetInfo {
  return {
    id: `${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
    name: name,
    type: type,
    level: 1,
    exp: 0,
    expToNextLevel: 100,
    hunger: 0,
    mood: 0,
    intimacy: 50,
    adoptDate: Date.now(),
    accessories: []
  };
}

/**
 * 更新宠物饥饿度（手动复制属性）
 */
export function updateHunger(pet: PetInfo, delta: number): PetInfo {
  let newHunger = pet.hunger + delta;
  newHunger = Math.max(0, Math.min(100, newHunger));

  const updatedPet: PetInfo = {
    id: pet.id,
    name: pet.name,
    type: pet.type,
    level: pet.level,
    exp: pet.exp,
    expToNextLevel: pet.expToNextLevel,
    hunger: newHunger,
    mood: pet.mood,
    intimacy: pet.intimacy,
    adoptDate: pet.adoptDate,
    accessories: pet.accessories
  };
  return updatedPet;
}

/**
 * 更新宠物心情值（手动复制属性）
 */
export function updateMood(pet: PetInfo, delta: number): PetInfo {
  let newMood = pet.mood + delta;
  newMood = Math.max(0, Math.min(100, newMood));

  const updatedPet: PetInfo = {
    id: pet.id,
    name: pet.name,
    type: pet.type,
    level: pet.level,
    exp: pet.exp,
    expToNextLevel: pet.expToNextLevel,
    hunger: pet.hunger,
    mood: newMood,
    intimacy: pet.intimacy,
    adoptDate: pet.adoptDate,
    accessories: pet.accessories
  };
  return updatedPet;
}

/**
 * 增加经验值（自动处理升级，手动复制属性）
 */
export function addExp(pet: PetInfo, exp: number): PetInfo {
  let newExp = pet.exp + exp;
  let newLevel = pet.level;
  let newExpToNext = pet.expToNextLevel;

  while (newExp >= newExpToNext) {
    newExp -= newExpToNext;
    newLevel++;
    newExpToNext = Math.floor(newExpToNext * 1.5);
  }

  const updatedPet: PetInfo = {
    id: pet.id,
    name: pet.name,
    type: pet.type,
    level: newLevel,
    exp: newExp,
    expToNextLevel: newExpToNext,
    hunger: pet.hunger,
    mood: pet.mood,
    intimacy: pet.intimacy,
    adoptDate: pet.adoptDate,
    accessories: pet.accessories
  };
  return updatedPet;
}

/**
 * 计算宠物当前状态摘要
 */
export function getPetStatusSummary(pet: PetInfo): string {
  if (pet.hunger < 30) return '😋 饿了';
  if (pet.mood < 30) return '😢 心情低落';
  if (pet.mood > 70 && pet.hunger > 70) return '😊 开心';
  return '😐 平静';
}