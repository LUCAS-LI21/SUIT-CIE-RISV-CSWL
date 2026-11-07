// common/utils/PermissionManager.ets
import { abilityAccessCtrl, Permissions } from '@kit.AbilityKit';
import { common } from '@kit.AbilityKit';
import { BusinessError } from '@kit.BasicServicesKit';

export class PermissionManager {
  // 检查权限
  static checkPermission(permission: Permissions): boolean {
    const atManager = abilityAccessCtrl.createAtManager();
    // 注意：此方法需要context，应传入有效的UIAbilityContext
    // 这里仅作示意，实际调用时需提供context
    // return atManager.checkAccessTokenSync(context.applicationInfo.accessTokenId, permission) === 0;
    return false; // 暂时代码
  }

  // 申请权限
  static async requestPermission(context: common.UIAbilityContext, permission: Array<Permissions>): Promise<boolean> {
    const atManager = abilityAccessCtrl.createAtManager();
    try {
      const data = await atManager.requestPermissionsFromUser(context, permission);
      return data.authResults[0] === 0; // 0代表授权成功[reference:8]
    } catch (err) {
      console.error('申请权限失败:', JSON.stringify(err));
      return false;
    }
  }
}