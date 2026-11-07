#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
纯 ctypes 的 i2c 地址扫描 —— 不依赖 i2c-tools / smbus。
用途：判断 MIPI 摄像头 sensor 是否真的挂在 i2c 总线上（接线/供电是否到位）。

原理与 i2cdetect 一致：open("/dev/i2c-N") 后 ioctl(I2C_SLAVE, addr)，
再 read(fd,1)。若返回 -1 且 errno 为 ENXIO/EREMOTEIO 则无器件；
能读到或报 EIO/EBUSY 都说明总线上有东西应答。
"""
import ctypes
import errno
import os
import sys

I2C_SLAVE = 0x0703

# 常见 sensor 地址（7 位）
KNOWN = {
    0x10: "IMX219",
    0x1A: "IMX135 / OV8865(常见)",
    0x36: "OV5647",
    0x37: "GC2093 / OV13850",
    0x20: "GC2375H(常见)",
    0x3C: "OV9734",
    0x6C: "IMX135(8bit 0x6C)",
}


def scan(bus):
    path = "/dev/i2c-%d" % bus
    if not os.path.exists(path):
        return None
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError as e:
        return "open失败:%s" % e

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    found = []
    for addr in range(0x03, 0x78):
        ctypes.set_errno(0)
        r = libc.ioctl(fd, I2C_SLAVE, addr)
        if r < 0:
            e = ctypes.get_errno()
            # EBUSY 说明该地址已被某个驱动占用 —— 这恰恰说明总线上有器件
            if e == errno.EBUSY:
                found.append((addr, "EBUSY(被驱动占用)"))
            continue
        # 地址被接受，尝试读一个字节
        try:
            data = os.read(fd, 1)
            if data:
                found.append((addr, "读出数据"))
            else:
                found.append((addr, "可寻址"))
        except OSError as e:
            # ENXIO(6) / EREMOTEIO(121) 都是从机 NAK —— 该地址上没有器件
            if e.errno in (errno.ENXIO, getattr(errno, "EREMOTEIO", 121)):
                continue
            found.append((addr, "errno=%d" % e.errno))
    os.close(fd)
    return found


def main():
    buses = [int(a) for a in sys.argv[1:]] or list(range(0, 12))
    print("i2c 扫描开始，共 %d 条总线\n" % len(buses))
    for b in buses:
        res = scan(b)
        if res is None:
            continue
        if isinstance(res, str):
            print("i2c-%-2d  %s" % (b, res))
            continue
        if not res:
            print("i2c-%-2d  无器件" % b)
            continue
        print("i2c-%-2d  发现 %d 个:" % (b, len(res)))
        for addr, why in res:
            name = KNOWN.get(addr, "")
            print("    0x%02X  %-18s %s" % (addr, why, name))
    print("\n判读：若 0x36 出现 → OV5647 在总线上；0x10 → IMX219；0x20 → GC2375H")


if __name__ == "__main__":
    main()
