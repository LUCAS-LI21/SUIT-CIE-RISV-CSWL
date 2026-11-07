#!/bin/bash
# 把 K1 的 8 个核切成两个互不干扰的池子，并落到 systemd drop-in 里（重启永久生效）。
#
# 为什么必须切：实测 ollama 的 runner 被调度在 cpu0-3（AI 核）上，
# 和 YOLO 的 SpaceMIT EP 抢同一批核。后果是 0.6B 模型 decode 掉到 0.5 token/s，
# 同一条请求在 1.4 s ~ 70 s 之间乱跳。
#
#   cpu0-3 : X60 + ime（AI 核 + TCM）+ 满血 RVV。YOLO 和 LLM 都在这里。
#            两个原因，缺一不可：
#              1) SpaceMIT EP 的 TCM 只在 cluster0，PANNs 试过在 0-3 上与 YOLO
#                 共存 → tcm buffer alloc failed，进程直接 terminate；
#              2) ollama 的 llama.cpp 绑定到 4-7 会 **illegal instruction 崩溃**
#                 （实测 signal: illegal instruction (core dumped)），
#                 因为 cluster1 的向量指令集不是满血 RVV 1.0。
#            所以 LLM 与 CV 只能在 0-3 上分时复用，靠网关的 vision pause 让路，
#            而不是靠绑到不同核上——后者物理上跑不起来。
#   cpu4-7 : 通用核。叫声 / 网关 / 知识库在这里，一点都不碰 AI 核。
#
# 【2026-11-06 血坑，别再犯】k1-asr-ws（sherpa-onnx，:11440）曾经被放进 4-7，
# 结果**每一次识别请求都把进程打成 SIGILL**（status=4/ILL, core-dump）：
#     14:14:14  Decode:68 size: 1        ← 连接成功、收到音频、开始推理
#     14:14:15  Main process exited, code=dumped, status=4/ILL
# 从 13:46 打上 4-7 绑定，到 14:14 第一个请求，之后每次按语音键都崩。
# 前端只能看到 `ConnectionClosedError: no close frame received or sent`，
# 于是一直被当成「网络断了 / 麦克风坏了」排查，其实麦克风采得好好的
# （同一时刻 rms 370~515、peak 1268~4468、zcr 0.05~0.06，全是正常人声范围）。
# 解除绑定后同一份二进制立刻正常：官方 zh.wav → 「开饭时间早上九点至下午五点」。
# → 结论：sherpa-onnx 这个 spacemit 构建同样要满血 RVV，**只能待在 0-3**。
#    代价是识别那 2~3 秒会和视觉抢核（视觉 fps 短暂下降），
#    但识别是按需触发的，比「每次必崩」可接受得多。
#
# 判据：凡是 native 二进制（ollama/sherpa-onnx）都只能在 0-3；
#      纯 Python（mic-asr / gateway / petkb / petvoice）才放 4-7。
#
# 【2026-11-06 又一条实测】onnxruntime 的 **int8 量化卷积**内核同样只能在 0-3：
#     taskset -c 4-7 python3 -c "...InferenceSession(int8模型).run(...)"
#     → bash: 非法指令（核心已转储）；dmesg cause=2（illegal instruction）
#     同一份模型在 0-3 上正常，p50 492 ms。
#   连 session 创建都成功（931 ms），只在真正的 run() 时炸 —— 说明是 int8 推理
#   内核用了 cluster1 没有的指令（大概率是 RVV 的 INT8 点积 vdot/vqdot）。
#   → 所以 petvoice（常驻 4-7）**不能**在里面直接跑 int8 ONNX；
#     真要跑叫声模型，必须另起进程并绑 0-3。
# 判据更新：**int8 量化推理一律 0-3**，与是否 Python 无关。
#
# 用法：sudo bash pin-cores.sh [--check]
#   --check  只打印当前绑定，不改任何东西

set -u

AI_CORES="0-3"
CPU_CORES="4-7"

# 0-3 组：YOLO（SpaceMIT EP 要 TCM）+ ollama（满血 RVV）+ PANNs 侧车（int8 卷积）
UNITS_VISION="k1-vision-csi panns-sidecar"
# k1-asr-ws 是 native 二进制（sherpa-onnx），4-7 上必 SIGILL，见文件头说明。
UNITS_NATIVE="k1-asr-ws"
# 这几个是纯 Python，放 4-7 安全。
UNITS_CPU="k1-mic-asr k1-gateway petkb petvoice"

apply_dropin() {
    local unit="$1" cores="$2" extra="$3" dir
    dir="/etc/systemd/system/${unit}.service.d"
    mkdir -p "$dir"
    {
        echo "# 由 k1-vision/deploy/pin-cores.sh 生成，勿手改"
        echo "[Service]"
        echo "AllowedCPUs=${cores}"
        [ -n "$extra" ] && echo "$extra"
    } > "$dir/60-pinning.conf"
    echo "  ${unit} -> ${cores}"
}

check() {
    echo "=== 各单元 AllowedCPUs ==="
    for u in $UNITS_VISION ollama $UNITS_NATIVE $UNITS_CPU; do
        printf '  %-18s %s\n' "$u" \
            "$(systemctl show -p AllowedCPUs --value "$u" 2>/dev/null || echo '?')"
    done
    echo "=== 实际进程亲和性 ==="
    for pat in vision_service "ollama runner" sherpa-onnx petvoice_service "petkb"; do
        for p in $(pgrep -f "$pat" 2>/dev/null); do
            [ -d "/proc/$p" ] || continue
            printf '  %-32s pid=%-6s %s\n' "$pat" "$p" \
                "$(awk '/Cpus_allowed_list/{print $2}' /proc/$p/status 2>/dev/null)"
        done
    done
}

if [ "${1:-}" = "--check" ]; then
    check
    exit 0
fi

echo "写入 drop-in："
for u in $UNITS_VISION; do
    apply_dropin "$u" "$AI_CORES" ""
done
# LLM 必须留在 AI 核这一簇：换到 4-7 会 illegal instruction。
# 线程数=4 与核数一致：多给线程只会在 4 个核上来回切，反而更慢。
apply_dropin "ollama" "$AI_CORES" 'Environment="OLLAMA_NUM_THREADS=4"'
# sherpa-onnx 同理，4-7 上每个识别请求都 SIGILL（2026-11-06 实测）。
apply_dropin "$UNITS_NATIVE" "$AI_CORES" ""
for u in $UNITS_CPU; do
    apply_dropin "$u" "$CPU_CORES" ""
done

systemctl daemon-reload
echo "已 reload。重启服务使其生效："
echo "  systemctl restart k1-vision-csi ollama k1-gateway k1-asr-ws k1-mic-asr petkb petvoice"
echo
check
