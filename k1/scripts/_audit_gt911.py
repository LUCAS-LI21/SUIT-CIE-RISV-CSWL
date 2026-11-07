#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计历史会话里是否真的执行过对 GT911 的 I2C 写操作。

只读脚本：解析 .jsonl 会话记录，抽出 tool_use 的入参里含 i2cset / 0x8047 的命令，
按时间顺序打印。目的是把「是否写过芯片」从推测变成可复核的事实。
"""
import json
import os
import sys

TARGET = os.path.expanduser(
    "~/.workbuddy/projects/e-CIE-RISC-V/0ffb217a-dc2d-42f2-b00b-887eb0725f62.jsonl"
)
KEYS = ("i2cset", "0x8047", "0x8040", "gt9xx")


def walk(obj, out):
    """递归收集所有字符串，找含关键词的。"""
    if isinstance(obj, str):
        low = obj.lower()
        if any(k in low for k in KEYS):
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, out)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else TARGET
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = rec.get("timestamp") or rec.get("ts") or ""
            msg = rec.get("message") or {}
            role = msg.get("role") or rec.get("type") or ""
            hits = []
            walk(rec, hits)
            if not hits:
                continue
            for h in hits:
                rows.append((lineno, ts, role, h))

    print(f"# 文件 {os.path.basename(path)}  命中 {len(rows)} 条\n")
    for lineno, ts, role, text in rows:
        t = text.replace("\n", " ").strip()
        if len(t) > 400:
            t = t[:400] + " …"
        print(f"[L{lineno}] {ts} {role}\n    {t}\n")


if __name__ == "__main__":
    main()
