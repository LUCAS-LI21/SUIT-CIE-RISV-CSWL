#!/bin/sh
# install_petvoice.sh —— 在 K1 板子上把「宠物叫声识别」固化为开机自启服务。
#
# 用法（在板子上跑，仓库文件需已 scp 到 /root/k1-ai/）：
#   sh /root/k1-ai/install_petvoice.sh
#
# 这个脚本只做三件事，且全部幂等，重复执行安全：
#   1. 补 /etc/asound.conf 的 dsnoop0 设备（ASR 与叫声识别共用一个麦克风的前提）
#   2. 装 systemd 单元并 enable
#   3. 起服务后自查 /health，把关键字段打出来，不用再去翻日志
#
# 为什么不写「先 pkill 旧进程」：pkill -f petvoice_service.py 会匹配到
# 执行这条命令的 shell 自己（命令行里就含这串字符），实测把自己杀掉了。
# 这里改用 systemd 管理，停止交给 systemctl。

set -e

DEST=/root/k1-ai
UNIT=/etc/systemd/system/petvoice.service

# 脚本可能被放在 /root/k1-ai/ 下，也可能被放在 /root/k1-ai/deploy/ 下，
# 两种位置都找一下，免得因为路径不同就报「找不到文件」。
HERE=$(cd "$(dirname "$0")" && pwd)
find_file() {
    for p in "$HERE/$1" "$HERE/deploy/$1" "$DEST/$1" "$DEST/deploy/$1"; do
        if [ -f "$p" ]; then
            echo "$p"
            return 0
        fi
    done
    return 1
}

DSNOOP=$(find_file dsnoop.conf) || {
    echo "[失败] 找不到 dsnoop.conf（应与本脚本同目录，或在同级的 deploy/ 下）" >&2
    exit 1
}
UNITFILE=$(find_file petvoice.service) || {
    echo "[失败] 找不到 petvoice.service（应与本脚本同目录，或在同级的 deploy/ 下）" >&2
    exit 1
}

echo "== 1/3 检查 dsnoop0 =="
if [ ! -f /etc/asound.conf ]; then
    : > /etc/asound.conf
fi
if grep -q 'pcm.dsnoop0' /etc/asound.conf; then
    echo "   dsnoop0 已存在，跳过"
else
    cat "$DSNOOP" >> /etc/asound.conf
    echo "   已追加 dsnoop0 到 /etc/asound.conf（来源 $DSNOOP）"
fi
# 立刻验证设备能打开，而且**真的**是 16 kHz。
# 必须显式查采样率：板上麦克风是 USB 声卡，原生 44100 Hz，dsnoop 不做
# 重采样，`arecord -r 16000` 会被静默降级成 44100。服务若拿到 44100 的
# 数据当 16000 解析，所有频率整体偏 2.76 倍，谱重心/基频判据全部失效。
if arecord -D plug:dsnoop0 -f S16_LE -r 16000 -c 1 -d 1 /tmp/petvoice_probe.wav 2>/dev/null; then
    RATE=$(python3 -c "import wave;print(wave.open('/tmp/petvoice_probe.wav').getframerate())" 2>/dev/null)
    if [ "$RATE" = "16000" ]; then
        echo "   dsnoop0 打开正常（实测 $RATE Hz）"
    else
        echo "   [警告] 实测采样率 ${RATE:-未知} Hz ≠ 16000，频域特征会整体偏移" >&2
    fi
    rm -f /tmp/petvoice_probe.wav
else
    echo "   [警告] dsnoop0 打不开，服务起来后会退出。先用 arecord -l 确认声卡序号" >&2
fi

echo "== 2/3 安装 systemd 单元 =="
cp "$UNITFILE" "$UNIT"
systemctl daemon-reload
systemctl enable petvoice.service >/dev/null 2>&1
echo "   已 enable petvoice.service（来源 $UNITFILE）"

echo "== 3/3 启动并自查 =="
systemctl restart petvoice.service
# 采集线程要跑满一个分析窗（0.96 s）才有第一帧，留 6 秒余量
i=0
while [ $i -lt 6 ]; do
    sleep 1
    if curl -s -m 2 http://127.0.0.1:11439/health >/dev/null 2>&1; then
        break
    fi
    i=$((i + 1))
done

curl -s -m 3 http://127.0.0.1:11439/health || {
    echo "[失败] /health 无响应，最近日志：" >&2
    journalctl -u petvoice.service -n 30 --no-pager >&2
    exit 1
}
echo
curl -s -m 3 http://127.0.0.1:11439/v1/voice/latest || true
echo
echo "完成。3568 前端轮询 http://192.168.88.10:11439/v1/voice/latest"
