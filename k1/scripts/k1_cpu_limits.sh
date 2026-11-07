#!/bin/sh
# K1 切到「板载 CSI + 本地推理」模式的一键部署。
#
# 解决两个真问题（都是实测出来的，不是猜的）：
#   1. PC 推流多花 175 ms/帧  -> 改用板载 CSI，这 175 ms 归零
#   2. 语音服务空闲时吃 134% CPU，把推理从 89 ms 拖到 362 ms（4 倍）
#      -> 给语音设 CPUQuota，把核让给视觉
#
# 用法（在 K1 上执行）：
#   sh k1_cpu_limits.sh
#
# 前置：
#   /etc/systemd/system/k1-vision-csi.service  已放好
#   /root/k1-ai/vision_service.py             已存在
#   /usr/share/camera_json/csi3_camera_auto.json 已存在
#
# 回滚：
#   systemctl disable --now k1-vision-csi
#   systemctl enable --now k1-camingest
#   rm -f /etc/systemd/system/petvoice.service.d/cpu.conf
#   systemctl daemon-reload && systemctl restart petvoice

set -e

echo "=== 1/5 校时（K1 RTC 每次重启必漂，见过 20 分钟） ==="
# 漂了会让行为窗口的时间戳错乱、日志对不上，先校正再干别的
if [ -x /usr/bin/ntpdate ]; then
    ntpdate -u pool.ntp.org 2>/dev/null || true
fi
hwclock -w 2>/dev/null || true
date

echo "=== 2/5 停用 PC 推流版（它占着 11436） ==="
systemctl disable --now k1-camingest 2>/dev/null || true
# 顺手清掉手动 setsid 起的实例：不受 systemd 管理，会和正式服务抢 11436 和摄像头。
# 用 [v] 这个写法是为了让 pkill 不匹配到自己 —— 命令行里若出现完整的
# "vision_service.py"，pkill -f 会把这个 shell 也一起杀掉，脚本当场静默退出（踩过三次）。
pkill -f "[v]ision_service.py" 2>/dev/null || true
sleep 2

echo "=== 3/5 给语音链路设 CPU 上限 ==="
# 预算：8 核 = 800%
#   视觉  k1-vision-csi  不限（要 1.5 核左右：推理单线程 + 前后处理）
#   ASR   k1-asr         150%
#   petvoice(含 VAD)     100%   —— 它空闲就吃 134%，限到 100 不影响识别
mkdir -p /etc/systemd/system/petvoice.service.d
cat > /etc/systemd/system/petvoice.service.d/cpu.conf <<'EOF'
[Service]
# 空闲时实测 77.8% + 子进程 sherpa-onnx-vad 56.9% = 134%，纯空转。
# 限到 100% 既够用，又给视觉留出整核。
CPUQuota=100%
EOF

mkdir -p /etc/systemd/system/k1-asr.service.d
cat > /etc/systemd/system/k1-asr.service.d/cpu.conf <<'EOF'
[Service]
# ASR 是真正的算力用户（SenseVoice int8），但不需要无上限
CPUQuota=150%
EOF

echo "=== 4/5 应用配置 ==="
systemctl daemon-reload
systemctl restart petvoice k1-asr
systemctl enable --now k1-vision-csi

echo "=== 5/5 等待起服务并验收 ==="
sleep 8
systemctl is-active k1-vision-csi petvoice k1-asr
echo "--- 端口 ---"
ss -tlnp 2>/dev/null | grep 11436 || echo "11436 未监听（服务没起来）"
echo "--- 推理状态 ---"
curl -s -m 3 http://127.0.0.1:11436/v1/vision/latest | head -c 300
echo
echo "--- 负载 ---"
uptime
echo
echo "判据：health 里 fps 应该接近 6；如果还是 2，检查 --fps 参数是否被覆盖。"
echo "如果启动即崩、报 tcm buffer alloc failed，先 reboot 清 AI 核片上内存。"
