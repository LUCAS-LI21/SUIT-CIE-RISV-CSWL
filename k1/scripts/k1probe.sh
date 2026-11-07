#!/bin/sh
# 从 3568 向 K1 发一条 HTTP 请求并打印响应。
# 用法：k1probe.sh <port> <path> [bodyfile]
#   有 bodyfile → POST（自动算 Content-Length，含中文也准）
#   无 bodyfile → GET
# 背景：PC 与 K1 不同网段，hdc fport 只能到本机端口，busybox nc 的 -e 在
# 这个固件上不生效，所以最稳的验证路径是「在 3568 上出站直连 K1」。
PORT="$1"
PATH_="$2"
BODY="$3"

if [ -n "$BODY" ]; then
    L=$(wc -c < "$BODY" | tr -d ' ')
    { printf 'POST %s HTTP/1.0\r\nHost: k1\r\nContent-Type: application/json\r\nContent-Length: %s\r\nConnection: close\r\n\r\n' "$PATH_" "$L"
      cat "$BODY"
    } | busybox nc -w 120 192.168.88.10 "$PORT"
else
    { printf 'GET %s HTTP/1.0\r\nHost: k1\r\nConnection: close\r\n\r\n' "$PATH_"
    } | busybox nc -w 20 192.168.88.10 "$PORT"
fi
