import { AbilityConstant, ConfigurationConstant, UIAbility, Want } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { window } from '@kit.ArkUI';
import { PreferencesUtil } from '../common/utils/PreferencesUtil';
import { RemoteConfig } from '../common/config/RemoteConfig';

const DOMAIN = 0x0000;

export default class EntryAbility extends UIAbility {
  onCreate(want: Want, launchParam: AbilityConstant.LaunchParam): void {
    try {
      this.context.getApplicationContext().setColorMode(ConfigurationConstant.ColorMode.COLOR_MODE_NOT_SET);
    } catch (err) {
      hilog.error(DOMAIN, 'testTag', 'Failed to set colorMode. Cause: %{public}s', JSON.stringify(err));
    }
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onCreate');
    // 初始化本地存储工具
    PreferencesUtil.init(this.context);
  }

  onDestroy(): void {
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onDestroy');
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    // Main window is created, set main page for this ability
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onWindowStageCreate');
    // windowStage.getMainWindowSync().setFullScreen(true)
    // 首屏由部署形态决定。
    // 模拟器形态直接进主界面：完整流程是 引导页 → 登录（要后端发验证码）
    // → 选品种 → 填信息 → 生成 3D → 主界面，六步才能看到「对话」页，
    // 而登录后端还是原型，联调时根本走不通。
    // 真板形态（TARGET=board）仍从引导页开始，登录与建档流程一步不少。
    //
    // 为什么放在这里而不是靠 WelcomePage 里 router.replaceUrl：
    // 在 aboutToAppear / onPageShow 里替换路由，模拟器上会被丢掉且不报错，
    // 表现就是永远停在欢迎页。首屏直接加载目标页没有时序问题。
    const skip: boolean = RemoteConfig.demoSkipLogin();
    const entryPage: string = skip ? 'pages/Index' : 'pages/GuidePage';
    // 日志用单参数模板串，不用 %{public}s 多参数形式：
    // 实测多参数那行会被 hilog 吞掉，排查时看不到任何线索（这次就栽在这上面）。
    hilog.info(DOMAIN, 'testTag', `entryPage=${entryPage} target=${RemoteConfig.target()} skip=${skip}`);
    windowStage.loadContent(entryPage, (err) => {
      if (err.code) {
        hilog.error(DOMAIN, 'testTag', 'Failed to load the content. Cause: %{public}s', JSON.stringify(err));
        return;
      }
      hilog.info(DOMAIN, 'testTag', 'Succeeded in loading the content.');
    });


  }

  onWindowStageDestroy(): void {
    // Main window is destroyed, release UI related resources
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onWindowStageDestroy');
  }

  onForeground(): void {
    // Ability has brought to foreground
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onForeground');
  }

  onBackground(): void {
    // Ability has back to background
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onBackground');
  }
}