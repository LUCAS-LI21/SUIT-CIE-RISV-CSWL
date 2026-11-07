#!/usr/bin/env bash
# 一键构建并部署 pet-wellness 到 SC3568HA（OpenHarmony 5.0.0.71 / API 12）
#
# 为什么需要这么多步：
#   1. 工程用 HarmonyOS SDK 编译（本机只有 6.0.1），产出的 HAP 在 module.json 里写的是
#      compileSdkType=HarmonyOS、minAPIVersion=50000012，OpenHarmony 的 bm 会以
#      9568282 / 9568297 拒装；
#   2. 所以要在签名的**之前**把包改成 OpenHarmony 语义（compileSdkType + API 整数版本），
#      签名必须放在改包之后，否则签名块失效；
#   3. 设备是 Release 系统，provision 类型必须是 release，debug profile 会报 9568286。
#
# 用法：bash scripts/build_deploy_3568.sh [--skip-build]
set -euo pipefail

ROOT="E:/CIE-RISC-V"
APP="$ROOT/pet-wellness"
SIGN="$ROOT/sign"
SDK_LIB="E:/OpenHarmonySDK/20/toolchains/lib"
JAVA="E:/HarmonyOS/DevEco Studio/jbr/bin/java.exe"
HDC="E:/HarmonyOS/DevEco Studio/sdk/default/openharmony/toolchains/hdc.exe"
TARGET="df011853434133324710076f03096b00"
BUNDLE="com.ljp.myapplication"

UNSIGNED="$APP/entry/build/default/outputs/default/entry-default-unsigned.hap"
PATCHED="$SIGN/entry-oh-patched-unsigned.hap"
SIGNED="$SIGN/entry-3568.hap"

step() { printf '\n=== %s ===\n' "$1"; }

if [ "${1:-}" != "--skip-build" ]; then
  step "1/4 编译 HAP"
  cd "$APP"
  DEVECO_SDK_HOME="E:/HarmonyOS/DevEco Studio/sdk" \
    "E:/HarmonyOS/DevEco Studio/tools/hvigor/bin/hvigorw.bat" \
    --mode module -p product=default assembleHap --no-daemon 2>&1 \
    | grep -E "BUILD|ERROR|error:" | head -10
  [ -f "$UNSIGNED" ] || { echo "构建产物不存在: $UNSIGNED"; exit 1; }
else
  echo "跳过编译，使用已有产物"
fi

step "2/4 改标为 OpenHarmony（compileSdkType / API 版本）"
cd "$SIGN"
python patch_sdktype.py "$UNSIGNED" "$PATCHED" | tail -3

step "3/4 签名（release profile）"
"$JAVA" -jar "$SDK_LIB/hap-sign-tool.jar" sign-app \
  -keyAlias "openharmony application release" \
  -signAlg "SHA256withECDSA" -mode "localSign" \
  -appCertFile "AppCertChain2.pem" \
  -profileFile "profile.p7b" \
  -inFile "$PATCHED" -outFile "$SIGNED" \
  -keystoreFile "$SDK_LIB/OpenHarmony.p12" \
  -keyPwd "123456" -keystorePwd "123456" 2>&1 | grep -E "success|ERROR" | tail -2

step "4/4 安装并启动"
export MSYS_NO_PATHCONV=1
# hdc 会把当前工作目录拼在传入路径前面，所以必须 cd 到 hap 所在目录并只传文件名
cd "$SIGN"
"$HDC" -t "$TARGET" install -r "entry-3568.hap" 2>&1 | tail -2
"$HDC" -t "$TARGET" shell "aa force-stop $BUNDLE" >/dev/null 2>&1 || true
"$HDC" -t "$TARGET" shell "aa start -a EntryAbility -b $BUNDLE" 2>&1 | tail -2

printf '\n完成。查看日志：\n  %s -t %s shell "hilog -x | grep ljp | tail -20"\n' "$HDC" "$TARGET"
