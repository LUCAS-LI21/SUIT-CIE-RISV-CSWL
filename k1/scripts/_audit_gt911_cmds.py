#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只抽「真正发出去的命令」：从会话 jsonl 里取出所有 tool 调用的 command 字段，
筛出碰 i2c / GT911 / 设备树的那些，按时间顺序打印。

审计目标：那条新对话到底有没有对 GT911 配置区（0x8047）执行写操作。
只读脚本，不连接任何设备。
"""
import json
import os
import sys

KEYS = ("i2cset", "i2cget", "0x8047", "0x8040", "gt9xx", "i2cdump")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        "~/.workbuddy/projects/e-CIE-RISC-V/"
        "0ffb217a-dc2d-42f2-b00b-887eb0725f62.jsonl")
    n_print = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = rec.get("timestamp") or ""
            cand = []

            def dig(o, depth=0):
                # 工具入参有时以「JSON 字符串」形式存，需要再解一层
                if isinstance(o, str):
                    s = o.strip()
                    if depth < 4 and s[:1] == "{" and s[-1:] == "}":
                        try:
                            dig(json.loads(s), depth + 1)
                        except Exception:
                            pass
                elif isinstance(o, dict):
                    if isinstance(o.get("command"), str):
                        cand.append(o["command"])
                    for v in o.values():
                        dig(v, depth)
                elif isinstance(o, list):
                    for v in o:
                        dig(v, depth)

            dig(rec)
            seen = set()
            for cmd in cand:
                if cmd in seen:
                    continue
                seen.add(cmd)
                if not any(k in cmd.lower() for k in KEYS):
                    continue
                n_print += 1
                body = " ".join(cmd.split())
                if len(body) > 700:
                    body = body[:700] + " …"
                print(f"--- #{n_print}  ts={ts}")
                print(body)
                print()
    print(f"# 命中命令数 {n_print}")


if __name__ == "__main__":
    main()
