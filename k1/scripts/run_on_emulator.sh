#!/usr/bin/env bash
# 模拟器就绪后，一键完成：端口转发 -> 安装 -> 启动
#
# 用法：
#   bash scripts/run_on_emulator.sh
#
# 前置：
#   1. DevEco Studio 里已创建并启动 API 12 的 Phone 模拟器
#   2. 工程已配置签名（File > Project Structure > Signing Configs）
#   3. K1 板子已用 Type-C 连上 PC，adb devices 能看到设备

set -u

HDC="/e/HarmonyOS/DevEco Studio/sdk/default/openharmony/toolchains/hdc.exe"
ADB="/e/AndroidStudio/android/sdk/platform-tools/adb.exe"
PROJ="/e/CIE-RISC-V/pet-wellness"
HAP_MAIN="$PROJ/entry/build/default/outputs/default/entry-default-signed.hap"
HAP_RAW="$PROJ/entry/build/default/outputs/default/entry-default-unsigned.hap"
BUNDLE="com.ljp.myapplication"
ABILITY="EntryAbility"

echo "══════════════════════════════════════════════"
echo " 1/5  检查模拟器"
echo "══════════════════════════════════════════════"
TARGETS=$("$HDC" list targets 2>&1)
echo "$TARGETS"
if echo "$TARGETS" | grep -q "\[Empty\]"; then
  echo ""
  echo "✗ 没有在线的鸿蒙设备/模拟器。"
  echo "  请在 DevEco Studio 中启动模拟器后重试："
  echo "  Tools > Device Manager > Local Emulator > 启动 API 12 的 Phone"
  exit 1
fi
TARGET=$(echo "$TARGETS" | head -1 | tr -d '\r')
echo "✓ 目标设备: $TARGET"

echo ""
echo "══════════════════════════════════════════════"
echo " 2/5  检查 K1 板子链路"
echo "══════════════════════════════════════════════"
if ! MSYS_NO_PATHCONV=1 "$ADB" devices 2>&1 | grep -q "device$"; then
  echo "✗ K1 未连接。请确认 Type-C 线已插好，adb devices 能看到设备。"
  exit 1
fi
MSYS_NO_PATHCONV=1 "$ADB" forward tcp:11434 tcp:11434 >/dev/null 2>&1
if curl -s --noproxy '*' -m 5 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "✓ K1 上的 ollama 可达（经 USB 隧道，未使用 WiFi）"
else
  echo "✗ K1 的 11434 不可达。检查 adb forward 与板端 ollama 服务。"
  exit 1
fi

echo ""
echo "══════════════════════════════════════════════"
echo " 3/5  建立模拟器 -> 宿主 -> K1 的端口转发"
echo "══════════════════════════════════════════════"
# 模拟器是独立虚拟机，它自己的 127.0.0.1 不等于宿主。
# 用 hdc rport 把模拟器的 11434 反向转到宿主，宿主再由 adb forward 送到 K1。
"$HDC" -t "$TARGET" rport tcp:11434 tcp:11434 2>&1 || true
echo "--- 当前转发任务 ---"
"$HDC" fport ls 2>&1 | head -5

echo ""
echo "══════════════════════════════════════════════"
echo " 4/5  安装 HAP"
echo "══════════════════════════════════════════════"
if [ -f "$HAP_MAIN" ]; then
  HAP="$HAP_MAIN"
  echo "使用已签名包: $HAP"
else
  echo "⚠ 未找到签名包，只有 unsigned："
  echo "  $HAP_RAW"
  echo "  模拟器通常拒绝安装未签名包。请先在 DevEco 里配置签名："
  echo "  File > Project Structure > Signing Configs > Automatically generate signature"
  echo ""
  read -r -p "仍要用 unsigned 尝试安装？[y/N] " ans
  if [ "${ans:-N}" != "y" ]; then
    echo "已取消。"
    exit 1
  fi
  HAP="$HAP_RAW"
fi
"$HDC" -t "$TARGET" install -r "$HAP" 2>&1 | tail -5

echo ""
echo "══════════════════════════════════════════════"
echo " 5/5  启动应用"
echo "══════════════════════════════════════════════"
"$HDC" -t "$TARGET" shell aa start -a "$ABILITY" -b "$BUNDLE" 2>&1 | tail -3

echo ""
echo "══════════════════════════════════════════════"
echo " 完成。查看日志（含 [AI] / [LocalLLM] 标记）："
echo "   \"$HDC\" -t $TARGET hilog | grep -E 'AI|LocalLLM'"
echo "══════════════════════════════════════════════"
