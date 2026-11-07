#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过 3568 的 nc 中继访问 K1（PC 不在 192.168.88.0/24，只能这样绕）。

链路：本机 127.0.0.1:2222 --hdc fport--> 3568:2222 --nc -e hop.sh--> K1:22

用法：
    python scripts/k1_ssh.py run "systemctl status k1-vision"
    python scripts/k1_ssh.py put 本地文件 远端路径
    python scripts/k1_ssh.py get 远端路径 本地文件

只依赖 paramiko。连接不上时先检查两件事：
  1) 3568 上有没有 `busybox nc -lk -p 2222 -e /data/local/tmp/hop.sh` 在跑
  2) `hdc fport tcp:2222 tcp:2222` 是否还在

关于第 2 点，实测比「空闲后自动撤掉」更严格：
  hdc 的 fport 在**一次** TCP 连接关闭之后就会从 `fport ls` 里消失（变成 [Empty]），
  所以「先手动加 fport，再另起一条命令连接」这种两步操作必然失败——
  中间只要有任何东西碰过 2222 端口（哪怕是一次探测），转发就没了。
  因此这里把「补建 fport」和「建立 SSH」放进同一个进程，
  并且每次重试都重新补建一次。
"""
import os
import subprocess
import sys
import time

import paramiko

HOST = "127.0.0.1"
PORT = 2222
USER = "root"
PWD = "bianbu"

# hdc 不在 PATH 里，固定在套件目录。找不到就跳过自动补建（外部已加好时仍可用）。
_HDC_CANDIDATES = [
    r"E:\CIE-RISC-V\05-SC-3568HA开发套件（ISOH-SC-3568HA）-基础版\05 工具\OpenHarmony\HDC 工具\hdc.exe",
]

# 必须显式指定目标：本机同时连着模拟器(127.0.0.1:5555)与 3568，
# 不带 -t 的 `hdc fport` 会直接报 `need connect-key? please confirm a device`，
# 而且这个失败**不抛异常**（退出码非 0 但脚本原先用 DEVNULL 跑了），
# 表现为「fport 已补建」日志照打、本地 2222 却没人监听 → SSH 必然 ECONNREFUSED。
DEVICE = "df011853434133324710076f03096b00"


def _hdc():
    for p in _HDC_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def ensure_fport(tag=""):
    """把 PC:2222 -> 3568:2222 的转发补上。失败不抛异常，交给后面的连接去判断。"""
    hdc = _hdc()
    if not hdc:
        return
    try:
        # 必须用 DEVNULL，不能 capture_output：
        # hdc.exe 首次运行会 fork 出一个常驻 hdc server 并继承 stdout，
        # 管道永远等不到 EOF，capture_output 会一直挂住（实测卡死 >90s）。
        subprocess.run([hdc, "-t", DEVICE, "fport", f"tcp:{PORT}", f"tcp:{PORT}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=20)
        print(f"[tunnel{tag}] fport 已补建", flush=True)
    except Exception as e:
        print(f"[tunnel{tag}] fport 补建失败: {e}", flush=True)


def connect(retries=3):
    last = None
    for i in range(retries):
        ensure_fport(f" {i + 1}/{retries}")
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            c.connect(HOST, port=PORT, username=USER, password=PWD,
                      timeout=15, banner_timeout=20, auth_timeout=20,
                      look_for_keys=False, allow_agent=False)
            return c
        except Exception as e:
            last = e
            c.close()
            time.sleep(2)
    raise last


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    c = connect()
    try:
        if cmd == "run":
            script = sys.argv[2]
            _in, out, err = c.exec_command(script, timeout=120)
            o = out.read().decode("utf-8", "replace")
            e = err.read().decode("utf-8", "replace")
            print(o.rstrip())
            if e.strip():
                print("--- stderr ---")
                print(e.rstrip())
            return out.channel.recv_exit_status()
        if cmd == "put":
            local, remote = sys.argv[2], sys.argv[3]
            sftp = c.open_sftp()
            sftp.put(local, remote)
            st = sftp.stat(remote)
            sftp.close()
            print(f"uploaded {local} -> {remote} ({st.st_size} bytes)")
            return 0
        if cmd == "putcat":
            # 备用通道：SFTP 子系统在中继链路上可能不可用（实测 FileNotFoundError），
            # 但普通的 exec 通道 + stdin 是可靠的。用 cat 直接落盘。
            local, remote = sys.argv[2], sys.argv[3]
            data = open(local, "rb").read()
            _in, out, err = c.exec_command(f"cat > {remote}", timeout=120)
            _in.write(data)
            _in.channel.shutdown_write()
            out.channel.recv_exit_status()
            e = err.read().decode("utf-8", "replace")
            if e.strip():
                print("--- stderr ---")
                print(e.strip())
            print(f"putcat {local} -> {remote} ({len(data)} bytes)")
            return 0
        if cmd == "get":
            remote, local = sys.argv[2], sys.argv[3]
            sftp = c.open_sftp()
            sftp.get(remote, local)
            sftp.close()
            print(f"downloaded {remote} -> {local} ({os.path.getsize(local)} bytes)")
            return 0
        if cmd == "getcat":
            # 与 putcat 对称：SFTP 子系统实测在中继链路上 FileNotFoundError，
            # 这里走 exec 通道 + stdout 二进制直传（不经 utf-8 解码，避免图片被破坏）。
            remote, local = sys.argv[2], sys.argv[3]
            _in, out, err = c.exec_command(f"cat {remote}", timeout=180)
            data = out.read()          # 二进制安全的 bytes
            out.channel.recv_exit_status()
            e = err.read().decode("utf-8", "replace")
            if not data:
                print("--- stderr ---")
                print(e.strip() or "(empty stdout)")
                return 1
            with open(local, "wb") as f:
                f.write(data)
            print(f"getcat {remote} -> {local} ({len(data)} bytes)")
            return 0
    finally:
        c.close()
    print("unknown command:", cmd)
    return 1


if __name__ == "__main__":
    sys.exit(main())
