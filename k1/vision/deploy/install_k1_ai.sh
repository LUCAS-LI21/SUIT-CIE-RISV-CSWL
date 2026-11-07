#!/bin/sh
# install_k1_ai.sh —— 在 K1 板子上把四个 AI 服务一次性固化为开机自启。
#
# 用法（板端，仓库文件已 scp 到 /root/k1-ai/ 及其 deploy/ 子目录）：
#   sh /root/k1-ai/install_k1_ai.sh
#
# 覆盖服务：
#   11436 k1-vision    视觉感知（YOLOv8n int8 + SpaceMIT EP）
#   11437 k1-asr       语音识别（SenseVoice-Small int8 + silero-VAD，CPU）
#   11438 k1-gateway   LLM 网关（三通道仲裁 → ollama:11434）
#   11439 petvoice     宠物叫声识别（声学门控 + 7 类叫声 + 11 种状态）
#    —   k1-warmup     预热 + 保活（开机第 45 秒轮询拉模型进内存，之后每 25 分钟续命）
#
# 全部幂等，重复执行安全。为什么不用 pkill -f xxx_service.py：
# 那条命令的模式串就出现在执行它的 shell 命令行里，会把自己杀掉（实测踩过）。
# 本脚本改为「按监听端口找 pid 再 kill」，监听者不可能是脚本自身，安全。

set -e

DEST=/root/k1-ai
SYSD=/etc/systemd/system
HERE=$(cd "$(dirname "$0")" && pwd)

find_file() {
    for p in "$HERE/$1" "$HERE/deploy/$1" "$DEST/$1" "$DEST/deploy/$1"; do
        [ -f "$p" ] && { echo "$p"; return 0; }
    done
    return 1
}

# 杀掉占用某端口的旧进程（手动 nohup 起的实例），避免 systemd 起来时 bind 失败。
free_port() {
    pid=$(ss -ltnp 2>/dev/null | grep ":$1 " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
    if [ -n "$pid" ] && [ "$pid" != "$$" ]; then
        echo "   端口 $1 被 pid=$pid 占用，先停掉（$(tr -d '\0' < /proc/$pid/cmdline 2>/dev/null | cut -c1-80)）"
        kill "$pid" 2>/dev/null || true
        sleep 1
        kill -9 "$pid" 2>/dev/null || true
    fi
}

# 等端口返回 2xx/4xx（4xx 也算活着，说明 HTTP 栈已就绪）
wait_http() {
    i=0
    while [ $i -lt "$2" ]; do
        code=$(curl -s -m 2 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || echo 000)
        [ "$code" != "000" ] && return 0
        sleep 1
        i=$((i + 1))
    done
    return 1
}

echo "== 1/4 检查 ALSA dsnoop0（asr 与 petvoice 共用一个麦克风的前提）=="
DSNOOP=$(find_file dsnoop.conf) || { echo "[失败] 找不到 dsnoop.conf" >&2; exit 1; }
[ -f /etc/asound.conf ] || : > /etc/asound.conf
if grep -q 'pcm.dsnoop0' /etc/asound.conf; then
    echo "   dsnoop0 已存在，跳过"
else
    cat "$DSNOOP" >> /etc/asound.conf
    echo "   已追加 dsnoop0（来源 $DSNOOP）"
fi
# 必须显式查采样率：USB 声卡原生 44100，dsnoop 不重采样，
# `arecord -r 16000` 会被静默降级，频域特征整体偏 2.76 倍。
if arecord -D plug:dsnoop0 -f S16_LE -r 16000 -c 1 -d 1 /tmp/ai_probe.wav 2>/dev/null; then
    RATE=$(python3 -c "import wave;print(wave.open('/tmp/ai_probe.wav').getframerate())" 2>/dev/null)
    if [ "$RATE" = "16000" ]; then
        echo "   dsnoop0 打开正常（实测 $RATE Hz）"
    else
        echo "   [警告] 实测 ${RATE:-未知} Hz ≠ 16000，ASR 与叫声识别的频域判据都会失效" >&2
    fi
    rm -f /tmp/ai_probe.wav
else
    echo "   [警告] dsnoop0 打不开，11437/11439 起不来。先 arecord -l 确认声卡序号" >&2
fi

echo "== 2/5 安装 systemd 单元 =="
for u in k1-vision k1-asr k1-gateway petvoice k1-warmup; do
    src=$(find_file "$u.service") || { echo "[失败] 找不到 $u.service" >&2; exit 1; }
    cp "$src" "$SYSD/$u.service"
    echo "   安装 $u.service ← $src"
done
# 预热靠 timer 触发，不是靠 service 的 [Install]——oneshot 若挂在
# multi-user.target 上，会把执行时间算进开机时间，反而拖慢启动。
TSRC=$(find_file k1-warmup.timer) || { echo "[失败] 找不到 k1-warmup.timer" >&2; exit 1; }
cp "$TSRC" "$SYSD/k1-warmup.timer"
echo "   安装 k1-warmup.timer ← $TSRC"

# 预热脚本必须落在 k1-warmup.service 的 ExecStart 所写的绝对路径上，
# 否则单元每次执行都是 "No such file or directory"，而预热失败又不影响
# 其他服务，很容易到演示那天才发现模型一直是冷加载。
WSRC=$(find_file warmup.sh) || { echo "[失败] 找不到 warmup.sh" >&2; exit 1; }
if [ "$WSRC" != "$DEST/deploy/warmup.sh" ]; then
    mkdir -p "$DEST/deploy"
    cp "$WSRC" "$DEST/deploy/warmup.sh"
fi
chmod +x "$DEST/deploy/warmup.sh"
echo "   安装 warmup.sh ← $WSRC"
systemctl daemon-reload

echo "== 3/5 按依赖顺序启动 =="
# 顺序有讲究：
#   petvoice 先起（麦克风轻，且它只依赖声卡）
#   vision 次之（占 TCM，让它在 ollama 加载模型前就把 EP 绑好）
#   asr 最后起（CPU 4 线程 + 239 MB 模型冷加载 10~17 秒，最重）
#   gateway 放最后（要 vision 在才能做 pause 仲裁）
#   warmup 不在这里 start：它由 k1-warmup.timer 在开机 45 秒后触发，
#   之后每 25 分钟续一次保活；安装完成后由第 5 步立即预热一次，不用等开机。
for u in petvoice k1-vision k1-asr k1-gateway; do
    systemctl stop "$u.service" 2>/dev/null || true
done
free_port 11436
free_port 11437
free_port 11438
free_port 11439

for u in petvoice k1-vision k1-asr k1-gateway; do
    systemctl enable "$u.service" >/dev/null 2>&1
    systemctl start "$u.service"
    echo "   已 enable + start $u.service"
    # vision 要等 CSI pipeline 协商 + 首帧推理，asr 要等模型加载，都要给时间
    case "$u" in
        k1-vision) sleep 6 ;;
        k1-asr)    sleep 3 ;;
        *)         sleep 2 ;;
    esac
