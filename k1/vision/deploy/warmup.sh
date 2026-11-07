#!/bin/sh
# 模型预热 / 保活脚本（由 k1-warmup.service 调用，也可手动执行）
#
# 为什么不把 JSON 直接写在 unit 的 ExecStart 里：
#   套 sh -c 时 systemd 先吃掉 \" ，sh 再把 JSON 的引号当字符串边界剥掉，
#   发给 ollama 的是非法 JSON，返回 400 —— 预热静默失效，日志只有一行 400。
#   写进脚本文件后，引号属于脚本自身语法，不经过 systemd 两层转义。
#
# 为什么是轮询而不是一次性 curl：
#   实测开机 70 秒那次 timer 在重负载下被推迟到 +140 秒才执行，
#   一次性的 curl 若撞上 ollama 尚未监听，就永久失败（前缀 - 让它不报错，
#   反而更难发现）。这里最多重试 60 次 × 3 秒 = 3 分钟，覆盖冷加载 8~10 秒
#   与低内存下的劣化，成功即退出。

MODEL="qwen3:0.6b"
GATEWAY="http://127.0.0.1:11438/v1/chat/completions"
MAX_TRIES=60
SLEEP_SEC=3

i=1
while [ "$i" -le "$MAX_TRIES" ]; do
    # 走 11438 而不是 11434：顺便验证网关的 pause 仲裁链路是通的。
    # max_tokens=1：只要模型被拉进内存即可，不需要真正生成内容。
    code=$(curl -s -m 120 -o /dev/null -w '%{http_code}' \
        -H 'Content-Type: application/json' \
        -d "{\"model\":\"$MODEL\",\"stream\":false,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":1}" \
        "$GATEWAY" 2>/dev/null)

    if [ "$code" = "200" ]; then
        # 二次确认模型确实驻留。注意 ollama ps 在加载完成后的头一两秒可能
        # 还没刷新，所以先等一下再查；即便查到 0 也不判失败——HTTP 200 已经
        # 证明请求被模型服务处理过，这里只是把实情如实打出来供排查，不用它
        # 做生死判断（早期版本拿它做判断，日志出现过 resident=0 但模型实际
        # 已驻留 722 MB 的矛盾记录）。
        sleep 2
        resident=$(ollama ps 2>/dev/null | grep -c "$MODEL")
        echo "warmup ok: HTTP 200 on try $i (ollama ps 匹配行数: $resident)"
        exit 0
    fi

    echo "warmup try $i/$MAX_TRIES: HTTP ${code:-000}，${SLEEP_SEC}s 后重试"
    i=$((i + 1))
    sleep "$SLEEP_SEC"
done

echo "warmup failed after $MAX_TRIES tries" >&2
exit 1
