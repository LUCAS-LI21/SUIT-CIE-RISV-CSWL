#!/bin/sh
# 注入模式验证：拿一段 wav 循环喂给分析链路，替代麦克风。
#
# 用途：板子上没有真实宠物时，仍能验证「分类 → 状态 → 建议 → HTTP」整条链路
# 与真实采集完全一致，只有数据来源不同。端口用 11440，避免和线上的 11439 抢。
exec python3 /root/k1-ai/petvoice_service.py \
    --inject /root/k1-ai/meow.wav \
    --port 11440 \
    --device plug:dsnoop0 \
    > /tmp/inj.log 2>&1
