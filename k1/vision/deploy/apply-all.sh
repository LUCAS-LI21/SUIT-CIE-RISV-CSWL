#!/bin/bash
# 一条命令把 K1 的「断电重启自恢复」配置全部落地。
#
# 解决的正是比赛「异常处理」那一项：断电再上电、中途杀进程，都要能自己回来，
# 而不是每次都要人登上去重新打通链路。
#
# 做五件事（都是幂等的，可以反复跑）：
#   1. 核隔离：AI 核 0-3 给视觉+LLM，通用核 4-7 给 ASR/叫声/网关/知识库
#   2. 自愈：演示链路上的服务一律 Restart=always
#   3. 自检：k1-selfcheck.timer 开机 40 s 首跑，之后每 90 s 一次
#   4. 启用：所有单元 enable，开机自动拉起
#   5. 收尾：立即跑一次自检并打印结果
#
# 用法：sudo bash apply-all.sh
# 前提：脚本、unit 文件、py 源码已 putcat 到 /root/k1-ai 与 /etc/systemd/system

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1

echo "=== 1/5 核隔离 ==="
bash ./pin-cores.sh | tail -10

echo
echo "=== 2/5 自愈策略 ==="
for u in k1-gateway k1-asr-ws k1-mic-asr petvoice petkb k1-vision-csi; do
    mkdir -p "/etc/systemd/system/${u}.service.d"
    printf '[Service]\n# 由 apply-all.sh 生成：不管进程怎么退出都拉起来\nRestart=always\nRestartSec=4\n' \
        > "/etc/systemd/system/${u}.service.d/50-restart.conf"
    echo "  ${u} -> Restart=always"
done

echo
echo "=== 3/5 自检单元 ==="
systemctl daemon-reload
systemctl enable --now k1-selfcheck.timer >/dev/null 2>&1
systemctl is-enabled k1-selfcheck.timer

echo
echo "=== 4/5 开机自启 ==="
for u in ollama k1-vision-csi k1-gateway petkb petvoice k1-mic-asr k1-asr-ws; do
    systemctl enable "$u" >/dev/null 2>&1
    printf '  %-16s %s\n' "$u" "$(systemctl is-enabled "$u" 2>&1 | head -1)"
done

echo
echo "=== 5/5 立即自检 ==="
systemctl start k1-selfcheck.service
sleep 2
python3 /root/k1-ai/k1_selfcheck.py || echo "自检未通过，见 /run/k1-selfcheck.json"
