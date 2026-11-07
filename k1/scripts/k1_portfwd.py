#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 PC 本地端口经「3568 中继 + SSH」映射到 K1 的真实服务端口。

为什么需要它
------------
PC 的「以太网」网卡虽然 Link Up / 1 Gbps，但 K1 的 dnsmasq 租约表里只有 3568
（`192.168.88.182 4e:ca:45:6f:9f:9a`），ARP 表里也只有 3568 —— 说明 PC 这块网卡
根本不在 K1 那条二层链路上，只能拿到 169.254 的 APIPA 地址。改静态地址需要管理员
权限（`New-NetIPAddress` → 拒绝访问），还会搅乱 WLAN 的默认路由。

所以走一条已经验证过的零权限链路：

    PC:11436 --hdc fport--> 3568:2222 --busybox nc--> K1:22 --SSH direct-tcpip--> K1:11436

三跳都在内网，单请求额外开销 < 5 ms。

两个必须写对的细节（都踩过）
------------------------------
1) `hdc fport` **必须带 -t**。本机同时连着模拟器(127.0.0.1:5555)与 3568，
   不带 -t 会报 `need connect-key? please confirm a device` 且不抛异常 ——
   表现是日志显示「已补建」、本地 2222 却没人监听，SSH 必然 ECONNREFUSED。
2) **不要重复建同一个 fport**。已存在时再执行 `fport tcp:2222 tcp:2222`，
   hdc 会失败（`TCP Port listen failed`）**并顺手把原来那条可用的转发顶掉**，
   结果正在跑的 SSH 直接被掐断（实测：隧道活了 11 分钟后整条掉线）。
   所以先 `fport ls` 查，存在就绝不重加。

自愈结构
--------
监听 socket 只创建一次、常驻不销毁；每次 accept 时去取**当前**的 SSH transport。
transport 死了就重连、换掉全局引用，监听端口始终保持打开。
这样前端轮询不会看到 ECONNREFUSED，只是短暂拿不到数据。

用法
----
    python scripts/k1_portfwd.py               # 常驻转发
    python scripts/k1_portfwd.py --test        # 只做一次端到端取数校验后退出
    python scripts/k1_portfwd.py --ports 11438 # 只转发指定端口
"""
import os as _os_sys
import sys as _sys_sys
_sys_sys.path.insert(0, _os_sys.path.dirname(_os_sys.path.abspath(__file__)))
import _nowin  # noqa: F401  —— 统一禁止子进程弹控制台

import argparse
import os
import select
import socket
import subprocess
import tempfile
import sys
import threading
import time

import paramiko

HOST = "127.0.0.1"
RELAY_PORT = 2222
K1_USER = "root"
K1_PWD = "bianbu"
DEVICE = "df011853434133324710076f03096b00"

# fport 的 rm/add 必须串行，否则并发重建会互相顶掉
_FPORT_LOCK = threading.Lock()

DEFAULT_PORTS = [11434, 11436, 11437, 11438, 11439, 11440]

_HDC_CANDIDATES = [
    r"E:\CIE-RISC-V\05-SC-3568HA开发套件（ISOH-SC-3568HA）-基础版\05 工具\OpenHarmony\HDC 工具\hdc.exe",
]

# 当前活着的 transport。None 表示隧道断了，此时连进来的请求直接关掉。
_LOCK = threading.Lock()
_TRANSPORT = None
# 日志节流：同类错误 20 秒内只打一次，否则一次断线能刷出十几万行。
_LAST_LOG = {}


def log_throttled(key, msg):
    now = time.time()
    if now - _LAST_LOG.get(key, 0) > 20:
        _LAST_LOG[key] = now
        print(msg, flush=True)


def _hdc():
    for p in _HDC_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def fport_present():
    """`hdc -t <设备> fport ls` 的输出里**没有设备号**，只有
    `'tcp:2222 tcp:2222'  [Forward]` 这种行。原来这里要求行内含 DEVICE，
    结果恒判「不存在」→ 每次重连都重加一次 fport → 把正在用的 SSH 掐断
    （症状：日志说「SSH 已连到 K1」，但本地端口一条请求都取不到数据）。
    """
    hdc = _hdc()
    if not hdc:
        return False
    # 关键：不能用 PIPE 捕获 hdc 输出。hdc.exe 会 fork 常驻 server 继承 stdout，
    # 管道永远等不到 EOF，subprocess 直接挂死（表现为本地端口能连、零字节回）。
    tmp = os.path.join(tempfile.gettempdir(), "fport_ls_fwd.txt")
    try:
        with open(tmp, "wb") as f:
            subprocess.run([hdc, "-t", DEVICE, "fport", "ls"],
                           stdout=f, stderr=subprocess.DEVNULL, timeout=20)
        with open(tmp, "rb") as f:
            out = f.read().decode("utf-8", "replace")
        for line in out.splitlines():
            if f"tcp:{RELAY_PORT}" in line and "Forward" in line:
                return True
        return False
    except Exception:
        return False


def ensure_fport():
    """只在缺失时补建。

    fport 会随那条 TCP 连接一起存活：SSH transport 不断，转发就一直在。
    所以只要存在就绝不重加——重加等于把活着的 SSH 掐断。
    """
    hdc = _hdc()
    if not hdc:
        print("[fwd] 找不到 hdc.exe，跳过 fport 补建", flush=True)
        return False
    if fport_present():
        return True
    with _FPORT_LOCK:
        if fport_present():
            return True
        try:
            # 必须 DEVNULL：hdc 会 fork 常驻 server 继承 stdout，管道等不到 EOF。
            subprocess.run([hdc, "-t", DEVICE, "fport",
                            f"tcp:{RELAY_PORT}", f"tcp:{RELAY_PORT}"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            ok = fport_present()
            print(f"[fwd] fport {RELAY_PORT} 补建{'成功' if ok else '失败'}", flush=True)
            return ok
        except Exception as e:
            print(f"[fwd] fport 补建异常: {e}", flush=True)
            return False


def connect_transport(retries=4):
    last = None
    for i in range(retries):
        ensure_fport()
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            c.connect(HOST, port=RELAY_PORT, username=K1_USER, password=K1_PWD,
                      timeout=15, banner_timeout=20, auth_timeout=20,
                      look_for_keys=False, allow_agent=False)
            print("[fwd] SSH 已连到 K1（经 3568 中继）", flush=True)
            return c.get_transport()
        except Exception as e:
            last = e
            try:
                c.close()
            except Exception:
                pass
            print(f"[fwd] SSH 第 {i + 1}/{retries} 次失败: {e}", flush=True)
            time.sleep(2)
    raise last


def splice(a, b):
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 1.0)
            if a in r:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
            if b in r:
                data = b.recv(65536)
                if not data:
                    break
                a.sendall(data)
    except Exception:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass


def handle(cli, addr, target_port):
    """拿**当前**transport 开一条通道，然后双向搬运。"""
    global _TRANSPORT
    with _LOCK:
        tr = _TRANSPORT
    if tr is None or not tr.is_active():
        print(f"[fwd] {target_port} 无可用 SSH（transport={'None' if tr is None else 'dead'}）", flush=True)
        cli.close()
        return
    try:
        ch = tr.open_channel("direct-tcpip", ("127.0.0.1", target_port), addr, timeout=10)
    except Exception as e:
        print(f"[fwd] {target_port} 打开通道失败: {e}", flush=True)
        cli.close()
        return
    splice(cli, ch)


def serve(listen_port):
    """监听 socket 常驻；断线重连期间也不关，避免前端拿到 ECONNREFUSED。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", listen_port))
    srv.listen(64)
    print(f"[fwd] 本地 {listen_port} → K1:{listen_port} 已就绪", flush=True)
    while True:
        try:
            cli, addr = srv.accept()
        except Exception as e:
            log_throttled(f"accept{listen_port}", f"[fwd] {listen_port} accept 异常: {e}")
            time.sleep(0.2)
            continue
        threading.Thread(target=handle, args=(cli, addr, listen_port), daemon=True).start()


