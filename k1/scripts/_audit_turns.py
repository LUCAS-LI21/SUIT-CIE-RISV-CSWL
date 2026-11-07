#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打印某会话里：用户的每条消息 + 助手的每条最终答复，用于还原「结论是怎么来的」。

只读。用于核查「芯片配置被写坏」这句话到底出自谁、在什么上下文。
"""
import json
import os
import sys


def text_of(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for it in c:
            if isinstance(it, dict):
                if it.get("type") == "text" and it.get("text"):
                    parts.append(it["text"])
                elif it.get("type") == "output_text" and it.get("text"):
                    parts.append(it["text"])
        return "\n".join(parts)
    return ""


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        "~/.workbuddy/projects/e-CIE-RISC-V/"
        "0ffb217a-dc2d-42f2-b00b-887eb0725f62.jsonl")
    only = sys.argv[2] if len(sys.argv) > 2 else "user"
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            msg = rec.get("message") or {}
            role = msg.get("role")
            if role != only:
                continue
            t = text_of(msg).strip()
            if not t:
                continue
            if len(t) > 2600:
                t = t[:2600] + "\n…(截断)"
            print("=" * 70)
            print(f"[{rec.get('timestamp','')}] {role}")
            print(t)


if __name__ == "__main__":
    main()
