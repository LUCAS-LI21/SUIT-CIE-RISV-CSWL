#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""讯飞 IAT 鉴权探针

按 OpenHarmonySpeechService.generateAuthUrl() 的同一套算法生成 URL，
再对 wss://iat.xf-yun.com/v1 发起真实 WebSocket 握手，用来判断
「代码没问题但功能失效」到底是不是凭据/权限问题。

判读：
  101 Switching Protocols -> 凭据有效，链路能建
  401                      -> 凭据无效（过期/删除/欠费）
"""
import base64
import hashlib
import hmac
import os
import subprocess
import sys
from email.utils import formatdate

HOST = "iat.xf-yun.com"
URL = f"wss://{HOST}/v1"
REQ_LINE = "GET /v1 HTTP/1.1"

# 凭证一律从环境变量取，源码不留任何真实值。
#     export XF_APPID=...  XF_APIKEY=...  XF_APISECRET=...
CANDS = [
    ("环境变量", os.environ.get("XF_APPID", ""),
     os.environ.get("XF_APIKEY", ""), os.environ.get("XF_APISECRET", "")),
]
CANDS = [c for c in CANDS if c[1]]


def make_url(app_id, api_key, api_secret):
    date = formatdate(usegmt=True)                     # RFC1123, GMT
    origin = f"host: {HOST}\ndate: {date}\n{REQ_LINE}"
    sig = base64.b64encode(
        hmac.new(api_secret.encode(), origin.encode(), hashlib.sha256).digest()
    ).decode()
    auth_origin = (f'api_key="{api_key}", algorithm="hmac-sha256", '
                   f'headers="host date request-line", signature="{sig}"')
    auth = base64.b64encode(auth_origin.encode()).decode()
    return f"{URL}?authorization={auth}&date={date.replace(' ', '%20').replace(',', '%2C')}&host={HOST}"


def probe(url):
    # 本机 shell 里有 http_proxy/https_proxy（工具沙箱的代理），它不透传
    # WebSocket 升级，走代理会一律超时，测出来的"失败"是假的。
    # 因此这里显式 --noproxy 并清掉子进程的代理变量。
    env = {k: v for k, v in os.environ.items()
           if not k.lower().endswith("_proxy")}
    # curl 8.21 原生支持 wss://，会自动完成 Upgrade 握手。
    # 不能再手动加 Upgrade 头（会与协议处理器冲突，导致静默超时）。
    cmd = ["curl", "--noproxy", "*", "-sS", "-i", "-N", "-m", "10", url]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                       encoding="utf-8", errors="replace")
    head = (r.stdout or "") + (r.stderr or "")
    first = head.splitlines()[0] if head.splitlines() else "(无输出)"
    return first.strip(), head[:400]


if __name__ == "__main__":
    for label, app_id, api_key, api_secret in CANDS:
        print("=" * 70)
        print(f"{label}  appId={app_id}")
        try:
            line, detail = probe(make_url(app_id, api_key, api_secret))
            print(f"  握手: {line}")
            if "101" in line:
                print("  => 凭据有效，可以建立连接")
            elif "401" in line:
                print("  => 401：凭据被拒（AppID/APIKey/APISecret 不匹配或产品未开通）")
            elif "403" in line:
                print("  => 403：无权限（应用未开通「中英识别大模型」或已欠费）")
            else:
                for ln in detail.splitlines()[:6]:
                    if ln.strip():
                        print("   ", ln.strip()[:120])
        except Exception as e:  # noqa: BLE001
            print(f"  探测失败: {e}")
