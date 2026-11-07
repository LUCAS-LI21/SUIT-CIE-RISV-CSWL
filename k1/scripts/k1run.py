#!/usr/bin/env python
"""经 USB 隧道在 K1 上执行命令 / 传输文件。

链路：PC → hdc fport 2222 → 3568 的 busybox nc 中继 → K1:22 (sshd)

为什么不用 ssh/scp 命令：Windows 上没有 sshpass，OpenSSH 又不接受从 stdin
读密码，非交互场景只能靠 paramiko。隧道本身很脆弱（hdc 服务端会在没有客户端
时退出），所以本脚本每次运行前都会自己补一次 fport。
"""
import argparse
import os
import subprocess
import sys

import paramiko

HDC = r"E:\HarmonyOS\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe"
HOST, PORT = "127.0.0.1", 2222
USER = os.environ.get("K1_USER", "root")
PASS = os.environ.get("K1_PASS", "bianbu")


def ensure_tunnel():
    """确保 hdc 端口转发存在。重复添加同名任务 hdc 会直接报 OK，不用去重。"""
    try:
        subprocess.run([HDC, "fport", "tcp:2222", "tcp:2222"],
                       capture_output=True, timeout=15)
    except Exception as e:
        print(f"[警告] 建立转发失败：{e}", file=sys.stderr)


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=PASS,
              timeout=10, allow_agent=False, look_for_keys=False)
    return c


def run(c, cmd, timeout=120):
    _, out, err = c.exec_command(cmd, timeout=timeout, get_pty=False)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    rc = out.channel.recv_exit_status()
    return rc, o, e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", help="要执行的远程命令")
    ap.add_argument("--put", action="append", default=[],
                    metavar="LOCAL:REMOTE", help="上传文件，可重复")
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args()

    ensure_tunnel()
    c = connect()
    try:
        if a.put:
            sftp = c.open_sftp()
            for spec in a.put:
                # 必须从右侧切：Windows 盘符本身带冒号（E:/...），
                # 从左切会把 "E" 当成路径。
                local, remote = spec.rsplit(":", 1)
                sftp.put(local, remote)
                print(f"[put] {local} -> {remote} "
                      f"({os.path.getsize(local)} B)")
            sftp.close()
        if a.command:
            rc, o, e = run(c, a.command, a.timeout)
            sys.stdout.write(o)
            if e.strip():
                sys.stderr.write(e)
            return rc
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
