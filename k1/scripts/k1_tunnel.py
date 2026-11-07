# -*- coding: utf-8 -*-
"""极简隧道：本地端口 → hdc fport → 3568 中继 → SSH → K1 端口。

与 k1_portfwd.py 的区别：不做常驻 SSH 复用，**每条本地连接独立走一遍**
（建 fport → SSH 连接 → 开 direct-tcpip 通道 → 双向搬运 → 关闭）。
慢一点（每请求多 ~150ms），但不会因为「transport 挂了 / fport 被顶掉」
这类状态问题整条链路失联——实测这是这台机器上唯一稳定的形态。

用法：
    python scripts/k1_tunnel.py 11436 11438 11439 11437
"""
import select
import socket
import subprocess
import sys
import tempfile
import threading
import os

import paramiko

HDC = r"E:\CIE-RISC-V\05-SC-3568HA开发套件（ISOH-SC-3568HA）-基础版\05 工具\OpenHarmony\HDC 工具\hdc.exe"
DEVICE = "df011853434133324710076f03096b00"
RELAY_PORT = 2222
K1_USER = "root"
K1_PWD = "bianbu"

_fport_lock = threading.Lock()


def log(msg):
    print(msg, flush=True)


def fport_ok():
    """注意：hdc.exe 会 fork 一个常驻 server 继承 stdout，用 PIPE 捕获输出时
    管道永远等不到 EOF，subprocess 会**挂死**（实测整条隧道卡在这里，
    表现为本地端口能连上但一个字节都不回）。必须重定向到临时文件。"""
    tmp = os.path.join(tempfile.gettempdir(), "fport_ls.txt")
    try:
        with open(tmp, "wb") as f:
            subprocess.run([HDC, "-t", DEVICE, "fport", "ls"],
                           stdout=f, stderr=subprocess.DEVNULL, timeout=15)
        with open(tmp, "rb") as f:
            return "tcp:2222" in f.read().decode("utf-8", "replace")
    except Exception:
        return False


def ensure_fport():
    with _fport_lock:
        if fport_ok():
            return True
        try:
            subprocess.run([HDC, "-t", DEVICE, "fport", "rm", "tcp:2222", "tcp:2222"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            subprocess.run([HDC, "-t", DEVICE, "fport", "tcp:2222", "tcp:2222"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        except Exception as e:
            log(f"[tun] fport 异常: {e}")
        return fport_ok()


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


def handle(cli, port):
    try:
        if not ensure_fport():
            log(f"[tun] {port} fport 不可用，丢弃本次连接")
            cli.close()
            return
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect("127.0.0.1", port=RELAY_PORT, username=K1_USER, password=K1_PWD,
                  timeout=15, banner_timeout=20, auth_timeout=20,
                  look_for_keys=False, allow_agent=False)
        ch = c.get_transport().open_channel(
            "direct-tcpip", ("127.0.0.1", port), ("127.0.0.1", 0), timeout=15)
        splice(cli, ch)
        c.close()
    except Exception as e:
        log(f"[tun] {port} 失败: {e}")
        try:
            cli.close()
        except Exception:
            pass


def serve(port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(32)
    log(f"[tun] 本地 {port} → K1:{port} 就绪")
    while True:
        cli, _ = srv.accept()
        threading.Thread(target=handle, args=(cli, port), daemon=True).start()


def main():
    ports = [int(x) for x in sys.argv[1:]] or [11436, 11437, 11438, 11439]
    for p in ports:
        threading.Thread(target=serve, args=(p,), daemon=True).start()
    threading.Event().wait()


if __name__ == "__main__":
    main()