def keeper(ports):
    """维护 SSH 连接。断了就重连并替换全局 transport，监听线程不受影响。"""
    global _TRANSPORT
    while True:
        try:
            tr = connect_transport()
            with _LOCK:
                _TRANSPORT = tr
            while tr.is_active():
                time.sleep(2)
            print("[fwd] SSH 断开，3 秒后重连", flush=True)
        except Exception as e:
            print(f"[fwd] 隧道异常: {e}，3 秒后重试", flush=True)
        with _LOCK:
            _TRANSPORT = None
        time.sleep(3)


def do_test(ports):
    ensure_fport()
    tr = connect_transport()
    try:
        paths = {11434: "/api/tags", 11436: "/health", 11437: "/health",
                 11438: "/health", 11439: "/health", 11440: "/health"}
        ok = 0
        for p in ports:
            try:
                ch = tr.open_channel("direct-tcpip", ("127.0.0.1", p), ("127.0.0.1", 0), timeout=10)
            except Exception as e:
                print(f"  [BAD] {p} 通道打不开: {e}", flush=True)
                continue
            ch.sendall(f"GET {paths.get(p, '/health')} HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n".encode())
            buf = b""
            deadline = time.time() + 6
            while time.time() < deadline:
                if ch.recv_ready():
                    d = ch.recv(65536)
                    if not d:
                        break
                    buf += d
                elif ch.exit_status_ready():
                    break
                else:
                    time.sleep(0.02)
            ch.close()
            text = buf.decode("utf-8", "replace")
            body = text.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in text else text
            head = text.split("\r\n", 1)[0]
            good = body.strip().startswith("{")
            ok += 1 if good else 0
            print(f"  [{'OK ' if good else 'BAD'}] {p} {head}  {body.strip()[:140]}", flush=True)
        print(f"[fwd] 通道校验 {ok}/{len(ports)} 通则", flush=True)
        return 0 if ok == len(ports) else 1
    finally:
        tr.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--ports", default="")
    args = ap.parse_args()

    ports = [int(x) for x in args.ports.split(",") if x.strip()] or DEFAULT_PORTS
    if args.test:
        return do_test(ports)

    for p in ports:
        threading.Thread(target=serve, args=(p,), daemon=True).start()
    keeper(ports)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