done
systemctl enable k1-warmup.timer >/dev/null 2>&1
# 重装后要显式 restart：daemon-reload 只让 systemd 重读单元文件，
# 已在运行的 timer 仍按旧的时间表在执行（实测改完 OnUnitActiveSec 后
# `systemctl status` 显示的仍是上一次的触发时间）。
systemctl restart k1-warmup.timer >/dev/null 2>&1
echo "   已 enable + restart k1-warmup.timer（开机 45 秒预热，此后每 25 分钟保活）"

echo "== 4/5 逐服务自查 =="
FAIL=0

check() {   # check <名称> <url> <等待秒数>
    if wait_http "$2" "$3"; then
        printf '   %-10s ✓ ' "$1"
        curl -s -m 3 "$2" | head -c 300
        echo
    else
        printf '   %-10s ✗ 无响应（%s）\n' "$1" "$2"
        FAIL=1
    fi
}

check "11436视觉" http://127.0.0.1:11436/health 20
check "11437语音" http://127.0.0.1:11437/health 60
check "11438网关" http://127.0.0.1:11438/gate/health 10
check "11439叫声" http://127.0.0.1:11439/health 20

echo
echo "--- 开机自启状态 ---"
for u in k1-vision k1-asr k1-gateway petvoice; do
    printf '   %-12s %-10s %s\n' "$u" "$(systemctl is-enabled $u.service 2>&1)" "$(systemctl is-active $u.service 2>&1)"
done
printf '   %-12s %-10s %s\n' "k1-warmup.timer" "$(systemctl is-enabled k1-warmup.timer 2>&1)" "$(systemctl is-active k1-warmup.timer 2>&1)"

if [ "$FAIL" = "1" ]; then
    echo
    echo "[有服务未就绪] 最近日志：" >&2
    for u in k1-vision k1-asr k1-gateway petvoice; do
        echo "--- $u ---" >&2
        journalctl -u "$u.service" -n 15 --no-pager >&2 2>&1 || true
    done
    exit 1
fi

echo "== 5/5 预热语言模型 =="
# ollama 是懒加载的：HTTP 端口在听 ≠ 模型可用。不预热的话，模型要等到
# 第一次真实请求才被读进内存（实测 6.82 秒，叠加前端超时会先断开），
# 表现为「开机后第一次点对话必然失败一次」。
# 这里走 11438 而不是 11434，顺便验证网关的 pause 仲裁链路。
curl -s -m 200 -o /dev/null -w '   预热完成：HTTP %{http_code}，耗时 %{time_total}s\n' \
    -H "Content-Type: application/json" \
    -d '{"model":"qwen3:0.6b","stream":false,"messages":[{"role":"user","content":"hi"}],"max_tokens":1}' \
    http://127.0.0.1:11438/v1/chat/completions \
    || echo "   [警告] 预热失败，模型可能未加载；开机后首次对话会明显变慢" >&2

echo
echo "全部就绪。3568 前端轮询："
echo "  视觉 http://192.168.88.10:11436/v1/vision/latest"
echo "  语音 http://192.168.88.10:11437/v1/asr/latest"
echo "  网关 http://192.168.88.10:11438/v1/chat/completions"
echo "  叫声 http://192.168.88.10:11439/v1/voice/latest"
