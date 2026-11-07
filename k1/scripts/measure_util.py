#!/usr/bin/env python3
"""K1 算力占用采样器 —— 用于评分表「硬件利用率」项的实测取证。

背景：K1（MUSE Pi Pro）8 核全是 spacemit,x60，但**只有 cpu0-3 带 ime 扩展**，
      也就是评分表说的「AI 核」。K1 没有独立 NPU，所以用 cpu0-3 的 busy 比例
      作为 AI 核占用的代理指标，「CPU 核」用整机 8 核平均占用。

用法（板端 root 或普通用户均可）：
    python3 measure_util.py                  # 默认采 120 秒，500 ms 一次
    python3 measure_util.py -d 60 -i 0.5     # 采 60 秒
    python3 measure_util.py --pid 1234       # 额外跟踪某个进程（如 vision_service）
    python3 measure_util.py --json           # 输出 JSON 便于写进报告

输出：AI 核均值/95 分位、整机 CPU 均值、有 swap 时的 si/so（用于稳定性佐证）。

口径说明（写报告时必须带上，否则评委会按 x86 NPU 口径误读）：
    AI核占用  = sum(cpu0..3 busy) / sum(cpu0..3 total) × 100%
    整机占用  = sum(cpu0..7 busy) / sum(cpu0..7 total) × 100%
"""
import argparse
import json
import statistics
import time


def read_stat():
    """读 /proc/stat，返回 {核心名: (busy, total)}。"""
    out = {}
    with open("/proc/stat", "r") as f:
        for line in f:
            if not line.startswith("cpu"):
                continue
            parts = line.split()
            name = parts[0]
            if len(name) > 3 and not name[3:].isdigit():
                continue          # 跳过 cpu0 以外的怪行
            vals = [int(x) for x in parts[1:]]
            # user nice system idle iowait irq softirq steal
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            total = sum(vals[:8])
            out[name] = (total - idle, total)
    return out


def read_swaps():
    si = so = 0
    try:
        with open("/proc/vmstat", "r") as f:
            for line in f:
                if line.startswith("pswpin "):
                    si = int(line.split()[1])
                elif line.startswith("pswpout "):
                    so = int(line.split()[1])
    except OSError:
        pass
    return si, so


def proc_cpu(pid):
    """进程累计 CPU 时间（jiffies）。"""
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            fields = f.read().split()
        return int(fields[13]) + int(fields[14])       # utime + stime
    except (OSError, IndexError):
        return None


def pct(prev, cur):
    if prev is None or cur is None:
        return 0.0
    db = cur[0] - prev[0]
    dt = cur[1] - prev[1]
    return 100.0 * db / dt if dt > 0 else 0.0


def p95(values):
    if len(values) < 2:
        return values[0] if values else 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round(0.95 * (len(s) - 1))))
    return s[idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-d", "--duration", type=float, default=120.0, help="采样时长（秒）")
    ap.add_argument("-i", "--interval", type=float, default=0.5, help="采样间隔（秒）")
    ap.add_argument("--pid", type=int, default=0, help="额外跟踪的进程 pid")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    ai_series, all_series, proc_series = [], [], []
    prev = read_stat()
    prev_proc = proc_cpu(args.pid) if args.pid else None
    si0, so0 = read_swaps()
    t0 = time.time()
    hz = 100.0                                  # 内核 USER_HZ，K1 上实测为 100

    while time.time() - t0 < args.duration:
        time.sleep(args.interval)
        cur = read_stat()
        ai = [pct(prev.get(f"cpu{i}"), cur.get(f"cpu{i}")) for i in range(4)]
        allc = [pct(prev.get(f"cpu{i}"), cur.get(f"cpu{i}")) for i in range(8)]
        ai_series.append(sum(ai) / 4.0)
        all_series.append(sum(allc) / 8.0)

        if args.pid:
            pc = proc_cpu(args.pid)
            if pc is not None and prev_proc is not None:
                dt = args.interval
                proc_series.append(100.0 * (pc - prev_proc) / (hz * dt))
            prev_proc = pc
        prev = cur
        if not args.json:
            print(f"\rAI核 {ai_series[-1]:5.1f}%  整机 {all_series[-1]:5.1f}%  "
                  f"已采 {time.time() - t0:5.1f}s", end="", flush=True)

    si1, so1 = read_swaps()
    result = {
        "samples": len(ai_series),
        "ai_core_mean": round(statistics.mean(ai_series), 1) if ai_series else 0.0,
        "ai_core_p95": round(p95(ai_series), 1),
        "cpu_total_mean": round(statistics.mean(all_series), 1) if all_series else 0.0,
        "cpu_total_p95": round(p95(all_series), 1),
        "swap_in_pages": si1 - si0,
        "swap_out_pages": so1 - so0,
    }
    if proc_series:
        result["proc_cpu_mean"] = round(statistics.mean(proc_series), 1)
        result["proc_pid"] = args.pid

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\n" + "-" * 52)
        print(f"样本数        : {result['samples']}（{args.interval}s × {args.duration}s）")
        print(f"AI核 cpu0-3   : 均值 {result['ai_core_mean']}%  p95 {result['ai_core_p95']}%")
        print(f"整机 8 核     : 均值 {result['cpu_total_mean']}%  p95 {result['cpu_total_p95']}%")
        if proc_series:
            print(f"进程 {args.pid}    : 均值 {result['proc_cpu_mean']}%（占单核百分比）")
        print(f"swap 换入/出  : {result['swap_in_pages']} / {result['swap_out_pages']} 页")
        print("-" * 52)
        print("对照评分表：AI核≥90% 且 整机CPU≤60% → 优秀档（5~6 分）")


if __name__ == "__main__":
    main()
