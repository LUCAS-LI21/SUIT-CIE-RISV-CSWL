// PreferencesUtil.ets
// 本地首选项存储工具

import { preferences } from '@kit.ArkData';
import { AppConstants } from '../constants/AppConstants';

export class PreferencesUtil {
  private static context: Context;

  // 初始化，需在EntryAbility中调用一次
  static init(context: Context): void {
    PreferencesUtil.context = context;
  }

  // 获取Preferences实例
  private static async getPreferences(): Promise<preferences.Preferences> {
    return await preferences.getPreferences(PreferencesUtil.context, AppConstants.PREF_NAME);
  }

  // 保存数据
  static async put(key: string, value: preferences.ValueType): Promise<void> {
    try {
      const prefs = await PreferencesUtil.getPreferences();
      await prefs.put(key, value);
      await prefs.flush();
    } catch (error) {
      console.error(`PreferencesUtil put error: ${key}`, JSON.stringify(error));
    }
  }

  // 读取数据
  static async get<T extends preferences.ValueType>(key: string, defaultValue: T): Promise<T> {
    try {
      const prefs = await PreferencesUtil.getPreferences();
      const value = await prefs.get(key, defaultValue);
      return value as T;
    } catch (error) {
      console.error(`PreferencesUtil get error: ${key}`, JSON.stringify(error));
      return defaultValue;
    }
  }

  // 删除数据
  static async delete(key: string): Promise<void> {
    try {
      const prefs = await PreferencesUtil.getPreferences();
      await prefs.delete(key);
      await prefs.flush();
    } catch (error) {
      console.error(`PreferencesUtil delete error: ${key}`, JSON.stringify(error));
    }
  }

  // 清空所有数据
  static async clearAll(): Promise<void> {
    try {
      const prefs = await PreferencesUtil.getPreferences();
      await prefs.clear();
      await prefs.flush();
    } catch (error) {
      console.error('PreferencesUtil clearAll error', JSON.stringify(error));
    }
  }
}