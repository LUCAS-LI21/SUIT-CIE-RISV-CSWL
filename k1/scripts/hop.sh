#!/bin/sh
# 3568 上的单连接转发脚本，由 `busybox nc -lk -p 2222 -e /data/local/tmp/hop.sh` 调起。
#
# 为什么要有这个文件：busybox nc 的 -e 只接受**一个可执行路径**，
# 写成 -e "busybox nc 192.168.88.10 22" 不会报错但也不转发
# （实测：3568 本地 netstat 有 LISTEN，连上去却一个字节都不回来）。
# 所以把「带参数的命令」塞进脚本，-e 只指向脚本本身。
exec busybox nc 192.168.88.10 22
