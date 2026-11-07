#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现前端 http 模块的调用序列：同一条长连接上 start → stop，且带请求体。

为什么不能用两条 curl
    curl 每次新建连接，body 残留不会串到下一个请求，测不出
    「HTTP/1.1 不读 body 导致下一个请求 501」这个 bug。
    前端 ArkTS http 会复用连接池，所以必须用 http.client 复用同一连接。

用法：python3 /root/asr/test_mic_keepalive.py [录音秒数]
"""
import http.client
import json
import sys
import time

DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0


def main():
    c = http.client.HTTPConnection("127.0.0.1", 11441, timeout=90)

    def post(path):
        # body 故意带 '{}'，与前端 http 的 extraData 一致
        c.request("POST", path, body=b"{}",
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, r.read().decode("utf-8", "replace")

    t0 = time.time()
    st, body = post("/v1/asr/start")
    print(f"start  -> HTTP {st}  {body}   ({int((time.time()-t0)*1000)} ms)")

    time.sleep(DUR)

    t1 = time.time()
    st, body = post("/v1/asr/stop")
    print(f"stop   -> HTTP {st}  {body}   ({int((time.time()-t1)*1000)} ms)")

    # 再连打一次，确认服务没被上一次搞脏
    st, body = post("/v1/asr/start")
    print(f"start2 -> HTTP {st}  {body}")
    st, body = post("/v1/asr/stop")
    print(f"stop2  -> HTTP {st}  {body}")

    c.close()

    ok = True
    try:
        d = json.loads(body)
        if d.get("ok") is not True:
            ok = False
    except Exception:
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
